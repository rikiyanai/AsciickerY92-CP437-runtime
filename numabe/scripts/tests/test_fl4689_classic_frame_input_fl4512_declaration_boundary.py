from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
INCLUDE_FLAGS = ("-I.", "-Iengine", "-Iengine/render", "-Iplatform", "-Iserver")


def preprocess(path: str, *, classic: bool) -> str:
    command = ["c++", "-std=c++20"]
    if classic:
        command.append("-DASCIICKER_RENDERER_CLASSIC_CP437=1")
    command.extend((*INCLUDE_FLAGS, "-E", "-P", path))
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def test_classic_frame_input_has_no_fl4512_or_unicode_presentation_declarations() -> None:
    source = preprocess("engine/render/render_frame_input.h", classic=True)

    for token in (
        "Fl4512",
        "FL4512",
        "RenderPresentationFieldOpV1",
        "RENDER_PRESENTATION_FIELD_",
    ):
        assert token not in source


def test_classic_sprite_uses_only_neutral_composition_operations() -> None:
    source = preprocess("engine/render/render_sprite_blit.cpp", classic=True)

    assert "SpriteCompositionFieldOpV1" in source
    assert "SPRITE_COMPOSITION_FIELD_UNKNOWN = 0" in source
    assert "SPRITE_COMPOSITION_FIELD_LIGHTEN_BASE_BK = 9" in source
    assert "TrackSpriteComposition" in source
    for token in (
        "RenderPresentationFieldOpV1",
        "RENDER_PRESENTATION_FIELD_",
        "RecordSpritePresentationDependency",
        "presentation_fg_op",
        "presentation_bk_op",
    ):
        assert token not in source


def test_unicode_sprite_keeps_the_presentation_adapter_and_numeric_abi() -> None:
    source = preprocess("engine/render/render_sprite_blit.cpp", classic=False)
    normalized = " ".join(source.split())

    assert "RecordSpritePresentationDependency" in source
    assert "presentation_dependency_fg_op[idx] = fg_op" in source
    assert "presentation_dependency_bk_op[idx] = bk_op" in source
    assert (
        "(int)SPRITE_COMPOSITION_FIELD_UNKNOWN == "
        "(int)RENDER_PRESENTATION_FIELD_UNKNOWN"
    ) in normalized
    assert (
        "(int)SPRITE_COMPOSITION_FIELD_LIGHTEN_BASE_BK == "
        "(int)RENDER_PRESENTATION_FIELD_LIGHTEN_BASE_BK"
    ) in normalized
