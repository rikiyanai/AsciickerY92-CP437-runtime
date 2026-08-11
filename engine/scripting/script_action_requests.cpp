// script_action_requests.cpp — Script-facing gameplay mutation requests
//
// Extracted from engine/script_runtime_api.cpp.
// Owns the script-facing request surface. Does NOT directly mutate Game state;
// all requests are forwarded to script_intent_bridge, which owns gameplay
// authority.
//
// SEE ALSO:
// - engine/scripting/script_action_requests.h — header
// - engine/scripting/script_state_queries.cpp — read-only queries
// - engine/scripting/script_runtime_api.cpp — facade that wraps both
// - engine/scripting/script_intent_bridge.cpp — gameplay authority bridge

#include "script_action_requests.h"

#include "script_intent_bridge.h"

bool ScriptRequestMove(float api_move[3], const float move[3])
{
    return ScriptIntentBridgeApplyMove(api_move, move);
}

bool ScriptRequestJump(bool& jump)
{
    return ScriptIntentBridgeApplyJump(jump);
}

bool ScriptRequestSay(LocalPlayerState& player, const char* text)
{
    return ScriptIntentBridgeApplySay(player, text);
}
