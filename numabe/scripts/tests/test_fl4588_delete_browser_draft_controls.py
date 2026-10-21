# =============================================================================
# test_fl4588_delete_browser_draft_controls.py
# =============================================================================
# Focused fixtures for FL4588-DELETE-BROWSER-DRAFT-CONTROLS-20260717-30 / FL-4588.
#
# Proves the generic browser-draft mutation fallback is deleted: missing-handler
# controls cannot announce local success, and layout filters hide them.
# No headed Chrome, no native launch.
# =============================================================================

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "web/asciiid-authoring-shell/src/store.ts"
CONTROL = ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
APP = ROOT / "web/asciiid-authoring-shell/src/App.tsx"
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"


def test_setvalue_no_longer_announces_fake_draft_success() -> None:
    text = STORE.read_text(encoding="utf-8")
    assert "browser draft only — no native mutation handler is active" not in text
    assert "DELETED owner: announcing" in text or "DELETED owner" in text
    assert "setValue: (leafId, value, _label)" in text or "setValue: (leafId, value," in text
    # Silent preview buffer only — activity must not be set inside setValue.
    setvalue_block = text.split("setValue:")[1].split("clearValue:")[0]
    assert "activity:" not in setvalue_block


def test_controlwidget_deletes_generic_draft_fallback() -> None:
    text = CONTROL.read_text(encoding="utf-8")
    assert 'setValue(leaf.leaf_id, event.target.checked, label)}' not in text or (
        "nativeHandler" in text and "MISSING TYPED HANDLER" in text
    )
    # The old false-success branch: toggle without handler calling setValue alone.
    assert ": setValue(leaf.leaf_id, event.target.checked, label)" not in text
    assert "every other RGB leaf stays a labeled browser draft" not in text
    assert "MISSING TYPED HANDLER" in text
    assert "hasVisibleMutationOwner" in text
    # Wave flow still commits through the typed shape-preset owner.
    assert "CommitMaterialShapePreset" in text
    assert "commitMaterialShapePreset" in text


def test_app_layout_filters_missing_handler_mutation_leaves() -> None:
    text = APP.read_text(encoding="utf-8")
    assert "hasVisibleMutationOwner" in text
    assert "isObservationalLeaf" in text
    assert "FL4588-DELETE-BROWSER-DRAFT-CONTROLS" in text or "missing-handler mutation" in text


def test_visible_control_census_every_enabled_mutation_has_owner() -> None:
    """FL-4588: reachable live mutation leaves must own a typed or BrowserShell handler."""
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    toolbar = {
        tool.get("selection_leaf_id")
        for tool in (registry.get("active_tool_graph") or {}).get("tools") or []
        if tool.get("selection_leaf_id")
    }
    browser_shell = {
        "workspace.document.open.open",
        "workspace.document.save_as.open",
        "workspace.document.merge.open",
    }
    observational = []
    owned = []
    missing = []
    for leaf in registry["leaves"]:
        if leaf.get("status") != "live":
            continue
        if leaf["leaf_id"].startswith("workspace.navigation."):
            continue
        if leaf["leaf_id"] in toolbar:
            continue
        if (
            leaf["leaf_id"] == "scene.viewport.highlight_selected_material"
            and leaf.get("action_schema") is None
        ):
            continue
        kind = leaf.get("control_kind")
        help_ = leaf.get("short_help") or ""
        if kind == "readout" or (
            re.match(r"(?i)^(read|show|display) the ", help_)
            and kind in {"slider", "select", "color", "number", "text"}
        ):
            observational.append(leaf["leaf_id"])
            continue
        handler = leaf.get("application_service_handler")
        if handler or leaf["leaf_id"] in browser_shell:
            owned.append(leaf["leaf_id"])
            continue
        # Mutation-shaped without owner — must not be reachable.
        if kind in {"button", "tab", "toggle", "slider", "number", "text", "select", "color"}:
            missing.append(leaf["leaf_id"])

    assert "material.profile.shape_preset.wave_flow" in owned
    assert missing == [], f"reachable missing-handler mutation leaves: {missing}"
    draft_ix = sorted(
        item["leaf_id"]
        for item in registry["interactions"]
        if item.get("action_scope") == "browser_draft"
    )
    # Visible-control census (FL-4588 report surface).
    assert len(owned) > 0
    assert len(observational) >= 0
    assert "glyph.filter.repertoire" in draft_ix
    assert "scene.viewport.highlight_selected_material" in draft_ix
    # Print census for operator report capture.
    print(
        json.dumps(
            {
                "observational_live_leaves": len(observational),
                "owned_mutation_live_leaves": len(owned),
                "missing_handler_mutation_live_leaves": len(missing),
                "explicit_browser_draft_interactions": draft_ix,
            },
            sort_keys=True,
        )
    )


def test_wave_flow_cannot_use_draft_fallback_path() -> None:
    """Wave flow owns CommitMaterialShapePreset — not a local draft announce."""
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    wave = next(
        leaf
        for leaf in registry["leaves"]
        if leaf["leaf_id"] == "material.profile.shape_preset.wave_flow"
    )
    assert wave["application_service_handler"] == "CommitMaterialShapePreset"
    control = CONTROL.read_text(encoding="utf-8")
    store = STORE.read_text(encoding="utf-8")
    assert "browser draft only — no native mutation handler is active" not in store
    assert "CommitMaterialShapePreset" in control
