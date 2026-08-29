#!/usr/bin/env bash
# Set environment variables for web-game client and Playwright CLI.
#
# Source this file in your shell before running web-game Playwright tests:
#   source scripts/adhoc/web_game_env.sh
#
# Origin: proposals BF-8e41a2203da9, BF-033d9a26a0c3
#   from codex session rollout-2026-02-26T05-28-39
# Generalization: merged two env setup snippets; replaced absolute paths with
#   CODEX_HOME-relative defaults that fall back to well-known locations.

export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

# Web game Playwright client
if [ -f "$CODEX_HOME/skills/develop-web-game/scripts/web_game_playwright_client.js" ]; then
    export WEB_GAME_CLIENT="$CODEX_HOME/skills/develop-web-game/scripts/web_game_playwright_client.js"
fi
if [ -f "$CODEX_HOME/skills/develop-web-game/references/action_payloads.json" ]; then
    export WEB_GAME_ACTIONS="$CODEX_HOME/skills/develop-web-game/references/action_payloads.json"
fi

# Playwright CLI
if [ -f "$CODEX_HOME/skills/playwright/scripts/playwright_cli.sh" ]; then
    export PWCLI="$CODEX_HOME/skills/playwright/scripts/playwright_cli.sh"
fi

echo "CODEX_HOME=$CODEX_HOME"
echo "WEB_GAME_CLIENT=${WEB_GAME_CLIENT:-not found}"
echo "WEB_GAME_ACTIONS=${WEB_GAME_ACTIONS:-not found}"
echo "PWCLI=${PWCLI:-not found}"
