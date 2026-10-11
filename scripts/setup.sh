#!/bin/bash
# setup.sh — Universal one-command setup for Asciicker.
# Usage: make setup  (or: bash scripts/setup.sh)
# Idempotent — safe to re-run.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSET_ROOT="$REPO_ROOT/assets"

# Load probe functions from status.sh (--quiet = no banner)
# shellcheck source=scripts/status.sh
source "$REPO_ROOT/scripts/status.sh" --quiet
set -euo pipefail  # re-assert: source may have altered shell options

# Colors
if [ -t 1 ]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'; BOLD='\033[1m'
else
    GREEN=''; RED=''; YELLOW=''; NC=''; BOLD=''
fi

step()  { echo -e "\n${BOLD}[$1] $2${NC}"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
fail()  { echo -e "  ${RED}✗${NC} $1"; }
skip()  { echo -e "  ${YELLOW}→${NC} skipped"; }
SETUP_FAILED=0

mark_failed() {
    fail "$1"
    SETUP_FAILED=1
}

ask() {
    local prompt="$1" default="${2:-y}"
    if [ "$default" = "y" ]; then
        echo -ne "  $prompt [Y/n] "
    else
        echo -ne "  $prompt [y/N] "
    fi
    read -r answer
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[Yy] ]]
}

PLATFORM=$(detect_platform)
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Asciicker Setup — $(repo_version)${NC}"
echo -e "${BOLD}  Platform: $PLATFORM${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"

# ========================================
# [0/8] Repo-local git config
# ========================================
step "0/8" "Repo-local git config"

git config core.hooksPath .githooks \
    && ok "Git hooks path → .githooks" \
    || mark_failed "Failed to set core.hooksPath"

git config merge.fl-append.name "Failure log append-only union merge" \
    && git config merge.fl-append.driver "scripts/fl-merge-driver.sh %O %A %B" \
    && ok "Failure-log merge driver configured" \
    || mark_failed "Failed to configure failure-log merge driver"

# ========================================
# [1/8] System dependencies
# ========================================
step "1/8" "System dependencies"

NPROC=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

case "$(uname -s)" in
Darwin)
    if has_sdl2; then
        ok "SDL2"
    elif ask "SDL2 not found. Install via Homebrew?" "y"; then
        brew install sdl2 && ok "SDL2 installed" || fail "SDL2 install failed"
    else
        skip
    fi

    if has_v8; then
        ok "V8"
    elif ask "V8 not found. Needed for game/terminal (not editor/server). Install via Homebrew?" "y"; then
        brew install v8 && ok "V8 installed" || fail "V8 install failed"
    else
        skip; echo "    Run 'make setup-v8' later for game support."
    fi
    ;;
Linux)
    MISSING_PKGS=""
    for pkg in libsdl2-dev libpulse-dev libx11-dev libxinerama-dev libgl1-mesa-dev; do
        dpkg -s "$pkg" &>/dev/null 2>&1 || MISSING_PKGS="$MISSING_PKGS $pkg"
    done
    if [ -z "$MISSING_PKGS" ]; then
        ok "System deps"
    else
        echo "  Missing:$MISSING_PKGS"
        if ask "Install via apt?" "y"; then
            # shellcheck disable=SC2086
            sudo apt install -y $MISSING_PKGS && ok "System deps installed" || fail "apt install failed"
        else
            skip
        fi
    fi

    if dpkg -s libgpm-dev &>/dev/null 2>&1; then
        ok "libgpm"
    elif ask "libgpm-dev not found. Needed for terminal game. Install?" "y"; then
        sudo apt install -y libgpm-dev && ok "libgpm installed" || fail "libgpm install failed"
    else
        skip
    fi

    if has_v8; then
        ok "V8"
    elif ask "V8 not found. Build from source? (~10 min)" "n"; then
        bash "$REPO_ROOT/scripts/setup_v8.sh" && ok "V8 built" || fail "V8 build failed"
    else
        skip; echo "    Run 'make setup-v8' later for game support."
    fi
    ;;
MINGW*|MSYS*)
    echo "  Windows detected. System deps must be installed manually."
    echo "  See README.md Prerequisites section."
    ;;
esac

# ========================================
# [2/8] Python environment
# ========================================
step "2/8" "Python environment"

if ! has_python; then
    fail "Python 3 not found. Install Python 3.10+ and re-run."
    exit 1
fi
_PY_VERSION=$(python3 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>/dev/null || echo "0.0")
_PY_MAJOR=$(echo "$_PY_VERSION" | cut -d. -f1)
_PY_MINOR=$(echo "$_PY_VERSION" | cut -d. -f2)
if [ "$_PY_MAJOR" -lt 3 ] || { [ "$_PY_MAJOR" -eq 3 ] && [ "$_PY_MINOR" -lt 10 ]; }; then
    fail "Python 3.10+ required (found $_PY_VERSION). Install Python 3.10+ and re-run."
    exit 1
fi
ok "Python 3 ($(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+'))"

if ! has_venv; then
    echo "  Creating .venv..."
    python3 -m venv "$REPO_ROOT/.venv"
    ok "Venv created"
else
    ok "Venv exists → .venv/"
fi

echo "  Installing Python dependencies (runtime + dev)..."
"$REPO_ROOT/.venv/bin/pip" install -q -r "$REPO_ROOT/requirements-dev.txt" \
    && ok "pip install complete (runtime + dev deps)" \
    || mark_failed "pip install failed"

# RQ-104: Playwright browser install (required for make test-web)
echo "  Installing Playwright chromium browser..."
"$REPO_ROOT/.venv/bin/python3" -m playwright install --with-deps chromium 2>/dev/null \
    && ok "Playwright chromium installed" \
    || warn "Playwright install failed — 'make test-web' may not work (elevated permissions may be required on Linux)"

# ========================================
# [3/8] GPL-licensed dependencies
# ========================================
step "3/8" "GPL-licensed Blender addons"

if [ -f "$REPO_ROOT/scripts/install_gpl_deps.sh" ]; then
    bash "$REPO_ROOT/scripts/install_gpl_deps.sh"
else
    warn "scripts/install_gpl_deps.sh not yet written — skipping GPL deps"
    echo "    This step will fetch PixelArtAddon and any Blender-2.86-only legacy addons once the script is added."
fi

# ========================================
# [4/8] Asset & path validation
# ========================================
step "4/8" "Asset & path validation"

has_map      && ok "World map    → assets/a3d/game_map_y8.a3d" \
             || fail "World map MISSING — game will crash on load!"
has_font     && ok "Font atlas   → assets/fonts/cp437_12x12.png" \
             || fail "Font atlas MISSING"
has_sprites  && ok "Sprites      → assets/sprites/ ($(ls "$ASSET_ROOT/sprites/"*.xp 2>/dev/null | wc -l | tr -d ' ') files)" \
             || fail "Sprites dir missing"
has_meshes   && ok "Meshes       → assets/meshes/ ($(ls "$ASSET_ROOT/meshes/"*.akm 2>/dev/null | wc -l | tr -d ' ') files)" \
             || warn "Meshes dir: no .akm files"
has_fixtures && ok "Test fixtures → tests/fixtures/" \
             || warn "Test fixtures not found"

# ========================================
# [5/8] Build targets
# ========================================
step "5/8" "Building available targets"

BUILD_LOG="$REPO_ROOT/.setup-build.log"
: > "$BUILD_LOG"  # truncate

build_target() {
    local label="$1" out="$2"; shift 2
    echo "  Building $label..."
    if make "$@" >> "$BUILD_LOG" 2>&1; then
        ok "$label built → $out"
    else
        mark_failed "$label build failed — see $BUILD_LOG"
    fi
}

case "$(uname -s)" in
Darwin)
    if has_sdl2; then
        build_target "editor"   ".run/asciiid"   -C "$REPO_ROOT" -f makefile_asciiid_mac   -j"$NPROC"
        build_target "server"   ".run/server"    -C "$REPO_ROOT" -f makefile_server        -j"$NPROC"
        if has_v8; then
            build_target "game"     ".run/game"      -C "$REPO_ROOT" -f makefile_game_mac      -j"$NPROC"
            build_target "terminal" ".run/game_term" -C "$REPO_ROOT" -f makefile_game_term_mac -j"$NPROC"
        else
            warn "Skipping game/terminal (V8 not installed)"
        fi
    else
        warn "Skipping builds (SDL2 not installed)"
    fi
    ;;
Linux)
    if has_sdl2; then
        build_target "editor"   ".run/asciiid"   -C "$REPO_ROOT" -f makefile_asciiid   -j"$NPROC"
        build_target "server"   ".run/server"    -C "$REPO_ROOT" -f makefile_server    -j"$NPROC"
        if has_v8; then
            build_target "game"     ".run/game"      -C "$REPO_ROOT" -f makefile_game      -j"$NPROC"
            build_target "terminal" ".run/game_term" -C "$REPO_ROOT" -f makefile_game_term -j"$NPROC"
        else
            warn "Skipping game/terminal (V8 not installed)"
        fi
    else
        warn "Skipping builds (SDL2 not installed)"
    fi
    ;;
MINGW*|MSYS*)
    echo "  Windows build not yet automated. See README.md."
    ;;
esac

# ========================================
# [6/8] Optional tools
# ========================================
step "6/8" "Optional tools"

if has_blender; then
    local_ver=$(blender_version 2>/dev/null || true)
    ok "Blender $local_ver found"
    if has_addon_symlink; then
        ok "Addons already symlinked"
    elif ask "Symlink repo addons into detected Blender installs?" "y"; then
        if python3 "$REPO_ROOT/scripts/setup_addon.py"; then
            ok "Blender addons linked via scripts/setup_addon.py"
            echo "    Blender $local_ver profile: $(blender_addon_profile "$local_ver")"
            echo "    Enable in Blender: Edit → Preferences → Add-ons → search 'asciicker'"
        else
            mark_failed "Blender addon linking failed"
        fi
    else
        skip
    fi
else
    REQ_VER=$(blender_required_version 2>/dev/null || true)
    if [ -n "$REQ_VER" ]; then
        warn "Blender $REQ_VER not found — required for 3D export (addons target $REQ_VER)"
        echo "    Install Blender $REQ_VER from https://www.blender.org/download/"
    else
        warn "Blender not found — 3D export features unavailable"
    fi
fi

if has_emscripten; then
    _EMVER_FILE="$(dirname "$(realpath "$0")")/../.emscripten-version"
    if [ -f "$_EMVER_FILE" ]; then
        _PINNED="$(tr -d '[:space:]' < "$_EMVER_FILE")"
        _ACTUAL="$(emcc --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)"
        if [ -z "$_ACTUAL" ]; then
            warn "Emscripten found but version string could not be parsed (expected $_PINNED)"
            echo "    Check: emcc --version"
        elif [ "$_ACTUAL" = "$_PINNED" ]; then
            ok "Emscripten ${_ACTUAL} (pinned version)"
        else
            warn "Emscripten version mismatch: found ${_ACTUAL}, pinned ${_PINNED} (.emscripten-version)"
            echo "    Fix: cd ~/emsdk && ./emsdk install ${_PINNED} && ./emsdk activate ${_PINNED}"
        fi
    else
        ok "Emscripten (version pin file .emscripten-version not found)"
    fi
else
    warn "Emscripten not found — web/WASM build unavailable"
    echo "    Install: https://emscripten.org/docs/getting_started/downloads.html"
fi

# RQ-032: Node.js 18+ is required for build-web.sh (web client compilation)
if command -v node &>/dev/null; then
    NODE_VER=$(node --version 2>/dev/null | grep -oE '[0-9]+' | head -1 || echo "0")
    if [ "$NODE_VER" -ge 18 ] 2>/dev/null; then
        ok "Node.js $(node --version) — web build available"
    else
        warn "Node.js $(node --version) found but 18+ required for web build (build-web.sh)"
        echo "    Upgrade: https://nodejs.org/en/download/"
    fi
else
    warn "Node.js not found — web build unavailable (build-web.sh requires Node.js 18+)"
    echo "    Install: https://nodejs.org/en/download/"
fi

if has_mcp_venv; then
    ok "MCP venv exists → docs/agent/.mcp/venv/"
elif ask "Create MCP server venv (docs/agent/.mcp/venv)? Needed for AI-assisted development." "n"; then
    python3 -m venv "$REPO_ROOT/docs/agent/.mcp/venv"
    "$REPO_ROOT/docs/agent/.mcp/venv/bin/pip" install -q mcp fastmcp
    ok "MCP venv created"
else
    skip
fi

# ========================================
# [7/8] Validation
# ========================================
step "7/8" "Validation"

if has_venv; then
    echo "  Checking pipeline import..."
    "$REPO_ROOT/.venv/bin/python3" -c "from scripts.pipeline import cli" 2>/dev/null \
        && ok "Pipeline importable" \
        || mark_failed "Pipeline import failed — run 'make test' for details"

    echo "  Collecting tests..."
    WAVE2=$("$REPO_ROOT/.venv/bin/python3" -m pytest tests/test_wave2_*.py \
        --collect-only -q --noconftest 2>/dev/null \
        | tail -1 | grep -oE '[0-9]+' | head -1 || echo "0")
    ok "wave2 tests: $WAVE2 collected"

    MAINT=$("$REPO_ROOT/.venv/bin/python3" -m pytest scripts/maintainer/tests/ \
        --collect-only -q 2>/dev/null \
        | tail -1 | grep -oE '[0-9]+' | head -1 || echo "0")
    ok "maintainer tests: $MAINT collected"
else
    warn "Venv missing — skipping validation"
fi

# ========================================
# [8/8] Write stamp + print banner
# ========================================
step "8/8" "Finalizing"

if [ "$SETUP_FAILED" -eq 0 ]; then
    cat "$REPO_ROOT/requirements.txt" "$REPO_ROOT/requirements-dev.txt" | shasum -a 256 | cut -d' ' -f1 > "$REPO_ROOT/.setup-stamp"
    ok "Setup stamp written → .setup-stamp"
else
    skip
    echo "    .setup-stamp not written because setup is incomplete."
fi

echo ""
bash "$REPO_ROOT/scripts/status.sh"

if [ "$SETUP_FAILED" -ne 0 ]; then
    echo ""
    fail "Setup incomplete — fix the failed steps above and re-run 'make setup'."
    exit 1
fi
