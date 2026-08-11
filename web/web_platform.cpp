// web_platform.cpp — Web platform entry / browser bridge
//
// PURPOSE: Browser platform entry points (render, input, timing, haptics).
// Extracted from web/game_web.cpp to separate the browser event bridge from
// filesystem, networking, and diagnostics.
//
// SEE ALSO:
// - web_platform.h — declarations

#include <emscripten.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "game.h"
#include "game_utility.h"
#include "render.h"
#include "web_diagnostics.h"
#include "web_platform.h"

// ── Forward declarations of game_web.cpp globals ──

extern Game* game;
extern AnsiCell* render_buf;
extern "C" int Main();

// ── Platform globals ──

// Base path for loading game resources (models, textures, etc.)
char base_path[1024] = "./";

// Function pointer for timestamp generation (allows swapping timing implementations).
uint64_t (*MakeStamp)() = 0; // initialized in a3dGetTime() below

// Client render duration (sampled each frame, read by recorder)
// non-static: read by game_web.cpp RecorderStateJson via extern
uint32_t g_web_client_render_duration_us = 0;

// ── Helper: store player name from Load() ──

static void StoreWebPlayerName(const char* name)
{
    if (!name)
        return;
    snprintf(player_name, 32 * 4, "%s", name);
    ConvertToCP437(player_name_cp437, player_name, (int)sizeof(player_name_cp437));
}

// ── Timing ──

// Platform time: returns microseconds since page load (wraps every 584542 years).
uint64_t a3dGetTime()
{
    return (uint64_t)(emscripten_get_now() * 1000.0);
}

// Microsecond timestamp (delegates to a3dGetTime).
uint64_t GetTime()
{
    return a3dGetTime();
}

// Initialize MakeStamp to GetTime after GetTime is defined.
// This is set up lazily: first call to MakeStamp returns GetTime().
// Use a simple static init approach.
namespace {
    bool ensure_makestamp() {
        if (!MakeStamp)
            MakeStamp = GetTime;
        return true;
    }
    bool _ms_init = ensure_makestamp();
}

// ── Haptics ──

// Trigger haptic feedback (gamepad rumble or mobile vibration).
void Buzz()
{
    EM_ASM(
    {
        // Try gamepad vibration first (if gamepad is connected)
        if (gamepad>=0 && "getGamePads" in navigator)
        {
            let gm = navigator.getGamepads()[gamepad];
            let va = gm.vibrationActuator;
            if (va)
            {
                va.playEffect(va.type, {startDelay: 0,  duration: 50,  weakMagnitude: 1,  strongMagnitude: 1});
            }
        }
        else
        // Fallback to mobile vibration API
        if ("vibrate" in navigator)
        {
            navigator.vibrate(50);
        }
    });
}

// ══════════════════════════════════════════════════════════════════════════
// Extern "C" exports — JS-callable browser entry points
// ══════════════════════════════════════════════════════════════════════════

extern "C"
{
    // Initialize game with player name (called from JavaScript on game start)
    void Load(const char* name)
    {
        StoreWebPlayerName(name);
        if (!game)
            Main();
    }

    void SetRequestedA3dPath(const char* path)
    {
        if (!path || !path[0])
        {
            g_requested_a3d_path[0] = 0;
            return;
        }
        snprintf(g_requested_a3d_path, sizeof(g_requested_a3d_path), "%s", path);
        g_requested_a3d_path[sizeof(g_requested_a3d_path) - 1] = 0;
        printf("[WEB-MAP] requested a3d path: %s\n", g_requested_a3d_path);
    }

    // Set deterministic world seed received from multiplayer join response.
    void SetNetSeed(uint32_t seed)
    {
        SetMultiplayerWorldSeed(seed);
    }

    // Main render function (called every frame from JavaScript).
    // Returns pointer to AnsiCell buffer containing character grid to display.
    void* Render(int width, int height)
    {
        static int call_count = 0;
        if (call_count++ < 3) {
            printf("[C++ DEBUG] Render() called: width=%d height=%d game=%p render_buf=%p\n",
                   width, height, game, render_buf);
        }

        if (game && render_buf)
        {
            uint64_t render_begin_us = GetTime();
            game->Render(render_begin_us, render_buf, width, height);
            uint64_t render_end_us = GetTime();
            g_web_client_render_duration_us = (uint32_t)(render_end_us - render_begin_us);
            WebDiagnosticsSampleRenderBuffer(render_buf, width, height);
            // FL-4079: stamp the just-bumped probe_seq into game->debug so the
            // wearable proof probe reads ALL fields from one consistent source
            // (game->debug) instead of splitting across diagnostics globals and
            // debug state, which could race across Render() boundaries.
            game->debug.dbg_actor_render_probe_seq = WebDiagnosticsGetRenderBufProbeSeq();

            return render_buf;
        }

        WebDiagnosticsSampleRenderBuffer(0, width, height);
        printf("[C++ ERROR] Render() returning 0: game=%p render_buf=%p\n", game, render_buf);
        return 0;
    }

    // Handle window/canvas resize events from JavaScript.
    void Size(int w, int h, int fw, int fh)
    {
        if (game)
            game->OnSize(w, h, fw, fh);
    }

    // Handle keyboard events from JavaScript.
    void Keyb(int type, int val)
    {
        if (game)
            game->OnKeyb((GAME_KEYB)type, val);
    }

    // Handle mouse events from JavaScript.
    void Mouse(int type, int x, int y)
    {
        if (game)
            game->OnMouse((GAME_MOUSE)type, x, y);
    }

    // Handle touch events from JavaScript (for mobile).
    void Touch(int type, int id, int x, int y)
    {
        if (game)
            game->OnTouch((GAME_TOUCH)type, id, x, y);
    }

    // Handle gamepad events from JavaScript.
    void GamePad(int ev, int idx, float val)
    {
        static int gamepad_axes = 0;
        static int gamepad_buttons = 0;
        static uint8_t gamepad_mapping[256];

        switch (ev)
        {
            case 0:  // Mount/unmount gamepad
            {
                if (!idx)
                    GamePadUnmount();
                else
                    GamePadMount("fixme", gamepad_axes, gamepad_buttons, gamepad_mapping);
                break;
            }
            case 1:  // Button press/release
            {
                int16_t v = (int16_t)(val * 32767);
                GamePadButton(idx, v);
                break;
            }
            case 2:  // Analog stick/trigger axis
            {
                int16_t v = val > 1.0 || val < -1.0 ? (int16_t)-32768 : (int16_t)(val * 32767);
                GamePadAxis(idx, v);
                break;
            }
            case 3:  // Set mapping size
            {
                gamepad_buttons = idx & 0xFFFF;
                gamepad_axes = (idx >> 16) & 0xFFFF;
                break;
            }
            case 4:  // Map button
            {
                int map_idx = ((idx >> 8) & 0xFF) + 2 * gamepad_axes;
                gamepad_mapping[map_idx] = idx & 0xFF;
                break;
            }
            case 5:  // Map axis
            {
                int map_neg = 2 * ((idx >> 16) & 0xFF);
                int map_pos = map_neg + 1;
                gamepad_mapping[map_neg] = idx & 0xFF;
                gamepad_mapping[map_pos] = (idx >> 8) & 0xFF;
                break;
            }
        }
    }

    // Handle window focus/blur events from JavaScript.
    void Focus(int set)
    {
        if (game)
            game->OnFocus(set != 0);
    }
}
