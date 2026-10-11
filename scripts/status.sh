#!/bin/bash
# status.sh — Probe environment and print status banner.
# Usage: bash scripts/status.sh [--quiet]
# Called by: make status, make setup (at end), stale-stamp check
#
# When sourced with --quiet (e.g. source scripts/status.sh --quiet),
# only the probe functions are loaded — no banner is printed.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSET_ROOT="$REPO_ROOT/assets"

# Colors (disabled if not a terminal or if piped)
if [ -t 1 ]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'; BOLD='\033[1m'
else
    GREEN=''; RED=''; YELLOW=''; NC=''; BOLD=''
fi

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

# --- Platform detection ---
detect_platform() {
    case "$(uname -s)" in
        Darwin)
            if [ "$(uname -m)" = "arm64" ]; then echo "macOS (ARM64)"
            else echo "macOS (Intel)"; fi ;;
        Linux)  echo "Linux ($(uname -m))" ;;
        MINGW*|MSYS*) echo "Windows (MSYS/MinGW)" ;;
        *)      echo "Unknown ($(uname -s))" ;;
    esac
}

# --- Dependency probes (return 0=found, 1=missing) ---
has_sdl2()       { pkg-config --exists sdl2 2>/dev/null || brew list sdl2 &>/dev/null 2>&1; }
has_v8()         { [ -f "$REPO_ROOT/v8/v8/out.gn/x64.release/obj/libv8_monolith.a" ] \
                   || [ -f "$REPO_ROOT/v8/v8/out.gn/arm64.release/obj/libv8_monolith.a" ] \
                   || brew list v8 &>/dev/null 2>&1; }
has_python()     { command -v python3 &>/dev/null; }
has_venv()       { [ -d "$REPO_ROOT/.venv" ] && [ -f "$REPO_ROOT/.venv/bin/python3" ]; }
has_blender()    { blender_version &>/dev/null; }
has_emscripten() { command -v emcc &>/dev/null || [ -f "$HOME/emsdk/emsdk_env.sh" ]; }
has_mcp_venv()   { [ -d "$REPO_ROOT/docs/agent/.mcp/venv" ] && [ -f "$REPO_ROOT/docs/agent/.mcp/venv/bin/python3" ]; }

# --- Binary probes ---
has_editor()    { [ -x "$REPO_ROOT/.run/asciiid" ]; }
has_server()    { [ -x "$REPO_ROOT/.run/server" ]; }
has_game()      { [ -x "$REPO_ROOT/.run/game" ]; }
has_game_term() { [ -x "$REPO_ROOT/.run/game_term" ]; }

# --- Asset probes ---
has_map()      { [ -f "$ASSET_ROOT/a3d/game_map_y8.a3d" ]; }
has_font()     { [ -f "$ASSET_ROOT/fonts/cp437_12x12.png" ] && [ -s "$ASSET_ROOT/fonts/cp437_12x12.png" ]; }
has_sprites()  { [ -d "$ASSET_ROOT/sprites" ] && ls "$ASSET_ROOT/sprites/"*.xp &>/dev/null 2>&1; }
has_meshes()   { [ -d "$ASSET_ROOT/meshes" ] && ls "$ASSET_ROOT/meshes/"*.akm &>/dev/null 2>&1; }
has_fixtures() { [ -d "$REPO_ROOT/tests/fixtures/real_assets" ]; }

# --- GPL deps ---
has_gpl_deps() {
    [ -f "$REPO_ROOT/addons/blender_addons_4_5/PixelArtAddon_v_3_1.py" ]
}

# --- Portable SHA-256 (shasum on macOS, sha256sum on Linux) ---
sha256_of() {
    if command -v sha256sum &>/dev/null; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

# --- Pipeline probe ---
has_pipeline() {
    has_venv && PYTHONPATH="$REPO_ROOT" "$REPO_ROOT/.venv/bin/python3" -c "from scripts.pipeline import cli" 2>/dev/null
}

# --- Blender helpers ---

# Derive the required Blender version from the addon directory name.
# addons/blender_addons_4_5/ → "4.5"   addons/blender_addons_4_5_1/ → "4.5.1"
blender_required_version() {
    local dir ver
    dir=$(ls -d "$REPO_ROOT/addons/blender_addons_"*/ 2>/dev/null | sort -V | tail -1) || return 1
    [ -z "$dir" ] && return 1
    ver=$(basename "$dir" | sed 's/^blender_addons_//; s/_/./g')
    echo "$ver"
}

blender_version() {
    local req_ver pver app bin
    req_ver=$(blender_required_version 2>/dev/null) || req_ver=""
    # Scan all Blender*.app bundles in /Applications (handles stub .apps and generic Blender.app)
    for app in /Applications/Blender*.app; do
        bin="$app/Contents/MacOS/Blender"
        [ -x "$bin" ] || continue
        pver=$("$bin" --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
        [ -z "$pver" ] && continue
        if [ -n "$req_ver" ]; then
            [ "$pver" = "$req_ver" ] && echo "$pver" && return 0
        else
            echo "$pver" && return 0
        fi
    done
    # Fall back to PATH blender
    if command -v blender &>/dev/null; then
        pver=$(blender --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
        [ -z "$pver" ] && return 1
        if [ -n "$req_ver" ]; then
            [ "$pver" = "$req_ver" ] && echo "$pver" && return 0
            return 1
        fi
        echo "$pver" && return 0
    fi
    return 1
}
blender_addon_dir() {
    local ver="$1"
    case "$(uname -s)" in
        Darwin) echo "$HOME/Library/Application Support/Blender/$ver/scripts/addons" ;;
        Linux)  echo "$HOME/.config/blender/$ver/scripts/addons" ;;
        *)      echo "$HOME/.blender/$ver/scripts/addons" ;;
    esac
}
blender_addon_profile() {
    local ver="${1:-}"
    case "$ver" in
        2.86*) echo "legacy-2.86" ;;
        4.*)   echo "unified-4.x" ;;
        *)     echo "unknown" ;;
    esac
}
blender_required_addons() {
    local ver="${1:-}"
    case "$(blender_addon_profile "$ver")" in
        legacy-2.86)
            printf '%s\n' \
                "io_mesh_akm" \
                "akm_curve_volumizer.py" \
                "any_obj_vtex_color.py" \
                "vertex_coloring_building.py"
            ;;
        *)
            printf '%s\n' \
                "io_asciicker" \
                "blender_mcp_addon.py"
            ;;
    esac
}
blender_legacy_286_addons() {
    printf '%s\n' \
        "io_mesh_akm" \
        "akm_curve_volumizer.py" \
        "any_obj_vtex_color.py" \
        "vertex_coloring_building.py"
}
has_addon_symlink() {
    has_blender || return 1
    local ver dir addon
    ver=$(blender_version 2>/dev/null) || return 1
    [ -n "$ver" ] || return 1
    dir=$(blender_addon_dir "$ver")
    while IFS= read -r addon; do
        [ -n "$addon" ] || continue
        [ -L "$dir/$addon" ] || return 1
    done < <(blender_required_addons "$ver")
    return 0
}
has_blosm_addon() {
    has_blender || return 1
    local ver dir
    ver=$(blender_version 2>/dev/null) || return 1
    dir=$(blender_addon_dir "$ver")
    [ -d "$dir" ] || return 1
    find "$dir" -maxdepth 1 -iname 'blosm*' | grep -q .
}

# --- Repo version ---
repo_version() {
    git -C "$REPO_ROOT" describe --tags --always 2>/dev/null || echo "dev"
}

# --- Test counts ---
count_tests() {
    has_venv || { echo "?"; return; }
    "$REPO_ROOT/.venv/bin/python3" -m pytest tests/test_wave2_*.py \
        --collect-only -q --noconftest 2>/dev/null \
        | tail -1 | grep -oE '[0-9]+' | head -1 || echo "?"
}
count_maintainer_tests() {
    has_venv || { echo "?"; return; }
    "$REPO_ROOT/.venv/bin/python3" -m pytest scripts/maintainer/tests/ \
        --collect-only -q 2>/dev/null \
        | tail -1 | grep -oE '[0-9]+' | head -1 || echo "?"
}

# If sourced with --quiet, stop here — caller gets the probe functions only.
if [ "${1:-}" = "--quiet" ]; then
    return 0 2>/dev/null || true
fi

# =============================================================
# Banner
# =============================================================
PLATFORM=$(detect_platform)

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Asciicker Y9 — $(repo_version)${NC}"
echo -e "${BOLD}  Platform: $PLATFORM${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
echo ""

echo -e "${BOLD}  Binaries${NC}"
has_editor    && ok "Editor       → .run/asciiid"       || fail "Editor       → run: make editor"
has_server    && ok "Server       → .run/server"        || fail "Server       → run: make server"
has_game      && ok "Game         → .run/game"          || fail "Game         → needs V8  (make setup-v8)"
has_game_term && ok "Terminal     → .run/game_term"     || fail "Terminal     → run: make terminal"
echo ""

echo -e "${BOLD}  Dependencies${NC}"
has_sdl2       && ok "SDL2"                                                       || fail "SDL2         → run: make setup"
has_python     && ok "Python 3 ($(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+'))" \
               || fail "Python 3 missing"
has_venv       && ok "Python venv  → .venv/"                                     || warn "Python venv  → run: make setup"
has_v8         && ok "V8"                                                         || warn "V8           → optional  (make setup-v8)"
if has_blender; then
    BL_VER=$(blender_version 2>/dev/null || echo '?')
    BL_PROFILE=$(blender_addon_profile "$BL_VER")
    ok "Blender $BL_VER"
    ok "Blender addons profile → $BL_PROFILE"
    if has_addon_symlink; then
        ok "Required Blender addons linked"
    else
        warn "Required Blender addons missing → run: python3 scripts/setup_addon.py"
    fi
    if has_blosm_addon; then
        ok "blosm addon"
    else
        warn "blosm addon  → optional for online OSM import"
    fi
else
    REQ_VER=$(blender_required_version 2>/dev/null || true)
    if [ -n "$REQ_VER" ]; then
        warn "Blender $REQ_VER  → required version not found (3D export)"
    else
        warn "Blender      → optional  (3D export)"
    fi
fi
has_emscripten && ok "Emscripten"                                                 || warn "Emscripten   → optional  (make web)"
echo ""

echo -e "${BOLD}  Assets${NC}"
has_map      && ok "World map    → assets/a3d/game_map_y8.a3d"                    || fail "World map    MISSING"
has_font     && ok "Fonts        → assets/fonts/"                                 || fail "Fonts        MISSING"
has_sprites  && ok "Sprites      → assets/sprites/"                               || fail "Sprites      MISSING"
has_meshes   && ok "Meshes       → assets/meshes/"                                || warn "Meshes       → no .akm files"
has_fixtures && ok "Test fixtures → tests/fixtures/"                              || warn "Fixtures     → run: make test"
echo ""

echo -e "${BOLD}  Pipeline & Tests${NC}"
has_pipeline && ok "Pipeline importable" || fail "Pipeline     → run: make setup"
if has_venv; then
    TC=$(count_tests)
    MC=$(count_maintainer_tests)
    ok "Tests: wave2=$TC  maintainer=$MC  → make test"
else
    warn "Tests        → venv missing, run: make setup"
fi
has_mcp_venv && ok "MCP venv     → docs/agent/.mcp/venv/" || warn "MCP venv     → optional  (make setup)"
echo ""

# Stale stamp warning
if [ -f "$REPO_ROOT/.setup-stamp" ]; then
    CURRENT_HASH=$(cat "$REPO_ROOT/requirements.txt" "$REPO_ROOT/requirements-dev.txt" 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
    SAVED_HASH=$(cat "$REPO_ROOT/.setup-stamp" 2>/dev/null)
    if [ "$CURRENT_HASH" != "$SAVED_HASH" ]; then
        echo -e "  ${YELLOW}⚠  Setup may be outdated — run 'make setup' (deps changed)${NC}"
        echo ""
    fi
fi

echo -e "  Run ${BOLD}make help${NC} for all commands."
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
echo ""
