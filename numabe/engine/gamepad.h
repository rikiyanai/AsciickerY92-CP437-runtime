// =============================================================================
// Gamepad Mapping Configuration — Public API
// =============================================================================
//
// PURPOSE:
// Public API for gamepad mapping configuration system. Provides functions for
// loading gamepad visual assets, connecting/disconnecting gamepad devices,
// managing button/axis mappings, and rendering the configuration UI.
//
// MAPPING STORAGE:
// - gamepad_mapping[256]:  Input index → Output index (0xFF = unmapped)
//   - Input indices:   Raw button/axis states from platform backend (SDL)
//   - Output indices:  Logical game actions (0-5 axes, 6-20 buttons)
//   - 0xFF:            Unmapped input (ignored)
//
// OUTPUT INDEX TABLE (21 total):
//   Index  Type    Name    Description
//   0      Axis    Ll      Left stick left
//   1      Axis    Lr      Left stick right
//   2      Axis    Lu      Left stick up
//   3      Axis    Ld      Left stick down
//   4      Axis    Rl      Right stick left
//   5      Axis    Rr      Right stick right
//   (6-11  Axis    Ru,Rd,Lt,Rt — implemented as half-axes in UI, not in output table)
//   6      Button  A       Primary action
//   7      Button  B       Secondary action
//   8      Button  X       Tertiary action
//   9      Button  Y       Quaternary action
//   10     Button  E       Extra button 1
//   11     Button  G       Extra button 2
//   12     Button  F       Extra button 3
//   13     Button  L       Left bumper
//   14     Button  R       Right bumper
//   15     Button  Ls      Left stick press
//   16     Button  Rs      Right stick press
//   17     Button  Du      D-pad up
//   18     Button  Dd      D-pad down
//   19     Button  Dl      D-pad left
//   20     Button  Dr      D-pad right
//
// FUNCTIONS:
// - LoadGamePad():                   Load gamepad sprite from disk (.xp file)
// - FreeGamePad():                   Unload gamepad sprite
// - SetGamePadMapping(map):          Load mapping from array (256 bytes binary)
// - GetGamePadMapping():             Return current mapping pointer (256 bytes)
// - GetGamePad(axes, buttons):       Return gamepad name and axis/button counts
// - ConnectGamePad(name, axes, buttons, mapping[]): Called on gamepad mount
// - DisconnectGamePad():             Called on gamepad unmount
// - UpdateGamePadButton(b, pos, out[1]): Apply mapping to button event
// - UpdateGamePadAxis(a, pos, out[4]): Apply mapping to axis event
// - PaintGamePad(ptr, width, height, stamp): Render visual UI
// - GamePadOpen(close, g):           Open gamepad config UI
// - GamePadContact(id, ev, x, y, stamp): Handle mouse/touch input
// - GamePadKeyb(key, stamp):         Handle keyboard input
//
// GamePadContact PARAMETERS:
//   id = contact ID (0:lmb, 1:touch0, 2:touch1, ...)
//   ev = event type (0:begin, 1:move, 2:end, 3:cancel)
//   x,y = screen coordinates
//   stamp = timestamp for animation
//
// GamePadKeyb PARAMETERS:
//   key = key code (5:left, 6:right, 3:up, 4:down, 1:enter, 0:space,
//                    2:backslash/escape/backspace, 7:c/C, 8:r/R)
//   stamp = timestamp for animation
// =============================================================================

#ifndef GAMEPAD_H
#define GAMEPAD_H

void LoadGamePad();
void FreeGamePad();

void SetGamePadMapping(const uint8_t* map);
const uint8_t* GetGamePadMapping();
const char* GetGamePad(int* axes, int* buttons);

void ConnectGamePad(const char* name, int axes, int buttons, const uint8_t mapping[]);
void DisconnectGamePad();

// return num of outs
int UpdateGamePadAxis(int a, int16_t pos, uint32_t out[4]);
int UpdateGamePadButton(int b, int16_t pos, uint32_t out[1]);

void PaintGamePad(AnsiCell* ptr, int width, int height, uint64_t stamp);

void GamePadOpen( void (*close)(void* _g), void* g );

void GamePadContact(int id, int ev, int x, int y, uint64_t stamp);
/*
    id = contact (0:lmb, 1:touch0, 2:touch1, ...)
    ev = 0:begin 1;move 2:end 3:cancel
*/

void GamePadKeyb(int key, uint64_t stamp);
/*
    key = 5:l,6:r,3:u,4:d, 1:enter, 0:space, 2:(backslash,escape,backspace), 7:(c,C), 8:(r,R)
*/

#endif
