#!/usr/bin/env python3
# =============================================================================
# compile_fl4694_glyph_relations.py
# =============================================================================
# WHAT THIS PROGRAM IS FOR
# ------------------------
# The project keeps a very large catalogue of "glyphs".  A glyph is one drawable
# character cell -- a letter, a box-drawing line, a shading block, and so on.
# An earlier compiler (scripts/compile_fl4676_glyph_facts.py) measured every one
# of the 111,040 admitted glyphs and wrote the measurements into a single big
# file: assets/glyphs/generated/fl4676.glyph_fact.v1.json.  That file is the
# "unified glyph record artifact".  Each entry in it is called a GlyphRecord and
# carries numbers describing the glyph's shape, how much ink it uses, how many
# separate pieces and holes it has, which pixels are filled, and which way its
# strokes point.
#
# Two kinds of information that downstream design work wanted were measured as
# ABSENT from that artifact.  This program compiles those two missing families:
#
#   FAMILY 1 -- "weighted relations to other glyphs".
#       We want to know, for every glyph, which other glyphs look most like it,
#       and how strongly.  A complete table of every glyph against every other
#       glyph would need 111,040 x 111,040 numbers, roughly 1.6 terabytes, which
#       is not something anyone can store or ship.  The operator therefore chose
#       "Form A-LOCAL": instead of the complete table we compile a bounded
#       k-nearest-neighbour list -- for each glyph only its k closest relatives,
#       with k defaulting to 32.  The resulting cache is written to a LOCAL,
#       never-committed directory.  What the repository does commit is a small
#       "pointer manifest": a JSON file recording the cache's SHA-256 fingerprint,
#       its exact byte size, the k that was used, the precise distance rule, the
#       SHA-256 of every input that fed the computation, and the one command that
#       recreates the cache byte-for-byte.  A different machine can therefore
#       rebuild an identical cache and check it against the committed pins.
#
#   FAMILY 2 -- "ramp membership".
#       A "ramp" here is an ordered ladder of glyphs.  A TONAL ramp steps from
#       light to dark (increasing ink coverage), the way ASCII art uses
#       " .:-=+*#%@" to fake shading.  A DIRECTIONAL ramp steps around a turning
#       circle, so consecutive members point progressively further round.  The
#       fl4676 artifact records no ramp membership at all, so this program
#       derives ramps from the already-measured numbers and records, for every
#       glyph, which ramps it belongs to and at which rung of the ladder.
#
# WHAT THIS PROGRAM DELIBERATELY DOES NOT DO
# ------------------------------------------
# It does not touch the fl4676 schema, and in particular it does not weaken the
# rule that says the live game may not consume any of this data yet
# (runtime_rights.runtime_consumption_allowed is fixed to false there, blocked by
# FL-4683).  Everything here is offline compilation only.  The alternative that
# would have let the running game derive relations for itself ("Form B") was
# rejected; this file restates the prohibition in its output but never amends it.
#
# HOW TO RUN IT
# -------------
#   Rebuild the local cache and rewrite the committed pointer manifest:
#       python3 scripts/compile_fl4694_glyph_relations.py --write
#
#   Fast integrity check -- re-hash the local cache files that already exist on
#   disk and compare them against the committed pointer manifest:
#       python3 scripts/compile_fl4694_glyph_relations.py --verify
#
#   Full recomputation check -- redo the whole compile into a scratch directory
#   and confirm the committed manifest still describes it exactly (slow, minutes):
#       python3 scripts/compile_fl4694_glyph_relations.py --check
#
# FAIL-CLOSED BEHAVIOUR
# ---------------------
# "Fail closed" means: when something required is missing or inconsistent, stop
# with a named error instead of guessing, skipping, or substituting a default.
# Every check below raises RelationCompileError with a colon-separated reason
# code so the failure is greppable and unambiguous.
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator

import numpy as np

# The repository root is two levels up from this file (scripts/ -> repo root).
ROOT = Path(__file__).resolve().parents[1]

# The accepted FL-4676 compiler owns the weight-normalisation algorithm named
# "descending_rank_largest_remainder.v1".  We import it rather than writing a
# second copy so that there is exactly ONE owner of that arithmetic in the tree.
sys.path.insert(0, str(ROOT / "scripts"))
from compile_fl4676_glyph_facts import normalized_member_weights  # noqa: E402


# -----------------------------------------------------------------------------
# Identity constants: the names and versions this compiler stamps on its output.
# -----------------------------------------------------------------------------

# Identifier written into the committed pointer manifest.
POINTER_SCHEMA_ID = "fl4694.relation_pointer.v1"
# Identifier written into the header line of the local k-nearest-neighbour cache.
KNN_SCHEMA_ID = "fl4694.glyph_relation_knn.v1"
# Identifier written into the header line of the local ramp-definition cache.
RAMP_SCHEMA_ID = "fl4694.glyph_ramp.v1"
# Identifier written into the header line of the local per-glyph ramp index.
RAMP_INDEX_SCHEMA_ID = "fl4694.ramp_record_index.v1"
# Identifier for the exact arithmetic rule used to score glyph similarity.
DISTANCE_DEFINITION_ID = "fl4694.weighted_squared_integer_distance.v1"

# Default neighbour breadth.  32 is chosen to match the per-glyph relation
# breadth already accepted in the FL-4512 "glyph transform relation v2" table and
# the max_edges_per_fact = 32 bound in the fl4676 schema.  It is recorded in the
# manifest as an explicitly revisable parameter, not as a law.
DEFAULT_K = 32

# Every membership weight in this project is a "Q0.16" fixed-point number: an
# integer from 0 to 65535 standing for a fraction from 0.0 to 1.0, where 65535
# means 1.0.  A set of weights that describes one whole thing must sum to exactly
# 65535 -- no rounding drift allowed.
NORMALIZED_WEIGHT_SUM = 65535

# Upper bound on how many glyphs one ramp may contain.  Matches the 32-member
# cap the fl4676 schema places on a single fact's member list.
RAMP_MEMBER_CAP = 32

# How many direction sectors the turning circle is divided into for directional
# ramps.  32 sectors means each sector spans 1/32 of a full turn.
DIRECTION_SECTOR_COUNT = 32

# ---- Distance-rule constants (see build_feature_matrix for the full rationale)
# Each glyph becomes a vector of 141 whole numbers.  Distance between two glyphs
# is a weighted sum of squared differences of those numbers.  Keeping every value
# an integer is what makes the result identical on every machine.

# Ink-coverage counts (how many separate pieces, how many holes, how many stroke
# ends, how many junctions) are small integers, typically 0..24.  Multiplying them
# by 2048 puts them on roughly the same numeric footing as the 0..65535 fields:
# a difference of 31 steps becomes 63,488, close to the 65,535 full scale.
STRUCTURE_STEP_SCALE = 2048
# The largest count we are willing to represent.  Real data maxes out at 24, so
# this never triggers; if a future recompile exceeds it we stop rather than clamp.
STRUCTURE_COUNT_MAX = 31

# Each of the 64 pixels in a glyph's coverage mask becomes its own 0-or-BIT_SCALE
# coordinate.  With BIT_SCALE = 8192, two glyphs differing in all 64 pixels are
# 64 * 8192^2 = 65536^2 apart -- exactly the same as one full-scale 0..65535
# field differing end to end.  That is why 8192 was chosen: it makes "completely
# different pixels" weigh the same as "completely different shape number".
BIT_SCALE = 8192

# Per-family importance multipliers.  Larger means that family dominates more.
WEIGHT_SHAPE6 = 4       # the six normalised shape numbers (outline proportions)
WEIGHT_DENSITY = 8      # ink coverage -- the single most legible visual property
WEIGHT_STRUCTURE = 1    # piece/hole/endpoint/junction counts
WEIGHT_ORIENTATION = 2  # which way the strokes lean
WEIGHT_MASK8 = 4        # the filled-pixel mask
WEIGHT_SKELETON = 2     # the thinned centre-line mask (derived from the mask)

# Number of coordinates in the feature vector, and where each family starts.
DIM_SHAPE6 = 6
DIM_DENSITY = 1
DIM_STRUCTURE = 4
DIM_ORIENTATION = 2
DIM_MASK8 = 64
DIM_SKELETON = 64
FEATURE_DIMENSIONS = (
    DIM_SHAPE6 + DIM_DENSITY + DIM_STRUCTURE + DIM_ORIENTATION + DIM_MASK8 + DIM_SKELETON
)

# How many source glyphs are scored against the whole universe at once.  Larger
# chunks are faster but need more memory: one chunk holds CHUNK_ROWS x 111,040
# eight-byte numbers, so 128 rows is about 114 MB.
CHUNK_ROWS = 128

# IEEE-754 double-precision floating point stores whole numbers exactly only up
# to 2^53.  Every intermediate value in our matrix multiplication is a whole
# number well below this, which is why the floating-point fast path returns the
# exact integer answer.  The bound is asserted at runtime as a safety net.
FLOAT64_EXACT_INTEGER_LIMIT = 2 ** 53

# Default locations.
DEFAULT_GLYPH_FACT_PATH = ROOT / "assets/glyphs/generated/fl4676.glyph_fact.v1.json"
DEFAULT_LOCAL_DIR = ROOT / "assets/glyphs/generated/local"
DEFAULT_MANIFEST_PATH = ROOT / "assets/glyphs/generated/fl4694.relation_pointer.v1.json"
POINTER_SCHEMA_PATH = ROOT / "assets/glyphs/schema/fl4694.relation_pointer.v1.schema.json"
FL4676_SCHEMA_PATH = ROOT / "assets/glyphs/schema/fl4676.glyph_fact.v1.schema.json"
FL4676_COMPILER_PATH = ROOT / "scripts/compile_fl4676_glyph_facts.py"
SELF_PATH = ROOT / "scripts/compile_fl4694_glyph_relations.py"

# The one command a fresh machine runs to recreate the local cache.
REGENERATION_COMMAND = "python3 scripts/compile_fl4694_glyph_relations.py --write"

# Restated (never amended) prohibition, copied from the accepted fl4676 artifact
# so that anyone reading only the pointer manifest still sees the ban.
FORBIDDEN_RUNTIME_AUTHORITIES = [
    "derive_glyph_metadata_at_runtime",
    "derive_relation_edges_at_runtime",
    "invent_membership_at_runtime",
    "mutate_compiled_artifact",
    "substitute_missing_fact",
    "synthesize_replacement_rows",
    "treat_scene_fact_as_glyph_metadata",
]


class RelationCompileError(ValueError):
    """One stable, greppable, fail-closed diagnostic for this compiler.

    Every message is a colon-separated reason code, for example
    ``required_attribute_missing:morphology.density_q0_16:16851837``.  The first
    segment names the failure class, later segments locate it.
    """


# =============================================================================
# Section 1 -- small deterministic helpers (hashing and canonical JSON)
# =============================================================================


def canonical(value: Any) -> bytes:
    """Serialise a Python value to the project's one canonical JSON byte form.

    Canonical means: identical input always produces identical bytes.  We force
    sorted keys (so dictionary insertion order cannot leak in), the tightest
    separators (no incidental whitespace), UTF-8 text rather than \\u escapes,
    and we reject NaN/Infinity which have no portable JSON spelling.

    Parameters
    ----------
    value:
        Any JSON-compatible Python value (dict, list, str, int, float, bool, None).

    Returns
    -------
    The UTF-8 encoded canonical JSON bytes.
    """
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    """Return the SHA-256 fingerprint of some bytes as 64 lowercase hex digits.

    A SHA-256 fingerprint is a short string that changes completely if even one
    byte of the input changes, so it is how we prove two files are identical
    without shipping both of them.
    """
    return hashlib.sha256(payload).hexdigest()


def sha_value(value: Any) -> str:
    """Fingerprint a Python value by first putting it in canonical JSON form."""
    return sha_bytes(canonical(value))


def sha_file(path: Path) -> str:
    """Fingerprint a file on disk, reading it in chunks so huge files are safe.

    Parameters
    ----------
    path:
        Absolute path to the file to fingerprint.

    Returns
    -------
    64 lowercase hex digits.

    Raises
    ------
    RelationCompileError
        If the file cannot be read at all -- we never silently pin a missing input.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            # 1 MiB at a time keeps memory flat regardless of file size.
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise RelationCompileError(f"source_pin_read_failed:{path}") from exc
    return digest.hexdigest()


def relative_to_root(path: Path) -> str:
    """Express a path relative to the repository root when possible.

    Manifests must not contain machine-specific absolute paths, otherwise the
    same compile on another computer would produce a different manifest.
    """
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        # A path outside the repository (a pytest temporary directory, say) is
        # reported by its final name only, again to keep the output portable.
        return path.name


def require(mapping: Any, key: str, owner: str) -> Any:
    """Fetch ``mapping[key]`` or stop the whole compile with a named error.

    This is the single choke point that enforces "fail closed on any missing
    attribute": nothing in this compiler is allowed to reach for a field with a
    default value, because a silently defaulted field would corrupt the compiled
    relations without anybody noticing.

    Parameters
    ----------
    mapping:
        The dictionary we expect to contain ``key``.
    key:
        The field name we require.
    owner:
        Human-readable location used in the error text, for example
        ``"glyph_record[16851837].morphology"``.

    Returns
    -------
    The value stored at ``key``.

    Raises
    ------
    RelationCompileError
        Reason code ``required_attribute_missing`` if the field is absent, or
        ``malformed_record`` if the container was not a dictionary at all.
    """
    if not isinstance(mapping, dict):
        raise RelationCompileError(f"malformed_record:{owner}:not_an_object")
    if key not in mapping:
        raise RelationCompileError(f"required_attribute_missing:{owner}.{key}")
    return mapping[key]


def require_int(mapping: Any, key: str, owner: str) -> int:
    """Fetch a field that must be a whole number, stopping the compile if not.

    JSON has only one numeric type, so a value written as ``3.0`` would parse as
    a Python float.  We refuse floats outright because a float would break the
    exact-integer guarantee that makes this compile reproducible.
    """
    value = require(mapping, key, owner)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RelationCompileError(f"malformed_record:{owner}.{key}:not_an_integer")
    return value


# =============================================================================
# Section 2 -- read the accepted FL-4676 artifact and turn it into numbers
# =============================================================================


def load_glyph_fact_artifact(path: Path) -> dict[str, Any]:
    """Load the unified glyph record artifact and check its self-declared identity.

    We do not re-derive the artifact here; we only confirm that the file we were
    handed really is an fl4676 artifact and that the runtime prohibition inside it
    is still switched off.  If the prohibition were ever found flipped on, this
    compiler stops, because Form A-LOCAL exists precisely so that the prohibition
    can stay in place.

    Parameters
    ----------
    path:
        Absolute path to fl4676.glyph_fact.v1.json (or a test fixture shaped
        like it).

    Returns
    -------
    The parsed artifact as a dictionary.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RelationCompileError(f"json_read_failed:{path}") from exc
    if not isinstance(document, dict):
        raise RelationCompileError(f"root_not_object:{path}")

    # The artifact must announce itself as the schema we were built against.
    schema_id = require(document, "schema", "glyph_fact_artifact")
    if schema_id != "fl4676.glyph_fact.v1":
        raise RelationCompileError(f"schema_mismatch:{schema_id}")

    # The count field and the actual list length must agree; a mismatch means the
    # file was hand-edited or truncated.
    declared_count = require_int(document, "glyph_record_count", "glyph_fact_artifact")
    records = require(document, "glyph_records", "glyph_fact_artifact")
    if not isinstance(records, list):
        raise RelationCompileError("malformed_record:glyph_records:not_an_array")
    if declared_count != len(records):
        raise RelationCompileError(
            f"admitted_universe_mismatch:glyph_record_count:{declared_count}!={len(records)}"
        )

    # The runtime ban must still be in force.  This is a guard against somebody
    # quietly adopting the rejected "Form B" while this compiler keeps running.
    rights = require(document, "runtime_rights", "glyph_fact_artifact")
    allowed = require(rights, "runtime_consumption_allowed", "glyph_fact_artifact.runtime_rights")
    if allowed is not False:
        raise RelationCompileError(
            "runtime_rights_amended:runtime_consumption_allowed_must_stay_false"
        )
    return document


def extract_records(document: dict[str, Any], universe_limit: int) -> list[dict[str, Any]]:
    """Pull out the GlyphRecord list, optionally shortened, in a fixed order.

    Ordering matters enormously: the tie-break rule for neighbour selection is
    "lowest glyph_record_id wins", and we implement that by keeping the records in
    ascending id order so that a stable sort on distance automatically breaks ties
    by id.  We therefore sort explicitly rather than trusting the input order, and
    we reject duplicate ids.

    Parameters
    ----------
    document:
        The parsed fl4676 artifact.
    universe_limit:
        0 means "use every record".  A positive number keeps only the first N
        records in ascending id order, which is how the test-suite runs a fast
        miniature compile.  The chosen limit is recorded in the manifest so a
        miniature run can never be mistaken for a full one.

    Returns
    -------
    The record dictionaries, ascending by glyph_record_id.
    """
    records = list(document["glyph_records"])
    seen: set[int] = set()
    keyed: list[tuple[int, dict[str, Any]]] = []
    for index, record in enumerate(records):
        record_id = require_int(record, "glyph_record_id", f"glyph_records[{index}]")
        if record_id in seen:
            raise RelationCompileError(f"duplicate_glyph_record_id:{record_id}")
        seen.add(record_id)
        keyed.append((record_id, record))
    keyed.sort(key=lambda pair: pair[0])
    ordered = [record for _record_id, record in keyed]
    if universe_limit > 0:
        ordered = ordered[:universe_limit]
    if len(ordered) < 2:
        raise RelationCompileError(f"empty_subset:universe_too_small:{len(ordered)}")
    return ordered


def mask_bits(hex_text: str, owner: str) -> list[int]:
    """Turn a 16-character little-endian hex mask into 64 zero-or-one pixel flags.

    Background: a glyph's coverage is summarised as an 8x8 grid of "is this cell
    inked?" answers, packed into a 64-bit number, and written in the artifact as
    16 hexadecimal characters in little-endian byte order (least significant byte
    first).  For distance purposes we want those 64 answers as 64 separate
    coordinates, because then "how many pixels differ" falls out of ordinary
    squared-difference arithmetic.

    Parameters
    ----------
    hex_text:
        Exactly 16 lowercase hex characters, as validated by the fl4676 schema.
    owner:
        Error-message location.

    Returns
    -------
    A list of 64 integers, each 0 or 1, bit 0 first.
    """
    if not isinstance(hex_text, str) or len(hex_text) != 16:
        raise RelationCompileError(f"malformed_record:{owner}:mask_not_16_hex")
    try:
        # int.from_bytes with "little" undoes the little-endian packing, giving
        # back the original 64-bit number.
        value = int.from_bytes(bytes.fromhex(hex_text), "little")
    except ValueError as exc:
        raise RelationCompileError(f"malformed_record:{owner}:mask_not_hex") from exc
    # (value >> bit) & 1 reads out bit number `bit`: shift it down to the bottom
    # position, then keep only that single bit.
    return [(value >> bit) & 1 for bit in range(64)]


def build_feature_matrix(records: list[dict[str, Any]]) -> tuple[np.ndarray, list[int]]:
    """Convert every GlyphRecord into one row of whole numbers for distance work.

    THE DISTANCE RULE, IN PLAIN LANGUAGE
    ------------------------------------
    Two glyphs are "close" when the numbers describing them are close.  We line up
    141 numbers per glyph, subtract them pairwise, square each difference (so that
    a difference of -5 counts the same as +5, and large differences count much
    more than small ones), multiply each squared difference by that family's
    importance weight, and add everything up.  Small total means similar glyphs.

    The 141 numbers, in order:
        [0..5]     morphology.shape6_norm_q0_16 -- six normalised outline
                   proportions, each 0..65535.  Weight 4 each.
        [6]        morphology.density_q0_16 -- how much of the cell is inked,
                   0..65535.  Weight 8, the heaviest single field, because ink
                   coverage is what a reader notices first.
        [7..10]    structure.beta0, beta1, endpoint_count, junction_count -- the
                   count of separate pieces, the count of enclosed holes, the
                   count of loose stroke ends, and the count of places strokes
                   meet.  Each is multiplied by 2048 to bring small counts onto
                   the same scale as the 0..65535 fields.  Weight 1 each.
        [11..12]   stroke_identity.orientation_q1_15 -- a two-number vector saying
                   which way the strokes lean, each -32767..32767.  Weight 2 each.
        [13..76]   connectivity.mask8_u64_le_hex expanded to 64 pixel flags, each
                   flag stored as 0 or 8192.  Weight 4 each.
        [77..140]  connectivity.skeleton_u64_le_hex expanded the same way, each
                   flag stored as 0 or 8192.  Weight 2 each -- lower than the
                   coverage mask because the skeleton is derived from it and would
                   otherwise double-count the same evidence.

    WHY EVERY VALUE IS A WHOLE NUMBER
    ---------------------------------
    Floating-point arithmetic can give slightly different answers on different
    processors.  Whole-number arithmetic cannot.  Because the neighbour lists and
    their fingerprints must match byte-for-byte on any machine, every value here
    is an integer and every distance is an exact integer.

    Parameters
    ----------
    records:
        GlyphRecords in ascending glyph_record_id order.

    Returns
    -------
    (matrix, record_ids) where ``matrix`` has one row per glyph and
    FEATURE_DIMENSIONS columns, stored as float64 purely so that the fast matrix
    multiply can be used -- every stored value is a whole number, and the
    arithmetic stays exact (see knn_distance_chunk for the proof of the bound).
    ``record_ids`` is the parallel list of glyph_record_id values.
    """
    count = len(records)
    matrix = np.zeros((count, FEATURE_DIMENSIONS), dtype=np.float64)
    record_ids: list[int] = []

    for row_index, record in enumerate(records):
        record_id = require_int(record, "glyph_record_id", f"glyph_records[{row_index}]")
        owner = f"glyph_record[{record_id}]"
        record_ids.append(record_id)

        # ---- Family A: the six normalised shape proportions ------------------
        morphology = require(record, "morphology", owner)
        shape6 = require(morphology, "shape6_norm_q0_16", f"{owner}.morphology")
        if not isinstance(shape6, list) or len(shape6) != DIM_SHAPE6:
            raise RelationCompileError(
                f"malformed_record:{owner}.morphology.shape6_norm_q0_16:expected_6_values"
            )
        for axis, value in enumerate(shape6):
            if isinstance(value, bool) or not isinstance(value, int):
                raise RelationCompileError(
                    f"malformed_record:{owner}.morphology.shape6_norm_q0_16[{axis}]:not_an_integer"
                )
            if not 0 <= value <= NORMALIZED_WEIGHT_SUM:
                raise RelationCompileError(
                    f"malformed_record:{owner}.morphology.shape6_norm_q0_16[{axis}]:out_of_range"
                )
            matrix[row_index, axis] = value

        # ---- Family B: ink coverage -----------------------------------------
        density = require_int(morphology, "density_q0_16", f"{owner}.morphology")
        if not 0 <= density <= NORMALIZED_WEIGHT_SUM:
            raise RelationCompileError(f"malformed_record:{owner}.morphology.density_q0_16:out_of_range")
        matrix[row_index, DIM_SHAPE6] = density

        # ---- Family C: topology counts (pieces, holes, ends, junctions) ------
        structure = require(record, "structure", owner)
        base = DIM_SHAPE6 + DIM_DENSITY
        for offset, field in enumerate(("beta0", "beta1", "endpoint_count", "junction_count")):
            value = require_int(structure, field, f"{owner}.structure")
            if value < 0:
                raise RelationCompileError(f"malformed_record:{owner}.structure.{field}:negative")
            if value > STRUCTURE_COUNT_MAX:
                # We refuse rather than clamp: clamping would be a silent
                # approximation, and the manifest promises there is none.
                raise RelationCompileError(
                    f"structure_count_out_of_range:{owner}.structure.{field}:{value}>{STRUCTURE_COUNT_MAX}"
                )
            matrix[row_index, base + offset] = value * STRUCTURE_STEP_SCALE

        # ---- Family D: stroke lean ------------------------------------------
        stroke = require(record, "stroke_identity", owner)
        orientation = require(stroke, "orientation_q1_15", f"{owner}.stroke_identity")
        if not isinstance(orientation, list) or len(orientation) != DIM_ORIENTATION:
            raise RelationCompileError(
                f"malformed_record:{owner}.stroke_identity.orientation_q1_15:expected_2_values"
            )
        base = DIM_SHAPE6 + DIM_DENSITY + DIM_STRUCTURE
        for axis, value in enumerate(orientation):
            if isinstance(value, bool) or not isinstance(value, int):
                raise RelationCompileError(
                    f"malformed_record:{owner}.stroke_identity.orientation_q1_15[{axis}]:not_an_integer"
                )
            if not -32767 <= value <= 32767:
                raise RelationCompileError(
                    f"malformed_record:{owner}.stroke_identity.orientation_q1_15[{axis}]:out_of_range"
                )
            matrix[row_index, base + axis] = value

        # ---- Family E and F: the two 64-pixel masks --------------------------
        connectivity = require(record, "connectivity", owner)
        mask_hex = require(connectivity, "mask8_u64_le_hex", f"{owner}.connectivity")
        skeleton_hex = require(connectivity, "skeleton_u64_le_hex", f"{owner}.connectivity")
        base = DIM_SHAPE6 + DIM_DENSITY + DIM_STRUCTURE + DIM_ORIENTATION
        for bit, flag in enumerate(mask_bits(mask_hex, f"{owner}.connectivity.mask8_u64_le_hex")):
            matrix[row_index, base + bit] = flag * BIT_SCALE
        base += DIM_MASK8
        for bit, flag in enumerate(
            mask_bits(skeleton_hex, f"{owner}.connectivity.skeleton_u64_le_hex")
        ):
            matrix[row_index, base + bit] = flag * BIT_SCALE

    return matrix, record_ids


def family_weight_vector() -> np.ndarray:
    """Build the 141-long list of per-coordinate importance multipliers.

    Returns
    -------
    A float64 array whose entry j is the whole-number weight applied to the
    squared difference of coordinate j.  Stored as float64 for the same reason as
    the feature matrix: the values are whole numbers, the storage type is only
    chosen so the fast matrix multiply can be used.
    """
    weights = np.empty(FEATURE_DIMENSIONS, dtype=np.float64)
    cursor = 0
    weights[cursor : cursor + DIM_SHAPE6] = WEIGHT_SHAPE6
    cursor += DIM_SHAPE6
    weights[cursor : cursor + DIM_DENSITY] = WEIGHT_DENSITY
    cursor += DIM_DENSITY
    weights[cursor : cursor + DIM_STRUCTURE] = WEIGHT_STRUCTURE
    cursor += DIM_STRUCTURE
    weights[cursor : cursor + DIM_ORIENTATION] = WEIGHT_ORIENTATION
    cursor += DIM_ORIENTATION
    weights[cursor : cursor + DIM_MASK8] = WEIGHT_MASK8
    cursor += DIM_MASK8
    weights[cursor : cursor + DIM_SKELETON] = WEIGHT_SKELETON
    cursor += DIM_SKELETON
    if cursor != FEATURE_DIMENSIONS:
        raise RelationCompileError(f"malformed_record:weight_vector:{cursor}!={FEATURE_DIMENSIONS}")
    return weights


def exact_distance(row_a: np.ndarray, row_b: np.ndarray, weights: np.ndarray) -> int:
    """Compute one glyph-to-glyph distance with pure Python whole-number maths.

    This is the slow, obviously-correct reference implementation.  It exists so
    that the fast matrix-multiply path can be spot-checked against it on every
    run: if the two ever disagree we stop instead of shipping wrong numbers.

    Parameters
    ----------
    row_a, row_b:
        Two feature rows (141 whole numbers each).
    weights:
        The 141 per-coordinate importance multipliers.

    Returns
    -------
    The exact weighted sum of squared differences, as a Python integer (Python
    integers have unlimited size, so there is no overflow to worry about).
    """
    total = 0
    for axis in range(FEATURE_DIMENSIONS):
        difference = int(row_a[axis]) - int(row_b[axis])
        total += int(weights[axis]) * difference * difference
    return total


def theoretical_max_distance(weights: np.ndarray) -> int:
    """Largest distance the rule could ever produce, used only as documentation.

    Each coordinate's worst case is its full span squared: 65535 for the shape and
    density fields, 31*2048 for the scaled counts, 65534 for the orientation
    fields (from -32767 to +32767), and 8192 for a pixel flag flipping.  The
    number this returns is written into the manifest so a reader can judge how
    large a reported distance is without having to re-derive the scale.
    """
    spans = (
        [NORMALIZED_WEIGHT_SUM] * DIM_SHAPE6
        + [NORMALIZED_WEIGHT_SUM] * DIM_DENSITY
        + [STRUCTURE_COUNT_MAX * STRUCTURE_STEP_SCALE] * DIM_STRUCTURE
        + [2 * 32767] * DIM_ORIENTATION
        + [BIT_SCALE] * DIM_MASK8
        + [BIT_SCALE] * DIM_SKELETON
    )
    return sum(int(weights[axis]) * span * span for axis, span in enumerate(spans))


# =============================================================================
# Section 3 -- the bounded k-nearest-neighbour computation
# =============================================================================


def knn_rows(
    matrix: np.ndarray,
    weights: np.ndarray,
    k: int,
) -> Iterator[tuple[int, np.ndarray, np.ndarray]]:
    """Yield, for each glyph in turn, its k closest other glyphs and their distances.

    HOW THE FAST PATH WORKS
    -----------------------
    Writing d(a,b) for the weighted squared distance, ordinary algebra gives

        d(a,b) = SUM_j w_j * (a_j - b_j)^2
               = SUM_j w_j*a_j^2  +  SUM_j w_j*b_j^2  -  2 * SUM_j a_j*(w_j*b_j)

    The first two terms are per-glyph constants ("norms") we compute once.  The
    third term is an ordinary matrix product between the raw feature rows and the
    weight-scaled feature rows, which the numerical library performs at hardware
    speed.  Comparing 111,040 glyphs against 111,040 glyphs is 1.2e10 pairs, far
    too many for a plain Python loop, and this reformulation is what brings the
    job down to a couple of minutes.

    WHY THE FLOATING-POINT FAST PATH IS STILL EXACT
    -----------------------------------------------
    Every stored feature is a whole number no larger than 65,535, every weighted
    feature no larger than 8 * 65,535 = 524,280, so every individual product is at
    most about 3.4e10, and a sum of 141 of them is at most about 4.8e12.  Double
    precision represents every whole number below 9.0e15 exactly, and a sum of
    exact whole numbers whose running total never leaves that range is itself
    exact no matter what order the hardware adds them in.  The bound is checked at
    runtime, and a random sample of pairs is re-derived with pure Python integer
    arithmetic on every run.

    HOW TIES ARE BROKEN
    -------------------
    Many glyphs are genuinely identical under this rule (all the blank cells, for
    instance).  A "pick the smallest k" routine is free to return tied items in
    any order, which would make the output differ between runs.  We therefore do
    it in two steps: first find the k-th smallest distance value, then take every
    glyph at or below that value -- which comes back in ascending index order,
    and index order is ascending glyph_record_id order -- and stable-sort that
    short list by distance.  A stable sort keeps equal-distance items in the order
    they arrived, so the final tie-break is exactly "lowest glyph_record_id wins".

    Parameters
    ----------
    matrix:
        Feature rows, one per glyph, ascending by glyph_record_id.
    weights:
        Per-coordinate importance multipliers.
    k:
        How many neighbours to keep per glyph.

    Yields
    ------
    (source_row_index, neighbour_row_indices, neighbour_distances) where the two
    arrays are already in final order: nearest first, ties by lowest id.
    """
    count = matrix.shape[0]
    if k < 1:
        raise RelationCompileError(f"capacity_exceeded:k_must_be_positive:{k}")
    # A glyph is never its own neighbour, so at most count-1 neighbours exist.
    effective_k = min(k, count - 1)

    # Weight-scaled copy of the features; the matrix product uses raw rows on one
    # side and weighted rows on the other so the weights land exactly once.
    weighted = matrix * weights
    # Per-glyph constant term SUM_j w_j * x_j^2, computed once for all glyphs.
    norms = np.einsum("ij,ij->i", matrix, weighted)

    # Safety net for the exactness argument above.
    largest_norm = float(np.max(np.abs(norms))) if count else 0.0
    if largest_norm * 3.0 >= FLOAT64_EXACT_INTEGER_LIMIT:
        raise RelationCompileError(
            f"exactness_bound_exceeded:norm={largest_norm}:limit={FLOAT64_EXACT_INTEGER_LIMIT}"
        )

    for start in range(0, count, CHUNK_ROWS):
        stop = min(start + CHUNK_ROWS, count)
        # gram[i, j] = SUM_c matrix[start+i, c] * weighted[j, c]
        gram = matrix[start:stop] @ weighted.T
        # distances[i, j] = d(glyph start+i, glyph j), still exact whole numbers.
        distances = norms[start:stop, None] + norms[None, :] - 2.0 * gram

        for offset in range(stop - start):
            source = start + offset
            row = distances[offset]
            # Exclude the glyph itself by pushing its own distance out of reach.
            self_distance = row[source]
            row[source] = np.inf

            # Step 1: what is the k-th smallest distance in this row?
            threshold = np.partition(row, effective_k - 1)[effective_k - 1]
            # Step 2: every glyph at or under that distance is a candidate.
            # flatnonzero returns positions in ascending order, i.e. ascending
            # glyph_record_id, which is exactly the tie-break we want to preserve.
            candidates = np.flatnonzero(row <= threshold)
            candidate_distances = row[candidates]
            # Step 3: stable sort by distance keeps the ascending-id order inside
            # each group of equal distances.
            order = np.argsort(candidate_distances, kind="stable")[:effective_k]
            chosen = candidates[order]
            chosen_distances = candidate_distances[order]

            # Put the glyph's own distance back so the row stays reusable and no
            # infinity leaks into any later arithmetic.
            row[source] = self_distance

            if chosen.shape[0] != effective_k:
                raise RelationCompileError(
                    f"capacity_exceeded:neighbor_shortfall:{source}:{chosen.shape[0]}!={effective_k}"
                )
            yield source, chosen, chosen_distances


def verify_distance_sample(
    matrix: np.ndarray,
    weights: np.ndarray,
    sample_pairs: list[tuple[int, int]],
) -> None:
    """Re-derive a handful of distances with plain Python integers and compare.

    This is the run-time proof that the fast floating-point path produced exact
    whole numbers.  The pairs are chosen deterministically by the caller (never
    randomly at run time) so this check itself cannot make two runs differ.

    Raises
    ------
    RelationCompileError
        Reason code ``inexact_distance`` on any disagreement.
    """
    weighted = matrix * weights
    norms = np.einsum("ij,ij->i", matrix, weighted)
    for left, right in sample_pairs:
        fast = norms[left] + norms[right] - 2.0 * float(matrix[left] @ weighted[right])
        slow = exact_distance(matrix[left], matrix[right], weights)
        if fast != float(slow):
            raise RelationCompileError(
                f"inexact_distance:{left}:{right}:fast={fast!r}:slow={slow!r}"
            )


def deterministic_sample_pairs(count: int, sample_size: int = 64) -> list[tuple[int, int]]:
    """Pick a fixed, reproducible spread of glyph pairs to spot-check.

    We want pairs scattered across the whole universe but chosen the same way on
    every machine, so we walk the index space with two co-prime strides rather
    than using a random number generator.

    Parameters
    ----------
    count:
        How many glyphs exist.
    sample_size:
        How many pairs to return.
    """
    if count < 2:
        return []
    pairs: list[tuple[int, int]] = []
    # 7919 and 104729 are primes; stepping by a prime that does not divide the
    # universe size visits a wide, evenly spread set of indices.
    for step in range(sample_size):
        left = (step * 7919) % count
        right = (step * 104729 + 1) % count
        if left == right:
            right = (right + 1) % count
        pairs.append((left, right))
    return pairs


# =============================================================================
# Section 4 -- ramp derivation (tonal and directional ladders)
# =============================================================================


def pseudo_angle(x: int, y: int) -> Fraction:
    """Turn a stroke-lean vector into an exact position on the turning circle.

    We need to sort glyphs by which way they lean.  The obvious tool would be the
    arctangent function, but that returns a floating-point number whose last digits
    can differ between maths libraries, which would make the compile
    non-reproducible.  Instead we use the "diamond angle", a well-known
    substitute that increases in exactly the same order as the true angle but is
    built only from addition, subtraction and one division of whole numbers -- so
    Python's exact Fraction type represents it with no error at all.

    The construction, with d = |x| + |y|:
        upper half (y >= 0):  value = 1 - x/d, giving 0 pointing right,
                              1 pointing up, 2 pointing left.
        lower half (y <  0):  value = 3 + x/d, giving 2 pointing left,
                              3 pointing down, and approaching 4 (= back to 0)
                              as the vector comes round to pointing right again.

    Parameters
    ----------
    x, y:
        The two components of stroke_identity.orientation_q1_15.

    Returns
    -------
    An exact Fraction in the half-open range [0, 4).

    Raises
    ------
    RelationCompileError
        If both components are zero, which means the glyph has no lean at all and
        therefore no position on the circle.  Callers must filter those out first.
    """
    span = abs(x) + abs(y)
    if span == 0:
        raise RelationCompileError("orientation_degenerate:zero_vector")
    if y >= 0:
        return Fraction(span - x, span)
    return Fraction(3 * span + x, span)


def direction_sector(x: int, y: int) -> int:
    """Bucket a stroke-lean vector into one of 32 equal sectors of the circle.

    The turning circle is 4 diamond-angle units around, so multiplying by 8 and
    taking the whole part gives 32 sectors numbered 0 to 31.  Fraction arithmetic
    keeps this exact, so the same vector always lands in the same sector.
    """
    scaled = pseudo_angle(x, y) * DIRECTION_SECTOR_COUNT / 4
    sector = int(scaled)  # Fraction -> int truncates toward zero; scaled >= 0.
    if not 0 <= sector < DIRECTION_SECTOR_COUNT:
        raise RelationCompileError(f"malformed_record:direction_sector_out_of_range:{sector}")
    return sector


def build_ramps(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], dict[str, int]]:
    """Derive tonal and directional ramps and each glyph's place in them.

    WHAT A RAMP IS
    --------------
    A ramp is an ordered ladder of glyphs where each rung differs from the last
    along one chosen axis.  A tonal ramp climbs in ink coverage (light to dark); a
    directional ramp turns steadily around the circle.

    HOW A RAMP IS BUILT (the same recipe for both families)
    ------------------------------------------------------
    1. Split all glyphs into BUCKETS by stroke_identity.mask_class -- "blank",
       "block", "branching", "loop", "horizontal" and so on.  Mixing structurally
       unlike glyphs into one ladder would make the ladder read as noise rather
       than as a smooth progression, so ladders never cross a bucket boundary.
    2. Inside a bucket, group glyphs into LEVELS by the ramp axis.  For a tonal
       ramp the level is the exact density value; for a directional ramp it is the
       32-way sector index.  Sort the levels into ascending order, and sort the
       glyphs inside each level by ascending glyph_record_id.
    3. If a bucket has more than 32 levels, cut the ascending level list into
       consecutive SEGMENTS of at most 32 levels, because a ramp may hold at most
       32 members (the same cap the accepted fl4676 fact schema uses).
    4. Deal the glyphs out like cards: ramp number 0 takes the first glyph of each
       level in the segment, ramp number 1 takes the second glyph of each level
       that has one, and so on.  Every ramp therefore has strictly increasing
       values along its axis, and every glyph lands in exactly one ramp per family.

    Levels that run out of glyphs simply contribute nothing to the later ramps, so
    ramps get shorter as the numbering rises.  A ramp of length one is still
    emitted (its single member carries the whole 65535 weight); the count of such
    degenerate ladders is reported so nobody mistakes them for real ladders.

    GLYPHS WITH NO DIRECTION
    ------------------------
    A glyph whose lean vector is (0, 0) -- a perfectly symmetric blob, or a blank
    cell -- has no position on the turning circle.  Such glyphs are excluded from
    the directional family and counted explicitly under
    ``directional_degenerate_orientation_excluded``.  They are NOT dropped
    silently, and they still appear in the tonal family.

    Parameters
    ----------
    records:
        GlyphRecords in ascending glyph_record_id order.

    Returns
    -------
    (ramps, memberships, diagnostics)
        ``ramps`` is the list of ramp definitions, each with its member list and
        fingerprint.  ``memberships`` maps glyph_record_id to the list of ramps it
        belongs to, with its rung number.  ``diagnostics`` holds the deterministic
        counts written into the pointer manifest.
    """
    # ---- Step 1 and 2: bucket and level the glyphs for both families ---------
    # Keys are (family, bucket_key, level_value); values are ascending id lists.
    tonal_levels: dict[tuple[str, int], list[int]] = defaultdict(list)
    directional_levels: dict[tuple[str, int], list[int]] = defaultdict(list)
    degenerate_orientation = 0

    for index, record in enumerate(records):
        record_id = require_int(record, "glyph_record_id", f"glyph_records[{index}]")
        owner = f"glyph_record[{record_id}]"
        stroke = require(record, "stroke_identity", owner)
        bucket = require(stroke, "mask_class", f"{owner}.stroke_identity")
        if not isinstance(bucket, str) or not bucket:
            raise RelationCompileError(f"malformed_record:{owner}.stroke_identity.mask_class")
        morphology = require(record, "morphology", owner)
        density = require_int(morphology, "density_q0_16", f"{owner}.morphology")

        # Tonal family: every glyph participates, level = its exact ink coverage.
        tonal_levels[(bucket, density)].append(record_id)

        # Directional family: only glyphs that actually lean somewhere.
        orientation = require(stroke, "orientation_q1_15", f"{owner}.stroke_identity")
        lean_x = int(orientation[0])
        lean_y = int(orientation[1])
        if lean_x == 0 and lean_y == 0:
            degenerate_orientation += 1
        else:
            directional_levels[(bucket, direction_sector(lean_x, lean_y))].append(record_id)

    ramps: list[dict[str, Any]] = []
    memberships: dict[int, list[dict[str, Any]]] = defaultdict(list)
    singleton_ramps = 0
    family_counts: dict[str, int] = {}
    family_members: dict[str, int] = {}

    for family, axis_name, levels in (
        ("tonal", "morphology.density_q0_16", tonal_levels),
        (
            "directional",
            "stroke_identity.orientation_q1_15.diamond_angle_sector_32",
            directional_levels,
        ),
    ):
        # Regroup: bucket -> ascending list of (level_value, ascending id list).
        by_bucket: dict[str, list[tuple[int, list[int]]]] = defaultdict(list)
        for (bucket, level_value), ids in levels.items():
            by_bucket[bucket].append((level_value, sorted(ids)))
        family_ramp_count = 0
        family_member_count = 0

        for bucket in sorted(by_bucket):
            ordered_levels = sorted(by_bucket[bucket], key=lambda pair: pair[0])
            # ---- Step 3: cut into segments of at most RAMP_MEMBER_CAP levels --
            for segment_index, segment_start in enumerate(
                range(0, len(ordered_levels), RAMP_MEMBER_CAP)
            ):
                segment = ordered_levels[segment_start : segment_start + RAMP_MEMBER_CAP]
                # The deepest level in this segment decides how many ramps we need.
                deepest = max(len(ids) for _level_value, ids in segment)
                # ---- Step 4: deal the glyphs out across ramps ----------------
                for rung_slot in range(deepest):
                    member_ids: list[int] = []
                    member_levels: list[int] = []
                    for level_value, ids in segment:
                        if rung_slot < len(ids):
                            member_ids.append(ids[rung_slot])
                            member_levels.append(level_value)
                    if not member_ids:
                        # Cannot happen (deepest guarantees at least one), but we
                        # refuse to emit an empty ramp rather than assume.
                        raise RelationCompileError(f"empty_subset:ramp:{family}:{bucket}")
                    if len(member_ids) > RAMP_MEMBER_CAP:
                        raise RelationCompileError(
                            f"capacity_exceeded:ramp_members:{family}:{bucket}:{len(member_ids)}"
                        )
                    if len(member_ids) == 1:
                        singleton_ramps += 1

                    # Weights: the accepted "descending_rank_largest_remainder.v1"
                    # algorithm, imported from the FL-4676 compiler so there is one
                    # owner.  It hands out descending whole-number shares that add
                    # up to exactly 65535, matching the ramp's rung order.
                    weights = normalized_member_weights(len(member_ids))
                    if sum(weights) != NORMALIZED_WEIGHT_SUM:
                        raise RelationCompileError("unnormalized_fact_weights:ramp")

                    members = [
                        {
                            "glyph_record_id": member_id,
                            "membership_weight_q0_16": weight,
                        }
                        for member_id, weight in zip(member_ids, weights, strict=True)
                    ]
                    body = {
                        "ramp_family": family,
                        "ramp_axis": axis_name,
                        "bucket_key": bucket,
                        "segment_index": segment_index,
                        "ramp_index": rung_slot,
                        "member_count": len(members),
                        "axis_levels": member_levels,
                        "glyph_members": members,
                        "subset_weight_sum_q0_16": NORMALIZED_WEIGHT_SUM,
                    }
                    ramp_id = f"glyphramp://fl4694/v1/{sha_value(body)[:16]}"
                    ramp = dict(body)
                    ramp["ramp_id"] = ramp_id
                    ramp["ramp_sha256"] = sha_value(body)
                    ramps.append(ramp)
                    family_ramp_count += 1
                    family_member_count += len(members)

                    for position, member_id in enumerate(member_ids):
                        memberships[member_id].append(
                            {
                                "ramp_id": ramp_id,
                                "ramp_family": family,
                                "ramp_position": position,
                            }
                        )

        family_counts[family] = family_ramp_count
        family_members[family] = family_member_count

    # Ramp identifiers must be unique; a collision would make the per-glyph index
    # ambiguous, so we stop rather than ship it.
    ramp_ids = [ramp["ramp_id"] for ramp in ramps]
    if len(ramp_ids) != len(set(ramp_ids)):
        raise RelationCompileError("duplicate_fact_id:ramp_id_collision")

    diagnostics = {
        "tonal_ramp_count": family_counts.get("tonal", 0),
        "tonal_member_count": family_members.get("tonal", 0),
        "directional_ramp_count": family_counts.get("directional", 0),
        "directional_member_count": family_members.get("directional", 0),
        "directional_degenerate_orientation_excluded": degenerate_orientation,
        "singleton_ramp_count": singleton_ramps,
        "ramp_member_cap": RAMP_MEMBER_CAP,
        "direction_sector_count": DIRECTION_SECTOR_COUNT,
    }
    # Every glyph must land in exactly one tonal ramp; that is the coverage
    # promise this compiler makes for the ramp-membership gap.
    if diagnostics["tonal_member_count"] != len(records):
        raise RelationCompileError(
            f"admitted_universe_mismatch:tonal_coverage:"
            f"{diagnostics['tonal_member_count']}!={len(records)}"
        )
    expected_directional = len(records) - degenerate_orientation
    if diagnostics["directional_member_count"] != expected_directional:
        raise RelationCompileError(
            f"admitted_universe_mismatch:directional_coverage:"
            f"{diagnostics['directional_member_count']}!={expected_directional}"
        )
    return ramps, memberships, diagnostics


# =============================================================================
# Section 5 -- writing the local (never committed) cache files
# =============================================================================


class JsonlWriter:
    """Write a JSON-Lines file while fingerprinting and measuring it in one pass.

    JSON Lines means "one complete JSON value per line".  It suits these caches
    because they are far too large to hold in memory as a single JSON document,
    and because a reader can stream them a record at a time.

    Fingerprinting as we write avoids a second full read of a 100+ MB file.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("wb")
        self._digest = hashlib.sha256()
        self._bytes = 0
        self._lines = 0

    def write(self, value: Any) -> None:
        """Append one canonical JSON value as a line."""
        payload = canonical(value) + b"\n"
        self._handle.write(payload)
        self._digest.update(payload)
        self._bytes += len(payload)
        self._lines += 1

    def close(self) -> dict[str, Any]:
        """Finish the file and return its pin: path, fingerprint, size, line count."""
        self._handle.close()
        return {
            "path": relative_to_root(self.path),
            "sha256": self._digest.hexdigest(),
            "byte_size": self._bytes,
            "line_count": self._lines,
        }


def write_knn_cache(
    path: Path,
    matrix: np.ndarray,
    record_ids: list[int],
    weights: np.ndarray,
    k: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Compute the neighbour lists and stream them into the local cache file.

    File layout: the first line is a header object describing the file; every
    later line describes one glyph::

        {"glyph_record_id": 16851837,
         "neighbors": [[to_glyph_record_id, compiled_distance, membership_weight_q0_16], ...]}

    The three numbers in each neighbour entry are:
      * to_glyph_record_id     -- which other glyph this edge points at.
      * compiled_distance      -- the exact whole-number distance from the rule in
                                  build_feature_matrix.  Smaller means more alike.
      * membership_weight_q0_16 -- the edge's share of this glyph's relation
                                  budget, as a 0..65535 fixed-point fraction.  The
                                  k weights of one glyph add up to exactly 65535.
                                  The values come from the accepted
                                  "descending_rank_largest_remainder.v1" algorithm
                                  applied to the neighbours in nearest-first order,
                                  so the nearest neighbour always carries the
                                  largest share.

    Returns
    -------
    (pin, diagnostics) where ``pin`` is the fingerprint/size record for the
    manifest and ``diagnostics`` holds deterministic summary counts.
    """
    count = len(record_ids)
    effective_k = min(k, count - 1)
    # Weight shares depend only on how many neighbours there are, so compute once.
    share_weights = normalized_member_weights(effective_k)
    if sum(share_weights) != NORMALIZED_WEIGHT_SUM:
        raise RelationCompileError("unnormalized_fact_weights:knn")

    writer = JsonlWriter(path)
    writer.write(
        {
            "schema": KNN_SCHEMA_ID,
            "version": 1,
            "artifact_kind": "local_only_bounded_glyph_relation_cache",
            "record_count": count,
            "k": effective_k,
            "k_requested": k,
            "distance_definition_id": DISTANCE_DEFINITION_ID,
            "neighbor_tuple_fields": [
                "to_glyph_record_id",
                "compiled_distance",
                "membership_weight_q0_16",
            ],
            "neighbor_order": "ascending_compiled_distance_then_ascending_glyph_record_id",
            "self_edges_excluded": True,
            "weight_algorithm": "descending_rank_largest_remainder.v1",
            "weight_sum_per_record_q0_16": NORMALIZED_WEIGHT_SUM,
            "runtime_consumption_allowed": False,
        }
    )

    zero_distance_edges = 0
    max_distance = 0
    max_first_neighbor_distance = 0
    for source, neighbours, distances in knn_rows(matrix, weights, k):
        edges: list[list[int]] = []
        for rank in range(effective_k):
            distance_value = distances[rank]
            # The distance is an exact whole number held in a float64; convert it
            # back to a Python integer and refuse anything fractional.
            distance_int = int(distance_value)
            if float(distance_int) != float(distance_value):
                raise RelationCompileError(
                    f"inexact_distance:{record_ids[source]}:{distance_value!r}"
                )
            if distance_int < 0:
                raise RelationCompileError(
                    f"malformed_record:negative_distance:{record_ids[source]}:{distance_int}"
                )
            if distance_int == 0:
                zero_distance_edges += 1
            max_distance = max(max_distance, distance_int)
            if rank == 0:
                max_first_neighbor_distance = max(max_first_neighbor_distance, distance_int)
            edges.append(
                [int(record_ids[int(neighbours[rank])]), distance_int, share_weights[rank]]
            )
        targets = {edge[0] for edge in edges}
        if len(targets) != effective_k:
            raise RelationCompileError(f"duplicate_glyph_record_id:knn_target:{record_ids[source]}")
        if record_ids[source] in targets:
            raise RelationCompileError(f"relation_scope_violation:self_edge:{record_ids[source]}")
        writer.write({"glyph_record_id": record_ids[source], "neighbors": edges})

    pin = writer.close()
    diagnostics = {
        "knn_record_count": count,
        "knn_edge_count": count * effective_k,
        "knn_zero_distance_edge_count": zero_distance_edges,
        "knn_max_compiled_distance": max_distance,
        "knn_max_nearest_neighbor_distance": max_first_neighbor_distance,
    }
    return pin, diagnostics


def write_ramp_cache(path: Path, ramps: list[dict[str, Any]]) -> dict[str, Any]:
    """Stream the ramp definitions into the local ramp cache file.

    Layout: header line, then one line per ramp.  Each ramp line carries its
    identifier, family, bucket, the axis value of every rung (``axis_levels``) and
    the member list in fl4676 member shape -- ``glyph_record_id`` plus
    ``membership_weight_q0_16`` -- whose weights add up to exactly 65535.
    """
    writer = JsonlWriter(path)
    writer.write(
        {
            "schema": RAMP_SCHEMA_ID,
            "version": 1,
            "artifact_kind": "local_only_glyph_ramp_membership_cache",
            "ramp_count": len(ramps),
            "ramp_families": ["directional", "tonal"],
            "bucket_axis": "stroke_identity.mask_class",
            "member_shape": ["glyph_record_id", "membership_weight_q0_16"],
            "member_order": "ascending_ramp_axis_level",
            "weight_algorithm": "descending_rank_largest_remainder.v1",
            "weight_sum_per_ramp_q0_16": NORMALIZED_WEIGHT_SUM,
            "member_cap": RAMP_MEMBER_CAP,
            "runtime_consumption_allowed": False,
        }
    )
    for ramp in ramps:
        writer.write(ramp)
    return writer.close()


def write_ramp_index_cache(
    path: Path,
    record_ids: list[int],
    memberships: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Stream the per-glyph ramp membership index into its local cache file.

    This is the direct answer to the measured gap "per-record membership in
    tonal/directional ramps is absent": one line per glyph, listing every ramp it
    belongs to and its rung number inside that ramp.

    Layout: header line, then one line per glyph::

        {"glyph_record_id": 16851837,
         "ramp_memberships": [{"ramp_id": "...", "ramp_family": "tonal",
                               "ramp_position": 4}, ...]}
    """
    writer = JsonlWriter(path)
    writer.write(
        {
            "schema": RAMP_INDEX_SCHEMA_ID,
            "version": 1,
            "artifact_kind": "local_only_per_record_ramp_membership_index",
            "record_count": len(record_ids),
            "membership_fields": ["ramp_id", "ramp_family", "ramp_position"],
            "runtime_consumption_allowed": False,
        }
    )
    for record_id in record_ids:
        entries = memberships.get(record_id, [])
        if not entries:
            # Every glyph is in at least its tonal ramp; an empty list would mean
            # the ramp builder lost a record, so we stop rather than emit it.
            raise RelationCompileError(f"required_attribute_missing:ramp_membership:{record_id}")
        # Sort so the line is identical no matter what order the ramps were built.
        ordered = sorted(entries, key=lambda item: (item["ramp_family"], item["ramp_id"]))
        writer.write({"glyph_record_id": record_id, "ramp_memberships": ordered})
    return writer.close()


# =============================================================================
# Section 6 -- the committed pointer manifest
# =============================================================================


def build_source_pins(glyph_fact_path: Path) -> list[dict[str, str]]:
    """Fingerprint every input this compile depended on.

    A pointer manifest is only trustworthy if it says exactly what went in.  These
    pins let another machine confirm it is regenerating from identical inputs
    before it compares the output fingerprints.

    The pinned inputs are: the unified glyph record artifact itself, the schema
    that artifact validates against, the FL-4676 compiler revision that produced
    it (its own source text, which is also where the shared weight-normalisation
    algorithm lives), this compiler's source text, and the schema of the manifest
    being written.
    """
    pins = {
        "fl4676_glyph_fact_artifact": glyph_fact_path,
        "fl4676_glyph_fact_schema": FL4676_SCHEMA_PATH,
        "fl4676_compiler_script": FL4676_COMPILER_PATH,
        "fl4694_compiler_script": SELF_PATH,
        "fl4694_relation_pointer_schema": POINTER_SCHEMA_PATH,
    }
    return [
        {"name": name, "path": relative_to_root(path), "sha256": sha_file(path)}
        for name, path in sorted(pins.items())
    ]


def build_manifest(
    glyph_fact_document: dict[str, Any],
    glyph_fact_path: Path,
    k: int,
    universe_limit: int,
    weights: np.ndarray,
    local_pins: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the small JSON file the repository actually commits.

    IMPORTANT: nothing time-varying may appear here.  No timestamps, no measured
    durations, no absolute paths, no host names.  The manifest has to come out
    byte-identical from an identical compile, otherwise "regenerate and compare"
    stops being a usable check.  Measured wall-clock timings are reported to the
    operator on standard output instead, and never stored.

    Parameters
    ----------
    glyph_fact_document:
        The parsed fl4676 artifact, used to echo its declared identity.
    glyph_fact_path:
        Where that artifact was read from.
    k:
        Requested neighbour breadth.
    universe_limit:
        0 for a full compile, otherwise the miniature-run size.
    weights:
        Per-coordinate importance multipliers, echoed so the distance rule is
        fully specified by the manifest alone.
    local_pins:
        Fingerprint/size records for the never-committed cache files.
    diagnostics:
        Deterministic summary counts from the two compile stages.
    """
    identity = glyph_fact_document.get("artifact_identity", {})
    body: dict[str, Any] = {
        "schema": POINTER_SCHEMA_ID,
        "version": 1,
        "artifact_kind": "local_only_relation_and_ramp_cache_pointer",
        "form": "A_LOCAL",
        "form_rationale": (
            "A dense relation table over 111040 glyph records would need about "
            "1.6 TB, so the compiled relation family is bounded to k nearest "
            "neighbours per record and kept in a never-committed local cache. "
            "The repository commits only these pins plus the regeneration command."
        ),
        # ---- What the live game may and may not do with any of this ----------
        # Restated from the accepted fl4676 artifact.  This manifest never amends
        # the prohibition; the rejected alternative would have let the runtime
        # derive relations for itself, and it stays rejected.
        "runtime_rights": {
            "runtime_consumption_allowed": False,
            "blocked_by": "FL-4683",
            "derivation_owner": "offline_compiler",
            "forbidden_authorities": FORBIDDEN_RUNTIME_AUTHORITIES,
            "source_schema_amended": False,
        },
        # ---- The exact rule used to decide which glyphs are related ----------
        "relation_contract": {
            "distance_definition_id": DISTANCE_DEFINITION_ID,
            "k": k,
            "k_revisable": True,
            "k_choice_rationale": (
                "32 matches the per-glyph relation breadth already accepted in "
                "fl4512.glyph_transform_relation.v2 and the max_edges_per_fact "
                "bound of the fl4676 schema. It is a revisable parameter."
            ),
            "distance_semantics": (
                "Weighted sum of squared differences over integer per-record "
                "attributes. Lower means more similar. Every term is an exact "
                "whole number, so the ordering is identical on every machine."
            ),
            "feature_dimensions": FEATURE_DIMENSIONS,
            "feature_layout": [
                {
                    "attribute": "morphology.shape6_norm_q0_16",
                    "dimensions": DIM_SHAPE6,
                    "weight": WEIGHT_SHAPE6,
                    "scale": 1,
                },
                {
                    "attribute": "morphology.density_q0_16",
                    "dimensions": DIM_DENSITY,
                    "weight": WEIGHT_DENSITY,
                    "scale": 1,
                },
                {
                    "attribute": "structure.beta0|beta1|endpoint_count|junction_count",
                    "dimensions": DIM_STRUCTURE,
                    "weight": WEIGHT_STRUCTURE,
                    "scale": STRUCTURE_STEP_SCALE,
                },
                {
                    "attribute": "stroke_identity.orientation_q1_15",
                    "dimensions": DIM_ORIENTATION,
                    "weight": WEIGHT_ORIENTATION,
                    "scale": 1,
                },
                {
                    "attribute": "connectivity.mask8_u64_le_hex",
                    "dimensions": DIM_MASK8,
                    "weight": WEIGHT_MASK8,
                    "scale": BIT_SCALE,
                },
                {
                    "attribute": "connectivity.skeleton_u64_le_hex",
                    "dimensions": DIM_SKELETON,
                    "weight": WEIGHT_SKELETON,
                    "scale": BIT_SCALE,
                },
            ],
            "theoretical_max_distance": theoretical_max_distance(weights),
            "tie_break": "ascending_glyph_record_id",
            "self_edges_excluded": True,
            "pruning": "none",
            "exactness": "exact_over_full_admitted_universe",
            "approximation": False,
            "weight_encoding": "q0_16",
            "weight_algorithm": "descending_rank_largest_remainder.v1",
            "weight_sum_per_record_q0_16": NORMALIZED_WEIGHT_SUM,
            "fail_closed_on_missing_attribute": True,
        },
        # ---- The rule used to build the tonal and directional ladders ---------
        "ramp_contract": {
            "families": ["directional", "tonal"],
            "bucket_axis": "stroke_identity.mask_class",
            "tonal_axis": "morphology.density_q0_16",
            "directional_axis": "stroke_identity.orientation_q1_15.diamond_angle_sector_32",
            "direction_sector_count": DIRECTION_SECTOR_COUNT,
            "member_cap": RAMP_MEMBER_CAP,
            "member_shape": ["glyph_record_id", "membership_weight_q0_16"],
            "member_order": "ascending_ramp_axis_level",
            "weight_algorithm": "descending_rank_largest_remainder.v1",
            "weight_sum_per_ramp_q0_16": NORMALIZED_WEIGHT_SUM,
            "tie_break": "ascending_glyph_record_id",
            "tonal_coverage": "every_admitted_record_in_exactly_one_tonal_ramp",
            "directional_coverage": (
                "every_admitted_record_with_non_zero_orientation_in_exactly_one_"
                "directional_ramp"
            ),
            "fail_closed_on_missing_attribute": True,
        },
        # ---- Which inputs produced this, byte for byte ------------------------
        "source_pins": build_source_pins(glyph_fact_path),
        "glyph_universe": {
            "source_artifact_schema": glyph_fact_document.get("schema"),
            "declared_glyph_record_count": glyph_fact_document.get("glyph_record_count"),
            "compiled_record_count": diagnostics["knn_record_count"],
            "universe_limit": universe_limit,
            "source_artifact_id": identity.get("artifact_id"),
            "source_canonical_content_sha256": glyph_fact_document.get("canonical_content_sha256"),
            "source_input_set_sha256": identity.get("input_set_sha256"),
            "source_compiler_script_sha256": identity.get("compiler_script_sha256"),
        },
        # ---- Where the never-committed cache lives and how to rebuild it ------
        "local_artifacts": local_pins,
        "local_artifact_policy": {
            "committed": False,
            "gitignored_directory": "assets/glyphs/generated/local/",
            "reason": "regenerable_from_pinned_inputs_and_too_large_to_commit",
        },
        "regeneration": {
            "command": REGENERATION_COMMAND,
            "working_directory": ".",
            "verify_command": "python3 scripts/compile_fl4694_glyph_relations.py --verify",
            "recheck_command": "python3 scripts/compile_fl4694_glyph_relations.py --check",
            "deterministic": True,
            "determinism_basis": (
                "integer-only feature values, integer-only distance arithmetic "
                "held exactly in float64, explicit ascending glyph_record_id "
                "tie-break, canonical sorted-key JSON serialisation"
            ),
        },
        "compile_diagnostics": diagnostics,
        "manifest_sha256": "0" * 64,
    }
    # Self-fingerprint: hash the manifest with the placeholder removed, then store
    # the result.  A reader recomputes it the same way to detect hand edits.
    unhashed = dict(body)
    unhashed.pop("manifest_sha256")
    body["manifest_sha256"] = sha_value(unhashed)
    return body


# =============================================================================
# Section 7 -- the top-level compile and the command-line entry point
# =============================================================================


def compile_all(
    glyph_fact_path: Path,
    output_dir: Path,
    k: int,
    universe_limit: int,
) -> dict[str, Any]:
    """Run every stage and return the finished pointer manifest.

    Parameters
    ----------
    glyph_fact_path:
        Where to read the unified glyph record artifact from.
    output_dir:
        Directory that receives the three never-committed cache files.
    k:
        Requested neighbour breadth.
    universe_limit:
        0 for the full universe; a positive number for a fast miniature compile.
    """
    document = load_glyph_fact_artifact(glyph_fact_path)
    records = extract_records(document, universe_limit)
    matrix, record_ids = build_feature_matrix(records)
    weights = family_weight_vector()

    # Prove on every single run that the fast path is producing exact integers.
    verify_distance_sample(matrix, weights, deterministic_sample_pairs(len(record_ids)))

    knn_pin, knn_diagnostics = write_knn_cache(
        output_dir / "fl4694.glyph_relation_knn.v1.jsonl", matrix, record_ids, weights, k
    )
    knn_pin["name"] = "glyph_relation_knn"
    knn_pin["schema"] = KNN_SCHEMA_ID

    ramps, memberships, ramp_diagnostics = build_ramps(records)
    ramp_pin = write_ramp_cache(output_dir / "fl4694.glyph_ramp.v1.jsonl", ramps)
    ramp_pin["name"] = "glyph_ramp"
    ramp_pin["schema"] = RAMP_SCHEMA_ID

    index_pin = write_ramp_index_cache(
        output_dir / "fl4694.ramp_record_index.v1.jsonl", record_ids, memberships
    )
    index_pin["name"] = "ramp_record_index"
    index_pin["schema"] = RAMP_INDEX_SCHEMA_ID

    diagnostics: dict[str, Any] = {}
    diagnostics.update(knn_diagnostics)
    diagnostics.update(ramp_diagnostics)

    return build_manifest(
        document,
        glyph_fact_path,
        k,
        universe_limit,
        weights,
        [knn_pin, ramp_pin, index_pin],
        diagnostics,
    )


def write_manifest(path: Path, manifest: dict[str, Any]) -> str:
    """Write the pointer manifest in canonical form and return the file's fingerprint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(manifest) + b"\n"
    path.write_bytes(payload)
    return sha_bytes(payload)


def verify_local_against_manifest(manifest_path: Path) -> int:
    """Fast check: do the cache files on disk still match the committed pins?

    This does not recompute anything.  It re-reads the manifest, re-fingerprints
    each named cache file, and reports the first disagreement.  It is the cheap
    everyday check; ``--check`` is the expensive proof that a fresh compile still
    reproduces the pins.

    Returns
    -------
    A process exit status: 0 on agreement, 1 on any mismatch or missing file.
    """
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"FL-4694 verify failed: manifest_unreadable:{manifest_path} {exc}", file=sys.stderr)
        return 1

    # Confirm the manifest has not been hand-edited since it was written.
    unhashed = dict(manifest)
    declared = unhashed.pop("manifest_sha256", None)
    if sha_value(unhashed) != declared:
        print("FL-4694 verify failed: manifest_sha256_mismatch", file=sys.stderr)
        return 1

    status = 0
    for pin in manifest.get("local_artifacts", []):
        path = ROOT / pin["path"]
        if not path.exists():
            print(
                f"FL-4694 verify failed: local_artifact_missing:{pin['path']} "
                f"(regenerate with: {REGENERATION_COMMAND})",
                file=sys.stderr,
            )
            status = 1
            continue
        actual_sha = sha_file(path)
        actual_size = path.stat().st_size
        if actual_sha != pin["sha256"] or actual_size != pin["byte_size"]:
            print(
                f"FL-4694 verify failed: local_artifact_mismatch:{pin['path']} "
                f"expected_sha256={pin['sha256']} actual_sha256={actual_sha} "
                f"expected_bytes={pin['byte_size']} actual_bytes={actual_size}",
                file=sys.stderr,
            )
            status = 1
            continue
        print(f"ok {pin['path']} sha256={actual_sha} bytes={actual_size}")
    return status


def main() -> int:
    """Parse the command line, run the requested mode, and report to the operator."""
    parser = argparse.ArgumentParser(
        description=(
            "Compile the FL-4694 Form A-LOCAL bounded glyph relation cache and "
            "ramp membership cache, plus the committed pointer manifest."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--write",
        action="store_true",
        help="recompute the local caches and rewrite the pointer manifest",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help=(
            "recompute the local caches into --output-dir and compare the freshly "
            "derived manifest against the committed one (slow: full recompile)"
        ),
    )
    mode.add_argument(
        "--verify",
        action="store_true",
        help="re-fingerprint the existing local caches against the committed manifest (fast)",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=DEFAULT_K,
        help=f"neighbours kept per glyph record (default {DEFAULT_K})",
    )
    parser.add_argument(
        "--glyph-fact-artifact",
        type=Path,
        default=DEFAULT_GLYPH_FACT_PATH,
        help="path to the unified fl4676 glyph record artifact",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_LOCAL_DIR,
        help="directory that receives the never-committed cache files",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="path of the committed pointer manifest",
    )
    parser.add_argument(
        "--universe-limit",
        type=int,
        default=0,
        help="0 for the full universe; a positive number runs a fast miniature compile",
    )
    args = parser.parse_args()

    try:
        if args.verify:
            return verify_local_against_manifest(args.manifest_path)

        # Both --write and --check perform the full compile; they differ only in
        # what they do with the resulting manifest.
        started = time.monotonic()
        manifest = compile_all(
            args.glyph_fact_artifact.resolve(),
            args.output_dir.resolve(),
            args.k,
            args.universe_limit,
        )
        elapsed = time.monotonic() - started

        if args.write:
            file_sha256 = write_manifest(args.manifest_path, manifest)
            print(
                f"wrote {relative_to_root(args.manifest_path)} "
                f"manifest_sha256={manifest['manifest_sha256']} "
                f"manifest_file_sha256={file_sha256}"
            )
            for pin in manifest["local_artifacts"]:
                print(
                    f"  local {pin['name']} path={pin['path']} sha256={pin['sha256']} "
                    f"bytes={pin['byte_size']} lines={pin['line_count']}"
                )
            diagnostics = manifest["compile_diagnostics"]
            print(
                f"  records={diagnostics['knn_record_count']} k={manifest['relation_contract']['k']} "
                f"edges={diagnostics['knn_edge_count']} "
                f"tonal_ramps={diagnostics['tonal_ramp_count']} "
                f"directional_ramps={diagnostics['directional_ramp_count']}"
            )
            # Timing is reported but never stored, so the manifest stays stable.
            print(f"  compile_seconds={elapsed:.1f}")
            return 0

        # --check: compare the freshly computed manifest with the committed one.
        try:
            committed = json.loads(args.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            print(f"FL-4694 check failed: manifest_unreadable {exc}", file=sys.stderr)
            return 1
        if committed != manifest:
            print(f"{relative_to_root(args.manifest_path)} is stale", file=sys.stderr)
            print(f"expected_manifest_sha256={manifest['manifest_sha256']}", file=sys.stderr)
            print(
                f"actual_manifest_sha256={committed.get('manifest_sha256')}",
                file=sys.stderr,
            )
            return 1
        print(
            f"ok {relative_to_root(args.manifest_path)} "
            f"manifest_sha256={manifest['manifest_sha256']} "
            f"compile_seconds={elapsed:.1f}"
        )
        return 0
    except RelationCompileError as exc:
        print(f"FL-4694 compile failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
