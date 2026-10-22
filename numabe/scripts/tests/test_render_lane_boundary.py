"""FL-4877: the non-FF render lane is protected from True FF changes."""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import render_lane_boundary as lane  # noqa: E402

CONFIG = {
    "schema": lane.SCHEMA,
    "protected": [
        "godot_project/render/*",
        "godot_project/*.glsl",
        "godot_project/*.glslinc",
        "godot_project/*.gdshader",
        "godot_project/*.gdshaderinc",
        lane.CONFIG_RELATIVE,
    ],
    "excluded": ["*.uid", "*.md"],
    "ff_private": ["godot_project/render/ascii/composition/*"],
}
NONFF_ROOT = "godot_project/render/product/product.glsl"
SHARED_INC = "godot_project/render/core/shared.glslinc"
FF_ROOT = "godot_project/render/ascii/composition/true_ff_compose.glsl"
FF_INC = "godot_project/render/ascii/composition/true_ff_core.glslinc"
OTHER_INC = "godot_project/scripts/water/water_common.glslinc"
SPATIAL = "godot_project/visual/terrain/terrain_ascii.gdshader"
SPATIAL_INC = "godot_project/visual/terrain/terrain_common.gdshaderinc"
FF_GDINC = "godot_project/render/ascii/composition/true_ff_material.gdshaderinc"
SPATIAL_TEXT = (
    'shader_type spatial;\n#include "res://visual/terrain/terrain_common.gdshaderinc"\n'
    "void fragment() { ALBEDO = vec3(terrain_tone()); }\n"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _write(repo, lane.CONFIG_RELATIVE, json.dumps(CONFIG))
    _write(repo, SHARED_INC, "float shared_value() { return 1.0; }\n")
    _write(repo, NONFF_ROOT, '#[compute]\n#include "res://render/core/shared.glslinc"\nvoid main() {}\n')
    _write(repo, FF_INC, "float ff_value() { return 2.0; }\n")
    _write(
        repo,
        FF_ROOT,
        '#include "res://render/core/shared.glslinc"\n#include "res://render/ascii/composition/true_ff_core.glslinc"\nvoid main() {}\n',
    )
    _write(repo, OTHER_INC, "float water_value() { return 4.0; }\n")
    _write(repo, SPATIAL_INC, "float terrain_tone() { return 0.5; }\n")
    _write(repo, SPATIAL, SPATIAL_TEXT)
    _write(repo, FF_GDINC, "float ff_tone() { return 0.25; }\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _stage(repo: Path, relative: str, text: str) -> None:
    _write(repo, relative, text)
    _git(repo, "add", relative)


def test_ff_private_change_needs_no_trailer(repo: Path) -> None:
    _stage(repo, FF_INC, "float ff_value() { return 3.0; }\n")
    assert lane.staged_errors("fix: FF\n", base_dir=repo) == []


def test_comment_only_change_to_non_ff_is_not_a_change(repo: Path) -> None:
    _stage(repo, SHARED_INC, "// FL-4877 note\nfloat shared_value() {  return 1.0; }\n")
    assert lane.staged_errors("docs: comment\n", base_dir=repo) == []


def test_code_change_to_shared_non_ff_include_is_refused_without_trailer(repo: Path) -> None:
    _stage(repo, SHARED_INC, "float shared_value() { return 0.5; }\n")
    errors = lane.staged_errors("fix(FL-4877): True FF tweak\n", base_dir=repo)
    assert len(errors) == 1 and SHARED_INC in errors[0] and lane.TRAILER in errors[0]


def test_trailer_with_reason_admits_non_ff_change(repo: Path) -> None:
    _stage(repo, SHARED_INC, "float shared_value() { return 0.5; }\n")
    message = f"fix: product\n\n{lane.TRAILER} Product shoreline repair\n"
    assert lane.staged_errors(message, base_dir=repo) == []


def test_empty_or_none_trailer_does_not_admit(repo: Path) -> None:
    _stage(repo, SHARED_INC, "float shared_value() { return 0.5; }\n")
    for trailer in (f"{lane.TRAILER}", f"{lane.TRAILER} none"):
        assert lane.staged_errors(f"fix: x\n\n{trailer}\n", base_dir=repo)


def test_deleting_a_non_ff_file_is_a_change(repo: Path) -> None:
    _git(repo, "rm", "-q", SHARED_INC)
    errors = lane.staged_errors("chore: remove\n", base_dir=repo)
    assert any(SHARED_INC in error for error in errors)


def test_non_ff_shader_including_ff_private_file_is_refused_even_with_trailer(repo: Path) -> None:
    _stage(
        repo,
        NONFF_ROOT,
        '#[compute]\n#include "res://render/ascii/composition/true_ff_core.glslinc"\nvoid main() {}\n',
    )
    errors = lane.staged_errors(f"fix: x\n\n{lane.TRAILER} intended\n", base_dir=repo)
    assert len(errors) == 1 and "includes FF-private" in errors[0] and FF_INC in errors[0]


def test_moving_a_file_into_the_ff_lane_needs_the_trailer(repo: Path) -> None:
    config = dict(CONFIG, ff_private=CONFIG["ff_private"] + ["godot_project/render/core/*"])
    _stage(repo, lane.CONFIG_RELATIVE, json.dumps(config))
    errors = lane.staged_errors("chore: lane\n", base_dir=repo)
    assert any(lane.CONFIG_RELATIVE in error for error in errors)


def test_lane_diff_names_changed_file_commit_and_expanded_root(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _stage(repo, SHARED_INC, "float shared_value() { return 0.25; }\n")
    _git(repo, "commit", "-q", "-m", "feat: True FF change that leaked")
    _stage(repo, FF_INC, "float ff_value() { return 9.0; }\n")
    _git(repo, "commit", "-q", "-m", "feat: FF only")
    receipt = lane.lane_diff(base, "HEAD", base_dir=repo)
    assert [row["path"] for row in receipt["changed_files"]] == [SHARED_INC]
    assert "True FF change that leaked" in receipt["changed_files"][0]["commits"][0]
    shaders = {row["shader"] for row in receipt["changed_expanded_shaders"]}
    assert shaders == {NONFF_ROOT, SHARED_INC}
    assert receipt["closure_violations_at_head"] == []


# --- the real repository config ---------------------------------------------------------


def _real_boundary() -> lane.LaneBoundary:
    return lane.load_boundary((ROOT / lane.CONFIG_RELATIVE).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "path",
    [
        "godot_project/render/backends/perspective/publish/perspective_probe_payload_publish.glsl",
        "godot_project/render/ascii/authority/native_presentation_authority.glslinc",
        "godot_project/render/product/cell_owner_product.glsl",
        "godot_project/render/ascii/raster/ascii_compositor.glsl",
        "godot_project/render/diagnostics/FFGlyphDiagnostic.glslinc",
        "godot_project/render/api/ASCIICompositorEffect.gd",
        "godot_project/scripts/world/water/simulation/water_flow_field.glsl",
        lane.CONFIG_RELATIVE,
        # Godot shader-language files outside render/ (FL-4877 review item 3).
        "godot_project/ansi_spatial.gdshader",
        "godot_project/ansi_terrain.gdshader",
        "godot_project/visual/terrain/a3d_terrain_ascii.gdshader",
        "godot_project/visual/terrain/a3d_terrain_vertex_color.gdshader",
        "godot_project/visual/actors/actor_3d_ascii_sentinel.gdshader",
        "godot_project/visual/sprites/xp_sprite.gdshader",
        "godot_project/visual/overlays/ship_overlay_canvas.gdshader",
        "godot_project/level/cactus/Plants.gdshader",
        "godot_project/level/interactable/flag/goal_flag.gdshader",
        "godot_project/visual/terrain/future_include.gdshaderinc",
    ],
)
def test_real_config_protects_non_ff_owners(path: str) -> None:
    assert _real_boundary().is_non_ff(path)


def test_every_tracked_first_party_godot_shader_is_protected_or_ff_private() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "godot_project"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    boundary = _real_boundary()
    loose = [
        path
        for path in tracked
        if lane._is_res_shader(path)
        and not path.startswith(("godot_project/extensions/", "godot_project/canxkoz_gpu_port/"))
        and not (boundary.is_non_ff(path) or boundary.is_ff_private(path))
    ]
    assert loose == []


@pytest.mark.parametrize(
    "path",
    [
        "godot_project/render/ascii/structure/true_ff_structure_pass.glsl",
        "godot_project/render/ascii/composition/true_ff_prepare.glsl",
        "godot_project/render/ascii/law/true_ff_terrain_law.glslinc",
        "godot_project/render/ascii/raster/final_ff_raster.glsl",
        "godot_project/render/backends/ortho/ff_acquisition/OrthoMeasuredPorts.gd",
        "godot_project/render/backends/perspective/PerspectiveFFRenderer.gd",
        "godot_project/canxkoz_gpu_port/shaders/sky.glsl",
        "godot_project/extensions/terrain_3d_a3d/shaders/a3d_compat_terrain.gdshader",
    ],
)
def test_real_config_leaves_ff_and_vendored_files_unprotected(path: str) -> None:
    assert not _real_boundary().is_non_ff(path)


def test_every_real_ff_private_glob_matches_a_tracked_file() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split("\n")
    unmatched = [
        pattern
        for pattern in _real_boundary().ff_private
        if not any(fnmatch.fnmatchcase(path, pattern) for path in tracked)
    ]
    assert unmatched == []


def test_retired_ff_globs_match_no_current_file() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split("\n")
    revived = [
        path
        for path in tracked
        for pattern in _real_boundary().ff_private_retired
        if fnmatch.fnmatchcase(path, pattern)
    ]
    assert revived == []


def test_real_config_protects_itself() -> None:
    assert _real_boundary().is_non_ff(lane.CONFIG_RELATIVE)


# --- review item 1: merge commits are checked against the first parent -----------------


def _branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def _merge_without_commit(repo: Path, side_path: str, side_text: str) -> None:
    main = _branch(repo)
    _git(repo, "checkout", "-q", "-b", "side")
    _stage(repo, side_path, side_text)
    _git(repo, "commit", "-q", "-m", f"side\n\n{lane.TRAILER} side branch")
    _git(repo, "checkout", "-q", main)
    _stage(repo, FF_INC, "float ff_value() { return 7.0; }\n")
    _git(repo, "commit", "-q", "-m", "feat: FF on main")
    _git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    assert (Path(_git(repo, "rev-parse", "--absolute-git-dir").strip()) / "MERGE_HEAD").exists()


def test_merge_that_brings_a_non_ff_change_needs_the_trailer(repo: Path) -> None:
    _merge_without_commit(repo, SHARED_INC, "float shared_value() { return 0.75; }\n")
    errors = lane.staged_errors("Merge branch 'side'\n", base_dir=repo)
    assert any(SHARED_INC in error for error in errors)
    admitted = f"Merge branch 'side'\n\n{lane.TRAILER} merge Product shoreline repair\n"
    assert lane.staged_errors(admitted, base_dir=repo) == []


def test_merge_of_ff_only_work_needs_no_trailer(repo: Path) -> None:
    _merge_without_commit(repo, FF_GDINC, "float ff_tone() { return 0.125; }\n")
    assert lane.staged_errors("Merge branch 'side'\n", base_dir=repo) == []


def test_merge_that_makes_a_non_ff_shader_include_ff_code_is_refused(repo: Path) -> None:
    _merge_without_commit(
        repo,
        NONFF_ROOT,
        '#[compute]\n#include "res://render/ascii/composition/true_ff_core.glslinc"\nvoid main() {}\n',
    )
    errors = lane.staged_errors(f"Merge\n\n{lane.TRAILER} x\n", base_dir=repo)
    assert any("includes FF-private" in error for error in errors)


# --- review item 2: HEAD or the HEAD config is unavailable -> fail closed ------------------


@pytest.fixture()
def unborn(tmp_path: Path) -> Path:
    repo = tmp_path / "unborn"
    repo.mkdir()
    _git(repo, "init", "-q")
    _stage(repo, lane.CONFIG_RELATIVE, json.dumps(CONFIG))
    _stage(repo, SHARED_INC, "float shared_value() { return 1.0; }\n")
    _stage(repo, FF_INC, "float ff_value() { return 2.0; }\n")
    return repo


def test_unborn_branch_counts_every_staged_protected_file_as_changed(unborn: Path) -> None:
    errors = lane.staged_errors("init\n", base_dir=unborn)
    assert len(errors) == 1
    assert SHARED_INC in errors[0] and lane.CONFIG_RELATIVE in errors[0]
    assert FF_INC not in errors[0]


def test_unborn_branch_with_trailer_is_admitted(unborn: Path) -> None:
    assert lane.staged_errors(f"init\n\n{lane.TRAILER} initial import\n", base_dir=unborn) == []


def test_unborn_branch_still_refuses_ff_includes(unborn: Path) -> None:
    _stage(unborn, NONFF_ROOT, '#include "res://render/ascii/composition/true_ff_core.glslinc"\n')
    errors = lane.staged_errors(f"init\n\n{lane.TRAILER} initial import\n", base_dir=unborn)
    assert any("includes FF-private" in error for error in errors)


def test_config_absent_at_head_counts_every_staged_protected_file_as_changed(tmp_path: Path) -> None:
    repo = tmp_path / "noconfig"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _stage(repo, SHARED_INC, "float shared_value() { return 1.0; }\n")
    _git(repo, "commit", "-q", "-m", "base")
    _stage(repo, lane.CONFIG_RELATIVE, json.dumps(CONFIG))
    # A comment-only edit: with a HEAD config it would not count.
    _stage(repo, SHARED_INC, "// note\nfloat shared_value() { return 1.0; }\n")
    errors = lane.staged_errors("chore: lane config\n", base_dir=repo)
    assert len(errors) == 1 and SHARED_INC in errors[0]


def test_malformed_config_at_head_is_an_error_even_with_trailer(repo: Path) -> None:
    _stage(repo, lane.CONFIG_RELATIVE, "{not json")
    _git(repo, "commit", "-q", "-m", "break config")
    _stage(repo, lane.CONFIG_RELATIVE, json.dumps(CONFIG))
    errors = lane.staged_errors(f"fix\n\n{lane.TRAILER} repair config\n", base_dir=repo)
    assert any("unreadable at HEAD" in error for error in errors)


def test_config_removed_from_index_is_refused_even_with_trailer(repo: Path) -> None:
    _git(repo, "rm", "-q", "--cached", lane.CONFIG_RELATIVE)  # the worktree copy remains
    errors = lane.staged_errors(f"chore\n\n{lane.TRAILER} drop\n", base_dir=repo)
    assert any("unreadable in the index" in error for error in errors)


def test_malformed_config_in_index_is_refused_even_with_trailer(repo: Path) -> None:
    _stage(repo, lane.CONFIG_RELATIVE, json.dumps(dict(CONFIG, schema="other")))
    errors = lane.staged_errors(f"chore\n\n{lane.TRAILER} drop\n", base_dir=repo)
    assert any("unreadable in the index" in error for error in errors)


# --- review item 3: Godot shader language (.gdshader / .gdshaderinc) --------------------


def test_gdshader_code_change_is_refused_without_trailer(repo: Path) -> None:
    _stage(repo, SPATIAL, SPATIAL_TEXT.replace("terrain_tone()", "terrain_tone() * 0.5"))
    errors = lane.staged_errors("fix: FF terrain\n", base_dir=repo)
    assert len(errors) == 1 and SPATIAL in errors[0]


def test_gdshader_comment_only_change_is_not_a_change(repo: Path) -> None:
    _stage(repo, SPATIAL, "// FL-4877 note\n/* block */\n" + SPATIAL_TEXT.replace("; ", ";   "))
    assert lane.staged_errors("docs: comment\n", base_dir=repo) == []


def test_gdshader_including_ff_private_file_by_res_path_is_refused_even_with_trailer(repo: Path) -> None:
    _stage(
        repo,
        SPATIAL_INC,
        '#include "res://render/ascii/composition/true_ff_material.gdshaderinc"\n'
        "float terrain_tone() { return ff_tone(); }\n",
    )
    errors = lane.staged_errors(f"fix\n\n{lane.TRAILER} intended\n", base_dir=repo)
    assert len(errors) == 1 and "includes FF-private" in errors[0]
    assert SPATIAL in errors[0] and FF_GDINC in errors[0]


def test_gdshader_relative_include_is_resolved(repo: Path) -> None:
    relative = "godot_project/render/ascii/material_probe.gdshader"
    _stage(
        repo,
        relative,
        'shader_type spatial;\n#include "composition/true_ff_material.gdshaderinc"\nvoid fragment() {}\n',
    )
    errors = lane.staged_errors(f"feat\n\n{lane.TRAILER} intended\n", base_dir=repo)
    assert len(errors) == 1 and relative in errors[0] and FF_GDINC in errors[0]


def test_gdshader_missing_include_is_unresolved(repo: Path) -> None:
    _stage(repo, SPATIAL, SPATIAL_TEXT.replace("terrain_common", "missing_common"))
    errors = lane.staged_errors(f"fix\n\n{lane.TRAILER} x\n", base_dir=repo)
    assert any("include closure is unresolved" in error for error in errors)


def test_lane_diff_expands_gdshader_includes(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _stage(repo, SPATIAL_INC, "float terrain_tone() { return 0.4; }\n")
    _git(repo, "commit", "-q", "-m", "fix: terrain")
    receipt = lane.lane_diff(base, "HEAD", base_dir=repo)
    assert [row["path"] for row in receipt["changed_files"]] == [SPATIAL_INC]
    assert {row["shader"] for row in receipt["changed_expanded_shaders"]} == {SPATIAL, SPATIAL_INC}


# --- review item 4: classification changes that remove protection ------------------------


def _stage_config(repo: Path, **changes: object) -> None:
    _stage(repo, lane.CONFIG_RELATIVE, json.dumps(dict(CONFIG, **changes)))


@pytest.mark.parametrize(
    ("changes", "unprotected"),
    [
        ({"ff_private": CONFIG["ff_private"] + [OTHER_INC]}, OTHER_INC),
        ({"excluded": CONFIG["excluded"] + ["godot_project/render/core/*"]}, SHARED_INC),
        (
            {"protected": [item for item in CONFIG["protected"] if item != "godot_project/*.glslinc"]},
            OTHER_INC,
        ),
        (
            {"protected": [item for item in CONFIG["protected"] if item != "godot_project/*.gdshader"]},
            SPATIAL,
        ),
    ],
    ids=["add-to-ff-private", "move-to-excluded", "delete-glslinc-glob", "delete-gdshader-glob"],
)
def test_config_that_removes_protection_names_each_file_and_needs_trailer(
    repo: Path, changes: dict, unprotected: str
) -> None:
    _stage_config(repo, **changes)
    errors = lane.staged_errors("chore: lane\n", base_dir=repo)
    assert len(errors) == 1 and unprotected in errors[0] and "protection removed" in errors[0]
    assert lane.staged_errors(f"chore\n\n{lane.TRAILER} reclassify\n", base_dir=repo) == []


def test_reclassified_file_changed_in_the_same_commit_is_still_a_change(repo: Path) -> None:
    _stage_config(repo, ff_private=CONFIG["ff_private"] + [OTHER_INC])
    _stage(repo, OTHER_INC, "float water_value() { return 0.1; }\n")
    errors = lane.staged_errors("feat: FF\n", base_dir=repo)
    assert len(errors) == 1 and OTHER_INC in errors[0]


def test_moving_an_included_file_into_ff_private_breaks_the_includer(repo: Path) -> None:
    _stage_config(repo, ff_private=CONFIG["ff_private"] + [SHARED_INC])
    errors = lane.staged_errors(f"chore\n\n{lane.TRAILER} reclassify\n", base_dir=repo)
    assert len(errors) == 1 and f"{NONFF_ROOT} includes FF-private {SHARED_INC}" in errors[0]


def test_retired_glob_matching_a_live_file_is_refused_even_with_trailer(repo: Path) -> None:
    _stage_config(repo, ff_private_retired=["godot_project/scripts/water/*"])
    errors = lane.staged_errors(f"chore\n\n{lane.TRAILER} retire\n", base_dir=repo)
    assert len(errors) == 1 and "ff_private_retired" in errors[0] and OTHER_INC in errors[0]
    assert lane.staged_errors("chore\n", base_dir=repo)  # and without the trailer


def test_new_file_on_a_retired_ff_path_is_refused(repo: Path) -> None:
    _stage_config(repo, ff_private_retired=["godot_project/render/old_ff/*"])
    _git(repo, "commit", "-q", "-m", f"retire\n\n{lane.TRAILER} retire")
    _stage(repo, "godot_project/render/old_ff/revived.gd", "extends Node\n")
    errors = lane.staged_errors("feat: FF\n", base_dir=repo)
    assert any("ff_private_retired" in error for error in errors)


def test_deleting_a_file_on_a_retired_ff_path_is_allowed(repo: Path) -> None:
    _stage(repo, "godot_project/render/old_ff/stale.gd", "extends Node\n")
    _git(repo, "commit", "-q", "-m", f"add\n\n{lane.TRAILER} fixture")
    _stage_config(repo, ff_private_retired=["godot_project/render/old_ff/*"])
    _git(repo, "rm", "-q", "godot_project/render/old_ff/stale.gd")
    assert lane.staged_errors(f"retire\n\n{lane.TRAILER} retire old FF\n", base_dir=repo) == []
    _git(repo, "commit", "-q", "-m", "retire")
    _stage(repo, "godot_project/render/old_ff/other.gd", "extends Node\n")
    _git(repo, "commit", "-q", "--no-verify", "-m", f"revive\n\n{lane.TRAILER} x")
    _git(repo, "rm", "-q", "godot_project/render/old_ff/other.gd")
    assert lane.staged_errors("chore: remove revived FF file\n", base_dir=repo) == []


def test_config_that_unprotects_itself_is_refused_even_with_trailer(repo: Path) -> None:
    _stage_config(
        repo, protected=[item for item in CONFIG["protected"] if item != lane.CONFIG_RELATIVE]
    )
    errors = lane.staged_errors(f"chore\n\n{lane.TRAILER} x\n", base_dir=repo)
    assert any("must protect itself" in error for error in errors)


# --- review item 7: normalization ignores comments, not meaning ---------------------------


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        (SHARED_INC, "float shared_value() { return 1.0; }\n", '#define LABEL "a"\nfloat shared_value() { return 1.0; }\n'),
        (NONFF_ROOT, None, '#[compute]\n#include "res://render/core/shared.glslinc"\n#define TILE 16\nvoid main() {}\n'),
        (NONFF_ROOT, None, '#[compute]\n#include "res://scripts/water/water_common.glslinc"\nvoid main() {}\n'),
        (SPATIAL, None, SPATIAL_TEXT.replace('#include "res://visual', '#include "res://visual/terrain/../')),
    ],
    ids=["new-define-with-string", "define-added", "include-path-changed", "gdshader-include-path-spelling"],
)
def test_semantic_edits_count_as_changes(repo: Path, path: str, before: str | None, after: str) -> None:
    _stage(repo, path, after)
    errors = lane.staged_errors("fix: FF\n", base_dir=repo)
    assert len(errors) == 1 and path in errors[0]


def test_string_literal_and_define_value_changes_count(repo: Path) -> None:
    _stage(repo, SHARED_INC, '#define LABEL "a"\n#define TILE 16\nfloat shared_value() { return 1.0; }\n')
    _git(repo, "commit", "-q", "-m", f"base2\n\n{lane.TRAILER} fixture")
    for text in (
        '#define LABEL "b"\n#define TILE 16\nfloat shared_value() { return 1.0; }\n',
        '#define LABEL "a"\n#define TILE 8\nfloat shared_value() { return 1.0; }\n',
        '#define LABEL "a "\n#define TILE 16\nfloat shared_value() { return 1.0; }\n',
    ):
        _stage(repo, SHARED_INC, text)
        errors = lane.staged_errors("fix: FF\n", base_dir=repo)
        assert len(errors) == 1 and SHARED_INC in errors[0], text


# --- review item 6: the real commit-msg hook, end to end --------------------------------


GUARD_FILES = (
    ".githooks/commit-msg",
    "scripts/git_commit_policy_guard.py",
    "scripts/render_lane_boundary.py",
    "scripts/godot_feature_regression_gate.py",
    "scripts/maintainer/hooks/doc_placement_policy.py",
    lane.CONFIG_RELATIVE,  # the real lane config
)


@pytest.fixture()
def hooked(tmp_path: Path) -> Path:
    repo = tmp_path / "hooked"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    for relative in GUARD_FILES:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, repo / relative)
    _write(repo, "config/godot_feature_regressions.json", json.dumps({"features": []}))
    _write(repo, "godot_project/ansi_terrain.gdshader", "shader_type spatial;\nvoid fragment() { ALBEDO = vec3(0.5); }\n")
    _write(repo, "godot_project/render/ascii/structure/true_ff_structure_pass.glsl", "#[compute]\nvoid main() {}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")  # hooks are not installed yet
    _git(repo, "config", "core.hooksPath", ".githooks")
    return repo


def _commit(repo: Path, message: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_AUTHOR", "GIT_COMMITTER", "GIT_DIR", "GIT_INDEX", "GIT_WORK_TREE"))
    }
    return subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=repo, capture_output=True, text=True, env=env
    )


def test_commit_msg_hook_refuses_and_admits_through_the_real_guard(hooked: Path) -> None:
    head = _git(hooked, "rev-parse", "HEAD").strip()
    _stage(hooked, "godot_project/ansi_terrain.gdshader", "shader_type spatial;\nvoid fragment() { ALBEDO = vec3(0.25); }\n")
    refused = _commit(hooked, "fix(FL-4877): True FF terrain tone")
    assert refused.returncode != 0
    assert "GIT GUARD BLOCKED (FL-4877)" in refused.stderr
    assert "godot_project/ansi_terrain.gdshader" in refused.stderr
    assert _git(hooked, "rev-parse", "HEAD").strip() == head

    admitted = _commit(hooked, f"fix: terrain tone\n\n{lane.TRAILER} Product terrain tone")
    assert admitted.returncode == 0, admitted.stderr
    assert _git(hooked, "rev-parse", "HEAD").strip() != head


def test_commit_msg_hook_admits_ff_private_work_without_trailer(hooked: Path) -> None:
    _stage(
        hooked,
        "godot_project/render/ascii/structure/true_ff_structure_pass.glsl",
        "#[compute]\nvoid main() { int x = 1; }\n",
    )
    result = _commit(hooked, "feat(FL-4877): True FF structure pass")
    assert result.returncode == 0, result.stderr


def test_commit_msg_hook_refuses_ff_include_even_with_trailer(hooked: Path) -> None:
    _stage(
        hooked,
        "godot_project/ansi_terrain.gdshader",
        'shader_type spatial;\n#include "res://render/ascii/structure/true_ff_structure_pass.glsl"\n'
        "void fragment() {}\n",
    )
    result = _commit(hooked, f"fix: terrain\n\n{lane.TRAILER} reuse FF code")
    assert result.returncode != 0 and "includes FF-private" in result.stderr


# === Round 2 A: Godot-faithful include directive scan =====================================
#
# Godot 4.6 ShaderPreprocessor (servers/rendering/shader_preprocessor.cpp) removes // and
# /* */ comments first (CommentRemover::strip keeps newlines, inserts nothing), then treats a
# '#' preceded on its line only by blanks as a directive.  So a comment before '#include' does
# not hide it, and an '#include' inside a block comment is not an include.

FF_GDINC_RES = "res://render/ascii/composition/true_ff_material.gdshaderinc"


def _index_closure_violations(repo: Path, paths: list[str], config: dict | None = None) -> list[str]:
    boundary = lane.load_boundary(json.dumps(config or CONFIG))
    with lane.gate.FeatureFingerprinter(repo) as fingerprinter:
        return lane.closure_violations(fingerprinter, fingerprinter.reader(":"), boundary, paths)


@pytest.mark.parametrize(
    "include_block",
    [
        f'/* note */ #include "{FF_GDINC_RES}"',
        f'/* a note\n   that ends here */ #include "{FF_GDINC_RES}"',
        f'#  include "{FF_GDINC_RES}"',
        f'#\\\ninclude "{FF_GDINC_RES}"',
        f'#/**/include "{FF_GDINC_RES}"',
        f'float x_value() {{ return 1.0; }}\n  \t#include "{FF_GDINC_RES}" // trailing',
    ],
    ids=["block-comment-same-line", "multiline-block-comment", "spaced-directive", "continuation",
         "comment-inside-directive", "indented-after-code-line"],
)
def test_gdshader_hidden_include_still_reaches_ff_code(repo: Path, include_block: str) -> None:
    _stage(repo, SPATIAL, f"shader_type spatial;\n{include_block}\nvoid fragment() {{}}\n")
    errors = lane.staged_errors(f"fix\n\n{lane.TRAILER} intended\n", base_dir=repo)
    assert len(errors) == 1 and "includes FF-private" in errors[0] and FF_GDINC in errors[0]
    violations = _index_closure_violations(repo, [SPATIAL])
    assert len(violations) == 1 and FF_GDINC in violations[0]


@pytest.mark.parametrize(
    "include_block",
    [
        f'/*\n#include "{FF_GDINC_RES}"\n*/',
        f'// #include "{FF_GDINC_RES}"',
        f'/* #include "{FF_GDINC_RES}" */',
    ],
    ids=["inside-block-comment", "line-comment", "one-line-block-comment"],
)
def test_gdshader_commented_out_include_is_not_an_include(repo: Path, include_block: str) -> None:
    _stage(repo, SPATIAL, f"shader_type spatial;\n{include_block}\nvoid fragment() {{}}\n")
    assert lane.staged_errors(f"fix\n\n{lane.TRAILER} intended\n", base_dir=repo) == []
    assert _index_closure_violations(repo, [SPATIAL]) == []


def test_gdshader_include_path_with_double_slash_is_not_a_comment(repo: Path) -> None:
    # res:// contains '//'; Godot's comment remover skips string contents.
    _stage(repo, SPATIAL, f'shader_type spatial;\n#include "{FF_GDINC_RES}"\n')
    assert FF_GDINC in _index_closure_violations(repo, [SPATIAL])[0]


@pytest.mark.parametrize(
    "line",
    [
        '/* note */ #include "res://render/core/shared.glslinc"',
        '#  include "res://render/core/shared.glslinc"',
    ],
    ids=["comment-prefixed", "spaced"],
)
def test_glsl_include_the_loader_will_not_expand_is_refused(repo: Path, line: str) -> None:
    _stage(repo, NONFF_ROOT, f"#[compute]\n{line}\nvoid main() {{}}\n")
    errors = lane.staged_errors(f"fix\n\n{lane.TRAILER} intended\n", base_dir=repo)
    assert len(errors) == 1 and "the loader will not expand this include" in errors[0]
    assert f"{NONFF_ROOT}:2" in errors[0]
    violations = _index_closure_violations(repo, [NONFF_ROOT])
    assert len(violations) == 1 and "the loader will not expand" in violations[0]


def test_glsl_commented_out_include_is_allowed(repo: Path) -> None:
    _stage(repo, NONFF_ROOT, '#[compute]\n// #include "res://render/core/missing.glslinc"\nvoid main() {}\n')
    assert lane.staged_errors(f"fix\n\n{lane.TRAILER} intended\n", base_dir=repo) == []


# === Round 2 B: host-file coverage by responsibility =======================================

MAIN = "godot_project/scripts/Main.gd"
WORLD = "godot_project/scripts/World.gd"
PROJECT = "godot_project/project.godot"
MAIN_TEXT = (
    "extends Node3D\n"
    "const CELL := 8\n"
    "const UNUSED := 1\n"
    "var world = null\n"
    "\n"
    "func _render_hook() -> void:\n"
    "\tworld.set_cell(CELL)\n"
    "\n"
    "func _gameplay() -> void:\n"
    '\tprint("x")\n'
)
PROJECT_TEXT = (
    "; Engine configuration file.\n"
    "config_version=5\n"
    "\n[application]\n\n"
    'config/name="x"\n'
    "\n[display]\n\n"
    "window/size/viewport_width=848\n"
    'window/stretch/mode="canvas_items"\n'
    "\n[input]\n\n"
    'jump={\n"deadzone": 0.5,\n"events": []\n}\n'
    "\n[rendering]\n\n"
    "environment/defaults/default_clear_color=Color(0.4, 0.4, 0.75, 1)\n"
)
HOST_CONFIG = dict(
    CONFIG,
    protected_definitions={MAIN: {"_render_hook": "sets the compositor cell size"}},
    protected_settings={
        PROJECT: {
            "rendering/*": "renderer settings",
            "shader_globals/*": "global shader uniforms",
            "display/window/size/*": "viewport size",
            "display/window/stretch/*": "stretch mode",
        }
    },
)


@pytest.fixture()
def host(repo: Path) -> Path:
    _stage(repo, lane.CONFIG_RELATIVE, json.dumps(HOST_CONFIG))
    _stage(repo, MAIN, MAIN_TEXT)
    _stage(repo, PROJECT, PROJECT_TEXT)
    _git(repo, "commit", "-q", "-m", f"host\n\n{lane.TRAILER} fixture")
    return repo


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("\tworld.set_cell(CELL)\n", "\tworld.set_cell(CELL * 2)\n"),
        ("const CELL := 8\n", "const CELL := 16\n"),
    ],
    ids=["body", "referenced-constant"],
)
def test_protected_definition_change_needs_trailer(host: Path, old: str, new: str) -> None:
    _stage(host, MAIN, MAIN_TEXT.replace(old, new))
    errors = lane.staged_errors("feat: FF\n", base_dir=host)
    assert len(errors) == 1 and f"{MAIN}::_render_hook" in errors[0]
    assert lane.staged_errors(f"fix\n\n{lane.TRAILER} cell size\n", base_dir=host) == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('\tprint("x")\n', '\tprint("y")\n'),
        ("const UNUSED := 1\n", "const UNUSED := 2\n"),
        ("\tworld.set_cell(CELL)\n", "\t# FL-4877 note\n\tworld.set_cell( CELL )  # why\n"),
    ],
    ids=["unprotected-function", "unreferenced-constant", "comment-only"],
)
def test_other_host_edits_need_no_trailer(host: Path, old: str, new: str) -> None:
    _stage(host, MAIN, MAIN_TEXT.replace(old, new))
    assert lane.staged_errors("feat: gameplay\n", base_dir=host) == []


@pytest.mark.parametrize(
    "text",
    [MAIN_TEXT.replace("_render_hook", "_render_hook_v2"), MAIN_TEXT.replace("func _render_hook() -> void:\n\tworld.set_cell(CELL)\n", "")],
    ids=["renamed", "deleted"],
)
def test_missing_protected_definition_is_refused_even_with_trailer(host: Path, text: str) -> None:
    _stage(host, MAIN, text)
    errors = lane.staged_errors(f"refactor\n\n{lane.TRAILER} rename\n", base_dir=host)
    assert any("_render_hook" in error and "no longer exists" in error for error in errors)
    report = lane.integrity_report(host, revision=":")
    assert report["ok"] is False and any("_render_hook" in row for row in report["missing_definitions"])


def test_rename_with_config_update_needs_the_trailer(host: Path) -> None:
    _stage(host, MAIN, MAIN_TEXT.replace("_render_hook", "_render_hook_v2"))
    config = dict(HOST_CONFIG, protected_definitions={MAIN: {"_render_hook_v2": "sets the compositor cell size"}})
    _stage(host, lane.CONFIG_RELATIVE, json.dumps(config))
    errors = lane.staged_errors("refactor: rename\n", base_dir=host)
    assert len(errors) == 1 and f"{MAIN}::_render_hook" in errors[0]
    assert lane.staged_errors(f"refactor\n\n{lane.TRAILER} rename\n", base_dir=host) == []


def test_dropping_a_protected_definition_from_config_needs_the_trailer(host: Path) -> None:
    _stage(host, lane.CONFIG_RELATIVE, json.dumps(dict(HOST_CONFIG, protected_definitions={})))
    errors = lane.staged_errors("chore: lane\n", base_dir=host)
    assert len(errors) == 1 and f"{MAIN}::_render_hook (protection removed" in errors[0]


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("window/size/viewport_width=848", "window/size/viewport_width=640", "display/window/size/viewport_width"),
        ("default_clear_color=Color(0.4, 0.4, 0.75, 1)\n", "default_clear_color=Color(0.4, 0.4, 0.75, 1)\nanti_aliasing/quality/msaa_3d=2\n", "rendering/anti_aliasing/quality/msaa_3d"),
        ("\n[rendering]", '\n[shader_globals]\n\nwind={\n"type": "vec2",\n"value": Vector2(1, 0)\n}\n\n[rendering]', "shader_globals/wind"),
    ],
    ids=["viewport-size", "new-rendering-key", "new-shader-global"],
)
def test_protected_setting_change_needs_trailer(host: Path, old: str, new: str, key: str) -> None:
    _stage(host, PROJECT, PROJECT_TEXT.replace(old, new))
    errors = lane.staged_errors("feat: FF\n", base_dir=host)
    assert len(errors) == 1 and f"{PROJECT}::{key}" in errors[0]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('config/name="x"', 'config/name="y"'),
        ('"deadzone": 0.5', '"deadzone": 0.2'),
        ("; Engine configuration file.", "; Engine configuration file, edited."),
        ("window/size/viewport_width=848", "window/size/viewport_width = 848"),
    ],
    ids=["application-name", "input-map", "comment", "whitespace"],
)
def test_unrelated_setting_change_needs_no_trailer(host: Path, old: str, new: str) -> None:
    _stage(host, PROJECT, PROJECT_TEXT.replace(old, new))
    assert lane.staged_errors("feat: input\n", base_dir=host) == []


def test_ff_private_preload_from_a_host_file_is_refused_even_with_trailer(host: Path) -> None:
    ff = "res://render/ascii/composition/true_ff_material.gdshaderinc"
    _stage(host, MAIN, MAIN_TEXT.replace("const UNUSED := 1\n", f'const FF := preload("{ff}")\n'))
    errors = lane.staged_errors(f"feat\n\n{lane.TRAILER} x\n", base_dir=host)
    assert len(errors) == 1 and "FF-private" in errors[0] and f"{MAIN}:3" in errors[0]


def test_ff_private_path_in_a_host_comment_is_allowed(host: Path) -> None:
    ff = "res://render/ascii/composition/true_ff_material.gdshaderinc"
    _stage(host, MAIN, MAIN_TEXT.replace("const UNUSED := 1\n", f"const UNUSED := 1  # was {ff}\n"))
    assert lane.staged_errors("docs: note\n", base_dir=host) == []


def test_integrity_report_passes_on_a_consistent_tree(host: Path) -> None:
    assert lane.integrity_report(host, revision="HEAD")["ok"] is True


def test_lane_diff_reports_host_definitions_and_settings_with_the_commit(host: Path) -> None:
    base = _git(host, "rev-parse", "HEAD").strip()
    _stage(host, MAIN, MAIN_TEXT.replace("set_cell(CELL)", "set_cell(CELL + 1)").replace('print("x")', 'print("z")'))
    _git(host, "commit", "-q", "-m", "feat: True FF cell")
    _stage(host, PROJECT, PROJECT_TEXT.replace("848", "640").replace('config/name="x"', 'config/name="q"'))
    _git(host, "commit", "-q", "-m", "chore: window")
    receipt = lane.lane_diff(base, "HEAD", base_dir=host)
    rows = {(row["path"], row["name"]): row for row in receipt["changed_host_entries"]}
    assert set(rows) == {(MAIN, "_render_hook"), (PROJECT, "display/window/size/viewport_width")}
    assert [commit.split(" ", 1)[1] for commit in rows[(MAIN, "_render_hook")]["commits"]] == ["feat: True FF cell"]
    assert [commit.split(" ", 1)[1] for commit in rows[(PROJECT, "display/window/size/viewport_width")]["commits"]] == ["chore: window"]


def test_real_config_host_entries_exist_at_head() -> None:
    report = lane.integrity_report(ROOT, revision="HEAD", config_text=(ROOT / lane.CONFIG_RELATIVE).read_text(encoding="utf-8"))
    assert report["missing_definitions"] == [] and report["host_ff_private_references"] == []
    config = json.loads((ROOT / lane.CONFIG_RELATIVE).read_text(encoding="utf-8"))
    assert set(config["protected_definitions"]) == {MAIN, WORLD}
    assert set(config["protected_settings"]) == {PROJECT}
    for entries in [*config["protected_definitions"].values(), *config["protected_settings"].values()]:
        assert all(isinstance(reason, str) and len(reason) > 10 for reason in entries.values())
