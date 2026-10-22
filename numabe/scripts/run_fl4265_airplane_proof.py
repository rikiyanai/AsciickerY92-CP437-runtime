#!/usr/bin/env python3
"""FL-4265 P4 one-tab airplane dogfight proof orchestrator.

Boots the real Colyseus server, launches ONE headed Godot client on the real
--colyseus-client path against it, and collects the proof receipt. One tab is
sufficient because the opponent is server-owned bot AI (a second airplane flown
by an NPC the server drives), not a second human client.

Sequence:
  1. Start colyseus_server (node --import tsx src/index.ts) on COLYSEUS_PORT.
  2. Wait for ASCIICKER_COLYSEUS_SPIKE_LISTENING.
  3. Launch headed Godot with run_fl4265_airplane_dogfight_proof.gd, which connects,
     runs the selected visual, clean-flight, or material-211 damage route and
     writes receipt.json.
  4. Read the receipt, tear down the server, and report its admission result.

If no display is available the Godot client cannot create a rendering device: the
frame capture is reported as unavailable (capture_reason), but the data + render
facts (two airplanes, non-zero render faces, bot airborne) are still asserted. A
missing display is reported exactly as that — it is NOT an architecture blocker.
"""
from __future__ import annotations

import argparse
# copy.copy is used to give each recording arm its own private set of options,
# so adjusting one arm's camera type cannot leak into the other arm.
import copy
import hashlib
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLYSEUS_DIR = ROOT / "colyseus_server"
GODOT_PROJECT = ROOT / "godot_project"
HARNESS = "res://tools/proofs/run_fl4265_airplane_dogfight_proof.gd"
GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
GODOT_FULLSCREEN_ARGS = ["--fullscreen"]
GODOT_NATIVE_FULLSCREEN_ARG = "--fl4359-force-fullscreen"
# FL-4662 / FL-4431: a performance arm measures renderer headroom, so display
# pacing cannot remain an unrecorded frame-time owner.
GODOT_UNCAPPED_ARGS = ["--disable-vsync", "--max-fps", "0"]
FL4359_AIRPLANE_PROOF_SPAWN_X = "-180.0"
FL4359_AIRPLANE_PROOF_SPAWN_Z = "230.0"
FL4359_AIRPLANE_PROOF_ALTITUDE = "80"
FL4359_AIRPLANE_PROOF_THROTTLE = "0.37"
# The VSB cutover deleted the in-game JSONL producer. Keeping one stable reason
# lets old proof packages fail before launch while they migrate to the external
# recorder that now owns temporal render evidence.
EXTERNAL_RECORDER_JITTER_REASON = "external_recorder_owns_fl4359_jitter_capture"
FL4431_SECONDARY_EFFECT_EVIDENCE_ARG = "--fl4431-secondary-effect-evidence"
FL4431_DAMAGE_CURRENT_PROFILE = "current_retained_vsb_product_b62_mat211_v1"
FL4431_DAMAGE_CURRENT_ABSENCE_REASON = (
    "current_retained_product_deleted_the_flat_dynamic_render_owner"
)
FL4431_DAMAGE_CURRENT_STYLE = "vsb_product_apply_vapor_style.material_211"
FL4431_DAMAGE_CURRENT_CONTRIBUTION_BIT = 67108864
FL4431_DAMAGE_ROUTE_IDENTITY = "server_tick_constant_physical_flight_input"
AIRPLANE_DAMAGE_EVENT_PREFIX = "AIRPLANE_DAMAGE_EVENT "
# FL-4265 / FL-4832 route (B): the server's own narrow steering witness. One
# line per airframe per emitted tick, written by colyseus_server/src/sim/
# flight_steering_witness.ts. The runner joins it on
# (tick, pickupId, pilotSessionId, pilotActorId) so a receipt can quote what the
# SERVER did with an intent instead of only what the client put on the wire.
AIRPLANE_STEERING_WITNESS_PREFIX = "AIRPLANE_STEERING_WITNESS "
FL4265_STEERING_WITNESS_SCHEMA = "fl4265.server_steering_witness.v1"
# The shipping client's steering axis. player.gd:_process_colyseus_flight_intent_only
# converts move_right into YAW and hard-codes roll 0.0; FL-4832 makes the server
# DROP any non-zero network roll. So a shipping-client route steers on yaw and
# on nothing else.
FL4265_SHIPPING_STEERING_AXIS = "yaw"
# Below this the yaw axis is indistinguishable from an unpressed key.
FL4265_MIN_STEERING_YAW = 0.01
# Above this an axis that must be a literal zero is an invention.
FL4265_MAX_CLIENT_ROLL = 1e-6
# The client sends a float32-rounded axis; the server echoes the admitted double.
# This tolerance separates "the same number" from "a different command".
FL4265_CLIENT_SERVER_AXIS_TOLERANCE = 1e-6
BOT_PILOT_ACTOR_ID = "npc_airplane_bot_1"
PRIMARY_AIRPLANE_PICKUP_ID = "pickup_mount_airplane"
CAPTURE_CHILD_DENIED_ENV_EXACT = frozenset(
    {
        "ASCIICKER_CAPTURE_VIEWPORT_EXIT",
        "PERF_FRAME_TRACE",
        "PERF_GPU_TRACE",
        "PERF_READBACK_TRACE",
        "PERF_TRACE",
    }
)
CAPTURE_CHILD_DENIED_ENV_PREFIXES = ("FL4431_", "FL4265_PROOF_")
CAPTURE_CHILD_CANONICAL_SPAWN_ENV = {
    "AIRPLANE_SPAWN_AIRBORNE": "1",
    "AIRPLANE_SPAWN_ALTITUDE": FL4359_AIRPLANE_PROOF_ALTITUDE,
    "AIRPLANE_SPAWN_THROTTLE": FL4359_AIRPLANE_PROOF_THROTTLE,
    "AIRPLANE_SPAWN_X": FL4359_AIRPLANE_PROOF_SPAWN_X,
    "AIRPLANE_SPAWN_Z": FL4359_AIRPLANE_PROOF_SPAWN_Z,
}
CAPTURE_CHILD_NPC_CRASH_ENV = {
    "AIRPLANE_SPAWN_AIRBORNE": "1",
    "AIRPLANE_SPAWN_ALTITUDE": "45",
    # The crash route needs enough initial cruise margin to remain airborne
    # while the client connects and mounts. The canonical 0.37 capture value
    # sits only 0.002 u/tick above stall and can decay into a healthy landing.
    "AIRPLANE_SPAWN_THROTTLE": "0.85",
    "AIRPLANE_SPAWN_X": "86.0",
    "AIRPLANE_SPAWN_Z": "93.0",
}
CAPTURE_CHILD_NAUSICAA_SPAWN_ENV = {
    "NAUSICAA_SPAWN_IN_PLANE": "1",
}
CAPTURE_CHILD_FL4808_MOUNT_ENV = {
    "AIRPLANE_SPAWN_AIRBORNE": "0",
    "AIRPLANE_SPAWN_X": "86.0",
    "AIRPLANE_SPAWN_Z": "93.0",
}
CAPTURE_CHILD_ROUTE_ENV_KEYS = frozenset(
    set(CAPTURE_CHILD_CANONICAL_SPAWN_ENV)
    | set(CAPTURE_CHILD_NAUSICAA_SPAWN_ENV)
    | set(CAPTURE_CHILD_FL4808_MOUNT_ENV)
)


def _capture_child_environment_policy_sha256() -> str:
    """Return one stable identity for the child-environment deny policy.

    The hash covers the policy itself, not the accidental keys found in one
    shell. Every current capture can therefore prove which deletion boundary
    governed its children even when the parent shell was polluted.
    """

    policy_payload = json.dumps(
        {
            "exact_keys": sorted(CAPTURE_CHILD_DENIED_ENV_EXACT),
            "prefixes": sorted(CAPTURE_CHILD_DENIED_ENV_PREFIXES),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(policy_payload.encode("utf-8")).hexdigest()


def _capture_child_environment(
    parent_environment: dict[str, str],
    canonical_route_environment: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, object]]:
    """Delete inherited renderer knobs before any proof child starts.

    Performance ablations, phase counters, trace switches, and readbacks are
    diagnostics. Letting a shell export leak into only one adjacent arm can
    create a visually plausible but incomparable package. This boundary
    removes the complete FL-4431 runtime namespace plus the older trace keys,
    then hard-assigns the canonical airborne spawn instead of preserving a
    caller's stale values.
    """

    route_environment = (
        CAPTURE_CHILD_CANONICAL_SPAWN_ENV
        if canonical_route_environment is None
        else canonical_route_environment
    )
    denied_keys = sorted(
        key
        for key in parent_environment
        if key in CAPTURE_CHILD_DENIED_ENV_EXACT
        or key in CAPTURE_CHILD_ROUTE_ENV_KEYS
        or any(
            key.startswith(prefix)
            for prefix in CAPTURE_CHILD_DENIED_ENV_PREFIXES
        )
    )
    child = {
        key: value
        for key, value in parent_environment.items()
        if key not in denied_keys
    }
    if route_environment == CAPTURE_CHILD_CANONICAL_SPAWN_ENV:
        child.update(CAPTURE_CHILD_CANONICAL_SPAWN_ENV)
    else:
        child.update(route_environment)
    effective_forbidden = sorted(
        key
        for key in child
        if key in CAPTURE_CHILD_DENIED_ENV_EXACT
        or any(
            key.startswith(prefix)
            for prefix in CAPTURE_CHILD_DENIED_ENV_PREFIXES
        )
    )
    effective_canonical_route_environment = {
        key: child.get(key, "")
        for key in sorted(route_environment)
    }
    canonical_route_environment_match = (
        effective_canonical_route_environment
        == route_environment
    )
    denied_payload = json.dumps(denied_keys, separators=(",", ":"))
    spawn_payload = json.dumps(
        effective_canonical_route_environment,
        sort_keys=True,
        separators=(",", ":"),
    )
    contract: dict[str, object] = {
        "schema": "fl4431.capture_child_environment.v1",
        "denied_environment_policy": {
            "exact_keys": sorted(CAPTURE_CHILD_DENIED_ENV_EXACT),
            "prefixes": sorted(CAPTURE_CHILD_DENIED_ENV_PREFIXES),
        },
        "denied_environment_policy_sha256":
            _capture_child_environment_policy_sha256(),
        "denied_parent_keys": denied_keys,
        "denied_parent_keys_sha256": hashlib.sha256(
            denied_payload.encode("utf-8")
        ).hexdigest(),
        "effective_forbidden_keys": effective_forbidden,
        "effective_forbidden_keys_absent": not effective_forbidden,
        "effective_canonical_route_environment":
            effective_canonical_route_environment,
        "effective_canonical_route_environment_sha256": hashlib.sha256(
            spawn_payload.encode("utf-8")
        ).hexdigest(),
        "canonical_route_environment_match": canonical_route_environment_match,
        "ok": not effective_forbidden and canonical_route_environment_match,
    }
    return child, contract


def _read_airplane_damage_events(server_log: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    if not server_log.exists():
        return events
    for line in server_log.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = line.find(AIRPLANE_DAMAGE_EVENT_PREFIX)
        if marker < 0:
            continue
        payload = line[marker + len(AIRPLANE_DAMAGE_EVENT_PREFIX):]
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _read_flight_steering_witnesses(server_log: Path) -> list[dict[str, object]]:
    """Every FL-4265 steering-witness line the server emitted, in order.

    Malformed lines are skipped rather than raising: the log also carries
    unrelated server output, and a broken witness must surface as a MISSING
    join (which fails the receipt) instead of as a crashed runner.
    """
    rows: list[dict[str, object]] = []
    if not server_log.exists():
        return rows
    for line in server_log.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = line.find(AIRPLANE_STEERING_WITNESS_PREFIX)
        if marker < 0:
            continue
        payload = line[marker + len(AIRPLANE_STEERING_WITNESS_PREFIX):]
        try:
            row = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("schema") == FL4265_STEERING_WITNESS_SCHEMA:
            rows.append(row)
    return rows


def _fl4265_steering_witness_join(
    receipt: dict, witnesses: list[dict[str, object]]
) -> dict:
    """Bind the client's measured wire intent to the SERVER's own steering fact.

    The chain this join closes, left to right:

        physical key -> client emitted intent -> session -> actor -> mount
          -> server admitted intent -> pilot kind -> server steering command
          -> airframe result

    The first five links come from the runtime observation the Godot harness
    measured (run_fl4265_airplane_dogfight_proof.gd). The next three come from
    the server witness line. The last is the measured airplane yaw delta.

    A missing witness is reported as MISSING, never as agreement: the server has
    not spoken, so the receipt stays a client-side observation. Nothing in here
    is populated from an expected value -- every field is copied from the run.
    """
    observation = receipt.get("flight_command_observation")
    observation = observation if isinstance(observation, dict) else {}
    identity = observation.get("control_identity")
    identity = identity if isinstance(identity, dict) else {}
    input_event = observation.get("input_event")
    input_event = input_event if isinstance(input_event, dict) else {}

    session_id = str(identity.get("session_id", ""))
    actor_key = str(identity.get("actor_key", ""))
    pickup_id = str(identity.get("local_airplane_pickup_id", ""))
    route_tick = receipt.get("flight_command_observation_route_tick")
    route_tick = int(route_tick) if isinstance(route_tick, (int, float)) else None

    join: dict[str, object] = {
        "schema": "fl4265.steering_witness_join.v1",
        # -------- links 1..2: the physical input and what it put on the wire
        "input_action": str(input_event.get("action", "")),
        "input_physical_keycode": input_event.get("physical_keycode"),
        "input_device": input_event.get("device"),
        "client_yaw_command": receipt.get("first_active_yaw_command"),
        "client_pitch_command": receipt.get("first_active_pitch_command"),
        "client_roll_command": receipt.get("first_active_roll_command"),
        "client_throttle_command": receipt.get("first_active_throttle_command"),
        "client_observed_steering_axis": str(
            receipt.get("observed_steering_axis", "none")
        ),
        # -------- links 3..5: whose intent it was and what it was flying
        "session_id": session_id,
        "actor_key": actor_key,
        "pickup_id": pickup_id,
        "controls_airplane": bool(identity.get("controls_airplane", False)),
        "route_tick": route_tick,
        # -------- links 6..8: filled in from the server witness, if any
        "witness_line_count": len(witnesses),
        "server_steering_witness_available": False,
        "server_steering_witness_absence_reason": "",
        "matched_witness": None,
        "server_pilot_kind": None,
        "server_steering_yaw_command": None,
        "server_steering_pitch_command": None,
        "server_steering_roll_command": None,
        "server_network_roll_drops": None,
        "server_network_pilot_attributed": None,
        "client_server_yaw_agrees": None,
        "client_server_yaw_difference": None,
        "client_server_roll_agrees": None,
        # -------- link 9: what the airframe then did
        "airframe_yaw_delta": receipt.get("airplane_yaw_turn_delta"),
        "airframe_yaw_turn_measured": receipt.get("airplane_yaw_turn_measured"),
    }

    if not witnesses:
        join["server_steering_witness_absence_reason"] = (
            "server_emitted_no_flight_steering_witness_line"
        )
        return join
    if not session_id or not actor_key:
        join["server_steering_witness_absence_reason"] = (
            "receipt_carried_no_session_or_actor_to_join_on"
        )
        return join

    # An identity match is required on ALL of session, actor and airframe. Two
    # of three is a different pilot or a different plane, which is exactly the
    # confusion this join exists to make impossible.
    owned = [
        row for row in witnesses
        if str(row.get("pilotSessionId", "")) == session_id
        and str(row.get("pilotActorId", "")) == actor_key
        and (not pickup_id or str(row.get("pickupId", "")) == pickup_id)
    ]
    if not owned:
        join["server_steering_witness_absence_reason"] = (
            "no_witness_line_matched_this_session_actor_and_airframe"
        )
        return join

    matched = None
    if route_tick is not None:
        exact = [row for row in owned if row.get("tick") == route_tick]
        if exact:
            matched = exact[0]
    if matched is None:
        join["server_steering_witness_absence_reason"] = (
            "no_witness_line_carried_the_observed_route_tick"
            if route_tick is not None
            else "receipt_carried_no_route_tick_to_join_on"
        )
        return join

    join["server_steering_witness_available"] = True
    join["matched_witness"] = matched
    join["server_pilot_kind"] = matched.get("steeringPilotKind")
    join["server_steering_yaw_command"] = matched.get("steeringYawCommand")
    join["server_steering_pitch_command"] = matched.get("steeringPitchCommand")
    join["server_steering_roll_command"] = matched.get("steeringRollCommand")
    join["server_network_roll_drops"] = matched.get("networkRollDrops")
    join["server_network_pilot_attributed"] = matched.get("networkPilotAttributed")

    client_yaw = join["client_yaw_command"]
    server_yaw = join["server_steering_yaw_command"]
    if isinstance(client_yaw, (int, float)) and isinstance(server_yaw, (int, float)):
        difference = abs(float(client_yaw) - float(server_yaw))
        join["client_server_yaw_difference"] = difference
        join["client_server_yaw_agrees"] = (
            difference <= FL4265_CLIENT_SERVER_AXIS_TOLERANCE
        )
    server_roll = join["server_steering_roll_command"]
    client_roll = join["client_roll_command"]
    if isinstance(server_roll, (int, float)) and isinstance(client_roll, (int, float)):
        join["client_server_roll_agrees"] = (
            abs(float(server_roll) - float(client_roll))
            <= FL4265_CLIENT_SERVER_AXIS_TOLERANCE
        )
    return join


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for(predicate, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _source_identity(
    source_path: Path,
    *,
    scope_path: Path | None = None,
    fingerprint_scope: Path | None = None,
) -> dict[str, object]:
    """Read a full commit plus the dirty paths belonging to one source owner."""
    root_result = subprocess.run(
        ["git", "-C", str(source_path), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    source_root_text = root_result.stdout.strip()
    source_root = Path(source_root_text).resolve() if root_result.returncode == 0 else source_path.resolve()
    head_result = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    status_command = ["git", "-C", str(source_root), "status", "--porcelain"]
    source_scope = "."
    if scope_path is not None:
        try:
            source_scope = str(scope_path.resolve().relative_to(source_root))
        except ValueError:
            source_scope = ""
        if source_scope:
            status_command.extend(["--", source_scope])
    status_result = subprocess.run(
        status_command,
        text=True,
        capture_output=True,
        check=False,
    )
    head = head_result.stdout.strip()
    full_head = (
        head_result.returncode == 0
        and len(head) == 40
        and all(character in "0123456789abcdef" for character in head)
    )
    dirty_paths = [
        line
        for line in status_result.stdout.splitlines()
        if status_result.returncode == 0 and line.strip()
    ]
    return {
        "source_root": str(source_root),
        "source_scope": source_scope,
        "git_head": head if full_head else "",
        "git_dirty_paths": dirty_paths,
        "runtime_scope_fingerprint": _runtime_scope_fingerprint(
            fingerprint_scope or scope_path or source_path
        ),
        "valid": (
            root_result.returncode == 0
            and full_head
            and status_result.returncode == 0
            and (scope_path is None or bool(source_scope))
        ),
    }


def _runtime_scope_fingerprint(scope_path: Path) -> str:
    root_result = subprocess.run(
        ["git", "-C", str(scope_path), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if root_result.returncode != 0:
        return ""
    source_root = Path(root_result.stdout.strip()).resolve()
    try:
        scope = scope_path.resolve().relative_to(source_root)
    except ValueError:
        return ""
    diff_result = subprocess.run(
        ["git", "-C", str(source_root), "diff", "--binary", "HEAD", "--", str(scope)],
        capture_output=True,
        check=False,
    )
    untracked_result = subprocess.run(
        [
            "git", "-C", str(source_root), "ls-files", "--others",
            "--exclude-standard", "--", str(scope),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if diff_result.returncode != 0 or untracked_result.returncode != 0:
        return ""
    digest = hashlib.sha256()
    digest.update(diff_result.stdout)
    for relative in sorted(
        line for line in untracked_result.stdout.splitlines() if line
    ):
        if any(part in {".godot", ".run", "dist"} for part in Path(relative).parts):
            continue
        path = source_root / relative
        if path.is_file():
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=2567)
    ap.add_argument(
        "--fl4265-require-server-steering-witness",
        action="store_true",
        help=(
            "FL-4265/FL-4832: fail the run unless the server emitted a flight "
            "steering witness line that joins this receipt's tick, session, "
            "actor and airframe. The emitter IS integrated as of FL-4265 P6 "
            "(colyseus_server/src/sim/world.ts, advanceAirplane), so a route "
            "flown by a network pilot should now carry the binding. It stays "
            "off by default because a route with no network-pilot tick (an "
            "npc-only or unpiloted route) legitimately emits nothing, and "
            "absence is not disagreement. When a witness IS present the full "
            "binding is enforced either way: turning the emitter on can never "
            "buy a weaker gate."
        ),
    )
    ap.add_argument("--out", default="/tmp/fl4265_airplane_proof")
    ap.add_argument("--godot-project", default=str(GODOT_PROJECT),
                    help="Godot project directory to launch; defaults to the current checkout.")
    ap.add_argument(
        "--colyseus-dir",
        required=True,
        help=(
            "Explicit Colyseus source directory that owns the "
            "server-authoritative route."
        ),
    )
    ap.add_argument("--godot-script", default=HARNESS,
                    help="Godot script path to run; defaults to the current moving-capture harness.")
    ap.add_argument("--godot-timeout", type=float, default=120.0)
    ap.add_argument("--server-boot-timeout", type=float, default=30.0)
    ap.add_argument("--fl4359-jitter-capture", action="store_true",
                    help="Also collect PixelCamera jitter rows during the headed airplane run")
    ap.add_argument("--fl4359-jitter-capture-path", default="",
                    help="JSONL path for FL-4359 rows; defaults to <out>/jitter.jsonl")
    ap.add_argument("--fl4359-jitter-hold-frames", type=int, default=48,
                    help="Extra continuous flight frames to keep after the combat frame when jitter capture is enabled")
    ap.add_argument("--fl4359-jitter-capture-frame-start", type=int, default=0,
                    help="FL-4359: first measured render frame eligible for JSONL row capture.")
    ap.add_argument("--fl4359-jitter-capture-frame-limit", type=int, default=12,
                    help="FL-4359: cap JSONL row capture frames; 0 means no cap.")
    ap.add_argument("--fl4359-jitter-capture-world-sample-stride", type=int, default=8,
                    help="FL-4359: deterministic world-anchor sampling stride for JSONL rows.")
    ap.add_argument("--fl4359-clean-play-frames", type=int, default=0,
                    help="FL-4359: run a moving perspective flight window with no jitter capture/readback rows.")
    ap.add_argument("--fl4359-clean-play-no-capture", action="store_true",
                    help="FL-4359: skip final PNG capture/readback for clean-play FPS measurement.")
    ap.add_argument(
        "--fl4431-surface-falsifier-profile",
        choices=("bypass_observe", "canonical_3600x2252"),
        default="",
        help=(
            "FL-4431: run the uncapped route with per-frame PixelCamera size "
            "receipts, then capture one PNG after timing ends."
        ),
    )
    ap.add_argument(
        "--fl4667-sustained-fire-performance",
        action="store_true",
        help=(
            "FL-4667: hold the real attack_primary action throughout the "
            "uncapped clean-play route and require live server projectiles."
        ),
    )
    ap.add_argument(
        "--fl4265-damage-route",
        action="store_true",
        help=(
            "Run the separate server-owned material-211 damage route. This arm "
            "uses physical flight/fire actions and is never eligible for FPS."
        ),
    )
    ap.add_argument(
        "--fl4808-mount-regression",
        action="store_true",
        help=(
            "Join unmounted beside the ordinary airplane, board through the "
            "normal pickup intent, and reject any mount-time damage smoke or explosion."
        ),
    )
    ap.add_argument(
        "--fl4265-crash-sequence-route",
        action="store_true",
        help=(
            "Run the authentic server NPC dogfight crash sequence. Bot-owned "
            "projectiles drain the full hull through normal collision while "
            "the client records smoke, destruction, explosion, and crash frames."
        ),
    )
    ap.add_argument(
        "--fl4265-crash-perspective",
        action="store_true",
        help="Render the authentic NPC crash sequence with the perspective camera arm.",
    )
    ap.add_argument(
        "--fl4265-crash-performance",
        action="store_true",
        help=(
            "Measure the authentic NPC crash sequence uncapped with all frame "
            "capture and render-bank readbacks disabled."
        ),
    )
    ap.add_argument(
        "--fl4265-crash-recording-route",
        action="store_true",
        help=(
            "Operator recording route. Flies the same server-owned crash "
            "choreography TWICE -- once with the orthographic camera, once with "
            "the perspective camera -- saving no PNG stills at all and instead "
            "screen-recording each run to a video on the Desktop. Produces "
            "exactly two .mov files for the operator to watch."
        ),
    )
    ap.add_argument(
        "--fl4431-secondary-effect-diagnostic-only",
        action="store_true",
        help=(
            "Run a separate non-visual b62 Product-contribution diagnostic. "
            "Server-tick jumps are admitted because synchronous readback is "
            "the measured contamination, never matched-route evidence."
        ),
    )
    ap.add_argument("--fl4359-visual-capture-stride", type=int, default=0,
                    help="FL-4359: capture one moving PNG every N moving frames.")
    ap.add_argument("--fl4359-visual-capture-limit", type=int, default=0,
                    help="FL-4359: cap moving PNG captures; 0 means no cap.")
    ap.add_argument("--fl4359-visual-capture-start-frame", type=int, default=0,
                    help="FL-4359: first moving frame eligible for PNG capture.")
    ap.add_argument("--jitter-max-snap-px-delta", type=float, default=1.0)
    ap.add_argument("--jitter-max-sign-flip-rate", type=float, default=0.05)
    ap.add_argument("--jitter-max-content-pct", type=float, default=5.0)
    ap.add_argument("--extra-user-arg", action="append", default=[],
                    help="Additional Godot user argument appended after --.")
    args = ap.parse_args()
    # ------------------------------------------------------------------
    # Choose between "run once" and "run twice and record".
    # ------------------------------------------------------------------
    # Every route this script has ever had does ONE thing: boot the game
    # server, launch one game client, collect one receipt. That single-run
    # work now lives in _run_single_arm below, moved there unchanged.
    #
    # The operator recording route is the only route that needs TWO runs -- one
    # per camera type -- because the airplane cannot be brought back to life
    # after it crashes, so a second crash needs a genuinely fresh game. It gets
    # its own coordinator, which calls _run_single_arm twice and then checks and
    # renames the two videos.
    if args.fl4265_crash_recording_route:
        return _run_crash_recording_arms(args)
    return _run_single_arm(args)


def _run_single_arm(args: argparse.Namespace) -> int:
    """Boot the server, run ONE headed Godot client, and admit its receipt.

    This is the original body of main(), moved verbatim so that every existing
    route behaves exactly as it did before the recording route was added. The
    only thing that changed is that the command-line options now arrive as the
    `args` parameter instead of being parsed a few lines above.

    args: the parsed command line, possibly adjusted per arm by the recording
        coordinator (which sets the camera type and the output directory for
        each of its two runs).

    Returns the process exit code for this one arm: 0 when the receipt was
    admitted, non-zero otherwise.
    """

    if args.fl4667_sustained_fire_performance and (
        not args.fl4359_clean_play_no_capture
        or args.fl4359_clean_play_frames <= 0
    ):
        print(
            "[fl4265-proof] ERROR: sustained firing performance requires the "
            "bounded clean-play no-capture route",
            flush=True,
        )
        return 2
    if args.fl4431_surface_falsifier_profile and (
        not args.fl4359_clean_play_no_capture
        or args.fl4359_clean_play_frames <= 0
    ):
        print(
            "[fl4265-proof] ERROR: surface falsifier requires the bounded "
            "clean-play no-capture timing route",
            flush=True,
        )
        return 2
    if args.fl4359_jitter_capture:
        # Do not silently ignore the legacy request. A missing JSONL file could
        # otherwise be mistaken for a clean render instead of a retired input.
        print(
            "[fl4265-proof] ERROR: "
            f"{EXTERNAL_RECORDER_JITTER_REASON}; "
            "the external recorder must supply FL-4359 temporal evidence",
            flush=True,
        )
        return 2

    godot_project = Path(args.godot_project)
    if not godot_project.is_absolute():
        godot_project = (ROOT / godot_project).resolve()
    else:
        godot_project = godot_project.resolve()
    if not (godot_project / "project.godot").exists():
        print(f"[fl4265-proof] ERROR: no project.godot under {godot_project}", flush=True)
        return 2
    colyseus_dir = Path(args.colyseus_dir)
    if not colyseus_dir.is_absolute():
        colyseus_dir = (ROOT / colyseus_dir).resolve()
    if not (colyseus_dir / "src" / "index.ts").exists():
        print(f"[fl4265-proof] ERROR: no Colyseus entry point under {colyseus_dir}", flush=True)
        return 2
    godot_script = args.godot_script
    if not (godot_script.startswith("res://") or godot_script.startswith("user://")):
        script_path = Path(godot_script)
        if not script_path.is_absolute():
            script_path = (ROOT / script_path).resolve()
        if not script_path.exists():
            print(f"[fl4265-proof] ERROR: no Godot script at {script_path}", flush=True)
            return 2
        godot_script = str(script_path)

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        godot_out_dir = str(out_dir.relative_to(godot_project))
    except ValueError:
        godot_out_dir = str(out_dir)
    receipt_path = out_dir / "receipt.json"
    if receipt_path.exists():
        receipt_path.unlink()
    jitter_path = Path(args.fl4359_jitter_capture_path) if args.fl4359_jitter_capture_path else out_dir / "jitter.jsonl"
    if not jitter_path.is_absolute():
        jitter_path = (ROOT / jitter_path).resolve()
    try:
        godot_jitter_path = f"res://{jitter_path.relative_to(godot_project)}"
    except ValueError:
        godot_jitter_path = str(jitter_path)
    jitter_summary_path = out_dir / "jitter_summary.json"
    if args.fl4359_jitter_capture and jitter_path.exists():
        jitter_path.unlink()
    if args.fl4359_jitter_capture and jitter_summary_path.exists():
        jitter_summary_path.unlink()
    server_log = out_dir / "colyseus_server.log"
    godot_log = out_dir / "godot_client.log"
    fl4359_moving_capture = (
        args.fl4359_jitter_capture
        or args.fl4359_clean_play_frames > 0
        or args.fl4359_visual_capture_stride > 0
        or args.fl4359_visual_capture_limit > 0
        or args.fl4359_visual_capture_start_frame > 0
        or args.fl4431_secondary_effect_diagnostic_only
    )
    if args.fl4265_damage_route and args.fl4265_crash_sequence_route:
        print(
            "[fl4265-proof] ERROR: damage route and crash sequence route must "
            "run as separate processes",
            flush=True,
        )
        return 2
    if args.fl4265_crash_perspective and not args.fl4265_crash_sequence_route:
        print(
            "[fl4265-proof] ERROR: crash perspective requires the crash sequence route",
            flush=True,
        )
        return 2
    if args.fl4265_crash_performance and not args.fl4265_crash_sequence_route:
        print(
            "[fl4265-proof] ERROR: crash performance requires the crash sequence route",
            flush=True,
        )
        return 2
    if args.fl4265_crash_recording_route and not args.fl4265_crash_sequence_route:
        # The recording coordinator always switches the crash route on for each
        # arm it launches. Reaching here without it means somebody called this
        # single-arm function directly with a half-built request, which would
        # silently record the WRONG route, so refuse instead.
        print(
            "[fl4265-proof] ERROR: crash recording route requires the crash sequence route",
            flush=True,
        )
        return 2
    if args.fl4265_crash_recording_route and args.fl4265_crash_performance:
        # These two arms disagree about the display frame limiter: the measuring
        # arm switches it off to find the renderer's ceiling, while a recording
        # wants it left on so the motion matches the 60-pictures-per-second the
        # recorder samples at. Running both at once would produce a video that
        # is neither.
        print(
            "[fl4265-proof] ERROR: crash recording route and crash performance "
            "measure different things and cannot share one run",
            flush=True,
        )
        return 2
    if (args.fl4265_damage_route or args.fl4265_crash_sequence_route) and fl4359_moving_capture:
        print(
            "[fl4265-proof] ERROR: damage routes must be separate processes from "
            "matched movement and clean-play timing",
            flush=True,
        )
        return 2

    if _port_open(args.port):
        print(f"[fl4265-proof] ERROR: port {args.port} already in use; refusing to "
              f"start a second server. Stop the existing one or pass --port.", flush=True)
        return 2

    route_environment = None
    if args.fl4808_mount_regression:
        route_environment = CAPTURE_CHILD_FL4808_MOUNT_ENV
    elif args.fl4265_crash_sequence_route:
        route_environment = CAPTURE_CHILD_NPC_CRASH_ENV
    elif "--fl4265-use-nausicaa" in args.extra_user_arg:
        route_environment = CAPTURE_CHILD_NAUSICAA_SPAWN_ENV
    env, child_environment_contract = _capture_child_environment(
        dict(os.environ),
        route_environment,
    )
    if child_environment_contract["ok"] is not True:
        # Starting either child after this check fails would create evidence
        # from a route whose renderer settings or spawn facts are unknown.
        print(
            "[fl4265-proof] ERROR: canonical child environment could not be "
            "constructed",
            flush=True,
        )
        return 2
    env["COLYSEUS_PORT"] = str(args.port)
    # The mount-triggered hunter bot is PvE roster content: proof captures pin
    # their exact roster, so it stays off in every proof mode.
    env["ASCIICKER_DISABLE_HUNTER_BOT"] = "1"
    if args.fl4808_mount_regression:
        env["PLAYER_SPAWN_X"] = "86.0"
        env["PLAYER_SPAWN_Z"] = "93.0"
    # Full-screen recording is fail-closed everywhere else. Only the explicit
    # crash-recording proof route may opt into the bounded lifecycle supervisor.
    if args.fl4265_crash_recording_route:
        env["ASCIICKER_SCREEN_RECORDING_ALLOWED"] = "1"
        # FL-4873: a harness recording is never opened in front of the
        # operator by the F9 finalizer (f9_finalize_bundle harness signals).
        env["ASCIICKER_F9_FINALIZE_NO_OPEN"] = "1"
    else:
        env.pop("ASCIICKER_SCREEN_RECORDING_ALLOWED", None)
    if args.fl4808_mount_regression:
        env.pop("ASCIICKER_ENABLE_DOGFIGHT_BOT", None)
    elif args.fl4265_damage_route:
        # The route starts the real server bot and the normal premounted airborne
        # player plane. No HP, target, projectile, mount, or render truth is
        # supplied by the wrapper.
        env["ASCIICKER_ENABLE_DOGFIGHT_BOT"] = "1"
    elif args.fl4265_crash_sequence_route:
        env["ASCIICKER_ENABLE_DOGFIGHT_BOT"] = "1"
    elif fl4359_moving_capture:
        env.pop("ASCIICKER_ENABLE_DOGFIGHT_BOT", None)
    else:
        env["ASCIICKER_ENABLE_DOGFIGHT_BOT"] = "1"

    print(f"[fl4265-proof] starting colyseus server on :{args.port} ...", flush=True)
    slog = open(server_log, "w")
    server = subprocess.Popen(
        ["node", "--import", "tsx", "src/index.ts"],
        cwd=str(colyseus_dir), env=env, stdout=slog, stderr=subprocess.STDOUT,
    )

    def _listening() -> bool:
        if server.poll() is not None:
            return False
        try:
            return "ASCIICKER_COLYSEUS_SPIKE_LISTENING" in server_log.read_text()
        except OSError:
            return False

    godot_rc = None
    analyzer_rc = None
    display_note = ""
    try:
        if not _wait_for(_listening, args.server_boot_timeout):
            print("[fl4265-proof] ERROR: colyseus server did not report LISTENING.", flush=True)
            print(server_log.read_text()[-2000:], flush=True)
            return 3
        print("[fl4265-proof] server listening. launching HEADED godot client ...", flush=True)

        cmd = [
            GODOT_BIN,
            *([] if args.fl4265_crash_sequence_route else GODOT_FULLSCREEN_ARGS),
            *(
                GODOT_UNCAPPED_ARGS
                if args.fl4359_clean_play_no_capture or args.fl4265_crash_performance
                else []
            ),
            "--path", str(godot_project), "--script", godot_script, "--",
            "--colyseus-client", f"--port={args.port}",
            f"--fl4265-proof-output-dir={godot_out_dir}",
        ]
        if fl4359_moving_capture:
            cmd.extend([
                f"--fl4359-jitter-hold-frames={max(0, args.fl4359_clean_play_frames if args.fl4359_clean_play_frames > 0 else args.fl4359_jitter_hold_frames)}",
                f"--fl4359-visual-capture-stride={max(0, args.fl4359_visual_capture_stride)}",
                f"--fl4359-visual-capture-limit={max(0, args.fl4359_visual_capture_limit)}",
                f"--fl4359-visual-capture-start-frame={max(0, args.fl4359_visual_capture_start_frame)}",
            ])
            if args.fl4359_clean_play_no_capture:
                cmd.append("--fl4359-clean-play-no-capture")
            if args.fl4431_surface_falsifier_profile:
                cmd.append(
                    "--fl4431-surface-falsifier-profile="
                    + args.fl4431_surface_falsifier_profile
                )
            if args.fl4667_sustained_fire_performance:
                cmd.append("--fl4667-sustained-fire-performance")
            if args.fl4431_secondary_effect_diagnostic_only:
                cmd.append("--fl4431-secondary-effect-diagnostic-only")
        if args.fl4265_damage_route:
            cmd.append("--fl4265-damage-route")
            # The current damage route must observe the retained material-211
            # face and its Product contribution. This readback describes render
            # output; it never supplies HP, damage, flight, and smoke truth.
            cmd.append(FL4431_SECONDARY_EFFECT_EVIDENCE_ARG)
        if args.fl4808_mount_regression:
            cmd.append("--fl4808-mount-regression")
        if args.fl4265_crash_sequence_route:
            cmd.append("--fl4265-crash-sequence-route")
            cmd.append(GODOT_NATIVE_FULLSCREEN_ARG)
            if args.fl4265_crash_perspective:
                cmd.append("--fl4265-crash-perspective")
            if args.fl4265_crash_performance:
                cmd.append("--fl4265-crash-performance")
            if args.fl4265_crash_recording_route:
                # Tells the game client to save no PNG stills and to drive the
                # screen recorder around the choreography instead.
                cmd.append("--fl4265-crash-recording-route")
        for extra_arg in args.extra_user_arg:
            if extra_arg:
                cmd.append(extra_arg)
        with open(godot_log, "w") as glog:
            try:
                godot_rc = subprocess.run(
                    cmd, cwd=str(ROOT), stdout=glog, stderr=subprocess.STDOUT,
                    env=env,
                    timeout=args.godot_timeout,
                ).returncode
            except subprocess.TimeoutExpired:
                print("[fl4265-proof] ERROR: godot client timed out.", flush=True)
                godot_rc = 124
    finally:
        if server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
        slog.close()

    # Report Godot stdout tail (harness prints the receipt line + PASS/FAIL).
    godot_tail = ""
    if godot_log.exists():
        godot_tail = godot_log.read_text()
        for line in godot_tail.splitlines():
            if "FL4265_AIRPLANE_PROOF" in line or "rendering device" in line.lower() \
                    or "GODOT_COLYSEUS" in line:
                print(f"[godot] {line}", flush=True)

    if not receipt_path.exists():
        print("[fl4265-proof] FAIL: no receipt written. godot rc=%s. log tail:" % godot_rc, flush=True)
        print(godot_tail[-2000:], flush=True)
        return 1

    receipt = json.loads(receipt_path.read_text())
    airplane_damage_events = _read_airplane_damage_events(server_log)
    npc_kill_events = [
        event
        for event in airplane_damage_events
        if event.get("owner_actor_id") == BOT_PILOT_ACTOR_ID
        and event.get("target_pickup_id") == PRIMARY_AIRPLANE_PICKUP_ID
        and event.get("destroyed") is True
    ]
    receipt["server_airplane_damage_events"] = airplane_damage_events
    receipt["server_npc_kill_events"] = npc_kill_events
    # FL-4265 / FL-4832 route (B): join the server's own steering fact to the
    # client-measured wire intent for the same tick, session, actor and
    # airframe. Absence is recorded as absence, never as agreement.
    steering_witnesses = _read_flight_steering_witnesses(server_log)
    steering_join = _fl4265_steering_witness_join(receipt, steering_witnesses)
    receipt["server_flight_steering_witnesses"] = steering_witnesses
    receipt["server_flight_steering_join"] = steering_join
    receipt["server_steering_witness_available"] = bool(
        steering_join["server_steering_witness_available"]
    )
    receipt["server_steering_witness_absence_reason"] = steering_join[
        "server_steering_witness_absence_reason"
    ]
    receipt["flight_authority_binding_stage"] = (
        "client_and_server_witness_joined"
        if steering_join["server_steering_witness_available"]
        else "client_observation_only"
    )
    client_identity = _source_identity(
        godot_project.parent,
        fingerprint_scope=godot_project,
    )
    server_identity = _source_identity(colyseus_dir, scope_path=colyseus_dir)
    client_dirty_paths = list(client_identity["git_dirty_paths"])
    server_dirty_paths = list(server_identity["git_dirty_paths"])
    # Legacy identity keys remain aliases for the launched client checkout.
    receipt["git_head"] = client_identity["git_head"]
    receipt["git_dirty_count"] = len(client_dirty_paths)
    receipt["git_dirty_paths"] = client_dirty_paths
    receipt["git_source_root"] = client_identity["source_root"]
    receipt["git_identity_valid"] = client_identity["valid"]
    receipt["client_git_head"] = client_identity["git_head"]
    receipt["client_source_root"] = client_identity["source_root"]
    receipt["client_dirty_paths"] = client_dirty_paths
    receipt["client_identity_valid"] = client_identity["valid"]
    receipt["client_runtime_scope_fingerprint"] = client_identity[
        "runtime_scope_fingerprint"
    ]
    receipt["server_git_head"] = server_identity["git_head"]
    receipt["server_source_root"] = server_identity["source_root"]
    receipt["server_source_scope"] = server_identity["source_scope"]
    receipt["server_dirty_paths"] = server_dirty_paths
    receipt["server_identity_valid"] = server_identity["valid"]
    receipt["server_runtime_scope_fingerprint"] = server_identity[
        "runtime_scope_fingerprint"
    ]
    if not client_identity["valid"] or not server_identity["valid"]:
        # A frame without a readable full source identity is diagnostic debris,
        # not evidence. Fail closed before a caller can promote the receipt.
        receipt["pass"] = False
        receipt["source_identity_error"] = "client_or_shared_server_identity_unavailable"
    receipt["godot_rc"] = godot_rc
    receipt["analyzer_rc"] = analyzer_rc
    receipt["godot_project"] = str(godot_project)
    receipt["colyseus_dir"] = str(colyseus_dir)
    receipt["server_source_dir"] = str(colyseus_dir)
    receipt["godot_script"] = godot_script
    runtime_frame_pacing = receipt.get("frame_pacing_runtime", {})
    uncapped_requested = bool(
        args.fl4359_clean_play_no_capture or args.fl4265_crash_performance
    )
    receipt["frame_pacing"] = {
        "uncapped": uncapped_requested,
        "engine_args": (
            list(GODOT_UNCAPPED_ARGS)
            if uncapped_requested
            else []
        ),
        "runtime": runtime_frame_pacing if isinstance(runtime_frame_pacing, dict) else {},
    }
    receipt["capture_child_environment"] = child_environment_contract
    # These top-level mirrors are intentionally small. The package pair
    # validator can compare both arms without understanding every diagnostic
    # detail retained in the nested child-environment receipt.
    receipt["capture_child_denied_environment_policy_sha256"] = (
        child_environment_contract["denied_environment_policy_sha256"]
    )
    receipt["capture_child_forbidden_environment_absent"] = (
        child_environment_contract["effective_forbidden_keys_absent"]
    )
    receipt["capture_child_canonical_route_environment"] = (
        child_environment_contract["effective_canonical_route_environment"]
    )
    receipt["capture_child_canonical_route_environment_sha256"] = (
        child_environment_contract[
            "effective_canonical_route_environment_sha256"
        ]
    )
    receipt["capture_child_canonical_route_environment_match"] = (
        child_environment_contract["canonical_route_environment_match"]
    )
    if args.fl4265_damage_route:
        receipt["renderer_architecture_profile_from_source"] = (
            FL4431_DAMAGE_CURRENT_PROFILE
        )
        receipt["front_door_contract"] = _fl4265_damage_front_door_contract(receipt)
    elif args.fl4265_crash_sequence_route:
        # Which of the three kinds of artifact this arm was asked to produce.
        # The contract below insists the game client agreed, so a run can never
        # be filed under the wrong kind of evidence.
        expected_artifact_role = (
            "operator_recording"
            if args.fl4265_crash_recording_route
            else (
                "performance_diagnostic"
                if args.fl4265_crash_performance
                else "diagnostic_choreography"
            )
        )
        receipt["front_door_contract"] = {
            "schema": "fl4265.crash_sequence_front_door.v4",
            "route": "server_npc_projectile_airplane_crash_sequence",
            "artifact_role": expected_artifact_role,
            "acceptance_surface": False,
            "server_bot_required": True,
            "server_synthetic_damage_enabled": False,
            "npc_kill_event_required": True,
            "npc_kill_event_count": len(npc_kill_events),
            "client_damage_mutation_used": False,
            "fps_sample_eligible": args.fl4265_crash_performance,
            "ok": (
                receipt.get("proof") == "server_npc_projectile_airplane_crash_sequence"
                and receipt.get("artifact_role") == expected_artifact_role
                and receipt.get("acceptance_surface") is False
                and receipt.get("operator_visual_signoff_required") is True
                and receipt.get("server_bot_tracer_snapshot_observed") is True
                and receipt.get("fullscreen_surface_display_backed") is True
                and receipt.get("camera_projection")
                == ("perspective" if args.fl4265_crash_perspective else "orthographic")
                and receipt.get("camera_orbit_required") is True
                and receipt.get("camera_orbit_observed") is True
                and float(receipt.get("camera_orbit_delta_radians", 0.0)) >= 0.12
                and receipt.get("client_damage_mutation_used") is False
                and receipt.get("fps_sample_eligible")
                is args.fl4265_crash_performance
                # What counts as "this arm produced its artifact" differs by arm:
                #   measuring arm  -- it collected at least one frame timing
                #   recording arm  -- the screen recorder actually started, and
                #                     named the file it was writing
                #   stills arm     -- it saved at least one picture
                and (
                    int(receipt.get("fps_sample_count", 0)) > 0
                    if args.fl4265_crash_performance
                    else (
                        (
                            receipt.get("screen_recording_started") is True
                            and bool(receipt.get("screen_recording_path"))
                        )
                        if args.fl4265_crash_recording_route
                        else receipt.get("frame_captured") is True
                    )
                )
                and (
                    receipt.get("frame_pacing", {}).get("uncapped") is True
                    and receipt.get("frame_pacing", {}).get("engine_args")
                    == GODOT_UNCAPPED_ARGS
                    and receipt.get("frame_pacing", {}).get("runtime", {}).get(
                        "vsync_disabled"
                    )
                    is True
                    and int(
                        receipt.get("frame_pacing", {}).get("runtime", {}).get(
                            "engine_max_fps", -1
                        )
                    )
                    == 0
                    if args.fl4265_crash_performance
                    else True
                )
                and bool(npc_kill_events)
            ),
        }
    elif fl4359_moving_capture:
        receipt["front_door_contract"] = _fl4359_front_door_contract(args, receipt)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print("[fl4265-proof] receipt:", json.dumps(receipt, indent=2), flush=True)

    # The recording and measuring arms save no pictures ON PURPOSE, so "no frame
    # captured" is their normal, correct outcome and must not be reported as a
    # missing display. Only the stills arm is expected to have saved something.
    if not receipt.get("frame_captured") and not (
        args.fl4265_crash_recording_route
        or args.fl4265_crash_performance
        or args.fl4808_mount_regression
    ):
        reason = receipt.get("capture_reason", "")
        if "no_rendering_device" in reason or "image_null" in reason:
            display_note = ("DISPLAY UNAVAILABLE: Godot had no rendering device, so no "
                            "frame was captured. Data + render facts were still asserted. "
                            "This is a missing display, NOT an architecture blocker.")
        else:
            display_note = f"frame not captured (reason={reason})"
        print(f"[fl4265-proof] {display_note}", flush=True)

    if receipt.get("pass"):
        if args.fl4265_damage_route:
            contract = receipt.get("front_door_contract", {})
            if not contract.get("ok"):
                print(
                    "[fl4265-proof] damage front-door contract failed: %s"
                    % contract.get("reason", "unknown"),
                    flush=True,
                )
                return 1
        elif args.fl4265_crash_sequence_route:
            contract = receipt.get("front_door_contract", {})
            if not contract.get("ok"):
                print(
                    "[fl4265-proof] crash front-door contract failed",
                    flush=True,
                )
                return 1
        elif fl4359_moving_capture:
            contract = receipt.get("front_door_contract", {})
            if not contract.get("ok"):
                print(
                    "[fl4265-proof] FL-4359 front-door contract failed: %s"
                    % contract.get("reason", "unknown"),
                    flush=True,
                )
                return 1
        if args.fl4359_jitter_capture:
            if not jitter_path.exists():
                print(f"[fl4265-proof] FL-4359 jitter capture missing: {jitter_path}", flush=True)
                return 1
            # FL-4359 defect-1 fix: the old scripts/analyze_fl4359_jitter.py is
            # deleted and git-guard-BANNED (accepted steady-pose evidence). Use the
            # moving-camera analyzer, which fails closed on a non-moving capture.
            analyzer_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "analyze_fl4359_moving_camera.py"),
                str(jitter_path),
                "--all-frames",
                "--summary-json", str(jitter_summary_path),
                "--max-snap-px-delta", str(args.jitter_max_snap_px_delta),
                "--max-snap-sign-flip-rate", str(args.jitter_max_sign_flip_rate),
                "--max-content-jitter-pct", str(args.jitter_max_content_pct),
                # FL-4359 defect-1b (Codex re-review): without --fail-on-gate the
                # analyzer exits 0 even when the FL-4359 gameplay gates FAIL, so the
                # proof wrapper would return PASS on a failed run. Propagate gate
                # failure as a nonzero exit (the wrapper returns analyzer.returncode).
                "--fail-on-gate",
            ]
            analyzer = subprocess.run(analyzer_cmd, cwd=str(ROOT), text=True, capture_output=True)
            analyzer_rc = analyzer.returncode
            receipt["analyzer_rc"] = analyzer_rc
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            print(analyzer.stdout[-4000:], flush=True)
            if analyzer.returncode != 0:
                print(analyzer.stderr[-2000:], flush=True)
                return analyzer.returncode
        if args.fl4265_damage_route:
            print(
                "[fl4265-proof] material-211 damage receipt accepted: "
                "hp_interval=%s..%s frames=%s host_smoke=%s mat211_faces=%s "
                "bit26_subcells=%s phase_changed=%s ink_changed=%s paths=%s"
                % (
                    receipt.get("damage_hp_interval_min"),
                    receipt.get("damage_hp_interval_max"),
                    receipt.get("damage_frame_count"),
                    receipt.get("host_damage_smoke_sample_count"),
                    receipt.get("retained_mat211_face_count"),
                    receipt.get("product_mat211_contributed_subcell_count"),
                    receipt.get("damage_flame_phase_changed"),
                    receipt.get("damage_flame_ink_changed"),
                    receipt.get("damage_frame_paths"),
                ),
                flush=True,
            )
        else:
            print(
                "[fl4265-proof] receipt accepted proof=%s frame_captured=%s"
                % (receipt.get("proof"), receipt.get("frame_captured")),
                flush=True,
            )
        return 0
    print("[fl4265-proof] FAIL", flush=True)
    return 1


# ===========================================================================
# FL-4265 / FL-4667 operator recording route
# ===========================================================================
# Everything below coordinates the two-video run the operator asked for: fly the
# crash once with the flat "orthographic" camera, fly it again with the
# perspective camera, and end up with exactly two watchable videos on the
# Desktop.
#
# WHY TWO SEPARATE GAME LAUNCHES rather than one game that switches camera
# halfway. The airplane is owned by the game server, and the server has no way
# to bring a destroyed airplane back. Once the NPC shoots it down it stays shot
# down: there is no heal, no repair, and no respawn for world items. A second
# crash therefore needs a genuinely fresh server and a fresh game, which is
# exactly what running the single-arm function twice gives us. As a bonus, two
# launches produce the two separate video files naturally, with no risk of the
# two runs sharing leftover state.

# How long the recorder's video file is allowed to keep growing after the game
# has exited before we give up waiting for it. The game already waits a few
# seconds internally; this is the outer safety net for a slow disk.
FL4265_RECORDING_FINALIZE_TIMEOUT_S = 15.0
# How long the file size must stay unchanged before we call it finished. A video
# being written grows continuously, so "the size did not change for a whole
# second" is a simple and reliable end-of-writing signal.
FL4265_RECORDING_STABLE_WINDOW_S = 1.0
# Smallest believable finished recording, in bytes. A roughly twenty-second
# screen recording is tens of megabytes; anything under one megabyte means the
# recorder started and then failed almost immediately -- overwhelmingly because
# macOS refused it permission to see the screen.
FL4265_RECORDING_MIN_BYTES = 1024 * 1024


def _await_stable_recording_file(path: Path) -> tuple[bool, int, str]:
    """Wait until a video file has stopped growing, then judge whether it is real.

    path: the file the game client said the recorder was writing.

    Returns three things:
      ok    -- True only if the file exists and is comfortably larger than a
               plainly broken one.
      size  -- the final size in bytes (0 when the file never appeared).
      note  -- a short human-readable explanation, empty when everything is fine.

    WHY THIS EXISTS. The recorder is a separate program that keeps running for a
    moment after the game quits. Checking the file the instant the game exits
    could catch it half-written and wrongly declare the recording broken. So we
    watch the size until it stops changing.
    """

    deadline = time.time() + FL4265_RECORDING_FINALIZE_TIMEOUT_S
    last_size = -1
    # When the size last changed. Once "now" is a full stable-window past this,
    # the file has stopped growing.
    last_change = time.time()
    while time.time() < deadline:
        size = path.stat().st_size if path.exists() else 0
        if size != last_size:
            last_size = size
            last_change = time.time()
        elif size > 0 and (time.time() - last_change) >= FL4265_RECORDING_STABLE_WINDOW_S:
            break
        time.sleep(0.25)
    if not path.exists():
        return False, 0, "no video file was written"
    final_size = path.stat().st_size
    if final_size < FL4265_RECORDING_MIN_BYTES:
        return (
            False,
            final_size,
            f"video file is only {final_size} bytes, which is too small to be a real recording",
        )
    return True, final_size, ""


def _write_recording_final_path(receipt_path: Path, final_path: str) -> bool:
    """Record the video's FINAL name in an already-written receipt.

    receipt_path: the arm's receipt.json, written earlier by the game client.
    final_path:   where the video lives now, after this script renamed it.

    Returns True when the receipt was updated, False when it could not be.

    The existing `screen_recording_path` field is deliberately left untouched:
    it is the truthful record of the name the recorder originally wrote, and
    overwriting it would erase that history. The final name is stored beside it
    under a new key instead.

    The final path is checked on disk BEFORE it is written down. Recording a
    path that does not exist would be worse than recording nothing, because a
    later reader would trust it.
    """

    if not Path(final_path).exists():
        print(
            f"[fl4265-proof] refusing to record a video path that is not on "
            f"disk: {final_path}",
            flush=True,
        )
        return False
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, json.JSONDecodeError) as read_error:
        print(
            f"[fl4265-proof] could not reopen {receipt_path} to record the "
            f"final video path: {read_error}",
            flush=True,
        )
        return False
    receipt["screen_recording_final_path"] = final_path
    try:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    except OSError as write_error:
        print(
            f"[fl4265-proof] could not update {receipt_path} with the final "
            f"video path: {write_error}",
            flush=True,
        )
        return False
    return True


def _print_screen_recording_permission_help() -> None:
    """Explain the single most likely reason a recording came out empty."""

    print(
        "[fl4265-proof] The screen recorder produced no usable video. On macOS "
        "this is almost always a permissions problem: the application that "
        "launched this run (your terminal program) needs permission to see the "
        "screen. Grant it under System Settings > Privacy & Security > Screen "
        "Recording, then quit and reopen that application so the new permission "
        "takes effect, and run this again.",
        flush=True,
    )


def _run_crash_recording_arms(args: argparse.Namespace) -> int:
    """Fly the crash twice -- flat camera then perspective camera -- and film both.

    args: the parsed command line, exactly as the operator typed it.

    Returns 0 only when BOTH arms flew the whole crash story and BOTH produced a
    usable video file. Any problem gives a non-zero code, but both arms are
    always attempted so a single bad run never costs the operator the other video.
    """

    # Reject request combinations that cannot mean anything, before anything is
    # launched, so a doomed run fails in a second rather than in two minutes.
    if args.fl4265_crash_perspective:
        print(
            "[fl4265-proof] ERROR: the crash recording route already flies both "
            "camera types; do not also pass the perspective flag",
            flush=True,
        )
        return 2
    if args.fl4265_crash_performance:
        print(
            "[fl4265-proof] ERROR: the crash recording route and the performance "
            "measurement route cannot share one run",
            flush=True,
        )
        return 2
    if args.fl4265_damage_route:
        print(
            "[fl4265-proof] ERROR: the crash recording route and the damage route "
            "must run as separate processes",
            flush=True,
        )
        return 2
    # The FL-4359 "matched movement" family measures camera and content motion on
    # a scripted flight path; the crash route flies a server-owned dogfight. They
    # are different journeys, so combining them would produce numbers that
    # describe neither. The single-arm function rejects this too, but only once
    # it is already inside an arm -- which would print two arm banners before
    # failing. Checking here keeps the promise made at the top of this function
    # that a doomed request fails before anything is launched.
    if (
        args.fl4359_jitter_capture
        or args.fl4359_clean_play_frames > 0
        or args.fl4359_visual_capture_stride > 0
        or args.fl4359_visual_capture_limit > 0
        or args.fl4359_visual_capture_start_frame > 0
        or args.fl4431_secondary_effect_diagnostic_only
    ):
        print(
            "[fl4265-proof] ERROR: the crash recording route cannot share a run "
            "with the matched-movement and clean-play timing flags",
            flush=True,
        )
        return 2

    base_out = Path(args.out)
    if not base_out.is_absolute():
        base_out = (ROOT / base_out).resolve()

    # The two arms, in the order the operator will watch them. Each entry pairs
    # the camera type with the words used for the output folder and the final
    # video filename.
    arms = [
        {"label": "ortho", "perspective": False},
        {"label": "perspective", "perspective": True},
    ]

    arm_results: list[dict[str, object]] = []
    overall_rc = 0

    for arm_index, arm in enumerate(arms):
        label = str(arm["label"])
        perspective = bool(arm["perspective"])
        print(
            f"[fl4265-proof] ===== recording arm {arm_index + 1} of {len(arms)}: "
            f"{label} camera =====",
            flush=True,
        )

        # Build this arm's own command line. copy.copy gives a shallow copy of
        # the parsed options, which is what we want: we are only replacing whole
        # values, never editing a shared list in place.
        arm_args = copy.copy(args)
        arm_args.fl4265_crash_sequence_route = True
        arm_args.fl4265_crash_recording_route = True
        arm_args.fl4265_crash_perspective = perspective
        # Each arm writes its receipt and logs into its own folder, so the second
        # arm cannot overwrite the first arm's evidence.
        arm_args.out = str(base_out / label)
        # A fresh list per arm. Without list(...) both arms would share, and then
        # append to, the SAME list object.
        arm_args.extra_user_arg = list(args.extra_user_arg)

        arm_rc = _run_single_arm(arm_args)
        if arm_rc != 0:
            overall_rc = arm_rc

        # Read back what the game client recorded about itself. This is the only
        # honest way to learn which video file belongs to which camera type.
        arm_receipt_path = Path(arm_args.out) / "receipt.json"
        arm_receipt: dict = {}
        if arm_receipt_path.exists():
            try:
                arm_receipt = json.loads(arm_receipt_path.read_text())
            except (OSError, json.JSONDecodeError):
                arm_receipt = {}

        recording_path_text = str(arm_receipt.get("screen_recording_path", ""))
        result: dict[str, object] = {
            "label": label,
            "perspective": perspective,
            "rc": arm_rc,
            "receipt_path": str(arm_receipt_path),
            "camera_projection": arm_receipt.get("camera_projection"),
            "fl4703_airplane_presentation_law": arm_receipt.get(
                "fl4703_airplane_presentation_law"
            ),
            "hitch_count_33_333ms": arm_receipt.get("hitch_count_33_333ms"),
            "fps_frame_time_ms": arm_receipt.get("fps_frame_time_ms"),
            "fps_sample_count": arm_receipt.get("fps_sample_count"),
            "crash_sequence_frame_count": arm_receipt.get("crash_sequence_frame_count"),
            "recorded_path": recording_path_text,
            "final_path": "",
            "video_bytes": 0,
            "video_ok": False,
        }

        if not recording_path_text:
            # No video path in the receipt. There are two very different reasons
            # this can happen, and telling them apart matters: sending somebody
            # to the macOS privacy settings when their real problem was a server
            # that would not start is worse than saying nothing.
            #
            #   a) The arm never got as far as recording -- the request was
            #      rejected, the game server did not come up, the game client
            #      crashed early. In that case the receipt is missing entirely,
            #      and whatever went wrong was already printed above.
            #   b) The arm DID reach the recording step and the recorder refused
            #      to start. That is the case where screen-recording permission
            #      is the overwhelmingly likely cause.
            reached_the_recorder = bool(arm_receipt) and bool(
                arm_receipt.get("screen_recording_requested")
            )
            if reached_the_recorder:
                print(
                    f"[fl4265-proof] arm {label}: the game reached the recording "
                    "step but the recorder did not start",
                    flush=True,
                )
                _print_screen_recording_permission_help()
            else:
                print(
                    f"[fl4265-proof] arm {label}: the run ended before recording "
                    "could start; see the error above for the reason",
                    flush=True,
                )
            overall_rc = overall_rc or 1
            arm_results.append(result)
            continue

        recording_path = Path(recording_path_text)
        video_ok, video_bytes, video_note = _await_stable_recording_file(recording_path)
        result["video_bytes"] = video_bytes
        result["video_ok"] = video_ok
        if not video_ok:
            print(f"[fl4265-proof] arm {label}: {video_note}", flush=True)
            _print_screen_recording_permission_help()
            overall_rc = overall_rc or 1
            arm_results.append(result)
            continue

        result["final_path"] = str(recording_path)
        arm_results.append(result)
        print(
            f"[fl4265-proof] arm {label}: recorded {video_bytes} bytes to "
            f"{recording_path}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Give the two videos names that say what is in them.
    # ------------------------------------------------------------------
    # The recorder names its files after the moment it started, which is
    # truthful but tells the operator nothing about which camera they are about
    # to watch. Renaming afterwards is deliberately done HERE rather than inside
    # the recorder: the recorder's own naming stays untouched for everyone else,
    # including anyone pressing F9 by hand.
    good_arms = [r for r in arm_results if r["video_ok"]]
    distinct_sources = {str(r["recorded_path"]) for r in good_arms}
    if len(distinct_sources) != len(good_arms):
        # Two arms claiming the same file would mean one recording overwrote the
        # other. Renaming in that state would destroy evidence, so stop.
        print(
            "[fl4265-proof] ERROR: two recording arms name the SAME video file; "
            "refusing to rename anything",
            flush=True,
        )
        overall_rc = overall_rc or 1
    else:
        for result in good_arms:
            source = Path(str(result["recorded_path"]))
            # Keep the recorder's timestamp in the new name so two runs on the
            # same day still cannot collide with each other.
            stamp = source.stem.replace("FL4359_screen_recording_", "")
            destination = source.with_name(
                f"FL4265_crash_{result['label']}_{stamp}{source.suffix}"
            )
            try:
                source.rename(destination)
                result["final_path"] = str(destination)
            except OSError as rename_error:
                # A failed rename is not worth failing the run over: the video
                # still exists under its original name and is still watchable.
                print(
                    f"[fl4265-proof] could not rename {source} -> {destination}: "
                    f"{rename_error}",
                    flush=True,
                )

    # ------------------------------------------------------------------
    # Write the FINAL video path back into each arm's receipt.
    # ------------------------------------------------------------------
    # WHY THIS IS NOT OPTIONAL. The game client writes its receipt while it is
    # still running, which is necessarily BEFORE the rename above. So the
    # receipt's own `screen_recording_path` names a file that no longer exists
    # by the time anyone reads it. Any tool that tries to open the video named
    # in the receipt would get "file not found", and the rule that every visual
    # artifact must be paired with the runtime record that produced it would be
    # satisfied only in prose, not in machine-readable form.
    #
    # The original field is left EXACTLY as the game client wrote it, because it
    # is the honest record of what the recorder was told to write. The final
    # name is added alongside it as a new field. Keeping both means a reader can
    # follow the whole history: what was recorded, and what it is called now.
    for result in arm_results:
        final_path_text = str(result["final_path"])
        if not final_path_text:
            continue
        _write_recording_final_path(Path(str(result["receipt_path"])), final_path_text)

    # ------------------------------------------------------------------
    # Final report.
    # ------------------------------------------------------------------
    print("[fl4265-proof] ===== operator recording summary =====", flush=True)
    for result in arm_results:
        print(
            "[fl4265-proof]   %-11s camera_projection=%s video=%s bytes=%s "
            "slow_frames_over_33ms=%s mean_frame_ms=%s stills_saved=%s "
            "fl4703_presentation_law=%s"
            % (
                result["label"],
                result["camera_projection"],
                result["final_path"] or "(none)",
                result["video_bytes"],
                result["hitch_count_33_333ms"],
                result["fps_frame_time_ms"],
                result["crash_sequence_frame_count"],
                result["fl4703_airplane_presentation_law"],
            ),
            flush=True,
        )

    playable = [r for r in arm_results if r["video_ok"]]
    if len(playable) == len(arms):
        # Only now, with both recordings safely finished, is it safe to open
        # them: a video player appearing on screen earlier would have been
        # filmed into the second recording.
        for result in playable:
            opened = str(result["final_path"])
            if opened:
                subprocess.run(["/usr/bin/open", opened], check=False)
        print(
            "[fl4265-proof] two recordings are on your Desktop; both have been "
            "opened for viewing",
            flush=True,
        )
    else:
        print(
            "[fl4265-proof] did NOT produce two usable recordings; see the notes "
            "above",
            flush=True,
        )
        overall_rc = overall_rc or 1

    # Whether the crash actually looks right on screen -- the plane burning, the
    # explosion, the impact, and motion free of stutter -- is NOT decided here.
    # No automated check can see a video. That judgement is owed to the operator.
    print(
        "[fl4265-proof] machine checks only. Whether the crash, fire, and "
        "explosion are VISIBLE and the motion is smooth is owed to operator "
        "viewing of both files above.",
        flush=True,
    )
    return overall_rc


def _finite_damage_pose(record: dict, field: str) -> bool:
    value = record.get(field)
    if not isinstance(value, dict) or set(value) < {"x", "y", "z"}:
        return False
    try:
        return all(
            math.isfinite(float(value[axis]))
            for axis in ("x", "y", "z")
        )
    except (TypeError, ValueError):
        return False


def _damage_png_identity_reasons(record: dict, frame_index: int) -> list[str]:
    """Validate that one damage row still names its exact saved PNG bytes.

    The Godot child computes the identity immediately after encoding the image.
    This parent runs after Godot exits, so recomputing it here also detects a
    later overwrite between capture and package admission.
    """

    reasons: list[str] = []
    prefix = f"damage_frame_{frame_index}"
    capture_path = Path(str(record.get("capture_path", "")))
    png_sha256 = str(record.get("png_sha256", ""))
    try:
        png_byte_size = int(record.get("png_byte_size", 0))
    except (TypeError, ValueError):
        png_byte_size = 0
    if (
        len(png_sha256) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in png_sha256
        )
    ):
        reasons.append(f"{prefix}_png_hash_invalid")
    if png_byte_size <= 0:
        reasons.append(f"{prefix}_png_size_invalid")
    if not capture_path.is_file():
        reasons.append(f"{prefix}_png_file_missing")
        return reasons

    disk_byte_size = capture_path.stat().st_size
    if disk_byte_size != png_byte_size:
        reasons.append(f"{prefix}_png_size_mismatch")
    disk_sha256 = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    if disk_sha256.lower() != png_sha256.lower():
        reasons.append(f"{prefix}_png_hash_mismatch")
    return reasons


def _fl4265_damage_front_door_contract(
    receipt: dict,
) -> dict:
    """Validate the material-211 arm without treating choreography as gameplay.

    The Godot driver records the facts. This wrapper only rejects incomplete,
    contradictory, synthetic, and performance-contaminating receipts.
    """
    reasons: list[str] = []
    records = receipt.get("damage_capture_records", [])
    if (
        receipt.get("renderer_architecture_profile_from_source")
        != FL4431_DAMAGE_CURRENT_PROFILE
    ):
        reasons.append("damage_renderer_source_profile_stamp_missing")
    if receipt.get("proof") != "server_owned_material_211_damage_flight":
        reasons.append("wrong_damage_proof_surface")
    if receipt.get("schema") != "fl4431.damage_arm_asymmetric.v1":
        reasons.append("wrong_damage_arm_schema")
    if receipt.get("damage_route") is not True:
        reasons.append("damage_route_label_missing")
    if receipt.get("server_bot_required") is not True:
        reasons.append("server_bot_route_missing")
    if receipt.get("fps_sample_eligible") is not False:
        reasons.append("damage_route_claims_fps_eligibility")
    if receipt.get("synthetic_hp_used") is not False:
        reasons.append("synthetic_hp_owner_present")
    if receipt.get("synthetic_render_flag_used") is not False:
        reasons.append("synthetic_render_owner_present")
    if receipt.get("physical_flight_action") != "move_right":
        reasons.append("wrong_damage_flight_action")
    if receipt.get("physical_fire_action") != "attack_primary":
        reasons.append("wrong_damage_fire_action")
    if receipt.get("damage_interval_missed") is not False:
        reasons.append("authoritative_damage_interval_missed")
    if receipt.get("destruction_observed") is not False:
        reasons.append("airplane_destroyed_during_damage_route")
    if not isinstance(records, list) or len(records) != 2:
        reasons.append("damage_frame_pair_missing")
        records = []

    frame_paths: list[str] = []
    phase_steps: list[int] = []
    animation_times: list[float] = []
    route_ticks: list[int] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            reasons.append(f"damage_frame_{index}_invalid")
            continue
        frame_paths.append(str(record.get("capture_path", "")))
        # Validate the bytes while the arm still owns its original output
        # directory. A later package copy cannot repair a missing, truncated,
        # substituted, and post-exit-overwritten source frame.
        reasons.extend(_damage_png_identity_reasons(record, index))
        try:
            hp = int(record.get("plane_hp", -1))
            max_hp = int(record.get("plane_max_hp", -1))
            host_count = int(record.get("host_damage_smoke_sample_count", 0))
            phase_steps.append(int(record.get("damage_flame_phase_step", -1)))
            animation_times.append(
                float(record.get("animation_time_seconds", -1.0))
            )
            server_tick = int(record["authority_server_tick"])
            route_tick = int(record["route_tick"])
            route_ticks.append(route_tick)
            ink_sample = record.get("ink_sample", {})
            blue_count = int(
                ink_sample.get("blue_ramp_pixel_count", 0)
                if isinstance(ink_sample, dict)
                else 0
            )
        except (KeyError, TypeError, ValueError):
            reasons.append(f"damage_frame_{index}_numeric_fact_invalid")
            continue
        if hp < 10 or hp > 50 or max_hp != 100:
            reasons.append(f"damage_frame_{index}_hp_outside_interval")
        if record.get("pilot_mounted") is not True:
            reasons.append(f"damage_frame_{index}_pilot_not_mounted")
        if record.get("pilot_alive") is not True:
            reasons.append(f"damage_frame_{index}_pilot_not_alive")
        if record.get("pilot_is_dead") is not False:
            reasons.append(f"damage_frame_{index}_pilot_dead")
        if record.get("plane_airborne") is not True:
            reasons.append(f"damage_frame_{index}_plane_not_airborne")
        if record.get("plane_grounded") is not False:
            reasons.append(f"damage_frame_{index}_plane_grounded")
        if record.get("plane_destroyed") is not False:
            reasons.append(f"damage_frame_{index}_plane_destroyed")
        if record.get("physical_inputs_held") is not True:
            reasons.append(f"damage_frame_{index}_physical_input_not_held")
        if record.get("fps_sample_eligible") is not False:
            reasons.append(f"damage_frame_{index}_claims_fps_eligibility")
        if host_count <= 0:
            reasons.append(f"damage_frame_{index}_host_smoke_missing")
        if (
            record.get("renderer_architecture_profile")
            != FL4431_DAMAGE_CURRENT_PROFILE
        ):
            reasons.append(f"damage_frame_{index}_profile_mismatch")
        if record.get("route_identity") != FL4431_DAMAGE_ROUTE_IDENTITY:
            reasons.append(f"damage_frame_{index}_route_identity_mismatch")
        if server_tick < 0 or route_tick < 0:
            reasons.append(f"damage_frame_{index}_server_tick_missing")
        if animation_times[-1] < 0.0 or not math.isfinite(animation_times[-1]):
            reasons.append(f"damage_frame_{index}_animation_clock_invalid")
        for pose_field in (
            "player_position",
            "camera_position",
            "airplane_position",
        ):
            if not _finite_damage_pose(record, pose_field):
                reasons.append(
                    f"damage_frame_{index}_{pose_field}_invalid"
                )
        for yaw_field in ("player_yaw", "camera_yaw", "airplane_yaw"):
            try:
                if not math.isfinite(float(record[yaw_field])):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                reasons.append(f"damage_frame_{index}_{yaw_field}_invalid")
        ownership = record.get("ownership_receipt")
        if not isinstance(ownership, dict):
            reasons.append(f"damage_frame_{index}_ownership_receipt_missing")
        else:
            try:
                if int(
                    ownership.get("host_damage_smoke_sample_count", 0)
                ) != host_count:
                    reasons.append(
                        f"damage_frame_{index}_ownership_host_count_mismatch"
                    )
            except (TypeError, ValueError):
                reasons.append(
                    f"damage_frame_{index}_ownership_host_count_invalid"
                )
            for ownership_field in (
                "retained_mat211_face_count",
                "product_mat211_contribution_receipt_available",
                "product_mat211_contributed_subcell_count",
                "product_mat211_contribution_bit",
                "product_mat211_style",
                "architecture_absence_reason",
            ):
                if record.get(ownership_field) != ownership.get(
                    ownership_field
                ):
                    reasons.append(
                        f"damage_frame_{index}_{ownership_field}_mismatch"
                    )
        if isinstance(ownership, dict):
            current_required_keys = {
                "host_damage_smoke_sample_count",
                "retained_mat211_face_count",
                "product_mat211_contribution_receipt_available",
                "product_mat211_contributed_subcell_count",
                "product_mat211_contribution_bit",
                "product_mat211_style",
                "architecture_absence_reason",
            }
            if not current_required_keys.issubset(ownership):
                reasons.append(
                    f"damage_frame_{index}_current_receipt_schema_incomplete"
                )
            else:
                try:
                    retained_count = int(ownership["retained_mat211_face_count"])
                    product_count = int(
                        ownership[
                            "product_mat211_contributed_subcell_count"
                        ]
                    )
                    product_bit = int(
                        ownership["product_mat211_contribution_bit"]
                    )
                except (TypeError, ValueError):
                    retained_count = 0
                    product_count = 0
                    product_bit = 0
                if retained_count <= 0:
                    reasons.append(
                        f"damage_frame_{index}_current_retained_mat211_missing"
                    )
                if (
                    ownership.get(
                        "product_mat211_contribution_receipt_available"
                    )
                    is not True
                ):
                    reasons.append(
                        f"damage_frame_{index}_current_product_receipt_missing"
                    )
                if product_count <= 0:
                    reasons.append(
                        f"damage_frame_{index}_current_product_count_missing"
                    )
                if product_bit != FL4431_DAMAGE_CURRENT_CONTRIBUTION_BIT:
                    reasons.append(
                        f"damage_frame_{index}_current_product_bit_mismatch"
                    )
                if ownership.get("product_mat211_style") != (
                    FL4431_DAMAGE_CURRENT_STYLE
                ):
                    reasons.append(
                        f"damage_frame_{index}_current_product_style_mismatch"
                    )
                if ownership.get("architecture_absence_reason") != (
                    FL4431_DAMAGE_CURRENT_ABSENCE_REASON
                ):
                    reasons.append(
                        f"damage_frame_{index}_current_absence_reason_mismatch"
                    )
        if not isinstance(ink_sample, dict) or ink_sample.get(
            "blue_ramp_observed"
        ) is not True or blue_count <= 0:
            reasons.append(f"damage_frame_{index}_blue_ink_missing")
    if len(frame_paths) == 2 and (
        not all(frame_paths) or len(set(frame_paths)) != 2
    ):
        reasons.append("damage_frame_paths_not_distinct")
    if len(phase_steps) == 2 and phase_steps[0] == phase_steps[1]:
        reasons.append("damage_flame_phase_static")
    if len(animation_times) == 2 and animation_times[1] <= animation_times[0]:
        reasons.append("damage_animation_clock_not_separated")
    if receipt.get("damage_flame_phase_or_ink_changed") is not True:
        reasons.append("damage_flame_temporal_change_missing")

    return {
        "ok": not reasons,
        "reason": ",".join(reasons),
        "kind": "server_owned_material_211_damage_flight_asymmetric_arm",
        "renderer_architecture_profile": FL4431_DAMAGE_CURRENT_PROFILE,
        "manual_run": False,
        "diagnostic_substitute": False,
        "server_bot_required": True,
        "physical_input_required": True,
        "authoritative_hp_required": True,
        "pixel_evidence": True,
        "performance_evidence": False,
        "fps_sample_eligible": False,
        "damage_frame_paths": frame_paths,
        "damage_phase_steps": phase_steps,
        "damage_route_ticks": route_ticks,
    }


def _fl4359_front_door_contract(args: argparse.Namespace, receipt: dict) -> dict:
    requested_frames = max(
        0,
        args.fl4359_clean_play_frames if args.fl4359_clean_play_frames > 0 else args.fl4359_jitter_hold_frames,
    )
    jitter_frame_limit = max(0, args.fl4359_jitter_capture_frame_limit)
    reasons: list[str] = []

    if args.fl4431_secondary_effect_diagnostic_only:
        if receipt.get("secondary_effect_diagnostic_only") is not True:
            reasons.append("secondary_effect_diagnostic_mode_missing")
        if receipt.get("diagnostic_nonvisual") is not True:
            reasons.append("secondary_effect_diagnostic_not_labeled_nonvisual")
        if receipt.get("route_tick_exactness_required") is not False:
            reasons.append("secondary_effect_diagnostic_claims_exact_route")
        if receipt.get("frame_captured") is not False:
            reasons.append("secondary_effect_diagnostic_captured_pixels")
        if receipt.get("product_mat207_contribution_receipt_available") is not True:
            reasons.append("secondary_effect_product_receipt_missing")
        try:
            if int(receipt.get("product_mat207_contributed_subcell_count", 0)) <= 0:
                reasons.append("secondary_effect_product_contribution_missing")
            if int(receipt.get("host_vapor_sample_count", 0)) <= 0:
                reasons.append("secondary_effect_host_fact_missing")
            if int(receipt.get("retained_mat207_face_count", 0)) <= 0:
                reasons.append("secondary_effect_retained_face_missing")
        except (TypeError, ValueError):
            reasons.append("secondary_effect_diagnostic_counts_invalid")
        return {
            "ok": not reasons,
            "reason": ",".join(reasons),
            "kind": "secondary_effect_product_structural_diagnostic",
            "route_identity": receipt.get("route_identity"),
            "manual_run": False,
            "diagnostic_substitute": False,
            "diagnostic_nonvisual": True,
            "route_tick_exactness_required": False,
            "pixel_evidence": False,
            "performance_evidence": False,
        }

    def receipt_int(key: str, default: int) -> int:
        try:
            return int(receipt.get(key, default))
        except (TypeError, ValueError):
            reasons.append(f"{key}_invalid")
            return default

    if receipt.get("proof") != "moving_current_flight_capture":
        reasons.append("wrong_proof_surface")
    if receipt.get("route_identity") != "server_tick_constant_physical_flight_input":
        reasons.append("wrong_route_identity")
    if receipt.get("constant_flight_input_action") != "move_right":
        reasons.append("wrong_physical_flight_action")
    if receipt_int("turn_intent_frames", 0) <= 0:
        reasons.append("no_turn_intent_frames")
    if receipt.get("airplane_yaw_turn_measured") is not True:
        reasons.append("airplane_yaw_turn_not_measured")
    try:
        if abs(float(receipt.get("airplane_yaw_turn_delta", 0.0))) <= 0.002:
            reasons.append("airplane_yaw_turn_delta_too_small")
    except (TypeError, ValueError):
        reasons.append("airplane_yaw_turn_delta_invalid")
    try:
        if float(receipt.get("first_active_frame_delta", 0.0)) <= 0.05:
            reasons.append("first_active_frame_delta_too_small")
    except (TypeError, ValueError):
        reasons.append("first_active_frame_delta_invalid")
    if receipt_int("camera_turn_input_frames", -1) != 0:
        reasons.append("continuous_camera_input_present")
    if receipt.get("first_active_camera_turn_pressed") is not False:
        reasons.append("camera_input_owner_present")
    camera_yaw_hold_required = (
        receipt.get("camera_view_mode_label") == "true_perspective"
    )
    if camera_yaw_hold_required and receipt.get("camera_yaw_held") is not True:
        reasons.append("camera_yaw_not_held")
    # ---------------------------------------------------------------------
    # FL-4265 / FL-4832 — THE SHIPPING STEERING CONTRACT.
    #
    # WHAT THIS BLOCK USED TO SAY, AND WHY IT WAS WRONG. It demanded
    # abs(first_active_roll_command) > 0.01 and abs(first_active_yaw_command)
    # <= 1e-6: "the route steers on ROLL and never on yaw". That was already
    # false at HEAD. The harness presses move_right; player.gd converts
    # move_right into YAW and hard-codes "roll": 0.0; FL-4832 makes the server
    # DROP any non-zero network roll outright. The old expectation only ever
    # passed because the producer copied the harness constant
    # FL4359_TURN_ROLL_AXIS_ADMITTED into the roll field and published yaw as a
    # literal 0.0. Once the producer started MEASURING, the honest receipt
    # (roll 0.0, yaw -0.35000005364418, axis "yaw") was rejected with
    # first_active_roll_command_missing,unexpected_yaw_command.
    #
    # WHAT IT SAYS NOW. The same shape, inverted onto the axis that ships, and
    # NOT loosened into "anything finite passes". Four things must hold, and
    # each one names a distinct way the receipt could be a lie:
    #   (1) both axes were OBSERVED, not defaulted -- an absent key is a
    #       different receipt from a present zero, and only the present zero is
    #       evidence that roll was looked at;
    #   (2) the roll the client sent is a literal zero -- an invented roll axis
    #       is the original defect and must still fail;
    #   (3) the yaw the client sent is large enough to be a real deflection;
    #   (4) the run's own derived steering axis says "yaw" -- so a receipt that
    #       somehow carried both axes cannot pass as a clean yaw route.
    # ---------------------------------------------------------------------
    if receipt.get("flight_command_observed") is not True:
        reasons.append("flight_command_never_observed")
    if receipt.get("first_active_yaw_command_observed") is not True:
        reasons.append("first_active_yaw_command_not_observed")
    if receipt.get("first_active_roll_command_observed") is not True:
        reasons.append("first_active_roll_command_not_observed")
    if receipt.get("observed_steering_axis") != FL4265_SHIPPING_STEERING_AXIS:
        reasons.append("observed_steering_axis_is_not_yaw")
    client_yaw_command = receipt.get("first_active_yaw_command")
    client_roll_command = receipt.get("first_active_roll_command")
    if not isinstance(client_yaw_command, (int, float)) or not isinstance(
        client_roll_command, (int, float)
    ):
        # null is what the producer publishes when the axis was never seen.
        # It must not be coerced to 0.0 and silently satisfy the roll test.
        reasons.append("first_active_flight_command_invalid")
    else:
        if abs(float(client_roll_command)) > FL4265_MAX_CLIENT_ROLL:
            reasons.append("invented_roll_command")
        if abs(float(client_yaw_command)) <= FL4265_MIN_STEERING_YAW:
            reasons.append("first_active_yaw_command_missing")

    # ---------------------------------------------------------------------
    # THE SERVER-WITNESS BINDING. This is the link that turns "the client says
    # it sent yaw" into "the authority agrees it steered on yaw for this tick,
    # this session, this actor and this airframe".
    #
    # Absence and disagreement are NOT the same verdict. A run whose server
    # emitted no witness line is a client-side observation and is labelled as
    # one; it is rejected only when the caller asked for the binding with
    # --fl4265-require-server-steering-witness. A run whose server DID speak is
    # held to the whole contract unconditionally -- turning the emitter on can
    # never buy a weaker gate.
    # ---------------------------------------------------------------------
    steering_join = receipt.get("server_flight_steering_join")
    steering_join = steering_join if isinstance(steering_join, dict) else {}
    witness_available = bool(steering_join.get("server_steering_witness_available"))
    if getattr(args, "fl4265_require_server_steering_witness", False) and not (
        witness_available
    ):
        reasons.append(
            "server_steering_witness_required_but_absent:"
            + str(steering_join.get("server_steering_witness_absence_reason", "unknown"))
        )
    if witness_available:
        if steering_join.get("server_pilot_kind") != "player":
            # An npc- or none-attributed tick is the flight AI or an idle
            # airframe. Joining that to a human keypress compares two different
            # events, so it fails rather than reads as agreement.
            reasons.append("server_steering_not_attributed_to_a_network_pilot")
        if steering_join.get("server_network_pilot_attributed") is not True:
            reasons.append("server_steering_witness_carried_no_network_session")
        if steering_join.get("client_server_yaw_agrees") is not True:
            reasons.append("client_server_yaw_command_disagreement")
        server_roll = steering_join.get("server_steering_roll_command")
        if not isinstance(server_roll, (int, float)):
            reasons.append("server_steering_roll_command_missing")
        elif abs(float(server_roll)) > FL4265_MAX_CLIENT_ROLL:
            reasons.append("server_steered_on_roll")
        server_drops = steering_join.get("server_network_roll_drops")
        if not isinstance(server_drops, (int, float)):
            reasons.append("server_network_roll_drops_missing")
        elif int(server_drops) != 0:
            # The shipping client sends a literal 0.0 roll every tick. A drop on
            # this route means something other than the shipping client was
            # steering, so the receipt no longer describes the shipping route.
            reasons.append("server_dropped_a_network_roll_on_a_shipping_route")
        if not str(steering_join.get("session_id", "")):
            reasons.append("steering_join_carried_no_session")
        if not str(steering_join.get("actor_key", "")):
            reasons.append("steering_join_carried_no_actor")
        if steering_join.get("controls_airplane") is not True:
            reasons.append("steering_join_actor_did_not_control_an_airplane")
    if args.fl4359_clean_play_no_capture:
        frame_pacing = receipt.get("frame_pacing", {})
        runtime_frame_pacing = (
            frame_pacing.get("runtime", {})
            if isinstance(frame_pacing, dict)
            else {}
        )
        if (
            not isinstance(frame_pacing, dict)
            or frame_pacing.get("uncapped") is not True
        ):
            reasons.append("clean_play_not_uncapped")
        if frame_pacing.get("engine_args") != GODOT_UNCAPPED_ARGS:
            reasons.append("clean_play_uncapped_engine_args_missing")
        if not isinstance(runtime_frame_pacing, dict):
            reasons.append("clean_play_runtime_frame_pacing_missing")
        else:
            if runtime_frame_pacing.get("vsync_disabled") is not True:
                reasons.append("clean_play_runtime_vsync_not_disabled")
            try:
                runtime_max_fps = int(runtime_frame_pacing.get("engine_max_fps", -1))
            except (TypeError, ValueError):
                runtime_max_fps = -1
            if runtime_max_fps != 0:
                reasons.append("clean_play_runtime_max_fps_not_unlimited")
        capture_window = receipt.get("capture_window", {})
        expected_surface_size = {"x": 3600, "y": 2252}
        if (
            args.fl4431_surface_falsifier_profile == "bypass_observe"
            and isinstance(capture_window, dict)
        ):
            expected_surface_size = capture_window.get("window_size", {})
        if not args.fl4431_surface_falsifier_profile:
            if receipt.get("canonical_fullscreen_surface_ready") is not True:
                reasons.append("clean_play_canonical_fullscreen_surface_missing")
        elif receipt.get("surface_falsifier_profile") != (
            args.fl4431_surface_falsifier_profile
        ):
            reasons.append("surface_falsifier_profile_mismatch")
        if receipt.get("canonical_presentation_size") != {"x": 3600, "y": 2252}:
            reasons.append("clean_play_canonical_presentation_size_mismatch")
        if not isinstance(capture_window, dict) or capture_window.get(
            "window_size"
        ) != expected_surface_size:
            reasons.append("clean_play_observed_presentation_size_mismatch")
        if args.fl4431_surface_falsifier_profile:
            if receipt.get("surface_falsifier_ready") is not True:
                reasons.append("surface_falsifier_not_ready")
            if (
                args.fl4431_surface_falsifier_profile == "canonical_3600x2252"
                and receipt.get("surface_falsifier_expected_size")
                != expected_surface_size
            ):
                reasons.append("surface_falsifier_expected_size_mismatch")
            if int(receipt.get("pixel_camera_surface_sample_count", 0)) <= 0:
                reasons.append("surface_falsifier_pixel_camera_samples_missing")
            signature_counts = receipt.get(
                "pixel_camera_surface_signature_counts", {}
            )
            if not isinstance(signature_counts, dict) or len(signature_counts) != 1:
                reasons.append("surface_falsifier_signature_set_invalid")
            else:
                try:
                    signature_sample_count = sum(
                        int(count) for count in signature_counts.values()
                    )
                    pixel_sample_count = int(
                        receipt.get("pixel_camera_surface_sample_count", 0)
                    )
                    timing_sample_count = int(receipt.get("fps_sample_count", 0))
                    signature = json.loads(next(iter(signature_counts)))
                except (TypeError, ValueError, json.JSONDecodeError):
                    reasons.append("surface_falsifier_signature_invalid")
                else:
                    if signature_sample_count != pixel_sample_count:
                        reasons.append("surface_falsifier_signature_count_mismatch")
                    if pixel_sample_count not in {
                        timing_sample_count,
                        timing_sample_count + 1,
                    }:
                        reasons.append("surface_falsifier_timing_coverage_mismatch")
                    expected_presentation = [
                        int(expected_surface_size.get("x", 0)),
                        int(expected_surface_size.get("y", 0)),
                    ]
                    if signature.get("presentation_size") != expected_presentation:
                        reasons.append(
                            "surface_falsifier_presentation_window_mismatch"
                        )
                    if (
                        args.fl4431_surface_falsifier_profile
                        == "canonical_3600x2252"
                        and signature
                        != {
                            "cell_size": [20, 20],
                            "presentation_size": [3600, 2252],
                            "semantic_ansi_size": [180, 113],
                            "target_size": [3600, 2260],
                        }
                    ):
                        reasons.append("surface_falsifier_canonical_signature_mismatch")
            if receipt.get("frame_captured") is not True:
                reasons.append("surface_falsifier_post_timing_frame_missing")
        if args.fl4667_sustained_fire_performance:
            if receipt.get("sustained_fire_requested") is not True:
                reasons.append("sustained_fire_request_missing")
            if receipt.get("sustained_fire_input_action") != "attack_primary":
                reasons.append("sustained_fire_action_mismatch")
            first_intent = receipt.get("first_active_flight_intent", {})
            if not isinstance(first_intent, dict) or first_intent.get("fire") is not True:
                reasons.append("sustained_fire_intent_missing")
            if receipt_int("sustained_fire_projectile_frames", 0) <= 0:
                reasons.append("sustained_fire_projectile_frames_missing")
            if receipt_int("max_live_projectile_count", 0) <= 0:
                reasons.append("sustained_fire_live_projectiles_missing")
        if receipt_int("requested_route_tick_duration", 0) != requested_frames:
            reasons.append("clean_route_tick_duration_mismatch")
        observed_start_tick = receipt_int("clean_route_start_tick", -1)
        start_tick_overshoot = receipt_int("clean_route_start_tick_overshoot", -1)
        if (
            observed_start_tick < 30
            or observed_start_tick > 31
            or start_tick_overshoot != observed_start_tick - 30
        ):
            reasons.append("clean_route_start_tick_mismatch")
        expected_end_tick = observed_start_tick + requested_frames
        if receipt_int("clean_route_end_tick", -1) != expected_end_tick:
            reasons.append("clean_route_end_tick_mismatch")
        observed_end_tick = receipt_int("server_route_end_tick_observed", -1)
        end_tick_overshoot = receipt_int("clean_route_end_tick_overshoot", -1)
        if (
            observed_end_tick < expected_end_tick
            or observed_end_tick > expected_end_tick + 1
            or end_tick_overshoot != observed_end_tick - expected_end_tick
        ):
            reasons.append("clean_route_end_tick_not_observed")
        if receipt_int("clean_play_measured_frame_count", 0) <= 0:
            reasons.append("clean_play_frame_count_missing")
    if args.fl4359_jitter_capture and jitter_frame_limit <= 0:
        reasons.append("jitter_capture_unbounded")
    expected_visual_ticks = []
    if args.fl4359_visual_capture_stride > 0 and args.fl4359_visual_capture_limit > 0:
        expected_visual_ticks = [
            30 + max(0, args.fl4359_visual_capture_start_frame)
            + index * args.fl4359_visual_capture_stride
            for index in range(args.fl4359_visual_capture_limit)
        ]
    receipt_visual_ticks = receipt.get("visual_route_target_ticks", [])
    if receipt_visual_ticks != expected_visual_ticks:
        reasons.append("visual_route_target_set_mismatch")
    if receipt.get("missing_visual_route_ticks") not in ([], None):
        reasons.append("visual_route_tick_missing")
    if receipt.get("visual_route_ticks_exact") is not True and expected_visual_ticks:
        reasons.append("visual_route_ticks_not_exact")
    visual_samples = receipt.get("visual_frame_samples", [])
    sample_route_ticks: list[int] = []
    sample_route_ticks_valid = isinstance(visual_samples, list)
    if sample_route_ticks_valid:
        try:
            sample_route_ticks = [
                int(sample["route_tick"])
                for sample in visual_samples
                if isinstance(sample, dict)
            ]
        except (TypeError, ValueError):
            sample_route_ticks_valid = False
    if sample_route_ticks != expected_visual_ticks:
        reasons.append("visual_sample_route_ticks_mismatch")
    if len(sample_route_ticks) != len(set(sample_route_ticks)):
        reasons.append("visual_sample_route_tick_duplicate")
    if sample_route_ticks_valid:
        for sample in visual_samples:
            if not isinstance(sample, dict):
                reasons.append("visual_sample_invalid")
                continue
            try:
                if int(sample["authority_server_tick"]) < 0:
                    reasons.append("visual_sample_server_tick_missing")
            except (KeyError, TypeError, ValueError):
                reasons.append("visual_sample_authority_fields_invalid")
    return {
        "ok": not reasons,
        "reason": ",".join(reasons),
        "kind": "server_tick_constant_physical_flight_input",
        "route_identity": receipt.get("route_identity"),
        "manual_run": False,
        "diagnostic_substitute": False,
        "server_tick_route_required": True,
        "camera_turn_required": False,
        "camera_yaw_turn_measured": receipt.get("camera_yaw_turn_measured"),
        "camera_yaw_turn_delta": receipt.get("camera_yaw_turn_delta"),
        "camera_yaw_hold_required": camera_yaw_hold_required,
        "airplane_yaw_turn_measured": receipt.get("airplane_yaw_turn_measured"),
        "airplane_yaw_turn_delta": receipt.get("airplane_yaw_turn_delta"),
        "first_active_frame_delta": receipt.get("first_active_frame_delta"),
        "first_active_camera_turn_pressed": receipt.get("first_active_camera_turn_pressed"),
        "first_active_flight_intent": receipt.get("first_active_flight_intent"),
        "first_active_throttle_command": receipt.get("first_active_throttle_command"),
        "first_active_pitch_command": receipt.get("first_active_pitch_command"),
        "first_active_roll_command": receipt.get("first_active_roll_command"),
        "first_active_yaw_command": receipt.get("first_active_yaw_command"),
        # FL-4265/FL-4832: what the run actually steered on, and whether the
        # SERVER corroborated it for the same tick/session/actor/airframe.
        "observed_steering_axis": receipt.get("observed_steering_axis"),
        "flight_authority_binding_stage": receipt.get(
            "flight_authority_binding_stage"
        ),
        "server_steering_witness_available": receipt.get(
            "server_steering_witness_available"
        ),
        "server_steering_witness_absence_reason": receipt.get(
            "server_steering_witness_absence_reason"
        ),
        "server_flight_steering_join": receipt.get("server_flight_steering_join"),
        "bounded_duration_required": True,
        "requested_moving_frames": requested_frames,
        "effective_jitter_frame_limit": jitter_frame_limit if args.fl4359_jitter_capture else 0,
        "effective_world_sample_stride": max(1, args.fl4359_jitter_capture_world_sample_stride),
        "visual_capture_start_frame": max(0, args.fl4359_visual_capture_start_frame),
        "visual_capture_stride": max(0, args.fl4359_visual_capture_stride),
        "visual_route_target_ticks": expected_visual_ticks,
        "sample_route_ticks": sample_route_ticks,
        "performance_evidence": args.fl4359_clean_play_no_capture,
        "frame_pacing": receipt.get("frame_pacing"),
        "sustained_fire_required": args.fl4667_sustained_fire_performance,
        "sustained_fire_projectile_frames": receipt.get(
            "sustained_fire_projectile_frames"
        ),
        "max_live_projectile_count": receipt.get("max_live_projectile_count"),
    }


if __name__ == "__main__":
    sys.exit(main())
