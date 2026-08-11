#pragma once

#include <stdint.h>

#include "game.h"

struct Game;

uint16_t RebuildLocalAuthoritativeItemState(Game* game);
bool UseRespawnAuthoritativeItemBatch(Game* game);
void FlushPendingRespawnAuthoritativeItemRefresh(Game* game);
bool RequestUseAuthoritativeItemByIndex(Game* game, int index);
bool RequestPlaceAuthoritativeItemByIndex(Game* game, int index);
bool RequestPlaceEquippedPlaceableAuthoritativeItem(Game* game);
float GetAuthoritativePlaceDebugZOffset();
void AdjustAuthoritativePlaceDebugZOffset(Game* game, float delta_z);
bool RequestPickupAuthoritativeWorldItemByListIndex(Game* game, int index);
void UpdateMobileAutoPickup(Game* game, uint64_t stamp);
