from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "scripts/compile_glyph_manifest.py"


def _load_compiler():
    spec = importlib.util.spec_from_file_location("compile_glyph_manifest", COMPILER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v2_unicode_manifest_is_delegated_to_its_single_owner(
    tmp_path: Path, capsys
) -> None:
    manifest = tmp_path / "unicode-v2.json"
    manifest.write_text(
        json.dumps({
            "manifest_version": 2,
            "content_pack_id": "unicode16.onecell.v1",
        }),
        encoding="utf-8",
    )

    module = _load_compiler()
    assert module.cmd_check([str(manifest)]) == 0
    output = capsys.readouterr().out
    assert "[DELEGATED]" in output
    assert "scripts/fl4482_coverage_census.py --verify" in output


def test_build_front_doors_run_the_delegated_validator() -> None:
    command = "scripts/fl4482_coverage_census.py --verify"
    assert command in (ROOT / "build-web.sh").read_text(encoding="utf-8")
    assert command in (ROOT / "apps/game-fl4512/makefile_game_mac").read_text(encoding="utf-8")


def test_web_build_python_selector_requires_atlas_decoder() -> None:
    build_web = (ROOT / "build-web.sh").read_text(encoding="utf-8")
    selector = build_web.split("_asciicker_python_version_ok()", 1)[1].split(
        "_asciicker_select_build_python()", 1
    )[0]
    assert "import PIL" in selector
    assert "Python 3.11+ with Pillow" in build_web
