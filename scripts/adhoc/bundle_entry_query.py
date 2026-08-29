#!/usr/bin/env python3
"""Query appearance bundle JSON for entries by contract, slug, or path.

Reads a bundle JSON (default: assets/appearance_bundle/phase2-fixtures/
positive.bundle.json) and filters layer_definition_id entries by contract
type, slug substring, or asset path. Also supports querying
mounted_wrapper_definitions.

Useful for surveying which equipment variants exist for mounts, how many
entries per contract type, and verifying bundle consistency.

Origin: Codex session history — python3 -c one-liners querying
  positive.bundle.json for attack_mount_rider entries and wolf_mount_attack
  wrappers during rider overlay research (codex history.jsonl, entries near
  bigbee/wolack attack mount work).

Generalized: replaced hardcoded bundle path with --bundle flag, added
  multi-field filtering, JSON output, --count-only mode.

Usage:
  python3 scripts/adhoc/bundle_entry_query.py                        # list all entries
  python3 scripts/adhoc/bundle_entry_query.py --contract attack_mount_rider
  python3 scripts/adhoc/bundle_entry_query.py --slug wolf_mount
  python3 scripts/adhoc/bundle_entry_query.py --path bigbee-attack
  python3 scripts/adhoc/bundle_entry_query.py --count-only --contract idle_mount_rider
  python3 scripts/adhoc/bundle_entry_query.py --wrapper --slug wolf_mount_attack
  python3 scripts/adhoc/bundle_entry_query.py --json --contract attack_mount_rider
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLE = REPO_ROOT / "assets" / "appearance_bundle" / "phase2-fixtures" / "positive.bundle.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE,
                        help="Path to bundle JSON (default: phase2 positive.bundle.json)")
    parser.add_argument("--contract", type=str, default="",
                        help="Filter entries by contract field")
    parser.add_argument("--slug", type=str, default="",
                        help="Filter entries by slug substring")
    parser.add_argument("--path", type=str, default="",
                        help="Filter entries by path substring")
    parser.add_argument("--wrapper", action="store_true",
                        help="Query mounted_wrapper_definitions instead of layer_definition_id entries")
    parser.add_argument("--count-only", action="store_true",
                        help="Show only counts, not individual entries")
    parser.add_argument("--json", action="store_true",
                        help="Emit matching entries as JSON array")
    parser.add_argument("--group-by", type=str, default="",
                        help="Group entries by field (e.g. path, contract)")
    args = parser.parse_args()

    bundle_path = args.bundle
    if not bundle_path.is_file():
        print(f"error: bundle file not found: {bundle_path}", file=sys.stderr)
        sys.exit(1)

    with open(bundle_path) as f:
        d = json.load(f)

    if args.wrapper:
        entries = d.get("mounted_wrapper_definitions", [])
    else:
        ns = d.get("id_namespaces", {}).get("layer_definition_id", {})
        entries = ns.get("entries", [])

    # Filter
    if args.contract:
        entries = [e for e in entries if e.get("contract") == args.contract]
    if args.slug:
        entries = [e for e in entries if args.slug in e.get("slug", "")]
    if args.path:
        entries = [e for e in entries if args.path in e.get("path", "")]

    if args.json:
        json.dump(entries, sys.stdout, indent=2)
        print()
        return

    if args.count_only:
        print(f"matching entries: {len(entries)}")
        if args.group_by:
            from collections import Counter
            groups = Counter(e.get(args.group_by, "(none)") for e in entries)
            for key, count in sorted(groups.items()):
                print(f"  {key}: {count}")
        return

    print(f"bundle: {bundle_path.name}")
    print(f"section: {'mounted_wrapper_definitions' if args.wrapper else 'layer_definition_id'}")
    print(f"matching entries: {len(entries)}")
    print()

    if args.wrapper:
        _print_wrappers(entries, args.group_by)
    else:
        _print_entries(entries, args.group_by)


def _print_entries(entries: list[dict], group_by: str) -> None:
    if group_by:
        groups: dict[str, list[dict]] = {}
        for e in entries:
            key = e.get(group_by, "(none)")
            groups.setdefault(key, []).append(e)
        for key in sorted(groups):
            print(f"[{key}] ({len(groups[key])} entries):")
            for e in groups[key][:5]:
                print(f"  slug={e.get('slug', '?')}")
            if len(groups[key]) > 5:
                print(f"  ... and {len(groups[key]) - 5} more")
            print()
        return

    for e in entries:
        print(f"  slug={e.get('slug', '?')}")
        print(f"    contract={e.get('contract', '?')}")
        print(f"    path={e.get('path', '?')}")
        comp = e.get("composition", {})
        if comp:
            pieces = ", ".join(comp.keys())
            print(f"    composition: {pieces}")
        print()


def _print_wrappers(wrappers: list[dict], group_by: str) -> None:
    if group_by:
        groups: dict[str, list[dict]] = {}
        for w in wrappers:
            key = w.get(group_by, "(none)")
            groups.setdefault(key, []).append(w)
        for key in sorted(groups):
            print(f"[{key}] ({len(groups[key])} entries):")
            for w in groups[key][:3]:
                print(f"  slug={w.get('slug', '?')}")
            if len(groups[key]) > 3:
                print(f"  ... and {len(groups[key]) - 3} more")
            print()
        return

    for w in wrappers:
        slug = w.get("slug", "?")
        ref = w.get("parity_reference_layer_definition_slug", "?")
        print(f"slug: {slug}")
        print(f"  parity_reference: {ref}")
        offsets = w.get("rider_offset_by_facing", [])
        for o in offsets:
            print(f"    angle {o.get('angle_index', '?')}: dx={o.get('dx', '?')} dy={o.get('dy', '?')}")
        print()


if __name__ == "__main__":
    main()
