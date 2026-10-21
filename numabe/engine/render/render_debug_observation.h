#pragma once

// render_debug_observation.h — Engine adapters for render report helpers
//
// SEE ALSO: render_debug_observation.cpp

#include "render_frame_report.h"

struct SampleBuffer;
struct SpriteRenderBuf;

TrackedRemoteClampReport BuildTrackedRemoteClampReportForSprite(
	const SampleBuffer& sample_buffer,
	const SpriteRenderBuf* buf,
	int width, int height);
