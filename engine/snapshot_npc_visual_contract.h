#pragma once

#include <stdint.h>

struct SnapshotNpcVisualResolveDecision
{
	bool presentation_kind_unset;
	bool need_resolve;
	bool clear_cached_sprite;
	bool reset_sprite_miss_frames;
};

static inline SnapshotNpcVisualResolveDecision SnapshotNpcVisualResolveDecisionFor(
	uint16_t cached_presentation_kind_id,
	uint32_t cached_presentation_started_tick,
	bool cached_sprite_present,
	uint16_t snapshot_presentation_kind_id,
	uint32_t snapshot_presentation_started_tick)
{
	SnapshotNpcVisualResolveDecision decision = {};
	decision.presentation_kind_unset = snapshot_presentation_kind_id == 0;
	if (decision.presentation_kind_unset)
	{
		bool presentation_changed =
			cached_presentation_kind_id != 0 ||
			cached_presentation_started_tick != snapshot_presentation_started_tick;
		decision.clear_cached_sprite =
			cached_sprite_present ||
			presentation_changed;
		decision.need_resolve = false;
		decision.reset_sprite_miss_frames = false;
		return decision;
	}

	bool presentation_changed =
		cached_presentation_kind_id != snapshot_presentation_kind_id ||
		cached_presentation_started_tick != snapshot_presentation_started_tick;
	decision.need_resolve = presentation_changed || !cached_sprite_present;
	decision.clear_cached_sprite = false;
	decision.reset_sprite_miss_frames = presentation_changed;
	return decision;
}
