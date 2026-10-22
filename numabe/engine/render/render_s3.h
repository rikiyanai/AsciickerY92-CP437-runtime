#pragma once

// =============================================================================
// FL-4257 corrective rebuild — the SINGLE 4D spherical render owner.
//
// render_s3.cpp is the one render-side translation unit on the
// matrix_curved.h side of the no-dual-include split. It consumes the
// SERVER-PUBLISHED curvature view cached on the Renderer (present, kind,
// derived physical kappa, chart anchor — intake: STRUCT_RSP_JOIN only) and
// owns:
//   - per-frame derivation of the camera S3 pose + tangent frame from the
//     server-published player state (RenderS3Setup),
//   - the projection (documented next to the code in render_s3.cpp; cited
//     math: Szirmay-Kalos & Magdics 2022): embed -> decompose on the camera
//     tangent basis (the SO(4) view rotation) -> gnomonic divide of the RAY
//     DIRECTION by its forward component; depth = geodesic arc length,
//   - the MANDATORY antipodal dual image: every primitive is submitted for
//     image 0 (forward hemisphere, depth = arc) and image 1 (the long way
//     around, depth = 2*pi - arc) under ONE arc-length depth policy,
//   - the inverse (screen + arc depth -> world chart, compat),
//   - rasterization of water-globe triangles SUBMITTED BY THE WORLD MODEL
//     (engine/world_s3.cpp owns the globe's existence and tessellation;
//     render only projects and fills, exactly as it does for meshes).
//
// This header carries declarations only (no curved math) so Euclidean TUs
// can dispatch through it without touching matrix_curved.h.
// =============================================================================

struct Renderer;
struct Terrain;

struct S3TerrainVertex
{
	float wx, wy, wz;
	float u, v;
};

// Normal native spherical terrain stage. This projects/rasterizes terrain on
// the GL side, then writes the winning terrain samples back into SampleBuffer
// so world, water, sprites, and resolve keep the existing depth contract.
int RenderS3SubmitTerrainGpu(Renderer* r, Terrain* t);

// Derive the frame's 4D view state from r->curvature + the server-published
// player position/yaw passed into Render(). Returns 1 = spherical active,
// 0 = Euclidean baseline (absent or kind 0). Unrecognised kinds set
// fail-closed state (every projection entry refuses, Law 6).
int RenderS3Setup(Renderer* r, const float player_pos[3], float player_yaw,
                  int dw, int dh, const int scene_shift[2]);

int RenderS3Active(const Renderer* r);
int RenderS3FailClosed(const Renderer* r);

// Select the image pass: 0 near image, 1 antipodal (long-way) image.
void RenderS3SetImage(Renderer* r, int image);

// Core projection: world-space point (x, y world cells, z native) ->
// sample-space fx/fy + viewer_dist (geodesic arc, world units) + encoded
// arc depth key. Returns 0 outside the current image's hemisphere.
int RenderS3ProjectWorld(const Renderer* r, float wx, float wy, float wz,
                         float* fx, float* fy, float* viewer_dist,
                         float* depth);

// Seam entries used by render_projection.cpp's dispatchers.
struct RendererSeamInt3 { int v[3]; };
bool RenderS3ProjectCoords(Renderer* r, const float pos[3], int view[3]);
bool RenderS3UnprojectCoords2D(Renderer* r, const int xy[2], float pos[3]);
bool RenderS3UnprojectCoords3D(Renderer* r, const int xyz[3], float pos[3]);
bool RenderS3ProjectWorldVertex(Renderer* r, float vx, float vy, float vz,
                                float* fx, float* fy, float* viewer_dist);
bool RenderS3ProjectFaceVertex(Renderer* r, float ws_x, float ws_y, float ws_z,
                               float* fx, float* fy, float* viewer_dist);

// Depth key of the most recent successful RenderS3ProjectWorldVertex /
// RenderS3ProjectFaceVertex on this renderer (single-threaded raster loop;
// read immediately after the call that produced it).
float RenderS3LastDepth(const Renderer* r);

// Presentation parity for far-side S3 samples. Non-zero means the sample is
// beyond the sight equator and directional final glyphs/sprites must be
// vertically inverted in presentation.
int RenderS3DepthOrientationParity(float depth_key);

// Near-gate blow-up guard for integer screen triangles (patch cells, mesh
// faces): vertices that barely pass a hemisphere gate project toward screen
// infinity, and a linear rasterizer would fill a giant near-depth wedge.
// Returns 1 when the triangle must be skipped (NEAR triangle with an absurd
// screen span; far-side rim quads keep their legitimately huge images).
int RenderS3TriBlowup(const Renderer* r, const int* a, const int* b, const int* c);

// Terrain subdivision seam. Splits a terrain edge on S3 embedding, then maps
// the midpoint back to the chart payload used by the existing patch rasterizer.
// This keeps conjugate-region refinement on the spherical owner instead of
// reviving chart-linear midpoint ownership in render_world_pass.cpp.
void RenderS3TerrainMidpoint(const Renderer* r, const S3TerrainVertex& p1,
                             const S3TerrainVertex& p2, S3TerrainVertex& out);

// Project an EMBEDDED S3 point (unit 4-vector) for the current image —
// used by the world-model water sphere, whose geometry lives on S3 itself
// (no chart roundtrip, no chart degeneracies at the antipode). Same SO(4)
// view, hemisphere gates and arc depth policy as RenderS3ProjectWorld.
int RenderS3ProjectEmbedded(const Renderer* r, const double P[4],
                            float* fx, float* fy, float* viewer_dist,
                            float* depth);

// Fill one PRE-PROJECTED, screen-small water leaf triangle (verts already
// accepted by RenderS3ProjectEmbedded for the current image). The world
// model (engine/world_s3.cpp) owns the sphere tessellation and the
// projection-safe subdivision; this is only the leaf rasterizer.
// pattern[] is a per-vertex WORLD-SPACE wave value (computed by the world
// model from the sphere position) interpolated per pixel — the texture
// gradient that makes the ocean read as a surface: near waves are small on
// screen, far-side waves arrive magnified by reverse perspective.
void RenderS3FillWaterLeaf(Renderer* r,
                           const float fx[3], const float fy[3],
                           const float depth[3], const float pattern[3]);

// FL-4257 — spherical minimap sample. Given the player chart-world position
// (px,py) and a ground-tangent screen offset (east_m east-axis, north_m
// north-axis) in world metres, returns in (out_wx,out_wy) the chart-world
// coords of the point reached by the great circle leaving the player in that
// tangent direction: a PLAYER-CENTRED azimuthal-equidistant projection. This
// makes forward motion (a great circle through the player) read as a straight
// radial line on the minimap instead of the false circular drift the
// anchor-centred flat chart shows. Returns 1 when spherical curvature is
// active and the sample is valid; 0 when Euclidean/inactive or pole-degenerate
// (caller keeps the flat-chart sample, so Euclidean runs are unchanged).
int RenderS3MinimapSample(const Renderer* r, float px, float py,
                          float east_m, float north_m,
                          float* out_wx, float* out_wy);

// FL-4257 — inverse of RenderS3MinimapSample. Projects a world chart point
// (wx,wy) on the ground sheet to its player-centred tangent offset
// (east_m,north_m) in the SERVER-TRANSPORTED frame (log map), so marker / dot /
// mesh-footprint layers land on their true geodesic position in the same frame
// the terrain background uses. Returns 1 when spherical curvature is active and
// a valid authoritative pose is cached; 0 when Euclidean/inactive, no pose, or
// degenerate (caller keeps the chart-linear offset, so Euclidean runs are
// unchanged).
int RenderS3MinimapProject(const Renderer* r, float px, float py,
                           float wx, float wy,
                           float* out_east_m, float* out_north_m);

// FL-4257 — true when the transported-frame minimap projection is active
// (spherical curvature + valid authoritative pose). The minimap uses this to
// widen its chart-space mesh-footprint pre-cull so geodesic-bent buildings are
// not dropped before RenderS3MinimapProject places them.
int RenderS3MinimapActive(const Renderer* r);
