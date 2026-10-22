"""Tests for scripts/measure_silhouette_turnover.py.

Synthetic unit-cube artifact (12 triangles, welded edges, role 0,
face normals as edge normals) plus tiny two/three-frame manifests:
- identical eye positions -> sharing 1.0, zero polyline distance.
- 90-degree-apart eyes (tilted look-at so no dot lands exactly on
  zero) -> nonempty silhouette sets that differ (sharing below 1.0).
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "measure_silhouette_turnover.py"

VERTS = [(-1.0, -1.0, -1.0), (1.0, -1.0, -1.0),
         (1.0, 1.0, -1.0), (-1.0, 1.0, -1.0),
         (-1.0, -1.0, 1.0), (1.0, -1.0, 1.0),
         (1.0, 1.0, 1.0), (-1.0, 1.0, 1.0)]
FACES = [
    ((0, 1, 2), (0.0, 0.0, -1.0)),
    ((0, 2, 3), (0.0, 0.0, -1.0)),
    ((4, 6, 5), (0.0, 0.0, 1.0)),
    ((4, 7, 6), (0.0, 0.0, 1.0)),
    ((0, 3, 7), (-1.0, 0.0, 0.0)),
    ((0, 7, 4), (-1.0, 0.0, 0.0)),
    ((1, 5, 6), (1.0, 0.0, 0.0)),
    ((1, 6, 2), (1.0, 0.0, 0.0)),
    ((0, 4, 5), (0.0, -1.0, 0.0)),
    ((0, 5, 1), (0.0, -1.0, 0.0)),
    ((3, 2, 6), (0.0, 1.0, 0.0)),
    ((3, 6, 7), (0.0, 1.0, 0.0)),
]


def build_cube_artifact(path: Path) -> None:
    edge_faces = {}
    for fi, ((a, b, c), _) in enumerate(FACES):
        for u, v in ((a, b), (b, c), (c, a)):
            key = (min(u, v), max(u, v))
            edge_faces.setdefault(key, []).append(fi)
    count = len(edge_faces)
    assert count == 18  # 12 cube edges plus 6 coplanar face diagonals
    assert all(len(v) == 2 for v in edge_faces.values())
    floats, uints = [], []
    for (u, v), (fa, fb) in sorted(edge_faces.items()):
        na = FACES[fa][1]
        nb = FACES[fb][1]
        floats += [VERTS[u][0], VERTS[u][1], VERTS[u][2], 0.0,
                   VERTS[v][0], VERTS[v][1], VERTS[v][2], 0.0,
                   na[0], na[1], na[2], 0.0,
                   nb[0], nb[1], nb[2], 0.0]
        uints += [fa, fb, 0, 7]
    # v2 run columns: singleton runs (run_id = index), so run_jaccard must
    # equal edge jaccard exactly on every pair below.
    run_uints = []
    for i in range(count):
        run_uints.extend([i, 0])
    doc = {"edge_rows": {"floats": floats, "uints": uints,
                         "run_uints": run_uints},
           "edge_table": {"schema": "fl4359.edge_rows.v2", "count": count},
           "face_count": len(FACES),
           "dimensions_xyz": [2.0, 2.0, 2.0]}
    path.write_text(json.dumps(doc))


def write_manifest(path: Path, positions) -> None:
    lines = ["# test manifest"]
    for i, pos in enumerate(positions):
        lines.append(
            f"frame: seq={i} engine={i} unix_ms=0 rendered={i} f10=0:NORMAL "
            f"camera=perspective pos={pos} fov=58.00 png=frame_{i:05d}.png")
    path.write_text("\n".join(lines) + "\n")


def run_tool(artifact: Path, manifest: Path, *extra: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(artifact),
         "--manifest", str(manifest), "--mode", "ortho", *extra],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_zero_step_shares_everything(tmp_path: Path) -> None:
    artifact = tmp_path / "cube_faces.json"
    build_cube_artifact(artifact)
    manifest = tmp_path / "manifest.txt"
    write_manifest(manifest, ["0.3,0.2,10", "0.3,0.2,10"])
    result = run_tool(artifact, manifest, "--curve", "1.0")
    entry = result["assets"]["cube_faces"]
    pair = entry["modes"]["ortho"]["pairs"][0]
    assert pair["n_a"] > 0
    assert pair["jaccard"] == 1.0
    assert pair["mean_dist_obj"] == 0.0
    assert pair["run_jaccard"] == pair["jaccard"] == 1.0


def test_ninety_degree_step_drops_sharing(tmp_path: Path) -> None:
    artifact = tmp_path / "cube_faces.json"
    build_cube_artifact(artifact)
    manifest = tmp_path / "manifest.txt"
    write_manifest(manifest, ["0,0,10", "10,0,0"])
    result = run_tool(artifact, manifest, "--lookat", "0.3,0.2,0",
                      "--curve", "1.0")
    entry = result["assets"]["cube_faces"]
    pair = entry["modes"]["ortho"]["pairs"][0]
    assert pair["n_a"] > 0 and pair["n_b"] > 0
    assert pair["jaccard"] < 1.0
    assert "verdict_manifest" in entry
