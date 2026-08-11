#ifndef SCRIPT_INTENT_BRIDGE_H
#define SCRIPT_INTENT_BRIDGE_H

// script_intent_bridge.h — Gameplay Authority Bridge for Script Requests
//
// PURPOSE:
// Owns the actual gameplay mutations triggered by script action requests.
// Scripts formulate intents via script_action_requests; this bridge decides
// whether and how to apply them to Game state.
//
// TARGET RULE:
//   script_action_requests = client intent or admin-only command surface
//   script_intent_bridge   = gameplay authority that performs mutation
//   Scripts should NOT directly mutate gameplay authority.
//
// SEE ALSO:
//   engine/scripting/script_action_requests.h — script-facing request surface
//   engine/scripting/script_action_requests.cpp — forwards to this bridge

struct LocalPlayerState;

bool ScriptIntentBridgeApplyMove(float api_move[3], const float move[3]);
bool ScriptIntentBridgeApplyJump(bool& jump);
bool ScriptIntentBridgeApplySay(LocalPlayerState& player, const char* text);

#endif
