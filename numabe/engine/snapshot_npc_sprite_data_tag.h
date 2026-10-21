#pragma once

#include <stdint.h>

// Snapshot NPC visuals publish a body-inst sentinel through SetInstSpriteData.
// Render must read the same tag so tracked-NPC proof follows the real body
// blit path instead of treating the sentinel as a Character*.
static constexpr uintptr_t kSnapshotNpcBodySpriteDataTag = 1;
