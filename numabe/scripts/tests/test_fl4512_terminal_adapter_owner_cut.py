"""FL-4512 terminal ANSI adapter owner cut.

The ANSI polling driver is terminal-only source owned by apps/terminal-runtime.
The shared neutral header stays at platform/backend/host_terminal_ansi.h for the
shared engine TU (engine/game_app.cpp). Only the two terminal specs compile the
adapter cpp; Classic/current/server/presenter specs compile zero adapter cpp.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NEW_ADAPTER = ROOT / "apps/terminal-runtime/platform/backend/host_terminal_ansi.cpp"
OLD_ADAPTER = ROOT / "platform/backend/host_terminal_ansi.cpp"
SHARED_HEADER = ROOT / "platform/backend/host_terminal_ansi.h"
TERMINAL_SPECS = (ROOT / "makefile_game_term", ROOT / "makefile_game_term_mac")
OTHER_SPECS = (
    ROOT / "apps/asciicker-presenter/makefile_asciiid",
    ROOT / "apps/asciicker-presenter/makefile_asciiid_mac",
    ROOT / "apps/game-fl4512/makefile_game",
    ROOT / "apps/game-fl4512/makefile_game_mac",
    ROOT / "apps/game-classic/makefile_game_classic",
    ROOT / "apps/game-classic/makefile_game_classic_mac",
    ROOT / "makefile_server",
)
MANIFEST = ROOT / "docs/research/ascii/verification/fl4512/2026-09-18-fl4512-extraction-manifest.json"


def test_adapter_cpp_has_single_terminal_runtime_owner() -> None:
    assert NEW_ADAPTER.is_file(), NEW_ADAPTER
    assert not OLD_ADAPTER.exists(), "old shared-platform adapter owner must be deleted"
    assert len(list((ROOT / "apps/terminal-runtime").rglob("host_terminal_ansi.cpp"))) == 1


def test_shared_header_stays_neutral() -> None:
    assert SHARED_HEADER.is_file(), SHARED_HEADER
    assert (ROOT / "apps/terminal-runtime/platform/backend/host_terminal_ansi.h").exists() is False


def test_only_terminal_specs_compile_adapter() -> None:
    for spec in TERMINAL_SPECS:
        text = spec.read_text(encoding="utf-8")
        assert text.count("apps/terminal-runtime/platform/backend/host_terminal_ansi.cpp") == 1, spec.name
        assert "platform/backend/host_terminal_ansi.cpp" not in text.replace(
            "apps/terminal-runtime/platform/backend/host_terminal_ansi.cpp", ""
        ), spec.name
    for spec in OTHER_SPECS:
        assert "host_terminal_ansi.cpp" not in spec.read_text(encoding="utf-8"), spec.name


def test_engine_tu_keeps_shared_header_include() -> None:
    text = (ROOT / "engine/game_app.cpp").read_text(encoding="utf-8")
    assert '#include "platform/backend/host_terminal_ansi.h"' in text


def test_adapter_includes_resolve_to_shared_headers() -> None:
    text = NEW_ADAPTER.read_text(encoding="utf-8")
    assert '#include "backend/host_terminal_ansi.h"' in text
    assert '#include "backend/host_input.h"' in text
    assert '#include "backend/terminal_input_parser.h"' in text
    assert '#include "host_terminal_ansi.h"' not in text


def test_manifest_moves_adapter_owner() -> None:
    by_path = {entry["path"]: entry for entry in json.loads(MANIFEST.read_text(encoding="utf-8"))["entries"]}
    assert by_path["apps/terminal-runtime/platform/backend/host_terminal_ansi.cpp"]["category"] == "build_source"
    assert "platform/backend/host_terminal_ansi.cpp" not in by_path
    assert by_path["platform/backend/host_terminal_ansi.h"]["category"] == "build_include"
