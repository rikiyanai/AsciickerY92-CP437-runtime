#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python3 -m pytest -q \
  scripts/test_compile_actor_visual_profiles_upstream_contract.py \
  scripts/test_fl4131_actor_visual_compile_admission.py \
  scripts/test_glyph_topology_gate_t1.py \
  scripts/test_glyph_topology_gate_t2_fixtures.py
python3 scripts/test_fl4131_glyph_admission.py
python3 scripts/test_fl4131_glyph_manifest_compile_outputs.py
python3 scripts/test_glyph_manifest_parity.py
python3 scripts/test_glyph_sidecar_parity.py
python3 scripts/test_fl4131_native_extended_glyph_fail_closed.py
node scripts/test_fl4131_web_compiled_atlas_binding.js
node scripts/test_fl4159_join_v2_web_send_capacity.js
node scripts/test_game_web_inbound_budget.js

python3 scripts/compile_glyph_manifest.py --check
python3 scripts/check_actor_visual_table_coverage.py
python3 scripts/dump_actor_visual_reachability.py --check
python3 scripts/check_web_diagnostic_isolation.py
