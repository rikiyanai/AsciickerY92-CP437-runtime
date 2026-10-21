"""Jev adhoc-routing correction tests (FL-4853).

Proves: no Jev output can create H1; probabilities and provenance are
retained; cache invalidates on evidence/model/rule changes; service
failures remain H6; stale Jev results cannot enter a new manifest;
H4/H5 cannot issue without their required evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.maintainer import adhoc_routing as routing
from scripts.maintainer import cleaner_scan as scan
from scripts.maintainer.jev_adhoc_adapter import (
    QUESTION_SCHEMA_VERSION,
    RULE_VERSION,
    CannedJevBackend,
    cache_key_for,
)


def _ambiguous_row(**overrides):
    row = {
        "path": "scripts/adhoc/2026-09-22-auto-python-abcdef1234.py",
        "sha256": "0" * 64,
        "size_bytes": 120,
        "age_days": 1.0,
        "tracked": False,
        "fl_id_in_name": None,
        "fl_name_body_match": False,
        "is_auto_chaff_name": False,
        "repo_ref_count": 0,
        "ref_paths": [],
        "pipeline_imports": False,
        "pipeline_import_lines": [],
        "ref_evidence_complete": True,
        "cgc_callers_n": None,
        "cgc_complexity": None,
        "cgc_language_supported": True,
    }
    row.update(overrides)
    return row

def _with_deterministic(rows):
    return [{**r, "advisory_route": routing.suggest_h16(r)} for r in rows]


def test_jev_criteria_exclude_h1():
    assert "H1" not in routing.JEV_CRITERIA
    assert set(routing.JEV_ALLOWED_ROUTES) == {"H2", "H3", "H4", "H5", "H6"}


def test_no_jev_output_can_create_h1():
    row = _ambiguous_row()

    class H1Backend:
        def decide(self, state):
            return "H1"

    out = routing.suggest_h16(row, H1Backend())
    assert out["route"] == "H6"
    assert out["authorizes_deletion"] is False
    assert out["jev_status"].startswith("clamped:")

    class UnknownBackend:
        def decide(self, state):
            return "H9"

    out2 = routing.suggest_h16(row, UnknownBackend())
    assert out2["route"] == "H6"


def test_deterministic_routes_never_consult_jev():
    hits = []

    class SpyBackend:
        def decide(self, state):
            hits.append(state)
            return "H6"

    h2 = _ambiguous_row(fl_id_in_name="FL-1", fl_name_body_match=True)
    h1 = _ambiguous_row(is_auto_chaff_name=True, tracked=False, age_days=30.0,
                        ref_evidence_complete=True)
    assert routing.suggest_h16(h2, SpyBackend())["route"] == "H2"
    assert routing.suggest_h16(h1, SpyBackend())["route"] == "H1"
    assert hits == []


def test_probabilities_and_provenance_retained():
    row = _ambiguous_row()
    rows = _with_deterministic([row])
    backend = CannedJevBackend(route="H2", confidence=0.7, model="canned-test-v1")
    cache: dict = {}
    receipt = scan.apply_jev_to_rows(rows, backend, cache, "canned-test-v1")
    assert receipt["requests"] == 1
    kept = rows[0]["advisory_route"]["jev_receipt"]
    assert kept["choice"] == "H2"
    assert abs(sum(kept["probabilities"].values()) - 1.0) < 1e-9
    assert set(kept["probabilities"]) == {"H2", "H3", "H4", "H5", "H6"}
    assert kept["confidence"] == 0.7
    assert kept["model"] == "canned-test-v1"
    assert kept["requested_model"] == "canned-test-v1"
    assert kept["question_schema"] == QUESTION_SCHEMA_VERSION
    assert kept["rule_version"] == RULE_VERSION
    assert kept["usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert "latency_ms" in kept and "timestamp" in kept
    assert rows[0]["jev"]["cache_key"] in cache


def test_cache_invalidates_on_evidence_model_rule_changes():
    state = routing.to_jev_state(_ambiguous_row())
    k1 = cache_key_for("a" * 64, state, model_version="jev-latest")
    k2 = cache_key_for("b" * 64, state, model_version="jev-latest")
    k3 = cache_key_for("a" * 64, state, model_version="jev-next")
    k4 = cache_key_for(
        "a" * 64, state, model_version="jev-latest",
        question_schema="other", rule_version=RULE_VERSION,
    )
    assert len({k1, k2, k3, k4}) == 4

    # Evidence change on the same path misses the old cache entry.
    rows = _with_deterministic([_ambiguous_row(sha256="a" * 64)])
    cache: dict = {}
    backend = CannedJevBackend(route="H3")
    scan.apply_jev_to_rows(rows, backend, cache, "canned-test-v1")
    assert len(cache) == 1
    rows2 = _with_deterministic([_ambiguous_row(sha256="b" * 64)])
    out = scan.apply_jev_to_rows(rows2, backend, cache, "canned-test-v1")
    assert out["requests"] == 1 and out["cache_hits"] == 0


def test_service_failures_remain_h6():
    for backend in (
        _FailingBackend(RuntimeError("boom")),
        _MalformedBackend(),
    ):
        rows = _with_deterministic([_ambiguous_row()])
        out = scan.apply_jev_to_rows(rows, backend, {}, "m")
        assert rows[0]["advisory_route"]["route"] == "H6"
        assert rows[0]["jev"]["status"].startswith("failed:")
        assert out["failures"] == 1

    # Missing backend (e.g. no credentials at init) is recorded, not judged.
    rows = _with_deterministic([_ambiguous_row()])
    scan.apply_jev_to_rows(rows, None, {}, "m")
    assert rows[0]["advisory_route"]["route"] == "H6"
    assert rows[0]["jev"]["status"] == "disabled"


def test_stale_jev_results_cannot_enter_new_manifest():
    row = _ambiguous_row(sha256="a" * 64)
    rows = _with_deterministic([row])
    cache: dict = {}
    scan.apply_jev_to_rows(rows, CannedJevBackend(route="H2"), cache, "canned-test-v1")
    assert len(cache) == 1
    # Same cache presented to a changed-evidence row must miss, not attach.
    changed = _with_deterministic([_ambiguous_row(sha256="b" * 64)])
    out = scan.apply_jev_to_rows(changed, CannedJevBackend(route="H2"), cache, "canned-test-v1")
    assert out["cache_hits"] == 0
    assert changed[0]["jev"]["cache_hit"] is False


def test_h4_h5_gated_on_evidence():
    assert routing.clamp_jev_route("H4", _ambiguous_row()) is None
    assert routing.clamp_jev_route("H5", _ambiguous_row()) is None
    h4row = _ambiguous_row(fixture_contract="table X", consuming_tests=["t1"])
    h5row = _ambiguous_row(
        candidate_counterpart="scripts/pipeline/x.py",
        counterpart_sha256="f" * 64, interfaces=["f()"],
        side_effects="none", behavioral_equivalence="diff-receipt-1",
    )
    assert routing.clamp_jev_route("H4", h4row) == "H4"
    assert routing.clamp_jev_route("H5", h5row) == "H5"

    # End-to-end: Jev H4 without evidence stays H6 with a clamp record.
    rows = _with_deterministic([_ambiguous_row()])
    scan.apply_jev_to_rows(rows, CannedJevBackend(route="H4"), {}, "canned-test-v1")
    assert rows[0]["advisory_route"]["route"] == "H6"
    assert rows[0]["jev"]["status"] == "clamped:missing-evidence"


def test_reviewer_packets_bind_manifest_hash(tmp_path):
    rows = _with_deterministic([
        _ambiguous_row(path="scripts/adhoc/a.py", fl_id_in_name="FL-1",
                       fl_name_body_match=True),
        _ambiguous_row(path="scripts/adhoc/b.py"),
    ])
    manifest = {"manifest_hash": "m" * 64, "head": "h" * 40,
                "timestamp": "t", "adhoc_census": rows}
    paths = scan.generate_reviewer_packets(manifest, tmp_path)
    assert paths, "expected H2 + H6 packets"
    import json

    for p in paths:
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
        assert doc["manifest_hash"] == "m" * 64
        assert doc["head"] == "h" * 40
        assert doc["decision_required"]
        for f in doc["files"]:
            assert f["sha256"]


class _FailingBackend:
    def __init__(self, exc):
        self._exc = exc

    def decide_with_receipt(self, state):
        raise self._exc


class _MalformedBackend:
    def decide_with_receipt(self, state):
        return {"choice": "H2"}  # no probabilities/usage shape

def test_h1_age_evidence_gate():
    young = _ambiguous_row(is_auto_chaff_name=True, tracked=False,
                           age_days=0.5, ref_evidence_complete=True)
    out = routing.suggest_h16(young)
    assert out["route"] == "H6"
    assert "H1 gate unmet" in out["rationale"]
    assert out["authorizes_deletion"] is False

    aged = _ambiguous_row(is_auto_chaff_name=True, tracked=False,
                          age_days=30.0, ref_evidence_complete=True)
    assert routing.suggest_h16(aged)["route"] == "H1"

    unevidenced = _ambiguous_row(is_auto_chaff_name=True, tracked=False,
                                 age_days=30.0, ref_evidence_complete=False)
    assert routing.suggest_h16(unevidenced)["route"] == "H6"

    ok, _ = routing.h1_eligible({**aged})
    assert ok is True
    for bad in ({"age_days": 14.0}, {"age_days": 0.0},
                {"tracked": True}, {"repo_ref_count": 1},
                {"is_auto_chaff_name": False},
                {"ref_evidence_complete": False}):
        row = {**aged, **bad}
        eligible, _ = routing.h1_eligible(row)
        assert eligible is False


def test_jev_state_contains_bounded_godot_cpp_semantics():
    row = _ambiguous_row(
        path="scripts/adhoc/2026-auto-shell-a.sh",
        basename="2026-auto-shell-a.sh",
        topic_slug="trueff-gbuffer-probe",
        source_language="shell",
        semantic_tags=["godot", "glsl", "shader", "trueff"],
        semantic_excerpt="godot --headless --path godot_project\ninspect true_ff.glsl",
        semantic_excerpt_truncated=False,
        ref_paths=["godot_project/render/ascii/true_ff.glsl"],
        test_links=["godot_project/scripts/tests/test_true_ff.gd"],
    )
    state = routing.to_jev_state(row)
    assert state["identity"]["path"] == row["path"]
    assert state["identity"]["semantic_tags"] == row["semantic_tags"]
    assert "godot --headless" in state["bounded_source_evidence"]["excerpt"]
    assert state["repo_signals"]["test_links"] == row["test_links"]


def test_semantic_excerpt_is_bounded_and_redacts_secrets():
    text = (
        "TYPESAFE_API_KEY=do-not-leak godot --headless\n"
        + "shader render TrueFF " * 400
    )
    excerpt, truncated, tags = scan._bounded_semantic_evidence(text)
    assert "do-not-leak" not in excerpt
    assert "TYPESAFE_API_KEY=<redacted>" in excerpt
    assert len(excerpt) <= scan._SEMANTIC_EXCERPT_LIMIT
    assert truncated is True
    assert {"godot", "shader", "render", "trueff"}.issubset(set(tags))


def test_timeout_reaches_client_boundary(monkeypatch):
    import os
    from scripts.maintainer import jev_adhoc_adapter as adapter
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    captured: dict = {}

    class _Ans:
        choice = "H3"
        confidence = 0.85
        probabilities = {"H2": 0.05, "H3": 0.85, "H4": 0.03,
                         "H5": 0.02, "H6": 0.05}

    class _Usage:
        input_tokens = 12
        output_tokens = 4

    class _Resp:
        choices = {"adhoc_disposition": _Ans()}
        model = "jev-test"
        usage = _Usage()
        request_id = "req-1"

    class _FakeClient:
        def __init__(self):
            self.calls = []

        def system_one(self, state, questions):
            self.calls.append((state, questions))
            return _Resp()

    fake = _FakeClient()

    def _factory(kwargs):
        captured.update(kwargs)
        return fake

    backend = adapter.TypeSafeJevBackend(model="m", timeout=7.5,
                                        client_factory=_factory)
    receipt = backend.decide_with_receipt({"a": 1})
    assert captured["model"] == "m"
    assert float(captured["retry"].timeout) == 7.5
    assert len(fake.calls) == 1
    assert receipt["choice"] == "H3"
    assert receipt["usage"] == {"input_tokens": 12, "output_tokens": 4}


def _good_receipt(model="canned-test-v1"):
    return {
        "question_id": adapter_qid(), "choice": "H2",
        "probabilities": {"H2": 0.7, "H3": 0.1, "H4": 0.05,
                          "H5": 0.05, "H6": 0.1},
        "confidence": 0.7, "model": model,
        "request_id": None, "request_schema": adapter_req_schema(),
        "question_schema": adapter_qschema(), "rule_version": adapter_rule(),
        "usage": {"input_tokens": 3, "output_tokens": 1},
        "latency_ms": 9, "timestamp": "2026-09-22T00:00:00+00:00",
    }


def adapter_qid():
    from scripts.maintainer.jev_adhoc_adapter import QUESTION_ID
    return QUESTION_ID


def adapter_req_schema():
    from scripts.maintainer.jev_adhoc_adapter import REQUEST_SCHEMA
    return REQUEST_SCHEMA


def adapter_qschema():
    from scripts.maintainer.jev_adhoc_adapter import QUESTION_SCHEMA_VERSION
    return QUESTION_SCHEMA_VERSION


def adapter_rule():
    from scripts.maintainer.jev_adhoc_adapter import RULE_VERSION
    return RULE_VERSION


def test_strict_receipt_validation():
    from scripts.maintainer.jev_adhoc_adapter import (
        JevError, validate_receipt_dict)
    validate_receipt_dict(_good_receipt(), "canned-test-v1")

    resolved = _good_receipt()
    resolved["requested_model"] = "jev-latest"
    resolved["model"] = "jev-1.13.0"
    validate_receipt_dict(resolved, "jev-latest")
    import math
    bad_variants = []
    truncated = _good_receipt()
    del truncated["usage"]
    bad_variants.append(truncated)
    no_ts = _good_receipt()
    del no_ts["timestamp"]
    bad_variants.append(no_ts)
    no_qid = _good_receipt()
    del no_qid["question_id"]
    bad_variants.append(no_qid)
    neg = _good_receipt()
    neg["probabilities"] = {"H2": -0.1, "H3": 0.5, "H4": 0.2,
                            "H5": 0.2, "H6": 0.2}
    bad_variants.append(neg)
    nan = _good_receipt()
    nan["probabilities"] = {"H2": math.nan, "H3": 0.5, "H4": 0.2,
                            "H5": 0.2, "H6": 0.1}
    bad_variants.append(nan)
    over = _good_receipt()
    over["probabilities"] = {"H2": 1.5, "H3": 0.0, "H4": 0.0,
                             "H5": 0.0, "H6": -0.5}
    bad_variants.append(over)
    wrong_schema = _good_receipt()
    wrong_schema["request_schema"] = "other.v9"
    bad_variants.append(wrong_schema)
    wrong_qschema = _good_receipt()
    wrong_qschema["question_schema"] = "other"
    bad_variants.append(wrong_qschema)
    wrong_model = _good_receipt(model="other-model")
    bad_variants.append(wrong_model)
    for bad in bad_variants:
        try:
            validate_receipt_dict(bad, "canned-test-v1")
        except JevError:
            continue
        raise AssertionError("invalid receipt accepted: %r" % (bad,))
    # Invalid receipts never enter the cache or the route.
    rows = _with_deterministic([_ambiguous_row()])
    out = scan.apply_jev_to_rows(rows, _CannedInvalid(), {}, "m")
    assert rows[0]["advisory_route"]["route"] == "H6"
    assert out["failures"] == 1


def test_manifest_hash_binds_jev_summary():
    from scripts.maintainer import cleaner_apply
    rows: list = []
    stale: list = []
    h1 = scan.manifest_hash_for("head", rows, stale, "git", {"requests": 0})
    h2 = scan.manifest_hash_for("head", rows, stale, "git", {"requests": 3})
    assert h1 != h2
    manifest = {"head": "head", "adhoc_census": rows,
                "stale_artifacts": stale, "ref_enumeration": "git",
                "manifest_hash": h2, "jev": {"requests": 3}}
    assert cleaner_apply.recompute_manifest_hash(manifest) == h2
    manifest["jev"] = {"requests": 4}
    try:
        cleaner_apply.check_manifest_integrity(manifest)
    except cleaner_apply.DriftError:
        pass
    else:
        raise AssertionError("edited jev summary passed integrity")


def test_packets_have_real_owners_and_evidence(tmp_path):
    import json
    rows = _with_deterministic([
        _ambiguous_row(path="scripts/adhoc/fl.py", fl_id_in_name="FL-9",
                       fl_name_body_match=True, repo_ref_count=2,
                       ref_paths=["docs/plan.md", "scripts/x.py"]),
        _ambiguous_row(path="scripts/adhoc/nolink.py", repo_ref_count=2,
                       ref_paths=["scripts/tool.py"],
                       receipt_links=[], test_links=[]),
        _ambiguous_row(path="scripts/adhoc/pipe.py", repo_ref_count=1,
                       ref_paths=["scripts/pipeline/p.py"],
                       pipeline_callers=["scripts/pipeline/p.py"],
                       pipeline_imports=True,
                       pipeline_import_lines=["from scripts.pipeline.q import f"]),
        _ambiguous_row(path="scripts/adhoc/amb.py"),
    ])
    manifest = {"manifest_hash": "m" * 64, "head": "h" * 40,
                "timestamp": "t", "adhoc_census": rows}
    docs = [json.loads(Path(p).read_text(encoding="utf-8"))
            for p in scan.generate_reviewer_packets(manifest, tmp_path)]
    owners = [d["owner"] for d in docs]
    assert not [o for o in owners if "unlinked" in o.lower()]
    assert any(o == "FL owner FL-9" for o in owners)
    assert any(o.startswith("lane reviewer") for o in owners)
    h3 = [d for d in docs if d["route"] == "H3"]
    assert len(h3) == 1 and h3[0]["evidence_complete"] is True
    assert h3[0]["evidence"]["callers"]["scripts/adhoc/pipe.py"] == [
        "scripts/pipeline/p.py"]
    assert h3[0]["evidence"]["observed_pipeline_imports"][
        "scripts/adhoc/pipe.py"] == ["from scripts.pipeline.q import f"]
    h2files = [f for d in docs if d["route"] == "H2" for f in d["files"]]
    assert all(f["ref_paths"] for f in h2files)


class _CannedInvalid:
    def decide_with_receipt(self, state):
        return {"choice": "H2"}
