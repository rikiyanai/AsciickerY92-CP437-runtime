#!/usr/bin/env python3
"""FL-4512 G4 census focused source test: tuple-retire single writer.

Pins the FL4512-FINAL-TUPLE-RETIRE-WRITER-CUT ownership cut:
- Fl4693RetireExactCellDecisionV1 declared once (transport header),
  defined once (transport cpp, the sole mutable owner).
- Exactly 3 call sites: 2 in fl4693_final_tuple_transport.cpp
  (Fl4693FinalTupleReconcilePostWorldV1 sprite + overlay paths),
  1 in render_sprite_blit.cpp (sprite-actor attribution).
- Zero direct two-field retirement clears outside the owner body in
  the scoped files; bulk frame-init memsets in render_resolve.cpp are
  initialization, not per-cell retirement, and out of scope.
- Classic (ASCIICKER_RENDERER_CLASSIC_CP437) acquires no owner:
  render_sprite_blit.cpp call sits in the non-Classic branch and the
  transport header is included only outside Classic.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HEADER = REPO_ROOT / "engine/render/fl4693_final_tuple_transport.h"
TRANSPORT = REPO_ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"
SPRITE = REPO_ROOT / "engine/render/render_sprite_blit.cpp"

OWNER = "Fl4693RetireExactCellDecisionV1"
DECISION_CLEAR = re.compile(r"fl4693_exact_cell_decision_valid\s*\[.*?\]\s*=\s*0u")
FACT_CLEAR = re.compile(r"fl4683_scene_fact\s*\[.*?\]\.valid\s*=\s*0u")


def _owner_body_span(text: str) -> tuple[int, int]:
    m = re.search(
        r"void\s+" + OWNER + r"\s*\(Fl4131HarriGpuBridge\s*\*\s*bridge,\s*int\s+index\s*\)\s*\{",
        text,
    )
    assert m, "owner definition missing in transport.cpp"
    depth = 0
    for i in range(m.end() - 1, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return (m.start(), i + 1)
    raise AssertionError("unbalanced owner body")


def test_owner_declared_once() -> None:
    text = HEADER.read_text()
    decls = re.findall(r"void\s+" + OWNER + r"\s*\(Fl4131HarriGpuBridge\s*\*", text)
    assert len(decls) == 1, f"header must declare owner once, saw {len(decls)}"


def test_owner_defined_once_with_both_clears() -> None:
    text = TRANSPORT.read_text()
    defs = re.findall(
        r"void\s+" + OWNER + r"\s*\(Fl4131HarriGpuBridge\s*\*\s*bridge,\s*int\s+index\s*\)",
        text,
    )
    assert len(defs) == 1, f"transport must define owner once, saw {len(defs)}"
    start, end = _owner_body_span(text)
    body = text[start:end]
    assert DECISION_CLEAR.search(body), "owner body must clear decision_valid"
    assert FACT_CLEAR.search(body), "owner body must clear scene_fact.valid"


def test_exactly_three_call_sites() -> None:
    t_text = TRANSPORT.read_text()
    s_text = SPRITE.read_text()
    _, end = _owner_body_span(t_text)
    after = t_text[end:]
    t_calls = re.findall(r"\b" + OWNER + r"\s*\(bridge,\s*index\s*\)", after)
    s_calls = re.findall(r"\b" + OWNER + r"\s*\(&g_fl4131_harri_gpu_bridge,\s*idx\s*\)", s_text)
    assert len(t_calls) == 2, f"transport needs 2 owner calls, saw {len(t_calls)}"
    assert len(s_calls) == 1, f"sprite blit needs 1 owner call, saw {len(s_calls)}"


def test_no_direct_retirement_clear_outside_owner() -> None:
    t_text = TRANSPORT.read_text()
    start, end = _owner_body_span(t_text)
    outside = t_text[:start] + t_text[end:]
    assert not DECISION_CLEAR.search(outside), "direct decision_valid=0u outside owner"
    assert not FACT_CLEAR.search(outside), "direct scene_fact.valid=0u outside owner"
    s_text = SPRITE.read_text()
    assert not DECISION_CLEAR.search(s_text), "direct decision_valid=0u in sprite blit"
    assert not FACT_CLEAR.search(s_text), "direct scene_fact.valid=0u in sprite blit"


def test_classic_acquires_no_owner() -> None:
    s_text = SPRITE.read_text()
    classic_return = s_text.find("defined(ASCIICKER_RENDERER_CLASSIC_CP437)")
    call = s_text.find(OWNER + "(&g_fl4131_harri_gpu_bridge")
    assert classic_return != -1 and call != -1
    assert call > classic_return, "owner call must sit in non-Classic branch"
    assert "return;" in s_text[classic_return:call], "Classic must early-return before call"
    assert "fl4693_final_tuple_transport.h" in s_text
    inc = s_text.find('#include "fl4693_final_tuple_transport.h"')
    guard = s_text.rfind("#if", 0, inc)
    assert guard != -1 and "ASCIICKER_RENDERER_CLASSIC_CP437" in s_text[guard:inc]
