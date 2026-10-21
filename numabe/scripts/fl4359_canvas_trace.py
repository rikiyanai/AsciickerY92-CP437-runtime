"""Offline FL-4359 Canvas provenance validator and replay.

This module consumes the existing NativeFrameObserver receipt and its raw
sidecars.  It does not infer draw order from GPU append order, rerender the
Canvas, or use scene-tree bounds as pixel ownership.  The fragment ABI and
blend laws mirror the pinned observer reader:

* fragment records are 48-byte little-endian records;
* source records are 104-byte little-endian records;
* authoritative order is ``pixel, pass_id, draw_ordinal``;
* blend factors are materialized as float32 before arithmetic and each draw
  is quantized back to the target RGBA8 representation.

Validation uses bounded memory maps for raw sidecars and row chunks for the
PNG/base comparison.  The compact per-pixel result is three arrays rather
than a surface-sized Python dictionary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from PIL import Image


FORMAT_RGBA8_UNORM = 36
FORMAT_RGBA8_SRGB = 42
FRAGMENT_STRIDE = 48
SOURCE_STRIDE = 104
MAX_WIDTH = 3600
MAX_HEIGHT = 2252
MAX_RECORDS = 262_144
MAX_SOURCE_RECORDS = 4_096
MAX_SIDECAR_BYTES = 1 << 30
DEFAULT_ROW_CHUNK = 128
SCHEMA = "fl4691.canvas_provenance.v2"
SUPPORTED_SCHEMAS = {SCHEMA, "fl4691.canvas_provenance.v3"}
SURFACE_BOUNDARY = "viewport_after_canvas_layers"
CANVAS_BOUNDARY = "canvas_same_pass_fragment_capture"
BLEND_SENTINEL = 0xFFFFFFFF
SUPPORTED_BLEND_MODES = frozenset((0, 1, 2, 3, 5, 6))


FRAGMENT_DTYPE = np.dtype(
    {
        "names": ["r", "g", "b", "a", "pixel", "source_key", "draw", "pass_id", "blend"],
        "formats": ["<f4", "<f4", "<f4", "<f4", "<u4", "<u4", "<u4", "<u4", "<u4"],
        "offsets": [0, 4, 8, 12, 16, 24, 28, 32, 36],
        "itemsize": FRAGMENT_STRIDE,
    }
)
SOURCE_DTYPE = np.dtype(
    {
        "names": [
            "source_key", "draw", "pass_id", "command_type", "canvas_item_rid",
            "material_rid", "texture_rid", "sampled_texture_rid", "command_flags", "blend",
        ],
        "formats": ["<u4", "<u4", "<u4", "<u4", "<u8", "<u8", "<u8", "<u8", "<u4", "<u4"],
        "offsets": [0, 4, 8, 12, 16, 24, 32, 40, 96, 100],
        "itemsize": SOURCE_STRIDE,
    }
)


class CanvasTraceError(ValueError):
    """Raised when an input receipt or sidecar cannot be trusted."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(reason: str) -> CanvasTraceError:
    return CanvasTraceError(reason)


def _int(value: Any, label: str, *, positive: bool = False, nonnegative: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{label}_not_integral")
    if isinstance(value, int):
        # JSON integers are exact Python integers. Do not route RIDs or other
        # uint64 fields through float, where values above 2**53 lose identity.
        result = value
    else:
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            raise _fail(f"{label}_not_integral")
        if abs(number) > float(1 << 53):
            raise _fail(f"{label}_precision_loss")
        result = int(number)
    if positive and result <= 0:
        raise _fail(f"{label}_not_positive")
    if nonnegative and result < 0:
        raise _fail(f"{label}_negative")
    return result


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise _fail(f"{label}_invalid_sha256")
    return value


def _resolve_path(value: Any, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise _fail(f"{label}_missing")
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def _meta_bytes(meta: Any, path_root: Path, label: str, *, max_bytes: int) -> tuple[Path, int]:
    if not isinstance(meta, Mapping) or meta.get("status") != "ok":
        raise _fail(f"{label}_not_present")
    path = _resolve_path(meta.get("path"), path_root, f"{label}_path")
    declared = _int(meta.get("bytes"), f"{label}_bytes", nonnegative=True)
    if declared > max_bytes or declared > MAX_SIDECAR_BYTES:
        raise _fail(f"{label}_bytes_out_of_bounds")
    if not path.is_file():
        raise _fail(f"{label}_file_missing")
    actual = path.stat().st_size
    if actual != declared:
        raise _fail(f"{label}_length_mismatch")
    expected_sha = _sha(meta.get("sha256"), f"{label}_sha256")
    if sha256_file(path) != expected_sha:
        raise _fail(f"{label}_sha256_mismatch")
    return path, declared


def _memmap(path: Path, dtype: np.dtype[Any], count: int) -> np.ndarray:
    if count == 0:
        return np.empty((0,), dtype=dtype)
    return np.memmap(path, dtype=dtype, mode="r", shape=(count,))


def _inventory_canvas_ids(frame: Mapping[str, Any]) -> set[int]:
    inventory = frame.get("source_inventory")
    if not isinstance(inventory, Mapping):
        raise _fail("source_inventory_missing")
    result: set[int] = set()
    for key in ("controls", "canvas_items"):
        rows = inventory.get(key, [])
        if not isinstance(rows, list):
            raise _fail(f"source_inventory_{key}_invalid")
        for row in rows:
            if not isinstance(row, Mapping):
                raise _fail("source_inventory_row_invalid")
            result.add(_int(row.get("canvas_item_rid"), "source_inventory_canvas_item_rid", nonnegative=True))
    return result


def _validate_target(frame: Mapping[str, Any], width: int, height: int) -> tuple[dict[str, Any], bool]:
    provenance = frame.get("canvas_provenance")
    if not isinstance(provenance, Mapping):
        raise _fail("canvas_provenance_missing")
    if provenance.get("status") != "ok":
        raise _fail("canvas_provenance_status_not_ok")
    if provenance.get("schema") not in SUPPORTED_SCHEMAS:
        raise _fail("canvas_provenance_schema_unsupported")
    if provenance.get("surface_boundary") != CANVAS_BOUNDARY:
        raise _fail("canvas_surface_boundary_mismatch")
    if bool(provenance.get("overflow", True)):
        raise _fail("canvas_provenance_overflow")
    if provenance.get("ordering") != "pass_id_then_draw_ordinal; gpu_atomic_append_order_is_not_authoritative":
        raise _fail("canvas_ordering_authority_missing")
    if provenance.get("source_blend_authority") != "fragment_record_blend_mode":
        raise _fail("canvas_blend_authority_missing")
    if provenance.get("same_draw_ordinal_records") != "same_source_sample_set_without_painter_order_claim":
        raise _fail("canvas_same_draw_order_policy_missing")
    target = provenance.get("target_info")
    if not isinstance(target, Mapping):
        raise _fail("canvas_target_info_missing")
    target = dict(target)
    if provenance.get("schema") == "fl4691.canvas_provenance.v3":
        _int(target.get("texture_rid"), "canvas_target_texture_rid", positive=True)
    if _int(target.get("width"), "canvas_target_width", positive=True) != width or _int(
        target.get("height"), "canvas_target_height", positive=True
    ) != height:
        raise _fail("canvas_target_extent_mismatch")
    fmt = _int(target.get("format"), "canvas_target_format", nonnegative=True)
    srgb = target.get("srgb")
    if not isinstance(srgb, bool):
        raise _fail("canvas_target_srgb_invalid")
    if fmt not in (FORMAT_RGBA8_UNORM, FORMAT_RGBA8_SRGB):
        raise _fail("canvas_target_format_unsupported")
    if (fmt == FORMAT_RGBA8_SRGB) != srgb:
        raise _fail("canvas_target_format_transfer_mismatch")
    for key, expected in (("depth", 1), ("array_layers", 1), ("mipmaps", 1), ("framebuffer_samples", 0), ("texture_samples", 0)):
        if _int(target.get(key), f"canvas_target_{key}", nonnegative=True) != expected:
            raise _fail(f"canvas_target_{key}_unsupported")
    if target.get("load_action") not in ("load", "clear"):
        raise _fail("canvas_target_load_action_unsupported")
    if target.get("conversion_supported") is not True:
        raise _fail("canvas_target_conversion_unsupported")
    if target.get("load_action") == "load" and target.get("base_capture_supported") is not True:
        raise _fail("canvas_base_capture_unsupported")
    if bool(target.get("clear_requested")) != (target.get("load_action") == "clear"):
        raise _fail("canvas_clear_load_action_mismatch")
    if bool(target.get("clear_requested")):
        clear = target.get("clear_color_rgba")
        if not isinstance(clear, list) or len(clear) != 4 or not np.isfinite(np.asarray(clear, dtype=np.float64)).all():
            raise _fail("canvas_clear_color_invalid")
    return dict(provenance), srgb


def _validate_dispatch(frame: Mapping[str, Any], provenance: Mapping[str, Any]) -> None:
    dispatch = frame.get("dispatch")
    if not isinstance(dispatch, Mapping):
        raise _fail("canvas_dispatch_missing")
    token = _int(frame.get("canvas_request_token"), "canvas_request_token", positive=True)
    target = _int(frame.get("canvas_provenance_target"), "canvas_provenance_target", positive=True)
    render_frame = _int(frame.get("dispatch_canvas_render_frame_id"), "dispatch_canvas_render_frame_id", nonnegative=True)
    if _int(provenance.get("request_id"), "canvas_request_id", positive=True) != token:
        raise _fail("canvas_dispatch_request_token_mismatch")
    if _int(dispatch.get("canvas_request_token"), "dispatch_canvas_request_token", positive=True) != token:
        raise _fail("canvas_dispatch_request_token_mismatch")
    if _int(provenance.get("render_target"), "canvas_render_target", positive=True) != target:
        raise _fail("canvas_dispatch_target_mismatch")
    if _int(dispatch.get("canvas_render_target_id"), "dispatch_canvas_render_target_id", positive=True) != target:
        raise _fail("canvas_dispatch_target_mismatch")
    if _int(provenance.get("render_frame_id"), "canvas_render_frame_id", nonnegative=True) != render_frame:
        raise _fail("canvas_dispatch_render_frame_mismatch")
    if _int(dispatch.get("canvas_render_frame_id"), "dispatch_canvas_render_frame_id", nonnegative=True) != render_frame:
        raise _fail("canvas_dispatch_render_frame_mismatch")
    if _int(provenance.get("frame_id"), "canvas_frame_id", nonnegative=True) != render_frame:
        raise _fail("canvas_engine_frame_id_mismatch")
    if _int(dispatch.get("frame_index"), "dispatch_frame_index", nonnegative=True) != _int(
        frame.get("post_draw_frame"), "post_draw_frame", nonnegative=True
    ):
        raise _fail("canvas_post_draw_frame_mismatch")
    if _int(frame.get("sequence_frame"), "sequence_frame", nonnegative=True) != _int(
        dispatch.get("sequence_frame"), "dispatch_sequence_frame", nonnegative=True
    ):
        raise _fail("canvas_sequence_frame_mismatch")


def _read_png(path: Path, expected_sha: Any, width: int, height: int) -> Image.Image:
    expected = _sha(expected_sha, "final_png_sha256")
    if not path.is_file() or sha256_file(path) != expected:
        raise _fail("final_png_sha256_mismatch")
    try:
        image = Image.open(path)
        # Inspect the PNG header before decompression. A hostile or stale
        # header must not allocate an extent unrelated to the receipt.
        if image.format != "PNG" or image.mode != "RGBA" or image.size != (width, height):
            image.close()
            raise _fail("final_png_format_or_extent_invalid")
        image.load()
    except Exception as exc:  # pragma: no cover - Pillow-specific parser errors
        if isinstance(exc, CanvasTraceError):
            raise
        raise _fail("final_png_decode_failed") from exc
    return image


def _raw_binding(
    raw_path: Path | None,
    image: Image.Image,
    width: int,
    height: int,
    row_chunk: int,
) -> tuple[np.memmap | None, str]:
    if raw_path is None:
        digest = hashlib.sha256()
        for y in range(0, height, DEFAULT_ROW_CHUNK):
            chunk = np.asarray(image.crop((0, y, width, min(height, y + DEFAULT_ROW_CHUNK))), dtype=np.uint8)
            digest.update(chunk.tobytes())
        return None, digest.hexdigest()
    if not raw_path.is_file() or raw_path.stat().st_size != width * height * 4:
        raise _fail("actual_raw_payload_length_mismatch")
    raw = np.memmap(raw_path, dtype=np.uint8, mode="r", shape=(height, width, 4))
    for y in range(0, height, row_chunk):
        png_chunk = _row_chunk(image, y, min(height, y + row_chunk), width)
        if not np.array_equal(png_chunk, raw[y : min(height, y + row_chunk)]):
            raise _fail("actual_raw_payload_mismatch")
    return raw, sha256_file(raw_path)


def _validate_sidecars(
    frame: Mapping[str, Any], provenance: Mapping[str, Any], width: int, height: int, root: Path
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    sidecars = provenance.get("binary_sidecars")
    if not isinstance(sidecars, Mapping):
        raise _fail("canvas_sidecars_missing")
    record_count = _int(provenance.get("record_count"), "canvas_record_count", nonnegative=True)
    if record_count > MAX_RECORDS:
        raise _fail("canvas_record_count_out_of_bounds")
    fragment_path, fragment_bytes = _meta_bytes(
        sidecars.get("fragments"), root, "canvas_fragments", max_bytes=MAX_RECORDS * FRAGMENT_STRIDE
    )
    if fragment_bytes != record_count * FRAGMENT_STRIDE:
        raise _fail("canvas_fragment_count_or_stride_mismatch")
    source_path, source_bytes = _meta_bytes(
        sidecars.get("sources"), root, "canvas_sources", max_bytes=MAX_SOURCE_RECORDS * SOURCE_STRIDE
    )
    if source_bytes % SOURCE_STRIDE:
        raise _fail("canvas_source_stride_mismatch")
    source_count = source_bytes // SOURCE_STRIDE
    if source_count > MAX_SOURCE_RECORDS or (record_count and source_count == 0):
        raise _fail("canvas_source_count_out_of_bounds")
    fragments = _memmap(fragment_path, FRAGMENT_DTYPE, record_count)
    sources = _memmap(source_path, SOURCE_DTYPE, source_count)
    base: np.ndarray | None = None
    if provenance["target_info"].get("load_action") == "load":
        base_path, base_bytes = _meta_bytes(sidecars.get("pre_canvas_base"), root, "pre_canvas_base", max_bytes=width * height * 4)
        if base_bytes != width * height * 4:
            raise _fail("pre_canvas_base_size_mismatch")
        base = np.memmap(base_path, dtype=np.uint8, mode="r", shape=(height, width, 4))
    elif sidecars.get("pre_canvas_base", {}).get("status") != "absent":
        raise _fail("clear_action_base_sidecar_unexpected")
    return fragments, sources, base


def _validate_records(
    fragments: np.ndarray, sources: np.ndarray, frame: Mapping[str, Any], width: int, height: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[dict[str, int]]]:
    if len(sources):
        if np.any(sources["source_key"][1:] <= sources["source_key"][:-1]):
            raise _fail("duplicate_or_unsorted_canvas_source_key")
    keys = fragments["source_key"]
    source_indices = np.searchsorted(sources["source_key"], keys)
    if len(keys) and np.any(source_indices >= len(sources)):
        raise _fail("canvas_fragment_source_key_missing")
    if len(keys) and np.any(sources["source_key"][source_indices] != keys):
        raise _fail("canvas_fragment_source_key_missing")
    colors = np.column_stack((fragments["r"], fragments["g"], fragments["b"], fragments["a"]))
    if not np.isfinite(colors).all():
        raise _fail("canvas_fragment_non_finite_color")
    if len(keys) and np.any(fragments["pixel"] >= width * height):
        raise _fail("canvas_fragment_pixel_out_of_bounds")
    if len(keys) and (
        np.any(sources["draw"][source_indices] != fragments["draw"])
        or np.any(sources["pass_id"][source_indices] != fragments["pass_id"])
    ):
        raise _fail("canvas_fragment_source_order_mismatch")
    source_blend = sources["blend"][source_indices] if len(keys) else np.empty((0,), dtype=np.uint32)
    if len(keys) and np.any((source_blend != BLEND_SENTINEL) & (source_blend != fragments["blend"])):
        raise _fail("canvas_fragment_source_blend_mismatch")
    inventory_ids = _inventory_canvas_ids(frame)
    if any(int(value) not in inventory_ids for value in sources["canvas_item_rid"]):
        raise _fail("source_canvas_item_rid_not_in_inventory")
    order = np.lexsort((fragments["draw"], fragments["pass_id"], fragments["pixel"]))
    ordered_pixels = fragments["pixel"][order]
    ordered_passes = fragments["pass_id"][order]
    ordered_draws = fragments["draw"][order]
    if len(order) > 1:
        duplicate = (
            (ordered_pixels[1:] == ordered_pixels[:-1])
            & (ordered_passes[1:] == ordered_passes[:-1])
            & (ordered_draws[1:] == ordered_draws[:-1])
        )
        if np.any(duplicate):
            raise _fail("same_pixel_draw_order_ambiguous")
    if len(order):
        starts = np.r_[0, np.flatnonzero(ordered_pixels[1:] != ordered_pixels[:-1]) + 1].astype(np.int64)
        pixels = ordered_pixels[starts].astype(np.int64, copy=False)
        offsets = np.r_[starts, len(order)].astype(np.int64)
    else:
        pixels = np.empty((0,), dtype=np.int64)
        offsets = np.asarray([0], dtype=np.int64)
    touched_source_indices = np.unique(source_indices) if len(source_indices) else np.empty((0,), dtype=np.int64)
    touched_source_indices = touched_source_indices[np.argsort(sources["draw"][touched_source_indices], kind="stable")]
    source_rows: list[dict[str, int]] = []
    for index in touched_source_indices:
        row = sources[int(index)]
        source_rows.append(
            {
                "source_key": int(row["source_key"]),
                "draw_ordinal": int(row["draw"]),
                "pass_id": int(row["pass_id"]),
                "command_type": int(row["command_type"]),
                "canvas_item_rid": int(row["canvas_item_rid"]),
                "material_rid": int(row["material_rid"]),
                "texture_rid": int(row["texture_rid"]),
                "sampled_texture_rid": int(row["sampled_texture_rid"]),
                "command_flags": int(row["command_flags"]),
                "blend_mode": int(row["blend"]),
            }
        )
    return order, pixels, offsets, source_indices, source_rows


def _f32(value: Any) -> np.float32:
    return np.float32(value)


def _decode_rgba(encoded: np.ndarray, srgb: bool) -> np.ndarray:
    value = np.asarray(encoded, dtype=np.float32) / _f32(255.0)
    value = value.astype(np.float32, copy=False)
    if srgb:
        value[:3] = _srgb_to_linear(value[:3])
    return value


def _encode_rgba(value: np.ndarray, srgb: bool) -> np.ndarray:
    value = np.asarray(value, dtype=np.float32).copy()
    if srgb:
        value[:3] = _linear_to_srgb(value[:3])
    # Godot Color stores the result as float32, then roundi(value * 255.0)
    # performs the scalar multiplication after promotion to double. Keeping
    # the multiplication in float32 changes exact half-way UNORM outcomes.
    value = np.clip(value, _f32(0.0), _f32(1.0)).astype(np.float32)
    scaled = value.astype(np.float64) * 255.0
    return np.floor(scaled + 0.5).astype(np.uint8)


def _quantize(value: np.ndarray, srgb: bool) -> np.ndarray:
    return _decode_rgba(_encode_rgba(value, srgb), srgb)


def _linear_to_srgb(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=np.float32)
    return np.where(
        value <= _f32(0.0031308),
        value * _f32(12.92),
        _f32(1.055) * np.power(value, _f32(1.0 / 2.4), dtype=np.float32) - _f32(0.055),
    ).astype(np.float32)


def _srgb_to_linear(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=np.float32)
    return np.where(
        value <= _f32(0.04045),
        value / _f32(12.92),
        np.power((value + _f32(0.055)) / _f32(1.055), _f32(2.4), dtype=np.float32),
    ).astype(np.float32)


def _blend(dst: np.ndarray, src: np.ndarray, blend_mode: int) -> np.ndarray:
    if blend_mode not in SUPPORTED_BLEND_MODES:
        raise _fail(f"unsupported_blend_mode_{blend_mode}")
    src = np.asarray(src, dtype=np.float32)
    dst = np.asarray(dst, dtype=np.float32)
    src_alpha = _f32(src[3])
    inverse_alpha = _f32(_f32(1.0) - src_alpha)
    src_rgb = src[:3].astype(np.float32, copy=False)
    dst_rgb = dst[:3].astype(np.float32, copy=False)
    if blend_mode == 0:
        rgb = np.add(np.multiply(src_rgb, src_alpha, dtype=np.float32), np.multiply(dst_rgb, inverse_alpha, dtype=np.float32), dtype=np.float32)
        alpha = _f32(_f32(src_alpha) + _f32(_f32(dst[3]) * inverse_alpha))
    elif blend_mode == 1:
        rgb = np.add(np.multiply(src_rgb, src_alpha, dtype=np.float32), dst_rgb, dtype=np.float32)
        alpha = _f32(_f32(src_alpha * src_alpha) + _f32(dst[3]))
    elif blend_mode == 2:
        rgb = np.subtract(dst_rgb, np.multiply(src_rgb, src_alpha, dtype=np.float32), dtype=np.float32)
        alpha = _f32(_f32(dst[3]) - _f32(src_alpha * src_alpha))
    elif blend_mode == 3:
        rgb = np.multiply(src_rgb, dst_rgb, dtype=np.float32)
        alpha = _f32(src_alpha * _f32(dst[3]))
    elif blend_mode == 5:
        rgb = np.add(src_rgb, np.multiply(dst_rgb, inverse_alpha, dtype=np.float32), dtype=np.float32)
        alpha = _f32(_f32(src_alpha) + _f32(_f32(dst[3]) * inverse_alpha))
    else:  # replace
        return src.copy()
    return np.asarray([rgb[0], rgb[1], rgb[2], alpha], dtype=np.float32)


def _row_chunk(image: Image.Image, y0: int, y1: int, width: int) -> np.ndarray:
    return np.asarray(image.crop((0, y0, width, y1)), dtype=np.uint8).copy().reshape((y1 - y0, width, 4))


def _replay_rows(
    image: Image.Image,
    base: np.ndarray | None,
    fragments: np.ndarray,
    order: np.ndarray,
    pixels: np.ndarray,
    offsets: np.ndarray,
    width: int,
    height: int,
    srgb: bool,
    clear_color: Sequence[float] | None,
    row_chunk: int,
    raw: np.ndarray | None,
) -> dict[str, Any]:
    expected_clear = None
    if clear_color is not None:
        expected_clear = _encode_rgba(np.asarray(clear_color, dtype=np.float32), srgb)
    mismatched = 0
    max_error = 0
    first_mismatch: list[int] = []
    samples: list[dict[str, int]] = []
    histogram: dict[str, int] = {}
    for y0 in range(0, height, row_chunk):
        y1 = min(height, y0 + row_chunk)
        actual = _row_chunk(image, y0, y1, width)
        if raw is not None and not np.array_equal(actual, raw[y0:y1]):
            raise _fail("actual_raw_payload_mismatch")
        if base is None:
            expected = np.empty_like(actual)
            expected[:] = expected_clear
        else:
            expected = np.asarray(base[y0:y1], dtype=np.uint8).copy()
        first_group = int(np.searchsorted(pixels, y0 * width, side="left"))
        last_group = int(np.searchsorted(pixels, y1 * width, side="left"))
        for group in range(first_group, last_group):
            pixel = int(pixels[group])
            local_y, x = divmod(pixel, width)
            value = _decode_rgba(expected[local_y - y0, x], srgb)
            for ordered_index in order[int(offsets[group]) : int(offsets[group + 1])]:
                record = fragments[int(ordered_index)]
                source = np.asarray([record["r"], record["g"], record["b"], record["a"]], dtype=np.float32)
                value = _quantize(_blend(value, source, int(record["blend"])), srgb)
            expected[local_y - y0, x] = _encode_rgba(value, srgb)
        difference = expected.astype(np.int16) - actual.astype(np.int16)
        pixel_mask = np.any(difference != 0, axis=2)
        mismatch_count = int(pixel_mask.sum())
        mismatched += mismatch_count
        if mismatch_count:
            locations = np.argwhere(pixel_mask)
            if not first_mismatch:
                ly, lx = locations[0]
                first_mismatch = [int(lx), int(y0 + ly)]
            for ly, lx in locations[: max(0, 16 - len(samples))]:
                for channel in range(4):
                    error = int(difference[ly, lx, channel])
                    if error:
                        if len(samples) < 16:
                            samples.append({"x": int(lx), "y": int(y0 + ly), "channel": channel, "expected": int(expected[ly, lx, channel]), "actual": int(actual[ly, lx, channel])})
                        max_error = max(max_error, abs(error))
        if mismatch_count == 0:
            continue
        max_error = max(max_error, int(np.abs(difference).max()))
        for channel in range(4):
            for error, count in zip(*np.unique(difference[:, :, channel], return_counts=True)):
                if int(error) != 0:
                    key = f"{channel}:{int(error)}"
                    histogram[key] = histogram.get(key, 0) + int(count)
    return {
        "exact": mismatched == 0,
        "mismatched_pixels": mismatched,
        "max_channel_error": max_error,
        "first_mismatch": first_mismatch,
        "mismatch_samples": samples,
        "signed_error_histogram": histogram,
    }


def _failure(reason: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "reason": reason,
        "exact": False,
        "mismatched_pixels": 0,
        "max_channel_error": 0,
        "first_mismatch": [],
        "mismatch_samples": [],
        "signed_error_histogram": {},
        "pixel_record_pixels": np.empty((0,), dtype=np.int64),
        "pixel_record_offsets": np.empty((0,), dtype=np.int64),
        "ordered_record_indices": np.empty((0,), dtype=np.int64),
        "contributing_sources": [],
    }


def read_frame(
    frame: Mapping[str, Any],
    *,
    receipt_root: Path | None = None,
    replay: bool = True,
    row_chunk: int = DEFAULT_ROW_CHUNK,
    actual_raw_path: Path | None = None,
) -> dict[str, Any]:
    """Validate and optionally replay one immutable frame receipt.

    ``replay=False`` validates receipt joins, PNG/raw binding, sidecar hashes,
    ABI, source identities, and compact order without visiting every output
    pixel.  ``replay=True`` is the explicit offline exact comparison.
    """
    try:
        if not isinstance(frame, Mapping):
            raise _fail("frame_not_object")
        if isinstance(row_chunk, bool) or not isinstance(row_chunk, int) or row_chunk <= 0 or row_chunk > DEFAULT_ROW_CHUNK:
            raise _fail("row_chunk_out_of_bounds")
        root = (receipt_root or Path.cwd()).resolve()
        size = frame.get("size")
        if not isinstance(size, list) or len(size) != 2:
            raise _fail("frame_size_invalid")
        width, height = _int(size[0], "frame_width", positive=True), _int(size[1], "frame_height", positive=True)
        if width > MAX_WIDTH or height > MAX_HEIGHT:
            raise _fail("frame_extent_out_of_bounds")
        _validate_target(frame, width, height)
        provenance = frame["canvas_provenance"]
        _validate_dispatch(frame, provenance)
        png_path = _resolve_path(frame.get("path"), root, "final_png")
        image = _read_png(png_path, frame.get("sha256"), width, height)
        raw_path = actual_raw_path
        raw, raw_sha = _raw_binding(raw_path, image, width, height, row_chunk)
        fragments, sources, base = _validate_sidecars(frame, provenance, width, height, root)
        if len(fragments) != _int(provenance.get("record_count"), "canvas_record_count", nonnegative=True):
            raise _fail("canvas_record_count_mismatch")
        order, pixels, offsets, source_indices, source_rows = _validate_records(fragments, sources, frame, width, height)
        result: dict[str, Any] = {
            "status": "ok",
            "reason": "structural_validation_only" if not replay else "exact",
            "replay_performed": replay,
            "exact": False,
            "mismatched_pixels": 0,
            "max_channel_error": 0,
            "first_mismatch": [],
            "mismatch_samples": [],
            "signed_error_histogram": {},
            "pixel_record_pixels": pixels,
            "pixel_record_offsets": offsets,
            "ordered_record_indices": order.astype(np.int64, copy=False),
            "contributing_sources": source_rows,
            "actual_raw_sha256": raw_sha,
            "surface_boundary": SURFACE_BOUNDARY,
            "input_boundary": "captured_pre_canvas_load_action_target",
        }
        if not replay:
            return result
        target = provenance["target_info"]
        clear_color = target.get("clear_color_rgba") if target.get("load_action") == "clear" else None
        replay_result = _replay_rows(
            image,
            base,
            fragments,
            order,
            pixels,
            offsets,
            width,
            height,
            bool(target.get("srgb")),
            clear_color,
            row_chunk,
            raw,
        )
        result.update(replay_result)
        result["reason"] = "exact" if result["exact"] else "byte_mismatch"
        return result
    except (CanvasTraceError, OSError, ValueError, TypeError, OverflowError, KeyError, IndexError) as exc:
        return _failure(str(exc))
    finally:
        # Pillow holds the decoded image until close.  Memmaps are released by
        # their owning ndarray and need no explicit copy or sidecar expansion.
        image = locals().get("image")
        if isinstance(image, Image.Image):
            image.close()


def read_capture(
    receipt_path: Path,
    *,
    replay: bool = True,
    row_chunk: int = DEFAULT_ROW_CHUNK,
    expected_frames: int | None = 5,
) -> dict[str, Any]:
    """Read every frame in one observer receipt and return bounded summaries."""
    receipt_path = receipt_path.resolve()
    try:
        with receipt_path.open("r", encoding="utf-8") as handle:
            receipt = json.load(handle)
        if not isinstance(receipt, Mapping):
            raise _fail("receipt_not_object")
        if receipt.get("status") != "readback_complete":
            raise _fail("receipt_status_not_readback_complete")
        if receipt.get("surface_boundary") != SURFACE_BOUNDARY:
            raise _fail("receipt_surface_boundary_mismatch")
        frames = receipt.get("frames")
        if not isinstance(frames, list) or (expected_frames is not None and len(frames) != expected_frames):
            raise _fail("receipt_frame_count_mismatch")
        results = [read_frame(frame, receipt_root=receipt_path.parent, replay=replay, row_chunk=row_chunk) for frame in frames]
        exact = all(row.get("status") == "ok" and (not replay or row.get("exact") is True) for row in results)
        return {
            "status": "ok" if exact else "failed",
            "reason": "exact" if replay and exact else ("structural_validation_only" if not replay and exact else "frame_failure"),
            "exact": exact if replay else False,
            "frames": results,
            "frame_count": len(results),
        }
    except (OSError, json.JSONDecodeError, CanvasTraceError) as exc:
        return {"status": "failed", "reason": str(exc), "exact": False, "frames": [], "frame_count": 0}


def _summary(result: Mapping[str, Any]) -> dict[str, Any]:
    summary = dict(result)
    if "frames" in summary:
        summary["frames"] = [
            {
                key: value
                for key, value in row.items()
                if key not in {"pixel_record_pixels", "pixel_record_offsets", "ordered_record_indices", "contributing_sources"}
            }
            for row in summary["frames"]
        ]
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--structural-only", action="store_true")
    parser.add_argument("--row-chunk", type=int, default=DEFAULT_ROW_CHUNK)
    args = parser.parse_args(argv)
    result = read_capture(args.receipt, replay=not args.structural_only, row_chunk=args.row_chunk)
    print(json.dumps(_summary(result), sort_keys=True, separators=(",", ":")))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
