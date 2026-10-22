from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

CORE_PATHS = (
    "engine/render/fl4208_surface_identity.h",
    "engine/render/render_internal.h",
    "engine/render/render_scene.cpp",
    "engine/render/render_world_pass.cpp",
)

OLD_RENDERER_OWNER_TOKENS = (
    "Fl4512VisibilitySheetV1",
    "Fl4512VisibilitySheetFailureV1",
    "Fl4512VisibilitySheetForSubmissionV1",
    "FL4512_VISIBILITY_SHEET_",
    "Fl4512S3SubmissionStateV1",
    "FL4512_S3_SUBMISSION_",
)


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_shared_visibility_identity_has_no_fl4512_owner_name() -> None:
    sources = "\n".join(read(path) for path in CORE_PATHS)

    for token in OLD_RENDERER_OWNER_TOKENS:
        assert token not in sources

    assert "enum RenderVisibilitySheetV1" in sources
    assert "enum RenderVisibilitySheetFailureV1" in sources
    assert "enum RenderS3SubmissionStateV1" in sources
    assert "RenderVisibilitySheetForSubmissionV1" in sources


def test_shared_visibility_identity_keeps_exact_wire_values() -> None:
    header = read("engine/render/fl4208_surface_identity.h")
    expected_values = {
        "RENDER_VISIBILITY_SHEET_INVALID": 0,
        "RENDER_VISIBILITY_SHEET_EUCLIDEAN_DIRECT": 1,
        "RENDER_VISIBILITY_SHEET_EUCLIDEAN_PLANAR_REFLECTION": 2,
        "RENDER_VISIBILITY_SHEET_S3_IMAGE0_PARITY0": 3,
        "RENDER_VISIBILITY_SHEET_S3_IMAGE0_PARITY1": 4,
        "RENDER_VISIBILITY_SHEET_S3_IMAGE1_PARITY0": 5,
        "RENDER_VISIBILITY_SHEET_S3_IMAGE1_PARITY1": 6,
        "RENDER_VISIBILITY_SHEET_FAILURE_NONE": 0,
        "RENDER_VISIBILITY_SHEET_FAILURE_S3_PROJECTION_FAILED": 1,
        "RENDER_VISIBILITY_SHEET_FAILURE_S3_PARITY_UNAVAILABLE": 2,
        "RENDER_VISIBILITY_SHEET_FAILURE_INVALID_S3_IMAGE": 3,
        "RENDER_VISIBILITY_SHEET_FAILURE_INVALID_REFLECTION_COMBINATION": 4,
        "RENDER_VISIBILITY_SHEET_FAILURE_INVALID_S3_STATE": 5,
        "RENDER_S3_SUBMISSION_OFF": 0,
        "RENDER_S3_SUBMISSION_ACTIVE": 1,
        "RENDER_S3_SUBMISSION_INVALID_PUBLISHED": 2,
    }

    for symbol, value in expected_values.items():
        assert f"{symbol} = {value}" in header


def test_classic_preprocessed_closure_has_no_old_visibility_owner_name() -> None:
    preprocessed = ROOT / ".o_game_classic"
    if not preprocessed.exists():
        return

    for path in preprocessed.rglob("*.ii"):
        source = path.read_text(encoding="utf-8", errors="ignore")
        for token in OLD_RENDERER_OWNER_TOKENS:
            assert token not in source, f"{token} remains in {path}"
