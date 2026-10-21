"""Exercise the native front door against disposable ledgers, not this repo."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import analyze_runs as runs


def test_native_attempt_appends_are_serialized_and_monotonic(tmp_path, monkeypatch):
    log = tmp_path / 'docs/FAILURE_LOG.md'
    log.parent.mkdir()
    baseline = ('### FL-100: synthetic attempt fixture\n\n**Status:** OPEN\n'
                '\n#### Fix attempt — legacy\n\n- **Approach:** legacy\n- **Proof state:** FAILED.\n')
    log.write_text(baseline)
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    monkeypatch.setattr(runs, 'fl_required_fields_advisory_for_id', lambda *_: {})
    def append(number):
        args = SimpleNamespace(fl_id='FL-100', description=f'synthetic-{number}',
            session_id=f'fixture-{number}', branch='fixture', json=True, stage=False,
            no_canon_spec_sync=True, no_lane_doc_refresh=True,
            no_auto_overlay_refs=True, no_inline_comment_suggestions=True,
            proof_state='FAILED')
        runs.cmd_fl_fix_attempt(args, tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, range(4)))
    text = log.read_text()
    assert text.startswith(baseline.rstrip() + '\n')
    for number in range(4):
        assert text.count(f'**Approach:** session_id=fixture-{number} synthetic-{number}') == 1
    for sequence in range(2, 6):
        assert text.count(f'**Attempt sequence:** {sequence}\n') == 1
    assert text.count('#### Fix attempt') == 5


def test_explicit_high_counter_never_decreases(tmp_path, monkeypatch):
    log = tmp_path / 'docs/FAILURE_LOG.md'
    log.parent.mkdir()
    log.write_text('### FL-100: synthetic fixture\n\n**Status:** OPEN\n\n'
                   '#### Fix attempt — historic\n- **Attempt sequence:** 80\n')
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    monkeypatch.setattr(runs, 'fl_required_fields_advisory_for_id', lambda *_: {})
    args = SimpleNamespace(fl_id='FL-100', description='synthetic-next', session_id='fixture',
        branch='fixture', json=True, stage=False, no_canon_spec_sync=True,
        no_lane_doc_refresh=True, no_auto_overlay_refs=True, no_inline_comment_suggestions=True)
    runs.cmd_fl_fix_attempt(args, tmp_path)
    assert '**Attempt sequence:** 81\n' in log.read_text()
