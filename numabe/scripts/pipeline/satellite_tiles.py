#!/usr/bin/env python3
"""Fetch ESRI World Imagery satellite tiles for a geographic bounding box.

Usage:
  # Fetch tiles from terrain_metadata.json bounds:
  python3 scripts/pipeline/satellite_tiles.py --metadata terrain_metadata.json --zoom 18

  # Custom bounding box:
  python3 scripts/pipeline/satellite_tiles.py --bbox 40.91 -73.13 40.92 -73.12 --zoom 18

  # Custom cache dir and tile budget:
  python3 scripts/pipeline/satellite_tiles.py --metadata terrain_metadata.json --zoom 18 \\
      --cache-dir ./cache --max-tiles 200

Tiles are cached to disk so re-runs skip already-fetched tiles.
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from osm_projection import osm_project_inverse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = "sbu_sac_scale075_topo3_clean_20260508"

ESRI_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
TILE_SIZE = 256  # pixels per tile edge
MERCATOR_LAT_LIMIT = 85.0511  # Web Mercator singularity
FETCH_DELAY = 0.5  # seconds between network fetches
MAX_RETRIES = 3
USER_AGENT = "asciicker-pipeline/1.0"


def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Convert lat/lon to slippy map tile index.

    Raises ValueError if |lat| > 85.0511 (Mercator singularity).
    """
    if abs(lat) > MERCATOR_LAT_LIMIT:
        raise ValueError(
            f"Latitude {lat} exceeds Mercator limit +/-{MERCATOR_LAT_LIMIT}"
        )
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int(
        (1.0 - math.log(
            math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))
        ) / math.pi) / 2.0 * n
    )
    # Clamp to valid range.
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    return x, y


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Convert tile index to (lat_min, lon_min, lat_max, lon_max)."""
    n = 2 ** z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lat_min, lon_min, lat_max, lon_max


def fetch_tile(
    z: int, x: int, y: int, cache_dir: Path
) -> tuple[Path | None, str]:
    """Download a single tile with disk cache.

    Returns (path, status) where status is 'cached', 'fetched', or 'failed: <reason>'.
    """
    tile_path = cache_dir / str(z) / str(y) / f"{x}.jpg"
    if tile_path.exists() and tile_path.stat().st_size > 0:
        return tile_path, "cached"

    tile_path.parent.mkdir(parents=True, exist_ok=True)
    url = ESRI_TILE_URL.format(z=z, y=y, x=x)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            tile_path.write_bytes(data)
            time.sleep(FETCH_DELAY)
            return tile_path, "fetched"
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last_err = str(exc)
            backoff = 2 ** attempt  # 1s, 2s, 4s
            time.sleep(backoff)

    return None, f"failed: {last_err}"


def ping_tile_source() -> bool:
    """Verify tile source responds before bulk fetch."""
    url = ESRI_TILE_URL.format(z=0, y=0, x=0)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def fetch_area(
    lat_min: float,
    lon_min: float,
    lat_max: float,
    lon_max: float,
    zoom: int,
    cache_dir: Path,
    max_tiles: int = 100,
) -> tuple[list[tuple[int, int, int, Path]], list[tuple[int, int, int, str]]]:
    """Fetch all tiles for a bounding box.

    Returns (success_list, fail_list) where:
      success_list = [(z, x, y, path), ...]
      fail_list = [(z, x, y, reason), ...]

    Raises ValueError if tile count exceeds max_tiles.
    """
    x_min, y_min = latlon_to_tile(lat_max, lon_min, zoom)  # NW corner
    x_max, y_max = latlon_to_tile(lat_min, lon_max, zoom)  # SE corner

    tile_count = (x_max - x_min + 1) * (y_max - y_min + 1)
    if tile_count > max_tiles:
        raise ValueError(
            f"Bounding box requires {tile_count} tiles at zoom {zoom}, "
            f"exceeds --max-tiles={max_tiles}. "
            f"Use a smaller area or increase --max-tiles."
        )

    print(f"Tile grid: x=[{x_min}..{x_max}] y=[{y_min}..{y_max}] "
          f"({tile_count} tiles at zoom {zoom})")

    successes: list[tuple[int, int, int, Path]] = []
    failures: list[tuple[int, int, int, str]] = []
    idx = 0

    for ty in range(y_min, y_max + 1):
        for tx in range(x_min, x_max + 1):
            idx += 1
            path, status = fetch_tile(zoom, tx, ty, cache_dir)
            if path is not None:
                print(f"  [{idx}/{tile_count}] z={zoom} x={tx} y={ty}: {status}")
                successes.append((zoom, tx, ty, path))
            else:
                print(f"  [{idx}/{tile_count}] z={zoom} x={tx} y={ty}: {status}",
                      file=sys.stderr)
                failures.append((zoom, tx, ty, status))

    return successes, failures


def _load_bounds_from_metadata(metadata_path: Path) -> tuple[float, float, float, float]:
    """Extract lat/lon bounding box from terrain_metadata.json."""
    meta = json.loads(metadata_path.read_text())

    scene_lat = meta["scene_lat"]
    scene_lon = meta["scene_lon"]
    content_scale = meta["content_scale"]
    shift_x = meta["terrain_shift"]["x"]
    shift_y = meta["terrain_shift"]["y"]
    cb = meta["content_bounds"]

    # Convert world-coordinate content bounds to lat/lon.
    x_m_min = (cb["min_x"] - shift_x) / content_scale
    y_m_min = (cb["min_y"] - shift_y) / content_scale
    x_m_max = (cb["max_x"] - shift_x) / content_scale
    y_m_max = (cb["max_y"] - shift_y) / content_scale

    lat_min, lon_min = osm_project_inverse(x_m_min, y_m_min, scene_lat, scene_lon)
    lat_max, lon_max = osm_project_inverse(x_m_max, y_m_max, scene_lat, scene_lon)

    # Ensure min < max.
    if lat_min > lat_max:
        lat_min, lat_max = lat_max, lat_min
    if lon_min > lon_max:
        lon_min, lon_max = lon_max, lon_min

    return lat_min, lon_min, lat_max, lon_max


def main():
    parser = argparse.ArgumentParser(
        description="Fetch ESRI satellite tiles for a geographic bounding box"
    )
    parser.add_argument(
        "--metadata", type=Path, default=None,
        help="terrain_metadata.json to extract bounds from"
    )
    parser.add_argument(
        "--bbox", nargs=4, type=float,
        metavar=("LAT_MIN", "LON_MIN", "LAT_MAX", "LON_MAX"),
        help="Manual lat/lon bounding box"
    )
    parser.add_argument("--zoom", type=int, default=18, help="Zoom level (default: 18)")
    parser.add_argument(
        "--cache-dir", type=Path, default=None,
        help="Tile cache directory (default: <run_dir>/satellite_cache)"
    )
    parser.add_argument("--max-tiles", type=int, default=100, help="Tile budget (default: 100)")
    args = parser.parse_args()

    if args.bbox:
        lat_min, lon_min, lat_max, lon_max = args.bbox
    elif args.metadata:
        if not args.metadata.exists():
            print(f"ERROR: {args.metadata} not found", file=sys.stderr)
            return 1
        lat_min, lon_min, lat_max, lon_max = _load_bounds_from_metadata(args.metadata)
        print(f"Bounds from metadata: lat=[{lat_min:.6f}, {lat_max:.6f}] "
              f"lon=[{lon_min:.6f}, {lon_max:.6f}]")
    else:
        parser.print_help()
        return 1

    cache_dir = args.cache_dir
    if cache_dir is None:
        if args.metadata:
            cache_dir = args.metadata.parent / "satellite_cache"
        else:
            cache_dir = Path("satellite_cache")

    # Startup ping.
    print("Verifying tile source...")
    if not ping_tile_source():
        print("ERROR: ESRI tile source is not responding", file=sys.stderr)
        return 1
    print("Tile source OK")

    try:
        successes, failures = fetch_area(
            lat_min, lon_min, lat_max, lon_max,
            args.zoom, cache_dir, args.max_tiles
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Statistics.
    cached = sum(1 for _ in successes)  # all successes include cached
    print(f"\nDone: {len(successes)} tiles fetched, {len(failures)} failed")
    if failures:
        for z, x, y, reason in failures:
            print(f"  FAILED: z={z} x={x} y={y}: {reason}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
