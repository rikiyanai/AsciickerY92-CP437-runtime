"""FL-4907 N5/N10/N12 (GPU_RAIN_EFFECTS_AND_FAMILY_OMISSION_20261002).

Source-ownership contracts for the True FF GPU weather field:
- the FF effects family publishes one retained template source per authored
  rain/snow part and never reads per-particle weather facts;
- the GPU accessor owns the rain law, landing, tier, alive window and keyframe;
- the host packs the set-1 field block in the byte order the GLSL declares;
- the canopy landing query reads a cell index, not every box;
- one rejected effect source is omitted by name inside the effects family.

These are static text contracts. They do not compile shaders or run Godot.
"""
from __future__ import annotations

import re
import struct
import math
from pathlib import Path

from scripts.tests.test_true_ff_end_to_end_repairs import gd_body, gd_syntax
from scripts.tests.test_true_ff_figure_transport import D, Bytes

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"
WEATHER = ROOT / "godot_project/scripts/world/weather"
EFFECTS = FEATURES / "TrueFFEffectFeatures.gd"
FIELD_GLSL = FEATURES / "true_ff_effect_field_source.glslinc"
LIVE_SOURCE = FEATURES / "true_ff_live_feature_source.glslinc"
BINDINGS = FEATURES / "TrueFFLiveSourceBindings.gd"
WORLD_SOURCES = FEATURES / "TrueFFWorldFeatureSources.gd"
REPRESENTATION = FEATURES / "TrueFFPartRepresentation.gd"
TRAVERSE = FEATURES / "true_ff_feature_traverse.glslinc"
RAIN = WEATHER / "RainPresentationBuilder.gd"
CANOPY = WEATHER / "RainCanopyAdmission.gd"


def _body(text: str, signature: str) -> str:
    return text.split(signature, 1)[1].split("\nfunc ", 1)[0]


def test_ff_effects_never_build_weather_particles_on_the_host():
    effects = EFFECTS.read_text(encoding="utf-8")
    for deleted in ("func _weather(", "func _weather_collision_children(", "_collision_parts",
                    "source_children\"", "impact_progress"):
        assert deleted not in effects, deleted
    rain = _body(effects, "func _describe_source(")
    rain = rain.split('if kind == "rain":', 1)[1].split('if kind == "projectile":', 1)[0]
    assert 'fact.has("rain_field")' in rain and "_rain_field(" in rain
    # The only per-particle host work left on this kind is the bounded fly MARK.
    assert "_fly(sources, particle_value, object_id)" in rain
    field = _body(effects, "func _rain_field(")
    assert "Basis(" not in field.replace("Basis.IDENTITY", "")
    assert "particles" not in field
    assert "select_authored_part" not in effects


def test_one_template_bank_serves_every_field_part():
    effects = EFFECTS.read_text(encoding="utf-8")
    bank = _body(effects, "func _field_bank(")
    assert "StrokeBank.compile(" in bank and "_banks.has(key)" in bank
    parts = re.findall(r"\[FIELD_LAW_[A-Z_]+, \d, \"GODOT-E\d\d\", \"([^\"]+)\", \"([^\"]+)\"", effects)
    assert len(parts) == 7
    assert parts == [("authored.sky.rain.drop", "D"), ("authored.sky.rain.splash", "S"),
                     ("authored.sky.rain.splash", "L"), ("authored.sky.rain.splash", "R"),
                     ("authored.sky.rain.splash_water", "S"), ("authored.sky.rain.splash_water", "W"),
                     ("authored.sky.rain.snow", "F")]
    assert "FIELD_LAW_CHILD" not in effects and "GODOT-E11" not in effects.split("const FIELD_PARTS:", 1)[1].split("\n]", 1)[0]
    assert 'field.get("collision_child_count"' not in _body(effects, "func _rain_field(")
    assert "TFF_FIELD_LAW_CHILD" not in FIELD_GLSL.read_text()


def test_field_uniform_bytes_match_the_glsl_block():
    glsl = FIELD_GLSL.read_text(encoding="utf-8")
    block = glsl.split("uniform TffEffectField {", 1)[1].split("} tff_effect_field;", 1)[0]
    members = re.findall(r"^\s*(u?vec4) (\w+);", block, re.M)
    assert members == [("vec4", "window"), ("vec4", "timing"), ("vec4", "extent"),
                       ("vec4", "motion"), ("vec4", "contact"), ("uvec4", "state")]
    effects = EFFECTS.read_text(encoding="utf-8")
    assert "const FIELD_UNIFORM_BYTES: int = 96" in effects
    field = gd_body(effects, "_field_uniform")
    words = field.split("var words: Array = [", 1)[1].split("]\n", 1)[0]
    assert len([w for w in words.replace("\n", " ").split(",") if w.strip()]) == 20
    for offset in (80, 84, 88, 92):
        assert f"bytes.encode_u32({offset}," in field
    assert "layout(set=1,binding=7,std140) uniform TffEffectField" in glsl
    assert "layout(set=1,binding=8,std430) readonly buffer TffEffectFieldTables" in glsl


def test_live_bindings_publish_bindings_seven_and_eight():
    bindings = BINDINGS.read_text(encoding="utf-8")
    keys = re.search(r"const BINDINGS: Array\[String\] = (\[.*?\])", bindings, re.S).group(1)
    assert re.findall(r'"([^\"]+)"', keys)[7:9] == ["effect_field", "effect_tables"]
    assert '"flow_terrain", "effect_tables"]' in bindings
    assert '_prepare_effects(facts.get("field_bindings", {}))' in bindings
    world = WORLD_SOURCES.read_text(encoding="utf-8")
    assert '_bindings.call("prepare_family", family, facts)' in world
    assert '_bindings.call("update_family", family, facts, _sea)' in world


def test_gpu_accessor_owns_law_and_representation_selection():
    glsl = FIELD_GLSL.read_text(encoding="utf-8")
    live = LIVE_SOURCE.read_text(encoding="utf-8")
    assert '#include "res://render/ascii/features/true_ff_effect_field_source.glslinc"' in live
    assert "f=tff_effect_field_feature(inst,address,f);" in live
    assert "subtype==TFF_EFFECT_FIELD_PART" in live
    assert "glyph=tff_effect_field_idiom(f);" in live
    for owner in ("tff_field_event(", "tff_field_canopy(", "tff_field_terrain(", "tff_field_rows("):
        assert owner in glsl
    selection = glsl.split("uint row=1u+part*TFF_FIELD_PART_ROWS;", 1)[1]
    assert "tier<uint(head.x)" in selection and "present" in selection and "key.x<=part_time" in selection
    # A multi-feature point instance owes one site per feature, not one total.
    assert "if (tff_effect_point(inst)) return max(node.links.w, 1u);" in TRAVERSE.read_text(encoding="utf-8")


def test_rain_hash_salts_match_the_non_ff_owner():
    rain = RAIN.read_text(encoding="utf-8")
    glsl = FIELD_GLSL.read_text(encoding="utf-8")
    host = set(re.findall(r"_noise\((?:seed|column_seed), (\d+)\)", rain))
    gpu = set(re.findall(r"tff_field_noise\((?:e\.seed|column_seed),(\d+)u\)", glsl))
    assert {"71", "53", "19", "37", "83", "23", "31"} <= host
    assert host <= gpu | {"97"}  # 97 is the non-FF shade only
    for constant in ("374761393", "668265263", "1274126177", "83492791"):
        assert constant in rain and constant in glsl
    assert "0.04 + 0.50 * intensity * intensity" in rain
    assert "0.04+0.50*intensity*intensity" in glsl


def test_rain_builder_keeps_only_non_ff_particle_facts_and_publishes_the_field():
    rain = RAIN.read_text(encoding="utf-8")
    for deleted in ("func _collision_children(", '"landing_normal"', '"collision_children"',
                    '"trajectory_velocity"', '"event_epoch"'):
        assert deleted not in rain, deleted
    assert '"rain_field": field_descriptor(' in rain
    assert "static func field_descriptor(" in rain


def test_canopy_landing_reads_one_cell_and_no_neighbour_queries():
    canopy = CANOPY.read_text(encoding="utf-8")
    contact = _body(canopy, "func canopy_contact_at(")
    assert "for box in canopy_boxes(" not in contact
    assert "_grid_ranges[range_at + 1]" in contact
    assert "func _land_normal(" not in canopy
    surface = _body(canopy, "func surface_fact(")
    assert surface.count("base_surface.call(") == 1


def test_one_bad_effect_source_is_omitted_inside_the_family():
    effects = EFFECTS.read_text(encoding="utf-8")
    tail = _body(effects, "func apply_changes(")
    assert '"ready": true' in tail and '"omitted_sources": _errors' in tail
    assert 'sources.clear()' in tail and '_errors[str(object_id)] = rejection' in tail
    assert '"ready": errors.is_empty()' not in effects
    world = WORLD_SOURCES.read_text(encoding="utf-8")
    assert 'omitted[family + ".sources"] = errors' in world


def test_inherited_recipe_fields_have_one_owner():
    assert "static func definition_field(" in REPRESENTATION.read_text(encoding="utf-8")
    effects = EFFECTS.read_text(encoding="utf-8")
    assert "func _definition_array(" not in effects
    assert "StrokeBank.authored_part(" in effects
    representation = REPRESENTATION.read_text()
    assert 'definition_field(binding["definition_path"], "tiers_rows")' in representation
    assert 'definition_field(binding["definition_path"], "ink_rgb")' in representation
    assert "Representation.authored_definition(" in effects


def test_runtime_visual_eligibility_rejects_retained_historical_recipe_only():
    source = REPRESENTATION.read_text()
    body = gd_syntax(gd_body(source, "_visual_eligible"))
    body = body.replace("audit is Dictionary", "isinstance(audit, dict)")
    state = {"String": str}
    exec("def eligible(feature):\n" + body, state)
    for disposition in ("DROP_VISUAL_TREATMENT", "RETAIN_NONVISUAL_BEHAVIOR"):
        assert not state["eligible"]({"manual_audit": {"disposition": disposition}})
    assert not state["eligible"]({"visual_eligible": False})
    for audit in (None, {"disposition": "ADAPT_TO_AA_STYLE"}, {"disposition": "PRESERVE_MINIMAL_VISUAL_CHANGE"}):
        assert state["eligible"]({"manual_audit": audit})
    early = gd_body(source, "_authored_definition").split("\tvar runtime:", 1)[0]
    early = early[early.index("\tvar depictions:"):]
    early = gd_syntax(early).replace(" as Array", "")
    state["_visual_eligible"] = state["eligible"]
    exec("def definition(outline,feature_id,table_name):\n" + early, state)
    historical = D(feature_depictions=D(direct=D({"GODOT-E11": D(visual_eligible=False)})))
    assert state["definition"](historical, "GODOT-E11", "authored.sky.rain.fragments")["reason"] == "true_ff_visual_depiction_ineligible"


def test_production_field_uniform_does_not_require_or_emit_collision_children():
    class Vector3:
        def __init__(self): self.x, self.y, self.z = 1.0, -2.0, 0.0
        def is_finite(self): return True
    class Packed(Bytes):
        def encode_float(self, at, value): struct.pack_into("<f", self, at, value)
    body = gd_syntax(gd_body(EFFECTS.read_text(), "_field_uniform"))
    body = body.replace(" as Vector3", "").replace("not velocity_value is Vector3", "not isinstance(velocity_value, Vector3)")
    body = body.replace("words[lane] is int", "isinstance(words[lane], int)").replace("words[lane] is float", "isinstance(words[lane], float)")
    state = {"Vector3": Vector3, "PackedByteArray": Packed, "FIELD_UNIFORM_BYTES": 96,
             "NAN": math.nan, "is_finite": math.isfinite}
    exec("def pack(field):\n" + body, state)
    fields = ("clock", "intensity", "site_size", "period", "fall_seconds", "gap_seconds",
              "ripple_seconds", "fall_height", "ink_half_width", "ink_half_height",
              "ripple_start_radius", "ripple_end_radius", "ripple_half_width", "contact_lift", "fall_drift")
    field = {name: 1.0 for name in fields}; field.update(fall_velocity=Vector3(), resident_radius=4, snow=False)
    without = state["pack"](field)
    assert len(without) == 96 and struct.unpack_from("<I", without, 92)[0] == 0
    assert struct.unpack_from("<f", without, 60)[0] == 0.0
    field.update(collision_child_count=3, collision_reflection_damp=0.4)
    assert state["pack"](field) == without


def test_smoke_consumes_the_accepted_contour_frame_once():
    class Vector:
        def __init__(self, finite=True): self.finite = finite
        def is_finite(self): return self.finite
    class Basis:
        def __init__(self): self.x = self.y = self.z = Vector()
        def determinant(self): return 1.0
    class Transform3D:
        def __init__(self): self.origin, self.basis = Vector(), Basis()
    body = gd_syntax(gd_body(EFFECTS.read_text(), "_smoke"))
    body = body.replace("not frame_value is Transform3D", "not isinstance(frame_value, Transform3D)")
    calls = []
    state = {"Transform3D": Transform3D, "NAN": math.nan, "is_finite": math.isfinite,
             "_authored_parts": lambda *args: calls.append(args) or ""}
    exec("def smoke(sources,fact,object_id):\n" + body, state)
    frame = Transform3D()
    # No consumer radius/position is required: the accepted support owns both.
    fact = {"effect_age_seconds": 2.0, "fade_seconds": 4.0, "effect_contour_transform": frame}
    assert state["smoke"]([], fact, 17) == ""
    assert len(calls) == 1 and calls[0][3] is frame and calls[0][9] == 0.5
    frame.origin = Vector(False)
    assert state["smoke"]([], fact, 17) == "smoke_accepted_contour_frame_invalid"
    assert len(calls) == 1
    producer = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
    sample = gd_body(producer, "_append_dynamic_effect_sample")
    assert "center + Vector3.UP * rise" in sample and 'bool(fact.get("cell_particle", false))' in sample
    assert "frame.basis.x.normalized()" not in body and "age * 0.7" not in body
