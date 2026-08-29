#!/usr/bin/env python3
"""Regression checks for FL-4131 material additive manifest completeness."""

from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"
COVERAGE = REPO_ROOT / "assets/glyphs/generated/extended_coverage_table.json"
MATERIAL_SIDECAR = REPO_ROOT / "assets/glyphs/fixtures/fl4131_material_sidecar_valid.a3d.glyph_profile.json"
ASCIIID = REPO_ROOT / "editor/asciiid.cpp"
WEB_HTML = REPO_ROOT / "web/game_web.html"
WEB_ATLAS = REPO_ROOT / "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json"
PROMISED_RANGE = range(544, 616)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _asciiid_label_ids() -> set[int]:
    text = ASCIIID.read_text(encoding="utf-8", errors="replace")
    labels_block = text.split("static const AsciiidExtendedGlyphLabel kAsciiidExtendedGlyphLabels[] =", 1)[1]
    labels_block = labels_block.split("};", 1)[0]
    return {int(match.group(1)) for match in re.finditer(r"\{\s*(\d+),\s*\"", labels_block)}


def _web_compiled_atlas_ids() -> set[int]:
    atlas = _json(WEB_ATLAS)
    return {int(gid) for gid in atlas["glyph_index"].keys()}


def _material_sidecar_ids() -> set[int]:
    sidecar = _json(MATERIAL_SIDECAR)
    ids: set[int] = set()
    for material in sidecar["material_entries"]:
        ids.update(cell["glyph_id"] for cell in material["cells"])
    return ids


def test_promised_material_gid_range_is_manifest_declared() -> None:
    manifest = _json(MANIFEST)
    declared = {entry["glyph_id"] for entry in manifest["entries"]}
    missing = [gid for gid in PROMISED_RANGE if gid not in declared]
    assert not missing, f"manifest missing promised material GIDs: {missing}"


def test_promised_material_gid_range_has_coverage_rows() -> None:
    coverage = _json(COVERAGE)
    covered = {entry["glyph_id"] for entry in coverage["entries"]}
    missing = [gid for gid in PROMISED_RANGE if gid not in covered]
    assert not missing, f"coverage table missing promised material GIDs: {missing}"


def test_promised_material_gid_range_has_asciiid_fallback_labels() -> None:
    labeled = _asciiid_label_ids()
    missing = [gid for gid in PROMISED_RANGE if gid not in labeled]
    assert not missing, f"ASCIIID fallback labels missing promised material GIDs: {missing}"


def test_promised_material_gid_range_is_bound_in_web_admitted_manifest() -> None:
    admitted = _web_compiled_atlas_ids()
    missing = [gid for gid in PROMISED_RANGE if gid not in admitted]
    assert not missing, f"web compiled atlas missing promised material GIDs: {missing}"
    text = WEB_HTML.read_text(encoding="utf-8", errors="replace")
    assert "material.additive.v1.atlas_of_atlases.json" in text
    assert "var admittedGlyphIds = [" not in text


def test_promised_material_gid_range_is_present_in_native_sidecar_fixture() -> None:
    sidecar_ids = _material_sidecar_ids()
    missing = [gid for gid in PROMISED_RANGE if gid not in sidecar_ids]
    assert not missing, f"native material sidecar fixture missing promised material GIDs: {missing}"


if __name__ == "__main__":
    test_promised_material_gid_range_is_manifest_declared()
    test_promised_material_gid_range_has_coverage_rows()
    test_promised_material_gid_range_has_asciiid_fallback_labels()
    test_promised_material_gid_range_is_bound_in_web_admitted_manifest()
    test_promised_material_gid_range_is_present_in_native_sidecar_fixture()
    print("FL-4131 material manifest completeness tests passed")
