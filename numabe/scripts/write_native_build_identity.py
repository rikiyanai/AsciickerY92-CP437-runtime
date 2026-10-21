#!/usr/bin/env python3
"""Write the launcher identity sidecar for a completed native game build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Single-authority product identity map (FL-4512 G4): this writer is the only
# producer of ``*.build_identity.json`` under ``.run/``. Each allowed binary
# maps to exactly one renderer identity and one build route; anything else is
# rejected before any sidecar bytes are written.
BINARY_RENDERER = {
    ".run/game": "fl4512_unicode",
    ".run/game_classic": "classic_cp437",
}
BINARY_TARGETS = {
    ".run/game": frozenset({"game-fl4512"}),
    ".run/game_classic": frozenset({"game-classic"}),
}

# ``game`` is a Makefile alias, not a writer route: no production callsite
# passes ``--make-target game`` (the Makefile invokes the writer only with
# ``game-fl4512`` and ``game-classic``), so the writer admits no alias.


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def resolve_identity(
    binary_arg: str,
    renderer: str,
    make_target: str,
) -> tuple[str, str, str]:
    """Validate one writer tuple; return its normalized form.

    Raises ValueError on unknown binaries, contradictory binary/renderer
    pairs, and unclaimed routes. Pure: no filesystem writes.
    """
    rel = Path(str(binary_arg).strip()).as_posix()
    while rel.startswith("./"):
        rel = rel[2:]
    expected = BINARY_RENDERER.get(rel)
    if expected is None:
        raise ValueError(
            f"unknown binary {binary_arg!r}: writer only owns "
            f"{sorted(BINARY_RENDERER)}"
        )
    if renderer != expected:
        raise ValueError(
            f"contradictory tuple: {rel} is {expected}, not {renderer}"
        )
    allowed = BINARY_TARGETS[rel]
    if make_target not in allowed:
        raise ValueError(
            f"contradictory tuple: {rel} is claimed only by "
            f"{sorted(allowed)}, not {make_target!r}"
        )
    return rel, expected, make_target


def write_identity(
    binary: Path, renderer: str, make_target: str, source_commit: str
) -> Path:
    """Hash the binary and atomically write its identity sidecar."""
    binary_hash = sha256_file(binary)
    build_material = "\n".join(
        (source_commit, binary_hash, renderer, make_target)
    ).encode("utf-8")
    payload = {
        "schema": "launcher.native_build_identity.v1",
        "source_commit": source_commit,
        "binary_path": str(binary.relative_to(ROOT)),
        "binary_sha256": binary_hash,
        "build_hash": hashlib.sha256(build_material).hexdigest(),
        "renderer": renderer,
        "make_target": make_target,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    output = binary.with_name(binary.name + ".build_identity.json")
    temp = output.with_name(output.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, output)
    print(f"Native build identity: {output.relative_to(ROOT)}")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--renderer", required=True, choices=("classic_cp437", "fl4512_unicode"))
    parser.add_argument("--make-target", required=True)
    args = parser.parse_args(argv)

    candidate = (ROOT / args.binary).resolve()
    try:
        repo_rel = candidate.relative_to(ROOT).as_posix()
    except ValueError:
        parser.error(f"binary is outside the repo: {args.binary}")
    try:
        binary_rel, renderer, make_target = resolve_identity(
            repo_rel, args.renderer, args.make_target
        )
    except ValueError as exc:
        parser.error(str(exc))

    binary = (ROOT / binary_rel).resolve()
    if not binary.is_file():
        parser.error(f"binary does not exist: {binary}")
    write_identity(binary, renderer, make_target, git_head())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
