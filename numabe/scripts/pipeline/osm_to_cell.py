#!/usr/bin/env python3
"""Convert between OSM lat/lon and A3D world/cell coordinates.

Uses the exact blosm Transverse Mercator projection with scene center
extracted from the Blender workspace file.

Usage:
  # lat/lon → A3D world coords
  python3 scripts/pipeline/osm_to_cell.py --to-world 40.9143 -73.1246

  # A3D world coords → lat/lon
  python3 scripts/pipeline/osm_to_cell.py --to-latlon 2616.8 1444.1

  # Verify projection against all buildings
  python3 scripts/pipeline/osm_to_cell.py --verify

  # Add ground truth calibration point (from asciiid INFO tab)
  python3 scripts/pipeline/osm_to_cell.py --add-ground-truth LAT LON WORLD_X WORLD_Y

Projection: Transverse Mercator (WGS84, R=6378137)
  - Scene center: scene["lat"], scene["lon"] from workspace.blend
  - world = TM(lat, lon) * content_scale + terrain_shift + A3D export offset
  - Known accuracy: 3-12m for most buildings (centroid vs instance origin mismatch)
"""
import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from osm_projection import osm_project, osm_project_inverse, latlon_to_world as _proj_latlon_to_world, world_to_latlon as _proj_world_to_latlon

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = "sbu_sac_scale075_topo3_clean_20260508"
A3D_EXPORT_OFFSET_X = -32.0
A3D_EXPORT_OFFSET_Y = -32.0


def load_run_params(run_dir: Path) -> dict:
    """Load projection parameters from a pipeline run directory."""
    manifest = json.loads((run_dir / "manifest.json").read_text())
    metadata = json.loads((run_dir / "terrain_metadata.json").read_text())

    # Prefer scene_lat/scene_lon embedded by the pipeline (since 2026-05-09).
    scene_lat = metadata.get("scene_lat")
    scene_lon = metadata.get("scene_lon")

    # Fallback 1: extract from workspace.blend via Blender (slow, needs app)
    if scene_lat is None or scene_lon is None:
        blend_path = run_dir / "workspace.blend"
        if blend_path.exists():
            try:
                import subprocess
                result = subprocess.run(
                    ["/Applications/Blender.app/Contents/MacOS/Blender",
                     "--background", str(blend_path), "--python-expr",
                     'import bpy,json;s=bpy.context.scene;print("BLOSM_LATLON="+json.dumps({"lat":s.get("lat"),"lon":s.get("lon")}))'],
                    capture_output=True, text=True, timeout=30)
                for line in result.stdout.splitlines():
                    if line.startswith("BLOSM_LATLON="):
                        d = json.loads(line[len("BLOSM_LATLON="):])
                        scene_lat = d.get("lat")
                        scene_lon = d.get("lon")
            except Exception:
                pass

    # Fallback 2: bbox midpoint (less accurate)
    if scene_lat is None or scene_lon is None:
        bbox = manifest["bbox"]
        scene_lat = (float(bbox["min_lat"]) + float(bbox["max_lat"])) / 2
        scene_lon = (float(bbox["min_lon"]) + float(bbox["max_lon"])) / 2
        print(f"WARNING: Using bbox midpoint ({scene_lat}, {scene_lon}) — less accurate", file=sys.stderr)
        print(f"  Re-run pipeline to embed scene center in terrain_metadata.json", file=sys.stderr)

    # Calibration offset: residual error between TM projection and actual A3D positions.
    # Computed from ground truth points via --add-ground-truth.
    cal_x = float(metadata.get("calibration_offset_x", 0.0))
    cal_y = float(metadata.get("calibration_offset_y", 0.0))

    return {
        "scene_lat": scene_lat,
        "scene_lon": scene_lon,
        "content_scale": float(metadata["content_scale"]),
        "shift_x": float(metadata["terrain_shift"]["x"]),
        "shift_y": float(metadata["terrain_shift"]["y"]),
        # FL-2534/FL-3853: export_a3d shifts patches and object XY by
        # PATCH_OFFSET_X/Y * PATCH_SIZE (-32,-32). Projection helpers must
        # return engine-space A3D coordinates, not Blender-space coordinates,
        # or OSM paint, satellite proof, and building source checks drift by
        # exactly 32 cells from the baked geometry.
        "engine_offset_x": float(metadata.get("engine_offset_x", A3D_EXPORT_OFFSET_X)),
        "engine_offset_y": float(metadata.get("engine_offset_y", A3D_EXPORT_OFFSET_Y)),
        "cal_x": cal_x,
        "cal_y": cal_y,
        "terrain_size": int(metadata["terrain_bounds"]["max_x"]),
        "osm_path": str(run_dir / "osm_blosm_input.osm"),
        "run_dir": str(run_dir),
        "metadata_path": str(run_dir / "terrain_metadata.json"),
    }


def latlon_to_world(lat, lon, params):
    """Convert lat/lon → A3D world coordinates using blosm TM projection + calibration."""
    wx, wy = _proj_latlon_to_world(lat, lon, params["scene_lat"], params["scene_lon"],
                                    params["content_scale"], params["shift_x"], params["shift_y"])
    return (
        wx + params.get("engine_offset_x", A3D_EXPORT_OFFSET_X) + params.get("cal_x", 0.0),
        wy + params.get("engine_offset_y", A3D_EXPORT_OFFSET_Y) + params.get("cal_y", 0.0),
    )


def world_to_latlon(wx, wy, params):
    """Convert A3D world coordinates → lat/lon using inverse blosm TM + calibration."""
    return _proj_world_to_latlon(
                                  wx - params.get("engine_offset_x", A3D_EXPORT_OFFSET_X) - params.get("cal_x", 0.0),
                                  wy - params.get("engine_offset_y", A3D_EXPORT_OFFSET_Y) - params.get("cal_y", 0.0),
                                  params["scene_lat"], params["scene_lon"],
                                  params["content_scale"], params["shift_x"], params["shift_y"])


def world_to_cell(wx, wy):
    """Convert world coords to terrain cell indices."""
    return int(math.floor(wx)), int(math.floor(wy))


def verify(params):
    """Verify projection against all named buildings in the A3D."""
    osm_path = params["osm_path"]
    if not os.path.exists(osm_path):
        print(f"OSM file not found: {osm_path}")
        return

    root = ET.parse(osm_path).getroot()
    nodes = {}
    for node in root.findall("node"):
        nodes[node.attrib["id"]] = (float(node.attrib["lat"]), float(node.attrib["lon"]))

    buildings_json = Path(params["run_dir"]) / "building_instances.json"
    if not buildings_json.exists():
        print(f"building_instances.json not found")
        return

    buildings = json.loads(buildings_json.read_text())
    errors = []

    print(f"Scene center: lat={params['scene_lat']}, lon={params['scene_lon']}")
    print(f"Content scale: {params['content_scale']}, shift: ({params['shift_x']}, {params['shift_y']})")
    print()

    for b in buildings:
        mesh = b["mesh_name"]
        # Extract OSM way ID from mesh name (e.g., way_55446707.akm → 55446707)
        if not mesh.startswith("way_"):
            continue
        way_id = mesh.replace("way_", "").replace(".akm", "")
        way = root.find(f".//way[@id='{way_id}']")
        if way is None:
            continue

        refs = [nd.attrib.get("ref") for nd in way.findall("nd")]
        pts = [(nodes[r][0], nodes[r][1]) for r in refs if r in nodes]
        if not pts:
            continue

        avg_lat = sum(p[0] for p in pts) / len(pts)
        avg_lon = sum(p[1] for p in pts) / len(pts)
        pred_x, pred_y = latlon_to_world(avg_lat, avg_lon, params)

        a3d_x, a3d_y = b["transform"][12], b["transform"][13]
        err = math.hypot(pred_x - a3d_x, pred_y - a3d_y)
        errors.append(err)

        status = "OK" if err < 50 else "WARN" if err < 150 else "BAD"
        print(f"{b['inst_name']:40s} err={err:5.1f} ({err/9:.1f}m) [{status}]")

    if errors:
        print(f"\nMean error: {sum(errors)/len(errors):.1f} world units ({sum(errors)/len(errors)/9:.1f}m)")
        print(f"Max error:  {max(errors):.1f} world units ({max(errors)/9:.1f}m)")


def add_ground_truth(lat, lon, world_x, world_y, params):
    """Compute calibration offset from a known ground truth point and save it."""
    # Project lat/lon WITHOUT calibration to get the raw projection error
    raw_wx, raw_wy = _proj_latlon_to_world(lat, lon, params["scene_lat"], params["scene_lon"],
                                            params["content_scale"], params["shift_x"], params["shift_y"])
    raw_wx += params.get("engine_offset_x", A3D_EXPORT_OFFSET_X)
    raw_wy += params.get("engine_offset_y", A3D_EXPORT_OFFSET_Y)
    cal_x = world_x - raw_wx
    cal_y = world_y - raw_wy
    err = math.hypot(cal_x, cal_y)

    print(f"Ground truth: ({lat}, {lon}) → actual ({world_x}, {world_y})")
    print(f"Raw projection: ({raw_wx:.1f}, {raw_wy:.1f})")
    print(f"Calibration offset: ({cal_x:.1f}, {cal_y:.1f})  error={err:.1f} world units")

    # Save to terrain_metadata.json
    meta_path = Path(params["metadata_path"])
    metadata = json.loads(meta_path.read_text())
    metadata["calibration_offset_x"] = round(cal_x, 1)
    metadata["calibration_offset_y"] = round(cal_y, 1)
    metadata["calibration_ground_truth"] = {
        "lat": lat, "lon": lon, "world_x": world_x, "world_y": world_y
    }
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True))
    print(f"Saved calibration to {meta_path}")

    # Verify: re-project with calibration
    params["cal_x"] = cal_x
    params["cal_y"] = cal_y
    vx, vy = latlon_to_world(lat, lon, params)
    print(f"Verify: ({lat}, {lon}) → ({vx:.1f}, {vy:.1f})  (expected {world_x}, {world_y})")


def main():
    parser = argparse.ArgumentParser(description="Convert between OSM lat/lon and A3D world coords")
    parser.add_argument("--run-dir", default=None, help="Pipeline run directory")
    parser.add_argument("--to-world", nargs=2, type=float, metavar=("LAT", "LON"),
                        help="Convert lat/lon to A3D world coords")
    parser.add_argument("--to-latlon", nargs=2, type=float, metavar=("WX", "WY"),
                        help="Convert A3D world coords to lat/lon")
    parser.add_argument("--verify", action="store_true", help="Verify projection against buildings")
    parser.add_argument("--add-ground-truth", nargs=4, type=float,
                        metavar=("LAT", "LON", "WORLD_X", "WORLD_Y"),
                        help="Calibrate projection from a known world position")
    args = parser.parse_args()

    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        run_dir = PROJECT_ROOT / "assets" / "meshes" / "osm_runs" / DEFAULT_RUN

    params = load_run_params(run_dir)

    if args.add_ground_truth:
        lat, lon, wx, wy = args.add_ground_truth
        add_ground_truth(lat, lon, wx, wy, params)
    elif args.to_world:
        lat, lon = args.to_world
        wx, wy = latlon_to_world(lat, lon, params)
        cx, cy = world_to_cell(wx, wy)
        print(f"lat/lon: ({lat}, {lon})")
        print(f"world:   ({wx:.1f}, {wy:.1f})")
        print(f"cell:    ({cx}, {cy})")
    elif args.to_latlon:
        wx, wy = args.to_latlon
        lat, lon = world_to_latlon(wx, wy, params)
        print(f"world:   ({wx}, {wy})")
        print(f"lat/lon: ({lat:.7f}, {lon:.7f})")
        print(f"google:  https://www.google.com/maps/@{lat:.7f},{lon:.7f},19z?layer=satellite")
    elif args.verify:
        verify(params)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
