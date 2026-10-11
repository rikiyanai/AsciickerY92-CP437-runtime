#pragma once

// engine/actor_visual_key_derivation.h
//
// CompiledActorVisualKey derivation contract — single owner across
// authoritative state sources. Closes FL-3955 V-2 (three independent
// derivations across compiler / runtime / server with no shared code).
//
// Contract (internal design notes):
//
//   A CompiledActorVisualKey is the exact runtime lookup key made from
//   server-owned state IDs only:
//
//     skin_id, actor_style_id, presentation_kind_id, variation_id,
//     mount_id, rig_id, canonical slot item/style IDs (head, chest,
//     weapon, shield), plus a fixed-arity ordered overflow array for
//     future canonical slots.
//
//   Two distinct state-source types must produce identical keys for the
//   same authoritative state:
//
//     1. ActorVisualProfile  (client-side runtime, used by the renderer)
//     2. SvrAuthoritativeAppearanceState  (server-side authoritative state)
//
//   The client-side builder BuildCompiledActorVisualKey is declared and
//   defined static-inline in engine/actor_visual_profile_runtime.h
//   (kept there for include-graph compatibility). The server-side builder
//   SvrBuildCompiledActorVisualKey is declared here and defined in
//   server/actor_visual_key_derivation.cpp.
//
// Equivalence rules (must hold for every authoritative state X):
//
//   BuildCompiledActorVisualKey(ActorVisualProfile_from(X), kind, &out_a)
//     ==
//   SvrBuildCompiledActorVisualKey(SvrAuthoritativeAppearanceState_from(X), kind, &out_b)
//
//   For every pair (out_a, out_b): every field must be byte-equal.
//
// Slot mapping (BOTH builders implement identically):
//
//   APPEARANCE_SLOT_KIND_HEAD    -> head_item_id, head_style_id
//   APPEARANCE_SLOT_KIND_ARMOR   -> chest_item_id, chest_style_id
//   APPEARANCE_SLOT_KIND_WEAPON  -> weapon_item_id, weapon_style_id
//   APPEARANCE_SLOT_KIND_SHIELD  -> shield_item_id, shield_style_id
//   APPEARANCE_SLOT_KIND_BODY    -> (ignored; body is implied by skin_id)
//   APPEARANCE_SLOT_KIND_MOUNT   -> (ignored; mount is in mount_id field)
//   any other                    -> future_slot_kind_ids[] / _item_ids[] / _style_ids[]
//                                   inserted in ASCENDING (slot_kind_id, item_id) order
//                                   with at most 4 entries; overflow rejected.
//
// Identity fields:
//
//   skin_id              = source.skin_id (or skin_definition_id alias)
//   actor_style_id       = 0 (placeholder; populate once snapshot path carries it)
//   presentation_kind_id = passed-in argument
//   variation_id         = source.variation_id
//   mount_id             = source.mount_definition_id (alias mount_id)
//   rig_id               = source.rig_id
//
// FL-3955 violations the contract closes:
//
//   V-1 (LIVE):    future_slot_* must NOT be silently skipped by either side.
//   V-2 (STRUCTURAL): one shared declaration owns the contract.
//   V-3 (LATENT):  SVR_APPEARANCE_VISUAL_STYLE_DEFAULT is consistent across
//                  all surfaces; see protocol_common.h's APPEARANCE_VISUAL_STYLE_DEFAULT.
//   V-4 (LATENT):  visual_style_id == 0 is "default", not "missing"; both
//                  builders include it in the key.
//   V-5 (ARCHITECTURAL): bidirectional parse check is owned by
//                  ValidateCompiledActorVisualKeyHasRow (this header) plus the
//                  client-side BuildCompiledActorVisualKey path.
//   V-6 (ROOT PATTERN): a future Python compiler (per FL-3912 ADR M5)
//                  consumes these rules via a generated test-vector pairing.

#include <stdint.h>

#include "actor_visual_profile.h"

struct SvrAuthoritativeAppearanceState;

// Server-side builder. Mirrors the static-inline runtime builder
// (BuildCompiledActorVisualKey in actor_visual_profile_runtime.h) exactly:
// same slot-kind mapping, same future-slot sorting, same defaults.
//
// Returns true on success; false when the input has more than 4 future-slot
// entries (the only failure case the runtime version also rejects).
//
// Implementation in server/actor_visual_key_derivation.cpp.
bool SvrBuildCompiledActorVisualKey(
    const SvrAuthoritativeAppearanceState* appearance,
    uint16_t presentation_kind_id,
    CompiledActorVisualKey* out);

// Compile-gate-at-runtime per internal design notes + FL-4055 closure: given a
// key just derived from authoritative state, assert that a matching row
// exists in the compiled visual table. Returns true if a row exists.
//
// Used by the server right before publishing appearance changes so
// unrenderable reachable keys fail loudly instead of silently producing
// invisible actors at runtime. The caller decides whether to log, reject
// the publish, or flag the state — this function is purely a query.
//
// Implementation in server/actor_visual_key_derivation.cpp; wraps the
// existing FindCompiledActorVisualRow.
bool ValidateCompiledActorVisualKeyHasRow(const CompiledActorVisualKey* key);
