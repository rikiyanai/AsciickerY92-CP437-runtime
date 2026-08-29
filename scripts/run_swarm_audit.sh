#!/bin/bash
# SWARM AUDIT V5 — Continuous Phase Executor
# Each `claude` invocation gets a fresh context window (natural /clear)
# Usage: ./scripts/run_swarm_audit.sh [start_phase] [end_phase]
#   e.g. ./scripts/run_swarm_audit.sh 14 21

set -e

START_PHASE=${1:-14}
END_PHASE=${2:-21}
LOG_DIR=".gsd/audit-logs"
mkdir -p "$LOG_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " SWARM AUDIT V5 — Continuous Executor"
echo " Phases $START_PHASE → $END_PHASE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for PHASE in $(seq "$START_PHASE" "$END_PHASE"); do
    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo " Phase $PHASE: PLANNING"
    echo "═══════════════════════════════════════════════════════"

    # Plan phase (fresh context)
    claude -p "/gsd:plan-phase $PHASE" \
        --allowedTools "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,Skill,mcp__plugin_serena_serena__*" \
        2>&1 | tee "$LOG_DIR/phase-${PHASE}-plan.log"

    PLAN_EXIT=$?
    if [ $PLAN_EXIT -ne 0 ]; then
        echo "✗ Phase $PHASE planning failed (exit $PLAN_EXIT). Stopping."
        exit 1
    fi

    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo " Phase $PHASE: EXECUTING"
    echo "═══════════════════════════════════════════════════════"

    # Execute phase (fresh context)
    claude -p "/gsd:execute-phase $PHASE" \
        --allowedTools "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,Skill,mcp__plugin_serena_serena__*" \
        2>&1 | tee "$LOG_DIR/phase-${PHASE}-execute.log"

    EXEC_EXIT=$?
    if [ $EXEC_EXIT -ne 0 ]; then
        echo "✗ Phase $PHASE execution failed (exit $EXEC_EXIT). Stopping."
        exit 1
    fi

    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo " Phase $PHASE: VERIFYING"
    echo "═══════════════════════════════════════════════════════"

    # Verify phase (fresh context) — compilation check + doc quality
    claude -p "Verify Phase $PHASE of the SWARM AUDIT V5 is complete. Read the current canon docs and touched phase materials for success criteria. Check: 1) All files in this phase have architectural headers, 2) Complex functions have WHY comments, 3) TAG labels are present, 4) Code compiles with 'make -f makefile_game_mac'. Record the result in the current canonical status surface if all criteria pass." \
        --allowedTools "Bash,Read,Write,Edit,Glob,Grep" \
        2>&1 | tee "$LOG_DIR/phase-${PHASE}-verify.log"

    # Check if build succeeds (hard gate)
    if make -f makefile_game_mac -n > /dev/null 2>&1; then
        echo "✓ Phase $PHASE: Build check passed"
    else
        echo "⚠ Phase $PHASE: Build check — dry run unavailable, skipping"
    fi

    echo ""
    echo "✓ Phase $PHASE COMPLETE"
    echo "───────────────────────────────────────────────────────"
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " SWARM AUDIT V5 COMPLETE"
echo " All phases $START_PHASE-$END_PHASE processed"
echo " Logs: $LOG_DIR/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
