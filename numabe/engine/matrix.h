#pragma once

// =============================================================================
// FL-4257 Phase 0 — Matrix dispatcher.
//
// matrix.h used to own every Euclidean primitive (Invert, MatProduct, Product,
// TransposeProduct, Rotation, CrossProduct, DotProduct, RayIntersectsTriangle,
// PlaneFromPoints, SphereIntersectTriangle, RandRot stub). Phase 0 moved that
// content to engine/matrix_euclidean.h verbatim and turned this file into a
// dispatcher so every existing #include "matrix.h" still resolves to the same
// Euclidean primitives.
//
// Curved-space primitives live in engine/matrix_curved.h. They are deliberately
// NOT re-exported by this header. A promotable runtime translation unit must
// include EITHER matrix_euclidean.h (legacy/dispatcher path) OR
// matrix_curved.h — never both. Dual inclusion would resurrect the multi-owner
// shape FL-4257 was opened to delete. scripts/analyze_runs.py
// audit-no-dual-curvature-include enforces the rule.
// =============================================================================

#include "matrix_euclidean.h"
