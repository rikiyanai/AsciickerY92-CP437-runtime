// render_s3_gpu_terrain.cpp — FL-4257 native S3 terrain projection owner.
//
// Normal headed spherical rendering must not spend the frame in the recursive
// CPU terrain rasterizer. This pass submits terrain cells to GL, performs the
// S3 projection and depth competition there, then writes the winning terrain
// samples back into Renderer::sample_buffer so the existing world/water/sprite
// stages keep their contracts.

#include "render_internal.h"
#include "render_s3.h"
#include "terrain.h"

#if (defined(GAME) && !defined(PURE_TERM)) || defined(__EMSCRIPTEN__)
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <vector>
#if defined(__EMSCRIPTEN__)
#include <GLES3/gl3.h>
#include <emscripten/html5.h>
#else
#include "gl.h"
#endif

struct S3GpuTerrainVert
{
	float wx, wy, wz;
	float visual;
	float diffuse;
};

struct S3GpuTerrainState
{
	GLuint fbo = 0;
	GLuint color = 0;
	GLuint depth = 0;
	GLuint vao = 0;
	GLuint vbo = 0;
	GLuint prg = 0;
	int w = 0;
	int h = 0;
	bool init_attempted = false;
	bool init_ok = false;
	std::vector<float> readback;
};

static S3GpuTerrainState g_s3_gpu_terrain;

static GLuint S3GpuCompileShader(GLenum kind, const char* src)
{
	GLuint sh = glCreateShader(kind);
	if (!sh)
		return 0;
	GLint len = (GLint)strlen(src);
	glShaderSource(sh, 1, &src, &len);
	glCompileShader(sh);
	GLint ok = 0;
	glGetShaderiv(sh, GL_COMPILE_STATUS, &ok);
	if (!ok)
	{
		char log[2048];
		GLint n = 0;
		glGetShaderInfoLog(sh, (GLint)sizeof(log) - 1, &n, log);
		log[n > 0 ? n : 0] = 0;
		fprintf(stderr, "[FL-4257] S3 GPU terrain shader compile failed: %s\n", log);
		glDeleteShader(sh);
		return 0;
	}
	return sh;
}

static bool S3GpuEnsureProgram(S3GpuTerrainState& st)
{
	if (st.prg)
		return true;
#if defined(__EMSCRIPTEN__)
	const char* vs_src =
		"#version 300 es\n"
		"precision highp float;\n"
		"layout(location=0) in vec3 a_world;\n"
		"layout(location=1) in float a_visual;\n"
		"layout(location=2) in float a_diffuse;\n"
		"uniform vec2 u_size;\n"
		"uniform vec3 u_anchor;\n"
		"uniform float u_R;\n"
		"uniform vec4 u_cam;\n"
		"uniform vec4 u_right;\n"
		"uniform vec4 u_up;\n"
		"uniform vec4 u_fwd;\n"
		"uniform vec2 u_ofs;\n"
		"uniform float u_focal;\n"
		"uniform float u_focal_y;\n"
		"uniform int u_image;\n"
		"out float v_visual;\n"
		"out float v_diffuse;\n"
		"out float v_depth_key;\n"
		"out float v_ok;\n"
		"const float PI = 3.14159265358979323846;\n"
		"const float DEPTH_C = 800000.0;\n"
		"const float DEPTH_S = 10000.0;\n"
		"const float FORWARD_MIN = 0.017;\n"
		"vec4 embed(vec3 world){\n"
		"  float cx = world.x - u_anchor.x;\n"
		"  float cy = world.y - u_anchor.y;\n"
		"  float hh = (world.z - u_anchor.z) / 64.0;\n"
		"  float rho = length(vec2(cx, cy));\n"
		"  vec2 dir = rho > 1e-6 ? vec2(cx, cy) / rho : vec2(1.0, 0.0);\n"
		"  float theta = rho / u_R;\n"
		"  float phi = hh / u_R;\n"
		"  float cp = cos(phi);\n"
		"  float sp = sin(phi);\n"
		"  float st = sin(theta);\n"
		"  float ct = cos(theta);\n"
		"  return vec4(cp * st * dir.x, cp * st * dir.y, cp * ct, sp);\n"
		"}\n"
		"void main(){\n"
		"  v_visual = a_visual;\n"
		"  v_diffuse = a_diffuse;\n"
		"  v_depth_key = -1000000.0;\n"
		"  v_ok = 0.0;\n"
		"  vec3 rel = a_world - u_anchor;\n"
		"  if (length(rel.xy) > PI * u_R * 1.000001) { gl_Position = vec4(3.0,3.0,0.0,1.0); return; }\n"
		"  vec4 P = embed(a_world);\n"
		"  float qr = dot(P, u_right);\n"
		"  float qu = dot(P, u_up);\n"
		"  float qf = dot(P, u_fwd);\n"
		"  float qw = clamp(dot(P, u_cam), -1.0, 1.0);\n"
		"  float arc = acos(qw);\n"
		"  float salpha = length(vec3(qr, qu, qf));\n"
		"  if (salpha <= 1e-7 || arc * u_R < 0.25) { gl_Position = vec4(3.0,3.0,0.0,1.0); return; }\n"
		"  if (u_image == 0) {\n"
		"    if (qf < FORWARD_MIN * salpha) { gl_Position = vec4(3.0,3.0,0.0,1.0); return; }\n"
		"  } else {\n"
		"    if (qf > -FORWARD_MIN * salpha) { gl_Position = vec4(3.0,3.0,0.0,1.0); return; }\n"
		"    arc = 2.0 * PI - arc;\n"
		"  }\n"
		"  float inv = 1.0 / qf;\n"
		"  float fx = u_ofs.x + u_focal * qr * inv;\n"
		"  float fy = u_ofs.y + u_focal_y * qu * inv;\n"
		"  float key = DEPTH_C - arc * DEPTH_S;\n"
		"  v_depth_key = key;\n"
		"  v_ok = 1.0;\n"
		"  float ndc_x = (fx + 0.5) / u_size.x * 2.0 - 1.0;\n"
		"  float ndc_y = (fy + 0.5) / u_size.y * 2.0 - 1.0;\n"
		"  gl_Position = vec4(ndc_x, ndc_y, (key - (DEPTH_C - 6.28318530718 * DEPTH_S)) / (6.28318530718 * DEPTH_S), 1.0);\n"
		"}\n";
	const char* fs_src =
		"#version 300 es\n"
		"precision highp float;\n"
		"in float v_visual;\n"
		"in float v_diffuse;\n"
		"in float v_depth_key;\n"
		"in float v_ok;\n"
		"layout(location=0) out vec4 out_color;\n"
		"void main(){\n"
		"  if (v_ok < 0.99) discard;\n"
		"  out_color = vec4(v_visual / 65535.0, v_diffuse / 255.0, v_depth_key / 1000000.0, 1.0);\n"
		"}\n";
#else
	const char* vs_src =
		"#version 330\n"
		"layout(location=0) in vec3 a_world;\n"
		"layout(location=1) in float a_visual;\n"
		"layout(location=2) in float a_diffuse;\n"
		"uniform vec2 u_size;\n"
		"uniform vec3 u_anchor;\n"
		"uniform float u_R;\n"
		"uniform vec4 u_cam;\n"
		"uniform vec4 u_right;\n"
		"uniform vec4 u_up;\n"
		"uniform vec4 u_fwd;\n"
		"uniform vec2 u_ofs;\n"
		"uniform float u_focal;\n"
		"uniform float u_focal_y;\n"
		"uniform int u_image;\n"
		"out float v_visual;\n"
		"out float v_diffuse;\n"
		"out float v_depth_key;\n"
		"out float v_ok;\n"
		"const float PI = 3.14159265358979323846;\n"
		"const float DEPTH_C = 800000.0;\n"
		"const float DEPTH_S = 10000.0;\n"
		"const float FORWARD_MIN = 0.017;\n"
		"vec4 embed(vec3 world){\n"
		"  float cx = world.x - u_anchor.x;\n"
		"  float cy = world.y - u_anchor.y;\n"
		"  float hh = (world.z - u_anchor.z) / 64.0;\n"
		"  float rho = length(vec2(cx, cy));\n"
		"  vec2 dir = rho > 1e-6 ? vec2(cx, cy) / rho : vec2(1.0, 0.0);\n"
		"  float theta = rho / u_R;\n"
		"  float phi = hh / u_R;\n"
		"  float cp = cos(phi);\n"
		"  float sp = sin(phi);\n"
		"  float st = sin(theta);\n"
		"  float ct = cos(theta);\n"
		"  return vec4(cp * st * dir.x, cp * st * dir.y, cp * ct, sp);\n"
		"}\n"
		"void main(){\n"
		"  v_visual = a_visual;\n"
		"  v_diffuse = a_diffuse;\n"
		"  v_depth_key = -1000000.0;\n"
		"  v_ok = 0.0;\n"
		"  vec3 rel = a_world - u_anchor;\n"
		"  if (length(rel.xy) > PI * u_R * 1.000001) { gl_Position = vec4(3.0,3.0,0.0,1.0); return; }\n"
		"  vec4 P = embed(a_world);\n"
		"  float qr = dot(P, u_right);\n"
		"  float qu = dot(P, u_up);\n"
		"  float qf = dot(P, u_fwd);\n"
		"  float qw = clamp(dot(P, u_cam), -1.0, 1.0);\n"
		"  float arc = acos(qw);\n"
		"  float salpha = length(vec3(qr, qu, qf));\n"
		"  if (salpha <= 1e-7 || arc * u_R < 0.25) { gl_Position = vec4(3.0,3.0,0.0,1.0); return; }\n"
		"  if (u_image == 0) {\n"
		"    if (qf < FORWARD_MIN * salpha) { gl_Position = vec4(3.0,3.0,0.0,1.0); return; }\n"
		"  } else {\n"
		"    if (qf > -FORWARD_MIN * salpha) { gl_Position = vec4(3.0,3.0,0.0,1.0); return; }\n"
		"    arc = 2.0 * PI - arc;\n"
		"  }\n"
		"  float inv = 1.0 / qf;\n"
		"  float fx = u_ofs.x + u_focal * qr * inv;\n"
		"  float fy = u_ofs.y + u_focal_y * qu * inv;\n"
		"  float key = DEPTH_C - arc * DEPTH_S;\n"
		"  v_depth_key = key;\n"
		"  v_ok = 1.0;\n"
		"  float ndc_x = (fx + 0.5) / u_size.x * 2.0 - 1.0;\n"
		"  float ndc_y = (fy + 0.5) / u_size.y * 2.0 - 1.0;\n"
		"  gl_Position = vec4(ndc_x, ndc_y, (key - (DEPTH_C - 6.28318530718 * DEPTH_S)) / (6.28318530718 * DEPTH_S), 1.0);\n"
		"}\n";
	const char* fs_src =
		"#version 330\n"
		"in float v_visual;\n"
		"in float v_diffuse;\n"
		"in float v_depth_key;\n"
		"in float v_ok;\n"
		"layout(location=0) out vec4 out_color;\n"
		"void main(){\n"
		"  if (v_ok < 0.99) discard;\n"
		"  out_color = vec4(v_visual / 65535.0, v_diffuse / 255.0, v_depth_key / 1000000.0, 1.0);\n"
		"}\n";
#endif
	GLuint vs = S3GpuCompileShader(GL_VERTEX_SHADER, vs_src);
	GLuint fs = S3GpuCompileShader(GL_FRAGMENT_SHADER, fs_src);
	if (!vs || !fs)
		return false;
	GLuint prg = glCreateProgram();
	glAttachShader(prg, vs);
	glAttachShader(prg, fs);
	glLinkProgram(prg);
	glDeleteShader(vs);
	glDeleteShader(fs);
	GLint linked = 0;
	glGetProgramiv(prg, GL_LINK_STATUS, &linked);
	if (!linked)
	{
		char log[2048];
		GLint n = 0;
		glGetProgramInfoLog(prg, (GLint)sizeof(log) - 1, &n, log);
		log[n > 0 ? n : 0] = 0;
		fprintf(stderr, "[FL-4257] S3 GPU terrain program link failed: %s\n", log);
		glDeleteProgram(prg);
		return false;
	}
	st.prg = prg;
	return true;
}

static bool S3GpuEnsureTarget(S3GpuTerrainState& st, int w, int h)
{
#if defined(__EMSCRIPTEN__)
	if (!emscripten_webgl_get_current_context())
	{
		EmscriptenWebGLContextAttributes attrs;
		emscripten_webgl_init_context_attributes(&attrs);
		attrs.alpha = EM_FALSE;
		attrs.depth = EM_FALSE;
		attrs.stencil = EM_FALSE;
		attrs.antialias = EM_FALSE;
		attrs.majorVersion = 2;
		attrs.minorVersion = 0;
		attrs.enableExtensionsByDefault = EM_TRUE;
		EMSCRIPTEN_WEBGL_CONTEXT_HANDLE ctx =
			emscripten_webgl_create_context("#asciicker_canvas", &attrs);
		if (ctx <= 0 || emscripten_webgl_make_context_current(ctx) != EMSCRIPTEN_RESULT_SUCCESS)
		{
			fprintf(stderr, "[FL-4257] S3 GPU terrain cannot make WebGL2 context current (ctx=%d)\n", (int)ctx);
			return false;
		}
	}
	emscripten_webgl_enable_extension(emscripten_webgl_get_current_context(),
	                                  "EXT_color_buffer_float");
#endif
	if (!S3GpuEnsureProgram(st))
		return false;
	if (!st.vao)
		glGenVertexArrays(1, &st.vao);
	if (!st.vbo)
		glGenBuffers(1, &st.vbo);
	if (!st.fbo)
		glGenFramebuffers(1, &st.fbo);
	if (!st.color)
		glGenTextures(1, &st.color);
	if (!st.depth)
		glGenRenderbuffers(1, &st.depth);
	if (!st.vao || !st.vbo || !st.fbo || !st.color || !st.depth)
		return false;
	if (st.w == w && st.h == h)
		return true;
	st.w = w;
	st.h = h;
	glBindTexture(GL_TEXTURE_2D, st.color);
	glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA32F, w, h, 0, GL_RGBA, GL_FLOAT, NULL);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
	glBindRenderbuffer(GL_RENDERBUFFER, st.depth);
	glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, w, h);
	glBindFramebuffer(GL_FRAMEBUFFER, st.fbo);
	glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, st.color, 0);
	glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, st.depth);
	GLenum draw = GL_COLOR_ATTACHMENT0;
	glDrawBuffers(1, &draw);
	bool ok = glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE;
	glBindFramebuffer(GL_FRAMEBUFFER, 0);
	if (!ok)
		fprintf(stderr, "[FL-4257] S3 GPU terrain FBO incomplete\n");
	return ok;
}

static uint8_t S3GpuDiffuse(const Renderer* r, float dzdx, float dzdy)
{
	float nl = sqrtf(dzdx * dzdx + dzdy * dzdy + (float)HEIGHT_SCALE * (float)HEIGHT_SCALE);
	float df = (dzdx * r->light[0] + dzdy * r->light[1] + (float)HEIGHT_SCALE * r->light[2]) / nl;
	df = df * (1.0f - 0.5f * r->light[3]) + 0.5f * r->light[3];
	int out = df <= 0.0f ? 0 : (int)(df * 255.0f);
	return (uint8_t)(out < 0 ? 0 : (out > 255 ? 255 : out));
}

struct S3GpuTerrainBuild
{
	Renderer* r;
	std::vector<S3GpuTerrainVert>* verts;
};

static void S3GpuTerrainPatch(Patch* p, int x, int y, int, void* cookie)
{
	S3GpuTerrainBuild* b = (S3GpuTerrainBuild*)cookie;
	uint16_t* visual = GetTerrainVisualMap(p);
	auto H = [&](int vx, int vy) -> float
	{
		double u = (double)vx / (double)VISUAL_CELLS;
		double v = (double)vy / (double)VISUAL_CELLS;
		return (float)HitTerrain(p, u, v);
	};
	auto V = [&](int vx, int vy, uint16_t vis, uint8_t dif) -> S3GpuTerrainVert
	{
		S3GpuTerrainVert out;
		out.wx = (float)(x + vx);
		out.wy = (float)(y + vy);
		out.wz = H(vx, vy);
		out.visual = (float)vis;
		out.diffuse = (float)dif;
		return out;
	};
	for (int vy = 0; vy < VISUAL_CELLS; vy++)
	{
		for (int vx = 0; vx < VISUAL_CELLS; vx++)
		{
			float h00 = H(vx, vy);
			float h10 = H(vx + 1, vy);
			float h01 = H(vx, vy + 1);
			uint8_t dif = S3GpuDiffuse(b->r, h00 - h10, h00 - h01);
			uint16_t vis = visual[vy * VISUAL_CELLS + vx];
			S3GpuTerrainVert a = V(vx, vy, vis, dif);
			S3GpuTerrainVert c = V(vx + 1, vy + 1, vis, dif);
			if (GetTerrainDiag(p) & (1 << ((vx / 2) + (vy / 2) * HEIGHT_CELLS)))
			{
				S3GpuTerrainVert b0 = V(vx + 1, vy, vis, dif);
				S3GpuTerrainVert d = V(vx, vy + 1, vis, dif);
				b->verts->push_back(a); b->verts->push_back(b0); b->verts->push_back(d);
				b->verts->push_back(c); b->verts->push_back(d); b->verts->push_back(b0);
			}
			else
			{
				S3GpuTerrainVert b0 = V(vx + 1, vy, vis, dif);
				S3GpuTerrainVert d = V(vx, vy + 1, vis, dif);
				b->verts->push_back(b0); b->verts->push_back(c); b->verts->push_back(a);
				b->verts->push_back(d); b->verts->push_back(a); b->verts->push_back(c);
			}
		}
	}
}

int RenderS3SubmitTerrainGpu(Renderer* r, Terrain* t)
{
	if (!r || !t || !RenderS3Active(r) || !r->sample_buffer.ptr)
		return 0;
	S3GpuTerrainState& st = g_s3_gpu_terrain;
	const int w = r->sample_buffer.w;
	const int h = r->sample_buffer.h;
	if (!S3GpuEnsureTarget(st, w, h))
		return 0;

	std::vector<S3GpuTerrainVert> verts;
	verts.reserve(256 * 1024);
	S3GpuTerrainBuild build = { r, &verts };
	QueryTerrain(t, 0, (double (*)[4])0, 0xFF, S3GpuTerrainPatch, &build);
	if (verts.empty())
		return 0;

	GLint old_fbo = 0, old_viewport[4] = { 0, 0, 0, 0 };
	GLint old_program = 0, old_vao = 0, old_array = 0, old_depth_func = 0;
	GLboolean old_depth = glIsEnabled(GL_DEPTH_TEST);
	GLboolean old_blend = glIsEnabled(GL_BLEND);
	glGetIntegerv(GL_FRAMEBUFFER_BINDING, &old_fbo);
	glGetIntegerv(GL_VIEWPORT, old_viewport);
	glGetIntegerv(GL_CURRENT_PROGRAM, &old_program);
	glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &old_vao);
	glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &old_array);
	glGetIntegerv(GL_DEPTH_FUNC, &old_depth_func);

	glBindFramebuffer(GL_FRAMEBUFFER, st.fbo);
	glViewport(0, 0, w, h);
	const float clear[4] = { 0.0f, 0.0f, -1.0f, 0.0f };
	glClearBufferfv(GL_COLOR, 0, clear);
	float clear_depth = 0.0f;
	glClearBufferfv(GL_DEPTH, 0, &clear_depth);
	glEnable(GL_DEPTH_TEST);
	glDepthFunc(GL_GREATER);
	glDisable(GL_BLEND);

	glUseProgram(st.prg);
	auto U = [&](const char* name) { return glGetUniformLocation(st.prg, name); };
	glUniform2f(U("u_size"), (float)w, (float)h);
	glUniform3f(U("u_anchor"), (float)r->s3_anchor[0], (float)r->s3_anchor[1], (float)r->s3_anchor[2]);
	glUniform1f(U("u_R"), (float)r->s3_R);
	glUniform4f(U("u_cam"), (float)r->s3_cam[0], (float)r->s3_cam[1], (float)r->s3_cam[2], (float)r->s3_cam[3]);
	glUniform4f(U("u_right"), (float)r->s3_right[0], (float)r->s3_right[1], (float)r->s3_right[2], (float)r->s3_right[3]);
	glUniform4f(U("u_up"), (float)r->s3_up[0], (float)r->s3_up[1], (float)r->s3_up[2], (float)r->s3_up[3]);
	glUniform4f(U("u_fwd"), (float)r->s3_fwd[0], (float)r->s3_fwd[1], (float)r->s3_fwd[2], (float)r->s3_fwd[3]);
	glUniform2f(U("u_ofs"), (float)r->s3_ofs[0], (float)r->s3_ofs[1]);
	glUniform1f(U("u_focal"), (float)r->s3_focal);
	glUniform1f(U("u_focal_y"), (float)r->s3_focal_y);

	glBindVertexArray(st.vao);
	glBindBuffer(GL_ARRAY_BUFFER, st.vbo);
	glBufferData(GL_ARRAY_BUFFER, (GLsizeiptr)(verts.size() * sizeof(S3GpuTerrainVert)), verts.data(), GL_STREAM_DRAW);
	glEnableVertexAttribArray(0);
	glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, sizeof(S3GpuTerrainVert), (void*)0);
	glEnableVertexAttribArray(1);
	glVertexAttribPointer(1, 1, GL_FLOAT, GL_FALSE, sizeof(S3GpuTerrainVert), (void*)(3 * sizeof(float)));
	glEnableVertexAttribArray(2);
	glVertexAttribPointer(2, 1, GL_FLOAT, GL_FALSE, sizeof(S3GpuTerrainVert), (void*)(4 * sizeof(float)));
	for (int image = 0; image < 2; image++)
	{
		glUniform1i(U("u_image"), image);
		glDrawArrays(GL_TRIANGLES, 0, (GLsizei)verts.size());
	}

	st.readback.resize((size_t)w * (size_t)h * 4u);
	glReadPixels(0, 0, w, h, GL_RGBA, GL_FLOAT, st.readback.data());

	unsigned writes = 0;
	for (int yy = 0; yy < h; yy++)
	{
		for (int xx = 0; xx < w; xx++)
		{
			float* px = &st.readback[((size_t)yy * (size_t)w + (size_t)xx) * 4u];
			if (px[3] < 0.5f)
				continue;
			Sample* s = r->sample_buffer.ptr + (size_t)yy * (size_t)w + (size_t)xx;
			float z = px[2] * 1000000.0f;
			if (s->height >= z)
				continue;
			uint16_t vis = (uint16_t)floorf(px[0] * 65535.0f + 0.5f);
			if (vis & 0x8000)
				z += (float)HEIGHT_SCALE;
			s->height = z;
			s->visual = vis;
			s->material_id = (int16_t)(vis & 0x00FF);
			s->diffuse = (uint8_t)floorf(px[1] * 255.0f + 0.5f);
			s->spare = (s->spare & ~(0x8 | 0x3 | 0x44));
			s->glyph_dispatch_surface = GLYPH_DISPATCH_TERRAIN_MATERIAL;
			s->s3_parity = (uint8_t)RenderS3DepthOrientationParity(z);
			writes++;
		}
	}
	r->s3_primary_count += writes;
	r->s3_antipodal_count += writes;

	glBindBuffer(GL_ARRAY_BUFFER, (GLuint)old_array);
	glBindVertexArray((GLuint)old_vao);
	glUseProgram((GLuint)old_program);
	if (old_blend) glEnable(GL_BLEND); else glDisable(GL_BLEND);
	if (old_depth) glEnable(GL_DEPTH_TEST); else glDisable(GL_DEPTH_TEST);
	glDepthFunc((GLenum)old_depth_func);
	glViewport(old_viewport[0], old_viewport[1], old_viewport[2], old_viewport[3]);
	glBindFramebuffer(GL_FRAMEBUFFER, (GLuint)old_fbo);
	return writes > 0 ? 1 : 0;
}

#else
int RenderS3SubmitTerrainGpu(Renderer*, Terrain*)
{
	return 0;
}
#endif
