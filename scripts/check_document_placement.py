#!/usr/bin/env python3
"""Check tracked document placement against canonical and exact named owners.

Upstream source documentation and licenses stay beside their source. They
require explicit ownership; no imported directory receives a blanket exemption.
"""
import json
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def violations(paths, owners):
    """Return misplaced documents, forbidden instruction files and stale owners."""
    paths, owners = set(paths), set(owners)
    invalid = sorted(owner for owner in owners
                     if owner not in paths or ".." in PurePosixPath(owner).parts
                     or PurePosixPath(owner).is_absolute()
                     or any(char in owner for char in "*?[]"))
    bad = ["invalid exact document owner: " + owner for owner in invalid]
    for path in sorted(paths):
        name = PurePosixPath(path).name
        if name in ("AGENTS.md", "CLAUDE.md", "CONTEXT.md"):
            bad.append("instruction file: " + path)
        elif (path.endswith(".md") or name.startswith("LICENSE")):
            if path != "README.md" and not path.startswith("docs/") and path not in owners:
                bad.append("unowned document: " + path)
    return bad


def main():
    paths = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-z"]).decode().split("\0")
    owners = json.loads((ROOT / ".config/repo-profile.json").read_text())["document_owners"]
    bad = violations(filter(None, paths), owners)
    if bad:
        raise SystemExit("\n".join(bad))
    print(f"Document placement OK: {len(owners)} exact source/license owners")


if __name__ == "__main__":
    main()
