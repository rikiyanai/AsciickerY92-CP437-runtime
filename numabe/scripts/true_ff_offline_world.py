#!/usr/bin/env python3
"""FL-4877: Godot-free publications consumed by the offline True FF frame.

Each function below reproduces one GDScript owner's GPU-facing bytes for a
cold, single-view publication from the same repository files the runtime
reads. The owner is named on every function; the byte layout follows it
line for line, and every ABI number is read from the owner's constants.

  language_catalog()  TrueFFFeatureRenderer._setup + TrueFFPartRepresentation
                      .production/.timing_product + TrueFFTextFont
  terrain_world()     A3D map -> SceneGeometryDB terrain facts (through
                      scripts/true_ff_terrain_sources.py, the bank compiler's
                      reader) + TrueFFFeatureBank cold publication
  camera_facts()      CameraFrameState.live_true_perspective_projection +
                      TrueFFCompositorEffect._camera_facts + TrueFFPagePhase
  sun_direction()     CelestialFrameState.sun_direction

The world modelled here is the authoritative terrain with its compiled bank.
Static placements, actors, vegetation and the live water/effect/sky families
are not published by this module; the caller names that in its receipt.
"""
from __future__ import annotations

import json
import math
import re
import struct
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / "godot_project"

import importlib.util  # noqa: E402


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BANK = _module("true_ff_feature_bank")
GD = BANK._gd_constants
FEATURE_ABI = GD(PROJ / "render/ascii/features/TrueFFFeatureABI.gd")
STRUCTURE_ABI = GD(PROJ / "render/ascii/structure/TrueFFStructureABI.gd")
SURFACE_ABI = GD(PROJ / "render/core/contracts/visibility/VisibleSurfaceABI.gd")
BANK_GD = GD(PROJ / "render/ascii/features/TrueFFFeatureBank.gd")
RENDERER_GD = GD(PROJ / "render/ascii/features/TrueFFFeatureRenderer.gd")
TERRAIN_TREE_GD = GD(PROJ / "render/ascii/structure/TrueFFTerrainVisibility.gd")
VEGETATION_GD = GD(PROJ / "render/ascii/features/TrueFFVegetationFeatures.gd")
PALETTE_GD = GD(PROJ / "scripts/map/TerrainMaterialPalette.gd")
A3D_GD = GD(PROJ / "scripts/map/A3DMapLoader.gd")
DEFAULT_MAP = PROJ / "assets/maps/game_map_y8.a3d"
UINT_NONE = 0xFFFFFFFF


class PublicationError(RuntimeError):
    pass


def u32s(*values) -> bytes:
    return struct.pack("<%dI" % len(values), *[int(v) & 0xFFFFFFFF for v in values])


def f32s(*values) -> bytes:
    return struct.pack("<%df" % len(values), *[float(v) for v in values])


# ------------------------------------------------------------ language catalog

def _json(res: str) -> dict:
    return json.loads((PROJ / res).read_text(encoding="utf-8"))


def _admission_index(language: dict) -> dict:
    index: dict = {}
    for group in language.get("groups", []):
        if not isinstance(group, dict):
            continue
        sequences = group.get("sequences", [])
        if not isinstance(sequences, list):
            continue
        admitted = index.setdefault(str(group.get("mode", "")), {})
        for sequence in sequences:
            if isinstance(sequence, str):
                admitted[sequence] = True
    return index


def _spelling(members, language: dict, admission: dict) -> str:
    if not isinstance(members, list):
        return ""
    text = ""
    for member in members:
        if not isinstance(member, list) or len(member) != 2:
            return ""
        sequences = admission.get(str(member[0]))
        if not isinstance(sequences, dict) or str(member[1]) not in sequences:
            return ""
        text += str(member[1])
    return text if 1 <= len(text) <= 4 else ""


def _append(result: list, seen: dict, text: str) -> None:
    if not text or text in seen:
        return
    seen[text] = True
    result.append(text)


def _mode_spelling(members, mode: str, outline: dict, admission: dict) -> str:
    if not isinstance(members, list):
        return ""
    qualified = []
    for text in members:
        if not isinstance(text, str):
            return ""
        qualified.append([mode, text])
    return _spelling(qualified, outline, admission)


def _part_spellings(part: dict, outline: dict, admission: dict) -> list:
    result = []
    mode = "empty" if str(part.get("role", "")) == "contour" else "sparse_scatter"
    if "terminal" in part:
        text = _mode_spelling(part["terminal"].get("idiom"), mode, outline, admission)
        if not text:
            return []
        result.append(text)
    for members in part.get("idiom_by_tier", []):
        if members is None:
            continue
        text = _mode_spelling(members, mode, outline, admission)
        if not text:
            return []
        result.append(text)
    keys = list(part.get("keys", []))
    for tier_keys in part.get("keys_by_tier", []):
        if isinstance(tier_keys, list):
            keys.extend(tier_keys)
    for key in keys:
        if not isinstance(key, list) or len(key) != 2:
            return []
        text = _mode_spelling(key[1], mode, outline, admission)
        if not text:
            return []
        result.append(text)
    return result


def _definition(outline: dict, path: list):
    value = outline
    if not path:
        return None
    for component in path:
        if isinstance(value, dict):
            if component not in value:
                return None
            value = value[component]
        elif isinstance(value, list) and isinstance(component, (int, float)):
            index = int(component)
            if index < 0 or index >= len(value):
                return None
            value = value[index]
        else:
            return None
    return value


def _definition_spellings(definition, outline: dict, admission: dict) -> list:
    result = []
    if isinstance(definition, list):
        for star_class in definition:
            if not isinstance(star_class, dict):
                return []
            for members in star_class.get("idioms", []):
                text = _mode_spelling(members, str(star_class.get("mode", "")), outline, admission)
                if not text:
                    return []
                result.append(text)
    elif isinstance(definition, dict):
        for part in definition.get("parts", []):
            if not isinstance(part, dict):
                return []
            spellings = _part_spellings(part, outline, admission)
            if not spellings:
                return []
            result.extend(spellings)
        if "shards" in definition:
            shards = dict(definition["shards"])
            shards["role"] = "sparse"
            result.extend(_part_spellings(shards, outline, admission))
        for members in definition.get("classes", {}).values():
            text = _mode_spelling(members, str(definition.get("mode", "")), outline, admission)
            if not text:
                return []
            result.append(text)
        if "token" in definition:
            text = _mode_spelling(definition["token"], str(definition.get("mode", "")), outline, admission)
            if not text:
                return []
            result.append(text)
    return result


def _production(outline: dict) -> dict:
    """TrueFFPartRepresentation.production."""
    admission = _admission_index(outline)
    depictions = outline.get("feature_depictions", {})
    runtime = depictions.get("runtime", {})
    if depictions.get("schema") != "true_ff.feature_depictions.v1" \
            or runtime.get("schema") != "true_ff.depiction_bindings.v1" \
            or int(runtime.get("required_feature_count", 0)) != 237:
        raise PublicationError("true_ff_required_depiction_bindings_missing")
    table_rows = outline.get("run_table", {}).get("rows", [])
    terminal = outline.get("terminal_axes", {})
    if terminal.get("schema") != "true_ff.compiled_terminal_axes.v1" \
            or not isinstance(terminal.get("by_sequence"), dict):
        raise PublicationError("true_ff_terminal_axes_missing")
    terminal_parts = outline.get("terminal_parts", {})
    if terminal_parts.get("schema") != "true_ff.compiled_terminal_parts.v1" \
            or not isinstance(terminal_parts.get("by_sequence"), dict):
        raise PublicationError("true_ff_terminal_parts_missing")
    # _base_vocabulary
    sequences: list = []
    seen: dict = {}
    for row in table_rows:
        if not isinstance(row, dict):
            continue
        for text in (row.get("sequence", ""), row.get("substitute", "")):
            _append(sequences, seen, str(text))
    face = outline.get("face_authoring", {})
    for tier in face.get("tiers", []):
        if not isinstance(tier, dict):
            continue
        for feature in ("eye_left", "eye_right", "nose", "mouth"):
            _append(sequences, seen, _spelling(tier.get(feature), outline, admission))
    fold = face.get("fold", {})
    for feature in ("steep", "falling", "rising"):
        _append(sequences, seen, _spelling(fold.get(feature), outline, admission))
    authored_tables = runtime.get("authored_tables", {})
    for table_name, binding in authored_tables.items():
        spellings = _definition_spellings(_definition(outline, binding.get("definition_path", [])),
                                          outline, admission)
        if not spellings:
            raise PublicationError(f"true_ff_authored_depiction_definition_rejected:{table_name}")
        for text in spellings:
            _append(sequences, seen, text)
    rows: list = []
    covered: dict = {}
    group_addresses: dict = {}
    maximum_address = -1
    for group in runtime.get("role_groups", []):
        if not isinstance(group, dict):
            raise PublicationError("true_ff_depiction_role_group_invalid")
        role = int(group.get("role_id", -1))
        language_id = int(group.get("language_id", -1))
        address = role * 16 + language_id
        if role < 0 or role >= 64 or language_id < 0 or language_id >= 16 \
                or address in group_addresses or not group.get("feature_ids", []):
            raise PublicationError("true_ff_depiction_role_group_identity_invalid")
        first = len(rows)
        required = group.get("required_run_indices", [])
        if not required:
            raise PublicationError("true_ff_depiction_role_has_no_required_recipe")
        for index_value in group.get("run_indices", []):
            index = int(index_value)
            if index < 0 or index >= len(table_rows) or index in covered \
                    or not isinstance(table_rows[index], dict):
                raise PublicationError("true_ff_depiction_run_address_invalid")
            row = table_rows[index]
            if int(row.get("role_id", -1)) != role or str(row.get("role", "")) != str(group.get("role", "")) \
                    or str(row.get("language", "")) != str(group.get("language", "")) \
                    or int(outline.get("languages", {}).get(row["language"], -1)) != language_id:
                raise PublicationError("true_ff_depiction_run_owner_mismatch")
            covered[index] = True
            rows.append(row)
        group_addresses[address] = [first, len(rows) - first, len(required), len(group["feature_ids"])]
        maximum_address = max(maximum_address, address)
    if not rows or len(covered) != len(table_rows):
        raise PublicationError("true_ff_depiction_run_continuum_incomplete")
    group_count = ((maximum_address // 16) + 1) * 16
    binding_words = [0] * (group_count * 4)
    for address, words in group_addresses.items():
        for lane in range(4):
            binding_words[address * 4 + lane] = words[lane]
    # _fold_product
    geometry = []
    for field in ("min_rows", "steep_max_lean", "shallow_min_lean"):
        value = fold.get(field)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise PublicationError("true_ff_fold_geometry_missing")
        geometry.append(float(value))
    if geometry[0] <= 0.0 or geometry[0] != math.floor(geometry[0]) \
            or geometry[1] < 0.0 or geometry[2] <= geometry[1]:
        raise PublicationError("true_ff_fold_geometry_invalid")
    geometry.append(0.0)
    idioms = [-1]
    for shape in ("steep", "falling", "rising"):
        text = _spelling(fold.get(shape), outline, admission)
        if len(text) != 1 or text not in sequences:
            raise PublicationError("true_ff_fold_spelling_not_admitted")
        idioms.append(sequences.index(text))
    fold_bytes = f32s(*geometry) + struct.pack("<4i", *idioms)
    return {"run_rows": rows, "binding_words": binding_words, "fold_bytes": fold_bytes,
            "sequences": sequences, "terminal_axes": terminal["by_sequence"],
            "terminal_parts": terminal_parts["by_sequence"]}


def _timing_product() -> bytes:
    """TrueFFPartRepresentation.timing_product."""
    timing = _json("assets/glyphs/true_ff/figure_part_timing.v1.json")
    if timing.get("schema") != "true_ff.figure_part_timing.v1" \
            or timing.get("clock") != "accepted_source_animation_seconds":
        raise PublicationError("figure_part_timing_asset_missing")
    sources = [{"id": 0, "profiles": timing.get("profiles", [])}]
    ids = {0}
    profile_count = 1
    for source in timing.get("source_profiles", []):
        identity = int(source.get("id", -1))
        if identity <= 0 or identity > 15 or identity in ids:
            raise PublicationError("figure_timing_profile_identity_invalid")
        ids.add(identity)
        profile_count = max(profile_count, identity + 1)
        sources.append(source)
    default = timing.get("default", {})
    default_duration = float(default.get("duration_seconds", 0.0))
    default_phase = float(default.get("phase_seconds", 0.0))
    if default_duration <= 0.0:
        raise PublicationError("figure_timing_default_invalid")
    tempos = [0.0] * (profile_count * 256 * 4)
    for source in sources:
        base = int(source["id"]) * 256 * 4
        for part in range(256):
            tempos[base + part * 4] = default_duration
            tempos[base + part * 4 + 1] = default_phase
            tempos[base + part * 4 + 2] = 1.0
        assigned = set()
        for profile in source.get("profiles", []):
            duration = float(profile.get("duration_seconds", 0.0))
            phase = float(profile.get("phase_seconds", 0.0))
            if duration <= 0.0:
                raise PublicationError("figure_timing_part_tempo_invalid")
            for part_value in profile.get("parts", []):
                part = int(part_value)
                if part < 0 or part > 255 or part in assigned:
                    raise PublicationError("figure_timing_part_identity_invalid")
                assigned.add(part)
                tempos[base + part * 4] = duration
                tempos[base + part * 4 + 1] = phase
    events = []
    previous = -1.0
    seen = {}
    for event in timing.get("events", []):
        phase = float(event.get("phase", -1.0))
        identity = str(event.get("id", ""))
        spelling = str(event.get("spelling", ""))
        if phase < 0.0 or phase >= 1.0 or phase <= previous or (not events and phase != 0.0) \
                or not identity or identity in seen or spelling not in ("run_primary", "run_substitute"):
            raise PublicationError("figure_timing_event_order_invalid")
        seen[identity] = phase
        previous = phase
        events.extend([phase, 1.0 if spelling == "run_substitute" else 0.0, 1.0 if "from" in event else 0.0, 0.0])
    if not events:
        raise PublicationError("figure_timing_events_missing")
    words = [float(profile_count * 256), float(len(events) // 4), 0.0, 0.0] + tempos + events
    return f32s(*words)


def _font(repertoire: list) -> dict:
    """TrueFFTextFont.load_repertoire."""
    from PIL import Image

    source = _json("assets/glyphs/true_ff/saitamaar/catalog.json")
    if source.get("schema") != "true_ff.saitamaar.catalog.v1":
        raise PublicationError("text_font_schema_rejected")
    image = Image.open(PROJ / "assets/glyphs/true_ff/saitamaar/atlas.png").convert("RGBA")
    atlas = source.get("atlas", {})
    if image.width != int(atlas.get("width_px", -1)) or image.height != int(atlas.get("height_px", -1)):
        raise PublicationError("text_font_atlas_extent_mismatch")
    by_codepoint = {int(glyph["codepoint"]): glyph for glyph in source["glyphs"]}
    glyphs = {}
    pixels = image.load()
    for cp in repertoire:
        if cp in glyphs or cp not in by_codepoint:
            raise PublicationError(f"text_font_repertoire_codepoint_rejected:{cp}")
        entry = by_codepoint[cp]
        bounds = []
        for field in ("atlas_rect", "ink_bounds"):
            x0, y0, x1, y1 = (int(v) for v in entry[field])
            bounds.append((x0, y0, x1 - x0, y1 - y0))
        rect, ink = bounds
        if rect[2:] != ink[2:]:
            raise PublicationError(f"text_font_ink_extent_invalid:{cp}")
        for y in range(ink[3]):
            for x in range(ink[2]):
                r, g, b, a = pixels[rect[0] + x, rect[1] + y]
                if a != 255 or r != g or r != b or r not in (0, 255):
                    raise PublicationError(f"text_font_mask_not_binary:{cp}")
        if int(entry["advance_px"]) <= 0:
            raise PublicationError(f"text_font_nonspacing_character:{cp}")
        glyphs[cp] = {"advance_px": int(entry["advance_px"]), "bearing_x": int(entry["bearing_x"]),
                      "bearing_y": int(entry["bearing_y"]), "atlas_rect": rect, "ink_bounds": ink}
    if int(source["font_px"]) != RENDERER_GD["FONT_PX"] \
            or int(source["recommended_row_step_px"]) != RENDERER_GD["ROW_PITCH"]:
        raise PublicationError("true_ff_feature_font_pitch_rejected")
    return {"glyphs": glyphs, "image": image.tobytes(), "width": image.width, "height": image.height}


def _hud_catalog_codepoints() -> list:
    """TrueFFHudText.catalog_codepoints."""
    parsed = _json("assets/glyphs/true_ff/saitamaar/catalog.json")
    admitted = {int(cp) for cp in parsed.get("candidate_admission", {}).get("codepoints", [])}
    result = [int(g["codepoint"]) for g in parsed["glyphs"]
              if g.get("has_ink", False) and (0x21 <= int(g["codepoint"]) <= 0x7E or int(g["codepoint"]) in admitted)]
    return sorted(result)


ALNUM = re.compile("[A-Za-z0-9０-９Ａ-Ｚａ-ｚ]")


def language_catalog() -> dict:
    """TrueFFFeatureRenderer._setup: the immutable P1/P5 catalog bytes."""
    authored = _json("assets/glyphs/true_ff/outline_language.v1.json")
    if authored.get("schema") != "true_ff.outline_language.v1":
        raise PublicationError("true_ff_feature_language_missing")
    if not isinstance(authored.get("run_table", {}).get("rows"), list):
        raise PublicationError("true_ff_feature_run_table_missing")
    product = _production(authored)
    timing = _timing_product()
    sequences = product["sequences"]
    sequence_index = {}
    repertoire = [0x20, 0x3000]
    for index, text in enumerate(sequences):
        if not text or len(text) > 4:
            raise PublicationError("true_ff_feature_idiom_too_long")
        sequence_index[text] = index
        for ch in text:
            if ord(ch) not in repertoire:
                repertoire.append(ord(ch))
    crest = sequence_index.get("≈≈", -1)  # TrueFFFeatureRenderer.CREST_PAIR
    if crest < 0:
        raise PublicationError("true_ff_feature_required_crest_pair_missing")
    for cp in _hud_catalog_codepoints():
        if cp not in repertoire:
            repertoire.append(cp)
    for cp in (0x22, 0x27, 0x2A, 0x2B, 0x2C, 0x2E, 0x6F, 0x76, 0x3002, 0x309C):
        if cp not in repertoire:
            repertoire.append(cp)
    repertoire.sort()
    font = _font(repertoire)
    glyphs = font["glyphs"]
    glyph_index = {}
    glyph_bytes = b""
    min_advance = 0x7FFFFFFF
    for cp in repertoire:
        glyph = glyphs[cp]
        glyph_index[cp] = len(glyph_index)
        rect, ink = glyph["atlas_rect"], glyph["ink_bounds"]
        min_advance = min(min_advance, glyph["advance_px"])
        glyph_bytes += struct.pack("<12i", glyph["advance_px"], glyph["bearing_x"], glyph["bearing_y"], cp,
                                   *rect, *ink)
    half, full = glyphs[0x20]["advance_px"], glyphs[0x3000]["advance_px"]
    if half != 5 or full != 11:
        raise PublicationError("true_ff_feature_native_spacing_contract_rejected")
    terminal_axes, terminal_parts = product["terminal_axes"], product["terminal_parts"]
    wave = {0xFF5E, 0x2248}
    crest_pair = "≈≈"
    idiom_bytes = b""
    for text in sequences:
        members, offsets = [-1, -1, -1, -1], [0, 0, 0, 0]
        pen, ink_left, ink_right, has_wave = 0, 0x3FFF, -0x3FFF, False
        for i, ch in enumerate(text):
            glyph = glyphs[ord(ch)]
            ink = glyph["ink_bounds"]
            if ink[2] > 0:
                ink_left = min(ink_left, pen + ink[0])
                ink_right = max(ink_right, pen + ink[0] + ink[2])
            members[i] = glyph_index[ord(ch)]
            offsets[i] = pen
            pen += glyph["advance_px"]
            has_wave = has_wave or ord(ch) in wave
        flags = (RENDERER_GD["IDIOM_ALNUM"] if ALNUM.search(text) else 0) \
            | (RENDERER_GD["IDIOM_NOT_CREST_PAIR"] if has_wave and text != crest_pair else 0)
        terminal = terminal_axes.get(text, {})
        compact = str(terminal.get("compact", ""))
        part = terminal_parts.get(text, {})
        part_word = 0
        if part:
            part_word = (int(part["anchor_x2"]) + 1) << 16
            if str(part.get("compact", "")):
                part_word |= sequence_index[str(part["compact"])] + 1
        idiom_bytes += struct.pack("<4i", len(text), pen, ink_left, ink_right)
        idiom_bytes += struct.pack("<4i", *members) + struct.pack("<4i", *offsets)
        idiom_bytes += struct.pack("<4I", flags, sequence_index[compact] + 1 if compact else 0,
                                   int(terminal.get("language_mask", 0)), part_word & 0xFFFFFFFF)
    languages = authored["languages"]
    run_bytes = b""
    representative_sets: dict = {}
    for row in product["run_rows"]:
        language_id = int(languages[str(row["language"])])
        dots = representative_sets.setdefault(language_id, [])
        for ch in str(row["sequence"]):
            if ord(ch) in (39, 44, 46, 96, 12289, 65344, 65380) and glyph_index[ord(ch)] not in dots:
                dots.append(glyph_index[ord(ch)])
        substitute = str(row["substitute"])
        run_bytes += struct.pack("<4i", int(row["role_id"]), language_id, int(row["flags"]), int(row["detail"]))
        run_bytes += f32s(*row["lo"][:4], *row["hi"][:4])
        run_bytes += struct.pack("<4i", sequence_index[str(row["sequence"])],
                                 sequence_index[substitute] if substitute else -1, int(row["phase_advance_px"]), 0)
    language_words = []
    for source_kind in range(RENDERER_GD["SOURCE_KINDS"]):
        default_name = str(authored["source_languages"].get(str(source_kind), "hard"))
        for material in range(256):
            selected = default_name
            for binding in authored["material_languages"]:
                if int(binding["material"]) == material:
                    selected = str(binding["language"])
            language_words.append(int(languages[selected]))
    representative_words = [0] * (16 * 8)
    for language_id, dots in representative_sets.items():
        representative_words[language_id * 8] = len(dots)
        for member, glyph in enumerate(dots):
            representative_words[language_id * 8 + member + 1] = glyph
    return {
        "glyphs": glyph_bytes, "glyph_count": len(repertoire),
        "idioms": idiom_bytes, "idiom_count": len(sequences),
        "runs": run_bytes, "run_count": len(product["run_rows"]),
        "run_bindings": struct.pack("<%di" % len(product["binding_words"]), *product["binding_words"]),
        "depiction_span_count": len(product["binding_words"]) // 4,
        "representatives": struct.pack("<%di" % len(representative_words), *representative_words),
        "languages": struct.pack("<%di" % len(language_words), *language_words),
        "part_timings": timing, "fold_rules": product["fold_bytes"],
        "atlas_rgba8": font["image"], "atlas_size": (font["width"], font["height"]),
        "row_pitch": RENDERER_GD["ROW_PITCH"], "half_advance": half, "full_advance": full,
        "minimum_advance": min_advance, "crest_idiom": crest, "native_origin": (0.0, 0.0),
        "glyph_codepoints": repertoire,
    }


# ------------------------------------------------------------ terrain world

def _parse_bank(content_hash: str) -> dict:
    """TrueFFFeatureBank.parse_bank for one cached bank file."""
    path = PROJ / "assets/world_partition/true_ff_feature_bank" / f"{content_hash}.v1.bin"
    if not path.is_file():
        raise PublicationError(f"feature_bank_missing:{content_hash}")
    data = path.read_bytes()
    if data[:8] != FEATURE_ABI["FILE_MAGIC"].encode() or struct.unpack_from("<I", data, 8)[0] != FEATURE_ABI["FILE_VERSION"]:
        raise PublicationError(f"feature_bank_header_invalid:{content_hash}")
    header_len = struct.unpack_from("<I", data, 12)[0]
    header = json.loads(data[16:16 + header_len].decode("utf-8"))
    if header.get("schema") != FEATURE_ABI["SCHEMA"] or header.get("content_hash") != content_hash:
        raise PublicationError(f"feature_bank_schema_invalid:{content_hash}")
    strides = {"nodes": FEATURE_ABI["NODE_BYTES"], "features": FEATURE_ABI["FEATURE_BYTES"],
               "faces": FEATURE_ABI["FACE_BYTES"]}
    counts = {"nodes": int(header["node_count"]), "features": int(header["feature_count"]),
              "faces": int(header["face_count"])}
    if header.get("kind") == "terrain":
        dims = header["terrain_dims"]
        strides["terrain_classes"] = FEATURE_ABI["TERRAIN_CLASS_BYTES"]
        counts["terrain_classes"] = (int(dims[0]) + 1) * (int(dims[1]) + 1)
        if header.get("encoding") != FEATURE_ABI["TERRAIN_ENCODING"] or counts["features"] or counts["faces"] \
                or int(header.get("leaf_cells", -1)) != TERRAIN_TREE_GD["LEAF"]:
            raise PublicationError(f"terrain_bank_encoding_invalid:{content_hash}")
    out = {"header": header}
    for name, stride in strides.items():
        section = header["sections"].get(name, {})
        offset, size = int(section.get("offset", -1)), int(section.get("bytes", -1))
        if offset < 0 or size != counts[name] * stride or offset + size > len(data):
            raise PublicationError(f"feature_bank_section_invalid:{content_hash}:{name}")
        out[name] = data[offset:offset + size]
        out[name + "_count"] = counts[name]
    levels = [struct.unpack_from("<I", out["nodes"], n * 64 + 52)[0] for n in range(counts["nodes"])]
    if not levels or max(levels) >= FEATURE_ABI["HIERARCHY_LEVELS"] or int(header.get("depth", -1)) != max(levels):
        raise PublicationError(f"feature_bank_levels_invalid:{content_hash}")
    out["max_level"] = max(levels)
    return out


def material_library(map_path: Path = DEFAULT_MAP) -> dict:
    """TerrainMaterialPalette.load_from_a3d_material_block/to_matlib_image_texture bytes."""
    data = map_path.read_bytes()
    if len(data) < 16 or data[:4] != b"AS3D":
        raise PublicationError("a3d_material_library_header_invalid")
    header_size, patch_count = struct.unpack_from("<II", data, 4)
    materials = int(PALETTE_GD["MATERIAL_COUNT"])
    rows = int(PALETTE_GD["SHADE_ROWS"])
    shades = int(PALETTE_GD["SHADE_COLS"])
    cell_bytes = int(PALETTE_GD["MAT_CELL_BYTES"])
    block_start = header_size + patch_count * int(A3D_GD["PATCH_BYTES"])
    block_size = materials * rows * shades * cell_bytes
    block = data[block_start:block_start + block_size]
    if header_size != 16 or patch_count <= 0 or len(block) != block_size:
        raise PublicationError("a3d_material_library_missing")
    slice_height = rows * shades
    image = bytearray(materials * slice_height * 3 * 4)
    for material in range(materials):
        for row in range(rows):
            for shade in range(shades):
                source = (material * rows * shades + row * shades + shade) * cell_bytes
                y = row * shades + shade
                for slice_index, rgba in enumerate((
                        (block[source + 3], block[source + 7], 0, 255),
                        (block[source], block[source + 1], block[source + 2], 255),
                        (block[source + 4], block[source + 5], block[source + 6], 255))):
                    target = ((slice_index * slice_height + y) * materials + material) * 4
                    image[target:target + 4] = bytes(rgba)
    return {"width": materials, "height": slice_height * 3, "rgba8": bytes(image)}


def _terrain_parents(bank: dict) -> bytes:
    """TrueFFFeatureBank._surface_asset_topology, terrain branch."""
    nodes = bank["nodes"]
    count = bank["nodes_count"]
    parents = [-1] * count
    for node in range(count):
        at = node * 64
        level = struct.unpack_from("<I", nodes, at + 52)[0]
        first, children = struct.unpack_from("<2I", nodes, at + 32)
        if level >= FEATURE_ABI["HIERARCHY_LEVELS"] or first + children > count or children > 4:
            raise PublicationError("terrain_surface_topology_invalid")
        for child in range(first, first + children):
            if child <= node or parents[child] != -1 \
                    or struct.unpack_from("<I", nodes, child * 64 + 52)[0] != level + 1:
                raise PublicationError("terrain_surface_topology_invalid")
            parents[child] = node
    if -1 in parents[1:]:
        raise PublicationError("terrain_surface_topology_invalid")
    return struct.pack("<%di" % count, *parents)


def _radix_put(radix: bytearray, counts: list, row: list, slot: int) -> None:
    """TrueFFFeatureBank._surface_index_put on a fresh trie."""
    node_bytes = BANK_GD["SURFACE_RADIX_NODE_BYTES"] if "SURFACE_RADIX_NODE_BYTES" in BANK_GD else 1024

    def key_byte(depth: int) -> int:
        word = int(row[0] if depth < 4 else row[1])
        return (word >> (24 - (depth % 4) * 8)) & 255

    def write(node: int, byte: int, value: int) -> None:
        at = node * node_bytes + byte * 4
        if struct.unpack_from("<I", radix, at)[0] == 0 and value != 0:
            counts[node] += 1
        struct.pack_into("<I", radix, at, value)

    node = 0
    for depth in range(8):
        byte = key_byte(depth)
        if depth == 7:
            write(node, byte, slot + 1)
            return
        child = struct.unpack_from("<I", radix, node * node_bytes + byte * 4)[0]
        if child == 0:
            new = len(counts)
            counts.append(0)
            radix.extend(bytes(node_bytes))
            child = new + 1
            write(node, byte, child)
        node = child - 1


def _pack_instance(node_base, feature_base, face_base, object_id, max_displacement, max_deviation,
                   node_count, feature_count, source_namespace) -> bytes:
    """TrueFFFeatureABI.pack_instance for an identity transform and no figure/effect union."""
    out = bytearray(FEATURE_ABI["INSTANCE_BYTES"])
    for column in range(3):
        struct.pack_into("<4f", out, column * 16, *[1.0 if row == column else 0.0 for row in range(3)], 0.0)
    struct.pack_into("<4f", out, 48, 0.0, 0.0, 0.0, 1.0)
    struct.pack_into("<4I", out, FEATURE_ABI["INSTANCE_OFF_REFS"], node_base, feature_base, face_base, object_id)
    struct.pack_into("<2f2I", out, FEATURE_ABI["INSTANCE_OFF_DEFORM"], max_displacement, max_deviation,
                     node_count, feature_count)
    struct.pack_into("<4I", out, FEATURE_ABI["INSTANCE_OFF_IDENTITY"], source_namespace, 0, 0, 0)
    return bytes(out)


def terrain_world(map_path: Path = DEFAULT_MAP) -> dict:
    """SceneGeometryDB terrain facts + TrueFFFeatureBank cold publication (terrain only)."""
    doc = BANK._extension("true_ff_terrain_sources").map_source(str(map_path), SimpleNamespace(**vars(BANK)))
    dims = (int(doc["dims"][0]), int(doc["dims"][1]))
    origin = (float(doc["origin"][0]), float(doc["origin"][1]))
    cell, water_level = float(doc["cell"]), float(doc["water_level"])
    heights = doc["heights"]
    materials = doc.get("materials") or [0] * (dims[0] * dims[1])
    elevations = doc.get("elevations")
    if len(heights) != (dims[0] + 1) * (dims[1] + 1):
        raise PublicationError("terrain_corner_heights_shape_invalid")
    if not isinstance(elevations, list) or len(elevations) != dims[0] * dims[1]:
        raise PublicationError("terrain_elevation_rows_shape_invalid")
    digest = BANK.terrain_content_hash(heights, dims, origin, cell, water_level, doc.get("materials"))
    bank = _parse_bank(digest)
    header = bank["header"]
    if [int(v) for v in header["terrain_dims"]] != list(dims) \
            or not all(math.isclose(float(a), b) for a, b in zip(header["terrain_origin"], origin)) \
            or not math.isclose(float(header["terrain_cell"]), cell) \
            or not math.isclose(float(header["water_level"]), water_level):
        raise PublicationError("terrain_header_mismatch")
    terrain_object = STRUCTURE_ABI["P0_TERRAIN_OBJECT_ID"]
    node_count = bank["nodes_count"]
    arena = BANK_GD["ARENA_MIN_UNITS"]
    table = BANK_GD["TABLE_MIN_SLOTS"]
    # Static region (one terrain slice) + zeroed arena capacity per section.
    nodes = bank["nodes"] + bytes(arena * FEATURE_ABI["NODE_BYTES"])
    features = bytes(arena * FEATURE_ABI["FEATURE_BYTES"])
    faces = bytes(arena * FEATURE_ABI["FACE_BYTES"])
    surface_topology = _terrain_parents(bank)
    surface = surface_topology + bytes(arena * 4)
    vegetation = bytes(arena * VEGETATION_GD["RECORD_BYTES"])
    # Namespaces: scene static (1), scene nonstatic (2), terrain source (3).
    namespaces = (1, 2)
    terrain_namespace = 3
    instance = _pack_instance(0, 0, 0, terrain_object, 0.0, 0.0, node_count, 0, terrain_namespace)
    instances = instance + bytes((table - 1) * FEATURE_ABI["INSTANCE_BYTES"])
    # Surface metadata row: surface_refs then family/part; lookup [terrain object, 0, 1].
    surface_row = u32s(0, 0, 0, 0, 1, 0, 0, 0)
    radix = bytearray(1024)
    radix_counts = [0]
    _radix_put(radix, radix_counts, [terrain_object, 0, 1], 0)
    radix_at = 16 + table * 32
    capacity = 1024
    while capacity < radix_at + len(radix):
        capacity *= 2
    surface_instances = bytearray(capacity)
    surface_instances[0:16] = u32s(8, radix_at // 16, 0, 1)
    surface_instances[16:48] = surface_row
    surface_instances[radix_at:radix_at + len(radix)] = radix
    roots = u32s(0, 0, 0, 0) + bytes((table - 1) * 16)
    classes = bank["terrain_classes"]
    classes += bytes(max(4, (len(classes) + 3) // 4 * 4) - len(classes))
    leaf_cells = TERRAIN_TREE_GD["LEAF"]
    leaf_side = 1
    while leaf_side * leaf_cells < max(dims):
        leaf_side *= 2
    digest_state = FEATURE_ABI["FNV_OFFSET"]
    for byte in bytes.fromhex(digest):
        digest_state = ((digest_state ^ byte) * FEATURE_ABI["FNV_PRIME"]) & 0xFFFFFFFF
    params = u32s(dims[0], dims[1], dims[0] + 1, digest_state) + f32s(origin[0], origin[1], cell, water_level) \
        + u32s(leaf_cells, leaf_side, 0, node_count)
    max_level = bank["max_level"]
    height_min, height_max = min(heights), max(heights)
    slice_ = {"node_base": 0, "feature_base": 0, "face_base": 0, "node_count": node_count,
              "feature_count": 0, "face_count": 0, "max_level": max_level, "terrain": True}
    return {
        "digest": digest, "dims": dims, "origin": origin, "cell": cell, "water_level": water_level,
        "heights": heights, "materials": materials, "elevations": elevations,
        "height_min": height_min, "height_max": height_max,
        "bank_header": header,
        "buffers": {"nodes": nodes, "features": features, "faces": faces, "instances": instances,
                    "figure_bindings": u32s(4, 1, 0, 0), "vegetation_features": vegetation,
                    "surface_topology": surface, "surface_instances": bytes(surface_instances),
                    "terrain_classes": classes, "terrain_params": params,
                    "static_nodes": bytes(32), "static_leaf_ids": bytes(4),
                    "static_leaf_instances": bytes(4), "roots": roots},
        "facts": {
            "instance_count": 1, "instance_capacity": table, "node_total": len(bank["nodes"]) // 64 + arena,
            "feature_total": arena, "face_total": arena, "instance_node_total": node_count,
            "instance_feature_total": 0, "admission_node_total": node_count,
            "instance_node_ranges": u32s(0, 0), "admission_history_key": "offline:1",
            "max_level": max_level, "change_serial": 3, "change_floor": 0,
            "assets": [slice_], "figure_binding_words": 4, "source_tick": 0,
            "hierarchy": {"static_node_count": 0, "static_leaf_count": 0, "root_count": 1,
                          "root_capacity": table, "generation": 1, "scene_owner": 1,
                          "residency_generation": 1, "static_max_displacement": 0.0,
                          "static_bounds_unbounded": False},
            "scene_ingress": {"serial": 1, "floor": 0, "changes": [[1, 0]],
                              "namespaces": namespaces, "root_capacity": table},
        },
    }


# ------------------------------------------------------------ camera

def _mat_mul(a: list, b: list) -> list:
    """Column-major 4x4 product a * b (Godot Projection operator*)."""
    return [[sum(a[k][row] * b[col][k] for k in range(4)) for row in range(4)] for col in range(4)]


def mat_inverse(m: list) -> list:
    """General 4x4 inverse of a column-major matrix (Gauss-Jordan)."""
    rows = [[m[c][r] for c in range(4)] + [1.0 if r == i else 0.0 for i in range(4)] for r in range(4)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(rows[r][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        value = rows[col][col]
        if value == 0.0:
            raise PublicationError("camera_matrix_singular")
        rows[col] = [v / value for v in rows[col]]
        for r in range(4):
            if r != col:
                factor = rows[r][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    return [[rows[r][4 + c] for r in range(4)] for c in range(4)]


def camera_facts(position, yaw: float, pitch: float, fov: float, viewport: tuple,
                 near: float = 0.1, far: float = 4000.0, frame_time: float = 0.0) -> dict:
    """CameraFrameState.live_true_perspective_projection + TrueFFCompositorEffect._camera_facts
    + TrueFFPagePhase.page_window for a perspective camera."""
    forward = (-math.sin(yaw) * math.cos(pitch), math.sin(pitch), -math.cos(yaw) * math.cos(pitch))
    z = tuple(-v for v in forward)
    up = (0.0, 1.0, 0.0)
    x = (up[1] * z[2] - up[2] * z[1], up[2] * z[0] - up[0] * z[2], up[0] * z[1] - up[1] * z[0])
    length = math.sqrt(sum(v * v for v in x))
    x = tuple(v / length for v in x)
    y = (z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0])
    origin = tuple(float(v) for v in position)
    # Projection.create_perspective(fov, aspect, near, far) with KEEP_HEIGHT.
    aspect = viewport[0] / viewport[1]
    radians = math.radians(fov / 2.0)
    cotangent = math.cos(radians) / math.sin(radians)
    delta = far - near
    perspective = [[cotangent / aspect, 0, 0, 0], [0, cotangent, 0, 0],
                   [0, 0, -(far + near) / delta, -1.0], [0, 0, -2.0 * near * far / delta, 0.0]]
    # Projection.create_depth_correction(true): flip y, reverse z, remap to [0,1].
    correction = [[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, -0.5, 0], [0, 0, 0.5, 1]]
    projection = _mat_mul(correction, perspective)
    # pose.affine_inverse() as a column-major matrix.
    view = [[x[0], y[0], z[0], 0.0], [x[1], y[1], z[1], 0.0], [x[2], y[2], z[2], 0.0],
            [-sum(a * b for a, b in zip(x, origin)), -sum(a * b for a, b in zip(y, origin)),
             -sum(a * b for a, b in zip(z, origin)), 1.0]]
    world_to_clip = _mat_mul(projection, view)
    margin = (22, 17)  # TrueFFPagePhase.PAGE_MARGIN_PX
    extent = (viewport[0] + 2 * margin[0], viewport[1] + 2 * margin[1])
    expand = [[viewport[0] / extent[0], 0, 0, 0], [0, viewport[1] / extent[1], 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    matrix = _mat_mul(expand, world_to_clip)
    camera_to_world = [[x[0], x[1], x[2], 0.0], [y[0], y[1], y[2], 0.0], [z[0], z[1], z[2], 0.0],
                       [origin[0], origin[1], origin[2], 1.0]]
    lens = _mat_mul(matrix, camera_to_world)
    tan_half = 1.0 / abs(lens[1][1])
    return {"world_to_clip": matrix, "inv_view_proj": mat_inverse(matrix), "origin": origin,
            "forward": forward, "camera_near": near, "camera_far": far, "is_ortho": False,
            "tan_half_fov": tan_half, "camera_fov": math.degrees(2.0 * math.atan(tan_half)),
            "ortho_half_height": 0.0, "frame_time_seconds": frame_time, "page_extent": extent,
            "viewport": viewport, "page_origin": margin, "translation": (-margin[0], -margin[1])}


def sun_direction(time_of_day: float) -> tuple:
    """CelestialFrameState.sun_direction with its authored horizon heading."""
    h = (0.7378, 0.0, 0.6639)
    length = math.sqrt(h[0] * h[0] + h[2] * h[2])
    h = (h[0] / length, 0.0, h[2] / length)
    phi = (time_of_day - 0.25) * 2.0 * math.pi
    v = (h[0] * math.cos(phi), math.sin(phi), h[2] * math.cos(phi))
    length = math.sqrt(sum(c * c for c in v))
    return tuple(c / length for c in v)
