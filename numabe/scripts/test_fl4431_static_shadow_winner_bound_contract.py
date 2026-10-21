#!/usr/bin/env python3
"""FL-4431 retained-static smallest-winner bound contract.

The retained static selector preserves the old ascending scan by selecting the
smallest admitted legacy face that the shadow ray hits. Tree traversal can find
that winner early, yet shipping Product still reconstructs every later face
before comparing its legacy index with the selected winner.

This red-first contract pins the next ownership move: compare metadata-derived
legacy indices with the current winner before face reconstruction, and skip a
whole instance before asset/BLAS materialization when its first legacy index
cannot beat that winner. Structural and arithmetic results are diagnostics;
they do not establish runtime timing, visual parity, operator acceptance, or
closure.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from dataclasses import dataclass
import random
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"

DEFAULT_SCAN_CAP = 2048
RANDOM_SEED = 4431_62


@dataclass(frozen=True)
class Face:
    material_id: int
    hit_depth: float | None
    payload: tuple[int, int, int] = (0, 0, 0)


@dataclass(frozen=True)
class Instance:
    object_id: int
    first_legacy_face: int
    faces: tuple[Face, ...]


@dataclass(frozen=True)
class Scene:
    instances: tuple[Instance, ...]
    receiver_face: int = -1
    scan_cap: int = DEFAULT_SCAN_CAP


@dataclass(frozen=True)
class Selection:
    object_id: int
    primitive_id: int
    legacy_face_index: int
    material_id: int
    hit_depth: float
    payload: tuple[int, int, int]


@dataclass
class Work:
    instance_visits: int = 0
    asset_materializations: int = 0
    blas_entries: int = 0
    face_materializations: int = 0
    row_loads: int = 0
    transformed_points: int = 0
    triangle_tests: int = 0


@dataclass(frozen=True)
class Result:
    selection: Selection | None
    work: Work


@dataclass(frozen=True)
class Policy:
    candidate_bound: str = "winner"
    instance_bound: str = "winner"


CORRECT_POLICY = Policy()
LATE_WINNER_MUTANT = Policy(candidate_bound="late")
REVERSED_INEQUALITY_MUTANT = Policy(candidate_bound="reversed")
EQUAL_INDEX_ADMITTED_MUTANT = Policy(candidate_bound="strict_greater")
WHOLE_INSTANCE_BOUND_MISSING_MUTANT = Policy(instance_bound="missing")
HIT_DEPTH_BOUND_MUTANT = Policy(
    candidate_bound="hit_depth",
    instance_bound="hit_depth",
)


Traversal = tuple[tuple[int, tuple[int, ...]], ...]


def metadata_admitted(
    scene: Scene,
    instance: Instance,
    primitive_id: int,
) -> tuple[bool, int]:
    if scene.scan_cap <= 0:
        return False, 0
    if primitive_id < 0 or primitive_id >= len(instance.faces):
        return False, 0
    legacy_face_index = instance.first_legacy_face + primitive_id
    if legacy_face_index >= scene.scan_cap:
        return False, legacy_face_index
    if legacy_face_index == scene.receiver_face:
        return False, legacy_face_index
    return True, legacy_face_index


def smallest_legacy_reference(scene: Scene) -> Selection | None:
    """Model the former ascending scan's exact first eligible ray hit."""
    candidates: list[tuple[int, Instance, int, Face]] = []
    for instance in scene.instances:
        for primitive_id, face in enumerate(instance.faces):
            legacy_face_index = instance.first_legacy_face + primitive_id
            candidates.append(
                (legacy_face_index, instance, primitive_id, face)
            )
    for legacy_face_index, instance, primitive_id, face in sorted(
        candidates,
        key=lambda item: item[0],
    ):
        admitted, derived_index = metadata_admitted(
            scene,
            instance,
            primitive_id,
        )
        assert derived_index == legacy_face_index
        if not admitted or face.material_id == 250 or face.hit_depth is None:
            continue
        return Selection(
            instance.object_id,
            primitive_id,
            legacy_face_index,
            face.material_id,
            face.hit_depth,
            face.payload,
        )
    return None


def instance_cannot_beat(
    instance: Instance,
    selected: Selection | None,
    policy: Policy,
) -> bool:
    if selected is None or policy.instance_bound == "missing":
        return False
    if policy.instance_bound == "hit_depth":
        return instance.first_legacy_face >= selected.hit_depth
    assert policy.instance_bound == "winner"
    return instance.first_legacy_face >= selected.legacy_face_index


def candidate_cannot_beat(
    legacy_face_index: int,
    selected: Selection | None,
    policy: Policy,
) -> bool:
    if selected is None or policy.candidate_bound == "late":
        return False
    if policy.candidate_bound == "reversed":
        return legacy_face_index <= selected.legacy_face_index
    if policy.candidate_bound == "strict_greater":
        return legacy_face_index > selected.legacy_face_index
    if policy.candidate_bound == "hit_depth":
        return legacy_face_index >= selected.hit_depth
    assert policy.candidate_bound == "winner"
    return legacy_face_index >= selected.legacy_face_index


def select_with_bound(
    scene: Scene,
    traversal: Traversal,
    policy: Policy = CORRECT_POLICY,
) -> Result:
    selected: Selection | None = None
    work = Work()

    for instance_slot, primitive_order in traversal:
        instance = scene.instances[instance_slot]
        work.instance_visits += 1
        if scene.scan_cap <= 0:
            continue
        if instance.first_legacy_face >= scene.scan_cap:
            continue
        if instance_cannot_beat(instance, selected, policy):
            continue

        work.asset_materializations += 1
        work.blas_entries += 1
        for primitive_id in primitive_order:
            admitted, legacy_face_index = metadata_admitted(
                scene,
                instance,
                primitive_id,
            )
            if not admitted:
                continue
            if candidate_cannot_beat(
                legacy_face_index,
                selected,
                policy,
            ):
                continue

            work.face_materializations += 1
            work.row_loads += 4
            work.transformed_points += 5
            face = instance.faces[primitive_id]
            if face.material_id == 250:
                continue

            work.triangle_tests += 2
            if face.hit_depth is None:
                continue
            candidate = Selection(
                instance.object_id,
                primitive_id,
                legacy_face_index,
                face.material_id,
                face.hit_depth,
                face.payload,
            )
            # This is the reference selection law, not an admission owner:
            # traversal order must never replace an equal or smaller winner
            # with a larger provenance index.
            if (
                selected is not None
                and candidate.legacy_face_index
                >= selected.legacy_face_index
            ):
                continue
            selected = candidate

    return Result(selected, work)


def natural_traversal(scene: Scene) -> Traversal:
    return tuple(
        (instance_slot, tuple(range(len(instance.faces))))
        for instance_slot, instance in enumerate(scene.instances)
    )


def shuffled_traversal(scene: Scene, rng: random.Random) -> Traversal:
    instance_slots = list(range(len(scene.instances)))
    rng.shuffle(instance_slots)
    result: list[tuple[int, tuple[int, ...]]] = []
    for instance_slot in instance_slots:
        primitive_ids = list(range(len(scene.instances[instance_slot].faces)))
        rng.shuffle(primitive_ids)
        result.append((instance_slot, tuple(primitive_ids)))
    return tuple(result)


def extract_glsl_function(source: str, name: str) -> str:
    match = re.search(
        r"\b[\w\s]+\b" + re.escape(name) + r"\s*\([^;]*?\)\s*\{",
        source,
        re.S,
    )
    if match is None:
        return ""
    brace = source.find("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    return ""


def compact(value: str) -> str:
    value = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
    value = re.sub(r"//[^\n]*", "", value)
    return re.sub(r"\s+", "", value)


def shipping_source_errors(source: str) -> tuple[str, ...]:
    """Return source-shape failures for both retained-static query modes.

    Static traversal must use the host-validated retained face total. The
    deleted 2,048-face flat-bank ceiling was a capacity limit from a different
    owner; restoring it here silently removes later retained instances from
    every shadow query.
    """
    errors: list[str] = []
    helper_name = "vsb_retained_static_legacy_candidate_admitted"
    helper = compact(extract_glsl_function(source, helper_name))
    consider = compact(
        extract_glsl_function(source, "vsb_retained_consider_static_caster")
    )
    blas = compact(
        extract_glsl_function(source, "vsb_retained_static_blas_select")
    )
    traversal = compact(
        extract_glsl_function(
            source,
            "vsb_retained_select_static_shadow_caster",
        )
    )
    point = compact(
        extract_glsl_function(
            source,
            "vsb_retained_select_point_light_caster",
        )
    )
    q3 = compact(
        extract_glsl_function(
            source,
            "vsb_retained_select_q3_static_shadow_caster",
        )
    )
    if not all((helper, consider, blas, traversal, point, q3)):
        errors.append("retained-static selector closure is incomplete")
        return tuple(errors)

    materializer = consider.find("vsb_retained_static_face_by_indices(")
    asset_load = blas.find(
        "VsbSemanticStaticAssetasset=vsb_semantic_static_assets.values"
    )
    if materializer < 0 or asset_load < 0:
        errors.append("face or asset materialization seam is missing")
        return tuple(errors)
    pre_asset = blas[:asset_load]

    helper_derivation = helper.find(
        "legacy_face_index=instance.refs.z+primitive_id"
    )
    helper_winner = helper.find(
        "selected.valid&&legacy_face_index"
        ">=selected.legacy_face_index"
    )
    helper_shape = (
        helper_derivation >= 0
        and helper_winner > helper_derivation
        and "intreceiver_face_idx,uintretained_face_total" in helper
        and "legacy_face_index>=retained_face_total" in helper
        and "require_q3_admission" not in helper
        and "selected.depth" not in helper
        and "legacy_face_index>selected.legacy_face_index"
        not in helper
        and "legacy_face_index<=selected.legacy_face_index"
        not in helper
    )
    whole_instance_shape = (
        "selected.valid" in pre_asset
        and "instance.refs.z>=selected.legacy_face_index" in pre_asset
        and "selected.depth" not in pre_asset
    )
    if not helper_shape:
        errors.append("smallest-legacy candidate bound drifted")
    if not whole_instance_shape:
        errors.append("pre-asset whole-instance winner bound drifted")
    if blas.count(helper_name + "(") < 2:
        errors.append("BLAS leaves bypass metadata admission")
    if "require_q3_admission" in blas:
        errors.append("retired dual-admission owner returned")
    if "retained_face_total" not in blas or "scan_cap" in blas:
        errors.append("BLAS lost the retained publication total")
    if consider.find("facts.material_id==250u") <= materializer:
        errors.append("material-250 exclusion moved before face facts")

    q3_return = consider.find("if(q3_any_hit)returntrue;")
    point_valid = consider.find("selected.valid=true;")
    point_legacy = consider.find(
        "selected.legacy_face_index=facts.legacy_face_index;"
    )
    point_depth = consider.find("selected.depth=hit_depth;")
    if not (
        q3_return >= 0
        and point_valid > q3_return
        and point_legacy > point_valid
        and point_depth > point_legacy
    ):
        errors.append("Q3 any-hit no longer precedes point-light payload")
    if blas.count("if(q3_any_hit&&hit)returntrue;") != 2:
        errors.append("BLAS any-hit propagation drifted")
    if traversal.count("if(q3_any_hit&&hit)") != 2:
        errors.append("TLAS any-hit propagation drifted")

    retained_total = (
        "uintretained_face_total=product_vsb_frame.view_identity.w;"
    )
    zero_gate = "if(retained_face_total==0u)returnselected;"
    if retained_total not in traversal or zero_gate not in traversal:
        errors.append("selector lost the validated retained face total")
    if "2048" in traversal or "scan_cap" in traversal:
        errors.append("retired 2,048 static-shadow cap resurrected")

    false_call = (
        "returnvsb_retained_select_static_shadow_caster("
        "shadow_ray,receiver_face_idx,false);"
    )
    true_call = (
        "returnvsb_retained_select_static_shadow_caster("
        "shadow_ray,receiver_face_idx,true);"
    )
    if false_call not in point:
        errors.append("point-light selector no longer requests full selection")
    if true_call not in q3:
        errors.append("Q3 selector no longer requests any-hit")
    if "FL4270_STATIC_FACE_SCAN_MAX" in source:
        errors.append("retired static-shadow cap owner still exists")
    if re.search(
        r"\bvsb_retained_select_sun_shadow_caster\s*\(",
        re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S),
    ):
        errors.append("dead sun-shadow wrapper resurrected")
    return tuple(errors)


def test_shipping_source_requires_winner_bound_and_any_hit_modes() -> None:
    errors = shipping_source_errors(
        _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    )
    assert not errors, f"retained-static source contract failed: {errors}"


def test_source_gate_rejects_query_mode_and_payload_mutants() -> None:
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    mutants = (
        (
            "point-light any-hit leakage",
            source.replace(
                "receiver_face_idx, false);",
                "receiver_face_idx, true);",
                1,
            ),
            "point-light selector no longer requests full selection",
        ),
        (
            "Q3 full-selection drift",
            source.replace(
                "receiver_face_idx, true);",
                "receiver_face_idx, false);",
                1,
            ),
            "Q3 selector no longer requests any-hit",
        ),
        (
            "BLAS any-hit edge removed",
            source.replace(
                "if (q3_any_hit && hit) return true;",
                "if (q3_any_hit && hit) selected.valid = true;",
                1,
            ),
            "BLAS any-hit propagation drifted",
        ),
        (
            "Q3 constructs point-light payload",
            source.replace(
                "if (q3_any_hit) return true;\n"
                "\tselected.valid = true;",
                "selected.valid = true;\n"
                "\tif (q3_any_hit) return true;",
                1,
            ),
            "Q3 any-hit no longer precedes point-light payload",
        ),
        (
            "point-light depth deleted",
            source.replace(
                "\tselected.depth = hit_depth;\n",
                "",
                1,
            ),
            "Q3 any-hit no longer precedes point-light payload",
        ),
        (
            "dead sun wrapper restored",
            source
            + "\nVsbRetainedStaticCasterSelection "
            "vsb_retained_select_sun_shadow_caster("
            "ViewRay ray, int receiver) {\n"
            "    return vsb_retained_select_static_shadow_caster("
            "ray, receiver, true);\n"
            "}\n",
            "dead sun-shadow wrapper resurrected",
        ),
        (
            "legacy 2,048 face ceiling restored",
            source.replace(
                "uint retained_face_total = product_vsb_frame.view_identity.w;",
                "uint retained_face_total = min("
                "product_vsb_frame.view_identity.w, 2048u);",
                1,
            ),
            "retired 2,048 static-shadow cap resurrected",
        ),
        (
            "retained face total bypassed",
            source.replace(
                "uint retained_face_total = product_vsb_frame.view_identity.w;",
                "uint retained_face_total = 0xFFFFFFFFu;",
                1,
            ),
            "selector lost the validated retained face total",
        ),
    )
    for name, mutant, expected_error in mutants:
        assert mutant != source, name
        errors = shipping_source_errors(mutant)
        assert expected_error in errors, f"{name} survived: {errors}"


def test_randomized_traversal_orders_match_ascending_scan_exactly() -> None:
    rng = random.Random(RANDOM_SEED)
    for scene_index in range(96):
        instances: list[Instance] = []
        first_legacy_face = rng.randint(0, 3)
        for object_id in range(rng.randint(2, 9)):
            faces = tuple(
                Face(
                    material_id=(
                        250 if rng.random() < 0.13 else rng.randint(1, 12)
                    ),
                    hit_depth=(
                        None
                        if rng.random() < 0.28
                        else round(rng.uniform(0.05, 80.0), 6)
                    ),
                    payload=(scene_index, object_id, primitive_id),
                )
                for primitive_id in range(rng.randint(1, 8))
            )
            instances.append(
                Instance(object_id, first_legacy_face, faces)
            )
            first_legacy_face += len(faces) + rng.randint(0, 3)

        all_indices = [
            instance.first_legacy_face + primitive_id
            for instance in instances
            for primitive_id in range(len(instance.faces))
        ]
        receiver_face = (
            rng.choice(all_indices) if rng.random() < 0.3 else -1
        )
        scene = Scene(
            tuple(instances),
            receiver_face=receiver_face,
            scan_cap=rng.randint(1, max(all_indices) + 3),
        )
        expected = smallest_legacy_reference(scene)
        for _ in range(12):
            actual = select_with_bound(
                scene,
                shuffled_traversal(scene, rng),
            )
            assert actual.selection == expected


def test_primitive_range_receiver_cap_material_and_hit_semantics_are_preserved() -> None:
    scene = Scene(
        (
            Instance(
                0,
                20,
                (
                    Face(9, 1.0, payload=(1, 0, 0)),
                    Face(250, 2.0, payload=(1, 0, 1)),
                    Face(7, None, payload=(1, 0, 2)),
                    Face(6, 4.0, payload=(1, 0, 3)),
                    Face(5, 5.0, payload=(1, 0, 4)),
                ),
            ),
        ),
        receiver_face=20,
        scan_cap=24,
    )
    expected = smallest_legacy_reference(scene)
    assert expected is not None
    assert expected.legacy_face_index == 23
    assert select_with_bound(
        scene,
        ((0, (-1, 99, 4, 3, 2, 1, 0)),),
    ).selection == expected


def test_winner_bounds_reduce_face_and_asset_materialization() -> None:
    instances = [
        Instance(
            0,
            0,
            tuple(Face(1, float(index + 1)) for index in range(12)),
        )
    ]
    for object_id in range(1, 9):
        first_face = object_id * 32
        instances.append(
            Instance(
                object_id,
                first_face,
                tuple(
                    Face(2 + object_id, float(primitive_id + 2))
                    for primitive_id in range(10)
                ),
            )
        )
    scene = Scene(tuple(instances), scan_cap=4096)
    traversal = natural_traversal(scene)
    early = select_with_bound(scene, traversal)
    late = select_with_bound(scene, traversal, LATE_WINNER_MUTANT)
    no_instance_bound = select_with_bound(
        scene,
        traversal,
        WHOLE_INSTANCE_BOUND_MISSING_MUTANT,
    )

    expected = smallest_legacy_reference(scene)
    assert early.selection == late.selection == no_instance_bound.selection
    assert early.work.face_materializations == 1
    assert late.work.face_materializations == 12
    assert early.work.face_materializations < late.work.face_materializations
    assert early.work.asset_materializations == 1
    assert no_instance_bound.work.asset_materializations == len(instances)


def test_late_winner_check_mutant_is_killed_by_materialization_count() -> None:
    scene = Scene(
        (
            Instance(
                0,
                40,
                tuple(Face(3, float(index + 1)) for index in range(7)),
            ),
        ),
        scan_cap=100,
    )
    traversal = natural_traversal(scene)
    correct = select_with_bound(scene, traversal)
    mutant = select_with_bound(scene, traversal, LATE_WINNER_MUTANT)
    assert mutant.selection == correct.selection
    assert correct.work.face_materializations == 1
    assert mutant.work.face_materializations == 7


def test_reversed_inequality_mutant_loses_the_smaller_late_winner() -> None:
    scene = Scene(
        (Instance(0, 100, (Face(2, 9.0), Face(3, 1.0))),),
        scan_cap=200,
    )
    traversal = ((0, (1, 0)),)
    expected = smallest_legacy_reference(scene)
    correct = select_with_bound(scene, traversal)
    mutant = select_with_bound(
        scene,
        traversal,
        REVERSED_INEQUALITY_MUTANT,
    )
    assert correct.selection == expected
    assert mutant.selection != expected
    assert mutant.selection is not None
    assert mutant.selection.legacy_face_index == 101


def test_equal_index_admitted_mutant_is_killed_by_duplicate_leaf_work() -> None:
    scene = Scene(
        (Instance(0, 12, (Face(5, 2.0, payload=(8, 8, 8)),)),),
        scan_cap=100,
    )
    duplicate_leaf_traversal = ((0, (0, 0)),)
    correct = select_with_bound(scene, duplicate_leaf_traversal)
    mutant = select_with_bound(
        scene,
        duplicate_leaf_traversal,
        EQUAL_INDEX_ADMITTED_MUTANT,
    )
    assert mutant.selection == correct.selection
    assert correct.work.face_materializations == 1
    assert mutant.work.face_materializations == 2


def test_missing_whole_instance_bound_is_killed_before_asset_load() -> None:
    scene = Scene(
        (
            Instance(0, 0, (Face(1, 3.0),)),
            Instance(1, 20, (Face(2, 2.0), Face(2, 1.0))),
            Instance(2, 40, (Face(3, 1.0),)),
        ),
        scan_cap=100,
    )
    traversal = natural_traversal(scene)
    correct = select_with_bound(scene, traversal)
    mutant = select_with_bound(
        scene,
        traversal,
        WHOLE_INSTANCE_BOUND_MISSING_MUTANT,
    )
    assert mutant.selection == correct.selection
    assert correct.work.asset_materializations == 1
    assert mutant.work.asset_materializations == 3
    assert mutant.work.face_materializations == correct.work.face_materializations


def test_hit_depth_bound_mutant_confuses_nearest_with_smallest_index() -> None:
    scene = Scene(
        (Instance(0, 100, (Face(8, 50.0), Face(9, 0.25))),),
        scan_cap=200,
    )
    traversal = ((0, (1, 0)),)
    expected = smallest_legacy_reference(scene)
    correct = select_with_bound(scene, traversal)
    mutant = select_with_bound(scene, traversal, HIT_DEPTH_BOUND_MUTANT)
    assert correct.selection == expected
    assert mutant.selection != expected
    assert mutant.selection is not None
    assert mutant.selection.legacy_face_index == 101
