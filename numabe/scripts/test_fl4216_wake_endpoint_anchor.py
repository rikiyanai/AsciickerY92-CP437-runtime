"""FL-4216/FL-4431 host-owned wake-ring topology contract.

The compositor host already owns source lanes, cursors, and insertion order.
It therefore publishes each row's immediate predecessor and unique newest
marker in metadata z/w. Product validates those facts in O(1) per row and
keeps the established receiver-dependent paint arithmetic.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import math
from dataclasses import dataclass, replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
WAKE_FIELD = ROOT / "godot_project/render/effects/water/WakeTrailField.gd"
COMPOSITOR = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
WAKE_CAPACITY = 32
LANE_SIZE = 8
MAX_SOURCES = 4
VISUAL_AGE = 1.8
RETIRE_AGE = 3.5
MAX_EXACT_SEQUENCE = 16_777_215
WAKE_SOURCE_PLAYER = 1


def function_body(text: str, signature: str, *, last: bool = False) -> str:
    """Return one balanced function so an unrelated token cannot satisfy it."""

    start = text.rindex(signature) if last else text.index(signature)
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def gd_function_body(text: str, signature: str) -> str:
    """Return one top-level GDScript function by its next func boundary."""

    start = text.index(signature)
    next_function = text.find("\nfunc ", start + len(signature))
    return text[start:] if next_function < 0 else text[start:next_function]


@dataclass
class WakeRow:
    x: float = 0.0
    z: float = 0.0
    server_time: float = -1.0
    source_id: float = 0.0
    sequence: float = 0.0
    predecessor_plus_one: float = 0.0
    latest: float = 0.0


@dataclass(frozen=True)
class WakeFact:
    strength: float
    source_id: int
    along: float


def exact_int(value: float, minimum: int, maximum: int) -> int | None:
    if (
        not math.isfinite(value)
        or value < minimum
        or value > maximum
        or math.floor(value) != value
    ):
        return None
    return int(value)


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    ratio = min(max((value - edge0) / (edge1 - edge0), 0.0), 1.0)
    return ratio * ratio * (3.0 - 2.0 * ratio)


def distance_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float]:
    ab = (end[0] - start[0], end[1] - start[1])
    denominator = max(ab[0] * ab[0] + ab[1] * ab[1], 0.0001)
    along = min(
        max(
            (
                (point[0] - start[0]) * ab[0]
                + (point[1] - start[1]) * ab[1]
            )
            / denominator,
            0.0,
        ),
        1.0,
    )
    nearest = (start[0] + ab[0] * along, start[1] + ab[1] * along)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1]), along


def live_row(
    rows: list[WakeRow], index: int, clock: float
) -> tuple[WakeRow, int, int, int, bool, bool] | None:
    if index < 0 or index >= WAKE_CAPACITY:
        return None
    row = rows[index]
    if (
        not math.isfinite(row.x)
        or not math.isfinite(row.z)
        or not math.isfinite(row.server_time)
        or not math.isfinite(clock)
        or row.server_time < 0.0
        or clock < row.server_time
        or clock - row.server_time > VISUAL_AGE
    ):
        return None
    source = exact_int(row.source_id, 1, MAX_EXACT_SEQUENCE)
    sequence = exact_int(row.sequence, 1, MAX_EXACT_SEQUENCE)
    if source is None or sequence is None:
        return None
    predecessor = exact_int(row.predecessor_plus_one, 0, WAKE_CAPACITY)
    predecessor_valid = (
        predecessor is not None and predecessor != index + 1
    )
    if not predecessor_valid:
        predecessor = 0
    latest = exact_int(row.latest, 0, 1)
    return (
        row,
        source,
        sequence,
        predecessor,
        predecessor_valid,
        latest == 1,
    )


def published_wake_fact(
    rows: list[WakeRow],
    receiver_xz: tuple[float, float],
    clock: float,
    live_player_xz: tuple[float, float],
) -> WakeFact:
    """Execute Product's direct metadata consumer."""

    best = WakeFact(0.0, 0, 0.0)
    half_width = 0.72
    for index in range(WAKE_CAPACITY):
        decoded = live_row(rows, index, clock)
        if decoded is None:
            continue
        point, source, sequence, predecessor_plus_one, pred_ok, latest = decoded
        predecessor: WakeRow | None = None
        if pred_ok and predecessor_plus_one:
            pred = live_row(rows, predecessor_plus_one - 1, clock)
            if (
                pred is not None
                and pred[1] == source
                and pred[2] + 1 == sequence
            ):
                predecessor = pred[0]
        if (
            predecessor is not None
            and math.hypot(
                point.x - predecessor.x, point.z - predecessor.z
            )
            >= 0.05
        ):
            distance, along = distance_to_segment(
                receiver_xz,
                (predecessor.x, predecessor.z),
                (point.x, point.z),
            )
            strength = 1.0 - smoothstep(
                half_width, half_width * 1.70, distance
            )
        else:
            distance = math.hypot(
                receiver_xz[0] - point.x,
                receiver_xz[1] - point.z,
            )
            strength = (
                1.0
                - smoothstep(
                    half_width * 0.72,
                    half_width * 1.20,
                    distance,
                )
            ) * 0.45
            along = 0.5
        if source == WAKE_SOURCE_PLAYER and latest:
            live_distance, live_along = distance_to_segment(
                receiver_xz,
                (point.x, point.z),
                live_player_xz,
            )
            live_strength = 1.0 - smoothstep(
                half_width, half_width * 1.70, live_distance
            )
            if live_strength > strength:
                strength = live_strength
                along = live_along
        if strength > best.strength:
            best = WakeFact(strength, source, along)
    strength = min(max(best.strength, 0.0), 1.0)
    if strength <= 0.12:
        return WakeFact(0.0, 0, 0.0)
    return WakeFact(strength, best.source_id, best.along)


def scanned_wake_fact(
    rows: list[WakeRow],
    receiver_xz: tuple[float, float],
    clock: float,
    live_player_xz: tuple[float, float],
) -> WakeFact:
    """Execute the deleted greatest-lower-sequence topology owner."""

    rebuilt = [replace(row) for row in rows]
    live = [live_row(rows, index, clock) for index in range(WAKE_CAPACITY)]
    for index, decoded in enumerate(live):
        if decoded is None:
            continue
        _point, source, sequence, _pred, _pred_ok, _latest = decoded
        candidates = [
            (candidate[2], candidate_index)
            for candidate_index, candidate in enumerate(live)
            if candidate is not None
            and candidate[1] == source
            and candidate[2] < sequence
        ]
        rebuilt[index].predecessor_plus_one = (
            float(max(candidates)[1] + 1) if candidates else 0.0
        )
        rebuilt[index].latest = float(
            not any(
                candidate is not None
                and candidate[1] == source
                and candidate[2] > sequence
                for candidate in live
            )
        )
    return published_wake_fact(
        rebuilt, receiver_xz, clock, live_player_xz
    )


class HostRing:
    """Small executable model of the host's four fixed eight-row lanes."""

    def __init__(self) -> None:
        self.rows = [WakeRow() for _ in range(WAKE_CAPACITY)]
        self.lane_by_source: dict[int, int] = {}
        self.source_by_lane: dict[int, int] = {}
        self.cursor: dict[int, int] = {}
        self.sequence: dict[int, int] = {}
        self.last_xz: dict[int, tuple[float, float]] = {}
        self.clock = -1.0
        self.last_reason = ""
        self.player_xz = (0.0, 0.0)
        self.player_world = (0.0, 0.0, 0.0)
        self.last_player_tick = -1
        self.last_player_rate = 0.0
        self.live_endpoint = (0.0, 0.0)

    def snapshot(self) -> tuple:
        return (
            tuple(
                (
                    row.x,
                    row.z,
                    row.server_time,
                    row.source_id,
                    row.sequence,
                    row.predecessor_plus_one,
                    row.latest,
                )
                for row in self.rows
            ),
            dict(self.lane_by_source),
            dict(self.source_by_lane),
            dict(self.cursor),
            dict(self.sequence),
            dict(self.last_xz),
            self.clock,
            self.player_xz,
            self.player_world,
            self.last_player_tick,
            self.last_player_rate,
            self.live_endpoint,
        )

    def clear(self, source: int) -> None:
        lane = self.lane_by_source.pop(source)
        self.source_by_lane.pop(lane)
        for index in range(lane * LANE_SIZE, (lane + 1) * LANE_SIZE):
            self.rows[index] = WakeRow()
        self.cursor.pop(source, None)
        self.sequence.pop(source, None)
        self.last_xz.pop(source, None)

    def allocate(self, source: int) -> int | None:
        if source in self.lane_by_source:
            return self.lane_by_source[source]
        for lane in range(MAX_SOURCES):
            if lane not in self.source_by_lane:
                self.source_by_lane[lane] = source
                self.lane_by_source[source] = lane
                return lane
        self.last_reason = "wake_source_capacity_full"
        return None

    def latest_time(self, source: int) -> float:
        lane = self.lane_by_source.get(source)
        if lane is None:
            return -1.0
        times = [
            row.server_time
            for row in self.rows[lane * LANE_SIZE : (lane + 1) * LANE_SIZE]
            if exact_int(row.source_id, 1, MAX_EXACT_SEQUENCE) == source
            and math.isfinite(row.server_time)
            and row.server_time >= 0.0
        ]
        return max(times, default=-1.0)

    def prune(self, server_time: float) -> None:
        for source in list(self.lane_by_source):
            latest = self.latest_time(source)
            if latest < 0.0 or server_time - latest > RETIRE_AGE:
                self.clear(source)

    def push(
        self, xz: tuple[float, float], server_time: float, source: int
    ) -> bool:
        if (
            source <= 0
            or not all(math.isfinite(value) for value in (*xz, server_time))
            or server_time < 0.0
        ):
            return False
        source_time = self.latest_time(source)
        if source_time >= 0.0 and server_time < source_time:
            self.last_reason = "wake_source_time_regressed"
            return False
        if self.clock >= 0.0 and server_time < self.clock:
            self.last_reason = "wake_global_time_regressed"
            return False
        self.prune(server_time)
        previous_xz = self.last_xz.get(source)
        if (
            previous_xz is not None
            and math.dist(previous_xz, xz) < 0.4
        ):
            self.clock = server_time
            return False
        lane = self.allocate(source)
        if lane is None:
            return False
        self.clock = server_time
        sequence = self.sequence.get(source, 0)
        if sequence >= MAX_EXACT_SEQUENCE:
            self.clear(source)
            lane = self.allocate(source)
            assert lane is not None
            sequence = 0
        cursor = self.cursor.get(source, 0)
        base = lane * LANE_SIZE
        tail = base + (cursor - 1 + LANE_SIZE) % LANE_SIZE
        if sequence:
            tail_row = self.rows[tail]
            if (
                exact_int(tail_row.source_id, 1, MAX_EXACT_SEQUENCE)
                != source
                or exact_int(
                    tail_row.sequence, 1, MAX_EXACT_SEQUENCE
                )
                != sequence
                or exact_int(tail_row.latest, 0, 1) != 1
            ):
                self.clear(source)
                self.last_reason = "wake_topology_tail_invalid"
                return False
        overwritten = base + cursor
        old = self.rows[overwritten]
        if not (
            old.source_id == 0.0
            and old.sequence == 0.0
            and old.server_time < 0.0
        ):
            old_source = exact_int(
                old.source_id, 0, MAX_EXACT_SEQUENCE
            )
            old_sequence = exact_int(
                old.sequence, 0, MAX_EXACT_SEQUENCE
            )
            successor = base + (cursor + 1) % LANE_SIZE
            successor_row = self.rows[successor]
            if (
                old_source != source
                or old_sequence is None
                or old_sequence <= 0
                or exact_int(
                    successor_row.source_id, 1, MAX_EXACT_SEQUENCE
                )
                != source
                or exact_int(
                    successor_row.sequence, 1, MAX_EXACT_SEQUENCE
                )
                != old_sequence + 1
                or exact_int(
                    successor_row.predecessor_plus_one, 0, WAKE_CAPACITY
                )
                != overwritten + 1
            ):
                self.clear(source)
                self.last_reason = "wake_topology_overwrite_invalid"
                return False
            successor_row.predecessor_plus_one = 0.0
        if sequence:
            self.rows[tail].latest = 0.0
        self.rows[overwritten] = WakeRow(
            xz[0],
            xz[1],
            server_time,
            float(source),
            float(sequence + 1),
            float(tail + 1) if sequence else 0.0,
            1.0,
        )
        self.cursor[source] = (cursor + 1) % LANE_SIZE
        self.sequence[source] = sequence + 1
        self.last_xz[source] = xz
        return True

    def set_player(
        self,
        xz: tuple[float, float],
        tick: int,
        rate: float,
        emit_player_wake: bool = True,
    ) -> bool:
        if (
            not all(math.isfinite(value) for value in (*xz, rate))
            or tick < 0
            or rate <= 0.0
        ):
            return False
        server_time = tick / rate
        previous_time = (
            self.last_player_tick / self.last_player_rate
            if self.last_player_tick >= 0 and self.last_player_rate > 0.0
            else -1.0
        )
        if (
            previous_time >= 0.0
            and server_time < previous_time
        ) or (self.clock >= 0.0 and server_time < self.clock):
            return False
        self.player_xz = xz
        self.player_world = (xz[0], self.player_world[1], xz[1])
        self.last_player_tick = tick
        self.last_player_rate = rate
        if not emit_player_wake:
            return True
        self.live_endpoint = xz
        return self.push(xz, server_time, WAKE_SOURCE_PLAYER)


def source_contract_errors(
    product_text: str, host_text: str | None = None
) -> list[str]:
    """Return structural violations across both topology owners."""

    if host_text is None:
        host_text = (
            WAKE_FIELD.read_text(encoding="utf-8")
            + "\n"
            + COMPOSITOR.read_text(encoding="utf-8")
        )
    errors: list[str] = []
    wake = function_body(
        product_text,
        "ProductWakeFact product_wake_fact(vec2 world_xz)",
        last=True,
    )
    live = function_body(product_text, "bool product_wake_live_row(")
    main = function_body(product_text, "void main()", last=True)
    push = gd_function_body(
        host_text, "func push_sample_at_server_time("
    )
    overwrite = gd_function_body(
        host_text, "func detach_overwritten_predecessor("
    )
    latest_time = gd_function_body(
        host_text, "func latest_server_time_for_source("
    )
    player_admission = gd_function_body(
        host_text, "func _player_server_pose_admitted("
    )
    player_setter = gd_function_body(
        host_text, "func set_player_world_xz_at_server_tick("
    )

    for forbidden in (
        "product_wake_topology_rows",
        "product_wake_topology_ready",
        "product_wake_materialize_topology",
        "product_wake_topology_needed",
    ):
        if forbidden in product_text:
            errors.append(f"retired Product topology owner survived: {forbidden}")
    if (
        "const float PRODUCT_WAKE_VISUAL_AGE_SECONDS = 1.8;"
        not in product_text
    ):
        errors.append("Product wake visual age changed")
    if "for (int j = 0; j < WAKE_TRAIL_CAPACITY; j++)" in wake:
        errors.append("nested Product topology scan restored")
    for token in (
        "meta.z, 0u, uint(WAKE_TRAIL_CAPACITY)",
        "predecessor_row_plus_one != uint(index) + 1u",
        "meta.w, 0u, 1u",
        "wake_clock.value.x < point.w",
        "age > PRODUCT_WAKE_VISUAL_AGE_SECONDS",
    ):
        if token not in live:
            errors.append(f"Product row validation missing: {token}")
    for token in (
        "predecessor_source_id == point_source_id",
        "predecessor_sequence + 1u == point_sequence",
        "product_distance_to_segment(world_xz, prev.xy, point.xy",
        "point_source_id == WAKE_SOURCE_PLAYER && is_latest_sample",
        "wake_clock.value.zw",
        "live_strength > strength",
    ):
        if token not in wake:
            errors.append(f"Product relationship law missing: {token}")
    if "product_wake_materialize_topology" in main:
        errors.append("main still materializes invocation-private topology")
    if product_text.count(
        "ProductWakeFact wake_fact = product_wake_fact(s.world_xz);"
    ) != 1:
        errors.append("receiver-specific wake query count changed")

    for token in (
        "const WAKE_TRAIL_MAX_EXACT_SEQUENCE := 16777215",
        "const WAKE_TRAIL_VISUAL_AGE_SECONDS := 1.8",
        "static func exact_int(",
        "func current_tail_slot(",
        "func detach_overwritten_predecessor(",
    ):
        if token not in host_text:
            errors.append(f"host topology authority missing: {token}")
    for token in (
        'server_time_s < prior_source_time',
        '"wake_source_time_regressed"',
        'server_time_s < current_server_time_s',
        '"wake_global_time_regressed"',
        "current_sequence >= WAKE_TRAIL_MAX_EXACT_SEQUENCE",
        "clear_lane(lane, source_id)",
        "if slot_base < 0:\n\t\t\treturn\n\t\tcursor = 0",
        "prior_tail_slot * WAKE_TRAIL_STRIDE + 7",
        "float(prior_tail_slot + 1) if prior_tail_slot >= 0 else 0.0",
        "trail_data[base + 7] = 1.0",
    ):
        if token not in push:
            errors.append(f"host insertion law missing: {token}")
    for token in (
        "trail_data[successor_base + 5]",
        "overwritten_sequence + 1",
        "overwritten_slot + 1",
        "trail_data[successor_base + 6] = 0.0",
    ):
        if token not in overwrite:
            errors.append(f"overwrite repair missing: {token}")
    if "exact_int(" not in latest_time:
        errors.append("latest-time scan rounds malformed source metadata")
    for token in (
        "not xz.is_finite()",
        "server_tick < 0",
        "not is_finite(server_tick_rate_hz)",
        "server_time_s < previous_server_time_s",
        "server_time_s < wake_trail.current_server_time_s",
    ):
        if token not in player_admission:
            errors.append(f"player pose admission missing: {token}")
    for retired_writer in (
        "func set_player_world_xz(xz: Vector2)",
        "func set_player_world_at_server_tick(",
    ):
        if retired_writer in host_text:
            errors.append(f"retired player pose writer restored: {retired_writer}")
    admission_call = player_setter.find(
        "if not _player_server_pose_admitted("
    )
    player_write = player_setter.find("_player_world_xz = xz")
    emit_return = player_setter.find("if not emit_player_wake:")
    wake_body_write = player_setter.find("_wake_trail_last_body_xz = xz")
    if (
        admission_call < 0
        or player_write < 0
        or admission_call > player_write
    ):
        errors.append("player pose mutates before finite/time admission")
    if (
        emit_return < 0
        or wake_body_write < 0
        or emit_return > wake_body_write
    ):
        errors.append("mounted pose still rewrites source-1 wake history")
    if "_wake_clock_data[2] = xz.x" in player_setter \
            or "_wake_clock_data[3] = xz.y" in player_setter:
        errors.append("server history writer restored the retired live endpoint")
    if "func set_player_wake_presentation_xz(" not in host_text:
        errors.append("Product wake presentation endpoint writer missing")
    else:
        presentation_setter = gd_function_body(
            host_text, "func set_presentation_xz("
        )
        for token in (
            "not xz.is_finite()",
            "clock_data[2] = xz.x",
            "clock_data[3] = xz.y",
            "clock_dirty = true",
        ):
            if token not in presentation_setter:
                errors.append(f"presentation endpoint writer missing: {token}")
    return errors


def destructive_mutants(product_text: str) -> dict[str, str]:
    """Product-side mutations reused by the activation mutation harness."""

    predecessor_anchor = (
        "\t\tvec4 prev = vec4(0.0, 0.0, -999.0, -1.0);\n"
    )
    return {
        "private_topology_owner_restored": product_text.replace(
            predecessor_anchor,
            "\t\tuint product_wake_topology_rows = 0u;\n"
            + predecessor_anchor,
            1,
        ),
        "nested_scan_restored": product_text.replace(
            predecessor_anchor,
            predecessor_anchor
            + "\t\tfor (int j = 0; j < WAKE_TRAIL_CAPACITY; j++) {}\n",
            1,
        ),
        "self_pointer_admitted": product_text.replace(
            "predecessor_row_plus_one != uint(index) + 1u",
            "predecessor_row_plus_one != 0xffffffffu",
            1,
        ),
        "fractional_predecessor_rounded": product_text.replace(
            "meta.z, 0u, uint(WAKE_TRAIL_CAPACITY)",
            "round(meta.z), 0u, uint(WAKE_TRAIL_CAPACITY)",
            1,
        ),
        "latest_fraction_rounded": product_text.replace(
            "meta.w, 0u, 1u",
            "round(meta.w), 0u, 1u",
            1,
        ),
        "cross_source_join": product_text.replace(
            "&& predecessor_source_id == point_source_id",
            "&& predecessor_source_id != 0u",
            1,
        ),
        "sequence_gap_join": product_text.replace(
            "&& predecessor_sequence + 1u == point_sequence",
            "&& predecessor_sequence < point_sequence",
            1,
        ),
        "clock_regression_admitted": product_text.replace(
            "|| point.w < 0.0 || wake_clock.value.x < point.w",
            "|| point.w < 0.0",
            1,
        ),
        "visual_age_widened": product_text.replace(
            "const float PRODUCT_WAKE_VISUAL_AGE_SECONDS = 1.8;",
            "const float PRODUCT_WAKE_VISUAL_AGE_SECONDS = 3.5;",
            1,
        ),
        "live_endpoint_detached": product_text.replace(
            "wake_clock.value.zw",
            "point.xy",
            1,
        ),
    }


def host_destructive_mutants(host_text: str) -> dict[str, str]:
    """Host mutations cover insertion laws that Product cannot reconstruct."""

    latest_scan = gd_function_body(
        host_text, "func latest_server_time_for_source("
    )
    rounded_latest_scan = latest_scan.replace(
        "exact_int(\n"
        "\t\t\ttrail_data[sample_base + 4],\n"
        "\t\t\t1,\n"
        "\t\t\tWAKE_TRAIL_MAX_EXACT_SEQUENCE\n"
        "\t\t)",
        "int(round(trail_data[sample_base + 4]))",
        1,
    )
    return {
        "stale_source_time_after_mutation": host_text.replace(
            "server_time_s < prior_source_time",
            "server_time_s > prior_source_time",
            1,
        ),
        "global_clock_regression_admitted": host_text.replace(
            "server_time_s < current_server_time_s",
            "server_time_s > current_server_time_s",
            1,
        ),
        "sequence_overflow_not_retired": host_text.replace(
            "current_sequence >= WAKE_TRAIL_MAX_EXACT_SEQUENCE",
            "current_sequence > WAKE_TRAIL_MAX_EXACT_SEQUENCE",
            1,
        ),
        "old_latest_not_cleared": host_text.replace(
            "prior_tail_slot * WAKE_TRAIL_STRIDE + 7",
            "prior_tail_slot * WAKE_TRAIL_STRIDE + 6",
            1,
        ),
        "new_latest_not_marked": host_text.replace(
            "trail_data[base + 7] = 1.0",
            "trail_data[base + 7] = 0.0",
            1,
        ),
        "predecessor_pointer_zeroed": host_text.replace(
            "float(prior_tail_slot + 1) if prior_tail_slot >= 0 else 0.0",
            "0.0 if prior_tail_slot >= 0 else 0.0",
            1,
        ),
        "wrap_successor_not_detached": host_text.replace(
            "trail_data[successor_base + 6] = 0.0",
            "trail_data[successor_base + 7] = 0.0",
            1,
        ),
        "wrap_sequence_gap_admitted": host_text.replace(
            "overwritten_sequence + 1",
            "overwritten_sequence + 2",
            1,
        ),
        "fractional_source_rounded": host_text.replace(
            latest_scan,
            rounded_latest_scan,
            1,
        ),
        "overflow_reuse_unreachable": host_text.replace(
            "\t\tcursor = 0\n\t\tcurrent_sequence = 0",
            "\t\treturn\n\t\tcursor = 0\n\t\tcurrent_sequence = 0",
            1,
        ),
        "player_stale_admission_after_write": host_text.replace(
            "\tif not _player_server_pose_admitted(\n",
            "\t_player_world_xz = xz\n"
            "\tif not _player_server_pose_admitted(\n",
            1,
        ),
        "player_nan_admission_removed": host_text.replace(
            "\t\tnot xz.is_finite()\n\t\tor server_tick < 0",
            "\t\tserver_tick < 0",
            1,
        ),
        "mounted_endpoint_before_emit_gate": host_text.replace(
            "\tif not emit_player_wake:\n\t\treturn true\n"
            "\t_wake_trail_last_body_xz = xz",
            "\t_wake_trail_last_body_xz = xz\n"
            "\tif not emit_player_wake:\n\t\treturn true",
            1,
        ),
        "unclocked_player_writer_restored": host_text.replace(
            "func set_springarm_slope_occlusion_metrics(",
            "func set_player_world_xz(xz: Vector2) -> void:\n"
            "\t_player_world_xz = xz\n\n"
            "func set_springarm_slope_occlusion_metrics(",
            1,
        ),
        "unused_world_pose_writer_restored": host_text.replace(
            "func set_player_world_xz_at_server_tick(",
            "func set_player_world_at_server_tick(\n"
            "\tworld_pos: Vector3, server_tick: int, "
            "server_tick_rate_hz: float\n"
            ") -> void:\n"
            "\t_player_world = world_pos\n\n"
            "func set_player_world_xz_at_server_tick(",
            1,
        ),
    }


def test_host_ring_wrap_interleave_equal_time_and_parity() -> None:
    ring = HostRing()
    # Equal times are valid; four sources occupy independent fixed lanes.
    for source in range(1, 5):
        assert ring.push((float(source), 0.0), 10.0, source)
        assert ring.push((float(source) + 1.0, 0.0), 10.0, source)
    # Source one wraps twice. The new oldest loses its overwritten predecessor,
    # and only the newest row keeps the endpoint marker.
    for step in range(2, 10):
        assert ring.push((1.0 + float(step), 0.0), 10.0 + step * 0.1, 1)
    lane = ring.lane_by_source[1]
    live_rows = ring.rows[lane * LANE_SIZE : (lane + 1) * LANE_SIZE]
    assert sum(row.latest == 1.0 for row in live_rows) == 1
    newest = max(live_rows, key=lambda row: row.sequence)
    oldest = min(live_rows, key=lambda row: row.sequence)
    assert newest.latest == 1.0
    assert oldest.predecessor_plus_one == 0.0
    for receiver in ((3.5, 0.0), (6.2, 0.3), (10.0, 0.0), (4.0, 2.0)):
        old = scanned_wake_fact(
            ring.rows, receiver, ring.clock, (11.5, 0.0)
        )
        new = published_wake_fact(
            ring.rows, receiver, ring.clock, (11.5, 0.0)
        )
        assert new == old


def test_host_rejects_stale_and_full_without_mutating_authority() -> None:
    ring = HostRing()
    assert ring.push((0.0, 0.0), 10.0, 1)
    before = ring.snapshot()
    assert not ring.push((1.0, 0.0), 9.0, 1)
    assert ring.last_reason == "wake_source_time_regressed"
    assert ring.snapshot() == before
    assert ring.push((2.0, 0.0), 11.0, 2)
    before = ring.snapshot()
    assert not ring.push((3.0, 0.0), 10.5, 3)
    assert ring.last_reason == "wake_global_time_regressed"
    assert ring.snapshot() == before
    assert ring.push((3.0, 0.0), 11.0, 3)
    assert ring.push((4.0, 0.0), 11.0, 4)
    before = ring.snapshot()
    assert not ring.push((5.0, 0.0), 11.0, 5)
    assert ring.last_reason == "wake_source_capacity_full"
    assert ring.snapshot() == before


def test_sequence_boundary_clears_lane_before_exact_reuse() -> None:
    ring = HostRing()
    assert ring.push((0.0, 0.0), 1.0, 1)
    ring.sequence[1] = MAX_EXACT_SEQUENCE
    lane = ring.lane_by_source[1]
    cursor = ring.cursor[1]
    tail = lane * LANE_SIZE + (cursor - 1 + LANE_SIZE) % LANE_SIZE
    ring.rows[tail].sequence = float(MAX_EXACT_SEQUENCE)
    assert ring.push((1.0, 0.0), 1.0, 1)
    lane_rows = ring.rows[lane * LANE_SIZE : (lane + 1) * LANE_SIZE]
    nonempty = [row for row in lane_rows if row.sequence > 0.0]
    assert len(nonempty) == 1
    assert nonempty[0].sequence == 1.0
    assert nonempty[0].predecessor_plus_one == 0.0
    assert nonempty[0].latest == 1.0


def test_player_pose_admission_precedes_every_wake_mutation() -> None:
    ring = HostRing()
    assert ring.set_player((0.0, 0.0), 100, 10.0)
    admitted = ring.snapshot()
    assert not ring.set_player((9.0, 9.0), 99, 10.0)
    assert ring.snapshot() == admitted
    assert not ring.set_player((math.nan, 4.0), 101, 10.0)
    assert ring.snapshot() == admitted

    wake_before_mounted_pose = (
        tuple(
            (
                row.x,
                row.z,
                row.server_time,
                row.source_id,
                row.sequence,
                row.predecessor_plus_one,
                row.latest,
            )
            for row in ring.rows
        ),
        dict(ring.cursor),
        dict(ring.sequence),
        dict(ring.lane_by_source),
        dict(ring.source_by_lane),
        ring.clock,
        ring.live_endpoint,
    )
    assert ring.set_player((2.0, 2.0), 101, 10.0, False)
    assert ring.player_xz == (2.0, 2.0)
    assert ring.last_player_tick == 101
    wake_after_mounted_pose = (
        tuple(
            (
                row.x,
                row.z,
                row.server_time,
                row.source_id,
                row.sequence,
                row.predecessor_plus_one,
                row.latest,
            )
            for row in ring.rows
        ),
        dict(ring.cursor),
        dict(ring.sequence),
        dict(ring.lane_by_source),
        dict(ring.source_by_lane),
        ring.clock,
        ring.live_endpoint,
    )
    assert wake_after_mounted_pose == wake_before_mounted_pose


def test_malformed_metadata_never_creates_false_topology() -> None:
    ring = HostRing()
    assert ring.push((0.0, 0.0), 10.0, 1)
    assert ring.push((1.0, 0.0), 10.0, 1)
    current = next(
        index
        for index, row in enumerate(ring.rows)
        if row.source_id == 1.0 and row.sequence == 2.0
    )
    clean = published_wake_fact(
        ring.rows, (0.5, 0.0), 10.0, (1.0, 0.0)
    )
    assert clean.strength > 0.0
    mutations = {
        "nan": math.nan,
        "fraction": 1.5,
        "self": float(current + 1),
        "out_of_range": 33.0,
    }
    for pointer in mutations.values():
        damaged = [replace(row) for row in ring.rows]
        damaged[current].predecessor_plus_one = pointer
        fact = published_wake_fact(
            damaged, (0.5, 0.0), 10.0, (1.0, 0.0)
        )
        # The row remains a historical point blob, never a false segment.
        assert fact.strength >= 0.0
    predecessor = int(ring.rows[current].predecessor_plus_one) - 1
    for damage in ("cross_source", "sequence_gap", "expired"):
        damaged = [replace(row) for row in ring.rows]
        if damage == "cross_source":
            damaged[predecessor].source_id = 2.0
        elif damage == "sequence_gap":
            damaged[predecessor].sequence = 7.0
        else:
            damaged[predecessor].server_time = 1.0
        fact = published_wake_fact(
            damaged, (0.5, 0.0), 10.0, (1.0, 0.0)
        )
        assert fact.strength >= 0.0
    damaged = [replace(row) for row in ring.rows]
    damaged[current].latest = 0.5
    clean_live_endpoint = published_wake_fact(
        ring.rows, (3.0, 0.0), 10.0, (3.0, 0.0)
    )
    far_live_endpoint = published_wake_fact(
        damaged, (3.0, 0.0), 10.0, (3.0, 0.0)
    )
    assert clean_live_endpoint.strength > 0.0
    assert far_live_endpoint.strength == 0.0


def test_source_contract_and_all_mutants() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    host = (
        WAKE_FIELD.read_text(encoding="utf-8")
        + "\n"
        + COMPOSITOR.read_text(encoding="utf-8")
    )
    errors = source_contract_errors(product, host)
    assert errors == [], errors
    product_mutants = destructive_mutants(product)
    host_mutants = host_destructive_mutants(host)
    assert len(product_mutants) == 10
    assert len(host_mutants) == 15
    for name, mutant in product_mutants.items():
        assert mutant != product, f"Product mutant did not alter source: {name}"
        assert source_contract_errors(mutant, host), (
            f"Product mutant survived: {name}"
        )
    for name, mutant in host_mutants.items():
        assert mutant != host, f"host mutant did not alter source: {name}"
        assert source_contract_errors(product, mutant), (
            f"host mutant survived: {name}"
        )


def test_original_receiver_float_order_remains_present() -> None:
    wake = function_body(
        _expand_includes(PRODUCT.read_text(encoding="utf-8")),
        "ProductWakeFact product_wake_fact(vec2 world_xz)",
        last=True,
    )
    for token in (
        "float half_width = 0.72;",
        "strength = 1.0 - smoothstep(hw, hw * 1.70, d);",
        "strength = (1.0 - smoothstep(half_width * 0.72, "
        "half_width * 1.20, dp)) * 0.45;",
        "product_distance_to_segment(world_xz, prev.xy, point.xy, seg_along)",
        "point_source_id == WAKE_SOURCE_PLAYER && is_latest_sample",
        "wake_clock.value.zw",
    ):
        assert token in wake


if __name__ == "__main__":
    test_host_ring_wrap_interleave_equal_time_and_parity()
    test_host_rejects_stale_and_full_without_mutating_authority()
    test_sequence_boundary_clears_lane_before_exact_reuse()
    test_player_pose_admission_precedes_every_wake_mutation()
    test_malformed_metadata_never_creates_false_topology()
    test_source_contract_and_all_mutants()
    test_original_receiver_float_order_remains_present()
    print(
        "FL4216_WAKE_ENDPOINT_ANCHOR_PASS "
        "owner=host_predecessor_latest receivers=4 "
        "product_mutants=10 host_mutants=15"
    )
