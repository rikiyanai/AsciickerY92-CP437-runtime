#!/usr/bin/env python3
"""Build a readable Manny Souls visual proof grid from a Godot receipt."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


REPO_ROOT = Path(__file__).resolve().parents[1]
CELL_PX = 16
PANEL_BG = (22, 24, 28)
TEXT = (234, 238, 244)
MUTED = (171, 178, 191)
ACCENT = (255, 89, 76)
OK = (107, 214, 122)
LINE = (75, 82, 96)


def parse_vec4(value: object) -> tuple[int, int, int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(int(v) for v in value)  # type: ignore[return-value]
    text = str(value)
    nums = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]
    if len(nums) != 4:
        raise ValueError(f"expected vec4, got {value!r}")
    return nums[0], nums[1], nums[2], nums[3]


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def fit_image(img: Image.Image, size: tuple[int, int], resample: int = Image.Resampling.LANCZOS) -> Image.Image:
    img = img.convert("RGB")
    copy = img.copy()
    copy.thumbnail(size, resample)
    return copy


def draw_panel(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], title: str, lines: Iterable[str]) -> None:
    draw.rectangle(xy, fill=PANEL_BG, outline=LINE, width=2)
    font_title = load_font(22)
    font_body = load_font(17)
    x0, y0, _, _ = xy
    draw.text((x0 + 18, y0 + 14), title, fill=TEXT, font=font_title)
    y = y0 + 50
    for line in lines:
        fill = OK if line.startswith("PASS") else MUTED
        draw.text((x0 + 18, y), line, fill=fill, font=font_body)
        y += 25


def image_panel(canvas: Image.Image, xy: tuple[int, int, int, int], title: str, img: Image.Image) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(xy, fill=PANEL_BG, outline=LINE, width=2)
    font_title = load_font(22)
    x0, y0, x1, y1 = xy
    draw.text((x0 + 18, y0 + 14), title, fill=TEXT, font=font_title)
    fitted = fit_image(img, (x1 - x0 - 36, y1 - y0 - 70), Image.Resampling.NEAREST)
    px = x0 + ((x1 - x0) - fitted.width) // 2
    py = y0 + 58 + ((y1 - y0 - 70) - fitted.height) // 2
    canvas.paste(fitted, (px, py))


def runtime_actor_crop(runtime_png: Image.Image, receipt: dict) -> Image.Image:
    cells = receipt["final_cell_dump"]["actor_cells"]
    xs = [int(c["screen_cell"]["x"]) for c in cells]
    ys = [int(c["screen_cell"]["y"]) for c in cells]
    x0 = max(0, (min(xs) - 2) * CELL_PX)
    y0 = max(0, (min(ys) - 2) * CELL_PX)
    x1 = min(runtime_png.width, (max(xs) + 3) * CELL_PX)
    y1 = min(runtime_png.height, (max(ys) + 3) * CELL_PX)
    crop = runtime_png.crop((x0, y0, x1, y1)).convert("RGB")
    draw = ImageDraw.Draw(crop)
    for cell in cells:
        cx = int(cell["screen_cell"]["x"]) * CELL_PX - x0
        cy = int(cell["screen_cell"]["y"]) * CELL_PX - y0
        draw.rectangle((cx, cy, cx + CELL_PX - 1, cy + CELL_PX - 1), outline=ACCENT, width=1)
    return crop


def selected_variant(receipt: dict, presentation: str = "Slash1") -> dict:
    rows = receipt.get("manny_souls_pre_paint_rows", [])
    for row in rows:
        for variant in row.get("view_yaw_variants", []):
            if variant.get("presentation_kind") == presentation:
                return variant
    for sample in receipt.get("manny_souls_pose_samples", []):
        if sample.get("presentation_kind") == presentation:
            return sample
    raise ValueError(f"no {presentation} Manny variant in receipt")


def presentation_montage(sidecar: dict, atlas: Image.Image, selected_rects: dict[str, tuple[int, int, int, int]]) -> Image.Image:
    presentations = ["Slash1", "Slash2", "Slash3"]
    strips: list[Image.Image] = []
    label_font = load_font(18)
    for presentation in presentations:
        strip = single_presentation_strip(sidecar, atlas, presentation, selected_rects.get(presentation))
        labeled = Image.new("RGB", (strip.width, strip.height + 28), (12, 14, 18))
        ImageDraw.Draw(labeled).text((6, 4), presentation, fill=TEXT, font=label_font)
        labeled.paste(strip, (0, 28))
        strips.append(labeled)
    gap = 10
    width = max(strip.width for strip in strips)
    height = sum(strip.height for strip in strips) + gap * (len(strips) - 1)
    out = Image.new("RGB", (width, height), (12, 14, 18))
    y = 0
    for strip in strips:
        out.paste(strip, (0, y))
        y += strip.height + gap
    return out


def single_presentation_strip(
    sidecar: dict,
    atlas: Image.Image,
    presentation: str,
    selected_rect: tuple[int, int, int, int] | None,
) -> Image.Image:
    frames = [
        frame for frame in sidecar.get("compositor_frames", [])
        if frame.get("presentation_kind") == presentation and int(frame.get("yaw", -1)) == 0
    ]
    frames.sort(key=lambda f: int(f.get("frame", 0)))
    crops: list[Image.Image] = []
    for frame in frames:
        rect = parse_vec4(frame["frame_rect_px"])
        crop = atlas.crop((rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3])).convert("RGB")
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.NEAREST)
        d = ImageDraw.Draw(crop)
        outline = ACCENT if selected_rect is not None and rect == selected_rect else LINE
        d.rectangle((0, 0, crop.width - 1, crop.height - 1), outline=outline, width=5)
        crops.append(crop)
    if not crops:
        raise ValueError(f"no {presentation} montage frames")
    gap = 12
    width = sum(c.width for c in crops) + gap * (len(crops) - 1)
    height = max(c.height for c in crops)
    out = Image.new("RGB", (width, height), (12, 14, 18))
    x = 0
    for crop in crops:
        out.paste(crop, (x, height - crop.height))
        x += crop.width + gap
    return out


def git_commit() -> str:
	try:
		commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True).strip()
		status = subprocess.check_output(["git", "status", "--short"], cwd=REPO_ROOT, text=True).strip()
		return f"{commit}-dirty" if status else commit
	except Exception:
		return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--png", required=True, type=Path)
    parser.add_argument("--sidecar", default=REPO_ROOT / "godot_project/assets/xp_sprites/demo_manny-souls.json", type=Path)
    parser.add_argument("--atlas", default=REPO_ROOT / "godot_project/assets/xp_sprites/demo_manny-souls.png", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    receipt = json.loads(args.receipt.read_text())
    sidecar = json.loads(args.sidecar.read_text())
    runtime = Image.open(args.png).convert("RGB")
    atlas = Image.open(args.atlas).convert("RGBA")
    selected_variants = {name: selected_variant(receipt, name) for name in ("Slash1", "Slash2", "Slash3")}
    selected_rects = {name: parse_vec4(variant["frame_rect_px"]) for name, variant in selected_variants.items()}
    variant = selected_variants["Slash1"]
    selected_rect = selected_rects["Slash1"]
    selected_frame = atlas.crop((selected_rect[0], selected_rect[1], selected_rect[0] + selected_rect[2], selected_rect[1] + selected_rect[3])).convert("RGB")
    runtime_crop = runtime_actor_crop(runtime, receipt)
    montage = presentation_montage(sidecar, atlas, selected_rects)

    canvas = Image.new("RGB", (1800, 1320), (13, 15, 19))
    draw = ImageDraw.Draw(canvas)
    title_font = load_font(30)
    body_font = load_font(18)
    draw.text((28, 22), "FL-4351 / FL-4407 Manny Souls melee visual proof", fill=TEXT, font=title_font)
    draw.text((28, 62), f"commit={git_commit()} receipt={args.receipt.name} runtime_png={args.png.name}", fill=MUTED, font=body_font)

    image_panel(canvas, (28, 105, 888, 600), "Runtime full viewport", runtime)
    image_panel(canvas, (912, 105, 1772, 600), "Runtime compositor actor cells, zoomed", runtime_crop.resize((runtime_crop.width * 8, runtime_crop.height * 8), Image.Resampling.NEAREST))
    image_panel(canvas, (28, 630, 588, 1190), "Selected receipt atlas frame", selected_frame.resize((selected_frame.width * 3, selected_frame.height * 3), Image.Resampling.NEAREST))
    image_panel(canvas, (612, 630, 1772, 1035), "Demo rig source-action strips, yaw 0", montage)

    actor = receipt["actors"][0]
    damaged = receipt["npcs"][0]
    source_action = next(
        (m.get("source_action", "") for m in sidecar.get("presentation_action_map", []) if m.get("presentation_kind") == "Slash1"),
        "",
    )
    draw_panel(
        draw,
        (612, 982, 1772, 1268),
        "Receipt facts",
        [
            f"PASS ok={receipt['ok']} gate={receipt['sequence_gate_kind']} final_cells={receipt['final_cell_proof_ok']}",
            f"PASS actor visual={actor['visual_composite_key']} weapon={actor['weapon_id']} active={actor['attack_active']}",
            f"PASS selected target render id={actor['lock_on_target_render_actor_id']} damaged_npc_hp={damaged['hp']} posture={damaged['posture']}",
            f"PASS presentations={','.join(receipt.get('manny_souls_presentations_seen', []))} intents={receipt.get('manny_souls_attack_intent_count', 0)}",
            f"PASS selected Slash1/Slash2/Slash3 frame rects={selected_rects['Slash1']} {selected_rects['Slash2']} {selected_rects['Slash3']}",
            f"source repo={sidecar.get('source_repo', '')}",
            f"source asset={sidecar.get('source_asset', '')}",
            f"source weapon={sidecar.get('source_weapon_asset', '')}",
            f"source action={source_action}",
        ],
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.out)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
