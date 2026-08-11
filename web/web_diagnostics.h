// web_diagnostics.h — Web diagnostics / lifecycle / recorder JSON seam
//
// PURPOSE: Narrow interface for browser-side diagnostics, lifecycle canary
// probes, recorder JSON builders, and world-parity statistics. Extracted from
// web/game_web.cpp to isolate probe infrastructure from networking and platform
// entry code.
//
// CALLBACK ENTRYPOINTS (called from networking & platform code):
// - WebDiagnosticsOnLifecycleEvent(...) — record lifecycle event with server state
// - WebDiagnosticsCountPacketToken(token) — record packet token histogram
// - WebDiagnosticsTrackPacketProc(token, proc_us) — track packet processing time
// - WebDiagnosticsSampleRenderBuffer(buf, w, h) — sample render buffer for diag
// - WebDiagnosticsBuildLifecycleRingJson() — build lifecycle ring JSON
// - WebDiagnosticsBuildAnsiFrameSnapshotJson(render_buf) — build ANSI frame snapshot
//
// SEE ALSO:
// - web_diagnostics.cpp — implementation

#pragma once

#include <stdint.h>
#include <stddef.h>
#include "web_recorder_bridge.h"

#ifdef __EMSCRIPTEN__
#include <emscripten/emscripten.h>
#else
#ifndef EMSCRIPTEN_KEEPALIVE
#define EMSCRIPTEN_KEEPALIVE
#endif
#endif

struct AnsiCell;

struct WebDiagnosticsLifecycleSnapshot
{
    uint32_t seq;
    char event[64];
    char prev_event[64];
    int server_ptr_ok;
    int alloc_ok;
    int canary_ok;
    uint32_t sentinel_a;
    uint32_t sentinel_b;
    uint32_t epoch;
};

struct WebDiagnosticsServerLossProvenanceSnapshot
{
    int first_live_seen;
    char first_live_stage[64];
    uint32_t first_live_ptr;
    uint32_t first_live_lifecycle_seq;
    uint32_t first_live_join_generation;
    int first_null_after_live_seen;
    char first_null_after_live_stage[64];
    uint32_t first_null_after_live_op;
    uint32_t first_null_after_live_size;
    uint32_t first_null_after_live_packet_calls;
    uint32_t first_null_after_live_packet_proc;
    uint32_t first_null_after_live_packet_null;
    uint32_t first_null_after_live_packet_defer;
    uint32_t first_null_after_live_pending;
    int first_null_after_live_canary_ok;
    uint32_t first_null_after_live_alloc_ptr;
    uint32_t first_null_after_live_alloc_server_ptr;
    uint32_t first_null_after_live_lifecycle_seq;
    uint32_t first_null_after_live_join_generation;
    int first_null_after_live_game_loading;
    int first_null_after_live_menu_progress;
    int first_null_after_live_world_bits;
    uint32_t first_null_after_live_sentinel_a;
    uint32_t first_null_after_live_sentinel_b;
};

#ifdef __cplusplus
extern "C" {
#endif

// ── Lifecycle / FL933 probe entrypoints ──

// Record a lifecycle event. Accepts server/allocation pointer values
// and pending queue depth so diagnostics does not reach into networking globals.
void WebDiagnosticsOnLifecycleEvent(
    const char* event,
    uint32_t server_ptr,
    uint32_t alloc_ptr,
    uint32_t alloc_server_ptr,
    int canary_ok,
    int pending_count);

// Record a packet token for histogram tracking.
void WebDiagnosticsCountPacketToken(uint8_t token);

// Record a dropped pending packet token.
void WebDiagnosticsCountDroppedPendingToken(uint8_t token);

// Sample render buffer for diagnostics (stores hash, center pixels, etc.).
void WebDiagnosticsSampleRenderBuffer(const AnsiCell* buf, int width, int height);

// Track packet processing duration.
void WebDiagnosticsTrackPacketProc(uint8_t token, uint32_t proc_us);

// Reset server-loss provenance tracking.
void WebDiagnosticsResetServerLossProvenance(void);

// Observe authoritative server pointer state for first-live / first-null-after-live
// provenance. Callers pass current packet and join context so diagnostics owns the
// resulting witness state rather than game_web.cpp.
void WebDiagnosticsObserveServerPointer(
    const char* stage,
    uint32_t server_ptr,
    int authoritative_join_active,
    uint32_t join_generation,
    uint32_t packet_op,
    uint32_t packet_size,
    uint32_t packet_calls,
    uint32_t packet_proc,
    uint32_t packet_null,
    uint32_t packet_defer,
    uint32_t pending_count,
    int canary_ok,
    uint32_t alloc_ptr,
    uint32_t alloc_server_ptr);

// Compute pending queue token counts from caller-provided data.
void WebDiagnosticsPendingQueueTokenCounts(
    int pending_count,
    const uint16_t* pending_sizes,
    const uint8_t* pending_tokens,
    int* b, int* q, int* a, int* i);

// ── JSON builders ──

// Build and return lifecycle ring JSON snapshot (caller must not free).
const char* WebDiagnosticsBuildLifecycleRingJson(void);

// Build and return ANSI frame snapshot JSON (caller must not free).
// render_buf param provides the current frame buffer; diagnostics owns
// the sample metadata captured by WebDiagnosticsSampleRenderBuffer().
const char* WebDiagnosticsBuildAnsiFrameSnapshotJson(const AnsiCell* render_buf);

// ── Transitional accessors for globals (used by game_web.cpp RecorderStateJson) ──

// Packet histogram counters.
uint32_t WebDiagnosticsGetPacketHistB(void);
uint32_t WebDiagnosticsGetPacketHistQ(void);
uint32_t WebDiagnosticsGetPacketHistA(void);
uint32_t WebDiagnosticsGetPacketHistI(void);
uint32_t WebDiagnosticsGetPacketHistJ(void);
uint32_t WebDiagnosticsGetPacketHistN(void);

// Packet processing timing.
uint32_t WebDiagnosticsGetPacketLastToken(void);
uint32_t WebDiagnosticsGetPacketLastProcUs(void);
uint32_t WebDiagnosticsGetPacketMaxProcUs(void);
uint32_t WebDiagnosticsGetPacketRLastProcUs(void);
uint32_t WebDiagnosticsGetPacketRMaxProcUs(void);
uint32_t WebDiagnosticsGetPacketDLastProcUs(void);
uint32_t WebDiagnosticsGetPacketDMaxProcUs(void);
uint32_t WebDiagnosticsGetPacketKLastProcUs(void);
uint32_t WebDiagnosticsGetPacketKMaxProcUs(void);

// Dropped pending token counters.
uint32_t WebDiagnosticsGetPendingDroppedTokenB(void);
uint32_t WebDiagnosticsGetPendingDroppedTokenQ(void);
uint32_t WebDiagnosticsGetPendingDroppedTokenA(void);
uint32_t WebDiagnosticsGetPendingDroppedTokenI(void);
uint32_t WebDiagnosticsGetPendingDroppedTokenJ(void);
uint32_t WebDiagnosticsGetPendingDroppedTokenN(void);

// Render buffer sample state.
int WebDiagnosticsGetRenderBufSampleValid(void);
int WebDiagnosticsGetRenderBufSampleWidth(void);
int WebDiagnosticsGetRenderBufSampleHeight(void);
uint32_t WebDiagnosticsGetRenderBufSampleCells(void);
uint32_t WebDiagnosticsGetRenderBufNonzeroCells(void);
uint32_t WebDiagnosticsGetRenderBufNonzeroGlyphCells(void);
uint32_t WebDiagnosticsGetRenderBufHash(void);
// FL-4079: monotonic sample-sequence stamp, incremented inside
// WebDiagnosticsSampleRenderBuffer() only when a frame produces a valid sample.
uint32_t WebDiagnosticsGetRenderBufProbeSeq(void);
const uint8_t* WebDiagnosticsGetRenderBufCenterFg(void);
const uint8_t* WebDiagnosticsGetRenderBufCenterBk(void);
const uint8_t* WebDiagnosticsGetRenderBufCenterGl(void);
const uint8_t* WebDiagnosticsGetRenderBufCenterSpare(void);

// Read-only lifecycle/provenance snapshots.
const WebDiagnosticsLifecycleSnapshot* WebDiagnosticsGetLifecycleSnapshot(void);
const WebDiagnosticsServerLossProvenanceSnapshot* WebDiagnosticsGetServerLossProvenanceSnapshot(void);

// ── Recorder bridge ──

// Query and return current recorder bridge mode from JavaScript.
WebRecorderBridgeMode ActiveWebRecorderBridgeMode(void);

// ── JSON builder helpers (transitional, used by legacy RecorderStateJson) ──

// Append formatted text to a JSON buffer (va_args).
// Returns true on success, false on buffer full.
bool WebDiagnosticsAppendProbeText(char* buf, int cap, int& used, const char* fmt, ...);

// Convenience wrappers around AppendProbeText for JSON fields.
bool WebDiagnosticsAppendJsonIntField(char* buf, int cap, int& used, const char* key, int value);
bool WebDiagnosticsAppendJsonUIntField(char* buf, int cap, int& used, const char* key, uint32_t value);
bool WebDiagnosticsAppendJsonUInt64Field(char* buf, int cap, int& used, const char* key, uint64_t value);
bool WebDiagnosticsAppendJsonBoolField(char* buf, int cap, int& used, const char* key, bool value);
bool WebDiagnosticsAppendJsonFloatField(char* buf, int cap, int& used, const char* key, float value);
bool WebDiagnosticsAppendJsonStringField(char* buf, int cap, int& used, const char* key, const char* value);
bool WebDiagnosticsAppendJsonU8ArrayField(char* buf, int cap, int& used, const char* key, const uint8_t* values, int count);
bool WebDiagnosticsAppendJsonUIntArrayField(char* buf, int cap, int& used, const char* key, const uint16_t* values, int count);
bool WebDiagnosticsAppendJsonFloatArrayField(char* buf, int cap, int& used, const char* key, const float* values, int count);

// ── Server-loss provenance tracking accessors ──

// First server pointer observation.
int WebDiagnosticsGetServerFirstLiveSeen(void);
const char* WebDiagnosticsGetServerFirstLiveStage(void);
uint32_t WebDiagnosticsGetServerFirstLivePtr(void);
uint32_t WebDiagnosticsGetServerFirstLiveLifecycleSeq(void);
uint32_t WebDiagnosticsGetServerFirstLiveJoinGeneration(void);

// First null-after-live observation.
int WebDiagnosticsGetServerFirstNullAfterLiveSeen(void);
const char* WebDiagnosticsGetServerFirstNullAfterLiveStage(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveOp(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveSize(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLivePacketCalls(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLivePacketProc(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLivePacketNull(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLivePacketDefer(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLivePending(void);
int WebDiagnosticsGetServerFirstNullAfterLiveCanaryOk(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveAllocPtr(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveAllocServerPtr(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveLifecycleSeq(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveJoinGeneration(void);
int WebDiagnosticsGetServerFirstNullAfterLiveGameLoading(void);
int WebDiagnosticsGetServerFirstNullAfterLiveMenuProgress(void);
int WebDiagnosticsGetServerFirstNullAfterLiveWorldBits(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveSentinelA(void);
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveSentinelB(void);

// ── Lifecycle state accessors ──

uint32_t WebDiagnosticsGetLifecycleSeq(void);
const char* WebDiagnosticsGetLifecycleEvent(void);
const char* WebDiagnosticsGetLifecyclePrevEvent(void);
int WebDiagnosticsGetLifecycleServerPtrOk(void);
int WebDiagnosticsGetLifecycleAllocOk(void);
int WebDiagnosticsGetLifecycleCanaryOk(void);
uint32_t WebDiagnosticsGetFl933SentinelA(void);
uint32_t WebDiagnosticsGetFl933SentinelB(void);
uint32_t WebDiagnosticsGetFl933LifecycleEpoch(void);

// ── Terrain sample ──

struct WebDiagnosticsTerrainSample
{
    int valid;
    int patch_present;
    uint32_t height_raw;
    uint32_t material_id;
};

// Sample terrain at (x,y). Returns terrain sample struct.
WebDiagnosticsTerrainSample WebDiagnosticsSampleTerrainAt(float x, float y);

// ── Extern "C" JS exports ──

// Get current render stage code (from game.cpp extern).
EMSCRIPTEN_KEEPALIVE int GetRenderStageCode(void);

// Get ANSI frame snapshot JSON (calls BuildAnsiFrameSnapshotJson with global render_buf).
EMSCRIPTEN_KEEPALIVE const char* GetCppAnsiFrameSnapshotJson(void);

// Get lifecycle ring JSON.
EMSCRIPTEN_KEEPALIVE const char* GetLifecycleRingJson(void);

#ifdef __cplusplus
}
#endif
