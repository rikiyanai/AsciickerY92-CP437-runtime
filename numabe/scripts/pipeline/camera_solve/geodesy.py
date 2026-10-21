"""WGS84 <-> local ENU conversion for the camera solve (FL-4812).

The solve needs ONE metric frame that every source can be expressed in:
capture GPS, OSM node coordinates and reconstructed structure. Lat/lon is not
metric, so every residual comparing them would be in mixed units and the
weighting the operator asked to be "explicit and inspectable" would be a lie.

So the solve declares a local ENU (east/north/up) frame anchored at a stated
origin and converts everything into it exactly once, here. The origin is part
of the frame identity and is recorded in the World IR coordinate frame note:
a document whose frame origin is unknown cannot be reprojected later.

Stdlib + numpy only. No pyproj: an authority-grade projection is not needed
for a corridor under a kilometre, and adding a dependency to prove a method
is a cost the repo should not pay silently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# WGS84
_A = 6378137.0
_F = 1.0 / 298.257223563
_E2 = _F * (2.0 - _F)


def _geodetic_to_ecef(lon_deg: float, lat_deg: float, alt_m: float) -> tuple[float, float, float]:
    lon = math.radians(lon_deg)
    lat = math.radians(lat_deg)
    s = math.sin(lat)
    n = _A / math.sqrt(1.0 - _E2 * s * s)
    x = (n + alt_m) * math.cos(lat) * math.cos(lon)
    y = (n + alt_m) * math.cos(lat) * math.sin(lon)
    z = (n * (1.0 - _E2) + alt_m) * s
    return x, y, z


@dataclass(frozen=True)
class EnuFrame:
    """A local east/north/up frame anchored at one geodetic origin."""

    origin_lon: float
    origin_lat: float
    origin_alt: float = 0.0

    @property
    def frame_id(self) -> str:
        return "control_enu"

    @property
    def crs(self) -> str:
        # A project-local frame, deliberately not an authority code: the IR
        # does not privilege either, and claiming EPSG here would assert a
        # datum realization this solve never performed.
        return "local:enu"

    @property
    def origin_note(self) -> str:
        return (
            f"east/north/up metres about WGS84 origin "
            f"lon={self.origin_lon:.9f} lat={self.origin_lat:.9f} "
            f"alt={self.origin_alt:.3f}m (ellipsoidal)"
        )

    def to_enu(self, lon: float, lat: float, alt: float = 0.0) -> tuple[float, float, float]:
        x, y, z = _geodetic_to_ecef(lon, lat, alt)
        x0, y0, z0 = _geodetic_to_ecef(self.origin_lon, self.origin_lat, self.origin_alt)
        dx, dy, dz = x - x0, y - y0, z - z0
        lon0 = math.radians(self.origin_lon)
        lat0 = math.radians(self.origin_lat)
        sl, cl = math.sin(lon0), math.cos(lon0)
        sp, cp = math.sin(lat0), math.cos(lat0)
        east = -sl * dx + cl * dy
        north = -sp * cl * dx - sp * sl * dy + cp * dz
        up = cp * cl * dx + cp * sl * dy + sp * dz
        return east, north, up

    def to_geodetic(self, east: float, north: float, up: float) -> tuple[float, float, float]:
        """Inverse of :meth:`to_enu`, so a solved point can be reported in lon/lat."""
        lon0 = math.radians(self.origin_lon)
        lat0 = math.radians(self.origin_lat)
        sl, cl = math.sin(lon0), math.cos(lon0)
        sp, cp = math.sin(lat0), math.cos(lat0)
        dx = -sl * east - sp * cl * north + cp * cl * up
        dy = cl * east - sp * sl * north + cp * sl * up
        dz = cp * north + sp * up
        x0, y0, z0 = _geodetic_to_ecef(self.origin_lon, self.origin_lat, self.origin_alt)
        x, y, z = x0 + dx, y0 + dy, z0 + dz
        lon = math.degrees(math.atan2(y, x))
        p = math.hypot(x, y)
        lat = math.atan2(z, p * (1.0 - _E2))
        for _ in range(8):  # Bowring iteration; converges well inside 1 mm here
            s = math.sin(lat)
            n = _A / math.sqrt(1.0 - _E2 * s * s)
            alt = p / math.cos(lat) - n
            lat = math.atan2(z, p * (1.0 - _E2 * n / (n + alt)))
        s = math.sin(lat)
        n = _A / math.sqrt(1.0 - _E2 * s * s)
        alt = p / math.cos(lat) - n
        return lon, math.degrees(lat), alt


def exif_rational(text: str) -> float:
    """Parse one EXIF rational such as ``6345/1000``."""
    num, _, den = text.partition("/")
    return float(num) / float(den) if den else float(num)


def exif_dms(text: str) -> float:
    """Parse an EXIF ``d/1 m/1 s/n`` triple into signed decimal degrees."""
    parts = [exif_rational(p) for p in text.split()]
    while len(parts) < 3:
        parts.append(0.0)
    return parts[0] + parts[1] / 60.0 + parts[2] / 3600.0


def bearing_deg(east: float, north: float) -> float:
    """Compass bearing of a horizontal vector: 0 = north, 90 = east."""
    return math.degrees(math.atan2(east, north)) % 360.0


def angle_delta_deg(a: float, b: float) -> float:
    """Signed smallest difference a - b, wrapped into (-180, 180]."""
    return (a - b + 180.0) % 360.0 - 180.0
