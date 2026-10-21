"""FL-4902 lane FA: face anchors and face/fold mark rules, offline only.

No Godot launch or visual claim. Checks:
- the anchor sidecars are exactly what scripts/derive_true_ff_face_anchors.py
  derives from the current meshes (reproducible, pinned to the mesh hash);
- sanity of the measured Pieta face: symmetry, eyes above nose above mouth,
  every anchor and fold point on its mesh triangle, the face facing forward;
- the placement rules in true_ff_face_marks.glslinc, compiled as C++
  (facing, tier rows, eye clearance, fold stroke class, half-open row cover);
- TRAIN fail-closed admission: the compiler rejects an unadmitted member and
  every authored member is an admitted entry of its mode.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from scripts import derive_true_ff_face_anchors as derive
from scripts.compile_true_ff_outline_language import validate_face_authoring

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "godot_project"
PIETA = PROJECT / "assets/world_mesh/pieta_statue_features.v1.json"
XBOT = PROJECT / "assets/actor_mesh/xbot_head_features.v1.json"
MARKS = PROJECT / "render/ascii/composition/true_ff_face_marks.glslinc"
LANGUAGE = PROJECT / "assets/glyphs/true_ff/outline_language.v1.json"
AUTHORING = PROJECT / "assets/glyphs/true_ff/outline_authoring.v1.json"


@pytest.fixture(scope="module")
def pieta():
    return json.loads(PIETA.read_text())


@pytest.fixture(scope="module")
def pieta_tris():
    return derive.load_static_asset("pieta_statue")[2]


@pytest.fixture(scope="module")
def language():
    return json.loads(LANGUAGE.read_text())


# --- derivation ------------------------------------------------------------
def test_sidecars_are_reproducible_from_the_current_meshes(pieta):
    for asset, path in (("pieta_statue", PIETA), ("xbot_head", XBOT)):
        data = derive.derive_static(asset) if asset != "xbot_head" else derive.derive_xbot_head()
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        assert text == path.read_text(), f"{path.name} is stale: rerun the derivation script"
    mesh = PROJECT / pieta["mesh"]
    assert pieta["mesh_sha256"] == derive.sha256(mesh)
    placements = json.loads(derive.LANDMARKS.read_text())["placements"]
    assert placements["pieta_statue"]["mesh"] == pieta["mesh"], "sidecar measures the mesh the game loads"


def test_pieta_face_is_symmetric_and_ordered(pieta):
    anchors = {(a["kind"], a["side"]): np.array(a["position"]) for a in pieta["anchors"]}
    assert set(anchors) == {("eye", "viewer_left"), ("eye", "viewer_right"), ("nose", "centre"),
                            ("mouth", "centre")}
    face = pieta["face"]
    normal, up, right = (np.array(face[k]) for k in ("normal", "up", "right"))
    for v in (normal, up, right):
        assert abs(np.linalg.norm(v) - 1.0) < 1e-3
    assert abs(normal @ up) < 1e-3 and abs(normal @ right) < 1e-3
    left, rgt = anchors[("eye", "viewer_left")], anchors[("eye", "viewer_right")]
    nose, mouth = anchors[("nose", "centre")], anchors[("mouth", "centre")]
    d = np.linalg.norm(rgt - left)
    assert abs(d - face["eye_distance"]) < 1e-3
    # Eyes above nose above mouth along the face's own up axis.
    assert min(left @ up, rgt @ up) > nose @ up > mouth @ up
    # Viewer-left is left along the face's right axis.
    assert left @ right < nose @ right < rgt @ right
    # Symmetry: nose and mouth near the eye midline; eyes near level.
    mid = 0.5 * (left + rgt)
    assert abs((nose - mid) @ right) / d < 0.2
    assert abs((mouth - mid) @ right) / d < 0.25
    assert abs((left - rgt) @ up) / d < 0.35
    # Proportions stay inside the bounded search windows.
    assert 0.4 <= (mid - nose) @ up / d <= 1.3
    assert 0.5 <= (nose - mouth) @ up / d <= 1.5
    checks = pieta["checks"]
    assert checks["order_along_up"] == "eyes>nose>mouth"
    assert checks["nose_distance_asymmetry"] <= derive.PARAMETERS["max_asymmetry"]
    assert checks["mirror_error"] <= derive.PARAMETERS["max_mirror_error"]
    # The two detection passes agree within half an eye distance.
    assert checks["pass_shift_over_eye_distance"] < 0.5
    # The statue faces +z (front view) and looks down (the Pieta's lowered head).
    assert face["front_view"] == "+z" and normal[2] > 0.8 and normal[1] < 0.0


def _on_triangle(tris, record):
    a, b, c = tris[record["face_index"]]
    w = np.array(record["barycentric"])
    assert w.min() > -1e-3 and abs(w.sum() - 1.0) < 1e-3
    return np.linalg.norm(w[0] * a + w[1] * b + w[2] * c - np.array(record["position"]))


def test_anchors_and_fold_points_lie_on_their_mesh_triangles(pieta, pieta_tris):
    for anchor in pieta["anchors"]:
        assert _on_triangle(pieta_tris, anchor) < 5e-3, anchor["kind"]  # 4-decimal rounding
        a, b, c = pieta_tris[anchor["face_index"]]
        n = np.cross(b - a, c - a)
        n /= np.linalg.norm(n)
        assert abs(abs(n @ np.array(anchor["normal"])) - 1.0) < 1e-3
    assert pieta["folds"], "the Pieta veil and robe carry measured folds"
    centre = np.array(pieta["face"]["centre"])
    for fold in pieta["folds"]:
        assert 2 <= len(fold["points"]) <= 16
        for point in fold["points"]:
            assert _on_triangle(pieta_tris, point) < 5e-3
        # Folds stay out of the face disc (its pits are the features).
        right, up = np.array(pieta["face"]["right"]), np.array(pieta["face"]["up"])
        nearest = min(np.hypot((np.array(p["position"]) - centre) @ right,
                               (np.array(p["position"]) - centre) @ up) for p in fold["points"])
        assert nearest > 0.5 * derive.PARAMETERS["fold_face_clearance"] * pieta["face"]["eye_distance"]


def test_player_head_has_no_locatable_features():
    data = json.loads(XBOT.read_text())
    assert data["anchors"] == [] and data["face"] is None
    assert data["head"]["volume_over_convex_hull"] > 0.95 and data["head"]["vertices"] < 32
    assert {f for row in data["omitted"] for f in row["features"]} >= {"eye", "nose", "mouth"}


# --- placement rules as C++ --------------------------------------------------
SHIM = r'''
#include <algorithm>
#include <cmath>
#include <iostream>
#include <string>
using std::max; using std::min; using std::abs; using std::ceil;
struct vec2 { float x, y; };
struct vec3 { float x, y, z; };
// GLSL float literals are double in C++.
inline float max(float a, double b) { return std::max(a, float(b)); }
'''
MAIN = r'''
int main() {
    std::string op;
    while (std::cin >> op) {
        if (op == "toward") {
            vec3 n, v; float m;
            std::cin >> n.x >> n.y >> n.z >> v.x >> v.y >> v.z >> m;
            std::cout << tff_face_toward(n, v, m) << "\n";
        } else if (op == "tier") {
            int el, er, nr, end, nose, lw, rw, gap, rows; float lx, rx;
            std::cin >> el >> er >> nr >> end >> nose >> lx >> rx >> lw >> rw >> gap >> rows;
            std::cout << tff_face_tier_fits(el, er, nr, end, nose != 0, lx, rx, lw, rw, gap, rows) << "\n";
        } else if (op == "class") {
            float dx, dy, s, h;
            std::cin >> dx >> dy >> s >> h;
            std::cout << tff_fold_class(dx, dy, s, h) << "\n";
        } else if (op == "rows") {
            float ay, by, pitch; int first, count;
            std::cin >> ay >> by >> pitch;
            tff_fold_rows(ay, by, pitch, first, count);
            std::cout << first << " " << count << "\n";
        } else if (op == "x") {
            float ax, ay, bx, by, y;
            std::cin >> ax >> ay >> bx >> by >> y;
            std::cout << tff_fold_x(ax, ay, bx, by, y) << "\n";
        } else if (op == "ordinal") {
            int row, first, count; float ay, by;
            std::cin >> row >> first >> count >> ay >> by;
            std::cout << tff_fold_ordinal(row, first, count, ay, by) << "\n";
        }
    }
}
'''


@pytest.fixture(scope="module")
def marks():
    if shutil.which("c++") is None:
        pytest.skip("no C++ compiler")
    directory = Path(tempfile.mkdtemp(prefix="fl4902-face-"))
    binary = directory / "marks"
    source = SHIM + re.sub(r"\bout (\w+) (\w+)", r"\1& \2", MARKS.read_text()) + MAIN
    built = subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", str(binary)],
                           input=source, text=True, capture_output=True)
    assert built.returncode == 0, built.stderr[-3000:]

    def query(*lines):
        out = subprocess.run([str(binary)], input="\n".join(lines) + "\n",
                             capture_output=True, text=True, check=True).stdout.split("\n")[:-1]
        return out
    yield query
    shutil.rmtree(directory, ignore_errors=True)


def test_facing_is_a_hard_gate_without_fade(marks):
    assert marks("toward 0 0 1 0 0 1 0.35", "toward 0 0 1 0 0 -1 0.35",
                 "toward 0 0 1 0.9 0 0.4359 0.35", "toward 0 0 1 0.95 0 0.3122 0.35") == ["1", "0", "1", "0"]


def test_transformed_face_lengths_cannot_change_facing_or_admit_degenerate_anchors(marks):
    assert marks("toward 0 0 0.01 0 0 25 0.35",
                 "toward 0 0 100 0.95 0 0.3122 0.35",
                 "toward 0 0 0 0 0 1 0", "toward 0 0 1 0 0 0 0",
                 "toward 0 0 1 0 0 1 1.01", "toward 0 0 1 0 0 1 -1.01") == [
                     "1", "0", "0", "0", "0", "0"]


def test_tier_rows_order_and_eye_clearance(marks):
    # eyes row 10, nose 11, mouth 12 -> full (2 rows); compact needs 1 row.
    full = "tier 10 10 11 12 1 100 160 26 30 11 2"
    assert marks(full) == ["1"]
    # nose on the eye row or the mouth row fails the nose rule.
    assert marks("tier 10 10 10 12 1 100 160 26 30 11 2", "tier 10 10 12 12 1 100 160 26 30 11 2") == ["0", "0"]
    # too few rows for the full tier, enough for compact (no nose).
    assert marks("tier 10 10 11 11 1 100 160 26 30 11 2", "tier 10 10 11 11 0 100 127 16 16 11 1") == ["0", "1"]
    # eyes on one row need half their widths plus one U+3000 between centres.
    assert marks("tier 10 10 11 12 1 100 138 26 30 11 2", "tier 10 10 11 12 1 100 139 26 30 11 2") == ["0", "1"]
    # eyes on different rows need no horizontal clearance; a mirrored face fails.
    assert marks("tier 10 11 12 13 1 100 110 26 30 11 2", "tier 10 10 11 12 1 160 100 26 30 11 2") == ["1", "0"]


def test_invalid_face_tier_metrics_cannot_sanction_marks(marks):
    assert marks("tier 10 10 11 12 1 100 160 0 30 11 2",
                 "tier 10 10 11 12 1 100 160 26 -1 11 2",
                 "tier 10 10 11 12 1 100 160 26 30 -1 2",
                 "tier 10 10 11 12 1 100 160 26 30 11 0") == ["0"] * 4


def test_fold_stroke_class_follows_the_lean(marks):
    # 1 steep, 2 falling (right going down: ヽ), 3 rising (/), 0 too shallow.
    assert marks("class 0.2 10 0.4142 2.4142", "class 10 10 0.4142 2.4142", "class -10 10 0.4142 2.4142",
                 "class 10 -10 0.4142 2.4142", "class 30 10 0.4142 2.4142") == ["1", "2", "3", "3", "0"]


def test_fold_rows_cover_each_row_once_along_a_polyline(marks):
    pitch = 17.0
    ys = [3.0, 40.5, 51.0, 120.2]  # a monotone polyline in page y
    claimed = []
    for a, b in zip(ys, ys[1:]):
        first, count = map(int, marks(f"rows {a} {b} {pitch}")[0].split())
        claimed += list(range(first, first + count))
        # reversed segment direction claims the same rows
        assert marks(f"rows {b} {a} {pitch}")[0] == f"{first} {count}"
    expected = [r for r in range(0, 10) if ys[0] <= (r + 0.5) * pitch < ys[-1]]
    assert claimed == expected
    assert float(marks("x 0 0 10 20 5")[0]) == pytest.approx(2.5)
    # ordinals count from the segment's start point in either direction
    assert marks("ordinal 3 2 4 10 80", "ordinal 3 2 4 80 10") == ["1", "2"]


def _project(points, view_scale, pitch=17.0):
    """Orthographic front view: page x right, page y down, `view_scale` px per unit."""
    return [(p[0] * view_scale, -p[1] * view_scale) for p in points]


def test_measured_pieta_face_picks_tiers_by_projected_size(marks, pieta, language):
    anchors = {(a["kind"], a["side"]): a["position"] for a in pieta["anchors"]}
    order = [("eye", "viewer_left"), ("eye", "viewer_right"), ("nose", "centre"), ("mouth", "centre")]
    widths = {}
    face = language["face_authoring"]
    advances = {}
    catalog = json.loads((PROJECT / "assets/glyphs/true_ff/saitamaar/catalog.json").read_text())
    for glyph in catalog["glyphs"]:
        advances[chr(int(glyph["codepoint"]))] = int(glyph["advance_px"])
    for tier in face["tiers"]:
        widths[tier["name"]] = [sum(advances[c] for _, t in tier[k] for c in t) for k in ("eye_left", "eye_right")]
    results = {}
    for scale in (4.0, 12.0, 30.0):
        page = _project([anchors[k] for k in order], scale)
        page = [(x + 1000.0, y + 1000.0) for x, y in page]
        rows = [int(np.floor(y / 17.0)) for _, y in page]
        fits = {}
        for tier in face["tiers"]:
            lw, rw = widths[tier["name"]]
            fits[tier["name"]] = marks("tier {} {} {} {} {} {} {} {} {} 11 {}".format(
                rows[0], rows[1], rows[2], rows[3], 1 if tier["nose"] else 0,
                page[0][0], page[1][0], lw, rw, tier["min_rows"]))[0] == "1"
        results[scale] = fits
    assert results[4.0] == {"compact": False, "full": False}   # face too small: no face at all
    assert results[30.0]["full"]                               # close up: the full madonna face


# --- TRAIN fail-closed ------------------------------------------------------------
def _admitted(language):
    return {(group["mode"], text): {} for group in language["groups"] for text in group["sequences"]}


def test_every_face_member_is_an_admitted_train_entry(language):
    face = language["face_authoring"]
    authored = json.loads(AUTHORING.read_text())["face_authoring"]
    assert face == authored, "the compiled language carries the authored block unchanged"
    admitted = _admitted(language)
    validate_face_authoring({"face_authoring": face, "budgets": language["budgets"]}, admitted)
    members = [m for tier in face["tiers"] for k in ("eye_left", "eye_right", "nose", "mouth")
               for m in (tier[k] or [])] + [m for k in ("steep", "falling", "rising") for m in face["fold"][k]]
    assert members and all(tuple(m) in admitted for m in members)


# FL-4512 fe4c9b7e6 admitted "o" into train_empty (waterfall impact rows), so the
# unadmitted empty-mode case is now a ring glyph TRAIN pages show but never admit.
@pytest.mark.parametrize("member", [["empty", "し"], ["sparse_scatter", "＝"], ["empty", "◎"]])
def test_compiler_rejects_an_unadmitted_face_member(language, member):
    face = json.loads(json.dumps(language["face_authoring"]))
    face["tiers"][-1]["nose"] = [member]
    with pytest.raises(ValueError, match="Unadmitted face sequence"):
        validate_face_authoring({"face_authoring": face, "budgets": language["budgets"]}, _admitted(language))


# --- wire constants -------------------------------------------------------------
