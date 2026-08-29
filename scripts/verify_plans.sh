#!/usr/bin/env bash
# verify_plans.sh — Local plan governance verification
#
# Runs the semantic plan linter in strict mode. Use before phase execution
# or in CI to ensure all plans meet quality requirements.
#
# Exit codes:
#   0 — all plans pass
#   1 — warnings found (strict mode treats these as failures)
#   2 — blockers found
#
# Usage:
#   bash scripts/verify_plans.sh              # strict lint all plans
#   bash scripts/verify_plans.sh path/to.md   # lint specific file(s)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Plan Governance Verification ==="
echo "Root: $ROOT_DIR"
echo ""

# Run semantic plan linter in strict mode
if [ $# -gt 0 ]; then
    python3 "$SCRIPT_DIR/plan_lint.py" --strict --root "$ROOT_DIR" "$@"
else
    python3 "$SCRIPT_DIR/plan_lint.py" --strict --root "$ROOT_DIR"
fi

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "=== PASS: All plan checks passed ==="
else
    echo ""
    echo "=== FAIL: Plan verification failed (exit $EXIT_CODE) ==="
fi

exit $EXIT_CODE
