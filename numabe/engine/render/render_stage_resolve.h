// render_stage_resolve.h — Stage 6: 2x2 downsample + material lookup
//
// PURPOSE:
// The resolve stage is the highest-value extraction target because it
// contains the hardest-to-test material/grid/water/deferred-sprite/output
// logic. Once extracted here, it can be tested against a fixed SampleBuffer
// and expected AnsiCell output.

#pragma once

#include <stdint.h>

struct Renderer;
struct AnsiCell;
struct Material;

void RenderStageResolve(
	Renderer* r,
	AnsiCell* ptr,
	int width,
	int height,
	Material* matlib,
	const double inv_tm[16],
	float water);
