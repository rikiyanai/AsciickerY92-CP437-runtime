#!/usr/bin/env python3
"""Phase driver coverage gate and optional driver runner.

Purpose:
- Enforce that each roadmap phase has at least one declared user-simulation driver.
- Provide a single command to run driver commands for a given phase.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli_style import status as cli_status  # noqa: E402


_ROADMAP_PHASE_RE = re.compile(
    r"^-\s+\[([x ])\]\s+\*\*Phase\s+([\d.]+):\s+(.+?)\*\*",
    re.MULTILINE,
)


@dataclass
class RoadmapPhase:
    phase: str
    status: str
    name: str


def _canon_phase_num(raw: str) -> str:
    raw = raw.strip()
    if "." in raw:
        left, right = raw.split(".", 1)
        left = str(int(left)) if left.isdigit() else left
        return f"{left}.{right}"
    if raw.isdigit():
        return str(int(raw))
    return raw


def _parse_roadmap(root: Path) -> List[RoadmapPhase]:
    roadmap = root / ".planning" / "ROADMAP.md"
    if not roadmap.exists():
        return []
    content = roadmap.read_text(encoding="utf-8")
    phases = [
        RoadmapPhase(
            phase=_canon_phase_num(num),
            status="complete" if checked == "x" else "pending",
            name=_name.strip(),
        )
        for checked, num, _name in _ROADMAP_PHASE_RE.findall(content)
    ]
    # unique, preserve order
    out: List[RoadmapPhase] = []
    seen = set()
    for p in phases:
        if p.phase not in seen:
            out.append(p)
            seen.add(p.phase)
    return out


def _phase_sort_key(phase_num: str) -> Tuple[float, str]:
    try:
        return (float(phase_num), phase_num)
    except ValueError:
        return (10_000.0, phase_num)


@dataclass
class Driver:
    phase: str
    driver_id: str
    description: str
    command: str
    argv: Tuple[str, ...]
    simulates_user: bool


def _parse_roadmap_phases(root: Path) -> List[str]:
    return [p.phase for p in _parse_roadmap(root)]


def load_manifest(path: Path) -> Dict[str, List[Driver]]:
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    phases = payload.get("phases", {})
    if not isinstance(phases, dict):
        raise ValueError("Manifest 'phases' must be an object")

    result: Dict[str, List[Driver]] = {}
    for phase_raw, entries in phases.items():
        phase = _canon_phase_num(str(phase_raw))
        if not isinstance(entries, list):
            raise ValueError(f"Phase '{phase}' entries must be a list")
        drivers: List[Driver] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"Phase '{phase}' has non-object driver entry")
            command = str(entry.get("command", "")).strip()
            if not command:
                raise ValueError(f"Phase '{phase}' has driver with empty command")
            try:
                argv = tuple(shlex.split(command))
            except ValueError as exc:
                raise ValueError(
                    f"Phase '{phase}' driver {entry.get('id', 'unnamed')}: invalid shell quoting ({exc})"
                ) from exc
            if not argv:
                raise ValueError(
                    f"Phase '{phase}' driver {entry.get('id', 'unnamed')}: command produced no argv tokens"
                )
            drivers.append(
                Driver(
                    phase=phase,
                    driver_id=str(entry.get("id", "")).strip() or "unnamed",
                    description=str(entry.get("description", "")).strip(),
                    command=command,
                    argv=argv,
                    simulates_user=bool(entry.get("simulates_user", False)),
                )
            )
        result[phase] = drivers
    return result


def _is_browser_driver_command(command: str) -> bool:
    cmd = command.lower()
    return (
        "playwright" in cmd
        or "tests/e2e/" in cmd
        or "tests\\e2e\\" in cmd
    )


def _phase_requires_browser_driver(phase: RoadmapPhase) -> bool:
    # Explicitly enforce browser-runtime coverage for known web/workbench phases.
    if phase.phase.startswith("13"):
        return True
    if phase.phase in {"16.4", "16.5", "16.6"}:
        return True

    lowered = phase.name.lower()
    return any(token in lowered for token in ("web", "workbench", "viewer"))


def coverage_findings(
    roadmap_phases: Sequence[RoadmapPhase],
    manifest: Dict[str, List[Driver]],
) -> List[str]:
    findings: List[str] = []

    roadmap_nums = [p.phase for p in roadmap_phases]
    for phase_meta in roadmap_phases:
        phase = phase_meta.phase
        drivers = manifest.get(phase, [])
        if not drivers:
            findings.append(f"Phase {phase}: missing driver entries in manifest")
            continue
        if not any(d.simulates_user for d in drivers):
            findings.append(f"Phase {phase}: no driver marked simulates_user=true")
            continue

        if _phase_requires_browser_driver(phase_meta):
            has_browser_driver = any(
                d.simulates_user and _is_browser_driver_command(d.command) for d in drivers
            )
            if not has_browser_driver:
                findings.append(
                    "Phase "
                    f"{phase}: web/workbench phase requires at least one browser E2E "
                    "driver (playwright or scripts/pipeline/tests/e2e/*)"
                )

    extra = sorted(set(manifest.keys()) - set(roadmap_nums), key=_phase_sort_key)
    for phase in extra:
        findings.append(f"Manifest has phase {phase} not present in ROADMAP")

    return findings


def command_findings(root: Path, manifest: Dict[str, List[Driver]]) -> List[str]:
    findings: List[str] = []
    for phase, drivers in manifest.items():
        for drv in drivers:
            cmd = drv.command.strip()
            if "pytest" not in cmd:
                continue
            tokens = list(drv.argv)

            if _is_browser_driver_command(cmd):
                has_addopts_override = any(tok.startswith("addopts=") for tok in tokens)
                if not has_addopts_override:
                    findings.append(
                        f"Phase {phase} driver {drv.driver_id}: browser E2E driver must "
                        "override pytest addopts (use -o \"addopts=\")"
                    )
                has_e2e_marker = any(
                    tok == "-m" and i + 1 < len(tokens) and tokens[i + 1].strip() == "e2e"
                    for i, tok in enumerate(tokens)
                )
                if not has_e2e_marker:
                    findings.append(
                        f"Phase {phase} driver {drv.driver_id}: browser E2E driver must "
                        "select e2e tests explicitly (use -m e2e)"
                    )

            test_paths = [t for t in tokens if t.endswith(".py")]
            if not test_paths:
                findings.append(
                    f"Phase {phase} driver {drv.driver_id}: pytest command has no explicit test path"
                )
                continue
            for rel in test_paths:
                if rel.startswith("-"):
                    continue
                if not (root / rel).exists():
                    findings.append(
                        f"Phase {phase} driver {drv.driver_id}: missing test path '{rel}'"
                    )
    return findings


def run_phase_drivers(
    root: Path,
    phase: str,
    manifest: Dict[str, List[Driver]],
) -> int:
    phase = _canon_phase_num(phase)
    drivers = manifest.get(phase, [])
    if not drivers:
        print(f"No drivers found for phase {phase}", file=sys.stderr)
        return 2

    print(f"=== Running phase {phase} drivers ({len(drivers)}) ===")
    for drv in drivers:
        print(f"\n[{drv.driver_id}] {drv.description}")
        print(f"$ {drv.command}")
        proc = subprocess.run(
            list(drv.argv),
            cwd=str(root),
            shell=False,
            text=True,
        )
        if proc.returncode != 0:
            print(
                f"Driver failed: {drv.driver_id} (exit {proc.returncode})",
                file=sys.stderr,
            )
            return proc.returncode
    return 0


def run_phase_sequence(
    root: Path,
    phases: Sequence[str],
    manifest: Dict[str, List[Driver]],
) -> int:
    if not phases:
        print("No phases selected for execution.")
        return 0

    print(f"=== Running phase driver sequence ({len(phases)} phases) ===")
    for phase in phases:
        code = run_phase_drivers(root, phase, manifest)
        if code != 0:
            print(f"Phase sequence failed at phase {phase}", file=sys.stderr)
            return code
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Phase driver coverage gate")
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument(
        "--manifest",
        default="scripts/phase_drivers/manifest.json",
        help="Driver manifest path (relative to root)",
    )
    parser.add_argument(
        "--phase",
        help="Run drivers for a specific phase (requires --run)",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute driver commands for --phase",
    )
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Execute drivers for all roadmap phases in order",
    )
    parser.add_argument(
        "--run-completed",
        action="store_true",
        help="Execute drivers only for roadmap phases marked complete",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    manifest_path = root / args.manifest

    try:
        manifest = load_manifest(manifest_path)
    except Exception as e:
        print(cli_status("ERROR", f"manifest-load: {e}"), file=sys.stderr)
        return 2

    roadmap = _parse_roadmap(root)
    roadmap_phases = [p.phase for p in roadmap]
    if not roadmap_phases:
        print(cli_status("ERROR", "roadmap: no phases found in the legacy phase ledger"), file=sys.stderr)
        return 2

    findings = coverage_findings(roadmap, manifest)
    findings.extend(command_findings(root, manifest))
    if findings:
        print("=== Phase Driver Coverage FAIL ===")
        for f in findings:
            print(f"- {f}")
        return 2

    print("=== Phase Driver Coverage PASS ===")
    print(f"Roadmap phases covered: {len(roadmap_phases)}")

    mode_flags = int(args.run) + int(args.run_all) + int(args.run_completed)
    if mode_flags > 1:
        print("Use only one of --run, --run-all, --run-completed", file=sys.stderr)
        return 2

    if args.run:
        if not args.phase:
            print("--run requires --phase", file=sys.stderr)
            return 2
        return run_phase_drivers(root, args.phase, manifest)
    if args.run_all:
        if args.phase:
            print("--phase cannot be combined with --run-all", file=sys.stderr)
            return 2
        return run_phase_sequence(root, roadmap_phases, manifest)
    if args.run_completed:
        if args.phase:
            print("--phase cannot be combined with --run-completed", file=sys.stderr)
            return 2
        completed = [p.phase for p in roadmap if p.status == "complete"]
        return run_phase_sequence(root, completed, manifest)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
