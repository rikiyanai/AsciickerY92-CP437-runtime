#!/usr/bin/env python3
"""Reject tests that make shader presentation implementation a required outcome.

FL-4359 visual acceptance belongs to headed baseline/current evidence and the
matching runtime recorder.  A Python test may measure rendered contrast, and it
may assert that a rejected implementation symbol is absent.  It must not keep a
contrast helper, rail, lattice, blend, threshold, call order, or source fragment
alive by asserting against shader source text.

The pre-commit route scans the complete staged-index test corpus.  Scanning the
whole corpus, instead of only changed tests, prevents a source deletion from
being blocked by a previously committed implementation-pinning test.
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF_TEST_PATH = "scripts/tests/test_no_static_contrast_contracts.py"
ABI_ONLY_TEST_PATHS = {
    # FL-4260 checks the serialized profile/control wiring.  The field name is
    # an ABI/schema fact; the test does not prescribe rendered appearance.
    "scripts/tests/test_fl4260_color_shade_slider_wiring.py",
}
REFERENCE_TARGET_TEST_PATHS = {
    # This lane measures an operator-supplied reference image.  It calibrates a
    # detector and does not claim that a current build passed presentation.
    "scripts/test_fl4101_product_day_godray_contract.py",
}
CONTRAST_MARKER_RE = re.compile(
    r"contrast|SKY_INK_(?:MIN|MAX)_CONTRAST|"
    r"final_product_(?:index_at_quarter_lattice|color_samples_are_continuous)|"
    r"normalized_weights|color_weight_sum|fl4330_cell_uniform|"
    r"framebuffer[-_ ]wide (?:quarter[-_ ]centre )?lattice|global colour lattice",
    re.IGNORECASE,
)
STATIC_LOOKUP_METHODS = {"count", "find", "index", "rfind", "rindex"}
STATIC_CONTRACT_HELPERS = {
    "_replace_in_function",
    "require_ordered",
    "require_patterns",
    "replace_in_function",
    "replace_once",
    "replace_once_after",
}
FRAME_OBSERVABLE_RE = re.compile(
    r"frame|\.png|cell[_-]?dump|final[_-]?cell|analysis_summary|receipt",
    re.IGNORECASE,
)
RUN_IDENTITY_MARKERS = ("scene", "pose", "time", "source_commit", "receipt")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    reason: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}"


def _is_test_path(path: str) -> bool:
    if path == SELF_TEST_PATH or not path.endswith(".py"):
        return False
    name = Path(path).name
    if not name.startswith("test_"):
        return False
    return path.startswith(("scripts/", "tests/"))


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _has_runtime_measurement_call(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call)
        and _call_name(child).lower().startswith(("measure", "analy"))
        for child in ast.walk(node)
    )


def _target_names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _literal_has_contrast_marker(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and CONTRAST_MARKER_RE.search(child.value)
        for child in ast.walk(node)
    )


def _contrast_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if value is not None and _literal_has_contrast_marker(value):
                for target in targets:
                    names.update(_target_names(target))
    names.update(
        child.id
        for child in ast.walk(tree)
        if isinstance(child, ast.Name) and CONTRAST_MARKER_RE.search(child.id)
    )
    return names


def _is_contrast_related(node: ast.AST, names: set[str]) -> bool:
    if _literal_has_contrast_marker(node):
        return True
    return any(
        isinstance(child, ast.Name) and child.id in names
        for child in ast.walk(node)
    )


def _is_allowed_absence_assert(node: ast.Assert, names: set[str]) -> bool:
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.NotIn):
        return False
    return _is_contrast_related(test.left, names) or _is_contrast_related(
        test.comparators[0], names
    )


def validate_test_source(source: str, *, path: str) -> list[Finding]:
    if path in ABI_ONLY_TEST_PATHS:
        return []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [Finding(path, exc.lineno or 1, "cannot parse test while enforcing contrast policy")]

    names = _contrast_names(tree)
    findings: list[Finding] = []
    allowed_absence_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and _is_allowed_absence_assert(node, names):
            allowed_absence_nodes.update(id(child) for child in ast.walk(node))

    for node in ast.walk(tree):
        if id(node) in allowed_absence_nodes:
            continue
        if isinstance(node, ast.Compare):
            for index, operator in enumerate(node.ops):
                if not isinstance(operator, ast.In):
                    continue
                left = node.left if index == 0 else node.comparators[index - 1]
                right = node.comparators[index]
                if _is_contrast_related(left, names) or _is_contrast_related(right, names):
                    findings.append(
                        Finding(
                            path,
                            node.lineno,
                            "positive source-membership presentation contract is banned",
                        )
                    )
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node)
        if call_name in STATIC_LOOKUP_METHODS:
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else node
            if _is_contrast_related(receiver, names) or any(
                _is_contrast_related(arg, names) for arg in node.args
            ):
                findings.append(
                    Finding(
                        path,
                        node.lineno,
                        f"source-text {call_name} presentation contract is banned",
                    )
                )
        if call_name in STATIC_CONTRACT_HELPERS and any(
            _is_contrast_related(arg, names) for arg in node.args
        ):
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    f"{call_name} may not require a presentation implementation",
                )
            )
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        helper_call_present = any(
            isinstance(child, ast.Call)
            and _call_name(child) in STATIC_CONTRACT_HELPERS
            for child in ast.walk(node)
        )
        if not helper_call_present:
            continue
        for decorator in node.decorator_list:
            if _literal_has_contrast_marker(decorator):
                findings.append(
                    Finding(
                        path,
                        decorator.lineno,
                        "parameterized mutation fixture may not require a presentation implementation",
                    )
                )
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        function_source = ast.get_source_segment(source, node) or ""
        if not CONTRAST_MARKER_RE.search(function_source):
            continue
        if not FRAME_OBSERVABLE_RE.search(function_source):
            continue
        if not _has_runtime_measurement_call(node):
            continue
        if path in REFERENCE_TARGET_TEST_PATHS:
            continue
        missing_identity = [
            marker
            for marker in RUN_IDENTITY_MARKERS
            if marker not in function_source.lower()
        ]
        if missing_identity:
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    "rendered-contrast test lacks exact run identity fields: "
                    + ", ".join(missing_identity),
                )
            )
    return sorted(set(findings), key=lambda item: (item.path, item.line, item.reason))


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def staged_index_sources() -> list[tuple[str, str]]:
    listed = _git("ls-files")
    if listed.returncode != 0:
        raise RuntimeError((listed.stderr or listed.stdout).strip())
    sources: list[tuple[str, str]] = []
    for path in sorted(filter(_is_test_path, listed.stdout.splitlines())):
        blob = _git("show", f":{path}")
        if blob.returncode == 0:
            sources.append((path, blob.stdout))
    return sources


def worktree_sources(paths: list[str]) -> list[tuple[str, str]]:
    selected = paths or [
        str(path.relative_to(ROOT))
        for base in (ROOT / "scripts", ROOT / "tests")
        if base.exists()
        for path in base.rglob("test_*.py")
        if _is_test_path(str(path.relative_to(ROOT)))
    ]
    return [
        (path, (ROOT / path).read_text(encoding="utf-8"))
        for path in sorted(set(selected))
        if _is_test_path(path) and (ROOT / path).is_file()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument(
        "--staged-index",
        action="store_true",
        help="Scan every tracked test from the staged Git index.",
    )
    args = parser.parse_args(argv)
    try:
        sources = staged_index_sources() if args.staged_index else worktree_sources(args.paths)
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        print(f"STATIC_CONTRAST_TEST_GUARD_ERROR: {exc}", file=sys.stderr)
        return 2

    findings = [
        finding
        for path, source in sources
        for finding in validate_test_source(source, path=path)
    ]
    if findings:
        print("STATIC_CONTRAST_TEST_GUARD_BLOCKED", file=sys.stderr)
        print(
            "Static tests may not preserve contrast rails, colour lattices, blends, helpers, "
            "source order, or thresholds. "
            "Use headed baseline/current evidence or runtime image measurements carrying scene, "
            "pose, time, source_commit, and receipt identity. Negative absence assertions and "
            "ABI-only wiring tests remain allowed.",
            file=sys.stderr,
        )
        for finding in findings:
            print(f"- {finding.render()}", file=sys.stderr)
        return 1
    print(f"STATIC_CONTRAST_TEST_GUARD_PASS tests={len(sources)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
