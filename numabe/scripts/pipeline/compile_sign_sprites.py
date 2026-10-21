"""Shop logos as XP sprites from the capture-review crops (FL-4876 SIGN-XP-01).

The custom-identity owner (compile_custom_asset_plan -> lower_plan_custom)
already places every operator-boxed storefront / signboard at its source
position, but colours it with the crop's single dominant colour, so no logo
is ever visible. This stage keeps the crop itself: it quantises each crop to a
CP437 half-block cell grid (glyph 223, fg = upper pixel, bg = lower pixel,
exact 6-level terminal-cube colours), writes it as a single-frame .xp sprite
into "<map stem>.sprites/" (registered by the Classic runtime before the map
loads, engine/sprite_registry.cpp RegisterMapSprites), and adds one sprite
instance per crop to the map at the custom instance's position, pushed
SPRITE_OUT_M toward the street.

Sheet layout (engine/sprite.cpp LoadSpriteLayer, as assets/sprites/ufo.xp):
layer 0 metadata ((1,0)='1' one frame, (0,1)='0' y ref = bottom row,
(0,2)='0' z ref, background = key (255,255,85)); layer 1 per-cell height
digits counted up from the bottom; layer 2 the image.

SIGN-XP-02: an OSM brand place front (FamilyMart, My Basket, Cocokara Fine)
takes its logo once, from the reviewed crop bound in
place_front_logo_sources.v1.json, on the front's identity_sign fascia band.
Custom-identity crops of the same brand are per-camera duplicates (their pose
resolution scattered six FamilyMart crops over five buildings, 0.4-65 m from
the place), so they get no sprite. A custom sprite whose position falls inside
a building footprint is skipped and named in the receipt. Sprite size follows
the Classic sprite blit density, so a metre of sign is a metre of building.

usage: compile_sign_sprites.py --run-root RUN --map MAP [--logo-bindings JSON]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "docs" / "agent" / "cli-anything")]
from scripts.pipeline.compile_custom_asset_plan import resolve_crop_path  # noqa: E402
from scripts.pipeline.authored_sign_cards import card_cells, fit_card, load_cards  # noqa: E402

SCHEMA = "y92.sign_sprites.v1"
KEY = (255, 255, 85)
LEVELS = (0, 51, 102, 153, 204, 255)
HALF_UPPER = 223
SPRITE_OUT_M = 0.3
# Classic sprite blit density (engine/render/render_sprite_blit.cpp, terrain.h,
# sprite_constants.h): ds = 2*SPRITE_ZOOM*SPRITE_SCALE/VISUAL_CELLS*0.5 cells per
# HEIGHT_CELLS-scaled world unit, so one world x/y unit is HEIGHT_CELLS*ds
# columns, and one row spans dz_dy = HEIGHT_SCALE/(cos30*HEIGHT_CELLS*ds) z.
_HEIGHT_CELLS, _VISUAL_CELLS, _SPRITE_ZOOM, _SPRITE_SCALE = 4, 8, 1.0, 3.0
_DS = 2.0 * _SPRITE_ZOOM * _SPRITE_SCALE / _VISUAL_CELLS * 0.5
COLS_PER_WORLD_UNIT = _HEIGHT_CELLS * _DS                                   # 1.5
Z_PER_ROW = 16.0 / (math.cos(math.radians(30.0)) * _HEIGHT_CELLS * _DS)    # ~12.32
MAX_CELLS = 64
MAX_ROWS = 22
MAPPING = (ROOT / "artifacts/experiments/fl4813_capture_review_20260915/preserved/"
           "numabe_full_727_mapping_20260915/observation_module_mapping.jsonl")
# SIGN-XP-02: brand place fronts take their logo from a reviewed crop binding.
LOGO_BINDINGS = (ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
                 "place_front_logo_sources.v1.json")
HEIGHT_SCALE = 16.0  # instance z scale: world z per metre = content_scale * 16
DEFAULT_FASCIA_M = (2.02, 3.22)  # tokyo familymart/mybasket identity_sign fascia band


def is_sign_subject(label) -> bool:
    """A custom-identity subject that is signage, not a whole frontage or a scaffold."""
    n = str(label or "").lower()
    return "sign" in n and "scaffold" not in n


def _snap(rgb) -> tuple[int, int, int]:
    c = tuple(min(LEVELS, key=lambda v: abs(v - int(x))) for x in rgb[:3])
    return (255, 255, 51) if c == KEY else c  # never the transparency key


def cells_for(img: Image.Image, width_m: float, max_height_m: float, sc: float) -> list[list[tuple]]:
    """Half-block grid that shows the crop at its true size in metres.

    The crop keeps its aspect; it spans ``width_m`` unless that would exceed
    ``max_height_m``, in which case the height binds. Columns and rows come
    from the Classic sprite density, so a metre of sign is as many cells as a
    metre of the building behind it.
    """
    w, h = img.size
    aspect = h / max(w, 1)
    wm = min(width_m, max_height_m / max(aspect, 1e-6))
    hm = wm * aspect
    cols = max(4, min(MAX_CELLS, round(wm * sc * COLS_PER_WORLD_UNIT)))
    rows = max(1, min(MAX_ROWS, round(hm * sc * HEIGHT_SCALE / Z_PER_ROW)))
    small = img.convert("RGB").resize((cols, rows * 2), Image.LANCZOS)
    px = small.load()
    return [[(HALF_UPPER, _snap(px[x, 2 * y]), _snap(px[x, 2 * y + 1])) for x in range(cols)]
            for y in range(rows)]


def write_xp(path: Path, visual: list[list[tuple]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(xp_bytes(visual))


def _height_digit(k: int) -> int:
    k = max(0, min(35, int(k)))
    return ord("0") + k if k < 10 else ord("A") + k - 10


def xp_bytes(visual: list[list[tuple]]) -> bytes:
    """The gzip .xp sheet for a visual cell grid (rows top first, (glyph, fg, bg))."""
    rows, cols = len(visual), len(visual[0])
    if cols < 2:
        raise ValueError("xp_bytes: a sprite needs >= 2 columns (the header cell is layer 0 column 1); got %d" % cols)
    l0 = [[(0, (0, 0, 0), KEY) for _ in range(cols)] for _ in range(rows)]
    l0[0][1] = (ord("1"), (0, 0, 0), KEY)
    if rows > 1:
        l0[1][0] = (ord("0"), (0, 0, 0), KEY)
    if rows > 2:
        l0[2][0] = (ord("0"), (0, 0, 0), KEY)
    # Per-cell height in rows above the bottom, as the engine reads layer 1
    # (engine/sprite.cpp: '0'-'9' = 0-9, 'A'-'Z' = 10-35; the blit's depth test is
    # height >= sample height, so a row capped below its true height loses to the
    # wall it stands on — FACADE-CAPTURE-01 measured rows 10+ of a 17-row sprite hidden).
    l1 = [[(_height_digit(rows - 1 - y), (0, 0, 0), (0, 0, 0)) for _ in range(cols)] for y in range(rows)]
    buf = io.BytesIO()
    buf.write(struct.pack("<i", -1))
    buf.write(struct.pack("<I", 3))
    for cells in (l0, l1, visual):
        buf.write(struct.pack("<ii", cols, rows))
        for x in range(cols):
            for y in range(rows):
                g, fg, bg = cells[y][x]
                buf.write(struct.pack("<I", g))
                buf.write(bytes(fg))
                buf.write(bytes(bg))
    return gzip.compress(buf.getvalue(), compresslevel=9, mtime=0)


def _crop_index() -> dict:
    out = {}
    for line in MAPPING.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            aid = d.get("annotation_id") or d.get("id")
            if aid and d.get("crop_path"):
                out[str(aid)] = d["crop_path"]
    return out


def _norm(text) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _carriageway_test(run_root: Path, meta: dict):
    """Return f(world_x, world_y) -> True when the point is on painted carriageway.

    Uses the same surfaces owner and inputs the population plan uses for its
    ROADSIDE-SNAP-01 pole test (world_surfaces.load_surfaces with the run's
    placement zones, resolved rules and surface stamp plan).
    """
    sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))
    from world_surfaces import load_surfaces
    surf = load_surfaces(run_root / "observation_registration" / "placement_zones.json",
                         run_root / "canonical.world_ir.json",
                         run_root / "plan" / "rules_resolved.json", None,
                         painted_plan_path=run_root / "plan" / "surface_stamp_plan.json")
    sc = float(meta["content_scale"])
    oe, on = float(meta["origin_easting"]), float(meta["origin_northing"])
    ox, oy = float(meta["offset_x"]), float(meta["offset_y"])

    def on_carriageway(x: float, y: float) -> bool:
        return "carriageway" in surf.point_surfaces(oe + (x - ox) / sc, on + (y - oy) / sc)
    return on_carriageway


def _front_profiles() -> dict:
    out = {}
    for pack in ("common_global", "japan_urban", "tokyo"):
        doc = json.loads((ROOT / "assets/world_templates" / pack / "pack.json").read_text(encoding="utf-8"))
        for p in doc.get("place_front_profiles", []) or []:
            out[p["id"]] = p
    return out


def brand_front_logos(run_root: Path, sprite_dir: Path, sc: float,
                      bindings_path: Path, cards_path: Path = None) -> tuple[list[dict], list[dict], set[str]]:
    """SIGN-XP-02: one logo sprite per bound brand place front, on its fascia band.

    The profile's identity_sign fascia band (fascia_z_min_m..fascia_z_max_m,
    else the label band) bounds the logo height; the fitted front width times
    label_width_fraction bounds its width.
    """
    if not bindings_path.is_file():
        return [], [{"reason": "logo bindings absent", "path": str(bindings_path)}], set()
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))["bindings"]
    fronts = {f["feature_id"]: f for f in
              json.loads((run_root / "place_front_instances.json").read_text(encoding="utf-8"))}
    profiles = _front_profiles()
    signs, skipped, brands = [], [], set()
    # FL-4876 ENTITY-01: a bound crop may present a canonical place only when
    # the entity resolver linked its observation to that entity AND that
    # observation supports the entity's independently resolved host.
    ent_path = run_root / "observation_registration" / "identity_entities.json"
    if not ent_path.is_file():
        raise SystemExit("FL-4876 ENTITY-01: identity_entities.json absent; "
                         "logo crops cannot be bound without entity resolution")
    eligible = {e["place_id"]: set(e.get("crop_eligible_annotations") or [])
                for e in json.loads(ent_path.read_text(encoding="utf-8"))["entities"]
                if e.get("kind") == "mapped_place" and e.get("state") == "entity"}
    # FL-4876 CARD-FIX-01: a hand-authored card (authored_sign_cards.json) replaces the photo crop of its front,
    # and a front with a card needs no crop binding (Lawson has no photo).
    cards = load_cards(cards_path)
    for fid, card in cards.items():
        if not fid.startswith("feature:place:"):
            continue
        front = fronts.get(fid)
        if front is None:
            skipped.append({"place_feature_id": fid, "card_id": card.get("card_id"), "reason": "front not emitted"})
            continue
        prof = profiles.get(front.get("resolved_profile_id") or "", {})
        slot = prof.get("identity_sign") or {}
        z0 = float(slot.get("fascia_z_min_m", DEFAULT_FASCIA_M[0]))
        z1 = float(slot.get("fascia_z_max_m", DEFAULT_FASCIA_M[1]))
        slot_w = float(front.get("fitted_width_m") or 4.0) * float(slot.get("label_width_fraction", 0.9))
        fitted = fit_card(card, slot_w, z1 - z0)
        wm, hm = fitted["size_m"]
        cells = card_cells(fitted)
        name = "sign_front_%s_card.xp" % fid.rsplit("/", 1)[-1]
        write_xp(sprite_dir / name, cells)
        t = front["transform"]
        nx, ny = front["front_outward_normal_xy"]
        ax, ay = front["front_anchor_world_xy"]
        face_o = (front.get("identity_sign") or {}).get("fascia_face_o_m")
        out_m = 0.0 if face_o is not None else SPRITE_OUT_M  # planes_for_signs adds face_o itself
        # centred in the band: the plane is anchored at its bottom edge
        pos = [ax + nx * out_m * sc, ay + ny * out_m * sc,
               float(t[14]) + (z0 + max(0.0, (z1 - z0 - hm) / 2.0)) * sc * HEIGHT_SCALE]
        brands.add(_norm(front.get("label")))
        signs.append({"placement_id": "place_front:" + fid, "label": front.get("label"), "sprite": name,
                      "card_id": card.get("card_id"), "annotation_id": None, "crop": None,
                      "cells": [len(cells[0]), len(cells)], "slot_m": [round(slot_w, 3), z0, z1],
                      "size_m": [round(wm, 4), round(hm, 4)],
                      "pos": [round(v, 3) for v in pos], "kind": "brand_front_logo",
                      "normal": [nx, ny], "height_above_street_m": z0,
                      # the emitter's flat band face (metres from the anchor): the plane stands 0.06 m beyond it and
                      # is never depth-tested against the block-text glyphs that stand 0.04 m in front of the band
                      "plane_face_o_m": float(face_o) if face_o is not None else None})
    for b in bindings:
        fid = b["place_feature_id"]
        if fid in cards:
            continue
        front = fronts.get(fid)
        crop = ROOT / b["crop"]
        if b.get("annotation_id") not in eligible.get(fid, set()):
            skipped.append({"place_feature_id": fid, "annotation_id": b.get("annotation_id"),
                            "reason": "crop observation not linked to this entity's host "
                                      "(identity_entities crop_eligible_annotations)"})
            continue
        if front is None or not crop.is_file():
            skipped.append({"place_feature_id": fid,
                            "reason": "front not emitted" if front is None else "crop absent"})
            continue
        prof = profiles.get(front.get("resolved_profile_id") or "", {})
        slot = prof.get("identity_sign") or {}
        # A profile without identity_sign (tokyo.pharmacy_front) uses the
        # fascia band every Tokyo identity_sign profile declares.
        z0 = float(slot.get("fascia_z_min_m", DEFAULT_FASCIA_M[0]))
        z1 = float(slot.get("fascia_z_max_m", DEFAULT_FASCIA_M[1]))
        width_m = float(front.get("fitted_width_m") or 4.0) * float(slot.get("label_width_fraction", 0.9))
        cells = cells_for(Image.open(crop), width_m, z1 - z0, sc)
        digest = hashlib.sha256(crop.read_bytes()).hexdigest()[:10]
        name = "sign_front_%s_%s.xp" % (fid.rsplit("/", 1)[-1], digest)
        write_xp(sprite_dir / name, cells)
        t = front["transform"]
        nx, ny = front["front_outward_normal_xy"]
        ax, ay = front["front_anchor_world_xy"]
        pos = [ax + nx * SPRITE_OUT_M * sc, ay + ny * SPRITE_OUT_M * sc,
               float(t[14]) + z0 * sc * HEIGHT_SCALE]
        brands.add(_norm(front.get("label")))
        signs.append({"placement_id": "place_front:" + fid, "label": front.get("label"),
                      "sprite": name, "crop": b["crop"], "annotation_id": b["annotation_id"],
                      "cells": [len(cells[0]), len(cells)], "slot_m": [round(width_m, 3), z0, z1],
                      "pos": [round(v, 3) for v in pos], "kind": "brand_front_logo",
                      "normal": [nx, ny], "height_above_street_m": z0})
    return signs, skipped, brands


# FL-4876 SIGN-WALL-01. Buildings are extruded into the terrain, and the bake
# raises a height vertex only when every incident cell is inside the footprint
# (bake_osm_building_footprints.terrain_support_inside_footprint, FL-4814), so
# the visible wall stands 0.8-1.4 m inside the canonical footprint face the
# fronts are anchored to. Probed on build_reg_29's own terrain, 0 of 36 sign
# sprites hugged the wall (gaps 0.9-2.1 m, three below street level, one
# 3.9 m inside). Each sprite is therefore placed on the wall it faces, measured
# on the map's terrain along the front normal.
CUSTOM_DEFAULT_Z_M = 1.8      # median custom sign mount height in build_reg_29
BUSINESS_FASCIA_MAX_M = 1.5   # tallest horizontal shop fascia sign drawn (LOGO-CROP-01)
BUSINESS_BLADE_MAX_M = 3.5    # tallest vertical blade sign drawn
# SIGN-CLEAR-01: sprite anchor this far outside the terrain wall foot. Measured on
# build_reg_73 from 8 camera headings with the build's own sprites (after SIGN-SHADOW-01;
# facade_capture_checks/sign_yaws_*_unshadowed.jsonl): signs showing >= 40% of their
# cells from some heading: 7/19 at the foot, 17/19 at +1 m (komatsuya 270/270,
# 割烹浜蝶 120/120). Mass-mesh walls stand outside the terrain wall the foot measures.
FOOT_CLEAR_M = 1.0


def snap_to_wall(terrain, pos, normal, sc: float, height_above_street_m=None):
    """(new_pos, record) with the sprite at the foot of the wall it faces.

    The wall comes from extruded_wall.wall_foot (the baked map's terrain).
    z is street level at the foot plus the sign's mount height; a custom
    sign keeps its own mount height when it is plausible (0.5-6 m), else
    CUSTOM_DEFAULT_Z_M. Returns (None, record) with no wall behind.
    """
    from scripts.pipeline.extruded_wall import wall_foot
    z_per_m = sc * HEIGHT_SCALE
    hit = wall_foot(terrain, pos[0], pos[1], normal, sc)
    if hit is None:
        return None, {"reason": "no extruded wall behind the sprite"}
    nx, ny = normal
    n = math.hypot(nx, ny) or 1.0
    nx, ny = nx / n, ny / n
    # FL-4876 SIGN-CLEAR-01: stand FOOT_CLEAR_M past the foot. Classic sprite depth
    # is height-only (render_sprite_blit.cpp: height >= sample height); at the foot
    # itself build_reg_73 drew 0 cells for all 19 signs, while the same sprite 1 m
    # further out drew 16-173 cells on 9 of them (facade_capture_checks/sign_vis.mjs).
    o = hit["foot_m"] + FOOT_CLEAR_M
    rel_m = height_above_street_m
    if rel_m is None:
        rel_m = (float(pos[2]) - hit["street_h"]) / z_per_m
        if not 0.5 <= rel_m <= 6.0:
            rel_m = CUSTOM_DEFAULT_Z_M
    z = hit["street_h"] + rel_m * z_per_m
    new = [pos[0] + nx * o * sc, pos[1] + ny * o * sc, z]
    return new, {"wall_offset_m": hit["wall_m"], "moved_along_normal_m": o,
                 "height_above_street_m": round(rel_m, 2),
                 "source_pos": [round(v, 3) for v in pos]}


# FL-4876 FACADE-CAPTURE-01. Operator 2026-10-13: "I want to screenshot facade for
# lawson, paste it on the currently grey/missing lawson face, it should let me
# convert to xp sprite billboard or whatever is cheapest for runtime". The a3d
# editor (artifacts/experiments/fl4876_manual_world/map_editor.py) rectifies an
# operator photo onto ONE footprint face, the operator marks bands, and each
# sprite band's CP437 cells are written as an .xp under operator_facade_sprites/
# with the capture recorded in operator_facade_captures.v1.json. This stage
# places every accepted sprite band at capture_anchor(), the same function the
# editor's live preview calls, so the preview is the build.
OPERATOR_CAPTURES = LOGO_BINDINGS.parent / "operator_facade_captures.v1.json"
OPERATOR_SUPERSEDE_M = 2.5   # other sign sprites this close to a captured face are replaced by the capture


def face_frame(ring_en, i: int) -> dict:
    """Face i of an E/N footprint ring as a viewer on the street sees it.

    ``left`` is the end on the viewer's left, ``right`` the unit vector from it
    to the other end, ``normal`` the unit outward normal (E/N; world x/y share
    the orientation because the projection is a uniform scale + offset).
    """
    n = len(ring_en)
    area = sum(ring_en[k][0] * ring_en[(k + 1) % n][1] - ring_en[(k + 1) % n][0] * ring_en[k][1]
               for k in range(n))
    p, q = ring_en[i][:2], ring_en[(i + 1) % n][:2]
    de, dn = q[0] - p[0], q[1] - p[1]
    length = math.hypot(de, dn) or 1e-9
    ne, nn = ((dn, -de) if area > 0 else (-dn, de))
    ne, nn = ne / length, nn / length
    rx, ry = -nn, ne  # the viewer faces -normal; their right is that direction turned clockwise
    left = p if p[0] * rx + p[1] * ry <= q[0] * rx + q[1] * ry else q
    return {"left": list(left), "right": [rx, ry], "normal": [ne, nn], "length_m": length,
            "bearing_deg": round(math.degrees(math.atan2(ne, nn)) % 360, 1)}


def _world(meta: dict, E: float, N: float):
    sc = float(meta["content_scale"])
    return ((E - float(meta["origin_easting"])) * sc + float(meta["offset_x"]),
            (N - float(meta["origin_northing"])) * sc + float(meta["offset_y"]))


def face_metrics(terrain, meta: dict, ring_en, i: int) -> dict:
    """World geometry of face i on the baked map: ends, street level and eave height.

    Street level and the wall come from extruded_wall.wall_foot at the face
    midpoint; the eave is the terrain 0.3 m inside the extruded wall there.
    """
    from scripts.pipeline.extruded_wall import wall_foot
    sc = float(meta["content_scale"])
    z_per_m = sc * HEIGHT_SCALE
    fr = face_frame(ring_en, i)
    L, (lx, ly), (rx, ry), (nx, ny) = fr["length_m"], fr["left"], fr["right"], fr["normal"]
    a = _world(meta, lx, ly)
    b = _world(meta, lx + rx * L, ly + ry * L)
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    hit = wall_foot(terrain, mid[0], mid[1], (nx, ny), sc)
    out = {**fr, "left_world": list(a), "right_world": list(b), "content_scale": sc, "z_per_m": z_per_m}
    if hit is None:
        return {**out, "street_z": None, "eave_z": None, "height_m": None, "wall_offset_m": None}
    o = hit["wall_m"] - 0.3
    top = terrain.height(mid[0] + nx * o * sc, mid[1] + ny * o * sc)
    h = None if top is None else round((top - hit["street_h"]) / z_per_m, 2)
    return {**out, "street_z": hit["street_h"], "eave_z": top, "height_m": h, "wall_offset_m": hit["wall_m"]}


def capture_anchor(terrain, meta: dict, ring_en, i: int, x_center_m: float, y0_m: float,
                   out_m: float = None):
    """Sprite position for a capture band: x_center_m along face i from its left end
    (street view), bottom y0_m above street level, at least out_m (default
    SPRITE_OUT_M) outside the footprint face and never inside the baked wall.
    out_m is the operator's per-band clearance: a building drawn by a mass mesh
    stands further out than its terrain wall (8 build_reg_73 signs needed 0.5-1 m
    more than the foot; facade_capture_checks/sign_sweep). Returns (pos | None, record)."""
    out_m = SPRITE_OUT_M if out_m is None else max(0.0, min(3.0, float(out_m)))
    sc = float(meta["content_scale"])
    fr = face_frame(ring_en, i)
    (lx, ly), (rx, ry), (nx, ny) = fr["left"], fr["right"], fr["normal"]
    x, y = _world(meta, lx + rx * x_center_m, ly + ry * x_center_m)
    pos = (x + nx * SPRITE_OUT_M * sc, y + ny * SPRITE_OUT_M * sc, 0.0)
    new, rec = snap_to_wall(terrain, pos, (nx, ny), sc, y0_m)
    if new is None:
        return new, rec
    # The anchor must not sit inside the baked building. Measured on build_reg_73
    # (Lawson face 1, zoom 1, camera facing the face; scratch sweep fc_sweep.mjs):
    # anchors 0 to +2 m from the footprint face drew all rows (5/5 sign, 17/17
    # storefront); -0.2 to -0.6 m drew none. snap_to_wall's foot was -0.1 m there
    # (hidden), so a capture sprite stands SPRITE_OUT_M outside the face, or 0.1 m
    # past the measured foot when the wall stands further out than that.
    foot_from_face = SPRITE_OUT_M + rec["moved_along_normal_m"]
    off = max(out_m, foot_from_face + 0.1)
    new = [x + nx * off * sc, y + ny * off * sc, new[2]]
    rec = {**rec, "anchor": "footprint face + %.2f m (never inside the baked building)" % off,
           "anchor_from_face_m": round(off, 2)}
    return new, rec


def _rings(run_root: Path) -> dict:
    canon = json.loads((run_root / "canonical.world_ir.json").read_text(encoding="utf-8"))
    out = {}
    for f in canon["canonical_features"]:
        g = f.get("geometry") or {}
        if f.get("semantic_class") == "building_footprint" and g.get("kind") == "polygon":
            ring = [q[:2] for q in g["coordinates"]]
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]
            out[f["feature_id"]] = ring
    return out


def operator_capture_signs(run_root: Path, sprite_dir: Path, terrain, meta: dict,
                           captures_path: Path = None) -> tuple[list[dict], list[dict], list[tuple]]:
    """FACADE-CAPTURE-01: one sprite per accepted capture sprite band.

    Returns (signs, skipped, faces) where faces are the captured face segments
    in world units, used to retire other sign sprites on the same face.
    """
    path = captures_path or OPERATOR_CAPTURES
    if not path.is_file():
        return [], [], []
    doc = json.loads(path.read_text(encoding="utf-8"))
    rings = _rings(run_root)
    signs, skipped, faces = [], [], []
    for cap in doc.get("captures", []):
        if cap.get("status") != "accepted":
            continue
        ring = rings.get(cap["building_id"])
        if ring is None or not 0 <= int(cap["face_index"]) < len(ring):
            skipped.append({"capture_id": cap["capture_id"], "reason": "building/face not in this run's footprints"})
            continue
        i = int(cap["face_index"])
        fm = face_metrics(terrain, meta, ring, i)
        made = len(signs)
        for k, band in enumerate(cap.get("bands", [])):
            if band.get("mode") != "sprite" or not band.get("sprite"):
                continue
            src = path.parent / band["sprite"]
            if not src.is_file():
                skipped.append({"capture_id": cap["capture_id"], "band": k, "reason": "sprite file absent"})
                continue
            xc = (float(band["x0_m"]) + float(band["x1_m"])) / 2
            pos, rec = capture_anchor(terrain, meta, ring, i, xc, float(band["y0_m"]), band.get("out_m"))
            if pos is None:
                skipped.append({"capture_id": cap["capture_id"], "band": k, "reason": rec["reason"]})
                continue
            name = "sign_opcap_%s_%d_%s.xp" % (cap["capture_id"], k, hashlib.sha256(src.read_bytes()).hexdigest()[:10])
            (sprite_dir / name).write_bytes(src.read_bytes())
            signs.append({"placement_id": "operator_facade_capture:%s:%d" % (cap["capture_id"], k),
                          "label": band.get("label") or band.get("kind"), "sprite": name,
                          "crop": band["sprite"], "cells": band.get("cells"),
                          "pos": [round(v, 3) for v in pos], "kind": "operator_facade_capture",
                          "building_id": cap["building_id"], "face_index": i,
                          "source_kind": (cap.get("source") or {}).get("kind"), "wall_snap": rec})
        # FACADE-CAPTURE-REVIEW-20261014 P1: a capture retires the other signs on its
        # face only when it actually put a replacement sprite there (a wall/trim-colour
        # capture keeps them) and the operator did not opt out (replaces_signs: false).
        if len(signs) > made and cap.get("replaces_signs", True) is not False:
            faces.append((cap["capture_id"], fm["left_world"], fm["right_world"]))
    return signs, skipped, faces


def _seg_dist2(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ((dx * dx + dy * dy) or 1e-9)))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def build(run_root: Path, map_path: Path, bindings_path: Path = None, cards_path: Path = None) -> dict:
    import a3d_edit as A
    fmt = A._load_a3d_format()
    meta = json.loads((run_root / "terrain_metadata.json").read_text(encoding="utf-8"))
    sc = float(meta["content_scale"])
    plan = json.loads((run_root / "plan" / "custom_asset_plan.json").read_text(encoding="utf-8"))
    rows = {r["placement_id"]: r for r in plan["rows"]}
    crops = _crop_index()
    inst = json.loads((run_root / "lower_custom_instances.json").read_text(encoding="utf-8"))
    sprite_dir = map_path.with_suffix(".sprites")
    sprite_dir.mkdir(parents=True, exist_ok=True)
    for old in sprite_dir.glob("sign_*.xp"):
        old.unlink()
    cards = load_cards(cards_path)
    signs, skipped, brands = brand_front_logos(run_root, sprite_dir, sc,
                                               bindings_path or LOGO_BINDINGS, cards_path)
    bound_annotations = {s["annotation_id"] for s in signs}
    on_carriageway = _carriageway_test(run_root, meta)
    from scripts.pipeline.extruded_wall import Terrain
    terrain = Terrain(map_path)
    for s in signs:  # SIGN-WALL-01: the brand logo goes on the wall it faces
        if s.get("plane_face_o_m") is not None:  # CARD-FIX-01: already on the emitter's band face
            s["wall_snap"] = {"reason": "plane keyed off identity_sign.fascia_face_o_m"}
            s["on_carriageway"] = bool(on_carriageway(s["pos"][0], s["pos"][1]))
            continue
        new, rec = snap_to_wall(terrain, s["pos"], s["normal"], sc, s["height_above_street_m"])
        s["wall_snap"] = rec
        if new is not None:
            s["pos"] = [round(v, 3) for v in new]
        s["on_carriageway"] = bool(on_carriageway(s["pos"][0], s["pos"][1]))
    # FL-4876 LOGO-CROP-01: an adjudicated business sign presents its entity's
    # sign-only crop at the entity's sign width (identity_entities.json).
    _ents = {e["entity_id"]: e for e in json.loads(
        (run_root / "observation_registration" / "identity_entities.json").read_text(encoding="utf-8"))["entities"]}
    _pop = run_root / "plan" / "population" / "population_plan.json"
    _pid_entity = {}
    if _pop.is_file():
        for c in json.loads(_pop.read_text(encoding="utf-8")).get("carriers", []):
            for p in c.get("placements", []):
                _pid_entity[p["placement_id"]] = (p.get("evidence") or {}).get("entity_id")
    for r in inst:
        pid = r.get("placement_id")
        row = rows.get(pid)
        t = r.get("transform")
        if row is None or not t:
            skipped.append({"placement_id": pid, "reason": "no custom asset row"})
            continue
        ent = _ents.get(_pid_entity.get(pid) or _pid_entity.get("obs-" + str(pid)) or "") or {}
        is_business = ent.get("presentation") == "storefront_sign" and ent.get("state") == "entity"
        label_n = _norm(row.get("label"))
        brand = next((bn for bn in brands if bn and bn in label_n), None)
        if not is_business and (brand or bound_annotations & set(row.get("annotation_ids") or [])):
            skipped.append({"placement_id": pid, "label": row.get("label"),
                            "reason": "duplicate observation of a brand place front (%s); "
                                      "its logo is placed once at the front" % (brand or "bound crop")})
            continue
        if not is_business and not is_sign_subject(row.get("label")):
            skipped.append({"placement_id": pid, "label": row.get("label"),
                            "reason": "not a sign subject (frontage or scaffold crop); no sprite"})
            continue
        card = cards.get(pid)
        crop = None
        if card is None and is_business and ent.get("logo_crop") and (ROOT / ent["logo_crop"]).is_file():
            crop = ROOT / ent["logo_crop"]
        aids = (ent.get("crop_eligible_annotations") if is_business else None) or row.get("annotation_ids") or []
        for aid in ([] if (crop or card) else aids):
            raw = crops.get(str(aid))
            p = resolve_crop_path(raw) if raw else None
            if p is not None and p.is_file():
                crop = p
                break
        if crop is None and card is None:
            skipped.append({"placement_id": pid, "reason": "no resolvable crop"})
            continue
        # Outward normal of the plane: the custom lowerer writes the plane in
        # its local XZ with the face normal on local +Y (transform column 1).
        nx, ny = float(t[4]), float(t[5])
        n = math.hypot(nx, ny) or 1.0
        x = float(t[12]) + nx / n * SPRITE_OUT_M * sc
        y = float(t[13]) + ny / n * SPRITE_OUT_M * sc
        z = float(t[14])
        # SIGN-WALL-01: onto the extruded wall the plane faces. The canonical
        # footprint test this replaced refused correctly placed sprites, since
        # the extruded wall stands inside the canonical ring by design.
        new, wall_rec = snap_to_wall(terrain, (x, y, z), (nx / n, ny / n), sc)
        if new is None:
            skipped.append({"placement_id": pid, "label": row.get("label"), "parent": r.get("parent"),
                            "reason": wall_rec["reason"]})
            continue
        x, y, z = new
        # the carriageway test is about where the sign is mounted (the wall foot), not
        # the SIGN-CLEAR-01 draw clearance: a shop wall on the road edge (damassa)
        # would otherwise lose its sign to the 0.2 m clearance
        if on_carriageway(x - nx / n * FOOT_CLEAR_M * sc, y - ny / n * FOOT_CLEAR_M * sc):
            skipped.append({"placement_id": pid, "label": row.get("label"), "parent": r.get("parent"),
                            "reason": "sprite position is on painted carriageway "
                                      "(custom attachment carrier %s)" % r.get("parent")})
            continue
        width_m = float(row.get("width_m") or r.get("plane_width_m") or 2.0)
        height_m = float(row.get("height_m") or r.get("plane_height_m") or 2.4)
        if card is not None:  # CARD-FIX-01: the authored card, at its own density, fitted to the slab's slot
            fitted = fit_card(card, width_m, height_m)
            wm, hm = fitted["size_m"]
            cells = card_cells(fitted)
            name = f"sign_{pid.replace('/', '_').replace(':', '_')}_card.xp"
            write_xp(sprite_dir / name, cells)
            signs.append({"placement_id": pid, "label": row.get("label"), "sprite": name,
                          "card_id": card.get("card_id"), "crop": None, "cells": [len(cells[0]), len(cells)],
                          "size_m": [round(wm, 4), round(hm, 4)],
                          "pos": [round(x, 3), round(y, 3), round(z, 3)], "kind": "custom_identity",
                          "normal": [nx / n, ny / n], "wall_snap": wall_rec})
            continue
        img = Image.open(crop)
        if is_business:
            # a fascia is at most BUSINESS_FASCIA_MAX_M tall; a vertical sign
            # (taller than wide) keeps its aspect up to BUSINESS_BLADE_MAX_M
            width_m = float(ent.get("sign_width_m") or width_m)
            vertical = img.height > 1.3 * img.width
            height_m = BUSINESS_BLADE_MAX_M if vertical else BUSINESS_FASCIA_MAX_M
            if vertical:
                width_m = min(width_m, height_m * img.width / img.height)
        cells = cells_for(img, width_m, height_m, sc)
        digest = hashlib.sha256(crop.read_bytes()).hexdigest()[:10]
        name = f"sign_{pid.replace('/', '_').replace(':', '_')}_{digest}.xp"
        write_xp(sprite_dir / name, cells)
        signs.append({"placement_id": pid, "label": row.get("label"), "sprite": name,
                      "crop": str(crop.relative_to(ROOT)), "cells": [len(cells[0]), len(cells)],
                      "pos": [round(x, 3), round(y, 3), round(z, 3)], "kind": "custom_identity",
                      "normal": [nx / n, ny / n], "wall_snap": wall_rec})
    # FACADE-CAPTURE-01: operator captures replace any other sign sprite standing
    # on the same face (same side: normals within ~45 deg, within OPERATOR_SUPERSEDE_M).
    op_signs, op_skipped, op_faces = operator_capture_signs(run_root, sprite_dir, terrain, meta)
    if op_faces:
        keep = []
        for s in signs:
            hit = None
            for cid, a, b in op_faces:
                fx, fy = b[0] - a[0], b[1] - a[1]
                fl = math.hypot(fx, fy) or 1.0
                fn = (fy / fl, -fx / fl)
                sn = s.get("normal") or fn
                same_side = abs(sn[0] * fn[0] + sn[1] * fn[1]) > 0.7
                if same_side and _seg_dist2(s["pos"][:2], a, b) <= OPERATOR_SUPERSEDE_M * sc:
                    hit = cid
                    break
            if hit:
                skipped.append({"placement_id": s["placement_id"], "label": s.get("label"),
                                "reason": "superseded by operator facade capture %s" % hit})
                (sprite_dir / s["sprite"]).unlink(missing_ok=True)
            else:
                keep.append(s)
        signs = keep
    signs += op_signs
    skipped += op_skipped
    # A capture that rebuilt a face's storefront replaces the generic place-front
    # mesh(es) the operator ticked (replaces_place_fronts, by instance name).
    replaced = set()
    if OPERATOR_CAPTURES.is_file():
        for cap in json.loads(OPERATOR_CAPTURES.read_text(encoding="utf-8")).get("captures", []):
            if cap.get("status") == "accepted":
                replaced.update(cap.get("replaces_place_fronts") or [])
    # FL-4876 SIGN-SHADOW-01: the Classic registry keeps the first sprite registered
    # under a name and LoadSprites registers assets/sprites before RegisterMapSprites
    # reads <map>.sprites/, so a same-named file in assets/sprites silently replaces
    # this build's sign (measured: 19 stale 1x copies there, FamilyMart 14x5 shown
    # instead of 21x8). Refuse instead of shipping a map whose signs never appear.
    shadowed = sorted(s["sprite"] for s in signs if (ROOT / "assets" / "sprites" / s["sprite"]).exists())
    if shadowed:
        raise SystemExit("SIGN-SHADOW-01: assets/sprites holds same-named files that would replace this "
                         "build's signs at runtime: " + ", ".join(shadowed[:5]) + (" ..." if len(shadowed) > 5 else ""))
    secs = list(A.read_a3d_sections(map_path))
    instances = secs[2]
    removed_fronts = sorted({str(i.inst_name) for i in instances if str(getattr(i, "inst_name", "")) in replaced})
    # Sprite records read back from a map carry their name in inst_name (mesh_name
    # is empty), so test both: re-running this stage on its own output doubled all
    # 19 build_reg_73 signs when only mesh_name was tested (FACADE-CAPTURE-01 check).
    instances = [i for i in instances if not (getattr(i, "variant", "") == "sprite"
                                              and (str(i.mesh_name).startswith("sign_")
                                                   or str(getattr(i, "inst_name", "")).startswith("sign_")))
                 and str(getattr(i, "inst_name", "")) not in replaced]
    skipped += [{"placement_id": n, "reason": "place-front mesh replaced by an operator facade capture"}
                for n in removed_fronts]
    # FL-4876 SIGN-PLANE-01: each logo goes on its wall as a flat mesh (compile_sign_planes), not as a billboard
    # sprite: a sprite turns with the camera and read as a card sticking out of the wall (operator 2026-10-16).
    # The .xp stays in <map>.sprites/ (the editor's capture preview and the receipt name it); no sprite instance
    # is placed. Earlier logo planes of this map are replaced, like the sprites above.
    from scripts.pipeline.compile_sign_planes import load_facade_meshes, paneled_buildings, planes_for_signs
    mesh_dir = run_root / "meshes"
    for old in mesh_dir.glob("signplane_*.akm"):
        old.unlink()
    instances = [i for i in instances if not str(getattr(i, "inst_name", "")).startswith("signplane_")]
    plane_insts, plane_recs = planes_for_signs(signs, sprite_dir, mesh_dir, terrain, meta, _rings(run_root),
                                               COLS_PER_WORLD_UNIT, Z_PER_ROW, HEIGHT_SCALE,
                                               paneled_buildings(run_root),
                                               load_facade_meshes(run_root, mesh_dir, [
                                                   (str(i.inst_name), str(i.mesh_name), list(i.transform))
                                                   for i in instances
                                                   if str(i.mesh_name).startswith("attach_")]))
    by_pid = {r["placement_id"]: r for r in plane_recs}
    for s in signs:
        r = by_pid.get(s.get("placement_id")) or {}
        if "plane" in r:
            s["plane"] = r["plane"]
        else:
            s["plane_skipped"] = r.get("skipped", "no plane record")
    for p in plane_insts:
        i = fmt.A3DInstance(mesh_name=p["mesh_name"], inst_name=p["inst_name"], transform=list(p["transform"]),
                            flags=3, story_id=-1, variant="mesh")
        instances.append(i)
    secs[2] = instances
    A.write_a3d_sections(map_path, *secs)
    return {"schema": SCHEMA, "sprite_dir": str(sprite_dir), "signs": signs, "skipped": skipped,
            "provenance": {"image": "capture-review crop (observation_module_mapping.jsonl); "
                                    "brand fronts from place_front_logo_sources.v1.json; "
                                    "operator facade captures (cells as authored) from "
                                    "operator_facade_captures.v1.json",
                           "position": "brand: place_front_instances anchor + outward normal, "
                                       "fascia band z; custom: lower_custom_instances transform "
                                       "+ outward normal",
                           "density": {"cols_per_world_unit": COLS_PER_WORLD_UNIT,
                                       "z_per_row": round(Z_PER_ROW, 4),
                                       "source": "engine/render/render_sprite_blit.cpp ds, dz_dy"},
                           "encoding": "CP437 half-block 223, fg upper / bg lower, 6-level cube"}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-root", type=Path, required=True)
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--logo-bindings", type=Path, default=LOGO_BINDINGS)
    ap.add_argument("--authored-cards", type=Path, default=None,
                    help="authored_sign_cards.json (default: beside the logo bindings)")
    a = ap.parse_args(argv)
    cards = a.authored_cards or a.logo_bindings.parent / "authored_sign_cards.json"
    doc = build(a.run_root, a.map, a.logo_bindings, cards)
    out = a.run_root / "sign_sprites_receipt.json"
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[sign-sprites] {len(doc['signs'])} sprites, {len(doc['skipped'])} skipped -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
