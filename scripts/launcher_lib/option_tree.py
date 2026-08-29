"""Scripts launcher option tree / callgraph metadata.

This tree is the code-owned callgraph contract for ``scripts/launcher.py``.
The old testing-side launcher package owner was retired with the legacy
launcher family and must not be revived.
"""

from __future__ import annotations

from copy import deepcopy
import re

OPTION_TREE_VERSION = "2026-05-05-scripts-cutover-v1"
MANUAL_CLI_PROOF = "required: run python3 scripts/launcher.py, navigate visible menu keys, capture output"


def _default_script_ux_state(command: list[str] | None, support_state: str) -> str:
    if support_state == "non-authoritative":
        return "retired/non-authoritative"
    if not command:
        return "not-applicable"

    if command[:2] == ["node", "scripts/multiplayer_visual_watchdog.js"]:
        return "machine-mode-only"
    if command and command[0] in {".run/game", ".run/server", "make"}:
        return "not-applicable"

    return "open-user-ux"


def _item(
    key: str,
    label: str,
    kind: str,
    *,
    handler: str,
    submenu_id: str | None = None,
    action: str | None = None,
    command: list[str] | None = None,
    owner: str | None = None,
    handler_kind: str | None = None,
    inputs: str = "none",
    command_preview: str | None = None,
    artifacts: str = "none",
    non_interactive_action: str | None = None,
    support_state: str = "planned",
    failure_surface: str = "scripts launcher migration gap",
    preconditions: list[str] | None = None,
    status_hook: str | None = None,
    related_fls: list[str] | None = None,
    script_ux_state: str | None = None,
    destructive: bool = False,
    goto_settings: bool = False,
    interactive_only: bool = False,
    help_tag: str | None = None,
) -> dict:
    # OPTION_TREE_COMMAND_PREVIEW_OWNER:
    # option_tree.py owns the launcher leaf schema exported through
    # `python3 scripts/launcher.py --option-tree-json`, including
    # command_preview, support_state, non_interactive_action, destructive,
    # goto_settings, and proof-expectation metadata such as manual_cli_proof.
    # launcher.py consumes this schema but does not define it.
    if handler_kind is None:
        if kind == "submenu":
            handler_kind = "function"
        elif kind == "goto":
            handler_kind = "goto"
        elif kind == "copy_only":
            handler_kind = "copy_only"
        elif kind == "back":
            handler_kind = "back"
        elif kind == "exit":
            handler_kind = "exit"
        elif kind == "command":
            handler_kind = "command"
        elif action:
            handler_kind = "action_router"
        else:
            handler_kind = "function"
    if command_preview is None:
        command_preview = " ".join(command) if command else "none"
    if non_interactive_action is None:
        if action and not action.startswith("goto:"):
            non_interactive_action = action
        elif kind == "goto":
            non_interactive_action = action or "interactive_only: goto"
        elif kind in {"back", "exit"}:
            non_interactive_action = kind
        elif kind == "submenu":
            non_interactive_action = "interactive_only: submenu navigation"
        elif kind == "copy_only":
            non_interactive_action = "copy_only"
        else:
            non_interactive_action = "interactive_only"
    if script_ux_state is None:
        script_ux_state = _default_script_ux_state(command, support_state)
    entry = {
        "key": key,
        "label": label,
        "kind": kind,
        "handler": handler,
        "owner": owner or handler,
        "test_owner": "tests/scripts_tests/test_scripts_launcher_parity.py",
        "handler_kind": handler_kind,
        "inputs": inputs,
        "command_preview": command_preview,
        "failure_surface": failure_surface,
        "artifacts": artifacts,
        "non_interactive_action": non_interactive_action,
        "related_fls": related_fls or [],
        "preconditions": preconditions or [],
        "status_hook": status_hook,
        "manual_cli_proof": MANUAL_CLI_PROOF,
        "support_state": support_state,
        "script_ux_state": script_ux_state,
        "destructive": destructive,
        "goto_settings": goto_settings,
        "help_tag": help_tag,
    }
    if submenu_id is not None:
        entry["submenu_id"] = submenu_id
    if action is not None:
        entry["action"] = action
    if command is not None:
        entry["command"] = command
    return entry


def _back() -> dict:
    return _item("q", "Back", "back", handler="return", support_state="manual-cli-required", failure_surface="none")


def _menu(menu_id: str, title: str, items: list[dict], **extra: object) -> dict:
    menu = {"id": menu_id, "title": title, "items": items}
    menu.update(extra)
    return menu


_TREE = {
    "version": OPTION_TREE_VERSION,
    "proof_state": "blocked: every launcher path requires manual CLI smoke before any done/proven claim",
    "root_menu": "main",
    "menus": [
        _menu(
            "main",
            "Asciicker Launcher",
            [
                _item("1", "GAME", "submenu", handler="_menu_game", submenu_id="game", support_state="manual-cli-required"),
                _item("2", "ASSET & MAP EDITOR", "submenu", handler="_menu_asset_map_editor", submenu_id="asset_map_editor", support_state="manual-cli-required"),
                _item("3", "CONFIG & STATUS", "submenu", handler="_menu_config_status", submenu_id="config_status", support_state="manual-cli-required"),
                _item(">", "Command Line", "command", handler="_prompt_command_root", support_state="manual-cli-required", failure_surface="command resolution failure, unknown command, or registry build error"),
                _item("q", "Quit", "exit", handler="main loop break", support_state="manual-cli-required", failure_surface="none"),
            ],
            status_hook="launcher_lib.health.fast_probes",
        ),
        _menu(
            "game",
            "Game",
            [
                _item(
                    "1",
                    "Single Player",
                    "action",
                    handler="_run_single_player",
                    action="game-single-player",
                    command=[".run/game", "assets/a3d/game_map_y8.a3d"],
                    support_state="manual-cli-required",
                    failure_surface="native game build/launch failure",
                    preconditions=["preserve repo-local stale .run/server --max-players 1 cleanup"],
                ),
                _item("2", "Multiplayer", "submenu", handler="_menu_multiplayer", submenu_id="multiplayer", support_state="manual-cli-required", related_fls=["FL-807"]),
                _back(),
            ],
        ),
        _menu(
            "multiplayer",
            "Multiplayer",
            [
                _item("1", "Join", "submenu", handler="_menu_multiplayer_join", submenu_id="multiplayer_join", support_state="manual-cli-required", related_fls=["FL-807"]),
                _item("2", "Host", "submenu", handler="_menu_multiplayer_host", submenu_id="multiplayer_host", support_state="manual-cli-required", related_fls=["FL-807"]),
                _item(
                    "x",
                    "Settings",
                    "goto",
                    handler="_edit_multiplayer_settings",
                    action="goto:3.1",
                    command=["python3", "scripts/launcher.py", "--action", "multiplayer-configure"],
                    support_state="manual-cli-required",
                    failure_surface="multiplayer settings render/save failure",
                    goto_settings=True,
                ),
                _back(),
            ],
        ),
        _menu(
            "multiplayer_join",
            "Join",
            [
                _item(
                    "u",
                    "Enter Server URL",
                    "action",
                    handler="_join_server_url",
                    action="join-server-url",
                    command=["python3", "scripts/launcher.py", "--action", "join-server-url", "--server-url", "<host-or-url>"],
                    inputs="--server-url, prompt, or configured current server URL",
                    support_state="manual-cli-required",
                    failure_surface="URL normalization or browser launch failure",
                    related_fls=["FL-807"],
                ),
                _item(
                    "l",
                    "Show Local Server Join URLs",
                    "action",
                    handler="_open_local_lan_server",
                    action="join-local-lan",
                    command=["python3", "scripts/launcher.py", "--action", "join-local-lan", "--no-browser"],
                    inputs="launcher-owned local server state from .run/launcher-local-server.json",
                    artifacts="prints local and detected LAN URLs; opens browser unless --no-browser",
                    support_state="manual-cli-required",
                    failure_surface="no launcher-owned local server is running, stale pid, LAN address unavailable, or browser launch failure",
                    related_fls=["FL-807"],
                ),
                _item(
                    "x",
                    "Settings",
                    "goto",
                    handler="_edit_multiplayer_settings",
                    action="goto:3.1",
                    command=["python3", "scripts/launcher.py", "--action", "multiplayer-configure"],
                    support_state="manual-cli-required",
                    failure_surface="multiplayer settings render/save failure",
                    goto_settings=True,
                ),
                _back(),
            ],
        ),
        _menu(
            "multiplayer_host",
            "Host",
            [
                _item("1", "Host Local", "submenu", handler="_menu_host_local", submenu_id="host_local", support_state="manual-cli-required"),
                _item("2", "Host VPS", "submenu", handler="_menu_vps_operations_center", submenu_id="vps_operations_center", support_state="manual-cli-required"),
                _item(
                    "x",
                    "Settings",
                    "goto",
                    handler="_edit_multiplayer_settings",
                    action="goto:3.1",
                    command=["python3", "scripts/launcher.py", "--action", "multiplayer-configure"],
                    support_state="manual-cli-required",
                    failure_surface="multiplayer settings render/save failure",
                    goto_settings=True,
                ),
                _back(),
            ],
        ),
        _menu(
            "host_local",
            "Host Local",
            [
                _item(
                    "p",
                    "Play With Friends",
                    "action",
                    handler="_host_local_play_with_friends",
                    action="local-play-with-friends",
                    command=[".run/server", "--port", "<port>", "--max-players", "<players>", "--map", "<selected-map>"],
                    inputs="selected List Maps row or default map; --port/default 8080; --max-players/default 5",
                    artifacts=".run/launcher-local-server.json pid/command/join-url lifecycle record",
                    support_state="manual-cli-required",
                    failure_surface="local server build/start failure, invalid selected map, or stale launcher-owned pid",
                    related_fls=["FL-807"],
                ),
                _item(
                    "c",
                    "Max Players",
                    "action",
                    handler="_set_local_host_max_players",
                    action="local-set-max-players",
                    inputs="--max-players or prompt; default persists in .run/launcher-local-host.json",
                    artifacts=".run/launcher-local-host.json max_players preference",
                    support_state="manual-cli-required",
                    failure_surface="invalid max-player count or launcher preference write failure",
                    related_fls=["FL-2046"],
                ),
                _item("b", "Build Server Binary", "action", handler="_run_command", action="local-build", command=["make", "-C", "<repo>", "server"], support_state="manual-cli-required"),
                _item("w", "Watchdog Monitor (local)", "action", handler="_run_command", action="watchdog-local-mode", command=["node", "scripts/multiplayer_visual_watchdog.js", "--base-url", "http://127.0.0.1:<port>", "--ws-server", "127.0.0.1:<port>"], support_state="manual-cli-required"),
                _item(
                    "s",
                    "⚠ Stop Server",
                    "action",
                    handler="_stop_repo_local_servers",
                    action="local-stop-server",
                    command=["python3", "scripts/launcher.py", "--action", "local-stop-server"],
                    support_state="manual-cli-required",
                    failure_surface="launcher-owned local server pid missing, stale, permission-denied, or termination failure",
                    related_fls=["FL-1140"],
                    destructive=True,
                ),
                _item(
                    "m",
                    "Change Map",
                    "goto",
                    handler="_menu_list_maps",
                    action="goto:2.2",
                    support_state="manual-cli-required",
                    failure_surface="map list navigation requires an interactive TTY",
                    related_fls=["FL-888"],
                ),
                _item(
                    "x",
                    "Settings",
                    "goto",
                    handler="_edit_multiplayer_settings",
                    action="goto:3.1",
                    command=["python3", "scripts/launcher.py", "--action", "multiplayer-configure"],
                    support_state="manual-cli-required",
                    failure_surface="multiplayer settings render/save failure",
                    goto_settings=True,
                ),  # FL-1192/FL-1219: was [c] Local Config; key+label now matches multiplayer/join/host menus
                _back(),
            ],
        ),
        _menu(
            "vps_operations_center",
            "VPS Operations Center",
            [
                _item("h", "Server Status", "submenu", handler="_menu_vps_header", submenu_id="vps_header", support_state="manual-cli-required"),
                _item("a", "Analyze Runs", "submenu", handler="_menu_analyze_runs", submenu_id="analyze_runs", support_state="manual-cli-required"),
                _item("w", "Run Automated Tests", "submenu", handler="_menu_run_watchdog", submenu_id="run_watchdog", support_state="manual-cli-required"),  # FL-2015: merged Run Ops; re-run latest/selected now here
                _item("f", "Failure Log", "submenu", handler="_menu_failure_log", submenu_id="failure_log", support_state="manual-cli-required"),
                _item("d", "Deploy", "submenu", handler="_menu_deploy", submenu_id="deploy", support_state="manual-cli-required"),
                _item("s", "Slot Management", "submenu", handler="_menu_slot_management", submenu_id="slot_management", support_state="manual-cli-required"),
                _item(
                    "y",
                    "Switch Target",
                    "action",
                    handler="_switch_target_context",
                    action="vps-switch-target",
                    command=["python3", "scripts/launcher.py", "--action", "vps-switch-target", "--target-context", "<localhost|test-vps|live-vps|custom>"],
                    inputs="--target-context; --target-host for custom; defaults to current dashboard target context",
                    artifacts=".run/launcher-target-context.json selected target context",
                    support_state="manual-cli-required",
                    failure_surface="invalid target context or missing custom host",
                    related_fls=["FL-807", "FL-1167"],
                ),
                _item("r", "Recipes", "submenu", handler="_menu_recipes", submenu_id="recipes", support_state="manual-cli-required", related_fls=["FL-747", "FL-749"]),
                _item("m", "Mobile / Playwright", "submenu", handler="_menu_mobile_playwright", submenu_id="mobile_playwright", support_state="manual-cli-required"),
                _item("t", "Legacy Health Check", "submenu", handler="_menu_trust_audit", submenu_id="trust_audit", command=["python3", "scripts/watchdog_trust_audit.py", "--strict"], support_state="non-authoritative", related_fls=["FL-1149"]),
                _item(
                    "x",
                    "Settings",
                    "goto",
                    handler="_edit_multiplayer_settings",
                    action="goto:3.1",
                    command=["python3", "scripts/launcher.py", "--action", "multiplayer-configure"],
                    support_state="manual-cli-required",
                    failure_surface="multiplayer settings render/save failure",
                    goto_settings=True,
                ),
                _back(),
            ],
        ),
        _menu("vps_header", "Server Status", [_item("l", "Latest run summary", "action", handler="_show_watchdog_context", action="watchdog-context", support_state="manual-cli-required"), _back()]),
        _menu(
            "analyze_runs",
            "Analyze Runs",
            [
                _item("l", "List / Search", "action", handler="_run_command", action="watchdog-dashboard-list", command=["python3", "scripts/analyze_runs.py", "list"], inputs="optional analyzer filters; no run id required", artifacts="stdout run inventory", support_state="manual-cli-required"),
                _item("s", "Run Summary", "action", handler="_run_command", action="watchdog-dashboard-run-show", command=["python3", "scripts/analyze_runs.py", "show", "<run-id>"], inputs="--run-id or prompt/latest run", artifacts="stdout run summary", support_state="manual-cli-required"),
                _item("t", "Triage", "action", handler="_run_command", action="watchdog-dashboard-triage", command=["python3", "scripts/analyze_runs.py", "triage", "<run-id>"], inputs="--run-id or prompt/latest run", artifacts="stdout triage report", support_state="manual-cli-required", related_fls=["FL-883"]),
                _item("r", "Run Recordings", "action", handler="_run_command", action="watchdog-dashboard-recorder", command=["python3", "scripts/analyze_runs.py", "recorder", "<run-id>"], inputs="--run-id or prompt/latest run", artifacts="stdout recorder evidence", support_state="manual-cli-required", related_fls=["FL-883"]),
                _item("m", "Metrics", "action", handler="_run_command", action="watchdog-dashboard-metrics", command=["python3", "scripts/analyze_runs.py", "metrics", "<run-id>"], inputs="--run-id or prompt/latest run", artifacts="stdout metrics", support_state="manual-cli-required", related_fls=["FL-883"]),
                _item("g", "Server Log", "action", handler="_run_command", action="watchdog-dashboard-server-log", command=["python3", "scripts/analyze_runs.py", "server-log", "<run-id>"], inputs="--run-id or prompt/latest run", artifacts="stdout server log excerpt", support_state="manual-cli-required", related_fls=["FL-890"]),
                _item("n", "Server Snapshot", "action", handler="_run_command", action="watchdog-dashboard-server-snapshot", command=["python3", "scripts/analyze_runs.py", "server-snapshot", "<run-id>", "--at|--tick|--entity", "<selector>"], inputs="--run-id or prompt/latest run; exactly one of --at, --tick, --entity", artifacts="stdout server snapshot", support_state="manual-cli-required", related_fls=["FL-890"]),
                _item("a", "Artifacts", "action", handler="_run_command", action="watchdog-dashboard-artifacts", command=["python3", "scripts/analyze_runs.py", "artifacts", "<run-id>"], inputs="--run-id or prompt/latest run", artifacts="stdout artifact inventory", support_state="manual-cli-required", related_fls=["FL-883"]),
                _item("o", "Deploy Slot Info", "action", handler="_run_command", action="watchdog-dashboard-slot", command=["python3", "scripts/analyze_runs.py", "slot", "<run-id>"], inputs="--run-id or prompt/latest run", artifacts="stdout slot identity", support_state="manual-cli-required", related_fls=["FL-883"]),
                _item("p", "Phases", "action", handler="_run_command", action="watchdog-dashboard-phases", command=["python3", "scripts/analyze_runs.py", "phases", "<run-id>"], inputs="--run-id or prompt/latest run", artifacts="stdout phase timing", support_state="manual-cli-required", related_fls=["FL-883"]),
                _item("e", "Epochs Timeline", "action", handler="_run_command", action="watchdog-dashboard-fl-epochs", command=["python3", "scripts/analyze_runs.py", "fl", "epochs"], inputs="none", artifacts="stdout FL epoch timeline", support_state="manual-cli-required", related_fls=["FL-883"]),
                _item("u", "Epoch Statuses", "action", handler="_run_command", action="watchdog-dashboard-fl-epoch-statuses", command=["python3", "scripts/analyze_runs.py", "fl", "epoch-statuses"], inputs="none", artifacts="stdout FL epoch status summary", support_state="manual-cli-required", related_fls=["FL-883"]),
                _item("f", "Fix Attempt Accounting", "action", handler="_run_command", action="watchdog-dashboard-fl-attempt-accounting", command=["python3", "scripts/analyze_runs.py", "fl", "attempt-accounting", "--fl-ids", "<FL-NNN,...>", "--summary"], inputs="--fl-ids comma-sep FL IDs; optional --since/--until YYYY-MM-DD; remove --summary for per-FL detail", artifacts="stdout attempt accounting report (Tier 1: LINEAGE_JSON tombstone; Tier 2: keyword classifier)", support_state="manual-cli-required", related_fls=["FL-3719"]),
                _item("x", "Which Tool / Cross-refs", "action", handler="_run_command", action="watchdog-dashboard-which-tool", command=["python3", "scripts/analyze_runs.py", "which-tool"], inputs="none", artifacts="stdout analyzer route guide", support_state="manual-cli-required", related_fls=["FL-883"]),
                _back(),
            ],
        ),
        _menu(
            "failure_log",
            "Failure Log",
            [
                _item("p", "Show Log Location", "action", handler="_run_command", action="failure-log-path", command=["python3", "scripts/analyze_failure_log.py", "path"], support_state="manual-cli-required"),
                _item("s", "Search / Browse Failure Log", "action", handler="_run_command", action="failure-log-query", command=["python3", "scripts/analyze_failure_log.py", "card", "<FL-NNN>"], inputs="fuzzy FL picker", support_state="manual-cli-required"),
                _item("a", "Audit Failure Log (~10 s)", "action", handler="_run_command", action="failure-log-audit", command=["python3", "scripts/analyze_failure_log.py", "audit", "<term>"], inputs="--term or prompt", support_state="manual-cli-required"),
                _item("f", "Show Family Group", "action", handler="_run_command", action="failure-log-family", command=["python3", "scripts/analyze_failure_log.py", "family", "<FL-NNN>"], inputs="fuzzy FL picker", support_state="manual-cli-required"),
                _item("c", "View as Card", "action", handler="_run_command", action="failure-log-card", command=["python3", "scripts/analyze_failure_log.py", "card", "<FL-NNN>"], inputs="fuzzy FL picker", support_state="manual-cli-required"),
                _item("e", "Epochs Timeline", "action", handler="_run_command", action="failure-log-epochs", command=["python3", "scripts/analyze_failure_log.py", "epochs"], inputs="none", artifacts="stdout failure-log epoch timeline", support_state="manual-cli-required"),
                _item("u", "Epoch Statuses", "action", handler="_run_command", action="failure-log-epoch-statuses", command=["python3", "scripts/analyze_failure_log.py", "epoch-statuses"], inputs="none", artifacts="stdout failure-log epoch status summary", support_state="manual-cli-required"),
                _back(),
            ],
        ),
        _menu(
            "deploy",
            "Deploy",
            [
                _item("s", "Deploy → candidate server", "action", handler="_run_command", action="deploy-candidate-server", command=["python3", "scripts/deploy_candidate_server.py"], inputs="candidate ssh target from server.env; runtime slot args resolved by launcher", artifacts="candidate deploy logs and slot manifest", support_state="manual-cli-required", related_fls=["FL-873", "FL-920"]),
                _item("w", "Deploy web → candidate", "action", handler="_run_command", action="deploy-candidate-web", command=["python3", "scripts/deploy_candidate_web.py"], inputs="candidate ssh/base-url from server.env", artifacts="candidate web files and slot_manifest.json", support_state="manual-cli-required", related_fls=["FL-058", "FL-1177"]),
                _item("c", "⚠ Deploy → CURRENT (live) server", "action", handler="_run_command", action="deploy-current-server", command=["python3", "scripts/deploy_current_server.py"], inputs="current ssh target from server.env; runtime slot args resolved by launcher", artifacts="current deploy logs and slot manifest", support_state="manual-cli-required", related_fls=["FL-873", "FL-920"]),
                _back(),
            ],
        ),
        _menu(
            "run_watchdog",
            "Run Automated Tests",
            [
                _item(
                    "g",
                    "⚠ Reset & Redeploy Candidate",
                    "action",
                    handler="_run_command",
                    action="run-watchdog-commit-reset-candidate",
                    command=["python3", "scripts/watchdog_runner.py", "--mode", "full", "--target", "candidate", "--commit-all-and-reset"],
                    inputs="candidate slot config; canonical runner owns commit/reset/deploy/preflight",
                    artifacts="canonical candidate watchdog run artifacts and summary.json",
                    support_state="manual-cli-required",
                    failure_surface="canonical commit/reset candidate launch failure; FL-1167 remains open until headed proof",
                    related_fls=["FL-1167"],
                    destructive=True,
                ),
                _item(
                    "p",
                    "Proof Run Builder",
                    "submenu",
                    handler="_menu_proof_run_builder",
                    submenu_id="proof_run_builder",
                    inputs="latest run context, open FL targets, recent commits",
                    support_state="planned",
                    failure_surface="FL-1601 proof run builder submenu; requires smart_derive + analyze_runs --json + analyze_failure_log required-fields",
                    related_fls=["FL-1601", "FL-883", "FL-747"],
                ),
                _item("v", "Paste & Run Command", "action", handler="_paste_and_run_watchdog", action="paste-and-run-watchdog", command=["python3", "scripts/launcher.py", "--action", "paste-and-run-watchdog"], inputs="pasted watchdog command text (line breaks healed automatically)", artifacts="none (runs watchdog_runner.py)", support_state="implemented-unproven", failure_surface="wrun.py flatten parse failure; empty paste; operator cancel", related_fls=["FL-1601"]),
                _item("f", "Full candidate (all services)", "action", handler="_run_command", action="run-watchdog-full", command=["python3", "scripts/watchdog_runner.py", "--mode", "full", "--target", "candidate"], support_state="manual-cli-required"),
                _item("o", "Visual tests only (candidate slot)", "action", handler="_run_command", action="run-watchdog-watchdog-only", command=["python3", "scripts/watchdog_runner.py", "--mode", "watchdog-only", "--target", "candidate"], support_state="manual-cli-required"),
                _item("c", "Current smoke", "action", handler="_run_command", action="slot-current-smoke", command=["python3", "scripts/watchdog_runner.py", "--mode", "current-smoke", "--target", "current"], support_state="manual-cli-required"),
                _item("l", "Run candidate locally", "action", handler="_run_command", action="watchdog-local-mode", command=["node", "scripts/multiplayer_visual_watchdog.js", "--base-url", "http://127.0.0.1:<port>"], support_state="manual-cli-required"),
                _item("r", "Re-run Latest", "action", handler="_summary_rerun_command", action="watchdog-dashboard-rerun-latest", inputs="latest complete run summary", support_state="manual-cli-required"),  # FL-2015: merged from run_ops
                _item("s", "Re-run Selected", "action", handler="_summary_rerun_command", action="watchdog-dashboard-rerun-selected", inputs="--run-id or prompt", support_state="manual-cli-required"),  # FL-2015: merged from run_ops
                _item("?", "Watchdog System Guide", "action", handler="_show_watchdog_system_guide", handler_kind="function", action="watchdog-system-guide", support_state="manual-cli-required", failure_surface="watchdog guide render failure"),
                _back(),
            ],
        ),
        _menu(
            "proof_run_builder",
            "Proof Run Builder",
            [
                _item(
                    "r",
                    "Run from visible state",
                    "action",
                    handler="_proof_run_from_visible",
                    action="proof-run-from-state",
                    command=["python3", "scripts/launcher.py", "--action", "proof-run-from-state"],
                    inputs="smart-derive output; operator confirm/edit/save before launch",
                    artifacts="composed watchdog_runner.py command; watchdog run artifacts on launch",
                    support_state="planned",
                    failure_surface="smart derive failure, missing runs/FL data, or operator cancel",
                    related_fls=["FL-1601"],
                ),
                _item(
                    "w",
                    "Intent Wizard",
                    "action",
                    handler="_proof_run_intent_wizard",
                    action="proof-wizard",
                    command=["python3", "scripts/launcher.py", "--action", "proof-wizard"],
                    inputs="5-step guided flow: (1) select baseline runs, (2) toggle FL targets, (3) confirm fix-attempt commits, (4) confirm required fields, (5) edit observation; optional --profile seed",
                    artifacts="composed command; run artifacts on launch; optional saved profile",
                    support_state="planned",
                    failure_surface="wizard step failure, missing analyze_runs/FL data, or operator cancel",
                    related_fls=["FL-1601"],
                ),
                _item(
                    "p",
                    "Run Profiles",
                    "action",
                    handler="_proof_run_profiles",
                    action="proof-profile-list",
                    command=["python3", "scripts/launcher.py", "--action", "proof-profile-list"],
                    inputs=".run/watchdog-profiles/*.json; CRUD: list, show, create (from wizard/latest), delete (to dumpster)",
                    artifacts=".run/watchdog-profiles/<name>.json on create; dumpster on delete",
                    support_state="planned",
                    failure_surface="profile read/write failure or empty profile store",
                    related_fls=["FL-1601"],
                ),
                _item(
                    "d",
                    "Derive Intent (JSON)",
                    "action",
                    handler="_proof_run_derive_intent",
                    action="smart-derive",
                    command=["python3", "scripts/launcher.py", "--action", "smart-derive", "--json"],
                    inputs="latest run + FL + git state; read-only diagnostic",
                    artifacts="stdout JSON DerivedRunIntent",
                    support_state="planned",
                    failure_surface="smart derive failure or missing prerequisite data",
                    related_fls=["FL-1601"],
                ),
                _item(
                    "f",
                    "Refresh (re-derive from current state)",
                    "action",
                    handler="_proof_run_refresh",
                    action="proof-refresh",
                    inputs="re-runs smart derive against latest run + FL + git state",
                    artifacts="refreshed in-memory DerivedRunIntent only",
                    support_state="planned",
                    failure_surface="smart derive failure or missing prerequisite data",
                    related_fls=["FL-1601", "FL-1986"],
                ),
                _back(),
            ],
        ),
        _menu(
            "slot_management",
            "Slot Management",
            [
                _item("s", "Smoke test (current slot)", "action", handler="_run_command", action="slot-current-smoke", command=["python3", "scripts/watchdog_runner.py", "--mode", "current-smoke", "--target", "current"], support_state="manual-cli-required"),
                _item("p", "⚠ Promote Candidate → Current", "action", handler="_run_command", action="slot-promote", command=["python3", "scripts/promote_candidate_to_current.py"], support_state="manual-cli-required", destructive=True),
                _item("d", "⚠ Deploy Current Server", "action", handler="_run_command", action="deploy-current-server", command=["python3", "scripts/deploy_current_server.py"], support_state="manual-cli-required", destructive=True),
                _back(),
            ],
        ),
        _menu(
            "recipes",
            "Recipes",
            [
                _item("h", "Help / Workflow", "action", handler="_recipe_help", action="recipe-help", support_state="manual-cli-required"),
                _item("l", "List recipes", "action", handler="_run_command", action="recipe-list", command=["python3", "scripts/watchdog/recipe_store.py", "list"], support_state="manual-cli-required"),
                _item("s", "Show recipe", "action", handler="_run_command", action="recipe-show", command=["python3", "scripts/watchdog/recipe_store.py", "show", "<recipe>"], inputs="--recipe-name or prompt", support_state="manual-cli-required"),
                _item("m", "Make recipe from run", "action", handler="_run_command", action="recipe-make", command=["python3", "scripts/watchdog/recipe_store.py", "capture-from-run", "<run-id>", "--recipe-name", "<name>"], inputs="--run-id, --recipe-name, optional --tab/--from-rel-s/--to-rel-s", support_state="manual-cli-required"),
                _item("v", "Validate recipe", "action", handler="_validate_recipe_payload", action="recipe-validate", inputs="--recipe-name or prompt", support_state="manual-cli-required"),
                _item("d", "Dry-run replay command", "copy_only", handler="_recipe_dry_run_command", action="recipe-dry-run", command=["python3", "scripts/watchdog_runner.py", "--mode", "watchdog-only", "--target", "candidate", "--controller-mode", "recipe", "--controller-recipe", "<name>", "--dry-run"], inputs="--recipe-name or prompt", support_state="manual-cli-required"),
                _item("p", "Repeat recipe", "action", handler="_recipe_repeat_command", action="recipe-repeat", command=["python3", "scripts/watchdog_runner.py", "--mode", "watchdog-only", "--target", "candidate", "--controller-mode", "recipe", "--controller-recipe", "<name>"], inputs="--recipe-name, optional --mode/--controller-hold-open-ms", support_state="manual-cli-required"),
                _item("u", "Manual source + auto repeat", "action", handler="_run_command", action="recipe-followup-derived", command=["python3", "scripts/watchdog_runner.py", "--mode", "watchdog-only", "--target", "candidate", "--controller-mode", "manual", "--followup-repeat-with-derived-recipe"], inputs="candidate slot config; manual controller source run is auto-followed by exact-repeat replay", support_state="manual-cli-required"),
                _item("a", "Auto repeat latest", "action", handler="_run_command", action="recipe-auto", command=["python3", "scripts/watchdog_runner.py", "--auto"], support_state="manual-cli-required"),
                _item("x", "Export recipe JSON", "copy_only", handler="_recipe_export_command", action="recipe-export", command=["python3", "scripts/watchdog/recipe_store.py", "show", "<recipe>"], inputs="--recipe-name and optional --output", support_state="manual-cli-required"),
                _back(),
            ],
        ),
        _menu(
            "mobile_playwright",
            "Mobile / Playwright",
            [
                _item(
                    "r",
                    "Run",
                    "action",
                    handler="_run_command",
                    action="mobile-playwright",
                    command=["node", "scripts/multiplayer_visual_watchdog.js", "--mobile-device", "<PLAYWRIGHT_DEVICE>"],
                    inputs="candidate slot configured; PLAYWRIGHT_* set in Multiplayer Settings",
                    support_state="manual-cli-required",
                ),
                _item("c", "Config", "action", handler="_menu_mobile_playwright", action="mobile-config", support_state="manual-cli-required"),
                _item("s", "Status", "action", handler="_show_mobile_status", action="mobile-status", support_state="manual-cli-required"),
                _back(),
            ],
        ),
        _menu(
            "trust_audit",
            "Legacy Health Check",
            [
                _item(
                    "r",
                    "Run legacy strict audit",
                    "action",
                    handler="_run_command",
                    action="trust-audit",
                    command=["python3", "scripts/watchdog_trust_audit.py", "--strict"],
                    support_state="non-authoritative",
                    failure_surface="legacy canary only; not an R1-R9 proof gate",
                    related_fls=["FL-1149"],
                    script_ux_state="retired/non-authoritative",
                ),
                _item(
                    "v",
                    "View recent legacy result",
                    "action",
                    handler="_show_trust_audit_result",
                    action="trust-audit-view",
                    support_state="non-authoritative",
                    failure_surface="legacy trust-audit artifact missing or unreadable",
                    related_fls=["FL-1149"],
                    script_ux_state="retired/non-authoritative",
                ),
                _back(),
            ],
        ),
        _menu(
            "asset_map_editor",
            "Asset & Map Editor",
            [
                _item(
                    "1",
                    "Launch ASCIIID Map Editor",
                    "action",
                    handler="_menu_asciiid",
                    action="map-asciiid",
                    command=["python3", "scripts/launcher.py", "--action", "map-asciiid", "--map", "<map_path>"],
                    inputs="--map, selected List Maps row, or manual prompt; default assets/a3d/game_map_y8.a3d; path must be an existing .a3d",
                    artifacts=".run/map_selection/asciiid-load-proof.json when opened through the selected-map proof path",
                    support_state="manual-cli-required",
                    failure_surface="asciiid missing/build failure or selected map not actually loaded",
                    related_fls=["FL-888"],
                ),
                _item("2", "List Maps", "submenu", handler="_menu_list_maps", submenu_id="list_maps", support_state="manual-cli-required", related_fls=["FL-888", "FL-891"]),
                _item("3", "Sprite Asset Browser", "submenu", handler="_menu_xp_asset_browser", submenu_id="xp_asset_browser", support_state="manual-cli-required", related_fls=["FL-917"]),
                _item("4", "Bundle Mods (retired)", "action", handler="_menu_bundle_mods_retired", action="asset-bundle-retired", support_state="retired/non-authoritative", failure_surface="old appearance-bundle mod/compiler owner deleted by FL-4049", related_fls=["FL-4049", "FL-1177"]),
                _item("5", "Dev Tool Scripts", "submenu", handler="_menu_dev_tool_scripts", submenu_id="dev_tool_scripts", support_state="manual-cli-required", related_fls=["FL-883"]),
                _item("6", "Info / Help", "submenu", handler="_menu_info_help", submenu_id="info_help", support_state="manual-cli-required"),
                _item(
                    "7",
                    "Semantic Maps",
                    "submenu",
                    handler="_menu_semantic_maps",
                    submenu_id="semantic_maps",
                    support_state="manual-cli-required",
                    related_fls=["FL-3190", "FL-2897"],
                ),
                _back(),
            ],
        ),
        _menu(
            "list_maps",
            "List Maps",
            [
                _item(
                    "b",
                    "Browse Minimap Maps",
                    "action",
                    handler="_browse_minimap_maps",
                    action="map-browse-minimaps",
                    command=["python3", "scripts/launcher.py", "--action", "map-browse-minimaps"],
                    inputs="map inventory from assets/a3d/*.a3d; renders minimap/browser rows and stores selected map in launcher context",
                    artifacts="launcher session selected-map context only; no config writes",
                    non_interactive_action="map-browse-minimaps",
                    support_state="manual-cli-required",
                    failure_surface="minimap browser shell exists; visual minimap preview and manual smoke proof still open",
                    related_fls=["FL-888", "FL-891", "FL-904", "FL-905"],
                ),
                _item(
                    "s",
                    "Set Local Game Map",
                    "action",
                    handler="_swap_map_for_single_player",
                    action="map-swap-single-player",
                    command=["python3", "scripts/launcher.py", "--action", "map-swap-single-player", "--map", "<map_path>"],
                    inputs="selected minimap-browser row or --map; path must be an existing .a3d",
                    artifacts=".run/map_selection/single-player.json consumed by _run_single_player",
                    non_interactive_action="map-swap-single-player --map <map_path>",
                    support_state="manual-cli-required",
                    failure_surface="selected map missing or atomic map-selection write failure",
                    related_fls=["FL-809", "FL-888", "FL-891", "FL-904"],
                ),
                _item(
                    "c",
                    "Set Candidate Map",
                    "action",
                    handler="_swap_map_for_multiplayer",
                    action="map-swap-multiplayer-candidate",
                    command=["python3", "scripts/launcher.py", "--action", "map-swap-multiplayer-candidate", "--map", "<map_path>"],
                    inputs="selected minimap-browser row or --map; explicit candidate target only; path must be an existing .a3d",
                    artifacts=".run/map_selection/candidate.json; server runtime accepts --map/--world, remote deploy/run-watchdog consumption remains unproven",
                    non_interactive_action="map-swap-multiplayer-candidate --map <map_path>",
                    support_state="manual-cli-required",
                    failure_surface="selected map missing or atomic map-selection write failure",
                    related_fls=["FL-807", "FL-809", "FL-873", "FL-888", "FL-891", "FL-904"],
                ),
                _item(
                    "u",
                    "⚠ Set Current (Live) Map",
                    "action",
                    handler="_swap_map_for_multiplayer",
                    action="map-swap-multiplayer-current",
                    command=["python3", "scripts/launcher.py", "--action", "map-swap-multiplayer-current", "--map", "<map_path>"],
                    inputs="selected minimap-browser row or --map; explicit current target only; path must be an existing .a3d",
                    artifacts=".run/map_selection/current.json; server runtime accepts --map/--world, remote deploy/current-smoke consumption remains unproven",
                    non_interactive_action="map-swap-multiplayer-current --map <map_path>",
                    support_state="manual-cli-required",
                    failure_surface="selected map missing or atomic map-selection write failure",
                    related_fls=["FL-807", "FL-809", "FL-873", "FL-888", "FL-891", "FL-904"],
                ),
                _item(
                    "o",
                    "Open Selected Map in ASCIIID",
                    "action",
                    handler="_open_selected_map_in_asciiid",
                    action="map-open-asciiid",
                    command=["python3", "scripts/launcher.py", "--action", "map-open-asciiid", "--map", "<map_path>"],
                    inputs="selected minimap-browser row or --map; path must be an existing .a3d",
                    artifacts=".run/map_selection/asciiid-load-proof.json",
                    non_interactive_action="map-open-asciiid --map <map_path>",
                    support_state="manual-cli-required",
                    failure_surface="selected map missing, asciiid missing/build failure, or ASCIIID load proof failure",
                    related_fls=["FL-888", "FL-891"],
                ),
                _item(
                    "e",
                    "Edit Instances",
                    "action",
                    handler="_run_instance_list",
                    action="map-instance-list",
                    command=["python3", "scripts/launcher.py", "--action", "map-instance-list", "--map", "<map_path>"],
                    inputs="--map, selected minimap-browser row, or default map",
                    artifacts="stdout instance inventory from inspect_a3d.py",
                    non_interactive_action="map-instance-list --map <map_path>",
                    support_state="manual-cli-required",
                    failure_surface="selected map missing or inspect_a3d instance listing failure",
                    related_fls=["FL-888", "FL-904", "FL-905"],
                ),
                _item(
                    "n",
                    "New Blank/Test Map",
                    "action",
                    handler="_run_new_test_map",
                    action="map-new-test-map",
                    command=["python3", "scripts/gen_minimal_a3d.py", "--out", "<a3d_output>", "--grid", "<grid>", "--material-id", "<material_id>"],
                    inputs="--output or prompt; --grid default 1; --material-id default 1; refuses overwrite unless --force is passed directly",
                    artifacts="new .a3d file at requested output path",
                    support_state="manual-cli-required",
                    related_fls=["FL-888", "FL-891"],
                ),
                _back(),
            ],
        ),
        _menu(
            "xp_asset_browser",
            "Sprite Asset Browser",
            [
                _item("v", "View Layer-2 Browser", "action", handler="_menu_xp_asset_layer2_browser", action="asset-browse-xp-assets", support_state="manual-cli-required"),
                _item("r", "Raw Layer Inspector", "action", handler="_menu_xp_raw_layer_inspector", action="asset-browse-xp-raw-layers", support_state="manual-cli-required"),
                _item("a", "Anchor Review (Semantic Map)", "action", handler="_menu_xp_anchor_review", action="asset-anchor-review", support_state="manual-cli-required"),
                _item("u", "UV Body Viewer", "action", handler="_menu_xp_uv_body_viewer", action="asset-uv-body-viewer", support_state="manual-cli-required", inputs="semantic map JSON path (prompted interactively); XP sprite via reference_xp; body map XP auto-loaded from pipeline-v3/output/ if present", artifacts="interactive TTY: 3-panel layout — sprite+tint / region-only / UV or body map band ([b] toggle); [Ctrl+S] saves anchor edits", failure_surface="pipeline-v3 not a sibling of Y9-2; TTY required"),
                _item("m", "Mounted Overlay Validation", "action", handler="_menu_mounted_overlay_validation", action="asset-mounted-overlay-validation", support_state="manual-cli-required", inputs="wolack-0101.json pre-selected (docs/research/ascii/semantic_maps/wolack-0101.json); load wolack-attack-body.xp as skin in right panel", artifacts="interactive UV Body Viewer: composite panel shows rider overlay on mount base; validate rider_offset_by_facing at all 8 angles", failure_surface="wolack-0101.json missing; pipeline-v3 not a sibling", related_fls=["FL-3386", "FL-2407"]),
                _item(
                    "w",
                    "Open XPEdit",
                    "action",
                    handler="webbrowser.open",
                    action="asset-open-xpedit",
                    command=["open", "https://rikiworld.com/xpedit"],
                    inputs="none",
                    artifacts="browser navigates to hosted XP editor",
                    support_state="manual-cli-required",
                    failure_surface="browser launch failure",
                    related_fls=["FL-917"],
                ),
                _item(
                    "c",
                    "Compare Sprite Animations",
                    "action",
                    handler="_compare_xp_animation_slots",
                    action="asset-compare-xp-animation-slots",
                    interactive_only=True,
                    inputs="two-slot target: Slot A production XP catalog from assets/sprites/*.xp via optional --slot-a; Slot B production XP catalog from assets/sprites/*.xp via optional --slot-b",
                    artifacts=".run/xp_animation_compare/latest.json by default; --output may override",
                    support_state="manual-cli-required",
                    failure_surface="metadata parse/input selector failure; manual slot-picker UX remains unproven",
                    related_fls=["FL-917", "FL-903", "FL-935"],
                ),
                _item("p", "Export XP → PNG", "action", handler="_export_xp_to_png", action="asset-export-xp-to-png", interactive_only=True, support_state="manual-cli-required", failure_surface="XP export render/write failure", related_fls=["FL-917"]),
                _back(),
            ],
        ),
        _menu(
            "semantic_maps",
            "Semantic Maps",
            [
                _item(
                    "v",
                    "Validate",
                    "action",
                    handler="_run_command",
                    action="semantic-maps-validate",
                    command=["python3", "scripts/validate_semantic_maps.py"],
                    artifacts="stdout validation report for docs/research/ascii/semantic_maps/*.json",
                    support_state="manual-cli-required",
                    failure_surface="semantic map JSON missing, schema violation, or reference XP not found",
                    related_fls=["FL-3190", "FL-2897"],
                ),
                _item(
                    "l",
                    "List Files",
                    "action",
                    handler="_run_command",
                    action="semantic-maps-list",
                    command=["ls", "-la", "docs/research/ascii/semantic_maps/"],
                    artifacts="stdout listing of vendored semantic map files",
                    support_state="manual-cli-required",
                    failure_surface="semantic maps directory missing or unreadable",
                    related_fls=["FL-3190", "FL-2897"],
                ),
                _item(
                    "g",
                    "Generate Body Map",
                    "action",
                    handler="_menu_generate_body_map",
                    action="semantic-maps-generate-body-map",
                    inputs="semantic map JSON path (number picker or full path); reference XP from reference_xp field",
                    artifacts="pipeline-v3/output/<stem>_body_map.xp — flat body map XP organized as region bands x angle columns",
                    support_state="manual-cli-required",
                    failure_surface="pipeline-v3 not a sibling; Y9-2 xp_core import failure; source_layer inconsistency",
                    related_fls=["FL-3190"],
                ),
                _item("u", "UV Body Viewer", "action", handler="_menu_xp_uv_body_viewer", action="semantic-maps-uv-body-viewer", support_state="manual-cli-required", inputs="semantic map JSON path; body map XP auto-loaded from pipeline-v3/output/ if present", artifacts="interactive TTY: 3-panel sprite+tint / region-only / body map band ([b] toggle)", failure_surface="pipeline-v3 not a sibling; TTY required"),
                _back(),
            ],
        ),
        _menu(
            "dev_tool_scripts",
            "Dev Tool Scripts",
            [
                _item("l", "All Scripts", "action", handler="_list_all_scripts", action="dev-scripts-all", support_state="manual-cli-required", artifacts="stdout inventory listing; child script UX audit remains open"),
                _item(
                    "a",
                    "Asset Pipeline",
                    "action",
                    handler="_run_command",
                    action="asset-help",
                    command=["python3", "-m", "scripts.pipeline", "--help"],
                    artifacts="stdout pipeline CLI help",
                    support_state="manual-cli-required",
                    failure_surface="pipeline CLI help command failure",
                ),
                _item("b", "Blender & OSM", "submenu", handler="_menu_blender_osm", submenu_id="blender_osm_tools", action="goto:2.5.b", support_state="manual-cli-required"),
                _item("c", "CLI / CLI Anything (list)", "action", handler="_list_script_family", action="dev-scripts-cli-anything", support_state="manual-cli-required", artifacts="stdout inventory listing; child script UX audit remains open"),
                _item("d", "Deployment (list)", "action", handler="_list_script_family", action="dev-scripts-deployment", support_state="manual-cli-required", artifacts="stdout inventory listing; child script UX audit remains open"),
                _item("e", "Multiplayer / Watchdog (list)", "action", handler="_list_script_family", action="dev-scripts-multiplayer-watchdog", support_state="manual-cli-required", artifacts="stdout inventory listing; child script UX audit remains open"),
                _item("f", "Testing & Verification (list)", "action", handler="_list_script_family", action="dev-scripts-testing-verification", support_state="manual-cli-required", artifacts="stdout inventory listing; child script UX audit remains open"),
                _item("g", "Maintenance (list)", "action", handler="_list_script_family", action="dev-scripts-maintenance", support_state="manual-cli-required", artifacts="stdout inventory listing; child script UX audit remains open"),
                _item("h", "Sprite Tools (list)", "action", handler="_list_script_family", action="dev-scripts-sprite-tools", support_state="manual-cli-required", artifacts="stdout inventory listing; child script UX audit remains open"),
                _item(
                    "x",
                    "Blender & OSM Config",
                    "goto",
                    handler="_edit_blender_paths",
                    action="goto:3.2",
                    support_state="manual-cli-required",
                    failure_surface="Blender/OSM config probe failure",
                ),
                _back(),
            ],
        ),
        _menu(
            "blender_osm_tools",
            "Blender & OpenStreetMap (OSM)",
            [
                _item(
                    "l",
                    "New Map From Location (traditional)",
                    "action",
                    handler="_action_osm_online",
                    action="map-osm-online",
                    command=["python3", "scripts/sbu_e2e_run.py", "--min-lat", "<min>", "--max-lat", "<max>", "--min-lon", "<min>", "--max-lon", "<max>", "--pipeline-mode", "traditional"],
                    inputs="BLOSM_API_KEY plus bounding box; non-interactive CLI requires --min-lat/--max-lat/--min-lon/--max-lon",
                    artifacts="assets/meshes/osm_runs/<run_id>/workspace.blend, meshes/, output.a3d",
                    support_state="manual-cli-required",
                    failure_surface="Blosm/addon/API-key failure or export failure",
                    related_fls=["FL-906", "FL-1175", "FL-1176", "FL-2522", "FL-2523"],
                ),
                _item(
                    "o",
                    "New Map From .osm (traditional)",
                    "action",
                    handler="_action_osm_local",
                    action="map-osm-local",
                    command=["python3", "scripts/sbu_e2e_run.py", "--osm-file", "<map.osm>", "--pipeline-mode", "traditional"],
                    inputs="local .osm path; non-interactive CLI requires --local-path",
                    artifacts="assets/meshes/osm_runs/<run_id>/workspace.blend, meshes/, output.a3d",
                    support_state="manual-cli-required",
                    failure_surface="local OSM import/export failure",
                    related_fls=["FL-906", "FL-1175", "FL-1176", "FL-2522", "FL-2523"],
                ),
                _item(
                    "b",
                    "New Pre-processed Map (.osm + ASCIIID terrain bake, baked mode)",
                    "action",
                    handler="_action_osm_local",
                    action="map-osm-local-baked",
                    command=["python3", "scripts/sbu_e2e_run.py", "--osm-file", "<map.osm>", "--pipeline-mode", "baked", "[--no-topology-bake]"],
                    inputs="local .osm path; .run/asciiid must exist; non-interactive CLI requires --local-path",
                    artifacts="assets/meshes/osm_runs/<run_id>/output.a3d baked terrain",
                    support_state="manual-cli-required",
                    failure_surface="baked OSM import/export/asciiid bake failure",
                    related_fls=["FL-906", "FL-1169", "FL-1175", "FL-1176", "FL-1181", "FL-2521"],
                ),
                _item(
                    "t",
                    "Terrain-Only Debug (baked, no topology bake, no fixtures)",
                    "action",
                    handler="_action_osm_local",
                    action="map-osm-terrain-debug",
                    command=["python3", "scripts/sbu_e2e_run.py", "--osm-file", "<map.osm>", "--pipeline-mode", "baked", "--no-topology-bake", "--stop-after-buildings-only"],
                    inputs="local .osm path; .run/asciiid must exist; non-interactive CLI requires --local-path",
                    artifacts="assets/meshes/osm_runs/<run_id>/output_terrain_only.a3d terrain-only map",
                    support_state="manual-cli-required",
                    failure_surface="terrain-only baked OSM import/export failure",
                    related_fls=["FL-906", "FL-1169", "FL-1181", "FL-2521"],
                ),
                _item(
                    "p",
                    "Process .blend",
                    "action",
                    handler="_run_osm_commands",
                    action="map-osm-export",
                    command=["python3", "scripts/sbu_e2e_run.py", "--blend-file", "<scene.blend>", "--pipeline-mode", "baked", "--skip-import"],
                    inputs="local .blend path",
                    artifacts="assets/meshes/osm_runs/<run_id>/meshes and output.a3d",
                    support_state="manual-cli-required",
                    failure_surface="Blender scene export failure",
                    related_fls=["FL-906", "FL-1175", "FL-1176"],
                ),
                _item(
                    "r",
                    "Resume",
                    "action",
                    handler="_action_osm_resume",
                    action="map-osm-resume",
                    command=["python3", "scripts/launcher.py", "--action", "map-osm-resume", "--run-id", "<run-id>", "--local-path", "<baked-map.a3d>"],
                    inputs="existing OSM run id plus baked resume .a3d path; defaults to latest run in interactive mode",
                    artifacts="assets/meshes/osm_runs/<run_id>/output.a3d with deferred fixtures appended",
                    support_state="manual-cli-required",
                    failure_surface="missing run, missing fixture specs, missing baked resume map, or sbu_e2e resume failure",
                    related_fls=["FL-1175", "FL-1176"],
                ),
                _item(
                    "h",
                    "Past Runs",
                    "action",
                    handler="_show_osm_past_runs",
                    action="map-osm-past-runs",
                    command=["python3", "scripts/launcher.py", "--action", "map-osm-past-runs"],
                    inputs="assets/meshes/osm_runs; selectable numbered list with resume handoff",
                    artifacts="stdout run inventory",
                    support_state="manual-cli-required",
                    failure_surface="OSM run directory unreadable",
                    related_fls=["FL-906", "FL-1175", "FL-1176"],
                ),
                _item(
                    "v",
                    "Verify OSM Building",
                    "action",
                    handler="_run_sbu_verify_building",
                    action="map-osm-verify-building",
                    command=["python3", "scripts/sbu_verify_building.py", "--run-id", "<run-id>", "--building", "<name-or-id>"],
                    inputs="--run-id or latest OSM run; --building name/id; optional --bbox",
                    artifacts="building verification summary and embedded marker evidence",
                    support_state="manual-cli-required",
                    failure_surface="missing run or missing building instance/marker",
                    related_fls=["FL-1171", "FL-1175", "FL-1176"],
                ),
                _item(
                    "c",
                    "Blender Config",
                    "goto",
                    handler="_edit_blender_paths",
                    action="goto:3.2",
                    support_state="manual-cli-required",
                    failure_surface="Blender/OSM config probe failure",
                ),
                _back(),
            ],
        ),
        _menu(
            "info_help",
            "Info / Help",
            [
                _item("g", "Getting Started", "action", handler="_show_getting_started", handler_kind="function", action="getting-started", support_state="manual-cli-required", failure_surface="launcher getting-started panel render failure"),
                _item("b", "Bundle System Guide", "action", handler="_show_bundle_system_guide", handler_kind="function", action="bundle-system-guide", support_state="manual-cli-required", failure_surface="bundle system guide render failure"),
                _item("p", "Pipeline CLI Help", "action", handler="_run_command", action="asset-help", command=["python3", "-m", "scripts.pipeline", "--help"], support_state="manual-cli-required"),
                _item("w", "Workbench Help", "action", handler="_show_workbench_help", action="workbench-help", support_state="manual-cli-required"),
                _item("l", "Launcher Option Tree", "action", handler="_print_option_tree_json", action="option-tree-json", support_state="manual-cli-required"),
                _item("m", "Migration Guide (read-only)", "action", handler="_print_migration_plan", action="migration-plan", command=["python3", "scripts/launcher.py", "--migration-plan"], support_state="manual-cli-required"),
                _back(),
            ],
        ),
        _menu(
            "config_status",
            "Config & Status",
            [
                _item("1", "Multiplayer Settings", "submenu", handler="_edit_multiplayer_settings", submenu_id="multiplayer_settings", support_state="manual-cli-required"),
                _item("2", "Blender & OpenStreetMap Config", "submenu", handler="_edit_blender_paths", submenu_id="blender_osm_config", support_state="manual-cli-required"),
                _item("v", "Server Status", "submenu", handler="_menu_vps_header", submenu_id="vps_header", support_state="manual-cli-required", related_fls=["FL-2044"]),
                _item("a", "Analyze Runs", "submenu", handler="_menu_analyze_runs", submenu_id="analyze_runs", support_state="manual-cli-required", related_fls=["FL-2044"]),
                _item("3", "Tool Server Status (MCP)", "action", handler="_show_mcp_inventory", action="show-mcps", support_state="manual-cli-required"),
                _item("b", "Build Game (~60 s)", "action", handler="_run_command", action="build-game", command=["make", "-C", "<repo>", "game"], support_state="manual-cli-required"),
                _item("s", "Build Server (~60 s)", "action", handler="_run_command", action="build-server", command=["make", "-C", "<repo>", "server"], support_state="manual-cli-required"),
                _item("h", "Expand Health Details", "action", handler="_render_health_table", action="health-json", support_state="manual-cli-required"),
                _item(
                    "o",
                    "Blender & OpenStreetMap Tools",
                    "goto",
                    handler="_menu_blender_osm",
                    action="goto:2.5.b",
                    support_state="manual-cli-required",
                    failure_surface="Blender/OSM tools navigation requires an interactive TTY",
                    related_fls=["FL-906", "FL-1175", "FL-1176"],
                ),
                _back(),
            ],
            status_hook="launcher_lib.health.full_health_check",
        ),
        _menu(
            "multiplayer_settings",
            "Multiplayer Settings",
            [
                _item("w", "Multiplayer Wizard", "action", handler="_wizard.run_vps_wizard", action="multiplayer-wizard", support_state="manual-cli-required"),
                _item("r", "⚠ Reset All", "action", handler="_edit_multiplayer_settings", action="multiplayer-reset", support_state="manual-cli-required", destructive=True),
                _item(
                    "h",
                    "Host Server",
                    "goto",
                    handler="_menu_multiplayer_host",
                    action="goto:1.2.2",
                    support_state="manual-cli-required",
                    failure_surface="Host Server navigation requires an interactive TTY",
                    related_fls=["FL-807"],
                ),
                _item("q", "Save + Back", "back", handler="_senv.save + return", support_state="manual-cli-required", failure_surface="config save failure"),
                _item("u", "Unsave + Back", "back", handler="return without _senv.save", support_state="manual-cli-required", failure_surface="config discard path"),
            ],
        ),
        _menu(
            "blender_osm_config",
            "Blender & OpenStreetMap Config",
            [
                _item("f", "Fix Addons", "action", handler="_run_command", action="blender-fix-addons", command=["python3", "scripts/setup_addon.py"], support_state="manual-cli-required"),
                _item("k", "API Key", "action", handler="_edit_blender_paths", action="blender-api-key", support_state="manual-cli-required"),
                _item(
                    "o",
                    "↗ Open Blender & OpenStreetMap Workflow",
                    "goto",
                    handler="_menu_blender_osm",
                    action="goto:2.5.b",
                    support_state="manual-cli-required",
                    failure_surface="Blender/OSM tools navigation requires an interactive TTY",
                    related_fls=["FL-906", "FL-1175", "FL-1176"],
                ),
                _back(),
            ],
        ),
    ],
}


def _attach_paths(tree: dict) -> dict:
    menus = {menu.get("id"): menu for menu in tree.get("menus", [])}
    root_id = tree.get("root_menu", "main")

    def annotate_menu(menu_id: str, base_path: list[str], seen: set[str]) -> None:
        menu = menus.get(menu_id)
        if not menu:
            return
        for item in menu.get("items", []):
            item_path = [*base_path, str(item.get("label", ""))]
            item["path"] = " / ".join(part for part in item_path if part)
            submenu_id = item.get("submenu_id")
            if not isinstance(submenu_id, str):
                continue
            for child_id in submenu_id.split("|"):
                if child_id in seen:
                    continue
                annotate_menu(child_id, item_path, {*seen, child_id})

    root = menus.get(root_id)
    if root:
        for item in root.get("items", []):
            label = str(item.get("label", ""))
            item["path"] = label
            submenu_id = item.get("submenu_id")
            if isinstance(submenu_id, str):
                for child_id in submenu_id.split("|"):
                    annotate_menu(child_id, [label], {root_id, child_id})

    for menu in tree.get("menus", []):
        menu_base = [str(menu.get("title") or menu.get("id") or "")]
        for item in menu.get("items", []):
            item.setdefault("path", " / ".join([*menu_base, str(item.get("label", ""))]))
    return tree


def option_tree() -> dict:
    """Return a deep-copied launcher option tree with stable human paths."""
    return _attach_paths(deepcopy(_TREE))


def derive_command_name(leaf: dict) -> str | None:
    """Derive a canonical command name from an option-tree leaf.

    Skips leaves with kind in {'command', 'back'} — those are navigation
    surfaces, not executable commands.

    Priority:
    1. action field (strip menu-id prefix, keep hyphens)
    2. label field slugified
    3. handler field fallback
    """
    kind = leaf.get("kind")
    if kind in ("command", "back"):
        return None
    action = leaf.get("action")
    label = leaf.get("label", "")
    handler = leaf.get("handler", "")
    if action and isinstance(action, str) and not action.startswith("goto:"):
        # Strip menu-id prefix like "game-single-player" → "single-player"
        parts = action.split("-")
        if len(parts) > 1 and parts[0] in {"game", "local", "watchdog", "bundle", "map", "mesh", "sprite", "deploy"}:
            return "-".join(parts[1:])
        return action
    if label:
        return _slugify(label)
    if handler and isinstance(handler, str):
        return handler.lstrip("_")
    return None


def _slugify(label: str) -> str:
    s = label.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s]+", "-", s)
    return s.strip("-")


def build_command_registry(handler_globals: dict) -> "CommandRegistry":
    """Build a CommandRegistry from the canonical option tree."""
    from scripts.launcher_lib.command_registry import CommandRegistry
    reg = CommandRegistry(handler_globals)
    reg.build_from_option_tree()
    return reg


def build_tree(tree: dict | None = None) -> dict[str, dict]:
    """Return menus indexed by id for rule-oriented tests."""
    source = tree or option_tree()
    return {menu["id"]: menu for menu in source.get("menus", [])}


def flatten_options(tree: dict | None = None) -> list[dict]:
    """Flatten menu items into one list with ``menu_id`` attached."""
    source = tree or option_tree()
    flattened: list[dict] = []
    for menu in source.get("menus", []):
        for item in menu.get("items", []):
            entry = dict(item)
            entry["menu_id"] = menu.get("id")
            flattened.append(entry)
    return flattened


def assert_key_uniqueness(tree: dict | None = None) -> None:
    """Raise AssertionError if any menu has duplicate item keys.

    Call this from tests to detect unreachable items introduced by colliding keys.
    """
    source = tree or option_tree()
    for menu in source.get("menus", []):
        menu_id = menu.get("id", "<unknown>")
        seen: set[str] = set()
        for item in menu.get("items", []):
            key = item.get("key")
            if key is None:
                continue
            assert key not in seen, (
                f"Duplicate key {key!r} in menu {menu_id!r}. "
                "The second item is permanently unreachable."
            )
            seen.add(key)
