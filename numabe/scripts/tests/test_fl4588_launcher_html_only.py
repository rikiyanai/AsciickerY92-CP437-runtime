"""FL-4588/RQ-201f: ASCIIID launch modes after the native-default amendment.

The default product mode is the candidate-publication ASCIID FL4512 presenter wrapper.
The HTML shell is an explicit alternate authoring mode.  Neither mode may
become a sprite-runtime owner or bypass the FL-4162 normalized XP contract.
"""

from pathlib import Path

from scripts.launcher_lib import option_tree
from scripts.launcher_lib import product_catalog

ROOT = Path(__file__).resolve().parents[2]


def _function_block(source: str, name: str, next_name: str) -> str:
    return source.split(f"def {name}", 1)[1].split(f"def {next_name}", 1)[0]


def _leaf(action: str) -> dict:
    matches = [
        item
        for item in option_tree.flatten_options()
        if item.get("action") == action
    ]
    assert len(matches) == 1
    return matches[0]


def test_map_editor_action_is_presenter_with_explicit_wrapper_identity() -> None:
    item = _leaf("map-asciicker-presenter")

    assert item["handler"] == "_run_presenter_default"
    assert item["command"] == [
        "python3",
        "scripts/launcher.py",
        "--action",
        "map-asciicker-presenter",
    ]
    assert item["owner"] == "_run_presenter_default"
    assert "presenter-runtime" in item["support_state"]
    assert "scripts/presenter_app.py" in item["artifacts"]
    assert ".run/asciicker-presenter" in item["artifacts"]
    assert "visible presenter file UI" not in item["inputs"]
    assert "takes no map input" in item["inputs"]


def test_html_asciiid_action_is_registered_as_alternate_mode_only() -> None:
    item = _leaf("map-asciicker-presenter-html")

    assert item["handler"] == "_run_presenter_html_alternate"
    assert item["command"] == [
        "python3",
        "scripts/launcher.py",
        "--action",
        "map-asciicker-presenter-html",
    ]
    assert item["owner"] == "_run_presenter_html_alternate"
    assert "alternate" in item["support_state"]
    assert "scripts/package_asciicker_presenter_html_mac.py" in item["artifacts"]
    assert "scripts/asciiid_app.py" not in item["artifacts"]


def test_launcher_presenter_uses_presenter_app_without_html_packager() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    block = _function_block(source, "_run_presenter", "_run_presenter_html_alternate")

    assert "_ensure_built_binary" in block
    assert "scripts/presenter_app.py" in block
    assert "asciicker-presenter" in block
    assert "scripts/package_asciicker_presenter_html_mac.py" not in block
    assert "presenter_app" in source


def test_launcher_html_alternate_uses_package_front_door() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    block = _function_block(source, "_run_presenter_html_alternate", "_run_presenter_default")

    assert "scripts/package_asciicker_presenter_html_mac.py" in block
    assert "launch_asciiid_" + "gui" not in block
    assert "_ensure_built_binary" not in block


def test_agent_bridge_does_not_inject_a_hidden_document_path() -> None:
    source = (ROOT / "scripts" / "launcher_agent_bridge.py").read_text(
        encoding="utf-8"
    )
    block = _function_block(
        source,
        "_adapt__run_presenter_default",
        "_adapt__menu_mobile_playwright",
    )

    assert "map_path" not in block
    assert "DEFAULT_MAP" not in block


def test_launcher_tree_keeps_native_and_html_failure_surfaces_separate() -> None:
    source = (ROOT / "scripts" / "launcher_lib" / "option_tree.py").read_text(
        encoding="utf-8"
    )
    native = _leaf("map-asciicker-presenter")
    html = _leaf("map-asciicker-presenter-html")

    assert native["related_fls"] == ["FL-4588", "FL-888"]
    assert html["related_fls"] == ["FL-4588", "FL-888"]
    assert native["failure_surface"] != html["failure_surface"]
    assert "presenter-runtime" in source
    assert "html-alternate" in source


def test_presenter_app_wrapper_is_present_and_has_no_runtime_visual_authority() -> None:
    source = (ROOT / "scripts" / "presenter_app.py").read_text(encoding="utf-8")

    assert "launch_presenter_gui" in source
    assert "exec \"$PRESENTER_BIN\" \"$@\"" in source
    assert "LoadSprite" not in source
    assert "CompiledActorVisualKey" not in source
    assert "profile_bindings" not in source
    assert "server_reachable_keys" not in source


def test_crashlog_diagnostic_cannot_present_native_product_windows() -> None:
    source = (ROOT / "scripts" / "pipeline" / "asciiid_crashlog.sh").read_text(
        encoding="utf-8"
    )

    assert 'ASCIIID_HTML_SHELL_BACKEND=1 "$PRESENTER" "$@"' in source


def test_make_run_editor_defaults_native_and_retains_html_alternate() -> None:
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = source.split("run-editor:", 1)[1].split("run-game:", 1)[0]

    assert block.lstrip().startswith("run-editor-native")
    assert "scripts/presenter_app.py" in block
    assert "run-editor-html:" in block
    assert "scripts/package_asciicker_presenter_html_mac.py" in block


def test_status_names_the_native_binary_as_the_headless_engine() -> None:
    source = (ROOT / "scripts" / "status.sh").read_text(encoding="utf-8")

    presenter = product_catalog.PRODUCTS["fl4512_presenter"]
    assert presenter["binary"] == "asciicker-presenter"
    assert presenter["label"] == "ASCIID FL4512 Presenter"
    assert "product_health_signals" in source
    assert "headless HTML backend" not in source


def test_osm_bake_prerequisite_does_not_call_the_backend_the_editor() -> None:
    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")

    assert "ASCIIID Classic terrain-bake engine not built" in source
    assert "ASCIIID editor binary not built" not in source
    assert 'RUN_DIR / "asciiid-classic"' in source
    assert ".run/asciicker-presenter) for terrain bake" not in source
    assert "make editor-classic" in source


def test_asciiid_legacy_routes_name_the_classic_binary() -> None:
    tree_source = (ROOT / "scripts" / "launcher_lib" / "option_tree.py").read_text(
        encoding="utf-8"
    )
    for action in ("map-osm-local-baked", "map-osm-terrain-debug"):
        item = _leaf(action)
        assert "asciiid_legacy" in " ".join(item["command"])
        assert ".run/asciiid-classic" in item["inputs"]
        assert ".run/asciicker-presenter" not in item["inputs"]
    assert ".run/asciicker-presenter must exist" not in tree_source


def test_asset_pipeline_cannot_mutate_asciiid_through_native_stdin() -> None:
    source = (
        ROOT / "scripts" / "pipeline" / "service" / "asset_service.py"
    ).read_text(encoding="utf-8")

    assert "PLACE_SPRITE" not in source
    assert "place_in_editor" not in source
    assert "editor_stdin" not in source
    assert "Editor not connected" not in source
