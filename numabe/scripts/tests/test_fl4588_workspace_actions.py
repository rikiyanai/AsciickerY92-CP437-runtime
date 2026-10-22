# =============================================================
# test_fl4588_workspace_actions.py
# =============================================================
# FL4588-PERSISTENT-WORKSPACE-ACTIONS-20260718-51 / FL-4588
#
# Structural guard for the one persistent workspace-action toolbar
# (Open / Save / Undo / Redo). These checks read the shipped source and the
# registry contract and confirm the OWNERSHIP shape is correct. They do NOT
# run the editor and do NOT claim the toolbar visibly works — a headed
# physical-input receipt is a separate, still-open step (PROOF_OPEN).
#
# The rules being frozen:
#   1) The registry owns one explicit ordered workspace_action_graph with the
#      exact existing leaf identities workspace.document.open.open,
#      workspace.document.save_as.open, workspace.history.undo,
#      workspace.history.redo. No private hard-coded tool list.
#   2) WorkspaceActions.tsx is the single persistent presentation owner: it
#      resolves each leaf from the graph, renders one actionable DOM element
#      per leaf with data-leaf-id + data-control-uid, and fails closed if the
#      graph is absent or inconsistent.
#   3) ControlWidget.tsx is no longer a workspace-action owner: its route-row
#      Open / Save / Undo / Redo presentation branches are deleted.
#   4) App.tsx mounts the toolbar beside the active-tool controls and excludes
#      the four workspace-action leaves from route rendering, so each leaf
#      occurs on exactly one actionable element in the rendered shell.
#   5) The registry generator validates the graph (schema, ownership, ordered
#      actions, live leaf references, PROOF_OPEN).
# =============================================================
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "web/asciiid-authoring-shell/src"
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
SCHEMA = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.schema.json"
GENERATOR = ROOT / "scripts/fl4588_generate_html_architecture.py"


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_registry_owns_one_ordered_workspace_action_graph():
    registry = _registry()
    graph = registry.get("workspace_action_graph")
    assert isinstance(graph, dict), "workspace_action_graph missing"
    assert graph["schema"] == "asciiid.workspace_action_graph.v1"
    ownership = graph["ownership"]
    assert ownership["contract_owner"] == (
        "assets/asciiid/ui/asciiid.ui_registry.v1.json"
    )
    assert ownership["layout_owners"] == [
        "web/asciiid-authoring-shell/src/App.tsx",
        "web/asciiid-authoring-shell/src/WorkspaceActions.tsx",
    ]
    note = ownership["note"]
    assert "hard-delet" in note
    assert "route-row" in note
    actions = graph["actions"]
    expected = [
        ("open_document", "workspace.document.open.open"),
        ("save_document", "workspace.document.save_as.open"),
        ("history_undo", "workspace.history.undo"),
        ("history_redo", "workspace.history.redo"),
    ]
    seen = [(a["kind"], a["leaf_id"]) for a in actions]
    assert seen == expected, f"workspace_action_graph actions mismatch: {seen}"
    # Every action proof_status stays PROOF_OPEN — no closure claim.
    assert all(a["proof_status"] == "PROOF_OPEN" for a in actions)


def test_workspace_action_graph_leaves_are_live_registry_leaves():
    registry = _registry()
    graph = registry["workspace_action_graph"]
    leaves_by_id = {leaf["leaf_id"]: leaf for leaf in registry["leaves"]}
    for action in graph["actions"]:
        leaf = leaves_by_id.get(action["leaf_id"])
        assert leaf is not None, (
            f"workspace_action_graph references missing leaf: {action['leaf_id']}"
        )
        assert leaf["status"] == "live", (
            f"workspace_action_graph references non-live leaf: {action['leaf_id']}"
        )


def test_schema_declares_workspace_action_graph_as_required():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert "workspace_action_graph" in schema["required"]
    assert "workspace_action_graph" in schema["properties"]
    assert "workspace_action_graph" in schema["$defs"]
    assert "workspace_action_node" in schema["$defs"]
    kinds = schema["$defs"]["workspace_action_node"]["properties"]["kind"]["enum"]
    assert kinds == ["open_document", "save_document", "history_undo", "history_redo"]


def test_generator_validates_workspace_action_graph():
    result = subprocess.run(
        ["python3", str(GENERATOR), "--check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "generator --check failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS" in result.stdout


def test_workspace_actions_consumes_the_single_workspace_action_graph_contract():
    src = _read("WorkspaceActions.tsx")
    # Reads the one shared contract, not a private hard-coded tool list.
    assert "registry.workspace_action_graph" in src
    assert "asciiid.workspace_action_graph.v1" in src
    # No private hard-coded list of leaf identities: the component derives
    # every action identity solely from registry.workspace_action_graph.
    assert "EXPECTED_ACTION_LEAF_IDS" not in src
    assert "workspace.document.open.open" not in src
    assert "workspace.document.save_as.open" not in src
    assert "workspace.history.undo" not in src
    assert "workspace.history.redo" not in src
    # Resolves each action's leaf by iterating graph.actions in graph order.
    assert "for (const action of actions)" in src
    assert "action.leaf_id" in src
    assert "action.kind" in src
    # Fails closed (renders nothing) when the graph is absent or inconsistent.
    assert "return null" in src


def test_workspace_actions_reuses_existing_browser_and_history_transports():
    src = _read("WorkspaceActions.tsx")
    # Open uses the real browser picker and sends the registered interaction
    # plus bytes through the single runtime-intent boundary.
    assert "submitRuntimeFileIntent" in src
    assert 'type="file"' in src
    assert 'accept=".a3d,application/vnd.asciiid.a3d"' in src
    # Save receives the runtime-owned canonical document response and presents
    # it through a real browser download.
    assert "submitRuntimeDocumentDownload" in src
    assert "URL.createObjectURL" in src
    assert "anchor.download" in src
    # Undo/Redo send raw registered pointer facts; no browser history action
    # constructor survives.
    assert "submitRuntimePhysicalIntent" in src
    assert "commitHistoryWrite" not in src
    assert "fetchSnapshot" in src


def test_workspace_actions_stamps_one_actionable_dom_element_per_leaf():
    src = _read("WorkspaceActions.tsx")
    # The visible buttons are the sole actionable DOM owners: each carries
    # the registry leaf identity directly.
    assert 'data-leaf-id={openLeaf.leaf_id}' in src
    assert 'data-control-uid={openLeaf.control_uid}' in src
    assert 'data-leaf-id={saveLeaf.leaf_id}' in src
    assert 'data-control-uid={saveLeaf.control_uid}' in src
    assert 'data-leaf-id={undoLeaf.leaf_id}' in src
    assert 'data-control-uid={undoLeaf.control_uid}' in src
    assert 'data-leaf-id={redoLeaf.leaf_id}' in src
    assert 'data-control-uid={redoLeaf.control_uid}' in src
    # One Open button, one Save button, one Undo button, one Redo button.
    assert src.count("workspace-action-button") == 4
    # The hidden file input carries NO leaf identity — the visible Open
    # button is the single actionable owner of workspace.document.open.open.
    assert 'data-leaf-id={openLeaf.leaf_id}' not in src.replace(
        'data-leaf-id={openLeaf.leaf_id}\n          data-control-uid={openLeaf.control_uid}\n          disabled',
        ""
    ) or src.count('data-leaf-id={openLeaf.leaf_id}') == 1
    # The hidden input block must not stamp data-control-uid onto the input.
    hidden_input_block = src[src.index('ref={openFileInput}'):]
    hidden_input_block = hidden_input_block[:hidden_input_block.index("/>")]
    assert "data-leaf-id" not in hidden_input_block
    assert "data-control-uid" not in hidden_input_block


def test_workspace_actions_disabled_state_mirrors_authoritative_snapshot():
    src = _read("WorkspaceActions.tsx")
    # Undo/Redo arm only from historyWriteArmed (can_undo / can_redo).
    assert "historyWriteArmed" in src
    # Dirty state is PRESENTATION only: it must not disable a clean-document
    # download. Save arms whenever the live session and snapshot permit a
    # download; the dirty flag is reflected in the button's presentation
    # (className / title), never in its enablement.
    assert "activeDocument.dirty" in src
    assert "saveArmed = !!saveLeaf && !!saveInteraction && sessionReady" in src
    assert "saveDirty" in src
    # Open/Save require a live connected session.
    assert "sessionReady" in src


def test_app_mounts_the_persistent_workspace_actions_toolbar():
    app = _read("App.tsx")
    assert 'import { WorkspaceActions } from "./WorkspaceActions";' in app
    assert "<WorkspaceActions registry={registry} />" in app
    # The four workspace-action leaves are excluded from route rendering.
    assert "workspaceActionLeaves" in app
    assert "workspace_action_graph: z.any()" in app
def test_controlwidget_is_no_longer_a_workspace_action_owner():
    control = _read("ControlWidget.tsx")
    # The route-row Open / Save / Undo / Redo presentation branches are gone.
    # The old owner identifiers may survive inside historical comments
    # documenting the delete-first rationale; the branches themselves
    # (const declarations + else-if render branches) must not.
    assert "const opensDocument" not in control
    assert "const savesDocument" not in control
    assert "const saveCurrentDocument" not in control
    assert "const uploadSelectedDocument" not in control
    assert "} else if (opensDocument)" not in control
    assert "} else if (savesDocument)" not in control
    assert "saveCurrentDocument" not in control.replace(
        "savesDocument / saveCurrentDocument", ""
    )
    # The deleted route-row DOM presentation labels do not survive as rendered
    # buttons. Historical comments may still name them; the render branches
    # are already covered by the else-if assertions above.
    assert ">OPEN .A3D</button>" not in control
    assert ">SAVE .A3D</button>" not in control
    # The undo/redo route-row branch is gone; the toolbar owns it now.
    assert "historyWriteArmed" not in control
    assert "commitHistoryWrite" not in control
