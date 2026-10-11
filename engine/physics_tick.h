#pragma once

// SYNC: must match server_state.h (SVR_TICK_RATE / SVR_PHYSICS_SUBSTEPS).
static constexpr uint64_t PHYSICS_TICK_RATE = 30;
static constexpr uint64_t PHYSICS_TICK_INTERVAL_US = 1000000 / PHYSICS_TICK_RATE;
static constexpr uint64_t PHYSICS_SUBSTEPS = 2;

// [H-2] Outer accumulator step MUST equal Animate()'s inner interval (15000us).
// Previous value: PHYSICS_TICK_INTERVAL_US / PHYSICS_SUBSTEPS = 33333/2 = 16666us.
// Animate() loops `while(stamp - phys->stamp >= 15000)`, so 16666us outer steps left
// a 1666us remainder that accumulated a double-step every ~9 ticks. During replay the
// remainder could diverge from the original prediction, manufacturing ~0.5-1.0 units/sec
// of position error. Setting PHYSICS_STEP_US = 15000 makes outer = inner = 1:1.
static constexpr uint64_t PHYSICS_STEP_US = 15000;

static constexpr uint64_t PHYSICS_SPIRAL_CLAMP_US = 83333;

template <typename T>
static inline T MpClamp(T value, T min_value, T max_value)
{
    return value < min_value ? min_value : (value > max_value ? max_value : value);
}

static inline float MpWrapYaw(float yaw)
{
    while (yaw > 180.0f)
        yaw -= 360.0f;
    while (yaw < -180.0f)
        yaw += 360.0f;
    return yaw;
}
