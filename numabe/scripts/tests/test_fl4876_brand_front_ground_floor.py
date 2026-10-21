"""FL-4876 BRAND-FRONT-01: profile-driven ground floor of commercial place fronts.

Profiles without a ``ground_floor`` object must emit the same vertices and faces
as the constants the emitter had before the keys existed. The FamilyMart and
Lawson profiles must use the new keys.
"""

import json
from pathlib import Path

import pytest

from scripts.pipeline.classic_procedural_mesh import _add_block_text, _add_box
from scripts.pipeline.compile_world_ir_fixtures import _commercial_front_ground_floor

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKS = sorted((REPO_ROOT / "assets" / "world_templates").glob("*/pack.json"))
SCALE = 5.1320023927966725


def _palette(profile):
    return {
        "body": tuple(profile["body_rgb"]),
        "primary": tuple(profile["primary_rgb"]),
        "accent": tuple(profile["accent_rgb"]),
        "awning": tuple(profile["awning_rgb"]),
        "window": tuple(profile.get("window_rgb", (45, 58, 62))),
        "door": tuple(profile.get("door_rgb", (37, 48, 52))),
        "label": tuple(profile.get("label_rgb", (239, 238, 224))),
    }


def _legacy(profile, width_m, name):
    """The hard-coded shopfront as it stood before FL-4876 BRAND-FRONT-01."""
    palette = _palette(profile)
    local_vertices, faces = [], []
    identity_sign = profile.get("identity_sign") or {}
    fascia_z_min = float(identity_sign.get("fascia_z_min_m", 2.12))
    fascia_z_max = float(identity_sign.get("fascia_z_max_m", 2.78))
    label_z_min = float(identity_sign.get("label_z_min_m", 2.24))
    label_z_max = float(identity_sign.get("label_z_max_m", 2.68))
    front_top_m = max(2.94, fascia_z_max + .12)
    _add_box(local_vertices, faces,
             (-width_m / 2, width_m / 2, -.07, .07, .05, front_top_m),
             palette["body"], SCALE)
    _add_box(local_vertices, faces,
             (-width_m / 2 + .18, width_m / 2 - .18, -.10, -.07, .22, 1.94),
             palette["window"], SCALE)
    for mullion_x in (-width_m * .25, 0.0, width_m * .25):
        _add_box(local_vertices, faces,
                 (mullion_x - .035, mullion_x + .035, -.115, -.07, .18, 2.02),
                 palette["body"], SCALE)
    _add_box(local_vertices, faces, (-.48, .48, -.125, -.07, .06, 2.04),
             palette["door"], SCALE)
    fascia_bands = profile.get("fascia_bands")
    if fascia_bands:
        top = fascia_z_max
        total_height = fascia_z_max - fascia_z_min
        total_fraction = sum(float(band["fraction"]) for band in fascia_bands)
        for band in fascia_bands:
            height = total_height * float(band["fraction"]) / total_fraction
            _add_box(local_vertices, faces,
                     (-width_m / 2 + .08, width_m / 2 - .08, -.125, -.07,
                      top - height, top), tuple(band["rgb"]), SCALE)
            top -= height
    else:
        _add_box(local_vertices, faces,
                 (-width_m / 2 + .08, width_m / 2 - .08, -.12, -.07,
                  fascia_z_min, fascia_z_max), palette["primary"], SCALE)
        _add_box(local_vertices, faces,
                 (-width_m / 2 + .08, width_m / 2 - .08, -.125, -.12,
                  fascia_z_min - .04, fascia_z_min + .04), palette["accent"], SCALE)
    _add_box(local_vertices, faces, (-width_m / 2, width_m / 2, -.58, .05, 1.94, 2.08),
             palette["awning"], SCALE)
    text = _add_block_text(
        local_vertices, faces,
        str(identity_sign.get("display_text") or name or ""),
        max_width_m=width_m * float(identity_sign.get("label_width_fraction", .96)),
        z_min_m=label_z_min, z_max_m=label_z_max, rgb=palette["label"], scale=SCALE,
        y_min_m=float(identity_sign.get("label_y_min_m", -.205)),
        y_max_m=float(identity_sign.get("label_y_max_m", -.165)))
    return local_vertices, faces, text


def _new(profile, width_m, name):
    local_vertices, faces = [], []
    out = _commercial_front_ground_floor(
        local_vertices, faces, width_m=width_m, front_profile=profile,
        palette=_palette(profile), scale=SCALE, fallback_text=name)
    return local_vertices, faces, out


def _profiles():
    rows = []
    for pack in PACKS:
        for profile in json.loads(pack.read_text(encoding="utf-8")).get(
                "place_front_profiles", []):
            if profile.get("geometry_family") == "place_entry_gate":
                continue
            rows.append(pytest.param(profile, id=f"{pack.parent.name}:{profile['id']}"))
    return rows


def _load(profile_id):
    pack = json.loads((REPO_ROOT / "assets/world_templates/tokyo/pack.json")
                      .read_text(encoding="utf-8"))
    return next(p for p in pack["place_front_profiles"] if p["id"] == profile_id)


@pytest.mark.parametrize("profile", _profiles())
@pytest.mark.parametrize("width_m", [2.6, 3.4, 5.396, 8.0])
def test_profiles_without_ground_floor_keep_their_exact_geometry(profile, width_m):
    if profile.get("ground_floor"):
        pytest.skip("profile opts in to the new keys")
    old = _legacy(profile, width_m, "Name En")
    new = _new(profile, width_m, "Name En")
    assert new[0] == old[0]
    assert new[1] == old[1]
    assert new[2]["rendered_text"] == old[2]


def test_the_generic_front_is_one_of_the_unchanged_profiles():
    ids = [p.values[0]["id"] for p in _profiles() if not p.values[0].get("ground_floor")]
    assert "tokyo.generic_small_commercial_front" in ids
    assert "tokyo.pharmacy_front" in ids


def test_familymart_ground_floor_follows_the_review():
    profile = _load("tokyo.familymart_front")
    width = 5.396
    verts, _faces, out = _new(profile, width, "FamilyMart")
    metres = [(v[0] / SCALE, v[1] / SCALE, v[2] / SCALE, v[6:9]) for v in verts]
    # no awning slab: nothing is as deep as the old 0.58 m slab
    assert min(m[1] for m in metres) > -0.58
    # flat fascia face at the declared depth
    assert out["fascia_face_o_m"] == pytest.approx(0.30)
    band = [m for m in metres if m[2] > 2.19 and abs(m[1] + 0.30) < 1e-6]
    assert band, "fascia band face missing"
    assert min(m[2] for m in band) == pytest.approx(2.2, abs=1e-6)
    assert max(m[2] for m in band) == pytest.approx(3.27, abs=1e-6)
    # entrance at the right end, 1.7 m wide
    door = [m for m in metres if tuple(m[3]) == (170, 190, 186)]
    xs = [m[0] for m in door]
    assert max(xs) - min(xs) == pytest.approx(1.7)
    assert max(xs) > width / 2 - 0.6
    assert max(m[2] for m in door) == pytest.approx(2.1)
    # brick body
    assert tuple(metres[0][3]) == (104, 62, 50)
    # glazing 0.40..1.95
    glass = [m for m in metres if tuple(m[3]) == (196, 208, 200)]
    assert min(m[2] for m in glass) == pytest.approx(0.4)
    assert max(m[2] for m in glass) == pytest.approx(1.95)


def test_lawson_profile_matches_and_has_no_min_width():
    profile = _load("tokyo.lawson_front")
    assert profile["match"]["brand_identity_contains"] == ["lawson", "ローソン"]
    assert "min_width_m" not in profile
    assert profile["primary_rgb"] == [0, 118, 190]
    assert profile["ground_floor"]["awning"] is False
    verts, _faces, out = _new(profile, 3.4, "Lawson Three F")
    assert out["rendered_text"] == "LAWSON"
    assert min(v[1] for v in verts) / SCALE > -0.58
    tokyo = json.loads((REPO_ROOT / "assets/world_templates/tokyo/pack.json")
                       .read_text(encoding="utf-8"))["place_front_profiles"]
    order = [p["id"] for p in tokyo]
    assert order.index("tokyo.lawson_front") < order.index(
        "tokyo.generic_small_commercial_front")
