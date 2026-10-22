#!/usr/bin/env python3
"""Open a satellite view matching the current asciiid camera position.

Usage:
  # From world coordinates (asciiid camera position):
  python3 scripts/pipeline/satellite_view.py --world 859 590

  # From lat/lon directly:
  python3 scripts/pipeline/satellite_view.py --latlon 40.9140 -73.1250

  # Custom zoom (default: 18, range 1-20):
  python3 scripts/pipeline/satellite_view.py --world 859 590 --zoom 19

  # With terrain bounds overlay:
  python3 scripts/pipeline/satellite_view.py --world 859 590 --bounds

Generates a standalone HTML file with Leaflet.js + ESRI satellite tiles
and opens it in the default browser. The view matches asciiid's camera
position so you can compare terrain side-by-side.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from osm_projection import osm_project_inverse
from osm_to_cell import A3D_EXPORT_OFFSET_X, A3D_EXPORT_OFFSET_Y

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = "sbu_sac_scale075_topo3_clean_20260508"


def load_projection(run_dir: Path) -> dict:
    meta_path = run_dir / "terrain_metadata.json"
    if not meta_path.exists():
        return {}
    meta = json.loads(meta_path.read_text())
    return {
        "scene_lat": meta.get("scene_lat", 0),
        "scene_lon": meta.get("scene_lon", 0),
        "content_scale": meta.get("content_scale", 1),
        "shift_x": meta.get("terrain_shift", {}).get("x", 0),
        "shift_y": meta.get("terrain_shift", {}).get("y", 0),
        "engine_offset_x": meta.get("engine_offset_x", A3D_EXPORT_OFFSET_X),
        "engine_offset_y": meta.get("engine_offset_y", A3D_EXPORT_OFFSET_Y),
        "cal_x": meta.get("calibration_offset_x", 0),
        "cal_y": meta.get("calibration_offset_y", 0),
        "content_bounds": meta.get("content_bounds"),
    }


def world_to_latlon(wx, wy, proj):
    x_m = (wx - proj["engine_offset_x"] - proj["cal_x"] - proj["shift_x"]) / proj["content_scale"]
    y_m = (wy - proj["engine_offset_y"] - proj["cal_y"] - proj["shift_y"]) / proj["content_scale"]
    return osm_project_inverse(x_m, y_m, proj["scene_lat"], proj["scene_lon"])


def generate_html(lat, lon, zoom, bounds_coords=None, markers=None):
    markers_js = ""
    if markers:
        for m in markers:
            markers_js += f'L.marker([{m["lat"]}, {m["lon"]}]).addTo(map).bindPopup("{m["label"]}");\n'

    bounds_js = ""
    if bounds_coords:
        sw, ne = bounds_coords
        bounds_js = f"""
        L.rectangle([[{sw[0]},{sw[1]}],[{ne[0]},{ne[1]}]], {{
            color: '#ff0', weight: 2, fill: false, dashArray: '5,5'
        }}).addTo(map).bindPopup('Terrain bounds');
        """

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Satellite View — {lat:.6f}, {lon:.6f}</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body {{ margin:0; padding:0; }}
  #map {{ position:absolute; top:0; bottom:0; width:100%; }}
  .info-box {{ position:absolute; top:10px; right:10px; z-index:1000;
    background:rgba(0,0,0,0.8); color:#fff; padding:8px 12px;
    border-radius:4px; font:13px/1.4 monospace; }}
  .info-box b {{ color:#0f0; }}
</style>
</head><body>
<div id="map"></div>
<div class="info-box">
  <b>Lat/Lon:</b> {lat:.7f}, {lon:.7f}<br>
  <b>Zoom:</b> {zoom}<br>
  Click map to copy coords
</div>
<script>
var map = L.map('map').setView([{lat}, {lon}], {zoom});

// ESRI World Imagery (free, no API key)
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
  attribution: 'Tiles: Esri, Maxar, Earthstar',
  maxZoom: 20
}}).addTo(map);

// OpenStreetMap overlay for labels
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: 'Labels: OSM',
  maxZoom: 19,
  opacity: 0.35
}}).addTo(map);

// Crosshair at center
L.marker([{lat}, {lon}], {{
  icon: L.divIcon({{
    className: '',
    html: '<div style="width:20px;height:20px;border:2px solid red;border-radius:50%;margin:-10px 0 0 -10px;"></div>',
    iconSize: [0, 0]
  }})
}}).addTo(map).bindPopup('Camera center');

{markers_js}
{bounds_js}

// Click to copy coords
map.on('click', function(e) {{
  var txt = e.latlng.lat.toFixed(7) + ', ' + e.latlng.lng.toFixed(7);
  navigator.clipboard.writeText(txt);
  L.popup().setLatLng(e.latlng).setContent(txt + '<br><small>copied</small>').openOn(map);
}});
</script>
</body></html>"""


def main():
    parser = argparse.ArgumentParser(description="Open satellite view matching asciiid camera")
    parser.add_argument("--world", nargs=2, type=float, metavar=("WX", "WY"),
                        help="World coordinates (from asciiid camera/probe)")
    parser.add_argument("--latlon", nargs=2, type=float, metavar=("LAT", "LON"),
                        help="Lat/lon coordinates directly")
    parser.add_argument("--zoom", type=int, default=17, help="Map zoom level (1-20, default 17 matches asciiid top-down)")
    parser.add_argument("--bounds", action="store_true", help="Show terrain bounds overlay")
    parser.add_argument("--run-dir", default=None, help="Pipeline run directory")
    parser.add_argument("--no-open", action="store_true", help="Generate HTML but don't open browser")
    parser.add_argument("--out", type=str, default=None, help="Output HTML path (default: temp file)")
    args = parser.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else PROJECT_ROOT / "assets" / "meshes" / "osm_runs" / DEFAULT_RUN
    proj = load_projection(run_dir)

    if args.latlon:
        lat, lon = args.latlon
    elif args.world:
        if not proj:
            print("ERROR: terrain_metadata.json not found, cannot convert world coords", file=sys.stderr)
            return 1
        lat, lon = world_to_latlon(args.world[0], args.world[1], proj)
    else:
        parser.print_help()
        return 1

    bounds_coords = None
    if args.bounds and proj.get("content_bounds"):
        cb = proj["content_bounds"]
        sw_lat, sw_lon = world_to_latlon(cb["min_x"], cb["min_y"], proj)
        ne_lat, ne_lon = world_to_latlon(cb["max_x"], cb["max_y"], proj)
        bounds_coords = ((sw_lat, sw_lon), (ne_lat, ne_lon))

    html = generate_html(lat, lon, args.zoom, bounds_coords)

    if args.out:
        out_path = args.out
    else:
        fd, out_path = tempfile.mkstemp(suffix=".html", prefix="satellite_view_")
        os.close(fd)

    with open(out_path, "w") as f:
        f.write(html)

    print(f"Satellite view: {lat:.7f}, {lon:.7f} zoom={args.zoom}")
    print(f"HTML: {out_path}")

    if not args.no_open:
        webbrowser.open(f"file://{out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
