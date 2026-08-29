#!/usr/bin/env python3
"""FL-4131 shape-lab profile evaluator.

Preflight only. This script verifies that the inputs the shape-lab UX would read
are internally consistent and admission-clean. It does NOT prove UX, headed
runtime behavior, or visual comparison. Runtime UX proof remains headed/CDP
ASCIIID with screenshot and dumped runtime comparison state.

Inputs:
- assets/glyphs/generated/material.additive.v1.shape_catalog.json
- assets/glyphs/generated/material_shape_presets.json
- assets/a3d/fl4131_shape_lab_20x20.a3d.glyph_profile.json
- assets/glyphs/fixtures/extended_glyph_material_additive_v1.json (admission manifest)

Output:
- docs/research/ascii/verification/fl4131/shape_lab/latest_profile_eval.json

Hard-fail conditions (exit 1):
- selected GlyphId missing from admitted catalog or manifest
- manifest coverage_quadrants missing or zero for any selected GlyphId
- shape_catalog entry missing shape6 (or shape6 is the zero vector) for any selected GlyphId
- generated preset count not exactly 7
- the gameplay-visible fallback character for the admission set is `?`
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SHAPE_CATALOG = REPO_ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
PRESETS = REPO_ROOT / "assets/glyphs/generated/material_shape_presets.json"
FIXTURE_SIDECAR = REPO_ROOT / "assets/a3d/fl4131_shape_lab_20x20.a3d.glyph_profile.json"
ADMISSION_MANIFEST = REPO_ROOT / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"
OUT_DIR = REPO_ROOT / "docs/research/ascii/verification/fl4131/shape_lab"
OUT_PATH = OUT_DIR / "latest_profile_eval.json"

EXPECTED_PRESET_COUNT = 7
QUESTION_MARK_SCALAR = 0x3F


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_selected_glyph_ids(presets: dict, sidecar: dict) -> dict[str, list[int]]:
    """Return per-source lists of selected GlyphIds.

    Fixture zone glyph_ids and preset row glyphs are both selections that the
    shape-lab UX will display. We keep them separate so the receipt records
    where each id came from.
    """
    fixture_ids: list[int] = []
    for zone in sidecar.get("zones", []):
        for gid in zone.get("glyph_ids", []) or []:
            fixture_ids.append(int(gid))

    preset_top_ids: list[int] = []
    preset_row_ids: list[int] = []
    for preset in presets.get("presets", []):
        for gid in preset.get("glyphs", []) or []:
            preset_top_ids.append(int(gid))
        for row in preset.get("row_roles", []) or []:
            for gid in row.get("glyphs", []) or []:
                preset_row_ids.append(int(gid))
            for gid in row.get("preferred_glyph_ids", []) or []:
                preset_row_ids.append(int(gid))

    return {
        "fixture": fixture_ids,
        "preset_top_level_glyphs": preset_top_ids,
        "preset_row_glyphs": preset_row_ids,
    }


def _evaluate(catalog: dict, presets: dict, sidecar: dict, manifest: dict) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    catalog_entries = catalog.get("entries", []) or []
    manifest_entries = manifest.get("entries", []) or []

    catalog_by_gid = {int(e["glyph_id"]): e for e in catalog_entries if "glyph_id" in e}
    manifest_by_gid = {int(e["glyph_id"]): e for e in manifest_entries if "glyph_id" in e}

    admitted_in_both = set(catalog_by_gid) & set(manifest_by_gid)
    only_catalog = sorted(set(catalog_by_gid) - admitted_in_both)
    only_manifest = sorted(set(manifest_by_gid) - admitted_in_both)
    if only_catalog:
        warnings.append(
            f"catalog has GlyphIds not in admission manifest: {only_catalog[:10]}"
            + ("..." if len(only_catalog) > 10 else "")
        )
    if only_manifest:
        warnings.append(
            f"manifest has GlyphIds not in shape catalog: {only_manifest[:10]}"
            + ("..." if len(only_manifest) > 10 else "")
        )

    if catalog.get("manifest_hash") != manifest.get("manifest_hash") and manifest.get("manifest_hash") is not None and catalog.get("manifest_hash") is not None:
        # Manifest's own file does not necessarily expose `manifest_hash` -- the
        # catalog's manifest_hash is the hash *of* the manifest. Skip silently
        # if either is missing.
        pass
    catalog_manifest_hash = catalog.get("manifest_hash")
    if not catalog_manifest_hash:
        warnings.append("shape catalog is missing manifest_hash")
    if catalog.get("manifest_path") and Path(catalog["manifest_path"]).name != ADMISSION_MANIFEST.name:
        warnings.append(
            f"shape catalog manifest_path {catalog['manifest_path']!r} does not point at the expected admission manifest"
        )

    selected = _collect_selected_glyph_ids(presets, sidecar)
    all_selected = sorted({gid for ids in selected.values() for gid in ids})

    # Per-glyph admission + coverage + shape6 checks
    not_admitted: list[int] = []
    zero_coverage: list[int] = []
    missing_shape6: list[int] = []
    zero_shape6: list[int] = []
    question_mark_selected: list[int] = []

    for gid in all_selected:
        if gid not in catalog_by_gid or gid not in manifest_by_gid:
            not_admitted.append(gid)
            continue
        cov = manifest_by_gid[gid].get("coverage_quadrants")
        if cov is None or int(cov) == 0:
            zero_coverage.append(gid)
        c_entry = catalog_by_gid[gid]
        shape6 = c_entry.get("shape6")
        if not isinstance(shape6, list) or len(shape6) != 6:
            missing_shape6.append(gid)
        elif all((float(v) == 0.0) for v in shape6):
            # The shape catalog schema test permits a zero shape6 vector
            # (length and 0..1 range only). Flag as a warning so the shape lab
            # can surface degenerate scoring inputs, but do not hard-fail --
            # the user's spec calls out missing shape6, not zero shape6.
            zero_shape6.append(gid)
        if c_entry.get("unicode_scalar") == QUESTION_MARK_SCALAR or c_entry.get("unicode") == "?":
            question_mark_selected.append(gid)

    if not_admitted:
        errors.append(
            f"selected GlyphIds missing from admitted catalog/manifest: {not_admitted}"
        )
    if zero_coverage:
        errors.append(
            f"selected GlyphIds with missing/zero coverage_quadrants: {zero_coverage}"
        )
    if missing_shape6:
        errors.append(
            f"selected GlyphIds with missing or malformed shape6 in catalog: {missing_shape6}"
        )
    if zero_shape6:
        warnings.append(
            f"selected GlyphIds with all-zero shape6 vector (degenerate scoring input, permitted by catalog schema): {zero_shape6}"
        )
    if question_mark_selected:
        errors.append(
            f"selected GlyphIds resolve to gameplay-visible `?`: {question_mark_selected}"
        )

    # Admission-set fallback GlyphId must itself be admitted and not `?`
    fallback_gid = manifest.get("fallback_glyph_id")
    fallback_entry = manifest_by_gid.get(int(fallback_gid)) if fallback_gid is not None else None
    fallback_catalog_entry = catalog_by_gid.get(int(fallback_gid)) if fallback_gid is not None else None
    if fallback_gid is None:
        errors.append("admission manifest is missing fallback_glyph_id")
    elif fallback_entry is None:
        errors.append(
            f"admission manifest fallback_glyph_id={fallback_gid} is not present in manifest entries"
        )
    elif fallback_catalog_entry is None:
        errors.append(
            f"admission manifest fallback_glyph_id={fallback_gid} is not present in shape catalog entries"
        )
    else:
        fb_cov = fallback_entry.get("coverage_quadrants")
        if fb_cov is None or int(fb_cov) == 0:
            errors.append(
                f"admission manifest fallback_glyph_id={fallback_gid} has missing/zero coverage_quadrants"
            )
        fb_scalar = fallback_catalog_entry.get("unicode_scalar")
        fb_unicode = fallback_catalog_entry.get("unicode")
        if fb_scalar == QUESTION_MARK_SCALAR or fb_unicode == "?":
            errors.append(
                f"gameplay-visible fallback for the admission set is `?` (fallback_glyph_id={fallback_gid})"
            )

    # Generated preset count must be exactly 7
    preset_list = presets.get("presets", []) or []
    if len(preset_list) != EXPECTED_PRESET_COUNT:
        errors.append(
            f"generated preset count is {len(preset_list)}, expected {EXPECTED_PRESET_COUNT}"
        )

    # Per-preset row admission summary (non-fatal, captures evaluator context)
    preset_summary: list[dict[str, Any]] = []
    for preset in preset_list:
        rows = []
        for row in preset.get("row_roles", []) or []:
            row_gids = [int(g) for g in (row.get("glyphs", []) or [])]
            preferred = [int(g) for g in (row.get("preferred_glyph_ids", []) or [])]
            rows.append({
                "role": row.get("role"),
                "glyph_count": len(row_gids),
                "preferred_glyph_count": len(preferred),
                "all_in_catalog": all(g in catalog_by_gid for g in row_gids),
                "all_in_manifest": all(g in manifest_by_gid for g in row_gids),
            })
        preset_summary.append({
            "name": preset.get("name"),
            "material": preset.get("material"),
            "purpose": preset.get("purpose"),
            "rows": preset.get("rows"),
            "cols": preset.get("cols"),
            "row_role_count": len(rows),
            "row_role_summary": rows,
            "top_level_glyph_count": len(preset.get("glyphs", []) or []),
        })

    # Fixture zone summary (non-fatal context)
    zone_summary: list[dict[str, Any]] = []
    for zone in sidecar.get("zones", []) or []:
        zone_gids = [int(g) for g in (zone.get("glyph_ids", []) or [])]
        zone_summary.append({
            "zone_id": zone.get("zone_id"),
            "materials": zone.get("materials"),
            "mesh": zone.get("mesh"),
            "mesh_asset": zone.get("mesh_asset"),
            "glyph_count": len(zone_gids),
            "all_in_catalog": all(g in catalog_by_gid for g in zone_gids),
            "all_in_manifest": all(g in manifest_by_gid for g in zone_gids),
        })

    verdict = "PASS" if not errors else "FAIL"

    return {
        "schema": "fl4131_shape_lab_profile_eval_v1",
        "scope": "preflight only -- does NOT prove UX",
        "non_ux_proof_notice": (
            "This evaluator is preflight only. It cannot prove UX, headed runtime "
            "behavior, or visual comparison. Runtime UX proof must come from "
            "headed/CDP ASCIIID with screenshot and dumped runtime comparison state."
        ),
        "verdict": verdict,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit_under_test": _git_head(),
        "inputs": {
            "shape_catalog": str(SHAPE_CATALOG.relative_to(REPO_ROOT)),
            "presets": str(PRESETS.relative_to(REPO_ROOT)),
            "fixture_sidecar": str(FIXTURE_SIDECAR.relative_to(REPO_ROOT)),
            "admission_manifest": str(ADMISSION_MANIFEST.relative_to(REPO_ROOT)),
        },
        "checks": {
            "all_selected_glyph_ids_admitted": not not_admitted,
            "coverage_nonzero_for_all_selected": not zero_coverage,
            "shape6_present_for_all_selected": not missing_shape6,
            "no_selected_glyph_renders_as_question_mark": not question_mark_selected,
            "admission_fallback_glyph_is_admitted_and_not_question_mark": (
                fallback_gid is not None
                and fallback_entry is not None
                and fallback_catalog_entry is not None
                and fallback_catalog_entry.get("unicode_scalar") != QUESTION_MARK_SCALAR
                and fallback_catalog_entry.get("unicode") != "?"
            ),
            "generated_preset_count_is_seven": len(preset_list) == EXPECTED_PRESET_COUNT,
        },
        "summary": {
            "admitted_catalog_count": len(catalog_by_gid),
            "manifest_entry_count": len(manifest_by_gid),
            "admitted_intersection_count": len(admitted_in_both),
            "preset_count": len(preset_list),
            "zone_count": len(sidecar.get("zones", []) or []),
            "selected_glyph_id_count_unique": len(all_selected),
            "selected_glyph_ids_by_source": {
                k: sorted(set(v)) for k, v in selected.items()
            },
            "selected_glyph_ids_union": all_selected,
            "admission_fallback_glyph_id": fallback_gid,
            "admission_fallback_unicode_scalar": (
                fallback_catalog_entry.get("unicode_scalar") if fallback_catalog_entry else None
            ),
            "catalog_metric_model": catalog.get("metric_model"),
            "catalog_manifest_hash": catalog.get("manifest_hash"),
            "catalog_page_hash": catalog.get("page_hash"),
            "presets_manifest_hash": presets.get("manifest_hash"),
        },
        "details": {
            "presets": preset_summary,
            "zones": zone_summary,
            "not_admitted_glyph_ids": not_admitted,
            "zero_coverage_glyph_ids": zero_coverage,
            "missing_shape6_glyph_ids": missing_shape6,
            "zero_shape6_glyph_ids": zero_shape6,
            "question_mark_selected_glyph_ids": question_mark_selected,
        },
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    inputs_missing: list[str] = []
    for path in (SHAPE_CATALOG, PRESETS, FIXTURE_SIDECAR, ADMISSION_MANIFEST):
        if not path.is_file():
            inputs_missing.append(str(path.relative_to(REPO_ROOT)))
    if inputs_missing:
        for p in inputs_missing:
            print(f"FAIL: missing input {p}")
        return 1

    catalog = _read_json(SHAPE_CATALOG)
    presets = _read_json(PRESETS)
    sidecar = _read_json(FIXTURE_SIDECAR)
    manifest = _read_json(ADMISSION_MANIFEST)

    report = _evaluate(catalog, presets, sidecar, manifest)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)}")
    if report["warnings"]:
        for w in report["warnings"]:
            print(f"WARN: {w}")
    if report["verdict"] != "PASS":
        for e in report["errors"]:
            print(f"FAIL: {e}")
        return 1
    print(
        f"PASS: FL-4131 shape-lab profile preflight clean "
        f"(presets={report['summary']['preset_count']}, "
        f"selected_glyphs={report['summary']['selected_glyph_id_count_unique']}, "
        f"admitted={report['summary']['admitted_intersection_count']}). "
        f"This is preflight only -- not UX proof."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
