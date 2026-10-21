from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
WORLD = (ROOT / "godot_project/scripts/World.gd").read_text()
COMPOSITOR = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
SCENE_DB = (ROOT / "godot_project/render/scene/SceneGeometryDB.gd").read_text()
BUILDER = (ROOT / "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd").read_text()
VSB_SHADER = (ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl").read_text()
SPIDER_BUILDER = (ROOT / "godot_project/scripts/creatures/SpiderPresentationBuilder.gd").read_text()
PRODUCT = _expand_includes((ROOT / "godot_project/render/product/cell_owner_product.glsl").read_text())


def _function(source: str, name: str) -> str:
    start = source.index(f"func {name}(")
    next_function = source.find("\nfunc ", start + 1)
    return source[start:] if next_function < 0 else source[start:next_function]


def _static_function(source: str, name: str) -> str:
    # SpiderPresentationBuilder.gd is all `static func`, so the plain `\nfunc `
    # delimiter above would run to end of file and make an absence assertion
    # meaningless.
    start = source.index(f"static func {name}(")
    next_function = source.find("\nstatic func ", start + 1)
    return source[start:] if next_function < 0 else source[start:next_function]


def _kind_table(source: str, marker: str) -> list[tuple[str, int]]:
    table = source.split(marker, 1)[1].split("}", 1)[0]
    return [(name, int(value)) for name, value in re.findall(r'&"([a-z_]+)"\s*:\s*(\d+)', table)]


def test_world_publishes_typed_spider_product_and_server_validated_mount_intent() -> None:
    append = _function(WORLD, "_append_spider_source_facts")
    assert "SpiderPresentationBuilderScript.build_spider_product" in append
    assert '"kind": "airplane"' not in append
    assert 'snapshot.get("spiders", {})' in WORLD
    assert "_ridden_spider_id_for_actor_snapshot" in WORLD
    assert 'String(spider.get("role", "")) != "subdued"' in WORLD

    key_m = WORLD.split("KEY_M:", 1)[1].split("# DELETED 2026-08-06", 1)[0]
    assert key_m.index("_mounted_on_spider") < key_m.index("_mounted_on_airplane")
    assert 'notify_local_intent({"pickup_id": _near_spider_pickup_id})' in key_m


def test_spider_has_explicit_retained_kind_and_24_bit_identity() -> None:
    spider_appender = _function(COMPOSITOR, "_append_dynamic_mover_spider")
    assert "_append_dynamic_mover_airplane" not in spider_appender
    assert '"spider":\n\t\t\t\t_append_dynamic_mover_spider' in COMPOSITOR

    scene_kinds = _kind_table(SCENE_DB, "VSB_DYNAMIC_KIND_IDS := {")
    assert 'preload("res://render/scene/SceneGeometryDB.gd").VSB_DYNAMIC_KIND_IDS' in BUILDER
    assert "const DYNAMIC_KIND_IDS := {" not in BUILDER
    assert ("spider", 10) in scene_kinds
    assert ("ambient_mesh", 11) in scene_kinds
    assert ("rain", 12) in scene_kinds
    shader_kind_max = int(re.search(r"DYNAMIC_KIND_ID_MAX = (\d+)u;", VSB_SHADER).group(1))
    assert shader_kind_max == max(value for _, value in scene_kinds), (
        "a CPU-admitted kind must not invalidate the GPU's complete mover bank"
    )
    assert "& 0x00ffffff" in SPIDER_BUILDER


def test_spider_geometry_is_volumetric_not_single_ribbon_rows() -> None:
    # FL-4907 N16 operator order 2026-10-02: the minimum rig is a baked rigid
    # mesh asset (2 hexagonal bipyramids, saddle, 16 tetrahedral leg segments).
    import json

    assert "const LEG_HALF_HEIGHT := 0.050" in SPIDER_BUILDER
    for deleted in ("_append_ellipsoid_rows", "_append_body_rows", "_body_point",
                    "BODY_RADIAL_SEGMENTS", "BODY_LATITUDE_BANDS", "_append_quad", "_append_seat_rows"):
        assert deleted not in SPIDER_BUILDER, deleted
    assert 'RIGID_MESH_ASSET := "res://assets/world_mesh/spider_parts_rigid_mesh.json"' in SPIDER_BUILDER
    asset = json.loads((ROOT / "godot_project/assets/world_mesh/spider_parts_rigid_mesh.json")
                       .read_text(encoding="utf-8"))
    parts = asset["part_indices"]
    assert asset["face_count"] == 89 and len(parts) == 89
    assert parts[:24] == [0] * 24 and parts[24] == 1
    assert parts[25:] == [p for p in range(2, 18) for _ in range(4)]


def test_spider_perspective_product_admits_only_scoped_centroid_drift() -> None:
    assert "vsb_retained_spider_material" in PRODUCT
    for material in ("238u", "239u", "246u", "247u", "248u"):
        assert material in PRODUCT
    dynamic_match = PRODUCT.split("bool vsb_retained_dynamic_anchor_matches", 1)[1].split(
        "bool vsb_retained_static_world_point", 1
    )[0]
    assert "product_vsb_frame.view_identity.z != VSB_PROJECTION_PERSPECTIVE" in dynamic_match
    assert "vsb_retained_anchor_matches_exact" in dynamic_match
    assert "vsb_retained_perspective_anchor_matches_scoped" in dynamic_match


def test_spider_colour_is_keyed_by_server_variant_not_by_role() -> None:
    # FL-4790 operator requirement: one grey/silver ridable creature and one dark
    # enemy. Mounting or subduing must not repaint either, so every role key in
    # the published Product receives the SAME body material for one creature and
    # the choice is made from the server's variant field.
    append = _function(WORLD, "_append_spider_source_facts")
    assert 'String(product.get("variant", "dark")) == "silver"' in append
    assert 'product["mat_body_hostile"] = body_material' in append
    assert 'product["mat_body_subdued"] = body_material' in append
    assert 'product["mat_body_ridden"] = body_material' in append
    assert "SPIDER_MAT_SILVER_LEG if is_silver else SPIDER_MAT_DARK_LEG" in append
    # The five palette columns must stay inside the set the Product shader
    # already admits, or the silver creature drops out of the closed-mesh normal
    # path and out of the scoped perspective comparator (the FL-4790 magenta
    # regression). This test is the guard on that coupling.
    palette = _function(WORLD, "_inject_spider_matlib_columns")
    declared = dict(re.findall(r"const (SPIDER_MAT_[A-Z_]+) := (\d+)", WORLD))
    assert set(declared.values()) == {"238", "239", "246", "247", "248"}
    for name in declared:
        assert name in palette, f"{name} has no authored colour column"
    for material in ("238u", "239u", "246u", "247u", "248u"):
        assert material in PRODUCT.split("vsb_retained_spider_material", 1)[1][:300]

    # The variant reaches the client through the wire and the pure builder, and
    # neither invents it locally.
    adapter = (ROOT / "godot_project/scripts/net/ColyseusStateAdapter.gd").read_text()
    assert '"variant": String(_read_field(spider, "variant", "dark"))' in adapter
    assert '"variant": String(spider.get("variant", "dark"))' in SPIDER_BUILDER
    schema = (ROOT / "colyseus_server/src/rooms/schema/SpiderSchema.ts").read_text()
    field_order = re.findall(r"^  ([a-z_]+): ", schema.split("defineTypes(SpiderSchema, {", 1)[1], re.M)
    assert field_order[-1] == "variant", "variant must be APPENDED so older wire indices are preserved"
    assert field_order[:-1] == [
        "spider_id", "role", "position", "velocity", "facing_yaw", "hp", "max_hp",
        "aggro_actor_id", "attack_phase", "attack_seq", "rider_actor_id", "seat_position",
    ]


def test_spider_presentation_never_drops_the_creature_for_one_bad_leg() -> None:
    # FL-4790 vanishing fix. World._append_spider_source_facts skips a creature
    # whose product is empty, so any per-leg abstention deletes the whole spider
    # for that frame — the operator's "it disappears after a few frames".
    build_leg = _static_function(SPIDER_BUILDER, "_build_leg")
    assert "return {}" not in build_leg, "_build_leg must be total: clamp, substitute, keep the contact"
    solve_knee = _static_function(SPIDER_BUILDER, "_solve_knee")
    assert "INF" not in solve_knee, "the IK solver must always produce a finite knee"
    sample_up = _static_function(SPIDER_BUILDER, "_sample_body_up")
    assert "INF" not in sample_up, "a missing slope sample degrades to world up, it does not abstain"
    assert "static func _clamp_to_reach" in SPIDER_BUILDER
    # The body's own terrain support stays the single legitimate abstention.
    build = _static_function(SPIDER_BUILDER, "build_spider_product")
    assert "if not is_finite(body_ground_y):\n\t\treturn {}" in build


def test_spider_gait_follows_the_cberry22_distance_trigger_and_diagonal_pairs() -> None:
    # Reference facts recorded on FL-4790: step_distance trigger with no phase
    # clock, a 0.1+0.1 s arc through a midpoint lifted along the body up axis,
    # and a diagonal-trot pair graph. Knee pole FRONT/UP per MbaPDWfbNLo.
    assert "const DIAGONAL_PARTNER := {" in SPIDER_BUILDER
    partners = dict(re.findall(r'"([a-z_]+)": "([a-z_]+)",', SPIDER_BUILDER.split(
        "const DIAGONAL_PARTNER := {", 1)[1].split("}", 1)[0]))
    assert len(partners) == 8
    for leg, partner in partners.items():
        assert partners[partner] == leg, "the diagonal pair graph must be symmetric"
    layout = dict(re.findall(r'"leg_id": "([a-z_]+)", "lateral": [-\d.]+, "longitudinal": [-\d.]+, "group": (\d)',
                             SPIDER_BUILDER))
    assert len(layout) == 8
    assert sum(1 for group in layout.values() if group == "0") == 4, "four legs per diagonal group"
    for leg, partner in partners.items():
        assert layout[leg] == layout[partner], "diagonal opposites share a group and step together"
    # Adjacent legs must be in OPPOSITE groups, which is what makes the
    # group-exclusive rule reproduce the donor's per-leg adjacency guard.
    for left, right in (("front_left", "front_right"), ("mid_front_left", "mid_front_right"),
                        ("mid_rear_left", "mid_rear_right"), ("rear_left", "rear_right"),
                        ("front_left", "mid_front_left"), ("mid_front_left", "mid_rear_left"),
                        ("mid_rear_left", "rear_left"), ("front_right", "mid_front_right"),
                        ("mid_front_right", "mid_rear_right"), ("mid_rear_right", "rear_right")):
        assert layout[left] != layout[right], f"{left} and {right} are adjacent and must alternate"
    # Distance trigger, speed-derived swing, donor arc, and the pole direction.
    assert "const STEP_DISTANCE_BASE :=" in SPIDER_BUILDER
    assert "step_distance / speed_per_second * SWING_STANCE_RATIO" in SPIDER_BUILDER
    # The stride must aim at TOUCHDOWN, not at lift-off: without the swing-time
    # term the foot lands already behind its target and re-triggers immediately,
    # which measured as a 0.778 duty factor at the ridden speed.
    assert "speed_per_second * step_seconds + step_distance * 0.5" in SPIDER_BUILDER
    # Longer strides when faster, so the duty factor is speed-stable.
    assert "STEP_DISTANCE_BASE + speed_per_second * STEP_DISTANCE_PER_SPEED" in SPIDER_BUILDER
    assert "+ body_up * STEP_HEIGHT" in SPIDER_BUILDER
    assert "(body_up + forward * KNEE_POLE_FORWARD).normalized()" in SPIDER_BUILDER
    # Second-order dynamics is a FILTER around already-decided values only.
    second_order = _static_function(SPIDER_BUILDER, "_second_order_step")
    assert "damping / (PI * frequency)" in second_order
    assert "1.0 / (omega * omega)" in second_order
    assert "response * damping / omega" in second_order
    assert "SECOND_ORDER_STABILITY_MARGIN" in second_order
    assert "const BODY_HEIGHT_DAMPING := 0.5" in SPIDER_BUILDER
    assert "const BODY_HEIGHT_RESPONSE := -2.0" in SPIDER_BUILDER
    assert "const BODY_UP_DAMPING := 1.0" in SPIDER_BUILDER
    assert "const BODY_UP_RESPONSE := 0.0" in SPIDER_BUILDER


def test_pose_and_mover_sources_have_disjoint_owners(tmp_path) -> None:
    """Execute the shared production scalar contract, including refusal bounds."""
    import shutil
    import subprocess
    import pytest

    compiler = shutil.which("clang++")
    if compiler is None:
        pytest.skip("clang++ unavailable")
    contract = ROOT / "godot_project/render/core/contracts/source/render_source_contract.glslinc"
    code = '''#include <cstdint>
#include <cassert>
#include <initializer_list>
using uint = uint32_t;
#include "%s"
int main() {
    for (uint id : {1u, 65535u, 16777215u}) {
        uint pose = render_source_actor_pose_id(id);
        assert(render_source_is_actor_pose(pose));
        assert((pose & 0xffffffu) == id);
        for (uint kind = 1; kind <= 12; ++kind) {
            uint mover = (kind << 24) | id;
            assert(mover != pose);
            assert(!render_source_is_actor_pose(mover));
        }
    }
    assert(render_source_actor_pose_id(0u) == 0u);
    assert(render_source_actor_pose_id(0x1000000u) == 0u);
    assert(render_source_actor_pose_id(0xffffffffu) == 0u);
    for (uint material : {205u, 220u, 237u, 255u})
        assert(!source_material_owns_paper(material));
    for (uint material : {0u, 240u, 241u, 252u})
        assert(source_material_owns_paper(material));
}
''' % contract
    source = tmp_path / "source_contract.cpp"
    source.write_text(code)
    executable = tmp_path / "source_contract"
    subprocess.run([compiler, "-std=c++17", str(source), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
    capture = (ROOT / "godot_project/render/backends/perspective/capture/perspective_probe_capture.glsl").read_text()
    for producer in (capture, VSB_SHADER):
        assert "render_source_actor_pose_id(actor.identity_pose.x)" in producer
        assert "(10u << 24u) | actor.identity_pose.x" not in producer
    assert "if (render_source_is_actor_pose(record.object_id))" in PRODUCT
    assert "kind10_retained_mover" not in PRODUCT
