#!/usr/bin/env python3
"""FL-4359: bake native terminal advances; never fit a glyph's ink bounding box."""
from __future__ import annotations

import hashlib
import json
import argparse
import ast
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "godot_project/assets/glyphs/terminal"
FONT = ROOT / "assets/fonts/unifont-17.0.04.otf"
CONTROLS = "\0☺☻♥♦♣♠•◘○◙♂♀♪♫☼►◄↕‼¶§▬↨↑↓→←∟↔▲▼"
VOCABULARY = ROOT / "godot_project/render/ascii/glyphs/authored_glyph_vocabulary.glslinc"
COMBINATIONS = ROOT / "assets/glyphs/authored/glyph_combinations.v1.json"
FAMILIES = ROOT / "docs/research/ascii/glyph_audit/saved_families.jsonl"
# The archived implementation owns the row generators used by L1/L2.  The
# short projection at src/lateletter/garden/plants.py intentionally contains
# only the L0 symbol map and is not a stamp source.
PLANTS = ROOT / "articles/2026-09-07-lateletter-text-art-media/archive/legacy-repo-7b9389d/src/lateletter/garden/plants.py"
PLANT_SYMBOLS = ROOT / "articles/2026-09-07-lateletter-text-art-media/src/lateletter/garden/plants.py"
PLANT_STAMPS = ROOT / "assets/glyphs/authored/plant_stamps.v1.json"


def glyph_scalars():
    scalars = {i: ord(CONTROLS[i] if i < 32 else "⌂" if i == 127
                      else bytes([i]).decode("cp437")) for i in range(256)}
    scalars[0] = 32
    for pack in ("material_additive_v1", "material_morphology_v2"):
        path = ROOT / f"assets/glyphs/fixtures/extended_glyph_{pack}.json"
        for entry in json.loads(path.read_text())["entries"]:
            if entry["glyph_id"] < 1876 or (pack == "material_morphology_v2" and entry["glyph_id"] == 1880):
                scalars[entry["glyph_id"]] = entry["unicode_scalar"]
    return scalars


def _combination_mirror_chars(combinations):
    """Return the reviewed character-level horizontal mirror lookup.

    The combination JSON is the owner of the mirror relation.  Plant rows use
    the same relation for slash, bracket, and parenthesis members; characters
    absent from that reviewed lookup remain self-symmetric.
    """
    mirrored = {}
    for combo in combinations:
        cols = int(combo["cols"])
        source = {(int(c["col"]), int(c["row"])): c
                  for c in combo["cells"]}
        target = {(int(c["col"]), int(c["row"])): c
                  for c in combo["mirror"]["cells"]}
        for (col, row), cell in source.items():
            partner = target.get((cols - 1 - col, row))
            if partner is not None:
                mirrored.setdefault(cell["char"], partner["char"])
    return mirrored


def _validate_plant_stamps(data, combinations):
    if data.get("schema") != "fl4512.authored_plant_stamps.v1":
        raise ValueError("unsupported authored plant stamp schema")
    if data.get("cell_size") != [16, 32]:
        raise ValueError("authored plant stamps must target native 16x32 cells")
    if data.get("l1", {}).get("dimensions") != [3, 5]:
        raise ValueError("L1 plant stamps must be 3x5")
    if data.get("l2", {}).get("dimensions") != [5, 2]:
        raise ValueError("L2 plant heads must be 5x2")
    species = data["l1"]["species"]
    if len(species) != 9:
        raise ValueError("expected three rooted grass variants, fern, and five flowers")
    if [s["id"] for s in species[:3]] != ["rooted_grass_0", "rooted_grass_1", "rooted_grass_2"]:
        raise ValueError("rooted grass variants must occupy species 0..2")
    if species[3]["id"] != "fern" or any(s["kind"] != "flower" for s in species[4:]):
        raise ValueError("species 3 must be fern and species 4..8 must be flowers")
    combo_names = {str(c["id"]) for c in combinations}
    for head in data["l2"]["species"]:
        if head.get("contour_combo") not in combo_names:
            raise ValueError(f"unknown reviewed L2 contour combo: {head.get('contour_combo')}")
    role_names = set(data.get("roles", {}))
    for plant in species:
        for row in plant.get("rows", []):
            if not 0 <= int(row["row"]) <= 4:
                raise ValueError(f"L1 row out of bounds for {plant['id']}")
            for cell in row.get("cells", []):
                if not -1 <= int(cell["col"]) <= 1:
                    raise ValueError(f"L1 member out of bounds for {plant['id']}")
                if cell.get("role") not in role_names:
                    raise ValueError(f"unknown L1 role {cell.get('role')!r}")
                chars = cell.get("chars", [cell.get("char")])
                if any(not isinstance(ch, str) or len(ch) != 1 for ch in chars):
                    raise ValueError(f"L1 cells must contain single source characters for {plant['id']}")


def _emit_plant_stamp_functions(lines, ids, stamps, combinations):
    """Emit L1/L2 plant lookups from the reviewed stamp data.

    The generated include keeps the old L0 APIs above this section.  L1 rows
    are source cells, with only their phase-indexed _grass tip list varying.
    L2 flower contours call the already-generated Stone Story combination
    functions, so no second hardcoded motif table is introduced.
    """
    roles = stamps["roles"]
    role_id = {name: int(value) for name, value in roles.items()}
    combos = {str(c["id"]): c for c in combinations}
    mirror_chars = _combination_mirror_chars(combinations)
    l1_species = stamps["l1"]["species"]
    l2_by_species = {str(s["species"]): s for s in stamps["l2"]["species"]}

    def glyph_id(char):
        cp = ord(char)
        if cp not in ids:
            raise ValueError(f"authored plant source character unavailable in native atlas: {char!r}")
        return ids[cp]

    grass_tip_chars = next(
        cell["chars"]
        for plant in l1_species
        for row in plant.get("rows", [])
        for cell in row.get("cells", [])
        if "chars" in cell
    )
    grass_ids = [glyph_id(ch) for ch in grass_tip_chars]
    lines += [
        "// FL-4512: authored Late Letter L1 plant stamps; source rows are data-driven.",
        "// member.x is -1..1 for L1 and member.y is 0 (attachment)..4 (tip).",
        f"const uint AUTHORED_PLANT_SPECIES_COUNT = {len(l1_species)}u;",
        f"const uint AUTHORED_PLANT_GRASS_VARIANT_COUNT = {sum(s['kind'] == 'grass' for s in l1_species)}u;",
        "const ivec2 AUTHORED_PLANT_L1_SIZE = ivec2(3, 5);",
        "const ivec2 AUTHORED_PLANT_L2_HEAD_SIZE = ivec2(5, 2);",
    ]
    for species_index, plant in enumerate(l1_species):
        occupied = [cell for row in plant.get("rows", []) for cell in row.get("cells", [])]
        min_col = min(int(cell["col"]) for cell in occupied)
        max_col = max(int(cell["col"]) for cell in occupied)
        max_row = max(int(row["row"]) for row in plant.get("rows", []))
        symbol = "".join(ch if ch.isalnum() else "_" for ch in plant["id"]).upper()
        lines.append(f"const ivec2 AUTHORED_PLANT_{symbol}_SIZE = ivec2({max_col - min_col + 1}, {max_row + 1});")
    lines += [
        "ivec2 authored_plant_stamp_size(uint species, uint detail) {",
        "    species %= AUTHORED_PLANT_SPECIES_COUNT;",
        "    if (detail != 0u && species >= 4u) return AUTHORED_PLANT_L2_HEAD_SIZE;",
    ]
    for species_index, plant in enumerate(l1_species):
        symbol = "".join(ch if ch.isalnum() else "_" for ch in plant["id"]).upper()
        lines.append(f"    if (species == {species_index}u) return AUTHORED_PLANT_{symbol}_SIZE;")
    lines += [
        "    return AUTHORED_PLANT_L1_SIZE;",
        "}",
        "uint authored_plant_stamp_oriented(uint species, ivec2 member, uint phase, uint detail, bool mirrored, out uint colour_role) {",
        "    colour_role = 0u;",
        "    species %= AUTHORED_PLANT_SPECIES_COUNT;",
        "    const uint grass_tips[%d] = uint[%d](%s);" % (
            len(grass_ids), len(grass_ids), ", ".join(f"{x}u" for x in grass_ids)),
    ]
    for species_index, plant in enumerate(l1_species):
        lines.append(f"    if (species == {species_index}u && detail == 0u) {{")
        for row in plant.get("rows", []):
            row_index = int(row["row"])
            for cell in row.get("cells", []):
                col = int(cell["col"])
                role = role_id[cell["role"]]
                # Direct source rows are mirrored by selecting the measured
                # character partner, while phase-indexed grass tips retain
                # their authored source order.
                lines += [
                    f"        if (member == ivec2({col}, {row_index})) {{",
                    f"            colour_role = {role}u;",
                ]
                if "chars" in cell:
                    offset = int(plant.get("phase_offset", 0))
                    lines.append(f"            return grass_tips[(phase + {offset}u) % {len(grass_ids)}u];")
                else:
                    normal = glyph_id(cell["char"])
                    mirrored_char = mirror_chars.get(cell["char"], cell["char"])
                    mirrored_id = glyph_id(mirrored_char)
                    if mirrored_id == normal:
                        lines.append(f"            return {normal}u;")
                    else:
                        lines.append(f"            return mirrored ? {mirrored_id}u : {normal}u;")
                lines.append("        }")
        lines.append("    }")

    # L2 head dispatch is generated below in the same function. It uses L2
    # coordinates -2..2, while L1 calls remain the root seam.
    lines += [
        "    if (detail != 0u && species >= 4u) {",
        "        return authored_plant_l2_head(species, member, mirrored, colour_role);",
        "    }",
        "    return 0u;",
        "}",
    ]

    # L2 is emitted after the oriented function declaration.  A prototype is
    # needed because the oriented function dispatches to this helper.
    # GLSL accepts a declaration before the definition and keeps the call site
    # readable in generated output.
    insert_at = next(i for i, line in enumerate(lines)
                     if line == "uint authored_plant_stamp_oriented(uint species, ivec2 member, uint phase, uint detail, bool mirrored, out uint colour_role) {")
    lines.insert(insert_at, "uint authored_plant_l2_head(uint species, ivec2 member, bool mirrored, out uint colour_role);")
    lines += [
        "uint authored_plant_l2_head(uint species, ivec2 member, bool mirrored, out uint colour_role) {",
        "    colour_role = 0u;",
        "    species %= AUTHORED_PLANT_SPECIES_COUNT;",
    ]
    for species_index, plant in enumerate(l1_species[4:], start=4):
        head = l2_by_species[plant.get("source_species", plant["id"])]
        combo_name = str(head["contour_combo"])
        combo = combos[combo_name]
        combo_cols = int(combo["cols"])
        combo_row = next(r for r in head["rows"] if "combo_offset" in r)
        combo_offset = int(combo_row["combo_offset"])
        lines.append(f"    if (species == {species_index}u) {{")
        lines += [
            f"        if (member.y == {int(combo_row['row'])} && member.x >= {combo_offset} && member.x < {combo_offset + combo_cols}) {{",
            f"            colour_role = {role_id[combo_row['role']]}u;",
            f"            return authored_{combo_name}(ivec2(member.x - {combo_offset}, member.y - {int(combo_row['row'])}), mirrored);",
            "        }",
        ]
        for row in head["rows"]:
            if "cells" not in row:
                continue
            row_index = int(row["row"])
            for cell in row["cells"]:
                col = int(cell["col"])
                normal = glyph_id(cell["char"])
                mirrored_id = glyph_id(mirror_chars.get(cell["char"], cell["char"]))
                lines += [
                    f"        if (member == ivec2({col}, {row_index})) {{",
                    f"            colour_role = {role_id[cell['role']]}u;",
                ]
                if mirrored_id == normal:
                    lines.append(f"            return {normal}u;")
                else:
                    lines.append(f"            return mirrored ? {mirrored_id}u : {normal}u;")
                lines.append("        }")
        lines.append("    }")
    lines += ["    return 0u;", "}"]
    lines += [
        "// Root consumer seam: front chooses source phase; leans_left selects the reviewed mirror.",
        "uint authored_plant_stamp(uint species, ivec2 member, float front, bool leans_left, bool flower, out uint colour_role) {",
        "    uint phase = uint(round(clamp(front, 0.0, 1.0) * 3.0));",
        "    uint detail = 0u;",
        "    // The species data owns flower identity; this flag is retained in the ABI",
        "    // for callers that already classify a root before asking for its stamp.",
        "    if (flower && species < 4u) { colour_role = 0u; return 0u; }",
        "    return authored_plant_stamp_oriented(species, member, phase, detail, leans_left, colour_role);",
        "}",
        "uint authored_plant_stamp(uint species, ivec2 member, uint phase, uint detail) {",
        "    uint colour_role = 0u;",
        "    bool mirrored = (phase & 4u) != 0u;",
        "    return authored_plant_stamp_oriented(species, member, phase & 3u, detail, mirrored, colour_role);",
        "}",
    ]


def vocabulary_source():
    """FL-4512: compile reviewed codepoints, never confuse Unicode with atlas IDs.

    Keep spatial offsets and family order. Unavailable glyphs decline the entire
    combination; silently replacing an acute, overline or mirror destroys its
    authored construction. The native-atlas compiler owns this mapping.
    """
    scalars = glyph_scalars()
    widths = json.loads((OUT / "manifest.json").read_text())["widths"]
    ids = {}
    for gid, cp in sorted(scalars.items()):
        if widths[gid] > 0:
            ids.setdefault(cp, gid)
    lines = ["// FL-4512: generated by compile_native_terminal_atlas.py --vocabulary-only.",
             "// Source order, native glyph IDs and authored offsets; do not hand edit."]
    for path in (COMBINATIONS, FAMILIES, PLANT_SYMBOLS, PLANTS, PLANT_STAMPS, OUT / "manifest.json"):
        lines.append(f"// {path.relative_to(ROOT)} sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")
    families = [json.loads(row) for row in FAMILIES.read_text().splitlines() if row.strip()]
    # FL-4512/FL-4359 (operator 2026-09-09, "use combo / cycle glyphs from late
    # letter and morph viewer"): every reviewed foliage same-cell cycle is a
    # plant family, not only the three tagged foliage_grass_wind. A family saved
    # twice (the viewer normalised one ramp save) is admitted once, first row.
    admitted_hints = ("foliage_grass_wind", "foliage_wind_same_cell")
    seen_chars: set[str] = set()
    families = [f for f in families
                if f.get("role_hint") in admitted_hints
                and not (f["chars"] in seen_chars or seen_chars.add(f["chars"]))]
    if not families:
        raise ValueError("no reviewed foliage families")
    lines += [f"const uint AUTHORED_PLANT_FAMILY_COUNT = {len(families)}u;",
              "uint authored_plant_cycle(uint family, float front) {",
              "    family %= AUTHORED_PLANT_FAMILY_COUNT;"]
    for index, family in enumerate(families):
        missing = [cp for cp in family["cps"] if cp not in ids]
        if missing:
            raise ValueError(f"reviewed plant family unavailable in native atlas: {missing}")
        glyphs = [ids[cp] for cp in family["cps"]]
        n = len(glyphs)
        lines += [f"    // {family['chars']} -- {family['source']}",
                  f"    if (family == {index}u) {{",
                  f"        const uint frames[{n}] = uint[{n}]({', '.join(str(g) + 'u' for g in glyphs)});",
                  f"        return frames[uint(round(clamp(front, 0.0, 1.0) * {n-1}.0))];", "    }"]
    lines += ["    return 0u;", "}"]
    symbols = next(ast.literal_eval(n.value) for n in ast.parse(PLANT_SYMBOLS.read_text()).body
                   if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name)
                       and t.id == "_PLANT_SYMBOLS" for t in n.targets))
    # The approved L0 land-flower roles. Rose's U+273F is not in this atlas;
    # do not substitute an unrelated glyph or reuse the aquatic lily role.
    flower_species = ("hydrangea", "lavender", "rosemary", "tulip", "sunflower")
    flower_ids = [ids[ord(symbols[s])] for s in flower_species]
    lines += [f"const uint AUTHORED_MEADOW_ATTACHMENT = {ids[ord(symbols['meadow_grass'])]}u;",
              "// Late Letter L0 species: " + ", ".join(flower_species),
              "uint authored_flower_symbol(uint species) {",
              f"    const uint glyphs[5] = uint[5]({', '.join(str(g) + 'u' for g in flower_ids)});",
              "    return glyphs[species % 5u];", "}"]
    # Late Letter cycles and combos (archived operator text art). The stem sway
    # is flower-animations.txt "WIND SWAY: Small Flower": | (center), \ (right),
    # / (left), loop 1-2-3-2-1. The grass tip is plants.py _grass
    # `tip = rng.choice(['/', '\\', '`', "'"])`; the fern is plants.py _fern:
    # `*` tip, `|` stem, side leaves `*` on odd rows and `,` on even rows. These
    # are CP437 ids, present in every page-0 atlas.
    stem = {"center": ids[ord("|")], "right": ids[ord("\\")], "left": ids[ord("/")]}
    tips = [ids[ord(c)] for c in ("/", "\\", "`", "'")]
    lines += ["// Late Letter flower-animations.txt small-flower stem sway: | \\ /",
              "uint authored_lateletter_stem_cycle(float front, bool leans_left) {",
              f"    if (front < 0.34) return {stem['center']}u;",
              f"    return leans_left ? {stem['left']}u : {stem['right']}u;", "}",
              "// Late Letter plants.py _grass tip choice: / \\ ` '",
              "uint authored_lateletter_grass_tip(uint seed) {",
              f"    const uint glyphs[4] = uint[4]({', '.join(str(g) + 'u' for g in tips)});",
              "    return glyphs[seed % 4u];", "}",
              "// Late Letter plants.py _fern: a three-column combo, member -1..1.",
              "uint authored_lateletter_fern_row(int member, bool odd_row) {",
              f"    if (member == 0) return {ids[ord('|')]}u;",
              f"    if (member < -1 || member > 1) return 0u;",
              f"    return odd_row ? {ids[ord('*')]}u : {ids[ord(',')]}u;", "}",
              f"const uint AUTHORED_LATELETTER_FERN_TIP = {ids[ord('*')]}u;"]
    combinations = json.loads(COMBINATIONS.read_text())["combinations"]
    for combo in combinations:
        symbol = "AUTHORED_" + combo["id"].upper()
        lines += [f"const ivec2 {symbol}_SIZE = ivec2({combo['cols']}, {combo['rows']});",
                  f"uint authored_{combo['id']}(ivec2 member, bool mirrored) {{"]
        cells = combo["cells"] + combo["mirror"]["cells"]
        missing = sorted({c["cp"] for c in cells if c["cp"] not in ids})
        if missing:
            lines += [f"    // Whole motif declined: native atlas lacks {missing}.", "    return 0u;", "}"]
            continue
        for mirror, members in ((False, combo["cells"]), (True, combo["mirror"]["cells"])):
            lines.append(f"    if ({'mirrored' if mirror else '!mirrored'}) {{")
            for cell in members:
                lines.append(f"        if (member == ivec2({cell['col']}, {cell['row']})) return {ids[cell['cp']]}u;")
            lines.append("    }")
        lines += ["    return 0u; // Authored negative space or out of bounds.", "}"]
    plant_stamps = json.loads(PLANT_STAMPS.read_text())
    _validate_plant_stamps(plant_stamps, combinations)
    _emit_plant_stamp_functions(lines, ids, plant_stamps, combinations)
    return "\n".join(lines) + "\n"


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(FONT), 32, layout_engine=ImageFont.Layout.BASIC)
    scalars = glyph_scalars()
    widths = [0] * 1881
    pages = []
    declined = []
    for page, (lo, hi) in enumerate(((0, 255), (512, 671), (672, 1880))):
        image = Image.new("RGBA", (16 * 32, ((hi - lo + 16) // 16) * 32))
        draw = ImageDraw.Draw(image)
        for glyph in range(lo, hi + 1):
            if glyph not in scalars:
                continue  # Explicit absence IDs own no native bitmap.
            char = chr(scalars[glyph])
            advance = font.getlength(char)
            if advance == 0:
                declined.append({"glyph_id": glyph, "unicode_scalar": scalars[glyph],
                                 "reason": "combining_mark_requires_grapheme_cluster"})
                continue
            left, top, right, bottom = font.getbbox(char)
            if left < 0 or top < 0 or right > advance or bottom > 32:
                raise ValueError(f"ink outside native advance: glyph {glyph}")
            if advance not in (16, 32):
                raise ValueError(f"unsupported native advance: glyph {glyph}: {advance}")
            widths[glyph] = int(advance / 16)
            # Shared font origin/baseline; no per-glyph centering, cropping or fitting.
            x, y = ((glyph - lo) % 16) * 32, ((glyph - lo) // 16) * 32
            draw.text((x, y), char, font=font, fill=(255, 255, 255, 255))
        path = OUT / f"page_{page}.png"
        image.save(path)
        # FL-4822: two digests per page, for the same reason the audio lane keeps
        # two. "sha256" is the loose-file digest and it can only be checked in a
        # source checkout; an exported PCK strips the source PNG and ships only
        # the imported .ctex, so FileAccess cannot read the file at all.
        # "decoded_rgba8_sha256" is the digest of the DECODED RGBA8 pixels, which
        # is what the runtime actually uploads, and it is checkable identically in
        # a checkout and inside a pack. It holds only while the page's .import
        # keeps process/fix_alpha_border=false and compress/mode=0; either one
        # changed makes the imported decode diverge from this value on purpose.
        pages.append({"page": page, "path": "res://" + str(path.relative_to(ROOT / "godot_project")),
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "decoded_rgba8_sha256": hashlib.sha256(
                          image.convert("RGBA").tobytes()).hexdigest(),
                      "glyph_min": lo, "glyph_max": hi, "size": list(image.size),
                      "slot_size": [32, 32], "coverage_channel": "alpha"})
    metadata = {"schema": "fl4359.native_terminal_atlas.v1", "cell_size": [16, 32],
                "font_path": str(FONT.relative_to(ROOT)),
                "font_sha256": hashlib.sha256(FONT.read_bytes()).hexdigest(),
                "widths": widths, "pages": pages, "declined": declined,
                "layout": "left-to-right native advances; full-width glyph occupies two tall cells"}
    (OUT / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print("native terminal atlas", {w: widths.count(w) for w in (0, 1, 2)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vocabulary-only", action="store_true")
    parser.add_argument("--check", action="store_true", help="Check the compiled vocabulary without writing")
    args = parser.parse_args()
    if not args.vocabulary_only and not args.check:
        build()
    source = vocabulary_source()
    if args.check:
        if not VOCABULARY.exists() or VOCABULARY.read_text() != source:
            raise SystemExit("authored glyph vocabulary is stale")
    else:
        VOCABULARY.write_text(source)
