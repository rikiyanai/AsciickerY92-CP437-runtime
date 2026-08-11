#!/usr/bin/env python3
"""Canonical FL-4482 offline font-chain discovery and one-cell rasterization.

This module is the sole owner of font priority, font hashes, cmap selection,
and 16x16 raster normalization. Diagnostic browsers may consume this module;
they must not discover a second ambient font chain.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "assets/fonts"
CELL_PX = 16
INK_THRESHOLD = 96

# FL-4482: ordered, content-addressed compiler inputs. Ambient system fonts and
# newly dropped files are not admission inputs until this list is amended.
PINNED_FONT_CHAIN: tuple[tuple[str, str, bool], ...] = (
    ("unifont-17.0.04.otf", "d1f664a9753b9c6b7ff357128749e32b5d3eee90c7c03618363fabd43a39b5b7", True),
    ("BabelStoneHan.ttf", "d8bb747b3fdccd84a60bd0aa56bb90937270d0bd15f1101cd8f2a5a3709dd0a3", False),
    ("NotoSansAnatolianHieroglyphs-Regular.ttf", "be46e1111764b0c65e448634f7fb410a14242ccbffd66002681d8f16a98bf26d", False),
    ("NotoSansCuneiform-Regular.ttf", "b4e0a892514fcb1beb081c8b652fb36689f77105491d1796c2c3f5a59b4524d2", False),
    ("NotoSansEgyptianHieroglyphs-Regular.ttf", "b792accc6207ca9caa59fc67f53926b6985a99d255dd432d7c68b68a6dfd62f8", False),
    ("NotoSerifTangut-Regular.ttf", "f6098b9210e0a10f2ba2e4fbbb496c1df3594bf84555090929750c79ac75495d", False),
    ("Roboto-Medium.ttf", "8559132c89ad51d8a2ba5b171887a44a7ba93776e205f553573de228e64b45f8", False),
    ("unifont_upper-17.0.04.otf", "adce94bc065675242e9d65d8cc4c250de9471041f0c0f22deb9d14086d9f59ea", True),
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_font_chain() -> list[Path]:
    """Return the exact pinned compiler chain after fail-closed hash checks."""
    paths: list[Path] = []
    for name, expected, _pixel in PINNED_FONT_CHAIN:
        path = FONT_DIR / name
        if not path.is_file():
            raise SystemExit(f"missing pinned FL-4482 font: {path.relative_to(ROOT)}")
        observed = sha256_file(path)
        if observed != expected:
            raise SystemExit(f"FL-4482 font hash mismatch for {name}: {observed} != {expected}")
        paths.append(path)
    return paths


def font_chain_receipt() -> list[dict[str, object]]:
    return [
        {
            "priority": priority,
            "path": str((FONT_DIR / name).relative_to(ROOT)),
            "sha256": digest,
            "raster_kind": "native_pixel" if pixel else "outline_crop_fit",
        }
        for priority, (name, digest, pixel) in enumerate(PINNED_FONT_CHAIN)
    ]


@dataclass(frozen=True)
class RasterResult:
    rgba8: bytes
    font_index: int
    font_name: str
    ink_count: int


@dataclass
class _Face:
    path: Path
    cmap: set[int]
    small: ImageFont.FreeTypeFont
    large: ImageFont.FreeTypeFont | None


class CanonicalFontChain:
    """First-cmap-hit resolution plus deterministic 16x16 monochrome raster."""

    def __init__(self, cell_px: int = CELL_PX):
        self.cell_px = cell_px
        self.faces: list[_Face] = []
        self.owner_by_scalar: dict[int, int] = {}
        for font_index, path in enumerate(discover_font_chain()):
            _name, _digest, pixel = PINNED_FONT_CHAIN[font_index]
            tt = TTFont(str(path), fontNumber=0, lazy=True)
            cmap = {
                int(cp)
                for cp, glyph_name in (tt.getBestCmap() or {}).items()
                if glyph_name != ".notdef"
            }
            tt.close()
            small = ImageFont.truetype(str(path), cell_px)
            large = None if pixel else ImageFont.truetype(str(path), cell_px * 6)
            self.faces.append(_Face(path=path, cmap=cmap, small=small, large=large))
            for cp in cmap:
                self.owner_by_scalar.setdefault(cp, font_index)

    @property
    def font_cps(self) -> set[int]:
        return set(self.owner_by_scalar)

    def font_index_for(self, cp: int) -> int | None:
        return self.owner_by_scalar.get(cp)

    def font_name_for(self, cp: int) -> str | None:
        index = self.font_index_for(cp)
        return None if index is None else self.faces[index].path.name

    def render(self, cp: int) -> RasterResult | None:
        font_index = self.font_index_for(cp)
        if font_index is None:
            return None
        face = self.faces[font_index]
        ch = chr(cp)
        if face.large is None:
            image = Image.new("L", (self.cell_px, self.cell_px), 0)
            ImageDraw.Draw(image).text((0, 0), ch, fill=255, font=face.small)
        else:
            image = self._render_outline(ch, face.large)
        raw = image.tobytes()
        rgba = bytearray(self.cell_px * self.cell_px * 4)
        ink_count = 0
        for index, value in enumerate(raw):
            if value <= INK_THRESHOLD:
                continue
            out = index * 4
            rgba[out:out + 4] = b"\xff\xff\xff\xff"
            ink_count += 1
        return RasterResult(bytes(rgba), font_index, face.path.name, ink_count)

    def _render_outline(self, ch: str, font: ImageFont.FreeTypeFont) -> Image.Image:
        n = self.cell_px
        bbox = font.getbbox(ch)
        if not bbox:
            return Image.new("L", (n, n), 0)
        width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if width <= 0 or height <= 0:
            return Image.new("L", (n, n), 0)
        pad = 6
        canvas = Image.new("L", (width + pad * 2, height + pad * 2), 0)
        ImageDraw.Draw(canvas).text((pad - bbox[0], pad - bbox[1]), ch, fill=255, font=font)
        ink = canvas.getbbox()
        if ink is None:
            return Image.new("L", (n, n), 0)
        crop = canvas.crop(ink)
        scale = min(n / crop.width, n / crop.height)
        out_width = max(1, round(crop.width * scale))
        out_height = max(1, round(crop.height * scale))
        resized = crop.resize((out_width, out_height), Image.Resampling.LANCZOS)
        output = Image.new("L", (n, n), 0)
        output.paste(resized, ((n - out_width) // 2, (n - out_height) // 2))
        return output
