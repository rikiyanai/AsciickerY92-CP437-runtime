#!/usr/bin/env python3
"""FL-4431 Product actor-shadow XZ admission destructive contract.

``actor_row_shadow_density_at_world`` clamps every actor shadow radius to at most
0.95 world units. After the actor foot passes the existing vertical threshold,
a receiver whose Chebyshev XZ separation from that foot is at least 0.95
cannot produce positive density: Euclidean distance is at least the Chebyshev
distance, and GLSL ``smoothstep`` is exactly one at and beyond its upper edge.

The accepted optimization therefore rejects that row before
``actor_fixed256_pair(row, 13)`` without changing the scalar density output.
The executable float32 model, boundary fixtures, randomized scenes, tie cases,
projection labels, and destructive mutants are green. The shipping-source
shape assertion is intentionally red until Product moves the exact admission
check ahead of the size fetch.

This contract is structural and arithmetic evidence only. It does not measure
GPU time, establish headed visual behavior, record operator acceptance, or
close FL-4431.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from dataclasses import dataclass
import math
import random
import re
import struct
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"

ACTOR_SHADOW_ELEVATION_THRESHOLD = 0.6
ACTOR_SHADOW_XZ_MAX = 0.95
ACTOR_SHADOW_MIN_RADIUS = 0.42
ACTOR_SHADOW_DENSITY_SCALE = 0.84
WORLD_SHADOW_MAX_CASTERS = 256
RANDOM_SEED = 0x4431A5
RANDOM_SCENES = 192
PROJECTIONS = ("orthographic", "perspective")


def f32(value: float) -> float:
    """Round one arithmetic checkpoint to IEEE-754 binary32."""
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def float_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", f32(value)))[0]


def float_from_bits(value: int) -> float:
    return struct.unpack("<f", struct.pack("<I", value))[0]


XZ_MAX_F32 = f32(ACTOR_SHADOW_XZ_MAX)
XZ_IMMEDIATELY_BELOW = float_from_bits(float_bits(XZ_MAX_F32) - 1)
XZ_IMMEDIATELY_ABOVE = float_from_bits(float_bits(XZ_MAX_F32) + 1)


@dataclass(frozen=True)
class Vec2:
    x: float
    y: float


@dataclass(frozen=True)
class Vec3:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class ActorRow:
    foot: Vec3
    world_size: Vec2


@dataclass(frozen=True)
class ShadowOutput:
    density_bits: int


@dataclass
class Work:
    foot_fetches: int = 0
    size_fetches: int = 0


@dataclass(frozen=True)
class RunResult:
    output: ShadowOutput
    work: Work


@dataclass(frozen=True)
class AdmissionPolicy:
    """Mutation surface for the one proposed XZ admission predicate."""

    bound: float = XZ_MAX_F32
    reject_equality: bool = True


DEFAULT_POLICY = AdmissionPolicy()


def vec2(x: float, y: float) -> Vec2:
    return Vec2(f32(x), f32(y))


def vec3(x: float, y: float, z: float) -> Vec3:
    return Vec3(f32(x), f32(y), f32(z))


def actor(
    foot: Vec3,
    world_size: Vec2,
) -> ActorRow:
    return ActorRow(
        vec3(foot.x, foot.y, foot.z),
        vec2(world_size.x, world_size.y),
    )


def glsl_length2(x: float, y: float) -> float:
    xx = f32(f32(x) * f32(x))
    yy = f32(f32(y) * f32(y))
    return f32(math.sqrt(f32(xx + yy)))


def glsl_smoothstep(edge_0: float, edge_1: float, value: float) -> float:
    t = f32(f32(value - edge_0) / f32(edge_1 - edge_0))
    t = f32(min(max(t, 0.0), 1.0))
    return f32(f32(t * t) * f32(3.0 - f32(2.0 * t)))


def xz_separation(receiver: Vec3, foot: Vec3) -> tuple[float, float, float]:
    dx = f32(receiver.x - foot.x)
    dz = f32(receiver.z - foot.z)
    maximum = f32(max(abs(dx), abs(dz)))
    return dx, dz, maximum


def actor_density(
    receiver: Vec3,
    foot: Vec3,
    world_size: Vec2,
) -> float:
    size_x = f32(max(world_size.x, f32(0.001)))
    size_y = f32(max(world_size.y, f32(0.001)))
    radius_x = f32(size_x * f32(0.62))
    radius_y = f32(size_y * f32(0.20))
    radius = f32(
        min(
            max(max(radius_x, radius_y), f32(ACTOR_SHADOW_MIN_RADIUS)),
            XZ_MAX_F32,
        )
    )
    dx, dz, _maximum = xz_separation(receiver, foot)
    distance = glsl_length2(dx, dz)
    smooth = glsl_smoothstep(f32(0.0), max(radius, f32(0.001)), distance)
    return f32(f32(1.0 - smooth) * f32(ACTOR_SHADOW_DENSITY_SCALE))


def policy_rejects(maximum: float, policy: AdmissionPolicy) -> bool:
    bound = f32(policy.bound)
    if policy.reject_equality:
        return maximum >= bound
    return maximum > bound


def select_shadow(
    rows: tuple[ActorRow, ...],
    receiver: Vec3,
    projection: str,
    *,
    early_xz_admission: bool,
    policy: AdmissionPolicy = DEFAULT_POLICY,
) -> RunResult:
    """Execute the current row loop with an optional pre-size XZ rejection."""
    assert projection in PROJECTIONS
    work = Work()
    density = f32(0.0)

    actor_count = min(len(rows), WORLD_SHADOW_MAX_CASTERS)
    for row_index in range(actor_count):
        row = rows[row_index]
        work.foot_fetches += 1
        foot = row.foot
        if f32(receiver.y - foot.y) > f32(
            ACTOR_SHADOW_ELEVATION_THRESHOLD
        ):
            continue
        _dx, _dz, maximum = xz_separation(receiver, foot)
        if early_xz_admission and policy_rejects(maximum, policy):
            continue

        # Models actor_fixed256_pair(row, 13).
        work.size_fetches += 1
        candidate_density = actor_density(receiver, foot, row.world_size)
        if candidate_density > density:
            density = candidate_density

    density = f32(min(max(density, f32(0.0)), f32(1.0)))
    return RunResult(
        ShadowOutput(float_bits(density)),
        work,
    )


def assert_output_equal(expected: RunResult, actual: RunResult) -> None:
    assert actual.output == expected.output


def extract_glsl_function(source: str, name: str) -> str:
    match = re.search(
        r"(?m)^(?:precise\s+)?[A-Za-z_]\w*\s+"
        + re.escape(name)
        + r"\s*\([^;{}]*?\)\s*\{",
        source,
        re.S,
    )
    if match is None:
        return ""
    brace = source.find("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        depth += source[index] == "{"
        depth -= source[index] == "}"
        if depth == 0:
            return source[match.start() : index + 1]
    return ""


def compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def test_shipping_source_is_red_until_xz_admission_precedes_size_fetch() -> None:
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    body = compact(
        extract_glsl_function(source, "actor_row_shadow_density_at_world")
    )
    assert body
    clean_source = compact(source)
    assert "structShadowFact" not in clean_source
    assert "actor_row_shadow_fact_at_world(" not in clean_source
    assert "actor_cell_word(15,row)" not in body
    assert "caster_id" not in body
    assert "caster_xz" not in body
    foot = body.index("vec3foot=actor_foot_world(row);")
    vertical = body.index(
        "if(receiver_world.y-foot.y>ACTOR_SHADOW_ELEVATION_THRESHOLD)continue;"
    )
    size = body.index(
        "vec2world_size=max(actor_fixed256_pair(row,13),vec2(0.001));"
    )
    exact_admission = (
        "if(max(abs(receiver_world.x-foot.x),"
        "abs(receiver_world.z-foot.z))>=0.95)continue;"
    )
    admission = body.find(exact_admission)

    current_red = admission < 0 and foot < vertical < size
    replacement_shape = foot < vertical < admission < size
    assert current_red or replacement_shape, (
        "actor shadow source matches neither the pinned exhaustive shape nor "
        "the exact pre-size XZ admission replacement"
    )
    assert replacement_shape, (
        "RED: after actor foot load and vertical eligibility, add the exact "
        "max(abs(receiver_world.x - foot.x), "
        "abs(receiver_world.z - foot.z)) >= 0.95 continue before "
        "actor_fixed256_pair(row, 13)"
    )


@pytest.mark.parametrize("projection", PROJECTIONS)
@pytest.mark.parametrize(
    ("separation", "expected_size_fetches"),
    (
        (XZ_IMMEDIATELY_BELOW, 1),
        (XZ_MAX_F32, 0),
        (XZ_IMMEDIATELY_ABOVE, 0),
    ),
)
def test_exact_f32_boundary_output_and_work(
    projection: str,
    separation: float,
    expected_size_fetches: int,
) -> None:
    rows = (actor(vec3(0.0, 0.0, 0.0), vec2(10.0, 10.0)),)
    receiver = vec3(separation, 0.0, 0.0)
    exhaustive = select_shadow(
        rows, receiver, projection, early_xz_admission=False
    )
    early = select_shadow(
        rows, receiver, projection, early_xz_admission=True
    )
    assert_output_equal(exhaustive, early)
    assert early.work.size_fetches == expected_size_fetches
    if separation >= XZ_MAX_F32:
        assert exhaustive.output.density_bits == float_bits(0.0)


@pytest.mark.parametrize("projection", PROJECTIONS)
def test_seeded_random_actor_rows_preserve_exact_output(projection: str) -> None:
    rng = random.Random(RANDOM_SEED)
    for _scene_index in range(RANDOM_SCENES):
        receiver = vec3(
            rng.uniform(-40.0, 40.0),
            rng.uniform(-8.0, 30.0),
            rng.uniform(-40.0, 40.0),
        )
        rows = tuple(
            actor(
                vec3(
                    receiver.x + rng.uniform(-6.0, 6.0),
                    receiver.y + rng.uniform(-2.0, 3.0),
                    receiver.z + rng.uniform(-6.0, 6.0),
                ),
                vec2(
                    rng.uniform(-0.5, 12.0),
                    rng.uniform(-0.5, 12.0),
                ),
            )
            for _row in range(rng.randint(1, 24))
        )
        exhaustive = select_shadow(
            rows, receiver, projection, early_xz_admission=False
        )
        early = select_shadow(
            rows, receiver, projection, early_xz_admission=True
        )
        assert_output_equal(exhaustive, early)
        assert early.work.size_fetches <= exhaustive.work.size_fetches


@pytest.mark.parametrize("projection", PROJECTIONS)
def test_multiple_rows_preserve_scalar_maximum(
    projection: str,
) -> None:
    rows = (
        actor(vec3(0.20, 0.0, 0.10), vec2(4.0, 4.0)),
        actor(vec3(0.20, 0.0, 0.10), vec2(4.0, 4.0)),
        actor(vec3(XZ_MAX_F32, 0.0, 0.0), vec2(100.0, 100.0)),
    )
    receiver = vec3(0.0, 0.0, 0.0)
    exhaustive = select_shadow(
        rows, receiver, projection, early_xz_admission=False
    )
    early = select_shadow(
        rows, receiver, projection, early_xz_admission=True
    )
    assert_output_equal(exhaustive, early)
    assert early.output.density_bits != float_bits(0.0)
    assert early.work.size_fetches == 2


@pytest.mark.parametrize("projection", PROJECTIONS)
def test_too_narrow_bound_mutant_is_destroyed(projection: str) -> None:
    rows = (actor(vec3(0.945, 0.0, 0.0), vec2(100.0, 100.0)),)
    receiver = vec3(0.0, 0.0, 0.0)
    exhaustive = select_shadow(
        rows, receiver, projection, early_xz_admission=False
    )
    correct = select_shadow(
        rows, receiver, projection, early_xz_admission=True
    )
    mutant = select_shadow(
        rows,
        receiver,
        projection,
        early_xz_admission=True,
        policy=AdmissionPolicy(bound=f32(0.94)),
    )
    assert_output_equal(exhaustive, correct)
    assert exhaustive.output.density_bits != float_bits(0.0)
    assert mutant.output != exhaustive.output


@pytest.mark.parametrize("projection", PROJECTIONS)
def test_wrong_strictness_mutant_is_destroyed_by_work_counter(
    projection: str,
) -> None:
    rows = (actor(vec3(XZ_MAX_F32, 0.0, 0.0), vec2(100.0, 100.0)),)
    receiver = vec3(0.0, 0.0, 0.0)
    correct = select_shadow(
        rows, receiver, projection, early_xz_admission=True
    )
    mutant = select_shadow(
        rows,
        receiver,
        projection,
        early_xz_admission=True,
        policy=AdmissionPolicy(reject_equality=False),
    )
    assert mutant.output == correct.output
    assert correct.work.size_fetches == 0
    assert mutant.work.size_fetches == 1


@pytest.mark.parametrize("projection", PROJECTIONS)
def test_vertical_rejection_still_dominates_xz_and_size_work(
    projection: str,
) -> None:
    rows = (actor(vec3(0.0, -1.0, 0.0), vec2(100.0, 100.0)),)
    receiver = vec3(0.0, 0.0, 0.0)
    exhaustive = select_shadow(
        rows, receiver, projection, early_xz_admission=False
    )
    early = select_shadow(
        rows, receiver, projection, early_xz_admission=True
    )
    assert_output_equal(exhaustive, early)
    assert early.work.foot_fetches == 1
    assert early.work.size_fetches == 0
