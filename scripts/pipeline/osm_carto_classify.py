#!/usr/bin/env python3
"""Classify controlled OSM-Carto RGB pixels into A3D visual values."""

from __future__ import annotations

import math
from dataclasses import dataclass


MAT_WATER = 0
MAT_GRASS = 1
MAT_PAVEMENT = 2
MAT_LAND = 3
MAT_ROAD = 4
MAT_BUILDING = 5
ELEVATION_FLAG = 0x8000


@dataclass(frozen=True)
class CartoMaterial:
    mat_id: int
    shade: int = 0
    elevation: bool = False
    role: str = "unknown"

    @property
    def visual(self) -> int:
        visual = self.mat_id | ((self.shade & 0x7F) << 8)
        if self.elevation:
            visual |= ELEVATION_FLAG
        return visual


CARTO_COLOR_TO_MATERIAL: dict[tuple[int, int, int], CartoMaterial] = {
    (252, 248, 218): CartoMaterial(MAT_LAND, role="land"),
    (170, 211, 223): CartoMaterial(MAT_WATER, role="water"),
    (141, 201, 131): CartoMaterial(MAT_GRASS, shade=10, elevation=True, role="wood"),
    (207, 237, 166): CartoMaterial(MAT_GRASS, shade=6, role="grass"),
    (185, 211, 143): CartoMaterial(MAT_GRASS, shade=8, role="garden"),
    (141, 185, 128): CartoMaterial(MAT_GRASS, shade=9, elevation=True, role="hedge"),
    (238, 233, 221): CartoMaterial(MAT_PAVEMENT, shade=8, role="parking"),
    (200, 195, 185): CartoMaterial(MAT_PAVEMENT, shade=7, role="pavement_outline"),
    (224, 218, 202): CartoMaterial(MAT_PAVEMENT, shade=7, role="sport_court"),
    (229, 231, 238): CartoMaterial(MAT_PAVEMENT, shade=10, role="pedestrian_area"),
    (217, 208, 187): CartoMaterial(MAT_BUILDING, shade=8, role="building"),
    (182, 173, 152): CartoMaterial(MAT_BUILDING, shade=5, role="building_outline"),
    (255, 255, 255): CartoMaterial(MAT_ROAD, shade=12, role="road_primary"),
    (210, 210, 210): CartoMaterial(MAT_ROAD, shade=8, role="road_casing"),
    (215, 215, 215): CartoMaterial(MAT_ROAD, shade=8, role="road_casing"),
    (220, 220, 220): CartoMaterial(MAT_ROAD, shade=8, role="road_casing"),
    (253, 251, 243): CartoMaterial(MAT_ROAD, shade=11, role="road_secondary"),
    (254, 254, 254): CartoMaterial(MAT_ROAD, shade=11, role="road_residential"),
    (232, 216, 205): CartoMaterial(MAT_PAVEMENT, shade=9, role="footway"),
    (221, 180, 167): CartoMaterial(MAT_PAVEMENT, shade=7, role="path"),
    (209, 225, 242): CartoMaterial(MAT_PAVEMENT, shade=9, role="cycleway"),
    (108, 158, 90): CartoMaterial(MAT_GRASS, shade=11, elevation=True, role="tree"),
    (180, 120, 100): CartoMaterial(MAT_BUILDING, shade=6, role="monument_artwork"),
    (160, 110, 95): CartoMaterial(MAT_BUILDING, shade=5, role="memorial"),
    (180, 180, 180): CartoMaterial(MAT_PAVEMENT, shade=6, role="bench"),
    (180, 200, 230): CartoMaterial(MAT_PAVEMENT, shade=7, role="bus_stop"),
    (190, 195, 210): CartoMaterial(MAT_PAVEMENT, shade=7, role="bicycle_parking"),
}


def classify_carto_pixel(rgb: tuple[int, int, int]) -> tuple[CartoMaterial, float]:
    """Return material plus confidence for a controlled Carto RGB."""
    if rgb in CARTO_COLOR_TO_MATERIAL:
        return CARTO_COLOR_TO_MATERIAL[rgb], 1.0
    if rgb == (0, 0, 0):
        return CartoMaterial(MAT_GRASS, role="unclassified"), 0.0
    # The renderer emits a controlled color table. Unknown colors are style
    # drift, not classifier input; do not guess them into the wrong material.
    return CartoMaterial(MAT_GRASS, role="unknown"), 0.0


def material_debug_rgb(material: CartoMaterial) -> tuple[int, int, int]:
    role_rgb = {
        "land": (252, 248, 218),
        "parking": (226, 216, 194),
        "sport_court": (214, 205, 187),
        "pavement_outline": (200, 195, 185),
        "pedestrian_area": (220, 211, 190),
        "footway": (232, 216, 205),
        "steps": (232, 216, 205),
        "path": (221, 180, 167),
        "cycleway": (209, 225, 242),
        "road_casing": (150, 165, 178),
        "road_primary": (188, 198, 208),
        "road_secondary": (176, 188, 199),
        "road_tertiary": (176, 188, 199),
        "road_residential": (176, 188, 199),
        "building": (217, 208, 187),
        "building_outline": (188, 178, 158),
    }
    if material.role in role_rgb:
        return role_rgb[material.role]
    return {
        MAT_WATER: (80, 150, 210),
        MAT_GRASS: (70, 170, 70),
        MAT_LAND: (252, 248, 218),
        MAT_PAVEMENT: (210, 195, 150),
        MAT_ROAD: (230, 230, 230),
        MAT_BUILDING: (200, 185, 160),
    }.get(material.mat_id, (255, 0, 255))
