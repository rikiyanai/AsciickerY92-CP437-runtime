"""Negative controls for exact source-document ownership in standalone CI."""
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "document_placement", Path(__file__).resolve().parents[1] /
    "scripts/check_document_placement.py")
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


def test_exact_owned_source_docs_and_canonical_docs_are_allowed():
    paths = {"README.md", "docs/VERIFICATION.md", "vendor/lib/README.md", "vendor/lib/LICENSE"}
    assert not POLICY.violations(paths, {"vendor/lib/README.md", "vendor/lib/LICENSE"})


def test_new_unowned_docs_are_rejected_without_directory_exemptions():
    paths = {"vendor/lib/README.md", "vendor/lib/session.md"}
    assert POLICY.violations(paths, {"vendor/lib/README.md"}) == [
        "unowned document: vendor/lib/session.md"]


def test_stale_and_glob_owners_and_instruction_files_are_rejected():
    problems = POLICY.violations({"docs/AGENTS.md"}, {"vendor/*", "missing.md"})
    assert len(problems) == 3
    assert "instruction file: docs/AGENTS.md" in problems
