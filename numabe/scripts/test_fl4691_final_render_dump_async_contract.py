#!/usr/bin/env python3
"""Static ownership contract for the explicit final-render dump exporter."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"


def _function(source: str, name: str, next_name: str) -> str:
    start = source.index(f"func {name}")
    end = source.index(f"\nfunc {next_name}", start)
    return source[start:end]


def test_dump_has_one_async_artifact_writer_and_preserves_html_data() -> None:
    world = WORLD.read_text(encoding="utf-8")
    dump = _function(world, "_write_final_render_cell_dump", "_write_final_render_cell_dump_artifacts")
    worker = _function(world, "_write_final_render_cell_dump_artifacts", "_final_render_dump_html_prefix_suffix")

    assert "var export_thread := Thread.new()" in dump
    assert "_write_final_render_cell_dump_artifacts.bind(export_snapshot)" in dump
    assert '"final_image": final_image.duplicate()' in dump
    assert "while export_thread.is_alive():" in dump
    assert "await get_tree().process_frame" in dump
    assert "var export_result: Dictionary = export_thread.wait_to_finish()" in dump
    assert "_final_render_dump_html(metadata, cells)" not in dump

    # One per-row serialization feeds both companion artifacts. HTML keeps the
    # exact embedded data while JSONL retains the unescaped line contract.
    assert "var row_json := JSON.stringify(cells[cell_index])" in worker
    assert "cells_file.store_line(row_json)" in worker
    assert 'html_file.store_string(row_json.replace("</", "<\\\\/"))' in worker
    assert "final_image.save_png(final_png_path)" in worker
    assert "compositor methods" in worker


def test_final_paint_abi_is_resolved_once_for_the_immutable_capture() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    builder = _function(effect, "build_final_render_cell_rows", "_final_dump_cell_average")
    decoder = _function(effect, "_decode_final_cell_paint_row", "_fl4359_build_product_cell_join")

    assert "var final_cell_record_bytes := _final_cell_paint_record_bytes(final_cell_data)" in builder
    assert "final_cell_data, ci, final_cell_record_bytes" in builder
    assert "func _final_cell_paint_record_bytes(data: PackedByteArray) -> int:" in effect
    assert "data: PackedByteArray, cell_index: int, record_bytes: int = 0" in decoder
    assert "record_bytes = _final_cell_paint_record_bytes(data)" in decoder
