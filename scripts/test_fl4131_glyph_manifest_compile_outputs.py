#!/usr/bin/env python3
"""Regression checks for FL-4131 glyph manifest atlas compile output."""

from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"
TRACKED_OUT = REPO_ROOT / "assets/glyphs/atlases"
EXPECTED_HASH = "077de379be107288555c7162ad737f0545fc637d43a2cf085051aed578b4aa8e"
EXPECTED_SIZES = [16]
ATLAS_COLS = 16
ENTRY_COUNT = 164  # 512..671 plus 1876..1879
EXPECTED_ROWS = (ENTRY_COUNT + ATLAS_COLS - 1) // ATLAS_COLS  # 11
MIN_UNIQUE_CANONICAL_GLYPH_MASKS = 90


def count_nonempty_unique_alpha_masks(page: dict) -> tuple[int, int]:
    width = int(page["width"])
    cell_px = int(page["cell_px"])
    rgba8 = page["rgba8"]
    unique: set[str] = set()
    nonempty = 0
    for glyph_index in range(ENTRY_COUNT):
        x0 = (glyph_index % ATLAS_COLS) * cell_px
        y0 = (glyph_index // ATLAS_COLS) * cell_px
        alpha = bytearray()
        for y in range(cell_px):
            row_offset = ((y0 + y) * width + x0) * 4
            for x in range(cell_px):
                alpha.append(int(rgba8[row_offset + x * 4 + 3]))
        if any(alpha):
            nonempty += 1
            unique.add(hashlib.sha256(bytes(alpha)).hexdigest())
    return nonempty, len(unique)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fl4131-glyph-compile-") as td:
        out_dir = Path(td)
        result = subprocess.run(
            [
                "python3",
                str(REPO_ROOT / "scripts/compile_glyph_manifest.py"),
                f"--compile={MANIFEST}",
                "--out",
                str(out_dir),
            ],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            print(result.stdout, end="")
            print(result.stderr, end="")
            return result.returncode

        aoa_path = out_dir / "material.additive.v1.atlas_of_atlases.json"
        lut_path = out_dir / "material.additive.v1.lut_rgba8.json"
        legacy_page_path = out_dir / "material.additive.v1.page0_rgba8.json"
        per_size_pages = [out_dir / f"material.additive.v1.page{px}_rgba8.json" for px in EXPECTED_SIZES]
        missing = [str(p.name) for p in (aoa_path, lut_path, legacy_page_path, *per_size_pages) if not p.exists()]
        if missing:
            print(f"FAIL: compile output missing files: {missing}")
            return 1

        aoa = json.loads(aoa_path.read_text(encoding="utf-8"))
        lut = json.loads(lut_path.read_text(encoding="utf-8"))
        legacy_page = json.loads(legacy_page_path.read_text(encoding="utf-8"))
        errors: list[str] = []
        if aoa.get("manifest_hash") != EXPECTED_HASH:
            errors.append(f"atlas manifest_hash does not match source manifest (got {aoa.get('manifest_hash')!r})")
        if aoa.get("fallback_glyph_id") != 539:
            errors.append("atlas fallback_glyph_id does not match manifest")
        if not aoa.get("font_id") or not aoa.get("font_sha256"):
            errors.append("AOA must carry font_id and font_sha256 in Phase 1+")
        if not aoa.get("lut_hash"):
            errors.append("AOA must carry lut_hash in Phase 1+")
        if aoa.get("glyph_index", {}).get("512") != [0, 0, 0, 16, 16]:
            errors.append("GlyphId 512 atlas rect is not first 16x16 cell")
        if aoa.get("glyph_index", {}).get("1879") != [0, 48, 160, 64, 176]:
            errors.append("GlyphId 1879 atlas rect is not stable")
        page_meta = {p["cell_px"]: p for p in aoa.get("pages", []) if "cell_px" in p}
        if sorted(page_meta) != EXPECTED_SIZES:
            errors.append(f"AOA pages cell_px set {sorted(page_meta)} does not match expected ladder {EXPECTED_SIZES}")
        for px, meta in page_meta.items():
            if "page_hash" not in meta:
                errors.append(f"page{px} entry missing page_hash")
            if meta.get("width_px") != ATLAS_COLS * px:
                errors.append(f"page{px} width_px {meta.get('width_px')} != {ATLAS_COLS*px}")
            if meta.get("height_px") != EXPECTED_ROWS * px:
                errors.append(f"page{px} height_px {meta.get('height_px')} != {EXPECTED_ROWS*px}")
        if lut.get("width") != 1624 or lut.get("height") != 1:
            errors.append("LUT dimensions must cover GlyphIds 256..1879")
        if lut.get("rgba8", [])[256 * 4 + 2] != 0:
            errors.append("GlyphId 512 LUT entry is not admitted")
        if lut.get("rgba8", [])[1623 * 4 + 2] != 0:
            errors.append("GlyphId 1879 LUT entry is not admitted")
        canonical_px = 16
        canonical_w = ATLAS_COLS * canonical_px
        canonical_h = EXPECTED_ROWS * canonical_px
        if legacy_page.get("width") != canonical_w or legacy_page.get("height") != canonical_h:
            errors.append(f"legacy page0 alias must mirror 16px canonical layout ({canonical_w}x{canonical_h}); got {legacy_page.get('width')}x{legacy_page.get('height')}")
        if len(legacy_page.get("rgba8", [])) != canonical_w * canonical_h * 4:
            errors.append("legacy page0 alias rgba8 payload length wrong")
        # Verify each per-size page's page_hash matches its actual rgba8 byte content
        for px in EXPECTED_SIZES:
            page = json.loads((out_dir / f"material.additive.v1.page{px}_rgba8.json").read_text(encoding="utf-8"))
            raw = bytes(page["rgba8"])
            actual_hash = hashlib.sha256(raw).hexdigest()
            if page.get("page_hash") != actual_hash:
                errors.append(f"page{px} page_hash does not match rgba8 byte digest")
            if page_meta.get(px, {}).get("page_hash") != actual_hash:
                errors.append(f"AOA page{px} page_hash does not match actual page bytes")
            if px == 16:
                nonempty, unique = count_nonempty_unique_alpha_masks(page)
                if nonempty != ENTRY_COUNT:
                    errors.append(f"canonical page16 has {nonempty}/{ENTRY_COUNT} non-empty glyph cells")
                if unique < MIN_UNIQUE_CANONICAL_GLYPH_MASKS:
                    errors.append(
                        f"canonical page16 has only {unique} unique glyph alpha masks; likely font fallback/tofu"
                    )
        tracked_aoa = TRACKED_OUT / aoa_path.name
        tracked_lut = TRACKED_OUT / lut_path.name
        for generated, tracked in ((aoa_path, tracked_aoa), (lut_path, tracked_lut)):
            if not tracked.exists():
                errors.append(f"tracked atlas artifact missing: {tracked.relative_to(REPO_ROOT)}")
            elif generated.read_bytes() != tracked.read_bytes():
                errors.append(f"tracked atlas artifact is stale: {tracked.relative_to(REPO_ROOT)}")
        for px in EXPECTED_SIZES:
            tracked = TRACKED_OUT / f"material.additive.v1.page{px}_rgba8.json"
            if not tracked.exists():
                errors.append(f"tracked page artifact missing: {tracked.relative_to(REPO_ROOT)}")
        if errors:
            for error in errors:
                print(f"FAIL: {error}")
            return 1

    print("PASS: FL-4131 glyph manifest compile emits atlas/LUT/page outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
