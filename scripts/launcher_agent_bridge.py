"""launcher_agent_bridge.py — Auto-register launcher actions for agent use.

FL-XXXX: Scans the canonical option tree and wires every non-interactive
action leaf into the launcher ActionRegistry so ``--action <name>`` works
for agents and MCP callers without manual menu navigation.

Usage (from launcher.py):
    from launcher_agent_bridge import register_all_actions
    register_all_actions(_action_registry, globals())
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.launcher_lib import option_tree as _option_tree
from scripts.launcher_actions import ActionRegistry


# ── Placeholder resolution ──────────────────────────────────────────

def _resolve_placeholder(token: str, args: argparse.Namespace, defaults: dict[str, Any]) -> str | None:
    """Resolve a single command-template placeholder using argparse args."""
    if token == "<map_path>":
        return getattr(args, "map_path", None) or defaults.get("DEFAULT_MAP")
    if token == "<run-id>":
        return getattr(args, "run_id", None) or (defaults.get("latest_run_id")() if defaults.get("latest_run_id") else None)
    if token == "<host-or-url>":
        return getattr(args, "server_url", None)
    if token == "<port>":
        return str(getattr(args, "port", None) or defaults.get("DEFAULT_LOCAL_WEB_PORT", "8080"))
    if token == "<players>":
        return str(getattr(args, "max_players", None) or defaults.get("DEFAULT_LOCAL_MAX_PLAYERS", "5"))
    if token == "<selected-map>":
        return getattr(args, "map_path", None) or defaults.get("DEFAULT_MAP")
    if token == "<mod>":
        return getattr(args, "mod_dir", None)
    if token == "<slug>":
        return getattr(args, "mod_slug", None)
    if token == "<asset>":
        return getattr(args, "local_path", None)
    if token == "<FL-NNN>":
        return getattr(args, "fl_id", None)
    if token == "<FL-NNN,...>":
        return getattr(args, "fl_id", None)
    if token == "<term>":
        return getattr(args, "term", None)
    if token == "<recipe>":
        return getattr(args, "recipe_name", None)
    if token == "<name>":
        return getattr(args, "recipe_name", None)
    if token == "<output>":
        return getattr(args, "output", None) or getattr(args, "a3d_output", None)
    if token == "<grid>":
        return getattr(args, "grid", "1")
    if token == "<material_id>":
        return getattr(args, "material_id", "1")
    if token == "<snapshot>":
        return getattr(args, "rollback_snapshot", None)
    if token == "<repo>":
        return defaults.get("REPO_ROOT") or str(REPO_ROOT)
    if token == "<repo_root>":
        return defaults.get("REPO_ROOT") or str(REPO_ROOT)
    if token == "<kind>":
        return getattr(args, "asset_kind", None)
    if token == "<path>":
        return getattr(args, "local_path", None)
    if token == "<mode>":
        return getattr(args, "bundle_mode", None)
    if token == "<a3d_output>":
        return getattr(args, "a3d_output", None) or getattr(args, "output", None) or "test_map.a3d"
    if token == "<min>":
        # Used by OSM commands for lat/lon bounds
        # We'll return a generic placeholder error; OSM actions use function wrappers instead
        return None
    if token == "<map.osm>":
        return getattr(args, "local_path", None)
    if token == "<scene.blend>":
        return getattr(args, "blend_file", None)
    if token == "<baked-map.a3d>":
        return getattr(args, "local_path", None)
    if token == "<name-or-id>":
        return getattr(args, "building", None)
    if token == "<PLAYWRIGHT_DEVICE>":
        return defaults.get("PLAYWRIGHT_DEVICE")
    if token == "<localhost|test-vps|live-vps|custom>":
        return getattr(args, "target_context", None)
    if token.startswith("<") and token.endswith(">"):
        attr = token[1:-1].replace("-", "_").replace("|", "_")
        val = getattr(args, attr, None)
        if val is not None:
            return str(val)
        return None
    return token


def _server_snapshot_selector_tokens(args: argparse.Namespace) -> tuple[str, str] | None:
    """Resolve one of --at/--tick/--entity into a concrete selector flag/value pair."""
    selectors = [
        ("--at", getattr(args, "at", None)),
        ("--tick", getattr(args, "tick", None)),
        ("--entity", getattr(args, "entity", None)),
    ]
    chosen = [(flag, value) for flag, value in selectors if value not in (None, "")]
    if len(chosen) != 1:
        return None
    flag, value = chosen[0]
    if flag == "--at":
        try:
            float(value)
        except (TypeError, ValueError):
            return None
    return flag, str(value)


def _resolve_command(command_template: list[str], args: argparse.Namespace, defaults: dict[str, Any]) -> list[str]:
    """Substitute all placeholders in a command template."""
    result: list[str] = []
    i = 0
    while i < len(command_template):
        token = command_template[i]
        if token in {"[--bundle-wizard-phase0-only]", "[--no-topology-bake]"}:
            # Conditional flags based on argparse booleans
            if token == "[--bundle-wizard-phase0-only]" and getattr(args, "bundle_wizard_phase0_only", False):
                result.append("--bundle-wizard-phase0-only")
            if token == "[--no-topology-bake]" and getattr(args, "no_topology_bake", False):
                result.append("--no-topology-bake")
            i += 1
            continue
        if token == "--at|--tick|--entity":
            selector = _server_snapshot_selector_tokens(args)
            if selector is None:
                raise ValueError("server-snapshot requires exactly one of --at, --tick, or --entity.")
            result.extend(selector)
            if i + 1 < len(command_template) and command_template[i + 1] == "<selector>":
                i += 2
                continue
            i += 1
            continue
        resolved = _resolve_placeholder(token, args, defaults)
        if resolved is None:
            raise ValueError(f"Missing required argument for placeholder: {token}")
        result.append(str(resolved))
        i += 1
    return result


# ── Wrapper factories ───────────────────────────────────────────────

def _build_run_command_wrapper(
    command_template: list[str],
    label: str,
    globals_dict: dict[str, Any],
) -> Callable[[argparse.Namespace], int]:
    """Return a wrapper that resolves placeholders and calls _run_command."""
    _run_command = globals_dict["_run_command"]
    _repo_root = globals_dict["REPO_ROOT"]

    def wrapper(args: argparse.Namespace) -> int:
        defaults = {
            "DEFAULT_MAP": str(globals_dict.get("DEFAULT_MAP", _repo_root / "assets" / "a3d" / "game_map_y8.a3d")),
            "DEFAULT_LOCAL_WEB_PORT": str(globals_dict.get("DEFAULT_LOCAL_WEB_PORT", "8080")),
            "DEFAULT_LOCAL_MAX_PLAYERS": str(globals_dict.get("DEFAULT_LOCAL_MAX_PLAYERS", "5")),
            "REPO_ROOT": str(_repo_root),
            "PLAYWRIGHT_DEVICE": str(globals_dict.get("PLAYWRIGHT_DEVICE", "iPhone 14")),
        }
        try:
            cmd = _resolve_command(command_template, args, defaults)
        except ValueError as exc:
            console = globals_dict.get("console")
            if console:
                console.print(f"  [red]✗[/red]  {exc}")
            else:
                print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return _run_command(cmd, label=label, cwd=_repo_root)
    return wrapper


# ── Explicit function adapters ──────────────────────────────────────

def _adapt__run_single_player(handler, item, g):
    return lambda args: handler()


def _adapt__join_server_url(handler, item, g):
    return lambda args: handler(
        getattr(args, "server_url", None),
        open_browser=not getattr(args, "no_browser", False),
    )


def _adapt__open_local_lan_server(handler, item, g):
    return lambda args: handler(
        open_browser=not getattr(args, "no_browser", False),
        pause_on_success=False,
    )


def _adapt__browse_minimap_maps(handler, item, g):
    return lambda args: handler(
        getattr(args, "map_path", None),
        pause_on_success=False,
    )


def _adapt__swap_map_for_single_player(handler, item, g):
    return lambda args: handler(getattr(args, "map_path", None))


def _adapt__swap_map_for_multiplayer(handler, item, g):
    def wrapper(args: argparse.Namespace) -> int:
        map_path = getattr(args, "map_path", None)
        action_name = item.get("action", "")
        if "candidate" in action_name:
            target = "candidate"
        elif "current" in action_name:
            target = "current"
        else:
            target = "candidate"
        return handler(map_path, target=target)
    return wrapper


def _adapt__open_selected_map_in_asciiid(handler, item, g):
    return lambda args: handler(getattr(args, "map_path", None))


def _adapt__host_local_play_with_friends(handler, item, g):
    return lambda args: handler(args, pause_on_success=False)


def _adapt__set_local_host_max_players(handler, item, g):
    return lambda args: handler(args, pause_on_success=False)


def _adapt__run_new_test_map(handler, item, g):
    return lambda args: handler(
        getattr(args, "a3d_output", None) or getattr(args, "output", None) or "test_map.a3d",
        getattr(args, "grid", "1"),
        getattr(args, "material_id", "1"),
    )


def _adapt__run_instance_list(handler, item, g):
    default_map = str(g.get("DEFAULT_MAP", REPO_ROOT / "assets" / "a3d" / "game_map_y8.a3d"))
    return lambda args: handler(getattr(args, "map_path", None) or default_map)


def _adapt__show_watchdog_context(handler, item, g):
    latest_run = g.get("_latest_run_id_shared")
    return lambda args: handler(
        getattr(args, "run_id", None) or (latest_run(g.get("RUNS_ROOT")) if latest_run else None),
        pause_on_success=False,
    )


def _adapt__show_mobile_status(handler, item, g):
    return lambda args: handler(None, pause_on_success=False)


def _adapt__show_trust_audit_result(handler, item, g):
    return lambda args: handler(pause_on_success=False)


def _adapt__recipe_help(handler, item, g):
    return lambda args: handler(pause_on_success=False)


def _adapt__show_watchdog_system_guide(handler, item, g):
    def wrapper(args: argparse.Namespace) -> int:
        handler()
        return 0
    return wrapper


def _adapt__print_migration_plan(handler, item, g):
    return lambda args: handler(pause_on_success=False)


def _adapt__proof_run_derive_intent(handler, item, g):
    def wrapper(args: argparse.Namespace) -> int:
        result = handler(as_json=getattr(args, "json", False))
        return result if isinstance(result, int) else 0
    return wrapper


def _adapt__proof_run_refresh(handler, item, g):
    return lambda args: handler()


def _adapt__proof_run_from_visible(handler, item, g):
    return lambda args: handler()


def _adapt__proof_run_intent_wizard(handler, item, g):
    return lambda args: handler()


def _adapt__proof_run_profiles(handler, item, g):
    return lambda args: handler()


def _adapt__paste_and_run_watchdog(handler, item, g):
    return lambda args: handler()


def _adapt__run_xp_combination_check(handler, item, g):
    def wrapper(args: argparse.Namespace) -> int:
        handler()
        return 0
    return wrapper


def _adapt__compare_xp_animation_slots(handler, item, g):
    return lambda args: handler(
        slot_a=getattr(args, "slot_a", None),
        slot_b=getattr(args, "slot_b", None),
        output=getattr(args, "output", None),
    )


def _adapt__switch_target_context(handler, item, g):
    return lambda args: handler(args, pause_on_success=False)


def _adapt__validate_recipe_payload(handler, item, g):
    return lambda args: handler(getattr(args, "recipe_name", None) or "")


def _adapt__recipe_dry_run_command(handler, item, g):
    _run_command = g["_run_command"]
    _repo_root = g["REPO_ROOT"]
    return lambda args: _run_command(
        handler(getattr(args, "recipe_name", None) or ""),
        label="recipe dry-run",
        cwd=_repo_root,
    )


def _adapt__recipe_export_command(handler, item, g):
    _run_command = g["_run_command"]
    _repo_root = g["REPO_ROOT"]
    return lambda args: _run_command(
        handler(getattr(args, "recipe_name", None) or ""),
        label="recipe export",
        cwd=_repo_root,
    )


def _adapt__recipe_repeat_command(handler, item, g):
    _run_command = g["_run_command"]
    _repo_root = g["REPO_ROOT"]
    _senv_load = g.get("_senv", {}).get("load") if isinstance(g.get("_senv"), dict) else g.get("_senv", object()).load
    _latest_run_id = g.get("_latest_run_id_shared")
    _runs_root = g.get("RUNS_ROOT")

    def wrapper(args: argparse.Namespace) -> int:
        env = _senv_load() if callable(_senv_load) else {}
        recipe_name = getattr(args, "recipe_name", None) or ""
        if not recipe_name:
            console = g.get("console")
            if console:
                console.print("  [red]✗[/red]  --recipe-name is required.")
            return 1
        mode = getattr(args, "mode", "watchdog-only")
        hold_open_ms = getattr(args, "controller_hold_open_ms", 120000)
        diff_corpus = (getattr(args, "diff_corpus", None) or "gameplay").strip().lower()
        cmd = handler(env, recipe_name, mode=mode, hold_open_ms=hold_open_ms, diff_corpus=diff_corpus)
        previous = _latest_run_id(_runs_root) if _latest_run_id and _runs_root else None
        rc = _run_command(cmd, label=f"replay recipe {recipe_name}", cwd=_repo_root)
        # TODO: _print_recipe_repeat_summary is not exported in globals; skip for now
        return rc
    return wrapper


def _adapt__summary_rerun_command(handler, item, g):
    _run_command = g["_run_command"]
    _repo_root = g["REPO_ROOT"]
    _read_run_summary = g.get("_read_run_summary")
    _latest_run_id = g.get("_latest_run_id_shared")
    _runs_root = g.get("RUNS_ROOT")

    _is_latest_action = item.get("action") == "watchdog-dashboard-rerun-latest"

    def wrapper(args: argparse.Namespace) -> int:
        run_id = getattr(args, "run_id", None)
        # For the "rerun-latest" action, auto-resolve the latest run without requiring --latest flag
        if not run_id and (_is_latest_action or getattr(args, "latest", False)):
            run_id = _latest_run_id(_runs_root) if _latest_run_id and _runs_root else None
        if not run_id:
            console = g.get("console")
            if console:
                console.print("  [red]✗[/red]  --run-id or --latest is required.")
            return 1
        summary = _read_run_summary(run_id) if _read_run_summary else {}
        diff_corpus = getattr(args, "diff_corpus", None)
        cmd = handler(summary, diff_corpus=diff_corpus)
        if cmd is None:
            console = g.get("console")
            if console:
                console.print("  [red]✗[/red]  selected summary does not contain enough fields for rerun.")
            return 1
        return _run_command(cmd, label=f"re-run {run_id}", cwd=_repo_root)
    return wrapper


def _adapt__action_osm_online(handler, item, g):
    return lambda args: handler(args)


def _adapt__action_osm_local(handler, item, g):
    def wrapper(args: argparse.Namespace) -> int:
        action = item.get("action", "")
        if "terrain-debug" in action or "stop-after-buildings" in str(item.get("command", [])):
            return handler(args, pipeline_mode="baked")
        if "local-baked" in action:
            return handler(args, pipeline_mode="baked")
        return handler(args, pipeline_mode="traditional")
    return wrapper


def _adapt__action_osm_resume(handler, item, g):
    return lambda args: handler(args)


def _adapt__run_osm_commands(handler, item, g):
    # For map-osm-export: build commands from args
    _build_osm_local_commands = g.get("_build_osm_local_commands")
    _run_command = g["_run_command"]
    _repo_root = g["REPO_ROOT"]

    def wrapper(args: argparse.Namespace) -> int:
        blend_file = getattr(args, "blend_file", None) or str(g.get("DEFAULT_BLEND", ""))
        meshes_dir = getattr(args, "meshes_dir", None) or str(g.get("DEFAULT_MESHES_DIR", ""))
        a3d_output = getattr(args, "a3d_output", None) or str(g.get("DEFAULT_A3D_OUTPUT", ""))
        local_path = getattr(args, "local_path", None)
        if not local_path:
            console = g.get("console")
            if console:
                console.print("  [red]✗[/red]  --local-path is required.")
            return 1
        resolved = Path(local_path).expanduser().resolve()
        if _build_osm_local_commands:
            try:
                commands = _build_osm_local_commands(str(resolved), blend_file, meshes_dir, a3d_output, pipeline_mode="baked")
            except Exception as exc:
                console = g.get("console")
                if console:
                    console.print(f"  [red]✗[/red]  {exc}")
                return 1
            return _run_command(commands[0] if commands else [], label="osm export", cwd=_repo_root)
        return 1
    return wrapper


def _adapt__run_sbu_verify_building(handler, item, g):
    return lambda args: handler(args)


def _adapt__show_osm_past_runs(handler, item, g):
    return lambda args: handler(pause_on_success=False)


def _adapt__show_bundle_system_guide(handler, item, g):
    def wrapper(args: argparse.Namespace) -> int:
        handler()
        return 0
    return wrapper


def _adapt__show_workbench_help(handler, item, g):
    def wrapper(args: argparse.Namespace) -> int:
        handler()
        return 0
    return wrapper


def _adapt__show_mcp_inventory(handler, item, g):
    def wrapper(args: argparse.Namespace) -> int:
        handler()
        return 0
    return wrapper


def _adapt__edit_blender_paths(handler, item, g):
    def wrapper(args: argparse.Namespace) -> int:
        if not g.get("_can_prompt", lambda: False)():
            console = g.get("console")
            if console:
                console.print("  [yellow]⚠[/yellow]  blender-api-key requires an interactive TTY.")
            return 3
        handler()
        return 0
    return wrapper


def _adapt__edit_multiplayer_settings(handler, item, g):
    def wrapper(args: argparse.Namespace) -> int:
        if not g.get("_can_prompt", lambda: False)():
            console = g.get("console")
            if console:
                console.print("  [yellow]⚠[/yellow]  multiplayer-configure requires an interactive TTY.")
            return 3
        handler()
        return 0
    return wrapper


def _adapt__list_all_scripts(handler, item, g):
    return lambda args: handler()


def _adapt__list_script_family(handler, item, g):
    return lambda args: handler(getattr(args, "term", None) or "")


def _adapt__menu_asciiid(handler, item, g):
    _run_asciiid_editor = g.get("_run_asciiid_editor")
    default_map = str(g.get("DEFAULT_MAP", REPO_ROOT / "assets" / "a3d" / "game_map_y8.a3d"))

    def wrapper(args: argparse.Namespace) -> int:
        map_path = getattr(args, "map_path", None) or default_map
        if _run_asciiid_editor:
            return _run_asciiid_editor("Map Editor", map_path=map_path)
        return handler()
    return wrapper


def _adapt__menu_mobile_playwright(handler, item, g):
    _senv = g.get("_senv")
    def wrapper(args: argparse.Namespace) -> int:
        load_fn = getattr(_senv, "load", None) if _senv else None
        env = load_fn() if callable(load_fn) else {}
        return handler(env)
    return wrapper


def _adapt__menu_xp_anchor_review(handler, item, g):
    return lambda args: handler()


def _adapt__menu_xp_asset_layer2_browser(handler, item, g):
    return lambda args: handler()


def _adapt__menu_xp_raw_layer_inspector(handler, item, g):
    return lambda args: handler()


def _adapt__wizard_run_vps_wizard(handler, item, g):
    console = g.get("console")
    can_prompt = g.get("_can_prompt", lambda: False)
    def wrapper(args: argparse.Namespace) -> int:
        if not can_prompt():
            console.print("  [yellow]⚠[/yellow]  multiplayer-wizard requires an interactive TTY.")
            return 3
        return handler(console, input_fn=None)
    return wrapper


def _adapt__webbrowser_open(handler, item, g):
    url = "https://rikiworld.com/xpedit"
    return lambda args: handler(url)


# Map handler name -> adapter factory
_ADAPTERS: dict[str, Callable[[Any, dict, dict[str, Any]], Callable[[argparse.Namespace], int]]] = {
    "_run_single_player": _adapt__run_single_player,
    "_join_server_url": _adapt__join_server_url,
    "_open_local_lan_server": _adapt__open_local_lan_server,
    "_browse_minimap_maps": _adapt__browse_minimap_maps,
    "_swap_map_for_single_player": _adapt__swap_map_for_single_player,
    "_swap_map_for_multiplayer": _adapt__swap_map_for_multiplayer,
    "_open_selected_map_in_asciiid": _adapt__open_selected_map_in_asciiid,
    "_host_local_play_with_friends": _adapt__host_local_play_with_friends,
    "_set_local_host_max_players": _adapt__set_local_host_max_players,
    "_run_new_test_map": _adapt__run_new_test_map,
    "_run_instance_list": _adapt__run_instance_list,
    "_show_watchdog_context": _adapt__show_watchdog_context,
    "_show_mobile_status": _adapt__show_mobile_status,
    "_show_trust_audit_result": _adapt__show_trust_audit_result,
    "_recipe_help": _adapt__recipe_help,
    "_show_watchdog_system_guide": _adapt__show_watchdog_system_guide,
    "_print_migration_plan": _adapt__print_migration_plan,
    "_proof_run_derive_intent": _adapt__proof_run_derive_intent,
    "_proof_run_refresh": _adapt__proof_run_refresh,
    "_proof_run_from_visible": _adapt__proof_run_from_visible,
    "_proof_run_intent_wizard": _adapt__proof_run_intent_wizard,
    "_proof_run_profiles": _adapt__proof_run_profiles,
    "_paste_and_run_watchdog": _adapt__paste_and_run_watchdog,
    "_run_xp_combination_check": _adapt__run_xp_combination_check,
    "_compare_xp_animation_slots": _adapt__compare_xp_animation_slots,
    "_switch_target_context": _adapt__switch_target_context,
    "_validate_recipe_payload": _adapt__validate_recipe_payload,
    "_recipe_dry_run_command": _adapt__recipe_dry_run_command,
    "_recipe_export_command": _adapt__recipe_export_command,
    "_recipe_repeat_command": _adapt__recipe_repeat_command,
    "_summary_rerun_command": _adapt__summary_rerun_command,
    "_action_osm_online": _adapt__action_osm_online,
    "_action_osm_local": _adapt__action_osm_local,
    "_action_osm_resume": _adapt__action_osm_resume,
    "_run_osm_commands": _adapt__run_osm_commands,
    "_run_sbu_verify_building": _adapt__run_sbu_verify_building,
    "_show_osm_past_runs": _adapt__show_osm_past_runs,
    "_show_bundle_system_guide": _adapt__show_bundle_system_guide,
    "_show_workbench_help": _adapt__show_workbench_help,
    "_show_mcp_inventory": _adapt__show_mcp_inventory,
    "_edit_blender_paths": _adapt__edit_blender_paths,
    "_edit_multiplayer_settings": _adapt__edit_multiplayer_settings,
    "_list_all_scripts": _adapt__list_all_scripts,
    "_list_script_family": _adapt__list_script_family,
    "_menu_asciiid": _adapt__menu_asciiid,
    "_menu_mobile_playwright": _adapt__menu_mobile_playwright,
    "_menu_xp_anchor_review": _adapt__menu_xp_anchor_review,
    "_menu_xp_asset_layer2_browser": _adapt__menu_xp_asset_layer2_browser,
    "_menu_xp_raw_layer_inspector": _adapt__menu_xp_raw_layer_inspector,
    "wizard.run_vps_wizard": _adapt__wizard_run_vps_wizard,
    "_wizard.run_vps_wizard": _adapt__wizard_run_vps_wizard,
    "webbrowser.open": _adapt__webbrowser_open,
}


def _build_function_wrapper(
    handler: Callable,
    handler_name: str,
    item: dict[str, Any],
    globals_dict: dict[str, Any],
) -> Callable[[argparse.Namespace], int] | None:
    """Build a wrapper for a direct function handler."""
    factory = _ADAPTERS.get(handler_name)
    if factory:
        return factory(handler, item, globals_dict)

    # Fallback: inspect signature and try a generic passthrough
    sig = inspect.signature(handler)
    params = list(sig.parameters.values())

    # If it takes argparse.Namespace as first positional arg
    if params and params[0].annotation in (argparse.Namespace, "argparse.Namespace"):
        return lambda args: handler(args)

    # If it takes no required args, just call it
    if not params or all(p.default is not inspect.Parameter.empty for p in params):
        def wrapper(args: argparse.Namespace) -> int:
            result = handler()
            return 0 if result is None else (result if isinstance(result, int) else 0)
        return wrapper

    return None


def register_all_actions(registry: ActionRegistry, globals_dict: dict[str, Any]) -> None:
    """Populate *registry* with every actionable leaf from the option tree."""
    items = _option_tree.flatten_options()
    registered: set[str] = set()
    skipped: list[tuple[str, str]] = []

    for item in items:
        action = item.get("action")
        if not action:
            continue
        non_ia = item.get("non_interactive_action", "")
        if non_ia.startswith(("interactive_only", "copy_only", "goto")):
            continue
        if item.get("kind") in ("back", "exit", "submenu"):
            continue

        handler_name = item.get("handler", "")
        command_template = item.get("command")
        label = item.get("label", action)

        wrapper: Callable[[argparse.Namespace], int] | None = None

        # Case 1: _run_command with a concrete command array
        if handler_name == "_run_command" and command_template:
            wrapper = _build_run_command_wrapper(command_template, label, globals_dict)

        # Case 2: direct function handler (look up in globals)
        elif handler_name:
            # Handle dotted names like wizard.run_vps_wizard
            if "." in handler_name:
                parts = handler_name.split(".")
                obj = globals_dict.get(parts[0])
                for part in parts[1:]:
                    obj = getattr(obj, part, None) if obj else None
                handler = obj
            else:
                handler = globals_dict.get(handler_name)

            if handler is not None and callable(handler):
                wrapper = _build_function_wrapper(handler, handler_name, item, globals_dict)
            else:
                skipped.append((action, f"handler {handler_name!r} not callable"))
                continue

        if wrapper is not None:
            registry.register(action, wrapper)
            registered.add(action)
        else:
            skipped.append((action, f"no wrapper could be built for {handler_name!r}"))

    # Also register well-known aliases that _execute_action already handles
    aliases = {
        "health-json": "health-json",
        "option-tree-json": "option-tree-json",
        "migration-plan": "migration-plan",
    }
    # These are handled inside _execute_action directly, so no registry needed.

    # Store skip list for debugging (accessible via _agent_bridge_skipped in launcher globals)
    globals_dict["_agent_bridge_skipped"] = skipped
    globals_dict["_agent_bridge_registered_count"] = len(registered)


if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser(description="Launcher agent bridge")
    _parser.add_argument("--list-actions", action="store_true", help="Print all registered action IDs")
    _args = _parser.parse_args()
    if _args.list_actions:
        # Use flatten_options() + same filter logic as register_all_actions() so
        # the output matches what is actually registerable (excludes interactive-only,
        # copy-only, goto, back, exit, submenu, and items with no action field).
        _actions = sorted(
            item["action"]
            for item in _option_tree.flatten_options()
            if item.get("action")
            and item.get("kind") not in ("back", "exit", "submenu")
            and not item.get("non_interactive_action", "").startswith(
                ("interactive_only", "copy_only", "goto")
            )
            and item.get("support_state") != "planned"
        )
        for a in _actions:
            print(a)
