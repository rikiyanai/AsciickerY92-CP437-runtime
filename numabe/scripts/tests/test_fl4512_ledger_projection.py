"""Adversarial ownership tests; these do not mint IDs or push a real repository."""
import pytest
import os
import json
import subprocess
import sys
from pathlib import Path

from scripts.fl4512_ledger_projection import append_attempt, append_entry

HEAD = "# Ledger\n\n### FL-1: old\nOld evidence\n"
PART = "docs/FAILURE_LOG.overlay/part-0001.jsonl"
ROW = '{"fl":"FL-2","CodeRefs":["scripts/owned.py:12"],"ComplaintRefs":["FL-1"]}'
WT = HEAD + "\n### FL-2: native-key\nNew evidence\n\n### FL-3: sibling\nForeign addition\n"


def test_native_entry_keeps_lineage_and_code_refs_without_foreign_additions():
    before = '{"fl":"FL-1","CodeRefs":["old.py"]}\n'
    out = append_entry(HEAD, WT, "FL-2", "native-key", {PART: before},
                       {PART: before + ROW + '\n{"fl":"FL-3","CodeRefs":["foreign.py"]}\n'})
    assert out[PART] == before + ROW + "\n"
    assert "Foreign addition" not in out["docs/FAILURE_LOG.md"]
    assert out["docs/FAILURE_LOG.md"].startswith(HEAD)


@pytest.mark.parametrize("key", ["sibling", "absent", ""])
def test_entry_key_cannot_select_a_foreign_section(key):
    with pytest.raises(ValueError, match="key"):
        append_entry(HEAD, WT, "FL-2", key, {}, {PART: ROW})


def test_entry_refuses_missing_overlay_or_existing_identifier():
    with pytest.raises(ValueError, match="overlay missing"):
        append_entry(HEAD, WT, "FL-2", "native-key", {}, {})
    with pytest.raises(ValueError, match="already exists"):
        append_entry(WT, WT, "FL-2", "native-key", {}, {PART: ROW})


def test_entry_refuses_overlay_only_id_even_in_worktree_absent_shard():
    with pytest.raises(ValueError, match="overlay-only"):
        append_entry(HEAD, WT, "FL-2", "native-key", {PART: ROW},
                     {"docs/FAILURE_LOG.overlay/part-0002.jsonl": ROW})


def test_attempt_preserves_sequence_and_future_fields_without_next_attempt():
    worktree = HEAD + ("\n#### Fix attempt today\n- key-own\n- **Proof state:** FAILED\n"
                       "- **Attempt sequence:** 81\n- **Future field:** retained\n\n"
                       "#### Fix attempt sibling\n- foreign-key\n- **Proof state:** OPEN\n")
    out = append_attempt(HEAD, worktree, "FL-1", "key-own")
    assert "sequence:** 81" in out and "Future field:** retained" in out
    assert "foreign-key" not in out


def test_attempt_cannot_clip_a_different_fl_block():
    worktree = HEAD + "\n### FL-2: another\n#### Fix attempt today\nwrong-key\n- **Proof state:** FAILED\n"
    with pytest.raises(ValueError, match="key"):
        append_attempt(HEAD, worktree, "FL-1", "wrong-key")


def test_full_new_entry_cli_runs_real_guards_and_resyncs_only_fixture(tmp_path):
    repo = tmp_path / "isolated"
    repo.mkdir()
    source_root = Path(__file__).resolve().parents[2]
    files = ["fl4512_commit_owned.py", "fl4512_ledger_projection.py",
             "fl4512_commit_preflight.py", "fl4512_commit_transport.py",
             "fl4512_index_resync.py", "git_commit_policy_guard.py",
             "godot_feature_regression_gate.py", "render_lane_boundary.py",
             "maintainer/hooks/doc_placement_policy.py"]
    for relative in files:
        target = repo / "scripts" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source_root / "scripts" / relative).read_bytes())
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Synthetic fixture")
    (repo / "docs").mkdir()
    (repo / "docs/FAILURE_LOG.md").write_text(HEAD)
    (repo / "config").mkdir()
    (repo / "config/godot_feature_regressions.json").write_text(json.dumps({"features": []}))
    (repo / "config/render_lane_boundary.json").write_text(json.dumps({
        "schema": "render-lane-boundary/1", "protected": ["config/render_lane_boundary.json"],
        "ff_private": ["godot_project/render/ascii/composition/*"], "excluded": [],
    }))
    (repo / "sibling.py").write_text("old = 1\n")
    git("add", ".")
    git("commit", "-qm", "synthetic baseline")
    (repo / "sibling.py").write_text("foreign = 2\n")
    git("add", "sibling.py")
    foreign_index = git("rev-parse", ":sibling.py")
    (repo / "docs/FAILURE_LOG.md").write_text(WT)
    (repo / "docs/FAILURE_LOG.overlay").mkdir()
    (repo / PART).write_text(ROW + '\n{"fl":"FL-3","CodeRefs":["foreign.py"]}\n')
    result = subprocess.run(
        [sys.executable, str(repo / "scripts/fl4512_commit_owned.py"),
         "--fl-add", "FL-2", "native-key"], cwd=repo,
        env={**os.environ, "FL4512_REPO_ROOT": str(repo)},
        input="fix(FL-2): native ledger fixture\n\nGodot-Feature-Impact: none\n",
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pre-commit guard: pass" in result.stdout
    assert "commit-msg guard: pass" in result.stdout
    assert "push skipped" in result.stdout
    assert git("remote") == ""
    assert "FL-3" not in git("show", "HEAD:docs/FAILURE_LOG.md")
    assert git("show", "HEAD:" + PART) == ROW
    assert git("rev-parse", ":sibling.py") == foreign_index
    assert git("diff", "--cached", "--name-only") == "sibling.py"
    assert "FL-3" in (repo / "docs/FAILURE_LOG.md").read_text()
