// engine/render/fl4652_upstream_tonal_base.h
// =============================================================================
// FL-4652 automatic meshes use the upstream engine's original flat, per-face
// diffuse byte as their persistent tonal base at every progression revision.
// This pure helper owns that arithmetic so the world rasterizer and the native
// contract test execute one implementation. It has no renderer state, performs
// no sample write, and deliberately reports invalid transformed normals instead
// of falling back to the retired smooth-tone presentation path.

#pragma once

#include <math.h>
#include <stdint.h>

struct Fl4652UpstreamTonalBaseV1
{
	uint8_t diffuse_u8;
	bool valid;
};

// Fl4652ComputeUpstreamTonalBaseV1 applies the exact upstream operation order.
// `object_normal` is the geometric cross product of the face edges,
// `instance_transform` is the mesh instance's column-major 4x4 transform,
// `light` contains the three raw direction components plus ambient weight, and
// `height_scale` converts the engine's vertically scaled coordinate system.
// The returned validity bit makes malformed geometry explicit so callers can
// publish the fail-closed dark byte without consulting a second tone owner.
static inline Fl4652UpstreamTonalBaseV1
Fl4652ComputeUpstreamTonalBaseV1(const float object_normal[3],
		const double instance_transform[16], const float light[4], float height_scale)
{
	Fl4652UpstreamTonalBaseV1 out = { 0u, false };
	if (!object_normal || !instance_transform || !light ||
		!(height_scale > 0.0f) || !isfinite(height_scale))
		return out;

	// A direction has homogeneous W=0, so translation terms never influence
	// the transformed face normal.  The multiplication order matches Product().
	float transformed[3] = {
		(float)(instance_transform[0] * object_normal[0] +
			instance_transform[4] * object_normal[1] +
			instance_transform[8] * object_normal[2]),
		(float)(instance_transform[1] * object_normal[0] +
			instance_transform[5] * object_normal[1] +
			instance_transform[9] * object_normal[2]),
		(float)(instance_transform[2] * object_normal[0] +
			instance_transform[6] * object_normal[1] +
			instance_transform[10] * object_normal[2])
	};
	transformed[2] /= height_scale;
	const float length_squared = transformed[0] * transformed[0] +
		transformed[1] * transformed[1] + transformed[2] * transformed[2];
	if (!(length_squared > 0.0f) || !isfinite(length_squared))
		return out;

	const float inverse_length = 1.0f / sqrtf(length_squared);
	float diffuse = inverse_length * (transformed[0] * light[0] +
		transformed[1] * light[1] + transformed[2] * light[2]);
	// Upstream mixes ambient before adding its half-range display bias.  The
	// raw light vector is intentionally not normalized in this legacy formula.
	diffuse = diffuse * (1.0f - 0.5f * light[3]) + 0.5f * light[3];
	diffuse += 0.5f;
	if (!isfinite(diffuse))
		return out;
	if (diffuse < 0.0f) diffuse = 0.0f;
	if (diffuse > 1.0f) diffuse = 1.0f;
	out.diffuse_u8 = (uint8_t)(diffuse * 255.0f);
	out.valid = true;
	return out;
}
