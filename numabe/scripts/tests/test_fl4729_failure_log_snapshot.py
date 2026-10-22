#!/usr/bin/env python3
"""Interface tests for FL-4729's single request-scoped ledger owner."""
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from maintainer.lib.failure_log import (  # noqa: E402
    FailureLogSnapshot,
    _parse_entry_block,
    next_failure_id,
)
from maintainer.lib.fl_overlay import _effective_overlay_record  # noqa: E402


def _fixture(tmp_path: Path) -> Path:
    """Build a ledger that exercises every overlay shape the CLIs must handle.

    FL-100 has an authored overlay row, FL-101 has none, FL-102 authors a
    Category but no Area so the effective record has to synthesize one, and
    FL-999 exists only in the overlay with no ledger entry behind it.
    """
    log = tmp_path / "docs" / "FAILURE_LOG.md"
    log.parent.mkdir(parents=True)
    log.write_text(
        "### FL-100: Parser fixture (2026-08-01)\n\n"
        "- **Status:** OPEN\n"
        "- **Category:** tooling\n"
        "- **Description:** one snapshot owns every ledger read facet\n"
        "- **Gate:** `fixture_gate`\n"
        "- **Artifact:** `/tmp/watchdog-fixture`\n"
        "- **Hypothesis:** one snapshot owns the facts\n"
        "- **Related:** FL-099\n\n"
        "> **[2026-08-02] Status update: OPEN -> PARTIAL**\n\n"
        "### FL-101: Second fixture\n\n"
        "**Status:** OPEN\n"
        "**Category:** process\n\n"
        "### FL-102: Overlay without an authored area\n\n"
        "**Status:** OPEN\n"
        "**Category:** tooling\n"
        "**Description:** first line of the summary\n"
        "  continuation line the parser has never captured\n",
        encoding="utf-8",
    )
    overlay = log.with_name("FAILURE_LOG.overlay")
    overlay.mkdir()
    (overlay / "part-0001.jsonl").write_text(
        '{"fl":"FL-100","Area":"tooling","Kinds":["tooling_gap"]}\n'
        '{"fl":"FL-999","Area":"process"}\n'
        '{"fl":"FL-102","Category":"tooling","Kinds":["process_gap"]}\n',
        encoding="utf-8",
    )
    (overlay / "part-0002.jsonl").write_text(
        '{"fl":"FL-100","ProofState":"PARTIAL","Kinds":["performance"]}\n',
        encoding="utf-8",
    )
    return log


def _runs_compat_effective_record(entry: dict | None, overlay: dict | None, fl_id: str) -> dict:
    """Mirror of _RunsCompat.effective_fl_overlay_record in analyze_failure_log.

    Reproduced from the shared library function rather than imported from the
    CLI so this test does not drag the whole command module in. Any change to
    the CLI wrapper's defaults must be mirrored here.
    """
    return _effective_overlay_record(
        fl_id,
        default_category=str((entry or {}).get("category") or ""),
        overlay=overlay
        or ((entry or {}).get("overlay") if isinstance((entry or {}).get("overlay"), dict) else None),
        default_kinds=["unclassified"],
        default_proof_state="OPEN",
    )


def test_overlay_target_is_lazy_constant_memory_facet(tmp_path: Path) -> None:
    log = _fixture(tmp_path)
    snapshot = FailureLogSnapshot(log)

    record = snapshot.overlay_record("FL-100")

    assert record["Area"] == "tooling"
    assert record["Kinds"] == ["performance"]
    assert record["ProofState"] == "PARTIAL"
    assert record == FailureLogSnapshot(log).overlay_raw()["FL-100"]
    assert snapshot.diagnostics == {
        "markdown_read_count": 0,
        "overlay_part_read_count": 2,
        "overlay_line_parse_count": 2,
        "row_parse_count": 0,
        "entry_parse_count": 0,
    }


def test_rows_are_parsed_once_and_preserve_analyzer_contract(tmp_path: Path) -> None:
    snapshot = FailureLogSnapshot(_fixture(tmp_path))

    first = snapshot.rows()
    second = snapshot.rows()

    assert first is second
    assert snapshot.diagnostics["markdown_read_count"] == 1
    assert snapshot.diagnostics["row_parse_count"] == 3
    row = first[0]
    assert row["title"] == "Parser fixture"
    assert row["date"] == "2026-08-01"
    assert row["gate"] == "fixture_gate"
    assert row["artifact_run_id"] == "watchdog-fixture"
    assert row["hypothesis"] == "one snapshot owns the facts"
    assert row["effective_status"] == "PARTIAL"
    assert row["last_status_update_date"] == "2026-08-02"
    assert row["related"] == ["FL-099"]


def test_old_snapshot_stays_frozen_and_replacement_observes_write(tmp_path: Path) -> None:
    log = _fixture(tmp_path)
    old = FailureLogSnapshot(log)
    assert len(old.rows()) == 3

    with log.open("a", encoding="utf-8") as handle:
        handle.write("\n### FL-103: Added later\n\n**Status:** OPEN\n")

    assert len(old.rows()) == 3
    assert len(FailureLogSnapshot(log).rows()) == 4
    assert not hasattr(old, "refresh")


def test_id_allocation_reuses_locked_snapshot_overlay_view(tmp_path: Path) -> None:
    snapshot = FailureLogSnapshot(_fixture(tmp_path))
    entries = snapshot.entries()
    before = dict(snapshot.diagnostics)

    assert next_failure_id(entries, fl_path=snapshot.path, snapshot=snapshot) == "FL-1000"
    assert snapshot.diagnostics == before


def test_rows_carry_the_same_description_text_as_parsed_entries(tmp_path: Path) -> None:
    """The `related` and `suggest-related` rankers score on row description text.

    Rows and FailureEntry objects are two views of the same authored block, so
    a row's description has to be exactly what the entry parser reads. Anything
    less degrades ranking without failing anything.
    """
    snapshot = FailureLogSnapshot(_fixture(tmp_path))
    rows = {row["fl"]: row for row in snapshot.rows()}

    assert rows["FL-100"]["description"] == "one snapshot owns every ledger read facet"
    # Field values stop at the end of their line. An indented continuation is
    # not part of the description, matching what the entry parser has always
    # returned, so the two views cannot disagree.
    assert rows["FL-102"]["description"] == "first line of the summary"
    assert rows["FL-101"]["description"] == ""

    for fl_id, title, block in snapshot.entry_blocks():
        entry = _parse_entry_block(block, fl_id, title)
        assert rows[fl_id]["description"] == entry.description


def test_effective_overlay_view_is_stable_when_cli_re_synthesizes_records(tmp_path: Path) -> None:
    """Pin what changed when load_fl_overlay switched from raw rows to effective ones.

    Four analyze_failure_log commands re-run the CLI's own record synthesis
    over whatever load_fl_overlay handed them. Feeding already-synthesized
    records back in must not keep mutating them, and the only field the switch
    moves is Area, which now carries the value derived from the ledger Category
    instead of being dropped.
    """
    snapshot = FailureLogSnapshot(_fixture(tmp_path))
    rows_by_fl = {row["fl"]: row for row in snapshot.rows()}
    effective = snapshot.effective_overlay()
    raw = snapshot.overlay_raw()

    assert set(effective) == {"FL-100", "FL-101", "FL-102", "FL-999"}

    for fl_id in sorted(set(effective) | set(raw)):
        entry = rows_by_fl.get(fl_id)
        from_effective = _runs_compat_effective_record(entry, effective.get(fl_id), fl_id)
        from_raw = _runs_compat_effective_record(entry, raw.get(fl_id), fl_id)

        # Re-applying synthesis to an already-effective record is a no-op, so
        # the repeated calls at the four command sites cannot drift.
        assert _runs_compat_effective_record(entry, from_effective, fl_id) == from_effective

        differing = {key for key in set(from_effective) | set(from_raw) if from_effective.get(key) != from_raw.get(key)}
        assert differing <= {"Area"}, (fl_id, differing)

    # FL-102 authors a Category but no Area: the effective view supplies the
    # derived area, the raw view leaves it empty.
    assert effective["FL-102"]["Area"] == "tooling"
    assert "Area" not in raw["FL-102"]
    assert _runs_compat_effective_record(rows_by_fl["FL-102"], effective["FL-102"], "FL-102")["Area"] == "tooling"
    assert _runs_compat_effective_record(rows_by_fl["FL-102"], raw["FL-102"], "FL-102")["Area"] == ""


def test_retired_parallel_cache_owners_are_absent() -> None:
    analyze_runs = (REPO_ROOT / "scripts/analyze_runs.py").read_text(encoding="utf-8")
    analyze_failure_log = (REPO_ROOT / "scripts/analyze_failure_log.py").read_text(encoding="utf-8")

    for retired in (
        "def _parse_fl_entries",
        "_RunsCompat._load",
        "_RunsCompat._fl_cache",
        "_request_fl_snapshots",
        "_request_fl_indexes",
        "def _invalidate_fl_cache",
    ):
        assert retired not in analyze_runs
        assert retired not in analyze_failure_log
