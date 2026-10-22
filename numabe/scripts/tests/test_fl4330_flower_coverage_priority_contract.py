from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _without_comments(source))


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def _replace_exact(source: str, old: str, new: str, count: int = 1) -> str:
    assert source.count(old) == count, old
    return source.replace(old, new)


def _assert_fl4330_contract(source: str) -> None:
    coverage = _compact(_function(source, "FoliageCoverage foliage_blade_coverage_at_cell("))
    identity_blade = _compact(_function(source, "FoliageBlade foliage_identity_blade("))
    depth_pull = _compact(_function(source, "float foliage_accent_depth_pull("))
    scatter = _compact(_function(source, "bool foliage_scatter_accept_anchor("))
    blade = _compact(_function(source, "FoliageBlade foliage_blade_from_anchor("))
    glyph = _compact(_function(source, "int foliage_glyph_for_coverage("))
    cycle = _compact(_function(source, "int foliage_saved_static_cycle_glyph("))
    species = _compact(_function(source, "vec3 foliage_flower_species_ink("))
    direct = _compact(_function(source, "void compute_product_foliage_fact("))
    finish = _compact(_function(source, "bool q3_probe_payload_finish_foliage("))
    apply_foliage = _compact(_function(source, "bool q3_probe_payload_apply_foliage("))
    compile_payload = _compact(_function(source, "ProbePayloadCompileResult compile_probe_subcell_payload("))

    # The sampled card mask is the semantic fact. Flower class outranks depth;
    # depth remains the winner only when candidate and incumbent share a class.
    identity_sample = "FoliageBladeidentity_blade=foliage_identity_blade("
    card_sample = (
        "FoliageCardMaskmask="
        "foliage_card_mask_sample(identity_blade,uv);"
    )
    alpha_gate = "if(shell_alpha<=0.04)continue;"
    depth_key = "floatdepth_key=effective_y+shell_fraction*0.08-0.04*length(vec2(cell-anchor));"
    candidate = "boolcandidate_is_flower=mask.shape==FOLIAGE_SHAPE_FLOWER;"
    incumbent = "boolincumbent_is_flower=c.present&&c.role==FOLIAGE_ROLE_FLOWER;"
    priority = (
        "if(candidate_is_flower!=incumbent_is_flower){"
        "if(!candidate_is_flower)continue;"
        "}elseif(depth_key<=c.depth_key){continue;}"
    )
    publication = "c.present=true;"
    actual_role = "c.role=(mask.shape==FOLIAGE_SHAPE_FLOWER)?FOLIAGE_ROLE_FLOWER"
    for token in (identity_sample, card_sample, alpha_gate, depth_key, candidate, incumbent, priority,
                  publication, actual_role):
        assert token in coverage
    assert coverage.index(identity_sample) < coverage.index(card_sample)
    assert coverage.index(card_sample) < coverage.index(alpha_gate)
    assert coverage.index(alpha_gate) < coverage.index(depth_key)
    assert coverage.index(depth_key) < coverage.index(candidate)
    assert coverage.index(candidate) < coverage.index(priority)
    assert coverage.index(priority) < coverage.index(publication)
    assert "candidate_is_flower=b.variant" not in coverage
    assert "candidate_is_flower=b.grammar_role" not in coverage
    for animation_field in (
        "stable.quantized_time=0.0;",
        "stable.wind_dir=vec2(0.0);",
        "stable.wind_sample=0.0;",
        "stable.world_sway=0.0;",
        "stable.view_sway=0.0;",
        "stable.character_push=vec2(0.0);",
    ):
        assert animation_field in identity_blade

    # The retired shape-specific depth boost cannot become a second flower
    # survival owner. Variant/local-v retain only the established small accent.
    assert depth_pull.startswith("floatfoliage_accent_depth_pull(intvariant,floatlocal_v)")
    assert (
        "floataccent=(variant==FOLIAGE_VARIANT_FLOWER||"
        "variant==FOLIAGE_VARIANT_ACCENT_BLADE)?1.0:0.0;"
    ) in depth_pull
    assert "returnaccent*0.10*smoothstep(0.45,0.94,local_v);" in depth_pull
    assert "shape" not in depth_pull
    assert "floatflower=" not in depth_pull
    assert "0.42" not in depth_pull
    assert coverage.count("foliage_accent_depth_pull(b.variant,uv.y)") == 1
    assert "foliage_accent_depth_pull(mask.shape" not in coverage

    # Authored admission, slot identity, sparse flower publication, species,
    # glyph and root-band treatment remain the same on both foliage routes.
    assert "(slot==FOLIAGE_CLUMP_SLOT_BLOOM)?density*0.90:density" in scatter
    assert "if(slot==FOLIAGE_CLUMP_SLOT_BLOOM)slot_role=FOLIAGE_ROLE_FLOWER;" in blade
    assert "if(slot_role==FOLIAGE_ROLE_FLOWER" in blade
    assert "variant=FOLIAGE_VARIANT_FLOWER;" in blade
    assert "foliage_saved_static_cycle_glyph(c)" in glyph
    assert "c.role==FOLIAGE_ROLE_FLOWER" in cycle
    assert "uintspecies=h%4u;" in species
    assert "FOLIAGE_FLOWER_HUE_WHITE" in species
    for route in (direct, finish):
        assert "cov.coverage=max(cov.coverage,0.46);" in route
        assert "cov.density==FOLIAGE_DENSITY_SPARSE" in route
        assert "cov.density=FOLIAGE_DENSITY_MEDIUM;" in route
        assert "foliage_glyph_for_coverage(cov)" in route
        assert "foliage_flower_species_ink(" in route
        assert "foliage_apply_root_terrain_ramp_band(" in route

    # Retained moss deliberately consumes the same winner and finalizer.
    assert "FoliageCoveragemoss_growth=foliage_blade_coverage_at_cell(moss_cell);" in apply_foliage
    assert (
        "q3_probe_payload_finish_foliage(moss_cell,moss_growth,"
        "foliage_root_ramp_band,foliage_glyph,foliage_fg,coverage,shape,role)"
    ) in apply_foliage

    # Flower coverage still enters before the existing cast/weather ownership.
    foliage_call = compile_payload.index("q3_probe_payload_apply_foliage(")
    cast_call = compile_payload.index("q3_probe_payload_apply_cast_shadow_hue(")
    weather_call = compile_payload.index("q3_probe_payload_apply_weather(")
    assert foliage_call < cast_call < weather_call

    # Four-quarter foliage publication remains projection independent and fail
    # closed for mixed owners. Ordinary surface glyphs do not enter this owner.
    publication_start = source.index("// FL-4330/RQ-190: foliage")
    publication_end = source.index("bool fl4722_water_quad", publication_start)
    quarter_publication = _compact(source[publication_start:publication_end])
    assert "for(intslot=0;slot<4;slot+=1)" in quarter_publication
    assert "fl4330_owner_uniform=false;" in quarter_publication
    assert "role==FOLIAGE_ROLE_FLOWER?0u" in quarter_publication
    assert "staged_final_records[slot].r0.z=fl4330_glyph;" in quarter_publication
    for forbidden in ("VSB_PROJECTION_", "camera", "screen", "view_identity"):
        assert forbidden not in quarter_publication


def test_fl4330_flower_coverage_priority_contract() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    _assert_fl4330_contract(source)


@pytest.mark.parametrize(
    ("old", "new", "count"),
    [
        (
            "bool candidate_is_flower = mask.shape == FOLIAGE_SHAPE_FLOWER;",
            "bool candidate_is_flower = b.variant == FOLIAGE_VARIANT_FLOWER;",
            1,
        ),
        (
            "if (!candidate_is_flower) continue;",
            "if (candidate_is_flower) continue;",
            1,
        ),
        (
            "} else if (depth_key <= c.depth_key) {",
            "} else if (false) {",
            1,
        ),
        (
            "float foliage_accent_depth_pull(int variant, float local_v)",
            "float foliage_accent_depth_pull(int shape, int variant, float local_v)",
            1,
        ),
        (
            "return accent * 0.10 * smoothstep(0.45, 0.94, local_v);",
            "return (accent * 0.10 + 0.42) * smoothstep(0.45, 0.94, local_v);",
            1,
        ),
        (
            "? density * 0.90 : density;",
            "? density * 0.60 : density;",
            1,
        ),
        (
            "uint species = h % 4u;",
            "uint species = h % 3u;",
            1,
        ),
        (
            "FoliageCoverage moss_growth = foliage_blade_coverage_at_cell(moss_cell);",
            "FoliageCoverage moss_growth; moss_growth.present = false;",
            1,
        ),
        (
            "bool fl4330_owner_uniform = true;",
            "bool fl4330_owner_uniform = VSB_PROJECTION_ORTHOGRAPHIC;",
            1,
        ),
    ],
)
def test_fl4330_contract_rejects_regressions(old: str, new: str, count: int) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = _replace_exact(source, old, new, count)
    with pytest.raises(AssertionError):
        _assert_fl4330_contract(mutant)


# ---------------------------------------------------------------------------
# Behavioural model of FL-4740's universal semantic election.
#
# Foliage no longer owns a private identity-uniformity gate. Flowers compete in
# the same post-finalization Product election as water, retained edges, terrain,
# and effects. Provenance may break a semantic-rank tie; it may not decline a
# mixed-family cell or let raw glyph value and screen-slot order choose.
# ---------------------------------------------------------------------------
def _semantic_priority(rec) -> int:
    if rec["late_glyph"]:
        return 0
    if rec["owner"] == 4 and rec["water_class"] == 0:
        return 100
    if rec["owner"] == 4 and rec["water_class"] == 1:
        return 200
    if rec["flower"]:
        return 300
    if rec["retained_edge"] == 3:
        return 400
    if rec["terrain_class"] == 2:
        return 500
    if rec["retained_edge"] == 2:
        return 600
    if rec["terrain_class"] == 1:
        return 700
    if rec["foliage"]:
        return 800
    if rec["owner"] == 1:
        return 850
    return 900


def _semantic_key(rec):
    return (
        rec["owner"], rec["object"], rec["primitive"], rec["material"],
        rec["r1"], rec["anchor"], rec["fx"],
    )


def _model_cell_glyph(records):
    if len(records) != 4 or any(not rec["valid"] for rec in records):
        return None
    winner = None
    ambiguous = False
    for rec in records:
        candidate = (_semantic_priority(rec), _semantic_key(rec))
        if winner is None or candidate < winner[0]:
            winner = (candidate, rec["glyph"])
            ambiguous = False
        elif candidate == winner[0] and rec["glyph"] != winner[1]:
            ambiguous = True
    return None if ambiguous else winner[1]


def _rec(
    *, glyph=100, owner=1, material=7, source=3, entity=0, matcell=99,
    object_id=5, primitive=11, anchor=(1, 2, 3), fx=0, flower=False,
    foliage=True, late_glyph=False, water_class=2, terrain_class=0,
    retained_edge=0, valid=True,
):
    return {
        "glyph": glyph,
        "owner": owner,
        "object": object_id,
        "primitive": primitive,
        "material": material,
        "r1": (material, source, entity, matcell),
        "anchor": anchor,
        "fx": fx,
        "flower": flower,
        "foliage": foliage,
        "late_glyph": late_glyph,
        "water_class": water_class,
        "terrain_class": terrain_class,
        "retained_edge": retained_edge,
        "valid": valid,
    }


def test_fl4330_universal_model_admits_one_semantic_cell() -> None:
    assert _model_cell_glyph([_rec()] * 4) == 100


def test_fl4330_flower_wins_across_mixed_product_identities() -> None:
    records = [
        _rec(glyph=81, source=9, foliage=False),
        _rec(glyph=65, source=7, flower=True),
        _rec(glyph=70, owner=2, source=2, foliage=False),
        _rec(glyph=90, source=1),
    ]
    assert _model_cell_glyph(records) == 65


def test_fl4330_semantic_winner_is_permutation_stable() -> None:
    records = [
        _rec(glyph=90, source=9),
        _rec(glyph=80, source=4),
        _rec(glyph=70, source=2),
        _rec(glyph=60, source=7),
    ]
    assert _model_cell_glyph(records) == 70
    assert _model_cell_glyph(list(reversed(records))) == 70


def test_fl4330_raw_glyph_value_does_not_outrank_semantics() -> None:
    records = [
        _rec(glyph=1, source=8),
        _rec(glyph=250, source=2, flower=True),
        _rec(glyph=2, source=3),
        _rec(glyph=3, source=4),
    ]
    assert _model_cell_glyph(records) == 250


def test_fl4330_equal_semantic_key_with_different_glyph_declines() -> None:
    records = [_rec()] * 3 + [_rec(glyph=101)]
    assert _model_cell_glyph(records) is None


def test_fl4330_invalid_quarter_declines_cell_mask() -> None:
    records = [_rec()] * 3 + [_rec(valid=False)]
    assert _model_cell_glyph(records) is None
