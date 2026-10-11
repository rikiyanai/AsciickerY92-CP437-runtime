# Multiplayer Visual Watchdog Report

- Time: 2026-10-10T19:51:52.345Z
- Result: FAIL
- Runtime-required gates: 29/45
- Runtime-core gates: 26/39
- Manifest gate: true
- Input mode: observe
- Mobile controller input: inactive
- Active input owner: controller_recipe
- Bug verdicts: dead_label_only=false standing_corpse_state_signature=false auth_item_regressed=false auth_item_parity_mismatch=false jitter_regressed=false jitter_signal=false stale_snapshot_rejected=false position_reorder_rejected=false
- Visual signoff required: true
- Bundles: A_movement=false B_items=true C_combat=true D_npc_death=true all_pass=false
- Local position surface: visual

| Gate | Result |
|---|---|
| scenario_mounted_visibility_observed | FAIL |
| gameplay_mounted_exact_assembly_observed | FAIL |
| gameplay_mounted_addability_fixture_observed | FAIL |
| gameplay_mounted_no_generic_stack_observed | FAIL |
| gameplay_mounted_visual_stability_ok | FAIL |
| scenario_mounted_attack_death_observed | FAIL |
| scenario_crossbow_ranged_authority_observed | FAIL |
| gameplay_unsupported_combination_rejected | FAIL |
| live_manifest_match | PASS |
| machine_health_pass | FAIL |
| bundle_hash_match_ok | FAIL |
| ids_lock_hash_match_ok | FAIL |
| actor_bundle_load_ok | PASS |
| render_selector_contract_match_ok | FAIL |
| world_item_visual_contract_match_ok | FAIL |
| inventory_visual_contract_match_ok | FAIL |
| equip_visual_contract_match_ok | PASS |
| server_blood_decal_coverage_ok | FAIL |
| scenario_attack_lane_scope_ok | FAIL |
| evidence_attack_animation_surface_observed | FAIL |
| scenario_attack_attachment_integrity_ok | FAIL |
| evidence_death_animation_surface_observed | FAIL |
| gameplay_dead_anim_frame_advancing_when_clamped_absent | FAIL |
| gameplay_dead_anim_wrong_family_absent | FAIL |
| gameplay_npc_live_anim_advancing_ok | FAIL |
| evidence_npc_attachment_source_visible_ok | FAIL |
| gameplay_npc_live_wrong_family_absent | PASS |
| evidence_npc_corpse_visibility_observed | FAIL |
| scenario_npc_corpse_survives_hold | FAIL |
| gameplay_npc_dead_anim_wrong_family_absent | FAIL |
| ws_tcp_ready | PASS |
| http_ready | PASS |
| ws_stable_tab1 | PASS |
| ws_stable_tab2 | PASS |
| tick_cadence_ok | PASS |
| bootstrap_state_sane_tab1 | PASS |
| bootstrap_state_sane_tab2 | PASS |
| bootstrap_state_sane_bidirectional | PASS |
| tab1_world_ready | PASS |
| tab2_world_ready | PASS |
| load_both_world | PASS |
| movement_intent_nonzero_tab1 | PASS |
| movement_intent_nonzero_tab2 | PASS |
| movement_intent_nonzero_bidirectional | PASS |
| facing_intent_nonzero_tab1 | FAIL |
| facing_intent_nonzero_tab2 | FAIL |
| facing_intent_nonzero_bidirectional | FAIL |
| evidence_pair_ready | FAIL |
| remote_pair_ready | FAIL |
| tab1_motion_progress | FAIL |
| tab2_motion_progress | FAIL |
| tab1_jitter_ok | FAIL |
| tab2_jitter_ok | FAIL |
| local_ack_base_parity_ok | FAIL |
| local_replay_post_pending_parity_ok | FAIL |
| local_prediction_quantization_parity_ok | FAIL |
| local_contact_parity_ok | FAIL |
| local_correction_budget_ok | FAIL |
| local_replay_queue_health_ok | FAIL |
| local_ack_seq_monotonic_ok | FAIL |
| tab1_z_jitter_ok | FAIL |
| tab2_z_jitter_ok | FAIL |
| tab1_snapback_absent | FAIL |
| tab2_snapback_absent | FAIL |
| remote_observer_ready_tab1 | FAIL |
| remote_observer_ready_tab2 | FAIL |
| remote_observer_ready_bidirectional | FAIL |
| local_body_visible_tab1 | FAIL |
| local_body_visible_tab2 | FAIL |
| remote_visible_tab1 | FAIL |
| remote_visible_tab2 | FAIL |
| remote_visible_bidirectional | FAIL |
| position_sync_tab1_to_tab2 | FAIL |
| position_sync_tab2_to_tab1 | FAIL |
| position_sync_bidirectional | FAIL |
| camera_qe_jitter_ok | FAIL |
| pickup_signal_observed | FAIL |
| pickup_map_item_authorship_observed | FAIL |
| pickup_server_world_item_observed | FAIL |
| pickup_client_rendered_world_item_observed | FAIL |
| pickup_intent_observed | FAIL |
| pickup_ownership_transition_observed | FAIL |
| pickup_visible_legitimate_chain_observed | FAIL |
| combat_attack_input_observed | FAIL |
| combat_signal_observed | FAIL |
| combat_swing_broadcast | FAIL |
| npc_combat_signal_observed | FAIL |
| death_signal_observed | FAIL |
| death_pvp_confirmed | FAIL |
| death_npc_confirmed | FAIL |
| fallthrough_absent | FAIL |
| npc_not_overlapping_local | PASS |
| npc_anim_while_moving_ok | FAIL |
| artifacts_present | PASS |
| phase_sample_coherence_ok | FAIL |
| npc_proximity_ok | FAIL |
| npc_hp_drop_observed | FAIL |
| local_snapshot_age_guard_observed | FAIL |
| local_position_reorder_guard_observed | FAIL |
| snap_frequency_ok | FAIL |
| position_alignment_ok | FAIL |
| lag_spike_count_ok | PASS |
| lag_yellow_spike_budget_ok | PASS |
| local_controlled_movement_smooth | FAIL |
| lag_stable_at_respawn | FAIL |
| respawn_presentation_oscillation | FAIL |
| post_join_lag_spike_count_ok | PASS |
| post_join_lag_stable | FAIL |
| lag_floor_p95_ok | FAIL |
| lag_floor_p95_steady_ok | FAIL |
| terrain_dark_runtime_observed | PASS |
| gameplay_spawn_physics_ready_ok | FAIL |
| gameplay_minimap_remote_dot_visible_ok | FAIL |
| gameplay_remote_floor_support_ok | FAIL |
| remote_interpolation_smooth | FAIL |
| server_log_swing_recv_ok | FAIL |
| join_opcode_ok_tab1 | PASS |
| join_opcode_ok_tab2 | PASS |
| ws_endpoint_correct_tab1 | PASS |
| ws_endpoint_correct_tab2 | PASS |
| bootstrap_nshot_pass | PASS |
| appearance_contract_match_ok | FAIL |
| appearance_sentinel_absent | PASS |
| passive_hold_no_mutation_ok | FAIL |
| pickup_inventory_transition_observed | FAIL |
| equip_swap_clear_drop_observed | FAIL |
| render_plan_exact_proof_health_ok | PASS |
| pickup_any_transition_observed | FAIL |

## Position Surface Policy

- selected=visual
- legacy_field=local_pos_*

## Camera Q/E Jitter

- gate=null
- active=false
- active_tabs=-
- inactive_tabs=tab1,tab2
- coverage_complete=false
- failure_reason=no_active_tabs
- tab1: inactive (no_rotate_only_window)
- tab2: inactive (no_rotate_only_window)

## Artifacts
- phase_snapshot: /tmp/watchdog-vps-manual-20261010-194939/watchdog_phase.json
- phase_history: /tmp/watchdog-vps-manual-20261010-194939/watchdog_phase_history.json
- phase_history_jsonl: /tmp/watchdog-vps-manual-20261010-194939/watchdog_phase_history.jsonl
- recorder_trace: /tmp/watchdog-vps-manual-20261010-194939/recorder/recording.jsonl
- recorder_meta: /tmp/watchdog-vps-manual-20261010-194939/recorder/recording_meta.json
- ansi_capture_dir: /tmp/watchdog-vps-manual-20261010-194939/ansi_frames
- ansi_world_ready: [object Object]