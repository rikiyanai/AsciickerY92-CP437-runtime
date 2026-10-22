#pragma once

// =============================================================================
// FL-4257 — world-model water globe (item 7 of the corrective contract).
//
// Under spherical curvature the world is CLOSED: the authored terrain
// (chart disc of radius <= pi*R by the server's R = circumradius/pi
// derivation) covers part of the ground sphere and WATER AT WATER LEVEL
// covers the entire rest of it — a closed ocean globe, world geometry with
// no edge. This module OWNS that geometry: its existence is decided by the
// server-published curvature (world model), its tessellation is built
// here, and the renderer merely projects the submitted triangles through
// the same dual-image owner used for terrain and meshes. It is NOT a
// screen fill, NOT a resolve fallback, and NOT render-owned closure: a
// build without this module simply has visible holes (measurable sky).
//
// A3D remains import payload only: nothing here reads map files. Inputs
// are the published kappa/anchor and water level.
// =============================================================================

struct Renderer;

// Submit the water sphere for the CURRENT image pass: a geodesic
// (octahedron-subdivision) tessellation of the entire ground sphere at
// water level, rasterized with projection-safe recursive subdivision —
// triangles split in embedded space until they project wholly inside one
// image at leaf size or are clipped at a hemisphere gate. Call once per
// image inside the dual-image loop.
void WorldS3SubmitWaterGlobe(Renderer* r, float water_native_z);
