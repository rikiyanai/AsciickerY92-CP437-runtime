#!/usr/bin/env python3
"""Generate FL-4131 material morphology v2 review tables and receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
AOA_PATH = REPO_ROOT / "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json"
V2_CATALOG_PATH = REPO_ROOT / "assets/glyphs/generated/material.morphology.v2.shape_catalog.json"
V2_AOA_PATH = REPO_ROOT / "assets/glyphs/atlases/material.morphology.v2.atlas_of_atlases.json"
WATER_RECEIPTS_PATH = REPO_ROOT / "assets/glyphs/generated/fl4208_water_role_scoring.jsonl"
OUT_DIR = REPO_ROOT / "assets/glyphs/generated"

INVENTORY_OUT = OUT_DIR / "material.morphology.v2.candidate_inventory.jsonl"
RECEIPTS_OUT = OUT_DIR / "material.morphology.v2.shape_receipts.jsonl"
TABLES_OUT = OUT_DIR / "material.morphology.v2.profile_tables.json"
REJECTIONS_OUT = OUT_DIR / "material.morphology.v2.rejections.jsonl"
UI_SCHEMA_OUT = OUT_DIR / "material.morphology.v2.ui_receipt_schema.json"

DIRECTIONS = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
    "NONE",
]
DENSITIES = ["D0", "D1", "D2", "D3"]
PROFILES = ["GRASS", "WATER", "ROCK", "DIRT", "SAND", "SNOW", "MUD", "GRAVEL"]

SOURCE_BLOCKS = {
    "hiragana": "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん",
    "katakana": "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン",
    "cjk_strokes": "㇀㇁㇂㇃㇄㇅㇆㇇㇈㇉㇊㇋㇌㇍㇎㇏",
    "radicals": "一丨丶丿乙亅二亠人儿入八冂冖冫几凵刀力匚匸十卜厂厶口囗土士夕大女子宀寸小尢尸山巛工己巾干幺广廴廾弋弓彐彡彳",
    "kangxi_radicals": "⼀⼁⼂⼃⼄⼅⼆⼇⼈⼉⼊⼋⼌⼍⼎⼏⼐⼒⼕⼖⼗⼘⼙⼚⼛⼝⼞⼟⼠⼡⼤⼥⼦⼧⼨⼩⼪⼫⼭⼮⼯⼰⼱⼲⼳⼴⼵⼶⼷⼸⼹⼺⼻",
    "kana_marks": "ゝゞーヽヾ",
    "arabic": "ابتثجحخدذرزسشصضطظعغفقكلمنهويءٮ",
    "arabic_marks": "َُِّْٰـ",
    "persian_urdu": "پچژگںھےکی",
    "eastern_digits": "۰۱۲۳۴۵۶۷۸۹",
    "arabic_punct": "،؛؟۔٫٬",
    "syriac": "ܐܒܓܕܗܘܙܚܛܝܟܠܡܢܣܥܦܨܩܪܫܬ",
    "thaana": "ހށނރބޅކއވމފދތލގޏސޑޒޓޔޕޖ",
    "sinhala": "අආඇඉඊඋඌඑඒඔඕකගචජටඩතදනපබමයරලවශෂසහෆඞ",
    "devanagari": "अआइईउऊऋएऐओऔकखगघचछजझटठडढतथदधनपफबभमयरलवशषसह",
    "bengali": "অআইঈউঊঋএঐওঔকখগঘচছজঝটঠডঢতথদধনপফবভমযরলশষসহ",
    "gujarati": "અઆઇઈઉઊઋએઐઓઔકખગઘચછજઝટઠડઢતથદધનપફબભમયરલવશષસહ",
    "gurmukhi": "ਅਆਇਈਉਊਏਐਓਔਕਖਗਘਚਛਜਝਟਠਡਢਤਥਦਧਨਪਫਬਭਮਯਰਲਵਸਹ",
    "kannada": "ಅಆಇಈಉಊಋಎಏಐಒಓಔಕಖಗಘಚಛಜಝಟಠಡಢತಥದಧನಪಫಬಭಮಯರಲವಶಷಸಹ",
    "telugu": "అఆఇఈఉఊఋఎఏఐఒఓఔకఖగఘచఛజఝటఠడఢతథదధనపఫబభమయరలవశషసహ",
    "tamil": "அஆஇஈஉஊஎஏஐஒஓஔகசடதநபமயரலவழளறன",
    "malayalam": "അആഇഈഉഊഋഎഏഐഒഓഔകഖഗഘചഛജഝടഠഡഢതഥദധനപഫബഭമയരലവശഷസഹ",
    "hangul_jamo": "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㅏㅓㅗㅜㅡㅣ",
    "bopomofo": "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙ",
    "braille": "⠂⠆⠒⠲⡀⡄⡠⡴⢴⣀⣄⣠⣤⣴⣶⣿",
    "blocks": "░▒▓█▄▀▌▐",
    "box_math": "◜◝◟◞╭╮╯╰⌜⌝⌞⌟⊂⊃∩∪∴∵⌒",
    "geometric_shapes": "△▲▽▼◇◆○●◐◑◒◓◔◕◖◗",
    "arrows": "↑↗→↘↓↙←↖↔↕⇧⇨⇩⇦",
    "math_operators": "∧∨∩∪⊂⊃⊓⊔⊕⊖⊗⊘⋀⋁⋂⋃",
    "misc_technical": "⌃⌄⌅⌆⌇⌒⌜⌝⌞⌟⌀⌁",
    "dingbats": "✦✧✩✶✷✸✹✺✻✼",
    "ethiopic": "ሀሁሂሃሄህሆለሉሊላሌልሎመሙሚማሜምሞ",
    "georgian": "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ",
    "armenian": "ԱԲԳԴԵԶԷԸԹԺԻԼԽԾԿՀՁՂՃՄՅՆՇՈՉՊՋՌՍՎՏՐՑՒՓՔՕՖ",
    "cherokee": "ᎠᎡᎢᎣᎤᎥᎦᎧᎨᎩᎪᎫᎬᎭᎮᎯᎰᎱᎲᎳᎴᎵᎶᎷ",
    "canadian_aboriginal": "ᐁᐃᐅᐊᐱᐯᐳᐸᑎᑌᑐᑕᑭᑫᑯᑲᓂᓀᓄᓇᓯᓭᓱᓴ",
    "selected_kanji_hanzi": "山川田目口日月土石水火木草林森斜右凸凹厂厶凵匚",
    "hand_seed_bias": "∩⌒︵へヘᐱ˄︿＾㇀㇁㇂೧⌃ᘁᑎᘀ丄凸山ᗝᗞᘺᙀᙁ◠うウ╮ϡ⡴⢴⌝ᜮᘂᘃ⣴⡶⣷⡿◝ㄱ㇕ඞᘗᗒ⣶ᘖわワᑐ؛۔⊃ᙅ⣿つツ)j〉›右目ノ丿وܚ⟟⡠⣠厶فزدنٮ˅ܫ﹀⌄⌵しシ凹حع،⌞╰ˁ(〈‹くクᗴЄܘ⌜⡀⣀⡄厂·˙˚٫｡。∴∵⠂⠆٭⁙⠒⠲░▒▓█●◆田",
}

PROFILE_ROLES = {
    "GRASS": ["sparse_blade", "curved_tuft", "top_blade", "bloom_accent", "edge_lip"],
    "WATER": ["rounded_wave", "curl_body", "broken_foam", "soft_speck_fade"],
    "ROCK": ["grain", "strata", "hard_edge", "fracture", "mass_face"],
    "DIRT": ["granular_speck", "broken_stroke", "low_band", "dry_clump"],
    "SAND": ["soft_speck", "dust_fade", "rounded_low_mark", "wind_ripple"],
    "SNOW": ["soft_sparkle", "powder_speck", "snow_cap", "highlight_fade"],
    "MUD": ["sagging_curve", "wet_clump", "soft_mass", "bank_smear"],
    "GRAVEL": ["angular_chip", "small_fracture", "broken_edge", "dense_shard"],
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")


def unique_chars(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for ch in text:
        if ch.isspace() or ch in seen:
            continue
        seen.add(ch)
        out.append(ch)
    return out


def source_group(name: str) -> str:
    if name in {"hiragana", "katakana", "kana_marks", "bopomofo", "hangul_jamo", "cjk_strokes", "radicals", "kangxi_radicals", "selected_kanji_hanzi"}:
        return "east_asian"
    if name in {"devanagari", "bengali", "gujarati", "gurmukhi", "kannada", "telugu", "tamil", "malayalam", "sinhala"}:
        return "indic_sanskrit_family"
    if name in {"arabic", "arabic_marks", "persian_urdu", "eastern_digits", "arabic_punct", "syriac", "thaana"}:
        return "arabic_family"
    if name in {"braille", "blocks", "box_math", "geometric_shapes", "arrows", "math_operators", "misc_technical", "dingbats"}:
        return "symbol"
    if name == "hand_seed_bias":
        return "seed_bias"
    return "other_high_shape_script"


def inventory_bias(source: str) -> tuple[str, list[str], list[str]]:
    group = source_group(source)
    if group == "arabic_family":
        return "rounded_directional", ["foliage_curl", "shoreline_curve", "soft_break"], ["GRASS", "WATER", "MUD", "DIRT"]
    if group == "indic_sanskrit_family":
        return "rounded_mass", ["rounded_mass", "soft_mark", "curl"], ["MUD", "SAND", "SNOW", "DIRT"]
    if source in {"cjk_strokes", "radicals", "kangxi_radicals", "selected_kanji_hanzi", "box_math", "math_operators", "arrows"}:
        return "angular_directional", ["fracture", "edge", "strata"], ["ROCK", "GRAVEL", "DIRT"]
    if source == "hand_seed_bias":
        return "seed_bias", ["seed_direction", "seed_density", "seed_speck"], ["GRASS", "WATER", "ROCK", "DIRT", "SAND", "SNOW", "MUD", "GRAVEL"]
    if source in {"braille", "blocks", "geometric_shapes", "dingbats"}:
        return "speck_mass", ["speck", "mass", "sparkle"], ["SAND", "SNOW", "GRAVEL", "DIRT"]
    return "rounded_directional", ["curl", "blade", "tuft"], ["GRASS", "WATER", "MUD"]


PLANNED_INVENTORY_START_GID = 672
ADDITIVE_V1_FROZEN_RANGE = [512, 671]


def _load_v2_measurements() -> tuple[dict[int, dict[str, Any]], str | None, str | None]:
    """Return (gid->shape catalog entry, font_sha256, page_hash) for rendered v2.

    If the v2 atlas has not been compiled yet, returns ({}, None, None) and the
    inventory is generated as planned-but-unrendered (gate stays
    review_state=needs_atlas_render).
    """
    if not V2_CATALOG_PATH.exists() or not V2_AOA_PATH.exists():
        return {}, None, None
    catalog = load_json(V2_CATALOG_PATH)
    aoa = load_json(V2_AOA_PATH)
    by_gid = {int(e["glyph_id"]): e for e in catalog.get("entries", [])}
    page16 = next((p for p in aoa.get("pages", []) if int(p.get("cell_px", -1)) == 16), None)
    page_hash = page16.get("page_hash") if page16 else None
    return by_gid, aoa.get("font_sha256"), page_hash


def generate_inventory() -> list[dict[str, Any]]:
    v2_measurements, v2_font_hash, v2_page_hash = _load_v2_measurements()
    rows: list[dict[str, Any]] = []
    next_gid = PLANNED_INVENTORY_START_GID
    seen: set[str] = set()
    for source, chars in SOURCE_BLOCKS.items():
        visual_family, role_biases, material_affinity = inventory_bias(source)
        for ch in unique_chars(chars):
            if ch in seen:
                continue
            seen.add(ch)
            measured = v2_measurements.get(next_gid)
            if measured:
                density_lane = density_index(float(measured.get("density", 0.0)))
                review_state = "rendered_review_pending"
                glyph_id_status = "rendered_review_pending"
                rows.append(
                    {
                        "candidate_pack": "material.morphology.v2",
                        "glyph_id": next_gid,
                        "glyph_id_status": glyph_id_status,
                        "unicode_sequence": ch,
                        "codepoints": [f"U+{ord(c):04X}" for c in ch],
                        "source_block": source,
                        "source_family": source_group(source),
                        "script_family": source,
                        "visual_family": visual_family,
                        "role_biases": role_biases,
                        "material_affinity": material_affinity,
                        "direction_lanes": measured.get("direction_lanes", []),
                        "density_index": density_lane,
                        "shape6_norm": measured.get("shape6_norm"),
                        "external10": None,
                        "atlas_font_hash": v2_font_hash,
                        "atlas_page_hash": v2_page_hash,
                        "rendered_bitmap_hash": measured.get("rendered_bitmap_hash"),
                        "review_state": review_state,
                        "runtime_profile_live": False,
                        "manual_review_receipt": None,
                        "emoji_presentation": False,
                        "must_render_before_runtime": False,
                    }
                )
            else:
                rows.append(
                    {
                        "candidate_pack": "material.morphology.v2",
                        "glyph_id": next_gid,
                        "glyph_id_status": "planned_unrendered",
                        "unicode_sequence": ch,
                        "codepoints": [f"U+{ord(c):04X}" for c in ch],
                        "source_block": source,
                        "source_family": source_group(source),
                        "script_family": source,
                        "visual_family": visual_family,
                        "role_biases": role_biases,
                        "material_affinity": material_affinity,
                        "direction_lanes": [],
                        "density_index": None,
                        "shape6_norm": None,
                        "external10": None,
                        "atlas_font_hash": None,
                        "atlas_page_hash": None,
                        "rendered_bitmap_hash": None,
                        "review_state": "needs_atlas_render",
                        "runtime_profile_live": False,
                        "manual_review_receipt": None,
                        "emoji_presentation": False,
                        "must_render_before_runtime": True,
                    }
                )
            next_gid += 1
    return rows


def load_water_receipts() -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    if not WATER_RECEIPTS_PATH.exists():
        return rows
    with WATER_RECEIPTS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            rows[int(row["extended_glyph"])] = row
    return rows


def page16_data(aoa: dict[str, Any], aoa_path: Path = AOA_PATH) -> tuple[dict[str, Any], list[int], int, int]:
    page_meta = next(p for p in aoa["pages"] if int(p["cell_px"]) == 16)
    page_path = aoa_path.parent / page_meta["url"]
    page = load_json(page_path)
    rgba = page["rgba8"]
    height = int(page.get("height", page_meta.get("height_px")))
    width = int(page.get("width", len(rgba) // max(1, height * 4)))
    return page_meta, rgba, width, height


def glyph_grid(glyph_id: int, aoa: dict[str, Any], rgba: list[int], width: int) -> list[list[int]]:
    page_id, x0, y0, x1, y1 = aoa["glyph_index"][str(glyph_id)]
    _ = page_id
    grid: list[list[int]] = []
    for y in range(int(y0), int(y1)):
        row: list[int] = []
        for x in range(int(x0), int(x1)):
            alpha = rgba[((y * width) + x) * 4 + 3]
            row.append(1 if alpha > 0 else 0)
        grid.append(row)
    return grid


def bitmap_sha256(grid: list[list[int]]) -> str:
    raw = bytes(255 if px else 0 for row in grid for px in row)
    return hashlib.sha256(raw).hexdigest()


def external10(grid: list[list[int]]) -> list[float]:
    h = len(grid)
    w = len(grid[0]) if h else 0
    anchors = [
        (0.15, 0.05),
        (0.50, 0.05),
        (0.85, 0.05),
        (0.95, 0.30),
        (0.95, 0.70),
        (0.85, 0.95),
        (0.50, 0.95),
        (0.15, 0.95),
        (0.05, 0.70),
        (0.05, 0.30),
    ]
    vals: list[float] = []
    for ax, ay in anchors:
        cx = min(w - 1, max(0, round(ax * (w - 1))))
        cy = min(h - 1, max(0, round(ay * (h - 1))))
        ink = 0
        total = 0
        for yy in range(max(0, cy - 1), min(h, cy + 2)):
            for xx in range(max(0, cx - 1), min(w, cx + 2)):
                total += 1
                ink += grid[yy][xx]
        vals.append(round(ink / float(max(1, total)), 4))
    return vals


def centroid(grid: list[list[int]]) -> list[float]:
    pts = [(x, y) for y, row in enumerate(grid) for x, px in enumerate(row) if px]
    if not pts:
        return [0.5, 0.5]
    w = len(grid[0])
    h = len(grid)
    return [round(sum(x for x, _ in pts) / len(pts) / max(1, w - 1), 4), round(sum(y for _, y in pts) / len(pts) / max(1, h - 1), 4)]


def axis_deg(grid: list[list[int]]) -> float:
    pts = [(x, y) for y, row in enumerate(grid) for x, px in enumerate(row) if px]
    if len(pts) < 2:
        return 0.0
    mx = sum(x for x, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    sxx = sum((x - mx) * (x - mx) for x, _ in pts)
    syy = sum((y - my) * (y - my) for _, y in pts)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    return round((math.degrees(angle) + 180.0) % 180.0, 2)


def edge_mask(grid: list[list[int]]) -> list[str]:
    if not grid:
        return []
    top = any(grid[0])
    bottom = any(grid[-1])
    left = any(row[0] for row in grid)
    right = any(row[-1] for row in grid)
    return [name for name, present in (("top", top), ("right", right), ("bottom", bottom), ("left", left)) if present]


def density_index(density: float) -> str:
    if density < 0.18:
        return "D0"
    if density < 0.34:
        return "D1"
    if density < 0.52:
        return "D2"
    return "D3"


def direction_lanes(entry: dict[str, Any], center: list[float]) -> list[str]:
    dx = float(entry.get("right_weight", 0.0)) - float(entry.get("left_weight", 0.0))
    dy = float(entry.get("bottom_weight", 0.0)) - float(entry.get("top_weight", 0.0))
    dx += (center[0] - 0.5) * 0.75
    dy += (center[1] - 0.5) * 0.75
    if abs(dx) + abs(dy) < 0.08:
        return ["NONE"]
    angle = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
    screen_dirs = ["E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW", "N", "NNE", "NE", "ENE"]
    idx = int((angle + 11.25) // 22.5) % 16
    primary = screen_dirs[idx]
    if primary == "NONE":
        return ["NONE"]
    adjacent = [screen_dirs[(idx - 1) % 16], screen_dirs[(idx + 1) % 16]]
    return [primary] + adjacent


def visual_family(entry: dict[str, Any]) -> str:
    rep = str(entry.get("repertoire", ""))
    stroke = str(entry.get("stroke_class", ""))
    if rep in {"hiragana", "katakana", "arabic"}:
        return "rounded_directional"
    if stroke in {"line", "corner"}:
        return "angular_directional"
    if rep in {"braille", "blocks"}:
        return "speck_mass"
    return "mixed_single_cell"


def material_affinity(entry: dict[str, Any], water_row: dict[str, Any] | None) -> list[str]:
    if water_row:
        return ["WATER"]
    roles = set(entry.get("roles", []))
    stroke = str(entry.get("stroke_class", ""))
    rep = str(entry.get("repertoire", ""))
    out: set[str] = set()
    if roles.intersection({"grass_top", "flower_top"}) or rep in {"hiragana", "katakana"}:
        out.update(["GRASS", "MUD"])
    if roles.intersection({"rock_face", "corner_lip"}) or stroke in {"corner", "line"}:
        out.update(["ROCK", "GRAVEL", "DIRT"])
    if float(entry.get("density", 0.0)) < 0.22:
        out.update(["SAND", "SNOW"])
    if float(entry.get("curve_score", 0.0)) > 0.20:
        out.update(["WATER", "MUD", "GRASS"])
    if not out:
        out.update(["DIRT"])
    return sorted(out)


def shape_role(entry: dict[str, Any], profile: str | None = None) -> str:
    if profile and PROFILE_ROLES.get(profile):
        density = float(entry.get("density", 0.0))
        if profile == "WATER":
            if int(entry["glyph_id"]) in {516, 517}:
                return "rounded_wave"
            if int(entry["glyph_id"]) in {518, 519}:
                return "broken_foam"
            return "curl_body"
        if density < 0.18:
            return PROFILE_ROLES[profile][0]
        if density > 0.50:
            return PROFILE_ROLES[profile][-1]
        return PROFILE_ROLES[profile][1]
    roles = entry.get("roles", [])
    return str(roles[0] if roles else entry.get("stroke_class", "mark"))


def build_receipts(catalog: dict[str, Any], aoa: dict[str, Any], aoa_path: Path = AOA_PATH, source_tag: str = "measured_existing_additive", font_backend: str = "material.additive.v1.page16_rgba8") -> list[dict[str, Any]]:
    water = load_water_receipts()
    page_meta, rgba, width, _height = page16_data(aoa, aoa_path)
    rows: list[dict[str, Any]] = []
    for entry in sorted(catalog["entries"], key=lambda e: int(e["glyph_id"])):
        gid = int(entry["glyph_id"])
        grid = glyph_grid(gid, aoa, rgba, width)
        sha = bitmap_sha256(grid)
        center = centroid(grid)
        water_row = water.get(gid)
        density_lane = density_index(float(entry.get("density", 0.0)))
        family = visual_family(entry)
        role = shape_role(entry)
        rows.append(
            {
                "receipt_id": f"FL4131-M2-{gid}",
                "glyph_id": gid,
                "unicode_sequence": entry.get("unicode"),
                "unicode_scalar": entry.get("unicode_scalar"),
                "content_pack_id": catalog.get("content_pack_id"),
                "script_family": entry.get("repertoire"),
                "visual_family": family,
                "shape_role": role,
                "material_affinity": material_affinity(entry, water_row),
                "direction_lanes": direction_lanes(entry, center),
                "vertical_relation_lanes": ["none"],
                "density_index": density_lane,
                "shape6_norm": entry.get("shape6_norm"),
                "shape6_density": entry.get("shape6_density"),
                "external10": external10(grid),
                "external10_model": "glyph_edge10_v1",
                "principal_axis_deg": axis_deg(grid),
                "mass_center_xy": center,
                "stroke_center_xy": center,
                "edge_contact_mask": edge_mask(grid),
                "void_count": 0,
                "symmetry_class": "balanced" if entry.get("shape6_asymmetry_lr", 1.0) < 0.12 else "asymmetric",
                "sibling_group": f"{family}:{role}:{density_lane}",
                "rendered_bitmap_hash": int(sha[:8], 16),
                "rendered_bitmap_sha256": sha,
                "atlas_font_hash": aoa.get("font_sha256"),
                "atlas_page_hash": page_meta.get("page_hash"),
                "cell_px": 16,
                "font_render_backend": font_backend,
                "shape_extractor_version": "fl4131_material_morphology_v2_review",
                "manual_review_receipt": None,
                "review_state": source_tag,
                "runtime_profile_live": False,
                "morphology_v2_runtime_profile_live": False,
                "fl4208_water_receipt_id": water_row.get("receipt_id") if water_row else None,
                "rejection_reason": None,
            }
        )
    return rows


def receipt_score(row: dict[str, Any], profile: str, lane: str, density: str) -> float:
    score = 0.0
    if profile in row.get("material_affinity", []):
        score += 4.0
    if lane in row.get("direction_lanes", []):
        score += 2.0
    if density == row.get("density_index"):
        score += 1.5
    if profile == "WATER" and row.get("fl4208_water_receipt_id"):
        score += 3.0
    if profile in {"SAND", "SNOW"}:
        score -= max(0.0, float(row.get("shape6_density", 0.0)) - 0.35)
    if profile in {"ROCK", "GRAVEL"}:
        score += 0.4 if row.get("visual_family") == "angular_directional" else 0.0
    return round(score, 4)


def build_tables(receipts: list[dict[str, Any]], inventory_rows: list[dict[str, Any]], catalog: dict[str, Any], aoa: dict[str, Any]) -> dict[str, Any]:
    cells_by_profile: dict[str, dict[str, dict[str, Any]]] = {}
    used: dict[str, set[int]] = defaultdict(set)
    for profile in PROFILES:
        lane_table: dict[str, dict[str, Any]] = {}
        for lane in DIRECTIONS:
            density_cells: dict[str, Any] = {}
            for density in DENSITIES:
                ranked = sorted(
                    receipts,
                    key=lambda r: (receipt_score(r, profile, lane, density), -abs(int(r["glyph_id"]) - 590)),
                    reverse=True,
                )
                picked = [r for r in ranked if receipt_score(r, profile, lane, density) > 1.49][:12]
                if not picked:
                    picked = ranked[:4]
                primary = picked[0]
                used[profile].update(int(r["glyph_id"]) for r in picked)
                density_cells[density] = {
                    "material_profile": profile,
                    "direction_lane": lane,
                    "density_index": density,
                    "vertical_relation_lane": "none",
                    "role": shape_role(primary, profile),
                    "primary_glyph_ids": [int(primary["glyph_id"])],
                    "candidate_glyph_ids": [int(r["glyph_id"]) for r in picked],
                    "candidate_receipt_ids": [r["receipt_id"] for r in picked],
                    "receipt_ids": [r["receipt_id"] for r in picked],
                    "candidate_count": len(picked),
                    "min_candidate_count": 8,
                    "preferred_candidate_count": 12,
                    "runtime_state": "review_pending",
                    "needs_manual_review": True,
                    "needs_more_candidates": len(picked) < 8,
                    "rare_cell": False,
                    "max_shape6_distance": 0.12,
                    "max_density_delta": 0.08,
                    "tie_rule": "lowest_shape6_distance_then_density_delta_then_receipt_id",
                    "manual_review_receipt": None,
                    "sibling_policy": {
                        "same_visual_family": True,
                        "compatible_roles": PROFILE_ROLES[profile],
                        "same_density_index": True,
                        "same_vertical_relation_lane_when_present": True,
                        "shared_sibling_group": True,
                        "adjacent_density_allowed_if": {
                            "max_density_delta": 0.08,
                            "max_shape6_distance": 0.12,
                        },
                    },
                }
            lane_table[lane] = density_cells
        cells_by_profile[profile] = lane_table

    return {
        "schema_version": "fl4131_material_morphology_profile_tables.v2.review",
        "generated_at": date.today().isoformat(),
        "review_state": "review_incomplete",
        "runtime_profile_live": False,
        "review_summary": {
            "accepted_cells": 0,
            "total_cells": len(PROFILES) * len(DIRECTIONS) * len(DENSITIES),
            "input_hash": "",
            "refreshed_at": "",
        },
        "source_catalog": str(CATALOG_PATH.relative_to(REPO_ROOT)),
        "source_catalog_hash": hashlib.sha256(json.dumps(catalog, sort_keys=True).encode("utf-8")).hexdigest(),
        "candidate_inventory": str(INVENTORY_OUT.relative_to(REPO_ROOT)),
        "candidate_inventory_count": len(inventory_rows),
        "shape_receipts": str(RECEIPTS_OUT.relative_to(REPO_ROOT)),
        "shape_receipts_count": len(receipts),
        "profile_inventory": PROFILES,
        "direction_lanes": DIRECTIONS,
        "density_lanes": DENSITIES,
        "vertical_relation_lanes": ["flat", "rise", "fall", "ridge", "valley", "wall", "overhang", "none"],
        "glyph_id_allocation": {
            "material_additive_v1_frozen_range": ADDITIVE_V1_FROZEN_RANGE,
            "material_morphology_v2_planned_start": PLANNED_INVENTORY_START_GID,
            "planned_inventory_status": "unrendered_not_runtime",
        },
        "atlas": {
            "content_pack_id": aoa.get("content_pack_id"),
            "font_id": aoa.get("font_id"),
            "font_sha256": aoa.get("font_sha256"),
        },
        "profiles": cells_by_profile,
        "profile_measured_glyphs": {profile: sorted(ids) for profile, ids in used.items()},
        "acceptance_blockers": [
            "material.morphology.v2 inventory must be rendered into atlas pages",
            "manual review receipts must accept primary winners",
            "common cells need at least 8 measured candidates",
            "runtime diagnostic glyph must be receipt-backed",
        ],
    }


def build_rejections() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    policies = {
        "WATER": [
            ("sharp_angle", "sharp marks cannot satisfy rounded shoreline water"),
            ("straight_kashida_default", "straight default kashida is not water motion"),
            ("star_snow", "sparkle ownership belongs to SNOW"),
            ("triangle", "triangle silhouettes are hard geometry"),
        ],
        "GRASS": [("hard_rock_chip_primary", "hard chips cannot be primary grass blades")],
        "ROCK": [("soft_mud_blob_primary", "soft blobs cannot be primary rock faces")],
        "SAND": [("dense_block_mass", "dense blocks exceed sparse sand profile")],
        "SNOW": [("water_foam_primary", "water foam receipts do not prove snow sparkle")],
        "MUD": [("angular_gravel_primary", "angular gravel chips cannot be primary mud")],
        "GRAVEL": [("soft_mud_mass_primary", "soft mud mass cannot be primary gravel")],
        "DIRT": [("water_curl_primary", "water curls cannot be primary dirt grains")],
    }
    for profile, items in policies.items():
        for rejection_class, reason in items:
            rows.append(
                {
                    "profile": profile,
                    "rejection_class": rejection_class,
                    "rejection_reason": reason,
                    "review_state": "policy_rejected_until_measured_exception",
                    "runtime_profile_live": False,
                }
            )
    return rows


def build_ui_schema() -> dict[str, Any]:
    required_fields = [
        "commit",
        "profile_table_hash",
        "candidate_inventory_hash",
        "atlas_font_hash",
        "atlas_page_hash",
        "material_id",
        "material_profile",
        "authored_swatch",
        "source_cell",
        "source_surface",
        "raw6",
        "normalized6",
        "external10",
        "extmax6",
        "directional6",
        "global6",
        "assigned_direction",
        "assigned_density",
        "assigned_vertical",
        "resolved_direction",
        "resolved_density",
        "resolved_vertical",
        "primary_glyph_id",
        "winner_glyph_id",
        "sibling_source_glyph_id",
        "score",
        "shape6_distance",
        "density_delta",
        "receipt_id",
        "rejection_reason",
        "slider_delta",
        "screenshot_path",
        "manual_review_state",
    ]
    return {
        "schema_version": "fl4131_material_profile_resolution_receipt.v2",
        "review_state": "review_schema_locked",
        "required_fields": required_fields,
        "panels": {
            "edit_tab_material_workspace": ["material_id", "authored_swatch", "assigned_profile", "profile_table_version", "manifest_hash", "undo_state"],
            "harri_profile_panel": [
                "assigned_profile",
                "assigned_direction",
                "assigned_density",
                "assigned_vertical",
                "resolved_direction",
                "resolved_density",
                "resolved_vertical",
                "winner_glyph",
                "winner_receipt_id",
                "sibling_source",
            ],
            "candidate_table": ["rank", "glyph_cell", "glyph_id", "unicode_text", "visual_family", "role", "density", "direction_lanes", "score", "receipt_id", "rejection_state"],
            "stage_vector_panel": ["raw6", "normalized6", "external10", "extmax6", "directional6", "global6", "changed_slider", "pre_value", "post_value"],
            "material_preview_strip": ["authored_4x4_ramp", "resolved_4x16_lane", "sibling_expanded_winners", "fail_closed_diagnostics"],
        },
    }


def generate() -> dict[str, int]:
    catalog = load_json(CATALOG_PATH)
    aoa = load_json(AOA_PATH)
    inventory = generate_inventory()
    receipts = build_receipts(catalog, aoa, AOA_PATH, "measured_existing_additive", "material.additive.v1.page16_rgba8")
    if V2_CATALOG_PATH.exists() and V2_AOA_PATH.exists():
        v2_catalog = load_json(V2_CATALOG_PATH)
        v2_aoa = load_json(V2_AOA_PATH)
        receipts += build_receipts(
            v2_catalog,
            v2_aoa,
            V2_AOA_PATH,
            "measured_morphology_v2_review_pending",
            "material.morphology.v2.page16_rgba8",
        )
    tables = build_tables(receipts, inventory, catalog, aoa)
    rejections = build_rejections()
    ui_schema = build_ui_schema()

    dump_jsonl(INVENTORY_OUT, inventory)
    dump_jsonl(RECEIPTS_OUT, receipts)
    dump_json(TABLES_OUT, tables)
    dump_jsonl(REJECTIONS_OUT, rejections)
    dump_json(UI_SCHEMA_OUT, ui_schema)
    return {
        "candidate_inventory_rows": len(inventory),
        "shape_receipt_rows": len(receipts),
        "profiles": len(PROFILES),
        "profile_cells": len(PROFILES) * len(DIRECTIONS) * len(DENSITIES),
        "rejection_rows": len(rejections),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    counts = generate()
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
