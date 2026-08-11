#pragma once

// render_stage_shadow.h — Stage 4: player blob shadow
//
// SEE ALSO: render_stages.h, render_stage_shadow.cpp

struct Renderer;
struct Material;

// Project a radial player shadow onto the SampleBuffer by inverse-transforming
// nearby samples back to world space and attenuating diffuse within ~2 units.
void RenderStageShadow(
	Renderer* r,
	int dw, int dh,
	int width,
	const float pos[3],
	const int scene_shift[2],
	Material* matlib);
