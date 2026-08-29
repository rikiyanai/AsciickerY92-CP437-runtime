# # CLI / Console Output Style Guide
#
# Status: active repo-wide UX requirement / implemented incrementally
#
# Purpose: every script a user or operator may need to run should explain what is happening, what failed, and what to do next without requiring source-code archaeology. This guide is the required target style for Python, shell, Node, and helper CLIs in this repo.
#
# ## Scope
#
# This applies to user-facing and operator-facing console output from:
#
# - launchers and setup scripts
# - watchdog, preflight, deploy, reset, promote, and analyze scripts
# - map, editor, OSM, Blender, and asset-pipeline scripts
# - diagnostic scripts that a maintainer may run directly
#
# Machine-readable output is separate. JSON, JSONL, CSV, and protocol payloads must stay uncolored and schema-stable.
#
# ## Color Contract
#
# Color must be additive and disabled when output is not a TTY, `NO_COLOR` is set, `TERM=dumb`, or the script is in a JSON-only/machine mode.
#
# Required styles:
#
# | Surface | Style |
# |---|---|
# | Major headers, including `=== ... ===` and pure `====` rules | bold yellow |
# | Subheaders, including `--- Phase ... ---` | bold cyan |
# | Success states: `OK`, `PASS`, `SUCCESS`, completed operation | bold green |
# | Warnings, skipped work, next action, recovery prompts | bold yellow |
# | Dangerous prompts, `FAILED`, `FATAL`, prelaunch blockers, errors | bold red |
# | File paths, artifact paths, URLs, summary paths | cyan |
# | Commands printed for copying | dim or plain; never red unless unsafe |
# | Counts and numeric summaries | bold white |
#
# Do not use color as the only carrier of meaning. The text must still be readable in plain output.
#
# ## Required Layout
#
# Long-running scripts should print:
#
# 1. A start card: command purpose, target, mode, key config, and whether it may mutate state.
# 2. Numbered or named phases.
# 3. A heartbeat or progress line for long silent operations.
# 4. A final consolidated summary as the last human-visible block.
# 5. A `next action` line on failure or partial completion.
# 6. Artifact paths and machine-summary paths where applicable.
#
# Example:
#
# ```text
# === Candidate Runtime Reset ===
#   target:      candidate
#   ssh:         r@35.226.113.14
#   mode:        server+web
#
# --- Phase 1: Build web locally ---
#   Running: bash build-web.sh
#
# === Final Summary ===
#   result:      FAILED
#   stage:       deploy
#   summary:     artifacts/...
#   next action: Fix the listed prelaunch blocker, then rerun the canonical command.
# ```
#
# ## Prompt Rules
#
# Prompts that may stage, commit, reset, deploy, delete, overwrite, or force-add ignored files must be conspicuous.
#
# Required prompt examples:
#
# ```text
# WORKTREE DIRTY OR RUNTIME IDENTITY PREFLIGHT BLOCKED. COMMIT ALL AND RESET? (Y/N)
# UNTRACKED FILES ARE PRESENT. INCLUDE THEM IN THE WATCHDOG COMMIT? (Y/N)
# IGNORED FILES ARE PRESENT. FORCE-ADD THEM WITH git add -f? (Y/N)
# ```
#
# The untracked and ignored-file prompts are danger prompts and must render bold red when color is enabled.
#
# ## Machine Mode Rules
#
# Scripts with `--json`, `--json-only`, `--summary-json`, or equivalent output modes must not print human color/control codes into the machine stream.
#
# For mixed human and machine output:
#
# - stdout owns the requested machine payload.
# - stderr may carry human progress when needed.
# - JSON-only stdout must remain parseable as exactly one JSON object where that is the documented contract.
#
# ## Adoption Requirement
#
# New or touched user-facing scripts must follow this guide. Existing scripts may migrate incrementally, but ship-readiness requires the important first-run, setup, launcher, deploy, watchdog, analyzer, map/editor, and asset-pipeline scripts to conform.
#
# Any script that cannot conform yet must document why in the canon spec or failure log and must not be presented as polished ship-ready UX.
#
# ---
#
# ## Direct-Run Script Inventory — Step 12 Classification (2026-04-22)
#
# Full audit: `/tmp/claude-step12-cli-ux-audit-20260422.md`
# Canon spec cross-reference: § Step 12 - CLI / Console UX Scope and Classification
# Total scripts in repo: 316 — all classified below.
#
# ### GROUP A — Watchdog / Deploy / Runtime Control
#
# | Script | Class |
# |---|---|
# | `scripts/watchdog_run_canonical.py` | style-compliant (gold standard) |
# | `scripts/simplified_watchdog_vps_launcher.py` | style-compliant |
# | `scripts/simplified_watchdog_preflight.py` | style-compliant |
# | `scripts/watchdog_recipe_runner.py` | style-compliant |
# | `scripts/deploy_candidate_server.py` | style-compliant |
# | `scripts/deploy_candidate_web.py` | style-compliant |
# | `scripts/deploy_current_server.py` | style-compliant |
# | `scripts/promote_candidate_to_current.py` | style-compliant |
# | `scripts/reset_candidate_runtime.py` | style-compliant |
# | `scripts/reset_current_runtime.py` | style-compliant |
# | `scripts/watchdog_operator_help.py` | machine-mode-only |
# | `scripts/watchdog_deploy_constants.py` | machine-mode-only |
# | `scripts/watchdog_recipe_store.py` | machine-mode-only |
# | `scripts/watchdog_run_current.py` | machine-mode-only (deprecated redirect) |
# | `scripts/watchdog_run_live_host.py` | machine-mode-only (deprecated redirect) |
# | `scripts/watchdog_run_candidate_live.py` | machine-mode-only (deprecated redirect) |
# | `scripts/watchdog_preflight.py` | machine-mode-only (compatibility wrapper) |
# | `scripts/watchdog_source.py` | machine-mode-only |
# | `scripts/watchdog/constants.py` | machine-mode-only (generated constants) |
# | `scripts/watchdog/tests/test_slot_admin.py` | machine-mode-only (test harness) |
# | `scripts/watchdog_trust_audit.py` | machine-mode-only (restored real enforcement; TRUSTED/DRIFT/REGRESSION canary) |
# | `scripts/watchdog_vps_launcher.py` | retired/non-authoritative (deprecated wrapper) |
# | **`scripts/trace_candidate_lag.py`** | **OPEN / USER-UX** |
# | **`scripts/watchdog_remote_slot_admin.py`** | **OPEN / USER-UX** |
#
# ### GROUP B — Analyze / Verify / Inspect / Code Tools
#
# | Script | Class |
# |---|---|
# | `scripts/analyze_failure_log.py` | style-compliant |
# | `scripts/analyze_runs.py` | style-compliant |
# | `scripts/game_source.py` | style-compliant |
# | `scripts/inspect_a3d.py` | style-compliant |
# | `scripts/plan_lint.py` | style-compliant |
# | `scripts/gsd_audit.py` | style-compliant |
# | `scripts/multiplayer_canon_guard.py` | style-compliant |
# | `scripts/phase_driver_gate.py` | style-compliant |
# | `scripts/verify_candidate_web.py` | machine-mode-only |
# | `scripts/verify_instances.py` | machine-mode-only |
# | `scripts/verify_report.py` | machine-mode-only |
# | `scripts/verify_roundtrip.py` | machine-mode-only |
# | `scripts/verify_corpus_checksums.py` | machine-mode-only |
# | `scripts/verify_collision_alpha.py` | machine-mode-only |
# | `scripts/ci_corpus_check.py` | machine-mode-only |
# | `scripts/ci_real_asset_policy.py` | machine-mode-only |
# | `scripts/generate_fixture_report.py` | machine-mode-only |
# | `scripts/asciicker_constants.py` | machine-mode-only (constants module) |
# | `scripts/color_quantizer.py` | machine-mode-only (library) |
# | `scripts/glyph_matcher.py` | machine-mode-only (library) |
# | `scripts/processor_core.py` | machine-mode-only (library) |
# | `scripts/semantic_analyzer.py` | machine-mode-only (library) |
# | `scripts/extract_backlog.py` | machine-mode-only (library) |
# | `scripts/extract_comments.py` | machine-mode-only (library) |
# | `scripts/verify_tk.py` | retired/non-authoritative (empty) |
# | `scripts/verify_xp_mcp.py` | retired/non-authoritative (test harness) |
# | `scripts/verify.py` | retired/non-authoritative (legacy) |
# | `scripts/analyze_colors.py` | retired/non-authoritative |
# | **`scripts/inspect_terrain.py`** | **OPEN / USER-UX** |
# | **`scripts/compare_images.py`** | **OPEN / USER-UX** |
# | **`scripts/swarm_audit.py`** | **OPEN / USER-UX** |
# | **`scripts/validate_a3d.py`** | **OPEN / USER-UX** |
# | **`scripts/validate_blosm_mesh.py`** | **OPEN / USER-UX** |
# | **`scripts/verify_e2e.py`** | **OPEN / USER-UX** |
# | **`scripts/cflow_annotate.py`** | **OPEN / USER-UX** |
# | **`scripts/ascii_research_inventory.py`** | **OPEN / USER-UX** |
# | **`scripts/asks_extractor_enhanced.py`** | **OPEN / USER-UX** |
# | **`scripts/comment_pattern_analyzer.py`** | **OPEN / USER-UX** |
# | **`scripts/reference_linker.py`** | **OPEN / USER-UX** |
#
# ### GROUP C — OSM / Map / Terrain / A3D / Asset Creation
#
# | Script | Class |
# |---|---|
# | `scripts/attach_sbu_assets.py` | style-compliant |
# | `scripts/web_preload_assets.py` | style-compliant |
# | `scripts/process_blosm.py` | machine-mode-only |
# | `scripts/create_test_map_with_instances.py` | machine-mode-only |
# | `scripts/visualize_ascii_dims.py` | machine-mode-only |
# | `scripts/make_multiplayer_visual_composites.py` | machine-mode-only |
# | `scripts/create_ai_fixture.py` | machine-mode-only |
# | `scripts/create_blender_fixture.py` | machine-mode-only |
# | `scripts/create_multi_angle_fixture.py` | machine-mode-only |
# | `scripts/create_demo_xp.py` | machine-mode-only |
# | `scripts/create_test_sprite.py` | machine-mode-only |
# | `scripts/generate_demo_asset.py` | machine-mode-only |
# | `scripts/gen_asset.py` | machine-mode-only |
# | `scripts/bake_tex_to_vcol.py` | machine-mode-only (Blender library) |
# | `scripts/cleanup_and_bake_vcol.py` | machine-mode-only (Blender library) |
# | `scripts/3d_gmap_tile_cleanupper.py` | retired/non-authoritative (legacy GPL Blender script) |
# | `scripts/sbu_e2e_run.py` | style-compliant |
# | **`scripts/sbu_sac_verify_run.py`** | **OPEN / USER-UX — TIER 1 / SHIP-BLOCKER** |
# | `scripts/sbu_verify_building.py` | style-compliant |
# | `scripts/gen_minimal_a3d.py` | style-compliant |
# | **`scripts/debug_a3d_reader.py`** | **OPEN / USER-UX** |
# | **`scripts/setup_addon.py`** | **OPEN / USER-UX** |
# | **`scripts/convert_automat.py`** | **OPEN / USER-UX** |
# | **`scripts/enable_shader_debug.py`** | **OPEN / USER-UX** |
# | **`scripts/asset_maker.py`** | **OPEN / USER-UX** |
# | **`scripts/isometric_map_prototype.py`** | **OPEN / USER-UX** |
# | **`scripts/isometric_tile_prototype.py`** | **OPEN / USER-UX** |
#
# ### GROUP D — Asset Pipeline
#
# | Script | Class |
# |---|---|
# | `scripts/pipeline/cli.py` | style-compliant |
# | `scripts/pipeline/__main__.py` | style-compliant (delegates to cli.py) |
# | `scripts/compile_actor_visual_profiles.py` | machine-mode-only |
# | `scripts/pipeline/quality_gates.py` | machine-mode-only |
# | `scripts/pipeline/verification.py` | machine-mode-only |
# | `scripts/pipeline/web_ui/sprite_viewer/viewer_loaders.py` | machine-mode-only |
# | `scripts/pipeline/web_ui/sprite_viewer/frame_sequence_mirror.py` | machine-mode-only |
# | `scripts/pipeline/generate_presentation_overlays.py` | tombstoned; do not restore FL-3991 standalone slot authority |
# | `scripts/pipeline/nanobanana_batch.py` | machine-mode-only |
# | `scripts/pipeline/debug_sheet.py` | machine-mode-only |
# | `scripts/pipeline/xp_browser.py` | machine-mode-only (GUI) |
# | `scripts/pipeline/xp_tool.py` | machine-mode-only (GUI) |
# | `scripts/pipeline/__init__.py` | machine-mode-only (package init) |
# | `scripts/pipeline/_render_core.py` | machine-mode-only (library) |
# | `scripts/pipeline/ai_provider.py` | machine-mode-only (library) |
# | `scripts/pipeline/angle_synthesis.py` | machine-mode-only (library) |
# | `scripts/validate_actor_visual_profiles.py` | machine-mode-only |
# | `scripts/pipeline/assembler.py` | machine-mode-only (library) |
# | `scripts/pipeline/auto_adjust.py` | machine-mode-only (library) |
# | `scripts/pipeline/blender_to_xp.py` | machine-mode-only (library) |
# | `scripts/pipeline/branch_enums.py` | machine-mode-only (library) |
# | `scripts/pipeline/branch_model.py` | machine-mode-only (library) |
# | `scripts/pipeline/branch_thumbnails.py` | machine-mode-only (library) |
# | `scripts/pipeline/canonical_manifest.py` | machine-mode-only (library) |
# | `scripts/pipeline/color_correction.py` | machine-mode-only (library) |
# | `scripts/pipeline/config_schema.py` | machine-mode-only (library) |
# | `scripts/pipeline/console_tui.py` | machine-mode-only (library) |
# | `scripts/pipeline/dispatch.py` | machine-mode-only (library) |
# | `scripts/pipeline/downscale.py` | machine-mode-only (library) |
# | `scripts/pipeline/engine_client.py` | machine-mode-only (library) |
# | `scripts/pipeline/export_service.py` | machine-mode-only (library) |
# | `scripts/pipeline/frame_remap.py` | machine-mode-only (library) |
# | `scripts/pipeline/generator.py` | machine-mode-only (library) |
# | `scripts/pipeline/grid_detect.py` | machine-mode-only (library) |
# | `scripts/pipeline/grid_validator.py` | machine-mode-only (library) |
# | `scripts/pipeline/guidance_manifest.py` | machine-mode-only (library) |
# | `scripts/pipeline/manifest_loader.py` | machine-mode-only (library) |
# | `scripts/pipeline/matcher.py` | machine-mode-only (library) |
# | `scripts/pipeline/mcp_session.py` | machine-mode-only (library) |
# | `scripts/pipeline/mesh_importer.py` | machine-mode-only (library) |
# | `scripts/pipeline/normalize_sheet.py` | machine-mode-only (library) |
# | `scripts/pipeline/palette.py` | machine-mode-only (library) |
# | `scripts/pipeline/pipeline.py` | machine-mode-only (library) |
# | `scripts/pipeline/processor_literal.py` | machine-mode-only (library) |
# | `scripts/pipeline/processor_subcell.py` | machine-mode-only (library) |
# | `scripts/pipeline/processor.py` | machine-mode-only (library) |
# | `scripts/pipeline/prompt_pack.py` | machine-mode-only (library) |
# | `scripts/pipeline/quantizer.py` | machine-mode-only (library) |
# | `scripts/pipeline/reflection_handler.py` | machine-mode-only (library) |
# | `scripts/pipeline/reformatter.py` | machine-mode-only (library) |
# | `scripts/pipeline/schemas.py` | machine-mode-only (library) |
# | `scripts/pipeline/sheet_stitcher.py` | machine-mode-only (library) |
# | `scripts/pipeline/slicer.py` | machine-mode-only (library) |
# | `scripts/pipeline/snap_magenta.py` | machine-mode-only (library) |
# | `scripts/pipeline/sprite_errors.py` | machine-mode-only (library) |
# | `scripts/pipeline/sprite_extract.py` | machine-mode-only (library) |
# | `scripts/pipeline/sprite_invariants.py` | machine-mode-only (library) |
# | `scripts/pipeline/sprite_manifest.py` | machine-mode-only (library) |
# | `scripts/pipeline/sprite_validator.py` | machine-mode-only (library) |
# | `scripts/pipeline/validator.py` | machine-mode-only (library) |
# | `scripts/pipeline/service/adapters.py` | machine-mode-only (library) |
# | `scripts/pipeline/service/asset_service.py` | machine-mode-only (library) |
# | `scripts/pipeline/service/config_resolver.py` | machine-mode-only (library) |
# | `scripts/pipeline/service/constants.py` | machine-mode-only (library) |
# | `scripts/pipeline/service/job.py` | machine-mode-only (library) |
# | `scripts/pipeline/service/manifest.py` | machine-mode-only (library) |
# | `scripts/pipeline/service/slicing.py` | machine-mode-only (library) |
# | `scripts/pipeline/xp_core.py` | machine-mode-only (library) |
# | `scripts/pipeline/xp_viewer.py` | machine-mode-only (library) |
# | `scripts/pipeline/web_api/app.py` | machine-mode-only (Flask factory) |
# | `scripts/pipeline/web_api/routes.py` | machine-mode-only (Flask blueprint) |
# | `scripts/pipeline/web_api/workbench_session.py` | machine-mode-only (library) |
# | `scripts/pipeline/tui/app.py` | machine-mode-only (Textual app) |
# | `scripts/pipeline/tui/state.py` | machine-mode-only (library) |
# | `scripts/pipeline/tui/screens/analyze.py` | machine-mode-only (Textual screen) |
# | `scripts/pipeline/tui/screens/configure.py` | machine-mode-only (Textual screen) |
# | `scripts/pipeline/tui/screens/result.py` | machine-mode-only (Textual screen) |
# | `scripts/pipeline/tui/screens/run.py` | machine-mode-only (Textual screen) |
# | `scripts/pipeline/tui/screens/welcome.py` | machine-mode-only (Textual screen) |
# | `scripts/pipeline/tui/widgets/bg_panel.py` | machine-mode-only (Textual widget) |
# | `scripts/pipeline/tui/widgets/breadcrumb.py` | machine-mode-only (Textual widget) |
# | `scripts/pipeline/tui/widgets/slicing_panel.py` | machine-mode-only (Textual widget) |
# | `scripts/pipeline/templates/loader.py` | machine-mode-only (library) |
# | `scripts/pipeline/templates/models.py` | machine-mode-only (library) |
# | `scripts/pipeline/templates/schemas.py` | machine-mode-only (library) |
# | `scripts/pipeline/wizard/availability.py` | machine-mode-only (library) |
# | `scripts/pipeline/wizard/engine.py` | machine-mode-only (library) |
# | `scripts/pipeline/wizard/errors.py` | machine-mode-only (library) |
# | `scripts/pipeline/wizard/intents.py` | machine-mode-only (library) |
# | `scripts/pipeline/wizard/navigation.py` | machine-mode-only (library) |
# | `scripts/pipeline/wizard/result.py` | machine-mode-only (library) |
# | `scripts/pipeline/wizard/validation.py` | machine-mode-only (library) |
# | `scripts/pipeline/xp_cat.py` | retired/non-authoritative |
# | `scripts/pipeline/diagnose_xp.py` | retired/non-authoritative |
# | `scripts/pipeline/debug_sprite.py` | retired/non-authoritative |
# | `scripts/pipeline/preview_skin.py` | retired/non-authoritative |
# | **`scripts/xp_to_meta.py`** | **OPEN / USER-UX** |
# | **`scripts/xp_to_png.py`** | **OPEN / USER-UX** |
# | **`scripts/pipeline/recolor_skin_family.py`** | **OPEN / USER-UX** |
# | **`scripts/pipeline/recolor_wearables.py`** | **OPEN / USER-UX** |
# | **`scripts/pipeline/generate_geom_test_fixtures.py`** | **OPEN / USER-UX** |
# | **`scripts/pipeline/web_ui/generate_cp437_atlas.py`** | **OPEN / USER-UX** |
# | **`scripts/pipeline/xp_anim_viewer.py`** | **OPEN / USER-UX** |
#
# ### GROUP E — Testing
#
# | Script | Class |
# |---|---|
# | `scripts/test_xp_core.py` | style-compliant |
# | `scripts/test_render_row_offset.py` | style-compliant |
# | `scripts/run_perf_compare.py` | style-compliant |
# | `scripts/test_blender.py` | machine-mode-only |
# | `scripts/test_blender_pipeline.py` | machine-mode-only |
# | `scripts/test_blender_gmap_bake.py` | machine-mode-only |
# | `scripts/test_blender_gmap_project.py` | machine-mode-only |
# | `scripts/test_engine_render.py` | machine-mode-only |
# | `scripts/test_engine_render_fast.py` | machine-mode-only |
# | `scripts/test_bridge_socket.py` | machine-mode-only |
# | `scripts/test_live_bridge.py` | machine-mode-only |
# | `scripts/test_mcp_connection.py` | machine-mode-only |
# | `scripts/test_pipeline_e2e.py` | machine-mode-only |
# | `scripts/maintainer/tests/test_audit.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_browser_semantic.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_claim_guard.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_claim_vocabulary.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_cleaner_apply.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_cleaner_scan.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_command_handoff_hook.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_failure_log.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_install_hooks.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_jsonl_parser.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_report_schema.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_search_pattern_guard.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_session_end_hook.py` | machine-mode-only (pytest) |
# | `scripts/maintainer/tests/test_startup_gate_hook.py` | machine-mode-only (pytest) |
# | `scripts/blender/test_render_unified.py` | machine-mode-only (pytest) |
# | `scripts/debug_manual.py` | retired/non-authoritative |
# | `scripts/parse_game_output.py` | retired/non-authoritative |
#
# ### GROUP F — Blender Scripts
#
# | Script | Class |
# |---|---|
# | `scripts/blender/blender_preflight.py` | style-compliant |
# | `scripts/blender/walk_anim_tool.py` | style-compliant |
# | `scripts/blender_render.py` | machine-mode-only (library) |
# | `scripts/blender_client.py` | machine-mode-only (library) |
# | `scripts/blender_build_scene.py` | machine-mode-only (Blender context) |
# | `scripts/blender_utils.py` | machine-mode-only (library) |
# | `scripts/blender/render_sprite.py` | machine-mode-only (subprocess bridge) |
# | `scripts/blender/render_unified.py` | machine-mode-only (Blender context) |
# | `scripts/blender/render_turntable.py` | machine-mode-only (subprocess bridge) |
# | `scripts/blender/import_mesh.py` | machine-mode-only (Blender context) |
# | `scripts/blender/render_payload.py` | machine-mode-only (code generator) |
# | `scripts/blender/motion_template.py` | machine-mode-only (library) |
# | `scripts/blender/create_cooker_asset.py` | machine-mode-only (Blender headless) |
# | `scripts/blender/create_test_asset.py` | machine-mode-only (Blender headless) |
#
# ### GROUP G — Maintainer / Conductor / Governance / Hooks
#
# | Script | Class |
# |---|---|
# | `scripts/maintainer/startup_preflight.py` | style-compliant |
# | `scripts/maintainer/cleaner_apply.py` | style-compliant |
# | `scripts/maintainer/goal_sanity.py` | style-compliant |
# | `scripts/maintainer/search_pattern_guard.py` | style-compliant |
# | `scripts/mcp_preflight.py` | style-compliant |
# | `scripts/git_guardrails.py` | style-compliant |
# | `scripts/maintainer/audit_run.py` | machine-mode-only |
# | `scripts/maintainer/janitor_run.py` | machine-mode-only |
# | `scripts/maintainer/claim_guard.py` | machine-mode-only |
# | `scripts/maintainer/install_hooks.py` | machine-mode-only |
# | `scripts/maintainer/install_cli_startup_gate.py` | machine-mode-only |
# | `scripts/maintainer/backfill_gate_stub_overlays.py` | machine-mode-only |
# | `scripts/maintainer/cleaner_scan.py` | machine-mode-only |
# | `scripts/maintainer/cmv_phase_workflow.py` | machine-mode-only |
# | `scripts/maintainer/generate_session_handoffs.py` | machine-mode-only |
# | `scripts/maintainer/run_tests.py` | machine-mode-only (pytest wrapper) |
# | `scripts/maintainer/lib/failure_log.py` | machine-mode-only (library) |
# | `scripts/maintainer/lib/jsonl_parser.py` | machine-mode-only (library) |
# | `scripts/maintainer/lib/report_schema.py` | machine-mode-only (library) |
# | `scripts/maintainer/hooks/claim_guard_content_hook.py` | machine-mode-only (hook harness) |
# | `scripts/maintainer/hooks/claim_guard_hook.py` | machine-mode-only (hook harness) |
# | `scripts/maintainer/hooks/claim_guard_transcript_hook.py` | machine-mode-only (hook harness) |
# | `scripts/maintainer/hooks/command_handoff_hook.py` | machine-mode-only (hook harness) |
# | `scripts/maintainer/hooks/search_pattern_warning_hook.py` | machine-mode-only (hook harness) |
# | `scripts/maintainer/hooks/session_end_hook.py` | machine-mode-only (hook harness) |
# | `scripts/maintainer/hooks/session_start_hook.py` | machine-mode-only (hook harness) |
# | `scripts/maintainer/hooks/skill_gate_hook.py` | machine-mode-only (hook harness) |
# | `scripts/maintainer/hooks/startup_gate_hook.py` | machine-mode-only (hook harness) |
# | `scripts/hooks/code_line_ceiling_guard.py` | machine-mode-only (git hook) |
# | `scripts/hooks/doc_gen.py` | machine-mode-only (git hook) |
# | `scripts/hooks/fl_fix_attempt_gate.py` | machine-mode-only (git hook) |
# | `scripts/hooks/pre-commit-web-ui-phase.py` | machine-mode-only (git hook) |
# | `scripts/hooks/propagation_chain_hook.py` | machine-mode-only (git hook) |
# | `scripts/hooks/r1_r9_enforcement_hook.py` | machine-mode-only (git hook) |
# | `scripts/ralph_fix_hook.py` | machine-mode-only (hook utility) |
#
# ### GROUP H — Launcher Library / Agent Tools / Misc
#
# | Script | Class |
# |---|---|
# | `scripts/agent_fix_metadata.py` | machine-mode-only (async MCP client) |
# | `scripts/agent_logger.py` | machine-mode-only (JSONL logger library) |
# | `scripts/agent_overlay_ascii.py` | machine-mode-only (async MCP client) |
# | `scripts/agent_refine_edit.py` | machine-mode-only (async MCP client) |
# | `scripts/agent_replace_purple.py` | machine-mode-only (async MCP client) |
# | `scripts/knowledge_tracker.py` | machine-mode-only |
# | **`scripts/agent_hook.py`** | **OPEN / USER-UX** |
# | **`scripts/agent_repl.py`** | **OPEN / USER-UX** |
#
# ### OPEN / USER-UX Count by Group
#
# | Group | Count |
# |---|---|
# | A — Watchdog / Deploy | 2 |
# | B — Analyze / Verify / Code Tools | 11 |
# | C — OSM / Map / A3D / Asset Creation | 10 |
# | D — Asset Pipeline | 7 |
# | E — Testing | 0 |
# | F — Blender | 0 |
# | G — Maintainer / Governance / Hooks | 0 |
# | H — Launcher Lib / Agent Tools | 2 |
# | **Total** | **32** |
#
# Ship-gate: OPEN / USER-UX count must reach 0 for all Tier 1 and Tier 2 scripts before promotion to candidate.

"""Shared TTY console style helpers for scripts/*.py CLI tools.

Extracted from watchdog_run_canonical.py (commit 0d46811c) and
console_tui.py color primitives.  Consolidates duplicated ANSI helpers
across 28+ script files into one shared module.

Three layers:
  1. Color primitives — _color_enabled(), _ansi(), named wrappers
  2. Status system    — STATUS_COLORS dict, status(), style()
  3. Table renderer   — table(), kv()

Usage from any scripts/*.py file:
    from cli_style import status, style, table

Usage from docs/agent/cli-anything/ scripts:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from cli_style import status, style, table
"""

from __future__ import annotations

import colorsys
import os
import re
import sys
import time
import threading as _threading
from typing import Any, Callable, Sequence

# ---------------------------------------------------------------------------
# Layer 1 — Color primitives
# ---------------------------------------------------------------------------

def _color_enabled(stream: Any = None) -> bool:
    """Return True when ANSI escapes should be emitted.

    Checks NO_COLOR (https://no-color.org/), TERM=dumb, and isatty().
    *stream* defaults to sys.stdout; pass sys.stderr for JSON-mode scripts
    that emit human text on stderr.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if stream is None:
        stream = sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


# Module-level flag — evaluated once at import, covers the common case.
# Scripts that need per-call checks (e.g. watchdog JSON mode toggling stderr)
# can call _color_enabled(stream) directly.
_USE_COLOR: bool = _color_enabled()


def set_color(enabled: bool) -> None:
    """Override the module-level color flag.

    Call ``set_color(False)`` to suppress all ANSI output — useful when a
    script enters ``--json`` mode after argparse completes.
    """
    global _USE_COLOR
    _USE_COLOR = enabled


def _ansi(text: Any, code: str) -> str:
    """Wrap *text* in an ANSI escape if color is enabled."""
    if not _USE_COLOR:
        return str(text)
    return f"\033[{code}m{text}\033[0m"


# --- Named color wrappers (ANSI code helpers, not status claims) ----------
# These are formatting primitives — each wraps text in the given ANSI code.

def clr_green(text: Any) -> str:
    """Wrap text in ANSI 32 (color primitive, ref: console_tui.py:53)."""
    return _ansi(text, "32")

def clr_red(text: Any) -> str:
    """Wrap text in ANSI 31 (color primitive, ref: console_tui.py:65)."""
    return _ansi(text, "31")

def clr_yellow(text: Any) -> str:
    """Wrap text in ANSI 33 (color primitive, ref: console_tui.py:57)."""
    return _ansi(text, "33")

def clr_cyan(text: Any) -> str:
    """Wrap text in ANSI 36 (color primitive, ref: console_tui.py:61)."""
    return _ansi(text, "36")

def clr_bold(text: Any) -> str:
    """Wrap text in ANSI 1 (color primitive, ref: console_tui.py:49)."""
    return _ansi(text, "1")

def clr_dim(text: Any) -> str:
    """Wrap text in ANSI 2 (color primitive, ref: console_tui.py:69)."""
    return _ansi(text, "2")


# --- Truecolor (24-bit) helpers — for pixel/image rendering in terminal ----
# Raw escape variants (no text argument) so callers can chain fg + bg + char.
# Use RESET to close a truecolor sequence.

RESET: str = "\033[0m"
"""ANSI reset sequence.  Use to close a truecolor or mixed-mode sequence."""


def fg_rgb(r: int, g: int, b: int) -> str:
    """Return raw ANSI 24-bit foreground escape ``\\033[38;2;R;G;Bm`` (no reset).

    Use when building pixel rows that chain fg/bg escapes directly:
    ``fg_rgb(r, g, b) + bg_rgb(r2, g2, b2) + char + RESET``
    """
    return f"\033[38;2;{r};{g};{b}m" if _USE_COLOR else ""


def bg_rgb(r: int, g: int, b: int) -> str:
    """Return raw ANSI 24-bit background escape ``\\033[48;2;R;G;Bm`` (no reset)."""
    return f"\033[48;2;{r};{g};{b}m" if _USE_COLOR else ""


# ---------------------------------------------------------------------------
# Layer 2 — Status system (semantic coloring)
# ---------------------------------------------------------------------------

# Maps uppercase status words to ANSI codes.  Bold variants so badges pop.
# Source: watchdog_run_canonical.py:201 (commit 0d46811c)
STATUS_COLORS: dict[str, str] = {
    "OK":       "1;32",
    "PASS":     "1;32",
    "RUNNING":  "36",
    "ALIVE":    "36",
    "INFO":     "36",
    "SKIP":     "1;33",
    "WARN":     "1;33",
    "WARNING":  "1;33",
    "FAIL":     "1;31",
    "ERROR":    "1;31",
    "BLOCKED":  "1;31",
    "MISSING":  "1;33",
    "MISMATCH": "1;31",
}

# Named text styles for non-status semantic coloring.
# Source: watchdog_run_canonical.py:212 (commit 0d46811c)
TEXT_STYLES: dict[str, str] = {
    "header":    "1;33",
    "subheader": "1;36",
    "ok":        "1;32",
    "warn":      "1;33",
    "fail":      "1;31",
    "path":      "36",
    "dim":       "2",
    "count":     "1;37",
    "watchdog":  "1;35",   # bold magenta — [WATCHDOG ...] prefix brackets
}


def status(word: str, msg: str | None = None) -> str:
    """Format a status badge: ``[PASS] some message``.

    If *msg* is None, returns just the colored badge (no trailing text).
    """
    upper = word.upper()
    code = STATUS_COLORS.get(upper)
    if code and _USE_COLOR:
        badge = f"\033[{code}m[{upper}]\033[0m"
    else:
        badge = f"[{upper}]"
    if msg is None:
        return badge
    return f"{badge} {msg}"


def style(text: Any, name: str) -> str:
    """Apply a named text style from TEXT_STYLES."""
    code = TEXT_STYLES.get(name)
    if not code or not _USE_COLOR:
        return str(text)
    return f"\033[{code}m{text}\033[0m"


# --- Screen control helpers ------------------------------------------------

def clear_screen() -> str:
    """Return ANSI sequence to clear the screen (cursor home + erase)."""
    return "\033[H\033[J" if _USE_COLOR else ""


def alt_screen_enter() -> str:
    """Return ANSI sequence to enter alternate screen buffer + hide cursor."""
    return "\033[?1049h\033[?25l" if _USE_COLOR else ""


def alt_screen_exit() -> str:
    """Return ANSI sequence to exit alternate screen buffer + show cursor."""
    return "\033[?1049l\033[?25h" if _USE_COLOR else ""


# --- Semantic formatting helpers -------------------------------------------

def fmt_header(title: str, char: str = "=", width: int = 70) -> str:
    """Return a formatted section divider as a multi-line string (no print).

    Composable alternative to ``header()`` for contexts where you need the
    string rather than an immediate print side-effect.
    """
    rule = char * width
    if _USE_COLOR:
        c = "\033[1;33m"
        r = "\033[0m"
        return f"{c}{rule}{r}\n{c}  {title}{r}\n{c}{rule}{r}"
    return f"{rule}\n  {title}\n{rule}"


def header(title: str, char: str = "=", width: int = 70) -> None:
    """Print a section divider: ``====== title ======``."""
    print(fmt_header(title, char=char, width=width))


def progress(step: int, total: int, msg: str = "") -> str:
    """Format a progress counter: ``[1/4] Processing...``."""
    counter = f"[{step}/{total}]"
    if _USE_COLOR:
        counter = f"\033[2m{counter}\033[0m"
    if msg:
        if _USE_COLOR:
            return f"{counter} \033[1m{msg}\033[0m"
        return f"{counter} {msg}"
    return counter


def ok_item(text: Any) -> str:
    """Format a pass/success bullet: ANSI-32 checkmark + text."""
    if _USE_COLOR:
        return f"\033[32m✓\033[0m {text}"
    return f"✓ {text}"


def fail_item(text: Any) -> str:
    """Format a failure bullet: ANSI-31 cross + text."""
    if _USE_COLOR:
        return f"\033[31m✗\033[0m {text}"
    return f"✗ {text}"


_DIFF_COLORS: dict[str, str] = {
    "+": "32",   # ANSI-32
    "-": "31",   # ANSI-31
    "→": "36",   # ANSI-36
    "->": "36",
}


def diff_line(text: str, op: str = "→") -> str:
    """Prefix *text* with a colored diff-style operator (``+``, ``-``, ``→``)."""
    code = _DIFF_COLORS.get(op)
    if code and _USE_COLOR:
        return f"\033[{code}m{op}\033[0m {text}"
    return f"{op} {text}"


def prompt(msg: str) -> str:
    """Return a bold-cyan prompt string suitable for ``input(prompt(...))``."""
    if _USE_COLOR:
        return f"\033[1;36m{msg}\033[0m "
    return f"{msg} "


def count_badge(label: str, count: int) -> str:
    """Format a count badge: ``(3 errors)`` in bold-white."""
    badge = f"({count} {label})"
    if _USE_COLOR:
        return f"\033[1;37m{badge}\033[0m"
    return badge


_HIGHLIGHT_BRACKET_RE = re.compile(r"(\[[^\]]{1,60}\])")
_HIGHLIGHT_ALLCAPS_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")


def highlight_tokens(text: str) -> str:
    """Bold ``[bracket]`` groups and ``ALL_CAPS`` words in *text*.

    Applies two rules:
    - Any ``[...]`` group (up to 60 chars) is rendered bold.
    - Any word of 3+ uppercase letters/digits/underscores is rendered bold.

    Color must be enabled — returns *text* unchanged when color is off.
    Used by watchdog output line styling for generic pass-through lines.
    """
    if not _USE_COLOR:
        return text
    text = _HIGHLIGHT_BRACKET_RE.sub(lambda m: f"\033[1m{m.group(1)}\033[0m", text)
    text = _HIGHLIGHT_ALLCAPS_RE.sub(lambda m: f"\033[1m{m.group(1)}\033[0m", text)
    return text


# ---------------------------------------------------------------------------
# Layer 3 — Table renderer
# ---------------------------------------------------------------------------

def table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    indent: int = 0,
    max_col: int = 40,
) -> str:
    """Return an aligned table as a string.

    *rows* is a list of sequences (tuples or lists), one per row.
    Columns are left-aligned, auto-sized to content (capped at *max_col*).
    """
    if not rows:
        return "  " * indent + "(no data)"

    ncols = len(headers)
    prefix = "  " * indent
    sep = "  "

    # Stringify + truncate
    str_rows: list[list[str]] = []
    for row in rows:
        cells: list[str] = []
        for i in range(ncols):
            val = row[i] if i < len(row) else ""
            if val is None:
                s = "-"
            elif isinstance(val, float):
                s = f"{val:.2f}"
            elif isinstance(val, list):
                s = ", ".join(str(x) for x in val)
            else:
                s = str(val)
            if len(s) > max_col:
                s = s[: max_col - 3] + "..."
            cells.append(s)
        str_rows.append(cells)

    # Column widths
    widths = [len(h) for h in headers]
    for cells in str_rows:
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))

    # Render
    lines: list[str] = []
    lines.append(prefix + sep.join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    lines.append(prefix + sep.join("-" * w for w in widths))
    for cells in str_rows:
        lines.append(prefix + sep.join(c.ljust(widths[i]) for i, c in enumerate(cells)))
    return "\n".join(lines)


def kv(
    pairs: Sequence[tuple[str, Any]],
    *,
    indent: int = 0,
) -> str:
    """Return aligned key: value rows as a string."""
    if not pairs:
        return ""
    prefix = "  " * indent
    width = max(len(str(k)) for k, _ in pairs)
    return "\n".join(f"{prefix}{str(k):<{width}}  {v}" for k, v in pairs)


# ---------------------------------------------------------------------------
# Layer 4 — Cycling animation primitives
# ---------------------------------------------------------------------------

# ── Spinner frame sets ───────────────────────────────────────────────────────

SPINNER_BRAILLE: list[str] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
"""Braille throbber — 10 frames at 80 ms each → one cycle per 800 ms.
Source: testing/anim_E_throbber.py (direct import)."""

SPINNER_PIPE: list[str] = ["|", "/", "-", "\\"]
"""Classic ASCII pipe spinner — 4 frames, ASCII-safe, works on any terminal."""

SPINNER_DOTS: list[str] = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]
"""Braille-dots variant — 8 frames, fills the braille cell in rotation."""

SPINNER_BLOCK: list[str] = ["▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"]
"""Left-to-right block fill — 8 frames, 0 → full block then restarts."""

SPINNER_WAVE: list[str] = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█", "▇", "▆", "▅", "▄", "▃", "▂"]
"""Wave pulse spinner — climbs and falls like a single-column sparkline."""

COLOR_PURPLE: str = "35"
"""ANSI magenta/purple — used for launcher loading indicators."""


def spin_frame(n: int, frames: list[str] | None = None) -> str:
    """Return the spinner character for integer counter *n*.

    Stateless — caller owns the counter and timing loop.
    Defaults to ``SPINNER_BRAILLE``; pass ``SPINNER_PIPE`` for ASCII-safe output.
    """
    if frames is None:
        frames = SPINNER_BRAILLE
    return frames[n % len(frames)]


def spin_line(
    msg: str,
    frame: int,
    frames: list[str] | None = None,
    *,
    stream: Any = None,
) -> str:
    r"""Return a ``\r``-prefixed spinner line ready to write to a stream.

    Writes ``\r``, the spinner char (cyan when color is on), two spaces,
    *msg*, then ``\033[K`` (erase-to-end-of-line) so previous longer text
    does not ghost.

    Usage::

        frame = 0
        while busy:
            sys.stdout.write(spin_line("Building...", frame))
            sys.stdout.flush()
            frame += 1
            time.sleep(0.080)
        sys.stdout.write("\r\033[K")  # clear line
    """
    if frames is None:
        frames = SPINNER_BRAILLE
    ch = spin_frame(frame, frames)
    use_color = _USE_COLOR and (
        stream is None or bool(getattr(stream, "isatty", lambda: False)())
    )
    if use_color:
        return f"\r\033[36m{ch}\033[0m  {msg}\033[K"
    return f"\r{ch}  {msg}\033[K"


# ── Progress bar ─────────────────────────────────────────────────────────────

def progress_bar(
    current: int,
    total: int,
    width: int = 20,
    fill: str = "█",
    empty: str = "░",
    color: str = "1;32",
) -> str:
    """Return a progress bar string: ``[████████░░░░]  66%``.

    *color* is an ANSI code for the filled portion (default ``1;32``).
    Empty portion uses dim. Non-ASCII fill/empty chars are replaced with
    ``=`` / space when color is disabled so the bar is always printable.
    """
    pct = current / max(total, 1)
    filled = round(pct * width)
    pct_str = f"{int(pct * 100):3d}%"
    if _USE_COLOR:
        bar = (
            f"\033[{color}m{fill * filled}\033[2m{empty * (width - filled)}\033[0m"
        )
        return f"[{bar}] {pct_str}"
    f_ch = fill if fill.isascii() else "="
    e_ch = empty if empty.isascii() else " "
    bar = f_ch * filled + e_ch * (width - filled)
    return f"[{bar}] {pct_str}"


# ── Gradient text ────────────────────────────────────────────────────────────

def gradient_text(
    text: str,
    phase: float = 0.0,
    *,
    hue_spread: float = 0.04,
    saturation: float = 1.0,
    value: float = 1.0,
) -> str:
    """Render *text* with a sliding HSV hue gradient.

    Each non-whitespace character gets a unique hue derived from its column
    index and the global *phase* offset.  Incrementing *phase* each frame
    makes the gradient appear to slide across the text (Gemini-CLI style).

    Parameters
    ----------
    text:
        The string to colorize.  Whitespace chars are output unchanged —
        no color escape emitted, consistent with ``gradient-string`` behaviour.
    phase:
        Global hue offset in ``[0.0, 1.0)``.  Compute as::

            phase = (time.monotonic() - start) / CYCLE % 1.0

        where ``CYCLE`` is the desired repeat period in seconds.  Gemini
        uses 4 s for its brand spinner cycle.
    hue_spread:
        Hue delta per non-whitespace character.  ``0.04`` ≈ 14° per char;
        at 26 chars the full spectrum repeats once.  Decrease for a gentler
        gradient on short strings.
    saturation:
        HSV saturation ``[0.0, 1.0]``.  Default 1.0 = fully saturated.
    value:
        HSV value (brightness) ``[0.0, 1.0]``.  Default 1.0 = full brightness.

    Returns *text* unchanged when ``_USE_COLOR`` is False.

    Animated usage::

        import time
        CYCLE = 4.0
        start = time.monotonic()
        while True:
            phase = (time.monotonic() - start) / CYCLE % 1.0
            sys.stdout.write("\\r" + gradient_text("loading...", phase) + "\\033[K")
            sys.stdout.flush()
            time.sleep(0.033)   # ~30 fps
    """
    if not _USE_COLOR:
        return text
    out: list[str] = []
    char_i = 0
    for ch in text:
        if ch in (" ", "\t"):
            out.append(ch)
            continue
        hue = (phase + char_i * hue_spread) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
        out.append(
            f"\033[38;2;{int(r * 255)};{int(g * 255)};{int(b * 255)}m{ch}\033[0m"
        )
        char_i += 1
    return "".join(out)


def gradient_text_stops(
    text: str,
    stops: list[tuple[int, int, int]],
) -> str:
    """Render *text* with a static multi-stop RGB gradient.

    One pre-computed RGB colour per non-whitespace character, linearly
    interpolated across *stops* in RGB space — matches the Gemini CLI
    ``ThemedGradient`` / ``gradient-string`` / ``tinygradient`` approach.

    Parameters
    ----------
    text:
        String to colorize.  Whitespace chars are output unchanged.
    stops:
        At least two ``(R, G, B)`` tuples, each component 0–255.
        Example (Gemini theme)::

            gradient_text_stops(msg, GRADIENT_GEMINI)

    Returns *text* unchanged when ``_USE_COLOR`` is False or *stops* has
    fewer than two entries.
    """
    if not _USE_COLOR or len(stops) < 2:
        return text
    non_ws_count = sum(1 for c in text if c not in (" ", "\t"))
    if non_ws_count == 0:
        return text
    k = len(stops) - 1
    colors: list[tuple[int, int, int]] = []
    for i in range(non_ws_count):
        t = i / max(non_ws_count - 1, 1)
        seg = min(int(t * k), k - 1)
        local_t = t * k - seg
        r0, g0, b0 = stops[seg]
        r1, g1, b1 = stops[seg + 1]
        colors.append((
            int(r0 + (r1 - r0) * local_t),
            int(g0 + (g1 - g0) * local_t),
            int(b0 + (b1 - b0) * local_t),
        ))
    out: list[str] = []
    ci = 0
    for ch in text:
        if ch in (" ", "\t"):
            out.append(ch)
        else:
            r, g, b = colors[ci]
            out.append(f"\033[38;2;{r};{g};{b}m{ch}\033[0m")
            ci += 1
    return "".join(out)


# Named gradient presets ──────────────────────────────────────────────────────

GRADIENT_GEMINI: list[tuple[int, int, int]] = [
    (0x47, 0x96, 0xE4),   # #4796E4  blue
    (0x84, 0x7A, 0xCE),   # #847ACE  purple
    (0xC3, 0x67, 0x7F),   # #C3677F  rose
]
"""Gemini CLI ``ThemedGradient`` stops: blue → purple → rose (RGB lerp, static).

Source: ``gemini-cli/packages/cli/src/ui/themes/theme.ts`` ``GradientColors``."""

GRADIENT_GEMINI_BRAND: list[tuple[int, int, int]] = [
    (0xD7, 0xAF, 0xFF),   # AccentPurple
    (0x87, 0xAF, 0xFF),   # AccentBlue
    (0x87, 0xD7, 0xD7),   # AccentCyan
    (0xD7, 0xFF, 0xD7),   # AccentLime
    (0xFF, 0xFF, 0xAF),   # AccentYellow
    (0xFF, 0x87, 0xAF),   # AccentRed
]
"""Gemini brand color sequence for the animated spinner (wrap-around 4 s cycle).

Source: ``gemini-cli/packages/cli/src/ui/components/GeminiSpinner.tsx``.
Use with ``gradient_text_stops`` or cycle via phase-based lerp::

    phase = (time.monotonic() - start) / 4.0 % 1.0
"""


# ── Scroll-region coordination (FL-1878) ─────────────────────────────────────
#
# When a DECSTBM scroll region is active (e.g. the watchdog pinned panel),
# any stdout writer that moves the cursor (\r, \033[H, \033[s/u) without
# coordinating with the panel owner corrupts the layout.  The registry
# below lets the Spinner (and any future animated primitive) detect this
# and suppress its inline animation — the panel's own spinner already
# shows progress.  An optional sub-status callback lets suppressed
# Spinners route their message into the panel instead.

_scroll_region_active: bool = False
_scroll_region_sub_status_cb: Callable[[str], None] | None = None
_scroll_region_sub_status_clear_cb: Callable[[], None] | None = None


def register_scroll_region(
    *,
    sub_status_cb: Callable[[str], None] | None = None,
    sub_status_clear_cb: Callable[[], None] | None = None,
) -> None:
    """Mark a DECSTBM scroll region as active on stdout.

    Called by panel_init() or equivalent.  While active, Spinner will
    suppress inline animation on stdout and optionally route its message
    through *sub_status_cb* so the panel can display it.
    """
    global _scroll_region_active, _scroll_region_sub_status_cb
    global _scroll_region_sub_status_clear_cb
    _scroll_region_active = True
    _scroll_region_sub_status_cb = sub_status_cb
    _scroll_region_sub_status_clear_cb = sub_status_clear_cb


def unregister_scroll_region() -> None:
    """Clear the scroll-region flag.  Called by panel_cleanup()."""
    global _scroll_region_active, _scroll_region_sub_status_cb
    global _scroll_region_sub_status_clear_cb
    _scroll_region_active = False
    _scroll_region_sub_status_cb = None
    _scroll_region_sub_status_clear_cb = None


# ── Spinner context manager (thread-based) ───────────────────────────────────

class Spinner:
    """Thread-based inline spinner context manager.

    Prints a cycling spinner to *stream* (default ``sys.stderr``) while the
    ``with`` block executes.  Hides the cursor during animation and clears
    the spinner line on exit.

    Color contract: the animation is a no-op when the stream is not a TTY,
    ``NO_COLOR`` is set, ``TERM=dumb``, or ``_USE_COLOR`` is False — the
    ``with`` block executes normally but the spinner emits nothing.

    Parameters
    ----------
    msg:
        Initial label shown after the spinner character.
    frames:
        Frame sequence.  Defaults to ``SPINNER_BRAILLE``.  Pass
        ``SPINNER_PIPE`` for ASCII-only terminals or when unicode is
        unavailable.
    interval:
        Seconds per frame.  Default ``0.080`` s (12.5 fps) matches
        ``testing/anim_E_throbber.py``.
    stream:
        Output stream.  Defaults to ``sys.stderr`` so stdout stays clean
        for machine output — consistent with the Color Contract.

    Usage::

        # Fire-and-forget around any blocking call
        with Spinner("Building server binary"):
            subprocess.run(build_cmd, check=True)

        # Swap message and print a checkmark line on success
        with Spinner("Connecting", frames=SPINNER_PIPE) as sp:
            wait_for_server()
            sp.finish("Server ready")

        # ASCII-safe (no-op when not a TTY)
        with Spinner("Running checks", frames=SPINNER_PIPE):
            run_all_checks()
    """

    def __init__(
        self,
        msg: str = "busy",
        *,
        frames: list[str] | None = None,
        interval: float = 0.080,
        stream: Any = None,
        color: str = "36",
    ) -> None:
        self._msg = msg
        self._frames = frames if frames is not None else SPINNER_BRAILLE
        self._interval = interval
        self._stream = stream if stream is not None else sys.stderr
        self._color = color
        self._stop = _threading.Event()
        self._thread: _threading.Thread | None = None
        self._active = False
        self._routed_to_panel = False

    def _can_animate(self) -> bool:
        # Suppress inline animation when a scroll region owns stdout (FL-1878).
        # The panel's own block spinner already shows step progress; an
        # uncoordinated \r from this thread would corrupt the panel layout.
        if _scroll_region_active and self._stream is sys.stdout:
            return False
        return _USE_COLOR and bool(
            getattr(self._stream, "isatty", lambda: False)()
        )

    def __enter__(self) -> "Spinner":
        if self._can_animate():
            self._active = True
            self._thread = _threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        elif _scroll_region_active and self._stream is sys.stdout:
            # Route spinner message into the panel's sub-status (approach C).
            if _scroll_region_sub_status_cb is not None:
                _scroll_region_sub_status_cb(self._msg)
            self._routed_to_panel = True
        return self

    def _loop(self) -> None:
        self._stream.write("\033[?25l")   # hide cursor
        self._stream.flush()
        try:
            i = 0
            while not self._stop.is_set():
                ch = self._frames[i % len(self._frames)]
                self._stream.write(f"\r\033[{self._color}m{ch}\033[0m  {self._msg}\033[K")
                self._stream.flush()
                i += 1
                self._stop.wait(self._interval)
        finally:
            self._stream.write("\033[?25h")  # restore cursor
            self._stream.flush()

    def update(self, msg: str) -> None:
        """Replace the spinner label while it is running."""
        self._msg = msg
        # Update panel sub-status when routed there (FL-1878).
        # Local-bind to avoid TOCTOU if unregister fires concurrently.
        if self._routed_to_panel:
            cb = _scroll_region_sub_status_cb
            if cb is not None:
                cb(msg)

    def finish(self, msg: str = "") -> None:
        """Stop the spinner and optionally print a checkmark line.

        Clears the spinner line and, if *msg* is given, writes an
        ``ok_item(msg)`` line to the stream.  Call this inside the
        ``with`` block once the outcome is known.
        """
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._active:
            if msg:
                self._stream.write(f"\r{ok_item(msg)}\033[K\n")
            else:
                self._stream.write("\r\033[K")
            self._stream.flush()
        self._active = False
        if self._routed_to_panel:
            clear_cb = _scroll_region_sub_status_clear_cb
            if clear_cb is not None:
                clear_cb()
            self._routed_to_panel = False

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._active:
            self._stream.write("\r\033[K")   # erase spinner line
            self._stream.flush()
        if self._routed_to_panel:
            clear_cb = _scroll_region_sub_status_clear_cb
            if clear_cb is not None:
                clear_cb()
            self._routed_to_panel = False
        return False


# ── Sparkline ─────────────────────────────────────────────────────────────────

SPARKLINE_CHARS: str = "▁▂▃▄▅▆▇█"
"""Block-element mini chart characters, 1/8-height to full-height."""


def sparkline(
    values: list[float],
    *,
    lo: float | None = None,
    hi: float | None = None,
    color: bool = True,
) -> str:
    """Return an inline mini-chart string using ▁▂▃▄▅▆▇█ block chars.

    Each value maps to one of eight block heights.  Values are normalised
    against *lo* / *hi* (default: range of *values*).

    Color (when ``_USE_COLOR`` and *color* are True): 24-bit ANSI gradient
    from cool blue (low values) through warm amber (high values).

    Returns an empty string when *values* is empty.

    Usage::

        import time, math
        start = time.monotonic()
        while True:
            t = time.monotonic() - start
            values = [math.sin(t + i * 0.4) * 0.5 + 0.5 for i in range(24)]
            sys.stdout.write("\\r  " + sparkline(values) + "\\033[K")
            sys.stdout.flush()
            time.sleep(0.033)
    """
    if not values:
        return ""
    lo_ = min(values) if lo is None else lo
    hi_ = max(values) if hi is None else hi
    span = float(hi_ - lo_) or 1.0

    chars: list[str] = []
    for v in values:
        t = max(0.0, min(1.0, (v - lo_) / span))
        chars.append(SPARKLINE_CHARS[min(7, int(t * 8))])

    if not (_USE_COLOR and color):
        return "".join(chars)

    # 24-bit gradient: cool blue (low) → warm amber (high)
    out: list[str] = []
    for v, ch in zip(values, chars):
        t = max(0.0, min(1.0, (v - lo_) / span))
        r = int(60 + t * 160)
        g = int(100 + t * 40)
        b = int(220 - t * 220)
        out.append(f"\033[38;2;{r};{g};{b}m{ch}\033[0m")
    return "".join(out)


# ── Step list ─────────────────────────────────────────────────────────────────

STEP_OK:   str = "ok"    #: Completed successfully — renders ✓ in ANSI 1;32.
STEP_RUN:  str = "run"   #: Currently executing — renders animated spinner.
STEP_WAIT: str = "wait"  #: Not yet started — renders a dim · dot.
STEP_ERR:  str = "err"   #: Error encountered — renders ✗ in bold red.


def step_list(
    steps: list[tuple[str, str]],
    frame: int = 0,
) -> str:
    """Render a multi-line step-status block to a single string.

    Parameters
    ----------
    steps:
        ``[(label, status), ...]`` where *status* is one of the ``STEP_*``
        constants: ``STEP_OK``, ``STEP_RUN``, ``STEP_WAIT``, ``STEP_ERR``.
    frame:
        Animation counter for the ``STEP_RUN`` spinner (increment each tick).

    Returns a ``\\n``-joined block of *len(steps)* lines.  Each line ends with
    ``\\033[K`` (erase-to-end-of-line) so redrawing in place after a
    cursor-up clears any previous longer text.

    Initial render + animation loop::

        steps = [
            ("compile server", STEP_OK),
            ("health probe",   STEP_RUN),
            ("run scenarios",  STEP_WAIT),
        ]
        print(step_list(steps))       # first render
        for frame in range(200):
            step_redraw(steps, frame) # in-place update
            time.sleep(0.080)
    """
    lines: list[str] = []
    for label, status in steps:
        if status == STEP_OK:
            ind = "\033[1;32m✓\033[0m" if _USE_COLOR else "+"
        elif status == STEP_RUN:
            ch = spin_frame(frame)
            ind = f"\033[36m{ch}\033[0m" if _USE_COLOR else ch
        elif status == STEP_ERR:
            ind = "\033[1;31m✗\033[0m" if _USE_COLOR else "!"
        else:  # STEP_WAIT
            ind = "\033[2m·\033[0m" if _USE_COLOR else "."
        lines.append(f"  {ind}  {label}\033[K")
    return "\n".join(lines)


def step_redraw(
    steps: list[tuple[str, str]],
    frame: int = 0,
    *,
    stream: Any = None,
) -> None:
    """Erase and redraw a step list previously rendered by ``print(step_list(…))``.

    Moves the cursor up ``len(steps)`` lines, writes a fresh
    ``step_list(steps, frame)``, then emits a trailing newline so the
    cursor position always matches the state left by the initial
    ``print(step_list(…))``.

    Parameters
    ----------
    stream:
        Output stream.  Defaults to ``sys.stdout``.
    """
    if stream is None:
        stream = sys.stdout
    n = len(steps)
    stream.write(f"\033[{n}A")
    stream.write(step_list(steps, frame))
    stream.write("\n")
    stream.flush()


# ── ETA progress bar ──────────────────────────────────────────────────────────

def eta_bar(
    current: int,
    total: int,
    elapsed: float,
    *,
    width: int = 20,
    unit: str = "it",
) -> str:
    """Progress bar extended with throughput rate and ETA.

    Returns::

        [████████░░░░]  66%  12.3 it/s  ETA 0:04

    Rate and ETA are omitted when *current* or *elapsed* is zero.

    Parameters
    ----------
    current:
        Items completed so far.
    total:
        Total item count.
    elapsed:
        Seconds since the operation began (``time.monotonic() - start``).
    width:
        Inner fill-bar character count (default 20).
    unit:
        Label for the throughput rate (default ``"it"``).
    """
    bar = progress_bar(current, total, width=width)
    if current <= 0 or elapsed <= 0.0 or total <= 0:
        return bar
    rate = current / elapsed
    remaining = max(0.0, (total - current) / rate) if rate > 0.0 else 0.0
    m, s = divmod(int(remaining), 60)
    h, m2 = divmod(m, 60)
    eta_str = f"{h}:{m2:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    rate_str = f"{rate:.1f} {unit}/s"
    if _USE_COLOR:
        return f"{bar}  \033[2m{rate_str}  ETA {eta_str}\033[0m"
    return f"{bar}  {rate_str}  ETA {eta_str}"


# ── Countdown ─────────────────────────────────────────────────────────────────

def countdown_line(
    n: int,
    msg: str = "restarting",
    *,
    stream: Any = None,
) -> None:
    """Blocking countdown that overwrites one terminal line each second.

    Counts from *n* down to 1 then clears the line when it reaches zero.
    Hides the cursor during the count.

    Parameters
    ----------
    n:
        Number of seconds to count from.  Values < 1 return immediately.
    msg:
        Label shown before the counter (e.g. ``"restarting"``).
    stream:
        Output stream.  Defaults to ``sys.stderr`` so stdout remains clean.
    """
    if stream is None:
        stream = sys.stderr
    use_color = _USE_COLOR and bool(getattr(stream, "isatty", lambda: False)())
    stream.write("\033[?25l")
    stream.flush()
    try:
        for i in range(n, 0, -1):
            if use_color:
                line = (
                    f"\r\033[2m⏳\033[0m \033[1m{msg}\033[0m"
                    f" in \033[36m{i:2d}\033[0m …\033[K"
                )
            else:
                line = f"\r{msg} in {i:2d} ...\033[K"
            stream.write(line)
            stream.flush()
            time.sleep(1.0)
        stream.write("\r\033[K")
    finally:
        stream.write("\033[?25h")
        stream.flush()


def wave_notice_line(
    duration_s: float,
    msg: str,
    *,
    stream: Any = None,
    interval_s: float = 0.060,
) -> None:
    """Animate a Gemini purple→rose wave spinner on one line for *duration_s*.

    Intended as a short prelude before a blocking countdown or other loud
    transition. Clears the line on exit and restores the cursor.
    """
    if duration_s <= 0:
        return
    if stream is None:
        stream = sys.stderr
    use_color = _USE_COLOR and bool(getattr(stream, "isatty", lambda: False)())
    start = time.monotonic()
    frame = 0
    stream.write("\033[?25l")
    stream.flush()
    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= duration_s:
                break
            ch = spin_frame(frame, SPINNER_WAVE)
            line = f"{ch}  {msg}"
            if use_color:
                line = gradient_text_stops(
                    line,
                    [
                        GRADIENT_GEMINI[1],  # purple
                        GRADIENT_GEMINI[2],  # rose
                    ],
                )
            stream.write(f"\r{line}\033[K")
            stream.flush()
            frame += 1
            remaining = duration_s - elapsed
            time.sleep(min(interval_s, max(0.0, remaining)))
        stream.write("\r\033[K")
    finally:
        stream.write("\033[?25h")
        stream.flush()


# ── Watchdog pipeline panel ──────────────────────────────────────────────────


def watchdog_panel(
    steps: list[str],
    current_index: int,
    elapsed_per_step: dict[str, float] | None = None,
    sub_statuses: dict[str, str] | None = None,
    frame: int = 0,
    *,
    step_weights: dict[str, float] | None = None,
    tty_only: bool = False,
) -> str:
    """Render the watchdog 11-step pipeline panel as a multi-line string.

    Parameters
    ----------
    steps:
        Ordered list of step names (the CONSOLE_PHASES tuple).
    current_index:
        0-based index of the currently active step.  -1 means all are
        pending, ``len(steps)`` means all are finished.
    elapsed_per_step:
        ``{step_name: seconds}`` for steps that have timing data.
    sub_statuses:
        ``{step_name: "one-line sub-status"}`` shown after the step row.
    frame:
        Animation frame counter for SPINNER_BLOCK on the active step.
    tty_only:
        When True and color is disabled, return ``""`` (skip rendering).

    Returns a ``\\n``-joined block with a separator, step rows, and a
    progress bar footer.
    """
    if tty_only and not _USE_COLOR:
        return ""
    if elapsed_per_step is None:
        elapsed_per_step = {}
    if sub_statuses is None:
        sub_statuses = {}

    total = len(steps)
    done_count = max(0, min(current_index, total))

    # Build step_list input with per-step status.
    step_tuples: list[tuple[str, str]] = []
    for i, name in enumerate(steps):
        elapsed = elapsed_per_step.get(name)
        elapsed_str = f"  {elapsed:.0f}s" if elapsed is not None else ""
        sub = sub_statuses.get(name, "")
        sub_str = f"  {sub}" if sub else ""
        label = f"{name:<26s}{elapsed_str}{sub_str}"
        if i < current_index:
            step_tuples.append((label, STEP_OK))
        elif i == current_index:
            step_tuples.append((label, STEP_RUN))
        else:
            step_tuples.append((label, STEP_WAIT))

    lines: list[str] = []

    # Separator.
    sep = "\033[2m" + "─" * 40 + "\033[0m" if _USE_COLOR else "-" * 40
    lines.append(sep + "\033[K")

    # Step rows — build inline rather than calling step_list() so we can
    # use SPINNER_BLOCK for the active step instead of the braille spinner.
    for i, (label, status) in enumerate(step_tuples):
        if status == STEP_OK:
            ind = "\033[1;32m✔\033[0m" if _USE_COLOR else "+"
        elif status == STEP_RUN:
            ch = spin_frame(frame, SPINNER_BLOCK)
            ind = f"\033[{COLOR_PURPLE}m{ch}\033[0m" if _USE_COLOR else ch
        elif status == STEP_ERR:
            ind = "\033[1;31m✗\033[0m" if _USE_COLOR else "!"
        else:
            ind = "\033[2m○\033[0m" if _USE_COLOR else "."
        lines.append(f"  {ind} {label}\033[K")

    # Progress bar footer.
    bar = progress_bar(done_count, total, width=20)
    # ETA calculation from step weights (caller-supplied or uniform fallback).
    _weights = step_weights or {}
    total_weight = sum(_weights.get(s, 5) for s in steps)
    done_weight = sum(_weights.get(s, 5) for s in steps[:done_count])
    remaining_weight = total_weight - done_weight
    # Pace adjustment: if we have actual elapsed data, compare to expected.
    actual_elapsed = sum(elapsed_per_step.get(s, 0) for s in steps[:done_count])
    if done_weight > 0 and actual_elapsed > 0:
        pace = actual_elapsed / done_weight
        eta_s = remaining_weight * pace
    else:
        eta_s = remaining_weight  # 1x estimate

    eta_s = max(0, eta_s)
    m, s = divmod(int(eta_s), 60)
    eta_str = f"{m}:{s:02d}"
    if done_count >= total:
        footer = f"  [{done_count}/{total}] {bar}"
    else:
        if _USE_COLOR:
            footer = f"  [{done_count}/{total}] {bar}  \033[2mETA {eta_str}\033[0m"
        else:
            footer = f"  [{done_count}/{total}] {bar}  ETA {eta_str}"
    lines.append(footer + "\033[K")

    return "\n".join(lines)


def watchdog_panel_step_line(
    steps: list[str],
    current_index: int,
    elapsed_per_step: dict[str, float] | None = None,
    sub_statuses: dict[str, str] | None = None,
    frame: int = 0,
) -> tuple[int, str] | None:
    """Render only the active step's line for partial panel redraws (FL-1807).

    Returns ``(offset, line)`` where *offset* is the 0-based row within
    the panel (separator = row 0, first step = row 1, ...).  Returns
    ``None`` when there is no active step (all done or all pending).
    """
    if current_index < 0 or current_index >= len(steps):
        return None
    if elapsed_per_step is None:
        elapsed_per_step = {}
    if sub_statuses is None:
        sub_statuses = {}
    name = steps[current_index]
    elapsed = elapsed_per_step.get(name)
    elapsed_str = f"  {elapsed:.0f}s" if elapsed is not None else ""
    sub = sub_statuses.get(name, "")
    sub_str = f"  {sub}" if sub else ""
    label = f"{name:<26s}{elapsed_str}{sub_str}"
    ch = spin_frame(frame, SPINNER_BLOCK)
    if _USE_COLOR:
        ind = f"\033[{COLOR_PURPLE}m{ch}\033[0m"
    else:
        ind = ch
    line = f"  {ind} {label}\033[K"
    # row 0 = separator, row 1..N = steps
    offset = 1 + current_index
    return (offset, line)
