#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
README = ROOT / "docs" / "audits" / "2026-06-17-fl4231-fl4184-git-line-audit" / "README.md"
VALIDATOR = ROOT / "scripts" / "validate_godot_visual_grid_canon.py"


def test_world_writes_same_capture_companion_package() -> None:
    src = EFFECT.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")

    required = [
        "func build_final_render_cell_rows(final_image: Image) -> Array:",
        '"row_type": "exact_final_cell"',
        '"readback_state": "final_cell"',
        '"glyph_id": int(data.decode_u32(base + 8))',
        '"glyph_char": _glyph_resolved["char"]',
        '"fg_rgb": final_paint_row.get("fg_rgb", null)',
        '"bg_rgb": final_paint_row.get("bg_rgb", null)',
        '"ink_pixels": ink_px',
        '"total_pixels": total_px',
    ]
    for needle in required:
        assert needle in src, needle

    world_required = [
        'has_method("build_final_render_cell_rows")',
        'var cells: Array = _ascii_effect.call("build_final_render_cell_rows", final_image)',
        'var cells_path := abs_dir.path_join("cells.jsonl")',
        'var metadata_path := abs_dir.path_join("metadata.json")',
        'var index_path := abs_dir.path_join("index.html")',
        'var final_png_path := abs_dir.path_join("final.png")',
        'metadata["dump_contract"] = "exact_final_ansi_cell_buffer"',
        "var artifact_paths := {",
        "A current-runtime dump cannot choose its own comparison baseline",
        "await RenderingServer.frame_post_draw",
    ]
    for needle in world_required:
        assert needle in world, needle

    forbidden = [
        '"row_type": "final_paint_cell"',
        '"readback_state": "final_paint_cell"',
        'metadata["dump_contract"] = "final_paint_cell_buffer_at_x_press"',
        "FINAL_PAINT_CELL_BUFFER_DUMP",
        '"row_type": "final_framebuffer_cell"',
        '"source": "viewport_final_image"',
        '"dump_surface_contract": "facts_and_final_samples_not_single_painted_truth"',
        'metadata["baseline_ref"]',
    ]
    for needle in forbidden:
        assert needle not in src, needle
        assert needle not in world, needle


def test_exact_final_rows_count_only_authoritative_owner_proof() -> None:
    """Guard the exact-row branch against silent owner-count and provenance drift."""
    world = WORLD.read_text(encoding="utf-8")
    branch_start = world.index(
        'if String(row.get("row_type", "")) == "exact_final_cell" '
        'and String(row.get("readback_state", "")) == "final_cell":'
    )
    branch_end = world.index(
        '\n\t\t\tcontinue',
        branch_start,
    )
    exact_branch = world[branch_start:branch_end]

    assert 'row.get("owner_product_surface", null)' in exact_branch
    assert '"proves_final_dispatch_branch", false' in exact_branch
    assert 'counts["owner_product_fact_cells"]' in exact_branch
    assert '_final_render_dump_row_has_selected_payload_identity(row)' in exact_branch

    # A descriptive owner surface is not a Product fact. The positive count
    # must be guarded by the producer-owned proof bit rather than by dict shape.
    assert 'if owner_surface is Dictionary:' in exact_branch or 'exact_owner_surface is Dictionary' in exact_branch
    assert 'and bool((exact_owner_surface as Dictionary).get(' in exact_branch

    # The final payload identity contract remains strict and cannot be made
    # valid by helper colours or a quarter-indexed record.
    identity_start = world.index(
        'func _final_render_dump_row_has_selected_payload_identity',
    )
    identity_end = world.index(
        '\nfunc _final_render_dump_certification',
        identity_start,
    )
    identity = world[identity_start:identity_end]
    assert '"selected_cell_product_payload"' in identity
    assert '"proof_flags"' in identity
    assert '"readback_state", "")) == "final_cell_paint_readback_ok"' in identity
    assert '"fg_rgb"' in identity and '"bg_rgb"' in identity
    assert 'center_rgb' not in identity
    assert 'avg_rgb' not in identity


def test_product_payload_join_uses_ansi_cells_and_authoritative_b55_fields() -> None:
    """Reject quarter-index joins, display-helper colours, and null-as-valid payloads."""
    src = EFFECT.read_text(encoding="utf-8")

    # The outer row is one ANSI cell. The four b55 records are nested inside
    # that row; treating a quarter record as the row key joins the wrong owner.
    assert 'var fact_idx := product_y * ansi_size.x + x' in src
    assert 'var capture_final_index := capture_cell_index * 4 + capture_slot' in src
    assert 'capture_product_cell_rows[capture_cell_index] = (' in src
    assert 'var product_cell_join: Dictionary = _fl4359_build_product_cell_join(' in src
    assert 'capture_product_quarters, fact_idx' in src
    assert '"final_index_base": cell_index * 4' in src
    assert '"final_index_count": 4' in src
    assert '"slot_order": [0, 1, 2, 3]' in src
    assert 'func _fl4359_product_status_is_valid(status: int) -> bool:' in src
    assert '(status & FINAL_PRODUCT_STATUS_VALID) != 0' in src
    assert '(status & FINAL_PRODUCT_STATUS_ABI_MASK) == FINAL_PRODUCT_STATUS_ABI_V2' in src
    assert '(status & ~FINAL_PRODUCT_STATUS_KNOWN_MASK) == 0' in src

    payload_start = src.index('var selected_cell_product_payload: Dictionary = {')
    payload_end = src.index('\n\t\t\tvar final_mask_measurement', payload_start)
    payload = src[payload_start:payload_end]

    # These values must remain explicit four-quarter Product facts. A scalar
    # owner, slot-0 owner, averaged colour, or final-image helper is invalid.
    assert '"schema": "fl4359.product_cell_quarters.v1"' in payload
    assert '"quarter_values": product_cell_join.get("owner_kind_quarters", [])' in payload
    assert '"stable_surface_ids": product_cell_join.get(' in payload
    assert '"cell_presentation": product_cell_join.get("cell_presentation", null)' in payload
    assert '"quarter_values": product_cell_join.get("glyph_id_quarters", [])' in payload
    assert '"quarter_values": product_cell_join.get(' in payload
    assert '"matcell_packed": {' in payload
    assert '"quarter_values": product_cell_join.get("fg_rgb_quarters", [])' in payload
    assert '"quarter_values": product_cell_join.get("bg_rgb_quarters", [])' in payload
    assert '"quarter_values": product_cell_join.get("anchor_xyz_quarters", [])' in payload
    assert '"quarter_values": product_cell_join.get("world_depth_quarters", [])' in payload
    assert '"source_key"' not in payload
    assert '"product_cell_mask_agreement": product_join_ok' in payload
    assert '"product_quarter_count": capture_product_quarters.size()' in payload
    assert 'product_cell_join.get("quarters", [])' in payload
    assert 'product_cell_join.get("owner_kind_quarters", [])[0]' not in payload
    assert 'product_cell_join.get("fg_rgb_quarters", [])[0]' not in payload
    assert 'avg_color' not in payload
    assert 'cell_owner_paint_fg_rgb' not in payload
    assert 'cell_owner_paint_bg_rgb' not in payload
    assert 'final_paint_row.get("fg_rgb"' not in payload
    assert 'final_paint_row.get("bg_rgb"' not in payload
    assert '"readback_state": cell_readback_state' in payload

    # The join must fail closed for missing, malformed, misindexed, invalid
    # quarter records rather than making nulls look authoritative; the elected
    # cell presentation is decoded once from the consumed NativePresentationResult
    # bank (FL-4848), never from a quarter copy.
    assert 'func _fl4359_build_product_cell_join(' in src
    assert 'if quarters.size() != 4:' in src
    assert 'product_quarter_index_mismatch' in src
    assert 'product_quarter_status_invalid' in src
    assert 'cell_presentation_record_missing' in src
    assert 'product_quarter_field_missing' in src
    assert 'product_quarter_anchor_invalid' in src
    assert 'quarter.get("final_index", cell_index * 4 + slot)' in src
    assert '_fl4359_decode_cell_presentation_record(presentation, record_base)' in src
    assert 'cell_owner_product_b55_status_word' not in src

    # No compatibility scalar buffer or old retired-readback state may be
    # reintroduced as a producer or accepted proof state.
    assert 'var cell_product_rows: Array = []' not in src
    assert 'cell_owner_product_readback_retired' not in src


def test_png_grid_is_canonical_review_surface() -> None:
    readme = README.read_text(encoding="utf-8")
    required = [
        "Canonical evidence package going forward: the labeled clone/current PNG grid is the human-visible review surface.",
        "same-pose clone/current PNG grid is the canonical human review surface",
        "Exact final rendered ANSI-cell rows are same-capture companion diagnostics only",
        "That PNG grid is the canonical acceptance surface.",
        "they do not replace the PNG grid and do not independently close the lane",
    ]
    for needle in required:
        assert needle in readme, needle

    forbidden = [
        "grid alone is not closure",
        "final-cell rows proving",
        "rows prove shared",
        "standalone HTML dump is closure",
        "Baseline A is the current comparator",
    ]
    for needle in forbidden:
        assert needle not in readme, needle


def test_visual_grid_canon_validator_passes_repo() -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GODOT_VISUAL_GRID_CANON_OK" in result.stdout


def test_visual_grid_canon_validator_blocks_standalone_dump_claims() -> None:
    bad = "A standalone final_render_cell_dump index.html is the canonical proof surface for foliage closure."
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--stdin-content", "--label", "bad.md"],
        cwd=ROOT,
        input=bad,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 1
    assert "GODOT_VISUAL_GRID_CANON_FAIL" in result.stdout


if __name__ == "__main__":
    test_world_writes_same_capture_companion_package()
    test_png_grid_is_canonical_review_surface()
    test_visual_grid_canon_validator_passes_repo()
    test_visual_grid_canon_validator_blocks_standalone_dump_claims()
    print("FL4231_DUMP_CELL_OWNER_CONTRACT_TESTS_OK")
