// script_action_requests.h — Script-facing gameplay mutation requests
//
// Extracted from engine/script_runtime_api.cpp.
// Owns the script-facing request surface. Does NOT directly mutate Game state;
// all requests are forwarded to script_intent_bridge, which owns gameplay
// authority.
//
// Read-only queries live in script_state_queries.h.
// The ScriptRuntimeApi facade in script_runtime_api.h wraps both.
//
// SEE ALSO:
// - engine/scripting/script_action_requests.cpp — implementation
// - engine/scripting/script_state_queries.h — read-only queries
// - engine/scripting/script_runtime_api.h — combined facade
// - engine/scripting/script_intent_bridge.h — gameplay authority bridge

#ifndef SCRIPT_ACTION_REQUESTS_H
#define SCRIPT_ACTION_REQUESTS_H

struct LocalPlayerState;

bool ScriptRequestMove(float api_move[3], const float move[3]);
bool ScriptRequestJump(bool& jump);
bool ScriptRequestSay(LocalPlayerState& player, const char* text);

#endif
