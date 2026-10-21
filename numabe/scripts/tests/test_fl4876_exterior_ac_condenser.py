"""FL-4876 MANUAL-WORLD-04: authored exterior AC condenser asset."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.population_asset_geometry import (  # noqa: E402
    CUSTOM_METRIC_FAMILY,
    CUSTOM_MODULES,
    build_custom_geometry,
)
from scripts.pipeline.population_representation_contract import (  # noqa: E402
    FIXTURE_CUSTOM_EXTENT_M,
)


def test_exterior_ac_is_a_distinct_authored_fixture_not_a_generic_box():
    module = "exterior_ac_condenser"
    assert module in CUSTOM_MODULES
    assert CUSTOM_METRIC_FAMILY[module] == "building_service_equipment"
    assert FIXTURE_CUSTOM_EXTENT_M[module] == (0.82, 0.34, 0.62)
    vertices, faces = build_custom_geometry(
        module, "off_white_single_fan", 1.0)
    assert len(vertices) >= 48
    assert len(faces) >= 36
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    assert max(xs) - min(xs) == 0.82
    assert max(ys) - min(ys) >= 0.34
    assert max(zs) - min(zs) == 0.72  # includes the two wall brackets


def test_exterior_ac_catalog_requires_an_explicit_building_placement():
    path = (ROOT / "artifacts/experiments/fl4813_capture_review_20260915/"
            "preserved/numabe_full_727_mapping_20260915/"
            "generic_module_catalog.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in doc["modules"]
            if row["module_id"] == "exterior_ac_condenser"]
    assert len(rows) == 1
    assert doc["module_count"] == len(doc["modules"])
    assert rows[0]["parent_feature_requirements"] == [
        "explicit authored building carrier and wall-side position"]


def test_residential_carport_is_a_distinct_four_post_asset():
    module = "residential_carport_canopy"
    assert module in CUSTOM_MODULES
    assert CUSTOM_METRIC_FAMILY[module] == "residential_frontage_structure"
    assert FIXTURE_CUSTOM_EXTENT_M[module] == (4.8, 3.6, 2.35)
    vertices, faces = build_custom_geometry(
        module, "dark_four_post_metal", 1.0)
    assert len(vertices) >= 64
    assert len(faces) >= 48
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    assert max(xs) - min(xs) == 4.8
    assert max(ys) - min(ys) == 3.6
    assert max(zs) - min(zs) == 2.35


def test_covered_waste_enclosure_is_one_curved_lid_cabinet_not_two_bins():
    module = "covered_waste_storage_enclosure"
    assert module in CUSTOM_MODULES
    assert CUSTOM_METRIC_FAMILY[module] == "waste_storage"
    assert FIXTURE_CUSTOM_EXTENT_M[module] == (1.25, 0.65, 1.05)
    vertices, faces = build_custom_geometry(
        module, "pale_grey_single_curved_lid", 1.0)
    assert len(vertices) >= 56
    assert len(faces) >= 42
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    assert max(xs) - min(xs) == 1.25
    assert max(ys) - min(ys) == 0.65
    assert max(zs) - min(zs) == 1.05


def test_covered_waste_enclosure_catalog_requires_manual_building_carrier():
    path = (ROOT / "artifacts/experiments/fl4813_capture_review_20260915/"
            "preserved/numabe_full_727_mapping_20260915/"
            "generic_module_catalog.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in doc["modules"]
            if row["module_id"] == "covered_waste_storage_enclosure"]
    assert len(rows) == 1
    assert doc["module_count"] == len(doc["modules"])
    assert rows[0]["parent_feature_requirements"] == [
        "explicit authored building carrier and wall-side position"]
