#!/usr/bin/env python3
"""Assemble the four FL-4359 presentation modes into labeled 2x2 GIFs.

The source collection contains four independently captured mode arms.  This
builder preserves every source pane at its decoded pixel dimensions and adds
only an external label band and gutter around the panes.  The archive's
directory names are BG/FG ordered; the displayed mode labels are the
operator-facing FG/BG order: FF, FC, CF, CC.

The output is diagnostic presentation evidence only.  It does not run Godot,
change source captures, compute a jitter verdict, or make an acceptance claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from fl4359_label_media import attributes, resolve_media_path

SCHEMA = "fl4359.native_presentation_mode_grids.v1"
FRAME_COUNT = 5
DEFAULT_FRAME_MS = 60
DEFAULT_LABEL_BAND_PX = 72
DEFAULT_GUTTER_PX = 24
DEFAULT_FONT_SIZE = 40

# Archive directory names are BG/FG ordered.  Labels intentionally use the
# requested FG/BG order and the fixed 2x2 positions are part of the contract.
MODES = OrderedDict(
    [
        ("FF", ("FLAT_BG_FLAT_FG", "FG=FLAT  BG=FLAT")),
        ("FC", ("CONTINUOUS_BG_FLAT_FG", "FG=FLAT  BG=CONTINUOUS")),
        ("CF", ("FLAT_BG_CONTINUOUS_FG", "FG=CONTINUOUS  BG=FLAT")),
        (
            "CC",
            ("CONTINUOUS_BG_CONTINUOUS_FG", "FG=CONTINUOUS  BG=CONTINUOUS"),
        ),
    ]
)

ROLE_DIRS = OrderedDict(
    [
        ("native", None),
        ("jitter_capture", "jitter_capture"),
        ("bg_only", "bg_only"),
        ("fg_only", "fg_only"),
        ("fg_highlight", "fg_highlight"),
        ("fg_glyph_change_highlight", "fg_glyph_change_highlight"),
        ("glyph_change_highlight", "glyph_change_highlight"),
    ]
)

PANE_POSITIONS = OrderedDict(
    [
        ("FF", "top-left"),
        ("FC", "top-right"),
        ("CF", "bottom-left"),
        ("CC", "bottom-right"),
    ]
)

LABEL_BG = (24, 24, 24)
LABEL_FG = (245, 245, 245)
CANVAS_BG = (8, 8, 8)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rgb_frame_digest(image: Image.Image) -> str:
    return sha256_bytes(image.convert("RGB").tobytes())


def _inspect_source(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"source GIF is missing: {path}")
    frame_digests: list[str] = []
    durations: list[int] = []
    dimensions: list[list[int]] = []
    with Image.open(path) as image:
        frame_count = int(getattr(image, "n_frames", 1))
        for index in range(frame_count):
            image.seek(index)
            frame = image.convert("RGB")
            frame_digests.append(_rgb_frame_digest(frame))
            dimensions.append([int(frame.width), int(frame.height)])
            duration = image.info.get("duration", DEFAULT_FRAME_MS)
            durations.append(int(duration))
    if frame_count != FRAME_COUNT:
        raise ValueError(f"{path}: expected {FRAME_COUNT} frames, found {frame_count}")
    if len(set(tuple(size) for size in dimensions)) != 1:
        raise ValueError(f"{path}: source frame dimensions vary: {dimensions}")
    if not durations or any(duration <= 0 for duration in durations):
        raise ValueError(f"{path}: source frame duration is missing or non-positive")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "frame_count": frame_count,
        "dimensions": dimensions[0],
        "durations_ms": durations,
        "decoded_rgb_sha256": frame_digests,
    }


def _load_frame(path: Path, index: int) -> Image.Image:
    with Image.open(path) as image:
        image.seek(index)
        return image.convert("RGB").copy()


def _font_candidates() -> list[Path]:
    return [
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/Library/Fonts/Arial Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]


def _load_font(size: int, requested: Path | None) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str]:
    candidates = [requested] if requested else _font_candidates()
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        try:
            return ImageFont.truetype(str(candidate), size), str(candidate)
        except OSError:
            continue
    return ImageFont.load_default(), "Pillow default bitmap font"


def _pane_box(
    label: str,
    pane_width: int,
    pane_height: int,
    label_band: int,
    gutter: int,
) -> tuple[int, int, int, int]:
    column = 0 if label in ("FF", "CF") else 1
    row = 0 if label in ("FF", "FC") else 1
    x = column * (pane_width + gutter)
    y = row * (pane_height + label_band + gutter) + label_band
    return x, y, pane_width, pane_height


def _compose_frame(
    panes: dict[str, Image.Image],
    labels: dict[str, str],
    label_band: int,
    gutter: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> tuple[Image.Image, dict[str, list[int]]]:
    pane_width = panes["FF"].width
    pane_height = panes["FF"].height
    expected_size = (
        2 * pane_width + gutter,
        2 * (pane_height + label_band) + gutter,
    )
    canvas = Image.new("RGB", expected_size, CANVAS_BG)
    draw = ImageDraw.Draw(canvas)
    boxes: dict[str, list[int]] = {}
    for label in PANE_POSITIONS:
        pane = panes[label]
        if pane.size != (pane_width, pane_height):
            raise ValueError(
                f"frame pane {label} has {pane.size}; expected {(pane_width, pane_height)}"
            )
        x, y, width, height = _pane_box(
            label, pane_width, pane_height, label_band, gutter
        )
        band_y = y - label_band
        draw.rectangle(
            [x, band_y, x + width - 1, y - 1],
            fill=LABEL_BG,
        )
        text = f"{label}  {labels[label]}"
        bounds = draw.textbbox((0, 0), text, font=font)
        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]
        draw.text(
            (x + max(12, (width - text_width) // 2), band_y + max(0, (label_band - text_height) // 2 - bounds[1])),
            text,
            font=font,
            fill=LABEL_FG,
        )
        canvas.paste(pane, (x, y))
        boxes[label] = [x, y, width, height]
    return canvas, boxes


def _quantize_for_gif(image: Image.Image) -> Image.Image:
    # FASTOCTREE is deterministic for RGB input and avoids introducing a
    # dither pattern into source panes or their external labels.
    return image.quantize(
        colors=256,
        method=Image.Quantize.FASTOCTREE,
        dither=Image.Dither.NONE,
    )


def _write_gif(
    frames: list[Image.Image],
    output: Path,
    durations: list[int],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
        optimize=False,
    )


def _pane_comparison(
    source: Image.Image,
    rendered: Image.Image,
) -> dict[str, float | int]:
    source_rgb = source.convert("RGB")
    rendered_rgb = rendered.convert("RGB")
    if source_rgb.size != rendered_rgb.size:
        raise ValueError(
            f"decoded pane size changed from {source_rgb.size} to {rendered_rgb.size}"
        )
    # Use compact numeric arrays and a 256-bin histogram.  A Python list of
    # every channel delta would multiply memory by tens of millions of object
    # pointers and sorting it would create avoidable swap pressure.
    source_array = np.asarray(source_rgb, dtype=np.int16)
    rendered_array = np.asarray(rendered_rgb, dtype=np.int16)
    deltas = np.abs(source_array - rendered_array)
    changed_pixels = int(np.any(deltas != 0, axis=2).sum())
    pixel_count = int(source_rgb.width * source_rgb.height)
    histogram = np.bincount(deltas.reshape(-1), minlength=256)
    percentile_rank = max(1, math.ceil(deltas.size * 0.95))
    p95 = int(np.searchsorted(np.cumsum(histogram), percentile_rank))
    return {
        "exact_pixel_fraction": round(1.0 - changed_pixels / pixel_count, 8),
        "changed_pixel_fraction": round(changed_pixels / pixel_count, 8),
        "mean_abs_rgb": round(float(deltas.mean()), 6),
        "max_abs_rgb": int(deltas.max()) if deltas.size else 0,
        "p95_abs_rgb": p95,
        "pixels": pixel_count,
    }


def _validate_output(
    path: Path,
    expected_dimensions: list[int],
    expected_durations: list[int],
    boxes: dict[str, list[int]],
    source_manifest: dict[str, dict],
) -> tuple[list[int], dict[str, list[dict]]]:
    """Decode one output frame at a time and compare each pane to its source."""
    pane_comparisons: dict[str, list[dict]] = OrderedDict(
        (label, []) for label in PANE_POSITIONS
    )
    durations: list[int] = []
    frame_count = 0
    with Image.open(path) as image:
        frame_count = int(getattr(image, "n_frames", 1))
        for index in range(frame_count):
            image.seek(index)
            rendered = image.convert("RGB").copy()
            if rendered.size != tuple(expected_dimensions):
                rendered.close()
                raise ValueError(
                    f"{path}: decoded frame {index} is {rendered.size}, "
                    f"expected {tuple(expected_dimensions)}"
                )
            durations.append(int(image.info.get("duration", 0)))
            for label in PANE_POSITIONS:
                x, y, width, height = boxes[label]
                crop = rendered.crop((x, y, x + width, y + height))
                source_frame = _load_frame(Path(source_manifest[label]["path"]), index)
                pane_comparisons[label].append(
                    _pane_comparison(source_frame, crop)
                )
                source_frame.close()
                crop.close()
            rendered.close()
    if frame_count != FRAME_COUNT:
        raise ValueError(f"{path}: decoded {frame_count} frames, expected {FRAME_COUNT}")
    if durations != expected_durations:
        raise ValueError(
            f"{path}: decoded durations {durations} != {expected_durations}"
        )
    return durations, pane_comparisons


def _role_source_path(source_root: Path, mode_dir: str, role: str) -> Path:
    if role == "native":
        path = source_root / mode_dir / f"native_5frames_{mode_dir}.gif"
    elif role == "jitter_capture":
        path = source_root / mode_dir / "RASTER" / f"jitter_capture_5frames_{mode_dir}.gif"
    else:
        subdir = ROLE_DIRS[role]
        assert subdir is not None
        path = source_root / mode_dir / "RASTER" / subdir / f"{role}_5frames_{mode_dir}.gif"
    return resolve_media_path(path)


def grid_output_name(role, dimensions, source_attributes):
    def common(key, mixed):
        values = {row[key] for row in source_attributes}
        return next(iter(values)) if len(values) == 1 else mixed
    architecture = common("architecture", "ARCH_MIXED")
    state = common("git_state", "GIT_MIXED")
    if state == "DIRTY" and len({r["dirty_diff_sha256"] for r in source_attributes}) > 1:
        state = "DIRTY-MIXED"
    source = common("source_commit", "MIXED") or "UNKNOWN"
    source = source[:12] if source not in ("MIXED", "UNKNOWN") else source
    cell = common("cell_px", "MIXED")
    glyph = common("glyph_source_px", "MIXED")
    return (f"{architecture}__FF-FC-CF-CC__{state}__SRC-{source}__CELL-{cell}__"
            f"GLYPHSRC{glyph}__IMG-{dimensions[0]}x{dimensions[1]}__{role}-2x2-5f.gif")


def build(source_root: Path, output_root: Path, label_band: int, gutter: int, font_size: int, font_path: Path | None) -> dict:
    if label_band <= 0 or gutter < 0 or font_size <= 0:
        raise ValueError("label band, gutter, and font size must be valid positive values")
    if (output_root / "grid_receipt.json").exists():
        raise ValueError("choose a new output directory; existing grid receipts are immutable")
    output_root.mkdir(parents=True, exist_ok=True)
    font, font_source = _load_font(font_size, font_path)

    source_manifest: dict[str, dict[str, dict]] = OrderedDict()
    for role in ROLE_DIRS:
        source_manifest[role] = OrderedDict()
        for label, (mode_dir, _display) in MODES.items():
            path = _role_source_path(source_root, mode_dir, role)
            source_manifest[role][label] = _inspect_source(path)

    # Every role is required to have one common full-resolution pane size and
    # one common timing sequence across all four captured modes.
    role_geometry: dict[str, dict] = {}
    for role, modes in source_manifest.items():
        dimensions = {tuple(entry["dimensions"]) for entry in modes.values()}
        durations = {tuple(entry["durations_ms"]) for entry in modes.values()}
        if len(dimensions) != 1:
            raise ValueError(f"{role}: mode pane dimensions differ: {sorted(dimensions)}")
        if len(durations) != 1:
            raise ValueError(f"{role}: mode frame durations differ: {sorted(durations)}")
        role_geometry[role] = {
            "pane_dimensions": list(next(iter(dimensions))),
            "durations_ms": list(next(iter(durations))),
            "frame_count": FRAME_COUNT,
        }

    output_manifest: dict[str, dict] = OrderedDict()
    validations: dict[str, dict] = OrderedDict()
    for role in ROLE_DIRS:
        geometry = role_geometry[role]
        pane_width, pane_height = geometry["pane_dimensions"]
        grid_dimensions = [
            2 * pane_width + gutter,
            2 * (pane_height + label_band) + gutter,
        ]
        source_attributes = [attributes(Path(row["path"]), source_root)
                             for row in source_manifest[role].values()]
        output_path = output_root / grid_output_name(role, grid_dimensions, source_attributes)
        if output_path.exists():
            raise ValueError(f"refusing to overwrite an existing grid: {output_path}")
        quantized_frames: list[Image.Image] = []
        boxes: dict[str, list[int]] | None = None
        for frame_index in range(FRAME_COUNT):
            panes = {
                label: _load_frame(source_manifest[role][label]["path"], frame_index)
                for label in PANE_POSITIONS
            }
            composed, boxes = _compose_frame(
                panes,
                {label: MODES[label][1] for label in MODES},
                label_band,
                gutter,
                font,
            )
            quantized_frames.append(_quantize_for_gif(composed))
            for pane in panes.values():
                pane.close()
            composed.close()
        assert boxes is not None
        _write_gif(quantized_frames, output_path, geometry["durations_ms"])
        for frame in quantized_frames:
            frame.close()

        decoded_durations, pane_comparisons = _validate_output(
            output_path,
            grid_dimensions,
            geometry["durations_ms"],
            boxes,
            source_manifest[role],
        )
        output_manifest[role] = {
            "path": str(output_path),
            "sha256": sha256_file(output_path),
            "bytes": output_path.stat().st_size,
            "frame_count": FRAME_COUNT,
            "dimensions": grid_dimensions,
            "durations_ms": decoded_durations,
        }
        validations[role] = {
            "decoded_frame_count": FRAME_COUNT,
            "decoded_dimensions": grid_dimensions,
            "decoded_durations_ms": decoded_durations,
            "pane_boxes_xywh": boxes,
            "pane_comparisons": pane_comparisons,
            "comparison_note": (
                "Source and grid GIFs are independently palette-quantized. "
                "Comparisons decode both to RGB and report quantization deltas; "
                "source panes are not expected to be byte-identical after stitching."
            ),
        }

    receipt = {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "assembled_diagnostic_only",
        "acceptance_claimed": False,
        "source_root": str(source_root),
        "output_root": str(output_root),
        "command": shlex.join([sys.executable, *sys.argv]),
        "layout": {
            "mode_order_fg_bg": list(MODES),
            "pane_positions": PANE_POSITIONS,
            "label_text": {label: MODES[label][1] for label in MODES},
            "label_band_px": label_band,
            "gutter_px": gutter,
            "font_size_px": font_size,
            "font": font_source,
            "source_panes_unscaled_or_cropped": True,
            "quantization": "per-composite-frame 256-colour GIF palette, no dithering",
        },
        "roles": list(ROLE_DIRS),
        "sources": source_manifest,
        "outputs": output_manifest,
        "validation": validations,
        "validation_summary": {
            "roles": len(ROLE_DIRS),
            "outputs": len(output_manifest),
            "frames_per_output": FRAME_COUNT,
            "all_structural_checks_pass": True,
            "all_panes_full_source_dimensions": True,
            "pane_comparison_method": "decoded RGB crop against decoded RGB source frame",
        },
    }
    receipt_path = output_root / "grid_receipt.json"
    receipt["receipt_path"] = str(receipt_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"receipt": str(receipt_path), "outputs": output_manifest}, indent=2))
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build seven labeled, full-resolution FL-4359 2x2 GIF grids."
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--label-band-px", type=int, default=DEFAULT_LABEL_BAND_PX)
    parser.add_argument("--gutter-px", type=int, default=DEFAULT_GUTTER_PX)
    parser.add_argument("--font-size-px", type=int, default=DEFAULT_FONT_SIZE)
    parser.add_argument("--font", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        build(
            args.source_root.expanduser().resolve(),
            args.output_root.expanduser().resolve(),
            args.label_band_px,
            args.gutter_px,
            args.font_size_px,
            args.font.expanduser().resolve() if args.font else None,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"[fl4359-grid] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
