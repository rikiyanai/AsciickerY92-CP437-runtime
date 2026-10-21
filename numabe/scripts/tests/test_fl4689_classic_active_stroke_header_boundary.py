from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_active_stroke_owner_header_is_nonclassic_only() -> None:
    source = read("engine/render/render_internal.h")
    include = '#include "../../apps/fl4512-runtime/engine/fl4512_active_stroke_owner.h"'
    include_pos = source.index(include)
    guard = "#if !defined(ASCIICKER_RENDERER_CLASSIC_CP437)"
    assert source.rfind(guard, 0, include_pos) > source.rfind("#endif", 0, include_pos)


def test_classic_renderer_does_not_embed_active_stroke_owner_state() -> None:
    source = read("engine/render/render_internal.h")
    guard = "#if !defined(ASCIICKER_RENDERER_CLASSIC_CP437)"
    fields = (
        "Fl4512ActiveStrokeSnakeStoreV2* fl4512_active_stroke_store",
        "Fl4512SpacetimeTrackStoreV1 fl4512_spacetime_track_store",
        "Fl4512ActiveStrokeSnakeFrameObservationV2",
        "Fl4512ActiveStrokeSnakeLifecycleObservationV2",
        "Fl4512ActiveStrokeSnakeEventV2* fl4512_active_stroke_events",
    )
    for field in fields:
        pos = source.index(field)
        assert source.rfind(guard, 0, pos) > source.rfind("#endif", 0, pos)


def test_active_stroke_runtime_helpers_are_nonclassic_only() -> None:
    world = read("engine/render/render_world_pass.cpp")
    guard = "#if !defined(ASCIICKER_RENDERER_CLASSIC_CP437)"
    helpers = (
        "static void Fl4512CaptureActiveStrokeReprojectionV2",
        "static bool Fl4512ActiveStrokeAdmissionForFrameV2",
        "static bool Fl4512ReprojectActiveStrokeAnchorV2",
        "static void Fl4684AccumulateActiveStrokeReachabilityV1",
        "static void Fl4684EmitActiveStrokeWedgeTraceV1",
    )
    for helper in helpers:
        pos = world.index(helper)
        assert world.rfind(guard, 0, pos) > world.rfind("#endif", 0, pos)


def test_classic_depfiles_exclude_active_stroke_and_epoch_headers() -> None:
    depdir = ROOT / ".d_game_classic"
    if not depdir.exists():
        return
    dep_text = "\n".join(
        path.read_text(errors="ignore") for path in depdir.rglob("*.d")
    )
    assert "fl4512_active_stroke_owner.h" not in dep_text
    assert "fl4693_runtime_epoch.h" not in dep_text
