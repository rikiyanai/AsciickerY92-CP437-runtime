"""The single placement policy, and the parity that must hold across doors.

FL-4853. History this pins down:
  - `.markdown` was blocked by the commit guard and waved through by all three
    hooks. A parity test existed; it passed because its hand-picked sample had
    no `.markdown` path in it. A test that can pass vacuously is not a test, so
    the parity case below asserts its own sample contains the known traps.
  - 46 prose surfaces (.brief, .prompt, .notes, .rst, extensionless, ...) were
    unguarded at every door, which is how a task brief could be written anywhere
    simply by choosing a suffix nobody had enumerated.
  - `git mv code.py notes.md` was invisible to the commit guard, which asked
    git for --diff-filter=A and therefore never saw renames.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_POLICY_PATH = ROOT / "scripts/maintainer/hooks/doc_placement_policy.py"
if not _POLICY_PATH.is_file():
    # The FL-4512 paper gate re-runs suites inside a snapshot that exports only
    # paper-owned paths. Skip rather than fail there; a full checkout always has it.
    pytest.skip("hook tree absent in this checkout", allow_module_level=True)

policy = _load("dpp_under_test", "scripts/maintainer/hooks/doc_placement_policy.py")

# Every suffix an agent has actually reached for, plus the ones that started this.
PROSE_SUFFIXES = [
    ".md", ".txt", ".markdown", ".rst", ".org", ".adoc",
    ".brief", ".prompt", ".input", ".notes", ".plan", ".draft", ".summary", "",
]
CODE_SUFFIXES = [
    ".py", ".gd", ".cpp", ".h", ".json", ".yaml", ".toml", ".sh", ".ts",
    ".glsl", ".png", ".a3d", ".uid", ".meta", ".import", ".xp", ".akm", ".log",
]
STRAY_LOCATIONS = ["{n}", "docs/plans/{n}", ".xx/{n}", "scripts/{n}", "deep/nest/{n}"]
LOOSE_EVIDENCE_NOTES = [
    "artifacts/x/HANDOFF.md", "docs/audits/x-audit.md", ".run/x-status.md",
    "docs/research/x/verification/y/README.md",
    "artifacts/x/line\nHANDOFF.md", ".run/x status.md",
]


@pytest.mark.parametrize("path", LOOSE_EVIDENCE_NOTES)
def test_evidence_exemptions_do_not_authorize_agent_notes(path: str) -> None:
    assert policy.is_forbidden_new_doc(path)


def test_named_handoff_producer_is_exactly_scoped() -> None:
    producer = "artifacts/maintainer/handoffs/session-abc/session_handoff.md"
    assert policy.is_forbidden_new_doc(producer) is False
    assert policy.is_forbidden_new_doc(
        "artifacts/maintainer/handoffs/session-abc/HANDOFF.md"
    ) is True


@pytest.mark.parametrize("suffix", PROSE_SUFFIXES)
@pytest.mark.parametrize("loc", STRAY_LOCATIONS)
def test_prose_is_forbidden_outside_the_lane(suffix: str, loc: str) -> None:
    """Inverted rule: an unenumerated suffix is prose, not a loophole."""
    if suffix == "" and loc == "scripts/{n}":
        pytest.skip("covered by test_extensionless_command_dir_carve_out")
    assert policy.is_forbidden_new_doc(loc.format(n="task_wire01" + suffix)) is True


def test_extensionless_command_dir_carve_out() -> None:
    """A deliberate, bounded hole - recorded so it is a decision, not a leak.

    An extensionless file directly inside scripts/ is a command (scripts/fl,
    scripts/export_tables both exist and must stay creatable). The cost is that
    an extensionless brief could be dropped there. It is bounded to depth one,
    and every suffixed form stays blocked, so renaming is not a way around.
    """
    assert policy.is_forbidden_new_doc("scripts/fl") is False
    assert policy.is_forbidden_new_doc("scripts/task_wire01") is False  # the cost
    assert policy.is_forbidden_new_doc("scripts/task_wire01.brief") is True
    assert policy.is_forbidden_new_doc("scripts/deep/task_wire01") is True


@pytest.mark.parametrize("suffix", PROSE_SUFFIXES)
def test_prose_is_allowed_in_the_lane(suffix: str) -> None:
    assert policy.is_forbidden_new_doc("docs/agent/notes/task" + suffix) is False


@pytest.mark.parametrize("suffix", CODE_SUFFIXES)
@pytest.mark.parametrize("loc", STRAY_LOCATIONS)
def test_code_and_data_are_never_blocked(suffix: str, loc: str) -> None:
    """The inversion must not make the repo unusable for real work."""
    assert policy.is_forbidden_new_doc(loc.format(n="thing" + suffix)) is False


@pytest.mark.parametrize(
    "path",
    ["AGENTS.md", "README.md", "engine/render/README.md",
     "apps/asciiid-classic/SKILL.md", "docs/agent/x.brief"],
)
def test_router_exceptions_survive(path: str) -> None:
    assert policy.is_forbidden_new_doc(path) is False


@pytest.mark.parametrize(
    "path",
    ["artifacts/run-42/report.md", "articles/2026-09-19-media/MEDIA.md",
     ".run/numabe/receipt.txt", "docs/research/ascii/source.md"],
)
def test_evidence_prefixes_stay_open(path: str) -> None:
    """Receipts and media manifests are owned by their producers, not this rule."""
    assert policy.is_forbidden_new_doc(path) is False


@pytest.mark.parametrize(
    "name",
    ["Makefile", "makefile_asciiid", "LICENSE", ".gitignore", ".clang-format",
     "launcher", "pre-commit"],
)
def test_real_extensionless_artifacts_are_not_prose(name: str) -> None:
    assert policy.is_prose_surface(name) is False


@pytest.mark.parametrize("path", [
    "assets/samples/footsteps.txt",
    "assets/unicode/16.0.0/UnicodeData.txt",
    "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
    "captures/mapillary/mapillary_123_An_token_stp",
    "godot_project/assets/imported/upstream/glyph_coverage.gd.txt",
    "godot_project/docs/plan004-ansi-frame-dump/shot-source-info.txt",
    "scripts/adhoc/.fl4588_terrain42_phase1_done",
    "scripts/pipeline/requirements.txt",
    "scripts/sprite_frames_walk_xu.txt",
    "requirements-dev.txt",
])
def test_censused_data_paths_are_not_treated_as_prose(path: str) -> None:
    assert not policy.is_forbidden_new_doc(path)


@pytest.mark.parametrize("path", [
    "assets/unicode/16.0.0/handoff.md",
    "assets/unicode/16.0.0/handoff.txt",
    "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
    "captures/mapillary/mapillary_123_brief.md",
    "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
    "captures/mapillary/mapillary_task_brief",
    "scripts/sprite_frames_summary.txt",
])
def test_data_exceptions_do_not_open_document_prefixes(path: str) -> None:
    assert policy.is_forbidden_new_doc(path)


def test_extensionless_brief_is_prose() -> None:
    assert policy.is_prose_surface("task_wire01") is True


# --------------------------------------------------------------- door parity

def test_every_door_agrees_and_the_sample_contains_the_known_traps() -> None:
    """Non-vacuous parity: assert the sample holds the cases that fooled us."""
    guard = _load("gcpg_under_test", "scripts/git_commit_policy_guard.py")

    sample = [
        loc.format(n="task_wire01" + suf)
        for loc in STRAY_LOCATIONS + ["docs/agent/{n}", "artifacts/run1/{n}"]
        for suf in PROSE_SUFFIXES + CODE_SUFFIXES
    ]
    sample += LOOSE_EVIDENCE_NOTES
    assert set(LOOSE_EVIDENCE_NOTES) <= set(sample)
    # Guard the guard: these exact shapes must be exercised.
    assert any(p.endswith(".markdown") for p in sample), "the .markdown trap must be covered"
    assert any(p.endswith("task_wire01") for p in sample), "extensionless must be covered"
    assert any(p.startswith("artifacts/") for p in sample), "evidence prefix must be covered"

    for path in sample:
        policy_says = policy.is_forbidden_new_doc(path)
        guard_says = bool(guard.planning_surface_paths([path]))
        assert policy_says == guard_says, (
            f"{path}: policy={policy_says} commit_guard={guard_says} - doors disagree"
        )


def test_commit_path_readers_preserve_unicode_newlines_and_spaces(monkeypatch):
    guard = _load("gcpg_nul_paths", "scripts/git_commit_policy_guard.py")
    paths = ["docs/agent/café.md", "artifacts/x/line\nHANDOFF.md", " leading.md"]

    def git(*args):
        assert "-z" in args
        return subprocess.CompletedProcess(args, 0, "\0".join(paths) + "\0", "")

    monkeypatch.setattr(guard, "_git", git)
    assert guard.added_paths() == paths
    assert guard.staged_paths() == paths
    assert guard.tracked_paths_under(".") == paths


# ------------------------------------------------------------------ renames

def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def test_commit_guard_sees_renames_not_just_additions() -> None:
    """`git mv code.py notes.md` introduces a doc path with no 'A' status."""
    guard = _load("gcpg_rename", "scripts/git_commit_policy_guard.py")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        _git(td, "init", "-q")
        _git(td, "config", "user.email", "a@b.c")
        _git(td, "config", "user.name", "t")
        (td / "code.py").write_text("x = 1\n")
        _git(td, "add", "-A")
        _git(td, "commit", "-qm", "base")
        _git(td, "mv", "code.py", "sneaky_notes.md")

        added_only = _git(td, "diff", "--cached", "--name-only", "--diff-filter=A").stdout.split()
        with_renames = _git(td, "diff", "--cached", "--name-only", "--diff-filter=AR").stdout.split()

        assert "sneaky_notes.md" not in added_only, "precondition: A alone misses renames"
        assert "sneaky_notes.md" in with_renames
        assert guard.planning_surface_paths(with_renames) == ["sneaky_notes.md"]


# ------------------------------------------------- host pseudo-paths (URIs)

@pytest.mark.parametrize(
    "ref",
    ["skill://doc-hygiene", "mcp://server/tool", "https://example.com/page",
     "memory://session", "file:///tmp/x.md"],
)
def test_uri_references_are_not_documents(ref: str) -> None:
    """The Skill and Read tools pass references through the same file_path
    field a write uses. Judging them as prose blocked skill loading outright:
    `Read skill://doc-hygiene` was refused as an extensionless document.
    """
    assert policy.is_filesystem_path(ref) is False
    assert policy.is_forbidden_new_doc(ref) is False
    assert policy.is_prose_surface(ref) is False


@pytest.mark.parametrize("path", ["stray.md", ".xx/AGENTS.md", "docs/plans/t.brief", "notes"])
def test_real_paths_are_still_judged(path: str) -> None:
    """The URI carve-out must not become a way through for real paths."""
    assert policy.is_filesystem_path(path) is True
    assert policy.is_forbidden_new_doc(path) is True
