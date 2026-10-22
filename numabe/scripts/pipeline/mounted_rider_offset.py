#!/usr/bin/env python3
"""Solve mounted rider offsets from raw XP frames using exact cell matching."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.bundle_wizard import semantic_dict
from scripts.pipeline.xp_core import XPFile


SPRITE_DIR = REPO_ROOT / "assets" / "sprites"
AUTO_LAYER_PREFERENCE = (3, 2)
AUTO_LAYER_MIN_REFERENCE_CELLS = 4


@dataclass(frozen=True)
class FrameLayout:
    frame_width: int
    frame_height: int
    angles: int
    projs: int
    anims: list[int]

    @property
    def anim_sum(self) -> int:
        return sum(self.anims)


@dataclass(frozen=True)
class VisibleCell:
    x: int
    y: int
    glyph: int
    fg: tuple[int, int, int]
    bg: tuple[int, int, int]


def _resolve_path(path_or_name: str, sprite_dir: Path = SPRITE_DIR) -> Path:
    candidate = Path(path_or_name).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    if not candidate.is_absolute():
        direct = (sprite_dir / path_or_name).resolve()
        if direct.is_file():
            return direct
    raise FileNotFoundError(path_or_name)


def _load_xp(path: Path) -> XPFile:
    with contextlib.redirect_stdout(io.StringIO()):
        return XPFile(path)


def _parse_layout(xp: XPFile) -> FrameLayout:
    meta = xp.get_metadata()
    if meta is None:
        raise ValueError("XP file has no metadata layers")
    if not xp.layers:
        raise ValueError("XP file has no layers")
    angles = int(meta["angles"])
    projs = int(meta["projs"])
    anims = [int(value) for value in meta["anims"]]
    if angles <= 0 or projs <= 0 or not anims:
        raise ValueError(f"invalid XP metadata: {meta}")
    l0 = xp.layers[0]
    total_frames = projs * sum(anims)
    if total_frames <= 0:
        raise ValueError(f"invalid total frame count from metadata: {meta}")
    frame_width = l0.width // total_frames
    frame_height = l0.height // angles
    return FrameLayout(
        frame_width=frame_width,
        frame_height=frame_height,
        angles=angles,
        projs=projs,
        anims=anims,
    )


def _frame_origin(
    layout: FrameLayout,
    *,
    angle: int,
    anim_index: int,
    frame_index: int,
    proj: int,
) -> tuple[int, int]:
    x0 = (proj * layout.anim_sum + sum(layout.anims[:anim_index]) + frame_index) * layout.frame_width
    y0 = angle * layout.frame_height
    return x0, y0


def _layer0_key_rgb(xp: XPFile, *, sheet_x: int, sheet_y: int) -> tuple[int, int, int]:
    _glyph, _fg, bg = xp.layers[0].data[sheet_y][sheet_x]
    return tuple(bg)


def _cell_visible(
    glyph: int,
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
    *,
    layer0_key_rgb: tuple[int, int, int],
) -> bool:
    flags = semantic_dict._engine_cell_transparency_flags(
        (glyph, fg, bg),
        layer0_key_rgb=layer0_key_rgb,
    )
    return bool(flags["engine_visible"])


def frame_cells(
    xp: XPFile,
    layout: FrameLayout,
    *,
    angle: int,
    anim_index: int,
    frame_index: int,
    proj: int,
    layer_index: int,
) -> list[VisibleCell]:
    if layer_index < 0 or layer_index >= len(xp.layers):
        raise ValueError(f"layer_index {layer_index} out of range for asset with {len(xp.layers)} layers")
    if angle < 0 or angle >= layout.angles:
        raise ValueError(f"angle {angle} out of range for layout with {layout.angles} angles")
    if proj < 0 or proj >= layout.projs:
        raise ValueError(f"proj {proj} out of range for layout with {layout.projs} projections")
    if anim_index < 0 or anim_index >= len(layout.anims):
        raise ValueError(f"anim_index {anim_index} out of range for layout with {len(layout.anims)} animations")
    anim_length = layout.anims[anim_index]
    if frame_index < 0 or frame_index >= anim_length:
        raise ValueError(f"frame_index {frame_index} out of range for anim {anim_index} with {anim_length} frames")

    layer = xp.layers[layer_index]
    x0, y0 = _frame_origin(
        layout,
        angle=angle,
        anim_index=anim_index,
        frame_index=frame_index,
        proj=proj,
    )
    cells: list[VisibleCell] = []
    for local_y in range(layout.frame_height):
        for local_x in range(layout.frame_width):
            glyph, fg, bg = layer.data[y0 + local_y][x0 + local_x]
            fg_rgb = tuple(fg)
            bg_rgb = tuple(bg)
            key_rgb = _layer0_key_rgb(xp, sheet_x=x0 + local_x, sheet_y=y0 + local_y)
            if _cell_visible(glyph, fg_rgb, bg_rgb, layer0_key_rgb=key_rgb):
                cells.append(
                    VisibleCell(
                        x=local_x,
                        y=local_y,
                        glyph=int(glyph),
                        fg=fg_rgb,
                        bg=bg_rgb,
                    )
                )
    return cells


def _auto_layer_candidates(player_xp: XPFile, mounted_xp: XPFile) -> list[int]:
    return [
        layer_index
        for layer_index in AUTO_LAYER_PREFERENCE
        if layer_index < len(player_xp.layers) and layer_index < len(mounted_xp.layers)
    ]


def score_offset(
    reference_cells: list[VisibleCell],
    target_cells: list[VisibleCell],
    *,
    dx: int,
    dy: int,
) -> dict[str, int | float]:
    target_by_xy = {(cell.x, cell.y): (cell.glyph, cell.fg, cell.bg) for cell in target_cells}
    matches = 0
    overlaps = 0
    mismatches = 0
    for cell in reference_cells:
        target = target_by_xy.get((cell.x + dx, cell.y + dy))
        if target is None:
            continue
        overlaps += 1
        if target == (cell.glyph, cell.fg, cell.bg):
            matches += 1
        else:
            mismatches += 1
    total = len(reference_cells)
    coverage = (matches / total) if total else 0.0
    return {
        "dx": dx,
        "dy": dy,
        "matches": matches,
        "overlaps": overlaps,
        "mismatches": mismatches,
        "reference_cells": total,
        "target_cells": len(target_cells),
        "coverage": round(coverage, 6),
    }


def _offset_score_tuple(result: dict[str, int | float]) -> tuple[int, int, int, int]:
    return (
        int(result["matches"]),
        int(result["overlaps"]),
        -int(result["mismatches"]),
        -(abs(int(result["dx"])) + abs(int(result["dy"]))),
    )


def best_offset(
    reference_cells: list[VisibleCell],
    target_cells: list[VisibleCell],
    *,
    min_dx: int,
    max_dx: int,
    min_dy: int,
    max_dy: int,
) -> dict[str, int | float]:
    best: tuple[tuple[int, int, int, int], dict[str, int | float]] | None = None
    best_tie_count = 0
    for dx in range(min_dx, max_dx + 1):
        for dy in range(min_dy, max_dy + 1):
            result = score_offset(reference_cells, target_cells, dx=dx, dy=dy)
            score = _offset_score_tuple(result)
            if best is None or score > best[0]:
                best = (score, result)
                best_tie_count = 1
            elif best is not None and score == best[0]:
                best_tie_count += 1
    assert best is not None
    winner = dict(best[1])
    winner["score_tie_count"] = best_tie_count
    winner["exact_match"] = (
        int(winner["reference_cells"]) > 0
        and int(winner["matches"]) == int(winner["reference_cells"])
        and int(winner["mismatches"]) == 0
    )
    return winner


def _auto_layer_is_valid(
    per_angle: list[dict[str, int | float]],
    *,
    min_reference_cells: int = AUTO_LAYER_MIN_REFERENCE_CELLS,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for entry in per_angle:
        angle = int(entry["angle"])
        if int(entry["reference_cells"]) < min_reference_cells:
            reasons.append(
                f"angle {angle}: reference_cells {entry['reference_cells']} < {min_reference_cells}"
            )
        if not bool(entry["exact_match"]):
            reasons.append(
                f"angle {angle}: exact_match=false matches={entry['matches']} mismatches={entry['mismatches']}"
            )
        if int(entry["score_tie_count"]) != 1:
            reasons.append(
                f"angle {angle}: ambiguous best offset tie_count={entry['score_tie_count']}"
            )
    return (not reasons, reasons)


def _evaluate_layer(
    player_xp: XPFile,
    mounted_xp: XPFile,
    player_layout: FrameLayout,
    mounted_layout: FrameLayout,
    *,
    layer_index: int,
    anim_index: int,
    frame_index: int,
    proj: int,
    min_dx: int,
    max_dx: int,
    min_dy: int,
    max_dy: int,
) -> list[dict[str, int | float]]:
    per_angle: list[dict[str, int | float]] = []
    for angle in range(player_layout.angles):
        player_cells = frame_cells(
            player_xp,
            player_layout,
            angle=angle,
            anim_index=anim_index,
            frame_index=frame_index,
            proj=proj,
            layer_index=layer_index,
        )
        mounted_cells = frame_cells(
            mounted_xp,
            mounted_layout,
            angle=angle,
            anim_index=anim_index,
            frame_index=frame_index,
            proj=proj,
            layer_index=layer_index,
        )
        best = best_offset(
            player_cells,
            mounted_cells,
            min_dx=min_dx,
            max_dx=max_dx,
            min_dy=min_dy,
            max_dy=max_dy,
        )
        best["angle"] = angle
        per_angle.append(best)
    return per_angle


def build_report(
    player_path: Path,
    mounted_path: Path,
    *,
    anim_index: int,
    frame_index: int,
    proj: int,
    layer: str | int,
    min_dx: int,
    max_dx: int,
    min_dy: int,
    max_dy: int,
) -> dict[str, object]:
    player_xp = _load_xp(player_path)
    mounted_xp = _load_xp(mounted_path)
    player_layout = _parse_layout(player_xp)
    mounted_layout = _parse_layout(mounted_xp)

    if player_layout.angles != mounted_layout.angles:
        raise ValueError("player and mounted files disagree on angle count")
    if anim_index < 0 or anim_index >= min(len(player_layout.anims), len(mounted_layout.anims)):
        raise ValueError("invalid animation index for selected files")
    if frame_index < 0 or frame_index >= min(player_layout.anims[anim_index], mounted_layout.anims[anim_index]):
        raise ValueError("invalid frame index for selected animation")
    if proj < 0 or proj >= min(player_layout.projs, mounted_layout.projs):
        raise ValueError("invalid projection index for selected files")

    layer_selection: dict[str, object]
    if layer == "auto":
        candidates: list[dict[str, object]] = []
        chosen_layer: int | None = None
        per_angle: list[dict[str, int | float]] | None = None
        available_candidates = set(_auto_layer_candidates(player_xp, mounted_xp))
        for candidate_layer in AUTO_LAYER_PREFERENCE:
            if candidate_layer not in available_candidates:
                candidates.append(
                    {
                        "layer": candidate_layer,
                        "valid": False,
                        "reasons": ["layer missing from one or both assets"],
                    }
                )
                continue
            candidate_angles = _evaluate_layer(
                player_xp,
                mounted_xp,
                player_layout,
                mounted_layout,
                layer_index=candidate_layer,
                anim_index=anim_index,
                frame_index=frame_index,
                proj=proj,
                min_dx=min_dx,
                max_dx=max_dx,
                min_dy=min_dy,
                max_dy=max_dy,
            )
            valid, reasons = _auto_layer_is_valid(candidate_angles)
            candidates.append(
                {
                    "layer": candidate_layer,
                    "valid": valid,
                    "reasons": reasons,
                    "per_angle": candidate_angles,
                }
            )
            if valid and chosen_layer is None:
                chosen_layer = candidate_layer
                per_angle = candidate_angles
        if chosen_layer is None or per_angle is None:
            summaries = []
            for candidate in candidates:
                layer_label = candidate["layer"]
                reasons = "; ".join(candidate.get("reasons", [])) or "unknown auto-layer failure"
                summaries.append(f"layer {layer_label}: {reasons}")
            raise ValueError(
                "auto layer selection failed closed; no candidate layer passed whole-angle validation: "
                + " | ".join(summaries)
            )
        layer_index = chosen_layer
        layer_selection = {
            "mode": "auto",
            "chosen_layer": chosen_layer,
            "min_reference_cells": AUTO_LAYER_MIN_REFERENCE_CELLS,
            "require_exact_match": True,
            "require_unique_best_offset": True,
            "candidates": candidates,
        }
    else:
        layer_index = int(layer)
        if layer_index >= len(player_xp.layers) or layer_index >= len(mounted_xp.layers):
            raise ValueError("selected layer does not exist in both files")
        per_angle = _evaluate_layer(
            player_xp,
            mounted_xp,
            player_layout,
            mounted_layout,
            layer_index=layer_index,
            anim_index=anim_index,
            frame_index=frame_index,
            proj=proj,
            min_dx=min_dx,
            max_dx=max_dx,
            min_dy=min_dy,
            max_dy=max_dy,
        )
        layer_selection = {
            "mode": "explicit",
            "chosen_layer": layer_index,
        }

    return {
        "player": str(player_path),
        "mounted": str(mounted_path),
        "layer_used": layer_index,
        "layer_selection": layer_selection,
        "anim_index": anim_index,
        "frame_index": frame_index,
        "proj": proj,
        "player_layout": {
            "frame_width": player_layout.frame_width,
            "frame_height": player_layout.frame_height,
            "angles": player_layout.angles,
            "projs": player_layout.projs,
            "anims": player_layout.anims,
        },
        "mounted_layout": {
            "frame_width": mounted_layout.frame_width,
            "frame_height": mounted_layout.frame_height,
            "angles": mounted_layout.angles,
            "projs": mounted_layout.projs,
            "anims": mounted_layout.anims,
        },
        "offset_x_by_angle": [int(entry["dx"]) for entry in per_angle],
        "offset_y_by_angle": [int(entry["dy"]) for entry in per_angle],
        "per_angle": per_angle,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Infer per-angle rider offsets by matching player XP cells against mounted XP cells.",
    )
    parser.add_argument("--player", default="player-0100.xp", help="On-foot player XP path or sprite filename.")
    parser.add_argument("--mounted", default="wolfie-0100.xp", help="Mounted XP path or sprite filename.")
    parser.add_argument("--anim-index", type=int, default=0, help="Animation index within the native strip.")
    parser.add_argument("--frame-index", type=int, default=0, help="Frame index within the selected animation.")
    parser.add_argument("--proj", type=int, default=0, help="Projection index. Usually 0 for projected view.")
    parser.add_argument(
        "--layer",
        default="auto",
        help="Layer index to match. Use 'auto' to prefer layer 3 when both files have it.",
    )
    parser.add_argument("--min-dx", type=int, default=-4, help="Minimum X offset to search.")
    parser.add_argument("--max-dx", type=int, default=8, help="Maximum X offset to search.")
    parser.add_argument("--min-dy", type=int, default=-4, help="Minimum Y offset to search.")
    parser.add_argument("--max-dy", type=int, default=8, help="Maximum Y offset to search.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = parser.parse_args(argv)

    report = build_report(
        _resolve_path(args.player),
        _resolve_path(args.mounted),
        anim_index=args.anim_index,
        frame_index=args.frame_index,
        proj=args.proj,
        layer=args.layer,
        min_dx=args.min_dx,
        max_dx=args.max_dx,
        min_dy=args.min_dy,
        max_dy=args.max_dy,
    )

    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        return 0

    print(f"player={report['player']}")
    print(f"mounted={report['mounted']}")
    print(
        "player_layout="
        f"{report['player_layout']['frame_width']}x{report['player_layout']['frame_height']} "
        f"angles={report['player_layout']['angles']} projs={report['player_layout']['projs']} "
        f"anims={report['player_layout']['anims']}"
    )
    print(
        "mounted_layout="
        f"{report['mounted_layout']['frame_width']}x{report['mounted_layout']['frame_height']} "
        f"angles={report['mounted_layout']['angles']} projs={report['mounted_layout']['projs']} "
        f"anims={report['mounted_layout']['anims']}"
    )
    print(
        f"layer_used={report['layer_used']} anim_index={report['anim_index']} "
        f"frame_index={report['frame_index']} proj={report['proj']}"
    )
    print(f"offset_x_by_angle={report['offset_x_by_angle']}")
    print(f"offset_y_by_angle={report['offset_y_by_angle']}")
    print("per_angle:")
    for entry in report["per_angle"]:
        print(
            f"  angle={entry['angle']} dx={entry['dx']} dy={entry['dy']} "
            f"matches={entry['matches']} overlaps={entry['overlaps']} "
            f"mismatches={entry['mismatches']} coverage={entry['coverage']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
