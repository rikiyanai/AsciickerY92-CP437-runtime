#!/usr/bin/env python3
"""Guard FL-4377 visual-proof wording.

For terrain, water, foliage, and mountain visual lanes, Baseline A means the
June 14 clone comparator and the canonical human review surface is the labeled
multi-angle clone/current PNG grid at exact poses. Final dump files, HTML, rows,
validators, screenshots, and sidecars are companion diagnostics when linked from
the same grid package; they are not standalone acceptance surfaces.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SCAN_PATHS = [
    ROOT / "docs" / "audits" / "2026-06-17-fl4231-fl4184-git-line-audit" / "README.md",
    ROOT / "docs" / "agent" / "2026-06-18-fl4231-fl4184-structure-driven-grammar-plan.md",
    ROOT / "scripts" / "launch_godot_baseline_compositor_ab.py",
    ROOT / "scripts" / "validate_fl4184_foliage_gates.py",
]

REQUIRED_NEEDLES = {
    "docs/audits/2026-06-17-fl4231-fl4184-git-line-audit/README.md": [
        "Canonical evidence package going forward: the labeled clone/current PNG grid is the human-visible review surface.",
        "same-pose clone/current PNG grid is the canonical human review surface",
        "attached diagnostics for source-trace and falsification; they do not replace the PNG grid",
        "`Baseline A` means the June 14 pre-product-refactor clone",
        "why the agent thought that fix would work",
    ],
    "docs/agent/2026-06-18-fl4231-fl4184-structure-driven-grammar-plan.md": [
        "same-pose June 14 clone/current PNG grid is the canonical",
        "Exact final-cell rows are attached same-capture diagnostics",
        "they cannot replace the PNG grid",
        "`Baseline A` means the June 14 pre-product-refactor clone",
        "why the agent thought the fix would work",
    ],
    "scripts/launch_godot_baseline_compositor_ab.py": [
        'PROMOTED_BASELINE_ARCHIVE_DIR = Path("/private/tmp/y9-2-visual-baseline-20260614-1600")',
        'PROMOTED_BASELINE_SOURCE_IDENTITY = "cad593850"',
        'PROMOTED_BASELINE_LOCK_DATE = "2026-06-14"',
        'PROMOTED_BASELINE_LOCK_FL = "FL-4377"',
        '"source_identity_reason": "operator-directed FL-4377 Baseline A promotion to the June 14 pre-product-refactor clone"',
        '"clone_dir": str(PROMOTED_BASELINE_ARCHIVE_DIR)',
        '"a": "June 14 pre-product-refactor clone comparator"',
    ],
    "scripts/validate_fl4184_foliage_gates.py": [
        "The canonical acceptance surface is",
        "the multi-angle June 14 clone/current PNG grid at exact poses",
        "accepted rows\n    are same-capture",
        "Active Baseline A means that June 14 clone comparator",
    ],
}

FORBIDDEN_PATTERNS = [
    (
        re.compile(r"(?is)\b(?:html|index\.html|cells\.jsonl|metadata\.json|final_render_cell_dump|validator|screenshot|sidecar)\b.{0,120}\b(?:canonical|closure|acceptance|accepted|proof surface|proves|prove)\b"),
        "non-grid artifact promoted as canonical/proof/closure",
    ),
    (
        re.compile(r"(?is)\b(?:canonical|closure|acceptance|accepted|proof surface|proves|prove)\b.{0,120}\b(?:html|index\.html|cells\.jsonl|metadata\.json|final_render_cell_dump|validator|screenshot|sidecar)\b"),
        "non-grid artifact promoted as canonical/proof/closure",
    ),
    (
        re.compile(r"(?is)\bBaseline A\b.{0,120}\b(?:canonical|current comparison|acceptance|closure|proof)\b"),
        "Baseline A promoted for current visual comparison",
    ),
    (
        re.compile(r"(?is)\bJune 7\b.{0,120}\bBaseline A\b.{0,120}\b(?:comparator|comparison|baseline|proof|acceptance|closure)\b"),
        "June 7 archive mislabeled as active Baseline A",
    ),
    (
        re.compile(r"(?is)2026-06-07_fl4231_p215_5_58|baseline_a_2026_06_07_fl4231_p215_5_58|BASELINE A \[2026-06-07|baseline_a_promoted"),
        "June 7 promoted archive used as active Baseline A",
    ),
    (
        re.compile(r"(?is)\bBaseline A archive remains historical evidence\b"),
        "Baseline A name kept on historical archive",
    ),
    (
        re.compile(r"(?is)\bnot Baseline A\b"),
        "June 14 clone denied as Baseline A",
    ),
    (
        re.compile(r"(?is)\b(?:rows?|final-cell rows?|cells\.jsonl)\b.{0,80}\b(?:prove|proves|proving|closure|acceptance)\b"),
        "final-cell rows promoted beyond companion diagnostics",
    ),
    (
        re.compile(r"(?is)\b(?:html|index\.html|cells\.jsonl|metadata\.json|final_render_cell_dump|validator|screenshot|sidecar|median)\b.{0,120}\b(?:parity|accepted current|visual acceptance)\b"),
        "non-grid artifact promoted as visual parity evidence",
    ),
    (
        re.compile(r"(?is)\bgrid alone is not closure\b"),
        "PNG grid demoted below rows",
    ),
]

ALLOW_PATTERNS = [
    re.compile(r"(?is)same-capture companion diagnostics"),
    re.compile(r"(?is)accepted rows are same-capture"),
    re.compile(r"(?is)rows are same-capture diagnostics"),
    re.compile(r"(?is)same-capture diagnostics linked"),
    re.compile(r"(?is)same-capture diagnostics only"),
    re.compile(r"(?is)companion artifacts attached to that grid package"),
    re.compile(r"(?is)diagnostic evidence only"),
    re.compile(r"(?is)attached diagnostics for source-trace and falsification"),
    re.compile(r"(?is)do not replace the PNG grid"),
    re.compile(r"(?is)cannot replace the PNG grid"),
    re.compile(r"(?is)not a replacement proof surface"),
    re.compile(r"(?is)HTML is permitted as attached evidence"),
    re.compile(r"(?is)diagnostic views only when derived from the PNG"),
    re.compile(r"(?is)same-capture diagnostics that must be driven"),
    re.compile(r"(?is)cannot accept the visual fix without the PNG grid"),
    re.compile(r"(?is)same-capture companion artifacts"),
    re.compile(r"(?is)attached same-capture diagnostics"),
    re.compile(r"(?is)Companion dump artifacts"),
    re.compile(r"(?is)unless the same sentence anchors them as diagnostic"),
    re.compile(r"(?is)not a replacement proof surface"),
    re.compile(r"(?is)linked from that grid package"),
    re.compile(r"(?is)Accepted rows are same-capture diagnostics"),
    re.compile(r"(?is)not accepted"),
    re.compile(r"(?is)the rows must show"),
    re.compile(r"(?is)not accepted as a replacement proof path"),
    re.compile(r"(?is)not as the acceptance surface"),
    re.compile(r"(?is)not acceptance surfaces"),
    re.compile(r"(?is)not claim closure"),
    re.compile(r"(?is)must not claim closure"),
    re.compile(r"(?is)must not describe"),
    re.compile(r"(?is)not be presented as the current comparison authority"),
    re.compile(r"(?is)screenshot comparison shows"),
    re.compile(r"(?is)Baseline A`?,?\s+means the June 14"),
    re.compile(r"(?is)Baseline A`?,?\s+meaning the June 14"),
    re.compile(r"(?is)Active Baseline A means"),
]


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _allowed(snippet: str) -> bool:
    lower = snippet.lower()
    if "png grid" in lower and "same-capture" in lower and "diagnostic" in lower:
        return True
    return any(pattern.search(snippet) for pattern in ALLOW_PATTERNS)


def validate_text(text: str, *, label: str) -> list[str]:
    if label.endswith("test_fl4231_dump_cell_owner_contract.py"):
        return []
    errors: list[str] = []
    for pattern, reason in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 90)
            end = min(len(text), match.end() + 90)
            snippet = text[start:end].replace("\n", " ")
            if _allowed(snippet):
                continue
            errors.append(f"{label}: {reason}: {snippet}")
    return errors


def _active_repo_text(text: str, *, rel: str) -> str:
    if rel == "docs/audits/2026-06-17-fl4231-fl4184-git-line-audit/README.md":
        return text.split("\n## Failure Class Ledger", 1)[0]
    return text


def validate_repo() -> list[str]:
    errors: list[str] = []
    for path in SCAN_PATHS:
        rel = _rel(path)
        if not path.exists():
            errors.append(f"{rel}: missing required scan file")
            continue
        text = path.read_text(encoding="utf-8")
        active_text = _active_repo_text(text, rel=rel)
        if active_text:
            errors.extend(validate_text(active_text, label=rel))
        for needle in REQUIRED_NEEDLES.get(rel, []):
            if needle not in text:
                errors.append(f"{rel}: missing required canon text: {needle}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdin-content", action="store_true", help="Validate stdin as one edited content chunk")
    parser.add_argument("--label", default="<stdin>")
    args = parser.parse_args()

    if args.stdin_content:
        errors = validate_text(sys.stdin.read(), label=args.label)
    else:
        errors = validate_repo()
    if errors:
        print("GODOT_VISUAL_GRID_CANON_FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("GODOT_VISUAL_GRID_CANON_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
