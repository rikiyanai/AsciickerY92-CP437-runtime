"""FL-4873: the replay source identity covers the True FF scope only.

A baseline replay of a True FF defect must not be refused because another lane
edited gameplay, product water or the server; it must be refused, naming the
files, when the True FF renderer that drew the recording changed.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "recording"))
import f9_source_identity as sid  # noqa: E402


def test_scope_excludes_unrelated_lanes_and_includes_the_true_ff_path():
    files, status = sid.executed_source_files()
    assert status == "captured"
    assert "godot_project/render/ascii/structure/true_ff_gbuffer.glsl" in files
    assert "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd" in files
    assert "godot_project/render/api/ASCIICompositorEffect.gd" in files
    assert "godot_project/render/api/PresentationModes.gd" in files
    assert "godot_project/scripts/World.gd" not in files
    assert not any(name.startswith("colyseus_server/") for name in files)
    assert not any(name.startswith("godot_project/render/product/") for name in files)
    assert not any(name.endswith((".import", ".uid")) for name in files)


def test_out_of_scope_edit_keeps_the_fingerprint_and_in_scope_edit_names_the_file():
    recorded = {"godot_project/render/ascii/a.glsl": "1" * 64,
                "godot_project/render/ascii/b.gd": "2" * 64}
    same = dict(recorded)
    assert sid.inventory_sha256(same) == sid.inventory_sha256(recorded)
    assert sid.source_drift(recorded, same) == []
    edited = dict(recorded, **{"godot_project/render/ascii/b.gd": "3" * 64})
    assert sid.inventory_sha256(edited) != sid.inventory_sha256(recorded)
    assert sid.source_drift(recorded, edited) == ["changed:godot_project/render/ascii/b.gd"]
    grown = dict(recorded, **{"godot_project/render/ascii/c.glslinc": "4" * 64})
    assert sid.source_drift(recorded, grown) == ["added:godot_project/render/ascii/c.glslinc"]
    shrunk = {"godot_project/render/ascii/a.glsl": "1" * 64}
    assert sid.source_drift(recorded, shrunk) == ["removed:godot_project/render/ascii/b.gd"]


def test_launch_identity_carries_the_inventory_it_fingerprints():
    doc = sid.launch_source_identity("test")
    assert doc["executed_source_status"] == "captured"
    assert doc["executed_source_sha256"] == sid.inventory_sha256(doc["executed_source_files"])
    assert doc["executed_source_paths"] == list(sid.TRUE_FF_SCOPE)
