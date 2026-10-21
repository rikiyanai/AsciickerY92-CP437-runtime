import hashlib
import json
from pathlib import Path

from scripts.pipeline.official_road_source import refresh_official_road_source


UPDATE_XML = """<?xml version="1.0" encoding="utf-8"?>
<news>
  <item><label>道路台帳現況平面図</label><time>令和8年8月4日</time></item>
  <item><label>道路・水路網図</label><time>令和8年8月4日</time></item>
</news>
""".encode()


def _write_inputs(tmp_path: Path, *, rm_date="2026-08-04", gap=False):
    route = tmp_path / "route.json"
    route.write_text("{}\n", encoding="utf-8")
    route_hash = hashlib.sha256(route.read_bytes()).hexdigest()
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "schema": "y92.official_road_source_policy.v1",
        "adapter_id": "test.adapter",
        "update_feed_endpoint_id": "updates",
        "phase1_required_layers": ["GH", "RM"],
        "update_labels": {
            "道路台帳現況平面図": "GH",
            "道路・水路網図": "RM",
        },
        "refresh_endpoints": [
            {"id": "updates", "url": "https://example.test/update.xml"},
            {"id": "terms", "url": "https://example.test/terms.html"},
        ],
    }), encoding="utf-8")
    classification = tmp_path / "classification.json"
    classification.write_text(json.dumps({
        "schema": "y92.official_road_classification.v1",
        "adapter_id": "test.adapter",
        "route": {"sha256": route_hash, "length_m": 10.0},
        "source_layer_updates": {"GH": "2026-08-04", "RM": rm_date},
        "management_spans": [
            {
                "start_m": 1.0 if gap else 0.0,
                "end_m": 10.0,
                "management_class": "ota_managed",
                "route_key": "1",
            }
        ],
        "phase1_output_boundary": {"legal_width_selected": False},
    }), encoding="utf-8")
    return policy, classification, route


def _fetch(url):
    data = UPDATE_XML if url.endswith("update.xml") else b"official terms"
    return data, {"last-modified": "Wed, 02 Sep 2026 00:14:03 GMT", "etag": '"x"'}


def test_refresh_passes_only_with_live_matching_versions_and_full_coverage(tmp_path):
    policy, classification, route = _write_inputs(tmp_path)
    receipt = refresh_official_road_source(
        policy_path=policy,
        classification_path=classification,
        route_path=route,
        receipt_path=tmp_path / "receipt.json",
        fetcher=_fetch,
        fetched_at="2026-09-16T00:00:00+00:00",
    )
    assert receipt["phase1_step1_gate"] == {"passed": True, "failures": []}
    assert receipt["live_layer_updates"] == {"GH": "2026-08-04", "RM": "2026-08-04"}
    assert receipt["claim_boundary"]["drawing_or_tile_retained"] is False
    # FL-4813: Step 1 exposes only its own pass state; the final gate owns
    # generation_authorized and no alias of it.
    assert "generation_authorized" not in receipt
    assert "downstream_sequence_gate" not in receipt
    assert set(receipt["fetched_endpoints"]) == {"updates", "terms"}


def test_refresh_fails_closed_when_official_layer_version_changes(tmp_path):
    policy, classification, route = _write_inputs(tmp_path, rm_date="2026-07-01")
    receipt = refresh_official_road_source(
        policy_path=policy,
        classification_path=classification,
        route_path=route,
        receipt_path=tmp_path / "receipt.json",
        fetcher=_fetch,
    )
    assert receipt["phase1_step1_gate"]["passed"] is False
    assert receipt["classification_check"]["changed_layers"]["RM"] == {
        "reviewed": "2026-07-01", "live": "2026-08-04"
    }


def test_refresh_fails_closed_on_route_coverage_gap(tmp_path):
    policy, classification, route = _write_inputs(tmp_path, gap=True)
    receipt = refresh_official_road_source(
        policy_path=policy,
        classification_path=classification,
        route_path=route,
        receipt_path=tmp_path / "receipt.json",
        fetcher=_fetch,
    )
    assert receipt["phase1_step1_gate"]["passed"] is False
    assert "starts at 1.0, expected 0.0" in " ".join(
        receipt["phase1_step1_gate"]["failures"]
    )


def test_direct_runner_refreshes_before_observation_ingest_and_base_compile():
    source = Path("scripts/sbu_e2e_run.py").read_text(encoding="utf-8")
    refresh = source.index("refresh_official_road_source(")
    generation_block = source.index("generation_authorized", refresh)
    observation_join = source.index("ingest_configured_corpora(", refresh)
    generation = source.index("compile_base(", observation_join)
    assert refresh < generation_block < observation_join < generation
