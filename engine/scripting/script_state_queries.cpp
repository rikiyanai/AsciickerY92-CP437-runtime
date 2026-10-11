// script_state_queries.cpp — Script-facing read-only state queries
//
// SEE ALSO:
// - engine/scripting/script_state_queries.h — header
// - engine/scripting/script_action_requests.cpp — action requests (mutations)
// - engine/scripting/script_runtime_api.cpp — facade that wraps both

#include "script_state_queries.h"

#include <math.h>
#include <string.h>

#include "game.h"
#include "local_player_state.h"
#include "mp_move.h"

static void ScriptSetUnavailablePose(ScriptPlayerPose* out_pose)
{
    if (!out_pose)
        return;
    out_pose->pos[0] = NAN;
    out_pose->pos[1] = NAN;
    out_pose->pos[2] = NAN;
    out_pose->dir = NAN;
    out_pose->yaw = NAN;
    out_pose->source = SCRIPT_POSE_SOURCE_UNAVAILABLE;
}

bool ScriptQueryPlayerPose(const LocalPlayerState& player, bool has_server, ScriptPlayerPose* out_pose)
{
    ScriptSetUnavailablePose(out_pose);
    if (!out_pose)
        return false;

    if (!LocalPlayerAuthoritativePoseReady(player, has_server))
        return false;

    if (MpMoveHasAuthoritativeSnapshot(&player.mp_move))
    {
        out_pose->pos[0] = player.mp_move.auth_state.pos[0];
        out_pose->pos[1] = player.mp_move.auth_state.pos[1];
        out_pose->pos[2] = player.mp_move.auth_state.pos[2];
        out_pose->dir = player.mp_move.auth_state.player_dir;
        out_pose->source = SCRIPT_POSE_SOURCE_AUTHORITATIVE_SNAPSHOT;
    }
    else
    {
        out_pose->pos[0] = player.pos[0];
        out_pose->pos[1] = player.pos[1];
        out_pose->pos[2] = player.pos[2];
        out_pose->dir = player.dir;
        out_pose->source = SCRIPT_POSE_SOURCE_LOCAL;
    }

    out_pose->yaw = player.prev_yaw;
    return true;
}

bool ScriptQueryPlayerName(const LocalPlayerState& player, char* out_name, int size)
{
    if (!out_name || size <= 0)
        return false;
    out_name[0] = 0;
    strncpy(out_name, player.name, (size_t)size - 1u);
    out_name[size - 1] = 0;
    return true;
}

int ScriptQueryMountState(const LocalPlayerState& player)
{
    return (int)player.mount_state;
}

int ScriptQueryActionState(const LocalPlayerState& player)
{
    return (int)player.combat_state;
}

void ScriptQueryMoveIntent(const float api_move[3], float out_move[3])
{
    if (!out_move)
        return;
    memcpy(out_move, api_move, sizeof(float[3]));
}

float ScriptQueryWaterLevel(float water)
{
    return water;
}

void ScriptQueryLightState(const float light[4], float out_light[4])
{
    if (!out_light)
        return;
    memcpy(out_light, light, sizeof(float[4]));
}

bool ScriptQueryGrounded(bool prev_grounded)
{
    return prev_grounded;
}
