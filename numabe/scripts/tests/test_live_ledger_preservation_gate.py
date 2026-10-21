"""Real Git-index checks; never stage or commit in the shared checkout."""
import os
import subprocess
from pathlib import Path

import pytest
from scripts import git_commit_policy_guard as guard


def git(root, *args, env=None):
    return subprocess.run(['git', *args], cwd=root, env=env, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    git(tmp_path, 'config', 'user.name', 'Synthetic hook fixture')
    log = tmp_path / 'docs/FAILURE_LOG.md'
    log.parent.mkdir()
    log.write_text('### FL-1: fixture\n\n**Status:** OPEN\nA retained warning.\n')
    git(tmp_path, 'add', 'docs/FAILURE_LOG.md')
    git(tmp_path, 'commit', '-qm', 'synthetic baseline')
    monkeypatch.setattr(guard, 'REPO_ROOT', tmp_path)
    return tmp_path, log


def test_append_is_allowed_and_foreign_deletion_is_refused(repo):
    root, log = repo
    baseline = log.read_text()
    log.write_text(baseline + '\n#### Fix attempt — fixture\nNew owned evidence.\n')
    git(root, 'add', 'docs/FAILURE_LOG.md')
    assert guard.ledger_preservation_error() is None
    log.write_text(log.read_text().replace('A retained warning.\n', ''))
    git(root, 'add', 'docs/FAILURE_LOG.md')
    assert 'existing FL lines removed' in guard.ledger_preservation_error()


def test_duplicate_line_cardinality_cannot_launder_removal(repo):
    root, log = repo
    log.write_text(log.read_text().replace('**Status:** OPEN', '**Status:** RESOLVED') + '\nA retained warning.\n')
    git(root, 'add', 'docs/FAILURE_LOG.md')
    assert '**Status:** OPEN' in guard.ledger_preservation_error()


def test_deleted_ledger_refused(repo):
    root, log = repo
    log.unlink()
    git(root, 'add', 'docs/FAILURE_LOG.md')
    assert 'cannot read both ledger snapshots' in guard.ledger_preservation_error()


def test_private_index_is_the_checked_snapshot(repo, monkeypatch):
    root, log = repo
    baseline = log.read_text()
    log.write_text(baseline + '\nOwned append.\n')
    git(root, 'add', 'docs/FAILURE_LOG.md')
    private = root / 'private-index'
    env = {**os.environ, 'GIT_INDEX_FILE': str(private)}
    git(root, 'read-tree', 'HEAD', env=env)
    log.write_text(baseline.replace('A retained warning.\n', ''))
    git(root, 'add', 'docs/FAILURE_LOG.md', env=env)
    monkeypatch.setenv('GIT_INDEX_FILE', str(private))
    assert guard.ledger_preservation_error()
    monkeypatch.delenv('GIT_INDEX_FILE')
    assert guard.ledger_preservation_error() is None
