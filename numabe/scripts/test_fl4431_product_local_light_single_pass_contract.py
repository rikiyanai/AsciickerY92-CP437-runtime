#!/usr/bin/env python3
"""FL-4431 Product binding-12 single-pass source contract.

The Product finalizer historically scanned the same immutable local-light
record table once for lantern admission, once for the lighthouse beam, then
once for point illumination. This contract pins the exact-output replacement:
one typed collector scans rows in ascending order, retains the independent
facts, then the finalizer applies lantern paint, beam paint, and point-light
paint in the established order.

The checks are deliberately destructive. Every mutant below remains valid GLSL
source shape while changing ownership, admission, selection, shadowing, count
handling, or paint order. The contract must reject every one. This is static
evidence; it does not claim runtime visuals or performance.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from test_fl4431_vsb_activation_contract import glsl_reachable_functions

COLLECTOR = "vsb_product_collect_local_light_slot_facts"
FINALIZER = "vsb_product_finalize_record"
LANTERN = "vsb_product_apply_lighthouse_lantern_style"
DOWNWASH = "foliage_downwash_boost"

RETIRED_OWNERS = (
    "local_light_first_beam_record_index",
    "compute_lighthouse_beam_cell_product",
    "vsb_product_local_light_addition",
    "vsb_product_bonfire_light_contributed",
    "lighthouse_cone_from_record",
)


def without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def compact(source: str) -> str:
    return re.sub(r"\s+", "", without_comments(source))


def glsl_function(source: str, name: str) -> str:
    """Return one GLSL function definition using balanced braces."""
    clean = without_comments(source)
    match = re.search(
        rf"(?m)^\s*(?:precise\s+)?[A-Za-z_]\w*\s+"
        rf"{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        clean,
        re.S,
    )
    if match is None:
        return ""
    brace = clean.find("{", match.start())
    depth = 0
    for index in range(brace, len(clean)):
        if clean[index] == "{":
            depth += 1
        elif clean[index] == "}":
            depth -= 1
            if depth == 0:
                return clean[match.start() : index + 1]
    return ""


def definition_count(source: str, name: str) -> int:
    clean = without_comments(source)
    return len(
        re.findall(
            rf"(?m)^\s*(?:precise\s+)?[A-Za-z_]\w*\s+"
            rf"{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
            clean,
            re.S,
        )
    )


def ordered(body: str, tokens: tuple[str, ...]) -> bool:
    cursor = -1
    for token in tokens:
        cursor = body.find(token, cursor + 1)
        if cursor < 0:
            return False
    return True


def contract_errors(
    source: str,
    *,
    check_reachability: bool = True,
) -> tuple[str, ...]:
    """Return every divergence from the selected single-pass law."""
    errors: list[str] = []
    clean = without_comments(source)
    collector = compact(glsl_function(source, COLLECTOR))
    finalizer = compact(glsl_function(source, FINALIZER))
    lantern = compact(glsl_function(source, LANTERN))
    downwash = compact(glsl_function(source, DOWNWASH))
    glyph_light = compact(
        glsl_function(source, "compute_local_light_glyph_cell_product")
    )

    if definition_count(source, COLLECTOR) != 1:
        errors.append("typed local-light collector definition count drifted")
    if definition_count(source, FINALIZER) != 1:
        errors.append("Product finalizer definition count drifted")
    if definition_count(source, LANTERN) != 1:
        errors.append("lantern style owner definition count drifted")
    for retired in RETIRED_OWNERS:
        if retired in clean:
            errors.append(f"retired binding-12 owner survived: {retired}")

    collector_calls = len(re.findall(rf"\b{re.escape(COLLECTOR)}\s*\(", clean))
    if collector_calls != 2:
        errors.append("collector must have one definition plus one live call")
    if check_reachability:
        reachable = glsl_reachable_functions(source)
        if FINALIZER not in reachable or COLLECTOR not in reachable:
            errors.append("collector must remain reachable from Product main")
    if collector.count("for(") != 1:
        errors.append("collector must contain one binding-12 scan loop")
    if "precise" in collector:
        errors.append("collector introduced the prohibited traversal qualifier")
    if (
        "VSB_PROJECTION_PERSPECTIVE" in collector
        or "VSB_PROJECTION_PERSPECTIVE" in finalizer
        or "VSB_PROJECTION_PERSPECTIVE" in glyph_light
        or "product_is_perspective" in collector
        or "product_is_perspective" in finalizer
        or "product_is_perspective" in glyph_light
    ):
        errors.append("local-light paint acquired a projection fence")

    count_law = (
        "if(!local_light_records_enabled())returnfacts;",
        "intraw_count=local_light_record_count();",
        "intscan_count=clamp(raw_count,0,LOCAL_LIGHT_MAX_RECORDS);",
        "boolpoint_scan_admitted="
        "point_light_admitted"
        "&&raw_count>0"
        "&&raw_count<=LOCAL_LIGHT_MAX_RECORDS;",
        "for(intlight_index=0;"
        "light_index<LOCAL_LIGHT_MAX_RECORDS;"
        "light_index+=1){",
        "if(light_index>=scan_count)break;",
        "vec4slot_2=ll_slot2(light_index);",
        "intkind=int(slot_2.x);",
    )
    if not collector or not ordered(collector, count_law):
        errors.append("ascending scan or historical count asymmetry drifted")

    lantern_and_beam_law = (
        "if(kind==4){",
        "vec4slot_0=ll_slot0(light_index);",
        "vec4slot_1=ll_slot1(light_index);",
        "vec4slot_3=ll_slot3(light_index);",
        "if(!facts.lantern_present){",
        "facts.lantern_present=true;",
        "facts.lantern_color="
        "clamp(slot_1.rgb,vec3(0.0),vec3(1.0));",
        "LighthouseConecone="
        "lighthouse_cone_from_rows(slot_0,slot_2,slot_3);",
        "if(beam_contribution>best_beam_contribution){",
        "best_beam_coverage=beam_coverage;",
        "best_beam_glyph=lighthouse_beam_screen_stroke_glyph("
        "cone,beam_axis_t,fb_size);",
        "best_beam_color=slot_1.rgb;",
        "best_beam_intensity=slot_1.w;",
        "}continue;}",
    )
    if not ordered(collector, lantern_and_beam_law):
        errors.append("first-lantern or strict beam-winner law drifted")
    if "beam_contribution>=best_beam_contribution" in collector:
        errors.append("beam tie changed from strict greater-than")

    point_law = (
        "if(!point_scan_admitted"
        "||kind==LOCAL_LIGHT_KIND_DOWNWASH)continue;",
        "vec4slot_0=ll_slot0(light_index);",
        "if(distance_to_light>influence_radius)continue;",
        "vec4slot_1=ll_slot1(light_index);",
        "floatfalloff=radius_t*radius_t*slot_1.w;",
        "if(falloff==0.0)continue;",
        "if((int(slot_2.w)&LOCAL_LIGHT_FLAG_CAST_SHADOW)!=0){",
        "falloff*=vsb_product_local_light_visibility("
        "receiver_world,receiver_normal,light_world,"
        "receiver_face_idx,receiver_actor_row);",
        "}else{",
        "vec4slot_3=ll_slot3(light_index);",
        "floatocclusion_depth=slot_3.z;",
        "if(kind==1)bonfire_warmth+=falloff*0.85;",
        "accumulated+=slot_1.rgb*falloff;",
        "accepted_count+=1;",
    )
    if not ordered(collector, point_law):
        errors.append("point admission, shadows, or row accumulation drifted")

    if (
        "input_value.identity.material" in collector
        or "q3_probe_payload_night_enabled" in collector
        or "baseline_feature_night_enabled" in collector
    ):
        errors.append("whole binding-12 pass acquired material or night gating")
    if ".length()" in collector:
        errors.append("collector revived a Metal-unsafe runtime-array length")
    if "facts.bonfire_contributed=false;" not in collector:
        errors.append("bonfire contribution fact no longer starts false")
    kind_offset = collector.find("intkind=int(slot_2.x);")
    pre_kind_prefix = collector[:kind_offset]
    if any(
        accessor in pre_kind_prefix
        for accessor in (
            "ll_slot0(light_index)",
            "ll_slot1(light_index)",
            "ll_slot3(light_index)",
        )
    ):
        errors.append("binding-12 payload row was fetched before kind admission")
    for eager_row in (
        "vec4slot_0=ll_slot0(light_index);",
        "vec4slot_1=ll_slot1(light_index);",
        "vec4slot_3=ll_slot3(light_index);",
    ):
        first_row_read = collector.find(eager_row)
        if first_row_read < 0 or first_row_read < kind_offset:
            errors.append(
                "binding-12 payload row was fetched before kind admission"
            )
    expected_accessor_counts = {
        "ll_slot0(light_index)": 2,
        "ll_slot1(light_index)": 2,
        "ll_slot2(light_index)": 1,
        "ll_slot3(light_index)": 2,
    }
    for accessor, expected_count in expected_accessor_counts.items():
        if collector.count(accessor) != expected_count:
            errors.append(
                f"binding-12 conditional row-read count drifted for {accessor}"
            )

    beam_payload_law = (
        "if(best_beam_contribution>0.0){",
        "facts.beam.present=true;",
        "facts.beam.glyph=best_beam_glyph;",
        "facts.beam.blend=SEC_BLEND_ADDITIVE;",
        "facts.beam.coverage=clamp(max("
        "best_beam_coverage*0.82,"
        "best_beam_contribution"
        "/max(best_beam_intensity,1e-3)*0.32),0.0,0.86);",
        "facts.beam.alpha=clamp(max("
        "best_beam_coverage*0.72,"
        "best_beam_contribution*0.38),0.0,0.78);",
        "facts.beam.fg=clamp(best_beam_color,vec3(0.0),vec3(1.0));",
        "facts.beam.intensity="
        "clamp(best_beam_contribution*0.48,0.0,1.0);",
    )
    if not ordered(collector, beam_payload_law):
        errors.append("winning beam payload construction drifted")
    retired_beam_provenance = (
        "facts.beam.kind",
        "facts.beam.source_id",
        "facts.beam.reject_bits",
        "facts.beam.depth",
        "facts.beam.world",
        "facts.beam.aux",
    )
    if any(token in collector for token in retired_beam_provenance):
        errors.append("beam resurrected deleted paint-irrelevant provenance")
    retired_glyph_provenance = (
        "product.kind",
        "product.source_id",
        "product.reject_bits",
        "product.depth",
        "product.world",
        "product.aux",
    )
    if any(token in glyph_light for token in retired_glyph_provenance):
        errors.append("glyph light resurrected deleted paint-irrelevant provenance")

    lantern_law = (
        "input_value.context.surface_kind!=OWNER_STATIC_MESH",
        "record.r0.y!=OWNER_STATIC_MESH",
        "input_value.identity.material!=204u",
        "!q3_probe_payload_night_enabled(input_value.context.flags)",
        "if(!local_light_facts.lantern_present)return;",
        "vec3lighthouse_color=local_light_facts.lantern_color;",
        "record.fg.rgb=clamp("
        "record.fg.rgb+lighthouse_color*0.60,"
        "vec3(0.0),vec3(1.0));",
        "record.bg.rgb=clamp("
        "record.bg.rgb+lighthouse_color*0.60*0.28,"
        "vec3(0.0),vec3(1.0));",
    )
    if not lantern or not ordered(lantern, lantern_law):
        errors.append("material-204 night-only lantern styling drifted")

    receiver_adjustment = "effect_receiver_actor_row=actor.present?actor.row:-1;"
    actor_receiver = "effect_receiver=input_value.ray.near_world"
    cell_center = "vec2cell_center_px="
    collector_call = (
        "VsbProductLocalLightSlotFactslocal_light_facts="
        "vsb_product_collect_local_light_slot_facts("
    )
    finalizer_law = (
        receiver_adjustment,
        actor_receiver,
        cell_center,
        collector_call,
        "effect_receiver_face_idx,effect_receiver_actor_row,"
        "record.r0.y!=OWNER_SKY);",
        "vsb_product_apply_lighthouse_lantern_style("
        "record,input_value,local_light_facts);",
        "EffectCellProductbeam=local_light_facts.beam;",
        "vsb_product_apply_effect(beam,record);",
        "if(record.r0.y!=OWNER_SKY){",
        "vec3addition=local_light_facts.point_addition;",
        "if(local_light_facts.bonfire_contributed",
    )
    if not finalizer or not ordered(finalizer, finalizer_law):
        errors.append("receiver collection or lantern-beam-point order drifted")
    if finalizer.count(f"{COLLECTOR}(") != 1:
        errors.append("finalizer collector call count drifted")
    if finalizer.count("local_light_facts.point_addition") != 1:
        errors.append("point-light result must have one finalizer application")
    if finalizer.count("local_light_facts.beam") != 1:
        errors.append("beam result must have one finalizer application")
    if any(accessor in finalizer for accessor in ("ll_slot0(", "ll_slot1(", "ll_slot2(", "ll_slot3(")):
        errors.append("finalizer bypasses the typed collector")

    downwash_law = (
        "intn=local_light_record_count();",
        "for(inti=0;i<n;++i){",
        "if(local_light_kind(i)!=LOCAL_LIGHT_KIND_DOWNWASH)continue;",
    )
    if not downwash or not ordered(downwash, downwash_law):
        errors.append("separate foliage downwash consumer drifted")
    if COLLECTOR in downwash:
        errors.append("downwash was folded into the paint finalizer pass")

    return tuple(errors)


@dataclass(frozen=True)
class LightRow:
    kind: int
    beam: float
    point: float


def old_selection(rows: tuple[LightRow, ...], raw_count: int, sky: bool) -> tuple:
    """Model the three deleted scans using precomputed per-row contributions."""
    scan_count = max(0, min(raw_count, 64))
    prefix = rows[:scan_count]
    lantern = next((index for index, row in enumerate(prefix) if row.kind == 4), -1)
    best_index = -1
    best_beam = 0.0
    for index, row in enumerate(prefix):
        if row.kind == 4 and row.beam > best_beam:
            best_index = index
            best_beam = row.beam
    point_sum = 0.0
    accepted = 0
    if not sky and 0 < raw_count <= 64:
        for row in prefix:
            if row.kind in (4, 5):
                continue
            if row.point == 0.0:
                continue
            point_sum += row.point
            accepted += 1
    return lantern, best_index, best_beam, point_sum, accepted


def merged_selection(rows: tuple[LightRow, ...], raw_count: int, sky: bool) -> tuple:
    """Model the selected one-pass law with independent typed accumulators."""
    scan_count = max(0, min(raw_count, 64))
    point_admitted = not sky and 0 < raw_count <= 64
    lantern = -1
    best_index = -1
    best_beam = 0.0
    point_sum = 0.0
    accepted = 0
    for index, row in enumerate(rows[:scan_count]):
        if row.kind == 4:
            if lantern < 0:
                lantern = index
            if row.beam > best_beam:
                best_index = index
                best_beam = row.beam
            continue
        if not point_admitted or row.kind == 5 or row.point == 0.0:
            continue
        point_sum += row.point
        accepted += 1
    return lantern, best_index, best_beam, point_sum, accepted


def binding_12_visit_counts(
    raw_count: int,
    *,
    sky: bool,
    lantern_index: int | None,
) -> tuple[int, int]:
    """Return deleted-scan visits and selected merged-scan visits."""
    scan_count = max(0, min(raw_count, 64))
    deleted_visits = scan_count
    if not sky and 0 < raw_count <= 64:
        deleted_visits += scan_count
    if lantern_index is not None and lantern_index < scan_count:
        deleted_visits += lantern_index + 1
    return deleted_visits, scan_count


def merged_row_read_count(
    *,
    kind: int,
    point_scan_admitted: bool,
    inside_point_radius: bool,
    positive_falloff: bool,
    casts_shadow: bool,
) -> int:
    """Model vec4 reads performed by one row in the selected source order."""
    reads = 1  # Slot two owns kind and flags.
    if kind == 4:
        return reads + 3
    if not point_scan_admitted or kind == 5:
        return reads
    reads += 1  # Slot zero owns point position and radius.
    if not inside_point_radius:
        return reads
    reads += 1  # Slot one owns color and intensity.
    if not positive_falloff:
        return reads
    if not casts_shadow:
        reads += 1  # Slot three owns only non-shadow occlusion depth.
    return reads


def test_single_pass_source_contract() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert contract_errors(source) == ()


def test_merged_selection_matches_three_deleted_scans() -> None:
    rows = (
        LightRow(1, 0.0, 0.12),
        LightRow(4, 0.25, 9.0),
        LightRow(2, 0.0, 0.0),
        LightRow(4, 0.25, 7.0),
        LightRow(5, 0.0, 8.0),
        LightRow(4, 0.80, 6.0),
        LightRow(3, 0.0, 0.07),
    )
    for raw_count in (-1, 0, 1, 4, len(rows), 64, 65, 400):
        for sky in (False, True):
            assert merged_selection(rows, raw_count, sky) == old_selection(
                rows, raw_count, sky
            )


def test_binding_12_visit_reduction_is_exact() -> None:
    """Pin the traffic reduction without turning timing into correctness."""
    # Ordinary visible receiver: beam N plus point N becomes one N-row pass.
    assert binding_12_visit_counts(
        20, sky=False, lantern_index=None
    ) == (40, 20)
    assert binding_12_visit_counts(
        21, sky=False, lantern_index=None
    ) == (42, 21)
    assert binding_12_visit_counts(
        64, sky=False, lantern_index=None
    ) == (128, 64)
    # Night material 204: lantern finds the first kind-4 row at index one in
    # the host-sorted table, so the deleted visits were 2 + N + N.
    assert binding_12_visit_counts(
        20, sky=False, lantern_index=1
    ) == (42, 20)
    assert binding_12_visit_counts(
        21, sky=False, lantern_index=1
    ) == (44, 21)
    assert binding_12_visit_counts(
        64, sky=False, lantern_index=1
    ) == (130, 64)


def test_binding_12_rejected_rows_do_not_fetch_unused_payload() -> None:
    """Pin row-read traffic that loop-visit counts cannot distinguish."""
    assert merged_row_read_count(
        kind=2,
        point_scan_admitted=False,
        inside_point_radius=True,
        positive_falloff=True,
        casts_shadow=False,
    ) == 1
    assert merged_row_read_count(
        kind=5,
        point_scan_admitted=True,
        inside_point_radius=True,
        positive_falloff=True,
        casts_shadow=False,
    ) == 1
    assert merged_row_read_count(
        kind=2,
        point_scan_admitted=True,
        inside_point_radius=False,
        positive_falloff=True,
        casts_shadow=False,
    ) == 2
    assert merged_row_read_count(
        kind=2,
        point_scan_admitted=True,
        inside_point_radius=True,
        positive_falloff=True,
        casts_shadow=True,
    ) == 3
    assert merged_row_read_count(
        kind=2,
        point_scan_admitted=True,
        inside_point_radius=True,
        positive_falloff=True,
        casts_shadow=False,
    ) == 4
    assert merged_row_read_count(
        kind=4,
        point_scan_admitted=False,
        inside_point_radius=False,
        positive_falloff=False,
        casts_shadow=False,
    ) == 4
    # Sky never ran point illumination; its beam N remains one N-row pass.
    assert binding_12_visit_counts(
        20, sky=True, lantern_index=None
    ) == (20, 20)


def replace_once(source: str, old: str, new: str) -> str:
    assert old in source, f"mutation source token missing: {old!r}"
    return source.replace(old, new, 1)


def damaging_mutants(source: str) -> tuple[tuple[str, str], ...]:
    """Return compile-valid source mutations that must all fail the contract."""
    mutants: list[tuple[str, str]] = []
    compact_effect_struct = """struct EffectCellProduct {
	bool present;
	uint glyph;
	uint blend;
	float coverage;
	float alpha;
	vec3 fg;
	float intensity;
};"""

    def add(name: str, old: str, new: str) -> None:
        mutants.append((name, replace_once(source, old, new)))

    def with_effect_fields(declarations: tuple[str, ...]) -> str:
        """Restore metadata only in the compact effect record under review."""
        assert source.count(compact_effect_struct) == 1
        restored_fields = "".join(
            f"\t{declaration};\n" for declaration in declarations
        )
        restored = compact_effect_struct.replace(
            "\tuint glyph;",
            restored_fields + "\tuint glyph;",
            1,
        )
        return source.replace(compact_effect_struct, restored, 1)

    add(
        "lantern_first_record_reversed",
        "if (!facts.lantern_present) {",
        "if (facts.lantern_present) {",
    )
    add(
        "beam_tie_reversed",
        "if (beam_contribution > best_beam_contribution) {",
        "if (beam_contribution >= best_beam_contribution) {",
    )
    add(
        "malformed_point_count_clamped",
        "&& raw_count <= LOCAL_LIGHT_MAX_RECORDS;",
        "&& scan_count <= LOCAL_LIGHT_MAX_RECORDS;",
    )
    add(
        "sky_point_light_admitted",
        "record.r0.y != OWNER_SKY);",
        "true);",
    )
    add(
        "downwash_becomes_point_light",
        "kind == LOCAL_LIGHT_KIND_DOWNWASH",
        "kind == 6",
    )
    add(
        "positive_point_light_discarded",
        "if (falloff == 0.0) continue;",
        "if (falloff <= 0.05) continue;",
    )
    add(
        "static_and_actor_shadow_bypassed",
        "falloff *= vsb_product_local_light_visibility(\n"
        "\t\t\t\treceiver_world, receiver_normal, light_world,\n"
        "\t\t\t\treceiver_face_idx, receiver_actor_row);",
        "falloff *= 1.0;",
    )
    add(
        "receiver_actor_identity_dropped",
        "receiver_face_idx, receiver_actor_row);",
        "receiver_face_idx, -1);",
    )
    add(
        "accepted_count_precedes_accumulation",
        "accumulated += slot_1.rgb * falloff;\n"
        "\t\taccepted_count += 1;",
        "accepted_count += 1;\n"
        "\t\taccumulated += slot_1.rgb * falloff;",
    )
    add(
        "whole_pass_night_gate",
        "if (!local_light_records_enabled()) return facts;",
        "if (!local_light_records_enabled()) return facts;\n"
        "\tif (!baseline_feature_night_enabled()) return facts;",
    )
    add(
        "lantern_material_changed",
        "input_value.identity.material != 204u",
        "input_value.identity.material != 205u",
    )
    add(
        "host_light_gate_inverted",
        "if (!local_light_records_enabled()) return facts;",
        "if (local_light_records_enabled()) return facts;",
    )
    add(
        "descending_record_scan",
        "light_index += 1",
        "light_index -= 1",
    )
    add(
        "second_binding_scan_added",
        "\tfor (int light_index = 0;",
        "\tfor (int duplicate_index = 0; duplicate_index < 1; duplicate_index += 1) {}\n"
        "\tfor (int light_index = 0;",
    )
    add(
        "kind4_falls_into_point_path",
        "\t\t\tcontinue;\n\t\t}\n\n\t\t// Sky still receives",
        "\t\t\tif (false) continue;\n\t\t}\n\n\t\t// Sky still receives",
    )
    add(
        "eager_payload_rows_restored",
        "\t\tvec4 slot_2 = ll_slot2(light_index);\n"
        "\t\tint kind = int(slot_2.x);",
        "\t\tvec4 slot_2 = ll_slot2(light_index);\n"
        "\t\tvec4 eager_slot_0 = ll_slot0(light_index);\n"
        "\t\tint kind = int(slot_2.x);",
    )
    add(
        "global_bonfire_side_channel_restored",
        "VsbProductLocalLightSlotFacts vsb_product_collect_local_light_slot_facts(",
        "bool vsb_product_bonfire_light_contributed;\n\n"
        "VsbProductLocalLightSlotFacts vsb_product_collect_local_light_slot_facts(",
    )
    add(
        "point_applied_before_beam",
        "EffectCellProduct beam = local_light_facts.beam;",
        "vec3 early_point_addition = local_light_facts.point_addition;\n"
        "\trecord.fg.rgb += early_point_addition;\n"
        "\tEffectCellProduct beam = local_light_facts.beam;",
    )
    add(
        "collector_called_twice",
        "vsb_product_apply_lighthouse_lantern_style(\n"
        "\t\trecord, input_value, local_light_facts);",
        "local_light_facts = vsb_product_collect_local_light_slot_facts(\n"
        "\t\t\tinput_value.ray, framebuffer_size, cell_center_px,\n"
        "\t\t\teffect_receiver, effect_normal, effect_depth,\n"
        "\t\t\teffect_receiver_face_idx, effect_receiver_actor_row,\n"
        "\t\t\trecord.r0.y != OWNER_SKY);\n"
        "\tvsb_product_apply_lighthouse_lantern_style(\n"
        "\t\trecord, input_value, local_light_facts);",
    )
    add(
        "beam_source_color_bypassed",
        "best_beam_color = slot_1.rgb;",
        "best_beam_color = slot_0.rgb;",
    )
    add(
        "beam_source_intensity_bypassed",
        "best_beam_intensity = slot_1.w;",
        "best_beam_intensity = slot_0.w;",
    )
    # Restore the old metadata fields and writes as coordinated, compile-valid
    # regressions. The compact Product carries only values consumed by paint;
    # these provenance values previously increased register pressure without
    # affecting one output byte.
    mutants.append(
        (
            "local_light_provenance_resurrected",
            with_effect_fields(("uint kind", "uint source_id")).replace(
                "\tproduct.present = true;\n"
                "\tproduct.glyph = best_glyph;",
                "\tproduct.present = true;\n"
                "\tproduct.kind = OWNER_LOCAL_LIGHT;\n"
                "\tproduct.source_id = 1u;\n"
                "\tproduct.glyph = best_glyph;",
                1,
            ),
        )
    )
    add(
        "beam_coverage_coefficient_drifted",
        "best_beam_coverage * 0.82",
        "best_beam_coverage * 0.81",
    )
    add(
        "beam_alpha_coefficient_drifted",
        "best_beam_coverage * 0.72",
        "best_beam_coverage * 0.71",
    )
    add(
        "beam_foreground_channel_order_drifted",
        "clamp(best_beam_color, vec3(0.0), vec3(1.0))",
        "clamp(best_beam_color.bgr, vec3(0.0), vec3(1.0))",
    )
    add(
        "beam_intensity_coefficient_drifted",
        "best_beam_contribution * 0.48",
        "best_beam_contribution * 0.47",
    )
    mutants.append(
        (
            "beam_provenance_resurrected",
            with_effect_fields(
                ("uint source_id", "vec3 world", "float aux")
            ).replace(
                "\t\tfacts.beam.present = true;\n"
                "\t\tfacts.beam.glyph = best_beam_glyph;",
                "\t\tfacts.beam.present = true;\n"
                "\t\tfacts.beam.source_id = 1u;\n"
                "\t\tfacts.beam.world = ray.near_world;\n"
                "\t\tfacts.beam.aux = best_beam_contribution;\n"
                "\t\tfacts.beam.glyph = best_beam_glyph;",
                1,
            ),
        )
    )
    add(
        "projection_fence_resurrected",
        "\tfacts.bonfire_contributed = false;\n"
        "\tif (!local_light_records_enabled()) return facts;",
        "\tfacts.bonfire_contributed = false;\n"
        "\tif (product_vsb_frame.view_identity.z\n"
        "\t\t\t!= VSB_PROJECTION_PERSPECTIVE) return facts;\n"
        "\tif (!local_light_records_enabled()) return facts;",
    )
    add(
        "bonfire_contribution_starts_true",
        "facts.bonfire_contributed = false;",
        "facts.bonfire_contributed = true;",
    )
    add(
        "nonshadow_occlusion_source_shifted",
        "float occlusion_depth = slot_3.z;",
        "float occlusion_depth = slot_3.y;",
    )
    return tuple(mutants)


def compile_glsl(source: str) -> tuple[bool, str]:
    """Compile one mutated Product shader through the Vulkan 1.1 front end."""
    compiler = shutil.which("glslangValidator")
    assert compiler is not None, "glslangValidator is required"
    lines = source.splitlines(keepends=True)
    if lines and lines[0].startswith("#["):
        source = "".join(lines[1:])
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".comp",
        encoding="utf-8",
        delete=False,
    ) as handle:
        handle.write(source)
        path = Path(handle.name)
    try:
        result = subprocess.run(
            [
                compiler,
                "--target-env",
                "vulkan1.1",
                "-S",
                "comp",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
    finally:
        path.unlink(missing_ok=True)
    diagnostic = "\n".join(
        text for text in (result.stdout, result.stderr) if text
    )
    return result.returncode == 0, diagnostic


def test_all_compile_valid_damaging_mutants_are_rejected() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutants = damaging_mutants(source)
    assert len(mutants) == 30
    escaped: list[str] = []
    for name, mutant in mutants:
        compiled, diagnostic = compile_glsl(mutant)
        assert compiled, (
            f"damaging mutant {name} was not compile-valid:\n{diagnostic}"
        )
        if contract_errors(mutant, check_reachability=False) == ():
            escaped.append(name)
    assert escaped == [], f"damaging mutants escaped: {escaped}"


def main() -> int:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    errors = contract_errors(source)
    if errors:
        for error in errors:
            print(f"FL4431_PRODUCT_LOCAL_LIGHT_SINGLE_PASS_FAIL {error}")
        return 1
    test_merged_selection_matches_three_deleted_scans()
    test_binding_12_visit_reduction_is_exact()
    test_binding_12_rejected_rows_do_not_fetch_unused_payload()
    test_all_compile_valid_damaging_mutants_are_rejected()
    print(
        "FL4431_PRODUCT_LOCAL_LIGHT_SINGLE_PASS_PASS "
        "old_loops=3 new_loops=1 conditional_row_reads=true mutants=30"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
