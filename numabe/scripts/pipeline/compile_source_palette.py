#!/usr/bin/env python3
"""Compile a source-derived A3D material palette for an OSM run.

FL-4787 appearance lane.

WHY THIS EXISTS
===============
The Classic CP437 renderer resolves every terrain visual cell as

    mat = visual & 0x00FF                       # render_resolve.cpp:3446
    shd = 0                                     # render_resolve.cpp:3447 (hardcoded)
    elv = 0                                     # render_resolve.cpp:3448 (hardcoded)
    cell = matlib[mat].shade[1][shd]            # render_resolve.cpp:3451

so the stored shade byte and the stored elevation bit are BOTH ignored, and a
terrain cell's entire appearance is one MatCell: ``matlib[mat].shade[1][0]``.

The existing cartographic classifier (``osm_carto_classify.py``) distinguishes
27 semantic roles, but encodes that distinction in the *shade* byte while
collapsing every role onto one of six material ids.  In Classic the shade byte
is discarded, so all 27 roles render as six colours and every one of the 23
buildings renders as the single grey of material 5.  That is the mechanical
cause of "no colour palette that tries to match the building colours".

This compiler fixes it in DATA ONLY: it allocates a distinct material id per
semantic role and per building out of the 250 unused ids (6..255 are unreferenced
by any shipped visual cell), and emits the MatCell recipe for each.  No engine
change, no format change, no new runtime path.

PROVENANCE
==========
Every emitted material carries ``provenance_class`` per the FL-4787 contract:

    measured    - taken from a source fact (OSM ``roof:colour`` / ``building:colour``)
    derived     - computed from a source fact (OSM-Carto reference colour for the
                  role the classifier assigned; building family from OSM tags)
    synthesized - deterministically invented for readability where no source fact
                  exists.  Seeded from the OSM element id so it is reproducible.

Synthesis here is confined to STYLE (colour choice).  It never sets height,
collision, walkability, entrances or any other gameplay truth.

OUTPUT
======
``source_palette.json``::

    {
      "version": 1,
      "provenance": {...},
      "materials": {"<id>": {"key","bg","fg","glyphs","provenance_class","source"}},
      "role_material": {"<role>": <id>},
      "building_material": {"<osm id>": {"roof": <id>, "wall": <id>}},
      "reserved": [0,1,2,3,4,5]
    }
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# --- Classic-renderer facts (see module docstring) -------------------------
CLASSIC_RAMP_INDEX = 1   # matlib[mat].shade[1][shd]
CLASSIC_SHADE_INDEX = 0  # shd is hardcoded to 0

# Material ids 0..5 are the legacy OSM terrain contract and stay untouched so
# that any map or tool still emitting them keeps working.
RESERVED_MATERIAL_IDS = (0, 1, 2, 3, 4, 5)
FIRST_FREE_MATERIAL_ID = 6
LAST_MATERIAL_ID = 255

PROV_MEASURED = "measured"
PROV_DERIVED = "derived"
PROV_SYNTHESIZED = "synthesized"

# CP437 glyph ramp used across OSM maps. FL-4192 found that writing spaces into
# every ramp made large OSM surfaces render as blank texture, so keep real ink.
CANONICAL_RAMP = (32, 46, 58, 35)  # ' ', '.', ':', '#'

# CP437 shade blocks. On a wall cell these dither foreground over background,
# which is how a CP437 facade reads as "wall with windows" without any UV.
GLYPH_LIGHT_SHADE = 176   # 0xB0 - sparse windows
GLYPH_MED_SHADE = 177     # 0xB1 - regular window grid
GLYPH_DARK_SHADE = 178    # 0xB2 - dense glazing / curtain wall
GLYPH_SOLID = 219         # 0xDB - solid block

WALL_RAMP = (GLYPH_MED_SHADE,) * 4
GLAZED_RAMP = (GLYPH_DARK_SHADE,) * 4


# ---------------------------------------------------------------------------
# Role palette
# ---------------------------------------------------------------------------
# Background colours are the OSM-Carto reference colours the classifier already
# matches against (osm_carto_classify.py CARTO_COLORS), so the role palette is
# *derived from the same source fact the classifier used*, not invented here.
# The foreground is a readable ink for that surface.
#
#   role -> (bg, fg, glyph ramp, provenance_class)
ROLE_PALETTE: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, ...], str]] = {
    # --- landcover ---------------------------------------------------------
    "land":             ((252, 248, 218), (228, 222, 190), CANONICAL_RAMP, PROV_DERIVED),
    "railway":          ((108, 105, 99), (146, 142, 132), CANONICAL_RAMP, PROV_SYNTHESIZED),
    "crossing":         ((226, 222, 208), (244, 242, 230), CANONICAL_RAMP, PROV_SYNTHESIZED),
    "water":            ((116, 168, 204), (170, 211, 223), CANONICAL_RAMP, PROV_DERIVED),
    "grass":            ((141, 191, 104), (186, 224, 140), CANONICAL_RAMP, PROV_DERIVED),
    "garden":           ((150, 190, 118), (196, 226, 152), CANONICAL_RAMP, PROV_DERIVED),
    "wood":             (( 84, 138,  72), (126, 180,  98), CANONICAL_RAMP, PROV_DERIVED),
    "hedge":            (( 96, 146,  80), (140, 190, 110), CANONICAL_RAMP, PROV_DERIVED),
    "tree":             (( 72, 124,  62), (132, 190, 100), CANONICAL_RAMP, PROV_DERIVED),
    # --- carriageway: warm dark asphalt, graded by class -------------------
    "road_primary":     (( 96,  92,  90), (150, 145, 140), CANONICAL_RAMP, PROV_DERIVED),
    "road_secondary":   (( 88,  85,  83), (140, 136, 132), CANONICAL_RAMP, PROV_DERIVED),
    "road_tertiary":    (( 82,  80,  78), (132, 128, 125), CANONICAL_RAMP, PROV_DERIVED),
    "road_residential": (( 76,  75,  74), (124, 122, 120), CANONICAL_RAMP, PROV_DERIVED),
    "road_casing":      (( 58,  57,  56), ( 92,  90,  88), CANONICAL_RAMP, PROV_DERIVED),
    "parking":          (( 70,  70,  72), (112, 112, 114), CANONICAL_RAMP, PROV_DERIVED),
    # --- pedestrian surfaces: pale concrete, distinct from carriageway -----
    "footway":          ((198, 186, 170), (230, 220, 205), CANONICAL_RAMP, PROV_DERIVED),
    "pavement_outline": ((178, 168, 154), (210, 200, 186), CANONICAL_RAMP, PROV_DERIVED),
    "pedestrian_area":  ((206, 200, 192), (236, 231, 224), CANONICAL_RAMP, PROV_DERIVED),
    "path":             ((172, 142, 118), (208, 180, 152), CANONICAL_RAMP, PROV_DERIVED),
    "steps":            ((160, 150, 140), (206, 198, 188), CANONICAL_RAMP, PROV_DERIVED),
    "cycleway":         ((138, 156, 178), (186, 204, 224), CANONICAL_RAMP, PROV_DERIVED),
    "sport_court":      ((168, 122,  96), (206, 162, 134), CANONICAL_RAMP, PROV_DERIVED),
    # --- punctuation -------------------------------------------------------
    "building_outline": ((132, 124, 110), (168, 160, 146), WALL_RAMP,      PROV_DERIVED),
    "monument_artwork": ((150, 104,  86), (196, 152, 132), CANONICAL_RAMP, PROV_DERIVED),
    "memorial":         ((134,  96,  82), (180, 142, 126), CANONICAL_RAMP, PROV_DERIVED),
    "bench":            ((150, 118,  82), (194, 162, 124), CANONICAL_RAMP, PROV_SYNTHESIZED),
    "bus_stop":         ((120, 140, 172), (176, 196, 226), CANONICAL_RAMP, PROV_SYNTHESIZED),
    "bicycle_parking":  ((126, 132, 150), (180, 186, 204), CANONICAL_RAMP, PROV_SYNTHESIZED),
    "unclassified":     ((141, 191, 104), (186, 224, 140), CANONICAL_RAMP, PROV_SYNTHESIZED),
    "unknown":          ((141, 191, 104), (186, 224, 140), CANONICAL_RAMP, PROV_SYNTHESIZED),
}


# ---------------------------------------------------------------------------
# Building colour families
# ---------------------------------------------------------------------------
# Deterministic, readable families for a campus. Selection is driven by OSM
# tags where they exist; otherwise seeded from the OSM element id so repeated
# compiles produce identical output.
#
#   name -> (wall_bg, wall_fg/glass, roof_bg, roof_fg)
BUILDING_FAMILIES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    "brick_red":      ((142,  82,  64), (176, 198, 214), (104,  92,  86), (138, 126, 118)),
    "brick_brown":    ((126,  88,  66), (172, 194, 212), ( 98,  88,  80), (132, 122, 112)),
    "concrete_grey":  ((150, 148, 142), (168, 192, 210), (118, 118, 116), (150, 150, 148)),
    "concrete_pale":  ((178, 174, 166), (176, 200, 218), (138, 136, 132), (170, 168, 164)),
    "limestone":      ((186, 178, 158), (170, 194, 212), (146, 140, 126), (178, 172, 158)),
    "glass_tower":    (( 96, 118, 134), (168, 206, 228), ( 84,  96, 106), (124, 140, 152)),
    "metal_panel":    ((122, 128, 134), (180, 202, 220), ( 96, 100, 105), (132, 137, 142)),
    "sandstone":      ((176, 150, 112), (172, 196, 214), (140, 120,  92), (172, 152, 124)),
}
FAMILY_ORDER = sorted(BUILDING_FAMILIES)


def _norm_hex(value: str) -> Optional[tuple[int, int, int]]:
    """Parse an OSM colour value. Returns None when it is not a plain hex RGB."""
    v = value.strip().lower()
    if v.startswith("#"):
        v = v[1:]
    if len(v) == 3 and all(c in "0123456789abcdef" for c in v):
        return (int(v[0] * 2, 16), int(v[1] * 2, 16), int(v[2] * 2, 16))
    if len(v) == 6 and all(c in "0123456789abcdef" for c in v):
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    named = {
        "white": (238, 238, 238), "black": (40, 40, 40), "grey": (140, 140, 140),
        "gray": (140, 140, 140), "red": (150, 70, 60), "brown": (126, 88, 66),
        "silver": (176, 178, 180), "green": (90, 130, 90), "blue": (90, 116, 150),
        "beige": (198, 184, 156), "yellow": (206, 190, 120), "tan": (186, 162, 126),
    }
    return named.get(v)


MANUAL_RGB_GRID = 32


MANUAL_WALL_RGB_GRID = 16


def _srgb_to_lab(rgb) -> tuple[float, float, float]:
    """sRGB (0-255) -> CIELAB (D65), used only to pick the perceptually nearest grid corner."""
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(float(v)) for v in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883
    f = lambda t: t ** (1.0 / 3.0) if t > 216.0 / 24389.0 else (24389.0 / 27.0 * t + 16.0) / 116.0
    fx, fy, fz = f(x), f(y), f(z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def _quantize_manual_rgb(rgb, grid: int = MANUAL_RGB_GRID) -> tuple[int, int, int]:
    """Snap a hand-authored evidence RGB onto the shared Classic grid.

    The Classic material table is fixed at 256 entries, so the hundreds of
    distinct per-building evidence colours cannot each allocate a recipe; the
    authored source keeps its exact evidence RGB.  The snap picks, among the 8
    grid corners around the colour, the one nearest in CIELAB: independent
    per-channel rounding moved channels in opposite directions and turned real
    Numabe greige (208,207,199) and cream (212,203,185) into pink (224,192,192),
    dE ~12.7 (build_reg_62 headed classic).  Walls use grid 16 (see
    MANUAL_WALL_RGB_GRID), roofs grid 32.
    """
    import itertools
    opts = [sorted({min(255, int(c) // grid * grid), min(255, (int(c) // grid + 1) * grid)}) for c in rgb]
    target = _srgb_to_lab(rgb)
    best = min(itertools.product(*opts),
               key=lambda q: sum((p - t) ** 2 for p, t in zip(_srgb_to_lab(q), target)))
    return tuple(int(v) for v in best)


def _seeded_family(osm_id: str) -> str:
    """Pick a colour family deterministically from the OSM element id."""
    h = hashlib.sha256(osm_id.encode("utf-8")).digest()
    return FAMILY_ORDER[h[0] % len(FAMILY_ORDER)]


def _family_for_building(tags: dict[str, str], osm_id: str) -> tuple[str, str, list[str]]:
    """Return (family, provenance_class, reasons) for one building."""
    reasons: list[str] = []
    b = (tags.get("building") or "").lower()
    name = (tags.get("name") or "").lower()

    material = (tags.get("building:material") or "").lower()
    if material:
        reasons.append(f"building:material={material}")
        if "brick" in material:
            return "brick_red", PROV_MEASURED, reasons
        if "glass" in material:
            return "glass_tower", PROV_MEASURED, reasons
        if "metal" in material:
            return "metal_panel", PROV_MEASURED, reasons
        if "concrete" in material:
            return "concrete_grey", PROV_MEASURED, reasons
        if "stone" in material or "limestone" in material:
            return "limestone", PROV_MEASURED, reasons

    # Typed hints from OSM tags. Derived: computed from a source fact.
    if b in ("university", "college", "school"):
        reasons.append(f"building={b}")
        return "brick_red", PROV_DERIVED, reasons
    if b in ("industrial", "service", "shed", "garage", "garages"):
        reasons.append(f"building={b}")
        return "metal_panel", PROV_DERIVED, reasons
    if b in ("roof", "carport"):
        reasons.append(f"building={b}")
        return "concrete_pale", PROV_DERIVED, reasons
    if "library" in name or "center" in name or "centre" in name:
        reasons.append(f"name~{name}")
        return "limestone", PROV_DERIVED, reasons
    if "tower" in name:
        reasons.append(f"name~{name}")
        return "glass_tower", PROV_DERIVED, reasons
    if "substation" in name or "accelerator" in name or "observatory" in name:
        reasons.append(f"name~{name}")
        return "concrete_grey", PROV_DERIVED, reasons

    reasons.append("seeded_from_osm_id")
    return _seeded_family(osm_id), PROV_SYNTHESIZED, reasons


def _levels_glyph(tags: dict[str, str]) -> int:
    """Denser glazing glyph for taller buildings; sparser for low ones."""
    try:
        lv = float(tags.get("building:levels") or tags.get("roof:levels") or 0)
    except ValueError:
        lv = 0.0
    if lv >= 6:
        return GLYPH_DARK_SHADE
    if lv >= 2:
        return GLYPH_MED_SHADE
    return GLYPH_LIGHT_SHADE


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------
class PaletteAllocator:
    def __init__(self) -> None:
        self.next_id = FIRST_FREE_MATERIAL_ID
        self.materials: dict[int, dict] = {}
        # FL-4813: route-scale maps share identical rendering recipes so the
        # fixed 256-entry Classic table does not become a building-count cap.
        self._by_recipe: dict[tuple, int] = {}

    def alloc(self, key: str, bg, fg, glyphs, provenance_class: str, source: str) -> int:
        if len(glyphs) == 1:
            glyphs = tuple(glyphs) * 4
        recipe = (
            tuple(int(value) for value in bg),
            tuple(int(value) for value in fg),
            tuple(int(value) for value in glyphs),
            provenance_class,
            source,
        )
        existing = self._by_recipe.get(recipe)
        if existing is not None:
            return existing
        if self.next_id > LAST_MATERIAL_ID:
            raise RuntimeError(
                f"material palette exhausted at id {LAST_MATERIAL_ID}; "
                f"cannot allocate {key!r}"
            )
        mid = self.next_id
        self.next_id += 1
        self.materials[mid] = {
            "key": key,
            "bg": list(bg),
            "fg": list(fg),
            "glyphs": list(glyphs),
            "provenance_class": provenance_class,
            "source": source,
        }
        self._by_recipe[recipe] = mid
        return mid


def _read_osm_building_tags(osm_path: str) -> dict[str, dict[str, str]]:
    """Return {"way/<id>": {tag: value}} for every way carrying a building tag."""
    out: dict[str, dict[str, str]] = {}
    if not osm_path or not os.path.isfile(osm_path):
        return out
    root = ET.parse(osm_path).getroot()
    for way in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in way.findall("tag") if t.get("k")}
        if "building" not in tags:
            continue
        out[f"way/{way.get('id')}"] = tags
    return out


def _resolve_authored_mesh(record, authored_appearance_path):
    """AUTHORED-MESH-01: the owner's authored_mesh record with "file" made absolute, or None."""
    if not isinstance(record, dict) or not record.get('file'):
        return None
    out = dict(record)
    out['file'] = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(authored_appearance_path)),
                                                record['file']))
    return out


def compile_palette(
    building_specs_path: Optional[str],
    osm_path: Optional[str],
    roles: Optional[list[str]] = None,
    *, locale_context: Optional[dict] = None,
    image_evidence_paths: Optional[list[str]] = None,
    facade_associations_path: Optional[str] = None,
    authored_appearance_path: Optional[str] = None,
    inferred_palette_path: Optional[str] = None,
) -> dict:
    alloc = PaletteAllocator()
    # FL-4814: locale profiles are presentation choices, never recovered geometry.
    profiles = []
    surface_overrides = {}
    if locale_context:
        from pathlib import Path
        from scripts.pipeline.template_resolver import discover_pack_chain
        for pack in discover_pack_chain(
            Path(HERE).parents[1] / 'assets/world_templates', locale_context
        ):
            profiles.extend(pack.get('facade_profiles', []))
            surface_overrides.update(pack.get('surface_palette', {}))
    # Direct facade colour is usable only after the capture ray has been
    # associated with a canonical building.  Unassociated regional samples
    # are not distributed by hash: that made local appearance a lottery.
    facade_associations = {}
    if facade_associations_path:
        with open(facade_associations_path, encoding='utf-8') as stream:
            association_document = json.load(stream)
        if association_document.get('schema') != \
                'y92.facade_evidence_associations.v1':
            raise ValueError('unsupported facade evidence association schema')
        facade_associations = association_document.get('buildings') or {}
    authored_appearance = {}
    if authored_appearance_path and os.path.isfile(authored_appearance_path):
        with open(authored_appearance_path, encoding='utf-8') as stream:
            authored_document = json.load(stream)
        if authored_document.get('schema') != \
                'y92.fl4876.authored_building_appearance.v1':
            raise ValueError('unsupported authored building appearance schema')
        authored_appearance = authored_document.get('buildings') or {}
    # FL-4876 MANUAL-WORLD-01 type palette: for buildings no photo shows lit,
    # a lit wall hand-sampled on a Numabe building of the same PLATEAU usage
    # class (infer_palette.py).  Derived, never manual; it replaces only the
    # id-hash colour family, never a measured or manual colour.
    inferred_palette = {}
    if inferred_palette_path is None and authored_appearance_path:
        inferred_palette_path = os.path.join(
            os.path.dirname(authored_appearance_path), 'inferred_building_palette.json')
    if inferred_palette_path and os.path.isfile(inferred_palette_path):
        with open(inferred_palette_path, encoding='utf-8') as stream:
            inferred_document = json.load(stream)
        if inferred_document.get('schema') != \
                'y92.fl4876.inferred_building_palette.v1':
            raise ValueError('unsupported inferred building palette schema')
        inferred_palette = inferred_document.get('buildings') or {}

    # --- roles ------------------------------------------------------------
    role_material: dict[str, int] = {}
    role_names = roles if roles is not None else sorted(ROLE_PALETTE)
    for role in role_names:
        entry = ROLE_PALETTE.get(role)
        if entry is None:
            entry = ROLE_PALETTE["unknown"]
            src = f"osm_carto_role:{role}:fallback"
        else:
            src = f"osm_carto_role:{role}"
        bg, fg, glyphs, prov = entry
        if role in surface_overrides:
            override = surface_overrides[role]
            bg, fg = tuple(override['bg']), tuple(override['fg'])
            prov, src = PROV_SYNTHESIZED, 'locale surface art direction:'+role
        role_material[role] = alloc.alloc(f"role:{role}", bg, fg, glyphs, prov, src)

    # --- buildings --------------------------------------------------------
    building_material: dict[str, dict] = {}
    building_material_by_feature: dict[str, dict] = {}
    osm_tags = _read_osm_building_tags(osm_path) if osm_path else {}

    specs: list[dict] = []
    if building_specs_path and os.path.isfile(building_specs_path):
        with open(building_specs_path, encoding="utf-8") as fh:
            specs = json.load(fh)

    for spec in specs:
        osm_id = spec.get("source") or spec.get("inst_name") or ""
        if not osm_id:
            continue
        tags = osm_tags.get(osm_id, {})
        family, prov, reasons = _family_for_building(tags, osm_id)
        wall_bg, glass_fg, roof_bg, roof_fg = BUILDING_FAMILIES[family]

        # An explicit OSM roof:colour outranks the family: it is a source fact.
        roof_prov = prov
        roof_src = f"family:{family}"
        raw_roof = tags.get("roof:colour") or tags.get("roof:color")
        if raw_roof:
            parsed = _norm_hex(raw_roof)
            if parsed:
                roof_bg = parsed
                roof_fg = tuple(min(255, int(c * 1.25) + 12) for c in parsed)
                roof_prov = PROV_MEASURED
                roof_src = f"osm:roof:colour={raw_roof}"
                reasons.append(roof_src)

        raw_wall = tags.get("building:colour") or tags.get("building:color")
        wall_prov = prov
        wall_src = f"family:{family}"
        if raw_wall:
            parsed = _norm_hex(raw_wall)
            if parsed:
                wall_bg = parsed
                wall_prov = PROV_MEASURED
                wall_src = f"osm:building:colour={raw_wall}"
                reasons.append(wall_src)

        feature_id = str(spec.get('feature_id') or '')
        direct_facade = facade_associations.get(feature_id)
        if direct_facade and not (raw_wall and _norm_hex(raw_wall)):
            wall_bg = tuple(int(value) for value in direct_facade['wall_rgb'])
            wall_prov = PROV_MEASURED
            wall_src = 'imagery:direct_canonical_building_association'
            reasons.append(wall_src)
        manual = authored_appearance.get(feature_id)
        inferred = inferred_palette.get(feature_id)
        if (inferred and inferred.get('wall_rgb') is not None
                and not direct_facade and not (raw_wall and _norm_hex(raw_wall))
                and not (manual and manual.get('wall_rgb') is not None)):
            # The colour IS a hand sample from another Numabe building, so it
            # shares that sample's rendering recipe (the fixed 256-entry
            # material table must not hold the same colour twice); the
            # building's own record below says the assignment is inferred.
            wall_bg = _quantize_manual_rgb(inferred['wall_rgb'], MANUAL_WALL_RGB_GRID)
            wall_prov = PROV_MEASURED
            wall_src = 'manual:authored_building_appearance'
            # tier is hand_judged (hand target -> nearest lit sample), type_palette or district_palette
            reasons.append('inferred:%s:%s' % (inferred.get('tier', 'type_palette'), inferred.get('source_building')))
        else:
            inferred = None
        if manual:
            if manual.get('wall_rgb') is not None:
                wall_bg = _quantize_manual_rgb(manual['wall_rgb'], MANUAL_WALL_RGB_GRID)
                wall_prov = PROV_MEASURED
                wall_src = 'manual:authored_building_appearance'
                reasons.append(wall_src)
            if manual.get('roof_rgb') is not None:
                roof_bg = _quantize_manual_rgb(manual['roof_rgb'])
                roof_fg = tuple(min(255, int(c * 1.25) + 12) for c in roof_bg)
                roof_prov = PROV_MEASURED
                roof_src = 'manual:authored_building_appearance'
                reasons.append(roof_src)

        if wall_src == 'manual:authored_building_appearance':
            # A hand-sampled (or type-palette) wall colour must not inherit the
            # window ink of an id-hash colour family: that multiplied each sampled
            # colour by eight unrelated recipes and exhausted the 256-entry table.
            # Glazing is a darker tone of the building's own paint.
            glass_fg = tuple(max(0, int(c * 0.45)) for c in wall_bg)
        name = spec.get("inst_name") or osm_id
        roof_id = alloc.alloc(
            f"building_roof:{roof_src}", roof_bg, roof_fg, CANONICAL_RAMP, roof_prov, roof_src
        )
        wall_glyph = _levels_glyph(tags)
        wall_id = alloc.alloc(
            f"building_wall:{wall_src}:glyph-{wall_glyph}",
            wall_bg, glass_fg, (wall_glyph,), wall_prov, wall_src,
        )
        building_material[osm_id] = {
            "name": name,
            "family": family,
            "roof": roof_id,
            "wall": wall_id,
            "provenance_class": prov,
            "reasons": reasons,
        }
        if inferred:
            building_material[osm_id]["wall_assignment"] = {
                "tier": "inferred:" + str(inferred.get("tier")),
                "provenance_class": PROV_DERIVED,
                "basis": inferred.get("basis"),
                "source_building": inferred.get("source_building"),
            }
        if profiles:
            typed = [p for p in profiles if tags.get('building') in p.get('building_types', [])]
            choices = typed or [p for p in profiles if not p.get('building_types')]
            seed = int.from_bytes(hashlib.sha256(osm_id.encode()).digest()[:4], 'big')
            profile = dict(choices[seed % len(choices)])
            profile['provenance_class'] = PROV_SYNTHESIZED
            profile['placement'] = 'parent footprint; synthesized facade layout'
            profile['building_family'] = (spec.get('building_fallback') or {}).get('family', 'unclassified')
            profile['source_observations'] = []
            if direct_facade:
                profile['wall_rgb'] = list(direct_facade['wall_rgb'])
                profile['source_observations'] = list(
                    direct_facade['source_observations'])
                profile['source_digests'] = list(
                    direct_facade['source_digests'])
                profile['colour_method'] = direct_facade['colour_method']
            elif inferred:
                profile['wall_rgb'] = [int(v) for v in inferred['wall_rgb']]
                if inferred.get('trim_rgb') is not None:
                    profile['trim_rgb'] = [int(v) for v in inferred['trim_rgb']]
                profile['colour_method'] = 'inferred: Numabe type palette (%s)' % inferred.get('basis')
                profile['provenance_class'] = PROV_DERIVED
            else:
                profile['colour_method'] = 'locale art-direction fallback'
            if raw_wall and _norm_hex(raw_wall):
                profile['wall_rgb'] = list(_norm_hex(raw_wall))
                profile['colour_method'] = wall_src
                profile['source_observations'] = []
                profile.pop('source_digest', None)
            if raw_roof and _norm_hex(raw_roof):
                profile['roof_rgb'] = list(_norm_hex(raw_roof))
            if manual:
                if manual.get('wall_rgb') is not None:
                    profile['wall_rgb'] = [int(v) for v in manual['wall_rgb']]
                if manual.get('trim_rgb') is not None:
                    profile['trim_rgb'] = [int(v) for v in manual['trim_rgb']]
                if manual.get('roof_rgb') is not None:
                    profile['roof_rgb'] = [int(v) for v in manual['roof_rgb']]
                profile['colour_method'] = 'manual authored panorama assessment'
                profile['provenance_class'] = PROV_MEASURED
                profile['source_observations'] = sorted({
                    str(e['capture_id']) for e in manual.get('evidence', [])
                    if e.get('capture_id')
                })
                profile.pop('source_digest', None)
                profile.pop('source_digests', None)
            building_material[osm_id]['facade_profile'] = profile
        if manual:
            building_material[osm_id]['authored_appearance'] = {
                'what': manual.get('what'),
                'ground_floor': manual.get('ground_floor'),
                'features': list(manual.get('features') or []),
                # FL-4876: hand-authored balconies / railings / grilles the bake lowers as presentation meshes
                'attachments': list(manual.get('attachments') or []),
                'evidence_captures': sorted({
                    str(e['capture_id']) for e in manual.get('evidence', [])
                    if e.get('capture_id')
                }),
            }
            # FL-4876 AUTHORED-MESH-01: a hand-authored mesh replaces this building's terrain box and roof field
            # in the building bake; "file" is resolved against the owner file's directory. Other buildings keep
            # their palette bytes unchanged (no key).
            _authored_mesh = _resolve_authored_mesh(manual.get('authored_mesh'), authored_appearance_path)
            if _authored_mesh:
                building_material[osm_id]['authored_appearance']['authored_mesh'] = _authored_mesh
        if feature_id:
            building_material_by_feature[feature_id] = \
                building_material[osm_id]

    return {
        "version": 1,
        "provenance": {
            "compiler": "scripts/pipeline/compile_source_palette.py",
            "renderer_contract": {
                "note": "Classic CP437 resolves terrain as matlib[mat].shade[1][0]; "
                        "the stored shade byte and elevation bit are ignored.",
                "evidence": "engine/render/render_resolve.cpp:3446-3452",
                "ramp_index": CLASSIC_RAMP_INDEX,
                "shade_index": CLASSIC_SHADE_INDEX,
            },
            "role_colour_source": "OSM-Carto reference colours matched by "
                                  "scripts/pipeline/osm_carto_classify.py",
            "building_tag_source": os.path.basename(osm_path) if osm_path else None,
            "reserved_ids": list(RESERVED_MATERIAL_IDS),
        },
        "materials": {str(k): v for k, v in sorted(alloc.materials.items())},
        "role_material": role_material,
        "building_material": building_material,
        "building_material_by_feature": building_material_by_feature,
        "next_free_id": alloc.next_id,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--building-specs", help="building_instances.json")
    ap.add_argument("--osm", help="raw .osm input used for the run (tag source)")
    ap.add_argument("--roles", help="carto_roles.json from the run (role id -> name)")
    ap.add_argument("--facade-associations",
                    help="canonical camera-ray facade associations")
    ap.add_argument("--authored-appearance",
                    help="manual per-building appearance decisions")
    ap.add_argument("--out", required=True, help="output source_palette.json")
    args = ap.parse_args(argv)

    roles = None
    if args.roles and os.path.isfile(args.roles):
        with open(args.roles, encoding="utf-8") as fh:
            roles = sorted(set(json.load(fh).values()))

    palette = compile_palette(
        args.building_specs, args.osm, roles,
        facade_associations_path=args.facade_associations,
        authored_appearance_path=args.authored_appearance)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(palette, fh, indent=2, sort_keys=True)

    n_mat = len(palette["materials"])
    n_bld = len(palette["building_material"])
    prov = {}
    for m in palette["materials"].values():
        prov[m["provenance_class"]] = prov.get(m["provenance_class"], 0) + 1
    print(f"[palette] materials={n_mat} roles={len(palette['role_material'])} "
          f"buildings={n_bld} next_free_id={palette['next_free_id']}")
    print(f"[palette] provenance={prov}")
    print(f"[palette] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
