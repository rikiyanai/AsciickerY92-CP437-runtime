// render_core.cpp — Renderer creation/destruction
//
// Extracted from engine/render.cpp.
// SEE ALSO: render.h, render_internal.h

#include "render_internal.h"

Renderer* CreateRenderer(uint64_t stamp)
{
	Renderer* r = (Renderer*)malloc(sizeof(Renderer));

	r->Init();
	r->stamp = stamp;
	return r;
}

void DeleteRenderer(Renderer* r)
{
	r->Free();
	free(r);
}

bool global_refl_mode = false;
int render_break_point[2] = { -1,-1 };

int SpriteRenderBuf::FarToNear(const void* a, const void* b)
{
	const SpriteRenderBuf* p = (const SpriteRenderBuf*)a;
	const SpriteRenderBuf* q = (const SpriteRenderBuf*)b;
	if (p->dist > q->dist) return -1;
	if (p->dist < q->dist) return 1;
	if (p->render_order_bias < q->render_order_bias) return -1;
	if (p->render_order_bias > q->render_order_bias) return 1;
	return 0;
}
