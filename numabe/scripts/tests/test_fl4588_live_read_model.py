# =============================================================
# test_fl4588_live_read_model.py
# =============================================================
# Focused FL-4588 checks for the live read-model cutover:
#
# 1. Every live presentation/readout leaf either has an application
#    handler or an explicit snapshotValueForLeaf binding (no dash orphans).
# 2. Disconnected evidence subscription claims are tombstoned.
# 3. Camera pose readouts bind to authoring-snapshot camera.* fields.
# 4. ControlWidget no longer invents "— from editor" or panel placeholders
#    as product UI for missing owners.
#
# These tests are source/registry contracts only — they do not claim
# headed proof.
# =============================================================

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
TRANSPORT = ROOT / "web/asciiid-authoring-shell/src/transport.ts"
CONTROL_WIDGET = ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"

SHOW_AS_READOUT = re.compile(r"^(read|show|display) the ", re.I)
PLACEHOLDER_KINDS = {"header", "preview", "diagram", "tree", "group"}
EDITABLE_KINDS = {"slider", "select", "color", "number", "text"}


def _load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _snapshot_value_cases(source: str) -> set[str]:
    """Collect leaf_id strings named by snapshotValueForLeaf case arms."""
    start = source.find("export function snapshotValueForLeaf")
    assert start >= 0, "snapshotValueForLeaf must exist in transport.ts"
    # Slice through the function body only so unrelated switch statements
    # elsewhere in the file cannot pollute the live-read-model census.
    body = source[start : source.find("\n}", start) + 2]
    return set(re.findall(r'case "([^"]+)":', body))


def test_evidence_subscription_claim_is_tombstoned():
    registry = _load_registry()
    by_id = {leaf["leaf_id"]: leaf for leaf in registry["leaves"]}
    leaf = by_id["evidence.artifacts.subscription_status"]
    assert leaf["status"] == "tombstoned"
    assert leaf["migration_status"] == "tombstone"
    assert leaf["proof_status"] == "TOMBSTONE_DECISION"
    assert leaf["read_model_fields"] == []
    assert leaf["application_service_handler"] is None


def test_cell_coordinate_dash_orphans_are_tombstoned():
    """Browser-only inspect selection is not an authoring-snapshot owner."""
    registry = _load_registry()
    by_id = {leaf["leaf_id"]: leaf for leaf in registry["leaves"]}
    for leaf_id in ("inspect.cell.coordinate.x", "inspect.cell.coordinate.y"):
        leaf = by_id[leaf_id]
        assert leaf["status"] == "tombstoned"
        assert leaf["migration_status"] == "tombstone"
        assert leaf["read_model_fields"] == []


def test_retired_camera_pose_readouts_remain_unavailable():
    registry = _load_registry()
    by_id = {leaf["leaf_id"]: leaf for leaf in registry["leaves"]}
    expected = ("scene.camera.pose.pitch", "scene.camera.pose.yaw", "scene.camera.pose.zoom")
    # FL-4588: operator-rejected frozen controls were retired at 8e7c9e6b4.
    # A read-model test must not require that deleted UI to become live again.
    for leaf_id in expected:
        leaf = by_id[leaf_id]
        assert leaf["status"] == "tombstoned"
        assert leaf["read_model_fields"] == []
        assert leaf["application_service_handler"] is None


def test_material_profile_status_stays_snapshot_wired():
    registry = _load_registry()
    by_id = {leaf["leaf_id"]: leaf for leaf in registry["leaves"]}
    leaf = by_id["material.profile.status"]
    assert leaf["status"] == "live"
    assert leaf["control_kind"] == "readout"
    assert "material_runtime.active_material" in leaf["read_model_fields"]
    cases = _snapshot_value_cases(TRANSPORT.read_text(encoding="utf-8"))
    assert "material.profile.status" in cases


def test_live_presentation_leaves_are_not_dash_orphans():
    """
    Every live leaf that ControlWidget would render as a readout must have
    a snapshotValueForLeaf case. Placeholder presentation kinds must not
    remain live without an application handler.
    """
    registry = _load_registry()
    cases = _snapshot_value_cases(TRANSPORT.read_text(encoding="utf-8"))
    orphans: list[str] = []
    for leaf in registry["leaves"]:
        if leaf.get("status") != "live":
            continue
        if leaf.get("application_service_handler"):
            continue
        leaf_id = leaf["leaf_id"]
        # Navigation tabs are filtered out of the HTML shell in App.tsx.
        if leaf_id.startswith("workspace.navigation."):
            continue
        # Browser document gestures have dedicated ControlWidget owners.
        if leaf_id in {
            "workspace.document.save_as.open",
            "workspace.document.open.open",
            "workspace.document.merge.open",
            "workspace.document.new",
        }:
            continue
        kind = leaf.get("control_kind")
        if kind in PLACEHOLDER_KINDS:
            orphans.append(f"placeholder-kind:{leaf_id}")
            continue
        is_readout = kind == "readout" or (
            kind in EDITABLE_KINDS
            and SHOW_AS_READOUT.match(leaf.get("short_help") or "")
        )
        if is_readout and leaf_id not in cases:
            orphans.append(f"unwired-readout:{leaf_id}")
    assert orphans == [], orphans


def test_control_widget_refuses_dash_and_panel_placeholders():
    source = CONTROL_WIDGET.read_text(encoding="utf-8")
    assert "— from editor" not in source
    assert "control-panel-placeholder" not in source
    assert "MISSING READ MODEL" in source
    assert "MISSING PRESENTATION OWNER" in source


def test_every_scoring_control_reads_the_native_authored_value():
    # FL-4512: exercise the browser's production projection, not its widget
    # fallback (which previously made eight absent fields look like zero).
    script = '''
import { snapshotValueForLeaf } from './src/transport.ts';
const fields = {detail: 73, tone: 26, density_bias: -17,
  tl: .25, tr: .5, ml: .75, mr: 1.25, bl: 2.5, br: 3.75};
const scoring = {detail_contrast: fields.detail, tone_contrast: fields.tone,
  density_bias: fields.density_bias,
  role_weights: [fields.tl, fields.tr, fields.ml, fields.mr, fields.bl, fields.br]};
const snapshot = {material_runtime: {scoring}};
console.log(JSON.stringify(Object.fromEntries(Object.keys(fields).map(key =>
  [key, snapshotValueForLeaf('material.score.' + key, snapshot)]))));
'''
    result = subprocess.run(
        ["npx", "--no-install", "tsx", "--eval", script],
        cwd=ROOT / "web/asciiid-authoring-shell",
        capture_output=True, text=True, check=True, timeout=60,
    )
    assert json.loads(result.stdout) == {
        "detail": 73, "tone": 26, "density_bias": -17,
        "tl": .25, "tr": .5, "ml": .75, "mr": 1.25, "bl": 2.5, "br": 3.75,
    }


def test_native_snapshot_publishes_all_authored_scoring_fields():
    source = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    snapshot = source.split("static AsciiidApplicationResultV1 BuildAsciiidAuthoringSnapshot()", 1)[1]
    assert snapshot.index("Fl4260EnsureProfileEditLoaded(g_asciiid_active_profile_key);") < snapshot.index("const bool active_profile_loaded")
    scoring = source.split('cJSON* scoring = cJSON_AddObjectToObject(material, "scoring");', 1)[1]
    scoring = scoring.split("if (d.feature_parameters_loaded)", 1)[0]
    for name in ("detail_contrast", "tone_contrast", "density_bias"):
        assert f'"{name}"' in scoring
        assert f"d.{name}" in scoring
    assert '"role_weights"' in scoring
    assert "d.scoring_role_weights[slot]" in scoring
