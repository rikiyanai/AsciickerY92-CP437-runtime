#!/usr/bin/env python3
"""Inventory and package the complete local FL-4512 corpus for Prism.

This is deliberately broader than fl4512_extract_manifest.py.  The extraction
manifest is the future standalone-repository source profile; this packager also
includes paper builds, the full article corpus, figures, historical receipts,
FL-4512 scratch/capture trees, the private routing skill, and the separately
archived reference-corpus copy.

Generated build/object/cache trees remain excluded.  The output manifest hashes
the exact working-tree bytes placed in the archive and records whether each
repository file is tracked or untracked.  FL-4512 proof state is never changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = ROOT / (
    "docs/research/ascii/verification/fl4512/"
    "2026-09-18-fl4512-extraction-manifest.json"
)
DEFAULT_INVENTORY = ROOT / (
    "docs/research/ascii/verification/fl4512/"
    "2026-09-18-fl4512-prism-archive-inventory.json"
)
PRIVATE_SKILL_ROOT = Path(
    "/Users/r/.claude/skills/fl4512-rendering-npr-rendering-techniques"
)
REFERENCE_CORPUS_ROOT = Path(
    "/Users/r/Desktop/FL4512-reference-corpus-2026-08-18"
)
SCHEMA = "fl4512.prism_archive_inventory.v1"
GENERATOR_VERSION = "1.0.0"

COMPLETE_REPO_ROOTS = (
    "docs/agent/glyph-rendering-paper",
    "docs/research/ascii/articles",
    "docs/research/ascii/verification/fl4512",
)
NAME_RE = re.compile(
    r"fl[-_]?4512|4512|glyph-rendering-paper|asciiid-classic|"
    r"classic_asciiid|asciicker-presenter|presenter_app",
    re.IGNORECASE,
)
CONTENT_RE = (
    r"FL-4512|fl4512|glyph-rendering-paper|ASCIIID Classic|"
    r"asciiid-classic|asciicker-presenter"
)
PRUNED_COMPONENTS = frozenset(
    {".git", ".run", ".cache", "dist", "__pycache__", "node_modules"}
)


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=check
    )


def _tracked_files() -> set[str]:
    return set(_git("ls-files", "-z").stdout.rstrip("\0").split("\0"))


def _source_profile_files() -> set[str]:
    data = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    return set(data["files"])


def _content_hits() -> set[str]:
    # Restrict the content scan to text-bearing formats. Path/root rules own
    # PDFs, images, videos, archives, and other binary evidence; scanning those
    # bytes adds minutes without discovering meaningful content references.
    text_pathspecs = (
        "*.md", "*.txt", "*.json", "*.jsonl", "*.py", "*.cpp", "*.cc",
        "*.c", "*.h", "*.hpp", "*.gd", "*.yml", "*.yaml", "*.toml",
        "*.tex", "*.sh", "*.html", "*.js", "*.ts", "*.plist", "*.csv",
        "*.excalidraw",
    )
    result = _git(
        "grep", "-Il", "-E", CONTENT_RE, "--", *text_pathspecs, check=False
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or "git grep failed")
    return set(result.stdout.splitlines())


def _is_pruned(rel: Path) -> bool:
    for part in rel.parts:
        if part in PRUNED_COMPONENTS:
            return True
        if part.startswith((".o_", ".d_", ".venv")):
            return True
    return False


def _iter_tree_files(root: Path):
    if not root.exists():
        return
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name
            for name in dirs
            if not _is_pruned((current_path / name).relative_to(root))
        ]
        for name in files:
            path = current_path / name
            if not _is_pruned(path.relative_to(root)):
                yield path


def _category(rel: str, origin: str) -> str:
    if origin == "private_skill":
        return "routing_skill"
    if origin == "reference_corpus_copy":
        return "reference_corpus_external_copy"
    if rel.startswith("docs/agent/glyph-rendering-paper/"):
        return "paper"
    if rel.startswith("docs/research/ascii/articles/"):
        return "article_corpus"
    if rel.startswith("docs/research/ascii/verification/fl4512/"):
        return "verification"
    if rel.startswith("articles/"):
        return "figures_media"
    if rel.startswith("artifacts/"):
        return "historical_artifact"
    if rel.startswith((".scratch/", ".codex-render/")):
        return "local_preview_scratch"
    if rel.startswith("graft/"):
        return "graft_projection_non_authority"
    if rel.startswith("apps/asciiid-classic/"):
        return "asciiid_classic"
    if rel.startswith(("engine/", "platform/", "editor/")):
        return "runtime_source"
    if rel.startswith(("scripts/", "tests/", ".github/")):
        return "script_test_ci"
    if rel.startswith("assets/"):
        return "runtime_asset"
    return "related_repository_file"


def build_inventory(*, require_external: bool = True) -> dict:
    tracked = _tracked_files()
    selected: dict[str, dict] = {}
    reasons: dict[str, set[str]] = defaultdict(set)

    def add_repo(rel: str, reason: str) -> None:
        path = ROOT / rel
        if not (path.is_file() or path.is_symlink()) or _is_pruned(Path(rel)):
            return
        archive_path = f"FL4512_PRISM_EXPORT/repo/{rel}"
        selected.setdefault(
            archive_path,
            {
                "archive_path": archive_path,
                "source_path": rel,
                "origin": "repository",
                "git_state": "tracked" if rel in tracked else "untracked",
                "category": _category(rel, "repository"),
            },
        )
        reasons[archive_path].add(reason)

    for rel in sorted(_source_profile_files()):
        add_repo(rel, "standalone-source-profile")
    for rel in sorted(tracked):
        if NAME_RE.search(rel):
            add_repo(rel, "tracked-path-match")
    for rel in sorted(_content_hits()):
        add_repo(rel, "tracked-content-match")
    for root_rel in COMPLETE_REPO_ROOTS:
        root = ROOT / root_rel
        for path in _iter_tree_files(root):
            add_repo(path.relative_to(ROOT).as_posix(), "complete-corpus-root")
    for path in _iter_tree_files(ROOT):
        rel = path.relative_to(ROOT).as_posix()
        if NAME_RE.search(rel):
            add_repo(rel, "working-tree-path-match")

    external_roots = (
        ("private_skill", PRIVATE_SKILL_ROOT, "skill"),
        ("reference_corpus_copy", REFERENCE_CORPUS_ROOT, "reference-corpus"),
    )
    missing_external: list[str] = []
    for origin, root, archive_name in external_roots:
        if not root.is_dir():
            missing_external.append(str(root))
            continue
        for path in _iter_tree_files(root):
            rel = path.relative_to(root).as_posix()
            archive_path = f"FL4512_PRISM_EXPORT/external/{archive_name}/{rel}"
            selected[archive_path] = {
                "archive_path": archive_path,
                "source_path": rel,
                "origin": origin,
                "git_state": "external",
                "category": _category(rel, origin),
                "reasons": ["complete-external-root"],
            }
    if require_external and missing_external:
        raise FileNotFoundError(
            "required FL-4512 external roots missing: " + ", ".join(missing_external)
        )

    entries = []
    for archive_path in sorted(selected):
        entry = selected[archive_path]
        if "reasons" not in entry:
            entry["reasons"] = sorted(reasons[archive_path])
        entries.append(entry)

    categories = Counter(entry["category"] for entry in entries)
    origins = Counter(entry["origin"] for entry in entries)
    git_states = Counter(entry["git_state"] for entry in entries)
    return {
        "schema": SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_head": _git("rev-parse", "HEAD").stdout.strip(),
        "proof_status": "FL-4512 OPEN / Proof OPEN",
        "scope": (
            "all local FL-4512 source, runtime dependencies, scripts, tests, "
            "paper, articles, figures, receipts, historical media, tracked "
            "projection copies, routing skill, and external corpus copy"
        ),
        "exclusions": [
            ".git metadata",
            ".run build products",
            "dist packaging and web build products",
            ".o_* and .d_* compiler residue",
            "cache, __pycache__, node_modules, and virtual-environment trees",
        ],
        "missing_external_roots": missing_external,
        "counts": {
            "files": len(entries),
            "categories": dict(sorted(categories.items())),
            "origins": dict(sorted(origins.items())),
            "git_states": dict(sorted(git_states.items())),
        },
        "entries": entries,
    }


def _source_for(entry: dict) -> Path:
    origin = entry["origin"]
    if origin == "repository":
        return ROOT / entry["source_path"]
    if origin == "private_skill":
        return PRIVATE_SKILL_ROOT / entry["source_path"]
    if origin == "reference_corpus_copy":
        return REFERENCE_CORPUS_ROOT / entry["source_path"]
    raise ValueError(f"unknown origin: {origin}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        return digest.hexdigest()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_output(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("archive output must be an absolute path")
    resolved = path.resolve() if path.exists() else path.parent.resolve() / path.name
    if ROOT.resolve() == resolved or ROOT.resolve() in resolved.parents:
        raise ValueError("archive output must be outside the repository")
    if resolved in {Path("/"), Path.home(), Path.home() / "Downloads"}:
        raise ValueError(f"refusing broad output path: {resolved}")
    return resolved


def _copy_snapshot(src: Path, dst: Path, *, attempts: int = 3) -> None:
    """Copy one file without macOS copyfile(3), retrying transient timeouts."""
    last_error: TimeoutError | None = None
    for _attempt in range(attempts):
        try:
            with src.open("rb") as source, dst.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            shutil.copystat(src, dst, follow_symlinks=True)
            return
        except TimeoutError as error:
            last_error = error
            dst.unlink(missing_ok=True)
    assert last_error is not None
    raise last_error


def _materialize(staging: Path, inventory: dict) -> dict:
    total_bytes = 0
    copied = 0
    for entry in inventory["entries"]:
        src = _source_for(entry)
        dst = staging / entry["archive_path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
            size = len(os.readlink(src).encode("utf-8", errors="surrogateescape"))
        else:
            # Copy rather than hard-link: the archive is a frozen working-tree
            # snapshot even when another session edits the repository while
            # compression is running.
            _copy_snapshot(src, dst)
            size = dst.stat().st_size
            copied += 1
        entry["bytes"] = size
        entry["sha256"] = _sha256(dst)
        total_bytes += size
    inventory["counts"]["source_bytes"] = total_bytes
    inventory["materialization"] = {"copied": copied, "symlinks": len(inventory["entries"]) - copied}
    return inventory


def _write_metadata(staging: Path, inventory: dict) -> None:
    meta = staging / "FL4512_PRISM_EXPORT" / "_metadata"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "manifest.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (meta / "git-status.txt").write_text(_git("status", "--short").stdout, encoding="utf-8")
    history = _git(
        "log", "--all", "--date=iso-strict", "--format=%H %ad %s", "--grep=FL-4512"
    ).stdout
    (meta / "git-history-fl4512.txt").write_text(history, encoding="utf-8")
    (meta / "README.md").write_text(
        "# FL-4512 Prism export\n\n"
        "This archive is a current-working-tree evidence corpus, not proof of "
        "renderer acceptance. FL-4512 is OPEN / Proof OPEN.\n\n"
        "`repo/` preserves repository-relative paths. `external/skill/` is the "
        "private FL-4512 routing skill. `external/reference-corpus/` is the "
        "separate local corpus copy. Generated binaries, "
        "objects, dist bundles, caches, virtual environments, and node_modules are excluded.\n",
        encoding="utf-8",
    )


def _validate_archive(output: Path) -> None:
    # Archives are encoded with a 2 GiB long-distance window, so the decoder
    # must opt into the same maximum window during integrity checks.
    subprocess.run(["zstd", "-t", "--long=31", str(output)], check=True)


def create_archive(output: Path, inventory: dict, *, level: int, keep_staging: bool) -> dict:
    output = _safe_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"archive already exists: {output}")
    staging = Path(tempfile.mkdtemp(prefix=".fl4512-prism-stage-", dir=output.parent))
    try:
        inventory = _materialize(staging, inventory)
        _write_metadata(staging, inventory)
        tar = subprocess.Popen(
            ["tar", "-cf", "-", "-C", str(staging), "FL4512_PRISM_EXPORT"],
            stdout=subprocess.PIPE,
        )
        assert tar.stdout is not None
        zstd_args = ["zstd"]
        if level > 19:
            zstd_args.append("--ultra")
        zstd_args.extend([f"-{level}", "--long=31", "-T0", "-o", str(output)])
        zstd = subprocess.run(
            zstd_args,
            stdin=tar.stdout,
            check=False,
        )
        tar.stdout.close()
        tar_code = tar.wait()
        if tar_code != 0 or zstd.returncode != 0:
            raise RuntimeError(
                f"archive pipeline failed: tar={tar_code} zstd={zstd.returncode}"
            )
        _validate_archive(output)
        receipt = {
            "schema": "fl4512.prism_archive_receipt.v1",
            "archive": str(output),
            "archive_bytes": output.stat().st_size,
            "archive_sha256": _sha256(output),
            "source_files": inventory["counts"]["files"],
            "source_bytes": inventory["counts"]["source_bytes"],
            "compression": f"zstd -{level} --long=31 -T0",
            "source_head": inventory["source_head"],
            "proof_status": inventory["proof_status"],
        }
        receipt_path = output.with_suffix(output.suffix + ".receipt.json")
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return receipt
    finally:
        if keep_staging:
            print(f"staging retained: {staging}", file=sys.stderr)
        else:
            shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-output", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--compression-level", type=int, default=19, choices=range(1, 23))
    parser.add_argument("--allow-missing-external", action="store_true")
    parser.add_argument("--keep-staging", action="store_true")
    ns = parser.parse_args()

    inventory = build_inventory(require_external=not ns.allow_missing_external)
    ns.inventory_output.parent.mkdir(parents=True, exist_ok=True)
    ns.inventory_output.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"inventory: {inventory['counts']['files']} files -> {ns.inventory_output}"
    )
    if ns.inventory_only:
        return 0
    if ns.archive is None:
        parser.error("--archive is required unless --inventory-only is used")
    try:
        receipt = create_archive(
            ns.archive,
            inventory,
            level=ns.compression_level,
            keep_staging=ns.keep_staging,
        )
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"archive refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
