"""Capture + intrinsics model for the camera solve (FL-4812).

WHY MISSING FOCAL LENGTH IS MODELLED AND NOT DEFAULTED
======================================================
The control sequence this solve was proven on (Panoramax collection
``05e79ef6-1aff-4c03-93c6-dab1d8552e75``, CC-BY-SA-4.0) carries

    pers:interior_orientation.camera_model          = "iPhone"
    pers:interior_orientation.sensor_array_dimensions = [4032, 3024]
    pers:interior_orientation.focal_length          = ABSENT on all 268 items
    exif Exif.Photo.FocalLength / FocalLengthIn35mmFilm = ABSENT on all 268 items

so there is NO measured focal length anywhere in the source. A pipeline that
silently substituted a constant would be reporting a synthesized number as a
measured one, and every downstream metric residual would inherit that lie
invisibly.

So intrinsics are a first-class, three-state thing:

  ``measured``   the source stated a focal length; it is used and pinned hard.
  ``prior``      the source stated none; a stated prior seeds the solve, the
                 focal is then a FREE PARAMETER refined by bundle adjustment,
                 and its posterior sigma is reported in the receipt.
There is deliberately no third "absent" state.  A prior can always be formed
from the working image size, so refusing to solve would discard usable evidence;
what must never happen is the prior being *reported* as a measurement, and
``state`` is what prevents that.

``IntrinsicsPrior.provenance`` is what the World IR emitter maps onto
``PROVENANCE_MEASURED`` / ``PROVENANCE_DERIVED``, so the distinction survives
all the way into world truth instead of being lost at ingest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse

from .geodesy import exif_dms, exif_rational

# A 35 mm-equivalent focal length for the phone class when the source states
# none.  It is a PRIOR MEAN, never a measurement: the solve refines it and the
# receipt prints where it ended up.  Stated here once so it is inspectable
# rather than buried in a solver call.
DEFAULT_EQUIV35_MM = 26.0
DEFAULT_EQUIV35_SIGMA_MM = 3.0
FULL_FRAME_LONG_EDGE_MM = 36.0

# Capture GPS: the sequence states ``quality:horizontal_accuracy`` explicitly.
# Vertical accuracy is never stated by this source, and phone barometric/GPS
# altitude is far worse than its horizontal fix, so it gets its own weaker
# sigma rather than borrowing the horizontal one.
FALLBACK_HORIZONTAL_ACCURACY_M = 10.0
VERTICAL_ACCURACY_MULTIPLIER = 3.0

# Phone compass, quantised to whole degrees by this source and subject to local
# magnetic disturbance next to steel fences and overhead wires.
AZIMUTH_SIGMA_DEG = 15.0

# ROLL: why this prior is NOT optional.
# A walking capture track is very nearly a straight line, and a straight line
# is invariant under rotation about itself.  So capture GPS - however accurate -
# cannot constrain the roll of the block about the walking direction, and the
# compass cannot either, because a rolled camera still points the same way.
# That gauge freedom is real: the first solve of this control corridor came back
# with every camera rolled ~64 deg, self-consistent, with a 0.44 px median
# reprojection error and a plausible compass residual.  Nothing in the residual
# set objected, because nothing in it knew which way was down.
#
# The source states NO roll (pers:roll is absent on all 268 items), so a prior is
# the only way to tie the solve to gravity.  A hand-held phone street photo is
# held near-upright; 10 deg is a loose allowance around that.  It is corroborated
# independently by the imagery itself: vertical structures do project as
# near-vertical image edges, which is exactly what the structure-tensor test in
# ``solve._vertical_structure_vote`` measures.  Pitch is deliberately left free -
# it really does swing several degrees frame to frame while walking.
ROLL_SIGMA_DEG = 10.0

INTRINSICS_MEASURED = "measured"
INTRINSICS_PRIOR = "prior"


@dataclass(frozen=True)
class IntrinsicsPrior:
    """Focal length knowledge for one capture, with its provenance kept."""

    focal_px: float
    sigma_px: float
    state: str
    basis: str

    @property
    def is_free_parameter(self) -> bool:
        """A prior seeds the solve; a measurement pins it."""
        return self.state == INTRINSICS_PRIOR

    def as_record(self) -> dict:
        return {
            "focal_px": self.focal_px,
            "sigma_px": self.sigma_px,
            "state": self.state,
            "basis": self.basis,
            "refined_by_bundle_adjustment": self.is_free_parameter,
        }


def intrinsics_from_item(props: Mapping[str, Any], width_px: int, height_px: int) -> IntrinsicsPrior:
    """Derive focal knowledge for one Panoramax/STAC item at a working size.

    ``width_px``/``height_px`` are the size of the image actually solved, which
    is NOT the sensor array size: the open derivative used here is 2048 wide
    where the sensor reports 4032.  Focal length in pixels scales with the
    image actually measured, so the conversion happens against the working
    size and the sensor dimensions are used only to check the aspect ratio.
    """
    interior = props.get("pers:interior_orientation") or {}
    exif = props.get("exif") or {}
    long_edge = float(max(width_px, height_px))

    stated_px = interior.get("focal_length_px")
    if stated_px:
        return IntrinsicsPrior(
            focal_px=float(stated_px),
            sigma_px=1.0,
            state=INTRINSICS_MEASURED,
            basis="pers:interior_orientation.focal_length_px",
        )

    equiv35 = exif.get("Exif.Photo.FocalLengthIn35mmFilm")
    if equiv35:
        mm = exif_rational(str(equiv35))
        return IntrinsicsPrior(
            focal_px=long_edge * mm / FULL_FRAME_LONG_EDGE_MM,
            sigma_px=long_edge * 0.5 / FULL_FRAME_LONG_EDGE_MM,
            state=INTRINSICS_MEASURED,
            basis="exif Exif.Photo.FocalLengthIn35mmFilm",
        )

    focal_mm = interior.get("focal_length") or exif.get("Exif.Photo.FocalLength")
    sensor = interior.get("sensor_array_dimensions")
    sensor_width_mm = interior.get("sensor_width_mm")
    if focal_mm and sensor and sensor_width_mm:
        mm = exif_rational(str(focal_mm))
        return IntrinsicsPrior(
            focal_px=long_edge * mm / float(sensor_width_mm),
            sigma_px=long_edge * 0.2 / float(sensor_width_mm),
            state=INTRINSICS_MEASURED,
            basis="pers:interior_orientation.focal_length + sensor_width_mm",
        )

    # Nothing measured.  State the prior loudly, including WHY it is a prior.
    if focal_mm and not sensor_width_mm:
        basis = (
            "focal_length present in mm but the source states no sensor width, "
            "so it cannot be converted to pixels; prior used instead"
        )
    else:
        basis = (
            f"source states NO focal length (pers:interior_orientation.focal_length and "
            f"exif FocalLength/FocalLengthIn35mmFilm all absent); prior seeded from a "
            f"{DEFAULT_EQUIV35_MM:.0f}mm 35mm-equivalent phone lens and refined by bundle adjustment"
        )
    return IntrinsicsPrior(
        focal_px=long_edge * DEFAULT_EQUIV35_MM / FULL_FRAME_LONG_EDGE_MM,
        sigma_px=long_edge * DEFAULT_EQUIV35_SIGMA_MM / FULL_FRAME_LONG_EDGE_MM,
        state=INTRINSICS_PRIOR,
        basis=basis,
    )


@dataclass
class Capture:
    """One solved-or-solvable picture: pixels, priors and the source's claims."""

    capture_id: str
    rank: int
    image_path: str
    width: int
    height: int
    lon: float
    lat: float
    alt: float | None
    azimuth_deg: float | None
    horizontal_accuracy_m: float
    intrinsics: IntrinsicsPrior
    licence: str
    producer: str
    captured_at: str
    attribution: str
    source_url: str
    digest: str = ""
    source_provider: str = "unknown"
    source_id: str = "unknown_street_imagery"
    vertical_accuracy_override_m: float | None = None
    source_dimensions_px: tuple[int, int] | None = None
    working_image_digest: str = ""
    working_image_policy: Any = "source_pixels"
    enu: tuple[float, float, float] = field(default=(0.0, 0.0, 0.0))

    @property
    def principal_point(self) -> tuple[float, float]:
        """Assumed image centre.

        The source states no principal point.  Recorded as an assumption here
        rather than being invisible inside the projection function; a real
        calibration would replace it and the receipt would change.
        """
        return (self.width / 2.0, self.height / 2.0)

    @property
    def vertical_accuracy_m(self) -> float:
        if self.vertical_accuracy_override_m is not None:
            return self.vertical_accuracy_override_m
        return self.horizontal_accuracy_m * VERTICAL_ACCURACY_MULTIPLIER

    def as_record(self) -> dict:
        return {
            "capture_id": self.capture_id,
            "rank": self.rank,
            "width": self.width,
            "height": self.height,
            "lon": self.lon,
            "lat": self.lat,
            "alt": self.alt,
            "azimuth_deg": self.azimuth_deg,
            "horizontal_accuracy_m": self.horizontal_accuracy_m,
            "vertical_accuracy_m": self.vertical_accuracy_m,
            "azimuth_sigma_deg": AZIMUTH_SIGMA_DEG,
            "intrinsics": self.intrinsics.as_record(),
            "licence": self.licence,
            "producer": self.producer,
            "captured_at": self.captured_at,
            "attribution": self.attribution,
            "source_url": self.source_url,
            "source_provider": self.source_provider,
            "source_id": self.source_id,
            "digest": self.digest,
            "source_dimensions_px": list(self.source_dimensions_px or (self.width, self.height)),
            "working_image_digest": self.working_image_digest or self.digest,
            "working_image_policy": self.working_image_policy,
        }


def capture_from_item(item: Mapping[str, Any], image_path: str, width: int, height: int,
                      digest: str = "") -> Capture:
    """Build a Capture from one Panoramax/STAC item plus its downloaded pixels."""
    props = item["properties"]
    exif = props.get("exif") or {}
    lon, lat = item["geometry"]["coordinates"][:2]

    alt = None
    if exif.get("Exif.GPSInfo.GPSAltitude"):
        alt = exif_rational(str(exif["Exif.GPSInfo.GPSAltitude"]))
        if str(exif.get("Exif.GPSInfo.GPSAltitudeRef", "0")) == "1":
            alt = -alt

    # Prefer the EXIF GPS fix over the rounded item geometry when it is present:
    # the item geometry is the same fix, but the EXIF rationals keep more digits.
    if exif.get("Exif.GPSInfo.GPSLatitude") and exif.get("Exif.GPSInfo.GPSLongitude"):
        elat = exif_dms(str(exif["Exif.GPSInfo.GPSLatitude"]))
        elon = exif_dms(str(exif["Exif.GPSInfo.GPSLongitude"]))
        if str(exif.get("Exif.GPSInfo.GPSLatitudeRef", "N")).upper().startswith("S"):
            elat = -elat
        if str(exif.get("Exif.GPSInfo.GPSLongitudeRef", "E")).upper().startswith("W"):
            elon = -elon
        # Only trust it when it agrees with the published geometry; a mismatch
        # means the two disagree about the capture and must not be merged.
        if abs(elat - lat) < 1e-4 and abs(elon - lon) < 1e-4:
            lon, lat = elon, elat

    azimuth = props.get("view:azimuth")
    if azimuth is None and exif.get("Exif.GPSInfo.GPSImgDirection"):
        azimuth = exif_rational(str(exif["Exif.GPSInfo.GPSImgDirection"]))
    if azimuth is not None:
        azimuth = float(azimuth) % 360.0

    accuracy = props.get("quality:horizontal_accuracy")
    accuracy = float(accuracy) if accuracy is not None else FALLBACK_HORIZONTAL_ACCURACY_M

    providers = item.get("providers") or []
    producer = props.get("geovisio:producer") or (providers[0].get("name") if providers else "unknown")
    contributors = ", ".join(sorted({p.get("name", "") for p in providers if p.get("name")}))
    licence = props.get("license") or "unknown"
    self_link = next((l["href"] for l in item.get("links", []) if l.get("rel") == "self"), "")
    hostname = urlparse(self_link).hostname or ""
    if hostname == "commons.wikimedia.org" or hostname.endswith(".wikimedia.org"):
        source_provider = "Wikimedia Commons"
        source_id = "wikimedia_commons_street_imagery"
    elif "panoramax" in hostname or any(str(key).startswith("geovisio:") for key in props):
        source_provider = "Panoramax"
        source_id = "panoramax_street_imagery"
    else:
        source_provider = hostname or "STAC imagery source"
        source_id = "stac_street_imagery"

    return Capture(
        capture_id=str(item["id"]),
        rank=int(props.get("geovisio:rank_in_collection") or 0),
        image_path=image_path,
        width=int(width),
        height=int(height),
        lon=float(lon),
        lat=float(lat),
        alt=alt,
        azimuth_deg=azimuth,
        horizontal_accuracy_m=accuracy,
        intrinsics=intrinsics_from_item(props, width, height),
        licence=licence,
        producer=str(producer),
        captured_at=str(props.get("datetime") or ""),
        attribution=f"(C) {contributors or producer} / {source_provider}, {licence}",
        source_url=self_link,
        digest=digest,
        source_provider=source_provider,
        source_id=source_id,
    )


def capture_from_source_record(
    record: Mapping[str, Any], image_path: str, width: int, height: int,
    digest: str = "", *,
    source_dimensions_px: tuple[int, int] | None = None,
    working_image_digest: str = "",
    working_image_policy: Any = "source_pixels",
) -> Capture:
    """Build a Capture from the canonical route-capture manifest schema.

    The route acquisition owner has already normalized provider-specific STAC,
    Commons and EXIF fields.  Requiring callers to turn that canonical record
    back into provider-shaped STAC would create a second source-data owner.
    """
    focal_35mm = record.get("focal_35mm")
    horizontal_fov = record.get("horizontal_fov_deg")
    long_edge = float(max(width, height))
    if focal_35mm is not None:
        focal_px = long_edge * float(focal_35mm) / FULL_FRAME_LONG_EDGE_MM
        intrinsics = IntrinsicsPrior(
            focal_px=focal_px,
            sigma_px=long_edge * 0.5 / FULL_FRAME_LONG_EDGE_MM,
            state=INTRINSICS_MEASURED,
            basis="route capture manifest focal_35mm",
        )
    elif horizontal_fov is not None:
        focal_px = width / (2.0 * math.tan(math.radians(float(horizontal_fov)) / 2.0))
        intrinsics = IntrinsicsPrior(
            focal_px=focal_px,
            sigma_px=max(focal_px * 0.02, 1.0),
            state=INTRINSICS_MEASURED,
            basis="route capture manifest horizontal_fov_deg",
        )
    else:
        intrinsics = intrinsics_from_item({}, width, height)

    provider = str(record.get("provider") or "unknown")
    licence = str(record.get("license") or "unknown")
    return Capture(
        capture_id=str(record["capture_id"]),
        rank=int(record.get("sequence_index") or 0),
        image_path=image_path,
        width=int(width),
        height=int(height),
        lon=float(record["lon"]),
        lat=float(record["lat"]),
        alt=float(record["altitude_m"]) if record.get("altitude_m") is not None else None,
        azimuth_deg=(
            float(record["heading_deg"]) % 360.0
            if record.get("heading_deg") is not None else None
        ),
        horizontal_accuracy_m=float(
            record.get("horizontal_accuracy_m") or FALLBACK_HORIZONTAL_ACCURACY_M
        ),
        intrinsics=intrinsics,
        licence=licence,
        producer=provider,
        captured_at=str(record.get("timestamp") or ""),
        attribution=str(record.get("attribution") or f"{provider}, {licence}"),
        source_url=str(record.get("source_uri") or record.get("asset_uri") or ""),
        digest=digest or str(record.get("sha256") or ""),
        source_provider=provider,
        source_id=str(record.get("source_id") or record.get("sequence_id") or "route_capture"),
        vertical_accuracy_override_m=(
            float(record["vertical_accuracy_m"])
            if record.get("vertical_accuracy_m") is not None else None
        ),
        source_dimensions_px=source_dimensions_px,
        working_image_digest=working_image_digest,
        working_image_policy=working_image_policy,
    )


def horizontal_fov_deg(focal_px: float, width_px: int) -> float:
    return math.degrees(2.0 * math.atan(width_px / (2.0 * focal_px)))
