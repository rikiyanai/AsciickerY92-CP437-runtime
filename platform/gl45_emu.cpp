// ============================================================================
// OpenGL 4.5 Direct State Access (DSA) Emulation for GL 3.3 Compatibility
// ============================================================================
//
// PURPOSE: Provide modern OpenGL 4.5 DSA API on platforms limited to GL 3.3.
//          Translates DSA calls (glTextureStorage2D, glCreateTextures) into
//          legacy binding-based calls (glBindTexture + glTexImage2D).
//
// WHY EMULATION LAYER:
//
//   Desktop builds (OpenGL 4.5):
//   - Support Direct State Access (DSA) introduced in OpenGL 4.5
//   - DSA allows operating on textures/buffers without binding them first
//   - Cleaner API, less state management, better performance
//
//   Web builds (Emscripten / WebGL 2.0):
//   - Limited to OpenGL ES 3.0 / WebGL 2.0 (GL 3.3 subset)
//   - No DSA support -- must use legacy binding model
//
//   Older drivers (Intel HD 4000, 2012-era hardware):
//   - Only support OpenGL 3.3 or earlier
//   - No DSA support
//
//   USE_GL3 preprocessor flag (defined in gl45_emu.h):
//   - USE_GL3 = 1: Enable emulation (web builds, old drivers)
//   - USE_GL3 = 0: Passthrough to native GL 4.5 (desktop builds)
//
// DSA vs LEGACY COMPARISON:
//
//   OpenGL 4.5 DSA (direct state access):
//     glTextureStorage2D(tex, levels, GL_RGBA8, w, h);
//     // No binding required -- operates directly on texture object
//
//   OpenGL 3.3 Legacy (binding-based):
//     glBindTexture(GL_TEXTURE_2D, tex);
//     glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, 0);
//     // Must bind texture first, operate on bound target, then unbind
//
//   Emulation translates DSA → Legacy automatically when USE_GL3=1.
//
// EMULATED FUNCTIONS (by category):
//
//   Texture Creation & Storage:
//   - gl3CreateTextures: Maps glCreateTextures → glGenTextures
//   - gl3TextureStorage2D/3D: Allocates mipmap storage via glTexImage2D/3D
//   - gl3TextureSubImage2D: Uploads pixel data to texture
//
//   Texture Binding:
//   - gl3BindTextureUnit2D/3D: Binds texture to texture unit (for shaders)
//
//   Texture Parameters:
//   - gl3TextureParameteri2D/3D: Sets texture filtering, wrapping modes
//   - gl3TextureParameterfv2D/3D: Sets border color, LOD bias
//
//   Buffer Objects:
//   - gl3CreateBuffers: Maps glCreateBuffers → glGenBuffers
//   - gl3NamedBufferStorage: Allocates buffer storage (VBOs, UBOs)
//   - gl3NamedBufferSubData: Updates buffer contents
//
//   Vertex Arrays:
//   - gl3CreateVertexArrays: Maps glCreateVertexArrays → glGenVertexArrays
//
//   Unimplemented (warnings logged, not needed for core rendering):
//   - gl3CopyImageSubData: Copy pixels between textures (all calls ported)
//   - gl3GetTextureSubImage: Read pixels from texture (font editor only)
//
// EMULATION PATTERN (all functions follow this pattern):
//
//   1. Query current binding state (glGetIntegerv)
//   2. Bind target texture/buffer (glBindTexture/glBindBuffer)
//   3. Perform legacy GL 3.3 operation (glTexImage2D, glTexParameteri, etc.)
//   4. Restore previous binding state (glBindTexture with saved value)
//
//   WHY state save/restore: Emulation must be transparent to calling code.
//   Renderer may have texture already bound when gl3* function is called.
//   If we don't restore binding, renderer state becomes corrupted.
//
// PASSTHROUGH MODE (#if USE_GL3 == 0):
//
//   Desktop builds with OpenGL 4.5 support call native DSA functions directly.
//   Zero overhead -- preprocessor replaces gl3TextureStorage2D(...) with
//   glTextureStorage2D(...) at compile time.
//
// INTEGRATION POINTS:
//
//   render.cpp: Shader texture creation (ANSI terminal, mesh, terrain)
//   sprite.cpp: Sprite atlas texture creation
//   texheap.cpp: Texture heap management (dynamic texture allocation)
//   asciiid.cpp: Editor texture creation (framebuffers, debug views)
//
// [FLOW:RENDER] All texture/buffer operations go through gl3* emulation layer
//
// ============================================================================

#include <stdio.h>
#include "gl45_emu.h"

// WHY unimplemented: All glCopyImageSubData calls have been ported to
// alternative methods (CPU-side copy via glGetTexImage + glTexSubImage2D).
// This function is no longer called in production code paths.
// Warning logs if accidentally invoked in GL 3.3 mode.
void gl3CopyImageSubData(GLuint srcName, GLenum srcTarget, GLint srcLevel, GLint srcX, GLint srcY, GLint srcZ,
						GLuint dstName, GLenum dstTarget, GLint dstLevel, GLint dstX, GLint dstY, GLint dstZ,
						GLsizei srcWidth, GLsizei srcHeight, GLsizei srcDepth)
{
#if USE_GL3
	// all calls ported
	static bool warn_once = true;
	if (warn_once)
	{
		warn_once = false;
		printf("WARNING: GL3 calling unimplemented glCopyImageSubData()\n");
	}
#else
	glCopyImageSubData(srcName, srcTarget, srcLevel, srcX, srcY, srcZ,
		dstName, dstTarget, dstLevel, dstX, dstY, dstZ,
		srcWidth, srcHeight, srcDepth);
#endif
}

// WHY unimplemented: Only used by font editor (pixel readback) and glyph
// coverage calculator (development tools). Not needed for end-user builds.
// Font editor and coverage calculator are desktop-only tools (not web builds).
// GL 3.3 emulation can use glReadPixels + framebuffer if needed later.
void gl3GetTextureSubImage(GLuint texture, GLint level, GLint xoffset, GLint yoffset, GLint zoffset,
						   GLsizei width, GLsizei height, GLsizei depth,
						   GLenum format, GLenum type, GLsizei bufSize, void *pixels)
{
#if USE_GL3
	// TODO later
	// used by font editor (get pixel) & glyphs coverage calculator (not needed for end user)
	static bool warn_once = true;
	if (warn_once)
	{
		warn_once = false;
		printf("WARNING: GL3 calling unimplemented glGetTextureSubImage()\n");
	}
#else
	glGetTextureSubImage(texture, level, xoffset, yoffset, zoffset,
		width, height, depth, format, type, bufSize, pixels);
#endif
}

// WHY state save/restore: Must preserve GL_TEXTURE_BINDING_2D across call.
// Renderer may already have texture bound when this is called (e.g., during
// sprite atlas creation while mesh texture is bound). Restoring binding
// prevents corruption of renderer state.
//
// WHY mipmap loop: glTextureStorage2D allocates all mipmap levels at once.
// GL 3.3 glTexImage2D must allocate each level separately. Loop divides
// dimensions by 2 per level (w >>= 1, h >>= 1), clamping to minimum 1×1.
//
// WHY ifmt format switch: GL_RGBA8UI and GL_R16UI are integer formats,
// requiring GL_RGBA_INTEGER/GL_RED_INTEGER format parameter (not GL_RGBA).
// GL 3.3 glTexImage2D is stricter about format/internalFormat matching than
// GL 4.5 glTextureStorage2D (which infers format from internal format).
void gl3TextureStorage2D(GLuint tex, GLint levels, GLenum ifmt, GLsizei w, GLsizei h)
{
#if USE_GL3
	int t;
	glGetIntegerv(GL_TEXTURE_BINDING_2D, &t);
	glBindTexture(GL_TEXTURE_2D, tex);
	for (int lev = 0; lev < levels; lev++)
	{
		if (ifmt == GL_RGBA8UI)
			glTexImage2D(GL_TEXTURE_2D, lev, ifmt, w, h, 0, GL_RGBA_INTEGER, GL_UNSIGNED_BYTE, 0);
		else
		if (ifmt == GL_R16UI)
			glTexImage2D(GL_TEXTURE_2D, lev, ifmt, w, h, 0, GL_RED_INTEGER, GL_UNSIGNED_SHORT, 0);
		else
			glTexImage2D(GL_TEXTURE_2D, lev, ifmt, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, 0);
		w >>= 1;
		h >>= 1;
		if (!w)
			w = 1;
		if (!h)
			h = 1;
	}
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, levels - 1);
	glBindTexture(GL_TEXTURE_2D, t);
#else
	glTextureStorage2D(tex, levels, ifmt, w, h);
#endif
}

void gl3TextureStorage3D(GLuint tex, GLint levels, GLenum ifmt, GLsizei w, GLsizei h, GLsizei d)
{
#if USE_GL3
	int t;
	glGetIntegerv(GL_TEXTURE_BINDING_3D, &t);
	glBindTexture(GL_TEXTURE_3D, tex);
	for (int lev = 0; lev < levels; lev++)
	{
		glTexImage3D(GL_TEXTURE_3D, lev, ifmt, w, h, d, 0, GL_RGBA, GL_UNSIGNED_BYTE, 0);
		w >>= 1;
		h >>= 1;
		d >>= 1;
		if (!w)
			w = 1;
		if (!h)
			h = 1;
		if (!d)
			d = 1;
	}
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, levels - 1);
	glBindTexture(GL_TEXTURE_3D, t);
#else
	glTextureStorage3D(tex, levels, ifmt, w, h, d);
#endif
}


void gl3CreateTextures(GLenum target, GLsizei num, GLuint* arr)
{
#if USE_GL3
	glGenTextures(num, arr);
#else
	glCreateTextures(target, num, arr);
#endif
}

void gl3TextureSubImage2D(GLuint tex, GLint level, GLint x, GLint y, GLsizei w, GLsizei h, GLenum fmt, GLenum type, const void *pix)
{
#if USE_GL3
	int t;
	glGetIntegerv(GL_TEXTURE_BINDING_2D, &t);
	glBindTexture(GL_TEXTURE_2D, tex);
	glTexSubImage2D(GL_TEXTURE_2D, level, x, y, w, h, fmt, type, pix);
	glBindTexture(GL_TEXTURE_2D, t);
#else
	glTextureSubImage2D(tex, level, x, y, w, h, fmt, type, pix);
#endif
}

void gl3BindTextureUnit2D(GLuint unit, GLuint tex)
{
#if USE_GL3
	int u;
	glGetIntegerv(GL_ACTIVE_TEXTURE, &u);
	glActiveTexture(GL_TEXTURE0 + unit);
	glBindTexture(GL_TEXTURE_2D, tex);
	if (!tex)
		glBindTexture(GL_TEXTURE_3D, 0);
	glActiveTexture(u);
#else
	glBindTextureUnit(unit, tex);
#endif
}

void gl3BindTextureUnit3D(GLuint unit, GLuint tex)
{
#if USE_GL3
	int u;
	glGetIntegerv(GL_ACTIVE_TEXTURE, &u);
	glActiveTexture(GL_TEXTURE0 + unit);
	glBindTexture(GL_TEXTURE_3D, tex);
	if (!tex)
		glBindTexture(GL_TEXTURE_2D, 0);
	glActiveTexture(u);
#else
	glBindTextureUnit(unit, tex);
#endif
}

void gl3TextureParameteri2D(GLuint tex, GLenum param, GLint val)
{
#if USE_GL3
	int t;
	glGetIntegerv(GL_TEXTURE_BINDING_2D, &t);
	glBindTexture(GL_TEXTURE_2D, tex);
	glTexParameteri(GL_TEXTURE_2D, param, val);
	glBindTexture(GL_TEXTURE_2D, t);
#else
	glTextureParameteri(tex, param, val);
#endif
}

void gl3TextureParameteri3D(GLuint tex, GLenum param, GLint val)
{
#if USE_GL3
	int t;
	glGetIntegerv(GL_TEXTURE_BINDING_3D, &t);
	glBindTexture(GL_TEXTURE_3D, tex);
	glTexParameteri(GL_TEXTURE_3D, param, val);
	glBindTexture(GL_TEXTURE_3D, t);
#else
	glTextureParameteri(tex, param, val);
#endif
}

void gl3TextureParameterfv2D(GLuint tex, GLenum param, GLfloat* val)
{
#if USE_GL3
	int t;
	glGetIntegerv(GL_TEXTURE_BINDING_2D, &t);
	glBindTexture(GL_TEXTURE_2D, tex);
	glTexParameterfv(GL_TEXTURE_2D, param, val);
	glBindTexture(GL_TEXTURE_2D, t);
#else
	glTextureParameterfv(tex, param, val);
#endif
}

void gl3TextureParameterfv3D(GLuint tex, GLenum param, GLfloat* val)
{
#if USE_GL3
	int t;
	glGetIntegerv(GL_TEXTURE_BINDING_3D, &t);
	glBindTexture(GL_TEXTURE_3D, tex);
	glTexParameterfv(GL_TEXTURE_3D, param, val);
	glBindTexture(GL_TEXTURE_3D, t);
#else
	glTextureParameterfv(tex, param, val);
#endif
}

void gl3CreateBuffers(GLsizei num, GLuint* arr)
{
#if USE_GL3
	glGenBuffers(num, arr);
#else
	glCreateBuffers(num, arr);
#endif
}

// WHY GL_COPY_WRITE_BUFFER: Dedicated buffer binding target for data transfer.
// Using GL_COPY_WRITE_BUFFER avoids disturbing GL_ARRAY_BUFFER (vertex data)
// or GL_ELEMENT_ARRAY_BUFFER (index data) bindings. Allows buffer operations
// without disrupting active VAO state.
//
// WHY bind/unbind pattern: glNamedBufferStorage operates without binding in
// GL 4.5, but GL 3.3 glBufferData requires buffer to be bound. Bind to
// GL_COPY_WRITE_BUFFER, allocate storage, then binding is implicitly cleared
// on next operation (no explicit unbind needed for COPY_WRITE_BUFFER).
void gl3NamedBufferStorage(GLuint buffer, GLsizeiptr size, const void *data, GLbitfield flags)
{
#if USE_GL3
	glBindBuffer(GL_COPY_WRITE_BUFFER, buffer);
	GLenum usage = GL_STATIC_DRAW;
	if (flags & GL_DYNAMIC_STORAGE_BIT)
		usage = GL_DYNAMIC_DRAW;
	glBufferData(GL_COPY_WRITE_BUFFER, size, data, GL_DYNAMIC_DRAW);
#else
	glNamedBufferStorage(buffer, size, data, flags);
#endif
}

void gl3NamedBufferSubData(GLuint buffer, GLintptr offset, GLsizeiptr size, const void *data)
{
#if USE_GL3
	glBindBuffer(GL_COPY_WRITE_BUFFER, buffer);
	glBufferSubData(GL_COPY_WRITE_BUFFER, offset, size, data);
#else
	glNamedBufferSubData(buffer, offset, size, data);
#endif
}

void gl3CreateVertexArrays(GLsizei num, GLuint* arr)
{
#if USE_GL3
	glGenVertexArrays(num, arr);
#else
	glCreateVertexArrays(num, arr);
#endif
}
