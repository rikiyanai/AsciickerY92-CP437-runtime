// =============================================================================
// [FLOW:TERMINAL] Terminal Emulator Public API — OpenGL Window Management
// =============================================================================
//
// PURPOSE:
// Public API for creating and managing OpenGL terminal emulator windows.
// Each window runs an independent game instance (Game object) and renders
// its AnsiCell buffer to screen via OpenGL shaders (see term.cpp for pipeline).
//
// FUNCTIONS:
//
// - TermOpen(A3D_WND* share, float yaw, float pos[3], void(*close)())
//   Create new terminal window with OpenGL context.
//   - share: Shared OpenGL context (for texture/buffer sharing, can be NULL)
//   - yaw: Initial camera rotation (radians, passed to game instance)
//   - pos[3]: Initial camera position (x, y, z world coordinates)
//   - close: Optional callback invoked when window closes (cleanup hook)
//   - Returns: Game* pointer to game instance for this window
//   - Allocates TERM_LIST node, initializes OpenGL resources (textures, shaders,
//     VAO/VBO for fullscreen quad), creates Game instance, adds to linked list
//   - Registers PlatformInterface callbacks (term_render, term_resize, term_keyb_*,
//     term_mouse) to route OS events to Game object
//
// - TermCloseAll()
//   Close all terminal windows and cleanup OpenGL resources.
//   - Traverses linked list of TERM_LIST nodes (term_head → term_tail)
//   - For each window: delete textures/shaders/buffers, free game instance, close window
//   - Resets linked list to empty (term_head = term_tail = 0)
//   - Called on program exit (main shutdown sequence)
//
// - TermResizeAll()
//   Resize all terminal windows to match current viewport dimensions.
//   - Traverses linked list of TERM_LIST nodes
//   - For each window: call term_resize() to recompute cell dimensions and
//     notify Game::OnSize() for viewport-dependent logic (e.g., UI layout)
//   - Called when font size changes (NextGLFont/PrevGLFont) or display scaling changes
//
// TERMINAL MANAGEMENT:
// - Linked list of TERM_LIST nodes (term_head, term_tail in term.cpp)
// - Each node contains: OpenGL window handle (A3D_WND*), game instance (Game*),
//   OpenGL resources (textures, shaders, VAO/VBO), input state (keys[], yaw)
// - Multiple windows supported (each with independent OpenGL context and game state)
//
// LIFECYCLE:
// 1. TermOpen() — Create window, initialize OpenGL, start game instance
// 2. Platform event loop (platform.h) — OS dispatches events to PlatformInterface callbacks
// 3. term_render() (term.cpp) — Render game to screen every frame
// 4. term_close() (term.cpp) or TermCloseAll() — Cleanup OpenGL resources, free game
//
// INTEGRATION POINTS:
// - platform.h: PlatformInterface callbacks route OS events (render, resize, keyboard,
//   mouse) to term_* functions in term.cpp
// - game.cpp: Game object API (Render, OnSize, OnKeyb, OnMouse, OnFocus) receives
//   events from term_* callbacks
// - GetGLFont() (asciiid.cpp/game_app.cpp): Provides CP437 font texture for rendering
//
// THREAD SAFETY:
// - NOT thread-safe — all functions assume single-threaded OpenGL context
// - OpenGL contexts are per-thread, TERM_LIST operations modify global linked list
//   without locking (expects single main thread calling TermOpen/TermCloseAll)
// =============================================================================

#pragma once

#include <stdint.h>
#include "window_backend.h"
#include "input_backend.h"
#include "terminal_backend.h"

struct Game;

Game* TermOpen(A3D_WND* share, float yaw, float pos[3], void(*close)() = 0);
// FL-4588 condition-2 (sole continuous presenter): reports whether at least
// one detached TERM++ window currently exists (the term_head linked list is
// non-empty). The editor's sole-presenter arm loop polls this every frame so
// it opens exactly ONE presenter when a world is loaded and never stacks a
// second window on top of a running one.
int TermHasOpenWindow();
void TermCloseAll();
void TermResizeAll();
void TermRenderAll();
int TermApplyPlayerSkinId(uint16_t skin_id);
int TermApplyPlayerSkin();
int TermSetPlayerSpriteVisible(int visible);
int TermGetPlayerSpriteVisible();
int TermSetEnvironmentalAnimationEnabled(int enabled);
int TermGetEnvironmentalAnimationEnabled();
// FL-4652: bit 0 is animation-enabled; bits 1..63 are the monotonic
// environment revision consumed by the native TERM++ presentation frame.
uint64_t TermGetEnvironmentalAnimationPresentationPackedV1();
// Reset the existing renderer-local observation phase in every detached TERM++
// view. This is a user-reachable presentation action; it never creates a
// second phase owner.
int TermResetEnvironmentalPhase();
int TermSetPerformanceHudVisible(int visible);
int TermGetPerformanceHudVisible();
// Mechanical equivalent of a user moving/resizing the native TERM++ window.
// Presentation geometry only: this cannot change camera, cells, profiles,
// gameplay state, or the renderer's final-GlyphId owner.
int TermSetWindowRect(int x, int y, int w, int h);
int TermSetCameraView(float x, float y, float z, float yaw);
// FL-4588 authoring follow: also set the world-Render zoom used when the
// HTML shell mirrors the editor camera into TERM++. world_zoom<=0 fails closed
// without falling into the legacy player/physics camera owner.
//
// When world_zoom > 0 this also arms the synchronized authoring camera owner:
// TERM++ final must consume the same footpoint camera center as editor_scene
// (no Game perspective focus_node look-ahead as a second center).
int TermSetCameraViewEx(float x, float y, float z, float yaw, float world_zoom);
// Returns the authoring follow zoom when armed; otherwise fallback.
float TermGetAuthoringWorldZoomOrDefault(float fallback);
// True while HTML authoring follow has armed TermSetCameraViewEx with a
// positive world zoom. Game::Render must then delete the divergent
// session.perspective / focus_node camera-center owner for that frame.
int TermAuthoringCameraFollowActive(void);
// Sole authoring footpoint pose written by TermSetCameraViewEx. Game::Render
// must consume these values for TERM++ final under follow — not player.pos /
// physics io.pos, which can diverge after PhysicsStep each term frame.
int TermGetAuthoringFollowCamera(
	float* out_x, float* out_y, float* out_z, float* out_yaw);
int TermSetLight(float x, float y, float z, float ambience);
int TermDumpRenderedBuffer(const char* path);
int TermDumpBridgeCells(const char* path);
// FL-4588 task 40B/53: explicit per-phase artifact snapshots (no implicit
// capture, no forever cache). TermCaptureArtifactSnapshotC builds the
// rendered-buffer and bridge-facts JSONL from the exact requested HTML-visible
// TERM++ frame into a new immutable snapshot. The render thread retains a
// bounded ring keyed by authoring_frame_id + final_frame_sha256 after emitting
// fl4548.termpp.cell_trace.v7 on that SAME frame. Unknown, evicted, and
// identity-mismatched requests fail closed. Returns the snapshot's monotonic id
// (>0) + frame (0 => fail closed). TermGetArtifactSnapshotBytesC
// returns the exact bytes of an existing snapshot (which: 0=rendered_buffer,
// 1=bridge_facts, 2=cell_trace); it fails closed (returns 0) on
// unknown/expired/evicted ids or an artifact not frozen on this snapshot frame.
extern "C" unsigned long long TermCaptureArtifactSnapshotC(
	unsigned long long expected_authoring_frame_id,
	const char* expected_final_frame_sha256,
	unsigned long long* frame_out);
// FL-4588 task 67/67A: read-only SAME-FRAME rendered_buffer + bridge_facts
// observation for the UNAUTHENTICATED authoring state_snapshot. Unlike
// TermCaptureArtifactSnapshotC this mints NO immutable snapshot id and requires
// NO cell_trace — so the HTML authoring session can read /api/v1/state_snapshot
// without an evidence-viewer artifact.read handshake and WITHOUT permanently
// arming cell-trace serialization. It rebuilds rb + bf from ONE final frame via
// the same in-memory serializers TermDumpRenderedBuffer/TermDumpBridgeCells use
// (no blocking wait on the render thread, no file evidence, no synthetic bytes).
// Returns 1 on success (out pointers into an internal buffer valid until the next
// observation/capture; frame_out = the shared rb/bf frame). Returns 0 fail-closed
// when no single live frame carries both artifacts. The authenticated
// /api/v1/artifacts/capture path remains the SOLE three-way rb/bf/ct snapshot-id
// mint and still fails closed without the arm.
extern "C" int TermObserveRenderedPairC(
	const char** rendered_buffer, size_t* rendered_buffer_len,
	const char** bridge_facts, size_t* bridge_facts_len,
	unsigned long long* frame_out);
// FL-4588 task 53/53A: arm the cell-trace observation once at backend startup so
// the render emitter deposits the fl4548.termpp.cell_trace.v7 JSONL into memory
// every frame. This lets the FIRST capture mint one id whose rendered_buffer,
// bridge_facts and cell_trace are all on one frame — no warm-up capture.
extern "C" void TermArmCellTraceCapture(void);
extern "C" int TermGetArtifactSnapshotBytesC(
	unsigned long long snapshot_id, int which,
	const char** data, size_t* len, unsigned long long* frame_out);
// FL-4588 task 56: immutable final-frame + trace metadata bound to a snapshot id
// for sealed transaction receipts. Fills frame, the monotonic authoring frame id,
// the actual presented TERM++ RGBA frame hash (NOT the rendered-buffer hash) and
// the frozen cell-trace byte hash. Returns 1 on success; 0 on unknown/expired id.
extern "C" int TermGetArtifactSnapshotMetaC(
	unsigned long long snapshot_id, unsigned long long* frame_out,
	unsigned long long* authoring_frame_id_out,
	char final_frame_sha256_out[65], char cell_trace_sha256_out[65]);
extern "C" void TermRequestCaptureWithRenderedBufferAndBridge(const char* png_path, const char* rendered_buffer_path, const char* bridge_path);
extern "C" void TermRequestCaptureWithRenderedBuffer(const char* png_path, const char* rendered_buffer_path);

// FL-4207 step 2/3 (2026-06-09): in-process read of the TERM++ rendered buffer
// so the editor UI can compute per-mat winner histograms and per-cell click
// traces without going through a JSONL roundtrip. TermDumpRenderedBuffer is
// still the file-level truth surface; these accessors expose the same per-cell
// final_gid + sidecar identity for in-frame UI consumption.
//
// TermGetRenderDims: writes the live render width/height (clipped to
// TERM_LIST::max_width/max_height). Returns 0 on success, negative if no
// TERM_LIST is active. Pass nulls to skip a field.
//
// TermReadCell: returns 0 on success, negative on (no term / out-of-range /
// stride miss). final_gid is the same value TermDumpRenderedBuffer would write
// for this cell — sidecar when extended, otherwise the AnsiCell.gl byte.
int TermGetRenderDims(int* out_w, int* out_h);
int TermReadCell(int x, int y,
	uint32_t* out_final_gid, uint32_t* out_sidecar_gid,
	uint8_t* out_extended, uint8_t* out_cp437);
int TermReadCellVisual(int x, int y,
	uint32_t* out_final_gid, uint32_t* out_sidecar_gid,
	uint8_t* out_extended, uint8_t* out_cp437,
	uint8_t* out_fg, uint8_t* out_bk);

// FL-4588 read-only selected-cell contract for the browser inspector. The
// presenter copies the identities already owned by the live render pipeline;
// callers may display them but may not recompute routing, scoring, sequence,
// temporal retention, or final presentation.
struct TermCellInspectionV1
{
	uint8_t valid;
	uint8_t surface_id_state;
	uint8_t surface_id_reason;
	uint8_t dispatch_surface;
	uint64_t frame_id;
	int32_t grid_w;
	int32_t grid_h;
	int32_t cell_x;
	int32_t cell_y;
	int16_t material_id;
	int8_t ramp_idx;
	int8_t density_idx;
	int8_t vertical_relation_idx;
	int8_t direction_idx;
	int8_t flow_idx;
	int8_t mesh_feature_kind_idx;
	uint8_t fact_mask;
	uint8_t scene_fact_plane_valid;
	uint16_t scene_fact_plane_w;
	uint16_t scene_fact_plane_h;
	uint16_t scene_fact_valid_count;
// Product scene-fact payload (136-byte opaque ABI, completed product-side).
// Shared keeps opaque bytes only so no product-private decl remains under
// shared roots; product casts to the complete type. Size pinned to the
// 136-byte ABI. Identical layout in every product: Classic carries the
// bytes without filling them.
	uint8_t scene_fact_payload[136];
	uint64_t surface_world_generation;
	uint32_t surface_owner_kind;
	int32_t surface_owner_x;
	int32_t surface_owner_y;
	uint16_t surface_anchor_u_q;
	uint16_t surface_anchor_v_q;
	uint32_t unary_winner_gid;
	uint32_t current_winner_gid;
	uint32_t presented_winner_gid;
	uint32_t final_screen_gid;
	uint8_t final_screen_fg;
	uint8_t final_screen_bg;
	uint8_t final_screen_cp437;
	uint8_t final_screen_extended;
	uint8_t selected_role;
	uint8_t result_kind;
	uint8_t absence_state;
	uint8_t absence_reason;
	uint8_t descriptor_kind;
	uint8_t descriptor_version;
	uint8_t scorer_kind;
	uint8_t scorer_version;
	uint8_t final_writer;
	int16_t pool_material_key;
	int8_t pool_lane_id;
	int8_t pool_secondary_lane_id;
	uint8_t pool_profile_namespace;
	uint8_t pool_route_key;
	uint32_t candidate_count;
	uint64_t pool_revision;
	uint32_t score_num;
	float score_f32;
	uint64_t sequence_chain_id;
	// Mesh provenance is retained for recorder joins. It is not part of the
	// producer-independent sequence identity above. FL-4783.
	uint64_t mesh_provenance_uid;
	uint16_t sequence_node_index;
	uint16_t sequence_u_q16;
	uint8_t sequence_phase;
	uint8_t sequence_anchor_kind;
	uint32_t sequence_hash;
	uint8_t temporal_decision_valid;
	uint8_t temporal_decision_kind;
	uint8_t temporal_previous_legal;
	uint8_t temporal_collision;
	uint32_t temporal_previous_gid;
	uint32_t temporal_current_gid;
};

int TermReadCellInspectionV1(int x, int y, TermCellInspectionV1* out);

// One immutable inspection snapshot captured with the native TERM++ RGBA
// frame. The browser selects a frame by this identity, then reads cells from
// that snapshot. A later presenter draw must never change what an earlier
// displayed frame means.
struct TermAuthoringInspectionFrameV1
{
	uint64_t frame_id;
	int32_t pixel_w;
	int32_t pixel_h;
	int32_t grid_w;
	int32_t grid_h;
	char view_state_sha256[65];
};

int TermFindAuthoringInspectionFrameV1(
	uint64_t frame_id, const char* view_state_sha256,
	TermAuthoringInspectionFrameV1* out);
int TermReadAuthoringFrameCellInspectionV1(
	uint64_t frame_id, int x, int y, TermCellInspectionV1* out);
