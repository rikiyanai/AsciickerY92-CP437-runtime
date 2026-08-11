#pragma once

// local_player_authority.h — Local player authoritative movement and network seam
//
// PURPOSE:
// Declares the free-function seam for local-player physics IO preparation
// and network update sending. Extracted from game.h so the local-player
// authority boundary is explicit.
//
// PrepareLocalMovementStepIO was previously a Game:: member. It is now a
// free function that takes explicit state references (InputState,
// LocalPlayerState, CameraState, GameSession, UiState, DebugTelemetryState)
// so its dependencies are visible at the call site instead of hidden behind
// the Game object (FL-2731).
//
// SEE ALSO: local_player_authority.cpp, local_player_state.h, game.h

#include <stdint.h>

struct InputState;
struct LocalPlayerState;
struct CameraState;
struct GameSession;
struct UiState;
struct DebugTelemetryState;
struct Physics;
struct PhysicsIO;
struct MpMoveSendLifecycleResult;
struct MpMoveState;
struct Server;
struct Terrain;
struct World;

// Build the local player's PhysicsIO from input, camera, session, and
// current player pose. Mutates player.yaw_vel and writes debug counters.
// The caller (game_render_bridge) assembles the state references from Game.
void PrepareLocalMovementStepIO(
    InputState& input,
    LocalPlayerState& player,
    CameraState& camera,
    const GameSession& session,
    const UiState& ui,
    DebugTelemetryState& debug,
    bool authoritative_session,
    bool is_server_session,
    int server_local_id,
    uint64_t _stamp,
    uint64_t stamp,
    Physics* physics,
    PhysicsIO* io);

// Send local pose/inputs to the authoritative server if one exists.
// Returns the lifecycle result; caller checks jump_consumed and clears input.
// Takes explicit dependencies instead of globals or Game*.
MpMoveSendLifecycleResult SendLocalNetworkUpdates(
    MpMoveState& mp_move,
    Server* server,
    uint64_t stamp,
    const PhysicsIO& io,
    Terrain* terrain,
    World* world,
    float water);

// Release torque drag by snapping physics yaw to prev_yaw.
// Does not touch Game state; reads only the two float params passed in.
void ApplyLocalInputYawRelease(Physics* physics, float prev_yaw, float yaw_vel);
