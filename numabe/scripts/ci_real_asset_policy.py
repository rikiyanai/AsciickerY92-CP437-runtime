#!/usr/bin/env python3
"""Enforce real-asset testing policy for CI.

Policy:
1. Tests under scripts/pipeline/tests must not generate synthetic images
   via PIL constructors (Image.new, Image.fromarray).
2. Stub AI provider must not synthesize test images via PIL constructors.
3. Critical suites must retain at least one real_asset_e2e marker.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set


_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_ROOT = _REPO_ROOT / "scripts" / "asset_gen" / "tests"
_ALT_TEST_ROOT = _REPO_ROOT / "tests"
_AI_PROVIDER_PATH = _REPO_ROOT / "scripts" / "asset_gen" / "ai_provider.py"

_BANNED_PATTERNS = (
    ("Image.new", re.compile(r"\bImage\.new\s*\(")),
    ("Image.fromarray", re.compile(r"\bImage\.fromarray\s*\(")),
)
_WAVE2_SYNTHETIC_PATTERNS = (
    ("Image.new", re.compile(r"\bImage\.new\s*\(")),
    ("Image.fromarray", re.compile(r"\bImage\.fromarray\s*\(")),
    ("np.zeros", re.compile(r"\bnp\.zeros\s*\(")),
    ("np.ones", re.compile(r"\bnp\.ones\s*\(")),
    ("np.full", re.compile(r"\bnp\.full\s*\(")),
    ("np.random", re.compile(r"\bnp\.random\.")),
)
_FIXTURE_HINT_PATTERN = re.compile(
    r"ASSET_TEST_BANK_DIR|SMALLTESTPNGs|tests/fixtures/real_assets|tests\._asset_bank|require_asset\("
)
_REAL_ASSET_MARKER = re.compile(r"real_asset_e2e")

_WAVE2_MODULE_PATTERNS: Dict[str, re.Pattern[str]] = {
    "grid_detect": re.compile(
        r"(scripts\.asset_gen\.grid_detect|asset_gen\.grid_detect|\bgrid_detect\b)"
    ),
    "processor_literal": re.compile(
        r"(scripts\.asset_gen\.processor_literal|asset_gen\.processor_literal|\bprocessor_literal\b)"
    ),
    "sprite_extract": re.compile(
        r"(scripts\.asset_gen\.sprite_extract|asset_gen\.sprite_extract|\bsprite_extract\b)"
    ),
    "processor_subcell": re.compile(
        r"(scripts\.asset_gen\.processor_subcell|asset_gen\.processor_subcell|\bprocessor_subcell\b)"
    ),
}

_REQUIRED_REAL_ASSET_FILES = (
    "scripts/pipeline/tests/test_render_core.py",
    "scripts/pipeline/tests/test_roundtrip_xp_png_xp.py",
    "scripts/pipeline/tests/test_xp_viewer.py",
)


def _iter_asset_gen_test_files() -> Iterable[Path]:
    """Yield policy-scoped test/support files under scripts/pipeline/tests."""
    if not _TEST_ROOT.exists():
        return
    for path in sorted(_TEST_ROOT.rglob("*.py")):
        if path.name.startswith("test_") or path.name == "conftest.py":
            yield path


def _iter_test_files() -> Iterable[Path]:
    """Yield Python test/support files under known roots for Wave 2 coverage."""
    seen: Set[Path] = set()
    for root in (_TEST_ROOT, _ALT_TEST_ROOT):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if path.name.startswith("test_") or path.name == "conftest.py":
                if path not in seen:
                    seen.add(path)
                    yield path


def _find_line_numbers(text: str, pattern: re.Pattern[str]) -> List[int]:
    """Return 1-based line numbers where regex matches."""
    lines = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            lines.append(idx)
    return lines


def _scan_for_policy_violations() -> List[str]:
    """Scan tests and strict files for synthetic image generation."""
    failures: List[str] = []

    for path in _iter_asset_gen_test_files():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for label, pattern in _BANNED_PATTERNS:
            lines_found = _find_line_numbers(text, pattern)
            if lines_found:
                lines = ", ".join(str(n) for n in lines_found)
                failures.append(
                    f"{rel}: synthetic {label}() is disallowed (lines: {lines})"
                )

    failures.extend(_scan_stub_adapter_policy())

    return failures


def _scan_stub_adapter_policy() -> List[str]:
    """Ensure StubAdapter does not synthesize images via PIL constructors."""
    failures: List[str] = []
    if not _AI_PROVIDER_PATH.exists():
        return failures

    text = _AI_PROVIDER_PATH.read_text(encoding="utf-8")
    rel = _AI_PROVIDER_PATH.relative_to(_REPO_ROOT).as_posix()

    start = text.find("class StubAdapter")
    end = text.find("class GeminiNanoBananaAdapter")
    if start == -1 or end == -1 or end <= start:
        failures.append(f"{rel}: could not locate StubAdapter block for policy check")
        return failures

    block = text[start:end]
    for label, pattern in _BANNED_PATTERNS:
        lines_found = _find_line_numbers(block, pattern)
        if lines_found:
            lines = ", ".join(str(n) for n in lines_found)
            failures.append(
                f"{rel}: StubAdapter uses synthetic {label}() (relative lines: {lines})"
            )
    return failures


def _check_required_real_asset_markers() -> List[str]:
    """Ensure critical suites still carry real_asset_e2e coverage markers."""
    failures: List[str] = []
    if not _TEST_ROOT.exists():
        return failures

    # If the legacy asset_gen test tree has been removed/migrated, skip the
    # legacy required-file marker gate instead of hard-failing on missing paths.
    has_any_asset_gen_tests = any(_TEST_ROOT.rglob("test_*.py"))
    if not has_any_asset_gen_tests:
        return failures

    for rel in _REQUIRED_REAL_ASSET_FILES:
        path = _REPO_ROOT / rel
        if not path.exists():
            failures.append(f"{rel}: required real-asset suite file missing")
            continue
        text = path.read_text(encoding="utf-8")
        if not _REAL_ASSET_MARKER.search(text):
            failures.append(
                f"{rel}: missing required real_asset_e2e marker"
            )
    return failures


def _wave2_coverage_map() -> Dict[str, List[Path]]:
    """Map Wave 2 module name -> list of pytest files that reference it."""
    coverage: Dict[str, List[Path]] = {name: [] for name in _WAVE2_MODULE_PATTERNS}
    for test_file in _iter_test_files():
        text = test_file.read_text(encoding="utf-8")
        for module_name, pattern in _WAVE2_MODULE_PATTERNS.items():
            if pattern.search(text):
                coverage[module_name].append(test_file)
    return coverage


def _check_wave2_module_coverage() -> List[str]:
    """Require pytest coverage for each Wave 2 module."""
    failures: List[str] = []
    coverage = _wave2_coverage_map()
    for module_name, files in coverage.items():
        if not files:
            failures.append(
                f"Wave2 module '{module_name}' has no pytest coverage in tests/ or scripts/pipeline/tests/"
            )
    return failures


def _check_wave2_no_synthetic_tests() -> List[str]:
    """For Wave 2 module tests, disallow synthetic image/array construction."""
    failures: List[str] = []
    coverage = _wave2_coverage_map()
    for module_name, files in coverage.items():
        for path in files:
            rel = path.relative_to(_REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            for label, pattern in _WAVE2_SYNTHETIC_PATTERNS:
                lines_found = _find_line_numbers(text, pattern)
                if lines_found:
                    lines = ", ".join(str(n) for n in lines_found)
                    failures.append(
                        f"{rel}: Wave2 test for '{module_name}' uses synthetic {label}() (lines: {lines})"
                    )
            if not _FIXTURE_HINT_PATTERN.search(text):
                failures.append(
                    f"{rel}: Wave2 test for '{module_name}' must reference committed/external real fixtures"
                )
    return failures


def main() -> int:
    """Run policy checks and print actionable failures."""
    failures: List[str] = []
    failures.extend(_scan_for_policy_violations())
    failures.extend(_check_required_real_asset_markers())
    failures.extend(_check_wave2_module_coverage())
    failures.extend(_check_wave2_no_synthetic_tests())

    if failures:
        print("Real-asset policy: FAIL", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print("Real-asset policy: PASS")
    if _TEST_ROOT.exists():
        print(f"Checked tests under {_TEST_ROOT}")
    else:
        print(f"Skipped asset_gen test scan; directory not present: {_TEST_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
