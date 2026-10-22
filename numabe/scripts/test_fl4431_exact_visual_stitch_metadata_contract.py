#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "adhoc" / "2026-06-23-FL-4431-June-14-visual-FPS-AB-harness.py"
spec = importlib.util.spec_from_file_location("fl4431_ab_harness", HARNESS)
harness = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(harness)


def _write_panel(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (96, 64), (20, 30, 50))
    draw = ImageDraw.Draw(image)
    for x in range(0, 96, 8):
        draw.rectangle((x, 32, x + 5, 63), fill=(80 + x, 120, 55))
    for y in range(8, 58, 8):
        draw.line((0, y, 95, 63 - y // 2), fill=(220, 180 - y, 90), width=2)
    image.save(path)


def _write_metadata(path: Path) -> None:
    path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(
            {
                "actual_godot_capture": True,
                "proof_state": "actual_godot_capture",
                "scene_content_sentinel": {
                    "passed": True,
                    "unique_sampled_colors": 48,
                    "luma_range": 91,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _panel_paths(out_dir: Path) -> list[Path]:
    return harness._expected_panel_paths(out_dir, ["foliage_grass_land"])


def test_stitch_rejects_png_without_metadata(tmp_path: Path) -> None:
    for path in _panel_paths(tmp_path):
        _write_panel(path)

    try:
        harness._stitch_exact_visual_grid(tmp_path, ["foliage_grass_land"], False)
    except SystemExit as exc:
        assert "missing panel metadata" in str(exc)
    else:
        raise AssertionError("stitcher accepted PNG-only panels")


def test_stitch_requires_scene_content_sentinel_pass(tmp_path: Path) -> None:
    paths = _panel_paths(tmp_path)
    for path in paths:
        _write_panel(path)
        _write_metadata(path)
    bad_meta = paths[0].with_suffix(paths[0].suffix + ".json")
    metadata = json.loads(bad_meta.read_text(encoding="utf-8"))
    metadata["scene_content_sentinel"]["passed"] = False
    bad_meta.write_text(json.dumps(metadata) + "\n", encoding="utf-8")

    try:
        harness._stitch_exact_visual_grid(tmp_path, ["foliage_grass_land"], False)
    except SystemExit as exc:
        assert "scene-content sentinel failed" in str(exc)
    else:
        raise AssertionError("stitcher accepted failed panel sentinel")


def test_stitch_accepts_panels_with_verified_metadata(tmp_path: Path) -> None:
	for path in _panel_paths(tmp_path):
		_write_panel(path)
		_write_metadata(path)

	grid = harness._stitch_exact_visual_grid(tmp_path, ["foliage_grass_land"], False)

	assert grid is not None
	assert Path(grid).exists()


def test_perf_trace_parser_keeps_frame_and_readback_attribution(tmp_path: Path) -> None:
	log = tmp_path / "godot.log"
	log.write_text(
		"\n".join(
			[
				"PERF_TRACE frame=1 fps=42.0 process_ms=24.0 render_cpu_ms=2.0 render_gpu_ms=0.0 draws=12 objs=34 vmem_mb=512.0 | percall_ms mesh=0.1 actors=2.8 env=0.2 blood=0.0 input=0.0 misc=0.1 tail=0.0",
				"PERF_FRAME_TRACE frames=30 internal=3600x2176 avg_wall_ms=5.500 views=1 actor_source_publish_ms=1.250 cell_owner_product_ms=2.500 ascii_compositor_ms=1.750 jitter_readback_write_ms=0.500",
				"PERF_READBACK_TRACE source=PixelCameraViewport texture_ms=0.100 get_image_ms=3.200 normalize_ms=0.050 total_ms=3.350",
				"PERF_READBACK_TRACE source=FL4359Jitter cells=19620 product_get_data_ms=1.000 final_get_data_ms=0.900 serialize_ms=12.000 file_write_ms=4.000 total_ms=17.900",
			]
		)
		+ "\n",
		encoding="utf-8",
	)

	frame_rows = harness._parse_frame_trace(log)
	readback = harness._parse_readback_trace(log)

	assert frame_rows[0]["actor_source_publish_ms"] == 1.25
	assert frame_rows[0]["cell_owner_product_ms"] == 2.5
	assert frame_rows[0]["ascii_compositor_ms"] == 1.75
	assert readback["PixelCameraViewport"]["get_image_ms"]["avg"] == 3.2
	assert readback["FL4359Jitter"]["serialize_ms"]["avg"] == 12.0
