# =============================================================
# test_fl4512_feature_action_write_set.py
# =============================================================
# FL-4512 / FL-4588 focused native write-set contract for the
# 17 HTML `material.feature.*` scalar controls.
#
# WHAT THIS TEST OWNS
# -------------------
# The HTML registry is already the single source of truth for the first three
# write-set paths on every `CommitMaterialFeatureParameter` leaf. This suite
# compiles a tiny C++ driver against the real
# `AsciiidApplicationService::CommitMaterialFeatureParameter`, wires one fake
# native committer, calls the real method directly, then exact-compares the
# emitted `asciiid.action_result.v1.write_set` against the registry after
# substituting `{profile}`.
#
# WHY THIS EXISTS
# ---------------
# The service previously emitted a stale generic `/structural_features/...`
# write-set for every feature parameter. That created a second mutation-report
# owner that disagreed with the HTML registry and with downstream proof joins.
# These tests keep the native action-result formatter on the same exact owner
# as the registry.
#
# WHAT THIS TEST PROVES
# ---------------------
# 1. All 17 registry-routed parameters produce the exact first three paths the
#    registry declares, plus the same four honest revision paths.
# 2. Projected-feature controls stay under
#    `projected_feature_extent_admission`, while Active Stroke controls stay
#    under `active_stroke_topology`.
# 3. A one-fact mismatch is detected by the comparison helper.
# 4. An unmapped parameter fails shut before the fake native committer runs.
# =============================================================

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "assets" / "asciiid" / "ui" / "asciiid.ui_registry.v1.json"
PROFILE_ID = "mesh:200"
REVISION_PATHS = [
    "/state_revision",
    "/profiles/revision",
    "/compiled_pool/revision",
    "/runtime_publication/revision",
]

# One tiny C++ driver. It wires a fake material-feature committer into the real
# service, calls CommitMaterialFeatureParameter directly, and prints enough
# structured state for the Python assertions to prove whether the native write
# set matches the registry.
DRIVER = r"""
#define private public
#include "apps/asciicker-presenter/asciiid_application_service.h"
#undef private

#include <cstdio>
#include <string>

static int g_calls = 0;

int main(int argc, char** argv)
{
    const std::string parameter = argc > 1 ? argv[1] : "";

    AsciiidApplicationService service(
        []{ return AsciiidApplicationResultV1{true, "", std::string()}; },
        {}, {}, {},
        []{ return 0; },
        [](int value){ return value; });

    service.set_material_feature_parameter_committer(
        [](const std::string&, int, const std::string&, uint32_t)
            -> AsciiidMaterialFeatureCommitV1
        {
            ++g_calls;
            AsciiidMaterialFeatureCommitV1 result;
            result.ok = true;
            result.changed = true;
            result.profile_revision = 77;
            return result;
        });

    const AsciiidApplicationResultV1 result =
        service.CommitMaterialFeatureParameter(
            "mesh", 200, parameter, 123u, "golden-feature-write-set");

    std::printf("ok=%d\n", result.ok ? 1 : 0);
    std::printf("reason=%s\n", result.reason.c_str());
    std::printf("calls=%d\n", g_calls);
    std::printf("json=%s\n", result.json.c_str());
    return 0;
}
"""


def _load_feature_leaves() -> list[dict]:
    """Return the 17 registry leaves that route through the native feature owner."""

    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    leaves = [
        leaf
        for leaf in registry["leaves"]
        if leaf.get("application_service_handler") == "CommitMaterialFeatureParameter"
    ]
    assert len(leaves) == 17
    return leaves


def _expected_write_set(leaf: dict) -> list[str]:
    """Resolve one registry leaf's exact seven write-set paths for mesh:200."""

    first_three = [
        path.replace("{profile}", PROFILE_ID)
        for path in leaf["write_set_template"]
    ]
    return [*first_three, *REVISION_PATHS]


def _write_set_matches(actual: list[str], expected: list[str]) -> bool:
    """Exact ordered comparison helper used by the golden and mismatch fixtures."""

    return actual == expected


def _build_harness(tmp_path: Path) -> Path:
    """Compile the focused C++ driver against the real application service."""

    driver = tmp_path / "feature_action_write_set_driver.cpp"
    driver.write_text(DRIVER, encoding="utf-8")

    cjson_object = tmp_path / "cJSON.o"
    subprocess.run(
        [
            "cc",
            "-Iengine/third_party/cjson",
            "-c",
            "engine/third_party/cjson/cJSON.c",
            "-o",
            str(cjson_object),
        ],
        cwd=ROOT,
        check=True,
    )

    binary = tmp_path / "feature_action_write_set_harness"
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-I.",
            str(driver),
            "apps/asciicker-presenter/asciiid_application_service.cpp",
            "apps/asciicker-presenter/asciiid_counterfactual.cpp",
            str(cjson_object),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=True,
    )
    return binary


@pytest.fixture(scope="module")
def harness_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the focused native harness once for the whole suite."""

    return _build_harness(tmp_path_factory.mktemp("fl4512-feature-write-set"))


def _run(binary: Path, parameter: str) -> dict[str, str]:
    """Execute the harness once and decode its simple `key=value` output."""

    completed = subprocess.run(
        [str(binary), parameter],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result: dict[str, str] = {}
    for raw_line in completed.stdout.splitlines():
        key, value = raw_line.split("=", 1)
        result[key] = value
    return result


def test_every_feature_parameter_leaf_matches_the_registry_write_set(
    harness_binary: Path,
) -> None:
    """All 17 feature leaves must emit the registry-owned seven-path write set."""

    leaves = _load_feature_leaves()
    for leaf in leaves:
        parameter = leaf["payload_schema"]["properties"]["parameter"]["const"]
        observed = _run(harness_binary, parameter)
        assert observed["ok"] == "1"
        assert observed["reason"] == ""
        assert observed["calls"] == "1"

        payload = json.loads(observed["json"])
        expected = _expected_write_set(leaf)
        assert payload["write_set"][:3] == expected[:3]
        assert payload["write_set"][3:] == REVISION_PATHS
        assert payload["write_set"] == expected

        # The family split is the whole point of this cut: projected feature
        # thresholds must stay on projected_feature_extent_admission, while
        # Active Stroke controls must stay on active_stroke_topology.
        if ".active_stroke." in leaf["leaf_id"]:
            assert expected[1].endswith("/active_stroke_topology")
            assert expected[2].endswith("/active_stroke_topology")
        else:
            assert expected[1].endswith("/projected_feature_extent_admission")
            assert expected[2].endswith("/projected_feature_extent_admission")


def test_one_fact_mismatch_fixtures_fail_exact_comparison(
    harness_binary: Path,
) -> None:
    """A single wrong path must make the exact comparison helper fail."""

    leaf = _load_feature_leaves()[0]
    parameter = leaf["payload_schema"]["properties"]["parameter"]["const"]
    observed = _run(harness_binary, parameter)
    payload = json.loads(observed["json"])
    expected = _expected_write_set(leaf)

    wrong_feature_path = list(expected)
    wrong_feature_path[0] = wrong_feature_path[0] + "/wrong"
    assert not _write_set_matches(payload["write_set"], wrong_feature_path)

    wrong_revision_path = list(expected)
    wrong_revision_path[3] = "/wrong_revision_path"
    assert not _write_set_matches(payload["write_set"], wrong_revision_path)


def test_unmapped_parameter_fails_before_the_fake_native_committer_runs(
    harness_binary: Path,
) -> None:
    """An unmapped parameter must fail shut with zero committer calls."""

    observed = _run(harness_binary, "unknown_feature_parameter")
    assert observed["ok"] == "0"
    assert observed["reason"] == "material_feature_parameter_write_set_unmapped"
    assert observed["calls"] == "0"
    assert observed["json"] == ""
