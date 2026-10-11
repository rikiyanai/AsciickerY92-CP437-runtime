// =============================================================================
// engine/input_state.cpp — Engine-Side Input Policy Adapter
// =============================================================================

#include "input_state.h"
#include "game.h"
#include "platform/input_backend.h"

GameInputSink::GameInputSink(InputDispatch* d) : dispatch(d) {}

void GameInputSink::SetDispatch(InputDispatch* d) { dispatch = d; }

void GameInputSink::Tick(uint64_t dt_us)
{
    now_us += dt_us;
    for (int k = 0; k < 256; k++)
    {
        if (hold_down[k] && now_us >= hold_deadline[k])
        {
            hold_down[k] = 0;
            if (dispatch)
                dispatch->OnKeyb((int)GAME_KEYB::KEYB_UP, k);
        }
    }
}

void GameInputSink::OnKey(const HostKeyEvent& ev)
{
    if (!dispatch)
        return;
    int key = ev.domain == HostKeyDomain::TerminalByte
        ? NormalizeTerminalByteKey(ev.key)
        : ev.key;
    switch (ev.action)
    {
    case KeyAction::Down:
        if (key >= 0)
            HoldKey(key);
        break;
    case KeyAction::Up:
        if (key < 0)
            break;
        if (key >= 0 && key < 256)
            hold_down[key] = 0;
        dispatch->OnKeyb((int)GAME_KEYB::KEYB_UP, key);
        break;
    case KeyAction::Press:
        if (ev.domain == HostKeyDomain::KeyInfo)
            dispatch->OnKeyb((int)GAME_KEYB::KEYB_PRESS, key | ev.mods);
        break;
    }
}

void GameInputSink::OnText(const HostTextEvent& ev)
{
    if (!dispatch)
        return;
    dispatch->OnKeyb((int)GAME_KEYB::KEYB_CHAR, (int)ev.codepoint);
    switch (ev.codepoint)
    {
    case 'w': case 'W': HoldKey(A3D_W); break;
    case 'a': case 'A': HoldKey(A3D_A); break;
    case 's': case 'S': HoldKey(A3D_S); break;
    case 'd': case 'D': HoldKey(A3D_D); break;
    case 'q': case 'Q': HoldKey(A3D_Q); break;
    case 'e': case 'E': HoldKey(A3D_E); break;
    case 'i': case 'I': HoldKey(A3D_I); break;
    case 'x': case 'X': HoldKey(A3D_X); break;
    case '2':             HoldKey(A3D_2); break;
    case ' ':             HoldKey(A3D_SPACE); break;
    }
}

void GameInputSink::OnMouse(const HostMouseEvent& ev)
{
    if (!dispatch)
        return;
    switch (ev.action)
    {
    case MouseAction::Move:
        dispatch->OnMouse((int)GAME_MOUSE::MOUSE_MOVE, ev.x, ev.y);
        break;
    case MouseAction::ButtonDown:
        if (ev.button == 0)
            dispatch->OnMouse((int)GAME_MOUSE::MOUSE_LEFT_BUT_DOWN, ev.x, ev.y);
        else if (ev.button == 1)
            dispatch->OnMouse((int)GAME_MOUSE::MOUSE_MIDDLE_BUT_DOWN, ev.x, ev.y);
        else if (ev.button == 2)
            dispatch->OnMouse((int)GAME_MOUSE::MOUSE_RIGHT_BUT_DOWN, ev.x, ev.y);
        break;
    case MouseAction::ButtonUp:
        if (ev.button == 0)
            dispatch->OnMouse((int)GAME_MOUSE::MOUSE_LEFT_BUT_UP, ev.x, ev.y);
        else if (ev.button == 1)
            dispatch->OnMouse((int)GAME_MOUSE::MOUSE_MIDDLE_BUT_UP, ev.x, ev.y);
        else if (ev.button == 2)
            dispatch->OnMouse((int)GAME_MOUSE::MOUSE_RIGHT_BUT_UP, ev.x, ev.y);
        break;
    case MouseAction::Wheel:
        if (ev.wheel > 0)
            dispatch->OnMouse((int)GAME_MOUSE::MOUSE_WHEEL_UP, ev.x, ev.y);
        else
            dispatch->OnMouse((int)GAME_MOUSE::MOUSE_WHEEL_DOWN, ev.x, ev.y);
        break;
    }
}

void GameInputSink::OnResize(const HostResizeEvent& ev)
{
    if (dispatch)
        dispatch->OnSize(ev.cols, ev.rows, ev.fbw, ev.fbh);
}

int GameInputSink::NormalizeTerminalByteKey(int key) const
{
    switch (key)
    {
    case 'w': case 'W': return A3D_W;
    case 'a': case 'A': return A3D_A;
    case 's': case 'S': return A3D_S;
    case 'd': case 'D': return A3D_D;
    case 'q': case 'Q': return A3D_Q;
    case 'e': case 'E': return A3D_E;
    case 'i': case 'I': return A3D_I;
    case 'x': case 'X': return A3D_X;
    case '2':           return A3D_2;
    case ' ':           return A3D_SPACE;
    default:            return key < 128 ? key : -1;
    }
}

void GameInputSink::HoldKey(int key)
{
    if (key < 0 || key >= 256)
        return;
    if (!hold_down[key])
    {
        hold_down[key] = 1;
        dispatch->OnKeyb((int)GAME_KEYB::KEYB_DOWN, key);
    }
    hold_deadline[key] = now_us + 140000ULL;
}
