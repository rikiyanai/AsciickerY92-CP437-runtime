"""FL-4907 PASSES lane: offline checks of the P1-P4 Python reference and of the
GLSL/GDScript dispatch discipline. No Godot launch; not runtime evidence."""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import true_ff_feature_passes_ref as R  # noqa: E402

FEATURES = ROOT / "godot_project/render/ascii/features"
SHADERS = ["true_ff_feature_traverse_dispatch.glsl",
           "true_ff_feature_traverse_hierarchy_dispatch.glsl",
           "true_ff_feature_traverse_surface_dispatch.glsl",
           "true_ff_feature_extract.glsl", "true_ff_feature_sites.glsl",
           "true_ff_feature_visibility.glsl"]


@pytest.fixture(scope="module")
def sphere():
    v, f, n = R.uv_sphere()
    return R.mini_bank(v, f, n)


@pytest.fixture(scope="module")
def torus():
    v, f, n = R.torus()
    return R.mini_bank(v, f, n)


def loop_count(out):
    return sum(out["loops"]["is_cut"])


# ------------------------------------------------------------ facing interval
def cam_at(eye, ortho=False, target=(0.0, 0.0, 0.0)):
    return R.make_camera(eye, target, ortho=ortho)


@pytest.mark.parametrize("orthographic", [False, True])
def test_live_native_density_p1_bound_covers_p4_through_diagonal(sphere, orthographic):
    # FL-4907: the frame path passes the 11px full-space advance and 17px
    # pitch. Exercise the actual reference P1->P4 interface, not just a
    # density helper with an already chosen units value.
    cam = R.make_camera((0., 0., 4.), (0., 0., 0.), ortho=orthographic,
                        width=1000., height=1000., advance=11.)
    assert cam.row_pitch == 17.
    inst = R.single_instance(sphere)
    inv = cam.inv_view_proj
    def world_at(x, y):
        p = inv @ np.array((x / cam.width * 2. - 1., y / cam.height * 2. - 1., .5, 1.))
        return p[:3] / p[3]
    a = world_at(350., 350.)
    identity_sets = []
    for dy in (181.499, 181.5, 181.501):
        b = world_at(531.5, 350. + dy)
        plan = R.segment_density(cam, inst, a, b, 0., 1.)
        assert plan is not None
        seg = R.Segment(a, b, 0., 1., 1, R.CREASE, 7, 0,
                        level_mask=plan.level_mask)
        sites = R.segment_sites(cam, seg)
        assert plan.level_mask == 1 << 6
        assert len(sites) <= R.density_bound(plan.density, plan.level_mask, cam.quantum)
        identity_sets.append({site.q for site in sites})
    assert identity_sets == [set(range(0, 1024, 64))] * 3


def test_facing_all_front():
    cam = cam_at((0.0, 0.0, 10.0))
    assert R.facing_interval(np.zeros(3), 0.5, np.array([0.0, 0.0, 1.0]), math.radians(20),
                             0.0, 0.0, cam) == R.FRONT


def test_facing_all_back():
    cam = cam_at((0.0, 0.0, 10.0))
    assert R.facing_interval(np.zeros(3), 0.5, np.array([0.0, 0.0, -1.0]), math.radians(20),
                             0.0, 0.0, cam) == R.BACK


def test_facing_straddling():
    cam = cam_at((0.0, 0.0, 10.0))
    assert R.facing_interval(np.zeros(3), 0.5, np.array([1.0, 0.0, 0.0]), math.radians(20),
                             0.0, 0.0, cam) == R.STRADDLE


def test_facing_perspective_beta_widens_interval():
    axis = np.array([math.sin(math.radians(50)), 0.0, math.cos(math.radians(50))])
    half = math.radians(30)
    far = cam_at((0.0, 0.0, 200.0))
    near = cam_at((0.0, 0.0, 2.0))
    # theta 50 + alpha 30 = 80 < 90: front far away, beta = asin(1/2) = 30 deg near
    assert R.facing_interval(np.zeros(3), 1.0, axis, half, 0.0, 0.0, far) == R.FRONT
    assert R.facing_interval(np.zeros(3), 1.0, axis, half, 0.0, 0.0, near) == R.STRADDLE
    # displacement inflates r, normal deviation inflates alpha
    assert R.facing_interval(np.zeros(3), 1.0, axis, half, 0.0, math.radians(15), far) == R.STRADDLE
    assert R.facing_interval(np.zeros(3), 0.5, axis, math.radians(10), 0.0, 0.0,
                             cam_at((0.0, 0.0, 3.0))) == R.FRONT
    assert R.facing_interval(np.zeros(3), 0.5, axis, math.radians(10), 1.5, 0.0,
                             cam_at((0.0, 0.0, 3.0))) == R.STRADDLE


def test_facing_orthographic_uses_fixed_view_direction():
    cam = cam_at((0.0, 0.0, 1.2), ortho=True)
    # the eye sits inside the sphere, but orthographic has no eye point: beta = 0
    axis = np.array([math.sin(math.radians(50)), 0.0, math.cos(math.radians(50))])
    assert R.facing_interval(np.zeros(3), 5.0, axis, math.radians(30), 0.0, 0.0, cam) == R.FRONT
    offset = np.array([30.0, 0.0, 0.0])   # position does not change the direction
    assert R.facing_interval(offset, 5.0, axis, math.radians(30), 0.0, 0.0, cam) == R.FRONT


def test_uncertain_is_retained():
    cam = cam_at((0.0, 0.0, 10.0))
    front = np.array([0.0, 0.0, 1.0])
    assert R.facing_interval(np.zeros(3), 0.5, front, -1.0, 0.0, 0.0, cam) == R.UNKNOWN
    assert R.facing_interval(np.zeros(3), 20.0, front, 0.1, 0.0, 0.0, cam) == R.UNKNOWN
    assert R.facing_interval(np.zeros(3), 0.5, front, 0.1, -1.0, 0.0, cam) == R.UNKNOWN
    assert R.facing_interval(np.zeros(3), 0.5, front, 0.1, 0.0, -1.0, cam) == R.UNKNOWN
    assert R.facing_interval(np.zeros(3), 0.5, front, 0.1, 0.0, 0.0, cam, cone_valid=False) == R.UNKNOWN


def test_uncertain_instances_disable_facing_cull_but_require_source_crossings(sphere):
    cam = cam_at((0.3, 1.2, 4.0))
    culled = R.traverse(sphere, [R.single_instance(sphere)], cam)
    unbounded = R.traverse(sphere, [R.single_instance(sphere, max_disp=-1.0)], cam)
    assert len(culled.admitted) < len(sphere.features)
    inst = R.single_instance(sphere, max_disp=-1.0)
    crossing_edges = {i for i, f in enumerate(sphere.features)
                      if R.source_crossing(cam, inst, f) is not None}
    assert {i for _, i, _ in unbounded.admitted} == crossing_edges
    skew = np.diag([1.0, 2.0, 1.0, 1.0])
    kept = R.traverse(sphere, [R.single_instance(sphere, transform=skew)], cam_at((0.3, 1.2, 8.0)))
    # An affine cone may be widened conservatively; it need not disable all
    # facing culling. Every true crossing must nevertheless reach extraction.
    inst = R.single_instance(sphere, transform=skew)
    cam = cam_at((0.3, 1.2, 8.0))
    crossing_edges = {i for i, f in enumerate(sphere.features)
                      if R.source_crossing(cam, inst, f) is not None}
    assert crossing_edges <= {i for _, i, _ in kept.admitted}


@pytest.mark.parametrize("basis", [
    np.diag([3., .5, 2.]),
    np.array([[1., 1., 0.], [0., 1., 0.], [0., 0., 1.]]),
    np.array([[-2., .5, 0.], [0., 1., .25], [0., 0., 3.]]),
])
def test_affine_source_normal_stays_perpendicular_to_surface(sphere, basis):
    transform = np.eye(4)
    transform[:3, :3] = basis
    inst = R.single_instance(sphere, transform=transform)
    tangent0 = np.array([1., 2., 0.])
    tangent1 = np.array([0., 1., 3.])
    normal = np.cross(tangent0, tangent1)
    normal /= np.linalg.norm(normal)
    actual = R.xform_normal(inst, normal)
    assert np.linalg.norm(actual) == pytest.approx(1.)
    assert actual @ (basis @ tangent0) == pytest.approx(0., abs=1e-12)
    assert actual @ (basis @ tangent1) == pytest.approx(0., abs=1e-12)


def test_equal_column_lengths_do_not_make_a_shear_conformal(sphere):
    transform = np.eye(4)
    transform[:3, :3] = [[1., .6, 0.], [0., .8, 0.], [0., 0., 1.]]
    inst = R.single_instance(sphere, transform=transform)
    scale, condition = R.instance_metric(inst)
    assert condition > 1. or condition < 0.
    assert scale >= np.linalg.svd(transform[:3, :3], compute_uv=False)[0] - 1e-12


def test_shear_sphere_bound_contains_its_longest_axis(sphere):
    transform = np.eye(4)
    transform[:3, :3] = [[1., 1., 0.], [0., 1., 0.], [0., 0., 1.]]
    inst = R.single_instance(sphere, transform=transform)
    scale, _ = R.instance_metric(inst)
    singular = np.linalg.svd(transform[:3, :3], compute_uv=False)[0]
    assert scale >= singular - 1e-12


@pytest.mark.parametrize("scale", [.25, 1., 3.])
def test_rigid_uniform_scale_keeps_tight_sphere_and_cone(sphere, scale):
    transform = np.eye(4)
    transform[:3, :3] = scale * np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., -1.]])
    bound, condition = R.instance_metric(R.single_instance(sphere, transform=transform))
    assert condition == pytest.approx(1.)
    assert bound == pytest.approx(scale)


@pytest.mark.parametrize("ortho", [False, True])
def test_source_crossing_parameter_is_affine_coordinate_invariant(sphere, ortho):
    # Change world coordinates for both source and camera, keeping the same
    # projection. A crossing may not slide along its retained source edge.
    from dataclasses import replace
    transform = np.array([[2., .7, 0., 1.], [0., .6, .2, -2.],
                          [0., 0., 1.4, .5], [0., 0., 0., 1.]])
    cam = cam_at((.3, 1.2, 4.), ortho=ortho)
    changed = replace(cam, world_to_clip=cam.world_to_clip @ np.linalg.inv(transform),
                      eye=(transform @ np.append(cam.eye, 1.))[:3],
                      forward=transform[:3, :3] @ cam.forward)
    before = R.single_instance(sphere)
    after = R.single_instance(sphere, transform=transform)
    count = 0
    for feature in sphere.features:
        expected = R.source_crossing(cam, before, feature)
        actual = R.source_crossing(changed, after, feature)
        if expected is None:
            assert actual is None
        else:
            assert actual == pytest.approx(expected, abs=1e-12)
            count += 1
    assert count > 0


def test_sheared_visible_feature_survives_p1_frustum():
    from dataclasses import replace
    transform = np.eye(4)
    transform[:3, :3] = [[1., 1., 0.], [0., 1., 0.], [0., 0., 1.]]
    u, _, vt = np.linalg.svd(transform[:3, :3])
    normal, local = u[:, 0], vt[0]
    projection = np.array([list(normal) + [-2.5],
                           [-normal[1], normal[0], 0., 0.],
                           [0., 0., .1, .5], [0., 0., 0., 1.]])
    cam = replace(cam_at((0., 0., 4.), ortho=True), world_to_clip=projection)
    p0, p1 = .98 * local - [0., 0., .02], .98 * local + [0., 0., .02]
    packed = R.oct_encode([0., 1., 0.])
    bank = R.Bank(nodes=[R.Node(np.zeros(3), 1., np.array([0., 1., 0.]), -1.,
                               feature_count=1, flags=R.NODE_LEAF | R.NODE_BYPASS_PRESENT)],
                  features=[R.Feature(p0, 0., p1, .04, (packed,) * 4,
                                      R.CREASE, 0, 1)])
    inst = R.single_instance(bank, transform=transform)
    assert R.sphere_in_frustum(cam, R.xform_point(inst, p0), 0.)
    old_bound = max(np.linalg.norm(transform[:3, i]) for i in range(3))
    assert not R.sphere_in_frustum(cam, np.zeros(3), old_bound)
    admitted = R.traverse(bank, [inst], cam).admitted
    assert len(admitted) == 1
    assert admitted[0][:2] == (0, 0)


def test_affine_metric_bounds_sampled_sphere_and_normal_cone(sphere):
    rng = np.random.default_rng(4907)
    samples = rng.normal(size=(96, 3))
    samples /= np.linalg.norm(samples, axis=1)[:, None]
    for _ in range(64):
        basis = np.eye(3) + rng.normal(scale=.25, size=(3, 3))
        transform = np.eye(4)
        transform[:3, :3] = basis
        inst = R.single_instance(sphere, transform=transform)
        bound, condition = R.instance_metric(inst)
        assert np.max(np.linalg.norm(samples @ basis.T, axis=1)) <= bound + 1e-12
        if condition < 0.:
            assert R.xform_cone_angle(.2, condition) < 0.
            continue
        assert condition >= np.linalg.cond(basis) - 1e-12
        axis = np.array([0., 0., 1.])
        axis_w = R.xform_normal(inst, axis)
        # Sample boundary and interior directions of one retained cone.
        for angle in (0., .1, .2):
            limit = R.xform_cone_angle(angle, condition)
            for azimuth in np.linspace(0., 2. * math.pi, 16, endpoint=False):
                normal = np.array([math.sin(angle) * math.cos(azimuth),
                                   math.sin(angle) * math.sin(azimuth), math.cos(angle)])
                actual = R.angle_between(axis_w, R.xform_normal(inst, normal))
                assert actual <= limit + 3e-8


# ------------------------------------------------------------ bypass classes
@pytest.mark.parametrize("cls", [R.CREASE, R.MATERIAL, R.OPEN_BOUNDARY, R.TWO_SIDED, R.AUTHORED,
                                 R.RELIEF, R.SHORELINE])
def test_bypass_classes_never_cone_rejected(cls):
    v, f, n = R.uv_sphere(rings=12, sectors=24)
    marked = lambda e, a, b, n0, n1: cls if e % 5 == 0 else R.SMOOTH
    bank = R.mini_bank(v, f, n, classify=marked)
    cam = cam_at((0.2, 0.5, 6.0))
    tr = R.traverse(bank, [R.single_instance(bank)], cam)
    admitted_cls = [bank.features[fl].cls for _, fl, _ in tr.admitted]
    total = sum(1 for x in bank.features if x.cls == cls)
    assert admitted_cls.count(cls) == total
    assert admitted_cls.count(R.SMOOTH) < sum(1 for x in bank.features if x.cls == R.SMOOTH)


def test_admission_is_compact_and_ordered_by_instance_prefix(sphere):
    cam = cam_at((0.3, 1.2, 6.0))
    insts = [R.single_instance(sphere, object_id=3),
             R.single_instance(sphere, object_id=9, transform=np.array(
                 [[1, 0, 0, 1.5], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]]))]
    tr = R.traverse(sphere, insts, cam)
    assert None not in tr.admitted
    assert len(tr.admitted) == sum(tr.feature_counts)
    for i, (off, d) in enumerate(zip(tr.offsets, tr.feature_counts)):
        assert all(tr.admitted[k][0] == i for k in range(off, off + d))
    assert tr.rounds == sphere.max_level + 1


def test_projected_level_mask_is_unchanged_by_unrelated_capacity(sphere):
    cam = cam_at((0.3, 1.2, 6.0))
    insts = [R.single_instance(sphere, object_id=9),
             R.single_instance(sphere, object_id=3, transform=np.array(
                 [[1, 0, 0, 1.5], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]]))]
    unconstrained = R.traverse(sphere, insts, cam)
    constrained = R.traverse(sphere, insts, cam, site_capacity=0)
    # The bank has no authored required/optional tier facts.  Capacity may
    # diagnose physical page overflow, but it must not invent a coarser density,
    # delete an object, or depend on object arrival/stable-id order.
    assert constrained.overflow
    assert constrained.admitted == unconstrained.admitted
    assert constrained.feature_counts == unconstrained.feature_counts
    assert constrained.offsets == unconstrained.offsets
    baseline_sites = R.run(sphere, insts, cam)["sites"]
    constrained_sites = R.run(sphere, insts, cam, site_capacity=0)["sites"]
    assert [(s.key, s.q) for s in constrained_sites] == [(s.key, s.q) for s in baseline_sites]


def test_smooth_demand_uses_incident_face_when_source_edge_is_offscreen():
    # The smooth edge at x=10 is outside this orthographic viewport, while
    # both incident triangles reach the visible x=0 boundary.  A source-edge
    # interval would report no demand even though the live contour can cross
    # the visible face. Demand must measure that live segment, not its edge.
    verts = np.array([[10.0, 0.0, 0.0], [10.0, 1.0, 0.0],
                      [0.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    bank = R.mini_bank(verts, [(0, 1, 2), (1, 0, 3), (0, 2, 3), (1, 3, 2)],
                       np.array([[0., 0., 1.], [0., 0., -1.],
                                 [1., 0., 0.], [1., 0., 0.]]),
                       classify=lambda *_: R.SMOOTH)
    inst = R.single_instance(bank)
    cam = R.make_camera((0.0, 0.0, 5.0), (0.0, 0.0, 0.0), ortho=True)
    source_edge = next(i for i, f in enumerate(bank.features)
                       if np.allclose(f.p0, [10.0, 0.0, 0.0])
                       and np.allclose(f.p1, [10.0, 1.0, 0.0]))
    assert R.segment_density(cam, inst, bank.features[source_edge].p0,
                             bank.features[source_edge].p1,
                             bank.features[source_edge].arc0,
                             bank.features[source_edge].arc1) is None
    level_mask, count = R.feature_demand(bank, inst, cam, source_edge)
    assert level_mask != 0
    assert count > 0


def test_smooth_face_covering_viewport_is_not_rejected_by_offscreen_edges():
    vertices = np.array([[-100.0, -100.0, 0.0], [100.0, -100.0, 0.0],
                         [0.0, 100.0, 0.0]])
    bank = R.mini_bank(vertices, [(0, 1, 2), (2, 1, 0)],
                       np.array([[0., 0., 1.], [0., 0., -1.], [1., 0., 0.]]),
                       classify=lambda *_: R.SMOOTH)
    inst = R.single_instance(bank)
    cam = R.make_camera((0.0, 0.0, 5.0), (0.0, 0.0, 0.0), ortho=True)
    clips = [R.clip_of(cam, point) for point in vertices]
    assert not any(R.clip_inside(c) for c in clips)
    assert all(R.clip_source_interval(clips[i], clips[(i + 1) % 3]) is None
               for i in range(3))
    demands = [R.feature_demand(bank, inst, cam, i) for i in range(len(bank.features))]
    assert any(level_mask != 0 and count > 0 for level_mask, count in demands)


def test_smooth_no_crossing_has_no_candidate_demand():
    vertices = np.array([[-1., -1., 0.], [1., -1., 0.], [0., 1., 0.]])
    bank = R.mini_bank(vertices, [(0, 1, 2), (2, 1, 0)], np.tile([[0., 0., 1.]], (3, 1)),
                       classify=lambda *_: R.SMOOTH)
    inst = R.single_instance(bank, max_disp=-1.0)
    cam = cam_at((0., 0., 5.), ortho=True)
    tr = R.traverse(bank, [inst], cam)
    assert tr.feature_counts == [0]  # P2 rejects every edge before crossing publication
    assert tr.demand == [0]


@pytest.mark.parametrize("orthographic", [False, True])
def test_no_crossing_compaction_preserves_p2_links_and_arc_sites(orthographic):
    """Compare the old leaf admission set with the compacted production model.

    No runtime claim: this exercises the existing offline P1/P2/P4 interface.
    """
    verts, faces, normals = R.uv_sphere(rings=6, sectors=8)
    bank = R.mini_bank(verts, faces, normals)
    inst = R.single_instance(bank, max_disp=-1.)
    cam = cam_at((0.3, 0.6, 5.), ortho=orthographic)
    tr = R.traverse(bank, [inst], cam)
    legacy = []
    for instance, node_index, rejected in tr.leaves:
        node = bank.nodes[inst.node_base + node_index]
        for f in range(node.feature_first, node.feature_first + node.feature_count):
            if rejected and bank.features[inst.feature_base + f].cls == R.SMOOTH:
                continue
            mask, _ = R.feature_demand(bank, inst, cam, f)
            legacy.append((instance, f, mask))
    assert len(tr.admitted) < len(legacy)

    def extracted(admitted):
        fixed, crossings, slots = R.extract(bank, [inst], cam, admitted)
        nxt, prev = R.link(bank, [inst], cam, crossings, slots)
        loops = R.loops(crossings, nxt, prev)
        segments = fixed + R.loop_segments(crossings, nxt, loops)
        crossing_keys = [(c.source_identity, tuple(c.world), tuple(c.local), c.level_mask)
                         for c in crossings]
        segment_keys = [(s.identity, tuple(s.a), tuple(s.b), s.arc_lo, s.arc_hi, s.level_mask)
                        for s in segments]
        return crossing_keys, nxt, prev, segment_keys, R.sites(cam, segments)

    old, compact = extracted(legacy), extracted(tr.admitted)
    assert old[:4] == compact[:4]
    assert len(old[4]) == len(compact[4]) > 0
    for before, after in zip(old[4], compact[4]):
        assert vars(before).keys() == vars(after).keys()
        for field, value in vars(before).items():
            if isinstance(value, np.ndarray):
                np.testing.assert_array_equal(value, getattr(after, field))
            else:
                assert value == getattr(after, field), field


def test_fixed_partner_is_not_invented_as_a_smooth_crossing():
    vertices = np.array([[-1., -1., 0.], [1., -1., 0.], [0., 1., 0.]])
    bank = R.mini_bank(vertices, [(0, 1, 2), (2, 1, 0)],
                       np.array([[0., 0., 1.], [0., 0., -1.], [0., 0., 1.]]),
                       classify=lambda _e, a, b, *_: R.CREASE if (a, b) == (1, 2) else R.SMOOTH)
    inst = R.single_instance(bank, max_disp=-1.)
    cam = cam_at((0., 0., 5.), ortho=True)
    out = R.run(bank, [inst], cam)
    assert len(out['crossings']) == 1
    crossing = out['crossings'][0]
    assert R.feature_demand(bank, inst, cam, crossing.feature) == (0, 0)
    assert out['next'] == [R.NONE]
    assert all(s.cls == R.CREASE for s in out['segments'])


def test_smooth_level_mask_uses_crossing_span_not_face_diameter():
    # FL-4907 recorded counterexample: a long depth edge gives a face ratio
    # ~1005/100, but the live crossing span has length/projection ratio 1.
    vertices = np.array([[0., 0., 0.], [0., 0., 1000.], [100., 0., 0.]])
    normals = np.array([[math.sqrt(.19), 0., .9], [math.sqrt(.99), 0., -.1],
                        [math.sqrt(.19), 0., .9]])
    bank = R.mini_bank(vertices, [(0, 1, 2), (2, 1, 0)], normals, classify=lambda *_: R.SMOOTH)
    inst = R.single_instance(bank)
    cam = R.make_camera((5., 0., 950.), (5., 0., 900.), ortho=True)
    crossings = []
    for i, f in enumerate(bank.features):
        t = R.source_crossing(cam, inst, f)
        if t is not None:
            crossings.append((i, f.p0 + (f.p1 - f.p0) * t))
    assert len(crossings) == 2
    a, b = crossings[0][1], crossings[1][1]
    detail = R.segment_density(cam, inst, a, b, 0., float(np.linalg.norm(b - a)))
    assert detail is not None
    for edge, _ in crossings:
        level_mask, count = R.feature_demand(bank, inst, cam, edge)
        assert level_mask == detail.level_mask
        assert count == R.density_bound(detail.density, level_mask, cam.quantum)
    # Old triangle ratio selects a coarser level than the actual segment.
    clips = [R.project(cam, p) for p in vertices]
    points = [(c[:2] / c[3] * .5 + .5) * (cam.width, cam.height) for c in clips]
    diameter = max(np.linalg.norm(a - b) for a in vertices for b in vertices)
    units = max(R.site_units(b[0] - a[0], b[1] - a[1], cam.advance, cam.row_pitch)
                for a in points for b in points)
    assert R.arc_level(diameter / units, cam.quantum) > R.density_last_level(detail.level_mask)


def test_perspective_level_mask_uses_local_windows_without_duplicate_ordinals():
    density = R.Density(10.0, 10.0, 0.1, 2.0)
    mask = R.density_plan_mask(density, 1.0 / 64.0)
    levels = [level for level in range(R.DENSITY_MAX_LEVEL + 1)
              if mask & (1 << level)]
    assert len(levels) > 1
    emitted = []
    for level in levels:
        lo, hi = R.density_window(density, 1.0 / 64.0, mask, level)
        spacing = (1.0 / 64.0) * (1 << level)
        first = math.ceil(density.length * lo / spacing)
        end = math.ceil(density.length * hi / spacing)
        for j in range(first, end):
            t = j * spacing / density.length
            if 0.0 <= t < 1.0 and R.density_point_level(density, t, 1.0 / 64.0) == level:
                emitted.append((j * (1 << level), level))
    assert emitted
    assert len(emitted) == len({q for q, _ in emitted})
    assert R.density_endpoint_level(density, 1.0 / 64.0, 0.0) < \
        R.density_endpoint_level(density, 1.0 / 64.0, 1.0)


@pytest.mark.parametrize("ortho", [False, True])
def test_smooth_demand_bounds_emitted_sites_for_clipped_transformed_contours(sphere, ortho):
    # Production supports rotation + uniform scale + translation. A sheared
    # fixture would establish arithmetic parity, not physically correct normals.
    angle, scale = .23, 1.2
    transform = np.array([[scale * math.cos(angle), 0., scale * math.sin(angle), .3],
                          [0., scale, 0., 0.],
                          [-scale * math.sin(angle), 0., scale * math.cos(angle), 0.],
                          [0., 0., 0., 1.]])
    inst = R.single_instance(sphere, transform=transform, max_disp=-1.)
    for eye in ((0.1, .2, 1.05), (.3, .4, 2.), (2., .4, 3.)):
        cam = R.make_camera(eye, (0., 0., 0.), ortho=ortho, width=120., height=90.)
        out = R.run(sphere, [inst], cam)
        assert sum(out['traverse'].demand) >= len(out['candidates'])
        # Projection clips demand only. Source crossings remain P2's identity set.
        expected = {i for i, f in enumerate(sphere.features)
                    if R.source_crossing(cam, inst, f) is not None}
        assert {c.feature for c in out['crossings']} == expected
        live_segments = R.loop_segments(out['crossings'], out['next'], out['loops'])
        owners = [c for c, nxt in zip(out['crossings'], out['next']) if nxt != R.NONE]
        assert len(live_segments) == len(owners)
        for segment, owner in zip(live_segments, owners):
            level_mask, bound = R.feature_demand(sphere, inst, cam, owner.feature)
            assert segment.level_mask == level_mask
            assert len(R.sites(cam, [segment])) <= bound


def test_smooth_demand_charges_each_directed_source_span_once(sphere):
    inst = R.single_instance(sphere, max_disp=-1.)
    cam = cam_at((.3, 1.2, 4.))
    out = R.run(sphere, [inst], cam)
    expected = 0
    for c, nxt in zip(out['crossings'], out['next']):
        if nxt == R.NONE:
            continue
        n = out['crossings'][nxt]
        detail = R.segment_density(cam, inst, c.local, n.local, 0.,
                                   float(np.linalg.norm(n.local - c.local)))
        if detail is not None:
            expected += R.density_bound(detail.density, detail.level_mask, cam.quantum)
    assert expected > 0
    assert out['traverse'].demand == [expected]


def test_two_smooth_crossings_with_fixed_third_edge_stay_separate():
    vertices = np.array([[-1., -1., 0.], [1., -1., 0.], [0., 1., 0.]])
    bank = R.mini_bank(vertices, [(0, 1, 2), (2, 1, 0)],
                       np.array([[0., 0., 1.], [0., 0., -1.], [0., 0., 1.]]),
                       classify=lambda _e, a, b, *_: R.CREASE if (a, b) == (0, 2) else R.SMOOTH)
    inst = R.single_instance(bank, max_disp=-1.)
    cam = cam_at((0., 0., 5.), ortho=True)
    out = R.run(bank, [inst], cam)
    assert len(out['crossings']) == 2
    assert out['next'] == [1, 0]  # one smooth span per oppositely wound face
    assert sum(s.cls == R.CREASE for s in out['segments']) == 1
    assert sum(s.cls == R.SMOOTH for s in out['segments']) == 2


@pytest.mark.parametrize('signs', [(0., 1., -1.), (0., 1., 1.), (0., 0., 1.),
                                  (0., -1., -1.), (0., 0., -1.)])
def test_zero_vertex_demand_agrees_with_mutual_source_links(signs):
    vertices = np.array([[-1., -1., 0.], [1., -1., 0.], [0., 1., 0.]])
    normals = np.array([[math.sqrt(1. - s * s), 0., s] for s in signs])
    bank = R.mini_bank(vertices, [(0, 1, 2), (2, 1, 0)], normals, classify=lambda *_: R.SMOOTH)
    inst = R.single_instance(bank, max_disp=-1.)
    cam = cam_at((0., 0., 5.), ortho=True)
    out = R.run(bank, [inst], cam)
    for c, nxt in zip(out['crossings'], out['next']):
        level_mask, count = R.feature_demand(bank, inst, cam, c.feature)
        if nxt == R.NONE:
            assert count == 0
        else:
            n = out['crossings'][nxt]
            if np.linalg.norm(c.local - n.local) == 0.:
                assert (level_mask, count) == (0, 0)
                continue
            detail = R.segment_density(cam, inst, c.local, n.local, 0.,
                                       float(np.linalg.norm(n.local - c.local)))
            assert count == R.density_bound(detail.density, level_mask, cam.quantum)
    if signs == (0., -1., -1.):
        assert len(out['crossings']) == 2  # non-vacuous exact vertex case
        assert out['next'] == [1, 0]
        assert loop_count(out) == 1
        assert out['sites'] == []  # links are topology, never duplicate marks


def test_rejected_crossing_does_not_repair_to_another_neighbor(sphere):
    inst = R.single_instance(sphere, max_disp=-1.)
    cam = cam_at((.3, 1.2, 4.))
    out = R.run(sphere, [inst], cam)
    removed = out['crossings'][out['next'][0]].feature
    admitted = [a for a in out['traverse'].admitted if a[1] != removed]
    _, crossings, slots = R.extract(sphere, [inst], cam, admitted)
    nxt, prv = R.link(sphere, [inst], cam, crossings, slots)
    for c, n, p in zip(crossings, nxt, prv):
        expected = R.source_links(sphere, inst, cam, c.feature)
        for address, index in zip(expected, (n, p)):
            if address == removed:
                assert index == R.NONE
            elif index != R.NONE:
                assert crossings[index].feature == address


@pytest.mark.parametrize('phase', [0., .0003, .17, 8.125])
def test_near_plane_trim_preserves_unknown_phase_candidate_bound(sphere, phase):
    inst = R.single_instance(sphere)
    cam = R.make_camera((0., 0., 0.), (0., 0., -1.), width=320., height=240.)
    a, b = np.array([0., 0., .5]), np.array([.5, .2, -3.])
    length = float(np.linalg.norm(b - a))
    detail = R.segment_density(cam, inst, a, b, 0., length)
    assert detail is not None
    assert detail.arc_lo > 0.  # endpoint behind the eye was clipped, not discarded
    bound = R.density_bound(detail.density, detail.level_mask, cam.quantum)
    segment = R.Segment(a, b, phase, phase + length, 17, R.SMOOTH, 3, 0,
                        level_mask=detail.level_mask)
    sites = R.sites(cam, [segment])
    assert 0 < len(sites) <= bound
    assert all(np.isfinite([s.x, s.y, s.depth]).all() for s in sites)


# ------------------------------------------------------------ contours and loops
@pytest.mark.parametrize("eye", [(0., 0., 4.), (.00001, 0., 4.), (-.00001, 0., 4.)])
def test_source_vertex_crossings_do_not_sever_a_closed_silhouette(eye):
    vertices, faces, normals = R.uv_sphere(rings=8, sectors=16)
    bank = R.mini_bank(vertices, faces, normals)
    out = R.run(bank, [R.single_instance(bank)], cam_at(eye, ortho=True))
    assert out["crossings"]
    assert loop_count(out) == 1
    assert not any(out["loops"]["open"])
    assert R.NONE not in out["next"] and R.NONE not in out["prev"]
    assert sorted(out["next"]) == list(range(len(out["crossings"])))


def test_sphere_single_closed_loop(sphere):
    out = R.run(sphere, [R.single_instance(sphere)], cam_at((0.3, 1.2, 4.0)))
    assert len(out["crossings"]) > 0
    assert loop_count(out) == 1
    assert not any(out["loops"]["open"])
    assert R.NONE not in out["next"] and R.NONE not in out["prev"]
    # each crossing is linked once in each direction: a permutation cycle
    assert sorted(out["next"]) == list(range(len(out["crossings"])))
    assert out["loops"]["rounds"] == math.ceil(math.log2(len(out["crossings"])))


def test_sphere_crossings_lie_on_the_contour(sphere):
    cam = cam_at((0.3, 1.2, 4.0))
    out = R.run(sphere, [R.single_instance(sphere)], cam)
    for c in out["crossings"]:
        n = c.world / np.linalg.norm(c.world)
        assert abs(float(np.dot(n, (c.world - cam.eye) / np.linalg.norm(c.world - cam.eye)))) < 0.1


@pytest.mark.parametrize("ortho", [False, True])
def test_torus_two_closed_loops(torus, ortho):
    eye = (0.4, 6.0, 1.1) if not ortho else (0.2, 6.0, 0.9)
    out = R.run(torus, [R.single_instance(torus)], cam_at(eye, ortho=ortho))
    assert loop_count(out) == 2
    assert not any(out["loops"]["open"])
    cuts = {out["loops"]["cut_of"][i] for i in range(len(out["crossings"]))}
    assert len(cuts) == 2


def test_closed_loop_anchor_is_retained_source_id(sphere):
    out = R.run(sphere, [R.single_instance(sphere)], cam_at((0.3, 1.2, 4.0)))
    cs = out["crossings"]
    cut = cs[out["loops"]["cut_of"][0]]
    assert cut.stable_id == min(c.stable_id for c in cs)
    arcs = out["loops"]["arc"]
    assert arcs[out["loops"]["cut_of"][0]] == 0.0
    # arc grows along next from the cut
    i, prev_arc = out["next"][out["loops"]["cut_of"][0]], 0.0
    while i != out["loops"]["cut_of"][0]:
        assert arcs[i] > prev_arc
        prev_arc = arcs[i]
        i = out["next"][i]


# ------------------------------------------------------------ temporal stability
def sites_by_q(out):
    return {(s.key, s.q): s for s in out["sites"] if s.cls == R.SMOOTH}


def orbit_camera(angle, **kw):
    r = 4.0
    return cam_at((r * math.sin(angle), 1.2, r * math.cos(angle)), **kw)


def test_geometric_minimum_swap_cannot_recut_unchanged_source_loop(sphere):
    # Causal witness: two moving crossings trade local-y minima despite no
    # membership/link change. A geometry-keyed cut rebases every later arc.
    inst = [R.single_instance(sphere)]
    frames = [R.run(sphere, inst, orbit_camera(a)) for a in (-.19618, -.19616)]
    members, links, anchors, sites = [], [], [], []
    for out in frames:
        cs, nxt, lp = out["crossings"], out["next"], out["loops"]
        members.append({c.stable_id for c in cs})
        links.append({(c.stable_id, cs[nxt[i]].stable_id)
                      for i, c in enumerate(cs) if nxt[i] != R.NONE})
        anchors.append({cs[i].stable_id for i, cut in enumerate(lp["is_cut"]) if cut})
        sites.append(sites_by_q(out))
    assert members[0] == members[1] and links[0] == links[1]
    assert anchors[0] == anchors[1], "unchanged source loop was recut by moving geometry"
    # Continuous camera motion still moves the sites; neither a hold nor an
    # arbitrary cut is allowed to replace that geometric response.
    assert sites[0].keys() == sites[1].keys()
    distances = [math.hypot(sites[0][k].x - sites[1][k].x,
                           sites[0][k].y - sites[1][k].y) for k in sites[0]]
    assert 0.0 < max(distances) < .1


def test_stationary_smooth_span_keeps_phase_when_remote_span_changes():
    # FL-4907: unchanged membership and a stable loop cut are insufficient.
    # Only A moves. B->C, its normal, camera, density and correspondence do
    # not change, so a length change before B cannot move or delete its sites.
    cam = cam_at((0.0, 0.0, 4.0), ortho=True)
    frames = []
    for ax in (-.21, -.26):
        points = [np.array(p) for p in ((ax, 0., 0.), (0., 0., 0.), (0., .3, 0.))]
        crossings = [R.Crossing(p, p.copy(), 0, i, i + 1, 1,
                                np.array((0., 0., 1.)), level_mask=1 << 8)
                     for i, p in enumerate(points)]
        nxt, prev = [1, 2, 0], [2, 0, 1]
        lp = R.loops(crossings, nxt, prev)
        assert lp["is_cut"] == [True, False, False]
        # The previous accepted frame owns B's source-chart coordinate. The
        # production phase-history pass reconstructs B on the current source
        # surface before prefixing the rest of the component.
        segment = R.loop_segments(crossings, nxt, lp, {(1, 0, 1): .21})[1]
        np.testing.assert_array_equal(segment.a, points[1])
        np.testing.assert_array_equal(segment.b, points[2])
        frames.append({(site.key, site.q): site.source_world
                       for site in R.segment_sites(cam, segment)})
    assert frames[0], "the stationary span must exercise actual P4 sites"
    assert frames[0].keys() == frames[1].keys(), "remote length change deleted a stationary-span site"
    for identity in frames[0]:
        np.testing.assert_array_equal(frames[0][identity], frames[1][identity])


def test_production_phase_history_reconstructs_the_current_local_anchor():
    source = (FEATURES / "true_ff_feature_extract_common.glslinc").read_text()
    sample = source.split("bool phase_sample(", 1)[1].split("bool phase_sample_less", 1)[0]
    history = source.split("void history(", 1)[1].split("void emit(", 1)[0]
    assert "tff_current_geometry(inst, h.key.y" in sample
    assert "tff_current_geometry(inst, h.key.z" in sample
    assert "tff_source_local_at(inst, fa, h.anchor.x" in sample
    assert "tff_source_local_at(inst, fb, h.anchor.y" in sample
    assert "dot(c.local.xyz - anchor, old_direction)" in sample
    assert "h.component.w, h.anchor.z" in sample
    assert "h.component = uvec4(c.link.w, component_reference(jump1[i]), cam.counts.y, p.lattice.y);" in history
    # A remote geometric prefix is not part of the transported source anchor.
    assert "jump1" not in sample


@pytest.mark.parametrize("opened", [False, True])
def test_loop_anchor_and_arc_ignore_crossing_publication_order(sphere, opened):
    inst, cam = [R.single_instance(sphere)], orbit_camera(-.19618)
    admitted = R.traverse(sphere, inst, cam).admitted
    if opened:
        # A removed source crossing creates actual heads. Their topology,
        # not the closed-loop minimum, must continue to own open-chain phase.
        first = R.run(sphere, inst, cam)["crossings"][0]
        admitted = [a for a in admitted if a[1] != first.feature]
    results = []
    for publication in (admitted, list(reversed(admitted))):
        _, cs, slots = R.extract(sphere, inst, cam, publication)
        nxt, prv = R.link(sphere, inst, cam, cs, slots)
        lp = R.loops(cs, nxt, prv)
        assert any(lp["open"]) == opened
        if opened:
            assert all(lp["is_cut"][i] == (prv[i] == R.NONE) for i in range(len(cs)))
        results.append({c.stable_id: (cs[lp["cut_of"][i]].stable_id, lp["arc"][i])
                        for i, c in enumerate(cs)})
    assert results[0] == results[1]


def test_production_loop_anchor_uses_only_source_identity():
    source = (FEATURES / "true_ff_feature_extract_common.glslinc").read_text()
    assert "j.k = uvec4(crossings[i].ids.zy, candidate_phase.reference.x != 0u ? i : TFF_NONE, 0u);" in source
    assert "n.k.x < j.k.x || (n.k.x == j.k.x && n.k.y < j.k.y)" in source
    assert "opened ? c.link.y == TFF_NONE" in source
    assert "j.k.z != TFF_NONE ? i == j.k.z : all(equal(c.ids.zy, j.k.xy))" in source


def test_hash_collision_still_has_one_source_anchor(sphere):
    out = R.run(sphere, [R.single_instance(sphere)], orbit_camera(-.19618))
    cs = out["crossings"]
    # Hashes are not uniqueness guarantees. An asset-local source address is;
    # a collision must not split a closed contour into multiple arc origins.
    for crossing in cs:
        crossing.stable_id = 7
    lp = R.loops(cs, out["next"], out["prev"])
    assert sum(lp["is_cut"]) == 1
    assert cs[lp["cut_of"][0]].feature == min(c.feature for c in cs)


@pytest.mark.parametrize("ortho", [False, True])
def test_cut_stable_and_sites_continuous_under_small_motion(sphere, ortho):
    inst = [R.single_instance(sphere)]
    base = R.run(sphere, inst, orbit_camera(0.30, ortho=ortho))
    worst = []
    for delta in (2e-4, 1e-4):
        moved = R.run(sphere, inst, orbit_camera(0.30 + delta, ortho=ortho))
        cut_a = base["crossings"][base["loops"]["cut_of"][0]].stable_id
        cut_b = moved["crossings"][moved["loops"]["cut_of"][0]].stable_id
        assert cut_a == cut_b, "cut crossing changed without an event"
        a, b = sites_by_q(base), sites_by_q(moved)
        qa, qb = {q for _, q in a}, {q for _, q in b}
        common = qa & qb
        # no re-indexing: only the ordinal at the loop end may appear/vanish
        assert len(qa ^ qb) <= 1 and (not (qa ^ qb) or max(qa ^ qb) >= max(common))
        ka = {q: s for (_, q), s in a.items()}
        kb = {q: s for (_, q), s in b.items()}
        worst.append(max(math.hypot(ka[q].x - kb[q].x, ka[q].y - kb[q].y) for q in common))
    # displacement is bounded by and proportional to the camera delta
    assert worst[0] < 1.0
    assert worst[1] <= worst[0] * 0.75 + 1e-6


def test_static_camera_is_deterministic(sphere):
    inst = [R.single_instance(sphere)]
    a = R.run(sphere, inst, orbit_camera(0.3))
    b = R.run(sphere, inst, orbit_camera(0.3))
    assert [(s.key, s.q, s.x, s.y) for s in a["sites"]] == [(s.key, s.q, s.x, s.y) for s in b["sites"]]


def test_near_plane_crossing_keeps_visible_sites_and_original_arc_phase():
    cam = R.make_camera((0, 0, 2), (0, 0, 0))
    a, b = np.array([-0.2, 0., 3.]), np.array([0.2, 0., 0.])
    length = float(np.linalg.norm(b - a))
    inst = R.single_instance(R.Bank())
    detail = R.segment_density(cam, inst, a, b, 0., length)
    assert detail is not None
    segment = R.Segment(a, b, 5., 5. + length, 17, R.CREASE, 9, 0,
                        level_mask=detail.level_mask)
    ca, cb = R.project(cam, a), R.project(cam, b)
    assert ca[3] < 0 < cb[3]  # the previous implementation dropped this whole edge
    lo, hi = R.clip_source_interval(ca, cb)
    assert 0 < lo < hi <= 1
    sites = R.segment_sites(cam, segment)
    assert sites
    trimmed = R.Segment(a + (b - a) * lo, a + (b - a) * hi,
                        5. + length * lo, 5. + length * hi, 17, R.CREASE, 9, 0,
                        level_mask=detail.level_mask)
    expected = R.segment_sites(cam, trimmed)
    assert [(s.key, s.q) for s in sites] == [(s.key, s.q) for s in expected]
    for site, other in zip(sites, expected):
        assert (site.x, site.y, site.arc) == pytest.approx((other.x, other.y, other.arc))
        assert 0 <= site.x <= cam.width and 0 <= site.y <= cam.height
        assert site.arc == pytest.approx(site.q * cam.quantum)


def test_source_clipping_rejects_wholly_hidden_segments():
    assert R.clip_source_interval(np.array([2, 0, .5, 1]), np.array([3, 0, .5, 1])) is None
    assert R.clip_source_interval(np.array([0, 0, -2, 1]), np.array([0, 0, -1, 1])) is None


# ------------------------------------------------------------ nesting
def test_coarse_sites_are_a_subset_of_fine(sphere):
    inst = [R.single_instance(sphere)]
    coarse = R.make_camera((0.3, 1.2, 4.0), (0, 0, 0), advance=22.0)
    fine = R.make_camera((0.3, 1.2, 4.0), (0, 0, 0), advance=5.5)
    a = {(s.key, s.q): (s.x, s.y) for s in R.run(sphere, inst, coarse)["sites"]}
    b = {(s.key, s.q): (s.x, s.y) for s in R.run(sphere, inst, fine)["sites"]}
    assert a and len(b) > len(a)
    assert set(a) <= set(b)
    for k in a:
        assert a[k] == pytest.approx(b[k])


def test_site_spacing_at_least_one_native_advance(sphere):
    cam = R.make_camera((0.3, 1.2, 4.0), (0, 0, 0), advance=11.0)
    out = R.run(sphere, [R.single_instance(sphere)], cam)
    for seg in out["segments"]:
        ss = R.segment_sites(cam, seg)
        for s, t in zip(ss, ss[1:]):
            assert s.q % (1 << s.level) == 0
            assert R.site_units(t.x - s.x, t.y - s.y, cam.advance, cam.row_pitch) >= 0.999


def test_fixed_class_uses_chain_arc():
    feat = R.Feature(np.array([0.0, 0.0, 0.0]), 0.5, np.array([1.0, 0.0, 0.0]), 1.5,
                     (0, 0, 0, 0), R.CREASE, 42, 99, 37)
    bank = R.Bank([R.Node(np.array([0.5, 0, 0]), 0.6, np.array([0, 1.0, 0]), -1.0, 0, 0, 0, 1,
                          R.NODE_LEAF | R.NODE_BYPASS_PRESENT, 0)], [feat], [], [(R.NONE, R.NONE)])
    cam = R.make_camera((0.5, 0.0, 3.0), (0.5, 0.0, 0.0), quantum=1.0 / 64.0, advance=2.0)
    out = R.run(bank, [R.single_instance(bank)], cam)
    assert len(out["segments"]) == 1 and out["segments"][0].arc_lo == 0.5
    qs = sorted(s.q for s in out["sites"])
    assert qs and qs[0] * cam.quantum >= 0.5 and qs[-1] * cam.quantum < 1.5
    assert out["segments"][0].source[0] == 37
    assert out["sites"]
    site = out["sites"][0]
    assert site.source[0] == 37
    assert site.source_world is not None and site.source_normal is not None


# ------------------------------------------------------------ visibility
def one_site(cam, depth, object_id=7, cls=R.SMOOTH, x=100.5, y=100.5):
    return R.Site(x, y, depth, 0, 1, cls, 0, 0, object_id, (1.0, 0.0), 0.0)


def ndc_for_view_depth(cam, d):
    p = cam.eye + cam.forward * d
    c = cam.world_to_clip @ np.append(p, 1.0)
    return c[2] / c[3]


@pytest.mark.parametrize("ortho", [False, True])
def test_object_identity_never_makes_a_rear_surface_visible(ortho):
    cam = cam_at((0.0, 0.0, 5.0), ortho=ortho)
    near = ndc_for_view_depth(cam, 2.0)
    pp = R.flat_prepass(cam, depth_ndc=near, object_id=7)
    assert not R.visible(cam, pp, one_site(cam, 4.0))
    assert not R.visible(cam, pp, one_site(cam, 4.0, cls=R.CREASE))
    assert R.visible(cam, pp, one_site(cam, 1.9))
    assert R.visible(cam, pp, one_site(cam, 2.0))
    pp_other = R.flat_prepass(cam, depth_ndc=near, object_id=8)
    assert not R.visible(cam, pp_other, one_site(cam, 4.0))       # occluded by another object


def test_visibility_maps_page_pixels_onto_a_half_size_prepass():
    # P0 is ceil(page / 2) (TrueFFFrameRenderer.PREPASS_DIVISOR): page pixel
    # (101.5, 100.5) reads texel (50, 50), and depth is reconstructed at that
    # texel's centre in page pixels (101, 101), not at (50.5, 50.5).
    cam = cam_at((0.0, 0.0, 5.0))
    pp = R.flat_prepass(cam, depth_ndc=ndc_for_view_depth(cam, 2.0), object_id=8, divisor=2)
    assert pp.depth.shape == (-(-int(cam.height) // 2), -(-int(cam.width) // 2))
    assert R.prepass_texel(cam, pp, 101.5, 100.5) == (50, 50)
    assert R.texel_centre_px(cam, pp, 50, 50) == (101.0, 101.0)
    pp.object_id[50, 50] = 7
    pp.depth[50, 50] = ndc_for_view_depth(cam, 4.0)
    assert R.visible(cam, pp, one_site(cam, 4.0, x=101.5, y=100.5))
    assert R.visible(cam, pp, one_site(cam, 4.0, x=100.0, y=101.9))
    assert not R.visible(cam, pp, one_site(cam, 4.0, x=102.0, y=100.5))
    full = R.flat_prepass(cam, depth_ndc=ndc_for_view_depth(cam, 3.0), object_id=8)
    half = R.flat_prepass(cam, depth_ndc=ndc_for_view_depth(cam, 3.0), object_id=8, divisor=2)
    for d in (2.9, 3.0, 3.1):
        assert R.visible(cam, full, one_site(cam, d)) == R.visible(cam, half, one_site(cam, d))


def test_visibility_depth_bias_is_quantum_and_slope_only():
    cam = cam_at((0.0, 0.0, 5.0))
    d = 3.0
    pp = R.flat_prepass(cam, depth_ndc=ndc_for_view_depth(cam, d), object_id=8)
    assert R.visible(cam, pp, one_site(cam, d))
    assert R.visible(cam, pp, one_site(cam, d * (1 + 1e-7)))
    assert not R.visible(cam, pp, one_site(cam, d + 0.01))       # no tuned tolerance
    # a sloped pre-pass surface widens the bias by one texel footprint of slope
    h, w = pp.depth.shape
    ramp = np.array([[ndc_for_view_depth(cam, d + 0.004 * x) for x in range(w)]] * h, np.float32)
    sloped = R.Prepass(ramp, pp.object_id)
    assert R.visible(cam, sloped, one_site(cam, d + 0.004 * 100 + 0.003))
    assert not R.visible(cam, sloped, one_site(cam, d + 0.004 * 100 + 0.006))


def test_visibility_keeps_isolated_samples():
    cam = cam_at((0.0, 0.0, 5.0))
    pp = R.flat_prepass(cam, depth_ndc=ndc_for_view_depth(cam, 2.0), object_id=8)
    pp.object_id[100, 100] = 7
    pp.depth[100, 100] = ndc_for_view_depth(cam, 4.0)
    sites = [one_site(cam, 4.0, x=100.5 + dx) for dx in (-1, 0, 1)]
    kept = R.visibility(cam, pp, sites)
    assert [s.x for s in kept] == [100.5]    # the lone visible sample survives


# ------------------------------------------------------------ dispatch discipline
def test_shaders_exist_and_mirror_the_reference():
    for name in SHADERS + ["true_ff_feature_passes.glslinc", "TrueFFFeaturePasses.gd"]:
        assert (FEATURES / name).is_file(), name
    assert (FEATURES / "true_ff_feature_density.glslinc").is_file()


def test_no_runtime_array_length_and_set_zero_only():
    for p in list(FEATURES.glob("true_ff_feature_*.glsl")) + [FEATURES / "true_ff_feature_passes.glslinc"]:
        text = re.sub(r"//[^\n]*|/\*.*?\*/", "", p.read_text(encoding="utf-8"), flags=re.S)
        assert ".length()" not in text, p.name
        for m in re.finditer(r"set\s*=\s*(\d+)", text):
            assert int(m.group(1)) <= 1, p.name


def test_no_capacity_sized_dispatch():
    gd = (FEATURES / "TrueFFFeaturePasses.gd").read_text(encoding="utf-8")
    direct = re.findall(r"compute_list_dispatch\(list, ([^)]+)\)", gd)
    assert direct
    assert set(direct) <= {"1, 1, 1", "dims.x, dims.y, 1", "dimensions.x, dimensions.y, 1"}
    helper = re.search(r"func _dispatch_host_count\(.*?(?=\nfunc )", gd, re.S).group(0)
    assert "compute_list_dispatch(" in helper
    rest = gd.replace(helper, "")
    assert "compute_list_dispatch(list, candidate_slots" not in rest
    assert "compute_list_dispatch(list, int(_caps" not in rest
    assert "compute_list_dispatch_indirect(" in gd
    def declares_local_size(path, seen=None):
        seen = set() if seen is None else seen
        if path in seen:
            return False
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        if "local_size_x" in text:
            return True
        includes = re.findall(
            r'#include "res://render/ascii/features/([^\"]+)"', text
        )
        return any(declares_local_size(FEATURES / include, seen) for include in includes)

    for p in FEATURES.glob("true_ff_feature_*.glsl"):
        assert declares_local_size(p), p.name


def test_projected_density_mask_is_planned_in_p1_and_carried_to_p4():
    traverse = (FEATURES / "true_ff_feature_traverse.glslinc").read_text(encoding="utf-8")
    sites = (FEATURES / "true_ff_feature_sites.glsl").read_text(encoding="utf-8")
    assert "demand_levels" not in traverse
    assert "TFF_DETAIL_LEVELS" not in traverse
    assert "TFF_COARSE_DELTA" not in traverse
    assert "selected_delta" not in traverse
    assert "tff_feature_demand" in traverse
    assert "slot_map[inst.refs.y + f]" not in traverse
    assert "tff_set_count(TFF_SLOT_ADMITTED, run)" in traverse
    assert "tff_density_plan_mask" in traverse
    assert "s.source.w" in sites
    assert "tff_density_plan_mask" not in sites
    assert "tff_density_point_level" in sites


def test_jump_rounds_log2():
    assert [R.jump_rounds(n) for n in (0, 1, 2, 3, 4, 5, 1024, 1025)] == [0, 0, 1, 2, 2, 3, 10, 11]


def test_authoring_source_extent_is_not_viewport_fragment_length():
    page = np.array([200., 100.])
    a, b = np.array([-2., 0., .5, 1.]), np.array([.5, 0., .5, 1.])
    assert R.source_projected_extent(a, b, page) == pytest.approx(250.)
    shift = np.array([.75, 0., 0., 0.])
    assert R.source_projected_extent(a + shift, b + shift, page) == pytest.approx(250.)
    lo, hi = R.clip_source_interval(a, b)
    assert hi - lo < 1.0
    # Crossing the depth plane remains finite; no raster-derived fallback.
    a, b = np.array([-1., 0., -.5, 1.]), np.array([1., 0., .5, 1.])
    assert R.source_projected_extent(a, b, page) == pytest.approx(100.)


def _retained_pass_function(name):
    """Inspect the offline source contract; this does not execute GDScript."""
    source = (FEATURES / "TrueFFFeaturePasses.gd").read_text(encoding="utf-8")
    return source.split(f"func {name}(", 1)[1].split("\nfunc ", 1)[0]


def test_retained_publication_reconciles_before_submission_without_transform_prepare():
    prepare = _retained_pass_function("prepare")
    reconcile = _retained_pass_function("reconcile_publication")
    execute = _retained_pass_function("execute")
    assert "reconcile_publication(bank, page_facts, prepass, view_index)" in prepare
    assert reconcile.index("signature == _prepared_signature") < reconcile.index("_ensure_buffers(")
    assert reconcile.index("if _prepared_sets_valid():") < reconcile.index("_ensure_buffers(")
    for forbidden in ("configure(", "_ensure_buffers(", "_ensure_sets(", "_bank_changes(",
                      "buffer_create(", "buffer_copy(", "buffer_clear("):
        assert forbidden not in execute


def test_retained_topology_updates_do_not_upload_the_span_between_sparse_changes():
    changes = _retained_pass_function("_bank_changes")
    reconcile = _retained_pass_function("reconcile_publication")
    resize = _retained_pass_function("_resize_buffers")
    assert 'node_slots[slot] = true' in changes
    assert 'slot >= 0 and slot < int(bank["instance_count"])' in changes
    assert "slots.sort()" in changes and "nodes[-1].y == slot" in changes
    assert 'for node_range in changes["nodes"]:' in reconcile
    assert "node_low" not in changes and "node_high" not in changes
    assert '_bank_changes(bank, _history_change_serial)' in reconcile
    assert 'for clear in history_changes["clears"]:' in reconcile
    assert 'for name in ["admission_nodes", "instance_nodes", "edge_faces"]:' in resize
    assert 'name == "instance_nodes" and not old.is_valid()' in resize
    assert '_adjacency_stale = _adjacency_stale or not old.is_valid()' in resize


def test_retained_view_histories_and_source_completed_use_clock_have_distinct_owners():
    save = _retained_pass_function("_save_view_history")
    activate = _retained_pass_function("_activate_view")
    begin = _retained_pass_function("begin_source_frame")
    complete = _retained_pass_function("complete_source_frame")
    execute = _retained_pass_function("execute")
    for field in ('"admission_nodes"', '"phase_history"', '"epoch"', '"history_serial"'):
        assert field in save
    assert 'state.get("epoch", 0)' in activate
    assert 'state.get("phase_capacities", Vector2i.ZERO)' in activate
    assert '"source_tick"' in begin and "advance_source_tick" not in begin
    assert '_source_frame_submitted_views.size() != _source_frame_view_count' in complete
    assert 'bank_owner.call("advance_source_tick", tick)' in complete
    assert 'tick: int = _source_frame_tick + 1' in complete
    assert "advance_source_tick" not in execute and "_activate_view(" not in execute
    assert '_source_frame_submitted_views[view_index] = true' in execute


def _source_lifetime_owner():
    """Execute the scalar production protocol subset, not the GPU/Godot owner."""
    scope = dict(_source_frame_open=False, _source_frame_tick=-1,
                 _source_frame_history_key="", _source_frame_view_count=0,
                 _source_frame_submitted_views={}, String=str)
    for name, signature in (("begin_source_frame", "bank, view_count"),
                            ("complete_source_frame", "bank_owner, views_complete=True")):
        body = _retained_pass_function(name).split("\n", 1)[1]
        body = re.sub(r"var (\w+): \w+ =", r"\1 =", body)
        body = body.replace("history_key.is_empty()", "not history_key")
        body = body.replace("_source_frame_submitted_views.size()", "len(_source_frame_submitted_views)")
        body = re.sub(r"\btrue\b", "True", body)
        body = re.sub(r"\bfalse\b", "False", body)
        body = re.sub(r"\bnull\b", "None", body)
        globals_line = "\tglobal " + ", ".join(key for key in scope if key.startswith("_source_frame_")) + "\n"
        exec(f"def {name}({signature}):\n" + globals_line + body, scope)
    return scope


class _CompletedUseBank:
    def __init__(self):
        self.tick = 7
        self.key = "retained-owner:1"
        self.advances = []

    def has_method(self, name):
        return name in {"source_tick", "admission_history_key", "advance_source_tick"}

    def call(self, name, *args):
        if name == "source_tick":
            return self.tick
        if name == "admission_history_key":
            return self.key
        assert name == "advance_source_tick" and args == (self.tick + 1,)
        self.tick = args[0]
        self.advances.append(self.tick)
        return True


def test_source_clock_advances_once_after_all_view_consumers_and_never_per_view():
    owner = _source_lifetime_owner()
    bank = _CompletedUseBank()
    facts = {"ready": True, "source_tick": bank.tick, "admission_history_key": bank.key}
    assert owner["begin_source_frame"](facts, 2)["ready"]
    owner["_source_frame_submitted_views"][0] = True
    assert not owner["complete_source_frame"](bank)["ready"]
    assert bank.tick == 7 and not bank.advances
    assert owner["begin_source_frame"](facts, 2)["ready"]
    owner["_source_frame_submitted_views"].update({0: True, 1: True})
    assert owner["complete_source_frame"](bank)["source_tick"] == 8
    assert bank.advances == [8]
    assert not owner["complete_source_frame"](bank)["ready"]
    assert bank.advances == [8]


@pytest.mark.parametrize("failure", ["consumer", "owner", "clock"])
def test_source_clock_does_not_recycle_when_a_consumer_or_owner_is_incomplete(failure):
    owner = _source_lifetime_owner()
    bank = _CompletedUseBank()
    facts = {"ready": True, "source_tick": bank.tick, "admission_history_key": bank.key}
    assert owner["begin_source_frame"](facts, 2)["ready"]
    owner["_source_frame_submitted_views"].update({0: True, 1: True})
    if failure == "owner":
        bank.key = "replacement-owner:1"
    if failure == "clock":
        bank.tick = 8
    result = owner["complete_source_frame"](bank, failure != "consumer")
    assert not result["ready"] and not bank.advances
