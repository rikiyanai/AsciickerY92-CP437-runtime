#!/usr/bin/env python3
"""Verify the user-reachable FL-4512 feature-lever typed boundary.

The authoritative registry already defines eight ordinary numeric Material
Profile controls.  Their transport, native application-service handler,
compiler block, and runtime admission consumer already exist.  This test keeps
the HTML widget connected to that chain without introducing a visual-result
button, a hidden setter, and another renderer authority.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROL_WIDGET = (
    ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
)
TRANSPORT = ROOT / "web/asciiid-authoring-shell/src/transport.ts"
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
APPLICATION_SERVICE = ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp"
COMPILER = ROOT / "scripts/compile_fl4512_pools.py"
RUNTIME_RESOLVER = ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"
RENDER_RESOLVER = ROOT / "engine/render/render_resolve.cpp"


# Each tuple records the complete authored identity that must remain consistent:
# registry leaf, typed payload parameter, profile path, and paper rule family.
FEATURE_LEVERS = {
    "material.feature.silhouette.extent_enter": (
        "silhouette_extent_enter_q",
        "mesh_thresholds/silhouette/enter_q",
        "fl4512.u2.projected_feature_extent.v2",
    ),
    "material.feature.silhouette.extent_exit": (
        "silhouette_extent_exit_q",
        "mesh_thresholds/silhouette/exit_q",
        "fl4512.u2.projected_feature_extent.v2",
    ),
    "material.feature.crease.extent_enter": (
        "crease_extent_enter_q",
        "mesh_thresholds/crease/enter_q",
        "fl4512.u2.projected_feature_extent.v2",
    ),
    "material.feature.crease.extent_exit": (
        "crease_extent_exit_q",
        "mesh_thresholds/crease/exit_q",
        "fl4512.u2.projected_feature_extent.v2",
    ),
    "material.feature.suggestive.extent_enter": (
        "suggestive_extent_enter_q",
        "mesh_thresholds/suggestive_contour/enter_q",
        "fl4512.u2.suggestive_contour.v2",
    ),
    "material.feature.suggestive.extent_exit": (
        "suggestive_extent_exit_q",
        "mesh_thresholds/suggestive_contour/exit_q",
        "fl4512.u2.suggestive_contour.v2",
    ),
    "material.feature.suggestive.derivative": (
        "suggestive_derivative_enter_q16",
        "suggestive_contour/derivative_enter_q16",
        "fl4512.u2.suggestive_contour.v2",
    ),
    "material.feature.suggestive.stability": (
        "suggestive_stability_angle_min_q16",
        "suggestive_contour/stability_angle_min_q16",
        "fl4512.u2.suggestive_contour.v2",
    ),
}


def _read(path: Path) -> str:
    """Read one tracked source file so assertions inspect product authority."""

    return path.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    """Return one named source section and fail clearly when anchors drift."""

    assert start in text, f"missing source anchor: {start}"
    tail = text.split(start, 1)[1]
    assert end in tail, f"missing source anchor: {end}"
    return tail.split(end, 1)[0]


def test_exact_eight_registry_leaves_are_typed_numeric_controls() -> None:
    """Every retained feature leaf has one numeric typed-action identity."""

    registry = json.loads(_read(REGISTRY))
    leaves = {
        row["leaf_id"]: row
        for row in registry["leaves"]
        if row["leaf_id"].startswith("material.feature.")
    }
    assert set(leaves) == set(FEATURE_LEVERS)
    for leaf_id, (parameter, profile_suffix, paper_rule) in FEATURE_LEVERS.items():
        leaf = leaves[leaf_id]
        assert leaf["control_kind"] == "number"
        assert leaf["action_schema"] == (
            "asciiid.set_material_feature_parameter.v1"
        )
        assert leaf["application_service_handler"] == (
            "CommitMaterialFeatureParameter"
        )
        assert leaf["payload_schema"]["properties"]["parameter"]["const"] == parameter
        assert leaf["payload_schema"]["properties"]["value"]["type"] == "integer"
        assert leaf["write_set_template"][0].endswith(profile_suffix)
        assert leaf["paper_rule_id"] == paper_rule


def test_numeric_widget_admits_the_feature_handler_and_rejects_unknown() -> None:
    """The deleted exclusion stays gone while unknown handlers still fail shut."""

    widget = _read(CONTROL_WIDGET)
    numeric = _between(
        widget,
        '} else if (prop?.type === "integer" || prop?.type === "number") {',
        '} else if (prop?.type === "string") {',
    )
    accepted_handlers = set(
        re.findall(
            r'leaf\.application_service_handler === "([^"]+)"',
            numeric,
        )
    )
    assert "CommitMaterialFeatureParameter" in accepted_handlers
    assert "CommitMaterialScoreParameter" in accepted_handlers
    assert "CommitMaterialColorParameter" in accepted_handlers
    assert "CommitUnknownNumericParameter" not in accepted_handlers
    assert "scoreHandler || featureHandler || colorHandler" in numeric
    assert "if (scoreHandler || featureHandler) commitNativeNumber(value);" in numeric
    assert (
        '<span className="widget-contract-error">'
        "MISSING TYPED HANDLER</span>"
    ) in numeric


def test_range_and_exact_inputs_commit_only_on_gesture_completion() -> None:
    """Preview changes stay local until pointer, key, blur, and Enter completion."""

    widget = _read(CONTROL_WIDGET)
    numeric = _between(
        widget,
        '} else if (prop?.type === "integer" || prop?.type === "number") {',
        '} else if (prop?.type === "string") {',
    )
    range_input = _between(
        numeric,
        'widget = hasRange ? (',
        '<input\n        className="widget-number"',
    )
    range_preview = _between(
        range_input,
        "onChange={(event) => {",
        "onPointerUp={(event) => {",
    )
    assert "setValue(leaf.leaf_id, value, label);" in range_preview
    assert "commitNativeScalar" not in range_preview
    assert (
        "onPointerUp={(event) => {\n"
        "            commitNativeScalar(Number(event.currentTarget.value));"
    ) in range_input
    assert (
        '["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]'
    ) in range_input
    assert "onBlur={(event) => commitNativeScalar(" in numeric
    assert 'if (event.key === "Enter") {' in numeric


def test_typed_chain_reaches_compiler_and_runtime_admission() -> None:
    """All eight field identities reach the existing compiled admission owner."""

    transport = _read(TRANSPORT)
    transport_routes = _between(
        transport,
        "const featureLeafRoutes:",
        "const featureRoute =",
    )
    service = _read(APPLICATION_SERVICE)
    service_routes = _between(
        service,
        "static const FeatureLeafRoute kFeatureLeafRoutes[] = {",
        "for (const FeatureLeafRoute& route",
    )
    service_write_set = _between(
        service,
        "static const MaterialFeatureWriteSetRoute kWriteSetRoutes[] = {",
        "const MaterialFeatureWriteSetRoute* write_set_route",
    )
    for leaf_id, (parameter, _profile_suffix, _paper_rule) in FEATURE_LEVERS.items():
        assert leaf_id in transport_routes
        assert parameter in transport_routes
        assert leaf_id in service_routes
        assert parameter in service_routes
        assert parameter in service_write_set

    assert '? "set_material_feature_parameter"' in transport
    assert "commitDirectAction(sessionId, leaf, snapshot, value)" in _read(
        CONTROL_WIDGET
    )
    compiler = _read(COMPILER)
    assert 'profile.get("projected_feature_extent_admission")' in compiler
    assert '"mesh_thresholds"' in compiler
    assert '"derivative_enter_q16"' in compiler
    assert '"stability_angle_min_q16"' in compiler

    runtime = _read(RUNTIME_RESOLVER)
    assert "Fl4512LoadProjectedFeatureExtentAdmissionInto" in runtime
    assert "Fl4512ProjectedFeatureExtentAdmissionForFeatureV2" in runtime
    assert "profile.has_projected_feature_extent_admission" in runtime
    render = _read(RENDER_RESOLVER)
    assert "Fl4512ProjectedFeatureExtentAdmissionForFeatureV2(" in render
    assert "extent_q < extent_admission.admission.enter_q" in render


def test_no_direct_visual_result_authority_is_introduced() -> None:
    """The widget remains a scalar authoring surface, never a result selector."""

    widget = _read(CONTROL_WIDGET)
    numeric = _between(
        widget,
        '} else if (prop?.type === "integer" || prop?.type === "number") {',
        '} else if (prop?.type === "string") {',
    )
    forbidden = (
        "mesh_feature_progression",
        "progression_stage",
        "feature_mask",
        "panel_result",
        "glyph_id",
        "final_tuple",
        "SILHOUETTE",
        "CONTOURS",
        "SUGGESTIVE",
    )
    for token in forbidden:
        assert token not in numeric
