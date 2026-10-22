#!/usr/bin/env python3
"""FL-4359 sweep frame stitcher: per-arm GIF plus one side-by-side GIF.

This script only stitches. It applies no threshold, computes no metric, and
makes no renderer, proof, or acceptance claim. Its inputs are the five
per-sequence-index PNGs the external movement driver's frame-PNG route writes
(``frame_00.png`` .. ``frame_04.png``, sequence indices 0-4: the four declared
sweep poses followed by the held final pose).

Usage:

    python3 scripts/fl4359_stitch_sweep_gif.py \\
        --arm on=/abs/run_a/frames --arm off=/abs/run_b/frames \\
        --out /abs/compare_5frames.gif --frame-ms 60

Every arm is one horizontal panel per frame, so all arms play in parallel. Each
panel carries a label band naming the arm, the frame index, and the declared
camera position for that index when the driver receipt is available next to the
frames.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# Sequence indices the driver's frame-PNG route writes.
FRAME_COUNT = 5
FRAME_NAME = "frame_%02d.png"
# Height of the per-panel label band, in pixels.
LABEL_BAND_PX = 40
# The operator reference (fl4359_airplane_5frame_fixture/source_shimmer-jitter.gif)
# is 3566x2280 for five frames, so a panel is fitted to this height by default.
REFERENCE_PANEL_HEIGHT_PX = 2280
DEFAULT_FRAME_MS = 60
LABEL_BG = (16, 16, 16)
LABEL_FG = (235, 235, 235)
PANEL_BG = (0, 0, 0)


def _parse_arm(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"--arm expects LABEL=DIR, got {value!r}"
        )
    label, _, directory = value.partition("=")
    label = label.strip()
    directory = directory.strip()
    if not label or not directory:
        raise argparse.ArgumentTypeError(
            f"--arm expects a non-empty LABEL and DIR, got {value!r}"
        )
    return label, Path(directory).expanduser()


def _frame_paths(directory: Path) -> list[Path]:
    return [directory / (FRAME_NAME % index) for index in range(FRAME_COUNT)]


def _missing_frames(directory: Path) -> list[str]:
    return [path.name for path in _frame_paths(directory) if not path.is_file()]


def _declared_poses(directory: Path) -> dict[int, str]:
    """Read `frame_png_poses` from a driver receipt near the frame directory.

    The frames live in `<out>/frames` and the driver receipt in `<out>`, so the
    directory itself and its parent are both checked. A missing or unreadable
    receipt is not an error: the label band then omits the pose text rather than
    inventing one.
    """
    poses: dict[int, str] = {}
    candidates = [
        directory / "receipt.json",
        directory.parent / "receipt.json",
        directory.parent / "fl4359_receipt.json",
    ]
    candidates.extend(sorted(directory.parent.glob("*receipt*.json")))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        rows = payload.get("frame_png_poses")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            index = row.get("sequence_frame")
            position = row.get("camera_position")
            if not isinstance(index, int) or not isinstance(position, dict):
                continue
            try:
                poses[index] = "(%.3f, %.3f, %.3f)" % (
                    float(position.get("x")),
                    float(position.get("y")),
                    float(position.get("z")),
                )
            except (TypeError, ValueError):
                continue
        if poses:
            return poses
    return poses


def _scaled(image: Image.Image, target_height: int) -> Image.Image:
    if image.height == target_height:
        return image.convert("RGB")
    width = max(1, round(image.width * target_height / image.height))
    return image.convert("RGB").resize((width, target_height), Image.LANCZOS)


def _label_panel(image: Image.Image, text: str) -> Image.Image:
    panel = Image.new(
        "RGB", (image.width, image.height + LABEL_BAND_PX), PANEL_BG
    )
    panel.paste(image, (0, LABEL_BAND_PX))
    draw = ImageDraw.Draw(panel)
    draw.rectangle([0, 0, panel.width, LABEL_BAND_PX], fill=LABEL_BG)
    # Pillow's default bitmap font is used deliberately: no external font file
    # has to be located, so the stitch result is identical on every machine.
    draw.text((8, max(0, (LABEL_BAND_PX - 11) // 2)), text, fill=LABEL_FG)
    return panel


def _write_gif(frames: list[Image.Image], out_path: Path,
               frame_ms: int | list[int]) -> None:
    if not frames:
        raise ValueError("GIF requires at least one frame")
    if len({frame.size for frame in frames}) != 1:
        raise ValueError("GIF frames must have matching dimensions")
    # Learn one palette for the sequence. Independent adaptive palettes repaint
    # identical RGB inputs between frames; error diffusion also depends on neighbours.
    width, height = frames[0].size
    palette_source = Image.new("RGB", (width, height * len(frames)))
    for index, frame in enumerate(frames):
        palette_source.paste(frame.convert("RGB"), (0, height * index))
    palette = palette_source.quantize(colors=256, dither=Image.Dither.NONE)
    palette_source.close()
    indexed = [frame.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE)
               for frame in frames]
    palette.close()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    indexed[0].save(
        out_path,
        save_all=True,
        append_images=indexed[1:],
        duration=frame_ms,
        # 0 == loop forever.
        loop=0,
        disposal=2,
        optimize=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stitch FL-4359 sweep frame PNGs into per-arm GIFs and one "
            "side-by-side GIF. Stitching only: no thresholds, no analysis."
        )
    )
    parser.add_argument(
        "--arm",
        action="append",
        default=[],
        type=_parse_arm,
        metavar="LABEL=DIR",
        help=(
            "Repeatable. DIR must hold frame_00.png..frame_04.png written by "
            "the driver's --fl4408-frame-png-dir route."
        ),
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Absolute path of the side-by-side GIF to write.",
    )
    parser.add_argument(
        "--frame-ms",
        type=int,
        default=DEFAULT_FRAME_MS,
        help="Per-frame duration in milliseconds (operator reference: 60).",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=REFERENCE_PANEL_HEIGHT_PX,
        help=(
            "Panel height in pixels; aspect ratio is preserved and a taller "
            "source is fitted down to it. Default matches the operator "
            "reference frame height."
        ),
    )
    args = parser.parse_args(argv)

    if not args.arm:
        print("[fl4359-stitch] ERROR: at least one --arm LABEL=DIR is required")
        return 1
    if args.frame_ms <= 0:
        print("[fl4359-stitch] ERROR: --frame-ms must be positive")
        return 1
    if args.scale <= 0:
        print("[fl4359-stitch] ERROR: --scale must be positive")
        return 1

    for label, directory in args.arm:
        missing = _missing_frames(directory)
        if missing:
            print(
                f"[fl4359-stitch] ERROR: arm {label!r} in {directory} is "
                f"missing {len(missing)} of {FRAME_COUNT} frames: "
                + ", ".join(missing)
            )
            return 1

    # Panel height: never upscale past the source, never exceed --scale.
    source_heights: list[int] = []
    for _label, directory in args.arm:
        for path in _frame_paths(directory):
            with Image.open(path) as image:
                source_heights.append(image.height)
    panel_height = min(args.scale, min(source_heights))

    per_arm_panels: list[list[Image.Image]] = []
    for label, directory in args.arm:
        poses = _declared_poses(directory)
        arm_frames: list[Image.Image] = []
        panels: list[Image.Image] = []
        for index, path in enumerate(_frame_paths(directory)):
            with Image.open(path) as image:
                scaled = _scaled(image, panel_height)
            arm_frames.append(scaled)
            pose_text = poses.get(index, "")
            caption = f"{label}  f{index:02d}"
            if pose_text:
                caption = f"{caption}  {pose_text}"
            panels.append(_label_panel(scaled, caption))
        per_arm_panels.append(panels)
        # (1) One GIF per arm, written only when it is not already there.
        arm_gif = directory / "sweep_5frames.gif"
        if not arm_gif.exists():
            _write_gif(arm_frames, arm_gif, args.frame_ms)
            print(f"[fl4359-stitch] arm_gif {label} {arm_gif}")
        else:
            print(f"[fl4359-stitch] arm_gif_exists {label} {arm_gif}")

    # (2) The side-by-side GIF: one horizontal panel per arm, per frame. Every
    # panel already shares `panel_height`, so the composite height never varies
    # between frames.
    composite_frames: list[Image.Image] = []
    for index in range(FRAME_COUNT):
        row = [panels[index] for panels in per_arm_panels]
        total_width = sum(panel.width for panel in row)
        total_height = max(panel.height for panel in row)
        composite = Image.new("RGB", (total_width, total_height), PANEL_BG)
        offset = 0
        for panel in row:
            composite.paste(panel, (offset, 0))
            offset += panel.width
        composite_frames.append(composite)

    out_path = Path(args.out).expanduser()
    _write_gif(composite_frames, out_path, args.frame_ms)
    print(
        f"[fl4359-stitch] side_by_side {out_path} "
        f"arms={len(args.arm)} frames={len(composite_frames)} "
        f"size={composite_frames[0].width}x{composite_frames[0].height} "
        f"frame_ms={args.frame_ms}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
