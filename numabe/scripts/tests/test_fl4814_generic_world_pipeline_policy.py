import json
from pathlib import Path

from scripts.pipeline.template_resolver import discover_pack_chain, resolve_data_profile


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "assets" / "world_templates"
GENERIC_EXECUTORS = (
    ROOT / "scripts" / "pipeline" / "compile_world_ir_fixtures.py",
    ROOT / "scripts" / "pipeline" / "compile_facade_shell.py",
    ROOT / "scripts" / "pipeline" / "template_resolver.py",
)


def test_location_operator_and_business_policy_stays_out_of_generic_executors():
    forbidden = ("numabe", "sakurazaka", "tokyu", "familymart", "my basket")
    for path in GENERIC_EXECUTORS:
        text = path.read_text(encoding="utf-8").casefold()
        assert not [token for token in forbidden if token in text], path


def test_japan_and_us_rail_policy_resolves_from_data_packs():
    jp = discover_pack_chain(TEMPLATES, {
        "country_code": "JP", "region": "Tokyo",
        "site_contexts": ["urban_residential", "railway_station"],
    })
    us = discover_pack_chain(TEMPLATES, {
        "country_code": "US", "region": "Maryland",
        "site_contexts": ["urban_residential", "railway_station"],
    })
    jp_rail = resolve_data_profile(
        collection="rail_infrastructure_profiles", attributes={}, packs=jp,
    )
    us_rail = resolve_data_profile(
        collection="rail_infrastructure_profiles", attributes={}, packs=us,
    )
    assert (jp_rail["default_gauge_mm"], jp_rail["pack_id"]) == (1067, "japan")
    assert (us_rail["default_gauge_mm"], us_rail["pack_id"]) == (1435, "united_states")


def test_evidence_specific_place_rule_beats_child_pack_fallback():
    packs = discover_pack_chain(TEMPLATES, {
        "country_code": "JP", "region": "Tokyo",
        "site_contexts": ["urban_residential", "railway_station"],
    })
    profile = resolve_data_profile(
        collection="place_front_profiles",
        attributes={
            "amenity": "place_of_worship", "religion": "buddhist",
            "brand_identity": "", "commercial_category": "amenity=place_of_worship",
        },
        packs=packs,
    )
    assert profile["geometry_family"] == "place_entry_gate"
    assert profile["pack_id"] == "japan_urban"


def test_template_packs_are_valid_json_documents():
    for path in sorted(TEMPLATES.glob("*/pack.json")):
        assert json.loads(path.read_text(encoding="utf-8"))["pack_id"] == path.parent.name
