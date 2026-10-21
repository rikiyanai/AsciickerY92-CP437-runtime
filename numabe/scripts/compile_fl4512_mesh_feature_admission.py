# =============================================================================
# compile_fl4512_mesh_feature_admission.py
# =============================================================================
# SCENE-INDEPENDENT, ONE-TIME offline compiler for the FL-4512 mesh-feature
# structural-glyph admission artifact — STRICT edition.
#
# WHAT IT DOES (plain words):
#   The FL-4512 engine draws mesh silhouettes/contours as small stroke glyphs.
#   Out of the universal Unicode one-cell glyph catalog it must pick a fixed set
#   of exactly 32 stroke glyphs (a "legal row") that best approximate every short
#   stroke a mesh feature can project onto one 8x8 terminal cell. This compiler
#   builds that pick, ONCE, offline, as a checked-in JSON artifact:
#
#     universal Unicode catalog  ─┐
#                                 ├─> STRICT validate + join + derive topology ─┐
#     native atlas registry      ─┘                                             │
#                                                                               v
#                        fl4512_mesh_feature_candidate_admission_v2.admit_row
#                          (the SOLE v3 exact 32-row selector)
#                                                                               │
#                                                                               v
#                   canonical deterministic artifact JSON (atomic write)
#
#   STRICT means: every ambiguous or malformed input class FAILS SHUT with a
#   stable typed reason, and the artifact is bound to its sources so a re-hashed
#   but semantically divergent artifact cannot pass validation. Specifically:
#
#     * Both source objects must carry EXACTLY their expected top-level key set,
#       declared schema, and (registry) a self-consistent content hash; the
#       catalog manifest hash must be a 64-hex string.
#     * Every catalog entry and every atlas binding must carry EXACTLY its
#       expected key set.
#     * GlyphIds must be unique, integer, and NOT booleans, in BOTH the catalog
#       and the registry (there is no "first wins" — a duplicate is fatal).
#     * mask8 must be "0x" + exactly 16 lowercase hex digits (a uint64).
#     * Every atlas-binding scalar must be a non-boolean integer that lies inside
#       the bounds DERIVED FROM the registry's own declared page/pack topology
#       (the (layer,pack,page) triple must name a real page, the column must fit
#       the page width, the row must fit the page's row count).
#     * The registry's declared binding_count must equal the actual array length.
#
#   admit_row remains the ONE and ONLY selector. Validation may rebuild
#   deterministic projections (supply hash, receipt content hash, selected-row
#   atlas join) but it NEVER becomes a second selector — the fast real-source
#   validation does not re-run the full-supply optimum search.
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

# The compiler lives at <root>/scripts/. Make sibling modules importable so the
# admission helper and the canonical u7 topology are the exact same code the
# rest of the FL-4512 pipeline uses.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fl4512_mesh_feature_candidate_admission_v2 as ADM  # noqa: E402
from fl4512_u7_mask import topology_counts, u64_le_hex     # noqa: E402  (u64_le_hex kept for callers)

# --------------------------------------------------------------------------- #
# Fixed identities and input/output locations. No implicit defaults elsewhere.
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.shape_catalog.json"
REGISTRY_PATH = ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"
# One stable product artifact address remains because downstream compilers read
# this path. The wire schema, generator, grammar, and receipt are v3; no v2
# reader, writer branch, or mirror survives this migration.
ARTIFACT_PATH = ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json"

GENERATOR_ID = "fl4512.mesh_feature_admission_compiler.v3"
ARTIFACT_SCHEMA = "fl4512.mesh_feature_candidate_admission_artifact.v3"
CATALOG_SCHEMA = "unicode16.onecell.shape_catalog.v3"
REGISTRY_SCHEMA = "fl4482.native_atlas_registry.v2"
EXPECTED_SCORER = "u7.mask.v1"

# EXACT expected key sets. A source/artifact object with a missing OR extra key
# is rejected — this is what closes the "re-hashed divergent artifact" class.
CATALOG_TOP_KEYS = frozenset({
    "content_pack_id", "entries", "manifest_hash",
    "page16_sha256_by_page", "schema", "scorer_id",
})
CATALOG_ENTRY_KEYS = frozenset({
    "endpoint_count", "font_index", "glyph_id", "junction_cluster_count",
    "mask8_u64", "page16_tile_sha256", "scorer_id", "unicode_scalar",
})
REGISTRY_TOP_KEYS = frozenset({
    "binding_count", "bindings", "cell_px", "columns", "lut_mapping",
    "packs", "page_count", "pages", "proof_state", "registry_sha256",
    "runtime_consumed", "schema",
})
BINDING_KEYS = frozenset({
    "glyph_id", "layer_index", "pack_index", "page_index",
    "source_column", "source_row",
})
# The five ordered atlas-binding coordinate fields joined onto each selected
# glyph (glyph_id is carried separately as the row key).
ATLAS_BINDING_FIELDS = (
    "layer_index", "pack_index", "page_index", "source_column", "source_row",
)
ARTIFACT_TOP_KEYS = frozenset({
    "schema", "generator_id", "source", "counts", "grammar_id",
    "grammar_hash", "selected_row", "receipt", "artifact_content_hash",
})
ARTIFACT_SOURCE_KEYS = frozenset({
    "catalog_path", "catalog_schema", "catalog_declared_manifest_hash",
    "catalog_source_sha256", "registry_path", "registry_schema",
    "registry_declared_sha256", "registry_source_sha256",
})
ARTIFACT_COUNTS_KEYS = frozenset({
    "catalog_entry_count", "nonzero_supply_count",
    "distinct_signature_count", "atlas_binding_count",
})
SELECTED_ROW_KEYS = frozenset({"glyph_id"} | set(ATLAS_BINDING_FIELDS))

# The embedded v3 receipt's EXACT key set, and the EXACT key set of every item
# in its per_target list. The fast validator rebuilds the whole receipt as the
# canonical projection of its selected row and compares every one of these keys.
RECEIPT_KEYS = frozenset({
    "schema", "grammar_id", "grammar_hash", "supply_hash", "scorer_id",
    "row_capacity", "supply_count", "distinct_signature_count",
    "selected_glyph_ids", "selected_descriptor_hashes", "per_target",
    "worst_score", "score_sum", "mean_numerator", "mean_denominator",
    "boundary_contact_coverage", "selection_algorithm",
    "legacy_worst_score", "legacy_worst_bound", "selection_iterations",
    "selection_swap_count", "selection_exact_u7_operations",
    "selection_modeled_live_tensor_bytes", "selection_memory_cap_bytes",
    "selection_actual_rss_measured", "selection_actual_rss_status",
    "content_hash",
})
PER_TARGET_KEYS = frozenset({"nearest_winner_glyph_id", "nearest_score"})

# String-shape patterns.
HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")   # sha256 / manifest hashes
MASK8_RE = re.compile(r"\A0x[0-9a-f]{16}\Z")  # "0x" + uint64, lowercase


class CompileError(Exception):
    """Raised on any malformed input, ambiguity, failed validation, or tamper.

    `reason` is one of the stable typed reason codes below so tests and callers
    can branch on the exact failure class without matching prose.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# Stable typed reason codes. Never rename/renumber an existing one.
REASON_MALFORMED_SOURCE = "malformed_source"
# The supplied decoded document diverges from the parse of its supplied raw bytes
# (a transaction must not hash one byte document while projecting another object).
REASON_CATALOG_DOC_DIVERGENCE = "catalog_document_divergence"
REASON_REGISTRY_DOC_DIVERGENCE = "registry_document_divergence"
REASON_CATALOG_SCHEMA = "catalog_schema_mismatch"
REASON_CATALOG_SCORER = "catalog_scorer_mismatch"
REASON_REGISTRY_SCHEMA = "registry_schema_mismatch"
REASON_REGISTRY_HASH = "registry_hash_mismatch"
REASON_ATLAS_JOIN_MISSING = "atlas_join_missing"
REASON_SOURCE_HASH_MISMATCH = "source_hash_mismatch"
REASON_ARTIFACT_HASH_MISMATCH = "artifact_hash_mismatch"
REASON_SELECTED_BINDING_MISMATCH = "selected_binding_mismatch"
REASON_ARTIFACT_SCHEMA_MISMATCH = "artifact_schema_mismatch"
REASON_COUNTS_MISMATCH = "counts_mismatch"
# Strict additions.
REASON_CATALOG_KEY_SET = "catalog_key_set_mismatch"
REASON_REGISTRY_KEY_SET = "registry_key_set_mismatch"
REASON_CATALOG_ENTRY_KEY_SET = "catalog_entry_key_set_mismatch"
REASON_BINDING_KEY_SET = "binding_key_set_mismatch"
REASON_MANIFEST_HASH_SHAPE = "manifest_hash_shape_invalid"
REASON_BINDING_COUNT_MISMATCH = "binding_count_mismatch"
REASON_GLYPH_ID_NOT_INT = "glyph_id_not_int"
REASON_DUPLICATE_CATALOG_GLYPH_ID = "duplicate_catalog_glyph_id"
REASON_DUPLICATE_REGISTRY_GLYPH_ID = "duplicate_registry_glyph_id"
REASON_MASK_FORMAT = "mask_format_invalid"
REASON_BINDING_SCALAR_NOT_INT = "binding_scalar_not_int"
REASON_BINDING_SCALAR_OUT_OF_RANGE = "binding_scalar_out_of_range"
REASON_PAGE_TOPOLOGY_INVALID = "page_topology_invalid"
REASON_ARTIFACT_UNKNOWN_FIELD = "artifact_key_set_mismatch"
REASON_GENERATOR_MISMATCH = "generator_id_mismatch"
REASON_GRAMMAR_ID_MISMATCH = "grammar_id_mismatch"
REASON_GRAMMAR_HASH_MISMATCH = "grammar_hash_mismatch"
REASON_SOURCE_IDENTITY_MISMATCH = "source_identity_mismatch"
# Strict receipt-projection reason codes (replacing the single permissive one).
REASON_RECEIPT_KEY_SET = "receipt_key_set_mismatch"
REASON_PER_TARGET_KEY_SET = "per_target_key_set_mismatch"
REASON_RECEIPT_IDENTITY_MISMATCH = "receipt_identity_mismatch"
REASON_RECEIPT_SUPPLY_MISMATCH = "receipt_supply_mismatch"
REASON_SELECTED_IDS_INVALID = "selected_ids_invalid"
REASON_DUPLICATE_SELECTED_GLYPH_ID = "duplicate_selected_glyph_id"
REASON_SELECTED_ORDER_MISMATCH = "selected_order_mismatch"
REASON_DESCRIPTOR_HASH_MISMATCH = "descriptor_hash_mismatch"
REASON_PER_TARGET_WINNER_MISMATCH = "per_target_winner_mismatch"
REASON_PER_TARGET_SCORE_MISMATCH = "per_target_score_mismatch"
REASON_AGGREGATE_MISMATCH = "aggregate_mismatch"
REASON_BOUNDARY_MISMATCH = "boundary_mismatch"
REASON_RECEIPT_CONTENT_HASH = "receipt_content_hash_mismatch"


# --------------------------------------------------------------------------- #
# Canonical bytes + hashing. Reuse the helper's ONE canonical JSON serializer so
# the artifact's byte stream is consistent with the receipt it embeds.
# --------------------------------------------------------------------------- #
def canonical_json_bytes(obj) -> bytes:
    return ADM.canonical_json_bytes(obj)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_source_bytes(path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise CompileError(REASON_MALFORMED_SOURCE, f"{path}: {exc}") from exc


def _parse_json(raw: bytes, path) -> dict:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CompileError(REASON_MALFORMED_SOURCE, f"{path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise CompileError(REASON_MALFORMED_SOURCE, f"{path}: not a JSON object")
    return obj


# --------------------------------------------------------------------------- #
# Strict scalar helpers.
# --------------------------------------------------------------------------- #
def _require_int(value, reason: str, detail: str = ""):
    """Return `value` if it is a real integer. A Python bool is a subclass of
    int, so we reject it EXPLICITLY first — True/False must never masquerade as
    a GlyphId or a coordinate."""
    if isinstance(value, bool):
        raise CompileError(reason, f"{detail} bool")
    if not isinstance(value, int):
        raise CompileError(reason, f"{detail} {type(value).__name__}")
    return value


def _require_exact_keys(obj: dict, expected: frozenset, reason: str) -> None:
    """Reject an object whose key set is not EXACTLY `expected` (missing OR
    extra). This is the core anti-ambiguity guard."""
    keys = frozenset(obj.keys())
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise CompileError(reason, f"missing={missing} extra={extra}")


# --------------------------------------------------------------------------- #
# Source identity validation.
# --------------------------------------------------------------------------- #
def _registry_declared_identity(reg: dict) -> str:
    """Recompute the registry's self-declared content identity.

    The fl4482 registry seals `registry_sha256` = sha256 of its own canonical
    JSON body with that one field removed (compact separators, sorted keys). We
    recompute it exactly so a tampered registry body is rejected."""
    body = {k: v for k, v in reg.items() if k != "registry_sha256"}
    return _sha256_hex(canonical_json_bytes(body))


def _validate_catalog(cat: dict) -> None:
    _require_exact_keys(cat, CATALOG_TOP_KEYS, REASON_CATALOG_KEY_SET)
    if cat.get("schema") != CATALOG_SCHEMA:
        raise CompileError(REASON_CATALOG_SCHEMA, str(cat.get("schema")))
    if cat.get("scorer_id") != EXPECTED_SCORER:
        raise CompileError(REASON_CATALOG_SCORER, str(cat.get("scorer_id")))
    mh = cat.get("manifest_hash")
    if not (isinstance(mh, str) and HEX64_RE.match(mh)):
        raise CompileError(REASON_MANIFEST_HASH_SHAPE, str(mh))
    if not isinstance(cat.get("entries"), list) or not cat["entries"]:
        raise CompileError(REASON_MALFORMED_SOURCE, "catalog entries absent")


def _validate_registry(reg: dict) -> None:
    _require_exact_keys(reg, REGISTRY_TOP_KEYS, REASON_REGISTRY_KEY_SET)
    if reg.get("schema") != REGISTRY_SCHEMA:
        raise CompileError(REASON_REGISTRY_SCHEMA, str(reg.get("schema")))
    if not isinstance(reg.get("bindings"), list) or not reg["bindings"]:
        raise CompileError(REASON_MALFORMED_SOURCE, "registry bindings absent")
    bc = reg.get("binding_count")
    if _require_int(bc, REASON_BINDING_COUNT_MISMATCH, "binding_count") != len(reg["bindings"]):
        raise CompileError(REASON_BINDING_COUNT_MISMATCH,
                           f"declared={bc} actual={len(reg['bindings'])}")
    declared = reg.get("registry_sha256")
    if declared != _registry_declared_identity(reg):
        raise CompileError(REASON_REGISTRY_HASH, str(declared))


def _build_page_topology(reg: dict) -> dict:
    """Map (layer_index, pack_index, page_index) -> (col_count, row_count) from
    the registry's DECLARED page grid, so binding coordinates can be bounded by
    the real atlas topology and nothing else."""
    cell = _require_int(reg.get("cell_px"), REASON_PAGE_TOPOLOGY_INVALID, "cell_px")
    if cell <= 0:
        raise CompileError(REASON_PAGE_TOPOLOGY_INVALID, "cell_px<=0")
    pages = reg.get("pages")
    if not isinstance(pages, list) or not pages:
        raise CompileError(REASON_PAGE_TOPOLOGY_INVALID, "pages absent")
    topo: dict = {}
    for pg in pages:
        if not isinstance(pg, dict):
            raise CompileError(REASON_PAGE_TOPOLOGY_INVALID, "page not object")
        for f in ("layer_index", "pack_index", "page_index", "width_px", "height_px"):
            if f not in pg:
                raise CompileError(REASON_PAGE_TOPOLOGY_INVALID, f"page missing {f}")
        li = _require_int(pg["layer_index"], REASON_PAGE_TOPOLOGY_INVALID, "layer_index")
        pi = _require_int(pg["pack_index"], REASON_PAGE_TOPOLOGY_INVALID, "pack_index")
        gi = _require_int(pg["page_index"], REASON_PAGE_TOPOLOGY_INVALID, "page_index")
        w = _require_int(pg["width_px"], REASON_PAGE_TOPOLOGY_INVALID, "width_px")
        h = _require_int(pg["height_px"], REASON_PAGE_TOPOLOGY_INVALID, "height_px")
        # Columns/rows come from the page pixel size divided by the cell size.
        topo[(li, pi, gi)] = (w // cell, h // cell)
    return topo


def _check_binding_bounds(b: dict, topo: dict) -> None:
    """Every binding scalar must be a non-bool integer, and the coordinate must
    address a real page cell within that page's declared grid."""
    _require_exact_keys(b, BINDING_KEYS, REASON_BINDING_KEY_SET)
    for f in ATLAS_BINDING_FIELDS:
        _require_int(b[f], REASON_BINDING_SCALAR_NOT_INT, f)
    triple = (b["layer_index"], b["pack_index"], b["page_index"])
    grid = topo.get(triple)
    if grid is None:
        raise CompileError(REASON_BINDING_SCALAR_OUT_OF_RANGE, f"no page {triple}")
    cols, rows = grid
    if not (0 <= b["source_column"] < cols):
        raise CompileError(REASON_BINDING_SCALAR_OUT_OF_RANGE,
                           f"col {b['source_column']} not in [0,{cols})")
    if not (0 <= b["source_row"] < rows):
        raise CompileError(REASON_BINDING_SCALAR_OUT_OF_RANGE,
                           f"row {b['source_row']} not in [0,{rows})")


def _build_atlas_index(reg: dict, topo: dict) -> dict:
    """glyph_id -> its canonical atlas binding (fixed field order).

    STRICT: GlyphIds must be unique non-bool integers, every binding must pass
    the exact-key + bounds check. There is NO first-wins fallback — a duplicate
    GlyphId is fatal (REASON_DUPLICATE_REGISTRY_GLYPH_ID)."""
    index: dict = {}
    for b in reg["bindings"]:
        if not isinstance(b, dict):
            raise CompileError(REASON_MALFORMED_SOURCE, "binding not object")
        gid = _require_int(b.get("glyph_id"), REASON_GLYPH_ID_NOT_INT, "registry glyph_id")
        _check_binding_bounds(b, topo)
        if gid in index:
            raise CompileError(REASON_DUPLICATE_REGISTRY_GLYPH_ID, f"gid={gid}")
        index[gid] = {f: b[f] for f in ATLAS_BINDING_FIELDS}
    return index


def _build_supply(cat: dict, atlas_index: dict) -> list:
    """Build the compiler-admitted supply from the catalog:

    - REJECT any entry with the wrong key set, a non-int/bool GlyphId, a
      duplicate GlyphId, or a malformed mask (must be 0x + 16 lowercase hex).
    - EXCLUDE zero-mask entries (empty glyphs like space are not structural).
    - JOIN every kept glyph_id to a native atlas binding, FAIL-SHUT (a missing
      binding aborts the whole compile — no silent drop).
    - DERIVE the u7 topology facts (endpoint/junction) from the mask itself via
      the shared thinner — never from a scene, camera, or runtime.
    """
    supply: list = []
    seen: set = set()
    for e in cat["entries"]:
        if not isinstance(e, dict):
            raise CompileError(REASON_MALFORMED_SOURCE, "entry not object")
        _require_exact_keys(e, CATALOG_ENTRY_KEYS, REASON_CATALOG_ENTRY_KEY_SET)
        gid = _require_int(e.get("glyph_id"), REASON_GLYPH_ID_NOT_INT, "catalog glyph_id")
        if gid in seen:
            raise CompileError(REASON_DUPLICATE_CATALOG_GLYPH_ID, f"gid={gid}")
        seen.add(gid)
        raw_mask = e.get("mask8_u64")
        if not (isinstance(raw_mask, str) and MASK8_RE.match(raw_mask)):
            raise CompileError(REASON_MASK_FORMAT, str(raw_mask))
        mask = int(raw_mask, 16)
        if mask == 0:
            continue  # exclude zero mask
        if gid not in atlas_index:
            raise CompileError(REASON_ATLAS_JOIN_MISSING, f"gid={gid}")
        _sk, endpoint_count, junction_count = topology_counts(mask)
        supply.append({
            "glyph_id": gid,
            "mask8": mask,
            "endpoint_count": endpoint_count,
            "junction_count": junction_count,
        })
    return supply


# --------------------------------------------------------------------------- #
# Compile.
# --------------------------------------------------------------------------- #
def _load_validated_sources(catalog_path, registry_path):
    """Read + strictly validate both sources; return everything downstream needs
    (parsed objects, raw bytes, atlas index, page topology)."""
    cat_bytes = _load_source_bytes(catalog_path)
    reg_bytes = _load_source_bytes(registry_path)
    cat = _parse_json(cat_bytes, catalog_path)
    reg = _parse_json(reg_bytes, registry_path)
    _validate_catalog(cat)
    _validate_registry(reg)
    topo = _build_page_topology(reg)
    atlas_index = _build_atlas_index(reg, topo)
    return cat, reg, cat_bytes, reg_bytes, atlas_index


def compile_artifact(catalog_path=CATALOG_PATH, registry_path=REGISTRY_PATH) -> dict:
    """Produce the full admission artifact body (dict). Deterministic: identical
    inputs yield a byte-identical canonical serialization. Runs the exact 32-row
    selector once (the long pass)."""
    cat, reg, cat_bytes, reg_bytes, atlas_index = _load_validated_sources(
        catalog_path, registry_path)
    supply = _build_supply(cat, atlas_index)

    grammar = ADM.build_grammar()
    receipt = ADM.admit_row(supply, grammar=grammar)  # SOLE selector

    selected_row = [
        {"glyph_id": gid, **atlas_index[gid]}
        for gid in receipt["selected_glyph_ids"]
    ]

    body = {
        "schema": ARTIFACT_SCHEMA,
        "generator_id": GENERATOR_ID,
        "source": {
            "catalog_path": _rel(catalog_path),
            "catalog_schema": CATALOG_SCHEMA,
            "catalog_declared_manifest_hash": cat["manifest_hash"],
            "catalog_source_sha256": _sha256_hex(cat_bytes),
            "registry_path": _rel(registry_path),
            "registry_schema": REGISTRY_SCHEMA,
            "registry_declared_sha256": reg["registry_sha256"],
            "registry_source_sha256": _sha256_hex(reg_bytes),
        },
        "counts": {
            "catalog_entry_count": len(cat["entries"]),
            "nonzero_supply_count": len(supply),
            "distinct_signature_count": receipt["distinct_signature_count"],
            "atlas_binding_count": len(atlas_index),
        },
        "grammar_id": grammar["grammar_id"],
        "grammar_hash": grammar["grammar_hash"],
        "selected_row": selected_row,
        "receipt": receipt,
        "artifact_content_hash": "",
    }
    body["artifact_content_hash"] = _artifact_hash(body)
    return body


def _rel(path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _artifact_hash(body: dict) -> str:
    """Content hash over the whole artifact EXCEPT its own hash field."""
    inner = {k: v for k, v in body.items() if k != "artifact_content_hash"}
    return _sha256_hex(canonical_json_bytes(inner))


def write_artifact_atomically(body: dict, out_path=ARTIFACT_PATH) -> Path:
    """Write the canonical artifact bytes atomically (temp in same dir + replace)
    so a partial/interrupted write can never leave a corrupt checked-in file."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(body) + b"\n"
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out_path)
    return out_path


# --------------------------------------------------------------------------- #
# Independent validation of a produced artifact.
# --------------------------------------------------------------------------- #
def _expected_selected_row(receipt: dict, atlas_index: dict) -> list:
    """Rebuild the selected_row projection from receipt.selected_glyph_ids joined
    to the CURRENT atlas bindings. This is a deterministic PROJECTION, not a
    re-selection — it never re-runs the optimum search."""
    ids = receipt.get("selected_glyph_ids") or []
    row = []
    for gid in ids:
        if gid not in atlas_index:
            raise CompileError(REASON_SELECTED_BINDING_MISMATCH, f"gid={gid} unbound")
        row.append({"glyph_id": gid, **atlas_index[gid]})
    return row


def validate_artifact(body: dict, catalog_path=CATALOG_PATH,
                      registry_path=REGISTRY_PATH, *, full_receipt: bool = False) -> None:
    """Rebuild every sealed fact from the trusted sources at the given PATHS and
    reject any mismatch with a typed reason. (Path-based convenience wrapper.)"""
    cat, reg, cat_bytes, reg_bytes, atlas_index = _load_validated_sources(
        catalog_path, registry_path)
    _validate_artifact_core(
        body, cat, cat_bytes, reg, reg_bytes, atlas_index,
        _rel(catalog_path), _rel(registry_path), full_receipt=full_receipt)


def _json_exact_equal(a: object, b: object) -> bool:
    """JSON-TYPE-EXACT recursive equality — the SOLE document-binding authority.

    Plain Python ``==`` is NOT safe as a document-identity check because Python
    conflates JSON-distinct scalar types: ``True == 1`` and ``1 == 1.0`` are both
    True. A decoded document could therefore carry a JSON boolean where its raw
    bytes carried the integer 1 (or an int where the bytes carried a float) and
    slip past a loose ``!=`` comparison, letting the transaction hash one byte
    document while projecting a structurally-different object.

    This function instead requires the two values to be IDENTICAL as JSON trees:

      * identical node TYPE at every node — ``type(a) is type(b)``. Because
        ``type(True) is bool`` while ``type(1) is int``, and ``type(1) is int``
        while ``type(1.0) is float``, a JSON boolean never equals a JSON integer
        and a JSON integer never equals a JSON float. (Python's ``bool`` is a
        subclass of ``int``, so an ``isinstance``/``==`` test would NOT catch
        this — only an exact ``type() is`` identity test does.)
      * for objects: EXACTLY the same set of keys (JSON objects are unordered, so
        key-set equality is the right identity), each mapped to an exactly-equal
        value.
      * for arrays: EXACTLY the same length and the same values in the same order
        (JSON arrays are ordered, so both cardinality and order must match).
      * for scalars (str / int / float / bool / None): the type already matched
        above, so a direct value ``==`` is a same-type comparison (``None==None``
        holds; no cross-type coercion can occur).

    Args:
        a: one decoded JSON value (the in-memory document supplied to validation).
        b: the other decoded JSON value (the parse of that document's raw bytes).

    Returns:
        True only when ``a`` and ``b`` are the same JSON tree by the rules above;
        False on the first divergence in type, keys, order/length, or scalar.
    """
    # First gate: the two nodes must be the SAME concrete Python type. This is the
    # rule that makes bool!=int and int!=float — an ``isinstance`` check would let
    # bool masquerade as int (bool subclasses int in Python), so we use ``type()
    # is`` identity, which admits no subclass slippage.
    if type(a) is not type(b):
        return False
    # Objects: same key SET (order-independent, matching JSON object semantics),
    # then recurse into every value under those shared keys.
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return False
        return all(_json_exact_equal(a[k], b[k]) for k in a)
    # Arrays: same LENGTH and same element order (JSON arrays are ordered), then
    # recurse element-by-element in lockstep.
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_json_exact_equal(x, y) for x, y in zip(a, b))
    # Scalars: the type already matched (str/int/float/bool/None), so this is a
    # same-type value comparison with no cross-type coercion possible.
    return a == b


def validate_artifact_documents(body: dict, catalog: dict, catalog_raw: bytes,
                                registry: dict, registry_raw: bytes, *,
                                full_receipt: bool = False,
                                catalog_path_rel: str | None = None,
                                registry_path_rel: str | None = None) -> None:
    """Validate the artifact against the EXACT SUPPLIED documents (in memory) —
    never re-reading any canonical path. This is the binding a transaction MUST
    use so it validates the same universal catalog + native atlas registry whose
    hashes it then stamps (no "validate one document, stamp another's hash").

    The strong binding is: source.catalog_source_sha256 == sha256(catalog_raw)
    and source.registry_source_sha256 == sha256(registry_raw), proven inside the
    core before any row projection. The selector is never called.
    """
    if not isinstance(catalog, dict) or not isinstance(registry, dict):
        raise CompileError(REASON_MALFORMED_SOURCE, "supplied documents")
    # Prove each supplied DECODED document is EXACTLY the parse of its supplied raw
    # bytes BEFORE trusting either. The transaction hashes the raw bytes and then
    # projects the decoded object; if they diverge, the hash binds one document
    # while the projection reads another.
    try:
        catalog_from_raw = json.loads(catalog_raw.decode("utf-8"))
        registry_from_raw = json.loads(registry_raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CompileError(REASON_MALFORMED_SOURCE, f"raw parse: {exc}") from exc
    # DELETE-FIRST: the old loose ``catalog != catalog_from_raw`` / ``registry !=
    # registry_from_raw`` comparisons are GONE. They used Python's default ``==``,
    # which treats True==1 and 1==1.0 as equal and so could NOT detect a nested
    # bool-vs-int or int-vs-float divergence. The single JSON-type-exact owner
    # ``_json_exact_equal`` now is the authority; each document keeps its own
    # distinct typed divergence reason.
    if not _json_exact_equal(catalog, catalog_from_raw):
        raise CompileError(REASON_CATALOG_DOC_DIVERGENCE, "catalog object != parse(catalog_raw)")
    if not _json_exact_equal(registry, registry_from_raw):
        raise CompileError(REASON_REGISTRY_DOC_DIVERGENCE, "registry object != parse(registry_raw)")
    _validate_catalog(catalog)
    _validate_registry(registry)
    topo = _build_page_topology(registry)
    atlas_index = _build_atlas_index(registry, topo)
    # Default the path-identity check to the artifact's own declared paths (the
    # HASH is the real binding); a caller may pin exact relative paths instead.
    src = body.get("source") if isinstance(body, dict) else None
    cat_rel = catalog_path_rel if catalog_path_rel is not None else (
        src.get("catalog_path") if isinstance(src, dict) else None)
    reg_rel = registry_path_rel if registry_path_rel is not None else (
        src.get("registry_path") if isinstance(src, dict) else None)
    _validate_artifact_core(
        body, catalog, catalog_raw, registry, registry_raw, atlas_index,
        cat_rel, reg_rel, full_receipt=full_receipt)


def _validate_artifact_core(body: dict, cat: dict, cat_bytes: bytes, reg: dict,
                            reg_bytes: bytes, atlas_index: dict,
                            catalog_path_rel, registry_path_rel, *,
                            full_receipt: bool) -> None:
    """Prove every sealed artifact fact against the provided (already-validated)
    catalog + registry documents + their raw bytes. No path is ever read here;
    the selector is never called (only full_receipt=True re-runs the helper's own
    recompute, and the main compiler never passes that)."""
    # 0. Exact artifact key set + top-level identities.
    if not isinstance(body, dict):
        raise CompileError(REASON_ARTIFACT_SCHEMA_MISMATCH, "not object")
    _require_exact_keys(body, ARTIFACT_TOP_KEYS, REASON_ARTIFACT_UNKNOWN_FIELD)
    if body.get("schema") != ARTIFACT_SCHEMA:
        raise CompileError(REASON_ARTIFACT_SCHEMA_MISMATCH, str(body.get("schema")))
    if body.get("generator_id") != GENERATOR_ID:
        raise CompileError(REASON_GENERATOR_MISMATCH, str(body.get("generator_id")))
    src = body.get("source")
    counts = body.get("counts")
    if not isinstance(src, dict) or not isinstance(counts, dict):
        raise CompileError(REASON_ARTIFACT_SCHEMA_MISMATCH, "source/counts")
    _require_exact_keys(src, ARTIFACT_SOURCE_KEYS, REASON_ARTIFACT_UNKNOWN_FIELD)
    _require_exact_keys(counts, ARTIFACT_COUNTS_KEYS, REASON_ARTIFACT_UNKNOWN_FIELD)

    # 2. Source identity + the STRONG source-hash binding to the SUPPLIED bytes.
    if (src.get("catalog_schema") != CATALOG_SCHEMA or
            src.get("registry_schema") != REGISTRY_SCHEMA or
            src.get("catalog_declared_manifest_hash") != cat["manifest_hash"] or
            src.get("registry_declared_sha256") != reg["registry_sha256"] or
            (catalog_path_rel is not None and src.get("catalog_path") != catalog_path_rel) or
            (registry_path_rel is not None and src.get("registry_path") != registry_path_rel)):
        raise CompileError(REASON_SOURCE_IDENTITY_MISMATCH, "declared identity")
    if src.get("catalog_source_sha256") != _sha256_hex(cat_bytes):
        raise CompileError(REASON_SOURCE_HASH_MISMATCH, "catalog")
    if src.get("registry_source_sha256") != _sha256_hex(reg_bytes):
        raise CompileError(REASON_SOURCE_HASH_MISMATCH, "registry")

    # 3. Counts recomputed from the supplied sources.
    supply = _build_supply(cat, atlas_index)
    receipt = body.get("receipt")
    if not isinstance(receipt, dict):
        raise CompileError(REASON_ARTIFACT_SCHEMA_MISMATCH, "receipt")
    if (counts.get("catalog_entry_count") != len(cat["entries"]) or
            counts.get("nonzero_supply_count") != len(supply) or
            counts.get("atlas_binding_count") != len(atlas_index) or
            counts.get("distinct_signature_count") != receipt.get("distinct_signature_count")):
        raise CompileError(REASON_COUNTS_MISMATCH, "")

    # 4. Grammar identity/hash must match the live authored grammar AND receipt.
    grammar = ADM.build_grammar()
    if (body.get("grammar_id") != grammar["grammar_id"] or
            receipt.get("grammar_id") != grammar["grammar_id"]):
        raise CompileError(REASON_GRAMMAR_ID_MISMATCH, str(body.get("grammar_id")))
    if (body.get("grammar_hash") != grammar["grammar_hash"] or
            receipt.get("grammar_hash") != grammar["grammar_hash"]):
        raise CompileError(REASON_GRAMMAR_HASH_MISMATCH, str(body.get("grammar_hash")))

    # 5. Selected-row: exactly ROW_CAPACITY items with exact item key sets, and
    #    byte-order-equal to receipt.selected_glyph_ids joined to the SUPPLIED
    #    atlas bindings (deterministic join, NOT a re-selection). This proves
    #    every selected binding tuple equals the exact supplied registry binding.
    selected_row = body.get("selected_row")
    if not isinstance(selected_row, list) or len(selected_row) != ADM.ROW_CAPACITY:
        raise CompileError(REASON_SELECTED_BINDING_MISMATCH, "length")
    for item in selected_row:
        if not isinstance(item, dict) or frozenset(item.keys()) != SELECTED_ROW_KEYS:
            raise CompileError(REASON_SELECTED_BINDING_MISMATCH, "item key set")
    if selected_row != _expected_selected_row(receipt, atlas_index):
        raise CompileError(REASON_SELECTED_BINDING_MISMATCH, "")

    # 6. Artifact self content-hash.
    if body.get("artifact_content_hash") != _artifact_hash(body):
        raise CompileError(REASON_ARTIFACT_HASH_MISMATCH, "")

    # 7. Receipt is the EXACT canonical projection of its selected row over the
    #    SUPPLIED supply + authored grammar — without re-running the selector.
    #    (Binds receipt supply/cardinality/hash + every selected descriptor
    #    identity to the in-memory supplied-catalog projection.)
    _validate_receipt_projection(receipt, supply, grammar)

    # 8. Optional: the full re-selection (helper re-runs admit_row's optimum).
    #    The main compiler NEVER passes full_receipt=True.
    if full_receipt:
        ADM.recompute_and_validate(receipt, supply)


def _project_per_target(selected_descs: list, targets_runtime: list) -> list:
    """Rebuild the per_target NEAREST record for a CHOSEN selected row — the
    canonical projection, NOT a re-selection.

    For each grammar target, the nearest record is the minimum u7 cost over the
    32 chosen glyphs, with the winner being the LOWEST glyph_id among those that
    tie for that minimum (exactly the tie-break the selector's incremental owner
    update settles on). This is O(targets * 32) — a scoring pass over the already
    chosen row, never the O(supply * targets * rounds) selection search.
    """
    INF = 1 << 62
    out = []
    for t in targets_runtime:
        tm, tep, tjn = t["_mask8"], t["endpoint_count"], t["junction_count"]
        best = INF
        best_gid = None
        for d in selected_descs:
            c = ADM._cost(tm, tep, tjn, d["mask8"], d["endpoint_count"], d["junction_count"])
            # Strictly lower cost wins; an exact tie keeps the lowest glyph_id.
            if c < best or (c == best and (best_gid is None or d["glyph_id"] < best_gid)):
                best = c
                best_gid = d["glyph_id"]
        out.append({"nearest_winner_glyph_id": best_gid, "nearest_score": best})
    return out


def _validate_receipt_projection(receipt: dict, supply, grammar: dict) -> None:
    """Prove the embedded receipt is the EXACT canonical projection of its own
    selected row over the current supply and authored grammar — WITHOUT
    re-running the selector.

    The old permissive check only compared self hashes, supply identity, and
    capacity; a caller could permute/duplicate selected_glyph_ids or alter the
    descriptor hashes / per_target facts / aggregates / boundary coverage, then
    recompute the receipt + artifact hashes, and pass. This replacement rebuilds
    every derived field from the receipt's selected row and compares each one, so
    a re-hashed-but-divergent receipt is rejected with a specific typed reason.
    """
    # 1. Exact receipt key set.
    if not isinstance(receipt, dict):
        raise CompileError(REASON_RECEIPT_KEY_SET, "not object")
    _require_exact_keys(receipt, RECEIPT_KEYS, REASON_RECEIPT_KEY_SET)

    # 2. Authored identities.
    if (receipt["schema"] != ADM.RECEIPT_SCHEMA or
            receipt["scorer_id"] != EXPECTED_SCORER or
            receipt["row_capacity"] != ADM.ROW_CAPACITY or
            receipt["grammar_id"] != grammar["grammar_id"] or
            receipt["grammar_hash"] != grammar["grammar_hash"]):
        raise CompileError(REASON_RECEIPT_IDENTITY_MISMATCH, "")
    if (receipt["selection_algorithm"] != ADM.SELECTION_ALGORITHM or
            receipt["legacy_worst_bound"] != ADM.GRAMMAR_WORST_BOUND or
            receipt["selection_memory_cap_bytes"] != ADM.FULL_SUPPLY_MEMORY_CAP_BYTES or
            receipt["selection_actual_rss_measured"] is not False or
            receipt["selection_actual_rss_status"] != "pending_canonical_execution"):
        raise CompileError(REASON_RECEIPT_IDENTITY_MISMATCH, "selection provenance")

    # 3. Bind the receipt to the CURRENT supply (count, hash, distinct signatures).
    validated = ADM._validate_supply(supply)
    by_id = {d["glyph_id"]: d for d in validated}
    distinct = len({ADM._descriptor_signature(d) for d in validated})
    if (receipt["supply_count"] != len(validated) or
            receipt["supply_hash"] != ADM._supply_hash(validated) or
            receipt["distinct_signature_count"] != distinct):
        raise CompileError(REASON_RECEIPT_SUPPLY_MISMATCH, "")

    # 4. selected_glyph_ids: exact capacity list of unique non-bool ints in supply.
    ids = receipt["selected_glyph_ids"]
    if not isinstance(ids, list) or len(ids) != ADM.ROW_CAPACITY:
        raise CompileError(REASON_SELECTED_IDS_INVALID, "length")
    for gid in ids:
        _require_int(gid, REASON_SELECTED_IDS_INVALID, "selected id")
    if len(set(ids)) != len(ids):
        raise CompileError(REASON_DUPLICATE_SELECTED_GLYPH_ID, "")
    # CANONICAL ORDER. selected_glyph_ids is set-shaped and MUST be published in
    # ascending GlyphId order. This single check closes the entire permutation
    # class: any reordering — including a fully-consistent ids+hashes+row+hash
    # permutation — is non-ascending and rejected here, before any hash could
    # make it look self-consistent.
    if ids != sorted(ids):
        raise CompileError(REASON_SELECTED_ORDER_MISMATCH, "noncanonical (must be ascending GlyphId)")
    missing = [g for g in ids if g not in by_id]
    if missing:
        raise CompileError(REASON_SELECTED_IDS_INVALID, f"not in supply {missing[:3]}")
    selected = [by_id[g] for g in ids]

    # 5. selected_descriptor_hashes: index-aligned with selected_glyph_ids. If the
    #    SAME hashes appear in a different order, it is a permutation; otherwise a
    #    genuine content mismatch.
    dh = receipt["selected_descriptor_hashes"]
    if not isinstance(dh, list) or len(dh) != ADM.ROW_CAPACITY:
        raise CompileError(REASON_DESCRIPTOR_HASH_MISMATCH, "length")
    expected_dh = [ADM._descriptor_hash(d) for d in selected]
    if dh != expected_dh:
        if sorted(dh) == sorted(expected_dh):
            raise CompileError(REASON_SELECTED_ORDER_MISMATCH, "descriptor hashes reordered")
        raise CompileError(REASON_DESCRIPTOR_HASH_MISMATCH, "")

    # 6. per_target: exact projection over the chosen 32 (winner + score).
    targets = grammar["_targets_runtime"]
    pt = receipt["per_target"]
    if not isinstance(pt, list) or len(pt) != len(targets):
        raise CompileError(REASON_PER_TARGET_KEY_SET, "length")
    projected = _project_per_target(selected, targets)
    for i, (got, exp) in enumerate(zip(pt, projected)):
        if not isinstance(got, dict):
            raise CompileError(REASON_PER_TARGET_KEY_SET, f"item {i} not object")
        _require_exact_keys(got, PER_TARGET_KEYS, REASON_PER_TARGET_KEY_SET)
        _require_int(got["nearest_score"], REASON_PER_TARGET_SCORE_MISMATCH, f"item {i}")
        _require_int(got["nearest_winner_glyph_id"], REASON_PER_TARGET_WINNER_MISMATCH, f"item {i}")
        if got["nearest_score"] != exp["nearest_score"]:
            raise CompileError(REASON_PER_TARGET_SCORE_MISMATCH, f"item {i}")
        if got["nearest_winner_glyph_id"] != exp["nearest_winner_glyph_id"]:
            raise CompileError(REASON_PER_TARGET_WINNER_MISMATCH, f"item {i}")

    # 7. Aggregates reduced from the projected scores.
    scores = [p["nearest_score"] for p in projected]
    worst = max(scores) if scores else 0
    score_sum = sum(scores)
    mean_num, mean_den = ADM._reduce_fraction(score_sum, len(scores) if scores else 1)
    if (receipt["worst_score"] != worst or receipt["score_sum"] != score_sum or
            receipt["mean_numerator"] != mean_num or
            receipt["mean_denominator"] != mean_den):
        raise CompileError(REASON_AGGREGATE_MISMATCH, "")

    # 8. Boundary contact coverage recomputed from the union of selected masks.
    union = 0
    for d in selected:
        union |= d["mask8"]
    if receipt["boundary_contact_coverage"] != list(ADM._boundary_contacts(union)):
        raise CompileError(REASON_BOUNDARY_MISMATCH, "")

    # 9. Receipt self content-hash (last — every field above is already pinned).
    if receipt["content_hash"] != ADM._content_hash(receipt):
        raise CompileError(REASON_RECEIPT_CONTENT_HASH, "")


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=(
        "Compile the FL-4512 mesh-feature admission artifact from the universal "
        "Unicode catalog + native atlas registry (strict)."))
    parser.add_argument("--catalog", default=str(CATALOG_PATH))
    parser.add_argument("--registry", default=str(REGISTRY_PATH))
    parser.add_argument("--out", default=str(ARTIFACT_PATH))
    parser.add_argument("--validate", action="store_true",
                        help="Fast strict validation of the written artifact "
                             "(does NOT re-run the full-supply selector).")
    parser.add_argument("--validate-full", action="store_true",
                        help="Full validation including the selector re-run.")
    args = parser.parse_args(argv)

    t0 = time.time()
    body = compile_artifact(args.catalog, args.registry)
    compile_s = time.time() - t0
    out = write_artifact_atomically(body, args.out)
    print(json.dumps({
        "generator_id": GENERATOR_ID,
        "artifact_path": str(out),
        "artifact_content_hash": body["artifact_content_hash"],
        "nonzero_supply_count": body["counts"]["nonzero_supply_count"],
        "distinct_signature_count": body["counts"]["distinct_signature_count"],
        "selected_count": len(body["selected_row"]),
        "catalog_source_sha256": body["source"]["catalog_source_sha256"],
        "registry_source_sha256": body["source"]["registry_source_sha256"],
        "compile_elapsed_s": round(compile_s, 2),
    }, indent=2))
    if args.validate or args.validate_full:
        t1 = time.time()
        validate_artifact(body, args.catalog, args.registry,
                          full_receipt=args.validate_full)
        print(f"validate_elapsed_s={time.time() - t1:.2f} "
              f"full={args.validate_full} OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
