// harness_stubs.cpp — the few symbols the linked engine code needs that
// normally live in game_app.cpp / inventory.cpp / the platform layer.
// Nothing here affects how a frame is rendered.

#include "render_api.h"
#include "game.h"
#include <stdlib.h>
#include <string.h>
#include <time.h>

// game_app.cpp owns the global material table the renderer reads.
Material mat[256];
void* GetMaterialArr() { return mat; }

// platform timer (game_app.cpp under PURE_TERM). Only used for telemetry
// timings inside terrain.cpp.
uint64_t a3dGetTime()
{
	timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}

// inventory.cpp: world item records referenced by the A3D loader. Identical
// bodies; the full inventory.cpp drags in game/UI code.
Item* CreateItem()
{
	Item* item = (Item*)malloc(sizeof(Item));
	memset(item, 0, sizeof(Item));
	return item;
}

void DestroyItem(Item* item)
{
	if (item->inst)
		DeleteInst(item->inst);
	free(item);
}
