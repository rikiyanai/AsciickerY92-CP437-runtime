#!/usr/bin/env python3
"""Guard the deletion of the retired FL-4431 airborne route latch.

The server still owns the airborne spawn and airplane simulation. This contract
keeps proof-only route timing from returning inside that gameplay path.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "colyseus_server/src/sim/world.ts"
ROOM = ROOT / "colyseus_server/src/rooms/AuthoritativeRoom.ts"
STATE = ROOT / "colyseus_server/src/rooms/schema/WorldState.ts"
SERVER_TEST = ROOT / "colyseus_server/test/world.test.mjs"
AIRPLANE_RUNNER = ROOT / "scripts/run_fl4265_airplane_proof.py"
GDSCRIPT_DAMAGE_DRIVER = (
    ROOT / "godot_project/tools/proofs/run_fl4265_airplane_dogfight_proof.gd"
)
NIGHT_TUPLE_TEST = (
    ROOT / "godot_project/scripts/tests/test_fl4431_night_null_product_tuple.gd"
)
RETIRED_CONSUMERS = (
    GDSCRIPT_DAMAGE_DRIVER,
    ROOT / "scripts/run_fl4265_airplane_proof.py",
    ROOT / "scripts/run_fl4359_headed_movement.py",
    ROOT / "scripts/run_fl4359_acceptance_package.py",
    ROOT / "scripts/fl4408_actual_godot_capture_adapter.py",
    ROOT / "scripts/build_fl4359_sunday_current_review_grid.py",
    ROOT / "scripts/build_godot_canonical_png_grid.py",
    ROOT / "scripts/test_fl4359_acceptance_package.py",
    ROOT / "scripts/test_fl4359_moving_camera_analyzer.py",
    ROOT / "scripts/test_fl4359_review_grid_builder.py",
    ROOT / "scripts/test_godot_canonical_png_grid_runner.py",
    NIGHT_TUPLE_TEST,
)

RETIRED_SYMBOLS = (
    "FLIGHT_INTENT_ACTIVITY_EPSILON",
    "flightIntentHasActivity",
    "awaitingFirstPilotIntent",
    "AppliedFlightIntentReceipt",
    "appliedFlightIntentReceipt",
    "pendingFlightRouteStart",
    "flightRouteStartCapturedByActor",
    "flight_intent_epoch",
    "last_applied_flight_intent_",
    "last_applied_flight_route_start_",
)
RETIRED_BASELINE_V2_CONSUMERS = (
    ROOT / "scripts/run_fl4359_acceptance_package.py",
    ROOT / "scripts/test_fl4359_acceptance_package.py",
)
RETIRED_BASELINE_V2_SYMBOLS = (
    "BASELINE_RECEIPT_V2",
    "build_fl4431_baseline_receipt_v2",
    "--baseline-receipt",
    "baseline_receipt_identity",
    "visual_baseline_arm_ok",
    "run_clean_play_comparison",
    "damage_pair_comparison",
)
RETIRED_SUNDAY_COMPARISON_CONSUMERS = (
    AIRPLANE_RUNNER,
    ROOT / "scripts/run_fl4359_acceptance_package.py",
    ROOT / "scripts/test_fl4359_acceptance_package.py",
    ROOT / "scripts/test_fl4359_moving_camera_analyzer.py",
)
RETIRED_SUNDAY_COMPARISON_SYMBOLS = (
    "--sunday-summary-json",
    "sunday_summary_json",
    "build_sunday_comparison",
    "current_vs_sunday",
    "test_sunday_comparison",
)
RETIRED_DAMAGE_COMPARISON_SYMBOLS = (
    "faa36357eef9dee7e29b9333cb4c1397ca27b929",
    "Baseline A",
    "--expected-red-gate",
    "FL4431_DAMAGE_BASELINE_COMMIT",
    "FL4431_DAMAGE_BASELINE_PROFILE",
    "_damage_renderer_profile",
    "FL4431_DAMAGE_PROFILE_ARG",
    "--fl4431-damage-renderer-profile",
    "damage_renderer_profile_not_source_bound",
)
RETIRED_GDSCRIPT_COMPARISON_SYMBOLS = (
    "fl4408_capture_side",
    "--fl4408-capture-side=",
    "FL4431_DAMAGE_BASELINE_PROFILE",
    "FL4431_DAMAGE_BASELINE_ABSENCE_REASON",
    "FL4431_DAMAGE_BASELINE_STYLE",
    "fl4431_damage_renderer_profile",
    "--fl4431-damage-renderer-profile=",
    "_fl4431_baseline_damage_vapor_runtime_evidence",
    "_arm_fl4431_night_product_receipt",
)
CURRENT_DAMAGE_ROUTE_PINS = (
    (
        'FL4431_DAMAGE_CURRENT_PROFILE = '
        '"current_retained_vsb_product_b62_mat211_v1"'
    ),
    "cmd.append(FL4431_SECONDARY_EFFECT_EVIDENCE_ARG)",
    "product_mat211_contribution_receipt_available",
    "product_mat211_contributed_subcell_count",
    "product_mat211_contribution_bit",
)
CURRENT_GDSCRIPT_DAMAGE_PINS = (
    (
        'const FL4431_DAMAGE_CURRENT_PROFILE := '
        '"current_retained_vsb_product_b62_mat211_v1"'
    ),
    "var vapor_evidence := _fl4431_damage_vapor_runtime_evidence(ascii_effect)",
    '"retained_mat211_face_count"',
    '"product_mat211_contribution_receipt_available"',
    '"product_mat211_contributed_subcell_count"',
    '"product_mat211_contribution_bit"',
    '"product_mat211_style"',
)
CURRENT_NIGHT_TUPLE_PINS = (
    "current-source FL-4431 temporal receipt",
    "var current_samples := _make_samples(1)",
    "current accepted null tuple member",
    "current accepted missing tuple member",
    "current accepted invalid tuple member",
)


def _sources() -> dict[Path, str]:
    return {
        WORLD: WORLD.read_text(encoding="utf-8"),
        ROOM: ROOM.read_text(encoding="utf-8"),
        STATE: STATE.read_text(encoding="utf-8"),
        SERVER_TEST: SERVER_TEST.read_text(encoding="utf-8"),
        **{
            path: path.read_text(encoding="utf-8")
            for path in RETIRED_CONSUMERS
        },
    }


def _contract(sources: dict[Path, str]) -> list[str]:
    errors: list[str] = []
    for path, source in sources.items():
        for symbol in RETIRED_SYMBOLS:
            if symbol in source:
                errors.append(f"{path.relative_to(ROOT)} resurrected {symbol}")
    for path in RETIRED_BASELINE_V2_CONSUMERS:
        source = sources[path]
        for symbol in RETIRED_BASELINE_V2_SYMBOLS:
            if symbol in source:
                errors.append(
                    f"{path.relative_to(ROOT)} resurrected retired Baseline-v2 owner {symbol}"
                )
    for path in RETIRED_SUNDAY_COMPARISON_CONSUMERS:
        source = sources[path]
        for symbol in RETIRED_SUNDAY_COMPARISON_SYMBOLS:
            if symbol in source:
                errors.append(
                    f"{path.relative_to(ROOT)} resurrected retired Sunday comparison owner {symbol}"
                )
    airplane_runner = sources[AIRPLANE_RUNNER]
    for symbol in RETIRED_DAMAGE_COMPARISON_SYMBOLS:
        if symbol in airplane_runner:
            errors.append(
                f"{AIRPLANE_RUNNER.relative_to(ROOT)} resurrected retired damage comparison owner {symbol}"
            )
    for pin in CURRENT_DAMAGE_ROUTE_PINS:
        if pin not in airplane_runner:
            errors.append(
                f"{AIRPLANE_RUNNER.relative_to(ROOT)} lost current damage route seam: {pin}"
            )
    gdscript_driver = sources[GDSCRIPT_DAMAGE_DRIVER]
    night_tuple_test = sources[NIGHT_TUPLE_TEST]
    for symbol in RETIRED_GDSCRIPT_COMPARISON_SYMBOLS:
        for path, source in (
            (GDSCRIPT_DAMAGE_DRIVER, gdscript_driver),
            (NIGHT_TUPLE_TEST, night_tuple_test),
        ):
            if symbol in source:
                errors.append(
                    f"{path.relative_to(ROOT)} resurrected retired GDScript comparison owner {symbol}"
                )
    for pin in CURRENT_GDSCRIPT_DAMAGE_PINS:
        if pin not in gdscript_driver:
            errors.append(
                f"{GDSCRIPT_DAMAGE_DRIVER.relative_to(ROOT)} lost current Product evidence seam: {pin}"
            )
    for pin in CURRENT_NIGHT_TUPLE_PINS:
        if pin not in night_tuple_test:
            errors.append(
                f"{NIGHT_TUPLE_TEST.relative_to(ROOT)} lost current tuple law: {pin}"
            )

    world = sources[WORLD]
    room = sources[ROOM]
    # These positive pins protect the actual gameplay owner while the negative
    # checks delete the route-proof owner. A green deletion test must not pass
    # after somebody removes airborne spawning together with the old latch.
    required_world = (
        'process.env.AIRPLANE_SPAWN_AIRBORNE === "1"',
        "const admittedPilotIntent = pilot ? world.flightIntents.get(pilot.actorId) : undefined;",
        "const intent: FlightIntent = admittedPilotIntent ?? {",
        "throttle: pilot ? 0 : -1",
    )
    for text in required_world:
        if text not in world:
            errors.append(f"{WORLD.relative_to(ROOT)} lost gameplay seam: {text}")

    required_room = (
        "if (airplaneAirborneSpawnConfig().enabled && spawnIndex === 0)",
        "actor.mountPickupId = AIRPLANE_PICKUP_ID;",
        "actor.position.x = plane.position.x;",
        "const result = applyInput(this.world, client.sessionId, actorId, payload);",
    )
    for text in required_room:
        if text not in room:
            errors.append(f"{ROOM.relative_to(ROOT)} lost gameplay seam: {text}")
    return errors


def test_airborne_route_latch_and_receipt_are_deleted() -> None:
    assert _contract(_sources()) == []


def test_contract_rejects_resurrected_gameplay_latch() -> None:
    sources = _sources()
    sources[WORLD] += "\ninterface Mutant { awaitingFirstPilotIntent: boolean; }\n"
    assert any("awaitingFirstPilotIntent" in error for error in _contract(sources))


def test_contract_rejects_resurrected_route_receipt() -> None:
    sources = _sources()
    sources[STATE] += "\ndeclare last_applied_flight_route_start_valid: boolean;\n"
    assert any("last_applied_flight_route_start_" in error for error in _contract(sources))


def test_contract_rejects_resurrected_consumer_receipt_field() -> None:
    # A former reader is also part of the deletion boundary. Requiring a field
    # the live server no longer publishes would turn retired proof metadata
    # back into a fabricated runtime prerequisite.
    sources = _sources()
    sources[RETIRED_CONSUMERS[0]] += '\n_RETIRED_RECEIPT_FIELD = "flight_intent_epoch"\n'
    assert any("flight_intent_epoch" in error for error in _contract(sources))


def test_contract_rejects_resurrected_baseline_v2_package_owner() -> None:
    sources = _sources()
    sources[RETIRED_BASELINE_V2_CONSUMERS[0]] += (
        '\nRETIRED_FLAG = "--baseline-receipt"\n'
    )
    assert any("--baseline-receipt" in error for error in _contract(sources))


def test_contract_rejects_resurrected_sunday_comparison_owner() -> None:
    sources = _sources()
    sources[AIRPLANE_RUNNER] += (
        '\nRETIRED_FLAG = "--sunday-summary-json"\n'
    )
    assert any(
        "--sunday-summary-json" in error for error in _contract(sources)
    )


def test_contract_rejects_resurrected_damage_profile_by_head() -> None:
    sources = _sources()
    sources[AIRPLANE_RUNNER] += (
        "# Baseline A profile owner\n"
        "\ndef _damage_renderer_profile(client_identity):\n"
        '    return "faa36357eef9dee7e29b9333cb4c1397ca27b929"\n'
    )
    errors = _contract(sources)
    assert any("_damage_renderer_profile" in error for error in errors)
    assert any("faa36357e" in error for error in errors)
    assert any("Baseline A" in error for error in errors)


def test_contract_rejects_resurrected_runner_expected_red_control() -> None:
    sources = _sources()
    sources[AIRPLANE_RUNNER] += '\nRETIRED_FLAG = "--expected-red-gate"\n'
    assert any(
        "--expected-red-gate" in error for error in _contract(sources)
    )


def test_contract_rejects_resurrected_gdscript_capture_side_owner() -> None:
    sources = _sources()
    sources[GDSCRIPT_DAMAGE_DRIVER] += (
        '\nvar fl4408_capture_side := "baseline"\n'
    )
    assert any(
        "fl4408_capture_side" in error
        for error in _contract(sources)
    )


def test_contract_rejects_resurrected_gdscript_baseline_profile_owner() -> None:
    sources = _sources()
    sources[GDSCRIPT_DAMAGE_DRIVER] += (
        '\nconst FL4431_DAMAGE_BASELINE_PROFILE := "retired"\n'
        'func _fl4431_baseline_damage_vapor_runtime_evidence() -> Dictionary:\n'
        '\treturn {}\n'
    )
    errors = _contract(sources)
    assert any("FL4431_DAMAGE_BASELINE_PROFILE" in error for error in errors)
    assert any(
        "_fl4431_baseline_damage_vapor_runtime_evidence" in error
        for error in errors
    )


def test_contract_rejects_deleted_current_product_evidence_binding() -> None:
    sources = _sources()
    sources[AIRPLANE_RUNNER] = sources[AIRPLANE_RUNNER].replace(
        "cmd.append(FL4431_SECONDARY_EFFECT_EVIDENCE_ARG)",
        "pass  # MUTANT removed current Product observation",
        1,
    )
    assert any(
        "lost current damage route seam" in error
        for error in _contract(sources)
    )


def test_contract_rejects_resurrected_late_night_evidence_arm() -> None:
    sources = _sources()
    sources[GDSCRIPT_DAMAGE_DRIVER] += (
        "\nfunc _arm_fl4431_night_product_receipt(_effect: Variant) -> void:\n"
        "\tpass\n"
    )
    assert any(
        "_arm_fl4431_night_product_receipt" in error
        for error in _contract(sources)
    )


def main() -> int:
    errors = _contract(_sources())
    if errors:
        for error in errors:
            print(f"FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL {error}")
        return 1

    mutated_latch = _sources()
    mutated_latch[WORLD] += "\ninterface Mutant { awaitingFirstPilotIntent: boolean; }\n"
    if not _contract(mutated_latch):
        print("FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL latch mutant survived")
        return 1

    mutated_receipt = _sources()
    mutated_receipt[STATE] += "\ndeclare last_applied_flight_route_start_valid: boolean;\n"
    if not _contract(mutated_receipt):
        print("FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL receipt mutant survived")
        return 1

    mutated_consumer = _sources()
    mutated_consumer[RETIRED_CONSUMERS[0]] += '\n_RETIRED_RECEIPT_FIELD = "flight_intent_epoch"\n'
    if not _contract(mutated_consumer):
        print("FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL consumer mutant survived")
        return 1

    mutated_baseline = _sources()
    mutated_baseline[RETIRED_BASELINE_V2_CONSUMERS[0]] += (
        '\nRETIRED_FLAG = "--baseline-receipt"\n'
    )
    if not _contract(mutated_baseline):
        print("FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL baseline-v2 mutant survived")
        return 1

    mutated_sunday = _sources()
    mutated_sunday[AIRPLANE_RUNNER] += (
        '\nRETIRED_FLAG = "--sunday-summary-json"\n'
    )
    if not _contract(mutated_sunday):
        print("FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL Sunday mutant survived")
        return 1

    mutated_profile_owner = _sources()
    mutated_profile_owner[AIRPLANE_RUNNER] += (
        "# Baseline A profile owner\n"
        "\ndef _damage_renderer_profile(client_identity):\n"
        '    return "faa36357eef9dee7e29b9333cb4c1397ca27b929"\n'
    )
    if not _contract(mutated_profile_owner):
        print(
            "FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL "
            "damage profile-by-HEAD mutant survived"
        )
        return 1

    mutated_expected_red = _sources()
    mutated_expected_red[AIRPLANE_RUNNER] += (
        '\nRETIRED_FLAG = "--expected-red-gate"\n'
    )
    if not _contract(mutated_expected_red):
        print(
            "FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL "
            "runner expected-red mutant survived"
        )
        return 1

    mutated_gdscript_side = _sources()
    mutated_gdscript_side[GDSCRIPT_DAMAGE_DRIVER] += (
        '\nvar fl4408_capture_side := "baseline"\n'
    )
    if not _contract(mutated_gdscript_side):
        print(
            "FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL "
            "GDScript capture-side mutant survived"
        )
        return 1

    mutated_gdscript_profile = _sources()
    mutated_gdscript_profile[GDSCRIPT_DAMAGE_DRIVER] += (
        '\nconst FL4431_DAMAGE_BASELINE_PROFILE := "retired"\n'
        'func _fl4431_baseline_damage_vapor_runtime_evidence() -> Dictionary:\n'
        '\treturn {}\n'
    )
    if not _contract(mutated_gdscript_profile):
        print(
            "FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL "
            "GDScript baseline-profile mutant survived"
        )
        return 1

    mutated_product_evidence = _sources()
    mutated_product_evidence[AIRPLANE_RUNNER] = mutated_product_evidence[
        AIRPLANE_RUNNER
    ].replace(
        "cmd.append(FL4431_SECONDARY_EFFECT_EVIDENCE_ARG)",
        "pass  # MUTANT removed current Product observation",
        1,
    )
    if not _contract(mutated_product_evidence):
        print(
            "FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL "
            "current Product evidence mutant survived"
        )
        return 1

    mutated_late_night_arm = _sources()
    mutated_late_night_arm[GDSCRIPT_DAMAGE_DRIVER] += (
        "\nfunc _arm_fl4431_night_product_receipt(_effect: Variant) -> void:\n"
        "\tpass\n"
    )
    if not _contract(mutated_late_night_arm):
        print(
            "FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_FAIL "
            "late night evidence arm mutant survived"
        )
        return 1

    print("FL4431_AIRBORNE_LATCH_DELETED_CONTRACT_PASS mutants=11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
