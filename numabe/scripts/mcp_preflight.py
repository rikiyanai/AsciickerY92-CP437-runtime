"""
mcp_preflight.py -- Match task text to required MCP servers.

Reads a task description, matches keywords against a rule map, and reports
which MCP servers are required/optional for that task. Compares against
the currently configured servers in `docs/agent/.mcp/config.json` and
`docs/agent/.mcp.json`.

Usage:
    python3 scripts/mcp_preflight.py "fix the sprite sheet slicer"
    python3 scripts/mcp_preflight.py "render cooker.blend to xp"
    python3 scripts/mcp_preflight.py --check          # health check only
    python3 scripts/mcp_preflight.py --rules           # print rule map

Output is machine-readable JSON when --json flag is used, human-readable
otherwise.
"""

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Rule map: keyword patterns -> (required servers, optional servers)
#
# Each rule is (keywords, required, optional).
# keywords: list of substrings to match (case-insensitive, any-match).
# required: servers that MUST be enabled for this task.
# optional: servers that are useful but not blocking.
# ---------------------------------------------------------------------------

RULES = [
    # Blender tasks
    {
        "keywords": ["blend", "blender", ".blend", "render_sprite", "bake", "gmap", "akm"],
        "required": ["blender", "asciicker-dev"],
        "optional": ["serena"],
        "label": "Blender / 3D pipeline",
    },
    # XP sprite editing
    {
        "keywords": ["xp", "sprite", "rexpaint", "xp_tool", "cell", "glyph", "cp437"],
        "required": ["xp-server"],
        "optional": ["xp-bridge"],
        "label": "XP sprite editing",
    },
    # REXPaint Wine
    {
        "keywords": ["rexpaint", "wine", "rex_mcp"],
        "required": ["rex-mcp"],
        "optional": ["xp-server"],
        "label": "REXPaint Wine launcher",
    },
    # Asset wizard
    {
        "keywords": ["wizard", "asset wizard", "interactive", "wizard_mcp"],
        "required": ["asset-wizard"],
        "optional": [],
        "label": "Asset generation wizard",
    },
    # Asset pipeline verification / trace probing
    {
        "keywords": [
            "asset pipeline",
            "pipeline verification",
            "pipeline debug",
            "pipeline trace",
            "real verification",
            "step-by-step probe",
            "probe each step",
        ],
        "required": ["asset-pipeline"],
        "optional": ["xp-server", "asset-wizard"],
        "label": "Asset pipeline probing",
    },
    # Web / browser
    {
        "keywords": ["web", "browser", "emscripten", "wasm", "github pages", "chrome", "devtools"],
        "required": ["chrome-devtools"],
        "optional": ["asciicker-dev"],
        "label": "Web build / browser testing",
    },
    # Docs / library API
    {
        "keywords": ["docs", "library", "api reference", "documentation", "context7"],
        "required": ["context7"],
        "optional": [],
        "label": "Library documentation lookup",
    },
    # Engine / codebase diagnostics
    {
        "keywords": ["engine", "asciiid", "editor", "terrain", "codebase", "symbol", "lsp"],
        "required": ["asciicker-dev"],
        "optional": ["serena"],
        "label": "Engine / codebase diagnostics",
    },
    # Code navigation / LSP
    {
        "keywords": ["navigate", "symbol", "definition", "references", "lsp", "serena"],
        "required": ["serena"],
        "optional": [],
        "label": "LSP code navigation",
    },
]


def match_rules(task_text: str) -> dict:
    """Match task text against rules, return required/optional server sets."""
    text_lower = task_text.lower()
    required = set()
    optional = set()
    matched_labels = []

    for rule in RULES:
        if any(kw in text_lower for kw in rule["keywords"]):
            required.update(rule["required"])
            optional.update(rule["optional"])
            matched_labels.append(rule["label"])

    # Don't list a server as optional if it's already required
    optional -= required

    return {
        "required": sorted(required),
        "optional": sorted(optional),
        "matched": matched_labels,
    }


def load_configured_servers(repo_root: Path) -> set:
    """Load all server names from both config files."""
    servers = set()

    for config_path, key in [
        (repo_root / "docs/agent/.mcp/config.json", "servers"),
        (repo_root / "docs/agent/.mcp.json", "mcpServers"),
    ]:
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                servers.update(data.get(key, {}).keys())
            except (json.JSONDecodeError, KeyError):
                pass

    return servers


def health_check(repo_root: Path) -> list:
    """Verify config files are valid JSON and source files exist."""
    issues = []

    for config_path in [
        repo_root / "docs/agent/.mcp/config.json",
        repo_root / "docs/agent/.mcp.json",
    ]:
        if not config_path.exists():
            issues.append(f"MISSING: {config_path.relative_to(repo_root)}")
        else:
            try:
                json.loads(config_path.read_text())
            except json.JSONDecodeError as e:
                issues.append(f"INVALID JSON: {config_path.relative_to(repo_root)}: {e}")

    # Check source files for repo-local servers
    source_files = {
        "xp-server": "docs/agent/mcp/xp_mcp_server.py",
        "asset-pipeline": "docs/agent/mcp/asset_pipeline_mcp_server.py",
        "xp-bridge": "docs/agent/mcp/xp_mcp_bridge.py",
        "rex-mcp": "docs/agent/mcp/rex_mcp/server.py",
        "asset-wizard": "docs/agent/mcp/wizard_mcp_server.py",
        "asciicker-dev": "docs/agent/.mcp/servers/asciicker-dev/server.py",
    }
    for name, path in source_files.items():
        if not (repo_root / path).exists():
            issues.append(f"MISSING SOURCE: {name} -> {path}")

    return issues


def print_rules():
    """Print the rule map in human-readable format."""
    print("MCP Preflight Rule Map")
    print("=" * 60)
    for rule in RULES:
        print(f"\n  {rule['label']}")
        print(f"    Keywords:  {', '.join(rule['keywords'])}")
        print(f"    Required:  {', '.join(rule['required'])}")
        if rule["optional"]:
            print(f"    Optional:  {', '.join(rule['optional'])}")


def main():
    repo_root = Path(__file__).parent.parent

    if "--check" in sys.argv:
        issues = health_check(repo_root)
        if issues:
            print("MCP Health Check: ISSUES FOUND")
            for issue in issues:
                print(f"  - {issue}")
            sys.exit(1)
        else:
            print("MCP Health Check: ALL OK")
            sys.exit(0)

    if "--rules" in sys.argv:
        print_rules()
        sys.exit(0)

    # Collect task text from all non-flag arguments
    task_parts = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not task_parts:
        print("Usage: python3 scripts/mcp_preflight.py <task description>")
        print("       python3 scripts/mcp_preflight.py --check")
        print("       python3 scripts/mcp_preflight.py --rules")
        sys.exit(1)

    task_text = " ".join(task_parts)
    result = match_rules(task_text)
    configured = load_configured_servers(repo_root)

    # Compute gaps
    missing_required = [s for s in result["required"] if s not in configured]
    missing_optional = [s for s in result["optional"] if s not in configured]

    if "--json" in sys.argv:
        output = {
            "task": task_text,
            "matched_rules": result["matched"],
            "required": result["required"],
            "optional": result["optional"],
            "configured": sorted(configured),
            "missing_required": missing_required,
            "missing_optional": missing_optional,
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"Task: {task_text}")
        print(f"Matched: {', '.join(result['matched']) or '(none)'}")
        print()
        if result["required"]:
            print(f"Required MCPs: {', '.join(result['required'])}")
        if result["optional"]:
            print(f"Optional MCPs: {', '.join(result['optional'])}")
        if not result["required"] and not result["optional"]:
            print("No MCP servers needed for this task.")
        if missing_required:
            print(f"\nNOT CONFIGURED (required): {', '.join(missing_required)}")
            print("  -> Add to docs/agent/.mcp/config.json or enable via /mcp")
        if missing_optional:
            print(f"\nNot configured (optional): {', '.join(missing_optional)}")


if __name__ == "__main__":
    main()
