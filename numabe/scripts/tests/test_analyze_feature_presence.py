"""Synthetic-fixture contracts for the feature-presence regression gate.

Each registered feature gets a PRESENT fixture and an ABSENT fixture built from
the same synthetic capture writer, so a wrong verdict fails here rather than in
a headed run. The fixtures deliberately mirror the CURRENT dump contract: every
``cell_owner_*`` product field is NULL and the only per-cell payload is
``final_cell_paint_buffer``.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "scripts/analyze_feature_presence.py"
SPEC = importlib.util.spec_from_file_location("analyze_feature_presence", TOOL_PATH)
assert SPEC and SPEC.loader
TOOL = importlib.util.module_from_spec(SPEC)
# @dataclass resolves annotations through sys.modules, so the module must be
# registered before exec_module or the dataclass decorator raises.
sys.modules["analyze_feature_presence"] = TOOL
SPEC.loader.exec_module(TOOL)


# ---------------------------------------------------------------------------
# Synthetic capture writer
# ---------------------------------------------------------------------------

GRID_COLS = 24
GRID_ROWS = 16

BASE_FEATURE_STATE = {
    "ascii_birds": False,
    "clouds": True,
    "cloud_shadow_deep": True,
    "night": False,
    "foliage_cards": True,
    "grass_field": True,
    "water_current_package": True,
    "weather_kind": 0,
    "weather_kind_name": "clear",
}


def _row(x: int, y: int, glyph_id: int | None, owner_kind: str, **extra) -> dict:
    """One cells.jsonl row shaped like the live final-cell dump contract."""

    row: dict = {
        "row_type": "exact_final_cell",
        "screen_cell": {"x": x, "y": y},
        "final_cell_paint_buffer": {
            "glyph_id": glyph_id,
            "owner_kind": owner_kind,
            "owner_kind_id": 5 if owner_kind == "water" else 1,
            "material_id": 140,
            "world_anchor_xz": [float(x), float(y)],
            "ink_coverage": 0.08,
            # The real dump carries fg_rgb on every row; the fixtures must too,
            # or the flower hue check would silently report itself SKIPPED.
            "fg_rgb": list(WATER_FG if owner_kind == "water" else GRASS_FG),
        },
        "luma": 0.5,
        # The retired-readback contract: every product fact is NULL.
        "cell_owner_kind": None,
        "cell_owner_glyph_id": None,
        "cell_owner_waterline_clip": None,
        "dynamic_mover_owner_kind": None,
        "fact_readback_state": "cell_owner_product_readback_retired",
    }
    row.update(extra)
    return row


def write_capture(
    directory: Path,
    *,
    rows: list[dict],
    feature_state: dict | None = None,
    actor_rows: list[dict] | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    state = dict(BASE_FEATURE_STATE)
    state.update(feature_state or {})
    metadata = {
        "feature_state": state,
        "weather_kind_name": state["weather_kind_name"],
        "actor_rows": actor_rows or [],
        "git_head": "synthetic",
    }
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    with (directory / "cells.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return directory


def terrain_field(glyph_id: int = 0x23) -> list[dict]:
    """A plain terrain grid: no feature signature anywhere in it."""

    return [
        _row(x, y, glyph_id, "terrain")
        for y in range(GRID_ROWS)
        for x in range(GRID_COLS)
    ]


def shore_field(water_glyph: int = 0x7E) -> list[dict]:
    """Left half water, right half terrain, so column 11 is water touching land."""

    rows: list[dict] = []
    for y in range(GRID_ROWS):
        for x in range(GRID_COLS):
            if x < GRID_COLS // 2:
                rows.append(_row(x, y, water_glyph, "water"))
            else:
                rows.append(_row(x, y, 0x23, "terrain"))
    return rows


def _index(rows: list[dict]) -> dict[tuple[int, int], dict]:
    return {(r["screen_cell"]["x"], r["screen_cell"]["y"]): r for r in rows}


def paint(
    rows: list[dict],
    x: int,
    y: int,
    glyph_id: int,
    fg_rgb: list[float] | None = None,
) -> None:
    buffer = _index(rows)[(x, y)]["final_cell_paint_buffer"]
    buffer["glyph_id"] = glyph_id
    if fg_rgb is not None:
        buffer["fg_rgb"] = list(fg_rgb)


# Authored flower paint (a pink) and the defect colour (grass-coloured paint,
# the exact case where a flower is glyph-present and viewer-invisible).
FLOWER_PINK_FG = [0.90, 0.35, 0.62]
FLOWER_WHITE_FG = [0.94, 0.95, 0.93]
GRASS_FG = [0.32, 0.58, 0.20]
WATER_FG = [0.57, 0.70, 0.72]


def analyze_dir(directory: Path, **kwargs) -> dict:
    capture = TOOL.Capture()
    TOOL.discover_capture(directory, capture)
    return TOOL.analyze(capture, **kwargs)


def verdict(report: dict, feature: str) -> str:
    return report["features"][feature]["verdict"]


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------


def test_glyph_tables_match_the_shader_source():
    """The FL-4216 table is 8 directions x 4 stages and the waterfall reads the S row."""

    assert len(TOOL.FL4216_GLYPH_ID) == 32
    assert TOOL.WATERFALL_GLYPH_DIR == 4
    assert TOOL.WATERFALL_GLYPH_IDS == {665, 656, 660}
    assert TOOL.SNOW_SPARKLE_GLYPH_ID == 0xB7
    assert TOOL.SKY_CLOUD_BODY_GLYPH_IDS == {0xB0, 0xB1}


def test_red_verdict_vocabulary_is_declared_once_and_exported():
    """--list-registry must publish every verdict the analyzer can emit."""

    assert TOOL.V_PRESENT_WEAK in TOOL.RED_VERDICTS
    assert TOOL.V_PRESENT_WRONG_COLOR in TOOL.RED_VERDICTS
    assert TOOL.RED_VERDICTS <= set(TOOL.ALL_VERDICTS)
    payload = json.loads(_cli("--list-registry").stdout)
    assert payload["verdicts"]["all"] == list(TOOL.ALL_VERDICTS)
    assert set(payload["verdicts"]["red"]) == set(TOOL.RED_VERDICTS)


def test_every_registry_feature_has_a_capability_row():
    registry = {sig.name for sig in TOOL.FEATURE_REGISTRY}
    table = {row["feature"] for row in TOOL.CAPABILITY_TABLE}
    assert registry == table
    for row in TOOL.CAPABILITY_TABLE:
        assert row["cells_jsonl"] and row["frames_png"]


# ---------------------------------------------------------------------------
# shoreline_breaks
# ---------------------------------------------------------------------------


def test_shoreline_breaks_present(tmp_path):
    rows = shore_field()
    # Break glyphs on the water cells that touch land.
    for y in range(4, 10):
        paint(rows, GRID_COLS // 2 - 1, y, 653)
    report = analyze_dir(write_capture(tmp_path / "present", rows=rows))
    assert verdict(report, "shoreline_breaks") == TOOL.V_PRESENT
    assert report["features"]["shoreline_breaks"]["count"] == 6
    assert report["features"]["shoreline_breaks"]["evidence_locator"]


def test_shoreline_breaks_missing_when_water_is_in_frame(tmp_path):
    report = analyze_dir(write_capture(tmp_path / "absent", rows=shore_field()))
    assert verdict(report, "shoreline_breaks") == TOOL.V_MISSING
    assert report["features"]["shoreline_breaks"]["expected"] is True
    assert "shoreline_breaks" in report["summary"]["red"]


def test_shoreline_break_glyphs_far_from_land_do_not_count(tmp_path):
    """The adjacency filter is what separates a shore break from a waterfall."""

    rows = shore_field()
    for y in range(4, 10):
        paint(rows, 0, y, 653)  # deep water column, nowhere near terrain
    report = analyze_dir(write_capture(tmp_path / "deep", rows=rows))
    assert verdict(report, "shoreline_breaks") == TOOL.V_MISSING


# ---------------------------------------------------------------------------
# flowers
# ---------------------------------------------------------------------------


def test_flowers_present(tmp_path):
    rows = terrain_field()
    for x in range(3, 9):
        paint(rows, x, 5, 622, FLOWER_PINK_FG)
    report = analyze_dir(write_capture(tmp_path / "present", rows=rows))
    detail = report["features"]["flowers"]["detail"]
    assert verdict(report, "flowers") == TOOL.V_PRESENT
    assert report["features"]["flowers"]["count"] == 6
    assert detail["hue_family_counts"] == {"pink": 6}
    assert detail["hue_check"].startswith("PASS")


def test_flowers_present_with_the_white_family(tmp_path):
    """White flowers are a NEUTRAL colour, not a hue wedge; they must still pass."""

    rows = terrain_field()
    for x in range(3, 9):
        paint(rows, x, 5, 628, FLOWER_WHITE_FG)
    report = analyze_dir(write_capture(tmp_path / "white", rows=rows))
    assert verdict(report, "flowers") == TOOL.V_PRESENT
    assert report["features"]["flowers"]["detail"]["hue_family_counts"] == {"white": 6}


def test_flowers_painted_as_grass_are_present_wrong_color_and_red(tmp_path):
    """THE REGISTRY GAP: flower glyphs are present but painted grass-coloured.

    Glyph identity alone reported PRESENT and the gate stayed quiet while the
    flowers were invisible on screen. The hue check must turn this RED.
    """

    rows = terrain_field()
    for x in range(3, 12):
        paint(rows, x, 5, 622, GRASS_FG)
    report = analyze_dir(write_capture(tmp_path / "invisible", rows=rows))
    row = report["features"]["flowers"]
    assert row["verdict"] == TOOL.V_PRESENT_WRONG_COLOR
    assert row["count"] == 9
    assert row["detail"]["hue_matching_cells"] == 0
    assert row["detail"]["hue_family_counts"] == {"grass": 9}
    assert row["detail"]["hue_check"].startswith("FAIL")
    assert "flowers" in report["summary"]["red"]


def test_flowers_below_the_hue_count_floor_are_present_wrong_color(tmp_path):
    """One correctly coloured flower among twenty grass-coloured ones is not paint."""

    rows = terrain_field()
    for x in range(2, 22):
        paint(rows, x, 5, 622, GRASS_FG)
    paint(rows, 2, 5, 622, FLOWER_PINK_FG)
    report = analyze_dir(write_capture(tmp_path / "one_pink", rows=rows))
    detail = report["features"]["flowers"]["detail"]
    assert verdict(report, "flowers") == TOOL.V_PRESENT_WRONG_COLOR
    assert detail["hue_matching_cells"] == 1
    assert detail["hue_floor_applied"] == 3


def test_flowers_hue_check_reports_itself_skipped_when_a_dump_has_no_fg_rgb(tmp_path):
    """Capability honesty: no fg colour means the check was NOT performed."""

    rows = terrain_field()
    for x in range(3, 9):
        paint(rows, x, 5, 622)
    for row in rows:
        row["final_cell_paint_buffer"].pop("fg_rgb", None)
    report = analyze_dir(write_capture(tmp_path / "no_color", rows=rows))
    detail = report["features"]["flowers"]["detail"]
    assert verdict(report, "flowers") == TOOL.V_PRESENT
    assert detail["cells_with_fg_rgb"] == 0
    assert detail["hue_check"].startswith("SKIPPED")


def test_hue_family_classifier_covers_the_authored_palettes():
    assert TOOL._hue_family(tuple(GRASS_FG)) == "grass"
    assert TOOL._hue_family(tuple(FLOWER_PINK_FG)) == "pink"
    assert TOOL._hue_family(tuple(FLOWER_WHITE_FG)) == "white"
    assert TOOL._hue_family((0.95, 0.90, 0.20)) == "yellow"
    assert TOOL._hue_family((0.25, 0.40, 0.92)) == "blue"
    assert TOOL._hue_family((0.02, 0.03, 0.02)) == "dark"
    assert TOOL.FLOWER_HUE_FAMILY_ALLOWLIST == {
        "yellow",
        "blue",
        "purple",
        "pink",
        "white",
    }


def test_flowers_missing(tmp_path):
    report = analyze_dir(write_capture(tmp_path / "absent", rows=terrain_field()))
    assert verdict(report, "flowers") == TOOL.V_MISSING
    assert "flowers" in report["summary"]["red"]


def test_flowers_gated_off_when_foliage_cards_are_disabled(tmp_path):
    capture_dir = write_capture(
        tmp_path / "gated", rows=terrain_field(), feature_state={"foliage_cards": False}
    )
    report = analyze_dir(capture_dir)
    assert verdict(report, "flowers") == TOOL.V_GATED_OFF
    assert report["summary"]["red"] == []


# ---------------------------------------------------------------------------
# waterfall
# ---------------------------------------------------------------------------


def test_waterfall_present_as_a_vertical_run(tmp_path):
    rows = shore_field()
    for y in range(2, 12):
        paint(rows, 3, y, 665)
    report = analyze_dir(
        write_capture(tmp_path / "present", rows=rows), expect=["waterfall"]
    )
    assert verdict(report, "waterfall") == TOOL.V_PRESENT
    assert report["features"]["waterfall"]["count"] == 1


def test_waterfall_missing_when_expected(tmp_path):
    report = analyze_dir(
        write_capture(tmp_path / "absent", rows=shore_field()), expect=["waterfall"]
    )
    assert verdict(report, "waterfall") == TOOL.V_MISSING
    assert "waterfall" in report["summary"]["red"]


def test_waterfall_scattered_glyphs_are_not_a_ribbon(tmp_path):
    """Two isolated band glyphs are not a fall; the run length is the signature."""

    rows = shore_field()
    paint(rows, 3, 2, 665)
    paint(rows, 7, 9, 656)
    report = analyze_dir(
        write_capture(tmp_path / "scatter", rows=rows), expect=["waterfall"]
    )
    assert verdict(report, "waterfall") == TOOL.V_MISSING


def test_waterfall_gate_unknown_without_an_expect_override(tmp_path):
    """A world-anchored site that may be out of frame must not fake a regression."""

    report = analyze_dir(write_capture(tmp_path / "unknown", rows=shore_field()))
    assert verdict(report, "waterfall") == TOOL.V_GATE_UNKNOWN
    assert "waterfall" not in report["summary"]["red"]


# ---------------------------------------------------------------------------
# water_activity
# ---------------------------------------------------------------------------


def test_water_activity_present(tmp_path):
    report = analyze_dir(write_capture(tmp_path / "present", rows=shore_field()))
    assert verdict(report, "water_activity") == TOOL.V_PRESENT
    assert report["features"]["water_activity"]["count"] > 0


def test_water_activity_missing_when_water_paints_no_activity_glyph(tmp_path):
    rows = shore_field(water_glyph=0x20)  # blank water: the animated family is gone
    report = analyze_dir(write_capture(tmp_path / "absent", rows=rows))
    assert verdict(report, "water_activity") == TOOL.V_MISSING


def test_water_activity_phase_frozen_between_two_captures(tmp_path):
    """Two identical captures mean the animation phase never advanced."""

    first = write_capture(tmp_path / "t0", rows=shore_field())
    second = write_capture(tmp_path / "t1", rows=shore_field())
    capture = TOOL.Capture()
    TOOL.discover_capture(first, capture)
    TOOL.discover_capture(second, capture)
    report = TOOL.analyze(capture)
    assert verdict(report, "water_activity") == TOOL.V_PRESENT_STATIC
    assert "water_activity" in report["summary"]["red"]


def test_water_activity_phase_varies_between_two_captures(tmp_path):
    rows_a = shore_field(water_glyph=0x7E)
    rows_b = shore_field(water_glyph=0x3D)
    first = write_capture(tmp_path / "t0", rows=rows_a)
    second = write_capture(tmp_path / "t1", rows=rows_b)
    capture = TOOL.Capture()
    TOOL.discover_capture(first, capture)
    TOOL.discover_capture(second, capture)
    report = TOOL.analyze(capture)
    assert verdict(report, "water_activity") == TOOL.V_PRESENT


# ---------------------------------------------------------------------------
# snow_sparkle: the expected-ABSENT direction
# ---------------------------------------------------------------------------


def test_snow_sparkle_gated_off_in_clear_weather(tmp_path):
    report = analyze_dir(write_capture(tmp_path / "clear", rows=terrain_field()))
    assert verdict(report, "snow_sparkle") == TOOL.V_GATED_OFF
    assert report["features"]["snow_sparkle"]["expected"] is False


def test_snow_sparkle_reappearing_in_clear_weather_is_red(tmp_path):
    rows = terrain_field()
    for x in range(2, 6):
        paint(rows, x, 3, 0xB7)
    report = analyze_dir(write_capture(tmp_path / "reappear", rows=rows))
    assert verdict(report, "snow_sparkle") == TOOL.V_UNEXPECTED_PRESENT
    assert "snow_sparkle" in report["summary"]["red"]


def test_snow_sparkle_present_when_snow_weather_is_on(tmp_path):
    rows = terrain_field()
    for x in range(2, 6):
        paint(rows, x, 3, 0xB7)
    capture_dir = write_capture(
        tmp_path / "snow", rows=rows, feature_state={"weather_kind_name": "snow"}
    )
    report = analyze_dir(capture_dir)
    assert verdict(report, "snow_sparkle") == TOOL.V_PRESENT


def test_snow_sparkle_missing_when_snow_weather_is_on(tmp_path):
    capture_dir = write_capture(
        tmp_path / "snow_gone",
        rows=terrain_field(),
        feature_state={"weather_kind_name": "snow"},
    )
    report = analyze_dir(capture_dir)
    assert verdict(report, "snow_sparkle") == TOOL.V_MISSING


# ---------------------------------------------------------------------------
# rain_glyphs, birds, actor_submerge, sky_cloud_bodies
# ---------------------------------------------------------------------------


def test_rain_glyphs_gated_off_and_unexpected_present(tmp_path):
    clear = analyze_dir(write_capture(tmp_path / "clear", rows=terrain_field()))
    assert verdict(clear, "rain_glyphs") == TOOL.V_GATED_OFF

    rows = terrain_field()
    for y in range(2, 8):
        paint(rows, 4, y, 0xB3)
    wet = analyze_dir(write_capture(tmp_path / "wet", rows=rows))
    assert verdict(wet, "rain_glyphs") == TOOL.V_UNEXPECTED_PRESENT
    assert "rain_glyphs" in wet["summary"]["red"]


def test_birds_gated_off_then_missing_then_present(tmp_path):
    off = analyze_dir(write_capture(tmp_path / "off", rows=terrain_field()))
    assert verdict(off, "birds") == TOOL.V_GATED_OFF

    on = analyze_dir(
        write_capture(
            tmp_path / "on", rows=terrain_field(), feature_state={"ascii_birds": True}
        )
    )
    assert verdict(on, "birds") == TOOL.V_MISSING

    rows = terrain_field()
    _index(rows)[(5, 5)]["dynamic_mover_owner_kind"] = "bird_face"
    flying = analyze_dir(
        write_capture(
            tmp_path / "flying", rows=rows, feature_state={"ascii_birds": True}
        )
    )
    assert verdict(flying, "birds") == TOOL.V_PRESENT


def test_actor_submerge_expected_only_when_the_actor_touches_water(tmp_path):
    dry = analyze_dir(write_capture(tmp_path / "dry", rows=shore_field()))
    assert verdict(dry, "actor_submerge") == TOOL.V_GATED_OFF

    wading = analyze_dir(
        write_capture(
            tmp_path / "wading",
            rows=shore_field(),
            actor_rows=[{"actor_id": 1, "actor_water_contact": True}],
        )
    )
    assert verdict(wading, "actor_submerge") == TOOL.V_MISSING
    assert "actor_submerge" in wading["summary"]["red"]

    rows = shore_field()
    _index(rows)[(4, 4)]["cell_owner_waterline_clip"] = 0.5
    sinking = analyze_dir(
        write_capture(
            tmp_path / "sinking",
            rows=rows,
            actor_rows=[{"actor_id": 1, "actor_water_contact": True}],
        )
    )
    assert verdict(sinking, "actor_submerge") == TOOL.V_PRESENT


def test_sky_cloud_bodies_present_when_the_dump_labels_sky_cells(tmp_path):
    rows = terrain_field()
    for x in range(GRID_COLS):
        for y in range(3):
            _index(rows)[(x, y)]["final_cell_paint_buffer"]["owner_kind"] = "sky"
    for x in range(6, 12):
        paint(rows, x, 1, 0xB0)
    present = analyze_dir(write_capture(tmp_path / "sky", rows=rows))
    assert verdict(present, "sky_cloud_bodies") == TOOL.V_PRESENT

    blank = terrain_field()
    for x in range(GRID_COLS):
        for y in range(3):
            _index(blank)[(x, y)]["final_cell_paint_buffer"]["owner_kind"] = "sky"
    absent = analyze_dir(write_capture(tmp_path / "sky_blank", rows=blank))
    assert verdict(absent, "sky_cloud_bodies") == TOOL.V_MISSING


def test_snow_fall_glyph_collision_is_reported_but_never_red(tmp_path):
    """The falling-snow family is plain ASCII shared with terrain punctuation."""

    rows = terrain_field(glyph_id=0x2E)
    report = analyze_dir(write_capture(tmp_path / "punct", rows=rows))
    assert verdict(report, "snow_fall_glyphs") == TOOL.V_UNEXPECTED_PRESENT
    assert report["features"]["snow_fall_glyphs"]["red_eligible"] is False
    assert "snow_fall_glyphs" not in report["summary"]["red"]


# ---------------------------------------------------------------------------
# cloud_shadow_drift (temporal, PNG)
# ---------------------------------------------------------------------------


# The drift fixtures carry metadata.json (for the gate) but no cells.jsonl, so
# the analyzer falls back to its default 180x113 analysis grid. PX_PER_CELL keeps
# the synthetic pixel frames and that grid in a stable, readable relation.
DRIFT_COLS, DRIFT_ROWS = 180, 113
PX_PER_CELL = 8


def _blob_field(blob_scale_cells: int, seed: int = 7):
    """A seeded low-frequency RANDOM blob field at pixel resolution.

    Random, not a sine: a periodic pattern would correlate equally well at
    several shifts and make the moving/static split meaningless.
    """

    numpy = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    width, height = DRIFT_COLS * PX_PER_CELL, DRIFT_ROWS * PX_PER_CELL
    rng = numpy.random.default_rng(seed)
    coarse = rng.random(
        (
            max(2, DRIFT_ROWS // blob_scale_cells),
            max(2, DRIFT_COLS // blob_scale_cells),
        )
    ).astype("float32")
    field = (
        numpy.asarray(
            Image.fromarray((coarse * 255).astype("uint8")).resize(
                (width, height), Image.BICUBIC
            ),
            dtype="float32",
        )
        / 255.0
    )
    return field - float(field.mean())


def _cell_texture(amplitude: float, seed: int = 99):
    """SCREEN-STATIC per-cell relief/material texture at one-cell scale.

    This is the structure the first band-pass implementation accidentally kept:
    it never moves with the camera-locked grid, so a detector that consumes it
    reports a (0,0) correlation peak no matter how fast the clouds advance.
    """

    numpy = pytest.importorskip("numpy")
    rng = numpy.random.default_rng(seed)
    per_cell = rng.random((DRIFT_ROWS, DRIFT_COLS)).astype("float32") - 0.5
    return (
        numpy.repeat(numpy.repeat(per_cell, PX_PER_CELL, axis=0), PX_PER_CELL, axis=1)
        * amplitude
    )


def _write_frames(directory: Path, fields, *, feature_state: dict | None = None) -> Path:
    """Write an ordered list of float luma fields as sidecar-backed PNG frames."""

    numpy = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    directory.mkdir(parents=True, exist_ok=True)
    for index, field in enumerate(fields):
        frame = numpy.clip(field, 0.0, 1.0) * 255.0
        path = directory / f"f{index}.png"
        Image.fromarray(frame.astype("uint8")).convert("RGB").save(path)
        path.with_name(path.name + ".pixel_camera.json").write_text(
            json.dumps(
                {"frame_index": float(index * 60), "frame_time_seconds": index * 1.0}
            ),
            encoding="utf-8",
        )
    state = dict(BASE_FEATURE_STATE)
    state.update(feature_state or {})
    (directory / "metadata.json").write_text(
        json.dumps(
            {"feature_state": state, "weather_kind_name": state["weather_kind_name"]}
        ),
        encoding="utf-8",
    )
    return directory


def _write_shadow_frames(
    directory: Path,
    *,
    shift_cells: int,
    blotches: bool,
    blob_scale_cells: int = 4,
    texture_amplitude: float = 0.0,
    feature_state: dict | None = None,
) -> Path:
    """Two frames of a lit terrain band, optionally with a drifting shadow field."""

    numpy = pytest.importorskip("numpy")
    width, height = DRIFT_COLS * PX_PER_CELL, DRIFT_ROWS * PX_PER_CELL
    if blotches:
        blob = _blob_field(blob_scale_cells)
    else:
        blob = numpy.zeros((height, width), dtype="float32")
    texture = _cell_texture(texture_amplitude) if texture_amplitude else 0.0
    fields = [
        0.50
        - 0.30 * numpy.roll(blob, index * shift_cells * PX_PER_CELL, axis=1)
        + texture
        for index in range(2)
    ]
    return _write_frames(directory, fields, feature_state=feature_state)


def test_cloud_shadow_drift_present_moving(tmp_path):
    directory = _write_shadow_frames(tmp_path / "moving", shift_cells=3, blotches=True)
    report = analyze_dir(directory)
    detail = report["features"]["cloud_shadow_drift"]["detail"]
    assert verdict(report, "cloud_shadow_drift") == TOOL.V_PRESENT_MOVING
    assert detail["best_shift_cells"] == [3, 0]
    assert "cloud_shadow_drift" not in report["summary"]["red"]


def test_cloud_shadow_drift_moving_low_frequency_field_under_static_texture(tmp_path):
    """REGRESSION for the band-pass polarity defect.

    The first ``_band_pass`` subtracted the 10x-downsampled base and returned the
    RESIDUAL, so it kept screen-locked one-cell texture and discarded the entire
    cloud-scale band. On exactly this input it reported a (0,0) peak. The
    corrected difference-of-low-pass band must recover the true 3-cell shift.
    """

    directory = _write_shadow_frames(
        tmp_path / "band_polarity",
        shift_cells=3,
        blotches=True,
        blob_scale_cells=8,
        texture_amplitude=0.35,
    )
    report = analyze_dir(directory)
    detail = report["features"]["cloud_shadow_drift"]["detail"]
    assert verdict(report, "cloud_shadow_drift") == TOOL.V_PRESENT_MOVING
    assert detail["best_shift_cells"] == [3, 0]
    assert "cloud_shadow_drift" not in report["summary"]["red"]

    # Falsifier: run the SAME frames through the old high-pass polarity and show
    # it is the filter, not the fixture, that used to lose the drift.
    frames = sorted(directory.glob("f*.png"))
    grids = [TOOL._frame_luma_grid(path, DRIFT_COLS, DRIFT_ROWS) for path in frames]
    horizon = detail["horizon_row"]
    old_band = [TOOL._detail_high_pass(grid, 10)[horizon:, :] for grid in grids]
    old_shift, _ = TOOL._best_shift(old_band[0], old_band[1], 6)
    assert old_shift == (0, 0), (
        "the old high-pass polarity must still lock onto the static texture; "
        "if it does not, this fixture no longer demonstrates the defect"
    )
    new_band = [TOOL._cloud_band_pass(grid)[horizon:, :] for grid in grids]
    new_shift, _ = TOOL._best_shift(new_band[0], new_band[1], 6)
    assert new_shift == (3, 0)


def test_cloud_shadow_drift_identical_frames_are_present_weak_not_static(tmp_path):
    """Two identical frames cannot separate "frozen" from "never contributed".

    Run identity: scene=synthetic_feature_presence, pose=cloud_shadow_frozen,
    time=two_identical_fixture_frames, source_commit=fixture_source,
    receipt=analysis_summary_json.

    The band still carries contrast, because static terrain relief lives in the
    same spatial band, but the TEMPORAL DELTA energy is exactly zero. The old
    single-number verdict called this PRESENT_STATIC, which over-claims an
    existing-but-frozen cloud field. It is RED either way, but the honest
    verdict is PRESENT_WEAK.
    """

    directory = _write_shadow_frames(tmp_path / "frozen", shift_cells=0, blotches=True)
    report = analyze_dir(directory)
    detail = report["features"]["cloud_shadow_drift"]["detail"]
    assert verdict(report, "cloud_shadow_drift") == TOOL.V_PRESENT_WEAK
    assert detail["band_pass_contrast"] >= detail["contrast_threshold"]
    assert detail["temporal_delta_energy"] == 0.0
    assert "cloud_shadow_drift" in report["summary"]["red"]


def test_cloud_shadow_drift_present_static_when_the_field_changes_without_advancing(
    tmp_path,
):
    """Delta energy passes but the correlation peak stays at (0,0).

    The blotch field breathes in intensity at an unchanging screen position.
    Something demonstrably changed, so this is not the undecidable PRESENT_WEAK
    case; the field simply never advances, which is the real drift regression.
    """

    blob = _blob_field(4)
    fields = [0.50 - 0.30 * blob, 0.50 - 0.30 * 0.55 * blob]
    directory = _write_frames(tmp_path / "breathing", fields)
    report = analyze_dir(directory)
    detail = report["features"]["cloud_shadow_drift"]["detail"]
    assert verdict(report, "cloud_shadow_drift") == TOOL.V_PRESENT_STATIC
    assert detail["best_shift_cells"] == [0, 0]
    assert detail["temporal_delta_energy"] >= detail["delta_energy_threshold"]
    assert "cloud_shadow_drift" in report["summary"]["red"]


def test_cloud_shadow_drift_reports_contrast_and_delta_energy_separately(tmp_path):
    """The decomposition must be visible as data, not folded into one verdict.

    Run identity: scene=synthetic_feature_presence, pose=cloud_shadow_decomposed,
    time=two_shifted_fixture_frames, source_commit=fixture_source,
    receipt=analysis_summary_json.
    """

    directory = _write_shadow_frames(
        tmp_path / "decomposed", shift_cells=3, blotches=True
    )
    detail = analyze_dir(directory)["features"]["cloud_shadow_drift"]["detail"]
    for key in (
        "band_pass_contrast",
        "contrast_threshold",
        "temporal_delta_energy",
        "delta_energy_threshold",
        "delta_over_contrast_ratio",
        "band_fine_cells",
        "band_coarse_cells",
    ):
        assert key in detail, key
    assert detail["band_fine_cells"] == TOOL.CLOUD_BAND_FINE_CELLS
    assert detail["temporal_delta_energy"] > 0.0


def test_cloud_shadow_drift_missing_when_no_shadow_field_exists(tmp_path):
    directory = _write_shadow_frames(tmp_path / "flat", shift_cells=3, blotches=False)
    report = analyze_dir(directory)
    assert verdict(report, "cloud_shadow_drift") == TOOL.V_MISSING
    assert "cloud_shadow_drift" in report["summary"]["red"]


def test_cloud_shadow_drift_gated_off_at_night(tmp_path):
    directory = _write_shadow_frames(
        tmp_path / "night", shift_cells=3, blotches=False, feature_state={"night": True}
    )
    report = analyze_dir(directory)
    assert verdict(report, "cloud_shadow_drift") == TOOL.V_GATED_OFF


def test_derived_crops_are_excluded_from_the_temporal_pair(tmp_path):
    directory = _write_shadow_frames(tmp_path / "crops", shift_cells=3, blotches=True)
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (40, 40), (255, 255, 255)).save(directory / "hud_crop.png")
    report = analyze_dir(directory)
    frame_names = [Path(f["path"]).name for f in report["capture"]["frames"]]
    assert "hud_crop.png" not in frame_names
    assert verdict(report, "cloud_shadow_drift") == TOOL.V_PRESENT_MOVING


# ---------------------------------------------------------------------------
# Report shape and the --strict proof-gate hook
# ---------------------------------------------------------------------------


def test_report_is_machine_parseable_with_the_required_per_feature_fields(tmp_path):
    report = analyze_dir(write_capture(tmp_path / "shape", rows=shore_field()))
    assert report["schema"] == TOOL.REPORT_SCHEMA
    assert report["scope"] == "intake-only"
    assert report["complete_feature_coverage"] is False
    assert report["complete_coverage_owner"] == "scripts/godot_feature_regression_gate.py"
    json.dumps(report)  # must round-trip without a custom encoder
    assert set(report["features"]) == {sig.name for sig in TOOL.FEATURE_REGISTRY}
    for name, row in report["features"].items():
        assert row["verdict"], name
        assert isinstance(row["count"], int), name
        assert row["expected"] in (True, False, None), name
        assert isinstance(row["evidence_locator"], list), name
        assert row["signature_provenance"], name
    assert report["summary"]["registry_size"] == len(TOOL.FEATURE_REGISTRY)


def test_insufficient_input_when_the_capture_has_no_cells_or_frames(tmp_path):
    directory = tmp_path / "empty"
    directory.mkdir()
    (directory / "metadata.json").write_text(
        json.dumps({"feature_state": dict(BASE_FEATURE_STATE)}), encoding="utf-8"
    )
    report = analyze_dir(directory)
    assert verdict(report, "shoreline_breaks") == TOOL.V_INSUFFICIENT_INPUT
    assert report["summary"]["red"] == []

    out = tmp_path / "unforced.json"
    result = _cli("--capture", str(directory), "--out", str(out), "--strict")
    assert result.returncode == 0, result.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["features"]["shoreline_breaks"]["verdict"] == TOOL.V_INSUFFICIENT_INPUT
    assert payload["summary"]["red"] == []


def test_strict_mode_fails_when_forced_feature_input_is_insufficient(tmp_path):
    directory = tmp_path / "empty_expected"
    directory.mkdir()
    (directory / "metadata.json").write_text(
        json.dumps({"feature_state": dict(BASE_FEATURE_STATE)}), encoding="utf-8"
    )
    out = tmp_path / "forced.json"
    result = _cli(
        "--capture",
        str(directory),
        "--expect",
        "shoreline_breaks",
        "--out",
        str(out),
        "--strict",
    )
    assert result.returncode == 2
    assert "STRICT FAIL: shoreline_breaks" in result.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    row = payload["features"]["shoreline_breaks"]
    assert row["verdict"] == TOOL.V_INSUFFICIENT_INPUT
    assert row["forced_expectation"] is True
    assert payload["summary"]["red"] == ["shoreline_breaks"]


def test_strict_mode_fails_when_forced_signature_is_missing(tmp_path):
    directory = write_capture(tmp_path / "missing_expected", rows=terrain_field())
    out = tmp_path / "missing_forced.json"
    result = _cli(
        "--capture",
        str(directory),
        "--expect",
        "snow_fall_glyphs",
        "--out",
        str(out),
        "--strict",
    )
    assert result.returncode == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    row = payload["features"]["snow_fall_glyphs"]
    assert row["verdict"] == TOOL.V_MISSING
    assert row["forced_expectation"] is True
    assert "snow_fall_glyphs" in payload["summary"]["red"]


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL_PATH), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def test_strict_mode_exits_non_zero_on_a_missing_feature(tmp_path):
    directory = write_capture(tmp_path / "regressed", rows=shore_field())
    out = tmp_path / "report.json"
    result = _cli("--capture", str(directory), "--out", str(out), "--strict")
    assert result.returncode == 2
    assert "STRICT FAIL" in result.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "shoreline_breaks" in payload["summary"]["red"]


def test_strict_mode_exits_zero_when_every_expected_feature_is_present(tmp_path):
    rows = shore_field()
    for y in range(4, 10):
        paint(rows, GRID_COLS // 2 - 1, y, 653)
    for x in range(GRID_COLS // 2, GRID_COLS // 2 + 5):
        paint(rows, x, 5, 622, FLOWER_PINK_FG)
    directory = write_capture(tmp_path / "healthy", rows=rows)
    out = tmp_path / "report.json"
    result = _cli("--capture", str(directory), "--out", str(out), "--strict")
    assert result.returncode == 0, result.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["red"] == []


def test_list_registry_emits_the_capability_table():
    result = _cli("--list-registry")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["scope"] == "intake-only"
    assert payload["complete_feature_coverage"] is False
    assert payload["complete_coverage_owner"] == "scripts/godot_feature_regression_gate.py"
    assert len(payload["features"]) == len(TOOL.FEATURE_REGISTRY)
    assert len(payload["capability_table"]) == len(TOOL.CAPABILITY_TABLE)


def test_unknown_expect_name_is_rejected():
    result = _cli("--list-registry", "--expect", "not_a_feature")
    assert result.returncode != 0
    assert "unknown feature" in result.stderr
