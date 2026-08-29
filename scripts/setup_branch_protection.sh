#!/usr/bin/env bash
# setup_branch_protection.sh -- Configure main branch protection rules
#
# Prerequisites: gh CLI authenticated with admin access
#   gh auth login
#
# What this configures:
#   - Require 'quick' CI job to pass before merge
#   - Require branches to be up-to-date before merge
#   - Allow force pushes disabled
#   - Allow deletions disabled
#
# Usage:
#   ./scripts/setup_branch_protection.sh
#
# If gh CLI is not available, configure manually via GitHub UI:
#   Settings > Branches > Add branch protection rule
#   - Branch name pattern: main
#   - Require status checks: quick
#   - Require branches to be up-to-date before merging: checked
#   - Include administrators: checked

set -euo pipefail

REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null)
if [ -z "$REPO" ]; then
    echo "ERROR: Could not determine repository. Run 'gh auth login' first."
    exit 1
fi

echo "Configuring branch protection for $REPO/main..."

gh api \
    --method PUT \
    "repos/$REPO/branches/main/protection" \
    --input - <<'JSON'
{
    "required_status_checks": {
        "strict": true,
        "contexts": ["quick"]
    },
    "enforce_admins": false,
    "required_pull_request_reviews": null,
    "restrictions": null,
    "allow_force_pushes": false,
    "allow_deletions": false
}
JSON

echo "Branch protection configured successfully."
echo ""
echo "Verification:"
gh api "repos/$REPO/branches/main/protection" --jq '{
    status_checks: .required_status_checks.contexts,
    strict_updates: .required_status_checks.strict,
    enforce_admins: .enforce_admins.enabled
}'
