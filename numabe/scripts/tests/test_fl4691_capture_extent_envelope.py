from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAX_WIDTH = 3840
MAX_HEIGHT = 2252
MAX_PIXELS = 3840 * 2160
AIRPLANE = (3600, 2252)
GROUND_TURN = (3840, 2160)
UNREQUESTED_CORNER = (3840, 2252)


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _admitted(size: tuple[int, int]) -> bool:
    width, height = size
    return (
        width > 0
        and height > 0
        and width <= MAX_WIDTH
        and height <= MAX_HEIGHT
        and width * height <= MAX_PIXELS
    )


def _canvas_observer_required(width: int, height: int) -> int:
    pixels = width * height
    engine_peak = 128 + 262_144 * (4 * 48) + 4_096 * (2 * 104) + pixels * (3 * 4)
    surface_bytes = pixels * 4
    retained_manifests = 5 * (262_144 * 48 + 4_096 * 104)
    return engine_peak + 15 * surface_bytes + retained_manifests + 128 * 1024 * 1024


def _tonemap_required(width: int, height: int) -> int:
    return width * height * 8 * 7


def test_two_canonical_shapes_are_admitted_without_rectangular_corner() -> None:
    assert _admitted(GROUND_TURN)
    assert _admitted(AIRPLANE)
    assert not _admitted(UNREQUESTED_CORNER)
    assert GROUND_TURN[0] * GROUND_TURN[1] == 8_294_400
    assert AIRPLANE[0] * AIRPLANE[1] == 8_107_200


def test_canvas_and_tonemap_shared_peak_is_bounded_for_both_routes() -> None:
    declared = 1280 * 1024 * 1024
    ground_canvas = _canvas_observer_required(*GROUND_TURN)
    airplane_canvas = _canvas_observer_required(*AIRPLANE)
    ground_tonemap = _tonemap_required(*GROUND_TURN)
    airplane_tonemap = _tonemap_required(*AIRPLANE)
    assert (ground_canvas, ground_tonemap) == (847_642_752, 464_486_400)
    assert (airplane_canvas, airplane_tonemap) == (834_164_352, 454_003_200)
    assert ground_canvas + ground_tonemap == 1_312_129_152 < declared
    assert airplane_canvas + airplane_tonemap == 1_288_167_552 < declared
    assert _canvas_observer_required(*UNREQUESTED_CORNER) + _tonemap_required(*UNREQUESTED_CORNER) > declared


def test_project_capture_owners_share_the_shape_envelope() -> None:
    observer = _source("godot_project/render/diagnostics/frame/NativeFrameObserver.gd")
    tonemap = _source("godot_project/render/diagnostics/capture/ToneMapInputCapture.gd")
    reader = _source("godot_project/render/diagnostics/provenance/CanvasProvenanceReader.gd")
    native_source = _source("godot_project/render/diagnostics/capture/NativeSourceCapture.gd")
    compositor = _source("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "const MAX_CAPTURE_TARGET_WIDTH := 3840" in observer
    assert "const MAX_CAPTURE_TARGET_PIXELS := 3840 * 2160" in observer
    assert "const MAX_WIDTH := 3840" in tonemap
    assert "const MAX_PIXELS := 3840 * 2160" in tonemap
    assert "const MAX_CAPTURE_TARGET_WIDTH := 3840" in reader
    assert "const MAX_CAPTURE_TARGET_PIXELS := 3840 * 2160" in reader
    assert "const MAX_CAPTURE_PIXELS := 3840 * 2160" in native_source
    assert "const FINAL_PIXEL_PAINT_MAX_TARGET_WIDTH := 3840" in compositor
    assert "const FINAL_PIXEL_PAINT_MAX_TARGET_PIXELS := 3840 * 2160" in compositor


def test_unsupported_shapes_are_rejected_before_per_pixel_allocations() -> None:
    observer = _source("godot_project/render/diagnostics/frame/NativeFrameObserver.gd")
    tonemap = _source("godot_project/render/diagnostics/capture/ToneMapInputCapture.gd")
    compositor = _source("godot_project/render/api/ASCIICompositorEffect.gd")
    assert observer.index("if not capture_extent_supported(_format.width, _format.height):") \
        < observer.index("var slot := device.texture_create(copy_format")
    assert tonemap.index("if not extent_supported(size.x, size.y):", tonemap.index("func queue(")) \
        < tonemap.index("if not _ensure_slots(size):")
    assert compositor.index("or target_size.x * target_size.y > FINAL_PIXEL_PAINT_MAX_TARGET_PIXELS") \
        < compositor.index("var slot_rd := rd.storage_buffer_create(required_bytes)")


def test_head_full_presentation_peak_covers_ground_turn() -> None:
    pixels = GROUND_TURN[0] * GROUND_TURN[1]
    assert pixels * 96 == 796_262_400
    assert pixels * 96 * 6 == 4_777_574_400


def test_head_native_source_peak_is_unchanged_by_the_raster_expansion() -> None:
    def required(pixel_count: int) -> int:
        capacity = min(1_048_576, pixel_count * 8, 384 * 384 * 18 * 2)
        return (16 + capacity * 144) * 7 + 384 * 384 * 18 * 2 * 4 + 32

    assert required(AIRPLANE[0] * AIRPLANE[1]) == 1_078_198_416
    assert required(GROUND_TURN[0] * GROUND_TURN[1]) == 1_078_198_416
