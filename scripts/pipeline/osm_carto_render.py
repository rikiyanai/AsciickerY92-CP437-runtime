#!/usr/bin/env python3
"""Render OSM-derived features into an OSM-Carto-like raster.

This module is intentionally small and deterministic: it draws controlled RGB
colors so osm_carto_classify.py can map pixels back to A3D material roles
without satellite/HSV ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class CartoFeature:
    feature_type: str
    geometry: list[tuple[float, float]]
    is_polygon: bool
    tags: dict[str, str]
    osm_id: str
    osm_type: str
    z_order: int
    width_cells: float = 0.0


CARTO_STYLE: dict[str, dict[str, Any]] = {
    "land": {"fill": (252, 248, 218), "stroke": None, "z": -10},
    "water": {"fill": (170, 211, 223), "stroke": None, "z": 0},
    "wood": {"fill": (141, 201, 131), "stroke": None, "z": 10},
    "grass": {"fill": (207, 237, 166), "stroke": None, "z": 20},
    "garden": {"fill": (185, 211, 143), "stroke": None, "z": 25},
    "hedge": {"fill": (141, 185, 128), "stroke": None, "z": 27, "width": 2},
    "parking": {"fill": (238, 233, 221), "stroke": (200, 195, 185), "z": 30},
    "sport_court": {"fill": (224, 218, 202), "stroke": (200, 195, 185), "z": 32},
    "pedestrian_area": {"fill": (229, 231, 238), "stroke": None, "z": 35},
    "building": {"fill": (217, 208, 187), "stroke": (182, 173, 152), "z": 40},
    "road_primary": {"fill": (255, 255, 255), "stroke": (210, 210, 210), "z": 50, "width": 14},
    "road_secondary": {"fill": (253, 251, 243), "stroke": (215, 215, 215), "z": 48, "width": 10},
    "road_tertiary": {"fill": (254, 254, 254), "stroke": (220, 220, 220), "z": 46, "width": 7},
    "road_residential": {"fill": (254, 254, 254), "stroke": (220, 220, 220), "z": 44, "width": 5},
    "footway": {"fill": (232, 216, 205), "stroke": None, "z": 52, "width": 3},
    "path": {"fill": (221, 180, 167), "stroke": None, "z": 54, "width": 2},
    "cycleway": {"fill": (209, 225, 242), "stroke": None, "z": 55, "width": 3},
    "steps": {"fill": (232, 216, 205), "stroke": None, "z": 56, "width": 2},
    "tree": {"fill": (108, 158, 90), "stroke": None, "z": 100, "width": 3},
    "monument_artwork": {"fill": (180, 120, 100), "stroke": None, "z": 110, "width": 1},
    "bench": {"fill": (180, 180, 180), "stroke": None, "z": 105, "width": 1},
    "bus_stop": {"fill": (180, 200, 230), "stroke": None, "z": 106, "width": 1},
    "bicycle_parking": {"fill": (190, 195, 210), "stroke": None, "z": 107, "width": 1},
    "memorial": {"fill": (160, 110, 95), "stroke": None, "z": 111, "width": 1},
}


def style_for(feature_type: str) -> dict[str, Any]:
    return CARTO_STYLE.get(feature_type, CARTO_STYLE["grass"])


def render_osm_carto(
    features: list[CartoFeature],
    cell_bounds: tuple[int, int, int, int],
    pixels_per_cell: int = 1,
) -> tuple[Image.Image, Image.Image]:
    """Render features into RGB raster and source-id owner raster.

    The owner raster stores a 24-bit feature index + 1 as RGB.  Zero means no
    feature painted that pixel.
    """
    min_x, min_y, max_x, max_y = cell_bounds
    width = max(1, int((max_x - min_x) * pixels_per_cell))
    height = max(1, int((max_y - min_y) * pixels_per_cell))
    image = Image.new("RGB", (width, height), CARTO_STYLE["land"]["fill"])
    owners = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    owner_draw = ImageDraw.Draw(owners)

    def project(pt: tuple[float, float]) -> tuple[int, int]:
        return (
            int(round((pt[0] - min_x) * pixels_per_cell)),
            int(round((pt[1] - min_y) * pixels_per_cell)),
        )

    for owner_index, feature in sorted(enumerate(features), key=lambda item: (item[1].z_order, item[0])):
        if not feature.geometry:
            continue
        style = style_for(feature.feature_type)
        fill = style["fill"]
        stroke = style.get("stroke")
        owner_rgb = (
            ((owner_index + 1) >> 16) & 0xFF,
            ((owner_index + 1) >> 8) & 0xFF,
            (owner_index + 1) & 0xFF,
        )
        points = [project(pt) for pt in feature.geometry]
        if feature.is_polygon and len(points) >= 3:
            if stroke:
                draw.polygon(points, fill=fill, outline=stroke)
            else:
                draw.polygon(points, fill=fill)
            owner_draw.polygon(points, fill=owner_rgb)
        elif len(points) == 1:
            radius = max(1, int(round((feature.width_cells or style.get("width", 1)) * pixels_per_cell * 0.5)))
            x, y = points[0]
            bbox = [x - radius, y - radius, x + radius, y + radius]
            draw.ellipse(bbox, fill=fill)
            owner_draw.ellipse(bbox, fill=owner_rgb)
        elif len(points) >= 2:
            line_width = max(1, int(round((feature.width_cells or style.get("width", 1)) * pixels_per_cell)))
            if stroke and line_width >= 3:
                draw.line(points, fill=stroke, width=line_width + 2, joint="curve")
            draw.line(points, fill=fill, width=line_width, joint="curve")
            owner_draw.line(points, fill=owner_rgb, width=line_width, joint="curve")

    return image, owners
