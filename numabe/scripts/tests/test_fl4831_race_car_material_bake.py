"""Race-car geometry must never inherit the airplane material columns."""

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BAKER_PATH = ROOT / "scripts/bake_akm_to_static_world_faces.py"
SOURCE = ROOT / "assets/meshes/incoming/race_car_body.akm"
SHIPPED = ROOT / "godot_project/assets/world_mesh/race_car_faces.json"
BODY_MAT = 218
ACCENT_MAT = 219
AIRPLANE_MATS = {200, 201}


def _load_baker():
    spec = importlib.util.spec_from_file_location("static_face_baker", BAKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _face_materials(doc):
    return {int(doc["rows"][offset + 3]) for offset in range(0, len(doc["rows"]), 16)}


def _edge_materials(doc):
    uints = doc["edge_rows"]["uints"]
    return {int(uints[offset + 3]) for offset in range(0, len(uints), 4)}


def _assert_dedicated_car_materials(doc):
    assert doc["mats"] == {"body": BODY_MAT, "accent": ACCENT_MAT}
    assert _face_materials(doc) == {BODY_MAT, ACCENT_MAT}
    assert _edge_materials(doc) == {BODY_MAT, ACCENT_MAT}
    assert _face_materials(doc).isdisjoint(AIRPLANE_MATS)
    assert _edge_materials(doc).isdisjoint(AIRPLANE_MATS)


def test_shipped_car_owns_face_and_edge_materials():
    doc = json.loads(SHIPPED.read_text())
    _assert_dedicated_car_materials(doc)
    assert "merge_race.js" not in doc["baker"]


def test_race_car_rebake_cannot_restore_airplane_materials(tmp_path):
    baker = _load_baker()
    output = tmp_path / "race_car_body_faces.json"
    baker.bake(
        str(SOURCE),
        str(output),
        4.0,
        0.82,
        "airframe",
        forward_axis="+z",
        up_axis="+z",
        airframe_body_mat=BODY_MAT,
        airframe_accent_mat=ACCENT_MAT,
    )
    _assert_dedicated_car_materials(json.loads(output.read_text()))
