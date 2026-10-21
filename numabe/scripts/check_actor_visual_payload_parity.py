#!/usr/bin/env python3
"""FL-4162: compare compiled ActorVisualProfile cells with raw XP engine truth."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SCRIPTS = REPO_ROOT / "pipeline-v3" / "scripts"
if str(PIPELINE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SCRIPTS))
REPO_SCRIPTS = REPO_ROOT / "scripts"
if str(REPO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REPO_SCRIPTS))

from xp_core import XPFile  # noqa: E402


MAGENTA = (255, 0, 255)
CYAN = (0, 255, 255)
TRANSPARENT = 255
SWOOSH = 254
Cell = tuple[int, int, int, int]
RawCell = tuple[int, tuple[int, int, int], tuple[int, int, int]]
FrameMeta = tuple[tuple[int, int, int], tuple[int, int]]


def _load_compiler():
    path = REPO_ROOT / "scripts" / "compile_actor_visual_profiles.py"
    spec = importlib.util.spec_from_file_location("fl4162_compiler", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load compiler from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digit(glyph: int) -> int:
    if ord("0") <= glyph <= ord("9"):
        return glyph - ord("0")
    if ord("A") <= glyph <= ord("Z"):
        return glyph + 10 - ord("A")
    if ord("a") <= glyph <= ord("z"):
        return glyph + 10 - ord("a")
    return -1


def _engine_coverage_table() -> list[int]:
    path = REPO_ROOT / "engine" / "glyph_coverage_lookup.cpp"
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"glyph_coverage_table\[256\]\s*=\s*\{(?P<body>.*?)\};",
        text,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError(f"missing glyph coverage table in {path}")
    values = [int(value, 16) for value in re.findall(r"0x[0-9a-fA-F]+", match["body"])]
    if len(values) != 256:
        raise RuntimeError(f"glyph coverage table has {len(values)} entries")
    return values


@dataclass
class ReferenceSprite:
    path: Path
    xp: XPFile
    coverage: list[int]

    @classmethod
    def load(cls, path: Path, coverage: list[int]) -> "ReferenceSprite":
        with contextlib.redirect_stdout(io.StringIO()):
            xp = XPFile(str(path))
        return cls(path=path, xp=xp, coverage=coverage)

    def __post_init__(self) -> None:
        if len(self.xp.layers) < 3:
            raise RuntimeError(f"{self.path}: expected L0/L1/L2")
        meta = self.xp.layers[0]
        raw_angles = _digit(meta.data[0][0][0])
        self.angles = raw_angles if raw_angles > 0 else 1
        self.projs = 2 if raw_angles > 0 else 1
        self.anim_lens: list[int] = []
        for x in range(1, meta.width):
            value = _digit(meta.data[0][x][0])
            if value <= 0:
                break
            self.anim_lens.append(value)
        if not self.anim_lens:
            self.anim_lens = [1]
        self.width = meta.width
        self.height = meta.height
        self.frame_cols = self.projs * sum(self.anim_lens)
        if self.width % self.frame_cols or self.height % self.angles:
            raise RuntimeError(f"{self.path}: invalid frame topology")
        self.frame_width = self.width // self.frame_cols
        self.frame_height = self.height // self.angles
        self.atlas_frames = self.frame_cols * self.angles
        self._flat_layers = [
            [layer.data[y][x] for x in range(layer.width) for y in range(layer.height)]
            for layer in self.xp.layers
        ]

    def _flat(self, layer_index: int) -> list[RawCell]:
        return self._flat_layers[layer_index]

    @staticmethod
    def _rgb2pal(rgb: tuple[int, int, int]) -> int:
        r = (rgb[0] + 25) // 51
        g = (rgb[1] + 25) // 51
        b = (rgb[2] + 25) // 51
        return 16 + 36 * r + 6 * g + b

    @staticmethod
    def _pal2rgb(pal: int) -> tuple[int, int, int]:
        pal -= 16
        r = pal // 36
        pal -= r * 36
        g = pal // 6
        b = pal - g * 6
        return r * 51, g * 51, b * 51

    @staticmethod
    def _lighten(color: int) -> int:
        color -= 16
        r = color // 36
        color -= 36 * r
        g = color // 6
        b = color - 6 * g
        return 16 + min(5, r + 1) * 36 + min(5, g + 1) * 6 + min(5, b + 1)

    def _average(self, glyph: int, fg: int, bg: int, mask: int) -> int:
        coverage = self.coverage[glyph & 0xFF]
        values = [
            (coverage >> shift) & 0xF
            for bit, shift in ((1, 0), (2, 4), (4, 8), (8, 12))
            if mask & bit
        ]
        return fg if sum(values) > len(values) * 2 else bg

    def _merge(
        self, base: RawCell, overlay: RawCell, key: RawCell, final_layer: bool
    ) -> RawCell:
        overlay_glyph, overlay_fg, overlay_bg = overlay
        if final_layer and overlay_fg == CYAN:
            base_glyph, base_fg, base_bg = base
            key_bg = key[2]
            fg_transparent = base_fg == key_bg
            bg_transparent = base_bg == key_bg
            if base_bg == MAGENTA:
                fg_transparent = True
                bg_transparent = True
            swoosh_bg_transparent = overlay_bg == key_bg
            glyph = 219 if overlay_bg == CYAN else overlay_glyph
            if glyph in (0, 32):
                return overlay if overlay_bg != MAGENTA else base
            masks = {220: 3, 221: 5, 222: 10, 223: 12}
            mask = masks.get(glyph, 0)
            if mask:
                fg = TRANSPARENT if fg_transparent else self._rgb2pal(base_fg)
                bg = TRANSPARENT if bg_transparent else self._rgb2pal(base_bg)
                fg_avg = self._average(base_glyph, fg, bg, mask)
                if swoosh_bg_transparent:
                    bg_avg = self._average(base_glyph, fg, bg, 0xF ^ mask)
                    out_fg = CYAN if fg_avg == TRANSPARENT else self._pal2rgb(self._lighten(fg_avg))
                    out_bg = key_bg if fg_avg == TRANSPARENT else self._pal2rgb(bg_avg)
                else:
                    out_fg = CYAN if fg_avg == TRANSPARENT else self._pal2rgb(self._lighten(fg_avg))
                    out_bg = overlay_bg
                return glyph, out_fg, out_bg
            if fg_transparent and bg_transparent:
                return overlay
            out_fg = CYAN if fg_transparent else tuple(min(255, v + 51) for v in base_fg)
            out_bg = CYAN if bg_transparent else tuple(min(255, v + 51) for v in base_bg)
            return base_glyph, out_fg, out_bg
        return overlay if overlay_bg != MAGENTA else base

    def engine_visual(self) -> list[RawCell]:
        visual = self._flat(2)
        key = self._flat(0)
        final = len(self.xp.layers) - 1
        for layer_index in range(3, len(self.xp.layers)):
            overlay = self._flat(layer_index)
            for index, cell in enumerate(overlay):
                visual[index] = self._merge(
                    visual[index], cell, key[index], layer_index == final
                )
        return visual

    @staticmethod
    def _quantize(rgb: tuple[int, int, int], divisor: int) -> int:
        r = (rgb[0] * 5 + 128) // divisor
        g = (rgb[1] * 5 + 128) // divisor
        b = (rgb[2] * 5 + 128) // divisor
        return 16 + 36 * r + 6 * g + b

    def _render_cell(self, index: int, cell: RawCell, divisor: int) -> Cell:
        key = self._flat(0)[index][2]
        height_glyph = self._flat(1)[index][0]
        glyph, fg, bg = cell
        bg_transparent = bg == key
        fg_transparent = fg == key
        if bg == MAGENTA:
            bg_transparent = True
            fg_transparent = True
        out_bg = SWOOSH if bg == CYAN else TRANSPARENT if bg_transparent else self._quantize(bg, divisor)
        out_fg = SWOOSH if fg == CYAN else TRANSPARENT if fg_transparent else self._quantize(fg, divisor)
        spare = _digit(height_glyph)
        return glyph, out_fg, out_bg, spare if spare >= 0 else 255

    def frame_cells(self, visual: list[RawCell], atlas_frame: int) -> list[Cell]:
        angle = atlas_frame // self.frame_cols
        frame_col = atlas_frame % self.frame_cols
        x0 = frame_col * self.frame_width
        y0 = angle * self.frame_height
        divisor = 255 if self.projs < 2 or 2 * frame_col < self.frame_cols else 400
        out: list[Cell] = []
        for y in range(y0 + self.frame_height - 1, y0 - 1, -1):
            for x in range(x0, x0 + self.frame_width):
                index = x * self.height + y
                out.append(self._render_cell(index, visual[index], divisor))
        return out

    def frame_meta(self, atlas_frame: int) -> FrameMeta:
        frame_col = atlas_frame % self.frame_cols
        angle = atlas_frame // self.frame_cols
        reflected = self.projs >= 2 and 2 * frame_col >= self.frame_cols
        ref = [self.frame_width, 2 * self.frame_height if reflected else 0, 0]
        l0 = self._flat(0)
        if self.height >= 2:
            projected = _digit(l0[1][0])
            reflected_y = _digit(l0[1 + self.height][0])
            if not reflected and 0 <= projected <= 2 * self.frame_height:
                ref[1] = projected
            if reflected and 0 <= reflected_y <= 2 * self.frame_height:
                ref[1] = 2 * self.frame_height - reflected_y
        if self.height >= 3:
            projected_z = _digit(l0[2][0])
            reflected_z = _digit(l0[2 + self.height][0])
            if not reflected and projected_z >= 0:
                ref[2] = -projected_z
            if reflected and reflected_z >= 0:
                ref[2] = -reflected_z
        x0 = frame_col * self.frame_width
        y0 = angle * self.frame_height
        meta_xy = [0, 0]
        for y in range(y0 + self.frame_height - 1, y0 - 1, -1):
            for x in range(x0, x0 + self.frame_width):
                if l0[x * self.height + y][0] == 2:
                    meta_xy[0] = (x - x0) * 2 - ref[0]
                    meta_xy[1] = (y0 + self.frame_height - 1 - y) * 2 - ref[1]
        return (ref[0], ref[1], ref[2]), (meta_xy[0], meta_xy[1])


def _cells_hash(cells: list[Cell]) -> str:
    digest = hashlib.sha256()
    for glyph, fg, bg, spare in cells:
        digest.update(struct.pack("<Iiii", glyph, fg, bg, spare))
    return digest.hexdigest()


def _cells_fit_generated_format(cells: list[Cell]) -> bool:
    return all(
        0 <= glyph <= 0xFFFFFFFF
        and 0 <= fg <= 255
        and 0 <= bg <= 255
        and 0 <= spare <= 255
        for glyph, fg, bg, spare in cells
    )


def _meta_hash(rows: list[FrameMeta]) -> str:
    digest = hashlib.sha256()
    for ref, xy in rows:
        digest.update(struct.pack("<iiiii", *ref, *xy))
    return digest.hexdigest()


def _first_mismatch(left: list[Any], right: list[Any]) -> dict[str, Any] | None:
    for index, (left_value, right_value) in enumerate(zip(left, right)):
        if left_value != right_value:
            return {
                "index": index,
                "compiled": left_value,
                "upstream": right_value,
            }
    if len(left) != len(right):
        return {
            "index": min(len(left), len(right)),
            "compiled_length": len(left),
            "upstream_length": len(right),
        }
    return None


def check_payload_parity() -> dict[str, Any]:
    compiler = _load_compiler()
    coverage = _engine_coverage_table()
    reachability, keys = compiler._server_reachable_keys()
    bindings = compiler._profile_bindings(reachability, keys)
    source_results: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    counts = {"pass": 0, "fail": 0}

    for key in keys:
        binding = bindings[compiler._key_sort_tuple(key)]
        composition = binding["composition_kind"]
        row = {
            "profile_id": binding["profile_id"],
            "composition_kind": composition,
            "source_xp_id": binding["source_xp_id"],
        }
        source = binding["source_xp"]
        source_result = source_results.get(source)
        if source_result is None:
            profile, _ = compiler._profile_for_binding(key, binding)
            header, actual_cells, actual_meta = compiler._compile_profile_cells(
                profile, {}
            )
            reference = ReferenceSprite.load(REPO_ROOT / source, coverage)
            visual = reference.engine_visual()
            expected_cells = [
                cell
                for frame in range(reference.atlas_frames)
                for cell in reference.frame_cells(visual, frame)
            ]
            expected_meta = [
                reference.frame_meta(frame) for frame in range(reference.atlas_frames)
            ]
            topology_ok = (
                header["frames"] == reference.atlas_frames
                and header["width"] == reference.frame_width
                and header["height"] == reference.frame_height
                and profile["angles"] == reference.angles
                and profile["projs"] == reference.projs
                and profile["anim_lens"] == reference.anim_lens
            )
            cells_ok = actual_cells == expected_cells
            meta_ok = actual_meta == expected_meta
            format_ok = _cells_fit_generated_format(
                actual_cells
            ) and _cells_fit_generated_format(expected_cells)
            source_result = {
                "status": (
                    "pass"
                    if topology_ok and cells_ok and meta_ok and format_ok
                    else "fail"
                ),
                "topology_match": topology_ok,
                "cells_match": cells_ok,
                "frame_meta_match": meta_ok,
                "generated_cell_format_match": format_ok,
                "compiled_cells_sha256": _cells_hash(actual_cells),
                "upstream_cells_sha256": _cells_hash(expected_cells),
                "compiled_frame_meta_sha256": _meta_hash(actual_meta),
                "upstream_frame_meta_sha256": _meta_hash(expected_meta),
            }
            cell_mismatch = _first_mismatch(actual_cells, expected_cells)
            if cell_mismatch is not None:
                source_result["first_cell_mismatch"] = cell_mismatch
            meta_mismatch = _first_mismatch(actual_meta, expected_meta)
            if meta_mismatch is not None:
                source_result["first_frame_meta_mismatch"] = meta_mismatch
            source_results[source] = source_result
        row.update(source_result)
        counts[row["status"]] += 1
        rows.append(row)

    return {
        "schema": "fl4162.actor_visual_payload_parity.v1",
        "authority": False,
        "expected_owner": "raw XP plus pinned engine composition semantics",
        "actual_owner": "ActorVisualProfile compiler payload",
        "row_count": len(rows),
        "counts": counts,
        "all_pass": counts["fail"] == 0,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = check_payload_parity()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        counts = result["counts"]
        print(
            "actor visual payload parity: "
            f"pass={counts['pass']} fail={counts['fail']}"
        )
    if result["all_pass"]:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
