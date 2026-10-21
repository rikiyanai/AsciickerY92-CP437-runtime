#!/usr/bin/env python3
"""FL-4876 CARD-FIX-01. Logo planes clear every facade surface by triangle overlap; shop cards are authored.

Pins: a board whose vertices all lie outside the logo rectangle but whose face covers it is seen (the old vertex test
missed it); an edge-on triangle is not a surface; attach_* meshes (no host) are found by overlap; a place-front plane
keys off identity_sign.fascia_face_o_m and ignores the glyphs 0.04 m in front of the band; every authored card renders
inside its board, its text is drawn from the pixel font, and the owner file has one card per key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline import authored_sign_cards as AC  # noqa: E402
from scripts.pipeline.compile_sign_planes import ANY_HOST, front_depth_m, planes_for_signs  # noqa: E402

SC = 2.0


def _quad(x0, x1, z0, z1, y):
    """Two triangles of a board facing +y (a normal of (0, 1)), in world units."""
    a, b, c, d = (x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)
    return [(a, b, c), (a, c, d)]


def test_front_depth_sees_a_board_whose_vertices_are_all_outside_the_rect():
    big = _quad(-50.0, 50.0, -50.0, 50.0, 3.0 * SC / 2)  # 1.5 m out; logo rect is tiny, no vertex inside it
    d, who = front_depth_m({"h": [("board", big)]}, "h", 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 2.0, SC)
    assert who == "board" and d == pytest.approx(1.5)


def test_front_depth_ignores_a_triangle_edge_on_to_the_wall():
    side = [((0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 2.0, 2.0))]  # lies in the plane x = 0: no area along the wall
    assert front_depth_m({"h": [("side", side)]}, "h", 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 2.0, SC) == (None, None)


def test_front_depth_finds_a_host_less_attachment_but_not_another_hosts_fixture():
    near = _quad(-1.0, 1.0, 0.0, 2.0, 0.6 * SC)
    meshes = {ANY_HOST: [("attach_x", near)], "other": [("far", _quad(-1.0, 1.0, 0.0, 2.0, 2.0 * SC))]}
    d, who = front_depth_m(meshes, "h", 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 2.0, SC)
    assert who == "attach_x" and d == pytest.approx(0.6)


def test_front_depth_clips_to_the_logo_rectangle():
    out_of_span = _quad(5.0, 9.0, 0.0, 2.0, 1.0 * SC)  # beside the logo, not in front of it
    assert front_depth_m({"h": [("b", out_of_span)]}, "h", 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 2.0, SC) == (None, None)


class _FlatTerrain:  # wall_distance_m needs a terrain only when a footprint face does not decide
    pass


def _sign(**kw):
    return {"placement_id": "p", "sprite": "sign_p.xp", "pos": [0.0, 0.0, 10.0], "normal": [0.0, 1.0], **kw}


def _write_xp(path):
    from scripts.pipeline.compile_sign_sprites import xp_bytes
    path.write_bytes(xp_bytes([[(223, (0, 0, 0), (255, 255, 255))] * 4] * 2))


def test_place_front_plane_keys_off_the_band_face_and_ignores_glyphs(tmp_path):
    _write_xp(tmp_path / "sign_p.xp")
    meta = {"content_scale": SC, "origin_easting": 0, "origin_northing": 0, "offset_x": 0, "offset_y": 0}
    glyphs = {"h": [("glyph", _quad(-9.0, 9.0, 0.0, 99.0, 0.34 * SC))]}  # 0.04 m in front of a 0.30 m band face
    inst, rec = planes_for_signs([_sign(plane_face_o_m=0.30, size_m=[1.0, 0.5])], tmp_path, tmp_path, None, meta, {},
                                 1.5, 12.0, 16.0, frozenset(), glyphs)
    plane = rec[0]["plane"]
    assert plane["moved_from_sprite_m"] == pytest.approx(0.36)
    assert inst[0]["transform"][13] == pytest.approx(0.36 * SC)  # y: anchor + (0.30 + 0.06 m) * content scale
    assert (plane["width_m"], plane["height_m"]) == (1.0, 0.5)  # the card's own size, not the sprite density


def test_plane_clears_the_frontmost_surface_by_0_06_m(tmp_path):
    _write_xp(tmp_path / "sign_p.xp")
    meta = {"content_scale": SC, "origin_easting": 0, "origin_northing": 0, "offset_x": 0, "offset_y": 0}
    # face y = 0, outward normal +y; rings are easting/northing in metres (planes_for_signs scales them)
    ring = [(-20.0 / SC, 0.0), (20.0 / SC, 0.0), (20.0 / SC, -10.0), (-20.0 / SC, -10.0)]
    poster = {"h": [("poster", _quad(-9.0, 9.0, 0.0, 99.0, 0.8 * SC))]}
    inst, rec = planes_for_signs([_sign()], tmp_path, tmp_path, None, meta, {"h": ring}, 1.5, 12.0, 16.0,
                                 frozenset({"h"}), poster)
    plane = rec[0]["plane"]
    assert plane["frontmost_surface"] == "poster"
    assert plane["moved_from_sprite_m"] == pytest.approx(0.86)
    assert plane["clearance_m"] == pytest.approx(0.06)


# --- authored cards -------------------------------------------------------------------------------------------

CARDS = AC.load_cards()


def test_owner_file_has_the_fifteen_shops_and_the_brand_fronts():
    ids = {c["card_id"] for c in CARDS.values()}
    assert {"familymart", "lawson", "cafe_bit", "komatsuya", "damassa", "respa", "shige"} <= ids
    assert len(CARDS) >= 20
    assert "feature:place:node/441523546" in CARDS  # Lawson: no photo, brand prior
    assert "brand prior" in CARDS["feature:place:node/441523546"]["text_basis"]


@pytest.mark.parametrize("key", sorted(CARDS))
def test_card_renders_inside_its_board_with_a_stated_reading(key):
    card = CARDS[key]
    cells = AC.card_cells(card)
    px, W, H = AC.card_grid(card)
    assert len(cells) == H // 2 and len(cells[0]) == W
    assert all(AC.KEY not in (c[1], c[2]) for row in cells for c in row)  # the transparency key never appears
    ink = AC._rgb(card["text_rgb"])
    assert any(ink in row for row in px), "no text pixel drawn"
    assert card["text_reading"] and "romanised" in card and card["text_basis"]


def test_font_refuses_a_glyph_it_cannot_draw():
    with pytest.raises(ValueError):
        AC.text_pixels("みかさ", 1, 1)


def test_fit_card_scales_size_and_pixel_size_together():
    card = {"size_m": [4.0, 1.0], "px_m": [0.1, 0.05], "board_rgb": [1, 1, 1], "text_rgb": [2, 2, 2],
            "lines": [{"text": "AB"}]}
    fitted = AC.fit_card(card, 2.0, 5.0)
    assert fitted["size_m"] == [2.0, 0.5] and fitted["px_m"] == [0.05, 0.025]
    assert AC.card_grid(fitted)[1:] == AC.card_grid(card)[1:]  # same pixel grid
    assert AC.fit_card(card, 9.0, 9.0)["size_m"] == [4.0, 1.0]  # never above the nominal size


def test_stack_draws_one_character_per_row_block():
    card = {"size_m": [0.6, 2.0], "px_m": [0.1, 0.05], "board_rgb": [0, 0, 0], "text_rgb": [255, 255, 255],
            "lines": [{"text": "IL", "stack": True, "gap_px": 1}]}
    px, W, H = AC.card_grid(card)
    lit_rows = [r for r in range(H) if any(p == (255, 255, 255) for p in px[r])]
    assert len(lit_rows) == 14 and lit_rows[7] - lit_rows[6] == 2  # two 7-row letters with a one-row gap


def test_the_familymart_photo_binding_is_gone_and_the_card_replaces_it():
    doc = json.loads((AC.CARDS.parent / "place_front_logo_sources.v1.json").read_text(encoding="utf-8"))
    assert "feature:place:node/2929215092" not in {b["place_feature_id"] for b in doc["bindings"]}
    assert "feature:place:node/2929215092" in CARDS
