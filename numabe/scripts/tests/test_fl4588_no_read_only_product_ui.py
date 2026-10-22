from pathlib import Path
import importlib.util
import json

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _packager():
    path = ROOT / "scripts/package_asciicker_presenter_html_mac.py"
    spec = importlib.util.spec_from_file_location("fl4588_packager_gate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_html_product_never_exposes_read_only_editor_controls():
    app = (ROOT / "web/asciiid-authoring-shell/src/App.tsx").read_text()
    controls = (ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx").read_text()
    frame = (ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx").read_text()

    assert "ASCIIID EDITOR · READ ONLY" not in app
    assert "nothing was changed" not in app
    assert "native connection is read only" not in controls
    assert "nothing was changed" not in controls
    assert "READ ONLY · PAPER AND C++ OWN THE DECISION" not in frame
    assert "NOT CONNECTED" not in frame
    assert "!health.mutation_enabled" in app
    assert "!health?.product_ready" in app
    # Product readiness is a three-way join: healthy native service, complete
    # product capabilities, and exact loaded-shell/native registry identity.
    # Keep these assertions structural so formatting cannot weaken the gate.
    assert "report.ok === true &&" in app
    assert "report.product_ready === true &&" in app
    assert "registryIdentity.matched" in app
    assert "report.product_ready === true || Boolean(sessionId)" not in app
    assert '!leaf.leaf_id.startsWith("workspace.navigation.")' in app


def test_packager_refuses_non_mutating_backend():
    packager = (ROOT / "scripts/package_asciicker_presenter_html_mac.py").read_text()
    assert '"mutation_enabled",' in packager
    assert "REQUIRED_HEALTH_PRODUCT_READY_KEYS" in packager


def test_packaged_app_is_the_sole_headed_product_driver():
    """Delete the static-screenshot journey substitute and keep one launcher."""
    assert not (ROOT / "scripts/fl4588/headed_journey_proof.py").exists()
    packager = ROOT / "scripts/package_asciicker_presenter_html_mac.py"
    assert packager.is_file()
    source = packager.read_text(encoding="utf-8")
    assert "--launch-from-app-resources" in source
    assert "Contents/Resources" in source
    assert "Google Chrome --app=" in source


def test_document_open_has_one_visible_picker_and_no_duplicate_commit_button():
    """
    Headed failure: two visible Open gestures (picker + Commit uploaded document).

    Contract: tombstone target_workspace_document_open_commit; keep the sole
    visible workspace.document.open.open BrowserShell picker; typed open bytes
    remain on the interaction AsciiidApplicationService::OpenDocumentBytes.
    """
    registry = json.loads(
        (ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert not (ROOT / "scripts/fl4588/headed_journey_proof.py").exists()

    picker = next(
        item
        for item in registry["leaves"]
        if item["leaf_id"] == "workspace.document.open.open"
    )
    assert picker["control_uid"] == "fl4260_row_520__view_load"
    assert picker["status"] == "live"
    assert picker["migration_status"] == "read_only"
    assert picker["old_owner_deleted"] is True
    # Visible HTML OPEN .A3D picker leaf — browser-owned selection, no
    # duplicate product commit button beside it.
    assert picker["application_service_handler"] is None
    assert picker["action_schema"] is None

    leaf = next(
        item
        for item in registry["leaves"]
        if item["control_uid"] == "target_workspace_document_open_commit"
    )
    assert leaf["leaf_id"] == "workspace.document.open.commit"
    assert leaf["status"] == "tombstoned"
    assert leaf["migration_status"] == "tombstone"
    assert leaf["application_service_handler"] is None
    assert leaf["action_schema"] is None
    assert leaf["proof_status"] == "TOMBSTONE_DECISION"

    interaction = next(
        item
        for item in registry["interactions"]
        if item["leaf_id"] == "workspace.document.open.commit"
    )
    assert interaction["control_uid"] == "target_workspace_document_open_commit"
    assert interaction["application_service_handler"] == (
        "AsciiidApplicationService::OpenDocumentBytes"
    )
    assert interaction["action_scope"] == "product_transaction"
    assert interaction["commit_class"] == "staged"
    assert interaction["payload_schema"]["required"] == ["logical_name"]

    widget = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
   # FL-4588 persistent workspace actions: Open moved from the ControlWidget
    # route row to the one persistent WorkspaceActions toolbar. ControlWidget
    # no longer renders the OPEN .A3D button; the toolbar does.
    workspace_actions = (
        ROOT / "web/asciiid-authoring-shell/src/WorkspaceActions.tsx"
    ).read_text(encoding="utf-8")
    # The component derives the Open leaf identity solely from the registry's
    # workspace_action_graph (no private hard-coded leaf-id list).
    assert "registry.workspace_action_graph" in workspace_actions
    assert "OPEN .A3D" in workspace_actions
    assert "submitRuntimeFileIntent(" in workspace_actions
    assert "classifyA3dPickerSelection(file)" in workspace_actions
    assert 'leaf.leaf_id === "workspace.document.open.open"' not in widget
    # Rendered button label gone (historical Fragment-walk comment may still
    # name the old OPEN .A3D button; the rendered button must not survive).
    assert ">OPEN .A3D</button>" not in widget
    assert "uploadDocument(sessionId, file)" not in widget
    # Tombstoned commit leaf must not render a second visible commit button.
    assert "Commit uploaded document" not in widget

    # After filesystem Save dialog deletion, the document page exposes exactly
    # four live user-action buttons: Save As download, Open upload, Merge
    # upload, and New.
    live_document_actions = sorted(
        leaf["leaf_id"]
        for leaf in registry["leaves"]
        if leaf.get("route") == "/workspace/document"
        and leaf.get("status") == "live"
        and leaf.get("control_kind") == "button"
    )
    assert live_document_actions == [
        "workspace.document.merge.open",
        "workspace.document.new",
        "workspace.document.open.open",
        "workspace.document.save_as.open",
    ]
    save_leaf = next(
        item for item in registry["leaves"]
        if item["leaf_id"] == "workspace.file.save"
    )
    assert save_leaf["status"] == "tombstoned"
    assert save_leaf["application_service_handler"] is None
    assert save_leaf["action_schema"] is None
    assert save_leaf["commit_class"] is None


def test_packager_refuses_any_inert_product_control(tmp_path):
    packager = _packager()
    registry = tmp_path / "registry.json"
    registry.write_text(
        """{
          "leaves": [{
            "leaf_id": "scene.test.mutation",
            "migration_status": "migrate",
            "old_owner_deleted": false,
            "application_service_handler": null
          }],
          "interactions": [{
            "leaf_id": "scene.test.viewport_mutation",
            "action_scope": "product_transaction",
            "application_service_handler": null
          }, {
            "leaf_id": "scene.test.browser_draft",
            "action_scope": "browser_draft",
            "application_service_handler": null
          }]
        }"""
    )
    with pytest.raises(packager.PackagingError, match="cutover incomplete"):
        packager.assert_registry_editor_cutover_complete(registry)


def test_packager_refuses_unowned_live_button_without_handler(tmp_path):
    """Generic inert-button deletion: live button/tab without a handler refuses."""
    packager = _packager()
    registry = tmp_path / "registry.json"
    registry.write_text(
        """{
          "leaves": [{
            "leaf_id": "scene.test.inert_button",
            "status": "live",
            "control_kind": "button",
            "migration_status": "read_only",
            "old_owner_deleted": true,
            "application_service_handler": null
          }],
          "interactions": []
        }"""
    )
    with pytest.raises(packager.PackagingError, match="unowned_live_buttons"):
        packager.assert_registry_editor_cutover_complete(registry)


def test_packager_accepts_zero_debt_registry(tmp_path):
    """Empty debt arrays are valid completion for the package cutover gate."""
    packager = _packager()
    registry = tmp_path / "registry.json"
    registry.write_text(
        """{
          "leaves": [{
            "leaf_id": "scene.test.mutation",
            "status": "live",
            "control_kind": "slider",
            "migration_status": "migrate",
            "old_owner_deleted": true,
            "application_service_handler": "CommitTest"
          }],
          "interactions": [{
            "leaf_id": "scene.test.viewport_mutation",
            "action_scope": "product_transaction",
            "application_service_handler": "CommitTestViewport"
          }]
        }"""
    )
    packager.assert_registry_editor_cutover_complete(registry)


def test_packager_accepts_typed_delete_first_product_controls(tmp_path):
    packager = _packager()
    registry = tmp_path / "registry.json"
    registry.write_text(
        """{
          "leaves": [{
            "leaf_id": "scene.test.mutation",
            "migration_status": "migrate",
            "old_owner_deleted": true,
            "application_service_handler": "CommitTest"
          }],
          "interactions": [{
            "leaf_id": "scene.test.viewport_mutation",
            "action_scope": "product_transaction",
            "application_service_handler": "CommitTestViewport"
          }, {
            "leaf_id": "scene.test.browser_draft",
            "action_scope": "browser_draft",
            "application_service_handler": "BrowserShell::SetTestDraft"
          }]
        }"""
    )
    packager.assert_registry_editor_cutover_complete(registry)
