#pragma once

// =============================================================================
// Render API — Public renderer interface
// =============================================================================
//
// PURPOSE:
// Single public front door for the renderer module. Include this header
// instead of reaching into internal render headers.
//
// Provides:
//   - AnsiCell, Material, MatCell — output cell and material types
//   - Renderer opaque handle
//   - RenderFrameInput, RenderFrameReport — per-frame bridge structs
//   - CreateRenderer / DeleteRenderer / Render
//   - ProjectCoords / UnprojectCoords2D / UnprojectCoords3D
//
// Internal implementation lives in engine/render/*.cpp.
// Do not include render_internal.h from outside the render module.

#include "render.h"
#include "render_frame_input.h"
#include "render_frame_report.h"
