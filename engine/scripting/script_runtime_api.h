#ifndef SCRIPT_RUNTIME_API_H
#define SCRIPT_RUNTIME_API_H

#include "script_state_queries.h"
#include "script_action_requests.h"

struct LocalPlayerState;

struct ScriptRuntimeApi
{
    explicit ScriptRuntimeApi(LocalPlayerState& player_ref, bool has_server_ref,
        float api_move_ref[3], float water_ref, const float light_ref[4],
        bool& jump_ref, bool prev_grounded_ref)
        : player(player_ref), has_server(has_server_ref), api_move(api_move_ref),
          water(water_ref), light_base(light_ref), jump(jump_ref),
          prev_grounded(prev_grounded_ref) {}

    bool GetPlayerPose(ScriptPlayerPose* out_pose) const;
    bool GetPlayerName(char* out_name, int size) const;
    int GetMountState() const;
    int GetActionState() const;
    void GetMoveIntent(float out_move[3]) const;
    float GetWaterLevel() const;
    void GetLightState(float out_light[4]) const;
    bool IsGrounded() const;

    bool RequestMove(const float move[3]) const;
    bool RequestJump() const;
    bool RequestSay(const char* text) const;

    LocalPlayerState& player;
    bool has_server;
    float* api_move;
    float water;
    const float* light_base;
    bool& jump;
    bool prev_grounded;
};

#endif
