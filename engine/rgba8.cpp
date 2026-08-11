// rgba8.cpp - Pixel Format Conversion Library
//
// Purpose: Converts between any A3D_ImageFormat and the engine's internal
// uint32 packed-pixel representation. Handles luminance (1/2/4/8/16-bit),
// indexed with palette (1/2/4/8-bit), RGB/RGBA (8/16-bit), and
// luminance-alpha variants.
//
// WHY multiple output formats: Different platforms require different byte
// orders in their 32-bit pixels:
//   AABBGGRR (R=shift 0)  - OpenGL GL_RGBA on little-endian hardware
//   AARRGGBB (R=shift 16) - Native display (Windows GDI, macOS bitmaps)
//   unsigned long variant  - X11 XImage compatibility (8-byte pixels on LP64)
//
// WHY macro-based conversion (L_UNPACK*, I_UNPACK*, RGB_UNPACK*, etc.):
//   Each macro is parameterized by (R,G,B,A) shift amounts and output type.
//   This generates specialized per-pixel code at compile time with zero
//   dispatch overhead. A function-pointer approach would add a branch per
//   pixel; macros let the compiler inline and optimize the bit-shifts.
//   The tradeoff is code size (each Convert function expands all macros)
//   but pixel conversion is performance-critical for texture loading.
//
// Macro naming convention:
//   L_UNPACK{N}  - Luminance, N bits per pixel (grayscale, no palette)
//   I_UNPACK{N}  - Indexed, N bits per pixel (palette lookup via palbuf)
//   RGB_UNPACK{N} - RGB, N bits per channel
//   RGBA_UNPACK{N} - RGBA, N bits per channel
//   LA_UNPACK{N}  - Luminance + Alpha, N bits per channel
//
// Relationship: Used by texture loading in texheap.cpp, sprite rendering
// in sprite.cpp, and display output in render.cpp / platform backends.

#include <string.h>
#include "rgba8.h"


#define L_UNPACK1(w,h,data,buf,type,R,G,B,A) \
{ \
	int in_row = (w+7) >> 3; \
	const uint8_t* in_line = (const uint8_t*)data; \
	type* out_line = (type*)buf; \
	int wq = w>>3; \
	int wr = (8 - (w&0x7)) & 0x7; \
	for (int y = 0; y < h; y++) \
	{ \
		int x = 0; \
		for (int i = 0; i < wq; i++) \
		{ \
			uint8_t l; \
			uint8_t v = in_line[i]; \
			l = 255*((v>>7)&1); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 255*((v>>6)&1); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 255*((v>>5)&1); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 255*((v>>4)&1); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 255*((v>>3)&1); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 255*((v>>2)&1); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 255*((v>>1)&1); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 255*(v&1);      out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
		} \
		if (wr) \
		{ \
			uint8_t l; \
			uint8_t v = in_line[wq]; \
			for (int i = 7; i >= wr; i--) \
			{ \
				l = 255*((v>>i)&1); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			} \
		} \
		in_line += in_row; \
		out_line += w; \
	} \
}

#define L_UNPACK2(w,h,data,buf,type,R,G,B,A) \
{ \
	int in_row = (w+3) >> 2; \
	const uint8_t* in_line = (const uint8_t*)data; \
	type* out_line = (type*)buf; \
	int wq = w>>2; \
	int wr = 2*((4 - (w&0x3)) & 0x3); \
	for (int y = 0; y < h; y++) \
	{ \
		int x = 0; \
		for (int i = 0; i < wq; i++) \
		{ \
			uint8_t l; \
			uint8_t v = in_line[i]; \
			l = 85*((v>>6)&3); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 85*((v>>4)&3); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 85*((v>>2)&3); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 85*(v&3);      out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
		} \
		if (wr) \
		{ \
			uint8_t l; \
			uint8_t v = in_line[wq]; \
			for (int i = 6; i >= wr; i-=2) \
			{ \
				l = 85*((v>>i)&3); out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			} \
		} \
		in_line += in_row; \
		out_line += w; \
	} \
}

#define L_UNPACK4(w,h,data,buf,type,R,G,B,A) \
{ \
	int in_row = (w+1) >> 1; \
	const uint8_t* in_line = (const uint8_t*)data; \
	type* out_line = (type*)buf; \
	int wq = w>>1; \
	int wr = 4*((2 - (w&0x1)) & 0x1); \
	for (int y = 0; y < h; y++) \
	{ \
		int x = 0; \
		for (int i = 0; i < wq; i++) \
		{ \
			uint8_t l; \
			uint8_t v = in_line[i]; \
			l = 17*((v>>4)&0xF); 	out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
			l = 17*(v&0xF);        	out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
		} \
		if (wr) \
		{ \
			uint8_t l; \
			uint8_t v = in_line[wq]; \
			l = 17*((v>>4)&0xF); 	out_line[x++] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
		} \
		in_line += in_row; \
		out_line += w; \
	} \
}

#define L_UNPACK8(w,h,data,buf,type,R,G,B,A) \
{ \
	const uint8_t* src = (const uint8_t*)data; \
	int n = w * h; \
	type* out_buf=(type*)buf; \
	for (int i = 0; i < n; i++) \
	{ \
		uint8_t l = src[i]; \
		out_buf[i] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
	} \
}

#define L_UNPACK16(w,h,data,buf,type,R,G,B,A) \
{ \
	const uint8_t* src = (const uint8_t*)data; \
	int n = w * h; \
	type* out_buf=(type*)buf; \
	for (int i = 0; i < n; i++) \
	{ \
		uint8_t l = src[2*i]; \
		out_buf[i] = (l<<R) | (l<<G) | (l<<B) | (0xFF<<A); \
	} \
}

#define I_UNPACK1(w,h,data,buf,type,R,G,B,A,palsize,palbuf) \
{ \
	int in_row = (w+7) >> 3; \
	const uint8_t* in_line = (const uint8_t*)data; \
	type* out_line = (type*)buf; \
	const uint8_t* pal = (const uint8_t*)palbuf; \
	palsize*=4; \
	int wq = w>>3; \
	int wr = (8 - (w&0x7)) & 0x7; \
	for (int y = 0; y < h; y++) \
	{ \
		int x = 0; \
		for (int i = 0; i < wq; i++) \
		{ \
			int l; \
			uint8_t v = in_line[i]; \
			l = 4*((v>>7)&1); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*((v>>6)&1); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*((v>>5)&1); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*((v>>4)&1); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*((v>>3)&1); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*((v>>2)&1); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*((v>>1)&1); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*(v&1);      out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
		} \
		if (wr) \
		{ \
			int l; \
			uint8_t v = in_line[wq]; \
			for (int i = 7; i >= wr; i--) \
			{ \
				l = 4*((v>>i)&1); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			} \
		} \
		in_line += in_row; \
		out_line += w; \
	} \
}

#define I_UNPACK2(w,h,data,buf,type,R,G,B,A,palsize,palbuf) \
{ \
	int in_row = (w+3) >> 2; \
	const uint8_t* in_line = (const uint8_t*)data; \
	type* out_line = (type*)buf; \
	const uint8_t* pal = (const uint8_t*)palbuf; \
	palsize*=4; \
	int wq = w>>2; \
	int wr = 2*((4 - (w&0x3)) & 0x3); \
	for (int y = 0; y < h; y++) \
	{ \
		int x = 0; \
		for (int i = 0; i < wq; i++) \
		{ \
			int l; \
			uint8_t v = in_line[i]; \
			l = 4*((v>>6)&3); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*((v>>4)&3); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*((v>>2)&3); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*(v&3);      out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
		} \
		if (wr) \
		{ \
			int l; \
			uint8_t v = in_line[wq]; \
			for (int i = 6; i >= wr; i-=2) \
			{ \
				l = 4*((v>>i)&3); out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			} \
		} \
		in_line += in_row; \
		out_line += w; \
	} \
}

#define I_UNPACK4(w,h,data,buf,type,R,G,B,A,palsize,palbuf) \
{ \
	int in_row = (w+1) >> 1; \
	const uint8_t* in_line = (const uint8_t*)data; \
	type* out_line = (type*)buf; \
	const uint8_t* pal = (const uint8_t*)palbuf; \
	palsize*=4; \
	int wq = w>>1; \
	int wr = 4*((2 - (w&0x1)) & 0x1); \
	for (int y = 0; y < h; y++) \
	{ \
		int x = 0; \
		for (int i = 0; i < wq; i++) \
		{ \
			int l; \
			uint8_t v = in_line[i]; \
			l = 4*((v>>4)&0xF); 	out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
			l = 4*(v&0xF);        	out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
		} \
		if (wr) \
		{ \
			int l; \
			uint8_t v = in_line[wq]; \
			l = 4*((v>>4)&0xF); 	out_line[x++] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
		} \
		in_line += in_row; \
		out_line += w; \
	} \
}

#define I_UNPACK8(w,h,data,buf,type,R,G,B,A,palsize,palbuf) \
{ \
	const uint8_t* src = (const uint8_t*)data; \
	const uint8_t* pal = (const uint8_t*)palbuf; \
	palsize*=4; \
	type* out_buf=(type*)buf; \
	int n = w * h; \
	for (int i = 0; i < n; i++) \
	{ \
		int l = 4*src[i]; \
		out_buf[i] = l>=palsize ? 0 : (pal[l+0]<<R) | (pal[l+1]<<G) | (pal[l+2]<<B) | (pal[l+3]<<A); \
	} \
}

#define RGB_UNPACK8(w,h,data,buf,type,R,G,B,A) \
{ \
	const uint8_t* src = (const uint8_t*)data; \
	int n = w * h; \
	type* out_buf=(type*)buf; \
	for (int i = 0; i < n; i++) \
	{ \
		int j = 3*i; \
		out_buf[i] = (src[j+0]<<R) | (src[j+1]<<G) | (src[j+2]<<B) | (0xFF<<A); \
	} \
}

#define RGB_UNPACK16(w,h,data,buf,type,R,G,B,A) \
{ \
	const uint8_t* src = (const uint8_t*)data; \
	int n = w * h; \
	type* out_buf=(type*)buf; \
	for (int i = 0; i < n; i++) \
	{ \
		int j = 6*i; \
		out_buf[i] = (src[j+0]<<R) | (src[j+2]<<G) | (src[j+4]<<B) | (0xFF<<A); \
	} \
}

#define RGBA_UNPACK8(w,h,data,buf,type,R,G,B,A) \
{ \
	const uint8_t* src = (const uint8_t*)data; \
	int n = w * h; \
	type* out_buf=(type*)buf; \
	for (int i = 0; i < n; i++) \
	{ \
		int j = 4*i; \
		out_buf[i] = (src[j+0]<<R) | (src[j+1]<<G) | (src[j+2]<<B) | (src[j+3]<<A); \
	} \
}

#define RGBA_UNPACK16(w,h,data,buf,type,R,G,B,A) \
{ \
	const uint8_t* src = (const uint8_t*)data; \
	int n = w * h; \
	type* out_buf=(type*)buf; \
	for (int i = 0; i < n; i++) \
	{ \
		int j = 8*i; \
		out_buf[i] = (src[j+0]<<R) | (src[j+2]<<G) | (src[j+4]<<B) | (src[j+6]<<A); \
	} \
}

#define LA_UNPACK8(w,h,data,buf,type,R,G,B,A) \
{ \
	const uint8_t* src = (const uint8_t*)data; \
	int n = w * h; \
	type* out_buf=(type*)buf; \
	for (int i = 0; i < n; i++) \
	{ \
		int j = 2*i; \
		uint8_t l = src[j+0]; \
		uint8_t a = src[j+1]; \
		out_buf[i] = (l<<R) | (l<<G) | (l<<B) | (a<<24); \
	} \
}

#define LA_UNPACK16(w,h,data,buf,type,R,G,B,A) \
{ \
	const uint8_t* src = (const uint8_t*)data; \
	int n = w * h; \
	type* out_buf=(type*)buf; \
	for (int i = 0; i < n; i++) \
	{ \
		int j = 4*i; \
		uint8_t l = src[j+0]; \
		uint8_t a = src[j+2]; \
		out_buf[i] = (l<<R) | (l<<G) | (l<<B) | (a<<24); \
	} \
}

// WHY: OpenGL on little-endian stores pixels as AABBGGRR in memory,
// meaning R is at byte offset 0 (shift 0). This is the format needed
// for glTexImage2D with GL_RGBA + GL_UNSIGNED_BYTE on x86/ARM.
// The switch dispatches on A3D_ImageFormat, selecting the matching
// unpack macro. Palette params (palsize/palbuf) are passed through
// but only used by I_UNPACK variants for indexed formats.
void Convert_UI32_AABBGGRR(uint32_t* buf, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf)
{
	switch (f)
	{
		case A3D_RGB8:   RGB_UNPACK8(w,h,data,buf,uint32_t,0,8,16,24) break;
		case A3D_RGB16:  RGB_UNPACK16(w,h,data,buf,uint32_t,0,8,16,24) break;
		case A3D_RGBA8:  RGBA_UNPACK8(w,h,data,buf,uint32_t,0,8,16,24) break;
		case A3D_RGBA16: RGBA_UNPACK16(w,h,data,buf,uint32_t,0,8,16,24) break;

		case A3D_LUMINANCE1:  L_UNPACK1(w,h,data,buf,uint32_t,0,8,16,24) break;
		case A3D_LUMINANCE2:  L_UNPACK2(w,h,data,buf,uint32_t,0,8,16,24) break;
		case A3D_LUMINANCE4:  L_UNPACK4(w,h,data,buf,uint32_t,0,8,16,24) break;
		case A3D_LUMINANCE8:  L_UNPACK8(w,h,data,buf,uint32_t,0,8,16,24) break;
		case A3D_LUMINANCE16: L_UNPACK16(w,h,data,buf,uint32_t,0,8,16,24) break;

		case A3D_LUMINANCE_ALPHA8:  LA_UNPACK8(w,h,data,buf,uint32_t,0,8,16,24) break;
		case A3D_LUMINANCE_ALPHA16: LA_UNPACK16(w,h,data,buf,uint32_t,0,8,16,24) break;

		case A3D_INDEX1_RGB:
		case A3D_INDEX1_RGBA: I_UNPACK1(w,h,data,buf,uint32_t,0,8,16,24,palsize,palbuf) break;
		
		case A3D_INDEX2_RGB:
		case A3D_INDEX2_RGBA: I_UNPACK2(w,h,data,buf,uint32_t,0,8,16,24,palsize,palbuf) break;

		case A3D_INDEX4_RGB:
		case A3D_INDEX4_RGBA: I_UNPACK4(w,h,data,buf,uint32_t,0,8,16,24,palsize,palbuf) break;

		case A3D_INDEX8_RGB:
		case A3D_INDEX8_RGBA: I_UNPACK8(w,h,data,buf,uint32_t,0,8,16,24,palsize,palbuf) break;
	}
}

// WHY: Native display APIs (Windows GDI DIBs, macOS CGBitmapContext)
// expect AARRGGBB byte order — R at shift 16, G at shift 8, B at shift 0.
// Same dispatch logic as AABBGGRR but with swapped channel shifts.
void Convert_UI32_AARRGGBB(uint32_t* buf, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf)
{
	switch (f)
	{
		case A3D_RGB8:   RGB_UNPACK8(w,h,data,buf,uint32_t,16,8,0,24) break;
		case A3D_RGB16:  RGB_UNPACK16(w,h,data,buf,uint32_t,16,8,0,24) break;
		case A3D_RGBA8:  RGBA_UNPACK8(w,h,data,buf,uint32_t,16,8,0,24) break;
		case A3D_RGBA16: RGBA_UNPACK16(w,h,data,buf,uint32_t,16,8,0,24) break;

		case A3D_LUMINANCE1:  L_UNPACK1(w,h,data,buf,uint32_t,16,8,0,24) break;
		case A3D_LUMINANCE2:  L_UNPACK2(w,h,data,buf,uint32_t,16,8,0,24) break;
		case A3D_LUMINANCE4:  L_UNPACK4(w,h,data,buf,uint32_t,16,8,0,24) break;
		case A3D_LUMINANCE8:  L_UNPACK8(w,h,data,buf,uint32_t,16,8,0,24) break;
		case A3D_LUMINANCE16: L_UNPACK16(w,h,data,buf,uint32_t,16,8,0,24) break;

		case A3D_LUMINANCE_ALPHA8:  LA_UNPACK8(w,h,data,buf,uint32_t,16,8,0,24) break;
		case A3D_LUMINANCE_ALPHA16: LA_UNPACK16(w,h,data,buf,uint32_t,16,8,0,24) break;

		case A3D_INDEX1_RGB:
		case A3D_INDEX1_RGBA: I_UNPACK1(w,h,data,buf,uint32_t,16,8,0,24,palsize,palbuf) break;
		
		case A3D_INDEX2_RGB:
		case A3D_INDEX2_RGBA: I_UNPACK2(w,h,data,buf,uint32_t,16,8,0,24,palsize,palbuf) break;

		case A3D_INDEX4_RGB:
		case A3D_INDEX4_RGBA: I_UNPACK4(w,h,data,buf,uint32_t,16,8,0,24,palsize,palbuf) break;

		case A3D_INDEX8_RGB:
		case A3D_INDEX8_RGBA: I_UNPACK8(w,h,data,buf,uint32_t,16,8,0,24,palsize,palbuf) break;
	}
}

// WHY: X11 XImage pixel buffers use unsigned long, which is 8 bytes on
// 64-bit Linux (LP64 data model). Even though only 4 bytes of color data
// are written, the stride between pixels must match sizeof(unsigned long).
// Without this variant, writing uint32_t into an XImage buffer would
// corrupt every other pixel on 64-bit X11 systems.
void Convert_UL_AARRGGBB(unsigned long* buf, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf)
{
	switch (f)
	{
		case A3D_RGB8:   RGB_UNPACK8(w,h,data,buf,unsigned long,16,8,0,24) break;
		case A3D_RGB16:  RGB_UNPACK16(w,h,data,buf,unsigned long,16,8,0,24) break;
		case A3D_RGBA8:  RGBA_UNPACK8(w,h,data,buf,unsigned long,16,8,0,24) break;
		case A3D_RGBA16: RGBA_UNPACK16(w,h,data,buf,unsigned long,16,8,0,24) break;

		case A3D_LUMINANCE1:  L_UNPACK1(w,h,data,buf,unsigned long,16,8,0,24) break;
		case A3D_LUMINANCE2:  L_UNPACK2(w,h,data,buf,unsigned long,16,8,0,24) break;
		case A3D_LUMINANCE4:  L_UNPACK4(w,h,data,buf,unsigned long,16,8,0,24) break;
		case A3D_LUMINANCE8:  L_UNPACK8(w,h,data,buf,unsigned long,16,8,0,24) break;
		case A3D_LUMINANCE16: L_UNPACK16(w,h,data,buf,unsigned long,16,8,0,24) break;

		case A3D_LUMINANCE_ALPHA8:  LA_UNPACK8(w,h,data,buf,unsigned long,16,8,0,24) break;
		case A3D_LUMINANCE_ALPHA16: LA_UNPACK16(w,h,data,buf,unsigned long,16,8,0,24) break;

		case A3D_INDEX1_RGB:
		case A3D_INDEX1_RGBA: I_UNPACK1(w,h,data,buf,unsigned long,16,8,0,24,palsize,palbuf) break;
		
		case A3D_INDEX2_RGB:
		case A3D_INDEX2_RGBA: I_UNPACK2(w,h,data,buf,unsigned long,16,8,0,24,palsize,palbuf) break;

		case A3D_INDEX4_RGB:
		case A3D_INDEX4_RGBA: I_UNPACK4(w,h,data,buf,unsigned long,16,8,0,24,palsize,palbuf) break;

		case A3D_INDEX8_RGB:
		case A3D_INDEX8_RGBA: I_UNPACK8(w,h,data,buf,unsigned long,16,8,0,24,palsize,palbuf) break;
	}
}


// WHY: Directional lighting needs a single brightness value per texel,
// not full RGB. This function converts any image format to luminance
// packed into the high byte (bits 31-24) with a constant RGB tint in
// the low 24 bits. The luminance formula (2R + 3G + 1B) / 6 weights
// green highest because human vision is most sensitive to green.
// The rgb parameter supplies the surface color; luminance modulates it
// at render time (light_intensity * surface_color). Output format is
// LLZZYYXX where LL=luminance, ZZ/YY/XX=constant tint from rgb[].
void ConvertLuminance_UI32_LLZZYYXX(uint32_t* buf, const uint8_t rgb[3], A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf)
{
	uint32_t const_rgb = 0;

	if (rgb)
		const_rgb = rgb[0] | (rgb[1] << 8) | (rgb[2] << 16);

	switch (f)
	{
		case A3D_RGB8:
		{
			const uint8_t* src = (const uint8_t*)data;
			for (int i = 0; i < w*h; i++)
				buf[i] = (((src[3 * i + 0] * 2 + src[3 * i + 1] * 3 + src[3 * i + 2] + 3) / 6) << 24) | const_rgb;
			break;
		}

		case A3D_RGB16:
		{
			const uint16_t* src = (const uint16_t*)data;
			for (int i = 0; i < w*h; i++)
				buf[i] = (((src[3 * i + 0] * 2 + src[3 * i + 1] * 3 + src[3 * i + 2] + 3 * 257) / (6 * 257)) << 24) | const_rgb;
			break;
		}

		case A3D_RGBA8:
		{
			const uint8_t* src = (const uint8_t*)data;
			for (int i = 0; i < w*h; i++)
				buf[i] = (((src[4 * i + 0] * 2 + src[4 * i + 1] * 3 + src[4 * i + 2] + 3) / 6) << 24) | const_rgb;
			break;
		}

		case A3D_RGBA16:
		{
			const uint16_t* src = (const uint16_t*)data;
			for (int i = 0; i < w*h; i++)
				buf[i] = (((src[4 * i + 0] * 2 + src[4 * i + 1] * 3 + src[4 * i + 2] + 3 * 257) / (6 * 257)) << 24) | const_rgb;
			break;
		}

		case A3D_LUMINANCE1:
			break;
		case A3D_LUMINANCE2:
			break;
		case A3D_LUMINANCE4:
			break;
		case A3D_LUMINANCE8:
		{
			const uint8_t* src = (const uint8_t*)data;
			for (int i = 0; i < w*h; i++)
				buf[i] = (src[i] << 24) | const_rgb;
			break;
		}
		case A3D_LUMINANCE_ALPHA8:
		{
			const uint8_t* src = (const uint8_t*)data;
			for (int i = 0; i < w*h; i++)
				buf[i] = (src[2 * i + 0] << 24) | const_rgb;
			break;
		}
		case A3D_LUMINANCE_ALPHA16:
			break;
		case A3D_INDEX1_RGB:
			break;
		case A3D_INDEX2_RGB:
			break;
		case A3D_INDEX4_RGB:
			break;
		case A3D_INDEX8_RGB:
		{
			const uint8_t* src = (const uint8_t*)data;
			for (int i = 0; i < w*h; i++)
			{
				int q = src[i];
				if (q >= palsize)
					buf[i] = 0xFFFFFF;
				else
				{
					const uint8_t* p = (const uint8_t*)palbuf + 4 * q;
					buf[i] = (((p[0] * 2 + p[1] * 3 + p[2] + 3) / 6) << 24) | const_rgb;
				}
			}
			break;
		}

		case A3D_INDEX1_RGBA:
			break;
		case A3D_INDEX2_RGBA:
			break;
		case A3D_INDEX4_RGBA:
			break;
		case A3D_INDEX8_RGBA:
			const uint8_t* src = (const uint8_t*)data;
			for (int i = 0; i < w*h; i++)
			{
				int q = src[i];
				if (q >= palsize)
					buf[i] = 0xFFFFFF;
				else
				{
					const uint8_t* p = (const uint8_t*)palbuf + 4 * q;
					buf[i] = (((p[0] * 2 + p[1] * 3 + p[2] + 3) / 6) << 24) | const_rgb;
				}
			}
			break;
	}
}
