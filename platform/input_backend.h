// platform/input_backend.h — Keyboard/mouse/focus input contract
//
// PURPOSE: Input event enums, callback interface, and query functions.
// Extracted from platform/platform.h as part of the platform abstraction split.
//
// BACKENDS: sdl.cpp, x11.cpp, mswin.cpp, game_web.cpp (Emscripten)
//
// SEE ALSO: platform.h (umbrella shim)

#pragma once

#include <stdint.h>

struct A3D_WND;

// Mouse event enumeration
enum MouseInfo
{
	// messages
	MOVE = 1,
	LEFT_DN = 2,
	LEFT_UP = 3,
	RIGHT_DN = 4,
	RIGHT_UP = 5,
	MIDDLE_DN = 6,
	MIDDLE_UP = 7,
	WHEEL_UP = 8,
	WHEEL_DN = 9,
	ENTER = 10,
	LEAVE = 11,

	// but flags
	LEFT = 0x10,
	RIGHT = 0x20,
	MIDDLE = 0x40,

	INSIDE = 0x80
};

// Keyboard key codes
enum KeyInfo
{
	A3D_NONE = 0,

	A3D_BACKSPACE,
	A3D_TAB,
	A3D_ENTER,

	A3D_PAUSE,
	A3D_ESCAPE,

	A3D_SPACE,
	A3D_PAGEUP,
	A3D_PAGEDOWN,
	A3D_END,
	A3D_HOME,
	A3D_LEFT,
	A3D_UP,
	A3D_RIGHT,
	A3D_DOWN,

	A3D_PRINT,
	A3D_INSERT,
	A3D_DELETE,

	A3D_0,
	A3D_1,
	A3D_2,
	A3D_3,
	A3D_4,
	A3D_5,
	A3D_6,
	A3D_7,
	A3D_8,
	A3D_9,

	A3D_A,
	A3D_B,
	A3D_C,
	A3D_D,
	A3D_E,
	A3D_F,
	A3D_G,
	A3D_H,
	A3D_I,
	A3D_J,
	A3D_K,
	A3D_L,
	A3D_M,
	A3D_N,
	A3D_O,
	A3D_P,
	A3D_Q,
	A3D_R,
	A3D_S,
	A3D_T,
	A3D_U,
	A3D_V,
	A3D_W,
	A3D_X,
	A3D_Y,
	A3D_Z,

	A3D_LWIN,
	A3D_RWIN,
	A3D_APPS,

	A3D_NUMPAD_0,
	A3D_NUMPAD_1,
	A3D_NUMPAD_2,
	A3D_NUMPAD_3,
	A3D_NUMPAD_4,
	A3D_NUMPAD_5,
	A3D_NUMPAD_6,
	A3D_NUMPAD_7,
	A3D_NUMPAD_8,
	A3D_NUMPAD_9,
	A3D_NUMPAD_MULTIPLY,
	A3D_NUMPAD_DIVIDE,
	A3D_NUMPAD_ADD,
	A3D_NUMPAD_SUBTRACT,
	A3D_NUMPAD_DECIMAL,
	A3D_NUMPAD_ENTER,

	A3D_F1,
	A3D_F2,
	A3D_F3,
	A3D_F4,
	A3D_F5,
	A3D_F6,
	A3D_F7,
	A3D_F8,
	A3D_F9,
	A3D_F10,
	A3D_F11,
	A3D_F12,
	A3D_F13,
	A3D_F14,
	A3D_F15,
	A3D_F16,
	A3D_F17,
	A3D_F18,
	A3D_F19,
	A3D_F20,
	A3D_F21,
	A3D_F22,
	A3D_F23,
	A3D_F24,

	A3D_CAPSLOCK,
	A3D_NUMLOCK,
	A3D_SCROLLLOCK,

	A3D_LSHIFT,
	A3D_RSHIFT,
	A3D_LCTRL,
	A3D_RCTRL,
	A3D_LALT,
	A3D_RALT,

	A3D_OEM_COLON,		// ';:' for US
	A3D_OEM_PLUS,		// '=+' any country
	A3D_OEM_COMMA,		// ',<' any country
	A3D_OEM_MINUS,		// '-_' any country
	A3D_OEM_PERIOD,		// '.>' any country
	A3D_OEM_SLASH,		// '/?' for US
	A3D_OEM_TILDE,		// '`~' for US

	A3D_OEM_OPEN,       //  '[{' for US
	A3D_OEM_CLOSE,      //  ']}' for US
	A3D_OEM_BACKSLASH,  //  '\|' for US
	A3D_OEM_QUOTATION,  //  ''"' for US

	A3D_MAPEND,
	A3D_AUTO_REPEAT = 256
};

// Platform backend callback interface
struct PlatformInterface
{
	void(*init)(A3D_WND* wnd);
	void(*render)(A3D_WND* wnd);
	void(*resize)(A3D_WND* wnd, int w, int h);
	void(*close)(A3D_WND* wnd);
	void(*keyb_key)(A3D_WND* wnd, KeyInfo vk, bool down);
	void(*keyb_char)(A3D_WND* wnd, wchar_t ch);
	void(*keyb_focus)(A3D_WND* wnd, bool set);
	void(*mouse)(A3D_WND* wnd, int x, int y, MouseInfo mi);

	void(*image)(void* cookie, int width, int height, int channels, int depth, void* data);
	void(*sound)(void* cookie, int samples, int channels, int depth, void* data);
};

// Input state queries
MouseInfo a3dGetMouse(A3D_WND* wnd, int* x, int* y);
bool a3dGetKeyb(A3D_WND* wnd, KeyInfo ki);
bool a3dGetFocus(A3D_WND* wnd);
void a3dSetFocus(A3D_WND* wnd);
void a3dCharSync(A3D_WND* wnd);
