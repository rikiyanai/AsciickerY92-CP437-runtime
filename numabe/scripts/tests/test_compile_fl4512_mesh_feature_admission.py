"""Focused tests for the v3 admission artifact owner."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import compile_fl4512_mesh_feature_admission as C  # noqa: E402
import fl4512_mesh_feature_candidate_admission_v2 as A  # noqa: E402
from fl4512_u7_mask import topology_counts  # noqa: E402


def _seed_pairs():
    artifact = json.loads((ROOT / "assets/glyphs/generated/"
                           "fl4512.mesh_feature_candidate_admission.v2.json").read_text())
    catalog = json.loads((ROOT / "assets/glyphs/generated/"
                          "unicode16.onecell.v1.shape_catalog.json").read_text())
    wanted = set(artifact["receipt"]["selected_glyph_ids"])
    return [(entry["glyph_id"], int(entry["mask8_u64"], 16))
            for entry in catalog["entries"] if entry["glyph_id"] in wanted]


def _catalog(pairs):
    entries = []
    for gid, mask in pairs:
        _skel, ep, jn = topology_counts(mask)
        entries.append({
            "glyph_id": gid, "mask8_u64": f"0x{mask:016x}",
            "endpoint_count": ep, "junction_cluster_count": jn,
            "font_index": 0, "page16_tile_sha256": "00" * 32,
            "scorer_id": C.EXPECTED_SCORER, "unicode_scalar": gid,
        })
    return {"schema": C.CATALOG_SCHEMA, "scorer_id": C.EXPECTED_SCORER,
            "content_pack_id": "synthetic.v3", "entries": entries,
            "manifest_hash": "de" * 32, "page16_sha256_by_page": {}}


def _registry(pairs):
    bindings = [{"glyph_id": gid, "layer_index": 0, "pack_index": 0,
                 "page_index": 0, "source_column": i % 16,
                 "source_row": i // 16} for i, (gid, _mask) in enumerate(pairs)]
    page = {"content_pack_id": "synthetic.v3", "height_px": 32,
            "layer_index": 0, "pack_index": 0, "page16_format": "png_rgba8",
            "page16_path": "assets/glyphs/atlases/synthetic.png",
            "page16_sha256": "11" * 32, "page_index": 0,
            "row_count": 2, "width_px": 256}
    pack = {"aoa_canonical_sha256": "22" * 32,
            "aoa_path": "assets/glyphs/generated/synthetic.aoa.json",
            "content_pack_id": "synthetic.v3", "layer_count": 1,
            "layer_first": 0, "pack_index": 0, "page_count": 1}
    body = {"schema": C.REGISTRY_SCHEMA, "bindings": bindings,
            "binding_count": len(bindings), "cell_px": 16, "columns": 16,
            "lut_mapping": {"columns": 2048}, "packs": [pack],
            "page_count": 1, "pages": [page], "proof_state": "TEST",
            "runtime_consumed": True}
    body["registry_sha256"] = hashlib.sha256(
        C.canonical_json_bytes(body)).hexdigest()
    return body


@pytest.fixture()
def source_paths(tmp_path):
    pairs = _seed_pairs()
    cpath, rpath = tmp_path / "catalog.json", tmp_path / "registry.json"
    cpath.write_text(json.dumps(_catalog(pairs)))
    rpath.write_text(json.dumps(_registry(pairs)))
    return cpath, rpath


def test_v3_identity_and_stable_artifact_address():
    assert A.GRAMMAR_ID.endswith(".v3")
    assert A.RECEIPT_SCHEMA.endswith(".v3")
    assert C.ARTIFACT_SCHEMA.endswith(".v3")
    assert C.GENERATOR_ID.endswith(".v3")
    # The historical filename is the single stable artifact address. Wire
    # schema/generator/grammar identities are v3; no v2 reader or writer exists.
    assert C.ARTIFACT_PATH.name == "fl4512.mesh_feature_candidate_admission.v2.json"


def test_compile_and_fast_full_validate(source_paths):
    cpath, rpath = source_paths
    body = C.compile_artifact(cpath, rpath)
    assert body["schema"] == C.ARTIFACT_SCHEMA
    assert body["generator_id"] == C.GENERATOR_ID
    assert len(body["selected_row"]) == A.ROW_CAPACITY
    C.validate_artifact(body, cpath, rpath, full_receipt=False)
    C.validate_artifact(body, cpath, rpath, full_receipt=True)


def test_artifact_bytes_are_deterministic(source_paths):
    cpath, rpath = source_paths
    first = C.canonical_json_bytes(C.compile_artifact(cpath, rpath))
    second = C.canonical_json_bytes(C.compile_artifact(cpath, rpath))
    assert first == second


def test_stale_v2_artifact_is_rejected(source_paths):
    cpath, rpath = source_paths
    body = C.compile_artifact(cpath, rpath)
    body["schema"] = "fl4512.mesh_feature_candidate_admission_artifact.v2"
    with pytest.raises(C.CompileError) as exc:
        C.validate_artifact(body, cpath, rpath)
    assert exc.value.reason == C.REASON_ARTIFACT_SCHEMA_MISMATCH


def test_receipt_requires_selection_provenance(source_paths):
    cpath, rpath = source_paths
    body = C.compile_artifact(cpath, rpath)
    body["receipt"]["selection_algorithm"] = "old.greedy"
    body["receipt"]["content_hash"] = A._content_hash(body["receipt"])
    body["artifact_content_hash"] = "0" * 64
    with pytest.raises(C.CompileError) as exc:
        C.validate_artifact(body, cpath, rpath)
    assert exc.value.reason in {
        C.REASON_RECEIPT_IDENTITY_MISMATCH,
        C.REASON_ARTIFACT_HASH_MISMATCH,
    }
