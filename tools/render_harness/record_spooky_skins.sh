#!/usr/bin/env bash
# Record turntable GIFs of the grandma / dracula / witch body sheets with the
# offscreen render harness. Render harness only: these sheets are loaded
# directly from assets/sprites, not through the game's actor profile tables.
#
#   tools/render_harness/record_spooky_skins.sh [frames_tmp_dir]
set -euo pipefail
cd "$(dirname "$0")/../.."

TMP="${1:-$(mktemp -d)}"
OUT=docs/recordings/spooky-skins
FPS=30
FRAMES=210   # 7 s

[ -f assets/a3d/preview_flat_green.a3d ] || python3 scripts/adhoc/2026-10-08-generate-preview-flat-green-map.py
make render-harness

clips=()
for char in grandma dracula witch; do
  ./.run/render_harness turntable \
    out="$TMP/$char" fps=$FPS frames=$FRAMES w=40 h=24 crop_bottom=8 \
    font=assets/fonts/cp437_16x16.png \
    map=assets/a3d/preview_flat_green.a3d \
    sprite=assets/sprites/2026-10-08-$char-body.xp
  python3 tools/render_harness/make_gif.py single "$TMP/$char" "$OUT/$char-turntable.gif" --fps $FPS
  clips+=("$char=$TMP/$char")
done
python3 tools/render_harness/make_gif.py combine "$OUT/all-turntable.gif" "${clips[@]}" --fps $FPS
echo "frames kept in $TMP"
