// interaction_query.cpp — World interaction queries (nearby items, characters)
//
// Extracted from engine/render_scene.cpp: the old render-side item/character
// query collector is deleted. The gameplay-authoritative query lives in
// engine/interaction_query.h as inline functions using QueryWorldItems.
//
// This file exists so the seam is real (one object file in the build) even
// though the current query surface is header-only. When the query surface
// gains non-trivial implementation (e.g. QueryWorldCharacters), it lives here.

#include "interaction_query.h"
