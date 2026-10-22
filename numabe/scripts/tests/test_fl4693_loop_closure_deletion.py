#!/usr/bin/env python3
"""FL-4693 P4 deletion contract for the unavailable loop-closure path."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / "engine/render/fl4512_spacetime_track_owner.h"
PRESENTER = ROOT / "platform/terminal_gl_present.cpp"


def test_p4_deletes_the_dormant_runtime_loop_owner() -> None:
    owner = OWNER.read_text(encoding="utf-8")
    presenter = PRESENTER.read_text(encoding="utf-8")

    for retired in (
        "kFl4512LambdaLoopV2",
        "is_loop_closing_frame",
        "loop_anchor_frame",
        "loop_anchor_nodes",
        "loop_cost",
        "prior_loop_anchor_frame",
    ):
        assert retired not in owner

    for retired in (
        "is_loop_closing_frame",
        "prior_loop_anchor_frame",
        "spacetime_loop_cost",
        '"loop":%u',
    ):
        assert retired not in presenter


def test_p4_runtime_api_has_no_loop_activation_seam() -> None:
    owner = OWNER.read_text(encoding="utf-8")
    presenter = PRESENTER.read_text(encoding="utf-8")

    build_signature = owner.split(
        "Fl4512BuildSpacetimeSequencesV4(", 1
    )[1].split(")", 1)[0]
    solve_signature = owner.split(
        "Fl4512SolveSpacetimeChainV4(", 1
    )[1].split(")", 1)[0]
    assert "bool" not in build_signature
    assert "bool" not in solve_signature
    call = presenter.split("Fl4512BuildSpacetimeSequencesV4(", 1)[1].split(");", 1)[0]
    assert "false" not in call
