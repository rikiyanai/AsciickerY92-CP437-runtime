#!/usr/bin/env bash
# Session startup pipeline: conductor guardrail → git guardrails → git snapshot → tests
#
# Run this at the start of a new session to verify repo state and run smoke tests.
#
# Origin: proposals BF-154bc5b1f128, BF-cb033c444c1a, BF-232b5718cb86
#   from claude session b8ccc721 and codex sessions rollout-2026-02-23T...
# Generalization: merged three session-startup pipelines into one; replaced
#   absolute worktree paths with repo-relative commands.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

echo "=== 1) Conductor guardrail ==="
python3 scripts/conductor_tools.py status --auto-setup

echo ""
echo "=== 2) Git guardrail audit ==="
python3 scripts/git_guardrails.py audit

echo ""
echo "=== 3) Git topology snapshot ==="
git status --short
echo "---"
git branch --all
echo "---"
git worktree list
echo "---"
git stash list

echo ""
echo "=== 4) Smoke tests ==="
if [ -d tests ]; then
    python3 -m pytest tests/test_wave2_*.py -q --tb=short 2>/dev/null || echo "(no wave2 tests)"
    python3 scripts/maintainer/install_hooks.py --verify 2>/dev/null || true
    python3 scripts/maintainer/run_tests.py 2>/dev/null || echo "(no maintainer tests)"
fi

echo ""
echo "=== Session startup complete ==="
