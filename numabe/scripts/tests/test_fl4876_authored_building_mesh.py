"""FL-4876 AUTHORED-MESH-01: a hand-authored building mesh replaces the terrain box and roof field.

The owner record is authored_building_appearance.json buildings[<feature>]["authored_mesh"]; compile_source_palette
carries it to the building bake; bake_osm_building_footprints.bake() emits the mesh as the building's mass
(mass_<source>.akm, collision_instance) and stamps nothing into the height lattice for that footprint.  Every other
building is untouched.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.bake_osm_building_footprints import bake, fmt, write_a3d, load_a3d  # noqa: E402
from scripts.pipeline.compile_source_palette import _resolve_authored_mesh  # noqa: E402

BUNDLE = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"

# metres: a 2 x 2 m box 6 m tall plus a 1 m post on top (max z 6.0, min z 0); colours are off the terminal cube
_VERTS = [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0), (-1, -1, 6), (1, -1, 6), (1, 1, 6), (-1, 1, 6)]
_FACES = [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]


def _write_mesh(path: Path) -> None:
    lines = ["ply", "format ascii 1.0", "element vertex 8", "property float x", "property float y", "property float z",
             "property float nx", "property float ny", "property float nz", "property uchar red",
             "property uchar green", "property uchar blue", "property uchar alpha", "element face 6",
             "property list uchar uint vertex_indices", "end_header"]
    lines += [f"{x} {y} {z} 0 0 1 233 232 228 0" for x, y, z in _VERTS]
    lines += ["4 " + " ".join(map(str, f)) for f in _FACES]
    path.write_text("\n".join(lines) + "\n")


def _ground(tmp_path: Path) -> Path:
    patches = []
    for py in range(2):
        for px in range(4):
            p = fmt.A3DPatch(x=px, y=py)
            for row in p.height:
                for i in range(len(row)):
                    row[i] = 128
            patches.append(p)
    path = tmp_path / "ground.a3d"
    # tail = 256 materials, world format version -4, no instances, no player start, no enemy gens, no markers
    tail = (b"\x00" * (256 * 512) + struct.pack("<i", -4) + struct.pack("<i", 0) * 4)
    write_a3d(path, fmt.A3DHeader(len(patches)), patches, tail)
    return path


def _spec(source: str, centre: tuple, half: float = 3.0) -> dict:
    cx, cy = centre
    return {"source": source, "feature_id": "feature:building_footprint:" + source, "inst_name": "mass_" + source[4:],
            "footprint": [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)],
            "roof_height_m": 5.0, "content_scale": 3.0, "bake_height": 1}


def _palette(authored: dict | None) -> dict:
    entry = {"wall": 6, "roof": 7}
    if authored:
        entry["authored_appearance"] = {"authored_mesh": authored}
    return {"materials": {"6": {"bg": [200, 200, 200]}, "7": {"bg": [90, 90, 90]}},
            "building_material": {"way/1": dict(entry), "way/2": {"wall": 6, "roof": 7}}}


def _heights(path: Path):
    _header, patches, _tail = load_a3d(path)
    return {(p.x, p.y): [row[:] for row in p.height] for p in patches}


def test_authored_mesh_replaces_terrain_box_and_emits_the_mesh(tmp_path):
    ground = _ground(tmp_path)
    mesh = tmp_path / "hall.akm"
    _write_mesh(mesh)
    authored = {"file": str(mesh), "units": "metres", "footprint_area_m2": 4.0, "height_m": 6.0,
                "height_basis": "test claim", "generator": "test"}
    # a 6 x 6 world-unit footprint at content_scale 3 is 2 x 2 m = 4 m2
    specs = [_spec("way/1", (8.0, 8.0)), _spec("way/2", (22.0, 8.0))]
    sp = tmp_path / "specs.json"
    sp.write_text(json.dumps(specs))
    out = tmp_path / "out.a3d"
    meshes = tmp_path / "meshes"
    receipt = bake(ground, sp, out, 5, 0.0, 0.0, palette=_palette(authored), collision_mesh_root=meshes)
    assert receipt["ok"]
    rows = {r["source"]: r for r in receipt["per_building"]}
    row = rows["way/1"]
    # no terrain box and no roof field for the authored footprint ...
    assert row["height_model"] == "authored_akm_mesh"
    assert row["height_vertices"] == 0 and row["visual_cells"] == 0 and row["roof_field"] is None
    before, after = _heights(ground), _heights(out)
    for (px, py), grid in after.items():
        for hy, line in enumerate(grid):
            for hx, h in enumerate(line):
                wx, wy = px * 8 + hx * 2, py * 8 + hy * 2
                if 5 <= wx <= 11 and 5 <= wy <= 11:
                    assert h == before[(px, py)][hy][hx], "authored footprint must not raise the terrain"
    # ... while the neighbouring ordinary building still bakes its box
    assert rows["way/2"]["height_model"] != "authored_akm_mesh" and rows["way/2"]["height_vertices"] > 0
    # the mesh is emitted as the building's mass, scaled by content_scale and placed at the footprint centroid
    inst = row["collision_instance"]
    assert inst["mesh_name"] == "mass_way_1.akm" and row["mesh_promotion_basis"] == "authored_mesh"
    assert inst["transform"][12:14] == pytest.approx([8.0, 8.0])
    assert inst["realized_roof_m"] == pytest.approx(6.0) and inst["intended_roof_m"] == 6.0
    text = (meshes / "mass_way_1.akm").read_text()
    body = text.split("end_header\n")[1].splitlines()
    zs = [float(r.split()[2]) for r in body[:8]]
    assert max(zs) == pytest.approx(18.0) and min(zs) == 0.0          # 6 m x content_scale 3
    assert all(r.split()[6:9] == ["233", "232", "228"] for r in body[:8])  # exact colour, not snapped to the cube
    assert not (meshes / "mass_way_2.akm").exists()                    # the ordinary building is untouched


def test_wrong_footprint_for_the_authored_mesh_is_refused(tmp_path):
    ground = _ground(tmp_path)
    mesh = tmp_path / "hall.akm"
    _write_mesh(mesh)
    authored = {"file": str(mesh), "footprint_area_m2": 40.0, "height_m": 6.0}
    sp = tmp_path / "specs.json"
    sp.write_text(json.dumps([_spec("way/1", (8.0, 8.0))]))
    with pytest.raises(ValueError, match="authored for a 40.0 m2 footprint"):
        bake(ground, sp, tmp_path / "out.a3d", 5, 0.0, 0.0, palette=_palette(authored),
             collision_mesh_root=tmp_path / "meshes")


def test_palette_resolves_the_mesh_file_against_the_owner_directory():
    rec = {"file": "authored_meshes/x.akm", "height_m": 3.0}
    out = _resolve_authored_mesh(rec, str(BUNDLE / "authored_building_appearance.json"))
    assert out["file"] == str(BUNDLE / "authored_meshes/x.akm") and out["height_m"] == 3.0
    assert _resolve_authored_mesh(None, "x.json") is None and _resolve_authored_mesh({}, "x.json") is None


def test_owner_records_name_meshes_whose_height_is_the_claimed_height():
    """The shipped records: every authored_mesh file exists and its z extent is 0..height_m (generator drift guard)."""
    doc = json.loads((BUNDLE / "authored_building_appearance.json").read_text())
    named = {k: v["authored_mesh"] for k, v in doc["buildings"].items() if v.get("authored_mesh")}
    assert "feature:building_footprint:way/225101288" in named
    assert "feature:building_footprint:way/225101048" in named
    from scripts.pipeline.bake_osm_building_footprints import _load_authored_akm
    for feature, rec in named.items():
        verts, faces = _load_authored_akm(BUNDLE / rec["file"])
        zs = [v[2] for v in verts]
        assert min(zs) == 0.0 and max(zs) == pytest.approx(rec["height_m"], abs=0.011), feature
        assert any(v[9] <= 128 for v in verts) and any(v[9] == 255 for v in verts), "collision and presentation faces"
        assert all(len(f) in (3, 4) and len(set(f)) == len(f) for f in faces)
