#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

git config --local alias.gstate '!python3 scripts/git_guardrails.py audit'
git config --local alias.gsafe '!python3 scripts/git_guardrails.py session-end'
git config --local alias.gswitch '!python3 scripts/git_guardrails.py switch'
git config --local alias.gco '!python3 scripts/git_guardrails.py switch'
git config --local alias.gcheckout '!python3 scripts/git_guardrails.py switch'
git config --local alias.grestore '!python3 scripts/git_guardrails.py restore'
git config --local alias.gpullsafe '!python3 scripts/git_guardrails.py pull'
git config --local alias.gtagsafe '!python3 scripts/git_guardrails.py tag'
git config --local merge.fl-append.name 'Failure log append-only union merge'
git config --local merge.fl-append.driver 'scripts/fl-merge-driver.sh %O %A %B'

cat <<'EOF'
Installed local git guardrail aliases:
  git gstate              # audit branch/stash/worktree safety
  git gsafe               # end-of-session safety gate
  git gswitch <branch>    # safe wrapper for git switch
  git gco <branch>        # checkout-style shortcut with the same safety gate
  git gcheckout <branch>  # explicit checkout-style wrapper
  git grestore <paths...> # safe wrapper for git restore on non-canon paths
  git gpullsafe [args...] # safe wrapper for git pull --ff-only
  git gtagsafe <topic>    # create safety/YYYY-MM-DD-<topic> tag

Installed merge driver:
  merge.fl-append         # append-only union merge for docs/FAILURE_LOG.md

Note:
  Raw `git checkout` / `git switch` still bypass builtin Git command guards.
  Use `git gswitch`, `git gco`, or `git gcheckout` for branch changes when you
  want dirty-tree protection before switching.
  Raw `git restore` is not a safe front door for canon docs. Use
  `git grestore <paths...>` for non-canon files instead.
EOF
