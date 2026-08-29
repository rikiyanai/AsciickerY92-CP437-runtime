#!/usr/bin/env bash
# Extract a PR diff from GitHub given a PR URL.
#
# Origin: proposals 678b7118, 85a4e05e from claude session 0ec754f6, cdfd1667
# Generalized: accepts any PR URL, resolves base branch automatically.
#
# Usage:
#   bash scripts/adhoc/pr_diff_extract.sh https://github.com/owner/repo/pull/123
#   bash scripts/adhoc/pr_diff_extract.sh 123  # if origin remote matches

set -euo pipefail

PR_URL="${1:-}"
if [ -z "$PR_URL" ]; then
    echo "Usage: $0 <pr-url-or-number>" >&2
    exit 1
fi

# Parse PR number from URL or use directly
if echo "$PR_URL" | grep -qE '^https?://'; then
    PR_NUM=$(echo "$PR_URL" | sed 's|.*/pull/||' | sed 's|/.*||')
    REPO=$(echo "$PR_URL" | sed 's|https://github.com/||' | sed 's|/pull/.*||')
else
    PR_NUM="$PR_URL"
    # Try to infer repo from git remote
    REPO=$(git remote -v 2>/dev/null | awk '/origin.*github.com/ {print $2}' | head -1 | sed 's|https://github.com/||; s|\.git$||; s|^.*:||')
    if [ -z "$REPO" ]; then
        echo "ERROR: cannot determine repo. Provide full URL." >&2
        exit 1
    fi
fi

# Fetch PR metadata to get base ref
PR_JSON=$(curl -sf "https://api.github.com/repos/${REPO}/pulls/${PR_NUM}" 2>/dev/null || echo "")
BASE_REF=$(echo "$PR_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('base',{}).get('ref',''))" 2>/dev/null || echo "")
HEAD_REF=$(echo "$PR_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('head',{}).get('ref',''))" 2>/dev/null || echo "")

if [ -z "$BASE_REF" ] || [ -z "$HEAD_REF" ]; then
    echo "ERROR: could not resolve PR #${PR_NUM}" >&2
    exit 1
fi

echo "PR #${PR_NUM}: ${BASE_REF}..${HEAD_REF}"
echo "---"
echo "BASE:$BASE_REF"
echo "FILES:"
git diff --name-only "origin/${BASE_REF}".."origin/${HEAD_REF}" 2>/dev/null || echo "(fetch branches first)"
echo "DIFF:"
git diff -U10 "origin/${BASE_REF}".."origin/${HEAD_REF}" 2>/dev/null || echo "(fetch branches first)"
