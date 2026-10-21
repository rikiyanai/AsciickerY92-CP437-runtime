"""Focused source contracts for the single HTML glyph catalog (FL-4588)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.fl4131_gate_status import (
    STATUS_IMPLEMENTED_UNPROVEN,
    check_asciiid_extended_glyph_browser_visible,
)


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
SCHEMA = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.schema.json"
APP = ROOT / "web/asciiid-authoring-shell/src/App.tsx"
PANEL = ROOT / "web/asciiid-authoring-shell/src/GlyphRepertoirePanel.tsx"
CONTROL = ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
TRANSPORT = ROOT / "web/asciiid-authoring-shell/src/transport.ts"
STORE = ROOT / "web/asciiid-authoring-shell/src/store.ts"
GENERATOR = ROOT / "scripts/fl4588_generate_html_architecture.py"


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_registry_owns_one_browser_selected_glyph() -> None:
    registry = _registry()
    interactions = {item["leaf_id"]: item for item in registry["interactions"]}
    selected = interactions["glyph.catalog.select"]
    assert registry["interaction_count"] == len(registry["interactions"]) == 43
    assert selected["route"] == "/glyphs/catalog"
    assert selected["action_scope"] == "browser_draft"
    assert selected["action_schema"] == "asciiid.browser.select_glyph_catalog_entry.v1"
    assert selected["application_service_handler"] == (
        "BrowserShell::SelectGlyphCatalogEntry"
    )
    assert selected["payload_schema"] == {
        "type": "object",
        "additionalProperties": False,
        "required": ["glyph_id", "unicode_scalar"],
        "properties": {
            "glyph_id": {
                "type": "integer",
                "minimum": 0,
                "maximum": 4294967293,
            },
            "unicode_scalar": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1114111,
            },
        },
    }
    assert selected["write_set_template"] == [
        "/browser/glyphs/selected_glyph_id"
    ]
    assert selected["predictor_model_id"] == (
        "fl4588.empty_product_delta_browser_draft.v1"
    )
    assert selected["proof_status"] == "PROOF_OPEN"
    assert all(
        item["route"] != "/glyphs/search" for item in registry["interactions"]
    )
    assert interactions["glyph.filter.repertoire"]["route"] == "/glyphs/catalog"


def test_visible_catalog_cells_are_real_accessible_selection_buttons() -> None:
    panel = PANEL.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    assert 'route === "/glyphs/catalog"' in app
    assert "/glyphs/search" not in app
    assert "filterLeaf={glyphCatalogFilter}" in app
    assert "selectionLeaf={glyphCatalogSelection}" in app
    assert 'type="button"' in panel
    assert "aria-pressed" in panel
    assert "data-leaf-id={selectionLeaf.leaf_id}" in panel
    assert "data-control-uid={selectionLeaf.control_uid}" in panel
    assert "GlyphId {entry.glyph_id}" in panel
    assert "unicodeLabel(entry.unicode_scalar)" in panel
    assert "setSelectedGlyphId(entry.glyph_id)" in panel
    assert "Catalog failed to load" in panel
    assert "useState" in panel  # catalog loading only
    assert "useState<number" not in panel


def test_add_remove_consume_selection_without_numeric_entry_owner() -> None:
    control = CONTROL.read_text(encoding="utf-8")
    transport = TRANSPORT.read_text(encoding="utf-8")
    branch_start = control.index("const commitGlyphCatalogPoolAction")
    branch_end = control.index("// FL-4588 browser drafts", branch_start)
    branch = control[branch_start:branch_end]
    assert "selectedGlyphId" in branch
    assert "activeCandidateLaneId" in branch
    assert "active_profile_loaded !== true" in branch
    assert "select one visible catalog glyph first" in branch
    assert "no active material profile is loaded" in branch
    assert "no active candidate lane is selected" in branch
    assert "outcome.reason" in branch
    assert 'type="button"' in branch
    assert 'type="number"' not in branch
    assert "commitCandidateLane(" in branch
    assert "commitEnvViewGlyph(" not in branch
    assert 'lane_id: "ramp.0"' not in control
    assert "glyph_id: typeof raw" not in control

    candidate_start = transport.index("const CANDIDATE_POOL5_HANDLERS")
    candidate_end = transport.index("// ---- live value bindings", candidate_start)
    candidate_transport = transport[candidate_start:candidate_end]
    assert "CommitGlyphCatalogAddToActivePool" in candidate_transport
    assert "CommitGlyphCatalogRemoveFromActivePool" in candidate_transport
    assert 'leaf.leaf_id === "glyph.catalog.add_to_active_pool"' in candidate_transport
    assert 'leaf.leaf_id === "glyph.catalog.remove_from_active_pool"' in candidate_transport

    env_start = transport.index("const ENV11_HANDLERS")
    env_end = transport.index("export async function commitEnvViewGlyph", env_start)
    env_transport = transport[env_start:env_end]
    assert "CommitGlyphCatalogAddToActivePool" not in env_transport
    assert "CommitGlyphCatalogRemoveFromActivePool" not in env_transport

    interactions = {
        item["leaf_id"]: item for item in _registry()["interactions"]
    }
    for leaf_id in (
        "glyph.catalog.add_to_active_pool",
        "glyph.catalog.remove_from_active_pool",
    ):
        item = interactions[leaf_id]
        assert "browser.glyphs.selected_glyph_id" in item["read_model_fields"]
        assert item["proof_status"] == "PROOF_OPEN"


def test_store_has_exactly_one_selected_glyph_state_owner() -> None:
    store = STORE.read_text(encoding="utf-8")
    shell_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (APP, PANEL, CONTROL, STORE)
    )
    assert store.count("selectedGlyphId: number | null") == 1
    assert store.count("selectedGlyphId: null") == 1
    assert "setSelectedGlyphId: (glyphId: number) => void" in store
    assert "useState<number" not in shell_sources


def test_generator_schema_and_fl4131_gate_report_implemented_unproven() -> None:
    generator = GENERATOR.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert '"asciiid.browser.select_glyph_catalog_entry.v1"' in generator
    assert schema["properties"]["interactions"]["minItems"] == 43
    assert schema["properties"]["interactions"]["maxItems"] == 43
    gate = check_asciiid_extended_glyph_browser_visible()
    assert gate.status == STATUS_IMPLEMENTED_UNPROVEN
    assert "no headed closed-graph receipt yet" in gate.evidence
