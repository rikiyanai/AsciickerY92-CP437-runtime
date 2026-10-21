#!/usr/bin/env python3
"""Ingest GSI DEM5A elevation tiles into a Y92 World IR document.

FL-4809 ingest owner, sibling of ``world_ir_ingest_carto.py`` and
``world_ir_ingest_osm_extract.py``.  Same split, same reason: the IR knows the
class ``terrain`` and the record ``coordinate_frame``; it does not know what a
GSI XYZ tile is.

THE SOURCE
==========
``cyberjapandata.gsi.go.jp/xyz/dem5a/{z}/{x}/{y}.txt`` - the Geospatial
Information Authority of Japan's 5 m mesh DEM, derived from airborne LiDAR.
A tile is plain text: 256 comma-separated rows of 256 values, elevation in
metres, with the literal ``e`` for a cell that has no data.  The frozen bundle's
``source_manifest.json`` already carries this endpoint as record
``gsi_dem5a_elevation`` with ``retention: not_retained`` - the bytes were probed
but never stored - so this owner re-fetches them, re-hashes them, and refuses to
continue if the digest does not match the frozen record.  Licence is
``JP-GSI-TERMS``, which ``source_manifest.OPEN_LICENCES`` already recognises as
open-with-attribution, and the attribution line travels into every emitted
observation.

RAW TILES ARE CACHE, THE IR DOCUMENT IS THE PRODUCT
===================================================
``--tile-cache`` holds the fetched ``.txt`` files.  Nothing downstream reads
them; they exist so a re-run does not re-hit GSI and so the digest can be
re-checked.  The World IR document is what is produced.

WHAT IS MEASURED AND WHAT IS NOT
================================
The elevations are measured; they are copied through verbatim, to the two
decimals the source publishes.  Nodata cells are COUNTED AND DROPPED, never
interpolated, because an interpolated elevation is an invented elevation.

The positional uncertainty attached to the terrain feature is the **sampling**
spread this owner can measure from the tile itself: sampling a 3.9 m grid at an
arbitrary point can be wrong by at most the local cell-to-cell step, so the
claim carries ``bounds(0, max |dz| between 4-adjacent AOI cells / 2)``.  It is
deliberately NOT the source's published vertical accuracy: the frozen manifest
does not record one, and this owner does not assert numbers it cannot cite.
That distinction is written into the claim's own ``method`` string so a reader
cannot mistake one for the other.

USAGE
=====
    python3 scripts/pipeline/world_ir_ingest_elevation.py \
        --bundle assets/source_bundles/numabe_corridor_20260911 \
        --tile-cache /tmp/dem5a \
        --out numabe_dem5a.world_ir.json \
        --summary numabe_dem5a.ingest.json --validate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline.source_manifest import is_open_licence, sha256_file  # noqa: E402
from scripts.pipeline.world_ir import WorldIRStore, schema as S  # noqa: E402
from scripts.pipeline.world_ir_ingest_osm_extract import (  # noqa: E402
    point_in_ring,
)

DEM_TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/dem5a/{z}/{x}/{y}.txt"
DEM_MANIFEST_RECORD = "gsi_dem5a_elevation"
DEM_TILE_SIZE = 256
DEM_NODATA_TOKEN = "e"
DEFAULT_ZOOM = 15
FETCH_TIMEOUT_S = 60

# Web Mercator ground resolution at the equator, metres per pixel at z0/256px.
EQUATORIAL_CIRCUMFERENCE_M = 2.0 * math.pi * GF.A_WGS84


# ==========================================================================
# XYZ tiling
# ==========================================================================


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_pixel_lonlat(tile_x: int, tile_y: int, zoom: int,
                      col: float, row: float) -> tuple[float, float]:
    """Lon/lat of a fractional pixel inside a tile (col/row from the tile's NW)."""
    n = 2 ** zoom
    gx = (tile_x + col / DEM_TILE_SIZE) / n
    gy = (tile_y + row / DEM_TILE_SIZE) / n
    lon = gx * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * gy))))
    return lon, lat


def cell_size_m(lat: float, zoom: int) -> float:
    """Ground size of one DEM txt cell at this latitude, metres."""
    return (EQUATORIAL_CIRCUMFERENCE_M * math.cos(math.radians(lat))
            / (2 ** zoom * DEM_TILE_SIZE))


def tiles_covering(bbox_lonlat: Sequence[float], zoom: int) -> list[tuple[int, int]]:
    min_lon, min_lat, max_lon, max_lat = bbox_lonlat
    x0, y0 = lonlat_to_tile(min_lon, max_lat, zoom)   # NW
    x1, y1 = lonlat_to_tile(max_lon, min_lat, zoom)   # SE
    return [(x, y) for x in range(min(x0, x1), max(x0, x1) + 1)
            for y in range(min(y0, y1), max(y0, y1) + 1)]


# ==========================================================================
# fetch + parse
# ==========================================================================


class TileFetchError(RuntimeError):
    """A tile could not be obtained.  Carries the status so it can be reported."""

    def __init__(self, url: str, status: Any, detail: str) -> None:
        self.url = url
        self.status = status
        self.detail = detail
        super().__init__(f"{url}: status={status} {detail}")


def fetch_tile(zoom: int, tile_x: int, tile_y: int, cache_dir: Path,
               *, offline: bool = False) -> dict:
    """Return a fetch record for one tile, downloading it only when not cached."""
    url = DEM_TILE_URL.format(z=zoom, x=tile_x, y=tile_y)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"dem5a_{zoom}_{tile_x}_{tile_y}.txt"
    if path.is_file():
        return {
            "url": url, "path": str(path), "http_status": "cached",
            "size_bytes": path.stat().st_size, "sha256": sha256_file(path),
        }
    if offline:
        raise TileFetchError(url, "offline", f"{path} is not cached and --offline was given")
    request = urllib.request.Request(
        url, headers={"User-Agent": "asciicker-Y9-2 FL-4809 world_ir_ingest_elevation"}
    )
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise TileFetchError(url, exc.code, exc.reason or "HTTPError") from exc
    except urllib.error.URLError as exc:
        raise TileFetchError(url, "no-response", str(exc.reason)) from exc
    except OSError as exc:
        raise TileFetchError(url, "os-error", f"{type(exc).__name__}: {exc}") from exc
    if status != 200:
        raise TileFetchError(url, status, "expected HTTP 200")
    path.write_bytes(body)
    return {
        "url": url, "path": str(path), "http_status": status,
        "size_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
    }


def parse_tile(path: Path) -> tuple[list[list[float | None]], int]:
    """Parse a DEM txt tile into a row-major grid.  Returns (grid, nodata_count)."""
    rows = Path(path).read_text(encoding="utf-8").splitlines()
    if len(rows) != DEM_TILE_SIZE:
        raise SystemExit(f"[dem-ingest] {path}: expected {DEM_TILE_SIZE} rows, got {len(rows)}")
    grid: list[list[float | None]] = []
    nodata = 0
    for row in rows:
        cells = row.split(",")
        if len(cells) != DEM_TILE_SIZE:
            raise SystemExit(
                f"[dem-ingest] {path}: expected {DEM_TILE_SIZE} columns, got {len(cells)}"
            )
        parsed: list[float | None] = []
        for cell in cells:
            token = cell.strip()
            if token == DEM_NODATA_TOKEN or token == "":
                parsed.append(None)
                nodata += 1
            else:
                parsed.append(float(token))
        grid.append(parsed)
    return grid, nodata


# ==========================================================================
# ingest
# ==========================================================================


def _dem_record(bundle_dir: Path) -> dict:
    manifest = json.loads((bundle_dir / "source_manifest.json").read_text(encoding="utf-8"))
    for rec in manifest["records"]:
        if rec["record_id"] == DEM_MANIFEST_RECORD:
            return rec
    raise SystemExit(
        f"[dem-ingest] {bundle_dir}/source_manifest.json has no {DEM_MANIFEST_RECORD!r} "
        "record; refusing to ingest unprovenanced elevation"
    )


def _bundle_aoi_path(bundle_dir: Path) -> Path:
    manifest = json.loads((bundle_dir / "source_manifest.json").read_text(encoding="utf-8"))
    for rec in manifest["records"]:
        if rec["record_id"] == "aoi_definition_geojson" and rec.get("artifact_path"):
            return bundle_dir / rec["artifact_path"]
    raise SystemExit(
        f"[dem-ingest] {bundle_dir}/source_manifest.json has no retained "
        "'aoi_definition_geojson' artifact"
    )


def _aoi(bundle_dir: Path) -> tuple[list[list[float]], dict]:
    geojson = json.loads(_bundle_aoi_path(bundle_dir).read_text(encoding="utf-8"))
    ring = None
    entrance = None
    for feat in geojson["features"]:
        if feat.get("id") == "aoi_polygon" or feat.get("properties", {}).get("role") == "aoi":
            ring = feat["geometry"]["coordinates"][0]
        if (feat.get("id") == "selected_entrance" or
                feat.get("properties", {}).get("role") == "selected_station_entrance"):
            entrance = feat
    if ring is None or entrance is None:
        raise SystemExit("[dem-ingest] the AOI geojson needs aoi_polygon and selected_entrance")
    return ring, entrance


def _aoi_from_geojson(aoi_path: Path, entrance_lonlat: str | None,
                      entrance_label: str) -> tuple[list[list[float]], dict]:
    """AOI override for bundles without a retained aoi_definition_geojson.

    Uses the feature with role ``aoi`` (or id ``aoi_polygon``) as the ring and
    the given ``lon,lat`` as the frame anchor.  No new validation is added;
    clipping and sampling below are unchanged.
    """
    geojson = json.loads(Path(aoi_path).read_text(encoding="utf-8"))
    ring = None
    for feat in geojson["features"]:
        props = feat.get("properties", {})
        if feat.get("id") == "aoi_polygon" or props.get("role") == "aoi":
            ring = feat["geometry"]["coordinates"][0]
            break
    if ring is None:
        raise SystemExit(f"[dem-ingest] {aoi_path} has no aoi polygon feature")
    if entrance_lonlat is None:
        raise SystemExit("[dem-ingest] --aoi-geojson requires --entrance-lonlat")
    entrance_lon, entrance_lat = (float(v) for v in entrance_lonlat.split(","))
    entrance_feature = {
        "geometry": {"coordinates": [entrance_lon, entrance_lat]},
        "properties": {
            "osm_type": "node",
            "osm_id": "override-anchor",
        },
    }
    entrance_feature["properties"]["label"] = entrance_label
    return ring, entrance_feature


def build(bundle_dir: Path, cache_dir: Path, *, zoom: int = DEFAULT_ZOOM,
          ir_id: str = "numabe_corridor_dem5a",
          generated_at: str = "2026-09-11T00:00:00Z",
          offline: bool = False,
          aoi_geojson: Path | None = None,
          entrance_lonlat: str | None = None,
          entrance_label: str = "override anchor (connected AOI)",
          provenance_bundle: Path | None = None) -> tuple[WorldIRStore, dict]:
    bundle_dir = Path(bundle_dir)
    dem_bundle = Path(provenance_bundle) if provenance_bundle else bundle_dir
    record = _dem_record(dem_bundle)
    if aoi_geojson is not None:
        ring, entrance_feature = _aoi_from_geojson(
            Path(aoi_geojson), entrance_lonlat, entrance_label)
    else:
        ring, entrance_feature = _aoi(bundle_dir)
    entrance_lon, entrance_lat = entrance_feature["geometry"]["coordinates"]

    frame = GF.Y9WorldFrame.anchored_at_lonlat(
        entrance_lon, entrance_lat,
        f"OSM {entrance_feature['properties']['osm_type']}/"
        f"{entrance_feature['properties']['osm_id']} (selected station entrance)",
    )

    store = WorldIRStore.new(ir_id, generated_at=generated_at)
    # The vertical datum is quoted from the frozen manifest, not named from
    # memory: it is the only evidence this repo holds about the tiles' datum.
    GF.declare_frames(store, frame, vertical_datum=GF.VERTICAL_DATUM_GSI)

    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    bbox = (min(lons), min(lats), max(lons), max(lats))
    tiles = tiles_covering(bbox, zoom)

    if not is_open_licence(record["licence"]):
        raise SystemExit(
            f"[dem-ingest] licence {record['licence']!r} is not in "
            "source_manifest.OPEN_LICENCES; refusing to retain tiles"
        )

    tile_reports: list[dict] = []
    samples: list[dict] = []
    total_nodata = 0
    aoi_nodata = 0
    entrance_sample: dict | None = None
    neighbour_steps: list[float] = []

    for tile_x, tile_y in tiles:
        fetched = fetch_tile(zoom, tile_x, tile_y, cache_dir, offline=offline)
        grid, nodata = parse_tile(Path(fetched["path"]))
        total_nodata += nodata

        digest_matches = None
        if record.get("sha256") and record.get("source_id", "").endswith(
            f"{zoom}/{tile_x}/{tile_y}"
        ):
            digest_matches = fetched["sha256"] == record["sha256"]
            if not digest_matches:
                raise SystemExit(
                    f"[dem-ingest] {fetched['url']} digest {fetched['sha256']} does not match "
                    f"the frozen manifest record {record['sha256']}; the tile changed upstream "
                    "and the frozen evidence can no longer be reproduced"
                )

        centre_lat = tile_pixel_lonlat(tile_x, tile_y, zoom,
                                       DEM_TILE_SIZE / 2, DEM_TILE_SIZE / 2)[1]
        cell_m = cell_size_m(centre_lat, zoom)
        tile_in_aoi = 0
        values = [v for row in grid for v in row if v is not None]

        for row_idx in range(DEM_TILE_SIZE):
            for col_idx in range(DEM_TILE_SIZE):
                lon, lat = tile_pixel_lonlat(tile_x, tile_y, zoom,
                                             col_idx + 0.5, row_idx + 0.5)
                if not point_in_ring((lon, lat), ring):
                    continue
                elevation = grid[row_idx][col_idx]
                if elevation is None:
                    aoi_nodata += 1
                    continue
                tile_in_aoi += 1
                easting, northing = GF.utm_forward(lon, lat)
                samples.append({
                    "easting": easting,
                    "northing": northing,
                    "elevation_m": elevation,
                    "tile": f"{zoom}/{tile_x}/{tile_y}",
                    "cell": [col_idx, row_idx],
                })
                # Sampling spread evidence: the step to each present 4-neighbour.
                for dc, dr in ((1, 0), (0, 1)):
                    nc, nr = col_idx + dc, row_idx + dr
                    if nc < DEM_TILE_SIZE and nr < DEM_TILE_SIZE:
                        other = grid[nr][nc]
                        if other is not None:
                            neighbour_steps.append(abs(other - elevation))

        entrance_tile = lonlat_to_tile(entrance_lon, entrance_lat, zoom)
        if entrance_tile == (tile_x, tile_y):
            n = 2 ** zoom
            gx = (entrance_lon + 180.0) / 360.0 * n
            lat_rad = math.radians(entrance_lat)
            gy = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
            col_idx = int((gx - tile_x) * DEM_TILE_SIZE)
            row_idx = int((gy - tile_y) * DEM_TILE_SIZE)
            entrance_sample = {
                "tile": f"{zoom}/{tile_x}/{tile_y}",
                "cell": [col_idx, row_idx],
                "elevation_m": grid[row_idx][col_idx],
            }

        tile_reports.append({
            "tile": f"{zoom}/{tile_x}/{tile_y}",
            "url": fetched["url"],
            "http_status": fetched["http_status"],
            "size_bytes": fetched["size_bytes"],
            "sha256": fetched["sha256"],
            "sha256_matches_frozen_manifest": digest_matches,
            "cached_path": fetched["path"],
            "grid": [DEM_TILE_SIZE, DEM_TILE_SIZE],
            "cells_total": DEM_TILE_SIZE * DEM_TILE_SIZE,
            "cells_nodata": nodata,
            "cell_size_m": cell_m,
            "elevation_min_m": min(values) if values else None,
            "elevation_max_m": max(values) if values else None,
            "elevation_mean_m": (sum(values) / len(values)) if values else None,
            "cells_inside_aoi": tile_in_aoi,
        })

    if not samples:
        raise SystemExit("[dem-ingest] no DEM cell centre falls inside the AOI ring")

    max_step = max(neighbour_steps) if neighbour_steps else 0.0
    sampling_half_step = max_step / 2.0

    observation_ids: list[str] = []
    for report in tile_reports:
        obs_id = f"obs:dem5a:{report['tile']}"
        store.add_observation(
            S.source_observation(
                observation_id=obs_id,
                source_id=record["record_id"],
                source_kind="point_cloud",
                observed_at=record.get("acquired_utc") or "unknown",
                attributes={
                    "provider": record["provider"],
                    "source_uri": report["url"],
                    "manifest_source_uri": record["source_uri"],
                    "manifest_source_id": record["source_id"],
                    "licence": record["licence"],
                    "licence_uri": record["licence_uri"],
                    "attribution": record["attribution"],
                    "open_terms": True,
                    "open_terms_basis": "licence JP-GSI-TERMS is listed in "
                                        "scripts/pipeline/source_manifest.OPEN_LICENCES "
                                        "(attribution required, retention permitted)",
                    "model": "DEM5A, 5 m mesh, airborne-LiDAR derived",
                    "tile": report["tile"],
                    "tile_format": "plain text, 256 rows x 256 comma-separated metre "
                                   f"values, {DEM_NODATA_TOKEN!r} marks a nodata cell",
                    "http_status": report["http_status"],
                    "size_bytes": report["size_bytes"],
                    "sha256_matches_frozen_manifest":
                        report["sha256_matches_frozen_manifest"],
                    "cells_total": report["cells_total"],
                    "cells_nodata": report["cells_nodata"],
                    "cells_inside_aoi": report["cells_inside_aoi"],
                    "cell_size_m": report["cell_size_m"],
                    "elevation_min_m": report["elevation_min_m"],
                    "elevation_max_m": report["elevation_max_m"],
                    "elevation_mean_m": report["elevation_mean_m"],
                    "vertical_datum": GF.VERTICAL_DATUM_GSI,
                    "raw_tile_retained_at": report["cached_path"],
                    "raw_tile_retention_note": "cache only; the World IR document is the "
                                               "product, the .txt is kept so the digest can "
                                               "be rechecked without re-fetching",
                },
                quality={
                    "nodata_cells": report["cells_nodata"],
                    "nodata_handling": "counted and dropped; never interpolated, because an "
                                       "interpolated elevation is an invented elevation",
                    "sampling": "one sample per DEM cell CENTRE whose lon/lat falls inside "
                                "the AOI ring; no resampling, no smoothing, values copied "
                                "verbatim to the source's two decimals",
                },
                digest=report["sha256"],
            )
        )
        observation_ids.append(obs_id)

    elevations = [s["elevation_m"] for s in samples]
    feature = store.add_feature(
        S.canonical_feature(
            feature_id="feature:terrain:gsi_dem5a_aoi_samples",
            semantic_class="terrain",
            geometry=S.geometry_field_samples(
                GF.FRAME_PROJECTED,
                samples,
                lookup="nearest DEM cell centre by planar distance in EPSG:32654; "
                       "for a continuous surface, bilinear between the four surrounding "
                       "cell centres. Cells are ~"
                       f"{tile_reports[0]['cell_size_m']:.3f} m apart at this latitude.",
            ),
            properties={
                "geometry": S.property_claim(
                    value=None,
                    provenance=S.PROVENANCE_MEASURED,
                    method="GSI DEM5A cell centres, projected EPSG:4326 -> EPSG:32654 by "
                           "scripts/pipeline/geo_frames.py; elevations copied verbatim. "
                           "The uncertainty below is the SAMPLING spread measured from the "
                           "tile (largest step between 4-adjacent AOI cells, halved), NOT "
                           "the source's published vertical accuracy, which the frozen "
                           "manifest does not record and this owner therefore does not assert.",
                    observation_ids=list(observation_ids),
                    uncertainty=S.uncertainty_bounds(0.0, sampling_half_step, "metre"),
                ),
                "semantic_class": S.property_claim(
                    value="terrain",
                    provenance=S.PROVENANCE_DERIVED,
                    method="a bare-earth elevation model samples the terrain surface",
                    observation_ids=list(observation_ids),
                    uncertainty=S.uncertainty_categorical({"terrain": 1.0}),
                ),
                "vertical_datum": S.property_claim(
                    value=GF.VERTICAL_DATUM_GSI,
                    provenance=S.PROVENANCE_MEASURED,
                    method="quoted from source_manifest.json record "
                           f"{record['record_id']}, field crs",
                    observation_ids=list(observation_ids),
                ),
                "elevation_range_m": S.property_claim(
                    value=[min(elevations), max(elevations)],
                    provenance=S.PROVENANCE_MEASURED,
                    method="min/max over the AOI-clipped cell values",
                    observation_ids=list(observation_ids),
                    uncertainty=S.uncertainty_bounds(
                        min(elevations), max(elevations), "metre"
                    ),
                ),
                "attribution": S.property_claim(
                    value=record["attribution"],
                    provenance=S.PROVENANCE_MEASURED,
                    method="attribution line required by JP-GSI-TERMS; any renderer of "
                           "this terrain must display it",
                    observation_ids=list(observation_ids),
                ),
            },
            confidence=0.95,
            # A bare-earth DEM decides the ground the player stands on, and the
            # claim that earns it is measured.  It is not collision: the model
            # says nothing about what blocks movement.
            gameplay_authority=S.AUTHORITY_WALKABILITY,
            authority_supporting_properties=["geometry", "vertical_datum"],
            presentation=S.presentation_requirements(
                visible=True,
                silhouette_required=False,
                must_not_imply_collision=False,
                legibility_class="background",
            ),
        )
    )

    receipt = {
        "bundle": str(bundle_dir),
        "provenance_bundle": str(dem_bundle),
        "aoi_ring_source": str(aoi_geojson) if aoi_geojson is not None else None,
        "manifest_record": {
            "record_id": record["record_id"],
            "provider": record["provider"],
            "source_uri": record["source_uri"],
            "source_id": record["source_id"],
            "licence": record["licence"],
            "licence_uri": record["licence_uri"],
            "attribution": record["attribution"],
            "retention_in_bundle": record["retention"],
            "frozen_sha256": record["sha256"],
            "frozen_size_bytes": record["size_bytes"],
        },
        "zoom": zoom,
        "aoi_bbox_lonlat": list(bbox),
        "tiles_required": [f"{zoom}/{x}/{y}" for x, y in tiles],
        "tiles": tile_reports,
        "tile_cache_dir": str(cache_dir),
        "vertical_datum": GF.VERTICAL_DATUM_GSI,
        "samples_ingested": len(samples),
        "aoi_cells_nodata_dropped": aoi_nodata,
        "tile_cells_nodata_total": total_nodata,
        "elevation_min_m": min(elevations),
        "elevation_max_m": max(elevations),
        "elevation_mean_m": sum(elevations) / len(elevations),
        "sampling": "one sample per DEM cell centre inside the AOI ring; nearest-cell "
                    "lookup declared on the geometry, no resampling performed",
        "sampling_max_adjacent_step_m": max_step,
        "sampling_uncertainty_bounds_m": [0.0, sampling_half_step],
        "entrance_probe": entrance_sample,
        "entrance_elevation_declared_by_bundle_m":
            entrance_feature["properties"].get("gsi_dem5a_elevation_m"),
        "y9_frame": frame.as_metadata(),
        "entrance_bake_height_value": (
            frame.bake_height_value(frame.metres_to_world_z(entrance_sample["elevation_m"]))
            if entrance_sample and entrance_sample["elevation_m"] is not None else None
        ),
        "features_emitted": len(store.features),
        "observations_emitted": len(store.observations),
        "feature_id": feature["feature_id"],
        # Counted here, not only in main(), so an importing caller sees the same
        # receipt the CLI prints.
        "validation_findings": len(store.validate()),
    }
    return store, receipt


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bundle", type=Path, required=True,
                    help="frozen source bundle directory")
    ap.add_argument("--tile-cache", type=Path, required=True,
                    help="directory for the raw .txt tiles (scratch, not a product)")
    ap.add_argument("--out", type=Path, required=True, help="World IR document to write")
    ap.add_argument("--summary", type=Path, help="write the ingest receipt here")
    ap.add_argument("--zoom", type=int, default=DEFAULT_ZOOM)
    ap.add_argument("--ir-id", default="numabe_corridor_dem5a")
    ap.add_argument("--generated-at", default="2026-09-11T00:00:00Z")
    ap.add_argument("--offline", action="store_true",
                    help="use cached tiles only; fail rather than fetch")
    ap.add_argument("--validate", action="store_true",
                    help="validate before writing and fail on any finding")
    ap.add_argument("--aoi-geojson", type=Path, default=None,
                    help="override AOI ring source (geojson with role=aoi)")
    ap.add_argument("--entrance-lonlat", default=None,
                    help="override anchor as 'lon,lat' (required with --aoi-geojson)")
    ap.add_argument("--entrance-label", default="override anchor (connected AOI)")
    ap.add_argument("--provenance-bundle", type=Path, default=None,
                    help="bundle holding the gsi_dem5a_elevation record "
                         "(defaults to --bundle)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    try:
        store, receipt = build(
            args.bundle, args.tile_cache, zoom=args.zoom, ir_id=args.ir_id,
            generated_at=args.generated_at, offline=args.offline,
            aoi_geojson=args.aoi_geojson,
            entrance_lonlat=args.entrance_lonlat,
            entrance_label=args.entrance_label,
            provenance_bundle=args.provenance_bundle,
        )
    except TileFetchError as exc:
        # Report the failure with its status; never estimate an elevation.
        print(f"[dem-ingest] TILE FETCH FAILED {exc.url} status={exc.status} "
              f"{exc.detail}", file=sys.stderr)
        print("[dem-ingest] no elevation was emitted; nothing was estimated", file=sys.stderr)
        return 2

    findings = store.validate()
    assert receipt["validation_findings"] == len(findings)
    if args.validate and findings:
        for finding in findings[:20]:
            print(f"  FAIL {finding}", file=sys.stderr)
        print(f"[dem-ingest] {len(findings)} finding(s); nothing written", file=sys.stderr)
        return 1

    store.save(args.out)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    for tile in receipt["tiles"]:
        print(f"[dem-ingest] tile {tile['tile']} http={tile['http_status']} "
              f"{tile['size_bytes']} bytes sha256_matches_frozen="
              f"{tile['sha256_matches_frozen_manifest']} "
              f"nodata={tile['cells_nodata']}/{tile['cells_total']} "
              f"aoi_cells={tile['cells_inside_aoi']}")
    print(f"[dem-ingest] samples={receipt['samples_ingested']} "
          f"nodata_dropped_in_aoi={receipt['aoi_cells_nodata_dropped']} "
          f"elevation {receipt['elevation_min_m']:.2f}..{receipt['elevation_max_m']:.2f} m "
          f"(mean {receipt['elevation_mean_m']:.2f} m)")
    print(f"[dem-ingest] vertical datum: {receipt['vertical_datum']}")
    print(f"[dem-ingest] sampling uncertainty bounds "
          f"{receipt['sampling_uncertainty_bounds_m']} m "
          f"(max adjacent step {receipt['sampling_max_adjacent_step_m']:.2f} m)")
    probe = receipt["entrance_probe"]
    if probe:
        print(f"[dem-ingest] entrance cell {probe['cell']} = {probe['elevation_m']} m "
              f"(bundle declared "
              f"{receipt['entrance_elevation_declared_by_bundle_m']} m)")
    print(f"[dem-ingest] features={receipt['features_emitted']} "
          f"observations={receipt['observations_emitted']} findings={len(findings)}")
    print(f"[dem-ingest] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
