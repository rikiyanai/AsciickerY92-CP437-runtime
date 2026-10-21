#pragma once

// Product mode is selected before a map is loaded. The map header declares
// compatibility; it never chooses the running geometry or renderer.

#include <stdint.h>
#include <string.h>

enum AsciickerWorldGeometryModeV1 : uint8_t
{
	ASCIICKER_WORLD_GEOMETRY_EUCLIDEAN_V1 = 0,
	ASCIICKER_WORLD_GEOMETRY_SPHERICAL_V1 = 1,
	ASCIICKER_WORLD_GEOMETRY_INVALID_V1 = 0xFFu,
};

enum AsciickerRendererModeV1 : uint8_t
{
	ASCIICKER_RENDERER_CLASSIC_CP437_V1 = 0,
	ASCIICKER_RENDERER_FL4512_UNICODE_V1 = 1,
	ASCIICKER_RENDERER_INVALID_V1 = 0xFFu,
};

struct AsciickerRuntimeProductSelectionV1
{
	AsciickerWorldGeometryModeV1 world_geometry;
	AsciickerRendererModeV1 renderer;
};

// Own-binary identity: which adapter this binary links. This is the one
// legitimate product query in shared code (link identity, not selection);
// all selection, parsing, and naming below is unconditional so a binary
// asked for another renderer parses successfully and fails at bind time.
static inline AsciickerRendererModeV1 AsciickerBuiltRendererModeV1()
{
#if defined(ASCIICKER_RENDERER_CLASSIC_CP437)
	return ASCIICKER_RENDERER_CLASSIC_CP437_V1;
#else
	return ASCIICKER_RENDERER_FL4512_UNICODE_V1;
#endif
}

static inline AsciickerRuntimeProductSelectionV1& AsciickerRequestedProductSelectionV1()
{
	static AsciickerRuntimeProductSelectionV1 selection = {
		ASCIICKER_WORLD_GEOMETRY_EUCLIDEAN_V1,
		AsciickerBuiltRendererModeV1(),
	};
	return selection;
}

static inline bool AsciickerParseWorldGeometryModeV1(
	const char* text, AsciickerWorldGeometryModeV1* out)
{
	if (!text || !out)
		return false;
	if (strcmp(text, "euclidean") == 0)
	{
		*out = ASCIICKER_WORLD_GEOMETRY_EUCLIDEAN_V1;
		return true;
	}
	if (strcmp(text, "spherical") == 0)
	{
		*out = ASCIICKER_WORLD_GEOMETRY_SPHERICAL_V1;
		return true;
	}
	return false;
}

static inline bool AsciickerParseRendererModeV1(
	const char* text, AsciickerRendererModeV1* out)
{
	if (!text || !out)
		return false;
	if (strcmp(text, "classic_cp437") == 0)
	{
		*out = ASCIICKER_RENDERER_CLASSIC_CP437_V1;
		return true;
	}
	// Both names parse in every product: a Classic binary asked for
	// fl4512_unicode parses successfully and fails later at bind time
	// (RENDERER_BINARY_MISMATCH), never at parse time.
	if (strcmp(text, "fl4512_unicode") == 0)
	{
		*out = ASCIICKER_RENDERER_FL4512_UNICODE_V1;
		return true;
	}
	return false;
}

static inline const char* AsciickerWorldGeometryModeNameV1(
	AsciickerWorldGeometryModeV1 mode)
{
	switch (mode)
	{
		case ASCIICKER_WORLD_GEOMETRY_EUCLIDEAN_V1: return "euclidean";
		case ASCIICKER_WORLD_GEOMETRY_SPHERICAL_V1: return "spherical";
		default: return "invalid";
	}
}

static inline const char* AsciickerRendererModeNameV1(
	AsciickerRendererModeV1 mode)
{
	switch (mode)
	{
		case ASCIICKER_RENDERER_CLASSIC_CP437_V1: return "classic_cp437";
		case ASCIICKER_RENDERER_FL4512_UNICODE_V1: return "fl4512_unicode";
		default: return "invalid";
	}
}

static inline const char* AsciickerGameModeNameV1(
	const AsciickerRuntimeProductSelectionV1& selection)
{
	if (selection.renderer == ASCIICKER_RENDERER_CLASSIC_CP437_V1)
	{
		return selection.world_geometry == ASCIICKER_WORLD_GEOMETRY_SPHERICAL_V1
			? "spherical_classic"
			: "classic_euclidean";
	}
	return selection.world_geometry == ASCIICKER_WORLD_GEOMETRY_EUCLIDEAN_V1
		? "fl4512_unicode_euclidean"
		: "unsupported";
}

static inline bool AsciickerProductSelectionSupportedV1(
	const AsciickerRuntimeProductSelectionV1& selection)
{
	if (selection.renderer == ASCIICKER_RENDERER_CLASSIC_CP437_V1)
		return selection.world_geometry == ASCIICKER_WORLD_GEOMETRY_EUCLIDEAN_V1 ||
			selection.world_geometry == ASCIICKER_WORLD_GEOMETRY_SPHERICAL_V1;
	return selection.renderer == ASCIICKER_RENDERER_FL4512_UNICODE_V1 &&
		selection.world_geometry == ASCIICKER_WORLD_GEOMETRY_EUCLIDEAN_V1;
}

static inline uint8_t AsciickerWorldGeometryCurvatureKindV1(
	AsciickerWorldGeometryModeV1 mode)
{
	return mode == ASCIICKER_WORLD_GEOMETRY_EUCLIDEAN_V1 ? 0u :
		mode == ASCIICKER_WORLD_GEOMETRY_SPHERICAL_V1 ? 1u : 0xFFu;
}

static inline bool AsciickerMapGeometryCompatibleV1(
	AsciickerWorldGeometryModeV1 selected, uint8_t map_declared_kind)
{
	return AsciickerWorldGeometryCurvatureKindV1(selected) == map_declared_kind;
}
