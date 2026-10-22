"""FL-4876 ENTITY-01 / ENTITY-GATE-01 generic invariants.

Synthetic world, no real place, brand or region. These sit above any
neighbourhood regression fixture: they prove properties of the pipeline, not
of one map.
"""
import json

from scripts.pipeline.check_identity_invariants import run as gate
from scripts.pipeline.resolve_identity_entities import identity_match, load_adjudication, resolve, tokens


def _bldg(fid, x0, y0, x1, y1):
    return {"feature_id": fid, "semantic_class": "building_footprint",
            "geometry": {"kind": "polygon",
                         "coordinates": [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]}}


def _place(fid, oid, x, y):
    return {"feature_id": fid, "semantic_class": "place", "supporting_observation_ids": [oid],
            "geometry": {"kind": "point", "coordinates": [x, y]}}


WORLD = {
    "source_observations": [
        {"observation_id": "o1", "attributes": {"tags": {"shop": "convenience", "brand:en": "Brandex Mart"}}},
        {"observation_id": "o2", "attributes": {"tags": {"shop": "convenience", "name:en": "Other Mart"}}},
        {"observation_id": "o3", "attributes": {"tags": {"railway": "station", "name:en": "Testfield"}}},
    ],
    "canonical_features": [
        _bldg("b:a", 0, 0, 10, 10), _bldg("b:b", 30, 0, 40, 10), _bldg("b:c", 100, 0, 110, 10),
        _place("p:brandex", "o1", 5, 5), _place("p:other", "o2", 35, 5), _place("p:station", "o3", 105, 5),
    ],
}
AUTH = {"population_role_authoritative": True, "lawful_reference_state": "resolved"}
UNRES = {"population_role_authoritative": False, "lawful_reference_state": "unresolved_needs_operator_review"}


def _obs(iid, subj, e, n, u, ann=None):
    return {"instance_id": iid, "custom_identity_subject": subj, "E": e, "N": n,
            "position_uncertainty_m": u, "annotation_ids": [ann or "a-" + iid]}


def _mapping(*pairs):
    return {a: {"annotation_id": a, "mapping": m} for a, m in pairs}


def test_token_match_is_exact_not_substring():
    assert identity_match(tokens("Brandex Mart"), tokens("brandex_mart_storefront"))
    assert identity_match(tokens("Brandex Mart"), tokens("brandexmart_sign"))
    assert not identity_match(tokens("Mart"), tokens("smartphone_shop"))
    assert not identity_match(tokens("Other Mart"), tokens("brandexmart_storefront"))


def test_sightings_of_a_mapped_place_never_create_geometry_and_cannot_migrate():
    on1 = _obs("on1", "brandexmart_storefront", 0.1, 5, 3.0)
    on2 = _obs("on2", "brandexmart_storefront", 0.2, 6, 8.0)
    off = _obs("off", "brandexmart_storefront", 30.0, 5, 1.0)        # sharp ray, wrong building
    d = resolve([on1, on2, off], WORLD, mapping_rows=_mapping(*[("a-" + i, AUTH) for i in ("on1", "on2", "off")]))
    ent = next(e for e in d["entities"] if e["place_id"] == "p:brandex")
    assert ent["observations"]["supporting"] == ["on1", "on2"]
    assert ent["observations"]["contesting"] == ["off"]
    assert d["population_records"] == []          # the place front owns the storefront
    other = next(e for e in d["entities"] if e["place_id"] == "p:other")
    assert other["observations"]["supporting"] == [] and other["observations"]["contesting"] == []


def test_unmapped_identity_needs_manual_identity_and_placement_even_when_reviewed():
    unres = _obs("u1", "candidate_sign_cluster_7", 0.0, 5, 0.5)
    amb = _obs("u2", "candidate_sign_cluster_8", 20.0, 5, 12.0)       # 10 m to both buildings
    ok = _obs("u3", "candidate_sign_cluster_9", 40.0, 5, 0.5)
    rows = _mapping(("a-u1", UNRES), ("a-u2", AUTH), ("a-u3", AUTH))
    d = resolve([unres, amb, ok], WORLD, mapping_rows=rows)
    state = {e["identity"]["subject"]: e["state"] for e in d["entities"] if e["kind"] == "observed_place"}
    assert set(state.values()) == {"debt:manual_identity_placement_required"}
    assert d["population_records"] == []


def test_repeated_unadjudicated_sightings_do_not_auto_cluster_or_emit():
    a = _obs("r1", "candidate_sign_cluster_5", 40.0, 5, 0.5)
    b = _obs("r2", "candidate_sign_cluster_5", 40.0, 7, 2.0)          # 2 m <= 2.5 m: same object
    d = resolve([a, b], WORLD, mapping_rows=_mapping(("a-r1", AUTH), ("a-r2", AUTH)))
    assert d["population_records"] == []
    debt = [e for e in d["entities"] if e.get("state") == "debt:manual_identity_placement_required"]
    assert len(debt) == 2


def test_transit_sightings_are_station_evidence_and_every_observation_is_conserved():
    s = _obs("s1", "testfield_station", 60.0, 5, 30.0)
    fx = {"instance_id": "f1", "module_id": "utility_pole", "E": 0, "N": 0}
    junk = {"instance_id": "j1", "E": 0, "N": 0}
    d = resolve([s, fx, junk], WORLD)
    st = next(e for e in d["entities"] if e["place_id"] == "p:station")
    assert st["owner"] == "station_assembly" and st["observations"]["supporting"] == ["s1"]
    assert d["conservation"] == {"observations": 3, "consumed": 2, "unresolved": 1}


def test_crop_contributes_only_when_bound_and_supporting():
    on = _obs("c1", "brandexmart_storefront", 0.1, 5, 3.0, ann="crop-on")
    off = _obs("c2", "brandexmart_storefront", 30.0, 5, 1.0, ann="crop-off")
    links = {"crop-on": "p:brandex", "crop-off": "p:brandex"}
    d = resolve([on, off], WORLD, explicit_links=links)
    ent = next(e for e in d["entities"] if e["place_id"] == "p:brandex")
    assert ent["crop_eligible_annotations"] == ["crop-on"]


def _adj(identities, placements=()):
    return {"identities": {a: d for d in identities for a in d["annotation_ids"]},
            "placements": {p["business_key"]: p for p in placements}}


def _dec(i, ann, kind, **kw):
    return dict({"decision_id": "D%d" % i, "annotation_ids": [ann], "kind": kind, "shows": "x"}, **kw)


def test_adjudicated_logo_of_a_mapped_place_stays_crop_eligible_without_registration():
    # MANUAL-WORLD-03 withholds identity annotations from registration, so a
    # mapped place's adjudicated, explicitly bound logo has no observation.
    # The photo still shows that place's logo: it must remain crop-eligible.
    adj = _adj([_dec(1, "a-logo", "mapped_place", place_feature_id="p:brandex", role="logo"),
                _dec(2, "a-ev", "mapped_place", place_feature_id="p:brandex", role="evidence"),
                _dec(3, "a-unbound", "mapped_place", place_feature_id="p:brandex", role="logo")])
    links = {"a-logo": "p:brandex", "a-ev": "p:brandex"}
    d = resolve([], WORLD, explicit_links=links, adjudication=adj)
    ent = next(e for e in d["entities"] if e.get("place_id") == "p:brandex")
    assert ent["crop_eligible_annotations"] == ["a-logo"]
    assert d["population_records"] == []


def test_adjudicated_identity_overrides_the_review_label_and_registration():
    # The review labelled this photo as one brand; the photo shows the other
    # place. The adjudicated identity decides; position still comes from the map.
    mislabelled = _obs("m1", "brandexmart_storefront", 0.1, 5, 1.0, ann="a-m1")
    adj = _adj([_dec(1, "a-m1", "mapped_place", place_feature_id="p:other", role="logo")])
    d = resolve([mislabelled], WORLD, explicit_links={"a-m1": "p:other"}, adjudication=adj)
    brandex = next(e for e in d["entities"] if e.get("place_id") == "p:brandex")
    other = next(e for e in d["entities"] if e.get("place_id") == "p:other")
    assert brandex["observations"]["supporting"] == [] and brandex["observations"]["contesting"] == []
    assert other["observations"]["contesting"] == ["m1"]          # registered on the wrong building
    assert other["crop_eligible_annotations"] == ["a-m1"]         # identity read from the photo
    assert d["population_records"] == []


def test_photos_of_one_unmapped_business_are_one_sign_placed_only_where_decided():
    a = _obs("b1", "candidate_sign_1", 40.5, 5, 0.5, ann="a-b1")
    b = _obs("b2", "candidate_sign_2", 41.0, 9, 3.0, ann="a-b2")      # different review label, same shop
    c = _obs("b3", "candidate_sign_3", 0.5, 5, 0.5, ann="a-b3")       # a second shop, not placed
    junk = _obs("b4", "candidate_sign_4", 0.5, 2, 0.5, ann="a-b4")    # a vending machine
    adj = _adj([_dec(1, "a-b1", "business", business_key="shop_x", role="logo"),
                _dec(2, "a-b2", "business", business_key="shop_x", role="evidence"),
                _dec(3, "a-b3", "business", business_key="shop_y", role="logo"),
                _dec(4, "a-b4", "not_identity")],
               [{"business_key": "shop_x", "host_building_id": "b:b", "width_m": 2.0,
                 "face_index": 2, "along_face_m": 5.0,
                 "basis": "manual address-to-map building decision",
                 "face_basis": "manual facade review"}])
    d = resolve([a, b, c, junk], WORLD, adjudication=adj)
    recs = d["population_records"]
    assert len(recs) == 1 and recs[0]["entity_id"] == "entity:business:shop_x"
    assert recs[0]["consumed_instance_ids"] == ["b1", "b2"] and recs[0]["annotation_ids"][0] == "a-b1"
    assert abs(recs[0]["E"] - 35.0) < 1e-6 and abs(recs[0]["N"] - 10.0) < 1e-6
    y = next(e for e in d["entities"] if e["entity_id"] == "entity:business:shop_y")
    assert y["state"] == "debt:business_unplaced"
    assert {u["instance_id"] for u in d["unresolved"]} == {"b4"}
    assert d["conservation"]["consumed"] + d["conservation"]["unresolved"] == 4


def test_unmapped_business_cannot_take_a_mapped_place_slot():
    plan = {"rows": [{"feature_id": "p:other", "attachment_state": "resolved", "primary": True,
                      "attachment": {"anchor_kind": "building_face", "canonical_building_id": "b:b",
                                     "face_index": 0, "along_face_fraction": 0.5}}]}
    s = _obs("s1", "candidate_sign_9", 35.0, 0.0, 0.5, ann="a-s1")
    adj = _adj([_dec(1, "a-s1", "business", business_key="shop_z", role="logo")],
               [{"business_key": "shop_z", "host_building_id": "b:b", "width_m": 2.0,
                 "face_index": 0, "along_face_m": 5.5,
                 "basis": "manual address-to-map building decision",
                 "face_basis": "manual facade review"}])
    d = resolve([s], WORLD, representation_plan=plan, adjudication=adj, slot_half_m=4.0)
    z = next(e for e in d["entities"] if e["entity_id"] == "entity:business:shop_z")
    assert z["state"] == "debt:overlaps_mapped_place_slot" and d["population_records"] == []


def test_address_and_photo_rays_cannot_auto_author_building_or_face():
    # A listing address and camera position are evidence for a human author;
    # neither is executable placement authority by itself.
    s1 = _obs("x1", "candidate_sign_1", 5.0, 0.0, 0.5, ann="a-x1")
    s2 = _obs("x2", "candidate_sign_2", 5.0, 0.0, 0.5, ann="a-x2")
    adj = _adj([_dec(1, "a-x1", "business", business_key="tenant", role="logo", camera_epsg32654=[35, -6]),
                _dec(2, "a-x2", "business", business_key="corner", role="logo", camera_epsg32654=[105, -6])],
               [{"business_key": "tenant", "width_m": 2.0,
                 "address_geocode": {"address": "Testtown 9丁目 4-2", "E": 20.0, "N": 50.0}},
                {"business_key": "corner", "width_m": 2.0,
                 "address_geocode": {"address": "Testtown 9丁目 7-1", "E": 105.0, "N": 5.0}}])
    adj["mapped_addresses"] = {"p:other": {"address": "Testtown 9丁目4番2号"}}
    d = resolve([s1, s2], WORLD, adjudication=adj, slot_half_m=1.0)
    businesses = [e for e in d["entities"] if e["entity_id"].startswith("entity:business")]
    assert {e["state"] for e in businesses} == {"debt:business_unplaced"}
    assert d["population_records"] == []


def test_manual_building_and_face_win_even_when_photo_ray_points_elsewhere():
    s = _obs("x3", "candidate_sign_3", 5.0, 0.0, 0.5, ann="a-x3")
    adj = _adj(
        [_dec(3, "a-x3", "business", business_key="manual", role="logo",
              camera_epsg32654=[5, -6], bearing_deg=0)],
        [{"business_key": "manual", "host_building_id": "b:c", "width_m": 2.0,
          "face_index": 2, "along_face_m": 4.0,
          "basis": "manual public-address to canonical-map decision",
          "face_basis": "manual facade review"}],
    )
    d = resolve([s], WORLD, adjudication=adj, slot_half_m=1.0)
    e = next(e for e in d["entities"] if e["entity_id"] == "entity:business:manual")
    assert e["state"] == "entity"
    assert e["host"]["building_id"] == "b:c" and e["host"]["face_index"] == 2
    rec = d["population_records"][0]
    assert abs(rec["E"] - 106.0) < 1e-6 and abs(rec["N"] - 10.0) < 1e-6
    assert rec["position_uncertainty_basis"] == "manual_authored_building_face_slot"


def test_manual_building_without_manual_face_does_not_emit():
    s = _obs("x4", "candidate_sign_4", 5.0, 0.0, 0.5, ann="a-x4")
    adj = _adj([_dec(4, "a-x4", "business", business_key="no_face", role="logo")],
               [{"business_key": "no_face", "host_building_id": "b:c", "width_m": 2.0,
                 "basis": "manual public-address to canonical-map decision"}])
    d = resolve([s], WORLD, adjudication=adj)
    e = next(e for e in d["entities"] if e["entity_id"] == "entity:business:no_face")
    assert e["state"] == "debt:business_face_unresolved"
    assert d["population_records"] == []


def test_unboxed_business_can_emit_only_from_complete_manual_record():
    adj = _adj([], [{
        "business_key": "unboxed", "shows": "Unboxed Cafe sign",
        "host_building_id": "b:c", "face_index": 2, "along_face_m": 5.0,
        "width_m": 3.0, "basis": "manual address-to-map decision",
        "face_basis": "manual panorama facade review",
        "source_capture_ids": ["mapillary_123"],
        "logo_crop": "artifacts/manual/unboxed.jpg",
    }])
    d = resolve([], WORLD, adjudication=adj, slot_half_m=1.0)
    e = next(e for e in d["entities"] if e["entity_id"] == "entity:business:unboxed")
    assert e["state"] == "entity" and e["logo_crop"] == "artifacts/manual/unboxed.jpg"
    assert e["observations"]["supporting"] == []
    assert d["conservation"] == {"observations": 0, "consumed": 0, "unresolved": 0}
    rec = d["population_records"][0]
    assert rec["capture_ids"] == ["mapillary_123"]
    assert rec["annotation_ids"] == [] and rec["logo_crop"].endswith("unboxed.jpg")


def test_scene_fixture_file_admits_only_explicit_manual_acceptance(tmp_path):
    adjudication = tmp_path / "authored_observation_positions.json"
    adjudication.write_text(json.dumps({"identities": [], "business_placements": []}))
    (tmp_path / "authored_scene_fixtures.json").write_text(json.dumps({"fixtures": [
        {"fixture_id": "SF-projected", "module_id": "pole_streetlight", "E": 1, "N": 2},
        {"fixture_id": "SF-manual", "module_id": "horizontal_traffic_signal", "E": 3, "N": 4,
         "manual_review": {"status": "accepted"}},
    ]}))
    loaded = load_adjudication(adjudication)
    assert [x["fixture_id"] for x in loaded["scene_fixtures"]] == ["SF-manual"]
    assert loaded["scene_fixture_debt"] == [{
        "fixture_id": "SF-projected",
        "reason": "projected fixture candidate is not manually accepted",
    }]


def test_not_identity_observation_owned_by_an_authored_fixture_is_consumed_not_debt():
    review = {"status": "accepted", "reviewed_by": "r", "reviewed_on": "2026-10-13",
              "module_basis": "m", "position_basis": "p", "carrier_basis": "c", "orientation_basis": "o"}
    fx = {"fixture_id": "SF-vend", "module_id": "vending_white_red_blue", "E": 1.0, "N": 0.2,
          "carrier_id": "carrier:wall", "yaw_deg": 0.0, "position_uncertainty_m": 0.5,
          "manual_review": review}
    inst = {"instance_id": "inst-9", "annotation_ids": ["ann-v"], "capture_ids": ["c"],
            "module_id": None, "population_role": "custom_identity", "E": 1.0, "N": 0.2}
    adj = {"identities": {"ann-v": {"decision_id": "D-1", "annotation_ids": ["ann-v"],
                                    "kind": "not_identity", "shows": "vending machine",
                                    "owned_by_scene_fixture": "SF-vend"}},
           "scene_fixtures": [fx],
           "zones": [{"carrier_id": "carrier:wall", "carrier_class": "building",
                      "polyline_epsg32654": [[0, 0], [2, 0]]}]}
    d = resolve([inst], WORLD, adjudication=adj)
    assert d["unresolved"] == []
    assert d["observation_to_entity"]["inst-9"] == "entity:fixture:authored:SF-vend"
    assert [r.get("authored_fixture_id") for r in d["population_records"]] == ["SF-vend"]


def test_manually_rejected_scene_fixture_is_decided_not_debt(tmp_path):
    adjudication = tmp_path / "authored_observation_positions.json"
    adjudication.write_text(json.dumps({"identities": [], "business_placements": []}))
    (tmp_path / "authored_scene_fixtures.json").write_text(json.dumps({"fixtures": [
        {"fixture_id": "SF-dup", "module_id": "pole_streetlight", "E": 1, "N": 2,
         "manual_review": {"status": "rejected", "reviewed_by": "test reviewer",
                           "reviewed_on": "2026-10-12", "reject_reason": "duplicate of SF-keep"}},
        {"fixture_id": "SF-unsigned", "module_id": "pole_streetlight", "E": 1, "N": 2,
         "manual_review": {"status": "rejected"}},
    ]}))
    loaded = load_adjudication(adjudication)
    assert [x["fixture_id"] for x in loaded["scene_fixture_rejections"]] == ["SF-dup"]
    assert [x["fixture_id"] for x in loaded["scene_fixture_debt"]] == ["SF-unsigned"]
    d = resolve([], WORLD, adjudication=loaded)
    assert [x["instance_id"] for x in d["unresolved"]] == ["SF-unsigned"]
    assert d["rejected_scene_fixtures"] == [{"fixture_id": "SF-dup", "reject_reason": "duplicate of SF-keep",
                                             "reviewed_by": "test reviewer"}]
    assert d["population_records"] == []


def test_manual_scene_fixture_uses_authored_carrier_and_orientation_not_nearest():
    review = {
        "status": "accepted", "reviewed_by": "test reviewer", "reviewed_on": "2026-10-12",
        "module_basis": "traffic head seen directly", "position_basis": "two-view manual triangulation",
        "carrier_basis": "mapped junction signal carrier", "orientation_basis": "signal faces west",
    }
    fx = {
        "fixture_id": "SF-manual", "module_id": "horizontal_traffic_signal",
        "variant": "three_lamp_black_head",
        "E": 1.0, "N": 0.2, "carrier_id": "carrier:chosen", "yaw_deg": 270.0,
        "run_length_m": 2.75,
        "position_uncertainty_m": 0.4,
        "sightings": [{"capture_id": "mapillary_test"},
                      {"capture_id": "mapillary_other", "disposition": "excluded_different_object"}],
        "manual_review": review,
    }
    adj = {
        "scene_fixtures": [fx],
        "scene_fixture_debt": [{"fixture_id": "SF-pending", "reason": "pending manual review"}],
        "zones": [
            {"carrier_id": "carrier:near", "carrier_class": "road", "polyline_epsg32654": [[0, 0], [2, 0]]},
            {"carrier_id": "carrier:chosen", "carrier_class": "road", "polyline_epsg32654": [[0, 5], [2, 5]]},
        ],
    }
    d = resolve([], WORLD, adjudication=adj)
    assert [x["instance_id"] for x in d["unresolved"]] == ["SF-pending"]
    rec = d["population_records"][0]
    assert rec["carrier_id"] == "carrier:chosen"
    assert rec["variant"] == "three_lamp_black_head"
    assert rec["yaw_deg"] == 270.0
    assert rec["run_length_m"] == 2.75
    assert rec["capture_ids"] == ["mapillary_test"]
    assert rec["position_uncertainty_basis"] == "two-view manual triangulation"


def _run_root(tmp_path, entities, fronts, registered=()):
    (tmp_path / "observation_registration").mkdir()
    (tmp_path / "observation_registration" / "identity_entities.json").write_text(json.dumps(entities))
    (tmp_path / "observation_registration" / "registered_instances.json").write_text(json.dumps(list(registered)))
    (tmp_path / "place_front_instances.json").write_text(json.dumps(fronts))
    (tmp_path / "terrain_metadata.json").write_text(json.dumps({"content_scale": 1.0}))
    return tmp_path


def _front(fid, parent, xy, w=4.0, edge=0):
    return {"feature_id": fid, "parent_building_feature_id": parent, "front_edge_index": edge,
            "front_anchor_world_xy": list(xy), "fitted_width_m": w}


def test_gate_refuses_ownerless_misplaced_and_overlapping_storefronts(tmp_path):
    d = resolve([], WORLD)
    d.pop("population_records")
    fronts = [_front("p:brandex", "b:a", (5, 0)),
              _front("p:other", "b:a", (6, 0)),        # wrong host AND same slot
              _front("p:nowhere", "b:c", (100, 0))]    # no entity
    g = gate(_run_root(tmp_path, d, fronts))
    rules = sorted({x["rule"] for x in g["violations"]})
    assert not g["pass"] and rules == ["G2", "G3", "G4"]


def test_gate_passes_one_owner_per_entity_on_its_host(tmp_path):
    d = resolve([], WORLD)
    d.pop("population_records")
    fronts = [_front("p:brandex", "b:a", (5, 0)), _front("p:other", "b:b", (35, 0))]
    assert gate(_run_root(tmp_path, d, fronts))["pass"]
