#!/usr/bin/env python3
# =============================================================================
# test_fl4265_steering_contract_validator.py
# =============================================================================
#
# WHAT THIS FILE CHECKS, AND WHY ANYONE SHOULD CARE
# -------------------------------------------------
# The FL-4265 flight proof answers one question: "when a person pressed D in the
# running game, did the aeroplane turn because of THAT, and does the authority
# agree?" A receipt is the written answer. A validator is the thing that decides
# whether a receipt is allowed to count.
#
# For a long time the receipt was not a measurement at all. The producer copied a
# constant named FL4359_TURN_ROLL_AXIS_ADMITTED into the field "how much roll did
# the client send" and wrote a literal zero into "how much yaw did the client
# send". Those two numbers therefore read identically whether the key was pressed
# or not, which is the definition of evidence that cannot falsify anything. The
# producer was repaired: it now reads the exact message the client handed to the
# network, one axis at a time, with no cross-axis fallback.
#
# That repair made the VALIDATOR wrong instead. It still demanded a large roll
# and a zero yaw -- the shape of the old invented answer. The shipping client
# does the opposite: pressing D produces YAW, roll is hard-coded to zero, and the
# server now drops any non-zero roll that arrives over the network. So the honest
# receipt was refused with "first_active_roll_command_missing" and
# "unexpected_yaw_command". A run that measures the truth and is then rejected by
# its own front door is worse than no gate at all, because it teaches the next
# person to loosen the gate.
#
# This file pins the repaired contract. Every check below asks the validator a
# question whose answer must differ between an honest receipt and a dishonest
# one. If any check stops discriminating, the gate has gone slack and this file
# fails.
#
# HOW TO READ A CHECK. Each one builds a receipt dictionary, hands it to the real
# validator, and looks at the list of refusal reasons that comes back. The
# receipts here are deliberately minimal: the validator has many other rules
# (screen size, frame pacing, camera hold) that these fixtures do not satisfy and
# are not about. So a check never asserts "the receipt passed"; it asserts that
# the SPECIFIC reason it cares about is present or absent. That keeps this file
# from breaking every time an unrelated rule is added elsewhere.
#
# HOW TO RUN IT
#   python3 scripts/test_fl4265_steering_contract_validator.py

import argparse
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The proof runner is a script, not an installed package, so it is loaded by
# path. Loading it does not start a server or a game: everything at module level
# is a definition.
_spec = importlib.util.spec_from_file_location(
    "fl4265_proof_runner", ROOT / "scripts" / "run_fl4265_airplane_proof.py"
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

# The measured values from the executed headed run of 2026-09-13: session
# NXR1ACJpS pressing the D key, whose wire message carried yaw -0.35000005364418
# and roll 0.0. They are quoted rather than rounded so that a validator which
# starts comparing rounded numbers is caught.
MEASURED_YAW = -0.35000005364418
MEASURED_SESSION = "NXR1ACJpS"
MEASURED_ACTOR = "player_NXR1ACJpS"
MEASURED_PICKUP = "airplane_red"
MEASURED_ROUTE_TICK = 30


def _args(require_witness: bool = False) -> argparse.Namespace:
    """The command-line options the validator reads, with nothing switched on.

    Only the two fields the steering rules touch matter here. The rest of the
    validator's option surface is exercised by its own callers.
    """
    return argparse.Namespace(
        fl4265_require_server_steering_witness=require_witness,
        fl4359_clean_play_frames=0,
        fl4359_clean_play_no_capture=False,
        fl4359_jitter_capture=False,
        fl4359_jitter_capture_frame_limit=0,
        fl4359_jitter_capture_world_sample_stride=0,
        fl4359_jitter_hold_frames=0,
        fl4359_visual_capture_limit=0,
        fl4359_visual_capture_start_frame=0,
        fl4359_visual_capture_stride=0,
        fl4431_secondary_effect_diagnostic_only=False,
        fl4431_surface_falsifier_profile="",
        fl4667_sustained_fire_performance=False,
    )


def _honest_receipt(**overrides) -> dict:
    """A receipt shaped like the one the repaired producer actually wrote."""
    receipt = {
        "proof": "moving_current_flight_capture",
        "route_identity": "server_tick_constant_physical_flight_input",
        "constant_flight_input_action": "move_right",
        "turn_intent_frames": 72,
        "airplane_yaw_turn_measured": True,
        "airplane_yaw_turn_delta": -0.5424,
        "first_active_frame_delta": 0.4,
        "camera_turn_input_frames": 0,
        "first_active_camera_turn_pressed": False,
        "camera_view_mode_label": "orthographic",
        # The measured steering rows.
        "flight_command_observed": True,
        "first_active_yaw_command": MEASURED_YAW,
        "first_active_yaw_command_observed": True,
        "first_active_roll_command": 0.0,
        "first_active_roll_command_observed": True,
        "first_active_pitch_command": 0.0,
        "first_active_throttle_command": 0.0,
        "observed_steering_axis": "yaw",
    }
    receipt.update(overrides)
    return receipt


def _reasons(receipt: dict, require_witness: bool = False) -> list[str]:
    verdict = runner._fl4359_front_door_contract(_args(require_witness), receipt)
    return [r for r in str(verdict.get("reason", "")).split(",") if r]


def _join(**overrides) -> dict:
    """A server-witness join that agrees with the honest receipt."""
    join = {
        "server_steering_witness_available": True,
        "server_steering_witness_absence_reason": "",
        "server_pilot_kind": "player",
        "server_network_pilot_attributed": True,
        "server_steering_yaw_command": MEASURED_YAW,
        "server_steering_roll_command": 0.0,
        "server_network_roll_drops": 0,
        "client_server_yaw_agrees": True,
        "client_server_yaw_difference": 0.0,
        "session_id": MEASURED_SESSION,
        "actor_key": MEASURED_ACTOR,
        "pickup_id": MEASURED_PICKUP,
        "controls_airplane": True,
    }
    join.update(overrides)
    return join


# =============================================================================
# 1. The honest receipt is ACCEPTED on the steering rules.
# =============================================================================

def test_measured_yaw_receipt_raises_no_steering_objection():
    reasons = _reasons(_honest_receipt())
    for stale in ("first_active_roll_command_missing", "unexpected_yaw_command"):
        assert stale not in reasons, f"the retired roll expectation is still live: {stale}"
    for objection in (
        "flight_command_never_observed",
        "observed_steering_axis_is_not_yaw",
        "invented_roll_command",
        "first_active_yaw_command_missing",
        "first_active_flight_command_invalid",
        "first_active_yaw_command_not_observed",
        "first_active_roll_command_not_observed",
    ):
        assert objection not in reasons, f"honest receipt refused with {objection}: {reasons}"


# =============================================================================
# 2. It still REJECTS an invented roll axis. This is the original defect.
# =============================================================================

def test_invented_roll_is_rejected():
    # Exactly the number the old constant published: 0.65.
    reasons = _reasons(_honest_receipt(
        first_active_roll_command=0.65, observed_steering_axis="roll_and_yaw"))
    assert "invented_roll_command" in reasons, reasons


def test_a_roll_only_receipt_is_rejected_even_with_the_axis_field_agreeing():
    # A self-consistent lie: roll present, yaw zero, axis says "roll". The old
    # validator ACCEPTED precisely this. It must now fail twice over.
    reasons = _reasons(_honest_receipt(
        first_active_roll_command=0.65,
        first_active_yaw_command=0.0,
        observed_steering_axis="roll",
    ))
    assert "invented_roll_command" in reasons, reasons
    assert "observed_steering_axis_is_not_yaw" in reasons, reasons
    assert "first_active_yaw_command_missing" in reasons, reasons


# =============================================================================
# 3. It still REJECTS a receipt with no observed steering.
# =============================================================================

def test_unobserved_axes_are_rejected():
    # This is the falsifier run: the route never reached an observation frame,
    # so both axes are null and each carries an absence reason.
    reasons = _reasons(_honest_receipt(
        flight_command_observed=False,
        first_active_yaw_command=None,
        first_active_yaw_command_observed=False,
        first_active_roll_command=None,
        first_active_roll_command_observed=False,
        observed_steering_axis="none",
    ))
    assert "flight_command_never_observed" in reasons, reasons
    assert "first_active_yaw_command_not_observed" in reasons, reasons
    assert "first_active_roll_command_not_observed" in reasons, reasons
    assert "first_active_flight_command_invalid" in reasons, reasons
    assert "observed_steering_axis_is_not_yaw" in reasons, reasons


def test_a_null_axis_is_not_quietly_read_as_zero():
    # The dangerous shortcut: float(None) fails, a default of 0.0 would make a
    # NEVER-OBSERVED roll indistinguishable from an OBSERVED zero roll, which is
    # the exact collapse the producer repair removed.
    reasons = _reasons(_honest_receipt(
        first_active_roll_command=None, first_active_roll_command_observed=False))
    assert "first_active_flight_command_invalid" in reasons, reasons
    assert "first_active_roll_command_not_observed" in reasons, reasons


def test_a_yaw_too_small_to_be_a_deflection_is_rejected():
    reasons = _reasons(_honest_receipt(
        first_active_yaw_command=0.0001, observed_steering_axis="yaw"))
    assert "first_active_yaw_command_missing" in reasons, reasons


# =============================================================================
# 4. The server-witness binding.
# =============================================================================

def test_an_agreeing_server_witness_raises_no_objection():
    reasons = _reasons(_honest_receipt(server_flight_steering_join=_join()))
    for objection in (
        "client_server_yaw_command_disagreement",
        "server_steered_on_roll",
        "server_steering_not_attributed_to_a_network_pilot",
        "server_dropped_a_network_roll_on_a_shipping_route",
        "steering_join_carried_no_session",
        "steering_join_carried_no_actor",
        "steering_join_actor_did_not_control_an_airplane",
    ):
        assert objection not in reasons, f"agreeing witness refused with {objection}: {reasons}"


def test_server_client_axis_disagreement_is_rejected():
    # The server says it steered on a different yaw than the client says it
    # sent. Something between the key and the simulation rewrote the command.
    reasons = _reasons(_honest_receipt(server_flight_steering_join=_join(
        server_steering_yaw_command=-0.9, client_server_yaw_agrees=False,
        client_server_yaw_difference=0.55)))
    assert "client_server_yaw_command_disagreement" in reasons, reasons


def test_a_server_that_steered_on_roll_is_rejected():
    reasons = _reasons(_honest_receipt(server_flight_steering_join=_join(
        server_steering_roll_command=0.35)))
    assert "server_steered_on_roll" in reasons, reasons


def test_a_dropped_network_roll_disqualifies_a_shipping_route():
    # The shipping client sends a literal 0.0 roll on every tick, so a drop
    # means something else was flying and the receipt is about a different
    # client than the one it claims to describe.
    reasons = _reasons(_honest_receipt(server_flight_steering_join=_join(
        server_network_roll_drops=1)))
    assert "server_dropped_a_network_roll_on_a_shipping_route" in reasons, reasons


def test_a_bot_attributed_witness_is_not_human_evidence():
    reasons = _reasons(_honest_receipt(server_flight_steering_join=_join(
        server_pilot_kind="npc", server_network_pilot_attributed=False)))
    assert "server_steering_not_attributed_to_a_network_pilot" in reasons, reasons
    assert "server_steering_witness_carried_no_network_session" in reasons, reasons


def test_a_witness_missing_its_session_or_actor_is_rejected():
    reasons = _reasons(_honest_receipt(server_flight_steering_join=_join(
        session_id="", actor_key="", controls_airplane=False)))
    assert "steering_join_carried_no_session" in reasons, reasons
    assert "steering_join_carried_no_actor" in reasons, reasons
    assert "steering_join_actor_did_not_control_an_airplane" in reasons, reasons


def test_absence_is_reported_as_absence_not_as_agreement():
    # No witness line at all. By default that is a client-side observation and
    # raises no server objection -- but it must not silently read as agreement
    # either, so none of the agreement reasons appear and the caller can demand
    # the binding with a flag.
    missing = {
        "server_steering_witness_available": False,
        "server_steering_witness_absence_reason":
            "server_emitted_no_flight_steering_witness_line",
    }
    relaxed = _reasons(_honest_receipt(server_flight_steering_join=missing))
    assert not any(r.startswith("server_steering_witness_required") for r in relaxed), relaxed
    assert "client_server_yaw_command_disagreement" not in relaxed, relaxed

    strict = _reasons(_honest_receipt(server_flight_steering_join=missing),
                      require_witness=True)
    assert any(r.startswith("server_steering_witness_required_but_absent") for r in strict), strict


def test_the_witness_gate_cannot_be_loosened_by_turning_the_emitter_on():
    # A run whose server DID speak is held to the whole contract whether or not
    # the caller asked for it. Otherwise emitting a witness would be a way to
    # buy a weaker gate.
    bad = _join(server_steering_roll_command=0.35)
    assert "server_steered_on_roll" in _reasons(
        _honest_receipt(server_flight_steering_join=bad), require_witness=False)
    assert "server_steered_on_roll" in _reasons(
        _honest_receipt(server_flight_steering_join=bad), require_witness=True)


# =============================================================================
# 5. The join itself: it must bind the WHOLE chain or report that it could not.
# =============================================================================

def _witness(**overrides) -> dict:
    row = {
        "schema": runner.FL4265_STEERING_WITNESS_SCHEMA,
        "tick": MEASURED_ROUTE_TICK,
        "pickupId": MEASURED_PICKUP,
        "pilotActorId": MEASURED_ACTOR,
        "pilotSessionId": MEASURED_SESSION,
        "steeringPilotKind": "player",
        "steeringYawCommand": MEASURED_YAW,
        "steeringPitchCommand": 0.0,
        "steeringRollCommand": 0.0,
        "networkRollDrops": 0,
        "networkPilotAttributed": True,
    }
    row.update(overrides)
    return row


def _receipt_with_identity(**overrides) -> dict:
    receipt = _honest_receipt(**overrides)
    receipt["flight_command_observation_route_tick"] = MEASURED_ROUTE_TICK
    receipt["flight_command_observation"] = {
        "input_event": {"action": "move_right", "physical_keycode": 68, "device": 0},
        "control_identity": {
            "session_id": MEASURED_SESSION,
            "actor_key": MEASURED_ACTOR,
            "local_airplane_pickup_id": MEASURED_PICKUP,
            "controls_airplane": True,
        },
    }
    return receipt


def test_the_join_binds_key_to_intent_to_session_to_actor_to_mount_to_server():
    join = runner._fl4265_steering_witness_join(
        _receipt_with_identity(), [_witness()])
    assert join["server_steering_witness_available"] is True, join
    # Every link of the chain the receipt must carry.
    assert join["input_action"] == "move_right"
    assert join["input_physical_keycode"] == 68
    assert join["client_yaw_command"] == MEASURED_YAW
    assert join["session_id"] == MEASURED_SESSION
    assert join["actor_key"] == MEASURED_ACTOR
    assert join["pickup_id"] == MEASURED_PICKUP
    assert join["controls_airplane"] is True
    assert join["server_pilot_kind"] == "player"
    assert join["server_steering_yaw_command"] == MEASURED_YAW
    assert join["server_steering_roll_command"] == 0.0
    assert join["server_network_roll_drops"] == 0
    assert join["client_server_yaw_agrees"] is True
    assert join["airframe_yaw_delta"] == -0.5424


def test_the_join_refuses_a_witness_belonging_to_another_session():
    join = runner._fl4265_steering_witness_join(
        _receipt_with_identity(), [_witness(pilotSessionId="somebody_else")])
    assert join["server_steering_witness_available"] is False
    assert join["server_steering_witness_absence_reason"] == (
        "no_witness_line_matched_this_session_actor_and_airframe"), join


def test_the_join_refuses_a_witness_belonging_to_another_actor_or_airframe():
    for bad in ({"pilotActorId": "player_other"}, {"pickupId": "airplane_nausicaa"}):
        join = runner._fl4265_steering_witness_join(
            _receipt_with_identity(), [_witness(**bad)])
        assert join["server_steering_witness_available"] is False, (bad, join)


def test_the_join_refuses_a_witness_from_another_tick():
    # "The server agreed, just not about this moment" is not agreement.
    join = runner._fl4265_steering_witness_join(
        _receipt_with_identity(), [_witness(tick=MEASURED_ROUTE_TICK + 5)])
    assert join["server_steering_witness_available"] is False
    assert join["server_steering_witness_absence_reason"] == (
        "no_witness_line_carried_the_observed_route_tick"), join


def test_the_join_picks_the_right_tick_out_of_a_stream():
    stream = [_witness(tick=t, steeringYawCommand=float(t)) for t in range(28, 34)]
    stream.append(_witness())
    join = runner._fl4265_steering_witness_join(_receipt_with_identity(), stream)
    assert join["server_steering_witness_available"] is True, join
    assert join["matched_witness"]["tick"] == MEASURED_ROUTE_TICK
    # The first matching line for that tick is the one from the range() stream,
    # so a validator reading it will see the DISAGREEING yaw and refuse. That is
    # the point: the join reports what is there, it does not shop for agreement.
    assert join["client_server_yaw_agrees"] is False, join


def test_an_empty_log_is_an_absence_with_a_reason():
    join = runner._fl4265_steering_witness_join(_receipt_with_identity(), [])
    assert join["server_steering_witness_available"] is False
    assert join["server_steering_witness_absence_reason"] == (
        "server_emitted_no_flight_steering_witness_line")
    assert join["witness_line_count"] == 0


def test_a_receipt_without_an_identity_cannot_be_joined():
    receipt = _honest_receipt()
    join = runner._fl4265_steering_witness_join(receipt, [_witness()])
    assert join["server_steering_witness_available"] is False
    assert join["server_steering_witness_absence_reason"] == (
        "receipt_carried_no_session_or_actor_to_join_on"), join


def test_the_log_reader_skips_noise_and_keeps_witnesses(tmp_path=None):
    import json as _json
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "colyseus_server.log"
        log.write_text("\n".join([
            "ASCIICKER_COLYSEUS_SPIKE_LISTENING port=24999",
            "AIRPLANE_DAMAGE_EVENT {\"tick\": 3}",
            runner.AIRPLANE_STEERING_WITNESS_PREFIX + "not json",
            runner.AIRPLANE_STEERING_WITNESS_PREFIX + _json.dumps(
                {"schema": "some.other.v1"}),
            runner.AIRPLANE_STEERING_WITNESS_PREFIX + _json.dumps(_witness()),
        ]))
        rows = runner._read_flight_steering_witnesses(log)
    assert len(rows) == 1, rows
    assert rows[0]["pilotSessionId"] == MEASURED_SESSION


if __name__ == "__main__":
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_")):
        try:
            globals()[name]()
        except Exception as error:  # noqa: BLE001 - a report, not a handler
            failures += 1
            print(f"FAIL {name}: {error}")
        else:
            print(f"ok   {name}")
    print(f"{'FAILED' if failures else 'passed'}: {failures} failing check(s)")
    sys.exit(1 if failures else 0)
