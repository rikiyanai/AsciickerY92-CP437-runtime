#!/usr/bin/env python3
"""FL-4482 / FL-4512 deliverable #1: allocate the manifest-admitted absence-tile GlyphId.

Canonical allocator front door for the C6 absence tile (ledger §05.6 ABI-A1).
Scans the glyph-id allocation surfaces, picks the collision-free next-free id,
and writes `assets/glyphs/glyph_id_registry.json` in ABI-A1 schema. A collision
aborts with a nonzero exit and writes no registry (the 1693->1876 drift after
d53b590ae is the recorded reason re-scans are mandatory). The chosen id is
invalid until manifest+atlas+LUT+shape-catalog admission echoes it (ABI-A1
admission rule); this command pins the id only.

Usage:
  python3 scripts/allocate_glyph_id.py --role absence_tile \
      --registry assets/glyphs/glyph_id_registry.json \
      --scan assets/glyphs/fixtures assets/glyphs/generated assets/glyphs/atlases assets/glyphs/profiles \
      [--write]
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()

def scan_surface(d: Path):
    """Return (entries, used_ids) for one scan dir."""
    entries = []
    used = set()
    if not d.exists():
        return entries, used
    for jf in d.rglob("*.json"):
        try:
            j = json.loads(jf.read_text())
        except Exception:
            continue
        ids = []
        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("glyph_id", "glyph_ids") and isinstance(v, (int, list)):
                        if isinstance(v, int):
                            ids.append(v)
                        else:
                            ids.extend(x for x in v if isinstance(x, int))
                    else:
                        walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(j)
        if not ids:
            continue
        used.update(ids)
        entries.append({
            "path": str(jf.relative_to(REPO)), "kind": "json_glyph_ids",
            "sha256": sha256(jf), "min_gid": min(ids), "max_gid": max(ids),
            "count": len(ids), "scanner": "allocate_glyph_id.py",
        })
    return entries, used

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="absence_tile")
    ap.add_argument("--registry", default="assets/glyphs/glyph_id_registry.json")
    ap.add_argument("--scan", nargs="+", default=[
        "assets/glyphs/fixtures", "assets/glyphs/generated",
        "assets/glyphs/atlases", "assets/glyphs/profiles"])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--display-name", default="Absence Tile")
    ap.add_argument("--unicode-scalar", default="U+FFFD")
    ap.add_argument("--symbol-name")
    ap.add_argument("--fg", type=int)
    ap.add_argument("--bg", type=int)
    ap.add_argument("--reason")
    ap.add_argument("--refresh-admission", action="store_true")
    args = ap.parse_args()

    all_entries, used = [], set()
    # Include the packed content range 512..671 as a reserved surface (ledger C6).
    used.update(range(512, 672))
    all_entries.append({
        "path": "<packed_content_range>", "kind": "reserved_range",
        "sha256": "n/a", "min_gid": 512, "max_gid": 671, "count": 160,
        "scanner": "allocate_glyph_id.py",
    })
    for s in args.scan:
        e, u = scan_surface(REPO / s)
        all_entries.extend(e)
        used.update(u)
    reg_path = REPO / args.registry
    existing = json.loads(reg_path.read_text()) if reg_path.exists() else None
    existing_class = None
    if existing and args.role != "absence_tile":
        existing_class = existing.get("absence_classes", {}).get(args.role)
    hi = max(used) if used else 255
    candidate = int(existing_class["glyph_id"]) if existing_class else hi + 1
    if not existing_class and candidate in used:
        sys.exit(f"allocate_glyph_id: collision at candidate {candidate}; aborting (no registry written)")
    try:
        commit = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"]).decode().strip()
    except Exception:
        commit = "unknown"
    utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    scan_sha = hashlib.sha256(json.dumps(all_entries, sort_keys=True).encode()).hexdigest()
    if args.role != "absence_tile" and not existing:
        sys.exit("allocate_glyph_id: class allocation requires an existing registry")
    if args.role != "absence_tile" and not existing_class:
        required = (args.symbol_name, args.reason, args.unicode_scalar,
                    args.display_name, args.fg, args.bg)
        if any(value is None for value in required):
            sys.exit("allocate_glyph_id: class allocation requires --symbol-name, "
                     "--reason, --unicode-scalar, --display-name, --fg, and --bg")
    reg = {
        "schema": "glyph_id_registry.v1",
        "generated_by": "scripts/allocate_glyph_id.py",
        "registry_sha256": "PENDING",  # backfilled after write
        "allocation_surfaces": all_entries,
        "reserved_ids": [],
        "allocated_ids": [candidate],
        "absence_tile": {
            "glyph_id": candidate,
            "symbol_name": "GLYPH_ID_ABSENT_TILE",
            "unicode_scalar": args.unicode_scalar,
            "display_name": args.display_name,
            "semantic_role": "absence_tile",
            "allocated_by": "scripts/allocate_glyph_id.py",
            "allocated_at_commit": commit,
            "allocation_scan_sha256": scan_sha,
            "atlas_manifest_path": "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json",
            "atlas_manifest_sha256": "PENDING_ATLAS_ADMISSION",
            "shape_catalog_path": "assets/glyphs/generated/material.morphology.v2.shape_catalog.json",
            "shape_catalog_sha256": "PENDING_ATLAS_ADMISSION",
            "admission_manifest_path": "assets/glyphs/manifests/absence_tile.v1.manifest.json",
            "admission_manifest_sha256": "PENDING_ATLAS_ADMISSION",
        },
        "note": "absence_tile.*_sha256 fields are PENDING until the atlas-admission step (ABI-A3) lands.",
    }
    if args.role != "absence_tile":
        reg = existing
        reg["allocation_surfaces"] = all_entries
        reg["allocated_ids"] = sorted(set(reg.get("allocated_ids", [])) | {candidate})
        if not existing_class:
            reg.setdefault("absence_classes", {})[args.role] = {
                "glyph_id": candidate,
                "symbol_name": args.symbol_name,
                "unicode_scalar": args.unicode_scalar,
                "display_name": args.display_name,
                "fg": args.fg,
                "bg": args.bg,
                "reason": args.reason,
                "admitted": False,
            }
        if args.refresh_admission:
            manifest_path = REPO / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"
            atlas_path = REPO / "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json"
            lut_path = REPO / "assets/glyphs/atlases/material.additive.v1.lut_rgba8.json"
            page_path = REPO / "assets/glyphs/atlases/material.additive.v1.page16_rgba8.json"
            manifest = json.loads(manifest_path.read_text())
            atlas = json.loads(atlas_path.read_text())
            if candidate not in manifest.get("admission_set", []):
                sys.exit(f"allocate_glyph_id: {candidate} absent from manifest admission_set")
            if str(candidate) not in atlas.get("glyph_index", {}):
                sys.exit(f"allocate_glyph_id: {candidate} absent from atlas glyph_index")
            common = {
                "admission_manifest_sha256": sha256(manifest_path),
                "atlas_manifest_sha256": sha256(atlas_path),
                "lut_sha256": sha256(lut_path),
                "atlas_page_sha256": sha256(page_path),
                "admitted": True,
            }
            reg["absence_classes"][args.role].update(common)
            reg.get("absence_tile", {}).update(common)
    print(json.dumps({"candidate": candidate, "highest_allocated": hi,
                      "collision_free": True, "surfaces": len(all_entries),
                      "will_write": args.write}, indent=2))
    if args.write:
        reg["registry_sha256"] = "PENDING"
        body = json.dumps(reg, indent=2)
        reg["registry_sha256"] = hashlib.sha256(body.encode()).hexdigest()
        reg_path.write_text(json.dumps(reg, indent=2) + "\n")
        print(f"wrote {reg_path.relative_to(REPO)} (candidate {candidate})")

if __name__ == "__main__":
    main()
