"""FL-4512/FL-4588: ASCIIID Classic is a separate restored editor target."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.launcher_lib import option_tree


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _leaf(action: str) -> dict:
    matches = [
        item
        for item in option_tree.flatten_options()
        if item.get("action") == action
    ]
    assert len(matches) == 1
    return matches[0]


def test_asciiid_classic_wrapper_targets_separate_binary() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "asciid.py"),
            "launch-classic",
            "--dry-run",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema"] == "asciiid.classic_wrapper.dry_run.v1"
    assert payload["binary_path"].endswith("/.run/asciiid-classic")
    assert payload["ownership"]["launch_target"] == ".run/asciiid-classic"
    assert payload["ownership"]["source_pin_record"] == "apps/asciiid-classic/CLASSIC_PIN.md"
    assert payload["ownership"]["does_not_launch_presenter"] is True
    assert payload["ownership"]["does_not_launch_html_alternate"] is True


def test_root_makefile_exposes_classic_target_without_stealing_presenter() -> None:
    source = read("Makefile")

    assert "editor-classic:" in source
    assert "apps/asciiid-classic/makefile_asciiid_classic$(MAC_SUFFIX)" in source
    assert "run-editor-classic: editor-classic" in source
    assert "python3 scripts/asciid.py launch-classic" in source
    assert "editor-native:" in source
    assert "apps/asciicker-presenter/makefile_asciiid$(MAC_SUFFIX) native-default" in source


def test_classic_makefiles_build_separate_output_and_object_roots() -> None:
    for makefile in (
        "apps/asciiid-classic/makefile_asciiid_classic",
        "apps/asciiid-classic/makefile_asciiid_classic_mac",
    ):
        source = read(makefile)
        assert "BIN := .run/asciiid-classic" in source
        assert "OBJDIR := .o_asciiid_classic" in source
        assert "DEPDIR := .d_asciiid_classic" in source
        assert "CLASSIC_ROOT := apps/asciiid-classic/pinned" in source
        assert "SHARED_DATA_ROOT := shared/runtime_data" in source
        assert "PINNED_SRCS :=\teditor/asciiid.cpp" in source
        assert "SRCS := $(addprefix $(CLASSIC_ROOT)/,$(PINNED_SRCS))" in source
        assert "-I $(CLASSIC_ROOT)/editor" in source
        assert "-I $(SHARED_DATA_ROOT)" in source
        assert "engine/render/fl4512_" not in source
        assert "platform/fl4683_" not in source
        assert "platform/fl4693_" not in source


def test_live_map_tools_do_not_route_to_retired_plain_asciiid() -> None:
    for path in ("scripts/attach_sbu_assets.py", "scripts/sbu_e2e_run.py"):
        source = read(path)
        assert '".run" / "asciiid"' not in source
        assert ".run/asciiid --batch" not in source
        assert "--action map-asciiid\"" not in source
    assert ".run/asciiid-classic" in read("scripts/sbu_e2e_run.py")
    assert "map-asciicker-presenter-html" in read("scripts/sbu_e2e_run.py")


def test_fl4935_presenter_cross_surface_identity() -> None:
    contract = json.loads(read("shared/runtime_data/runtime_data_contract.v1.json"))
    presenter_contract = contract["products"]["asciicker_presenter"]
    canonical = presenter_contract["product_label"]
    html_surface = presenter_contract["html_surface_label"]
    catalog = json.loads(read("config/launcher_products.json"))
    wrapper = read("scripts/presenter_app.py")
    launcher = read("scripts/launcher.py")
    option_source = read("scripts/launcher_lib/option_tree.py")
    index = read("web/asciiid-authoring-shell/index.html")
    app = read("web/asciiid-authoring-shell/src/App.tsx")
    active_options = read("web/asciiid-authoring-shell/src/ActiveToolOptions.tsx")
    toolbar = read("web/asciiid-authoring-shell/src/Toolbar.tsx")
    widgets = read("web/asciiid-authoring-shell/src/ControlWidget.tsx")
    packager = read("scripts/package_asciicker_presenter_html_mac.py")
    runtime = read("apps/fl4512-runtime/engine/fl4512_neutral_adapter.cpp")
    platform = read("platform/sdl.cpp")
    backend = read("platform/backend/host_sdl2.cpp")
    canon = read("docs/plans/2026-03-22-multiplayer-canonical-spec.md")
    animated_menus = (
        read("scripts/anim_N_rain_menu.py"),
        read("scripts/anim_N_rain_menu_red.py"),
    )

    assert canonical == "ASCIID FL4512 Presenter"
    assert html_surface == "ASCIID FL4512 Presenter HTML Authoring"
    assert catalog["products"]["fl4512_presenter"]["label"] == canonical
    assert f"<string>{canonical}</string>" in wrapper
    assert f"<title>{canonical}</title>" in index
    assert f"{canonical.upper()} · LIVE" in app
    assert f'CHROME_SHELL_WINDOW_TITLE_SUBSTRING = "{canonical}"' in packager
    assert f'"{canonical.upper()}"' in runtime
    assert f'SDL_CreateWindow("{canonical.upper()}"' in platform
    assert f'SDL_CreateWindow("{canonical.upper()}"' in backend
    assert f'label: str = "{html_surface}"' in launcher
    assert f'"Launch {html_surface}"' in option_source
    assert f'APP_BUNDLE_NAME = "{html_surface}.app"' in packager
    assert f'APP_EXECUTABLE_NAME = "{html_surface}"' in packager
    assert f"<string>{html_surface}</string>" in packager
    assert presenter_contract["html_app_bundle"] == f"dist/{html_surface}.app"
    assert "`map-asciicker-presenter-html`" in canon
    assert "`map-asciiid`" not in canon
    assert "ASCIID FL4512 HTML" not in canon
    for menu in animated_menus:
        assert canonical.upper() in menu
        assert "ASCIIID EDITOR" not in menu

    joined = "\n".join(
        (
            wrapper,
            launcher,
            option_source,
            index,
            app,
            active_options,
            toolbar,
            widgets,
            runtime,
            platform,
            backend,
        )
    )
    assert "ASCIIID Editor" not in joined
    assert "ASCIIID is still starting" not in joined
    assert "ASCIID FL-4512 Presenter" not in joined
    assert "ASCIID FL4512 TERM++ Presenter" not in joined
    assert "ASCIID FL4512 TERM++ PRESENTER" not in joined
    assert "ASCIID FL4512 HTML Authoring Alternate" not in joined
    assert 'APP_BUNDLE_NAME = "ASCIID FL4512 HTML.app"' not in packager
    assert 'APP_EXECUTABLE_NAME = "ASCIID FL4512 HTML"' not in packager
    # Prior names remain only as explicit delete-first migration inputs.
    assert 'ROOT / "dist" / "ASCIID FL4512 HTML.app"' in packager
    assert 'ROOT / ".run" / "ASCIID FL4512 HTML.app"' in packager


def test_classic_game_excludes_presenter_tool_through_product_adapter() -> None:
    menu = read("engine/mainmenu.cpp")
    backend = read("platform/backend/host_sdl2.cpp")
    classic = read("apps/classic-runtime/classic_product_adapter.cpp")
    presenter = read("apps/fl4512-runtime/engine/fl4512_neutral_adapter.cpp")

    assert "const ProductStartupV1* startup = AsciickerStartup();" in menu
    assert "const ProductToolV1* tool = startup ? startup->tool : 0;" in menu
    assert "available ? tool->label : 0" in menu
    assert "ASCIID FL4512 PRESENTER" not in menu
    assert "NULL, // This product supplies no application tool." in classic
    assert '"ASCIID FL4512 PRESENTER"' in presenter
    assert "&Fl4512PresenterLaunchToolV1" in presenter
    assert "#elif defined(EDITOR)" in backend
    assert 'SDL_CreateWindow("ASCIICKER"' in backend


def test_launcher_tree_has_classic_as_explicit_third_mode() -> None:
    item = _leaf("map-asciiid-classic")

    assert item["handler"] == "_run_asciiid_classic"
    assert item["command"] == [
        "python3",
        "scripts/launcher.py",
        "--action",
        "map-asciiid-classic",
    ]
    assert item["support_state"] == "classic-separate-target"
    assert "scripts/asciid.py launch-classic" in item["artifacts"]
    assert ".run/asciiid-classic" in item["artifacts"]
    assert "FL-4512" in item["related_fls"]


def test_classic_pin_records_source_provenance_and_scope_boundary() -> None:
    source = read("apps/asciiid-classic/CLASSIC_PIN.md")

    assert "bad6036c23af40815efe87b5a6158158afe022f6" in source
    assert "6191d08e790b973161bdc3b7321b040a17ea3b9a" in source
    assert "PIN_MANIFEST.sha256" in source
    assert "immediate parent" in source
    assert "shared/runtime_data/a3d_abi.h" in source
    assert "zoomed-out mesh pass" in source
    assert "WASD camera pan" in source


def test_world_rejection_cannot_be_reported_as_successful_map_load() -> None:
    classic = read("apps/asciiid-classic/pinned/editor/asciiid.cpp")
    presenter = read("apps/asciicker-presenter/asciiid.cpp")

    assert "World intake rejected; editor restored to empty world" in classic
    assert "if (!LoadMapForSession(path, \"[MCP]\"))" in classic
    assert "Error: Failed to load map" in classic

    # The current product intentionally has no Classic MCP LOAD_MAP command.
    # Its HTML/application-service document transaction reaches the same hard
    # failure in Load() and must also restore safe state before returning.
    assert "World intake rejected; editor restored to empty world" in presenter
    reject_block = presenter.split(
        "World intake rejected; editor restored to empty world", maxsplit=1
    )[0].rsplit("if (!world)", maxsplit=1)[-1]
    assert "terrain = CreateTerrain();" in reject_block
    assert "world = CreateWorld();" in reject_block
    assert "loaded_from_file = false;" in reject_block


def test_classic_manifest_hashes_every_compiled_input() -> None:
    manifest = read("apps/asciiid-classic/PIN_MANIFEST.sha256").splitlines()

    assert len(manifest) == 270
    assert any(line.endswith("  shared/runtime_data/a3d_abi.h") for line in manifest)
    result = subprocess.run(
        [
            "shasum",
            "-a",
            "256",
            "-c",
            "apps/asciiid-classic/PIN_MANIFEST.sha256",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_classic_pinned_closure_has_no_fl4512_or_successor_owner_tokens() -> None:
    pinned = ROOT / "apps" / "asciiid-classic" / "pinned"
    assert pinned.is_dir()
    paths = [path for path in pinned.rglob("*") if path.is_file()]
    assert len(paths) == 269

    forbidden = ("fl-4512", "fl4512", "fl-4683", "fl4683", "fl-4693", "fl4693")
    offenders: list[str] = []
    for path in paths:
        rel = path.relative_to(ROOT).as_posix().lower()
        if any(token in rel for token in forbidden):
            offenders.append(rel)
            continue
        if path.suffix.lower() not in {".c", ".cc", ".cpp", ".h", ".hpp"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if any(token in text for token in forbidden):
            offenders.append(rel)
    assert offenders == []
