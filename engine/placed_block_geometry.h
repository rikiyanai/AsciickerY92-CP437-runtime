#pragma once

#include <math.h>
#include <stdio.h>

#include "sprite.h"

struct PlacedBlockGeometry
{
	float half_extent;
	float height;
	float visual_bottom_z;
	float visual_top_z;
};

// FL-4137 #57 off-by-one cell subtraction was REVERTED 2026-05-30 — operator:
// "OFF BY ONE FIX WAS ALREADY TRIED". Per the FL log entry "Sprite proj_bbox
// investigation falsified the hidden-short-content hypothesis", subtracting
// one cell-z from raw_height was a falsified approach. proj_bbox[5] is the
// authoritative top per the sprite contract; the wireframe and collision
// both use it directly and stay aligned.

static inline bool PlacedBlockGeometryFromSpriteProjection(
	const Sprite* sprite,
	float authored_half_extent,
	float authored_height,
	PlacedBlockGeometry* out,
	char* errbuf = 0,
	int errbuf_size = 0)
{
	if (out)
		*out = {};
	if (!sprite)
	{
		if (errbuf && errbuf_size > 0)
			snprintf(errbuf, (size_t)errbuf_size, "missing sprite");
		return false;
	}
	const float min_x = sprite->proj_bbox[0];
	const float max_x = sprite->proj_bbox[1];
	const float min_y = sprite->proj_bbox[2];
	const float max_y = sprite->proj_bbox[3];
	const float bottom_z = sprite->proj_bbox[4];
	const float top_z = sprite->proj_bbox[5];
	if (!isfinite(min_x) || !isfinite(max_x) ||
		!isfinite(min_y) || !isfinite(max_y) ||
		!isfinite(bottom_z) || !isfinite(top_z))
	{
		if (errbuf && errbuf_size > 0)
			snprintf(errbuf, (size_t)errbuf_size, "non-finite sprite projection bbox");
		return false;
	}
	const float bottom_eps = 0.01f;
	if (fabsf(bottom_z) > bottom_eps)
	{
		if (errbuf && errbuf_size > 0)
			snprintf(errbuf, (size_t)errbuf_size,
				"sprite visual bottom must be anchored at placed pos.z: bottom_z=%.3f",
				bottom_z);
		return false;
	}
		const float raw_half_extent = fmaxf(fmaxf(fabsf(min_x), fabsf(max_x)),
			fmaxf(fabsf(min_y), fabsf(max_y)));
		const float raw_height = top_z;
		const float height = authored_height > 0.0f ? authored_height : raw_height;
		const float scale = raw_height > 0.0f ? height / raw_height : 1.0f;
		const float half_extent =
			authored_half_extent > 0.0f ? authored_half_extent : raw_half_extent * scale;
		if (!(half_extent > 0.0f) || !(height > 0.0f))
		{
		if (errbuf && errbuf_size > 0)
			snprintf(errbuf, (size_t)errbuf_size,
				"invalid placed block geometry half=%.3f height=%.3f",
				half_extent, height);
		return false;
	}
	if (out)
	{
		out->half_extent = half_extent;
		out->height = height;
			out->visual_bottom_z = bottom_z * scale;
			out->visual_top_z = height;
		}
		return true;
}

static inline bool PlacedBlockGeometryLoadFromSpritePath(
	const char* path,
	const char* slug,
	float authored_half_extent,
	float authored_height,
	PlacedBlockGeometry* out,
	char* errbuf = 0,
	int errbuf_size = 0)
{
	if (!path || !path[0])
	{
		if (errbuf && errbuf_size > 0)
			snprintf(errbuf, (size_t)errbuf_size, "missing sprite path");
		return false;
	}
	Sprite* sprite = LoadSprite(path, slug ? slug : "placed_block", 0, true, true);
	if (!sprite)
	{
		if (errbuf && errbuf_size > 0)
			snprintf(errbuf, (size_t)errbuf_size, "failed to load sprite path '%s'", path);
		return false;
	}
	const bool ok = PlacedBlockGeometryFromSpriteProjection(
		sprite, authored_half_extent, authored_height, out, errbuf, errbuf_size);
	FreeSprite(sprite);
	return ok;
}
