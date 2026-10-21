"""The shimmer observer must follow displayed paint, including stale VSB rows."""
from copy import deepcopy
from scripts.analyze_fl4359_moving_camera import summarize_presented_surface_stability


def row(frame, colour=0.4):
    return {
        "frame_id": frame, "screen_cell": {"x": 3 + frame, "y": 8},
        "selected_surface_owner": 0,  # retired perspective VSB is absent
        "final_paint_glyph": 124,
        "final_paint_slot_fg_rgb": [round(colour * 255), 128, 51],
        "final_paint_slot_bg_rgb": [26, 51, 26],
        "presented_surface": {
            "state": [1, 1, 96, 256], "head": [9, 2, 18, 124],
            "identity": [1, 18, 44, 255], "world": [10.25, 2.0, 20.75, 1.0],
            "fg": [colour, 0.5, 0.2, 1], "bg": [0.1, 0.2, 0.1, 1],
        },
    }


def test_joins_displayed_source_across_moving_cells_with_absent_legacy_vsb():
    result = summarize_presented_surface_stability([row(0), row(1, 0.6)])
    assert result["comparable_source_pairs"] == 1
    assert result["colour_changes"] == 1
    assert abs(result["max_fg_delta"] - 0.2) < 1e-6
    assert result["acceptance_claimed"] is False


def test_unjoined_or_different_world_samples_cannot_report_zero_shimmer():
    a, b = row(0), row(1)
    b["presented_surface"]["world"][0] += 0.01
    result = summarize_presented_surface_stability([a, b])
    assert result["comparable_source_pairs"] == 0
    assert result["colour_change_pct"] is None
    b = row(1)
    b["final_paint_glyph"] = 47
    result = summarize_presented_surface_stability([a, b])
    assert result["terminal_paint_mismatch_cells"] == 1
    assert result["comparable_source_pairs"] == 0


def test_quarter_duplicates_do_not_inflate_comparisons():
    a, b = row(0), row(1)
    result = summarize_presented_surface_stability([a, deepcopy(a), b, deepcopy(b)])
    assert result["valid_presented_cells"] == 2
    assert result["comparable_source_pairs"] == 1
    assert result["colour_change_pct"] == 0.0
