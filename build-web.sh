#!/bin/bash
set -euo pipefail
trap 'type _stop_spinner >/dev/null 2>&1 && _stop_spinner 2>/dev/null || true' EXIT INT TERM

# ARCHITECTURE:
#   Emscripten (emcc) web build script for the Asciicker game.
#
#   What it builds:
#     Compiles ~15 C/C++ source files into a WebAssembly-based web application
#     using Emscripten's emcc toolchain. The game runs in-browser via WASM with
#     a JS glue layer and a preloaded virtual filesystem for assets.
#
#   Compilation steps:
#     1. Source ~/emsdk/emsdk_env.sh to configure Emscripten environment variables
#     2. Clean previous build artifacts from .web/
#     3. Collect preload-file arguments for all game assets (maps, meshes, sprites,
#        audio samples, palettes, images)
#     4. Invoke emcc with O3 optimizations, LTO, and no-exceptions to compile all
#        C/C++ sources into a single WASM module + JS glue + HTML shell
#     5. Stage PWA support files (favicon, icon, manifest, service worker)
#
#   Key output files (all in .web/):
#     index.html  - HTML shell (from web/game_web.html template) that bootstraps the WASM module
#     index.js    - Emscripten-generated JS glue code (module loader, API bindings)
#     index.wasm  - Compiled WebAssembly binary containing the full game engine
#     index.data  - Emscripten virtual filesystem archive of all preloaded assets
#     asciicker.js - PWA service worker (copied from web/)
#
#   Preloaded assets:
#     assets/a3d/game_map_y8.a3d   - Main game map in custom A3D format
#     assets/meshes/<map refs>.akm - AKM meshes referenced by game_map_y8.a3d
#     assets/sprites/*.xp          - REXPaint sprite sheets
#     assets/samples/*.ogg         - Audio samples (Ogg Vorbis)
#     assets/palettes/palette.gz   - Compressed color palette
#     assets/images/menu.png       - Main menu image
#
# ACTOR VISUAL PROFILE SYSTEM, PLAIN ENGLISH:
#   ActorVisualProfile runtime data is compiled into the C++ generated table.
#   build-web.sh packages the generated table into the WASM binary and records
#   its hash in slot_manifest.json. This lane uses server reachability plus the
#   upstream resolver/load rule as the compiler input.

# BASED ON:
# https://webassembly.org/getting-started/developers-guide/

# install emscripten:
# $ cd ~
# git clone https://github.com/emscripten-core/emsdk.git
# cd emsdk
# ./emsdk install latest
# ./emsdk activate latest

# BEFORE BUILDING setup env vars to terminal
# cd ~/emsdk
# source ./emsdk_env.sh --build=Release

# AUTO SETUP (will be called everytime as all vars are set to this batch env only)
_EM_CACHE_OVERRIDE="${EM_CACHE:-}"
if [ -f "$HOME/emsdk/emsdk_env.sh" ]; then
    source "$HOME/emsdk/emsdk_env.sh" > /dev/null
    if [ -n "$_EM_CACHE_OVERRIDE" ]; then
        export EM_CACHE="$_EM_CACHE_OVERRIDE"
    fi
fi

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != "dumb" ]; then
    AK_BOLD_YELLOW=$'\033[1;33m'
    AK_BOLD_RED=$'\033[1;31m'
    AK_RESET=$'\033[0m'
else
    AK_BOLD_YELLOW=""
    AK_BOLD_RED=""
    AK_RESET=""
fi

say_header() { printf '%s%s%s\n' "$AK_BOLD_YELLOW" "$1" "$AK_RESET"; }
say_error() { printf '%s%s%s\n' "$AK_BOLD_RED" "$1" "$AK_RESET" >&2; }

_asciicker_python_version_ok() {
    local py="$1"
    [ -n "$py" ] || return 1
    "$py" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

_asciicker_select_build_python() {
    local candidate
    if [ -n "${ASCIICKER_BUILD_PYTHON:-}" ]; then
        if _asciicker_python_version_ok "$ASCIICKER_BUILD_PYTHON"; then
            _ASCIICKER_SELECTED_BUILD_PYTHON="$ASCIICKER_BUILD_PYTHON"
            return 0
        fi
        say_error "ERROR: ASCIICKER_BUILD_PYTHON must point to Python 3.11+; got ${ASCIICKER_BUILD_PYTHON}"
        return 1
    fi

    for candidate in \
        "${EMSDK_PYTHON:-}" \
        "$(command -v python3 2>/dev/null || true)" \
        "/opt/homebrew/bin/python3" \
        "/usr/local/bin/python3" \
        "$HOME/emsdk/python/3.13.3_64bit/bin/python3"
    do
        if [ -n "$candidate" ] && [ -x "$candidate" ] && _asciicker_python_version_ok "$candidate"; then
            _ASCIICKER_SELECTED_BUILD_PYTHON="$candidate"
            return 0
        fi
    done

    say_error "ERROR: Python 3.11+ is required for build-web.sh pipeline helpers. Set ASCIICKER_BUILD_PYTHON to a compatible interpreter."
    return 1
}

_ASCIICKER_SELECTED_BUILD_PYTHON=""
if ! _asciicker_select_build_python; then
    exit 1
fi
ASCIICKER_BUILD_PYTHON="$_ASCIICKER_SELECTED_BUILD_PYTHON"
unset _ASCIICKER_SELECTED_BUILD_PYTHON
export ASCIICKER_BUILD_PYTHON

# Purple SPINNER_BLOCK animation — matching cli_style.py convention
AK_PURPLE=$'\033[35m'
_SPINNER_FRAMES=("▏" "▎" "▍" "▌" "▋" "▊" "▉" "█")
_spinner_pid=""
_start_spinner() {
    local msg="$1"
    if [ ! -t 2 ] || [ -n "${NO_COLOR:-}" ]; then return; fi
    (
        local i=0
        printf '\033[?25l' >&2  # hide cursor
        while true; do
            local ch="${_SPINNER_FRAMES[$((i % 8))]}"
            printf '\r%s%s%s  %s\033[K' "$AK_PURPLE" "$ch" "$AK_RESET" "$msg" >&2
            i=$((i + 1))
            sleep 0.08
        done
    ) &
    _spinner_pid=$!
}
_stop_spinner() {
    if [ -n "$_spinner_pid" ]; then
        kill "$_spinner_pid" 2>/dev/null
        wait "$_spinner_pid" 2>/dev/null
        _spinner_pid=""
        printf '\r\033[K\033[?25h' >&2  # clear line + show cursor
    fi
}

export TMPDIR="${ASCIICKER_WEB_TMPDIR:-$PWD/.tmp-ems}"
mkdir -p "$TMPDIR"
mkdir -p .web

# Emscripten version pin check (RQ-091 / FL-2425)
_EMSCRIPTEN_VERSION_FILE="$(dirname "$0")/.emscripten-version"
if [ -f "$_EMSCRIPTEN_VERSION_FILE" ]; then
    _PINNED_VER="$(tr -d '[:space:]' < "$_EMSCRIPTEN_VERSION_FILE")"
    _ACTUAL_VER="$(emcc --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
    if [ -z "$_ACTUAL_VER" ]; then
        say_error "ERROR: emcc not found. Install Emscripten ${_PINNED_VER} via emsdk: ./emsdk install ${_PINNED_VER} && ./emsdk activate ${_PINNED_VER}"
        exit 1
    fi
    if [ "$_ACTUAL_VER" != "$_PINNED_VER" ]; then
        say_error "ERROR: Emscripten version mismatch. Found ${_ACTUAL_VER}, required ${_PINNED_VER} (see .emscripten-version). Run: ./emsdk install ${_PINNED_VER} && ./emsdk activate ${_PINNED_VER}"
        exit 1
    fi
fi

# now we can build,

echo ""
say_header "CHECKING glyph manifest (FL-4131) ..."
"$ASCIICKER_BUILD_PYTHON" scripts/compile_glyph_manifest.py --check

echo ""
say_header "CHECKING actor visual table coverage ..."
"$ASCIICKER_BUILD_PYTHON" scripts/check_actor_visual_table_coverage.py

echo ""
say_header "CLEARING previous build ..."

{
    rm -f .web/index.html
    rm -f .web/index.js
    rm -f .web/index.wasm
    rm -f .web/index.data
    rm -f .web/audio.js
} &> /dev/null

# Build the preload arguments array without collapsing filenames that contain spaces.
PRELOAD_ARGS=()
while IFS= read -r -d '' f; do
    PRELOAD_ARGS+=(--preload-file "$f")
done < <("$ASCIICKER_BUILD_PYTHON" scripts/web_preload_assets.py --repo-root "$PWD" --nul)

echo ""
say_header "MAKING audio worklet ..."

WORKLET_SAFETY=""
WORKLET_OPTIMIZE="-O3 -fno-exceptions -flto"

#WORKLET_SAFETY="-s SAFE_HEAP=1 -s ASSERTIONS=2"
#WORKLET_OPTIMIZE="-g"

emcc $WORKLET_OPTIMIZE $WORKLET_SAFETY \
    -DWORKLET \
    -I. \
    -Iengine \
    engine/audio.cpp \
    engine/stb_vorbis.cpp \
    -o .web/audio.js \
    -s BINARYEN_ASYNC_COMPILATION=0 \
    -s SINGLE_FILE=1 \
    --pre-js web/audio-pre.js \
    --post-js web/audio-post.js \
    --no-heap-copy \
    -s FILESYSTEM=1 \
    -s NO_EXIT_RUNTIME=1 \
    -s ALLOW_MEMORY_GROWTH=1 \
    -s EXPORTED_RUNTIME_METHODS='["ccall", "cwrap", "HEAPU8", "HEAP16"]' \
    -s EXPORTED_FUNCTIONS='["_malloc","_free","_Init","_Proc","_Call","_XOgg","_AudioDebugStateJson"]'

if [ $? -ne 0 ];
then
    exit $?
fi

echo ""
say_header "MAKING index(wasm + data + js + html) ..."

## PRODUCTION BUILD (debug flags reverted — SAFE_HEAP/ASSERTIONS prevented WASM init)
INDEX_SAFETY="-s ASSERTIONS=1"
# [BUG-2 SPENT] Compiler/control falsifiers — entire lane spent:
#   prediction-disabled: miss (FL-099). no-flto: miss (FL-100).
#   symmetric -ffp-contract=off: miss (FL-101).
#   Do not re-attempt build-flag approaches for BUG-2.
INDEX_OPTIMIZE="-O2 -fno-exceptions -gsource-map --profiling-funcs"
## DEBUG (uncomment to investigate WASM traps — WARNING: WASM may fail to init in browser):
# INDEX_SAFETY="-s SAFE_HEAP=1 -s ASSERTIONS=2 -s STACK_OVERFLOW_CHECK=2"
# INDEX_OPTIMIZE="-g -fno-exceptions"

# NOTE: Removed ASCIICKER_DEBUG_SPRITE_DEPTH for performance (was logging every frame)
INDEX_AUDIO="-DASCIICKER_DEBUG_SPRITE_SCALE"

# === DIAGNOSTIC PROBES for sprite head-gap debugging ===
# Uncomment ONE at a time to test:
#   -DASCIICKER_DEBUG_DEPTH_BYPASS    # Probe 1: Skip ALL depth checks (isolates depth vs sampling)
#   -DASCIICKER_DEBUG_FORCE_SCALE_1   # Probe 2: Force scale=1.0 (isolates downscale issues)
#   -DASCIICKER_DEBUG_GAP_LOG         # Probe 3: Log rows in upper sprite zone (finds culled rows)
#   -DASCIICKER_DEBUG_SPRITE_DEPTH    # Original verbose depth logging (WARNING: kills FPS)

# (moved to active position above for FL-017 investigation)

"$ASCIICKER_BUILD_PYTHON" scripts/check_web_diagnostic_isolation.py web/game_web.html

# CRITICAL FIX: EXPORTED_RUNTIME_METHODS below MUST include "HEAPU8"
# This exports Module.HEAPU8 so JavaScript can access WASM memory
# Without it, the render buffer can't be read and screen stays black
# See line 115: -s EXPORTED_RUNTIME_METHODS='["ccall", "cwrap", "HEAPU8"]'

# [DEPENDENCY:EMSCRIPTEN] -- requires emcc from ~/emsdk (see setup instructions above)
#
# WHY (emcc flags reference):
#   --shell-file web/game_web.html
#       Overrides Emscripten's default HTML template with our custom web/game_web.html,
#       which contains the <canvas>, input event hooks, and the JS glue that calls
#       the EXPORTED_FUNCTIONS below.
#
#   -s EXPORTED_FUNCTIONS='[...]'
#       Lists every C function callable from JS. These are the game engine entry
#       points: _Load (init), _Render (frame), _Size (resize),
#       _Keyb/_Mouse/_Touch/_GamePad (input), _Join/_Packet (network),
#       _Audio/_Sample/_XOgg (sound), _akAPI_Call (generic API bridge).
#       _malloc/_free are needed so JS can allocate/free WASM heap memory.
#
#   -s EXPORTED_RUNTIME_METHODS='["ccall", "cwrap", "HEAPU8"]'
#       Exposes Emscripten runtime helpers to JS.
#       ccall/cwrap: call C functions from JS by name with automatic type marshaling.
#       HEAPU8: typed array view into WASM linear memory, required for reading the
#       software-rendered framebuffer from JS and painting it to <canvas>.
#
#   -s ALLOW_MEMORY_GROWTH=1
#       Lets the WASM heap grow beyond its initial size at runtime. Needed because the
#       game dynamically loads large assets (maps, sprites, meshes) whose total size is
#       unpredictable at compile time.
#
#   -s NO_EXIT_RUNTIME=1
#       Prevents Emscripten from tearing down the runtime when main() returns. The game
#       uses a browser requestAnimationFrame loop driven from JS, so the WASM module
#       must stay alive after main() exits.
#
#   -lidbfs.js
#       Links the Emscripten IndexedDB filesystem backend, enabling persistent
#       save-game storage in the browser via the IDBFS virtual filesystem.

# FL-1148: verifier gameplay mutators were intentionally removed from the public WASM export surface.
_start_spinner "Compiling WASM (this takes ~60s)"
emcc $INDEX_OPTIMIZE $INDEX_SAFETY $INDEX_AUDIO \
    -I. \
    -Iengine \
    -Iengine/render \
    -Iplatform \
    -Iserver \
    -Iweb \
    engine/stb_vorbis.cpp \
    engine/font1.cpp \
    engine/gamepad.cpp \
    engine/game.cpp \
    engine/network_ingest_dispatch.cpp \
    engine/network_ingest_join.cpp \
    engine/network_ingest_appearance.cpp \
    engine/network_ingest_snapshot.cpp \
    engine/network_ingest_items.cpp \
    engine/network_ingest_combat.cpp \
    engine/network_ingest_chat.cpp \
    engine/network_ingest_lag.cpp \
    engine/game_input.cpp \
    engine/local_player_state.cpp \
    engine/local_player_authority.cpp \
    engine/game_debug_telemetry.cpp \
    engine/debug_telemetry_state.cpp \
    engine/game_inventory_actions.cpp \
    engine/game_menu_ui.cpp \
    engine/game_utility.cpp \
    engine/game_appearance_client.cpp \
    engine/game_combat_client.cpp \
    engine/game_render_bridge.cpp \
    engine/server_authority.cpp \
    engine/server_connection.cpp \
    engine/a3d_load_context.cpp \
    engine/enemy_recolor_palette.cpp \
    engine/render/minimap_renderer.cpp \
    engine/sprite_registry.cpp \
    engine/snapshot_client/remote_authoritative_snapshot.cpp \
    engine/snapshot_client/snapshot_entity_decoder.cpp \
    engine/snapshot_client/remote_snapshot_presentation_track.cpp \
    engine/snapshot_client/local_authoritative_snapshot.cpp \
    engine/remote_observer_probe.cpp \
    engine/remote_actor_roster.cpp \
    engine/remote_authoritative_presentation_lifecycle.cpp \
    engine/remote_mounted_witness.cpp \
    engine/snapshot_client/snapshot_stream_applier.cpp \
    engine/authoritative_presentation_adapters.cpp \
    engine/authoritative_item_query_surface.cpp \
    engine/authoritative_item_command_surface.cpp \
    engine/authoritative_world_item_appearance.cpp \
    engine/authoritative_world_item_pickup_strip.cpp \
    engine/snapshot_client/local_snapshot_presentation_track.cpp \
    engine/snapshot_client/snapshot_npc_repository.cpp \
    engine/snapshot_client/snapshot_npc_visual_lifecycle.cpp \
    engine/weather.cpp \
    engine/mainmenu.cpp \
    engine/material_glyph_plane.cpp \
    engine/material_sidecar.cpp \
    engine/scripting/script_state_queries.cpp \
    engine/scripting/script_runtime_api.cpp \
    engine/scripting/script_intent_bridge.cpp \
    engine/scripting/script_action_requests.cpp \
    engine/game_api.cpp \
    web/web_recorder_bridge.cpp \
    web/web_filesystem.cpp \
    web/web_diagnostics.cpp \
    web/web_network_client.cpp \
    web/web_platform.cpp \
    web/web_disconnect_witness.cpp \
    net/transport/websocket_client_transport_web.cpp \
    web/game_web.cpp \
    engine/enemygen.cpp \
    server/mp_move.cpp \
    server/mp_step.cpp \
    engine/world_core.cpp \
    engine/world_instance.cpp \
    engine/world_editor_ops.cpp \
    engine/world_query.cpp \
    engine/world_serialization_a3d.cpp \
    engine/world_mesh.cpp \
    engine/world_tick.cpp \
    engine/world_minimap_markers.cpp \
    engine/inventory.cpp \
    engine/terrain.cpp \
    engine/sprite.cpp \
    engine/glyph_sidecar.cpp \
    engine/glyph_plane.cpp \
    engine/glyph_coverage_lookup.cpp \
    engine/glyph_compositor.cpp \
    engine/glyph_manifest.cpp \
    engine/third_party/cjson/cJSON.c \
    engine/physics.cpp \
    engine/physics_query.cpp \
    engine/physics_commands.cpp \
    engine/render/render_core.cpp \
    engine/render/render_projection.cpp \
    engine/render/render_scene.cpp \
    engine/render/render_debug_observation.cpp \
    engine/render/render_observation_builder.cpp \
    engine/render/render_hud_overlay.cpp \
    engine/render/render_stage_shadow.cpp \
    engine/render/render_resolve.cpp \
    engine/render/render_sprite_blit.cpp \
    engine/render/render_world_pass.cpp \
    engine/rgba8.cpp \
    engine/audio.cpp \
    engine/upng.c \
    engine/tinfl.c \
    -o .web/index.html \
    --shell-file web/game_web.html \
    -s EXPORTED_FUNCTIONS='["_malloc","_free","_main","_Load","_SetNetSeed","_SetRequestedA3dPath","_GetAppearanceContractJoinV2Json","_SetAppearanceContractRejectReason","_SetAppearanceContractServerHashes","_Render","_Size","_Keyb","_Mouse","_Touch","_Focus","_GamePad","_Join","_Packet","_SetRespawnItemRefreshBatchMode","_ClientObservationJsonV1","_GameWorldReady","_GameAuthoritativeWorldReady","_GameAuthoritativeWorldReadyMissingMask","_MainMenuWebGameLoadingState","_MainMenuWebProgressState","_GetRenderStageCode","_GetCppAnsiFrameSnapshotJson","_GetActorWearableProofProbeJson","_GetGlyphSidecar","_GetGlyphSidecarW","_GetGlyphSidecarH","_GlyphSidecarTestInject","_FL4131RecordFallbackRenderEvent","_ResetRemoteVisibilityLatches","_WebSocketClientTransportWeb_Reset","_WebSocketClientTransportWeb_OnOpen","_WebSocketClientTransportWeb_OnMessage","_WebSocketClientTransportWeb_OnMessageMeta","_WebSocketClientTransportWeb_OnClose","_WebSocketClientTransportWeb_OnError","_WebSocketClientTransportWeb_OnBackpressure","_Audio","_Sample","_XOgg","_SwitchToScriptProcessorMode","_GetAudioMode","_AudioRestoreForestAmbient","_AudioDebugPlayJump","_AudioDebugStateJson","_akAPI_Call"]' \
    -s EXPORTED_RUNTIME_METHODS='["ccall", "cwrap", "HEAPU8", "HEAP16"]' \
    -s ALLOW_MEMORY_GROWTH=1 \
    -s NO_EXIT_RUNTIME=1 \
    -s MINIFY_HTML=0 \
    -lidbfs.js \
    "${PRELOAD_ARGS[@]}"

# SAFARI!
#    -msimd128
emcc_status=$?
_stop_spinner
if [ $emcc_status -ne 0 ];
then
    exit $emcc_status
fi

# Fail closed: never publish a partial web artifact.
for required in .web/index.html .web/index.js .web/index.wasm .web/index.data; do
    if [ ! -f "$required" ]; then
        say_error "ERROR: missing web build output: $required"
        exit 1
    fi
done

echo ""
say_header "STAGING site (icon png, manifest json, service worker js)..."
_start_spinner "Staging assets and computing hashes"

cp web/favicon.ico .web/favicon.ico
cp web/favicon-16x16.png .web/favicon-16x16.png
cp web/favicon-32x32.png .web/favicon-32x32.png
cp web/apple-touch-icon.png .web/apple-touch-icon.png
cp web/android-chrome-192x192.png .web/android-chrome-192x192.png
cp web/android-chrome-512x512.png .web/android-chrome-512x512.png
cp web/site.webmanifest .web/site.webmanifest
cp web/asciicker.png .web/asciicker.png
cp web/asciicker.json .web/asciicker.json
cp web/asciicker.js .web/asciicker.js
if [ ! -f docs/player-guide.md ]; then
    say_error "ERROR: missing player guide: docs/player-guide.md"
    exit 1
fi
cp docs/player-guide.md .web/player-guide.md
# CP437 bitmap fonts are loaded at runtime via <Image> (not preloaded into WASM VFS)
cp -r assets/fonts/. .web/fonts
# FL-4131: compiled extended glyph atlases are fetched by browser WebGL binding,
# so they must exist as HTTP-served files in addition to the wasm preload VFS.
# P1+ ships the full cell-size ladder so the web client picks the page matching
# its current CP437 cell size; P9 binds them by default (no URL query gate).
mkdir -p .web/assets/glyphs/atlases
cp assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json .web/assets/glyphs/atlases/
cp assets/glyphs/atlases/material.additive.v1.lut_rgba8.json .web/assets/glyphs/atlases/
cp assets/glyphs/atlases/material.additive.v1.page0_rgba8.json .web/assets/glyphs/atlases/
for sz in 4 6 8 10 12 14 16 18 20 24 28 32 36 40; do
  src="assets/glyphs/atlases/material.additive.v1.page${sz}_rgba8.json"
  if [ -f "$src" ]; then
    cp "$src" .web/assets/glyphs/atlases/
  fi
done

WEB_BUILD_VERSION="$(node <<'EOF'
const crypto = require('crypto');
const fs = require('fs');

const versionHasher = crypto.createHash('sha256');
for (const rel of ['.web/index.js', '.web/index.wasm', '.web/index.data']) {
  versionHasher.update(fs.readFileSync(rel));
}
process.stdout.write(versionHasher.digest('hex').slice(0, 16));
EOF
)"

node - "$WEB_BUILD_VERSION" <<'EOF'
const fs = require('fs');

const version = process.argv[2];
const htmlPath = '.web/index.html';
let html = fs.readFileSync(htmlPath, 'utf8');

if (!html.includes('__AK_WEB_BUILD_VERSION__')) {
  throw new Error('missing __AK_WEB_BUILD_VERSION__ placeholder in .web/index.html');
}
if (!html.includes('src="index.js"')) {
  throw new Error('missing Emscripten loader script tag in .web/index.html');
}

html = html.replace(/__AK_WEB_BUILD_VERSION__/g, version);
html = html.replace(/src="index\.js"/g, `src="index.js?v=${version}"`);

fs.writeFileSync(htmlPath, html);
console.log(`Stamped .web/index.html with web build version ${version}`);
EOF

_SLOT_MANIFEST_EXTRA_FLAGS=""
if [ "${WATCHDOG_ALLOW_MISSING_SERVER:-}" = "1" ]; then
    _SLOT_MANIFEST_EXTRA_FLAGS="--allow-missing-server"
fi
node ./scripts/generate_watchdog_slot_manifest.js \
    --repo-root . \
    --slot-name "${WATCHDOG_SLOT_NAME:-candidate}" \
    --machine-role "${WATCHDOG_MACHINE_ROLE:-candidate}" \
    --web-dir .web \
    --server-path .run/server \
    --runtime-root . \
    --config-file web/asciicker.json \
    --output .web/slot_manifest.json \
    $_SLOT_MANIFEST_EXTRA_FLAGS

_stop_spinner

echo ""
# emrun --no_browser --port 8888 .web/index.html

exit $?
