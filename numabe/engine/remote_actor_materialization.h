// remote_actor_materialization.h — Remote actor render-materialization
//
// PURPOSE:
// Remote actor materialization covers the render-side decisions for
// presenting remote players/actors: which sprite to blit, what pose,
// interpolation state, visibility. Extracted from engine/render_scene.cpp
// and engine/render_sprite_blit.cpp.
//
// Current state: remote actor materialization is still inline in the
// sprite blit loop (RenderSpriteBlit in render_sprite_blit.cpp). This
// header establishes the seam. Extraction requires splitting the main
// blit loop into callbacks that remote_actor_materialization.cpp can own.
//
// SEE ALSO:
// - engine/remote_actor_materialization.cpp — implementation (stub)
// - engine/render_sprite_blit.cpp — sprite blit loop (owns materialization)
// - engine/render_scene.cpp — Render() main frame function

#pragma once

#include <stdint.h>

struct Game;
struct SpriteRenderBuf;
struct Terrain;

// (API TBD) — remote actor materialization is still inline in the blit loop.
