#!/usr/bin/env bash
# FL-4164: Owned headed Chrome launcher with deterministic CDP port.
#
# Completes the missing step 1 of FL-4164's plan ("Add a local debug launch
# wrapper that starts Chrome/Chromium headed with a repo-owned user data dir,
# a deterministic loopback CDP port, the requested map URL, and no gameplay
# mutation"). Companion to the existing probe attacher
# scripts/adhoc/2026-05-31-Live-CDP-probe-for-Y8-wasm-collision-debug-tab.js
# (step 2 of the plan). Together they let an agent observe live RecorderStateJson
# and capture rendered frames from the same tab a human can also drive.
#
# Refuses to attach to an arbitrary already-running Chrome. Uses its own
# user-data-dir so it cannot collide with the operator's normal profile.
# Refuses to rebind ports the operator's server/http stack already owns.
#
# Usage:
#   scripts/adhoc/2026-05-31-FL4164-owned-chrome-cdp-launcher.sh
#
# Env overrides:
#   CDP_PORT=9223
#   USER_DATA_DIR=.run/chrome-fl4164-cdp
#   SERVER_PORT=38402                       # game-server WS port (must be up)
#   HTTP_PORT=38082                         # static http server (must be up)
#   MAP_PATH=assets/a3d/game_map_y8.a3d
#   PLAYER=human                            # player= URL param
#   CHROME_BIN=/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome
#   EXTRA_TAB=1                             # open a second observation tab
#
# Exit codes:
#   0  Chrome launched (or already up on $CDP_PORT) and DevToolsActivePort present
#   2  Server or HTTP port not listening (operator must start them)
#   3  Chrome binary missing
#   4  DevToolsActivePort never appeared after launch
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CDP_PORT="${CDP_PORT:-9223}"
USER_DATA_DIR="${USER_DATA_DIR:-$REPO_ROOT/.run/chrome-fl4164-cdp}"
SERVER_PORT="${SERVER_PORT:-38402}"
HTTP_PORT="${HTTP_PORT:-38082}"
MAP_PATH="${MAP_PATH:-assets/a3d/game_map_y8.a3d}"
PLAYER="${PLAYER:-human}"
CHROME_BIN="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
EXTRA_TAB="${EXTRA_TAB:-0}"

require_port() {
  local port="$1" label="$2" hint="$3"
  if ! nc -z 127.0.0.1 "$port" 2>/dev/null; then
    echo "[FL4164] FAIL: ${label} not listening on 127.0.0.1:${port}" >&2
    echo "[FL4164]   start it with: ${hint}" >&2
    exit 2
  fi
}

require_port "$SERVER_PORT" "game server" "./.run/server --map ${MAP_PATH} --port ${SERVER_PORT} --max-players 4 &"
require_port "$HTTP_PORT"   "static http" "( cd .web && python3 -m http.server ${HTTP_PORT} --bind 127.0.0.1 ) &"

if [[ ! -x "$CHROME_BIN" ]]; then
  echo "[FL4164] FAIL: Chrome not found at: $CHROME_BIN" >&2
  echo "[FL4164]   override with CHROME_BIN=/path/to/chrome" >&2
  exit 3
fi

map_enc="$(printf '%s' "$MAP_PATH" | sed 's|/|%2F|g')"
URL="http://127.0.0.1:${HTTP_PORT}/index.html?player=${PLAYER}&server=localhost%3A${SERVER_PORT}&map=${map_enc}"

if nc -z 127.0.0.1 "$CDP_PORT" 2>/dev/null; then
  echo "[FL4164] CDP port ${CDP_PORT} already in use — refusing to relaunch."
  echo "[FL4164] attach with:"
  echo "  CDP_URL=http://127.0.0.1:${CDP_PORT} URL_SUBSTR=$(basename "$MAP_PATH") \\"
  echo "    node scripts/adhoc/2026-05-31-Live-CDP-probe-for-Y8-wasm-collision-debug-tab.js"
  exit 0
fi

mkdir -p "$USER_DATA_DIR"
# Ensure a stale DevToolsActivePort isn't picked up from a previous launch
rm -f "$USER_DATA_DIR/DevToolsActivePort"

echo "[FL4164] launching owned Chrome"
echo "[FL4164]   CDP_PORT=${CDP_PORT}"
echo "[FL4164]   user-data-dir=${USER_DATA_DIR}"
echo "[FL4164]   primary URL=${URL}"

EXTRA_URL=""
if [[ "$EXTRA_TAB" == "1" ]]; then
  EXTRA_URL="http://127.0.0.1:${HTTP_PORT}/index.html?player=${PLAYER}_observer&server=localhost%3A${SERVER_PORT}&map=${map_enc}"
  echo "[FL4164]   extra observer URL=${EXTRA_URL}"
fi

# Run Chrome in background. Do NOT use nohup; we want it to stay tied to
# this shell so the operator can SIGINT to close it cleanly.
"$CHROME_BIN" \
  --remote-debugging-port="$CDP_PORT" \
  --user-data-dir="$USER_DATA_DIR" \
  --no-default-browser-check \
  --no-first-run \
  --disable-features=ChromeWhatsNewUI,DefaultBrowserPromptRefresh \
  --new-window \
  "$URL" ${EXTRA_URL:+"$EXTRA_URL"} >"$USER_DATA_DIR/chrome.stdout.log" 2>"$USER_DATA_DIR/chrome.stderr.log" &
CHROME_PID=$!
echo "[FL4164]   chrome pid=${CHROME_PID}"

cdp_endpoint_ready() {
  # Truth surface: the CDP HTTP endpoint exists AND /json/version returns
  # a JSON body containing a webSocketDebuggerUrl. DevToolsActivePort file
  # is unreliable on macOS Chrome 148 (it isn't always written to the
  # user-data-dir root, so checking it produces false negatives even when
  # CDP is listening). The HTTP endpoint is the authoritative signal.
  curl -fsS --max-time 1 "http://127.0.0.1:${CDP_PORT}/json/version" 2>/dev/null \
    | grep -q '"webSocketDebuggerUrl"'
}

for i in $(seq 1 80); do
  if cdp_endpoint_ready; then
    echo "[FL4164] ready — CDP HTTP endpoint live at http://127.0.0.1:${CDP_PORT} chrome pid=${CHROME_PID}"
    if [[ -s "$USER_DATA_DIR/DevToolsActivePort" ]]; then
      port_line="$(head -n1 "$USER_DATA_DIR/DevToolsActivePort")"
      echo "[FL4164]   DevToolsActivePort=${port_line}"
    fi
    echo "[FL4164] attach probe:"
    echo "  CDP_URL=http://127.0.0.1:${CDP_PORT} URL_SUBSTR=$(basename "$MAP_PATH") \\"
    echo "    SCREENSHOT_PATH=.run/fl4164_attach_screenshot.png \\"
    echo "    OUT_PATH=.run/fl4164_attach_probe.json \\"
    echo "    node scripts/adhoc/2026-05-31-Live-CDP-probe-for-Y8-wasm-collision-debug-tab.js"
    exit 0
  fi
  sleep 0.25
done

echo "[FL4164] FAIL: CDP HTTP endpoint /json/version never returned a webSocketDebuggerUrl after Chrome launch" >&2
echo "[FL4164]   chrome stderr tail:" >&2
tail -n 30 "$USER_DATA_DIR/chrome.stderr.log" >&2 || true
exit 4
