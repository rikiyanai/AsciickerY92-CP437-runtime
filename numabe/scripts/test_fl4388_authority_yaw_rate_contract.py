#!/usr/bin/env python3
# =============================================================================
# test_fl4388_authority_yaw_rate_contract.py
# =============================================================================
#
# WHAT THIS FILE CHECKS, AND WHY ANYONE SHOULD CARE
# -------------------------------------------------
# The game server ("the authority") decides where every vehicle is and which way
# it is pointing. It says so about thirty times a second. The game DRAWS about
# ninety frames a second. If the drawing code simply repeated the last reported
# heading it would show a turn as three big steps per second, which reads as a
# stutter. So between server messages the drawing code carries the heading
# forward: "it was pointing here, it is turning this fast, so by now it is
# pointing there." That needs a TURN RATE -- how many radians of rotation happen
# per server tick.
#
# Until 2026-08-06 the drawing client worked that rate out for itself, by
# subtracting the previously reported heading from the newest one. That silently
# assumed every heading change is a smooth turn. It is not. The flight simulation
# can also REPOSITION a heading in a single step -- its terrain-bounds response
# swings an aircraft's nose by up to half a turn in one tick to keep it inside the
# map. Subtracting across such a step reported a turn rate of roughly 0.79 radians
# per tick for an aircraft that was not turning at all. The drawing code then
# leaned the whole scene about sixty-one degrees ahead of the authority and snapped
# it back one frame later. It was captured directly (FL-4388): authority heading
# pinned at 7.4613 radians while the drawn heading ramped to 8.5216 and dropped
# back.
#
# The repair moved ownership of the turn rate to the authority, which knows it
# exactly, and DELETED the client-side derivation instead of leaving it as a
# fallback. This file is the guard rail for that repair. It reads the source files
# as text and checks the shape of the seam:
#
#   1. the server schema declares the new field, and declares it LAST;
#   2. the room copies it onto every published world item;
#   3. the flight simulation records the rate its turn law applied, and does NOT
#      record the bounds reposition as a rate;
#   4. the client parses the field with a zero default;
#   5. the client no longer derives a yaw rate by subtracting two headings.
#
# These are STATIC checks: they establish that the code says what it should say.
# They do not establish that the game looks right -- that needs a run and an
# operator's eyes. They exist so that a later tidy-up cannot quietly reinstate the
# defect.
#
# Run with:  python3 -m pytest scripts/test_fl4388_authority_yaw_rate_contract.py
# =============================================================================

from pathlib import Path

# The repository root, found by walking up from this file (scripts/ -> repo root).
ROOT = Path(__file__).resolve().parents[1]

# The five files that together form the turn-rate seam.
SCHEMA = ROOT / "colyseus_server" / "src" / "rooms" / "schema" / "ActorSchema.ts"
ROOM = ROOT / "colyseus_server" / "src" / "rooms" / "AuthoritativeRoom.ts"
SIM = ROOT / "colyseus_server" / "src" / "sim" / "world.ts"
ADAPTER = ROOT / "godot_project" / "scripts" / "net" / "ColyseusStateAdapter.gd"
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"


def _read(path: Path) -> str:
    """Read one source file as plain text.

    Every check in this file is a text check, so this is the only I/O helper
    needed. It is a separate function purely so the encoding is stated once.
    """
    return path.read_text(encoding="utf-8")


def test_schema_declares_the_turn_rate_field_last() -> None:
    """The wire format gains one field, appended, so older peers stay valid.

    Colyseus numbers a schema's fields in the order they are declared and puts
    those numbers on the wire. Inserting a field in the MIDDLE renumbers every
    field after it, which silently breaks any peer built against the older
    definition. Appending leaves all existing numbers untouched, which is what
    makes this change additive.
    """
    schema = _read(SCHEMA)
    assert "declare yaw_rate: number;" in schema
    assert "this.yaw_rate = 0;" in schema

    # Locate the WorldItemSchema field-type table and confirm yaw_rate is its
    # final entry. The table is the block that starts at defineTypes(WorldItemSchema
    # and ends at the first closing "});" after it.
    start = schema.index("defineTypes(WorldItemSchema, {")
    end = schema.index("});", start)
    table = schema[start:end]
    assert 'yaw_rate: "number",' in table
    # Every other field in the table must appear BEFORE yaw_rate. Checking it this
    # way (rather than pinning an exact field list) lets unrelated fields be added
    # later while still refusing an insertion that would renumber the wire format.
    yaw_rate_at = table.index('yaw_rate: "number",')
    for other in ("facing_yaw", "pitch", "roll", "grounded", "hp", "max_hp", "crashed", "destroyed"):
        assert table.index(f"{other}:") < yaw_rate_at, (
            f"{other} is declared after yaw_rate, which renumbers the wire format"
        )


def test_room_publishes_the_turn_rate_for_every_world_item() -> None:
    """Every published item carries a rate, defaulting to "not turning".

    The "?? 0" matters: most world items are not vehicles and never turn (a
    dropped sword), and a vehicle's very first pose has not been simulated yet.
    Both must publish zero, which tells the drawing client to hold the reported
    heading rather than project it anywhere.
    """
    room = _read(ROOM)
    assert "schema.yaw_rate = item.yawRate ?? 0;" in room


def test_simulation_records_the_turn_law_rate_and_not_the_bounds_reposition() -> None:
    """The published rate comes from the turn law only.

    This is the load-bearing distinction of the whole repair. Both the airborne
    bank-to-yaw turn and the ground steering write `yawRateThisTick` and then apply
    it, so the number published and the rotation performed are provably the same
    value. The bounds response, which repositions the nose in one step, writes
    `plane.facingYaw` directly and must never touch `yawRateThisTick` -- otherwise
    the client would be told to keep rotating the drawn scene at a quarter turn per
    tick.
    """
    sim = _read(SIM)
    # The turn law records the rate and then applies exactly that number.
    assert "yawRateThisTick = Math.sin(flight.roll) * AIRPLANE_BANK_TO_YAW_PER_TICK" in sim
    assert "plane.facingYaw += yawRateThisTick;" in sim
    # It is mirrored onto the published item alongside the other render mirrors.
    assert "plane.yawRate = yawRateThisTick;" in sim
    # The boat publishes the turn step that survived its per-tick clip.
    assert "item.yawRate = appliedYawStep;" in sim

    # THE NEGATIVE CHECK. Inside the bounds-response block -- the region between the
    # candidate-heading assignment and the half-turn fallback -- nothing may write a
    # turn rate. If a future edit "helpfully" published the reposition as a rate,
    # this is where it would appear, and this assertion refuses it.
    redirect_start = sim.index("plane.facingYaw = candidateYaw;")
    redirect_end = sim.index("plane.facingYaw += Math.PI;", redirect_start)
    bounds_block = sim[redirect_start:redirect_end]
    assert "yawRate" not in bounds_block, (
        "the out-of-bounds reposition writes a turn rate; a reposition is not a rate"
    )


def test_client_parses_the_field_with_a_zero_default() -> None:
    """The client reads the rate off the wire and fails safe when it is absent.

    Zero means "not turning", so the heading is held exactly where the authority
    put it. That is the correct answer for an older server, for offline play, and
    for harness routes whose world items are plain data rather than a live room.
    """
    adapter = _read(ADAPTER)
    assert '"yaw_rate": float(_read_field(item, "yaw_rate", 0.0)),' in adapter

    world = _read(WORLD)
    # One parse helper, used by both vehicle lanes, with the same zero fallback.
    assert "func _world_item_yaw_rate(_pickup_id: String, item_snapshot: Variant) -> float:" in world
    assert 'return float(item_snapshot.get("yaw_rate", 0.0))' in world
    # Both the aeroplane and the boat raw-server snapshots carry it.
    assert world.count('"yaw_rate": _world_item_yaw_rate(String(pickup_id), item_snapshot),') == 2


def test_client_no_longer_derives_a_yaw_rate_by_differencing() -> None:
    """The old derivation is deleted, not merely bypassed.

    Two owners of "how fast is it turning" is the split this repair removes, so the
    presentation sample must take the rate straight from the server pose and there
    must be no surviving code that subtracts two headings to make one. The whole
    file is checked for `angle_difference` because that was the exact call the old
    derivation used, and it has no other business in this file.
    """
    world = _read(WORLD)
    assert '"yaw_rate_per_tick": float(vehicle_snapshot.get("yaw_rate", 0.0)),' in world
    assert "angle_difference(" not in world, (
        "a heading-differencing call survives in World.gd; the derived turn-rate "
        "owner must stay deleted"
    )


# =============================================================================
# Direct-run driver
# =============================================================================
#
# WHY THIS EXISTS. Every check above is a plain function whose name starts with
# `test_`, which is the convention pytest looks for. Run under pytest, all five
# execute. Run as an ordinary program -- `python3 <this file>` -- Python would
# define the five functions, call none of them, and exit 0 with no output. That
# is a SILENT FALSE PASS on a file whose entire purpose is to refuse a
# reintroduction of the defect, and a runner (or a future agent) invoking it the
# ordinary way would be told everything is fine without a single check running.
#
# So the file also drives itself: it finds every function in its own namespace
# whose name begins with `test_`, calls each one, prints a line per check, and
# exits non-zero if any of them raised. `globals()` is the dictionary of
# everything defined in this file, and `sorted(...)` keeps the output order
# stable from run to run rather than depending on definition order.
if __name__ == "__main__":
    import sys

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
    # A non-zero exit code is what makes a runner notice. Exiting 0 on failure
    # would recreate the very silent-pass problem this driver exists to remove.
    sys.exit(1 if failures else 0)
