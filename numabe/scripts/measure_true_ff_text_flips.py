#!/usr/bin/env python3
"""True FF temporal flip instrument on native text rows (FL-4896, FL-4894, FL-4872).

WHAT IT MEASURES
True FF draws Saitamaar mixed-width text: ordered characters on 17 px rows,
each with a pixel pen x and a native advance width. A fixed pixel-cell grid
(scripts/measure_f10_churn.py, the retired Desktop annotate_full.py) cannot
tell a legitimate glyph move from a flip on that layout, so True FF temporal
claims are measured here, on the text itself.

INPUT (one continuous run, one entry per rendered frame)
A JSON Lines file, one `true_ff.text_frame.v1` object per rendered frame in
presentation order. The True FF text export (FL-4512 G6) writes it from the
completed text bank. Fields:
  schema        "true_ff.text_frame.v1"
  frame         renderer frame index (present_view frame_epoch); consecutive
                when every rendered frame was exported, so a gap is visible
  engine_frame  process frame; pairs the line with an F9 PNG (--f9-bundle)
  row_pitch_px  row pitch in page pixels (17 for Saitamaar 16 px)
  page_shift_px [x, y] whole-page raster shift of this frame (the raster's
                page translation); screen position = placement + shift
  png           optional path (relative to the JSONL file) of the same
                frame's presented image, for annotation
  raster        optional {"origin_px": [x, y], "scale": s, "size_px": [w, h]}:
                page pixel to image pixel mapping (default origin 0, scale 1).
                The True FF export writes origin 0, scale 1 and the
                output framebuffer size; with --f9-bundle the scale becomes
                the F9 PNG width over size_px[0] (aspect checked).
  chars         list of [cp, x, y, w, flags, fg, bg]: codepoint, pen x and
                row top y in page pixels, native advance, symbol flags,
                packed foreground and background paint (R | G<<8 | B<<16 |
                A<<24). Since FL-4512 G6 the exporter writes the paint
                the raster draws: a non-HUD character's bg is the page
                paper texel at its advance centre on its row, not the
                composer's surface paint. Renderer identity (track, birth,
                material) may be appended but is never used to classify:
                the instrument does not let the renderer under test explain
                away its own flips.
  page          optional page-field transport (FL-4512 G1/G6), additive to
                v1: {"extent_px": [w, h], "full_advance_px": a,
                "glyph_only": bool, "rows": [[x0, [bg, ...]], ...]}. rows[r]
                gives the paper past row r's last character: x0 is that
                row's end (0 for an empty row) and each bg paints one cell
                of the implicit U+3000 lattice [x0 + k*a, x0 + (k+1)*a),
                clipped at w. Characters plus these cells reproduce every
                page pixel's paper. Frames without it (older exports) still
                parse; the same-state test then compares characters only.

CLASSIFICATION (consecutive frames A -> B, ink characters only)
Spacing characters (U+0020, U+3000, SPACE / STRUCTURAL_SPACE / PAGE_PAPER
flags) are not ink. Positions are screen positions (page shift applied).
  carried   a B character with the same codepoint in A within `reach_px`
            horizontally and `reach_rows` rows (one-to-one, nearest first).
            Displacement, whole-page scroll and row changes of the same
            glyph are motion, not flips.
  RED       change without a displacement explanation:
            substitute  unmatched B character where A had ink overlapping
                        its span widened by reach_px, on its row or a
                        neighbouring row (glyph swapped in place);
            vanish      unmatched A character that no unmatched B character
                        overlaps (ink removed without motion).
  YELLOW    appear: unmatched B character with no A ink within that
            neighbourhood (ink from nothing).
  repaint   carried character whose fg or bg changed (counted, not boxed).
  returned  a RED event whose old codepoint is back at the same place
            (same row, within reach_px) at t+2 or t+3: the flip signature
            of shimmer (same look-ahead as measure_f10_churn.py).
  repeat_sites  screen sites (row, x // full advance) with two or more RED
            events inside --repeat-window frames: flicker in place, as
            opposed to one threshold crossing per contour.

SAME-STATE TEST (history dependence, `compare` subcommand)
Two frames rendered from the identical scene, pose and camera reached by
different paths must be identical. Every character present in one and not
identical (cp, position, advance, fg, bg) in the other is RED.

OUTPUTS
stdout: one JSON summary. --out DIR (required): <stem>_textflips.json
(summary), <stem>_textflips_events.jsonl, and when frames name PNGs
<stem>_textflips.gif plus <stem>_textflips.html (legend: RED and YELLOW
meaning, counts, source). Before it reports success the tool preserves the
summary, the events and the input frame file with
scripts/run_receipt_preservation.preserve_file_set under
artifacts/run_receipts/<fl>/true-ff-text-flips__<hash16>/ (content-addressed,
never overwritten with different bytes); the GIF and HTML are recorded there
by sha256. The durable manifest path is printed and is in the summary.
--stem is required and must carry bundle, view, grid, projection and
selection identity (FL-4894).

PUBLICATION (docs/agent/godot-proof-publication.md)
When the tool draws media, it publishes them through
scripts/godot_proof_publication.py before it reports success: each GIF and
HTML gets its canonical publisher filename (deterministic_artifact_name), the
FL-4894 stem name stays as the manifest alias of the same bytes (a hardlink),
and <stem>_textflips.publication.json (godot-proof-publication.v1) is written,
validated, Finder-tagged and tag-verified. The capture identity comes from
--publication-metadata, a JSON object recorded from the capture evidence of
the run that produced the export (fl_id, run_id, proof_kind, architecture,
foreground/background mode, capture state, source commit, effective tree
fingerprint, provenance_status, and for ATTESTED the executed sources, build,
runtime and workload identity). The text export does not carry that identity,
so the tool never fills it in. Media drawn without --publication-metadata,
or a publication that fails validation, exit 4 after the receipt is preserved;
the preserved receipt includes the publication manifest when one exists.

INVALID FRAMES (FL-4907 WAVE_C_CAPTURE)
The exporter writes an error record for a frame it could not lay out
({"error": ..., "text_layout_valid": false, "text_layout_reason": ...}, no
row_pitch_px and no chars). Such a record, and any record whose
text_layout_valid is false, is an invalid frame: it is counted, its reasons
and first examples go into the summary (invalid_frames, invalid_reasons,
invalid_examples, valid=false), it takes part in the frame-exact sequence
check, and no pair is measured across it. A run with any invalid frame is
never a pass: the summary and receipt are still preserved, then the tool
exits 5. Before this, run .run/fl4359_headed_movement_20261002-204436 (7 of 7
error records) raised KeyError row_pitch_px and the finalizer saw crawl_rc_2.

EVIDENCE BOUNDARY
Unit tests of this file use synthetic frames and prove only the classifier.
A True FF temporal claim needs a frame-exact export from one continuous
headed run on the designated route (docs/proofs/README.md).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "true_ff.text_frame.v1"
SPACE, STRUCTURAL_SPACE, PAGE_PAPER_SPACE = 2, 8, 32
SPACING_CODEPOINTS = {0x20, 0x3000}
FLIP_LOOKAHEAD = (2, 3)
FULL_ADVANCE_DEFAULT = 11
RED = (255, 48, 48)
YELLOW = (255, 230, 0)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_receipt_preservation import preserve_file_set  # noqa: E402
import godot_proof_publication as publication  # noqa: E402

MEDIA_ROLES = {".gif": "true-ff-text-flips", ".html": "true-ff-text-flips-legend"}


@dataclass(frozen=True)
class Glyph:
    cp: int
    x: int
    y: int  # screen row top in page pixels (page shift applied)
    w: int
    fg: int
    bg: int
    # FL-4512: the exported site identity word (identity.x = tff_detail_site)
    # and whether the character belongs to a HUD row; 0/False when absent.
    site: int = 0
    hud: bool = False

    def overlaps(self, other: "Glyph", widen: int) -> bool:
        return self.x - widen < other.x + other.w and other.x < self.x + self.w + widen


@dataclass
class Frame:
    seq: int
    pitch: int
    glyphs: list[Glyph]
    png: Path | None
    origin: tuple[float, float]
    scale: float
    engine_frame: int | None = None
    size: tuple[int, int] | None = None
    page: tuple[tuple[int, tuple[int, ...]], ...] | None = None
    # FL-4512 composer loss facts carried by the exporter, when present.
    losses: dict | None = None

    def __post_init__(self):
        self.rows = by_row(self.glyphs)


def is_ink(cp: int, flags: int) -> bool:
    if cp in SPACING_CODEPOINTS:
        return False
    return not flags & (SPACE | STRUCTURAL_SPACE | PAGE_PAPER_SPACE)


def parse_page(obj: dict, pitch: int) -> tuple[tuple[int, tuple[int, ...]], ...] | None:
    """Validate the optional page-field transport against the frame's rows.

    Each row's x0 must equal the end of its last character (0 when empty) and
    its cell list must cover [x0, extent width) on the full-advance lattice.
    """
    page = obj.get("page")
    if page is None:
        return None
    width, height = (int(v) for v in page["extent_px"])
    advance = int(page["full_advance_px"])
    if width <= 0 or height <= 0 or advance <= 0:
        raise ValueError(f"frame {obj['frame']}: page extent and full advance must be positive")
    rows = page["rows"]
    if len(rows) * pitch < height:
        raise ValueError(f"frame {obj['frame']}: page rows do not cover its height")
    ends: dict[int, int] = {}
    for entry in obj["chars"]:
        x, y, w = (int(v) for v in entry[1:4])
        ends[y // pitch] = max(ends.get(y // pitch, 0), x + w)
    parsed = []
    for r, (x0, cells) in enumerate(rows):
        x0 = int(x0)
        if x0 != ends.get(r, 0):
            raise ValueError(f"frame {obj['frame']}: page row {r} starts at {x0}, row ends at {ends.get(r, 0)}")
        need = max(0, -(-(width - x0) // advance))
        if len(cells) != need:
            raise ValueError(f"frame {obj['frame']}: page row {r} has {len(cells)} cells, needs {need}")
        parsed.append((x0, tuple(int(c) for c in cells)))
    return tuple(parsed)


def parse_frame(obj: dict, base: Path, region: tuple[int, int, int, int] | None) -> Frame:
    if obj.get("schema") != SCHEMA:
        raise ValueError(f"frame schema {obj.get('schema')!r} is not {SCHEMA}")
    pitch = int(obj["row_pitch_px"])
    if pitch <= 0:
        raise ValueError("row_pitch_px must be positive")
    page = parse_page(obj, pitch)
    sx, sy = (int(v) for v in obj.get("page_shift_px", (0, 0)))
    glyphs = []
    for entry in obj["chars"]:
        cp, x, y, w, flags, fg, bg = (int(v) for v in entry[:7])
        if not is_ink(cp, flags):
            continue
        if y % pitch:
            raise ValueError(f"frame {obj['frame']}: row top {y} is not a multiple of {pitch}")
        gx, gy = x + sx, y + sy
        if region and not (region[0] <= gx and gx + w <= region[2] and region[1] <= gy and gy + pitch <= region[3]):
            continue
        glyphs.append(Glyph(cp, gx, gy, w, fg, bg, int(entry[7]) if len(entry) > 7 else 0,
                            bool(flags & HUD_TEXT_FLAG)))
    raster = obj.get("raster") or {}
    ox, oy = raster.get("origin_px", (0, 0))
    png = base / obj["png"] if obj.get("png") else None
    size = raster.get("size_px")
    return Frame(int(obj["frame"]), pitch, glyphs, png, (float(ox), float(oy)), float(raster.get("scale", 1.0)),
                 int(obj["engine_frame"]) if "engine_frame" in obj else None,
                 (int(size[0]), int(size[1])) if size else None, page, parse_losses(obj))


LOSS_REASONS = ("no_representation", "no_fitted_origin", "solve")
HUD_TEXT_FLAG = 64  # true_ff_text_types.glslinc HUD_TEXT


def site_strings(frame: "Frame") -> dict[int, tuple[str, int, int]]:
    """World sites of one frame: site id -> (glyph string in x order, x, row top)."""
    members: dict[int, list[Glyph]] = {}
    for g in frame.glyphs:
        if g.site and not g.hud:
            members.setdefault(g.site, []).append(g)
    out = {}
    for site, glyphs in members.items():
        glyphs.sort(key=lambda g: g.x)
        out[site] = ("".join(chr(g.cp) for g in glyphs), glyphs[0].x, glyphs[0].y)
    return out


def summarize_identity(frames: list["Frame"], reach_px: int) -> dict | None:
    """FL-4512 shimmer attribution by site identity, per consecutive pair.

    kept: the site id persists with the same glyph string; changed: it
    persists with another string (representation flip); relabel: the id is
    gone but the same string stands on the same row within reach_px under a
    new id (identity instability); vanish: gone with no such stand-in;
    appeared: ids new in the later frame. Fractions are of frame A's ids."""
    if len(frames) < 2 or not any(g.site for f in frames for g in f.glyphs):
        return None
    totals = {"kept": 0, "changed": 0, "relabel": 0, "vanish": 0, "appeared": 0}
    pairs = 0
    for a, b in zip(frames, frames[1:]):
        if b.seq != a.seq + 1:
            continue  # an invalid frame between them: not a pair
        pairs += 1
        sa, sb = site_strings(a), site_strings(b)
        new = {}
        for site in sb.keys() - sa.keys():
            text, x, y = sb[site]
            new.setdefault((text, y), []).append(x)
            totals["appeared"] += 1
        for site in sa.keys() & sb.keys():
            totals["kept" if sa[site][0] == sb[site][0] else "changed"] += 1
        for site in sa.keys() - sb.keys():
            text, x, y = sa[site]
            near = any(abs(nx - x) <= reach_px for nx in new.get((text, y), ()))
            totals["relabel" if near else "vanish"] += 1
    base = totals["kept"] + totals["changed"] + totals["relabel"] + totals["vanish"]
    return {"totals": totals, "pairs": pairs,
            "fractions": {k: (v / base if base else 0.0) for k, v in totals.items() if k != "appeared"}}


def parse_losses(obj: dict) -> dict | None:
    """The exporter's per-frame loss facts, or None for an older frame."""
    if "unscheduled" not in obj:
        return None
    omissions = obj.get("omissions") or {}
    requests = obj.get("requests") or {}
    return {"unscheduled": int(obj["unscheduled"]),
            **{reason: int(omissions.get(reason, 0)) for reason in LOSS_REASONS},
            "reasons_reported": bool(omissions),
            "inexact_rows": int(obj.get("inexact_rows", 0)),
            "budget_flags": int(obj.get("budget_flags", 0)),
            "published": int(requests.get("published", 0)),
            "overflow": int(requests.get("overflow", 0))}


def summarize_losses(frames: list[Frame]) -> dict | None:
    """Totals and per-frame means of omitted supports by reason (FL-4512)."""
    reporting = [f.losses for f in frames if f.losses is not None]
    if not reporting:
        return None
    keys = ("unscheduled", *LOSS_REASONS, "inexact_rows", "published", "overflow")
    totals = {key: sum(loss[key] for loss in reporting) for key in keys}
    return {"frames_reporting": len(reporting),
            "frames_with_reasons": sum(loss["reasons_reported"] for loss in reporting),
            "totals": totals,
            "per_frame_mean": {key: totals[key] / len(reporting) for key in keys},
            # Budget flags 1 ink, 2 occupancy, 4 horizontal run: frames raising each.
            "budget_flag_frames": {name: sum(bool(loss["budget_flags"] & bit) for loss in reporting)
                                   for name, bit in (("ink", 1), ("occupancy", 2), ("run", 4))}}


def attach_f9_pngs(frames: list[Frame], bundle: Path) -> int:
    """Pair export lines with F9 frame PNGs by engine_frame (FL-4896).

    The export captures no image; the F9 recorder keys each PNG by
    engine_frame in its manifest.json. The PNG to page scale is the image
    width over the export raster width, and the aspect must agree. Returns
    how many frames got a PNG; unmatched frames keep none (no media).
    """
    from PIL import Image

    doc = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    by_engine = {int(r["engine_frame"]): r["png"] for r in doc.get("frames") or [] if "engine_frame" in r}
    matched = 0
    for f in frames:
        name = by_engine.get(f.engine_frame) if f.engine_frame is not None else None
        if not name or not f.size:
            continue
        png = bundle / name
        with Image.open(png) as im:
            w, h = im.size
        scale = w / f.size[0]
        if abs(h - f.size[1] * scale) > 1.0:
            raise ValueError(f"F9 image {png} is {w}x{h}, not the export raster {f.size} scaled")
        f.png, f.origin, f.scale = png, (0.0, 0.0), scale
        matched += 1
    return matched


INVALID_RC = 5
INVALID_EXAMPLES = 10


def invalid_record(obj: dict) -> dict | None:
    """The invalid-frame entry for an exporter error record, else None.

    Error records carry no row_pitch_px and no chars, so they can never be
    parsed as a frame; a record whose text_layout_valid is false is not a
    trusted layout either. Both are counted, never classified."""
    if "error" not in obj and obj.get("text_layout_valid") is not False:
        return None
    if obj.get("schema") != SCHEMA:
        raise ValueError(f"frame schema {obj.get('schema')!r} is not {SCHEMA}")
    if "frame" not in obj:
        raise ValueError("invalid frame record carries no frame index")
    return {"frame": int(obj["frame"]),
            "engine_frame": obj.get("engine_frame"),
            "error": str(obj.get("error") or "text_layout_invalid"),
            "text_layout_reason": obj.get("text_layout_reason"),
            "layout_overflow_bits": obj.get("layout_overflow_bits"),
            "source_overflow_bits": obj.get("source_overflow_bits")}


def load_run(path: Path, region=None) -> tuple[list[Frame], list[dict]]:
    """Parse one export: (valid frames, invalid-frame entries).

    The frame-exact check runs over every record, valid or invalid, so a gap
    in the export stays a refusal while an error record stays a counted frame."""
    frames: list[Frame] = []
    invalid: list[dict] = []
    seqs: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        bad = invalid_record(obj)
        if bad is not None:
            invalid.append(bad)
            seqs.append(bad["frame"])
            continue
        frame = parse_frame(obj, path.parent, region)
        frames.append(frame)
        seqs.append(frame.seq)
    if len(seqs) < 2:
        raise ValueError("need at least two frames")
    for a, b in zip(seqs, seqs[1:]):
        if b != a + 1:
            raise ValueError(f"frames are not frame-exact: {a} then {b}")
    for a, b in zip(frames, frames[1:]):
        if b.pitch != a.pitch:
            raise ValueError("row pitch changed inside one run")
    return frames, invalid


def load_frames(path: Path, region=None) -> list[Frame]:
    """Strict loader: every record must be a valid frame."""
    frames, invalid = load_run(path, region)
    if invalid:
        raise ValueError(f"{len(invalid)} invalid frame record(s), first frame {invalid[0]['frame']}: "
                         f"{invalid[0]['error']}")
    return frames


def summarize_invalid(frames: list[Frame], invalid: list[dict]) -> dict:
    """Invalid-frame accounting merged into the run summary. valid is true
    only when every record is a valid frame and at least one pair exists."""
    reasons: dict[str, int] = {}
    for entry in invalid:
        key = entry["error"]
        if entry.get("text_layout_reason"):
            key += ":" + str(entry["text_layout_reason"])
        reasons[key] = reasons.get(key, 0) + 1
    return {"records": len(frames) + len(invalid),
            "valid_frames": len(frames),
            "invalid_frames": len(invalid),
            "invalid_reasons": reasons,
            "invalid_examples": invalid[:INVALID_EXAMPLES],
            "valid": not invalid and len(frames) >= 2}


def match(a: list[Glyph], b: list[Glyph], reach_px: int, reach_y: int):
    """One-to-one nearest same-codepoint matching. Returns (pairs, un_a, un_b)."""
    by_cp: dict[int, list[int]] = {}
    for j, gb in enumerate(b):
        by_cp.setdefault(gb.cp, []).append(j)
    candidates = []
    for i, ga in enumerate(a):
        for j in by_cp.get(ga.cp, ()):
            gb = b[j]
            dr, dx = abs(ga.y - gb.y), abs(ga.x - gb.x)
            if dr <= reach_y and dx <= reach_px:
                candidates.append((dr, dx, i, j))
    candidates.sort()
    used_a, used_b, pairs = set(), set(), []
    for _, _, i, j in candidates:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((a[i], b[j]))
    un_a = [g for i, g in enumerate(a) if i not in used_a]
    un_b = [g for j, g in enumerate(b) if j not in used_b]
    return pairs, un_a, un_b


def classify_pair(a: list[Glyph], b: list[Glyph], reach_px: int, reach_y: int):
    pairs, un_a, un_b = match(a, b, reach_px, reach_y)
    rows_a = by_row(a)
    rows_un_b = by_row(un_b)
    events = []
    for g in un_b:
        near = any(o.overlaps(g, reach_px) for y, row in rows_a.items() if abs(y - g.y) <= reach_y for o in row)
        events.append(("red", "substitute", g) if near else ("yellow", "appear", g))
    for g in un_a:
        if not any(o.overlaps(g, 0) for o in rows_un_b.get(g.y, ())):
            events.append(("red", "vanish", g))
    repaint = sum(1 for ga, gb in pairs if (ga.fg, ga.bg) != (gb.fg, gb.bg))
    return events, len(pairs), repaint


def by_row(glyphs: list[Glyph]) -> dict[int, list[Glyph]]:
    rows: dict[int, list[Glyph]] = {}
    for g in glyphs:
        rows.setdefault(g.y, []).append(g)
    return rows


def returned(frames: list[Frame], t: int, g_old: Glyph, reach_px: int) -> bool:
    for k in FLIP_LOOKAHEAD:
        # Only the frame exactly k later: an invalid frame leaves a seq gap.
        if t + k < len(frames) and frames[t + k].seq == frames[t].seq + k:
            if any(o.cp == g_old.cp and abs(o.x - g_old.x) <= reach_px
                   for o in frames[t + k].rows.get(g_old.y, ())):
                return True
    return False


def measure(frames: list[Frame], reach_px: int, reach_rows: int, full_advance: int, repeat_window: int):
    events, totals = [], {"red": 0, "yellow": 0, "carried": 0, "repaint": 0, "returned": 0}
    kinds: dict[str, int] = {}
    pairs = 0
    for t in range(1, len(frames)):
        a, b = frames[t - 1], frames[t]
        if b.seq != a.seq + 1:
            # An invalid frame sits between them (load_run): no pair.
            continue
        pairs += 1
        reach_y = reach_rows * b.pitch
        pair_events, carried, repaint = classify_pair(a.glyphs, b.glyphs, reach_px, reach_y)
        totals["carried"] += carried
        totals["repaint"] += repaint
        for colour, kind, g in pair_events:
            # The flip signature: the replaced (substitute) or removed
            # (vanish) glyph is back in place at t+2 or t+3.
            back = False
            if kind == "vanish":
                back = returned(frames, t - 1, g, reach_px)
            elif kind == "substitute":
                back = any(o.cp != g.cp and returned(frames, t - 1, o, reach_px)
                           for o in a.rows.get(g.y, ()) if o.overlaps(g, 0))
            totals[colour] += 1
            totals["returned"] += int(back)
            kinds[kind] = kinds.get(kind, 0) + 1
            events.append({"frame": b.seq, "colour": colour, "kind": kind, "cp": g.cp, "x": g.x,
                           "y": g.y, "w": g.w, "returned": back})
    sites: dict[tuple[int, int], list[int]] = {}
    for e in events:
        if e["colour"] == "red":
            sites.setdefault((e["y"], e["x"] // max(1, full_advance)), []).append(e["frame"])
    repeat_sites = 0
    for hits in sites.values():
        hits.sort()
        if any(h2 - h1 < repeat_window for h1, h2 in zip(hits, hits[1:])):
            repeat_sites += 1
    ink = [len(f.glyphs) for f in frames]
    summary = {
        "schema": "true_ff.text_flip_summary.v1",
        "frames": len(frames),
        "first_frame": frames[0].seq if frames else None,
        "last_frame": frames[-1].seq if frames else None,
        "pairs": pairs, "ink_chars_mean": sum(ink) / len(ink) if ink else None,
        "reach_px": reach_px, "reach_rows": reach_rows, "full_advance_px": full_advance,
        "repeat_window": repeat_window, **totals, "kinds": kinds,
        "red_per_pair": totals["red"] / pairs if pairs else None,
        "repeat_sites": repeat_sites,
        "row_losses": summarize_losses(frames),
        "site_identity": summarize_identity(frames, full_advance),
    }
    return summary, events


def compare_frames(a: Frame, b: Frame):
    keys = ("cp", "x", "y", "w", "fg", "bg")
    sa = {(g.cp, g.x, g.y, g.w, g.fg, g.bg) for g in a.glyphs}
    sb = {(g.cp, g.x, g.y, g.w, g.fg, g.bg) for g in b.glyphs}
    only_a = [dict(zip(keys, k), side="a", colour="red", frame=a.seq) for k in sorted(sa - sb)]
    only_b = [dict(zip(keys, k), side="b", colour="red", frame=b.seq) for k in sorted(sb - sa)]
    # The page field is part of the picture: blank paper that differs between
    # two same-state frames is a difference too (FL-4512 G1/G6).
    page_compared = a.page is not None and b.page is not None
    page_red = []
    if page_compared:
        for r in range(max(len(a.page), len(b.page))):
            ra = a.page[r] if r < len(a.page) else None
            rb = b.page[r] if r < len(b.page) else None
            if ra != rb:
                page_red.append({"row": r, "side": "page", "colour": "red",
                                 "a": list(ra[1]) if ra else None, "b": list(rb[1]) if rb else None})
    return {"schema": "true_ff.text_same_state.v1", "frame_a": a.seq, "frame_b": b.seq,
            "identical": not only_a and not only_b and not page_red,
            "red": len(only_a) + len(only_b), "page_compared": page_compared,
            "page_rows_differ": len(page_red)}, only_a + only_b + page_red


def validate_stem(stem: str) -> str:
    parts = [p for p in stem.replace("-", "_").split("_") if p]
    if len(parts) < 5 or any(c in stem for c in "/\\ "):
        raise ValueError("--stem must carry bundle, view, grid, projection and selection "
                         "identity (FL-4894), for example "
                         "FL4359_strafe-f10_FF-1x2-225x71-persp_sel4cf5b301")
    return stem


def annotate(frames: list[Frame], events: list[dict], out: Path, stem: str, summary: dict, source: str) -> list[str]:
    from PIL import Image, ImageDraw

    if not frames or not all(f.png and f.png.exists() for f in frames):
        return []
    by_frame: dict[int, list[dict]] = {}
    for e in events:
        if "x" in e:  # page-row differences carry no character box
            by_frame.setdefault(e["frame"], []).append(e)
    images = []
    for f in frames:
        im = Image.open(f.png).convert("RGB")
        draw = ImageDraw.Draw(im)
        for e in by_frame.get(f.seq, []):
            x0 = f.origin[0] + e["x"] * f.scale
            y0 = f.origin[1] + e["y"] * f.scale
            box = [x0, y0, x0 + e["w"] * f.scale - 1, y0 + f.pitch * f.scale - 1]
            draw.rectangle(box, outline=RED if e["colour"] == "red" else YELLOW, width=2)
        band = Image.new("RGB", (im.width, 22), (0, 0, 0))
        # Frame number first: every GIF frame then differs, so Pillow cannot
        # merge two consecutive frames that carry the same boxes.
        ImageDraw.Draw(band).text((6, 5), f"frame {f.seq}  RED changed without motion  "
                                  f"YELLOW appeared from nothing  {stem}", fill=(255, 255, 255))
        canvas = Image.new("RGB", (im.width, im.height + 22))
        canvas.paste(band, (0, 0))
        canvas.paste(im, (0, 22))
        images.append(canvas)
    gif = out / f"{stem}_textflips.gif"
    images[0].save(gif, save_all=True, append_images=images[1:], duration=50, loop=0)
    page = out / f"{stem}_textflips.html"
    rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>"
                   for k, v in summary.items() if not isinstance(v, dict))
    page.write_text(
        "<!DOCTYPE html><html lang='en'><meta charset='utf-8'>"
        f"<title>{html.escape(stem)} text flips</title>"
        "<body style='background:#111;color:#eee;font-family:monospace'>"
        f"<h1>{html.escape(stem)}</h1>"
        "<p><b style='color:#ff3030'>RED</b>: a True FF character changed without a displacement "
        "explanation (substituted in place, or vanished).<br>"
        "<b style='color:#ffe600'>YELLOW</b>: a character appeared where the previous frame had no "
        "ink nearby.<br>No box: the same glyph moved, scrolled or stayed.</p>"
        f"<p>Source: {html.escape(source)}. Instrument: scripts/measure_true_ff_text_flips.py.</p>"
        f"<img src='{html.escape(gif.name)}' style='max-width:100%;image-rendering:pixelated'>"
        f"<table>{rows}</table></body></html>", encoding="utf-8")
    return [gif.name, page.name]


def publish_media(out: Path, stem: str, media: list[str], metadata_path: Path) -> dict:
    """Publish the drawn GIF and HTML through the shared publisher.

    The stem-named file (FL-4894) is the alias; the canonical publisher name is
    a hardlink to the same bytes. Raises publication.PublicationError."""
    try:
        metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise publication.PublicationError(f"cannot read --publication-metadata {metadata_path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise publication.PublicationError("--publication-metadata must hold a JSON object")
    metadata.setdefault("storage", {"status": "PENDING"})
    artifacts = []
    for name in media:
        alias = out / name
        record = {"role": MEDIA_ROLES[alias.suffix], "extension": alias.suffix}
        if alias.suffix == ".gif":
            width, height, count = publication.inspect_visual(alias)
            record.update(frame_count=count, image_dimensions={"width": width, "height": height})
        else:
            record["frame_count"] = 1
        canonical = out / publication.deterministic_artifact_name(metadata, record)
        if canonical.exists() and publication.sha256_file(canonical) != publication.sha256_file(alias):
            raise publication.PublicationError(f"refusing to replace different bytes at {canonical}")
        if not canonical.exists():
            canonical.hardlink_to(alias)
        artifacts.append({**record, "path": str(canonical), "aliases": [str(alias)]})
    manifest_path = out / f"{stem}_textflips.publication.json"
    publication.publish_artifacts(manifest_path, metadata, artifacts, artifact_root=out)
    errors = publication.validate_manifest(manifest_path)
    if errors:
        raise publication.PublicationError("publication manifest invalid: " + "; ".join(errors))
    publication.apply_finder_tags(manifest_path)
    errors = publication.verify_finder_tags(manifest_path)
    if errors:
        raise publication.PublicationError("Finder tag verification failed: " + "; ".join(errors))
    return {"manifest": manifest_path.name, "schema": publication.SCHEMA, "validated": True,
            "finder_tags": "applied and verified",
            "artifacts": [{"canonical": Path(a["path"]).name, "alias": Path(a["aliases"][0]).name}
                          for a in artifacts]}


def parse_region(text: str | None):
    if not text:
        return None
    vals = [int(v) for v in text.split(",")]
    if len(vals) != 4 or vals[0] >= vals[2] or vals[1] >= vals[3]:
        raise ValueError("--region is x0,y0,x1,y1 in screen page pixels")
    return tuple(vals)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="classify one continuous run")
    run.add_argument("frames", type=Path, help="true_ff.text_frame.v1 JSON Lines file")
    run.add_argument("--f9-bundle", type=Path,
                     help="F9 recording bundle whose manifest.json pairs PNGs to export lines by engine_frame")
    same = sub.add_parser("compare", help="same-state test of two frames")
    same.add_argument("frame_a", type=Path)
    same.add_argument("frame_b", type=Path)
    for p in (run, same):
        p.add_argument("--stem", required=True)
        p.add_argument("--fl", default="FL-4896", help="FL whose run_receipts folder keeps the receipt")
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--region")
        p.add_argument("--publication-metadata", type=Path, default=None,
                       help="capture identity JSON for the godot-proof-publication.v1 manifest of the media")
    run.add_argument("--reach-px", type=int, default=FULL_ADVANCE_DEFAULT)
    run.add_argument("--reach-rows", type=int, default=1)
    run.add_argument("--full-advance", type=int, default=FULL_ADVANCE_DEFAULT)
    run.add_argument("--repeat-window", type=int, default=8)
    args = ap.parse_args(argv)
    try:
        stem = validate_stem(args.stem)
        region = parse_region(args.region)
        invalid: list[dict] = []
        if args.cmd == "run":
            frames, invalid = load_run(args.frames, region)
            if args.f9_bundle and frames:
                attach_f9_pngs(frames, args.f9_bundle)
            summary, events = measure(frames, args.reach_px, args.reach_rows, args.full_advance, args.repeat_window)
            summary.update(summarize_invalid(frames, invalid))
            source = str(args.frames)
        else:
            fa = load_frames_single(args.frame_a, region)
            fb = load_frames_single(args.frame_b, region)
            summary, events = compare_frames(fa, fb)
            frames, source = [fa, fb], f"{args.frame_a} vs {args.frame_b}"
    except (ValueError, KeyError) as exc:
        print(f"[true-ff-text-flips] ERROR: {exc}", file=sys.stderr)
        return 2
    summary["stem"] = stem
    summary["source"] = source
    params = {k: v for k, v in vars(args).items() if isinstance(v, (int, str)) and k != "cmd"}
    summary["command"] = args.cmd
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / f"{stem}_textflips.json"
    events_path = out / f"{stem}_textflips_events.jsonl"
    events_path.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in events), encoding="utf-8")
    summary["media"] = annotate(frames, events, out, stem, summary, source)
    publication_error = None
    summary["publication"] = None
    if summary["media"]:
        if args.publication_metadata is None:
            publication_error = "media drawn without --publication-metadata; not published"
        else:
            try:
                summary["publication"] = publish_media(out, stem, summary["media"], args.publication_metadata)
            except (publication.PublicationError, OSError) as exc:
                publication_error = str(exc)
        if publication_error:
            summary["publication"] = {"status": "NOT_PUBLISHED", "reason": publication_error}
    summary_path.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    inputs = [args.frames] if args.cmd == "run" else [args.frame_a, args.frame_b]
    files = {summary_path.name: summary_path, events_path.name: events_path}
    if summary["publication"] and summary["publication"].get("manifest"):
        files[summary["publication"]["manifest"]] = out / summary["publication"]["manifest"]
    for i, path in enumerate(inputs):
        files[f"input_{i}_{path.name}"] = path
    referenced = [{"name": name, "sha256": hashlib.sha256((out / name).read_bytes()).hexdigest(),
                   "storage": str((out / name).resolve())} for name in summary["media"]]
    try:
        manifest = preserve_file_set(repo_root=ROOT, fl_id=args.fl, runner="measure_true_ff_text_flips.py",
                                     label="true-ff-text-flips", files=files, referenced=referenced,
                                     metadata={"stem": stem, "command": args.cmd, "params": params})
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[true-ff-text-flips] ERROR: receipt not preserved: {exc}", file=sys.stderr)
        return 3
    summary["durable_receipt"] = (str(manifest.relative_to(ROOT)) if manifest.is_relative_to(ROOT)
                                  else str(manifest))
    print(json.dumps(summary, sort_keys=True))
    print(f"[true-ff-text-flips] durable receipt: {summary['durable_receipt']}", file=sys.stderr)
    if invalid:
        # Counted, preserved and reported; never a pass (FL-4907 WAVE_C_CAPTURE).
        print(f"[true-ff-text-flips] INVALID: {len(invalid)} of {summary['records']} frames are "
              f"invalid records {summary['invalid_reasons']}; not a pass", file=sys.stderr)
        return INVALID_RC
    if publication_error:
        print(f"[true-ff-text-flips] ERROR: media not published: {publication_error}", file=sys.stderr)
        return 4
    return 0


def load_frames_single(path: Path, region) -> Frame:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError(f"{path} must hold exactly one frame")
    return parse_frame(json.loads(lines[0]), path.parent, region)


if __name__ == "__main__":
    sys.exit(main())
