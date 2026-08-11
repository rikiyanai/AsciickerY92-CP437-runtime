#ifndef SCRIPT_STATE_QUERIES_H
#define SCRIPT_STATE_QUERIES_H

struct LocalPlayerState;

enum ScriptPoseSource
{
    SCRIPT_POSE_SOURCE_UNAVAILABLE = 0,
    SCRIPT_POSE_SOURCE_LOCAL = 1,
    SCRIPT_POSE_SOURCE_AUTHORITATIVE_SNAPSHOT = 2
};

struct ScriptPlayerPose
{
    float pos[3];
    float dir;
    float yaw;
    int source;
};

bool ScriptQueryPlayerPose(const LocalPlayerState& player, bool has_server, ScriptPlayerPose* out_pose);
bool ScriptQueryPlayerName(const LocalPlayerState& player, char* out_name, int size);
int ScriptQueryMountState(const LocalPlayerState& player);
int ScriptQueryActionState(const LocalPlayerState& player);
void ScriptQueryMoveIntent(const float api_move[3], float out_move[3]);
float ScriptQueryWaterLevel(float water);
void ScriptQueryLightState(const float light[4], float out_light[4]);
bool ScriptQueryGrounded(bool prev_grounded);

#endif
