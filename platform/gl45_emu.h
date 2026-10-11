// ============================================================================
// OpenGL 4.5 DSA Emulation API Header
// ============================================================================
//
// PURPOSE: Declares gl3* functions that emulate OpenGL 4.5 Direct State
//          Access (DSA) API on platforms limited to GL 3.3 (web builds,
//          older drivers).
//
// USE_GL3 FLAG (defined below):
//   USE_GL3 = 1: Functions contain emulation logic (GL 3.3 binding-based)
//   USE_GL3 = 0: Functions are inline passthrough to native GL 4.5 DSA
//
// USAGE: Include this header instead of calling GL 4.5 functions directly.
//        Preprocessor selects emulation or passthrough automatically based
//        on USE_GL3 flag. Renderer code is platform-agnostic.
//
// FUNCTION LIST:
//
//   Texture Creation & Storage:
//   - gl3CreateTextures: Create texture objects (glGenTextures wrapper)
//   - gl3TextureStorage2D/3D: Allocate immutable texture storage + mipmaps
//   - gl3TextureSubImage2D: Upload pixel data to texture region
//
//   Texture Binding:
//   - gl3BindTextureUnit2D/3D: Bind texture to shader texture unit
//
//   Texture Parameters:
//   - gl3TextureParameteri2D/3D: Set filtering, wrapping (GL_NEAREST, etc.)
//   - gl3TextureParameterfv2D/3D: Set border color, LOD bias
//
//   Buffer Objects:
//   - gl3CreateBuffers: Create buffer objects (glGenBuffers wrapper)
//   - gl3NamedBufferStorage: Allocate immutable buffer storage (VBOs, UBOs)
//   - gl3NamedBufferSubData: Update buffer contents
//
//   Vertex Arrays:
//   - gl3CreateVertexArrays: Create VAOs (glGenVertexArrays wrapper)
//
//   Unimplemented (development tools only):
//   - gl3CopyImageSubData: Copy pixels between textures
//   - gl3GetTextureSubImage: Read pixels from texture
//
// See gl45_emu.cpp for emulation implementation details.
//
// ============================================================================

#pragma once

#include "gl.h"

#define USE_GL3 1

void gl3CopyImageSubData(GLuint srcName, GLenum srcTarget, GLint srcLevel, GLint srcX, GLint srcY, GLint srcZ,
	GLuint dstName, GLenum dstTarget, GLint dstLevel, GLint dstX, GLint dstY, GLint dstZ,
	GLsizei srcWidth, GLsizei srcHeight, GLsizei srcDepth);

void gl3GetTextureSubImage(GLuint texture, GLint level, GLint xoffset, GLint yoffset, GLint zoffset,
	GLsizei width, GLsizei height, GLsizei depth,
	GLenum format, GLenum type, GLsizei bufSize, void *pixels);

void gl3TextureStorage2D(GLuint tex, GLint levels, GLenum ifmt, GLsizei w, GLsizei h);
void gl3TextureStorage3D(GLuint tex, GLint levels, GLenum ifmt, GLsizei w, GLsizei h, GLsizei d);
void gl3CreateTextures(GLenum target, GLsizei num, GLuint* arr);
void gl3TextureSubImage2D(GLuint tex, GLint level, GLint x, GLint y, GLsizei w, GLsizei h, GLenum fmt, GLenum type, const void *pix);
void gl3BindTextureUnit2D(GLuint unit, GLuint tex);
void gl3BindTextureUnit3D(GLuint unit, GLuint tex);
void gl3TextureParameteri2D(GLuint tex, GLenum param, GLint val);
void gl3TextureParameteri3D(GLuint tex, GLenum param, GLint val);
void gl3TextureParameterfv2D(GLuint tex, GLenum param, GLfloat* val);
void gl3TextureParameterfv3D(GLuint tex, GLenum param, GLfloat* val);
void gl3CreateBuffers(GLsizei num, GLuint* arr);
void gl3NamedBufferStorage(GLuint buffer, GLsizeiptr size, const void *data, GLbitfield flags);
void gl3NamedBufferSubData(GLuint buffer, GLintptr offset, GLsizeiptr size, const void *data);
void gl3CreateVertexArrays(GLsizei num, GLuint* arr);
