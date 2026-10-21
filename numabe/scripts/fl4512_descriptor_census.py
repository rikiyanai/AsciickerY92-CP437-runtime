#!/usr/bin/env python3
# =============================================================
# fl4512_descriptor_census.py
# =============================================================
# FL-4512 universal research target, step 2: the unified
# descriptor census.
#
# The admitted runtime supply is 110,604 glyph bindings: 1,188
# measured material glyphs (pack material.morphology.v2) plus
# 109,416 universal Unicode allocations (pack unicode16.onecell.v1).
# The universal catalog records the u7 mask/endpoint/junction
# descriptors but carries NO global6/shape6 record, while the
# material catalog carries shape6 but no unified u7 record. That
# split makes "exact descriptor signature" equivalence (step 3 of
# the research chain) undefined across the full supply.
#
# This script produces ONE census artifact with the SAME descriptor
# tuple for every admitted binding:
#
#   glyph_id, pack, unicode_scalar,
#   shape6 / shape6_norm      (six-region ink coverage, the exact
#                              function the material catalog used),
#   shape6_norm_q             (each component through the SS-C.10
#                              fixed-point quantizer q_F, F=8 — the
#                              same integers the runtime scorer
#                              compares, so census equivalence is
#                              runtime equivalence),
#   mask8_u64, endpoint_count, junction_cluster_count
#                             (the canonical u7 tile rules from the
#                              FL-4482 coverage census),
#   signature                 (one exact string over all of the
#                              above — equality of signatures IS the
#                              step-3 equivalence relation; there is
#                              no threshold anywhere).
#
# Nothing here is a new measurement DEFINITION. Every rule is
# imported from its owning module so the census cannot drift:
#   - shape6_metrics(...)  from scripts/generate_glyph_shape_catalog.py
#   - mask8/topology/tiles from scripts/fl4482_coverage_census.py
#
# Self-checks written into the manifest:
#   - for the material pack, shape6 measured from the atlas TILE is
#     compared against the catalog's authored shape6 (which was
#     measured from the font render); the deviation statistics
#     validate that atlas tiles are a faithful measurement surface;
#   - for a sample of universal tiles, mask8 recomputed from the
#     tile must equal the catalog's mask8 exactly (catches page or
#     tile addressing bugs).
#
# Deterministic by construction: inputs are hash-pinned artifacts,
# records are sorted by glyph_id, no timestamps are written.
#
# Run:  python3 scripts/fl4512_descriptor_census.py
# Out:  assets/glyphs/generated/fl4512.descriptor_census.v1.jsonl
#       assets/glyphs/generated/fl4512.descriptor_census.v1.manifest.json
# =============================================================

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# The canonical measurement functions — imported, never copied.
from generate_glyph_shape_catalog import shape6_metrics  # noqa: E402
import fl4482_coverage_census as f82  # noqa: E402

from PIL import Image  # noqa: E402

GENERATED = ROOT / "assets/glyphs/generated"
BINDINGS_PATH = GENERATED / "unicode16.onecell.v1.runtime_bindings.json"
UNIVERSAL_CATALOG_PATH = GENERATED / "unicode16.onecell.v1.shape_catalog.json"
MORPH_CATALOG_PATH = GENERATED / "material.morphology.v2.shape_catalog.json"
OUT_JSONL = GENERATED / "fl4512.descriptor_census.v1.jsonl"
OUT_MANIFEST = GENERATED / "fl4512.descriptor_census.v1.manifest.json"

CELL_PX = f82.CELL_PX  # 16 — one atlas tile edge in pixels
COLS = f82.COLS        # 16 — tiles per page row


def tile_grid(tile: bytes) -> list[list[int]]:
    """16x16 binary ink grid from one RGBA8 tile.

    Uses the SAME pixel-on rule as the canonical mask8() reducer
    (alpha above 8 AND any nonzero color channel), just without the
    2x2 downsample, so the shape6 measurement sees the full tile.
    """
    grid = []
    for y in range(CELL_PX):
        row = []
        for x in range(CELL_PX):
            p = (y * CELL_PX + x) * 4
            on = tile[p + 3] > 8 and (tile[p] or tile[p + 1] or tile[p + 2])
            row.append(1 if on else 0)
        grid.append(row)
    return grid


def qf8(x: float) -> int:
    """SS-C.10 q_F with F=8: min(256, max(0, floor(x*256 + 0.5))).

    The same half-up, clamped, fail-shut-on-NaN map the runtime
    scorer uses (Fl4512QuantF8 in fl4131_runtime_harri_resolver.cpp),
    so two glyphs share a census signature exactly when the runtime
    scorer cannot tell them apart on that component.
    """
    if not (x > 0.0):
        return 0
    if x > 1.0:
        x = 1.0
    return int(x * 256.0 + 0.5)


def universal_tiles() -> dict[tuple[int, int], bytes]:
    """Load all universal page16 PNGs and slice them into tiles.

    Returns {(page_index, tile_index): rgba8 bytes}. Page geometry
    comes from the PNG itself; the column count is the canonical
    COLS from the coverage census.
    """
    tiles: dict[tuple[int, int], bytes] = {}
    page_index = 0
    while True:
        path = f82.unicode_page_png_path(page_index)
        if not path.exists():
            break
        img = Image.open(path).convert("RGBA")
        raw = img.tobytes()
        width, height = img.size
        rows = height // CELL_PX
        for tile_index in range(rows * COLS):
            x0 = (tile_index % COLS) * CELL_PX
            y0 = (tile_index // COLS) * CELL_PX
            tile = bytearray()
            for y in range(CELL_PX):
                start = ((y0 + y) * width + x0) * 4
                tile.extend(raw[start:start + CELL_PX * 4])
            tiles[(page_index, tile_index)] = bytes(tile)
        page_index += 1
    return tiles


def main() -> int:
    bindings = json.loads(BINDINGS_PATH.read_text())
    universal_catalog = json.loads(UNIVERSAL_CATALOG_PATH.read_text())
    morph_catalog = json.loads(MORPH_CATALOG_PATH.read_text())

    # Per-gid descriptor rows from the two source catalogs.
    universal_by_gid = {int(e["glyph_id"]): e for e in universal_catalog["entries"]}
    morph_by_gid = {int(e["glyph_id"]): e for e in morph_catalog["entries"]}

    # Atlas tiles for both packs. The material pack loader is the
    # canonical one from the coverage census (keyed by scalar).
    print("loading atlas tiles…", flush=True)
    uni_tiles = universal_tiles()
    _, morph_tiles_by_scalar, _, _, _ = (lambda t: (None, t[1], None, None, None))(f82.current_pack())

    rows = []
    signatures: set[str] = set()
    per_pack = {"unicode16.onecell.v1": 0, "material.morphology.v2": 0}
    # Self-check accumulators (documented in the file header).
    crosscheck_n = 0
    crosscheck_max_dev = 0.0
    crosscheck_sum_dev = 0.0
    mask_check_n = 0
    mask_check_bad = 0
    missing = []

    ordered = sorted(bindings["bindings"], key=lambda b: int(b["glyph_id"]))
    for i, binding in enumerate(ordered):
        gid = int(binding["glyph_id"])
        pack = binding["pack"]
        scalar = int(binding["unicode_scalar"])
        if pack == "unicode16.onecell.v1":
            cat = universal_by_gid.get(gid)
            tile = uni_tiles.get((int(binding["page_index"]), int(binding["tile_index"])))
            if cat is None or tile is None:
                missing.append(gid)
                continue
            metrics = shape6_metrics(tile_grid(tile))
            mask = int(cat["mask8_u64"], 16)
            endpoints = int(cat["endpoint_count"])
            junctions = int(cat["junction_cluster_count"])
            # Addressing self-check on a deterministic sample: every
            # 512th binding recomputes mask8 from the tile and must
            # match the catalog bit for bit.
            if i % 512 == 0:
                mask_check_n += 1
                if f82.mask8(tile) != mask:
                    mask_check_bad += 1
        else:
            cat = morph_by_gid.get(gid)
            tile = morph_tiles_by_scalar.get(scalar)
            if cat is None or tile is None:
                missing.append(gid)
                continue
            # Authored catalog shape6 is the source of truth for the
            # measured pack; the tile-measured value is the cross-check.
            metrics = {"shape6": cat["shape6"], "shape6_norm": cat["shape6_norm"]}
            tile_metrics = shape6_metrics(tile_grid(tile))
            for a, b in zip(cat["shape6"], tile_metrics["shape6"]):
                dev = abs(float(a) - float(b))
                crosscheck_max_dev = max(crosscheck_max_dev, dev)
                crosscheck_sum_dev += dev
            crosscheck_n += 1
            mask = f82.mask8(tile)
            endpoints, junctions = f82.topology(mask)

        q6 = [qf8(float(v)) for v in metrics["shape6_norm"]]
        # The exact signature: quantized shape6 + full u7 record.
        # String equality of signatures is the step-3 equivalence
        # relation — exact, therefore transitive by construction.
        signature = "{}-{:016x}-{:02x}-{:02x}".format(
            "".join(f"{q:03x}" for q in q6), mask, endpoints, junctions)
        signatures.add(signature)
        per_pack[pack] += 1
        rows.append({
            "glyph_id": gid,
            "pack": pack,
            "unicode_scalar": scalar,
            "shape6": [float(v) for v in metrics["shape6"]],
            "shape6_norm": [float(v) for v in metrics["shape6_norm"]],
            "shape6_norm_q": q6,
            "mask8_u64": f"0x{mask:016x}",
            "endpoint_count": endpoints,
            "junction_cluster_count": junctions,
            "signature": signature,
        })
        if i % 10000 == 0:
            print(f"  {i}/{len(ordered)}", flush=True)

    # One line per record, sorted by glyph_id (already ordered).
    body = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
    OUT_JSONL.write_text(body)
    body_sha = hashlib.sha256(body.encode()).hexdigest()

    manifest = {
        "schema": "fl4512.descriptor_census.v1",
        "census_jsonl": OUT_JSONL.name,
        "census_sha256": body_sha,
        "record_count": len(rows),
        "records_per_pack": per_pack,
        "missing_bindings": missing,
        "distinct_signature_count": len(signatures),
        "signature_rule": "exact string equality over shape6_norm_q (SS-C.10 q_F, F=8) + mask8_u64 + endpoint_count + junction_cluster_count; no thresholds",
        "quantizer": {"name": "q_F", "F": 8, "contract": "2026-06-27 phase-b design SS-C.10"},
        "shape6_source": {
            "unicode16.onecell.v1": "measured from canonical atlas tile (grid rule: alpha>8 and nonzero color, full 16x16)",
            "material.morphology.v2": "authored shape catalog values (font-render measurement)",
        },
        "material_tile_crosscheck": {
            "glyphs": crosscheck_n,
            "component_mean_abs_dev": (crosscheck_sum_dev / (6 * crosscheck_n)) if crosscheck_n else None,
            "component_max_abs_dev": crosscheck_max_dev,
        },
        "universal_mask8_addressing_check": {
            "sampled": mask_check_n, "mismatches": mask_check_bad,
        },
        "inputs": {
            "runtime_bindings_sha256": hashlib.sha256(BINDINGS_PATH.read_bytes()).hexdigest(),
            "universal_catalog_manifest_hash": universal_catalog["manifest_hash"],
            "material_catalog_manifest_hash": morph_catalog["manifest_hash"],
        },
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(f"census: {len(rows)} records, {len(signatures)} distinct signatures")
    print(f"material cross-check: n={crosscheck_n} max_dev={crosscheck_max_dev:.4f}")
    print(f"mask8 addressing check: {mask_check_bad}/{mask_check_n} mismatches")
    print(f"missing bindings: {len(missing)}")
    return 0 if not missing and mask_check_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
