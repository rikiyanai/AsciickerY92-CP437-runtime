#!/usr/bin/env python3
"""Command-line front door for the Y92 World IR (FL-4811).

    python3 -m scripts.pipeline.world_ir validate <doc.json> [--json]
    python3 -m scripts.pipeline.world_ir canonicalize <doc.json> [--out PATH] [--check]
    python3 -m scripts.pipeline.world_ir stats <doc.json>

WHY A CLI AND NOT JUST AN IMPORT
================================
FL-4815 requires a deterministic rebuild whose World IR can be validated as a
build step, and FL-4816 requires a receipt that names what was accepted. Both
need an owner that a Makefile or a run script can call and whose exit status
means something, so validation must not be reachable only from inside pytest.

Exit status is the contract:

    0   the document validated (or canonicalize wrote/confirmed byte-identity)
    1   the document has findings (or --check found non-canonical bytes)
    2   the document could not be read or parsed at all

``validate`` deliberately reports EVERY finding rather than the first, because
a partially-repaired evidence document is the failure mode this gate exists to
catch.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):  # direct `python3 .../world_ir/__main__.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    __package__ = "scripts.pipeline.world_ir"

from . import schema as S  # noqa: E402
from .store import WorldIRStore  # noqa: E402
from .validate import validate_document  # noqa: E402


def _read(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"[world-ir] cannot read {path}: {exc}") from exc
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[world-ir] {path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise SystemExit(f"[world-ir] {path} top level must be an object, got {type(doc).__name__}")
    return doc


def _counts(doc: dict) -> dict:
    """Counts read from the CANONICAL key names, not the store's accessors.

    The document keys are ``coordinate_frames`` / ``source_observations`` /
    ``canonical_features`` / ``generated_representations``; the store exposes
    them under shorter attribute names. Reading the attribute names here
    silently reported 0 for every real document.
    """
    return {
        "frames": len(doc.get("coordinate_frames") or {}),
        "observations": len(doc.get("source_observations") or []),
        "features": len(doc.get("canonical_features") or []),
        "representations": len(doc.get("generated_representations") or []),
    }


def cmd_validate(args) -> int:
    path = Path(args.document)
    doc = _read(path)
    findings = validate_document(doc)
    counts = _counts(doc)
    by_code = Counter(f.code for f in findings)

    if args.json:
        print(json.dumps({
            "document": str(path),
            "schema_version_declared": doc.get("schema_version"),
            "schema_version_expected": S.SCHEMA_VERSION,
            "counts": counts,
            "finding_count": len(findings),
            "findings_by_code": dict(by_code.most_common()),
            "findings": [f.as_dict() for f in findings],
            "ok": not findings,
        }, indent=2, sort_keys=True))
    else:
        print(f"[world-ir] {path}")
        print(f"[world-ir] schema_version={doc.get('schema_version')} expected={S.SCHEMA_VERSION}")
        print("[world-ir] " + "  ".join(f"{k}={v}" for k, v in counts.items()))
        limit = len(findings) if args.all else min(len(findings), 40)
        for f in findings[:limit]:
            print(f"  FAIL {f}")
        if limit < len(findings):
            print(f"  ... {len(findings) - limit} further finding(s); pass --all to list them")
        if findings:
            print(f"[world-ir] {len(findings)} finding(s): "
                  + ", ".join(f"{c}x{n}" for c, n in by_code.most_common(8)))
        else:
            print("[world-ir] ok")
    return 1 if findings else 0


def cmd_canonicalize(args) -> int:
    path = Path(args.document)
    doc = _read(path)
    canonical = S.canonical_bytes(doc)
    current = path.read_bytes()

    if args.check:
        same = canonical == current
        print(f"[world-ir] canonical={'yes' if same else 'no'} {path} "
              f"({len(current)} bytes on disk, {len(canonical)} canonical)")
        return 0 if same else 1

    out = Path(args.out) if args.out else path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(canonical)
    changed = canonical != current or out != path
    print(f"[world-ir] wrote {out} ({len(canonical)} bytes, "
          f"{'rewritten' if changed else 'already canonical'})")
    return 0


def cmd_stats(args) -> int:
    path = Path(args.document)
    store = WorldIRStore.from_dict(_read(path))
    per_class = Counter(f.get("semantic_class") for f in store.features)
    per_authority = Counter(f.get("gameplay_authority") for f in store.features)
    per_conflict = Counter((f.get("conflict") or {}).get("state") for f in store.features)
    per_prov: Counter = Counter()
    for feat in store.features:
        for claim in (feat.get("properties") or {}).values():
            if isinstance(claim, dict):
                per_prov[claim.get("provenance")] += 1

    payload = {
        "document": str(path),
        "counts": _counts(store.to_dict()),
        "features_by_class": dict(per_class.most_common()),
        "features_by_gameplay_authority": dict(per_authority.most_common()),
        "features_by_conflict_state": dict(per_conflict.most_common()),
        "property_claims_by_provenance": dict(per_prov.most_common()),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m scripts.pipeline.world_ir",
                                 description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate a World IR document; exit 1 on any finding")
    v.add_argument("document")
    v.add_argument("--json", action="store_true", help="machine-readable receipt")
    v.add_argument("--all", action="store_true", help="list every finding, not the first 40")
    v.set_defaults(func=cmd_validate)

    c = sub.add_parser("canonicalize", help="rewrite a document as canonical bytes")
    c.add_argument("document")
    c.add_argument("--out", help="write here instead of in place")
    c.add_argument("--check", action="store_true",
                   help="do not write; exit 1 if the file is not already canonical")
    c.set_defaults(func=cmd_canonicalize)

    s = sub.add_parser("stats", help="summarize classes, authority, conflicts and provenance")
    s.add_argument("document")
    s.set_defaults(func=cmd_stats)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
