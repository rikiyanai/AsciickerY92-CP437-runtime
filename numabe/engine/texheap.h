// texheap.h - Texture Atlas Heap Allocator Interface
//
// Purpose: Manages GPU texture pages with grid-based sub-allocation.
// Sprites and other small textures are packed into large atlas pages
// to minimize OpenGL texture bind calls during rendering.
//
// WHY page-based model: Each TexPage is a single GPU texture divided into
// a grid of cap_x * cap_y uniform cells. All cells in a heap share the
// same pixel dimensions (item_w x item_h). This means one glBindTexture
// covers an entire page of sprites, dramatically reducing draw call
// overhead compared to one-texture-per-sprite.
//
// WHY TEXHEAP_MAX_NUMTEX=4: A single allocation can span up to 4 texture
// layers (e.g., diffuse color + normal map + material properties + mask).
// All layers share the same grid layout but may have different internal
// pixel formats (TexDesc.ifmt), allowing heterogeneous texture data to
// be allocated and freed as a single unit.

#pragma once
#include "gl.h"

#define TEXHEAP

#define TEXHEAP_MAX_NUMTEX 4

struct TexHeap;
struct TexAlloc;
struct TexPage;

// WHY format/type/data are separated: Matches the OpenGL glTexSubImage2D
// interface directly (format=GL_RGBA, type=GL_UNSIGNED_BYTE, data=pixels).
// This avoids an extra translation step when uploading to the GPU.
struct TexData
{
	GLenum format;
	GLenum type;
	void* data;
};

// WHY user pointer exists: Allows callers to associate arbitrary data with
// each allocation (e.g., sprite metadata, animation frame info). The heap
// manages GPU texture slots; the caller manages game-level meaning.
struct TexAlloc
{
	// read-write
	void* user;

	// read-only
	TexPage* page;
	int x, y;               // grid coordinates within the page

	void Update(int first, int count, const TexData[]);
	TexAlloc* Free();
};

// WHY alloc array is declared as alloc[1]: C-style flexible array member.
// The actual allocation is malloc'd with sizeof(TexPage) + extra slots,
// so alloc[] holds cap_x*cap_y pointers. This avoids a second allocation
// for the pointer array and keeps the page as one contiguous block.
struct TexPage
{
	// read only
	void* user; // data at the pointer can be modified
	TexHeap* heap;
	TexPage* next;       // WHY doubly-linked: pages are created on demand
	TexPage* prev;       // and destroyed when empty, requiring O(1) removal
	GLuint tex[TEXHEAP_MAX_NUMTEX];
	TexAlloc* alloc[1]; // [heap->cap_x*heap->cap_y]
};

// WHY per-texture item dimensions and internal format: Different texture
// layers may have different pixel formats (e.g., RGBA8 for diffuse,
// RG8 for normal map). Each TexDesc defines one layer's cell size and
// OpenGL internal format independently.
struct TexDesc
{
	int item_w;
	int item_h;
	GLenum ifmt;
};

// WHY doubly-linked page list: Pages are created dynamically when existing
// pages fill up and destroyed when emptied. A doubly-linked list gives
// O(1) insertion at tail and O(1) removal of any page.
struct TexHeap
{
	void Create(int page_cap_x, int page_cap_y, int numtex, const TexDesc* texdesc, int page_userbytes);
	void Destroy();

	TexAlloc* Alloc(const TexData data[]);

	// spatial optimizer support
	// simply swap TexAlloc pointers and TexAlloc::user data
	// then update both allocs

	// const read-only

	int cap_x;            // grid columns per page
	int cap_y;            // grid rows per page

	// read only
	int user; // num of extra bytes allocated with every page for user
	int allocs;           // total allocations across all pages

	TexPage* head;        // first page in doubly-linked list
	TexPage* tail;        // last page (new allocs go here)

	/*
	int item_w;
	int item_h;
	GLenum ifmt;
	*/

	int num;
	TexDesc tex[TEXHEAP_MAX_NUMTEX];
};
