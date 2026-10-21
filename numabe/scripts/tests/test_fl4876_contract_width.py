#!/usr/bin/env python3
"""FL-4876 CONTRACT-WIDTH-01. Producer and consumer disagreed about a key.

population_representation_contract.build writes ``planned_width_m`` ONLY when
the resolved dimensions carry a ``width_m``:

    if dimensions.get("width_m") is not None:
        contract["planned_width_m"] = float(dimensions["width_m"])

lower_plan_fixtures read it unconditionally, so one building-face panel whose
module declares no width raised KeyError and took the entire fixtures lowering
down with it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIX = ROOT / "scripts" / "pipeline" / "lower_plan_fixtures.py"
CONTRACT = ROOT / "scripts" / "pipeline" / "population_representation_contract.py"


def test_the_producer_really_is_conditional():
    """Pin the asymmetry this repair exists for."""
    src = CONTRACT.read_text(encoding="utf-8")
    i = src.index('contract["planned_width_m"]')
    before = src[max(0, i - 200):i]
    assert 'if dimensions.get("width_m") is not None:' in before


def test_the_consumer_no_longer_subscripts_the_optional_key():
    src = FIX.read_text(encoding="utf-8")
    assert 'asset_contract["planned_width_m"]' not in src
    assert 'asset_contract.get("planned_width_m")' in src


def test_the_fallback_is_the_placements_own_declared_envelope():
    src = FIX.read_text(encoding="utf-8")
    i = src.index('asset_contract.get("planned_width_m")')
    block = src[i:i + 700]
    assert '"asset_physical_envelope"' in block
    assert '"longitudinal_extent_m"' in block


def test_a_placement_with_neither_is_refused_by_identity():
    """No guessed width, and no silent skip."""
    src = FIX.read_text(encoding="utf-8")
    i = src.index('asset_contract.get("planned_width_m")')
    block = src[i:i + 1100]
    assert "raise ValueError(" in block
    assert "{pid}" in block
    assert "declares no width" in block


def test_no_default_width_number_was_introduced():
    """A fallback constant would be invention. There must not be one.

    Scoped to the resolution expression itself -- from the optional read to
    the float() that consumes it. A wider window catches unrelated pre-existing
    arithmetic such as expected_metric_norms = (length, 1.0, 1.0), which is not
    a width default.
    """
    src = FIX.read_text(encoding="utf-8")
    # start at the beginning of the line that does the optional read, so both
    # assignments to _planned_w fall inside the window
    i = src.rindex("\n", 0, src.index(
        'asset_contract.get("planned_width_m")')) + 1
    j = src.index("length = float(_planned_w)", i)
    block = src[i:j]
    for bad in ("or 1.0", "or 0.5", "or 1)", "or 0", ", 1.0)", "= 1.0",
                "default"):
        assert bad not in block, (bad, block)
    # the only two sources permitted are the contract key and the envelope
    # exactly two sources: the contract key, then the declared envelope
    assert block.count("_planned_w =") == 2, block


def test_the_sibling_branch_already_used_that_envelope():
    """The fallback follows an existing convention rather than inventing one."""
    src = FIX.read_text(encoding="utf-8")
    assert 'envelope["vertical_extent_m"]' in src
