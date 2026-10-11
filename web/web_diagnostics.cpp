// web_diagnostics.cpp — Web diagnostics / lifecycle / recorder probe helpers
//
// PURPOSE: Browser-side diagnostics infrastructure extracted from web/game_web.cpp.
// Owns FL933 lifecycle canary rings, render-buffer sampling, packet token histograms,
// probe timing, and JSON builder helpers. Does NOT reach into networking globals
// directly — receives all entity state via parameterized entrypoints.
//
// EXTRACTED FROM: web/game_web.cpp (monolith)
//
// INTEGRATION POINTS:
// - game_web.cpp: calls WebDiagnosticsOnLifecycleEvent(), WebDiagnosticsCountPacketToken(), etc.
// - JavaScript: exports GetCppAnsiFrameSnapshotJson(), GetLifecycleRingJson()
//
// SEE ALSO:
// - web_diagnostics.h — declarations
// - web_network_client.cpp (future) — will call diagnostics entrypoints

#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <emscripten.h>

#include "game.h"
#include "terrain.h"
#include "world.h"
#include "render.h"

#include "web_recorder_bridge.h"
#include "web_diagnostics.h"
#include "web_filesystem.h"

// ── Forward declarations of web-global pointers (owned by game_web.cpp) ──
// These are read-only reads by diagnostics helpers that need to inspect
// the game/world/terrain state. They will be parameterized away when the
// networking layer is extracted.

extern Game* game;
extern Terrain* terrain;
extern World* world;
extern int g_web_render_stage_code;
extern "C" int MainMenuWebGameLoadingState();
extern "C" int MainMenuWebProgressState();

// ── Private state: render buffer sample ──

static int g_web_render_buf_sample_valid = 0;
static int g_web_render_buf_sample_width = 0;
static int g_web_render_buf_sample_height = 0;
static uint32_t g_web_render_buf_sample_cells = 0;
static uint32_t g_web_render_buf_nonzero_cells = 0;
static uint32_t g_web_render_buf_nonzero_glyph_cells = 0;
static uint32_t g_web_render_buf_hash = 0;
// FL-4079: monotonic sequence stamped each time a Render() pass produces a valid
// sample. The wearable proof probe reads this in the same critical section as
// the recorder/render fields so a single JS call cannot straddle two Render()s.
static uint32_t g_web_render_buf_probe_seq = 0;
static uint8_t g_web_render_buf_center_fg[8] = {};
static uint8_t g_web_render_buf_center_bk[8] = {};
static uint8_t g_web_render_buf_center_gl[8] = {};
static uint8_t g_web_render_buf_center_spare[8] = {};

// ── Private state: packet token histograms ──

static uint32_t g_web_packet_hist_b = 0;
static uint32_t g_web_packet_hist_q = 0;
static uint32_t g_web_packet_hist_a = 0;
static uint32_t g_web_packet_hist_i = 0;
static uint32_t g_web_packet_hist_j = 0;
static uint32_t g_web_packet_hist_n = 0;

// ── Private state: dropped pending token counters ──

static uint32_t g_web_pending_dropped_token_b = 0;
static uint32_t g_web_pending_dropped_token_q = 0;
static uint32_t g_web_pending_dropped_token_a = 0;
static uint32_t g_web_pending_dropped_token_i = 0;
static uint32_t g_web_pending_dropped_token_j = 0;
static uint32_t g_web_pending_dropped_token_n = 0;

// ── Private state: packet processing timing ──

static uint32_t g_web_packet_last_token = 0;
static uint32_t g_web_packet_last_proc_us = 0;
static uint32_t g_web_packet_max_proc_us = 0;
static uint32_t g_web_packet_r_last_proc_us = 0;
static uint32_t g_web_packet_r_max_proc_us = 0;
static uint32_t g_web_packet_d_last_proc_us = 0;
static uint32_t g_web_packet_d_max_proc_us = 0;
static uint32_t g_web_packet_k_last_proc_us = 0;
static uint32_t g_web_packet_k_max_proc_us = 0;

// ── Private state: FL933 lifecycle ring ──

static uint32_t g_web_server_lifecycle_seq = 0;
static char g_web_server_lifecycle_event[64] = "init";
static char g_web_server_lifecycle_prev_event[64] = "";
static int g_web_server_lifecycle_server_ptr_ok = 0;
static int g_web_server_lifecycle_alloc_ok = 0;
static int g_web_server_lifecycle_canary_ok = -1;
static uint32_t g_web_fl933_sentinel_a = 0xa933c001u;
static uint32_t g_web_fl933_sentinel_b = 0xb933c002u;
static uint32_t g_web_fl933_lifecycle_epoch = 0;

struct WebFL933LifecycleEvent
{
    uint32_t seq;
    char event[48];
    uint32_t server_ptr;
    uint32_t alloc_ptr;
    uint32_t alloc_server_ptr;
    int canary_ok;
    int pending_count;
    int game_loading;
    int menu_progress;
    int world_ready_bits;
};

static WebFL933LifecycleEvent g_web_fl933_lifecycle_ring[16] = {};
static uint32_t g_web_fl933_lifecycle_ring_write = 0;

// ── Private state: server-loss provenance tracking ──

static int g_web_server_first_live_seen = 0;
static char g_web_server_first_live_stage[64] = "";
static uint32_t g_web_server_first_live_ptr = 0;
static uint32_t g_web_server_first_live_lifecycle_seq = 0;
static uint32_t g_web_server_first_live_join_generation = 0;
static int g_web_server_first_null_after_live_seen = 0;
static char g_web_server_first_null_after_live_stage[64] = "";
static uint32_t g_web_server_first_null_after_live_op = 0;
static uint32_t g_web_server_first_null_after_live_size = 0;
static uint32_t g_web_server_first_null_after_live_packet_calls = 0;
static uint32_t g_web_server_first_null_after_live_packet_proc = 0;
static uint32_t g_web_server_first_null_after_live_packet_null = 0;
static uint32_t g_web_server_first_null_after_live_packet_defer = 0;
static uint32_t g_web_server_first_null_after_live_pending = 0;
static int g_web_server_first_null_after_live_canary_ok = -1;
static uint32_t g_web_server_first_null_after_live_alloc_ptr = 0;
static uint32_t g_web_server_first_null_after_live_alloc_server_ptr = 0;
static uint32_t g_web_server_first_null_after_live_lifecycle_seq = 0;
static uint32_t g_web_server_first_null_after_live_join_generation = 0;
static int g_web_server_first_null_after_live_game_loading = -1;
static int g_web_server_first_null_after_live_menu_progress = -1;
static int g_web_server_first_null_after_live_world_bits = 0;
static uint32_t g_web_server_first_null_after_live_sentinel_a = 0;
static uint32_t g_web_server_first_null_after_live_sentinel_b = 0;

// ── Private state: JSON output buffers ──

static char g_web_fl933_lifecycle_ring_json[4096] = {};
static char g_web_cpp_ansi_frame_snapshot_json[262144] = {};
static WebDiagnosticsLifecycleSnapshot g_web_lifecycle_snapshot = {};
static WebDiagnosticsServerLossProvenanceSnapshot g_web_server_loss_snapshot = {};

// ══════════════════════════════════════════════════════════════════════════
// Pure formatting / JSON helpers
// ══════════════════════════════════════════════════════════════════════════

bool WebDiagnosticsAppendProbeText(char* buf, int cap, int& used, const char* fmt, ...)
{
    if (!buf || cap <= 0)
        return false;

    if (used < 0)
        used = 0;
    if (used >= cap)
    {
        buf[cap - 1] = 0;
        return false;
    }

    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf + used, cap - used, fmt, ap);
    va_end(ap);

    if (n < 0)
        return false;

    int remain = cap - used;
    if (n >= remain)
    {
        used = cap - 1;
        buf[cap - 1] = 0;
        return false;
    }

    used += n;
    return true;
}

bool WebDiagnosticsAppendJsonIntField(char* buf, int cap, int& used, const char* key, int value)
{
    return WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":%d", used > 1 ? "," : "", key, value);
}

bool WebDiagnosticsAppendJsonUIntField(char* buf, int cap, int& used, const char* key, uint32_t value)
{
    return WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":%u", used > 1 ? "," : "", key, value);
}

bool WebDiagnosticsAppendJsonUInt64Field(char* buf, int cap, int& used, const char* key, uint64_t value)
{
    return WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":%llu", used > 1 ? "," : "", key, (unsigned long long)value);
}

bool WebDiagnosticsAppendJsonBoolField(char* buf, int cap, int& used, const char* key, bool value)
{
    return WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":%s", used > 1 ? "," : "", key, value ? "true" : "false");
}

bool WebDiagnosticsAppendJsonUIntArrayField(char* buf, int cap, int& used, const char* key,
    const uint16_t* values, int count)
{
    if (!WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":[", used > 1 ? "," : "", key))
        return false;
    for (int i = 0; i < count; i++)
    {
        if (!WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u", i > 0 ? "," : "",
                values ? (uint32_t)values[i] : 0u))
            return false;
    }
    return WebDiagnosticsAppendProbeText(buf, cap, used, "]");
}

bool WebDiagnosticsAppendJsonU8ArrayField(char* buf, int cap, int& used, const char* key,
    const uint8_t* values, int count)
{
    if (!WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":[", used > 1 ? "," : "", key))
        return false;
    for (int i = 0; i < count; i++)
    {
        if (!WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u", i > 0 ? "," : "",
                values ? (uint32_t)values[i] : 0u))
            return false;
    }
    return WebDiagnosticsAppendProbeText(buf, cap, used, "]");
}

bool WebDiagnosticsAppendJsonFloatArrayField(char* buf, int cap, int& used, const char* key,
    const float* values, int count)
{
    if (!WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":[", used > 1 ? "," : "", key))
        return false;
    for (int i = 0; i < count; i++)
    {
        const float value = values ? values[i] : 0.0f;
        if (!isfinite(value))
        {
            if (!WebDiagnosticsAppendProbeText(buf, cap, used, "%snull", i > 0 ? "," : ""))
                return false;
        }
        else if (!WebDiagnosticsAppendProbeText(buf, cap, used, "%s%.3f", i > 0 ? "," : "", value))
        {
            return false;
        }
    }
    return WebDiagnosticsAppendProbeText(buf, cap, used, "]");
}

bool WebDiagnosticsAppendJsonFloatField(char* buf, int cap, int& used, const char* key, float value)
{
    if (!isfinite(value))
        return WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":null", used > 1 ? "," : "", key);
    return WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":%.3f", used > 1 ? "," : "", key, value);
}

bool WebDiagnosticsAppendJsonStringField(char* buf, int cap, int& used, const char* key, const char* value)
{
    if (!value || !value[0])
        return WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":null", used > 1 ? "," : "", key);
    return WebDiagnosticsAppendProbeText(buf, cap, used, "%s\"%s\":\"%s\"", used > 1 ? "," : "", key, value);
}

// ══════════════════════════════════════════════════════════════════════════
// Hash helpers (pure)
// ══════════════════════════════════════════════════════════════════════════

uint64_t HashCombine64(uint64_t h, uint64_t v)
{
    h ^= v + 0x9e3779b97f4a7c15ULL + (h << 6) + (h >> 2);
    return h;
}

uint64_t HashFloatBits(float v)
{
    uint32_t bits = 0;
    memcpy(&bits, &v, sizeof(bits));
    return (uint64_t)bits;
}

uint32_t HashFinalize32(uint64_t h)
{
    h ^= h >> 33;
    h *= 0xff51afd7ed558ccdULL;
    h ^= h >> 33;
    h *= 0xc4ceb9fe1a85ec53ULL;
    h ^= h >> 33;
    return (uint32_t)(h ^ (h >> 32));
}

// ══════════════════════════════════════════════════════════════════════════
// Pointer / hash helpers
// ══════════════════════════════════════════════════════════════════════════

static uint32_t WebFL933Ptr32(const void* ptr)
{
    return (uint32_t)(uintptr_t)ptr;
}

static uint32_t WebFL933HashAnsiCell(uint32_t hash, const AnsiCell& cell)
{
    uint32_t packed = (uint32_t)cell.fg |
        ((uint32_t)cell.bk << 8) |
        ((uint32_t)cell.gl << 16) |
        ((uint32_t)cell.spare << 24);
    hash ^= packed;
    hash *= 16777619u;
    return hash;
}

// ══════════════════════════════════════════════════════════════════════════
// Render buffer sampling
// ══════════════════════════════════════════════════════════════════════════

void WebDiagnosticsSampleRenderBuffer(const AnsiCell* buf, int width, int height)
{
    g_web_render_buf_sample_valid = 0;
    g_web_render_buf_sample_width = width;
    g_web_render_buf_sample_height = height;
    g_web_render_buf_sample_cells = 0;
    g_web_render_buf_nonzero_cells = 0;
    g_web_render_buf_nonzero_glyph_cells = 0;
    g_web_render_buf_hash = 0;
    memset(g_web_render_buf_center_fg, 0, sizeof(g_web_render_buf_center_fg));
    memset(g_web_render_buf_center_bk, 0, sizeof(g_web_render_buf_center_bk));
    memset(g_web_render_buf_center_gl, 0, sizeof(g_web_render_buf_center_gl));
    memset(g_web_render_buf_center_spare, 0, sizeof(g_web_render_buf_center_spare));
    if (!buf || width <= 0 || height <= 0)
        return;

    int cell_count = width * height;
    if (cell_count > 160 * 160)
        cell_count = 160 * 160;
    g_web_render_buf_sample_cells = (uint32_t)cell_count;
    uint32_t hash = 2166136261u;
    for (int i = 0; i < cell_count; i++)
    {
        const AnsiCell& cell = buf[i];
        if (cell.fg || cell.bk || cell.gl || cell.spare)
            g_web_render_buf_nonzero_cells++;
        if (cell.gl)
            g_web_render_buf_nonzero_glyph_cells++;
        hash = WebFL933HashAnsiCell(hash, cell);
    }
    g_web_render_buf_hash = hash;

    int cx = width / 2;
    int cy = height / 2;
    for (int k = 0; k < 8 && cx + k < width; k++)
    {
        const AnsiCell& cell = buf[cy * width + (cx + k)];
        g_web_render_buf_center_fg[k] = cell.fg;
        g_web_render_buf_center_bk[k] = cell.bk;
        g_web_render_buf_center_gl[k] = cell.gl;
        g_web_render_buf_center_spare[k] = cell.spare;
    }
    g_web_render_buf_probe_seq++; // FL-4079: bump only on valid sample
    g_web_render_buf_sample_valid = 1;
}

// ══════════════════════════════════════════════════════════════════════════
// Packet token histogram
// ══════════════════════════════════════════════════════════════════════════

void WebDiagnosticsCountPacketToken(uint8_t token)
{
    switch (token)
    {
        case 'b': g_web_packet_hist_b++; break;
        case 'q': g_web_packet_hist_q++; break;
        case 'a': g_web_packet_hist_a++; break;
        case 'i': g_web_packet_hist_i++; break;
        case 'j': g_web_packet_hist_j++; break;
        case 'n': g_web_packet_hist_n++; break;
        default: break;
    }
}

// ══════════════════════════════════════════════════════════════════════════
// Dropped pending token counters
// ══════════════════════════════════════════════════════════════════════════

void WebDiagnosticsCountDroppedPendingToken(uint8_t token)
{
    switch (token)
    {
        case 'b': g_web_pending_dropped_token_b++; break;
        case 'q': g_web_pending_dropped_token_q++; break;
        case 'a': g_web_pending_dropped_token_a++; break;
        case 'i': g_web_pending_dropped_token_i++; break;
        case 'j': g_web_pending_dropped_token_j++; break;
        case 'n': g_web_pending_dropped_token_n++; break;
        default: break;
    }
}

// ══════════════════════════════════════════════════════════════════════════
// Terrain sampling (reads terrain global externally)
// ══════════════════════════════════════════════════════════════════════════

struct WebFL933TerrainSample
{
    int valid;
    int patch_present;
    uint32_t height_raw;
    uint32_t material_id;
};

static WebFL933TerrainSample WebFL933SampleTerrainAt(float x, float y)
{
    WebFL933TerrainSample out;
    out.valid = 0;
    out.patch_present = 0;
    out.height_raw = 0;
    out.material_id = 0xffffffffu;
    if (!terrain)
        return out;

    const float patch_span = (float)(HEIGHT_CELLS * 2);
    int px = (int)floorf(x / patch_span);
    int py = (int)floorf(y / patch_span);
    Patch* patch = GetTerrainPatch(terrain, px, py);
    if (!patch)
    {
        out.valid = 1;
        return out;
    }

    uint16_t* hmap = GetTerrainHeightMap(patch);
    uint16_t* vmap = GetTerrainVisualMap(patch);
    if (!hmap || !vmap)
    {
        out.valid = 1;
        out.patch_present = 1;
        return out;
    }

    float lx = fmodf(x, patch_span);
    float ly = fmodf(y, patch_span);
    if (lx < 0.0f)
        lx += patch_span;
    if (ly < 0.0f)
        ly += patch_span;

    int hx = ((int)(lx / 2.0f)) % (HEIGHT_CELLS + 1);
    int hy = ((int)(ly / 2.0f)) % (HEIGHT_CELLS + 1);
    int vx = ((int)lx) % VISUAL_CELLS;
    int vy = ((int)ly) % VISUAL_CELLS;

    out.valid = 1;
    out.patch_present = 1;
    out.height_raw = hmap[hy * (HEIGHT_CELLS + 1) + hx];
    out.material_id = vmap[vy * VISUAL_CELLS + vx] & 0xffu;
    return out;
}

// ══════════════════════════════════════════════════════════════════════════
// World ready bits
// ══════════════════════════════════════════════════════════════════════════

static int WebFL933WorldReadyBits()
{
    return (game ? 1 : 0) |
        (world ? 2 : 0) |
        (terrain ? 4 : 0) |
        ((game && game->physics) ? 8 : 0);
}

// ══════════════════════════════════════════════════════════════════════════
// Packet processing timing tracking
// ══════════════════════════════════════════════════════════════════════════

void WebDiagnosticsTrackPacketProc(uint8_t token, uint32_t proc_us)
{
    g_web_packet_last_token = (uint32_t)token;
    g_web_packet_last_proc_us = proc_us;
    if (proc_us > g_web_packet_max_proc_us)
        g_web_packet_max_proc_us = proc_us;

    uint32_t* last = 0;
    uint32_t* max = 0;
    switch (token)
    {
        case 'r':
            last = &g_web_packet_r_last_proc_us;
            max = &g_web_packet_r_max_proc_us;
            break;
        case 'd':
            last = &g_web_packet_d_last_proc_us;
            max = &g_web_packet_d_max_proc_us;
            break;
        case 'k':
            last = &g_web_packet_k_last_proc_us;
            max = &g_web_packet_k_max_proc_us;
            break;
        default:
            break;
    }
    if (!last || !max)
        return;
    *last = proc_us;
    if (proc_us > *max)
        *max = proc_us;
}

// ══════════════════════════════════════════════════════════════════════════
// Server-loss provenance tracking
// ══════════════════════════════════════════════════════════════════════════

void WebDiagnosticsResetServerLossProvenance(void)
{
    g_web_server_first_live_seen = 0;
    g_web_server_first_live_stage[0] = 0;
    g_web_server_first_live_ptr = 0;
    g_web_server_first_live_lifecycle_seq = 0;
    g_web_server_first_live_join_generation = 0;
    g_web_server_first_null_after_live_seen = 0;
    g_web_server_first_null_after_live_stage[0] = 0;
    g_web_server_first_null_after_live_op = 0;
    g_web_server_first_null_after_live_size = 0;
    g_web_server_first_null_after_live_packet_calls = 0;
    g_web_server_first_null_after_live_packet_proc = 0;
    g_web_server_first_null_after_live_packet_null = 0;
    g_web_server_first_null_after_live_packet_defer = 0;
    g_web_server_first_null_after_live_pending = 0;
    g_web_server_first_null_after_live_canary_ok = -1;
    g_web_server_first_null_after_live_alloc_ptr = 0;
    g_web_server_first_null_after_live_alloc_server_ptr = 0;
    g_web_server_first_null_after_live_lifecycle_seq = 0;
    g_web_server_first_null_after_live_join_generation = 0;
    g_web_server_first_null_after_live_game_loading = -1;
    g_web_server_first_null_after_live_menu_progress = -1;
    g_web_server_first_null_after_live_world_bits = 0;
    g_web_server_first_null_after_live_sentinel_a = 0;
    g_web_server_first_null_after_live_sentinel_b = 0;
}

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
    uint32_t alloc_server_ptr)
{
    if (server_ptr && !g_web_server_first_live_seen)
    {
        g_web_server_first_live_seen = 1;
        snprintf(g_web_server_first_live_stage, sizeof(g_web_server_first_live_stage),
                 "%s", stage ? stage : "unknown");
        g_web_server_first_live_ptr = server_ptr;
        g_web_server_first_live_lifecycle_seq = g_web_server_lifecycle_seq;
        g_web_server_first_live_join_generation = join_generation;
    }

    if (server_ptr || !authoritative_join_active ||
        !g_web_server_first_live_seen || g_web_server_first_null_after_live_seen)
        return;

    g_web_server_first_null_after_live_seen = 1;
    snprintf(g_web_server_first_null_after_live_stage,
             sizeof(g_web_server_first_null_after_live_stage),
             "%s", stage ? stage : "unknown");
    g_web_server_first_null_after_live_op = packet_op;
    g_web_server_first_null_after_live_size = packet_size;
    g_web_server_first_null_after_live_packet_calls = packet_calls;
    g_web_server_first_null_after_live_packet_proc = packet_proc;
    g_web_server_first_null_after_live_packet_null = packet_null;
    g_web_server_first_null_after_live_packet_defer = packet_defer;
    g_web_server_first_null_after_live_pending = pending_count;
    g_web_server_first_null_after_live_canary_ok = canary_ok;
    g_web_server_first_null_after_live_alloc_ptr = alloc_ptr;
    g_web_server_first_null_after_live_alloc_server_ptr = alloc_server_ptr;
    g_web_server_first_null_after_live_lifecycle_seq = g_web_server_lifecycle_seq;
    g_web_server_first_null_after_live_join_generation = join_generation;
    g_web_server_first_null_after_live_game_loading = MainMenuWebGameLoadingState();
    g_web_server_first_null_after_live_menu_progress = MainMenuWebProgressState();
    g_web_server_first_null_after_live_world_bits = WebFL933WorldReadyBits();
    g_web_server_first_null_after_live_sentinel_a = g_web_fl933_sentinel_a;
    g_web_server_first_null_after_live_sentinel_b = g_web_fl933_sentinel_b;
}

// ══════════════════════════════════════════════════════════════════════════
// FL-933 game-stage canary stubs
// Called from game.cpp / mainmenu.cpp at InitGame / LoadGame stages.
// Full implementation deferred — these stubs preserve the call convention
// so the web build links without the diagnostic wiring going live yet.
// ══════════════════════════════════════════════════════════════════════════

extern "C" void WebFL933ServerPointerWatch(
    const char* /*stage*/,
    const void* /*game_ptr*/,
    uint32_t    /*game_size*/,
    const void* /*observed_player_head*/,
    const void* /*observed_player_tail*/)
{
    // FL-933 stub — full wiring pending
}

extern "C" int WebFL933AssertAuthoritativeServerPresent(
    const char* /*stage*/,
    const void* /*game_ptr*/,
    uint32_t    /*game_size*/,
    const void* /*observed_player_head*/,
    const void* /*observed_player_tail*/)
{
    // FL-933 stub — returns 1 (ok) until proper wiring is in place
    return 1;
}

// ══════════════════════════════════════════════════════════════════════════
// Lifecycle event ring buffer
// ══════════════════════════════════════════════════════════════════════════

void WebDiagnosticsOnLifecycleEvent(
    const char* event,
    uint32_t server_ptr,
    uint32_t alloc_ptr,
    uint32_t alloc_server_ptr,
    int canary_ok,
    int pending_count)
{
    snprintf(g_web_server_lifecycle_prev_event,
             sizeof(g_web_server_lifecycle_prev_event),
             "%s",
             g_web_server_lifecycle_event);
    snprintf(g_web_server_lifecycle_event,
             sizeof(g_web_server_lifecycle_event),
             "%s",
             event ? event : "unknown");
    g_web_server_lifecycle_seq++;
    g_web_fl933_lifecycle_epoch++;
    g_web_fl933_sentinel_a ^= 0x01010101u + g_web_server_lifecycle_seq;
    g_web_fl933_sentinel_b += 0x9e3779b9u ^ g_web_server_lifecycle_seq;

    WebFL933LifecycleEvent* slot =
        &g_web_fl933_lifecycle_ring[g_web_fl933_lifecycle_ring_write %
                                    (sizeof(g_web_fl933_lifecycle_ring) / sizeof(g_web_fl933_lifecycle_ring[0]))];
    memset(slot, 0, sizeof(*slot));
    slot->seq = g_web_server_lifecycle_seq;
    snprintf(slot->event, sizeof(slot->event), "%s", g_web_server_lifecycle_event);
    slot->server_ptr = server_ptr;
    slot->alloc_ptr = alloc_ptr;
    slot->alloc_server_ptr = alloc_server_ptr;
    slot->canary_ok = canary_ok;
    slot->pending_count = pending_count;
    slot->game_loading = MainMenuWebGameLoadingState();
    slot->menu_progress = MainMenuWebProgressState();
    slot->world_ready_bits = WebFL933WorldReadyBits();
    g_web_fl933_lifecycle_ring_write++;
    WebDiagnosticsObserveServerPointer(event, server_ptr, 0, 0, 0, 0, 0, 0, 0, 0,
        (uint32_t)pending_count, canary_ok, alloc_ptr, alloc_server_ptr);
}

// ══════════════════════════════════════════════════════════════════════════
// Pending queue token counts (data-driven, no globals)
// ══════════════════════════════════════════════════════════════════════════

void WebDiagnosticsPendingQueueTokenCounts(
    int pending_count,
    const uint16_t* pending_sizes,
    const uint8_t* pending_tokens,
    int* b, int* q, int* a, int* i)
{
    if (b) *b = 0;
    if (q) *q = 0;
    if (a) *a = 0;
    if (i) *i = 0;
    for (int n = 0; n < pending_count; n++)
    {
        uint8_t token = (pending_sizes && pending_tokens && pending_sizes[n] > 0) ? pending_tokens[n] : 0;
        if (token == 'b' && b) (*b)++;
        else if (token == 'q' && q) (*q)++;
        else if (token == 'a' && a) (*a)++;
        else if (token == 'i' && i) (*i)++;
    }
}

// ══════════════════════════════════════════════════════════════════════════
// JSON builders (build from private state)
// ══════════════════════════════════════════════════════════════════════════

const char* WebDiagnosticsBuildLifecycleRingJson(void)
{
    int used = 0;
    g_web_fl933_lifecycle_ring_json[0] = 0;
    WebDiagnosticsAppendProbeText(g_web_fl933_lifecycle_ring_json,
                    (int)sizeof(g_web_fl933_lifecycle_ring_json), used, "[");
    const uint32_t ring_cap = (uint32_t)(sizeof(g_web_fl933_lifecycle_ring) / sizeof(g_web_fl933_lifecycle_ring[0]));
    uint32_t count = g_web_fl933_lifecycle_ring_write < ring_cap ? g_web_fl933_lifecycle_ring_write : ring_cap;
    uint32_t start = g_web_fl933_lifecycle_ring_write > count ? (g_web_fl933_lifecycle_ring_write - count) : 0;
    for (uint32_t i = 0; i < count; i++)
    {
        const WebFL933LifecycleEvent* ev = &g_web_fl933_lifecycle_ring[(start + i) % ring_cap];
        WebDiagnosticsAppendProbeText(g_web_fl933_lifecycle_ring_json,
                        (int)sizeof(g_web_fl933_lifecycle_ring_json), used,
                        "%s{\"seq\":%u,\"event\":\"%s\",\"server\":%u,\"alloc\":%u,"
                        "\"alloc_server\":%u,\"canary\":%d,\"pending\":%d,"
                        "\"loading\":%d,\"progress\":%d,\"world_bits\":%d}",
                        i ? "," : "",
                        ev->seq,
                        ev->event,
                        ev->server_ptr,
                        ev->alloc_ptr,
                        ev->alloc_server_ptr,
                        ev->canary_ok,
                        ev->pending_count,
                        ev->game_loading,
                        ev->menu_progress,
                        ev->world_ready_bits);
    }
    WebDiagnosticsAppendProbeText(g_web_fl933_lifecycle_ring_json,
                    (int)sizeof(g_web_fl933_lifecycle_ring_json), used, "]");
    return g_web_fl933_lifecycle_ring_json;
}

const char* WebDiagnosticsBuildAnsiFrameSnapshotJson(const AnsiCell* render_buf)
{
    int used = 0;
    g_web_cpp_ansi_frame_snapshot_json[0] = 0;
    WebDiagnosticsAppendProbeText(g_web_cpp_ansi_frame_snapshot_json,
                    (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used, "{");
    WebDiagnosticsAppendJsonIntField(g_web_cpp_ansi_frame_snapshot_json, (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used,
                       "valid", (render_buf && g_web_render_buf_sample_valid) ? 1 : 0);
    WebDiagnosticsAppendJsonIntField(g_web_cpp_ansi_frame_snapshot_json, (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used,
                       "width", g_web_render_buf_sample_width);
    WebDiagnosticsAppendJsonIntField(g_web_cpp_ansi_frame_snapshot_json, (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used,
                       "height", g_web_render_buf_sample_height);
    WebDiagnosticsAppendJsonUIntField(g_web_cpp_ansi_frame_snapshot_json, (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used,
                        "hash", g_web_render_buf_hash);
    WebDiagnosticsAppendJsonUIntField(g_web_cpp_ansi_frame_snapshot_json, (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used,
                        "nonzero_cells", g_web_render_buf_nonzero_cells);
    WebDiagnosticsAppendJsonUIntField(g_web_cpp_ansi_frame_snapshot_json, (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used,
                        "nonzero_glyph_cells", g_web_render_buf_nonzero_glyph_cells);
    int cell_count = g_web_render_buf_sample_width * g_web_render_buf_sample_height;
    int max_cells = cell_count;
    int truncated = 0;
    if (!render_buf || cell_count <= 0 || cell_count > 160 * 160)
    {
        max_cells = 0;
        truncated = cell_count > 160 * 160 ? 1 : 0;
    }
    const int raw_hex_budget_cells = 30000;
    if (max_cells > raw_hex_budget_cells)
    {
        max_cells = raw_hex_budget_cells;
        truncated = 1;
    }
    WebDiagnosticsAppendJsonIntField(g_web_cpp_ansi_frame_snapshot_json, (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used,
                       "raw_cell_count", max_cells);
    WebDiagnosticsAppendJsonIntField(g_web_cpp_ansi_frame_snapshot_json, (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used,
                       "truncated", truncated);
    WebDiagnosticsAppendProbeText(g_web_cpp_ansi_frame_snapshot_json,
                    (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used,
                    ",\"raw_hex\":\"");
    static const char hex[] = "0123456789abcdef";
    const uint8_t* bytes = (const uint8_t*)render_buf;
    const int byte_count = max_cells * (int)sizeof(AnsiCell);
    for (int i = 0; i < byte_count; i++)
    {
        if (used + 3 >= (int)sizeof(g_web_cpp_ansi_frame_snapshot_json))
        {
            truncated = 1;
            break;
        }
        g_web_cpp_ansi_frame_snapshot_json[used++] = hex[(bytes[i] >> 4) & 0x0f];
        g_web_cpp_ansi_frame_snapshot_json[used++] = hex[bytes[i] & 0x0f];
        g_web_cpp_ansi_frame_snapshot_json[used] = 0;
    }
    WebDiagnosticsAppendProbeText(g_web_cpp_ansi_frame_snapshot_json,
                    (int)sizeof(g_web_cpp_ansi_frame_snapshot_json), used,
                    "\",\"truncated_after_write\":%d}", truncated);
    return g_web_cpp_ansi_frame_snapshot_json;
}

// ══════════════════════════════════════════════════════════════════════════
// Recorder bridge mode (EM_ASM bridge)
// ══════════════════════════════════════════════════════════════════════════

WebRecorderBridgeMode ActiveWebRecorderBridgeMode()
{
    int raw_mode = EM_ASM_INT({
        if (typeof Module !== 'undefined' && typeof Module.webRecorderBridgeMode === 'number')
            return Module.webRecorderBridgeMode | 0;
        if (typeof globalThis !== 'undefined' && typeof globalThis.webRecorderBridgeMode === 'number')
            return globalThis.webRecorderBridgeMode | 0;
        return 0;
    });
    return WebRecorderBridgeClampMode(raw_mode);
}

// ══════════════════════════════════════════════════════════════════════════
// Transitional accessor functions
// ══════════════════════════════════════════════════════════════════════════

uint32_t WebDiagnosticsGetPacketHistB(void) { return g_web_packet_hist_b; }
uint32_t WebDiagnosticsGetPacketHistQ(void) { return g_web_packet_hist_q; }
uint32_t WebDiagnosticsGetPacketHistA(void) { return g_web_packet_hist_a; }
uint32_t WebDiagnosticsGetPacketHistI(void) { return g_web_packet_hist_i; }
uint32_t WebDiagnosticsGetPacketHistJ(void) { return g_web_packet_hist_j; }
uint32_t WebDiagnosticsGetPacketHistN(void) { return g_web_packet_hist_n; }

uint32_t WebDiagnosticsGetPacketLastToken(void) { return g_web_packet_last_token; }
uint32_t WebDiagnosticsGetPacketLastProcUs(void) { return g_web_packet_last_proc_us; }
uint32_t WebDiagnosticsGetPacketMaxProcUs(void) { return g_web_packet_max_proc_us; }
uint32_t WebDiagnosticsGetPacketRLastProcUs(void) { return g_web_packet_r_last_proc_us; }
uint32_t WebDiagnosticsGetPacketRMaxProcUs(void) { return g_web_packet_r_max_proc_us; }
uint32_t WebDiagnosticsGetPacketDLastProcUs(void) { return g_web_packet_d_last_proc_us; }
uint32_t WebDiagnosticsGetPacketDMaxProcUs(void) { return g_web_packet_d_max_proc_us; }
uint32_t WebDiagnosticsGetPacketKLastProcUs(void) { return g_web_packet_k_last_proc_us; }
uint32_t WebDiagnosticsGetPacketKMaxProcUs(void) { return g_web_packet_k_max_proc_us; }

uint32_t WebDiagnosticsGetPendingDroppedTokenB(void) { return g_web_pending_dropped_token_b; }
uint32_t WebDiagnosticsGetPendingDroppedTokenQ(void) { return g_web_pending_dropped_token_q; }
uint32_t WebDiagnosticsGetPendingDroppedTokenA(void) { return g_web_pending_dropped_token_a; }
uint32_t WebDiagnosticsGetPendingDroppedTokenI(void) { return g_web_pending_dropped_token_i; }
uint32_t WebDiagnosticsGetPendingDroppedTokenJ(void) { return g_web_pending_dropped_token_j; }
uint32_t WebDiagnosticsGetPendingDroppedTokenN(void) { return g_web_pending_dropped_token_n; }

int WebDiagnosticsGetRenderBufSampleValid(void) { return g_web_render_buf_sample_valid; }
int WebDiagnosticsGetRenderBufSampleWidth(void) { return g_web_render_buf_sample_width; }
int WebDiagnosticsGetRenderBufSampleHeight(void) { return g_web_render_buf_sample_height; }
uint32_t WebDiagnosticsGetRenderBufSampleCells(void) { return g_web_render_buf_sample_cells; }
uint32_t WebDiagnosticsGetRenderBufNonzeroCells(void) { return g_web_render_buf_nonzero_cells; }
uint32_t WebDiagnosticsGetRenderBufNonzeroGlyphCells(void) { return g_web_render_buf_nonzero_glyph_cells; }
uint32_t WebDiagnosticsGetRenderBufHash(void) { return g_web_render_buf_hash; }
uint32_t WebDiagnosticsGetRenderBufProbeSeq(void) { return g_web_render_buf_probe_seq; } // FL-4079
const uint8_t* WebDiagnosticsGetRenderBufCenterFg(void) { return g_web_render_buf_center_fg; }
const uint8_t* WebDiagnosticsGetRenderBufCenterBk(void) { return g_web_render_buf_center_bk; }
const uint8_t* WebDiagnosticsGetRenderBufCenterGl(void) { return g_web_render_buf_center_gl; }
const uint8_t* WebDiagnosticsGetRenderBufCenterSpare(void) { return g_web_render_buf_center_spare; }

const WebDiagnosticsLifecycleSnapshot* WebDiagnosticsGetLifecycleSnapshot(void)
{
    g_web_lifecycle_snapshot.seq = g_web_server_lifecycle_seq;
    snprintf(g_web_lifecycle_snapshot.event, sizeof(g_web_lifecycle_snapshot.event),
        "%s", g_web_server_lifecycle_event);
    snprintf(g_web_lifecycle_snapshot.prev_event, sizeof(g_web_lifecycle_snapshot.prev_event),
        "%s", g_web_server_lifecycle_prev_event);
    g_web_lifecycle_snapshot.server_ptr_ok = g_web_server_lifecycle_server_ptr_ok;
    g_web_lifecycle_snapshot.alloc_ok = g_web_server_lifecycle_alloc_ok;
    g_web_lifecycle_snapshot.canary_ok = g_web_server_lifecycle_canary_ok;
    g_web_lifecycle_snapshot.sentinel_a = g_web_fl933_sentinel_a;
    g_web_lifecycle_snapshot.sentinel_b = g_web_fl933_sentinel_b;
    g_web_lifecycle_snapshot.epoch = g_web_fl933_lifecycle_epoch;
    return &g_web_lifecycle_snapshot;
}

const WebDiagnosticsServerLossProvenanceSnapshot* WebDiagnosticsGetServerLossProvenanceSnapshot(void)
{
    g_web_server_loss_snapshot.first_live_seen = g_web_server_first_live_seen;
    snprintf(g_web_server_loss_snapshot.first_live_stage,
        sizeof(g_web_server_loss_snapshot.first_live_stage),
        "%s", g_web_server_first_live_stage);
    g_web_server_loss_snapshot.first_live_ptr = g_web_server_first_live_ptr;
    g_web_server_loss_snapshot.first_live_lifecycle_seq = g_web_server_first_live_lifecycle_seq;
    g_web_server_loss_snapshot.first_live_join_generation = g_web_server_first_live_join_generation;
    g_web_server_loss_snapshot.first_null_after_live_seen = g_web_server_first_null_after_live_seen;
    snprintf(g_web_server_loss_snapshot.first_null_after_live_stage,
        sizeof(g_web_server_loss_snapshot.first_null_after_live_stage),
        "%s", g_web_server_first_null_after_live_stage);
    g_web_server_loss_snapshot.first_null_after_live_op = g_web_server_first_null_after_live_op;
    g_web_server_loss_snapshot.first_null_after_live_size = g_web_server_first_null_after_live_size;
    g_web_server_loss_snapshot.first_null_after_live_packet_calls = g_web_server_first_null_after_live_packet_calls;
    g_web_server_loss_snapshot.first_null_after_live_packet_proc = g_web_server_first_null_after_live_packet_proc;
    g_web_server_loss_snapshot.first_null_after_live_packet_null = g_web_server_first_null_after_live_packet_null;
    g_web_server_loss_snapshot.first_null_after_live_packet_defer = g_web_server_first_null_after_live_packet_defer;
    g_web_server_loss_snapshot.first_null_after_live_pending = g_web_server_first_null_after_live_pending;
    g_web_server_loss_snapshot.first_null_after_live_canary_ok = g_web_server_first_null_after_live_canary_ok;
    g_web_server_loss_snapshot.first_null_after_live_alloc_ptr = g_web_server_first_null_after_live_alloc_ptr;
    g_web_server_loss_snapshot.first_null_after_live_alloc_server_ptr = g_web_server_first_null_after_live_alloc_server_ptr;
    g_web_server_loss_snapshot.first_null_after_live_lifecycle_seq = g_web_server_first_null_after_live_lifecycle_seq;
    g_web_server_loss_snapshot.first_null_after_live_join_generation = g_web_server_first_null_after_live_join_generation;
    g_web_server_loss_snapshot.first_null_after_live_game_loading = g_web_server_first_null_after_live_game_loading;
    g_web_server_loss_snapshot.first_null_after_live_menu_progress = g_web_server_first_null_after_live_menu_progress;
    g_web_server_loss_snapshot.first_null_after_live_world_bits = g_web_server_first_null_after_live_world_bits;
    g_web_server_loss_snapshot.first_null_after_live_sentinel_a = g_web_server_first_null_after_live_sentinel_a;
    g_web_server_loss_snapshot.first_null_after_live_sentinel_b = g_web_server_first_null_after_live_sentinel_b;
    return &g_web_server_loss_snapshot;
}

// ══════════════════════════════════════════════════════════════════════════
// Extern "C" exports for diagnostics
// ══════════════════════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════════════════════
// Server-loss provenance tracking accessors
// ══════════════════════════════════════════════════════════════════════════

int WebDiagnosticsGetServerFirstLiveSeen(void) { return g_web_server_first_live_seen; }
const char* WebDiagnosticsGetServerFirstLiveStage(void) { return g_web_server_first_live_stage; }
uint32_t WebDiagnosticsGetServerFirstLivePtr(void) { return g_web_server_first_live_ptr; }
uint32_t WebDiagnosticsGetServerFirstLiveLifecycleSeq(void) { return g_web_server_first_live_lifecycle_seq; }
uint32_t WebDiagnosticsGetServerFirstLiveJoinGeneration(void) { return g_web_server_first_live_join_generation; }

int WebDiagnosticsGetServerFirstNullAfterLiveSeen(void) { return g_web_server_first_null_after_live_seen; }
const char* WebDiagnosticsGetServerFirstNullAfterLiveStage(void) { return g_web_server_first_null_after_live_stage; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveOp(void) { return g_web_server_first_null_after_live_op; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveSize(void) { return g_web_server_first_null_after_live_size; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLivePacketCalls(void) { return g_web_server_first_null_after_live_packet_calls; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLivePacketProc(void) { return g_web_server_first_null_after_live_packet_proc; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLivePacketNull(void) { return g_web_server_first_null_after_live_packet_null; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLivePacketDefer(void) { return g_web_server_first_null_after_live_packet_defer; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLivePending(void) { return g_web_server_first_null_after_live_pending; }
int WebDiagnosticsGetServerFirstNullAfterLiveCanaryOk(void) { return g_web_server_first_null_after_live_canary_ok; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveAllocPtr(void) { return g_web_server_first_null_after_live_alloc_ptr; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveAllocServerPtr(void) { return g_web_server_first_null_after_live_alloc_server_ptr; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveLifecycleSeq(void) { return g_web_server_first_null_after_live_lifecycle_seq; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveJoinGeneration(void) { return g_web_server_first_null_after_live_join_generation; }
int WebDiagnosticsGetServerFirstNullAfterLiveGameLoading(void) { return g_web_server_first_null_after_live_game_loading; }
int WebDiagnosticsGetServerFirstNullAfterLiveMenuProgress(void) { return g_web_server_first_null_after_live_menu_progress; }
int WebDiagnosticsGetServerFirstNullAfterLiveWorldBits(void) { return g_web_server_first_null_after_live_world_bits; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveSentinelA(void) { return g_web_server_first_null_after_live_sentinel_a; }
uint32_t WebDiagnosticsGetServerFirstNullAfterLiveSentinelB(void) { return g_web_server_first_null_after_live_sentinel_b; }

uint32_t WebDiagnosticsGetLifecycleSeq(void) { return g_web_server_lifecycle_seq; }
const char* WebDiagnosticsGetLifecycleEvent(void) { return g_web_server_lifecycle_event; }
const char* WebDiagnosticsGetLifecyclePrevEvent(void) { return g_web_server_lifecycle_prev_event; }
int WebDiagnosticsGetLifecycleServerPtrOk(void) { return g_web_server_lifecycle_server_ptr_ok; }
int WebDiagnosticsGetLifecycleAllocOk(void) { return g_web_server_lifecycle_alloc_ok; }
int WebDiagnosticsGetLifecycleCanaryOk(void) { return g_web_server_lifecycle_canary_ok; }
uint32_t WebDiagnosticsGetFl933SentinelA(void) { return g_web_fl933_sentinel_a; }
uint32_t WebDiagnosticsGetFl933SentinelB(void) { return g_web_fl933_sentinel_b; }
uint32_t WebDiagnosticsGetFl933LifecycleEpoch(void) { return g_web_fl933_lifecycle_epoch; }

WebDiagnosticsTerrainSample WebDiagnosticsSampleTerrainAt(float x, float y)
{
    const WebFL933TerrainSample sample = WebFL933SampleTerrainAt(x, y);
    WebDiagnosticsTerrainSample out = {};
    out.valid = sample.valid;
    out.patch_present = sample.patch_present;
    out.height_raw = sample.height_raw;
    out.material_id = sample.material_id;
    return out;
}

// ══════════════════════════════════════════════════════════════════════════

extern "C"
{
    EMSCRIPTEN_KEEPALIVE int GetRenderStageCode()
    {
        return g_web_render_stage_code;
    }

    EMSCRIPTEN_KEEPALIVE const char* GetCppAnsiFrameSnapshotJson()
    {
        extern AnsiCell* render_buf;
        return WebDiagnosticsBuildAnsiFrameSnapshotJson(render_buf);
    }

    EMSCRIPTEN_KEEPALIVE const char* GetLifecycleRingJson()
    {
        return WebDiagnosticsBuildLifecycleRingJson();
    }
}
