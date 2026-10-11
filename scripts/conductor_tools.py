#!/usr/bin/env python3
"""Conductor bootstrap helpers for Codex workflows.

Primary command used by docs/agent/agents.md:
  python3 scripts/conductor_tools.py status --auto-setup
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple


MANAGED_START = "<!-- codex-conductor:start -->"
MANAGED_END = "<!-- codex-conductor:end -->"
GUARDRAIL_BLOCK = """## Conductor Guardrail
Always run `conductor:status` first.

- Command alias: `conductor_status`
- Direct command: `python3 scripts/conductor_tools.py status --auto-setup`
- Behavior: if Conductor is missing, status runs setup and creates the baseline.
"""

_CONDUCTOR_TEMPLATES: Dict[str, str] = {
    ".conductor/README.md": """# Conductor Workspace
This folder stores structured project context for Codex.
Workflow:
1. Run `conductor:status` (or `conductor_status`) at the start of every session.
2. Define/refresh context in `context/PROJECT_CONTEXT.md`.
3. Write specs in `specs/` before code changes.
4. Write implementation plans in `plans/`.
5. Implement and verify.
""",
    ".conductor/context/PROJECT_CONTEXT.md": """# Project Context
## Project
- Name:
- Goal:
- Users:
## Constraints
- Technical:
- Product:
- Operational:
## Stack
- Languages:
- Frameworks:
- Runtime/infra:
## Definition Of Done
""",
    ".conductor/specs/README.md": """# Specs
Create one spec file per change before implementation.
Suggested naming:
- `YYYY-MM-DD-<feature>-spec.md`
""",
    ".conductor/plans/README.md": """# Plans
Create one implementation plan per approved spec.
Suggested naming:
- `YYYY-MM-DD-<feature>-plan.md`
""",
}

_REQUIRED_PATHS: List[str] = [
    ".conductor/README.md",
    ".conductor/context/PROJECT_CONTEXT.md",
    ".conductor/specs/README.md",
    ".conductor/plans/README.md",
]


def _write_text(path: Path, content: str, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _contains_guardrail(text: str) -> bool:
    if MANAGED_START in text and MANAGED_END in text:
        return True
    return "python3 scripts/conductor_tools.py status --auto-setup" in text


def _pick_agents_file(repo_root: Path, explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        return p

    nested = (repo_root / "docs" / "agent" / "agents.md").resolve()
    legacy_upper = (repo_root / "docs" / "AGENTS.md").resolve()
    legacy_lower = (repo_root / "docs" / "agents.md").resolve()
    if nested.exists():
        return nested
    if legacy_upper.exists():
        return legacy_upper
    if legacy_lower.exists():
        return legacy_lower
    return nested


def ensure_agents_guardrail(
    repo_root: Path,
    explicit: str | None = None,
    force: bool = False,
) -> Tuple[str, Path]:
    target = _pick_agents_file(repo_root, explicit)
    created = False

    if target.exists():
        current = _load_text(target)
    else:
        created = True
        current = "# Agent Entry Point\nFollow this repository rule before doing any task.\n"

    managed_block = f"{MANAGED_START}\n{GUARDRAIL_BLOCK}{MANAGED_END}\n"

    if MANAGED_START in current and MANAGED_END in current:
        before, tail = current.split(MANAGED_START, 1)
        _, after = tail.split(MANAGED_END, 1)
        merged = before.rstrip() + "\n\n" + managed_block + after.lstrip()
    elif _contains_guardrail(current) and not force:
        return ("unchanged", target)
    else:
        sep = "\n\n" if current.strip() else ""
        merged = current.rstrip() + sep + managed_block

    if target.exists() and merged == current and not force:
        return ("unchanged", target)

    _write_text(target, merged, force=True)
    return ("created" if created else "updated", target)


def setup_conductor(
    repo_root: Path,
    force: bool,
    ensure_agents: bool,
    agents_file: str | None,
    force_agents: bool,
) -> Tuple[List[Path], Tuple[str, Path] | None]:
    created: List[Path] = []

    for rel_path, content in _CONDUCTOR_TEMPLATES.items():
        path = repo_root / rel_path
        if _write_text(path, content, force=force):
            created.append(path)

    # Ensure directory skeleton exists even if files are force-disabled.
    for rel_path in (".conductor/context", ".conductor/specs", ".conductor/plans"):
        (repo_root / rel_path).mkdir(parents=True, exist_ok=True)

    agents_result: Tuple[str, Path] | None = None
    if ensure_agents:
        agents_result = ensure_agents_guardrail(
            repo_root=repo_root,
            explicit=agents_file,
            force=force_agents,
        )

    return created, agents_result


def collect_status(repo_root: Path) -> Tuple[List[Path], bool]:
    missing: List[Path] = []
    for rel_path in _REQUIRED_PATHS:
        p = repo_root / rel_path
        if not p.exists():
            missing.append(p)

    guardrail_ok = False
    candidates = [
        repo_root / "docs" / "agent" / "agents.md",
        repo_root / "docs" / "AGENTS.md",
        repo_root / "docs" / "agents.md",
    ]
    for candidate in candidates:
        if candidate.exists() and _contains_guardrail(_load_text(candidate)):
            guardrail_ok = True
            break

    return missing, guardrail_ok


def _print_status(repo_root: Path, missing: List[Path], guardrail_ok: bool) -> None:
    print(f"Repo: {repo_root}")
    if missing:
        print("Conductor files: missing")
        for path in missing:
            try:
                rel = path.relative_to(repo_root)
            except ValueError:
                rel = path
            print(f"  - {rel}")
    else:
        print("Conductor files: ready")
    print(f"Guardrail present: {'yes' if guardrail_ok else 'no'}")


def cmd_setup(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    created, agents_result = setup_conductor(
        repo_root=repo_root,
        force=bool(args.force),
        ensure_agents=bool(args.ensure_agents),
        agents_file=args.agents_file,
        force_agents=bool(args.force_agents),
    )

    print(f"Setup complete for {repo_root}")
    if created:
        print("Created:")
        for item in created:
            print(f"  - {item.relative_to(repo_root)}")
    else:
        print("No new files were created.")

    if agents_result is not None:
        action, path = agents_result
        print(f"Guardrail {action}: {path.relative_to(repo_root)}")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    missing, guardrail_ok = collect_status(repo_root)
    _print_status(repo_root, missing, guardrail_ok)

    if not missing and guardrail_ok:
        print("Status: READY")
        return 0

    if args.auto_setup:
        print("Status: not ready, running setup...")
        setup_conductor(
            repo_root=repo_root,
            force=False,
            ensure_agents=True,
            agents_file=args.agents_file,
            force_agents=False,
        )
        missing, guardrail_ok = collect_status(repo_root)
        if not missing and guardrail_ok:
            print("Status: READY (after setup)")
            return 0
        print("Status: STILL NOT READY")
        return 1

    print("Status: NOT READY")
    return 1


def cmd_codex_init(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    setup_conductor(
        repo_root=repo_root,
        force=False,
        ensure_agents=True,
        agents_file=args.agents_file,
        force_agents=False,
    )
    if args.no_status:
        return 0

    status_args = argparse.Namespace(
        repo_root=str(repo_root),
        auto_setup=True,
        agents_file=args.agents_file,
    )
    return cmd_status(status_args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Conductor bootstrap and update helpers for Codex.")
    sub = p.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Create .conductor baseline files.")
    setup.add_argument("--repo-root", default=".")
    setup.add_argument("--force", action="store_true")
    setup.add_argument("--ensure-agents", action="store_true")
    setup.add_argument("--agents-file")
    setup.add_argument("--force-agents", action="store_true")
    setup.set_defaults(func=cmd_setup)

    status = sub.add_parser("status", help="Check Conductor status and auto-setup if needed.")
    status.add_argument("--repo-root", default=".")
    status.add_argument("--auto-setup", action="store_true", default=False)
    status.add_argument("--no-auto-setup", dest="auto_setup", action="store_false")
    status.add_argument("--agents-file")
    status.set_defaults(func=cmd_status)

    codex_init = sub.add_parser("codex-init", help="Install Conductor guardrail in agents file.")
    codex_init.add_argument("--repo-root", default=".")
    codex_init.add_argument("--agents-file")
    codex_init.add_argument("--no-status", action="store_true")
    codex_init.set_defaults(func=cmd_codex_init)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
