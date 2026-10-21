from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILD_WEB = ROOT / "build-web.sh"


def _diagnostic_block() -> str:
    source = BUILD_WEB.read_text(encoding="utf-8")
    start = source.index('case "${FL4600_DIAG:-}" in')
    end = source.index("esac\n\n# NOTE: Removed ASCIICKER_DEBUG_SPRITE_DEPTH")
    return source[start:end]


def test_asan_diagnostic_is_explicitly_opt_in() -> None:
    block = _diagnostic_block()

    assert "asan)" in block
    assert (
        'INDEX_SAFETY="-fsanitize=address -DFL4600_DIAG_ASAN -s ASSERTIONS=2 '
        '-s STACK_OVERFLOW_CHECK=1 -s STACK_SIZE=8388608"'
    ) in block
    assert 'INDEX_OPTIMIZE="-O2 -fno-exceptions -gsource-map --profiling-funcs"' in block
    assert "SAFE_HEAP=1" not in block


def test_default_web_build_keeps_production_flags_with_unset_env() -> None:
    block = _diagnostic_block()

    assert 'case "${FL4600_DIAG:-}" in' in block
    assert '\n"")\n' in block
    assert 'INDEX_SAFETY="-s ASSERTIONS=1 -s STACK_SIZE=8388608"' in block
    assert "FL-4733" in block
    assert "8 MiB production stack" in block
    assert 'INDEX_OPTIMIZE="-O2 -fno-exceptions -gsource-map --profiling-funcs"' in block


def test_unknown_diagnostic_value_fails_closed() -> None:
    block = _diagnostic_block()

    assert 'say_error "ERROR: unsupported FL4600_DIAG value: ${FL4600_DIAG}"' in block
    assert "exit 2" in block
