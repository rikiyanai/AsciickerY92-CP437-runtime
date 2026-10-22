// remote_actor_materialization.cpp — Remote actor render-materialization
//
// Extracted from engine/render_scene.cpp and engine/render_sprite_blit.cpp.
// Owns the render-side remote actor materialization logic:
// - Which sprite to blit for a remote actor
// - Pose interpolation for remote actors
// - Remote actor visibility determination
//
// Current state: still inline in the sprite blit loop (RenderSpriteBlit).
// See remote_actor_materialization.h for the planned API.

#include "remote_actor_materialization.h"
