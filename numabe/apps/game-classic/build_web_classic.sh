#!/bin/bash
# build_web_classic.sh - WebAssembly build of the Classic CP437 renderer product.
#
# PURPOSE: compile the exact renderer source set of
# apps/game-classic/makefile_game_classic_mac (SRCS) with emcc into
# .web-classic/index.js + index.wasm (+ a minimal index.html). The SRCS list is
# READ from that makefile, never copied, so the web product cannot drift from
# the native Classic product. Only the native host/presenter translation units
# are swapped for web equivalents:
#   platform/gl.c, platform/gl45_emu.cpp, platform/backend/host_sdl2.cpp,
#   platform/terminal_gl_present.cpp, engine/game_app.cpp (V8 + SDL host)
#     -> apps/game-classic/web_classic_main.cpp (C API) and
#        apps/game-classic/web_classic_stubs.cpp (host symbol stubs).
# None of the "Excluded Unicode renderer owners" are linked; the script fails
# if any of them appears in the resolved source list.
#
# DOES NOT TOUCH build-web.sh or .web/ (the FL-4512 Unicode web product).
#
# Usage: bash apps/game-classic/build_web_classic.sh   (or: make web-classic)
# Env:   WEB_CLASSIC_JOBS=N  parallel compile jobs (default 8)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [ -f "$HOME/emsdk/emsdk_env.sh" ]; then
    source "$HOME/emsdk/emsdk_env.sh" > /dev/null 2>&1 || true
fi

PINNED="$(tr -d '[:space:]' < .emscripten-version)"
ACTUAL="$(emcc --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
if [ "$ACTUAL" != "$PINNED" ]; then
    echo "ERROR: emcc ${ACTUAL:-missing} but .emscripten-version pins ${PINNED}" >&2
    exit 1
fi

MAKEFILE="apps/game-classic/makefile_game_classic_mac"
OUT=".web-classic"
OBJ="$OUT/obj"
JOBS="${WEB_CLASSIC_JOBS:-8}"
mkdir -p "$OBJ"
export TMPDIR="$PWD/.tmp-ems"
mkdir -p "$TMPDIR"

# Native-only translation units replaced by web equivalents.
NATIVE_SWAP=" platform/gl.c platform/gl45_emu.cpp platform/backend/host_sdl2.cpp platform/terminal_gl_present.cpp engine/game_app.cpp "
WEB_SRCS="apps/game-classic/web_classic_main.cpp apps/game-classic/web_classic_stubs.cpp"

# Extract SRCS (continuation list) from the makefile.
RAW="$(awk '/^SRCS[ \t]*:=/{on=1; sub(/^SRCS[ \t]*:=/, "")} on{ line=$0; cont=(line ~ /\\[ \t]*$/); sub(/\\[ \t]*$/, "", line); gsub(/[ \t]+/, " ", line); print line; if(!cont) exit }' "$MAKEFILE")"

SRCS=()
for f in $RAW; do
    case "$NATIVE_SWAP" in *" $f "*) continue ;; esac
    SRCS+=("$f")
done
for f in $WEB_SRCS; do SRCS+=("$f"); done

# Forbidden Unicode renderer owners must be absent.
for f in "${SRCS[@]}"; do
    case "$f" in
    apps/fl4512-runtime/*|*fl4512*|*fl4683*|*fl4693*|*fl4131_runtime*|*fl4131_harri*)
        echo "ERROR: forbidden Unicode renderer owner in Classic web link: $f" >&2; exit 1 ;;
    esac
done

# No -pthread: the wasm module is single-threaded.
CFLAGS_COMMON=(-O2 -fno-exceptions -DGAME -DASCIICKER_RENDERER_CLASSIC_CP437=1
    -I. -Iengine -Iengine/render -Ieditor -Iplatform -Iserver -Iweb)

compile_one() {
    local src="$1"
    local o="$OBJ/${src//\//__}.o"
    if [ -f "$o" ] && [ "$o" -nt "$src" ] && [ -z "${WEB_CLASSIC_FORCE:-}" ] && \
       [ -z "$(find engine platform server apps/classic-runtime apps/game-classic -name '*.h' -newer "$o" 2>/dev/null | head -1)" ]; then
        return 0
    fi
    case "$src" in
    *.c) emcc "${CFLAGS_COMMON[@]}" -c "$src" -o "$o" ;;
    *)   emcc -std=c++20 "${CFLAGS_COMMON[@]}" -c "$src" -o "$o" ;;
    esac
}

echo "Compiling ${#SRCS[@]} translation units with $JOBS jobs ..."
FAIL=0
export OBJ
export -f compile_one
export CFLAGS_STR="${CFLAGS_COMMON[*]}"
# xargs -P (portable to macOS bash 3.2, which lacks 'wait -n').
printf '%s\n' "${SRCS[@]}" | xargs -P "$JOBS" -I{} bash -c 'CFLAGS_COMMON=($CFLAGS_STR); compile_one "$1"' _ {} || FAIL=1
if [ "$FAIL" -ne 0 ]; then
    echo "ERROR: one or more translation units failed to compile" >&2
    exit 1
fi

OBJS=()
for src in "${SRCS[@]}"; do OBJS+=("$OBJ/${src//\//__}.o"); done

EXPORTS='["_malloc","_free","_ClassicLoadMap","_ClassicSetCamera","_ClassicGetCameraJson","_ClassicRender","_ClassicPickJson","_ClassicProjectJson","_ClassicCellStride","_ClassicSetLoadOptions","_ClassicBakeTerrainDark","_ClassicGroundJson","_ClassicSetMinimap","_ClassicPreviewSpriteAdd","_ClassicPreviewSpriteRemove","_ClassicSetInstVisible","_ClassicSetSpriteVisible"]'

echo "Linking $OUT/index.js ..."
rm -f "$OUT/index.js" "$OUT/index.wasm" "$OUT/index.html"
if ! emcc -O2 -fno-exceptions "${OBJS[@]}" -Wl,--error-limit=0 \
    -o "$OUT/index.js" \
    -s ALLOW_MEMORY_GROWTH=1 \
    -s INITIAL_MEMORY=268435456 \
    -s STACK_SIZE=8388608 \
    -s ENVIRONMENT=web,node \
    -s FILESYSTEM=1 \
    -s MODULARIZE=0 \
    -s NO_EXIT_RUNTIME=1 \
    -s EXPORTED_RUNTIME_METHODS='["FS","cwrap","ccall","HEAPU8"]' \
    -s EXPORTED_FUNCTIONS="$EXPORTS" \
    --preload-file assets/sprites --preload-file assets/palettes/palette.gz \
    --preload-file assets/images/menu.png 2> "$OUT/link.log"; then
    echo "ERROR: link failed; unique undefined symbols (full log: $OUT/link.log):" >&2
    grep -o 'undefined symbol: .*' "$OUT/link.log" | sort -u >&2
    grep -v 'undefined symbol\|^emcc: error' "$OUT/link.log" | head -20 >&2
    exit 1
fi

cp apps/game-classic/web_classic_index.html "$OUT/index.html"

for required in index.js index.wasm index.html; do
    [ -f "$OUT/$required" ] || { echo "ERROR: missing $OUT/$required" >&2; exit 1; }
done
echo "OK: $OUT/index.js $OUT/index.wasm $OUT/index.html"
