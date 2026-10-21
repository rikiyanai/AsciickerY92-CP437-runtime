#include "script_runtime_api.h"

#include "local_player_state.h"

bool ScriptRuntimeApi::GetPlayerPose(ScriptPlayerPose* out_pose) const
{
    return ScriptQueryPlayerPose(player, has_server, out_pose);
}

bool ScriptRuntimeApi::GetPlayerName(char* out_name, int size) const
{
    return ScriptQueryPlayerName(player, out_name, size);
}

int ScriptRuntimeApi::GetMountState() const
{
    return ScriptQueryMountState(player);
}

int ScriptRuntimeApi::GetActionState() const
{
    return ScriptQueryActionState(player);
}

void ScriptRuntimeApi::GetMoveIntent(float out_move[3]) const
{
    ScriptQueryMoveIntent(api_move, out_move);
}

float ScriptRuntimeApi::GetWaterLevel() const
{
    return ScriptQueryWaterLevel(water);
}

void ScriptRuntimeApi::GetLightState(float out_light[4]) const
{
    ScriptQueryLightState(light_base, out_light);
}

bool ScriptRuntimeApi::IsGrounded() const
{
    return ScriptQueryGrounded(prev_grounded);
}

bool ScriptRuntimeApi::RequestMove(const float move[3]) const
{
    return ScriptRequestMove(api_move, move);
}

bool ScriptRuntimeApi::RequestJump() const
{
    return ScriptRequestJump(jump);
}

bool ScriptRuntimeApi::RequestSay(const char* text) const
{
    return ScriptRequestSay(player, text);
}
