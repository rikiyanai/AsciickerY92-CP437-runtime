// =============================================================================
// engine/input_state.h — Raw input state + engine-side input policy adapter
// =============================================================================
//
// PURPOSE:
// - InputState owns the accumulated keyboard/mouse/touch/gamepad state used by
//   the runtime.
// - GameInputSink adapts host/backend events into the engine dispatch seam
//   extracted from game_app.cpp.
//
// TESTABILITY:
// GameInputSink can be driven by synthetic events without a terminal or window
// system. See tests/cpp/input_state_test.cpp.

#pragma once

#include <stdint.h>

#include "platform/backend/host_input.h"

struct Item;

// ---------------------------------------------------------------------------
// InputState — raw and high-level input accumulation
// ---------------------------------------------------------------------------

struct InputState
{
    // time relaxated KEY_UP/DOWN emulation by KEY_PRESSes
    uint64_t PressStamp;
    int PressKey;
    int KeybAutoRepCap;
    char KeybAutoRepChar;
    uint64_t KeybAuroRepDelayStamp;
    uint8_t keyb_key[32]; // simulated key presses by touch/mouse

    int last_hit_char;
    uint8_t key[32]; // keyb state

    // pad state
    int pad_item; // item index to pick + 1
    bool pad_connected;
    int pad_autorep; // button+1
    uint64_t pad_stamp;
    uint32_t pad_button;
    int16_t pad_axis[32];

    // we split touch input to multiple separate mice with left button only
    struct Contact
    {
        enum
        {
            NONE,
            KEYBCAP,
            PLAYER,
            TORQUE, // can be abs (right mouse but) or (timer touch on margin)
            FORCE,

            ITEM_LIST_CLICK,
            ITEM_LIST_DRAG,
            ITEM_GRID_CLICK,
            ITEM_GRID_DRAG,
            ITEM_GRID_SCROLL,
        };

        int action;

        int drag;    // which button initiated drag and is still down since then OR ZERO if there is no contact!!!
        int pos[2];  // mouse pos
        int drag_from[2]; // where drag has started

        Item* item;
        int my_item;
        int keyb_cap; // if touch starts at some cap
        bool player_hit; // if touch started at player/talkbox
        int margin; // -1: if touch started at left margin, +1 : if touch started at right margin, 0 otherwise
        float start_yaw; // absolute by mouse
        int scroll;
    };

    Contact contact[4]; // 0:mouse, 1:primary_touch 2:secondary_touch ( 3:unused -> GAMEPAD/KEYB )

    uint8_t but; // real mouse buttons currently down
    int wheel;   // relative mouse wheel (only from real mouse)
    int size[2]; // window size (in pixels)
    bool jump;

    float api_move[3]; // x,y,alpha
    // x,y is screen space (rotate it by yaw to set it in world space)
    // alpha=0 : fully additive,
    // alpha=1 : replace

    bool shot; // screenshot!

    bool IsKeyDown(int k)
    {
        return (key[k >> 3] & (1 << (k & 7))) != 0;
    }

    void ClearKey(int k)
    {
        key[k >> 3] &= ~(1 << (k & 7));
    }
};

// ---------------------------------------------------------------------------
// InputDispatch — thin output target for GameInputSink
// ---------------------------------------------------------------------------
// Implemented by game_app.cpp wiring (forwards to Game*) and by test mocks.

struct InputDispatch
{
    virtual void OnKeyb(int keyb_type, int key) = 0;
    virtual void OnMouse(int mouse_type, int x, int y) = 0;
    virtual void OnSize(int w, int h, int fw, int fh) = 0;
    virtual ~InputDispatch() = default;
};

// ---------------------------------------------------------------------------
// GameInputSink — engine-side InputSink adapter
// ---------------------------------------------------------------------------

struct GameInputSink final : InputSink
{
    InputDispatch* dispatch = nullptr;
    uint64_t now_us = 0;

    explicit GameInputSink(InputDispatch* d = nullptr);
    void SetDispatch(InputDispatch* d);

    void Tick(uint64_t dt_us);

    void OnKey(const HostKeyEvent& ev) override;
    void OnText(const HostTextEvent& ev) override;
    void OnMouse(const HostMouseEvent& ev) override;
    void OnResize(const HostResizeEvent& ev) override;

private:
    uint8_t  hold_down[256] = {};
    uint64_t hold_deadline[256] = {};

    int NormalizeTerminalByteKey(int key) const;
    void HoldKey(int key);
};
