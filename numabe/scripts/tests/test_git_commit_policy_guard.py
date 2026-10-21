#!/usr/bin/env python3
"""Regression tests for the tracked local git commit policy guard."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import git_commit_policy_guard as guard

# The FL-4512 paper gate re-runs this suite inside a snapshot that exports only
# paper-owned paths, so scripts/maintainer/hooks/ is absent there. The placement
# rule lives in that tree on purpose - one definition, imported by both doors -
# so these cases skip when it is out of reach rather than being duplicated into
# a second copy that can drift, which is the exact bug this change removes.
# A full checkout always has it, so they always run here.
_POLICY_PRESENT = (
    Path(__file__).resolve().parents[2]
    / "scripts/maintainer/hooks/doc_placement_policy.py"
).is_file()
_needs_policy = pytest.mark.skipif(
    not _POLICY_PRESENT, reason="hook tree absent in this checkout"
)


TRACKED_FIXTURE = {
    ".githooks/commit-msg",
    ".githooks/pre-commit",
    ".githooks/pre-push",
    ".github/workflows/fl4512-paper.yml",
    "assets/glyphs/generated/fl4216_shoreline_wave_receipts.json",
    "docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex",
    "docs/agent/glyph-rendering-paper/2026-06-30-fl4512-source-accurate-rendering-pipeline-callgraph.md",
    "docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.bbl",
    "docs/agent/glyph-rendering-paper/PAPER_ARTIFACT_INDEX.md",
    "docs/agent/glyph-rendering-paper/README.md",
    "docs/agent/glyph-rendering-paper/figure_dispositions.v1.json",
    "docs/agent/glyph-rendering-paper/figure_provenance/fig_direction_vocab_grid.json",
    "docs/agent/glyph-rendering-paper/figure_provenance/fig_pipeline_callgraph.json",
    "docs/agent/glyph-rendering-paper/figures/panel.png",
    "docs/agent/glyph-rendering-paper/references.bib",
    "docs/agent/glyph-rendering-paper/scripts/build_versioned_paper.py",
    "docs/agent/glyph-rendering-paper/scripts/validate_fl4512_paper_lane.py",
    "docs/plans/2026-03-22-multiplayer-canonical-spec.md",
    "docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv",
    "docs/research/ascii/verification/fl4260/fl4548/2026-07-10-direction-relief-strict/direction_before.png",
    "docs/research/ascii/verification/fl4260/fl4548/2026-07-13-pieta-edge-yaw-observation/review-panel-paired-v3.png",
    "docs/research/ascii/verification/fl4260/fl4548/2026-07-13-pieta-edge-yaw-observation/review-panel-paired-v3.provenance.json",
    "docs/research/ascii/verification/fl4512/decisions/2026-07-11-execution-lock-validation-v1/audit.json",
    "docs/research/ascii/verification/fl4512/decisions/2026-07-11-remaining-v1/receipt.json",
    "docs/research/glyph-rendering/README.md",
    "docs/research/glyph-rendering/references.bib",
    "engine/render/render_resolve.cpp",
    "pyproject.toml",
    "scripts/__init__.py",
    "scripts/fl4512_projected_feature_extent.py",
    "scripts/fl4512_projected_scale.py",
    "scripts/git_commit_policy_guard.py",
    "scripts/tests/test_fl4512_paper_contract.py",
    "scripts/tests/test_fl4512_paper_contracts.py",
    "scripts/tests/test_git_commit_policy_guard.py",
    "scripts/validate_fl4512_paper_lane.py",
}


@pytest.fixture(autouse=True)
def tracked_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    def tracked(prefix: str) -> list[str]:
        prefix = prefix.removeprefix("./")
        if prefix in {"", "."}:
            return sorted(TRACKED_FIXTURE)
        return sorted(path for path in TRACKED_FIXTURE if path.startswith(prefix))

    monkeypatch.setattr(guard, "tracked_paths_under", tracked)


def _blob_loader(_: str) -> str:
    if _ == "docs/agent/glyph-rendering-paper/figure_provenance/fig_pipeline_callgraph.json":
        return """
        {
          "input_artifact_sha256s": [
            {"path": "engine/render/render_resolve.cpp", "sha256": "abc"}
          ]
        }
        """
    return "ordinary staged text\n"


def _snapshot_builder(path: Path, paths: list[str] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _snapshot_builder_with_paths(calls: list[tuple[Path, list[str]]]):
    def build(path: Path, paths: list[str]) -> None:
        path.mkdir(parents=True, exist_ok=True)
        calls.append((path, list(paths)))

    return build


@pytest.mark.parametrize(
    ("remote_name", "remote_url"),
    [
        ("upstream", "https://github.com/msokalski/asciicker.git"),
        ("source", "git@github.com:msokalski/asciicker.git"),
        ("source", "ssh://git@github.com/msokalski/asciicker"),
    ],
)
def test_pre_push_rejects_original_repository(remote_name: str, remote_url: str) -> None:
    error = guard.pre_push_error(remote_name, remote_url)
    assert error is not None
    assert "FL-3856" in error


def test_pre_push_allows_origin() -> None:
    assert guard.pre_push_error(
        "origin", "https://github.com/rikiyanai/asciicker-Y9-2.git"
    ) is None


def test_staged_paths_keeps_nul_delimited_non_ascii_names(monkeypatch) -> None:
    """The guard must not regress to Git's C-quoted line-oriented output."""
    expected = "docs/agent/café.md"

    def fake_git(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=expected + "\0", stderr="")

    monkeypatch.setattr(guard, "_git", fake_git)
    assert guard.staged_paths() == [expected]


def test_path_fl_owner_requires_the_render_owner() -> None:
    error = guard.path_fl_owner_error(
        ["godot_project/render/ascii/composition/example.glsl"],
        "fix: renderer research\n\nGodot-Feature-Impact: none\nFL-4512",
    )
    assert error is not None and "FL-4877" in error
    assert guard.path_fl_owner_error(
        ["godot_project/render/ascii/composition/example.glsl"],
        "fix: renderer\n\nFL-4877",
    ) is None


def test_path_fl_owner_requires_the_feature_registry_owner() -> None:
    error = guard.path_fl_owner_error(
        ["config/godot_feature_regressions.json"], "chore: registry\n\nFL-4512"
    )
    assert error is not None and "FL-4781" in error


def test_fl4512_paper_content_commit_runs_prebuild_gate_against_index_snapshot() -> None:
    calls: list[dict] = []
    snapshots: list[tuple[Path, list[str]]] = []

    def blob_loader(path: str) -> str:
        if path == "docs/agent/glyph-rendering-paper/figure_provenance/fig_direction_vocab_grid.json":
            return """
            {
              "input_artifact_sha256s": [
                {"path": "assets/glyphs/generated/fl4216_shoreline_wave_receipts.json", "sha256": "abc"},
                {"path": "docs/research/ascii/verification/fl4260/fl4548/2026-07-10-direction-relief-strict/direction_before.png", "sha256": "def"}
              ]
            }
            """
        return _blob_loader(path)

    def runner(args, **kwargs):
        calls.append({"args": list(args), "kwargs": kwargs})
        return SimpleNamespace(returncode=0, stdout="[FL-4512] PAPER HYGIENE PASS\n", stderr="")

    error = guard.pre_commit_error(
        paths=["docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex"],
        blob_loader=blob_loader,
        command_runner=runner,
        snapshot_builder=_snapshot_builder_with_paths(snapshots),
    )

    assert error is None
    snapshot_root = calls[0]["kwargs"]["cwd"]
    assert calls[0]["args"] == [
        guard.sys.executable,
        f"{snapshot_root}/docs/agent/glyph-rendering-paper/scripts/validate_fl4512_paper_lane.py",
        "--prebuild-source",
    ]
    assert snapshot_root.name == "repo"
    assert calls[0]["kwargs"]["env"]["FL4512_PAPER_REPO_ROOT"] == str(snapshot_root)
    assert calls[0]["kwargs"]["env"]["PYTHONPATH"] == str(snapshot_root)
    exported = set(snapshots[0][1])
    assert "docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex" in exported
    assert "assets/glyphs/generated/fl4216_shoreline_wave_receipts.json" in exported
    assert "docs/research/ascii/verification/fl4260/fl4548/2026-07-10-direction-relief-strict/direction_before.png" in exported
    assert ".scratch/upstream-clean-msokalski-asciicker/game_app.cpp" not in exported
    assert len(exported) < 500


def test_fl4512_snapshot_includes_indexed_pieta_edge_yaw_diagnostic_inputs() -> None:
    """The indexed Pietà panel and sidecar must survive the temporary snapshot.

    The artifact-index row cites these exact paths directly. Their names do not
    match the receipt/manifest pattern used by ``add_text_path_refs``, so they
    remain explicit snapshot inputs instead of disappearing in INDEX_SNAPSHOT_MODE.
    """
    paths = guard.fl4512_required_snapshot_paths(
        ["docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex"],
        blob_loader=_blob_loader,
    )
    assert (
        "docs/research/ascii/verification/fl4260/fl4548/2026-07-13-pieta-edge-yaw-observation/"
        "review-panel-paired-v3.png"
    ) in paths
    assert (
        "docs/research/ascii/verification/fl4260/fl4548/2026-07-13-pieta-edge-yaw-observation/"
        "review-panel-paired-v3.provenance.json"
    ) in paths


def test_fl4512_paper_content_commit_fails_when_snapshot_gate_fails() -> None:
    def runner(args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="[FL-4512] PAPER HYGIENE FAILED\n", stderr="")

    error = guard.pre_commit_error(
        paths=["docs/research/glyph-rendering/README.md"],
        blob_loader=_blob_loader,
        command_runner=runner,
        snapshot_builder=_snapshot_builder,
    )

    assert error is not None
    assert "FL-4512 paper staged source gate failed" in error
    assert "--prebuild-source" in error


@pytest.mark.parametrize("dependency_path", [
    # the canonical specification - the paper quotes it, it is not the paper
    "docs/plans/2026-03-22-multiplayer-canonical-spec.md",
    # compiled glyph assets - offline inputs the paper describes
    "assets/glyphs/generated/fl4694.gtr_v2_subsumption.v1.json",
    "assets/glyphs/schema/fl4694.gtr_v2_subsumption.v1.schema.json",
    # runtime evidence receipts cited by the paper
    "docs/research/ascii/verification/fl4652/example/receipt.json",
    "docs/research/ascii/verification/fl4512/decisions/example/receipt.json",
])
def test_fl4512_paper_dependency_commit_does_not_run_prebuild_gate(
    dependency_path: str,
) -> None:
    """A paper DEPENDENCY must not start paper validation.

    CORRECTED BOUNDARY (FL-4716, 2026-08-09). This test previously asserted the
    opposite, and that assertion encoded a real defect.

    The guard states its own rule at the TRIGGER BOUNDARY note: only a
    paper-owned staged path may start paper validation. FL-4665 applied that
    rule to engine sources discovered through figure provenance, but the static
    dependency lists stayed inside the content classifier, so the canonical
    specification and the whole assets/glyphs tree behaved exactly like
    manuscript prose.

    The cost was concrete. On 2026-08-09 a canon route edit and an offline glyph
    measurement were both blocked by a gate that was red for three stale figure
    provenance pins and a build record hash - none of which those commits touch,
    and none of which they could clear by doing their own work correctly. A lane
    that cannot ever satisfy a gate must not be held by it.

    Dependencies remain snapshot inputs; see the companion test below.
    """
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    error = guard.pre_commit_error(
        paths=[dependency_path],
        blob_loader=_blob_loader,
        command_runner=runner,
        snapshot_builder=_snapshot_builder,
    )

    assert error is None
    assert not calls, (
        f"a paper dependency still triggers paper validation: {dependency_path}"
    )


def test_fl4512_provenance_referenced_source_file_does_not_run_prebuild_gate() -> None:
    """An engine source cited by figure provenance must NOT trigger paper validation.

    CORRECTED BOUNDARY (FL-4665, 2026-07-31). This test previously asserted the
    opposite, and that assertion encoded a real defect.

    The paper's figure provenance files list the source artifacts each figure was
    built from. That list is discovered dynamically and includes ordinary engine
    sources — render_resolve.cpp, game_render_bridge.cpp,
    terminal_gl_present.cpp. When mere membership in that discovered set was
    treated as a TRIGGER, editing any of those files ran the entire paper
    validator, so unrelated renderer commits were blocked by the paper lane's
    unpublished provenance (stale figure hashes, a missing receipt, a figure
    input that is not even git-tracked).

    Two separate things were being conflated. The paper builder SHOULD stay red
    until its figures are refreshed. Engine commits should not inherit that red.
    Refreshing the provenance to unblock an engine commit would be worse still:
    it would reseal figure hashes against unfinished source, converting
    work-in-progress into published provenance.

    So discovery now belongs to the SNAPSHOT only — see the companion test
    below, which proves a legitimately triggered run still receives these files.
    """
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    error = guard.pre_commit_error(
        paths=["engine/render/render_resolve.cpp"],
        blob_loader=_blob_loader,
        command_runner=runner,
        snapshot_builder=_snapshot_builder,
    )

    assert error is None
    assert calls == [], (
        "staging a provenance-referenced engine source invoked the paper "
        "validator; engine commits must not be gated on paper provenance"
    )


def test_fl4665_staging_only_terminal_gl_present_does_not_run_paper_validator() -> None:
    """The exact case that blocked the FL-4665 instrumentation commit.

    platform/terminal_gl_present.cpp is an input artifact of
    fig_pipeline_callgraph.json, so editing the renderer changed its hash and
    ran the whole paper validator. The validator was already failing on the
    paper lane's own state, so a renderer-only change could not be committed at
    all. This pins that this specific path never starts paper validation.
    """
    calls: list[list[str]] = []

    def blob_loader(path: str) -> str:
        if path == (
            "docs/agent/glyph-rendering-paper/figure_provenance/"
            "fig_pipeline_callgraph.json"
        ):
            # Mirrors the real figure: it cites engine sources as build inputs.
            return """
            {
              "input_artifact_sha256s": [
                {"path": "platform/terminal_gl_present.cpp", "sha256": "abc"},
                {"path": "engine/render/render_resolve.cpp", "sha256": "def"}
              ]
            }
            """
        return _blob_loader(path)

    def runner(args, **kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    error = guard.pre_commit_error(
        paths=["platform/terminal_gl_present.cpp"],
        blob_loader=blob_loader,
        command_runner=runner,
        snapshot_builder=_snapshot_builder,
    )

    assert error is None
    assert calls == [], (
        "staging only platform/terminal_gl_present.cpp invoked the FL-4512 "
        "paper validator; a renderer-only commit must not be paper-gated"
    )


def test_fl4512_paper_trigger_still_snapshots_discovered_engine_inputs() -> None:
    """Narrowing the trigger must not narrow what a real paper run can see.

    When a genuinely paper-owned path is staged, validation runs AND the
    dynamically discovered inputs must still be exported into the snapshot the
    validator reads — otherwise hash checks would compare against files that
    were never provided. Discovery moved out of the trigger, not out of the
    snapshot.
    """
    calls: list[list[str]] = []
    snapshots: list[tuple[Path, list[str]]] = []

    def blob_loader(path: str) -> str:
        if path == (
            "docs/agent/glyph-rendering-paper/figure_provenance/"
            "fig_pipeline_callgraph.json"
        ):
            return """
            {
              "input_artifact_sha256s": [
                {"path": "platform/terminal_gl_present.cpp", "sha256": "abc"},
                {"path": "engine/render/render_resolve.cpp", "sha256": "def"}
              ]
            }
            """
        return _blob_loader(path)

    def runner(args, **kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def snapshot_builder(root: Path, paths: list[str]) -> None:
        snapshots.append((root, list(paths)))
        _snapshot_builder(root, paths)

    error = guard.pre_commit_error(
        # A manuscript path: unambiguously paper-owned, so validation must run.
        paths=["docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex"],
        blob_loader=blob_loader,
        command_runner=runner,
        snapshot_builder=snapshot_builder,
    )

    assert error is None
    assert calls, "a staged manuscript path must still run the paper validator"
    assert calls[0][2] == "--prebuild-source"
    exported = snapshots[0][1]
    assert "engine/render/render_resolve.cpp" in exported, (
        "a triggered paper run no longer receives its discovered engine inputs"
    )


@pytest.mark.parametrize("paper_owned_path", [
    # manuscript
    "docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex",
    # provenance
    "docs/agent/glyph-rendering-paper/figure_provenance/fig_pipeline_callgraph.json",
    # figure
    "docs/agent/glyph-rendering-paper/figures/panel.png",
    # paper tooling
    "docs/agent/glyph-rendering-paper/scripts/build_versioned_paper.py",
    # NOTE (FL-4716): runtime evidence under
    # docs/research/ascii/verification/fl4512/ used to be listed here as a
    # paper-owned category. It is a dependency - evidence the paper cites, not
    # the paper - and it now appears in the non-triggering test above instead.
])
def test_fl4512_paper_owned_paths_still_trigger_validation(paper_owned_path: str) -> None:
    """Full validation is preserved for every category of paper-owned path.

    The trigger was narrowed to exclude dynamically discovered engine sources
    ONLY. Manuscript, provenance, figure, evidence, and paper-tooling paths must
    all still be gated exactly as before, so this correction cannot be misread
    as weakening the paper lane's own protection.
    """
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    guard.pre_commit_error(
        paths=[paper_owned_path],
        blob_loader=_blob_loader,
        command_runner=runner,
        snapshot_builder=_snapshot_builder,
    )

    assert calls, f"paper-owned path no longer triggers validation: {paper_owned_path}"


def test_fl4512_paper_deleted_content_still_triggers_gate() -> None:
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=1, stdout="missing tracked build artifact\n", stderr="")

    error = guard.pre_commit_error(
        paths=["docs/agent/glyph-rendering-paper/figure_provenance/old.json"],
        blob_loader=lambda _: (_ for _ in ()).throw(RuntimeError("deleted from index")),
        command_runner=runner,
        snapshot_builder=_snapshot_builder,
    )

    assert error is not None
    assert "staged source gate failed" in error
    assert calls


def test_fl4512_paper_tooling_commit_runs_self_tests() -> None:
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="7 passed\n", stderr="")

    error = guard.pre_commit_error(
        paths=["docs/agent/glyph-rendering-paper/scripts/validate_fl4512_paper_lane.py"],
        blob_loader=_blob_loader,
        command_runner=runner,
        snapshot_builder=_snapshot_builder,
    )

    assert error is None
    assert calls == [
        [
            guard.sys.executable,
            "-m",
            "pytest",
            "-q",
            *guard.FL4512_PAPER_SELF_TESTS,
        ]
    ]


def test_fl4512_paper_tooling_commit_fails_when_self_tests_fail() -> None:
    def runner(args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="self-test failed\n", stderr="")

    error = guard.pre_commit_error(
        paths=["scripts/git_commit_policy_guard.py"],
        blob_loader=_blob_loader,
        command_runner=runner,
        snapshot_builder=_snapshot_builder,
    )

    assert error is not None
    assert "enforcement self-tests failed" in error


def test_non_paper_commit_does_not_run_fl4512_gate() -> None:
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=1, stdout="should not run", stderr="")

    error = guard.pre_commit_error(
        paths=["scripts/some_other_tool.py"],
        blob_loader=_blob_loader,
        command_runner=runner,
        snapshot_builder=_snapshot_builder,
    )

    assert error is None
    assert calls == []


# --- Planning-surface governance (operator correction, 2026-09-13) ------------
#
# Product truth has exactly three canonical surfaces. Every other tracked
# roadmap-shaped Markdown file is authority drift: one agent writes a plan, the
# next reads GODOT_GOAL, a third reads the FL log, and three different sequences
# now exist. These tests pin the guard that stops the population growing.


@pytest.mark.parametrize(
    "path",
    [
        "docs/plans/2026-09-13-recovered-agent-plans/2026-09-12-steam-mvp-plan.md",
        "docs/plans/2026-09-13-recovered-agent-plans/RECOVERY.md",
        "docs/plans/roadmap-v2.md",
        "handoff-plan.md",
        "NEXT_STEPS.md",
        "docs/MVP.md",
        "docs/backlog.md",
        "BLOCKGOAL.MD",
        "SHIPGOAL.md",
    ],
)
@_needs_policy
def test_roadmap_shaped_markdown_is_flagged(path: str) -> None:
    assert guard.planning_surface_paths([path]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        # The three canonical surfaces plus routing entry points.
        "docs/plans/2026-03-22-multiplayer-canonical-spec.md",
        "docs/plans/GODOT_GOAL.MD",
        "docs/FAILURE_LOG.md",
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        # Evidence and receipts are not authority.
        "docs/research/ascii/some-plan.md",
        # The Stop-hook scratchpad is scratch by path, never authority
        # (FL-4843 guard must not break FL-0035/FL-0036 preservation).
        "research/scratchpad/2026-09-17-next-steps.md",
        "research/scratchpad/todo.md",
        "artifacts/maintainer/handoffs/session-123/SESSION_HANDOFF.md",
        "articles/foo/handoffs/INDEX.md",
        "docs/agent/glyph-rendering-paper/README.md",
        ".run/scratch/plan.md",
        "docs/research/x/node_modules/plan.md",
        # Not Markdown at all.
        "colyseus_server/src/sim/world.ts",
        "godot_project/scripts/Main.gd",
    ],
)
@_needs_policy
def test_canonical_and_evidence_paths_are_not_flagged(path: str) -> None:
    assert guard.planning_surface_paths([path]) == []


@_needs_policy
def test_new_planning_surface_blocks_commit_without_trailer() -> None:
    error = guard.planning_surface_error(
        ["docs/plans/roadmap-v2.md"], message_text="docs: add a roadmap"
    )
    assert error is not None
    assert "roadmap-v2.md" in error
    assert "New-Canonical-Surface:" in error


@_needs_policy
def test_new_canonical_surface_trailer_authorizes_the_commit() -> None:
    message = (
        "docs: add a roadmap\n"
        "\n"
        "New-Canonical-Surface: the RQ table cannot express cross-lane ordering\n"
    )
    assert guard.planning_surface_error(["docs/plans/roadmap-v2.md"], message) is None


@_needs_policy
def test_guard_is_silent_when_no_planning_surface_is_added() -> None:
    assert guard.planning_surface_error(["godot_project/scripts/Main.gd"], "fix: x") is None


@_needs_policy
def test_commit_msg_error_rejects_unregistered_planning_surface(monkeypatch) -> None:
    monkeypatch.setattr(guard, "commit_identity_error", lambda *a, **k: None)
    monkeypatch.setattr(guard, "added_paths", lambda: ["docs/plans/steam-mvp-plan.md"])
    error = guard.commit_msg_error("docs: capture the plan")
    assert error is not None
    assert "steam-mvp-plan.md" in error


# --- FL-4853: placement, not just roadmap-shaped names -----------------------
#
# The name rule only ever caught paths containing plan/roadmap/goal/etc. A new
# hidden agent-instruction surface (.xx/AGENTS.md) or a blandly named note
# (notes.md) walked straight past it, and .txt was not considered at all.


@pytest.mark.parametrize(
    "path",
    [
        ".xx/AGENTS.md",
        ".xx/notes.md",
        "notes.md",
        "scripts/README.txt",
        "docs/solutions/summary.txt",
        "tools/NOTES.markdown",
        "some/dir/AGENTS.md",
    ],
)
@_needs_policy
def test_generically_named_new_docs_are_flagged(path: str) -> None:
    assert guard.planning_surface_paths([path]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        "README.md",
        "engine/render/README.md",
        "docs/agent/notes.md",
        "docs/agent/deep/notes.txt",
        "apps/asciiid-classic/SKILL.md",
        "artifacts/run-42/report.md",
        ".run/numabe/receipt.txt",
        "docs/research/ascii/source.md",
        "engine/render/minimap_renderer.cpp",
        "config/godot_proof_pose_registry.json",
    ],
)
@_needs_policy
def test_allowed_and_non_doc_paths_are_not_flagged(path: str) -> None:
    assert guard.planning_surface_paths([path]) == []


@_needs_policy
def test_stray_doc_policy_matches_the_md_ban_hook() -> None:
    """Parity pin: the commit door and the tool-call door must agree.

    They are separate implementations on purpose (the git hook must not import
    a Claude hook), so drift is the real risk. Any path the hook forbids
    creating must also be flagged here, and vice versa.
    """
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[2]
    hook_path = root / "scripts/maintainer/hooks/md_file_ban_hook.py"
    if not hook_path.is_file():
        # The FL-4512 paper-index pre-commit stage runs this suite inside a
        # sparse checkout that carries scripts/tests but not the hook tree.
        # Parity is only checkable where both doors exist.
        pytest.skip("hook tree not present in this checkout")
    spec = importlib.util.spec_from_file_location("md_ban_parity", hook_path)
    assert spec is not None and spec.loader is not None
    hook = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = hook
    spec.loader.exec_module(hook)

    import os

    os.environ["CLAUDE_PROJECT_DIR"] = str(root)
    hook._CASE_INSENSITIVE.clear()

    samples = [
        ".xx/AGENTS.md", ".xx/notes.txt", "notes.md", "AGENTS.md", "README.md",
        "engine/README.md", "docs/agent/x.md", "docs/agent/deep/y.txt",
        "apps/asciiid-classic/SKILL.md", "scripts/x.txt", "docs/solutions/z.md",
    ]
    for rel in samples:
        hook_forbids = not hook._is_allowed(str(root / rel))
        guard_flags = bool(guard._is_stray_doc(rel))
        assert hook_forbids == guard_flags, (
            f"{rel}: hook_forbids={hook_forbids} guard_flags={guard_flags} — "
            "the two doors disagree"
        )


@_needs_policy
def test_unreadable_index_blocks_instead_of_waving_the_commit_through(monkeypatch) -> None:
    """A git failure used to return [] — 'nothing added' — and allow anything."""
    def boom(*_args, **_kwargs):
        return SimpleNamespace(returncode=128, stdout="", stderr="fatal: not a git repository")

    monkeypatch.setattr(guard, "_git", boom)
    with pytest.raises(guard.GitQueryError):
        guard.added_paths()

    error = guard.planning_surface_error(message_text="chore: anything")
    assert error is not None
    assert "could not read the staged index" in error


@_needs_policy
def test_trailer_still_authorizes_a_genuinely_new_surface() -> None:
    assert guard.planning_surface_error(
        [".xx/AGENTS.md"],
        "feat: add lane router\n\nNew-Canonical-Surface: lane router needs its own entry point",
    ) is None


# --- FL-4877: the render lane check fails closed inside commit_msg_error ------


def _lane_module():
    """The module object the guard imports (by plain name, from scripts/)."""
    import importlib
    import sys

    scripts_dir = str(Path(guard.__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    if not (Path(scripts_dir) / "render_lane_boundary.py").is_file():
        # The FL-4512 paper self-test snapshot copies only paper-lane inputs.
        pytest.skip("render lane module not present in this checkout")
    return importlib.import_module("render_lane_boundary")


@pytest.mark.parametrize("failure", [RuntimeError("cat-file died"), KeyError("oid"), AssertionError()])
def test_render_lane_check_crash_blocks_the_commit(monkeypatch, failure) -> None:
    lane = _lane_module()

    def crash(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(lane, "staged_errors", crash)
    error = guard.render_lane_error("fix: x")
    assert error is not None and "GIT GUARD BLOCKED (FL-4877)" in error


def test_commit_msg_error_reports_the_render_lane_refusal(monkeypatch) -> None:
    lane = _lane_module()
    monkeypatch.setattr(guard, "commit_identity_error", lambda *a, **k: None)
    monkeypatch.setattr(guard, "planning_surface_error", lambda **_: None)
    monkeypatch.setattr(guard, "feature_impact_error", lambda _m: None)
    monkeypatch.setattr(lane, "staged_errors", lambda *_a, **_k: ["GIT GUARD BLOCKED (FL-4877): lane"])
    assert guard.commit_msg_error("fix: x") == "GIT GUARD BLOCKED (FL-4877): lane"
