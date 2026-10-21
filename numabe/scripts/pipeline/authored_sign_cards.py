"""Hand-authored shop sign cards (FL-4876 CARD-FIX-01).

WHY. compile_sign_sprites.cells_for LANCZOS-downsamples a photo crop to the Classic sprite density (~7.7 cols per
metre) and 6 colour levels, so every name vanished (build_reg_87 review: 0 of 15 shop logos readable). A screen cell
of the Classic view covers ~0.13 m x 0.15 m of wall, so a sign reads only through big, high-contrast letters in the
brand colours, never through photo detail. Each shop therefore gets a card drawn here from an authored record.

OWNER. assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_sign_cards.json (cards[]):
  card_id; placement_id (custom shop sign) or place_feature_id (OSM brand place front); label;
  size_m [w, h]: nominal board size in metres; px_m [pw, ph]: metres per card pixel (width, height); a cell of the
    half-block grid is one pixel wide and two pixels tall, so ph is half the cell height;
  board_rgb, text_rgb; optional border_rgb + border_px;
  stripes [{edge: top|bottom|left|right, px, rgb}] painted over the board edge, first entry outermost;
  lines [{text, fw, fh, rgb?}]: text rows top to bottom, centred in the area inside the stripes; fw/fh = card
    pixels per font pixel across/down (the font is 5x7, one pixel gap); "stack": true puts one character per row
    (vertical signs); "gap_px" blank pixel rows after the line;
  text_reading: the text as read from the source crop (kana/kanji as written); romanised: true when the drawn text
    is a romanisation because the pixel font has no kana or kanji; basis: what the reading rests on.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CARDS = (ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_sign_cards.json")
HALF_UPPER = 223
KEY = (255, 255, 85)

_F = {}


def _def(ch, *rows):
    assert len(rows) == 7 and all(len(r) == 5 for r in rows), ch
    _F[ch] = rows


_def(" ", "00000", "00000", "00000", "00000", "00000", "00000", "00000")
_def("A", "01110", "10001", "10001", "11111", "10001", "10001", "10001")
_def("B", "11110", "10001", "10001", "11110", "10001", "10001", "11110")
_def("C", "01110", "10001", "10000", "10000", "10000", "10001", "01110")
_def("D", "11110", "10001", "10001", "10001", "10001", "10001", "11110")
_def("E", "11111", "10000", "10000", "11110", "10000", "10000", "11111")
_def("F", "11111", "10000", "10000", "11110", "10000", "10000", "10000")
_def("G", "01110", "10001", "10000", "10111", "10001", "10001", "01111")
_def("H", "10001", "10001", "10001", "11111", "10001", "10001", "10001")
_def("I", "01110", "00100", "00100", "00100", "00100", "00100", "01110")
_def("J", "00111", "00010", "00010", "00010", "00010", "10010", "01100")
_def("K", "10001", "10010", "10100", "11000", "10100", "10010", "10001")
_def("L", "10000", "10000", "10000", "10000", "10000", "10000", "11111")
_def("M", "10001", "11011", "10101", "10101", "10001", "10001", "10001")
_def("N", "10001", "11001", "10101", "10011", "10001", "10001", "10001")
_def("O", "01110", "10001", "10001", "10001", "10001", "10001", "01110")
_def("P", "11110", "10001", "10001", "11110", "10000", "10000", "10000")
_def("Q", "01110", "10001", "10001", "10001", "10101", "10010", "01101")
_def("R", "11110", "10001", "10001", "11110", "10100", "10010", "10001")
_def("S", "01111", "10000", "10000", "01110", "00001", "00001", "11110")
_def("T", "11111", "00100", "00100", "00100", "00100", "00100", "00100")
_def("U", "10001", "10001", "10001", "10001", "10001", "10001", "01110")
_def("V", "10001", "10001", "10001", "10001", "10001", "01010", "00100")
_def("W", "10001", "10001", "10001", "10101", "10101", "11011", "10001")
_def("X", "10001", "10001", "01010", "00100", "01010", "10001", "10001")
_def("Y", "10001", "10001", "01010", "00100", "00100", "00100", "00100")
_def("Z", "11111", "00001", "00010", "00100", "01000", "10000", "11111")
_def("0", "01110", "10001", "10011", "10101", "11001", "10001", "01110")
_def("1", "00100", "01100", "00100", "00100", "00100", "00100", "01110")
_def("2", "01110", "10001", "00001", "00010", "00100", "01000", "11111")
_def("3", "11110", "00001", "00001", "01110", "00001", "00001", "11110")
_def("4", "00010", "00110", "01010", "10010", "11111", "00010", "00010")
_def("5", "11111", "10000", "11110", "00001", "00001", "10001", "01110")
_def("6", "00110", "01000", "10000", "11110", "10001", "10001", "01110")
_def("7", "11111", "00001", "00010", "00100", "01000", "01000", "01000")
_def("8", "01110", "10001", "10001", "01110", "10001", "10001", "01110")
_def("9", "01110", "10001", "10001", "01111", "00001", "00010", "01100")
_def("-", "00000", "00000", "00000", "11111", "00000", "00000", "00000")
_def(".", "00000", "00000", "00000", "00000", "00000", "01100", "01100")
_def("/", "00001", "00010", "00010", "00100", "01000", "01000", "10000")
_def("a", "00000", "00000", "01110", "00001", "01111", "10001", "01111")
_def("e", "00000", "00000", "01110", "10001", "11111", "10000", "01110")
_def("i", "00100", "00000", "01100", "00100", "00100", "00100", "01110")
_def("l", "01100", "00100", "00100", "00100", "00100", "00100", "01110")
_def("m", "00000", "00000", "11010", "10101", "10101", "10101", "10101")
_def("p", "00000", "00000", "11110", "10001", "11110", "10000", "10000")
_def("r", "00000", "00000", "10110", "11001", "10000", "10000", "10000")
_def("s", "00000", "00000", "01111", "10000", "01110", "00001", "11110")
_def("t", "00100", "00100", "11111", "00100", "00100", "00101", "00010")
_def("y", "00000", "00000", "10001", "10001", "01111", "00001", "01110")


def text_pixels(text: str, fw: int, fh: int):
    """Font bitmap of `text` as rows of 0/1, fw x fh card pixels per font pixel, one font-pixel gap between glyphs."""
    missing = sorted({c for c in text if c not in _F})
    if missing:
        raise ValueError("pixel font has no glyph for %r in %r" % ("".join(missing), text))
    rows = []
    for fr in range(7):
        row = []
        for k, ch in enumerate(text):
            if k:
                row += [0] * fw
            for b in _F[ch][fr]:
                row += [int(b)] * fw
        for _ in range(fh):
            rows.append(list(row))
    return rows


def _rgb(v):
    t = tuple(int(x) for x in v)
    return (255, 255, 51) if t == KEY else t


def card_grid(card: dict):
    """(pixels, W, H): the card as rows top first of RGB; H is even so it folds into half-block cells."""
    pw, ph = card["px_m"]
    W = max(2, round(card["size_m"][0] / pw))
    H = max(2, round(card["size_m"][1] / ph))
    H += H % 2
    px = [[_rgb(card["board_rgb"])] * W for _ in range(H)]
    x0, x1, y0, y1 = 0, W, 0, H
    if card.get("border_rgb"):
        b = int(card.get("border_px", 1))
        for y in range(H):
            for x in range(W):
                if x < b or y < b or x >= W - b or y >= H - b:
                    px[y][x] = _rgb(card["border_rgb"])
        x0, x1, y0, y1 = b, W - b, b, H - b
    for st in card.get("stripes", []):
        n, c = int(st["px"]), _rgb(st["rgb"])
        e = st["edge"]
        for y in range(H):
            for x in range(W):
                if ((e == "top" and y0 <= y < y0 + n) or (e == "bottom" and y1 - n <= y < y1)
                        or (e == "left" and x0 <= x < x0 + n) or (e == "right" and x1 - n <= x < x1)):
                    if x0 <= x < x1 and y0 <= y < y1:
                        px[y][x] = c
        if e == "top":
            y0 += n
        elif e == "bottom":
            y1 -= n
        elif e == "left":
            x0 += n
        else:
            x1 -= n
    blocks = []
    for ln in card.get("lines", []):
        fw, fh = int(ln.get("fw", 1)), int(ln.get("fh", 1))
        chars = list(ln["text"]) if ln.get("stack") else [ln["text"]]
        for ch in chars:
            blocks.append((text_pixels(ch, fw, fh), _rgb(ln.get("rgb") or card["text_rgb"]),
                           int(ln.get("gap_px", 0)), int(ln.get("dx_px", 0))))
    total = sum(len(b[0]) + b[2] for b in blocks) - (blocks[-1][2] if blocks else 0)
    ytop = y0 + max(0, (y1 - y0 - total) // 2)
    if total > y1 - y0:
        raise ValueError("card %s: text rows (%d px) exceed the board (%d px)" % (card.get("card_id"), total, y1 - y0))
    y = ytop
    for bits, rgb, gap, dx in blocks:
        bw = len(bits[0])
        if bw > x1 - x0:
            raise ValueError("card %s: a line is %d px wide, the board has %d" % (card.get("card_id"), bw, x1 - x0))
        xs = x0 + (x1 - x0 - bw) // 2 + dx
        for r, row in enumerate(bits):
            for c, v in enumerate(row):
                if v:
                    px[y + r][xs + c] = rgb
        y += len(bits) + gap
    return px, W, H


def card_cells(card: dict):
    """Half-block cells (rows top first of (223, fg upper, bg lower)) as compile_sign_planes.plane_akm takes."""
    px, W, H = card_grid(card)
    return [[(HALF_UPPER, px[2 * r][c], px[2 * r + 1][c]) for c in range(W)] for r in range(H // 2)]


def load_cards(path: Path = None) -> dict:
    """key -> card, key = placement_id or place_feature_id. Absent file: no cards."""
    p = Path(path or CARDS)
    if not p.is_file():
        return {}
    out = {}
    for c in json.loads(p.read_text(encoding="utf-8"))["cards"]:
        k = c.get("placement_id") or c.get("place_feature_id")
        if not k or k in out:
            raise ValueError("authored sign card without a unique placement_id/place_feature_id: %r" % c.get("card_id"))
        out[k] = c
    return out


def fit_card(card: dict, slot_w_m: float, slot_h_m: float) -> dict:
    """The card scaled (size and pixel size together, so the pixel grid is unchanged) to the largest size that fits
    the slot, never above its nominal size."""
    w, h = float(card["size_m"][0]), float(card["size_m"][1])
    k = min(1.0, slot_w_m / w, slot_h_m / h)
    return {**card, "size_m": [w * k, h * k], "px_m": [card["px_m"][0] * k, card["px_m"][1] * k]}
