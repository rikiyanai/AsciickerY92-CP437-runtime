#!/usr/bin/env python3
"""Operator-facing semantics for canonical watchdog summaries.

The canonical runner orchestrates proof runs, but gate interpretation belongs
to analyzer-owned facts. Keep this module text-only and side-effect free so the
console and dashboard can share the same meaning without scraping stdout.
"""
from __future__ import annotations

import json
import re as _re
from functools import lru_cache as _lru_cache
from pathlib import Path
from typing import Any

try:
    import cli_style as _cs
    _HAS_CLI_STYLE = True
except ImportError:
    _HAS_CLI_STYLE = False

REPO_ROOT = Path(__file__).resolve().parents[1]


DEPRECATED_FALSE_GATES: set[str] = {
    "local_position_reorder_guard_observed",
    "local_snapshot_age_guard_observed",
}

DIAGNOSTIC_FALSE_GATES: set[str] = {
    "passive_hold_no_mutation_ok",
    "respawn_presentation_oscillation",
}

PICKUP_GATES: set[str] = {
    "pickup_client_rendered_world_item_observed",
    "pickup_signal_observed",
    "pickup_visible_legitimate_chain_observed",
    "pickup_map_item_authorship_observed",
    "pickup_server_world_item_observed",
    "pickup_intent_observed",
    "pickup_ownership_transition_observed",
}

LAG_FAMILY_GATES: set[str] = {
    "lag_stable_at_respawn",
    "post_join_lag_stable",
    "lag_floor_p95_ok",
    "remote_interpolation_smooth",
    "gameplay_remote_floor_support_ok",
}

GATE_HELP: dict[str, str] = {
    "facing_intent_nonzero_bidirectional": (
        "Server-wire facing intent was not observed for both tabs; this is an "
        "intent-evidence failure, not proof that the sprites visually faced wrong."
    ),
    "facing_intent_nonzero_tab1": (
        "Server-wire facing intent was not observed for tab1."
    ),
    "facing_intent_nonzero_tab2": (
        "Server-wire facing intent was not observed for tab2."
    ),
    "pickup_client_rendered_world_item_observed": (
        "A server/map world item existed, but the recorder did not prove that the "
        "client rendered a world item before pickup."
    ),
    "pickup_signal_observed": (
        "The full visible pickup chain was not observed: map item, server world "
        "item, client-rendered item, pickup intent, and ownership transition must "
        "all line up."
    ),
    "pickup_visible_legitimate_chain_observed": (
        "Visible pickup proof did not close for a single legitimate item chain."
    ),
    "passive_hold_no_mutation_ok": (
        "Passive hold saw or could not rule out state mutation before an intended "
        "transition; diagnostic until passive windows are rebaselined."
    ),
    "respawn_presentation_oscillation": (
        "Respawn-window presentation changed too many times or the derived "
        "respawn presentation surface regressed; diagnostic stability gate."
    ),
    "world_item_visual_contract_match_ok": (
        "Dropped/world items resolved to expected bundle definition/style rows; "
        "false is a world-item visual contract mismatch, not attack proof."
    ),
    "equip_visual_contract_match_ok": (
        "Equipped authoritative items match rendered bundle item rows."
    ),
    "render_selector_contract_match_ok": (
        "The render selector emitted the expected bundle layers."
    ),
    "scenario_attack_attachment_integrity_ok": (
        "Attack render had body plus equipped slot layers with fallback mask zero."
    ),
    "evidence_attack_animation_surface_observed": (
        "The recorder observed an attack animation surface over the proof window."
    ),
    "scenario_attack_lane_scope_ok": (
        "The attack proof window was actually exercised."
    ),
    "evidence_death_animation_surface_observed": (
        "The recorder observed a death animation surface; null means death was not "
        "observed, not that death rendering passed."
    ),
    "gameplay_dead_anim_frame_advancing_when_clamped_absent": (
        "Death animation did not continue advancing after the expected clamp."
    ),
    "gameplay_dead_anim_wrong_family_absent": (
        "Death/corpse presentation did not use the wrong animation family."
    ),
    "evidence_npc_corpse_visibility_observed": (
        "The recorder observed tracked NPC corpse visibility after death; null means "
        "missing observation, not wrong gameplay."
    ),
    "scenario_npc_corpse_survives_hold": (
        "The tracked NPC corpse remained visible through the hold window."
    ),
    "gameplay_npc_dead_anim_wrong_family_absent": (
        "Tracked NPC death/corpse presentation did not use the wrong animation family."
    ),
    "scenario_input_choreography_ok": (
        "The controller attach/input choreography completed. False is input dispatch "
        "failure, not gameplay proof."
    ),
    # Infrastructure / connectivity
    "ws_tcp_ready": "WebSocket TCP layer is responsive; false means the server was unreachable before any game handshake.",
    "http_ready": "HTTP server is serving game assets; false means the web/game asset server failed before the browser could load.",
    "ws_stable_tab1": "WebSocket connection held stable for tab1; false means the connection dropped during the session.",
    "ws_stable_tab2": "WebSocket connection held stable for tab2; false means the connection dropped during the session.",
    "tick_cadence_ok": "Server tick rate stayed within acceptable bounds; false means server overload, clock skew, or tick loop stall.",
    "bootstrap_state_sane_tab1": "Tab1 received a valid initial game state; false means the bootstrap/join handshake failed or produced an invalid snapshot.",
    "bootstrap_state_sane_tab2": "Tab2 received a valid initial game state; false means the bootstrap/join handshake failed or produced an invalid snapshot.",
    "bootstrap_state_sane_bidirectional": "Both tabs received valid initial game state; false means at least one tab's join handshake failed.",
    "tab1_world_ready": "World geometry and entities loaded for tab1; false means world load failed or timed out before gameplay could begin.",
    "tab2_world_ready": "World geometry and entities loaded for tab2; false means world load failed or timed out before gameplay could begin.",
    "load_both_world": "Both tabs loaded the game world; false means at least one tab failed world load entirely.",
    "live_manifest_match": "Deployed slot manifest matches expected source identity; false means a stale or wrong binary/web bundle is running.",
    "machine_health_pass": "VPS server reported healthy CPU/memory state; false means resource pressure may have affected game behavior.",
    "bundle_hash_match_ok": "Deployed web bundle hash matches the canonical build; false means stale or wrong web assets are being served.",
    "ids_lock_hash_match_ok": "ids-lock file hash matches expected; false means bundle IDs don't match the deployed server — visual contract is broken.",
    "actor_bundle_load_ok": "Actor/character bundle loaded successfully; false means V2 bundle data is missing or malformed on the candidate.",
    "artifacts_present": "Run produced recorder/metrics artifacts; false means the run aborted before any evidence was collected.",
    "phase_sample_coherence_ok": "Recorder samples are coherent across proof phases; false means recording was reset, split, or the phase boundary is wrong.",
    "join_opcode_ok_tab1": "Tab1 received the expected JOIN opcode; false means wrong protocol version, wrong slot, or a non-authoritative connection path.",
    "join_opcode_ok_tab2": "Tab2 received the expected JOIN opcode; false means wrong protocol version, wrong slot, or a non-authoritative connection path.",
    "ws_endpoint_correct_tab1": "Tab1 connected to the correct WebSocket endpoint; false means it hit the wrong slot (current vs candidate) or a relay/proxy.",
    "ws_endpoint_correct_tab2": "Tab2 connected to the correct WebSocket endpoint; false means it hit the wrong slot (current vs candidate) or a relay/proxy.",
    "bootstrap_nshot_pass": "N-shot bootstrap check completed within retry limit; false means the tab exhausted retries and never reached a ready game state.",
    # Movement / prediction
    "movement_intent_nonzero_tab1": "At least one nonzero movement input was sent to the server from tab1; false is intent-evidence failure, not proof the sprite didn't move.",
    "movement_intent_nonzero_tab2": "At least one nonzero movement input was sent to the server from tab2; false is intent-evidence failure, not proof the sprite didn't move.",
    "movement_intent_nonzero_bidirectional": "Both tabs sent nonzero movement input to the server; false is intent-evidence failure, not a visual movement bug.",
    "tab1_motion_progress": "Tab1 player position changed during the movement window; false means position was frozen despite movement input being sent.",
    "tab2_motion_progress": "Tab2 player position changed during the movement window; false means position was frozen despite movement input being sent.",
    "tab1_jitter_ok": "Tab1 position jitter stayed within bounds; false means erratic oscillation in the local player's position.",
    "tab2_jitter_ok": "Tab2 position jitter stayed within bounds; false means erratic oscillation in the local player's position.",
    "tab1_z_jitter_ok": "Tab1 vertical (Z) jitter was within bounds; false means floor-height oscillation or physics instability.",
    "tab2_z_jitter_ok": "Tab2 vertical (Z) jitter was within bounds; false means floor-height oscillation or physics instability.",
    "tab1_snapback_absent": "No snapback correction spikes detected for tab1; false means server corrections were large enough to cause visible snapping.",
    "tab2_snapback_absent": "No snapback correction spikes detected for tab2; false means server corrections were large enough to cause visible snapping.",
    "local_ack_base_parity_ok": "Local ack-base sequence matched expected; false means prediction/reconciliation state drifted from the server's view.",
    "local_correction_budget_ok": "Server correction magnitude stayed within budget; false means large corrections indicate prediction drift or physics mismatch.",
    "local_replay_queue_health_ok": "Replay queue did not accumulate stale entries; false means a prediction backlog built up, typically causing rubberbanding.",
    "snap_frequency_ok": "Server snapshot delivery frequency was within expected range; false means snapshots arrived too slowly or irregularly.",
    "position_alignment_ok": "Client position aligned with server-authoritative position within tolerance; false means persistent position mismatch.",
    # Remote visibility / sync
    "evidence_pair_ready": "Both tabs observed each other during the recording window; false means one tab never saw the other player.",
    "remote_pair_ready": "Both tabs confirmed remote player visibility; false means remote player was not rendered or not observed.",
    "remote_observer_ready_tab1": "Tab1 had a valid remote observer reference; false means tab1 never established remote visibility before the proof window.",
    "remote_observer_ready_tab2": "Tab2 had a valid remote observer reference; false means tab2 never established remote visibility before the proof window.",
    "remote_observer_ready_bidirectional": "Both tabs had valid remote observer references; false means at least one tab never established remote visibility.",
    "local_body_visible_tab1": "Local player body was visible in tab1's own view; false means the local player was invisible to itself (render or asset failure).",
    "local_body_visible_tab2": "Local player body was visible in tab2's own view; false means the local player was invisible to itself (render or asset failure).",
    "remote_visible_tab1": "Remote player was visible to tab1; false means tab1 could not see the other player during the proof window.",
    "remote_visible_tab2": "Remote player was visible to tab2; false means tab2 could not see the other player during the proof window.",
    "remote_visible_bidirectional": "Both tabs could see each other's player; false means at least one tab failed to render the remote player.",
    "position_sync_bidirectional": "Position data from each tab matched what the other observed via server relay; false means cross-tab position disagreement.",
    # Lag / performance
    "lag_spike_count_ok": "No red-ping RTT samples were observed; false means lag_ms reached the HUD red threshold (>=200ms).",
    "post_join_lag_spike_count_ok": "DIAGNOSTIC: No red-ping RTT samples in the post-join window (>30s); tests whether startup-only spikes are the sole cause of lag_spike_count_ok failure (FL-1926).",
    "lag_yellow_spike_budget_ok": "No HUD-yellow RTT samples were observed and post-join p95 stayed below the yellow floor; false means visible yellow lag occurred or the post-join floor exceeded budget.",
    "local_controlled_movement_smooth": "The controlled local player's presentation moved smoothly during movement windows; false means local choppiness or hard-snap bursts were observed.",
    "camera_qe_jitter_ok": "Local camera yaw stayed smooth across rotate-only Q/E snapshot edges; false means snapshot reconciliation caused an opposite-direction or oversized camera jump.",
    "lag_stable_at_respawn": "Lag was within bounds during the respawn window; false means respawn-triggered processing overload caused noticeable freeze.",
    "post_join_lag_stable": "Lag was stable after the join phase settled; false means post-join processing overload persisted into gameplay.",
    "lag_floor_p95_ok": "95th-percentile lag floor was within acceptable bounds; false means the baseline latency is too high for acceptable gameplay.",
    "remote_interpolation_smooth": "Remote player interpolation was smooth; false means interpolation gaps or jumps in the remote player's movement.",
    # Combat / death
    "combat_attack_input_observed": "At least one attack input was recorded from the client; false is input-evidence failure, not a combat bug.",
    "combat_signal_observed": "Server broadcast a combat signal (swing/hit); false means the server never registered an attack.",
    "combat_swing_broadcast": "Server sent a swing message to both tabs; false means one or both tabs did not receive the swing broadcast.",
    "npc_combat_signal_observed": "NPC combat was observed — server processed NPC attack or damage; false means NPCs didn't engage during the proof window.",
    "death_signal_observed": "A death state change was observed; false means no death event was recorded (may be expected in non-combat runs).",
    "death_pvp_confirmed": "Player-vs-player death was confirmed by both recorder and server state; false means death was not confirmed as a PvP kill.",
    "death_npc_confirmed": "NPC death was confirmed by recorder evidence; false means NPC kill was not captured in the proof window.",
    "fallthrough_absent": "No unexpected state fall-through in the death/respawn cycle; false means the state machine hit an unhandled transition.",
    "npc_not_overlapping_local": "NPCs were not overlapping the local player in a way that triggers false combat signals; false means NPC spatial collision contaminated proof.",
    "npc_anim_while_moving_ok": "NPCs showed movement animation while moving; false means NPC animation desync — moving but playing idle.",
    "npc_proximity_ok": "At least one NPC was within proximity range during the combat window; false means the combat window ran without any NPCs nearby.",
    "npc_hp_drop_observed": "NPC health dropped during the run, confirming damage registered; false means attacks were sent but the server never applied damage.",
    "server_log_swing_recv_ok": "Server log shows at least one swing_recv event during combat; false means the server never received the attack intent from the client.",
    # Gameplay / physics
    "gameplay_spawn_physics_ready_ok": "Physics was ready for the player at spawn; false means the player was placed before floor physics settled, causing a spawn-fall or freeze.",
    "gameplay_remote_floor_support_ok": "Remote player floor support was computed correctly; false means the remote player appeared to float or fall through terrain.",
    # Bundle / appearance
    "appearance_contract_match_ok": "Rendered appearance matched the expected bundle contract for the equipped loadout; false is a visual contract mismatch.",
    "appearance_sentinel_absent": "No sentinel/fallback appearance values in the rendered output; false means the bundle lookup fell back to a default sentinel instead of real data.",
    "inventory_visual_contract_match_ok": "Inventory item visuals matched bundle-defined expectations; false means an inventory item rendered with wrong or missing visual data.",
    "server_blood_decal_coverage_ok": "Blood decal coverage was within expected bounds after combat; false means decals were missing, excessive, or placed at wrong positions.",
}

# Fallback map for environments that cannot read the live failure log.
GATE_FL: dict[str, str] = {
    "world_item_visual_contract_match_ok": "FL-1173",
    "equip_visual_contract_match_ok": "FL-1172",
    "render_selector_contract_match_ok": "FL-1172",
    "scenario_attack_attachment_integrity_ok": "FL-1170",
    "evidence_attack_animation_surface_observed": "FL-869",  # FL-869 STALE 2026-05-10 — 5D arrays deleted, bundle V2 live. Map to FL-3848 (mount types) or FL-3845 (shield).
    "scenario_attack_lane_scope_ok": "FL-869",  # FL-869 STALE 2026-05-10 — see above.
    "evidence_death_animation_surface_observed": "FL-643",
    "gameplay_dead_anim_frame_advancing_when_clamped_absent": "FL-643",
    "gameplay_dead_anim_wrong_family_absent": "FL-643",
    "evidence_npc_corpse_visibility_observed": "FL-643",
    "scenario_npc_corpse_survives_hold": "FL-643",
    "gameplay_npc_dead_anim_wrong_family_absent": "FL-643",
    "scenario_input_choreography_ok": "FL-1165",
    "pickup_client_rendered_world_item_observed": "FL-1184",
    "pickup_signal_observed": "FL-1184",
    "pickup_visible_legitimate_chain_observed": "FL-1184",
    "pickup_map_item_authorship_observed": "FL-1184",
    "pickup_server_world_item_observed": "FL-1184",
    "pickup_intent_observed": "FL-1184",
    "pickup_ownership_transition_observed": "FL-1184",
    "respawn_presentation_oscillation": "FL-2372",
    "scenario_npc_corpse_survives_hold": "FL-2498",
    "camera_qe_jitter_ok": "FL-1733",
}


@_lru_cache(maxsize=1)
def _dynamic_gate_fl_map() -> dict[str, str]:
    """Resolve gate -> preferred FL from the live failure log when available."""
    try:
        import analyze_runs as _analyze_runs

        fl_index = _analyze_runs.load_fl_index(REPO_ROOT)
    except Exception:
        return {}

    resolved: dict[str, str] = {}
    for gate in getattr(fl_index, "by_gate", {}).keys():
        try:
            preferred = fl_index.gate_preferred(str(gate))
        except Exception:
            continue
        if preferred and preferred.get("fl"):
            resolved[str(gate)] = str(preferred["fl"])
    return resolved


def _gate_fl(gate: str | None) -> str | None:
    if not gate:
        return None
    gate_key = str(gate)
    return _dynamic_gate_fl_map().get(gate_key) or GATE_FL.get(gate_key)

GATE_EVIDENCE_FIELDS: dict[str, tuple[str, ...]] = {
    "gameplay_dead_anim_wrong_family_absent": (
        "remote0_snapshot_life_state",
        "remote0_life_state",
        "combat_death",
        "remote0_anim_family_at_death",
        "remote0_anim_id_at_death",
        "remote0_animation_playback_direction_at_death",
        "remote0_frame_clamped_at_death",
        "remote0_death_tick",
        "remote0_corpse_hold_age_ticks",
        "remote0_render_presentation_kind_id",
    ),
    "gameplay_dead_anim_frame_advancing_when_clamped_absent": (
        "remote0_frame_clamped_at_death",
        "remote0_frame_changed_expected",
        "remote0_frame",
        "remote0_death_tick",
        "remote0_corpse_hold_age_ticks",
    ),
    "evidence_death_animation_surface_observed": (
        "death_animation_surface_observed",
        "remote0_anim_id_at_death",
        "remote0_frame_clamped_at_death",
        "remote0_death_tick",
    ),
    "scenario_attack_lane_scope_ok": (
        "attack_lane_scope_ok",
        "remote0_attack_started_tick",
        "remote0_anim_id_at_attack",
        "remote0_attack_frame_advanced",
        "attack_animation_surface_observed",
    ),
    "evidence_attack_animation_surface_observed": (
        "attack_animation_surface_observed",
        "remote0_attack_started_tick",
        "remote0_anim_id_at_attack",
        "remote0_attack_frame_advanced",
    ),
    "scenario_npc_corpse_survives_hold": (
        "npc_corpse_survives_hold",
        "tracked_npc0_entity_id",
        "tracked_npc0_hp",
        "tracked_npc0_death_tick",
        "tracked_npc0_corpse_visible",
        "tracked_npc0_corpse_hold_age_ticks",
        "npc_focus_visible_after_death_hold_ok",
        "npc_focus_visible_after_death_samples",
    ),
    "evidence_npc_corpse_visibility_observed": (
        "npc_corpse_visibility_observed",
        "tracked_npc0_entity_id",
        "tracked_npc0_hp",
        "tracked_npc0_death_tick",
        "tracked_npc0_corpse_visible",
        "tracked_npc0_corpse_hold_age_ticks",
    ),
    "gameplay_npc_dead_anim_wrong_family_absent": (
        "npc_dead_anim_wrong_family_absent",
        "npc_focus_anim_family_at_death",
        "npc_focus_anim_id_at_death",
        "npc_focus_animation_playback_direction_at_death",
        "tracked_npc0_death_tick",
        "tracked_npc0_corpse_hold_age_ticks",
    ),
    "facing_intent_nonzero_bidirectional": (
        "input_world_dir",
        "local_sent_q_yaw100",
        "remote0_last_received_dir",
        "server_wire_truth.direction_nonzero_clients",
    ),
    "facing_intent_nonzero_tab1": (
        "input_world_dir",
        "local_sent_q_yaw100",
        "server_wire_truth.direction_nonzero_clients",
    ),
    "facing_intent_nonzero_tab2": (
        "input_world_dir",
        "local_sent_q_yaw100",
        "server_wire_truth.direction_nonzero_clients",
    ),
    "camera_qe_jitter_ok": (
        "camera_yaw",
        "auth_snap_yaw",
        "pred_ack_yaw",
        "replay_post_yaw",
        "ack_error_yaw",
        "local_sent_q_yaw100",
        "local_predicted_q_yaw100",
        "local_replayed_q_yaw100",
        "reconcile_tick",
        "snapshot_tick",
    ),
    "pickup_client_rendered_world_item_observed": (
        "auth_visible_world_count",
        "auth_visible_world_0",
        "auth_visible_world_definition_ids",
        "auth_visible_world_visual_style_ids",
        "auth_item_sample",
    ),
    "pickup_signal_observed": (
        "auth_item_sample",
        "auth_item_world",
        "auth_visible_world_count",
        "auth_pickup_req_sent",
        "auth_item_local",
        "auth_item_equipped_local",
    ),
    "pickup_visible_legitimate_chain_observed": (
        "auth_item_sample",
        "auth_item_world",
        "auth_visible_world_count",
        "auth_pickup_req_sent",
        "auth_item_local",
        "auth_item_equipped_local",
    ),
    "passive_hold_no_mutation_ok": (
        "auth_item_local",
        "auth_item_equipped_local",
        "actor_inventory_item_ids",
        "auth_self_loadout_revision",
    ),
    "respawn_presentation_oscillation": (
        "combat_respawn",
        "actor_render_presentation_kind_id",
        "remote0_snapshot_presentation_kind_id",
        "remote0_render_presentation_kind_id",
        "remote0_render_diverged_from_snapshot",
        "remote0_corpse_hold_age_ticks",
    ),
}

# ---------------------------------------------------------------------------
# Gate emitter location + decoupling classification
# ---------------------------------------------------------------------------

# Gates that are emitted outside direct ``result.gates.NAME =`` assignments.
# Keep this narrow and source-backed: lib-owned derived gates and Python
# post-run analyzer gates need explicit ownership so operator help can report
# the real owner rather than a bootstrap/null placeholder.
GATE_EMITTER_OVERRIDES: dict[str, dict[str, Any]] = {
    "combat_signal_observed": {
        "file": "watchdog/lib/combat_verdict.js",
        "line": 92,
    },
    "npc_combat_signal_observed": {
        "file": "watchdog/lib/combat_verdict.js",
        "line": 162,
    },
    "scenario_attack_lane_scope_ok": {
        "file": "watchdog/lib/phase67_gates.js",
        "line": 161,
    },
    "scenario_npc_corpse_survives_hold": {
        "file": "watchdog/lib/phase67_gates.js",
        "line": 182,
    },
    "pickup_visible_legitimate_chain_observed": {
        "file": "analyze_runs.py",
        "line": 2374,
    },
    "pickup_signal_observed": {
        "file": "analyze_runs.py",
        "line": 2376,
    },
}

# Coupling annotations for gates whose emitters depend on debug-only data or
# game-binary-internal state, violating Law-7 (debug mutation is not proof) or
# Law-9 (watchdog is outside the game binary).
#
# Structure per entry:
#   law:          architecture law violated (e.g. "Law-7", "Law-9", "R1")
#   reason:       one-line explanation of the coupling
#   allowed_debug: True = explicitly allowed for candidate debugging;
#                  coupling is stripped from promote requirements
#   promote_strip: if True, report this gate as non-applicable during promote
#
# Add entries here after auditing the gate's emitter context in watchdog.js.
GATE_COUPLING_ANNOTATIONS: dict[str, dict] = {
    "combat_signal_observed": {
        "law": "Law-9",
        "reason": "derived in combat_verdict.js from recorder-private attack samples and [swing-resolve] server-log fields.",
        "allowed_debug": True,
        "promote_strip": True,
    },
    "npc_combat_signal_observed": {
        "law": "Law-9",
        "reason": "derived in combat_verdict.js from tracked-NPC recorder probes plus private server combat log fields.",
        "allowed_debug": True,
        "promote_strip": True,
    },
    "local_snapshot_age_guard_observed": {
        "law": "Law-9",
        "reason": "reads watchdog recorder bug-tracker counters (reconcile_stale_rejected), not an external runtime surface.",
        "allowed_debug": True,
        "promote_strip": False,
    },
    "local_position_reorder_guard_observed": {
        "law": "Law-9",
        "reason": "reads watchdog recorder bug-tracker counters (reconcile_pos_reorder_rejected), not an external runtime surface.",
        "allowed_debug": True,
        "promote_strip": False,
    },
    "gameplay_remote_floor_support_ok": {
        "law": "Law-9",
        "reason": "derived from recorder-only remote_floor_support_ok samples rather than an external current-smoke surface.",
        "allowed_debug": True,
        "promote_strip": True,
    },
    "passive_hold_no_mutation_ok": {
        "law": "Law-9",
        "reason": "depends on recorder-private passive_hold_mutation_before_transition probe fields during passive hold.",
        "allowed_debug": True,
        "promote_strip": True,
    },
    "pickup_signal_observed": {
        "law": "Law-9",
        "reason": "post-run pickup proof is derived from recorder JSONL item/intent/ownership probes, not an external smoke surface.",
        "allowed_debug": True,
        "promote_strip": True,
    },
    "pickup_visible_legitimate_chain_observed": {
        "law": "Law-9",
        "reason": "post-run pickup chain verdict is analyzer-owned over recorder JSONL rather than a release/current smoke signal.",
        "allowed_debug": True,
        "promote_strip": True,
    },
    "scenario_attack_lane_scope_ok": {
        "law": "Law-9",
        "reason": "phase67 proof gate is derived from recorder-private attack-lane samples and is not part of bootstrap-only current smoke.",
        "allowed_debug": True,
        "promote_strip": True,
    },
    "scenario_npc_corpse_survives_hold": {
        "law": "Law-9",
        "reason": "phase67 corpse-hold proof gate depends on tracked-NPC recorder probes and is not a current-smoke requirement.",
        "allowed_debug": True,
        "promote_strip": True,
    },
}


def _watchdog_scripts_dir() -> Path | None:
    """Infer scripts/ directory from this module's location."""
    try:
        here = Path(__file__).resolve().parent
        if (here / "multiplayer_visual_watchdog.js").exists():
            return here
    except Exception:
        pass
    return None


@_lru_cache(maxsize=1)
def _scan_gate_emitters() -> dict[str, dict]:
    """Scan watchdog JS source for result.gates.NAME = assignment sites.

    Returns {gate_name: {file, line}} for the final direct assignment site.
    Cached once per process — source doesn't change during a run.
    """
    scripts_dir = _watchdog_scripts_dir()
    if not scripts_dir:
        return {}

    watchdog_files: list[Path] = [scripts_dir / "multiplayer_visual_watchdog.js"]
    lib_dir = scripts_dir / "watchdog" / "lib"
    if lib_dir.exists():
        watchdog_files.extend(sorted(lib_dir.glob("*.js")))

    found: dict[str, dict] = {}
    assign_re = _re.compile(r'result\.gates\.([a-zA-Z_][a-zA-Z0-9_]*)\s*=')

    for path in watchdog_files:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            rel = str(path.relative_to(scripts_dir))
        except ValueError:
            rel = path.name

        for i, line in enumerate(text.splitlines(), start=1):
            m = assign_re.search(line)
            if not m:
                continue
            gate_name = m.group(1)
            # Prefer the final direct assignment so early bootstrap/null writes
            # do not hide the later authoritative emitter.
            found[gate_name] = {"file": rel, "line": i}

    return found


def _get_gate_emitter(gate: str) -> dict | None:
    """Return emitter location + coupling annotation for a gate, or None if not found."""
    emitters = _scan_gate_emitters()
    base = GATE_EMITTER_OVERRIDES.get(gate) or emitters.get(gate)
    if base is None:
        return None
    annotation = GATE_COUPLING_ANNOTATIONS.get(gate)
    if annotation:
        coupling = "debug_allowed" if annotation.get("allowed_debug") else "violation"
        return {
            **base,
            "coupling": coupling,
            "coupling_law": annotation.get("law"),
            "coupling_reason": annotation.get("reason"),
            "promote_strip": bool(annotation.get("promote_strip")),
        }
    return {
        **base,
        "coupling": "clean",
        "coupling_law": None,
        "coupling_reason": None,
        "promote_strip": False,
    }


def gates_to_promote_strip() -> list[str]:
    """Return gate names marked promote_strip=True in GATE_COUPLING_ANNOTATIONS."""
    return [g for g, ann in GATE_COUPLING_ANNOTATIONS.items() if ann.get("promote_strip")]


def gate_description(gate: str) -> str:
    if gate in GATE_HELP:
        return GATE_HELP[gate]
    # FL-686 / FL-1713: keep staging/choreography surfaces separate from
    # gameplay runtime meaning. A false or null scenario_* gate is not itself a
    # gameplay bug; it usually means the proof window/setup never became valid.
    if gate.startswith("scenario_"):
        return "Scenario/setup gate over recorded evidence; false means the proof window or choreography precondition failed."
    if gate.startswith("evidence_"):
        return "Evidence-surface gate over recorder data; null means insufficient observation."
    if gate.startswith("gameplay_"):
        return "Gameplay gate over recorded runtime facts; false means an observed contradiction."
    return "Analyzer gate over recorded watchdog artifacts."


def _gate_kind(gate: str) -> str:
    # FL-686 / FL-1713: "actionable" means worth surfacing to the operator, not
    # necessarily "gameplay runtime owner". scenario_* and evidence_* gates can
    # still be actionable proof-window failures without being gameplay bugs.
    if gate in DEPRECATED_FALSE_GATES:
        return "deprecated"
    if gate in DIAGNOSTIC_FALSE_GATES:
        return "diagnostic"
    return "actionable"


def _gate_sort_key(gate: str) -> tuple[int, str]:
    order = {"actionable": 0, "diagnostic": 1, "deprecated": 2}
    return (order.get(_gate_kind(gate), 3), gate)


def _ordered_false_gates(summary: dict[str, Any]) -> list[str]:
    gates = [str(g) for g in (summary.get("false_gates") or [])]
    return sorted(gates, key=_gate_sort_key)


def _top_gates(summary: dict[str, Any], key: str, limit: int = 3) -> list[dict[str, Any]]:
    gates = [str(g) for g in (summary.get(key) or [])]
    if key == "false_gates":
        gates = _ordered_false_gates(summary)
    gates = gates[:limit]
    return [
        {
            "gate": gate,
            "description": gate_description(gate),
            "fl": _gate_fl(gate),
            "kind": _gate_kind(gate),
        }
        for gate in gates
    ]


def _family_fl(summary: dict[str, Any]) -> str:
    focus_gate = _choose_focus_gate(summary)
    if focus_gate:
        fl_id = _gate_fl(focus_gate)
        if fl_id:
            return fl_id
    for gate in summary.get("null_gates") or []:
        fl_id = _gate_fl(str(gate))
        if fl_id:
            return fl_id
    return "FL-1168"


def _choose_focus_gate(summary: dict[str, Any]) -> str | None:
    false_set = {str(g) for g in (summary.get("false_gates") or [])}
    deltas = summary.get("gate_deltas") or {}
    for gate in deltas.get("regressions_true_to_false") or []:
        gate = str(gate)
        if gate in false_set:
            return gate
    for gate in _ordered_false_gates(summary):
        if _gate_kind(gate) == "actionable":
            return gate
    false_gates = [str(g) for g in (summary.get("false_gates") or [])]
    return false_gates[0] if false_gates else None


def _rel_s(sample: dict[str, Any]) -> float | None:
    value = sample.get("rel_s")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _tab_name(sample: dict[str, Any]) -> str:
    return str(sample.get("tab_id") or sample.get("tab") or sample.get("source") or "-")


def _value_key(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _is_bad_sample_value(gate: str, value: Any) -> bool:
    if value is None:
        return False
    if gate in {"respawn_presentation_oscillation"}:
        return value is True or value == 1
    return value is False or value == 0


def _format_s(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}s"


def _format_window(first_s: float | None, last_s: float | None) -> str | None:
    first = _format_s(first_s)
    last = _format_s(last_s)
    if not first:
        return None
    if not last or last == first:
        return first
    return f"{first}-{last}"


def _evidence_fields_for(gate: str) -> tuple[str, ...]:
    if gate in GATE_EVIDENCE_FIELDS:
        return GATE_EVIDENCE_FIELDS[gate]
    return (gate,)


def _expected_source_text(gate: str, limit: int = 6) -> str:
    fields = _evidence_fields_for(gate)
    if not fields:
        return "-"
    shown = list(fields[:limit])
    suffix = f", +{len(fields) - limit} more" if len(fields) > limit else ""
    return ", ".join(shown) + suffix


def _collect_evidence(sample: dict[str, Any] | None, gate: str) -> dict[str, Any]:
    if not sample:
        return {}
    result: dict[str, Any] = {}
    for field in _evidence_fields_for(gate):
        if field in sample and sample.get(field) is not None:
            result[field] = sample.get(field)
    return result


def _evidence_score(sample: dict[str, Any], gate: str) -> int:
    score = 0
    for field in _evidence_fields_for(gate):
        if field not in sample:
            continue
        value = sample.get(field)
        if value is None:
            continue
        score += 1
        if value not in (False, 0, -1, ""):
            score += 2
    return score


def _format_evidence(evidence: dict[str, Any], limit: int = 5) -> str | None:
    if not evidence:
        return None
    items = list(evidence.items())[:limit]
    return ", ".join(f"{key}={value}" for key, value in items)


def _recording_path(summary: dict[str, Any]) -> Path | None:
    artifact_path = summary.get("artifact_path")
    if not artifact_path:
        return None
    path = Path(str(artifact_path)) / "recorder" / "recording.jsonl"
    return path if path.exists() else None


def _scan_gate_timelines(summary: dict[str, Any], gates: list[str]) -> tuple[dict[str, Any], str | None]:
    if not gates:
        return {}, None
    rec_path = _recording_path(summary)
    if rec_path is None:
        return {}, "recorder timeline unavailable"

    tracked = {
        gate: {
            "gate": gate,
            "present_samples": 0,
            "bad_samples": 0,
            "value_counts": {},
            "tabs": set(),
            "first_rel_s": None,
            "last_rel_s": None,
            "first_bad_rel_s": None,
            "last_bad_rel_s": None,
            "first_sample": None,
            "first_bad_sample": None,
            "first_bad_evidence_rel_s": None,
            "best_bad_evidence_score": -1,
        }
        for gate in gates
    }

    try:
        with rec_path.open(encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    sample = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                rel_s = _rel_s(sample)
                tab = _tab_name(sample)
                for gate, info in tracked.items():
                    if gate not in sample:
                        continue
                    value = sample.get(gate)
                    info["present_samples"] += 1
                    info["tabs"].add(tab)
                    counts = info["value_counts"]
                    key = _value_key(value)
                    counts[key] = counts.get(key, 0) + 1
                    if info["first_sample"] is None:
                        info["first_sample"] = sample
                    if rel_s is not None:
                        if info["first_rel_s"] is None:
                            info["first_rel_s"] = rel_s
                        info["last_rel_s"] = rel_s
                    if _is_bad_sample_value(gate, value):
                        info["bad_samples"] += 1
                        evidence_score = _evidence_score(sample, gate)
                        if (
                            info["first_bad_sample"] is None
                            or evidence_score > info["best_bad_evidence_score"]
                        ):
                            info["first_bad_sample"] = sample
                            info["first_bad_evidence_rel_s"] = rel_s
                            info["best_bad_evidence_score"] = evidence_score
                        if rel_s is not None:
                            if info["first_bad_rel_s"] is None:
                                info["first_bad_rel_s"] = rel_s
                            info["last_bad_rel_s"] = rel_s
    except OSError as exc:
        return {}, f"recorder timeline read failed: {exc}"

    result: dict[str, Any] = {}
    run_id = str(summary.get("run_id") or "")
    for gate, info in tracked.items():
        tabs = sorted(t for t in info.pop("tabs") if t)
        evidence_sample = info.get("first_bad_sample") or info.get("first_sample")
        evidence = _collect_evidence(evidence_sample, gate)
        result[gate] = {
            "gate": gate,
            "run_id": run_id,
            "present_samples": info["present_samples"],
            "bad_samples": info["bad_samples"],
            "value_counts": dict(sorted(info["value_counts"].items())),
            "tabs": tabs,
            "first_rel_s": info["first_rel_s"],
            "last_rel_s": info["last_rel_s"],
            "first_bad_rel_s": info["first_bad_rel_s"],
            "last_bad_rel_s": info["last_bad_rel_s"],
            "bad_window": _format_window(info["first_bad_rel_s"], info["last_bad_rel_s"]),
            "present_window": _format_window(info["first_rel_s"], info["last_rel_s"]),
            "evidence": evidence,
            "evidence_text": _format_evidence(evidence),
            "evidence_rel_s": info.get("first_bad_evidence_rel_s"),
        }
    return result, None


def _gate_item(gate: str, timeline: dict[str, Any] | None = None) -> dict[str, Any]:
    # Always preserve gate name in timeline so _timeline_summary can produce
    # gate-aware wording even when the recorder scan failed and returned None.
    tl: dict[str, Any] = timeline if timeline is not None else {"gate": gate, "scan_failed": True}
    return {
        "gate": gate,
        "description": gate_description(gate),
        "fl": _gate_fl(gate),
        "kind": _gate_kind(gate),
        "timeline": tl,
        "emitter": _get_gate_emitter(gate),
    }


def _summary_path(summary: dict[str, Any]) -> Path | None:
    raw = summary.get("summary_path")
    if raw:
        return Path(str(raw))
    return None


def _runs_root(summary: dict[str, Any]) -> Path | None:
    path = _summary_path(summary)
    if path and path.name == "summary.json":
        return path.parent.parent
    return None


def _load_sibling_summaries(summary: dict[str, Any]) -> list[dict[str, Any]]:
    root = _runs_root(summary)
    if not root or not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in root.glob("*/summary.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        data.setdefault("summary_path", str(path))
        rows.append(data)
    rows.sort(key=lambda row: str(row.get("started_at_utc") or ""), reverse=True)
    return rows


def _run_label(summary: dict[str, Any]) -> str:
    return str(summary.get("run_label") or "passive")


def _bad_gates(summary: dict[str, Any]) -> set[str]:
    return {str(g) for g in (summary.get("false_gates") or [])} | {
        str(g) for g in (summary.get("null_gates") or [])
    }


def _false_gates(summary: dict[str, Any]) -> set[str]:
    return {str(g) for g in (summary.get("false_gates") or [])}


def _has_gate_data(summary: dict[str, Any]) -> bool:
    return summary.get("false_gates") is not None or summary.get("null_gates") is not None


def _run_ratio(summary: dict[str, Any]) -> float:
    total = summary.get("runtime_required_total") or 0
    passed = summary.get("runtime_required_passed") or 0
    if not isinstance(total, (int, float)) or total <= 0:
        return -1.0
    if not isinstance(passed, (int, float)):
        return -1.0
    return float(passed) / float(total)


def _short_list(values: list[str] | set[str], limit: int = 5) -> str:
    ordered = sorted(str(v) for v in values)
    if not ordered:
        return "-"
    suffix = f", +{len(ordered) - limit} more" if len(ordered) > limit else ""
    return ", ".join(ordered[:limit]) + suffix


def _compare_summary(current: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    current_bad = _bad_gates(current)
    other_bad = _bad_gates(other)
    current_false = _false_gates(current)
    other_false = _false_gates(other)
    return {
        "run_id": other.get("run_id"),
        "date": other.get("started_at_utc"),
        "commit": other.get("git_head"),
        "verdict": other.get("verdict"),
        "runtime_required": f"{other.get('runtime_required_passed')}/{other.get('runtime_required_total')}",
        "false_count": len(other_false),
        "null_count": len(other.get("null_gates") or []),
        "shared_false": sorted(current_false & other_false),
        "new_bad": sorted(current_bad - other_bad),
        "recovered_bad": sorted(other_bad - current_bad),
        "command": f"python3 scripts/analyze_runs.py diff {other.get('run_id')} {current.get('run_id')}",
    }


def _similarity(current: dict[str, Any], other: dict[str, Any]) -> float:
    a = _bad_gates(current)
    b = _bad_gates(other)
    union = a | b
    gate_score = len(a & b) / len(union) if union else 0.0
    ratio_score = max(0.0, 1.0 - abs(_run_ratio(current) - _run_ratio(other)))
    return 0.75 * gate_score + 0.25 * ratio_score


def _comparison_blocks(summary: dict[str, Any]) -> dict[str, Any]:
    current_id = summary.get("run_id")
    started = str(summary.get("started_at_utc") or "")
    label = _run_label(summary)
    siblings = [
        row
        for row in _load_sibling_summaries(summary)
        if row.get("run_id") != current_id
        and _run_label(row) == label
        and _has_gate_data(row)
        and (not started or str(row.get("started_at_utc") or "") < started)
    ]
    if not siblings:
        return {}

    previous = siblings[0]
    peak_candidates = sorted(
        siblings,
        key=lambda row: (
            1 if row.get("verdict") == "pass" else 0,
            _run_ratio(row),
            row.get("runtime_required_total") or 0,
            str(row.get("started_at_utc") or ""),
        ),
        reverse=True,
    )
    peak = peak_candidates[0] if peak_candidates else None
    similar_candidates = [(row, _similarity(summary, row)) for row in siblings[:40]]
    similar_candidates.sort(key=lambda item: item[1], reverse=True)
    similar = similar_candidates[0][0] if similar_candidates else None

    result: dict[str, Any] = {}
    if previous:
        result["previous"] = _compare_summary(summary, previous)
    if peak:
        result["peak"] = _compare_summary(summary, peak)
    if similar:
        block = _compare_summary(summary, similar)
        block["score"] = round(_similarity(summary, similar), 2)
        result["similar"] = block
    return result


def _focused_recorder_commands(summary: dict[str, Any], focus_gate: str | None) -> list[str]:
    run_id = str(summary.get("run_id") or "<run_id>")
    gates = {focus_gate} if focus_gate else set()
    gates.update(str(g) for g in (summary.get("false_gates") or []))
    commands: list[str] = []
    if gates & PICKUP_GATES:
        commands.append(f"python3 scripts/analyze_runs.py recorder {run_id} --item --every 10")
        commands.append(f"python3 scripts/analyze_runs.py recorder {run_id} --timeline auth_visible_world_count")
    if focus_gate == "respawn_presentation_oscillation" or "respawn_presentation_oscillation" in gates:
        commands.append(f"python3 scripts/analyze_runs.py respawn-window {run_id}")
        commands.append(f"python3 scripts/analyze_runs.py metrics {run_id} --lag")
    if gates & LAG_FAMILY_GATES:
        commands.append(f"python3 scripts/analyze_runs.py metrics {run_id} --lag")
        lag_focus_gate = None
        if focus_gate in LAG_FAMILY_GATES:
            lag_focus_gate = focus_gate
        else:
            for gate in (summary.get("false_gates") or []):
                gate_name = str(gate)
                if gate_name in LAG_FAMILY_GATES:
                    lag_focus_gate = gate_name
                    break
        if lag_focus_gate:
            commands.append(f"python3 scripts/analyze_runs.py trend {lag_focus_gate} --json")
            lag_focus_fl = _gate_fl(lag_focus_gate)
            if lag_focus_fl and lag_focus_fl != "FL-1713":
                commands.append(f"python3 scripts/analyze_runs.py fl code {lag_focus_fl} --json")
                commands.append(f"python3 scripts/analyze_runs.py callgraph --fl {lag_focus_fl}")
        commands.append("python3 scripts/analyze_runs.py fl code FL-1713 --json")
        commands.append("python3 scripts/analyze_runs.py callgraph --fl FL-1713")
    return commands


def _false_gate_groups(summary: dict[str, Any], timelines: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {
        "actionable": [],
        "diagnostic": [],
        "deprecated": [],
    }
    for gate in _ordered_false_gates(summary):
        groups[_gate_kind(gate)].append(_gate_item(gate, timelines.get(gate)))
    return groups


def build_operator_help(summary: dict[str, Any]) -> dict[str, Any]:
    run_id = str(summary.get("run_id") or "<run_id>")
    runtime_passed = summary.get("runtime_required_passed")
    runtime_total = summary.get("runtime_required_total")
    controller_mode = summary.get("controller_mode")
    failure_class = summary.get("failure_class")
    focus_gate = _choose_focus_gate(summary)
    family_fl = _family_fl(summary)
    false_gates = _ordered_false_gates(summary)
    timelines, timeline_warning = _scan_gate_timelines(summary, false_gates)
    focus = _gate_item(focus_gate, timelines.get(focus_gate)) if focus_gate else None
    comparisons = _comparison_blocks(summary)
    next_commands = [
        f"python3 scripts/analyze_runs.py show {run_id}",
        f"python3 scripts/analyze_runs.py recorder {run_id} --fields",
        f"python3 scripts/analyze_runs.py fl family {family_fl}",
    ]
    for label in ("previous", "peak", "similar"):
        command = (comparisons.get(label) or {}).get("command")
        if command and command not in next_commands:
            next_commands.append(command)
    for command in _focused_recorder_commands(summary, focus_gate):
        if command not in next_commands:
            next_commands.append(command)

    return {
        "format_version": 1,
        "focus_gate": focus,
        "interpretation": {
            "fail": "FAIL means at least one gate/prelaunch condition failed; it is not automatically a visual or gameplay crash.",
            "failure_none": "failure: None means no classified infrastructure/game crash; inspect false and null gates.",
            "runtime_required": (
                f"runtime req: {runtime_passed}/{runtime_total} means required gates passed over total required gates."
            ),
            "runtime_product": (
                f"runtime product: {summary.get('runtime_product') or 'unknown'} on "
                f"{summary.get('runtime_map') or 'unknown map'}; Classic is the repo "
                "default and Current must be explicit."
            ),
            "false": "false means the analyzer observed a contradiction.",
            "null": "null means insufficient or not-applicable evidence; it is not a pass.",
            "controller_off": "controller mode=off means passive/recorder-only; no scripted input owner was active.",
        },
        "failure_class": failure_class,
        "controller_mode": controller_mode,
        "runtime_product": summary.get("runtime_product"),
        "runtime_map": summary.get("runtime_map"),
        "false_gate_groups": _false_gate_groups(summary, timelines),
        "timeline_warning": timeline_warning,
        "comparisons": comparisons,
        "top_false_gates": _top_gates(summary, "false_gates"),
        "top_null_gates": _top_gates(summary, "null_gates"),
        "next_commands": next_commands,
    }


def _gate_label(item: dict[str, Any]) -> str:
    fl = item.get("fl")
    return f"{item.get('gate')} [{fl}]" if fl else str(item.get("gate"))


def _timeline_summary(timeline: dict[str, Any]) -> str:
    if not timeline:
        return "timeline: no per-sample recorder field found"
    if timeline.get("bad_samples"):
        window = timeline.get("bad_window") or "unknown time"
        samples = timeline.get("bad_samples")
        tabs = ",".join(timeline.get("tabs") or []) or "-"
        return f"false-valued samples: {samples} at {window} tabs={tabs}"
    if timeline.get("present_samples"):
        window = timeline.get("present_window") or "unknown time"
        counts = timeline.get("value_counts") or {}
        count_text = ", ".join(f"{k}={v}" for k, v in counts.items()) or "none"
        return f"field present at {window}; values {count_text}; aggregate analyzer still marked gate false"
    gate = str(timeline.get("gate") or "")
    run_id = str(timeline.get("run_id") or "<run_id>")
    fields = _expected_source_text(gate)
    if timeline.get("scan_failed"):
        # Scan never ran (no artifact_path or early read error): name expected
        # fields so the operator knows what to look for once an artifact exists.
        return (
            "recorder scan unavailable (no artifact path or read error); "
            f"expected source fields: {fields}"
        )
    command = f"python3 scripts/analyze_runs.py recorder {run_id} --fields"
    return (
        "derived gate not emitted as a raw recorder field; "
        f"expected source fields: {fields}; inspect: {command}"
    )


def _clr(text: str, style: str, color: bool) -> str:
    """Apply cli_style styling when color is enabled and cli_style is available."""
    if not color or not _HAS_CLI_STYLE:
        return text
    return _cs.style(text, style)


def _append_gate_lines(lines: list[str], item: dict[str, Any], color: bool = False) -> None:
    kind = item.get("kind", "actionable")
    label_style = "fail" if kind == "actionable" else ("warn" if kind == "diagnostic" else "dim")
    label = _clr(_gate_label(item), label_style, color)
    desc = _clr(item["description"], "dim", color)
    lines.append(f"    - {label}: {desc}")
    timeline = item.get("timeline") or {}
    tl_text = _clr(_timeline_summary(timeline), "dim", color)
    lines.append(f"      {tl_text}")
    evidence = timeline.get("evidence_text")
    if evidence:
        rel = _format_s(timeline.get("evidence_rel_s"))
        prefix = f"evidence @{rel}" if rel else "evidence"
        lines.append(f"      {_clr(prefix + ': ' + evidence, 'dim', color)}")
    # Emitter location + decoupling status
    emitter = item.get("emitter")
    if emitter:
        loc = f"{emitter['file']}:{emitter['line']}"
        coupling = emitter.get("coupling") or "clean"
        law = emitter.get("coupling_law") or ""
        reason = emitter.get("coupling_reason") or ""
        if coupling == "clean":
            lines.append(f"      {_clr('emitter: ' + loc, 'dim', color)}")
        elif coupling == "debug_allowed":
            tag = _clr(f"[debug-coupling-allowed{': ' + law if law else ''}]", "warn", color)
            lines.append(f"      {_clr('emitter: ' + loc, 'dim', color)} {tag}")
            if reason:
                lines.append(f"        {_clr(reason, 'dim', color)}")
        else:  # violation
            tag = _clr(f"[COUPLING VIOLATION{': ' + law if law else ''}]", "fail", color)
            lines.append(f"      {_clr('emitter: ' + loc, 'dim', color)} {tag}")
            if reason:
                lines.append(f"        {_clr(reason, 'dim', color)}")


def format_operator_help(help_data: dict[str, Any], color: bool = False) -> list[str]:
    """Format operator help as a list of human-readable lines.

    Pass ``color=True`` to apply ANSI styling (requires cli_style on the path).
    The ``build_operator_help`` dict itself is always plain text / JSON-safe.
    """
    interp = help_data.get("interpretation") or {}
    lines: list[str] = []

    # Focus gate — the single highest-priority failing gate
    focus = help_data.get("focus_gate")
    if focus:
        lines.append(_clr("  Focus gate:", "subheader", color))
        _append_gate_lines(lines, focus, color=color)

    # Compact run context line (replaces the old verbose "Meaning:" boilerplate)
    runtime_req = interp.get("runtime_required") or ""
    controller = interp.get("controller_off") or ""
    warning = help_data.get("timeline_warning")
    context_parts = [p for p in [runtime_req, controller] if p]
    if context_parts:
        lines.append("  " + _clr("  ".join(context_parts), "dim", color))
    if warning:
        lines.append("  " + _clr(f"note: {warning} — use recorder commands below for raw details.", "warn", color))

    # Failed gates grouped by kind
    groups = help_data.get("false_gate_groups") or {}
    grouped_false = any(groups.get(name) for name in ("actionable", "diagnostic", "deprecated"))
    if grouped_false:
        lines.append(_clr("  Failed gates:", "subheader", color))
        for label, key, style in (
            ("Actionable false", "actionable", "fail"),
            ("Diagnostic false", "diagnostic", "warn"),
            ("Deprecated false", "deprecated", "dim"),
        ):
            items = groups.get(key) or []
            if not items:
                continue
            header = _clr(f"    {label} ({len(items)}):", style, color)
            lines.append(header)
            for item in items:
                _append_gate_lines(lines, item, color=color)
    else:
        false_gates = help_data.get("top_false_gates") or []
        lines.append(_clr("  Top false gates:", "subheader", color))
        if false_gates:
            for item in false_gates:
                label = _clr(item["gate"], "fail", color)
                desc = _clr(item["description"], "dim", color)
                lines.append(f"    - {label}: {desc}")
        else:
            lines.append("    - none")

    # Null gates — brief with descriptions
    null_gates = help_data.get("top_null_gates") or []
    if null_gates:
        lines.append(_clr("  Null gates (insufficient evidence):", "subheader", color))
        for item in null_gates:
            label = _clr(item["gate"], "warn", color)
            desc = _clr(item["description"], "dim", color)
            lines.append(f"    - {label}: {desc}")

    # Comparison context: previous / peak / most-similar runs
    comparisons = help_data.get("comparisons") or {}
    if comparisons:
        lines.append(_clr("  Run comparisons:", "subheader", color))
        for label, title in (
            ("previous", "Previous comparable"),
            ("peak", "Peak comparable"),
            ("similar", "Most similar failing"),
        ):
            block = comparisons.get(label) or {}
            run_id = block.get("run_id")
            if not run_id:
                continue
            score = block.get("score")
            score_text = f" similarity={score}" if score is not None else ""
            lines.append(
                f"    - {_clr(title, 'subheader', color)}: {run_id}"
                f" req={block.get('runtime_required')}"
                f" false={block.get('false_count')} null={block.get('null_count')}{score_text}"
            )
            shared = _short_list(block.get("shared_false") or [])
            lines.append(f"      shared false: {_clr(shared, 'warn', color)}")
            new_bad = _short_list(block.get("new_bad") or [])
            if new_bad and new_bad != "-":
                lines.append(f"      {_clr('new/regressed now:', 'fail', color)} {new_bad}")
            recovered = _short_list(block.get("recovered_bad") or [])
            if recovered and recovered != "-":
                lines.append(f"      {_clr('recovered vs that run:', 'ok', color)} {recovered}")
            lines.append(f"      compare: {_clr(str(block.get('command') or ''), 'dim', color)}")

    # Pasteable commands
    lines.append(_clr("  Next commands:", "subheader", color))
    for command in help_data.get("next_commands") or []:
        lines.append(f"    - {_clr(command, 'dim', color)}")
    return lines
