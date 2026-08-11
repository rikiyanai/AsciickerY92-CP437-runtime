// game_api.cpp - JavaScript <-> C++ Bridge Layer
//
// PURPOSE: Provides high-performance bidirectional data exchange between JavaScript NPC scripts
// and C++ game engine via shared memory buffer and callback dispatch system.
//
// SHARED MEMORY BUFFER LAYOUT (akAPI_Buff):
// - Size: 65536 + 32 bytes = 65568 bytes total
// - Layout:
//   [0..65535]      : Data exchange region (int32/float32/string transfer)
//   [65536..65567]  : Callback bitfield flags (256 bits / 32 bytes)
//
// WHY SHARED BUFFER: Avoid marshaling overhead for 60+ calls per frame. JavaScript and C++
// directly read/write fixed offsets in WASM memory heap. Zero copy transfers for floats/ints.
//
// DATA EXCHANGE PROTOCOL:
// - JavaScript writes arguments to buffer offsets → calls akAPI_Call(id) → C++ reads buffer
// - C++ writes results to buffer offsets → returns → JavaScript reads buffer
// - Fixed offsets documented per-function below (offset 0 = position X, offset 4 = position Y, etc.)
//
// CALLBACK SYSTEM:
// - JavaScript registers callbacks via cb(idx, fnc) → sets bitfield flag at akAPI_Buff[65536 + (idx>>3)]
// - C++ checks bitfield via akAPI_CheckCB(id) before invoking → O(1) registered check
// - WHY BITFIELD: Faster than linear search of 256-element callback array
// - Callback slots: 0=onSay, 1=onItem, 2=onFrame (see akAPI_CB dispatch in akAPI_Init)
//
// JAVASCRIPT API SURFACE (ak object):
// - Player Queries: getPos, getDir, getYaw, getName, getMount, getAction
// - Intent Queries: getMoveIntent
// - Environment Queries: getWater, getLight, isGrounded
// - Action Requests: requestMove(arr3), requestSay(str), requestJump()
// - Queries: isGrounded()
// - Callbacks: onSay(fnc), onItem(fnc), onFrame(fnc)
//
// KEY FILES:
// - game_api.h: Header declarations
// - game.cpp: C++ game state modified by API calls
// - game_app.cpp: V8 JavaScript engine initialization (native platforms)
// - game_web.cpp: Emscripten WASM integration (web platform)
//
// [FLOW:SCRIPT] Tags below mark critical bridge points for JavaScript interop.

#include <stdint.h>
#include <string.h>
#include <math.h>

#include "game_api.h"
#include "game.h"
#include "scripting/script_runtime_api.h"

// [DATA-CONTRACT:SCRIPT]
// Shared memory buffer for C++ <-> JavaScript data exchange.
// JavaScript accesses via Module.HEAPF32/HEAP32/HEAPU8 at offset akAPI_Buff.
// Size: 65568 bytes (65536 data + 32 callback flags).
void* akAPI_Buff = 0;

#define CODE(...) #__VA_ARGS__

// [FLOW:SCRIPT]
// Initialization of the JavaScript <-> C++ Bridge (Emscripten).
// Defines a set of JS functions (akGetF32, akSetF32, etc.) and a shared memory buffer (akAPI_Buff)
// to facilitate high-performance data exchange.
//
// The 'ak' object on the JS side exposes the high-level API:
// - read-only queries
// - explicit action requests
// - Callbacks: onSay, onItem, onFrame
void akAPI_Init()
{
    akAPI_Exec( CODE(
    {
        this.akGetF32 = function(buf_ofs)
        {
            buf_ofs += akAPI_Buff>>2;
            return Module.HEAPF32[buf_ofs];
        };
        this.akSetF32 = function(val, buf_ofs)
        {
            buf_ofs += akAPI_Buff>>2;
            Module.HEAPF32[buf_ofs] = val;
        };
        this.akReadF32 = function(arr,arr_ofs,buf_ofs,num)
        {
            buf_ofs += akAPI_Buff>>2;
            for (let i=0; i<num; i++)
                arr[i+arr_ofs] = Module.HEAPF32[i+buf_ofs];
        };
        this.akWriteF32 = function(arr,arr_ofs,buf_ofs,num)
        {
            buf_ofs += akAPI_Buff>>2;
            for (let i=0; i<num; i++)
                Module.HEAPF32[i+buf_ofs] = arr[i+arr_ofs];
        }; 
        this.akGetI32 = function(buf_ofs)
        {
            buf_ofs += akAPI_Buff>>2;
            return Module.HEAP32[buf_ofs];
        };
        this.akSetI32 = function(val, buf_ofs)
        {
            buf_ofs += akAPI_Buff>>2;
            Module.HEAP32[buf_ofs] = val;
        };
        this.akReadI32 = function(arr,arr_ofs,buf_ofs,num)
        {
            buf_ofs += akAPI_Buff>>2;
            for (let i=0; i<num; i++)
                arr[i+arr_ofs] = Module.HEAP32[i+buf_ofs];
        };
        this.akWriteI32 = function(arr,arr_ofs,buf_ofs,num)
        {
            buf_ofs += akAPI_Buff>>2;
            for (let i=0; i<num; i++)
                Module.HEAP32[i+buf_ofs] = arr[i+arr_ofs];
        };
        this.akGetStr = function(buf_ofs)
        {
            return UTF8ToString(akAPI_Buff+buf_ofs,0xFFFF-buf_ofs);
        };
        this.akSetStr = function(str,buf_ofs)
        {
            stringToUTF8(str,akAPI_Buff+buf_ofs,0xFFFF-buf_ofs);
        };

        this.akAPI_Back = Array(256);
        let cb = function(idx,fnc) 
        { 
            fnc = typeof fnc === 'function' ? fnc : null;
            akAPI_Back[idx] = fnc;

            // last 256 bits of api buffer contains
            // flags set if given cb is active
            let adr = akAPI_Buff+65536+(idx>>3);
            let flg = Module.HEAPU8[adr];
            let bit = 1<<(idx&0x7);
            flg = fnc ? flg|bit : flg&~bit; 
            Module.HEAPU8[adr] = flg;
        };

        this.ak = 
        {
            getPos : function(arr3, ofs) { akAPI_Call(0); akReadF32(arr3,ofs|0,0,3); },

            getDir : function() { akAPI_Call(2); return akGetF32(0); },

            getYaw : function() { akAPI_Call(4); return akGetF32(0); },

            getName : function() { akAPI_Call(6); return akGetStr(0); },

            getMount : function() { akAPI_Call(8); return akGetI32(0); },

            getAction : function() { akAPI_Call(10); return akGetI32(0); },

            getMoveIntent : function(arr3, ofs) { akAPI_Call(12); akReadF32(arr3,ofs|0,0,3); },
            requestMove : function(arr3, ofs) { akWriteF32(arr3,ofs|0,0,3); akAPI_Call(13); },

            getWater : function() { akAPI_Call(14); return akGetF32(0); },

            getLight : function(arr4, ofs) { akAPI_Call(16); akReadF32(arr4,ofs|0,0,4); },

            //////////////////////////////////////////////////////////////////////

            requestSay  : function(str) { akSetStr(String(str),0); akAPI_Call(100); },
            requestJump : function()    { akAPI_Call(101); },

            //////////////////////////////////////////////////////////////////////

            isGrounded : function() { akAPI_Call(200); return akGetI32(0)!=0; },

            //////////////////////////////////////////////////////////////////////
            onSay: function(fnc) { cb(0,fnc); },
            onItem: function(fnc) { cb(1,fnc); },
            onFrame: function(fnc) { cb(2,fnc); }
        };
       
        Object.freeze(ak);

        this.akAPI_CB = function(id)
        {
            let fnc = akAPI_Back[id];
            let ret,t;
            switch(id)
            {
                case 0: // onSay(str) -> bool
                    let str = akGetStr(0);
                    ret = fnc.apply(akAPI_This,[str]);

                    t = typeof ret;

                    if (t == 'boolean')
                        akSetI32(ret?1:0,0);
                    else
                        akSetI32(1,0);
                    break;

                case 1: // onItem(action,story,kind,subkind,weight,desc) -> bool/int/str/[int,str]/{story_id,desc}
                    let story_id = akGetI32(1);
                    let desc = akGetStr(20);
                    ret = fnc.apply(akAPI_This,[
                        akGetI32(0), story_id, akGetI32(2),
                        akGetI32(3), akGetI32(4), desc]);
                    
                    t = typeof ret;
                    if (t == 'boolean')
                    {
                        akSetI32(ret?1:0,0);
                        if (!ret)
                            break;
                    }

                    akSetI32(1,0);

                    if (t == 'number')
                    {
                        akSetI32(ret|0,4);
                        akSetStr(desc,8);
                    }
                    else
                    if (t == 'string')
                    {
                        akSetI32(story_id,4);
                        akSetStr(ret,8);
                    }
                    else
                    if (t == 'array')
                    {
                        if (typeof ret[0] == 'number')
                            akSetI32(ret[0]|4);
                        else
                            akSetI32(story_id,4);

                        if (typeof ret[1] == 'string')
                            akSetStr(ret[1],8);
                        else
                            akSetStr(desc,8);
                    }
                    else
                    if (t == 'object')
                    {
                        if (typeof ret.story_id == 'number')
                            akSetI32(ret.story_id|4);
                        else
                            akSetI32(story_id,4);

                        if (typeof ret.desc == 'string')
                            akSetStr(ret.desc,8);
                        else
                            akSetStr(desc,8);
                    }
                    else
                    {
                        akSetI32(story_id,4);
                        akSetStr(desc,8);
                    }
                    break;

                case 2:
                // onFrame()  
                {
                    ret = fnc.apply(akAPI_This);            
                    break;
                }
            }
        };

    }),-1,true);
}

// [FLOW:SCRIPT]
// Check if JavaScript callback is registered for given ID.
// WHY: O(1) bitfield check avoids linear search through 256-element callback array.
// Used before invoking akAPI_CB to skip callbacks that JavaScript didn't register.
//
// BITFIELD ENCODING:
// - Callback ID 0-255 maps to bit (id & 0x7) in byte at offset (id >> 3)
// - Byte 0 (akAPI_Buff[65536]) holds bits for callbacks 0-7
// - Byte 1 (akAPI_Buff[65537]) holds bits for callbacks 8-15
// - etc. (32 bytes total = 256 bits = 256 possible callbacks)
//
// PARAMETERS:
// - id: Callback slot (0=onSay, 1=onItem, 2=onFrame, 3-255 reserved)
//
// RETURNS: true if JavaScript registered a callback function for this ID
#if defined GAME || defined EMSCRIPTEN
bool akAPI_CheckCB(int id)
{
    int bit = 1<<(id&0x7);
    uint8_t* ptr = (uint8_t*)akAPI_Buff + 65536 + (id>>3);
    return (*ptr & bit) != 0;
}

// [FLOW:SCRIPT]
// C++ -> JavaScript callback: Player attempts to speak (chat message).
// WHY: Allows JavaScript to filter/modify chat messages before broadcasting.
//
// BUFFER PROTOCOL:
// - C++ writes: str at offset 0 (max 255 chars, null-terminated)
// - C++ invokes: akAPI_CB(0) → JavaScript onSay(str) handler
// - JavaScript writes: int32 at offset 0 (0=block, 1=allow)
// - C++ reads: allowed flag from offset 0
//
// PARAMETERS:
// - str: Chat message string (null-terminated)
// - len: String length (-1 = auto-detect via strlen, >255 = clamp to 255)
// - allowed: [out] true if JavaScript allowed message, false if blocked
//
// RETURNS: true if callback was invoked, false if no onSay handler registered
bool akAPI_OnSay(const char* str, int len,
                 bool* allowed)
{
    const int id = 0;
    if (!akAPI_CheckCB(id))
        return false;

    if (len<0)
        len=strlen(str);

    if (len>255)
        len=255;

    memcpy((char*)akAPI_Buff,str,len);
    ((char*)akAPI_Buff)[len] = 0;

    akAPI_CB(id);

    if (allowed)
        *allowed = *(int*)akAPI_Buff != 0;

    return true;
}

// [FLOW:SCRIPT]
// C++ -> JavaScript callback: Player interacts with inventory item.
// WHY: Allows JavaScript to handle custom item logic (consume, modify, transform items).
//
// BUFFER PROTOCOL (INPUT):
// - C++ writes: int32[0] = action (pickup, drop, use, etc.)
//               int32[1] = story_id (unique item ID in world)
//               int32[2] = kind (item category)
//               int32[3] = subkind (item variant)
//               int32[4] = weight
//               char[20..51] = desc (max 31 chars, null-terminated)
// - C++ invokes: akAPI_CB(1) → JavaScript onItem(action, story_id, kind, subkind, weight, desc)
//
// BUFFER PROTOCOL (OUTPUT):
// - JavaScript writes: int32[0] = 0 (block) or 1 (allow)
//                      int32[4] = modified story_id (if changed)
//                      char[8..] = modified desc (if changed)
// - C++ reads: allowed, out_story_id, out_desc from buffer
//
// WHY COMPLEX RETURN: JavaScript can return bool (allow/block), int (new story_id),
// string (new desc), array, or object with {story_id, desc} fields. C++ parses
// return type and extracts modifications.
//
// PARAMETERS:
// - action, story_id, kind, subkind, weight, desc: Item properties
// - allowed: [out] true if JavaScript allowed action
// - out_story_id: [out] Modified story_id (or original if unchanged)
// - out_desc: [out] Modified description (or original if unchanged)
//
// RETURNS: true if callback was invoked, false if no onItem handler registered
bool akAPI_OnItem(int action, int story_id, int kind, int subkind, int weight, const char* desc,
                  bool* allowed, int* out_story_id, const char** out_desc)
{
    const int id = 1;
    if (!akAPI_CheckCB(id))
        return false;

    int* ptr = (int*)akAPI_Buff;
    ptr[0] = action;
    ptr[1] = story_id;
    ptr[2] = kind;
    ptr[3] = subkind;
    ptr[4] = weight;

    int len=strlen(desc);
    if (len>31)
        len=31;

    memcpy((char*)akAPI_Buff+20,desc,len);
    ((char*)akAPI_Buff+20)[len] = 0;

    akAPI_CB(id);

    if (allowed)
        *allowed = *(int*)akAPI_Buff != 0;
    if (out_story_id)
        *out_story_id = *(int*)akAPI_Buff ? *((int*)akAPI_Buff+4) : story_id;
    if (out_desc)
        *out_desc = *(int*)akAPI_Buff ? (char*)akAPI_Buff+8 : desc;

    return true;
}

// [FLOW:SCRIPT]
// C++ -> JavaScript callback: Per-frame update tick.
// WHY: Allows JavaScript to run NPC AI logic every frame (60 Hz).
//
// BUFFER PROTOCOL:
// - No arguments or return values
// - C++ invokes: akAPI_CB(2) → JavaScript onFrame() handler
//
// TYPICAL USE: NPC behavior trees, pathfinding updates, state machines.
//
// RETURNS: true if callback was invoked, false if no onFrame handler registered
bool akAPI_OnFrame()
{
    const int id = 2;
    if (!akAPI_CheckCB(id))
        return false;

    akAPI_CB(id);
    return true;
}
#endif // defined GAME || defined EMSCRIPTEN

// Cleanup shared buffer allocation.
// WHY NOT IMPLEMENTED: akAPI_Buff allocated by platform-specific code
// (game_app.cpp via V8 external memory, game_web.cpp via Emscripten malloc).
// Platform owns deallocation responsibility.
void akAPI_Free()
{
    // allocated by platform specific thing
    // free(akAPI_Buff);
}

// [FLOW:SCRIPT]
// Command Dispatcher - JavaScript -> C++ API Entry Point
// WHY: Single dispatch function reduces WASM import overhead vs 20+ individual exports.
// JavaScript calls akAPI_Call(id) with arguments in shared buffer, C++ executes, writes results to buffer.
//
// DISPATCH TABLE (JavaScript API -> C++ Implementation):
// ┌────────┬──────────────────────────────┬─────────────────────────────────────────────────┐
// │ ID     │ JavaScript API               │ C++ Action                                      │
// ├────────┼──────────────────────────────┼─────────────────────────────────────────────────┤
// │ 0      │ ak.getPos(arr3, ofs)         │ Query player pose → write float[3] to buf[0]    │
// │ 1      │ reserved dead mutator        │ Phase 9 removed direct script mutation           │
// │ 2      │ ak.getDir()                  │ Query player pose → write dir to buf[0]         │
// │ 3      │ reserved dead mutator        │ Phase 9 removed direct script mutation           │
// │ 4      │ ak.getYaw()                  │ Query player pose → write yaw to buf[0]         │
// │ 5      │ reserved dead mutator        │ Phase 9 removed direct script mutation           │
// │ 6      │ ak.getName()                 │ Query player name → write string to buf[0]      │
// │ 7      │ reserved dead mutator        │ Phase 9 removed direct script mutation           │
// │ 8      │ ak.getMount()                │ Query mount state → write int32 to buf[0]       │
// │ 9      │ reserved dead mutator        │ Phase 9 removed direct script mutation           │
// │ 10     │ ak.getAction()               │ Query action state → write int32 to buf[0]      │
// │ 11     │ reserved dead mutator        │ Phase 9 removed direct script mutation           │
// │ 12     │ ak.getMoveIntent(arr3, ofs)  │ Query input intent → write float[3] to buf[0]   │
// │ 13     │ ak.requestMove(arr3, ofs)    │ Request move intent through the input owner      │
// │ 14     │ ak.getWater()                │ Query water level → write float to buf[0]       │
// │ 15     │ reserved dead mutator        │ Phase 9 removed direct script mutation           │
// │ 16     │ ak.getLight(arr4, ofs)       │ Query light → write float[4] to buf[0]          │
// │ 17     │ reserved dead mutator        │ Phase 9 removed direct script mutation           │
// │ 100    │ ak.requestSay(str)           │ Request speech action                            │
// │ 101    │ ak.requestJump()             │ Request jump action                              │
// │ 200    │ ak.isGrounded()              │ Query grounded state → write int32 to buf[0]    │
// └────────┴──────────────────────────────┴─────────────────────────────────────────────────┘
//
// BUFFER OFFSETS (documented per-case below):
// - float[3] at buf[0]: 3D position (x, y, z) or movement vector (vx, vy, vz)
// - float[4] at buf[0]: RGBA color (r, g, b, a)
// - float at buf[0]: Single scalar (direction angle, water level, yaw)
// - int32 at buf[0]: Integer flags (mount ID, action enum, grounded boolean)
// - string at buf[0]: Null-terminated UTF-8 text (player name, chat message)
//
// WHY GROUPED IDS:
// - 0-17: Query/request entry points
// - 100-101: Explicit action requests
// - 200+: Query functions (isGrounded)
//
// Arguments/Results exchanged via shared buffer `akAPI_Buff`.
extern "C" void akAPI_Call(int id)
{
    // Safety check: API calls blocked during main menu or before game initialization
    if (!game || game->ui.main_menu)
    {
        printf("game = NULL!\n");
        return;
    }

    ScriptRuntimeApi api(
        game->player,
        server != nullptr,
        game->input.api_move,
        (float)game->session.water,
        game->session.light,
        game->input.jump,
        game->player.prev_grounded
    );

    // [FLOW:SCRIPT] Dispatch to C++ implementation based on JavaScript API call ID
		    switch (id)
		    {
        case 0:
        // getPos: function(arr3, ofs) { akAPI_Call(0); akReadF32(arr3,ofs|0,0,3); }
        // BUFFER: writes float[3] at offset 0 (x, y, z world coordinates)
        {
            ScriptPlayerPose pose;
            api.GetPlayerPose(&pose);
            memcpy(akAPI_Buff, pose.pos, sizeof(pose.pos));
            break;
        }
        case 1:
        // Reserved mutator: Phase 9 removed direct script writes into player/world state.
        {
            break;
        }

        case 2: 
        // getDir: function() { akAPI_Call(2); return akGetF32(0); }
        {
            ScriptPlayerPose pose;
            api.GetPlayerPose(&pose);
            *(float*)akAPI_Buff = pose.dir;
            break;
        }
        case 3: 
        // Reserved mutator: Phase 9 removed direct script writes into player/world state.
        {
            break;
        }

        case 4: 
        // getYaw: function() { akAPI_Call(4); akGetF32(0); }
        {
            ScriptPlayerPose pose;
            api.GetPlayerPose(&pose);
            *(float*)akAPI_Buff = pose.yaw;
            break;
        }
        case 5: 
        // Reserved mutator: Phase 9 removed direct script writes into player/world state.
        {
            break;
        }

        case 6:
        // getName: function() { akAPI_Call(6); return akGetStr(0); },
        {
            api.GetPlayerName((char*)akAPI_Buff, 0xFFFF);
            break;
        }

        case 7:
        // Reserved mutator: Phase 9 removed direct script writes into player/world state.
        {
            break;
        }

        case 8:
        // getMount : function() { akAPI_Call(8); return akGetI32(0); },
        // Read-only query into the local authoritative runtime state.
        {
            *(int*)akAPI_Buff = api.GetMountState();
            break;
        }

        case 9: 
        // Reserved mutator: Phase 9 removed direct script writes into player/world state.
        {
            break;
        }

        case 10:
        // getAction : function() { akAPI_Call(10); return akGetI32(0); },
        // Read-only query into the local authoritative runtime state.
        {
            *(int*)akAPI_Buff = api.GetActionState();
            break;
        }

        case 11:
        // Reserved mutator: Phase 9 removed direct script writes into player/world state.
        {
            break;
        }

        case 12: 
        // getMoveIntent: function(arr3, ofs) { akAPI_Call(12); akReadF32(arr3,ofs|0,0,3); }
        {
            float move_intent[3];
            api.GetMoveIntent(move_intent);
            memcpy(akAPI_Buff, move_intent, sizeof(move_intent));
            break;
        }
        case 13: 
        // requestMove: function(arr3, ofs) { akWriteF32(arr3,ofs|0,0,3); akAPI_Call(13); }
        {
            api.RequestMove((const float*)akAPI_Buff);
            break;
        }

        case 14: 
        // getWater : function() { akAPI_Call(14); return akGetF32(0); },
        {
            *(float*)akAPI_Buff = api.GetWaterLevel();
            break;
        }
        case 15:
        // Reserved mutator: Phase 9 removed direct script writes into player/world state.
        {
            break;
        }

        case 16: 
        // getLight : function(arr4, ofs) { akAPI_Call(16); akReadF32(arr4,ofs|0,0,4); },
        {
            float light[4];
            api.GetLightState(light);
            memcpy(akAPI_Buff, light, sizeof(light));
            break;
        }
        case 17:
        // Reserved mutator: Phase 9 removed direct script writes into player/world state.
        {
            break;
        }

        ////////////////////////////////////////////////////////////////////////

        case 100:
        // requestSay : function(str) { akSetStr(str,0); akAPI_Call(100); },
        // BUFFER: reads null-terminated UTF-8 string from offset 0
        // WHY: Scripts request a speech action; gameplay decides when it lands.
        {
            api.RequestSay((const char*)akAPI_Buff);
            break;
        }

        case 101:
        // requestJump : function() { akAPI_Call(101); },
        // WHY: Writes only to the input-intent owner; physics consumes it next frame.
        {
            api.RequestJump();
            break;
        }

        ////////////////////////////////////////////////////////////////////////

        case 200: 
        // isGrounded : function() { akAPI_Call(200); return akGetI32(0)!=0; },
        {
            *(int*)akAPI_Buff = api.IsGrounded() ? 1 : 0;
            break;
        }        

        default:
            break;
    }
}
