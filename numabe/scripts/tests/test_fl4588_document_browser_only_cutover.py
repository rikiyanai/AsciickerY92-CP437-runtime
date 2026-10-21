# =============================================================
# test_fl4588_document_browser_only_cutover.py
# =============================================================
# FL-4588: Workspace Document persistence is browser upload/download only.
# Hidden native filesystem Save ownership must stay deleted.
#
# Preserved: DownloadAsciiidBrowserDocument, runtime-intent framed Save As
# download, Open upload, Merge upload, New, and active document state.
# =============================================================

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVICE_H = ROOT / "apps/asciicker-presenter/asciiid_application_service.h"
SERVICE_CPP = ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp"
EDITOR = ROOT / "apps/asciicker-presenter/asciiid.cpp"
HTTP = ROOT / "apps/asciicker-presenter/asciiid_authoring_http.cpp"
CONTROL = ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
WORKSPACE_ACTIONS = ROOT / "web/asciiid-authoring-shell/src/WorkspaceActions.tsx"
TRANSPORT = ROOT / "web/asciiid-authoring-shell/src/transport.ts"


def test_native_filesystem_save_owners_are_deleted():
    """Runtime Save committers and DispatchAction route must be absent."""
    service_h = SERVICE_H.read_text(encoding="utf-8")
    service_cpp = SERVICE_CPP.read_text(encoding="utf-8")
    editor = EDITOR.read_text(encoding="utf-8")

    assert "AsciiidDocumentSaveCommitV1" not in service_h
    assert "document_save_committer_" not in service_h
    assert "document_save_committer_" not in service_cpp
    assert "CommitSaveDocument(" not in service_h
    assert "AsciiidApplicationService::CommitSaveDocument(" not in service_cpp
    assert "CommitSaveDocument(\n" not in service_cpp
    assert "return CommitSaveDocument" not in service_cpp
    assert 'leaf_id->valuestring, "workspace.file.save"' not in service_cpp
    assert "return CommitSaveDocument(" not in service_cpp
    assert "static AsciiidDocumentSaveCommitV1 CommitAsciiidSaveDocument" not in editor
    assert "CommitAsciiidSaveDocument," not in editor
    # Comments may mention the deleted name; runtime tokens must not remain.
    assert "document_save_committer_(" not in service_cpp
    assert "document_save_committer)" not in service_cpp


def test_browser_document_paths_are_preserved():
    """Open/Merge upload, Save As download, and New use runtime intent."""
    editor = EDITOR.read_text(encoding="utf-8")
    http = HTTP.read_text(encoding="utf-8")
    control = CONTROL.read_text(encoding="utf-8")
    transport = TRANSPORT.read_text(encoding="utf-8")
    service_h = SERVICE_H.read_text(encoding="utf-8")
    service_cpp = SERVICE_CPP.read_text(encoding="utf-8")

    assert "DownloadAsciiidBrowserDocument" in editor
    assert "POST /api/v1/runtime-intent HTTP/1.1" in http
    assert "GET /api/v1/document/download HTTP/1.1" not in http
    assert "POST /api/v1/document/open HTTP/1.1" not in http
    assert "POST /api/v1/document/merge HTTP/1.1" not in http
    assert "ASCIIRF1" in http
    assert "ASCIIRI1" in http
    assert "CommitAsciiidNewDocument" in editor
    assert "CommitNewDocument(" in service_h
    assert "document_new_committer_" in service_cpp
    assert "document_downloader_" in service_h or "document_downloader" in service_h

    # HTML: Save As browser download preserved; in-place Save button deleted.
    # FL-4588 persistent workspace actions: Save As browser download moved
    # from the ControlWidget route row to the one persistent WorkspaceActions
    # toolbar. ControlWidget no longer owns the Save leaf; the toolbar presents
    # the runtime-owned canonical document result as a browser download.
    # The component derives the Save leaf identity solely from the registry's
    # workspace_action_graph (no private hard-coded leaf-id list).
    assert "registry.workspace_action_graph" in WORKSPACE_ACTIONS.read_text(encoding="utf-8")
    assert "submitRuntimeDocumentDownload" in WORKSPACE_ACTIONS.read_text(encoding="utf-8")
    assert "SAVE .A3D" in WORKSPACE_ACTIONS.read_text(encoding="utf-8")
    assert "saveCurrentDocument" not in control
    assert "SAVE .A3D" not in control
    assert "commitDocumentSave" not in control
    assert "documentSaveArmed" not in control
    assert 'application_service_handler === "CommitSaveDocument"' not in control
    assert "CommitNewDocument" in control
    # Open moved to WorkspaceActions; ControlWidget keeps only Merge. Both send
    # framed file intents without a browser-owned document action sequence.
    assert "uploadDocument(" not in control
    assert "runDocumentOpenSequence" not in control
    assert "submitRuntimeFileIntent" in WORKSPACE_ACTIONS.read_text(encoding="utf-8")
    assert "runDocumentOpenSequence" not in WORKSPACE_ACTIONS.read_text(encoding="utf-8")
    assert "submitRuntimeFileIntent(" in control

    # Transport serializes raw physical facts generically; it has no dedicated
    # New or Save handler gate.
    assert "submitRuntimePhysicalIntent" in transport
    assert 'leaf.application_service_handler !== "CommitNewDocument"' not in transport
    assert 'leaf.application_service_handler !== "CommitSaveDocument"' not in transport
    assert "submitRuntimeDocumentDownload" in transport
    assert "document/download" not in transport


def test_html_shell_has_no_inplace_save_caller():
    control = CONTROL.read_text(encoding="utf-8")
    transport = TRANSPORT.read_text(encoding="utf-8")
    # Runtime caller tokens — comments may name the deleted handler.
    assert 'application_service_handler === "CommitSaveDocument"' not in control
    assert "onClick={commitDocumentSave}" not in control
    assert '!== "CommitSaveDocument"' not in transport
    assert '=== "CommitSaveDocument"' not in transport
