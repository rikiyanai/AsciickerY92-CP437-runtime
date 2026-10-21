// world_raycast.h — World raycast queries
//
// PURPOSE:
// Declares the public raycast API. The HitWorld() free-function wrapper
// lives in world_query.cpp. The inline implementation (World::HitWorld,
// HitWorld0-7, HitSprite overloads) is in world_internal.h.
//
// This header exists so callers that only need raycasting can include a
// narrow surface. Full extraction of the ~1600-line inline implementation
// from world_internal.h into world_raycast.cpp is a future step.
//
// SEE ALSO:
// - engine/world_query.cpp — HitWorld() free-function wrapper
// - engine/world_internal.h — inline implementation (HitWorld0-7, HitSprite)
// - engine/world.h — HitWorld/HitSprite declarations

#pragma once

// HitWorld is declared in world.h and implemented via:
//   world_query.cpp: free function → w->HitWorld(...)
//   world_internal.h: World::HitWorld(...) → sign-case dispatch to HitWorld0-7
//
// HitSprite (EDITOR only) is declared in world.h.
