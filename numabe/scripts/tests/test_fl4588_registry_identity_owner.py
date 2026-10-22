from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "web/asciiid-authoring-shell"
APP = SHELL / "src/App.tsx"
TRANSPORT = SHELL / "src/transport.ts"
RUNNER = ROOT / "scripts/tests/fl4588_registry_identity_owner_runner.mts"


def test_registry_identity_contract_fails_closed() -> None:
    proc = subprocess.run(
        ["npx", "--yes", "tsx", str(RUNNER)],
        cwd=SHELL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])

    assert result["exact_match"] == {
        "loadedRegistrySha256": "a" * 64,
        "nativeRegistrySha256": "a" * 64,
        "matched": True,
    }
    for name in (
        "generation_split",
        "missing_native",
        "missing_loaded",
        "both_missing",
        "uppercase_native",
        "padded_loaded",
    ):
        assert result[name]["matched"] is False, result[name]
    assert result["entries_closed"] is True
    assert result["entry_split"] is False
    assert result["entry_missing"] is False


def test_app_deletes_health_hash_as_handshake_owner() -> None:
    app = APP.read_text(encoding="utf-8")
    transport = TRANSPORT.read_text(encoding="utf-8")

    assert not re.search(
        r"const\s+registryHash\s*=\s*health\?\.ui_registry_sha256",
        app,
    )
    assert re.search(
        r"const\s+registryHash\s*=\s*registryIdentity\.loadedRegistrySha256",
        app,
    )
    assert re.search(
        r"registryEntriesMatchLoadedIdentity\(\s*parsed\.registry_sha256\s*,\s*registryRows\s*\)",
        app,
    )
    assert re.search(r"parsed\.leaf_count\s*!==\s*parsed\.leaves\.length", app)
    assert re.search(
        r"parsed\.interaction_count\s*!==\s*parsed\.interactions\.length",
        app,
    )
    assert re.search(
        r"registry\?\.registry_sha256\s*,\s*report\.ui_registry_sha256",
        app,
    )
    assert 'schema: z.literal("asciiid.ui_registry.v1")' in app
    assert "registry_version: z.literal(1)" in app
    assert 'cutover_status: z.literal("authoritative")' in app
    assert app.count("z.number().int().positive()") >= 2
    assert "!registryIdentity.matched" in app
    assert len(re.findall(r"health\?\.ok\s*!==\s*true", app)) >= 2
    assert re.search(r"setSession\(null\);\s*setSnapshot\(null\);", app)
    assert "registry hash it got from\n// health" not in transport
    assert "honest disconnected presentation" not in transport


def test_handshake_lifetime_uses_stable_readiness_generation() -> None:
    app = APP.read_text(encoding="utf-8")
    handshake = app.split("// Silent package handshake.", 1)[1].split(
        "// Keep the open page in the URL hash", 1
    )[0]

    # The former whole-object dependency cancelled an in-flight successful
    # handshake on every health poll, then left bootstrapAttempt latched so no
    # snapshot session could start. Repeated equal reports must not own effect
    # lifetime; only the primitive readiness generation may do so.
    assert "[sessionId, health, registry]" not in handshake
    assert re.search(
        r"\},\s*\[\s*sessionId,\s*health\?\.ok,\s*"
        r"health\?\.product_ready,\s*health\?\.ui_registry_sha256,\s*"
        r"registry\?\.registry_sha256,\s*\]\);",
        handshake,
    )
    assert "if (!active) return;" in handshake
    assert "setSession(result.sessionId);" in handshake
    assert "bootstrapAttempt.current = registryHash;" in handshake
