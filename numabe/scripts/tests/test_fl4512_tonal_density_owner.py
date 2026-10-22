#!/usr/bin/env python3
"""Source contract for FL-4512 D1 absolute-tone ownership."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EDITOR = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
RESOLVER = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()


def test_production_defaults_do_not_erase_absolute_tone():
    assert "false,      // enable_normalize: D1 density owns absolute ink amount" in EDITOR
    assert "g_asciiid_harri_pipeline.enable_normalize = true" not in EDITOR
    assert "g_fl4131_runtime_resolver_config.normalize_src6 = 0" in RESOLVER
    assert "g_fl4131_runtime_resolver_config.normalize_src6 = 1" not in RESOLVER
