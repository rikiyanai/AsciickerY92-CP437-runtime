#!/usr/bin/env python3
"""Canonical Transverse Mercator projection for OSM ↔ A3D coordinate conversion.

All OSM pipeline tools should import from here instead of re-implementing.
Projection matches blosm's internal math (WGS84 equatorial radius).
"""
from __future__ import annotations

import math

R = 6378137.0  # WGS84 equatorial radius (meters)


def osm_project(lat: float, lon: float, scene_lat: float, scene_lon: float) -> tuple[float, float]:
    """Transverse Mercator: lat/lon → local meters relative to scene center.

    Returns (x, y) in meters.  Multiply by content_scale and add terrain_shift
    to get A3D world coordinates.
    """
    lat_rad = math.radians(lat)
    delta_lon_rad = math.radians(lon - scene_lon)
    scene_lat_rad = math.radians(scene_lat)
    b = math.sin(delta_lon_rad) * math.cos(lat_rad)
    # Guard against singularity at antipodal points.
    if abs(1.0 - abs(b)) < 1e-12:
        return 0.0, 0.0
    x = 0.5 * R * math.log((1.0 + b) / (1.0 - b))
    y = R * (math.atan(math.tan(lat_rad) / math.cos(delta_lon_rad)) - scene_lat_rad)
    return x, y


def osm_project_inverse(x_m: float, y_m: float, scene_lat: float, scene_lon: float) -> tuple[float, float]:
    """Inverse Transverse Mercator: local meters → lat/lon.

    Input (x_m, y_m) is in meters relative to scene center (i.e. after
    subtracting terrain_shift and dividing by content_scale).
    """
    x_norm = x_m / R
    y_norm = y_m / R
    D = y_norm + math.radians(scene_lat)
    lon = math.atan(math.sinh(x_norm) / math.cos(D))
    lat = math.asin(math.sin(D) / math.cosh(x_norm))
    return math.degrees(lat), scene_lon + math.degrees(lon)


def latlon_to_world(lat: float, lon: float, scene_lat: float, scene_lon: float,
                    content_scale: float, shift_x: float, shift_y: float) -> tuple[float, float]:
    """Lat/lon → A3D world coordinates (convenience wrapper)."""
    x, y = osm_project(lat, lon, scene_lat, scene_lon)
    return x * content_scale + shift_x, y * content_scale + shift_y


def world_to_latlon(wx: float, wy: float, scene_lat: float, scene_lon: float,
                    content_scale: float, shift_x: float, shift_y: float) -> tuple[float, float]:
    """A3D world coordinates → lat/lon (convenience wrapper)."""
    x_m = (wx - shift_x) / content_scale
    y_m = (wy - shift_y) / content_scale
    return osm_project_inverse(x_m, y_m, scene_lat, scene_lon)
