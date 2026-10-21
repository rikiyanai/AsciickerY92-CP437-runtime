"""Contract: U4 failure domains are explicit, fail closed, and replayable."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
RESOLVER = ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"


def compiled_pool_loader(source: str) -> str:
    """Return the current U4 transaction body without pinning a stale ABI signature."""
    match = re.search(
        r"static bool Fl4512LoadCompiledPoolArtifact\s*\([^)]*\)\s*\{",
        source,
    )
    assert match, "compiled-pool transaction loader is missing"
    return source[match.end() :]


def test_declared_profiles_are_captured_before_artifact_hash_rejection():
    source = RESOLVER.read_text()
    loader = compiled_pool_loader(source)
    capture = loader.index("Fl4512CaptureDeclaredMaterials(root)")
    hash_gate = loader.index('Fl4512CanonicalHashMatches(root, "artifact_sha256")')
    assert capture < hash_gate
    assert "artifact_parse_failed" in loader
    assert "POOL_LOAD_FAILED absence path" in loader


def test_rejected_declared_profile_reaches_gpu_failure_selector():
    source = RESOLVER.read_text()
    assert "g_fl4512_compiled_pool_declared_profile" in source
    assert "g_fl4512_compiled_pool_load_failed" in source
    assert "GPU selector, which emits GlyphId 1879" in source


def test_unparseable_and_invalid_declaration_use_the_universal_failure_domain():
    source = RESOLVER.read_text()
    loader = compiled_pool_loader(source)
    universal = "failure_domain = kFl4512CompiledPoolFailureDomainUniversal;"
    parse_reject = loader.index('rejection = "artifact_parse_failed";')
    invalid_declaration = loader.index('rejection = "materials_schema_invalid";')
    assert universal in loader[parse_reject : parse_reject + 256]
    assert universal in loader[invalid_declaration : invalid_declaration + 256]
    assert "Fl4512CompiledPoolFailureDomainApplies(profile_key)" in source
    assert "Fl4512CompiledPoolProfileDeclared" not in source


def test_missing_artifact_rejects_before_route_assertion_fl4512():
    """FL-4512 U4: route_dormant legacy presentation allowance is deleted."""
    source = RESOLVER.read_text()
    loader = compiled_pool_loader(source)
    # Live symbols must not remain as callable/state owners (FL-4512).
    assert "static void Fl4512EnterCompiledPoolDormancy" not in source
    assert 'return g_fl4512_compiled_pool_route_asserted ? "committed" : "route_dormant";' not in source
    assert '"artifact_missing_dormant"' not in source
    missing = loader.index('rejection = Fl4512CompiledPoolRequired()')
    window = loader[missing:missing + 400]
    assert 'artifact_missing_before_commit' in window or 'artifact_missing_required' in window
    assert "failure_domain = kFl4512CompiledPoolFailureDomainUniversal;" in window
    assert '"artifact_missing_after_commit"' in loader
    assert '"artifact_missing_required"' in loader
    assert "g_fl4512_compiled_pool_route_asserted = true;" in loader
    assert 'unasserted_fail_closed' in source


def test_capture_header_exposes_route_state_and_load_reason():
    terminal = (ROOT / "platform/terminal_gl_present.cpp").read_text()
    assert '\\"pool_route_state\\":\\"%s\\"' in terminal
    assert '\\"pool_load_reason\\":\\"%s\\"' in terminal
    assert "Fl4512CompiledPoolRouteState()" in terminal
