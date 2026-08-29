#!/usr/bin/env python3
"""FL-4184 diagnostic gates over same-capture cells.jsonl output.

The canonical acceptance surface is the labeled June 14 clone/current PNG grid.
Accepted rows are same-capture diagnostics linked from that grid package, not a
replacement proof surface. Each gate consumes one or more companion dump
directories (.run/final_render_cell_dump/<stamp>/) and validates runtime facts
against the FL-4184 foliage architecture contract.

Exit codes:
  0 - all configured diagnostic gates PASS
  1 - one or more diagnostic gates FAIL (details on stderr)
  2 - input validation error
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]

# ── Gate registry ──────────────────────────────────────────────────────
GATE_ORDER = [
    "fl4184_dump_source_identity_current",
	"fl4184_fact_readback_complete",
	"fl4184_dylearn_masks_loaded",
	"fl4184_accepted_foliage_rows_present",
	"fl4184_no_blank_foliage_final_glyphs",
	"fl4184_foliage_grass_only",
    "fl4184_rejected_stone_sand_water_zero_foliage",
    "fl4184_flower_persists_four_yaws",
    "fl4184_panning_no_blank_terrain",
    "fl4184_multichar_displacement_rows_present",
    "fl4208_water_reserved_glyphs_water_only",
    "fl4184_visual_glyph_roles_cited",
]
ALL_GATES = set(GATE_ORDER)

# ── Helpers ────────────────────────────────────────────────────────────


def _annotated_desc(row: dict[str, Any]) -> str:
    """Compact human-readable cell annotation for error messages."""
    sc = row.get("screen_cell", {})
    return (
        f"cell({sc.get('x','?')},{sc.get('y','?')}) "
        f"mat_id={row.get('material_id')} "
        f"mat_fam={row.get('material_family')} "
        f"foliage.present={row.get('foliage.present')} "
        f"foliage.coverage={row.get('foliage.coverage')} "
        f"foliage.shape={row.get('foliage.shape')} "
        f"glyph_id={row.get('glyph_id')}"
    )


def _load_metadata(dump_dir: Path) -> dict[str, Any]:
    meta_path = dump_dir / "metadata.json"
    if not meta_path.is_file():
        raise ValueError(f"missing metadata.json in {dump_dir}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _load_cells(dump_dir: Path) -> list[dict[str, Any]]:
    cells_path = dump_dir / "cells.jsonl"
    if not cells_path.is_file():
        raise ValueError(f"missing cells.jsonl in {dump_dir}")
    rows: list[dict[str, Any]] = []
    with cells_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _current_git_head() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cannot resolve current git HEAD: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _foliage_mask_state_ge(state: str | None, min_state: str) -> bool:
    """Check if foliage_mask_state >= min_state in ordered progression.

    FL-4184 step #4 renamed the mask state strings so the binding 23-27
    Dylearn lifecycle no longer collides with the binding 28-31 readback
    "missing_gpu_fact_surface" state. The legacy names are accepted on
    input for backward compatibility with older dumps; comparison still
    uses the canonical order.
    """
    LEGACY_TO_CURRENT = {
        "missing_gpu_fact_surface": "missing_dylearn_mask_assets",
        "bound_unsampled": "loaded_bound_pending_sample",
    }
    ORDER = [
        "missing_dylearn_mask_assets",
        "loaded_unbound",
        "loaded_bound_pending_sample",
        "sampled",
    ]
    if state is None:
        return False
    canon_state = LEGACY_TO_CURRENT.get(state, state)
    canon_min = LEGACY_TO_CURRENT.get(min_state, min_state)
    try:
        return ORDER.index(canon_state) >= ORDER.index(canon_min)
    except ValueError:
        return False


def _fact_readback_is_ok(meta: dict[str, Any]) -> bool:
    state = meta.get("fact_readback_state", "")
    if state == "gpu_shader_readback_readback_ok":
        return True
    # Fallback: foliage_fact_state carries the raw state variable.
    raw = meta.get("foliage_fact_state", "")
    return raw == "readback_ok"


def _is_foliage_class(mat_fam: Any) -> bool:
    """Classification: foliage-or-grass-like material family."""
    if isinstance(mat_fam, str):
        ml = mat_fam.lower()
        return ml in ("foliage_or_grass_like", "grass", "foliage")
    if isinstance(mat_fam, (int, float)):
        return int(mat_fam) == 0
    return False


def _is_rejected_class(mat_fam: Any) -> bool:
    """Classification: stone/sand/water — should never have foliage.

    Accepts both string labels and numeric material_family IDs from the shader:
    0=grass, 1=dirt, 2=cliff/rock, 3=water.
    """
    if isinstance(mat_fam, str):
        ml = mat_fam.lower()
        return any(k in ml for k in ("stone", "sand", "water", "terrain_warm_like", "dark_or_empty_like"))
    if isinstance(mat_fam, (int, float)):
        return int(mat_fam) in (1, 2, 3, 4, 5, 140)
    return False


def _is_flower_shape(shape: Any) -> bool:
    if isinstance(shape, str):
        return shape.lower() == "flower"
    if isinstance(shape, (int, float)):
        return int(shape) >= 4
    return False


# ── Individual gate functions ──────────────────────────────────────────


ACCEPTED_FACT_SCHEMA_VERSIONS = {
    "v2_fl4208_expanded",
    "v4_fl4208_fl4216_water_sink",
    "v5_fl4208_fl4216_water_sink_x13",
}
GRASS_MATERIAL_IDS = {0, 1, 2}


def gate_dump_source_identity_current(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 0: X dump source identity must match current checkout."""
    fails: list[str] = []
    try:
        head = _current_git_head()
    except RuntimeError as e:
        return False, [str(e)]
    for dump_dir, meta, _cells in dumps:
        dump_head = str(meta.get("git_head", "")).strip()
        if not dump_head:
            fails.append(
                f"{dump_dir.name}: metadata git_head missing; rerun headed X dump"
            )
            continue
        if dump_head != head and not head.startswith(dump_head) and not dump_head.startswith(head):
            fails.append(
                f"{dump_dir.name}: stale runtime proof artifact "
                f"git_head={dump_head[:12]} current_head={head[:12]}; "
                "rerun headed X dump before interpreting foliage rows, "
                "glyph roles, lighting, particles, yaw, panning, displacement, "
                "and visual signoff"
            )
    return len(fails) == 0, fails


def gate_fact_readback_complete(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 1: GPU fact readback must be complete AND expanded.

    FL-4208 step #25: the metadata must carry both:
      - fact_readback_state == 'gpu_shader_readback_readback_ok'
      - fact_schema_version is a current accepted FL-4208/FL-4216 schema
    Pre-step-#6 dumps that lack fact_schema_version or carry the older
    schema fail closed. expanded_schema_partial and
    expanded_schema_missing readback states also fail Gate 1.
    """
    fails: list[str] = []
    for dump_dir, meta, _cells in dumps:
        state = meta.get("fact_readback_state", "")
        schema_version = meta.get("fact_schema_version", "")
        if not _fact_readback_is_ok(meta):
            fails.append(
                f"{dump_dir.name}: fact_readback_state={state!r} "
                f"(expected 'gpu_shader_readback_readback_ok'; "
                f"FL-4208 step #25 fails closed on "
                f"expanded_schema_partial / expanded_schema_missing)"
            )
            continue
        if schema_version not in ACCEPTED_FACT_SCHEMA_VERSIONS:
            fails.append(
                f"{dump_dir.name}: fact_schema_version={schema_version!r} "
                f"(expected one of {sorted(ACCEPTED_FACT_SCHEMA_VERSIONS)!r}; "
                f"pre-step-#6 dump or missing schema identifier)"
            )
    return len(fails) == 0, fails


def gate_dylearn_masks_loaded(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 2: Foliage Dylearn masks must be loaded, bound, AND sampled.

    Step #21 strengthens this gate: the metadata-level
    foliage_mask_state must be exactly "sampled", AND every accepted
    foliage row (foliage.present == 1) must report a numeric
    foliage.mask_state of 3 (FOLIAGE_MASK_STATE_SAMPLED). Anything
    below sampled means the Dylearn texture did not actually drive
    the silhouette; an accepted row in that state would be claiming
    Dylearn identity from procedural fallback data.

    Step #20 extends Gate 2 to enforce alpha-source attribution:
    every accepted foliage row must report foliage.alpha_source in
    the sampled-* family (2=sampled_grassleaf, 3=sampled_accentleaf,
    4=sampled_combined). 0 (missing) and 1 (procedural_debug) on
    an accepted row are fail-closed conditions — the procedural
    fallback was hard-deleted, so the only way for a present row to
    carry those values is an architecture regression. The runtime
    label string ("foliage.alpha_source_label") is checked alongside
    the numeric so dumps stay self-describing.
    """
    SAMPLED_NUMERIC = {2, 3, 4}
    SAMPLED_LABELS = {
        "sampled_grassleaf",
        "sampled_accentleaf",
        "sampled_combined",
    }
    fails: list[str] = []
    for dump_dir, meta, cells in dumps:
        state = meta.get("foliage_mask_state")
        if not _foliage_mask_state_ge(state, "sampled"):
            fails.append(
                f"{dump_dir.name}: foliage_mask_state={state!r} "
                f"(expected 'sampled'; lower states cannot back accepted "
                f"Dylearn-identity rows)"
            )
        bad_state_rows: list[str] = []
        bad_alpha_rows: list[str] = []
        for row in cells:
            if int(row.get("foliage.present", 0) or 0) != 1:
                continue
            row_state = row.get("foliage.mask_state")
            if not (isinstance(row_state, int) and row_state == 3):
                bad_state_rows.append(
                    f"{_annotated_desc(row)} foliage.mask_state={row_state!r}"
                )
            row_alpha = row.get("foliage.alpha_source")
            row_alpha_label = row.get("foliage.alpha_source_label")
            alpha_ok = (
                isinstance(row_alpha, int) and row_alpha in SAMPLED_NUMERIC
                and isinstance(row_alpha_label, str)
                and row_alpha_label in SAMPLED_LABELS
            )
            if not alpha_ok:
                bad_alpha_rows.append(
                    f"{_annotated_desc(row)} foliage.alpha_source="
                    f"{row_alpha!r}/{row_alpha_label!r}"
                )
        if bad_state_rows:
            fails.append(
                f"{dump_dir.name}: accepted foliage rows below "
                f"FOLIAGE_MASK_STATE_SAMPLED ({len(bad_state_rows)} row(s)) "
                f"— first: {bad_state_rows[0]}"
            )
        if bad_alpha_rows:
            fails.append(
                f"{dump_dir.name}: accepted foliage rows with non-sampled "
                f"alpha_source ({len(bad_alpha_rows)} row(s)) — "
                f"first: {bad_alpha_rows[0]}; the procedural fallback was "
                f"hard-deleted in step #20"
            )
    return len(fails) == 0, fails


def gate_accepted_foliage_rows_present(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 2.5: at least one sampled foliage diagnostic row must exist.

    The other foliage gates are mostly rejection and quality checks. They can
    all pass vacuously when every row reports foliage.present=0, which is not
    usable diagnostic evidence for FL-4184. The canonical acceptance surface is
    the multi-angle June 14 clone/current PNG grid at exact poses; accepted rows
    are same-capture diagnostics that must be driven by sampled Dylearn mask
    data. Active Baseline A means that June 14 clone comparator.
    """
    SAMPLED_NUMERIC = {2, 3, 4}
    SAMPLED_LABELS = {
        "sampled_grassleaf",
        "sampled_accentleaf",
        "sampled_combined",
    }
    fails: list[str] = []
    for dump_dir, _meta, cells in dumps:
        accepted = [
            row for row in cells
            if int(row.get("foliage.present", 0) or 0) == 1
        ]
        sampled = [
            row for row in accepted
            if row.get("foliage.alpha_source") in SAMPLED_NUMERIC
            and row.get("foliage.alpha_source_label") in SAMPLED_LABELS
            and row.get("foliage.coverage") is not None
            and float(row.get("foliage.coverage") or 0.0) > 0.0
        ]
        if not accepted:
            fails.append(
                f"{dump_dir.name}: no accepted foliage rows; all-zero "
                f"foliage.present dumps cannot support same-capture diagnostics"
            )
            continue
        if not sampled:
            fails.append(
                f"{dump_dir.name}: {len(accepted)} accepted foliage row(s), "
                f"but none have sampled mask alpha plus positive coverage"
            )
    return len(fails) == 0, fails


def gate_no_blank_foliage_final_glyphs(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 2.6: accepted product foliage rows must have visible final ink."""
    fails: list[str] = []
    for dump_dir, _meta, cells in dumps:
        bad_rows: list[str] = []
        for row in cells:
            pa_present = row.get("cell_owner_foliage_pa_present")
            legacy_present = row.get("foliage.present")
            if pa_present is not True and int(legacy_present or 0) != 1:
                continue
            glyph_id = int(row.get("glyph_id", 0) or 0)
            ink = row.get("final_glyph_ink", {})
            ink_pixels = int(ink.get("ink_pixels", 0) or 0) if isinstance(ink, dict) else 0
            glyph_blank = bool(ink.get("glyph_is_blank", False)) if isinstance(ink, dict) else False
            if glyph_id <= 32 or glyph_blank or ink_pixels <= 0:
                bad_rows.append(f"{_annotated_desc(row)} ink_pixels={ink_pixels} glyph_blank={glyph_blank}")
        if bad_rows:
            fails.append(
                f"{dump_dir.name}: {len(bad_rows)} foliage-present row(s) are blank or zero-ink; "
                f"first: {bad_rows[0]}"
            )
    return len(fails) == 0, fails


def gate_foliage_grass_only(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 3: foliage_present is allowed only on grass cells.

    Also enforces Item 14 fail conditions:
    - accepted foliage row has null material facts
    - accepted foliage row has null foliage facts

    When fact data is non-null and material_id is available:
      - y8 grass material ids 0/1/2 with grass family -> foliage_present may be 0/1
      - material id 4 and every non-grass material id -> foliage_present must be 0
    Null mat_id cells (actors, sprite, sky) are excluded.
    """
    fails: list[str] = []
    for dump_dir, _meta, cells in dumps:
        for row in cells:
            fp = row.get("foliage.present")
            mat_id = row.get("material_id")
            # Item 14: accepted foliage row has null material facts
            if fp == 1 and mat_id is None:
                fails.append(
                    f"{dump_dir.name}: {_annotated_desc(row)} — "
                    f"foliage present but material_id is null (Item 14 fail)"
                )
            # Item 14: accepted foliage row has null foliage facts
            if fp == 1:
                for fk in ("foliage.coverage", "foliage.shape", "foliage.effective_y"):
                    if row.get(fk) is None:
                        sc = row.get("screen_cell", {})
                        fails.append(
                            f"{dump_dir.name}: {_annotated_desc(row)} — "
                            f"foliage present but {fk} is null (Item 14 fail)"
                        )
                if row.get("water_state") is not None:
                    fails.append(
                        f"{dump_dir.name}: {_annotated_desc(row)} — "
                        "foliage present on a row with water_state"
                    )
            # Non-grass family rows must have zero foliage. Current y8 can
            # render grass-bearing shade rows through material ids 0/2, so
            # the shader-owned material_family fact is the placement gate.
            if mat_id is not None and fp is not None:
                mat_family = row.get("material_family")
                grass_row = int(mat_id) in GRASS_MATERIAL_IDS and _is_foliage_class(mat_family)
                if not grass_row and fp != 0:
                    fails.append(
                        f"{dump_dir.name}: {_annotated_desc(row)} — "
                        f"foliage_present={fp} on mat_id={mat_id} "
                        f"material_family={mat_family!r} (non-grass)"
                    )
    return len(fails) == 0, fails


def gate_rejected_stone_sand_water_zero_foliage(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 4: Stone/sand/water cells must have zero foliage.

    Cells classified as stone/sand/water (by material_family) must have
    foliage_present == 0. Checks both GPU fact and CPU-inferred families.
    """
    fails: list[str] = []
    for dump_dir, _meta, cells in dumps:
        for row in cells:
            fam = row.get("material_family")
            fp = row.get("foliage.present")
            if fam is None or fp is None:
                continue
            if _is_rejected_class(fam) and fp != 0:
                fails.append(
                    f"{dump_dir.name}: {_annotated_desc(row)} — "
                    f"foliage_present={fp} on rejected class {fam!r}"
                )
    return len(fails) == 0, fails


def gate_flower_persists_four_yaws(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 5: Flower-class rows are visible across all 4 cardinal yaws.

    Requires at least 4 dumps with distinct camera_yaw values
    (0, 90, 180, 270 degrees ≈ -0.0, 1.57, 3.14, -1.57 rad).
    Each yaw quadrant must show sampled, palette-backed flower rows.
    Exact screen-cell persistence is not valid under camera rotation because
    screen coordinates move with the view.
    """
    fails: list[str] = []
    if len(dumps) < 4:
        fails.append(
            f"need ≥4 dumps (got {len(dumps)}) for multi-yaw flower test — DELIBERATE FAIL, insufficient evidence"
        )
        return False, fails

    # Group dumps by yaw quadrant
    def _yaw_quadrant(rad: float) -> int:
        deg = rad * 180.0 / 3.14159
        if -45.0 <= deg < 45.0:
            return 0
        if 45.0 <= deg < 135.0:
            return 90
        if -135.0 <= deg < -45.0:
            return 270
        return 180

    quads: dict[int, list[dict[str, Any]]] = {}
    for _dd, meta, cells in dumps:
        yaw = meta.get("camera_yaw", 0.0)
        q = _yaw_quadrant(yaw)
        quads.setdefault(q, []).extend(cells)

    expected_quads = {0, 90, 180, 270}
    covered = set(quads.keys())
    missing = expected_quads - covered
    if missing:
        fails.append(f"missing yaw quadrants: {missing} (only have {covered})")
        return False, fails

    min_flower_rows_per_yaw = 20
    sampled_labels = {
        "sampled_grassleaf",
        "sampled_accentleaf",
        "sampled_combined",
    }
    for q, cells in quads.items():
        flower_rows = [
            row for row in cells
            if int(row.get("foliage.present", 0) or 0) == 1
            and _is_flower_shape(row.get("foliage.shape"))
        ]
        if len(flower_rows) < min_flower_rows_per_yaw:
            fails.append(
                f"yaw {q}: only {len(flower_rows)} flower rows "
                f"(need >= {min_flower_rows_per_yaw})"
            )
            continue
        sampled_rows = [
            row for row in flower_rows
            if row.get("foliage.alpha_source_label") in sampled_labels
        ]
        if len(sampled_rows) != len(flower_rows):
            fails.append(
                f"yaw {q}: sampled flower rows {len(sampled_rows)}/{len(flower_rows)}"
            )
        palette_matched = [
            row for row in flower_rows
            if row.get("foliage.palette_glyph_id") == row.get("glyph_id")
            and row.get("foliage.rejected_combo_status") == "accepted"
        ]
        if len(palette_matched) != len(flower_rows):
            fails.append(
                f"yaw {q}: palette-matched flower rows "
                f"{len(palette_matched)}/{len(flower_rows)}"
            )
        water_overlap = [
            row for row in flower_rows
            if row.get("water_state") is not None
        ]
        if water_overlap:
            fails.append(
                f"yaw {q}: {len(water_overlap)} flower rows overlap water_state"
            )
        flower_glyphs = {
            int(row.get("glyph_id"))
            for row in flower_rows
            if row.get("glyph_id") is not None
        }
        if len(flower_glyphs) < 2:
            fails.append(
                f"yaw {q}: flower glyph variety too low: {sorted(flower_glyphs)}"
            )

    return len(fails) == 0, fails


def gate_panning_no_blank_terrain(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 6: No blank terrain cells when panning.

    Panning should not reveal ANSI cells with null material_id that
    are actually terrain (based on pixel position). Requires ≥2 dumps
    at different camera positions. Each dump checks that terrain-representing
    cells (based on avg_rgb, not fact data) have non-null material_id
    or at least a meaningful glyph_char.
    """
    fails: list[str] = []
    if len(dumps) < 2:
        fails.append(
            f"need ≥2 dumps (got {len(dumps)}) for panning test — DELIBERATE FAIL, insufficient evidence"
        )
        return False, fails

    for dump_dir, _meta, cells in dumps:
        blank_count = 0
        blank_examples: list[str] = []
        for row in cells:
            mat_id = row.get("material_id")
            glyph_char = row.get("glyph_char", " ")
            # A "blank terrain" cell: no GPU fact AND a space/empty glyph
            # AND the avg color suggests it's not sky/outside FOV
            if mat_id is None and glyph_char in (" ", "."):
                avg = row.get("avg_rgb", {})
                luma = avg.get("r", 0) * 0.2126 + avg.get("g", 0) * 0.7152 + avg.get("b", 0) * 0.0722
                # Non-black non-white suggests terrain but no fact
                if 0.02 < luma < 0.98:
                    blank_count += 1
                    if blank_count <= 5:
                        sc = row.get("screen_cell", {})
                        blank_examples.append(
                            f"  cell({sc.get('x','?')},{sc.get('y','?')}) "
                            f"luma={luma:.3f} glyph={glyph_char!r}"
                        )
        if blank_count > 10:
            examples = "\n".join(blank_examples)
            fails.append(
                f"{dump_dir.name}: {blank_count} blank terrain cells "
                f"(first {len(blank_examples)}):\n{examples}"
            )

    return len(fails) == 0, fails


def gate_multichar_displacement_rows_present(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 7: Multi-character displacement rows are present.

    FL-4184 / FL-4208 step #5: the dump must carry the displacement
    accounting fields on every accepted foliage row. At least one dump
    must show displacement_count >= 1 (single-actor pose) and the
    multi-actor proof requires displacement_count >= 2 across the dump
    set. source_mask must be non-zero whenever count > 0 so each
    contributing actor's source-kind bit is observable.
    """
    fails: list[str] = []
    accepted_rows_seen = 0
    rows_missing_count = 0
    rows_missing_source_mask = 0
    rows_missing_dropped = 0
    max_count_seen = 0
    aggregated_source_mask = 0
    for _dd, _meta, cells in dumps:
        for row in cells:
            if int(row.get("foliage.present", 0) or 0) != 1:
                continue
            accepted_rows_seen += 1
            count_v = row.get("foliage.displacement_count")
            mask_v = row.get("foliage.displacement_source_mask")
            dropped_v = row.get("foliage.displacement_dropped_count")
            if count_v is None:
                rows_missing_count += 1
            else:
                ci = int(count_v)
                if ci > max_count_seen:
                    max_count_seen = ci
            if mask_v is None:
                rows_missing_source_mask += 1
            else:
                aggregated_source_mask |= int(mask_v)
            if dropped_v is None:
                rows_missing_dropped += 1
    if accepted_rows_seen == 0:
        # No accepted foliage rows to inspect; FL-4184 Gate 1 (the
        # readback gate) already fails closed for empty-foliage dumps.
        # Gate 7 stays silent so it does not double-fail.
        return True, []
    if rows_missing_count or rows_missing_source_mask or rows_missing_dropped:
        fails.append(
            f"accepted foliage rows missing displacement fields: "
            f"count={rows_missing_count} mask={rows_missing_source_mask} "
            f"dropped={rows_missing_dropped}"
        )
    if max_count_seen < 1:
        fails.append(
            "no accepted foliage row carries displacement_count >= 1; "
            "expected at least the local player to drive single-actor push"
        )
    if aggregated_source_mask == 0 and max_count_seen >= 1:
        fails.append(
            "displacement_count >= 1 but source_mask aggregated == 0; "
            "each contributing actor must light at least one source-kind bit"
        )
    return len(fails) == 0, fails


def gate_water_reserved_glyphs_water_only(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate: water-reserved extended glyph IDs 512..519 are water-only.

    FL-4216 reserves 512..519 for the compositor water package. Terrain
    material glyph variety must not reuse those slots, because the atlas shapes
    read visually as water/underwater markers on grass, dirt, and cliff rows.
    """
    fails: list[str] = []
    water_reserved = set(range(512, 520))
    bad: list[str] = []
    for dump_dir, _meta, cells in dumps:
        for row in cells:
            glyph_id = row.get("glyph_id")
            if glyph_id is None:
                continue
            try:
                gid = int(glyph_id)
            except (TypeError, ValueError):
                continue
            if gid not in water_reserved:
                continue
            if row.get("water_state") is not None:
                continue
            bad.append(
                f"{dump_dir.name}: {_annotated_desc(row)} "
                f"cell.side={row.get('cell.side')!r} glyph_char={row.get('glyph_char')!r}"
            )
            if len(bad) >= 20:
                break
        if len(bad) >= 20:
            break
    if bad:
        fails.append(
            "water-reserved glyph IDs 512..519 appeared on non-water rows:\n"
            + "\n".join(bad)
        )
    return len(fails) == 0, fails


def gate_visual_glyph_roles_cited(
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
) -> tuple[bool, list[str]]:
    """Gate 8: Cited glyph roles match visible PNG content.

    FL-4208 step #7 plant-role glyph checks live inline here. The deleted
    FL-4208 validator must not be imported as a second proof owner.
    Human visual signoff remains required.
    """
    fails: list[str] = []
    for dump_dir, _meta, cells in dumps:
        png_path = dump_dir / "final.png"
        # FL-4208 plant-role glyphs (katakana stems + arabic flowers/leaves/tips):
        #   Stem sparse: 559(to), 556(no), 561(ri), 560(i)
        #   Stem medium: 562(so), 563(tsu), 600(a), 601(u)
        #   Stem dense: 604(ka), 605(ki), 612(se), 613(ta)
        #   Stem legacy: 616(alef), 617(beh)
        #   Tip: 623(dal), 624(reh), 625(zain)
        #   Leaf left: 626(seen), Leaf right: 627(sheen)
        #   Flower: 620(jeem), 622(khah), 628(sad), 630(tah), 647(waw), 629(dad)
        #   Flower accent: 576, 571, 579, 581
        #   Legacy CP437 stems: 20-25(DC1-DC5), 124(pipe), 43(plus)
        #   Legacy CP437 flowers: 42(star), 15(sun), 249-251, 248(degree)
        #   Legacy CP437 tips: 94(caret), 118(v), 39(apostrophe)
        KNOWN_PLANT_GLYPHS: set[int] = set([
            556, 559, 560, 561, 562, 563,  # katakana stem sparse-medium
            600, 601, 604, 605, 612, 613,   # katakana stem medium-dense
            616, 617,                        # arabic stem legacy
            620, 622, 628, 630, 647, 629,   # arabic flower
            571, 576, 579, 581,              # flower accent
            623, 624, 625,                   # arabic tip
            626, 627,                        # arabic leaf
        ] + list(range(20, 26)) + [124, 43, 42, 15, 249, 250, 251, 248, 94, 118, 39, 44, 46, 96])
        role_display_chars: dict[str, set[str]] = {
            "flower": {"F"},
            "flower_head": {"F"},
            "leaf_left": {"<"},
            "leaf_right": {">"},
            "tip": {"^"},
            "accent": {"A"},
            "stem": {"|"},
        }
        seen = 0
        mismatches: list[str] = []
        palette_misses: list[str] = []
        for row in cells:
            if seen >= 20:
                break
            fp = row.get("foliage.present")
            glyph_id = row.get("glyph_id")
            shape = row.get("foliage.shape")
            if fp == 1 and glyph_id is not None:
                seen += 1
                palette_entry = row.get("foliage.palette_entry_id")
                palette_status = row.get("foliage.rejected_combo_status")
                palette_glyph = row.get("foliage.palette_glyph_id")
                if palette_entry is None or palette_status != "accepted" or palette_glyph is None:
                    palette_misses.append(
                        f"  {_annotated_desc(row)} — palette_entry_id="
                        f"{palette_entry!r} rejected_combo_status={palette_status!r} "
                        f"palette_glyph_id={palette_glyph!r}"
                    )
                if _is_flower_shape(shape) and glyph_id not in KNOWN_PLANT_GLYPHS:
                    mismatches.append(
                        f"  {_annotated_desc(row)} — glyph {glyph_id} "
                        f"not in known plant-role set"
                    )
                role = str(row.get("foliage.role", ""))
                expected_chars = role_display_chars.get(role, set())
                if expected_chars and row.get("glyph_char") not in expected_chars:
                    mismatches.append(
                        f"  {_annotated_desc(row)} — glyph_char={row.get('glyph_char')!r} "
                        f"does not expose role marker {sorted(expected_chars)}"
                    )

        png_exists = png_path.is_file()
        if not png_exists:
            fails.append(f"{dump_dir.name}: final.png missing — cannot do visual comparison")
        if palette_misses:
            fails.append(
                f"{dump_dir.name}: {len(palette_misses)} accepted foliage row(s) "
                f"lack accepted FL-4208 palette attribution:\n"
                + "\n".join(palette_misses[:15])
            )
        elif mismatches:
            fails.append(
                f"{dump_dir.name}: {len(mismatches)} glyph role mismatches "
                f"(first {len(mismatches)}):\n" + "\n".join(mismatches)
            )

    if not fails:
        fails.append("glyph roles match known plant set — PASS (requires visual signoff)")

    # Fail until human visual signoff is recorded. The soft-pass path
    # was removed because non-human gate passes cannot verify visible
    # glyph morphology at gameplay scale.
    return False, fails

# ── Main ───────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> tuple[set[str], list[Path]]:
    """Parse CLI args: optional gate names and dump dirs."""
    gates: set[str] = set()
    dirs: list[Path] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ALL_GATES:
            gates.add(arg)
        elif arg == "--all":
            gates = set(ALL_GATES)
        elif arg == "--help":
            print(__doc__)
            print(f"\nGates: {', '.join(sorted(ALL_GATES))}")
            print(f"\nUsage: {SCRIPT.name} [--all | GATE ...] [DUMP_DIR ...]")
            print("  If no gates specified, runs all that have data.")
            print("  If no dump dirs, uses latest in .run/final_render_cell_dump/.")
            raise SystemExit(0)
        else:
            p = Path(arg)
            if p.is_dir():
                dirs.append(p.resolve())
            else:
                print(f"error: unrecognized arg or missing directory: {arg}", file=sys.stderr)
                raise SystemExit(2)
        i += 1

    # Default: all gates, latest dump
    if not gates:
        gates = set(ALL_GATES)
    if not dirs:
        base = ROOT / ".run" / "final_render_cell_dump"
        if base.is_dir():
            candidates = [p for p in base.iterdir() if p.is_dir()]
            if candidates:
                dirs = [max(candidates, key=lambda p: p.stat().st_mtime)]
        if not dirs:
            print("error: no dump dirs found (run headed X-dump first)", file=sys.stderr)
            raise SystemExit(2)
    return gates, dirs


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    gates, dump_dirs = parse_args(argv)

    # Load data from each dump dir
    dumps: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]] = []
    for dd in dump_dirs:
        try:
            meta = _load_metadata(dd)
            cells = _load_cells(dd)
            dumps.append((dd, meta, cells))
        except (ValueError, json.JSONDecodeError) as e:
            print(f"warning: skipping {dd.name}: {e}", file=sys.stderr)
            continue

    if not dumps:
        print("error: no valid dump dirs loaded", file=sys.stderr)
        return 2

    print(f"Loaded {len(dumps)} dump dir(s), {sum(len(c) for _,_,c in dumps)} total rows")
    if len(dumps) == 1:
        meta = dumps[0][1]
        print(f"  foliage_mask_state={meta.get('foliage_mask_state')}")
        print(f"  fact_readback_state={meta.get('fact_readback_state')}")
        print(f"  yaw={meta.get('camera_yaw'):.4f}")
    else:
        for dd, meta, cells in dumps:
            print(f"  {dd.name}: yaw={meta.get('camera_yaw', 0):.4f} cells={len(cells)}")

    print()

    # Run gates in canonical order
    gate_map = {
        "fl4184_dump_source_identity_current": gate_dump_source_identity_current,
        "fl4184_fact_readback_complete": gate_fact_readback_complete,
        "fl4184_dylearn_masks_loaded": gate_dylearn_masks_loaded,
        "fl4184_accepted_foliage_rows_present": gate_accepted_foliage_rows_present,
        "fl4184_foliage_grass_only": gate_foliage_grass_only,
        "fl4184_rejected_stone_sand_water_zero_foliage": gate_rejected_stone_sand_water_zero_foliage,
        "fl4184_flower_persists_four_yaws": gate_flower_persists_four_yaws,
        "fl4184_panning_no_blank_terrain": gate_panning_no_blank_terrain,
        "fl4184_multichar_displacement_rows_present": gate_multichar_displacement_rows_present,
        "fl4208_water_reserved_glyphs_water_only": gate_water_reserved_glyphs_water_only,
        "fl4184_visual_glyph_roles_cited": gate_visual_glyph_roles_cited,
    }

    any_fail = False
    for gname in GATE_ORDER:
        if gname not in gates:
            continue
        fn = gate_map[gname]
        ok, errs = fn(dumps)
        status = "PASS" if ok else "FAIL"
        if not ok:
            any_fail = True
        print(f"[{status}] {gname}: {len(errs)} failure(s)")
        for e in errs:
            # Indent multi-line messages
            for line in e.split("\n"):
                print(f"  | {line}")

    print()
    if any_fail:
        print("One or more gates FAILED — see above")
        return 1
    print("FL4184_FOLIAGE_GATES_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
