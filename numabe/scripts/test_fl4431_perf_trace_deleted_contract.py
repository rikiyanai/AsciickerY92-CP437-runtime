#!/usr/bin/env python3
"""Guard the physical deletion of FL-4431's retired frame-trace owner.

The old profiler duplicated World._process and inserted timers, prints, plus
timestamp queries throughout the compositor callback. A disabled flag still
left a second behavior owner in source, so this contract requires deletion
rather than a default-off gate. Each in-memory mutant restores one real defect
shape and must make the contract fail.
"""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ACE_PATH = REPO / "godot_project/render/api/ASCIICompositorEffect.gd"
WORLD_PATH = REPO / "godot_project/scripts/World.gd"

ACE_FORBIDDEN = (
    "--perf-trace",
    "PERF_FRAME_TRACE",
    "PERF_GPU_TRACE",
    "PERF_READBACK_TRACE",
    "_pt_on",
    "_pt_inited",
    "_pt_gpu_supported",
    "_pt_backend",
    "_pt_acc",
    "_pt_window_total_us",
    "_pt_samples",
    "_pt_last_captured_frame",
    "_pt_size",
    "_pt_ready",
    "_pt_stamp",
    "_pt_is_owned_boundary",
    "_pt_collect_and_report",
    "_cpu_pt_acc",
    "_cpu_pt_frames",
    "_cpu_pt_total_ms",
    "_cpu_trace_begin",
    "_cpu_trace_mark",
    "_cpu_trace_add",
    "_cpu_trace_finish",
)
WORLD_FORBIDDEN = (
    "--perf-trace",
    "PERF_TRACE",
    "PERF_READBACK_TRACE",
    "_perf_trace_enabled",
    "_perf_trace_inited",
    "_perf_trace_frame",
    "_perf_acc",
    "_perf_wall_last_us",
    "_perf_wall_acc_us",
    "_perf_delta_acc_us",
    "_perf_wall_samples",
    "_perf_delta_max_ms",
    "_perf_trace_init",
    "_perf_trace_begin_frame",
    "_perf_trace_emit",
    "viewport_set_measure_render_time",
    "viewport_get_measured_render_time_cpu",
    "viewport_get_measured_render_time_gpu",
)
WORLD_PROCESS_CALLS = (
    "_poll_manual_bird_trigger_key()",
    "_process_fl4187_start_bird_trigger()",
    "_update_manual_environment_controller(delta)",
    "_sync_manual_environment_light_anchor()",
    "_publish_world_bonfire()",
    "_cleanup_hit_visuals()",
    "_sync_bird_flock_gate()",
    "prepare_airplane_presentation_for_frame()",
    "_sync_pixel_camera_to_compositor()",
    "_sync_world_mesh_face_buffer()",
    "_sync_actor_visuals_to_compositor()",
    "_process_blood_marks(delta)",
    "_process_manual_sequence_recording()",
    "_process_auto_final_render_dump()",
    "_update_airplane_mount_proximity()",
    "_emit_phase2a_world_receipt()",
)


def extract_gd_function(source: str, function_name: str) -> str:
    """Return one top-level GDScript function through its next declaration."""

    marker = f"func {function_name}("
    start = source.find(marker)
    if start < 0:
        return ""
    next_function = source.find("\nfunc ", start + len(marker))
    if next_function < 0:
        return source[start:]
    return source[start:next_function]


def contract_failures(ace: str, world: str) -> list[str]:
    """Report every restored trace owner plus duplicated World callback work."""

    failures: list[str] = []
    for token in ACE_FORBIDDEN:
        if token in ace:
            failures.append(f"ACE restored retired trace token: {token}")
    for token in WORLD_FORBIDDEN:
        if token in world:
            failures.append(f"World restored retired trace token: {token}")

    render_callback = extract_gd_function(ace, "_render_callback")
    if not render_callback:
        failures.append("ACE render callback is missing")
    if any(token in render_callback for token in ACE_FORBIDDEN):
        failures.append("ACE render callback contains a retired trace marker")

    world_process = extract_gd_function(world, "_process")
    if not world_process:
        failures.append("World process callback is missing")
    for call in WORLD_PROCESS_CALLS:
        count = world_process.count(call)
        if count != 1:
            failures.append(
                f"World process must call {call} exactly once; observed {count}"
            )
    return failures


def require_rejected(name: str, ace: str, world: str) -> None:
    """Fail loudly when a destructive mutant escapes the source contract."""

    if not contract_failures(ace, world):
        raise AssertionError(f"destructive mutant escaped: {name}")


def main() -> int:
    ace = ACE_PATH.read_text(encoding="utf-8", errors="replace")
    world = WORLD_PATH.read_text(encoding="utf-8", errors="replace")
    failures = contract_failures(ace, world)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    # Restoring the helper recreates the deleted timestamp owner even when no
    # live callback invokes it yet. The source boundary must reject that first
    # step, before a later edit can reconnect the hidden profiler.
    require_rejected(
        "trace helper restored",
        ace + "\nfunc _pt_stamp(label: String) -> void:\n\tpass\n",
        world,
    )

    # A callback stamp is frame-rate work. This mutant proves the contract sees
    # the exact hot-loop contamination that the deletion removes.
    callback_marker = (
        "func _render_callback(p_effect_callback_type: int, "
        "p_render_data: RenderData) -> void:\n"
    )
    if callback_marker not in ace:
        raise AssertionError("render callback mutation anchor is missing")
    mutated_callback = ace.replace(
        callback_marker,
        callback_marker + '\t_pt_stamp("00_start")\n',
        1,
    ) + "\nfunc _pt_stamp(label: String) -> void:\n\tpass\n"
    require_rejected("render callback marker restored", mutated_callback, world)

    # The historical World profiler duplicated the complete normal callback
    # behind this branch. Restoring the branch must fail before duplicated work
    # can diverge from gameplay publication order again.
    process_anchor = (
        "func _process(delta: float) -> void:\n"
        "\t_anim_time += delta\n"
        "\t_process_perf_orbit(delta)\n"
    )
    if process_anchor not in world:
        raise AssertionError("World process mutation anchor is missing")
    mutated_world = world.replace(
        process_anchor,
        "var _perf_trace_enabled: bool = true\n\n"
        + process_anchor
        + "\tif _perf_trace_enabled:\n\t\tpass\n",
        1,
    )
    require_rejected("World trace branch restored", ace, mutated_world)

    print("FL4431_PERF_TRACE_DELETED_CONTRACT_PASS mutants=3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
