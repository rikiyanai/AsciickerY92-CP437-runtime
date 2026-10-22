"""FL-4588/RQ-201f: presenter wrapper is distinct from HTML alternate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "presenter_app.py"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_wrapper_imports_and_dry_run_targets_current_presenter_binary() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(WRAPPER),
            "--dry-run",
            "--",
            "--authoring-port",
            "18080",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema"] == "asciicker.presenter_wrapper.dry_run.v1"
    assert payload["binary_path"].endswith("/.run/asciicker-presenter")
    assert payload["ownership"]["launch_target"] == ".run/asciicker-presenter"
    assert payload["ownership"]["html_alternate"] == "scripts/package_asciicker_presenter_html_mac.py"
    assert payload["ownership"]["classic_target"] == ".run/asciiid-classic"
    assert payload["ownership"]["does_not_build"] is True
    assert payload["ownership"]["does_not_copy_source_tree"] is True
    assert payload["ownership"]["does_not_select_sprite_profiles"] is True
    assert payload["ownership"]["does_not_mutate_xp_or_bundle_state"] is True
    assert payload["current_native_entrypoint"] == (
        "native_presenter_and_html_authoring_backend_available"
    )
    assert payload["argv"] == ["--authoring-port", "18080"]
    # Verbatim forwarding: no map sniffing, no synthesized environment.
    assert payload["environment"] == {}
    assert "derived_environment" not in payload


def test_wrapper_sheds_asciiid_app_role_without_old_asset_authority() -> None:
    source = read("scripts/presenter_app.py")

    assert "ASCIID FL4512 Presenter.app" in source
    assert "<string>ASCIID FL4512 Presenter</string>" in source
    assert "ASCIID FL4512 TERM++ Presenter" not in source
    assert "world.riki.asciicker.presenter" in source
    assert "exec \"$PRESENTER_BIN\" \"$@\"" in source
    assert "ASCIIID.app" not in source
    assert "world.riki.asciiid.native" not in source
    # The binary parses no --map/.a3d argument, so the wrapper must not sniff it.
    assert "_candidate_map_arg" not in source
    assert "_derived_mesh_root" not in source
    assert "_merge_mesh_root_env" not in source
    assert "Non-Authoring" not in source
    assert "non-authoring" not in source.lower()
    assert "package_asciicker_presenter_html_mac.py" in source
    assert "PLACE_SPRITE" not in source
    assert "sprite profile" not in source.lower()
    assert "xp compiler" not in source.lower()
    assert "bundle compiler" not in source.lower()


def test_makefile_exposes_native_default_and_html_alternate() -> None:
    source = read("Makefile")

    assert "editor: editor-native" in source
    assert "editor-native:" in source
    assert "editor-html-backend:" in source
    assert "run-editor: run-editor-native" in source
    assert "run-editor-native: editor-native" in source
    assert "python3 scripts/presenter_app.py" in source
    assert "run-editor-html:" in source
    assert "python3 scripts/package_asciicker_presenter_html_mac.py" in source


def test_low_level_asciiid_makefiles_name_same_binary_for_distinct_roles() -> None:
    for makefile in ("apps/asciicker-presenter/makefile_asciiid", "apps/asciicker-presenter/makefile_asciiid_mac"):
        source = read(makefile)
        assert "BIN := .run/asciicker-presenter" in source
        assert "native-default: $(BIN)" in source
        assert "html-backend: $(BIN)" in source
        assert "run-native-default: native-default" in source
        assert "scripts/presenter_app.py" in source


def test_current_presenter_identity_does_not_claim_authoring_is_absent() -> None:
    for path in (
        "apps/asciicker-presenter/asciiid.cpp",
        "platform/sdl.cpp",
        "platform/backend/host_sdl2.cpp",
        "engine/mainmenu.cpp",
    ):
        source = read(path).lower()
        assert "non-authoring" not in source
        assert "native product surface retired" not in source
    assert "--authoring-port" in read("apps/asciicker-presenter/asciiid.cpp")
