#!/usr/bin/env python3
"""xp_anim_viewer.py — full-frame, frame-by-frame XP animation viewer.

This viewer is a teaching tool for the bundle refactor, not just a sprite
browser. It shows raw XP frames at real cell dimensions, and in compare modes
it explains how those sheets participate in the compiled appearance bundle.

Usage:
    python3 scripts/pipeline/xp_anim_viewer.py [PATTERN]
    python3 scripts/pipeline/xp_anim_viewer.py ATTACK_4TEST --compare-default
    python3 scripts/pipeline/xp_anim_viewer.py --compare-default
    python3 scripts/pipeline/xp_anim_viewer.py --compare-default-random --seed 7

When `--compare-default` is enabled, the left side is the selected test or
fixture sprite and the right side is the closest traditional/default XP
counterpart. When `--compare-default-random` is enabled, the left side is a
random bundle-shaped test composition (test body + test wearables only) and
the right side is a random traditional/default composition (default body +
normal wearables only).

Walkthrough and callgraph:
Step 0. A raw XP file exists on disk in `assets/sprites/`.
Step 1. ActorVisualProfile source declares what the XP is: profile, slot,
presentation family, style, and variation.
Step 2. `scripts/compile_actor_visual_profiles.py` validates the profile
contract. Legacy appearance-bundle catalog validation is retired by FL-4049.
Step 3. The compiler emits the generated ActorVisualProfile table.
Step 4. The authoritative server chooses `skin_definition_id`,
`mount_definition_id`, and equipped slot entries.
Step 5. The server sends `presentation_kind_id` plus `appearance_v2` to the
client.
Step 6. The client stores those IDs and, at render time, resolves the active
selector/presentation family from runtime state.
Step 7. The renderer resolves body, mount, and item layers by
owner/slot/presentation/style/variant.
Step 8. The renderer orders those layers and composes the final sprite.
Step 9. A frame inside that composed sprite is selected and drawn.

This script mostly inspects Steps 0-3 directly and mirrors Step 8 in a limited
way when it stacks overlay sheets in compare mode.

Viewer callgraph:
- run()                                                [viewer entry point]
- _build_view_pairs()                                  [Steps 0-3 teaching surface]
- _render_screen()                                     [Step 9 teaching surface]
- _build_subject_frame_actual()                        [Step 8 preview composition]
- _build_subject_frame_by_index()                      [Step 8 compare sync]
- _subject_educational_fields()                        [Steps 1-3 glossary matrix]
- build_frame_actual() / build_frame_by_index()        [raw XP frame extraction]

Alphabetical glossary:
- anchor_mode: the sheet-layout contract's anchor interpretation mode such as
  `character` or `mount_character`.
- appearance_v2: the authoritative appearance payload containing body owner,
  mount owner, and equipped slot entries.
- asset layout contract: the declared XP sheet family such as
  `idle_walk_character` or `attack_mount`.
- attachment order: the bundle-defined slot paint order for a presentation.
- item_definition_id: gameplay item identity and render-owner key for item
  layers.
- mount_definition_id: authoritative mount-owner identity for mount layers.
- owner_definition_kind: which owner namespace a layer belongs to: `skin`,
  `item`, or `mount`.
- presentation_kind_id: the actor's current render verb/state family such as
  `idle_walk`, `attack`, or `plydie`. It is not an outfit combination.
- row1_refs: layer-0 row-1 alignment/projection metadata extracted by the
  compiler.
- row2_refs: layer-0 row-2 depth/secondary alignment metadata extracted by the
  compiler.
- selector input contract: the runtime-state mask and variant fallback rules
  that activate a presentation family.
- skin_definition_id: the authoritative body-owner family such as `cyan_suit`
  or `normal_player`. It is not a body part.
- slot manifest: informal term for the `appearance_v2.entries[]` equipped-slot
  list. Each entry carries `slot_kind_id + item_definition_id + visual_style_id`.
- slot_kind_id: the attachment lane such as `body`, `head`, `weapon`,
  `shield`, `armor`, or `mount`.
- variant_signature: the geometry tuple
  `(height_class, width_class, silhouette_class)`.
- visual_style_id: style/color lane such as `default`, `gold`, or `dark`.

Abstraction hierarchy:
- Runtime entity
- Subject kind
- Authoritative appearance state
- Current runtime state
- Selector / presentation family
- Desired variant signature
- Owner namespaces (`skin`, `item`, `mount`)
- Ordered layer stack
- Composed sprite
- Final frame on screen

Controls:
    ← / →       previous / next sprite
    a / d       rotate angle  (8 steps for standard sprites)
    w / s       previous / next animation track
    , / .       step one frame backward / forward  (pauses autoplay)
    Space       toggle autoplay
    0           jump to frame 0  (useful for plydie corpse-clamp check)
    q / Esc     quit
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import random
import select
import signal
import shutil
import sys
import termios
import threading
import time
import tty
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.xp_assets_browser_layer_2_only import (
    SWOOSH_RGB,
    TRANSPARENT_RGB,
    BrowserState,
    PreviewCell,
    SpriteAsset,
    SpriteEntry,
    SpriteMetadata,
    _normalize_preview_cell,
    _parse_metadata,
    _quantize_preview_rgb,
    _load_xp_quiet,
    _select_frame,
    _style_cell,
    load_sprite_asset,
    scan_sprite_entries,
)

# Angle names parallel to engine canonical order (South first).
ANGLE_NAMES = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]

# ── FL-2345 combination-check constants ──────────────────────────────────
SLOT_LABELS = {
    "head": "Head wearable",
    "armor": "Armour wearable",
    "weapon": "Weapon",
    "shield": "Shield",
    "mount": "Mountable",
}
SLOT_ORDER = ["head", "armor", "weapon", "shield", "mount"]
DEFAULT_ATTACHMENT_ORDER = ["body", "armor", "shield", "weapon", "head"]

KEY_ESCAPE = "\x1b"
KEY_SPACE = " "
KEY_LEFT = "LEFT"
KEY_RIGHT = "RIGHT"
KEY_UP = "UP"
KEY_DOWN = "DOWN"
KEY_PAGEUP = "PAGEUP"
KEY_PAGEDOWN = "PAGEDOWN"

SPRITE_DIR = REPO_ROOT / "assets" / "sprites"
TEST_SPRITE_DIR = REPO_ROOT / "assets" / "test_xps"
ORIGINAL_SPRITE_COPY_DIR = TEST_SPRITE_DIR / "originals"
APPEARANCE_BUNDLE_PATH = REPO_ROOT / "assets" / "appearance_bundle" / "current" / "appearance_bundle.json"
_BUNDLE_ASSET_INDEX: dict[str, list[dict[str, object]]] | None = None
_RAW_ASSET_INFO: dict[Path, dict[str, object]] = {}
_VIEWER_ENTRY_CACHE: dict[tuple[str, ...], list[SpriteEntry]] = {}

DEFAULT_COMPARE_SPRITE_BY_PREFIX = {
    "attack": "attack-body.xp",
    "plydie": "plydie-body.xp",
    "player": "player-body.xp",
}


@dataclass(frozen=True)
class ViewSubject:
    name: str
    base_entry: SpriteEntry
    overlay_entries: tuple[SpriteEntry, ...] = ()


@dataclass(frozen=True)
class ViewPair:
    left: ViewSubject
    right: ViewSubject | None = None


@dataclass
class PanelState:
    show_details: bool = True
    scroll: int = 0


# ---------------------------------------------------------------------------
# Full-frame extraction (no 16×16 crop)
# ---------------------------------------------------------------------------

def build_frame_actual(
    asset: SpriteAsset,
    state: BrowserState,
    time_tick: int,
) -> tuple[list[list[PreviewCell]], int, int]:
    """Return the full frame at actual cell dimensions, plus (angle, frame_idx).

    Unlike build_preview_cells (which crops to 16×16), this returns every cell
    of the selected frame so you can see the per-cell encoded glyphs.
    """
    meta = asset.entry.meta
    atlas_idx, angle, frame_idx = _select_frame(meta, state, time_tick)
    fr_x = atlas_idx % meta.fr_num_x
    fr_y = atlas_idx // meta.fr_num_x
    x0 = fr_x * meta.fr_width
    y0 = fr_y * meta.fr_height

    rows: list[list[PreviewCell]] = []
    for fy in range(meta.fr_height):
        row: list[PreviewCell] = []
        for fx in range(meta.fr_width):
            glyph, fg_rgb, bg_rgb = asset.merged_visual[y0 + fy][x0 + fx]
            key_rgb = asset.color_key[y0 + fy][x0 + fx][2]

            if fg_rgb == SWOOSH_RGB:
                fg = SWOOSH_RGB
            elif fg_rgb == key_rgb:
                fg = None
            elif bg_rgb == TRANSPARENT_RGB and glyph == 32:
                # fully transparent (space + transparent bg) — kill fg too
                fg = None
            else:
                # half-block glyph on transparent bg keeps its fg color
                fg = _quantize_preview_rgb(fg_rgb)

            if bg_rgb == SWOOSH_RGB:
                bg = SWOOSH_RGB
            elif bg_rgb == TRANSPARENT_RGB or bg_rgb == key_rgb:
                bg = None
            else:
                bg = _quantize_preview_rgb(bg_rgb)

            row.append(_normalize_preview_cell(glyph, fg, bg))
        rows.append(row)

    return rows, angle, frame_idx


def build_frame_by_index(
    asset: SpriteAsset,
    anim: int,
    angle: int,
    frame_idx: int,
) -> list[list[PreviewCell]]:
    """Return an exact full frame selected by anim/angle/frame indexes."""
    meta = asset.entry.meta
    anim = min(max(anim, 0), len(meta.anim_lengths) - 1)
    angle = angle % max(1, meta.angles)
    frame_idx = frame_idx % meta.anim_lengths[anim]
    frame_base = sum(meta.anim_lengths[:anim])
    atlas_idx = frame_base + frame_idx + angle * meta.fr_num_x
    fr_x = atlas_idx % meta.fr_num_x
    fr_y = atlas_idx // meta.fr_num_x
    x0 = fr_x * meta.fr_width
    y0 = fr_y * meta.fr_height

    rows: list[list[PreviewCell]] = []
    for fy in range(meta.fr_height):
        row: list[PreviewCell] = []
        for fx in range(meta.fr_width):
            glyph, fg_rgb, bg_rgb = asset.merged_visual[y0 + fy][x0 + fx]
            key_rgb = asset.color_key[y0 + fy][x0 + fx][2]

            if fg_rgb == SWOOSH_RGB:
                fg = SWOOSH_RGB
            elif fg_rgb == key_rgb:
                fg = None
            elif bg_rgb == TRANSPARENT_RGB and glyph == 32:
                # fully transparent (space + transparent bg) — kill fg too
                fg = None
            else:
                # half-block glyph on transparent bg keeps its fg color
                fg = _quantize_preview_rgb(fg_rgb)

            if bg_rgb == SWOOSH_RGB:
                bg = SWOOSH_RGB
            elif bg_rgb == TRANSPARENT_RGB or bg_rgb == key_rgb:
                bg = None
            else:
                bg = _quantize_preview_rgb(bg_rgb)

            row.append(_normalize_preview_cell(glyph, fg, bg))
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _angle_label(meta: SpriteMetadata, angle: int) -> str:
    if meta.angles == 8 and angle < len(ANGLE_NAMES):
        return ANGLE_NAMES[angle]
    return str(angle)


def _render_frame_lines(
    rows: list[list[PreviewCell]],
    border_top: str,
    border_bot: str,
) -> list[str]:
    lines = [border_top]
    for row in rows:
        lines.append("  " + "".join(_style_cell(c) for c in row))
    lines.append(border_bot)
    return lines


def _render_compare_frame_lines(
    left_rows: list[list[PreviewCell]],
    right_rows: list[list[PreviewCell]],
    left_width: int,
    right_width: int,
) -> list[str]:
    left_border = "\033[2m+" + "-" * left_width + "+\033[0m"
    right_border = "\033[2m+" + "-" * right_width + "+\033[0m"
    lines = [f"  {left_border}    {right_border}"]
    total_rows = max(len(left_rows), len(right_rows))
    for row_idx in range(total_rows):
        if row_idx < len(left_rows):
            left = "".join(_style_cell(c) for c in left_rows[row_idx])
        else:
            left = " " * left_width
        if row_idx < len(right_rows):
            right = "".join(_style_cell(c) for c in right_rows[row_idx])
        else:
            right = " " * right_width
        lines.append(f"  {left}    {right}")
    lines.append(f"  {left_border}    {right_border}")
    return lines


def _animation_prefix_for(sprite_name: str) -> str:
    upper = sprite_name.upper()
    if upper.startswith("WOLACK-"):
        return "attack"
    if "ATTACK" in upper:
        return "attack"
    if "PLYDIE" in upper:
        return "plydie"
    return "player"


def _default_compare_candidates(sprite_name: str) -> list[str]:
    upper = sprite_name.upper()
    prefix = _animation_prefix_for(sprite_name)
    candidates: list[str] = []
    if "WOLF_MOUNTABLE" in upper:
        if "ATTACK" in upper:
            candidates.append("wolack-body.xp")
        else:
            candidates.append("wolfie-body.xp")
    if "BEE_MOUNTABLE" in upper:
        if "ATTACK" in upper:
            candidates.append("bigbee-attack-body.xp")
        else:
            candidates.append("bigbee-mount-body.xp")
    if "WOLFIE" in upper:
        if "ATTACK" in upper:
            candidates.append("wolack-body.xp")
        else:
            candidates.append("wolfie-body.xp")
    if "BIGBEE" in upper:
        if "ATTACK" in upper:
            candidates.append("bigbee-attack-body.xp")
        else:
            candidates.append("bigbee-mount-body.xp")
    if "GOLD_HAT" in upper:
        candidates.extend([
            f"{prefix}-helmet-gold.xp",
            f"{prefix}-helmet-regular.xp",
        ])
    if "SHIELD" in upper:
        candidates.append(f"{prefix}-shield-regular.xp")
    if "WEAPON" in upper or "SWORD" in upper:
        candidates.append(f"{prefix}-weapon-sword.xp")
    if "CYAN_SUIT" in upper:
        candidates.append(f"{prefix}-body.xp")
    candidates.append(DEFAULT_COMPARE_SPRITE_BY_PREFIX[prefix])
    candidates.append("player-body.xp")

    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _default_compare_name(sprite_name: str) -> str:
    return _default_compare_candidates(sprite_name)[0]


def _compare_anim_for(primary_name: str, compare_meta: SpriteMetadata, state_anim: int) -> int:
    upper = primary_name.upper()
    if "ATTACK" in upper or "PLYDIE" in upper:
        return 0
    if 0 <= state_anim < len(compare_meta.anim_lengths):
        return state_anim
    return 0


def _pattern_terms(pattern: str) -> list[str]:
    return [
        term.strip().lower()
        for term in pattern.replace("|", ",").split(",")
        if term.strip()
    ]


def _pattern_term_matches_name(term: str, name: str) -> bool:
    if term in name:
        return True
    tokens = [token for token in term.replace("-", "_").split("_") if token]
    if len(tokens) <= 1:
        return False
    pos = 0
    for token in tokens:
        idx = name.find(token, pos)
        if idx < 0:
            return False
        pos = idx + len(token)
    return True


def _viewer_sprite_dirs(sprite_dir: Path) -> tuple[Path, ...]:
    if sprite_dir.resolve() == SPRITE_DIR.resolve():
        return (SPRITE_DIR, TEST_SPRITE_DIR)
    return (sprite_dir,)


def scan_viewer_sprite_entries(sprite_dir: Path = SPRITE_DIR) -> list[SpriteEntry]:
    roots = _viewer_sprite_dirs(sprite_dir)
    cache_key = tuple(str(root.resolve()) for root in roots)
    cached = _VIEWER_ENTRY_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    entries: list[SpriteEntry] = []
    for root in roots:
        if root.exists():
            entries.extend(scan_sprite_entries(root))
    by_name: dict[str, SpriteEntry] = {}
    for entry in entries:
        by_name.setdefault(entry.name.lower(), entry)
    sorted_entries = sorted(by_name.values(), key=lambda entry: entry.name.lower())
    _VIEWER_ENTRY_CACHE[cache_key] = sorted_entries
    return list(sorted_entries)


def _find_matching_paths(sprite_dir: Path, pattern: str) -> list[Path]:
    terms = _pattern_terms(pattern)
    by_name: dict[str, Path] = {}
    for root in _viewer_sprite_dirs(sprite_dir):
        if not root.exists():
            continue
        for path in sorted(root.glob("*.xp"), key=lambda item: item.name.lower()):
            by_name.setdefault(path.name.lower(), path)
    paths = sorted(by_name.values(), key=lambda item: item.name.lower())
    if not terms:
        return paths
    matched: list[Path] = []
    for path in paths:
        name = path.name.lower()
        if any(_pattern_term_matches_name(term, name) for term in terms):
            matched.append(path)
    return matched


def _filter_entries(entries: list[SpriteEntry], pattern: str) -> list[SpriteEntry]:
    terms = _pattern_terms(pattern)
    if not terms:
        return list(entries)
    matched: list[SpriteEntry] = []
    for entry in entries:
        name = entry.name.lower()
        if any(_pattern_term_matches_name(term, name) for term in terms):
            matched.append(entry)
    return matched


def _entry_from_path(path: Path) -> SpriteEntry | None:
    try:
        xp = _load_xp_quiet(path)
        meta = _parse_metadata(xp)
    except Exception:
        return None
    if meta is None:
        return None
    return SpriteEntry(path=path, name=path.name, meta=meta)


def _get_asset(cache: dict[Path, SpriteAsset], entry: SpriteEntry) -> SpriteAsset:
    if entry.path not in cache:
        cache[entry.path] = load_sprite_asset(entry)
    return cache[entry.path]


def _compose_preview_rows(
    base_rows: list[list[PreviewCell]],
    overlay_rows: list[list[PreviewCell]],
) -> list[list[PreviewCell]]:
    height = min(len(base_rows), len(overlay_rows))
    width = min(len(base_rows[0]) if base_rows else 0, len(overlay_rows[0]) if overlay_rows else 0)
    out = [list(row) for row in base_rows]
    for y in range(height):
        for x in range(width):
            cell = overlay_rows[y][x]
            if cell.fg is None and cell.bg is None and cell.glyph == 32:
                continue
            out[y][x] = cell
    return out


def _build_subject_frame_actual(
    subject: ViewSubject,
    state: BrowserState,
    tick: int,
    cache: dict[Path, SpriteAsset],
) -> tuple[list[list[PreviewCell]], int, int]:
    base_asset = _get_asset(cache, subject.base_entry)
    rows, angle, frame_idx = build_frame_actual(base_asset, state, tick)
    anim = min(max(state.anim, 0), len(subject.base_entry.meta.anim_lengths) - 1)
    for overlay_entry in subject.overlay_entries:
        overlay_asset = _get_asset(cache, overlay_entry)
        overlay_anim = _compare_anim_for(subject.base_entry.name, overlay_entry.meta, anim)
        overlay_rows = build_frame_by_index(overlay_asset, overlay_anim, angle, frame_idx)
        rows = _compose_preview_rows(rows, overlay_rows)
    return rows, angle, frame_idx


def _build_subject_frame_by_index(
    subject: ViewSubject,
    reference_name: str,
    anim: int,
    angle: int,
    frame_idx: int,
    cache: dict[Path, SpriteAsset],
) -> list[list[PreviewCell]]:
    base_asset = _get_asset(cache, subject.base_entry)
    subject_anim = _compare_anim_for(reference_name, subject.base_entry.meta, anim)
    rows = build_frame_by_index(base_asset, subject_anim, angle, frame_idx)
    for overlay_entry in subject.overlay_entries:
        overlay_asset = _get_asset(cache, overlay_entry)
        overlay_anim = _compare_anim_for(reference_name, overlay_entry.meta, anim)
        overlay_rows = build_frame_by_index(overlay_asset, overlay_anim, angle, frame_idx)
        rows = _compose_preview_rows(rows, overlay_rows)
    return rows


def _subject_overlay_summary(subject: ViewSubject) -> str:
    if not subject.overlay_entries:
        return "-"
    return ", ".join(entry.name for entry in subject.overlay_entries)


def _infer_presentation_kind_slug(sprite_name: str) -> str:
    prefix = _animation_prefix_for(sprite_name)
    if prefix == "attack":
        return "attack"
    if prefix == "plydie":
        return "plydie"
    return "idle_walk"


def _infer_slot_kind_slug(sprite_name: str) -> str:
    upper = sprite_name.upper()
    if "HAT" in upper or "HELMET" in upper:
        return "head"
    if "SHIELD" in upper:
        return "shield"
    if "WEAPON" in upper or "SWORD" in upper:
        return "weapon"
    if "ARMOUR" in upper or "ARMOR" in upper:
        return "armor"
    if "MOUNTABLE_ITEM_WORLD" in upper:
        return "world_item"
    if "MOUNTABLE_GRID" in upper or "GRID" in upper:
        return "inventory_item"
    return "body"


def _body_family_prefix(sprite_name: str) -> str:
    lower = sprite_name.lower()
    if lower.startswith("bigbee-"):
        return "bigbee"
    if lower.startswith("wolfie-"):
        return "wolfie"
    if lower.startswith("wolack-"):
        return "wolack"
    if lower.startswith("player-"):
        return "player"
    if lower.startswith("attack-"):
        return "attack"
    if lower.startswith("plydie-"):
        return "plydie"
    return lower.split("-", 1)[0]


def _load_bundle_asset_index() -> dict[str, list[dict[str, object]]]:
    global _BUNDLE_ASSET_INDEX
    if _BUNDLE_ASSET_INDEX is not None:
        return _BUNDLE_ASSET_INDEX
    if not APPEARANCE_BUNDLE_PATH.exists():
        _BUNDLE_ASSET_INDEX = {}
        return _BUNDLE_ASSET_INDEX
    bundle = json.loads(APPEARANCE_BUNDLE_PATH.read_text(encoding="utf-8"))
    index: dict[str, list[dict[str, object]]] = {}
    for entry in bundle.get("catalog", {}).get("layer_definitions", []):
        asset = entry.get("asset", {})
        path = asset.get("path")
        if not isinstance(path, str):
            continue
        key = Path(path).name.lower()
        index.setdefault(key, []).append(
            {
                "record_kind": "layer",
                "slug": entry.get("slug", "-"),
                "owner_kind": entry.get("owner_definition_kind", "-"),
                "owner_slug": entry.get("owner_definition_slug", "-"),
                "owner_id": entry.get("owner_definition_id", "-"),
                "presentation_kind_slug": entry.get("presentation_kind_slug", "-"),
                "presentation_kind_id": entry.get("presentation_kind_id", "-"),
                "slot_kind_slug": entry.get("slot_kind_slug", "-"),
                "slot_kind_id": entry.get("slot_kind_id", "-"),
                "visual_style_slug": entry.get("visual_style_slug", "default"),
                "visual_style_id": entry.get("visual_style_id", "-"),
                "variant_signature": entry.get("variant_signature"),
                "contract": asset.get("contract", "-"),
                "frame_size": asset.get("frame_size"),
                "sheet_size": asset.get("sheet_size"),
                "row1_refs": asset.get("row1_refs"),
                "row2_refs": asset.get("row2_refs"),
            }
        )
    _BUNDLE_ASSET_INDEX = index
    return _BUNDLE_ASSET_INDEX


def _format_variant_signature(value: object) -> str:
    if not isinstance(value, dict):
        return "-"
    return "/".join(
        str(value.get(key, "-"))
        for key in ("height_class", "width_class", "silhouette_class")
    )


def _format_int_list(value: object) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(str(item) for item in value) + "]"
    return "-"


def _format_size(value: object) -> str:
    if isinstance(value, dict):
        width = value.get("width", "-")
        height = value.get("height", "-")
        return f"{width}x{height}"
    return "-"


def _decode_digit(glyph: int) -> int | None:
    if 48 <= glyph <= 57:
        return glyph - 48
    if 65 <= glyph <= 90:
        return glyph + 10 - 65
    if 97 <= glyph <= 122:
        return glyph + 10 - 97
    return None


def _read_raw_asset_info(entry: SpriteEntry) -> dict[str, object]:
    cached = _RAW_ASSET_INFO.get(entry.path)
    if cached is not None:
        return cached

    info: dict[str, object] = {
        "row1_refs": "-",
        "row2_refs": "-",
        "frame_size": {"width": entry.meta.fr_width, "height": entry.meta.fr_height},
        "sheet_size": "-",
    }
    try:
        xp = _load_xp_quiet(entry.path)
        layer0 = xp.layers[0]
        row1: list[int] = []
        row2: list[int] = []
        for x in range(min(2, layer0.width)):
            d1 = _decode_digit(layer0.data[1][x][0]) if layer0.height > 1 else None
            d2 = _decode_digit(layer0.data[2][x][0]) if layer0.height > 2 else None
            if d1 is not None:
                row1.append(d1)
            if d2 is not None:
                row2.append(d2)
        if row1:
            info["row1_refs"] = row1
        if row2:
            info["row2_refs"] = row2
        visual = xp.layers[2] if len(xp.layers) > 2 else layer0
        info["sheet_size"] = {"width": visual.width, "height": visual.height}
    except Exception:
        pass

    _RAW_ASSET_INFO[entry.path] = info
    return info


def _infer_contract_and_anchor_mode(subject: ViewSubject) -> tuple[str, str]:
    slot = _infer_slot_kind_slug(subject.base_entry.name)
    presentation = _infer_presentation_kind_slug(subject.base_entry.name)
    if slot in {"world_item", "inventory_item"}:
        return "-", "none"
    if presentation == "plydie":
        return "plydie_character", "character"
    if presentation == "attack":
        if slot == "mount":
            return "attack_mount", "mount_character"
        return "attack_character", "character"
    if presentation == "idle_walk":
        if slot == "mount":
            return "idle_walk_mount", "mount_character"
        return "idle_walk_character", "character"
    return "-", "-"


def _subject_source_of_truth(subject: ViewSubject) -> str:
    bundle_records = _load_bundle_asset_index().get(subject.base_entry.name.lower(), [])
    return "compiled_bundle" if bundle_records else "viewer_inference"


def _definition_lines_for_pair(pair: ViewPair) -> list[str]:
    left_source = _subject_source_of_truth(pair.left)
    right_source = _subject_source_of_truth(pair.right) if pair.right is not None else "-"
    lines = [
        "",
        "  \033[1mDefinitions\033[0m",
        "  presentation_kind_id = render verb/state family, not outfit or camera angle",
        "  owner_definition_kind = namespace that owns this layer: skin, item, or mount",
        "  slot_kind_id = compositing lane this layer occupies: body/head/shield/weapon/armor/mount",
        "  variant_signature = geometry tuple height_class/width_class/silhouette_class",
        "  contract = declared XP sheet-layout family the compiler validates against",
        "  row1_refs / row2_refs = layer0 alignment metadata used for projection/depth matching",
        f"  source_of_truth = left:{left_source} right:{right_source}",
    ]
    if pair.right is not None:
        shared_slots = sorted(
            set(_infer_slot_kind_slug(entry.name) for entry in pair.left.overlay_entries)
            & set(_infer_slot_kind_slug(entry.name) for entry in pair.right.overlay_entries)
        )
        shared_slot_text = ", ".join(shared_slots) if shared_slots else "-"
        overlay_policy = (
            "left=test fixtures, right=normal/default fixtures"
            if pair.left.overlay_entries or pair.right.overlay_entries
            else "body-only compare"
        )
        lines.extend(
            [
                "  compare_reason = pair test fixture content against nearest default/traditional counterpart",
                f"  overlay_policy = {overlay_policy}",
                f"  shared_slots = {shared_slot_text}",
            ]
        )
    return lines


def _subject_stack_entries(subject: ViewSubject) -> tuple[SpriteEntry, ...]:
    return (subject.base_entry,) + subject.overlay_entries


def _frame_rect_for_entry(
    entry: SpriteEntry,
    reference_name: str,
    anim: int,
    angle: int,
    frame_idx: int,
) -> str:
    meta = entry.meta
    subject_anim = _compare_anim_for(reference_name, meta, anim)
    subject_anim = min(max(subject_anim, 0), len(meta.anim_lengths) - 1)
    subject_angle = angle % max(1, meta.angles)
    subject_frame = frame_idx % meta.anim_lengths[subject_anim]
    frame_base = sum(meta.anim_lengths[:subject_anim])
    atlas_idx = frame_base + subject_frame + subject_angle * meta.fr_num_x
    fr_x = atlas_idx % meta.fr_num_x
    fr_y = atlas_idx // meta.fr_num_x
    x0 = fr_x * meta.fr_width
    y0 = fr_y * meta.fr_height
    sheet_w = meta.fr_num_x * meta.fr_width
    sheet_h = meta.fr_num_y * meta.fr_height
    bl_x = x0
    bl_y = sheet_h - (y0 + meta.fr_height)
    tr_x = x0 + meta.fr_width - 1
    tr_y = sheet_h - y0 - 1
    return f"bl({bl_x},{bl_y}) tr({tr_x},{tr_y})"


def _subject_stack_summary(subject: ViewSubject) -> str:
    return " + ".join(entry.name for entry in _subject_stack_entries(subject))


def _subject_frame_rect_summary(
    subject: ViewSubject,
    reference_name: str,
    anim: int,
    angle: int,
    frame_idx: int,
) -> str:
    parts = []
    for entry in _subject_stack_entries(subject):
        rect = _frame_rect_for_entry(entry, reference_name, anim, angle, frame_idx)
        parts.append(f"{entry.name}: {rect}")
    return " ; ".join(parts)


def _subject_stack_detail_lines(
    label: str,
    subject: ViewSubject,
    reference_name: str,
    anim: int,
    angle: int,
    frame_idx: int,
) -> list[str]:
    lines = [f"  \033[1m{label} Source Sheets\033[0m"]
    for idx, entry in enumerate(_subject_stack_entries(subject), start=1):
        rect = _frame_rect_for_entry(entry, reference_name, anim, angle, frame_idx)
        lines.append(f"  {label.lower()}[{idx}] {entry.name}")
        lines.append(f"      frame_rect = {rect}")
    return lines


def _subject_educational_fields(subject: ViewSubject) -> dict[str, str]:
    bundle_records = _load_bundle_asset_index().get(subject.base_entry.name.lower(), [])
    raw_info = _read_raw_asset_info(subject.base_entry)
    if bundle_records:
        record = bundle_records[0]
        contract = str(record.get("contract", "-"))
        anchor_mode = "mount_character" if contract.endswith("_mount") else ("character" if contract != "-" else "-")
        return {
            "asset": subject.base_entry.name,
            "record": str(record.get("slug", "-")),
            "owner_kind": str(record.get("owner_kind", "-")),
            "owner_slug": str(record.get("owner_slug", "-")),
            "owner_id": str(record.get("owner_id", "-")),
            "presentation_slug": str(record.get("presentation_kind_slug", "-")),
            "presentation_id": str(record.get("presentation_kind_id", "-")),
            "slot_slug": str(record.get("slot_kind_slug", "-")),
            "slot_id": str(record.get("slot_kind_id", "-")),
            "style_slug": str(record.get("visual_style_slug", "-")),
            "style_id": str(record.get("visual_style_id", "-")),
            "variant": _format_variant_signature(record.get("variant_signature")),
            "contract": contract,
            "anchor_mode": anchor_mode,
            "row1_refs": _format_int_list(record.get("row1_refs")),
            "row2_refs": _format_int_list(record.get("row2_refs")),
            "frame_size": _format_size(record.get("frame_size")),
            "sheet_size": _format_size(record.get("sheet_size")),
            "source_of_truth": "compiled_bundle",
        }
    contract, anchor_mode = _infer_contract_and_anchor_mode(subject)
    return {
        "asset": subject.base_entry.name,
        "record": "fixture_only",
        "owner_kind": "fixture",
        "owner_slug": subject.base_entry.name,
        "owner_id": "-",
        "presentation_slug": _infer_presentation_kind_slug(subject.base_entry.name),
        "presentation_id": "-",
        "slot_slug": _infer_slot_kind_slug(subject.base_entry.name),
        "slot_id": "-",
        "style_slug": "fixture",
        "style_id": "-",
        "variant": "-",
        "contract": contract,
        "anchor_mode": anchor_mode,
        "row1_refs": _format_int_list(raw_info.get("row1_refs")),
        "row2_refs": _format_int_list(raw_info.get("row2_refs")),
        "frame_size": _format_size(raw_info.get("frame_size")),
        "sheet_size": _format_size(raw_info.get("sheet_size")),
        "source_of_truth": "viewer_inference",
    }


def _format_topology(meta: SpriteMetadata) -> str:
    return f"{meta.angles}a {meta.projs}p {list(meta.anim_lengths)} {meta.fr_width}x{meta.fr_height}"


def _entry_matches_pattern(entry: SpriteEntry, pattern: str) -> bool:
    terms = _pattern_terms(pattern)
    if not terms:
        return True
    name = entry.name.lower()
    return any(_pattern_term_matches_name(term, name) for term in terms)


def _is_test_sprite_name(name: str) -> bool:
    return "4TEST" in name.upper()


def _is_actor_body_entry(entry: SpriteEntry) -> bool:
    slot = _infer_slot_kind_slug(entry.name)
    presentation = _infer_presentation_kind_slug(entry.name)
    return (
        slot == "body"
        and presentation in {"idle_walk", "attack", "plydie"}
        and entry.meta.angles > 1
    )


def _topology_key(meta: SpriteMetadata) -> tuple[int, int, int, int]:
    return (meta.angles, meta.projs, meta.fr_width, meta.fr_height)


def _overlay_slot_sort_key(slot: str) -> int:
    order = {
        "armor": 0,
        "shield": 1,
        "weapon": 2,
        "head": 3,
    }
    return order.get(slot, 99)


def _default_right_subject_for_entry(
    entry: SpriteEntry,
    all_paths: list[Path],
    all_paths_by_name: dict[str, Path],
) -> ViewSubject | None:
    for wanted in _default_compare_candidates(entry.name):
        compare_path = all_paths_by_name.get(wanted.lower())
        if compare_path is None:
            compare_path = next(
                (
                    path for path in all_paths
                    if _pattern_term_matches_name(wanted.lower(), path.name.lower())
                ),
                None,
            )
        if compare_path is None:
            continue
        compare_entry = _entry_from_path(compare_path)
        if compare_entry is not None:
            return ViewSubject(compare_entry.name, compare_entry)
    return None


def _compare_subject_for_name(
    compare_name: str,
    all_paths: list[Path],
    all_paths_by_name: dict[str, Path],
) -> ViewSubject | None:
    if not compare_name:
        return None
    compare_path = all_paths_by_name.get(compare_name.lower())
    if compare_path is None:
        compare_path = next(
            (
                path for path in all_paths
                if _pattern_term_matches_name(compare_name.lower(), path.name.lower())
            ),
            None,
        )
    if compare_path is None:
        return None
    compare_entry = _entry_from_path(compare_path)
    if compare_entry is None:
        return None
    return ViewSubject(compare_entry.name, compare_entry)


def _test_overlay_pool_for_entry(entry: SpriteEntry, all_entries: list[SpriteEntry]) -> dict[str, list[SpriteEntry]]:
    # Step 3/8 mirror: restrict overlay fixtures to the same presentation family
    # and frame topology so the compare view demonstrates plausible bundle stacks.
    wanted_topology = _topology_key(entry.meta)
    wanted_presentation = _infer_presentation_kind_slug(entry.name)
    pool: dict[str, list[SpriteEntry]] = {}
    for candidate in all_entries:
        if not _is_test_sprite_name(candidate.name):
            continue
        slot = _infer_slot_kind_slug(candidate.name)
        if slot not in {"head", "shield", "weapon", "armor"}:
            continue
        if _infer_presentation_kind_slug(candidate.name) != wanted_presentation:
            continue
        if _topology_key(candidate.meta) != wanted_topology:
            continue
        pool.setdefault(slot, []).append(candidate)
    return pool


def _normal_overlay_pool_for_subject(base_subject: ViewSubject, all_entries: list[SpriteEntry]) -> dict[str, list[SpriteEntry]]:
    wanted_topology = _topology_key(base_subject.base_entry.meta)
    wanted_presentation = _infer_presentation_kind_slug(base_subject.base_entry.name)
    wanted_family = _body_family_prefix(base_subject.base_entry.name)

    def collect(require_presentation: bool) -> dict[str, list[SpriteEntry]]:
        pool: dict[str, list[SpriteEntry]] = {}
        for candidate in all_entries:
            if _is_test_sprite_name(candidate.name):
                continue
            slot = _infer_slot_kind_slug(candidate.name)
            if slot not in {"head", "shield", "weapon", "armor"}:
                continue
            if _topology_key(candidate.meta) != wanted_topology:
                continue
            if require_presentation and _infer_presentation_kind_slug(candidate.name) != wanted_presentation:
                continue
            if not require_presentation and _body_family_prefix(candidate.name) != wanted_family:
                continue
            pool.setdefault(slot, []).append(candidate)
        return pool

    pool = collect(require_presentation=True)
    if pool:
        return pool
    return collect(require_presentation=False)


def _pick_random_overlay_entries(
    pool: dict[str, list[SpriteEntry]],
    rng: random.Random,
    chosen_slots: tuple[str, ...] | None = None,
) -> tuple[SpriteEntry, ...]:
    slots = list(chosen_slots) if chosen_slots is not None else sorted(pool.keys(), key=_overlay_slot_sort_key)
    if not slots:
        return ()
    if chosen_slots is None:
        chosen = [slot for slot in slots if rng.random() < 0.65]
        if not chosen:
            chosen = [rng.choice(slots)]
    else:
        chosen = slots
    overlays: list[SpriteEntry] = []
    for slot in chosen:
        candidates = pool.get(slot, [])
        if candidates:
            overlays.append(rng.choice(candidates))
    overlays.sort(key=lambda entry: _overlay_slot_sort_key(_infer_slot_kind_slug(entry.name)))
    return tuple(overlays)


def _build_plain_pairs(entries: list[SpriteEntry]) -> list[ViewPair]:
    return [ViewPair(ViewSubject(entry.name, entry)) for entry in entries]


def _build_default_compare_pairs(
    entries: list[SpriteEntry],
    all_paths: list[Path],
    all_paths_by_name: dict[str, Path],
    compare: str,
) -> list[ViewPair]:
    pairs: list[ViewPair] = []
    for entry in entries:
        left = ViewSubject(entry.name, entry)
        right = (
            _compare_subject_for_name(compare, all_paths, all_paths_by_name)
            if compare
            else _default_right_subject_for_entry(entry, all_paths, all_paths_by_name)
        )
        pairs.append(ViewPair(left=left, right=right))
    return pairs


def _build_random_compare_pairs(
    entries: list[SpriteEntry],
    all_entries: list[SpriteEntry],
    all_paths: list[Path],
    all_paths_by_name: dict[str, Path],
    rng: random.Random,
) -> list[ViewPair]:
    pairs: list[ViewPair] = []
    for entry in entries:
        if not _is_actor_body_entry(entry):
            continue
        left_pool = _test_overlay_pool_for_entry(entry, all_entries)
        right_base = _default_right_subject_for_entry(entry, all_paths, all_paths_by_name)
        if right_base is None:
            pairs.append(ViewPair(left=ViewSubject(entry.name, entry)))
            continue
        right_pool = _normal_overlay_pool_for_subject(right_base, all_entries)
        shared_slots = sorted(
            set(left_pool.keys()) & set(right_pool.keys()),
            key=_overlay_slot_sort_key,
        )
        if not shared_slots:
            continue
        shared_left_pool = {slot: left_pool[slot] for slot in shared_slots}
        shared_right_pool = {slot: right_pool[slot] for slot in shared_slots}
        chosen_slots = [
            slot for slot in shared_slots if rng.random() < 0.65
        ]
        if not chosen_slots and shared_slots:
            chosen_slots = [rng.choice(shared_slots)]
        left_subject = ViewSubject(
            entry.name,
            entry,
            overlay_entries=_pick_random_overlay_entries(
                shared_left_pool,
                rng,
                chosen_slots=tuple(chosen_slots),
            ),
        )
        right_subject = ViewSubject(
            right_base.name,
            right_base.base_entry,
            overlay_entries=_pick_random_overlay_entries(
                shared_right_pool,
                rng,
                chosen_slots=tuple(chosen_slots),
            ),
        )
        pairs.append(ViewPair(left=left_subject, right=right_subject))
    return pairs


def _build_view_pairs(
    entries: list[SpriteEntry],
    all_entries: list[SpriteEntry],
    all_paths: list[Path],
    all_paths_by_name: dict[str, Path],
    compare_default: bool,
    compare_default_random: bool,
    compare: str,
    random_seed: int | None,
) -> list[ViewPair]:
    if compare_default_random:
        return _build_random_compare_pairs(
            entries,
            all_entries,
            all_paths,
            all_paths_by_name,
            random.Random(random_seed),
        )
    if compare_default or compare:
        return _build_default_compare_pairs(entries, all_paths, all_paths_by_name, compare)
    return _build_plain_pairs(entries)


def _render_screen(
    pairs: list[ViewPair],
    index: int,
    pair: ViewPair,
    state: BrowserState,
    cache: dict[Path, SpriteAsset],
    panel_state: PanelState,
) -> str:
    meta = pair.left.base_entry.meta
    tick = _time_tick()
    frame_rows, angle, frame_idx = _build_subject_frame_actual(pair.left, state, tick, cache)

    anim = min(max(state.anim, 0), len(meta.anim_lengths) - 1)
    anim_len = meta.anim_lengths[anim]
    ang_label = _angle_label(meta, angle)
    left_fields = _subject_educational_fields(pair.left)

    # Header
    hdr = [
        "\033[1mXP Anim Viewer\033[0m",
        f"  \033[1m{pair.left.name}\033[0m   [{index + 1}/{len(pairs)}]",
        (
            f"  angle {ang_label}  "
            f"frame {frame_idx + 1}/{anim_len}  "
            f"anim {anim + 1}/{len(meta.anim_lengths)}  "
            f"frame size {meta.fr_width}x{meta.fr_height}"
        ),
        "  \033[2m[←/→] sprite  [a/d] angle  [w/s] anim  [,/.] frame  [[/]] scroll  [m] details  [Space] autoplay  [0] frame 0  [q] quit\033[0m",
        "",
    ]

    border = "  " + "\033[2m+" + "-" * meta.fr_width + "+\033[0m"
    if pair.right is not None:
        compare_meta = pair.right.base_entry.meta
        compare_rows = _build_subject_frame_by_index(
            pair.right,
            pair.left.base_entry.name,
            anim,
            angle,
            frame_idx,
            cache,
        )
        right_fields = _subject_educational_fields(pair.right)
        frame_lines = [
            (
                f"  \033[2mleft: {pair.left.name}"
                f"    right: {pair.right.name}\033[0m"
            )
        ] + _subject_stack_detail_lines(
            "Left",
            pair.left,
            pair.left.base_entry.name,
            anim,
            angle,
            frame_idx,
        ) + [
            ""
        ] + _subject_stack_detail_lines(
            "Right",
            pair.right,
            pair.left.base_entry.name,
            anim,
            angle,
            frame_idx,
        ) + [
            ""
        ] + _render_compare_frame_lines(
            frame_rows,
            compare_rows,
            meta.fr_width,
            compare_meta.fr_width,
        )
        metadata_lines = [
            "",
            "  \033[1mEducational Matrix\033[0m",
            "  field           left                                      right",
            f"  base_asset      {left_fields['asset']:<40} {right_fields['asset']}",
            f"  record          {left_fields['record']:<40} {right_fields['record']}",
            f"  owner_kind      {left_fields['owner_kind']:<40} {right_fields['owner_kind']}",
            f"  owner_slug      {left_fields['owner_slug']:<40} {right_fields['owner_slug']}",
            f"  owner_id        {left_fields['owner_id']:<40} {right_fields['owner_id']}",
            f"  present_slug    {left_fields['presentation_slug']:<40} {right_fields['presentation_slug']}",
            f"  present_id      {left_fields['presentation_id']:<40} {right_fields['presentation_id']}",
            f"  slot_slug       {left_fields['slot_slug']:<40} {right_fields['slot_slug']}",
            f"  slot_id         {left_fields['slot_id']:<40} {right_fields['slot_id']}",
            f"  style_slug      {left_fields['style_slug']:<40} {right_fields['style_slug']}",
            f"  style_id        {left_fields['style_id']:<40} {right_fields['style_id']}",
            f"  variant         {left_fields['variant']:<40} {right_fields['variant']}",
            f"  contract        {left_fields['contract']:<40} {right_fields['contract']}",
            f"  anchor_mode     {left_fields['anchor_mode']:<40} {right_fields['anchor_mode']}",
            f"  row1_refs       {left_fields['row1_refs']:<40} {right_fields['row1_refs']}",
            f"  row2_refs       {left_fields['row2_refs']:<40} {right_fields['row2_refs']}",
            f"  frame_size      {left_fields['frame_size']:<40} {right_fields['frame_size']}",
            f"  sheet_size      {left_fields['sheet_size']:<40} {right_fields['sheet_size']}",
            f"  source_truth    {left_fields['source_of_truth']:<40} {right_fields['source_of_truth']}",
            f"  topology        {_format_topology(pair.left.base_entry.meta):<40} {_format_topology(pair.right.base_entry.meta)}",
            f"  overlays        {_subject_overlay_summary(pair.left):<40} {_subject_overlay_summary(pair.right)}",
            "  glossary        presentation=verb/state family  slot=attachment lane  owner=skin/item/mount namespace",
            "",
            "  \033[1mResolved Stacks\033[0m",
            f"  left stack_assets  = {_subject_stack_summary(pair.left)}",
            f"  left frame_rects   = {_subject_frame_rect_summary(pair.left, pair.left.base_entry.name, anim, angle, frame_idx)}",
            f"  right stack_assets = {_subject_stack_summary(pair.right)}",
            f"  right frame_rects  = {_subject_frame_rect_summary(pair.right, pair.left.base_entry.name, anim, angle, frame_idx)}",
        ] + _definition_lines_for_pair(pair)
    else:
        frame_lines = _subject_stack_detail_lines(
            "Left",
            pair.left,
            pair.left.base_entry.name,
            anim,
            angle,
            frame_idx,
        ) + [
            ""
        ] + _render_frame_lines(frame_rows, border, border)
        metadata_lines = [
            "",
            "  \033[1mEducational Matrix\033[0m",
            f"  base_asset  {left_fields['asset']}",
            f"  record      {left_fields['record']}",
            f"  owner_kind  {left_fields['owner_kind']}",
            f"  owner_slug  {left_fields['owner_slug']}",
            f"  owner_id    {left_fields['owner_id']}",
            f"  present_slug {left_fields['presentation_slug']}",
            f"  present_id  {left_fields['presentation_id']}",
            f"  slot_slug   {left_fields['slot_slug']}",
            f"  slot_id     {left_fields['slot_id']}",
            f"  style_slug  {left_fields['style_slug']}",
            f"  style_id    {left_fields['style_id']}",
            f"  variant     {left_fields['variant']}",
            f"  contract    {left_fields['contract']}",
            f"  anchor_mode {left_fields['anchor_mode']}",
            f"  row1_refs   {left_fields['row1_refs']}",
            f"  row2_refs   {left_fields['row2_refs']}",
            f"  frame_size  {left_fields['frame_size']}",
            f"  sheet_size  {left_fields['sheet_size']}",
            f"  source_truth {left_fields['source_of_truth']}",
            f"  topology    {_format_topology(pair.left.base_entry.meta)}",
            f"  overlays    {_subject_overlay_summary(pair.left)}",
            "",
            "  \033[1mResolved Stacks\033[0m",
            f"  left stack_assets = {_subject_stack_summary(pair.left)}",
            f"  left frame_rects  = {_subject_frame_rect_summary(pair.left, pair.left.base_entry.name, anim, angle, frame_idx)}",
        ] + _definition_lines_for_pair(pair)

    # Nearby sprite list
    nearby = ["  Nearby:"]
    start = max(0, index - 3)
    end = min(len(pairs), index + 4)
    for i in range(start, end):
        marker = "\033[1m>\033[0m" if i == index else " "
        nearby.append(f"  {marker} {pairs[i].left.name}")

    status_line = f"  \033[2m{state.status}\033[0m" if state.status else ""
    autoplay_indicator = "  \033[32m▶ autoplay\033[0m" if state.autoplay else "  \033[33m‖ paused\033[0m"

    panel_lines: list[str]
    if panel_state.show_details:
        panel_lines = metadata_lines
    else:
        panel_lines = [
            "",
            "  \033[2mDetails hidden; press [m] to show the metadata panel.\033[0m",
        ]

    panel_lines = panel_lines + [
        "",
        f"  \033[2mpanel scroll {panel_state.scroll}  [ [ / ] ] scroll metadata  [m] toggle details\033[0m",
        autoplay_indicator,
        status_line,
        "",
    ] + nearby

    fixed_lines = hdr + frame_lines
    max_lines = shutil.get_terminal_size((120, 80)).lines
    available_panel_lines = max(6, max_lines - len(fixed_lines))
    max_scroll = max(0, len(panel_lines) - available_panel_lines)
    scroll = min(max(panel_state.scroll, 0), max_scroll)
    panel_state.scroll = scroll
    visible_panel_lines = panel_lines[scroll:scroll + available_panel_lines]

    body = fixed_lines + visible_panel_lines
    return "\033[H\033[2J" + "\r\n".join(body)


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def _read_key(fd: int) -> str | None:
    if not select.select([fd], [], [], 0.05)[0]:
        return None
    raw = os.read(fd, 16)
    if not raw:
        return None
    data = raw.decode("utf-8", errors="ignore")
    if data.startswith("\x1b[D"):
        return KEY_LEFT
    if data.startswith("\x1b[C"):
        return KEY_RIGHT
    if data.startswith("\x1b[A"):
        return KEY_UP
    if data.startswith("\x1b[B"):
        return KEY_DOWN
    if data.startswith("\x1b[5~"):
        return KEY_PAGEUP
    if data.startswith("\x1b[6~"):
        return KEY_PAGEDOWN
    return data[0]


def _apply_key(
    state: BrowserState,
    meta: SpriteMetadata,
    key: str,
) -> tuple[bool, int | None]:
    """Return (keep_running, sprite_index_delta)."""
    if key in {"q", "Q", KEY_ESCAPE, "\x03"}:
        return False, None
    if key == KEY_LEFT:
        return True, -1
    if key == KEY_RIGHT:
        return True, 1
    if key in {"j", "J"}:
        return True, -10
    if key in {"k", "K"}:
        return True, 10
    if key in {"a", "A", KEY_UP}:
        step = 360.0 / max(1, meta.angles)
        state.yaw = (state.yaw - step) % 360.0
        state.status = f"yaw {int(state.yaw) % 360}"
        return True, None
    if key in {"d", "D", KEY_DOWN}:
        step = 360.0 / max(1, meta.angles)
        state.yaw = (state.yaw + step) % 360.0
        state.status = f"yaw {int(state.yaw) % 360}"
        return True, None
    if key in {"w", "W"}:
        state.anim = (state.anim - 1) % len(meta.anim_lengths)
        state.frame = 0
        state.status = f"anim {state.anim + 1}"
        return True, None
    if key in {"s", "S"}:
        state.anim = (state.anim + 1) % len(meta.anim_lengths)
        state.frame = 0
        state.status = f"anim {state.anim + 1}"
        return True, None
    if key == ",":
        state.autoplay = False
        anim = min(max(state.anim, 0), len(meta.anim_lengths) - 1)
        state.frame = (state.frame - 1) % meta.anim_lengths[anim]
        state.status = f"frame {state.frame + 1}"
        return True, None
    if key == ".":
        state.autoplay = False
        anim = min(max(state.anim, 0), len(meta.anim_lengths) - 1)
        state.frame = (state.frame + 1) % meta.anim_lengths[anim]
        state.status = f"frame {state.frame + 1}"
        return True, None
    if key == "0":
        state.autoplay = False
        state.frame = 0
        state.status = "jumped to frame 0"
        return True, None
    if key == KEY_SPACE:
        state.autoplay = not state.autoplay
        state.status = "autoplay on" if state.autoplay else "autoplay off"
        return True, None
    return True, None


def _default_state(meta: SpriteMetadata) -> BrowserState:
    # Start on anim 1 (move) if multi-anim, else anim 0.
    anim = 1 if len(meta.anim_lengths) > 1 else 0
    return BrowserState(anim=anim, autoplay=True)


def _time_tick() -> int:
    return (time.monotonic_ns() // 1000) >> 14


def _normalize_cli_argv(argv: list[str]) -> list[str]:
    normalized: list[str] = []
    idx = 0
    while idx < len(argv):
        current = argv[idx]
        if current == "--compare-" and idx + 1 < len(argv):
            nxt = argv[idx + 1]
            if nxt in {"default-random", "default--random"}:
                normalized.append("--compare-default-random")
                idx += 2
                continue
        normalized.append(current)
        idx += 1
    return normalized


@contextlib.contextmanager
def _loading_spinner(label: str):
    if not sys.stderr.isatty():
        yield
        return

    stop = threading.Event()

    def spin() -> None:
        frames = "|/-\\"
        idx = 0
        while not stop.wait(0.1):
            frame = frames[idx % len(frames)]
            sys.stderr.write(f"\r{label} {frame}")
            sys.stderr.flush()
            idx += 1
        sys.stderr.write(f"\r{label} done\n")
        sys.stderr.flush()

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    sprite_dir: Path = SPRITE_DIR,
    pattern: str = "",
    compare_default: bool = False,
    compare: str = "",
    compare_default_random: bool = False,
    random_seed: int | None = None,
) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("xp_anim_viewer requires a TTY", file=sys.stderr)
        return 1

    with _loading_spinner("Loading XP sprites"):
        all_entries = scan_viewer_sprite_entries(sprite_dir)
    all_paths = [entry.path for entry in all_entries]
    all_paths_by_name = {path.name.lower(): path for path in all_paths}

    effective_pattern = pattern
    if (compare_default or compare_default_random) and not effective_pattern:
        effective_pattern = "4TEST"

    entries = _filter_entries(all_entries, effective_pattern)

    if compare_default_random:
        entries = [entry for entry in entries if _is_actor_body_entry(entry)]

    if not entries:
        msg = (
            f"no sprites matching {effective_pattern!r}"
            if effective_pattern
            else "no valid .xp sprites found"
        )
        print(msg, file=sys.stderr)
        return 1

    with _loading_spinner("Building compare pairs"):
        pairs = _build_view_pairs(
            entries=entries,
            all_entries=all_entries,
            all_paths=all_paths,
            all_paths_by_name=all_paths_by_name,
            compare_default=compare_default,
            compare_default_random=compare_default_random,
            compare=compare,
            random_seed=random_seed,
        )
    if not pairs:
        print("no compatible compare pairs found", file=sys.stderr)
        return 1

    cache: dict[Path, SpriteAsset] = {}

    index = 0
    pair = pairs[index]
    state = _default_state(pair.left.base_entry.meta)
    panel_state = PanelState()
    redraw = [True]

    def on_resize(*_: object) -> None:
        redraw[0] = True

    old_sig = signal.signal(signal.SIGWINCH, on_resize)
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()

    try:
        tty.setraw(fd)
        while True:
            if redraw[0] or state.autoplay:
                redraw[0] = False
                sys.stdout.write(
                    _render_screen(
                        pairs,
                        index,
                        pair,
                        state,
                        cache,
                        panel_state,
                    )
                )
                sys.stdout.flush()

            key = _read_key(fd)
            if key is None:
                continue

            if key in {"m", "M"}:
                panel_state.show_details = not panel_state.show_details
                panel_state.scroll = 0
                state.status = "details shown" if panel_state.show_details else "details hidden"
                redraw[0] = True
                continue
            if key in {"[", KEY_PAGEUP}:
                panel_state.scroll = max(0, panel_state.scroll - 5)
                state.status = f"panel scroll {panel_state.scroll}"
                redraw[0] = True
                continue
            if key in {"]", KEY_PAGEDOWN}:
                panel_state.scroll += 5
                state.status = f"panel scroll {panel_state.scroll}"
                redraw[0] = True
                continue

            keep, delta = _apply_key(state, pair.left.base_entry.meta, key)
            if not keep:
                break
            if delta is not None:
                total = len(pairs)
                index = (index + delta) % total if total else 0
                pair = pairs[index]
                state = _default_state(pair.left.base_entry.meta)
                state.status = pair.left.name
                panel_state.scroll = 0
            redraw[0] = True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\033[?1049l\033[?25h\033[0m")
        sys.stdout.flush()
        signal.signal(signal.SIGWINCH, old_sig)

    return 0


# ---------------------------------------------------------------------------
# FL-2345 Combination-Check Viewer
# ---------------------------------------------------------------------------


def _variant_matches(ld_vs: dict, body_vs: dict) -> bool:
    """Return True if variant-signature fields match between two dicts."""
    return (
        ld_vs.get("height_class") == body_vs.get("height_class")
        and ld_vs.get("width_class") == body_vs.get("width_class")
        and ld_vs.get("silhouette_class") == body_vs.get("silhouette_class")
    )


def _owner_slug(ld: dict) -> str:
    """Return the owner slug for a layer definition, checking both key names."""
    return ld.get("owner_definition_slug", "") or ld.get("item_definition_slug", "")


def _load_bundle_catalog() -> dict:
    """Load the compiled appearance bundle catalog."""
    if not APPEARANCE_BUNDLE_PATH.exists():
        return {}
    try:
        bundle = json.loads(APPEARANCE_BUNDLE_PATH.read_text(encoding="utf-8"))
        return bundle.get("catalog", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _body_candidates(catalog: dict) -> list[dict]:
    """Return body layer definitions that can serve as base bodies."""
    ldefs = catalog.get("layer_definitions", [])
    bodies = [ld for ld in ldefs if ld.get("slot_kind_slug") == "body"]
    # Deduplicate by (skin_definition_slug, presentation_kind_slug, variant_signature)
    seen: set[tuple[str, str, str]] = set()
    uniq: list[dict] = []
    for b in bodies:
        vs = b.get("variant_signature", {}) or {}
        key = (
            str(b.get("skin_definition_slug", "-")),
            str(b.get("presentation_kind_slug", "-")),
            f"{vs.get('height_class','-')}/{vs.get('width_class','-')}/{vs.get('silhouette_class','-')}",
        )
        if key not in seen:
            seen.add(key)
            uniq.append(b)
    return uniq


def _build_sprite_asset_for_ld(
    cache: dict[Path, SpriteAsset],
    ld: dict,
) -> SpriteAsset | None:
    """Load a SpriteAsset for the XP file referenced by a layer definition."""
    asset_info = ld.get("asset", {}) or {}
    path_str = asset_info.get("path", "")
    if not path_str:
        return None
    xp_path = REPO_ROOT / path_str
    if not xp_path.exists():
        return None
    entry = _entry_from_path(xp_path)
    if entry is None:
        return None
    return _get_asset(cache, entry)


def _render_body_frame_lines(
    body_ld: dict,
    anim: int,
    angle: int,
    frame_idx: int,
    cache: dict[Path, SpriteAsset],
) -> tuple[list[list[PreviewCell]], list[str]]:
    """Render a full frame of the body XP and return (rows, rendered_lines)."""
    body_asset = _build_sprite_asset_for_ld(cache, body_ld)
    if body_asset is None:
        return [], ["  (body sprite not found)"]

    meta = body_asset.entry.meta
    anim = min(max(anim, 0), len(meta.anim_lengths) - 1)
    safe_angle = angle % max(1, meta.angles)
    safe_frame = frame_idx % meta.anim_lengths[anim]

    rows = build_frame_by_index(body_asset, anim, safe_angle, safe_frame)
    border = "  \033[2m+" + "-" * meta.fr_width + "+\033[0m"
    lines = _render_frame_lines(rows, border, border)
    return rows, lines


def _body_layer_defs(
    catalog: dict,
    body_ld: dict,
) -> list[dict]:
    """Return all body layer definitions for the same skin/presentation/variant."""
    pres_slug = body_ld.get("presentation_kind_slug", "idle_walk")
    owner_slug = body_ld.get("owner_definition_slug", "")
    vs = body_ld.get("variant_signature", {}) or {}
    matches: list[dict] = []
    for ld in catalog.get("layer_definitions", []):
        if ld.get("slot_kind_slug") != "body":
            continue
        if ld.get("presentation_kind_slug") != pres_slug:
            continue
        if ld.get("owner_definition_slug") != owner_slug:
            continue
        lvs = ld.get("variant_signature", {}) or {}
        if not _variant_matches(lvs, vs):
            continue
        matches.append(ld)
    return matches


def _compute_anim_and_local_frame(anims: tuple[int, ...], frame_idx: int) -> tuple[int, int]:
    """Given a flat frame index and anim_lengths, return (anim_idx, local_frame)."""
    if not anims:
        return 0, 0
    for i, length in enumerate(anims):
        if frame_idx < length:
            return i, frame_idx
        frame_idx -= length
    # Past the end — clamp to last anim's last frame
    return len(anims) - 1, anims[-1] - 1


def _compute_frame_rect_correct(
    asset_info: dict,
    anim_idx: int,
    local_frame: int,
    angle: int,
) -> dict:
    """Compute the frame-cell rectangle for a specific (anim, local_frame, angle).

    Uses the same atlas-index formula as build_frame_by_index() in this file
    and _select_frame() in xp_assets_browser_layer_2_only.py:
        frame_base = sum(anim_lengths[:anim_idx])
        atlas_idx = frame_base + local_frame + angle * fr_num_x

    Returns {x1, y1, x2, y2} in sheet-local coordinates (bottom-left origin).
    """
    fr_w = asset_info.get("frame_size", {}).get("width", 7)
    fr_h = asset_info.get("frame_size", {}).get("height", 9)
    fr_num_x = asset_info.get("fr_num_x", 9)
    anims = asset_info.get("anims", [1])

    frame_base = sum(anims[:anim_idx])
    atlas_idx = frame_base + local_frame + angle * fr_num_x
    fr_x = atlas_idx % fr_num_x
    fr_y = atlas_idx // fr_num_x
    x0 = fr_x * fr_w
    y0 = fr_y * fr_h
    return {
        "x1": x0,
        "y1": y0,
        "x2": x0 + fr_w - 1,
        "y2": y0 + fr_h - 1,
    }


def _parse_asset_info(ld: dict) -> dict:
    """Extract asset layout metadata from a layer definition's asset block."""
    a = ld.get("asset", {}) or {}
    if not a:
        return {"anims": [1], "fr_num_x": 1, "fr_num_y": 8, "angles": 8,
                "frame_size": {"width": 7, "height": 9},
                "sheet_size": {"width": 126, "height": 72},
                "layer_count": 3}
    anims = a.get("anims", [1])
    fr_w = a.get("frame_size", {}).get("width", 7)
    fr_h = a.get("frame_size", {}).get("height", 9)
    sheet_w = a.get("sheet_size", {}).get("width", 126)
    angles = a.get("angles", 8)
    projs = a.get("projs", 1)
    anim_sum = sum(anims)
    fr_num_x = projs * anim_sum
    fr_num_y = angles
    return {
        "anims": anims,
        "anim_sum": anim_sum,
        "fr_num_x": fr_num_x,
        "fr_num_y": fr_num_y,
        "angles": angles,
        "projs": projs,
        "frame_size": {"width": fr_w, "height": fr_h},
        "sheet_size": {"width": sheet_w, "height": fr_num_y * fr_h},
        "layer_count": a.get("layer_count", 3),
    }


def _available_items_in_slot(
    catalog: dict,
    body_ld: dict,
    slot: str,
) -> list[dict]:
    """Return unique item definitions for a slot that share presentation+variant
    with the body. Each entry has slug, id, and a representative layer def."""
    pres_slug = body_ld.get("presentation_kind_slug", "idle_walk")
    vs = body_ld.get("variant_signature", {}) or {}
    ldefs = catalog.get("layer_definitions", [])
    seen: set[str] = set()
    items: list[dict] = []
    for ld in ldefs:
        if ld.get("slot_kind_slug") != slot:
            continue
        if ld.get("presentation_kind_slug") != pres_slug:
            continue
        lvs = ld.get("variant_signature", {}) or {}
        if not _variant_matches(lvs, vs):
            continue
        slug = _owner_slug(ld)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        items.append({
            "slug": slug,
            "id": ld.get("owner_definition_id") or ld.get("item_definition_id"),
            "representative_ld": ld,
        })
    items.sort(key=lambda x: x["slug"])
    return items


def _prefer_mount_qualifier(
    rows: list[dict],
    mount_slug: str | None,
) -> list[dict]:
    """Prefer mount-qualified rows matching the equipped mount, else base rows."""
    if not rows:
        return []
    if mount_slug:
        qualified = [row for row in rows if row.get("mount_qualifier_definition_slug") == mount_slug]
        if qualified:
            return qualified
    unqualified = [row for row in rows if row.get("mount_qualifier_definition_slug") in {None, ""}]
    if unqualified:
        return unqualified
    return rows


def _prefer_visual_style(rows: list[dict], preferred: str = "default") -> list[dict]:
    """Prefer one visual style when multiple rows share the same owner/slot."""
    if not rows:
        return []
    preferred_rows = [row for row in rows if row.get("visual_style_slug") == preferred]
    if preferred_rows:
        return preferred_rows
    return rows


def _resolve_visible_body_layer_def(
    catalog: dict,
    body_ld: dict,
    mount_slug: str | None,
) -> dict:
    """Resolve the body layer definition that should actually render."""
    matches = _body_layer_defs(catalog, body_ld)
    matches = _prefer_mount_qualifier(matches, mount_slug)
    matches = _prefer_visual_style(matches, "default")
    return matches[0] if matches else body_ld


def _resolve_visible_item_layer_def(
    catalog: dict,
    body_ld: dict,
    slot: str,
    item_slug: str,
    mount_slug: str | None,
) -> dict | None:
    """Resolve the concrete layer definition that should render for one slot."""
    matches = _layer_defs_for_specific_item(catalog, body_ld, slot, item_slug)
    matches = _prefer_mount_qualifier(matches, mount_slug)
    matches = _prefer_visual_style(matches, "default")
    return matches[0] if matches else None


def _visible_item_layer_defs(
    catalog: dict,
    body_ld: dict,
    slot: str,
    item_slug: str,
    mount_slug: str | None,
) -> list[dict]:
    """Return the mount/style-filtered layer defs that can actually render."""
    matches = _layer_defs_for_specific_item(catalog, body_ld, slot, item_slug)
    matches = _prefer_mount_qualifier(matches, mount_slug)
    matches = _prefer_visual_style(matches, "default")
    return matches


def _mounted_admission_entry(
    catalog: dict,
    body_ld: dict,
    mount_slug: str,
) -> dict | None:
    """Return the mounted-admission row for this mount/presentation/variant."""
    pres_slug = body_ld.get("presentation_kind_slug", "idle_walk")
    vs = body_ld.get("variant_signature", {}) or {}
    for row in catalog.get("mounted_admission", []):
        if row.get("mount_definition_slug") != mount_slug:
            continue
        if row.get("presentation_kind_slug") != pres_slug:
            continue
        if row.get("height_class") != vs.get("height_class"):
            continue
        if row.get("width_class") != vs.get("width_class"):
            continue
        if row.get("silhouette_class") != vs.get("silhouette_class"):
            continue
        return row
    return None


def _compose_ld_rows_onto(
    base_rows: list[list[PreviewCell]],
    ld: dict | None,
    anim_idx: int,
    angle: int,
    frame_idx: int,
    cache: dict[Path, SpriteAsset],
) -> list[list[PreviewCell]]:
    """Overlay one layer-definition frame onto base_rows."""
    if ld is None:
        return base_rows
    asset = _build_sprite_asset_for_ld(cache, ld)
    if asset is None:
        return base_rows
    overlay_rows = build_frame_by_index(asset, anim_idx, angle, frame_idx)
    return _compose_preview_rows(base_rows, overlay_rows)


def _build_combination_frame_rows(
    catalog: dict,
    body_ld: dict,
    equipped: dict[str, str | bool],
    anim_idx: int,
    angle: int,
    frame_idx: int,
    cache: dict[Path, SpriteAsset],
    layer_defs_by_id: dict[int, dict],
) -> tuple[list[list[PreviewCell]], list[dict], dict | None, dict | None, dict]:
    """Build the visibly equipped stack for the combination-check preview.

    Mirrors runtime MountedComposeRuntime::Compose:
      rear -> rider body + items (with rider offset) -> front (masked by parity)
    """
    mount_slug = equipped.get("mount") if isinstance(equipped.get("mount"), str) else None
    mounted_entry = _mounted_admission_entry(catalog, body_ld, mount_slug) if mount_slug else None

    rear_ld = None
    front_ld = None
    attachment_order = list(DEFAULT_ATTACHMENT_ORDER)
    if mounted_entry is not None:
        attachment_order = list(mounted_entry.get("attachment_order_slot_kind_slugs") or DEFAULT_ATTACHMENT_ORDER)
        rear_id = mounted_entry.get("rear_layer_definition_id")
        front_id = mounted_entry.get("front_layer_definition_id")
        if rear_id is not None:
            rear_ld = layer_defs_by_id.get(int(rear_id))
        if front_id is not None:
            front_ld = layer_defs_by_id.get(int(front_id))

    visible_body_ld = _resolve_visible_body_layer_def(catalog, body_ld, mount_slug)
    body_asset = _build_sprite_asset_for_ld(cache, visible_body_ld)
    if body_asset is None:
        return [], [], rear_ld, front_ld, visible_body_ld

    # [RUNTIME-MIRRORED: build_frame_by_index uses same atlas-index formula as
    #  C++ runtime _select_frame(). The anim track selection mirrors runtime
    #  ResolvePresentationFrame() which picks the anim track from the selector's
    #  presentation_kind_id and state masks.]
    rows = build_frame_by_index(body_asset, anim_idx, angle, frame_idx)
    rendered_layers: list[dict] = []

    # [RUNTIME-MIRRORED: composition order matches ExecuteMountedComposePlan:
    #  1. render rear surface
    #  2. paste rider body + items on top (with rider offset in C++)
    #  3. render front surface (masked by parity in C++)
    #  Approximate: rider offset and parity mask not available in Python surface]
    if rear_ld is not None:
        rear_asset = _build_sprite_asset_for_ld(cache, rear_ld)
        if rear_asset is not None:
            rows = build_frame_by_index(rear_asset, anim_idx, angle, frame_idx)
            rows = _compose_ld_rows_onto(rows, visible_body_ld, anim_idx, angle, frame_idx, cache)
            rendered_layers.append(rear_ld)

    rendered_layers.append(visible_body_ld)

    for slot in attachment_order:
        if slot == "body":
            continue
        item_slug = equipped.get(slot)
        if not isinstance(item_slug, str) or not item_slug:
            continue
        overlay_ld = _resolve_visible_item_layer_def(catalog, body_ld, slot, item_slug, mount_slug)
        if overlay_ld is None:
            continue
        rows = _compose_ld_rows_onto(rows, overlay_ld, anim_idx, angle, frame_idx, cache)
        rendered_layers.append(overlay_ld)

    if front_ld is not None:
        # [APPROXIMATE: runtime applies PasteSpriteOntoByAngleOffsets + parity mask.
        #  This surface overlays front without offset or mask since rider offset
        #  arrays and parity-mask primitives are only in C++ runtime.]
        rows = _compose_ld_rows_onto(rows, front_ld, anim_idx, angle, frame_idx, cache)
        rendered_layers.append(front_ld)

    return rows, rendered_layers, rear_ld, front_ld, visible_body_ld


def _layer_defs_for_specific_item(
    catalog: dict,
    body_ld: dict,
    slot: str,
    item_slug: str,
) -> list[dict]:
    """Find layer definitions in a given slot that match the body's
    presentation/variant AND the specific item owner slug."""
    pres_slug = body_ld.get("presentation_kind_slug", "idle_walk")
    vs = body_ld.get("variant_signature", {}) or {}
    ldefs = catalog.get("layer_definitions", [])
    matches: list[dict] = []
    for ld in ldefs:
        if ld.get("slot_kind_slug") != slot:
            continue
        if ld.get("presentation_kind_slug") != pres_slug:
            continue
        ld_slug = _owner_slug(ld)
        if not ld_slug:
            continue
        # Match the specific item family
        if ld_slug != item_slug:
            continue
        lvs = ld.get("variant_signature", {}) or {}
        if not _variant_matches(lvs, vs):
            continue
        matches.append(ld)
    return matches


def _provenance_lines(
    label: str,
    ld: dict,
    anim_idx: int,
    local_frame: int,
    angle: int,
) -> list[str]:
    """Format provenance for one layer definition at the given (anim, frame, angle).

    Uses the corrected atlas-index formula matching build_frame_by_index().
    """
    asset = ld.get("asset", {}) or {}
    path = str(asset.get("path", "-"))
    ai = _parse_asset_info(ld)
    fr_w = ai["frame_size"]["width"]
    fr_h = ai["frame_size"]["height"]
    fr_num_x = ai["fr_num_x"]
    angles_cnt = ai["angles"]
    anims = ai["anims"]
    layer_count = ai["layer_count"]

    safe_angle = angle % max(1, angles_cnt)
    safe_anim = min(max(anim_idx, 0), len(anims) - 1)
    safe_local = local_frame % max(1, anims[safe_anim]) if anims else 0

    frame_base = sum(anims[:safe_anim])
    atlas_idx = frame_base + safe_local + safe_angle * fr_num_x
    col = atlas_idx % fr_num_x
    row = atlas_idx // fr_num_x

    rect = _compute_frame_rect_correct(ai, safe_anim, safe_local, safe_angle)

    lines = [
        f"  [{label}] slot={ld.get('slot_kind_slug','-')}  owner={ld.get('owner_definition_slug','-')}",
        f"          file={path}",
        f"          visual_layer=2 (of {layer_count})  row={row}  column={col}",
        f"          frame_cell=({rect['x1']},{rect['y1']}) - ({rect['x2']},{rect['y2']})",
    ]
    if ld.get("id") is not None:
        lines[0] += f"  layer_def_id={ld['id']}"
    return lines


def _run_combination_check() -> int:
    """Interactive FL-2345 combination-check viewer.

    Steps through angles, frames, and anim tracks. Lets operator toggle
    specific equipped items (head, armor, weapon, shield, mountable) and reports
    whether each specific layer definition exists in the compiled bundle with
    exact file/layer/row/column/frame-cell provenance. Renders the body frame
    on screen for visual cross-check.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Combination-check viewer requires a TTY", file=sys.stderr)
        return 1

    catalog = _load_bundle_catalog()
    if not catalog:
        print(f"Error: compiled bundle not found at {APPEARANCE_BUNDLE_PATH}", file=sys.stderr)
        return 1

    bodies = _body_candidates(catalog)
    if not bodies:
        print("Error: no body layer definitions in compiled bundle", file=sys.stderr)
        return 1

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        return _combination_check_loop(fd, old_settings, catalog, bodies)
    except KeyboardInterrupt:
        return 1


def _combination_check_loop(
    fd: int,
    old_settings: list,
    catalog: dict,
    bodies: list[dict],
) -> int:
    angle = 0
    frame_idx = 0
    anim_track = 0
    body_index = 0
    autoplay = True
    FRAME_INTERVAL = 0.15

    slot_keys = list(SLOT_ORDER)
    equipped: dict[str, str | bool] = {slot: False for slot in slot_keys}
    equip_index = 0
    scroll_offset = 0

    cache: dict[Path, SpriteAsset] = {}
    layer_defs_by_id = {
        int(ld["id"]): ld
        for ld in catalog.get("layer_definitions", [])
        if isinstance(ld, dict) and ld.get("id") is not None
    }
    last_frame_time = time.monotonic()

    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()

    try:
        tty.setraw(fd)
        while True:
            body_ld = bodies[body_index]
            ai = _parse_asset_info(body_ld)
            angles_cnt = ai["angles"]
            anims_list = ai["anims"]
            anim_track = min(max(anim_track, 0), len(anims_list) - 1)
            anim_track_count = len(anims_list)
            cur_anim_len = anims_list[anim_track] if anims_list else 1
            safe_angle = angle % max(1, angles_cnt)
            safe_frame = frame_idx % max(1, cur_anim_len)

            now = time.monotonic()
            if autoplay and now - last_frame_time >= FRAME_INTERVAL:
                last_frame_time = now
                frame_idx = (frame_idx + 1) % cur_anim_len
                safe_frame = frame_idx

            mount_slug = equipped.get("mount") if isinstance(equipped.get("mount"), str) else None

            body_rows, rendered_layers, rear_ld, front_ld, visible_body_ld = _build_combination_frame_rows(
                catalog,
                body_ld,
                equipped,
                anim_track,
                safe_angle,
                safe_frame,
                cache,
                layer_defs_by_id,
            )
            if body_rows:
                body_width = len(body_rows[0]) if body_rows and body_rows[0] else 0
                border = "  \033[2m+" + "-" * body_width + "+\033[0m"
                body_lines = _render_frame_lines(body_rows, border, border)
            else:
                body_lines = ["  (body sprite not found)"]

            match_results: dict[str, list[dict]] = {}
            for slot in slot_keys:
                item_slug = equipped.get(slot)
                if not item_slug or not isinstance(item_slug, str):
                    continue
                match_results[slot] = _visible_item_layer_defs(
                    catalog,
                    body_ld,
                    slot,
                    item_slug,
                    mount_slug,
                )
            for slot in slot_keys:
                if slot not in match_results and equipped.get(slot):
                    match_results[slot] = []

            # --- Provenance markers ---
            # [RUNTIME-SHARED DATA] = data from the compiled bundle JSON that
            #   the C++ runtime also reads (same catalog, layer_defs, admission).
            # [RUNTIME-MIRRORED LOGIC] = Python reimplementation of C++ selection/
            #   composition rules using the same data contracts and ordering.
            # [APPROXIMATE] = behavior that exists in C++ runtime but is not
            #   feasible in this Python surface (rider offsets, parity masking,
            #   full frame-clock playback).

            lines = []
            lines.append("\033[1mFL-2345 Combination Check\033[0m")
            lines.append("  \033[2mTruth labels: [R] runtime-shared data  [M] mirrored logic  [A] approximate\033[0m")
            lines.append("")
            lines.append(f"  \033[1mBody:\033[0m {body_ld.get('owner_definition_slug','-')} / {body_ld.get('presentation_kind_slug','-')}")
            vs = body_ld.get("variant_signature", {}) or {}
            lines.append(f"  \033[1mVariant:\033[0m {vs.get('height_class','-')}/{vs.get('width_class','-')}/{vs.get('silhouette_class','-')}")
            lines.append(f"  \033[2m[R]\033[0m Visible body file: {visible_body_ld.get('asset',{}).get('path','-')}")
            lines.append("")

            # Anim track display — mirrors runtime presentation kind selection
            pres_slug = body_ld.get('presentation_kind_slug', '?')
            autoplay_indicator = "\033[32m\u25b6 autoplay\033[0m" if autoplay else "\033[33m\u2016 paused\033[0m"
            lines.append(f"  \033[1mAngle:\033[0m {safe_angle} ({ANGLE_NAMES[safe_angle] if safe_angle < len(ANGLE_NAMES) else '?'})")
            lines.append(f"  \033[1mAnim Track:\033[0m {anim_track + 1}/{anim_track_count}  "
                         f"\033[2m[M] uses runtime-style anim_idx (presentation_kind_slug={pres_slug})\033[0m")
            lines.append(f"  \033[1mFrame:\033[0m {safe_frame + 1}/{cur_anim_len}  "
                         f"{autoplay_indicator}")
            lines.append(f"  \033[2m[a/d] angle  [t/T] anim track  [,/.] frame  [w/s] body  [Space] autoplay"
                         f"  [\u2191/\u2193] slot focus  [\u2190/\u2192] cycle item  [Enter] unequip"
                         f"  [[/]] scroll  [q] quit\033[0m")
            lines.append("")
            lines.extend(body_lines)

            # Composition stack display with provenance
            if rendered_layers:
                stack_text = " + ".join(
                    Path(str(layer.get("asset", {}).get("path", "-"))).name
                    for layer in rendered_layers
                )
                lines.append(f"  \033[2mvisible stack: {stack_text}\033[0m")
            if rear_ld is not None or front_ld is not None:
                lines.append(f"  \033[2m[M] mounted compose: rear \u2192 rider body/items \u2192 front"
                             f" (matches ExecuteMountedComposePlan order)\033[0m")
            if rear_ld is not None or front_ld is not None:
                rear_fn = Path(str(rear_ld.get('asset',{}).get('path','-'))).name if rear_ld else '-'
                front_fn = Path(str(front_ld.get('asset',{}).get('path','-'))).name if front_ld else '-'
                lines.append(f"  \033[2m[A] rider offset + parity mask not applied (C++ runtime only)\033[0m")
                lines.append(f"  \033[2m[A]   rear={rear_fn}  front={front_fn}\033[0m")
            lines.append("")

            # Equipment slots
            lines.append("  \033[1mEquipment\033[0m")
            for i, slot in enumerate(slot_keys):
                cursor = "\033[7m" if i == equip_index else ""
                reset = "\033[0m" if i == equip_index else ""
                val = equipped.get(slot)
                if val and isinstance(val, str):
                    marker = f"\033[32m\u2713\033[0m {val}"
                else:
                    marker = "\033[31m\u2717\033[0m"
                label = SLOT_LABELS.get(slot, slot)
                lines.append(f"  {cursor}[{marker}] {label}{reset}")
            lines.append("")

            # Combination check results
            lines.append("  \033[1mCombination Check Results\033[0m")
            lines.append("  \033[2m[R] layer defs from compiled bundle (same data runtime reads)\033[0m")

            has_all = True
            any_equipped = False
            for slot in slot_keys:
                val = equipped.get(slot)
                if not val or not isinstance(val, str):
                    continue
                any_equipped = True
                slot_matches = match_results.get(slot, [])
                if not slot_matches:
                    lines.append(f"  \033[31m\u2717\033[0m {SLOT_LABELS.get(slot, slot)} ({val}): NO MATCHING LAYER DEFINITION")
                    has_all = False
                else:
                    lines.append(f"  \033[32m\u2713\033[0m {SLOT_LABELS.get(slot, slot)} ({val}): {len(slot_matches)} layer def(s)")
                    for ld in slot_matches[:2]:
                        lines.extend(_provenance_lines("  ", ld, anim_track, safe_frame, safe_angle))
                    if len(slot_matches) > 2:
                        lines.append(f"    ... and {len(slot_matches) - 2} more")

            if not any_equipped:
                lines.append("  (no equipment toggled on \u2014 press [\u2192] on a focused slot)")

            if has_all and any_equipped:
                lines.append("")
                body_path = visible_body_ld.get("asset",{}).get("path","-")
                body_ai = _parse_asset_info(visible_body_ld)
                body_rect = _compute_frame_rect_correct(body_ai, anim_track, safe_frame, safe_angle)
                lines.append(f"  Body frame provenance:")
                lines.append(f"    [R] file={body_path}")
                lines.append(f"    [R] visual_layer=2 (of {body_ai['layer_count']})  angle={safe_angle}"
                             f"  anim_track={anim_track}  frame={safe_frame}")
                lines.append(f"    [R] frame_cell=({body_rect['x1']},{body_rect['y1']}) - ({body_rect['x2']},{body_rect['y2']})")
                lines.append("")
                lines.append("  \033[32m\u2713 All specific equipped combinations found in the bundle.\033[0m")
            elif not has_all:
                lines.append("")
                lines.append("  \033[31m\u2717 Some combinations missing \u2014 check individual slot results above.\033[0m")

            # Provenance summary
            lines.append("")
            lines.append("  \033[2m\u2500\u2500\u2500 Provenance \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\033[0m")
            lines.append("  \033[1mApproach\033[0m  \033[1mWhat is used\033[0m")
            lines.append("  [R]         Compiled bundle JSON catalog")
            lines.append("  [R]         Mounted admission entries (attachment_order, rear/front ids)")
            lines.append("  [R]         Layer definition metadata (anims, frame_size, angles)")
            lines.append("  [M]         Body selection by owner/presentation/variant (mirrors runtime")
            lines.append("              ResolveActorBundleLayers / FindMountedAdmission)")
            lines.append("  [M]         Item selection by slot+owner+presentation+variant+mount_qualifier")
            lines.append("              (mirrors runtime FindMountedBodyLayer / FindMountedItemLayer)")
            lines.append("  [M]         Composition order: rear \u2192 rider body + items \u2192 front")
            lines.append("              (mirrors ExecuteMountedComposePlan order)")
            lines.append("  [A]         Rider offset by angle not applied (PasteSpriteOntoByAngleOffsets)")
            lines.append("  [A]         Parity mask not applied (MaskSpriteByVisibleCells)")
            lines.append("  [A]         Frame-clock playback (simple autoplay, not ResolvePresentationFrame)")
            lines.append("  [A]         Full C++ renderer (CompositeSpriteOnto, FreeSprite life-cycle)")
            lines.append("")

            lines.append(f"  \033[2mscroll {scroll_offset}  Body {body_index + 1}/{len(bodies)}: {body_ld.get('owner_definition_slug','-')}\033[0m")
            lines.append("")

            max_lines = shutil.get_terminal_size((120, 80)).lines
            available = max(5, max_lines - 2)
            max_scroll = max(0, len(lines) - available)
            scroll_offset = min(max(scroll_offset, 0), max_scroll)

            visible = lines[scroll_offset:scroll_offset + available]
            output = "\033[H\033[2J" + "\r\n".join(visible)
            sys.stdout.write(output)
            sys.stdout.flush()

            if not select.select([fd], [], [], 0.05)[0]:
                continue
            raw = os.read(fd, 16)
            if not raw:
                continue
            data = raw.decode("utf-8", errors="ignore")

            if data in {"q", "Q", "\x1b", "\x03"}:
                break

            if data in {"w", "W"}:
                body_index = (body_index - 1) % len(bodies)
                anim_track = 0
                frame_idx = 0
                continue
            if data in {"s", "S"}:
                body_index = (body_index + 1) % len(bodies)
                anim_track = 0
                frame_idx = 0
                continue

            if data in {"a", "A"}:
                angle = (angle - 1) % max(1, angles_cnt)
                continue
            if data in {"d", "D"}:
                angle = (angle + 1) % max(1, angles_cnt)
                continue

            if data in {"t", "T"}:
                anim_track = (anim_track + 1) % max(1, len(anims_list))
                frame_idx = 0
                continue

            if data == ",":
                autoplay = False
                cur_len = anims_list[anim_track] if anims_list else 1
                frame_idx = (frame_idx - 1) % cur_len
                continue
            if data == ".":
                autoplay = False
                cur_len = anims_list[anim_track] if anims_list else 1
                frame_idx = (frame_idx + 1) % cur_len
                continue

            if data == " ":
                autoplay = not autoplay
                if autoplay:
                    last_frame_time = time.monotonic()
                continue

            if data in {"\x1b[A", "\x1bOA"}:
                equip_index = (equip_index - 1) % len(slot_keys)
                continue
            if data in {"\x1b[B", "\x1bOB"}:
                equip_index = (equip_index + 1) % len(slot_keys)
                continue

            if data in {"\x1b[C", "\x1bOC"}:
                slot = slot_keys[equip_index]
                items = _available_items_in_slot(catalog, body_ld, slot)
                if not items:
                    continue
                if not equipped.get(slot) or not isinstance(equipped.get(slot), str):
                    equipped[slot] = items[0]["slug"]
                else:
                    current = equipped[slot]
                    idx = 0
                    for i, item in enumerate(items):
                        if item["slug"] == current:
                            idx = i + 1
                            break
                    if idx >= len(items):
                        idx = 0
                    equipped[slot] = items[idx]["slug"]
                autoplay = False
                continue
            if data in {"\x1b[D", "\x1bOD"}:
                slot = slot_keys[equip_index]
                items = _available_items_in_slot(catalog, body_ld, slot)
                if not items:
                    continue
                if not equipped.get(slot) or not isinstance(equipped.get(slot), str):
                    equipped[slot] = items[-1]["slug"]
                else:
                    current = equipped[slot]
                    idx = len(items) - 1
                    for i, item in enumerate(items):
                        if item["slug"] == current:
                            idx = i - 1
                            break
                    if idx < 0:
                        idx = len(items) - 1
                    equipped[slot] = items[idx]["slug"]
                autoplay = False
                continue

            if data == "\t":
                equip_index = (equip_index + 1) % len(slot_keys)
                continue

            if data in {"\r", "\n"}:
                slot = slot_keys[equip_index]
                equipped[slot] = False
                continue

            if data in {"[", "\x1b[5~"}:
                scroll_offset = max(0, scroll_offset - 5)
                continue
            if data in {"]", "\x1b[6~"}:
                scroll_offset += 5
                continue

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\033[?1049l\033[?25h\033[0m")
        sys.stdout.flush()

    return 0


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Full-frame, frame-by-frame XP animation viewer.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument(
        "pattern", nargs="?", default="",
        help=(
            "Case-insensitive substring filter on sprite filenames. "
            "Use comma or | to match multiple terms."
        ),
    )
    parser.add_argument(
        "--dir", type=Path, default=SPRITE_DIR,
        help="Sprite directory (default: assets/sprites/).",
    )
    parser.add_argument(
        "--compare-default", action="store_true",
        help=(
            "Show a side-by-side frame against the default player-family sprite "
            "(attack-body.xp for attack, plydie-body.xp for death, player-body.xp otherwise)."
        ),
    )
    parser.add_argument(
        "--compare-default-random", "--compare-default--random",
        dest="compare_default_random",
        action="store_true",
        help=(
            "Show random body+wearable combinations with test-only fixtures on the left "
            "and normal/default wearables on the right."
        ),
    )
    parser.add_argument(
        "--compare", default="",
        help="Sprite filename or substring to show side-by-side with the selected test XP.",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Deterministic RNG seed for --compare-default-random.",
    )
    parser.add_argument(
        "--combination-check", action="store_true",
        help="FL-2345 combination-check viewer: verify equipped/non-equip permutations exist in the bundle.",
    )
    parser.add_argument(
        "--variants", default="",
        help=(
            "Style-variant browse mode. Pass a base name (e.g. 'wolfie-body-rear' or "
            "'wolfie-mounted-idle-rider-body') and the viewer auto-loads every matching "
            "variant: <base>.xp, <base>-gold.xp, <base>-armored.xp, <base>-armored-gold.xp, "
            "<base>-armored-h.xp, <base>-armored-ws.xp, <base>-armored-hws.xp, "
            "<base>-armored-gold-h.xp, <base>-armored-gold-ws.xp, <base>-armored-gold-hws.xp. "
            "Cycle with ←/→. Default variant shown first."
        ),
    )
    args = parser.parse_args(_normalize_cli_argv(sys.argv[1:]))
    if args.combination_check:
        return _run_combination_check()

    if args.variants:
        base = args.variants.removesuffix(".xp")
        suffixes = [
            "", "-gold", "-armored", "-armored-gold",
            "-armored-h", "-armored-ws", "-armored-hws",
            "-armored-gold-h", "-armored-gold-ws", "-armored-gold-hws",
        ]
        candidates = [f"{base}{suf}.xp" for suf in suffixes]
        existing = []
        missing = []
        for c in candidates:
            if (args.dir / c).is_file():
                existing.append(c.removesuffix(".xp"))
            else:
                missing.append(c)
        if not existing:
            print(f"No files match variant base '{base}' in {args.dir}", file=sys.stderr)
            return 1
        print(f"Found {len(existing)} variant(s):", file=sys.stderr)
        for v in existing:
            print(f"  {v}.xp", file=sys.stderr)
        if missing:
            print(f"Missing {len(missing)} (not yet authored):", file=sys.stderr)
            for m in missing:
                print(f"  {m}", file=sys.stderr)
        pattern = ",".join(existing)
        return run(
            sprite_dir=args.dir,
            pattern=pattern,
            compare_default=False,
            compare=base,  # side-by-side against the default base
            compare_default_random=False,
            random_seed=None,
        )

    return run(
        sprite_dir=args.dir,
        pattern=args.pattern,
        compare_default=args.compare_default,
        compare=args.compare,
        compare_default_random=args.compare_default_random,
        random_seed=args.seed,
    )


if __name__ == "__main__":
    raise SystemExit(main())
