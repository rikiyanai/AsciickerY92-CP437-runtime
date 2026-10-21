"""Six complete native car assets. Integrity checks, NOT runtime art acceptance."""
import base64
import hashlib
import json
import math
import struct

import numpy as np
from PIL import Image
import pytest

from scripts import compile_fl4831_cresta_native as compiler


@pytest.fixture(scope="module")
def assets():
    index = json.loads(compiler.INDEX.read_text())
    return index, {subject: json.loads((compiler.INDEX.parent/row["file"]).read_text())
                   for subject, row in index["cars"].items()}


@pytest.fixture(scope="module")
def font():
    base = compiler.ROOT/"godot_project/assets/glyphs/true_ff/saitamaar"
    catalog = json.loads((base/"catalog.json").read_text())
    atlas = np.asarray(Image.open(base/"atlas.png").convert("L"))
    return {g["codepoint"]: g for g in catalog["glyphs"]}, atlas


def test_index_covers_every_car_and_all_576_views_without_claiming_runtime_success(assets):
    index, banks = assets
    assert set(banks) == set(compiler.SUBJECTS)
    assert index["status"] == "NATIVE_ASSETS_RUNTIME_REVIEW_OPEN"
    assert index["required_runtime_review"] == ["continuous_rotation", "player_walk_around"]
    assert sum(len(bank["views"]) for bank in banks.values()) == 576
    assert sum(row["glyphs"] for row in index["cars"].values()) == 219896
    for subject, row in index["cars"].items():
        path = compiler.INDEX.parent/row["file"]
        assert row["file"] == subject+".native.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
        assert path.stat().st_size == row["bytes"]
        assert banks[subject]["subject"] == subject


@pytest.mark.parametrize("subject", compiler.SUBJECTS)
def test_every_original_native_passage_and_pixel_survives_the_complete_gif(subject, assets, font):
    index, banks = assets
    bank = banks[subject]
    metrics, atlas = font
    owner_path = compiler.ROOT/f"assets/meshes/incoming/{subject}.foreground.manual.json"
    owner = compiler.load_owner(owner_path)
    assert compiler.digest(owner_path) == bank["binding_sha256"]
    construction_path = compiler.ROOT/owner["construction"]
    assert compiler.digest(construction_path) == bank["construction_sha256"]
    assert compiler.digest(compiler.ROOT/bank["source_gif"]) == bank["source_gif_sha256"]
    if owner.get("mechanical_contract"):
        assert compiler.digest(compiler.ROOT/owner["mechanical_contract"]) == bank["mechanical_contract_sha256"]
    for sample, view in enumerate(bank["views"]):
        original_path = construction_path.parent/"frames"/f"{sample:03}.json"
        original = json.loads(original_path.read_text())
        assert view["sample"] == sample == bank["source_records"][sample]["sample"]
        assert compiler.digest(original_path) == bank["source_records"][sample]["sha256"]
        assert len(view["glyphs"]) == len(original["glyphs"])
        page = np.full((476, 640), 255, np.uint8)
        for glyph, token in zip(view["glyphs"], original["glyphs"]):
            assert glyph["source_text"] == token[0]
            assert glyph["source_position"] == token[1:3]
            assert glyph["cp"] == ord(token[0][0])
            assert glyph["source_advance"] > 0
            metric = metrics[glyph["cp"]]
            x0, y0, x1, y1 = metric["atlas_rect"]
            ix, iy, _, _ = metric["ink_bounds"]
            mask = atlas[y0:y1, x0:x1]
            x, y = token[1]+ix, token[2]+iy
            np.minimum(page[y:y+mask.shape[0], x:x+mask.shape[1]], mask,
                       out=page[y:y+mask.shape[0], x:x+mask.shape[1]])
            guides = list(struct.iter_unpack("<HBBfff", base64.b64decode(glyph["ink_guides_b64"], validate=True)))
            pixels = set(np.flatnonzero(mask.reshape(-1) < 128).tolist())
            assert len(guides) == len(pixels)
            assert {g[0] for g in guides} == pixels
            assert all(g[1] in range(5) and g[2] in (0, 1, 2, 255) for g in guides)
            assert all(math.isfinite(v) for g in guides for v in g[3:])
            assert sum(g[2] == 255 for g in guides) == glyph["ink_physical_support_refused_count"]
        approved = np.asarray(Image.open(original_path.with_suffix(".png")).convert("L"))
        assert np.array_equal(page, approved), f"{subject} native glyph/passage drift at view{sample:03}"


@pytest.mark.parametrize("subject", compiler.SUBJECTS)
def test_protected_openings_and_wheel_attachments_remain_model_specific(subject, assets):
    _, banks = assets
    bank = banks[subject]
    owner = compiler.load_owner(compiler.ROOT/f"assets/meshes/incoming/{subject}.foreground.manual.json")
    construction = json.loads((compiler.ROOT/owner["construction"]).read_text())
    assert bank["protected_openings"] == [
        {"id": q["id"], "part": 0, "points": [compiler.runtime_point(p) for p in q["points"]]}
        for q in construction["protected_openings"]]
    wheel_centers = {q["prefix"]: q["center"] for q in construction["wheel_motion"]["parts"]}
    assert bank["wheel_attachments"] == [
        {"part": i, "prefix": prefix, "center": compiler.runtime_point(wheel_centers[prefix])}
        for i, prefix in enumerate(compiler.PART_PREFIXES[1:], 1)]
    features = {f for view in bank["views"] for g in view["glyphs"] for f in g["features"]}
    if subject == "porsche911":
        assert any(f.startswith("911.") for f in features)
        assert any(".petal" in f for f in features)
    elif subject == "kei_sambar":
        assert any(f.startswith("bed.") for f in features)
        assert any(f.startswith("cab.") for f in features)
    elif subject == "teardrop1930":
        assert any(f.startswith("fender.") for f in features)
        assert any(".wire" in f for f in features)
    else:
        assert any(f.startswith("glass.") for f in features)
        assert any(".vent" in f for f in features)
    for view in bank["views"]:
        for glyph in view["glyphs"]:
            for feature, support in zip(glyph["features"], glyph["support"]):
                part = compiler.part_for_feature(feature, owner)
                if part is None:
                    assert feature in {site["id"] for site in construction["bounded_tone_sites"]}
                else:
                    assert support[0] == part
                assert support[5] == compiler.feature_motion(feature, support[0], owner)
