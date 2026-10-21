from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]

UNICODE_ONLY_FRAME_INPUT_TOKENS = (
    "GlyphResolverCellInput",
    "GlyphResolverCellResult",
    "HarriGpuParityBuffers",
    "Fl4512SpacetimeTrackStoreV1",
    "Fl4512ActiveStrokeSnakeEventV2",
    "Fl4512MeshBundleBuildStateV1",
    "Fl4512ProfileNamespaceV1",
    "ProfileKeyV1",
    "Fl4512SelectedRoleV1",
    "Fl4512FinalResultKindV1",
    "Fl4512PoolAddressV1",
    "Fl4512PoolRouteKeyV1",
    "Fl4512AbsenceReasonV1",
    "Fl4512DescriptorKindV1",
    "Fl4512ScorerKindV1",
    "FinalGlyphResultV1",
    "resolve_glyph_shape6",
    "observe_glyph_shape6",
)


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_sprite_composition_contract_has_renderer_neutral_names() -> None:
    header = read("engine/render/render_frame_input.h")
    sprite = read("engine/render/render_sprite_blit.cpp")

    assert "enum RenderPresentationFieldOpV1" in header
    assert "RENDER_PRESENTATION_FIELD_UNKNOWN = 0" in header
    assert "RENDER_PRESENTATION_FIELD_LIGHTEN_BASE_BK = 9" in header
    assert "Fl4512PresentationFieldOpV1" not in header
    assert "FL4512_PRESENTATION_FIELD_" not in header
    assert "Fl4512RecordSpritePresentationDependency" not in sprite
    assert "RecordSpritePresentationDependency" in sprite


def test_classic_preprocessed_frame_input_excludes_unicode_resolver_abi() -> None:
    result = subprocess.run(
        (
            "c++",
            "-std=c++20",
            "-DASCIICKER_RENDERER_CLASSIC_CP437=1",
            "-I.",
            "-E",
            "-P",
            "-x",
            "c++",
            "-",
        ),
        cwd=ROOT,
        input='#include "engine/render/render_frame_input.h"\n',
        text=True,
        capture_output=True,
        check=True,
    )

    for token in UNICODE_ONLY_FRAME_INPUT_TOKENS:
        assert token not in result.stdout


def test_classic_game_bridge_does_not_construct_unicode_resolver_owner() -> None:
    source = read("engine/game_render_bridge.cpp")
    owner = "void (*g_runtime_resolve_glyph_shape6_hook)"
    first_owner = source.index(owner)
    guard = source.rfind(
        "#if !defined(ASCIICKER_RENDERER_CLASSIC_CP437)", 0, first_owner
    )
    guard_end = source.index("#endif", first_owner)

    assert guard >= 0
    assert guard < first_owner < guard_end
