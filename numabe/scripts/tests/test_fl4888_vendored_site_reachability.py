"""FL-4888: a vendored shader compile site disqualifies only runs it can execute in."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_fl4359_headed_movement as runner  # noqa: E402


def _tree(tmp_path: Path, first_party: str) -> Path:
    godot = tmp_path / "godot_project"
    (godot / "canxkoz_gpu_port").mkdir(parents=True)
    (godot / "canxkoz_gpu_port" / "canxkoz_port.gd").write_text(
        "func f():\n\tvar s = rd.shader_compile_spirv_from_source(x)\n", encoding="utf-8")
    (godot / "scripts").mkdir()
    (godot / "scripts" / "World.gd").write_text(first_party, encoding="utf-8")
    (godot / "artifacts").mkdir()
    (godot / "artifacts" / "note.gd").write_text("# canxkoz_gpu_port mentioned in a handoff\n")
    return godot


def test_unloaded_vendored_site_is_exempt(tmp_path: Path) -> None:
    godot = _tree(tmp_path, "func _ready():\n\tpass\n")
    site = f"{godot}/canxkoz_gpu_port/canxkoz_port.gd:2"
    assert runner._unreachable_vendored_site(site, godot)


def test_vendored_site_referenced_by_game_code_is_not_exempt(tmp_path: Path) -> None:
    godot = _tree(tmp_path, 'const P := preload("res://canxkoz_gpu_port/canxkoz_port.gd")\n')
    site = f"{godot}/canxkoz_gpu_port/canxkoz_port.gd:2"
    assert not runner._unreachable_vendored_site(site, godot)


def test_first_party_site_is_never_exempt(tmp_path: Path) -> None:
    godot = _tree(tmp_path, "func _ready():\n\tpass\n")
    assert not runner._unreachable_vendored_site(f"{godot}/scripts/World.gd:2", godot)


def test_real_project_never_loads_the_canxkoz_port() -> None:
    assert not runner._vendored_root_referenced("canxkoz_gpu_port", ROOT / "godot_project")
    sites = [s for s in runner._unrecorded_shader_compile_sites(ROOT / "godot_project")]
    assert sites, "expected the vendored canxkoz sites to be found by the static scan"
    remaining = [s for s in sites if not runner._unreachable_vendored_site(s, ROOT / "godot_project")]
    assert remaining == []
