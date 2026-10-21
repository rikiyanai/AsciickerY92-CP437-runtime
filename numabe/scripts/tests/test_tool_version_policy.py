from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.launcher_lib import product_catalog, tool_versions


ROOT = Path(__file__).resolve().parents[2]


def test_live_tool_versions_have_one_canonical_policy() -> None:
    policy = json.loads((ROOT / "config" / "tool_versions.json").read_text())
    assert policy["schema"] == tool_versions.EXPECTED_SCHEMA
    assert policy["tools"]["godot"]["production_version"] == "4.7.2"
    assert policy["tools"]["blender"]["production_version"] == "5.2.2"
    assert policy["tools"]["mcp"]["protocol_revision"] == "2026-07-28"
    assert policy["tools"]["emscripten"]["production_version"] == "6.0.5"
    assert policy["tools"]["node"]["production_lts_major"] == 24
    assert policy["tools"]["python"]["current_feature_version"] == "3.14.7"

    raw_catalog = json.loads(
        (ROOT / "config" / "launcher_products.json").read_text()
    )
    godot = raw_catalog["products"]["godot"]
    assert godot["version_policy"] == "godot"
    assert "required_version_prefix" not in godot
    assert product_catalog.PRODUCTS["godot"]["required_version_prefix"] == "4.7.2"


def test_requirements_follow_the_mcp_policy() -> None:
    dev = (ROOT / "requirements-dev.txt").read_text()
    optional = (ROOT / "requirements-optional.txt").read_text()
    assert "mcp>=2.2.0,<3" in dev
    assert "mcp-for-blender>=2.0.4,<3" in optional
    assert "blender-mcp>=" not in optional


def test_every_project_mcp_server_uses_the_v2_sdk_surface() -> None:
    servers = (
        ROOT / "scripts" / "mcptoon_y9_server.py",
        ROOT / "pipeline-v3" / "scripts" / "workbench_mcp_server.py",
        ROOT / "pipeline-v3" / "scripts" / "rex_mcp" / "server.py",
        ROOT / "pipeline-v3" / "scripts" / "xp_mcp_server.py",
        ROOT / "scripts" / "pipeline" / "xp_mcp_server.py",
    )
    for server in servers:
        source = server.read_text()
        assert "from mcp.server import MCPServer" in source
        assert "mcp.server.fastmcp" not in source
        assert "MCPServer(" in source


def test_blender_mcp_config_uses_current_package_name_and_pin() -> None:
    config = json.loads((ROOT / ".mcp.json").read_text())
    blender = config["mcpServers"]["blender"]
    assert blender["command"] == "uvx"
    assert blender["args"] == [
        "--from", "mcp-for-blender==2.0.4", "mcp-for-blender"
    ]
    assert blender["env"]["DISABLE_TELEMETRY"] == "true"
    codex = (ROOT / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.blender]" in codex
    assert 'args = ["--from", "mcp-for-blender==2.0.4", "mcp-for-blender"]' in codex
    assert "mcp_servers.blender-mcp" not in codex
    addon = (ROOT / "addons" / "blender_mcp_addon.py").read_bytes()
    addon_text = addon.decode()
    mcp_policy = tool_versions.TOOLS["mcp"]
    assert f'ADDON_PROTOCOL_VERSION = {mcp_policy["blender_addon_protocol"]}' in addon_text
    assert hashlib.sha256(addon).hexdigest() == mcp_policy["blender_addon_sha256"]


def test_launcher_has_no_stale_live_version_literals() -> None:
    paths = (
        ROOT / "scripts" / "launcher.py",
        ROOT / "scripts" / "blender_utils.py",
        ROOT / "scripts" / "launcher_lib" / "blender_paths.py",
        ROOT / "scripts" / "launcher_lib" / "health.py",
        ROOT / "scripts" / "setup_addon.py",
        ROOT / "scripts" / "verify.py",
    )
    combined = "\n".join(path.read_text() for path in paths)
    assert 'startswith("4.5")' not in combined
    assert "Blender 4.5.app" not in combined
    assert "Application Support/Blender/4.5" not in combined
    assert '4.*|5.*) echo "unified-modern"' in (ROOT / "scripts" / "status.sh").read_text()


def test_godot_project_and_ci_follow_the_production_policy() -> None:
    project = (ROOT / "godot_project" / "project.godot").read_text()
    target_ci = (ROOT / ".github" / "workflows" / "colyseus-target-proof.yml").read_text()
    regression_ci = (
        ROOT / ".github" / "workflows" / "godot-feature-regressions.yml"
    ).read_text()
    proof = (
        ROOT / "scripts" / "proofs" / "proof_colyseus_target_readiness.js"
    ).read_text()
    assert 'config/features=PackedStringArray("4.7")' in project
    assert 'GODOT_VERSION: "4.7.2"' in target_ci
    assert 'GODOT_VERSION: "4.7.2"' in regression_ci
    assert "4.6.2" not in target_ci + regression_ci
    assert "tool_versions.json" in proof
    assert "Godot_v4.6.2" not in proof


def test_ci_toolchain_pins_follow_current_policy_and_ban_lfs() -> None:
    policy = tool_versions.TOOLS
    workflows = {
        path.name: path.read_text()
        for path in (ROOT / ".github" / "workflows").glob("*.yml")
    }
    combined = "\n".join(workflows.values())
    actions = policy["github_actions"]
    expected = {
        "actions/checkout": actions["checkout"],
        "actions/setup-node": actions["setup_node"],
        "actions/setup-python": actions["setup_python"],
        "actions/setup-java": actions["setup_java"],
        "actions/upload-artifact": actions["upload_artifact"],
        "actions/download-artifact": actions["download_artifact"],
        "actions/configure-pages": actions["configure_pages"],
        "actions/upload-pages-artifact": actions["upload_pages_artifact"],
        "actions/deploy-pages": actions["deploy_pages"],
        "actions/github-script": actions["github_script"],
        "mymindstorm/setup-emsdk": actions["setup_emsdk"],
    }
    for action, major in expected.items():
        for line in (line.strip() for line in combined.splitlines()):
            if f"uses: {action}@" in line:
                assert line.removeprefix("- ") == f"uses: {action}@v{major}"
    assert 'node-version: "22"' not in combined
    assert 'node-version: "24"' in combined
    assert "lfs: true" not in combined
    assert (ROOT / ".emscripten-version").read_text().strip() == policy["emscripten"]["production_version"]
    assert "version: '6.0.5'" in workflows["deploy-pages.yml"]
