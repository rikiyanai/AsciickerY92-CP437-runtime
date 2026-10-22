#!/usr/bin/env python3
# =============================================================================
# fl4547_walk_viewer.py
# =============================================================================
# FL-4547 / FL-4561 — versioned walk-cycle research pipeline + terminal viewer.
# -----------------------------------------------------------------------------
# WHAT THIS FILE IS (plain language)
# ----------------------------------
# It turns the CLIPasso outline sketches of a walking figure (8 compass
# directions x 8 walk-cycle frames) into ASCII/Unicode glyph grids, and then:
#
#   * builds ONE animated GIF in the SAME 2x4 grid format as
#     `walk_all_directions.gif` -- all eight directions stepping through their
#     frames in lockstep, so the whole cast walks together, and
#   * plays that same walk animation live in the TERMINAL, with a caption banner
#     that always states which VERSION/iteration it is and the file PATHs.
#
# NEW GLYPH VERSION PALETTE (the hard requirement):
#   Use ALL of Unicode that the pinned Unifont covers -- CJK, box drawing,
#   technical symbols, everything -- EXCEPT emoji. "No emoji" is enforced
#   precisely by subtracting every codepoint carrying an Emoji property in the
#   authoritative Unicode data file `assets/unicode/16.0.0/emoji/emoji-data.txt`
#   (we keep the ASCII keycap bases #, * and 0-9, which are only "emoji" when
#   combined with a variation selector, so dropping them would needlessly lose
#   readable characters). Historical playback never inherits this rule: its
#   banner reports the selected manifest's recorded supply and policy verbatim.
#
# EVERY NEW VERSION (the workflow the operator asked for):
#   `new` writes a Desktop GIF named
#   v<YYYYMMDD>T<HHMMSS>__<version>__walk_all_directions.gif, writes a TRACKED copy under
#   docs/research/ascii/verification/fl4547/walk-version-gifs/, records the run in
#   a tracked registry.json, and stores the glyph grids so the viewer can replay
#   that exact version. The `list` command also reads the semantic-skeletal
#   registry, so pre-glyph rig GIFs stay visible in the same version inventory.
#
# SUBCOMMANDS
#   new   -- compile a new version from CLIPasso outlines (needs the CLIPasso
#            virtualenv: .scratch/gradscii-art/.venv/bin/python, for numpy/PIL/
#            svgpathtools/fonttools). Produces the Desktop GIF + tracked copy.
#   list  -- list every recorded version (stdlib only; any python3).
#   play  -- animate any manifest-backed version's 2x4 walk grid (stdlib only).
#
# EXAMPLES
#   .scratch/gradscii-art/.venv/bin/python scripts/fl4547_walk_viewer.py new \
#       --version unifont-no-emoji
#   python3 scripts/fl4547_walk_viewer.py list
#   python3 scripts/fl4547_walk_viewer.py play              # latest version
#   python3 scripts/fl4547_walk_viewer.py play --montage    # one static frame
# =============================================================================
from __future__ import annotations

# NOTE: only the standard library is imported at module scope, so `list` and
# `play` run under a plain `python3`. The heavy rendering dependencies (numpy,
# Pillow, svgpathtools, the Unifont matcher) are imported lazily inside `new`.
import argparse
import hashlib
import json
import math
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Where tracked, versioned artifacts live (inside docs/ so git keeps them).
TRACK_DIR = REPO / "docs" / "research" / "ascii" / "verification" / "fl4547" / "walk-version-gifs"
REGISTRY = TRACK_DIR / "registry.json"
SKELETON_REGISTRY = (REPO / "docs" / "research" / "ascii" / "verification" /
                     "fl4547" / "skeleton-versions" / "registry.json")
DESKTOP = Path.home() / "Desktop"

# The pinned emoji property table (Unicode 16.0.0) used to subtract emoji.
EMOJI_DATA = REPO / "assets" / "unicode" / "16.0.0" / "emoji" / "emoji-data.txt"

# Grid geometry: 8 directions shown 4-across x 2-down, each an 8-frame cycle.
DIRS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]
FRAME_INDEX = [0, 2, 4, 7, 9, 12, 14, 17]
FRAME_KEYS = [f"f{i}" for i in FRAME_INDEX]
COLS, ROWS = 5, 7          # glyph grid per sprite (the winning "52" 5x7 layout)
GRID_COLS, GRID_ROWS = 4, 2  # direction panels across / down in the montage

# ---- ANSI terminal control codes (see the v2 viewer for a full gloss) -------
ESC = "\x1b"
HOME, CLEAR = f"{ESC}[H", f"{ESC}[2J"
HIDE, SHOW = f"{ESC}[?25l", f"{ESC}[?25h"
RESET = f"{ESC}[0m"
GREEN = f"{ESC}[38;5;120m"
LABEL = f"{ESC}[38;5;150m"
BANNER = f"{ESC}[38;5;186m"
DIM = f"{ESC}[38;5;244m"


# -----------------------------------------------------------------------------
# Curated "stroke" vocabulary — the comparison arm to the full palette.
# -----------------------------------------------------------------------------
# Path B in the operator's compare-both decision: instead of letting all ~53k
# Unicode glyphs compete per cell (which reads as noise), restrict matching to a
# CONSISTENT connective vocabulary — the kind of glyphs that visually JOIN into a
# figure: brackets, slashes, pipes, underscores/dashes, and the Unicode box-
# drawing + block-element line families. Still Unicode, still zero emoji; just a
# coherent alphabet so neighbouring cells read as one continuous stroke.
STROKE_ASCII = set("()[]{}<>/\\|_-=+~^.:'`,;iIlLvVxXtTyYrznuUcC")


def is_stroke_codepoint(cp: int) -> bool:
    """True if `cp` belongs to the curated connective/line vocabulary."""
    if cp < 0x80:
        return chr(cp) in STROKE_ASCII
    return (
        0x2500 <= cp <= 0x257F      # Box Drawing  (─ │ ┌ ┐ └ ┘ ├ ┤ ┬ ┴ ┼ …)
        or 0x2580 <= cp <= 0x259F   # Block Elements (▀ ▄ █ ▌ ▐ ░ ▒ ▓ …)
        or 0x25E2 <= cp <= 0x25E5   # triangular halves (◢ ◣ ◤ ◥) for diagonals
        or 0x2571 <= cp <= 0x2573   # box-drawing diagonals (╱ ╲ ╳)
    )


# -----------------------------------------------------------------------------
# Emoji exclusion — parse the authoritative Unicode Emoji property table.
# -----------------------------------------------------------------------------
def load_emoji_codepoints(path: Path) -> set[int]:
    """Return the set of codepoints that carry ANY emoji property, EXCLUDING
    ASCII (< 0x80). We drop ASCII from the exclusion because `#`, `*`, and the
    digits `0`-`9` appear in emoji-data.txt only as keycap bases — they are
    perfectly good readable glyphs and must stay in the palette.

    File format lines look like:
        231A..231B ; Emoji # ...
        2600       ; Emoji # ...
    Comments (after '#') and blank lines are ignored."""
    emoji: set[int] = set()
    if not path.exists():
        # Fail loud rather than silently shipping emoji into the palette.
        raise SystemExit(f"emoji property table not found: {path}")
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        field = line.split(";", 1)[0].strip()  # the codepoint or range field
        if not field:
            continue
        if ".." in field:
            lo_s, hi_s = field.split("..", 1)
            lo, hi = int(lo_s, 16), int(hi_s, 16)
        else:
            lo = hi = int(field, 16)
        for cp in range(lo, hi + 1):
            if cp >= 0x80:  # keep ASCII keycap bases in the palette
                emoji.add(cp)
    return emoji


# -----------------------------------------------------------------------------
# `new` — compile a version: outlines -> glyph grids -> Desktop+tracked GIF.
# -----------------------------------------------------------------------------
def cmd_new(args: argparse.Namespace) -> int:
    # Heavy deps imported here so `list`/`play` stay stdlib-only.
    import numpy as np
    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw, ImageFont

    clipasso = REPO / ".scratch" / "CLIPasso"
    sys.path.insert(0, str(clipasso))
    import unifont_match as um  # the pinned Unifont atlas + shape matcher

    svgroot = (args.svgroot if args.svgroot.is_absolute()
               else clipasso / args.svgroot)

    # 1. Build the full pinned-font cmap, then subtract emoji codepoints. --------
    # unifont_match.build_atlas() is the old baseline and stops at U+FFFF. The
    # operator's vocabulary contract is all drawable Unicode covered by the
    # pinned font, so this owner also admits astral cmap entries. Non-rendering
    # categories and blank/solid tiles are not glyph candidates.
    emoji = load_emoji_codepoints(EMOJI_DATA)
    cmap = sorted(TTFont(um.UNIFONT).getBestCmap())
    atlas_font = ImageFont.truetype(um.UNIFONT, um.TH)
    palette_chars: list[str] = []
    palette_features: list = []
    dropped = 0
    non_rendering = 0
    blank_solid = 0
    for codepoint in cmap:
        if codepoint in emoji:
            dropped += 1
            continue
        char = chr(codepoint)
        if unicodedata.category(char) in um.SKIP_CAT:
            non_rendering += 1
            continue
        tile = Image.new("L", (um.TW, um.TH), 0)
        try:
            ImageDraw.Draw(tile).text((0, 0), char, font=atlas_font, fill=255)
        except (OSError, ValueError):
            non_rendering += 1
            continue
        mask = np.asarray(tile) > 100
        ink = int(mask.sum())
        if not (um.TW * um.TH * 0.05 <= ink <= um.TW * um.TH * 0.72):
            blank_solid += 1
            continue
        palette_chars.append(char)
        palette_features.append(um.feature(mask))
    palette_feats = np.asarray(palette_features, dtype=float)
    astral = sum(ord(char) > 0xFFFF for char in palette_chars)
    print(
        f"palette: {len(palette_chars)} glyphs "
        f"(full pinned-font cmap minus {dropped} emoji codepoints; {astral} astral)"
    )

    # Path B (compare-both): optionally restrict to the curated stroke vocabulary
    # so cells share a consistent connective alphabet and read as one figure.
    if args.palette == "strokes":
        keep = [i for i, char in enumerate(palette_chars) if is_stroke_codepoint(ord(char))]
        palette_chars = [palette_chars[i] for i in keep]
        palette_feats = palette_feats[np.asarray(keep, dtype=int)]
        astral = sum(ord(char) > 0xFFFF for char in palette_chars)
        print(f"  stroke-vocabulary filter: {len(palette_chars)} connective glyphs kept")

    # 2. COHERENT structural solve (NOT independent per-cell argmax). ------------
    # Independent matching (one argmax per cell) produced disconnected,
    # flickering, asymmetric glyph soup — the exact failure this project exists to
    # fix. Here every direction is solved JOINTLY so the figure is:
    #   * CONNECTED           — neighbouring cells share compatible boundary
    #                           strokes (spatial port-join term),
    #   * TEMPORALLY COHERENT — each cell present in all frames follows ONE glyph
    #                           trajectory across the 8 walk frames, wrapped as a
    #                           cycle f17 -> f0 (cyclic temporal dynamic program),
    #   * SYMMETRIC           — left/right cells prefer horizontal-mirror-partner
    #                           glyphs (mirror coordinate descent),
    # all over the SAME full-Unifont-minus-emoji palette. It reuses the committed
    # P4b structural primitives so the cost model is the audited one.
    import fl4512_p4b_structural_feasibility as p4b

    # With a 53k-glyph palette the per-cell fidelity term is very strong, so the
    # connectivity and temporal terms must be weighted UP or coherence loses to a
    # marginally-better-matching obscure glyph in every cell. These override the
    # P4b module defaults (1.35 / 1.10) and are exposed as flags for tuning.
    p4b.LAMBDA_SPATIAL = args.lambda_spatial
    p4b.LAMBDA_TEMPORAL = args.lambda_temporal

    K = args.top_k         # candidate glyphs kept per cell (the legal lattice)
    LAMBDA_MIRROR = args.lambda_mirror  # left/right mirror-partner agreement
    INK_MIN = 0.012        # a cell below this ink fraction counts as empty

    def cell_candidates(cell):
        """Top-K palette glyphs for one source cell, by feature dot product."""
        feat = p4b.mask_feature(cell)
        scores = palette_feats @ feat
        kidx = np.argpartition(-scores, K - 1)[:K]
        kidx = kidx[np.argsort(-scores[kidx])]              # best first
        return kidx.astype(int), (1.0 - scores[kidx]).astype(float), int(np.argmax(scores))

    # --- pass 1: sample every direction/frame; collect the candidate union -----
    raw: dict[str, list] = {}
    union: set[int] = set()
    for d in DIRS:
        seq = []
        for i in FRAME_INDEX:
            svg = svgroot / f"{d}_f{i}" / "run0" / "best_iter.svg"
            if not svg.exists():
                raise SystemExit(f"missing outline: {svg}")
            cells = um.grid_cells(um.sample_polys(str(svg)), COLS, ROWS)
            sm, cand, un, fw = {}, {}, {}, {}
            for r in range(ROWS):
                for c in range(COLS):
                    cell = cells[r][c]
                    if cell.sum() < cell.size * INK_MIN:     # skip near-empty cells
                        continue
                    pos = (r, c)
                    sm[pos] = p4b.resize_mask(cell)          # 16x16 source mask
                    ci, ui, wi = cell_candidates(cell)
                    cand[pos], un[pos], fw[pos] = ci, ui, wi
                    union.update(int(x) for x in ci)
            seq.append((cells, sm, cand, un, fw))
        raw[d] = seq

    # --- describe ONLY the union candidates (bounded — not all 53k glyphs) -----
    union_sorted = sorted(union)
    palmap = {p: l for l, p in enumerate(union_sorted)}      # palette idx -> local idx
    umasks = p4b.rasterize_chars([palette_chars[p] for p in union_sorted])
    descriptors = [p4b.describe_glyph(l, palette_chars[union_sorted[l]], umasks[l])
                   for l in range(len(union_sorted))]
    print(f"  described {len(descriptors)} candidate glyphs (union across all frames)")

    def grid_of(labels):
        """Render a per-frame label map (pos -> local glyph) into 7 text rows."""
        return ["".join(descriptors[labels[(r, c)]].char if (r, c) in labels else " "
                        for c in range(COLS)) for r in range(ROWS)]

    # --- pass 2: per direction, joint spatial + temporal + mirror solve --------
    frames_by_dir: dict[str, list[list[str]]] = {}
    for d in DIRS:
        fds = []
        for (cells, sm, cand, un, fw) in raw[d]:
            # remap palette indices to local descriptor indices for this solve
            local_cand = {pos: np.array([palmap[int(p)] for p in cand[pos]], dtype=int)
                          for pos in cand}
            fds.append(p4b.FrameData(d, svgroot, cells, sm, local_cand, un, dict(fw)))
        # (a) within-frame connectivity: join glyph strokes across cell borders
        result = [p4b.spatial_refine(fd, descriptors, p4b.independent_labels(fd)) for fd in fds]
        # (b) temporal cyclic trajectory for every cell present in ALL 8 frames
        persistent = sorted(set.intersection(*[set(fd.candidates) for fd in fds])) if fds else []
        for pos in persistent:
            path, _expected, _energy = p4b.cyclic_track_dp(
                fds, [pos] * len(fds), ["source_best"] * len(fds), result, descriptors)
            for fi, local in enumerate(path):
                result[fi][pos] = local
        # (c) left/right mirror symmetry: prefer horizontal-mirror-partner glyphs
        for _sweep in range(3):
            for fi, fd in enumerate(fds):
                for (r, c) in sorted(result[fi]):
                    mc = COLS - 1 - c
                    if mc <= c or (r, mc) not in result[fi]:  # only left half w/ a partner
                        continue
                    partner = descriptors[result[fi][(r, mc)]].mask
                    best, best_cost = result[fi][(r, c)], math.inf
                    for local in fd.candidates[(r, c)].tolist():
                        cost = p4b.local_unary_with_spatial(fd, (r, c), local, result[fi], descriptors)
                        cost += LAMBDA_MIRROR * p4b.mask_distance(
                            descriptors[local].mask, partner, "mirror_h")
                        if (cost, descriptors[local].glyph_id) < (best_cost, descriptors[best].glyph_id):
                            best, best_cost = local, cost
                    result[fi][(r, c)] = best
        frames_by_dir[d] = [grid_of(result[fi]) for fi in range(len(fds))]
        print(f"  {d}: solved 8 frames  (persistent cells: {len(persistent)})")

    # 3. Render the animated 2x4 grid GIF in the exact legacy format. ------------
    cell_px = 34
    source_w, source_h = COLS * cell_px + 8, ROWS * cell_px + 8
    sprite_w, sprite_h = 129, 177
    label_h = 16
    pad = 6
    canvas_w = pad + GRID_COLS * (sprite_w + pad)
    canvas_h = pad + GRID_ROWS * (sprite_h + label_h + pad)
    if (canvas_w, canvas_h) != (546, 404):
        raise RuntimeError(f"legacy GIF geometry drifted: {(canvas_w, canvas_h)}")
    glyph_font = ImageFont.truetype(um.UNIFONT, cell_px - 4)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)

    gif_frames = []
    for fi in range(len(FRAME_INDEX)):             # one canvas per walk frame
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for idx, d in enumerate(DIRS):
            gc, gr = idx % GRID_COLS, idx // GRID_COLS
            x = pad + gc * (sprite_w + pad)
            y = pad + gr * (sprite_h + label_h + pad)
            draw.text((x, y), f"{d} {FRAME_KEYS[fi]}", fill=(150, 210, 150), font=label_font)
            grid = frames_by_dir[d][fi]
            source = Image.new("RGB", (source_w, source_h), (12, 12, 12))
            source_draw = ImageDraw.Draw(source)
            for r, row in enumerate(grid):         # draw the 5x7 glyphs
                for c, ch in enumerate(row):
                    if ch != " ":
                        source_draw.text((4 + c * cell_px, 4 + r * cell_px),
                                         ch, font=glyph_font, fill=(120, 230, 120))
            tile = source.resize((sprite_w, sprite_h), Image.Resampling.NEAREST)
            canvas.paste(tile, (x, y + label_h))
        gif_frames.append(canvas)

    # 4. Name with version + timestamp, write Desktop + tracked copies. ----------
    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__{args.version}__walk_all_directions"
    TRACK_DIR.mkdir(parents=True, exist_ok=True)
    tracked_gif = TRACK_DIR / f"{name}.gif"
    tracked_frames = TRACK_DIR / f"{name}.frames.json"
    desktop_gif = DESKTOP / f"{name}.gif"

    gif_frames[0].save(tracked_gif, save_all=True, append_images=gif_frames[1:],
                       duration=130, loop=0, disposal=2)
    # copy the identical bytes to the Desktop
    desktop_gif.write_bytes(tracked_gif.read_bytes())

    # store the glyph grids so `play` can replay this exact version
    frames_doc = {
        "version": args.version,
        "timestamp": timestamp,
        "palette": "unifont-17.0.04-no-emoji",
        "palette_mode": args.palette,
        "method": "joint spatial-connectivity + cyclic-temporal + mirror (P4b primitives)",
        "weights": {
            "lambda_spatial": args.lambda_spatial,
            "lambda_temporal": args.lambda_temporal,
            "lambda_mirror": args.lambda_mirror,
            "top_k": args.top_k,
        },
        "palette_size": len(palette_chars),
        "emoji_dropped": dropped,
        "astral_glyphs": astral,
        "atlas_admission": {
            "font_cmap": len(cmap),
            "non_rendering_removed": non_rendering,
            "blank_or_solid_removed": blank_solid,
        },
        "format": {
            "width": canvas_w,
            "height": canvas_h,
            "frames": len(FRAME_INDEX),
            "frame_ms": 130,
            "layout": [GRID_ROWS, GRID_COLS],
        },
        "svgroot": str(svgroot.relative_to(REPO)) if svgroot.is_relative_to(REPO) else str(svgroot),
        "dirs": DIRS,
        "frame_keys": FRAME_KEYS,
        "grid": [COLS, ROWS],
        "note": args.note,
        "frames": frames_by_dir,
    }
    tracked_frames.write_text(json.dumps(frames_doc, ensure_ascii=False, indent=1) + "\n")

    # 5. Append to the tracked registry. ----------------------------------------
    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else []
    registry.append({
        "name": name,
        "version": args.version,
        "timestamp": timestamp,
        "note": args.note,
        "palette_mode": args.palette,
        "palette_size": len(palette_chars),
        "emoji_dropped": dropped,
        "astral_glyphs": astral,
        "format": frames_doc["format"],
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "tracked_frames": str(tracked_frames.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "gif_sha256": hashlib.sha256(tracked_gif.read_bytes()).hexdigest(),
    })
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")

    print(f"\nwrote version '{args.version}' @ {timestamp}")
    print(f"  desktop : {desktop_gif}")
    print(f"  tracked : {tracked_gif.relative_to(REPO)}")
    print(f"  frames  : {tracked_frames.relative_to(REPO)}")
    print(f"  registry: {REGISTRY.relative_to(REPO)} ({len(registry)} versions)")
    print(f"\nplay it:  python3 scripts/fl4547_walk_viewer.py play --version {name}")
    return 0


# -----------------------------------------------------------------------------
# registry helpers (stdlib only)
# -----------------------------------------------------------------------------
def read_registry() -> list[dict]:
    if not REGISTRY.exists():
        raise SystemExit(f"no versions yet — run `new` first (registry: {REGISTRY})")
    return json.loads(REGISTRY.read_text())


def read_skeleton_registry() -> list[dict]:
    """Return the complete walking research registry, including list-only rows."""
    if not SKELETON_REGISTRY.exists():
        return []
    return json.loads(SKELETON_REGISTRY.read_text())


def manifest_provenance(doc: dict) -> dict[str, str]:
    """Project only provenance explicitly recorded by one playback manifest."""
    supply = doc.get("candidate_supply")
    if not isinstance(supply, str) or not supply.strip():
        palette_mode = doc.get("palette_mode")
        if isinstance(palette_mode, str) and palette_mode.strip():
            supply = f"palette_mode={palette_mode}"
        else:
            catalog = doc.get("catalog")
            if isinstance(catalog, dict):
                parts = []
                if catalog.get("path"):
                    parts.append(str(catalog["path"]))
                if catalog.get("eligible_non_emoji_count") is not None:
                    parts.append(
                        f"{int(catalog['eligible_non_emoji_count'])} eligible"
                    )
                if catalog.get("emoji_excluded") is True:
                    parts.append("emoji excluded")
                supply = "; ".join(parts) if parts else None
            if not supply and doc.get("palette"):
                parts = [str(doc["palette"])]
                if doc.get("palette_size") is not None:
                    parts.append(f"{doc['palette_size']} glyphs")
                if doc.get("emoji_dropped") is not None:
                    parts.append(f"{doc['emoji_dropped']} emoji codepoints dropped")
                supply = "; ".join(parts)

    details = [str(supply).strip()] if supply else ["not recorded"]
    policy = doc.get("candidate_policy")
    if isinstance(policy, str) and policy.strip():
        details.append(f"policy={policy.strip()}")
    codebook = doc.get("codebook") or doc.get("authored_codebook")
    if isinstance(codebook, str) and codebook.strip():
        details.append(f"codebook={codebook.strip()}")
    return {
        "schema": str(doc.get("schema") or "not recorded"),
        "status": str(doc.get("status") or "not recorded"),
        "glyph_supply": "; ".join(details),
    }


def registry_inventory() -> dict[str, int]:
    """Count listed, playable, and precisely classified list-only records."""
    glyph = read_registry()
    skeleton = read_skeleton_registry()
    glyph_playable = sum(
        1 for entry in glyph
        if (REPO / str(entry.get("tracked_frames", ""))).is_file()
    )
    no_manifest = sum(1 for entry in skeleton if not entry.get("tracked_manifest"))
    missing_manifest = sum(
        1 for entry in skeleton
        if entry.get("tracked_manifest")
        and not (REPO / str(entry["tracked_manifest"])).is_file()
    )
    skeleton_playable = len(skeleton) - no_manifest - missing_manifest
    glyph_missing = len(glyph) - glyph_playable
    return {
        "listed": len(glyph) + len(skeleton),
        "playable": glyph_playable + skeleton_playable,
        "legacy_glyph_playable": glyph_playable,
        "skeleton_manifest_playable": skeleton_playable,
        "list_only": glyph_missing + no_manifest + missing_manifest,
        "legacy_glyph_missing": glyph_missing,
        "no_manifest_declared": no_manifest,
        "declared_manifest_missing": missing_manifest,
    }


def read_playable_registry() -> list[dict]:
    """Return legacy glyph versions plus every available manifest-backed walk."""
    registry = []
    for source in read_registry():
        manifest = source.get("tracked_frames")
        if not manifest or not (REPO / manifest).is_file():
            continue
        entry = dict(source)
        doc = json.loads((REPO / manifest).read_text())
        entry["playback_provenance"] = manifest_provenance(doc)
        registry.append(entry)
    for source in read_skeleton_registry():
        manifest = source.get("tracked_manifest")
        if not manifest or not (REPO / manifest).is_file():
            continue
        entry = dict(source)
        doc = json.loads((REPO / manifest).read_text())
        entry.update({
            "version": source["name"],
            "tracked_frames": manifest,
            "playback_format": "versioned-walk-manifest",
            "playback_provenance": manifest_provenance(doc),
        })
        registry.append(entry)
    registry.sort(key=lambda entry: (entry["timestamp"], entry["name"]))
    return registry


def resolve_version(registry: list[dict], wanted: str | None) -> dict:
    """Pick a registry entry by exact name/version, or the latest if unset."""
    if wanted is None:
        return registry[-1]  # newest recorded
    for entry in reversed(registry):
        if wanted in (entry["name"], entry["version"]):
            return entry
    raise SystemExit(f"version not found: {wanted}")


def cmd_list(args: argparse.Namespace) -> int:
    rows = []
    if args.stage in ("all", "glyph"):
        for entry in read_registry():
            rows.append({
                "stage": "glyph",
                "name": entry["name"],
                "timestamp": entry["timestamp"],
                "status": entry.get("proof_status", "historical_experiment"),
                "note": entry.get("note", ""),
            })
    if args.stage in ("all", "rig"):
        for entry in read_skeleton_registry():
            rows.append({
                "stage": entry.get("kind", "rig"),
                "name": entry["name"],
                "timestamp": entry["timestamp"],
                "status": entry.get("status", "historical_checkpoint"),
                "note": entry.get("note", ""),
            })
    rows.sort(key=lambda row: (row["timestamp"], row["name"]))
    if not rows:
        raise SystemExit(f"no versions recorded for stage: {args.stage}")
    print(f"{'#':>2}  {'stage':<24} {'timestamp':<17} {'status':<44} name")
    for i, row in enumerate(rows):
        print(f"{i:>2}  {row['stage']:<24} {row['timestamp']:<17} "
              f"{row['status']:<44} {row['name']}")
    print(f"\nlatest: {rows[-1]['name']}")
    if args.stage == "all":
        inventory = registry_inventory()
        print(
            "inventory: "
            f"{inventory['listed']} listed / "
            f"{inventory['playable']} playable "
            f"({inventory['legacy_glyph_playable']} legacy glyph + "
            f"{inventory['skeleton_manifest_playable']} skeleton manifests) / "
            f"{inventory['list_only']} list-only "
            f"({inventory['no_manifest_declared']} without tracked_manifest + "
            f"{inventory['declared_manifest_missing']} declared manifest missing)"
        )
    return 0


# -----------------------------------------------------------------------------
# `play` — animate the 2x4 walk grid in the terminal, with a version caption.
# -----------------------------------------------------------------------------
def colorize(row: str) -> str:
    """Draw a glyph row: non-space characters in green, spaces left blank."""
    return "".join(" " if ch == " " else f"{GREEN}{ch}{RESET}" for ch in row.ljust(COLS)[:COLS])


def render_banner(entry: dict, extra: str) -> list[str]:
    bar = "=" * 72
    provenance = entry["playback_provenance"]
    return [
        f"{BANNER}{bar}{RESET}",
        f"{BANNER} FL-4547/FL-4561 walk viewer  |  version: {entry['version']}  ({entry['timestamp']}){RESET}",
        f"{DIM} glyph supply: {provenance['glyph_supply']}{RESET}",
        f"{DIM} manifest: {provenance['schema']}  |  status: {provenance['status']}{RESET}",
        f"{DIM} tracked: {entry.get('tracked_gif', entry['tracked_frames'])}{RESET}",
        f"{DIM} desktop: {entry.get('desktop_gif', 'not recorded')}{RESET}",
        f"{BANNER} {extra}{RESET}",
        f"{BANNER}{bar}{RESET}",
    ]


def compose_grid(
    frames_by_dir: dict,
    fi: int,
    frame_keys: list[str] = FRAME_KEYS,
    cols: int = COLS,
    rows: int = ROWS,
) -> list[str]:
    """Return the text lines for one animation frame: all 8 direction panels in
    a 4-across x 2-down grid, each captioned '<DIR> f<N>'."""
    gap = "   "
    out: list[str] = []
    for gr in range(GRID_ROWS):
        row_dirs = DIRS[gr * GRID_COLS:(gr + 1) * GRID_COLS]
        # caption line
        out.append(gap.join(
            f"{LABEL}{(d + ' ' + frame_keys[fi]).ljust(cols)}{RESET}" for d in row_dirs
        ))
        for r in range(rows):
            out.append(gap.join(
                "".join(
                    " " if ch == " " else f"{GREEN}{ch}{RESET}"
                    for ch in frames_by_dir[d][fi][r].ljust(cols)[:cols]
                )
                for d in row_dirs
            ))
        out.append("")
    return out


def manifest_playback(doc: dict) -> tuple[dict[str, list[list[str]]], int, list[str], int, int]:
    """Normalize the versioned manifest schemas into the terminal grid contract."""
    raw_frames = doc.get("frames")
    if raw_frames is None:
        directions = doc.get("directions") or {}
        raw_frames = {
            direction: (directions.get(direction) or {}).get("frames")
            for direction in DIRS
        }
    if any(not isinstance(raw_frames.get(direction), list) for direction in DIRS):
        raise SystemExit(f"unsupported walk manifest frame layout: {doc.get('schema', 'unknown')}")

    frames_by_dir = {
        direction: [frame["rows"] for frame in raw_frames[direction]]
        for direction in DIRS
    }
    frame_counts = {len(frames) for frames in frames_by_dir.values()}
    if len(frame_counts) != 1 or not frame_counts:
        raise SystemExit("walk manifest directions have inconsistent frame counts")
    n = frame_counts.pop()
    declared = int(doc.get("frames_per_direction", n))
    if declared != n:
        raise SystemExit(
            f"walk manifest frame count mismatch: declared={declared} actual={n}"
        )
    grid = doc.get("grid") or doc.get("presentation_grid")
    if not isinstance(grid, list) or len(grid) != 2:
        raise SystemExit(f"walk manifest lacks a playback grid: {doc.get('schema', 'unknown')}")
    cols, rows = (int(value) for value in grid)
    return frames_by_dir, n, [f"f{index}" for index in range(n)], cols, rows


def cmd_play(args: argparse.Namespace) -> int:
    registry = read_playable_registry()
    entry = resolve_version(registry, args.version)
    frames_path = REPO / entry["tracked_frames"]
    doc = json.loads(frames_path.read_text())
    if entry.get("playback_format") == "versioned-walk-manifest":
        frames_by_dir, n, frame_keys, cols, rows = manifest_playback(doc)
    else:
        frames_by_dir = doc["frames"]
        n = len(FRAME_INDEX)
        frame_keys = FRAME_KEYS
        cols, rows = COLS, ROWS

    if args.montage:
        # one static frame (frame 0), for screenshots / logging
        banner = render_banner(entry, "montage (frame 0)")
        sys.stdout.write(CLEAR + HOME + "\n".join(
            banner + [""] + compose_grid(frames_by_dir, 0, frame_keys, cols, rows)
        ) + "\n")
        return 0

    sys.stdout.write(HIDE + CLEAR)
    try:
        i = 0
        while True:
            fi = i % n
            banner = render_banner(
                entry,
                f"animating  frame {fi + 1}/{n}: {frame_keys[fi]}   (Ctrl-C to exit)",
            )
            sys.stdout.write(HOME + "\n".join(
                banner + [""] + compose_grid(frames_by_dir, fi, frame_keys, cols, rows)
            ) + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
            i += 1
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW + RESET + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="compile a timestamped version -> Desktop + tracked GIF")
    p_new.add_argument("--version", default="unifont-no-emoji",
                       help="version label (prefixes the gif name, before the timestamp)")
    p_new.add_argument("--svgroot", type=Path, default=Path("output_sketches/walk_p400"),
                       help="CLIPasso outline root (relative to .scratch/CLIPasso or absolute)")
    p_new.add_argument("--note", default="", help="free-text note stored in the registry")
    p_new.add_argument("--palette", choices=["full", "strokes"], default="full",
                       help="full = all Unicode minus emoji; strokes = curated connective vocabulary")
    p_new.add_argument("--lambda-spatial", type=float, default=3.2,
                       help="connectivity weight (raise to force glyphs to join; P4b default 1.35)")
    p_new.add_argument("--lambda-temporal", type=float, default=2.6,
                       help="temporal-coherence weight (raise to stabilize a cell across frames; P4b default 1.10)")
    p_new.add_argument("--lambda-mirror", type=float, default=1.6,
                       help="left/right mirror-symmetry weight")
    p_new.add_argument("--top-k", type=int, default=12,
                       help="candidate glyphs kept per cell before the joint solve")
    p_new.set_defaults(func=cmd_new)

    p_list = sub.add_parser("list", help="list recorded versions")
    p_list.add_argument("--stage", choices=["all", "glyph", "rig"], default="all",
                        help="filter the combined version inventory")
    p_list.set_defaults(func=cmd_list)

    p_play = sub.add_parser("play", help="animate a version's walk grid in the terminal")
    p_play.add_argument("--version", default=None, help="version name/label (default: latest)")
    p_play.add_argument("--montage", action="store_true", help="print one static frame instead of animating")
    p_play.add_argument("--interval", type=float, default=0.13, help="seconds per frame (default 0.13)")
    p_play.set_defaults(func=cmd_play)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
