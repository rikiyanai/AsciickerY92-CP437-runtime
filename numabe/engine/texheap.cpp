// texheap.cpp - Grid-Based Texture Atlas Allocator Implementation
//
// Purpose: Implements the TexHeap allocator that packs sprite frames and
// other small textures into large GPU atlas pages. Each page is a single
// OpenGL texture divided into a uniform grid of cells.
//
// Algorithm: Alloc() appends to the tail page's next free slot (simple
// sequential fill). When the tail page is full, a new page is created
// with fresh GPU textures. Free() swaps the freed slot with the last
// allocated slot (compaction), then releases the tail page if empty.
//
// WHY simple sequential allocation: All items in a TexHeap share the same
// cell dimensions, so there is no fragmentation concern. First-fit within
// a page is equivalent to best-fit when all cells are identical size.
// The swap-on-free strategy keeps allocations dense without requiring a
// free-list or defragmentation pass.
//
// Relationship: Used by sprite loading (sprite.cpp) to pack sprite frames
// into GPU texture atlases. Provides TexAlloc handles that sprite.cpp
// stores per-frame for rendering with correct UV coordinates.

#include <stdlib.h>
#include <string.h>
#include "texheap.h"
#include "gl45_emu.h"

// WHY page geometry is configured at creation: All items in a heap share
// the same cell size, so grid dimensions are fixed once. This allows the
// allocator to use simple index arithmetic (allocs % cap) instead of
// tracking variable-sized regions.
void TexHeap::Create(int page_cap_x, int page_cap_y, int numtex, const TexDesc* texdesc, int page_user_bytes)
{
	allocs = 0;
	cap_x = page_cap_x;
	cap_y = page_cap_y;
	user = page_user_bytes;
	head = 0;
	tail = 0;

	// item_w = alloc_w;
	// item_h = alloc_h;
	// ifmt = internal_format;

	num = numtex;
	memcpy(tex, texdesc, num * sizeof(TexDesc));
}

void TexHeap::Destroy()
{
	if (!head)
		return;

	int cap = cap_x * cap_y;
	TexPage* p = head;
	while (p!=tail)
	{
		TexPage* n = p->next;
		glDeleteTextures(num, p->tex);
		for (int i = 0; i < cap; i++)
			free(p->alloc[i]);
		free(p);
		p = n;
	}

	cap = allocs % cap;
	glDeleteTextures(num, p->tex);
	for (int i = 0; i < cap; i++)
		free(p->alloc[i]);
	free(p);
}

// WHY a new page is created when existing pages are full: Demand-driven
// page growth avoids pre-allocating GPU memory for sprites that may never
// be loaded. Each new page creates TEXHEAP_MAX_NUMTEX OpenGL textures
// sized to hold cap_x*cap_y cells, then the requested data is uploaded
// to the first free cell via glTexSubImage2D.
TexAlloc* TexHeap::Alloc(const TexData data[])
{
	int cap = cap_x * cap_y;
	TexPage* page = tail;
	if (allocs % cap == 0)
	{
		int alloc_bytes = sizeof(TexPage) + sizeof(TexAlloc*)*(cap - 1);
		int page_bytes = alloc_bytes + user;
		page = (TexPage*)malloc(page_bytes);
		if (!page)
			return 0;

		page->user = (char*)page + alloc_bytes;
		memset(page->user, 0, user);

		page->heap = this;
		page->prev = tail;
		page->next = 0;
		if (tail)
			tail->next = page;
		else
			head = page;
		tail = page;
		
		gl3CreateTextures(GL_TEXTURE_2D, num, page->tex);
		for (int t = 0; t < num; t++)
		{
			gl3TextureStorage2D(page->tex[t], 1, tex[t].ifmt, cap_x * tex[t].item_w, cap_y * tex[t].item_h);
			gl3TextureParameteri2D(page->tex[t], GL_TEXTURE_MIN_FILTER, GL_NEAREST);
			gl3TextureParameteri2D(page->tex[t], GL_TEXTURE_MAG_FILTER, GL_NEAREST);
			gl3TextureParameteri2D(page->tex[t], GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
			gl3TextureParameteri2D(page->tex[t], GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
		}
	}

	int on_page = allocs % cap;

	TexAlloc* a = (TexAlloc*)malloc(sizeof(TexAlloc));
	page->alloc[on_page] = a;
	a->page = page;
	a->x = on_page % cap_x;
	a->y = on_page / cap_x;

	allocs++;

	glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
	for (int t = 0; t < num; t++)
	{
		gl3TextureSubImage2D(page->tex[t], 0, a->x * tex[t].item_w, a->y * tex[t].item_h,
			tex[t].item_w, tex[t].item_h, data[t].format, data[t].type, data[t].data);
	}
	glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
	return a;
}

// WHY swap-with-last on free: Keeps all live allocations densely packed
// at the front of the page array. The freed slot receives the last
// allocation's texture data (via glCopyImageSubData on GL4, or caller
// re-upload on GL3). This avoids holes and allows tail page release.
// WHY page release on empty: When the last cell on the tail page is freed,
// the page's GPU textures are deleted and the page is removed from the
// linked list, reclaiming GPU memory when sprites are unloaded.
TexAlloc* TexAlloc::Free() // return last alloc which must be re-updated (GL3 only)
{
	TexHeap* h = page->heap;
	int cap = h->cap_x * h->cap_y;
	int on_page = y * h->cap_x + x;

	TexAlloc* last = 0;

	// not last alloc on last page?
	if (page != h->tail || on_page != (h->allocs-1) % cap)
	{
		last = h->tail->alloc[(h->allocs-1) % cap];

		#if !USE_GL3
		for (int t = 0; t < h->num; t++)
		{
			gl3CopyImageSubData(
				last->page->tex[t], GL_TEXTURE_2D, 0, last->x * h->tex[t].item_w, last->y * h->tex[t].item_h, 0,
				page->tex[t], GL_TEXTURE_2D, 0, x * h->tex[t].item_w, y * h->tex[t].item_h, 0,
				h->tex[t].item_w, h->tex[t].item_h, 1);
		}
		#endif

		page->alloc[on_page] = last;
		last->page = page;
		last->x = x;
		last->y = y;

		#if !USE_GL3
		last = 0;
		#endif
	}

	h->allocs--;
	free(this);

	// now check if we can delete last page 
	if (h->allocs % cap == 0)
	{
		// Delete the page that became empty after compaction, not the new tail.
		TexPage* old_tail = h->tail;
		glDeleteTextures(h->num, old_tail->tex);
		if (old_tail->prev)
			old_tail->prev->next = 0;
		else
			h->head = 0;
		h->tail = old_tail->prev;
		free(old_tail);
	}

	return last;
}

// WHY per-cell texture update exists: Allows streaming new pixel data
// into an existing allocation without freeing and re-allocating the slot.
// Used when sprite animation frames change or texture data is regenerated.
// The first/count parameters select which texture layers to update,
// enabling partial updates (e.g., only refresh the diffuse layer).
void TexAlloc::Update(int first, int count, const TexData data[])
{
	TexHeap* h = page->heap;
	int end = first + count;
	end = h->num < end ? h->num : end;
	glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
	for (int t = first; t < end; t++)
	{
		gl3TextureSubImage2D(page->tex[t], 0, x * h->tex[t].item_w, y * h->tex[t].item_h,
			h->tex[t].item_w, h->tex[t].item_h, data[t-first].format, data[t-first].type, data[t-first].data);
	}
	glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
}
