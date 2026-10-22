// render_core.cpp — Renderer creation/destruction
//
// Extracted from engine/render.cpp.
// SEE ALSO: render.h, render_internal.h

#include "render_internal.h"

#include <new>

Renderer* CreateRenderer(uint64_t stamp)
{
	// Renderer contains value-owned STL state (including the FL-4693 temporal
	// track store), so its lifetime must run C++ constructors and destructors.
	Renderer* r = new (std::nothrow) Renderer();
	if (!r) return NULL;
	r->Init();
	r->stamp = stamp;
	return r;
}

void DeleteRenderer(Renderer* r)
{
	if (!r) return;
	r->Free();
	delete r;
}

// FL-4257 Phase 1 S6 — public-API wrapper. Forwards to the inline setter
// in render_internal.h so callers with only a forward-declared Renderer*
// can wire curvature without pulling render_internal.h.
void RendererSetCurvatureFromAuthoritative(Renderer* r,
	unsigned char present_wire,
	unsigned char kind)
{
	if (!r) return;
	r->SetCurvatureFromAuthoritative(present_wire, kind);
}

void RendererSetCurvatureFromPublished(Renderer* r,
	unsigned char present_wire,
	unsigned char kind,
	float kappa,
	const float anchor[3])
{
	if (!r) return;
	r->SetCurvatureFromPublished(present_wire, kind, kappa, anchor);
}

void RendererSetS3AuthoritativePose(Renderer* r,
	unsigned char valid,
	const double ground[3],
	const double east[3],
	float height,
	float yaw)
{
	if (!r) return;
	r->SetS3AuthoritativePose(valid, ground, east, height, yaw);
}

void RendererSetS3CameraView(Renderer* r,
	int first_person,
	float free_pitch)
{
	if (!r) return;
	r->SetS3CameraView(first_person, free_pitch);
}

void RendererResetObservePhase(Renderer* r)
{
	if (!r) return;
	r->pn_time = 0.0;
}

void RendererSetEnvironmentalAnimationEnabled(Renderer* r, int enabled)
{
	if (!r) return;
	r->environment_animation_enabled = enabled ? 1 : 0;
}

int RendererGetEnvironmentalAnimationEnabled(const Renderer* r)
{
	return r && r->environment_animation_enabled ? 1 : 0;
}

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
