"""inspection_maps.py BUILD_DIR OUT_DIR -- viewing aid only (decides nothing, never edits the build):
for each place changed by the 2026-10-12/13 manual work, write OUT_DIR/<tag>.a3d = a copy of
BUILD_DIR/output.a3d whose map player-start is moved to a stand point 4 m in front of the target (toward
its nearest road, else its camera side), so the headed presenter -- whose camera starts at the player
start and has no pan control (FL-4588) -- opens looking at it. Uses the repository's own player-start
writer (docs/agent/cli-anything/a3d_edit.py set-player-start). Prints tag, target and stand coordinates."""
import json, math, shutil, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
build, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
meta = json.loads((build / "terrain_metadata.json").read_text())
s = meta["content_scale"]
to_map = lambda E, N: ((E - meta["origin_easting"]) * s + meta["offset_x"], (N - meta["origin_northing"]) * s + meta["offset_y"])
zones = json.loads((build / "observation_registration/placement_zones.json").read_text())
roads = [z["polyline_epsg32654"] for z in zones if str(z["carrier_class"]).startswith("road") and z.get("polyline_epsg32654")]
fx = {f["fixture_id"]: f for f in json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
                                                      "authored_scene_fixtures.json").read_text())["fixtures"]}
T = [("crossing", 379755.6, 3938508.0), ("shige_sakurazaka", 379790.6, 3938490.4), ("unagi_tempura", 379789.9, 3938498.4),
     ("pony_cleaner", 379769.6, 3938509.7)]
for k, tag in (("SF-4876-0395", "vending_0395"), ("SF-4876-0450", "pole_0450"), ("SF-4876-0294", "pole_0294"),
               ("SF-4876-0211", "lamp_0211"), ("SF-4876-X001", "guardrail_X001"), ("SF-4876-X002", "railfence_X002"),
               ("SF-4876-X003", "greenband_X003"), ("SF-4876-X004", "blockwall_X004")):
    T.append((tag, fx[k]["E"], fx[k]["N"]))


def nearest_road(p):
    best = None
    for pl in roads:
        for a, b in zip(pl, pl[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy or 1e-12
            t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
            q = (a[0] + t * dx, a[1] + t * dy); d = math.dist(p, q)
            if best is None or d < best[0]:
                best = (d, q)
    return best


for tag, E, N in T:
    d, q = nearest_road((E, N))
    if d > 0.5:
        ux, uy = (q[0] - E) / d, (q[1] - N) / d
    else:
        ux, uy = 0.0, -1.0
    # The classic editor centres its view ON the player start; the camera sits on the side of yaw-45
    # (calibrated 2026-10-13 on the 29-2 shopfront: its SW face, bearing ~217, shows at yaw 270).
    # So centre on the target and put the camera on its road side.
    tx, ty = to_map(E, N)
    road_bearing = math.degrees(math.atan2(ux, uy)) % 360
    yaw = (road_bearing + 45.0) % 360
    dst = out / (tag + ".a3d")
    r = subprocess.run([sys.executable, str(HERE / "set_view.py"), str(build / "output.a3d"), str(dst),
                        "%.2f" % tx, "%.2f" % ty, "%.1f" % yaw], capture_output=True, text=True)
    print("%-18s target map (%.1f, %.1f) road side %.0f deg -> yaw %.0f  %s" % (tag, tx, ty, road_bearing, yaw,
          (r.stdout.strip().splitlines() or ["?"])[-1] if r.returncode == 0 else ("FAIL " + (r.stderr or r.stdout)[-200:])))
