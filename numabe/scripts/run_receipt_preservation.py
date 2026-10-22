#!/usr/bin/env python3
"""Preserve structured run evidence outside disposable ``.run`` storage.

The runtime directory remains useful for bulky, replaceable capture output.  A
completed or failed run receipt does not: it is the durable record of what was
measured.  This module copies that receipt and its structured companions into a
content-addressed, repository-owned bundle.  Existing bytes are never replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path


SCHEMA = "asciicker.run-receipt-preservation.v1"
DEFAULT_COMPANION_NAMES = (
    "analysis_summary.json",
    "artifact_manifest.json",
    "godot_proof_publication.json",
    "runtime_source_identity.json",
    "window_activation.json",
    "analysis.txt",
    "godot_client.log",
    "colyseus_server.log",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return cleaned or "run"


def _repo_relative(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _sha256(destination) != _sha256(source):
            raise RuntimeError(f"refusing to replace preserved evidence: {destination}")
        return
    temporary = destination.with_name(destination.name + ".partial")
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, value: object) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise RuntimeError(f"refusing to replace preserved manifest: {path}")
        return
    temporary = path.with_name(path.name + ".partial")
    try:
        with temporary.open("xb") as target:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def preserve_run_receipt(
    *,
    repo_root: Path,
    receipt_path: Path,
    fl_id: str,
    runner: str,
    destination_root: Path | None = None,
    companion_names: tuple[str, ...] = DEFAULT_COMPANION_NAMES,
) -> Path:
    """Copy one run's receipt bundle into immutable repository-owned storage."""
    repo_root = repo_root.resolve()
    receipt_path = receipt_path.resolve()
    if not receipt_path.is_file():
        raise FileNotFoundError(f"run receipt does not exist: {receipt_path}")

    receipt_sha256 = _sha256(receipt_path)
    root = destination_root or repo_root / "artifacts" / "run_receipts"
    bundle_name = f"{_safe_component(receipt_path.parent.name)}__{receipt_sha256[:16]}"
    bundle_dir = root.resolve() / _safe_component(fl_id.lower()) / bundle_name

    sources = [receipt_path]
    sources.extend(
        receipt_path.parent / name
        for name in companion_names
        if (receipt_path.parent / name).is_file()
    )
    file_rows: list[dict[str, object]] = []
    for source in sources:
        destination = bundle_dir / source.name
        _atomic_copy(source, destination)
        file_rows.append(
            {
                "name": source.name,
                "bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
                "source": _repo_relative(repo_root, source),
            }
        )

    manifest = {
        "schema": SCHEMA,
        "fl_id": fl_id.upper(),
        "runner": runner,
        "source_run_directory": _repo_relative(repo_root, receipt_path.parent),
        "receipt_sha256": receipt_sha256,
        "files": file_rows,
    }
    manifest_path = bundle_dir / "preservation_manifest.json"
    _atomic_write_json(manifest_path, manifest)
    return manifest_path


FILE_SET_SCHEMA = "asciicker.run-file-set-preservation.v1"


def preserve_file_set(
    *,
    repo_root: Path,
    fl_id: str,
    runner: str,
    label: str,
    files: dict[str, Path],
    referenced: list[dict[str, object]] | None = None,
    metadata: dict[str, object] | None = None,
    destination_root: Path | None = None,
    source_labels: dict[str, str] | None = None,
) -> Path:
    """Copy a named set of files into immutable, content-addressed storage.

    source_labels names the origin of generated files (their staging path is
    meaningless after the copy).

    ``files`` maps the preserved relative name to its source path. The bundle
    directory is ``<root>/<fl>/<label>__<hash16>`` where hash16 is the sha256
    of the canonical listing (names, sizes, sha256 of every copied file, every
    ``referenced`` row and ``metadata``), so the same content always lands in
    the same place and different content never overwrites it. ``referenced``
    rows describe files that are deliberately NOT copied (for example bulky
    frame PNGs); each row must carry its sha256 and storage location so the
    bytes can be verified later. Returns the preservation manifest path.
    """
    repo_root = repo_root.resolve()
    rows: list[dict[str, object]] = []
    for name in sorted(files):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError(f"unsafe preserved name: {name}")
        source = files[name]
        if not source.is_file():
            raise FileNotFoundError(f"file to preserve does not exist: {source}")
        rows.append(
            {
                "name": relative.as_posix(),
                "bytes": source.stat().st_size,
                "sha256": _sha256(source),
                "source": (source_labels or {}).get(name) or _repo_relative(repo_root, source),
            }
        )
    listing = {
        "files": [{k: row[k] for k in ("name", "bytes", "sha256")} for row in rows],
        "referenced": referenced or [],
        "metadata": metadata or {},
    }
    content_sha256 = hashlib.sha256(
        json.dumps(listing, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    root = destination_root or repo_root / "artifacts" / "run_receipts"
    bundle_dir = (
        root.resolve()
        / _safe_component(fl_id.lower())
        / f"{_safe_component(label)}__{content_sha256[:16]}"
    )
    for row in rows:
        destination = bundle_dir / str(row["name"])
        _atomic_copy(files[str(row["name"])], destination)
        if _sha256(destination) != row["sha256"]:
            raise RuntimeError(f"preserved copy changed while copying: {destination}")
    manifest = {
        "schema": FILE_SET_SCHEMA,
        "fl_id": fl_id.upper(),
        "runner": runner,
        "label": label,
        "content_sha256": content_sha256,
        "files": rows,
        "referenced_not_copied": referenced or [],
        "metadata": metadata or {},
    }
    manifest_path = bundle_dir / "preservation_manifest.json"
    if manifest_path.exists():
        # The same content preserved earlier by another consumer (runner,
        # staging path) is the same evidence: keep the first manifest. Its
        # copied bytes were re-verified above; a different content hash under
        # this name is refused.
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"refusing to replace unreadable preserved manifest: {manifest_path}") from exc
        if existing.get("content_sha256") != content_sha256:
            raise RuntimeError(f"refusing to replace preserved manifest: {manifest_path}")
        return manifest_path
    _atomic_write_json(manifest_path, manifest)
    return manifest_path


class DeferredRunReceiptPreserver:
    """Preserve a configured receipt exactly once when a runner exits."""

    def __init__(self, *, repo_root: Path, fl_id: str, runner: str) -> None:
        self.repo_root = repo_root
        self.fl_id = fl_id
        self.runner = runner
        self.receipt_path: Path | None = None
        self.manifest_path: Path | None = None
        self.companion_names: tuple[str, ...] = DEFAULT_COMPANION_NAMES
        self._finalizers: list = []
        self._finalized = False

    def add_finalizer(self, finalizer) -> None:
        """Run ``finalizer(self)`` once, immediately before the first preserve().

        A finalizer records end-of-run facts (for example the end source
        snapshot) into the receipt and may add companions or reconfigure the
        receipt path. It must record its own failures instead of raising; an
        exception is re-raised only after the receipt is preserved.
        """
        if self._finalized:
            raise RuntimeError("finalizers already ran; add them before preserve()")
        self._finalizers.append(finalizer)

    def _run_finalizers(self) -> BaseException | None:
        if self._finalized:
            return None
        self._finalized = True
        error: BaseException | None = None
        for finalizer in self._finalizers:
            try:
                finalizer(self)
            except Exception as exc:  # preserve the receipt first, then report
                error = error or exc
        return error

    def configure(self, receipt_path: Path) -> None:
        self.receipt_path = receipt_path

    def add_companions(self, names: tuple[str, ...]) -> None:
        """Extra structured results beside the receipt to preserve with it."""
        if self.manifest_path is not None:
            raise RuntimeError("receipt already preserved; companions must be added before preserve()")
        self.companion_names = self.companion_names + tuple(n for n in names if n not in self.companion_names)

    def preserve(self) -> Path | None:
        if self.manifest_path is not None:
            return self.manifest_path
        finalizer_error = self._run_finalizers()
        if self.receipt_path is None or not self.receipt_path.is_file():
            if finalizer_error is not None:
                raise finalizer_error
            return None
        self.manifest_path = preserve_run_receipt(
            repo_root=self.repo_root,
            receipt_path=self.receipt_path,
            fl_id=self.fl_id,
            runner=self.runner,
            companion_names=self.companion_names,
        )
        if finalizer_error is not None:
            raise finalizer_error
        return self.manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preserve a disposable run receipt in repository-owned storage."
    )
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--fl-id", required=True)
    parser.add_argument("--runner", required=True)
    args = parser.parse_args()
    try:
        manifest = preserve_run_receipt(
            repo_root=args.repo_root,
            receipt_path=args.receipt,
            fl_id=args.fl_id,
            runner=args.runner,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[run-receipt-preservation] ERROR: {error}", file=sys.stderr)
        return 1
    print(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
