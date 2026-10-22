"""FL-4588/RQ-201f: audit the presenter against historical ASCIIID at 7455ae3d.

This is a non-headed contract test. It reads the current source and compares it
with the independent historical ASCIIID source anchor. It executes only the
separately named presenter binary, does not launch the GUI, and does not treat
the candidate deployment as ASCIIID provenance.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_ASCIIID_COMMIT = "7455ae3d7df5ea8fa6a62f576d349de8aadf3af7"


def read_current_source() -> str:
    return (ROOT / "apps" / "asciicker-presenter" / "asciiid.cpp").read_text(encoding="utf-8")


def read_historical_source() -> str:
    return subprocess.check_output(
        ["git", "show", f"{HISTORICAL_ASCIIID_COMMIT}:editor/asciiid.cpp"],
        cwd=ROOT,
        text=True,
    )


def read_current_product_closure() -> str:
    return "\n".join(
        (
            read_current_source(),
            (ROOT / "apps/fl4512-runtime/engine/fl4512_product_startup.cpp").read_text(
                encoding="utf-8"
            ),
            (ROOT / "apps/fl4512-runtime/engine/fl4512_neutral_adapter.cpp").read_text(
                encoding="utf-8"
            ),
        )
    )


def presenter_help() -> str:
    return subprocess.check_output(
        [str(ROOT / ".run" / "asciicker-presenter"), "--help"],
        cwd=ROOT,
        text=True,
    )


def test_presenter_default_help_and_guard_removal_are_present() -> None:
    current = read_current_source()
    help_text = presenter_help()

    assert "  --viewer         Use lean launcher/viewer bootstrap." in help_text
    assert "  --sprite-browser Start directly in the native sprite browser UI." in help_text
    assert "A bare launch opens the ASCIID FL4512 presenter UI." in help_text
    assert "Run the presenter runtime for its HTML Authoring client." in help_text
    assert "native ASCIIID product UI" not in help_text
    assert "HTML ASCIIID alternate" not in help_text
    assert "The HTML shell owns the sole visible product UI." not in help_text
    assert "native product surface retired" not in current
    assert 'strcmp(argv[i], "--viewer") == 0' in current
    assert 'strcmp(argv[i], "--sprite-browser") == 0' in current
    assert "g_startup_viewer_mode = true;" in current
    assert "g_startup_sprite_browser = true;" in current


def test_7455_viewer_and_sprite_browser_entrypoint_contract_is_restored() -> None:
    historical = read_historical_source()
    current = read_current_source()

    for token in (
        "#define g_startup_viewer_mode",
        "#define g_startup_sprite_browser",
        'strcmp(argv[i], "--viewer") == 0',
        'strcmp(argv[i], "--sprite-browser") == 0',
        "bool quiet_viewer_terminal = g_startup_viewer_mode",
        "if (!g_startup_viewer_mode)",
        "ScanEditorSpriteDirectory(g_startup_viewer_mode)",
        "if (g_startup_sprite_browser)",
        "edit_mode = 4;",
    ):
        assert token in historical
        assert token in current


def test_7455_cli_surfaces_still_missing_from_current_presenter_main() -> None:
    historical = read_historical_source()
    current = read_current_source()
    help_text = presenter_help()

    missing_from_current = (
        "--map FILE",
        "--sprite FILE",
        "--batch",
        "--headless-batch",
        "--fl4131-material-proof",
        "--test-script",
        "g_startup_map_path",
        "g_startup_sprite_path",
        "LoadMapForSession(g_startup_map_path",
        "ActivateSpriteTarget(g_startup_sprite_path",
    )
    for token in missing_from_current:
        assert token in historical
        assert token not in current
        assert token not in help_text


def test_current_normalized_contracts_intentionally_supersede_7455() -> None:
    historical = read_historical_source()
    current = read_current_product_closure()
    help_text = presenter_help()

    superseding_tokens = (
        "--authoring-port PORT",
        "asciiid_application_service.h",
        "asciiid_authoring_http.h",
        "asciiid_document_codec.h",
        "ASCIID_FL4588_CODEC_ROUNDTRIP",
        "Fl4512ConfigureProfileWorkspaceV1",
        "Fl4131RuntimeHarriInstall",
        "Run the presenter runtime for its HTML Authoring client.",
    )
    for token in superseding_tokens:
        assert token not in historical
        assert token in current or token in help_text
