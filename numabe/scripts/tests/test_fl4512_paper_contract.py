"""Semantic contracts for the scholarly FL-4512 reader manuscript.

Runtime implementation status and exact source constants are tested by their
own runtime suites. This file protects the paper's representation, equations,
claim ceiling, and boundary with FL-owned project state.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEX_PATH = ROOT / "docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex"
VALIDATOR_PATH = ROOT / "docs/agent/glyph-rendering-paper/scripts/validate_fl4512_paper_lane.py"


def _validator():
    spec = importlib.util.spec_from_file_location("validate_fl4512_paper_lane", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _reader() -> str:
    module = _validator()
    return module.strip_latex_comments(TEX_PATH.read_text(encoding="utf-8"))


def test_scholarly_contract_accepts_the_reader_manuscript() -> None:
    module = _validator()
    draft = TEX_PATH.read_text(encoding="utf-8")
    report = module.Report()
    assert module.is_scholarly_manuscript(draft)
    module.check_scholarly_manuscript_contract(
        report, module.strip_latex_comments(draft), draft
    )
    assert report.errors == []


def test_retired_status_manuscript_cannot_select_the_active_contract() -> None:
    module = _validator()
    draft = TEX_PATH.read_text(encoding="utf-8")
    retired = draft.replace(
        module.MANUSCRIPT_TITLE,
        "Glyph Selection Conditioned on Scene Facts for Realtime ASCII Rendering of Dynamic 3D Scenes",
        1,
    )
    assert retired != draft
    assert not module.is_scholarly_manuscript(retired)


def test_compiler_and_runtime_fact_owners_are_distinct() -> None:
    reader = " ".join(_reader().split())
    assert "Each \\GlyphFact{} is an immutable, content hash pinned projection" in reader
    assert "The compiled artifact supplies candidates to the renderer but never supplies scene facts" in reader
    assert "The runtime cannot derive glyph attributes, construct relations" in reader
    assert "The detector locates feature evidence" in reader


def test_glyph_connectivity_is_component_specific() -> None:
    reader = " ".join(_reader().split())
    assert "component-specific boundary ports" in reader
    assert "required entry and exit ports" in reader
    assert "component assigned to the feature does not connect" in reader
    assert "Boundary contact alone is insufficient" in reader


def test_detector_chain_solver_and_presenter_are_separate_stages() -> None:
    reader = " ".join(_reader().split())
    assert "The detector locates feature evidence" in reader
    assert "Persistent active strokes reconnect compatible projected samples" in reader
    assert "Candidate lattice and joint structural solve" in reader
    assert "Exactly one final tuple owner writes" in reader
    assert "without reconstructing presentation identity" in reader


def test_sequence_energy_contains_local_spatial_temporal_and_topology_terms() -> None:
    tex = TEX_PATH.read_text(encoding="utf-8")
    for term in (
        r"\sum_i U(c_i,g_i)",
        r"\lambda_s\sum_i J(",
        r"\lambda_t\,[g_i\ne g^-_{\pi(i)}]",
        r"\lambda_r R_{a_i}",
        r"\lambda_e T(q^-_{\pi(i)},q_i)",
    ):
        assert term in tex


def test_active_strokes_are_an_adapted_component_not_the_whole_method() -> None:
    reader = " ".join(_reader().split())
    assert "Active Strokes supplies one adapted separation within the larger renderer" in reader
    assert "later representation is a jointly selected sequence of glyph graphs" in reader
    assert "Active Strokes does not define Unicode admission" in reader
    assert r"\citep{benard2012activestrokes}" in reader


def test_html_authoring_is_proposed_as_one_runtime_not_a_second_renderer() -> None:
    reader = " ".join(_reader().split())
    assert "it is not a second renderer" in reader
    assert "native terminal game window remains open" in reader
    assert "cannot select glyphs or reconstruct a separate image" in reader
    assert "Each control sends raw intent to C++, which alone validates the action" in reader
    assert "does not present an executed end to end authoring experiment" in reader


def test_quantitative_claims_are_withheld_without_a_durable_receipt() -> None:
    reader = " ".join(_reader().split())
    assert "reports no quantitative structural or temporal result" in reader
    assert "no durable run receipt covers the complete method" in reader
    assert "no improvement over a baseline" in reader
    assert "does not report complete system quality or interactive performance" in reader
    assert "80 were invalid" not in reader
    assert "Of 97 frames" not in reader


def test_full_figure_and_forward_pipeline_inventory_cannot_be_compacted() -> None:
    tex = TEX_PATH.read_text(encoding="utf-8")
    assert tex.count(r"\begin{figure}") >= 34
    assert tex.count(r"\begin{table}") >= 10
    assert tex.count(r"\begin{tikzpicture}") >= 8
    assert tex.count(r"\label{fig:") >= 30
    assert len(tex.splitlines()) >= 4000
    for stage in range(1, 14):
        assert rf"\paragraph{{{stage}." in tex


def test_semantic_gate_rejects_governance_and_status_variants() -> None:
    module = _validator()
    fixtures = (
        "The paper follows the architecture rather than constraining it.",
        "The architecture remains mutable.",
        "The fixed objective is the visible result.",
        "This Jira ticket records the next action.",
        "Implementation status is ACCEPTED stage.",
        "Current head is origin/main.",
        "Commit deadbee repairs the paper.",
        "RQ-201 owns the next work.",
    )
    for fixture in fixtures:
        assert module.scholarly_prose_findings(fixture), fixture


def test_semantic_gate_accepts_scholarly_method_and_limit_language() -> None:
    module = _validator()
    prose = (
        "We propose a glyph graph selector. The method uses persistent feature identity. "
        "A diagnostic run does not establish visual quality or interactive performance."
    )
    assert module.scholarly_prose_findings(prose) == []
