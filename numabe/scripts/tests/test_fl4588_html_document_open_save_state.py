# =============================================================
# test_fl4588_html_document_open_save_state.py
# =============================================================
# FL4588-HTML-DOCUMENT-OPEN-SAVE-STATE-20260718-49 / FL-4588
#
# Focused React/transport contracts for the visible Open/Save/
# active-document presentation lane. Source asserts plus a Node
# runner that exercises documentOpenSave.ts pure presentation helpers. Runtime
# owns transaction sequencing and the browser no longer reconstructs it.
# =============================================================

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
APP = ROOT / "web/asciiid-authoring-shell/src/App.tsx"
WORKSPACE_ACTIONS = ROOT / "web/asciiid-authoring-shell/src/WorkspaceActions.tsx"
STORE = ROOT / "web/asciiid-authoring-shell/src/store.ts"
TRANSPORT = ROOT / "web/asciiid-authoring-shell/src/transport.ts"
DOC = ROOT / "web/asciiid-authoring-shell/src/documentOpenSave.ts"
RUNNER = ROOT / "scripts/tests/fl4588_html_document_open_save_state_runner.mjs"


def test_document_open_save_module_is_presentation_only():
    source = DOC.read_text(encoding="utf-8")
    assert "runDocumentOpenSequence" not in source
    assert "runDocumentSaveSequence" not in source
    assert "submitRuntime" not in source
    assert "picker_cancelled" in source
    assert "formatActiveDocumentStatus" in source
    assert "documentLeafUserHelp" in source
    assert "Pure Open / Save presentation helpers" in source


def test_workspace_actions_wires_open_save_through_runtime_owner():
    # FL-4588 persistent workspace actions: the route-row Open/Save owners in
    # ControlWidget.tsx were hard-deleted. The persistent WorkspaceActions
    # toolbar now owns the single DOM presentation of Open/Save beside the
    # active-tool controls and sends raw facts to RuntimeIntentOwner.
    src = WORKSPACE_ACTIONS.read_text(encoding="utf-8")
    assert "submitRuntimeFileIntent" in src
    assert "submitRuntimeDocumentDownload" in src
    assert "runDocumentOpenSequence" not in src
    assert "runDocumentSaveSequence" not in src
    assert "setActiveDocument" in src
    assert "formatActiveDocumentStatus" in src
    # Real browser file picker for Open; real browser download for Save.
    assert 'type="file"' in src
    assert "OPEN .A3D" in src
    assert "SAVE .A3D" in src
    assert "Open cancelled — no file selected." in src


def test_control_widget_no_longer_owns_route_row_open_save():
    # Delete-first: the route-row Open/Save DOM presentation owners are gone
    # from ControlWidget so each leaf occurs on exactly one actionable element.
    control = CONTROL.read_text(encoding="utf-8")
    assert "runDocumentOpenSequence" not in control
    assert "runDocumentSaveSequence" not in control
    # Rendered button labels are gone (historical comments may still name them).
    assert ">OPEN .A3D</button>" not in control
    assert ">SAVE .A3D</button>" not in control
    # Branch declarations are gone (historical comments may still name the
    # old owners; the const declarations and else-if branches must not).
    assert "const opensDocument" not in control
    assert "const savesDocument" not in control
    assert "} else if (opensDocument)" not in control
    assert "} else if (savesDocument)" not in control


def test_app_statusbar_reports_active_document_and_overrides_dialog_help():
    app = APP.read_text(encoding="utf-8")
    assert "formatActiveDocumentStatus(activeDocument)" in app
    assert "documentLeafUserHelp" in app
    assert "documentLeafUserHelp(focused.leaf_id)?.long_help" in app


def test_store_keeps_active_document_presentation():
    store = STORE.read_text(encoding="utf-8")
    assert "activeDocument" in store
    assert "setActiveDocument" in store
    assert "presentationFromSnapshot" in store


def test_transport_frames_binary_bytes_without_owning_document_semantics():
    transport = TRANSPORT.read_text(encoding="utf-8")
    assert "submitRuntimeFileIntent" in transport
    assert "submitRuntimeDocumentDownload" in transport
    assert "ASCIIRF1" in transport
    assert "ASCIIRI1" in transport
    assert "/api/v1/document/open" not in transport
    assert "/api/v1/document/download" not in transport
    assert "X-Asciiid-Session" in transport
    assert "X-Asciiid-Document-Name" not in transport
    assert "application/vnd.asciiid.runtime-file-intent" in transport


def test_stale_native_dialog_presentation_language_deleted_from_shell_help():
    help_src = DOC.read_text(encoding="utf-8")
    open_help = help_src[help_src.index("workspace.document.open.open") :]
    open_help = open_help[: open_help.index("workspace.document.save_as.open")]
    assert "directory list" not in open_help.lower()
    assert "save=2" not in open_help.lower()
    assert "path draft" not in open_help.lower()


def test_node_document_open_save_presentation_helpers():
    result = subprocess.run(
        ["node", str(RUNNER)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "node runner failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "FL4588_HTML_DOCUMENT_OPEN_SAVE_STATE_NODE_OK" in result.stdout
