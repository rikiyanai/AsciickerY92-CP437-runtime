"""FL-4512 G3: Classic owns the default `make game` / `make run-game` route.

The default path builds the binary, writes identity, then performs the
link-map / nm / strings exclusion checks exactly once. `game-classic` is the
route owner, `game` is its default alias, and `audit-game-classic` remains a
working explicit alias: it depends on `game-classic` and carries no second
audit implementation, and neither target depends back on the other, so there
is no make cycle. `game-fl4512` / `run-game-fl4512` keep the explicit
FL-4512 route with no renamed binaries.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
PRODUCT_CATALOG = ROOT / "config" / "launcher_products.json"

BUILD_MARKER = "apps/game-classic/makefile_game_classic"
IDENTITY_MARKER = "write_native_build_identity.py --binary .run/game_classic"
AUDIT_MARKERS = (
    'Classic link map missing',
    'Classic link map contains an excluded renderer owner',
    'Classic binary contains an excluded renderer-owner symbol',
    'Classic binary contains excluded renderer-owner text',
    'Classic renderer-owner audit ok',
)

def _recipe(text: str, target: str) -> str:
    lines = text.splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line == f"{target}:" or line.startswith(f"{target}: ")
    )
    body: list[str] = []
    for line in lines[start + 1:]:
        if not line.strip() or line.startswith("#"):
            continue
        if line[0] in (" ", "\t"):
            body.append(line)
        else:
            break
    return "\n".join(body)


def test_default_path_builds_identifies_then_audits() -> None:
    recipe = _recipe(MAKEFILE.read_text(encoding="utf-8"), "game-classic")
    build_at = recipe.index(BUILD_MARKER)
    identity_at = recipe.index(IDENTITY_MARKER)
    audit_at = [recipe.index(marker) for marker in AUDIT_MARKERS]
    assert build_at < identity_at < min(audit_at)


def test_explicit_alias_preserved_without_second_audit() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "audit-game-classic: game-classic" in text
    alias_recipe = _recipe(text, "audit-game-classic")
    assert BUILD_MARKER not in alias_recipe
    for marker in AUDIT_MARKERS:
        assert marker not in alias_recipe


def test_single_audit_owner() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    for marker in AUDIT_MARKERS:
        assert text.count(marker) == 1, marker


def test_no_cycle_between_default_and_alias() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    dep_line = next(
        line for line in text.splitlines() if line.startswith("game-classic:")
    )
    assert "audit-game-classic" not in dep_line


def test_dry_run_default_path_audits_exactly_once() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "game-classic"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert BUILD_MARKER in result.stdout
    assert result.stdout.count("Classic renderer-owner audit ok") == 1


def test_dry_run_explicit_alias_audits_exactly_once() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "audit-game-classic"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert BUILD_MARKER in result.stdout
    assert result.stdout.count("Classic renderer-owner audit ok") == 1

CLASSIC_BUILD_MARKER = "apps/game-classic/makefile_game_classic"
CLASSIC_IDENTITY_MARKER = "--binary .run/game_classic --renderer classic_cp437"
CURRENT_BUILD_MARKER = "apps/game-fl4512/makefile_game"
CURRENT_IDENTITY_MARKER = "--binary .run/game --renderer fl4512_unicode"


def test_default_game_aliases_classic_route() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    dep_line = next(
        line for line in text.splitlines() if line.startswith("game:")
    )
    assert "game-classic" in dep_line
    assert CURRENT_BUILD_MARKER not in _recipe(text, "game")
    assert CURRENT_IDENTITY_MARKER not in _recipe(text, "game")


def test_explicit_current_route_preserved() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    recipe = _recipe(text, "game-fl4512")
    assert CURRENT_BUILD_MARKER in recipe
    assert CURRENT_IDENTITY_MARKER in recipe
    assert CLASSIC_BUILD_MARKER not in recipe
    assert CLASSIC_IDENTITY_MARKER not in recipe


def test_default_run_routes_to_classic_binary() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    dep_line = next(
        line for line in text.splitlines() if line.startswith("run-game:")
    )
    assert "run-game-classic" in dep_line
    current_recipe = _recipe(text, "run-game-fl4512")
    assert ".run/game " in current_recipe
    assert ".run/game_classic" not in current_recipe


def test_help_and_phony_agree_on_routes() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "game-fl4512" in text
    assert "run-game-fl4512" in text
    assert "make game-fl4512" in text
    assert "make run-game-fl4512" in text


def test_dry_run_game_produces_classic_recipe() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "game"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert CLASSIC_BUILD_MARKER in result.stdout
    assert CLASSIC_IDENTITY_MARKER in result.stdout
    assert result.stdout.count("Classic renderer-owner audit ok") == 1
    assert CURRENT_BUILD_MARKER not in result.stdout
    assert CURRENT_IDENTITY_MARKER not in result.stdout


def test_dry_run_game_current_produces_current_recipe() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "game-fl4512"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert CURRENT_BUILD_MARKER in result.stdout
    assert CURRENT_IDENTITY_MARKER in result.stdout
    assert CLASSIC_BUILD_MARKER not in result.stdout
    assert "Classic renderer-owner audit ok" not in result.stdout

def test_explicit_current_product_mode_rebuilds_via_game_current() -> None:
    catalog = json.loads(PRODUCT_CATALOG.read_text(encoding="utf-8"))
    mode = catalog["native_modes"]["fl4512_unicode_euclidean"]
    assert mode["binary"] == "game"
    assert mode["make_target"] == "game-fl4512"


def test_setup_builds_classic_default_and_explicit_current() -> None:
    source = (ROOT / "scripts" / "setup.sh").read_text(encoding="utf-8")
    # Root routes execute the identity writer + Classic forbidden-owner audit.
    assert " game-classic -j" in source
    assert " game-fl4512 -j" in source
    assert "apps/game-classic/makefile_game_classic" not in source
    assert "apps/game-fl4512/makefile_game" not in source
    assert ".run/game_classic" in source
    assert "Classic default" in source
    assert "explicit FL-4512" in source


def test_status_probes_classic_default_and_explicit_current() -> None:
    source = (ROOT / "scripts" / "status.sh").read_text(encoding="utf-8")
    assert "product_health_signals" in source
    assert "product_status_lines" in source
    assert "Game-current" not in source
    assert "has_game_classic" not in source


def test_getting_started_names_classic_default_binary() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    assert '("Python venv", _health.fast_probes().venv)' in source
    assert "_product_catalog.product(\"classic_game\")" in source
    assert "_product_catalog.product(\"fl4512_game\")" in source
    assert "_product_catalog.product(\"godot\")" in source


def test_default_launcher_action_routes_to_classic() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    assert 'product_mode: str = "classic_euclidean"' in source
    assert 'product_mode: str = "fl4512_unicode_euclidean"' not in source


def test_legacy_single_player_alias_is_deleted_explicit_route_only() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    assert "_run_single_player" not in source
    assert "game-single-player" not in source
    tree = (ROOT / "scripts" / "launcher_lib" / "option_tree.py").read_text(
        encoding="utf-8"
    )
    assert "game-single-player" not in tree
    assert "game-fl4512-unicode-euclidean" in tree


def test_health_default_surface_probes_classic() -> None:
    source = (ROOT / "scripts" / "launcher_lib" / "health.py").read_text(encoding="utf-8")
    assert '_product_catalog.PRODUCTS["classic_game"]' in source
    assert '_product_catalog.PRODUCTS["fl4512_game"]' in source
    assert "_game_fl4512_surface_state" in source
    assert "_product_catalog.BINARY_BUILD_TARGETS" in source
    assert "_MAKE_TARGET_FOR_BINARY" not in source
    assert "product_health_signals()" in source


def test_getting_started_uses_health_surfaces() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    assert "_health.fast_probes().game_fl4512" in source
    assert "_health.fast_probes().godot" in source
    assert '(RUN_DIR / "game_classic").is_file()' not in source


def test_build_sh_builds_classic_default_and_explicit_current() -> None:
    source = (ROOT / "build.sh").read_text(encoding="utf-8")
    # Root routes execute the identity writer + Classic forbidden-owner audit.
    assert "make -j16 game-classic" in source
    assert "make -j16 game-fl4512" in source
    assert "apps/game-classic/makefile_game_classic" not in source
    assert "apps/game-fl4512/makefile_game" not in source
    assert "Classic default" in source
    assert "explicit FL-4512" in source


def test_classic_product_mode_builds_via_default_route() -> None:
    catalog = json.loads(PRODUCT_CATALOG.read_text(encoding="utf-8"))
    mode = catalog["native_modes"]["classic_euclidean"]
    assert mode["binary"] == "game_classic"
    assert mode["make_target"] == "game-classic"
    assert mode["renderer"] == "classic_cp437"


def test_single_player_menu_lists_classic_first() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    classic_at = source.index('_menu_line(f"  [1] {classic[\'label\']}")')
    current_at = source.index('_menu_line(f"  [3] {fl4512[\'label\']}")')
    assert classic_at < current_at


def test_config_menu_exposes_both_product_build_routes() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    assert '_product_catalog.native_mode("classic_euclidean")' in source
    assert '_product_catalog.native_mode("fl4512_unicode_euclidean")' in source
    assert 'str(classic["make_target"])' in source
    assert 'str(fl4512["make_target"])' in source
    assert "apps/game-classic/makefile" not in source
    assert "apps/game-fl4512/makefile" not in source
