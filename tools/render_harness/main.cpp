// main.cpp — render_harness CLI
//
// Usage (from the repo root):
//   .run/render_harness <scene> out=<dir> [w=40] [h=24] [fps=30] [frames=210]
//                       [font=assets/fonts/cp437_16x16.png] [cells=1] [crop_bottom=0] [scene args...]
//   .run/render_harness --list
//
// Writes <out>/frame_0000.png ... (and frame_NNNN.cells raw AnsiCell dumps
// with cells=1: int32 w, int32 h, then w*h * {fg,bk,gl,spare}, bottom row
// first, exactly as the engine buffer). GIF assembly is done by
// tools/render_harness/make_gif.py.

#include "harness.h"

#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <errno.h>

static void Usage()
{
	fprintf(stderr, "usage: render_harness <scene> out=<dir> [w=40] [h=24] [fps=30] [frames=210] [font=...] [cells=1] [key=value ...]\n");
	fprintf(stderr, "scenes:\n");
	for (auto& s : HarnessSceneRegistry())
		fprintf(stderr, "  %-12s %s\n", s.name, s.help);
}

static bool MkdirP(const std::string& p)
{
	std::string cur;
	for (size_t i = 0; i < p.size(); i++)
	{
		cur += p[i];
		if (p[i] == '/' || i + 1 == p.size())
			if (mkdir(cur.c_str(), 0755) != 0 && errno != EEXIST)
				return false;
	}
	return true;
}

int main(int argc, char** argv)
{
	if (argc < 2 || !strcmp(argv[1], "--list") || !strcmp(argv[1], "-h") || !strcmp(argv[1], "--help"))
	{
		Usage();
		return argc < 2 ? 1 : 0;
	}

	const HarnessScene* scene = nullptr;
	for (auto& s : HarnessSceneRegistry())
		if (!strcmp(s.name, argv[1]))
			scene = &s;
	if (!scene)
	{
		fprintf(stderr, "unknown scene '%s'\n", argv[1]);
		Usage();
		return 1;
	}

	HarnessArgs args;
	for (int i = 2; i < argc; i++)
	{
		const char* eq = strchr(argv[i], '=');
		if (!eq)
		{
			fprintf(stderr, "bad argument '%s' (expected key=value)\n", argv[i]);
			return 1;
		}
		args[std::string(argv[i], eq - argv[i])] = eq + 1;
	}

	extern char base_path[1024];
	Harness h;
	h.repo_root = HarnessArg(args, "root", "./");
	if (h.repo_root.back() != '/')
		h.repo_root += '/';
	snprintf(base_path, sizeof(base_path), "%s", h.repo_root.c_str());
	h.width = HarnessArgI(args, "w", 40);
	h.height = HarnessArgI(args, "h", 24);
	h.fps = HarnessArgI(args, "fps", 30);
	h.frames = HarnessArgI(args, "frames", 210);
	h.font_path = h.Path(HarnessArg(args, "font", "assets/fonts/cp437_16x16.png"));
	h.out_dir = HarnessArg(args, "out", "");
	h.dump_cells = HarnessArgI(args, "cells", 0) != 0;
	if (h.out_dir.empty())
	{
		fprintf(stderr, "out=<dir> is required\n");
		return 1;
	}
	if (!MkdirP(h.out_dir))
	{
		fprintf(stderr, "cannot create %s\n", h.out_dir.c_str());
		return 1;
	}

	HarnessFont font;
	if (!font.Load(h.font_path.c_str()))
	{
		fprintf(stderr, "cannot load CP437 font atlas %s\n", h.font_path.c_str());
		return 1;
	}

	if (!scene->setup(&h, args))
		return 1;

	// Framing: the game centres the camera on the actor's feet. crop_bottom=N
	// renders N extra rows (a taller game viewport) and drops the bottom N rows
	// from the output, which lifts the body toward the image centre without
	// moving the camera (the blob shadow is keyed to camera pos).
	const int out_h = h.height;
	const int crop_bottom = HarnessArgI(args, "crop_bottom", 0);
	h.height = out_h + crop_bottom;

	std::vector<uint8_t> rgb;
	for (int tick = 0; tick < h.frames; tick++)
	{
		if (scene->tick)
			scene->tick(&h, tick);
		h.RenderFrame(tick);
		if (scene->post_render)
			scene->post_render(&h, h.cells.data(), h.width, h.height, tick);

		char path[2048];
		if (h.dump_cells)
		{
			snprintf(path, sizeof(path), "%s/frame_%04d.cells", h.out_dir.c_str(), tick);
			FILE* f = fopen(path, "wb");
			if (f)
			{
				int32_t wh[2] = { h.width, h.height };
				fwrite(wh, sizeof(wh), 1, f);
				fwrite(h.cells.data(), sizeof(AnsiCell), h.cells.size(), f);
				fclose(f);
			}
		}
		// buffer is bottom-up: skipping the first crop_bottom rows drops the bottom.
		HarnessRasterize(font, h.cells.data() + (size_t)crop_bottom * h.width, h.width, out_h, rgb);
		snprintf(path, sizeof(path), "%s/frame_%04d.png", h.out_dir.c_str(), tick);
		if (!HarnessWritePng(path, rgb.data(), h.width * font.cell_w, out_h * font.cell_h))
		{
			fprintf(stderr, "cannot write %s\n", path);
			return 1;
		}
	}
	fprintf(stderr, "[harness] wrote %d frames (%dx%d cells, %dx%d px) to %s\n", h.frames, h.width, out_h,
		h.width * font.cell_w, out_h * font.cell_h, h.out_dir.c_str());
	return 0;
}
