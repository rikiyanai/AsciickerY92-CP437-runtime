#!/usr/bin/env python3
"""Compile server-reachable ActorVisualProfile rows into current artifacts.

The production rows are derived from the server reachability contract and the
upstream sprite resolver/load semantics. No authored profile JSON participates
in this track.

FL-4131/FL-4049 Stage A/B governance (added 2026-05):
  - Every _load_xp() call invokes _assert_glyphs_admitted_for_source(), which
    fails closed on any cell glyph > 255 unless the source XP has an admitted
    glyph manifest hash. Admission is driven by
    assets/glyphs/admission_allowlist.json plus a valid .glyph_profile.json
    sidecar whose manifest hash verifies against the referenced manifest.
  - _emit_generated_header() emits free-standing top-level constants
    ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256 and
    ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID. Runtime JoinV2 advertises both so
    web/native/server reject mismatched authored glyph content instead of
    treating the content-pack identity as an empty compatibility field.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any

from compile_glyph_manifest import sha256_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATED_HEADER = REPO_ROOT / "engine" / "actor_visual_profile_table.generated.h"
MATERIAL_GLYPH_MANIFEST = REPO_ROOT / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"
SERVER_IDENTITY = REPO_ROOT / "server" / "actor_visual_reachability_identity.generated.h"
SERVER_REACHABILITY = (
    REPO_ROOT / "assets/actor_visual_profiles/current/server_reachable_keys.json"
)
PROFILE_BINDINGS = (
    REPO_ROOT / "assets/actor_visual_profiles/source/profile_bindings.json"
)
CUSTOM_SOURCE_CONTRACT = (
    REPO_ROOT / "assets/actor_visual_profiles/source/custom_source_contract.json"
)
GENERATED_PROVENANCE = REPO_ROOT / "assets" / "actor_visual_profiles" / "current" / "actor_visual_profile_provenance.json"
ADMISSION_ALLOWLIST = REPO_ROOT / "assets" / "glyphs" / "admission_allowlist.json"
TRANSPARENT = 255
SWOOSH = 254
CYAN = (0, 255, 255)
MAGENTA = (255, 0, 255)
CROSSBOW_ITEM_ID = 400 + 17
PINNED_UPSTREAM_COMMIT = "8ff75d0c5a8d2745a8ad6a8a841dd31a46e81635"
SKIN_NORMAL_PLAYER = 101
ACTOR_STYLE_DEFAULT = 0
PRESENTATION_IDLE_WALK = 600
PRESENTATION_ATTACK = 601
PRESENTATION_DEATH = 602
LOCOMOTION_NONE = 0
LOCOMOTION_IDLE = 1
LOCOMOTION_MOVING = 2
LOCOMOTION_AIRBORNE = 3
VARIATION_DEFAULT = 0
MOUNT_NONE = 0
MOUNT_WOLF = 950
MOUNT_BEE = 951
RIG_DEFAULT = 0
RIG_MOUNTED_RIDER_SEAM = 1
ITEM_SHIELD = 402
ITEM_SWORD = 409
ITEM_HELMET = 410
ITEM_ARMOUR = 411
STYLE_DEFAULT = 500
SLOT_HEAD = 301
SLOT_SHIELD = 302
SLOT_WEAPON = 303
SLOT_ARMOR = 306
GLYPH_COVERAGE = [
    0x0000,0x2222,0x4433,0x3412,0x2312,0x2323,0x2312,0x1111,0x3333,0x1111,0x3333,0x4122,0x2222,0x2203,0x3322,0x3322,
    0x1212,0x2121,0x2222,0x2211,0x3321,0x2222,0x0022,0x2233,0x2211,0x1122,0x2121,0x1212,0x0111,0x2222,0x1122,0x2211,
    0x0000,0x2211,0x1100,0x2322,0x2211,0x1112,0x2222,0x1100,0x0201,0x1201,0x2211,0x1111,0x0011,0x1100,0x0011,0x2102,
    0x3222,0x1211,0x2112,0x2121,0x2221,0x2221,0x1222,0x2101,0x2222,0x2211,0x1111,0x1111,0x1111,0x1111,0x1101,0x2111,
    0x3212,0x2222,0x2322,0x1212,0x2322,0x2312,0x2302,0x1222,0x2222,0x1111,0x2012,0x2322,0x0322,0x3322,0x2322,0x2212,
    0x2302,0x2221,0x2312,0x2221,0x2211,0x2222,0x2211,0x2222,0x2222,0x2211,0x2322,0x1212,0x0320,0x2121,0x1200,0x0011,
    0x1100,0x1122,0x1322,0x1112,0x2122,0x1112,0x1202,0x1122,0x1322,0x1111,0x2121,0x1212,0x1111,0x1222,0x1122,0x1112,
    0x1113,0x1122,0x1112,0x1121,0x1211,0x1122,0x1111,0x1122,0x1112,0x1122,0x1112,0x1211,0x1111,0x1111,0x1200,0x1122,
    0x1212,0x1122,0x2112,0x2222,0x1122,0x1222,0x2222,0x1212,0x2212,0x2212,0x1212,0x1111,0x2211,0x1211,0x2222,0x1122,
    0x1212,0x1122,0x3322,0x2222,0x1122,0x1122,0x1222,0x1222,0x1122,0x2222,0x2222,0x1212,0x1312,0x2222,0x1221,0x3112,
    0x1222,0x1111,0x2122,0x1122,0x1322,0x2222,0x2211,0x2211,0x1112,0x1101,0x1110,0x2232,0x2232,0x1122,0x2211,0x2211,
    0x1111,0x2222,0x3333,0x1111,0x1212,0x1313,0x2222,0x1123,0x1213,0x2222,0x2222,0x2222,0x2222,0x2311,0x1312,0x0112,
    0x2110,0x2211,0x1122,0x2121,0x1111,0x2222,0x3131,0x2222,0x2222,0x2222,0x2222,0x2222,0x2222,0x2222,0x2222,0x2222,
    0x3311,0x2222,0x0033,0x3211,0x3121,0x2131,0x1132,0x3333,0x3333,0x1201,0x1021,0x4444,0x0044,0x0404,0x4040,0x4400,
    0x1212,0x2212,0x1201,0x2222,0x1212,0x1112,0x1112,0x1211,0x2222,0x2212,0x2222,0x1222,0x1212,0x2213,0x1211,0x2222,
    0x2211,0x1312,0x0212,0x0211,0x1202,0x2012,0x1111,0x1212,0x2200,0x0000,0x0000,0x2011,0x2200,0x2100,0x2222,0x1111,
]
SOURCE_KIND_ENUM = {
	"UPSTREAM_AUTHORED": "ACTOR_VISUAL_SOURCE_XP_KIND_UPSTREAM_AUTHORED",
	"DERIVED_SINGLEROLE": "ACTOR_VISUAL_SOURCE_XP_KIND_DERIVED_SINGLEROLE",
	"PIPELINE_DECOMPOSED": "ACTOR_VISUAL_SOURCE_XP_KIND_PIPELINE_DECOMPOSED",
	"VERIFIED_STATE_LAYER": "ACTOR_VISUAL_SOURCE_XP_KIND_VERIFIED_STATE_LAYER",
}
RENDER_OPERATION_BITS = {
    "define_height_channel": 1 << 0,
    "define_per_cell_color_key_and_frame_metadata": 1 << 1,
    "seed_l2_base_accumulator": 1 << 2,
    "ordinal_overlay_merge_into_l2": 1 << 3,
    "final_cyan_swoosh_context_composite": 1 << 4,
    "no_visual_contribution": 1 << 5,
}
PLAYBACK_ENUM = {
    "loop": "ACTOR_VISUAL_PLAYBACK_DIRECTION_LOOP",
    "forward_clamp": "ACTOR_VISUAL_PLAYBACK_DIRECTION_FORWARD_CLAMP",
    "clamp": "ACTOR_VISUAL_PLAYBACK_DIRECTION_FORWARD_CLAMP",
    "reverse_clamp": "ACTOR_VISUAL_PLAYBACK_DIRECTION_REVERSE_CLAMP",
}


def _fail(message: str) -> None:
    raise SystemExit(f"compile_actor_visual_profiles: {message}")


def _repo_rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        _fail(f"missing source file: {_repo_rel(path)}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"invalid JSON in {_repo_rel(path)}: {exc}")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


UPSTREAM_CONTRACT_DIR = (
    REPO_ROOT / "docs/research/ascii/semantic_maps/upstream_xp_cell_contract"
)
UPSTREAM_CONTRACT_FREEZE = UPSTREAM_CONTRACT_DIR / "family_contract_freeze.json"
UPSTREAM_CONTRACT_CUTOVER = UPSTREAM_CONTRACT_DIR / "compiler_cutover.json"
UPSTREAM_CONTRACT_QUEUE = UPSTREAM_CONTRACT_DIR / "review_queue.json"
UPSTREAM_CONTRACT_DECISIONS = UPSTREAM_CONTRACT_DIR / "cell_role_decisions.jsonl"
UPSTREAM_CONTRACT_REVIEW_STATES = (
    UPSTREAM_CONTRACT_DIR / "cell_review_state_decisions.jsonl"
)
UPSTREAM_CONTRACT_MANIFEST = UPSTREAM_CONTRACT_DIR / "manifest.json"
UPSTREAM_CONTRACT_HONESTY = UPSTREAM_CONTRACT_DIR / "semantic_honesty_audit.json"
UPSTREAM_FAMILY_CONTRACTS = (
    REPO_ROOT / "docs/research/ascii/semantic_maps/family_topology_contracts.json"
)
UPSTREAM_REVIEW_PROVENANCE = (
    REPO_ROOT / "docs/research/ascii/semantic_maps/review_provenance_manifest.json"
)
UPSTREAM_CONTRACT_FREEZE_SCHEMA = "fl4162.family_contract_freeze.v2"
UPSTREAM_CONTRACT_CUTOVER_SCHEMA = "fl4162.compiler_cutover.v1"
UPSTREAM_CELL_DECISION_SCHEMA = "fl4162.upstream_xp_cell_role_decision.v2"

DELETED_LEGACY_OWNER_PATHS = (
    REPO_ROOT / "assets/actor_visual_profiles/source/layer_roles.json",
    REPO_ROOT / "scripts/promote_entries_to_layer_roles.py",
    REPO_ROOT / "scripts/lib/row_compose_policies.py",
    REPO_ROOT / "scripts/lib/semantic_roles.py",
)


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if not path.is_file():
        _fail(f"missing {label}: {_repo_rel(path)}")
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        _fail(f"cannot read {label} {_repo_rel(path)}: {exc}")
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            _fail(f"{label} line {lineno} is malformed: {exc}")
        if not isinstance(row, dict):
            _fail(f"{label} line {lineno} must be an object")
        rows.append(row)
    return rows


def _require_frozen_upstream_contract() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load the reviewed RQ-200 full-cell contract as compiler source truth.

    The freeze is the reviewed authority boundary. Queue labels and generated
    summaries cannot independently admit a source layer. Every source key must
    resolve to one fingerprint-bound full-coordinate decision whose render
    operation and semantic contributions remain separate.
    """
    freeze = _read_json(UPSTREAM_CONTRACT_FREEZE)
    if (
        freeze.get("schema") != UPSTREAM_CONTRACT_FREEZE_SCHEMA
        or freeze.get("frozen") is not True
        or freeze.get("is_proposal") is not False
        or freeze.get("runtime_authoritative") is not False
        or freeze.get("contract_authority") != "reviewed_upstream_source_contract"
    ):
        _fail("RQ-200 upstream contract is not a reviewed frozen source contract")
    cutover = freeze.get("cutover_boundary") or {}
    if (
        cutover.get("compiler_cutover_complete") is not True
        or cutover.get("required_receipt")
        != _repo_rel(UPSTREAM_CONTRACT_CUTOVER)
    ):
        _fail("RQ-200 compiler cutover is not enabled by the frozen contract")

    bound_paths = {
        "review_queue_sha256": UPSTREAM_CONTRACT_QUEUE,
        "cell_role_decisions_sha256": UPSTREAM_CONTRACT_DECISIONS,
        "cell_review_state_decisions_sha256": UPSTREAM_CONTRACT_REVIEW_STATES,
        "cell_contract_manifest_sha256": UPSTREAM_CONTRACT_MANIFEST,
        "semantic_honesty_audit_sha256": UPSTREAM_CONTRACT_HONESTY,
        "family_topology_contracts_sha256": UPSTREAM_FAMILY_CONTRACTS,
        "review_provenance_manifest_sha256": UPSTREAM_REVIEW_PROVENANCE,
    }
    hashes = freeze.get("source_hashes") or {}
    for field, path in bound_paths.items():
        expected = hashes.get(field)
        if not isinstance(expected, str) or len(expected) != 64:
            _fail(f"RQ-200 freeze missing {field}")
        if _sha256_file(path) != expected:
            _fail(f"RQ-200 freeze source is stale: {_repo_rel(path)}")

    queue = _read_json(UPSTREAM_CONTRACT_QUEUE)
    coverage = queue.get("coverage") or {}
    if (
        queue.get("freeze_gate", {}).get("ready") is not True
        or int(coverage.get("pending_units", -1)) != 0
        or int(coverage.get("decided_units", -1))
        != int(coverage.get("unique_review_units", -2))
    ):
        _fail("RQ-200 full-cell review queue is incomplete")

    queue_units = {
        str(unit.get("review_unit_id") or ""): unit
        for unit in queue.get("review_units") or []
    }
    if "" in queue_units or len(queue_units) != int(coverage.get("unique_review_units", -1)):
        _fail("RQ-200 review queue has missing or duplicate review-unit ids")

    by_source_key: dict[str, dict[str, Any]] = {}
    decisions = _read_jsonl(UPSTREAM_CONTRACT_DECISIONS, "RQ-200 cell decisions")
    seen_units: set[str] = set()
    for decision in decisions:
        unit_id = str(decision.get("review_unit_id") or "")
        if not unit_id or unit_id in seen_units or unit_id not in queue_units:
            _fail(f"RQ-200 cell decision has unknown or duplicate unit {unit_id!r}")
        seen_units.add(unit_id)
        if (
            decision.get("schema") != UPSTREAM_CELL_DECISION_SCHEMA
            or decision.get("authority") is not False
            or decision.get("is_proposal") is not True
        ):
            _fail(f"{unit_id}: invalid full-cell decision authority boundary")
        queue_unit = queue_units[unit_id]
        if decision.get("source_layer_sha256") != queue_unit.get("source_layer_sha256"):
            _fail(f"{unit_id}: source fingerprint differs from frozen queue")
        assignments = decision.get("cell_assignments") or []
        assigned = sum(int(span[4]) for span in assignments if isinstance(span, list) and len(span) == 7)
        if assigned != int(queue_unit.get("coverage", {}).get("raw_cells", -1)):
            _fail(f"{unit_id}: full-cell assignment count differs from frozen queue")
        operations: set[str] = set()
        contributions: set[str] = set()
        for span in assignments:
            if not isinstance(span, list) or len(span) != 7:
                _fail(f"{unit_id}: malformed full-cell assignment")
            operation = span[5]
            semantics = span[6]
            if operation not in RENDER_OPERATION_BITS:
                _fail(f"{unit_id}: unknown render operation {operation!r}")
            if not isinstance(semantics, list) or not all(
                isinstance(value, str) and value for value in semantics
            ):
                _fail(f"{unit_id}: invalid semantic contribution set")
            operations.add(operation)
            contributions.update(semantics)
        if not operations:
            _fail(f"{unit_id}: full-cell decision has no render operations")
        record = {
            "review_unit_id": unit_id,
            "source_layer_sha256": decision["source_layer_sha256"],
            "render_operations": sorted(operations),
            "semantic_contributions": sorted(contributions),
            "review_provenance": decision.get("review_provenance") or {},
        }
        members = decision.get("member_source_keys") or []
        if sorted(members) != sorted(queue_unit.get("member_source_keys") or []):
            _fail(f"{unit_id}: exact-fingerprint member set differs from frozen queue")
        for source_key in members:
            if not isinstance(source_key, str) or not re.search(r"-L\d+$", source_key):
                _fail(f"{unit_id}: invalid source key {source_key!r}")
            if source_key in by_source_key:
                _fail(f"RQ-200 source key has multiple decision owners: {source_key}")
            by_source_key[source_key] = record

    if seen_units != set(queue_units):
        missing = sorted(set(queue_units) - seen_units)
        _fail(f"RQ-200 frozen queue units lack full-cell decisions: {missing}")
    if int(freeze.get("coverage", {}).get("pending_review_units", -1)) != 0:
        _fail("RQ-200 family freeze still records pending review units")
    return freeze, by_source_key


def _require_deleted_legacy_owners() -> dict[str, Any]:
    """Fail before generation when any retired visual owner is live again."""
    present = [_repo_rel(path) for path in DELETED_LEGACY_OWNER_PATHS if path.exists()]
    if present:
        _fail(f"retired actor visual owners still exist: {present}")

    compiler_source = Path(__file__).read_text(encoding="utf-8")
    compiler_tree = ast.parse(compiler_source)
    forbidden_compiler_functions = (
        "_ahs_digits",
        "_resolve_source_xp",
        "_enumerate_server_reachable_keys",
    )
    defined_functions = {
        node.name
        for node in ast.walk(compiler_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    found_compiler = [
        name for name in forbidden_compiler_functions if name in defined_functions
    ]
    merge_extra_owner_live = any(
        isinstance(node, ast.Dict)
        and any(
            isinstance(key, ast.Constant)
            and key.value == "merge_extra_layers"
            and isinstance(value, ast.Constant)
            and value.value is True
            for key, value in zip(node.keys, node.values)
        )
        for node in ast.walk(compiler_tree)
    )
    if merge_extra_owner_live:
        found_compiler.append("merge_extra_layers=true")
    runtime_source = (
        REPO_ROOT / "engine/actor_visual_profile_runtime.h"
    ).read_text(encoding="utf-8")
    forbidden_runtime_owners = (
        "ACTOR_VISUAL_LAYER_ROLE_",
        "const bool merge_extra",
    )
    found_runtime = [
        marker for marker in forbidden_runtime_owners if marker in runtime_source
    ]
    if found_compiler or found_runtime:
        _fail(
            "retired actor visual owner symbols are live: "
            f"compiler={found_compiler} runtime={found_runtime}"
        )
    return {
        "deleted_paths": [_repo_rel(path) for path in DELETED_LEGACY_OWNER_PATHS],
        "absent_compiler_symbols": list(forbidden_compiler_functions)
        + ["merge_extra_layers=true"],
        "absent_runtime_symbols": list(forbidden_runtime_owners),
    }


def _stable_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _cpp_string(value: Any) -> str:
    return json.dumps("" if value is None else str(value))


# FL-4131/FL-4049 Stage A: nullable glyph_manifest_hash literal helper.
# Emits `nullptr` for None and a C string literal otherwise. The runtime side
# (Phase 2+) is expected to compare this against the hash advertised by an
# admitted glyph manifest. CP437-only inputs use None and the runtime path
# remains the legacy CP437 contract.
def _cpp_glyph_manifest_literal(value: str | None) -> str:
    if value is None:
        return "nullptr"
    return _cpp_string(value)


# FL-4131/FL-4049 Stage A: aggregate compile-identity glyph manifest hash.
#
# CP437-only source sets return None and the constant is emitted as nullptr.
# If the material additive manifest is present, this returns its authored
# manifest hash directly. If not, admitted sprite manifests fall back to a
# deterministic hash over the admitted source-to-manifest mapping.
def _compile_identity_glyph_manifest_hash(source_xps: list[dict[str, Any]]) -> str | None:
    if MATERIAL_GLYPH_MANIFEST.is_file():
        return sha256_manifest(_read_json(MATERIAL_GLYPH_MANIFEST))
    admitted: list[tuple[str, str]] = []
    for src in source_xps:
        path = REPO_ROOT / str(src.get("source_xp") or "")
        h = _admitted_glyph_manifest_hash_for(path)
        if h is not None:
            admitted.append((str(src.get("source_xp_id") or ""), h))
    if not admitted:
        return None
    admitted.sort()
    return _sha256_bytes(_stable_json_bytes(admitted))


# FL-4131 P10: aggregate LUT + page-chain identity from the compiled AOA. The
# JoinV2 protocol carries these alongside glyph_manifest_hash so server can
# reject clients whose runtime atlas pages diverged from the canonical build
# (e.g. stale deploy, partial copy, manual edit).
def _compile_identity_lut_hash() -> str | None:
    """Read AOA.lut_hash. None if no AOA exists or no lut_hash field."""
    if not MATERIAL_GLYPH_MANIFEST.is_file():
        return None
    manifest = _read_json(MATERIAL_GLYPH_MANIFEST)
    content_pack_id = manifest.get("content_pack_id")
    if not isinstance(content_pack_id, str) or not content_pack_id:
        return None
    aoa_path = REPO_ROOT / "assets" / "glyphs" / "atlases" / f"{content_pack_id}.atlas_of_atlases.json"
    if not aoa_path.is_file():
        return None
    try:
        aoa = _read_json(aoa_path)
    except Exception:
        return None
    lh = aoa.get("lut_hash")
    return lh if isinstance(lh, str) and len(lh) == 64 else None


def _compile_identity_page_chain_hash() -> str | None:
    """Deterministic hash over (cell_px, page_hash) tuples in cell_px order.

    Lets a single 64-char digest represent "every atlas page in the ladder
    matches" without bloating the JoinV2 payload with 14 separate hashes.
    """
    if not MATERIAL_GLYPH_MANIFEST.is_file():
        return None
    manifest = _read_json(MATERIAL_GLYPH_MANIFEST)
    content_pack_id = manifest.get("content_pack_id")
    if not isinstance(content_pack_id, str) or not content_pack_id:
        return None
    aoa_path = REPO_ROOT / "assets" / "glyphs" / "atlases" / f"{content_pack_id}.atlas_of_atlases.json"
    if not aoa_path.is_file():
        return None
    try:
        aoa = _read_json(aoa_path)
    except Exception:
        return None
    pages = aoa.get("pages") or []
    chain: list[tuple[int, str]] = []
    for p in pages:
        if not isinstance(p, dict):
            continue
        cell_px = p.get("cell_px")
        page_hash = p.get("page_hash")
        if isinstance(cell_px, int) and isinstance(page_hash, str) and len(page_hash) == 64:
            chain.append((cell_px, page_hash))
    if not chain:
        return None
    chain.sort()
    return _sha256_bytes(_stable_json_bytes(chain))


def _compile_identity_content_pack_id(source_xps: list[dict[str, Any]]) -> str | None:
    if MATERIAL_GLYPH_MANIFEST.is_file():
        manifest = _read_json(MATERIAL_GLYPH_MANIFEST)
        content_pack_id = manifest.get("content_pack_id")
        return content_pack_id if isinstance(content_pack_id, str) and content_pack_id else None
    admitted: list[tuple[str, str]] = []
    for src in source_xps:
        path = REPO_ROOT / str(src.get("source_xp") or "")
        pack_id = _admitted_glyph_content_pack_id_for(path)
        if pack_id is not None:
            admitted.append((str(src.get("source_xp_id") or ""), pack_id))
    if not admitted:
        return None
    admitted.sort()
    return _sha256_bytes(_stable_json_bytes(admitted))


def _sanitize_profile_id(profile_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", profile_id).replace(".", "__")


def _guard_not_generated_input(path: Path) -> None:
    try:
        rel = path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return
    if rel.parts and rel.parts[0] == "engine" and rel.name.endswith(".generated.h"):
        _fail(f"refusing generated header input: {rel}")


def _digit(glyph: int) -> int:
    if ord("0") <= glyph <= ord("9"):
        return glyph - ord("0")
    if ord("A") <= glyph <= ord("Z"):
        return glyph + 10 - ord("A")
    if ord("a") <= glyph <= ord("z"):
        return glyph + 10 - ord("a")
    return -1


RawRgb = tuple[int, int, int]
RawCell = tuple[int, RawRgb, RawRgb]
AnsiCellTuple = tuple[int, int, int, int]
FrameMetaTuple = tuple[tuple[int, int, int], tuple[int, int]]


class XpLayer:
    def __init__(self, width: int, height: int, cells: list[RawCell]) -> None:
        self.width = width
        self.height = height
        self.cells = cells


class XpSprite:
    def __init__(self, path: Path, layers: list[XpLayer]) -> None:
        self.path = path
        self.layers = layers
        if not layers:
            _fail(f"{_repo_rel(path)} has no layers")
        meta = layers[0]
        self.width = meta.width
        self.height = meta.height
        angle_digit = _digit(meta.cells[0][0])
        self.angles = angle_digit if angle_digit > 0 else 1
        self.projs = 2 if angle_digit > 0 else 1
        self.anim_lens: list[int] = []
        for col in range(1, self.width):
            value = _digit(meta.cells[col * self.height][0])
            if value <= 0:
                break
            self.anim_lens.append(value)
        if not self.anim_lens:
            self.anim_lens = [1]
        self.total_anim_frames = sum(self.anim_lens)
        self.frame_cols = self.projs * self.total_anim_frames
        self.frame_rows = self.angles
        if self.width % self.frame_cols != 0:
            _fail(
                f"{_repo_rel(path)} width {self.width} not divisible by frame columns {self.frame_cols}"
            )
        if self.height % self.frame_rows != 0:
            _fail(
                f"{_repo_rel(path)} height {self.height} not divisible by angles {self.frame_rows}"
            )
        self.frame_width = self.width // self.frame_cols
        self.frame_height = self.height // self.frame_rows
        self.atlas_frames = self.angles * self.projs * self.total_anim_frames

    def _visual_cells(self, layer_index: int) -> list[RawCell]:
        if layer_index < 0 or layer_index >= len(self.layers):
            _fail(f"{_repo_rel(self.path)} missing layer {layer_index}")
        return list(self.layers[layer_index].cells)

    @staticmethod
    def _rgb2pal(rgb: RawRgb) -> int:
        r = (rgb[0] + 25) // 51
        g = (rgb[1] + 25) // 51
        b = (rgb[2] + 25) // 51
        return 16 + 36 * r + 6 * g + b

    @staticmethod
    def _pal2rgb(pal: int) -> RawRgb:
        pal -= 16
        r = pal // 36
        pal -= r * 36
        g = pal // 6
        pal -= g * 6
        b = pal
        return (r * 51, g * 51, b * 51)

    @staticmethod
    def _lighten_color(color: int) -> int:
        color -= 16
        r = color // 36
        color -= 36 * r
        g = color // 6
        color -= 6 * g
        b = color
        return 16 + min(5, r + 1) * 36 + min(5, g + 1) * 6 + min(5, b + 1)

    @staticmethod
    def _average_glyph_transp(glyph: int, fg: int, bg: int, mask: int) -> int:
        cov = GLYPH_COVERAGE[glyph & 0xFF]
        num = 0
        total = 0
        for bit, shift in ((1, 0), (2, 4), (4, 8), (8, 12)):
            if mask & bit:
                total += (cov >> shift) & 0xF
                num += 1
        return fg if total > num * 2 else bg

    @classmethod
    def _merge_raw_cell(cls, base: RawCell, merge: RawCell, key: RawCell, is_final_layer: bool) -> RawCell:
        merge_glyph, merge_fg, merge_bg = merge
        if is_final_layer and merge_fg == CYAN:
            base_glyph, base_fg, base_bg = base
            key_bg = key[2]
            fg_transp = base_fg == key_bg
            bg_transp = base_bg == key_bg
            if base_bg == MAGENTA:
                fg_transp = True
                bg_transp = True

            swoosh_bg_transp = merge_bg == key_bg
            glyph = 219 if merge_bg == CYAN else merge_glyph
            if glyph in (0, 32):
                return merge if merge_bg != MAGENTA else base
            if glyph == 220:
                mask = 3
            elif glyph == 221:
                mask = 5
            elif glyph == 222:
                mask = 10
            elif glyph == 223:
                mask = 12
            else:
                mask = 0

            if mask:
                ac_fg = TRANSPARENT if fg_transp else cls._rgb2pal(base_fg)
                ac_bg = TRANSPARENT if bg_transp else cls._rgb2pal(base_bg)
                fg_avg = cls._average_glyph_transp(base_glyph, ac_fg, ac_bg, mask)
                if swoosh_bg_transp:
                    bg_avg = cls._average_glyph_transp(base_glyph, ac_fg, ac_bg, 0xF ^ mask)
                    fg_rgb = CYAN if fg_avg == TRANSPARENT else cls._pal2rgb(cls._lighten_color(fg_avg))
                    bg_rgb = key_bg if fg_avg == TRANSPARENT else cls._pal2rgb(bg_avg)
                else:
                    fg_rgb = CYAN if fg_avg == TRANSPARENT else cls._pal2rgb(cls._lighten_color(fg_avg))
                    bg_rgb = merge_bg
                return glyph, fg_rgb, bg_rgb

            if fg_transp and bg_transp:
                return merge
            fg_rgb = CYAN if fg_transp else tuple(min(255, v + 51) for v in base_fg)
            bg_rgb = CYAN if bg_transp else tuple(min(255, v + 51) for v in base_bg)
            return base_glyph, fg_rgb, bg_rgb

        return merge if merge_bg != MAGENTA else base

    @staticmethod
    def _quantize(rgb: RawRgb, rgb_div: int) -> int:
        r = (rgb[0] * 5 + 128) // rgb_div
        g = (rgb[1] * 5 + 128) // rgb_div
        b = (rgb[2] * 5 + 128) // rgb_div
        return 16 + 36 * r + 6 * g + b

    def _render_cell(self, cell_index: int, c2: RawCell, rgb_div: int) -> AnsiCellTuple:
        c0 = self.layers[0].cells[cell_index]
        c1 = self.layers[1].cells[cell_index] if len(self.layers) > 1 else (0, (0, 0, 0), (0, 0, 0))
        glyph, fg, bk = c2
        key = c0[2]
        bk_transp = bk == key
        fg_transp = fg == key
        fg_swoosh = fg == CYAN
        bk_swoosh = bk == CYAN
        if bk == MAGENTA:
            bk_transp = True
            fg_transp = True
        if bk_swoosh:
            out_bk = SWOOSH
        elif bk_transp:
            out_bk = TRANSPARENT
        else:
            out_bk = self._quantize(bk, rgb_div)
        if fg_swoosh:
            out_fg = SWOOSH
        elif fg_transp:
            out_fg = TRANSPARENT
        else:
            out_fg = self._quantize(fg, rgb_div)
        spare = _digit(c1[0])
        if spare < 0:
            spare = 255
        return glyph, out_fg, out_bk, spare

    def frame_meta(self, atlas_frame: int) -> FrameMetaTuple:
        if atlas_frame < 0 or atlas_frame >= self.atlas_frames:
            _fail(f"{_repo_rel(self.path)} missing atlas frame {atlas_frame}")
        frame_col = atlas_frame % self.frame_cols
        angle = atlas_frame // self.frame_cols
        refl = self.projs >= 2 and 2 * frame_col >= self.frame_cols
        ref = [
            self.frame_width,
            2 * self.frame_height if refl else 0,
            0,
        ]
        if self.height >= 2:
            y_proj = _digit(self.layers[0].cells[1][0])
            y_refl = _digit(self.layers[0].cells[1 + self.height][0])
            if not refl and 0 <= y_proj <= 2 * self.frame_height:
                ref[1] = y_proj
            if refl and 0 <= y_refl <= 2 * self.frame_height:
                ref[1] = 2 * self.frame_height - y_refl
        if self.height >= 3:
            z_proj = _digit(self.layers[0].cells[2][0])
            z_refl = _digit(self.layers[0].cells[2 + self.height][0])
            if not refl and z_proj >= 0:
                ref[2] = -z_proj
            if refl and z_refl >= 0:
                ref[2] = -z_refl
        x0 = frame_col * self.frame_width
        y0 = angle * self.frame_height
        y1 = y0 + self.frame_height
        meta_xy = [0, 0]
        for y in range(y1 - 1, y0 - 1, -1):
            for x in range(x0, x0 + self.frame_width):
                cell_index = x * self.height + y
                if self.layers[0].cells[cell_index][0] == 2:
                    meta_xy[0] = (x - x0) * 2 - ref[0]
                    meta_xy[1] = (y1 - 1 - y) * 2 - ref[1]
        return (ref[0], ref[1], ref[2]), (meta_xy[0], meta_xy[1])

    def frame_cells(
        self,
        layer_index: int,
        atlas_frame: int,
    ) -> list[AnsiCellTuple]:
        if layer_index < 0 or layer_index >= len(self.layers):
            _fail(f"{_repo_rel(self.path)} missing layer {layer_index}")
        if atlas_frame < 0 or atlas_frame >= self.atlas_frames:
            _fail(f"{_repo_rel(self.path)} missing atlas frame {atlas_frame}")
        visual_cells = self._visual_cells(layer_index)
        angle = atlas_frame // self.frame_cols
        frame_col = atlas_frame % self.frame_cols
        x0 = frame_col * self.frame_width
        y0 = angle * self.frame_height
        y1 = y0 + self.frame_height
        rgb_div = 255 if self.projs < 2 or 2 * frame_col < self.frame_cols else 400
        out: list[AnsiCellTuple] = []
        for src_y in range(y1 - 1, y0 - 1, -1):
            for x in range(self.frame_width):
                src_x = x0 + x
                cell_index = src_x * self.height + src_y
                out.append(self._render_cell(cell_index, visual_cells[cell_index], rgb_div))
        return out

    def _visual_cells_multifold_composite(
        self, base_index: int, overlay_indices: list[int]
    ) -> list[RawCell]:
        """FL-4162: fold an ordered run of overlay layers INTO the base layer using the
        upstream merge (_merge_raw_cell), ACCUMULATING into one buffer so each fold sees
        the composite of all prior folds -- exactly as the legacy `merge_extra_layers`
        owner does (_visual_cells at layer 2). Only the FINAL overlay gets
        is_final_layer True (the cyan-fg swoosh special-case), and because the buffer is
        accumulated the swoosh reads base+all-prior-overlays as its body context (the
        wolack mounted case: mount_body_wolf + dense rider composite + equipment, then
        swoosh). NO merge_extra_layers flag is set on any migrated profile layer (Law 1:
        the compiler stays the single composite owner).

        Byte-identity is structural: overlay_indices MUST be exactly every raw layer
        above the base, in order (range(base_index+1, len(layers))). That makes
        _visual_cells_multifold_composite(2, [3..N]) cell-for-cell equal to
        _visual_cells(2, merge_extra_layers=True). Anything else fails closed."""
        if base_index < 0 or base_index >= len(self.layers):
            _fail(f"{_repo_rel(self.path)} missing base layer {base_index}")
        expected = list(range(base_index + 1, len(self.layers)))
        if list(overlay_indices) != expected:
            _fail(
                f"{_repo_rel(self.path)} multifold composite overlay indices "
                f"{list(overlay_indices)} != every raw layer above base {expected} "
                f"(byte-identity with the legacy merge requires the full contiguous run)"
            )
        if not expected:
            _fail(
                f"{_repo_rel(self.path)} multifold composite base {base_index} has no "
                f"overlay layers above it (nothing to fold)"
            )
        visual = list(self.layers[base_index].cells)
        key = self.layers[0].cells
        last = expected[-1]
        for oi in expected:
            is_final = oi == last
            for i, cell in enumerate(self.layers[oi].cells):
                visual[i] = self._merge_raw_cell(visual[i], cell, key[i], is_final)
        return visual

    def frame_cells_multifold_composite(
        self, base_index: int, overlay_indices: list[int], atlas_frame: int
    ) -> list[AnsiCellTuple]:
        """Render one frame of the base layer with the ordered overlay run folded in
        (FL-4162). Mirrors frame_cells exactly except for the visual source."""
        if base_index < 0 or base_index >= len(self.layers):
            _fail(f"{_repo_rel(self.path)} missing base layer {base_index}")
        if atlas_frame < 0 or atlas_frame >= self.atlas_frames:
            _fail(f"{_repo_rel(self.path)} missing atlas frame {atlas_frame}")
        visual_cells = self._visual_cells_multifold_composite(base_index, overlay_indices)
        angle = atlas_frame // self.frame_cols
        frame_col = atlas_frame % self.frame_cols
        x0 = frame_col * self.frame_width
        y0 = angle * self.frame_height
        y1 = y0 + self.frame_height
        rgb_div = 255 if self.projs < 2 or 2 * frame_col < self.frame_cols else 400
        out: list[AnsiCellTuple] = []
        for src_y in range(y1 - 1, y0 - 1, -1):
            for x in range(self.frame_width):
                src_x = x0 + x
                cell_index = src_x * self.height + src_y
                out.append(self._render_cell(cell_index, visual_cells[cell_index], rgb_div))
        return out

    def _visual_cells_swoosh_composite(
        self, body_index: int, swoosh_index: int
    ) -> list[RawCell]:
        """FL-4162 Branch A, now the degenerate single-overlay case of the multifold
        composite: fold the final cyan-fg swoosh into the body. Delegates to
        _visual_cells_multifold_composite so there is ONE fold owner. Output stays
        byte-identical to `_visual_cells(body_index, merge_extra=True)`. Fail closed if
        the swoosh is not the final source layer (is_final_layer parity)."""
        for idx in (body_index, swoosh_index):
            if idx < 0 or idx >= len(self.layers):
                _fail(f"{_repo_rel(self.path)} missing layer {idx}")
        if swoosh_index != len(self.layers) - 1:
            _fail(
                f"{_repo_rel(self.path)} swoosh composite requires the final source "
                f"layer {len(self.layers) - 1} (is_final_layer parity), got {swoosh_index}"
            )
        return self._visual_cells_multifold_composite(
            body_index, list(range(body_index + 1, len(self.layers)))
        )

    def frame_cells_swoosh_composite(
        self, body_index: int, swoosh_index: int, atlas_frame: int
    ) -> list[AnsiCellTuple]:
        """Render one frame of the body layer with the final swoosh overlay folded in
        (FL-4162 Branch A). Mirrors frame_cells exactly except for the visual source."""
        if body_index < 0 or body_index >= len(self.layers):
            _fail(f"{_repo_rel(self.path)} missing layer {body_index}")
        if atlas_frame < 0 or atlas_frame >= self.atlas_frames:
            _fail(f"{_repo_rel(self.path)} missing atlas frame {atlas_frame}")
        visual_cells = self._visual_cells_swoosh_composite(body_index, swoosh_index)
        angle = atlas_frame // self.frame_cols
        frame_col = atlas_frame % self.frame_cols
        x0 = frame_col * self.frame_width
        y0 = angle * self.frame_height
        y1 = y0 + self.frame_height
        rgb_div = 255 if self.projs < 2 or 2 * frame_col < self.frame_cols else 400
        out: list[AnsiCellTuple] = []
        for src_y in range(y1 - 1, y0 - 1, -1):
            for x in range(self.frame_width):
                src_x = x0 + x
                cell_index = src_x * self.height + src_y
                out.append(self._render_cell(cell_index, visual_cells[cell_index], rgb_div))
        return out


def _sidecar_path_for_xp(source_xp_path: Path) -> Path:
    return source_xp_path.with_suffix(source_xp_path.suffix + ".glyph_profile.json")


def _path_matches_entry(rel_path: str, entry: dict[str, Any]) -> bool:
    kind = entry.get("kind")
    if "path_prefix" in entry:
        prefix = str(entry.get("path_prefix") or "").rstrip("/")
        return rel_path == prefix or rel_path.startswith(prefix + "/") or rel_path.startswith(prefix)
    path = str(entry.get("path") or "").rstrip("/")
    if kind == "directory":
        return rel_path == path or rel_path.startswith(path + "/")
    return rel_path == path


def _source_allowed_by_admission_allowlist(source_xp_path: Path) -> bool:
    if not ADMISSION_ALLOWLIST.is_file():
        return False
    try:
        allowlist = _read_json(ADMISSION_ALLOWLIST)
    except SystemExit:
        return False
    rel = _repo_rel(source_xp_path)
    deny = allowlist.get("deny") if isinstance(allowlist.get("deny"), list) else []
    allow = allowlist.get("allow") if isinstance(allowlist.get("allow"), list) else []
    for entry in deny:
        if isinstance(entry, dict) and _path_matches_entry(rel, entry):
            return False
    for entry in allow:
        if isinstance(entry, dict) and _path_matches_entry(rel, entry):
            return True
    return False


# FL-4131/FL-4049 Stage B: admission lookup for extended-glyph sources.
def _admitted_glyph_manifest_hash_for(source_xp_path: Path) -> str | None:
    admitted = _admitted_glyph_manifest_identity_for(source_xp_path)
    return admitted[0] if admitted is not None else None


def _admitted_glyph_content_pack_id_for(source_xp_path: Path) -> str | None:
    admitted = _admitted_glyph_manifest_identity_for(source_xp_path)
    return admitted[1] if admitted is not None else None


def _admitted_glyph_manifest_identity_for(source_xp_path: Path) -> tuple[str, str] | None:
    if not _source_allowed_by_admission_allowlist(source_xp_path):
        return None
    sidecar_path = _sidecar_path_for_xp(source_xp_path)
    if not sidecar_path.is_file():
        return None
    try:
        sidecar = _read_json(sidecar_path)
    except SystemExit:
        return None
    if sidecar.get("sidecar_version") != 1 or sidecar.get("profile_kind") != "extended_glyph_v1":
        return None
    manifest_hash = sidecar.get("glyph_manifest_hash")
    manifest_path_raw = sidecar.get("glyph_manifest_path")
    if not isinstance(manifest_hash, str) or not isinstance(manifest_path_raw, str) or not manifest_path_raw:
        return None
    manifest_path = REPO_ROOT / manifest_path_raw
    if not manifest_path.is_file():
        return None
    try:
        manifest = _read_json(manifest_path)
    except SystemExit:
        return None
    if sha256_manifest(manifest) != manifest_hash:
        return None
    content_pack_id = manifest.get("content_pack_id")
    if not isinstance(content_pack_id, str) or not content_pack_id:
        return None
    return manifest_hash, content_pack_id


def _assert_glyphs_admitted_for_source(path: Path, layers: list["XpLayer"]) -> None:
    """Fail closed if any cell uses glyph > 255 without an admitted manifest hash.

    Phase 0/1: no admission registered; an extended glyph is an immediate
    contract violation. The compiler refuses to advance.
    """
    manifest_hash = _admitted_glyph_manifest_hash_for(path)
    if manifest_hash is not None:
        return  # source has an admitted hash; extended glyphs allowed.
    for li, layer in enumerate(layers):
        for ci, cell in enumerate(layer.cells):
            # XpLayer.cells stores RawCell tuples: (glyph, fg, bg).
            glyph = int(cell[0])
            if glyph > 255:
                _fail(
                    "FL-4131/FL-4049 Stage B: "
                    f"{_repo_rel(path)} layer={li} cell={ci} glyph={glyph} > 255 "
                    "but no admitted glyph_manifest_hash. Extended glyphs require "
                    "Phase 2+ admission via assets/glyphs/admission_allowlist.json "
                    "plus an authored manifest hash."
                )


def _load_xp(path: Path) -> XpSprite:
    if not path.is_file():
        _fail(f"missing XP source: {_repo_rel(path)}")
    raw = gzip.decompress(path.read_bytes())
    if len(raw) < 16:
        _fail(f"XP file too small: {_repo_rel(path)}")
    _version, layer_count, width, height = struct.unpack_from("<4i", raw, 0)
    offset = 16
    layers: list[XpLayer] = []
    for layer_index in range(layer_count):
        if layer_index > 0:
            if offset + 8 > len(raw):
                _fail(f"truncated XP layer header: {_repo_rel(path)}")
            layer_width, layer_height = struct.unpack_from("<2i", raw, offset)
            offset += 8
            if layer_width != width or layer_height != height:
                _fail(f"unsupported variable XP layer size in {_repo_rel(path)}")
        cells: list[RawCell] = []
        for _ in range(width * height):
            if offset + 10 > len(raw):
                _fail(f"truncated XP cell data: {_repo_rel(path)}")
            glyph = struct.unpack_from("<I", raw, offset)[0]
            fg = (raw[offset + 4], raw[offset + 5], raw[offset + 6])
            bg = (raw[offset + 7], raw[offset + 8], raw[offset + 9])
            offset += 10
            cells.append((glyph, fg, bg))
        layers.append(XpLayer(width, height, cells))
    # FL-4131/FL-4049 Stage B: extended-glyph admission contract check.
    _assert_glyphs_admitted_for_source(path, layers)
    return XpSprite(path, layers)


def _cell_visible(cell: AnsiCellTuple) -> bool:
    glyph, fg, bg, _spare = cell
    return not (
        (bg == TRANSPARENT and fg == TRANSPARENT) or
        (glyph in (0, 32) and bg == TRANSPARENT) or
        (glyph == 219 and fg == TRANSPARENT)
    )


def _paste_cell(dst: AnsiCellTuple, src: AnsiCellTuple) -> AnsiCellTuple:
    if not _cell_visible(src):
        if not _cell_visible(dst):
            return src
        return dst
    return src


def _validate_key(key: Any, profile_id: str) -> None:
    fields = [
        "skin_id",
        "actor_style_id",
        "presentation_kind_id",
        "variation_id",
        "mount_id",
        "rig_id",
        "head_item_id",
        "head_style_id",
        "chest_item_id",
        "chest_style_id",
        "weapon_item_id",
        "weapon_style_id",
        "shield_item_id",
        "shield_style_id",
    ]
    if not isinstance(key, dict):
        _fail(f"{profile_id}: key must be an object")
    for field in fields:
        if not isinstance(key.get(field), int):
            _fail(f"{profile_id}: key.{field} must be an integer")
    for field in ("future_slot_kind_ids", "future_item_ids", "future_style_ids"):
        value = key.get(field)
        if not isinstance(value, list) or len(value) != 4 or not all(isinstance(x, int) for x in value):
            _fail(f"{profile_id}: key.{field} must contain four integers")


def _empty_key() -> dict[str, Any]:
    return {
        "skin_id": SKIN_NORMAL_PLAYER,
        "actor_style_id": ACTOR_STYLE_DEFAULT,
        "presentation_kind_id": PRESENTATION_IDLE_WALK,
        "variation_id": VARIATION_DEFAULT,
        "mount_id": MOUNT_NONE,
        "rig_id": RIG_DEFAULT,
        "head_item_id": 0,
        "head_style_id": 0,
        "chest_item_id": 0,
        "chest_style_id": 0,
        "weapon_item_id": 0,
        "weapon_style_id": 0,
        "shield_item_id": 0,
        "shield_style_id": 0,
        "future_slot_kind_ids": [0, 0, 0, 0],
        "future_item_ids": [0, 0, 0, 0],
        "future_style_ids": [0, 0, 0, 0],
    }


def _key_sort_tuple(key: dict[str, Any]) -> tuple[int, ...]:
    return tuple(_key_values(key))


def _server_reachable_keys() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    doc = _read_json(SERVER_REACHABILITY)
    identity = _server_identity()
    if doc.get("source") != "server/actor_visual_reachability_dump.cpp":
        _fail("server reachability does not come from the C++ dump owner")
    if doc.get("catalog_source") != "server/actor_visual_catalog_source.h":
        _fail("server reachability does not cite the server catalog owner")
    if doc.get("server_markers_ok") is not True:
        _fail("server reachability dump reports failed server markers")
    identity_fields = {
        "server_reachability_scope_id": "kServerActorVisualReachabilityScopeId",
        "server_reachability_hash": "kServerActorVisualReachabilityHash",
        "server_catalog_hash": "kServerActorVisualCatalogHash",
    }
    for doc_field, identity_field in identity_fields.items():
        if doc.get(doc_field) != identity[identity_field]:
            _fail(f"server reachability identity mismatch: {doc_field}")
    rows = doc.get("reachable_keys")
    if not isinstance(rows, list) or len(rows) != int(doc.get("reachable_key_count", -1)):
        _fail("server reachability key count is missing or stale")
    keys: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        source = row.get("key") if isinstance(row, dict) else None
        if not isinstance(source, dict):
            _fail(f"server reachability row {row_index} lacks a full key")
        key = _empty_key()
        for field in (
            "skin_id",
            "actor_style_id",
            "presentation_kind_id",
            "variation_id",
            "mount_id",
            "rig_id",
            "head_item_id",
            "head_style_id",
            "chest_item_id",
            "chest_style_id",
            "weapon_item_id",
            "weapon_style_id",
            "shield_item_id",
            "shield_style_id",
        ):
            value = source.get(field)
            if not isinstance(value, int) or value < 0 or value > 0xFFFF:
                _fail(f"server reachability row {row_index} has invalid {field}")
            key[field] = value
        future_slots = source.get("future_slots")
        if not isinstance(future_slots, list) or len(future_slots) > 4:
            _fail(f"server reachability row {row_index} has invalid future_slots")
        for slot_index, slot in enumerate(future_slots):
            if not isinstance(slot, dict):
                _fail(f"server reachability row {row_index} has malformed future slot")
            key["future_slot_kind_ids"][slot_index] = int(slot.get("slot_kind_id", -1))
            key["future_item_ids"][slot_index] = int(slot.get("item_id", -1))
            key["future_style_ids"][slot_index] = int(slot.get("visual_style_id", -1))
            if min(
                key["future_slot_kind_ids"][slot_index],
                key["future_item_ids"][slot_index],
                key["future_style_ids"][slot_index],
            ) < 0:
                _fail(f"server reachability row {row_index} has negative future slot ids")
        keys.append(key)
    keys.sort(key=_key_sort_tuple)
    if len({_key_sort_tuple(key) for key in keys}) != len(keys):
        _fail("server reachability contains duplicate exact keys")
    return doc, keys


def _server_catalog_profiles(reachability_doc: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = reachability_doc.get("catalog_profiles")
    if (
        not isinstance(profiles, list)
        or len(profiles) != int(reachability_doc.get("catalog_profile_count", -1))
        or not profiles
    ):
        _fail("server reachability lacks the catalog-owned profile list")
    ids: set[int] = set()
    out: list[dict[str, Any]] = []
    for index, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            _fail(f"server catalog profile {index} is malformed")
        profile_id = profile.get("id")
        skin_id = profile.get("skin_definition_id")
        slug = profile.get("slug")
        starters = profile.get("starter_entries")
        if (
            not isinstance(profile_id, int)
            or profile_id <= 0
            or profile_id in ids
            or not isinstance(skin_id, int)
            or skin_id <= 0
            or not isinstance(slug, str)
            or not slug
            or not isinstance(starters, list)
        ):
            _fail(f"server catalog profile {index} has invalid identity")
        for starter_index, starter in enumerate(starters):
            if not isinstance(starter, dict) or any(
                not isinstance(starter.get(field), int)
                for field in (
                    "slot_kind_id",
                    "item_definition_id",
                    "visual_style_id",
                    "state_flags",
                )
            ):
                _fail(
                    f"server catalog profile {index} starter {starter_index} is malformed"
                )
        ids.add(profile_id)
        out.append(profile)
    return out


def _profile_bindings(
    reachability_doc: dict[str, Any],
    reachable_keys: list[dict[str, Any]],
) -> dict[tuple[int, ...], dict[str, Any]]:
    doc = _read_json(PROFILE_BINDINGS)
    if (
        doc.get("schema") != "actor_visual_profile_source_bindings/v1"
        or doc.get("authority") is not True
        or doc.get("source_owner") != "ActorVisualProfile authored exact-key bindings"
    ):
        _fail("profile bindings are not the authored exact-key source owner")
    if doc.get("server_reachability_hash") != reachability_doc.get("server_reachability_hash"):
        _fail("profile bindings are stale against server reachability")
    if doc.get("upstream_contract_freeze_sha256") != _sha256_file(UPSTREAM_CONTRACT_FREEZE):
        _fail("profile bindings are stale against the frozen upstream contract")
    rows = doc.get("rows")
    if not isinstance(rows, list) or len(rows) != int(doc.get("row_count", -1)):
        _fail("profile binding row count is missing or stale")
    by_key: dict[tuple[int, ...], dict[str, Any]] = {}
    profile_ids: set[str] = set()
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("key"), dict):
            _fail(f"profile binding row {row_index} lacks a full exact key")
        key_tuple = _key_sort_tuple(row["key"])
        if key_tuple in by_key:
            _fail(f"profile binding row {row_index} duplicates an exact key")
        profile_id = row.get("profile_id")
        source_xp = row.get("source_xp")
        source_id = row.get("source_xp_id")
        if not isinstance(profile_id, str) or not profile_id or profile_id in profile_ids:
            _fail(f"profile binding row {row_index} has an invalid profile id")
        if not isinstance(source_xp, str) or not isinstance(source_id, str):
            _fail(f"profile binding row {row_index} lacks an exact XP binding")
        if Path(source_xp).stem != source_id or not (REPO_ROOT / source_xp).is_file():
            _fail(f"profile binding row {row_index} has a stale XP binding")
        contract_kind = row.get("source_contract_kind", "upstream")
        if contract_kind == "upstream":
            if row.get("pinned_upstream_commit") != PINNED_UPSTREAM_COMMIT:
                _fail(f"profile binding row {row_index} has stale upstream provenance")
        elif contract_kind == "custom":
            contract = _custom_source_contract().get((source_xp, source_id))
            if contract is None or row.get("source_commit") != contract.get("source_commit"):
                _fail(f"profile binding row {row_index} has stale custom provenance")
        else:
            _fail(f"profile binding row {row_index} has an unknown source contract kind")
        profile_ids.add(profile_id)
        by_key[key_tuple] = row
    expected = {_key_sort_tuple(key) for key in reachable_keys}
    if set(by_key) != expected:
        missing = len(expected - set(by_key))
        orphan = len(set(by_key) - expected)
        _fail(f"profile bindings differ from server reachability: missing={missing} orphan={orphan}")
    return by_key


def _first_multi_frame_anim(sprite: Sprite) -> int:
    for index, length in enumerate(sprite.anim_lens):
        if length > 1:
            return index
    return 0


def _locomotion_anim_track_for_sprite(
    sprite: Sprite,
    presentation: int,
    resolver_outcome: str,
) -> list[int]:
    if resolver_outcome == "y9_extension_bee_attack_sword":
        attack_track = _first_multi_frame_anim(sprite)
        return [attack_track, attack_track, attack_track, attack_track]

    if presentation != PRESENTATION_IDLE_WALK:
        return [0, 0, 0, 0]

    moving_track = _first_multi_frame_anim(sprite)

    track = [0, 0, 0, 0]
    track[LOCOMOTION_NONE] = 0
    track[LOCOMOTION_IDLE] = 0
    track[LOCOMOTION_MOVING] = moving_track
    track[LOCOMOTION_AIRBORNE] = moving_track
    return track


def _steady_frame_index_for_sprite(
    sprite: Sprite,
    presentation: int,
    resolver_outcome: str,
) -> int:
    if presentation == PRESENTATION_IDLE_WALK:
        return 0
    if presentation == PRESENTATION_DEATH:
        return 0
    if not sprite.anim_lens:
        return 0
    if resolver_outcome == "y9_extension_bee_attack_sword":
        attack_track = _first_multi_frame_anim(sprite)
        if 0 <= attack_track < len(sprite.anim_lens):
            return max(0, int(sprite.anim_lens[attack_track]) - 1)
    return max(0, int(sprite.anim_lens[0]) - 1)


def _attack_to_bigbee_frame_map(attack_sprite: Sprite, bigbee_sprite: Sprite) -> list[int]:
    if attack_sprite.angles != bigbee_sprite.angles or attack_sprite.projs != bigbee_sprite.projs:
        _fail("bee attack extension requires matching attack/bigbee angle and projection topology")
    if len(bigbee_sprite.anim_lens) < 2 or bigbee_sprite.anim_lens[1] <= 0:
        _fail("bee attack extension requires a bigbee hover anim track")
    attack_total = sum(attack_sprite.anim_lens)
    bigbee_total = sum(bigbee_sprite.anim_lens)
    attack_proj_span = attack_total
    bigbee_proj_span = bigbee_total
    out: list[int] = []
    for frame_index in range(attack_sprite.atlas_frames):
        angle = frame_index // attack_sprite.frame_cols
        frame_col = frame_index % attack_sprite.frame_cols
        proj = frame_col // attack_proj_span
        frame_in_proj = frame_col % attack_proj_span
        hover_frame = 1 + (frame_in_proj % int(bigbee_sprite.anim_lens[1]))
        out.append(angle * bigbee_sprite.frame_cols + proj * bigbee_proj_span + hover_frame)
    return out


_UPSTREAM_CONTRACT_CACHE: tuple[dict[str, Any], dict[str, dict[str, Any]]] | None = None
_CUSTOM_SOURCE_CONTRACT_CACHE: dict[tuple[str, str], dict[str, Any]] | None = None


def _upstream_contract() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    global _UPSTREAM_CONTRACT_CACHE
    if _UPSTREAM_CONTRACT_CACHE is None:
        _UPSTREAM_CONTRACT_CACHE = _require_frozen_upstream_contract()
    return _UPSTREAM_CONTRACT_CACHE


def _custom_source_contract() -> dict[tuple[str, str], dict[str, Any]]:
    """Load target-owned authored sheets without borrowing upstream semantics."""
    global _CUSTOM_SOURCE_CONTRACT_CACHE
    if _CUSTOM_SOURCE_CONTRACT_CACHE is not None:
        return _CUSTOM_SOURCE_CONTRACT_CACHE
    doc = _read_json(CUSTOM_SOURCE_CONTRACT)
    if (
        doc.get("schema") != "actor_visual_custom_source_contract/v1"
        or doc.get("authority") is not True
        or doc.get("source_owner") != "target-authored standalone actor sheets"
    ):
        _fail("custom source contract is not the target-authored source owner")
    entries = doc.get("entries")
    if not isinstance(entries, list) or len(entries) != int(doc.get("entry_count", -1)):
        _fail("custom source contract entry count is missing or stale")
    by_source: dict[tuple[str, str], dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            _fail(f"custom source contract entry {index} is malformed")
        source_xp = entry.get("source_xp")
        source_id = entry.get("source_xp_id")
        key = (source_xp, source_id)
        if (
            not isinstance(source_xp, str)
            or not isinstance(source_id, str)
            or Path(source_xp).stem != source_id
            or key in by_source
        ):
            _fail(f"custom source contract entry {index} has an invalid source identity")
        source_path = REPO_ROOT / source_xp
        if not source_path.is_file() or _sha256_file(source_path) != entry.get("source_sha256"):
            _fail(f"custom source contract entry {index} has stale source bytes")
        if entry.get("source_kind") != "VERIFIED_STATE_LAYER":
            _fail(f"custom source contract entry {index} has an invalid source kind")
        operations = entry.get("render_operations")
        contributions = entry.get("semantic_contributions")
        if (
            operations != ["seed_l2_base_accumulator"]
            or not isinstance(contributions, list)
            or not contributions
            or not all(isinstance(value, str) and value for value in contributions)
        ):
            _fail(f"custom source contract entry {index} has invalid layer semantics")
        by_source[key] = entry
    _CUSTOM_SOURCE_CONTRACT_CACHE = by_source
    return by_source


def _source_layer_sha256(sprite: "XpSprite", layer_index: int) -> str:
    cells = [
        [glyph, *fg, *bg]
        for glyph, fg, bg in sprite.layers[layer_index].cells
    ]
    return _sha256_bytes(_stable_json_bytes(cells))


def _contract_layer(
    source_id: str,
    source_xp: str,
    sprite: "XpSprite",
    frame_map: list[int],
    layer_index: int,
    contract_kind: str = "upstream",
) -> dict[str, Any]:
    if contract_kind == "custom":
        contract = _custom_source_contract().get((source_xp, source_id))
        if contract is None:
            _fail(f"{source_id}: custom source contract lacks {source_xp}")
        if layer_index != int(contract.get("visual_layer_index", -1)):
            _fail(f"{source_id}: custom source contract does not own layer {layer_index}")
        operations = list(contract["render_operations"])
        operation_mask = 0
        for operation in operations:
            operation_mask |= RENDER_OPERATION_BITS[operation]
        return {
            "source_xp": source_xp,
            "source_xp_id": source_id,
            "source_kind": contract["source_kind"],
            "layer_index": layer_index,
            "render_operations": operations,
            "render_operation_mask": operation_mask,
            "semantic_contributions": list(contract["semantic_contributions"]),
            "contract_review_unit_id": contract["review_unit_id"],
            "source_layer_sha256": _source_layer_sha256(sprite, layer_index),
            "required": True,
            "anim_lens": sprite.anim_lens,
            "frame_map": frame_map,
            "frame_map_count": len(frame_map),
        }
    source_key = f"{source_id}-L{layer_index}"
    contract = _upstream_contract()[1].get(source_key)
    if contract is None:
        _fail(f"{source_id}: frozen RQ-200 contract lacks {source_key}")
    operations = list(contract["render_operations"])
    operation_mask = 0
    for operation in operations:
        operation_mask |= RENDER_OPERATION_BITS[operation]
    if layer_index == 2 and "seed_l2_base_accumulator" not in operations:
        _fail(f"{source_key}: L2 lacks seed_l2_base_accumulator")
    if layer_index > 2 and not any(
        operation in operations
        for operation in (
            "ordinal_overlay_merge_into_l2",
            "final_cyan_swoosh_context_composite",
        )
    ):
        _fail(f"{source_key}: overlay lacks a reviewed engine composition operation")
    return {
        "source_xp": source_xp,
        "source_xp_id": source_id,
        "source_kind": "UPSTREAM_AUTHORED",
        "layer_index": layer_index,
        "render_operations": operations,
        "render_operation_mask": operation_mask,
        "semantic_contributions": list(contract["semantic_contributions"]),
        "contract_review_unit_id": contract["review_unit_id"],
        "source_layer_sha256": contract["source_layer_sha256"],
        "required": True,
        "anim_lens": sprite.anim_lens,
        "frame_map": frame_map,
        "frame_map_count": len(frame_map),
    }


def _profile_layers(
    source_id: str,
    source_xp: str,
    sprite: "XpSprite",
    frame_map: list[int],
    contract_kind: str,
) -> list[dict[str, Any]]:
    """Build every raw visual layer from the frozen full-cell source contract."""
    if len(sprite.layers) <= 2:
        _fail(f"{source_id}: {source_xp} has no layer 2 (stub/split asset)")
    if contract_kind == "custom":
        custom = _custom_source_contract().get((source_xp, source_id))
        if custom is None:
            _fail(f"{source_id}: missing custom source contract")
        layer_indices = [int(custom["visual_layer_index"])]
    else:
        layer_indices = list(range(2, len(sprite.layers)))
    layers = [
        _contract_layer(
            source_id, source_xp, sprite, frame_map, layer_index, contract_kind
        )
        for layer_index in layer_indices
    ]
    # FL-4162: weapon_swoosh is upstream composition semantics, not an independent paste
    # layer. The legacy owner folded the final cyan-fg swoosh INTO the body (layer 2) via
    # the swoosh special-case merge -- and critically it folded EVERY intermediate layer
    # (3..N) into the body FIRST, so the swoosh reads the accumulated base+overlay
    # composite as its body context. The reviewed per-role stack keeps every layer
    # explicit (so the contract still owns them), but the compiler folds the whole
    # overlay run into the body via the accumulating multifold composite so output stays
    # byte-identical to legacy. attack-0001 is the degenerate single-overlay case (only
    # L3 above the base); wolack is the full mounted case (dense rider composite + armor/
    # helmet/shield + final swoosh). Do NOT reintroduce merge_extra_layers:true (that
    # routes migrated keys back through the legacy single-body owner -- a Law 1 two-owner
    # regression).
    swoosh_layers = [
        layer
        for layer in layers
        if "final_cyan_swoosh_context_composite" in layer["render_operations"]
    ]
    if swoosh_layers:
        if len(swoosh_layers) != 1:
            _fail(f"{source_id}: multiple weapon_swoosh layers in reviewed source")
        swoosh_layer = swoosh_layers[0]
        swoosh_index = int(swoosh_layer["layer_index"])
        if swoosh_index != len(sprite.layers) - 1:
            _fail(
                f"{source_id}: weapon_swoosh layer {swoosh_index} is not the final source "
                f"layer {len(sprite.layers) - 1} (is_final_layer parity for the swoosh "
                f"special-case)"
            )
        bodies = [l for l in layers if int(l["layer_index"]) == 2]
        if len(bodies) != 1:
            _fail(
                f"{source_id}: weapon_swoosh requires exactly one body layer at index 2 "
                f"to composite into (missing reviewed swoosh context)"
            )
        body_layer = bodies[0]
        # The swoosh special-case reads the accumulated composite, so byte-identity needs
        # the FULL contiguous overlay run [3..N] folded into the base in order. Every one
        # of those raw layers must be owned by the reviewed stack -- a legacy-folded layer
        # that the profile does not own would break byte-identity (fail closed).
        overlay_indices = list(range(int(body_layer["layer_index"]) + 1, len(sprite.layers)))
        owned_overlays = {int(l["layer_index"]) for l in layers if l is not body_layer}
        if owned_overlays != set(overlay_indices):
            _fail(
                f"{source_id}: weapon_swoosh multifold requires owned layers for every raw "
                f"layer above the base {overlay_indices}, got {sorted(owned_overlays)} "
                f"(a legacy-folded layer is unowned -- byte-identity cannot hold)"
            )
        body_layer["multifold_composite_overlay_indices"] = overlay_indices
        for l in layers:
            if l is not body_layer:
                l["composited_into_body_layer_index"] = int(body_layer["layer_index"])
    return layers


def _profile_for_binding(
    key: dict[str, Any], binding: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_xp = binding["source_xp"]
    source_id = binding["source_xp_id"]
    profile_id = binding["profile_id"]
    composition_kind = binding["composition_kind"]
    contract_kind = binding.get("source_contract_kind", "upstream")
    source_commit = binding.get("source_commit", PINNED_UPSTREAM_COMMIT)
    sprite = _load_xp(REPO_ROOT / source_xp)
    attack_xp = binding.get("extension_overlay_xp") or ""
    attack_id = binding.get("extension_overlay_source_xp_id") or ""
    if attack_xp:
        if not attack_id or Path(attack_xp).stem != attack_id:
            _fail(f"{profile_id}: invalid authored extension-overlay binding")
        attack_sprite = _load_xp(REPO_ROOT / attack_xp)
        frame_map = _attack_to_bigbee_frame_map(attack_sprite, sprite)
        attack_frame_map = list(range(attack_sprite.atlas_frames))
        target_sprite = attack_sprite
    else:
        attack_xp = ""
        attack_id = ""
        attack_sprite = sprite
        frame_map = list(range(sprite.atlas_frames))
        attack_frame_map = frame_map
        target_sprite = sprite
    presentation = key["presentation_kind_id"]
    profile = {
        "id": profile_id,
        "key": key,
        "angles": target_sprite.angles,
        "projs": target_sprite.projs,
        "anim_lens": target_sprite.anim_lens,
        "atlas_frames": target_sprite.atlas_frames,
        "playback": (
            "loop"
            if presentation == PRESENTATION_IDLE_WALK
            or composition_kind == "y9_extension_bee_attack_sword"
            else "reverse_clamp"
            if presentation == PRESENTATION_DEATH
            else "forward_clamp"
        ),
        "steady_frame_index": _steady_frame_index_for_sprite(
            target_sprite, presentation, composition_kind
        ),
        "locomotion_anim_track": _locomotion_anim_track_for_sprite(
            target_sprite, presentation, composition_kind
        ),
        "timeline_source_xp": source_xp,
        "timeline_source_xp_id": source_id,
        "timeline_source_kind": (
            "VERIFIED_STATE_LAYER" if contract_kind == "custom" else "UPSTREAM_AUTHORED"
        ),
        "timeline_frame_map": frame_map,
        # FL-4162/RQ-200: every raw visual layer comes from the frozen full-cell
        # source contract. There is no local role list and no legacy merge fallback.
        "layers": _profile_layers(
            source_id, source_xp, sprite, frame_map, contract_kind
        ),
    }
    if attack_xp:
        profile["layers"].append(_contract_layer(
            attack_id,
            attack_xp,
            attack_sprite,
            attack_frame_map,
            len(attack_sprite.layers) - 1,
        ))
    provenance = {
        "profile_id": profile_id,
        "composition_kind": composition_kind,
        "source_xp_id": source_id,
        "source_xp": source_xp,
        "extension_overlay_xp": attack_xp,
        "source_commit": source_commit,
        "source_contract_kind": contract_kind,
        "key": key,
    }
    return profile, provenance


def _target_frame_count(profile: dict[str, Any]) -> int:
    count = profile.get("atlas_frames")
    if isinstance(count, int) and count > 0:
        return count
    angles = int(profile.get("angles", 0))
    projs = int(profile.get("projs", 0))
    anim_lens = profile.get("anim_lens", [])
    if angles <= 0 or projs <= 0 or not anim_lens:
        _fail(f"{profile.get('id')}: cannot derive atlas frame count")
    return angles * projs * sum(anim_lens)


def _compile_profile_cells(
	profile: dict[str, Any],
	xp_cache: dict[str, XpSprite],
) -> tuple[dict[str, Any], list[AnsiCellTuple], list[FrameMetaTuple]]:
    profile_id = profile["id"]
    target_frames = _target_frame_count(profile)
    resolved_layers = []
    target_width = 0
    target_height = 0
    for layer in profile["layers"]:
        source = layer["source_xp"]
        sprite = xp_cache.get(source)
        if sprite is None:
            sprite = _load_xp(REPO_ROOT / source)
            xp_cache[source] = sprite
        layer_index = int(layer["layer_index"])
        if layer_index < 0 or layer_index >= len(sprite.layers):
            _fail(f"{profile_id}: {_repo_rel(sprite.path)} missing layer {layer_index}")
        if len(layer["frame_map"]) < target_frames:
            _fail(f"{profile_id}: layer frame_map shorter than profile frame count")
        target_width = max(target_width, sprite.frame_width)
        target_height = max(target_height, sprite.frame_height)
        resolved_layers.append((layer, sprite, layer_index))
    if target_width <= 0 or target_height <= 0:
        _fail(f"{profile_id}: invalid target cell canvas")
    target_cells: list[AnsiCellTuple] = []
    timeline_source = profile.get("timeline_source_xp") or profile["layers"][0]["source_xp"]
    timeline_sprite = xp_cache.get(timeline_source)
    if timeline_sprite is None:
        timeline_sprite = _load_xp(REPO_ROOT / timeline_source)
        xp_cache[timeline_source] = timeline_sprite
    timeline_frame_map = profile.get("timeline_frame_map")
    if not isinstance(timeline_frame_map, list):
        timeline_frame_map = profile["layers"][0]["frame_map"]
    if len(timeline_frame_map) < target_frames:
        _fail(f"{profile_id}: timeline frame_map shorter than profile frame count")
    target_frame_meta: list[FrameMetaTuple] = []

    for frame_index in range(target_frames):
        frame_cells: list[AnsiCellTuple] = [
            (32, TRANSPARENT, TRANSPARENT, 255)
        ] * (target_width * target_height)
        for layer, sprite, layer_index in resolved_layers:
            # FL-4162: an overlay folded into its base (multifold/swoosh composite) is
            # not pasted standalone -- the base layer renders the whole fold below.
            if layer.get("composited_into_body_layer_index") is not None:
                continue
            frame_map = layer["frame_map"]
            source_frame = int(frame_map[frame_index])
            multifold_overlays = layer.get("multifold_composite_overlay_indices")
            swoosh_index = layer.get("swoosh_composite_swoosh_layer_index")
            if multifold_overlays is not None:
                source_cells = sprite.frame_cells_multifold_composite(
                    layer_index, [int(i) for i in multifold_overlays], source_frame
                )
            elif swoosh_index is not None:
                source_cells = sprite.frame_cells_swoosh_composite(
                    layer_index, int(swoosh_index), source_frame
                )
            else:
                source_cells = sprite.frame_cells(
                    layer_index,
                    source_frame,
                )
            for y in range(sprite.frame_height):
                dst_offset = y * target_width
                src_offset = y * sprite.frame_width
                for x in range(sprite.frame_width):
                    dst_index = dst_offset + x
                    src = source_cells[src_offset + x]
                    frame_cells[dst_index] = _paste_cell(frame_cells[dst_index], src)
        target_cells.extend(frame_cells)
        target_frame_meta.append(timeline_sprite.frame_meta(int(timeline_frame_map[frame_index])))

    header = {
        "schema_id": "asciicker.actor_visual_profiles.cells.v1",
        "profile_id": profile_id,
        "key": profile["key"],
        "frames": target_frames,
        "width": target_width,
        "height": target_height,
        "cell_format": "uint32_glyph_u8_fg_u8_bg_spare",
        "source": "server_reachability_getsprite_loadsprite",
    }
    return header, target_cells, target_frame_meta


def _server_identity() -> dict[str, str]:
    text = SERVER_IDENTITY.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for name in (
        "kServerActorVisualReachabilityScopeId",
        "kServerActorVisualReachabilityHash",
        "kServerActorVisualCatalogHash",
    ):
        match = re.search(
            rf'static constexpr const char\*\s+{name}\s*=\s*"([^"]*)";',
            text,
        )
        if not match:
            _fail(f"missing {name} in {_repo_rel(SERVER_IDENTITY)}")
        out[name] = match.group(1)
    return out


def _source_xps(source_doc: dict[str, Any], profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del source_doc
    source_xps: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for source in source_xps:
        source_id = source.get("source_xp_id")
        if not isinstance(source_id, str) or not source_id:
            continue
        by_id[source_id] = source
    for profile in profiles:
        for layer in profile["layers"]:
            source_id = layer["source_xp_id"]
            if source_id not in by_id:
                by_id[source_id] = {
                    "source_xp_id": source_id,
                    "source_xp": layer["source_xp"],
                    "declared_layer_index": layer["layer_index"],
                    "source_kind": layer["source_kind"],
                }
        timeline_source_id = profile.get("timeline_source_xp_id")
        if isinstance(timeline_source_id, str) and timeline_source_id and timeline_source_id not in by_id:
            by_id[timeline_source_id] = {
                "source_xp_id": timeline_source_id,
                "source_xp": profile["timeline_source_xp"],
                "declared_layer_index": profile["layers"][0]["layer_index"],
                "source_kind": profile["timeline_source_kind"],
            }
    return list(by_id.values())


def _key_values(key: dict[str, Any]) -> list[int]:
    values = [
        key["skin_id"],
        key["actor_style_id"],
        key["presentation_kind_id"],
        key["variation_id"],
        key["mount_id"],
        key["rig_id"],
        key["head_item_id"],
        key["head_style_id"],
        key["chest_item_id"],
        key["chest_style_id"],
        key["weapon_item_id"],
        key["weapon_style_id"],
        key["shield_item_id"],
        key["shield_style_id"],
    ]
    values.extend(key["future_slot_kind_ids"])
    values.extend(key["future_item_ids"])
    values.extend(key["future_style_ids"])
    return values


def _cpp_u16_expr(value: int) -> str:
    if value == CROSSBOW_ITEM_ID:
        return "ACTOR_VISUAL_PROFILE_ITEM_WEAPON_CROSSBOW_ID"
    return str(value)


def _write_wrapped_ints(lines: list[str], values: list[int], indent: str = "    ") -> None:
    if not values:
        return
    for i in range(0, len(values), 16):
        chunk = ", ".join(str(v) for v in values[i : i + 16])
        suffix = "," if i + 16 < len(values) else ""
        lines.append(f"{indent}{chunk}{suffix}")


def _emit_generated_header(
    source_doc: dict[str, Any],
    profiles: list[dict[str, Any]],
    cell_entries: list[dict[str, Any]],
    catalog_profiles: list[dict[str, Any]],
) -> None:
    identity = _server_identity()
    source_bytes = _stable_json_bytes(source_doc)
    source_hash = _sha256_bytes(source_bytes)
    source_xps = _source_xps(source_doc, profiles)
    source_index = {source["source_xp_id"]: i for i, source in enumerate(source_xps)}
    ids_hash = _sha256_bytes(_stable_json_bytes([profile["id"] for profile in profiles]))
    source_xps_hash = _sha256_bytes(_stable_json_bytes(source_xps))
    semantic_sets = sorted(
        {
            tuple(sorted(layer["semantic_contributions"]))
            for profile in profiles
            for layer in profile["layers"]
        }
    )
    semantic_set_index = {values: index for index, values in enumerate(semantic_sets)}
    layer_manifest = [
        {
            "id": profile["id"],
            "layers": [
                {
                    "source_xp_id": layer["source_xp_id"],
                    "layer_index": layer["layer_index"],
                    "render_operations": layer["render_operations"],
                    "render_operation_mask": layer["render_operation_mask"],
                    "semantic_contributions": layer["semantic_contributions"],
                    "contract_review_unit_id": layer["contract_review_unit_id"],
                    "source_layer_sha256": layer["source_layer_sha256"],
                    "frame_map": layer["frame_map"],
                }
                for layer in profile["layers"]
            ],
        }
        for profile in profiles
    ]
    layer_hash = _sha256_bytes(_stable_json_bytes(layer_manifest))
    table_hash = _sha256_bytes(_stable_json_bytes({"source": source_hash, "layers": layer_hash}))
    # FL-4131/FL-4049 Stage A: compile-identity nullable glyph_manifest_hash
    # plus the paired content_pack_id. Both are runtime JoinV2 identity fields.
    # The material additive manifest is user-authored content, so the runtime
    # advertises its hash even when actor sprites remain CP437-only. Any
    # admitted extended sprite manifests join the same deterministic identity.
    glyph_manifest_hash = _compile_identity_glyph_manifest_hash(source_xps)
    content_pack_id = _compile_identity_content_pack_id(source_xps)
    # FL-4131 P10: LUT + page-chain identity for multiplayer join validation.
    lut_hash = _compile_identity_lut_hash()
    page_chain_hash = _compile_identity_page_chain_hash()

    source_label = source_doc.get("source") or "server_reachability_getsprite_loadsprite"
    lines: list[str] = [
        "#pragma once",
        "",
        "// Generated by scripts/compile_actor_visual_profiles.py.",
        f"// Source: {source_label}",
        "",
        '#include "actor_visual_profile.h"',
        "",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_COMPILED_TABLE_SHA256 = {_cpp_string(table_hash)};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_SOURCE_SHA256 = {_cpp_string(source_hash)};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_IDS_SHA256 = {_cpp_string(ids_hash)};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_SOURCE_XPS_SHA256 = {_cpp_string(source_xps_hash)};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_SERVER_REACHABILITY_ARTIFACT_SHA256 = {_cpp_string(_sha256_file(SERVER_REACHABILITY))};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_SOURCE_BINDINGS_SHA256 = {_cpp_string(_sha256_file(PROFILE_BINDINGS))};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_SEMANTIC_MASKS_SHA256 = {_cpp_string('all_visible')};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_CANVAS_CELL_MASKS_SHA256 = {_cpp_string('all_visible')};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_CELL_PARTITION_DECISIONS_SHA256 = {_cpp_string(layer_hash)};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_UPSTREAM_CONTRACT_FREEZE_SHA256 = {_cpp_string(_sha256_file(UPSTREAM_CONTRACT_FREEZE))};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_UPSTREAM_CELL_DECISIONS_SHA256 = {_cpp_string(_sha256_file(UPSTREAM_CONTRACT_DECISIONS))};",
        # FL-4131/FL-4049 Stage A: nullable glyph manifest hash. This is non-null
        # when user-authored material glyph content is bound into the runtime.
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256 = {_cpp_glyph_manifest_literal(glyph_manifest_hash)};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID = {_cpp_glyph_manifest_literal(content_pack_id)};",
        # FL-4131 P10: atlas runtime-identity hashes for the multiplayer join
        # handshake. lut_hash is the AOA glyph_index SHA-256; page chain hash
        # is the SHA-256 over (cell_px, page_hash) tuples sorted by cell_px.
        # Both are nullptr when no atlas is bound (CP437-only build).
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_LUT_SHA256 = {_cpp_glyph_manifest_literal(lut_hash)};",
        f"static constexpr const char* ACTOR_VISUAL_PROFILE_PAGE_ATLAS_CHAIN_SHA256 = {_cpp_glyph_manifest_literal(page_chain_hash)};",
        "",
        "static constexpr ActorVisualCompiledSourceXp kActorVisualSourceXps[] = {",
    ]
    for source in source_xps:
        kind = SOURCE_KIND_ENUM.get(source.get("source_kind"), "ACTOR_VISUAL_SOURCE_XP_KIND_DERIVED_SINGLEROLE")
        lines.append(
            "    {"
            f"{_cpp_string(source['source_xp_id'])}, "
            f"{_cpp_string(source['source_xp'])}, "
            f"{int(source.get('declared_layer_index', 2))}, "
            f"{kind}"
            "},"
        )
    lines.extend(
        [
            "};",
            "static constexpr int kActorVisualSourceXpCount = sizeof(kActorVisualSourceXps) / sizeof(kActorVisualSourceXps[0]);",
            "",
        ]
    )
    for index, values in enumerate(semantic_sets):
        if not values:
            continue
        lines.append(
            f"static constexpr const char* kActorVisualSemanticContributionValues_{index}[] = {{"
        )
        for value in values:
            lines.append(f"    {_cpp_string(value)},")
        lines.append("};")
    lines.extend(
        [
            "static constexpr ActorVisualSemanticContributionSet kActorVisualSemanticContributionSets[] = {",
        ]
    )
    for index, values in enumerate(semantic_sets):
        pointer = f"kActorVisualSemanticContributionValues_{index}" if values else "nullptr"
        lines.append(f"    {{{pointer}, {len(values)}}},")
    lines.extend(
        [
            "};",
            "static constexpr int kActorVisualSemanticContributionSetCount = sizeof(kActorVisualSemanticContributionSets) / sizeof(kActorVisualSemanticContributionSets[0]);",
            "",
            "static constexpr ActorVisualCompiledSemanticMask kActorVisualSemanticMasks[] = {",
            '    {"all_visible", ACTOR_VISUAL_SEMANTIC_MASK_METHOD_ALL_VISIBLE, 0, 0xffff, 0, 0, 0},',
            "};",
            "static constexpr int kActorVisualSemanticMaskCount = sizeof(kActorVisualSemanticMasks) / sizeof(kActorVisualSemanticMasks[0]);",
            "",
            "static constexpr ActorVisualCompiledCanvasCellMask kActorVisualCanvasCellMasks[] = {",
            "    {0, 0},",
            "};",
            "static constexpr int kActorVisualCanvasCellMaskCount = sizeof(kActorVisualCanvasCellMasks) / sizeof(kActorVisualCanvasCellMasks[0]);",
            "",
            "static constexpr CompiledActorVisualTableHeader kCompiledActorVisualTableHeader = {",
            "    ACTOR_VISUAL_COMPILED_SCHEMA_VERSION,",
            "    ACTOR_VISUAL_COMPILER_CAPABILITY_VERSION,",
            f"    {_cpp_string(source_xps_hash)},",
            f"    {_cpp_string('all_visible')},",
            f"    {_cpp_string('all_visible')},",
            f"    {_cpp_string(layer_hash)},",
            f"    {_cpp_string(identity['kServerActorVisualCatalogHash'])},",
            f"    {_cpp_string(identity['kServerActorVisualReachabilityScopeId'])},",
            f"    {_cpp_string(identity['kServerActorVisualReachabilityHash'])},",
            f"    {_cpp_string(table_hash)},",
            '    "local",',
            '    "server-reachability-upstream-resolver",',
            "};",
            "",
        ]
    )

    layer_symbols: list[str] = []
    for profile_index, profile in enumerate(profiles):
        layer_symbol = f"kCompiledActorVisualRowLayers_{profile_index}"
        layer_symbols.append(layer_symbol)
        for layer_index, layer in enumerate(profile["layers"]):
            map_symbol = f"kCompiledActorVisualRowLayerFrameMap_{profile_index}_{layer_index}"
            lines.append(f"static constexpr uint16_t {map_symbol}[] = {{")
            _write_wrapped_ints(lines, [int(v) for v in layer["frame_map"]])
            lines.append("};")
        lines.append(f"static constexpr CompiledActorVisualLayer {layer_symbol}[] = {{")
        for order, layer in enumerate(profile["layers"]):
            map_symbol = f"kCompiledActorVisualRowLayerFrameMap_{profile_index}_{order}"
            source = source_index[layer["source_xp_id"]]
            required = "true" if layer.get("required") else "false"
            contribution_set = semantic_set_index[tuple(sorted(layer["semantic_contributions"]))]
            lines.append(
                "    {"
                f"{order}, {int(layer['render_operation_mask'])}, "
                f"{int(layer['layer_index'])}, {required}, {source}, "
                f"{contribution_set}, 0, 0, {map_symbol}, {len(layer['frame_map'])}"
                "},"
            )
        lines.append("};")
        lines.append("")

    lines.append("static constexpr CompiledActorVisualRow kCompiledActorVisualRows[] = {")
    for profile, layer_symbol in zip(profiles, layer_symbols):
        key = _key_values(profile["key"])
        timeline_source = source_index[profile["timeline_source_xp_id"]]
        playback = PLAYBACK_ENUM[profile["playback"]]
        locomotion = ", ".join(str(int(v)) for v in profile["locomotion_anim_track"])
        lines.append("    {")
        lines.append(f"        {_cpp_string(profile['id'])},")
        lines.append("        {")
        scalar_values = key[:14]
        for value in scalar_values:
            lines.append(f"            {_cpp_u16_expr(value)},")
        for start in (14, 18, 22):
            lines.append(
                "            {"
                + ", ".join(_cpp_u16_expr(v) for v in key[start : start + 4])
                + "},"
            )
        lines.append("        },")
        lines.append(f"        {timeline_source},")
        lines.append(f"        {playback},")
        lines.append(f"        {int(profile['steady_frame_index'])},")
        lines.append(f"        {len(profile['layers'])},")
        lines.append(f"        {{{locomotion}}},")
        lines.append(f"        {layer_symbol},")
        lines.append("    },")
    lines.extend(
        [
            "};",
            "static constexpr int kCompiledActorVisualRowCount = sizeof(kCompiledActorVisualRows) / sizeof(kCompiledActorVisualRows[0]);",
            "",
        ]
    )
    for index, profile in enumerate(profiles):
        anim_lens = ", ".join(str(int(v)) for v in profile["anim_lens"])
        lines.append(f"static constexpr uint16_t kCompiledActorVisualCellPayloadAnimLens_{index}[] = {{{anim_lens}}};")
    lines.append("")
    for index, cell_entry in enumerate(cell_entries):
        lines.append(f"static constexpr CompiledActorVisualCell kCompiledActorVisualCells_{index}[] = {{")
        cells = cell_entry["cells"]
        for i in range(0, len(cells), 4):
            chunk = ", ".join(
                f"{{{int(glyph)}u, {int(fg)}, {int(bg)}, {int(spare)}}}"
                for glyph, fg, bg, spare in cells[i : i + 4]
            )
            lines.append(f"    {chunk},")
        lines.append("};")
        lines.append(f"static constexpr CompiledActorVisualFrameMeta kCompiledActorVisualFrameMeta_{index}[] = {{")
        for ref, meta_xy in cell_entry["frame_meta"]:
            lines.append(
                "    {{"
                + f"{int(ref[0])}, {int(ref[1])}, {int(ref[2])}"
                + "}, {"
                + f"{int(meta_xy[0])}, {int(meta_xy[1])}"
                + "}},"
            )
        lines.append("};")
    lines.append("")
    lines.append("static constexpr CompiledActorVisualCellPayload kCompiledActorVisualCellPayloads[] = {")
    for index, (profile, cell_entry) in enumerate(zip(profiles, cell_entries)):
        lines.append(
            "    {"
            + f"{_cpp_string(profile['id'])}, "
            + f"{int(cell_entry['width'])}, "
            + f"{int(cell_entry['height'])}, "
            + f"{int(cell_entry['frames'])}, "
            + f"{int(profile['angles'])}, "
            + f"{int(profile['projs'])}, "
            + f"{len(profile['anim_lens'])}, "
            + f"kCompiledActorVisualCellPayloadAnimLens_{index}, "
            + f"kCompiledActorVisualFrameMeta_{index}, "
            + f"kCompiledActorVisualCells_{index}"
            + "},"
        )
    lines.extend(
        [
            "};",
            "static constexpr int kCompiledActorVisualCellPayloadCount = sizeof(kCompiledActorVisualCellPayloads) / sizeof(kCompiledActorVisualCellPayloads[0]);",
            "static_assert(kCompiledActorVisualCellPayloadCount == kCompiledActorVisualRowCount, \"cell payload table must match visual row table\");",
            "",
        ]
    )
    for catalog_profile in catalog_profiles:
        profile_id = int(catalog_profile["id"])
        lines.append(
            f"static constexpr ActorVisualSlot kActorVisualCatalogProfileStarter_{profile_id}[] = {{"
        )
        starters = catalog_profile["starter_entries"]
        if starters:
            for starter in starters:
                lines.append(
                    "    {"
                    + f"{int(starter['slot_kind_id'])}, "
                    + f"{int(starter['item_definition_id'])}, "
                    + f"{int(starter['visual_style_id'])}, "
                    + f"{int(starter['state_flags'])}"
                    + "},"
                )
        else:
            lines.append("    {0, 0, 0, 0},")
        lines.extend(["};", ""])
    lines.append("static constexpr ActorVisualCatalogProfile kActorVisualCatalogProfiles[] = {")
    for catalog_profile in catalog_profiles:
        profile_id = int(catalog_profile["id"])
        lines.append(
            "    {"
            + f"{profile_id}, {int(catalog_profile['skin_definition_id'])}, "
            + f"{_cpp_string(catalog_profile['slug'])}, "
            + f"{len(catalog_profile['starter_entries'])}, "
            + f"kActorVisualCatalogProfileStarter_{profile_id}"
            + "},"
        )
    lines.extend(
        [
            "};",
            "static constexpr int kActorVisualCatalogProfileCount = sizeof(kActorVisualCatalogProfiles) / sizeof(kActorVisualCatalogProfiles[0]);",
            "",
            "static constexpr ActorVisualCatalogSeat kActorVisualCatalogSeats[] = {",
            "};",
            "static constexpr int kActorVisualCatalogSeatCount = 0;",
            "",
            "static constexpr ActorVisualCatalogItem kActorVisualCatalogItems[] = {",
            '    {402, 302, 0, 4, "shield_item", "assets/sprites/item-shield.xp", "assets/sprites/item-shield.xp"},',
            '    {409, 303, 0, 1, "normal_sword", "assets/sprites/item-sword.xp", "assets/sprites/item-sword.xp"},',
            '    {410, 301, 0, 4, "normal_helmet", "assets/sprites/item-helmet.xp", "assets/sprites/item-helmet.xp"},',
            '    {411, 306, 0, 4, "normal_armour", "assets/sprites/item-armor.xp", "assets/sprites/item-armor.xp"},',
            '    {412, 307, 950, 5, "wolf_mountable", "assets/sprites/wolfie.xp", "assets/sprites/wolfie.xp"},',
            '    {413, 307, 951, 5, "bee_mountable", "assets/sprites/bigbee.xp", "assets/sprites/bigbee.xp"},',
            '    {ACTOR_VISUAL_PROFILE_ITEM_WEAPON_CROSSBOW_ID, 303, 0, 1, "weapon_crossbow", "assets/sprites/item-crossbow.xp", "assets/sprites/item-crossbow.xp"},',
            "};",
            "static constexpr int kActorVisualCatalogItemCount = sizeof(kActorVisualCatalogItems) / sizeof(kActorVisualCatalogItems[0]);",
            "",
            "static constexpr ActorVisualCatalogMount kActorVisualCatalogMounts[] = {",
            '    {950, 1, "wolf_mount"},',
            '    {951, 2, "bee_mount"},',
            "};",
            "static constexpr int kActorVisualCatalogMountCount = sizeof(kActorVisualCatalogMounts) / sizeof(kActorVisualCatalogMounts[0]);",
            "",
        ]
    )
    GENERATED_HEADER.write_text("\n".join(lines), encoding="utf-8")


def emit_current() -> None:
    deletion_evidence = _require_deleted_legacy_owners()
    freeze, _ = _require_frozen_upstream_contract()
    reachability_doc, reachable_keys = _server_reachable_keys()
    catalog_profiles = _server_catalog_profiles(reachability_doc)
    bindings = _profile_bindings(reachability_doc, reachable_keys)
    profiles = []
    provenance = []
    for key in reachable_keys:
        profile, profile_provenance = _profile_for_binding(
            key, bindings[_key_sort_tuple(key)]
        )
        profiles.append(profile)
        provenance.append(profile_provenance)
    source_doc = {
        "schema_id": "asciicker.actor_visual_profiles.synthetic.v2",
        "source": "server reachability plus frozen upstream and target-authored source contracts",
        "pinned_upstream_commit": PINNED_UPSTREAM_COMMIT,
        "row_count": len(profiles),
        "server_reachability_hash": reachability_doc["server_reachability_hash"],
        "upstream_contract_freeze_sha256": _sha256_file(UPSTREAM_CONTRACT_FREEZE),
        "custom_source_contract_sha256": _sha256_file(CUSTOM_SOURCE_CONTRACT),
        "catalog_profiles": catalog_profiles,
        "compile_rule": "exact server C++ reachable keys bind to hash-verified authored XP contracts",
    }
    xp_cache: dict[str, XpSprite] = {}
    cell_entries = []
    for profile in profiles:
        cell_header, cells, frame_meta = _compile_profile_cells(profile, xp_cache)
        cell_entries.append(
            {
                "profile_id": profile["id"],
                "frames": cell_header["frames"],
                "width": cell_header["width"],
                "height": cell_header["height"],
                "frame_meta": frame_meta,
                "cells": cells,
            }
        )
    _emit_generated_header(source_doc, profiles, cell_entries, catalog_profiles)
    GENERATED_PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_PROVENANCE.write_text(
        json.dumps(
            {
                "schema_id": "asciicker.actor_visual_profiles.provenance.v1",
                "pinned_upstream_commit": PINNED_UPSTREAM_COMMIT,
                "custom_source_contract_sha256": _sha256_file(CUSTOM_SOURCE_CONTRACT),
                "row_count": len(provenance),
                "rows": provenance,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    source_ids = sorted(
        {
            str(row.get("source_xp_id") or "")
            for row in bindings.values()
            if row.get("source_xp_id")
        }
    )
    cutover_receipt = {
        "schema": UPSTREAM_CONTRACT_CUTOVER_SCHEMA,
        "fl": "FL-4162",
        "rq": "RQ-200",
        "compiler_cutover_complete": True,
        "runtime_authoritative": True,
        "authority_owner": "compiled_actor_visual_profile_table",
        "contract_authority": freeze["contract_authority"],
        "coverage": {
            "server_reachable_keys": len(reachable_keys),
            "compiled_rows": len(profiles),
            "bound_source_ids": len(source_ids),
            "frozen_raw_layers": int(freeze["coverage"]["raw_layers"]),
            "frozen_raw_cells": int(freeze["coverage"]["raw_cells"]),
            "pending_review_units": int(
                freeze["coverage"]["pending_review_units"]
            ),
        },
        "source_hashes": {
            "family_contract_freeze_sha256": _sha256_file(
                UPSTREAM_CONTRACT_FREEZE
            ),
            "cell_role_decisions_sha256": _sha256_file(
                UPSTREAM_CONTRACT_DECISIONS
            ),
            "server_reachable_keys_sha256": _sha256_file(SERVER_REACHABILITY),
            "profile_bindings_sha256": _sha256_file(PROFILE_BINDINGS),
            "custom_source_contract_sha256": _sha256_file(
                CUSTOM_SOURCE_CONTRACT
            ),
            "generated_table_sha256": _sha256_file(GENERATED_HEADER),
            "generated_provenance_sha256": _sha256_file(GENERATED_PROVENANCE),
            "compiler_source_sha256": _sha256_file(Path(__file__)),
        },
        "legacy_owner_deletion": deletion_evidence,
        "invariants": {
            "render_operation_separate_from_semantic_contributions": True,
            "composite_semantic_contribution_sets_first_class": True,
            "server_catalog_is_reachability_owner": True,
            "local_ahsw_resolution_deleted": True,
            "local_reachability_enumeration_deleted": True,
            "per_key_legacy_merge_fallback_deleted": True,
        },
    }
    UPSTREAM_CONTRACT_CUTOVER.write_text(
        json.dumps(cutover_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args(argv)

    emit_current()
    print(f"compile_actor_visual_profiles: wrote {GENERATED_HEADER.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
