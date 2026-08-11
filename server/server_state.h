#pragma once

// server_state.h — Authoritative Server State Structures
//
// All structs and constants for the two-thread authoritative server architecture.
// Tick thread owns all game state (SvrPlayerState, SvrNpcState, SvrItemState).
// IO thread owns sockets. Communication via SPSC ring buffers and triple buffer.

#include <stdint.h>
#include <string.h>
#include "physics.h"
#include "network.h"
#include "game.h"
#include "enemygen.h"
#include "world_entity_registry.h"
#include "protocol/protocol_items.h"

struct Inst;

// ─── Constants ───────────────────────────────────────────────────
#define SVR_TICK_RATE          30                          // Hz
#define SVR_TICK_INTERVAL_US   (1000000 / SVR_TICK_RATE)  // 33333 us
#define SVR_PHYSICS_SUBSTEPS   2                          // 60 Hz effective
#define SVR_SPIRAL_CLAMP_US    (SVR_TICK_INTERVAL_US * 5 / 2) // 83333 us

#define SVR_SWING_COOLDOWN_TICKS   12    // 400ms at 30Hz (presentation timing only)
#define SVR_SWING_COOLDOWN_US      400000  // 400ms wall-clock cooldown for rate-limit enforcement
#define SVR_RESPAWN_DELAY_TICKS    90    // 3 seconds at 30Hz

// FL-2481: rate-limit violation disconnect enforcement
#define SVR_RATE_LIMIT_WINDOW_TICKS  (60 * SVR_TICK_RATE)  // 60 seconds = 1800 ticks
#define SVR_RATE_LIMIT_MAX_VIOLATIONS 10                    // threshold within window
#define SVR_RATE_LIMIT_MAX_INPUT_PACKETS_PER_TICK 12        // tolerate browser arrival jitter without kicking real headed/controller traffic
#define SVR_RATE_LIMIT_RING_SIZE      16                    // circular buffer (>= max threshold)
#define SVR_NPC_RESPAWN_TICKS      180   // 6 seconds at 30Hz
#define SVR_SWING_RANGE            3.0f  // world units (matches game.cpp:7658)
#define SVR_SWING_DAMAGE_BASE      15    // base damage per hit
#define SVR_PLAYER_MAX_HP          100
#define SVR_NPC_MAX_HP             50     // ordinary NPC balance; no proof/debug HP env owner
#define SVR_PROJECTILE_TICKS_MIN 2
#define SVR_PROJECTILE_TICKS_MAX 18

static constexpr uint16_t SVR_INBOUND_MSG_MAX =
    (uint16_t)(sizeof(STRUCT_REQ_JOIN_V2) + 64u);
static_assert(SVR_INBOUND_MSG_MAX >= sizeof(STRUCT_REQ_JOIN_V2),
              "server inbound message buffer must fit JOIN_V2");
#define SVR_ITEM_PICKUP_RADIUS     6.0f  // world units (matches game.cpp d2<36 check)
#define SVR_CONSUMABLE_HEAL        30    // HP restored per consumable use
#define SVR_WATER_LEVEL            55.0f // global constant (game_app.cpp:2055)

#define SVR_INTEREST_RADIUS        200.0f  // world units: entity visibility distance
#define SVR_INTEREST_RADIUS_SQ     (SVR_INTEREST_RADIUS * SVR_INTEREST_RADIUS)

// S5/FL-1862: 6-tick freshness window replaced the 16-slot jitter queue (S5) and
// the ordered replay (S7). Tuning this value is safe; reviving the queue is not.
#define SVR_INPUT_STALE_TICKS      6     // latest input sample freshness window
#define SVR_SNAPSHOT_RING_SIZE     32    // per-client ACK ring (Quake 3 style)
#define SVR_MAX_CLIENTS            MULTIPLAYER_ENTITY_PLAYER_CAPACITY
#define SVR_MAX_NPCS               128
#define SVR_MAX_ITEMS              256
#define SVR_MAX_DECAL_HISTORY      64

static_assert(SVR_MAX_CLIENTS == MULTIPLAYER_ENTITY_NPC_BASE,
              "server NPC entity ids and shared client/server lane boundary must match");

// Outbound buffer: 256KB per buffer, triple-buffered (3 per client).
// Extra headroom keeps critical low-frequency events such as death/respawn
// from being squeezed out by larger baseline frames.
#define SVR_OUTBOUND_BUF_SIZE      262144

// S10/S11/FL-1959: direct io_send() for lag echo (old fast path) caused WebSocket
// frame serialization races that produced the post-join red spikes (19/14 red samples
// in passive-20260426-061003). The IO control-ring queue below is the closed fix.
// Do NOT re-add direct io_send() for lag echo, pong, or any control frame.
// Red-spike family closed by passive-20260426-201323 (0/0 red). See U2 for yellow lag.
// IO-thread-owned control frames (lag echo / WebSocket pong). These are kept
// separate from tick-thread snapshots so socket writes still have one owner.
#define SVR_IO_CONTROL_FRAME_SIZE  256
#define SVR_IO_CONTROL_RING_SIZE   32
#define SVR_IO_CONTROL_RING_MASK   (SVR_IO_CONTROL_RING_SIZE - 1)

// SPSC ring buffer capacity (must be power of 2)
#define SVR_MSG_RING_SIZE          64
#define SVR_MSG_RING_MASK          (SVR_MSG_RING_SIZE - 1)

// Accept ring capacity (must be power of 2)
#define SVR_ACCEPT_RING_SIZE       16
#define SVR_ACCEPT_RING_MASK       (SVR_ACCEPT_RING_SIZE - 1)

// Synthetic opcodes for tick-ingest path
#define SVR_SYNTHETIC_CONNECT      0xFE
#define SVR_SYNTHETIC_DISCONNECT   0xFF

// ─── Client Phase (7-state FSM + NONE sentinel) ─────────────────
enum ClientPhase : uint8_t {
    CPHASE_NONE = 0,
    CPHASE_CONNECTING,
    CPHASE_JOINED,
    CPHASE_ALIVE,
    CPHASE_DEAD,
    CPHASE_RESPAWNING,
    CPHASE_SPECTATING,
    CPHASE_DISCONNECTING,
    CPHASE_COUNT  // == 8
};

// Transition validation: [current][target] = allowed
static const bool PHASE_TRANSITIONS[CPHASE_COUNT][CPHASE_COUNT] = {
    //                NONE  CONN  JOIN  ALIVE DEAD  RESP  SPEC  DISC
    /* NONE */      { 0,    1,    0,    0,    0,    0,    0,    0 },
    /* CONNECTING */{ 0,    0,    1,    0,    0,    0,    0,    1 },
    /* JOINED */    { 0,    0,    0,    1,    0,    0,    0,    1 },
    /* ALIVE */     { 0,    0,    0,    0,    1,    0,    1,    1 },
    /* DEAD */      { 0,    0,    0,    0,    0,    1,    0,    1 },
    /* RESPAWNING */{ 0,    0,    0,    1,    0,    0,    0,    1 },
    /* SPECTATING */{ 0,    0,    0,    1,    0,    0,    0,    1 },
    /* DISCONNECT */{ 0,    0,    0,    0,    0,    0,    0,    0 },
};

// ─── Atomic Phase Helpers ────────────────────────────────────────

// Read phase with acquire semantics (IO thread reads this for send gating)
static inline ClientPhase atomic_load_phase(const volatile ClientPhase* p)
{
    return (ClientPhase)__atomic_load_n((const volatile uint8_t*)p, __ATOMIC_ACQUIRE);
}

// Write phase with release semantics
static inline void atomic_store_phase(volatile ClientPhase* p, ClientPhase val)
{
    __atomic_store_n((volatile uint8_t*)p, (uint8_t)val, __ATOMIC_RELEASE);
}

enum ClientDisconnectReason : uint8_t
{
    CDR_NONE = 0,
    CDR_POLL_EVENT,
    CDR_RECV_EOF,
    CDR_RECV_ERROR,
    CDR_WS_CLOSE,
};

// ─── Per-Client IO (owned by IO thread + SPSC boundary) ─────────
struct ClientIO
{
    // Socket (IO thread owns)
    TCP_SOCKET socket;

    // Inbound SPSC: IO thread writes, tick thread reads
    struct InMsg { uint8_t data[SVR_INBOUND_MSG_MAX]; uint16_t size; uint64_t recv_stamp_us; };
    InMsg in_ring[SVR_MSG_RING_SIZE];
    // SPSC ring: in_write owned exclusively by IO thread (producer).
    // in_read owned exclusively by tick thread (consumer).
    // Cache-line aligned to prevent false sharing. No additional
    // memory barriers needed — single-writer/single-reader guarantee.
    alignas(64) volatile uint32_t in_write;
    alignas(64) volatile uint32_t in_read;

    // ── Outbound Triple Buffer ───────────────────────────────────
    // Tick thread OWNS write_idx. IO thread OWNS read_idx.
    // Handoff via shared_idx + new_data using __atomic_exchange_n
    // with acquire/release semantics.
    struct OutBuf {
        uint8_t data[SVR_OUTBOUND_BUF_SIZE];
        int len;
    };
    OutBuf out[3];
    int write_idx;                           // tick thread private
    int read_idx;                            // IO thread private
    // Triple buffer handoff: shared_idx written by tick thread,
    // read by IO thread. new_data flags pending update.
    alignas(64) volatile int shared_idx;     // middle buffer
    alignas(64) volatile int new_data;       // 1 = tick published

    // Per-client partial send tracking (non-blocking writes)
    int send_offset;
    int send_total;
    uint64_t stall_start_us;  // when write first stalled; 0 = not stalled

    // IO-thread-owned control frame queue. Only IOThreadEntry writes/reads this
    // queue, so no atomics are needed; it serializes lag echo/pong frames through
    // the same socket-send owner as gameplay frames.
    struct ControlFrame {
        uint8_t data[SVR_IO_CONTROL_FRAME_SIZE];
        int len;
        bool lag_echo;
    };
    ControlFrame control_ring[SVR_IO_CONTROL_RING_SIZE];
    uint32_t control_read;
    uint32_t control_write;
    int control_send_offset;
    uint32_t control_queue_drop_count;
    uint32_t control_pong_drop_count;
    uint32_t control_queue_max_depth;
    uint32_t control_queue_depth_last;
    uint32_t control_send_offset_last;

    // Phase (written by tick thread, read by IO thread for send gating)
    volatile ClientPhase phase;

    // WebSocket upgrade state (IO thread only)
    bool ws_upgraded;
    uint16_t disconnect_ws_close_code;
    // Lag echo diagnostics (IO thread writes, tick thread reads via atomic loads
    // when publishing authoritative_state.json).
    uint32_t lag_echo_request_count;
    uint32_t lag_echo_send_success_count;
    uint32_t lag_echo_queue_drop_count;
    uint32_t lag_echo_send_errno_count;
    int lag_echo_last_errno;
    uint32_t lag_echo_last_trace_seq;
    uint32_t lag_echo_last_client_send_us32;
	uint32_t lag_echo_last_server_rx_us32;
	uint32_t lag_echo_last_server_enqueue_us32;
	uint32_t lag_echo_last_server_flush_start_us32;
	uint32_t lag_echo_last_server_flush_finish_us32;
	uint64_t lag_echo_last_server_rx_epoch_us;
	uint64_t lag_echo_last_server_enqueue_epoch_us;
	uint64_t lag_echo_last_server_flush_start_epoch_us;
	uint64_t lag_echo_last_server_flush_finish_epoch_us;
	uint32_t lag_echo_last_server_rx_to_enqueue_us;
	uint32_t lag_echo_last_server_enqueue_to_flush_start_us;
	uint32_t lag_echo_last_server_flush_us;
    uint32_t lag_echo_hol_block_count;
    uint32_t lag_echo_hol_remaining_bytes_max;
    bool lag_echo_hol_blocked_active;

    // Player name (written by tick thread during join handling, NOT the IO thread).
    // The comment "set during join, read by tick thread" was factually wrong — tick
    // thread is the writer. No current IO-thread reader exists, so there is no active
    // data race, but the field's existence invites future split reads.
    // DEFERRED: remove this field and pass name through the join SPSC event instead
    // (C-2 / ownership migration). Until then, tick thread is the sole writer.
    char name[32];

    // Per-IP rate limiting (RQ-103): IPv4 address in network byte order.
    // Written by IO thread when draining accept ring; read by tick thread on disconnect.
    uint32_t peer_ip;

    // ── WebSocket keepalive (IO thread only) ────────────────────────
    // RQ-035: application-level ping/pong to detect ghost connections.
    // IO thread sends WS ping (opcode 0x9) every WS_KEEPALIVE_PING_INTERVAL_US.
    // If no pong received within WS_IDLE_TIMEOUT_US, client is disconnected.
    uint64_t last_pong_us;        // timestamp of last pong (or connect time as baseline)
    uint64_t last_ping_sent_us;   // timestamp of last outgoing ping; 0 = none sent yet
    uint32_t keepalive_ping_count;       // total pings sent to this client
    uint32_t keepalive_pong_count;       // total pongs received from this client
    uint32_t keepalive_timeout_disconnect; // 1 if this client was disconnected by keepalive

    // Non-blocking WS frame accumulator (IO thread only)
    // Bytes are appended by recv() calls; complete frames extracted when ready.
    uint8_t recv_buf[2048];
    int recv_len;     // bytes valid in recv_buf
};

// ─── Latest Client Input Sample ─────────────────────────────────
struct InputSlot
{
    uint32_t recv_tick;
    uint16_t seq;
    float force[2];
    float force_z;
    float yaw;
    uint8_t flags;         // bit0=jump, bit1=fly, bit2=attack, bits3-4=mount
    uint8_t mount_intent;
    bool valid;
};

// ─── Per-Client Snapshot ACK Ring (Quake 3 pattern) ──────────────
struct SnapshotACK
{
    uint16_t seq;
    uint32_t tick;
    int entity_count;
};

#define SVR_MAX_APPEARANCE_LOADOUT_ENTRIES 8

enum SvrAppearanceSourceKind : uint8_t
{
    SVR_APPEARANCE_SOURCE_NONE = 0,
    SVR_APPEARANCE_SOURCE_DEFAULT_PROFILE = 1,
    SVR_APPEARANCE_SOURCE_SERVER_SEAT_PROFILE = 2,
};

enum SvrAppearanceProjectionKind : uint8_t
{
    SVR_APPEARANCE_PROJECTION_NONE = 0,
    SVR_APPEARANCE_PROJECTION_PROFILE = 1,
};

enum SvrAppearanceSubjectKind : uint8_t
{
    SVR_APPEARANCE_SUBJECT_NONE = 0,
    SVR_APPEARANCE_SUBJECT_DEFAULT = 1,
    SVR_APPEARANCE_SUBJECT_SERVER_SEAT = 2,
    SVR_APPEARANCE_SUBJECT_NPC_SPAWN = 3,
};

enum SvrAppearanceEntryStateFlags : uint16_t
{
    SVR_APPEARANCE_ENTRY_STATE_EQUIPPED = 1 << 0,
};

struct SvrAppearanceLoadoutEntry
{
    uint16_t slot_kind_id;
    uint16_t item_instance_id;
    uint16_t item_definition_id;
    uint16_t visual_style_id;
    uint16_t state_flags;
};

// Server-authoritative appearance identity — Step 4 server storage.
//
// STALE CONTRACT DISCLAIMER (2026-04-28): See engine/game.cpp header.
// FL-2345 mounted rollback, FL-2354 proxy, open gates apply.
// FL-2360 tracks this.
//
// Populated by SvrApplyProfileToAppearance() at Step 4 and read by
// SvrFillAppearanceStateV2() at Step 5 to build the STRUCT_BRC_APPEARANCE_STATE_V2
// packet. This is the server's ground truth for every actor's visual identity
// before any packet is sent.
//
// Client-side counterpart: AppearanceStateV2 in engine/game.h. The client
// struct mirrors these fields minus item_instance_id, which the server keeps
// for gameplay purposes but does not transmit.
//
// What each field means:
// - skin_definition_id: body-owner family (e.g. "cyan_suit") chosen by profile
//   or gameplay logic. Not a body part — the bundle resolves the actual body
//   layer from this family id at render time.
// - mount_definition_id: mount-owner family when the actor is mounted. Zero
//   when dismounted.
// - variation_id: server-owned presentation discriminator. Zero is the default
//   authored variation; clients must not infer this from equipment.
// - rig_id: server-owned rig/attachment seam discriminator. Zero is the default
//   rig contract.
// - appearance_profile_id: which server profile seeded this appearance. Used
//   for profile re-application on respawn or loadout reset.
// - loadout_revision: monotonic counter bumped on every equipment change.
//   Clients use it to detect stale sprite cache entries.
// - source_kind: server-owned enum recording where the appearance came from
//   (profile assignment, world source, forced override, etc.).
// - projection_kind: appearance projection namespace (actor vs world-item vs
//   inventory-item presentation path).
// - subject_kind: top-level surface kind (actor, world_item, inventory_item).
//   Determines which bundle lookup path the client uses.
// - entries[]: the equipped-slot list ("slot manifest" informally). Each entry
//   binds one slot_kind_id to an item_instance_id + item_definition_id +
//   visual_style_id. item_instance_id is gameplay-only; the client only
//   receives item_definition_id and visual_style_id.
struct SvrAuthoritativeAppearanceState
{
    uint16_t appearance_contract_version;
    uint16_t skin_definition_id;     // body-owner family id, not a body part
    uint16_t mount_definition_id;    // mount-owner family id; 0 when not mounted
    uint16_t variation_id;           // presentation discriminator; 0 is default
    uint16_t rig_id;                 // rig/attachment seam; 0 is default
    uint16_t appearance_profile_id;  // profile that seeded this appearance
    uint32_t loadout_revision;       // bumped on every equipment change
    uint8_t source_kind;             // where this appearance came from
    uint8_t projection_kind;         // appearance namespace (actor, world-item, etc.)
    uint8_t subject_kind;            // surface kind (actor, world_item, inventory_item)
    uint8_t entry_count;
    char subject_key[32];
    SvrAppearanceLoadoutEntry entries[SVR_MAX_APPEARANCE_LOADOUT_ENTRIES];
};

struct SvrAppearanceContractState
{
    bool loaded;
    uint16_t contract_version;
    char bundle_hash[APPEARANCE_HASH_HEX_LEN + 1];
    char ids_lock_hash[APPEARANCE_HASH_HEX_LEN + 1];
    // FL-4131 Phase 7 — glyph manifest identity (server-authoritative).
    // Populated alongside bundle_hash at contract load. Empty hash => the
    // server has no manifest bound; SvrValidateJoinV2Claims must still
    // reject mismatching claims (it does NOT silently accept empty server hash).
    char glyph_manifest_hash[APPEARANCE_HASH_HEX_LEN + 1];
    char content_pack_id[APPEARANCE_CONTENT_PACK_ID_CAP];
    // FL-4131 P10 — atlas runtime identity (server-authoritative). lut_hash is
    // the AOA glyph_index SHA-256; page_atlas_chain_hash is the SHA-256 over
    // (cell_px, page_hash) tuples in cell_px order. Both are populated from
    // ACTOR_VISUAL_PROFILE_LUT_SHA256 / _PAGE_ATLAS_CHAIN_SHA256 at contract
    // load. Empty implies CP437-only build; the validator rejects mixed empty
    // vs non-empty just like glyph_manifest_hash does.
    char lut_hash[APPEARANCE_HASH_HEX_LEN + 1];
    char page_atlas_chain_hash[APPEARANCE_HASH_HEX_LEN + 1];
};

// ─── Player State (authoritative, tick thread only) ──────────────
struct SvrPlayerState
{
    bool active;
    ClientPhase phase;

    // Identity
    char name[32];
    int player_id;
    bool join_v2_claim_present;
    uint16_t join_v2_contract_version;
    char join_v2_bundle_hash[APPEARANCE_HASH_HEX_LEN + 1];
    char join_v2_ids_lock_hash[APPEARANCE_HASH_HEX_LEN + 1];
    SvrAuthoritativeAppearanceState appearance;

    // Position & movement (SERVER IS AUTHORITY)
    float pos[3];
    float dir;
    float vel[3];
    float spawn_pos[3];      // stored at first ALIVE transition, used for respawn
    float spawn_terrain_z;   // independent terrain sample at spawn XY (GAP-2)
    float spawn_fallback_z;  // additive diagnostic contract field; current safe-player spawn path uses a fixed 0.0f fallback (GAP-2)
    float in_water;          // last MpStepOnce authoritative in_water ratio (GAP-1)
    float terrain_z;         // authoritative terrain surface height at current pos, updated per tick (GAP-12)
    uint8_t support_valid;   // last MpStepOnce support hit; server_tick may cache/publish but not compute support
    uint8_t support_source;
    float support_z;
    uint16_t support_item_id;
    uint16_t collision_debug_sample_count;
    uint8_t collision_debug_push_source;
    STRUCT_BRC_COLLISION_DEBUG_SAMPLE collision_debug_samples[COLLISION_DEBUG_SAMPLE_MAX];

    uint8_t life_state;
    uint8_t mount_state;
    uint8_t locomotion_state;
    uint8_t combat_state;
    uint16_t presentation_kind_id;
    uint32_t presentation_started_tick;

    // Combat
    int16_t hp;
    int16_t max_hp;
    uint32_t last_swing_tick;
    uint16_t last_swing_presentation_kind_id;
    uint64_t last_swing_stamp_us;  // FL-2481: wall-clock cooldown for rate-limit enforcement
    uint32_t death_tick;       // 0 = alive
    uint32_t respawn_tick;
    uint16_t last_attacker_id;
    float knockback[2];

    // S5/FL-1862: input is a state sample, not an ordered simulation log.
    // The 16-slot input_jitter[] replay queue (FL-1854/1858) and the ordered
    // SvrInputSeqDistanceAfter scan were deleted 2026-04-25 (08e866ae).
    // Do NOT re-add a multi-slot jitter buffer or per-tick queue drain loop.
    // Client movement packets are state samples, not an ordered simulation log.
    InputSlot latest_input;
    float input_force[2];
    float input_force_z;
    float input_yaw;
    uint8_t input_flags;
    uint16_t last_recv_input_seq;
    bool has_recv_input_seq;
    uint16_t last_applied_input_seq;
    uint32_t input_seq_regressions;
    uint32_t input_packets_tick;
    uint8_t input_packets_this_tick;

    // Movement intent intake telemetry (observability only; no behavior ownership).
    // Captures whether 'M' packets (STRUCT_REQ_INPUT_MOVE) were received, latched,
    // and applied by the server.
    uint32_t m_intent_rx_count;
    uint64_t m_intent_last_rx_us;
    uint16_t m_intent_last_rx_seq;
    int8_t m_intent_last_rx_move_x;
    int8_t m_intent_last_rx_move_y;
    int8_t m_intent_last_rx_move_z;
    int16_t m_intent_last_rx_yaw100;
    uint8_t m_intent_last_rx_flags;

    uint32_t m_intent_last_nonzero_seq;
    uint64_t m_intent_last_nonzero_rx_us;
    int8_t m_intent_last_nonzero_move_x;
    int8_t m_intent_last_nonzero_move_y;
    int8_t m_intent_last_nonzero_move_z;

    uint32_t m_intent_latch_accept_count;
    uint64_t m_intent_last_latch_accept_us;
    uint16_t m_intent_last_latch_accept_seq;

    uint32_t m_intent_apply_accept_count;
    uint64_t m_intent_last_apply_accept_us;
    uint16_t m_intent_last_apply_accept_seq;

    uint32_t m_intent_reject_count;
    uint32_t m_intent_last_reject_code;
    uint64_t m_intent_last_reject_us;
    uint16_t m_intent_last_reject_seq;

    uint32_t snapshot_after_m_count;

    // Physics body (server-side, headless)
    Physics* physics;

    // Client sprite request owner deleted; server owns V2 presentation_kind_id.

    // Snapshot ACK ring — Quake 3-style delta gating (P5.2 lifecycle contract):
    //   Baseline sent when !can_send_delta (first join or unacked).
    //   Delta sent only after client ACKs the last sent snapshot.
    //   Tombstones generated for entities leaving interest (out-of-interest convergence).
    //   Invariant: ack_ring/last_sent_* updated ONLY after successful SvrQueueToClient
    //     (backpressure drops leave delta state intact — P5.3).
    SnapshotACK ack_ring[SVR_SNAPSHOT_RING_SIZE];
    uint32_t ack_write;
    uint32_t snapshot_ack_received_count;
    uint32_t snapshot_ack_accepted_count;
    uint16_t last_snapshot_ack_received_seq;
    uint16_t last_snapshot_ack_accepted_seq;
    uint32_t last_snapshot_ack_accepted_tick;
    uint16_t last_acked_seq;
    bool has_acked;              // true after first ACK received (seq=0 safe)
    uint16_t last_sent_snapshot_seq;
    bool has_sent_snapshot_baseline;
    int last_sent_snapshot_entity_count;
    STRUCT_SNAPSHOT_ENTITY last_sent_snapshot_entities[SVR_MAX_CLIENTS + SVR_MAX_NPCS];
    uint32_t snapshot_drops;     // P5.3: backpressure drop counter
    uint8_t sent_player_appearance_valid[SVR_MAX_CLIENTS];
    uint32_t sent_player_appearance_signature[SVR_MAX_CLIENTS];
    uint8_t sent_npc_appearance_valid[SVR_MAX_NPCS];
    uint32_t sent_npc_appearance_signature[SVR_MAX_NPCS];

    // Stats
    uint32_t total_damage_dealt;
    uint32_t total_damage_taken;
    uint32_t rate_limit_violations;

    // FL-2481: rolling-window rate-limit disconnect enforcement
    uint32_t rl_violation_ticks[SVR_RATE_LIMIT_RING_SIZE]; // circular buffer of violation tick stamps
    uint32_t rl_violation_write;                           // next write index into ring
};

// Transition-guarded phase write (U-03). Returns true if transition was valid.
// Only called by tick thread (sole owner of authoritative game state).
// Uses atomic store for defense-in-depth even though tick thread is sole writer.
static inline bool SvrTransitionPhase(SvrPlayerState* ps, ClientPhase target)
{
    if (!PHASE_TRANSITIONS[ps->phase][target]) return false;
    __atomic_store_n((uint8_t*)&ps->phase, (uint8_t)target, __ATOMIC_RELEASE);
    return true;
}

// ─── NPC State (authoritative, tick thread only) ─────────────────
struct SvrNpcState
{
    bool active;
    uint16_t entity_id;       // SVR_MAX_CLIENTS + npc_index
    SvrAuthoritativeAppearanceState appearance;

    // Position & movement
    float pos[3];
    float dir;
    float vel[3];

    uint8_t life_state;
    uint8_t mount_state;
    uint8_t locomotion_state;
    uint8_t combat_state;
    uint16_t presentation_kind_id;
    uint32_t presentation_started_tick;

    // Combat
    int16_t hp;
    int16_t max_hp;
    uint32_t death_tick;
    uint32_t last_swing_tick;
    uint16_t last_swing_presentation_kind_id;

    // Respawn
    float spawn_pos[3];
    int spawn_gen_index;
    uint32_t respawn_delay;

    // AI state
    uint16_t target_id;
    bool target_is_player;
    bool enemy;
    int stuck_counter;
    float unstuck_pos[2][3];
    bool jump_request;
    float intent_force[2];
    float intent_dir;

    // Physics body
    Physics* physics;

    // Client sprite request owner deleted; server owns V2 presentation_kind_id.
};

// ─── Item State (authoritative) ──────────────────────────────────
enum SvrItemGameplayKind : uint8_t
{
    SVR_ITEM_GAMEPLAY_UNKNOWN = 0,
    SVR_ITEM_GAMEPLAY_WEAPON = 1,
    SVR_ITEM_GAMEPLAY_CONSUMABLE = 2,
    SVR_ITEM_GAMEPLAY_LOOT = 3,
    SVR_ITEM_GAMEPLAY_WEARABLE = 4,
    SVR_ITEM_GAMEPLAY_MOUNTABLE = 5,
    SVR_ITEM_GAMEPLAY_PLACEABLE_BLOCK = 6,
};

enum SvrItemSourceKind : uint8_t
{
    SVR_ITEM_SOURCE_UNKNOWN = 0,
    SVR_ITEM_SOURCE_MAP_A3D = 1,
    SVR_ITEM_SOURCE_STARTER_LOADOUT = 2,
    SVR_ITEM_SOURCE_PLAYER_PLACED = 3,
};

enum SvrPlacedItemFlags : uint16_t
{
    SVR_PLACED_ITEM_NONE = 0,
    SVR_PLACED_ITEM_PLACED = 1u << 0,
    SVR_PLACED_ITEM_COLLIDABLE = 1u << 1,
    SVR_PLACED_ITEM_EXPLICIT_PICKUP_ONLY = 1u << 2,
};

struct SvrItemState
{
    bool active;
    uint16_t item_id;

    float pos[3];

    uint16_t owner_id;       // entity ID, 0xFFFF = world
    uint16_t item_definition_id;
    uint16_t visual_style_id;
    uint16_t equip_slot_kind_id;
    uint16_t mount_definition_id;
    uint8_t gameplay_kind;   // copied from the bundle item definition at identity assignment
    uint8_t source_kind;     // SvrItemSourceKind; used by proof gates to distinguish map-authored content
    uint16_t last_drop_owner_id; // owner that most recently dropped this item; 0xFFFF means none/unknown
    uint32_t last_drop_tick;     // server tick of last owner->world drop, used to suppress same-input re-pickup
    uint16_t placed_flags;       // SvrPlacedItemFlags; server-owned placed item/collision/pickup policy
    uint16_t placed_durability;  // Runtime durability for breakable placed blocks
    float placed_yaw;            // Server-computed placed orientation; render may consume later
    uint64_t placed_entity_id;   // Server-owned world-entity component record; no render/collision proxy Inst
};

// ─── World Mutation State ────────────────────────────────────────
struct SvrDecalEvent
{
    uint32_t event_id;
    uint32_t tick;
    float x, y, r;
    uint8_t matid;
};

// ─── Event Broadcast Queue ──────────────────────────────────────
struct SvrEventQueue
{
    uint8_t buf[32768];
    int len;
    struct Entry { int offset; int size; int exclude_client; };
    Entry entries[512];
    int count;
};

// ─── Pending Combat ──────────────────────────────────────────────
struct PendingSwing {
    uint16_t attacker_id;     // entity ID (player < MAX_CLIENTS, NPC >= MAX_CLIENTS)
    uint16_t target_id;       // debug-only explicit target metadata; gameplay swings use 0xFFFF
    uint16_t weapon_item_id;  // Wave 3: catalog item id for behavior lookup (range, projectile, etc.).
    uint8_t explicit_target;  // debug-only targeted swing path
};

#define SVR_MAX_PENDING_SWINGS (SVR_MAX_CLIENTS + SVR_MAX_NPCS)

struct PendingProjectile {
    uint8_t active;
    uint16_t attacker_id;
    uint16_t target_id;
    uint32_t fire_tick;
    uint32_t impact_tick;
    float attacker_pos[3];
    float target_pos[3];
    float dist2;
};

#define SVR_MAX_PENDING_PROJECTILES (SVR_MAX_CLIENTS + SVR_MAX_NPCS)

// ─── Accept Event (accept thread → IO thread SPSC) ──────────────
struct AcceptEvent
{
    TCP_SOCKET socket;
    int slot;
    uint32_t peer_ip;   // IPv4 address (network byte order) for per-IP rate limiting (RQ-103)
};

struct AcceptRing
{
    AcceptEvent events[SVR_ACCEPT_RING_SIZE];
    // SPSC ring: write owned exclusively by accept thread (producer).
    // read owned exclusively by IO thread (consumer).
    // Same lock-free SPSC guarantee as per-client inbound ring above.
    alignas(64) volatile uint32_t write;
    alignas(64) volatile uint32_t read;
};

// ═══════════════════════════════════════════════════════════════════
// SLOT ALLOCATOR (lock-free via CAS)
// ═══════════════════════════════════════════════════════════════════

// Returns slot index [0, max_slots), or -1 if full.
// Uses compare-and-swap on a 64-bit bitmask (SVR_MAX_CLIENTS <= 64).
static inline int atomic_claim_slot(volatile uint64_t* bitmask, int max_slots)
{
    while (true)
    {
        uint64_t old = __atomic_load_n(bitmask, __ATOMIC_ACQUIRE);
        for (int i = 0; i < max_slots; i++)
        {
            if (old & ((uint64_t)1 << i)) continue; // already claimed
            uint64_t next = old | ((uint64_t)1 << i);
            if (__atomic_compare_exchange_n(bitmask, &old, next,
                                             false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
                return i;
            break; // CAS failed, retry from scratch
        }
        // If all bits set, no free slot
        uint64_t full_mask = (max_slots >= 64) ? ~(uint64_t)0 : ((uint64_t)1 << max_slots) - 1;
        if ((old & full_mask) == full_mask) return -1;
    }
}

// Release slot. Only called by tick thread after cleanup.
static inline void atomic_release_slot(volatile uint64_t* bitmask, int slot)
{
    uint64_t bit = (uint64_t)1 << slot;
    __atomic_and_fetch(bitmask, ~bit, __ATOMIC_RELEASE);
}

// ═══════════════════════════════════════════════════════════════════
// TOP-LEVEL SERVER STATE
// ═══════════════════════════════════════════════════════════════════
struct ServerState
{
    // Tick counter
    uint32_t tick;
    uint64_t tick_stamp_us;
    uint64_t accumulated_time_us;

    // Server start timestamp (CLOCK_MONOTONIC microseconds, set once at boot).
    // Used by the /health endpoint to report uptime_seconds.
    uint64_t start_time_us;

    // Slot ownership bitmask (atomic, accept thread claims, tick thread releases)
    alignas(64) volatile uint64_t slot_bitmask;

    // Accept → IO ring (accept thread produces, IO thread consumes)
    AcceptRing accept_ring;

    // IO layer (shared with IO thread via SPSC)
    ClientIO clients[SVR_MAX_CLIENTS];
    int client_count;
    TCP_SOCKET listen_socket;

    // Player state (tick thread exclusive)
    SvrPlayerState players[SVR_MAX_CLIENTS];

    // NPC state (tick thread exclusive)
    SvrNpcState npcs[SVR_MAX_NPCS];
    int npc_count;

    // Item state (tick thread exclusive)
    SvrItemState items[SVR_MAX_ITEMS];
    int item_count;
    ServerWorldEntityRegistry world_entities;
    uint32_t next_item_event_id;
    bool debug_runtime_diagnostics_enabled;
    // Law 7 compliance: fly-mode input is only honoured in debug/dev lanes.
    // Set via ASCIICKER_DEBUG_FLY_MODE env var.  Default false (off in all
    // release/current/candidate lanes).  Do NOT set this in production.
    bool debug_fly_mode_enabled;
    uint32_t authoritative_publish_interval_ticks;
    SvrAppearanceContractState appearance_contract;

    // Decal history (ring buffer for late joiners)
    SvrDecalEvent decal_history[SVR_MAX_DECAL_HISTORY];
    int decal_write_pos;
    uint32_t next_decal_event_id;

    // Event broadcast queue (reset each tick)
    SvrEventQueue events;

    // Pending swings (accumulated during phase 2 + 6, resolved in phase 4)
    PendingSwing pending_swings[SVR_MAX_PENDING_SWINGS];
    int pending_swing_count;
    PendingProjectile pending_projectiles[SVR_MAX_PENDING_PROJECTILES];
    int pending_projectile_count;

    // Authoritative observability counters.
    uint32_t combat_swing_count;
    uint32_t combat_damage_count;
    uint32_t combat_damage_player_to_player_count;
    uint32_t combat_damage_player_to_npc_count;
    uint32_t combat_damage_npc_to_player_count;
    uint32_t combat_damage_npc_to_npc_count;
    uint32_t combat_death_count;
    uint32_t combat_respawn_count;

    // Tick-loop timing observability (FL-530: server lag spike detection).
    // tick_overrun_count: frames where ServerTick() took longer than SVR_TICK_INTERVAL_US.
    // tick_max_elapsed_us: worst-case single-frame elapsed time since server start.
    uint32_t tick_overrun_count;
    uint64_t tick_max_elapsed_us;
    uint32_t tick_phase_overrun_count;
    uint32_t tick_phase_log_count;
    uint32_t tick_last_overrun_tick;
    uint8_t tick_last_overrun_phase_id;
    uint64_t tick_last_overrun_phase_us;
    uint32_t tick_max_phase_tick;
    uint8_t tick_max_phase_id;
    uint64_t tick_max_phase_us;
    uint32_t tick_physics_overrun_count;
    uint32_t tick_physics_log_count;
    uint32_t tick_last_physics_overrun_tick;
    uint8_t tick_last_physics_phase_id;
    uint64_t tick_last_physics_phase_us;
    uint32_t tick_max_physics_phase_tick;
    uint8_t tick_max_physics_phase_id;
    uint64_t tick_max_physics_phase_us;
    uint32_t tick_last_physics_players_active;
    uint32_t tick_last_physics_players_steps;
    uint32_t tick_last_physics_players_idle_fast_paths;
    uint64_t tick_last_physics_players_step_once_us;
    uint64_t tick_last_physics_players_us;
    uint32_t tick_max_physics_players_tick;
    uint32_t tick_max_physics_players_active;
    uint32_t tick_max_physics_players_steps;
    uint32_t tick_max_physics_players_idle_fast_paths;
    uint64_t tick_max_physics_players_step_once_us;
    int32_t tick_max_physics_players_step_once_client;
    uint32_t tick_max_physics_players_step_once_reject_mask;
    uint32_t tick_max_physics_players_step_once_input_flags;
    uint32_t tick_max_physics_players_step_once_grounded;
    uint32_t tick_max_physics_players_step_once_in_water;
    uint32_t tick_max_physics_players_step_once_idle_support_recovered;
    uint32_t tick_max_physics_players_step_once_full_steps;
    uint32_t tick_max_physics_players_step_once_idle_fast_paths;
    uint32_t tick_max_physics_players_step_once_max_abs_vel_milli;
    uint32_t tick_max_physics_players_step_once_yaw_delta_mdeg;
    uint32_t tick_max_physics_players_step_once_support_z_milli;
    uint32_t tick_max_physics_players_step_once_accum_contact_milli;
    uint64_t tick_max_physics_players_step_once_collect_us;
    uint64_t tick_max_physics_players_step_once_sweep_wall_us;
    uint64_t tick_max_physics_players_step_once_support_probe_us;
    uint64_t tick_max_physics_players_step_once_support_retry_probe_us;
    uint64_t tick_max_physics_players_step_once_unaccounted_us;
    uint32_t tick_max_physics_players_step_once_soup_items;
    uint32_t tick_max_physics_players_step_once_sweep_iters;
    uint32_t tick_max_physics_players_step_once_collision_checks;
    uint64_t tick_max_physics_players_us;
    uint32_t tick_snapshot_overrun_count;
    uint32_t tick_snapshot_log_count;
    uint32_t tick_last_snapshot_overrun_tick;
    uint8_t tick_last_snapshot_phase_id;
    uint64_t tick_last_snapshot_phase_us;
    uint32_t tick_max_snapshot_phase_tick;
    uint8_t tick_max_snapshot_phase_id;
    uint64_t tick_max_snapshot_phase_us;
    uint64_t tick_snapshot_authoritative_state_us_last;
    uint64_t tick_snapshot_authoritative_state_us_max;
    uint64_t snapshot_total_us;
    uint64_t snapshot_authoritative_state_us;
    uint64_t auth_phase_unaccounted_us;
    uint64_t auth_collect_us;
    uint64_t auth_diff_us;
    uint64_t auth_serialize_us;
    uint64_t auth_send_or_queue_us;
    uint64_t auth_copy_us;
    uint64_t auth_publish_prepare_us;
    uint64_t auth_socket_lookup_us;
    uint64_t auth_per_client_loop_us;
    uint64_t auth_client_queue_push_us;
    uint64_t auth_client_write_attempt_us;
    uint64_t auth_client_flush_us;
    uint64_t auth_lock_wait_us;
    uint64_t auth_lock_held_us;
    uint64_t auth_primary_file_write_us;
    uint64_t auth_legacy_shm_write_us;
    uint64_t auth_max_client_us;
    uint64_t auth_max_publish_sink_us;
    uint32_t auth_client_queue_bytes;
    uint32_t auth_client_write_bytes;
    uint32_t auth_client_queue_depth_before;
    uint32_t auth_client_queue_depth_after;
    uint32_t auth_client_backpressure_flag;
    uint32_t auth_client_write_result;
    uint32_t auth_max_client_id;
    uint32_t auth_max_client_queue_depth;
    uint32_t auth_clients_count;
    uint8_t auth_max_publish_sink_id;
    uint32_t auth_entries;
    uint32_t auth_bytes;
    uint32_t auth_player_count;
    uint32_t auth_npc_count;
    uint32_t auth_item_count;
    uint32_t auth_publish_tick;
    uint64_t auth_max_entry_us;
    uint8_t auth_max_entry_kind_id;
    uint32_t auth_max_entry_id;
    uint32_t auth_repeated_entry_count;
    uint32_t auth_buffer_size_before;
    uint32_t auth_buffer_size_after;
    uint32_t auth_buffer_reallocs;
    uint32_t io_poll_gap_last_us;
    uint32_t io_poll_gap_max_us;
    uint32_t io_poll_gap_over_100ms_count;
    // Last poll() context (IO thread) to disambiguate vCPU steal vs real work.
    // See FL-2957 / FL-3800: raw RTT spikes can be dominated by poll gaps.
    uint32_t io_poll_nfds_last;
    int32_t io_poll_ret_last;
    int32_t io_poll_timeout_ms_last;
    uint32_t io_poll_work_pending_last;
    int io_wake_read_fd;
    int io_wake_write_fd;
    uint32_t io_wake_write_count;
    uint32_t io_wake_read_count;
    uint32_t io_wake_write_errno_count;

    // Global sequence counter for snapshots
    uint16_t snapshot_seq;

    // World references (set at startup, immutable after)
    Terrain* terrain;
    World* world;

    // ── Per-IP connection rate limiter (RQ-103) ────────────────────
    // Tracks active handshake/connection count per source IPv4 address.
    // Accept thread increments after accept(); tick thread decrements on
    // disconnect. Protected by a CAS spinlock since two threads access it.
    #define SVR_IP_RATE_LIMIT_MAX_ENTRIES SVR_MAX_CLIENTS
    #define SVR_IP_RATE_LIMIT_MAX_CONNS   2   // max simultaneous connections per IP
    #define SVR_PROXY_IP_RATE_LIMIT_MAX_CONNS 4 // trusted nginx path: two game tabs plus proof probes
    struct IpRateEntry { uint32_t ip; int count; };
    IpRateEntry ip_rate_table[SVR_IP_RATE_LIMIT_MAX_ENTRIES];
    int ip_rate_count;
    alignas(64) volatile int ip_rate_lock;   // 0=unlocked, 1=locked (CAS spinlock)
};

// ═══════════════════════════════════════════════════════════════════
// SERVER TICK API (implemented in server_tick.cpp)
// ═══════════════════════════════════════════════════════════════════

// Initialize server state (zero + set defaults)
void SvrStateInit(ServerState* state, Terrain* t, World* w);

// Load and validate the appearance bundle contract once at startup. Bundle updates
// require process restart; runtime hot-reload is intentionally unsupported.
bool SvrLoadStartupAppearanceContract(ServerState* state, char* error, size_t error_cap);

// Spawn NPCs from EnemyGen linked list
void SvrInitNpcs(ServerState* state);

// Load ordinary world items from visible map-authored item instances.
void SvrInitWorldItems(ServerState* state);

// Main tick loop entry (runs on tick thread)
void ServerTickLoop(ServerState* state);

// IO thread entry
void* IOThreadEntry(void* arg);

// Accept thread entry (owns accept + WS handshake)
void* AcceptThreadEntry(void* arg);

// WS frame encoding (framing only, no send)
// Returns total frame bytes written to dst.
int WS_FRAME_ENCODE(uint8_t* dst, const uint8_t* payload, int payload_size, int opcode);

// WebSocket handshake (blocking, called by accept thread)
bool SvrDoWSHandshake(TCP_SOCKET socket, uint32_t peer_ip, uint32_t* rate_limit_ip_out, uint32_t* forwarded_for_ip_out);
