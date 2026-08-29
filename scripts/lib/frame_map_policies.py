"""FL-4059 / Q2 ratchet: closed compiler-owned enum of frame-map policies.

Each policy maps a row-timeline atlas frame to a source atlas frame
deterministically. The compiler emits the fully-resolved frame_map[]
per layer; runtime stays dumb (`source_frame = layer.frame_map[row_frame]`).

Adding a new policy requires editing this file (a diff-reviewed code change),
NOT a profiles.json edit. Per FL-4065 / Q8 addability guard, every policy
must be a generic visual capability — not a skin/mount/item/profile-specific
exception.

Initial closed set:
    same_as_row         — projs/angles/anim_count/anim_lengths must match
                          source; row frame N → source frame N. Default.
    single_frame        — every row frame maps to one source frame chosen by
                          params.frame (frame index inside source anim 0).
    steady_per_anim     — within each row anim, frame_index is held at
                          params.frame; the row's anim_index passes through
                          to source anim_index (clamping is REFUSED — both
                          row and source must have the same anim count).
    explicit_table      — fully-authored {row_atlas_index: source_atlas_index}
                          dict. Every row frame must have an entry.

NOT in initial set (rejected per Q2 lock):
    hold_last_per_anim  — too close to renaming the silent clamp. Re-add
                          only when a real upstream case proves the other
                          policies are insufficient.
"""

from __future__ import annotations

from typing import Any


FRAME_MAP_POLICY_SAME_AS_ROW = "same_as_row"
FRAME_MAP_POLICY_SINGLE_FRAME = "single_frame"
FRAME_MAP_POLICY_STEADY_PER_ANIM = "steady_per_anim"
FRAME_MAP_POLICY_EXPLICIT_TABLE = "explicit_table"

ALLOWED_FRAME_MAP_POLICIES = frozenset({
    FRAME_MAP_POLICY_SAME_AS_ROW,
    FRAME_MAP_POLICY_SINGLE_FRAME,
    FRAME_MAP_POLICY_STEADY_PER_ANIM,
    FRAME_MAP_POLICY_EXPLICIT_TABLE,
})


def policy_requires_authoring(timeline_topology, source_topology) -> bool:
    """Return True iff the layer cannot use the implicit same_as_row default.

    same_as_row is implicit only when projs/angles/anim_count/anim_lengths
    all match between row timeline and source. Any divergence forces the
    author to declare an explicit policy.
    """
    if timeline_topology.projs != source_topology.projs:
        return True
    if timeline_topology.angles != source_topology.angles:
        return True
    if tuple(timeline_topology.anim_lengths) != tuple(source_topology.anim_lengths):
        return True
    return False


def _atlas_index_for_position_strict(
    topology, projection: int, angle: int, anim_index: int, frame_index: int
) -> int:
    """Strict variant — NEVER clamps. Caller must supply a valid position."""
    if projection < 0 or projection >= topology.projs:
        raise ValueError(f"projection {projection} outside source projection count {topology.projs}")
    if angle < 0 or angle >= topology.angles:
        raise ValueError(f"angle {angle} outside source angle count {topology.angles}")
    if anim_index < 0 or anim_index >= len(topology.anim_lengths):
        raise ValueError(
            f"anim_index {anim_index} outside source anim count {len(topology.anim_lengths)} "
            "(no implicit clamp — author an explicit frame_map_policy)"
        )
    source_len = topology.anim_lengths[anim_index]
    if frame_index < 0 or frame_index >= source_len:
        raise ValueError(
            f"frame_index {frame_index} outside source anim {anim_index} length {source_len} "
            "(no implicit clamp — author an explicit frame_map_policy)"
        )
    anim_offset = sum(topology.anim_lengths[:anim_index])
    anim_sum = sum(topology.anim_lengths)
    cols = topology.projs * anim_sum
    atlas_index = angle * cols + projection * anim_sum + anim_offset + frame_index
    if atlas_index < 0 or atlas_index >= topology.frames:
        raise ValueError(
            f"computed source atlas index {atlas_index} outside frame count {topology.frames}"
        )
    return atlas_index


def _atlas_position_for_index(topology, atlas_index: int) -> tuple[int, int, int, int]:
    """Inverse of atlas_index — returns (projection, angle, anim_index, frame_index)."""
    if atlas_index < 0 or atlas_index >= topology.frames:
        raise ValueError(f"atlas index {atlas_index} outside frame count {topology.frames}")
    anim_sum = sum(topology.anim_lengths)
    cols = topology.projs * anim_sum
    angle = atlas_index // cols
    rem = atlas_index - angle * cols
    projection = rem // anim_sum
    anim_column = rem - projection * anim_sum
    cursor = 0
    for anim_index, length in enumerate(topology.anim_lengths):
        if anim_column < cursor + length:
            return projection, angle, anim_index, anim_column - cursor
        cursor += length
    raise ValueError(f"atlas index {atlas_index} has no animation owner")


def validate_policy_params(policy_name: str, params: Any, *, layer_label: str) -> dict:
    """Schema-check a policy's params; return the normalised params dict.

    Caller (validator AND compiler) uses the normalised form so authoring
    typos surface uniformly.
    """
    if policy_name == FRAME_MAP_POLICY_SAME_AS_ROW:
        if params not in (None, {}, []):
            raise ValueError(
                f"{layer_label}: frame_map_policy=same_as_row takes no params; got {params!r}"
            )
        return {}
    if policy_name == FRAME_MAP_POLICY_SINGLE_FRAME:
        if not isinstance(params, dict) or set(params.keys()) != {"frame"}:
            raise ValueError(
                f"{layer_label}: frame_map_policy=single_frame requires params={{'frame': N}}; "
                f"got {params!r}"
            )
        frame = params["frame"]
        if not isinstance(frame, int) or frame < 0:
            raise ValueError(
                f"{layer_label}: frame_map_policy=single_frame.frame must be non-negative int; "
                f"got {frame!r}"
            )
        return {"frame": int(frame)}
    if policy_name == FRAME_MAP_POLICY_STEADY_PER_ANIM:
        if not isinstance(params, dict) or set(params.keys()) != {"frame"}:
            raise ValueError(
                f"{layer_label}: frame_map_policy=steady_per_anim requires params={{'frame': N}}; "
                f"got {params!r}"
            )
        frame = params["frame"]
        if not isinstance(frame, int) or frame < 0:
            raise ValueError(
                f"{layer_label}: frame_map_policy=steady_per_anim.frame must be non-negative int; "
                f"got {frame!r}"
            )
        return {"frame": int(frame)}
    if policy_name == FRAME_MAP_POLICY_EXPLICIT_TABLE:
        if not isinstance(params, dict) or set(params.keys()) != {"table"}:
            raise ValueError(
                f"{layer_label}: frame_map_policy=explicit_table requires params={{'table': [...]}}; "
                f"got {params!r}"
            )
        table = params["table"]
        if not isinstance(table, list) or not table:
            raise ValueError(
                f"{layer_label}: frame_map_policy=explicit_table.table must be non-empty list "
                f"of source-frame indices; got {type(table).__name__}"
            )
        for i, entry in enumerate(table):
            if not isinstance(entry, int) or entry < 0:
                raise ValueError(
                    f"{layer_label}: explicit_table[{i}] must be non-negative int; got {entry!r}"
                )
        return {"table": [int(e) for e in table]}
    raise ValueError(
        f"{layer_label}: unknown frame_map_policy {policy_name!r}; allowed="
        f"{sorted(ALLOWED_FRAME_MAP_POLICIES)}"
    )


def resolve_frame_map(
    *,
    policy_name: str,
    params: dict,
    timeline_topology,
    source_topology,
    layer_label: str,
) -> list[int]:
    """Compute the full row→source frame_map[] for one layer.

    Returns a list of length timeline_topology.frames where entry i is the
    source atlas index for row frame i. Caller (compiler) emits this list
    verbatim into the generated header; runtime is a dumb lookup.

    Raises ValueError on any unresolved row frame — no implicit clamping,
    no fall-through, no "best available" fallback.
    """
    frames: list[int] = []
    if policy_name == FRAME_MAP_POLICY_SAME_AS_ROW:
        # same_as_row REQUIRES identical projs/angles/anim_count/anim_lengths.
        # Strict round-trip: row atlas i → (proj, angle, anim, frame) → source
        # atlas i via the same coordinates. Caller checks shape match before
        # picking this policy; the strict atlas builder enforces it anyway.
        for row_index in range(timeline_topology.frames):
            proj, angle, anim, frame = _atlas_position_for_index(
                timeline_topology, row_index
            )
            frames.append(_atlas_index_for_position_strict(
                source_topology, proj, angle, anim, frame
            ))
        return frames
    if policy_name == FRAME_MAP_POLICY_SINGLE_FRAME:
        # Every row frame resolves to the same source atlas position.
        # Caller params.frame is the source's frame index inside anim 0.
        target_frame = params["frame"]
        if len(source_topology.anim_lengths) == 0:
            raise ValueError(
                f"{layer_label}: single_frame requires source to have at least one anim"
            )
        # Determine source position: anim 0, frame = target_frame.
        # Then row's (proj, angle) passes through.
        for row_index in range(timeline_topology.frames):
            proj, angle, _row_anim, _row_frame = _atlas_position_for_index(
                timeline_topology, row_index
            )
            if proj >= source_topology.projs or angle >= source_topology.angles:
                raise ValueError(
                    f"{layer_label}: single_frame at row {row_index} (proj={proj}, angle={angle}) "
                    f"exceeds source dims (projs={source_topology.projs}, angles={source_topology.angles})"
                )
            frames.append(_atlas_index_for_position_strict(
                source_topology, proj, angle, 0, target_frame
            ))
        return frames
    if policy_name == FRAME_MAP_POLICY_STEADY_PER_ANIM:
        # Within each row anim, frame is held at params.frame; row anim_index
        # passes through to source anim_index. REQUIRES source has the same
        # anim count as row — if a row anim has no matching source anim,
        # that's a content gap (refused, not silently mapped to anim 0).
        target_frame = params["frame"]
        if len(source_topology.anim_lengths) != len(timeline_topology.anim_lengths):
            raise ValueError(
                f"{layer_label}: steady_per_anim requires source anim count "
                f"({len(source_topology.anim_lengths)}) == row anim count "
                f"({len(timeline_topology.anim_lengths)}); shape mismatch surfaces as content gap"
            )
        for row_index in range(timeline_topology.frames):
            proj, angle, anim, _row_frame = _atlas_position_for_index(
                timeline_topology, row_index
            )
            if proj >= source_topology.projs or angle >= source_topology.angles:
                raise ValueError(
                    f"{layer_label}: steady_per_anim at row {row_index} (proj={proj}, angle={angle}) "
                    f"exceeds source dims"
                )
            frames.append(_atlas_index_for_position_strict(
                source_topology, proj, angle, anim, target_frame
            ))
        return frames
    if policy_name == FRAME_MAP_POLICY_EXPLICIT_TABLE:
        table = params["table"]
        if len(table) != timeline_topology.frames:
            raise ValueError(
                f"{layer_label}: explicit_table length {len(table)} != row timeline "
                f"frames {timeline_topology.frames}; every row frame must have an explicit entry"
            )
        for row_index, source_atlas in enumerate(table):
            if source_atlas < 0 or source_atlas >= source_topology.frames:
                raise ValueError(
                    f"{layer_label}: explicit_table[{row_index}]={source_atlas} outside source "
                    f"frame count {source_topology.frames}"
                )
            frames.append(source_atlas)
        return frames
    raise ValueError(
        f"{layer_label}: unknown frame_map_policy {policy_name!r}; allowed="
        f"{sorted(ALLOWED_FRAME_MAP_POLICIES)}"
    )
