from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = ROOT / "scripts" / "analyze_runs.py"
SPEC = importlib.util.spec_from_file_location("analyze_runs_fl4659", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
ANALYZE_RUNS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZE_RUNS)


def _write_minimal_failure_log(root: Path) -> None:
    docs = root / "docs"
    docs.mkdir()
    (docs / "FAILURE_LOG.md").write_text(
        "\n".join(
            [
                "### FL-1: Inline comment scan fixture",
                "",
                "**Status:** OPEN",
                "**Date Opened:** 2026-08-10",
                "**Category:** TOOLING",
                "**Description:** Fixture entry.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_inline_comment_scan_ignores_scratch_non_utf8_cpp_sources(tmp_path: Path) -> None:
    _write_minimal_failure_log(tmp_path)
    scratch_header = (
        tmp_path
        / ".scratch"
        / "fl4512-technique-audit"
        / "part1"
        / "dgtal"
        / "FreemanChain.h"
    )
    scratch_header.parent.mkdir(parents=True)
    scratch_header.write_bytes(b"// latin-1 vendored comment: \xe7\n")
    owned_source = tmp_path / "engine" / "owned.cpp"
    owned_source.parent.mkdir()
    owned_source.write_text("// FL-FAMILY: FL-1\nint main() { return 0; }\n", encoding="utf-8")

    scanned = [path.relative_to(tmp_path) for path in ANALYZE_RUNS._iter_cpp_source_files(tmp_path)]

    assert Path("engine/owned.cpp") in scanned
    assert Path(".scratch/fl4512-technique-audit/part1/dgtal/FreemanChain.h") not in scanned
    plan = ANALYZE_RUNS._line_centric_inline_comment_plan(tmp_path)
    assert plan["stats"]["scanned_comments"] == 1


def test_inline_comment_scan_reports_non_utf8_in_owned_source_without_crashing(
    tmp_path: Path,
) -> None:
    _write_minimal_failure_log(tmp_path)
    owned_header = tmp_path / "engine" / "latin1_owner.h"
    owned_header.parent.mkdir()
    owned_header.write_bytes(b"// FL-FAMILY: FL-1 \xe7\n")

    plan = ANALYZE_RUNS._line_centric_inline_comment_plan(tmp_path)

    assert plan["stats"]["scanned_comments"] == 1
    assert any(
        warning["kind"] == "non_utf8_source_decoded_with_replacement"
        and warning["path"] == "engine/latin1_owner.h"
        for warning in plan["warnings"]
    )
