#!/usr/bin/env python3
"""GSD phase audit for plan/summary/requirements execution coherence.

This audit complements plan_lint.py:
- plan_lint validates individual plan document quality and cross-doc syntax.
- gsd_audit validates execution coherence across phases:
  - completed phases should have PLAN->SUMMARY closure,
  - completed phases should not leave blocking checklist items open,
  - completed phases should not retain non-complete requirements,
  - plan requirement IDs should exist in REQUIREMENTS.md.

The tool supports a baseline file so existing known debt can be tracked
without blocking new regressions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    finding_id: str
    message: str
    file: str


_ROADMAP_PHASE_RE = re.compile(
    r"^-\s+\[([x ])\]\s+\*\*Phase\s+([\d.]+):\s+(.+?)\*\*",
    re.MULTILINE,
)
_REQ_LINE_RE = re.compile(
    r"^-\s+\[([x ])\]\s+\*\*([A-Z][\w-]+-\d+)\*\*:\s*(.+)$",
    re.MULTILINE,
)
_REQ_TABLE_RE = re.compile(
    r"^\|\s*([A-Z][\w-]+-\d+)\s*\|\s*Phase\s+([\d.]+)\s*\|\s*([^|]+?)\s*\|",
    re.MULTILINE,
)
_PHASE_HEADER_RE = re.compile(r"^###\s+Phase\s+([\d.]+):", re.MULTILINE)
_PLAN_REQ_LINE_RE = re.compile(r"^\*\*Requirements?\*\*:\s*(.+)$", re.MULTILINE)
_PLAN_REQ_LINE_ALT_RE = re.compile(r"^Requirements:\s*(.+)$", re.MULTILINE)
_REQ_ID_RE = re.compile(r"\b([A-Z][\w-]+-\d+)\b")
_PHASE_DIR_RE = re.compile(r"^(\d+(?:\.\d+)?)-")


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _phase_sort_key(phase_num: str) -> Tuple[float, str]:
    try:
        return (float(phase_num), phase_num)
    except ValueError:
        return (10_000.0, phase_num)


def _canon_phase_num(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return raw
    if "." in raw:
        left, right = raw.split(".", 1)
        left = str(int(left)) if left.isdigit() else left
        # Keep right side as provided (e.g., 10.5)
        return f"{left}.{right}"
    if raw.isdigit():
        return str(int(raw))
    return raw


def _extract_section(content: str, title: str) -> str:
    pattern = re.compile(
        rf"^#{{1,3}}\s+{re.escape(title)}\s*$" r"(.*?)(?=^#|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(content)
    return m.group(1) if m else ""


def _count_unchecked_boxes(text: str) -> int:
    return len(re.findall(r"^\s*-\s+\[\s\]\s+", text, flags=re.MULTILINE))


def _extract_plan_requirement_ids(plan_content: str) -> Set[str]:
    ids: Set[str] = set()
    m = _PLAN_REQ_LINE_RE.search(plan_content) or _PLAN_REQ_LINE_ALT_RE.search(plan_content)
    if not m:
        return ids
    for req_id in _REQ_ID_RE.findall(m.group(1)):
        ids.add(req_id)
    return ids


def _parse_roadmap(root: Path) -> Dict[str, Dict[str, str]]:
    path = root / ".planning" / "ROADMAP.md"
    if not path.exists():
        return {}
    phases: Dict[str, Dict[str, str]] = {}
    content = _load_text(path)
    for checked, num, name in _ROADMAP_PHASE_RE.findall(content):
        phases[_canon_phase_num(num)] = {
            "status": "complete" if checked == "x" else "pending",
            "name": name.strip(),
        }
    return phases


def _parse_requirements(
    root: Path,
) -> Tuple[Set[str], Dict[str, List[Tuple[str, str]]]]:
    """Return (all_req_ids, phase_to_requirements[(req_id, status)])."""
    path = root / ".planning" / "REQUIREMENTS.md"
    if not path.exists():
        return set(), {}

    content = _load_text(path)
    all_req_ids: Set[str] = set()

    phase_sections: Dict[str, str] = {}
    headers = list(_PHASE_HEADER_RE.finditer(content))
    for i, h in enumerate(headers):
        phase_num = _canon_phase_num(h.group(1))
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(content)
        phase_sections[phase_num] = content[start:end]

    phase_to_requirements: Dict[str, List[Tuple[str, str]]] = {}
    for phase_num, section in phase_sections.items():
        entries: List[Tuple[str, str]] = []
        for checked, req_id, _desc in _REQ_LINE_RE.findall(section):
            status = "complete" if checked == "x" else "pending"
            all_req_ids.add(req_id)
            entries.append((req_id, status))
        phase_to_requirements[phase_num] = entries

    # Table statuses override checkbox status when present.
    table_status: Dict[str, str] = {}
    for req_id, _phase, status_raw in _REQ_TABLE_RE.findall(content):
        status = status_raw.strip().lower()
        if "complete" in status:
            table_status[req_id] = "complete"
        else:
            table_status[req_id] = "not_complete"
        all_req_ids.add(req_id)

    for phase_num, entries in phase_to_requirements.items():
        updated: List[Tuple[str, str]] = []
        for req_id, status in entries:
            if req_id in table_status:
                status = table_status[req_id]
            updated.append((req_id, status))
        phase_to_requirements[phase_num] = updated

    return all_req_ids, phase_to_requirements


def _discover_phase_dirs(root: Path) -> Dict[str, Path]:
    phase_root = root / ".planning" / "phases"
    result: Dict[str, Path] = {}
    if not phase_root.exists():
        return result
    for child in sorted(phase_root.iterdir()):
        if not child.is_dir():
            continue
        m = _PHASE_DIR_RE.match(child.name)
        if not m:
            continue
        result[_canon_phase_num(m.group(1))] = child
    return result


def _phase_scope_from_paths(paths: Sequence[str]) -> Optional[Set[str]]:
    """Infer phase scope from changed files.

    Returns:
      None -> audit all phases
      set() -> no phase-scoped files found
      {phase ids} -> audit only those phases
    """
    if not paths:
        return None

    scope: Set[str] = set()
    for p in paths:
        norm = p.replace("\\", "/")
        if norm.endswith("/ROADMAP.md") or norm.endswith("/STATE.md") or norm.endswith("/REQUIREMENTS.md"):
            return None
        m = re.search(r"\.planning/phases/(\d+(?:\.\d+)?)-[^/]+/", norm)
        if m:
            scope.add(_canon_phase_num(m.group(1)))
    return scope


def run_audit(root: Path, phase_scope: Optional[Set[str]] = None) -> List[Finding]:
    findings: List[Finding] = []

    roadmap = _parse_roadmap(root)
    all_req_ids, phase_reqs = _parse_requirements(root)
    phase_dirs = _discover_phase_dirs(root)

    phase_ids: Set[str] = set(roadmap.keys()) | set(phase_dirs.keys()) | set(phase_reqs.keys())
    if phase_scope is not None:
        phase_ids &= phase_scope

    for phase_num in sorted(phase_ids, key=_phase_sort_key):
        phase_info = roadmap.get(phase_num)
        phase_status = phase_info["status"] if phase_info else "unknown"
        phase_dir = phase_dirs.get(phase_num)

        if phase_status == "complete" and phase_dir is None:
            findings.append(
                Finding(
                    severity="P1",
                    check="phase-dir-missing",
                    finding_id=f"phase-dir-missing::{phase_num}",
                    message=f"ROADMAP marks Phase {phase_num} complete but phase directory is missing",
                    file="legacy phase ledger",
                )
            )
            continue

        if phase_dir is None:
            continue

        plan_paths = sorted(phase_dir.glob("*-PLAN.md"))
        for plan_path in plan_paths:
            plan_rel = str(plan_path.relative_to(root))
            plan_content = _load_text(plan_path)

            summary_path = plan_path.with_name(plan_path.name.replace("-PLAN.md", "-SUMMARY.md"))
            if phase_status == "complete" and not summary_path.exists():
                findings.append(
                    Finding(
                        severity="P1",
                        check="plan-summary-missing",
                        finding_id=f"plan-summary-missing::{plan_rel}",
                        message="Completed-phase plan has no matching SUMMARY file",
                        file=plan_rel,
                    )
                )

            if phase_status == "complete":
                block_sec = _extract_section(plan_content, "Blocking Checklist")
                open_blocks = _count_unchecked_boxes(block_sec)
                if open_blocks > 0:
                    findings.append(
                        Finding(
                            severity="P2",
                            check="blocking-checklist-open",
                            finding_id=f"blocking-checklist-open::{plan_rel}",
                            message=(
                                "Plan is in a completed phase but has open blocking checklist "
                                f"items ({open_blocks})"
                            ),
                            file=plan_rel,
                        )
                    )

                success_sec = _extract_section(plan_content, "Success Criteria")
                open_success = _count_unchecked_boxes(success_sec)
                if open_success > 0:
                    findings.append(
                        Finding(
                            severity="P2",
                            check="success-criteria-open",
                            finding_id=f"success-criteria-open::{plan_rel}",
                            message=(
                                "Plan is in a completed phase but has unchecked success criteria "
                                f"items ({open_success})"
                            ),
                            file=plan_rel,
                        )
                    )

            plan_req_ids = _extract_plan_requirement_ids(plan_content)
            for req_id in sorted(plan_req_ids):
                if req_id not in all_req_ids:
                    findings.append(
                        Finding(
                            severity="P1",
                            check="phantom-requirement",
                            finding_id=f"phantom-requirement::{plan_rel}::{req_id}",
                            message=f"Plan references requirement {req_id} not present in REQUIREMENTS.md",
                            file=plan_rel,
                        )
                    )

        req_entries = phase_reqs.get(phase_num, [])
        if phase_status == "complete" and req_entries:
            not_complete = [req_id for req_id, status in req_entries if status != "complete"]
            if not_complete:
                findings.append(
                    Finding(
                        severity="P1",
                        check="phase-requirements-incomplete",
                        finding_id=f"phase-requirements-incomplete::{phase_num}",
                        message=(
                            f"Phase {phase_num} is complete in ROADMAP but has non-complete "
                            f"requirements: {', '.join(not_complete)}"
                        ),
                        file=".planning/REQUIREMENTS.md",
                    )
                )

    return findings


def _load_baseline(path: Optional[Path]) -> Set[str]:
    if path is None or not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    ids = payload.get("finding_ids", [])
    if not isinstance(ids, list):
        return set()
    return {str(x) for x in ids}


def _write_baseline(path: Path, findings: Sequence[Finding]) -> None:
    payload = {
        "version": 1,
        "finding_ids": sorted({f.finding_id for f in findings}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _format_findings(findings: Iterable[Finding]) -> str:
    by_sev: Dict[str, List[Finding]] = {"P1": [], "P2": [], "P3": []}
    for f in findings:
        by_sev.setdefault(f.severity, []).append(f)

    lines: List[str] = []
    for sev, label in (("P1", "BLOCKERS"), ("P2", "WARNINGS"), ("P3", "INFO")):
        items = by_sev.get(sev, [])
        if not items:
            continue
        lines.append(f"\n--- {label} ({len(items)}) ---")
        for f in items:
            lines.append(f"[{f.severity}] {f.check}: {f.message} ({f.file})")
    return "\n".join(lines).strip()


def _exit_code(findings: Sequence[Finding], strict: bool) -> int:
    has_p1 = any(f.severity == "P1" for f in findings)
    has_p2 = any(f.severity == "P2" for f in findings)
    if has_p1:
        return 2
    if strict and has_p2:
        return 1
    if has_p2:
        return 1
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit GSD phase execution coherence (PLAN/SUMMARY/REQUIREMENTS)."
    )
    parser.add_argument("paths", nargs="*", help="Optional changed file paths for scoped audit")
    parser.add_argument("--root", default=".", help="Repository root (default: .)")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument(
        "--baseline",
        default=".planning/gsd_audit_baseline.json",
        help="Baseline file for known findings",
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="Ignore baseline filtering",
    )
    parser.add_argument(
        "--all-findings",
        action="store_true",
        help="Report all findings (default: report only new findings)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Write/overwrite baseline with current findings and exit 0",
    )

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    phase_scope = _phase_scope_from_paths(args.paths)
    findings = run_audit(root, phase_scope=phase_scope)

    baseline_path = None if args.no_baseline else (root / args.baseline)
    baseline_ids = _load_baseline(baseline_path)
    new_findings = [f for f in findings if f.finding_id not in baseline_ids]

    if args.update_baseline:
        if baseline_path is None:
            print("Cannot update baseline when --no-baseline is set", file=sys.stderr)
            return 2
        _write_baseline(baseline_path, findings)
        print(f"Baseline updated: {baseline_path}")
        return 0

    report_findings = findings if args.all_findings else new_findings
    exit_findings = new_findings

    if args.json:
        payload = {
            "root": str(root),
            "scoped": phase_scope is not None,
            "phase_scope": sorted(phase_scope) if phase_scope is not None else None,
            "counts": {
                "all": len(findings),
                "new": len(new_findings),
                "baselined": len(findings) - len(new_findings),
            },
            "findings": [
                {
                    "severity": f.severity,
                    "check": f.check,
                    "id": f.finding_id,
                    "message": f.message,
                    "file": f.file,
                    "is_new": f.finding_id not in baseline_ids,
                }
                for f in report_findings
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("=== GSD Audit ===")
        if phase_scope is None:
            print("Scope: all phases")
        else:
            print(f"Scope: phases {', '.join(sorted(phase_scope, key=_phase_sort_key)) or '(none)'}")
        print(f"Findings: all={len(findings)}, new={len(new_findings)}, baselined={len(findings) - len(new_findings)}")
        if report_findings:
            print(_format_findings(report_findings))
        else:
            print("No findings.")

    return _exit_code(exit_findings, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
