#!/usr/bin/env bash
# Record UFO Rapture Death GIFs (docs/specs/2026-10-08-ufo-rapture-death.md)
# with the offscreen render harness: 0.5 s idle, death tick 0 -> respawn at
# tick 180, then 0.5 s of the respawned idle; continuous 180-degree camera yaw.
# Render harness only (see scene_rapture.cpp for what is real feature code and
# what the harness stands in for).
#
# Frame: 48x28 output cells over a 36-row render (crop_bottom=8), i.e. the
# 18 rows above the centred feet that the 36-row minimum web viewport keeps,
# wide enough for the full beam footprint and shadow ring.
# all-rapture-no-ufo.gif re-runs every skin with no_ufo=1 (the harness
# stand-in for building with RAPTURE_UFO_VISIBLE 0: beam only, no saucer, no
# hull shadow); the compile-time default stays 1.
#
#   tools/render_harness/record_ufo_rapture.sh [frames_tmp_dir]
set -euo pipefail
cd "$(dirname "$0")/../.."

TMP="${1:-$(mktemp -d)}"
OUT=docs/recordings/ufo-rapture
FPS=30
LEAD=15                      # 0.5 s idle before death
FRAMES=$((LEAD + 180 + 15))  # death window (180 ticks) + 0.5 s respawned idle

[ -f assets/a3d/preview_flat_green.a3d ] || python3 scripts/adhoc/2026-10-08-generate-preview-flat-green-map.py
make render-harness

clips=()
for char in grandma dracula witch default; do
  if [ "$char" = default ]; then
    body=assets/sprites/player-body.xp
    death=assets/sprites/plydie-body.xp
  else
    body=assets/sprites/2026-10-08-$char-body.xp
    death=assets/sprites/2026-10-08-$char-plydie.xp
  fi
  ./.run/render_harness rapture \
    out="$TMP/$char" fps=$FPS frames=$FRAMES lead=$LEAD w=48 h=28 crop_bottom=8 cells=1 \
    font=assets/fonts/cp437_16x16.png \
    map=assets/a3d/preview_flat_green.a3d \
    body=$body death=$death
  python3 tools/render_harness/make_gif.py single "$TMP/$char" "$OUT/$char-rapture.gif" --fps $FPS
  clips+=("$char=$TMP/$char")
done
python3 tools/render_harness/make_gif.py combine "$OUT/all-rapture.gif" "${clips[@]}" --fps $FPS

# UFO hidden (beam only), every skin, combined like all-rapture.gif
noufo=()
for char in grandma dracula witch default; do
  if [ "$char" = default ]; then
    body=assets/sprites/player-body.xp
    death=assets/sprites/plydie-body.xp
  else
    body=assets/sprites/2026-10-08-$char-body.xp
    death=assets/sprites/2026-10-08-$char-plydie.xp
  fi
  ./.run/render_harness rapture \
    out="$TMP/$char-no-ufo" fps=$FPS frames=$FRAMES lead=$LEAD w=48 h=28 crop_bottom=8 cells=1 \
    font=assets/fonts/cp437_16x16.png \
    map=assets/a3d/preview_flat_green.a3d \
    body=$body death=$death no_ufo=1
  noufo+=("$char=$TMP/$char-no-ufo")
done
python3 tools/render_harness/make_gif.py combine "$OUT/all-rapture-no-ufo.gif" "${noufo[@]}" --fps $FPS
echo "frames kept in $TMP"
