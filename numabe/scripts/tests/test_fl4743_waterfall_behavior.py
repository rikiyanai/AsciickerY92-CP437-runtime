from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from check_fl4743_waterfall_behavior import (
    EXPECTED_CAMERA,
    EXPECTED_PLAYER,
    SetupFailure,
    _downward_motion,
    _frame_measurement,
    _project,
    site_footprint,
    validate_route_sidecar,
)


def _sidecar() -> dict:
    return {
        "actor_presentation_reference_world": list(EXPECTED_PLAYER),
        "camera_origin": list(EXPECTED_CAMERA),
        "camera_yaw": 0.698140061673321,
        "camera_pitch": -0.463640006066219,
        "saved_image_size": [3600, 2194],
        "camera_transform_source": "live_camera_world_true_perspective",
        "projection_mode": 2,
        "world_to_clip_valid": True,
        "world_to_clip": [
            0.844537913799286, 0.518594920635223, 0.00011229876690777, -0.574934601783752,
            0.0, -1.61359548568726, 8.735041046747e-05, -0.447206795215607,
            -0.708663463592529, 0.618026852607727, 0.00013383018085733, -0.685168743133545,
            -191.418716430664, -10.2529382705688, 0.0823623985052109, 90.3990325927734,
        ],
        "fl4359_proof_valid": True,
    }


def test_exact_site_route_is_accepted() -> None:
    validate_route_sidecar(_sidecar(), image_size=(3600, 2194))
    box = site_footprint(_sidecar(), (3600, 2194))
    assert box == (279, 929, 665, 1576)


def test_route_mismatch_fails_closed() -> None:
    sidecar = _sidecar()
    sidecar["camera_origin"] = [0, 0, 0]
    with pytest.raises(SetupFailure):
        validate_route_sidecar(sidecar, image_size=(3600, 2194))


def test_project_uses_clip_matrix_and_keeps_site_local() -> None:
    matrix = _sidecar()["world_to_clip"]
    x, y = _project(matrix, (132.6, 10.9, -60.78), (3600, 2194))
    assert 450 < x < 580
    assert 950 < y < 1100


def test_downward_motion_prefers_positive_image_y_shift() -> None:
    previous = np.zeros((100, 30, 3), dtype=np.float32)
    current = np.zeros_like(previous)
    previous[20:30, :, 2] = 1.0
    current[26:36, :, 2] = 1.0
    measurement = _downward_motion(previous, current)
    assert measurement["best_shift_px"] > 0
    assert measurement["best_corr"] > measurement["corr_at_zero"]


def test_uniform_filled_sheet_has_no_receiver_preserving_gap() -> None:
    support = np.zeros((120, 80), dtype=bool)
    support[10:110, 20:60] = True
    image = np.zeros((120, 80, 3), dtype=np.float32)
    image[..., 2] = 0.75
    image[..., 1] = 0.55
    image[..., 0] = 0.05
    measurement = _frame_measurement(image, support)
    assert measurement["gap_fraction"] < 0.08


def test_broken_ribbon_is_measurable() -> None:
    support = np.zeros((120, 80), dtype=bool)
    support[10:110, 20:60] = True
    image = np.zeros((120, 80, 3), dtype=np.float32)
    for y in range(10, 110, 20):
        image[y : y + 10, 20:60] = (0.05, 0.58, 0.78)
    measurement = _frame_measurement(image, support)
    assert measurement["active_row_count"] > 0
    assert measurement["gap_fraction"] > 0.08
