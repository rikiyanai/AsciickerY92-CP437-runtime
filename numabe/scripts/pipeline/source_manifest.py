"""source_manifest.py -- Generic source / licence manifest owner.

PURPOSE
  One owner for the question "where did this input come from, under what
  licence, and is the byte stream on disk still the byte stream we acquired?"
  Every acquisition lane (OSM extracts, street-level imagery, DEM/LiDAR
  tiles, hand annotations) writes its provenance through this module so the
  answer has a single shape.

  This module is deliberately LOCALE-INDEPENDENT and CORRIDOR-INDEPENDENT.
  No place name, bounding box, provider endpoint or project-specific default
  belongs in this file -- those live in the manifest DATA that callers write.
  If you find yourself adding an `if provider == ...` branch here, the branch
  belongs in the caller.

RECORD SCHEMA (SourceRecord)
  record_id       stable slug, unique within one manifest
  provider        organisation / service that published the source
  kind            one of KINDS (osm, imagery, lidar, dem, annotation, other)
  source_id       the provider's own identifier (element id, picture uuid, tile key)
  source_uri      the exact URI the bytes or metadata came from
  capture_date    when the world was observed (ISO 8601), not when we fetched
  acquired_utc    when WE fetched it (ISO 8601 Z)
  licence         SPDX id where one exists, else the provider's licence token
  licence_uri     canonical licence text
  attribution     the credit line a downstream renderer must display
  retention       one of RETENTIONS; metadata_only means no bytes were kept
  artifact_path   path of retained bytes, relative to the manifest's root
  sha256          digest of the retained bytes (required iff retention=retained)
  size_bytes      length of the retained bytes
  crs             coordinate reference system of the payload (e.g. EPSG:4326)
  tool_version    identifier of the code that performed the acquisition
  parameters      dict of the request that produced the artifact (query, bbox, ...)
  seed            RNG seed where acquisition sampled; None when deterministic
  notes           free text; never load-bearing

DETERMINISM
  write_manifest() sorts records by (kind, provider, record_id) and serialises
  with sorted keys and a trailing newline, so the same inputs produce the same
  bytes. Two runs that differ only in dict insertion order produce identical
  files.

CLI
    python -m scripts.pipeline.source_manifest --write SPEC.json --out MANIFEST.json
    python -m scripts.pipeline.source_manifest --verify MANIFEST.json [--root DIR]

  --write reads a spec JSON ({"manifest_id":..., "context":{...},
  "records":[{...}]}) and emits the normalised, deterministic manifest.
  --verify re-hashes every retained artifact and exits non-zero on any
  MISSING or MISMATCH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = "source-manifest/1"

KINDS = ("osm", "imagery", "lidar", "dem", "annotation", "other",
         "street_imagery_frame", "plateau_citygml_bldg", "plateau_citygml_tran",
         "plateau_citygml_dem", "plateau_dataset_metadata")

RETENTIONS = (
    "retained",       # bytes are stored at artifact_path and hashed
    "metadata_only",  # licence forbids or lane does not need byte retention
    "not_retained",   # bytes exist upstream but were deliberately not kept
)

# Licences that permit retaining and redistributing the acquired bytes given
# attribution. Membership here is a licence fact, not a project preference, so
# callers can gate downloads on it without re-deciding the policy per lane.
OPEN_LICENCES = frozenset({
    "CC0-1.0",
    "CC-BY-4.0",
    "CC-BY-3.0",
    "CC-BY-SA-4.0",
    "CC-BY-SA-3.0",
    "ODbL-1.0",
    "ODC-BY-1.0",
    "PDDL-1.0",
    "etalab-2.0",
    "JP-GSI-TERMS",  # Japan GSI standard terms: CC-BY-4.0 compatible, attribution required
})

CHUNK = 1 << 20


def sha256_file(path: Path | str) -> str:
    """Digest a file without reading it whole into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def is_open_licence(licence: Optional[str]) -> bool:
    """True when `licence` explicitly permits retention + redistribution.

    Unset, empty, 'proprietary' and anything unrecognised are FALSE. The
    default must be 'do not retain', because an unknown licence token is not
    evidence of permission.
    """
    if not licence:
        return False
    return licence.strip() in OPEN_LICENCES


@dataclass
class SourceRecord:
    record_id: str
    provider: str
    kind: str
    source_uri: str
    licence: str
    source_id: Optional[str] = None
    capture_date: Optional[str] = None
    acquired_utc: Optional[str] = None
    licence_uri: Optional[str] = None
    attribution: Optional[str] = None
    retention: str = "metadata_only"
    artifact_path: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    crs: Optional[str] = None
    tool_version: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    seed: Optional[int] = None
    notes: Optional[str] = None
    # Historical storage metadata is provenance, never an instruction to Git.
    # Raw survey bytes stay retained and continue to require their own digest.
    git_transport: Optional[Dict[str, str]] = None
    # Retained historical acquisition vocabulary. These are descriptive data,
    # not permission to download, publish, skip checks, or change a licence.
    bytes: Optional[int] = None
    feature_ids: List[str] = field(default_factory=list)
    page_uri: Optional[str] = None
    licence_category: Optional[int] = None
    licence_name: Optional[str] = None
    terms_reference: Optional[str] = None

    def validate(self) -> List[str]:
        problems: List[str] = []
        if not self.record_id:
            problems.append("record_id is empty")
        if self.kind not in KINDS:
            problems.append(f"{self.record_id}: kind {self.kind!r} not in {KINDS}")
        if self.retention not in RETENTIONS:
            problems.append(f"{self.record_id}: retention {self.retention!r} not in {RETENTIONS}")
        if self.retention == "retained":
            if not self.artifact_path:
                problems.append(f"{self.record_id}: retention=retained requires artifact_path")
            if not self.sha256:
                problems.append(f"{self.record_id}: retention=retained requires sha256")
        elif self.retention == "metadata_only" and self.sha256:
            # metadata_only means no byte stream was ever taken, so a digest
            # here is a contradiction. not_retained is different: the bytes
            # were fetched and hashed, then deliberately left out of the
            # bundle, and recording the digest is legitimate provenance.
            problems.append(f"{self.record_id}: sha256 set but retention=metadata_only")
        if not self.licence:
            problems.append(f"{self.record_id}: licence is empty")
        if self.git_transport is not None:
            if (not isinstance(self.git_transport, dict)
                    or set(self.git_transport) - {"encoding", "note"}
                    or self.git_transport.get("encoding") != "none"
                    or not isinstance(self.git_transport.get("note"), str)):
                problems.append(f"{self.record_id}: git_transport must be descriptive encoding=none/note metadata")
        if (not isinstance(self.feature_ids, list)
                or any(not isinstance(value, str) for value in self.feature_ids)):
            problems.append(f"{self.record_id}: feature_ids must be a list of strings")
        for name in ("size_bytes", "bytes"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                problems.append(f"{self.record_id}: {name} must be a non-negative integer")
        if self.bytes is not None and self.size_bytes is not None and self.bytes != self.size_bytes:
            problems.append(f"{self.record_id}: conflicting bytes/size_bytes")
        return problems

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceRecord":
        data = dict(data)  # never mutate the caller's provenance document
        known = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown SourceRecord field(s): {unknown}")
        if "bytes" in data:
            if data.get("size_bytes", data["bytes"]) != data["bytes"]:
                raise ValueError("conflicting bytes/size_bytes in source record")
            data.setdefault("size_bytes", data["bytes"])
        # Older retained-frame acquisitions predate the explicit retention
        # field. Recognise only this known shape; never silently turn an
        # arbitrary hash-bearing metadata row into retained evidence or SKIP.
        if "retention" not in data and data.get("kind") == "street_imagery_frame":
            if not data.get("source_uri") and data.get("page_uri"):
                # This is an item reference, NOT the missing exact byte URL.
                # Preserve that distinction instead of fabricating an API URL.
                data["source_uri"] = data["page_uri"]
                data["parameters"] = dict(data.get("parameters") or {})
                data["parameters"]["source_uri_basis"] = "legacy provider item page; exact byte-download URL not retained"
            if not all(data.get(key) for key in ("artifact_path", "sha256", "source_uri")):
                raise ValueError("legacy street_imagery_frame lacks retained-byte evidence")
            data["retention"] = "retained"
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SourceManifest:
    manifest_id: str
    records: List[SourceRecord] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def add(self, record: SourceRecord) -> None:
        if any(r.record_id == record.record_id for r in self.records):
            raise ValueError(f"duplicate record_id: {record.record_id}")
        self.records.append(record)

    def sorted_records(self) -> List[SourceRecord]:
        return sorted(self.records, key=lambda r: (r.kind, r.provider, r.record_id))

    def validate(self) -> List[str]:
        problems: List[str] = []
        seen = set()
        for record in self.records:
            if record.record_id in seen:
                problems.append(f"duplicate record_id: {record.record_id}")
            seen.add(record.record_id)
            problems.extend(record.validate())
        return problems

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "context": self.context,
            "record_count": len(self.records),
            "records": [r.to_dict() for r in self.sorted_records()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceManifest":
        return cls(
            manifest_id=data["manifest_id"],
            context=data.get("context", {}),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            records=[SourceRecord.from_dict(r) for r in data.get("records", [])],
        )


def serialize(manifest: SourceManifest) -> str:
    """Deterministic text form: stable record order, sorted keys, trailing \\n."""
    return json.dumps(
        manifest.to_dict(), indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n"


def write_manifest(manifest: SourceManifest, out_path: Path | str, *, strict: bool = True) -> Path:
    problems = manifest.validate()
    if problems and strict:
        raise ValueError("manifest is invalid:\n  " + "\n  ".join(problems))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(serialize(manifest), encoding="utf-8")
    tmp.replace(out_path)
    return out_path


def load_manifest(path: Path | str) -> SourceManifest:
    with open(path, "r", encoding="utf-8") as fh:
        return SourceManifest.from_dict(json.load(fh))


def verify(manifest: SourceManifest, root: Path | str) -> List[Dict[str, Any]]:
    """Re-hash every retained artifact. Returns one result row per record."""
    root = Path(root)
    results: List[Dict[str, Any]] = []
    for record in manifest.sorted_records():
        if record.retention != "retained":
            results.append({"record_id": record.record_id, "status": "SKIP",
                            "detail": f"retention={record.retention}"})
            continue
        target = root / str(record.artifact_path)
        if not target.is_file():
            results.append({"record_id": record.record_id, "status": "MISSING",
                            "detail": str(target)})
            continue
        digest = sha256_file(target)
        size = target.stat().st_size
        if digest != record.sha256:
            results.append({"record_id": record.record_id, "status": "MISMATCH",
                            "detail": f"expected {record.sha256} got {digest}"})
        elif record.size_bytes is not None and size != record.size_bytes:
            results.append({"record_id": record.record_id, "status": "MISMATCH",
                            "detail": f"size expected {record.size_bytes} got {size}"})
        else:
            results.append({"record_id": record.record_id, "status": "OK",
                            "detail": f"{size} bytes"})
    return results


def describe_record(path: Path | str, root: Path | str) -> Dict[str, Any]:
    """Digest + size fields for a retained artifact, ready to merge into a record."""
    root = Path(root)
    target = Path(path)
    absolute = target if target.is_absolute() else root / target
    return {
        "artifact_path": str(absolute.relative_to(root)),
        "sha256": sha256_file(absolute),
        "size_bytes": absolute.stat().st_size,
        "retention": "retained",
    }


def _cmd_write(args: argparse.Namespace) -> int:
    with open(args.write, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    manifest = SourceManifest.from_dict(spec)
    root = Path(args.root) if args.root else Path(args.out).parent
    for record in manifest.records:
        if record.retention == "retained" and record.artifact_path and not record.sha256:
            record.__dict__.update(describe_record(record.artifact_path, root))
    out = write_manifest(manifest, args.out, strict=not args.allow_invalid)
    print(f"wrote {out} ({len(manifest.records)} record(s))")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.verify)
    root = Path(args.root) if args.root else Path(args.verify).parent
    problems = manifest.validate()
    for problem in problems:
        print(f"SCHEMA {problem}")
    rows = verify(manifest, root)
    bad = 0
    for row in rows:
        print(f"{row['status']:9s} {row['record_id']}  {row['detail']}")
        if row["status"] in ("MISSING", "MISMATCH"):
            bad += 1
    print(f"-- {len(rows)} record(s): {bad} failing, {len(problems)} schema problem(s)")
    return 1 if (bad or problems) else 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="source_manifest",
        description="Write or verify a generic source/licence provenance manifest.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", metavar="SPEC.json",
                       help="normalise a manifest spec and emit it deterministically")
    group.add_argument("--verify", metavar="MANIFEST.json",
                       help="re-hash retained artifacts named by the manifest")
    parser.add_argument("--out", metavar="MANIFEST.json", help="output path for --write")
    parser.add_argument("--root", metavar="DIR",
                        help="root that artifact_path values resolve against "
                             "(default: the manifest's own directory)")
    parser.add_argument("--allow-invalid", action="store_true",
                        help="--write: emit even when schema validation fails")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.write:
        if not args.out:
            parser.error("--write requires --out")
        return _cmd_write(args)
    return _cmd_verify(args)


if __name__ == "__main__":
    sys.exit(main())
