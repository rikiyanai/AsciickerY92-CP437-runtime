"""FL-4512 shared-data ABI: one version/capability contract, no silent loss.

Interface tests cross the same paths production callers use: the FL-4588
document codec for parse/write, scripts/asciid.py for the Classic
intake decision and the offline downgrade, and scripts/apply_map_edits.py
for the write front door. The C++ mirror bounds in
shared/runtime_data/a3d_abi.h are pinned equal to the Python contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.a3d import compatibility as compat
from scripts import apply_map_edits
from scripts.a3d import runtime_data_contract as data_contract
from scripts.fl4588.a3d_document_codec import (
    A3dDocument,
    parse_document,
    serialize_canonical,
)


def _mesh(uid=0, material_key=200, override=0, story=5, flags=3):
    return {
        "kind": "mesh", "mesh": b"probe.akm", "name": b"probe-inst",
        "tm": struct.pack("<16d", *([1.0] * 16)),
        "flags": flags, "story": story,
        "material_key": material_key, "override": override, "uid": uid,
    }


def _doc_v6(instances, player_start=None):
    return serialize_canonical(A3dDocument(
        materials=bytes(256 * 4 * 16 * 8),
        world_format_version=6,
        instances=instances,
        player_start=player_start,
    ))


def _cpp_constants():
    text = (ROOT / "shared" / "runtime_data" / "a3d_abi.h").read_text(encoding="utf-8")
    found = dict(re.findall(r"k(StoryIdVersion|ItemRecordVersion|PlayerStartVersion|"
                            r"ClassicMaxVersion|ClassicWriteVersion|CurrentMaxVersion|"
                            r"CurrentWriteVersion|MaterialKeyVersion|MeshUidVersion)"
                            r"\s*=\s*(\d+)", text))
    return {k: int(v) for k, v in found.items()}


def test_cpp_header_bounds_equal_python_contract():
    cpp = _cpp_constants()
    assert cpp == {
        "ClassicMaxVersion": compat.CLASSIC_MAX_WORLD_VERSION,
        "ClassicWriteVersion": data_contract.CLASSIC_WRITE_WORLD_VERSION,
        "CurrentMaxVersion": compat.CURRENT_MAX_WORLD_VERSION,
        "CurrentWriteVersion": compat.CURRENT_WRITE_WORLD_VERSION,
        "StoryIdVersion": data_contract.STORY_ID_VERSION,
        "ItemRecordVersion": data_contract.ITEM_RECORD_VERSION,
        "PlayerStartVersion": data_contract.PLAYER_START_VERSION,
        "MaterialKeyVersion": compat.MATERIAL_KEY_VERSION,
        "MeshUidVersion": compat.MESH_UID_VERSION,
    }


def test_shared_runtime_data_contract_validates_and_classifies_packages():
    assert data_contract.validate() == []
    assert data_contract.package_for_path("assets/a3d/game_map_y8.a3d") == [
        "shared_world_maps"]
    assert data_contract.package_for_path("assets/fonts/cp437_12x12.png") == [
        "shared_authoring_presentation"]
    assert data_contract.package_for_path(
        "assets/glyphs/generated/fl4512.compiled_pool.v1.json") == [
            "glyph_data_tree", "fl4512_compiled_selection"]


def test_shared_contract_names_asset_compatibility_and_product_boundaries():
    contract = data_contract.CONTRACT
    assert set(contract["asset_formats"]) == {
        "a3d_world",
        "akm_mesh",
        "xp_sprite",
        "fonts_palettes_and_ui",
        "audio_samples",
        "glyph_data",
    }
    assert contract["filesystem_semantics"]["conflict_detection_supported"] is False
    assert contract["data_packages"]["shared_world_maps"]["runtime_writers"] == [
        "asciiid_classic"
    ]
    shared_maps_rule = contract["data_packages"]["shared_world_maps"].get("rule", "")
    assert "SaveMapToPath" in shared_maps_rule
    assert "browser" in shared_maps_rule
    assert "concurrent" in shared_maps_rule
    assert set(contract["data_packages"]["fl4512_compiled_selection"][
        "forbidden_consumers"
    ]) == {"asciiid_classic", "game_classic_cp437"}
    classic = contract["products"]["asciiid_classic"]
    presenter = contract["products"]["asciicker_presenter"]
    assert classic["app_bundle"] == ".run/ASCIIID Classic.app"
    assert classic["bundle_id"] == "world.riki.asciiid.classic"
    assert presenter["native_app_bundle"] == ".run/ASCIID FL4512 Presenter.app"
    assert presenter["native_bundle_id"] == "world.riki.asciicker.presenter"
    assert presenter["product_label"] == "ASCIID FL4512 Presenter"
    assert presenter["html_surface_label"] == "ASCIID FL4512 Presenter HTML Authoring"
    assert presenter["html_app_bundle"] == "dist/ASCIID FL4512 Presenter HTML Authoring.app"
    assert presenter["html_bundle_id"] == "world.riki.asciicker.presenter.html"
    assert "map-asciicker-presenter-html" in presenter["launcher_actions"]
    assert contract["products"]["game_terminal"]["binary"] == ".run/game_term"


def test_classic_accepts_v0_through_v4_only():
    for version in range(0, 5):
        assert compat.classic_accepts(version) == (True, "ok")
        assert compat.classic_reject_reason(version) is None


def test_classic_rejects_v5_v6_before_payload_parse():
    for version in (5, 6):
        accepted, reason = compat.classic_accepts(version)
        assert accepted is False
        assert reason == compat.CLASSIC_REJECT_REASON
        text = compat.classic_reject_reason(version)
        assert text is not None
        assert f"v{version}" in text and "before payload parse" in text


def test_lossless_v6_downgrade_preserves_common_fields():
    start = struct.pack("<5f", 1.0, 2.0, 3.0, 0.5, 0.0)
    before = parse_document(_doc_v6([_mesh()], player_start=start))
    receipt = compat.downgrade_to_classic(_doc_v6([_mesh()], player_start=start))
    assert receipt["target_version"] == 4
    assert receipt["lossless"] is True
    after = parse_document(receipt["output"])
    assert after.world_format_version == 4
    assert len(after.instances) == 1
    for key in ("mesh", "name", "tm", "flags", "story"):
        assert after.instances[0][key] == before.instances[0][key]
    assert after.player_start == start


def test_nondefault_uid_refused_without_opt_in_and_named():
    raw = _doc_v6([_mesh(uid=1)])
    with pytest.raises(compat.CompatibilityError, match=r"mesh_uid"):
        compat.downgrade_to_classic(raw)


def test_nondefault_material_key_refused_without_opt_in_and_named():
    raw = _doc_v6([_mesh(material_key=10, override=1)])
    with pytest.raises(compat.CompatibilityError, match=r"material_key"):
        compat.downgrade_to_classic(raw)


def test_lossy_opt_in_emits_receipt_and_v4_bytes():
    raw = _doc_v6([_mesh(uid=1, material_key=10, override=1)])
    receipt = compat.downgrade_to_classic(raw, allow_lossy=True)
    assert receipt["lossless"] is False
    assert receipt["lossy_opt_in"] is True
    assert receipt["lossy_records"] == [
        {"index": 0, "fields": ["material_key", "mesh_uid"]}]
    after = parse_document(receipt["output"])
    assert after.world_format_version == 4
    assert after.instances[0]["mesh"] == b"probe.akm"
    assert after.instances[0]["uid"] == 0


def test_upgrade_front_door_refuses_v6_source_with_shared_reason(tmp_path):
    target = tmp_path / "v6probe.a3d"
    target.write_bytes(_doc_v6([_mesh()]))
    with pytest.raises(apply_map_edits.MapEditError, match=r"downgrade_to_classic"):
        apply_map_edits.upgrade_world_format(target, to_version=4)


def test_cli_conversion_requires_distinct_output_and_writes_receipt(tmp_path):
    source = tmp_path / "source-v6.a3d"
    output = tmp_path / "classic-v4.a3d"
    source.write_bytes(_doc_v6([_mesh()]))
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/asciid.py"), "downgrade-to-classic",
         str(source), "--out", str(output)],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    receipt_path = output.with_suffix(output.suffix + ".conversion.json")
    assert output.is_file()
    assert receipt_path.is_file()
    assert "receipt=" in result.stdout
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert receipt["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert receipt["contract"] == "shared/runtime_data/runtime_data_contract.v1.json"

    refused = subprocess.run(
        [sys.executable, str(ROOT / "scripts/asciid.py"), "downgrade-to-classic",
         str(source), "--out", str(source)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert refused.returncode == 1
    assert "source overwrite forbidden" in refused.stdout
