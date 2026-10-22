// engine/render/fl4208_surface_identity.h
//
// FL-4208 N0a — presentation-neutral terrain surface-identity instrumentation.
//
// CONTRACT: docs/agent/2026-06-28-fl4208-native-temporal-prerequisite-phase0.md
//
// This module produces the screen-independent cell identity consumed by the
// separate temporal presentation owner. It aggregates the four depth-winning
// samples into one represented surface, but NOTHING here writes the presented
// glyph. Current-winner selection and temporal presentation retain their own
// declared writer boundaries.
//
// Single ownership (Law 1): an identity producer that never writes the final
// glyph is not a parallel presentation owner.
//
// FL-3955: do not relabel this identity module as a presentation owner.
#pragma once

#include <cstdint>
#include <cstring>
#include <cmath>
#include <cstdio>
#include <cstdlib>

// Owner kind for an Fl4208SurfaceId. N0a is terrain-only; MESH/ACTOR are
// reserved enum slots so the field width is locked now (round-2 finding #4).
enum Fl4208OwnerKind : uint32_t {
    FL4208_OWNER_TERRAIN = 0,
    FL4208_OWNER_MESH    = 1,
    FL4208_OWNER_ACTOR   = 2,
};

// Stable, screen-independent identity of a sampled terrain surface point.
// Six fields, 24 bytes, no padding (8/4/4/4/2/2). NO pointers, NO screen (x,y),
// NO interned index inside — keyed by world generation + global owner cell +
// quantized object-local sub-cell anchor. See contract §1 "Layout decision".
struct Fl4208SurfaceId {
    uint64_t world_generation;  // H4: CreateWorld-owned, monotonic (uint64_t end-to-end)
    uint32_t owner_kind;        // Fl4208OwnerKind
    int32_t  owner_x;           // global terrain visual-cell X (patch + floor(vr_u))
    int32_t  owner_y;           // global terrain visual-cell Y (patch + floor(vr_v))
    uint16_t anchor_u_q;        // object-local sub-cell bin (see Fl4208AnchorQuant)
    uint16_t anchor_v_q;

    bool operator==(const Fl4208SurfaceId& o) const {
        return world_generation == o.world_generation
            && owner_kind == o.owner_kind
            && owner_x == o.owner_x && owner_y == o.owner_y
            && anchor_u_q == o.anchor_u_q && anchor_v_q == o.anchor_v_q;
    }
};
static_assert(sizeof(Fl4208SurfaceId) == 24, "Fl4208SurfaceId must be 24 bytes (no padding)");

// Anchor quantization (contract §1 "Anchor quantization spec"): the object-local
// sub-cell coordinate local_uv in [0,1) is quantized onto a regular ANCHOR_BINS
// grid. clamp guarantees no overflow past ANCHOR_BINS-1; exactly local==1.0 folds
// into the top bin. ONLY local_(u,v) (fract of vr) ever enters here — never the
// raw non-normalized vr (round-3 finding #5).
static inline uint16_t Fl4208AnchorQuant(float local_uv, uint32_t anchor_bins) {
    float c = local_uv;
    if (c < 0.0f) c = 0.0f;
    if (c > 0.9999990f) c = 0.9999990f;
    return (uint16_t)floorf(c * (float)anchor_bins);
}

// ---------------------------------------------------------------------------
// Identity interner.
//
// Cell-level interning happens AFTER four-sample ownership aggregation. Terrain
// contributes at most one represented identity per terminal cell. Mesh follows
// its separate cell-level depth selection before using this same interner. The
// original sample-grid bound remains a conservative fixed capacity so the
// receipt ABI and fail-closed overflow behavior do not change in this repair.
// Capacity vs slot count are distinct (round-4 finding #5):
//   - record admission limit = 57600 live identities (index 0 reserved INVALID)
//   - open-addressing slot array = 131072 (2^17) slots -> worst-case load ~44%
//     and a single-mask index (h & 0x1FFFF) instead of a modulo.
// A slot hit is confirmed by FULL-RECORD equality before reuse (never a
// truncated-hash match). Overflow is deterministic and fail-closed (Law 6):
// a new distinct identity past the limit returns INVALID and is excluded from
// history; a live entry is NEVER evicted.
// ---------------------------------------------------------------------------
struct Fl4208IdentityInterner {
    static const char* HashVersion() {
        return "fl4208.surface_id_hash.fnv1a64.packed24.v1";
    }
    static const uint32_t SLOT_COUNT    = 131072;        // 2^17
    static const uint32_t SLOT_MASK     = SLOT_COUNT - 1; // 0x1FFFF
    static const uint32_t RECORD_LIMIT  = 57600;          // live-record admission cap
    static const uint32_t INVALID_INDEX = 0;              // reserved INVALID index

    // records[0] is the reserved INVALID slot; live records occupy [1..RECORD_LIMIT].
    Fl4208SurfaceId records[RECORD_LIMIT + 1];
    uint32_t        slots[SLOT_COUNT];   // slot -> record index (0 = empty)
    uint32_t        record_count;        // live records (excludes reserved index 0)
    bool            overflowed;          // sticky: a distinct identity was dropped this frame

    // FNV-1a over the 24 packed bytes (no padding, so byte hashing is stable).
    static uint64_t Hash(const Fl4208SurfaceId& id) {
        const unsigned char* p = (const unsigned char*)&id;
        uint64_t h = 1469598103934665603ULL;
        for (size_t i = 0; i < sizeof(Fl4208SurfaceId); i++) {
            h ^= p[i];
            h *= 1099511628211ULL;
        }
        return h;
    }

    // Clear slot occupancy and record count for a new frame. records[] storage is
    // not memset (overwritten on admission); only the INVALID record is pinned.
    void BeginFrame() {
        memset(slots, 0, sizeof(slots));
        record_count = 0;
        overflowed = false;
        memset(&records[INVALID_INDEX], 0, sizeof(Fl4208SurfaceId));
    }

    // Intern an identity. Returns a stable per-frame index in [1..RECORD_LIMIT],
    // or INVALID_INDEX (0) on fail-closed overflow.
    uint32_t Intern(const Fl4208SurfaceId& id) {
        uint32_t slot = (uint32_t)(Hash(id) & SLOT_MASK);
        for (;;) {
            uint32_t rec = slots[slot];
            if (rec == INVALID_INDEX) {
                if (record_count >= RECORD_LIMIT) { overflowed = true; return INVALID_INDEX; }
                uint32_t idx = ++record_count;     // [1..RECORD_LIMIT]
                records[idx] = id;
                slots[slot] = idx;
                return idx;
            }
            if (records[rec] == id) return rec;    // full-record compare confirms reuse
            slot = (slot + 1) & SLOT_MASK;          // linear probe
        }
    }
};

// ---------------------------------------------------------------------------
// Deterministic four-sample aggregation (contract §1; round-2 finding #2,
// corrected after the FL-4208 zero-identity audit).
//
// Surface ownership is decided before the local anchor. Four supersamples of
// one represented terrain surface normally carry different local anchors, so
// requiring exact equality of the complete 24-byte records rejects the normal
// case. The stable ownership key is world generation + owner kind + owner
// coordinates. A strict majority of that key selects the represented surface;
// its cell anchor is the component-wise rounded mean of the contributing
// already-quantized local anchors. This produces one deterministic cell-level
// SurfaceId without inventing a screen-coordinate identity.
//
// Exhaustive partition of every multiset of 4 ownership keys:
//   4/0 or 3/1                  -> represented surface wins
//   2/1/1 or 2/2 or 1/1/1/1     -> invalid (excluded from history, NEVER guessed)
// A strict majority is >2 of 4, so a leading pair of 2 is NOT a majority.
// Material disagreement is rejected by Fl4208DecideCell before this function.
// Fail-closed (Law 6): ambiguous ownership must not silently anchor history.
// ---------------------------------------------------------------------------
struct Fl4208AggResult {
    bool            valid;
    Fl4208SurfaceId id;
};

static inline bool Fl4208SameOwnedSurface(const Fl4208SurfaceId& a,
                                          const Fl4208SurfaceId& b) {
    return a.world_generation == b.world_generation
        && a.owner_kind == b.owner_kind
        && a.owner_x == b.owner_x
        && a.owner_y == b.owner_y;
}

static inline Fl4208AggResult Fl4208AggregateCell(const Fl4208SurfaceId ids[4], const bool valid[4]) {
    Fl4208AggResult out;
    out.valid = false;
    memset(&out.id, 0, sizeof(out.id));
    for (int i = 0; i < 4; i++) {
        if (!valid[i]) continue;
        int count = 0;
        uint32_t anchor_u_sum = 0;
        uint32_t anchor_v_sum = 0;
        for (int j = 0; j < 4; j++)
            if (valid[j] && Fl4208SameOwnedSurface(ids[j], ids[i])) {
                count++;
                anchor_u_sum += ids[j].anchor_u_q;
                anchor_v_sum += ids[j].anchor_v_q;
            }
        if (count >= 3) {
            out.valid = true;
            out.id = ids[i];
            out.id.anchor_u_q = (uint16_t)((anchor_u_sum + (uint32_t)(count / 2)) /
                                            (uint32_t)count);
            out.id.anchor_v_q = (uint16_t)((anchor_v_sum + (uint32_t)(count / 2)) /
                                            (uint32_t)count);
            return out;
        }
    }
    return out;
}

// ---------------------------------------------------------------------------
// SHA-256 (FIPS 180-4), self-contained static-inline — the fl4208 header has no
// engine dependencies, so it carries its own digest like it carries its own FNV
// interner hash. This is the round-5 finding #1 SCAFFOLD: capture provenance is a
// 32-byte SHA-256 digest slot (format v3), the correct algorithm and width for a
// paper-grade provenance field. The INPUT hashed below is STILL the
// FL4208_CAPTURE_SPEC env string — the N0b capture orchestrator (other lane) must
// replace that input with the executed camera-pose / input trajectory before a
// freeze. Only the input source is a placeholder; the digest is a real SHA-256
// (cross-checked against python hashlib in the round-trip tests). The engine
// already has an equivalent self-contained impl in glyph_manifest.cpp; this is a
// header-local copy because that one is file-local static (not reusable here).
// ---------------------------------------------------------------------------
struct Fl4208Sha256 {
    uint32_t h[8];
    uint64_t len;       // total bytes consumed
    uint8_t  buf[64];
    uint32_t buflen;
};
static inline uint32_t Fl4208Sha256Rotr(uint32_t x, uint32_t n) { return (x >> n) | (x << (32 - n)); }
static inline void Fl4208Sha256Init(Fl4208Sha256* c) {
    static const uint32_t iv[8] = { 0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                                    0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u };
    for (int i = 0; i < 8; i++) c->h[i] = iv[i];
    c->len = 0; c->buflen = 0;
}
static inline void Fl4208Sha256Block(Fl4208Sha256* c, const uint8_t* p) {
    static const uint32_t k[64] = {
        0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
        0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
        0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
        0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
        0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
        0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
        0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
        0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u };
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
        w[i] = ((uint32_t)p[i*4] << 24) | ((uint32_t)p[i*4+1] << 16) |
               ((uint32_t)p[i*4+2] << 8) | (uint32_t)p[i*4+3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = Fl4208Sha256Rotr(w[i-15],7) ^ Fl4208Sha256Rotr(w[i-15],18) ^ (w[i-15] >> 3);
        uint32_t s1 = Fl4208Sha256Rotr(w[i-2],17) ^ Fl4208Sha256Rotr(w[i-2],19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint32_t a=c->h[0],b=c->h[1],cc=c->h[2],d=c->h[3],e=c->h[4],f=c->h[5],g=c->h[6],hh=c->h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t S1 = Fl4208Sha256Rotr(e,6) ^ Fl4208Sha256Rotr(e,11) ^ Fl4208Sha256Rotr(e,25);
        uint32_t ch = (e & f) ^ ((~e) & g);
        uint32_t t1 = hh + S1 + ch + k[i] + w[i];
        uint32_t S0 = Fl4208Sha256Rotr(a,2) ^ Fl4208Sha256Rotr(a,13) ^ Fl4208Sha256Rotr(a,22);
        uint32_t maj = (a & b) ^ (a & cc) ^ (b & cc);
        uint32_t t2 = S0 + maj;
        hh=g; g=f; f=e; e=d+t1; d=cc; cc=b; b=a; a=t1+t2;
    }
    c->h[0]+=a; c->h[1]+=b; c->h[2]+=cc; c->h[3]+=d; c->h[4]+=e; c->h[5]+=f; c->h[6]+=g; c->h[7]+=hh;
}
static inline void Fl4208Sha256Update(Fl4208Sha256* c, const uint8_t* p, size_t n) {
    c->len += n;
    while (n) {
        uint32_t take = 64 - c->buflen;
        if ((size_t)take > n) take = (uint32_t)n;
        memcpy(c->buf + c->buflen, p, take);
        c->buflen += take; p += take; n -= take;
        if (c->buflen == 64) { Fl4208Sha256Block(c, c->buf); c->buflen = 0; }
    }
}
static inline void Fl4208Sha256Final(Fl4208Sha256* c, uint8_t out[32]) {
    uint64_t bits = c->len * 8;
    uint8_t one = 0x80; Fl4208Sha256Update(c, &one, 1);
    uint8_t zero = 0;
    while (c->buflen != 56) Fl4208Sha256Update(c, &zero, 1);
    uint8_t lenbe[8];
    for (int i = 0; i < 8; i++) lenbe[i] = (uint8_t)(bits >> (56 - 8*i));
    Fl4208Sha256Update(c, lenbe, 8);
    for (int i = 0; i < 8; i++) {
        out[i*4]   = (uint8_t)(c->h[i] >> 24);
        out[i*4+1] = (uint8_t)(c->h[i] >> 16);
        out[i*4+2] = (uint8_t)(c->h[i] >> 8);
        out[i*4+3] = (uint8_t)(c->h[i]);
    }
}
static inline void Fl4208Sha256Bytes(const void* data, size_t n, uint8_t out[32]) {
    Fl4208Sha256 c; Fl4208Sha256Init(&c);
    Fl4208Sha256Update(&c, (const uint8_t*)data, n);
    Fl4208Sha256Final(&c, out);
}

// True iff a 32-byte digest is not all zero (i.e. provenance was actually set).
static inline bool Fl4208Sha256NonZero(const uint8_t d[32]) {
    for (int i = 0; i < 32; i++) if (d[i]) return true;
    return false;
}

// FL-4512/U6 mesh_surface_id.v1. The SHA input is domain bytes followed by
// explicit little-endian tuple fields. SHA-256 digests are conventionally
// written as one big-endian integer, so low64 is digest bytes 24..31.
static const uint32_t FL4208_MESH_ANCHOR_BINS_V1 = 64;
static inline uint64_t Fl4208MeshOwnerHashV1(uint64_t mesh_instance_uid,
                                             uint32_t primitive_id) {
    static const char domain[] = "mesh_owner.v1";
    uint8_t tuple[12];
    for (int i = 0; i < 8; i++) tuple[i] = (uint8_t)(mesh_instance_uid >> (8 * i));
    for (int i = 0; i < 4; i++) tuple[8 + i] = (uint8_t)(primitive_id >> (8 * i));
    Fl4208Sha256 ctx;
    uint8_t digest[32];
    Fl4208Sha256Init(&ctx);
    Fl4208Sha256Update(&ctx, (const uint8_t*)domain, sizeof(domain) - 1);
    Fl4208Sha256Update(&ctx, tuple, sizeof(tuple));
    Fl4208Sha256Final(&ctx, digest);
    uint64_t out = 0;
    for (int i = 24; i < 32; i++) out = (out << 8) | digest[i];
    return out;
}

static inline uint16_t Fl4208MeshAnchorQuantV1(float x) {
    float c = x;
    if (c < 0.0f) c = 0.0f;
    if (c > 1.0f) c = 1.0f;
    uint32_t q = (uint32_t)floorf(c * (float)FL4208_MESH_ANCHOR_BINS_V1);
    if (q >= FL4208_MESH_ANCHOR_BINS_V1) q = FL4208_MESH_ANCHOR_BINS_V1 - 1;
    return (uint16_t)q;
}

static inline uint64_t Fl4208OwnerHashFromId(const Fl4208SurfaceId& id) {
    return (uint64_t)(uint32_t)id.owner_x |
           ((uint64_t)(uint32_t)id.owner_y << 32);
}

// ---------------------------------------------------------------------------
// Runtime config (N0b): frozen ANCHOR_BINS + identity tracing.
// N0b local native calibration receipt
// docs/research/ascii/verification/fl4208/2026-06-29-n0b-y8-bins-sweep/
// selected 4. The former FL4208_ANCHOR_BINS runtime selector is intentionally
// hard-disabled after that sweep so production captures cannot silently drift.
// ---------------------------------------------------------------------------
static const uint32_t FL4208_FROZEN_ANCHOR_BINS = 4;

struct Fl4208Config {
    uint32_t anchor_bins;            // frozen N0b value; no runtime selector
    bool     trace_enabled;          // identity tracing toggle
    bool     trace_path_set;         // FL4208_TRACE_PATH provided -> export per-frame receipts
    char     trace_path[512];        // receipt file (frames appended), empty unless set
    uint8_t  capture_spec_sha256[32];// SHA-256 of capture provenance (all-zero if unset)
    bool     capture_spec_set;       // FL4208_CAPTURE_SPEC provided
};

inline Fl4208Config g_fl4208_config = { FL4208_FROZEN_ANCHOR_BINS, false, false, {0}, {0}, false };

// Read proof/test-only trace controls from the environment. ANCHOR_BINS is not
// read here after N0b freeze; only observational tracing/provenance remains.
static inline void Fl4208ConfigInitFromEnv() {
    const char* b = getenv("FL4208_ANCHOR_BINS");
    if (b && b[0])
        fprintf(stderr, "[FL-4208] FL4208_ANCHOR_BINS is frozen at %u; ignoring env value %s\n",
                FL4208_FROZEN_ANCHOR_BINS, b);
    g_fl4208_config.anchor_bins = FL4208_FROZEN_ANCHOR_BINS;
    const char* t = getenv("FL4208_TRACE");
    if (t)
        g_fl4208_config.trace_enabled = atoi(t) != 0;
    // FL-4208 N0a correction (reviewer flag, round 2): receipt export path so the
    // N0b sweep can compare runs from disk. Enabling a path implies tracing.
    const char* p = getenv("FL4208_TRACE_PATH");
    if (p && p[0]) {
        size_t n = strlen(p);
        if (n >= sizeof(g_fl4208_config.trace_path)) {
            fprintf(stderr, "[FL-4208] FL4208_TRACE_PATH too long (%zu >= %zu); export disabled\n",
                    n, sizeof(g_fl4208_config.trace_path));
        } else {
            memcpy(g_fl4208_config.trace_path, p, n + 1);
            g_fl4208_config.trace_path_set = true;
            g_fl4208_config.trace_enabled = true;
        }
    }
    // round-5 finding #1 SCAFFOLD: capture-spec provenance is now a SHA-256 digest
    // (was FNV-1a 64). The N0b orchestrator sets FL4208_CAPTURE_SPEC identically
    // across the {4,8,16,32} sweep (and a DIFFERENT value for the held-out path);
    // the analyzer only compares receipts whose digest matches. The INPUT hashed
    // here is still the env string — the orchestrator (other lane) must feed the
    // executed camera-pose / input trajectory as the SHA-256 input before a freeze.
    const char* cs = getenv("FL4208_CAPTURE_SPEC");
    if (cs && cs[0]) {
        Fl4208Sha256Bytes(cs, strlen(cs), g_fl4208_config.capture_spec_sha256);
        g_fl4208_config.capture_spec_set = true;
    }
}

// ---------------------------------------------------------------------------
// Provisional identity plane (reviewer option-3 "post-raster, pre-resolve").
//
// Per-fragment interning in BlendVR would admit transient depth-winners and
// over-admit past 57600 -> false overflow before the final winners are known.
// Instead, BlendVR writes a compact PROVISIONAL identity per Sample (terrain
// coords + anchor + material fingerprint + valid flag); a bounded post-raster
// pass then interns only the FINAL surviving samples exactly once. This is NOT
// a second hash table — it is a flat parallel array sized to the Sample buffer.
// ---------------------------------------------------------------------------
// FL-4208 N0a correction (reviewer flag #2): the conflict fingerprint must be
// the RESOLVED terrain material_id, not the full 16-bit packed visual word. The
// engine-wide convention is material_id = visual & 0x00FF (render_scene.cpp:81,
// render_resolve.cpp:383-386, render_stage_shadow.cpp:57). Comparing the full
// word would falsely reject a cell whose 2x2 samples share a material but differ
// only in cosmetic high bits (e.g. the 0x8000 height-raise bit).
static inline uint16_t Fl4208ResolvedMaterialId(uint16_t visual_word) {
    return (uint16_t)(visual_word & 0x00FF);
}

// FL-4652 visibility-sheet identity.
//
// A mesh can reach the same hidden sample lattice through the ordinary world,
// the planar-reflection submission, and either parity of either S3 image.
// Instance plus primitive identity therefore does not identify one visible
// submission.  This enum is the renderer-owned discriminator stamped by the
// depth winner and copied unchanged to projected features.  INVALID is a
// fail-closed value; it is never treated as the direct sheet.
enum RenderVisibilitySheetV1 {
    RENDER_VISIBILITY_SHEET_INVALID = 0,
    RENDER_VISIBILITY_SHEET_EUCLIDEAN_DIRECT = 1,
    RENDER_VISIBILITY_SHEET_EUCLIDEAN_PLANAR_REFLECTION = 2,
    RENDER_VISIBILITY_SHEET_S3_IMAGE0_PARITY0 = 3,
    RENDER_VISIBILITY_SHEET_S3_IMAGE0_PARITY1 = 4,
    RENDER_VISIBILITY_SHEET_S3_IMAGE1_PARITY0 = 5,
    RENDER_VISIBILITY_SHEET_S3_IMAGE1_PARITY1 = 6
};

// A rejected mesh submission records one exact reason instead of silently
// representing missing current-invocation parity as parity zero.  The renderer
// keeps the latest reason plus a per-frame count so later observation work can
// report the failure without becoming another admission owner.
enum RenderVisibilitySheetFailureV1 {
    RENDER_VISIBILITY_SHEET_FAILURE_NONE = 0,
    RENDER_VISIBILITY_SHEET_FAILURE_S3_PROJECTION_FAILED = 1,
    RENDER_VISIBILITY_SHEET_FAILURE_S3_PARITY_UNAVAILABLE = 2,
    RENDER_VISIBILITY_SHEET_FAILURE_INVALID_S3_IMAGE = 3,
    RENDER_VISIBILITY_SHEET_FAILURE_INVALID_REFLECTION_COMBINATION = 4,
    // FL-4652: RenderS3Setup uses state 2 when curvature was
    // published but could not produce an authoritative spherical camera.
    // That state is distinct from ordinary Euclidean state 0. Naming it here
    // prevents a failed spherical setup from silently acquiring a valid
    // Euclidean sheet and publishing mesh identity.
    RENDER_VISIBILITY_SHEET_FAILURE_INVALID_S3_STATE = 5
};

// RenderS3Setup publishes three states, not a boolean. The visibility-sheet
// owner must preserve that distinction because OFF means a legitimate
// Euclidean frame while INVALID_PUBLISHED means required curvature authority
// was present but unusable. These values intentionally match Renderer::s3_active.
enum RenderS3SubmissionStateV1 {
    RENDER_S3_SUBMISSION_OFF = 0,
    RENDER_S3_SUBMISSION_ACTIVE = 1,
    RENDER_S3_SUBMISSION_INVALID_PUBLISHED = 2
};

// Convert one fully described mesh invocation into its immutable sheet.
//
// Parameters:
//   s3_state        - exact RenderS3Setup tri-state; only 0, 1, and 2 are legal.
//   s3_image        - current spherical image index; only zero and one are legal.
//   parity_valid    - proves parity was derived for this invocation.
//   parity          - zero for the near fold, one for the far fold.
//   reflection_mode - true only for the Euclidean planar-reflection pass.
//   failure         - receives the exact fail-closed reason when no sheet exists.
//
// The helper is pure so native tests can exercise every mapping without
// launching the renderer.  Callers must still reject before QueryMesh when it
// returns INVALID.
static inline uint8_t RenderVisibilitySheetForSubmissionV1(
    int s3_state, int s3_image, bool parity_valid, int parity,
    bool reflection_mode, RenderVisibilitySheetFailureV1* failure)
{
    if (failure)
        *failure = RENDER_VISIBILITY_SHEET_FAILURE_NONE;
    if (s3_state == RENDER_S3_SUBMISSION_INVALID_PUBLISHED ||
        (s3_state != RENDER_S3_SUBMISSION_OFF &&
         s3_state != RENDER_S3_SUBMISSION_ACTIVE))
    {
        if (failure)
            *failure = RENDER_VISIBILITY_SHEET_FAILURE_INVALID_S3_STATE;
        return RENDER_VISIBILITY_SHEET_INVALID;
    }
    if (s3_state == RENDER_S3_SUBMISSION_OFF)
        return reflection_mode
            ? RENDER_VISIBILITY_SHEET_EUCLIDEAN_PLANAR_REFLECTION
            : RENDER_VISIBILITY_SHEET_EUCLIDEAN_DIRECT;
    if (reflection_mode)
    {
        if (failure)
            *failure = RENDER_VISIBILITY_SHEET_FAILURE_INVALID_REFLECTION_COMBINATION;
        return RENDER_VISIBILITY_SHEET_INVALID;
    }
    if (s3_image != 0 && s3_image != 1)
    {
        if (failure)
            *failure = RENDER_VISIBILITY_SHEET_FAILURE_INVALID_S3_IMAGE;
        return RENDER_VISIBILITY_SHEET_INVALID;
    }
    if (!parity_valid)
    {
        if (failure)
            *failure = RENDER_VISIBILITY_SHEET_FAILURE_S3_PARITY_UNAVAILABLE;
        return RENDER_VISIBILITY_SHEET_INVALID;
    }
    if (s3_image == 0)
        return parity
            ? RENDER_VISIBILITY_SHEET_S3_IMAGE0_PARITY1
            : RENDER_VISIBILITY_SHEET_S3_IMAGE0_PARITY0;
    return parity
        ? RENDER_VISIBILITY_SHEET_S3_IMAGE1_PARITY1
        : RENDER_VISIBILITY_SHEET_S3_IMAGE1_PARITY0;
}

struct Fl4208ProvisionalId {
    int32_t  owner_x;
    int32_t  owner_y;
    uint64_t world_generation;
    uint64_t mesh_instance_uid; // complete tuple retained for hash-collision rejection
    uint32_t primitive_id;      // canonical asset-topology identity
    uint16_t anchor_u_q;
    uint16_t anchor_v_q;
    uint16_t material;   // revalidation/agreement field; never part of SurfaceId
    uint8_t  owner_kind; // Fl4208OwnerKind
    uint8_t  visibility_sheet; // RenderVisibilitySheetV1 for this exact depth win
    uint8_t  valid;      // 1 iff the final depth-owning fragment wrote this sample
};

static inline Fl4208SurfaceId Fl4208BuildIdFromProvisional(const Fl4208ProvisionalId& p) {
    Fl4208SurfaceId id;
    id.world_generation = p.world_generation;
    id.owner_kind = p.owner_kind;
    id.owner_x = p.owner_x;
    id.owner_y = p.owner_y;
    id.anchor_u_q = p.anchor_u_q;
    id.anchor_v_q = p.anchor_v_q;
    return id;
}

// Per-frame trace stats (only meaningful when g_fl4208_config.trace_enabled).
struct Fl4208FrameTrace {
    uint32_t cells_valid;       // terminal cells with a valid aggregated identity
    uint32_t cells_rejected;    // cells rejected for dispatch/material conflict
    uint32_t records;           // distinct interned identities this frame
    uint32_t mesh_owner_hash_collision_cells;
    uint32_t mesh_anchor_collision_cells;
    uint32_t terrain_anchor_collision_cells;
    uint8_t  overflow;          // interner hit the 57600 record limit
};

// ---------------------------------------------------------------------------
// Versioned trace receipt (reviewer flag, round 2). The per-cell capture lives
// only in Renderer memory; N0b must compare runs from disk, so this is the
// single documented on-disk format. A receipt with status != OK is a
// FAIL-CLOSED marker (capture allocation failed) and must NOT be read as an
// empty terrain frame (reviewer flag #3). Little-endian layout:
//   8B  magic "FL4208TR"
//   u32 version, u32 status, u64 frame, u64 stamp,
//   u32 width, u32 height, u32 terrain_anchor_bins, u32 mesh_anchor_bins,
//   u32 cell_count,
//   u32 cells_valid, u32 cells_rejected, u32 records, u32 overflow,
//   u32 mesh_owner_hash_collision_cells, u32 mesh_anchor_collision_cells,
//   u32 terrain_anchor_collision_cells,
//   u8[32] capture_spec_sha256  (provenance — receipts are only comparable
//                           when this matches; SHA-256, round-5 finding #1.
//                           The hashed INPUT is a scaffold (env string) until
//                           the orchestrator feeds the executed trajectory.)
//   then cell_count records of { u8 state; u8 reason;
//                                Fl4208SurfaceId id (24B raw) }
//   in row-major order (cell index = y*width + x). Fl4208SurfaceId field
//   order: u64 world_generation, u32 owner_kind, i32 owner_x, i32 owner_y,
//   u16 anchor_u_q, u16 anchor_v_q (size locked to 24 by static_assert).
// ---------------------------------------------------------------------------
static const char     FL4208_TRACE_MAGIC[8] = { 'F','L','4','2','0','8','T','R' };
static const uint32_t FL4208_TRACE_VERSION  = 5;  // v5: typed cross-cell terrain-anchor collisions
enum Fl4208TraceStatus { FL4208_TRACE_OK = 0, FL4208_TRACE_INVALID = 1 };

enum Fl4208CellIdentityReason : uint8_t {
    FL4208_CELL_ID_REASON_NONE = 0,
    FL4208_CELL_ID_REASON_TERRAIN_MATERIAL_CONFLICT = 1,
    FL4208_CELL_ID_REASON_AGGREGATION_AMBIGUOUS = 2,
    FL4208_CELL_ID_REASON_MESH_OWNER_HASH_COLLISION = 3,
    FL4208_CELL_ID_REASON_MESH_ANCHOR_COLLISION = 4,
    // FL-4208 residual burn-down (2026-07-16): a cell whose four supersamples
    // carry NO terrain identity candidate at all (sky, water, non-terrain
    // winners) is NOT "ambiguous" — there is nothing to aggregate. Before this
    // reason existed such cells were folded into AGGREGATION_AMBIGUOUS, which
    // conflated "no terrain here" with "terrain ownership genuinely split" and
    // made the residual look owner-less (measured: 702 of 5,830 "ambiguous"
    // cells on the 128x72 static frame were sky).
    FL4208_CELL_ID_REASON_NO_TERRAIN_SAMPLE = 5,
    // A complete terrain SurfaceId is a temporal key and therefore must name
    // at most one visible cell in a frame. Quantization collisions reject every
    // member; increasing bins here would silently change the identity policy.
    FL4208_CELL_ID_REASON_TERRAIN_ANCHOR_COLLISION = 6,
};

// Reviewer flag #5 (round 3): the format is EXPLICIT little-endian regardless of
// host byte order — fields are serialized byte-by-byte, not dumped as raw native
// structs, so the receipt is portable and the python reader's "<" layout is
// always correct (on an LE host these helpers reproduce the identical bytes).
static inline void Fl4208PutLE16(uint8_t* b, uint16_t v) { b[0]=(uint8_t)v; b[1]=(uint8_t)(v>>8); }
static inline void Fl4208PutLE32(uint8_t* b, uint32_t v) { for (int i=0;i<4;i++) b[i]=(uint8_t)(v>>(8*i)); }
static inline void Fl4208PutLE64(uint8_t* b, uint64_t v) { for (int i=0;i<8;i++) b[i]=(uint8_t)(v>>(8*i)); }

// Write one frame receipt to an already-open binary stream. When status !=
// OK (or buffers are null), cell_count is forced to 0 so the receipt is a pure
// fail-closed marker. Returns cells written (>=0), or -1 on any write error.
static inline long Fl4208WriteTraceFrame(FILE* f, uint32_t status,
                                         uint64_t frame, uint64_t stamp,
                                         uint32_t width, uint32_t height,
                                         uint32_t terrain_anchor_bins,
                                         uint32_t mesh_anchor_bins,
                                         const Fl4208FrameTrace& tr,
                                         const Fl4208SurfaceId* cell_ids,
                                         const uint8_t* cell_state,
                                         const uint8_t* cell_reason,
                                         uint32_t cell_count,
                                         const uint8_t* capture_spec_sha256) {
    if (!f) return -1;
    if (status != FL4208_TRACE_OK || !cell_ids || !cell_state || !cell_reason)
        cell_count = 0;
    uint8_t hdr[112];
    memcpy(hdr, FL4208_TRACE_MAGIC, 8);
    Fl4208PutLE32(hdr + 8,  FL4208_TRACE_VERSION);
    Fl4208PutLE32(hdr + 12, status);
    Fl4208PutLE64(hdr + 16, frame);
    Fl4208PutLE64(hdr + 24, stamp);
    Fl4208PutLE32(hdr + 32, width);
    Fl4208PutLE32(hdr + 36, height);
    Fl4208PutLE32(hdr + 40, terrain_anchor_bins);
    Fl4208PutLE32(hdr + 44, mesh_anchor_bins);
    Fl4208PutLE32(hdr + 48, cell_count);
    Fl4208PutLE32(hdr + 52, tr.cells_valid);
    Fl4208PutLE32(hdr + 56, tr.cells_rejected);
    Fl4208PutLE32(hdr + 60, tr.records);
    Fl4208PutLE32(hdr + 64, tr.overflow ? 1u : 0u);
    Fl4208PutLE32(hdr + 68, tr.mesh_owner_hash_collision_cells);
    Fl4208PutLE32(hdr + 72, tr.mesh_anchor_collision_cells);
    Fl4208PutLE32(hdr + 76, tr.terrain_anchor_collision_cells);
    if (capture_spec_sha256) memcpy(hdr + 80, capture_spec_sha256, 32);
    else                     memset(hdr + 80, 0, 32);
    if (fwrite(hdr, 1, 112, f) != 112) return -1;
    for (uint32_t i = 0; i < cell_count; i++) {
        uint8_t rec[26];
        rec[0] = cell_state[i];
        rec[1] = cell_reason[i];
        Fl4208PutLE64(rec + 2,  cell_ids[i].world_generation);
        Fl4208PutLE32(rec + 10, cell_ids[i].owner_kind);
        Fl4208PutLE32(rec + 14, (uint32_t)cell_ids[i].owner_x);
        Fl4208PutLE32(rec + 18, (uint32_t)cell_ids[i].owner_y);
        Fl4208PutLE16(rec + 22, cell_ids[i].anchor_u_q);
        Fl4208PutLE16(rec + 24, cell_ids[i].anchor_v_q);
        if (fwrite(rec, 1, 26, f) != 26) return -1;
    }
    return (long)cell_count;
}

// ---------------------------------------------------------------------------
// Post-raster per-cell decision (the aggregation CALLER — reviewer finding #4).
// Fl4208AggregateCell takes only identities+validity; the caller here is
// responsible for (a) demoting a sample whose FINAL winner is no longer terrain
// (mesh overdrew it), and (b) rejecting the whole cell BEFORE aggregation when
// the surviving terrain samples disagree on material. terrain_dispatch is the
// GLYPH_DISPATCH_TERRAIN_MATERIAL value (passed in so this header needs no
// engine include). It interns exactly one represented cell identity; index[i]=0
// for samples outside the selected surface and for rejected cells.
// ---------------------------------------------------------------------------
struct Fl4208CellInput {
    Fl4208ProvisionalId prov[4];
    uint8_t             final_dispatch[4];   // FINAL Sample.glyph_dispatch_surface
    // Native sample depth (Sample.height), one per supersample. Used ONLY by
    // the deterministic depth-winner fallback for cells whose four taps span
    // more than one terrain quad (no strict owner majority). Same field the
    // mesh cell rule already uses for its canonical tap selection.
    float               depth[4];
};
struct Fl4208CellDecision {
    bool            cell_valid;
    bool            rejected_conflict;
    uint8_t         reason;
    uint8_t         selected_tap;
    Fl4208SurfaceId id;
    uint32_t        index[4];
};

static inline Fl4208CellDecision Fl4208DecideCell(const Fl4208CellInput& in,
                                                  uint8_t terrain_dispatch,
                                                  Fl4208IdentityInterner& interner) {
    Fl4208CellDecision d;
    d.cell_valid = false;
    d.rejected_conflict = false;
    d.reason = FL4208_CELL_ID_REASON_NONE;
    d.selected_tap = 0xFF;
    memset(&d.id, 0, sizeof(d.id));
    for (int i = 0; i < 4; i++) d.index[i] = Fl4208IdentityInterner::INVALID_INDEX;

    bool valid[4];
    Fl4208SurfaceId ids[4];
    int  first_mat = -1;
    bool mat_conflict = false;
    for (int i = 0; i < 4; i++) {
        // A sample is a terrain identity candidate iff it was provisionally
        // written AND its FINAL winner is still terrain (not overdrawn by mesh).
        bool is_terrain = in.prov[i].valid && in.final_dispatch[i] == terrain_dispatch;
        valid[i] = is_terrain;
        if (is_terrain) {
            ids[i] = Fl4208BuildIdFromProvisional(in.prov[i]);
            // Compare RESOLVED material_id (low byte), not the full visual word
            // (reviewer flag #2). The producer already stores the resolved id;
            // masking here is defensive against any other prov writer.
            int mat = (int)Fl4208ResolvedMaterialId(in.prov[i].material);
            if (first_mat < 0) first_mat = mat;
            else if (mat != first_mat) mat_conflict = true;
        } else {
            memset(&ids[i], 0, sizeof(ids[i]));
        }
    }
    // Reject the whole cell on material conflict BEFORE aggregation, and admit
    // NO identities for it (fail-closed, Law 6).
    if (mat_conflict) {
        d.rejected_conflict = true;
        d.reason = FL4208_CELL_ID_REASON_TERRAIN_MATERIAL_CONFLICT;
        return d;
    }
    // A cell with zero terrain identity candidates has nothing to aggregate:
    // report it as NO_TERRAIN_SAMPLE, not as ambiguity (accounting honesty —
    // sky/water/mesh-covered cells are not "split ownership").
    int valid_count = 0;
    for (int i = 0; i < 4; i++) valid_count += valid[i] ? 1 : 0;
    if (valid_count == 0) {
        d.reason = FL4208_CELL_ID_REASON_NO_TERRAIN_SAMPLE;
        return d;
    }

    // First preference (unchanged from the strict-majority repair): a >2-of-4
    // owner-key majority selects the represented surface. Every cell that was
    // valid before this change keeps a BIT-IDENTICAL identity.
    Fl4208AggResult agg = Fl4208AggregateCell(ids, valid);
    if (!agg.valid) {
        // FL-4208 residual burn-down (2026-07-16): at editor zoom one terminal
        // cell spans MULTIPLE terrain quads, so the four supersamples routinely
        // land on 2+ owner quads and a strict majority is structurally
        // impossible (measured: 5,128 of 9,216 cells on the static 128x72
        // frame — a moire lattice at quad boundaries, solid near the camera).
        // Those cells are not "ambiguous ownership"; they are multi-quad cells.
        // Per the FL-4208 audit contract the represented surface must then be
        // derived deterministically FROM DEPTH-WINNING SAMPLES: pick the tap
        // with the greatest native depth (same duel axis the mesh cell rule
        // uses), break exact depth ties by the lower owner key
        // (owner_y, then owner_x, then tap index) so the result is a pure
        // function of the inputs. Material conflicts were already rejected
        // above, so every candidate tap agrees on material.
        int best = -1;
        for (int i = 0; i < 4; i++) {
            if (!valid[i]) continue;
            if (best < 0) { best = i; continue; }
            if (in.depth[i] > in.depth[best]) { best = i; continue; }
            if (in.depth[i] == in.depth[best]) {
                // Equal depth: lower owner key wins (y, then x). Tap index
                // order is implicit — earlier taps win remaining ties because
                // later equal candidates never replace `best`.
                if (ids[i].owner_y < ids[best].owner_y ||
                    (ids[i].owner_y == ids[best].owner_y &&
                     ids[i].owner_x < ids[best].owner_x))
                    best = i;
            }
        }
        // The represented identity is the winning owner; its cell anchor is
        // the rounded mean over the taps of THAT owner only (same anchor rule
        // the majority path uses), so one owner yields one stable anchor.
        int count = 0;
        uint32_t anchor_u_sum = 0, anchor_v_sum = 0;
        for (int j = 0; j < 4; j++) {
            if (valid[j] && Fl4208SameOwnedSurface(ids[j], ids[best])) {
                count++;
                anchor_u_sum += ids[j].anchor_u_q;
                anchor_v_sum += ids[j].anchor_v_q;
            }
        }
        agg.valid = true;
        agg.id = ids[best];
        agg.id.anchor_u_q = (uint16_t)((anchor_u_sum + (uint32_t)(count / 2)) / (uint32_t)count);
        agg.id.anchor_v_q = (uint16_t)((anchor_v_sum + (uint32_t)(count / 2)) / (uint32_t)count);
        d.selected_tap = (uint8_t)best;
    }

    // The paper's owner is one post-raster cell identity, not four sample
    // identities. Intern that represented identity exactly once and attach its
    // per-frame index only to samples belonging to the selected surface.
    const uint32_t represented_index = interner.Intern(agg.id);
    if (represented_index == Fl4208IdentityInterner::INVALID_INDEX) {
        d.reason = FL4208_CELL_ID_REASON_AGGREGATION_AMBIGUOUS;
        return d;
    }
    d.cell_valid = true;
    d.id = agg.id;
    for (int i = 0; i < 4; i++)
        if (valid[i] && Fl4208SameOwnedSurface(ids[i], agg.id))
            d.index[i] = represented_index;
    return d;
}

struct Fl4208MeshCellInput {
    Fl4208ProvisionalId prov[4];
    uint8_t final_dispatch[4];
    float depth[4];
    bool owner_hash_collision[4];
};

// Canonical mesh tap owner: greatest native depth, then lower primitive id,
// then lower existing 2x2 sample index. A colliding winning owner fails closed;
// the function never substitutes a farther, apparently valid surface.
static inline Fl4208CellDecision Fl4208DecideMeshCell(
        const Fl4208MeshCellInput& in, uint8_t mesh_dispatch) {
    Fl4208CellDecision d;
    d.cell_valid = false;
    d.rejected_conflict = false;
    d.reason = FL4208_CELL_ID_REASON_NONE;
    d.selected_tap = 0xFF;
    memset(&d.id, 0, sizeof(d.id));
    for (int i = 0; i < 4; i++) d.index[i] = Fl4208IdentityInterner::INVALID_INDEX;

    int best = -1;
    for (int i = 0; i < 4; i++) {
        if (!in.prov[i].valid || in.prov[i].owner_kind != FL4208_OWNER_MESH ||
            in.final_dispatch[i] != mesh_dispatch)
            continue;
        if (best < 0 || in.depth[i] > in.depth[best] ||
            (in.depth[i] == in.depth[best] &&
             (in.prov[i].primitive_id < in.prov[best].primitive_id ||
              (in.prov[i].primitive_id == in.prov[best].primitive_id && i < best))))
            best = i;
    }
    if (best < 0) {
        d.reason = FL4208_CELL_ID_REASON_AGGREGATION_AMBIGUOUS;
        return d;
    }
    d.selected_tap = (uint8_t)best;
    d.id = Fl4208BuildIdFromProvisional(in.prov[best]);
    if (in.owner_hash_collision[best]) {
        d.rejected_conflict = true;
        d.reason = FL4208_CELL_ID_REASON_MESH_OWNER_HASH_COLLISION;
        return d;
    }
    d.cell_valid = true;
    return d;
}
