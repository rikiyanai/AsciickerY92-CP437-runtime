#!/usr/bin/env python3
"""Coordinate frames for the Y9 world pipeline: EPSG:4326 <-> EPSG:32654 <-> Y9.

FL-4809 frame owner.  Sits OUTSIDE ``scripts/pipeline/world_ir/`` for the same
reason the ingest owners do: the IR declares frames, it does not know how to
compute one from another.  This module is the single place that knows

  * the geodetic maths that turns WGS84 lon/lat into UTM metres,
  * the Y9 bake constants that turn metres into world and bake units,
  * and the ``coordinate_frame`` records that declare all three into an IR
    document so that every geometry in that document names the frame it is in.

WHY THE PROJECTION IS IMPLEMENTED HERE AND NOT DELEGATED
=======================================================
``pyproj`` (3.7.2 under ``python3``, 3.8.0 under ``.venv``) and ``osgeo.osr``
both import in this repo today, so delegation would work *today*.  The World IR
package already refuses to let an optional package decide whether world truth is
valid (``world_ir/validate.py`` docstring), and a frame transform is the same
kind of load-bearing fact: if the only way to read the corridor's coordinates is
to have PROJ installed, then an evidence document stops being readable when a
wheel stops building.  So the transform is the Kruger n-series in stdlib
``math``, and PROJ/GDAL are used as *independent referees* in ``self_test()``
rather than as the implementation.

VERIFICATION (run ``python3 scripts/pipeline/geo_frames.py --selftest``)
=======================================================================
``self_test()`` reports, in metres, the residual of this implementation against
every referee it can import, the forward/inverse round trip, and the frozen
Numabe bundle's own EPSG:32654 figures.  Numbers are not asserted here; they are
printed and returned, and ``scripts/tests/test_fl4809_numabe_world_ir_ingest.py``
asserts the bounds.

WHAT IS **NOT** DECIDED HERE
============================
Where the Y9 frame's origin sits.  ``Y9WorldFrame`` takes an explicit anchor,
because an anchor is a per-corridor decision backed by a named source element
(for Numabe: OSM node 9865199050, the selected station entrance).  Hard-coding
a Tokyo anchor into a frame module would make the module locale-dependent, which
is exactly the defect the IR vocabulary was built to avoid.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.world_ir import schema as S  # noqa: E402

# --------------------------------------------------------------------------
# WGS84 / UTM constants
# --------------------------------------------------------------------------

A_WGS84 = 6378137.0                 # semi-major axis, metres
F_WGS84 = 1.0 / 298.257223563       # flattening
K0_UTM = 0.9996                     # UTM scale factor on the central meridian
FALSE_EASTING = 500000.0
FALSE_NORTHING_SOUTH = 10000000.0

# The AOI's projected working frame, stated by the frozen bundle itself
# (numabe_corridor_aoi.geojson -> crs.metric_working = "EPSG:32654").
UTM_ZONE_AOI = 54
UTM_EPSG_NORTH = {54: "EPSG:32654"}

# --------------------------------------------------------------------------
# Y9 metric calibration and existing-format bake constants
# --------------------------------------------------------------------------
#
#   scripts/pipeline/osm_projection.py:50   world = metres * content_scale + shift
#   compile_facade_shell consumes WORLD_UNITS_PER_METRE for canonical defaults
#   scripts/pipeline/compile_facade_shell.py:161   z_world = z_metres * content_scale
#   scripts/pipeline/compile_facade_shell.py:61    HEIGHT_SCALE = 16.0
#   scripts/pipeline/compile_facade_shell.py:256   transform row2 z = HEIGHT_SCALE
#   scripts/pipeline/probe_instance_terrain_z.py:15  raw patch height = 128 + z_world*16
#
# FL-4814 native Classic calibration candidate. The standing player-body frame
# occupies eight CP437 rows above its foot reference. Native sprite.cpp:1632
# and physics.cpp:1057 convert a row to (2/3)/cos(30deg) world units vertically.
# A declared 1.8m standing-height target gives 3.42133 world units/metre.
# This replaces the inherited 2.25 assumption globally, not per-building scale.
# Headed human-scale acceptance remains separate from this calibration model.
CLASSIC_STANDING_ROWS = 8
CLASSIC_TARGET_STANDING_HEIGHT_M = 1.8
# Sprite-relative calibration: world units that make the 8-row player read as
# 1.8 m (3.42133 wu/m).  Player/physics owners (collision radius, walk speed)
# live in engine units and are sized against THIS number, never the map number.
SPRITE_WORLD_UNITS_PER_METRE = (CLASSIC_STANDING_ROWS * (2.0 / 3.0)
                                / math.cos(math.radians(30.0)) / CLASSIC_TARGET_STANDING_HEIGHT_M)
# Operator request 2026-10-13: "map needs to be 1.5x bigger relative to sprite
# (everything: fixture road width etc.)".  The sprite does not change, so every
# map quantity measured in metres (terrain, roads, buildings, heights, fixtures,
# signs, rails, trains, trees, meshes) is multiplied by this factor ONCE, here.
# Nothing else may re-apply it: downstream code reads WORLD_UNITS_PER_METRE
# (5.132 wu/m) or the run's terrain_metadata content_scale, which stores it.
# At 1.5 the Numabe terrain is 115,092 patches. It was held at 1.0 while the
# Classic engine's 100,000 ceiling refused that; FL-4936 phase 1 (operator
# approval 2026-10-13) raised the shared ceiling to 1,000,000 and the operator
# then said "yes switch" (2026-10-13), so the map is 1.5x again.
OPERATOR_MAP_SCALE = 1.5
WORLD_UNITS_PER_METRE = SPRITE_WORLD_UNITS_PER_METRE * OPERATOR_MAP_SCALE
BAKE_UNITS_PER_WORLD_UNIT = 16.0
BAKE_UNITS_PER_METRE = WORLD_UNITS_PER_METRE * BAKE_UNITS_PER_WORLD_UNIT
BAKE_HEIGHT_ZERO = 128.0

FRAME_GEOGRAPHIC = "epsg4326"
FRAME_PROJECTED = "epsg32654"
FRAME_Y9_WORLD = "y9_world"
FRAME_Y9_BAKE = "y9_bake"

# The vertical datum string is quoted from the frozen manifest record
# ``gsi_dem5a_elevation.crs`` rather than named from memory: this repo has no
# other evidence of which Japanese vertical datum the tiles carry.
VERTICAL_DATUM_GSI = (
    "orthometric metres on the Japanese vertical datum "
    "(source_manifest.json record gsi_dem5a_elevation, field crs)"
)


# --------------------------------------------------------------------------
# Kruger n-series coefficients (functions of the third flattening n)
# --------------------------------------------------------------------------

_N = F_WGS84 / (2.0 - F_WGS84)
_RECTIFYING_RADIUS = (
    A_WGS84 / (1.0 + _N) * (1.0 + _N**2 / 4.0 + _N**4 / 64.0 + _N**6 / 256.0)
)

_ALPHA = (
    _N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16 + 41 * _N**4 / 180
    - 127 * _N**5 / 288 + 7891 * _N**6 / 37800,
    13 * _N**2 / 48 - 3 * _N**3 / 5 + 557 * _N**4 / 1440 + 281 * _N**5 / 630
    - 1983433 * _N**6 / 1935360,
    61 * _N**3 / 240 - 103 * _N**4 / 140 + 15061 * _N**5 / 26880
    + 167603 * _N**6 / 181440,
    49561 * _N**4 / 161280 - 179 * _N**5 / 168 + 6601661 * _N**6 / 7257600,
    34729 * _N**5 / 80640 - 3418889 * _N**6 / 1995840,
    212378941 * _N**6 / 319334400,
)

_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96 - _N**4 / 360 - 81 * _N**5 / 512
    + 96199 * _N**6 / 604800,
    _N**2 / 48 + _N**3 / 15 - 437 * _N**4 / 1440 + 46 * _N**5 / 105
    - 1118711 * _N**6 / 3870720,
    17 * _N**3 / 480 - 37 * _N**4 / 840 - 209 * _N**5 / 4480 + 5569 * _N**6 / 90720,
    4397 * _N**4 / 161280 - 11 * _N**5 / 504 - 830251 * _N**6 / 7257600,
    4583 * _N**5 / 161280 - 108847 * _N**6 / 3991680,
    20648693 * _N**6 / 638668800,
)

_DELTA = (
    2 * _N - 2 * _N**2 / 3 - 2 * _N**3 + 116 * _N**4 / 45 + 26 * _N**5 / 45
    - 2854 * _N**6 / 675,
    7 * _N**2 / 3 - 8 * _N**3 / 5 - 227 * _N**4 / 45 + 2704 * _N**5 / 315
    + 2323 * _N**6 / 945,
    56 * _N**3 / 15 - 136 * _N**4 / 35 - 1262 * _N**5 / 105 + 73814 * _N**6 / 2835,
    4279 * _N**4 / 630 - 332 * _N**5 / 35 - 399572 * _N**6 / 14175,
    4174 * _N**5 / 315 - 144838 * _N**6 / 6237,
    601676 * _N**6 / 22275,
)


def zone_for_lon(lon: float) -> int:
    """UTM zone number for a longitude.  Zone 54 spans 138E..144E."""
    return int((lon + 180.0) // 6.0) + 1


def central_meridian(zone: int) -> float:
    return zone * 6.0 - 183.0


def utm_epsg(zone: int, northern: bool = True) -> str:
    base = 32600 if northern else 32700
    return f"EPSG:{base + zone}"


def utm_forward(lon: float, lat: float, zone: int = UTM_ZONE_AOI,
                northern: bool = True) -> tuple[float, float]:
    """EPSG:4326 lon/lat (degrees) -> UTM easting/northing (metres).

    Kruger transverse-Mercator series to sixth order in the third flattening.
    Agreement with PROJ and GDAL over the AOI is reported by ``self_test()``.
    """
    lam0 = math.radians(central_meridian(zone))
    phi = math.radians(lat)
    lam = math.radians(lon) - lam0
    sin_phi = math.sin(phi)
    two_root_n = 2.0 * math.sqrt(_N) / (1.0 + _N)
    t = math.sinh(math.atanh(sin_phi) - two_root_n * math.atanh(two_root_n * sin_phi))
    xi_p = math.atan2(t, math.cos(lam))
    eta_p = math.atanh(math.sin(lam) / math.hypot(1.0, t))
    xi = xi_p + sum(
        _ALPHA[j] * math.sin(2 * (j + 1) * xi_p) * math.cosh(2 * (j + 1) * eta_p)
        for j in range(6)
    )
    eta = eta_p + sum(
        _ALPHA[j] * math.cos(2 * (j + 1) * xi_p) * math.sinh(2 * (j + 1) * eta_p)
        for j in range(6)
    )
    easting = FALSE_EASTING + K0_UTM * _RECTIFYING_RADIUS * eta
    northing = K0_UTM * _RECTIFYING_RADIUS * xi
    if not northern:
        northing += FALSE_NORTHING_SOUTH
    return easting, northing


def utm_inverse(easting: float, northing: float, zone: int = UTM_ZONE_AOI,
                northern: bool = True) -> tuple[float, float]:
    """UTM easting/northing (metres) -> EPSG:4326 lon/lat (degrees)."""
    lam0 = math.radians(central_meridian(zone))
    if not northern:
        northing -= FALSE_NORTHING_SOUTH
    xi = northing / (K0_UTM * _RECTIFYING_RADIUS)
    eta = (easting - FALSE_EASTING) / (K0_UTM * _RECTIFYING_RADIUS)
    xi_p = xi - sum(
        _BETA[j] * math.sin(2 * (j + 1) * xi) * math.cosh(2 * (j + 1) * eta)
        for j in range(6)
    )
    eta_p = eta - sum(
        _BETA[j] * math.cos(2 * (j + 1) * xi) * math.sinh(2 * (j + 1) * eta)
        for j in range(6)
    )
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    phi = chi + sum(_DELTA[j] * math.sin(2 * (j + 1) * chi) for j in range(6))
    lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lam0 + lam), math.degrees(phi)


# --------------------------------------------------------------------------
# Y9 world / bake frame
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Y9WorldFrame:
    """Y9 bake frame anchored on a named projected point.

    ``anchor_easting`` / ``anchor_northing`` are EPSG:32654 metres; the anchor
    is world-unit (0, 0).  ``anchor_source`` records which element the anchor
    came from so the frame can be reproduced without this object.
    """

    anchor_easting: float
    anchor_northing: float
    anchor_source: str
    zone: int = UTM_ZONE_AOI
    northern: bool = True
    world_units_per_metre: float = WORLD_UNITS_PER_METRE
    bake_units_per_world_unit: float = BAKE_UNITS_PER_WORLD_UNIT

    # -- construction ------------------------------------------------------

    @classmethod
    def anchored_at_lonlat(cls, lon: float, lat: float, anchor_source: str,
                           zone: int = UTM_ZONE_AOI, northern: bool = True) -> "Y9WorldFrame":
        easting, northing = utm_forward(lon, lat, zone, northern)
        return cls(easting, northing, anchor_source, zone, northern)

    # -- planar ------------------------------------------------------------

    def utm_to_world(self, easting: float, northing: float) -> tuple[float, float]:
        return (
            (easting - self.anchor_easting) * self.world_units_per_metre,
            (northing - self.anchor_northing) * self.world_units_per_metre,
        )

    def world_to_utm(self, wx: float, wy: float) -> tuple[float, float]:
        return (
            wx / self.world_units_per_metre + self.anchor_easting,
            wy / self.world_units_per_metre + self.anchor_northing,
        )

    def lonlat_to_world(self, lon: float, lat: float) -> tuple[float, float]:
        return self.utm_to_world(*utm_forward(lon, lat, self.zone, self.northern))

    def world_to_lonlat(self, wx: float, wy: float) -> tuple[float, float]:
        return utm_inverse(*self.world_to_utm(wx, wy), self.zone, self.northern)

    # -- vertical ----------------------------------------------------------

    def metres_to_world_z(self, metres: float) -> float:
        return metres * self.world_units_per_metre

    def metres_to_bake_units(self, metres: float) -> float:
        return metres * self.world_units_per_metre * self.bake_units_per_world_unit

    def bake_height_value(self, world_z: float) -> float:
        """Raw patch height as probe_instance_terrain_z reads it: 128 + z*16."""
        return BAKE_HEIGHT_ZERO + world_z * self.bake_units_per_world_unit

    def as_metadata(self) -> dict:
        return {
            "anchor_easting_m": self.anchor_easting,
            "anchor_northing_m": self.anchor_northing,
            "anchor_source": self.anchor_source,
            "anchor_lonlat": list(utm_inverse(self.anchor_easting, self.anchor_northing,
                                              self.zone, self.northern)),
            "projected_crs": utm_epsg(self.zone, self.northern),
            "world_units_per_metre": self.world_units_per_metre,
            "bake_units_per_world_unit": self.bake_units_per_world_unit,
            "bake_units_per_metre": self.world_units_per_metre * self.bake_units_per_world_unit,
            "bake_height_zero": BAKE_HEIGHT_ZERO,
        }


# --------------------------------------------------------------------------
# coordinate_frame records for a World IR document
# --------------------------------------------------------------------------


def frame_geographic() -> dict:
    return S.coordinate_frame(
        frame_id=FRAME_GEOGRAPHIC,
        crs="EPSG:4326",
        units="degree",
        axis_order=["longitude", "latitude"],
        vertical_datum="none:2d_geographic",
        note="WGS84 geographic, RFC 7946 ordinate order as written by the frozen "
             "bundle's numabe_corridor_aoi.geojson; the source frame of every OSM node",
    )


def frame_projected(zone: int = UTM_ZONE_AOI, northern: bool = True,
                    vertical_datum: str | None = None) -> dict:
    return S.coordinate_frame(
        frame_id=FRAME_PROJECTED,
        crs=utm_epsg(zone, northern),
        units="metre",
        axis_order=["easting", "northing", "up"],
        vertical_datum=vertical_datum or "none:2d_projected",
        note=f"WGS84 / UTM zone {zone}{'N' if northern else 'S'}, the AOI's declared "
             "metric working frame (geojson crs.metric_working); Kruger n-series to "
             "6th order in scripts/pipeline/geo_frames.py, verified against PROJ and GDAL",
    )


def frame_y9_world(frame: Y9WorldFrame) -> dict:
    meta = frame.as_metadata()
    return S.coordinate_frame(
        frame_id=FRAME_Y9_WORLD,
        crs="local:y9_world",
        units="y9_world_units",
        axis_order=["x", "y", "z"],
        vertical_datum="local:terrain_ground",
        note=(
            f"{meta['world_units_per_metre']} world units per metre on every axis "
            f"(scripts/pipeline/osm_projection.py:50, compile_facade_shell.py:187); "
            f"origin = {meta['anchor_source']} at "
            f"E {meta['anchor_easting_m']:.3f} N {meta['anchor_northing_m']:.3f} "
            f"in {meta['projected_crs']}"
        ),
    )


def frame_y9_bake(frame: Y9WorldFrame) -> dict:
    meta = frame.as_metadata()
    return S.coordinate_frame(
        frame_id=FRAME_Y9_BAKE,
        crs="local:y9_bake",
        units="y9_bake_units",
        axis_order=["x", "y", "height"],
        vertical_datum="local:bake_height_zero_128",
        note=(
            f"height axis of the baked .a3d patch grid: "
            f"{meta['bake_units_per_metre']} bake units per metre "
            f"({meta['world_units_per_metre']} world units/m x "
            f"{meta['bake_units_per_world_unit']} bake units/world unit, "
            f"compile_facade_shell.py:61); raw height value = "
            f"{BAKE_HEIGHT_ZERO} + world_z * {meta['bake_units_per_world_unit']} "
            "(probe_instance_terrain_z.py:15)"
        ),
    )


def declare_frames(store, frame: Y9WorldFrame, vertical_datum: str | None = None) -> list[dict]:
    """Add all four frames to a WorldIRStore and return them."""
    frames = [
        frame_geographic(),
        frame_projected(frame.zone, frame.northern, vertical_datum),
        frame_y9_world(frame),
        frame_y9_bake(frame),
    ]
    for rec in frames:
        store.add_frame(rec)
    return frames


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

# Points spread across the AOI and across zone 54, so a referee disagreement
# that only shows up far from the central meridian cannot hide.
REFERENCE_POINTS: tuple[tuple[float, float], ...] = (
    (139.6730398, 35.5828560),   # selected station entrance, OSM node 9865199050
    (139.6729365, 35.5827897),   # alternate entrance, OSM node 9865199049
    (139.6730652, 35.5827683),   # station node 7834193975
    (139.6722376, 35.5819619),   # AOI bbox SW
    (139.6746123, 35.5834444),   # AOI bbox NE
    (141.0000000, 35.0000000),   # zone 54 central meridian
    (138.0000000, 40.0000000),   # zone 54 west edge, high latitude
    (143.9000000, 45.0000000),   # zone 54 east edge, high latitude
)


def _referees() -> dict[str, Any]:
    """Every independent projection implementation that imports here."""
    found: dict[str, Any] = {}
    try:
        import pyproj  # noqa: PLC0415

        found["pyproj"] = {
            "version": pyproj.__version__,
            "forward": pyproj.Transformer.from_crs(
                "EPSG:4326", utm_epsg(UTM_ZONE_AOI), always_xy=True
            ).transform,
        }
    except Exception as exc:  # pragma: no cover - absence is a reported fact
        found["pyproj"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from osgeo import osr  # noqa: PLC0415

        osr.UseExceptions()
        src = osr.SpatialReference()
        src.ImportFromEPSG(4326)
        src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        dst = osr.SpatialReference()
        dst.ImportFromEPSG(int(utm_epsg(UTM_ZONE_AOI).split(":")[1]))
        dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        ct = osr.CoordinateTransformation(src, dst)

        def _gdal(lon: float, lat: float) -> tuple[float, float]:
            e, n, *_ = ct.TransformPoint(lon, lat)
            return e, n

        found["gdal"] = {"version": getattr(osr, "__version__", "unknown"), "forward": _gdal}
    except Exception as exc:  # pragma: no cover - absence is a reported fact
        found["gdal"] = {"error": f"{type(exc).__name__}: {exc}"}
    return found


def bundle_reference_residuals(bundle_dir: Path) -> dict:
    """Reproduce the frozen bundle's own EPSG:32654 figures with this transform.

    The bundle was built by a different agent with a different toolchain, so its
    metre figures are a third reference, independent of PROJ and of GDAL.
    """
    geojson = json.loads(
        (Path(bundle_dir) / "numabe_corridor_aoi.geojson").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (Path(bundle_dir) / "source_manifest.json").read_text(encoding="utf-8")
    )
    features = {f.get("id"): f for f in geojson["features"]}

    ring = [utm_forward(*pt) for pt in features["aoi_polygon"]["geometry"]["coordinates"][0]]
    twice_area = sum(
        ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
        for i in range(len(ring) - 1)
    )
    area_m2 = abs(twice_area) / 2.0

    line = [utm_forward(*pt) for pt in features["corridor_centerline"]["geometry"]["coordinates"]]
    length_m = sum(math.dist(line[i], line[i + 1]) for i in range(len(line) - 1))

    bbox_ring = [utm_forward(*pt) for pt in features["extract_bbox"]["geometry"]["coordinates"][0]]
    # Edge lengths, not the axis-aligned span: grid convergence rotates the
    # lon/lat rectangle by ~0.8 deg in UTM, so its bounding box is wider than
    # its sides.  The manifest records the SIDES.
    south_edge_m = math.dist(bbox_ring[0], bbox_ring[1])
    east_edge_m = math.dist(bbox_ring[1], bbox_ring[2])

    entrance = utm_forward(*features["selected_entrance"]["geometry"]["coordinates"])
    alternate = utm_forward(*features["alternate_entrance"]["geometry"]["coordinates"])
    entrance_gap_m = math.dist(entrance, alternate)

    declared = manifest["context"]
    return {
        "aoi_area_m2": {"computed": area_m2, "declared": declared["aoi"]["area_m2"],
                        "residual": area_m2 - declared["aoi"]["area_m2"]},
        "corridor_length_m": {
            "computed": length_m,
            "declared": declared["corridor"]["corridor_length_m"],
            "residual": length_m - declared["corridor"]["corridor_length_m"],
        },
        "bbox_edges_m": {
            "computed": [south_edge_m, east_edge_m],
            "declared": declared["aoi"]["bbox_extent_m"],
            "residual": [south_edge_m - declared["aoi"]["bbox_extent_m"][0],
                         east_edge_m - declared["aoi"]["bbox_extent_m"][1]],
        },
        "entrance_to_alternate_m": {"computed": entrance_gap_m},
    }


def self_test(points: Sequence[tuple[float, float]] = REFERENCE_POINTS,
              bundle_dir: Path | None = None) -> dict:
    """Residuals of this transform against every referee, in metres."""
    referees = _referees()
    per_referee: dict[str, Any] = {}
    for name, ref in referees.items():
        if "forward" not in ref:
            per_referee[name] = {"available": False, "reason": ref.get("error")}
            continue
        worst = 0.0
        for lon, lat in points:
            mine = utm_forward(lon, lat)
            theirs = ref["forward"](lon, lat)
            worst = max(worst, math.dist(mine, theirs))
        per_referee[name] = {
            "available": True,
            "version": ref.get("version"),
            "max_residual_m": worst,
            "points": len(points),
        }

    worst_rt = 0.0
    for lon, lat in points:
        easting, northing = utm_forward(lon, lat)
        lon2, lat2 = utm_inverse(easting, northing)
        e2, n2 = utm_forward(lon2, lat2)
        worst_rt = max(worst_rt, math.dist((easting, northing), (e2, n2)))

    out: dict[str, Any] = {
        "implementation": "Kruger n-series, 6th order, stdlib math",
        "zone": UTM_ZONE_AOI,
        "projected_crs": utm_epsg(UTM_ZONE_AOI),
        "referees": per_referee,
        "roundtrip_max_residual_m": worst_rt,
        "world_units_per_metre": WORLD_UNITS_PER_METRE,
        "bake_units_per_metre": BAKE_UNITS_PER_METRE,
    }
    if bundle_dir is not None:
        out["frozen_bundle_reference"] = bundle_reference_residuals(bundle_dir)
    return out


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true",
                    help="report residuals against every available referee")
    ap.add_argument("--bundle", type=Path,
                    help="frozen source bundle directory, for its own EPSG:32654 figures")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--lonlat", nargs=2, type=float, metavar=("LON", "LAT"),
                    help="project one point and print every frame's coordinates")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.lonlat:
        lon, lat = args.lonlat
        easting, northing = utm_forward(lon, lat)
        frame = Y9WorldFrame.anchored_at_lonlat(lon, lat, "cli anchor")
        payload = {
            "epsg4326": [lon, lat],
            "epsg32654": [easting, northing],
            "y9_world_at_own_anchor": list(frame.lonlat_to_world(lon, lat)),
            "metre_to_world_units": WORLD_UNITS_PER_METRE,
            "metre_to_bake_units": BAKE_UNITS_PER_METRE,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if not args.selftest:
        ap.error("nothing to do: pass --selftest or --lonlat")

    report = self_test(bundle_dir=args.bundle)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print(f"[geo-frames] {report['implementation']} -> {report['projected_crs']}")
    for name, res in sorted(report["referees"].items()):
        if res.get("available"):
            print(f"[geo-frames] vs {name} {res['version']}: "
                  f"max residual {res['max_residual_m']:.3e} m over {res['points']} points")
        else:
            print(f"[geo-frames] vs {name}: UNAVAILABLE ({res['reason']})")
    print(f"[geo-frames] forward/inverse round trip: "
          f"{report['roundtrip_max_residual_m']:.3e} m")
    ref = report.get("frozen_bundle_reference")
    if ref:
        print(f"[geo-frames] frozen bundle corridor length "
              f"{ref['corridor_length_m']['computed']:.2f} m vs declared "
              f"{ref['corridor_length_m']['declared']:.2f} m "
              f"(residual {ref['corridor_length_m']['residual']:+.3f} m)")
        print(f"[geo-frames] frozen bundle AOI area "
              f"{ref['aoi_area_m2']['computed']:.1f} m2 vs declared "
              f"{ref['aoi_area_m2']['declared']:.1f} m2 "
              f"(residual {ref['aoi_area_m2']['residual']:+.2f} m2)")
        print(f"[geo-frames] frozen bundle bbox edges "
              f"{ref['bbox_edges_m']['computed'][0]:.1f} x "
              f"{ref['bbox_edges_m']['computed'][1]:.1f} m vs declared "
              f"{ref['bbox_edges_m']['declared']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
