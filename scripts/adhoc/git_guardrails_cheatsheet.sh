#!/usr/bin/env bash
# Git guardrails cheatsheet — print available guardrail commands.
#
# Origin: proposal BF-444b7081d237 from codex session a40d6560
# Generalization: added usage docstring and help flag.

set -euo pipefail

cat <<USAGE
Git guardrails commands:

  python3 scripts/git_guardrails.py audit
  python3 scripts/git_guardrails.py session-end
  python3 scripts/git_guardrails.py switch <branch>
  python3 scripts/git_guardrails.py pull
  python3 scripts/git_guardrails.py tag <topic>

Run from repo root. See scripts/git_guardrails.py --help for details.
USAGE
