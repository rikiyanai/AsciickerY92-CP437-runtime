#!/usr/bin/env bash
# Detect git context: current branch, default branch, worktree status.
#
# Origin: proposal 9ca92b35c5e4 from claude session 13269ad8
# Generalized: works in any git repo; no hardcoded paths.
#
# Usage:
#   source scripts/adhoc/ci_context_check.sh   # prints detected vars
#   eval "$(bash scripts/adhoc/ci_context_check.sh --eval)"  # set in shell

set -euo pipefail

detect() {
    local current_branch default_branch
    current_branch=$(git branch --show-current 2>/dev/null || echo "")
    default_branch=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')

    if [ -z "$default_branch" ]; then
        default_branch=$(git rev-parse --verify origin/main 2>/dev/null && echo "main" || echo "master")
    fi

    echo "CURRENT_BRANCH=$current_branch"
    echo "DEFAULT_BRANCH=$default_branch"
    echo "IS_DEFAULT=$([ "$current_branch" = "$default_branch" ] && echo "true" || echo "false")"
    echo "WORKTREE_COUNT=$(git worktree list 2>/dev/null | wc -l | tr -d ' ')"
    echo "HAS_STASH=$([ -n "$(git stash list 2>/dev/null)" ] && echo "true" || echo "false")"
    echo "HAS_UNCOMMITTED=$([ -z "$(git status --porcelain 2>/dev/null)" ] && echo "false" || echo "true")"
}

if [ "${1:-}" = "--eval" ]; then
    detect
else
    echo "=== Git Context ==="
    detect
fi
