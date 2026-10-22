"""FL-4837 — correcting a Description that was measured wrong after it shipped.

FL-4837 published a Description asserting "3 of 6" viewer-only placements and an
11555-face delta. Both were measured wrong (2 of 6; 633 faces), every downstream
reader kept quoting the wrong text, and `docs/FAILURE_LOG.md` is append-only
prose that is never hand-edited. The correction therefore travels in the
OVERLAY, through the existing union-preserving front door
`scripts/fl_overlay_set.py`, which appends one row per touched key under the
per-key LAST-WRITE-WINS merge FL-4023 owns.

These tests pin the two properties that must hold AT ONCE:

  1. the current authoritative description stops asserting the wrong claim;
  2. the superseded claim stays retrievable as history.

Each has a falsifier. The sharpest one is
`test_a_second_correction_does_not_drop_the_first`: if a correction ever
REPLACED history instead of superseding it, that test goes red.

A setter that writes a field no reader honors is a fake fix, so the last group
proves the readers actually honor it: `FailureEntry.description`, the analyzer
dictionary row contract, and `fl show`, which prints raw markdown and would
otherwise hand a reader the wrong text with nothing on it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from maintainer.lib.failure_log import (  # noqa: E402
    FL_DESCRIPTION_CORRECTION_FIELD,
    FL_DESCRIPTION_CORRECTION_REASON_FIELD,
    FailureLogSnapshot,
    append_overlay_rows,
    description_correction_history,
    read_failure_log,
)

SETTER = REPO_ROOT / "scripts/fl_overlay_set.py"


def _load_script_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYZE_RUNS = _load_script_module(
    "analyze_runs_fl4837_description_correction",
    "scripts/analyze_runs.py",
)

WRONG = (
    "Result: 3 of 6 manifest placements are VIEWER-ONLY, and wiring the generic "
    "loop adds 11555 faces to fixed_face_count."
)
RIGHT = (
    "Result: 2 of 6 manifest placements (castle_massif 183 faces, castle_bridge "
    "450 faces) were VIEWER-ONLY; 19472 published + 633 delta = 20105."
)
REASON = (
    "pieta_statue was already published through _append_static_world_rock_field, "
    "so its 10922 faces were already inside fixed_face_count"
)


@pytest.fixture
def log(tmp_path: Path) -> Path:
    """A two-entry ledger with a matching overlay part, shaped like the real one."""
    path = tmp_path / "docs" / "FAILURE_LOG.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Failure Log\n\n"
        "### FL-999401: Authored map content does not reach the running game\n\n"
        "**Status:** OPEN\n"
        "**Date Opened:** 2026-09-13\n"
        "**Category:** MAINTAINABILITY\n"
        f"**Description:** {WRONG}\n"
        "**Related:** FL-999402\n\n"
        "### FL-999402: Unrelated neighbour entry\n\n"
        "**Status:** OPEN\n"
        "**Date Opened:** 2026-09-13\n"
        "**Category:** MAINTAINABILITY\n"
        "**Description:** Neighbour description that must not move.\n",
        encoding="utf-8",
    )
    overlay = path.parent / "FAILURE_LOG.overlay"
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "part-0001.jsonl").write_text(
        json.dumps({"fl": "FL-999401", "Area": "godot", "Kinds": ["defect"]}) + "\n"
        + json.dumps({"fl": "FL-999402", "Area": "godot", "Kinds": ["defect"]}) + "\n",
        encoding="utf-8",
    )
    return path


def _correct(log: Path, description: str, reason: str) -> None:
    """Append one correction row, exactly as `fl_overlay_set.py --write` does."""
    append_overlay_rows(
        log,
        [{
            "fl": "FL-999401",
            FL_DESCRIPTION_CORRECTION_FIELD: description,
            FL_DESCRIPTION_CORRECTION_REASON_FIELD: reason,
        }],
    )


def _entry(log: Path, fl_id: str = "FL-999401"):
    return {e.failure_id: e for e in read_failure_log(log)}[fl_id]


# --- property 1: the current description stops asserting the wrong claim ----


def test_the_correction_supersedes_the_authored_description(log: Path) -> None:
    assert _entry(log).description == WRONG
    _correct(log, RIGHT, REASON)
    entry = _entry(log)
    assert entry.description == RIGHT
    assert "3 of 6" not in entry.description
    assert "11555" not in entry.description
    assert entry.description_correction_reason == REASON


def test_the_authored_line_is_untouched_on_disk(log: Path) -> None:
    """Falsifier for "append-only": the ledger prose must not be rewritten."""
    before = log.read_text(encoding="utf-8")
    _correct(log, RIGHT, REASON)
    assert log.read_text(encoding="utf-8") == before
    assert WRONG in before


# --- property 2: the superseded claim stays retrievable --------------------


def test_the_superseded_description_is_still_retrievable(log: Path) -> None:
    _correct(log, RIGHT, REASON)
    entry = _entry(log)
    assert entry.authored_description == WRONG, "the replaced claim must remain readable"
    assert WRONG in log.read_text(encoding="utf-8")


def test_a_second_correction_does_not_drop_the_first(log: Path) -> None:
    """THE HISTORY FALSIFIER.

    Goes red the day a correction REPLACES history instead of superseding it.
    Overlay parts are append-only, so every correction ever written must still
    be returned here, oldest first, even after a later one wins the merge.
    """
    _correct(log, RIGHT, REASON)
    _correct(log, "Third reading of the same measurement.", "a later remeasure")
    history = description_correction_history(log, "FL-999401")
    assert [record["description"] for record in history] == [
        RIGHT,
        "Third reading of the same measurement.",
    ], "a superseded correction was dropped instead of being superseded"
    assert history[0]["reason"] == REASON
    # The newest correction is what readers resolve.
    assert _entry(log).description == "Third reading of the same measurement."
    # And the original authored claim is STILL the oldest thing in the chain.
    assert _entry(log).authored_description == WRONG


def test_history_is_empty_and_harmless_for_an_uncorrected_entry(log: Path) -> None:
    assert description_correction_history(log, "FL-999402") == []
    neighbour = _entry(log, "FL-999402")
    assert neighbour.description == "Neighbour description that must not move."
    assert neighbour.description_correction_reason == ""


def test_a_correction_touches_only_its_own_entry(log: Path) -> None:
    _correct(log, RIGHT, REASON)
    assert _entry(log, "FL-999402").description == "Neighbour description that must not move."


# --- the readers must actually honor it (a setter alone is a fake fix) -----


def test_the_dictionary_row_contract_honors_the_correction(log: Path) -> None:
    _correct(log, RIGHT, REASON)
    rows = {row["fl"]: row for row in FailureLogSnapshot(log).rows()}
    assert rows["FL-999401"]["description"] == RIGHT
    assert rows["FL-999401"]["authored_description"] == WRONG
    assert rows["FL-999402"]["description"] == "Neighbour description that must not move."


def test_fl_show_warns_that_the_printed_description_is_history(
    log: Path, capsys, monkeypatch
) -> None:
    """`fl show` prints RAW markdown, so without this banner the command would
    hand a reader the retracted claim with nothing on it."""
    _correct(log, RIGHT, REASON)
    monkeypatch.setattr(ANALYZE_RUNS, "canonical_failure_log_path", lambda root: log)
    from types import SimpleNamespace

    ANALYZE_RUNS.cmd_fl_show(SimpleNamespace(fl_id="FL-999401", json=False), log.parent.parent)
    out = capsys.readouterr().out
    assert "DESCRIPTION SUPERSEDED" in out
    assert RIGHT in out
    assert REASON in out
    assert out.index("DESCRIPTION SUPERSEDED") < out.index(WRONG), (
        "the notice must come BEFORE the superseded text, not after it"
    )


def test_fl_show_is_unchanged_for_an_uncorrected_entry(
    log: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(ANALYZE_RUNS, "canonical_failure_log_path", lambda root: log)
    from types import SimpleNamespace

    ANALYZE_RUNS.cmd_fl_show(SimpleNamespace(fl_id="FL-999402", json=False), log.parent.parent)
    assert "DESCRIPTION SUPERSEDED" not in capsys.readouterr().out


def test_description_history_command_prints_the_whole_chain(
    log: Path, capsys, monkeypatch
) -> None:
    _correct(log, RIGHT, REASON)
    _correct(log, "Third reading.", "a later remeasure")
    monkeypatch.setattr(ANALYZE_RUNS, "canonical_failure_log_path", lambda root: log)
    from types import SimpleNamespace

    ANALYZE_RUNS.cmd_fl_description_history(
        SimpleNamespace(fl_id="FL-999401", json=True), log.parent.parent
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["authored_description"] == WRONG
    assert [c["description"] for c in payload["corrections"]] == [RIGHT, "Third reading."]
    assert payload["current_description"] == "Third reading."


# --- the front door refuses a silent rewrite -------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--description-correction", RIGHT], "requires --description-correction-reason"),
        (["--description-correction-reason", REASON], "has no --description-correction"),
        (
            ["--description-correction", "a\nb", "--description-correction-reason", REASON],
            "must be a single line",
        ),
    ],
)
def test_the_setter_refuses_a_correction_it_cannot_record_honestly(
    log: Path, argv: list[str], expected: str
) -> None:
    before = sorted(p.read_text(encoding="utf-8") for p in (log.parent / "FAILURE_LOG.overlay").iterdir())
    result = subprocess.run(
        [sys.executable, str(SETTER), "--fl", "FL-999401", "--path", str(log), *argv],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert expected in result.stderr
    after = sorted(p.read_text(encoding="utf-8") for p in (log.parent / "FAILURE_LOG.overlay").iterdir())
    assert after == before, "a refused correction must write nothing"


def test_the_setter_is_dry_run_by_default_and_writes_the_expected_row(log: Path) -> None:
    """The dry-run path is the one the coordinator runs before landing."""
    before = sorted(p.read_text(encoding="utf-8") for p in (log.parent / "FAILURE_LOG.overlay").iterdir())
    result = subprocess.run(
        [
            sys.executable, str(SETTER), "--fl", "FL-999401", "--path", str(log),
            "--description-correction", RIGHT,
            "--description-correction-reason", REASON,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "DRY-RUN" in result.stdout
    assert FL_DESCRIPTION_CORRECTION_FIELD in result.stdout
    after = sorted(p.read_text(encoding="utf-8") for p in (log.parent / "FAILURE_LOG.overlay").iterdir())
    assert after == before, "a dry run must write nothing"
