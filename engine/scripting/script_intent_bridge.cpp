// script_intent_bridge.cpp — Gameplay Authority Bridge for Script Requests
//
// Owns all gameplay mutations resulting from script action requests.
// This is the narrow seam where script intent becomes gameplay state change.
// Gatekeeping (admin-only, rate limits, etc.) belongs here.
//
// SEE ALSO:
//   engine/scripting/script_intent_bridge.h
//   engine/scripting/script_action_requests.cpp — forwards to this bridge

#include "script_intent_bridge.h"

#include <string.h>

#include "local_player_state.h"
#include "platform/time_backend.h"

bool ScriptIntentBridgeApplyMove(float api_move[3], const float move[3])
{
    if (!move)
        return false;
    memcpy(api_move, move, sizeof(float[3]));
    return true;
}

bool ScriptIntentBridgeApplyJump(bool& jump)
{
    jump = true;
    return true;
}

bool ScriptIntentBridgeApplySay(LocalPlayerState& player, const char* text)
{
    if (!text)
        return false;
    player.Say(text, (int)strlen(text), a3dGetTime());
    return true;
}
