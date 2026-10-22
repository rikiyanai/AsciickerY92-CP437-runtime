"""Actual installed hook payload regressions; fixtures are disposable."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path('/Users/r/.claude/hooks/tmp_durable_doc_guard.py')
if not HOOK.is_file():
    pytest.skip("Installed-host integration suite: owned document hook unavailable; not installation proof",
                allow_module_level=True)


def invoke(tool, inp, cwd):
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({
        'tool_name': tool, 'tool_input': inp, 'cwd': str(cwd),
    }), text=True, capture_output=True, check=True)
    return json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'] if result.stdout else 'allow'


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    (root / '.config').mkdir(parents=True)
    (root / '.git').mkdir()
    (root / '.config/repo-profile.json').write_text(json.dumps({'document_owners': ['docs/agent/']}))
    return root


@pytest.mark.parametrize('tool', ['Write', 'Edit', 'NotebookEdit', 'write_file', 'edit_file'])
def test_all_direct_aliases_resolve_relative_temp_and_symlinks(repo, tmp_path, tool):
    # pytest's fixture root is temporary, so all document targets there are refused.
    assert invoke(tool, {'file_path': 'loose.md'}, repo) == 'deny'
    link = repo / 'alias'
    link.symlink_to(tmp_path, target_is_directory=True)
    assert invoke(tool, {'file_path': 'alias/escape.md'}, repo) == 'deny'


@pytest.mark.parametrize('inp', [
    '*** Begin Patch\n*** Add File: loose.md\n+x\n*** End Patch',
    {'command': '*** Begin Patch\n*** Update File: x.py\n*** Move to: loose.md\n@@\n-x\n+y\n*** End Patch'},
    {'patch': '*** Begin Patch\n*** Add File: loose.md\n+x\n*** End Patch'},
])
def test_native_and_legacy_patch_shapes_refuse_documents(repo, inp):
    assert invoke('apply_patch', inp, repo) == 'deny'


def test_multi_edit_resolves_top_level_file(repo):
    assert invoke('MultiEdit', {'file_path': 'loose.md', 'edits': [{'old_string': 'a', 'new_string': 'b'}]}, repo) == 'deny'


def test_non_document_producer_output_not_blanket_banned(repo):
    assert invoke('apply_patch', '*** Begin Patch\n*** Add File: fixture.py\n+x\n*** End Patch', repo) == 'allow'


def test_installed_profile_at_actual_repo_boundary_without_writing():
    root = Path(__file__).resolve().parents[2]
    owned = root / 'docs/agent/.observer-document-owner-probe.md'
    loose = root / '.observer-loose-document-probe.md'
    assert not owned.exists() and not loose.exists()
    assert invoke('apply_patch', f'*** Begin Patch\n*** Add File: {owned}\n+x\n*** End Patch', root) == 'allow'
    assert invoke('apply_patch', f'*** Begin Patch\n*** Add File: {loose}\n+x\n*** End Patch', root) == 'deny'
    assert not owned.exists() and not loose.exists()


@pytest.mark.parametrize('raw', ['not json', '[]', 'null'])
def test_malformed_hook_input_is_not_fail_open(raw):
    result = subprocess.run([sys.executable, str(HOOK)], input=raw, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'
