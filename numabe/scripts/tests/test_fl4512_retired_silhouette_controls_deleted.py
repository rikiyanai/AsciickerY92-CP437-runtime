#!/usr/bin/env python3
"""Guard the deletion of three retired silhouette authoring controls.

The visibility-lattice producer no longer consumes the old depth-jump,
normal-dot, and support-fraction probe thresholds.  Keeping editor addresses
for those values would present controls that cannot affect the product.  This
test checks every allowed authoring boundary while retaining the projected
extent, suggestive-contour, and Active-Stroke controls that remain live.
"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATHS = {
    "native_editor": ROOT / "apps/asciicker-presenter/asciiid.cpp",
    "application_service": ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp",
    "html_transport": ROOT / "web/asciiid-authoring-shell/src/transport.ts",
    "ui_registry": (
        ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
    ),
    "action_result_schema": ROOT / "schemas/asciiid.action_result.v1.json",
    "authoring_snapshot_schema": (
        ROOT / "schemas/asciiid.authoring_snapshot.v1.json"
    ),
    "current_ui_inventory": (
        ROOT
        / "docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv"
    ),
    "migration_matrix": (
        ROOT
        / "docs/research/ascii/verification/fl4260/HTML_UI_MIGRATION_MATRIX.csv"
    ),
}
SOURCE_TEXT = {
    name: path.read_text(encoding="utf-8") for name, path in PATHS.items()
}
REGISTRY = json.loads(
    (
        ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
    ).read_text(encoding="utf-8")
)

RETIRED_LEAVES = {
    "material.feature.silhouette.depth_jump",
    "material.feature.silhouette.normal_dot",
    "material.feature.silhouette.support",
}
RETIRED_PARAMETERS = {
    "silhouette_depth_jump_height_q8_8",
    "silhouette_normal_dot_max_q1_15",
    "silhouette_support_fraction_min_q0_16",
}
PRESERVED_LEAVES = {
    "material.feature.silhouette.extent_enter",
    "material.feature.silhouette.extent_exit",
    "material.feature.crease.extent_enter",
    "material.feature.crease.extent_exit",
    "material.feature.suggestive.derivative",
    "material.feature.suggestive.stability",
}


def test_retired_addresses_are_absent_from_every_authoring_boundary():
    """No native, HTML, service, and schema text can name a retired address."""

    for boundary, text in SOURCE_TEXT.items():
        for token in RETIRED_LEAVES | RETIRED_PARAMETERS:
            assert token not in text, f"{boundary} retained {token}"


def test_ui_registry_omits_retired_leaves_and_tool_graph_addresses():
    """The authoritative registry cannot render nor dispatch retired leaves."""

    leaves = {row["leaf_id"] for row in REGISTRY["leaves"]}
    assert RETIRED_LEAVES.isdisjoint(leaves)
    active_tool_graph = json.dumps(
        REGISTRY["active_tool_graph"], sort_keys=True
    )
    for leaf_id in RETIRED_LEAVES:
        assert leaf_id not in active_tool_graph


def test_real_feature_controls_remain_on_the_normal_typed_commit_path():
    """Extent and suggestive controls keep their typed application service."""

    leaves = {row["leaf_id"]: row for row in REGISTRY["leaves"]}
    assert PRESERVED_LEAVES <= set(leaves)
    for leaf_id in PRESERVED_LEAVES:
        row = leaves[leaf_id]
        assert row["action_schema"] == (
            "asciiid.set_material_feature_parameter.v1"
        )
        assert row["application_service_handler"] == (
            "CommitMaterialFeatureParameter"
        )


def test_active_stroke_snapshot_fields_remain_published_and_typed():
    """The deletion is narrow: live continuity controls remain observable."""
    # Continuity parameters retired from live C++ in 93437d08e (profile
    # controls cutover); the snapshot schema remains their published owner.
    snapshot_schema = SOURCE_TEXT["authoring_snapshot_schema"]
    for parameter in (
        "active_endpoint_projected_tolerance_q16_16",
        "active_endpoint_object_tolerance_q24_8",
        "active_depth_link_max_height_q8_8",
        "active_degree2_tangent_dot_min_q1_15",
        "active_smoothing_iterations",
        "active_persistence_projected_max_q16_16",
        "active_persistence_object_max_q24_8",
        "active_arc_landmark_count",
        "active_retire_after_missing_frames",
    ):
        assert parameter in snapshot_schema
