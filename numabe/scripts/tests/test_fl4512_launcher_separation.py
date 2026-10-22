"""FL-4512: launcher routes keep Classic, presenter, and game products distinct.

Interface-level, source-backed checks only. This file pins launcher action
identity and executable paths; it never builds binaries or opens a GUI.
Game makefile/link separation is owned by
scripts/tests/test_fl4512_game_build_separation.py, not by this file.
"""

import json
import re
from pathlib import Path

from scripts.launcher_lib import option_tree
from scripts.fl4512_extract_manifest import GENERATOR_VERSION

ROOT = Path(__file__).resolve().parents[2]


def _leaf(action: str) -> dict:
    matches = [
        item
        for item in option_tree.flatten_options()
        if item.get("action") == action
    ]
    assert len(matches) == 1
    return matches[0]


def _function_block(source: str, name: str, next_name: str) -> str:
    return source.split(f"def {name}", 1)[1].split(f"def {next_name}", 1)[0]


def test_classic_action_routes_to_classic_handler_and_binary() -> None:
    item = _leaf("map-asciiid-classic")

    assert item["handler"] == "_run_asciiid_classic"
    assert ".run/asciiid-classic" in item["artifacts"]
    assert ".run/asciicker-presenter" not in item["artifacts"]
    assert "classic-separate-target" in item["support_state"]


def test_presenter_action_routes_to_presenter_handler_and_binary() -> None:
    item = _leaf("map-asciicker-presenter")

    assert item["handler"] == "_run_presenter_default"
    assert ".run/asciicker-presenter" in item["artifacts"]
    assert ".run/asciiid-classic" not in item["artifacts"]
    assert "presenter-runtime" in item["support_state"]


def test_html_action_stays_an_alternate_of_the_presenter() -> None:
    item = _leaf("map-asciicker-presenter-html")

    assert item["handler"] == "_run_presenter_html_alternate"
    assert item["handler"] != "_run_asciiid_classic"
    assert "html-alternate" in item["support_state"]


def test_launcher_classic_function_builds_and_launches_classic() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    block = _function_block(source, "_run_asciiid_classic", "_visible_len")

    assert '_ensure_built_binary("asciiid-classic", "editor-classic"' in block
    assert '"scripts/asciid.py", "launch-classic"' in block
    assert "asciicker-presenter" not in block


def test_launcher_presenter_function_builds_and_launches_presenter() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    block = _function_block(source, "_run_presenter(", "_run_presenter_html_alternate")

    assert '_ensure_built_binary("asciicker-presenter", "editor-native"' in block
    assert "scripts/presenter_app.py" in block
    assert "asciiid-classic" not in block


def test_baked_osm_backend_names_the_classic_binary() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    block = _function_block(source, "_asciiid_bin_path", "_baked_osm_ready")

    assert 'RUN_DIR / "asciiid-classic"' in block

    tree_source = (ROOT / "scripts" / "launcher_lib" / "option_tree.py").read_text(
        encoding="utf-8"
    )
    assert ".run/asciiid-classic must exist" in tree_source
    assert ".run/asciicker-presenter must exist" not in tree_source


def test_game_actions_keep_classic_and_current_binaries_distinct() -> None:
    classic = _leaf("game-classic-euclidean")
    spherical = _leaf("game-spherical-classic")
    current = _leaf("game-fl4512-unicode-euclidean")

    assert ".run/game_classic" in classic["command"][0]
    assert ".run/game_classic" in spherical["command"][0]
    assert ".run/game" in current["command"][0]
    assert ".run/game_classic" not in current["command"][0]

    catalog = json.loads(
        (ROOT / "config" / "launcher_products.json").read_text(encoding="utf-8")
    )
    classic_mode = catalog["native_modes"]["classic_euclidean"]
    assert classic_mode["make_target"] == "game-classic"
    assert classic_mode["binary"] == "game_classic"


def test_helpers_use_app_owned_game_specs_without_stale_root_names() -> None:
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    clean = (ROOT / "clean.sh").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    # Root helpers route Classic and Current explicitly; Classic stays default.
    assert "game-classic" in build
    assert "game-fl4512" in build
    # Delegation to app-owned specs lives in the root Makefile, not helpers.
    assert "apps/game-fl4512/makefile_game" in makefile
    assert "apps/game-classic/makefile_game_classic" in makefile
    assert "-f apps/" not in build
    # Teardown still reaches each app-owned spec directly.
    assert "apps/game-fl4512/makefile_game" in clean
    assert "apps/game-classic/makefile_game_classic" in clean
    for helper in (build, clean):
        assert re.search(r"-f makefile_game(?!_term)", helper) is None
        assert "-f makefile_asciiid" not in helper
    # Identity/audit evidence: distinct binaries, renderers, Classic audit.
    assert ".run/game_classic" in makefile
    assert "classic_cp437" in makefile
    assert "fl4512_unicode" in makefile
    assert "audit-game-classic" in makefile


def test_launcher_actions_match_the_runtime_data_contract() -> None:
    contract = json.loads(
        (ROOT / "shared" / "runtime_data" / "runtime_data_contract.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["products"]["asciiid_classic"]["launcher_action"] == (
        "map-asciiid-classic"
    )
    presenter_actions = contract["products"]["asciicker_presenter"]["launcher_actions"]
    for action in (
        "map-asciicker-presenter",
        "map-asciicker-presenter-native",
        "map-asciicker-presenter-html",
    ):
        assert action in presenter_actions
    game_actions = (
        contract["products"]["game_classic_cp437"]["launcher_actions"]
        + contract["products"]["game_fl4512_unicode"]["launcher_actions"]
    )
    for action in (
        "game-classic-euclidean",
        "game-spherical-classic",
        "game-fl4512-unicode-euclidean",
    ):
        assert action in game_actions
    assert contract["filesystem_semantics"]["shared_path_is_shared_session"] is False

INDEX = (
    ROOT
    / "docs"
    / "research"
    / "ascii"
    / "verification"
    / "fl4512"
    / "2026-09-18-asciiid-runtime-separation-index.md"
)
MANIFEST = (
    ROOT
    / "docs"
    / "research"
    / "ascii"
    / "verification"
    / "fl4512"
    / "2026-09-18-fl4512-extraction-manifest.json"
)


def _index_text() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_index_names_current_generator_version() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["generator_version"] == GENERATOR_VERSION
    text = _index_text()
    assert f"v{GENERATOR_VERSION}" in text
    assert set(re.findall(r"v(2\.\d+\.\d+)", text)) == {GENERATOR_VERSION}


def test_index_game_rows_name_app_owned_specs() -> None:
    text = _index_text()
    assert "apps/game-fl4512/makefile_game" in text
    assert "apps/game-classic/makefile_game_classic" in text
    assert "CLASSIC_CP437=0" not in text
    assert "CLASSIC_CP437=1" not in text
    assert re.search(r"`makefile_game\{", text) is None


def test_index_uses_current_manifest_categories() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "build_source" in manifest["counts"]
    assert "build_include" in manifest["counts"]
    text = _index_text()
    assert "build_source" in text
    assert "build_include" in text
    assert "presenter_source" not in text
    assert "presenter_include" not in text
    assert "total_included" not in text


def test_index_source_section_names_all_eight_specs() -> None:
    text = _index_text()
    for spec in (
        "apps/asciicker-presenter/makefile_asciiid",
        "apps/game-fl4512/makefile_game",
        "apps/game-classic/makefile_game_classic",
        "makefile_game_term",
    ):
        assert spec in text
    assert "owns no SRCS list" in text
