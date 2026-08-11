// screen.cpp - Screen Layer Compositing System
//
// Purpose: Manages multiple overlapping UI layers with z-ordering, focus
// dispatch, and blending. Each Screen owns a doubly-linked list of child
// Layers; the Merge function composites AnsiCell buffers back-to-front.
//
// Status: UNIMPLEMENTED - the entire file is wrapped in #if 0.
// The architecture was designed (structs, merge traversal, hit-testing)
// but never completed. The game currently renders directly to a single
// output buffer in render.cpp without a layer compositing pass.
//
// Design intent:
//   Screen -> Layer -> Layer -> ...   (sibling linked list, z-ordered)
//   Each Layer is itself a Screen (recursive composition).
//   Merge() walks children front-to-back, blitting AnsiCell data.
//   HitTest() finds the topmost opaque layer under a coordinate.
//
// Relationship: Would sit between render.cpp output and final terminal
// display, enabling game-view + UI-overlay composition (e.g., inventory
// panel over the 3D viewport). Currently bypassed entirely.



// this is screen manager
// handling multiple layers (creation, destruction, order, focus, position and blending effects)
// it also receives input stream and dispatches it to given layer input handler
// in xterm mode it is responsible for displaying mouse cursor on top of screen

#include "render.h"

#if 0

struct ScreenCB
{
	void(*touch)();
	void(*mouse)();
	void(*keyb)(); // (focused screen only) 
	void(*pad)();  // (focused screen only) 
};

struct Layer;

struct Screen
{
	Layer* parent; // if null it is screen!
	void* cookie;

	// children
	Layer* head;
	Layer* tail;

	// buf should be cleared or prerendered with some scene or left dirty if children overlaps it fully
	void Merge(AnsiCell* buf, int width, int height);
};

struct Layer : Screen
{
	bool visible;

	// siblings
	Layer* prev;
	Layer* next;
};


void Screen::Merge(AnsiCell* buf, int width, int height)
{
	ClipRect cr = { 0,0, width,height };
	Layer* lay = head;
	while (lay)
	{
		cr.x1 = max(0, lay->x, )
		lay->Merge(buf, &cr, lay->x, lay->y);
		lay = lay->next;
	}
}

void Layer::Merge(AnsiCell* buf, int width, int height, int src_x, int src_y, int dst_x, int dst_y, int )
{
	cr->x1 += x;
	cr->y1 += x;
	cr->x2 += x;
	cr->y2 += x;
}

// for mouse & touch
Screen* HitTest(Screen* root, int x, int y)
{
	// 1. locate topmost screen for root
	// 2. traverse down until non transparent bg or fg is found
}

#endif
