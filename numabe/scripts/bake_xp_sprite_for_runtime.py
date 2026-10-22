#!/usr/bin/env python3
"""Bake an XP sprite to a runtime-ready PNG + sidecar JSON.

For each input ``assets/sprites/<name>.xp``, writes:
  - ``godot_project/assets/xp_sprites/<name>.png`` (RGBA, scale 16, L0 key aware)
  - ``godot_project/assets/xp_sprites/<name>.json`` (geometry + anim segment metadata)

The PNG is produced by the canonical converter (``scripts/png2xp2png.py``)
so that the upstream L0[0,0].bg + magenta + legacy-yellow transparency
contract is honored without divergence.

The sidecar JSON carries everything the compositor-owned baked PNG actor path
needs to render the sheet without the runtime having to re-read L0 metadata:

  {
    "source_xp": "assets/sprites/player-0001.xp",
    "cell_scale_px": 16,
    "actor_pixel_size_world": 0.0075,
    "width_cells": 126,
    "height_cells": 72,
    "angles": 8,
    "anim_lens": [1, 8],
    "frame_w_cells": 7,
    "frame_h_cells": 9,
    "frame_w_px": 112,
    "frame_h_px": 144,
    "y_proj": 1,
    "z_proj": 8,
    "color_key_l0_bg": [255, 255, 85],
    "family": "player",
    "anim_segments": [
      {"name": "idle", "start_col": 0, "len": 1, "fps_default": 4.0},
      {"name": "walk", "start_col": 1, "len": 8, "fps_default": 9.0}
    ]
  }

This script is the only owner of XP -> runtime metadata mapping. The runtime
loader reads the JSON and samples the PNG atlas; it does not parse XPs or
guess segment names.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONVERTER = REPO_ROOT / "scripts" / "png2xp2png.py"
OUTPUT_DIR = REPO_ROOT / "godot_project" / "assets" / "xp_sprites"
DEFAULT_SCALE = 16
# FL-4346: Baseline A (`faa36357e`) authored 0.0075 world units per
# raster pixel at a 16-pixel XP-cell bake, which was 0.12 world units per
# authored XP cell. Keep physical size independent of bake resolution.
#
# FL-4346 size bump (2026-08-05): raised 5 percent, 0.12 -> 0.126, so every
# actor sprite presents slightly larger on screen. At the default 16-pixel
# bake this makes actor_pixel_size_world = 0.126 / 16 = 0.007875 world units
# per raster pixel. This constant is the ONLY authored owner of actor world
# scale — the runtime reads the derived value from each baked sidecar JSON
# ("actor_pixel_size_world") and has no fallback constant of its own, so a
# full rebake of every sidecar (body, attack, equipment, death layers
# together) is required after changing this number to keep all layers
# proportional. The anchor law is unchanged: the presented root stays fixed
# and the waterline remains the billboard midpoint.
ACTOR_CELL_SIZE_WORLD = 0.126

sys.path.insert(0, str(REPO_ROOT / "pipeline-v3" / "src"))
from pipeline_v2.xp_codec import read_xp  # type: ignore  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from png2xp2png import (  # type: ignore  # noqa: E402
    BdfFont,
    _is_transparent,
    auto_select_bdf,
    render_layer,
    render_cell_font,
    sprite_transparency_keys,
    write_png,
    read_xp as read_xp_layers,
)


def _get_digit(g: int) -> int:
    if 48 <= g <= 57:
        return g - 48
    if 65 <= g <= 90:
        return g - 65 + 10
    if 97 <= g <= 122:
        return g - 97 + 36
    return 0


# Family-specific segment naming. Each list slot corresponds to anim[i+1]
# (anim_lens[i]) from the XP's L0 row 0 columns 1..N. Anchored to upstream
# game semantics (player walk cycle, attack swing, plydie death roll).
FAMILY_SEGMENTS: dict[str, list[dict]] = {
    "player": [
        {"name": "idle", "fps_default": 4.0},
        {"name": "walk", "fps_default": 9.0},
    ],
    "attack": [
        {"name": "swing", "fps_default": 11.0},
    ],
    "plydie": [
        {"name": "death", "fps_default": 5.0},
    ],
    "wolfie": [
        {"name": "idle", "fps_default": 3.0},
        {"name": "walk", "fps_default": 7.0},
    ],
    "wolack": [
        {"name": "lunge", "fps_default": 9.0},
    ],
    "bigbee": [
        {"name": "hover", "fps_default": 8.0},
    ],
}

# Attack XPs are authored as a visual stack: L2 is the full attacking body
# and later layers carry the swing/swoosh/equipment overlays. Rendering only
# the final layer produces a swoosh-only PNG, which is not a runtime actor
# sprite. Other families keep the converter's default final visual layer.
#
# FL-3993: plydie XPs follow the same visual-stack contract — L2 is the full
# death/corpse body and L3 is the weapon overlay. Without compositing from L2
# the baked PNG is the weapon-overlay layer alone, which is why the dead NPC
# visually disappeared except for a floating sword. Verified by rendering
# layers separately from plydie-0001.xp: L2 = full 8-yaw 5-frame death anim
# with corpse bodies, L3 = sparse weapon glyphs anchored to the body cells.
COMPOSITE_FROM_LAYER_BY_FAMILY: dict[str, int] = {
    "attack": 2,
    "plydie": 2,
}


def family_for(name: str) -> str:
    """Extract family prefix from a sprite stem like 'player-0001' -> 'player'."""
    return name.split("-", 1)[0] if "-" in name else name


def decode_l0(xp_path: Path) -> dict:
    xp = read_xp(xp_path)
    W, H, L0 = xp["width"], xp["height"], xp["cells"][0]
    at = lambda c, r: L0[r * W + c]  # noqa: E731

    angles = _get_digit(at(0, 0)[0])
    if angles <= 0:
        raise ValueError(f"{xp_path}: invalid L0[0,0] angle count")

    anim_lens: list[int] = []
    for a in range(1, 10):
        d = _get_digit(at(a, 0)[0])
        if d == 0:
            break
        anim_lens.append(d)
    if not anim_lens:
        raise ValueError(f"{xp_path}: no anim_lens in L0 row 0")

    anim_sum = sum(anim_lens)
    if W % (2 * anim_sum) != 0:
        raise ValueError(f"{xp_path}: width {W} not divisible by 2*anim_sum={2*anim_sum}")
    if H % angles != 0:
        raise ValueError(f"{xp_path}: height {H} not divisible by angles={angles}")

    return {
        "width_cells": W,
        "height_cells": H,
        "angles": angles,
        "anim_lens": anim_lens,
        "frame_w_cells": W // (2 * anim_sum),
        "frame_h_cells": H // angles,
        "y_proj": _get_digit(at(1, 0)[0]),
        "z_proj": _get_digit(at(2, 0)[0]),
        "color_key_l0_bg": list(at(0, 0)[2]),
        "layer_count": xp["layers"],
    }


def build_segments(family: str, anim_lens: list[int]) -> list[dict]:
    """Walk anim_lens and pair each with the family's named segment slot.

    Excess anim entries are emitted as 'animN' with a default fps so nothing
    is silently dropped. Missing names (more anim slots than the family
    template defines) are flagged via the suffix index.
    """
    template = FAMILY_SEGMENTS.get(family, [])
    segments = []
    start_col = 0
    for i, length in enumerate(anim_lens):
        if i < len(template):
            slot = dict(template[i])
        else:
            slot = {"name": f"anim{i + 1}", "fps_default": 6.0}
        slot["start_col"] = start_col
        slot["len"] = length
        segments.append(slot)
        start_col += length
    return segments


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def presentation_kinds_for(family: str) -> list[str]:
    if family == "attack":
        return ["attack"]
    if family == "plydie":
        return ["death"]
    if family in {"bigbee", "wolfie", "wolack"}:
        return ["idle_walk", "attack", "death"]
    return ["idle_walk"]


def build_compositor_frames(
    geom: dict,
    scale: int,
    family: str,
    source_cell_payloads: dict[tuple[int, int, int, int], dict],
    layer_role: str = "body",
    layer_order: int = 0,
    presentation_kinds: list[str] | None = None,
) -> list[dict]:
    frame_w_px = geom["frame_w_cells"] * scale
    frame_h_px = geom["frame_h_cells"] * scale
    occupied = stable_occupied_bounds_px(geom, scale, source_cell_payloads)
    occupied_x, occupied_y, occupied_w, occupied_h = occupied
    # Upstream RenderSprite fixes Frame::ref[0] to the full frame width for
    # every yaw. The flattened PNG therefore has one horizontal billboard axis:
    # the full frame midpoint. The occupied crop only removes transparent
    # candidate work; it never moves that axis toward the opaque ink.
    legacy_frame_ref_half_cells = [
        geom["frame_w_cells"],
        geom["y_proj"],
        -geom["z_proj"],
    ]
    occupied_dims_cells = [
        max((occupied_w + scale - 1) // scale, 1),
        max((occupied_h + scale - 1) // scale, 1),
    ]
    frame_count = sum(geom["anim_lens"])
    presentations = presentation_kinds if presentation_kinds else presentation_kinds_for(family)
    frames: list[dict] = []
    for presentation in presentations:
        for frame in range(frame_count):
            for yaw in range(geom["angles"]):
                billboard_anchor_px = [
                    frame_w_px * 0.5 - occupied_x,
                    float(frame_h_px - occupied_y),
                ]
                source_cells: list[dict] = []
                for cy in range(geom["frame_h_cells"]):
                    for cx in range(geom["frame_w_cells"]):
                        payload = source_cell_payloads.get((frame, yaw, cx, cy), {})
                        published_payload = {
                            key: value
                            for key, value in payload.items()
                            if key != "alpha_bounds_local_px"
                        }
                        source_cells.append({
                            "source_cell": [cx, cy],
                            "local_pixel_rect": [
                                cx * scale - occupied_x,
                                cy * scale - occupied_y,
                                scale,
                                scale,
                            ],
                            "uv_rect_px": [
                                (frame * geom["frame_w_cells"] + cx) * scale,
                                (yaw * geom["frame_h_cells"] + cy) * scale,
                                scale,
                                scale,
                            ],
                            **published_payload,
                        })
                frames.append({
                    "presentation_kind": presentation,
                    "frame": frame,
                    "yaw": yaw,
                    "layer_role": layer_role,
                    "layer_order": layer_order,
                    "frame_rect_px": [
                        frame * frame_w_px + occupied_x,
                        yaw * frame_h_px + occupied_y,
                        occupied_w,
                        occupied_h,
                    ],
                    # FL-4346: the converted PNG/LUT owns one flat billboard axis.
                    # Every yaw and layer keeps the same full-frame midpoint at
                    # the actor root; directional ink cannot redefine the pivot.
                    "anchor_ref_px": billboard_anchor_px,
                    "alpha_mask_bounds_px": [0, 0, occupied_w, occupied_h],
                    "frame_dims_cells": occupied_dims_cells,
                    "source_cell_facts": source_cells,
                })
    return frames


def stable_occupied_bounds_px(
    geom: dict,
    scale: int,
    source_cell_payloads: dict[tuple[int, int, int, int], dict],
) -> list[int]:
    """Return one pixel envelope shared by every frame and yaw in the sheet."""
    min_x = geom["frame_w_cells"] * scale
    min_y = geom["frame_h_cells"] * scale
    max_x = 0
    max_y = 0
    found = False
    for (_frame, _yaw, cx, cy), payload in source_cell_payloads.items():
        local = payload.get("alpha_bounds_local_px", [])
        if not isinstance(local, list) or len(local) < 4:
            continue
        x0 = cx * scale + int(local[0])
        y0 = cy * scale + int(local[1])
        x1 = cx * scale + int(local[2])
        y1 = cy * scale + int(local[3])
        min_x = min(min_x, x0)
        min_y = min(min_y, y0)
        max_x = max(max_x, x1)
        max_y = max(max_y, y1)
        found = True
    if not found:
        return [
            0,
            0,
            geom["frame_w_cells"] * scale,
            geom["frame_h_cells"] * scale,
        ]
    return [min_x, min_y, max(max_x - min_x, 1), max(max_y - min_y, 1)]


def _resolved_rendered_layer_indices(layer_count: int, rendered_layers: list[int]) -> list[int]:
    resolved: list[int] = []
    for layer_idx in rendered_layers:
        idx = layer_idx if layer_idx >= 0 else layer_count + layer_idx
        if 0 <= idx < layer_count:
            resolved.append(idx)
    return resolved


def _runtime_font(scale: int) -> BdfFont:
    font_path, _font_size = auto_select_bdf(scale)
    return BdfFont(font_path)


def _cell_visible_coverage(
    glyph: int,
    fg: bytes,
    bg: bytes,
    scale: int,
    keys: frozenset[tuple[int, int, int]],
    font: BdfFont,
    mask_cache: dict[int, list[int] | None],
) -> tuple[float, list[int] | None]:
    visible = 0
    min_x = scale
    min_y = scale
    max_x = -1
    max_y = -1
    total = max(scale * scale, 1)
    for py in range(scale):
        for px in range(scale):
            _r, _g, _b, a = render_cell_font(
                glyph, fg, bg, scale, px, py, font, mask_cache, keys
            )
            if a > 0:
                visible += 1
                min_x = min(min_x, px)
                min_y = min(min_y, py)
                max_x = max(max_x, px)
                max_y = max(max_y, py)
    bounds = None if visible == 0 else [min_x, min_y, max_x + 1, max_y + 1]
    return float(visible) / float(total), bounds


def _cell_visible_coverage_cached(
    glyph: int,
    fg: bytes,
    bg: bytes,
    scale: int,
    keys: frozenset[tuple[int, int, int]],
    cache: dict[
        tuple[int, tuple[int, int, int], tuple[int, int, int], int],
        tuple[float, list[int] | None],
    ],
    font: BdfFont,
    mask_cache: dict[int, list[int] | None],
) -> tuple[float, list[int] | None]:
    key = (
        int(glyph) & 0xFF,
        (int(fg[0]), int(fg[1]), int(fg[2])),
        (int(bg[0]), int(bg[1]), int(bg[2])),
        scale,
    )
    cached = cache.get(key)
    if cached is not None:
        return cached
    stats = _cell_visible_coverage(
        glyph, fg, bg, scale, keys, font, mask_cache
    )
    cache[key] = stats
    return stats


def build_source_cell_payloads(
    xp_path: Path,
    geom: dict,
    scale: int,
    rendered_layers: list[int],
) -> dict[tuple[int, int, int, int], dict]:
    _version, layers = read_xp_layers(str(xp_path))
    layer_indices = _resolved_rendered_layer_indices(len(layers), rendered_layers)
    keys = sprite_transparency_keys(layers)
    font = _runtime_font(scale)
    mask_cache: dict[int, list[int] | None] = {}
    payloads: dict[tuple[int, int, int, int], dict] = {}
    coverage_cache: dict[
        tuple[int, tuple[int, int, int], tuple[int, int, int], int],
        tuple[float, list[int] | None],
    ] = {}
    frame_count = sum(geom["anim_lens"])
    for frame in range(frame_count):
        for yaw in range(geom["angles"]):
            for cy in range(geom["frame_h_cells"]):
                for cx in range(geom["frame_w_cells"]):
                    sheet_x = frame * geom["frame_w_cells"] + cx
                    sheet_y = yaw * geom["frame_h_cells"] + cy
                    best: dict | None = None
                    combined_bounds: list[int] | None = None
                    for layer_idx in layer_indices:
                        width, height, cells = layers[layer_idx]
                        if sheet_x < 0 or sheet_x >= width or sheet_y < 0 or sheet_y >= height:
                            continue
                        glyph, fg, bg = cells[sheet_x * height + sheet_y]
                        coverage, local_bounds = _cell_visible_coverage_cached(
                            glyph, fg, bg, scale, keys, coverage_cache,
                            font, mask_cache,
                        )
                        if coverage <= 0.0:
                            continue
                        best = {
                            "glyph_id": int(glyph) & 0xFF,
                            "fg_rgb": [int(fg[0]), int(fg[1]), int(fg[2])],
                            "bg_rgb": [int(bg[0]), int(bg[1]), int(bg[2])],
                            "alpha_coverage": coverage,
                            "source_layer_index": int(layer_idx),
                            "fg_transparent": bool(_is_transparent(fg, keys)),
                            "bg_transparent": bool(_is_transparent(bg, keys)),
                        }
                        if local_bounds is not None:
                            if combined_bounds is None:
                                combined_bounds = list(local_bounds)
                            else:
                                combined_bounds[0] = min(combined_bounds[0], local_bounds[0])
                                combined_bounds[1] = min(combined_bounds[1], local_bounds[1])
                                combined_bounds[2] = max(combined_bounds[2], local_bounds[2])
                                combined_bounds[3] = max(combined_bounds[3], local_bounds[3])
                    if best is not None:
                        best["alpha_bounds_local_px"] = combined_bounds
                        payloads[(frame, yaw, cx, cy)] = best
    return payloads


def _alpha_blit(dst: bytearray, src: bytearray) -> None:
    for i in range(0, len(dst), 4):
        if src[i + 3] == 0:
            continue
        dst[i] = src[i]
        dst[i + 1] = src[i + 1]
        dst[i + 2] = src[i + 2]
        dst[i + 3] = src[i + 3]


def render_composite_layers(xp_path: Path, png_path: Path, scale: int, start_layer: int) -> list[int]:
    _version, layers = read_xp_layers(str(xp_path))
    if start_layer >= len(layers):
        raise ValueError(f"{xp_path}: composite start layer {start_layer} out of range for {len(layers)} layers")
    keys = sprite_transparency_keys(layers)
    rendered_layers = list(range(start_layer, len(layers)))
    font = _runtime_font(scale)
    out_w = 0
    out_h = 0
    out = bytearray()
    for layer_idx in rendered_layers:
        w, h, cells = layers[layer_idx]
        img_w, img_h, rgba = render_layer(w, h, cells, scale, False, font, keys)
        if not out:
            out_w = img_w
            out_h = img_h
            out = bytearray(img_w * img_h * 4)
        elif img_w != out_w or img_h != out_h:
            raise ValueError(f"{xp_path}: layer {layer_idx} dimensions do not match composite output")
        _alpha_blit(out, rgba)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    write_png(str(png_path), out_w, out_h, out)
    return rendered_layers


def render_single_layer(xp_path: Path, png_path: Path, scale: int, layer_index: int) -> list[int]:
    _version, layers = read_xp_layers(str(xp_path))
    if layer_index < 0 or layer_index >= len(layers):
        raise ValueError(f"{xp_path}: layer {layer_index} out of range for {len(layers)} layers")
    keys = sprite_transparency_keys(layers)
    w, h, cells = layers[layer_index]
    img_w, img_h, rgba = render_layer(
        w, h, cells, scale, False, _runtime_font(scale), keys
    )
    png_path.parent.mkdir(parents=True, exist_ok=True)
    write_png(str(png_path), img_w, img_h, rgba)
    return [layer_index]


def run_converter(xp_path: Path, png_path: Path, scale: int, family: str) -> dict:
    composite_start = COMPOSITE_FROM_LAYER_BY_FAMILY.get(family)
    if composite_start is not None:
        rendered_layers = render_composite_layers(xp_path, png_path, scale, composite_start)
        return {
            "render_mode": "composite_layers",
            "rendered_layers": rendered_layers,
        }
    png_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(CONVERTER),
        str(xp_path),
        "-o", str(png_path),
        "--scale", str(scale),
        "--font",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"converter failed for {xp_path}")
    return {
        "render_mode": "single_layer_last",
        "rendered_layers": [-1],
    }


def bake(
    xp_arg: str,
    scale: int = DEFAULT_SCALE,
    output_stem: str | None = None,
    source_layer_index: int | None = None,
    layer_role: str = "body",
    layer_order: int = 0,
    presentation_kinds: list[str] | None = None,
) -> tuple[Path, Path]:
    xp_path = Path(xp_arg)
    if not xp_path.is_absolute():
        xp_path = REPO_ROOT / xp_path
    if not xp_path.exists():
        raise SystemExit(f"missing: {xp_path}")

    source_stem = xp_path.stem
    stem = output_stem if output_stem else source_stem
    family = family_for(source_stem)

    geom = decode_l0(xp_path)

    png_path = OUTPUT_DIR / f"{stem}.png"
    json_path = OUTPUT_DIR / f"{stem}.json"

    if source_layer_index is not None:
        rendered_layers = render_single_layer(xp_path, png_path, scale, source_layer_index)
        render_info = {
            "render_mode": "single_source_layer",
            "rendered_layers": rendered_layers,
            "source_layer_index": source_layer_index,
        }
    else:
        render_info = run_converter(xp_path, png_path, scale, family)

    rel_source = os.path.relpath(xp_path, REPO_ROOT)
    source_cell_payloads = build_source_cell_payloads(
        xp_path,
        geom,
        scale,
        list(render_info.get("rendered_layers", [])),
    )

    rel_source_norm = rel_source.replace(os.sep, "/")
    rel_atlas_norm = "res://assets/xp_sprites/%s.png" % stem
    sidecar = {
        "schema": "baked_png_atlas_compositor_sidecar.v1",
        "backend": "baked_png_atlas_compositor",
        "visual_key": stem,
        "source_xp": rel_source_norm,
        "source_xp_sha256": sha256_file(xp_path),
        "atlas_path": rel_atlas_norm,
        "atlas_png": rel_atlas_norm,
        "atlas_png_sha256": sha256_file(png_path),
        "family": family,
        "cell_scale_px": scale,
        "actor_pixel_size_world": ACTOR_CELL_SIZE_WORLD / float(scale),
        "legacy_frame_ref_half_cells": [
            geom["frame_w_cells"],
            geom["y_proj"],
            -geom["z_proj"],
        ],
        **render_info,
        **geom,
        "frame_w_px": geom["frame_w_cells"] * scale,
        "frame_h_px": geom["frame_h_cells"] * scale,
        "anim_segments": build_segments(family, geom["anim_lens"]),
        "compositor_frames": build_compositor_frames(
            geom,
            scale,
            family,
            source_cell_payloads,
            layer_role=layer_role,
            layer_order=layer_order,
            presentation_kinds=presentation_kinds,
        ),
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2, sort_keys=False)
        f.write("\n")

    return png_path, json_path


def normalize_billboard_frame(sidecar_arg: str) -> Path:
    """Position an existing atlas crop on the full-frame billboard axis."""
    sidecar_path = Path(sidecar_arg)
    if not sidecar_path.is_absolute():
        sidecar_path = REPO_ROOT / sidecar_path
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if sidecar.get("backend") != "baked_png_atlas_compositor":
        raise SystemExit(f"{sidecar_path}: unsupported actor sidecar backend")
    frame_w = int(sidecar["frame_w_px"])
    frame_h = int(sidecar["frame_h_px"])
    frames = sidecar.get("compositor_frames", [])
    if not isinstance(frames, list) or not frames:
        raise SystemExit(f"{sidecar_path}: compositor_frames missing")
    sidecar.pop("billboard_yaw_axis_offsets_px", None)
    for frame in frames:
        frame_index = int(frame.get("frame", -1))
        yaw = int(frame.get("yaw", -1))
        if frame_index < 0 or yaw < 0:
            raise SystemExit(f"{sidecar_path}: frame identity missing")
        frame_rect = frame.get("frame_rect_px", [])
        if not isinstance(frame_rect, list) or len(frame_rect) < 4:
            raise SystemExit(f"{sidecar_path}: frame rectangle missing")
        crop_x = int(frame_rect[0]) - frame_index * frame_w
        crop_y = int(frame_rect[1]) - yaw * frame_h
        frame["anchor_ref_px"] = [
            frame_w * 0.5 - crop_x,
            float(frame_h - crop_y),
        ]
        frame.pop("contact_anchor_px", None)
    sidecar_path.write_text(
        json.dumps(sidecar, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return sidecar_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("xp_paths", nargs="*", help="XP file paths (relative to repo root or absolute)")
    p.add_argument(
        "--existing-sidecar",
        action="append",
        default=[],
        help="Rebuild an existing sidecar while preserving its output stem, layer, role, and presentation set",
    )
    p.add_argument(
        "--normalize-billboard-frame",
        action="append",
        default=[],
        help="Position an actor atlas crop on its full-frame bottom-centre pivot",
    )
    p.add_argument("--scale", type=int, default=DEFAULT_SCALE, help=f"Cell scale in pixels (default {DEFAULT_SCALE})")
    p.add_argument("--output-stem", default="", help="Override output PNG/JSON stem for a single XP input")
    p.add_argument("--source-layer-index", type=int, default=None, help="Render exactly one XP layer into the baked PNG")
    p.add_argument("--layer-role", default="body", help="Layer role written into compositor frame rows")
    p.add_argument("--layer-order", type=int, default=0, help="Layer order written into compositor frame rows")
    p.add_argument("--presentation-kind", action="append", default=[], help="Server presentation kind supported by this sidecar; repeatable")
    args = p.parse_args(argv)

    if (
        not args.xp_paths
        and not args.existing_sidecar
        and not args.normalize_billboard_frame
    ):
        raise SystemExit("provide an XP path or --existing-sidecar")

    if args.output_stem and len(args.xp_paths) != 1:
        raise SystemExit("--output-stem requires exactly one XP input")

    for sidecar_arg in args.normalize_billboard_frame:
        path = normalize_billboard_frame(sidecar_arg)
        print(f"billboard-normalized: {path.relative_to(REPO_ROOT)}")

    for sidecar_arg in args.existing_sidecar:
        sidecar_path = Path(sidecar_arg)
        if not sidecar_path.is_absolute():
            sidecar_path = REPO_ROOT / sidecar_path
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        frames = data.get("compositor_frames", [])
        presentations = sorted({
            str(frame.get("presentation_kind", ""))
            for frame in frames if isinstance(frame, dict)
            and str(frame.get("presentation_kind", ""))
        })
        roles = {
            str(frame.get("layer_role", "body"))
            for frame in frames if isinstance(frame, dict)
        }
        orders = {
            int(frame.get("layer_order", 0))
            for frame in frames if isinstance(frame, dict)
        }
        if len(roles) != 1 or len(orders) != 1:
            raise SystemExit(
                f"{sidecar_path}: expected one layer role and order, got {roles} / {orders}"
            )
        png_path, json_path = bake(
            str(data["source_xp"]),
            scale=int(data.get("cell_scale_px", DEFAULT_SCALE)),
            output_stem=str(data.get("visual_key", sidecar_path.stem)),
            source_layer_index=data.get("source_layer_index"),
            layer_role=next(iter(roles)),
            layer_order=next(iter(orders)),
            presentation_kinds=presentations,
        )
        print(f"baked: {png_path.relative_to(REPO_ROOT)} + {json_path.name}")

    for xp_arg in args.xp_paths:
        png_path, json_path = bake(
            xp_arg,
            scale=args.scale,
            output_stem=args.output_stem or None,
            source_layer_index=args.source_layer_index,
            layer_role=args.layer_role,
            layer_order=args.layer_order,
            presentation_kinds=args.presentation_kind or None,
        )
        print(f"baked: {png_path.relative_to(REPO_ROOT)} + {json_path.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
