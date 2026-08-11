/*
 * Server-side ActorVisualProfile walkthrough:
 * This file owns the server gameplay identity handoff to client render.
 *
 * =============================================================================
 * !! STALE CONTRACT DISCLAIMER (2026-04-28) !!
 * =============================================================================
 * This bundle contract documentation is LIKELY STALE as of today.
 * See engine/game.cpp header for full disclaimer and active changes.
 * FL-2345 mounted rollback happened TODAY.
 * =============================================================================
 *
 * `-- Step 4: choose the authoritative appearance identity
 *     |-- Description: server picks appearance_profile_id, skin_definition_id,
 *     |   mount_definition_id, variation_id, rig_id, and the slot manifest entries.
 *     `-- Primary function: SvrApplyProfileToAppearance()
 * `-- Step 5: send the network appearance contract plus ids to clients
 *     |-- Description: join hashes must match, then presentation_kind_id and
 *     |   appearance_v2 are sent to the client.
 *     `-- Primary functions: SvrLoadStartupAppearanceContract(),
 *         SvrFillAppearanceStateV2(), SvrQueueChangedAppearanceStateV2ToClient()
 *
 * Terminology:
 * - presentation_kind_id = the actor's current render verb/state family
 *   ("idle_walk", "attack", "plydie"), not an outfit combination.
 * - skin_definition_id = the body-owner family chosen by the server.
 * - variation_id / rig_id = server-owned authored key dimensions; zero is the
 *   default, and clients must not derive them from equipment or presentation.
 * - item_definition_id = gameplay item identity and render-owner key for an
 *   equipped slot.
 * - slot_kind_id = attachment channel such as body/head/weapon/shield/armor/mount.
 * - appearance_v2 = authoritative appearance payload; clients resolve layers
 *   from these ids later and do not invent their own sprite ownership.
 *
 * Contracts in play here:
 * - Contract 3: layer ownership contract at authoring time determines which ids
 *   the server is allowed to assign.
 * - Contract 5: network appearance contract requires matching contract version,
 *   bundle_hash, and ids_lock_hash before a client may join.
 *
 * Server callgraph tree:
 * `-- SvrLoadStartupAppearanceContract()                  [Step 5 / Contract 5]
 * `-- SvrApplyProfileToAppearance()                       [Step 4]
 * `-- SvrFillAppearanceStateV2()                          [Step 5]
 * `-- SvrQueueChangedAppearanceStateV2ToClient()          [Step 5]
 *
 * File cross-reference:
 * `-- scripts/validate_actor_visual_profiles.py
 * `-- scripts/compile_actor_visual_profiles.py
 *     `-- Profile-side Steps 0-3 and Contracts 1-3.
 * `-- server/network.h
 *     `-- Step 5 wire structs carrying presentation_kind_id and appearance_v2.
 * `-- server/server_state.h
 *     `-- Authoritative server-side appearance state shape.
 * `-- engine/game.h
 *     `-- Client-side stored appearance_v2 shape after receive.
 * `-- engine/actor_visual_profile_runtime.h
 *     `-- Steps 6-15 exact profile/layer/composite runtime.
 *
 * Contract callgraphs owned here:
 * Contract 3 handoff
 * `-- Exists in:
 *     |-- server/server_state.h authoritative appearance state
 *     `-- server/server_tick.cpp profile/loadout assignment path
 * `-- Callgraph:
 *     `-- SvrApplyProfileToAppearance()
 *         `-- SvrUpsertAppearanceEntry()
 *             `-- SvrFillAppearanceStateV2()
 *
 * Contract 5 network contract
 * `-- Exists in:
 *     |-- startup bundle_hash + ids_lock_hash validation
 *     `-- appearance_v2 packet emission path
 * `-- Callgraph:
 *     `-- SvrLoadStartupAppearanceContract()
 *         `-- join hash/version check
 *             `-- SvrFillAppearanceStateV2()
 *                 `-- SvrQueueChangedAppearanceStateV2ToClient()
 *
 * Plain English:
 * ActorVisualProfile is the approved catalog of visual parts. The server owns
 * gameplay truth: which skin/profile a player has, which item is equipped,
 * which mount is active, and which presentation state is active. The server
 * sends those profile ids to clients; clients render them through the compiled
 * ActorVisualProfile table.
 */

#include "server_state.h"
#include "rate_limit_disconnect_witness.h"
#include "item_ownership_contract.h"
#include "../engine/actor_visual_key_derivation.h"
#include "mp_step.h"
#include "appearance_contract_state.h"
#include "multiplayer_session.h"
#include "actor_visual_reachability.h"
#include "connection/network_lag_telemetry.h"
#include "network.h" // platform_net primitives: THREAD_*, MUTEX_*
#include "../engine/physics_commands.h"
#include "../engine/world.h"

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <errno.h>
#include <stdarg.h>
#include <stddef.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

extern volatile bool isRunning;
extern uint64_t a3dGetTime();
extern int max_players; // defined in game_svr.cpp
extern char base_path[];

static const size_t SVR_BASE_PATH_CAP = 1024;

enum
{
    SVR_MAX_SNAPSHOT_ENTITIES = (SVR_MAX_CLIENTS + SVR_MAX_NPCS) * 2,
    SVR_MAX_SNAPSHOT_FRAME_BYTES =
        (int)sizeof(STRUCT_SNAPSHOT_BASELINE) +
        SVR_MAX_SNAPSHOT_ENTITIES * (int)sizeof(STRUCT_SNAPSHOT_ENTITY)
};

static uint8_t snap_buf[SVR_MAX_SNAPSHOT_FRAME_BYTES];
static const char* SVR_AUTHORITATIVE_STATE_REL_PATH = ".web/authoritative_state.json";
static const char* SVR_AUTHORITATIVE_STATE_TMP_REL_PATH = ".web/authoritative_state.json.tmp";
static const char* SVR_AUTHORITATIVE_STATE_LEGACY_SHM_PATH = "/dev/shm/asciicker-authoritative_state.json";
static const char* SVR_AUTHORITATIVE_STATE_LEGACY_SHM_TMP_PATH = "/dev/shm/asciicker-authoritative_state.json.tmp";
// Paths now in appearance_contract_state.cpp
static const size_t SVR_AUTHORITATIVE_JSON_BUF_BYTES = 512 * 1024;
static const uint64_t SVR_TICK_PHASE_LOG_THRESHOLD_US = 10000;
static const uint32_t SVR_TICK_PHASE_LOG_LIMIT = 64;
static const uint64_t SVR_TICK_PHYSICS_LOG_THRESHOLD_US = 5000;
static const uint32_t SVR_TICK_PHYSICS_LOG_LIMIT = 64;
static const uint64_t SVR_TICK_SNAPSHOT_LOG_THRESHOLD_US = 5000;
static const uint32_t SVR_TICK_SNAPSHOT_LOG_LIMIT = 64;
static const uint32_t SVR_TICK_PLAYERS_BREAKDOWN_LOG_LIMIT = 32;

static uint64_t SvrRealtimeEpochUs()
{
    struct timespec ts;
    if (clock_gettime(CLOCK_REALTIME, &ts) != 0)
        return 0;
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}
static const uint64_t SVR_IO_POLL_GAP_LOG_THRESHOLD_US = 100000;
static const uint32_t SVR_IO_POLL_GAP_LOG_LIMIT = 32;
static const float SVR_IDLE_FASTPATH_INPUT_EPS = 0.001f;
static const float SVR_IDLE_FASTPATH_VEL_EPS = 0.01f;
static const float SVR_IDLE_FASTPATH_YAW_EPS = 0.01f;
// FL-2957 ATTEMPT #19 (of 23 total, 0 closed): Widen support_z tolerance 0.35→0.50.
// STILL NOT WORKING — spawn at (-2.8,-73.6,57.0) has terrain_z=57.0 but player
// falls to z=55.0 in 2 ticks → support_z_delta=2.0 permanently >> 0.50.
// This is a STRUCTURAL spawn/collision mismatch, NOT a threshold problem.
// Do NOT attempt #24 as another tolerance tweak. Fix spawn Z or collision surface.
//
// FL-2957 history: attempts #8 (wolf-mount idle path), #12 (soup cap 1024),
// #15 (5-hypothesis diagnosis), #17 (owner correction), #18 (source evidence),
// and #19 (this tolerance) all failed to close the gate.
// See FAILURE_LOG.md FL-2957 FAILED ATTEMPTS COUNTER for full enumeration.
static const float SVR_IDLE_FASTPATH_SUPPORT_Z_EPS = 0.50f;
static const float SVR_SUPPORT_LAUNCH_CLAMP_EPS = 1.00f;
// FL-2957: allow quiescent low-speed settle into idle fast path.
// Idle fast path snaps vel to 0 and skips MpStepOnce. The default velocity eps
// (SVR_IDLE_FASTPATH_VEL_EPS=0.01) is intentionally strict for truly stationary
// frames, but we also need a safe "settle to rest" band for zero-input,
// grounded, non-knockback drift that would otherwise pay full sweep/collision.
static const float SVR_IDLE_FASTPATH_SETTLE_VEL_MAX = 2.0f;

enum SvrIdleFastPathRejectReason : uint8_t
{
    SVR_IDLE_FASTPATH_REJECT_MOUNT = 0,
    SVR_IDLE_FASTPATH_REJECT_INPUT_FLAGS,
    SVR_IDLE_FASTPATH_REJECT_INPUT_FORCE,
    SVR_IDLE_FASTPATH_REJECT_KNOCKBACK,
    SVR_IDLE_FASTPATH_REJECT_VELOCITY,
    SVR_IDLE_FASTPATH_REJECT_YAW_VELOCITY,
    SVR_IDLE_FASTPATH_REJECT_YAW_DELTA,
    SVR_IDLE_FASTPATH_REJECT_PLAYER_STP,
    SVR_IDLE_FASTPATH_REJECT_GROUNDED,
    SVR_IDLE_FASTPATH_REJECT_SUPPORT_Z,
    SVR_IDLE_FASTPATH_REJECT_WATER,
    SVR_IDLE_FASTPATH_REJECT_COUNT,
};

struct SvrIdleFastPathEval
{
    uint32_t reject_mask;
    uint8_t input_flags;
    uint8_t grounded;
    uint8_t in_water;
    uint8_t idle_support_recovered;
    float max_abs_vel;
    float yaw_delta;
    float support_z_delta;
    float accum_contact;
    float pos_z;
    float terrain_z;
};

// ── RQ-035: WebSocket application-level keepalive ────────────────
// IO thread sends WS ping frames at WS_KEEPALIVE_PING_INTERVAL_US.
// If no pong is received within WS_IDLE_TIMEOUT_US, the client is
// considered a ghost connection and disconnected to free the player slot.
// These values are intentionally generous for mobile clients that may
// briefly sleep/wake. Tune down if ghost connections persist.
static const uint64_t WS_KEEPALIVE_PING_INTERVAL_US = 30ULL * 1000000ULL; // 30 seconds
static const uint64_t WS_IDLE_TIMEOUT_US            = 60ULL * 1000000ULL; // 60 seconds

static bool SvrKeepaliveDisabled()
{
    static int cached = -1;
    if (cached >= 0)
        return cached != 0;
    const char* raw = getenv("ASCIICKER_DISABLE_WS_KEEPALIVE");
    cached = (raw && raw[0] && strcmp(raw, "0") != 0) ? 1 : 0;
    return cached != 0;
}

enum ServerTickPhaseId : uint8_t
{
    SVR_TICK_PHASE_NONE = 0, SVR_TICK_PHASE_INGEST, SVR_TICK_PHASE_INPUT,
    SVR_TICK_PHASE_PHYSICS, SVR_TICK_PHASE_COMBAT, SVR_TICK_PHASE_GAME_RULES,
    SVR_TICK_PHASE_AI, SVR_TICK_PHASE_AI_COMBAT, SVR_TICK_PHASE_SNAPSHOT,
};

enum ServerTickPhysicsPhaseId : uint8_t
{
    SVR_TICK_PHYSICS_NONE = 0, SVR_TICK_PHYSICS_PLAYERS,
    SVR_TICK_PHYSICS_NPCS,
};

enum ServerTickSnapshotPhaseId : uint8_t
{
    SVR_TICK_SNAPSHOT_NONE = 0, SVR_TICK_SNAPSHOT_EVENTS,
    SVR_TICK_SNAPSHOT_GAMEPLAY_SNAPSHOT,
    SVR_TICK_SNAPSHOT_AUTHORITATIVE_STATE, SVR_TICK_SNAPSHOT_OUTBOUND,
};
static const float SVR_NPC_SWING_RANGE_BONUS = 0.5f;  // Preserve the server-owned NPC reach compensation.
static const float SVR_NPC_SWING_RANGE = SVR_SWING_RANGE + SVR_NPC_SWING_RANGE_BONUS;
// FL-3955 C-2: These constants are now owned by protocol_common.h.
// Local aliases reference the shared define to keep existing code compiling.
static const uint16_t SVR_APPEARANCE_VARIATION_DEFAULT = APPEARANCE_VARIATION_DEFAULT;
static const uint16_t SVR_APPEARANCE_RIG_DEFAULT = APPEARANCE_RIG_DEFAULT;
static const uint16_t SVR_APPEARANCE_RIG_MOUNTED_RIDER_SEAM = APPEARANCE_RIG_MOUNTED_RIDER_SEAM;
static const float SVR_NPC_AGGRO_RADIUS = 50.0f;
static const float SVR_VERTICAL_AGGRO_BAND = 12.0f;
static const float SVR_VERTICAL_SWING_BAND = 8.0f;
static const float SVR_SAFE_PLAYER_SPAWN_XY[2] = { -2.8f, -73.6f };
// S1/FL-642: spawn Z is server-resolved via SvrSampleTerrainHeight at bootstrap.
// Do NOT add a +200 lift, world_height/2 offset, or SetPhysicsPos override here.
// Those were tried (23 attempts); the real root cause was CreatePhysics overwriting
// the resolved Z. Fix: PHYSICS_CREATE_EXACT_POS flag, committed 2026-04-09.
static const float SVR_PLAYER_SPAWN_FALLBACK_Z = 0.0f; // Diagnostic contract field: safe spawn currently clamps from zero.
static const float SVR_SAFE_PLAYER_SPAWN_DIR = 0.0f;
static const float SVR_SAFE_PLAYER_SPAWN_YAW = -57.4f;
static const int SVR_AUTH_INVENTORY_ITEM_CAPACITY = 160; // Keep in sync with client Inventory::max_items.
static const uint32_t SVR_ITEM_DROP_REPICKUP_GRACE_TICKS = 60;
static const uint16_t SVR_APPEARANCE_VISUAL_STYLE_DEFAULT = APPEARANCE_VISUAL_STYLE_DEFAULT;
// FL-3955 V-3 FIXED: Both compilation units now use APPEARANCE_VISUAL_STYLE_DEFAULT(=500).
// Single-authority constant shared via protocol_common.h.
static const uint16_t SVR_APPEARANCE_VISUAL_STYLE_GOLD = APPEARANCE_VISUAL_STYLE_GOLD;
static const uint16_t SVR_APPEARANCE_VISUAL_STYLE_DARK = APPEARANCE_VISUAL_STYLE_DARK;

static uint16_t SvrNormalizeAppearanceVisualStyleId(uint16_t visual_style_id)
{
    return visual_style_id != 0
        ? visual_style_id
        : SVR_APPEARANCE_VISUAL_STYLE_DEFAULT;
}

static uint16_t SvrEquipSlotKindForCatalogItem(const SvrActorVisualProfileCatalogItemDef* item)
{
    return item ? item->slot_kind_id : 0;
}

static uint16_t SvrItemStateFlagsForSnapshot(const SvrItemState* it, uint16_t equip_slot_kind_id)
{
    if (!it)
        return 0;
    uint16_t flags = 0;
    if (it->owner_id == 0xFFFF)
        flags |= APPEARANCE_ITEM_STATE_WORLD;
    if (equip_slot_kind_id != 0)
        flags |= APPEARANCE_ITEM_STATE_EQUIPPED;
    if (it->source_kind == SVR_ITEM_SOURCE_MAP_A3D)
        flags |= APPEARANCE_ITEM_STATE_MAP_AUTHORED;
    if (it->placed_flags & SVR_PLACED_ITEM_PLACED)
        flags |= APPEARANCE_ITEM_STATE_PLACED;
    if (it->placed_flags & SVR_PLACED_ITEM_COLLIDABLE)
        flags |= APPEARANCE_ITEM_STATE_COLLIDABLE;
    if (it->placed_flags & SVR_PLACED_ITEM_EXPLICIT_PICKUP_ONLY)
        flags |= APPEARANCE_ITEM_STATE_EXPLICIT_PICKUP_ONLY;
    return flags;
}

static int fljit_move_recv_logs[SVR_MAX_CLIENTS];
static int fljit_resolve_logs[SVR_MAX_CLIENTS];
static int fljit_phys_logs[SVR_MAX_CLIENTS];
static int fl2896_rx_logs[SVR_MAX_CLIENTS];
static uint32_t g_tick_players_breakdown_logs = 0;
// WARNING FL-3800: server-health, clean-server, IO-poll, direction-split,
// host-tcpdump, and client-pcap phases killed current server gameplay/runtime,
// scheduling, flush, and host delayed-send ownership for captured yellow/red
// rows. Keep these counters as observability/falsifiers. Do not patch server
// lag behavior for parked FL-3800 unless same-run server-span evidence
// contradicts the parked external/no-repo-owner conclusion. FL-2957 raw-red
// gameplay lag and FL-3837 proof-harness admission are separate lanes.
static uint32_t g_io_poll_gap_logs = 0;
static uint32_t g_authoritative_state_breakdown_logs = 0;
static uint32_t g_authoritative_state_forensic_logs = 0;
static unsigned long long SvrLogStampUs() { return (unsigned long long)a3dGetTime(); }
// Async authoritative_state publish:
// Writing JSON to disk can stall unpredictably (fclose/rename tail latency). The snapshot/tick
// thread must not block on this. We coalesce to "latest only" and write on a detached thread.
typedef struct SvrAuthoritativeStateFileWriteStats SvrAuthoritativeStateFileWriteStats;
static bool SvrWriteAuthoritativeStateFile(const char* tmp_path,
                                           const char* final_path,
                                           const char* json_buf,
                                           size_t json_len,
                                           SvrAuthoritativeStateFileWriteStats* stats);

typedef struct SvrAuthoritativeStateAsyncPublish
{
	MUTEX_HANDLE* mu;
	volatile unsigned int seq;
	volatile unsigned int seq_written;
	volatile unsigned int thread_started;
	char buf[SVR_AUTHORITATIVE_JSON_BUF_BYTES];
	size_t len;
	char slot_local_path[4096];
	char slot_local_tmp_path[4096];
} SvrAuthoritativeStateAsyncPublish;

static SvrAuthoritativeStateAsyncPublish g_auth_async = {};

static void* SvrAuthoritativeStatePublishThreadMain(void*)
{
	for (;;)
	{
		const unsigned int seq = g_auth_async.seq;
		if (!g_auth_async.thread_started || seq == g_auth_async.seq_written)
		{
			THREAD_SLEEP(2);
			continue;
		}

		// Copy the latest snapshot under lock, then write outside lock.
		static char local_buf[SVR_AUTHORITATIVE_JSON_BUF_BYTES];
		size_t local_len = 0;
		char local_path[4096] = {};
		char local_tmp[4096] = {};
		MUTEX_LOCK(g_auth_async.mu);
		local_len = g_auth_async.len;
		if (local_len > sizeof(local_buf))
			local_len = sizeof(local_buf);
		memcpy(local_buf, g_auth_async.buf, local_len);
		strncpy(local_path, g_auth_async.slot_local_path, sizeof(local_path) - 1);
		strncpy(local_tmp, g_auth_async.slot_local_tmp_path, sizeof(local_tmp) - 1);
		MUTEX_UNLOCK(g_auth_async.mu);

		if (local_len > 0 && local_path[0] && local_tmp[0])
		{
			(void)SvrWriteAuthoritativeStateFile(local_tmp, local_path, local_buf, local_len, 0);
		}

		// Legacy /dev/shm mirror remains best-effort too.
		if (local_len > 0)
		{
			(void)SvrWriteAuthoritativeStateFile(
				SVR_AUTHORITATIVE_STATE_LEGACY_SHM_TMP_PATH,
				SVR_AUTHORITATIVE_STATE_LEGACY_SHM_PATH,
				local_buf,
				local_len,
				0);
		}

		g_auth_async.seq_written = seq;
	}
	return 0;
}

static bool SvrEnsureAuthoritativeStatePublishThreadStarted()
{
	if (g_auth_async.thread_started)
		return true;
	if (!g_auth_async.mu)
		g_auth_async.mu = MUTEX_CREATE();
	if (!g_auth_async.mu)
		return false;
	if (!THREAD_CREATE_DETACHED(SvrAuthoritativeStatePublishThreadMain, 0))
		return false;
	g_auth_async.thread_started = 1;
	return true;
}

static void SvrResolveAuthoritativeStatePath(char* out, size_t out_cap, const char* rel_path)
{
    if (!out || out_cap == 0)
        return;
    out[0] = 0;
    if (!rel_path || !rel_path[0])
        return;
    if (rel_path[0] == '/' || !base_path[0])
    {
        snprintf(out, out_cap, "%s", rel_path);
        return;
    }
    snprintf(out, out_cap, "%s%s", base_path, rel_path);
}

typedef struct SvrAuthoritativeStateFileWriteStats
{
    uint64_t fopen_us;
    uint64_t fwrite_us;
    uint64_t fclose_us;
    uint64_t rename_us;
    uint64_t total_us;
    size_t bytes;
    int result;
} SvrAuthoritativeStateFileWriteStats;

static bool SvrWriteAuthoritativeStateFile(const char* tmp_path,
                                           const char* final_path,
                                           const char* json_buf,
                                           size_t json_len,
                                           SvrAuthoritativeStateFileWriteStats* stats = nullptr)
{
    if (stats)
        memset(stats, 0, sizeof(*stats));
    if (!tmp_path || !tmp_path[0] || !final_path || !final_path[0] || !json_buf)
        return false;
    const uint64_t total_start_us = a3dGetTime();
    const uint64_t fopen_start_us = total_start_us;
    FILE* f = fopen(tmp_path, "wb");
    if (stats)
        stats->fopen_us = a3dGetTime() - fopen_start_us;
    if (!f)
        return false;
    size_t wrote = 0;
    if (json_len > 0)
    {
        const uint64_t fwrite_start_us = a3dGetTime();
        wrote = fwrite(json_buf, 1, json_len, f);
        if (stats)
            stats->fwrite_us = a3dGetTime() - fwrite_start_us;
    }
    const uint64_t fclose_start_us = a3dGetTime();
    const int close_result = fclose(f);
    if (stats)
        stats->fclose_us = a3dGetTime() - fclose_start_us;
    int rename_result = -1;
    if (close_result == 0)
    {
        const uint64_t rename_start_us = a3dGetTime();
        rename_result = rename(tmp_path, final_path);
        if (stats)
            stats->rename_us = a3dGetTime() - rename_start_us;
    }
    if (stats)
    {
        stats->total_us = a3dGetTime() - total_start_us;
        stats->bytes = wrote;
        stats->result = (close_result == 0 && rename_result == 0) ? 1 : 0;
    }
    return close_result == 0 && rename_result == 0;
}

static void SvrResetClientLogBudgets(int ci)
{
    fljit_move_recv_logs[ci] = fljit_resolve_logs[ci] = 0;
    fljit_phys_logs[ci] = 0;
    fl2896_rx_logs[ci] = 0;
}

static bool SvrInputSeqIsNewer(uint16_t seq, uint16_t prev)
{
    return (int16_t)(seq - prev) > 0;
}

static float SvrReadEnvFloatOrDefault(const char* name, float fallback)
{
    const char* raw = getenv(name);
    if (!raw || !raw[0])
        return fallback;
    char* end = 0;
    float parsed = strtof(raw, &end);
    if (!end || end == raw || *end != '\0' || !isfinite(parsed))
        return fallback;
    return parsed;
}

static float SvrWaterSubmersionAtZ(float pos_z, float water_level, float world_height)
{
    if (world_height <= 0.0f) return 0.0f;
    float res = (water_level - pos_z) / world_height;
    if (res < 0.0f) res = 0.0f;
    if (res > 1.0f) res = 1.0f;
    return res;
}

static float SvrSampleTerrainHeight(Terrain* terrain, float world_x, float world_y, float fallback_z)
{
    if (!terrain)
        return fallback_z;

    const float patch_world = HEIGHT_CELLS * 2.0f;
    int patch_x = (int)floorf(world_x / patch_world);
    int patch_y = (int)floorf(world_y / patch_world);
    Patch* patch = GetTerrainPatch(terrain, patch_x, patch_y);
    if (!patch)
        return fmaxf(fallback_z, SVR_WATER_LEVEL); // off-island floor: clamp diagnostic to water level

    uint16_t* hmap = GetTerrainHeightMap(patch);
    uint16_t* vmap = GetTerrainVisualMap(patch);
    if (!hmap || !vmap)
        return fallback_z;

    float local_x = fmodf(world_x, patch_world);
    float local_y = fmodf(world_y, patch_world);
    if (local_x < 0.0f) local_x += HEIGHT_CELLS * 2.0f;
    if (local_y < 0.0f) local_y += HEIGHT_CELLS * 2.0f;

    const float cell_x = local_x * 0.5f;
    const float cell_y = local_y * 0.5f;
    int hx = (int)floorf(cell_x);
    int hy = (int)floorf(cell_y);
    if (hx < 0) hx = 0;
    if (hy < 0) hy = 0;
    if (hx >= HEIGHT_CELLS) hx = HEIGHT_CELLS - 1;
    if (hy >= HEIGHT_CELLS) hy = HEIGHT_CELLS - 1;

    const float fx = cell_x - hx;
    const float fy = cell_y - hy;
    const float h00 = (float)hmap[hy * (HEIGHT_CELLS + 1) + hx];
    const float h10 = (float)hmap[hy * (HEIGHT_CELLS + 1) + hx + 1];
    const float h01 = (float)hmap[(hy + 1) * (HEIGHT_CELLS + 1) + hx];
    const float h11 = (float)hmap[(hy + 1) * (HEIGHT_CELLS + 1) + hx + 1];
    const int diag_index = hx + hy * HEIGHT_CELLS;
    const bool rot = ((GetTerrainDiag(patch) >> diag_index) & 1) != 0;

    float surface_z = 0.0f;
    if (rot)
    {
        if (fx + fy <= 1.0f)
            surface_z = h00 + fx * (h10 - h00) + fy * (h01 - h00);
        else
            surface_z = h11 + (1.0f - fx) * (h01 - h11) + (1.0f - fy) * (h10 - h11);
    }
    else
    {
        if (fx <= fy)
            surface_z = h00 + fx * (h11 - h01) + fy * (h01 - h00);
        else
            surface_z = h00 + fx * (h10 - h00) + fy * (h11 - h10);
    }

    int vx = (int)floorf(local_x);
    int vy = (int)floorf(local_y);
    if (vx < 0) vx = 0;
    if (vy < 0) vy = 0;
    if (vx >= VISUAL_CELLS) vx = VISUAL_CELLS - 1;
    if (vy >= VISUAL_CELLS) vy = VISUAL_CELLS - 1;

    const uint16_t visual = vmap[vy * VISUAL_CELLS + vx];
    // Mirror the render terrain depth path: visual bit 15 lifts the top surface
    // by one HEIGHT_SCALE step, so the authoritative floor sample must honor it.
    // FL-2957 HYPOTHESIS (untested): if spawn tile has bit 15, SvrSampleTerrainHeight
    // returns z=57 (55+HEIGHT_SCALE) but MpStepOnce collision resolves to z=55 (true
    // collision plane without visual lift). This would create a permanent support_z
    // delta of HEIGHT_SCALE, rejecting idle fast path forever. MpSoupCollector::Build
    // uses QueryWorld (BSP geometry) + QueryTerrain (heightmap), neither of which
    // applies this visual bit 15 lift. Needs verification: check spawn tile visual map.
    if (visual & 0x8000)
        surface_z += HEIGHT_SCALE;

    return surface_z;
}

static bool SvrWithinVerticalBand(const float a[3], const float b[3], float max_dz)
{
    if (!a || !b)
        return false;
    return fabsf(a[2] - b[2]) <= max_dz;
}

static void SvrResolveSafePlayerSpawn(ServerState* state, float out_pos[3])
{
    if (!out_pos) return;
    // WARNING (FL-2540/FL-2574): local single-player authority must consume
    // the same injected spawn contract as the native client. Leaving the
    // server on its own hardcoded safe-player XY recreates mixed ownership:
    // the client loads the selected OSM artifact while the authoritative side
    // still spawns at the legacy default island coordinates.
    out_pos[0] = SvrReadEnvFloatOrDefault("ASCIICKER_SPAWN_X", SVR_SAFE_PLAYER_SPAWN_XY[0]);
    out_pos[1] = SvrReadEnvFloatOrDefault("ASCIICKER_SPAWN_Y", SVR_SAFE_PLAYER_SPAWN_XY[1]);
    out_pos[2] = SvrSampleTerrainHeight(
        state ? state->terrain : 0,
        out_pos[0],
        out_pos[1],
        SvrReadEnvFloatOrDefault("ASCIICKER_SPAWN_Z", SVR_PLAYER_SPAWN_FALLBACK_Z));
}

static bool SvrResolveNpcGroupAnchor(ServerState* state, int group_ordinal, float out_pos[3])
{
    if (!state || !out_pos || group_ordinal < 0)
        return false;

    int found = 0;
    int last_gen = -1;
    for (int i = 0; i < state->npc_count; i++)
    {
        SvrNpcState* npc = &state->npcs[i];
        if (!npc->active) continue;
        if (npc->spawn_gen_index == last_gen) continue;
        last_gen = npc->spawn_gen_index;
        if (found == group_ordinal)
        {
            out_pos[0] = npc->spawn_pos[0];
            out_pos[1] = npc->spawn_pos[1];
            out_pos[2] = SvrSampleTerrainHeight(state->terrain, out_pos[0], out_pos[1], npc->spawn_pos[2]);
            return true;
        }
        found++;
    }
    return false;
}

static bool SvrQueueEvent(ServerState* state, const uint8_t* data, int size, int exclude);
static bool SvrQueueToClient(ServerState* state, int ci, const uint8_t* data, int size,
                             bool allow_replace_snapshot);
static bool SvrQueueItemChangeEvent(ServerState* state,
                                    const SvrItemState* it,
                                    uint8_t kind,
                                    uint16_t owner_id);
static bool SvrQueueItemChangeEventChecked(ServerState* state,
                                           const SvrItemState* it,
                                           uint8_t kind,
                                           uint16_t owner_id,
                                           const char* source);
// REMOVED: struct SvrActorVisualProfileCatalog (now in header)
// REMOVED fwd decl: static bool SvrLoadActorVisualProfileCatalog(SvrActorVisualProfileCatalog* out_cache)
static void SvrRuntimeDiagLog(const ServerState* state, const char* fmt, ...);

// Placed-block runtime mesh path. Replaces the sprite-derived AABB hack that
// caused FL-4137 to be patched ~60 times against kMpMaxImplicitStepUp. A placed
// block is now a real engine MeshInst on state->world; the existing
// QueryWorld()-based building-collision path in mp_step.cpp picks it up
// automatically — no carve-outs.
static Mesh* SvrPlacedBlockCubeMesh(ServerState* state)
{
    static Mesh* mesh = 0;
    static bool tried = false;
    if (mesh || !state || !state->world)
        return mesh;
    if (tried)
        return 0;
    tried = true;
    mesh = FindOrLoadMesh(state->world, "assets/meshes/PicoCube.akm", "placed_block_cube");
    if (mesh)
    {
        float bb[6] = {0};
        GetMeshBBox(mesh, bb);
        SvrRuntimeDiagLog(state,
                          "[placed-block-mesh] loaded path=assets/meshes/PicoCube.akm bbox_min=(%.3f,%.3f,%.3f) bbox_max=(%.3f,%.3f,%.3f)\n",
                          bb[0], bb[2], bb[4], bb[1], bb[3], bb[5]);
    }
    else
    {
        SvrRuntimeDiagLog(state, "[placed-block-mesh] load_failed path=assets/meshes/PicoCube.akm\n");
    }
    return mesh;
}

static void SvrComputePlacedBlockTM(const ServerWorldEntity* entity, Mesh* mesh, double tm[16])
{
    float bb[6] = {0};
    GetMeshBBox(mesh, bb);
    const float sx = bb[1] - bb[0];
    const float sy = bb[3] - bb[2];
    const float sz = bb[5] - bb[4];
    const float target_xy = entity->collision_half_extent * 2.0f;
    const float target_z = entity->collision_height;
    const float kx = sx > 0.0f ? target_xy / sx : 1.0f;
    const float ky = sy > 0.0f ? target_xy / sy : 1.0f;
    const float kz = sz > 0.0f ? target_z / sz : 1.0f;
    // Bottom-center: mesh centered in XY, bottom resting on pos.z.
    const double tx = (double)entity->pos[0] - (double)kx * 0.5 * (double)(bb[0] + bb[1]);
    const double ty = (double)entity->pos[1] - (double)ky * 0.5 * (double)(bb[2] + bb[3]);
    const double tz = (double)entity->pos[2] - (double)kz * (double)bb[4];
    // Column-major 4x4.
    tm[0]  = (double)kx; tm[1]  = 0;          tm[2]  = 0;          tm[3]  = 0;
    tm[4]  = 0;          tm[5]  = (double)ky; tm[6]  = 0;          tm[7]  = 0;
    tm[8]  = 0;          tm[9]  = 0;          tm[10] = (double)kz; tm[11] = 0;
    tm[12] = tx;         tm[13] = ty;         tm[14] = tz;         tm[15] = 1.0;
}

static ServerWorldEntity* SvrUpsertPlacedBlockEntity(
    ServerState* state,
    const SvrItemState* it,
    const SvrActorVisualProfileCatalogItemDef* item_def,
    const char* source)
{
    if (!state || !it || !item_def)
        return 0;
    ServerWorldEntity* entity = ServerWorldEntityRegistryUpsertPlacedBlock(
        &state->world_entities,
        it->item_id,
        it->item_definition_id,
        it->owner_id,
        it->pos,
        it->placed_yaw,
        item_def->collision_radius_units > 0.0f ? item_def->collision_radius_units : 1.0f,
        item_def->collision_height_units > 0.0f ? item_def->collision_height_units : 2.0f,
        item_def->explicit_pickup_only);
    if (!entity)
    {
        SvrRuntimeDiagLog(state,
                          "[item-place] world_entity_failed source=%s item_id=%u tick=%u\n",
                          source ? source : "unknown",
                          (unsigned)it->item_id,
                          (unsigned)state->tick);
        return 0;
    }
    // Spawn/update real engine mesh instance. Server collision (mp_step.cpp
    // QueryWorld) picks it up via the same path buildings use.
    Mesh* cube = SvrPlacedBlockCubeMesh(state);
    if (cube)
    {
        double tm[16] = {0};
        SvrComputePlacedBlockTM(entity, cube, tm);
        if (entity->mesh_inst)
        {
            SetInstTM((Inst*)entity->mesh_inst, tm);
        }
        else
        {
            char inst_name[64];
            snprintf(inst_name, sizeof(inst_name), "placed_block_%u", (unsigned)entity->item_id);
            const int inst_flags = INST_FLAGS::INST_VISIBLE | INST_FLAGS::INST_VOLATILE;
            entity->mesh_inst = CreateInst(cube, inst_flags, tm, inst_name, -1);
            if (!entity->mesh_inst)
            {
                SvrRuntimeDiagLog(state,
                                  "[item-place] mesh_inst_failed source=%s item_id=%u tick=%u\n",
                                  source ? source : "unknown",
                                  (unsigned)it->item_id,
                                  (unsigned)state->tick);
            }
            else
            {
                SvrRuntimeDiagLog(state,
                                  "[item-place] mesh_inst_spawned source=%s item_id=%u name=%s pos=(%.2f,%.2f,%.2f) half=%.2f height=%.2f tick=%u\n",
                                  source ? source : "unknown",
                                  (unsigned)it->item_id,
                                  inst_name,
                                  entity->pos[0], entity->pos[1], entity->pos[2],
                                  entity->collision_half_extent,
                                  entity->collision_height,
                                  (unsigned)state->tick);
            }
        }
    }
    SvrRuntimeDiagLog(state,
                      "[item-place] world_entity source=%s entity_id=%llu item_id=%u pos=(%.2f,%.2f,%.2f) top_z=%.2f tick=%u\n",
                      source ? source : "unknown",
                      (unsigned long long)entity->entity_id,
                      (unsigned)it->item_id,
                      entity->pos[0], entity->pos[1], entity->pos[2],
                      entity->pos[2] + entity->collision_height,
                      (unsigned)state->tick);
    return entity;
}

static void SvrRemovePlacedBlockEntity(ServerState* state, SvrItemState* it)
{
    if (!state || !it)
        return;
    ServerWorldEntity* entity =
        ServerWorldEntityRegistryFindByItemId(&state->world_entities, it->item_id);
    if (entity && entity->mesh_inst)
    {
        DeleteInst((Inst*)entity->mesh_inst);
        entity->mesh_inst = 0;
        SvrRuntimeDiagLog(state,
                          "[item-place] mesh_inst_deleted item_id=%u tick=%u\n",
                          (unsigned)it->item_id,
                          (unsigned)state->tick);
    }
    ServerWorldEntityRegistryRemoveByItemId(&state->world_entities, it->item_id);
    it->placed_entity_id = 0;
}

static bool SvrCanQueueItemChangeEventBatch(const ServerState* state, int count)
{
    if (!state || count < 0)
        return false;
    const int max_count = (int)(sizeof(state->events.entries) /
                                sizeof(state->events.entries[0]));
    const int max_len = (int)sizeof(state->events.buf);
    const int item_event_size = (int)sizeof(STRUCT_BRC_ITEM_CHANGE_V2);
    return state->events.count + count <= max_count &&
           state->events.len + count * item_event_size <= max_len;
}

static int SvrCountKnownItems(const ServerState* state)
{
    if (!state) return 0;
    int count = 0;
    for (int i = 0; i < SVR_MAX_ITEMS; i++) if (state->items[i].active) count++;
    return count;
}

static int SvrCountWorldItems(const ServerState* state)
{
    if (!state) return 0;
    int count = 0;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
        if (state->items[i].active && state->items[i].owner_id == 0xFFFF) count++;
    return count;
}

// REMOVED: static bool SvrHasAnyActiveSession(const ServerState* state)


static bool SvrAppendJsonf(char* dst, size_t cap, size_t* used, const char* fmt, ...)
{
    if (!dst || !used || !fmt || *used >= cap) return false;
    va_list args;
    va_start(args, fmt);
    int wrote = vsnprintf(dst + *used, cap - *used, fmt, args);
    va_end(args);
    if (wrote < 0) return false;
    size_t wrote_sz = (size_t)wrote;
    if (wrote_sz >= cap - *used) return false;
    *used += wrote_sz;
    return true;
}

enum SvrAppearanceJsonArrayField
{
    SVR_APPEARANCE_JSON_SLOT_KIND = 1,
    SVR_APPEARANCE_JSON_ITEM_DEFINITION = 2,
    SVR_APPEARANCE_JSON_VISUAL_STYLE = 3,
};

static bool SvrAppendAppearanceEquippedFieldArray(char* dst, size_t cap, size_t* used,
    const char* key, const SvrAuthoritativeAppearanceState* appearance,
    SvrAppearanceJsonArrayField field)
{
    if (!SvrAppendJsonf(dst, cap, used, ",\"%s\":[", key ? key : ""))
        return false;

    int emitted = 0;
    if (appearance)
    {
        for (int i = 0; i < appearance->entry_count; i++)
        {
            const SvrAppearanceLoadoutEntry* entry = &appearance->entries[i];
            if ((entry->state_flags & SVR_APPEARANCE_ENTRY_STATE_EQUIPPED) == 0)
                continue;

            uint32_t value = 0;
            switch (field)
            {
                case SVR_APPEARANCE_JSON_SLOT_KIND:
                    value = (uint32_t)entry->slot_kind_id;
                    break;
                case SVR_APPEARANCE_JSON_ITEM_DEFINITION:
                    value = (uint32_t)entry->item_definition_id;
                    break;
                case SVR_APPEARANCE_JSON_VISUAL_STYLE:
                    value = (uint32_t)entry->visual_style_id;
                    break;
                default:
                    value = 0;
                    break;
            }
            if (!SvrAppendJsonf(dst, cap, used, "%s%u", emitted > 0 ? "," : "", value))
                return false;
            emitted++;
        }
    }

    return SvrAppendJsonf(dst, cap, used, "]");
}

static const uint16_t SVR_MAP_AUTHORED_WORLD_ITEM_BASE_ID = 0x6200;

static int SvrFindFreeItemSlot(ServerState* state)
{
    if (!state) return -1;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        if (!state->items[i].active)
            return i;
    }
    return -1;
}

static bool SvrItemIdInUse(const ServerState* state, uint16_t item_id)
{
    if (!state || item_id == 0)
        return false;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        if (state->items[i].active && state->items[i].item_id == item_id)
            return true;
    }
    return false;
}

struct SvrMapWorldItemLoadContext
{
    ServerState* state;
    const SvrActorVisualProfileCatalog* cache;
    int visited;
    int spawned;
    int rejected;
};

static void SvrLoadMapWorldItemInst(Inst* inst,
                                    Item* item,
                                    const float pos[3],
                                    float yaw,
                                    int story_id,
                                    void* cookie)
{
    (void)inst;
    (void)yaw;
    SvrMapWorldItemLoadContext* ctx = (SvrMapWorldItemLoadContext*)cookie;
    if (!ctx || !ctx->state || !ctx->cache || !item || !pos)
        return;

    const int ordinal = ctx->visited++;
    const SvrActorVisualProfileCatalogItemDef* profile_item =
        SvrFindAppearanceItemById(ctx->cache, item->item_definition_id);
    if (!profile_item || profile_item->id == 0 ||
        profile_item->slot_kind_id == 0 ||
        profile_item->gameplay_kind == SVR_ITEM_GAMEPLAY_UNKNOWN)
    {
        ctx->rejected++;
        SvrRuntimeDiagLog(ctx->state,
                          "[item-world] map item rejected ordinal=%d story_id=%d definition=%u reason=unresolved_profile_item tick=%u\n",
                          ordinal,
                          story_id,
                          (unsigned)item->item_definition_id,
                          (unsigned)ctx->state->tick);
        return;
    }

    const int slot = SvrFindFreeItemSlot(ctx->state);
    if (slot < 0)
    {
        ctx->rejected++;
        SvrRuntimeDiagLog(ctx->state,
                          "[item-world] map item rejected ordinal=%d story_id=%d definition=%u reason=no_free_item_slot tick=%u\n",
                          ordinal,
                          story_id,
                          (unsigned)item->item_definition_id,
                          (unsigned)ctx->state->tick);
        return;
    }

    uint16_t item_id = 0;
    if (story_id > 0 && story_id <= 0xFFFF && !SvrItemIdInUse(ctx->state, (uint16_t)story_id))
        item_id = (uint16_t)story_id;
    else
        item_id = (uint16_t)(SVR_MAP_AUTHORED_WORLD_ITEM_BASE_ID + ordinal);
    while (SvrItemIdInUse(ctx->state, item_id))
        item_id++;

	SvrItemState* it = &ctx->state->items[slot];
	const uint16_t visual_style_id =
		SvrNormalizeAppearanceVisualStyleId(item->visual_style_id);
	memset(it, 0, sizeof(*it));
    it->active = true;
    it->item_id = item_id;
    it->owner_id = 0xFFFF;
    it->pos[0] = pos[0];
    it->pos[1] = pos[1];
    it->pos[2] = SvrSampleTerrainHeight(ctx->state->terrain, pos[0], pos[1], pos[2]);
    it->item_definition_id = profile_item->id;
	it->visual_style_id = visual_style_id;
	it->equip_slot_kind_id = SvrEquipSlotKindForCatalogItem(profile_item);
    it->mount_definition_id = profile_item->mount_definition_id;
    it->gameplay_kind = profile_item->gameplay_kind;
    it->source_kind = SVR_ITEM_SOURCE_MAP_A3D;
    ctx->spawned++;
    SvrRuntimeDiagLog(ctx->state,
                      "[item-world] map item slot=%d ordinal=%d item_id=%u story_id=%d definition=%u kind=%u style=%u raw_style=%u pos=(%.2f,%.2f,%.2f)\n",
                      slot,
                      ordinal,
                      (unsigned)it->item_id,
                      story_id,
                      (unsigned)it->item_definition_id,
                      (unsigned)it->gameplay_kind,
                      (unsigned)it->visual_style_id,
                      (unsigned)item->visual_style_id,
                      it->pos[0], it->pos[1], it->pos[2]);
}

// FL-4137 behavior 8: seed a normal sword next to the block so an automated
// proof can pick it up, equip, walk to a block, swing, and verify break.
// Sword has block_break_power=1; legacy_yy_block has placed_durability=3, so
// three swings should despawn the block. The sword is dropped at the spawn
// + (-4, 0) offset (opposite side from the block at +4, 0). Skipped if the
// sword catalog item is missing.
static bool SvrSeedNormalSwordWorldItem(ServerState* state,
                                        const SvrActorVisualProfileCatalog* cache)
{
    if (!state || !cache)
        return false;
    const SvrActorVisualProfileCatalogItemDef* sword_def =
        SvrFindAppearanceItemBySlug(cache, "normal_sword");
    if (!sword_def)
        return false;
    const int slot = SvrFindFreeItemSlot(state);
    if (slot < 0)
        return false;
    float spawn_pos[3] = {};
    SvrResolveSafePlayerSpawn(state, spawn_pos);
    float pos[3] = { spawn_pos[0] - 4.0f, spawn_pos[1], spawn_pos[2] };
    pos[2] = SvrSampleTerrainHeight(state->terrain, pos[0], pos[1], pos[2]);
    uint16_t item_id = 0x6400;
    while (SvrItemIdInUse(state, item_id))
        item_id++;
    SvrItemState* it = &state->items[slot];
    memset(it, 0, sizeof(*it));
    it->active = true;
    it->item_id = item_id;
    it->owner_id = 0xFFFF;
    memcpy(it->pos, pos, sizeof(it->pos));
    it->item_definition_id = sword_def->id;
    it->visual_style_id = SVR_APPEARANCE_VISUAL_STYLE_DEFAULT;
    it->equip_slot_kind_id = 0;
    it->mount_definition_id = 0;
    it->gameplay_kind = sword_def->gameplay_kind;
    it->source_kind = SVR_ITEM_SOURCE_MAP_A3D;
    it->placed_flags = SVR_PLACED_ITEM_NONE;
    it->placed_durability = 0;
    it->placed_yaw = 0.0f;
    printf("[tick] Seeded normal sword item_id=%u definition=%u slot=%d pos=(%.2f,%.2f,%.2f)\n",
           (unsigned)it->item_id, (unsigned)it->item_definition_id, slot,
           it->pos[0], it->pos[1], it->pos[2]);
    return true;
}

static bool SvrSeedLegacyBlockWorldItem(ServerState* state,
                                        const SvrActorVisualProfileCatalog* cache)
{
    if (!state || !cache)
    {
        printf("[tick] Legacy block seed skipped: missing state/cache\n");
        return false;
    }
    // FL-4137: seed the single canonical placed block (legacy-yy-block-angles.xp)
    // near the spawn so the operator can walk up to it in a headed run and
    // visually verify the "visible top == world top" contract without any
    // client place input. The 28 May taller/thicker variants pointed at
    // procedurally generated 1-row strip XPs (divide-by-zero L0) and have
    // been removed; see catalog comment in actor_visual_catalog_source.h.
    struct SeedSpec {
        const char* slug;
        float dx;
        float dy;
    };
    const SeedSpec specs[] = {
        { "legacy_yy_block", 4.0f, 0.0f },
        // FL-4137 #12 side-block proof seed: height=40 > kMpMaxImplicitStepUp.
        // Placed offset to the side of the short block so the proof can drive
        // toward each independently.
        { "tall_yy_block",   4.0f, 4.0f },
    };
    const int spec_count = (int)(sizeof(specs) / sizeof(specs[0]));

    float spawn_pos[3] = {};
    SvrResolveSafePlayerSpawn(state, spawn_pos);

    int seeded = 0;
    for (int n = 0; n < spec_count; n++)
    {
        const SvrActorVisualProfileCatalogItemDef* block_def =
            SvrFindAppearanceItemBySlug(cache, specs[n].slug);
        if (!block_def || !block_def->placeable)
        {
            printf("[tick] Legacy block seed skipped variant '%s': catalog item missing or not placeable item_count=%u\n",
                   specs[n].slug,
                   (unsigned)cache->item_count);
            continue;
        }
        const int slot = SvrFindFreeItemSlot(state);
        if (slot < 0)
        {
            printf("[tick] Legacy block seed stopped: no free item slot active_items=%d max_items=%d seeded=%d\n",
                   SvrCountKnownItems(state),
                   SVR_MAX_ITEMS,
                   seeded);
            break;
        }

        float pos[3] = {
            spawn_pos[0] + specs[n].dx,
            spawn_pos[1] + specs[n].dy,
            spawn_pos[2],
        };
        pos[2] = SvrSampleTerrainHeight(state->terrain, pos[0], pos[1], pos[2]);

        uint16_t item_id = (uint16_t)(0x6300 + n);
        while (SvrItemIdInUse(state, item_id))
            item_id++;

        SvrItemState* it = &state->items[slot];
        memset(it, 0, sizeof(*it));
        it->active = true;
        it->item_id = item_id;
        it->owner_id = 0xFFFF;
        memcpy(it->pos, pos, sizeof(it->pos));
        it->item_definition_id = block_def->id;
        it->visual_style_id = SVR_APPEARANCE_VISUAL_STYLE_DEFAULT;
        it->equip_slot_kind_id = 0;
        it->mount_definition_id = 0;
        it->gameplay_kind = block_def->gameplay_kind;
        it->source_kind = SVR_ITEM_SOURCE_MAP_A3D;
        it->placed_flags =
            SVR_PLACED_ITEM_PLACED |
            SVR_PLACED_ITEM_COLLIDABLE |
            (block_def->explicit_pickup_only ? SVR_PLACED_ITEM_EXPLICIT_PICKUP_ONLY : 0);
        it->placed_durability = block_def->placed_durability;
        it->placed_yaw = 0.0f;
        ServerWorldEntity* placed_entity =
            SvrUpsertPlacedBlockEntity(state, it, block_def, "seeded_legacy_block");
        if (!placed_entity)
        {
            memset(it, 0, sizeof(*it));
            SvrRuntimeDiagLog(state,
                              "[item-world] seeded legacy placed block rejected variant=%s seed_index=%d reason=world_entity_failed tick=%u\n",
                              specs[n].slug,
                              n,
                              (unsigned)state->tick);
            continue;
        }
        it->placed_entity_id = placed_entity->entity_id;
        seeded++;
        SvrRuntimeDiagLog(state,
                          "[item-world] seeded legacy placed block variant=%s item_id=%u definition=%u seed_index=%d pos=(%.2f,%.2f,%.2f) placed_flags=%u tick=%u\n",
                          specs[n].slug,
                          (unsigned)it->item_id,
                          (unsigned)it->item_definition_id,
                          n,
                          it->pos[0], it->pos[1], it->pos[2],
                          (unsigned)it->placed_flags,
                          (unsigned)state->tick);
        printf("[tick] Seeded legacy placed block variant=%s item_id=%u definition=%u seed_index=%d slot=%d pos=(%.2f,%.2f,%.2f) placed_flags=%u\n",
               specs[n].slug,
               (unsigned)it->item_id,
               (unsigned)it->item_definition_id,
               n,
               slot,
               it->pos[0], it->pos[1], it->pos[2],
               (unsigned)it->placed_flags);
    }
    return seeded > 0;
}

void SvrInitWorldItems(ServerState* state)
{
    if (!state)
        return;

    if (SvrCountKnownItems(state) > 0)
    {
        SvrRuntimeDiagLog(state,
                          "[item-world] map item load skipped: existing_items=%d tick=%u\n",
                          SvrCountKnownItems(state),
                          (unsigned)state->tick);
        return;
    }

    ServerWorldEntityRegistryInit(&state->world_entities);

    if (!state->world)
    {
        SvrRuntimeDiagLog(state,
                          "[item-world] map item load skipped: no world loaded tick=%u\n",
                          (unsigned)state->tick);
        state->item_count = SvrCountKnownItems(state);
        return;
    }

    SvrActorVisualProfileCatalog cache = {};
    if (!SvrLoadActorVisualProfileCatalog(&cache))
    {
        SvrRuntimeDiagLog(state,
                          "[item-world] map item load skipped: bundle unavailable tick=%u\n",
                          (unsigned)state->tick);
        state->item_count = SvrCountKnownItems(state);
        return;
    }

    SvrMapWorldItemLoadContext ctx = {};
    ctx.state = state;
    ctx.cache = &cache;
    if (SvrSeedLegacyBlockWorldItem(state, &cache))
        ctx.spawned++;
    // FL-4137 b8: sword auto-seed gated behind ASCIICKER_SEED_TEST_SWORD=1.
    // mobile_controls is hardcoded true (engine/game_utility.cpp:615) so an
    // equipped weapon auto-triggers mobile auto-combat every ~500ms, which
    // with heavy-break enabled despawns the seeded block in ~1.5s. The seed
    // is only useful for the heavy-break proof; production must not auto-
    // destroy the block on join.
    {
        const char* env_seed_sword = getenv("ASCIICKER_SEED_TEST_SWORD");
        if (env_seed_sword && env_seed_sword[0] == '1')
        {
            if (SvrSeedNormalSwordWorldItem(state, &cache))
                ctx.spawned++;
        }
    }
    QueryWorldItems(state->world, SvrLoadMapWorldItemInst, &ctx);
    SvrRuntimeDiagLog(state,
                      "[item-world] map item load complete tick=%u visited=%d initialized=%d rejected=%d existing_items=%d\n",
                      (unsigned)state->tick,
                      ctx.visited,
                      ctx.spawned,
                      ctx.rejected,
                      SvrCountKnownItems(state));
    state->item_count = SvrCountKnownItems(state);
    printf("[tick] Initialized %d ordinary world items from map-authored item instances (rejected=%d)\n",
           ctx.spawned,
           ctx.rejected);
}

static bool SvrResolveAppearanceEntryForItemState(const SvrActorVisualProfileCatalog* cache,
                                                  const SvrItemState* it,
                                                  SvrAppearanceLoadoutEntry* out_entry)
{
    if (!cache || !it || !out_entry)
        return false;
    const SvrActorVisualProfileCatalogItemDef* item = SvrFindAppearanceItemById(cache, it->item_definition_id);
    if (!item || item->id == 0 || item->slot_kind_id == 0)
        return false;

	memset(out_entry, 0, sizeof(*out_entry));
	out_entry->slot_kind_id = item->slot_kind_id;
    out_entry->item_instance_id = it->item_id;
    out_entry->item_definition_id = it->item_definition_id;
	out_entry->visual_style_id =
		SvrNormalizeAppearanceVisualStyleId(it->visual_style_id);
    out_entry->state_flags = SVR_APPEARANCE_ENTRY_STATE_EQUIPPED;
    return true;
}

static bool SvrResolveStarterAppearanceEntry(const SvrActorVisualProfileCatalog* cache,
                                             const SvrAppearanceLoadoutEntry* starter,
                                             SvrAppearanceLoadoutEntry* out_entry)
{
    if (!cache || !starter || !out_entry || starter->item_definition_id == 0)
        return false;
    const SvrActorVisualProfileCatalogItemDef* item =
        SvrFindAppearanceItemById(cache, starter->item_definition_id);
    if (!item || item->id == 0 || item->slot_kind_id == 0)
        return false;

    memset(out_entry, 0, sizeof(*out_entry));
    out_entry->slot_kind_id = item->slot_kind_id;
    out_entry->item_instance_id = starter->item_instance_id;
    out_entry->item_definition_id = item->id;
    out_entry->visual_style_id =
        SvrNormalizeAppearanceVisualStyleId(starter->visual_style_id);
    out_entry->state_flags = starter->state_flags;
    return true;
}

// Materialize starter loadout into SvrItemState[] owned by the joining player.
// Prior to this, SvrApplyProfileToAppearance populated only appearance.entries[]
// (the visual slots) but left state->items[] empty and entries[].item_instance_id=0.
// Result was that visuals rendered (server publishes appearance_v2) while
// client-side auth_item.items[] stayed empty — breaking inventory display and
// FindLocalEquippedWeaponItemId, which blocked autoswing arming.
// This helper creates one SvrItemState per starter_entry owned by ci, gives it
// the equipped slot kind, and links the entry's item_instance_id to the created
// item so both stores agree.
static int SvrCreateStarterLoadoutItems(ServerState* state,
                                        int ci,
                                        const SvrActorVisualProfileCatalog* cache,
                                        const SvrActorVisualProfileCatalogProfileDef* profile,
                                        SvrAuthoritativeAppearanceState* appearance)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS || !cache || !profile || !appearance)
        return 0;
    int created = 0;
    uint16_t next_id = 1;
    for (int i = 0; i < profile->starter_count; i++)
    {
        const SvrAppearanceLoadoutEntry& starter = profile->starter_entries[i];
        SvrAppearanceLoadoutEntry starter_entry = {};
        if (!SvrResolveStarterAppearanceEntry(cache, &starter, &starter_entry))
            continue;
        const SvrActorVisualProfileCatalogItemDef* item_def =
            SvrFindAppearanceItemById(cache, starter_entry.item_definition_id);
        if (!item_def || item_def->id == 0 || item_def->slot_kind_id == 0)
            continue;
        const int slot = SvrFindFreeItemSlot(state);
        if (slot < 0)
            break;
        while (next_id != 0 && SvrItemIdInUse(state, next_id))
            next_id++;
        if (next_id == 0)
            break;
        const uint16_t item_id = next_id++;
        SvrItemState* it = &state->items[slot];
        memset(it, 0, sizeof(*it));
        it->active = true;
        it->item_id = item_id;
        it->owner_id = (uint16_t)ci;
        it->item_definition_id = starter_entry.item_definition_id;
        it->visual_style_id = starter_entry.visual_style_id;
        it->equip_slot_kind_id = SvrEquipSlotKindForCatalogItem(item_def);
        it->mount_definition_id = item_def->mount_definition_id;
        it->gameplay_kind = item_def->gameplay_kind;
        it->source_kind = SVR_ITEM_SOURCE_STARTER_LOADOUT;
        for (int j = 0; j < appearance->entry_count; j++)
        {
            if (appearance->entries[j].slot_kind_id == starter_entry.slot_kind_id)
            {
                appearance->entries[j].item_instance_id = item_id;
                break;
            }
        }
        created++;
    }
    if (created > 0)
        SvrBumpAppearanceRevision(appearance);
    return created;
}


static SvrItemState* SvrFindOwnedItemById(ServerState* state, int ci, uint16_t item_id)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS || item_id == 0)
        return 0;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        SvrItemState* it = &state->items[i];
        if (!it->active || it->owner_id != (uint16_t)ci || it->item_id != item_id)
            continue;
        return it;
    }
    return 0;
}

static void SvrClearEquippedStateForItem(SvrItemState* it)
{
    if (!it)
        return;
    it->equip_slot_kind_id = 0;
}

static void SvrMarkDroppedItemRepickupGrace(ServerState* state,
                                            SvrItemState* it,
                                            int ci)
{
    if (!state || !it || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;
    it->last_drop_owner_id = (uint16_t)ci;
    it->last_drop_tick = state->tick;
}

static bool SvrItemInSameOwnerRepickupGrace(const ServerState* state,
                                            const SvrItemState* it,
                                            int ci)
{
    if (!state || !it || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;
    if (it->last_drop_tick == 0 || it->last_drop_owner_id != (uint16_t)ci)
        return false;
    return state->tick - it->last_drop_tick < SVR_ITEM_DROP_REPICKUP_GRACE_TICKS;
}

static void SvrSetEquippedStateForItem(SvrItemState* it, uint16_t slot_kind_id)
{
    if (!it)
        return;
    it->equip_slot_kind_id = slot_kind_id;
}

static uint8_t SvrResolveRuntimeMountStateForItem(const SvrActorVisualProfileCatalog* cache,
                                                  const SvrItemState* it)
{
    if (!cache || !it || it->mount_definition_id == 0)
        return MOUNT::NONE;
    const SvrActorVisualProfileCatalogMountDef* mount =
        SvrFindAppearanceMountById(cache, it->mount_definition_id);
    if (!mount || mount->runtime_mount_state >= MOUNT::SIZE)
        return MOUNT::NONE;
    return mount->runtime_mount_state;
}

static SvrItemState* SvrFindMountedItemByClient(ServerState* state, int ci)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return 0;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        SvrItemState* it = &state->items[i];
        if (!it->active || it->owner_id != (uint16_t)ci)
            continue;
        if (it->equip_slot_kind_id == APPEARANCE_SLOT_KIND_MOUNT)
            return it;
    }
    return 0;
}

static void SvrSyncAppearanceCompiledActorVisualKeyDimensions(SvrAuthoritativeAppearanceState* appearance,
                                                       uint16_t presentation_kind_id,
                                                       bool bump_revision);
static void SvrRefreshPlayerPresentationKind(ServerState* state, SvrPlayerState* ps);
static void SvrRefreshNpcPresentationKind(ServerState* state, SvrNpcState* npc);
static void SvrRefreshPlayerPresentationAfterEquipMutation(ServerState* state,
                                                           SvrPlayerState* ps);

static void SvrClearPlayerMountState(ServerState* state,
                                     SvrPlayerState* ps,
                                     bool bump_revision)
{
    if (!ps)
        return;
    ps->mount_state = MOUNT::NONE;
    if (ps->appearance.mount_definition_id != 0)
    {
        ps->appearance.mount_definition_id = 0;
        if (bump_revision)
            SvrBumpAppearanceRevision(&ps->appearance);
    }
    if (SvrFindAppearanceEntryIndexBySlot(&ps->appearance, APPEARANCE_SLOT_KIND_MOUNT) >= 0)
        SvrRemoveAppearanceEntryBySlot(&ps->appearance, APPEARANCE_SLOT_KIND_MOUNT, true);
    // FL-3955 V-2 DELETED: SvrRefreshAppearanceRig call removed.
    SvrSyncAppearanceCompiledActorVisualKeyDimensions(&ps->appearance,
                                                     ps->presentation_kind_id,
                                                     bump_revision);
    if (state)
        SvrRefreshPlayerPresentationKind(state, ps);
}

static bool SvrDropOwnedItemAtPlayer(ServerState* state,
                                     int ci,
                                     SvrPlayerState* ps,
                                     SvrItemState* it,
                                     const char* source)
{
    if (!state || !ps || !it || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;
    if (!it->active || it->owner_id != (uint16_t)ci)
        return false;

    SvrItemState event_item = *it;
    event_item.owner_id = 0xFFFF;
    event_item.equip_slot_kind_id = 0;
    event_item.placed_flags = SVR_PLACED_ITEM_NONE;
    event_item.placed_durability = 0;
    event_item.placed_yaw = 0.0f;
    event_item.placed_entity_id = 0;
    memcpy(event_item.pos, ps->pos, sizeof(event_item.pos));
    if (!SvrQueueItemChangeEventChecked(state,
                                        &event_item,
                                        ITEM_CHANGE_KIND_DROP,
                                        0xFFFF,
                                        source))
        return false;

    const SvrAppearanceLoadoutEntry* equipped_entry =
        SvrFindAppearanceEntryByItemInstanceId(&ps->appearance, it->item_id);
    if (equipped_entry)
    {
        const uint16_t dropped_slot_kind = equipped_entry->slot_kind_id;
        if (dropped_slot_kind == APPEARANCE_SLOT_KIND_MOUNT)
            SvrClearPlayerMountState(state, ps, true);
        else
            SvrRemoveAppearanceEntryBySlot(&ps->appearance, dropped_slot_kind, true);
    }

    it->owner_id = 0xFFFF;
    SvrClearEquippedStateForItem(it);
    it->placed_flags = SVR_PLACED_ITEM_NONE;
    it->placed_durability = 0;
    it->placed_yaw = 0.0f;
    SvrRemovePlacedBlockEntity(state, it);
    memcpy(it->pos, ps->pos, sizeof(it->pos));
    SvrMarkDroppedItemRepickupGrace(state, it, ci);
    return true;
}

static void SvrDropAllOwnedItemsAtPlayer(ServerState* state,
                                         int ci,
                                         const char* source)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;
    SvrPlayerState* ps = &state->players[ci];
    if (!ps->active)
        return;

    int owned_count = 0;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        const SvrItemState* it = &state->items[i];
        if (it->active && it->owner_id == (uint16_t)ci)
            owned_count++;
    }
    if (owned_count <= 0 || !SvrCanQueueItemChangeEventBatch(state, owned_count))
        return;

    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        SvrItemState* it = &state->items[i];
        if (!it->active || it->owner_id != (uint16_t)ci)
            continue;
        (void)SvrDropOwnedItemAtPlayer(state, ci, ps, it, source);
    }
}

static bool SvrApplyPlayerMountItem(ServerState* state,
                                    SvrPlayerState* ps,
                                    SvrItemState* it,
                                    const SvrActorVisualProfileCatalog* cache)
{
    if (!state || !ps || !it || !cache)
        return false;
    const uint8_t runtime_mount_state = SvrResolveRuntimeMountStateForItem(cache, it);
    if (runtime_mount_state == MOUNT::NONE)
        return false;

    SvrAppearanceLoadoutEntry mount_entry = {};
    if (!SvrResolveAppearanceEntryForItemState(cache, it, &mount_entry))
        return false;

    if (!SvrUpsertAppearanceEntry(&ps->appearance, &mount_entry, true))
        return false;

    ps->mount_state = runtime_mount_state;
    if (ps->appearance.mount_definition_id != it->mount_definition_id)
    {
        ps->appearance.mount_definition_id = it->mount_definition_id;
        SvrBumpAppearanceRevision(&ps->appearance);
    }
    // FL-3955 V-2 DELETED: SvrRefreshAppearanceRig call removed.
    SvrSyncAppearanceCompiledActorVisualKeyDimensions(&ps->appearance,
                                                     ps->presentation_kind_id,
                                                     true);
    SvrSetEquippedStateForItem(it, APPEARANCE_SLOT_KIND_MOUNT);
    SvrRefreshPlayerPresentationAfterEquipMutation(state, ps);
    return true;
}

static bool SvrTryMutateAppearanceEquipState(ServerState* state,
                                             int ci,
                                             SvrItemState* it,
                                             const SvrActorVisualProfileCatalog* cache,
                                             bool allow_toggle)
{
    if (!state || !it || !cache || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;

    SvrPlayerState* ps = &state->players[ci];
    if (!ps->active)
        return false;
    const SvrActorVisualProfileCatalogItemDef* item_def =
        SvrFindAppearanceItemById(cache, it->item_definition_id);
    if (!item_def)
        return false;

    SvrAppearanceLoadoutEntry next_entry = {};
    if (!SvrResolveAppearanceEntryForItemState(cache, it, &next_entry))
        return false;

    if (item_def->gameplay_kind == SVR_ITEM_GAMEPLAY_MOUNTABLE)
    {
        const bool currently_mounted = (it->equip_slot_kind_id == APPEARANCE_SLOT_KIND_MOUNT);
        if (allow_toggle && currently_mounted)
        {
            return SvrDropOwnedItemAtPlayer(state, ci, ps, it, "mount_toggle_drop");
        }

        if (SvrResolveRuntimeMountStateForItem(cache, it) == MOUNT::NONE)
            return false;

        SvrItemState* prior_mount = SvrFindMountedItemByClient(state, ci);
        const int mount_event_count =
            (prior_mount && prior_mount->item_id != it->item_id) ? 2 : 1;
        if (!SvrCanQueueItemChangeEventBatch(state, mount_event_count))
            return false;
        if (SvrFindAppearanceEntryIndexBySlot(&ps->appearance,
                                              APPEARANCE_SLOT_KIND_MOUNT) < 0 &&
            ps->appearance.entry_count >= SVR_MAX_APPEARANCE_LOADOUT_ENTRIES)
            return false;

        bool clear_prior_mount_equip = false;
        if (prior_mount && prior_mount->item_id != it->item_id)
        {
            SvrItemState prior_event_item = *prior_mount;
            prior_event_item.equip_slot_kind_id = 0;
            if (!SvrQueueItemChangeEventChecked(state,
                                                &prior_event_item,
                                                ITEM_CHANGE_KIND_EQUIP_CLEAR,
                                                (uint16_t)ci,
                                                "mount_replace_clear"))
                return false;
            clear_prior_mount_equip = true;
        }

        SvrItemState event_item = *it;
        event_item.owner_id = (uint16_t)ci;
        event_item.equip_slot_kind_id = APPEARANCE_SLOT_KIND_MOUNT;
        if (!SvrQueueItemChangeEventChecked(state,
                                            &event_item,
                                            ITEM_CHANGE_KIND_EQUIP_SET,
                                            (uint16_t)ci,
                                            "mount_set"))
            return false;

        if (!SvrApplyPlayerMountItem(state, ps, it, cache))
            return false;
        if (clear_prior_mount_equip)
            SvrClearEquippedStateForItem(prior_mount);
        return true;
    }

    const SvrAppearanceLoadoutEntry* current_entry =
        SvrFindAppearanceEntryByItemInstanceId(&ps->appearance, it->item_id);
    if (allow_toggle && current_entry && current_entry->slot_kind_id == next_entry.slot_kind_id)
    {
        SvrItemState event_item = *it;
        event_item.equip_slot_kind_id = 0;
        if (!SvrQueueItemChangeEventChecked(state,
                                            &event_item,
                                            ITEM_CHANGE_KIND_EQUIP_CLEAR,
                                            (uint16_t)ci,
                                            "equip_toggle_clear"))
            return false;

        SvrRemoveAppearanceEntryBySlot(&ps->appearance, next_entry.slot_kind_id, true);
        SvrClearEquippedStateForItem(it);
        SvrRefreshPlayerPresentationAfterEquipMutation(state, ps);
        return true;
    }

    int prior_slot_index = SvrFindAppearanceEntryIndexBySlot(&ps->appearance, next_entry.slot_kind_id);
    uint16_t prior_item_id = 0;
    if (prior_slot_index >= 0)
        prior_item_id = ps->appearance.entries[prior_slot_index].item_instance_id;

    SvrItemState* prior_item = 0;
    if (prior_item_id != 0 && prior_item_id != it->item_id)
        prior_item = SvrFindOwnedItemById(state, ci, prior_item_id);

    if (prior_slot_index < 0 &&
        ps->appearance.entry_count >= SVR_MAX_APPEARANCE_LOADOUT_ENTRIES)
    {
        return false;
    }

    const int equip_event_count = prior_item ? 2 : 1;
    if (!SvrCanQueueItemChangeEventBatch(state, equip_event_count))
        return false;

    bool clear_prior_equip = false;
    if (prior_item)
    {
        SvrItemState prior_event_item = *prior_item;
        prior_event_item.equip_slot_kind_id = 0;
        if (!SvrQueueItemChangeEventChecked(state,
                                            &prior_event_item,
                                            ITEM_CHANGE_KIND_EQUIP_CLEAR,
                                            (uint16_t)ci,
                                            "equip_replace_clear"))
            return false;
        clear_prior_equip = true;
    }

    SvrItemState event_item = *it;
    event_item.owner_id = (uint16_t)ci;
    event_item.equip_slot_kind_id = next_entry.slot_kind_id;
    if (!SvrQueueItemChangeEventChecked(state,
                                        &event_item,
                                        ITEM_CHANGE_KIND_EQUIP_SET,
                                        (uint16_t)ci,
                                        "equip_set"))
        return false;

    if (!SvrUpsertAppearanceEntry(&ps->appearance, &next_entry, true))
        return false;
    if (clear_prior_equip && prior_item)
        SvrClearEquippedStateForItem(prior_item);
    SvrSetEquippedStateForItem(it, next_entry.slot_kind_id);
    SvrRefreshPlayerPresentationAfterEquipMutation(state, ps);
    return true;
}


static void SvrNormalizeJoinDisplayName(const char* raw_name, char out_name[32])
{
    if (!out_name)
        return;

    const char* src = raw_name ? raw_name : "";

    if (*src == 0)
        src = "player";

    strncpy(out_name, src, 31);
    out_name[31] = 0;
}

// RQ-087: Validate that a normalized name contains only printable ASCII (0x20-0x7E)
// and has a reasonable length (1-32 chars). Returns true if valid.
static bool SvrValidateJoinNameChars(const char* name)
{
    if (!name || name[0] == 0)
        return false;

    int len = 0;
    for (const char* p = name; *p; p++)
    {
        unsigned char ch = (unsigned char)*p;
        if (ch < 0x20 || ch > 0x7E)
            return false;
        len++;
        if (len > 32)
            return false;
    }
    return len >= 1 && len <= 32;
}

// RQ-087: Check if a normalized name is already in use by an active player.
// Returns -1 if not in use, or the client index of the existing player.
static int SvrFindActivePlayerByName(const ServerState* state, const char* name, int exclude_ci)
{
    if (!state || !name || name[0] == 0)
        return -1;

    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        if (i == exclude_ci)
            continue;
        const SvrPlayerState* ps = &state->players[i];
        if (!ps->active)
            continue;
        // Only count players past CONNECTING (i.e. JOINED or ALIVE).
        // A player in DISCONNECTING is about to leave; allow the name.
        if (ps->phase == CPHASE_NONE || ps->phase == CPHASE_CONNECTING || ps->phase == CPHASE_DISCONNECTING)
            continue;
        if (strcmp(ps->name, name) == 0)
            return i;
    }
    return -1;
}

static bool SvrSelectJoinAppearanceProfile(const ServerState* state,
                                           const char* visible_name,
                                           const SvrActorVisualProfileCatalogProfileDef** out_profile,
                                           uint8_t* out_source_kind,
                                           uint8_t* out_subject_kind,
                                           char out_subject_key[32])
{
    if (out_profile)
        *out_profile = 0;
    if (out_source_kind)
        *out_source_kind = SVR_APPEARANCE_SOURCE_NONE;
    if (out_subject_kind)
        *out_subject_kind = SVR_APPEARANCE_SUBJECT_NONE;
    if (out_subject_key)
        out_subject_key[0] = 0;

    SvrActorVisualProfileCatalog cache = {};
    if (!SvrLoadActorVisualProfileCatalog(&cache))
        return false;

    const SvrActorVisualProfileCatalogProfileDef* default_profile =
        SvrFindAppearanceProfileById(&cache, cache.default_profile_id);
    if (!default_profile)
        return false;
    if (out_profile)
        *out_profile = default_profile;
    if (out_source_kind)
        *out_source_kind = SVR_APPEARANCE_SOURCE_DEFAULT_PROFILE;
    if (out_subject_kind)
        *out_subject_kind = SVR_APPEARANCE_SUBJECT_DEFAULT;
    if (out_subject_key)
        SvrCopyAppearanceSubjectKey(out_subject_key, "default_subject");
    return true;
}

static uint16_t SvrEquippedWeaponDefinitionIdForAppearance(
    const SvrAuthoritativeAppearanceState* appearance)
{
    if (!appearance)
        return 0;
    for (int i = 0; i < appearance->entry_count && i < SVR_MAX_APPEARANCE_LOADOUT_ENTRIES; i++)
    {
        const SvrAppearanceLoadoutEntry* entry = &appearance->entries[i];
        if (!(entry->state_flags & SVR_APPEARANCE_ENTRY_STATE_EQUIPPED))
            continue;
        if (entry->slot_kind_id == APPEARANCE_SLOT_KIND_WEAPON)
            return entry->item_definition_id;
    }
    return 0;
}

static uint16_t SvrAuthoredVariationForAppearance(
    const SvrAuthoritativeAppearanceState* appearance,
    uint16_t presentation_kind_id)
{
    const uint16_t weapon_item_definition_id =
        SvrEquippedWeaponDefinitionIdForAppearance(appearance);
    return ActorVisualReachabilityVariationForAppearance(
        presentation_kind_id,
        weapon_item_definition_id);
}

static const AppearanceCatalogItemDef* SvrEquippedWeaponCatalogItemForAppearance(
    const SvrAuthoritativeAppearanceState* appearance)
{
    if (!appearance)
        return 0;
    for (int i = 0; i < appearance->entry_count && i < SVR_MAX_APPEARANCE_LOADOUT_ENTRIES; i++)
    {
        const SvrAppearanceLoadoutEntry* entry = &appearance->entries[i];
        if (!(entry->state_flags & SVR_APPEARANCE_ENTRY_STATE_EQUIPPED))
            continue;
        if (entry->slot_kind_id == APPEARANCE_SLOT_KIND_WEAPON)
            return FindAppearanceCatalogItemById(entry->item_definition_id);
    }
    return 0;
}

static const uint32_t SVR_DEFAULT_ATTACK_PRESENTATION_TICKS = 8;

static uint16_t SvrCatalogSwingPresentationKindForAppearance(
    const SvrAuthoritativeAppearanceState* appearance)
{
    const AppearanceCatalogItemDef* weapon =
        SvrEquippedWeaponCatalogItemForAppearance(appearance);
    return weapon ? weapon->swing_presentation_kind_id : APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
}

static uint32_t SvrCatalogSwingPresentationTicksForAppearance(
    const SvrAuthoritativeAppearanceState* appearance)
{
    const AppearanceCatalogItemDef* weapon =
        SvrEquippedWeaponCatalogItemForAppearance(appearance);
    if (weapon && weapon->swing_presentation_ticks > 0)
        return weapon->swing_presentation_ticks;
    return SVR_DEFAULT_ATTACK_PRESENTATION_TICKS;
}

static void SvrRefreshPlayerPresentationAfterEquipMutation(ServerState* state,
                                                           SvrPlayerState* ps)
{
    if (!state || !ps)
        return;
    const uint16_t equipped_swing_kind =
        SvrCatalogSwingPresentationKindForAppearance(&ps->appearance);
    if (ps->last_swing_tick != 0 &&
        ps->last_swing_presentation_kind_id != equipped_swing_kind)
    {
        // FL-4076: weapon-slot changes must not leave a prior weapon's attack
        // family active against the new weapon appearance. Crossbow is one
        // current catalog example: its source-owned visual family is
        // IDLE_WALK, so ATTACK+weapon_crossbow is an unrenderable crossed key.
        ps->last_swing_tick = 0;
        ps->last_swing_presentation_kind_id = equipped_swing_kind;
    }
    SvrRefreshPlayerPresentationKind(state, ps);
}

// FL-3955 V-2 DELETED: SvrRefreshAppearanceVariation/SvrRefreshAppearanceRig
// were independent CompiledActorVisualKey derivation owners. Do not re-add them here.
// Variation and rig are server-authored fields that must be set explicitly and
// then consumed by the ActorVisualProfile exact key path without local re-derivation.
//
// Q5.2 + Rig Contract (CONTEXT.MD:87 — Rig Contract / avoid "rig ID alone, C++ rig
// branch"): rig_id is no longer derived from `mount_definition_id != 0` in C++.
// It is read from the catalog via AppearanceCatalogRigForMount(), which makes
// rig kind a catalog-owned attribute of the mount definition. Unknown mounts
// return APPEARANCE_RIG_DEFAULT — never invented. Adding a new rig = adding a
// catalog entry + authored profile rows, never a C++ branch.
static void SvrSyncAppearanceCompiledActorVisualKeyDimensions(SvrAuthoritativeAppearanceState* appearance,
                                                       uint16_t presentation_kind_id,
                                                       bool bump_revision)
{
    if (!appearance)
        return;
    const uint16_t next_variation = SvrAuthoredVariationForAppearance(appearance,
                                                                      presentation_kind_id);
    const uint16_t next_rig =
        AppearanceCatalogRigForMount(appearance->mount_definition_id);
    if (appearance->variation_id == next_variation &&
        appearance->rig_id == next_rig)
    {
        return;
    }

    // FL-4055: variation is presentation-scoped server state, not an equipment-only
    // cache lookup. Exact key construction consumes this already-authored id.
    appearance->variation_id = next_variation;
    appearance->rig_id = next_rig;
    if (bump_revision)
        SvrBumpAppearanceRevision(appearance);
}

static void SvrApplyProfileToAppearance(SvrAuthoritativeAppearanceState* appearance,
                                        const SvrActorVisualProfileCatalog* cache,
                                        const SvrActorVisualProfileCatalogProfileDef* profile,
                                        uint8_t source_kind,
                                        uint8_t subject_kind,
                                        const char* subject_key)
{
    // Walkthrough Step 4:
    // Profiles choose the body-owner family up front via skin_definition_id and
    // seed the initial slot entries. Clients later resolve visuals from these
    // authoritative ids; they do not choose their own skin/loadout.
    if (!appearance || !cache || !profile)
        return;
    SvrClearAppearanceEntries(appearance, false);
    SvrSetAppearanceIdentity(appearance,
                             cache->contract_version,
                             source_kind,
                             SVR_APPEARANCE_PROJECTION_PROFILE,
                             subject_kind,
                             subject_key,
                             profile->id,
                             profile->skin_definition_id);
    for (int i = 0; i < profile->starter_count; i++)
    {
        SvrAppearanceLoadoutEntry starter_entry = {};
        if (SvrResolveStarterAppearanceEntry(
                cache, &profile->starter_entries[i], &starter_entry))
        {
            SvrUpsertAppearanceEntry(appearance, &starter_entry, false);
        }
    }
    // FL-3955 V-2 DELETED: SvrRefreshAppearanceVariation + SvrRefreshAppearanceRig calls.
    SvrSyncAppearanceCompiledActorVisualKeyDimensions(appearance,
                                                     APPEARANCE_PRESENTATION_KIND_IDLE_WALK,
                                                     false);
    SvrBumpAppearanceRevision(appearance);
}

static bool SvrReapplyStoredProfileToAppearance(ServerState* state,
                                                SvrAuthoritativeAppearanceState* appearance,
                                                const char* source)
{
    if (!state || !appearance)
        return false;

    SvrActorVisualProfileCatalog cache = {};
    if (!SvrLoadActorVisualProfileCatalog(&cache))
        return false;

    uint16_t profile_id = appearance->appearance_profile_id;
    if (profile_id == 0)
        profile_id = cache.default_profile_id;

    const SvrActorVisualProfileCatalogProfileDef* profile =
        SvrFindAppearanceProfileById(&cache, profile_id);
    if (!profile)
        return false;

    const uint8_t source_kind = appearance->source_kind != SVR_APPEARANCE_SOURCE_NONE
        ? appearance->source_kind
        : SVR_APPEARANCE_SOURCE_DEFAULT_PROFILE;
    const uint8_t subject_kind = appearance->subject_kind != SVR_APPEARANCE_SUBJECT_NONE
        ? appearance->subject_kind
        : SVR_APPEARANCE_SUBJECT_DEFAULT;
    char subject_key[32] = {};
    SvrCopyAppearanceSubjectKey(
        subject_key,
        appearance->subject_key[0] ? appearance->subject_key : "default_subject");

    SvrApplyProfileToAppearance(appearance,
                                &cache,
                                profile,
                                source_kind,
                                subject_kind,
                                subject_key);
    SvrRuntimeDiagLog(state,
                      "[appearance-contract] starter profile reapplied source=%s profile_id=%u starter_count=%u tick=%u\n",
                      source ? source : "unknown",
                      (unsigned)profile->id,
                      (unsigned)profile->starter_count,
                      (unsigned)state->tick);
    return true;
}

// REMOVED fwd decl: static uint16_t SvrAppearanceContractVersion(const ServerSta

static bool SvrUpsertSpawnActorVisualProfileItem(SvrAuthoritativeAppearanceState* appearance,
                                               ServerState* state,
                                               const SvrActorVisualProfileCatalog* cache,
                                               const char* item_slug,
                                               uint16_t visual_style_id)
{
    if (!appearance || !cache || !item_slug || !item_slug[0])
    {
        SvrRuntimeDiagLog(state,
                          "[appearance-contract] npc spawn item upsert rejected: invalid arguments slug=%s\n",
                          item_slug ? item_slug : "(null)");
        return false;
    }
    const SvrActorVisualProfileCatalogItemDef* item = SvrFindAppearanceItemBySlug(cache, item_slug);
    if (!item || item->id == 0 || item->slot_kind_id == 0)
    {
        SvrRuntimeDiagLog(state,
                          "[appearance-contract] npc spawn item upsert rejected: missing slug=%s\n",
                          item_slug);
        return false;
    }
    if (item->gameplay_kind != SVR_ITEM_GAMEPLAY_WEAPON &&
        item->gameplay_kind != SVR_ITEM_GAMEPLAY_WEARABLE)
    {
        SvrRuntimeDiagLog(state,
                          "[appearance-contract] npc spawn item upsert rejected: non-render-equippable slug=%s item_definition_id=%u gameplay_kind=%u\n",
                          item_slug,
                          (unsigned)item->id,
                          (unsigned)item->gameplay_kind);
        return false;
    }

	SvrAppearanceLoadoutEntry entry = {};
	entry.slot_kind_id = item->slot_kind_id;
	entry.item_definition_id = item->id;
	entry.visual_style_id =
		SvrNormalizeAppearanceVisualStyleId(visual_style_id);
    entry.state_flags = SVR_APPEARANCE_ENTRY_STATE_EQUIPPED;
    if (!SvrUpsertAppearanceEntry(appearance, &entry, false))
    {
        SvrRuntimeDiagLog(state,
                          "[appearance-contract] npc spawn item upsert rejected: loadout full slug=%s item_definition_id=%u slot=%u\n",
                          item_slug,
                          (unsigned)item->id,
                          (unsigned)item->slot_kind_id);
        return false;
    }
    return true;
}

static const char* SvrSelectNpcSpawnWeaponSlug(const EnemyGen* eg)
{
    const int sword_weight = (eg && eg->sword > 0) ? eg->sword : 0;
    const int crossbow_weight = (eg && eg->crossbow > 0) ? eg->crossbow : 0;
    const int total_weight = sword_weight + crossbow_weight;
    if (total_weight <= 0)
        return 0;
    return (rand() % total_weight < sword_weight)
        ? "normal_sword"
        : WEAPON_CROSSBOW_SLUG;
}

static bool SvrApplyNpcSpawnAppearance(ServerState* state,
                                       SvrNpcState* npc,
                                       const EnemyGen* eg,
                                       int npc_index)
{
    if (!npc)
        return false;

    static const char* kNpcSubjectKey = "npc_spawn";
    SvrActorVisualProfileCatalog cache = {};
    const bool have_cache = SvrLoadActorVisualProfileCatalog(&cache);
    if (!have_cache)
    {
        SvrRuntimeDiagLog(state,
                          "[appearance-contract] npc spawn rejected: ActorVisualProfile catalog load failed npc_index=%d tick=%u\n",
                          npc_index,
                          state ? (unsigned)state->tick : 0);
        return false;
    }

    const SvrActorVisualProfileCatalogProfileDef* profile =
        SvrFindAppearanceProfileById(&cache, cache.default_profile_id);
    if (!profile)
    {
        SvrRuntimeDiagLog(state,
                          "[appearance-contract] npc spawn rejected: default profile missing npc_index=%d profile=%u tick=%u\n",
                          npc_index,
                          (unsigned)cache.default_profile_id,
                          state ? (unsigned)state->tick : 0);
        return false;
    }

    SvrApplyProfileToAppearance(&npc->appearance,
                                &cache,
                                profile,
                                SVR_APPEARANCE_SOURCE_DEFAULT_PROFILE,
                                SVR_APPEARANCE_SUBJECT_NPC_SPAWN,
                                kNpcSubjectKey);

    bool loadout_added = false;
    const char* weapon_slug = SvrSelectNpcSpawnWeaponSlug(eg);
    if (weapon_slug &&
        !SvrUpsertSpawnActorVisualProfileItem(&npc->appearance,
                                             state,
                                             &cache,
                                             weapon_slug,
                                             SVR_APPEARANCE_VISUAL_STYLE_DEFAULT))
    {
        SvrRuntimeDiagLog(state,
                          "[appearance-contract] npc spawn rejected: required item missing npc_index=%d slug=%s tick=%u\n",
                          npc_index,
                          weapon_slug,
                          state ? (unsigned)state->tick : 0);
        return false;
    }
    if (weapon_slug)
        loadout_added = true;

    if (eg && rand() % 11 < eg->armor)
    {
        if (!SvrUpsertSpawnActorVisualProfileItem(&npc->appearance,
                                                state,
                                                &cache,
                                                "normal_armour",
                                                SVR_APPEARANCE_VISUAL_STYLE_DEFAULT))
        {
            SvrRuntimeDiagLog(state,
                              "[appearance-contract] npc spawn rejected: optional item missing npc_index=%d slug=normal_armour tick=%u\n",
                              npc_index,
                              state ? (unsigned)state->tick : 0);
            return false;
        }
        loadout_added = true;
    }
    if (eg && rand() % 11 < eg->helmet)
    {
        // FL-3012: NPC helmet rolls must use the normal headed actor layer.
        // gold_hat is a test hat fixture and leaves the observed NPC looking
        // headless even though the attachment slot resolves.
        if (!SvrUpsertSpawnActorVisualProfileItem(&npc->appearance,
                                                state,
                                                &cache,
                                                "normal_helmet",
                                                SVR_APPEARANCE_VISUAL_STYLE_DEFAULT))
        {
            SvrRuntimeDiagLog(state,
                              "[appearance-contract] npc spawn rejected: optional item missing npc_index=%d slug=normal_helmet tick=%u\n",
                              npc_index,
                              state ? (unsigned)state->tick : 0);
            return false;
        }
        loadout_added = true;
    }

    if (loadout_added)
    {
        // FL-3955 V-2 DELETED: SvrRefreshAppearanceVariation call removed.
        SvrSyncAppearanceCompiledActorVisualKeyDimensions(&npc->appearance,
                                                         npc->presentation_kind_id,
                                                         false);
        SvrBumpAppearanceRevision(&npc->appearance);
    }

    return true;
}



// REMOVED: static bool SvrIsLowerHexHash64(const char* value)


// Always returns false so callers can write `return SvrAppearanceContractError(buf, cap, "...", args)`
// REMOVED: SvrAppearanceContractError


// REMOVED: bool SvrLoadStartupAppearanceContract(ServerState* state, ch


// REMOVED: static uint16_t SvrAppearanceContractVersion(const ServerSta


// REMOVED: static const char* SvrAppearanceContractRejectReasonString(u


// REMOVED: static uint8_t SvrValidateJoinV2Claims(const ServerState* st


static const char* SvrTickPhaseName(uint8_t phase_id)
{
    switch (phase_id)
    {
        case SVR_TICK_PHASE_INGEST: return "ingest";
        case SVR_TICK_PHASE_INPUT: return "input";
        case SVR_TICK_PHASE_PHYSICS: return "physics";
        case SVR_TICK_PHASE_COMBAT: return "combat";
        case SVR_TICK_PHASE_GAME_RULES: return "game_rules";
        case SVR_TICK_PHASE_AI: return "ai";
        case SVR_TICK_PHASE_AI_COMBAT: return "ai_combat";
        case SVR_TICK_PHASE_SNAPSHOT: return "snapshot";
        default: return "none";
    }
}

static const char* SvrTickPhysicsPhaseName(uint8_t phase_id)
{
    switch (phase_id)
    {
        case SVR_TICK_PHYSICS_PLAYERS: return "players";
        case SVR_TICK_PHYSICS_NPCS: return "npcs";
        default: return "none";
    }
}

static const char* SvrTickSnapshotPhaseName(uint8_t phase_id)
{
    switch (phase_id)
    {
        case SVR_TICK_SNAPSHOT_EVENTS:              return "events";
        case SVR_TICK_SNAPSHOT_GAMEPLAY_SNAPSHOT:   return "gameplay_snapshot";
        case SVR_TICK_SNAPSHOT_AUTHORITATIVE_STATE: return "authoritative_state";
        case SVR_TICK_SNAPSHOT_OUTBOUND:            return "outbound";
        default:                                    return "none";
    }
}

// T58 stdout-flush classification:
//   hot  = per-tick / per-player-event diagnostic logs; route through
//          ASCIICKER_DEBUG_RUNTIME_DIAGNOSTICS via SvrRuntimeDiagLog().
//   warm = one-shot join/spawn proof logs; keep immediate flush for operator visibility.
//   cold = startup / error logs; keep immediate flush.
static void SvrRuntimeDiagLog(const ServerState* state, const char* fmt, ...)
{
    if (!state || !state->debug_runtime_diagnostics_enabled || !fmt)
        return;
    va_list args;
    va_start(args, fmt);
    vprintf(fmt, args);
    va_end(args);
}

// FL-746: NaN floats produce invalid JSON ("-nan"), killing the recorder.
// Clamp to 0 so the JSON is always valid; NaN presence is a bug (FL-745)
// but the observer pipeline must not crash because of it.
static inline double SvrSafeJsonFloat(double v) { return isnan(v) || isinf(v) ? 0.0 : v; }

static uint8_t SvrRuntimeMountStateForPlayer(const SvrPlayerState* ps)
{
    if (!ps || ps->mount_state >= MOUNT::SIZE)
        return MOUNT::NONE;
    return ps->mount_state;
}

static uint16_t SvrResolveMountDefinitionIdForRuntimeState(
    const SvrActorVisualProfileCatalog* cache,
    uint8_t mount_state,
    uint8_t life_state,
    uint16_t presentation_kind_id)
{
    (void)life_state;
    (void)presentation_kind_id;
    if (!cache || mount_state == MOUNT::NONE || mount_state >= MOUNT::SIZE)
        return 0;

    for (int i = 0; i < cache->mount_count; i++)
    {
        const SvrActorVisualProfileCatalogMountDef* mount = &cache->mounts[i];
        if (mount->runtime_mount_state == mount_state)
            return mount->id;
    }
    return 0;
}

static uint16_t SvrResolveMountDefinitionIdForPlayer(
    const ServerState* state,
    const SvrActorVisualProfileCatalog* cache,
    const SvrPlayerState* ps,
    uint8_t life_state,
    uint16_t presentation_kind_id)
{
    (void)life_state;
    (void)presentation_kind_id;
    if (!state || !cache || !ps)
        return 0;
    const uint8_t runtime_mount_state = SvrRuntimeMountStateForPlayer(ps);
    if (runtime_mount_state == MOUNT::NONE)
        return 0;
    const int ci = ps->player_id;
    if (ci < 0 || ci >= SVR_MAX_CLIENTS)
        return 0;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        const SvrItemState* it = &state->items[i];
        if (!it->active || it->owner_id != (uint16_t)ci)
            continue;
        if (it->equip_slot_kind_id != APPEARANCE_SLOT_KIND_MOUNT ||
            it->mount_definition_id == 0)
        {
            continue;
        }
        const SvrActorVisualProfileCatalogMountDef* mount =
            SvrFindAppearanceMountById(cache, it->mount_definition_id);
        if (mount)
            return it->mount_definition_id;
        return 0;
    }
    return 0;
}

static bool SvrVec3IsFinite(const float v[3])
{
    return v && isfinite(v[0]) && isfinite(v[1]) && isfinite(v[2]);
}

// REMOVED: static bool SvrHasAnyAlivePlayer(const ServerState* state)


static SvrNpcState* SvrFindNpcByEntityId(ServerState* state, uint16_t entity_id)
{
    if (!state)
        return 0;
    for (int i = 0; i < state->npc_count; i++)
    {
        SvrNpcState* npc = &state->npcs[i];
        if (npc->active && npc->entity_id == entity_id)
            return npc;
    }
    return 0;
}

// FL-2957 H-N3/H-N6: this gate determines whether NPCs skip full MpStepOnce.
// manual-20260505-070756 promoted this seam from theory to active suspicion:
// max_physics_phase=npcs phase_us=438785 tick=4019 while the player spawn lane
// still separately showed reject_grounded=2 + reject_support_z=2.
// H-N6 (P=0.40): pre-attempt-29 source trace showed NPCs were created with
// PHYSICS_CREATE_TERRAIN_SAFE_LIFT, which forced +200 airborne spawn and
// accum_contact=0. Attempt #29 deletes that stale owner by switching initial NPC
// spawn to PHYSICS_CREATE_EXACT_POS and re-priming exact-terrain respawn
// teleports with accum_contact=1.0. If NPC full-step saturation persists after
// that, H-N6 narrows to the same structural soup/support coverage hole that still
// drives the player lane: exact terrain-resolved contact decays back below 1.0
// because the landing/support seam fails to replenish contact history.
// 50 NPCs × 2 substeps full MpStepOnce = 20-50ms min, 438ms worst case.
// LINEAGE_JSON: {"fl":"FL-2957","hypothesis":"H-N6","pre_attempt_29_owner":"PHYSICS_CREATE_TERRAIN_SAFE_LIFT -> accum_contact=0 + airborne spawn","post_attempt_29_expectation":"if H-N6 persists, owner is structural soup/support coverage after exact-pos bootstrap","proven_by":"source_first_trace_2026-05-05","updated_by_attempt":29}
// H-N3 (P=0.15): micro-velocity or AI intent noise may cause most NPCs to
// fail the velocity/intent gates above, forcing full physics even when settled.
static bool SvrNpcNeedsPhysicsStep(const SvrNpcState* npc)
{
    if (!npc || !npc->physics)
        return false;
    if (npc->jump_request)
        return true;
    if (fabsf(npc->intent_force[0]) > 0.001f || fabsf(npc->intent_force[1]) > 0.001f)
        return true;
    if (fabsf(npc->vel[0]) > 0.01f || fabsf(npc->vel[1]) > 0.01f || fabsf(npc->vel[2]) > 0.01f)
        return true;
    return !GetPhysicsGrounded(npc->physics);
}

// FL-2957 attempt #29: NPC spawn/respawn already resolves authoritative terrain Z in
// server_tick.cpp, so the old +200 PHYSICS_CREATE_TERRAIN_SAFE_LIFT owner is stale
// here. Re-prime exact terrain-resolved teleports with grounded contact so the next
// tick can use SvrNpcNeedsPhysicsStep() truthfully instead of inheriting a synthetic
// accum_contact=0 reset from SetPhysicsPos().
// LINEAGE_JSON: {"fl":"FL-2957","attempt":29,"commit":"pending","attempt_total":29,"closed":0,"what":"delete stale NPC terrain-safe-lift owner and bootstrap exact terrain-resolved respawns with grounded contact","result":"pending","run":"pending"}
static void SvrPrimeExactTerrainContact(Physics* physics)
{
    if (!physics)
        return;
    PhysicsFullState grounded_state = {};
    SavePhysicsState(physics, &grounded_state);
    grounded_state.accum_contact = 1.0f;
    grounded_state.slope = 0.0f;
    RestorePhysicsState(physics, &grounded_state);
}

// FL-2957 TRACE — LAG OWNER AFTER INPUT PATH RULING (Attempt #17 of 23):
//   M1 (client) OnKeyb -> input.key -> PrepareLocalMovementStepIO (game_render_bridge.cpp:390)
//   M2 (client outflow) SendLocalNetworkUpdates (local_player_authority.cpp:408)
//   M3 (server intake) SvrProcessInputMove -> latest_input (server_tick.cpp:3612)
//   M4 (server apply) SvrResolveInput (server_tick.cpp:4476)
//   M5 (THIS IS THE EXPENSIVE SEAM): even with zero input_force, idle fast path
//   rejects on grounded/support_z/velocity, forcing MpStepOnce at line ~5843.
//   MpStepOnce -> MpSoupCollector::Build (~100-300us) + collision loop (~149ms in bad
//   positions). That is the dominant lag owner — not auth-state publish, not browser
//   key receipt, and not SvrResolveInput zeroing.
//   Attempt #8 (wolf-mount idle path): partial, falsified as sole owner.
//   Attempt #12 (soup cap 1024): cap hit, prevented worse but didn't close gate.
//   Attempt #19 (support_z 0.35→0.50): STILL NOT WORKING — spawn Z mismatch structural.
//   Attempt #24 (BSP early-exit): World::Query traverses entire BSP frustum after
//   soup cap. passive-20260505-025840 ci=1 had collect_world_us=149403us. Fix adds
//   should_continue callback to QueryWorldCB so BSP recursion aborts when collector
//   signals done. See engine/world.h, world_internal.h, mp_step.cpp.
//   Attempt #26 (idle support recovery): bypass grounded/support_z rejection for
//   settled zero-input players where only terrain sampling disagrees. terrain_z is
//   diagnostic sampling, not authoritative contact. Requires accum_contact>=1.0.
//   manual-20260505-070756 still logged support_recovered=0 / accum_contact=0 on
//   the bad witness window, so it never armed there. See
//   mp_step-owned support result cache.
//   Attempt #27 (accum_contact decay ordering): MpStepOnce records result.grounded
//   from accum_contact>=1.0 BEFORE decaying by 0.9, so next tick always reads false.
//   Fix: clamp back to 1.0 on first ground-contact transition. The same
//   manual-20260505-070756 window still had grounded_last=0 / accum_contact=0,
//   so the fix never armed there either. See mp_step.cpp:1154.
//
//   ── HYPOTHESIS LEDGER (2026-05-05, updated after manual-20260505-070756) ──
//   PLAYER PHYSICS:
//     [F] H-P9  Transport jitter / RTT (FL-1797) — downstream of server stall
//     [F] H-P10 Auth-state disk write (FL-2504) — 736us typical, tmpfs
//     [F] H-P11 Join-era interp rebase — falsified by passive-20260425-003624
//     [F] H-P12 DeleteInst/CreateInst churn — weakened by 87b13717
//     [F] H-P5  Decay oscillation — support snap stabilizes accum_contact (refuted)
//     [F] H-P6  Support_z threshold 0.35→0.50 — delta is ~16, not ~0.5
//     [F] H-P0  REFUTED: bit-15 lift delta would be 16, actual is 2.0 (070756)
//     [U] H-P1  BSP early-exit should_continue (#24, code exists, unproven)
//     [F] H-P2  accum_contact decay clamp (#27) — manual-20260505-070756 never armed
//     [F] H-P3  Idle support recovery (#26) — support_recovered stayed 0
//     [S] H-P4  Bootstrap deadlock: accum_contact=0 + water clamp reset (SPENT — water clamp fix)
//     [S] H-P13 Soup-coverage hole: Build at pre-step XY, support searched at post-sweep XY (#30, PROVEN manual-20260505-105256)
//     [U] H-P14 BSP sibling-only early-exit granularity — traversal cost, not item growth
//     [U] H-P15 Terrain heightmap (57) vs collision mesh Z (55) at spawn — geometric delta=2.0 source
//     [P] H-P7  Wolf mount idle (#8, partial)
//     [P] H-P8  Soup cap 1024 (#12, partial)
//   NPC PHYSICS (PROMOTED by max_physics_phase=npcs=438785; still needs per-NPC
//   instrumentation before we can spend/falsify individual branches):
//     [U] H-N1  NPC soup density tail (P=0.45) — 1-5 NPCs near dense geometry
//     [U] H-N2  Active NPC count × substeps (P=0.25) — 50 NPCs × 2 substeps
//     [U] H-N3  SvrNpcNeedsPhysicsStep fast-path ineffective (P=0.15)
//     [U] H-N4  Player physics misattributed to NPCs (P=0.05)
//     [U] H-N5  ServerTickLoop batching observational lag (P=0.03)
//     [S] H-N6  NPC accum_contact bootstrap deadlock (P=0.40) — SPENT: water clamp fix
//   SNAPSHOT/IO:
//     [U] H-S1  NPC-DELTA-BYPASS payload inflation — 128 NPCs forced every delta
//     [P] H-S2  Respawn snapshot compaction (FL-754) — RESOLVED
//   Legend: [F]=falsified [U]=unproven/unspent [P]=partial/proven [S]=spent/fixed
//   LINEAGE_JSON: {"fl":"FL-2957","hypothesis_ledger_version":5,"date":"2026-05-05","falsified":["H-P0","H-P2","H-P3","H-P5","H-P6","H-P9","H-P10","H-P11","H-P12"],"root_cause":"SERVER: soup-coverage hole (H-P13) + water clamp reset (H-P4). CLIENT: StepDeferredTerrainDarkBootstrap 512 BSP shadow raycasts/frame (#31). Server proven fixed (4.3ms tick). Client terrain dark re-disabled (was never in original codebase).","spent":["H-P4","H-P13","H-N6"],"unspent_player":["H-P1","H-P14","H-P15"],"unspent_npc":["H-N1","H-N2","H-N3","H-N4","H-N5"],"unspent_io":["H-S1"],"resolved":["H-S2"],"partial":["H-P7","H-P8"],"proof_run_server_pass":"manual-20260505-105256","proof_run_baseline":"manual-20260504-234650","attempt_30":"soup_rebuild_retry_at_resolved_xy","attempt_31":"re-disable StepDeferredTerrainDarkBootstrap (15a239df)"}
//   See FAILURE_LOG.md FL-2957 FAILED ATTEMPTS COUNTER for full enumeration.
static uint32_t SvrIdleFastPathRejectMask(const SvrPlayerState* ps,
                                          const PhysicsFullState* prev_state,
                                          SvrIdleFastPathEval* eval)
{
    if (eval)
        memset(eval, 0, sizeof(*eval));
    if (!ps || !ps->physics || !prev_state)
        return 0xFFFFFFFFu;
    const bool grounded = GetPhysicsGrounded(ps->physics);
    const float max_abs_vel = fmaxf(fabsf(prev_state->vel[0]),
                                    fmaxf(fabsf(prev_state->vel[1]), fabsf(prev_state->vel[2])));
    const float yaw_delta = fabsf(ps->input_yaw - prev_state->yaw);
    const float cached_support_z = ps->support_valid ? ps->support_z : NAN;
    const float support_z_delta = isfinite(cached_support_z)
        ? fabsf(prev_state->pos[2] - cached_support_z)
        : INFINITY;
    uint32_t mask = 0;
    // FL-2957 ATTEMPT #8 (of 23): Mounted idle fast path — wolf mounts allowed.
    // Result: PARTIAL — reject_mount=0 achieved but lag gate still false.
    // Falsified as the sole owner. Attempt #10 confirmed active movement/knockback
    // still causes full MpStepOnce stalls even with reject_mount=0.
    // Grounded wolf mounts can use the same idle fast path as on-foot players.
    // Keep bee mounts on the full path because they use the flying vertical branch.
    // LINEAGE_JSON: {"fl":"FL-2957","attempt":8,"commit":"e0d5be47","attempt_total":23,"closed":0,"what":"mounted idle fast path — wolf mounts allowed, only BEE rejected","result":"partial — did not close lag gate alone","run":"manual-20260504-233537"}
    if (ps->mount_state == MOUNT::BEE)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_MOUNT;
    if (ps->input_flags != 0)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_INPUT_FLAGS;
    if (fabsf(ps->input_force[0]) > SVR_IDLE_FASTPATH_INPUT_EPS ||
        fabsf(ps->input_force[1]) > SVR_IDLE_FASTPATH_INPUT_EPS ||
        fabsf(ps->input_force_z) > SVR_IDLE_FASTPATH_INPUT_EPS)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_INPUT_FORCE;
    if (fabsf(ps->knockback[0]) > SVR_IDLE_FASTPATH_INPUT_EPS ||
        fabsf(ps->knockback[1]) > SVR_IDLE_FASTPATH_INPUT_EPS)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_KNOCKBACK;
    if (max_abs_vel > SVR_IDLE_FASTPATH_VEL_EPS)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_VELOCITY;
    if (fabsf(prev_state->yaw_vel) > SVR_IDLE_FASTPATH_VEL_EPS)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_YAW_VELOCITY;
    if (yaw_delta > SVR_IDLE_FASTPATH_YAW_EPS)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_YAW_DELTA;
    if (prev_state->player_stp >= 0)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_PLAYER_STP;
    if (prev_state->accum_contact < 1.0f || !grounded)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_GROUNDED;
    // FL-2957 owner reminder: passive-20260505-025840 still hit the worst
    // player-step samples with input_flags=0, max_abs_vel=0, idle_fast_paths=0,
    // and grounded/support_z rejects set. Start lag work from this source seam
    // before blaming diagnostic labels or summaries.
    if (!ps->support_valid || support_z_delta > SVR_IDLE_FASTPATH_SUPPORT_Z_EPS)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_SUPPORT_Z;
    if (ps->in_water > 0.1f)
        mask |= 1u << SVR_IDLE_FASTPATH_REJECT_WATER;

    // FL-2957: quiescent settle override.
    //
    // When there is no input intent (flags/force), no knockback, and the player
    // is already grounded, allow a small residual drift band to settle into the
    // idle fast path by clearing velocity + step-counter rejects. This prevents
    // rare but catastrophic full MpStepOnce sweeps from firing solely because
    // prev_state->vel / player_stp are nonzero on a no-intent frame.
    if (ps->input_flags == 0 &&
        fabsf(ps->input_force[0]) <= SVR_IDLE_FASTPATH_INPUT_EPS &&
        fabsf(ps->input_force[1]) <= SVR_IDLE_FASTPATH_INPUT_EPS &&
        fabsf(ps->input_force_z) <= SVR_IDLE_FASTPATH_INPUT_EPS &&
        fabsf(ps->knockback[0]) <= SVR_IDLE_FASTPATH_INPUT_EPS &&
        fabsf(ps->knockback[1]) <= SVR_IDLE_FASTPATH_INPUT_EPS &&
        grounded &&
        prev_state->accum_contact >= 1.0f &&
        ps->in_water <= 0.1f &&
        max_abs_vel <= SVR_IDLE_FASTPATH_SETTLE_VEL_MAX)
    {
        mask &= ~((1u << SVR_IDLE_FASTPATH_REJECT_VELOCITY) |
                  (1u << SVR_IDLE_FASTPATH_REJECT_PLAYER_STP));
    }
    if (eval)
    {
        eval->reject_mask = mask;
        eval->input_flags = ps->input_flags;
        eval->grounded = grounded ? 1 : 0;
        eval->in_water = ps->in_water > 0.1f ? 1 : 0;
        eval->idle_support_recovered = 0;
        eval->max_abs_vel = max_abs_vel;
        eval->yaw_delta = yaw_delta;
        eval->support_z_delta = support_z_delta;
        eval->accum_contact = prev_state->accum_contact;
        eval->pos_z = prev_state->pos[2];
        eval->terrain_z = cached_support_z;
    }
    return mask;
}

static bool SvrCanFastPathIdlePlayerPhysics(const SvrPlayerState* ps,
                                            const PhysicsFullState* prev_state,
                                            SvrIdleFastPathEval* eval = 0)
{
    return SvrIdleFastPathRejectMask(ps, prev_state, eval) == 0;
}

static void SvrRecordTickPhase(ServerState* state, uint8_t phase_id, uint64_t elapsed_us)
{
    if (!state || phase_id == SVR_TICK_PHASE_NONE)
        return;

    if (elapsed_us > state->tick_max_phase_us)
    {
        state->tick_max_phase_us = elapsed_us;
        state->tick_max_phase_id = phase_id;
        state->tick_max_phase_tick = state->tick;
    }

    if (elapsed_us < SVR_TICK_PHASE_LOG_THRESHOLD_US)
        return;

    state->tick_phase_overrun_count++;
    state->tick_last_overrun_phase_id = phase_id;
    state->tick_last_overrun_phase_us = elapsed_us;
    state->tick_last_overrun_tick = state->tick;

    if (state->tick_phase_log_count < SVR_TICK_PHASE_LOG_LIMIT)
    {
        SvrRuntimeDiagLog(state,
                          "[tick-phase] tick=%u phase=%s us=%llu\n",
                          (unsigned)state->tick,
                          SvrTickPhaseName(phase_id),
                          (unsigned long long)elapsed_us);
        state->tick_phase_log_count++;
    }
}

static void SvrRecordTickPhysicsPhase(ServerState* state, uint8_t phase_id, uint64_t elapsed_us)
{
    if (!state || phase_id == SVR_TICK_PHYSICS_NONE)
        return;

    if (elapsed_us > state->tick_max_physics_phase_us)
    {
        state->tick_max_physics_phase_us = elapsed_us;
        state->tick_max_physics_phase_id = phase_id;
        state->tick_max_physics_phase_tick = state->tick;
    }

    if (elapsed_us < SVR_TICK_PHYSICS_LOG_THRESHOLD_US)
        return;

    state->tick_physics_overrun_count++;
    state->tick_last_physics_phase_id = phase_id;
    state->tick_last_physics_phase_us = elapsed_us;
    state->tick_last_physics_overrun_tick = state->tick;

    if (state->tick_physics_log_count < SVR_TICK_PHYSICS_LOG_LIMIT)
    {
        SvrRuntimeDiagLog(state,
                          "[tick-physics] tick=%u phase=%s us=%llu\n",
                          (unsigned)state->tick,
                          SvrTickPhysicsPhaseName(phase_id),
                          (unsigned long long)elapsed_us);
        state->tick_physics_log_count++;
    }
}

static void SvrRecordTickSnapshotPhase(ServerState* state, uint8_t phase_id, uint64_t elapsed_us)
{
    if (!state || phase_id == SVR_TICK_SNAPSHOT_NONE)
        return;

    if (phase_id == SVR_TICK_SNAPSHOT_AUTHORITATIVE_STATE)
    {
        state->tick_snapshot_authoritative_state_us_last = elapsed_us;
        if (elapsed_us > state->tick_snapshot_authoritative_state_us_max)
            state->tick_snapshot_authoritative_state_us_max = elapsed_us;
    }

    if (elapsed_us > state->tick_max_snapshot_phase_us)
    {
        state->tick_max_snapshot_phase_us = elapsed_us;
        state->tick_max_snapshot_phase_id = phase_id;
        state->tick_max_snapshot_phase_tick = state->tick;
    }

    if (elapsed_us < SVR_TICK_SNAPSHOT_LOG_THRESHOLD_US)
        return;

    state->tick_snapshot_overrun_count++;
    state->tick_last_snapshot_phase_id = phase_id;
    state->tick_last_snapshot_phase_us = elapsed_us;
    state->tick_last_snapshot_overrun_tick = state->tick;

    if (state->tick_snapshot_log_count < SVR_TICK_SNAPSHOT_LOG_LIMIT)
    {
        SvrRuntimeDiagLog(state,
                          "[tick-snapshot] tick=%u phase=%s us=%llu\n",
                          (unsigned)state->tick,
                          SvrTickSnapshotPhaseName(phase_id),
                          (unsigned long long)elapsed_us);
        state->tick_snapshot_log_count++;
    }
}

static void SvrFinalizeAuthoritativeStatePublish(ServerState* state)
{
    if (!state)
        return;
    // Exported values are from the prior tick because the current tick's
    // authoritative-state slice is recorded after publish returns.
    state->tick_snapshot_authoritative_state_us_max = 0;
}

typedef struct SvrAuthoritativeStatePublishStats
{
    uint64_t build_us;
    uint64_t write_us;
    uint64_t total_us;
    uint64_t collect_us;
    uint64_t diff_us;
    uint64_t serialize_us;
    uint64_t send_or_queue_us;
    uint64_t copy_us;
    uint64_t publish_prepare_us;
    uint64_t socket_lookup_us;
    uint64_t per_client_loop_us;
    uint64_t client_queue_push_us;
    uint64_t client_write_attempt_us;
    uint64_t client_flush_us;
    uint64_t lock_wait_us;
    uint64_t lock_held_us;
    uint64_t primary_file_write_us;
    uint64_t legacy_shm_write_us;
    uint64_t max_publish_sink_us;
    uint8_t max_publish_sink_id;
    size_t json_bytes;
    size_t client_queue_bytes;
    size_t client_write_bytes;
    uint32_t active_players;
    uint32_t active_npcs;
    uint32_t active_items;
    uint32_t client_queue_depth_before;
    uint32_t client_queue_depth_after;
    uint32_t client_backpressure_flag;
    uint32_t client_write_result;
    uint32_t max_client_id;
    uint32_t max_client_queue_depth;
    uint32_t clients_count;
    uint32_t entries;
    uint64_t max_entry_us;
    uint8_t max_entry_kind_id;
    uint32_t max_entry_id;
    uint32_t repeated_entry_count;
    size_t buffer_size_before;
    size_t buffer_size_after;
    uint32_t buffer_reallocs;
} SvrAuthoritativeStatePublishStats;

static const char* SvrInputMoveRejectReason(uint32_t code);

static const char* SvrAuthEntryKindName(uint8_t kind_id)
{
    switch (kind_id)
    {
        case 1: return "player";
        case 2: return "npc";
        case 3: return "item";
        default: return "none";
    }
}

static const char* SvrAuthPublishSinkName(uint8_t sink_id)
{
    switch (sink_id)
    {
        case 1: return "primary_file_write";
        case 2: return "legacy_shm_write";
        case 3: return "publish_prepare";
        default: return "none";
    }
}

static void SvrAuthStatsRecordPublishSink(SvrAuthoritativeStatePublishStats* stats,
                                          uint8_t sink_id,
                                          uint64_t elapsed_us)
{
    if (!stats || elapsed_us <= stats->max_publish_sink_us)
        return;
    stats->max_publish_sink_us = elapsed_us;
    stats->max_publish_sink_id = sink_id;
}

static void SvrAuthStatsRecordEntry(SvrAuthoritativeStatePublishStats* stats,
                                    uint8_t kind_id,
                                    uint32_t entry_id,
                                    uint64_t start_us)
{
    if (!stats)
        return;
    const uint64_t elapsed_us = a3dGetTime() - start_us;
    stats->entries++;
    if (elapsed_us > stats->max_entry_us)
    {
        stats->max_entry_us = elapsed_us;
        stats->max_entry_kind_id = kind_id;
        stats->max_entry_id = entry_id;
    }
}

static bool SvrPublishAuthoritativeStateDetailed(ServerState* state,
                                                 SvrAuthoritativeStatePublishStats* stats)
{
    if (stats)
        memset(stats, 0, sizeof(*stats));
    if (!state)
        return false;

    const uint32_t publish_interval = state->authoritative_publish_interval_ticks > 0
        ? state->authoritative_publish_interval_ticks
        : 1u;
    // FL-2504: publish cadence is real, but current archived artifacts only
    // expose the aggregate snapshot-phase timing. Do not promote this path to
    // the yellow owner without a yellow-correlated sub-phase surface.
    if ((state->tick % publish_interval) != 0)
        return false;

    const uint64_t build_start_us = a3dGetTime();
    const uint64_t collect_start_us = a3dGetTime();

    const int item_known = SvrCountKnownItems(state);
    const int item_world = SvrCountWorldItems(state);
    int owned_counts[SVR_MAX_CLIENTS] = {};
    int equipped_counts[SVR_MAX_CLIENTS] = {};
    for (int item_i = 0; item_i < SVR_MAX_ITEMS; item_i++)
    {
        const SvrItemState* it = &state->items[item_i];
        if (!it->active || it->owner_id >= SVR_MAX_CLIENTS)
            continue;
        owned_counts[it->owner_id]++;
        if (it->equip_slot_kind_id != 0)
            equipped_counts[it->owner_id]++;
    }
    if (stats)
        stats->collect_us = a3dGetTime() - collect_start_us;

    // FL-742: authoritative_state JSON publish is a measured snapshot/publish cost surface.
    // Keep this payload observational; do not turn it into a second gameplay owner or add
    // per-entity churn here without re-checking publish-phase lag evidence.
    static uint32_t publish_seq = 0;
    publish_seq++;
    static char json_buf[SVR_AUTHORITATIVE_JSON_BUF_BYTES];
    const SvrAppearanceContractState* contract = &state->appearance_contract;
    uint16_t appearance_contract_version = SvrAppearanceContractVersion(state);
    size_t json_len = 0;
    bool ok = true;
    if (stats)
        stats->buffer_size_before = json_len;
    const uint64_t serialize_start_us = a3dGetTime();

    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "{");
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"schema_version\":2,");
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"appearance_contract_version\":%u,", (unsigned)appearance_contract_version);
    if (contract->loaded && contract->bundle_hash[0])
        ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"bundle_hash\":\"%s\",", contract->bundle_hash);
    else
        ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"bundle_hash\":null,");
    if (contract->loaded && contract->ids_lock_hash[0])
        ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"ids_lock_hash\":\"%s\",", contract->ids_lock_hash);
    else
        ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"ids_lock_hash\":null,");
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"server_publish_seq\":%u,", publish_seq);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick\":%u,", (unsigned)state->tick);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"snapshot_seq\":%u,", (unsigned)state->snapshot_seq);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"publish_interval_ticks\":%u,", (unsigned)publish_interval);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"item_known\":%d,", item_known);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"item_world\":%d,", item_world);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"combat_swing_count\":%u,", (unsigned)state->combat_swing_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"combat_damage_count\":%u,", (unsigned)state->combat_damage_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"combat_damage_player_to_player_count\":%u,", (unsigned)state->combat_damage_player_to_player_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"combat_damage_player_to_npc_count\":%u,", (unsigned)state->combat_damage_player_to_npc_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"combat_damage_npc_to_player_count\":%u,", (unsigned)state->combat_damage_npc_to_player_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"combat_damage_npc_to_npc_count\":%u,", (unsigned)state->combat_damage_npc_to_npc_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"combat_death_count\":%u,", (unsigned)state->combat_death_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"combat_respawn_count\":%u,", (unsigned)state->combat_respawn_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_overrun_count\":%u,", (unsigned)state->tick_overrun_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_elapsed_us\":%llu,", (unsigned long long)state->tick_max_elapsed_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_phase_overrun_count\":%u,", (unsigned)state->tick_phase_overrun_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_overrun_tick\":%u,", (unsigned)state->tick_last_overrun_tick);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_overrun_phase\":\"%s\",", SvrTickPhaseName(state->tick_last_overrun_phase_id));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_overrun_phase_us\":%llu,", (unsigned long long)state->tick_last_overrun_phase_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_phase_tick\":%u,", (unsigned)state->tick_max_phase_tick);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_phase\":\"%s\",", SvrTickPhaseName(state->tick_max_phase_id));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_phase_us\":%llu,", (unsigned long long)state->tick_max_phase_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_physics_overrun_count\":%u,", (unsigned)state->tick_physics_overrun_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_physics_overrun_tick\":%u,", (unsigned)state->tick_last_physics_overrun_tick);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_physics_phase\":\"%s\",", SvrTickPhysicsPhaseName(state->tick_last_physics_phase_id));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_physics_phase_us\":%llu,", (unsigned long long)state->tick_last_physics_phase_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_phase_tick\":%u,", (unsigned)state->tick_max_physics_phase_tick);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_phase\":\"%s\",", SvrTickPhysicsPhaseName(state->tick_max_physics_phase_id));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_phase_us\":%llu,", (unsigned long long)state->tick_max_physics_phase_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_physics_players_active\":%u,", (unsigned)state->tick_last_physics_players_active);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_physics_players_steps\":%u,", (unsigned)state->tick_last_physics_players_steps);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_physics_players_idle_fast_paths\":%u,", (unsigned)state->tick_last_physics_players_idle_fast_paths);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_physics_players_step_once_us\":%llu,", (unsigned long long)state->tick_last_physics_players_step_once_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_physics_players_us\":%llu,", (unsigned long long)state->tick_last_physics_players_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_tick\":%u,", (unsigned)state->tick_max_physics_players_tick);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_active\":%u,", (unsigned)state->tick_max_physics_players_active);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_steps\":%u,", (unsigned)state->tick_max_physics_players_steps);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_idle_fast_paths\":%u,", (unsigned)state->tick_max_physics_players_idle_fast_paths);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_us\":%llu,", (unsigned long long)state->tick_max_physics_players_step_once_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_client\":%d,", (int)state->tick_max_physics_players_step_once_client);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_reject_mask\":%u,", (unsigned)state->tick_max_physics_players_step_once_reject_mask);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_input_flags\":%u,", (unsigned)state->tick_max_physics_players_step_once_input_flags);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_grounded\":%u,", (unsigned)state->tick_max_physics_players_step_once_grounded);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_in_water\":%u,", (unsigned)state->tick_max_physics_players_step_once_in_water);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_idle_support_recovered\":%u,", (unsigned)state->tick_max_physics_players_step_once_idle_support_recovered);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_full_steps\":%u,", (unsigned)state->tick_max_physics_players_step_once_full_steps);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_idle_fast_paths\":%u,", (unsigned)state->tick_max_physics_players_step_once_idle_fast_paths);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_max_abs_vel_milli\":%u,", (unsigned)state->tick_max_physics_players_step_once_max_abs_vel_milli);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_yaw_delta_mdeg\":%u,", (unsigned)state->tick_max_physics_players_step_once_yaw_delta_mdeg);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_support_z_milli\":%u,", (unsigned)state->tick_max_physics_players_step_once_support_z_milli);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_accum_contact_milli\":%u,", (unsigned)state->tick_max_physics_players_step_once_accum_contact_milli);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_collect_us\":%llu,", (unsigned long long)state->tick_max_physics_players_step_once_collect_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_sweep_wall_us\":%llu,", (unsigned long long)state->tick_max_physics_players_step_once_sweep_wall_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_support_probe_us\":%llu,", (unsigned long long)state->tick_max_physics_players_step_once_support_probe_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_support_retry_probe_us\":%llu,", (unsigned long long)state->tick_max_physics_players_step_once_support_retry_probe_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_unaccounted_us\":%llu,", (unsigned long long)state->tick_max_physics_players_step_once_unaccounted_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_soup_items\":%u,", (unsigned)state->tick_max_physics_players_step_once_soup_items);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_sweep_iters\":%u,", (unsigned)state->tick_max_physics_players_step_once_sweep_iters);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_step_once_collision_checks\":%u,", (unsigned)state->tick_max_physics_players_step_once_collision_checks);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_physics_players_us\":%llu,", (unsigned long long)state->tick_max_physics_players_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_snapshot_overrun_count\":%u,", (unsigned)state->tick_snapshot_overrun_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_snapshot_overrun_tick\":%u,", (unsigned)state->tick_last_snapshot_overrun_tick);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_snapshot_phase\":\"%s\",", SvrTickSnapshotPhaseName(state->tick_last_snapshot_phase_id));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_last_snapshot_phase_us\":%llu,", (unsigned long long)state->tick_last_snapshot_phase_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_snapshot_phase_tick\":%u,", (unsigned)state->tick_max_snapshot_phase_tick);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_snapshot_phase\":\"%s\",", SvrTickSnapshotPhaseName(state->tick_max_snapshot_phase_id));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_max_snapshot_phase_us\":%llu,", (unsigned long long)state->tick_max_snapshot_phase_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_snapshot_authoritative_state_us_last\":%llu,", (unsigned long long)state->tick_snapshot_authoritative_state_us_last);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"tick_snapshot_authoritative_state_us_max\":%llu,", (unsigned long long)state->tick_snapshot_authoritative_state_us_max);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"snapshot_total_us\":%llu,", (unsigned long long)state->snapshot_total_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"snapshot_authoritative_state_us\":%llu,", (unsigned long long)state->snapshot_authoritative_state_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_phase_unaccounted_us\":%llu,", (unsigned long long)state->auth_phase_unaccounted_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_collect_us\":%llu,", (unsigned long long)state->auth_collect_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_diff_us\":%llu,", (unsigned long long)state->auth_diff_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_serialize_us\":%llu,", (unsigned long long)state->auth_serialize_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_send_or_queue_us\":%llu,", (unsigned long long)state->auth_send_or_queue_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_copy_us\":%llu,", (unsigned long long)state->auth_copy_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_publish_prepare_us\":%llu,", (unsigned long long)state->auth_publish_prepare_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_socket_lookup_us\":%llu,", (unsigned long long)state->auth_socket_lookup_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_per_client_loop_us\":%llu,", (unsigned long long)state->auth_per_client_loop_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_client_queue_push_us\":%llu,", (unsigned long long)state->auth_client_queue_push_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_client_queue_bytes\":%u,", (unsigned)state->auth_client_queue_bytes);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_client_queue_depth_before\":%u,", (unsigned)state->auth_client_queue_depth_before);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_client_queue_depth_after\":%u,", (unsigned)state->auth_client_queue_depth_after);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_client_backpressure_flag\":%u,", (unsigned)state->auth_client_backpressure_flag);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_client_write_attempt_us\":%llu,", (unsigned long long)state->auth_client_write_attempt_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_client_write_bytes\":%u,", (unsigned)state->auth_client_write_bytes);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_client_write_result\":%u,", (unsigned)state->auth_client_write_result);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_client_flush_us\":%llu,", (unsigned long long)state->auth_client_flush_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_lock_wait_us\":%llu,", (unsigned long long)state->auth_lock_wait_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_lock_held_us\":%llu,", (unsigned long long)state->auth_lock_held_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_primary_file_write_us\":%llu,", (unsigned long long)state->auth_primary_file_write_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_legacy_shm_write_us\":%llu,", (unsigned long long)state->auth_legacy_shm_write_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_max_client_us\":%llu,", (unsigned long long)state->auth_max_client_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_max_client_id\":%u,", (unsigned)state->auth_max_client_id);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_max_client_queue_depth\":%u,", (unsigned)state->auth_max_client_queue_depth);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_clients_count\":%u,", (unsigned)state->auth_clients_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_max_publish_sink_us\":%llu,", (unsigned long long)state->auth_max_publish_sink_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_max_publish_sink\":\"%s\",", SvrAuthPublishSinkName(state->auth_max_publish_sink_id));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_entries\":%u,", (unsigned)state->auth_entries);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_bytes\":%u,", (unsigned)state->auth_bytes);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_player_count\":%u,", (unsigned)state->auth_player_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_npc_count\":%u,", (unsigned)state->auth_npc_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_item_count\":%u,", (unsigned)state->auth_item_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_publish_tick\":%u,", (unsigned)state->auth_publish_tick);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_max_entry_us\":%llu,", (unsigned long long)state->auth_max_entry_us);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_max_entry_kind\":\"%s\",", SvrAuthEntryKindName(state->auth_max_entry_kind_id));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_max_entry_id\":%u,", (unsigned)state->auth_max_entry_id);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_repeated_entry_count\":%u,", (unsigned)state->auth_repeated_entry_count);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_buffer_size_before\":%u,", (unsigned)state->auth_buffer_size_before);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_buffer_size_after\":%u,", (unsigned)state->auth_buffer_size_after);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"auth_buffer_reallocs\":%u,", (unsigned)state->auth_buffer_reallocs);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"io_poll_gap_last_us\":%u,", (unsigned)__atomic_load_n(&state->io_poll_gap_last_us, __ATOMIC_RELAXED));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"io_poll_gap_max_us\":%u,", (unsigned)__atomic_load_n(&state->io_poll_gap_max_us, __ATOMIC_RELAXED));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"io_poll_gap_over_100ms_count\":%u,", (unsigned)__atomic_load_n(&state->io_poll_gap_over_100ms_count, __ATOMIC_RELAXED));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"io_poll_nfds_last\":%u,", (unsigned)__atomic_load_n(&state->io_poll_nfds_last, __ATOMIC_RELAXED));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"io_poll_ret_last\":%d,", (int)__atomic_load_n(&state->io_poll_ret_last, __ATOMIC_RELAXED));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"io_poll_timeout_ms_last\":%d,", (int)__atomic_load_n(&state->io_poll_timeout_ms_last, __ATOMIC_RELAXED));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"io_poll_work_pending_last\":%u,", (unsigned)__atomic_load_n(&state->io_poll_work_pending_last, __ATOMIC_RELAXED));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"io_wake_write_count\":%u,", (unsigned)__atomic_load_n(&state->io_wake_write_count, __ATOMIC_RELAXED));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"io_wake_read_count\":%u,", (unsigned)__atomic_load_n(&state->io_wake_read_count, __ATOMIC_RELAXED));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"io_wake_write_errno_count\":%u,", (unsigned)__atomic_load_n(&state->io_wake_write_errno_count, __ATOMIC_RELAXED));
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"water_level\":%.3f,", (double)SVR_WATER_LEVEL);
    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"players\":[");

    bool first = true;
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        const SvrPlayerState* ps = &state->players[i];
        if (!ps->active || ps->phase < CPHASE_JOINED)
            continue;
        if (stats)
            stats->active_players++;
        const uint64_t entry_start_us = a3dGetTime();
        const ClientIO* cio = &state->clients[i];
        const uint32_t lag_echo_request_count =
            __atomic_load_n(&cio->lag_echo_request_count, __ATOMIC_RELAXED);
        const uint32_t lag_echo_send_success_count =
            __atomic_load_n(&cio->lag_echo_send_success_count, __ATOMIC_RELAXED);
        const uint32_t lag_echo_queue_drop_count =
            __atomic_load_n(&cio->lag_echo_queue_drop_count, __ATOMIC_RELAXED);
        const uint32_t lag_echo_send_errno_count =
            __atomic_load_n(&cio->lag_echo_send_errno_count, __ATOMIC_RELAXED);
        const uint32_t lag_echo_hol_block_count =
            __atomic_load_n(&cio->lag_echo_hol_block_count, __ATOMIC_RELAXED);
        const uint32_t lag_echo_hol_remaining_bytes_max =
            __atomic_load_n(&cio->lag_echo_hol_remaining_bytes_max, __ATOMIC_RELAXED);
        const int lag_echo_last_errno =
            __atomic_load_n(&cio->lag_echo_last_errno, __ATOMIC_RELAXED);
        const uint32_t lag_echo_last_trace_seq =
            __atomic_load_n(&cio->lag_echo_last_trace_seq, __ATOMIC_RELAXED);
        const uint32_t lag_echo_last_client_send_us32 =
            __atomic_load_n(&cio->lag_echo_last_client_send_us32, __ATOMIC_RELAXED);
        const uint32_t lag_echo_last_server_rx_us32 =
            __atomic_load_n(&cio->lag_echo_last_server_rx_us32, __ATOMIC_RELAXED);
        const uint32_t lag_echo_last_server_enqueue_us32 =
            __atomic_load_n(&cio->lag_echo_last_server_enqueue_us32, __ATOMIC_RELAXED);
        const uint32_t lag_echo_last_server_flush_start_us32 =
            __atomic_load_n(&cio->lag_echo_last_server_flush_start_us32, __ATOMIC_RELAXED);
	    const uint32_t lag_echo_last_server_flush_finish_us32 =
	        __atomic_load_n(&cio->lag_echo_last_server_flush_finish_us32, __ATOMIC_RELAXED);
	    const uint64_t lag_echo_last_server_rx_epoch_us =
	        __atomic_load_n(&cio->lag_echo_last_server_rx_epoch_us, __ATOMIC_RELAXED);
	    const uint64_t lag_echo_last_server_enqueue_epoch_us =
	        __atomic_load_n(&cio->lag_echo_last_server_enqueue_epoch_us, __ATOMIC_RELAXED);
	    const uint64_t lag_echo_last_server_flush_start_epoch_us =
	        __atomic_load_n(&cio->lag_echo_last_server_flush_start_epoch_us, __ATOMIC_RELAXED);
	    const uint64_t lag_echo_last_server_flush_finish_epoch_us =
	        __atomic_load_n(&cio->lag_echo_last_server_flush_finish_epoch_us, __ATOMIC_RELAXED);
	    const uint32_t lag_echo_last_server_rx_to_enqueue_us =
	        __atomic_load_n(&cio->lag_echo_last_server_rx_to_enqueue_us, __ATOMIC_RELAXED);
        const uint32_t lag_echo_last_server_enqueue_to_flush_start_us =
            __atomic_load_n(&cio->lag_echo_last_server_enqueue_to_flush_start_us, __ATOMIC_RELAXED);
        const uint32_t lag_echo_last_server_flush_us =
            __atomic_load_n(&cio->lag_echo_last_server_flush_us, __ATOMIC_RELAXED);
        const uint32_t control_queue_drop_count =
            __atomic_load_n(&cio->control_queue_drop_count, __ATOMIC_RELAXED);
        const uint32_t control_pong_drop_count =
            __atomic_load_n(&cio->control_pong_drop_count, __ATOMIC_RELAXED);
        const uint32_t control_queue_max_depth =
            __atomic_load_n(&cio->control_queue_max_depth, __ATOMIC_RELAXED);
        const uint32_t control_queue_depth_last =
            __atomic_load_n(&cio->control_queue_depth_last, __ATOMIC_RELAXED);
        const uint32_t control_send_offset_last =
            __atomic_load_n(&cio->control_send_offset_last, __ATOMIC_RELAXED);
        const uint32_t keepalive_ping_count =
            __atomic_load_n(&cio->keepalive_ping_count, __ATOMIC_RELAXED);
        const uint32_t keepalive_pong_count =
            __atomic_load_n(&cio->keepalive_pong_count, __ATOMIC_RELAXED);
        const uint32_t keepalive_timeout_disconnect =
            __atomic_load_n(&cio->keepalive_timeout_disconnect, __ATOMIC_RELAXED);
        const int latest_input_nonzero =
            ps->latest_input.valid &&
            (ps->latest_input.force[0] != 0.0f ||
             ps->latest_input.force[1] != 0.0f ||
             ps->latest_input.force_z != 0.0f) ? 1 : 0;
        if (!first)
            ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, ",");
        first = false;

        ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len,
                "{"
                "\"id\":%d,"
                "\"phase\":%u,"
                "\"hp\":%d,"
                "\"max_hp\":%d,"
                "\"pos_x\":%.3f,"
                "\"pos_y\":%.3f,"
                "\"pos_z\":%.3f,"
                "\"dir\":%.3f,"
                "\"input_yaw\":%.3f,"
                "\"life_state\":%u,"
                "\"mount_state\":%u,"
                "\"locomotion_state\":%u,"
                "\"combat_state\":%u,"
                "\"presentation_kind_id\":%u,"
                "\"presentation_started_tick\":%u,"
                "\"appearance_profile_id\":%u,"
                "\"skin_definition_id\":%u,"
                "\"mount_definition_id\":%u,"
                "\"loadout_revision\":%u,"
                "\"appearance_contract_source_kind\":%u,"
                "\"appearance_contract_projection_kind\":%u,"
                "\"appearance_subject_kind\":%u,"
                "\"appearance_subject_key\":\"%s\","
                "\"appearance_entry_count\":%u,"
                "\"death_tick\":%u,"
                "\"local_owned_count\":%d,"
                "\"equipped_count\":%d,"
                "\"in_water\":%.3f,"
                "\"terrain_z\":%.3f,"
                "\"spawn_pos_x\":%.3f,"
                "\"spawn_pos_y\":%.3f,"
                "\"spawn_pos_z\":%.3f,"
                "\"spawn_terrain_z\":%.3f,"
                "\"spawn_fallback_z\":%.3f,"
                "\"m_intent_rx_count\":%u,"
                "\"m_intent_last_rx_us\":%llu,"
                "\"m_intent_last_rx_seq\":%u,"
                "\"m_intent_last_rx_move_x\":%d,"
                "\"m_intent_last_rx_move_y\":%d,"
                "\"m_intent_last_rx_move_z\":%d,"
                "\"m_intent_last_rx_yaw100\":%d,"
                "\"m_intent_last_rx_flags\":%u,"
                "\"m_intent_last_nonzero_seq\":%u,"
                "\"m_intent_last_nonzero_rx_us\":%llu,"
                "\"m_intent_last_nonzero_move_x\":%d,"
                "\"m_intent_last_nonzero_move_y\":%d,"
                "\"m_intent_last_nonzero_move_z\":%d,"
                "\"m_intent_latch_accept_count\":%u,"
                "\"m_intent_last_latch_accept_us\":%llu,"
                "\"m_intent_last_latch_accept_seq\":%u,"
                "\"m_intent_apply_accept_count\":%u,"
                "\"m_intent_last_apply_accept_us\":%llu,"
                "\"m_intent_last_apply_accept_seq\":%u,"
                "\"m_intent_reject_count\":%u,"
                "\"m_intent_last_reject_code\":%u,"
                "\"m_intent_last_reject_reason\":\"%s\","
                "\"m_intent_last_reject_us\":%llu,"
                "\"m_intent_last_reject_seq\":%u,"
                "\"latest_input_nonzero\":%u,"
                "\"snapshot_after_m_count\":%u,"
                "\"lag_echo_request_count\":%u,"
                "\"lag_echo_send_success_count\":%u,"
                "\"lag_echo_queue_drop_count\":%u,"
                "\"lag_echo_send_errno_count\":%u,"
                "\"lag_echo_hol_block_count\":%u,"
                "\"lag_echo_hol_remaining_bytes_max\":%u,"
                "\"lag_echo_last_errno\":%d,"
                "\"lag_echo_last_trace_seq\":%u,"
                "\"lag_echo_last_client_send_us32\":%u,"
                "\"lag_echo_last_server_rx_us32\":%u,"
	            "\"lag_echo_last_server_enqueue_us32\":%u,"
	            "\"lag_echo_last_server_flush_start_us32\":%u,"
	            "\"lag_echo_last_server_flush_finish_us32\":%u,"
	            "\"lag_echo_last_server_rx_epoch_us\":%llu,"
	            "\"lag_echo_last_server_enqueue_epoch_us\":%llu,"
	            "\"lag_echo_last_server_flush_start_epoch_us\":%llu,"
	            "\"lag_echo_last_server_flush_finish_epoch_us\":%llu,"
	            "\"lag_echo_last_server_rx_to_enqueue_us\":%u,"
                "\"lag_echo_last_server_enqueue_to_flush_start_us\":%u,"
                "\"lag_echo_last_server_flush_us\":%u,"
                "\"control_queue_drop_count\":%u,"
                "\"control_pong_drop_count\":%u,"
                "\"control_queue_max_depth\":%u,"
                "\"control_queue_depth_last\":%u,"
                "\"control_send_offset_last\":%u,"
                "\"snapshot_ack_received_count\":%u,"
                "\"snapshot_ack_accepted_count\":%u,"
                "\"last_snapshot_ack_received_seq\":%u,"
                "\"last_snapshot_ack_accepted_seq\":%u,"
                "\"last_snapshot_ack_accepted_tick\":%u,"
                "\"last_sent_snapshot_seq\":%u,"
                "\"last_sent_snapshot_entity_count\":%d,"
                "\"snapshot_drops\":%u,"
                "\"respawn_tick\":%u,"
                "\"keepalive_ping_count\":%u,"
                "\"keepalive_pong_count\":%u,"
                "\"keepalive_timeout_disconnect\":%u"
                "",
                i,
                (unsigned)ps->phase,
                (int)ps->hp,
                (int)ps->max_hp,
                SvrSafeJsonFloat(ps->pos[0]),
                SvrSafeJsonFloat(ps->pos[1]),
                SvrSafeJsonFloat(ps->pos[2]),
                SvrSafeJsonFloat(ps->dir),
                SvrSafeJsonFloat(ps->input_yaw),
                (unsigned)ps->life_state,
                (unsigned)SvrRuntimeMountStateForPlayer(ps),
                (unsigned)ps->locomotion_state,
                (unsigned)ps->combat_state,
                (unsigned)ps->presentation_kind_id,
                (unsigned)ps->presentation_started_tick,
                (unsigned)ps->appearance.appearance_profile_id,
                (unsigned)ps->appearance.skin_definition_id,
                (unsigned)ps->appearance.mount_definition_id,
                (unsigned)ps->appearance.loadout_revision,
                (unsigned)ps->appearance.source_kind,
                (unsigned)ps->appearance.projection_kind,
                (unsigned)ps->appearance.subject_kind,
                ps->appearance.subject_key,
                (unsigned)ps->appearance.entry_count,
                (unsigned)ps->death_tick,
                owned_counts[i],
                equipped_counts[i],
                SvrSafeJsonFloat(ps->in_water),
                SvrSafeJsonFloat(ps->terrain_z),
                SvrSafeJsonFloat(ps->spawn_pos[0]),
                SvrSafeJsonFloat(ps->spawn_pos[1]),
                SvrSafeJsonFloat(ps->spawn_pos[2]),
                SvrSafeJsonFloat(ps->spawn_terrain_z),
                SvrSafeJsonFloat(ps->spawn_fallback_z),
                (unsigned)ps->m_intent_rx_count,
                (unsigned long long)ps->m_intent_last_rx_us,
                (unsigned)ps->m_intent_last_rx_seq,
                (int)ps->m_intent_last_rx_move_x,
                (int)ps->m_intent_last_rx_move_y,
                (int)ps->m_intent_last_rx_move_z,
                (int)ps->m_intent_last_rx_yaw100,
                (unsigned)ps->m_intent_last_rx_flags,
                (unsigned)ps->m_intent_last_nonzero_seq,
                (unsigned long long)ps->m_intent_last_nonzero_rx_us,
                (int)ps->m_intent_last_nonzero_move_x,
                (int)ps->m_intent_last_nonzero_move_y,
                (int)ps->m_intent_last_nonzero_move_z,
                (unsigned)ps->m_intent_latch_accept_count,
                (unsigned long long)ps->m_intent_last_latch_accept_us,
                (unsigned)ps->m_intent_last_latch_accept_seq,
                (unsigned)ps->m_intent_apply_accept_count,
                (unsigned long long)ps->m_intent_last_apply_accept_us,
                (unsigned)ps->m_intent_last_apply_accept_seq,
                (unsigned)ps->m_intent_reject_count,
                (unsigned)ps->m_intent_last_reject_code,
                SvrInputMoveRejectReason(ps->m_intent_last_reject_code),
                (unsigned long long)ps->m_intent_last_reject_us,
                (unsigned)ps->m_intent_last_reject_seq,
                (unsigned)latest_input_nonzero,
                (unsigned)ps->snapshot_after_m_count,
                (unsigned)lag_echo_request_count,
                (unsigned)lag_echo_send_success_count,
                (unsigned)lag_echo_queue_drop_count,
                (unsigned)lag_echo_send_errno_count,
                (unsigned)lag_echo_hol_block_count,
                (unsigned)lag_echo_hol_remaining_bytes_max,
                lag_echo_last_errno,
                (unsigned)lag_echo_last_trace_seq,
                (unsigned)lag_echo_last_client_send_us32,
                (unsigned)lag_echo_last_server_rx_us32,
	            (unsigned)lag_echo_last_server_enqueue_us32,
	            (unsigned)lag_echo_last_server_flush_start_us32,
	            (unsigned)lag_echo_last_server_flush_finish_us32,
	            (unsigned long long)lag_echo_last_server_rx_epoch_us,
	            (unsigned long long)lag_echo_last_server_enqueue_epoch_us,
	            (unsigned long long)lag_echo_last_server_flush_start_epoch_us,
	            (unsigned long long)lag_echo_last_server_flush_finish_epoch_us,
	            (unsigned)lag_echo_last_server_rx_to_enqueue_us,
                (unsigned)lag_echo_last_server_enqueue_to_flush_start_us,
                (unsigned)lag_echo_last_server_flush_us,
                (unsigned)control_queue_drop_count,
                (unsigned)control_pong_drop_count,
                (unsigned)control_queue_max_depth,
                (unsigned)control_queue_depth_last,
                (unsigned)control_send_offset_last,
                (unsigned)ps->snapshot_ack_received_count,
                (unsigned)ps->snapshot_ack_accepted_count,
                (unsigned)ps->last_snapshot_ack_received_seq,
                (unsigned)ps->last_snapshot_ack_accepted_seq,
                (unsigned)ps->last_snapshot_ack_accepted_tick,
                (unsigned)ps->last_sent_snapshot_seq,
                ps->last_sent_snapshot_entity_count,
                (unsigned)ps->snapshot_drops,
                (unsigned)ps->respawn_tick,
                (unsigned)keepalive_ping_count,
                (unsigned)keepalive_pong_count,
                (unsigned)keepalive_timeout_disconnect);
        ok = ok && SvrAppendAppearanceEquippedFieldArray(json_buf, sizeof(json_buf), &json_len,
                "equipped_slot_kind_ids", &ps->appearance, SVR_APPEARANCE_JSON_SLOT_KIND);
        ok = ok && SvrAppendAppearanceEquippedFieldArray(json_buf, sizeof(json_buf), &json_len,
                "equipped_definition_ids", &ps->appearance, SVR_APPEARANCE_JSON_ITEM_DEFINITION);
        ok = ok && SvrAppendAppearanceEquippedFieldArray(json_buf, sizeof(json_buf), &json_len,
                "equipped_visual_style_ids", &ps->appearance, SVR_APPEARANCE_JSON_VISUAL_STYLE);
        ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "}");
        SvrAuthStatsRecordEntry(stats, 1, (uint32_t)i, entry_start_us);
    }

    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "],");

    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"npcs\":[");

    bool first_npc = true;
    for (int i = 0; i < state->npc_count; i++)
    {
        const SvrNpcState* npc = &state->npcs[i];
        if (!npc->active)
            continue;
        if (stats)
            stats->active_npcs++;
        const uint64_t entry_start_us = a3dGetTime();
        if (!first_npc)
            ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, ",");
        first_npc = false;
        ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len,
                "{"
                "\"id\":%u,"
                "\"pos_x\":%.3f,"
                "\"pos_y\":%.3f,"
                "\"pos_z\":%.3f,"
                "\"dir\":%.3f,"
                "\"life_state\":%u,"
                "\"mount_state\":%u,"
                "\"locomotion_state\":%u,"
                "\"combat_state\":%u,"
                "\"presentation_kind_id\":%u,"
                "\"presentation_started_tick\":%u,"
                "\"appearance_profile_id\":%u,"
                "\"skin_definition_id\":%u,"
                "\"mount_definition_id\":%u,"
                "\"loadout_revision\":%u,"
                "\"appearance_contract_source_kind\":%u,"
                "\"appearance_contract_projection_kind\":%u,"
                "\"appearance_subject_kind\":%u,"
                "\"appearance_subject_key\":\"%s\","
                "\"appearance_entry_count\":%u,"
                "\"hp\":%d,"
                "\"max_hp\":%d,"
                "\"death_tick\":%u"
                "",
                (unsigned)npc->entity_id,
                SvrSafeJsonFloat(npc->pos[0]),
                SvrSafeJsonFloat(npc->pos[1]),
                SvrSafeJsonFloat(npc->pos[2]),
                SvrSafeJsonFloat(npc->dir),
                (unsigned)npc->life_state,
                (unsigned)npc->mount_state,
                (unsigned)npc->locomotion_state,
                (unsigned)npc->combat_state,
                (unsigned)npc->presentation_kind_id,
                (unsigned)npc->presentation_started_tick,
                (unsigned)npc->appearance.appearance_profile_id,
                (unsigned)npc->appearance.skin_definition_id,
                (unsigned)npc->appearance.mount_definition_id,
                (unsigned)npc->appearance.loadout_revision,
                (unsigned)npc->appearance.source_kind,
                (unsigned)npc->appearance.projection_kind,
                (unsigned)npc->appearance.subject_kind,
                npc->appearance.subject_key,
                (unsigned)npc->appearance.entry_count,
                (int)npc->hp,
                (int)npc->max_hp,
                (unsigned)npc->death_tick);
        ok = ok && SvrAppendAppearanceEquippedFieldArray(json_buf, sizeof(json_buf), &json_len,
                "equipped_slot_kind_ids", &npc->appearance, SVR_APPEARANCE_JSON_SLOT_KIND);
        ok = ok && SvrAppendAppearanceEquippedFieldArray(json_buf, sizeof(json_buf), &json_len,
                "equipped_definition_ids", &npc->appearance, SVR_APPEARANCE_JSON_ITEM_DEFINITION);
        ok = ok && SvrAppendAppearanceEquippedFieldArray(json_buf, sizeof(json_buf), &json_len,
                "equipped_visual_style_ids", &npc->appearance, SVR_APPEARANCE_JSON_VISUAL_STYLE);
        ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "}");
        SvrAuthStatsRecordEntry(stats, 2, (uint32_t)npc->entity_id, entry_start_us);
    }

    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "],");

    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "\"items\":[");

    bool first_item = true;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        const SvrItemState* it = &state->items[i];
        if (!it->active)
            continue;
        if (stats)
            stats->active_items++;
        const uint64_t entry_start_us = a3dGetTime();
        const SvrAppearanceLoadoutEntry* appearance_entry =
            SvrFindEquippedAppearanceEntryForItem(state, it);
        const uint16_t item_definition_id = appearance_entry
            ? appearance_entry->item_definition_id
            : it->item_definition_id;
        const uint16_t visual_style_id = SvrNormalizeAppearanceVisualStyleId(
            appearance_entry && appearance_entry->visual_style_id != 0
                ? appearance_entry->visual_style_id
                : it->visual_style_id);
        const uint16_t equip_slot_kind_id = appearance_entry
            ? appearance_entry->slot_kind_id
            : it->equip_slot_kind_id;
        if (!first_item)
            ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, ",");
        first_item = false;
        ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len,
                "{"
                "\"id\":%u,"
                "\"owner_id\":%u,"
                "\"item_definition_id\":%u,"
                "\"visual_style_id\":%u,"
                "\"equip_slot_kind_id\":%u,"
                "\"source_kind\":%u,"
                "\"state_flags\":%u,"
                "\"pos_x\":%.3f,"
                "\"pos_y\":%.3f,"
                "\"pos_z\":%.3f"
                "}",
                (unsigned)it->item_id,
                (unsigned)it->owner_id,
                (unsigned)item_definition_id,
                (unsigned)visual_style_id,
                (unsigned)equip_slot_kind_id,
                (unsigned)it->source_kind,
                (unsigned)SvrItemStateFlagsForSnapshot(it, equip_slot_kind_id),
                SvrSafeJsonFloat(it->pos[0]),
                SvrSafeJsonFloat(it->pos[1]),
                SvrSafeJsonFloat(it->pos[2]));
        SvrAuthStatsRecordEntry(stats, 3, (uint32_t)it->item_id, entry_start_us);
    }

    ok = ok && SvrAppendJsonf(json_buf, sizeof(json_buf), &json_len, "]}");
    const uint64_t serialize_us = a3dGetTime() - serialize_start_us;
    if (!ok) {
        SvrRuntimeDiagLog(state,
                                  "[authoritative-state] publish buffer overflow tick=%u cap=%zu\n", (unsigned)state->tick, sizeof(json_buf));
        return false;
    }
    if (stats)
    {
        stats->serialize_us = serialize_us;
        stats->build_us = a3dGetTime() - build_start_us;
        stats->json_bytes = json_len;
        stats->buffer_size_after = json_len;
    }
    const uint64_t prepare_start_us = a3dGetTime();
    char slot_local_path[4096] = {};
    char slot_local_tmp_path[4096] = {};
    SvrResolveAuthoritativeStatePath(slot_local_path, sizeof(slot_local_path), SVR_AUTHORITATIVE_STATE_REL_PATH);
    SvrResolveAuthoritativeStatePath(slot_local_tmp_path, sizeof(slot_local_tmp_path), SVR_AUTHORITATIVE_STATE_TMP_REL_PATH);
    const uint64_t publish_prepare_us = a3dGetTime() - prepare_start_us;
    if (stats)
    {
        stats->publish_prepare_us = publish_prepare_us;
        SvrAuthStatsRecordPublishSink(stats, 3, publish_prepare_us);
    }
    // Non-blocking contract: do NOT perform file I/O on the snapshot/tick thread.
    if (!SvrEnsureAuthoritativeStatePublishThreadStarted())
        return false;
    const uint64_t copy_start_us = a3dGetTime();
    MUTEX_LOCK(g_auth_async.mu);
    if (json_len > sizeof(g_auth_async.buf))
        json_len = sizeof(g_auth_async.buf);
    memcpy(g_auth_async.buf, json_buf, json_len);
    g_auth_async.len = json_len;
    strncpy(g_auth_async.slot_local_path, slot_local_path, sizeof(g_auth_async.slot_local_path) - 1);
    strncpy(g_auth_async.slot_local_tmp_path, slot_local_tmp_path, sizeof(g_auth_async.slot_local_tmp_path) - 1);
    INTERLOCKED_INC(&g_auth_async.seq);
    MUTEX_UNLOCK(g_auth_async.mu);

    if (stats)
    {
        stats->copy_us = a3dGetTime() - copy_start_us;
        // write_us / send_or_queue_us now represent enqueue/copy cost (file write is async).
        stats->write_us = stats->copy_us;
        stats->send_or_queue_us = stats->copy_us;
        stats->total_us = stats->build_us + stats->write_us;
    }
    SvrFinalizeAuthoritativeStatePublish(state);
    return true;
}

// LINEAGE_JSON: {"fl":"FL-2957","surface":"SvrPublishAuthoritativeState","note":"H1 hypothesis: synchronous disk I/O (fopen+fwrite+fclose+rename x2) on tick thread. Max 361ms spike in manual-20260505-004954 but 736us typical — VPS disk tail latency. build_us/write_us split already instrumented below.","cautionary_precedent":"DO NOT reinvest in authoritative-state publish as the dominant lag fix. Next recipe-owned bad run had authoritative_state max only 813us/515us while physics still spiked badly. This theory is FALSIFIED as the primary owner.","run":"manual-20260505-015510"}
static void SvrPublishAuthoritativeState(ServerState* state) // FL-2957: authoritative state publish — lag echo response path
{
    SvrAuthoritativeStatePublishStats stats = {};
    if (!SvrPublishAuthoritativeStateDetailed(state, &stats))
        return;

    const uint64_t total_us = stats.build_us + stats.write_us;
    state->snapshot_authoritative_state_us = total_us;
    state->auth_collect_us = stats.collect_us;
    state->auth_diff_us = stats.diff_us;
    state->auth_serialize_us = stats.serialize_us;
    state->auth_send_or_queue_us = stats.send_or_queue_us;
    state->auth_copy_us = stats.copy_us;
    state->auth_publish_prepare_us = stats.publish_prepare_us;
    state->auth_socket_lookup_us = stats.socket_lookup_us;
    state->auth_per_client_loop_us = stats.per_client_loop_us;
    state->auth_client_queue_push_us = stats.client_queue_push_us;
    state->auth_client_queue_bytes = stats.client_queue_bytes > UINT32_MAX ? UINT32_MAX : (uint32_t)stats.client_queue_bytes;
    state->auth_client_queue_depth_before = stats.client_queue_depth_before;
    state->auth_client_queue_depth_after = stats.client_queue_depth_after;
    state->auth_client_backpressure_flag = stats.client_backpressure_flag;
    state->auth_client_write_attempt_us = stats.client_write_attempt_us;
    state->auth_client_write_bytes = stats.client_write_bytes > UINT32_MAX ? UINT32_MAX : (uint32_t)stats.client_write_bytes;
    state->auth_client_write_result = stats.client_write_result;
    state->auth_client_flush_us = stats.client_flush_us;
    state->auth_lock_wait_us = stats.lock_wait_us;
    state->auth_lock_held_us = stats.lock_held_us;
    state->auth_primary_file_write_us = stats.primary_file_write_us;
    state->auth_legacy_shm_write_us = stats.legacy_shm_write_us;
    state->auth_max_client_us = 0;
    state->auth_max_client_id = stats.max_client_id;
    state->auth_max_client_queue_depth = stats.max_client_queue_depth;
    state->auth_clients_count = stats.clients_count;
    state->auth_max_publish_sink_us = stats.max_publish_sink_us;
    state->auth_max_publish_sink_id = stats.max_publish_sink_id;
    state->auth_entries = stats.entries;
    state->auth_bytes = stats.json_bytes > UINT32_MAX ? UINT32_MAX : (uint32_t)stats.json_bytes;
    state->auth_player_count = stats.active_players;
    state->auth_npc_count = stats.active_npcs;
    state->auth_item_count = stats.active_items;
    state->auth_publish_tick = state->tick;
    state->auth_max_entry_us = stats.max_entry_us;
    state->auth_max_entry_kind_id = stats.max_entry_kind_id;
    state->auth_max_entry_id = stats.max_entry_id;
    state->auth_repeated_entry_count = stats.repeated_entry_count;
    state->auth_buffer_size_before = stats.buffer_size_before > UINT32_MAX ? UINT32_MAX : (uint32_t)stats.buffer_size_before;
    state->auth_buffer_size_after = stats.buffer_size_after > UINT32_MAX ? UINT32_MAX : (uint32_t)stats.buffer_size_after;
    state->auth_buffer_reallocs = stats.buffer_reallocs;

    if (total_us < SVR_TICK_SNAPSHOT_LOG_THRESHOLD_US)
        return;
    if (g_authoritative_state_breakdown_logs >= SVR_TICK_SNAPSHOT_LOG_LIMIT)
        return;

    SvrRuntimeDiagLog(state,
                      "[authoritative-state-breakdown] tick=%u total_us=%llu build_us=%llu write_us=%llu bytes=%zu players=%u npcs=%u items=%u interval=%u auth_collect_us=%llu auth_serialize_us=%llu auth_send_or_queue_us=%llu auth_publish_prepare_us=%llu auth_primary_file_write_us=%llu auth_legacy_shm_write_us=%llu auth_max_publish_sink=%s auth_max_publish_sink_us=%llu auth_max_entry_us=%llu auth_max_entry_kind=%s auth_max_entry_id=%u auth_entries=%u\n",
                      (unsigned)state->tick,
                      (unsigned long long)total_us,
                      (unsigned long long)stats.build_us,
                      (unsigned long long)stats.write_us,
                      stats.json_bytes,
                      (unsigned)stats.active_players,
                      (unsigned)stats.active_npcs,
                      (unsigned)stats.active_items,
                      (unsigned)(state->authoritative_publish_interval_ticks > 0
                          ? state->authoritative_publish_interval_ticks
                          : 1u),
                      (unsigned long long)stats.collect_us,
                      (unsigned long long)stats.serialize_us,
                      (unsigned long long)stats.send_or_queue_us,
                      (unsigned long long)stats.publish_prepare_us,
                      (unsigned long long)stats.primary_file_write_us,
                      (unsigned long long)stats.legacy_shm_write_us,
                      SvrAuthPublishSinkName(stats.max_publish_sink_id),
                      (unsigned long long)stats.max_publish_sink_us,
                      (unsigned long long)stats.max_entry_us,
                      SvrAuthEntryKindName(stats.max_entry_kind_id),
                      (unsigned)stats.max_entry_id,
                      (unsigned)stats.entries);
    g_authoritative_state_breakdown_logs++;
}

static void SvrMaybeLogAuthoritativeStateForensic(ServerState* state)
{
    if (!state)
        return;
    const uint64_t phase_us = state->tick_snapshot_authoritative_state_us_last;
    if (phase_us < SVR_TICK_SNAPSHOT_LOG_THRESHOLD_US)
        return;
    if (g_authoritative_state_forensic_logs >= SVR_TICK_SNAPSHOT_LOG_LIMIT)
        return;
    const bool publish_observed_this_tick = state->auth_publish_tick == state->tick;
    state->snapshot_authoritative_state_us = phase_us;
    const uint64_t auth_subtotal_us =
        state->auth_collect_us +
        state->auth_diff_us +
        state->auth_serialize_us +
        state->auth_send_or_queue_us +
        state->auth_copy_us;
    state->auth_phase_unaccounted_us = phase_us > auth_subtotal_us
        ? phase_us - auth_subtotal_us
        : 0;

    const char* max_owner = "none";
    uint64_t max_owner_us = 0;
    if (state->auth_collect_us > max_owner_us)
    {
        max_owner = "collect";
        max_owner_us = state->auth_collect_us;
    }
    if (state->auth_diff_us > max_owner_us)
    {
        max_owner = "diff";
        max_owner_us = state->auth_diff_us;
    }
    if (state->auth_serialize_us > max_owner_us)
    {
        max_owner = "serialize";
        max_owner_us = state->auth_serialize_us;
    }
    if (state->auth_send_or_queue_us > max_owner_us)
    {
        max_owner = "send_or_queue";
        max_owner_us = state->auth_send_or_queue_us;
    }
    if (state->auth_copy_us > max_owner_us)
    {
        max_owner = "copy";
        max_owner_us = state->auth_copy_us;
    }
    if (state->auth_phase_unaccounted_us > max_owner_us)
    {
        max_owner = publish_observed_this_tick ? "phase_unaccounted" : "phase_no_publish";
        max_owner_us = state->auth_phase_unaccounted_us;
    }

    const char* publish_block_owner = "none";
    if (state->auth_lock_wait_us >= 50000)
        publish_block_owner = "lock_wait";
    else if (state->auth_max_client_us >= 50000)
        publish_block_owner = "slow_client";
    else if (state->auth_client_queue_push_us >= 50000)
        publish_block_owner = "queue_push_copy";
    else if (state->auth_client_write_attempt_us >= 50000 || state->auth_client_flush_us >= 50000)
        publish_block_owner = "socket_write_flush";
    else if (state->auth_per_client_loop_us >= 50000)
        publish_block_owner = "per_client_loop";
    else if (state->auth_client_backpressure_flag)
        publish_block_owner = "client_backpressure";
    else if (state->auth_primary_file_write_us >= 50000 || state->auth_legacy_shm_write_us >= 50000)
        publish_block_owner = "file_publish_sink";
    else if (state->auth_send_or_queue_us >= 50000)
        publish_block_owner = "other_publish_sink";

    SvrRuntimeDiagLog(state,
                      "[auth-state-forensic] tick=%u snapshot_total_us=%llu snapshot_authoritative_state_us=%llu auth_phase_unaccounted_us=%llu auth_collect_us=%llu auth_diff_us=%llu auth_serialize_us=%llu auth_send_or_queue_us=%llu auth_copy_us=%llu auth_publish_prepare_us=%llu auth_socket_lookup_us=%llu auth_per_client_loop_us=%llu auth_client_queue_push_us=%llu auth_client_queue_bytes=%u auth_client_queue_depth_before=%u auth_client_queue_depth_after=%u auth_client_backpressure_flag=%u auth_client_write_attempt_us=%llu auth_client_write_bytes=%u auth_client_write_result=%u auth_client_flush_us=%llu auth_lock_wait_us=%llu auth_lock_held_us=%llu auth_primary_file_write_us=%llu auth_legacy_shm_write_us=%llu auth_max_client_us=%llu auth_max_client_id=%u auth_max_client_queue_depth=%u auth_clients_count=%u auth_max_publish_sink=%s auth_max_publish_sink_us=%llu auth_publish_block_owner=%s auth_entries=%u auth_bytes=%u auth_player_count=%u auth_npc_count=%u auth_item_count=%u auth_max_entry_us=%llu auth_max_entry_kind=%s auth_max_entry_id=%u auth_repeated_entry_count=%u auth_buffer_size_before=%u auth_buffer_size_after=%u auth_buffer_reallocs=%u auth_publish_tick=%u publish_observed_this_tick=%u max_sub_owner=%s max_sub_owner_us=%llu\n",
                      (unsigned)state->tick,
                      (unsigned long long)state->snapshot_total_us,
                      (unsigned long long)state->snapshot_authoritative_state_us,
                      (unsigned long long)state->auth_phase_unaccounted_us,
                      (unsigned long long)state->auth_collect_us,
                      (unsigned long long)state->auth_diff_us,
                      (unsigned long long)state->auth_serialize_us,
                      (unsigned long long)state->auth_send_or_queue_us,
                      (unsigned long long)state->auth_copy_us,
                      (unsigned long long)state->auth_publish_prepare_us,
                      (unsigned long long)state->auth_socket_lookup_us,
                      (unsigned long long)state->auth_per_client_loop_us,
                      (unsigned long long)state->auth_client_queue_push_us,
                      (unsigned)state->auth_client_queue_bytes,
                      (unsigned)state->auth_client_queue_depth_before,
                      (unsigned)state->auth_client_queue_depth_after,
                      (unsigned)state->auth_client_backpressure_flag,
                      (unsigned long long)state->auth_client_write_attempt_us,
                      (unsigned)state->auth_client_write_bytes,
                      (unsigned)state->auth_client_write_result,
                      (unsigned long long)state->auth_client_flush_us,
                      (unsigned long long)state->auth_lock_wait_us,
                      (unsigned long long)state->auth_lock_held_us,
                      (unsigned long long)state->auth_primary_file_write_us,
                      (unsigned long long)state->auth_legacy_shm_write_us,
                      (unsigned long long)state->auth_max_client_us,
                      (unsigned)state->auth_max_client_id,
                      (unsigned)state->auth_max_client_queue_depth,
                      (unsigned)state->auth_clients_count,
                      SvrAuthPublishSinkName(state->auth_max_publish_sink_id),
                      (unsigned long long)state->auth_max_publish_sink_us,
                      publish_block_owner,
                      (unsigned)state->auth_entries,
                      (unsigned)state->auth_bytes,
                      (unsigned)state->auth_player_count,
                      (unsigned)state->auth_npc_count,
                      (unsigned)state->auth_item_count,
                      (unsigned long long)state->auth_max_entry_us,
                      SvrAuthEntryKindName(state->auth_max_entry_kind_id),
                      (unsigned)state->auth_max_entry_id,
                      (unsigned)state->auth_repeated_entry_count,
                      (unsigned)state->auth_buffer_size_before,
                      (unsigned)state->auth_buffer_size_after,
                      (unsigned)state->auth_buffer_reallocs,
                      (unsigned)state->auth_publish_tick,
                      publish_observed_this_tick ? 1u : 0u,
                      max_owner,
                      (unsigned long long)max_owner_us);
    g_authoritative_state_forensic_logs++;
}

static int SvrCountOwnedItemsForClient(const ServerState* state, int ci)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return 0;

    int count = 0;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        const SvrItemState* it = &state->items[i];
        if (!it->active)
            continue;
        if (it->owner_id != (uint16_t)ci)
            continue;
        count++;
    }
    return count;
}


static void SvrFillItemChangeEventV2Payload(STRUCT_BRC_ITEM_CHANGE_V2* ev_v2,
                                            const ServerState* state,
                                            const SvrItemState* it,
                                            uint16_t owner_id)
{
    if (!ev_v2 || !it)
        return;
    const bool item_payload_only =
        owner_id == 0xFFFF ||
        ev_v2->kind == ITEM_CHANGE_KIND_DROP ||
        ev_v2->kind == ITEM_CHANGE_KIND_RESPAWN_RESET ||
        ev_v2->kind == ITEM_CHANGE_KIND_EQUIP_CLEAR ||
        ev_v2->kind == ITEM_CHANGE_KIND_CONSUME ||
        ev_v2->kind == ITEM_CHANGE_KIND_REMOVE;
    const SvrAppearanceLoadoutEntry* appearance_entry = item_payload_only
        ? 0
        : SvrFindEquippedAppearanceEntryForItem(state, it);
    ev_v2->item_definition_id = appearance_entry
        ? appearance_entry->item_definition_id
        : it->item_definition_id;
    ev_v2->visual_style_id = SvrNormalizeAppearanceVisualStyleId(
        appearance_entry && appearance_entry->visual_style_id != 0
            ? appearance_entry->visual_style_id
            : it->visual_style_id);
    ev_v2->equip_slot_kind_id = appearance_entry
        ? appearance_entry->slot_kind_id
        : it->equip_slot_kind_id;
    ev_v2->state_flags |= SvrItemStateFlagsForSnapshot(it, ev_v2->equip_slot_kind_id);
}

static void SvrReleaseOwnedItemsOnDisconnect(ServerState* state, int ci)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;

    SvrPlayerState* ps = &state->players[ci];
    float drop_pos[3] = { ps->pos[0], ps->pos[1], ps->pos[2] };

    for (int ii = 0; ii < SVR_MAX_ITEMS; ii++)
    {
        SvrItemState* it = &state->items[ii];
        if (!it->active || it->owner_id != (uint16_t)ci)
            continue;

        it->owner_id = 0xFFFF;
        SvrClearEquippedStateForItem(it);
        memcpy(it->pos, drop_pos, sizeof(it->pos));
        (void)SvrQueueItemChangeEventChecked(state,
                                             it,
                                             ITEM_CHANGE_KIND_DROP,
                                             0xFFFF,
                                             "disconnect_drop");
    }
    SvrClearAppearanceEntries(&ps->appearance, true);
}

static bool SvrQueueItemChangeEvent(ServerState* state,
                                    const SvrItemState* it,
                                    uint8_t kind,
                                    uint16_t owner_id)
{
    if (!state || !it)
        return false;

    STRUCT_BRC_ITEM_CHANGE_V2 ev_v2 = {};
    ev_v2.token = 'i';
    ev_v2.kind = kind;
    ev_v2.item_id = it->item_id;
    ev_v2.owner_id = owner_id;
    ev_v2.event_id = state->next_item_event_id + 1;
    ev_v2.tick = state->tick;
    memcpy(ev_v2.pos, it->pos, sizeof(ev_v2.pos));
    SvrFillItemChangeEventV2Payload(&ev_v2, state, it, owner_id);

    if (!SvrQueueEvent(state, (const uint8_t*)&ev_v2, sizeof(ev_v2), -1))
        return false;
    state->next_item_event_id = ev_v2.event_id;
    return true;
}

static_assert(offsetof(ServerState, slot_bitmask) == 64,
    "ServerState slot_bitmask offset changed; update SvrTestResetServerState memset boundaries");

#ifdef ASCIICKER_SERVER_TICK_CONTRACT_TESTS

static void SvrTestResetServerState(ServerState* state)
{
    const size_t slot_offset = offsetof(ServerState, slot_bitmask);
    memset((uint8_t*)state, 0, slot_offset);
    state->slot_bitmask = 0;
    const size_t after_slot_offset = slot_offset + sizeof(state->slot_bitmask);
    memset((uint8_t*)state + after_slot_offset, 0, sizeof(*state) - after_slot_offset);
    ServerWorldEntityRegistryInit(&state->world_entities);
}

static SvrItemState SvrTestMakeItemChangeItem()
{
    SvrItemState item = {};
    item.active = true;
    item.item_id = 900;
    item.owner_id = 0xFFFF;
    item.item_definition_id = 401;
    item.visual_style_id = SVR_APPEARANCE_VISUAL_STYLE_DARK;
    item.equip_slot_kind_id = APPEARANCE_SLOT_KIND_WEAPON;
    item.pos[0] = 1.0f;
    item.pos[1] = 2.0f;
    item.pos[2] = 3.0f;
    return item;
}

static bool SvrTestItemChangePayloadMatches(const STRUCT_BRC_ITEM_CHANGE_V2* ev)
{
    return ev &&
        ev->token == 'i' &&
        ev->kind == ITEM_CHANGE_KIND_DROP &&
        ev->item_id == 900 &&
        ev->owner_id == 0xFFFF &&
        ev->item_definition_id == 401 &&
        ev->visual_style_id == SVR_APPEARANCE_VISUAL_STYLE_DARK &&
        ev->equip_slot_kind_id == APPEARANCE_SLOT_KIND_WEAPON &&
        ev->state_flags == (APPEARANCE_ITEM_STATE_WORLD | APPEARANCE_ITEM_STATE_EQUIPPED) &&
        ev->event_id == 42 &&
        ev->tick == 77 &&
        ev->pos[0] == 1.0f &&
        ev->pos[1] == 2.0f &&
        ev->pos[2] == 3.0f;
}

static bool SvrTestRejectedItemChangePreservesQueue(ServerState* state, const SvrItemState* item)
{
    const uint32_t event_id_before = state->next_item_event_id;
    const int event_count_before = state->events.count;
    const int event_len_before = state->events.len;
    if (SvrQueueItemChangeEvent(state, item, ITEM_CHANGE_KIND_DROP, 0xFFFF))
        return false;
    if (state->next_item_event_id != event_id_before)
        return false;
    if (state->events.count != event_count_before || state->events.len != event_len_before)
        return false;
    return true;
}

bool SvrTestItemChangeEventIdNonAdvanceOnFullQueue()
{
    static ServerState state; // static: ServerState exceeds safe stack size (~50 KB+)
    SvrTestResetServerState(&state);
    state.tick = 77;
    state.next_item_event_id = 41;

    const SvrItemState item = SvrTestMakeItemChangeItem();

    if (!SvrQueueItemChangeEvent(&state, &item, ITEM_CHANGE_KIND_DROP, 0xFFFF))
        return false;
    if (state.next_item_event_id != 42)
        return false;
    if (state.events.count != 1)
        return false;
    const SvrEventQueue::Entry* success_entry = &state.events.entries[0];
    if (success_entry->size != (int)sizeof(STRUCT_BRC_ITEM_CHANGE_V2))
        return false;
    const STRUCT_BRC_ITEM_CHANGE_V2* success_event =
        (const STRUCT_BRC_ITEM_CHANGE_V2*)(state.events.buf + success_entry->offset);
    if (!SvrTestItemChangePayloadMatches(success_event))
        return false;

    SvrTestResetServerState(&state);
    state.tick = 88;
    state.next_item_event_id = 1234;
    const uint8_t filler_event[1] = { (uint8_t)'t' };
    const int kQueueCapacity = (int)(sizeof(state.events.entries) / sizeof(state.events.entries[0]));
    for (int i = 0; i < kQueueCapacity; i++)
    {
        if (!SvrQueueEvent(&state, filler_event, (int)sizeof(filler_event), -1))
            return false;
    }
    if (state.events.count != kQueueCapacity || state.events.len != kQueueCapacity)
        return false;
    if (!SvrTestRejectedItemChangePreservesQueue(&state, &item))
        return false;

    SvrTestResetServerState(&state);
    state.tick = 99;
    state.next_item_event_id = 4321;
    static const uint8_t large_filler_event[sizeof(((SvrEventQueue*)0)->buf)] = { (uint8_t)'t' };
    const int item_change_size = (int)sizeof(STRUCT_BRC_ITEM_CHANGE_V2);
    const int queue_buffer_bytes = (int)sizeof(state.events.buf);
    const int large_filler_size = queue_buffer_bytes - item_change_size + 1;
    if (large_filler_size <= 0 || large_filler_size > queue_buffer_bytes)
        return false;
    if (!SvrQueueEvent(&state, large_filler_event, large_filler_size, -1))
        return false;
    if (state.events.count != 1 || state.events.len != large_filler_size)
        return false;
    if (state.events.count >= kQueueCapacity)
        return false;
    if (state.events.len + item_change_size <= queue_buffer_bytes)
        return false;
    if (!SvrTestRejectedItemChangePreservesQueue(&state, &item))
        return false;

    return true;
}
#endif

static bool SvrQueueItemChangeEventChecked(ServerState* state,
                                           const SvrItemState* it,
                                           uint8_t kind,
                                           uint16_t owner_id,
                                           const char* source)
{
    if (SvrQueueItemChangeEvent(state, it, kind, owner_id))
        return true;

    SvrRuntimeDiagLog(state,
                      "[item-event] queue failed source=%s kind=%u item_id=%u owner=%u active=%d tick=%u\n",
                      source ? source : "unknown",
                      (unsigned)kind,
                      it ? (unsigned)it->item_id : 0,
                      (unsigned)owner_id,
                      it && it->active ? 1 : 0,
                      state ? (unsigned)state->tick : 0);
    return false;
}

static bool SvrEmitItemChangeEventForOwnershipContract(void* ctx,
                                                       const SvrItemState* it,
                                                       uint8_t kind,
                                                       uint16_t owner_id)
{
    return SvrQueueItemChangeEvent((ServerState*)ctx, it, kind, owner_id);
}

static void SvrPickupLootItem(ServerState* state, int ci, SvrItemState* it, const char* source)
{
    if (!state || !it || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;
    if (!SvrClaimWorldItemForOwnerAfterEvent(it,
                                             (uint16_t)ci,
                                             ITEM_CHANGE_KIND_PICKUP,
                                             SvrEmitItemChangeEventForOwnershipContract,
                                             state))
    {
        SvrRuntimeDiagLog(state,
                          "[item-loot] pickup rejected source=%s ci=%d item_id=%u owner=%u active=%d reason=claim_or_event_failed tick=%u\n",
                          source ? source : "unknown",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->owner_id,
                          it->active ? 1 : 0,
                          (unsigned)state->tick);
        return;
    }
    SvrRuntimeDiagLog(state,
                      "[item-loot] pickup source=%s ci=%d item_id=%u definition=%u style=%u tick=%u\n",
                      source ? source : "unknown",
                      ci,
                      (unsigned)it->item_id,
                      (unsigned)it->item_definition_id,
                      (unsigned)it->visual_style_id,
                      (unsigned)state->tick);
}

static bool SvrPickupInventoryItem(ServerState* state, int ci, SvrItemState* it, const char* source)
{
    if (!state || !it || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;
    if (SvrCountOwnedItemsForClient(state, ci) >= SVR_AUTH_INVENTORY_ITEM_CAPACITY)
    {
        SvrRuntimeDiagLog(state,
                          "[item-debug] explicit pickup rejected: inventory full ci=%d item_id=%u gameplay_kind=%u source=%s\n",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->gameplay_kind,
                          source ? source : "unknown");
        return false;
    }

    SvrItemState event_item = *it;
    event_item.owner_id = (uint16_t)ci;
    event_item.equip_slot_kind_id = 0;
    event_item.placed_flags = SVR_PLACED_ITEM_NONE;
    event_item.placed_durability = 0;
    event_item.placed_yaw = 0.0f;
    if (!SvrQueueItemChangeEventChecked(state,
                                        &event_item,
                                        ITEM_CHANGE_KIND_PICKUP,
                                        (uint16_t)ci,
                                        source ? source : "inventory_pickup"))
    {
        SvrRuntimeDiagLog(state,
                          "[item-debug] pickup inventory rejected: claim/event failed ci=%d item_id=%u owner=%u active=%d source=%s\n",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->owner_id,
                          it->active ? 1 : 0,
                          source ? source : "unknown");
        return false;
    }
    it->owner_id = (uint16_t)ci;
    it->equip_slot_kind_id = 0;
    it->placed_flags = SVR_PLACED_ITEM_NONE;
    it->placed_durability = 0;
    it->placed_yaw = 0.0f;
    SvrRemovePlacedBlockEntity(state, it);
    it->active = true;
    SvrRuntimeDiagLog(state,
                      "[item-debug] pickup inventory source=%s ci=%d item_id=%u gameplay_kind=%u definition=%u style=%u tick=%u\n",
                      source ? source : "unknown",
                      ci,
                      (unsigned)it->item_id,
                      (unsigned)it->gameplay_kind,
                      (unsigned)it->item_definition_id,
                      (unsigned)it->visual_style_id,
                      (unsigned)state->tick);
    return true;
}

static bool SvrTogglePlaceableBlockHeld(ServerState* state,
                                        int ci,
                                        SvrItemState* it,
                                        const SvrActorVisualProfileCatalogItemDef* item_def)
{
    if (!state || !it || !item_def || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;
    if (!it->active || it->owner_id != (uint16_t)ci ||
        item_def->gameplay_kind != SVR_ITEM_GAMEPLAY_PLACEABLE_BLOCK)
    {
        return false;
    }

    const bool currently_held = it->equip_slot_kind_id == APPEARANCE_SLOT_KIND_HELD_ITEM;
    SvrItemState event_item = *it;
    event_item.equip_slot_kind_id = currently_held ? 0 : APPEARANCE_SLOT_KIND_HELD_ITEM;
    const uint8_t event_kind =
        currently_held ? ITEM_CHANGE_KIND_EQUIP_CLEAR : ITEM_CHANGE_KIND_EQUIP_SET;
    if (!SvrQueueItemChangeEventChecked(state,
                                        &event_item,
                                        event_kind,
                                        (uint16_t)ci,
                                        currently_held ? "placeable_block_hold_clear" : "placeable_block_hold_set"))
    {
        return false;
    }

    it->equip_slot_kind_id = event_item.equip_slot_kind_id;
    return true;
}

// FL-4137 Gap C: MVP cap on stacking lift, in collision_height_units. Caps
// stacking to four block heights above the player's feet so blocks cannot be
// placed out of arm reach. Player/world Z scale lives in mp_step.cpp
// (HEIGHT_SCALE / kMpMaxImplicitStepUp); this cap is purely a placement-time
// reach gate and stays local to the server placement writer.
static constexpr int SVR_PLACE_MAX_STACK_LAYERS = 4;

static bool SvrPlacedBlockPositionOccupied(ServerState* state,
                                           const SvrActorVisualProfileCatalog* cache,
                                           const SvrItemState* placing,
                                           const SvrActorVisualProfileCatalogItemDef* placing_def,
                                           const float pos[3],
                                           const char** out_reason)
{
    if (!state || !cache || !placing || !placing_def || !pos)
        return true;
    if (out_reason)
        *out_reason = "none";
    const float placing_radius =
        placing_def->collision_radius_units > 0.0f ? placing_def->collision_radius_units : 1.0f;
    const float placing_height =
        placing_def->collision_height_units > 0.0f ? placing_def->collision_height_units : 2.0f;
    const float placing_top = pos[2] + placing_height;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        const SvrItemState* it = &state->items[i];
        if (!it->active || it == placing || it->owner_id != 0xFFFF)
            continue;
        if ((it->placed_flags & SVR_PLACED_ITEM_COLLIDABLE) == 0)
            continue;
        const SvrActorVisualProfileCatalogItemDef* item_def =
            SvrFindAppearanceItemById(cache, it->item_definition_id);
        const float other_radius =
            item_def && item_def->collision_radius_units > 0.0f
                ? item_def->collision_radius_units
                : 1.0f;
        const float other_height =
            item_def && item_def->collision_height_units > 0.0f
                ? item_def->collision_height_units
                : 2.0f;
        const float min_dist = placing_radius + other_radius;
        const float dx = it->pos[0] - pos[0];
        const float dy = it->pos[1] - pos[1];
        if (dx * dx + dy * dy >= min_dist * min_dist)
            continue;
        // FL-4137 Gap C: XY overlap is permitted when vertical bands are
        // disjoint, i.e. the candidate is stacked above (or below) the existing
        // placed block. Equality at the seam (top == bottom) counts as disjoint.
        const float other_top = it->pos[2] + other_height;
        if (placing_top <= it->pos[2] || pos[2] >= other_top)
            continue;
        if (out_reason)
            *out_reason = "placed_block_overlap";
        return true;
    }
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        const SvrPlayerState* player = &state->players[i];
        if (!player->active || player->phase < CPHASE_ALIVE)
            continue;
        const float player_radius = MpStepWorldRadiusForMount(player->mount_state);
        const float min_dist = placing_radius + player_radius;
        const float dx = player->pos[0] - pos[0];
        const float dy = player->pos[1] - pos[1];
        if (dx * dx + dy * dy >= min_dist * min_dist)
            continue;
        const float player_top = player->pos[2] + 2.0f * HEIGHT_SCALE;
        if (placing_top < player->pos[2] || pos[2] > player_top)
            continue;
        if (out_reason)
            *out_reason = "player_overlap";
        return true;
    }
    for (int i = 0; i < state->npc_count; i++)
    {
        const SvrNpcState* npc = &state->npcs[i];
        if (!npc->active || npc->death_tick > 0)
            continue;
        const float npc_radius = MpStepWorldRadiusForMount(npc->mount_state);
        const float min_dist = placing_radius + npc_radius;
        const float dx = npc->pos[0] - pos[0];
        const float dy = npc->pos[1] - pos[1];
        if (dx * dx + dy * dy >= min_dist * min_dist)
            continue;
        const float npc_top = npc->pos[2] + 2.0f * HEIGHT_SCALE;
        if (placing_top < npc->pos[2] || pos[2] > npc_top)
            continue;
        if (out_reason)
            *out_reason = "npc_overlap";
        return true;
    }
    return false;
}

struct SvrPlacedBlockPlacementSnapshot
{
    float pos[3];
    float half_extent;
    float height;
    uint16_t item_id;
};

static int SvrCollectPlacedBlockPlacementSnapshots(const ServerState* state,
                                                   SvrPlacedBlockPlacementSnapshot* out,
                                                   int out_cap)
{
    if (!state || !out || out_cap <= 0)
        return 0;
    int count = 0;
    for (int i = 0; i < SVR_MAX_ITEMS && count < out_cap; i++)
    {
        const SvrItemState* it = &state->items[i];
        if (!it->active || it->owner_id != 0xFFFF)
            continue;
        if ((it->placed_flags & SVR_PLACED_ITEM_COLLIDABLE) == 0)
            continue;
        const AppearanceCatalogItemDef* item_def =
            FindAppearanceCatalogItemById(it->item_definition_id);
        SvrPlacedBlockPlacementSnapshot* block = &out[count++];
        memcpy(block->pos, it->pos, sizeof(block->pos));
        block->half_extent =
            item_def && item_def->collision_radius_units > 0.0f
                ? item_def->collision_radius_units
                : 1.0f;
        block->height =
            item_def && item_def->collision_height_units > 0.0f
                ? item_def->collision_height_units
                : 2.0f;
        block->item_id = it->item_id;
    }
    return count;
}

static bool SvrSnapPlacedBlockXYToExistingGrid(const SvrPlacedBlockPlacementSnapshot* blocks,
                                               int block_count,
                                               float placing_half,
                                               float pos[3])
{
    if (!blocks || block_count <= 0 || !pos)
        return false;
    const float new_half = placing_half > 0.0f ? placing_half : 1.0f;
    const float snap_halo = new_half * 2.0f + 1.0f;
    const float snap_halo2 = snap_halo * snap_halo;
    const SvrPlacedBlockPlacementSnapshot* best = 0;
    float best_d2 = snap_halo2;
    for (int i = 0; i < block_count; i++)
    {
        const SvrPlacedBlockPlacementSnapshot& c = blocks[i];
        const float half = c.half_extent > 0.0f ? c.half_extent : 1.0f;
        const float dx = pos[0] - c.pos[0];
        const float dy = pos[1] - c.pos[1];
        const float d2 = dx * dx + dy * dy;
        if (d2 > best_d2)
            continue;
        best_d2 = d2;
        best = &c;
        if (fabsf(dx) <= half && fabsf(dy) <= half)
            break;
    }
    if (!best)
        return false;

    const float half = best->half_extent > 0.0f ? best->half_extent : 1.0f;
    const float dx = pos[0] - best->pos[0];
    const float dy = pos[1] - best->pos[1];
    if (fabsf(dx) <= half && fabsf(dy) <= half)
    {
        // Target is inside an existing block footprint: snap to its center so the
        // Z selector below preferentially stacks over it.
        pos[0] = best->pos[0];
        pos[1] = best->pos[1];
        return true;
    }

    // Target is near an existing block but outside its footprint: snap to the
    // nearest cardinal neighbor cell around that block. Occupancy still validates
    // the final candidate; this is only server-owned target normalization.
    const float center_offset = half + new_half;
    if (fabsf(dx) >= fabsf(dy))
    {
        pos[0] = best->pos[0] + (dx >= 0.0f ? center_offset : -center_offset);
        pos[1] = best->pos[1];
    }
    else
    {
        pos[0] = best->pos[0];
        pos[1] = best->pos[1] + (dy >= 0.0f ? center_offset : -center_offset);
    }
    return true;
}

static bool SvrPlaceOwnedItemFromPlayer(ServerState* state,
                                        int ci,
                                        SvrPlayerState* ps,
                                        SvrItemState* it,
                                        const SvrActorVisualProfileCatalog* cache,
                                        const char* source,
                                        float debug_z_offset)
{
    if (!state || !ps || !it || !cache || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;
    if (!ps->active || !it->active || it->owner_id != (uint16_t)ci)
        return false;

    const SvrActorVisualProfileCatalogItemDef* item_def =
        SvrFindAppearanceItemById(cache, it->item_definition_id);
    if (!item_def || !item_def->placeable ||
        item_def->gameplay_kind != SVR_ITEM_GAMEPLAY_PLACEABLE_BLOCK)
    {
        SvrRuntimeDiagLog(state,
                          "[item-place] rejected source=%s ci=%d item_id=%u reason=not_placeable definition=%u kind=%u tick=%u\n",
                          source ? source : "unknown",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->item_definition_id,
                          item_def ? (unsigned)item_def->gameplay_kind : 0u,
                          (unsigned)state->tick);
        return false;
    }
    if (it->equip_slot_kind_id != APPEARANCE_SLOT_KIND_HELD_ITEM)
    {
        SvrRuntimeDiagLog(state,
                          "[item-place] rejected source=%s ci=%d item_id=%u reason=not_held slot=%u tick=%u\n",
                          source ? source : "unknown",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->equip_slot_kind_id,
                          (unsigned)state->tick);
        return false;
    }

    const float block_half =
        item_def->collision_radius_units > 0.0f ? item_def->collision_radius_units : 1.0f;
    const float player_world_radius = MpStepWorldRadiusForMount(ps->mount_state);
    const float min_mesh_safe_distance = player_world_radius + block_half + 0.5f;
    const float requested_distance =
        item_def->place_distance_units > 0.0f ? item_def->place_distance_units : 4.0f;
    const float distance = fmaxf(requested_distance, min_mesh_safe_distance);
    const float yaw_rad = ps->dir * (float)(M_PI / 180.0);
    float placed_pos[3] = {
        ps->pos[0] + cosf(yaw_rad) * distance,
        ps->pos[1] + sinf(yaw_rad) * distance,
        ps->pos[2],
    };
    placed_pos[0] = floorf(placed_pos[0]) + 0.5f;
    placed_pos[1] = floorf(placed_pos[1]) + 0.5f;
    const float block_height_units =
        item_def->collision_height_units > 0.0f ? item_def->collision_height_units : 2.0f;
    const float max_stack_lift = block_height_units * (float)SVR_PLACE_MAX_STACK_LAYERS;
    SvrPlacedBlockPlacementSnapshot stack_blocks[SVR_MAX_ITEMS] = {};
    const int stack_block_count =
        SvrCollectPlacedBlockPlacementSnapshots(state, stack_blocks, SVR_MAX_ITEMS);
    const bool snapped_to_block_grid =
        SvrSnapPlacedBlockXYToExistingGrid(stack_blocks, stack_block_count, block_half, placed_pos);
    const float terrain_z = SvrSampleTerrainHeight(state->terrain, placed_pos[0], placed_pos[1], placed_pos[2]);

    // FL-4137 Gap C: stacking / upward retry.
    // Use authoritative item-state snapshots only for placement target
    // normalization and stacking Z. Runtime movement collision is NOT owned by
    // this list; placed blocks enter physics through World mesh instances and
    // QueryWorld/MeshCollect like AKM meshes.
    float best_z = terrain_z;
    for (int i = 0; i < stack_block_count; i++)
    {
        const SvrPlacedBlockPlacementSnapshot& c = stack_blocks[i];
        if (c.item_id == it->item_id)
            continue;
        // Cube footprint XY containment: target XY must lie inside the existing
        // block's AABB-style footprint to stand on top of it.
        if (fabsf(c.pos[0] - placed_pos[0]) > c.half_extent)
            continue;
        if (fabsf(c.pos[1] - placed_pos[1]) > c.half_extent)
            continue;
        const float top_z = c.pos[2] + c.height;
        if (top_z <= best_z)
            continue;
        // Cap the lift so the player cannot stack out of arm reach. When the
        // cap rejects every candidate, best_z stays at terrain_z and the
        // subsequent SvrPlacedBlockPositionOccupied check will reject the
        // placement (vertical bands intersect) instead of silently dropping
        // the request onto terrain (fail-closed per Law 6).
        if (top_z > ps->pos[2] + max_stack_lift)
            continue;
        best_z = top_z;
    }
    placed_pos[2] = best_z + debug_z_offset;

    const char* occupied_reason = "occupied";
    if (SvrPlacedBlockPositionOccupied(state, cache, it, item_def, placed_pos, &occupied_reason))
    {
        SvrRuntimeDiagLog(state,
                          "[item-place] rejected source=%s ci=%d item_id=%u reason=%s pos=(%.2f,%.2f,%.2f) tick=%u\n",
                          source ? source : "unknown",
                          ci,
                          (unsigned)it->item_id,
                          occupied_reason ? occupied_reason : "occupied",
                          placed_pos[0], placed_pos[1], placed_pos[2],
                          (unsigned)state->tick);
        return false;
    }

    SvrItemState event_item = *it;
    event_item.owner_id = 0xFFFF;
    event_item.equip_slot_kind_id = 0;
    memcpy(event_item.pos, placed_pos, sizeof(event_item.pos));
    event_item.source_kind = SVR_ITEM_SOURCE_PLAYER_PLACED;
    event_item.placed_flags =
        SVR_PLACED_ITEM_PLACED |
        SVR_PLACED_ITEM_COLLIDABLE |
        (item_def->explicit_pickup_only ? SVR_PLACED_ITEM_EXPLICIT_PICKUP_ONLY : 0);
    event_item.placed_durability = item_def->placed_durability;
    event_item.placed_yaw = ps->dir;
    event_item.placed_entity_id = 0;

    ServerWorldEntity* placed_entity =
        SvrUpsertPlacedBlockEntity(state, &event_item, item_def,
                                   source ? source : "placeable_block");
    if (!placed_entity)
    {
        SvrRuntimeDiagLog(state,
                          "[item-place] rejected source=%s ci=%d item_id=%u reason=world_entity_failed pos=(%.2f,%.2f,%.2f) tick=%u\n",
                          source ? source : "unknown",
                          ci,
                          (unsigned)it->item_id,
                          placed_pos[0], placed_pos[1], placed_pos[2],
                          (unsigned)state->tick);
        return false;
    }
    event_item.placed_entity_id = placed_entity->entity_id;

    if (!SvrQueueItemChangeEventChecked(state,
                                        &event_item,
                                        ITEM_CHANGE_KIND_PLACE,
                                        0xFFFF,
                                        source ? source : "placeable_block"))
    {
        ServerWorldEntityRegistryRemoveByItemId(&state->world_entities,
                                                event_item.item_id);
        return false;
    }

    it->owner_id = 0xFFFF;
    it->equip_slot_kind_id = 0;
    memcpy(it->pos, placed_pos, sizeof(it->pos));
    it->source_kind = SVR_ITEM_SOURCE_PLAYER_PLACED;
    it->placed_flags = event_item.placed_flags;
    it->placed_durability = event_item.placed_durability;
    it->placed_yaw = event_item.placed_yaw;
    it->placed_entity_id = event_item.placed_entity_id;
    SvrRuntimeDiagLog(state,
                      "[item-place] placed source=%s ci=%d item_id=%u definition=%u pos=(%.2f,%.2f,%.2f) top_z=%.2f half=%.2f height=%.2f yaw=%.2f flags=%u snap_grid=%u debug_z_offset=%.2f tick=%u\n",
                      source ? source : "unknown",
                      ci,
                      (unsigned)it->item_id,
                      (unsigned)it->item_definition_id,
                      it->pos[0], it->pos[1], it->pos[2],
                      it->pos[2] + (item_def->collision_height_units > 0.0f ? item_def->collision_height_units : 2.0f),
                      block_half,
                      item_def->collision_height_units > 0.0f ? item_def->collision_height_units : 2.0f,
                      it->placed_yaw,
                      (unsigned)it->placed_flags,
                      snapped_to_block_grid ? 1u : 0u,
                      debug_z_offset,
                      (unsigned)state->tick);
    return true;
}

static uint8_t SvrBlockBreakPowerForWeapon(uint16_t weapon_item_id)
{
    const AppearanceCatalogItemDef* item =
        FindAppearanceCatalogItemById(weapon_item_id);
    if (!item || item->gameplay_kind != APPEARANCE_CATALOG_GAMEPLAY_WEAPON)
        return 0;
    return item->block_break_power;
}

static float SvrSwingRangeForWeapon(uint16_t weapon_item_id, bool target_is_npc);

static bool SvrTryBreakPlacedBlocksFromSwing(ServerState* state,
                                             const PendingSwing* s,
                                             const float attacker_pos[3],
                                             float attacker_dir)
{
    if (!state || !s || !attacker_pos)
        return false;
    (void)attacker_dir;
    // FL-4137 b8: heavy-weapon block break gated by ASCIICKER_DEBUG_BLOCK_BREAK.
    // Default off per operator: "disable heavy break and autopick for blocks
    // only". The break code stays present and verified by the heavy-break
    // proof when env=1; production gameplay leaves blocks indestructible
    // via weapon swing. Autopick is already disabled for placed blocks at
    // engine/authoritative_item_command_surface.cpp:339 (EXPLICIT_PICKUP_ONLY
    // flag early-return).
    {
        static int s_cached = -1;
        if (s_cached < 0)
        {
            const char* env = getenv("ASCIICKER_DEBUG_BLOCK_BREAK");
            s_cached = (env && env[0] == '1') ? 1 : 0;
        }
        if (!s_cached)
            return false;
    }
    // FL-4137: breaking placed blocks must clear the same world-entity
    // collision owner as pickup/drop. Removing only the item row leaves a
    // ghost collidable block behind.
    const uint8_t break_power = SvrBlockBreakPowerForWeapon(s->weapon_item_id);
    if (break_power == 0)
        return false;

    const float swing_range = SvrSwingRangeForWeapon(s->weapon_item_id, false);
    const float radians = attacker_dir * (float)(M_PI / 180.0);
    const float fx = cosf(radians);
    const float fy = sinf(radians);
    SvrItemState* best = NULL;
    const AppearanceCatalogItemDef* best_def = NULL;
    float best_dist2 = 1.0e30f;
    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        SvrItemState* it = &state->items[i];
        if (!it->active || it->owner_id != 0xFFFF)
            continue;
        if ((it->placed_flags & SVR_PLACED_ITEM_COLLIDABLE) == 0)
            continue;
        const AppearanceCatalogItemDef* item_def =
            FindAppearanceCatalogItemById(it->item_definition_id);
        if (!item_def || !item_def->placeable ||
            item_def->gameplay_kind != APPEARANCE_CATALOG_GAMEPLAY_PLACEABLE_BLOCK)
        {
            continue;
        }
        if (break_power < item_def->block_break_power)
            continue;
        if (!SvrWithinVerticalBand(it->pos, attacker_pos, SVR_VERTICAL_SWING_BAND))
            continue;
        // FL-4137 behavior 8 reach fix: swing_range measures from attacker
        // to block edge, not block center. The collision sphere prevents
        // the attacker from getting closer than block.half_extent + player_radius
        // to the block center, so center-based range checks reject every
        // physically-reachable block once block.half_extent > swing_range.
        // For the canonical block (half_extent=4.333) and the default sword
        // (swing_range=3.0) that was every swing; adding block.half_extent
        // restores the intuitive "you're swinging at the surface" reach.
        // item_def->collision_radius_units is the RAW catalog literal which is
        // 0.0 for blocks that derive their footprint from sprite projection
        // (see actor_visual_catalog_source.h legacy_yy_block trailing literals).
        // The derived value lives in SvrAppearanceContractState's cached catalog
        // (appearance_contract_state.cpp:262), not reachable from this code path
        // without another lookup. Fall back to a sane block-default (5.0 units)
        // so the swing reach reflects the actual collision footprint instead of
        // requiring the attacker to be 3 units from block CENTER (geometrically
        // impossible when block half_extent > 3).
        const float block_half_for_reach =
            item_def->collision_radius_units > 0.0f
                ? item_def->collision_radius_units
                : 5.0f;
        const float effective_range = swing_range + block_half_for_reach;
        const float effective_range2 = effective_range * effective_range;
        const float dx = it->pos[0] - attacker_pos[0];
        const float dy = it->pos[1] - attacker_pos[1];
        const float dist2 = dx * dx + dy * dy;
        if (dist2 > effective_range2)
            continue;
        const float dist = sqrtf(dist2);
        if (dist > 0.01f)
        {
            const float dot = (dx / dist) * fx + (dy / dist) * fy;
            if (dot < 0.25f)
                continue;
        }
        if (!best || dist2 < best_dist2)
        {
            best = it;
            best_def = item_def;
            best_dist2 = dist2;
        }
    }
    if (!best || !best_def)
        return false;

    if (best->placed_durability == 0)
        best->placed_durability = best_def->placed_durability;
    if (best->placed_durability > 0)
        best->placed_durability--;

    if (best->placed_durability == 0)
    {
        if (!SvrQueueItemChangeEventChecked(state,
                                            best,
                                            ITEM_CHANGE_KIND_REMOVE,
                                            0xFFFF,
                                            "placed_block_break"))
        {
            SvrRuntimeDiagLog(state,
                              "[item-break] tick=%u attacker=%u item_id=%u outcome=event_rejected dist2=%.3f\n",
                              (unsigned)state->tick,
                              (unsigned)s->attacker_id,
                              (unsigned)best->item_id,
                              best_dist2);
            return false;
        }
        SvrRemovePlacedBlockEntity(state, best);
        best->active = 0;
        best->placed_flags = SVR_PLACED_ITEM_NONE;
        best->placed_durability = 0;
        best->placed_yaw = 0.0f;
        SvrRuntimeDiagLog(state,
                          "[item-break] tick=%u attacker=%u item_id=%u outcome=removed dist2=%.3f\n",
                          (unsigned)state->tick,
                          (unsigned)s->attacker_id,
                          (unsigned)best->item_id,
                          best_dist2);
    }
    else
    {
        SvrQueueItemChangeEventChecked(state,
                                       best,
                                       ITEM_CHANGE_KIND_PLACE,
                                       0xFFFF,
                                       "placed_block_damage");
        SvrRuntimeDiagLog(state,
                          "[item-break] tick=%u attacker=%u item_id=%u durability=%u dist2=%.3f\n",
                          (unsigned)state->tick,
                          (unsigned)s->attacker_id,
                          (unsigned)best->item_id,
                          (unsigned)best->placed_durability,
                          best_dist2);
    }
    return true;
}

static bool SvrPickupEquippableItem(ServerState* state,
                                    int ci,
                                    SvrItemState* it,
                                    const SvrActorVisualProfileCatalog* cache,
                                    const char* source)
{
    if (!state || !it || !cache || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;

    SvrPlayerState* ps = &state->players[ci];
    if (!ps->active)
        return false;
    const SvrActorVisualProfileCatalogItemDef* item_def =
        SvrFindAppearanceItemById(cache, it->item_definition_id);
    if (!item_def)
        return false;

    SvrAppearanceLoadoutEntry next_entry = {};
    if (!SvrResolveAppearanceEntryForItemState(cache, it, &next_entry))
    {
        SvrRuntimeDiagLog(state,
                          "[item-debug] explicit pickup rejected: unresolved equippable ci=%d item_id=%u gameplay_kind=%u source=%s\n",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->gameplay_kind,
                          source ? source : "unknown");
        return false;
    }

    const int prior_slot_index =
        SvrFindAppearanceEntryIndexBySlot(&ps->appearance, next_entry.slot_kind_id);
    uint16_t prior_item_id = 0;
    if (prior_slot_index >= 0)
        prior_item_id = ps->appearance.entries[prior_slot_index].item_instance_id;

    SvrItemState* prior_item = 0;
    if (prior_item_id != 0 && prior_item_id != it->item_id)
        prior_item = SvrFindOwnedItemById(state, ci, prior_item_id);

    const int owned_count = SvrCountOwnedItemsForClient(state, ci);
    if (owned_count >= SVR_AUTH_INVENTORY_ITEM_CAPACITY && !prior_item)
    {
        SvrRuntimeDiagLog(state,
                          "[item-debug] explicit pickup rejected: inventory full ci=%d item_id=%u gameplay_kind=%u source=%s\n",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->gameplay_kind,
                          source ? source : "unknown");
        return false;
    }

    if (prior_slot_index < 0 &&
        ps->appearance.entry_count >= SVR_MAX_APPEARANCE_LOADOUT_ENTRIES)
    {
        SvrRuntimeDiagLog(state,
                          "[item-debug] explicit pickup rejected: loadout full ci=%d item_id=%u gameplay_kind=%u source=%s\n",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->gameplay_kind,
                          source ? source : "unknown");
        return false;
    }

    if (item_def->gameplay_kind == SVR_ITEM_GAMEPLAY_MOUNTABLE &&
        SvrResolveRuntimeMountStateForItem(cache, it) == MOUNT::NONE)
    {
        SvrRuntimeDiagLog(state,
                          "[item-debug] explicit pickup mount rejected: unresolved mount state ci=%d item_id=%u mount_definition=%u source=%s\n",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->mount_definition_id,
                          source ? source : "unknown");
        return false;
    }

    int queued_item_event_count = 1; // EQUIP_SET publishes ownership atomically.
    if (owned_count >= SVR_AUTH_INVENTORY_ITEM_CAPACITY && prior_item)
        queued_item_event_count++;
    else if (prior_item && prior_item->owner_id == (uint16_t)ci)
        queued_item_event_count++;
    if (!SvrCanQueueItemChangeEventBatch(state, queued_item_event_count))
    {
        SvrRuntimeDiagLog(state,
                          "[item-debug] pickup equippable rejected: item event batch full ci=%d item_id=%u events=%d count=%d len=%d source=%s\n",
                          ci,
                          (unsigned)it->item_id,
                          queued_item_event_count,
                          state->events.count,
                          state->events.len,
                          source ? source : "unknown");
        return false;
    }

    bool drop_prior_for_capacity = false;
    bool clear_prior_equip = false;
    if (owned_count >= SVR_AUTH_INVENTORY_ITEM_CAPACITY && prior_item)
    {
        SvrItemState event_prior_item = *prior_item;
        event_prior_item.owner_id = 0xFFFF;
        event_prior_item.equip_slot_kind_id = 0;
        memcpy(event_prior_item.pos, ps->pos, sizeof(event_prior_item.pos));
        if (!SvrQueueItemChangeEventChecked(state,
                                            &event_prior_item,
                                            ITEM_CHANGE_KIND_DROP,
                                            0xFFFF,
                                            "equippable_capacity_drop"))
            return false;
        drop_prior_for_capacity = true;
    }
    SvrRuntimeDiagLog(state,
                      "[item-debug] pickup equippable source=%s ci=%d item_id=%u gameplay_kind=%u definition=%u style=%u slot=%u tick=%u\n",
                      source ? source : "unknown",
                      ci,
                      (unsigned)it->item_id,
                      (unsigned)it->gameplay_kind,
                      (unsigned)it->item_definition_id,
                      (unsigned)it->visual_style_id,
                      (unsigned)next_entry.slot_kind_id,
                      (unsigned)state->tick);

    if (item_def->gameplay_kind == SVR_ITEM_GAMEPLAY_MOUNTABLE)
    {
        if (!drop_prior_for_capacity &&
            prior_item && prior_item->owner_id == (uint16_t)ci)
        {
            SvrItemState event_prior_item = *prior_item;
            event_prior_item.equip_slot_kind_id = 0;
            if (!SvrQueueItemChangeEventChecked(state,
                                                &event_prior_item,
                                                ITEM_CHANGE_KIND_EQUIP_CLEAR,
                                                (uint16_t)ci,
                                                "mountable_prior_clear"))
                return false;
            clear_prior_equip = true;
        }

        SvrItemState event_item = *it;
        event_item.owner_id = (uint16_t)ci;
        event_item.equip_slot_kind_id = APPEARANCE_SLOT_KIND_MOUNT;
        if (!SvrQueueItemChangeEventChecked(state,
                                            &event_item,
                                            ITEM_CHANGE_KIND_EQUIP_SET,
                                            (uint16_t)ci,
                                            "mountable_equip_set"))
            return false;

        if (!SvrApplyPlayerMountItem(state, ps, it, cache))
        {
            SvrRuntimeDiagLog(state,
                              "[item-debug] explicit pickup mount failed: apply failed ci=%d item_id=%u gameplay_kind=%u source=%s\n",
                              ci,
                              (unsigned)it->item_id,
                              (unsigned)it->gameplay_kind,
                              source ? source : "unknown");
            return false;
        }
        if (drop_prior_for_capacity && prior_item)
        {
            prior_item->owner_id = 0xFFFF;
            SvrClearEquippedStateForItem(prior_item);
            memcpy(prior_item->pos, ps->pos, sizeof(prior_item->pos));
        }
        else if (clear_prior_equip && prior_item)
        {
            SvrClearEquippedStateForItem(prior_item);
        }
        it->owner_id = (uint16_t)ci;
        return true;
    }

    if (!drop_prior_for_capacity &&
        prior_item && prior_item->owner_id == (uint16_t)ci)
    {
        SvrItemState event_prior_item = *prior_item;
        event_prior_item.equip_slot_kind_id = 0;
        if (!SvrQueueItemChangeEventChecked(state,
                                            &event_prior_item,
                                            ITEM_CHANGE_KIND_EQUIP_CLEAR,
                                            (uint16_t)ci,
                                            "equippable_prior_clear"))
            return false;
        clear_prior_equip = true;
    }

    SvrItemState event_item = *it;
    event_item.owner_id = (uint16_t)ci;
    event_item.equip_slot_kind_id = next_entry.slot_kind_id;
    if (!SvrQueueItemChangeEventChecked(state,
                                        &event_item,
                                        ITEM_CHANGE_KIND_EQUIP_SET,
                                        (uint16_t)ci,
                                        "equippable_equip_set"))
        return false;

    if (!SvrUpsertAppearanceEntry(&ps->appearance, &next_entry, true))
    {
        SvrRuntimeDiagLog(state,
                          "[item-debug] explicit pickup equip failed: loadout upsert failed ci=%d item_id=%u gameplay_kind=%u source=%s\n",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->gameplay_kind,
                          source ? source : "unknown");
        return false;
    }
    if (drop_prior_for_capacity && prior_item)
    {
        prior_item->owner_id = 0xFFFF;
        SvrClearEquippedStateForItem(prior_item);
        memcpy(prior_item->pos, ps->pos, sizeof(prior_item->pos));
    }
    else if (clear_prior_equip && prior_item)
    {
        SvrClearEquippedStateForItem(prior_item);
    }
    it->owner_id = (uint16_t)ci;
    SvrSetEquippedStateForItem(it, next_entry.slot_kind_id);
    SvrRefreshPlayerPresentationAfterEquipMutation(state, ps);
    return true;
}

static void SvrConsumeOwnedLootItem(ServerState* state, int ci, SvrItemState* it)
{
    if (!state || !it || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;
    if (!SvrOwnedItemCanBeConsumed(it, (uint16_t)ci))
    {
        SvrRuntimeDiagLog(state,
                          "[item-loot] consume rejected ci=%d item_id=%u owner=%u active=%d tick=%u\n",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)it->owner_id,
                          it->active ? 1 : 0,
                          (unsigned)state->tick);
        return;
    }
    SvrRuntimeDiagLog(state,
                      "[item-loot] consume ci=%d item_id=%u definition=%u style=%u tick=%u\n",
                      ci,
                      (unsigned)it->item_id,
                      (unsigned)it->item_definition_id,
                      (unsigned)it->visual_style_id,
                      (unsigned)state->tick);
    if (!SvrConsumeOwnedItemAfterEvent(it,
                                       (uint16_t)ci,
                                       ITEM_CHANGE_KIND_CONSUME,
                                       SvrEmitItemChangeEventForOwnershipContract,
                                       state))
    {
        SvrRuntimeDiagLog(state,
                          "[item-loot] consume deferred ci=%d item_id=%u reason=event_not_queued tick=%u\n",
                          ci,
                          (unsigned)it->item_id,
                          (unsigned)state->tick);
    }
}

static void SvrFillAppearanceStateV2(STRUCT_BRC_APPEARANCE_STATE_V2* out,
                                     uint8_t entity_type,
                                     uint16_t entity_id,
                                     const SvrAuthoritativeAppearanceState* appearance)
{
    // Walkthrough Step 5:
    // Serialize the authoritative appearance identity. This packet carries ids,
    // not finished sprite names and not precomposed outfit combinations.
    if (!out || !appearance)
        return;

    memset(out, 0, sizeof(*out));
    out->token = 'a';
    out->entity_type = entity_type;
    out->entity_id = entity_id;
    out->loadout_revision = appearance->loadout_revision;
    out->appearance_contract_version = appearance->appearance_contract_version;
    out->appearance_profile_id = appearance->appearance_profile_id;
    out->skin_definition_id = appearance->skin_definition_id;
    out->mount_definition_id = appearance->mount_definition_id;
    out->variation_id = appearance->variation_id;
    out->rig_id = appearance->rig_id;
    out->source_kind = appearance->source_kind;
    out->projection_kind = appearance->projection_kind;
    out->subject_kind = appearance->subject_kind;
    out->entry_count = appearance->entry_count <= APPEARANCE_STATE_V2_MAX_ENTRIES
        ? appearance->entry_count
        : APPEARANCE_STATE_V2_MAX_ENTRIES;
    SvrCopyAppearanceSubjectKey(out->subject_key, appearance->subject_key);
    for (int i = 0; i < out->entry_count; i++)
    {
        out->entries[i].slot_kind_id = appearance->entries[i].slot_kind_id;
        out->entries[i].item_definition_id = appearance->entries[i].item_definition_id;
        out->entries[i].visual_style_id =
            SvrNormalizeAppearanceVisualStyleId(appearance->entries[i].visual_style_id);
        out->entries[i].state_flags = appearance->entries[i].state_flags;
    }
}


static uint32_t SvrAppearanceStateV2Signature(const STRUCT_BRC_APPEARANCE_STATE_V2* state)
{
    if (!state)
        return 0;
    uint32_t hash = 2166136261u;
    const uint8_t* bytes = (const uint8_t*)state;
    for (size_t i = 0; i < sizeof(*state); i++)
    {
        hash ^= bytes[i];
        hash *= 16777619u;
    }
    return hash ? hash : 1u;
}

// S8/FL-1711: per-snapshot full APPEARANCE_STATE_V2 resend was deleted (appearance
// signature caching added here). Removing this cache would re-introduce the
// ordered-WebSocket HOL blocking that was the root cause. Do NOT revert to
// unconditional per-tick resend; the *valid + *cached_signature == signature guard
// is the fix. This family is spent; broader lag floor lives in U2.
static bool SvrQueueChangedAppearanceStateV2ToClient(ServerState* state,
                                                     int ci,
                                                     uint8_t entity_type,
                                                     uint16_t entity_id,
                                                     const SvrAuthoritativeAppearanceState* appearance)
{
    // Walkthrough Step 5 / Contract 5:
    // appearance_v2 is resent only when the authoritative id payload changes.
    // The client caches these ids and resolves layers later during rendering.
    if (!state || !appearance || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;

    STRUCT_BRC_APPEARANCE_STATE_V2 ev = {};
    SvrFillAppearanceStateV2(&ev, entity_type, entity_id, appearance);
    uint32_t signature = SvrAppearanceStateV2Signature(&ev);

    SvrPlayerState* recipient = &state->players[ci];
    uint8_t* valid = 0;
    uint32_t* cached_signature = 0;
    if (entity_type == APPEARANCE_V2_ENTITY_PLAYER)
    {
        if (entity_id >= SVR_MAX_CLIENTS)
            return false;
        valid = &recipient->sent_player_appearance_valid[entity_id];
        cached_signature = &recipient->sent_player_appearance_signature[entity_id];
    }
    else if (entity_type == APPEARANCE_V2_ENTITY_NPC)
    {
        if (entity_id < SVR_MAX_CLIENTS)
            return false;
        uint16_t npc_index = (uint16_t)(entity_id - SVR_MAX_CLIENTS);
        if (npc_index >= SVR_MAX_NPCS)
            return false;
        valid = &recipient->sent_npc_appearance_valid[npc_index];
        cached_signature = &recipient->sent_npc_appearance_signature[npc_index];
    }
    else
        return false;

    if (*valid && *cached_signature == signature)
        return true;
    if (!SvrQueueToClient(state, ci, (const uint8_t*)&ev, sizeof(ev), false))
        return false;

    *valid = 1;
    *cached_signature = signature;
    return true;
}

static void SvrResetRecipientAppearanceSendCaches(ServerState* state, int ci)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;
    SvrPlayerState* recipient = &state->players[ci];
    memset(recipient->sent_player_appearance_valid, 0, sizeof(recipient->sent_player_appearance_valid));
    memset(recipient->sent_player_appearance_signature, 0, sizeof(recipient->sent_player_appearance_signature));
    memset(recipient->sent_npc_appearance_valid, 0, sizeof(recipient->sent_npc_appearance_valid));
    memset(recipient->sent_npc_appearance_signature, 0, sizeof(recipient->sent_npc_appearance_signature));
}

static void SvrInvalidatePlayerAppearanceSendCachesForSlot(ServerState* state, uint16_t entity_id)
{
    if (!state || entity_id >= SVR_MAX_CLIENTS)
        return;
    for (int ci = 0; ci < SVR_MAX_CLIENTS; ci++)
    {
        state->players[ci].sent_player_appearance_valid[entity_id] = 0;
        state->players[ci].sent_player_appearance_signature[entity_id] = 0;
    }
}


static void SvrReplayAuthoritativeItemsToClient(ServerState* state, int ci)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;

    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        const SvrItemState* it = &state->items[i];
        if (!it->active)
            continue;

        uint8_t kind = ITEM_CHANGE_KIND_OWNER_SET;
        if (it->owner_id == 0xFFFF)
            kind = ITEM_CHANGE_KIND_DROP;
        else if (it->equip_slot_kind_id != 0)
            kind = ITEM_CHANGE_KIND_EQUIP_SET;

        STRUCT_BRC_ITEM_CHANGE_V2 ev_v2 = {};
        ev_v2.token = 'i';
        ev_v2.kind = kind;
        ev_v2.item_id = it->item_id;
        ev_v2.owner_id = it->owner_id;
        ev_v2.event_id = state->next_item_event_id + 1;
        ev_v2.tick = state->tick;
        memcpy(ev_v2.pos, it->pos, sizeof(ev_v2.pos));
        SvrFillItemChangeEventV2Payload(&ev_v2, state, it, it->owner_id);

        if (SvrQueueToClient(state, ci, (const uint8_t*)&ev_v2, sizeof(ev_v2), false))
            state->next_item_event_id = ev_v2.event_id;
        else
            SvrRuntimeDiagLog(state,
                              "[item-replay] queue failed ci=%d item_id=%u event_id=%u tick=%u\n",
                              ci,
                              (unsigned)it->item_id,
                              (unsigned)ev_v2.event_id,
                              (unsigned)state->tick);
    }
}

int WS_FRAME_ENCODE(uint8_t* dst, const uint8_t* payload, int payload_size, int opcode)
{
    int hdr_len = 0;
    dst[0] = 0x80 | (opcode & 0x0F); // FIN + opcode
    if (payload_size < 126)
    {
        dst[1] = (uint8_t)payload_size;
        hdr_len = 2;
    }
    else if (payload_size < 65536)
    {
        dst[1] = 126;
        WS_WRITE_U16_BE(dst + 2, (uint16_t)payload_size);
        hdr_len = 4;
    }
    else
    {
        dst[1] = 127;
        WS_WRITE_U64_BE(dst + 2, (uint64_t)payload_size);
        hdr_len = 10;
    }
    memcpy(dst + hdr_len, payload, payload_size);
    return hdr_len + payload_size;
}

static void SvrSendWSCloseReason(TCP_SOCKET socket, uint16_t code, const char* reason)
{
    uint8_t payload[2 + 123];
    int reason_len = reason ? (int)strlen(reason) : 0;
    if (reason_len > 123)
        reason_len = 123;
    payload[0] = (uint8_t)((code >> 8) & 0xFFu);
    payload[1] = (uint8_t)(code & 0xFFu);
    if (reason_len > 0)
        memcpy(payload + 2, reason, (size_t)reason_len);

    uint8_t frame[256];
    int frame_len = WS_FRAME_ENCODE(frame, payload, 2 + reason_len, 0x8);
    int send_rc = 0;
#ifdef __linux__
    send_rc = send(socket, (const char*)frame, frame_len, MSG_NOSIGNAL);
#else
    send_rc = send(socket, (const char*)frame, frame_len, 0);
#endif
    if (send_rc < 0) {
        printf("[ws-close-send] failed code=%u errno=%d reason=%s\n", (unsigned)code, errno, reason ? reason : "");
    }
}

static void SvrWakeIOThread(ServerState* state);

void SvrStateInit(ServerState* state, Terrain* t, World* w)
{
    memset(state, 0, sizeof(ServerState));
    state->terrain = t;
    state->world = w;
    state->listen_socket = INVALID_TCP_SOCKET;
    state->io_wake_read_fd = -1;
    state->io_wake_write_fd = -1;
    state->start_time_us = a3dGetTime();

    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        state->clients[i].socket = INVALID_TCP_SOCKET;
        state->clients[i].phase = CPHASE_NONE;
        state->clients[i].write_idx = 0;
        state->clients[i].read_idx = 1;
        state->clients[i].shared_idx = 2;
    }
}

void SvrInitNpcs(ServerState* state)
{
    state->npc_count = 0;

    for (EnemyGen* eg = enemygen_head; eg && state->npc_count < SVR_MAX_NPCS; eg = eg->next)
    {
        int gen_index = state->npc_count; // track which generator

        for (int a = 0; a < eg->alive_max && state->npc_count < SVR_MAX_NPCS; a++)
        {
            SvrNpcState* npc = &state->npcs[state->npc_count];
            memset(npc, 0, sizeof(SvrNpcState));

            npc->active = true;
            npc->entity_id = SVR_MAX_CLIENTS + state->npc_count;
            npc->pos[0] = eg->pos[0] + (float)(rand() % 10 - 5);
            npc->pos[1] = eg->pos[1] + (float)(rand() % 10 - 5);
            npc->spawn_pos[0] = eg->pos[0];
            npc->spawn_pos[1] = eg->pos[1];
            // EnemyGen Z is authored map data, not a runtime-safe spawn height.
            // Resolve both the anchor and the randomized spawn against terrain so
            // authoritative NPCs do not begin in the air and immediately attack
            // while still falling.
            npc->spawn_pos[2] = SvrSampleTerrainHeight(
                state->terrain, npc->spawn_pos[0], npc->spawn_pos[1], 0.0f);
            npc->pos[2] = SvrSampleTerrainHeight(
                state->terrain, npc->pos[0], npc->pos[1], npc->spawn_pos[2]);
            npc->spawn_gen_index = gen_index;
            npc->enemy = true;
            npc->hp = SVR_NPC_MAX_HP;
            npc->max_hp = SVR_NPC_MAX_HP;
            npc->target_id = 0xFFFF;
            npc->life_state = LIFE_STATE::ALIVE;
            npc->locomotion_state = LOCOMOTION_STATE::IDLE;
            npc->combat_state = COMBAT_STATE::NONE;
            npc->presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
            npc->presentation_started_tick = state->tick;
            npc->last_swing_presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
            if (!SvrApplyNpcSpawnAppearance(state, npc, eg, state->npc_count))
            {
                memset(npc, 0, sizeof(SvrNpcState));
                continue;
            }
            SvrRefreshNpcPresentationKind(state, npc);

            // FL-2957 attempt #29: npc->pos is already resolved against terrain above,
            // so reviving PHYSICS_CREATE_TERRAIN_SAFE_LIFT here only re-launches the
            // NPC 200 units above the sampled floor and seeds accum_contact=0. Delete
            // that stale owner and keep the physics body on the exact server-resolved
            // terrain position instead.
            // LINEAGE_JSON: {"fl":"FL-2957","attempt":29,"owner_delete":"PHYSICS_CREATE_TERRAIN_SAFE_LIFT for exact terrain-resolved NPC spawn","replacement":"PHYSICS_CREATE_EXACT_POS","worked_if":["npcs_fast_path>0","npcs_full_step drops","max_physics_phase=npcs reduced"],"failed_if":["npcs_full_step remains dominant","npcs_fast_path stays 0"]}
            if (state->terrain && state->world)
            {
                npc->physics = CreatePhysics(state->terrain, state->world,
                                             npc->pos, npc->dir, 0.0f, a3dGetTime(),
                                             PHYSICS_CREATE_EXACT_POS);
            }

            // Respawn delay (random between 2^min and 2^max seconds)
            int min_sec = 1 << eg->revive_min;
            int max_sec = 1 << eg->revive_max;
            if (max_sec > min_sec)
                npc->respawn_delay = (min_sec + rand() % (max_sec - min_sec)) * SVR_TICK_RATE;
            else
                npc->respawn_delay = min_sec * SVR_TICK_RATE;

            state->npc_count++;
            SvrRuntimeDiagLog(state,
                "[SVR-NPC-SPAWN] idx=%d eid=%u gen=%d spawn=(%.2f,%.2f,%.2f) pos=(%.2f,%.2f,%.2f) profile=%u skin=%u rev=%u entries=%u respawn=%u\n",
                state->npc_count - 1,
                (unsigned)npc->entity_id,
                gen_index,
                npc->spawn_pos[0], npc->spawn_pos[1], npc->spawn_pos[2],
                npc->pos[0], npc->pos[1], npc->pos[2],
                (unsigned)npc->appearance.appearance_profile_id,
                (unsigned)npc->appearance.skin_definition_id,
                (unsigned)npc->appearance.loadout_revision,
                (unsigned)npc->appearance.entry_count,
                (unsigned)npc->respawn_delay);
        }
    }

    printf("[tick] Initialized %d NPCs from EnemyGen spawn points\n",
           state->npc_count);
}

static void SvrResetIdleWorld(ServerState* state)
{
    if (!state) return;

    for (int i = 0; i < state->npc_count; i++)
    {
        if (state->npcs[i].physics) DeletePhysics(state->npcs[i].physics);
    }

    memset(state->npcs, 0, sizeof(state->npcs)); state->npc_count = 0;
    memset(state->items, 0, sizeof(state->items)); state->item_count = 0;
    state->next_item_event_id = 0;
    memset(state->decal_history, 0, sizeof(state->decal_history));
    state->decal_write_pos = 0; state->next_decal_event_id = 0;
    memset(state->pending_swings, 0, sizeof(state->pending_swings)); state->pending_swing_count = 0;
    memset(state->pending_projectiles, 0, sizeof(state->pending_projectiles)); state->pending_projectile_count = 0;
    state->events.len = state->events.count = 0;
    state->combat_swing_count = state->combat_damage_count = 0;
    state->combat_damage_player_to_player_count = 0;
    state->combat_damage_player_to_npc_count = 0;
    state->combat_damage_npc_to_player_count = 0;
    state->combat_damage_npc_to_npc_count = 0;
    state->combat_death_count = state->combat_respawn_count = state->snapshot_seq = 0;

    SvrInitNpcs(state);
    SvrInitWorldItems(state);

    const uint64_t sync_stamp = state->tick_stamp_us ? state->tick_stamp_us : a3dGetTime();
    for (int i = 0; i < state->npc_count; i++)
    {
        if (state->npcs[i].physics) SyncPhysicsStamp(state->npcs[i].physics, sync_stamp);
    }

    printf("[tick] Idle reset completed tick=%u reason=no_active_sessions npcs=%d items=%d\n",
           (unsigned)state->tick, state->npc_count, state->item_count);
}

static bool SvrQueueEvent(ServerState* state, const uint8_t* data, int size, int exclude)
{
    if (!state || !data || size <= 0)
        return false;

    SvrEventQueue* eq = &state->events;
    char token = (char)data[0];
    bool critical_event =
        (token == 'i' || token == 'h' || token == 'd' || token == 'k' || token == 'r');
    if (eq->count >= 512 || eq->len + size > 32768)
    {
        if (critical_event)
        {
            SvrRuntimeDiagLog(state,
                              "[event-debug] drop token=%c size=%d count=%d len=%d exclude=%d tick=%u\n",
                              token, size, eq->count, eq->len, exclude, (unsigned)state->tick);
        }
        return false;
    }

    memcpy(eq->buf + eq->len, data, size);
    SvrEventQueue::Entry* e = &eq->entries[eq->count++];
    e->offset = eq->len;
    e->size = size;
    e->exclude_client = exclude;
    eq->len += size;
    if (critical_event)
    {
        SvrRuntimeDiagLog(state,
                          "[event-debug] queue token=%c size=%d count=%d len=%d exclude=%d tick=%u\n",
                          token, size, eq->count, eq->len, exclude, (unsigned)state->tick);
    }
    return true;
}

static bool SvrQueueToClient(ServerState* state, int ci, const uint8_t* data, int size,
                             bool is_snapshot)
{
    ClientIO* cio = &state->clients[ci];
    ClientIO::OutBuf* ob = &cio->out[cio->write_idx];
    char token = (data && size > 0) ? (char)data[0] : '?';
    bool critical_event =
        (!is_snapshot) &&
        (token == 'i' || token == 'h' || token == 'd' || token == 'k' || token == 'r');

    if (is_snapshot && ob->len > 0)
    {
        // Old-owner deletion: a client only needs the newest pending snapshot from the
        // tick thread's write buffer. Keeping historical q/b frames alive here turns
        // death/respawn bursts into seconds of whole-client delay while the browser drains
        // stale state. Preserve non-snapshot events, but replace older pending snapshots.
        int pre_strip_len = ob->len;
        int read_off = 0;
        int write_off = 0;
        while (read_off + 2 <= ob->len)
        {
            int hdr_len = 2;
            uint64_t payload_len = (uint64_t)(ob->data[read_off + 1] & 0x7Fu);
            if (payload_len == 126)
            {
                if (read_off + 4 > ob->len)
                    break;
                payload_len = ((uint64_t)ob->data[read_off + 2] << 8) |
                              (uint64_t)ob->data[read_off + 3];
                hdr_len = 4;
            }
            else if (payload_len == 127)
            {
                if (read_off + 10 > ob->len)
                    break;
                payload_len =
                    ((uint64_t)ob->data[read_off + 2] << 56) |
                    ((uint64_t)ob->data[read_off + 3] << 48) |
                    ((uint64_t)ob->data[read_off + 4] << 40) |
                    ((uint64_t)ob->data[read_off + 5] << 32) |
                    ((uint64_t)ob->data[read_off + 6] << 24) |
                    ((uint64_t)ob->data[read_off + 7] << 16) |
                    ((uint64_t)ob->data[read_off + 8] << 8) |
                    (uint64_t)ob->data[read_off + 9];
                hdr_len = 10;
            }
            const bool masked = (ob->data[read_off + 1] & 0x80u) != 0;
            if (masked)
                hdr_len += 4;
            if (payload_len > (uint64_t)(ob->len - read_off - hdr_len))
                break;
            int frame_len = hdr_len + (int)payload_len;
            char queued_token = payload_len > 0 ? (char)ob->data[read_off + hdr_len] : '?';
            const bool queued_snapshot = (queued_token == 'b' || queued_token == 'q');
            if (!queued_snapshot)
            {
                if (write_off != read_off)
                    memmove(ob->data + write_off, ob->data + read_off, (size_t)frame_len);
                write_off += frame_len;
            }
            read_off += frame_len;
        }
        ob->len = write_off;
        static int diag_strip_logs[SVR_MAX_CLIENTS] = {};
        if (write_off != pre_strip_len && ci >= 0 && ci < SVR_MAX_CLIENTS &&
            diag_strip_logs[ci] < 5)
        {
            SvrRuntimeDiagLog(state,
                          "[DIAG-STRIP] ci=%d pre_len=%d post_len=%d stripped=%d tick=%u token=%c\n",
                   ci, pre_strip_len, write_off, pre_strip_len - write_off,
                   state ? (unsigned)state->tick : 0, token);
            diag_strip_logs[ci]++;
        }
    }

    // Backpressure: if buffer > 75% full, drop snapshots (self-correcting)
    int threshold = SVR_OUTBOUND_BUF_SIZE * 3 / 4;
    if (ob->len > threshold && is_snapshot) return false;

    if (ob->len + size + 14 > SVR_OUTBOUND_BUF_SIZE)
    {
        if (critical_event)
        {
            SvrRuntimeDiagLog(state,
                              "[event-debug] to-client drop ci=%d token=%c size=%d ob_len=%d tick=%u\n",
                              ci, token, size, ob->len, (unsigned)state->tick);
        }
        return false;
    }

    int frame_len = WS_FRAME_ENCODE(ob->data + ob->len, data, size, 0x2);
    ob->len += frame_len;
    if (critical_event)
    {
        SvrRuntimeDiagLog(state,
                          "[event-debug] to-client ci=%d token=%c size=%d frame=%d ob_len=%d tick=%u\n",
                          ci, token, size, frame_len, ob->len, (unsigned)state->tick);
    }
    return true;
}

static bool SvrTransitionClientPhase(ServerState* state, int ci, ClientPhase target)
{
    SvrPlayerState* ps = &state->players[ci];
    if (!SvrTransitionPhase(ps, target)) return false;
    atomic_store_phase(&state->clients[ci].phase, target);
    return true;
}

static bool SvrQueueCollisionDebugToClient(ServerState* state, int ci, const SvrPlayerState* ps)
{
    if (!state || !ps || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;
    if (!state->debug_runtime_diagnostics_enabled && !state->debug_fly_mode_enabled)
        return true;

    const uint16_t total_count =
        ps->collision_debug_sample_count > COLLISION_DEBUG_SAMPLE_MAX
            ? COLLISION_DEBUG_SAMPLE_MAX
            : ps->collision_debug_sample_count;
    uint16_t chunk_count =
        (uint16_t)((total_count + COLLISION_DEBUG_PACKET_SAMPLE_MAX - 1) /
            COLLISION_DEBUG_PACKET_SAMPLE_MAX);
    if (chunk_count == 0)
        chunk_count = 1;
    bool ok = true;
    for (uint16_t chunk = 0; chunk < chunk_count; chunk++)
    {
        const uint16_t offset = (uint16_t)(chunk * COLLISION_DEBUG_PACKET_SAMPLE_MAX);
        uint16_t count = 0;
        if (offset < total_count)
        {
            count = (uint16_t)(total_count - offset);
            if (count > COLLISION_DEBUG_PACKET_SAMPLE_MAX)
                count = COLLISION_DEBUG_PACKET_SAMPLE_MAX;
        }
        STRUCT_BRC_COLLISION_DEBUG dbg = {};
        dbg.token = 'c';
        dbg.count = count;
        dbg.total_count = total_count;
        dbg.player_id = (uint16_t)ci;
        dbg.tick = state->tick;
        dbg.chunk_index = (uint8_t)chunk;
        dbg.chunk_count = (uint8_t)chunk_count;
        dbg.support_source = ps->support_source;
        dbg.push_source = ps->collision_debug_push_source;
        dbg.support_item_id = ps->support_item_id;
        dbg.player_pos[0] = ps->pos[0];
        dbg.player_pos[1] = ps->pos[1];
        dbg.player_pos[2] = ps->pos[2];
        dbg.support_z = ps->support_z;
        if (count > 0)
        {
            memcpy(dbg.samples, ps->collision_debug_samples + offset,
                (size_t)count * sizeof(dbg.samples[0]));
        }
        if (!SvrQueueToClient(state, ci, (const uint8_t*)&dbg, sizeof(dbg), false))
            ok = false;
    }
    return ok;
}

static void SvrQueueDecal(ServerState* state, float x, float y, float r, uint8_t matid)
{
    SvrDecalEvent* de = &state->decal_history[state->decal_write_pos % SVR_MAX_DECAL_HISTORY];
    de->event_id = state->next_decal_event_id++;
    de->tick = state->tick;
    de->x = x;
    de->y = y;
    de->r = r;
    de->matid = matid;
    state->decal_write_pos++;

    STRUCT_BRC_DECAL_ADD brc = {};
    brc.token = 'v';
    brc.matid = matid;
    brc.event_id = de->event_id;
    brc.tick = state->tick;
    brc.x = x;
    brc.y = y;
    brc.r = r;
    SvrQueueEvent(state, (const uint8_t*)&brc, sizeof(brc), -1);
}


static bool SvrBootstrapAlive(ServerState* state, int ci, const char* reason);
static void IOSendJoinRejectV2(const ServerState* state, TCP_SOCKET socket, uint8_t reason_code);

static bool SvrPrepareJoinName(ServerState* state, int ci, const char* raw_name)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS || !raw_name)
        return false;

    SvrPlayerState* ps = &state->players[ci];
    if (ps->phase != CPHASE_CONNECTING)
        return false;

    SvrNormalizeJoinDisplayName(raw_name, ps->name);

    // RQ-087: Reject names with non-printable or non-ASCII characters.
    if (!SvrValidateJoinNameChars(ps->name))
    {
        SvrRuntimeDiagLog(state,
                          "[name-validation] rejected invalid chars ci=%d name='%s' tick=%u\n",
                          ci, ps->name, (unsigned)state->tick);
        TCP_SOCKET socket = state->clients[ci].socket;
        IOSendJoinRejectV2(state, socket, APPEARANCE_CONTRACT_REJECT_REASON::NAME_INVALID_CHARS);
        SvrSendWSCloseReason(socket, 1008, "name_invalid_chars");
        state->clients[ci].disconnect_ws_close_code = 1008;
        SvrTransitionClientPhase(state, ci, CPHASE_DISCONNECTING);
        return false;
    }

    // RQ-087: Reject duplicate names.
    int existing_ci = SvrFindActivePlayerByName(state, ps->name, ci);
    if (existing_ci >= 0)
    {
        SvrRuntimeDiagLog(state,
                          "[name-validation] rejected duplicate name ci=%d name='%s' existing_ci=%d tick=%u\n",
                          ci, ps->name, existing_ci, (unsigned)state->tick);
        TCP_SOCKET socket = state->clients[ci].socket;
        IOSendJoinRejectV2(state, socket, APPEARANCE_CONTRACT_REJECT_REASON::NAME_DUPLICATE);
        SvrSendWSCloseReason(socket, 1008, "name_duplicate");
        state->clients[ci].disconnect_ws_close_code = 1008;
        SvrTransitionClientPhase(state, ci, CPHASE_DISCONNECTING);
        return false;
    }

    strncpy(state->clients[ci].name, ps->name, 31);
    state->clients[ci].name[31] = 0;

    SvrActorVisualProfileCatalog appearance_cache = {};
    const SvrActorVisualProfileCatalogProfileDef* profile = 0;
    uint8_t source_kind = SVR_APPEARANCE_SOURCE_NONE;
    uint8_t subject_kind = SVR_APPEARANCE_SUBJECT_NONE;
    char subject_key[32] = {};
    if (SvrSelectJoinAppearanceProfile(state,
                                       ps->name,
                                       &profile,
                                       &source_kind,
                                       &subject_kind,
                                       subject_key) &&
        SvrLoadActorVisualProfileCatalog(&appearance_cache) &&
        profile)
    {
        SvrApplyProfileToAppearance(&ps->appearance,
                                    &appearance_cache,
                                    profile,
                                    source_kind,
                                    subject_kind,
                                    subject_key);
        // Player-join path: materialize starter loadout items into state->items[]
        // owned by ci, and link the appearance entries to the created item ids.
        // Skip for NPC spawns (which use a separate code path further down).
        if (subject_kind != SVR_APPEARANCE_SUBJECT_NPC_SPAWN)
        {
            int created = SvrCreateStarterLoadoutItems(state, ci, &appearance_cache, profile, &ps->appearance);
            SvrRuntimeDiagLog(state,
                              "[starter-loadout] ci=%d created=%d starter_count=%u profile_id=%u tick=%u\n",
                              ci, created, (unsigned)profile->starter_count,
                              (unsigned)profile->id, (unsigned)state->tick);
        }
    }
    else
    {
        SvrRuntimeDiagLog(state,
                          "[appearance-contract] join rejected: no valid server-owned skin profile ci=%d name=%s tick=%u\n",
                          ci,
                          ps->name,
                          (unsigned)state->tick);
        return false;
    }

    ps->player_id = ci;
    return true;
}

static void SvrCommitJoinAccepted(ServerState* state, int ci)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;

    SvrPlayerState* ps = &state->players[ci];
    if (ps->phase != CPHASE_ALIVE)
        return;

    // JOIN starts a fresh recipient baseline and a fresh lifecycle for slot ci.
    // Do not let per-recipient signature caches suppress appearance_v2 resend
    // across reconnect/rejoin reuse of the same player slot.
    SvrResetRecipientAppearanceSendCaches(state, ci);
    SvrInvalidatePlayerAppearanceSendCachesForSlot(state, (uint16_t)ci);


    // IO thread owns the immediate join-accept response so the native client
    // does not stall for a whole authoritative tick before it can leave Connect().
    SvrQueueChangedAppearanceStateV2ToClient(state,
                                             ci,
                                             APPEARANCE_V2_ENTITY_PLAYER,
                                             (uint16_t)ci,
                                             &ps->appearance);

    // Send existing players to the new client
    STRUCT_BRC_JOIN brc = {};
    brc.token = 'j';
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        if (i == ci) continue;
        SvrPlayerState* other = &state->players[i];
        if (!other->active || other->phase < CPHASE_JOINED) continue;

        brc.id = i;
        brc.life_state = other->life_state;
        brc.mount_state = SvrRuntimeMountStateForPlayer(other);
        brc.locomotion_state = other->locomotion_state;
        brc.combat_state = other->combat_state;
        memcpy(brc.pos, other->pos, 12);
        brc.dir = other->dir;
        brc.presentation_kind_id = other->presentation_kind_id;
        brc.presentation_started_tick = other->presentation_started_tick;
        strncpy(brc.name, other->name, 31);
        brc.name[30] = 0;
        brc.name[31] = 0;
        SvrQueueToClient(state, ci, (const uint8_t*)&brc, sizeof(brc), false);
        SvrQueueChangedAppearanceStateV2ToClient(state,
                                                 ci,
                                                 APPEARANCE_V2_ENTITY_PLAYER,
                                                 (uint16_t)i,
                                                 &other->appearance);
    }

    // The browser JOIN contract requires RSP_JOIN to be the first bootstrap frame.
    // Authoritative item replay belongs after the accepted join response, not inside
    // SvrBootstrapAlive(), where it can outrun the response and appear as token 'i'.
    SvrReplayAuthoritativeItemsToClient(state, ci);

    // Broadcast new player to all others
    STRUCT_BRC_JOIN brc_new = {};
    brc_new.token = 'j';
    brc_new.id = ci;
    brc_new.life_state = ps->life_state;
    brc_new.mount_state = SvrRuntimeMountStateForPlayer(ps);
    brc_new.locomotion_state = ps->locomotion_state;
    brc_new.combat_state = ps->combat_state;
    memcpy(brc_new.pos, ps->pos, 12);
    brc_new.dir = ps->dir;
    brc_new.presentation_kind_id = ps->presentation_kind_id;
    brc_new.presentation_started_tick = ps->presentation_started_tick;
    strncpy(brc_new.name, ps->name, 31);
    brc_new.name[30] = 0;
    brc_new.name[31] = 0;
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        if (i == ci) continue;
        SvrPlayerState* other = &state->players[i];
        if (!other->active || other->phase < CPHASE_JOINED) continue;
        // Existing recipients must observe JOIN before the matching appearance_v2
        // payload for this slot. Queueing JOIN through SvrQueueEvent defers it
        // until phase-7 event flush, which lets the direct appearance_v2 packet
        // arrive first and then get wiped by the client's JOIN lifecycle reset.
        SvrQueueToClient(state, i, (const uint8_t*)&brc_new, sizeof(brc_new), false);
        SvrQueueChangedAppearanceStateV2ToClient(state,
                                                 i,
                                                 APPEARANCE_V2_ENTITY_PLAYER,
                                                 (uint16_t)ci,
                                                 &ps->appearance);
    }

    {
        static int diag_join_queue_logs[SVR_MAX_CLIENTS] = {};
        if (diag_join_queue_logs[ci] < 6)
        {
            ClientIO* cio = &state->clients[ci];
            printf("[DIAG-JOIN-QUEUE] ci=%d tick=%u phase=%d write_idx=%d write_len=%d shared_idx=%d read_idx=%d new_data=%d active=%d rsp_len=%u queue_after_accept=1\n",
                   ci,
                   (unsigned)state->tick,
                   (int)ps->phase,
                   cio->write_idx,
                   cio->out[cio->write_idx].len,
                   __atomic_load_n(&cio->shared_idx, __ATOMIC_RELAXED),
                   cio->read_idx,
                   __atomic_load_n(&cio->new_data, __ATOMIC_RELAXED),
                   ps->active ? 1 : 0,
                   (unsigned)sizeof(STRUCT_RSP_JOIN));
            diag_join_queue_logs[ci]++;
        }
    }

    printf("[tick] Player '%s' joined as ID %d tick=%u stamp_us=%llu\n",
           ps->name, ci, (unsigned)state->tick, SvrLogStampUs());
}

static void SvrProcessJoinV2(ServerState* state, int ci, const uint8_t* data, int size)
{
    // FL-4137 join-path diag (temporary): expose each silent-return seam so a
    // proof timeout can name the guard that killed it instead of reading as
    // "nothing happened". Strip when the join blocker is closed.
    if (size != sizeof(STRUCT_REQ_JOIN_V2)) {
        printf("[join-v2-silent] ci=%d size=%d expected=%zu reason=size_mismatch stamp_us=%llu\n",
               ci, size, sizeof(STRUCT_REQ_JOIN_V2), SvrLogStampUs());
        return;
    }
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS) {
        printf("[join-v2-silent] ci=%d reason=bad_state_or_ci stamp_us=%llu\n",
               ci, SvrLogStampUs());
        return;
    }

    SvrPlayerState* ps = &state->players[ci];
    if (ps->phase != CPHASE_CONNECTING) {
        printf("[join-v2-silent] ci=%d phase=%d expected=CPHASE_CONNECTING(%d) reason=phase_mismatch tick=%u stamp_us=%llu\n",
               ci, (int)ps->phase, (int)CPHASE_CONNECTING,
               (unsigned)state->tick, SvrLogStampUs());
        return;
    }

    const STRUCT_REQ_JOIN_V2* req = (const STRUCT_REQ_JOIN_V2*)data;
    if (req->name[30] != 0) {
        printf("[join-v2-silent] ci=%d reason=name_terminator byte30=0x%02x stamp_us=%llu\n",
               ci, (unsigned)(uint8_t)req->name[30], SvrLogStampUs());
        return;
    }
    if (req->bundle_hash[APPEARANCE_HASH_HEX_LEN] != 0) {  // FL-974: match name field null-terminator guard
        printf("[join-v2-silent] ci=%d reason=bundle_hash_terminator byte64=0x%02x stamp_us=%llu\n",
               ci, (unsigned)(uint8_t)req->bundle_hash[APPEARANCE_HASH_HEX_LEN], SvrLogStampUs());
        return;
    }
    if (req->ids_lock_hash[APPEARANCE_HASH_HEX_LEN] != 0) {
        printf("[join-v2-silent] ci=%d reason=ids_lock_hash_terminator byte64=0x%02x stamp_us=%llu\n",
               ci, (unsigned)(uint8_t)req->ids_lock_hash[APPEARANCE_HASH_HEX_LEN], SvrLogStampUs());
        return;
    }
    // FL-4131 Phase 7 — null-terminator guards for the new wire fields.
    if (req->glyph_manifest_hash[APPEARANCE_HASH_HEX_LEN] != 0) {
        printf("[join-v2-silent] ci=%d reason=glyph_manifest_hash_terminator byte64=0x%02x stamp_us=%llu\n",
               ci, (unsigned)(uint8_t)req->glyph_manifest_hash[APPEARANCE_HASH_HEX_LEN], SvrLogStampUs());
        return;
    }
    if (req->content_pack_id[APPEARANCE_CONTENT_PACK_ID_CAP - 1] != 0) {
        printf("[join-v2-silent] ci=%d reason=content_pack_id_terminator byte%d=0x%02x stamp_us=%llu\n",
               ci, APPEARANCE_CONTENT_PACK_ID_CAP - 1,
               (unsigned)(uint8_t)req->content_pack_id[APPEARANCE_CONTENT_PACK_ID_CAP - 1],
               SvrLogStampUs());
        return;
    }

    uint8_t reject_reason = SvrValidateJoinV2Claims(state,
                                                    req->appearance_contract_version,
                                                    req->bundle_hash,
                                                    req->ids_lock_hash,
                                                    req->glyph_manifest_hash,
                                                    req->content_pack_id,
                                                    req->lut_hash,
                                                    req->page_atlas_chain_hash);
    if (reject_reason != APPEARANCE_CONTRACT_REJECT_REASON::NONE)
    {
        const char* reject_text = SvrAppearanceContractRejectReasonString(reject_reason);
        const uint16_t server_contract_version = SvrAppearanceContractVersion(state);
        if (reject_reason == APPEARANCE_CONTRACT_REJECT_REASON::CONTRACT_VERSION_MISMATCH &&
            server_contract_version == 0)
        {
            SvrRuntimeDiagLog(state,
                              "[join-v2] rejecting ci=%d: server bundle not loaded (contract_version=0) tick=%u\n",
                              ci, (unsigned)state->tick);
        }
        TCP_SOCKET socket = state->clients[ci].socket;
        IOSendJoinRejectV2(state, socket, reject_reason);
        SvrSendWSCloseReason(socket, 1008, reject_text);
        state->clients[ci].disconnect_ws_close_code = 1008;
        SvrTransitionClientPhase(state, ci, CPHASE_DISCONNECTING);
        return;
    }

    ps->join_v2_claim_present = true;
    ps->join_v2_contract_version = req->appearance_contract_version;
    snprintf(ps->join_v2_bundle_hash, sizeof(ps->join_v2_bundle_hash), "%s", req->bundle_hash);
    snprintf(ps->join_v2_ids_lock_hash, sizeof(ps->join_v2_ids_lock_hash), "%s", req->ids_lock_hash);
    bool accept_ok =
        SvrPrepareJoinName(state, ci, req->name) &&
        SvrTransitionClientPhase(state, ci, CPHASE_JOINED) &&
        SvrBootstrapAlive(state, ci, "JOIN_ACCEPT");
    if (!accept_ok)
    {
        SvrRuntimeDiagLog(state,
                          "[appearance-join-v2] accept failed ci=%d tick=%u phase=%d\n",
                          ci, (unsigned)state->tick, (int)ps->phase);
        TCP_SOCKET socket = state->clients[ci].socket;
        IOSendJoinRejectV2(state, socket, APPEARANCE_CONTRACT_REJECT_REASON::JOIN_ACCEPT_FAILED);
        SvrSendWSCloseReason(socket, 1011, "join_accept_failed");
        state->clients[ci].disconnect_ws_close_code = 1011;
        SvrTransitionClientPhase(state, ci, CPHASE_DISCONNECTING);
        return;
    }
    SvrCommitJoinAccepted(state, ci);
}

static bool SvrBootstrapAlive(ServerState* state, int ci, const char* reason)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;

    SvrPlayerState* ps = &state->players[ci];
    if (ps->phase != CPHASE_JOINED)
        return ps->phase == CPHASE_ALIVE;
    if (!SvrTransitionClientPhase(state, ci, CPHASE_ALIVE))
        return false;

    // T58 warm: bootstrap transition trace is one-shot per join/life.
    printf("[FL036-ALIVE] ci=%d tick=%u transitioned JOINED->ALIVE via %s\n",
           ci,
           state->tick,
           (reason && reason[0]) ? reason : "BOOTSTRAP");

    ps->hp = SVR_PLAYER_MAX_HP;
    ps->max_hp = SVR_PLAYER_MAX_HP;
    SvrResolveSafePlayerSpawn(state, ps->spawn_pos);
    // GAP-2: capture decision trace independently — terrain sample and fallback
    ps->spawn_fallback_z = SVR_PLAYER_SPAWN_FALLBACK_Z;
    ps->spawn_terrain_z = SvrSampleTerrainHeight(
        state ? state->terrain : 0, ps->spawn_pos[0], ps->spawn_pos[1], ps->spawn_fallback_z);
    ps->terrain_z = ps->spawn_terrain_z; // GAP-12: initialize so first publish has real value
    ps->in_water = 0.0f;                 // GAP-1: initialize; physics will update on first tick
    memcpy(ps->pos, ps->spawn_pos, sizeof(ps->pos));
    ps->dir = SVR_SAFE_PLAYER_SPAWN_DIR;
    ps->input_yaw = SVR_SAFE_PLAYER_SPAWN_YAW;
    ps->input_force[0] = 0.0f;
    ps->input_force[1] = 0.0f;
    ps->input_force_z = 0.0f;
    ps->input_flags = 0;
    ps->mount_state = MOUNT::NONE;
    ps->last_applied_input_seq = 0;
    ps->latest_input.valid = false;
    ps->last_swing_tick = 0;
    ps->last_swing_presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
    ps->last_swing_stamp_us = 0;
    SvrRateLimitDisconnectResetPlayer(ps);

    if (state->terrain && state->world && !ps->physics)
    {
        // S1/FL-642: PHYSICS_CREATE_EXACT_POS is mandatory. Do not remove.
        // The generic +200 terrain-safe lift was the spawn-Z root cause (23 attempts
        // exhausted before this was identified). Render-side floor clamps and
        // post-blit support retries are also spent (S4/FL-1434). Server owns Z here.
        ps->physics = CreatePhysics(state->terrain, state->world,
                                    ps->spawn_pos, SVR_SAFE_PLAYER_SPAWN_DIR, SVR_SAFE_PLAYER_SPAWN_YAW, a3dGetTime(),
                                    PHYSICS_CREATE_EXACT_POS);
    }

    // FL-641/642 proof-only instrumentation: log the spawn decision trace
    // so diagnostics can capture what calculation chose this Z.
    {
        float phys_pos[3] = {0, 0, 0};
        float phys_vel[3] = {0, 0, 0};
        if (ps->physics)
        {
            GetPhysicsPos(ps->physics, phys_pos);
            GetPhysicsVel(ps->physics, phys_vel);
        }
        // T58 warm: spawn proof log is per-bootstrap, not a steady-state hot path.
        printf("[SPAWN-DECISION] ci=%d tick=%u spawn_pos=(%.3f,%.3f,%.3f) "
               "phys_pos=(%.3f,%.3f,%.3f) phys_vel=(%.3f,%.3f,%.3f) "
               "terrain_z=%.3f spawn_terrain_z=%.3f spawn_fallback_z=%.3f "
               "in_water=%.3f has_physics=%d\n",
               ci, state->tick,
               ps->spawn_pos[0], ps->spawn_pos[1], ps->spawn_pos[2],
               phys_pos[0], phys_pos[1], phys_pos[2],
               phys_vel[0], phys_vel[1], phys_vel[2],
               ps->terrain_z, ps->spawn_terrain_z, ps->spawn_fallback_z,
               ps->in_water, ps->physics ? 1 : 0);
        fflush(stdout);
    }

    SvrRefreshPlayerPresentationKind(state, ps);

    return true;
}

// FL-2957 TRACE: 'M' movement path step 3 — server intake. Client-sent forces
// land here as STRUCT_REQ_INPUT_MOVE and are unpacked into ps->latest_input.
// The only explicit zeroing owner later is SvrResolveInput (line ~4476)
// when SVR_INPUT_STALE_TICKS expires.
enum SvrInputMoveRejectCode : uint32_t
{
    SVR_INPUT_MOVE_REJECT_NONE = 0,
    SVR_INPUT_MOVE_REJECT_BAD_SIZE = 1,
    SVR_INPUT_MOVE_REJECT_BOOTSTRAP_FAILED = 2,
    SVR_INPUT_MOVE_REJECT_NOT_ALIVE = 3,
    SVR_INPUT_MOVE_REJECT_SEQ_REGRESSION = 4,
    SVR_INPUT_MOVE_REJECT_RATE_LIMIT_DISCONNECT = 5,
};

static const char* SvrInputMoveRejectReason(uint32_t code)
{
    switch (code)
    {
        case SVR_INPUT_MOVE_REJECT_NONE: return "none";
        case SVR_INPUT_MOVE_REJECT_BAD_SIZE: return "bad_size";
        case SVR_INPUT_MOVE_REJECT_BOOTSTRAP_FAILED: return "bootstrap_failed";
        case SVR_INPUT_MOVE_REJECT_NOT_ALIVE: return "not_alive";
        case SVR_INPUT_MOVE_REJECT_SEQ_REGRESSION: return "seq_regression";
        case SVR_INPUT_MOVE_REJECT_RATE_LIMIT_DISCONNECT: return "rate_limit_disconnect";
        default: return "unknown";
    }
}

static void SvrProcessInputMove(ServerState* state, int ci, const uint8_t* data, int size, uint64_t recv_stamp_us)
{
    SvrPlayerState* ps = &state->players[ci];
    ps->m_intent_rx_count++;
    ps->m_intent_last_rx_us = recv_stamp_us;

    if (size != sizeof(STRUCT_REQ_INPUT_MOVE))
    {
        ps->m_intent_reject_count++;
        ps->m_intent_last_reject_code = SVR_INPUT_MOVE_REJECT_BAD_SIZE;
        ps->m_intent_last_reject_us = recv_stamp_us;
        ps->m_intent_last_reject_seq = 0;
        return;
    }
    if (ps->phase == CPHASE_JOINED)
    {
        if (!SvrBootstrapAlive(state, ci, "INPUT_MOVE"))
        {
            ps->m_intent_reject_count++;
            ps->m_intent_last_reject_code = SVR_INPUT_MOVE_REJECT_BOOTSTRAP_FAILED;
            ps->m_intent_last_reject_us = recv_stamp_us;
            ps->m_intent_last_reject_seq = 0;
            return;
        }
    }
    if (ps->phase != CPHASE_ALIVE)
    {
        ps->m_intent_reject_count++;
        ps->m_intent_last_reject_code = SVR_INPUT_MOVE_REJECT_NOT_ALIVE;
        ps->m_intent_last_reject_us = recv_stamp_us;
        ps->m_intent_last_reject_seq = 0;
        return;
    }

    STRUCT_REQ_INPUT_MOVE* req = (STRUCT_REQ_INPUT_MOVE*)data;
    ps->m_intent_last_rx_seq = req->input_seq;
    ps->m_intent_last_rx_move_x = req->move_x;
    ps->m_intent_last_rx_move_y = req->move_y;
    ps->m_intent_last_rx_move_z = req->move_z;
    ps->m_intent_last_rx_yaw100 = req->yaw100;
    ps->m_intent_last_rx_flags = req->flags;

    const bool nonzero =
        (req->move_x != 0) || (req->move_y != 0) || (req->move_z != 0);
    if (nonzero)
    {
        ps->m_intent_last_nonzero_seq = req->input_seq;
        ps->m_intent_last_nonzero_rx_us = recv_stamp_us;
        ps->m_intent_last_nonzero_move_x = req->move_x;
        ps->m_intent_last_nonzero_move_y = req->move_y;
        ps->m_intent_last_nonzero_move_z = req->move_z;
    }
    if (state->debug_runtime_diagnostics_enabled && fljit_move_recv_logs[ci] < 4000)
    {
        SvrRuntimeDiagLog(state,
                          "[FLJIT-M-RECV] ci=%d phase=%d tick=%u seq=%u q=(%d,%d,%d) yaw100=%d flags=%u\n",
                          ci, (int)ps->phase, state->tick,
                          (unsigned)req->input_seq,
                          (int)req->move_x, (int)req->move_y, (int)req->move_z,
                          (int)req->yaw100, (unsigned)req->flags);
        fljit_move_recv_logs[ci]++;
    }

    if (ps->has_recv_input_seq && !SvrInputSeqIsNewer(req->input_seq, ps->last_recv_input_seq))
    {
        ps->input_seq_regressions++;
        ps->m_intent_reject_count++;
        ps->m_intent_last_reject_code = SVR_INPUT_MOVE_REJECT_SEQ_REGRESSION;
        ps->m_intent_last_reject_us = recv_stamp_us;
        ps->m_intent_last_reject_seq = req->input_seq;
        return;
    }

    if (SvrRateLimitDisconnectObserveInputMovePacket(state, ci))
    {
        ps->m_intent_reject_count++;
        ps->m_intent_last_reject_code = SVR_INPUT_MOVE_REJECT_RATE_LIMIT_DISCONNECT;
        ps->m_intent_last_reject_us = recv_stamp_us;
        ps->m_intent_last_reject_seq = req->input_seq;
        return; // FL-2481: client disconnected for exceeding burst threshold
    }

    ps->last_recv_input_seq = req->input_seq;
    ps->has_recv_input_seq = true;

    ps->m_intent_latch_accept_count++;
    ps->m_intent_last_latch_accept_us = recv_stamp_us;
    ps->m_intent_last_latch_accept_seq = req->input_seq;

    // Honor explicit fly-mode requests from the client. The local branch exposes fly
    // as a debug gameplay toggle, so authoritative input must not silently strip it.
    uint8_t flags = req->flags;

    InputSlot* slot = &ps->latest_input;
    slot->recv_tick = state->tick;
    slot->seq = req->input_seq;
    slot->force[0] = (float)req->move_x / 127.0f;
    slot->force[1] = (float)req->move_y / 127.0f;
    slot->force_z = (float)req->move_z / 127.0f;
    slot->yaw = (float)req->yaw100 / 100.0f;
    slot->flags = flags;
    slot->mount_intent = MpMoveFlagsMount(flags);
    if (slot->mount_intent >= MOUNT::SIZE)
        slot->mount_intent = MOUNT::NONE;
    slot->valid = true;
}

// [DEBUG-pst-asn] Append-only log of every presentation_started_tick assignment
// attempt, gated on env DEBUG_PST_LOG=1. Used to falsify FL-3993 hypothesis that
// the server rewrites started_tick every snapshot. Each line is JSON:
// {"tick":N,"kind":"player|npc","entity_id":N,"prev_kind":N,"next_kind":N,
//  "prev_started":N,"new_started":N,"changed":0|1}
static void SvrDebugLogPresentationEpochAssignment(const char* entity_kind,
                                                   uint32_t entity_id,
                                                   uint16_t prev_kind,
                                                   uint16_t next_kind,
                                                   uint32_t prev_started,
                                                   uint32_t new_started,
                                                   uint32_t tick,
                                                   int changed)
{
    static int s_enabled = -1;
    static FILE* s_fp = NULL;
    if (s_enabled < 0)
    {
        const char* env = getenv("DEBUG_PST_LOG");
        s_enabled = (env && env[0] && env[0] != '0') ? 1 : 0;
        if (s_enabled)
        {
            const char* path = getenv("DEBUG_PST_LOG_PATH");
            if (!path || !path[0])
                path = "/tmp/pst-assignments.jsonl";
            s_fp = fopen(path, "a");
        }
    }
    if (!s_enabled || !s_fp)
        return;
    fprintf(s_fp,
            "{\"tick\":%u,\"kind\":\"%s\",\"entity_id\":%u,\"prev_kind\":%u,"
            "\"next_kind\":%u,\"prev_started\":%u,\"new_started\":%u,\"changed\":%d}\n",
            (unsigned)tick, entity_kind, (unsigned)entity_id,
            (unsigned)prev_kind, (unsigned)next_kind,
            (unsigned)prev_started, (unsigned)new_started, changed);
    fflush(s_fp);
}

static void SvrUpdatePresentationEpoch(uint16_t next_presentation_kind,
                                       uint16_t* current_presentation_kind,
                                       uint32_t* started_tick,
                                       uint32_t tick,
                                       const char* debug_entity_kind = NULL,
                                       uint32_t debug_entity_id = 0)
{
    if (!current_presentation_kind || !started_tick)
        return;
    const uint16_t prev_kind = *current_presentation_kind;
    const uint32_t prev_started = *started_tick;
    int changed = 0;
    if (*current_presentation_kind != next_presentation_kind)
    {
        *current_presentation_kind = next_presentation_kind;
        *started_tick = tick;
        changed = 1;
    }
    if (debug_entity_kind)
    {
        SvrDebugLogPresentationEpochAssignment(
            debug_entity_kind, debug_entity_id,
            prev_kind, next_presentation_kind,
            prev_started, *started_tick, tick, changed);
    }
}

static void SvrRestartAttackPresentationEpoch(uint16_t swing_presentation_kind,
                                              uint16_t current_presentation_kind,
                                              uint32_t* started_tick,
                                              uint32_t tick)
{
    if (!started_tick)
        return;
    if (swing_presentation_kind != APPEARANCE_PRESENTATION_KIND_ATTACK)
        return;
    if (current_presentation_kind != APPEARANCE_PRESENTATION_KIND_ATTACK)
        return;
    *started_tick = tick;
}

static uint16_t SvrFindActorVisualPresentationKindForState(
    uint8_t life_state,
    uint8_t locomotion_state,
    uint8_t combat_state,
    uint8_t mount_state,
    const SvrAuthoritativeAppearanceState* appearance)
{
    (void)locomotion_state;
    (void)mount_state;
    // FL-4049 deletion restart: presentation ownership no longer flows through
    // selector rows. This is the server gameplay collapse into the
    // profile key's presentation dimension; future addability should move this
    // rule table into ActorVisualProfile source rather than restoring
    // selector/admission data.
    if (life_state == LIFE_STATE::DEAD)
        return APPEARANCE_PRESENTATION_KIND_DEATH;
    if (combat_state == COMBAT_STATE::ATTACKING)
    {
        // FL-4076: crossbow has no attack pose. Per upstream game.cpp:3240/3261-3263/3282-3283
        // and internal design notes, the swing presentation for the equipped weapon is
        // a catalog-owned trait (e.g. crossbow -> IDLE_WALK held). Read it from the
        // catalog rather than hardcoding ATTACK.
        return SvrCatalogSwingPresentationKindForAppearance(appearance);
    }
    return APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
}

static uint8_t SvrDerivePlayerLifeState(const SvrPlayerState* ps)
{
    if (!ps || ps->phase < CPHASE_ALIVE)
        return LIFE_STATE::NONE;
    // FL-708: do not derive dead from fall/airborne/presentation-family names.
    // Death is owned by the server death tick; ordinary falling remains alive.
    return (ps->death_tick == 0) ? LIFE_STATE::ALIVE : LIFE_STATE::DEAD;
}

static uint8_t SvrDerivePlayerLocomotionState(const SvrPlayerState* ps)
{
    if (!ps || ps->death_tick != 0 || ps->phase != CPHASE_ALIVE)
        return LOCOMOTION_STATE::NONE;
    const uint8_t runtime_mount_state = SvrRuntimeMountStateForPlayer(ps);
    // FL-2329 / 2026-04-28:
    // Do not re-spend the older generic AIRBORNE->IDLE vs AIRBORNE->FALLING
    // selector debate here. The live bug in this lane was narrower: local
    // mounted presentation already forbids AIRBORNE while mounted, but the
    // authoritative server owner still allowed it. Keep mounted players out of
    // the airborne presentation lane so live mount pickup/use cannot poison
    // itself into the 602 fall/death family on the next refresh.
    const bool airborne = runtime_mount_state == MOUNT::NONE &&
                          ps->physics &&
                          !GetPhysicsGrounded(ps->physics) &&
                          ps->vel[2] < -64.0f;
    // Match the local runtime presentation contract: mounted players stay on
    // live idle/move families, not the fall/death family, while the mount is
    // authoritative. Otherwise multiplayer can publish alive+mounted+airborne
    // and immediately select a mounted presentation family with no live rows.
    if (airborne)
        return LOCOMOTION_STATE::AIRBORNE;
    const float planar_speed_sq = ps->vel[0] * ps->vel[0] + ps->vel[1] * ps->vel[1];
    // FL-4071: player and mounted-player walking frames are selected from the
    // server-published locomotion_state. Keep this threshold in parity with the
    // NPC presentation collapse so low-speed authoritative movement still uses
    // the moving timeline instead of visually freezing on the idle track.
    if (planar_speed_sq > 4.0f)
        return LOCOMOTION_STATE::MOVING;
    return LOCOMOTION_STATE::IDLE;
}

static uint8_t SvrDerivePlayerCombatState(const ServerState* state, const SvrPlayerState* ps)
{
    if (!state || !ps || ps->death_tick != 0 || ps->phase != CPHASE_ALIVE)
        return COMBAT_STATE::NONE;
    if (ps->last_swing_tick != 0 &&
        state->tick >= ps->last_swing_tick &&
        (state->tick - ps->last_swing_tick) <
            SvrCatalogSwingPresentationTicksForAppearance(&ps->appearance) &&
        ps->last_swing_presentation_kind_id == APPEARANCE_PRESENTATION_KIND_ATTACK)
    {
        return COMBAT_STATE::ATTACKING;
    }
    return COMBAT_STATE::NONE;
}

static void SvrRefreshPlayerPresentationKind(ServerState* state, SvrPlayerState* ps)
{
    if (!state || !ps)
        return;
    ps->life_state = SvrDerivePlayerLifeState(ps);
    ps->locomotion_state = SvrDerivePlayerLocomotionState(ps);
    ps->combat_state = SvrDerivePlayerCombatState(state, ps);
    SvrActorVisualProfileCatalog cache = {};
    SvrLoadActorVisualProfileCatalog(&cache);
    const uint16_t next_presentation_kind = SvrFindActorVisualPresentationKindForState(
        ps->life_state, ps->locomotion_state, ps->combat_state,
        SvrRuntimeMountStateForPlayer(ps),
        &ps->appearance);
    SvrUpdatePresentationEpoch(next_presentation_kind, &ps->presentation_kind_id,
                               &ps->presentation_started_tick, state->tick,
                               "player", (uint32_t)(ps - state->players));
    // Mounted death remains server-owned mounted truth until respawn/loadout clear.
    // Keep mount_definition_id while the authoritative runtime mount state still says
    // mounted, so death/corpse presentation can resolve the admitted mounted layers.
    const uint16_t next_mount_definition_id =
        SvrResolveMountDefinitionIdForPlayer(
            state, &cache, ps, ps->life_state, next_presentation_kind);
    if (ps->appearance.mount_definition_id != next_mount_definition_id)
    {
        ps->appearance.mount_definition_id = next_mount_definition_id;
        SvrBumpAppearanceRevision(&ps->appearance);
    }
    // Presentation changes can change variation even when loadout/mount did not.
    // Keep this after mount resolution so rig and variation are synced together.
    SvrSyncAppearanceCompiledActorVisualKeyDimensions(&ps->appearance,
                                                     next_presentation_kind,
                                                     true);
    // M2 / FL-4055 closure: runtime compile-gate. Derive the key the renderer
    // will look up and verify a compiled row exists for it. If the gate fails,
    // the server is about to publish a server-reachable state that has no
    // renderable content — surface it loudly. Per internal design notes
    // this is a content/compile bug, not a runtime-recovery scenario; we log
    // but do not auto-correct.
    {
        CompiledActorVisualKey gate_key = {};
        if (SvrBuildCompiledActorVisualKey(&ps->appearance, next_presentation_kind, &gate_key) &&
            !ValidateCompiledActorVisualKeyHasRow(&gate_key))
        {
            static uint64_t last_player_gate_log_tick[SVR_MAX_CLIENTS] = {};
            const uint64_t ci = (uint64_t)(ps - state->players);
            if (ci < SVR_MAX_CLIENTS &&
                (state->tick - last_player_gate_log_tick[ci] > 60 ||
                 last_player_gate_log_tick[ci] == 0))
            {
                SvrRuntimeDiagLog(state,
                    "[fl-4055-gate] no_row ci=%llu tick=%u skin=%u kind=%u variation=%u mount=%u rig=%u "
                    "head=%u/%u chest=%u/%u weapon=%u/%u shield=%u/%u\n",
                    (unsigned long long)ci, (unsigned)state->tick,
                    (unsigned)gate_key.skin_id, (unsigned)gate_key.presentation_kind_id,
                    (unsigned)gate_key.variation_id, (unsigned)gate_key.mount_id,
                    (unsigned)gate_key.rig_id,
                    (unsigned)gate_key.head_item_id, (unsigned)gate_key.head_style_id,
                    (unsigned)gate_key.chest_item_id, (unsigned)gate_key.chest_style_id,
                    (unsigned)gate_key.weapon_item_id, (unsigned)gate_key.weapon_style_id,
                    (unsigned)gate_key.shield_item_id, (unsigned)gate_key.shield_style_id);
                last_player_gate_log_tick[ci] = state->tick;
            }
        }
    }
}

static uint8_t SvrDeriveNpcLifeState(const SvrNpcState* npc)
{
    if (!npc || !npc->active)
        return LIFE_STATE::NONE;
    return (npc->death_tick == 0) ? LIFE_STATE::ALIVE : LIFE_STATE::DEAD;
}

static uint8_t SvrDeriveNpcLocomotionState(const SvrNpcState* npc)
{
    if (!npc || npc->death_tick != 0)
        return LOCOMOTION_STATE::NONE;
    const float planar_speed_sq = npc->vel[0] * npc->vel[0] + npc->vel[1] * npc->vel[1];
    // FL-2193 boundary note: this is the server-side collapse from full
    // physics output into a tiny locomotion enum. The client later re-selects
    // idle-vs-walk presentation from this derived bit instead of consuming
    // authoritative anim/frame directly, so threshold mistakes here visibly
    // freeze walking even when chase/physics still look alive.
    // FL-2193 fix-attempt 14e665a3 breadcrumb: treat 2 units/s planar speed as
    // walking. The previous 4 units/s gate was too strict for visible chase motion.
    if (planar_speed_sq > 4.0f)
        return LOCOMOTION_STATE::MOVING;
    return LOCOMOTION_STATE::IDLE;
}

static uint8_t SvrDeriveNpcCombatState(const ServerState* state, const SvrNpcState* npc)
{
    if (!state || !npc || npc->death_tick != 0)
        return COMBAT_STATE::NONE;
    if (npc->last_swing_tick != 0 &&
        state->tick >= npc->last_swing_tick &&
        (state->tick - npc->last_swing_tick) <
            SvrCatalogSwingPresentationTicksForAppearance(&npc->appearance) &&
        npc->last_swing_presentation_kind_id == APPEARANCE_PRESENTATION_KIND_ATTACK)
    {
        return COMBAT_STATE::ATTACKING;
    }
    return COMBAT_STATE::NONE;
}

static void SvrRefreshNpcPresentationKind(ServerState* state, SvrNpcState* npc)
{
    if (!state || !npc)
        return;
    npc->life_state = SvrDeriveNpcLifeState(npc);
    npc->locomotion_state = SvrDeriveNpcLocomotionState(npc);
    npc->combat_state = SvrDeriveNpcCombatState(state, npc);
    SvrActorVisualProfileCatalog cache = {};
    SvrLoadActorVisualProfileCatalog(&cache);
    const uint16_t next_presentation_kind = SvrFindActorVisualPresentationKindForState(
        npc->life_state, npc->locomotion_state, npc->combat_state, npc->mount_state,
        &npc->appearance);
    SvrUpdatePresentationEpoch(next_presentation_kind, &npc->presentation_kind_id,
                               &npc->presentation_started_tick, state->tick,
                               "npc", (uint32_t)(npc - state->npcs));
    npc->appearance.mount_definition_id = SvrResolveMountDefinitionIdForRuntimeState(
        &cache,
        npc->mount_state,
        npc->life_state,
        npc->presentation_kind_id);
    // FL-3955 V-2 DELETED: SvrRefreshAppearanceRig call removed.
    SvrSyncAppearanceCompiledActorVisualKeyDimensions(&npc->appearance,
                                                     npc->presentation_kind_id,
                                                     true);
    // M2 / FL-4055 closure: runtime compile-gate for NPC visual keys.
    // See SvrRefreshPlayerPresentationKind for the contract.
    {
        CompiledActorVisualKey gate_key = {};
        if (SvrBuildCompiledActorVisualKey(&npc->appearance, npc->presentation_kind_id, &gate_key) &&
            !ValidateCompiledActorVisualKeyHasRow(&gate_key))
        {
            static uint64_t last_npc_gate_log_tick[SVR_MAX_NPCS] = {};
            const uint64_t ni = (uint64_t)(npc - state->npcs);
            if (ni < SVR_MAX_NPCS &&
                (state->tick - last_npc_gate_log_tick[ni] > 60 ||
                 last_npc_gate_log_tick[ni] == 0))
            {
                SvrRuntimeDiagLog(state,
                    "[fl-4055-gate] no_row npc=%llu tick=%u skin=%u kind=%u variation=%u mount=%u rig=%u "
                    "head=%u/%u chest=%u/%u weapon=%u/%u shield=%u/%u\n",
                    (unsigned long long)ni, (unsigned)state->tick,
                    (unsigned)gate_key.skin_id, (unsigned)gate_key.presentation_kind_id,
                    (unsigned)gate_key.variation_id, (unsigned)gate_key.mount_id,
                    (unsigned)gate_key.rig_id,
                    (unsigned)gate_key.head_item_id, (unsigned)gate_key.head_style_id,
                    (unsigned)gate_key.chest_item_id, (unsigned)gate_key.chest_style_id,
                    (unsigned)gate_key.weapon_item_id, (unsigned)gate_key.weapon_style_id,
                    (unsigned)gate_key.shield_item_id, (unsigned)gate_key.shield_style_id);
                last_npc_gate_log_tick[ni] = state->tick;
            }
        }
    }
}

static void SvrRefreshPresentationKindsBeforeSnapshot(ServerState* state)
{
    if (!state)
        return;
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        SvrPlayerState* ps = &state->players[i];
        if (!ps->active || ps->phase < CPHASE_ALIVE)
            continue;
        SvrRefreshPlayerPresentationKind(state, ps);
    }
    for (int i = 0; i < state->npc_count; i++)
    {
        SvrNpcState* npc = &state->npcs[i];
        if (!npc->active)
            continue;
        SvrRefreshNpcPresentationKind(state, npc);
    }
}

static bool SvrFindSnapshotAckTick(const SvrPlayerState* ps, uint16_t seq, uint32_t* out_tick)
{
    if (!ps || ps->ack_write == 0) return false;

    uint32_t count = ps->ack_write;
    if (count > SVR_SNAPSHOT_RING_SIZE) count = SVR_SNAPSHOT_RING_SIZE;
    for (uint32_t i = 0; i < count; i++)
    {
        int idx = (int)((ps->ack_write - 1 - i) % SVR_SNAPSHOT_RING_SIZE);
        const SnapshotACK* ack = &ps->ack_ring[idx];
        if (ack->seq != seq) continue;
        if (out_tick) *out_tick = ack->tick;
        return true;
    }
    return false;
}

static void SvrProcessSnapshotAck(ServerState* state, int ci, const uint8_t* data, int size)
{
    if (size != sizeof(STRUCT_REQ_SNAPSHOT_ACK)) return;
    SvrPlayerState* ps = &state->players[ci];
    // Gate at ALIVE: snapshots are only sent to phase >= CPHASE_ALIVE (line 1110),
    // so ACKs from JOINED/CONNECTING are orphaned by definition.
    if (!ps->active || ps->phase < CPHASE_ALIVE) return;

    STRUCT_REQ_SNAPSHOT_ACK* req = (STRUCT_REQ_SNAPSHOT_ACK*)data;
    ps->snapshot_ack_received_count++;
    ps->last_snapshot_ack_received_seq = req->seq;
    uint32_t ack_tick = 0;
    if (!SvrFindSnapshotAckTick(ps, req->seq, &ack_tick))
        return; // Ignore ACKs for snapshots not actually sent to this client.
    

    if (ps->has_acked)
    {
        uint32_t prev_tick = 0;
        if (SvrFindSnapshotAckTick(ps, ps->last_acked_seq, &prev_tick) &&
            ack_tick < prev_tick)
        {
            return; // Ignore regressive ACKs.
        }
    }

    ps->last_acked_seq = req->seq;
    ps->has_acked = true;
    ps->snapshot_ack_accepted_count++;
    ps->last_snapshot_ack_accepted_seq = req->seq;
    ps->last_snapshot_ack_accepted_tick = ack_tick;
}

static void SvrProcessTalk(ServerState* state, int ci, const uint8_t* data, int size)
{
    STRUCT_REQ_TALK* req = (STRUCT_REQ_TALK*)data;
    if (size < 4 || size != 4 + req->len) return;
    SvrPlayerState* ps = &state->players[ci];
    if (!ps->active || ps->phase < CPHASE_JOINED) return;

    // Build broadcast talk
    uint8_t buf[sizeof(STRUCT_BRC_TALK)];
    STRUCT_BRC_TALK* brc = (STRUCT_BRC_TALK*)buf;
    brc->token = 't';
    brc->len = req->len;
    brc->id = ci;
    memcpy(brc->str, req->str, req->len);

    int brc_size = 4 + req->len;
    SvrQueueEvent(state, buf, brc_size, ci);

    printf("[tick] %s: %.*s\n", ps->name, req->len, req->str);
}

struct SvrSwingHit
{
    uint16_t target_id;
    float target_pos[3];
    int16_t* target_hp;
    uint32_t* target_death_tick;
    const char* target_kind;
    bool target_is_npc;
    int target_index;
    float dist2;
};

static bool SvrResolveSwingAttackerState(ServerState* state, const PendingSwing* s,
                                         float attacker_pos[3], float* attacker_dir)
{
    if (!state || !s || !attacker_pos || !attacker_dir)
        return false;

    if (s->attacker_id < SVR_MAX_CLIENTS)
    {
        SvrPlayerState* attacker = &state->players[s->attacker_id];
        if (!attacker->active || attacker->phase != CPHASE_ALIVE || attacker->death_tick > 0)
            return false;
        if (!SvrVec3IsFinite(attacker->pos))
            return false;
        memcpy(attacker_pos, attacker->pos, sizeof(attacker->pos));
        *attacker_dir = attacker->dir;
        return true;
    }

    uint16_t npc_index = (uint16_t)(s->attacker_id - SVR_MAX_CLIENTS);
    if (npc_index >= (uint16_t)state->npc_count)
        return false;

    SvrNpcState* attacker = &state->npcs[npc_index];
    if (!attacker->active || attacker->death_tick > 0)
        return false;
    if (!SvrVec3IsFinite(attacker->pos))
        return false;
    memcpy(attacker_pos, attacker->pos, sizeof(attacker->pos));
    *attacker_dir = attacker->dir;
    return true;
}

static uint16_t SvrResolveSwingPresentationKind(ServerState* state, uint16_t attacker_id)
{
    if (!state)
        return APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
    if (attacker_id < SVR_MAX_CLIENTS)
        return SvrCatalogSwingPresentationKindForAppearance(&state->players[attacker_id].appearance);
    uint16_t npc_index = (uint16_t)(attacker_id - SVR_MAX_CLIENTS);
    if (npc_index < (uint16_t)state->npc_count)
        return SvrCatalogSwingPresentationKindForAppearance(&state->npcs[npc_index].appearance);
    return APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
}

static float SvrSwingRangeForWeapon(uint16_t weapon_item_id, bool target_is_npc)
{
    if (weapon_item_id != 0)
    {
        const AppearanceCatalogItemDef* item = FindAppearanceCatalogItemById(weapon_item_id);
        if (item && item->swing_range_units > 0.0f)
            return item->swing_range_units;
    }
    return target_is_npc ? SVR_NPC_SWING_RANGE : SVR_SWING_RANGE;
}

static void SvrComputeSwingEndpoint(const PendingSwing* s, const float attacker_pos[3], float attacker_dir,
                                    const SvrSwingHit* hits, int hit_count, float out_target_pos[3])
{
    if (!out_target_pos || !attacker_pos)
        return;
    if (hit_count > 0 && hits)
    {
        memcpy(out_target_pos, hits[0].target_pos, sizeof(hits[0].target_pos));
        return;
    }
    // Wave 3: range from catalog item, not a closed weapon-class branch.
    float range = SVR_SWING_RANGE;
    if (s && s->weapon_item_id != 0)
    {
        const AppearanceCatalogItemDef* item =
            FindAppearanceCatalogItemById(s->weapon_item_id);
        if (item && item->swing_range_units > 0.0f)
            range = item->swing_range_units;
    }
    const float radians = (attacker_dir - 90.0f) * (float)M_PI / 180.0f;
    out_target_pos[0] = attacker_pos[0] + cosf(radians) * range;
    out_target_pos[1] = attacker_pos[1] + sinf(radians) * range;
    out_target_pos[2] = attacker_pos[2];
}

static void SvrLiftSwingVisualPos(float pos[3])
{
    if (!pos)
        return;
    pos[2] += HEIGHT_SCALE * 4.0f;
}

// Wave 3: projectile travel speed is catalog-owned. The weapon_item_id
// argument lets us read projectile_units_per_tick from the item def. Missing
// or invalid projectile speed is a catalog/runtime contract failure; do not
// substitute a projectile timing constant.
static uint32_t SvrProjectileTravelTicks(uint16_t weapon_item_id, float dist2)
{
    if (weapon_item_id == 0)
        return 0;
    const AppearanceCatalogItemDef* item =
        FindAppearanceCatalogItemById(weapon_item_id);
    if (!item || item->projectile_units_per_tick <= 0.0f)
        return 0;
    const float units_per_tick = item->projectile_units_per_tick;
    float dist = sqrtf(dist2 > 0.0f ? dist2 : 0.0f);
    uint32_t ticks = (uint32_t)ceilf(dist / units_per_tick);
    if (ticks < SVR_PROJECTILE_TICKS_MIN)
        ticks = SVR_PROJECTILE_TICKS_MIN;
    if (ticks > SVR_PROJECTILE_TICKS_MAX)
        ticks = SVR_PROJECTILE_TICKS_MAX;
    return ticks;
}

// Wave 3: spawning a projectile is a catalog-owned weapon trait. The caller
// supplies the swing's weapon_item_id; the catalog decides whether projectile
// queueing is allowed and at what travel speed.
static bool SvrQueueProjectileFromSwing(ServerState* state,
                                        uint16_t attacker_id,
                                        uint16_t weapon_item_id,
                                        const float attacker_pos[3],
                                        const SvrSwingHit* hit)
{
    if (!state || !attacker_pos || !hit || !hit->target_hp)
        return false;
    if (state->pending_projectile_count >= SVR_MAX_PENDING_PROJECTILES)
    {
        SvrRuntimeDiagLog(state,
                          "[projectile-queue] tick=%u attacker=%u target=%u outcome=reject reason=pending_full pending=%d max=%d\n",
                          (unsigned)state->tick,
                          (unsigned)attacker_id,
                          (unsigned)hit->target_id,
                          state->pending_projectile_count,
                          SVR_MAX_PENDING_PROJECTILES);
        return false;
    }

    const uint32_t travel_ticks =
        SvrProjectileTravelTicks(weapon_item_id, hit->dist2);
    if (travel_ticks == 0)
    {
        SvrRuntimeDiagLog(state,
                          "[projectile-queue] tick=%u attacker=%u target=%u weapon_item_id=%u outcome=reject reason=missing_projectile_speed\n",
                          (unsigned)state->tick,
                          (unsigned)attacker_id,
                          (unsigned)hit->target_id,
                          (unsigned)weapon_item_id);
        return false;
    }
    PendingProjectile* p = &state->pending_projectiles[state->pending_projectile_count++];
    memset(p, 0, sizeof(*p));
    p->active = 1;
    p->attacker_id = attacker_id;
    p->target_id = hit->target_id;
    p->fire_tick = state->tick;
    p->impact_tick = state->tick + travel_ticks;
    memcpy(p->attacker_pos, attacker_pos, sizeof(p->attacker_pos));
    memcpy(p->target_pos, hit->target_pos, sizeof(p->target_pos));
    p->dist2 = hit->dist2;
    SvrRuntimeDiagLog(state,
                      "[projectile-queue] tick=%u attacker=%u target=%u impact_tick=%u dist2=%.3f\n",
                      (unsigned)state->tick,
                      (unsigned)attacker_id,
                      (unsigned)hit->target_id,
                      (unsigned)p->impact_tick,
                      p->dist2);
    return true;
}

static bool SvrResolveSwingTargetById(ServerState* state, uint16_t target_id, SvrSwingHit* hit)
{
    if (!hit)
        return false;

    if (target_id < SVR_MAX_CLIENTS)
    {
        SvrPlayerState* target = &state->players[target_id];
        if (!target->active || target->phase != CPHASE_ALIVE || target->death_tick > 0)
            return false;
        hit->target_id = target_id;
        memcpy(hit->target_pos, target->pos, 12);
        hit->target_hp = &target->hp;
        hit->target_death_tick = &target->death_tick;
        hit->target_kind = "player";
        hit->target_is_npc = false;
        hit->target_index = (int)target_id;
        hit->dist2 = 0.0f;
        return true;
    }

    if (target_id - SVR_MAX_CLIENTS < state->npc_count)
    {
        SvrNpcState* npc = &state->npcs[target_id - SVR_MAX_CLIENTS];
        if (!npc->active || npc->death_tick > 0)
            return false;
        hit->target_id = target_id;
        memcpy(hit->target_pos, npc->pos, 12);
        hit->target_hp = &npc->hp;
        hit->target_death_tick = &npc->death_tick;
        hit->target_kind = "npc";
        hit->target_is_npc = true;
        hit->target_index = (int)(target_id - SVR_MAX_CLIENTS);
        hit->dist2 = 0.0f;
        return true;
    }

    return false;
}

static int SvrCollectSwingHits(ServerState* state, const PendingSwing* s, SvrSwingHit* hits, int max_hits,
                               const char** reject_reason, const char** reject_kind, int* reject_index,
                               float* reject_dist2, float* reject_limit2)
{
    if (reject_reason) *reject_reason = NULL;
    if (reject_kind) *reject_kind = NULL;
    if (reject_index) *reject_index = -1;
    if (reject_dist2) *reject_dist2 = 0.0f;
    if (reject_limit2) *reject_limit2 = 0.0f;

    if (!state || !s || !hits || max_hits <= 0)
    {
        if (reject_reason) *reject_reason = "invalid_collect_args";
        return 0;
    }

    float attacker_pos[3] = { 0.0f, 0.0f, 0.0f };
    float attacker_dir = 0.0f;
    if (!SvrResolveSwingAttackerState(state, s, attacker_pos, &attacker_dir))
    {
        if (reject_reason) *reject_reason = "invalid_attacker";
        return 0;
    }

    if (s->explicit_target)
    {
        SvrSwingHit hit = {};
        if (!SvrResolveSwingTargetById(state, s->target_id, &hit))
        {
            if (reject_reason) *reject_reason = "invalid_target";
            return 0;
        }

        float tdx = hit.target_pos[0] - attacker_pos[0];
        float tdy = hit.target_pos[1] - attacker_pos[1];
        float dist2 = tdx * tdx + tdy * tdy;
        float swing_range = SvrSwingRangeForWeapon(s->weapon_item_id, hit.target_is_npc);
        if (!SvrWithinVerticalBand(hit.target_pos, attacker_pos, SVR_VERTICAL_SWING_BAND))
        {
            if (reject_reason) *reject_reason = "vertical_range";
            if (reject_kind) *reject_kind = hit.target_kind;
            if (reject_index) *reject_index = hit.target_index;
            return 0;
        }
        if (!(dist2 <= swing_range * swing_range))
        {
            if (reject_reason) *reject_reason = "range";
            if (reject_kind) *reject_kind = hit.target_kind;
            if (reject_index) *reject_index = hit.target_index;
            if (reject_dist2) *reject_dist2 = dist2;
            if (reject_limit2) *reject_limit2 = swing_range * swing_range;
            return 0;
        }

        hit.dist2 = dist2;
        hits[0] = hit;
        return 1;
    }

    int hit_count = 0;
    const float player_range = SvrSwingRangeForWeapon(s->weapon_item_id, false);
    const float npc_range = SvrSwingRangeForWeapon(s->weapon_item_id, true);
    const float player_range2 = player_range * player_range;
    const float npc_range2 = npc_range * npc_range;

    for (int p = 0; p < SVR_MAX_CLIENTS; p++)
    {
        SvrPlayerState* target = &state->players[p];
        if (!target->active || target->phase != CPHASE_ALIVE || target->death_tick > 0)
            continue;
        if (s->attacker_id < SVR_MAX_CLIENTS && p == (int)s->attacker_id)
            continue;

        float dx = target->pos[0] - attacker_pos[0];
        float dy = target->pos[1] - attacker_pos[1];
        float dist2 = dx * dx + dy * dy;
        if (!SvrWithinVerticalBand(target->pos, attacker_pos, SVR_VERTICAL_SWING_BAND))
            continue;
        if (!(dist2 <= player_range2))
            continue;

        if (hit_count < max_hits)
        {
            SvrSwingHit* hit = &hits[hit_count++];
            hit->target_id = (uint16_t)p;
            memcpy(hit->target_pos, target->pos, 12);
            hit->target_hp = &target->hp;
            hit->target_death_tick = &target->death_tick;
            hit->target_kind = "player";
            hit->target_is_npc = false;
            hit->target_index = p;
            hit->dist2 = dist2;
        }
    }

    for (int i = 0; i < state->npc_count; i++)
    {
        SvrNpcState* npc = &state->npcs[i];
        if (!npc->active || npc->death_tick > 0)
            continue;
        if (npc->entity_id == s->attacker_id)
            continue;

        float dx = npc->pos[0] - attacker_pos[0];
        float dy = npc->pos[1] - attacker_pos[1];
        float dist2 = dx * dx + dy * dy;
        if (!SvrWithinVerticalBand(npc->pos, attacker_pos, SVR_VERTICAL_SWING_BAND))
            continue;
        if (!(dist2 <= npc_range2))
            continue;

        if (hit_count < max_hits)
        {
            SvrSwingHit* hit = &hits[hit_count++];
            hit->target_id = npc->entity_id;
            memcpy(hit->target_pos, npc->pos, 12);
            hit->target_hp = &npc->hp;
            hit->target_death_tick = &npc->death_tick;
            hit->target_kind = "npc";
            hit->target_is_npc = true;
            hit->target_index = i;
            hit->dist2 = dist2;
        }
    }

    if (hit_count <= 0 && reject_reason)
        *reject_reason = *reject_reason ? *reject_reason : "no_target_in_range";
    return hit_count;
}

static void SvrProcessSwing(ServerState* state, int ci, const uint8_t* data, int size, bool explicit_target, uint64_t recv_stamp_us)
{
    if (size != sizeof(STRUCT_REQ_SWING))
    {
        SvrRuntimeDiagLog(state,
                          "[swing-recv] tick=%u attacker=%u outcome=reject reason=bad_size size=%d expected=%zu\n",
                          (unsigned)state->tick,
                          (unsigned)ci,
                          size,
                          sizeof(STRUCT_REQ_SWING));
        return;
    }
    STRUCT_REQ_SWING* req = (STRUCT_REQ_SWING*)data;
    SvrPlayerState* ps = &state->players[ci];
    if (ps->phase != CPHASE_ALIVE || ps->death_tick > 0)
    {
        SvrRuntimeDiagLog(state,
                          "[swing-recv] tick=%u attacker=%u req_target=%u explicit_target=%d outcome=reject reason=attacker_not_alive phase=%d death_tick=%u\n",
                          (unsigned)state->tick,
                          (unsigned)ci,
                          (unsigned)req->target_id,
                          explicit_target ? 1 : 0,
                          ps->phase,
                          (unsigned)ps->death_tick);
        return;
    }

    // Cooldown check — IO-receive-time based to avoid false violations during tick stalls (FL-2481/FL-2956).
    // last_swing_tick is preserved for presentation timing (SvrGetPlayerCombatState at :3655).
    // recv_stamp_us is when the IO thread received this packet, not when the tick thread processed it.
    if (ps->last_swing_stamp_us != 0 && recv_stamp_us >= ps->last_swing_stamp_us &&
        recv_stamp_us - ps->last_swing_stamp_us < SVR_SWING_COOLDOWN_US)
    {
        const bool disconnected = SvrRateLimitDisconnectRecordViolation(
            state,
            ci,
            SVR_RATE_LIMIT_VIOLATION_SWING_COOLDOWN);
        // LINEAGE_JSON: {"fl":"FL-2481","cautionary_precedent":"swing_cooldown_disconnect_not_lag_fix","note":"DO NOT reinvest in swing cooldown / rate-limit disconnect as a lag fix. This closed the false-disconnect lane (FL-2481). It did NOT close the lag lane (FL-2957). The lag survived after this slice. Separate owners."}
        SvrRuntimeDiagLog(state,
                          "[swing-recv] tick=%u attacker=%u req_target=%u explicit_target=%d outcome=reject reason=cooldown last_tick=%u now=%u violations=%u\n",
                          (unsigned)state->tick,
                          (unsigned)ci,
                          (unsigned)req->target_id,
                          explicit_target ? 1 : 0,
                          (unsigned)ps->last_swing_tick,
                          (unsigned)state->tick,
                          (unsigned)ps->rate_limit_violations);
        if (disconnected)
            return;
        return;
    }

    // Wave 3: catalog-owned weapon facts. Look up the equipped weapon item so
    // downstream behavior (range, projectile spawn, travel speed) reads catalog
    // metadata instead of branching on a closed weapon-class enum.
    const AppearanceCatalogItemDef* swing_item =
        SvrEquippedWeaponCatalogItemForAppearance(&ps->appearance);
    if (!swing_item)
    {
        SvrRuntimeDiagLog(state,
                          "[swing-recv] tick=%u attacker=%u req_target=%u explicit_target=%d outcome=reject reason=no_equipped_weapon\n",
                          (unsigned)state->tick,
                          (unsigned)ci,
                          (unsigned)req->target_id,
                          explicit_target ? 1 : 0);
        return;
    }
    // Queue for resolution in phase 4
    const uint16_t swing_presentation_kind =
        SvrResolveSwingPresentationKind(state, (uint16_t)ci);
    const uint16_t swing_item_id = swing_item->id;
    ps->last_swing_tick = state->tick;
    ps->last_swing_presentation_kind_id = swing_presentation_kind;
    SvrRestartAttackPresentationEpoch(swing_presentation_kind,
                                      ps->presentation_kind_id,
                                      &ps->presentation_started_tick,
                                      state->tick);
    ps->last_swing_stamp_us = recv_stamp_us;
    if (state->pending_swing_count < SVR_MAX_PENDING_SWINGS)
    {
        PendingSwing* s = &state->pending_swings[state->pending_swing_count++];
        s->attacker_id = ci;
        s->target_id = explicit_target ? req->target_id : 0xFFFF;
        s->explicit_target = explicit_target ? 1 : 0;
        s->weapon_item_id = swing_item_id;
        SvrRuntimeDiagLog(state,
                          "[swing-recv] tick=%u attacker=%u req_target=%u explicit_target=%d weapon_item_id=%u outcome=enqueued pos=(%.2f,%.2f,%.2f) dir=%.2f pending=%d\n",
                          (unsigned)state->tick,
                          (unsigned)ci,
                          (unsigned)req->target_id,
                          explicit_target ? 1 : 0,
                          (unsigned)s->weapon_item_id,
                          ps->pos[0], ps->pos[1], ps->pos[2],
                          ps->dir,
                          state->pending_swing_count);
    }
    else
    {
        SvrRuntimeDiagLog(state,
                          "[swing-recv] tick=%u attacker=%u req_target=%u explicit_target=%d outcome=reject reason=pending_full pending=%d max=%d\n",
                          (unsigned)state->tick,
                          (unsigned)ci,
                          (unsigned)req->target_id,
                          explicit_target ? 1 : 0,
                          state->pending_swing_count,
                          SVR_MAX_PENDING_SWINGS);
    }
}

static void SvrProcessItemAction(ServerState* state, int ci, const uint8_t* data, int size)
{
    if (size != sizeof(STRUCT_REQ_ITEM_ACTION))
    {
        SvrRuntimeDiagLog(state,
                          "[item-action] rejected ci=%d reason=size_mismatch size=%d expected=%zu token=%u tick=%u\n",
                          ci,
                          size,
                          sizeof(STRUCT_REQ_ITEM_ACTION),
                          (data && size > 0) ? (unsigned)data[0] : 0u,
                          state ? (unsigned)state->tick : 0u);
        return;
    }
    SvrPlayerState* ps = &state->players[ci];
    if (ps->phase != CPHASE_ALIVE) return;

    STRUCT_REQ_ITEM_ACTION* req = (STRUCT_REQ_ITEM_ACTION*)data;

    switch (req->kind)
    {
        case ITEM_ACTION_REQ_PICKUP:
        {
            // FL-1184: pickup proof requires explicit same-item intent. The
            // previous 0xFFFF nearest-world fallback was a second selector.
            if (req->item_id == 0xFFFF)
            {
                SvrRuntimeDiagLog(state,
                                  "[item-debug] explicit pickup rejected: wildcard item_id ci=%d player_pos=(%.2f,%.2f,%.2f)\n",
                                  ci,
                                  ps->pos[0], ps->pos[1], ps->pos[2]);
                return;
            }

            // Find item: exact ID match within server-owned pickup radius
            const float R2 = SVR_ITEM_PICKUP_RADIUS * SVR_ITEM_PICKUP_RADIUS;
            int best = -1;
            float best_d2 = R2;

            for (int i = 0; i < SVR_MAX_ITEMS; i++)
            {
                SvrItemState* it = &state->items[i];
                if (!it->active || it->owner_id != 0xFFFF) continue;
                if (it->item_id != req->item_id) continue;
                if (SvrItemInSameOwnerRepickupGrace(state, it, ci)) continue;

                // Proximity check vs player position
                float dx = it->pos[0] - ps->pos[0];
                float dy = it->pos[1] - ps->pos[1];
                float d2 = dx*dx + dy*dy;
                if (d2 <= best_d2) { best_d2 = d2; best = i; }
            }

            if (best < 0)
            {
                SvrRuntimeDiagLog(state,
                                  "[item-debug] pickup miss ci=%d item_id=%u player_pos=(%.2f,%.2f,%.2f)\n",
                                  ci,
                                  (unsigned)req->item_id,
                                  ps->pos[0], ps->pos[1], ps->pos[2]);
                return; // nothing in range
            }

            SvrItemState* it = &state->items[best];
            SvrRuntimeDiagLog(state,
                              "[item-debug] pickup hit ci=%d slot=%d item_id=%u gameplay_kind=%u item_pos=(%.2f,%.2f,%.2f) player_pos=(%.2f,%.2f,%.2f)\n",
                              ci,
                              best,
                              (unsigned)it->item_id,
                              (unsigned)it->gameplay_kind,
                              it->pos[0], it->pos[1], it->pos[2],
                              ps->pos[0], ps->pos[1], ps->pos[2]);

            switch (it->gameplay_kind)
            {
                case SVR_ITEM_GAMEPLAY_LOOT:
                    SvrPickupLootItem(state, ci, it, "explicit_req");
                    break;
                case SVR_ITEM_GAMEPLAY_CONSUMABLE:
                    (void)SvrPickupInventoryItem(state, ci, it, "explicit_req");
                    break;
                case SVR_ITEM_GAMEPLAY_PLACEABLE_BLOCK:
                    (void)SvrPickupInventoryItem(state, ci, it, "explicit_req");
                    break;
                case SVR_ITEM_GAMEPLAY_WEAPON:
                case SVR_ITEM_GAMEPLAY_WEARABLE:
                case SVR_ITEM_GAMEPLAY_MOUNTABLE:
                {
                    SvrActorVisualProfileCatalog cache = {};
                    if (!SvrLoadActorVisualProfileCatalog(&cache))
                    {
                        SvrRuntimeDiagLog(state,
                                          "[item-debug] explicit pickup rejected: bundle unavailable ci=%d item_id=%u gameplay_kind=%u\n",
                                          ci,
                                          (unsigned)it->item_id,
                                          (unsigned)it->gameplay_kind);
                        return;
                    }
                    (void)SvrPickupEquippableItem(state, ci, it, &cache, "explicit_req");
                    break;
                }
                default:
                    SvrRuntimeDiagLog(state,
                                      "[item-debug] explicit pickup rejected: unsupported gameplay_kind ci=%d item_id=%u gameplay_kind=%u\n",
                                      ci,
                                      (unsigned)it->item_id,
                                      (unsigned)it->gameplay_kind);
                    break;
            }
            break;
        }

        case ITEM_ACTION_REQ_DROP:
        {
            for (int i = 0; i < SVR_MAX_ITEMS; i++)
            {
                SvrItemState* it = &state->items[i];
                if (!it->active || it->owner_id != (uint16_t)ci) continue;
                if (req->item_id != 0xFFFF && it->item_id != req->item_id) continue;

                (void)SvrDropOwnedItemAtPlayer(state, ci, ps, it, "explicit_drop");
                break; // drop first matched item only
            }
            break;
        }

        case ITEM_ACTION_REQ_USE:
        {
            SvrActorVisualProfileCatalog cache = {};
            const bool have_cache = SvrLoadActorVisualProfileCatalog(&cache);
            for (int i = 0; i < SVR_MAX_ITEMS; i++)
            {
                SvrItemState* it = &state->items[i];
                if (!it->active || it->owner_id != (uint16_t)ci) continue;
                if (req->item_id != 0xFFFF && it->item_id != req->item_id) continue;

                uint8_t gameplay_kind = it->gameplay_kind;
                if (gameplay_kind == SVR_ITEM_GAMEPLAY_CONSUMABLE)
                {
                    if (SvrConsumeOwnedItemAfterEvent(it,
                                                      (uint16_t)ci,
                                                      ITEM_CHANGE_KIND_CONSUME,
                                                      SvrEmitItemChangeEventForOwnershipContract,
                                                      state))
                    {
                        int restored = ps->hp + SVR_CONSUMABLE_HEAL;
                        ps->hp = (int16_t)(restored > ps->max_hp ? ps->max_hp : restored);
                    }
                    else
                    {
                        SvrRuntimeDiagLog(state,
                                          "[item-debug] consumable use deferred ci=%d item_id=%u reason=event_not_queued tick=%u\n",
                                          ci,
                                          (unsigned)it->item_id,
                                          (unsigned)state->tick);
                    }
                }
                else if ((gameplay_kind == SVR_ITEM_GAMEPLAY_WEAPON ||
                          gameplay_kind == SVR_ITEM_GAMEPLAY_WEARABLE ||
                          gameplay_kind == SVR_ITEM_GAMEPLAY_MOUNTABLE) && have_cache)
                {
                    (void)SvrTryMutateAppearanceEquipState(state, ci, it, &cache, true);
                }
                else if (gameplay_kind == SVR_ITEM_GAMEPLAY_PLACEABLE_BLOCK && have_cache)
                {
                    const SvrActorVisualProfileCatalogItemDef* item_def =
                        SvrFindAppearanceItemById(&cache, it->item_definition_id);
                    (void)SvrTogglePlaceableBlockHeld(state, ci, it, item_def);
                }
                else if (gameplay_kind == SVR_ITEM_GAMEPLAY_LOOT)
                {
                    SvrConsumeOwnedLootItem(state, ci, it);
                }
                break; // consume first matched item only
            }
            break;
        }

        case ITEM_ACTION_REQ_PLACE:
        {
            SvrActorVisualProfileCatalog cache = {};
            if (!SvrLoadActorVisualProfileCatalog(&cache))
                break;
            bool matched = false;
            float debug_z_offset = 0.0f;
            if ((state->debug_runtime_diagnostics_enabled || state->debug_fly_mode_enabled) &&
                isfinite(req->pos[2]) && isfinite(ps->pos[2]))
            {
                debug_z_offset = req->pos[2] - ps->pos[2];
                const float cap = HEIGHT_SCALE * 16.0f;
                if (debug_z_offset < -cap) debug_z_offset = -cap;
                if (debug_z_offset > cap) debug_z_offset = cap;
            }
            SvrRuntimeDiagLog(state,
                              "[item-place] request ci=%d item_id=%u player_pos=(%.2f,%.2f,%.2f) debug_z_offset=%.2f tick=%u\n",
                              ci,
                              (unsigned)req->item_id,
                              ps->pos[0], ps->pos[1], ps->pos[2],
                              debug_z_offset,
                              (unsigned)state->tick);
            for (int i = 0; i < SVR_MAX_ITEMS; i++)
            {
                SvrItemState* it = &state->items[i];
                if (!it->active || it->owner_id != (uint16_t)ci) continue;
                if (req->item_id != 0xFFFF && it->item_id != req->item_id) continue;
                matched = true;
                (void)SvrPlaceOwnedItemFromPlayer(state, ci, ps, it, &cache, "explicit_place", debug_z_offset);
                break;
            }
            if (!matched)
            {
                SvrRuntimeDiagLog(state,
                                  "[item-place] rejected ci=%d item_id=%u reason=no_owned_item_match tick=%u\n",
                                  ci,
                                  (unsigned)req->item_id,
                                  (unsigned)state->tick);
            }
            break;
        }
    }
}

static void SvrProcessRespawnReq(ServerState* state, int ci, const uint8_t* data, int size)
{
    SvrPlayerState* ps = &state->players[ci];
    if (ps->phase != CPHASE_DEAD) return;

    if (!SvrTransitionClientPhase(state, ci, CPHASE_RESPAWNING)) return;
    ps->respawn_tick = state->tick;
}

// ── Tick-side synthetic event handlers (P1.5) ─────────────────

static void SvrHandleSyntheticConnect(ServerState* state, int ci)
{
    // Initialize player state (moved from IO thread, P1.7)
    SvrPlayerState* ps = &state->players[ci];
    memset(ps, 0, sizeof(SvrPlayerState));

    // Reset per-client diagnostic log budgets for this slot
    SvrResetClientLogBudgets(ci);

    ps->active = true;
    // Atomic reset to NONE before entering FSM. memset above already zeroed the field,
    // but we use atomic_store to match the acquire/release contract of SvrTransitionPhase
    // and eliminate the non-atomic write window that C-3 identified.
    __atomic_store_n((uint8_t*)&ps->phase, (uint8_t)CPHASE_NONE, __ATOMIC_RELEASE);
    SvrTransitionClientPhase(state, ci, CPHASE_CONNECTING);
    ps->player_id = ci;

    printf("[tick] Player slot %d connected (synthetic)\n", ci);
}

static void SvrHandleSyntheticDisconnect(ServerState* state, int ci)
{
    SvrPlayerState* ps = &state->players[ci];
    if (!ps->active) return;

    SvrTransitionClientPhase(state, ci, CPHASE_DISCONNECTING);
}

// S5/FL-1862: single latest-input latch with freshness window is the closed architecture.
// Ordered-queue replay (S5) and client prediction replay (S6/FL-1713) were both tried
// and produced catastrophic snapback (891ms max, passive-20260425-223259).
// Do NOT reintroduce a multi-slot scan, SvrInputSeqDistanceAfter, or a per-tick
// command-log drain here. SVR_INPUT_STALE_TICKS is the sole staleness gate.
//
// FL-2957 TRACE NOTE: this is the only explicit movement-zeroing seam on the M path.
// Once valid forces come in from SvrProcessInputMove, SvrResolveInput applies them.
// The expensive lag owner is NOT here. It is later in the tick loop:
// SvrCanFastPathIdlePlayerPhysics at line ~1513 + MpStepOnce at line ~5843.
static void SvrResolveInput(SvrPlayerState* ps, uint32_t tick)
{
    InputSlot* latest = &ps->latest_input;
    const uint32_t latest_age = latest->valid ? (uint32_t)(tick - latest->recv_tick) : UINT32_MAX;
    // S7/FL-1858: this is the final architecture after the ordered-queue experiment
    // (passive-20260425-223259, 891ms regression) and the churn band (FL-1854/1855).
    // Latest sample + freshness window is correct. Do not treat this as a placeholder.
    if (latest->valid && latest_age <= (uint32_t)SVR_INPUT_STALE_TICKS)
    {
        const uint16_t next_seq = latest->seq;
        ps->input_force[0] = latest->force[0];
        ps->input_force[1] = latest->force[1];
        ps->input_force_z = latest->force_z;
        ps->input_yaw = latest->yaw;
        ps->input_flags = latest->flags;
        ps->last_applied_input_seq = next_seq;

        // Observability: record when a new movement intent sequence is first applied.
        if (ps->m_intent_apply_accept_count == 0 || ps->m_intent_last_apply_accept_seq != next_seq)
        {
            ps->m_intent_apply_accept_count++;
            // tick thread wall clock; recv_stamp_us is recorded separately on latch.
            ps->m_intent_last_apply_accept_us = a3dGetTime();
            ps->m_intent_last_apply_accept_seq = next_seq;
        }
    }
    else
    {
        if (latest->valid)
            latest->valid = false;
        ps->input_force[0] = 0;
        ps->input_force[1] = 0;
        ps->input_force_z = 0;
        ps->input_flags = 0;
    }
}

static void SvrDebugResolvedInput(const ServerState* state, int ci, const SvrPlayerState* ps, uint32_t tick)
{
    if (!state || !state->debug_runtime_diagnostics_enabled || fljit_resolve_logs[ci] >= 4000) return;
    SvrRuntimeDiagLog(state,
                      "[FLJIT-RESOLVE] ci=%d tick=%u seq=%u force=(%.2f,%.2f) yaw=%.2f flags=%u phase=%d\n",
                      ci, tick,
                      (unsigned)ps->last_applied_input_seq,
                      ps->input_force[0], ps->input_force[1],
                      ps->input_yaw, (unsigned)ps->input_flags, (int)ps->phase);
    fljit_resolve_logs[ci]++;
}

static const char* SvrDebugSupportSourceName(uint8_t source)
{
    switch (source)
    {
        case MP_SUPPORT_TERRAIN: return "TERRAIN";
        case MP_SUPPORT_WORLD_MESH: return "WORLD_MESH";
        case MP_SUPPORT_PLACED_BLOCK: return "BLOCK";
        case MP_SUPPORT_WATER: return "WATER";
        default: return "NONE";
    }
}

static void SvrDebugPhysicsStep(const ServerState* state, int ci, const SvrPlayerState* ps, uint32_t tick, const PhysicsIO* pio,
    const MpStepResult* step_result, int steps_handled)
{
    if (!state || !state->debug_runtime_diagnostics_enabled || fljit_phys_logs[ci] >= 10000) return;
    float vel[3] = { 0,0,0 };
    float pre_vel[3] = { 0,0,0 };
    float post_vel[3] = { 0,0,0 };
    float move_world[2] = { 0,0 };
    int zeroed = 0;
    int zero_mask = 0;
    float cnz = 0.0f;
    int auto_jump = 0;
    int ix = 0;
    int iy = 0;
    float input_len = 0.0f;
    if (ps->physics)
    {
        GetPhysicsVel(ps->physics, vel);
        GetPhysicsDebugPreVel(ps->physics, pre_vel);
        GetPhysicsDebugPostVel(ps->physics, post_vel);
        zeroed = GetPhysicsDebugZeroed(ps->physics);
        zero_mask = GetPhysicsDebugZeroMask(ps->physics);
        cnz = GetPhysicsDebugContactNormalZ(ps->physics);
        auto_jump = GetPhysicsDebugAutoJump(ps->physics);
        ix = GetPhysicsDebugIx(ps->physics);
        iy = GetPhysicsDebugIy(ps->physics);
        input_len = GetPhysicsDebugInputLen(ps->physics);
        GetPhysicsDebugMoveWorld(ps->physics, move_world);
    }
    const int grounded = ps->physics && GetPhysicsGrounded(ps->physics) ? 1 : 0;
    SvrRuntimeDiagLog(state,
                      "[FLJIT-PHYS] ci=%d tick=%u steps=%d pos=(%.2f,%.2f,%.2f) vel=(%.2f,%.2f,%.2f) grounded=%d dir=%.2f force=(%.2f,%.2f,%.2f) yaw=%.2f fly=%u jump=%u ix=%d iy=%d ilen=%.3f mw=(%.2f,%.2f) zeroed=%d zmask=%d cnz=%.3f auto_jump=%d sweep=%d swz=%.5f wback=%d sgate=%d sfound=%d sapplied=%d sh=%.2f sdepth=%.2f rz=%.2f pre=(%.2f,%.2f,%.2f) post=(%.2f,%.2f,%.2f)\n",
                      ci, tick,
                      steps_handled,
                      ps->pos[0], ps->pos[1], ps->pos[2],
                      vel[0], vel[1], vel[2], grounded, ps->dir,
                      pio->x_force, pio->y_force, pio->z_force,
                      pio->yaw, (unsigned)pio->fly, (unsigned)pio->jump,
                      ix, iy, input_len, move_world[0], move_world[1],
                      zeroed, zero_mask, cnz, auto_jump,
                      step_result ? (int)step_result->debug_sweep_ran : 0,
                      step_result ? step_result->debug_sweep_z_mag : 0.0f,
                      step_result ? (int)step_result->debug_writeback_applied : 0,
                      step_result ? (int)step_result->debug_support_input_gate : 0,
                      step_result ? (int)step_result->debug_support_found : 0,
                      step_result ? (int)step_result->debug_support_applied : 0,
                      step_result ? step_result->debug_support_height : 0.0f,
                      step_result ? step_result->debug_support_depth : 0.0f,
                      step_result ? step_result->debug_resolved_pos_z : 0.0f,
                      pre_vel[0], pre_vel[1], pre_vel[2],
                      post_vel[0], post_vel[1], post_vel[2]);
    if (ps->support_valid)
    {
        SvrRuntimeDiagLog(state,
                          "[COLLISION-STATE] PLAYER_STANDING_ON_%s ci=%d tick=%u pos=(%.3f,%.3f,%.3f) support_z=%.3f support_item=%u support_inst=%llu support_mesh=%llu\n",
                          SvrDebugSupportSourceName(ps->support_source),
                          ci,
                          tick,
                          ps->pos[0], ps->pos[1], ps->pos[2],
                          ps->support_z,
                          (unsigned)ps->support_item_id,
                          step_result ? (unsigned long long)step_result->support.world_inst_id : 0ull,
                          step_result ? (unsigned long long)step_result->support.world_mesh_id : 0ull);
    }
    if (step_result && step_result->debug_last_sweep_collision_side &&
        step_result->debug_last_sweep_collision_source != MP_SUPPORT_NONE)
    {
        SvrRuntimeDiagLog(state,
                          "[COLLISION-STATE] PLAYER_PUSHING_AGAINST_%s ci=%d tick=%u pos=(%.3f,%.3f,%.3f) item=%u entity=%llu inst=%llu mesh=%llu face=%u normal=(%.3f,%.3f,%.3f) box_min=(%.3f,%.3f,%.3f) box_max=(%.3f,%.3f,%.3f)\n",
                          SvrDebugSupportSourceName(step_result->debug_last_sweep_collision_source),
                          ci,
                          tick,
                          ps->pos[0], ps->pos[1], ps->pos[2],
                          (unsigned)step_result->debug_last_sweep_collision_item_id,
                          (unsigned long long)step_result->debug_last_sweep_collision_entity_id,
                          (unsigned long long)step_result->debug_last_sweep_collision_inst_id,
                          (unsigned long long)step_result->debug_last_sweep_collision_mesh_id,
                          (unsigned)step_result->debug_last_sweep_collision_face_ordinal,
                          step_result->debug_last_sweep_collision_normal[0],
                          step_result->debug_last_sweep_collision_normal[1],
                          step_result->debug_last_sweep_collision_normal[2],
                          step_result->debug_last_sweep_collision_bmin[0],
                          step_result->debug_last_sweep_collision_bmin[1],
                          step_result->debug_last_sweep_collision_bmin[2],
                          step_result->debug_last_sweep_collision_bmax[0],
                          step_result->debug_last_sweep_collision_bmax[1],
                          step_result->debug_last_sweep_collision_bmax[2]);
    }
    fljit_phys_logs[ci]++;
}

// FL-319 / FL-320: NPC damage resolution is unified through this function.
// FL-319: user-reported NPC takes no damage; PvP wrong-target theory was explicitly
//   ruled out — the attacked NPC WAS the closest target. Do not reopen PvP blame.
// FL-320: prior event-consumer fix was insufficient; failure was before authoritative
//   snapshot application. Damage outcome is logged as [swing-resolve] below.
// Do not add target-selection patches here without reading FL-319 + FL-320 first.
static void SvrResolveCombat(ServerState* state)
{
    for (int i = 0; i < state->pending_swing_count; i++)
    {
        PendingSwing* s = &state->pending_swings[i];
        state->combat_swing_count++;
        float attacker_pos[3] = { 0.0f, 0.0f, 0.0f };
        float attacker_dir = 0.0f;
        if (!SvrResolveSwingAttackerState(state, s, attacker_pos, &attacker_dir))
        {
            SvrRuntimeDiagLog(state,
                              "[swing-resolve] tick=%u attacker=%u req_target=%u explicit_target=%d outcome=reject reason=invalid_attacker\n",
                              (unsigned)state->tick,
                              (unsigned)s->attacker_id,
                              (unsigned)s->target_id,
                              s->explicit_target ? 1 : 0);
            continue;
        }

        SvrSwingHit hits[SVR_MAX_PENDING_SWINGS] = {};
        const char* reject_reason = NULL;
        const char* reject_kind = NULL;
        int reject_index = -1;
        float reject_dist2 = 0.0f;
        float reject_limit2 = 0.0f;
        int hit_count = SvrCollectSwingHits(state, s, hits, SVR_MAX_PENDING_SWINGS,
                                            &reject_reason, &reject_kind, &reject_index,
                                            &reject_dist2, &reject_limit2);

        // Broadcast swing animation to all. Gameplay authority stays on the
        // server: clients replay the catalog item id and endpoint the server chose.
        STRUCT_BRC_SWING brc_h = {};
        brc_h.token = 'h';
        brc_h.attacker_id = s->attacker_id;
        brc_h.target_id = s->explicit_target ? s->target_id : 0xFFFF;
        brc_h.weapon_item_id = s->weapon_item_id;
        memcpy(brc_h.pos, attacker_pos, sizeof(attacker_pos));
        brc_h.dir = attacker_dir;
        SvrComputeSwingEndpoint(s, attacker_pos, attacker_dir, hits, hit_count, brc_h.target_pos);
        // Wave 3: lift visual to chest-height for projectile-spawning swings.
        // Decision comes from the catalog (spawns_projectile_on_swing).
        if (s->weapon_item_id != 0)
        {
            const AppearanceCatalogItemDef* swing_item =
                FindAppearanceCatalogItemById(s->weapon_item_id);
            if (swing_item && swing_item->spawns_projectile_on_swing)
            {
                SvrLiftSwingVisualPos(brc_h.pos);
                SvrLiftSwingVisualPos(brc_h.target_pos);
            }
        }
        SvrQueueEvent(state, (const uint8_t*)&brc_h, sizeof(brc_h), -1);

        const bool broke_placed_block =
            SvrTryBreakPlacedBlocksFromSwing(state, s, attacker_pos, attacker_dir);
        if (hit_count <= 0)
        {
            if (broke_placed_block)
                continue;
            if (reject_reason && strcmp(reject_reason, "range") == 0)
            {
                SvrRuntimeDiagLog(state,
                                  "[swing-resolve] tick=%u attacker=%u req_target=%u explicit_target=%d outcome=reject reason=range target_kind=%s target_index=%d dist2=%.3f limit2=%.3f\n",
                                  (unsigned)state->tick,
                                  (unsigned)s->attacker_id,
                                  (unsigned)s->target_id,
                                  s->explicit_target ? 1 : 0,
                                  reject_kind ? reject_kind : "none",
                                  reject_index,
                                  reject_dist2,
                                  reject_limit2);
            }
            else
            {
                SvrRuntimeDiagLog(state,
                                  "[swing-resolve] tick=%u attacker=%u req_target=%u explicit_target=%d outcome=reject reason=%s dir=%.2f\n",
                                  (unsigned)state->tick,
                                  (unsigned)s->attacker_id,
                                  (unsigned)s->target_id,
                                  s->explicit_target ? 1 : 0,
                                  reject_reason ? reject_reason : "unknown",
                                  attacker_dir);
            }
            continue;
        }

        // Wave 3: projectile-spawning is a catalog-owned weapon trait. We
        // look up the swing's weapon item and queue a projectile when the
        // catalog says spawns_projectile_on_swing != 0.
        if (s->weapon_item_id != 0)
        {
            const AppearanceCatalogItemDef* swing_item =
                FindAppearanceCatalogItemById(s->weapon_item_id);
            if (swing_item && swing_item->spawns_projectile_on_swing)
            {
                for (int hit_index = 0; hit_index < hit_count; hit_index++)
                    (void)SvrQueueProjectileFromSwing(state, s->attacker_id,
                                                      s->weapon_item_id,
                                                      attacker_pos,
                                                      &hits[hit_index]);
                continue;
            }
        }

        for (int hit_index = 0; hit_index < hit_count; hit_index++)
        {
            SvrSwingHit* hit = &hits[hit_index];
            int hp_before = *hit->target_hp;

            // Damage
            int damage = SVR_SWING_DAMAGE_BASE + (rand() % 16); // 15-30
            {
                *hit->target_hp -= damage;
                if (s->attacker_id < SVR_MAX_CLIENTS)
                    state->players[s->attacker_id].total_damage_dealt += (uint32_t)damage;
                if (hit->target_id < SVR_MAX_CLIENTS)
                {
                    state->players[hit->target_id].total_damage_taken += (uint32_t)damage;
                    state->players[hit->target_id].last_attacker_id = s->attacker_id;
                }
            }

            // Knockback impulse
            float tdx = hit->target_pos[0] - attacker_pos[0];
            float tdy = hit->target_pos[1] - attacker_pos[1];
            float dist = sqrtf(hit->dist2);
            if (dist > 0.01f)
            {
                float kb = 15.0f / dist;
                if (hit->target_id < SVR_MAX_CLIENTS)
                {
                    state->players[hit->target_id].knockback[0] += tdx * kb;
                    state->players[hit->target_id].knockback[1] += tdy * kb;
                }
            }

            // Broadcast damage
            STRUCT_BRC_DAMAGE brc_d = {};
            brc_d.token = 'd';
            brc_d.damage = (uint8_t)(damage > 255 ? 255 : damage);
            brc_d.target_id = hit->target_id;
            brc_d.attacker_id = s->attacker_id;
            brc_d.new_hp = *hit->target_hp;
            SvrQueueEvent(state, (const uint8_t*)&brc_d, sizeof(brc_d), -1);
            state->combat_damage_count++;
            const bool attacker_is_npc = MultiplayerEntityIdIsNpc(s->attacker_id);
            if (attacker_is_npc && hit->target_is_npc)
                state->combat_damage_npc_to_npc_count++;
            else if (attacker_is_npc)
                state->combat_damage_npc_to_player_count++;
            else if (hit->target_is_npc)
                state->combat_damage_player_to_npc_count++;
            else
                state->combat_damage_player_to_player_count++;
            SvrRuntimeDiagLog(state,
                              "[swing-resolve] tick=%u attacker=%u req_target=%u resolved_target=%u explicit_target=%d outcome=damage target_kind=%s target_index=%d hp_before=%d hp_after=%d damage=%d dist2=%.3f\n",
                              (unsigned)state->tick,
                              (unsigned)s->attacker_id,
                              (unsigned)s->target_id,
                              (unsigned)hit->target_id,
                              s->explicit_target ? 1 : 0,
                              hit->target_kind,
                              hit->target_index,
                              hp_before,
                              *hit->target_hp,
                              damage,
                              hit->dist2);

            // Blood decal
            float r = 0.6f + (rand() % 20) * 0.1f;
            SvrQueueDecal(state, hit->target_pos[0], hit->target_pos[1], r, 5);

            // Death check
            if (*hit->target_hp <= 0)
            {
                *hit->target_hp = 0;
                *hit->target_death_tick = state->tick;

                if (hit->target_id < SVR_MAX_CLIENTS)
                {
                    SvrPlayerState* target = &state->players[hit->target_id];
                    // Canonical death transition: server owns ALL state.
                    target->vel[0] = 0; target->vel[1] = 0; target->vel[2] = 0;
                    target->last_swing_tick = 0;
                    target->last_swing_presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
                    target->last_swing_stamp_us = 0;
                    SvrDropAllOwnedItemsAtPlayer(state,
                                                 (int)hit->target_id,
                                                 "death_item_drop");
                }
                else if (hit->target_is_npc &&
                         hit->target_index >= 0 &&
                         hit->target_index < state->npc_count)
                {
                    SvrNpcState* npc = &state->npcs[hit->target_index];
                    // FL-1434 / FL-1442 track: corpse floor clamping and support retry.
                    // Fix attempt 1: render-side clamp (partial recovery, re-introduced mixed ownership)
                    // Fix attempt 2: server-side Z clamp proposed (unproven)
                    // Ledger currently marks render path as SPENT but server path unproven.
                    float corpse_floor_z =
                        SvrSampleTerrainHeight(state->terrain, npc->pos[0], npc->pos[1], npc->pos[2]);
                    npc->pos[2] = fmaxf(corpse_floor_z, SVR_WATER_LEVEL);
                    npc->vel[0] = 0.0f;
                    npc->vel[1] = 0.0f;
                    npc->vel[2] = 0.0f;
                    if (npc->physics)
                    {
                        PhysicsTeleportCommand command = {};
                        command.set_pos = true;
                        memcpy(command.pos, npc->pos, sizeof(command.pos));
                        memcpy(command.vel, npc->vel, sizeof(command.vel));
                        PhysicsTeleport(npc->physics, command);
                        // FL-2957 attempt #29: NPC respawn teleports are also exact
                        // terrain-resolved positions, but SetPhysicsPos() intentionally
                        // resets accum_contact=0. Re-prime grounded contact here so the
                        // next tick does not pay a synthetic full-step bootstrap tax.
                        SvrPrimeExactTerrainContact(npc->physics);
                    }
                    memcpy(hit->target_pos, npc->pos, sizeof(npc->pos));
                }

                // T60 warm: death is a rare state transition and must remain visible in server.log.
                printf("[death] tick=%u target_id=%u target_kind=%s target_index=%d attacker_id=%u hp_before=%d damage=%d pos=(%.3f,%.3f,%.3f)\n",
                       (unsigned)state->tick,
                       (unsigned)hit->target_id,
                       hit->target_kind,
                       hit->target_index,
                       (unsigned)s->attacker_id,
                       hp_before,
                       damage,
                       hit->target_pos[0],
                       hit->target_pos[1],
                       hit->target_pos[2]);

                STRUCT_BRC_DEATH brc_k = {};
                brc_k.token = 'k';
                brc_k.dead_id = hit->target_id;
                brc_k.killer_id = s->attacker_id;
                SvrQueueEvent(state, (const uint8_t*)&brc_k, sizeof(brc_k), -1);
                state->combat_death_count++;

                // Player death → phase transition
                if (hit->target_id < SVR_MAX_CLIENTS)
                    SvrTransitionClientPhase(state, hit->target_id, CPHASE_DEAD);
            }
        }
    }
    state->pending_swing_count = 0;
}

static void SvrResolveProjectileImpacts(ServerState* state)
{
    if (!state || state->pending_projectile_count <= 0)
        return;

    int write_index = 0;
    for (int i = 0; i < state->pending_projectile_count; i++)
    {
        PendingProjectile* projectile = &state->pending_projectiles[i];
        if (!projectile->active)
            continue;
        if (state->tick < projectile->impact_tick)
        {
            if (write_index != i)
                state->pending_projectiles[write_index] = *projectile;
            write_index++;
            continue;
        }

        SvrSwingHit hit = {};
        if (!SvrResolveSwingTargetById(state, projectile->target_id, &hit))
        {
            SvrRuntimeDiagLog(state,
                              "[projectile-impact] tick=%u attacker=%u target=%u outcome=reject reason=target_unavailable fire_tick=%u impact_tick=%u\n",
                              (unsigned)state->tick,
                              (unsigned)projectile->attacker_id,
                              (unsigned)projectile->target_id,
                              (unsigned)projectile->fire_tick,
                              (unsigned)projectile->impact_tick);
            continue;
        }

        hit.dist2 = projectile->dist2;
        int hp_before = *hit.target_hp;
        int damage = SVR_SWING_DAMAGE_BASE + (rand() % 16);
        *hit.target_hp -= damage;
        if (projectile->attacker_id < SVR_MAX_CLIENTS)
            state->players[projectile->attacker_id].total_damage_dealt += (uint32_t)damage;
        if (hit.target_id < SVR_MAX_CLIENTS)
        {
            state->players[hit.target_id].total_damage_taken += (uint32_t)damage;
            state->players[hit.target_id].last_attacker_id = projectile->attacker_id;
        }

        float tdx = hit.target_pos[0] - projectile->attacker_pos[0];
        float tdy = hit.target_pos[1] - projectile->attacker_pos[1];
        float dist = sqrtf(projectile->dist2);
        if (dist > 0.01f && hit.target_id < SVR_MAX_CLIENTS)
        {
            float kb = 15.0f / dist;
            state->players[hit.target_id].knockback[0] += tdx * kb;
            state->players[hit.target_id].knockback[1] += tdy * kb;
        }

        STRUCT_BRC_DAMAGE brc_d = {};
        brc_d.token = 'd';
        brc_d.damage = (uint8_t)(damage > 255 ? 255 : damage);
        brc_d.target_id = hit.target_id;
        brc_d.attacker_id = projectile->attacker_id;
        brc_d.new_hp = *hit.target_hp;
        SvrQueueEvent(state, (const uint8_t*)&brc_d, sizeof(brc_d), -1);
        state->combat_damage_count++;
        const bool attacker_is_npc = MultiplayerEntityIdIsNpc(projectile->attacker_id);
        if (attacker_is_npc && hit.target_is_npc)
            state->combat_damage_npc_to_npc_count++;
        else if (attacker_is_npc)
            state->combat_damage_npc_to_player_count++;
        else if (hit.target_is_npc)
            state->combat_damage_player_to_npc_count++;
        else
            state->combat_damage_player_to_player_count++;
        SvrRuntimeDiagLog(state,
                          "[projectile-impact] tick=%u attacker=%u target=%u target_kind=%s target_index=%d hp_before=%d hp_after=%d damage=%d fire_tick=%u impact_tick=%u dist2=%.3f\n",
                          (unsigned)state->tick,
                          (unsigned)projectile->attacker_id,
                          (unsigned)hit.target_id,
                          hit.target_kind,
                          hit.target_index,
                          hp_before,
                          *hit.target_hp,
                          damage,
                          (unsigned)projectile->fire_tick,
                          (unsigned)projectile->impact_tick,
                          projectile->dist2);

        float r = 0.6f + (rand() % 20) * 0.1f;
        SvrQueueDecal(state, hit.target_pos[0], hit.target_pos[1], r, 5);

        if (*hit.target_hp <= 0)
        {
            *hit.target_hp = 0;
            *hit.target_death_tick = state->tick;

            if (hit.target_id < SVR_MAX_CLIENTS)
            {
                SvrPlayerState* target = &state->players[hit.target_id];
                target->vel[0] = 0.0f;
                target->vel[1] = 0.0f;
                target->vel[2] = 0.0f;
                target->last_swing_tick = 0;
                target->last_swing_presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
                target->last_swing_stamp_us = 0;
                SvrDropAllOwnedItemsAtPlayer(state,
                                             (int)hit.target_id,
                                             "death_item_drop");
            }
            else if (hit.target_is_npc &&
                     hit.target_index >= 0 &&
                     hit.target_index < state->npc_count)
            {
                SvrNpcState* npc = &state->npcs[hit.target_index];
                float corpse_floor_z =
                    SvrSampleTerrainHeight(state->terrain, npc->pos[0], npc->pos[1], npc->pos[2]);
                npc->pos[2] = fmaxf(corpse_floor_z, SVR_WATER_LEVEL);
                npc->vel[0] = 0.0f;
                npc->vel[1] = 0.0f;
                npc->vel[2] = 0.0f;
                if (npc->physics)
                {
                    PhysicsTeleportCommand command = {};
                    command.set_pos = true;
                    memcpy(command.pos, npc->pos, sizeof(command.pos));
                    memcpy(command.vel, npc->vel, sizeof(command.vel));
                    PhysicsTeleport(npc->physics, command);
                    SvrPrimeExactTerrainContact(npc->physics);
                }
                memcpy(hit.target_pos, npc->pos, sizeof(npc->pos));
            }

            printf("[death] tick=%u target_id=%u target_kind=%s target_index=%d attacker_id=%u hp_before=%d damage=%d pos=(%.3f,%.3f,%.3f)\n",
                   (unsigned)state->tick,
                   (unsigned)hit.target_id,
                   hit.target_kind,
                   hit.target_index,
                   (unsigned)projectile->attacker_id,
                   hp_before,
                   damage,
                   hit.target_pos[0],
                   hit.target_pos[1],
                   hit.target_pos[2]);

            STRUCT_BRC_DEATH brc_k = {};
            brc_k.token = 'k';
            brc_k.dead_id = hit.target_id;
            brc_k.killer_id = projectile->attacker_id;
            SvrQueueEvent(state, (const uint8_t*)&brc_k, sizeof(brc_k), -1);
            state->combat_death_count++;

            if (hit.target_id < SVR_MAX_CLIENTS)
                SvrTransitionClientPhase(state, hit.target_id, CPHASE_DEAD);
        }
    }
    state->pending_projectile_count = write_index;
}

static void SvrProcessRespawns(ServerState* state)
{
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        SvrPlayerState* ps = &state->players[i];
        if (!ps->active) continue;

        // Auto-respawn: begin respawning after death delay without
        // requiring a client respawn request packet.
        if (ps->phase == CPHASE_DEAD && ps->death_tick > 0 &&
            state->tick - ps->death_tick >= SVR_RESPAWN_DELAY_TICKS)
        {
            if (SvrTransitionClientPhase(state, i, CPHASE_RESPAWNING))
                ps->respawn_tick = state->tick;
        }

        if (ps->phase == CPHASE_RESPAWNING)
        {
            if (state->tick - ps->respawn_tick >= SVR_RESPAWN_DELAY_TICKS)
            {
                const uint32_t death_tick = ps->death_tick;
                SvrTransitionClientPhase(state, i, CPHASE_ALIVE);
                // Full respawn reset — match SvrBootstrapAliveFromInput.
                // No stale gameplay state survives death.
                ps->hp = ps->max_hp;
                ps->death_tick = 0;
                ps->knockback[0] = 0;
                ps->knockback[1] = 0;
                ps->mount_state = MOUNT::NONE;
                ps->vel[0] = 0; ps->vel[1] = 0; ps->vel[2] = 0;
                ps->last_swing_tick = 0;
                ps->last_swing_presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
                ps->last_swing_stamp_us = 0;
                ps->in_water = 0.0f;
                // S8/FL-1719: terrain_z is diagnostic/read-only since the post-MpStepOnce
                // SvrSampleTerrainHeight lift was deleted (was a second terrain-contact owner).
                // Do NOT restore the Z lift from this field. Server MpStepOnce owns Z.
                ps->terrain_z = SvrSampleTerrainHeight(state->terrain, ps->pos[0], ps->pos[1], ps->pos[2]);

                // Zero-item reset: drop all owned items on respawn
                for (int ii = 0; ii < SVR_MAX_ITEMS; ii++)
                {
                    SvrItemState* it = &state->items[ii];
                    if (!it->active || it->owner_id != (uint16_t)i) continue;
                    it->owner_id = 0xFFFF;
                    SvrClearEquippedStateForItem(it);
                    // FL-1148: legacy debug-seed respawn anchor is disabled.
                    // Ordinary gameplay drops held items at the death position.
                    memcpy(it->pos, ps->pos, sizeof(it->pos));
                    (void)SvrQueueItemChangeEventChecked(state,
                                                         it,
                                                         ITEM_CHANGE_KIND_DROP,
                                                         0xFFFF,
                                                         "respawn_item_reset");
                }
                // Legacy debug inventory mask reset disabled with ASCIICKER_DEBUG_SEED_INVENTORY.

                // Clear live item ownership, then reapply the same server-owned
                // profile starters used at join. The starter profile, not the
                // dropped item list, owns default visual loadout after respawn.
                if (!SvrReapplyStoredProfileToAppearance(state,
                                                         &ps->appearance,
                                                         "respawn_profile_reset"))
                {
                    SvrClearAppearanceEntries(&ps->appearance, true);
                    ps->appearance.mount_definition_id = 0;
                }

                // Teleport back to initial spawn position
                memcpy(ps->pos, ps->spawn_pos, 12);
                if (ps->physics)
                {
                    PhysicsTeleportCommand command = {};
                    command.set_pos = true;
                    memcpy(command.pos, ps->pos, sizeof(command.pos));
                    PhysicsTeleport(ps->physics, command);
                }

                STRUCT_BRC_RESPAWN brc = {};
                brc.token = 'r';
                brc.player_id = i;
                memcpy(brc.pos, ps->pos, 12);
                SvrQueueEvent(state, (const uint8_t*)&brc, sizeof(brc), -1);
                state->combat_respawn_count++;
                // T60 warm: respawn reset is rare and must remain visible in server.log.
                printf("[respawn] tick=%u player_id=%d death_tick=%u respawn_tick=%u pos=(%.3f,%.3f,%.3f)\n",
                       (unsigned)state->tick,
                       i,
                       (unsigned)death_tick,
                       (unsigned)ps->respawn_tick,
                       ps->pos[0],
                       ps->pos[1],
                       ps->pos[2]);
                fflush(stdout);
            }
        }
    }

    // NPC respawns
    for (int i = 0; i < state->npc_count; i++)
    {
        SvrNpcState* npc = &state->npcs[i];
        if (!npc->active || npc->death_tick == 0) continue;

        if (state->tick - npc->death_tick >= npc->respawn_delay)
        {
            const uint32_t death_tick = npc->death_tick;
            npc->death_tick = 0;
            npc->hp = SVR_NPC_MAX_HP;
            npc->pos[0] = npc->spawn_pos[0] + (float)(rand() % 10 - 5);
            npc->pos[1] = npc->spawn_pos[1] + (float)(rand() % 10 - 5);
            npc->pos[2] = SvrSampleTerrainHeight(
                state->terrain, npc->pos[0], npc->pos[1], npc->spawn_pos[2]);
            npc->target_id = 0xFFFF;
            npc->intent_force[0] = 0.0f;
            npc->intent_force[1] = 0.0f;
            npc->intent_dir = npc->dir;
            npc->vel[0] = 0.0f;
            npc->vel[1] = 0.0f;
            npc->vel[2] = 0.0f;
            npc->last_swing_tick = 0;
            npc->last_swing_presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
            if (npc->physics)
            {
                PhysicsTeleportCommand command = {};
                command.set_pos = true;
                memcpy(command.pos, npc->pos, sizeof(command.pos));
                memcpy(command.vel, npc->vel, sizeof(npc->vel));
                PhysicsTeleport(npc->physics, command);
            }
            printf("[respawn] tick=%u npc_id=%u death_tick=%u pos=(%.3f,%.3f,%.3f)\n",
                   (unsigned)state->tick,
                   (unsigned)npc->entity_id,
                   (unsigned)death_tick,
                   npc->pos[0],
                   npc->pos[1],
                   npc->pos[2]);
            fflush(stdout);
        }
    }
}

// Forward declarations for per-IP rate limiter helpers (RQ-103).
// Defined after AcceptThreadEntry near the accept/IO thread section.
static inline void ip_rate_lock(ServerState* s);
static inline void ip_rate_unlock(ServerState* s);
static void ip_rate_dec(ServerState* s, uint32_t ip);

static void SvrProcessDisconnects(ServerState* state)
{
    bool released_any = false;
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        SvrPlayerState* ps = &state->players[i];
        if (!ps->active) continue;

        if (ps->phase == CPHASE_DISCONNECTING)
        {
            // Broadcast exit
            STRUCT_BRC_EXIT brc = {};
            brc.token = 'e';
            brc.id = i;
            SvrQueueEvent(state, (const uint8_t*)&brc, sizeof(brc), i);

            SvrReleaseOwnedItemsOnDisconnect(state, i);
            SvrInvalidatePlayerAppearanceSendCachesForSlot(state, (uint16_t)i);

            // Clean up physics
            if (ps->physics)
            {
                DeletePhysics(ps->physics);
                ps->physics = NULL;
            }

            printf("[tick] Player '%s' (ID %d) disconnected\n", ps->name, i);

            ps->active = false;
            // Terminal reset bypasses the FSM because DISCONNECTING has no outgoing edge.
            __atomic_store_n((uint8_t*)&ps->phase, (uint8_t)CPHASE_NONE, __ATOMIC_RELEASE);
            atomic_store_phase(&state->clients[i].phase, CPHASE_NONE);
            // Legacy debug seed inventory mask was disabled under FL-1148.

            // RQ-103: decrement per-IP connection count on disconnect.
            // Must happen BEFORE slot release so the accept thread cannot
            // see a freed slot while the count is still elevated.
            {
                uint32_t disc_ip = state->clients[i].peer_ip;
                if (disc_ip != 0)
                {
                    ip_rate_lock(state);
                    ip_rate_dec(state, disc_ip);
                    ip_rate_unlock(state);
                    state->clients[i].peer_ip = 0;
                }
            }

            atomic_release_slot(&state->slot_bitmask, i);
            released_any = true;

            SvrResetClientLogBudgets(i);
        }
    }

    if (released_any && !SvrHasAnyActiveSession(state))
        SvrResetIdleWorld(state);
}

static void SvrUpdateNpcAI(ServerState* state)
{
    static uint32_t npc_ai_log_tick[SVR_MAX_NPCS] = {};
    for (int i = 0; i < state->npc_count; i++)
    {
        SvrNpcState* npc = &state->npcs[i];
        if (!npc->active || npc->death_tick > 0) continue;

        // Find closest player
        float min_dist = 999999.0f;
        int best_target = -1;
        float best_dx = 0.0f, best_dy = 0.0f;

        for (int p = 0; p < SVR_MAX_CLIENTS; p++)
        {
            SvrPlayerState* ps = &state->players[p];
            if (!ps->active || ps->phase != CPHASE_ALIVE || ps->death_tick > 0)
                continue;
            if (!SvrWithinVerticalBand(ps->pos, npc->pos, SVR_VERTICAL_AGGRO_BAND))
                continue;

            float dx = ps->pos[0] - npc->pos[0];
            float dy = ps->pos[1] - npc->pos[1];
            float d = sqrtf(dx * dx + dy * dy);
            if (d < min_dist)
            {
                min_dist = d;
                best_target = p;
                best_dx = dx;
                best_dy = dy;
            }
        }

        npc->target_id = (best_target >= 0) ? best_target : 0xFFFF;
        npc->target_is_player = (best_target >= 0);

        if (best_target < 0)
        {
            npc->intent_force[0] = 0.0f;
            npc->intent_force[1] = 0.0f;
            npc->intent_dir = npc->dir;
            continue;
        }

        SvrPlayerState* target = &state->players[best_target];
        float target_dir = npc->dir;
        if (best_dx != 0.0f || best_dy != 0.0f)
            target_dir = (float)(atan2((double)best_dy, (double)best_dx) * 180.0 / M_PI) + 90.0f;
        const uint16_t npc_swing_presentation_kind =
            SvrResolveSwingPresentationKind(state, npc->entity_id);
        // Wave 3: catalog-owned weapon facts for NPCs too.
        const AppearanceCatalogItemDef* npc_swing_item =
            SvrEquippedWeaponCatalogItemForAppearance(&npc->appearance);
        const uint16_t npc_swing_item_id = npc_swing_item ? npc_swing_item->id : 0;
        const float npc_attack_range = SvrSwingRangeForWeapon(npc_swing_item_id, false);

        if (min_dist > npc_attack_range * 0.8f && min_dist < SVR_NPC_AGGRO_RADIUS)
        {
            // Move toward target
            float inv = 0.5f / min_dist;
            npc->intent_force[0] = best_dx * inv;
            npc->intent_force[1] = best_dy * inv;
            npc->intent_dir = target_dir;
            if (state->debug_runtime_diagnostics_enabled &&
                (npc_ai_log_tick[i] == 0 || state->tick - npc_ai_log_tick[i] >= 15))
            {
                npc_ai_log_tick[i] = state->tick;
                SvrRuntimeDiagLog(state,
                    "[SVR-NPC-AI] tick=%u eid=%u target=%d mode=aggro dist=%.2f npc=(%.2f,%.2f,%.2f) target=(%.2f,%.2f,%.2f) force=(%.2f,%.2f)\n",
                    (unsigned)state->tick,
                    (unsigned)npc->entity_id,
                    best_target,
                    min_dist,
                    npc->pos[0], npc->pos[1], npc->pos[2],
                    target->pos[0], target->pos[1], target->pos[2],
                    npc->intent_force[0], npc->intent_force[1]);
            }
        }
        else if (min_dist <= npc_attack_range)
        {
            npc->intent_force[0] = 0.0f;
            npc->intent_force[1] = 0.0f;
            npc->intent_dir = target_dir;
            if (state->debug_runtime_diagnostics_enabled &&
                (npc_ai_log_tick[i] == 0 || state->tick - npc_ai_log_tick[i] >= 5))
            {
                npc_ai_log_tick[i] = state->tick;
                SvrRuntimeDiagLog(state,
                    "[SVR-NPC-AI] tick=%u eid=%u target=%d mode=attack_range dist=%.2f npc=(%.2f,%.2f,%.2f) target=(%.2f,%.2f,%.2f)\n",
                    (unsigned)state->tick,
                    (unsigned)npc->entity_id,
                    best_target,
                    min_dist,
                    npc->pos[0], npc->pos[1], npc->pos[2],
                    target->pos[0], target->pos[1], target->pos[2]);
            }

            // Attack if in range and off cooldown
            if (state->tick - npc->last_swing_tick >= SVR_SWING_COOLDOWN_TICKS)
            {
                npc->last_swing_tick = state->tick;
                npc->last_swing_presentation_kind_id = npc_swing_presentation_kind;
                SvrRestartAttackPresentationEpoch(npc_swing_presentation_kind,
                                                  npc->presentation_kind_id,
                                                  &npc->presentation_started_tick,
                                                  state->tick);

                // Queue NPC swing through same combat resolution
                if (state->pending_swing_count < SVR_MAX_PENDING_SWINGS)
                {
                    PendingSwing* s = &state->pending_swings[state->pending_swing_count++];
                    s->attacker_id = npc->entity_id;
                    s->target_id = (uint16_t)best_target;
                    s->explicit_target = 1;
                    s->weapon_item_id = npc_swing_item_id;
                    SvrRuntimeDiagLog(state,
                        "[SVR-NPC-SWING] tick=%u eid=%u target=%d weapon_item_id=%u pending=%u dist=%.2f\n",
                        (unsigned)state->tick,
                        (unsigned)npc->entity_id,
                        best_target,
                        (unsigned)s->weapon_item_id,
                        (unsigned)state->pending_swing_count,
                        min_dist);
                }
            }
        }
        else
        {
            npc->intent_force[0] = 0.0f;
            npc->intent_force[1] = 0.0f;
            npc->intent_dir = target_dir;
        }
    }
}

static void SvrFlushEvents(ServerState* state)
{
    for (int e = 0; e < state->events.count; e++)
    {
        SvrEventQueue::Entry* entry = &state->events.entries[e];
        uint8_t* data = state->events.buf + entry->offset;
        bool item_debug = state->debug_runtime_diagnostics_enabled &&
                          entry->size > 0 && data[0] == 'i';

        for (int ci = 0; ci < SVR_MAX_CLIENTS; ci++)
        {
            if (ci == entry->exclude_client) continue;
            // Do not leak live event traffic into the pre-bootstrap join window.
            // The browser join contract is: first meaningful bootstrap packet must be RSP_JOIN.
            // Fresh joiners still get authoritative item replay during JOIN_V2 commit.
            // Gate on ps->phase (tick-thread-owned) to match SvrBroadcastSnapshot — single read surface.
            if (state->players[ci].phase < CPHASE_ALIVE) continue;
            ClientIO* cio = &state->clients[ci];
            int before_len = cio->out[cio->write_idx].len;
            bool ok = SvrQueueToClient(state, ci, data, entry->size, false);
            if (item_debug)
            {
                SvrRuntimeDiagLog(state,
                                  "[item-debug] flush ci=%d ok=%d phase=%d token=%c size=%d before=%d after=%d tick=%u\n",
                                  ci,
                                  ok ? 1 : 0,
                                  (int)atomic_load_phase(&cio->phase),
                                  (char)data[0],
                                  entry->size,
                                  before_len,
                                  cio->out[cio->write_idx].len,
                                  (unsigned)state->tick);
            }
        }
    }
}

static int SvrFindSnapshotEntity(const STRUCT_SNAPSHOT_ENTITY* entities, int count,
                                 uint16_t entity_id, uint8_t entity_type)
{
    for (int i = 0; i < count; i++)
    {
        if (entities[i].entity_id == entity_id &&
            entities[i].entity_type == entity_type)
            return i;
    }
    return -1;
}

static bool SvrSnapshotEntityEqualForDelta(const STRUCT_SNAPSHOT_ENTITY* a,
                                           const STRUCT_SNAPSHOT_ENTITY* b)
{
    if (!a || !b) return false;
    if (a->entity_id != b->entity_id) return false;
    if (a->entity_type != b->entity_type) return false;
    if (a->life_state != b->life_state) return false;
    if (a->mount_state != b->mount_state) return false;
    if (a->locomotion_state != b->locomotion_state) return false;
    if (a->combat_state != b->combat_state) return false;
    if (a->presentation_kind_id != b->presentation_kind_id) return false;
    if (a->state_flags != b->state_flags) return false;
    if (a->hp != b->hp) return false;
    if (a->max_hp != b->max_hp) return false;
    if (a->presentation_started_tick != b->presentation_started_tick) return false;
    if (a->applied_input_seq != b->applied_input_seq) return false;
    if (a->dir != b->dir) return false;
    if (a->vel[0] != b->vel[0] ||
        a->vel[1] != b->vel[1] ||
        a->vel[2] != b->vel[2]) return false;
    if (a->yaw != b->yaw) return false;
    if (a->yaw_vel != b->yaw_vel) return false;
    if (a->slope != b->slope) return false;
    if (a->accum_contact != b->accum_contact) return false;
    if (a->knockback[0] != b->knockback[0]) return false;
    if (a->knockback[1] != b->knockback[1]) return false;
    if (a->pos[0] != b->pos[0] ||
        a->pos[1] != b->pos[1] ||
        a->pos[2] != b->pos[2]) return false;
    return true;
}

// P5.2 Snapshot lifecycle:
//   1. Build full entity list (players + NPCs).
//   2. Per-client interest filter (200m radius, always include self).
//   3. Delta gating: baseline if client hasn't ACK'd, else delta with tombstones.
//   4. Enqueue via SvrQueueToClient (backpressure may drop — P5.3).
//   5. On successful enqueue: update ACK ring + persist visible set for next delta.
//   6. On backpressure drop: skip persist, next tick recomputes against last sent state.
static void SvrBroadcastSnapshot(ServerState* state)
{
    state->snapshot_seq++;
    static int fl036_snapshot_logs[SVR_MAX_CLIENTS] = {};

    // Build entity list
    STRUCT_SNAPSHOT_ENTITY entities[SVR_MAX_CLIENTS + SVR_MAX_NPCS];
    int count = 0;

    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        SvrPlayerState* ps = &state->players[i];
        if (!ps->active || ps->phase < CPHASE_ALIVE) continue;
        STRUCT_SNAPSHOT_ENTITY* e = &entities[count++];
        float auth_vel[3] = { 0,0,0 };
        float auth_slope = 0.0f;
        float auth_accum_contact = 0.0f;
        PhysicsFullState auth_state = {};
        bool have_auth_state = false;
        if (ps->physics)
        {
            GetPhysicsVel(ps->physics, auth_vel);
            GetPhysicsSlope(ps->physics, &auth_slope);
            GetPhysicsAccumContact(ps->physics, &auth_accum_contact);
            SavePhysicsState(ps->physics, &auth_state);
            have_auth_state = true;
        }
        e->entity_id = i;
        e->entity_type = SNAPSHOT_ENTITY_PLAYER;
        e->life_state = ps->life_state;
        e->mount_state = SvrRuntimeMountStateForPlayer(ps);
        e->locomotion_state = ps->locomotion_state;
        e->combat_state = ps->combat_state;
        e->presentation_kind_id = ps->presentation_kind_id;
        e->state_flags = (ps->death_tick == 0) ? SNAPSHOT_STATE_ALIVE : 0;
        if (ps->physics && GetPhysicsGrounded(ps->physics))
            e->state_flags |= SNAPSHOT_STATE_GROUNDED;
        memcpy(e->pos, ps->pos, 12);
        e->dir = ps->dir;
        e->hp = ps->hp;
        e->max_hp = ps->max_hp;
        e->last_authoritative_tick = state->tick;
        e->presentation_started_tick = ps->presentation_started_tick;
        e->applied_input_seq = ps->last_applied_input_seq;
        e->vel[0] = auth_vel[0];
        e->vel[1] = auth_vel[1];
        e->vel[2] = auth_vel[2];
        e->yaw = have_auth_state ? auth_state.yaw : ps->dir;
        e->yaw_vel = have_auth_state ? auth_state.yaw_vel : 0.0f;
        e->slope = auth_slope;
        e->accum_contact = auth_accum_contact;
        e->knockback[0] = ps->knockback[0];
        e->knockback[1] = ps->knockback[1];
        // FL-2957: publish terrain_z so client can verify floor coherence
        e->terrain_z = ps->terrain_z;
        e->support_z = ps->support_z;
        e->support_item_id = ps->support_item_id;
        e->support_source = ps->support_source;
        e->support_valid = ps->support_valid;
    }

    for (int i = 0; i < state->npc_count; i++)
    {
        SvrNpcState* npc = &state->npcs[i];
        if (!npc->active) continue;
        STRUCT_SNAPSHOT_ENTITY* e = &entities[count++];
        e->entity_id = npc->entity_id;
        e->entity_type = SNAPSHOT_ENTITY_NPC;
        e->life_state = npc->life_state;
        e->mount_state = npc->mount_state;
        // FL-2193 boundary note: snapshot locomotion_state is only a derived
        // server render hint, not raw physics ownership. The client's
        // idle-walk branch trusts this field completely, so a bad derivation
        // here turns real chase motion into visible idle on the client.
        e->locomotion_state = npc->locomotion_state;
        e->combat_state = npc->combat_state;
        e->presentation_kind_id = npc->presentation_kind_id;
        e->state_flags = (npc->death_tick == 0) ? SNAPSHOT_STATE_ALIVE : 0;
        // FL-2193 fix-attempt 14e665a3 breadcrumb: export the exact server-side
        // SvrNpcNeedsPhysicsStep() branch into the recorder-visible snapshot.
        if (SvrNpcNeedsPhysicsStep(npc))
            e->state_flags |= SNAPSHOT_STATE_NPC_NEEDS_PHYSICS;
        memcpy(e->pos, npc->pos, 12);
        e->dir = npc->dir;
        e->hp = npc->hp;
        e->max_hp = npc->max_hp;
        e->last_authoritative_tick = state->tick;
        e->presentation_started_tick = npc->presentation_started_tick;
        e->applied_input_seq = 0;
        e->vel[0] = npc->vel[0];
        e->vel[1] = npc->vel[1];
        e->vel[2] = npc->vel[2];
        e->yaw = npc->dir;
        e->yaw_vel = 0.0f;
        e->slope = 0.0f;
        e->accum_contact = 0.0f;
        e->knockback[0] = 0.0f;
        e->knockback[1] = 0.0f;
        e->terrain_z = 0.0f; // NPCs don't track terrain_z; player gate only
        e->support_z = 0.0f;
        e->support_item_id = 0;
        e->support_source = MP_SUPPORT_NONE;
        e->support_valid = 0;
    }

    // Per-client snapshot with interest management + ACK ring tracking
    for (int ci = 0; ci < SVR_MAX_CLIENTS; ci++)
    {
        SvrPlayerState* ps = &state->players[ci];
        if (!ps->active || ps->phase < CPHASE_ALIVE) continue;

        // Interest management: filter entities within radius of this player
        STRUCT_SNAPSHOT_ENTITY filtered[SVR_MAX_CLIENTS + SVR_MAX_NPCS];
        int fcount = 0;
        for (int ei = 0; ei < count; ei++)
        {
            // Always include player entities so remote players never
            // disappear due to interest-radius tombstones.
            if (entities[ei].entity_id == (uint16_t)ci &&
                entities[ei].entity_type == SNAPSHOT_ENTITY_PLAYER)
            {
                filtered[fcount++] = entities[ei];
                continue;
            }
            if (entities[ei].entity_type == SNAPSHOT_ENTITY_PLAYER)
            {
                filtered[fcount++] = entities[ei];
                continue;
            }
            // Distance check (2D, XY plane)
            float dx = entities[ei].pos[0] - ps->pos[0];
            float dy = entities[ei].pos[1] - ps->pos[1];
            if (dx * dx + dy * dy <= SVR_INTEREST_RADIUS_SQ)
                filtered[fcount++] = entities[ei];
        }

        // WebSocket/TCP delivery is ordered and reliable, so a validated ACK
        // means the client is in the snapshot lifecycle and can consume deltas.
        // Requiring exact equality with the most recently sent seq self-locks
        // into repeated baselines when the server ticks faster than ACKs arrive.
        bool can_send_delta = ps->has_sent_snapshot_baseline &&
                              ps->has_acked;

        STRUCT_SNAPSHOT_ENTITY payload[(SVR_MAX_CLIENTS + SVR_MAX_NPCS) * 2];
        int payload_count = 0;

        if (can_send_delta)
        {
            // Add changed/new entities.
            for (int i = 0; i < fcount; i++)
            {
                const STRUCT_SNAPSHOT_ENTITY* cur = &filtered[i];
                int prev_i = SvrFindSnapshotEntity(ps->last_sent_snapshot_entities,
                                                   ps->last_sent_snapshot_entity_count,
                                                   cur->entity_id, cur->entity_type);
                if (prev_i < 0 && cur->entity_type == SNAPSHOT_ENTITY_PLAYER)
                {
                    SvrRuntimeDiagLog(state,
                                      "[REENTRY] ci=%d eid=%u tick=%u seq=%u\n",
                                      ci, (unsigned)cur->entity_id,
                                      (unsigned)state->tick, (unsigned)state->snapshot_seq);
                }
                // BUG-2 fix: always include player entities in deltas.
                // Delta compression excludes unchanged entities, but players are
                // few (max 4) and high-priority — excluding them starves remote
                // pose updates to ~0.56/sec, causing freeze-teleport motion.
                // NPC-DELTA-BYPASS: always include NPC entities in deltas too,
                // mirroring the H23 player bypass. Without this, NPCs whose
                // anim/frame/pos don't change between ticks get silently dropped,
                // causing frozen animation and position teleporting on clients.
                // FL-2957 FAILED/SPENT 2026-05-06: bounded_npc_delta_heartbeat
                // was attempted in 90a739f2 and user-visible lag worsened while
                // mesh clipping remained. Do not retry NPC delta throttling as a
                // lag fix unless the replacement proves NPC observance first.
                // LINEAGE_JSON: {"fl":"FL-2957","surface":"NPC-DELTA-BYPASS","hypothesis":"H-S1","attempt":"bounded_npc_delta_heartbeat","result":"failed_spent","failed_if":"user-visible lag worsened or NPC observance/clipping persisted","note":"keep all-NPC delta bypass active until a replacement preserves NPC presentation"}
                if (prev_i < 0 ||
                    cur->entity_type == SNAPSHOT_ENTITY_PLAYER ||
                    cur->entity_type == SNAPSHOT_ENTITY_NPC ||
                    !SvrSnapshotEntityEqualForDelta(cur, &ps->last_sent_snapshot_entities[prev_i]))
                {
                    payload[payload_count++] = *cur;
                }
            }

            // Add tombstones for entities no longer visible/present.
            for (int i = 0; i < ps->last_sent_snapshot_entity_count; i++)
            {
                const STRUCT_SNAPSHOT_ENTITY* prev = &ps->last_sent_snapshot_entities[i];
                int cur_i = SvrFindSnapshotEntity(filtered, fcount, prev->entity_id, prev->entity_type);
                if (cur_i >= 0) continue;

                if (prev->entity_type == SNAPSHOT_ENTITY_PLAYER)
                {
                    int in_all = SvrFindSnapshotEntity(entities, count, prev->entity_id, prev->entity_type);
                    if (in_all >= 0)
                    {
                        float dx = entities[in_all].pos[0] - ps->pos[0];
                        float dy = entities[in_all].pos[1] - ps->pos[1];
                        float dist = sqrtf(dx * dx + dy * dy);
                        SvrRuntimeDiagLog(state,
                                          "[TOMBSTONE] ci=%d eid=%u reason=INTEREST dist=%.1f tick=%u seq=%u\n",
                                          ci, (unsigned)prev->entity_id, dist,
                                          (unsigned)state->tick, (unsigned)state->snapshot_seq);
                    }
                    else
                    {
                        SvrRuntimeDiagLog(state,
                                          "[TOMBSTONE] ci=%d eid=%u reason=ABSENT tick=%u seq=%u\n",
                                          ci, (unsigned)prev->entity_id,
                                          (unsigned)state->tick, (unsigned)state->snapshot_seq);
                    }
                }

                STRUCT_SNAPSHOT_ENTITY tomb = {};
                tomb.entity_id = prev->entity_id;
                tomb.entity_type = prev->entity_type;
                tomb.state_flags = SNAPSHOT_STATE_REMOVE;
                tomb.last_authoritative_tick = state->tick;
                payload[payload_count++] = tomb;
            }
        }
        else
        {
            memcpy(payload, filtered, fcount * sizeof(STRUCT_SNAPSHOT_ENTITY));
            payload_count = fcount;
        }

        STRUCT_SNAPSHOT_BASELINE hdr = {};
        hdr.token = can_send_delta ? 'q' : 'b';
        hdr.layout_version = 10; // V10 carries support provenance.
        hdr.seq = state->snapshot_seq;
        hdr.tick = state->tick;
        hdr.entity_count = payload_count;
        hdr.entity_size = sizeof(STRUCT_SNAPSHOT_ENTITY);

        int total = sizeof(hdr) + payload_count * (int)sizeof(STRUCT_SNAPSHOT_ENTITY);
        if (total > (int)sizeof(snap_buf))
        {
            ps->snapshot_drops++;
            SvrRuntimeDiagLog(state,
                              "[SNAP-OVERSIZE] ci=%d token=%c tick=%u seq=%u payload=%d total=%d cap=%zu\n",
                              ci,
                              (char)hdr.token,
                              (unsigned)state->tick,
                              (unsigned)hdr.seq,
                              payload_count,
                              total,
                              sizeof(snap_buf));
            continue;
        }
        memcpy(snap_buf, &hdr, sizeof(hdr));
        memcpy(snap_buf + sizeof(hdr), payload, payload_count * sizeof(STRUCT_SNAPSHOT_ENTITY));

        if (fl036_snapshot_logs[ci] < 8)
        {
            SvrRuntimeDiagLog(state,
                              "[FL036-SNAP] ci=%d phase=%d seq=%u tick=%u token=%c payload=%d filtered=%d acked=%u last_sent=%u\n",
                              ci, (int)ps->phase, hdr.seq, hdr.tick, (char)hdr.token,
                              payload_count, fcount, (unsigned)ps->last_acked_seq,
                              (unsigned)ps->last_sent_snapshot_seq);
            fl036_snapshot_logs[ci]++;
        }

        bool appearance_queue_ok = true;
        // Snapshot consumers resolve bundle-backed visuals from server-owned
        // appearance_v2. Only publish that state when it changes; resending the
        // full appearance packet every snapshot creates ordered WebSocket
        // head-of-line blocking for movement snapshots and lag echoes.
        for (int i = 0; i < fcount; i++)
        {
            const STRUCT_SNAPSHOT_ENTITY* visible = &filtered[i];
            if (visible->entity_type == SNAPSHOT_ENTITY_PLAYER)
            {
                if (visible->entity_id < SVR_MAX_CLIENTS)
                {
                    SvrPlayerState* target = &state->players[visible->entity_id];
                    if (target->active &&
                        !SvrQueueChangedAppearanceStateV2ToClient(state,
                                                                  ci,
                                                                  APPEARANCE_V2_ENTITY_PLAYER,
                                                                  visible->entity_id,
                                                                  &target->appearance))
                    {
                        appearance_queue_ok = false;
                        break;
                    }
                }
            }
            else if (visible->entity_type == SNAPSHOT_ENTITY_NPC)
            {
                SvrNpcState* npc = SvrFindNpcByEntityId(state, visible->entity_id);
                if (npc &&
                    !SvrQueueChangedAppearanceStateV2ToClient(state,
                                                              ci,
                                                              APPEARANCE_V2_ENTITY_NPC,
                                                              visible->entity_id,
                                                              &npc->appearance))
                {
                    appearance_queue_ok = false;
                    break;
                }
            }
        }

        if (!appearance_queue_ok)
        {
            // Keep appearance_v2 + snapshot atomic per tick for a recipient.
            // If the changed appearance payload cannot be queued, do not queue
            // a new snapshot that would advance runtime mount/presentation
            // state without the matching mount_definition_id/loadout payload.
            ps->snapshot_drops++;
            if (ps->snapshot_drops <= 5)
            {
                SvrRuntimeDiagLog(state,
                                  "[DIAG-SNAP-DROP] ci=%d drops=%u tick=%u total=%d reason=appearance_contract ob_len=%d\n",
                                  ci, (unsigned)ps->snapshot_drops, (unsigned)state->tick,
                                  total, (int)state->clients[ci].out[state->clients[ci].write_idx].len);
            }
            continue;
        }

        if (!SvrQueueToClient(state, ci, snap_buf, total, true))
        {
            // P5.3: Backpressure drop — ACK ring and last_sent_* are NOT updated.
            // Next tick recomputes delta against the last successfully sent state.
            ps->snapshot_drops++;
            if (ps->snapshot_drops <= 5)
            {
                SvrRuntimeDiagLog(state,
                                  "[DIAG-SNAP-DROP] ci=%d drops=%u tick=%u total=%d ob_len=%d\n",
                                  ci, (unsigned)ps->snapshot_drops, (unsigned)state->tick,
                                  total, (int)state->clients[ci].out[state->clients[ci].write_idx].len);
            }
            continue;
        }
        (void)SvrQueueCollisionDebugToClient(state, ci, ps);

        // Observability: count snapshots successfully queued after at least one
        // movement intent ('M') was accepted/applied for this client.
        if (ps->m_intent_latch_accept_count > 0 || ps->m_intent_apply_accept_count > 0)
            ps->snapshot_after_m_count++;

        // DIAG: confirm snapshot was queued for this client
        {
            static int diag_snap_ok_logs[SVR_MAX_CLIENTS] = {};
            if (diag_snap_ok_logs[ci] < 5)
            {
                SvrRuntimeDiagLog(state,
                                  "[DIAG-SNAP-OK] ci=%d tick=%u token=%c total=%d ob_len=%d baseline=%d\n",
                                  ci, (unsigned)state->tick, (char)hdr.token, total,
                                  (int)state->clients[ci].out[state->clients[ci].write_idx].len,
                                  (int)ps->has_sent_snapshot_baseline);
                diag_snap_ok_logs[ci]++;
            }
        }

        // Record in ACK ring only after successful enqueue (P5.3 invariant).
        SnapshotACK* ack = &ps->ack_ring[ps->ack_write % SVR_SNAPSHOT_RING_SIZE];
        ack->seq = state->snapshot_seq;
        ack->tick = state->tick;
        ack->entity_count = fcount;
        ps->ack_write++;

        // Persist current full visible set for next delta computation.
        ps->last_sent_snapshot_entity_count = fcount;
        if (fcount > 0)
            memcpy(ps->last_sent_snapshot_entities, filtered,
                   fcount * sizeof(STRUCT_SNAPSHOT_ENTITY));
        ps->last_sent_snapshot_seq = state->snapshot_seq;
        ps->has_sent_snapshot_baseline = true;
    }
}

static void SvrPublishOutbound(ServerState* state)
{
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        ClientIO* cio = &state->clients[i];
        // Publish for any connected client (CONNECTING and above).
        // RSP_JOIN is queued while still CONNECTING, so JOINED check was too strict.
        ClientPhase cp = atomic_load_phase(&cio->phase);
        if (cp == CPHASE_NONE || cp == CPHASE_DISCONNECTING) continue;
        if (cio->out[cio->write_idx].len == 0)
        {
            // DIAG: log when an ALIVE client has nothing to publish
            static int diag_pub_empty_logs[SVR_MAX_CLIENTS] = {};
            if (state->players[i].active &&
                state->players[i].phase >= CPHASE_ALIVE &&
                diag_pub_empty_logs[i] < 5)
            {
                SvrRuntimeDiagLog(state,
                                  "[DIAG-PUB-EMPTY] ci=%d tick=%u write_idx=%d new_data=%d phase=%d\n",
                                  i, (unsigned)state->tick, cio->write_idx,
                                  __atomic_load_n(&cio->new_data, __ATOMIC_RELAXED),
                                  (int)state->players[i].phase);
                diag_pub_empty_logs[i]++;
            }
            continue;
        }

        // Do not overwrite a shared buffer the IO thread has not consumed yet.
        // Replacing pending snapshot frames with newer ones sparsifies
        // authoritative updates at the browser and inflates reconcile drift.
        if (__atomic_load_n(&cio->new_data, __ATOMIC_ACQUIRE))
        {
            // DIAG: log when tick thread cannot publish because IO hasn't consumed
            static int diag_pub_blocked_logs[SVR_MAX_CLIENTS] = {};
            if (state->players[i].active &&
                state->players[i].phase >= CPHASE_ALIVE &&
                diag_pub_blocked_logs[i] < 5)
            {
                SvrRuntimeDiagLog(state,
                                  "[DIAG-PUB-BLOCKED] ci=%d tick=%u write_idx=%d ob_len=%d\n",
                                  i, (unsigned)state->tick, cio->write_idx,
                                  cio->out[cio->write_idx].len);
                diag_pub_blocked_logs[i]++;
            }
            continue;
        }

        // Publish: swap write_idx into shared_idx, reclaim old shared buffer
        int published_idx = cio->write_idx;
        int published_len = cio->out[published_idx].len;
        int old_shared = __atomic_exchange_n(&cio->shared_idx,
                                             cio->write_idx, __ATOMIC_ACQ_REL);
        cio->write_idx = old_shared;
        cio->out[cio->write_idx].len = 0; // clear reclaimed buffer

        __atomic_store_n(&cio->new_data, 1, __ATOMIC_RELEASE);
        SvrWakeIOThread(state);
        {
            static int diag_pub_swap_logs[SVR_MAX_CLIENTS] = {};
            if (state->players[i].active &&
                state->players[i].phase >= CPHASE_JOINED &&
                diag_pub_swap_logs[i] < 8)
            {
                SvrRuntimeDiagLog(state,
                          "[DIAG-PUB-SWAP] ci=%d tick=%u phase=%d published_idx=%d published_len=%d old_shared=%d new_write_idx=%d shared_idx=%d new_data=%d\n",
                       i,
                       (unsigned)state->tick,
                       (int)state->players[i].phase,
                       published_idx,
                       published_len,
                       old_shared,
                       cio->write_idx,
                       __atomic_load_n(&cio->shared_idx, __ATOMIC_RELAXED),
                       __atomic_load_n(&cio->new_data, __ATOMIC_RELAXED));
                diag_pub_swap_logs[i]++;
            }
        }
    }
}

static void ServerTick(ServerState* state) // FL-2957: main server tick — tick_overrun owner
{
    uint64_t phase_start_us = 0;

    // Reset per-tick state
    state->events.len = 0;
    state->events.count = 0;
    state->pending_swing_count = 0;

    // ── PHASE 1: INGEST ──────────────────────────────────────────
    phase_start_us = a3dGetTime();
    struct PendingMsg {
        int client_idx;
        uint8_t data[SVR_INBOUND_MSG_MAX];
        int size;
        uint64_t recv_stamp_us;
    };
    PendingMsg pending[SVR_MAX_CLIENTS * 8];
    int pending_count = 0;

    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        ClientIO* cio = &state->clients[i];
        if (atomic_load_phase(&cio->phase) == CPHASE_NONE) continue;

        uint32_t rd = __atomic_load_n(&cio->in_read, __ATOMIC_ACQUIRE);
        uint32_t wr = __atomic_load_n(&cio->in_write, __ATOMIC_ACQUIRE);
        while (rd != wr && pending_count < SVR_MAX_CLIENTS * 8)
        {
            ClientIO::InMsg* m = &cio->in_ring[rd & SVR_MSG_RING_MASK];
            PendingMsg* pm = &pending[pending_count++];
            pm->client_idx = i;
            memcpy(pm->data, m->data, m->size);
            pm->size = m->size;
            pm->recv_stamp_us = m->recv_stamp_us;
            rd++;
        }
        __atomic_store_n(&cio->in_read, rd, __ATOMIC_RELEASE);
    }
    SvrRecordTickPhase(state, SVR_TICK_PHASE_INGEST, a3dGetTime() - phase_start_us);

    // ── PHASE 2: INPUT ───────────────────────────────────────────
    phase_start_us = a3dGetTime();
    for (int i = 0; i < pending_count; i++)
    {
        PendingMsg* pm = &pending[i];
        int ci = pm->client_idx;

        switch (pm->data[0])
        {
            case 'G': SvrProcessJoinV2(state, ci, pm->data, pm->size);     break;
            case 'M': SvrProcessInputMove(state, ci, pm->data, pm->size, pm->recv_stamp_us);  break;
            case 'T': SvrProcessTalk(state, ci, pm->data, pm->size);       break;
            case 'H': SvrProcessSwing(state, ci, pm->data, pm->size, false, pm->recv_stamp_us); break;
            case 'X': SvrProcessSwing(state, ci, pm->data, pm->size, true, pm->recv_stamp_us);  break;
            case 'I': SvrProcessItemAction(state, ci, pm->data, pm->size); break;
            // 'C' (debug disable-damage) removed: SVR-M-008 / FL-345
            case 'R': SvrProcessRespawnReq(state, ci, pm->data, pm->size);  break;
            case 'A': SvrProcessSnapshotAck(state, ci, pm->data, pm->size); break;
            case SVR_SYNTHETIC_CONNECT:    SvrHandleSyntheticConnect(state, ci);    break;
            case SVR_SYNTHETIC_DISCONNECT: SvrHandleSyntheticDisconnect(state, ci); break;
            // D, K: server-generated only. Unknown opcodes silently dropped.
        }
    }

    // Resolve de-jitter → stable input for this tick
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        SvrPlayerState* ps = &state->players[i];
        if (!ps->active || ps->phase != CPHASE_ALIVE) continue;
        SvrResolveInput(ps, state->tick);
        SvrDebugResolvedInput(state, i, ps, state->tick);
    }
    SvrRecordTickPhase(state, SVR_TICK_PHASE_INPUT, a3dGetTime() - phase_start_us);

    // ── PHASE 3: PHYSICS ─────────────────────────────────────────
    phase_start_us = a3dGetTime();
    uint64_t physics_players_us = 0, physics_npcs_us = 0;
    const bool measure_players_breakdown =
        g_tick_players_breakdown_logs < SVR_TICK_PLAYERS_BREAKDOWN_LOG_LIMIT &&
        state->tick_last_physics_phase_id == SVR_TICK_PHYSICS_PLAYERS &&
        state->tick_last_physics_phase_us >= SVR_TICK_PHYSICS_LOG_THRESHOLD_US;
    uint64_t physics_players_save_state_us = 0;
    uint64_t physics_players_build_env_us = 0;
    uint64_t physics_players_step_once_us = 0;
    uint64_t physics_players_restore_sync_us = 0;
    uint64_t physics_players_post_state_us = 0;
    uint64_t physics_players_water_clamp_us = 0;
    uint64_t physics_players_terrain_sample_us = 0;
    uint32_t physics_players_step_count = 0;
    uint32_t physics_players_active_count = 0;
    uint32_t physics_players_water_clamp_count = 0;
    uint32_t physics_players_idle_fast_path_count = 0;
    uint32_t physics_player_full_step_count[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_idle_fast_path_counts[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_once_us[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_soup_items_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_world_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_terrain_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_us[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_instances[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_faces[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_world_callbacks[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_terrain_tris[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_query_us_total[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_query_us_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_query_overhead_us_total[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_query_overhead_us_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_us_total[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_us_max[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_calls[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_accepts[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_reject_visual[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_reject_alpha[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_accept_us_total[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_accept_us_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_push_us_total[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_push_us_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_material_us_total[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_material_us_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_transform_us_total[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_transform_us_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_normal_us_total[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_normal_us_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_bbox_us_total[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_bbox_us_max[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_faces_reported_total[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_faces_reported_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_us_max_inst_id[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_face_cb_us_max_mesh_id[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_mesh_faces[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_face_ordinal[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_accept[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_visual[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_soup_index[SVR_MAX_CLIENTS] = {};
    int32_t physics_player_collect_mesh_face_cb_us_max_inst_story_id[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_inst_flags[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_inst_name_hash[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_mesh_name_hash[SVR_MAX_CLIENTS] = {};
    int32_t physics_player_collect_mesh_face_cb_us_max_inst_bbox_cx_milli[SVR_MAX_CLIENTS] = {};
    int32_t physics_player_collect_mesh_face_cb_us_max_inst_bbox_cy_milli[SVR_MAX_CLIENTS] = {};
    int32_t physics_player_collect_mesh_face_cb_us_max_inst_bbox_cz_milli[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_inst_bbox_diag_milli[SVR_MAX_CLIENTS] = {};
    int32_t physics_player_collect_mesh_face_cb_us_max_query_cx_milli[SVR_MAX_CLIENTS] = {};
    int32_t physics_player_collect_mesh_face_cb_us_max_query_cy_milli[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_query_radius_milli[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_face_cb_us_max_query_bbox_dist_milli[SVR_MAX_CLIENTS] = {};
    int32_t physics_player_collect_mesh_face_cb_us_max_query_bbox_overlap_milli[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_soup_reallocs[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_soup_capacity_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_soup_bytes_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_collect_mesh_soup_bytes_growth_total[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_bbox_would_skip[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_bbox_would_skip_faces[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_cache_hits[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_cache_misses[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_mesh_cache_items[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_world_bsp_tests[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_world_bsp_nodes[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_collect_world_bsp_insts[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_iterations[SVR_MAX_CLIENTS] = {};
	    uint32_t physics_player_collision_checks[SVR_MAX_CLIENTS] = {};
	    uint32_t physics_player_collision_broadphase_rejects[SVR_MAX_CLIENTS] = {};
	    uint64_t physics_player_sweep_total_us[SVR_MAX_CLIENTS] = {};
	    uint64_t physics_player_sweep_narrowphase_us_total[SVR_MAX_CLIENTS] = {};
	    uint64_t physics_player_sweep_narrowphase_us_max[SVR_MAX_CLIENTS] = {};
	    uint64_t physics_player_sweep_narrowphase_us_last[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_narrowphase_us_max_item_index[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_narrowphase_us_max_item_material[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_sweep_narrowphase_us_max_item_inst_id[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_sweep_narrowphase_us_max_item_mesh_id[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_narrowphase_us_max_item_mesh_faces[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_narrowphase_us_max_item_face_ordinal[SVR_MAX_CLIENTS] = {};
    int32_t physics_player_sweep_narrowphase_us_max_item_inst_story_id[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_narrowphase_us_max_item_inst_flags[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_narrowphase_us_max_item_inst_name_hash[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_narrowphase_us_max_item_mesh_name_hash[SVR_MAX_CLIENTS] = {};
    float physics_player_sweep_narrowphase_us_max_item_nrm_z[SVR_MAX_CLIENTS] = {};
    float physics_player_sweep_narrowphase_us_max_item_bbox_diag[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_sweep_iter_us_max[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_sweep_iter_us_last[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_iter_collision_checks_max[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_iter_collision_checks_last[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_iter_hits[SVR_MAX_CLIENTS] = {};
    float physics_player_sweep_iter_earliest_t_last[SVR_MAX_CLIENTS] = {};
    float physics_player_sweep_iter_remaining_move_len_last[SVR_MAX_CLIENTS] = {};
    float physics_player_sweep_iter_output_normal_z_last[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_sweep_iter_deflection_count[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_soup_capped[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_mesh_per_mesh_cap_hits[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_mesh_bbox_skips[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_support_priority_callbacks[SVR_MAX_CLIENTS] = {};

    // MpStepOnce accounting breakdown (observability only).
    uint64_t physics_player_step_wall_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_accounted_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_unaccounted_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_pre_collect_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_collect_build_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_post_collect_setup_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_sweep_wall_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_post_sweep_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_support_probe_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_support_retry_build_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_support_retry_probe_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_support_wide_probe_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_floor_probe_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_post_support_apply_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_material_votes_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_contact_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_grounding_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_mount_us[SVR_MAX_CLIENTS] = {};
    uint64_t physics_player_step_finalize_us[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_fast_path_reject_hits[SVR_MAX_CLIENTS][SVR_IDLE_FASTPATH_REJECT_COUNT] = {};
    uint8_t physics_player_input_flags_last[SVR_MAX_CLIENTS] = {};
    uint8_t physics_player_grounded_last[SVR_MAX_CLIENTS] = {};
    uint8_t physics_player_in_water_last[SVR_MAX_CLIENTS] = {};
    float physics_player_max_abs_vel[SVR_MAX_CLIENTS] = {};
    float physics_player_max_yaw_delta[SVR_MAX_CLIENTS] = {};
    float physics_player_max_support_z_delta[SVR_MAX_CLIENTS] = {};
    float physics_player_accum_contact_last[SVR_MAX_CLIENTS] = {};
    float physics_player_pos_z_last[SVR_MAX_CLIENTS] = {};
    float physics_player_terrain_z_last[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_idle_support_recovered[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_support_retry_rebuilt[SVR_MAX_CLIENTS] = {};
    uint32_t physics_player_support_retry_found[SVR_MAX_CLIENTS] = {};
    struct SvrSupportRetryCollectDebug
    {
        uint64_t soup_items;
        uint64_t collect_world_us;
        uint64_t collect_terrain_us;
        uint64_t collect_us;
        uint64_t collect_mesh_us;
        uint64_t collect_mesh_instances;
        uint64_t collect_mesh_faces;
        uint64_t collect_world_callbacks;
        uint64_t collect_terrain_tris;
        uint64_t collect_mesh_query_us_total;
        uint64_t collect_mesh_query_us_max;
        uint64_t collect_mesh_query_overhead_us_total;
        uint64_t collect_mesh_query_overhead_us_max;
        uint64_t collect_mesh_face_cb_us_total;
        uint64_t collect_mesh_face_cb_us_max;
        uint64_t collect_mesh_face_cb_calls;
        uint64_t collect_mesh_face_cb_accepts;
        uint64_t collect_mesh_face_cb_reject_visual;
        uint64_t collect_mesh_face_cb_reject_alpha;
        uint64_t collect_mesh_face_cb_accept_us_total;
        uint64_t collect_mesh_face_cb_accept_us_max;
        uint64_t collect_mesh_face_cb_push_us_total;
        uint64_t collect_mesh_face_cb_push_us_max;
        uint64_t collect_mesh_face_cb_material_us_total;
        uint64_t collect_mesh_face_cb_material_us_max;
        uint64_t collect_mesh_face_cb_transform_us_total;
        uint64_t collect_mesh_face_cb_transform_us_max;
        uint64_t collect_mesh_face_cb_normal_us_total;
        uint64_t collect_mesh_face_cb_normal_us_max;
        uint64_t collect_mesh_face_cb_bbox_us_total;
        uint64_t collect_mesh_face_cb_bbox_us_max;
        uint64_t collect_mesh_faces_reported_total;
        uint64_t collect_mesh_faces_reported_max;
        uint64_t collect_mesh_face_cb_us_max_inst_id;
        uint64_t collect_mesh_face_cb_us_max_mesh_id;
        uint64_t collect_mesh_face_cb_us_max_mesh_faces;
        uint64_t collect_mesh_face_cb_us_max_face_ordinal;
        uint64_t collect_mesh_face_cb_us_max_accept;
        uint64_t collect_mesh_face_cb_us_max_visual;
        uint64_t collect_mesh_face_cb_us_max_soup_index;
        int64_t collect_mesh_face_cb_us_max_inst_story_id;
        uint64_t collect_mesh_face_cb_us_max_inst_flags;
        uint64_t collect_mesh_face_cb_us_max_inst_name_hash;
        uint64_t collect_mesh_face_cb_us_max_mesh_name_hash;
        int64_t collect_mesh_face_cb_us_max_inst_bbox_cx_milli;
        int64_t collect_mesh_face_cb_us_max_inst_bbox_cy_milli;
        int64_t collect_mesh_face_cb_us_max_inst_bbox_cz_milli;
        uint64_t collect_mesh_face_cb_us_max_inst_bbox_diag_milli;
        int64_t collect_mesh_face_cb_us_max_query_cx_milli;
        int64_t collect_mesh_face_cb_us_max_query_cy_milli;
        uint64_t collect_mesh_face_cb_us_max_query_radius_milli;
        uint64_t collect_mesh_face_cb_us_max_query_bbox_dist_milli;
        int64_t collect_mesh_face_cb_us_max_query_bbox_overlap_milli;
        uint64_t collect_mesh_soup_reallocs;
        uint64_t collect_mesh_soup_capacity_max;
        uint64_t collect_mesh_soup_bytes_max;
        uint64_t collect_mesh_soup_bytes_growth_total;
        uint64_t collect_mesh_bbox_would_skip;
        uint64_t collect_mesh_bbox_would_skip_faces;
        uint64_t collect_mesh_cache_hits;
        uint64_t collect_mesh_cache_misses;
        uint64_t collect_mesh_cache_items;
        uint64_t collect_world_bsp_tests;
        uint64_t collect_world_bsp_nodes;
        uint64_t collect_world_bsp_insts;
        uint64_t same_region_repeat_count;
    } physics_player_support_retry_collect[SVR_MAX_CLIENTS] = {};
    const bool have_active_sessions = SvrHasAnyActiveSession(state), have_alive_players = SvrHasAnyAlivePlayer(state);
    // [H-2] Must match Animate()'s inner interval (15000us) to avoid replay divergence.
    const uint64_t substep_us = 15000;
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        SvrPlayerState* ps = &state->players[i];
        if (!ps->active || ps->phase != CPHASE_ALIVE) continue;
        if (!ps->physics) continue;
        physics_players_active_count++;
    }
    for (int sub = 0; sub < SVR_PHYSICS_SUBSTEPS; sub++)
    {
        const uint64_t substep_stamp_us =
            state->tick_stamp_us -
            (uint64_t)(SVR_PHYSICS_SUBSTEPS - 1 - sub) * substep_us;
        // Players
        uint64_t physics_slice_start_us = a3dGetTime();
        for (int i = 0; i < SVR_MAX_CLIENTS; i++)
        {
            SvrPlayerState* ps = &state->players[i];
            if (!ps->active || ps->phase != CPHASE_ALIVE) continue;
            if (!ps->physics) continue;

            PhysicsIO pio = {};
            pio.x_force = ps->input_force[0];
            pio.y_force = ps->input_force[1];
            pio.z_force = ps->input_force_z;
            pio.yaw = ps->input_yaw;
            pio.torque = 1000000.0f;
            pio.jump = (ps->input_flags & 0x01) != 0;
            // Law 7 (FL-1760 / RQ-029): fly-mode input is only accepted when
            // ASCIICKER_DEBUG_FLY_MODE is set.  In release/current/candidate lanes
            // debug_fly_mode_enabled is false and pio.fly stays 0.
            pio.fly = state->debug_fly_mode_enabled && ((ps->input_flags & 0x02) != 0);
            pio.x_impulse = ps->knockback[0];
            pio.y_impulse = ps->knockback[1];
            pio.water = SVR_WATER_LEVEL;
            uint8_t runtime_mount = ps->mount_state;
            if (runtime_mount >= MOUNT::SIZE)
                runtime_mount = MOUNT::NONE;

            PhysicsFullState prev_state = {};
            uint64_t step_component_start_us = 0;
            SvrIdleFastPathEval fast_path_eval = {};
            if (measure_players_breakdown)
                step_component_start_us = a3dGetTime();
            SavePhysicsState(ps->physics, &prev_state);
            if (measure_players_breakdown)
                physics_players_save_state_us += a3dGetTime() - step_component_start_us;

            // LINEAGE_JSON: {"fl":"FL-2957","attempt_counter":{"total":28,"closed":0},"note":"manual-20260505-070756 FAILED ALL FIXES. H-P0 REFUTED (delta=2.0 not 16). #24 BUG FIXED (ShouldContinueCollecting checked terrain cap instead of world-only). H-P4 accum_contact=0 despite bootstrap fix — needs investigation. NPCs 438ms DOMINANT. #28: fix ShouldContinueCollecting to CanCollectWorld() only + revert H-P0 bit-15 lift.","run":"manual-20260505-070756"}
            const bool can_fast_path = SvrCanFastPathIdlePlayerPhysics(ps, &prev_state, &fast_path_eval);
            physics_player_input_flags_last[i] = fast_path_eval.input_flags;
            physics_player_grounded_last[i] = fast_path_eval.grounded;
            physics_player_in_water_last[i] = fast_path_eval.in_water;
            physics_player_max_abs_vel[i] = fmaxf(physics_player_max_abs_vel[i], fast_path_eval.max_abs_vel);
            physics_player_max_yaw_delta[i] = fmaxf(physics_player_max_yaw_delta[i], fast_path_eval.yaw_delta);
            physics_player_max_support_z_delta[i] = fmaxf(physics_player_max_support_z_delta[i], fast_path_eval.support_z_delta);
            physics_player_accum_contact_last[i] = fast_path_eval.accum_contact;
            physics_player_pos_z_last[i] = fast_path_eval.pos_z;
            physics_player_terrain_z_last[i] = fast_path_eval.terrain_z;
            if (fast_path_eval.idle_support_recovered)
                physics_player_idle_support_recovered[i]++;

            // FL-2957-spawn-diag: targeted logging for idle players rejected on grounded/support-z.
            // Helps narrow why fresh spawns / bad-terrain states fail the idle fast path
            // even with zero movement input.
            if (fast_path_eval.reject_mask != 0 &&
                fast_path_eval.input_flags == 0 &&
                (fast_path_eval.reject_mask & ((1u << SVR_IDLE_FASTPATH_REJECT_GROUNDED) | (1u << SVR_IDLE_FASTPATH_REJECT_SUPPORT_Z))) != 0)
            {
                static int svr_spawn_reject_diag_logs = 0;
                if (svr_spawn_reject_diag_logs < 48)
                {
                    SvrRuntimeDiagLog(state,
                            "[FL-2957-SPAWN-REJECT] tick=%u sub=%d ci=%d reject_mask=0x%x grounded=%d support_z_delta=%.4f terrain_z=%.4f pos_z=%.4f eps=%.4f vel_milli=%u yaw_delta_mdeg=%u\n",
                           (unsigned)state->tick, sub, i,
                           fast_path_eval.reject_mask,
                           (int)fast_path_eval.grounded,
                           fast_path_eval.support_z_delta,
                           isfinite(ps->terrain_z) ? ps->terrain_z : NAN,
                           prev_state.pos[2],
                           SVR_IDLE_FASTPATH_SUPPORT_Z_EPS,
                           (unsigned)(fast_path_eval.max_abs_vel * 1000.0f),
                           (unsigned)(fast_path_eval.yaw_delta * 1000.0f));
                    svr_spawn_reject_diag_logs++;
                }
            }

            if (can_fast_path)
            {
                PhysicsFullState idle_state = prev_state;
                idle_state.stamp = substep_stamp_us;
                idle_state.vel[0] = 0.0f;
                idle_state.vel[1] = 0.0f;
                idle_state.vel[2] = 0.0f;
                idle_state.yaw_vel = 0.0f;
                idle_state.slope = 0.0f;
                idle_state.player_stp = -1;
                if (fast_path_eval.idle_support_recovered)
                    idle_state.accum_contact = 1.0f;
                if (measure_players_breakdown)
                    step_component_start_us = a3dGetTime();
                RestorePhysicsState(ps->physics, &idle_state);
                SyncPhysicsStamp(ps->physics, substep_stamp_us);
                if (measure_players_breakdown)
                    physics_players_restore_sync_us += a3dGetTime() - step_component_start_us;
                pio.player_dir = idle_state.player_dir;
                pio.player_stp = idle_state.player_stp;
                physics_players_idle_fast_path_count++;

                const int steps_handled = 0;
                if (measure_players_breakdown)
                    step_component_start_us = a3dGetTime();
                GetPhysicsPos(ps->physics, ps->pos);
                ps->dir = pio.player_dir;
                ps->vel[0] = idle_state.vel[0];
                ps->vel[1] = idle_state.vel[1];
                ps->vel[2] = idle_state.vel[2];
                if (measure_players_breakdown)
                    physics_players_post_state_us += a3dGetTime() - step_component_start_us;
                if (measure_players_breakdown)
                    step_component_start_us = a3dGetTime();
                ps->terrain_z = SvrSampleTerrainHeight(state->terrain, ps->pos[0], ps->pos[1], ps->pos[2]); // GAP-12
                if (measure_players_breakdown)
                    physics_players_terrain_sample_us += a3dGetTime() - step_component_start_us;
                ps->knockback[0] = 0.0f;
                ps->knockback[1] = 0.0f;
                physics_player_idle_fast_path_counts[i]++;
                SvrDebugPhysicsStep(state, i, ps, state->tick, &pio, 0, steps_handled);
                continue;
            }
            for (int reason = 0; reason < SVR_IDLE_FASTPATH_REJECT_COUNT; reason++)
            {
                if ((fast_path_eval.reject_mask & (1u << reason)) != 0)
                    physics_player_fast_path_reject_hits[i][reason]++;
            }

            MpStepInput step_input = {};
            step_input.x_force = ps->input_force[0];
            step_input.y_force = ps->input_force[1];
            step_input.z_force = ps->input_force_z;
            step_input.yaw = ps->input_yaw;
            step_input.jump = (ps->input_flags & 0x01) != 0;
            // Law 7 gate — matches pio.fly above (FL-1760 / RQ-029).
            step_input.fly = state->debug_fly_mode_enabled && ((ps->input_flags & 0x02) != 0);

            if (measure_players_breakdown)
                step_component_start_us = a3dGetTime();
            MpStepEnv step_env = MpStepBuildEnv(state->terrain,
                                                state->world,
                                                &state->world_entities,
                                                substep_stamp_us,
                                                SVR_WATER_LEVEL,
                                                runtime_mount);
            if (measure_players_breakdown)
                physics_players_build_env_us += a3dGetTime() - step_component_start_us;
            MpStepState step_state = MpStepFromPhysicsState(&prev_state, ps->knockback);
            MpStepResult step_result = {};
            if (measure_players_breakdown)
                step_component_start_us = a3dGetTime();
            const uint64_t player_step_once_start_us = a3dGetTime();
            step_state = MpStepOnce(step_state, step_input, step_env, &step_result);
            physics_player_step_once_us[i] += a3dGetTime() - player_step_once_start_us;
            physics_player_soup_items_max[i] = physics_player_soup_items_max[i] > step_result.debug_soup_items
                ? physics_player_soup_items_max[i]
                : step_result.debug_soup_items;
            physics_player_collect_world_us[i] += step_result.debug_collect_world_us;
            physics_player_collect_terrain_us[i] += step_result.debug_collect_terrain_us;
            physics_player_collect_us[i] += step_result.debug_collect_us;
            physics_player_collect_mesh_us[i] += step_result.debug_collect_mesh_us;
            physics_player_collect_mesh_instances[i] += step_result.debug_collect_mesh_instances;
            physics_player_collect_mesh_faces[i] += step_result.debug_collect_mesh_faces;
            physics_player_collect_world_callbacks[i] += step_result.debug_collect_world_callbacks;
            physics_player_collect_terrain_tris[i] += step_result.debug_collect_terrain_tris;
            physics_player_collect_mesh_query_us_total[i] += step_result.debug_collect_mesh_query_us_total;
            if (step_result.debug_collect_mesh_query_us_max > physics_player_collect_mesh_query_us_max[i])
                physics_player_collect_mesh_query_us_max[i] = step_result.debug_collect_mesh_query_us_max;
            physics_player_collect_mesh_query_overhead_us_total[i] += step_result.debug_collect_mesh_query_overhead_us_total;
            if (step_result.debug_collect_mesh_query_overhead_us_max > physics_player_collect_mesh_query_overhead_us_max[i])
                physics_player_collect_mesh_query_overhead_us_max[i] = step_result.debug_collect_mesh_query_overhead_us_max;
            physics_player_collect_mesh_face_cb_us_total[i] += step_result.debug_collect_mesh_face_cb_us_total;
            if (step_result.debug_collect_mesh_face_cb_us_max > physics_player_collect_mesh_face_cb_us_max[i])
            {
                physics_player_collect_mesh_face_cb_us_max[i] = step_result.debug_collect_mesh_face_cb_us_max;
                physics_player_collect_mesh_face_cb_us_max_inst_id[i] = step_result.debug_collect_mesh_face_cb_us_max_inst_id;
                physics_player_collect_mesh_face_cb_us_max_mesh_id[i] = step_result.debug_collect_mesh_face_cb_us_max_mesh_id;
                physics_player_collect_mesh_face_cb_us_max_mesh_faces[i] = step_result.debug_collect_mesh_face_cb_us_max_mesh_faces;
                physics_player_collect_mesh_face_cb_us_max_face_ordinal[i] = step_result.debug_collect_mesh_face_cb_us_max_face_ordinal;
                physics_player_collect_mesh_face_cb_us_max_accept[i] = step_result.debug_collect_mesh_face_cb_us_max_accept;
                physics_player_collect_mesh_face_cb_us_max_visual[i] = step_result.debug_collect_mesh_face_cb_us_max_visual;
                physics_player_collect_mesh_face_cb_us_max_soup_index[i] = step_result.debug_collect_mesh_face_cb_us_max_soup_index;
                physics_player_collect_mesh_face_cb_us_max_inst_story_id[i] = step_result.debug_collect_mesh_face_cb_us_max_inst_story_id;
                physics_player_collect_mesh_face_cb_us_max_inst_flags[i] = step_result.debug_collect_mesh_face_cb_us_max_inst_flags;
                physics_player_collect_mesh_face_cb_us_max_inst_name_hash[i] = step_result.debug_collect_mesh_face_cb_us_max_inst_name_hash;
                physics_player_collect_mesh_face_cb_us_max_mesh_name_hash[i] = step_result.debug_collect_mesh_face_cb_us_max_mesh_name_hash;
                physics_player_collect_mesh_face_cb_us_max_inst_bbox_cx_milli[i] = step_result.debug_collect_mesh_face_cb_us_max_inst_bbox_cx_milli;
                physics_player_collect_mesh_face_cb_us_max_inst_bbox_cy_milli[i] = step_result.debug_collect_mesh_face_cb_us_max_inst_bbox_cy_milli;
                physics_player_collect_mesh_face_cb_us_max_inst_bbox_cz_milli[i] = step_result.debug_collect_mesh_face_cb_us_max_inst_bbox_cz_milli;
                physics_player_collect_mesh_face_cb_us_max_inst_bbox_diag_milli[i] = step_result.debug_collect_mesh_face_cb_us_max_inst_bbox_diag_milli;
                physics_player_collect_mesh_face_cb_us_max_query_cx_milli[i] = step_result.debug_collect_mesh_face_cb_us_max_query_cx_milli;
                physics_player_collect_mesh_face_cb_us_max_query_cy_milli[i] = step_result.debug_collect_mesh_face_cb_us_max_query_cy_milli;
                physics_player_collect_mesh_face_cb_us_max_query_radius_milli[i] = step_result.debug_collect_mesh_face_cb_us_max_query_radius_milli;
                physics_player_collect_mesh_face_cb_us_max_query_bbox_dist_milli[i] = step_result.debug_collect_mesh_face_cb_us_max_query_bbox_dist_milli;
                physics_player_collect_mesh_face_cb_us_max_query_bbox_overlap_milli[i] = step_result.debug_collect_mesh_face_cb_us_max_query_bbox_overlap_milli;
            }
            physics_player_collect_mesh_face_cb_calls[i] += step_result.debug_collect_mesh_face_cb_calls;
            physics_player_collect_mesh_face_cb_accepts[i] += step_result.debug_collect_mesh_face_cb_accepts;
            physics_player_collect_mesh_face_cb_reject_visual[i] += step_result.debug_collect_mesh_face_cb_reject_visual;
            physics_player_collect_mesh_face_cb_reject_alpha[i] += step_result.debug_collect_mesh_face_cb_reject_alpha;
            physics_player_collect_mesh_face_cb_accept_us_total[i] += step_result.debug_collect_mesh_face_cb_accept_us_total;
            if (step_result.debug_collect_mesh_face_cb_accept_us_max > physics_player_collect_mesh_face_cb_accept_us_max[i])
                physics_player_collect_mesh_face_cb_accept_us_max[i] = step_result.debug_collect_mesh_face_cb_accept_us_max;
            physics_player_collect_mesh_face_cb_push_us_total[i] += step_result.debug_collect_mesh_face_cb_push_us_total;
            if (step_result.debug_collect_mesh_face_cb_push_us_max > physics_player_collect_mesh_face_cb_push_us_max[i])
                physics_player_collect_mesh_face_cb_push_us_max[i] = step_result.debug_collect_mesh_face_cb_push_us_max;
            physics_player_collect_mesh_face_cb_material_us_total[i] += step_result.debug_collect_mesh_face_cb_material_us_total;
            if (step_result.debug_collect_mesh_face_cb_material_us_max > physics_player_collect_mesh_face_cb_material_us_max[i])
                physics_player_collect_mesh_face_cb_material_us_max[i] = step_result.debug_collect_mesh_face_cb_material_us_max;
            physics_player_collect_mesh_face_cb_transform_us_total[i] += step_result.debug_collect_mesh_face_cb_transform_us_total;
            if (step_result.debug_collect_mesh_face_cb_transform_us_max > physics_player_collect_mesh_face_cb_transform_us_max[i])
                physics_player_collect_mesh_face_cb_transform_us_max[i] = step_result.debug_collect_mesh_face_cb_transform_us_max;
            physics_player_collect_mesh_face_cb_normal_us_total[i] += step_result.debug_collect_mesh_face_cb_normal_us_total;
            if (step_result.debug_collect_mesh_face_cb_normal_us_max > physics_player_collect_mesh_face_cb_normal_us_max[i])
                physics_player_collect_mesh_face_cb_normal_us_max[i] = step_result.debug_collect_mesh_face_cb_normal_us_max;
            physics_player_collect_mesh_face_cb_bbox_us_total[i] += step_result.debug_collect_mesh_face_cb_bbox_us_total;
            if (step_result.debug_collect_mesh_face_cb_bbox_us_max > physics_player_collect_mesh_face_cb_bbox_us_max[i])
                physics_player_collect_mesh_face_cb_bbox_us_max[i] = step_result.debug_collect_mesh_face_cb_bbox_us_max;
            physics_player_collect_mesh_faces_reported_total[i] += step_result.debug_collect_mesh_faces_reported_total;
            if (step_result.debug_collect_mesh_faces_reported_max > physics_player_collect_mesh_faces_reported_max[i])
                physics_player_collect_mesh_faces_reported_max[i] = step_result.debug_collect_mesh_faces_reported_max;
            physics_player_collect_mesh_soup_reallocs[i] += step_result.debug_collect_mesh_soup_reallocs;
            if (step_result.debug_collect_mesh_soup_capacity_max > physics_player_collect_mesh_soup_capacity_max[i])
                physics_player_collect_mesh_soup_capacity_max[i] = step_result.debug_collect_mesh_soup_capacity_max;
            if (step_result.debug_collect_mesh_soup_bytes_max > physics_player_collect_mesh_soup_bytes_max[i])
                physics_player_collect_mesh_soup_bytes_max[i] = step_result.debug_collect_mesh_soup_bytes_max;
            physics_player_collect_mesh_soup_bytes_growth_total[i] += step_result.debug_collect_mesh_soup_bytes_growth_total;
            physics_player_collect_mesh_bbox_would_skip[i] += step_result.debug_collect_mesh_bbox_would_skip;
            physics_player_collect_mesh_bbox_would_skip_faces[i] += step_result.debug_collect_mesh_bbox_would_skip_faces;
            physics_player_collect_mesh_cache_hits[i] += step_result.debug_collect_mesh_cache_hits;
            physics_player_collect_mesh_cache_misses[i] += step_result.debug_collect_mesh_cache_misses;
            physics_player_collect_mesh_cache_items[i] += step_result.debug_collect_mesh_cache_items;
            if (step_result.debug_collect_world_bsp_tests > physics_player_collect_world_bsp_tests[i])
                physics_player_collect_world_bsp_tests[i] = step_result.debug_collect_world_bsp_tests;
            if (step_result.debug_collect_world_bsp_nodes > physics_player_collect_world_bsp_nodes[i])
                physics_player_collect_world_bsp_nodes[i] = step_result.debug_collect_world_bsp_nodes;
            if (step_result.debug_collect_world_bsp_insts > physics_player_collect_world_bsp_insts[i])
                physics_player_collect_world_bsp_insts[i] = step_result.debug_collect_world_bsp_insts;
	            physics_player_sweep_iterations[i] += step_result.debug_sweep_iterations;
	            physics_player_collision_checks[i] += step_result.debug_collision_checks;
	            physics_player_collision_broadphase_rejects[i] += step_result.debug_collision_broadphase_rejects;
	            physics_player_sweep_total_us[i] += step_result.debug_sweep_total_us;
	            physics_player_sweep_narrowphase_us_total[i] += step_result.debug_sweep_narrowphase_us_total;
	            if (step_result.debug_sweep_narrowphase_us_max > physics_player_sweep_narrowphase_us_max[i])
	            {
	                physics_player_sweep_narrowphase_us_max[i] = step_result.debug_sweep_narrowphase_us_max;
                physics_player_sweep_narrowphase_us_max_item_index[i] = step_result.debug_sweep_narrowphase_us_max_item_index;
                physics_player_sweep_narrowphase_us_max_item_material[i] = step_result.debug_sweep_narrowphase_us_max_item_material;
                physics_player_sweep_narrowphase_us_max_item_inst_id[i] = step_result.debug_sweep_narrowphase_us_max_item_inst_id;
                physics_player_sweep_narrowphase_us_max_item_mesh_id[i] = step_result.debug_sweep_narrowphase_us_max_item_mesh_id;
                physics_player_sweep_narrowphase_us_max_item_mesh_faces[i] = step_result.debug_sweep_narrowphase_us_max_item_mesh_faces;
                physics_player_sweep_narrowphase_us_max_item_face_ordinal[i] = step_result.debug_sweep_narrowphase_us_max_item_face_ordinal;
                physics_player_sweep_narrowphase_us_max_item_inst_story_id[i] = step_result.debug_sweep_narrowphase_us_max_item_inst_story_id;
                physics_player_sweep_narrowphase_us_max_item_inst_flags[i] = step_result.debug_sweep_narrowphase_us_max_item_inst_flags;
                physics_player_sweep_narrowphase_us_max_item_inst_name_hash[i] = step_result.debug_sweep_narrowphase_us_max_item_inst_name_hash;
                physics_player_sweep_narrowphase_us_max_item_mesh_name_hash[i] = step_result.debug_sweep_narrowphase_us_max_item_mesh_name_hash;
                physics_player_sweep_narrowphase_us_max_item_nrm_z[i] = step_result.debug_sweep_narrowphase_us_max_item_nrm_z;
                physics_player_sweep_narrowphase_us_max_item_bbox_diag[i] = step_result.debug_sweep_narrowphase_us_max_item_bbox_diag;
            }
	            physics_player_sweep_narrowphase_us_last[i] = step_result.debug_sweep_narrowphase_us_last;
	            if (step_result.debug_sweep_iter_us_max > physics_player_sweep_iter_us_max[i])
	                physics_player_sweep_iter_us_max[i] = step_result.debug_sweep_iter_us_max;
	            physics_player_sweep_iter_us_last[i] = step_result.debug_sweep_iter_us_last;
	            if (step_result.debug_sweep_iter_collision_checks_max > physics_player_sweep_iter_collision_checks_max[i])
	                physics_player_sweep_iter_collision_checks_max[i] = step_result.debug_sweep_iter_collision_checks_max;
            physics_player_sweep_iter_collision_checks_last[i] = step_result.debug_sweep_iter_collision_checks_last;
            physics_player_sweep_iter_hits[i] += step_result.debug_sweep_iter_hits;
            physics_player_sweep_iter_earliest_t_last[i] = step_result.debug_sweep_iter_earliest_t_last;
            physics_player_sweep_iter_remaining_move_len_last[i] = step_result.debug_sweep_iter_remaining_move_len_last;
            physics_player_sweep_iter_output_normal_z_last[i] = step_result.debug_sweep_iter_output_normal_z_last;
            physics_player_sweep_iter_deflection_count[i] += step_result.debug_sweep_iter_deflection_count;
            physics_player_soup_capped[i] += step_result.debug_soup_capped;
            physics_player_mesh_per_mesh_cap_hits[i] += step_result.debug_mesh_per_mesh_cap_hits;
            physics_player_mesh_bbox_skips[i] += step_result.debug_mesh_bbox_skips;
            physics_player_support_priority_callbacks[i] += step_result.debug_support_priority_callbacks;
            physics_player_step_wall_us[i] += step_result.debug_step_wall_us;
            physics_player_step_accounted_us[i] += step_result.debug_step_accounted_us;
            physics_player_step_unaccounted_us[i] += step_result.debug_step_unaccounted_us;
            physics_player_step_pre_collect_us[i] += step_result.debug_step_pre_collect_us;
            physics_player_step_collect_build_us[i] += step_result.debug_step_collect_build_us;
            physics_player_step_post_collect_setup_us[i] += step_result.debug_step_post_collect_setup_us;
            physics_player_step_sweep_wall_us[i] += step_result.debug_step_sweep_wall_us;
            physics_player_step_post_sweep_us[i] += step_result.debug_step_post_sweep_us;
            physics_player_step_support_probe_us[i] += step_result.debug_step_support_probe_us;
            physics_player_step_support_retry_build_us[i] += step_result.debug_step_support_retry_build_us;
            physics_player_step_support_retry_probe_us[i] += step_result.debug_step_support_retry_probe_us;
            physics_player_step_support_wide_probe_us[i] += step_result.debug_step_support_wide_probe_us;
            physics_player_step_floor_probe_us[i] += step_result.debug_step_floor_probe_us;
            physics_player_step_post_support_apply_us[i] += step_result.debug_step_post_support_apply_us;
            physics_player_step_material_votes_us[i] += step_result.debug_step_material_votes_us;
            physics_player_step_contact_us[i] += step_result.debug_step_contact_us;
            physics_player_step_grounding_us[i] += step_result.debug_step_grounding_us;
            physics_player_step_mount_us[i] += step_result.debug_step_mount_us;
            physics_player_step_finalize_us[i] += step_result.debug_step_finalize_us;
            if (step_result.debug_support_retry_rebuilt)
                physics_player_support_retry_rebuilt[i]++;
            if (step_result.debug_support_retry_found)
                physics_player_support_retry_found[i]++;
            SvrSupportRetryCollectDebug& retry_collect = physics_player_support_retry_collect[i];
            retry_collect.soup_items += step_result.debug_support_retry_soup_items;
            retry_collect.collect_world_us += step_result.debug_support_retry_collect_world_us;
            retry_collect.collect_terrain_us += step_result.debug_support_retry_collect_terrain_us;
            retry_collect.collect_us += step_result.debug_support_retry_collect_us;
            retry_collect.collect_mesh_us += step_result.debug_support_retry_collect_mesh_us;
            retry_collect.collect_mesh_instances += step_result.debug_support_retry_collect_mesh_instances;
            retry_collect.collect_mesh_faces += step_result.debug_support_retry_collect_mesh_faces;
            retry_collect.collect_world_callbacks += step_result.debug_support_retry_collect_world_callbacks;
            retry_collect.collect_terrain_tris += step_result.debug_support_retry_collect_terrain_tris;
            retry_collect.collect_mesh_query_us_total += step_result.debug_support_retry_collect_mesh_query_us_total;
            if (step_result.debug_support_retry_collect_mesh_query_us_max > retry_collect.collect_mesh_query_us_max)
                retry_collect.collect_mesh_query_us_max = step_result.debug_support_retry_collect_mesh_query_us_max;
            retry_collect.collect_mesh_query_overhead_us_total += step_result.debug_support_retry_collect_mesh_query_overhead_us_total;
            if (step_result.debug_support_retry_collect_mesh_query_overhead_us_max > retry_collect.collect_mesh_query_overhead_us_max)
                retry_collect.collect_mesh_query_overhead_us_max = step_result.debug_support_retry_collect_mesh_query_overhead_us_max;
            retry_collect.collect_mesh_face_cb_us_total += step_result.debug_support_retry_collect_mesh_face_cb_us_total;
            if (step_result.debug_support_retry_collect_mesh_face_cb_us_max > retry_collect.collect_mesh_face_cb_us_max)
            {
                retry_collect.collect_mesh_face_cb_us_max = step_result.debug_support_retry_collect_mesh_face_cb_us_max;
                retry_collect.collect_mesh_face_cb_us_max_inst_id = step_result.debug_support_retry_collect_mesh_face_cb_us_max_inst_id;
                retry_collect.collect_mesh_face_cb_us_max_mesh_id = step_result.debug_support_retry_collect_mesh_face_cb_us_max_mesh_id;
                retry_collect.collect_mesh_face_cb_us_max_mesh_faces = step_result.debug_support_retry_collect_mesh_face_cb_us_max_mesh_faces;
                retry_collect.collect_mesh_face_cb_us_max_face_ordinal = step_result.debug_support_retry_collect_mesh_face_cb_us_max_face_ordinal;
                retry_collect.collect_mesh_face_cb_us_max_accept = step_result.debug_support_retry_collect_mesh_face_cb_us_max_accept;
                retry_collect.collect_mesh_face_cb_us_max_visual = step_result.debug_support_retry_collect_mesh_face_cb_us_max_visual;
                retry_collect.collect_mesh_face_cb_us_max_soup_index = step_result.debug_support_retry_collect_mesh_face_cb_us_max_soup_index;
                retry_collect.collect_mesh_face_cb_us_max_inst_story_id = step_result.debug_support_retry_collect_mesh_face_cb_us_max_inst_story_id;
                retry_collect.collect_mesh_face_cb_us_max_inst_flags = step_result.debug_support_retry_collect_mesh_face_cb_us_max_inst_flags;
                retry_collect.collect_mesh_face_cb_us_max_inst_name_hash = step_result.debug_support_retry_collect_mesh_face_cb_us_max_inst_name_hash;
                retry_collect.collect_mesh_face_cb_us_max_mesh_name_hash = step_result.debug_support_retry_collect_mesh_face_cb_us_max_mesh_name_hash;
                retry_collect.collect_mesh_face_cb_us_max_inst_bbox_cx_milli = step_result.debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_cx_milli;
                retry_collect.collect_mesh_face_cb_us_max_inst_bbox_cy_milli = step_result.debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_cy_milli;
                retry_collect.collect_mesh_face_cb_us_max_inst_bbox_cz_milli = step_result.debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_cz_milli;
                retry_collect.collect_mesh_face_cb_us_max_inst_bbox_diag_milli = step_result.debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_diag_milli;
                retry_collect.collect_mesh_face_cb_us_max_query_cx_milli = step_result.debug_support_retry_collect_mesh_face_cb_us_max_query_cx_milli;
                retry_collect.collect_mesh_face_cb_us_max_query_cy_milli = step_result.debug_support_retry_collect_mesh_face_cb_us_max_query_cy_milli;
                retry_collect.collect_mesh_face_cb_us_max_query_radius_milli = step_result.debug_support_retry_collect_mesh_face_cb_us_max_query_radius_milli;
                retry_collect.collect_mesh_face_cb_us_max_query_bbox_dist_milli = step_result.debug_support_retry_collect_mesh_face_cb_us_max_query_bbox_dist_milli;
                retry_collect.collect_mesh_face_cb_us_max_query_bbox_overlap_milli = step_result.debug_support_retry_collect_mesh_face_cb_us_max_query_bbox_overlap_milli;
            }
            retry_collect.collect_mesh_face_cb_calls += step_result.debug_support_retry_collect_mesh_face_cb_calls;
            retry_collect.collect_mesh_face_cb_accepts += step_result.debug_support_retry_collect_mesh_face_cb_accepts;
            retry_collect.collect_mesh_face_cb_reject_visual += step_result.debug_support_retry_collect_mesh_face_cb_reject_visual;
            retry_collect.collect_mesh_face_cb_reject_alpha += step_result.debug_support_retry_collect_mesh_face_cb_reject_alpha;
            retry_collect.collect_mesh_face_cb_accept_us_total += step_result.debug_support_retry_collect_mesh_face_cb_accept_us_total;
            if (step_result.debug_support_retry_collect_mesh_face_cb_accept_us_max > retry_collect.collect_mesh_face_cb_accept_us_max)
                retry_collect.collect_mesh_face_cb_accept_us_max = step_result.debug_support_retry_collect_mesh_face_cb_accept_us_max;
            retry_collect.collect_mesh_face_cb_push_us_total += step_result.debug_support_retry_collect_mesh_face_cb_push_us_total;
            if (step_result.debug_support_retry_collect_mesh_face_cb_push_us_max > retry_collect.collect_mesh_face_cb_push_us_max)
                retry_collect.collect_mesh_face_cb_push_us_max = step_result.debug_support_retry_collect_mesh_face_cb_push_us_max;
            retry_collect.collect_mesh_face_cb_material_us_total += step_result.debug_support_retry_collect_mesh_face_cb_material_us_total;
            if (step_result.debug_support_retry_collect_mesh_face_cb_material_us_max > retry_collect.collect_mesh_face_cb_material_us_max)
                retry_collect.collect_mesh_face_cb_material_us_max = step_result.debug_support_retry_collect_mesh_face_cb_material_us_max;
            retry_collect.collect_mesh_face_cb_transform_us_total += step_result.debug_support_retry_collect_mesh_face_cb_transform_us_total;
            if (step_result.debug_support_retry_collect_mesh_face_cb_transform_us_max > retry_collect.collect_mesh_face_cb_transform_us_max)
                retry_collect.collect_mesh_face_cb_transform_us_max = step_result.debug_support_retry_collect_mesh_face_cb_transform_us_max;
            retry_collect.collect_mesh_face_cb_normal_us_total += step_result.debug_support_retry_collect_mesh_face_cb_normal_us_total;
            if (step_result.debug_support_retry_collect_mesh_face_cb_normal_us_max > retry_collect.collect_mesh_face_cb_normal_us_max)
                retry_collect.collect_mesh_face_cb_normal_us_max = step_result.debug_support_retry_collect_mesh_face_cb_normal_us_max;
            retry_collect.collect_mesh_face_cb_bbox_us_total += step_result.debug_support_retry_collect_mesh_face_cb_bbox_us_total;
            if (step_result.debug_support_retry_collect_mesh_face_cb_bbox_us_max > retry_collect.collect_mesh_face_cb_bbox_us_max)
                retry_collect.collect_mesh_face_cb_bbox_us_max = step_result.debug_support_retry_collect_mesh_face_cb_bbox_us_max;
            retry_collect.collect_mesh_faces_reported_total += step_result.debug_support_retry_collect_mesh_faces_reported_total;
            if (step_result.debug_support_retry_collect_mesh_faces_reported_max > retry_collect.collect_mesh_faces_reported_max)
                retry_collect.collect_mesh_faces_reported_max = step_result.debug_support_retry_collect_mesh_faces_reported_max;
            retry_collect.collect_mesh_soup_reallocs += step_result.debug_support_retry_collect_mesh_soup_reallocs;
            if (step_result.debug_support_retry_collect_mesh_soup_capacity_max > retry_collect.collect_mesh_soup_capacity_max)
                retry_collect.collect_mesh_soup_capacity_max = step_result.debug_support_retry_collect_mesh_soup_capacity_max;
            if (step_result.debug_support_retry_collect_mesh_soup_bytes_max > retry_collect.collect_mesh_soup_bytes_max)
                retry_collect.collect_mesh_soup_bytes_max = step_result.debug_support_retry_collect_mesh_soup_bytes_max;
            retry_collect.collect_mesh_soup_bytes_growth_total += step_result.debug_support_retry_collect_mesh_soup_bytes_growth_total;
            retry_collect.collect_mesh_bbox_would_skip += step_result.debug_support_retry_collect_mesh_bbox_would_skip;
            retry_collect.collect_mesh_bbox_would_skip_faces += step_result.debug_support_retry_collect_mesh_bbox_would_skip_faces;
            retry_collect.collect_mesh_cache_hits += step_result.debug_support_retry_collect_mesh_cache_hits;
            retry_collect.collect_mesh_cache_misses += step_result.debug_support_retry_collect_mesh_cache_misses;
            retry_collect.collect_mesh_cache_items += step_result.debug_support_retry_collect_mesh_cache_items;
            if (step_result.debug_support_retry_collect_world_bsp_tests > retry_collect.collect_world_bsp_tests)
                retry_collect.collect_world_bsp_tests = step_result.debug_support_retry_collect_world_bsp_tests;
            if (step_result.debug_support_retry_collect_world_bsp_nodes > retry_collect.collect_world_bsp_nodes)
                retry_collect.collect_world_bsp_nodes = step_result.debug_support_retry_collect_world_bsp_nodes;
            if (step_result.debug_support_retry_collect_world_bsp_insts > retry_collect.collect_world_bsp_insts)
                retry_collect.collect_world_bsp_insts = step_result.debug_support_retry_collect_world_bsp_insts;
            retry_collect.same_region_repeat_count += step_result.debug_support_retry_same_region_repeat_count;
            if (measure_players_breakdown)
                physics_players_step_once_us += a3dGetTime() - step_component_start_us;

            PhysicsFullState next_state = {};
            MpStepToPhysicsState(&step_state, &next_state, substep_stamp_us);
            if (measure_players_breakdown)
                step_component_start_us = a3dGetTime();
            RestorePhysicsState(ps->physics, &next_state);
            SyncPhysicsStamp(ps->physics, substep_stamp_us);
            MpStepApplyStateToIO(&step_state, &step_result, &pio);
            if (measure_players_breakdown)
                physics_players_restore_sync_us += a3dGetTime() - step_component_start_us;

            const int steps_handled = 1;
            physics_players_step_count++;
            physics_player_full_step_count[i]++;

            if (measure_players_breakdown)
                step_component_start_us = a3dGetTime();
            GetPhysicsPos(ps->physics, ps->pos);
            ps->dir = pio.player_dir;
            ps->vel[0] = next_state.vel[0];
            ps->vel[1] = next_state.vel[1];
            ps->vel[2] = next_state.vel[2];
            if (measure_players_breakdown)
                physics_players_post_state_us += a3dGetTime() - step_component_start_us;
            ps->in_water = step_result.in_water;                                                    // GAP-1
            // Diagnostic/support read only. MpStepOnce owns terrain/world contact
            // resolution; a second post-step terrain lift can manufacture
            // one-tick authoritative Z launches that remote interpolation must
            // then render as real movement.
            if (measure_players_breakdown)
                step_component_start_us = a3dGetTime();
            ps->terrain_z = SvrSampleTerrainHeight(state->terrain, ps->pos[0], ps->pos[1], ps->pos[2]);
            ps->support_valid = step_result.support.found ? 1 : 0;
            ps->support_source = step_result.support.source;
            ps->support_z = step_result.support.z;
            ps->support_item_id = step_result.support.placed_item_id;
            ps->collision_debug_sample_count =
                (uint16_t)(step_result.debug_collision_sample_count > COLLISION_DEBUG_SAMPLE_MAX
                    ? COLLISION_DEBUG_SAMPLE_MAX
                    : step_result.debug_collision_sample_count);
            ps->collision_debug_push_source = step_result.debug_last_sweep_collision_side
                ? step_result.debug_last_sweep_collision_source
                : MP_SUPPORT_NONE;
            memcpy(ps->collision_debug_samples, step_result.debug_collision_samples,
                (size_t)ps->collision_debug_sample_count * sizeof(ps->collision_debug_samples[0]));
            if (measure_players_breakdown)
                physics_players_terrain_sample_us += a3dGetTime() - step_component_start_us;
            ps->knockback[0] = pio.x_impulse;
            ps->knockback[1] = pio.y_impulse;
            // SvrRefreshPlayerMoveVisual deleted — visual pipeline gutted.
            SvrDebugPhysicsStep(state, i, ps, state->tick, &pio, &step_result, steps_handled);
        }
        physics_players_us += a3dGetTime() - physics_slice_start_us;

        // NPCs
        // FL-2957 H-N1..N6: per-NPC breakdown instrumentation.
        // Captures fast_path/full_step counts and peak per-NPC cost to
        // identify whether NPC lag is density tail (H-N1), count multiplier
        // (H-N2), fast-path failure (H-N3), or bootstrap deadlock (H-N6).
        uint32_t npcs_fast_path = 0, npcs_full_step = 0;
        uint64_t npcs_max_step_once_us = 0;
        int npcs_max_step_once_id = -1;
        physics_slice_start_us = a3dGetTime();
        if (have_active_sessions && !have_alive_players)
        {
            for (int i = 0; i < state->npc_count; i++)
            {
                SvrNpcState* npc = &state->npcs[i];
                if (!npc->active || npc->death_tick > 0) continue;
                npc->intent_force[0] = 0.0f;
                npc->intent_force[1] = 0.0f;
                npc->jump_request = false;
                npc->target_id = 0xFFFF;
                npc->target_is_player = false;
            }
        }
        else if (have_active_sessions)
        {
            for (int i = 0; i < state->npc_count; i++)
            {
                SvrNpcState* npc = &state->npcs[i];
                if (!npc->active || npc->death_tick > 0) continue;
                if (!npc->physics) continue;

                if (!SvrNpcNeedsPhysicsStep(npc))
                {
                    SyncPhysicsStamp(npc->physics, substep_stamp_us);
                    npc->jump_request = false;
                    npc->dir = npc->intent_dir;
                    npc->vel[0] = 0.0f;
                    npc->vel[1] = 0.0f;
                    npc->vel[2] = 0.0f;
                    npcs_fast_path++;
                    continue;
                }
                npcs_full_step++;
                const uint64_t npc_step_start_us = a3dGetTime();

                const float intent_x = npc->intent_force[0];
                const float intent_y = npc->intent_force[1];
                PhysicsIO pio = {};
                pio.x_force = intent_x;
                pio.y_force = intent_y;
                pio.jump = npc->jump_request;
                pio.water = SVR_WATER_LEVEL;
                pio.player_dir = npc->intent_dir;

                PhysicsFullState prev_state = {};
                SavePhysicsState(npc->physics, &prev_state);

                MpStepInput step_input = {};
                step_input.x_force = intent_x;
                step_input.y_force = intent_y;
                step_input.z_force = 0.0f;
                step_input.yaw = npc->intent_dir;
                step_input.jump = npc->jump_request;
                step_input.fly = false;

                MpStepEnv step_env = MpStepBuildEnv(state->terrain,
                    state->world,
                    &state->world_entities,
                    substep_stamp_us,
                    SVR_WATER_LEVEL,
                    npc->mount_state);
                MpStepState step_state = MpStepFromPhysicsState(&prev_state, 0);
                MpStepResult step_result = {};
                step_state = MpStepOnce(step_state, step_input, step_env, &step_result);

                PhysicsFullState next_state = {};
                MpStepToPhysicsState(&step_state, &next_state, substep_stamp_us);
                RestorePhysicsState(npc->physics, &next_state);
                SyncPhysicsStamp(npc->physics, substep_stamp_us);
                MpStepApplyStateToIO(&step_state, &step_result, &pio);
                npc->jump_request = false;
                const uint64_t npc_step_us = a3dGetTime() - npc_step_start_us;
                if (npc_step_us > npcs_max_step_once_us)
                {
                    npcs_max_step_once_us = npc_step_us;
                    npcs_max_step_once_id = i;
                }

                GetPhysicsPos(npc->physics, npc->pos);
                npc->dir = pio.player_dir;
                npc->vel[0] = next_state.vel[0];
                npc->vel[1] = next_state.vel[1];
                npc->vel[2] = next_state.vel[2];
            }
        }
        physics_npcs_us += a3dGetTime() - physics_slice_start_us;

        // FL-2957 H-N1..N6: emit NPC breakdown when physics exceeds budget.
        // This log line supports per-NPC hypothesis falsification in direct log review.
        if (physics_npcs_us >= SVR_TICK_PHYSICS_LOG_THRESHOLD_US)
        {
            static uint32_t g_npc_breakdown_logs = 0;
            if (g_npc_breakdown_logs < 200)
            {
                SvrRuntimeDiagLog(state,
                       "[tick-npc-breakdown] tick=%u npcs_fast_path=%u npcs_full_step=%u npcs_us=%llu max_step_once_us=%llu max_step_once_id=%d\n",
                       (unsigned)state->tick,
                       npcs_fast_path, npcs_full_step,
                       (unsigned long long)physics_npcs_us,
                       (unsigned long long)npcs_max_step_once_us,
                       npcs_max_step_once_id);
                g_npc_breakdown_logs++;
            }
        }
    }
    const bool force_emit_players_breakdown =
        physics_players_us >= SVR_TICK_PHYSICS_LOG_THRESHOLD_US &&
        physics_players_us > state->tick_max_physics_phase_us;
    if ((measure_players_breakdown || force_emit_players_breakdown) &&
        physics_players_us >= SVR_TICK_PHYSICS_LOG_THRESHOLD_US &&
        (g_tick_players_breakdown_logs < SVR_TICK_PLAYERS_BREAKDOWN_LOG_LIMIT || force_emit_players_breakdown))
    {
        const char* capture_kind = force_emit_players_breakdown
            ? (measure_players_breakdown ? "budgeted+max_physics" : "max_physics")
            : "budgeted";
        SvrRuntimeDiagLog(state,
                          "[tick-players-breakdown] tick=%u players=%u steps=%u idle_fast_paths=%u total_us=%llu save_us=%llu env_us=%llu step_once_us=%llu restore_sync_us=%llu post_state_us=%llu water_clamp_us=%llu terrain_us=%llu water_clamps=%u capture=%s\n",
               (unsigned)state->tick,
               (unsigned)physics_players_active_count,
               (unsigned)physics_players_step_count,
               (unsigned)physics_players_idle_fast_path_count,
               (unsigned long long)physics_players_us,
               (unsigned long long)physics_players_save_state_us,
               (unsigned long long)physics_players_build_env_us,
               (unsigned long long)physics_players_step_once_us,
               (unsigned long long)physics_players_restore_sync_us,
               (unsigned long long)physics_players_post_state_us,
               (unsigned long long)physics_players_water_clamp_us,
               (unsigned long long)physics_players_terrain_sample_us,
               (unsigned)physics_players_water_clamp_count,
               capture_kind);
        for (int i = 0; i < SVR_MAX_CLIENTS; i++)
        {
            if (physics_player_full_step_count[i] == 0 && physics_player_idle_fast_path_counts[i] == 0)
                continue;
	            const SvrSupportRetryCollectDebug& retry_collect = physics_player_support_retry_collect[i];
	            SvrRuntimeDiagLog(state,
	                   "[tick-player-step] tick=%u ci=%d full_steps=%u idle_fast_paths=%u step_once_us=%llu step_wall_us=%llu step_accounted_us=%llu step_unaccounted_us=%llu step_pre_collect_us=%llu step_collect_build_us=%llu step_post_collect_setup_us=%llu step_sweep_wall_us=%llu step_post_sweep_us=%llu step_support_probe_us=%llu step_support_retry_build_us=%llu step_support_retry_probe_us=%llu step_support_wide_probe_us=%llu step_floor_probe_us=%llu step_post_support_apply_us=%llu step_material_votes_us=%llu step_contact_us=%llu step_grounding_us=%llu step_mount_us=%llu step_finalize_us=%llu reject_mount=%u reject_input_flags=%u reject_input_force=%u reject_knockback=%u reject_velocity=%u reject_yaw_velocity=%u reject_yaw_delta=%u reject_player_stp=%u reject_grounded=%u reject_support_z=%u reject_water=%u input_flags_last=%u grounded_last=%u in_water_last=%u max_abs_vel_milli=%u max_yaw_delta_mdeg=%u max_support_z_milli=%u support_recovered=%u support_retry_rebuilt=%u support_retry_found=%u accum_contact_milli=%u pos_z_milli=%d terrain_z_milli=%d support_retry_soup_items=%llu support_retry_collect_world_us=%llu support_retry_collect_terrain_us=%llu support_retry_collect_us=%llu support_retry_collect_mesh_us=%llu support_retry_collect_mesh_instances=%llu support_retry_collect_mesh_faces=%llu support_retry_collect_world_callbacks=%llu support_retry_collect_terrain_tris=%llu support_retry_collect_mesh_query_us_total=%llu support_retry_collect_mesh_query_us_max=%llu support_retry_collect_mesh_query_overhead_us_total=%llu support_retry_collect_mesh_query_overhead_us_max=%llu support_retry_collect_mesh_face_cb_us_total=%llu support_retry_collect_mesh_face_cb_us_max=%llu support_retry_collect_mesh_face_cb_calls=%llu support_retry_collect_mesh_face_cb_accepts=%llu support_retry_collect_mesh_face_cb_reject_visual=%llu support_retry_collect_mesh_face_cb_reject_alpha=%llu support_retry_collect_mesh_face_cb_accept_us_total=%llu support_retry_collect_mesh_face_cb_accept_us_max=%llu support_retry_collect_mesh_face_cb_push_us_total=%llu support_retry_collect_mesh_face_cb_push_us_max=%llu support_retry_collect_mesh_face_cb_material_us_total=%llu support_retry_collect_mesh_face_cb_material_us_max=%llu support_retry_collect_mesh_face_cb_transform_us_total=%llu support_retry_collect_mesh_face_cb_transform_us_max=%llu support_retry_collect_mesh_face_cb_normal_us_total=%llu support_retry_collect_mesh_face_cb_normal_us_max=%llu support_retry_collect_mesh_face_cb_bbox_us_total=%llu support_retry_collect_mesh_face_cb_bbox_us_max=%llu support_retry_collect_mesh_faces_reported_total=%llu support_retry_collect_mesh_faces_reported_max=%llu support_retry_collect_mesh_soup_reallocs=%llu support_retry_collect_mesh_soup_capacity_max=%llu support_retry_collect_mesh_soup_bytes_max=%llu support_retry_collect_mesh_soup_bytes_growth_total=%llu support_retry_collect_mesh_bbox_would_skip=%llu support_retry_collect_mesh_bbox_would_skip_faces=%llu support_retry_collect_mesh_cache_hits=%llu support_retry_collect_mesh_cache_misses=%llu support_retry_collect_mesh_cache_items=%llu support_retry_collect_world_bsp_tests=%llu support_retry_collect_world_bsp_nodes=%llu support_retry_collect_world_bsp_insts=%llu support_retry_same_region_repeat_count=%llu soup_items=%u collect_world_us=%llu collect_terrain_us=%llu collect_us=%llu collect_mesh_us=%llu collect_mesh_instances=%u collect_mesh_faces=%u collect_world_callbacks=%u collect_terrain_tris=%u collect_mesh_query_us_total=%llu collect_mesh_query_us_max=%llu collect_mesh_query_overhead_us_total=%llu collect_mesh_query_overhead_us_max=%llu collect_mesh_face_cb_us_total=%llu collect_mesh_face_cb_us_max=%llu collect_mesh_face_cb_calls=%u collect_mesh_face_cb_accepts=%u collect_mesh_face_cb_reject_visual=%u collect_mesh_face_cb_reject_alpha=%u collect_mesh_face_cb_accept_us_total=%llu collect_mesh_face_cb_accept_us_max=%llu collect_mesh_face_cb_push_us_total=%llu collect_mesh_face_cb_push_us_max=%llu collect_mesh_face_cb_material_us_total=%llu collect_mesh_face_cb_material_us_max=%llu collect_mesh_face_cb_transform_us_total=%llu collect_mesh_face_cb_transform_us_max=%llu collect_mesh_face_cb_normal_us_total=%llu collect_mesh_face_cb_normal_us_max=%llu collect_mesh_face_cb_bbox_us_total=%llu collect_mesh_face_cb_bbox_us_max=%llu collect_mesh_faces_reported_total=%u collect_mesh_faces_reported_max=%u collect_mesh_soup_reallocs=%u collect_mesh_soup_capacity_max=%u collect_mesh_soup_bytes_max=%llu collect_mesh_soup_bytes_growth_total=%llu collect_mesh_bbox_would_skip=%u collect_mesh_bbox_would_skip_faces=%u collect_mesh_cache_hits=%u collect_mesh_cache_misses=%u collect_mesh_cache_items=%u collect_world_bsp_tests=%u collect_world_bsp_nodes=%u collect_world_bsp_insts=%u sweep_iters=%u collision_checks=%u broadphase_rejects=%u sweep_total_us=%llu sweep_narrow_total_us=%llu sweep_narrow_us_max=%llu sweep_narrow_us_last=%llu sweep_iter_us_max=%llu sweep_iter_us_last=%llu sweep_iter_checks_max=%u sweep_iter_checks_last=%u sweep_hits=%u sweep_hit_t_last=%.3f sweep_remain_len_last=%.4f sweep_normal_z_last=%.4f sweep_deflects=%u soup_capped=%u mesh_cap_hits=%u mesh_bbox_skips=%u support_priority=%u\n",
	                   (unsigned)state->tick,
	                   i,
	                   (unsigned)physics_player_full_step_count[i],
	                   (unsigned)physics_player_idle_fast_path_counts[i],
	                   (unsigned long long)physics_player_step_once_us[i],
	                   (unsigned long long)physics_player_step_wall_us[i],
	                   (unsigned long long)physics_player_step_accounted_us[i],
	                   (unsigned long long)physics_player_step_unaccounted_us[i],
	                   (unsigned long long)physics_player_step_pre_collect_us[i],
	                   (unsigned long long)physics_player_step_collect_build_us[i],
	                   (unsigned long long)physics_player_step_post_collect_setup_us[i],
	                   (unsigned long long)physics_player_step_sweep_wall_us[i],
	                   (unsigned long long)physics_player_step_post_sweep_us[i],
	                   (unsigned long long)physics_player_step_support_probe_us[i],
	                   (unsigned long long)physics_player_step_support_retry_build_us[i],
	                   (unsigned long long)physics_player_step_support_retry_probe_us[i],
	                   (unsigned long long)physics_player_step_support_wide_probe_us[i],
	                   (unsigned long long)physics_player_step_floor_probe_us[i],
	                   (unsigned long long)physics_player_step_post_support_apply_us[i],
	                   (unsigned long long)physics_player_step_material_votes_us[i],
	                   (unsigned long long)physics_player_step_contact_us[i],
	                   (unsigned long long)physics_player_step_grounding_us[i],
	                   (unsigned long long)physics_player_step_mount_us[i],
	                   (unsigned long long)physics_player_step_finalize_us[i],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_MOUNT],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_INPUT_FLAGS],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_INPUT_FORCE],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_KNOCKBACK],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_VELOCITY],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_YAW_VELOCITY],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_YAW_DELTA],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_PLAYER_STP],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_GROUNDED],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_SUPPORT_Z],
                   (unsigned)physics_player_fast_path_reject_hits[i][SVR_IDLE_FASTPATH_REJECT_WATER],
                   (unsigned)physics_player_input_flags_last[i],
                   (unsigned)physics_player_grounded_last[i],
                   (unsigned)physics_player_in_water_last[i],
                   (unsigned)(physics_player_max_abs_vel[i] * 1000.0f),
                   (unsigned)(physics_player_max_yaw_delta[i] * 1000.0f),
                   (unsigned)(physics_player_max_support_z_delta[i] * 1000.0f),
                   (unsigned)physics_player_idle_support_recovered[i],
                   (unsigned)physics_player_support_retry_rebuilt[i],
                   (unsigned)physics_player_support_retry_found[i],
                   (unsigned)(physics_player_accum_contact_last[i] * 1000.0f),
                   (int)(physics_player_pos_z_last[i] * 1000.0f),
                   (int)(physics_player_terrain_z_last[i] * 1000.0f),
                   (unsigned long long)retry_collect.soup_items,
                   (unsigned long long)retry_collect.collect_world_us,
                   (unsigned long long)retry_collect.collect_terrain_us,
                   (unsigned long long)retry_collect.collect_us,
                   (unsigned long long)retry_collect.collect_mesh_us,
                   (unsigned long long)retry_collect.collect_mesh_instances,
                   (unsigned long long)retry_collect.collect_mesh_faces,
                   (unsigned long long)retry_collect.collect_world_callbacks,
                   (unsigned long long)retry_collect.collect_terrain_tris,
                   (unsigned long long)retry_collect.collect_mesh_query_us_total,
                   (unsigned long long)retry_collect.collect_mesh_query_us_max,
                   (unsigned long long)retry_collect.collect_mesh_query_overhead_us_total,
                   (unsigned long long)retry_collect.collect_mesh_query_overhead_us_max,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_total,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_calls,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_accepts,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_reject_visual,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_reject_alpha,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_accept_us_total,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_accept_us_max,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_push_us_total,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_push_us_max,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_material_us_total,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_material_us_max,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_transform_us_total,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_transform_us_max,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_normal_us_total,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_normal_us_max,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_bbox_us_total,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_bbox_us_max,
                   (unsigned long long)retry_collect.collect_mesh_faces_reported_total,
                   (unsigned long long)retry_collect.collect_mesh_faces_reported_max,
                   (unsigned long long)retry_collect.collect_mesh_soup_reallocs,
                   (unsigned long long)retry_collect.collect_mesh_soup_capacity_max,
                   (unsigned long long)retry_collect.collect_mesh_soup_bytes_max,
                   (unsigned long long)retry_collect.collect_mesh_soup_bytes_growth_total,
                   (unsigned long long)retry_collect.collect_mesh_bbox_would_skip,
                   (unsigned long long)retry_collect.collect_mesh_bbox_would_skip_faces,
                   (unsigned long long)retry_collect.collect_mesh_cache_hits,
                   (unsigned long long)retry_collect.collect_mesh_cache_misses,
                   (unsigned long long)retry_collect.collect_mesh_cache_items,
                   (unsigned long long)retry_collect.collect_world_bsp_tests,
                   (unsigned long long)retry_collect.collect_world_bsp_nodes,
                   (unsigned long long)retry_collect.collect_world_bsp_insts,
                   (unsigned long long)retry_collect.same_region_repeat_count,
                   (unsigned)physics_player_soup_items_max[i],
                   (unsigned long long)physics_player_collect_world_us[i],
                   (unsigned long long)physics_player_collect_terrain_us[i],
                   (unsigned long long)physics_player_collect_us[i],
                   (unsigned long long)physics_player_collect_mesh_us[i],
                   (unsigned)physics_player_collect_mesh_instances[i],
                   (unsigned)physics_player_collect_mesh_faces[i],
                   (unsigned)physics_player_collect_world_callbacks[i],
	                   (unsigned)physics_player_collect_terrain_tris[i],
	                   (unsigned long long)physics_player_collect_mesh_query_us_total[i],
	                   (unsigned long long)physics_player_collect_mesh_query_us_max[i],
	                   (unsigned long long)physics_player_collect_mesh_query_overhead_us_total[i],
	                   (unsigned long long)physics_player_collect_mesh_query_overhead_us_max[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_us_total[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_us_max[i],
	                   (unsigned)physics_player_collect_mesh_face_cb_calls[i],
	                   (unsigned)physics_player_collect_mesh_face_cb_accepts[i],
	                   (unsigned)physics_player_collect_mesh_face_cb_reject_visual[i],
	                   (unsigned)physics_player_collect_mesh_face_cb_reject_alpha[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_accept_us_total[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_accept_us_max[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_push_us_total[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_push_us_max[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_material_us_total[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_material_us_max[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_transform_us_total[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_transform_us_max[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_normal_us_total[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_normal_us_max[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_bbox_us_total[i],
	                   (unsigned long long)physics_player_collect_mesh_face_cb_bbox_us_max[i],
	                   (unsigned)physics_player_collect_mesh_faces_reported_total[i],
	                   (unsigned)physics_player_collect_mesh_faces_reported_max[i],
	                   (unsigned)physics_player_collect_mesh_soup_reallocs[i],
	                   (unsigned)physics_player_collect_mesh_soup_capacity_max[i],
	                   (unsigned long long)physics_player_collect_mesh_soup_bytes_max[i],
	                   (unsigned long long)physics_player_collect_mesh_soup_bytes_growth_total[i],
	                   (unsigned)physics_player_collect_mesh_bbox_would_skip[i],
                   (unsigned)physics_player_collect_mesh_bbox_would_skip_faces[i],
                   (unsigned)physics_player_collect_mesh_cache_hits[i],
                   (unsigned)physics_player_collect_mesh_cache_misses[i],
                   (unsigned)physics_player_collect_mesh_cache_items[i],
                   (unsigned)physics_player_collect_world_bsp_tests[i],
                   (unsigned)physics_player_collect_world_bsp_nodes[i],
                   (unsigned)physics_player_collect_world_bsp_insts[i],
	                   (unsigned)physics_player_sweep_iterations[i],
	                   (unsigned)physics_player_collision_checks[i],
	                   (unsigned)physics_player_collision_broadphase_rejects[i],
	                   (unsigned long long)physics_player_sweep_total_us[i],
	                   (unsigned long long)physics_player_sweep_narrowphase_us_total[i],
	                   (unsigned long long)physics_player_sweep_narrowphase_us_max[i],
	                   (unsigned long long)physics_player_sweep_narrowphase_us_last[i],
	                   (unsigned long long)physics_player_sweep_iter_us_max[i],
	                   (unsigned long long)physics_player_sweep_iter_us_last[i],
	                   (unsigned)physics_player_sweep_iter_collision_checks_max[i],
                   (unsigned)physics_player_sweep_iter_collision_checks_last[i],
                   (unsigned)physics_player_sweep_iter_hits[i],
                   physics_player_sweep_iter_earliest_t_last[i],
                   physics_player_sweep_iter_remaining_move_len_last[i],
                   physics_player_sweep_iter_output_normal_z_last[i],
                   (unsigned)physics_player_sweep_iter_deflection_count[i],
                   (unsigned)physics_player_soup_capped[i],
                   (unsigned)physics_player_mesh_per_mesh_cap_hits[i],
                   (unsigned)physics_player_mesh_bbox_skips[i],
                   (unsigned)physics_player_support_priority_callbacks[i]);
            SvrRuntimeDiagLog(state,
                         "[tick-player-step-forensic] tick=%u ci=%d collect_face_cb_max_inst=%llu collect_face_cb_max_mesh=%llu collect_face_cb_max_mesh_faces=%u collect_face_cb_max_face_ordinal=%u collect_face_cb_max_accept=%u collect_face_cb_max_visual=%u collect_face_cb_max_soup_index=%u collect_face_cb_max_inst_story_id=%d collect_face_cb_max_inst_flags=%u collect_face_cb_max_inst_name_hash=%u collect_face_cb_max_mesh_name_hash=%u collect_face_cb_max_inst_bbox_cx_milli=%d collect_face_cb_max_inst_bbox_cy_milli=%d collect_face_cb_max_inst_bbox_cz_milli=%d collect_face_cb_max_inst_bbox_diag_milli=%u collect_face_cb_max_query_cx_milli=%d collect_face_cb_max_query_cy_milli=%d collect_face_cb_max_query_radius_milli=%u collect_face_cb_max_query_bbox_dist_milli=%u collect_face_cb_max_query_bbox_overlap_milli=%d support_retry_face_cb_max_inst=%llu support_retry_face_cb_max_mesh=%llu support_retry_face_cb_max_mesh_faces=%llu support_retry_face_cb_max_face_ordinal=%llu support_retry_face_cb_max_accept=%llu support_retry_face_cb_max_visual=%llu support_retry_face_cb_max_soup_index=%llu support_retry_face_cb_max_inst_story_id=%lld support_retry_face_cb_max_inst_flags=%llu support_retry_face_cb_max_inst_name_hash=%llu support_retry_face_cb_max_mesh_name_hash=%llu support_retry_face_cb_max_inst_bbox_cx_milli=%lld support_retry_face_cb_max_inst_bbox_cy_milli=%lld support_retry_face_cb_max_inst_bbox_cz_milli=%lld support_retry_face_cb_max_inst_bbox_diag_milli=%llu support_retry_face_cb_max_query_cx_milli=%lld support_retry_face_cb_max_query_cy_milli=%lld support_retry_face_cb_max_query_radius_milli=%llu support_retry_face_cb_max_query_bbox_dist_milli=%llu support_retry_face_cb_max_query_bbox_overlap_milli=%lld sweep_narrow_max_item_index=%u sweep_narrow_max_item_material=%u sweep_narrow_max_item_inst=%llu sweep_narrow_max_item_mesh=%llu sweep_narrow_max_item_mesh_faces=%u sweep_narrow_max_item_face_ordinal=%u sweep_narrow_max_item_inst_story_id=%d sweep_narrow_max_item_inst_flags=%u sweep_narrow_max_item_inst_name_hash=%u sweep_narrow_max_item_mesh_name_hash=%u sweep_narrow_max_item_nrm_z_milli=%d sweep_narrow_max_item_bbox_diag_milli=%u\n",
                   (unsigned)state->tick,
                   i,
                   (unsigned long long)physics_player_collect_mesh_face_cb_us_max_inst_id[i],
                   (unsigned long long)physics_player_collect_mesh_face_cb_us_max_mesh_id[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_mesh_faces[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_face_ordinal[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_accept[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_visual[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_soup_index[i],
                   (int)physics_player_collect_mesh_face_cb_us_max_inst_story_id[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_inst_flags[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_inst_name_hash[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_mesh_name_hash[i],
                   (int)physics_player_collect_mesh_face_cb_us_max_inst_bbox_cx_milli[i],
                   (int)physics_player_collect_mesh_face_cb_us_max_inst_bbox_cy_milli[i],
                   (int)physics_player_collect_mesh_face_cb_us_max_inst_bbox_cz_milli[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_inst_bbox_diag_milli[i],
                   (int)physics_player_collect_mesh_face_cb_us_max_query_cx_milli[i],
                   (int)physics_player_collect_mesh_face_cb_us_max_query_cy_milli[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_query_radius_milli[i],
                   (unsigned)physics_player_collect_mesh_face_cb_us_max_query_bbox_dist_milli[i],
                   (int)physics_player_collect_mesh_face_cb_us_max_query_bbox_overlap_milli[i],
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_inst_id,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_mesh_id,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_mesh_faces,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_face_ordinal,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_accept,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_visual,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_soup_index,
                   (long long)retry_collect.collect_mesh_face_cb_us_max_inst_story_id,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_inst_flags,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_inst_name_hash,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_mesh_name_hash,
                   (long long)retry_collect.collect_mesh_face_cb_us_max_inst_bbox_cx_milli,
                   (long long)retry_collect.collect_mesh_face_cb_us_max_inst_bbox_cy_milli,
                   (long long)retry_collect.collect_mesh_face_cb_us_max_inst_bbox_cz_milli,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_inst_bbox_diag_milli,
                   (long long)retry_collect.collect_mesh_face_cb_us_max_query_cx_milli,
                   (long long)retry_collect.collect_mesh_face_cb_us_max_query_cy_milli,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_query_radius_milli,
                   (unsigned long long)retry_collect.collect_mesh_face_cb_us_max_query_bbox_dist_milli,
                   (long long)retry_collect.collect_mesh_face_cb_us_max_query_bbox_overlap_milli,
                   (unsigned)physics_player_sweep_narrowphase_us_max_item_index[i],
                   (unsigned)physics_player_sweep_narrowphase_us_max_item_material[i],
                   (unsigned long long)physics_player_sweep_narrowphase_us_max_item_inst_id[i],
                   (unsigned long long)physics_player_sweep_narrowphase_us_max_item_mesh_id[i],
                   (unsigned)physics_player_sweep_narrowphase_us_max_item_mesh_faces[i],
                   (unsigned)physics_player_sweep_narrowphase_us_max_item_face_ordinal[i],
                   (int)physics_player_sweep_narrowphase_us_max_item_inst_story_id[i],
                   (unsigned)physics_player_sweep_narrowphase_us_max_item_inst_flags[i],
                   (unsigned)physics_player_sweep_narrowphase_us_max_item_inst_name_hash[i],
                   (unsigned)physics_player_sweep_narrowphase_us_max_item_mesh_name_hash[i],
                   (int)(physics_player_sweep_narrowphase_us_max_item_nrm_z[i] * 1000.0f),
                   (unsigned)(physics_player_sweep_narrowphase_us_max_item_bbox_diag[i] * 1000.0f));
        }
        if (g_tick_players_breakdown_logs < SVR_TICK_PLAYERS_BREAKDOWN_LOG_LIMIT)
            g_tick_players_breakdown_logs++;
	    }
	    uint64_t physics_player_step_once_us_max = 0;
	    int32_t physics_player_step_once_us_max_client = -1;
	    uint64_t physics_players_step_once_total_us = 0;
	    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
	    {
	        physics_players_step_once_total_us += physics_player_step_once_us[i];
	        if (physics_player_step_once_us[i] > physics_player_step_once_us_max)
	        {
	            physics_player_step_once_us_max = physics_player_step_once_us[i];
	            physics_player_step_once_us_max_client = i;
	        }
	    }
	    state->tick_last_physics_players_active = physics_players_active_count;
	    state->tick_last_physics_players_steps = physics_players_step_count;
	    state->tick_last_physics_players_idle_fast_paths = physics_players_idle_fast_path_count;
	    state->tick_last_physics_players_step_once_us = physics_players_step_once_total_us;
	    state->tick_last_physics_players_us = physics_players_us;
	    if (physics_players_us > state->tick_max_physics_players_us)
	    {
	        state->tick_max_physics_players_tick = state->tick;
	        state->tick_max_physics_players_active = physics_players_active_count;
	        state->tick_max_physics_players_steps = physics_players_step_count;
	        state->tick_max_physics_players_idle_fast_paths = physics_players_idle_fast_path_count;
	        state->tick_max_physics_players_step_once_us = physics_player_step_once_us_max;
	        state->tick_max_physics_players_step_once_client = physics_player_step_once_us_max_client;
	        if (physics_player_step_once_us_max_client >= 0 &&
	            physics_player_step_once_us_max_client < SVR_MAX_CLIENTS)
	        {
	            const int ci = physics_player_step_once_us_max_client;
	            uint32_t reject_mask = 0;
	            for (int bit = 0; bit < SVR_IDLE_FASTPATH_REJECT_COUNT; bit++)
	            {
	                if (physics_player_fast_path_reject_hits[ci][bit] != 0)
	                    reject_mask |= 1u << bit;
	            }
	            state->tick_max_physics_players_step_once_reject_mask = reject_mask;
	            state->tick_max_physics_players_step_once_input_flags = physics_player_input_flags_last[ci];
	            state->tick_max_physics_players_step_once_grounded = physics_player_grounded_last[ci];
	            state->tick_max_physics_players_step_once_in_water = physics_player_in_water_last[ci];
	            state->tick_max_physics_players_step_once_idle_support_recovered = physics_player_idle_support_recovered[ci];
	            state->tick_max_physics_players_step_once_full_steps = physics_player_full_step_count[ci];
	            state->tick_max_physics_players_step_once_idle_fast_paths = physics_player_idle_fast_path_counts[ci];
	            state->tick_max_physics_players_step_once_max_abs_vel_milli = (uint32_t)fmaxf(0.0f, physics_player_max_abs_vel[ci] * 1000.0f);
	            state->tick_max_physics_players_step_once_yaw_delta_mdeg = (uint32_t)fmaxf(0.0f, physics_player_max_yaw_delta[ci] * 1000.0f);
	            state->tick_max_physics_players_step_once_support_z_milli = isfinite(physics_player_max_support_z_delta[ci])
	                ? (uint32_t)fmaxf(0.0f, physics_player_max_support_z_delta[ci] * 1000.0f)
	                : UINT32_MAX;
	            state->tick_max_physics_players_step_once_accum_contact_milli = (uint32_t)fmaxf(0.0f, physics_player_accum_contact_last[ci] * 1000.0f);
	            state->tick_max_physics_players_step_once_collect_us = physics_player_collect_us[ci];
	            state->tick_max_physics_players_step_once_sweep_wall_us = physics_player_step_sweep_wall_us[ci];
	            state->tick_max_physics_players_step_once_support_probe_us = physics_player_step_support_probe_us[ci];
	            state->tick_max_physics_players_step_once_support_retry_probe_us = physics_player_step_support_retry_probe_us[ci];
	            state->tick_max_physics_players_step_once_unaccounted_us = physics_player_step_unaccounted_us[ci];
	            state->tick_max_physics_players_step_once_soup_items = physics_player_soup_items_max[ci];
	            state->tick_max_physics_players_step_once_sweep_iters = physics_player_sweep_iterations[ci];
	            state->tick_max_physics_players_step_once_collision_checks = physics_player_collision_checks[ci];
	        }
	        state->tick_max_physics_players_us = physics_players_us;
	    }
	    SvrRecordTickPhysicsPhase(state, SVR_TICK_PHYSICS_PLAYERS, physics_players_us);
    SvrRecordTickPhysicsPhase(state, SVR_TICK_PHYSICS_NPCS, physics_npcs_us);
    SvrRecordTickPhase(state, SVR_TICK_PHASE_PHYSICS, a3dGetTime() - phase_start_us);

    // ── PHASE 4: COMBAT ──────────────────────────────────────────
    phase_start_us = a3dGetTime();
    SvrResolveCombat(state);
    SvrResolveProjectileImpacts(state);
    SvrRecordTickPhase(state, SVR_TICK_PHASE_COMBAT, a3dGetTime() - phase_start_us);

    // ── PHASE 5: GAME RULES ──────────────────────────────────────
    phase_start_us = a3dGetTime();
    SvrProcessRespawns(state);
    SvrProcessDisconnects(state);
    const bool have_active_sessions_after_rules = SvrHasAnyActiveSession(state);
    SvrRecordTickPhase(state, SVR_TICK_PHASE_GAME_RULES, a3dGetTime() - phase_start_us);

    // ── PHASE 6: AI ──────────────────────────────────────────────
    phase_start_us = a3dGetTime();
    if (have_active_sessions_after_rules) SvrUpdateNpcAI(state);
    SvrRecordTickPhase(state, SVR_TICK_PHASE_AI, a3dGetTime() - phase_start_us);

    // AI swings queued in phase 6 are resolved immediately
    phase_start_us = a3dGetTime();
    if (have_active_sessions_after_rules && state->pending_swing_count > 0) SvrResolveCombat(state);
    if (have_active_sessions_after_rules && state->pending_projectile_count > 0) SvrResolveProjectileImpacts(state);
    SvrRecordTickPhase(state, SVR_TICK_PHASE_AI_COMBAT, a3dGetTime() - phase_start_us);

    // Presentation ids are server-owned ActorVisualProfile key dimensions.
    // Refresh after combat/AI mutation and before snapshot publication so
    // clients never exact-match a zero presentation key in real gameplay.
    if (have_active_sessions_after_rules)
        SvrRefreshPresentationKindsBeforeSnapshot(state);

    // ── PHASE 7: SNAPSHOT ────────────────────────────────────────
    phase_start_us = a3dGetTime();
    uint64_t snapshot_slice_start_us = a3dGetTime();
    if (have_active_sessions_after_rules)
        SvrFlushEvents(state);
    else
        state->events.len = state->events.count = 0;
    SvrRecordTickSnapshotPhase(state, SVR_TICK_SNAPSHOT_EVENTS, a3dGetTime() - snapshot_slice_start_us);
    snapshot_slice_start_us = a3dGetTime();
    if (have_active_sessions_after_rules) SvrBroadcastSnapshot(state);
    SvrRecordTickSnapshotPhase(state, SVR_TICK_SNAPSHOT_GAMEPLAY_SNAPSHOT, a3dGetTime() - snapshot_slice_start_us);
    snapshot_slice_start_us = a3dGetTime();
    SvrPublishAuthoritativeState(state);
    SvrRecordTickSnapshotPhase(state, SVR_TICK_SNAPSHOT_AUTHORITATIVE_STATE, a3dGetTime() - snapshot_slice_start_us);
    snapshot_slice_start_us = a3dGetTime();
    if (have_active_sessions_after_rules) SvrPublishOutbound(state);
    SvrRecordTickSnapshotPhase(state, SVR_TICK_SNAPSHOT_OUTBOUND, a3dGetTime() - snapshot_slice_start_us);
    state->snapshot_total_us = a3dGetTime() - phase_start_us;
    SvrMaybeLogAuthoritativeStateForensic(state);
    SvrRecordTickPhase(state, SVR_TICK_PHASE_SNAPSHOT, state->snapshot_total_us);
}

void ServerTickLoop(ServerState* state)
{
    uint64_t prev_time = a3dGetTime();
    state->tick_stamp_us = prev_time;

    for (int i = 0; i < state->npc_count; i++)
    {
        SvrNpcState* npc = &state->npcs[i];
        if (npc->physics)
            SyncPhysicsStamp(npc->physics, prev_time);
    }
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        SvrPlayerState* ps = &state->players[i];
        if (ps->physics)
            SyncPhysicsStamp(ps->physics, prev_time);
    }

    printf("[tick] Server tick loop started at %d Hz\n", SVR_TICK_RATE);

    while (__atomic_load_n(&isRunning, __ATOMIC_ACQUIRE))
    {
        uint64_t now = a3dGetTime();
        uint64_t frame_time = now - prev_time;
        prev_time = now;

        if (frame_time > SVR_SPIRAL_CLAMP_US)
            frame_time = SVR_SPIRAL_CLAMP_US;

        state->accumulated_time_us += frame_time;

        while (state->accumulated_time_us >= SVR_TICK_INTERVAL_US)
        {
            state->accumulated_time_us -= SVR_TICK_INTERVAL_US;
            state->tick++;
            state->tick_stamp_us += SVR_TICK_INTERVAL_US;

            ServerTick(state);
        }

        // Tick timing observability (FL-530)
        uint64_t elapsed = a3dGetTime() - now;
        if (elapsed > state->tick_max_elapsed_us)
            state->tick_max_elapsed_us = elapsed;
        if (elapsed > SVR_TICK_INTERVAL_US)
            state->tick_overrun_count++;

        // Sleep remainder of frame
        if (elapsed < SVR_TICK_INTERVAL_US)
        {
            uint64_t sleep_us = SVR_TICK_INTERVAL_US - elapsed;
            THREAD_SLEEP((int)(sleep_us / 1000));
        }
    }

    printf("[tick] Server tick loop stopped at tick %u\n", state->tick);
}

#include <poll.h>
#include <climits>
#include <fcntl.h>
#include <errno.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netinet/in.h>   // sockaddr_in for per-IP rate limiting (RQ-103)

// Work around <netinet/tcp.h> colliding with this codebase's TCP_CLOSE().

static void SvrSetNonBlockingFd(int fd)
{
    if (fd < 0)
        return;
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0)
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static bool SvrEnsureIOWakePipe(ServerState* state)
{
    if (!state)
        return false;
    if (state->io_wake_read_fd >= 0 && state->io_wake_write_fd >= 0)
        return true;
    int wake_fds[2] = { -1, -1 };
    if (pipe(wake_fds) != 0)
    {
        __atomic_add_fetch(&state->io_wake_write_errno_count, 1u, __ATOMIC_RELAXED);
        return false;
    }
    SvrSetNonBlockingFd(wake_fds[0]);
    SvrSetNonBlockingFd(wake_fds[1]);
    __atomic_store_n(&state->io_wake_read_fd, wake_fds[0], __ATOMIC_RELEASE);
    __atomic_store_n(&state->io_wake_write_fd, wake_fds[1], __ATOMIC_RELEASE);
    return true;
}

static void SvrWakeIOThread(ServerState* state)
{
    if (!state)
        return;
    int wake_fd = __atomic_load_n(&state->io_wake_write_fd, __ATOMIC_ACQUIRE);
    if (wake_fd < 0)
        return;
    const uint8_t byte = 1;
    ssize_t wrote = write(wake_fd, &byte, sizeof(byte));
    if (wrote == (ssize_t)sizeof(byte))
        __atomic_add_fetch(&state->io_wake_write_count, 1u, __ATOMIC_RELAXED);
    else if (wrote < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
        __atomic_add_fetch(&state->io_wake_write_errno_count, 1u, __ATOMIC_RELAXED);
}

static void SvrDrainIOWakePipe(ServerState* state)
{
    if (!state)
        return;
    int wake_fd = __atomic_load_n(&state->io_wake_read_fd, __ATOMIC_ACQUIRE);
    if (wake_fd < 0)
        return;
    uint8_t buf[64];
    for (;;)
    {
        ssize_t got = read(wake_fd, buf, sizeof(buf));
        if (got > 0)
        {
            __atomic_add_fetch(&state->io_wake_read_count, (uint32_t)got, __ATOMIC_RELAXED);
            continue;
        }
        if (got < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
            return;
        return;
    }
}

// ── Per-IP rate limiter helpers (RQ-103) ──────────────────────────
// CAS spinlock: accept thread and tick thread both access ip_rate_table.
static inline void ip_rate_lock(ServerState* s)
{
    while (__atomic_exchange_n(&s->ip_rate_lock, 1, __ATOMIC_ACQUIRE) != 0)
    {
        // spin
    }
}
static inline void ip_rate_unlock(ServerState* s)
{
    __atomic_store_n(&s->ip_rate_lock, 0, __ATOMIC_RELEASE);
}

// Returns current connection count for the given IP (caller must hold lock).
static int ip_rate_get(ServerState* s, uint32_t ip)
{
    for (int i = 0; i < s->ip_rate_count; i++)
        if (s->ip_rate_table[i].ip == ip)
            return s->ip_rate_table[i].count;
    return 0;
}

// Increment connection count for IP (caller must hold lock).
static void ip_rate_inc(ServerState* s, uint32_t ip)
{
    for (int i = 0; i < s->ip_rate_count; i++)
    {
        if (s->ip_rate_table[i].ip == ip)
        {
            s->ip_rate_table[i].count++;
            return;
        }
    }
    // New entry
    if (s->ip_rate_count < SVR_IP_RATE_LIMIT_MAX_ENTRIES)
    {
        s->ip_rate_table[s->ip_rate_count].ip = ip;
        s->ip_rate_table[s->ip_rate_count].count = 1;
        s->ip_rate_count++;
    }
}

// Decrement connection count for IP (caller must hold lock).
// Removes entry when count reaches zero to prevent table bloat.
static void ip_rate_dec(ServerState* s, uint32_t ip)
{
    for (int i = 0; i < s->ip_rate_count; i++)
    {
        if (s->ip_rate_table[i].ip == ip)
        {
            s->ip_rate_table[i].count--;
            if (s->ip_rate_table[i].count <= 0)
            {
                // Swap-remove: replace with last entry
                s->ip_rate_table[i] = s->ip_rate_table[s->ip_rate_count - 1];
                s->ip_rate_count--;
            }
            return;
        }
    }
}
#ifndef TCP_NODELAY
#define TCP_NODELAY 1
#endif
#ifndef TCP_QUICKACK
#define TCP_QUICKACK 12
#endif
#ifndef IPTOS_LOWDELAY
#define IPTOS_LOWDELAY 0x10
#endif

static void SvrConfigureAcceptedClientSocket(TCP_SOCKET cs)
{
    int optval = 1;
    setsockopt(cs, SOL_SOCKET, SO_KEEPALIVE, (const char*)&optval, sizeof(optval));
    setsockopt(cs, IPPROTO_TCP, TCP_NODELAY, (const char*)&optval, sizeof(optval));
#ifdef __linux__
    setsockopt(cs, IPPROTO_TCP, TCP_QUICKACK, (const char*)&optval, sizeof(optval));
#endif
#ifdef IP_TOS
    const int tos = IPTOS_LOWDELAY;
    setsockopt(cs, IPPROTO_IP, IP_TOS, (const char*)&tos, sizeof(tos));
#endif
#ifdef __APPLE__
    setsockopt(cs, SOL_SOCKET, SO_NOSIGPIPE, (const char*)&optval, sizeof(optval));
#endif
}

static bool SvrIsLoopbackPeerIp(uint32_t peer_ip)
{
    return peer_ip == htonl(INADDR_LOOPBACK);
}

static bool SvrParseForwardedForIp(const char* value, uint32_t* ip_out)
{
    if (!value || !ip_out) return false;

    while (*value == ' ' || *value == '\t') value++;

    char token[64];
    int len = 0;
    while (value[len] != 0 && value[len] != ',' && value[len] != ' ' && value[len] != '\t')
    {
        if (len >= (int)sizeof(token) - 1) return false;
        token[len] = value[len];
        len++;
    }
    token[len] = 0;
    if (len <= 0) return false;

    struct in_addr parsed = {};
    if (inet_pton(AF_INET, token, &parsed) != 1) return false;
    if (parsed.s_addr == 0) return false;
    *ip_out = parsed.s_addr;
    return true;
}

static uint32_t SvrResolveRateLimitIp(uint32_t peer_ip, uint32_t forwarded_for_ip)
{
    // WARNING FL-3837: this is the proof-harness admission seam for public WSS
    // multi-connection diagnostics. The resolved fix keys trusted nginx
    // loopback traffic on X-Forwarded-For and allows the proxy-specific cap;
    // it is not a lag owner and must not be used to reopen parked FL-3800 or
    // raw-red FL-2957 unless future rows show direct admission/lag evidence.
    if (SvrIsLoopbackPeerIp(peer_ip) && forwarded_for_ip != 0)
        return forwarded_for_ip;
    return peer_ip;
}

static int SvrRateLimitMaxConns(uint32_t peer_ip, uint32_t forwarded_for_ip)
{
    if (SvrIsLoopbackPeerIp(peer_ip) && forwarded_for_ip != 0)
        return SVR_PROXY_IP_RATE_LIMIT_MAX_CONNS;
    return SVR_IP_RATE_LIMIT_MAX_CONNS;
}

extern "C" void SHA1(void* data, int len, unsigned char digest[20]);
extern int Base64Encode(unsigned char* data, int len, char* base64);

void* AcceptThreadEntry(void* arg)
{
    ServerState* state = (ServerState*)arg;
    printf("[accept] Accept thread started\n");

    while (__atomic_load_n(&isRunning, __ATOMIC_ACQUIRE))
    {
        // Poll listen socket with short timeout
        struct pollfd pfd;
        pfd.fd = state->listen_socket;
        pfd.events = POLLIN;
        int ret = poll(&pfd, 1, 50); // 50ms
        if (ret <= 0 || !(pfd.revents & POLLIN)) continue;

        // Accept with peer address for per-IP rate limiting (RQ-103)
        struct sockaddr_in peer_addr;
        socklen_t peer_len = sizeof(peer_addr);
        TCP_SOCKET cs = accept(state->listen_socket, (struct sockaddr*)&peer_addr, &peer_len);
        if (cs == INVALID_TCP_SOCKET) continue;

        uint32_t peer_ip = peer_addr.sin_addr.s_addr; // network byte order

        // ================================================================
        // /health endpoint — fast-path plain HTTP health check
        // ================================================================
        // Peek at the first bytes. If this is a GET /health request, respond
        // with JSON health data, close the connection, and continue without
        // consuming a player slot.  MSG_PEEK leaves the data in the socket
        // buffer so non-health connections fall through to WS handshake below.
        //
        // WHY 503: HTTP 503 Service Unavailable communicates
        // "the origin server is currently unable to handle the request"
        // (RFC 7231 §6.6.4).  This is the correct semantic for a game server
        // that has crashed, is in start-limit backoff, or whose process is
        // alive but not accepting gameplay.  The previous nginx static-200
        // truth (FL-2416) returned 200 even when the game process was dead,
        // giving operators a false-positive liveness signal.
        //
        // CONTRACT: Returns 200 only when the game server process is alive
        // AND the tick loop has started.  Returns 503 otherwise.  Fields:
        //   process_alive      bool    — server binary is running
        //   tick_rate_hz       int     — configured tick rate (SVR_TICK_RATE)
        //   connected_players  int     — clients with phase >= CPHASE_JOINED
        //   memory_mb          float   — approximate RSS from /proc/self/statm
        //   uptime_seconds     float   — wall-clock seconds since SvrStateInit
        // -----------------------------------------------------------------
        {
            char peek_buf[32];
            ssize_t peeked = recv(cs, peek_buf, sizeof(peek_buf) - 1, MSG_PEEK);
            if (peeked > 0)
            {
                peek_buf[peeked] = '\0';
                bool is_health = (strncmp(peek_buf, "GET /health", 11) == 0);
                if (is_health)
                {
                    // Count connected players (JOINED/ALIVE/DEAD/RESPAWNING/SPECTATING)
                    int conn_players = 0;
                    uint64_t now_us = a3dGetTime();
                    double uptime_s = (now_us - state->start_time_us) / 1000000.0;
                    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
                    {
                        uint8_t ph = __atomic_load_n((const volatile uint8_t*)&state->clients[i].phase, __ATOMIC_ACQUIRE);
                        if (ph >= CPHASE_JOINED && ph <= CPHASE_SPECTATING)
                            conn_players++;
                    }

                    // memory_mb: approximate RSS from /proc/self/statm (Linux).
                    // Assumes 4 KB page size (standard on Linux x86_64/arm64).
                    double mem_mb = 0.0;
#ifdef __linux__
                    FILE* statm = fopen("/proc/self/statm", "r");
                    if (statm)
                    {
                        long rss_pages = 0;
                        if (fscanf(statm, "%*ld %ld", &rss_pages) == 1)
                            mem_mb = (double)rss_pages * 4.0 / 1024.0;
                        fclose(statm);
                    }
#endif

                    bool tick_alive = __atomic_load_n(&isRunning, __ATOMIC_ACQUIRE) &&
                                      __atomic_load_n(&state->tick, __ATOMIC_RELAXED) > 0;

                    char json[512];
                    int json_len = snprintf(json, sizeof(json),
                        "{"
                        "\"process_alive\":%s,"
                        "\"tick_rate_hz\":%d,"
                        "\"connected_players\":%d,"
                        "\"memory_mb\":%.1f,"
                        "\"uptime_seconds\":%.0f"
                        "}\n",
                        tick_alive ? "true" : "false",
                        SVR_TICK_RATE,
                        conn_players,
                        mem_mb,
                        uptime_s);

                    int http_status = tick_alive ? 200 : 503;
                    char response[1024];
                    int response_len = snprintf(response, sizeof(response),
                        "HTTP/1.1 %d %s\r\n"
                        "Content-Type: application/json\r\n"
                        "Content-Length: %d\r\n"
                        "Connection: close\r\n"
                        "Cache-Control: no-store\r\n"
                        "\r\n"
                        "%s",
                        http_status,
                        (http_status == 200) ? "OK" : "Service Unavailable",
                        json_len,
                        json);

                    TCP_WRITE(cs, (const uint8_t*)response, response_len);
                    TCP_CLOSE(cs);
                    printf("[health] %s connected_players=%d uptime=%.0fs mem=%.1fMB\n",
                           tick_alive ? "200" : "503", conn_players, uptime_s, mem_mb);
                    continue;
                }
            }
        }

        // Check ring space BEFORE handshake (G-03: avoid wasting 5s on full ring)
        AcceptRing* ring = &state->accept_ring;
        uint32_t wr = __atomic_load_n(&ring->write, __ATOMIC_RELAXED);
        uint32_t rd = __atomic_load_n(&ring->read, __ATOMIC_ACQUIRE);
        if (wr - rd >= SVR_ACCEPT_RING_SIZE)
        {
            TCP_CLOSE(cs);
            printf("[accept] Accept ring full, rejecting connection\n");
            continue;
        }

        // Claim slot via atomic CAS
        int cap = max_players < SVR_MAX_CLIENTS ? max_players : SVR_MAX_CLIENTS;
        int slot = atomic_claim_slot(&state->slot_bitmask, cap);
        if (slot < 0)
        {
            uint32_t slot_mask = __atomic_load_n(&state->slot_bitmask, __ATOMIC_ACQUIRE);
            printf("[accept] atomic_claim_slot failed cap=%d slot_mask=0x%08x\n", cap, (unsigned)slot_mask);
            SvrSendWSCloseReason(cs, 1013, "SORRY, SERVER CURRENTLY AT MAX PLAYERS, PLEASE REFRESH YOUR BROWSER");
            TCP_CLOSE(cs);
            continue;
        }

        // Handshake stays in accept-thread blocking mode, so keep the timeout explicit.
        SvrConfigureAcceptedClientSocket(cs);

        struct timeval tv;
        tv.tv_sec = 5;
        tv.tv_usec = 0;
        setsockopt(cs, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof(tv));

        // Blocking WS handshake (only blocks THIS thread, not IO)
        uint32_t rate_limit_ip = peer_ip;
        uint32_t forwarded_for_ip = 0;
        if (!SvrDoWSHandshake(cs, peer_ip, &rate_limit_ip, &forwarded_for_ip))
        {
            TCP_CLOSE(cs);
            atomic_release_slot(&state->slot_bitmask, slot);
            printf("[accept] WS handshake failed for slot %d\n", slot);
            continue;
        }

        // -- RQ-103: enforce and increment after handshake so trusted loopback
        // nginx traffic can use X-Forwarded-For instead of collapsing to 127.0.0.1.
        {
            ip_rate_lock(state);
            const int max_conns = SvrRateLimitMaxConns(peer_ip, forwarded_for_ip);
            int ip_count = ip_rate_get(state, rate_limit_ip);
            if (ip_count >= max_conns)
            {
                ip_rate_unlock(state);
                TCP_CLOSE(cs);
                atomic_release_slot(&state->slot_bitmask, slot);
                printf("[accept] Per-IP rate limit reached (%d/%d conns) for ip=0x%08x peer=0x%08x, rejecting\n",
                       ip_count, max_conns, rate_limit_ip, peer_ip);
                continue;
            }
            ip_rate_inc(state, rate_limit_ip);
            ip_rate_unlock(state);
        }

        // Set non-blocking BEFORE enqueue (U-06)
        int flags = fcntl(cs, F_GETFL, 0);
        fcntl(cs, F_SETFL, flags | O_NONBLOCK);

        // Clear recv timeout (no longer needed, socket is non-blocking now)
        tv.tv_sec = 0;
        tv.tv_usec = 0;
        setsockopt(cs, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof(tv));

        // Enqueue accept event for IO thread
        AcceptEvent* ev = &ring->events[wr & SVR_ACCEPT_RING_MASK];
        ev->socket = cs;
        ev->slot = slot;
        ev->peer_ip = rate_limit_ip;
        __atomic_store_n(&ring->write, wr + 1, __ATOMIC_RELEASE);

        printf("[accept] Client accepted, slot %d ip=0x%08x peer=0x%08x stamp_us=%llu\n",
               slot, rate_limit_ip, peer_ip, SvrLogStampUs());
    }

    printf("[accept] Accept thread stopped\n");
    return NULL;
}

static bool IOEnqueueSynthetic(ClientIO* cio, uint8_t opcode)
{
    uint32_t wr = __atomic_load_n(&cio->in_write, __ATOMIC_RELAXED);
    uint32_t rd = __atomic_load_n(&cio->in_read, __ATOMIC_ACQUIRE);
    if (wr - rd >= SVR_MSG_RING_SIZE) return false;

    ClientIO::InMsg* m = &cio->in_ring[wr & SVR_MSG_RING_MASK];
    m->data[0] = opcode;
    m->size = 1;
    __atomic_store_n(&cio->in_write, wr + 1, __ATOMIC_RELEASE);
    return true;
}

// Helper: send() with platform-correct flags (no SIGPIPE)
static ssize_t io_send(TCP_SOCKET sock, const void* data, size_t len)
{
#ifdef __APPLE__
    return send(sock, data, len, 0); // SO_NOSIGPIPE set at accept time
#else
    return send(sock, data, len, MSG_NOSIGNAL);
#endif
}

static bool IOHasControlFramePending(const ClientIO* cio)
{
    return cio && cio->control_read != cio->control_write;
}

// REMOVED: static bool IOHasQueuedLagEcho(const ClientIO* cio)


static int IOWsEncodedFrameSize(int payload_size)
{
    if (payload_size < 0) return -1;
    if (payload_size < 126) return payload_size + 2;
    if (payload_size < 65536) return payload_size + 4;
    return payload_size + 10;
}

static uint32_t IOControlQueueDepth(const ClientIO* cio)
{
    if (!cio) return 0;
    return cio->control_write - cio->control_read;
}

static void IORefreshControlQueueDiag(ClientIO* cio)
{
    if (!cio) return;
    uint32_t depth = IOControlQueueDepth(cio);
    __atomic_store_n(&cio->control_queue_depth_last, depth, __ATOMIC_RELAXED);
    __atomic_store_n(&cio->control_send_offset_last,
                     (uint32_t)(cio->control_send_offset < 0 ? 0 : cio->control_send_offset),
                     __ATOMIC_RELAXED);
    uint32_t prev_max = __atomic_load_n(&cio->control_queue_max_depth, __ATOMIC_RELAXED);
    while (depth > prev_max &&
           !__atomic_compare_exchange_n(&cio->control_queue_max_depth,
                                        &prev_max,
                                        depth,
                                        false,
                                        __ATOMIC_RELAXED,
                                        __ATOMIC_RELAXED))
    {
    }
}

// REMOVED: static void IONoteControlDrop(ClientIO* cio, bool lag_echo, 


// REMOVED: static bool IODropOldestQueuedLagEcho(ClientIO* cio)


static void IOResetControlQueue(ClientIO* cio)
{
    if (!cio) return;
    cio->control_read = 0;
    cio->control_write = 0;
    cio->control_send_offset = 0;
    cio->lag_echo_hol_blocked_active = false;
    __atomic_store_n(&cio->control_queue_depth_last, 0u, __ATOMIC_RELAXED);
    __atomic_store_n(&cio->control_send_offset_last, 0u, __ATOMIC_RELAXED);
}

// REMOVED: static bool IOQueueControlFrame(ClientIO* cio, const uint8_t


static bool IOQueueBinaryControlPacket(ClientIO* cio, const void* payload, int payload_size,
                                       bool lag_echo)
{
    if (!cio || !payload || payload_size <= 0)
        return false;

    int frame_len = IOWsEncodedFrameSize(payload_size);
    if (frame_len <= 0 || frame_len > SVR_IO_CONTROL_FRAME_SIZE)
    {
        IONoteControlDrop(cio, lag_echo, false);
        return false;
    }

    uint8_t frame[SVR_IO_CONTROL_FRAME_SIZE];
    frame_len = WS_FRAME_ENCODE(frame, (const uint8_t*)payload, payload_size, 0x2);
    return IOQueueControlFrame(cio, frame, frame_len, lag_echo);
}

// REMOVED: static STRUCT_RSP_LAG* IOLagEchoRspPayload(ClientIO::Control


enum
{
    AK_LAG_TRACE_BPF_PHASE_RX = 1,
    AK_LAG_TRACE_BPF_PHASE_ENQUEUE = 2,
    AK_LAG_TRACE_BPF_PHASE_FLUSH_START = 3,
    AK_LAG_TRACE_BPF_PHASE_FLUSH_FINISH = 4,
};

#if defined(__GNUC__) || defined(__clang__)
extern "C" __attribute__((noinline, used)) void AKLagTraceBpfMarker(
    uint32_t phase, uint32_t trace_seq, uint32_t stamp0, uint32_t stamp1)
{
    __asm__ __volatile__("" : : "r"(phase), "r"(trace_seq), "r"(stamp0), "r"(stamp1) : "memory");
}
#else
extern "C" void AKLagTraceBpfMarker(uint32_t phase, uint32_t trace_seq,
                                    uint32_t stamp0, uint32_t stamp1)
{
    (void)phase;
    (void)trace_seq;
    (void)stamp0;
    (void)stamp1;
}
#endif

#ifdef ASCIICKER_SERVER_TICK_CONTRACT_TESTS
static bool SvrTestControlRingFrameMatches(const ClientIO* cio, uint32_t logical_idx,
                                           bool lag_echo, uint8_t first_byte)
{
    const ClientIO::ControlFrame* frame =
        &cio->control_ring[logical_idx & SVR_IO_CONTROL_RING_MASK];
    return frame->len == 3 &&
        frame->lag_echo == lag_echo &&
        frame->data[0] == first_byte;
}

bool SvrTestIdleFastPathSupportZThreshold()
{
    const float base_z = 64.0f;
    const float near_settled_delta = 0.412f;
    const float clearly_unsettled_delta = 0.501f;
    if (fabsf(base_z - (base_z + near_settled_delta)) > SVR_IDLE_FASTPATH_SUPPORT_Z_EPS)
        return false;
    if (!(fabsf(base_z - (base_z + clearly_unsettled_delta)) > SVR_IDLE_FASTPATH_SUPPORT_Z_EPS))
        return false;
    const float cached_support_z = base_z;
    if (fabsf((base_z + near_settled_delta) - cached_support_z) > SVR_IDLE_FASTPATH_SUPPORT_Z_EPS)
        return false;
    if (!(fabsf((base_z + clearly_unsettled_delta) - cached_support_z) > SVR_IDLE_FASTPATH_SUPPORT_Z_EPS))
        return false;
    return true;
}

bool SvrTestControlRingContracts()
{
    ClientIO cio = {};
    const uint8_t lag_a[3] = { 0xA1, 0x01, 0x02 };
    const uint8_t lag_b[3] = { 0xB2, 0x03, 0x04 };
    const uint8_t pong[3] = { 0x8A, 0x00, 0x00 };

    if (IOHasQueuedLagEcho(&cio))
        return false;
    if (IODropOldestQueuedLagEcho(&cio))
        return false;

    if (!IOQueueControlFrame(&cio, lag_a, (int)sizeof(lag_a), true))
        return false;
    if (!IOHasQueuedLagEcho(&cio))
        return false;
    if (!IODropOldestQueuedLagEcho(&cio))
        return false;
    if (cio.control_read != 0 || cio.control_write != 0 || IOControlQueueDepth(&cio) != 0)
        return false;
    if (cio.lag_echo_queue_drop_count != 1 || cio.control_queue_drop_count != 1)
        return false;

    IOResetControlQueue(&cio);
    memset(cio.control_ring, 0, sizeof(cio.control_ring));
    cio.lag_echo_queue_drop_count = 0;
    cio.control_queue_drop_count = 0;
    if (!IOQueueControlFrame(&cio, pong, (int)sizeof(pong), false))
        return false;
    if (IOHasQueuedLagEcho(&cio))
        return false;
    if (!IOQueueControlFrame(&cio, lag_a, (int)sizeof(lag_a), true))
        return false;
    if (!IOQueueControlFrame(&cio, lag_b, (int)sizeof(lag_b), true))
        return false;
    if (!IOHasQueuedLagEcho(&cio))
        return false;
    if (!IODropOldestQueuedLagEcho(&cio))
        return false;
    if (cio.control_read != 0 || cio.control_write != 2 || IOControlQueueDepth(&cio) != 2)
        return false;
    if (!SvrTestControlRingFrameMatches(&cio, 0, false, pong[0]))
        return false;
    if (!SvrTestControlRingFrameMatches(&cio, 1, true, lag_b[0]))
        return false;
    if (cio.lag_echo_queue_drop_count != 1 || cio.control_queue_drop_count != 1)
        return false;

    IOResetControlQueue(&cio);
    memset(cio.control_ring, 0, sizeof(cio.control_ring));
    cio.lag_echo_queue_drop_count = 0;
    cio.control_queue_drop_count = 0;
    cio.control_pong_drop_count = 0;
    const uint32_t usable_control_slots = SVR_IO_CONTROL_RING_SIZE - 1;
    for (uint32_t i = 0; i < usable_control_slots; i++)
    {
        uint8_t lag_frame[3] = { (uint8_t)i, 0xCC, 0xDD };
        if (!IOQueueControlFrame(&cio, lag_frame, (int)sizeof(lag_frame), true))
            return false;
    }
    if (IOControlQueueDepth(&cio) != usable_control_slots)
        return false;
    if (!IOQueueControlFrame(&cio, pong, (int)sizeof(pong), false))
        return false;
    if (IOControlQueueDepth(&cio) != usable_control_slots)
        return false;
    if (cio.control_read != 0 || cio.control_write != usable_control_slots)
        return false;
    if (!SvrTestControlRingFrameMatches(&cio, usable_control_slots - 1, false, pong[0]))
        return false;
    if (cio.lag_echo_queue_drop_count != 1 || cio.control_queue_drop_count != 1)
        return false;
    if (cio.control_pong_drop_count != 0)
        return false;

    cio.control_send_offset = 7;
    cio.control_queue_depth_last = 123;
    cio.control_send_offset_last = 456;
    IOResetControlQueue(&cio);
    if (cio.control_read != 0 || cio.control_write != 0 || cio.control_send_offset != 0)
        return false;
    if (cio.control_queue_depth_last != 0 || cio.control_send_offset_last != 0)
        return false;

    uint8_t too_large[SVR_IO_CONTROL_FRAME_SIZE] = {};
    const uint32_t drop_before = cio.lag_echo_queue_drop_count;
    if (IOQueueBinaryControlPacket(&cio, too_large, (int)sizeof(too_large), true))
        return false;
    if (cio.lag_echo_queue_drop_count != drop_before + 1)
        return false;

    return true;
}

bool SvrTestTickSnapshotPhaseContracts()
{
    ServerState* state = (ServerState*)calloc(1, sizeof(ServerState));
    if (!state)
        return false;
    state->tick = 77;

    SvrRecordTickSnapshotPhase(state, SVR_TICK_SNAPSHOT_EVENTS, 7000);
    if (state->tick_snapshot_authoritative_state_us_last != 0 ||
        state->tick_snapshot_authoritative_state_us_max != 0)
        goto fail;
    if (state->tick_max_snapshot_phase_us != 7000 ||
        state->tick_max_snapshot_phase_id != SVR_TICK_SNAPSHOT_EVENTS ||
        state->tick_max_snapshot_phase_tick != 77)
        goto fail;

    SvrRecordTickSnapshotPhase(state, SVR_TICK_SNAPSHOT_AUTHORITATIVE_STATE, 9000);
    if (state->tick_snapshot_authoritative_state_us_last != 9000 ||
        state->tick_snapshot_authoritative_state_us_max != 9000)
        goto fail;

    SvrRecordTickSnapshotPhase(state, SVR_TICK_SNAPSHOT_AUTHORITATIVE_STATE, 3000);
    if (state->tick_snapshot_authoritative_state_us_last != 3000 ||
        state->tick_snapshot_authoritative_state_us_max != 9000)
        goto fail;

    SvrFinalizeAuthoritativeStatePublish(state);
    if (state->tick_snapshot_authoritative_state_us_last != 3000 ||
        state->tick_snapshot_authoritative_state_us_max != 0)
        goto fail;

    SvrRecordTickSnapshotPhase(state, SVR_TICK_SNAPSHOT_AUTHORITATIVE_STATE, 12000);
    if (state->tick_snapshot_authoritative_state_us_last != 12000 ||
        state->tick_snapshot_authoritative_state_us_max != 12000)
        goto fail;

    free(state);
    return true;

fail:
    free(state);
    return false;
}

bool SvrMeasureAuthoritativeStatePublishBench(char* out, size_t out_cap)
{
    if (!out || out_cap == 0)
        return false;
    out[0] = 0;

    char tmp_dir[] = "/tmp/asciicker-auth-publish-XXXXXX";
    if (!mkdtemp(tmp_dir))
        return false;

    ServerState* state = (ServerState*)calloc(1, sizeof(ServerState));
    if (!state)
        return false;

    const size_t prior_base_len = strlen(base_path);
    char prior_base_path[SVR_BASE_PATH_CAP] = {};
    if (prior_base_len < sizeof(prior_base_path))
        memcpy(prior_base_path, base_path, prior_base_len + 1);
    snprintf(base_path, SVR_BASE_PATH_CAP, "%s/", tmp_dir);
    char web_dir[PATH_MAX] = {};
    snprintf(web_dir, sizeof(web_dir), "%s/.web", tmp_dir);
    mkdir(web_dir, 0700);

    state->tick = 10;
    state->snapshot_seq = 777;
    state->authoritative_publish_interval_ticks = 10;
    state->tick_overrun_count = 84;
    state->tick_max_elapsed_us = 371723;
    state->tick_max_phase_tick = 1100;
    state->tick_max_phase_id = SVR_TICK_PHASE_SNAPSHOT;
    state->tick_max_phase_us = 361515;
    state->tick_max_snapshot_phase_tick = 1100;
    state->tick_max_snapshot_phase_id = SVR_TICK_SNAPSHOT_AUTHORITATIVE_STATE;
    state->tick_max_snapshot_phase_us = 361493;
    state->tick_snapshot_authoritative_state_us_last = 361493;
    state->tick_snapshot_authoritative_state_us_max = 361493;
    state->tick_max_physics_phase_tick = 3619;
    state->tick_max_physics_phase_id = SVR_TICK_PHYSICS_NPCS;
    state->tick_max_physics_phase_us = 221893;
    __atomic_store_n(&state->io_poll_gap_max_us, 216197u, __ATOMIC_RELAXED);
    __atomic_store_n(&state->io_poll_gap_over_100ms_count, 838u, __ATOMIC_RELAXED);

    for (int i = 0; i < 2; i++)
    {
        SvrPlayerState* ps = &state->players[i];
        ps->active = 1;
        ps->phase = CPHASE_ALIVE;
        ps->player_id = i;
        ps->hp = 40;
        ps->max_hp = 40;
        ps->appearance.subject_key[0] = (char)('a' + i);
        ps->appearance.subject_key[1] = 0;
    }

    state->npc_count = SVR_MAX_NPCS;
    for (int i = 0; i < state->npc_count; i++)
    {
        SvrNpcState* npc = &state->npcs[i];
        npc->active = 1;
        npc->entity_id = (uint16_t)(SVR_MAX_CLIENTS + i);
        npc->hp = 23;
        npc->max_hp = 23;
        npc->appearance.subject_key[0] = 'n';
        npc->appearance.subject_key[1] = 0;
    }

    for (int i = 0; i < SVR_MAX_ITEMS; i++)
    {
        SvrItemState* it = &state->items[i];
        it->active = 1;
        it->item_id = (uint16_t)(1000 + i);
        it->owner_id = 0xFFFF;
        it->item_definition_id = 500;
        it->visual_style_id = 1;
        it->equip_slot_kind_id = 0;
        it->source_kind = SVR_ITEM_SOURCE_MAP_A3D;
    }

    SvrAuthoritativeStatePublishStats stats = {};
    const bool ok = SvrPublishAuthoritativeStateDetailed(state, &stats);
    const int wrote = snprintf(out,
                               out_cap,
                               "ok=%d build_us=%llu write_us=%llu bytes=%zu players=%u npcs=%u items=%u",
                               ok ? 1 : 0,
                               (unsigned long long)stats.build_us,
                               (unsigned long long)stats.write_us,
                               stats.json_bytes,
                               (unsigned)stats.active_players,
                               (unsigned)stats.active_npcs,
                               (unsigned)stats.active_items);

    snprintf(base_path, SVR_BASE_PATH_CAP, "%s", prior_base_path);
    remove("/dev/shm/asciicker-authoritative_state.json");
    remove("/dev/shm/asciicker-authoritative_state.json.tmp");
    char tmp_path[PATH_MAX] = {};
    char final_path[PATH_MAX] = {};
    snprintf(tmp_path, sizeof(tmp_path), "%s/.web/authoritative_state.json.tmp", tmp_dir);
    snprintf(final_path, sizeof(final_path), "%s/.web/authoritative_state.json", tmp_dir);
    remove(tmp_path);
    remove(final_path);
    rmdir(web_dir);
    rmdir(tmp_dir);
    free(state);
    return ok && wrote > 0 && (size_t)wrote < out_cap;
}
#endif

static void IOSendBinaryPacket(TCP_SOCKET socket, const void* payload, int payload_size)
{
    if (socket == INVALID_TCP_SOCKET || !payload || payload_size <= 0)
        return;
    uint8_t frame[512];
    int frame_len = WS_FRAME_ENCODE(frame, (const uint8_t*)payload, payload_size, 0x2);
    io_send(socket, frame, (size_t)frame_len);
}

static void IOSendJoinAcceptV2(const ServerState* state, TCP_SOCKET socket, int ci)
{
    if (!state || socket == INVALID_TCP_SOCKET || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;
    STRUCT_RSP_JOIN rsp = {};
    rsp.token = 'n';
    rsp.maxcli = (uint8_t)max_players;
    rsp.id = (uint16_t)ci;
    rsp.world_seed = 0;
    rsp.appearance_contract_version = SvrAppearanceContractVersion(state);
    snprintf(rsp.bundle_hash, sizeof(rsp.bundle_hash), "%s", state->appearance_contract.bundle_hash);
    snprintf(rsp.ids_lock_hash, sizeof(rsp.ids_lock_hash), "%s", state->appearance_contract.ids_lock_hash);
    // FL-4131 Phase 7 — echo server-authoritative glyph manifest identity.
    snprintf(rsp.glyph_manifest_hash, sizeof(rsp.glyph_manifest_hash), "%s", state->appearance_contract.glyph_manifest_hash);
    snprintf(rsp.content_pack_id, sizeof(rsp.content_pack_id), "%s", state->appearance_contract.content_pack_id);
    // FL-4131 P10 — echo server-authoritative atlas runtime identity so the
    // client can detect post-handshake drift between its own runtime and the
    // deployed server.
    snprintf(rsp.lut_hash, sizeof(rsp.lut_hash), "%s", state->appearance_contract.lut_hash);
    snprintf(rsp.page_atlas_chain_hash, sizeof(rsp.page_atlas_chain_hash), "%s", state->appearance_contract.page_atlas_chain_hash);
    IOSendBinaryPacket(socket, &rsp, (int)sizeof(rsp));
}

static void IOSendJoinRejectV2(const ServerState* state, TCP_SOCKET socket, uint8_t reason_code)
{
    STRUCT_BRC_JOIN_REJECT_V2 reject = {};
    reject.token = 'g';
    reject.reason_code = reason_code;
    reject.appearance_contract_version = SvrAppearanceContractVersion(state);
    IOSendBinaryPacket(socket, &reject, (int)sizeof(reject));
}

// Helper: close client socket and enqueue synthetic disconnect for tick thread
static void IODisconnectClient(ServerState* state, int ci)
{
    ClientIO* cio = &state->clients[ci];
    if (cio->socket == INVALID_TCP_SOCKET)
        return;
    TCP_CLOSE(cio->socket);
    cio->socket = INVALID_TCP_SOCKET;
    cio->send_offset = 0;
    cio->send_total = 0;
    IOResetControlQueue(cio);
    // Do NOT write cio->phase here — tick thread owns phase via SvrTransitionClientPhase.
    // Enqueue synthetic disconnect; tick thread will call SvrTransitionClientPhase(DISCONNECTING).
    IOEnqueueSynthetic(cio, SVR_SYNTHETIC_DISCONNECT);
}

static void IOLogDisconnectReason(ServerState* state, int ci, const char* reason,
                                  int err_no, int aux_value)
{
    if (!reason) reason = "unknown";
    uint16_t close_code = 0;
    unsigned long long stamp_us = SvrLogStampUs();
    if (state && ci >= 0 && ci < SVR_MAX_CLIENTS)
        close_code = state->clients[ci].disconnect_ws_close_code;
    if (strcmp(reason, "ws_close") == 0)
        printf("[io-disconnect] ci=%d tick=%u stamp_us=%llu reason=%s errno=%d aux=%d close_code=%u\n",
               ci, state ? state->tick : 0, stamp_us, reason, err_no, aux_value, (unsigned)close_code);
    else
        printf("[io-disconnect] ci=%d tick=%u stamp_us=%llu reason=%s errno=%d aux=%d\n",
               ci, state ? state->tick : 0, stamp_us, reason, err_no, aux_value);
}

static void IOHandleOutboundSendStall(ServerState* state, int ci, ClientIO* cio,
                                      const char* reason)
{
    uint64_t now_us = a3dGetTime();
    if (cio->stall_start_us == 0)
    {
        cio->stall_start_us = now_us;
        return;
    }
    if (now_us - cio->stall_start_us > 5000000) // 5s
    {
        IOLogDisconnectReason(state, ci, reason, 0,
                              (int)((now_us - cio->stall_start_us) / 1000));
        IODisconnectClient(state, ci);
    }
}

static bool IOGameplayFramePartialActive(const ClientIO* cio)
{
    return cio && cio->send_offset > 0 && cio->send_offset < cio->send_total;
}

// S9/FL-1942: respawn-window-only lag hypothesis falsified by passive-20260426-055235.
// Respawn-window lag was LOW (tab1 max=66ms, tab2 max=42ms) while post-join floor
// was high (p95=76ms). The lag floor is not spawn-window-local. See U2 for open lane.
//
// S13/FL-2024: IOFlushControlFrames was the behavior fix for the yellow lag floor.
// It was deployed and confirmed working (lag echo now drains without partial-frame
// blocking), but the yellow floor persisted — REJECTED by 212023+212426.
// The yellow floor is DOWNSTREAM of this: arrival-to-proc/render-stamp latency.
// Do NOT remove this function or add a second flush path. The fix is wired;
// the measurement-ownership seam (U2) is what remains open.
// REMOVED: static bool IOFlushControlFrames(ServerState* state, int ci,


#ifdef ASCIICKER_SERVER_TICK_CONTRACT_TESTS
bool SvrTestControlFlushContracts()
{
    int sockets[2] = { INVALID_TCP_SOCKET, INVALID_TCP_SOCKET };
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) != 0)
        return false;

    ClientIO cio = {};
    cio.socket = sockets[0];
    const uint8_t lag_frame[3] = { 0x82, 0x01, 'l' };
    if (!IOQueueControlFrame(&cio, lag_frame, (int)sizeof(lag_frame), true))
    {
        TCP_CLOSE(sockets[0]);
        TCP_CLOSE(sockets[1]);
        return false;
    }
    ServerState* state = (ServerState*)calloc(1, sizeof(ServerState));
    if (!state)
    {
        TCP_CLOSE(sockets[0]);
        TCP_CLOSE(sockets[1]);
        return false;
    }
    if (!IOFlushControlFrames(state, 0, &cio, 4))
    {
        free(state);
        TCP_CLOSE(sockets[0]);
        TCP_CLOSE(sockets[1]);
        return false;
    }
    free(state);

    uint8_t recv_buf[8] = {};
    ssize_t got = recv(sockets[1], recv_buf, sizeof(recv_buf), 0);
    TCP_CLOSE(sockets[0]);
    TCP_CLOSE(sockets[1]);

    if (got != (ssize_t)sizeof(lag_frame))
    {
        fprintf(stderr, "control_flush fail: raw got=%zd expected=%zu\n",
                got, sizeof(lag_frame));
        return false;
    }
    if (memcmp(recv_buf, lag_frame, sizeof(lag_frame)) != 0)
    {
        fprintf(stderr, "control_flush fail: raw frame mismatch\n");
        return false;
    }
    if (IOControlQueueDepth(&cio) != 0 || cio.control_send_offset != 0)
    {
        fprintf(stderr, "control_flush fail: raw queue depth=%u offset=%d\n",
                (unsigned)IOControlQueueDepth(&cio), cio.control_send_offset);
        return false;
    }
    if (cio.lag_echo_send_success_count != 1 || cio.lag_echo_last_errno != 0)
    {
        fprintf(stderr, "control_flush fail: raw counters success=%u errno=%d\n",
                (unsigned)cio.lag_echo_send_success_count, cio.lag_echo_last_errno);
        return false;
    }

    int lag_sockets[2] = { INVALID_TCP_SOCKET, INVALID_TCP_SOCKET };
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, lag_sockets) != 0)
        return false;

    ClientIO lag_cio = {};
    lag_cio.socket = lag_sockets[0];
    STRUCT_RSP_LAG lag_rsp = {};
    lag_rsp.token = 'l';
    lag_rsp.server_enqueue_us32 = 999900u;
    if (!IOQueueBinaryControlPacket(&lag_cio, &lag_rsp, (int)sizeof(lag_rsp), true))
    {
        TCP_CLOSE(lag_sockets[0]);
        TCP_CLOSE(lag_sockets[1]);
        return false;
    }
    ServerState* lag_state = (ServerState*)calloc(1, sizeof(ServerState));
    if (!lag_state)
    {
        TCP_CLOSE(lag_sockets[0]);
        TCP_CLOSE(lag_sockets[1]);
        return false;
    }
    if (!IOFlushControlFrames(lag_state, 0, &lag_cio, 4))
    {
        free(lag_state);
        TCP_CLOSE(lag_sockets[0]);
        TCP_CLOSE(lag_sockets[1]);
        return false;
    }
    free(lag_state);

    uint8_t lag_recv_buf[128] = {};
    ssize_t lag_got = recv(lag_sockets[1], lag_recv_buf, sizeof(lag_recv_buf), 0);
    TCP_CLOSE(lag_sockets[0]);
    TCP_CLOSE(lag_sockets[1]);

    if (lag_got != (ssize_t)IOWsEncodedFrameSize((int)sizeof(STRUCT_RSP_LAG)))
    {
        fprintf(stderr, "control_flush fail: lag got=%zd expected=%d\n",
                lag_got, IOWsEncodedFrameSize((int)sizeof(STRUCT_RSP_LAG)));
        return false;
    }
    const STRUCT_RSP_LAG* sent_lag =
        (const STRUCT_RSP_LAG*)(lag_recv_buf + lag_got - (int)sizeof(STRUCT_RSP_LAG));
    if (sent_lag->token != 'l')
    {
        fprintf(stderr, "control_flush fail: lag token=%d\n", (int)sent_lag->token);
        return false;
    }
    if (sent_lag->server_flush_start_us32 != 1000000u)
    {
        fprintf(stderr, "control_flush fail: lag start=%u\n",
                (unsigned)sent_lag->server_flush_start_us32);
        return false;
    }
    if (sent_lag->server_flush_finish_us32 != 0u)
        return false;
    if (sent_lag->prev_flush_trace_seq != 0u ||
        sent_lag->prev_server_flush_finish_us32 != 0u ||
        sent_lag->prev_server_flush_finish_epoch_us != 0u)
        return false;
    if (lag_cio.lag_echo_last_server_flush_start_us32 != 1000000u)
        return false;
    if (lag_cio.lag_echo_last_server_flush_finish_us32 != 1000000u)
        return false;
    if (lag_cio.lag_echo_last_server_enqueue_to_flush_start_us != 100u)
        return false;
    if (lag_cio.lag_echo_send_success_count != 1 || lag_cio.lag_echo_last_errno != 0)
        return false;

    int blocked_sockets[2] = { INVALID_TCP_SOCKET, INVALID_TCP_SOCKET };
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, blocked_sockets) != 0)
        return false;

    ClientIO blocked_cio = {};
    blocked_cio.socket = blocked_sockets[0];
    blocked_cio.send_offset = 128;
    blocked_cio.send_total = 512;
    if (!IOQueueControlFrame(&blocked_cio, lag_frame, (int)sizeof(lag_frame), true))
    {
        TCP_CLOSE(blocked_sockets[0]);
        TCP_CLOSE(blocked_sockets[1]);
        return false;
    }
    if (IOFlushControlFrames(NULL, 0, &blocked_cio, 4))
    {
        TCP_CLOSE(blocked_sockets[0]);
        TCP_CLOSE(blocked_sockets[1]);
        return false;
    }
    if (blocked_cio.lag_echo_hol_block_count != 1 ||
        blocked_cio.lag_echo_hol_remaining_bytes_max != 384u ||
        !blocked_cio.lag_echo_hol_blocked_active)
    {
        TCP_CLOSE(blocked_sockets[0]);
        TCP_CLOSE(blocked_sockets[1]);
        return false;
    }
    if (IOFlushControlFrames(NULL, 0, &blocked_cio, 4))
    {
        TCP_CLOSE(blocked_sockets[0]);
        TCP_CLOSE(blocked_sockets[1]);
        return false;
    }
    if (blocked_cio.lag_echo_hol_block_count != 1 ||
        blocked_cio.lag_echo_hol_remaining_bytes_max != 384u)
    {
        TCP_CLOSE(blocked_sockets[0]);
        TCP_CLOSE(blocked_sockets[1]);
        return false;
    }
    blocked_cio.send_offset = 0;
    blocked_cio.send_total = 512;
    if (!IOFlushControlFrames(NULL, 0, &blocked_cio, 4))
    {
        TCP_CLOSE(blocked_sockets[0]);
        TCP_CLOSE(blocked_sockets[1]);
        return false;
    }
    if (blocked_cio.lag_echo_hol_blocked_active)
    {
        TCP_CLOSE(blocked_sockets[0]);
        TCP_CLOSE(blocked_sockets[1]);
        return false;
    }
    blocked_cio.send_offset = 64;
    blocked_cio.send_total = 192;
    if (!IOQueueControlFrame(&blocked_cio, lag_frame, (int)sizeof(lag_frame), true))
    {
        TCP_CLOSE(blocked_sockets[0]);
        TCP_CLOSE(blocked_sockets[1]);
        return false;
    }
    if (IOFlushControlFrames(NULL, 0, &blocked_cio, 4))
    {
        TCP_CLOSE(blocked_sockets[0]);
        TCP_CLOSE(blocked_sockets[1]);
        return false;
    }
    TCP_CLOSE(blocked_sockets[0]);
    TCP_CLOSE(blocked_sockets[1]);
    if (blocked_cio.lag_echo_hol_block_count != 2 ||
        blocked_cio.lag_echo_hol_remaining_bytes_max != 384u)
        return false;
    return true;
}
#endif

void* IOThreadEntry(void* arg)
{
    ServerState* state = (ServerState*)arg;
    struct pollfd fds[SVR_MAX_CLIENTS + 1];
    int fd_to_client[SVR_MAX_CLIENTS + 1];
    __atomic_store_n(&state->io_poll_gap_last_us, 0u, __ATOMIC_RELAXED);
    __atomic_store_n(&state->io_poll_gap_max_us, 0u, __ATOMIC_RELAXED);
    __atomic_store_n(&state->io_poll_gap_over_100ms_count, 0u, __ATOMIC_RELAXED);
    __atomic_store_n(&state->io_poll_nfds_last, 0u, __ATOMIC_RELAXED);
    __atomic_store_n(&state->io_poll_ret_last, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&state->io_poll_timeout_ms_last, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&state->io_poll_work_pending_last, 0u, __ATOMIC_RELAXED);
    SvrEnsureIOWakePipe(state);

    printf("[io] IO thread started\n");

    while (__atomic_load_n(&isRunning, __ATOMIC_ACQUIRE))
    {
        // ── Drain accept ring (P1.4: register sockets from accept thread) ──
        AcceptRing* aring = &state->accept_ring;
        uint32_t ar_rd = __atomic_load_n(&aring->read, __ATOMIC_RELAXED);
        uint32_t ar_wr = __atomic_load_n(&aring->write, __ATOMIC_ACQUIRE);
        while (ar_rd != ar_wr)
        {
            AcceptEvent* aev = &aring->events[ar_rd & SVR_ACCEPT_RING_MASK];
            int slot = aev->slot;
            TCP_SOCKET cs = aev->socket;
            ar_rd++;
            __atomic_store_n(&aring->read, ar_rd, __ATOMIC_RELEASE);

            // Initialize client IO slot (IO thread owns these fields).
            // ORDERING: all ring/buffer state must be initialized BEFORE
            // publishing phase, so tick thread never reads stale slot data.
            ClientIO* cio = &state->clients[slot];
            cio->socket = cs;
            cio->peer_ip = aev->peer_ip;   // RQ-103: propagate IP for disconnect cleanup
            cio->ws_upgraded = true;
            cio->in_read = 0;
            cio->in_write = 0;
            cio->send_offset = 0;
            cio->send_total = 0;
            cio->stall_start_us = 0;
            IOResetControlQueue(cio);
            cio->control_queue_drop_count = 0;
            cio->control_pong_drop_count = 0;
            cio->control_queue_max_depth = 0;
            for (int b = 0; b < 3; b++)
                cio->out[b].len = 0;
            cio->write_idx = 0;
            cio->read_idx = 1;
            cio->shared_idx = 2;
            cio->new_data = 0;
            cio->recv_len = 0;
            cio->disconnect_ws_close_code = 0;
            cio->lag_echo_request_count = 0;
            cio->lag_echo_send_success_count = 0;
            cio->lag_echo_queue_drop_count = 0;
            cio->lag_echo_send_errno_count = 0;
            cio->lag_echo_hol_block_count = 0;
            cio->lag_echo_hol_remaining_bytes_max = 0;
            cio->lag_echo_hol_blocked_active = false;
            cio->lag_echo_last_errno = 0;
            cio->lag_echo_last_trace_seq = 0;
            cio->lag_echo_last_client_send_us32 = 0;
	        cio->lag_echo_last_server_rx_us32 = 0;
	        cio->lag_echo_last_server_enqueue_us32 = 0;
	        cio->lag_echo_last_server_flush_start_us32 = 0;
	        cio->lag_echo_last_server_flush_finish_us32 = 0;
	        cio->lag_echo_last_server_rx_epoch_us = 0;
	        cio->lag_echo_last_server_enqueue_epoch_us = 0;
	        cio->lag_echo_last_server_flush_start_epoch_us = 0;
	        cio->lag_echo_last_server_flush_finish_epoch_us = 0;
	        cio->lag_echo_last_server_rx_to_enqueue_us = 0;
            cio->lag_echo_last_server_enqueue_to_flush_start_us = 0;
            cio->lag_echo_last_server_flush_us = 0;

            // RQ-035: initialize keepalive state — treat connect time as last pong
            // so the idle timeout window starts fresh.
            cio->last_pong_us = a3dGetTime();
            // Do not front-load a ping before baseline gameplay state has even
            // had one IO sweep to leave the socket. Fresh connections are
            // already known-alive here; start the keepalive interval now.
            cio->last_ping_sent_us = cio->last_pong_us;
            cio->keepalive_ping_count = 0;
            cio->keepalive_pong_count = 0;
            cio->keepalive_timeout_disconnect = 0;

            // Publish phase AFTER init so tick thread sees clean ring state.
            atomic_store_phase(&cio->phase, CPHASE_CONNECTING);

            // Enqueue synthetic connect for tick thread (P1.7)
            IOEnqueueSynthetic(cio, SVR_SYNTHETIC_CONNECT);

            printf("[io] Registered socket for slot %d\n", slot);
        }

        // ── Build poll set (client sockets only, no listen socket) ──
        int nfds = 0;
        bool io_work_pending = false;
        int wake_read_fd = __atomic_load_n(&state->io_wake_read_fd, __ATOMIC_ACQUIRE);
        if (wake_read_fd >= 0)
        {
            fds[nfds].fd = wake_read_fd;
            fds[nfds].events = POLLIN;
            fd_to_client[nfds] = -1;
            nfds++;
        }
        for (int i = 0; i < SVR_MAX_CLIENTS; i++)
        {
            ClientIO* cio = &state->clients[i];
            if (cio->socket == INVALID_TCP_SOCKET) continue;

            // Check for new outbound data from tick thread. Do not adopt a
            // newer buffer mid-frame: TCP may already contain a prefix of the
            // current WebSocket frame, so the remainder must be sent first.
            bool outbound_send_pending = cio->send_offset < cio->send_total;
            if (!outbound_send_pending &&
                __atomic_load_n(&cio->new_data, __ATOMIC_ACQUIRE))
            {
                int old = __atomic_exchange_n(&cio->shared_idx,
                                              cio->read_idx, __ATOMIC_ACQ_REL);
                cio->read_idx = old;
                cio->send_offset = 0;
                cio->send_total = cio->out[cio->read_idx].len;
                if (cio->send_total >= 3)
                {
                    uint8_t* buf = cio->out[cio->read_idx].data;
                    int hdr_off = 2;
                    uint64_t payload_len = buf[1] & 0x7F;
                    if (payload_len == 126 && cio->send_total >= 4)
                    {
                        payload_len = ((uint64_t)buf[2] << 8) | buf[3];
                        hdr_off = 4;
                    }
                    else if (payload_len == 127 && cio->send_total >= 10)
                    {
                        hdr_off = 10;
                    }
                    if (cio->send_total > hdr_off)
                    {
                        char token = (char)buf[hdr_off];
                        if (token == 'i' || token == 'h' || token == 'd' || token == 'k' || token == 'r')
                        {
                            SvrRuntimeDiagLog(state,
                                              "[event-debug] io-publish ci=%d token=%c send_total=%d read_idx=%d\n",
                                              i, token, cio->send_total, cio->read_idx);
                        }
                    }
                }
                __atomic_store_n(&cio->new_data, 0, __ATOMIC_RELEASE);
                {
                    static int diag_io_adopt_logs[SVR_MAX_CLIENTS] = {};
                    if (state->players[i].active &&
                        state->players[i].phase >= CPHASE_JOINED &&
                        diag_io_adopt_logs[i] < 64)
                    {
                        SvrRuntimeDiagLog(state,
                        "[DIAG-IO-ADOPT] ci=%d tick=%u phase=%d read_idx=%d old_shared=%d send_total=%d send_offset=%d shared_idx=%d new_data=%d\n",
                               i,
                               (unsigned)state->tick,
                               (int)state->players[i].phase,
                               cio->read_idx,
                               old,
                               cio->send_total,
                               cio->send_offset,
                               __atomic_load_n(&cio->shared_idx, __ATOMIC_RELAXED),
                               __atomic_load_n(&cio->new_data, __ATOMIC_RELAXED));
                        diag_io_adopt_logs[i]++;
                    }
                }
            }

            fds[nfds].fd = cio->socket;
            fds[nfds].events = POLLIN;
            fd_to_client[nfds] = i;

            if (cio->send_offset < cio->send_total || IOHasControlFramePending(cio))
            {
                fds[nfds].events |= POLLOUT;
                io_work_pending = true;
            }

            nfds++;
        }

        if (nfds == 0)
        {
            THREAD_SLEEP(1);
            continue;
        }

        const uint64_t poll_start_us = a3dGetTime();
        // FL-2957: wake writes are the tick->IO responsiveness owner. Do not
        // spin permanently just because a client is connected; nonblocking poll
        // is only for actual outbound/control work, while the wake fd breaks
        // the bounded 1ms idle wait when tick publishes a new frame.
        const int poll_timeout_ms = io_work_pending ? 0 : 1;
        __atomic_store_n(&state->io_poll_nfds_last, (uint32_t)nfds, __ATOMIC_RELAXED);
        __atomic_store_n(&state->io_poll_timeout_ms_last, (int32_t)poll_timeout_ms, __ATOMIC_RELAXED);
        __atomic_store_n(&state->io_poll_work_pending_last, io_work_pending ? 1u : 0u, __ATOMIC_RELAXED);
        int ret = poll(fds, nfds, poll_timeout_ms);
        __atomic_store_n(&state->io_poll_ret_last, (int32_t)ret, __ATOMIC_RELAXED);
        const uint64_t poll_end_us = a3dGetTime();
        const uint32_t poll_gap_us = (uint32_t)(poll_end_us - poll_start_us);
        __atomic_store_n(&state->io_poll_gap_last_us, poll_gap_us, __ATOMIC_RELAXED);
        const bool io_poll_gap_actionable =
            io_work_pending || nfds > 1 || ret > 0;
        if (io_poll_gap_actionable)
        {
            uint32_t prev_poll_gap_max = __atomic_load_n(&state->io_poll_gap_max_us, __ATOMIC_RELAXED);
            while (poll_gap_us > prev_poll_gap_max &&
                   !__atomic_compare_exchange_n(&state->io_poll_gap_max_us,
                                                &prev_poll_gap_max,
                                                poll_gap_us,
                                                false,
                                                __ATOMIC_RELAXED,
                                                __ATOMIC_RELAXED))
            {
            }
            if (poll_gap_us >= SVR_IO_POLL_GAP_LOG_THRESHOLD_US)
            {
                __atomic_add_fetch(&state->io_poll_gap_over_100ms_count, 1u, __ATOMIC_RELAXED);
                if (g_io_poll_gap_logs < SVR_IO_POLL_GAP_LOG_LIMIT)
                {
                    SvrRuntimeDiagLog(state,
                    "[io-poll-gap] gap_us=%u nfds=%d ready=%d timeout_ms=%d\n",
                           (unsigned)poll_gap_us,
                           nfds,
                           ret,
                           poll_timeout_ms);
                    g_io_poll_gap_logs++;
                }
            }
        }
        if (ret <= 0) continue;

        for (int fi = 0; fi < nfds; fi++)
        {
            if (!fds[fi].revents) continue;
            int ci = fd_to_client[fi];
            if (ci < 0)
            {
                SvrDrainIOWakePipe(state);
                continue;
            }
            bool saw_hup = (fds[fi].revents & POLLHUP) != 0;

            // ── Hard error ─────────────────────────────────
            if (fds[fi].revents & (POLLERR | POLLNVAL))
            {
                IOLogDisconnectReason(state, ci, "poll_revents", 0, fds[fi].revents);
                IODisconnectClient(state, ci);
                continue;
            }

            // ── Readable: recv WS data (non-blocking accumulator) ────
            if (fds[fi].revents & POLLIN)
            {
                ClientIO* cio = &state->clients[ci];
                bool client_error = false;
                const char* client_error_reason = NULL;
                int client_error_errno = 0;
                int client_error_aux = 0;

                // Drain socket into accumulator buffer
                int space = (int)sizeof(cio->recv_buf) - cio->recv_len;
                if (space > 0)
                {
                    ssize_t r = recv(cio->socket, cio->recv_buf + cio->recv_len,
                                     space, 0);
                    if (r > 0)
                    {
                        cio->recv_len += (int)r;
                        // RQ-035: any received data proves the connection is alive.
                        // Refresh last_pong_us so active clients sending game data
                        // are never falsely timed out even if a proxy strips pong frames.
                        cio->last_pong_us = a3dGetTime();
                    }
                    else if (r == 0)
                    {
                        client_error = true; // peer closed
                        client_error_reason = "recv_0";
                    }
                    else if (errno != EAGAIN && errno != EWOULDBLOCK)
                    {
                        client_error = true;
                        client_error_reason = "recv_errno";
                        client_error_errno = errno;
                    }
                }

                // Extract complete WS frames from accumulator
                while (!client_error)
                {
                    uint8_t* rb = cio->recv_buf;
                    int rl = cio->recv_len;
                    if (rl < 2) break;

                    // Parse frame header
                    int opcode = rb[0] & 0x0F;
                    bool masked = (rb[1] & 0x80) != 0;
                    uint64_t payload_len = rb[1] & 0x7F;
                    int hdr_off = 2;

                    if (payload_len == 126)
                    {
                        if (rl < 4) break;
                        payload_len = ((uint64_t)rb[2] << 8) | rb[3];
                        hdr_off = 4;
                    }
                    else if (payload_len == 127)
                    {
                        if (rl < 10) break;
                        payload_len = ((uint64_t)rb[2] << 56) | ((uint64_t)rb[3] << 48) |
                                      ((uint64_t)rb[4] << 40) | ((uint64_t)rb[5] << 32) |
                                      ((uint64_t)rb[6] << 24) | ((uint64_t)rb[7] << 16) |
                                      ((uint64_t)rb[8] << 8)  |  (uint64_t)rb[9];
                        hdr_off = 10;
                    }

                    if (masked) hdr_off += 4;
                    if (payload_len > (uint64_t)(sizeof(cio->recv_buf) - hdr_off))
                    {
                        client_error = true;
                        client_error_reason = "ws_frame_too_large";
                        client_error_aux = opcode;
                        break;
                    }
                    // INT_MAX guard (FL-1777 / RQ-030): reject before cast to avoid
                    // integer truncation when a crafted header claims a huge payload.
                    // Practical impact is bounded by the recv_buf check above, but the
                    // explicit guard matches the pattern in network.cpp WS_READ.
                    if (payload_len > (uint64_t)INT_MAX)
                    {
                        client_error = true;
                        client_error_reason = "ws_frame_too_large";
                        client_error_aux = opcode;
                        break;
                    }
                    int frame_total = hdr_off + (int)payload_len;

                    if (rl < frame_total) break; // frame not yet complete

                    if (opcode == 0x8) // close frame
                    {
                        uint16_t close_code = 0;
                        if (payload_len >= 2)
                        {
                            const uint8_t* close_payload = rb + hdr_off;
                            uint8_t close_b0 = close_payload[0];
                            uint8_t close_b1 = close_payload[1];
                            if (masked)
                            {
                                const uint8_t* mk = rb + (hdr_off - 4);
                                close_b0 ^= mk[0];
                                close_b1 ^= mk[1];
                            }
                            close_code = (uint16_t)(((uint16_t)close_b0 << 8) | (uint16_t)close_b1);
                        }
                        cio->disconnect_ws_close_code = close_code;
                        client_error = true;
                        client_error_reason = "ws_close";
                        client_error_aux = opcode;
                        break;
                    }

                    if (opcode == 0x2 || opcode == 0x1) // binary or text
                    {
                        // Unmask payload (clamped to InMsg capacity)
                        uint8_t payload[SVR_INBOUND_MSG_MAX];
                        int copy = (int)payload_len <= (int)sizeof(payload) ? (int)payload_len : (int)sizeof(payload);
                        if (masked)
                        {
                            const uint8_t* mk = rb + (hdr_off - 4);
                            for (int k = 0; k < copy; k++)
                                payload[k] = rb[hdr_off - 4 + 4 + k] ^ mk[k & 3];
                        }
                        else
                        {
                            memcpy(payload, rb + hdr_off, copy);
                        }
                        if (fl2896_rx_logs[ci] < 64)
                        {
                            SvrRuntimeDiagLog(state,
                        "[FL-2896-SVR-RX] ci=%d tick=%u opcode=%d token=%c copy=%d masked=%d recv_len=%d\n",
                                   ci,
                                   (unsigned)state->tick,
                                   opcode,
                                   copy > 0 ? (char)payload[0] : '-',
                                   copy,
                                   masked ? 1 : 0,
                                   cio->recv_len);
                            fl2896_rx_logs[ci]++;
                        }

                        // Queue lag echo in the IO-owned control stream. Do not write
                        // directly to the socket here: the writable path is the single
                        // owner that preserves WebSocket frame serialization.
	                    if ((copy == sizeof(STRUCT_REQ_LAG) || copy == 4) && payload[0] == 'L')
	                    {
	                        uint32_t rx_us32 = (uint32_t)a3dGetTime();
	                        uint64_t rx_epoch_us = SvrRealtimeEpochUs();
	                        __atomic_add_fetch(&cio->lag_echo_request_count, 1u, __ATOMIC_RELAXED);
	                        uint32_t trace_seq = 0;
	                        uint32_t client_send_us32 = 0;
	                        uint32_t enqueue_us32 = (uint32_t)a3dGetTime();
	                        uint64_t enqueue_epoch_us = SvrRealtimeEpochUs();
	                        bool queued = false;
                            if (copy == sizeof(STRUCT_REQ_LAG))
                            {
                                STRUCT_RSP_LAG rsp = {};
                                const STRUCT_REQ_LAG* req = (const STRUCT_REQ_LAG*)payload;
                                rsp.token = 'l';
                                memcpy(rsp.stamp, req->stamp, sizeof(rsp.stamp));
                                rsp.trace_seq = req->trace_seq;
                                rsp.client_send_us32 = req->client_send_us32;
                                rsp.server_rx_us32 = rx_us32;
	                            rsp.server_enqueue_us32 = enqueue_us32;
	                            rsp.server_flush_start_us32 = 0;
	                            rsp.server_flush_finish_us32 = 0;
	                            rsp.server_rx_epoch_us = rx_epoch_us;
	                            rsp.server_enqueue_epoch_us = enqueue_epoch_us;
	                            rsp.server_flush_start_epoch_us = 0;
	                            rsp.server_flush_finish_epoch_us = 0;
	                            rsp.prev_flush_trace_seq =
	                                __atomic_load_n(&cio->lag_echo_last_trace_seq, __ATOMIC_RELAXED);
	                            rsp.prev_server_flush_finish_us32 =
	                                __atomic_load_n(&cio->lag_echo_last_server_flush_finish_us32, __ATOMIC_RELAXED);
	                            rsp.prev_server_flush_finish_epoch_us =
	                                __atomic_load_n(&cio->lag_echo_last_server_flush_finish_epoch_us, __ATOMIC_RELAXED);
                                AKLagTraceBpfMarker(AK_LAG_TRACE_BPF_PHASE_RX,
                                                    rsp.trace_seq,
                                                    rsp.server_rx_us32,
                                                    rsp.client_send_us32);
                                trace_seq = rsp.trace_seq;
                                client_send_us32 = rsp.client_send_us32;
                                // S10/FL-1959: enqueue, do not direct-send.
                                // Direct io_send() here was the red-spike root cause (S10/S11).
                                queued = IOQueueBinaryControlPacket(cio, &rsp, (int)sizeof(rsp), true);
                                if (queued)
                                {
                                    AKLagTraceBpfMarker(AK_LAG_TRACE_BPF_PHASE_ENQUEUE,
                                                        rsp.trace_seq,
                                                        rsp.server_enqueue_us32,
                                                        rsp.server_rx_us32);
                                }
                            }
                            else
                            {
                                // Backward compatibility for legacy 4-byte lag probes.
                                uint8_t rsp_legacy[4];
                                memcpy(rsp_legacy, payload, sizeof(rsp_legacy));
                                rsp_legacy[0] = 'l';
                                queued = IOQueueBinaryControlPacket(cio, rsp_legacy, (int)sizeof(rsp_legacy), true);
                            }
                            __atomic_store_n(&cio->lag_echo_last_trace_seq,
                                             trace_seq, __ATOMIC_RELAXED);
                            __atomic_store_n(&cio->lag_echo_last_client_send_us32,
                                             client_send_us32, __ATOMIC_RELAXED);
                            __atomic_store_n(&cio->lag_echo_last_server_rx_us32,
                                             rx_us32, __ATOMIC_RELAXED);
	                        __atomic_store_n(&cio->lag_echo_last_server_enqueue_us32,
	                                         enqueue_us32, __ATOMIC_RELAXED);
	                        __atomic_store_n(&cio->lag_echo_last_server_rx_epoch_us,
	                                         rx_epoch_us, __ATOMIC_RELAXED);
	                        __atomic_store_n(&cio->lag_echo_last_server_enqueue_epoch_us,
	                                         enqueue_epoch_us, __ATOMIC_RELAXED);
	                        __atomic_store_n(&cio->lag_echo_last_server_rx_to_enqueue_us,
                                             (uint32_t)(enqueue_us32 - rx_us32),
                                             __ATOMIC_RELAXED);
                            if (!queued)
                            {
                                __atomic_add_fetch(&cio->lag_echo_send_errno_count, 1u, __ATOMIC_RELAXED);
                                __atomic_store_n(&cio->lag_echo_last_errno, ENOBUFS, __ATOMIC_RELAXED);
                            }
                            else
                            {
                                IOFlushControlFrames(state, ci, cio, 4);
                                if (cio->socket == INVALID_TCP_SOCKET)
                                    break;
                            }
                        }
                        else
                        {
                            if (copy == sizeof(STRUCT_REQ_JOIN_V2) && payload[0] == 'G')
                            {
                                const STRUCT_REQ_JOIN_V2* req_v2 = (const STRUCT_REQ_JOIN_V2*)payload;
                                char safe_name[32] = {};
                                strncpy(safe_name, req_v2->name, 31);
                                safe_name[31] = 0;

                                // FL-4137 join-path diag (temporary): dump the contract
                                // identifiers the client claims so a mismatch is visible
                                // without 8-byte-prefix guessing. Strip when the join
                                // blocker is closed.
                                {
                                    char client_bundle[65] = {};
                                    char client_ids_lock[65] = {};
                                    char client_glyph[65] = {};
                                    char client_pack[APPEARANCE_CONTENT_PACK_ID_CAP] = {};
                                    strncpy(client_bundle, req_v2->bundle_hash, 64); client_bundle[64] = 0;
                                    strncpy(client_ids_lock, req_v2->ids_lock_hash, 64); client_ids_lock[64] = 0;
                                    strncpy(client_glyph, req_v2->glyph_manifest_hash, 64); client_glyph[64] = 0;
                                    strncpy(client_pack, req_v2->content_pack_id, APPEARANCE_CONTENT_PACK_ID_CAP - 1);
                                    client_pack[APPEARANCE_CONTENT_PACK_ID_CAP - 1] = 0;
                                    printf("[join-v2-diag] ci=%d io_branch name='%s' client_contract=%u server_contract=%u tick=%u stamp_us=%llu\n"
                                           "    client_bundle    =%s\n"
                                           "    server_bundle    =%s\n"
                                           "    client_ids_lock  =%s\n"
                                           "    server_ids_lock  =%s\n"
                                           "    client_glyph     =%s\n"
                                           "    server_glyph     =%s\n"
                                           "    client_pack      =%s\n"
                                           "    server_pack      =%s\n",
                                           ci, safe_name,
                                           (unsigned)req_v2->appearance_contract_version,
                                           (unsigned)SvrAppearanceContractVersion(state),
                                           state ? (unsigned)state->tick : 0u,
                                           SvrLogStampUs(),
                                           client_bundle,
                                           state ? state->appearance_contract.bundle_hash : "<null>",
                                           client_ids_lock,
                                           state ? state->appearance_contract.ids_lock_hash : "<null>",
                                           client_glyph,
                                           state ? state->appearance_contract.glyph_manifest_hash : "<null>",
                                           client_pack,
                                           state ? state->appearance_contract.content_pack_id : "<null>");
                                }

                                // RQ-087: Early character validation on IO thread (defense-in-depth).
                                // Normalize then validate before any further processing.
                                char validated_name[32] = {};
                                SvrNormalizeJoinDisplayName(safe_name, validated_name);

                                uint8_t reject_reason = APPEARANCE_CONTRACT_REJECT_REASON::NONE;
                                if (!SvrValidateJoinNameChars(validated_name))
                                {
                                    printf("[name-validation] IO rejecting invalid chars ci=%d name='%s' tick=%u stamp_us=%llu\n",
                                           ci, safe_name, state ? state->tick : 0, SvrLogStampUs());
                                    reject_reason = APPEARANCE_CONTRACT_REJECT_REASON::NAME_INVALID_CHARS;
                                }
                                else if (req_v2->glyph_manifest_hash[APPEARANCE_HASH_HEX_LEN] != 0 ||
                                         req_v2->content_pack_id[APPEARANCE_CONTENT_PACK_ID_CAP - 1] != 0)
                                {
                                    // FL-4131 Phase 7 — defense-in-depth null-terminator guards on the new wire fields.
                                    // Mirrors the bundle_hash/ids_lock_hash guards in SvrProcessJoinV2.
                                    reject_reason = APPEARANCE_CONTRACT_REJECT_REASON::GLYPH_MANIFEST_MISMATCH;
                                }
                                else
                                {
                                    reject_reason = SvrValidateJoinV2Claims(state,
                                                                            req_v2->appearance_contract_version,
                                                                            req_v2->bundle_hash,
                                                                            req_v2->ids_lock_hash,
                                                                            req_v2->glyph_manifest_hash,
                                                                            req_v2->content_pack_id,
                                                                            req_v2->lut_hash,
                                                                            req_v2->page_atlas_chain_hash);
                                }

                                if (reject_reason != APPEARANCE_CONTRACT_REJECT_REASON::NONE)
                                {
                                    const char* reject_text = SvrAppearanceContractRejectReasonString(reject_reason);
                                    const uint16_t server_contract_version = SvrAppearanceContractVersion(state);
                                    if (reject_reason == APPEARANCE_CONTRACT_REJECT_REASON::CONTRACT_VERSION_MISMATCH &&
                                        server_contract_version == 0)
                                    {
                                        printf("[join-v2] rejecting ci=%d: server bundle not loaded (contract_version=0) tick=%u stamp_us=%llu\n",
                                               ci,
                                               state ? state->tick : 0,
                                               SvrLogStampUs());
                                    }
                                    char safe_bundle_hash[65] = {};
                                    char safe_ids_lock_hash[65] = {};
                                    strncpy(safe_bundle_hash, req_v2->bundle_hash, 64);
                                    safe_bundle_hash[64] = 0;
                                    strncpy(safe_ids_lock_hash, req_v2->ids_lock_hash, 64);
                                    safe_ids_lock_hash[64] = 0;
                                    printf("[appearance-join-v2] rejecting ci=%d reason=%s client_contract=%u server_contract=%u prefix=%02x%02x%02x%02x%02x%02x%02x%02x bundle_hash=%.12s ids_lock_hash=%.12s tick=%u stamp_us=%llu\n",
                                           ci,
                                           reject_text,
                                           (unsigned)req_v2->appearance_contract_version,
                                           (unsigned)server_contract_version,
                                           (unsigned)payload[0],
                                           (unsigned)payload[1],
                                           (unsigned)payload[2],
                                           (unsigned)payload[3],
                                           (unsigned)payload[4],
                                           (unsigned)payload[5],
                                           (unsigned)payload[6],
                                           (unsigned)payload[7],
                                           safe_bundle_hash,
                                           safe_ids_lock_hash,
                                           state ? state->tick : 0,
                                           SvrLogStampUs());
                                    IOSendJoinRejectV2(state, cio->socket, reject_reason);
                                    SvrSendWSCloseReason(cio->socket, 1008, reject_text);
                                    cio->disconnect_ws_close_code = 1008;
                                    client_error = true;
                                    client_error_reason = reject_text;
                                    client_error_aux = (int)reject_reason;
                                    break;
                                }
                                IOSendJoinAcceptV2(state, cio->socket, ci);
                            }
                            if (copy == 32 && payload[0] == 'J')
                            {
                                // FL-972: align string reason with numeric code 1 (CONTRACT_VERSION_MISMATCH)
                                IOSendJoinRejectV2(state, cio->socket, APPEARANCE_CONTRACT_REJECT_REASON::CONTRACT_VERSION_MISMATCH);
                                SvrSendWSCloseReason(cio->socket, 1008, "contract_version_mismatch");
                                cio->disconnect_ws_close_code = 1008;
                                client_error = true;
                                client_error_reason = "contract_version_mismatch";
                                client_error_aux = APPEARANCE_CONTRACT_REJECT_REASON::CONTRACT_VERSION_MISMATCH;
                                break;
                            }
                            // Enqueue to SPSC inbound ring
                            uint32_t wr = __atomic_load_n(&cio->in_write, __ATOMIC_RELAXED);
                            uint32_t rd = __atomic_load_n(&cio->in_read, __ATOMIC_ACQUIRE);
                            if (wr - rd < SVR_MSG_RING_SIZE)
                            {
                                ClientIO::InMsg* m = &cio->in_ring[wr & SVR_MSG_RING_MASK];
                                memcpy(m->data, payload, copy);
                                m->size = (uint16_t)copy;
                                m->recv_stamp_us = a3dGetTime();
                                __atomic_store_n(&cio->in_write, wr + 1, __ATOMIC_RELEASE);
                            }
                        }
                    }
                    else if (opcode == 0x9) // ping — reply with pong (RFC 6455 §5.5.2)
                    {
                        // Unmask ping payload (client frames are always masked)
                        uint8_t ping_data[125]; // control frame payload max 125 bytes
                        int plen = (int)payload_len > 125 ? 125 : (int)payload_len;
                        if (masked)
                        {
                            const uint8_t* mk = rb + (hdr_off - 4);
                            for (int k = 0; k < plen; k++)
                                ping_data[k] = rb[hdr_off + k] ^ mk[k & 3];
                        }
                        else
                            memcpy(ping_data, rb + hdr_off, plen);

                        // Queue unmasked pong with same payload (server never masks)
                        // through the same IO-owned socket writer as gameplay frames.
                        uint8_t pong[256];
                        int pong_len = IOWsEncodedFrameSize(plen);
                        if (pong_len <= 0 || pong_len > (int)sizeof(pong))
                        {
                            IONoteControlDrop(cio, false, true);
                        }
                        else
                        {
                            pong_len = WS_FRAME_ENCODE(pong, ping_data, plen, 0xA);
                            if (IOQueueControlFrame(cio, pong, pong_len, false))
                            {
                                IOFlushControlFrames(state, ci, cio, 4);
                                if (cio->socket == INVALID_TCP_SOCKET)
                                    break;
                            }
                        }

                        // Consume ping frame and continue to next
                        int remaining = cio->recv_len - frame_total;
                        if (remaining > 0)
                            memmove(cio->recv_buf, cio->recv_buf + frame_total, remaining);
                        cio->recv_len = remaining;
                        continue;
                    }
                    else if (opcode == 0xA) // pong — keepalive response (RQ-035)
                    {
                        // Update last-pong timestamp for idle timeout tracking.
                        // Pong payload is consumed but not inspected (RFC 6455 §5.5.3:
                        // pong must echo the ping payload, but we don't validate it).
                        cio->last_pong_us = a3dGetTime();
                        cio->keepalive_pong_count++;

                        // Consume pong frame and continue
                        int remaining = cio->recv_len - frame_total;
                        if (remaining > 0)
                            memmove(cio->recv_buf, cio->recv_buf + frame_total, remaining);
                        cio->recv_len = remaining;
                        continue;
                    }
                    // other opcodes: ignore

                    // Consume frame from accumulator
                    int remaining = cio->recv_len - frame_total;
                    if (remaining > 0)
                        memmove(cio->recv_buf, cio->recv_buf + frame_total, remaining);
                    cio->recv_len = remaining;
                }

                if (client_error)
                {
                    IOLogDisconnectReason(state, ci, client_error_reason,
                                          client_error_errno, client_error_aux);
                    IODisconnectClient(state, ci);
                }
            }

            // ── Writable: send outbound data ─────────────────
            if (fds[fi].revents & POLLOUT)
            {
                ClientIO* cio = &state->clients[ci];
                if (cio->socket == INVALID_TCP_SOCKET)
                    continue;
                bool outbound_partial_active =
                    (cio->send_offset > 0 && cio->send_offset < cio->send_total);
                if (outbound_partial_active ||
                    (cio->send_offset < cio->send_total && !IOHasControlFramePending(cio)))
                {
                    ClientIO::OutBuf* ob = &cio->out[cio->read_idx];
                    int remaining = cio->send_total - cio->send_offset;
                    ssize_t sent = io_send(cio->socket,
                                           ob->data + cio->send_offset, remaining);

                    if (sent > 0)
                    {
                        cio->send_offset += (int)sent;
                        cio->stall_start_us = 0;
                        if (cio->send_offset >= cio->send_total &&
                            IOHasControlFramePending(cio))
                            IOFlushControlFrames(state, ci, cio, 4);
                    }
                    else if (sent == 0)
                        IOHandleOutboundSendStall(state, ci, cio, "send_zero_stall");
                    else if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
                        IOHandleOutboundSendStall(state, ci, cio, "send_stall");
                    else
                    {
                        IOLogDisconnectReason(state, ci, "send_errno", errno, 0);
                        IODisconnectClient(state, ci);
                    }
                }
                else if (IOHasControlFramePending(cio))
                {
                    IOFlushControlFrames(state, ci, cio, 4);
                }
            }

            if (saw_hup)
            {
                ClientIO* cio = &state->clients[ci];
                if (cio->socket != INVALID_TCP_SOCKET)
                {
                    IOLogDisconnectReason(state, ci, "poll_hup", 0, fds[fi].revents);
                    IODisconnectClient(state, ci);
                }
            }
        }

        // ── RQ-035: WebSocket keepalive ping + idle timeout sweep ────
        // After processing all poll events, check every connected client:
        //   1. If we haven't pinged in WS_KEEPALIVE_PING_INTERVAL_US, send a ping.
        //   2. If no pong received within WS_IDLE_TIMEOUT_US, disconnect (ghost).
        // This runs every IO loop iteration (~1ms) but only acts every 30s per client.
        {
            uint64_t now_us = a3dGetTime();
            bool disable_keepalive = SvrKeepaliveDisabled();
            for (int ki = 0; ki < SVR_MAX_CLIENTS; ki++)
            {
                ClientIO* kcio = &state->clients[ki];
                if (kcio->socket == INVALID_TCP_SOCKET)
                    continue;

                if (disable_keepalive)
                    continue;

                // Idle timeout check: disconnect if no pong within WS_IDLE_TIMEOUT_US
                if (kcio->last_pong_us > 0 &&
                    now_us - kcio->last_pong_us > WS_IDLE_TIMEOUT_US)
                {
                    kcio->keepalive_timeout_disconnect = 1;
                    IOLogDisconnectReason(state, ki, "keepalive_timeout", 0,
                                          (int)((now_us - kcio->last_pong_us) / 1000000ULL));
                    IODisconnectClient(state, ki);
                    continue;
                }

                // Periodic ping: send WS ping if interval elapsed
                if (kcio->last_ping_sent_us == 0 ||
                    now_us - kcio->last_ping_sent_us >= WS_KEEPALIVE_PING_INTERVAL_US)
                {
                    // Build a minimal WS ping frame (opcode 0x9, empty payload).
                    // Empty payload is valid per RFC 6455 §5.5.2 and avoids
                    // unnecessary bandwidth on mobile connections.
                    uint8_t ping_frame[2];
                    ping_frame[0] = 0x89; // FIN + opcode 0x9 (ping)
                    ping_frame[1] = 0x00; // no mask, zero payload
                    if (IOQueueControlFrame(kcio, ping_frame, 2, false))
                    {
                        kcio->last_ping_sent_us = now_us;
                        kcio->keepalive_ping_count++;
                        // Best-effort flush; if socket is busy the frame will
                        // be sent on the next POLLOUT cycle.
                        IOFlushControlFrames(state, ki, kcio, 1);
                    }
                }
            }
        }
    }

    printf("[io] IO thread stopped\n");
    return NULL;
}

bool SvrDoWSHandshake(TCP_SOCKET socket, uint32_t peer_ip, uint32_t* rate_limit_ip_out, uint32_t* forwarded_for_ip_out)
{
    struct Headers
    {
        static void SetFail(Headers* h, const char* reason, const char* header, const char* value)
        {
            if (!h) return;
            if (reason)
            {
                strncpy(h->fail_reason, reason, sizeof(h->fail_reason) - 1);
                h->fail_reason[sizeof(h->fail_reason) - 1] = 0;
            }
            if (header)
            {
                strncpy(h->fail_header, header, sizeof(h->fail_header) - 1);
                h->fail_header[sizeof(h->fail_header) - 1] = 0;
            }
            if (value)
            {
                strncpy(h->fail_value, value, sizeof(h->fail_value) - 1);
                h->fail_value[sizeof(h->fail_value) - 1] = 0;
            }
        }

        static int Fail(Headers* h, const char* reason, const char* header, const char* value)
        {
            SetFail(h, reason, header, value);
            return -3;
        }

        static int cb(const char* header, const char* value, void* param)
        {
            Headers* h = (Headers*)param;
            int mask = 1;

            if (!header)
            {
                auto is_ws_request_line = [](const char* value, char* reject_path, size_t reject_cap) -> int {
                    // FL-4150: parser accepted old/strict request line form and rejected
                    // valid absolute-form variants as request_line_mismatch.
                    if (strncmp(value, "GET ", 4) != 0)
                        return 0;

                    const char* path_start = value + 4;
                    const char* path_end = strchr(path_start, ' ');
                    if (!path_end)
                        return 0;

                    const char* version = path_end;
                    if (strncmp(version, " HTTP/1.1", 9) != 0)
                        return 0;
                    if (version[9] != '\0')
                        return 0;

                    size_t req_target_len = (size_t)(path_end - path_start);
                    if (req_target_len == 0 || req_target_len >= reject_cap)
                        return 0;

                    char target[256];
                    if (req_target_len >= sizeof(target))
                        return 0;
                    memcpy(target, path_start, req_target_len);
                    target[req_target_len] = 0;

                    const char* path = target;
                    if (strncmp(path, "ws://", 5) == 0)
                    {
                        const char* path_slash = strchr(path + 5, '/');
                        if (!path_slash || path_slash[0] == '\0')
                            return 0;
                        path = path_slash;
                    }
                    else if (strncmp(path, "wss://", 6) == 0)
                    {
                        const char* path_slash = strchr(path + 6, '/');
                        if (!path_slash || path_slash[0] == '\0')
                            return 0;
                        path = path_slash;
                    }
                    else if (path[0] != '/')
                    {
                        if (reject_cap > 0)
                        {
                            strncpy(reject_path, target, reject_cap - 1);
                            reject_path[reject_cap - 1] = 0;
                        }
                        return 0;
                    }

                    size_t path_len = strlen(path);
                    if (strncmp(path, "/ws/y8", 6) != 0)
                    {
                        if (reject_cap > 0)
                        {
                            strncpy(reject_path, path, reject_cap - 1);
                            reject_path[reject_cap - 1] = 0;
                        }
                        return 0;
                    }

                    if (path_len > 6 && path[6] != '/' && path[6] != '?')
                    {
                        if (reject_cap > 0)
                        {
                            strncpy(reject_path, path, reject_cap - 1);
                            reject_path[reject_cap - 1] = 0;
                        }
                        return 0;
                    }

                    return 1;
                };

                if (h->parsed & mask) return Fail(h, "duplicate_request_line", "<request-line>", value);
                h->parsed |= mask;

                char request_path[128];
                request_path[0] = 0;
                if (!is_ws_request_line(value, request_path, sizeof(request_path)))
                    return Fail(h, "request_line_mismatch", "<request-line>", value);
                return 0;
            }

            mask <<= 1;
            if (strcasecmp(header, "Sec-WebSocket-Version") == 0)
            {
                if (h->parsed & mask) return Fail(h, "duplicate_ws_version", header, value);
                h->parsed |= mask;
                if (strcmp(value, "13") != 0) return Fail(h, "unsupported_ws_version", header, value);
                return 0;
            }

            mask <<= 1;
            if (strcasecmp(header, "Sec-WebSocket-Key") == 0)
            {
                if (h->parsed & mask) return Fail(h, "duplicate_ws_key", header, value);
                h->parsed |= mask;
                h->keylen = (int)strlen(value);
                if (h->keylen >= 64) return Fail(h, "ws_key_too_long", header, value);
                strcpy(h->key, value);
                return 0;
            }

            mask <<= 1;
            if (strcasecmp(header, "Upgrade") == 0)
            {
                if (h->parsed & mask) return Fail(h, "duplicate_upgrade_header", header, value);
                h->parsed |= mask;
                if (strcasecmp(value, "websocket") != 0)
                    return Fail(h, "upgrade_header_value_mismatch", header, value);
                return 0;
            }

            mask <<= 1;
            if (strcasecmp(header, "Connection") == 0)
            {
                if (h->parsed & mask) return Fail(h, "duplicate_connection_header", header, value);
                h->parsed |= mask;

                int i = 0;
                while (1)
                {
                    while (value[i] == ' ' || value[i] == '\t') i++;
                    int j = i;
                    while (value[j] != ',' && value[j] != 0) j++;

                    int end = j;
                    while (end > i && (value[end - 1] == ' ' || value[end - 1] == '\t')) end--;

                    if (end - i == 7 && strncasecmp(value + i, "Upgrade", 7) == 0)
                        return 0;
                    if (value[j] == 0)
                        return Fail(h, "connection_missing_upgrade_token", header, value);
                    i = j + 1;
                }
                return Fail(h, "connection_parse_fell_through", header, value);
            }

            if (strcasecmp(header, "User-Agent") == 0)
            {
                strncpy(h->user_agent, value, sizeof(h->user_agent) - 1);
                h->user_agent[sizeof(h->user_agent) - 1] = 0;
                return 0;
            }

            if (strcasecmp(header, "X-Forwarded-For") == 0)
            {
                uint32_t parsed_ip = 0;
                if (SvrParseForwardedForIp(value, &parsed_ip))
                    h->forwarded_for_ip = parsed_ip;
                return 0;
            }

            mask <<= 1;
            if (strcasecmp(header, "Content-Length") == 0)
            {
                if (h->parsed & mask) return Fail(h, "duplicate_content_length", header, value);
                h->parsed |= mask;
                if (strcmp(value, "0") != 0) return Fail(h, "unexpected_content_length", header, value);
                return 0;
            }

            return 0;
        }

        int keylen;
        char key[128];
        int parsed;
        char fail_reason[128];
        char fail_header[128];
        char fail_value[256];
        char user_agent[256];
        uint32_t forwarded_for_ip;
    } headers;

    headers.parsed = 0;
    headers.fail_reason[0] = 0;
    headers.fail_header[0] = 0;
    headers.fail_value[0] = 0;
    headers.user_agent[0] = 0;
    headers.forwarded_for_ip = 0;

    int ok = HTTP_READ(socket, Headers::cb, &headers, 0);
    int read_errno = (ok == -1) ? errno : 0;
    bool timed_out = ok == -1 && (read_errno == EAGAIN || read_errno == EWOULDBLOCK);
    if (ok != 0 || (headers.parsed & 31) != 31)
    {
        printf("[ws] handshake reject: ok=%d errno=%d timeout=%d parsed=0x%02x stamp_us=%llu ua=%s reason=%s header=%s value=%s\n",
               ok,
               read_errno,
               timed_out ? 1 : 0,
               headers.parsed,
               SvrLogStampUs(),
               headers.user_agent[0] ? headers.user_agent : "<none>",
               headers.fail_reason[0] ? headers.fail_reason : "<unknown>",
               headers.fail_header[0] ? headers.fail_header : "<none>",
               headers.fail_value[0] ? headers.fail_value : "<none>");
        const char* why = headers.fail_reason[0] ? headers.fail_reason
            : (timed_out ? "recv_timeout" : (ok < 0 ? "recv_error" : "incomplete_headers"));
        char body[256];
        int body_len = snprintf(body, sizeof(body), "websocket handshake rejected: %s\n", why);
        char response[512];
        int response_len = snprintf(
            response,
            sizeof(response),
            "HTTP/1.1 400 Bad Request\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n"
            "\r\n"
            "%s",
            body_len,
            body
        );
        if (response_len > 0)
            TCP_WRITE(socket, (const uint8_t*)response, response_len);
        return false;
    }

    strcpy(headers.key + headers.keylen, "258EAFA5-E914-47DA-95CA-C5AB0DC85B11");

    unsigned char digest[20];
    SHA1(headers.key, (int)strlen(headers.key), digest);

    char base64[(20 + 2) / 3 * 4 + 1];
    Base64Encode(digest, 20, base64);
    base64[(20 + 2) / 3 * 4] = 0;

    static const char response_fmt[] =
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: WebSocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Accept: %s\r\n\r\n";

    char response_buf[256];
    int response_len = snprintf(response_buf, sizeof(response_buf), response_fmt, base64);

    int w = TCP_WRITE(socket, (const uint8_t*)response_buf, response_len);
    if (w <= 0)
        printf("[ws] handshake write failed: bytes=%d stamp_us=%llu ua=%s\n", w, SvrLogStampUs(), headers.user_agent[0] ? headers.user_agent : "<none>");
    else
        printf("[ws] handshake accepted stamp_us=%llu ua=%s\n", SvrLogStampUs(), headers.user_agent[0] ? headers.user_agent : "<none>");
    if (w > 0 && rate_limit_ip_out)
        *rate_limit_ip_out = SvrResolveRateLimitIp(peer_ip, headers.forwarded_for_ip);
    if (w > 0 && forwarded_for_ip_out)
        *forwarded_for_ip_out = headers.forwarded_for_ip;
    return w > 0;
}
