# =============================================================================
# test_fl4512_unicode_admission_cutover.py
# =============================================================================
# Fixtures for the main-compiler admission cutover: the compiled-pool compiler
# no longer accepts a profile-authored 32-glyph mesh row — it is the SOLE
# injector of the mesh_feature.bundle.v1 structural row, taken verbatim from the
# strict offline Unicode admission artifact.
#
# Two kinds of fixture:
#   * PROPERTY assertions on the real recompiled pool (fast, no compile): the
#     mesh row IS the admission artifact's 32 ascending GlyphIds, the header
#     binds the artifact + universal-catalog hashes, structural descriptors carry
#     u7 topology but NOT global6_norm, and no structural GlyphId leaks into any
#     other role.
#   * ONE-FACT rejection tests: each tampers exactly one input/fact and asserts
#     the specific typed CompileFailure. Compile-level tests monkeypatch the
#     strict validator to a no-op so they exercise the compiler's own guards
#     without rebuilding the full-supply validation projection.
# =============================================================================

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import compile_fl4512_pools as C  # noqa: E402

ADMISSION_PATH = ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json"
UNIVERSAL_PATH = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.shape_catalog.json"
POOL_PATH = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
ADDITIVE_CATALOG = ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
ATLAS_PATH = ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"
PAGE16_PATH = ROOT / "assets/glyphs/atlases/material.additive.v1.page16_rgba8.json"
PROFILES_PATH = ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
METRIC_PATH = ROOT / "assets/glyphs/profiles/fl4512.mesh_asset_metric.v1.json"
SCALE_GOLDEN = ROOT / "assets/glyphs/fixtures/fl4512_projected_scale_golden_v1.json"
EXTENT_GOLDEN = ROOT / "assets/glyphs/fixtures/fl4512_projected_feature_extent_golden_v2.json"
STALE_PRODUCT_ADMISSION_SCHEMA = "fl4512.mesh_feature_candidate_admission_artifact.v2"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rj(path):
    raw = Path(path).read_bytes()
    return json.loads(raw), raw


def _selected_ids():
    art, _ = _rj(ADMISSION_PATH)
    return [row["glyph_id"] for row in art["selected_row"]]


def _mesh_row(pool):
    for row in pool["rows"]:
        if row.get("role") == "mesh_feature" and row.get("candidate_count", 0) > 0:
            return row
    return None


# --------------------------------------------------------------------------- #
# PROPERTY assertions on the real recompiled pool.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def pool():
    if not POOL_PATH.is_file():
        pytest.skip("compiled pool not present")
    data, _ = _rj(POOL_PATH)
    return data


def test_property_mesh_row_is_admission_ascending(pool):
    # The live mesh row IS the admission artifact's selected_row, exact order,
    # 32 unique ascending GlyphIds (selected-order divergence would break this).
    row = _mesh_row(pool)
    assert row is not None and row["material_key"] == 200
    ids = row["candidate_glyph_ids"]
    assert ids == _selected_ids()
    assert len(ids) == 32 and len(set(ids)) == 32
    assert ids == sorted(ids)


def test_property_header_provenance(pool):
    header = pool["header"]
    assert header["mesh_admission_artifact_sha256"] == _sha(ADMISSION_PATH)
    assert header["universal_catalog_sha256"] == _sha(UNIVERSAL_PATH)
    row = _mesh_row(pool)
    # Stale product snapshot assertion: the active
    # water-lane guard leaves that generated pool on v2 while the compiler's
    # current admission contract is v3. Fresh compile tests normalize only the
    # in-memory fixture to v3; they never rewrite the guarded product artifact.
    assert row["candidate_admission_schema"] == STALE_PRODUCT_ADMISSION_SCHEMA
    assert row["candidate_admission_schema"] != C.ADMISSION.ARTIFACT_SCHEMA
    assert row["mesh_admission_artifact_sha256"] == _sha(ADMISSION_PATH)
    assert row["role_eligible_glyph_ids"] == _selected_ids()


def test_property_structural_descriptors_have_no_global6(pool):
    # Structural mesh candidates require u7 topology, NOT global6_norm.
    row = _mesh_row(pool)
    for desc in row["candidate_descriptors"]:
        assert "global6_norm" not in desc
        assert "beta0" in desc and "beta1" in desc and "mask8_u64_le_hex" in desc


def test_property_no_cross_role_unicode_leakage(pool):
    # No selected structural GlyphId may appear in any NON-mesh role's candidates.
    selected = set(_selected_ids())
    for row in pool["rows"]:
        if row.get("role") == "mesh_feature":
            continue
        assert not (set(row.get("candidate_glyph_ids", [])) & selected)


# --------------------------------------------------------------------------- #
# Compile-level one-fact rejections (validator monkeypatched to skip the
# full-supply validation projection).
# --------------------------------------------------------------------------- #
def _compile(monkeypatch, *, mutate_profile=None, mutate_admission=None,
             mutate_universal=None, validate=None):
    prof, prof_raw = _rj(PROFILES_PATH)
    if mutate_profile:
        mutate_profile(prof)
        prof_raw = json.dumps(prof).encode()
    adm, adm_raw = _rj(ADMISSION_PATH)
    # Test-only fixture normalization: the guarded product artifact remains v2,
    # while compiler-local guard tests start from the current v3 outer schema.
    adm["schema"] = C.ADMISSION.ARTIFACT_SCHEMA
    if mutate_admission:
        mutate_admission(adm)
    uni, uni_raw = _rj(UNIVERSAL_PATH)
    if mutate_universal:
        mutate_universal(uni)
    # Skip the strict validator's full-supply validation projection. The guards
    # under test are the compiler's own. A specific `validate` may inject a
    # failure instead.
    monkeypatch.setattr(C.ADMISSION, "validate_artifact_documents", validate or (lambda *a, **k: None))
    cat, cat_raw = _rj(ADDITIVE_CATALOG)
    atlas, atlas_raw = _rj(ATLAS_PATH)
    page16, page16_raw = _rj(PAGE16_PATH)
    metric, _ = _rj(METRIC_PATH)
    return C.compile_artifact(
        prof, prof_raw, cat, cat_raw, atlas, atlas_raw, page16, page16_raw,
        "testcommit", 2, None, ROOT, None, metric,
        SCALE_GOLDEN.read_bytes(), EXTENT_GOLDEN.read_bytes(),
        admission_artifact=adm, admission_artifact_raw=adm_raw,
        universal_catalog=uni, universal_catalog_raw=uni_raw,
    )


def _expect(monkeypatch, reason, **kw):
    with pytest.raises(C.CompileFailure) as e:
        _compile(monkeypatch, **kw)
    assert e.value.reason == reason, f"got {e.value.reason}"


def test_source_row_resurrection_rejected(monkeypatch):
    # Re-authoring candidates on the profile mesh lane is fatal: the profile
    # keeps address metadata only.
    def add_candidates(prof):
        for p in prof["profiles"]:
            mb = p.get("role_buckets", {}).get("mesh_bundle")
            if mb is not None and p.get("material_id", "").startswith("mesh:"):
                mb["candidates"] = [512, 513]
    _expect(monkeypatch, "mesh_row_authored_content", mutate_profile=add_candidates)


def test_authored_admission_field_rejected(monkeypatch):
    def add_admission(prof):
        for p in prof["profiles"]:
            mb = p.get("role_buckets", {}).get("mesh_bundle")
            if mb is not None and p.get("material_id", "").startswith("mesh:"):
                mb["role_eligible_glyph_ids"] = [1, 2, 3]
    _expect(monkeypatch, "mesh_row_authored_content", mutate_profile=add_admission)


def test_missing_atlas_binding_rejected(monkeypatch):
    def break_binding(adm):
        adm["selected_row"][0]["page_index"] = 99999  # no such registry page
    _expect(monkeypatch, "mesh_admission_atlas_binding_invalid",
            mutate_admission=break_binding)


def test_descriptor_mismatch_rejected(monkeypatch):
    # Make the universal catalog's declared mask for a selected glyph disagree
    # with what the native page actually samples.
    sel = _selected_ids()[0]

    def wrong_mask(uni):
        for e in uni["entries"]:
            if e.get("glyph_id") == sel:
                e["mask8_u64"] = "0xffffffffffffffff"  # cannot equal the sampled mask
    _expect(monkeypatch, "mesh_admission_descriptor_mismatch",
            mutate_universal=wrong_mask)


def test_source_hash_mismatch_wrapped(monkeypatch):
    # A strict-validator source-hash rejection is wrapped into the compiler's
    # typed mesh_admission_artifact_invalid.
    def raising(*a, **k):
        raise C.ADMISSION.CompileError(C.ADMISSION.REASON_SOURCE_HASH_MISMATCH, "catalog")
    _expect(monkeypatch, "mesh_admission_artifact_invalid", validate=raising)


# --------------------------------------------------------------------------- #
# Isolated make_row v2-provenance + aspect-override guards.
# --------------------------------------------------------------------------- #
def _u7(gid):
    # Minimal structural descriptor (NO global6_norm) as the compiler emits it.
    return {
        "glyph_id": gid, "mask8_u64_le_hex": "00" * 8, "skeleton_u64_le_hex": "00" * 8,
        "endpoint_count": 0, "junction_count": 0, "beta0": 1, "beta1": 0,
        "euler_characteristic": 1,
    }


def _provenance(ids):
    return {
        "schema": C.ADMISSION.ARTIFACT_SCHEMA,
        "receipt_id": "a" * 64,
        "role_eligible_glyph_ids": list(ids),
        "strict_artifact_sha256": "b" * 64,
    }


def test_make_row_mesh_provenance_valid_without_global6():
    # A structural mesh row with global6-free descriptors + valid v2 provenance
    # compiles (accidental global6 requirement would wrongly reject it).
    ids = [1000, 1001, 1002]
    descs = {g: _u7(g) for g in ids}
    row = C.make_row("mesh", 200, "mesh_feature", "mesh_feature.bundle.v1", 0,
                     "mesh_bundle", ids, set(ids), descs,
                     candidate_admission=_provenance(ids))
    assert row["candidate_admission_schema"] == C.ADMISSION.ARTIFACT_SCHEMA
    assert row["mesh_admission_artifact_sha256"] == "b" * 64


@pytest.mark.parametrize("field,bad", [
    ("schema", "fl4512.mesh_feature_candidate_admission.v1"),  # old v1 schema now rejected
    ("receipt_id", "not-hex"),
    ("strict_artifact_sha256", "short"),
])
def test_make_row_provenance_rejections(field, bad):
    ids = [1000, 1001, 1002]
    descs = {g: _u7(g) for g in ids}
    prov = _provenance(ids)
    prov[field] = bad
    with pytest.raises(C.CompileFailure) as e:
        C.make_row("mesh", 200, "mesh_feature", "mesh_feature.bundle.v1", 0,
                   "mesh_bundle", ids, set(ids), descs, candidate_admission=prov)
    assert e.value.reason == "candidate_role_admission_invalid"


def test_aspect_variant_inherits_base_v2_provenance(monkeypatch):
    # An aspect view-variant may select a SUBSET of the base row's candidates for
    # a view region; it INHERITS the base row's strict v2 admission provenance and
    # never re-runs the admission selector.
    base = {
        "profile_namespace": "mesh", "material_key": 200, "role": "mesh_feature",
        "route_key": "mesh_feature.bundle.v1", "lane": 0, "lane_name": "mesh_bundle",
        "candidate_glyph_ids": [1000, 1001, 1002],
        "candidate_admission_schema": C.ADMISSION.ARTIFACT_SCHEMA,
        "candidate_admission_receipt_id": "a" * 64,
        "role_eligible_glyph_ids": [1000, 1001, 1002],
        "mesh_admission_artifact_sha256": "b" * 64,
    }
    monkeypatch.setattr(C, "_aspect_base_row", lambda rows, addr: base)
    descs = {g: _u7(g) for g in (1000, 1001, 1002)}
    variant = {"source_pool_address": "mesh:200/mesh_feature",
               "candidate_glyph_ids": [1000, 1001]}
    _merge, provenance = C._aspect_variant_row(variant, [], {1000, 1001, 1002}, descs)
    assert provenance["candidate_admission_schema"] == C.ADMISSION.ARTIFACT_SCHEMA
    assert provenance["candidate_admission_receipt_id"] == "a" * 64
    assert provenance["mesh_admission_artifact_sha256"] == "b" * 64
    assert provenance["role_eligible_glyph_ids"] == [1000, 1001, 1002]


def test_selector_entry_points_are_dead_in_main_compiler(monkeypatch):
    # SELECTOR-DEAD evidence: monkeypatch every admission selector entry point to
    # raise. A real compile of the mesh row must still succeed — the main compiler
    # consumes the pre-computed selected_row and NEVER re-runs the selector.
    def boom(*a, **k):
        raise AssertionError("admission selector called in main compiler")
    monkeypatch.setattr(C.ADMISSION.ADM, "admit_row", boom)
    monkeypatch.setattr(C.ADMISSION.ADM, "_select_legacy", boom)
    monkeypatch.setattr(C.ADMISSION.ADM, "recompute_and_validate", boom)
    artifact = _compile(monkeypatch)  # _compile stubs validate_artifact_documents
    row = next(r for r in artifact["rows"]
               if r.get("role") == "mesh_feature" and r.get("candidate_count", 0) > 0)
    assert len(row["candidate_glyph_ids"]) == 32
    assert row["candidate_glyph_ids"] == _selected_ids()


def test_aspect_mesh_structural_subset_keeps_base_provenance(monkeypatch):
    # LEGAL: a mesh structural aspect variant selects a SUBSET of the base row's
    # 32 candidates and inherits the exact base admission provenance.
    base = {
        "profile_namespace": "mesh", "material_key": 200, "role": "mesh_feature",
        "route_key": "mesh_feature.bundle.v1", "lane": 0, "lane_name": "mesh_bundle",
        "candidate_glyph_ids": [1000, 1001, 1002],
        "candidate_admission_schema": C.ADMISSION.ARTIFACT_SCHEMA,
        "candidate_admission_receipt_id": "a" * 64,
        "role_eligible_glyph_ids": [1000, 1001, 1002],
        "mesh_admission_artifact_sha256": "b" * 64,
    }
    monkeypatch.setattr(C, "_aspect_base_row", lambda rows, addr: base)
    descs = {g: _u7(g) for g in (1000, 1001, 1002)}
    variant = {"source_pool_address": "mesh:200/mesh_feature",
               "candidate_glyph_ids": [1000, 1002]}  # strict subset of base
    _merge, provenance = C._aspect_variant_row(variant, [], {1000, 1001, 1002}, descs)
    assert provenance["candidate_admission_schema"] == C.ADMISSION.ARTIFACT_SCHEMA
    assert provenance["mesh_admission_artifact_sha256"] == "b" * 64


def test_aspect_non_mesh_base_rejects_structural_glyphid(monkeypatch):
    # REJECTED: a non-mesh base row (tonal, no admission) whose aspect variant
    # references a structural GlyphId not in its own base candidates — proving no
    # aspect can leak the unioned structural supply through a non-mesh base row.
    base = {
        "profile_namespace": "terrain", "material_key": 1, "role": "tonal",
        "route_key": "ramp.v1", "lane": 0, "lane_name": "ramp[0]",
        "candidate_glyph_ids": [300, 301],  # ordinary additive ids, NO admission
    }
    monkeypatch.setattr(C, "_aspect_base_row", lambda rows, addr: base)
    descs = {g: _u7(g) for g in (300, 301, 16778900)}
    variant = {"source_pool_address": "terrain:1/tonal",
               "candidate_glyph_ids": [16778900]}  # structural id NOT in base
    with pytest.raises(C.CompileFailure) as e:
        C._aspect_variant_row(variant, [], {300, 301, 16778900}, descs)
    assert e.value.reason == "aspect_candidate_not_base_subset"


@pytest.mark.parametrize(
    "base_candidates,variant_candidates",
    [
        ([1000, 1001], [[1000]]),
        ([[1000]], [1000]),
        ([1000, 1001], [True]),
    ],
)
def test_aspect_candidate_shapes_fail_with_typed_reason(
    monkeypatch, base_candidates, variant_candidates
):
    base = {
        "profile_namespace": "mesh", "material_key": 200, "role": "mesh_feature",
        "route_key": "mesh_feature.bundle.v1", "lane": 0, "lane_name": "mesh_bundle",
        "candidate_glyph_ids": base_candidates,
    }
    monkeypatch.setattr(C, "_aspect_base_row", lambda rows, addr: base)
    variant = {
        "source_pool_address": "mesh:200/mesh_feature",
        "candidate_glyph_ids": variant_candidates,
    }
    with pytest.raises(C.CompileFailure) as e:
        C._aspect_variant_row(variant, [], {1000, 1001}, {})
    assert e.value.reason == "candidate_schema_invalid"
