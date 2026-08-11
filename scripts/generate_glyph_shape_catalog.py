#!/usr/bin/env python3
"""Generate/verify FL-4131 extended glyph shape metrics from baked atlas pixels."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

from compile_glyph_manifest import sha256_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"
DEFAULT_AOA = REPO_ROOT / "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json"
DEFAULT_OUTPUT = REPO_ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
DEFAULT_CELL_PX = 16
ATLAS_COLS = 16
SHAPE6_REGION_MODEL = "harri6_internal6_regions_v1"
SHAPE6_REGION_CENTERS = [
    (0.25, 0.25),
    (0.75, 0.25),
    (0.25, 0.50),
    (0.75, 0.50),
    (0.25, 0.75),
    (0.75, 0.75),
]
SHAPE6_REGION_RADIUS = {
    "x_cell_fraction": 0.22,
    "y_cell_fraction": 0.18,
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def repertoire_for_scalar(u: int) -> str:
    if 0x0600 <= u <= 0x06FF:
        return "arabic"
    if 0x3040 <= u <= 0x309F:
        return "hiragana"
    if 0x30A0 <= u <= 0x30FF:
        return "katakana"
    if 0x4E00 <= u <= 0x9FFF:
        return "cjk"
    if 0x2500 <= u <= 0x257F:
        return "box"
    if 0x2580 <= u <= 0x259F:
        return "block"
    if 0x2190 <= u <= 0x21FF or 0x27F0 <= u <= 0x27FF or 0x2900 <= u <= 0x297F:
        return "arrows"
    if 0x2200 <= u <= 0x22FF or 0x2300 <= u <= 0x23FF:
        return "math"
    if 0x25A0 <= u <= 0x25FF or 0x2600 <= u <= 0x27BF:
        return "shapes"
    if 0x00A0 <= u <= 0x024F or 0x2000 <= u <= 0x206F or 0x02B0 <= u <= 0x036F:
        return "punct"
    return "other"


def page_path_for_cell_px(aoa: dict[str, Any], cell_px: int) -> Path:
    for page in aoa.get("pages", []):
        if int(page.get("cell_px", -1)) == cell_px:
            return (DEFAULT_AOA.parent / str(page["url"])).resolve()
    raise SystemExit(f"AOA has no page for cell_px={cell_px}")


def ink_grid(page: dict[str, Any], entry_index: int, cell_px: int) -> list[list[int]]:
    width = int(page["width"])
    height = int(page["height"])
    rgba8 = page["rgba8"]
    cols = width // cell_px
    x0 = (entry_index % cols) * cell_px
    y0 = (entry_index // cols) * cell_px
    if x0 + cell_px > width or y0 + cell_px > height:
        raise SystemExit(f"glyph entry index {entry_index} outside page {width}x{height}")
    out: list[list[int]] = []
    for y in range(cell_px):
        row: list[int] = []
        for x in range(cell_px):
            p = ((y0 + y) * width + (x0 + x)) * 4
            r, g, b, a = rgba8[p:p + 4]
            row.append(1 if a > 8 and (r or g or b) else 0)
        out.append(row)
    return out


def density(grid: list[list[int]], xs: range, ys: range) -> float:
    total = max(1, len(xs) * len(ys))
    ink = 0
    for y in ys:
        for x in xs:
            ink += grid[y][x]
    return ink / float(total)


def metric_round(v: float) -> float:
    return round(max(0.0, min(1.0, v)), 4)


def shape6_metrics(grid: list[list[int]]) -> dict[str, float | list[float]]:
    n = len(grid)
    radius_x = max(1.0, n * 0.22)
    radius_y = max(1.0, n * 0.18)
    raw: list[float] = []
    for cx_norm, cy_norm in SHAPE6_REGION_CENTERS:
        cx = cx_norm * (n - 1)
        cy = cy_norm * (n - 1)
        ink = 0.0
        weight = 0.0
        for y in range(n):
            for x in range(n):
                dx = (x - cx) / radius_x
                dy = (y - cy) / radius_y
                d2 = dx * dx + dy * dy
                if d2 <= 1.0:
                    w = 1.0 - d2
                    ink += grid[y][x] * w
                    weight += w
        raw.append(metric_round(ink / max(weight, 1e-6)))
    max_v = max(raw) if raw else 0.0
    norm = [metric_round(v / max(max_v, 1e-6)) for v in raw]
    left = (raw[0] + raw[2] + raw[4]) / 3.0
    right = (raw[1] + raw[3] + raw[5]) / 3.0
    top = (raw[0] + raw[1]) / 2.0
    bottom = (raw[4] + raw[5]) / 2.0
    return {
        "shape6": raw,
        "shape6_norm": norm,
        "shape6_density": metric_round(sum(raw) / 6.0),
        "shape6_asymmetry_lr": metric_round(abs(left - right)),
        "shape6_asymmetry_tb": metric_round(abs(top - bottom)),
        "shape6_diag_ne_sw": metric_round(abs(raw[1] + raw[2] + raw[4] - raw[0] - raw[3] - raw[5]) / 3.0),
        "shape6_diag_nw_se": metric_round(abs(raw[0] + raw[3] + raw[5] - raw[1] - raw[2] - raw[4]) / 3.0),
    }


def analyze_grid(grid: list[list[int]]) -> dict[str, float | str | list[str]]:
    n = len(grid)
    all_x = range(n)
    all_y = range(n)
    third = max(1, n // 3)
    top = range(0, third)
    mid = range(third, min(n, third * 2))
    bot = range(min(n, third * 2), n)
    left = range(0, n // 2)
    right = range(n // 2, n)

    d = density(grid, all_x, all_y)
    top_d = density(grid, all_x, top)
    mid_d = density(grid, all_x, mid)
    bot_d = density(grid, all_x, bot)
    left_d = density(grid, left, all_y)
    right_d = density(grid, right, all_y)
    q = [
        density(grid, range(0, n // 2), range(0, n // 2)),
        density(grid, range(n // 2, n), range(0, n // 2)),
        density(grid, range(0, n // 2), range(n // 2, n)),
        density(grid, range(n // 2, n), range(n // 2, n)),
    ]

    diag_band = max(1, int(math.ceil(n * 0.16)))
    nw_se_total = ne_sw_total = nw_se_ink = ne_sw_ink = 0
    for y in range(n):
        for x in range(n):
            if abs(x - y) <= diag_band:
                nw_se_total += 1
                nw_se_ink += grid[y][x]
            if abs((n - 1 - x) - y) <= diag_band:
                ne_sw_total += 1
                ne_sw_ink += grid[y][x]
    diag_nw_se = nw_se_ink / float(max(1, nw_se_total))
    diag_ne_sw = ne_sw_ink / float(max(1, ne_sw_total))

    edge_transitions = 0
    for y in range(n):
        for x in range(1, n):
            edge_transitions += 1 if grid[y][x] != grid[y][x - 1] else 0
    for y in range(1, n):
        for x in range(n):
            edge_transitions += 1 if grid[y][x] != grid[y - 1][x] else 0
    transition_rate = edge_transitions / float(max(1, 2 * n * (n - 1)))

    corner_score = max(q) * (1.0 - min(q))
    horizontal_bias = abs(mid_d - ((top_d + bot_d) * 0.5))
    vertical_bias = abs(left_d - right_d)
    diagonal_bias = max(diag_nw_se, diag_ne_sw)
    curve_score = max(0.0, min(1.0, (transition_rate * 1.35) + (min(top_d + mid_d + bot_d, 1.0) * 0.15) - corner_score * 0.35))

    if d < 0.05:
        stroke_class = "dot"
    elif d > 0.62:
        stroke_class = "block"
    elif corner_score > 0.22:
        stroke_class = "corner"
    elif diagonal_bias > 0.34 and diagonal_bias > max(mid_d, left_d, right_d):
        stroke_class = "diagonal"
    elif horizontal_bias > 0.18 and mid_d >= max(top_d, bot_d):
        stroke_class = "horizontal"
    elif vertical_bias < 0.08 and (left_d + right_d) > 0.28:
        stroke_class = "vertical"
    elif curve_score > 0.42:
        stroke_class = "curve"
    elif transition_rate > 0.38:
        stroke_class = "cross"
    else:
        stroke_class = "line"

    return {
        "density": metric_round(d),
        "top_weight": metric_round(top_d),
        "mid_weight": metric_round(mid_d),
        "bottom_weight": metric_round(bot_d),
        "left_weight": metric_round(left_d),
        "right_weight": metric_round(right_d),
        "diag_ne_sw": metric_round(diag_ne_sw),
        "diag_nw_se": metric_round(diag_nw_se),
        "curve_score": metric_round(curve_score),
        "corner_score": metric_round(corner_score),
        "stroke_class": stroke_class,
    }


def roles_for(entry: dict[str, Any], metrics: dict[str, Any], repertoire: str) -> list[str]:
    label = str(entry.get("label", "")).lower()
    roles: set[str] = set()
    density_v = float(metrics["density"])
    curve = float(metrics["curve_score"])
    top = float(metrics["top_weight"])
    diag = max(float(metrics["diag_ne_sw"]), float(metrics["diag_nw_se"]))
    stroke = metrics["stroke_class"]

    if repertoire == "arabic" or (curve >= 0.35 and top >= 0.12):
        roles.add("grass_top")
    if repertoire in {"arabic", "math", "box"} and (curve >= 0.25 or "wave" in label or "arc" in label):
        roles.add("wave_flow")
    if repertoire == "arabic" and density_v <= 0.55:
        roles.add("flower_top")
    if repertoire in {"katakana", "cjk"} or stroke in {"vertical", "diagonal"} or diag >= 0.30:
        roles.add("rock_face")
    if "horizontal" in label or "strata" in label or "almost_equal" in label or "identical" in label:
        roles.add("strata")
    if stroke == "corner" or "corner" in label or "arc" in label:
        roles.add("corner_lip")
    if stroke in {"cross", "diagonal"} or any(token in label for token in ("angle", "fracture", "minus", "plus", "division", "multiplication", "tack")):
        roles.add("fracture")
    if density_v > 0.45:
        roles.add("dense_fill")
    if density_v < 0.18:
        roles.add("sparse_detail")
    return sorted(roles)


def build_catalog(manifest_path: Path, aoa_path: Path, cell_px: int) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    aoa = load_json(aoa_path)
    page_path = page_path_for_cell_px(aoa, cell_px)
    page = load_json(page_path)
    actual_page_hash = hashlib.sha256(bytes(page["rgba8"])).hexdigest()

    entries = sorted(manifest["entries"], key=lambda e: int(e["glyph_id"]))
    catalog_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        glyph_id = int(entry["glyph_id"])
        scalar = int(entry.get("unicode_scalar") or 0)
        grid = ink_grid(page, index, cell_px)
        metrics = analyze_grid(grid)
        shape6 = shape6_metrics(grid)
        repertoire = repertoire_for_scalar(scalar)
        catalog_entries.append({
            "glyph_id": glyph_id,
            "label": str(entry.get("label", f"glyph_{glyph_id}")).lower().replace("_", " "),
            "unicode": chr(scalar) if scalar else "",
            "unicode_scalar": scalar,
            "repertoire": repertoire,
            **metrics,
            **shape6,
            "roles": roles_for(entry, metrics, repertoire),
        })

    return {
        "schema_version": 1,
        "generated_at": date.today().isoformat(),
        "generator": "scripts/generate_glyph_shape_catalog.py",
        "content_pack_id": manifest["content_pack_id"],
        "manifest_path": str(manifest_path.relative_to(REPO_ROOT)),
        "manifest_hash": sha256_manifest(manifest),
        "atlas_of_atlases": str(aoa_path.relative_to(REPO_ROOT)),
        "cell_px": cell_px,
        "page_path": str(page_path.relative_to(REPO_ROOT)),
        "page_hash": actual_page_hash,
        "metric_model": "atlas_summary_metrics_v1",
        "shape6_region_model": SHAPE6_REGION_MODEL,
        "shape6_region_centers": [[x, y] for x, y in SHAPE6_REGION_CENTERS],
        "shape6_region_radius": SHAPE6_REGION_RADIUS,
        "entries": catalog_entries,
    }


def verify_catalog(catalog_path: Path, manifest_path: Path) -> int:
    catalog = load_json(catalog_path)
    manifest = load_json(manifest_path)
    errors: list[str] = []
    if catalog.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if catalog.get("shape6_region_model") != SHAPE6_REGION_MODEL:
        errors.append("shape6_region_model mismatch")
    if catalog.get("shape6_region_centers") != [[x, y] for x, y in SHAPE6_REGION_CENTERS]:
        errors.append("shape6_region_centers mismatch")
    if catalog.get("shape6_region_radius") != SHAPE6_REGION_RADIUS:
        errors.append("shape6_region_radius mismatch")
    if catalog.get("manifest_hash") != sha256_manifest(manifest):
        errors.append("manifest_hash does not match current manifest")
    admitted = set(int(x) for x in manifest.get("admission_set", []))
    catalog_ids = {int(e.get("glyph_id")) for e in catalog.get("entries", [])}
    missing = sorted(admitted - catalog_ids)
    extra = sorted(catalog_ids - admitted)
    if missing:
        errors.append(f"missing admitted glyphs: {missing}")
    if extra:
        errors.append(f"catalog has non-admitted glyphs: {extra}")
    required = {
        "density", "top_weight", "mid_weight", "bottom_weight", "left_weight",
        "right_weight", "diag_ne_sw", "diag_nw_se", "curve_score",
        "corner_score", "stroke_class", "roles",
    }
    for e in catalog.get("entries", []):
        for field in required:
            if field not in e:
                errors.append(f"glyph_id {e.get('glyph_id')} missing {field}")
    if errors:
        for err in errors:
            print(f"[FAIL] {err}", file=sys.stderr)
        return 1
    print(f"[OK] shape catalog verified: {len(catalog_ids)} admitted glyphs")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--aoa", default=str(DEFAULT_AOA))
    parser.add_argument("--cell-px", type=int, default=DEFAULT_CELL_PX)
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    manifest = Path(args.manifest)
    out = Path(args.out)
    if args.verify:
        return verify_catalog(out, manifest)
    catalog = build_catalog(manifest, Path(args.aoa), args.cell_px)
    dump_json(out, catalog)
    print(f"[OK] wrote {len(catalog['entries'])} glyph shape rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
