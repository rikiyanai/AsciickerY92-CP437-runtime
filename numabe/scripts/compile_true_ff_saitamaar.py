#!/usr/bin/env python3
"""Compile the FF-private native Saitamaar text atlas.

This compiler has deliberately small semantics.  It rasterizes ordinary
Unicode text at the font's native 16 px size, keeps Pillow's exact native
advance for every scalar, and writes a binary (mode ``1``) atlas plus a JSON
catalog.  A candidate is an ordered string of Unicode scalars; spaces and
line-feeds remain ordinary text controls and are never replaced by a fallback
or by an authored fragment.

The reference corpus is a capability catalog only.  It is not renderer
vocabulary.  A renderer vocabulary is admitted only by an explicit
``--candidate-text`` or ``--candidate-file`` input, and the catalog records
that distinction in separate fields.

The output directory is intentionally outside every shared atlas/terminal
owner.  The source TTF is read and hashed, never copied into the repository.

Usage (from the repository root)::

    python3 scripts/compile_true_ff_saitamaar.py \
        --font /Users/r/Projects/ascii-art-archive/collections/aahub/Saitamaar.ttf

    python3 scripts/compile_true_ff_saitamaar.py --font FONT.ttf \
        --candidate-file ordinary.txt

The default corpus and reference manifest are the checked-in external
AAHub collection named by the FF-private asset contract.  Both can be
overridden for a reproducible local compile.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_DIR = Path(
    "/Users/r/Projects/ascii-art-archive/collections/aahub/bakuhatsu-kemuri"
)
DEFAULT_FONT = Path(
    "/Users/r/Projects/ascii-art-archive/collections/aahub/Saitamaar.ttf"
)
DEFAULT_OUT = ROOT / "godot_project/assets/glyphs/true_ff/saitamaar"
DEFAULT_ATLAS_FILENAME = "atlas.png"
DEFAULT_CATALOG_FILENAME = "catalog.json"
FONT_PX = 16
RECOMMENDED_ROW_STEP_PX = 17
REFERENCE_PAD_PX = 8
REFERENCE_THRESHOLD = 128
ATLAS_WIDTH_PX = 1024
ATLAS_PADDING_PX = 1
LINE_FEED = "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _json_number(value: float) -> int | float:
    """Use compact integer JSON for integral native metrics."""

    return int(value) if float(value).is_integer() else float(value)


def _name_value(font: TTFont, name_id: int) -> str | None:
    """Read the first useful name-table string without inferring a license."""

    for record in font["name"].names:
        if record.nameID != name_id:
            continue
        try:
            value = record.toUnicode()
        except Exception:
            value = record.string.decode("latin-1", errors="replace")
        if value:
            return value
    return None


def _load_unicode_cmap(font: TTFont) -> dict[int, str]:
    cmap: dict[int, str] = {}
    for table in font["cmap"].tables:
        if table.isUnicode():
            cmap.update(table.cmap)
    return cmap


def _read_reference_corpus(corpus_dir: Path) -> tuple[str, list[Path]]:
    files = sorted(corpus_dir.glob("resK-*.txt"))
    if not files:
        raise ValueError(f"reference corpus has no resK-*.txt files: {corpus_dir}")
    return "".join(path.read_text(encoding="utf-8") for path in files), files


def _read_candidate(args: argparse.Namespace) -> tuple[str | None, str]:
    if args.candidate_text is not None and args.candidate_file is not None:
        raise ValueError("use exactly one of --candidate-text and --candidate-file")
    if args.candidate_file is not None:
        path = Path(args.candidate_file)
        return path.read_text(encoding="utf-8"), str(path)
    if args.candidate_text is not None:
        return args.candidate_text, "--candidate-text"
    return None, "none (no renderer vocabulary admitted)"


def _validate_text(text: str, label: str, cmap: dict[int, str]) -> None:
    """Reject missing scalars and non-ordinary controls without fallback."""

    missing: list[str] = []
    controls: list[str] = []
    for char in text:
        cp = ord(char)
        if char == LINE_FEED:
            continue
        if cp < 0x20 or cp == 0x7F:
            controls.append(f"U+{cp:04X}")
        if cp not in cmap:
            missing.append(f"U+{cp:04X} {char!r}")
    if controls:
        raise ValueError(
            f"{label} contains unsupported control scalars (only U+000A line-feed is allowed): "
            + ", ".join(sorted(set(controls)))
        )
    if missing:
        raise ValueError(
            f"{label} contains code points absent from the explicit font cmap; no fallback is allowed: "
            + ", ".join(sorted(set(missing)))
        )


def _advance_px(font: TTFont, cmap: dict[int, str], cp: int, font_px: int) -> float:
    glyph_name = cmap[cp]
    units_per_em = int(font["head"].unitsPerEm)
    raw_advance = int(font["hmtx"][glyph_name][0])
    return raw_advance * font_px / units_per_em


@dataclass
class RasterGlyph:
    char: str
    codepoint: int
    advance_px: int | float
    bearing_x: int
    bearing_y: int
    logical_bbox: tuple[int, int, int, int]
    ink_bounds: tuple[int, int, int, int]
    ink: Image.Image | None


def _rasterize(
    char: str,
    font: ImageFont.FreeTypeFont,
    exact_advance: float,
) -> RasterGlyph:
    """Rasterize one scalar on the shared font row origin.

    Pillow's default ``la`` origin is the reference renderer's origin: the
    row starts at y=0 and the glyph's logical top is a bearing from that row
    top.  The temporary canvas is larger than the logical bbox so negative
    bearings and natural overhang are retained.  The only coverage operation
    is the reference recipe's ``pixel < 128`` bilevel threshold.
    """

    left, top, right, bottom = (int(v) for v in font.getbbox(char, anchor="la"))
    margin = 4
    min_x = min(0, left)
    max_x = max(0, right, math.ceil(exact_advance))
    min_y = min(0, top)
    max_y = max(RECOMMENDED_ROW_STEP_PX - 1, bottom)
    canvas_width = max_x - min_x + 2 * margin + 1
    canvas_height = max_y - min_y + 2 * margin + 1
    origin_x = margin - min_x
    origin_y = margin - min_y
    gray = Image.new("L", (canvas_width, canvas_height), color=255)
    ImageDraw.Draw(gray).text((origin_x, origin_y), char, font=font, fill=0)
    binary = gray.point(
        lambda pixel: 0 if pixel < REFERENCE_THRESHOLD else 255, mode="1"
    )
    dark = binary.point(lambda pixel: 255 - pixel, mode="1")
    dark_bbox = dark.getbbox()
    if dark_bbox is None:
        ink_bounds = (0, 0, 0, 0)
        ink = None
    else:
        ink_bounds = (
            dark_bbox[0] - origin_x,
            dark_bbox[1] - origin_y,
            dark_bbox[2] - origin_x,
            dark_bbox[3] - origin_y,
        )
        ink = binary.crop(dark_bbox)
    return RasterGlyph(
        char=char,
        codepoint=ord(char),
        advance_px=_json_number(exact_advance),
        bearing_x=left,
        bearing_y=top,
        logical_bbox=(left, top, right, bottom),
        ink_bounds=ink_bounds,
        ink=ink,
    )


def _pack(glyphs: list[RasterGlyph]) -> tuple[Image.Image, dict[int, list[int]]]:
    """Pack non-empty masks without overlap or geometry mutation."""

    placements: dict[int, list[int]] = {}
    cursor_x = ATLAS_PADDING_PX
    cursor_y = ATLAS_PADDING_PX
    shelf_height = 0
    for glyph in glyphs:
        if glyph.ink is None:
            placements[glyph.codepoint] = [0, 0, 0, 0]
            continue
        width, height = glyph.ink.size
        if width >= ATLAS_WIDTH_PX - 2 * ATLAS_PADDING_PX:
            raise ValueError(
                f"glyph U+{glyph.codepoint:04X} is wider than the fixed atlas width"
            )
        if cursor_x + width + ATLAS_PADDING_PX > ATLAS_WIDTH_PX:
            cursor_x = ATLAS_PADDING_PX
            cursor_y += shelf_height + ATLAS_PADDING_PX
            shelf_height = 0
        rect = [cursor_x, cursor_y, cursor_x + width, cursor_y + height]
        placements[glyph.codepoint] = rect
        cursor_x += width + ATLAS_PADDING_PX
        shelf_height = max(shelf_height, height)
    atlas_height = max(1, cursor_y + shelf_height + ATLAS_PADDING_PX)
    atlas = Image.new("1", (ATLAS_WIDTH_PX, atlas_height), color=255)
    for glyph in glyphs:
        if glyph.ink is None:
            continue
        rect = placements[glyph.codepoint]
        atlas.paste(glyph.ink, (rect[0], rect[1]))
    return atlas, placements


def _ordered_unique(text: str | None, *, include_newline: bool = False) -> list[str]:
    if text is None:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for char in text:
        if char == LINE_FEED and not include_newline:
            continue
        if char not in seen:
            seen.add(char)
            result.append(char)
    return result


def _metric(
    glyph: RasterGlyph,
    atlas_rect: list[int],
) -> dict[str, object]:
    return {
        "char": glyph.char,
        "codepoint": glyph.codepoint,
        "advance_px": glyph.advance_px,
        "bearing_x": glyph.bearing_x,
        "bearing_y": glyph.bearing_y,
        "atlas_rect": atlas_rect,
        "ink_bounds": list(glyph.ink_bounds),
        "has_ink": glyph.ink is not None,
    }


def compile_catalog(args: argparse.Namespace) -> tuple[dict[str, object], Image.Image, Path, Path]:
    font_path = Path(args.font)
    corpus_dir = Path(args.corpus_dir)
    reference_manifest = Path(args.reference_manifest)
    output_dir = Path(args.out)
    if args.font_px != FONT_PX:
        raise ValueError(
            f"Saitamaar is a native-{FONT_PX}px contract; --font-px {args.font_px} is not permitted"
        )
    if not font_path.is_file():
        raise ValueError(f"explicit font input does not exist: {font_path}")
    if not corpus_dir.is_dir():
        raise ValueError(f"reference corpus directory does not exist: {corpus_dir}")
    if not reference_manifest.is_file():
        raise ValueError(f"reference manifest does not exist: {reference_manifest}")

    corpus_text, corpus_files = _read_reference_corpus(corpus_dir)
    manifest_bytes = reference_manifest.read_bytes()
    manifest_text = manifest_bytes.decode("utf-8")
    required_recipe_markers = (
        "16px",
        "17px line step",
        "antialiasing OFF",
        "<128 threshold",
        "8px pad",
    )
    absent_markers = [marker for marker in required_recipe_markers if marker not in manifest_text]
    if absent_markers:
        raise ValueError(
            "reference manifest does not state the required native binary recipe; missing: "
            + ", ".join(absent_markers)
        )

    candidate_text, candidate_source = _read_candidate(args)
    font_tt = TTFont(str(font_path), lazy=False)
    cmap = _load_unicode_cmap(font_tt)
    _validate_text(corpus_text, "reference corpus", cmap)
    if candidate_text is not None:
        _validate_text(candidate_text, "candidate text", cmap)

    all_chars = _ordered_unique(corpus_text, include_newline=False)
    for char in _ordered_unique(candidate_text, include_newline=False):
        if char not in all_chars:
            all_chars.append(char)
    all_chars.sort(key=ord)

    try:
        layout = ImageFont.Layout.BASIC
    except AttributeError:
        layout = None
    if layout is None:
        pillow_font = ImageFont.truetype(str(font_path), FONT_PX)
    else:
        pillow_font = ImageFont.truetype(str(font_path), FONT_PX, layout_engine=layout)
    ascent_px, descent_px = (int(value) for value in pillow_font.getmetrics())

    glyphs: list[RasterGlyph] = []
    for char in all_chars:
        cp = ord(char)
        exact_advance = _advance_px(font_tt, cmap, cp, FONT_PX)
        pillow_advance = float(pillow_font.getlength(char))
        if not math.isclose(exact_advance, pillow_advance, abs_tol=1e-6):
            raise ValueError(
                f"font advance disagreement for U+{cp:04X}: fontTools={exact_advance} Pillow={pillow_advance}"
            )
        glyphs.append(_rasterize(char, pillow_font, exact_advance))

    atlas, placements = _pack(glyphs)
    atlas_filename = str(args.atlas_filename)
    catalog_filename = str(args.catalog_filename)
    atlas_path = output_dir / atlas_filename
    catalog_path = output_dir / catalog_filename
    source_font_sha = sha256_file(font_path)
    corpus_chars = _ordered_unique(corpus_text, include_newline=True)
    corpus_codepoints = [ord(char) for char in corpus_chars]
    renderable_corpus_chars = _ordered_unique(corpus_text, include_newline=False)
    approved_chars = _ordered_unique(candidate_text, include_newline=False)
    approved_codepoints = [ord(char) for char in approved_chars]
    glyph_metrics = [_metric(glyph, placements[glyph.codepoint]) for glyph in glyphs]

    name_records = {
        "family": _name_value(font_tt, 1),
        "subfamily": _name_value(font_tt, 2),
        "full_name": _name_value(font_tt, 4),
        "version": _name_value(font_tt, 5),
        "postscript_name": _name_value(font_tt, 6),
        "copyright_or_notice": _name_value(font_tt, 0),
        "manufacturer": _name_value(font_tt, 8),
        "designer": _name_value(font_tt, 9),
        "license_description": _name_value(font_tt, 13),
        "license_info_url": _name_value(font_tt, 14),
    }
    license_declared = bool(name_records["license_description"] or name_records["license_info_url"])
    catalog: dict[str, object] = {
        "schema": "true_ff.saitamaar.catalog.v1",
        "font_px": FONT_PX,
        "ascent_px": ascent_px,
        "descent_px": descent_px,
        "baseline_px": ascent_px,
        "recommended_row_step_px": RECOMMENDED_ROW_STEP_PX,
        "atlas_filename": atlas_filename,
        "source_font_sha256": source_font_sha,
        "text_contract": {
            "kind": "ordinary_unicode_text",
            "ordered_scalars": True,
            "spaces_preserved": True,
            "newlines_preserved": True,
            "exact_native_advances": True,
            "forbidden_operations": [
                "fragment_assembly",
                "forced_overlap",
                "scaling",
                "warping",
                "position_override",
                "fallback_substitution",
            ],
        },
        "source_font": {
            "input_path": str(font_path),
            "sha256": source_font_sha,
            "family": name_records["family"],
            "subfamily": name_records["subfamily"],
            "full_name": name_records["full_name"],
            "version": name_records["version"],
            "postscript_name": name_records["postscript_name"],
            "copyright_or_notice": name_records["copyright_or_notice"],
            "manufacturer": name_records["manufacturer"],
            "designer": name_records["designer"],
            "license": {
                "status": "declared_in_font_metadata" if license_declared else "not_stated",
                "description": name_records["license_description"],
                "info_url": name_records["license_info_url"],
                "fsType": int(font_tt["OS/2"].fsType),
                "permission_note": "Metadata inspection is not a permission grant.",
            },
        },
        "reference_recipe": {
            "manifest_path": str(reference_manifest),
            "manifest_sha256": sha256_bytes(manifest_bytes),
            "render_size_px": FONT_PX,
            "row_step_px": RECOMMENDED_ROW_STEP_PX,
            "padding_px": REFERENCE_PAD_PX,
            "antialiasing": False,
            "threshold": {"operator": "<", "value": REFERENCE_THRESHOLD},
            "coverage_encoding": "mode1 black ink (0), white background (255)",
        },
        "coordinate_system": {
            "origin": "the Pillow la (left/ascent) origin at the top of one native text row",
            "baseline": "baseline_px pixels below the row origin; baseline_px equals the native Pillow ascent",
            "x": "positive right in pixels; text advances by advance_px without fitting",
            "y": "positive down in pixels; bearing_y is the logical glyph-top offset from row top",
            "atlas_rect": "[x0, y0, x1, y1), atlas pixels containing the true binary ink crop",
            "ink_bounds": "[x0, y0, x1, y1), true binary ink bounds relative to the row origin",
            "natural_overhang": "preserved; atlas packing never clamps ink to advance_px",
        },
        "atlas": {
            "filename": atlas_filename,
            "format": "PNG mode 1",
            "coverage": "binary; black=ink, white=background",
            "padding_px": ATLAS_PADDING_PX,
            "width_px": atlas.width,
            "height_px": atlas.height,
        },
        "catalog_capability": {
            "kind": "reference_corpus_capability",
            "is_approved_renderer_vocabulary": False,
            "source_directory": str(corpus_dir),
            "source_files": [str(path) for path in corpus_files],
            "chars": corpus_chars,
            "codepoints": corpus_codepoints,
            "renderable_chars": renderable_corpus_chars,
            "renderable_codepoints": [ord(char) for char in renderable_corpus_chars],
            "line_feed_is_control": True,
        },
        "candidate_admission": {
            "kind": "explicit_input_only",
            "source": candidate_source,
            "status": "admitted" if candidate_text is not None else "none",
            "is_derived_from_reference_corpus": False,
            "chars": approved_chars,
            "codepoints": approved_codepoints,
            "ordered_sequence_codepoints": [ord(char) for char in candidate_text] if candidate_text is not None else [],
            "sequence_sha256": sha256_bytes(candidate_text.encode("utf-8")) if candidate_text is not None else None,
            "missing_char_policy": "fail closed; no fallback, substitution, or .notdef",
        },
        "glyphs": glyph_metrics,
        "controls": {
            "line_feed": {
                "char": LINE_FEED,
                "codepoint": ord(LINE_FEED),
                "kind": "line_break",
                "advance_px": 0,
                "atlas_rect": None,
                "ink_bounds": None,
            }
        },
    }
    # The atlas digest is known only after the output encoding is chosen.  The
    # caller adds it after saving, keeping all source metrics deterministic.
    catalog["_atlas_image"] = atlas_path
    catalog["_catalog_path"] = catalog_path
    return catalog, atlas, atlas_path, catalog_path


def _write_outputs(catalog: dict[str, object], atlas: Image.Image, atlas_path: Path, catalog_path: Path) -> None:
    output_dir = atlas_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    atlas.save(atlas_path)
    catalog.pop("_atlas_image", None)
    catalog.pop("_catalog_path", None)
    catalog["atlas"]["sha256"] = sha256_file(atlas_path)  # type: ignore[index]
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    glyphs = catalog["glyphs"]
    capabilities = catalog["catalog_capability"]
    candidate = catalog["candidate_admission"]
    print(
        f"WROTE {atlas_path} ({atlas.width}x{atlas.height}, mode={atlas.mode}) and "
        f"{catalog_path} ({len(glyphs)} glyphs; "
        f"reference capability={len(capabilities['codepoints'])}; "
        f"candidate admission={len(candidate['codepoints'])})"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--font",
        required=True,
        help="explicit Saitamaar TTF input; the TTF is hashed, never copied",
    )
    parser.add_argument("--font-px", type=int, default=FONT_PX)
    parser.add_argument("--corpus-dir", default=str(DEFAULT_CORPUS_DIR))
    parser.add_argument(
        "--reference-manifest",
        default=str(DEFAULT_CORPUS_DIR / "manifest.txt"),
    )
    parser.add_argument("--candidate-text")
    parser.add_argument("--candidate-file")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--atlas-filename", default=DEFAULT_ATLAS_FILENAME)
    parser.add_argument("--catalog-filename", default=DEFAULT_CATALOG_FILENAME)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        catalog, atlas, atlas_path, catalog_path = compile_catalog(args)
        _write_outputs(catalog, atlas, atlas_path, catalog_path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
