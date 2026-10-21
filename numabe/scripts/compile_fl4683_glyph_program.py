#!/usr/bin/env python3
# =============================================================================
# scripts/compile_fl4683_glyph_program.py
# =============================================================================
# WHAT THIS SCRIPT IS FOR
# -----------------------
# The project keeps a very large "glyph fact" file on disk:
#
#     assets/glyphs/generated/fl4676.glyph_fact.v1.json     (about 93 MiB)
#
# That file is written by an offline compiler. It lists 111,040 "glyph records"
# (one per drawable character shape that the engine is allowed to use) and 99
# "facts" (one per compiled decision address: "when the renderer reports THIS
# kind of cell, these are the legal glyphs and their weights").
#
# The engine is written in C++. Reading 93 MiB of JSON inside a C++ program at
# start-up would need a JSON parser, a large temporary object graph, and a lot
# of time. So this script performs the parse ONCE, offline, and writes two
# small companion files:
#
#   1. assets/glyphs/generated/fl4683.glyph_program.v1.bin
#      A compact, fixed-layout binary "sidecar". Every number sits at a known
#      byte offset, so the C++ side can read it with plain pointer arithmetic
#      and zero parsing. Roughly 2.45 MiB after the exhaustive FL-4694
#      aspect-conditioned relation section was added.
#
#   2. assets/glyphs/generated/fl4683.glyph_program_pin.v1.json
#      A human-readable "receipt". It records the SHA-256 fingerprint and the
#      exact byte size of the sidecar and of every input the sidecar was built
#      from. A fingerprint (also called a hash or digest) is a short string
#      that changes completely if even one byte of the file changes, so it lets
#      any later reader detect tampering or drift.
#
# WHY A BINARY SIDECAR INSTEAD OF STREAMING THE JSON IN C++
# ---------------------------------------------------------
# Three alternatives were considered:
#   (a) parse the 93 MiB JSON in C++ with the bundled cJSON library,
#   (b) hand-write a streaming (character-at-a-time) JSON reader in C++,
#   (c) compile a binary sidecar offline in Python (this script).
# (a) builds a full in-memory object tree for 111,040 records, which is slow
# and memory-hungry. (b) means a new hand-written parser that would itself need
# tests and could disagree with Python's parser. (c) reuses the parser that
# already produced the artifact, keeps the C++ side to ~0 parsing risk, and is
# deterministic: running this script twice on the same inputs produces
# byte-identical output, which the tests check.
#
# FAIL-CLOSED MEANING
# -------------------
# "Fail closed" means: when anything does not match its recorded fingerprint,
# the reader must stop and report a NAMED reason. It must never guess, never
# substitute a default, and never carry on with partly-trusted data. Both this
# script and the C++ loader follow that rule.
#
# SERIALIZED RUNTIME RIGHTS (IMPORTANT)
# --------------------------
# The sidecar is blocked at rest. This script stamps
# `runtime_consumption_allowed = 0` into header byte 160. The product loader
# validates that immutable byte stream, and the product startup owner promotes
# the validated in-memory view only after the loader accepts it. The receipt
# records those two states separately so the serialized byte is never mistaken
# for the live product state.
#
# USAGE
# -----
#     python3 scripts/compile_fl4683_glyph_program.py --write
#     python3 scripts/compile_fl4683_glyph_program.py --check
#
# `--write` regenerates the sidecar and the receipt.
# `--check` recomputes both in memory and exits non-zero if what is on disk
# differs, which is how continuous checks detect a stale sidecar.
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
# `ROOT` is the repository root. `__file__` is this script, `.parents[1]` walks
# up from `scripts/` to the repository directory that contains `assets/`.
ROOT = Path(__file__).resolve().parents[1]

# The authoritative 93 MiB compiled artifact this sidecar is derived from.
GLYPH_FACT_ARTIFACT = Path("assets/glyphs/generated/fl4676.glyph_fact.v1.json")
# The JSON Schema that describes the shape of the artifact above.
GLYPH_FACT_SCHEMA = Path("assets/glyphs/schema/fl4676.glyph_fact.v1.schema.json")
# The typed handshake contract that names what a renderer-owned scene fact is.
# Its fingerprint becomes GlyphProgramV1.scene_fact_schema_sha256, which lets the
# runtime detect a renderer/compiler contract mismatch.
SCENE_FACT_HANDSHAKE_SCHEMA = Path(
    "assets/glyphs/schema/fl4683.scene_fact_handshake.v1.schema.json"
)
# The FL-4694 "pointer manifest": a small committed file that records the
# fingerprints of three LARGE caches which are deliberately NOT committed
# (they live under assets/glyphs/generated/local/ and are regenerable).
RELATION_POINTER_MANIFEST = Path("assets/glyphs/generated/fl4694.relation_pointer.v1.json")
RELATION_POINTER_SCHEMA = Path("assets/glyphs/schema/fl4694.relation_pointer.v1.schema.json")
# FL-4694 E8 resolution. The bounded nearest-neighbour cache above is useful
# for local similarity, but the measured GTR-v2 audit proved that its
# aspect-free two-scalar rows cannot represent the complete 32 x 64 x 32
# directed transform relation. The exact source table and its schema are
# therefore committed inputs to a new aspect-conditioned GlyphProgram section.
# They remain data inputs only: runtime rights stay pinned off below.
GTR_V2_ARTIFACT = Path(
    "assets/glyphs/generated/fl4512.glyph_transform_relation.v2.json"
)
GTR_V2_SCHEMA = Path(
    "assets/glyphs/schema/fl4512.glyph_transform_relation.v2.schema.json"
)
# FL-4686 precondition PA. The compiled pool is the artifact that enumerates the
# COMPLETE profile-scoped address space: every (namespace, material, route
# family, lane) slot for every loaded material profile, whether or not any glyph
# was admitted into it. The glyph-fact artifact above carries only the slots that
# DID admit glyphs, so on its own it cannot say "this address exists and is
# deliberately empty" -- it can only fail to mention the address at all.
#
# Why that distinction is the whole point: a renderer cell whose address is
# missing from the sidecar produces a lookup MISS, which is a failure. A renderer
# cell whose address is present and marked DECLARED ABSENT is owned by the
# program, which then states that nothing should be drawn. Those are different
# facts about the world and the runtime must not have to guess between them by
# noticing that a lookup failed.
COMPILED_POOL_ADDRESS_SPACE = Path("assets/glyphs/generated/fl4512.compiled_pool.v1.json")

# FL-4768: the AUTHORED source the compiled pool above is derived from.
#
# This compiler joins every material's `no_match_policy` out of the pool and
# encodes the per-role bound into each shape6 fact record, but the bound is
# AUTHORED here, not in the pool. So the pool is only a faithful carrier of
# this file while the two are the same compilation. `compile_fl4512_pools.py`
# stamps `header.profile_hash = sha256(raw bytes of this file)` precisely so
# that a later reader can tell. Nothing in this compiler used to read it, which
# is how commit 61a11750f corrected the authored policy and republished the
# program from an un-regenerated pool in the same commit: `--check` stayed
# green while 83 of 97 shape6 rows shipped a bound this file does not
# authorize. `validate_artifact_source_identity` now closes that gap.
MATERIAL_RENDERING_PROFILES = Path(
    "assets/glyphs/profiles/material_rendering_profiles.v1.json"
)

# Outputs.
SIDECAR_OUTPUT = Path("assets/glyphs/generated/fl4683.glyph_program.v1.bin")
RECEIPT_OUTPUT = Path("assets/glyphs/generated/fl4683.glyph_program_pin.v1.json")

# -----------------------------------------------------------------------------
# Binary layout constants
# -----------------------------------------------------------------------------
# These numbers are duplicated, deliberately and with the same names, inside
# platform/fl4683_glyph_program_loader.h. If either side changes, the magic
# string or the format version below must change too so that an old reader
# refuses a new file instead of misreading it.

# An eight-byte "magic" marker at the very start of the file. A reader that
# does not see exactly these bytes knows immediately it was handed the wrong
# kind of file (a text file, a truncated download, a different artifact).
SIDECAR_MAGIC = b"FL4683GP"

# Bumping this integer tells old readers "you cannot understand this file".
# Version 2 added the scene-address index section described below, and changed
# the pinned handshake-schema fingerprint that goes with the FL-4683
# route-family discriminator. A version-1 reader must refuse a version-2 file.
#
# Version 3 (FL-4686 precondition PA) makes DECLARED ABSENCE a first-class
# encoded disposition. The scene-address index now covers the complete
# profile-scoped address space rather than only the addresses that admitted
# glyphs, and every entry carries a `disposition` byte saying which kind of
# address it is. A version-2 reader must refuse a version-3 file, because a
# version-2 reader would read the disposition byte as padding and would then
# treat a declared-absent entry's sentinel fact index as a real fact index.
#
# Version 4 (FL-4694 E8 resolution) spends the header's final twelve reserved
# bytes on the offset, row count, and stride of an exhaustive
# aspect-conditioned GTR-v2 relation section. Every one of the 65,536 directed
# rows is carried exactly. A version-3 reader must refuse this file because it
# cannot validate or expose that section.
#
# Version 5 (FL-4512 D2/D3 Harri selector cutover) makes the inputs to the
# compiled scorer self-contained. Each admitted member now carries one 12-byte
# compiler-owned operand union: six-sample normalised morphology, u7 mask plus
# topology counts, or primitive-field mask plus region/detail. Each fact names
# the union's scorer kind and the six profile role weights after the frozen q4
# transform. A version-4 reader must refuse the wider records instead of
# indexing them with the retired strides.
#
# Version 6 (FL-4512 Phase 2, role-correct abstention) spends the fact record's
# single former padding byte on `policy_flags` -- the compiled no-match policy
# carrier -- and re-scopes the record's trailing u32 threshold field by scorer
# kind (primitive keeps the extent-enter gate; the two distance scorers carry a
# compiled no-match bound, 0 = unbounded). The record stays 128 bytes and every
# section offset is unchanged, but a version-5 reader would read authored
# abstention policy as garbage padding, so the version number must move.
#
# Version 7 (FL-4783) adds an executable, compiler-owned glyph stroke-port
# graph. The header grows to carry explicit offsets, counts, and strides for
# the graph, boundary-port, and connectivity sections. A version-6 reader must
# refuse this file rather than silently
# dropping the structural graph.
SIDECAR_FORMAT_VERSION = 7

# The header is a fixed-size block at the front of the file. The first 256
# bytes retain the v6 fields. The final 64 bytes are the FL-4783 section
# directory, padded to a 320-byte boundary for naturally aligned records.
SIDECAR_HEADER_SIZE = 320

# Size in bytes of each entry in each table. Fixed sizes let the C++ reader
# jump straight to entry N with `base + N * stride` and no scanning.
FACT_RECORD_SIZE = 128
FACT_MEMBER_SIZE = 20
RELATION_EDGE_SIZE = 24
GLYPH_RECORD_ID_SIZE = 4
PIN_RECORD_SIZE = 224
# One entry in the scene-address index (added in format version 2). See
# `encode_scene_address_index` for what each field means and why the index has
# to exist at all. Format version 3 spends one byte of the record's existing
# padding on the disposition, so the record size is unchanged and every offset
# in the file stays where it was.
SCENE_ADDRESS_RECORD_SIZE = 12
# FL-4694 exhaustive aspect-conditioned relation row. See
# `encode_gtr_v2_relations` for the byte-level contract.
GTR_V2_RELATION_RECORD_SIZE = 32
GTR_V2_GLYPH_COUNT = 32
GTR_V2_ASPECT_COUNT = 64
GTR_V2_RELATIONS_PER_ASPECT = GTR_V2_GLYPH_COUNT * GTR_V2_GLYPH_COUNT
GTR_V2_RELATION_COUNT = GTR_V2_ASPECT_COUNT * GTR_V2_RELATIONS_PER_ASPECT
GTR_V2_STATIC_COMPONENT_COUNT = 11
GTR_V2_AFFINE_COMPONENT_COUNT = 7

# FL-4783 compiler-owned glyph graph records. All offsets are section-relative
# only through the graph records; child offsets are absolute sidecar offsets.
# Fixed widths keep native and WebAssembly consumers on one ABI. The serialized
# side/corner vocabulary is N..interior = 0..8; sequence adapters add one when
# converting to their 1..9 vocabulary, whose zero value is invalid. Tangent Y
# uses the compiler's atlas convention: row zero is visual top and positive Y
# points down. The sidecar preserves these raw row-down tangent bytes.
PORT_GRAPH_RECORD_SIZE = 32
BOUNDARY_PORT_RECORD_SIZE = 20
PORT_CONNECTIVITY_RECORD_SIZE = 16
PORT_SIDE_OR_CORNER_MAX = 8
# The v1 structural sequence ABI carries at most eight boundary ports for one
# glyph candidate. Reject larger compiler records rather than truncating a
# component graph or making the runtime infer a partial topology.
RUNTIME_PORT_GRAPH_MAX_PORTS = 8
# Eight ports have 8*7/2 = 28 unique unordered pairs. Pair rows are bounded
# explicitly so malformed or future source data cannot create an unbounded
# child slice. The compiler rejects overflow rather than truncating it.
RUNTIME_PORT_GRAPH_MAX_CONNECTIVITY = 28
RUNTIME_PORT_GRAPH_MAX_COMPONENTS = 64
RUNTIME_PORT_GRAPH_MAX_SKELETON_EDGES = 256

# -----------------------------------------------------------------------------
# Address disposition vocabulary (format version 3, FL-4686 precondition PA)
# -----------------------------------------------------------------------------
# What the program says about one address in the complete profile-scoped space.
# These numbers are duplicated with the same names in
# platform/fl4683_glyph_program_loader.h.
#
# Zero is deliberately NOT a valid disposition. A record that was zeroed by
# accident, or written by an older compiler that did not know about this field,
# therefore reads as UNSET and is refused, rather than silently meaning
# "populated" (which would resurrect the exact bug PA exists to remove).
ADDRESS_DISPOSITION_UNSET = 0
# The program holds admitted glyphs for this address. `fact_index` names them.
ADDRESS_DISPOSITION_POPULATED = 1
# The program OWNS this address and states that it holds no glyph. This is a
# positive declaration made offline, not the absence of a record, and it is not
# a failure. `fact_index` is the no-fact sentinel below.
ADDRESS_DISPOSITION_DECLARED_ABSENT = 2

ADDRESS_DISPOSITION_NAMES = {
    ADDRESS_DISPOSITION_POPULATED: "populated",
    ADDRESS_DISPOSITION_DECLARED_ABSENT: "declared_absent",
}

# Stored in `fact_index` for a declared-absent entry. All-ones is chosen because
# it can never be a legal index into a fact table that must fit in memory, so a
# reader that forgets to check the disposition gets an out-of-range index it
# will reject, rather than a plausible-looking index into the wrong fact.
NO_FACT_INDEX = 0xFFFFFFFF

# The bucket_state values the compiled pool uses for the two dispositions. Read
# from the pool rather than assumed, so a pool that grows a third state fails
# loudly here instead of being silently bucketed into one of these two.
POOL_STATE_POPULATED = "live"
POOL_STATE_DECLARED_ABSENT = "empty_absence"
# Longest repo-relative path we can store inside a pin record, including the
# terminating zero byte. 176 bytes covers every path in this repository with
# generous headroom.
PIN_PATH_CAPACITY = 176

# -----------------------------------------------------------------------------
# Scene-address index vocabulary (format version 2)
# -----------------------------------------------------------------------------
# WHY THIS INDEX EXISTS
#
# The compiled artifact identifies each address by `row_sha256`, a fingerprint
# of the WHOLE compiled row -- its candidate list, its descriptors, its
# admission provenance. That is excellent as an identity and useless as a
# lookup key, because a running renderer cannot compute it: the renderer does
# not have the row contents, only a description of the cell it just drew.
#
# So the sidecar carries a small sorted table that turns the things a renderer
# DOES know -- namespace, material key, route family, lane number -- into the
# index of the matching fact. Without it, nothing could ever go from "this cell
# looks like such and such" to "here is its compiled address", and the
# exact-cell prediction FL-4678 asks for would be impossible to state.
#
# The route-family numbers below are the same bit values as
# Fl4683RouteFamilyV1 in platform/fl4683_scene_fact_record.h. They are repeated
# here, deliberately, because this compiler and that header are the two ends of
# one contract; a test cross-checks them so the duplication cannot drift.
ROUTE_FAMILY_BIT = {
    "ramp.v1": 1 << 0,
    "density.v1": 1 << 1,
    "edge.v1": 1 << 2,
    "direction.v1": 1 << 3,
    "flow.v1": 1 << 4,
    "filled.field.v1": 1 << 5,
    "mesh_feature.bundle.v1": 1 << 6,
}

# The renderer reports a namespace as a small number, not a word. This mapping
# matches FL4512_PROFILE_NAMESPACE_* in engine/render/render_frame_input.h.
PROFILE_NAMESPACE_CODE = {"terrain": 1, "mesh": 2}

# Route families whose lane number comes from a numbered slot in the lane name,
# such as the 2 in "ramp[2]". The two single-lane families (filled field and
# mesh bundle) always use lane 0.
LANE_NAME_PATTERN = re.compile(r"^(?P<name>[a-z_]+)\[(?P<index>\d+)\]$")

# -----------------------------------------------------------------------------
# Pin roles
# -----------------------------------------------------------------------------
# Each "pin" is one file whose fingerprint the loader must confirm before it
# trusts anything. The role number is what the C++ side reports when a pin
# fails, so a failure message can say WHICH file was wrong rather than just
# "something was wrong".
PIN_ROLE_GLYPH_FACT_ARTIFACT = 0        # the 93 MiB source artifact
PIN_ROLE_GLYPH_FACT_SCHEMA = 1          # schema describing that artifact
PIN_ROLE_SCENE_FACT_HANDSHAKE_SCHEMA = 2  # renderer/compiler handshake contract
PIN_ROLE_RELATION_POINTER_MANIFEST = 3  # FL-4694 committed pointer manifest
PIN_ROLE_RELATION_POINTER_SCHEMA = 4    # schema for that manifest
PIN_ROLE_LOCAL_RELATION_KNN = 5         # LOCAL: nearest-neighbour relation cache
PIN_ROLE_LOCAL_GLYPH_RAMP = 6           # LOCAL: ramp ladders
PIN_ROLE_LOCAL_RAMP_RECORD_INDEX = 7    # LOCAL: record-to-ramp index
# FL-4686 PA. The artifact that enumerates every address in the profile-scoped
# space, including the ones deliberately left empty. Pinned because the sidecar
# now encodes a disposition for each of those addresses, so a pool that drifted
# would silently change what the program claims to own.
PIN_ROLE_COMPILED_POOL_ADDRESS_SPACE = 8
# FL-4694 E8: the exhaustive source relation and its shape contract. These are
# committed pins, never local-cache hints, because losing either would make the
# aspect-conditioned section unverifiable.
PIN_ROLE_GTR_V2_ARTIFACT = 9
PIN_ROLE_GTR_V2_SCHEMA = 10

# Bit flags stored in each pin record.
# Bit 0 set means "this file is regenerable and is deliberately not committed,
# so a fresh machine may legitimately not have it". Its absence is a NAMED
# unavailable state rather than corruption.
PIN_FLAG_LOCAL_REGENERABLE = 1 << 0

# Human-readable names, used only in the JSON receipt so a person reading the
# receipt does not have to decode role numbers by hand.
PIN_ROLE_NAMES = {
    PIN_ROLE_GLYPH_FACT_ARTIFACT: "glyph_fact_artifact",
    PIN_ROLE_GLYPH_FACT_SCHEMA: "glyph_fact_schema",
    PIN_ROLE_SCENE_FACT_HANDSHAKE_SCHEMA: "scene_fact_handshake_schema",
    PIN_ROLE_RELATION_POINTER_MANIFEST: "relation_pointer_manifest",
    PIN_ROLE_RELATION_POINTER_SCHEMA: "relation_pointer_schema",
    PIN_ROLE_LOCAL_RELATION_KNN: "local_relation_knn",
    PIN_ROLE_LOCAL_GLYPH_RAMP: "local_glyph_ramp",
    PIN_ROLE_LOCAL_RAMP_RECORD_INDEX: "local_ramp_record_index",
    PIN_ROLE_COMPILED_POOL_ADDRESS_SPACE: "compiled_pool_address_space",
    PIN_ROLE_GTR_V2_ARTIFACT: "gtr_v2_artifact",
    PIN_ROLE_GTR_V2_SCHEMA: "gtr_v2_schema",
}

# -----------------------------------------------------------------------------
# Declared-future-rights bit flags
# -----------------------------------------------------------------------------
# The artifact lists rights the runtime MAY be granted one day, after the
# handshake question is settled. We copy them into the sidecar purely as
# information. They are inert: the separate `runtime_consumption_allowed` byte
# stays 0, and the C++ pre-flight refuses the program whenever that byte is 0,
# no matter which right bits are set.
FUTURE_RIGHT_SELECT_FACT_FROM_SCENE_FACT = 1 << 0
FUTURE_RIGHT_SCORE_COMPILER_EMITTED_COSTS = 1 << 1
FUTURE_RIGHT_BITS = {
    "select_fact_id_from_scene_fact": FUTURE_RIGHT_SELECT_FACT_FROM_SCENE_FACT,
    "score_compiler_emitted_costs": FUTURE_RIGHT_SCORE_COMPILER_EMITTED_COSTS,
}

# FL-4512 D2/D3. Zero remains the loader's fail-closed unset value and is never
# emitted by this compiler. The other values are the complete scorer vocabulary
# currently declared by the pinned glyph-fact artifact.
SCORER_KIND_UNSET = 0
SCORER_KIND_SHAPE6_WEIGHTED_L2 = 1
SCORER_KIND_U7_MASK = 2
SCORER_KIND_S_PF = 3
# FL-4763. Explicit compiler-owned decline: the address is real and populated,
# and the compiler states that it has NO live structural operand to score it
# with. It is the only kind that carries a zero member-operand union, and the
# loader enforces that emptiness rather than trusting this emit.
SCORER_KIND_STRUCTURAL_DECLINE = 4
SCORER_KIND_BY_ID = {
    "fl4512.shape6_f8_w4.v2": SCORER_KIND_SHAPE6_WEIGHTED_L2,
    "u7.mask.v1": SCORER_KIND_U7_MASK,
    "S_pf.v1": SCORER_KIND_S_PF,
    "fl4763.structural_decline.v1": SCORER_KIND_STRUCTURAL_DECLINE,
}

LEGACY_HARRI_SCORER_ID = "fl4260.shape6_weighted_l2.v1"
EXACT_HARRI_SCORER_ID = "fl4512.shape6_f8_w4.v2"

# -----------------------------------------------------------------------------
# Fact policy flags and no-match bounds (format version 6, FL-4512 Phase 2)
# -----------------------------------------------------------------------------
# Mirrors Fl4683FactPolicyFlagV1 in platform/fl4683_glyph_program_loader.h.
# The bit says: when the live six-sample Harri operand is DEGENERATE (all six
# q0.16 dims exactly equal, the authored epsilon of zero), this row must
# DECLINE its claim instead of manufacturing a confident nearest-glyph. The
# loader rejects this flag on any non-Harri scorer, and rejects any bit this
# vocabulary does not name.
FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN = 0x1

# Route keys whose shape6 rows carry STRUCTURAL authority (they claim edges,
# directions, or flow for a cell). Only these may carry the degenerate-operand
# abstain flag: a flat surface has no structure to claim, so a structural row
# must stand aside and let the tonal pair answer.
STRUCTURAL_SHAPE6_ROUTE_KEYS = frozenset({"edge.v1", "direction.v1", "flow.v1"})
# Route keys whose shape6 rows carry TONAL authority (brightness ramps and
# density). A constant tonal operand is a legal description of a flat surface
# (`tonal_constant_operand_legal`), so tonal rows NEVER carry the flag.
TONAL_SHAPE6_ROUTE_KEYS = frozenset({"ramp.v1", "density.v1"})

# The only structural-decline fallback this binary format can carry: the
# selector's pass-1 rearbitration over the tonal pair. A pool that authored a
# different fallback names behavior this format cannot encode, so it fails the
# compile rather than being silently rewritten into the tonal rule.
NO_MATCH_POLICY_SUPPORTED_FALLBACK = "tonal"

# The complete field vocabulary of a materials[].no_match_policy object. An
# unknown field would be an authored policy distinction this compiler does not
# model; dropping it silently would ship a program that disobeys its own pool,
# so any unknown field fails the compile by name.
NO_MATCH_POLICY_KNOWN_FIELDS = frozenset(
    {
        "structural_degenerate_declines",
        "structural_decline_fallback",
        "tonal_constant_operand_legal",
        "max_error_q",
        # An authored nonzero epsilon has no compiled carrier in this format
        # (the flag encodes exactly epsilon == 0). The field is understood so
        # a nonzero value can be REFUSED by name instead of being unknown.
        "degenerate_epsilon_q0_16",
    }
)


def product_scorer_id(scorer_id: str) -> str:
    """Map the pool-era Harri label to the exact scorer exposed by GlyphFact."""
    if scorer_id == LEGACY_HARRI_SCORER_ID:
        return EXACT_HARRI_SCORER_ID
    return scorer_id

ROLE_WEIGHT_COUNT = 6
SHAPE6_COMPONENT_COUNT = 6
ROLE_WEIGHT_Q4_MIN = 0
ROLE_WEIGHT_Q4_MAX = 24


class CompileError(RuntimeError):
    """Raised when an input is missing, malformed, or contradicts a pin.

    Using one dedicated exception type lets `main()` catch exactly the
    fail-closed conditions and print them as a single named error, instead of
    letting a raw Python traceback leak out.
    """


def _root_path(path: Path) -> Path:
    """Resolve a repository-relative path without changing default behavior."""
    return path if path.is_absolute() else ROOT / path


def preview_path(value: str | Path, option: str) -> Path:
    from fl4512_compiler_paths import transaction_path
    return transaction_path(value, option, ROOT, CompileError)


def path_identity(path: Path) -> str:
    """Return a stable repository-relative path for receipt and pin identity."""
    resolved = _root_path(path).resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise CompileError(f"source_path_outside_repository:{path}") from exc


def authoring_path(value: str | Path, option: str) -> Path:
    """Authoring snapshots persist separately from disposable preview captures."""
    from fl4512_compiler_paths import transaction_path
    return transaction_path(value, option, ROOT, CompileError, authoring=True)


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    """Return the SHA-256 fingerprint of a file, as 64 lowercase hex characters.

    Parameters:
      path -- absolute path to the file to fingerprint.

    The file is read in 1 MiB chunks rather than all at once, because some of
    these inputs are around 100 MiB and reading them whole would waste memory.
    """
    digest = hashlib.sha256()
    with _root_path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)  # 1 MiB at a time
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    """Return the SHA-256 fingerprint of an in-memory byte string, as hex."""
    return hashlib.sha256(payload).hexdigest()


def hex_to_32_bytes(value: str, what: str) -> bytes:
    """Convert a 64-character hex fingerprint into the 32 raw bytes it encodes.

    Parameters:
      value -- the hex string, e.g. "11778b2c...".
      what  -- a label used only in the error message, so a failure names the
               field that was malformed.

    Two hex characters encode one byte, so 64 characters become 32 bytes. The
    binary sidecar stores raw bytes (32) rather than text (64) to halve the
    space and to let C++ compare with a single memcmp.
    """
    if not isinstance(value, str) or len(value) != 64:
        raise CompileError(f"{what} is not a 64-character sha256 hex string")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise CompileError(f"{what} is not valid hexadecimal: {exc}") from exc


def encode_path_field(relative_path: str) -> bytes:
    """Pack a repo-relative path into the fixed-size, zero-padded path field.

    Every pin record reserves exactly PIN_PATH_CAPACITY bytes for its path so
    that all records are the same size. The path is stored as UTF-8 bytes
    followed by zero bytes ("NUL padding"); C++ reads it as a normal
    zero-terminated string.
    """
    raw = relative_path.encode("utf-8")
    # One byte must remain for the terminating zero, hence the >= comparison.
    if len(raw) >= PIN_PATH_CAPACITY:
        raise CompileError(
            f"pin path '{relative_path}' needs {len(raw)} bytes, "
            f"capacity is {PIN_PATH_CAPACITY - 1}"
        )
    return raw + b"\x00" * (PIN_PATH_CAPACITY - len(raw))


def load_json(relative_path: Path) -> Any:
    """Read and parse a JSON file that must exist, or fail closed."""
    absolute = _root_path(relative_path)
    if not absolute.is_file():
        raise CompileError(f"required input is missing: {relative_path}")
    try:
        return json.loads(absolute.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CompileError(f"{relative_path} is not valid JSON: {exc}") from exc


# -----------------------------------------------------------------------------
# Pin collection
# -----------------------------------------------------------------------------
def collect_pins(
    pointer_manifest: Dict[str, Any],
    *,
    glyph_fact_path: Path = GLYPH_FACT_ARTIFACT,
    compiled_pool_path: Path = COMPILED_POOL_ADDRESS_SPACE,
    relation_pointer_manifest_path: Path = RELATION_POINTER_MANIFEST,
) -> List[Dict[str, Any]]:
    """Build the ordered list of files whose fingerprints the loader must confirm.

    Parameters:
      pointer_manifest -- the parsed contents of the FL-4694 pointer manifest.

    Returns a list of dictionaries, one per pinned file, each carrying:
      role       -- one of the PIN_ROLE_* numbers
      flags      -- PIN_FLAG_* bits (currently only "local and regenerable")
      path       -- repo-relative path as text
      byte_size  -- expected size in bytes (0 means "size not pinned")
      sha256     -- expected 64-character hex fingerprint

    WHY THE ORDER MATTERS: the C++ loader confirms pins in exactly this order
    and reports the first failure. Committed inputs come first so that a broken
    committed file is reported before an absent local cache, which is the more
    serious of the two conditions.
    """
    pins: List[Dict[str, Any]] = []

    # --- Committed inputs -----------------------------------------------------
    # These committed inputs are checked into the repository, so they must always be
    # present. Their absence is corruption, not a normal fresh-machine state.
    # We measure their size and fingerprint from disk right now; the receipt
    # records what we measured, so a later drift is detectable.
    for role, relative in (
        (PIN_ROLE_GLYPH_FACT_ARTIFACT, glyph_fact_path),
        (PIN_ROLE_GLYPH_FACT_SCHEMA, GLYPH_FACT_SCHEMA),
        (PIN_ROLE_SCENE_FACT_HANDSHAKE_SCHEMA, SCENE_FACT_HANDSHAKE_SCHEMA),
        (PIN_ROLE_RELATION_POINTER_MANIFEST, relation_pointer_manifest_path),
        (PIN_ROLE_RELATION_POINTER_SCHEMA, RELATION_POINTER_SCHEMA),
        # FL-4686 PA: the enumerated address space (see the role comment).
        (PIN_ROLE_COMPILED_POOL_ADDRESS_SPACE, compiled_pool_path),
        # FL-4694 E8: the exhaustive aspect-conditioned relation section is a
        # byte-for-byte projection of this committed table under this schema.
        (PIN_ROLE_GTR_V2_ARTIFACT, GTR_V2_ARTIFACT),
        (PIN_ROLE_GTR_V2_SCHEMA, GTR_V2_SCHEMA),
    ):
        absolute = _root_path(relative)
        if not absolute.is_file():
            raise CompileError(f"required committed input is missing: {relative}")
        pins.append(
            {
                "role": role,
                "flags": 0,  # committed: absence is an error, never "unavailable"
                "path": path_identity(relative),
                "byte_size": absolute.stat().st_size,
                "sha256": sha256_file(absolute),
            }
        )

    # --- Cross-check against fingerprints the FL-4694 manifest already pinned --
    # The FL-4694 manifest independently recorded the fingerprints of several of
    # the same files. If our freshly measured value disagrees with the value the
    # manifest recorded, then something drifted between the two lanes and we
    # must stop rather than silently prefer one of them.
    manifest_source_pins = {
        row["path"]: row["sha256"] for row in pointer_manifest.get("source_pins", [])
    }
    for pin in pins:
        expected = manifest_source_pins.get(pin["path"])
        if expected is not None and expected != pin["sha256"]:
            raise CompileError(
                f"pin disagreement for {pin['path']}: "
                f"fl4694 manifest recorded {expected}, disk currently has {pin['sha256']}"
            )

    # --- Local, regenerable caches -------------------------------------------
    # The FL-4694 manifest points at three large derived caches that are
    # deliberately NOT committed (together roughly 140 MiB). It records their
    # size and fingerprint. We copy those pins in verbatim: we must NOT measure
    # them from disk, because on this machine they might be stale, and on a
    # fresh machine they are simply absent. The manifest is the authority.
    local_role_by_name = {
        "glyph_relation_knn": PIN_ROLE_LOCAL_RELATION_KNN,
        "glyph_ramp": PIN_ROLE_LOCAL_GLYPH_RAMP,
        "ramp_record_index": PIN_ROLE_LOCAL_RAMP_RECORD_INDEX,
    }
    local_rows = pointer_manifest.get("local_artifacts", [])
    if len(local_rows) != len(local_role_by_name):
        raise CompileError(
            f"fl4694 manifest lists {len(local_rows)} local artifacts, "
            f"expected {len(local_role_by_name)}"
        )
    # Sorting by role number keeps the output byte-identical no matter what
    # order the manifest happens to list them in. Determinism is a hard
    # requirement here because the sidecar's own fingerprint is a pin.
    for row in sorted(local_rows, key=lambda item: local_role_by_name[item["name"]]):
        name = row["name"]
        if name not in local_role_by_name:
            raise CompileError(f"fl4694 manifest names an unexpected local artifact: {name}")
        pins.append(
            {
                "role": local_role_by_name[name],
                "flags": PIN_FLAG_LOCAL_REGENERABLE,  # absence is a NAMED state
                "path": row["path"],
                "byte_size": int(row["byte_size"]),
                "sha256": row["sha256"],
            }
        )

    return pins


# -----------------------------------------------------------------------------
# Section encoders
def scene_address_key_from_membership(
    key: Dict[str, Any], what: str
) -> Tuple[int, int, int, int]:
    """Turn one compiled row's membership key into the four-field lookup key.

    Parameters:
      key  -- a `scene_membership_key` mapping, from either the glyph-fact
              artifact or the compiled pool. Both use the same field names,
              which is why one reader serves both.
      what -- a short description of where this key came from, used only to make
              an error message say which row was at fault.

    Returns the tuple (namespace_code, material_key, route_family_bit,
    lane_index) -- the FOUR fields a compiled address is looked up by. Two
    fields (namespace and material) are not enough: the live renderer's own
    profile test uses only those two, and that mismatch is precisely why a cell
    can be owned by a profile and still have no compiled address. See FL-4686.

    Raises CompileError on anything it cannot name exactly. Guessing here would
    put a wrong address into a sorted table and make every later binary search
    quietly wrong.
    """
    namespace_name = str(key["profile_namespace"])
    namespace_code = PROFILE_NAMESPACE_CODE.get(namespace_name)
    if namespace_code is None:
        raise CompileError(
            f"{what} uses namespace {namespace_name!r}, which no renderer "
            "namespace code matches"
        )

    route_key = str(key["route_key"])
    family_bit = ROUTE_FAMILY_BIT.get(route_key)
    if family_bit is None:
        raise CompileError(
            f"{what} uses route key {route_key!r}, which the route-family "
            "discriminator does not define"
        )

    # Pull the lane number out of a name like "ramp[2]". A name with no
    # bracketed number is a single-lane family, which is lane 0.
    lane_name = str(key["lane_name"])
    match = LANE_NAME_PATTERN.match(lane_name)
    lane_index = int(match.group("index")) if match else 0
    if not 0 <= lane_index <= 0xFF:
        raise CompileError(f"{what} lane index {lane_index} out of range")

    material_key = int(key["material_key"])
    if not -0x8000 <= material_key <= 0x7FFF:
        raise CompileError(f"{what} material key {material_key} out of 16-bit range")

    return namespace_code, material_key, family_bit, lane_index


def collect_declared_absent_addresses(
    pool: Dict[str, Any], populated: Dict[Tuple[int, int, int, int], int]
) -> List[Tuple[int, int, int, int]]:
    """Find every address the program owns but holds no glyph for. FL-4686 PA.

    Parameters:
      pool      -- the parsed compiled pool, which enumerates the whole
                   profile-scoped address space.
      populated -- the lookup keys that the glyph-fact artifact did supply
                   glyphs for, mapped to their fact index.

    Returns the sorted list of four-field keys that exist in the pool and are
    marked as deliberately empty.

    THE THREE THINGS THIS FUNCTION REFUSES TO LET PASS
      1. A pool row whose state is neither "live" nor "empty_absence". A third
         state would mean the pool learned a distinction this compiler does not
         model, and bucketing it into one of the two known ones would be a
         guess.
      2. A pool row marked live whose address the glyph-fact artifact did NOT
         supply. That means the two artifacts were built from different inputs
         and the sidecar would claim coverage it does not have.
      3. A glyph-fact address that the pool does not enumerate at all. That
         means the address space is narrower than the sidecar assumes, so the
         guarantee this whole change exists to provide would be false.
    """
    rows = pool["rows"]
    declared_absent: List[Tuple[int, int, int, int]] = []
    pool_live: Dict[Tuple[int, int, int, int], int] = {}
    seen: Dict[Tuple[int, int, int, int], str] = {}

    for row_index, row in enumerate(rows):
        what = f"compiled pool row {row_index}"
        # The pool stores the same four key parts as the glyph-fact artifact,
        # just at the top level of the row rather than under a sub-object.
        lookup_key = scene_address_key_from_membership(
            {
                "profile_namespace": row["profile_namespace"],
                "material_key": row["material_key"],
                "route_key": row["route_key"],
                "lane_name": row["lane_name"],
            },
            what,
        )
        if lookup_key in seen:
            raise CompileError(
                f"{what} repeats the address {lookup_key}, which the enumerated "
                "address space may not do"
            )

        state = str(row["bucket_state"])
        if state == POOL_STATE_POPULATED:
            pool_live[lookup_key] = row_index
        elif state == POOL_STATE_DECLARED_ABSENT:
            declared_absent.append(lookup_key)
        else:
            raise CompileError(
                f"{what} has bucket_state {state!r}, which is neither "
                f"{POOL_STATE_POPULATED!r} nor {POOL_STATE_DECLARED_ABSENT!r}; "
                "this compiler will not guess which disposition it means"
            )
        seen[lookup_key] = state

    # Cross-check 2: the pool says live, the glyph-fact artifact must supply it.
    missing_from_artifact = sorted(set(pool_live) - set(populated))
    if missing_from_artifact:
        raise CompileError(
            f"{len(missing_from_artifact)} address(es) are marked live in the "
            f"compiled pool but supply no glyphs in the glyph-fact artifact, "
            f"first {missing_from_artifact[0]}; the two artifacts disagree"
        )
    # Cross-check 3: every glyph-fact address must exist in the enumerated space.
    missing_from_pool = sorted(set(populated) - set(pool_live))
    if missing_from_pool:
        raise CompileError(
            f"{len(missing_from_pool)} address(es) supply glyphs but are absent "
            f"from the enumerated address space, first {missing_from_pool[0]}; "
            "the pool does not enumerate every address the facts use"
        )

    return sorted(declared_absent)


def encode_scene_address_index(
    facts: List[Dict[str, Any]], pool: Dict[str, Any]
) -> Tuple[bytes, int, int]:
    """Build the sorted lookup table over the whole address space. FL-4686 PA.

    Parameters:
      facts -- the `facts` array straight out of the compiled artifact, in the
               same order `encode_fact_table` writes them, so the fact index
               stored here addresses the right record.
      pool  -- the parsed compiled pool, which enumerates every address in the
               profile-scoped space including the deliberately empty ones.

    Returns a 3-tuple of (packed bytes, total entries, declared-absent entries).

    WHAT EACH ENTRY HOLDS (12 bytes, little-endian)
      profile_namespace_code  i16  1 = terrain, 2 = mesh
      material_key            i16  which material profile
      route_family_bit        u8   one Fl4683RouteFamilyV1 bit
      lane_index              u8   the numbered slot inside that family
      disposition             u8   populated (1) or declared absent (2)
      reserved0               u8   padding, keeps the record 12 bytes and aligned
      fact_index              u32  the fact record, or NO_FACT_INDEX when absent

    WHY THE TABLE COVERS ADDRESSES WITH NO GLYPHS (format version 3)
    Before this change the table held one entry per compiled fact, so an address
    the compiler had deliberately left empty was simply not in the table. A
    renderer asking about such a cell got "no such address", which is a failure,
    and a failure is the one answer that must never be produced by a cell the
    program is supposed to own. Now every address in the profile-scoped space is
    present and says which of the two things it is. Only an address outside that
    space is missing from the table, and only that is a failure.

    WHY SORTED: the C++ reader finds an entry by binary search -- repeatedly
    halving the range rather than scanning. Binary search is only correct on
    sorted data, so the order is part of the format, not a convenience. Sorting
    also makes the output byte-identical between runs.

    FAIL-CLOSED: two entries with the same key would make a lookup ambiguous,
    which is the exact failure the route-family discriminator was added to
    remove. Rather than pick one, this refuses to build.
    """
    populated: Dict[Tuple[int, int, int, int], int] = {}
    for fact_index, fact in enumerate(facts):
        lookup_key = scene_address_key_from_membership(
            fact["scene_membership_key"], f"facts[{fact_index}]"
        )
        if lookup_key in populated:
            raise CompileError(
                f"two compiled addresses share the scene key {lookup_key}: facts "
                f"{populated[lookup_key]} and {fact_index}; a lookup could not "
                "choose between them"
            )
        populated[lookup_key] = fact_index

    declared_absent = collect_declared_absent_addresses(pool, populated)

    # Build one list of (key, disposition, fact_index) covering both kinds, then
    # sort it once by the key alone. Sorting the combined list -- rather than
    # concatenating two separately sorted lists -- is what guarantees the single
    # ascending order the binary search requires.
    entries: List[Tuple[Tuple[int, int, int, int], int, int]] = []
    for lookup_key, fact_index in populated.items():
        entries.append((lookup_key, ADDRESS_DISPOSITION_POPULATED, fact_index))
    for lookup_key in declared_absent:
        entries.append((lookup_key, ADDRESS_DISPOSITION_DECLARED_ABSENT, NO_FACT_INDEX))
    entries.sort(key=lambda entry: entry[0])

    seen_keys: Dict[Tuple[int, int, int, int], int] = {}
    packed: List[bytes] = []
    for lookup_key, disposition, fact_index in entries:
        if lookup_key in seen_keys:
            raise CompileError(
                f"address {lookup_key} appears twice in the index, once as "
                f"disposition {seen_keys[lookup_key]} and once as {disposition}"
            )
        seen_keys[lookup_key] = disposition
        namespace_code, material_key, family_bit, lane_index = lookup_key
        packed.append(
            struct.pack(
                "<hhBBBBI",
                namespace_code,
                material_key,
                family_bit,
                lane_index,
                disposition,
                0,
                fact_index,
            )
        )

    blob = b"".join(packed)
    if len(blob) != len(entries) * SCENE_ADDRESS_RECORD_SIZE:
        raise CompileError("scene address index packed to an unexpected size")
    return blob, len(entries), len(declared_absent)


# -----------------------------------------------------------------------------
def quantize_role_weight_q4(value: Any, what: str) -> int:
    """Apply the frozen Harri profile-weight transform.

    q_W(w) = round-half-up(clamp(w, 0, 1.5) * 16), yielding one byte in
    0..24. Decimal arithmetic avoids Python's binary-float and ties-to-even
    rounding rules from becoming a second compiler policy.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompileError(f"{what} is not a numeric role weight")
    weight = Decimal(str(value))
    if not weight.is_finite():
        raise CompileError(f"{what} is not a finite role weight")
    clamped = min(max(weight, Decimal("0")), Decimal("1.5"))
    quantized = int(
        (clamped * Decimal("16")).to_integral_value(rounding=ROUND_HALF_UP)
    )
    if not ROLE_WEIGHT_Q4_MIN <= quantized <= ROLE_WEIGHT_Q4_MAX:
        raise CompileError(f"{what} quantized outside q4 range: {quantized}")
    return quantized


def build_profile_role_weight_index(
    profiles: List[Dict[str, Any]],
) -> Dict[str, Tuple[int, ...]]:
    """Joinable profile id -> six frozen q4 Harri role weights."""
    role_weights_by_profile: Dict[str, Tuple[int, ...]] = {}
    for profile_index, profile in enumerate(profiles):
        profile_id = profile.get("profile_id")
        if not isinstance(profile_id, str) or not profile_id:
            raise CompileError(f"profiles[{profile_index}].profile_id is missing")
        if profile_id in role_weights_by_profile:
            raise CompileError(f"duplicate artifact profile_id: {profile_id}")
        role_weights = profile.get("role_weights")
        if not isinstance(role_weights, list) or len(role_weights) != ROLE_WEIGHT_COUNT:
            raise CompileError(
                f"profiles[{profile_index}] role_weights must hold "
                f"exactly {ROLE_WEIGHT_COUNT} values"
            )
        role_weights_by_profile[profile_id] = tuple(
            quantize_role_weight_q4(
                value, f"profiles[{profile_index}].role_weights[{weight_index}]"
            )
            for weight_index, value in enumerate(role_weights)
        )
    return role_weights_by_profile


def build_glyph_record_index(
    glyph_records: List[Dict[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    """Build the sole glyph-record join used by fact-member morphology emit."""
    records_by_id: Dict[int, Dict[str, Any]] = {}
    for record_index, record in enumerate(glyph_records):
        record_id = record.get("glyph_record_id")
        if isinstance(record_id, bool) or not isinstance(record_id, int):
            raise CompileError(
                f"glyph_records[{record_index}].glyph_record_id is not an integer"
            )
        if not 0 <= record_id <= 0xFFFFFFFF:
            raise CompileError(
                f"glyph_records[{record_index}].glyph_record_id {record_id} out of range"
            )
        if record_id in records_by_id:
            raise CompileError(f"duplicate glyph_record_id: {record_id}")
        records_by_id[record_id] = record
    return records_by_id


def member_shape6_norm_q0_16(
    record: Dict[str, Any], fact_index: int, record_id: int
) -> Tuple[int, ...]:
    """Read and validate the compiler-owned six-sample member morphology."""
    morphology = record.get("morphology")
    shape6 = morphology.get("shape6_norm_q0_16") if isinstance(morphology, dict) else None
    if not isinstance(shape6, list) or len(shape6) != SHAPE6_COMPONENT_COUNT:
        raise CompileError(
            f"facts[{fact_index}] member id {record_id} is missing "
            f"morphology.shape6_norm_q0_16[{SHAPE6_COMPONENT_COUNT}]"
        )
    values: List[int] = []
    for component_index, value in enumerate(shape6):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
            raise CompileError(
                f"facts[{fact_index}] member id {record_id} shape6 component "
                f"{component_index} is not q0_16"
            )
        values.append(value)
    return tuple(values)


def decode_u64_le_hex(value: Any, what: str) -> bytes:
    """Decode the artifact's canonical eight little-endian carrier bytes."""
    if not isinstance(value, str) or len(value) != 16:
        raise CompileError(f"{what} is not a 16-character u64 little-endian hex value")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise CompileError(f"{what} is not valid hexadecimal: {exc}") from exc
    if len(raw) != 8 or raw == b"\x00" * 8:
        raise CompileError(f"{what} is empty or malformed")
    return raw


def build_pool_candidate_descriptor_index(
    pool: Dict[str, Any],
) -> Dict[str, Tuple[str, Dict[int, Dict[str, Any]]]]:
    """Index compiled scorer operands by sealed row identity and glyph id."""
    rows = pool.get("rows")
    if not isinstance(rows, list):
        raise CompileError("compiled pool rows are missing")
    descriptors_by_row: Dict[str, Tuple[str, Dict[int, Dict[str, Any]]]] = {}
    for row_index, row in enumerate(rows):
        row_sha256 = row.get("row_sha256")
        if not isinstance(row_sha256, str) or len(row_sha256) != 64:
            raise CompileError(f"compiled pool row {row_index} has invalid row_sha256")
        if row_sha256 in descriptors_by_row:
            raise CompileError(f"compiled pool duplicates row_sha256 {row_sha256}")
        scorer_id = row.get("scorer_id")
        raw_descriptors = row.get("candidate_descriptors")
        if not isinstance(scorer_id, str) or not isinstance(raw_descriptors, list):
            raise CompileError(f"compiled pool row {row_index} scorer operands are missing")
        descriptors: Dict[int, Dict[str, Any]] = {}
        for descriptor_index, descriptor in enumerate(raw_descriptors):
            glyph_id = descriptor.get("glyph_id")
            if isinstance(glyph_id, bool) or not isinstance(glyph_id, int):
                raise CompileError(
                    f"compiled pool row {row_index} descriptor {descriptor_index} "
                    "has invalid glyph_id"
                )
            if glyph_id in descriptors:
                raise CompileError(
                    f"compiled pool row {row_index} duplicates descriptor glyph {glyph_id}"
                )
            descriptors[glyph_id] = descriptor
        descriptors_by_row[row_sha256] = (product_scorer_id(scorer_id), descriptors)
    return descriptors_by_row


def build_pool_material_index(pool: Dict[str, Any]) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """Index compiled material policy by the fact's namespace/material key."""
    materials = pool.get("materials")
    if not isinstance(materials, list):
        raise CompileError("compiled pool materials are missing")
    materials_by_key: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for material_index, material in enumerate(materials):
        namespace = material.get("profile_namespace")
        material_key = material.get("material_key")
        if not isinstance(namespace, str) or isinstance(material_key, bool) or not isinstance(material_key, int):
            raise CompileError(f"compiled pool material {material_index} has invalid key")
        key = (namespace, material_key)
        if key in materials_by_key:
            raise CompileError(f"compiled pool duplicates material policy {key}")
        materials_by_key[key] = material
    return materials_by_key


def fact_primitive_policy(
    scorer_kind: int,
    membership_key: Dict[str, Any],
    pool_materials: Dict[Tuple[str, int], Dict[str, Any]],
    fact_index: int,
) -> Tuple[int, int, int]:
    """Return the pinned S_pf region, priority, and filled-extent enter gate."""
    if scorer_kind != SCORER_KIND_S_PF:
        return 0, 0, 0
    namespace = membership_key.get("profile_namespace")
    material_key = membership_key.get("material_key")
    material = pool_materials.get((namespace, material_key))
    if material is None:
        raise CompileError(
            f"facts[{fact_index}] primitive scorer has no compiled material policy"
        )
    region_class = require_u16(
        material.get("filled_region_class"),
        f"facts[{fact_index}] primitive_target_region_class",
    )
    target_priority = require_u16(
        material.get("filled_priority_u16"),
        f"facts[{fact_index}] primitive_target_priority_u16",
    )
    extent_admission = material.get("projected_feature_extent_admission")
    filled_threshold = (
        extent_admission.get("filled_threshold")
        if isinstance(extent_admission, dict)
        else None
    )
    extent_enter = (
        filled_threshold.get("enter_q")
        if isinstance(filled_threshold, dict)
        else None
    )
    if isinstance(extent_enter, bool) or not isinstance(extent_enter, int) or not 0 <= extent_enter <= 0xFFFFFFFF:
        raise CompileError(
            f"facts[{fact_index}] primitive_extent_enter_q is not an unsigned 32-bit integer"
        )
    return region_class, target_priority, extent_enter


def require_u16(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        raise CompileError(f"{what} is not an unsigned 16-bit integer")
    return value


def require_u32(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise CompileError(f"{what} is not an unsigned 32-bit integer")
    return value


def material_no_match_policy(
    membership_key: Dict[str, Any],
    pool_materials: Dict[Tuple[str, int], Dict[str, Any]],
    fact_index: int,
) -> Dict[str, Any]:
    """Fetch and validate the owning material's authored no_match_policy.

    FL-4512 Phase 2. The policy is JOINED from the compiled pool's materials[]
    by (profile_namespace, material_key) -- the same two fields the fact's own
    scene membership key carries -- so no other input (in particular the
    fl4676 glyph-fact artifact's profiles[], which carry only role weights)
    needs regeneration to source it.

    FAIL-CLOSED CONDITIONS, each with a stable machine-greppable reason:
      no_match_policy_missing        -- the material has no no_match_policy at
                                        all. This is what a revision-2 pool
                                        looks like; the format-6 sidecar cannot
                                        be built from it, because every shape6
                                        row now carries a compiled bound.
      no_match_policy_unknown_field  -- the pool authored a policy distinction
                                        this compiler does not model.
      no_match_policy_fallback_unsupported -- the authored structural-decline
                                        fallback is not the tonal pass-1
                                        rearbitration, the only fallback this
                                        binary format encodes.
      no_match_policy_tonal_flag_uncarriable -- tonal constant operands were
                                        authored ILLEGAL, but this format has
                                        no carrier for flagging tonal rows
                                        (the flag is structural-only by
                                        contract), so the intent cannot ship.
      no_match_policy_epsilon_uncarriable -- an authored nonzero degenerate
                                        epsilon; the compiled flag encodes
                                        exactly epsilon == 0.
    """
    namespace = membership_key.get("profile_namespace")
    material_key = membership_key.get("material_key")
    material = pool_materials.get((namespace, material_key))
    if material is None:
        raise CompileError(
            f"facts[{fact_index}] shape6 scorer has no compiled material policy"
        )
    policy = material.get("no_match_policy")
    if not isinstance(policy, dict):
        raise CompileError(
            f"no_match_policy_missing:{namespace}:{material_key}:"
            "compiled pool materials[] carry no no_match_policy; a pool at "
            "revision 2 or older cannot source the format-6 fact policy and "
            "this compiler will not guess bounds"
        )
    unknown = sorted(set(policy) - NO_MATCH_POLICY_KNOWN_FIELDS)
    if unknown:
        raise CompileError(
            f"no_match_policy_unknown_field:{namespace}:{material_key}:{unknown[0]}"
        )
    declines = policy.get("structural_degenerate_declines")
    if not isinstance(declines, bool):
        raise CompileError(
            f"no_match_policy_missing:{namespace}:{material_key}:"
            "structural_degenerate_declines is not a boolean"
        )
    fallback = policy.get("structural_decline_fallback")
    if fallback != NO_MATCH_POLICY_SUPPORTED_FALLBACK:
        raise CompileError(
            f"no_match_policy_fallback_unsupported:{namespace}:{material_key}:"
            f"{fallback!r}"
        )
    if policy.get("tonal_constant_operand_legal") is not True:
        raise CompileError(
            f"no_match_policy_tonal_flag_uncarriable:{namespace}:{material_key}"
        )
    epsilon = policy.get("degenerate_epsilon_q0_16", 0)
    if require_u16(
        epsilon,
        f"no_match_policy degenerate_epsilon_q0_16 for {namespace}:{material_key}",
    ) != 0:
        raise CompileError(
            f"no_match_policy_epsilon_uncarriable:{namespace}:{material_key}:{epsilon}"
        )
    max_error_q = policy.get("max_error_q")
    if not isinstance(max_error_q, dict) or set(max_error_q) != {"structural", "tonal"}:
        raise CompileError(
            f"no_match_policy_missing:{namespace}:{material_key}:"
            "max_error_q must hold exactly the structural and tonal bounds"
        )
    require_u32(
        max_error_q["structural"],
        f"no_match_policy max_error_q.structural for {namespace}:{material_key}",
    )
    require_u32(
        max_error_q["tonal"],
        f"no_match_policy max_error_q.tonal for {namespace}:{material_key}",
    )
    return policy


def fact_shape6_no_match_policy(
    scorer_kind: int,
    membership_key: Dict[str, Any],
    pool_materials: Dict[Tuple[str, int], Dict[str, Any]],
    fact_index: int,
) -> Tuple[int, int]:
    """Return the (policy_flags, no_match_bound_q) pair for one fact record.

    FL-4512 Phase 2, format version 6. What each scorer kind gets:

      HARRI shape6  policy byte: FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN exactly
                    when the row's route role is STRUCTURAL (edge, direction,
                    flow) AND the owning material authored
                    structural_degenerate_declines. Tonal rows (ramp, density)
                    NEVER carry it: a constant tonal operand legally describes
                    a flat surface (tonal_constant_operand_legal).
                    u32 bound: max_error_q["structural"] for structural roles,
                    max_error_q["tonal"] for tonal roles. Units are the shape6
                    weighted-L2 fixed point the scorer itself produces.

      U7 mask       always (0, 0). UNIT CONSTRAINT: the authored max_error_q
                    values are in shape6 weighted-L2 units, and the U7 mask
                    scorer produces scores in DIFFERENT units (popcount-based
                    mask distance). Writing a shape6-unit bound into a U7 row
                    would silently compare unlike quantities, so U7 rows stay
                    unbounded (0) until a policy authored in U7 units exists.

      primitive     always (0, 0) from THIS function; the record's u32 keeps
                    its v5 extent-enter meaning via fact_primitive_policy, and
                    the loader rejects the abstain flag outside shape6.

      decline       always (0, 0); it has no operand and reaches no score or
                    bound comparison.
    """
    if scorer_kind != SCORER_KIND_SHAPE6_WEIGHTED_L2:
        return 0, 0
    policy = material_no_match_policy(membership_key, pool_materials, fact_index)
    route_key = str(membership_key.get("route_key"))
    if route_key in STRUCTURAL_SHAPE6_ROUTE_KEYS:
        flags = (
            FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN
            if policy["structural_degenerate_declines"]
            else 0
        )
        return flags, int(policy["max_error_q"]["structural"])
    if route_key in TONAL_SHAPE6_ROUTE_KEYS:
        # Never flagged, by the tonal_constant_operand_legal contract above.
        return 0, int(policy["max_error_q"]["tonal"])
    raise CompileError(
        f"facts[{fact_index}] shape6 route key {route_key!r} is neither "
        "structural nor tonal; this compiler will not guess which bound scopes it"
    )


def require_u8(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise CompileError(f"{what} is not an unsigned 8-bit integer")
    return value


def encode_member_scorer_operand(
    scorer_kind: int,
    glyph_record: Dict[str, Any],
    pool_descriptors: Dict[int, Dict[str, Any]],
    fact_index: int,
    record_id: int,
) -> bytes:
    """Encode the 12-byte union consumed only by the fact's named scorer.

    Tonal payload: shape6_norm_q0_16[6].
    u7 payload: mask8 u64 LE, endpoint u8, junction u8, reserved u16.
    S_pf payload: mask8 u64 LE, filled-region u16, detail u16.
    Structural decline: twelve zero bytes -- an explicitly EMPTY operand union.

    The stride is the same twelve bytes for every kind, so the decline carries
    no operand without changing the member stride or the program's byte size.
    """
    if scorer_kind == SCORER_KIND_STRUCTURAL_DECLINE:
        # FL-4763: emit nothing rather than a convenient stand-in. Packing the
        # glyph's shape6 vector here "just to fill the union" would re-create the
        # tonal operand this identity exists to withhold, and the runtime would
        # then be one edit away from scoring it.
        return b"\x00" * 12

    if scorer_kind == SCORER_KIND_SHAPE6_WEIGHTED_L2:
        shape6_norm = member_shape6_norm_q0_16(
            glyph_record, fact_index, record_id
        )
        return struct.pack("<6H", *shape6_norm)

    if scorer_kind == SCORER_KIND_U7_MASK:
        connectivity = glyph_record.get("connectivity")
        structure = glyph_record.get("structure")
        if not isinstance(connectivity, dict) or not isinstance(structure, dict):
            raise CompileError(
                f"facts[{fact_index}] u7 member id {record_id} is missing "
                "connectivity or structure"
            )
        mask8 = decode_u64_le_hex(
            connectivity.get("mask8_u64_le_hex"),
            f"facts[{fact_index}] u7 member id {record_id} mask8",
        )
        endpoint_count = require_u8(
            structure.get("endpoint_count"),
            f"facts[{fact_index}] u7 member id {record_id} endpoint_count",
        )
        junction_count = require_u8(
            structure.get("junction_count"),
            f"facts[{fact_index}] u7 member id {record_id} junction_count",
        )
        descriptor = pool_descriptors.get(record_id)
        if descriptor is None:
            raise CompileError(
                f"facts[{fact_index}] u7 member id {record_id} has no "
                "compiled-pool candidate descriptor"
            )
        pool_mask8 = decode_u64_le_hex(
            descriptor.get("mask8_u64_le_hex"),
            f"facts[{fact_index}] u7 pool member id {record_id} mask8",
        )
        pool_endpoints = require_u8(
            descriptor.get("endpoint_count"),
            f"facts[{fact_index}] u7 pool member id {record_id} endpoint_count",
        )
        pool_junctions = require_u8(
            descriptor.get("junction_count"),
            f"facts[{fact_index}] u7 pool member id {record_id} junction_count",
        )
        if (pool_mask8, pool_endpoints, pool_junctions) != (
            mask8,
            endpoint_count,
            junction_count,
        ):
            raise CompileError(
                f"facts[{fact_index}] u7 member id {record_id} disagrees with "
                "the compiled-pool scorer operands"
            )
        return struct.pack("<8sBBH", mask8, endpoint_count, junction_count, 0)

    if scorer_kind == SCORER_KIND_S_PF:
        descriptor = pool_descriptors.get(record_id)
        if descriptor is None:
            raise CompileError(
                f"facts[{fact_index}] primitive member id {record_id} has no "
                "compiled-pool candidate descriptor"
            )
        mask8 = decode_u64_le_hex(
            descriptor.get("mask8_u64_le_hex"),
            f"facts[{fact_index}] primitive member id {record_id} mask8",
        )
        connectivity = glyph_record.get("connectivity")
        glyph_mask8 = decode_u64_le_hex(
            connectivity.get("mask8_u64_le_hex")
            if isinstance(connectivity, dict)
            else None,
            f"facts[{fact_index}] primitive glyph member id {record_id} mask8",
        )
        if glyph_mask8 != mask8:
            raise CompileError(
                f"facts[{fact_index}] primitive member id {record_id} mask8 "
                "disagrees with the glyph record"
            )
        region_class = require_u16(
            descriptor.get("filled_region_class"),
            f"facts[{fact_index}] primitive member id {record_id} filled_region_class",
        )
        detail_u16 = require_u16(
            descriptor.get("detail_u16"),
            f"facts[{fact_index}] primitive member id {record_id} detail_u16",
        )
        return struct.pack("<8sHH", mask8, region_class, detail_u16)

    raise CompileError(f"facts[{fact_index}] scorer kind {scorer_kind} is not executable")


def encode_fact_table(
    facts: List[Dict[str, Any]],
    glyph_records: List[Dict[str, Any]],
    profiles: List[Dict[str, Any]],
    pool: Dict[str, Any],
) -> Tuple[bytes, bytes, bytes, int, int]:
    """Turn the artifact's 99 facts into three flat binary tables.

    Parameters:
      facts         -- the `facts` array straight out of the compiled artifact.
      glyph_records -- compiler-owned morphology joined by glyph_record_id.
      profiles      -- compiler-owned role weights joined by source_profile_id.
      pool          -- compiler-owned primitive descriptors joined by row/glyph.

    Returns a 5-tuple:
      fact_bytes    -- the fact table (99 fixed-size records)
      member_bytes  -- every fact's glyph members, concatenated
      edge_bytes    -- every fact's relation edges, concatenated
      member_total  -- how many member entries were written
      edge_total    -- how many edge entries were written

    WHAT A "FACT" IS: one compiled decision address. It says "for this material,
    in this role, in this lane, these specific glyph shapes are legal, and here
    is how strongly each one belongs". `glyph_members` is that legal list.

    WHY THREE TABLES INSTEAD OF ONE: facts have variable-length member lists.
    Storing variable-length data inline would destroy the fixed-stride property
    that makes the C++ reader trivial. Instead each fact stores an offset and a
    count pointing into one shared member array -- the standard "CSR" or
    "offset + count" layout used by mesh and graph formats.
    """
    fact_records: List[bytes] = []
    member_records: List[bytes] = []
    edge_records: List[bytes] = []
    glyph_records_by_id = build_glyph_record_index(glyph_records)
    role_weights_by_profile = build_profile_role_weight_index(profiles)
    pool_descriptors_by_row = build_pool_candidate_descriptor_index(pool)
    pool_materials = build_pool_material_index(pool)

    for fact_index, fact in enumerate(facts):
        membership_key = fact["scene_membership_key"]

        scorer_id = membership_key.get("scorer_id")
        scorer_kind = SCORER_KIND_BY_ID.get(scorer_id)
        if scorer_kind is None:
            raise CompileError(
                f"facts[{fact_index}] has missing or unknown scorer_id: {scorer_id!r}"
            )

        row_sha256 = membership_key.get("row_sha256")
        pool_row = pool_descriptors_by_row.get(row_sha256)
        if pool_row is None:
            raise CompileError(
                f"facts[{fact_index}] row_sha256 does not name a compiled-pool row"
            )
        pool_scorer_id, pool_descriptors = pool_row
        if pool_scorer_id != scorer_id:
            raise CompileError(
                f"facts[{fact_index}] scorer {scorer_id!r} disagrees with "
                f"compiled-pool scorer {pool_scorer_id!r}"
            )

        source_profile_id = fact.get("source_profile_id")
        if not isinstance(source_profile_id, str) or not source_profile_id:
            raise CompileError(f"facts[{fact_index}].source_profile_id is missing")
        role_weights_q4 = role_weights_by_profile.get(source_profile_id)
        if role_weights_q4 is None:
            raise CompileError(
                f"facts[{fact_index}] source_profile_id {source_profile_id!r} "
                "does not name an artifact profile"
            )
        primitive_region_class, primitive_target_priority, primitive_extent_enter = (
            fact_primitive_policy(
                scorer_kind, membership_key, pool_materials, fact_index
            )
        )
        # FL-4512 Phase 2 (format version 6). The compiled abstention flag and
        # the per-row no-match bound, joined from the pool's material policy.
        # Nonzero only for HARRI shape6 rows; see fact_shape6_no_match_policy
        # for the role scoping and the U7 unit constraint.
        policy_flags, shape6_no_match_bound_q = fact_shape6_no_match_policy(
            scorer_kind, membership_key, pool_materials, fact_index
        )
        # The record's trailing u32 is SCOPED BY SCORER KIND (format v6):
        #   primitive -> the extent-enter admission gate, exactly as in v5;
        #   shape6    -> the compiled no-match bound (0 = unbounded);
        #   u7        -> 0, because the authored bounds are in shape6 units
        #                and the U7 mask scorer's units differ (see above).
        #   decline   -> 0, because the row carries no operand to bound.
        # Exactly one of the two sources is ever nonzero, enforced by the two
        # policy functions each returning zeros outside their own scorer kind.
        threshold_u32 = (
            primitive_extent_enter
            if scorer_kind == SCORER_KIND_S_PF
            else shape6_no_match_bound_q
        )

        # `fact_id` is a URI-style string such as
        # "glyphfact://fl4676/v1/489dc3297af2719a". The handshake's selector
        # input carries a 32-byte `requested_glyph_fact_id_sha256`, so we store
        # the fingerprint OF THAT STRING, giving a fixed-width key the C++ side
        # can compare with memcmp and that never needs string handling.
        fact_id_digest = hashlib.sha256(fact["fact_id"].encode("utf-8")).digest()

        # `fact_sha256` is the compiler's own fingerprint of the whole fact.
        fact_digest = hex_to_32_bytes(fact["fact_sha256"], f"facts[{fact_index}].fact_sha256")

        # `row_sha256` identifies the compiled ADDRESS (material + role + lane +
        # scorer). The handshake calls this the `compiled_address_sha256`.
        address_digest = hex_to_32_bytes(
            membership_key["row_sha256"],
            f"facts[{fact_index}].scene_membership_key.row_sha256",
        )

        # Record where this fact's members start inside the shared member array.
        member_offset = len(member_records)
        for member in fact["glyph_members"]:
            record_id = int(member["glyph_record_id"])
            weight = int(member["membership_weight_q0_16"])
            # "q0_16" means the value is a fraction stored as a whole number:
            # 0 means 0.0 and 65535 means (almost) 1.0. Weights across one
            # fact are compiled to add up to exactly 65535.
            if not 0 <= record_id <= 0xFFFFFFFF:
                raise CompileError(f"facts[{fact_index}] member id {record_id} out of range")
            if not 0 <= weight <= 0xFFFF:
                raise CompileError(f"facts[{fact_index}] member weight {weight} out of range")
            glyph_record = glyph_records_by_id.get(record_id)
            if glyph_record is None:
                raise CompileError(
                    f"facts[{fact_index}] member id {record_id} has no glyph record"
                )
            scorer_operand = encode_member_scorer_operand(
                scorer_kind,
                glyph_record,
                pool_descriptors,
                fact_index,
                record_id,
            )
            if len(scorer_operand) != 12:
                raise CompileError(
                    f"facts[{fact_index}] member id {record_id} scorer operand "
                    "did not pack to 12 bytes"
                )
            # Version 5 member contract: id, admission weight, a 12-byte union
            # interpreted only by the fact's scorer kind, and zero reserved.
            member_records.append(
                struct.pack("<IH12sH", record_id, weight, scorer_operand, 0)
            )
        member_count = len(member_records) - member_offset

        # Same offset/count treatment for the compiled relation edges. An "edge"
        # says: glyph A may be replaced by glyph B at this compiled cost. Edges
        # are produced offline only; the runtime is forbidden to invent them.
        edge_offset = len(edge_records)
        projection = fact["relation_projection"]
        for edge in projection["edges"]:
            edge_records.append(
                # "<IIIHHHHI": from-id, to-id, cost, weight, confidence,
                # aspect, padding, and the index of the fact that owns the edge.
                struct.pack(
                    "<IIIHHHHI",
                    int(edge["from_glyph_record_id"]),
                    int(edge["to_glyph_record_id"]),
                    int(edge["compiled_transform_cost"]),
                    int(edge["membership_weight_q0_16"]),
                    int(edge["relation_confidence_q0_16"]),
                    int(edge["aspect"]),
                    0,
                    fact_index,
                )
            )
        edge_count = len(edge_records) - edge_offset

        capacity = int(membership_key["capacity"])
        subset_weight_sum = int(fact["subset_weight_sum_q0_16"])

        # A fact with no legal glyphs would be an "empty legal row", which the
        # handshake names as its own rejection reason. The offline compiler must
        # never emit one, so finding one here is a hard stop.
        if member_count == 0:
            raise CompileError(f"facts[{fact_index}] has an empty legal row")
        if member_count > capacity:
            raise CompileError(
                f"facts[{fact_index}] has {member_count} members but capacity {capacity}"
            )
        if subset_weight_sum != 65535:
            raise CompileError(
                f"facts[{fact_index}] weights sum to {subset_weight_sum}, expected 65535"
            )

        # "<32s32s32sIHHIHH": three 32-byte digests, then
        # member_offset(4) member_count(2) capacity(2)
        # edge_offset(4) edge_count(2) subset_weight_sum(2) = 112 bytes. Format
        # v5 appended scorer_kind(1), role_weights_q4[6], one reserved byte,
        # then the S_pf target region(2), target priority(2), and the
        # extent-enter gate(4). Format v6 (FL-4512 Phase 2) spends that former
        # reserved byte on `policy_flags` and re-scopes the trailing u32 by
        # scorer kind (see `threshold_u32` above). Every byte offset and the
        # 128-byte total are unchanged from v5.
        fact_records.append(
            struct.pack(
                "<32s32s32sIHHIHHB6BBHHI",
                fact_id_digest,
                fact_digest,
                address_digest,
                member_offset,
                member_count,
                capacity,
                edge_offset,
                edge_count,
                subset_weight_sum,
                scorer_kind,
                *role_weights_q4,
                policy_flags,
                primitive_region_class,
                primitive_target_priority,
                threshold_u32,
            )
        )

    fact_bytes = b"".join(fact_records)
    member_bytes = b"".join(member_records)
    edge_bytes = b"".join(edge_records)

    # Guard the fixed-stride promise. If these ever disagree, the C++ reader
    # would index into the middle of a record and silently read nonsense.
    if len(fact_bytes) != len(facts) * FACT_RECORD_SIZE:
        raise CompileError("fact record stride does not match FACT_RECORD_SIZE")
    if len(member_bytes) != len(member_records) * FACT_MEMBER_SIZE:
        raise CompileError("member record stride does not match FACT_MEMBER_SIZE")
    if len(edge_bytes) != len(edge_records) * RELATION_EDGE_SIZE:
        raise CompileError("edge record stride does not match RELATION_EDGE_SIZE")

    return fact_bytes, member_bytes, edge_bytes, len(member_records), len(edge_records)


def encode_glyph_record_ids(glyph_records: List[Dict[str, Any]]) -> bytes:
    """Flatten the admitted glyph identifiers into a sorted array of 4-byte numbers.

    Parameters:
      glyph_records -- the artifact's `glyph_records` array (111,040 entries).

    WHY ONLY THE IDENTIFIERS: the C++ side does not need each record's full
    shape description. It needs to answer one question fast -- "is this glyph
    identifier part of the admitted universe?" -- which the handshake names
    with the rejection reason `unadmitted_glyph`. A sorted array of 4-byte
    numbers answers that with a binary search in about 17 comparisons and costs
    about 434 KiB, versus tens of megabytes for the full records.

    Sorting is what makes binary search legal, so it is enforced, not assumed.
    """
    identifiers = [int(record["glyph_record_id"]) for record in glyph_records]
    if identifiers != sorted(identifiers):
        raise CompileError("glyph_records are not in ascending glyph_record_id order")
    if len(set(identifiers)) != len(identifiers):
        raise CompileError("glyph_records contain a duplicate glyph_record_id")
    # "<%dI" packs the whole list as little-endian 4-byte unsigned integers.
    return struct.pack(f"<{len(identifiers)}I", *identifiers)


PORT_SIDE_OR_CORNER = {
    "N": 0,
    "NE": 1,
    "E": 2,
    "SE": 3,
    "S": 4,
    "SW": 5,
    "W": 6,
    "NW": 7,
    "interior": 8,
}
PORT_CURVATURE_CLASS = {
    "straight": 0,
    "corner": 1,
    "curved": 2,
}
PORT_TERMINAL_KIND = {
    "endpoint": 0,
    "junction": 1,
    "boundary": 2,
}


def _port_u16(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        raise CompileError(f"{what} is not an unsigned 16-bit integer")
    return value


def _port_u32(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise CompileError(f"{what} is not an unsigned 32-bit integer")
    return value


def _port_u8(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise CompileError(f"{what} is not an unsigned 8-bit integer")
    return value


def _port_s16(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not -0x8000 <= value <= 0x7FFF:
        raise CompileError(f"{what} is not a signed 16-bit integer")
    return value


def _port_side_or_corner(value: Any, what: str) -> int:
    if isinstance(value, str):
        value = PORT_SIDE_OR_CORNER.get(value)
    value = _port_u8(value, what)
    if value > PORT_SIDE_OR_CORNER_MAX:
        raise CompileError(f"{what} is not a recognised boundary side or corner")
    return value


def _port_curvature(value: Any, what: str) -> int:
    if isinstance(value, str):
        value = PORT_CURVATURE_CLASS.get(value)
    value = _port_u8(value, what)
    if value > max(PORT_CURVATURE_CLASS.values()):
        raise CompileError(f"{what} is not a recognised curvature class")
    return value


def _port_terminal_kind(value: Any, what: str) -> int:
    if isinstance(value, str):
        value = PORT_TERMINAL_KIND.get(value)
    value = _port_u8(value, what)
    if value > max(PORT_TERMINAL_KIND.values()):
        raise CompileError(f"{what} is not a recognised terminal kind")
    return value


def _port_count(value: Any, what: str) -> int:
    if isinstance(value, list):
        return _port_u16(len(value), what)
    return _port_u16(value, what)


def encode_glyph_port_graph_tables(
    glyph_records: List[Dict[str, Any]],
    required_record_ids: Optional[set[int]] = None,
    required_junction_counts: Optional[Dict[int, int]] = None,
) -> Tuple[List[Dict[str, int]], bytes, bytes]:
    """Flatten compiler-owned per-glyph stroke graphs into fixed-width tables.

    The source contract is deliberately explicit. A record must carry the
    compiler-owned ``connectivity.stroke_port_graph`` object. This function
    never reconstructs ports from a mask or endpoint counts, and it does not
    invent a skeleton-edge table that the source facts do not own. Component
    labels remain on every port and connectivity row.
    Offsets in the returned graph rows are entry offsets. ``build_sidecar``
    converts them to absolute byte offsets after the section locations are
    known.
    """
    graph_rows: List[Dict[str, int]] = []
    port_records: List[bytes] = []
    connectivity_records: List[bytes] = []

    for glyph_index, glyph_record in enumerate(glyph_records):
        record_id = _port_u32(
            glyph_record.get("glyph_record_id"),
            f"glyph_records[{glyph_index}].glyph_record_id",
        )
        if required_record_ids is not None and record_id not in required_record_ids:
            continue
        connectivity_source = glyph_record.get("connectivity")
        connectivity_graph = (
            connectivity_source.get("stroke_port_graph")
            if isinstance(connectivity_source, dict)
            else None
        )
        if not isinstance(connectivity_graph, dict):
            raise CompileError(
                f"glyph_records[{glyph_index}] is missing connectivity.stroke_port_graph"
            )
        graph = connectivity_graph.get("skeleton_component_graph")
        ports = connectivity_graph.get("boundary_ports")
        connectivity = connectivity_graph.get("port_connectivity")
        if not isinstance(graph, dict) or not isinstance(ports, list) or not isinstance(connectivity, list):
            raise CompileError(
                f"glyph_records[{glyph_index}].stroke_port_graph requires "
                "skeleton_component_graph, boundary_ports, and port_connectivity"
            )
        if graph.get("graph_schema", connectivity_graph.get("graph_schema")) not in (None, "u7.stroke_port_graph.v1"):
            raise CompileError(f"glyph_records[{glyph_index}] declares an unknown stroke-port graph schema")
        if connectivity_graph.get("graph_valid") is not True or connectivity_graph.get("graph_rejection_reason") is not None:
            raise CompileError(
                f"glyph_records[{glyph_index}] carries a rejected stroke-port graph: "
                f"{connectivity_graph.get('graph_rejection_reason')}"
            )
        component_count = _port_u16(
            graph.get("components"),
            f"glyph_records[{glyph_index}].connectivity.stroke_port_graph.skeleton_component_graph.components",
        )
        if component_count > RUNTIME_PORT_GRAPH_MAX_COMPONENTS:
            raise CompileError(
                f"glyph_records[{glyph_index}] has {component_count} components; "
                f"runtime graph ABI allows at most {RUNTIME_PORT_GRAPH_MAX_COMPONENTS}"
            )
        if component_count == 0 and ports:
            raise CompileError(f"glyph_records[{glyph_index}] has ports but no skeleton components")
        boundary_port_count = _port_u16(
            connectivity_graph.get("boundary_port_count"),
            f"glyph_records[{glyph_index}].connectivity.stroke_port_graph.boundary_port_count",
        )
        port_connectivity_count = _port_u16(
            connectivity_graph.get("port_connectivity_count"),
            f"glyph_records[{glyph_index}].connectivity.stroke_port_graph.port_connectivity_count",
        )
        if boundary_port_count != len(ports):
            raise CompileError(f"glyph_records[{glyph_index}] boundary_port_count disagrees with boundary_ports")
        if port_connectivity_count != len(connectivity):
            raise CompileError(f"glyph_records[{glyph_index}] port_connectivity_count disagrees with port_connectivity")
        if len(ports) > RUNTIME_PORT_GRAPH_MAX_PORTS:
            raise CompileError(
                f"glyph_records[{glyph_index}] has {len(ports)} boundary ports; "
                f"runtime graph ABI allows at most {RUNTIME_PORT_GRAPH_MAX_PORTS}"
            )
        if len(connectivity) > RUNTIME_PORT_GRAPH_MAX_CONNECTIVITY:
            raise CompileError(
                f"glyph_records[{glyph_index}] has {len(connectivity)} connectivity rows; "
                "runtime graph ABI allows at most "
                f"{RUNTIME_PORT_GRAPH_MAX_CONNECTIVITY}"
            )

        parsed_ports: List[Dict[str, int]] = []
        source_port_ids: set[int] = set()
        for port_index, port in enumerate(ports):
            if not isinstance(port, dict):
                raise CompileError(f"glyph_records[{glyph_index}].boundary_ports[{port_index}] is not an object")
            component_id = _port_u16(port.get("component_id"), f"glyph_records[{glyph_index}].boundary_ports[{port_index}].component_id")
            if component_id >= component_count:
                raise CompileError(f"glyph_records[{glyph_index}].boundary_ports[{port_index}] names unknown component {component_id}")
            source_port_id = _port_u8(port.get("port_id"), f"glyph_records[{glyph_index}].boundary_ports[{port_index}].port_id")
            if source_port_id in source_port_ids:
                raise CompileError(f"glyph_records[{glyph_index}] duplicates port_id {source_port_id}")
            source_port_ids.add(source_port_id)
            tangent = port.get("tangent_q1_15", port.get("tangent"))
            if not isinstance(tangent, list) or len(tangent) != 2:
                raise CompileError(f"glyph_records[{glyph_index}].boundary_ports[{port_index}].tangent_q1_15 is missing")
            tangent_x = _port_s16(tangent[0], f"glyph_records[{glyph_index}].boundary_ports[{port_index}].tangent_q1_15[0]")
            tangent_y = _port_s16(tangent[1], f"glyph_records[{glyph_index}].boundary_ports[{port_index}].tangent_q1_15[1]")
            position = port.get("position_xy")
            endpoint = port.get("endpoint_xy")
            if not isinstance(position, list) or len(position) != 2 or not all(isinstance(value, int) and 0 <= value <= 7 for value in position):
                raise CompileError(f"glyph_records[{glyph_index}].boundary_ports[{port_index}].position_xy is invalid")
            if not isinstance(endpoint, list) or len(endpoint) != 2 or not all(isinstance(value, int) and 0 <= value <= 7 for value in endpoint):
                raise CompileError(f"glyph_records[{glyph_index}].boundary_ports[{port_index}].endpoint_xy is invalid")
            virtual_boundary = port.get("virtual_boundary")
            if not isinstance(virtual_boundary, bool):
                raise CompileError(f"glyph_records[{glyph_index}].boundary_ports[{port_index}].virtual_boundary is invalid")
            boundary_t_value = port.get("boundary_t_q0_16")
            boundary_t_is_null = boundary_t_value is None
            boundary_t = 0 if boundary_t_is_null else _port_u16(
                boundary_t_value,
                f"glyph_records[{glyph_index}].boundary_ports[{port_index}].boundary_t_q0_16",
            )
            parsed_ports.append(
                {
                    "component_id": component_id,
                    "source_port_id": source_port_id,
                    "side_or_corner": _port_side_or_corner(port.get("side_or_corner"), f"glyph_records[{glyph_index}].boundary_ports[{port_index}].side_or_corner"),
                    "degree": _port_u8(port.get("endpoint_or_junction_degree"), f"glyph_records[{glyph_index}].boundary_ports[{port_index}].endpoint_or_junction_degree"),
                    "boundary_t": boundary_t,
                    "tangent_x": tangent_x,
                    "tangent_y": tangent_y,
                    "endpoint_x": endpoint[0],
                    "endpoint_y": endpoint[1],
                    "boundary_x": position[0],
                    "boundary_y": position[1],
                    "virtual_boundary": 1 if virtual_boundary else 0,
                    "terminal_kind": _port_terminal_kind(port.get("terminal_kind"), f"glyph_records[{glyph_index}].boundary_ports[{port_index}].terminal_kind"),
                }
            )

        if source_port_ids != set(range(len(parsed_ports))):
            raise CompileError(f"glyph_records[{glyph_index}] port_id values must be contiguous from zero")
        port_index_by_source: Dict[int, int] = {port_id: port_id for port_id in source_port_ids}
        graph_port_offset = len(port_records)
        graph_connectivity_offset = len(connectivity_records)
        for port in parsed_ports:
            port_records.append(
                struct.pack(
                    "<HBBHhhHBBBBBBH",
                    port["component_id"],
                    port["side_or_corner"],
                    port["degree"],
                    port["boundary_t"],
                    port["tangent_x"],
                    port["tangent_y"],
                    port["source_port_id"],
                    port["endpoint_x"],
                    port["endpoint_y"],
                    port["boundary_x"],
                    port["boundary_y"],
                    port["virtual_boundary"],
                    port["terminal_kind"],
                    0,
                )
            )

        for connection_index, connection in enumerate(connectivity):
            if not isinstance(connection, dict):
                raise CompileError(f"glyph_records[{glyph_index}].port_connectivity[{connection_index}] is not an object")
            component_id = _port_u16(connection.get("component_id"), f"glyph_records[{glyph_index}].port_connectivity[{connection_index}].component_id")
            if component_id >= component_count:
                raise CompileError(f"glyph_records[{glyph_index}].port_connectivity[{connection_index}] names unknown component {component_id}")
        parsed_connectivity = []
        for connection_index, connection in enumerate(connectivity):
            assert isinstance(connection, dict)
            component_id = int(connection["component_id"])
            if connection.get("connected") is not True:
                raise CompileError(f"glyph_records[{glyph_index}].port_connectivity[{connection_index}] is not marked connected")
            port_a_source = _port_u8(connection.get("port_a"), f"glyph_records[{glyph_index}].port_connectivity[{connection_index}].port_a")
            port_b_source = _port_u8(connection.get("port_b"), f"glyph_records[{glyph_index}].port_connectivity[{connection_index}].port_b")
            if port_a_source == port_b_source or port_a_source not in port_index_by_source or port_b_source not in port_index_by_source:
                raise CompileError(f"glyph_records[{glyph_index}].port_connectivity[{connection_index}] references unknown or identical ports")
            if (
                parsed_ports[port_index_by_source[port_a_source]]["component_id"] != component_id
                or parsed_ports[port_index_by_source[port_b_source]]["component_id"] != component_id
            ):
                raise CompileError(f"glyph_records[{glyph_index}].port_connectivity[{connection_index}] crosses component labels")
            port_a = port_index_by_source[port_a_source]
            port_b = port_index_by_source[port_b_source]
            parsed_connectivity.append(
                {
                    "component_id": component_id,
                    "port_a": port_a,
                    "port_b": port_b,
                    "arc_length": _port_u16(connection.get("arc_length_q8_8"), f"glyph_records[{glyph_index}].port_connectivity[{connection_index}].arc_length_q8_8"),
                    "turn_count": _port_u8(connection.get("turn_count"), f"glyph_records[{glyph_index}].port_connectivity[{connection_index}].turn_count"),
                    "curvature_class": _port_curvature(connection.get("curvature_class"), f"glyph_records[{glyph_index}].port_connectivity[{connection_index}].curvature_class"),
                }
            )
        for connection in parsed_connectivity:
            connectivity_records.append(
                struct.pack(
                    "<HHHHBBHI",
                    connection["component_id"],
                    connection["port_a"],
                    connection["port_b"],
                    connection["arc_length"],
                    connection["curvature_class"],
                    connection["turn_count"],
                    0,
                    0,
                )
            )

        graph_holes = _port_count(graph.get("holes"), f"glyph_records[{glyph_index}].skeleton_component_graph.holes")
        graph_junctions = _port_count(graph.get("junctions"), f"glyph_records[{glyph_index}].skeleton_component_graph.junctions")
        graph_skeleton_edges = _port_count(
            graph.get("skeleton_edges"),
            f"glyph_records[{glyph_index}].skeleton_component_graph.skeleton_edges",
        )
        if graph_holes > RUNTIME_PORT_GRAPH_MAX_COMPONENTS or graph_junctions > RUNTIME_PORT_GRAPH_MAX_COMPONENTS:
            raise CompileError(
                f"glyph_records[{glyph_index}] graph summary exceeds the "
                f"{RUNTIME_PORT_GRAPH_MAX_COMPONENTS}-entry runtime bound"
            )
        if graph_skeleton_edges > RUNTIME_PORT_GRAPH_MAX_SKELETON_EDGES:
            raise CompileError(
                f"glyph_records[{glyph_index}] has {graph_skeleton_edges} skeleton edges; "
                f"runtime graph ABI allows at most {RUNTIME_PORT_GRAPH_MAX_SKELETON_EDGES}"
            )
        if required_junction_counts is not None:
            expected_junctions = required_junction_counts.get(record_id)
            if expected_junctions is None:
                raise CompileError(
                    f"FL-4783 structural graph has no U7 member descriptor for glyph_record_id {record_id}"
                )
            if graph_junctions != expected_junctions:
                raise CompileError(
                    f"glyph_records[{glyph_index}] graph junctions {graph_junctions} "
                    f"disagree with U7 member descriptor {expected_junctions}"
                )
        graph_rows.append(
            {
                "glyph_record_id": int(glyph_record["glyph_record_id"]),
                "component_count": component_count,
                "boundary_port_offset": graph_port_offset,
                "boundary_port_count": len(parsed_ports),
                "connectivity_offset": graph_connectivity_offset,
                "connectivity_count": len(parsed_connectivity),
                "hole_count": graph_holes,
                "junction_count": graph_junctions,
                "skeleton_edge_count": graph_skeleton_edges,
            }
        )

    if len(port_records) * BOUNDARY_PORT_RECORD_SIZE != len(b"".join(port_records)):
        raise CompileError("FL-4783 boundary-port record stride mismatch")
    if len(connectivity_records) * PORT_CONNECTIVITY_RECORD_SIZE != len(b"".join(connectivity_records)):
        raise CompileError("FL-4783 connectivity record stride mismatch")
    if required_record_ids is not None:
        encoded_ids = {row["glyph_record_id"] for row in graph_rows}
        missing = sorted(required_record_ids - encoded_ids)
        if missing:
            raise CompileError(f"FL-4783 structural graph missing glyph_record_ids: {missing[:8]}")
    return (
        graph_rows,
        b"".join(port_records),
        b"".join(connectivity_records),
    )


def encode_gtr_v2_relations(gtr_v2: Dict[str, Any]) -> bytes:
    """Encode the exhaustive FL-4694 aspect-conditioned relation projection.

    The old bounded relation cache retained only 32 neighbours per source and
    carried no aspect coordinate. It therefore addressed only 384 of GTR-v2's
    65,536 rows. This section does not approximate that missing truth: it
    projects every committed row into one fixed 32-byte record.

    Record layout, little-endian:
      from_glyph_id u32, to_glyph_id u32,
      compiled_transform_cost u16, relation_confidence_q0_16 u16,
      aspect u8, static_components[11] u8, affine_components[7] u8,
      reserved u8 = 0.

    Canonical source order is part of the contract. For each aspect, all 32
    sources appear in ascending `glyph_ids` order, and each source carries all
    32 targets in that same order. Keeping that order preserves directed rows
    and GTR-v2's stored tie order without inventing a second tie rule.
    """
    if gtr_v2.get("schema") != "fl4512.glyph_transform_relation.v2":
        raise CompileError("GTR-v2 source declares the wrong schema")
    if int(gtr_v2.get("version", 0)) != 2:
        raise CompileError("GTR-v2 source declares the wrong version")

    glyph_ids = [int(value) for value in gtr_v2.get("glyph_ids", [])]
    relations = gtr_v2.get("relations", [])
    if len(glyph_ids) != GTR_V2_GLYPH_COUNT:
        raise CompileError(
            f"GTR-v2 holds {len(glyph_ids)} glyph ids, expected {GTR_V2_GLYPH_COUNT}"
        )
    if glyph_ids != sorted(glyph_ids) or len(set(glyph_ids)) != len(glyph_ids):
        raise CompileError("GTR-v2 glyph_ids must be unique and ascending")
    declared_counts = (
        int(gtr_v2.get("aspect_count", 0)),
        int(gtr_v2.get("relations_per_aspect", 0)),
        int(gtr_v2.get("relation_count", 0)),
    )
    expected_counts = (
        GTR_V2_ASPECT_COUNT,
        GTR_V2_RELATIONS_PER_ASPECT,
        GTR_V2_RELATION_COUNT,
    )
    if declared_counts != expected_counts or len(relations) != GTR_V2_RELATION_COUNT:
        raise CompileError(
            f"GTR-v2 cardinality {declared_counts} with {len(relations)} rows, "
            f"expected {expected_counts} with {GTR_V2_RELATION_COUNT} rows"
        )

    records: List[bytes] = []
    for row_index, row in enumerate(relations):
        within_aspect = row_index % GTR_V2_RELATIONS_PER_ASPECT
        expected_aspect = row_index // GTR_V2_RELATIONS_PER_ASPECT
        expected_from = glyph_ids[within_aspect // GTR_V2_GLYPH_COUNT]
        expected_to = glyph_ids[within_aspect % GTR_V2_GLYPH_COUNT]
        if (
            int(row["aspect"]) != expected_aspect
            or int(row["from_glyph_id"]) != expected_from
            or int(row["to_glyph_id"]) != expected_to
        ):
            raise CompileError(
                f"GTR-v2 row {row_index} is not at canonical "
                f"(aspect={expected_aspect}, from={expected_from}, to={expected_to})"
            )

        static_components = [int(value) for value in row["static_components"]]
        affine_components = [int(value) for value in row["affine_components"]]
        if len(static_components) != GTR_V2_STATIC_COMPONENT_COUNT:
            raise CompileError(f"GTR-v2 row {row_index} has wrong static width")
        if len(affine_components) != GTR_V2_AFFINE_COMPONENT_COUNT:
            raise CompileError(f"GTR-v2 row {row_index} has wrong affine width")
        if any(not 0 <= value <= 0xFF for value in static_components):
            raise CompileError(f"GTR-v2 row {row_index} static component out of range")
        if any(not 0 <= value <= 0xFF for value in affine_components):
            raise CompileError(f"GTR-v2 row {row_index} affine component out of range")
        confidence = int(row["relation_confidence_q0_16"])
        cost = int(row["compiled_transform_cost"])
        if not 0 <= confidence <= 0xFFFF or not 0 <= cost <= 0xFFFF:
            raise CompileError(f"GTR-v2 row {row_index} scalar out of 16-bit range")

        records.append(
            struct.pack(
                "<IIHHB11B7Bx",
                expected_from,
                expected_to,
                cost,
                confidence,
                expected_aspect,
                *static_components,
                *affine_components,
            )
        )

    blob = b"".join(records)
    if len(blob) != GTR_V2_RELATION_COUNT * GTR_V2_RELATION_RECORD_SIZE:
        raise CompileError("GTR-v2 relation stride does not match its fixed contract")
    return blob


def encode_pin_table(pins: List[Dict[str, Any]]) -> bytes:
    """Flatten the pin list into fixed-size 224-byte records."""
    records: List[bytes] = []
    for pin in pins:
        records.append(
            # "<II Q 32s 176s": role(4) flags(4) byte_size(8) sha256(32) path(176)
            struct.pack(
                "<IIQ32s176s",
                pin["role"],
                pin["flags"],
                pin["byte_size"],
                hex_to_32_bytes(pin["sha256"], f"pin {pin['path']} sha256"),
                encode_path_field(pin["path"]),
            )
        )
    blob = b"".join(records)
    if len(blob) != len(pins) * PIN_RECORD_SIZE:
        raise CompileError("pin record stride does not match PIN_RECORD_SIZE")
    return blob


# -----------------------------------------------------------------------------
# Sidecar assembly
# -----------------------------------------------------------------------------
def build_sidecar(
    artifact: Dict[str, Any],
    pins: List[Dict[str, Any]],
    pool: Dict[str, Any],
    gtr_v2: Dict[str, Any],
) -> bytes:
    """Assemble the complete binary sidecar as one byte string.

    Parameters:
      artifact -- the parsed 93 MiB compiled artifact.
      pins     -- the ordered pin list from `collect_pins`.

    FILE SHAPE (all numbers little-endian, which is what every platform this
    project targets uses natively, so C++ can read them without byte swapping):

        bytes    0 ..  319   header (fixed size, see below)
        bytes  320 ..        fact table            (fact_count * 128)
                             fact member table     (member_count * 20)
                             relation edge table   (edge_count * 24)
                             glyph record ids      (glyph_record_count * 4)
                             pin table             (pin_count * 224)
                             scene address index   (scene_address_count * 12)
                             GTR-v2 relations      (65,536 * 32)
                             glyph port graph      (glyph_graph_count * 32)
                             boundary ports        (port_count * 20)
                             port connectivity     (connectivity_count * 16)

    The header stores the byte offset and entry count of every section, so a
    reader never has to guess where a section starts.
    """
    identity = artifact["artifact_identity"]

    # --- Identity fingerprints that end up inside GlyphProgramV1 --------------
    # `artifact_file_sha256` fingerprints the bytes of the JSON file itself.
    # It is taken from the pin we already measured, so the two can never
    # disagree with each other.
    artifact_file_sha = next(
        pin["sha256"] for pin in pins if pin["role"] == PIN_ROLE_GLYPH_FACT_ARTIFACT
    )
    # `canonical_content_sha256` fingerprints the artifact's CONTENT in a
    # normalised form, so it survives harmless re-serialisation (key ordering,
    # whitespace) that would change the file fingerprint.
    artifact_content_sha = artifact["canonical_content_sha256"]
    # The glyph universe is the set of characters the compiler was allowed to
    # draw from. Its fingerprint lets the runtime detect "the renderer's atlas
    # is not the atlas this program was compiled against".
    glyph_universe_sha = identity["glyph_universe_declared_sha256"]
    # The handshake schema fingerprint answers "does the renderer still describe
    # a scene fact the same way the compiler expects?".
    scene_fact_schema_sha = next(
        pin["sha256"]
        for pin in pins
        if pin["role"] == PIN_ROLE_SCENE_FACT_HANDSHAKE_SCHEMA
    )

    # --- Counts and caps ------------------------------------------------------
    glyph_record_count = int(artifact["glyph_record_count"])
    fact_count = int(artifact["fact_count"])
    if glyph_record_count != len(artifact["glyph_records"]):
        raise CompileError("glyph_record_count disagrees with the number of glyph_records")
    if fact_count != len(artifact["facts"]):
        raise CompileError("fact_count disagrees with the number of facts")

    # `max_relation_edges` is the compiler's declared ceiling on how many
    # relation edges a single fact may carry.
    max_relation_edges = int(artifact["relation_representation"]["max_edges_per_fact"])
    # `max_fact_members` is the declared ceiling on legal glyphs per fact. Every
    # fact records its own `capacity`; the ceiling is the largest of those.
    max_fact_members = max(
        int(fact["scene_membership_key"]["capacity"]) for fact in artifact["facts"]
    )
    if not 0 < max_fact_members <= 0xFFFF:
        raise CompileError(f"max_fact_members {max_fact_members} out of 16-bit range")
    if not 0 < max_relation_edges <= 0xFFFF:
        raise CompileError(f"max_relation_edges {max_relation_edges} out of 16-bit range")

    # --- Runtime rights -------------------------------------------------------
    rights = artifact["runtime_rights"]
    # Hard stop: this whole sidecar exists on the understanding that the live
    # game may not consume it. If the source artifact ever flips that flag, the
    # decision must be made deliberately, not inherited by a rebuild.
    if rights["runtime_consumption_allowed"] is not False:
        raise CompileError(
            "source artifact now declares runtime_consumption_allowed true; "
            "activation is a separate decision and this compiler refuses to infer it"
        )
    declared_future_rights = 0
    for right_name in rights.get("future_rights_after_handshake", []):
        bit = FUTURE_RIGHT_BITS.get(right_name)
        if bit is None:
            raise CompileError(f"unknown future right in artifact: {right_name}")
        declared_future_rights |= bit

    # --- Build every section --------------------------------------------------
    fact_bytes, member_bytes, edge_bytes, member_count, edge_count = encode_fact_table(
        artifact["facts"], artifact["glyph_records"], artifact["profiles"], pool
    )
    glyph_id_bytes = encode_glyph_record_ids(artifact["glyph_records"])
    pin_bytes = encode_pin_table(pins)
    # Format version 2: the sorted lookup table that turns a renderer's cell
    # description into a fact index. See `encode_scene_address_index`.
    scene_address_bytes, scene_address_count, declared_absent_count = (
        encode_scene_address_index(artifact["facts"], pool)
    )
    # Format version 4, FL-4694 E8: exhaustive aspect-conditioned relation
    # truth. This is a committed section, unlike the bounded local cache.
    gtr_v2_relation_bytes = encode_gtr_v2_relations(gtr_v2)
    # Format version 7, FL-4783: compiler-owned per-GlyphId structural graph.
    # Missing graph fields are a hard compile failure. The runtime must never
    # reconstruct ports from mask8 or from endpoint/junction counts.
    structural_record_ids: set[int] = set()
    structural_junction_counts: Dict[int, int] = {}
    glyph_record_by_id = {
        int(record["glyph_record_id"]): record for record in artifact["glyph_records"]
    }
    for fact_index, fact in enumerate(artifact["facts"]):
        if fact["scene_membership_key"].get("scorer_id") != "u7.mask.v1":
            continue
        members = fact.get("glyph_members")
        if not isinstance(members, list):
            raise CompileError(f"facts[{fact_index}] structural glyph_members are missing")
        for member in members:
            record_id = member.get("glyph_record_id")
            record_id = _port_u32(record_id, f"facts[{fact_index}].glyph_members.glyph_record_id")
            structural_record_ids.add(record_id)
            glyph_record = glyph_record_by_id.get(record_id)
            structure = glyph_record.get("structure") if glyph_record is not None else None
            if not isinstance(structure, dict):
                raise CompileError(
                    f"facts[{fact_index}] U7 member {record_id} has no structure descriptor"
                )
            junction_count = require_u8(
                structure.get("junction_count"),
                f"facts[{fact_index}] U7 member {record_id} junction_count",
            )
            prior_junction_count = structural_junction_counts.get(record_id)
            if prior_junction_count is not None and prior_junction_count != junction_count:
                raise CompileError(
                    f"U7 member {record_id} has inconsistent junction_count descriptors"
                )
            structural_junction_counts[record_id] = junction_count
    if not structural_record_ids:
        raise CompileError("FL-4783 structural graph source has no u7.mask.v1 members")
    (
        port_graph_rows,
        boundary_port_bytes,
        port_connectivity_bytes,
    ) = encode_glyph_port_graph_tables(
        artifact["glyph_records"], structural_record_ids, structural_junction_counts
    )
    # FL-4686 PA cross-check, stated as an equation rather than trusted: every
    # index entry is exactly one of the two dispositions, and the populated ones
    # are exactly the facts. If this ever fails, the index and the fact table
    # disagree about what the program contains.
    if scene_address_count - declared_absent_count != fact_count:
        raise CompileError(
            f"index holds {scene_address_count} addresses of which "
            f"{declared_absent_count} are declared absent, leaving "
            f"{scene_address_count - declared_absent_count} populated, but the "
            f"fact table holds {fact_count}"
        )

    # --- Compute where each section lands ------------------------------------
    # Sections are laid out in fixed order starting right after the header,
    # each at the alignment the C++ loader enforces (fail-closed
    # malformed_sidecar otherwise): fact/member/edge/pin tables hold fields
    # wider than 4 bytes and require 8-byte offsets; the remaining tables
    # require 4-byte offsets. Gaps are zero-filled and covered by the payload
    # fingerprint, so padding bytes are authenticated, not free space.
    def _align_up(offset: int, alignment: int) -> int:
        return (offset + alignment - 1) // alignment * alignment

    fact_offset = _align_up(SIDECAR_HEADER_SIZE, 8)
    member_offset = _align_up(fact_offset + len(fact_bytes), 8)
    pad_after_member = member_offset - (fact_offset + len(fact_bytes))
    edge_offset = _align_up(member_offset + len(member_bytes), 8)
    pad_after_edge = edge_offset - (member_offset + len(member_bytes))
    glyph_id_offset = _align_up(edge_offset + len(edge_bytes), 4)
    pad_after_glyph_id = glyph_id_offset - (edge_offset + len(edge_bytes))
    pin_offset = _align_up(glyph_id_offset + len(glyph_id_bytes), 8)
    pad_after_pin = pin_offset - (glyph_id_offset + len(glyph_id_bytes))
    scene_address_offset = _align_up(pin_offset + len(pin_bytes), 4)
    pad_after_scene = scene_address_offset - (pin_offset + len(pin_bytes))
    gtr_v2_relation_offset = _align_up(scene_address_offset + len(scene_address_bytes), 4)
    pad_after_gtr = gtr_v2_relation_offset - (scene_address_offset + len(scene_address_bytes))
    port_graph_offset = _align_up(gtr_v2_relation_offset + len(gtr_v2_relation_bytes), 4)
    pad_after_port_graph = port_graph_offset - (gtr_v2_relation_offset + len(gtr_v2_relation_bytes))
    boundary_port_offset = _align_up(port_graph_offset + len(port_graph_rows) * PORT_GRAPH_RECORD_SIZE, 4)
    pad_after_boundary = boundary_port_offset - (port_graph_offset + len(port_graph_rows) * PORT_GRAPH_RECORD_SIZE)
    port_connectivity_offset = _align_up(boundary_port_offset + len(boundary_port_bytes), 4)
    # Header size is a multiple of 8, so the fact table is always aligned.
    if fact_offset != SIDECAR_HEADER_SIZE:
        raise CompileError("header size broke fact table alignment")

    # Convert child entry offsets into absolute byte offsets once section starts
    # are known. A fixed graph row is the only public join from GlyphId to all of
    # its immutable child records.
    port_graph_bytes = b"".join(
        struct.pack(
            "<IIH2xIH2xHHHHI",
            row["glyph_record_id"],
            boundary_port_offset + row["boundary_port_offset"] * BOUNDARY_PORT_RECORD_SIZE,
            row["boundary_port_count"],
            port_connectivity_offset + row["connectivity_offset"] * PORT_CONNECTIVITY_RECORD_SIZE,
            row["connectivity_count"],
            row["component_count"],
            row["hole_count"],
            row["junction_count"],
            row["skeleton_edge_count"],
            0,
        )
        for row in port_graph_rows
    )
    if len(port_graph_bytes) != len(port_graph_rows) * PORT_GRAPH_RECORD_SIZE:
        raise CompileError("FL-4783 glyph graph record stride mismatch")

    payload = (
        fact_bytes
        + b"\x00" * pad_after_member
        + member_bytes
        + b"\x00" * pad_after_edge
        + edge_bytes
        + b"\x00" * pad_after_glyph_id
        + glyph_id_bytes
        + b"\x00" * pad_after_pin
        + pin_bytes
        + b"\x00" * pad_after_scene
        + scene_address_bytes
        + b"\x00" * pad_after_gtr
        + gtr_v2_relation_bytes
        + b"\x00" * pad_after_port_graph
        + port_graph_bytes
        + b"\x00" * pad_after_boundary
        + boundary_port_bytes
        + port_connectivity_bytes
    )
    # A fingerprint over everything after the header. A reader confirms this
    # before touching any section, so a truncated or edited body is caught even
    # if the outer file fingerprint were somehow not checked.
    payload_digest = hashlib.sha256(payload).digest()

    # --- Pack the header ------------------------------------------------------
    # Field order here must match, byte for byte, the struct in
    # platform/fl4683_glyph_program_loader.h.
    header = struct.pack(
        "<8sII"          # magic(8) format_version(4) header_size(4)         = 16
        "32s32s32s32s"   # four identity fingerprints                        = 128 -> 144
        "II"             # glyph_record_count(4) fact_count(4)               -> 152
        "I"              # declared_future_rights(4)                         -> 156
        "HH"             # max_fact_members(2) max_relation_edges(2)         -> 160
        "B3x"            # runtime_consumption_allowed(1) + 3 padding bytes  -> 164
        "I"              # fact_table_offset                                 -> 168
        "II"             # fact_member_offset, fact_member_count             -> 176
        "II"             # relation_edge_offset, relation_edge_count         -> 184
        "I"              # glyph_record_id_offset                            -> 188
        "II"             # pin_table_offset, pin_count                       -> 196
        "I"              # payload_size                                      -> 200
        "32s"            # payload_sha256                                    -> 232
        "II"             # scene_address_offset, scene_address_count         -> 240
        "I"              # scene_address_declared_absent_count               -> 244
        "III"            # GTR-v2 offset, count, record size                  -> 256
        "9I"             # FL-4783 graph/port section directory               -> 292
        "28x",           # fixed header padding                               -> 320
        SIDECAR_MAGIC,
        SIDECAR_FORMAT_VERSION,
        SIDECAR_HEADER_SIZE,
        hex_to_32_bytes(artifact_file_sha, "artifact_file_sha256"),
        hex_to_32_bytes(artifact_content_sha, "canonical_content_sha256"),
        hex_to_32_bytes(glyph_universe_sha, "glyph_universe_declared_sha256"),
        hex_to_32_bytes(scene_fact_schema_sha, "scene_fact_schema_sha256"),
        glyph_record_count,
        fact_count,
        declared_future_rights,
        max_fact_members,
        max_relation_edges,
        0,  # runtime_consumption_allowed: pinned OFF, see module header
        fact_offset,
        member_offset,
        member_count,
        edge_offset,
        edge_count,
        glyph_id_offset,
        pin_offset,
        len(pins),
        len(payload),
        payload_digest,
        scene_address_offset,
        scene_address_count,
        declared_absent_count,
        gtr_v2_relation_offset,
        GTR_V2_RELATION_COUNT,
        GTR_V2_RELATION_RECORD_SIZE,
        port_graph_offset,
        len(port_graph_rows),
        PORT_GRAPH_RECORD_SIZE,
        boundary_port_offset,
        len(boundary_port_bytes) // BOUNDARY_PORT_RECORD_SIZE,
        BOUNDARY_PORT_RECORD_SIZE,
        port_connectivity_offset,
        len(port_connectivity_bytes) // PORT_CONNECTIVITY_RECORD_SIZE,
        PORT_CONNECTIVITY_RECORD_SIZE,
    )
    if len(header) != SIDECAR_HEADER_SIZE:
        raise CompileError(
            f"header packed to {len(header)} bytes, expected {SIDECAR_HEADER_SIZE}"
        )

    return header + payload


def _pool_address_total(pool: Dict[str, Any]) -> int:
    """How many addresses the enumerated space holds. FL-4686 PA receipt field."""
    return len(pool["rows"])


def _pool_declared_absent_total(pool: Dict[str, Any]) -> int:
    """How many of those addresses hold no glyph. FL-4686 PA receipt field."""
    return sum(
        1
        for row in pool["rows"]
        if str(row["bucket_state"]) == POOL_STATE_DECLARED_ABSENT
    )


def _primitive_fact_policy_rows(
    artifact: Dict[str, Any], pool: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Human-readable projection of the S_pf fact-policy tail fields."""
    pool_materials = build_pool_material_index(pool)
    rows: List[Dict[str, Any]] = []
    for fact_index, fact in enumerate(artifact["facts"]):
        membership_key = fact["scene_membership_key"]
        if membership_key["scorer_id"] != "S_pf.v1":
            continue
        region, priority, extent_enter = fact_primitive_policy(
            SCORER_KIND_S_PF, membership_key, pool_materials, fact_index
        )
        rows.append(
            {
                "source_profile_id": fact["source_profile_id"],
                "primitive_target_region_class": region,
                "primitive_target_priority_u16": priority,
                "primitive_extent_enter_q": extent_enter,
            }
        )
    return rows


def _shape6_no_match_policy_summary(
    artifact: Dict[str, Any], pool: Dict[str, Any]
) -> Dict[str, Any]:
    """Human-readable projection of the format-6 fact policy emit. FL-4512 P2.

    Recomputed with the same join the binary emit uses, so a reader of the
    receipt alone can see how many rows were flagged and how each bound was
    scoped without unpacking the fact table.
    """
    pool_materials = build_pool_material_index(pool)
    flagged = 0
    structural_bound_rows = 0
    tonal_bound_rows = 0
    for fact_index, fact in enumerate(artifact["facts"]):
        membership_key = fact["scene_membership_key"]
        if membership_key["scorer_id"] != EXACT_HARRI_SCORER_ID:
            continue
        flags, _bound = fact_shape6_no_match_policy(
            SCORER_KIND_SHAPE6_WEIGHTED_L2, membership_key, pool_materials, fact_index
        )
        if flags & FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN:
            flagged += 1
        if str(membership_key["route_key"]) in STRUCTURAL_SHAPE6_ROUTE_KEYS:
            structural_bound_rows += 1
        else:
            tonal_bound_rows += 1
    return {
        "policy_source": (
            "fl4512.compiled_pool.v1 materials[].no_match_policy joined by "
            "profile_namespace/material_key"
        ),
        "degenerate_operand_abstain_flag": FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN,
        "flagged_fact_count": flagged,
        "structural_route_keys": sorted(STRUCTURAL_SHAPE6_ROUTE_KEYS),
        "tonal_route_keys": sorted(TONAL_SHAPE6_ROUTE_KEYS),
        "structural_bound_fact_count": structural_bound_rows,
        "tonal_bound_fact_count": tonal_bound_rows,
        "tonal_rows_never_flagged": True,
        "bound_units": (
            "fl4512.shape6_f8_w4.v2 weighted-L2 fixed point; u7.mask.v1 rows "
            "stay unbounded (0) because the mask scorer's units differ and no "
            "policy authored in those units exists"
        ),
        "threshold_u32_scoping": {
            "S_pf.v1": "projected-extent enter gate (unchanged from v5)",
            "fl4512.shape6_f8_w4.v2": "compiled no-match bound, 0 = unbounded",
            "u7.mask.v1": "always 0 (unbounded), see bound_units",
            "fl4763.structural_decline.v1": (
                "always 0; the row declines before any bound could apply"
            ),
        },
    }


def validate_pool_profile_pin(
    pool: Dict[str, Any], profile_path: Path = MATERIAL_RENDERING_PROFILES
) -> None:
    """Require the compiled pool to still match its AUTHORED profile source.

    FL-4768. `validate_artifact_source_identity` proves the glyph-fact, the
    pool, and the mesh-admission artifact are one compilation with each other.
    That is not sufficient. All three can agree perfectly and still encode a
    material policy the authored profile no longer authorizes, because the
    policy originates one step further upstream, in
    MATERIAL_RENDERING_PROFILES, and only the pool header records which
    revision of it the pool was built from.

    That is the exact state commit 61a11750f published: it corrected the
    authored bounds AND republished this program from an un-regenerated pool in
    one commit, so every downstream identity check agreed and `--check` stayed
    green while 83 of 97 shape6 fact rows shipped a bound the profile does not
    authorize.

    This check is separate from the identity join on purpose: it validates a
    different relationship (pool -> authored source, not artifact <-> artifact),
    so a failure here names the real owner instead of being reported as a
    glyph-fact join problem.

    The reason token is deliberately identical to the one
    scripts/compile_fl4676_glyph_facts.py already raises for this same drift,
    so both gates on this one relationship stay greppable as a single class.
    """
    pool_profile_hash = pool.get("header", {}).get("profile_hash")
    try:
        authored_profile_sha = sha256_file(profile_path)
    except OSError as exc:
        raise CompileError(
            "source_pin_mismatch:compiled_pool:material_profiles:"
            f"{path_identity(profile_path)}:unreadable"
        ) from exc
    if pool_profile_hash != authored_profile_sha:
        raise CompileError(
            "source_pin_mismatch:compiled_pool:material_profiles:"
            f"pool_header={pool_profile_hash}!=authored={authored_profile_sha}"
        )


def validate_artifact_source_identity(
    artifact: Dict[str, Any], pool: Dict[str, Any], compiled_pool_path: Path
) -> None:
    """Require the GlyphFact and pool inputs to be the exact same compilation."""
    raw_pins = artifact.get("source_pins")
    if not isinstance(raw_pins, list):
        raise CompileError("source_join_mismatch:glyph_fact:source_pins_missing")
    pins: Dict[str, Dict[str, Any]] = {}
    for pin in raw_pins:
        if not isinstance(pin, dict) or not isinstance(pin.get("name"), str):
            raise CompileError("source_join_mismatch:glyph_fact:malformed_source_pin")
        name = str(pin["name"])
        if name in pins:
            raise CompileError(f"source_join_mismatch:glyph_fact:duplicate_source_pin:{name}")
        pins[name] = pin
    pool_pin = pins.get("compiled_pool_v1")
    if pool_pin is None:
        raise CompileError("source_join_mismatch:glyph_fact:compiled_pool_v1_pin_missing")
    expected_path = path_identity(compiled_pool_path)
    try:
        expected_sha = sha256_file(compiled_pool_path)
    except OSError as exc:
        raise CompileError("source_join_mismatch:glyph_fact:compiled_pool_missing") from exc
    if pool_pin.get("path") != expected_path:
        raise CompileError(
            "source_join_mismatch:glyph_fact:compiled_pool_path:"
            f"{pool_pin.get('path')}!={expected_path}"
        )
    if pool_pin.get("sha256") != expected_sha:
        raise CompileError("source_join_mismatch:glyph_fact:compiled_pool_sha256")

    mesh_pin = pins.get("mesh_admission")
    mesh_sha = pool.get("header", {}).get("mesh_admission_artifact_sha256")
    if mesh_pin is None:
        raise CompileError("source_join_mismatch:mesh_admission:pin_missing")
    try:
        mesh_source = Path(str(mesh_pin.get("path", "")))
        mesh_path = path_identity(mesh_source)
        mesh_actual_sha = sha256_file(mesh_source)
    except (CompileError, OSError, TypeError, ValueError) as exc:
        raise CompileError("source_join_mismatch:mesh_admission:path") from exc
    if mesh_path != mesh_pin.get("path"):
        raise CompileError("source_join_mismatch:mesh_admission:path")
    if mesh_pin.get("sha256") != mesh_actual_sha:
        raise CompileError("source_join_mismatch:mesh_admission_sha256")
    if mesh_pin.get("sha256") != mesh_sha:
        raise CompileError("source_join_mismatch:mesh_admission:compiled_pool_sha256")


def validate_mesh_gtr_candidate_domain(
    artifact: Dict[str, Any], gtr_v2: Dict[str, Any]
) -> None:
    """Require every mesh candidate to have authored temporal relations.

    The structural selector chooses only from the mesh GlyphFact row.  The
    temporal optimizer then queries GTR-v2 using that chosen glyph.  Those two
    domains therefore form one executable contract: accepting a mesh glyph
    that GTR-v2 cannot address makes the warm chain fail after an otherwise
    valid exact selection.  Equality is intentional.  A GTR-only glyph is also
    stale authority because it can never be selected by this route.
    """
    raw_gtr_ids = gtr_v2.get("glyph_ids")
    if not isinstance(raw_gtr_ids, list):
        raise CompileError("mesh_gtr_candidate_id_mismatch:gtr_glyph_ids_missing")

    def duplicate_ids(values: List[int]) -> List[int]:
        counts: Dict[int, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return sorted(value for value, count in counts.items() if count > 1)

    try:
        gtr_ids = [int(value) for value in raw_gtr_ids]
    except (TypeError, ValueError) as exc:
        raise CompileError("mesh_gtr_candidate_id_mismatch:gtr_glyph_id_invalid") from exc

    mesh_rows = 0
    for fact_index, fact in enumerate(artifact.get("facts", [])):
        membership_key = fact.get("scene_membership_key", {})
        if membership_key.get("route_key") != "mesh_feature.bundle.v1":
            continue
        mesh_rows += 1
        members = fact.get("glyph_members")
        if not isinstance(members, list):
            raise CompileError(
                "mesh_gtr_candidate_id_mismatch:"
                f"fact_index={fact_index}:glyph_members_missing"
            )
        try:
            fact_ids = [int(member["glyph_record_id"]) for member in members]
        except (KeyError, TypeError, ValueError) as exc:
            raise CompileError(
                "mesh_gtr_candidate_id_mismatch:"
                f"fact_index={fact_index}:glyph_record_id_invalid"
            ) from exc

        fact_only = sorted(set(fact_ids) - set(gtr_ids))
        gtr_only = sorted(set(gtr_ids) - set(fact_ids))
        fact_duplicates = duplicate_ids(fact_ids)
        gtr_duplicates = duplicate_ids(gtr_ids)
        if (
            sorted(fact_ids) != sorted(gtr_ids)
            or fact_duplicates
            or gtr_duplicates
        ):
            raise CompileError(
                "mesh_gtr_candidate_id_mismatch:"
                f"fact_index={fact_index}:route_key=mesh_feature.bundle.v1:"
                f"fact_count={len(fact_ids)}:gtr_count={len(gtr_ids)}:"
                f"fact_only={fact_only}:gtr_only={gtr_only}:"
                f"fact_duplicates={fact_duplicates}:gtr_duplicates={gtr_duplicates}"
            )
    if mesh_rows == 0:
        raise CompileError("mesh_gtr_candidate_id_mismatch:mesh_route_missing")


def build_receipt(
    sidecar: bytes,
    pins: List[Dict[str, Any]],
    artifact: Dict[str, Any],
    pool: Dict[str, Any],
    gtr_v2: Dict[str, Any],
    *,
    glyph_fact_path: Path,
    compiled_pool_path: Path,
    sidecar_path: Path,
    receipt_path: Path,
    preview: bool,
    relation_pointer_manifest_path: Path = RELATION_POINTER_MANIFEST,
    profile_path: Path = MATERIAL_RENDERING_PROFILES,
    authoring: bool = False,
) -> Dict[str, Any]:
    """Build the committed JSON receipt that records what the sidecar contains.

    Parameters:
      sidecar  -- the finished sidecar bytes (so we can fingerprint them).
      pins     -- the pin list embedded in the sidecar.
      artifact -- the parsed source artifact, for counts and identities.

    The receipt is the piece a human or a test reads. It is also how the C++
    caller learns the sidecar's own expected fingerprint and size: the loader
    treats the sidecar as the authority for everything EXCEPT itself, and the
    caller supplies the sidecar's fingerprint from here. That closes the circle
    without needing a JSON parser inside the engine.
    """
    identity = artifact["artifact_identity"]
    if preview:
        relation_pointer_override = (
            f"--relation-pointer {path_identity(relation_pointer_manifest_path)} "
            if relation_pointer_manifest_path != RELATION_POINTER_MANIFEST
            else ""
        )
        regeneration_command = (
            "python3 scripts/compile_fl4683_glyph_program.py --write "
            + ("--authoring " if authoring else "")
            +
            f"--glyph-fact {path_identity(glyph_fact_path)} "
            f"--compiled-pool {path_identity(compiled_pool_path)} "
            f"{relation_pointer_override}"
            + (f"--profiles {path_identity(profile_path)} "
               if profile_path != MATERIAL_RENDERING_PROFILES else "")
            +
            f"--sidecar-out {path_identity(sidecar_path)} "
            f"--receipt-out {path_identity(receipt_path)}"
        )
        regeneration_recheck_command = regeneration_command.replace(
            " --write ", " --check ", 1
        )
    else:
        regeneration_command = "python3 scripts/compile_fl4683_glyph_program.py --write"
        regeneration_recheck_command = "python3 scripts/compile_fl4683_glyph_program.py --check"
    receipt = {
        "schema": "fl4683.glyph_program_pin.v1",
        # Receipt schema revision. Moves with the sidecar format version so a
        # reader of the receipt alone can tell which binary contract the
        # recorded sidecar follows. 7 = FL-4783 immutable port graph tables.
        "version": 7,
        "artifact_kind": "compiled_glyph_program_sidecar_pin_receipt",
        "generated_by": "scripts/compile_fl4683_glyph_program.py",
        "sidecar": {
            "path": path_identity(sidecar_path),
            "byte_size": len(sidecar),
            "sha256": sha256_bytes(sidecar),
            "format_version": SIDECAR_FORMAT_VERSION,
            "magic": SIDECAR_MAGIC.decode("ascii"),
            "header_size": SIDECAR_HEADER_SIZE,
            "fact_record_size": FACT_RECORD_SIZE,
            "fact_member_size": FACT_MEMBER_SIZE,
            "relation_edge_size": RELATION_EDGE_SIZE,
            "pin_record_size": PIN_RECORD_SIZE,
            "scene_address_record_size": SCENE_ADDRESS_RECORD_SIZE,
            "gtr_v2_relation_record_size": GTR_V2_RELATION_RECORD_SIZE,
        },
        # Format version 5, FL-4512 D2/D3. The glyph-fact and compiled-pool
        # pins cover the joined inputs below; this receipt states their exact
        # paths and transforms so the binary fields remain independently
        # inspectable without re-inventing runtime policy.
        "harri_selector_contract": {
            "owning_fl": "FL-4512",
            "member_scorer_operand_size": 12,
            "member_shape6_source": (
                "glyph_records[].morphology.shape6_norm_q0_16 joined by "
                "glyph_record_id"
            ),
            "shape6_component_count": SHAPE6_COMPONENT_COUNT,
            "member_u7_source": (
                "glyph_records[].connectivity.mask8_u64_le_hex plus "
                "glyph_records[].structure.endpoint_count/junction_count "
                "joined by glyph_record_id"
            ),
            "member_primitive_source": (
                "fl4512.compiled_pool.v1 rows[].candidate_descriptors "
                "mask8_u64_le_hex/filled_region_class/detail_u16 joined by "
                "row_sha256 and glyph_id"
            ),
            "primitive_boundary_rule": (
                "boundary4 is derived exactly from candidate mask8 by S_pf.v1"
            ),
            "primitive_fact_policy_source": (
                "fl4512.compiled_pool.v1 materials[] joined by "
                "profile_namespace/material_key"
            ),
            "primitive_live_fact_count": sum(
                1
                for fact in artifact["facts"]
                if fact["scene_membership_key"]["scorer_id"] == "S_pf.v1"
            ),
            "primitive_live_profile_ids": sorted(
                {
                    fact["source_profile_id"]
                    for fact in artifact["facts"]
                    if fact["scene_membership_key"]["scorer_id"] == "S_pf.v1"
                }
            ),
            "primitive_fact_policy_values": _primitive_fact_policy_rows(
                artifact, pool
            ),
            "fact_scorer_source": "facts[].scene_membership_key.scorer_id",
            "scorer_kind_values": {
                "unset_refuse": SCORER_KIND_UNSET,
                "fl4512.shape6_f8_w4.v2": SCORER_KIND_SHAPE6_WEIGHTED_L2,
                "u7.mask.v1": SCORER_KIND_U7_MASK,
                "S_pf.v1": SCORER_KIND_S_PF,
                "fl4763.structural_decline.v1": SCORER_KIND_STRUCTURAL_DECLINE,
            },
            "fact_role_weights_source": (
                "profiles[].role_weights joined by facts[].source_profile_id"
            ),
            "role_weight_count": ROLE_WEIGHT_COUNT,
            "role_weight_quantization": (
                "q_W(w)=round-half-up(clamp(w,0,1.5)*16)"
            ),
            "role_weight_q4_min": ROLE_WEIGHT_Q4_MIN,
            "role_weight_q4_max": ROLE_WEIGHT_Q4_MAX,
            "shape_quantization": (
                "q_F(x)=round-half-up(clamp(x,0,1)*256)"
            ),
            "shape_weighted_l2_order": (
                "sum k=0..5 role_weights_q4[k]*(source_f8[k]-candidate_f8[k])^2"
            ),
            "reserved_fields_zero": True,
            # Format version 6, FL-4512 Phase 2: the compiled role-correct
            # abstention policy carried by each fact's policy byte and
            # scorer-scoped u32 bound.
            "no_match_policy_contract": _shape6_no_match_policy_summary(
                artifact, pool
            ),
        },
        "program_identity": {
            "artifact_id": identity["artifact_id"],
            "artifact_file_sha256": next(
                pin["sha256"] for pin in pins if pin["role"] == PIN_ROLE_GLYPH_FACT_ARTIFACT
            ),
            "artifact_content_sha256": artifact["canonical_content_sha256"],
            "glyph_universe_sha256": identity["glyph_universe_declared_sha256"],
            "scene_fact_schema_sha256": next(
                pin["sha256"]
                for pin in pins
                if pin["role"] == PIN_ROLE_SCENE_FACT_HANDSHAKE_SCHEMA
            ),
            "glyph_record_count": int(artifact["glyph_record_count"]),
            "fact_count": int(artifact["fact_count"]),
        },
        # Format version 3 (FL-4686 PA). Recorded so a reader of the receipt
        # alone knows the sidecar carries a lookup table, knows what its key is
        # made of, and knows that the table now covers addresses that hold no
        # glyphs as an explicit disposition rather than by omission.
        "scene_address_index": {
            "entry_count": _pool_address_total(pool),
            "populated_entry_count": int(artifact["fact_count"]),
            "declared_absent_entry_count": _pool_declared_absent_total(pool),
            "record_size": SCENE_ADDRESS_RECORD_SIZE,
            "key_fields": [
                "profile_namespace_code",
                "material_key",
                "route_family_bit",
                "lane_index",
            ],
            "disposition_values": {
                "populated": ADDRESS_DISPOSITION_POPULATED,
                "declared_absent": ADDRESS_DISPOSITION_DECLARED_ABSENT,
            },
            "no_fact_index_sentinel": NO_FACT_INDEX,
            "address_space_source": path_identity(compiled_pool_path),
            "declared_absence_is": (
                "a positive, offline declaration that the program OWNS this "
                "address and holds no glyph for it. It is not a lookup miss, "
                "not an empty legal row, and not a failure. FL-4686 PA."
            ),
            "sorted_by_key": True,
            "discriminator_source": (
                "SceneFactV1::route_family_mask, added by the FL-4683 "
                "tonal-discriminator amendment"
            ),
            "why": (
                "the compiled address fingerprint seals a whole compiled row and "
                "cannot be recomputed from a live cell description; this table is "
                "what turns namespace, material, route family, and lane into the "
                "matching fact"
            ),
        },
        # Format version 4, FL-4694 E8. This is the exact replacement surface
        # the aspect-free bounded projection could not supply. The source table
        # stays pinned and the loader validates all 65,536 canonical addresses.
        "gtr_v2_relation_projection": {
            "owning_fl": "FL-4694",
            "projection_id": "fl4694.glyph_program_gtr_v2_exact_projection.v1",
            "source_path": GTR_V2_ARTIFACT.as_posix(),
            "source_schema_path": GTR_V2_SCHEMA.as_posix(),
            "glyph_count": GTR_V2_GLYPH_COUNT,
            "aspect_count": GTR_V2_ASPECT_COUNT,
            "relations_per_aspect": GTR_V2_RELATIONS_PER_ASPECT,
            "relation_count": GTR_V2_RELATION_COUNT,
            "record_size": GTR_V2_RELATION_RECORD_SIZE,
            "static_component_count": GTR_V2_STATIC_COMPONENT_COUNT,
            "affine_component_count": GTR_V2_AFFINE_COMPONENT_COUNT,
            "descriptor_sha256": gtr_v2["descriptor_sha256"],
            "aspect_sha256": gtr_v2["aspect_sha256"],
            "relation_sha256": gtr_v2["relation_sha256"],
            "content_sha256": gtr_v2["content_sha256"],
            "address_fields": ["aspect", "from_glyph_id", "to_glyph_id"],
            "preserved_fields": [
                "static_components",
                "affine_components",
                "relation_confidence_q0_16",
                "compiled_transform_cost",
            ],
            "directionality": "all ordered source-target pairs including identity",
            "tie_order": "source GTR-v2 canonical target-glyph order",
            "exact_parity_rows": GTR_V2_RELATION_COUNT,
            "mismatch_rows": 0,
            "disposition": "EXACT_GTR_V2_PARITY",
            "delete_list_entry": "E8",
            "delete_list_disposition": "MANDATORY_DELETE_DURING_FL_4693_CUTOVER",
            "runtime_authority": False,
        },
        "runtime_rights": build_runtime_rights(artifact),
        "pins": [
            {
                "role": pin["role"],
                "role_name": PIN_ROLE_NAMES[pin["role"]],
                "path": pin["path"],
                "byte_size": pin["byte_size"],
                "sha256": pin["sha256"],
                # A "local regenerable" input may legitimately be absent on a
                # machine that has not run the FL-4694 compiler yet.
                "local_regenerable": bool(pin["flags"] & PIN_FLAG_LOCAL_REGENERABLE),
            }
            for pin in pins
        ],
        "regeneration": {
            "command": regeneration_command,
            "recheck_command": regeneration_recheck_command,
            "deterministic": True,
            "determinism_basis": (
                "fixed section order, fixed record strides, integer-only fields, "
                "committed pins in declared order, local pins sorted by role, "
                "glyph identifiers in ascending order, "
                "scene address index sorted by its lookup key, "
                "fact scorer kinds mapped by exact scorer id, "
                "profile role weights joined by source_profile_id and quantized "
                "with decimal round-half-up after a fixed clamp, "
                "member scorer operands joined by glyph_record_id and sealed "
                "compiled-pool row identity, "
                "fact policy flags and no-match bounds joined from the compiled "
                "pool's material no_match_policy by namespace and material key, "
                "GTR-v2 rows in canonical aspect/source/target order, "
                "no timestamps and no machine-specific values anywhere in the output"
            ),
            "local_cache_regeneration_command": (
                "python3 scripts/compile_fl4694_glyph_relations.py --write"
            ),
        },
    }
    if preview:
        receipt["artifact_scope"] = "authoring" if authoring else "preview"
        receipt["deployment_eligible"] = authoring
    if authoring:
        receipt["authoring_profile"] = {
            "path": path_identity(profile_path),
            "sha256": sha256_file(profile_path),
            "activation_owner": "Fl4693ProductProgramPrepareV1",
        }
    return receipt


def serialize_receipt(receipt: Dict[str, Any]) -> bytes:
    """Render the receipt as canonical JSON bytes.

    `sort_keys=True` and a fixed separator set remove every source of
    formatting variation, so two runs on the same inputs produce byte-identical
    receipts. The trailing newline matches the convention used by the other
    generated JSON in this directory.
    """
    return json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
def compile_all(
    *,
    glyph_fact_path: Path | None = None,
    compiled_pool_path: Path | None = None,
    sidecar_path: Path | None = None,
    receipt_path: Path | None = None,
    relation_pointer_manifest_path: Path | None = None,
    profile_path: Path | None = None,
    authoring: bool = False,
) -> Tuple[bytes, bytes]:
    """Produce sidecar and receipt bytes from fixed inputs or one preview tuple."""
    requested = (glyph_fact_path, compiled_pool_path, sidecar_path, receipt_path)
    if authoring and (profile_path is None or not all(value is not None for value in requested)):
        raise CompileError("authoring requires a staged profile and the complete path tuple")
    if profile_path is not None and not all(value is not None for value in requested):
        raise CompileError("--profiles requires the complete preview path tuple")
    if relation_pointer_manifest_path is not None and not all(
        value is not None for value in requested
    ):
        raise CompileError(
            "--relation-pointer preview override requires --glyph-fact, "
            "--compiled-pool, --sidecar-out, and --receipt-out together"
        )
    if any(value is not None for value in requested) and not all(
        value is not None for value in requested
    ):
        raise CompileError(
            "preview overrides require --glyph-fact, --compiled-pool, "
            "--sidecar-out, and --receipt-out together"
        )
    preview = all(value is not None for value in requested)
    if preview:
        validate_path = authoring_path if authoring else preview_path
        glyph_fact_path = validate_path(glyph_fact_path, "--glyph-fact")
        compiled_pool_path = validate_path(compiled_pool_path, "--compiled-pool")
        sidecar_path = validate_path(sidecar_path, "--sidecar-out")
        receipt_path = validate_path(receipt_path, "--receipt-out")
        if profile_path is not None:
            profile_path = validate_path(profile_path, "--profiles")
        if relation_pointer_manifest_path is not None:
            relation_pointer_manifest_path = preview_path(
                relation_pointer_manifest_path, "--relation-pointer"
            )
        output_paths = {
            _root_path(sidecar_path).resolve(strict=False),
            _root_path(receipt_path).resolve(strict=False),
        }
        input_paths = {
            _root_path(glyph_fact_path).resolve(strict=False),
            _root_path(compiled_pool_path).resolve(strict=False),
            _root_path(relation_pointer_manifest_path).resolve(strict=False)
            if relation_pointer_manifest_path is not None
            else _root_path(RELATION_POINTER_MANIFEST).resolve(strict=False),
        }
        if profile_path is not None:
            input_paths.add(_root_path(profile_path).resolve(strict=False))
        if len(output_paths) != 2 or output_paths & input_paths:
            raise CompileError("preview_output_input_collision")
        if relation_pointer_manifest_path is None:
            relation_pointer_manifest_path = RELATION_POINTER_MANIFEST
    else:
        glyph_fact_path = GLYPH_FACT_ARTIFACT
        compiled_pool_path = COMPILED_POOL_ADDRESS_SPACE
        sidecar_path = SIDECAR_OUTPUT
        receipt_path = RECEIPT_OUTPUT
        relation_pointer_manifest_path = RELATION_POINTER_MANIFEST

    profile_path = MATERIAL_RENDERING_PROFILES if profile_path is None else profile_path

    pointer_manifest = load_json(relation_pointer_manifest_path)
    pins = collect_pins(
        pointer_manifest,
        glyph_fact_path=glyph_fact_path,
        compiled_pool_path=compiled_pool_path,
        relation_pointer_manifest_path=relation_pointer_manifest_path,
    )
    artifact = load_json(glyph_fact_path)
    # FL-4686 PA: the pool is read as a second input because it, not the
    # glyph-fact artifact, is what enumerates the addresses that hold no glyph.
    pool = load_json(compiled_pool_path)
    # FL-4694 E8: this committed source becomes an exact, aspect-conditioned
    # section inside GlyphProgram. Runtime activation remains blocked.
    gtr_v2 = load_json(GTR_V2_ARTIFACT)
    validate_artifact_source_identity(artifact, pool, compiled_pool_path)
    # FL-4768: and the pool must still match the authored policy source it was
    # compiled from, or every bound encoded below is unauthorized.
    validate_pool_profile_pin(pool, profile_path)
    profile_pins = [pin for pin in artifact.get("source_pins", [])
                    if pin.get("name") == "material_profiles"]
    if (len(profile_pins) != 1
            or profile_pins[0].get("path") != path_identity(profile_path)
            or profile_pins[0].get("sha256") != sha256_file(profile_path)):
        raise CompileError("source_join_mismatch:glyph_fact:material_profiles")
    validate_mesh_gtr_candidate_domain(artifact, gtr_v2)
    sidecar = build_sidecar(artifact, pins, pool, gtr_v2)
    receipt = serialize_receipt(
        build_receipt(
            sidecar,
            pins,
            artifact,
            pool,
            gtr_v2,
            glyph_fact_path=glyph_fact_path,
            compiled_pool_path=compiled_pool_path,
            sidecar_path=sidecar_path,
            receipt_path=receipt_path,
            relation_pointer_manifest_path=relation_pointer_manifest_path,
            preview=preview,
            profile_path=profile_path,
            authoring=authoring,
        )
    )
    return sidecar, receipt


# -----------------------------------------------------------------------------
# Loader reachability and product-boundary audit (FL-4694 / FL-4512)
# -----------------------------------------------------------------------------
# WHY THIS EXISTS
# ---------------
# The receipt records three different facts. The immutable sidecar byte remains
# zero. A gate-enabled product source can load the sidecar and promote the
# validated in-memory view. A live receipt is still required to prove that a
# process reached startup and selected cells. These audits record only the
# source/build facts and are therefore wired into every receipt generation.
#
# THE METHOD, STATED SO A READER CAN REPRODUCE IT BY HAND
# --------------------------------------------------------
# 1. The loader's single public entry point is `Fl4683GlyphProgramLoadV1`,
#    declared in `apps/fl4512-runtime/platform/fl4683_glyph_program_loader.h`.
# 2. Every tracked C/C++ source file under the bounded engine, platform, and
#    apps/fl4512-runtime roots is scanned for a CALL to that symbol.
#    The loader's own implementation and header are excluded, because they
#    contain the definition and the declaration rather than a call. Test
#    sources, vendored trees, build output directories, and `dist/` are
#    excluded because none of them is the live product frame path (FL-4694).
# 3. A line counts as a call only when the symbol is followed by `(` and is not
#    preceded by its return type `Fl4683GlyphProgramLoadResultV1`, which is how
#    the definition and the declaration are written.
# 4. For each call site, every build configuration in the repository root is
#    inspected for two independent conditions: it defines the compile-time gate
#    `FL4683_GLYPH_PROGRAM_LOADER_ENABLED`, AND it lists the calling translation
#    unit among its compiled sources. Both must hold, because a gate without
#    the source compiles nothing and a source without the gate compiles the
#    loader's inert stub.
# 5. The reported count is the number of call sites for which at least one build
#    configuration satisfies both conditions. Those are exactly the call sites
#    that a gate-enabled product build can execute.
#
# WHAT THIS MEASUREMENT CANNOT SETTLE
# ------------------------------------
# This is a static reachability measurement, not an execution record. It proves
# that a gate-enabled build CONTAINS the call, not that a play session reached
# it. Confirming execution requires a live run and belongs to a runtime lane.
LOADER_ENTRY_SYMBOL = "Fl4683GlyphProgramLoadV1"
LOADER_ENTRY_RETURN_TYPE = "Fl4683GlyphProgramLoadResultV1"
LOADER_COMPILE_TIME_GATE = "FL4683_GLYPH_PROGRAM_LOADER_ENABLED"
PRODUCT_COMPILE_TIME_GATE = "FL4693_PRODUCT_GLYPH_PROGRAM_ENABLED"
PRODUCT_SOURCE = "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp"
FRAME_SOURCE = "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"
PRODUCT_START_SYMBOL = "Fl4693ProductGlyphProgramStartV1"
PRODUCT_SELECT_SYMBOL = "Fl4693ProductGlyphSelectV1"
EXACT_SELECT_SYMBOL = "Fl4683ExactCellSelectV1"
EXACT_SELECT_RETURN_TYPE = "Fl4683ExactCellSelectionV1"
AUDIT_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".inl")


def _audit_candidate_sources() -> List[Path]:
    """Every tracked product C/C++ file that can hold a product call."""
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", "engine", "platform", "apps/fl4512-runtime"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise CompileError("git ls-files failed while measuring product call paths")
    found: List[Path] = []
    for encoded in completed.stdout.split(b"\0"):
        if not encoded:
            continue
        rel = encoded.decode("utf-8")
        path = ROOT / rel
        if path.suffix not in AUDIT_SOURCE_SUFFIXES:
            continue
        found.append(path)
    return found


PRODUCT_MANIFEST = "apps/fl4512-runtime/fl4512_product_sources.mk"
PRODUCT_MANIFEST_WEB_PARTITIONS = (
    "FL4512_CORE_SRCS",
    "FL4512_PROGRAM_SRCS",
    "FL4512_PORTS_ANCHOR",
)


def _product_manifest_entries() -> Dict[str, List[str]]:
    """Parse the product-owned source manifest into make variables."""
    entries: Dict[str, List[str]] = {}
    current: str | None = None
    try:
        text = (ROOT / PRODUCT_MANIFEST).read_text(encoding="utf-8")
    except OSError:
        return entries
    for line in text.splitlines():
        match = re.match(r"^(FL4512_[A-Z_]+)\s*:=\s*(.*)$", line)
        if match is not None:
            current = match.group(1)
            entries[current] = match.group(2).replace("\\", " ").split()
            continue
        if current is None:
            continue
        stripped = line.strip().rstrip("\\").strip()
        if stripped:
            entries[current].extend(stripped.split())
        if not line.rstrip().endswith("\\"):
            current = None
    return entries


def _read_config_blob(config: Path) -> str:
    """Read a build config with product-manifest selections expanded.

    Consumer specs select manifest partitions instead of naming product
    sources: ``$(VAR)`` tokens expand through the product manifest, and a
    manifest-driven spec (build-web.sh: its awk pulls exactly
    CORE|PROGRAM plus the ports anchor) gains those partition entries so
    source-membership checks resolve. Anything else stays literal.
    """
    try:
        blob = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    entries = _product_manifest_entries()
    for var, paths in entries.items():
        blob = blob.replace("$(%s)" % var, " ".join(paths))
    if PRODUCT_MANIFEST in blob and all(
        part in blob for part in ("FL4512_CORE_SRCS", "FL4512_PROGRAM_SRCS")
    ):
        web_selected: List[str] = []
        for part in PRODUCT_MANIFEST_WEB_PARTITIONS:
            web_selected.extend(entries.get(part, []))
        blob += "\n" + " ".join(web_selected)
    return blob


def _audit_build_configs() -> List[Path]:
    """Build configurations at the repository root, in a stable order."""
    configs = [p for p in sorted(ROOT.glob("makefile*")) if p.is_file()]
    configs += [p for p in sorted(ROOT.glob("Makefile*")) if p.is_file()]
    configs += [p for p in sorted(ROOT.glob("build*.sh")) if p.is_file()]
    configs += [
        ROOT / "apps/game-fl4512/makefile_game",
        ROOT / "apps/game-fl4512/makefile_game_mac",
        ROOT / "apps/asciicker-presenter/makefile_asciiid",
        ROOT / "apps/asciicker-presenter/makefile_asciiid_mac",
    ]
    return [p for p in configs if p.is_file()]


def _find_symbol_call_sites(symbol: str, return_type: str) -> List[Dict[str, Any]]:
    """Find direct source calls while excluding declarations and definitions."""
    call_pattern = re.compile(re.escape(symbol) + r"\s*\(")
    call_sites: List[Dict[str, Any]] = []
    for path in _audit_candidate_sources():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if symbol not in text:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for number, line in enumerate(text.splitlines(), start=1):
            if not call_pattern.search(line):
                continue
            # A definition or a declaration names the return type on the same
            # line or is the line the return type introduces; neither is a call.
            if return_type and return_type in line and "=" not in line:
                continue
            call_sites.append({"path": rel, "line": number, "text": line.strip()})
    return call_sites


def _enabled_build_configs(
    call_sites: List[Dict[str, Any]], required_gate: str
) -> Dict[str, List[str]]:
    """Join source calls to build files by exact macro and source tokens."""
    configs = _audit_build_configs()
    gate_pattern = re.compile(
        r"-D" + re.escape(required_gate) + r"(?:=1)?(?:\s|\\|$)"
    )
    gate_and_source: Dict[str, List[str]] = {}
    for site in call_sites:
        enabling: List[str] = []
        source_pattern = re.compile(
            r"(?<![A-Za-z0-9_])"
            + re.escape(site["path"])
            + r"(?![A-Za-z0-9_])"
        )
        for config in configs:
            blob = _read_config_blob(config)
            if gate_pattern.search(blob) and source_pattern.search(blob):
                enabling.append(config.name)
        gate_and_source[f"{site['path']}:{site['line']}"] = enabling
    return gate_and_source


def audit_loader_call_sites() -> Dict[str, Any]:
    """Measure loader calls in tracked gate-enabled product translation units."""
    call_sites = _find_symbol_call_sites(
        LOADER_ENTRY_SYMBOL, LOADER_ENTRY_RETURN_TYPE
    )
    gate_and_source = _enabled_build_configs(call_sites, LOADER_COMPILE_TIME_GATE)
    reachable_sites = []
    for site in call_sites:
        enabled_by = gate_and_source[
            f"{site['path']}:{site['line']}"
        ]
        if enabled_by:
            site["enabled_by"] = enabled_by
            reachable_sites.append(site)
    return {
        "measurement": "static_gate_reachability",
        "entry_symbol": LOADER_ENTRY_SYMBOL,
        "compile_time_gate": LOADER_COMPILE_TIME_GATE,
        # Serialize only gate-reachable calls. This makes the count and the
        # receipt list one measured set instead of two independently filtered
        # views that could drift.
        "call_sites": reachable_sites,
        "measured_call_site_count": len(reachable_sites),
        "scope_ceiling": "static_gate_reachability_only",
    }


def audit_product_startup_promotion() -> Dict[str, Any]:
    """Measure the source-owned promotion from validated image to live view."""
    source = ROOT / PRODUCT_SOURCE
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        text = ""
    required_markers = (
        f"bool {PRODUCT_START_SYMBOL}(void)",
        f"{LOADER_ENTRY_SYMBOL}(&request)",
        "g_image->program.runtime_rights = g_image->declared_future_rights",
        "g_image->program.runtime_consumption_allowed = 1u",
        "FL4683_GLYPH_PROGRAM_STATUS_LOADED",
        "FL4683_GLYPH_PROGRAM_STATUS_LOADED_WITHOUT_LOCAL_RELATION_CACHE",
    )
    configs = []
    for config in _audit_build_configs():
        blob = _read_config_blob(config)
        if (
            re.search(r"-D" + re.escape(LOADER_COMPILE_TIME_GATE) + r"(?:=1)?(?:\s|\\|$)", blob)
            and re.search(r"-D" + re.escape(PRODUCT_COMPILE_TIME_GATE) + r"(?:=1)?(?:\s|\\|$)", blob)
            and PRODUCT_SOURCE in blob
        ):
            configs.append(config.name)
    enabled = bool(text) and all(marker in text for marker in required_markers)
    return {
        "enabled": enabled and bool(configs),
        "owner_source": PRODUCT_SOURCE,
        "owner_symbol": PRODUCT_START_SYMBOL,
        "requires_loader_status": [
            "loaded",
            "loaded_without_local_relation_cache",
        ],
        "runtime_consumption_allowed_after_promotion": True,
        "runtime_rights_after_promotion": [
            "score_compiler_emitted_costs",
            "select_fact_id_from_scene_fact",
        ],
        "enabled_by": configs,
        "scope_ceiling": "static_source_contract_only",
    }


def audit_selector_call_path() -> Dict[str, Any]:
    """Measure the static resolver -> product -> exact-selector call chain."""
    frame_calls = [
        site
        for site in _find_symbol_call_sites(PRODUCT_SELECT_SYMBOL, "")
        if site["path"] == FRAME_SOURCE
    ]
    product_calls = [
        site
        for site in _find_symbol_call_sites(EXACT_SELECT_SYMBOL, EXACT_SELECT_RETURN_TYPE)
        if site["path"] == PRODUCT_SOURCE
    ]
    steps = []
    if len(frame_calls) == 1:
        steps.append(
            {
                "path": frame_calls[0]["path"],
                "line": frame_calls[0]["line"],
                "symbol": PRODUCT_SELECT_SYMBOL,
            }
        )
    if len(product_calls) == 1:
        steps.append(
            {
                "path": product_calls[0]["path"],
                "line": product_calls[0]["line"],
                "symbol": EXACT_SELECT_SYMBOL,
            }
        )
    return {
        "enabled": len(steps) == 2,
        "steps": steps,
        "scope_ceiling": "static_source_path_only",
    }


def build_runtime_rights(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """Build receipt rights from serialized bytes and measured source owners."""
    loader = audit_loader_call_sites()
    return {
        "serialized_sidecar": {
            "runtime_consumption_allowed": False,
            "header_byte_offset": 160,
            "header_byte_value": 0,
        },
        "declared_future_rights": sorted(
            artifact["runtime_rights"].get("future_rights_after_handshake", [])
        ),
        "product_startup_promotion": audit_product_startup_promotion(),
        "loader_call_site_measurement": {
            "entry_symbol": loader["entry_symbol"],
            "measured_call_site_count": loader["measured_call_site_count"],
            "call_sites": [
                {
                    "path": site["path"],
                    "line": site["line"],
                    "enabled_by": site["enabled_by"],
                }
                for site in loader["call_sites"]
            ],
            "scope_ceiling": loader["scope_ceiling"],
        },
        "selector_call_path": audit_selector_call_path(),
        "compile_time_gate": LOADER_COMPILE_TIME_GATE,
        "compile_time_gate_default": "off",
    }


def print_loader_call_site_audit() -> int:
    """Print the audit as readable text. Always exits 0; it reports, not gates."""
    report = audit_loader_call_sites()
    print(f"measurement: {report['measurement']}")
    print(f"entry symbol: {report['entry_symbol']}")
    print(f"compile-time gate: {report['compile_time_gate']}")
    print(f"call sites found: {len(report['call_sites'])}")
    for site in report["call_sites"]:
        state = ", ".join(site["enabled_by"]) if site["enabled_by"] else "NONE (stub only)"
        print(f"  {site['path']}:{site['line']}: {site['text']}")
        print(f"    enabled by: {state}")
    print(f"MEASURED loader_call_site_count = {report['measured_call_site_count']}")
    print(f"scope ceiling: {report['scope_ceiling']}")
    return 0


def compile_authoring_transaction(profile_source: Path, pool_source: Path, output: Path) -> Path:
    """Snapshot the edited source and compile through the existing owners.

    The retained transaction directory is immutable after this call; native
    preparation validates its pins before the presenter commits durable state.
    Never overwrite an earlier transaction or any canonical generated input.
    """
    import compile_fl4676_glyph_facts as fact_compiler

    members = {name: authoring_path(output / name, "--authoring-output") for name in (
        "profiles.json", "pool.json", "mesh.json", "facts.json", "program.bin", "receipt.json"
    )}
    root = _root_path(output)
    if root.exists() and any(root.iterdir()):
        raise CompileError("authoring_output_not_empty")
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(profile_source, _root_path(members["profiles.json"]))
    shutil.copyfile(pool_source, _root_path(members["pool.json"]))
    shutil.copyfile(_root_path(fact_compiler.SOURCES["mesh_admission"]),
                    _root_path(members["mesh.json"]))
    facts = fact_compiler.compile_artifact(
        pool_path=members["pool.json"], mesh_admission_path=members["mesh.json"],
        profile_path=members["profiles.json"], authoring=True,
    )
    fact_compiler.write_json(_root_path(members["facts.json"]), facts)
    sidecar, receipt = compile_all(
        glyph_fact_path=members["facts.json"], compiled_pool_path=members["pool.json"],
        sidecar_path=members["program.bin"], receipt_path=members["receipt.json"],
        profile_path=members["profiles.json"], authoring=True,
    )
    _root_path(members["program.bin"]).write_bytes(sidecar)
    _root_path(members["receipt.json"]).write_bytes(receipt)
    # The published receipt must not outlive unflushed input snapshots. This
    # path is authoring-time I/O, never part of glyph selection or a frame.
    for member in members.values():
        with _root_path(member).open("rb") as stream:
            os.fsync(stream.fileno())
    if os.name != "nt":
        for directory in (root, root.parent):
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    return members["receipt.json"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile the FL-4683 binary glyph-program sidecar and its pin receipt."
    )
    parser.add_argument(
        "--write", action="store_true", help="regenerate the sidecar and receipt on disk"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="recompute in memory and exit non-zero if the on-disk copies differ",
    )
    parser.add_argument("--glyph-fact")
    parser.add_argument("--compiled-pool")
    parser.add_argument("--sidecar-out")
    parser.add_argument("--receipt-out")
    parser.add_argument("--profiles", help="staged authored profile matching the overridden pool")
    parser.add_argument("--authoring", action="store_true", help="compile for validated live authoring activation")
    parser.add_argument("--authoring-source", type=Path)
    parser.add_argument("--authoring-pool", type=Path)
    parser.add_argument("--authoring-output", type=Path)
    parser.add_argument(
        "--relation-pointer",
        help=(
            "preview-only relation-pointer manifest override; requires all "
            "four other preview paths"
        ),
    )
    parser.add_argument(
        "--audit-loader-call-sites",
        action="store_true",
        help=(
            "FL-4694: measure and print the real number of loader call sites a "
            "gate-enabled build can execute; writes nothing, changes no pinned byte"
        ),
    )
    args = parser.parse_args()
    authoring_inputs = (args.authoring_source, args.authoring_pool, args.authoring_output)
    if any(value is not None for value in authoring_inputs):
        if (not all(value is not None for value in authoring_inputs) or not args.write
                or args.check or args.audit_loader_call_sites
                or any(value is not None for value in (
                    args.glyph_fact, args.compiled_pool, args.sidecar_out,
                    args.receipt_out, args.profiles, args.relation_pointer))):
            parser.error("authoring transaction requires --write and all three --authoring-* paths only")
        try:
            receipt_path = compile_authoring_transaction(*authoring_inputs)
            print(f"authoring_receipt={receipt_path.as_posix()}")
            return 0
        except (CompileError, ValueError, OSError) as exc:
            print(f"FL-4512 authoring compile failed: {exc}", file=sys.stderr)
            return 2
    # The audit is a read-only report, not a compile mode, so it is handled
    # before the write/check mode check and never combined with either.
    if args.audit_loader_call_sites:
        if args.write or args.check or args.authoring or any(
            value is not None
            for value in (
                args.glyph_fact,
                args.compiled_pool,
                args.sidecar_out,
                args.receipt_out,
                args.relation_pointer,
                args.profiles,
            )
        ):
            parser.error(
                "--audit-loader-call-sites is a report; do not combine it "
                "with compile modes or preview overrides"
            )
        return print_loader_call_site_audit()
    # Exactly one mode must be chosen; `==` on two booleans is True when both
    # are set and when neither is set, which are the two invalid cases.
    if args.write == args.check:
        parser.error("choose exactly one of --write and --check")

    override_values = (
        args.glyph_fact,
        args.compiled_pool,
        args.sidecar_out,
        args.receipt_out,
    )
    has_base_overrides = all(value is not None for value in override_values)
    if args.profiles is not None and not has_base_overrides:
        parser.error("--profiles requires the complete preview path tuple")
    if args.authoring and (args.profiles is None or not has_base_overrides):
        parser.error("--authoring requires --profiles and the complete path tuple")
    if args.relation_pointer is not None and not has_base_overrides:
        parser.error(
            "--relation-pointer preview override requires --glyph-fact, "
            "--compiled-pool, --sidecar-out, and --receipt-out together"
        )
    if any(value is not None for value in override_values) and not has_base_overrides:
        parser.error(
            "preview overrides require --glyph-fact, --compiled-pool, "
            "--sidecar-out, and --receipt-out together"
        )
    has_preview_overrides = has_base_overrides or args.relation_pointer is not None
    try:
        preview_args = (
            {}
            if not has_preview_overrides
            else {
                "glyph_fact_path": preview_path(args.glyph_fact, "--glyph-fact"),
                "compiled_pool_path": preview_path(args.compiled_pool, "--compiled-pool"),
                "sidecar_path": preview_path(args.sidecar_out, "--sidecar-out"),
                "receipt_path": preview_path(args.receipt_out, "--receipt-out"),
                "profile_path": (preview_path(args.profiles, "--profiles")
                                 if args.profiles is not None else None),
                "authoring": args.authoring,
                "relation_pointer_manifest_path": (
                    preview_path(args.relation_pointer, "--relation-pointer")
                    if args.relation_pointer is not None
                    else None
                ),
            }
        )
        if preview_args and preview_args["sidecar_path"] == preview_args["receipt_path"]:
            raise CompileError("preview_output_input_collision")
        sidecar, receipt = compile_all(**preview_args)
    except CompileError as exc:
        print(f"FL-4683 glyph-program compile failed: {exc}", file=sys.stderr)
        return 2

    sidecar_path = (
        ROOT / SIDECAR_OUTPUT
        if not preview_args
        else ROOT / preview_args["sidecar_path"]
    )
    receipt_path = (
        ROOT / RECEIPT_OUTPUT
        if not preview_args
        else ROOT / preview_args["receipt_path"]
    )

    if args.write:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_bytes(sidecar)
        receipt_path.write_bytes(receipt)
        print(
            f"wrote {path_identity(sidecar_path)} bytes={len(sidecar)} "
            f"sha256={sha256_bytes(sidecar)}"
        )
        print(f"wrote {path_identity(receipt_path)} bytes={len(receipt)}")
        return 0

    # --check mode below.
    stale = False
    if not sidecar_path.is_file():
        print(f"{path_identity(sidecar_path)} is missing", file=sys.stderr)
        stale = True
    elif sidecar_path.read_bytes() != sidecar:
        print(f"{path_identity(sidecar_path)} is stale", file=sys.stderr)
        stale = True
    if not receipt_path.is_file():
        print(f"{path_identity(receipt_path)} is missing", file=sys.stderr)
        stale = True
    elif receipt_path.read_bytes() != receipt:
        print(f"{path_identity(receipt_path)} is stale", file=sys.stderr)
        stale = True
    if stale:
        return 1
    print(
        f"ok {path_identity(sidecar_path)} bytes={len(sidecar)} "
        f"sha256={sha256_bytes(sidecar)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
