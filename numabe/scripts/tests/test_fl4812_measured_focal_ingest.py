"""A measured focal length must not abort the camera ingest (FL-4812).

WHAT THIS GUARDS
================
``GeoSolution.focal_sigma_px`` defaults to NaN and is assigned only when the
focal length was a FREE parameter in the solve
(``camera_solve/georegister.py:393``). When the source's EXIF states a focal
length the solve pins it instead of estimating it, so the sigma stays NaN --
and ``world_ir_ingest_cameras`` wrote that NaN straight into the World IR
document, where canonicalization rejects non-finite floats:

    CanonicalizationError: non-finite float nan

The effect was that the ingest worked on corpora whose cameras report NO focal
length and aborted on every corpus that reports one. It was not caught by the
control-corridor proof precisely because all 268 items of that corpus omit the
focal length, which is the failure mode where a green proof hides a bug: the
one code path the control data could not reach was the broken one.

This test drives the measured-focal path directly and asserts the emitted
record both canonicalizes and says which case it is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.camera_solve.capture import Capture, IntrinsicsPrior  # noqa: E402
from scripts.pipeline.camera_solve.geodesy import EnuFrame  # noqa: E402
from scripts.pipeline.camera_solve.georegister import GeoSolution  # noqa: E402
from scripts.pipeline.world_ir import schema as S  # noqa: E402
from scripts.pipeline.world_ir import validate as V  # noqa: E402
from scripts.pipeline import world_ir_ingest_cameras as ingest  # noqa: E402


def _capture(state: str, basis: str) -> Capture:
    return Capture(
        capture_id="cap-0001", rank=0, image_path="/nonexistent/frame_0.jpg",
        width=4032, height=3024, lon=139.673, lat=35.5828, alt=12.0,
        azimuth_deg=160.0, horizontal_accuracy_m=5.0,
        intrinsics=IntrinsicsPrior(focal_px=3200.0, sigma_px=64.0, state=state, basis=basis),
        licence="CC-BY-4.0", producer="test", captured_at="2024-05-30T07:15:44Z",
        attribution="test", source_url="https://example.invalid/cap-0001",
    )


def _solution(*, focal_free: bool) -> GeoSolution:
    sol = GeoSolution(
        frames=[0],
        rvecs={0: np.zeros(3)},
        centers={0: np.array([1.0, 2.0, 3.0])},
        points={},
        focal_px=3200.0, cx=2016.0, cy=1512.0,
        focal_free=focal_free,
    )
    sol.center_covariance[0] = np.eye(3) * 0.25
    if focal_free:
        sol.focal_sigma_px = 5.17
    # when the focal is NOT free the solve leaves focal_sigma_px at NaN
    return sol


@pytest.mark.parametrize("focal_free", [True, False])
def test_camera_observation_canonicalizes_for_both_focal_cases(focal_free):
    frame = EnuFrame(origin_lon=139.673, origin_lat=35.5828, origin_alt=0.0)
    state = "prior" if focal_free else "measured"
    obs = ingest.camera_observation(_capture(state, "exif FocalLengthIn35mmFilm"),
                                    _solution(focal_free=focal_free), 0, frame)
    # This is the call that raised CanonicalizationError on the measured path.
    blob = S.canonical_dumps({"probe": obs})
    assert "NaN" not in blob and "Infinity" not in blob

    solved = obs["attributes"]["solved_by_this_ingest"]
    if focal_free:
        assert solved["focal_sigma_px"] == pytest.approx(5.17)
        assert solved["focal_length_solved"] is True
    else:
        assert solved["focal_sigma_px"] is None
        assert solved["focal_length_solved"] is False


def test_a_measured_focal_document_validates_end_to_end():
    frame = EnuFrame(origin_lon=139.673, origin_lat=35.5828, origin_alt=0.0)
    capture = _capture("measured", "exif FocalLengthIn35mmFilm")
    solution = _solution(focal_free=False)

    store = S.document(ir_id="fl4812-measured-focal", generated_at="2026-09-12T00:00:00Z")
    store["coordinate_frames"][frame.frame_id] = ingest.coordinate_frame_for(frame)
    obs = ingest.camera_observation(capture, solution, 0, frame)
    store["source_observations"].append(obs)
    store["canonical_features"].append(
        ingest.camera_feature(capture, solution, 0, frame, obs["observation_id"])
    )

    assert V.validate_document(store) == []
    assert b"NaN" not in S.canonical_bytes(store)


def test_the_nan_can_never_reach_the_document_again():
    """Source guard: the sigma must not be emitted without a finiteness check."""
    src = (REPO_ROOT / "scripts" / "pipeline" / "world_ir_ingest_cameras.py").read_text("utf-8")
    bare = [
        line for line in src.splitlines()
        if '"focal_sigma_px": solution.focal_sigma_px' in line
        and not line.lstrip().startswith("#")
    ]
    assert bare == [], bare
