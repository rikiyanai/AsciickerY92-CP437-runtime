"""FL-4907 family-isolation source contracts (rung 0).

These checks read production ingress/binding code. They do not execute a
GDScript object, RenderingDevice, family recovery or rendered frame. The full
P1/P2 fixed descriptor ABI still needs an explicit missing-family consumer
variant; this test deliberately does not claim that gap is repaired.
"""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"
WORLD = (FEATURES / "TrueFFWorldFeatureSources.gd").read_text()
BINDINGS = (FEATURES / "TrueFFLiveSourceBindings.gd").read_text()


def function(source, name):
    start = re.search(rf"^(?:static )?func {name}\(", source, re.M)
    assert start, name
    end = re.search(r"^(?:static )?func |\Z", source[start.end():], re.M)
    return source[start.start():start.end() + end.start()]


def test_old_aggregate_family_failure_and_binding_owner_are_deleted():
    assert "func _reject(" not in WORLD
    assert "var _reason:" not in WORLD
    assert '_bindings.call("prepare",' not in WORLD
    assert '_bindings.call("update_live",' not in WORLD
    assert "func prepare(" not in BINDINGS
    assert "func update_live(" not in BINDINGS
    assert "func _borrow(" not in BINDINGS
    for symbol in ("_zero_sites", "_zero_wake_rows", "_zero_wake_clock"):
        assert symbol not in BINDINGS
    prepare = function(WORLD, "prepare")
    assert '_publish_family(family, _prepare_family(family, _scene), delta)' in prepare
    assert 'return _reject(family' not in prepare


def test_withdrawal_names_previously_published_sources_and_bank_providers():
    remember = function(WORLD, "_remember_changes")
    assert 'sources[key] = true' in remember and 'banks[key] = true' in remember
    assert 'sources.erase(key)' in remember and 'banks.erase(key)' in remember
    withdraw = function(WORLD, "_withdraw_family")
    assert '"removals": sources.keys()' in withdraw
    assert '"bank_removals": banks.keys()' in withdraw
    assert '"upserts": {}' in withdraw and '"banks": {}' in withdraw
    assert '_lane(family).call("release")' in withdraw
    assert '_bindings.call("withdraw_family", family, reason)' in withdraw
    assert '_published_sources.erase(family)' in withdraw
    assert '_published_banks.erase(family)' in withdraw
    assert '_facts[family] = {"ready": false, "reason": reason}' in withdraw
    assert '_scheduled_flow = null' in withdraw and '_sea = NAN' in withdraw


def test_live_failure_withdraws_one_lane_without_residency_rebuild():
    live = function(WORLD, "update_live")
    assert 'if _family_rejections.has(family):' in live
    assert '_withdraw_family(family,' in live
    assert 'continue' in live
    assert '_bindings.call("update_family", family, facts, _sea)' in live
    assert 'prepare_family' not in live and 'call("prepare"' not in live
    assert '.keys()' not in live and '.values()' not in live
    assert '_water_resource_reason(scene)' in live
    resource = function(WORLD, "_water_resource_reason")
    assert 'owner != _scene.get(key)' in resource
    assert 'current[key] != bindings.get(key, RID())' in resource


def test_recovery_requires_explicit_family_ingress_and_preserves_other_lanes():
    change = function(WORLD, "apply_changes")
    assert 'changes.get("residency", {})' in change
    assert 'if _family_rejections.has(family):' in change
    assert '_prepare_family(family, residency)' in change
    assert '_bindings.call("release")' not in change
    assert 'for family: String in FAMILIES' not in change
    prepare = function(BINDINGS, "prepare_family")
    assert 'match family:' in prepare
    assert '"water": accepted = _prepare_water' in prepare
    assert '"effects": accepted = _prepare_effects' in prepare
    assert '"sky": accepted = _prepare_sky' in prepare


def test_publication_readiness_does_not_certify_required_coverage():
    receipt = function(WORLD, "_receipt")
    assert '"publication_ready": _prepared' in receipt
    assert '"ready": complete' in receipt
    assert '"family_coverage_available": complete' in receipt
    assert '"coverage_complete": false' in receipt
    assert 'var omitted: Dictionary = _family_rejections.duplicate(false)' in receipt
    assert 'omitted[family + ".sources"] = errors' in receipt
    global_failure = function(WORLD, "_global_failure")
    assert '"publication_ready": false' in global_failure


def test_failed_bindings_are_absent_not_neutral_and_selected_sets_fail_closed():
    withdraw = function(BINDINGS, "withdraw_family")
    assert '_rids[BINDINGS.find(key)] = RID()' in withdraw
    assert '_rd.free_rid(rid)' in withdraw
    assert 'buffer_create' not in withdraw and '_zeros(' not in withdraw
    upload = function(BINDINGS, "update_family")
    assert 'withdraw_family(family,' in upload
    for name in ("update_family", "_update_water", "_update_effects", "_update_sky"):
        body = function(BINDINGS, name)
        assert 'buffer_create' not in body and 'uniform_set_create' not in body
    selected = function(BINDINGS, "uniform_set")
    assert 'if not _rids[binding].is_valid():' in selected
    assert 'return RID()' in selected
    assert 'for key: String in binding_keys(include_sky, include_effects):' in selected
    # Inactive resources are constrained to a positively accepted no-driven-row
    # state, never used after water-family failure or required-solver loss.
    water = function(BINDINGS, "_prepare_water")
    assert '(driven_rows > 0 and not flow_ready)' in water
    assert 'not flow_ready and driven_rows == 0' in water
    assert '"sites", "wake_rows", "wake_clock"' in water
