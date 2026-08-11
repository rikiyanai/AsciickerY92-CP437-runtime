// web_disconnect_witness.cpp — Web disconnect/proof witness implementation
//
// Extracted from web/game_web.cpp: exit_handler, VerifierRespawn,
// RecorderStateJson, ClientObservationJsonV1.
//
// SEE ALSO:
// - web/web_disconnect_witness.h

#include "web_disconnect_witness.h"

#include <emscripten.h>
#include <stdint.h>
#include <string.h>

#include "game.h"
#include "protocol_combat.h"
#include "web_network_client.h"
#include "web_recorder_bridge.h"

// Globals from game_web.cpp / game.h
extern Game* game;
extern GameServerAllocation* g_web_server_alloc;

// ── Disconnect lifecycle ──

void exit_handler(int signum)
{
    (void)signum;
    EM_ASM(
    {
        try
        {
            // If no history, try to close the window (may be blocked by browser)
            if (window.history.length<=1)
                window.close();
            else
            // If this is an inline embed, we can't navigate away
            if (history.state && history.state.inline == 1)
            {
                // we can't close, we cant go back
                // should we really go forward?
                // history.forward();
            }
            else
                // Navigate back to previous page
                history.back();
        }
        catch(e) {}  // Silently ignore if browser blocks the action
    });
}

// ── Proof witness surfaces (extern "C" exports) ──

extern "C" int VerifierRespawn()
{
    STRUCT_REQ_RESPAWN req = {};
    req.token = 'R';
    Packet((const uint8_t*)&req, sizeof(req));
    return 0;
}

// Static buffer for recorder state JSON snapshot (single browser bridge).
static char s_recorder_state_json[262144];

extern "C" EMSCRIPTEN_KEEPALIVE const char* RecorderStateJson()
{
    return BuildRecorderStateJson(
        s_recorder_state_json, (int)sizeof(s_recorder_state_json),
        game, server,
        g_web_server_alloc ? (const Server*)&g_web_server_alloc->server : 0);
}

extern "C" EMSCRIPTEN_KEEPALIVE const char* ClientObservationJsonV1()
{
    return RecorderStateJson();
}
