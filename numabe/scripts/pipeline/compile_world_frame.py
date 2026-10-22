#!/usr/bin/env python3
"""Compile the Numabe AOI coordinate frame without creating an A3D map.

The canonical/reconstruction stages need the metric frame and content scale,
but they do not need a serialized Classic world.  This module owns that small
interface so the direct runner can finish its pre-assembly candidate plan
before ``compile_world_ir_classic_base`` writes any A3D bytes.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

from scripts.pipeline import geo_frames as GF


PATCH_WORLD = 8.0
# Border around the AOI. It holds geometry that sits just outside the AOI ring
# (facade rule placements up to ~7 m west of it in Numabe), so it is a METRE
# quantity: 32 wu at the sprite calibration (9.35 m) scaled by the operator map
# factor (2026-10-13 "map 1.5x bigger"). Left at 32 wu, build_reg_68 placed
# rule-facade-1507398051 at world x = -4.24 and failed "no terrain support".
# 32 * 1.5 = 48 wu stays a whole number of 8-wu patches.
MARGIN_WORLD = 32.0 * GF.OPERATOR_MAP_SCALE
SCHEMA = "y92.classic_base_projection.v1"


@dataclass(frozen=True)
class WorldFrame:
    document: dict
    home: dict
    station: dict
    walking_routes: tuple[dict, ...]
    origin_easting: float
    origin_northing: float
    maximum_easting: float
    maximum_northing: float
    content_scale: float
    offset_x: float
    offset_y: float
    patches_x: int
    patches_y: int

    def world_point(self, feature: dict) -> tuple[float, float]:
        lon, lat = feature["geometry"]["coordinates"]
        easting, northing = GF.utm_forward(float(lon), float(lat))
        return (
            (easting - self.origin_easting) * self.content_scale
            + self.offset_x,
            (northing - self.origin_northing) * self.content_scale
            + self.offset_y,
        )

    @property
    def metadata(self) -> dict:
        return {
            "schema": SCHEMA,
            "projection_kind": "epsg32654_affine",
            "origin_easting": self.origin_easting,
            "origin_northing": self.origin_northing,
            "content_scale": self.content_scale,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "terrain_bounds": {
                "min_x": 0,
                "min_y": 0,
                "max_x": self.patches_x * PATCH_WORLD,
                "max_y": self.patches_y * PATCH_WORLD,
            },
        }


def compile_frame(aoi_path: Path) -> WorldFrame:
    document = json.loads(Path(aoi_path).read_text(encoding="utf-8"))
    by_role: dict[str, list[dict]] = {}
    for feature in document.get("features", []):
        role = (feature.get("properties") or {}).get("role")
        if role:
            by_role.setdefault(str(role), []).append(feature)
    for role in ("home_start", "selected_station_entrance", "aoi"):
        if len(by_role.get(role, [])) != 1:
            raise ValueError(
                f"AOI must contain exactly one {role} feature; got "
                f"{len(by_role.get(role, []))}"
            )
    ring = by_role["aoi"][0]["geometry"]["coordinates"][0]
    projected = [GF.utm_forward(float(lon), float(lat))
                 for lon, lat in ring]
    min_e = min(point[0] for point in projected)
    min_n = min(point[1] for point in projected)
    max_e = max(point[0] for point in projected)
    max_n = max(point[1] for point in projected)
    scale = GF.WORLD_UNITS_PER_METRE
    width_world = (max_e - min_e) * scale + 2.0 * MARGIN_WORLD
    height_world = (max_n - min_n) * scale + 2.0 * MARGIN_WORLD
    return WorldFrame(
        document=document,
        home=by_role["home_start"][0],
        station=by_role["selected_station_entrance"][0],
        walking_routes=tuple(by_role.get("walking_route", [])),
        origin_easting=min_e,
        origin_northing=min_n,
        maximum_easting=max_e,
        maximum_northing=max_n,
        content_scale=scale,
        offset_x=MARGIN_WORLD,
        offset_y=MARGIN_WORLD,
        patches_x=math.ceil(width_world / PATCH_WORLD),
        patches_y=math.ceil(height_world / PATCH_WORLD),
    )


def write_metadata(aoi_path: Path, output_path: Path) -> dict:
    metadata = compile_frame(aoi_path).metadata
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aoi", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    write_metadata(args.aoi, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
