# =============================================================
# test_fl4588_active_tool_options.py
# =============================================================
# Structural guard for FL-4588 "one persistent active-tool OPTIONS panel" work.
#
# What "structural guard" means here: these checks read the shipped source and
# the registry contract and confirm the OWNERSHIP shape is correct. They do NOT
# run the editor and do NOT claim the panel visibly works — a headed physical
# receipt is a separate, still-open step. What they lock down is the thing that
# must not regress: that each tool's option controls (brush/probe leaves) have
# exactly ONE HTML owner (the ActiveToolOptions panel), that the owner reads the
# single shared active_tool_graph contract instead of a private option list, and
# that App.tsx removes the moved leaves from the generic route rows.
#
# The rules being frozen:
#   1) ActiveToolOptions.tsx resolves the active tool solely from the snapshot's
#      editor_session.edit_mode and renders the tool's graph-declared option
#      leaves through the shared ControlWidget.
#   2) ActiveToolOptions.tsx keeps NO private hard-coded option-leaf array — it
#      reads tool.option_leaf_ids from the registry graph.
#   3) App.tsx derives the moved option leaves from the graph's option_leaf_ids
#      and filters them out of the generic route rows (delete-first), and mounts
#      the panel.
#   4) Every declared option leaf is live and has a single DOM owner.
# =============================================================
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "web/asciiid-authoring-shell/src"
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_panel_resolves_active_tool_from_edit_mode_and_reads_the_graph():
    panel = _read("ActiveToolOptions.tsx")
    # Reads the one shared contract, not a private hard-coded option list.
    assert "registry.active_tool_graph" in panel
    assert "tool.option_leaf_ids" in panel
    # Resolves the active tool solely from the authoritative snapshot edit_mode.
    assert "editor_session?.edit_mode" in panel
    assert "entry.edit_mode === activeMode" in panel
    # Draws each option through the shared ControlWidget — same behavior as the
    # route rows it replaces.
    assert 'import { ControlWidget } from "./ControlWidget";' in panel
    assert "<ControlWidget" in panel


def test_panel_keeps_no_private_hardcoded_option_leaf_list():
    panel = _read("ActiveToolOptions.tsx")
    # The concrete leaf identities must live in the registry graph, never as a
    # literal array in the component. If any appears here, the panel has grown a
    # second owner of the option contract.
    for leaf_id in (
        "legacy.edit.sculpt.brush.radius",
        "legacy.edit.sculpt.brush.alpha",
        "scene.terrain.material.brush.radius",
        "scene.terrain.material.brush.height_limit",
    ):
        assert leaf_id not in panel, (
            f"{leaf_id} hard-coded in ActiveToolOptions.tsx — must come from the graph"
        )


def test_app_derives_moved_option_leaves_from_the_graph_and_mounts_panel():
    app = _read("App.tsx")
    assert 'import { ActiveToolOptions } from "./ActiveToolOptions";' in app
    assert "<ActiveToolOptions registry={registry} />" in app
    # Derived from the graph's option_leaf_ids — not a second hard-coded list.
    assert "tool.option_leaf_ids" in app
    assert "optionLeaves.has(leaf.leaf_id)" in app


def test_moved_option_leaves_are_not_owned_by_a_route_row():
    """Delete-first: all moved leaves are removed from the generic route rows
    the same way selection/workspace leaves are, so each has one DOM owner."""
    registry = _registry()
    option_leaves = set()
    declared_option_count = 0
    for tool in registry["active_tool_graph"]["tools"]:
        tool_options = tool.get("option_leaf_ids") or []
        declared_option_count += len(tool_options)
        for leaf_id in tool_options:
            option_leaves.add(leaf_id)
    # One leaf cannot be privately re-declared by two tools: the graph owns one
    # option surface per rendered ControlWidget row.
    assert option_leaves
    assert len(option_leaves) == declared_option_count
    # App.tsx removes them via the derived optionLeaves set; assert the source
    # actually references the derived set inside the live-leaf filter (guards
    # against the filter line being deleted while the derivation stays).
    app = _read("App.tsx")
    filter_block = app.split("const liveLeaves")[1].split("}, [registry]);")[0]
    assert "optionLeaves.has(leaf.leaf_id)" in filter_block
    # And every moved leaf is a live registry leaf (so ControlWidget can render
    # it in the panel).
    leaves = {leaf["leaf_id"]: leaf for leaf in registry["leaves"]}
    for leaf_id in option_leaves:
        assert leaves[leaf_id]["status"] == "live"
