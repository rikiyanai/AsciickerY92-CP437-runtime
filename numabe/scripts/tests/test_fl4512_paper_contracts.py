"""Focused numeric checks for the FL-4512 paper's newly explicit contracts.

These tests do not promote paper-only admission rules into runtime authority. They
pin the arithmetic stated by the manuscript and cross-check live source where an
implementation already exists.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.fl4512_projected_scale import (
    ProjectedScaleError,
    Q_MAX,
    quantize_projected_scale_q16_16,
)
from scripts.fl4512_projected_feature_extent import admission_state


ROOT = Path(__file__).resolve().parents[2]

VALIDATOR_PATH = ROOT / "docs/agent/glyph-rendering-paper/scripts/validate_fl4512_paper_lane.py"
BUILD_SCRIPT_PATH = ROOT / "docs/agent/glyph-rendering-paper/scripts/build_versioned_paper.py"
TEX_PATH = ROOT / "docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex"
FIGURE_DISPOSITIONS_PATH = ROOT / "docs/agent/glyph-rendering-paper/figure_dispositions.v1.json"
PAPER_BIB_PATH = ROOT / "docs/agent/glyph-rendering-paper/references.bib"
FEATURE_LINE_INTAKE_PATH = ROOT / (
    "docs/agent/glyph-rendering-paper/2026-07-19-figure-disposition-rootcause-and-pieta-target.md"
)
REPOSITORY_MIRROR_PATH = ROOT / "docs/research/ascii/articles/FL4512_REPOSITORIES.md"
REPOSITORY_MIRROR_JSON_PATH = ROOT / "docs/research/ascii/articles/FL4512_REPOSITORIES.json"
PRODUCT_ACTIVATION_RECEIPT_PATH = ROOT / (
    "docs/agent/glyph-rendering-paper/artifacts/"
    "fl4783_product_module_activation_v1.json"
)


def _load_paper_validator():
    """Import the paper-lane hygiene gate as a module for unit-level probes."""
    spec = importlib.util.spec_from_file_location("validate_fl4512_paper_lane", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_build_script():
    """Import the versioned paper builder for isolated unit probes."""
    spec = importlib.util.spec_from_file_location("build_versioned_paper", BUILD_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _reader_tex_from_draft(draft: str) -> str:
    module = _load_paper_validator()
    return module.strip_latex_comments(draft)


def _reader_contract_text(draft: str) -> str:
    module = _load_paper_validator()
    return module.reader_contract_text(_reader_tex_from_draft(draft))


def _compact_validator_report():
    module = _load_paper_validator()
    report = module.Report()
    draft = TEX_PATH.read_text()
    reader = _reader_tex_from_draft(draft)
    artifact_index = (ROOT / "docs/agent/glyph-rendering-paper/PAPER_ARTIFACT_INDEX.md").read_text()
    module.check_compact_manuscript_contract(report, reader, draft)
    module.check_compact_product_activation_contract(report, reader, draft, artifact_index)
    module.check_compact_reader_status_hygiene(report, reader, draft)
    return report


def test_prose_hygiene_flags_semicolon_clause_break() -> None:
    module = _load_paper_validator()
    rep = module.Report()
    draft = r"One clause ; another clause."
    module.check_tex_reader_prose_hygiene(rep, _reader_tex_from_draft(draft), draft)
    assert rep.errors
    assert any("space-semicolon-space" in err for err in rep.errors)


@pytest.mark.parametrize("connector", ["-", "‐", "‑", "‒", "–", "—", "―", "−"])
def test_prose_hygiene_flags_every_horizontal_word_connector(connector: str) -> None:
    module = _load_paper_validator()
    findings = module.reader_word_connector_findings(f"alpha{connector}beta")
    assert findings == [(1, f"alpha{connector}beta")]


def test_prose_hygiene_masks_asset_paths_and_label_identifiers() -> None:
    module = _load_paper_validator()
    draft = (
        r"\includegraphics{figures/panel-one-cell.png}" "\n"
        r"\label{fig:one-cell}" "\n"
        "ordinary prose"
    )
    assert module.reader_word_connector_findings(draft) == []


def test_reference_maps_require_superscript_bibliography_pointer() -> None:
    module = _load_paper_validator()
    rep = module.Report()
    draft = (
        r"\usepackage[super,numbers]{natbib}" "\n"
        r"\newcommand{\sourcecite}[1]{\cite{#1}}" "\n"
        r"\caption*{\textbf{Reference map: projected scale.} Ni motivates scale.}"
        "\n" r"\end{figure}"
    )
    module.check_tex_reader_prose_hygiene(rep, _reader_tex_from_draft(draft), draft)
    assert any("superscript bibliography pointer" in err for err in rep.errors)


# The functions prefixed ``_retired_reader_fixture`` preserve historical test
# code for the superseded compact/status manuscript. Pytest does not collect
# them. Active reader semantics are covered by test_fl4512_paper_contract.py
# and by the validator's scholarly negative fixtures.
def _retired_reader_fixture_compact_product_activation_boundary_matches_published_program() -> None:
    module = _load_paper_validator()
    draft = TEX_PATH.read_text()
    reader = _reader_tex_from_draft(draft)
    normalized = " ".join(reader.split()).casefold()
    receipt = json.loads(PRODUCT_ACTIVATION_RECEIPT_PATH.read_text())
    product_source = (ROOT / "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp").read_text()
    pin = json.loads((ROOT / "assets/glyphs/generated/fl4683.glyph_program_pin.v1.json").read_text())

    expected_program_sha = "13934b20e96758f54190e0db2e0693120891bc7044eb202b575687063ed827fa"
    expected_fact_sha = "f367d92057d99820a46f48ae6d69431e73cad3c7dd1402731d1e9f1bf7b7f5eb"

    assert receipt["schema"] == "fl4783.product_module_activation_receipt.v1"
    assert receipt["scope_ceiling"] == "module_execution_only_not_headed_normal_play"
    assert receipt["glyph_program"]["sha256"] == expected_program_sha
    assert receipt["glyph_fact"]["sha256"] == expected_fact_sha
    assert receipt["glyph_fact"]["byte_size"] == 97632136
    assert receipt["glyph_fact"]["stroke_port_graph_materialization"] == (
        "32 u7.mask.v1 structural candidate records only"
    )
    assert receipt["product_probe"]["load_status"] == "loaded"
    assert receipt["product_probe"]["selection_accepted"] is True
    assert receipt["product_probe"]["winning_glyph_record_id"] == 552
    assert receipt["product_probe"]["considered_candidate_count"] == 32
    assert receipt["product_probe"]["census_selected"] == 1
    assert pin["sidecar"]["format_version"] == 7
    assert pin["sidecar"]["sha256"] == expected_program_sha
    assert expected_program_sha in product_source

    assert "compact v7 \\texttt{glyphprogram}" in normalized
    assert "product-module loader and selector activation" in normalized
    assert "product-module execution evidence only" in normalized
    assert "does not claim headed game execution" in normalized
    for open_claim in (
        "headed_normal_play_legibility",
        "adjacent_frame_snake_conservation",
        "camera_cycle_closure",
        "webassembly_execution",
        "c18_paper_evidence",
    ):
        assert open_claim in receipt["not_established"]
    for stale in (
        "remains unpublished and unpinned",
        "unpublished and unpinned",
        "no product path execution is claimed",
        "publish the port-graph metadata into the product glyph program",
        "working product renderer",
    ):
        assert stale not in normalized

    report = _compact_validator_report()
    assert not report.errors


def test_prose_hygiene_flags_tex_dash_wrappers_and_stale_voice() -> None:
    module = _load_paper_validator()
    rep = module.Report()
    draft = (
        r"alpha\mbox{-}beta. alpha\textemdash{}beta. "
        "The method in this paper begins Mathematically."
    )
    module.check_tex_reader_prose_hygiene(rep, _reader_tex_from_draft(draft), draft)
    joined = "\n".join(rep.errors)
    assert "word-connector mbox" in joined
    assert "LaTeX \\textemdash" in joined
    assert "in this paper" in joined
    assert "Mathematically" in joined


def test_prose_hygiene_flags_status_dashboard_and_source_line_audit_language() -> None:
    module = _load_paper_validator()
    rep = module.Report()
    draft = (
        "The Current source implements this path, but proof remains open pending receipt.\n"
        "The status vocabulary below is normative for the implemented renderer.\n"
        "Our method is implemented inside the current U2 and Active-Stroke implementation.\n"
        "The grouped proof remains an evidence package.\n"
        "Focused insertion faithfulness is verified live.\n"
        "This selector is the sole production tonal scorer.\n"
        "The authoring mismatch formerly blocked a claim and has now been reconciled.\n"
        "The current prototype retains a provisional compatibility value.\n"
        "A separate legacy path selects winners from an unstaged descriptor.\n"
        "The runtime source derives maximal chains and solves the field.\n"
        "The former structural writer and replay are deleted.\n"
        "The implementation patch deletes V1 and installs V2.\n"
        "At capture time, CP437 still followed a fallback path.\n"
        "The retired progression sealer has no method membership.\n"
        "There is no browser mask overlay painter.\n"
        "The BSP tree remains part of the live renderer.\n"
        "The prototype measures quantities the shipped renderer cannot report.\n"
        "See engine/render/render_resolve.cpp:2280 and tex:381 for the audit."
    )
    module.check_tex_reader_prose_hygiene(rep, _reader_tex_from_draft(draft), draft)
    joined = "\n".join(rep.errors)
    assert "status-dashboard prose 'current source implements'" in joined
    assert "status-dashboard prose 'status vocabulary'" in joined
    assert "status-dashboard prose 'implemented renderer'" in joined
    assert "status-dashboard prose 'implemented inside'" in joined
    assert "status-dashboard prose 'current u2'" in joined
    assert "status-dashboard prose 'active-stroke implementation'" in joined
    assert "status-dashboard prose 'grouped proof'" in joined
    assert "status-dashboard prose 'proof remains open'" in joined
    assert "status-dashboard prose 'pending receipt'" in joined
    assert "status-dashboard pattern 'live verification assertion'" in joined
    assert "status-dashboard pattern 'production-owner assertion'" in joined
    assert "status-dashboard pattern 'resolved implementation chronology'" in joined
    assert "status-dashboard pattern 'current prototype assertion'" in joined
    assert "status-dashboard pattern 'legacy runtime-path assertion'" in joined
    assert "status-dashboard pattern 'source-realization assertion'" in joined
    assert "status-dashboard pattern 'deleted-owner assertion'" in joined
    assert "status-dashboard pattern 'implementation-patch chronology assertion'" in joined
    assert "status-dashboard pattern 'capture-time fallback assertion'" in joined
    assert "status-dashboard pattern 'retired method-member assertion'" in joined
    assert "status-dashboard pattern 'browser-painter absence assertion'" in joined
    assert "status-dashboard pattern 'live-renderer source-state assertion'" in joined
    assert "status-dashboard pattern 'shipped-renderer source-state assertion'" in joined
    assert "source-audit file:line citation" in joined
    assert "manuscript-audit line citation" in joined


@pytest.mark.parametrize(
    "claim",
    [
        "The method contribution is a single native selection path across the product.",
        "Normative view conditioned GlyphTransformRelation v2 defines the final relation owner.",
        "One GPU revalidator writes the same final glyph result type.",
        "Each reported result retains a normal play input trace and continuous product viewport record.",
        "TERM++ presents the composed pixels inside the playable native/WebAssembly game frame.",
        "TERM++ is the sole final presenter for native and WebAssembly.",
        "The browser owns editor input, while C++ owns runtime state, rendering decisions, and the render product.",
    ],
)
def test_prose_hygiene_rejects_unsupported_cross_product_realization_claims(
    claim: str,
) -> None:
    module = _load_paper_validator()
    rep = module.Report()
    module.check_tex_reader_prose_hygiene(rep, claim, claim)
    assert any("unsupported product-realization claim" in error for error in rep.errors)


def test_prose_hygiene_accepts_intended_method_with_webassembly_limitation() -> None:
    module = _load_paper_validator()
    claim = (
        "The target method contribution is the specification of a single native selection path. "
        "This subsection defines a transitional view conditioned GlyphTransformRelation v2. "
        "The revalidator is a final frame ownership contract, not a GPU implementation claim. "
        "Each reported result declares the weakest passport it actually carries. "
        "TERM++ is the intended unified presentation stage for composed pixels in both "
        "playable targets. The WebAssembly target still uses a separate sidecar and browser "
        "identity path, so it does not implement this unified presentation contract. The target "
        "product contract assigns rendering decisions and the render product to C++. The "
        "WebAssembly browser identity path is the present exception to that target contract."
    )
    rep = module.Report()
    module.check_tex_reader_prose_hygiene(rep, claim, claim)
    assert not any("unsupported product-realization claim" in error for error in rep.errors)


def _retired_reader_fixture_excludes_recurring_status_and_unsupported_outcome_prose() -> None:
    reader = _reader_contract_text(TEX_PATH.read_text())
    for phrase in (
        "The method contribution is a single native selection path",
        "Normative view conditioned",
        "One GPU revalidator",
        "Each reported result retains",
        "four properties our method combines",
        "Glyph pipeline described here",
        "Full method definition",
        "floating point tonal owner and its runtime switch have been deleted",
        "focused compiled insertion faithfulness is verified live",
        "authoring mismatch that formerly blocked",
        "the mismatch has now been reconciled",
        "a separate legacy path outside the parity route selects winners",
        "sole production tonal scorer",
        "the current prototype retains",
        "the runtime source derives producer owned maximal chains",
        "the former current frame structural writer plus structural replay are deleted",
        "there is no browser mask overlay painter",
        "the result is an alternating gait",
        "smooth motion is produced entirely from the authored vocabulary",
        "remain part of the live renderer",
        "quantities the shipped renderer cannot report",
        "remains unpublished and unpinned",
        "unpublished and unpinned",
        "no product path execution is claimed",
        "working product renderer",
    ):
        assert phrase not in reader
    assert "equal scores by the declared row order or" not in reader
    for phrase in (
        "specified target method",
        "a scalar identity is not the same object",
        "extended grapheme clusters",
        "product-module execution evidence only",
        "sparse projection is a transport and product-program boundary",
        "whether webassembly consumes the same contract",
        "whether c18 evidence can be produced",
    ):
        assert phrase in reader


def test_paper_artifact_index_names_current_artifact_and_authoring_scope() -> None:
    module = _load_paper_validator()
    index = (ROOT / "docs/agent/glyph-rendering-paper/PAPER_ARTIFACT_INDEX.md").read_text()
    tex = TEX_PATH.read_text()
    version = re.search(r"\\newcommand\{\\PaperVersion\}\{(v\d+\.\d+\.\d+)\}", tex).group(1)
    assert module.find_current_reader_artifact_claims(index) == [version]
    assert "Current proof authority is the visible HTML action path" not in index
    assert "Current authoring-evidence authority is the visible HTML action path" in index


# ---------------------------------------------------------------------------
# FL-4658: exactly one paper build may be marked "current"
# ---------------------------------------------------------------------------
# The paper is rebuilt often.  Every rebuild appends a row to
# PAPER_ARTIFACT_INDEX.md and, when it becomes the build readers should open,
# gets the sentence "`vX.Y.Z` is the current reader artifact".  Older builds must
# then be reworded so they no longer claim currency.
#
# That demotion step was skipped, and the index ended up asserting that BOTH
# v0.9.49 and v0.9.90 were "the current reader artifact" at the same time.  The
# three tests below lock the repair in place:
#
#   1. the real committed index has exactly one current build;
#   2. the validator function ACCEPTS a document with exactly one;
#   3. the validator function REJECTS a document with two.
#
# Test 3 is the one that actually protects the lane.  Tests that only inspect
# the real file would silently start passing again if someone deleted the gate,
# so the gate itself is exercised against synthetic text.


def test_artifact_index_declares_exactly_one_current_reader_artifact() -> None:
    """The committed index must not contradict itself about which build is current."""
    module = _load_paper_validator()
    index = (ROOT / "docs/agent/glyph-rendering-paper/PAPER_ARTIFACT_INDEX.md").read_text()

    # find_current_reader_artifact_claims returns one version string per
    # currency claim found, in file order.
    claims = module.find_current_reader_artifact_claims(index)

    version = re.search(
        r"\\newcommand\{\\PaperVersion\}\{(v\d+\.\d+\.\d+)\}",
        TEX_PATH.read_text(),
    ).group(1)

    # Exactly one claimant, derived from the manuscript instead of hard-coded.
    assert claims == [version], (
        "PAPER_ARTIFACT_INDEX.md must mark exactly one current reader artifact; "
        f"found {claims}"
    )


def test_current_artifact_gate_accepts_a_single_claim() -> None:
    """A well-formed index (one current build) must produce no validator error."""
    module = _load_paper_validator()
    report = module.Report()

    # Minimal synthetic index: one demoted build, one current build.  The line
    # break inside the second sentence is deliberate -- the real index is
    # hard-wrapped, so the gate has to tolerate a newline mid-sentence.
    single = (
        "`v0.9.49` is a superseded reader artifact; it deletes the attachment owner.\n"
        "`v0.9.90` is the current reader\n"
        "artifact. It weakens the single-path wording.\n"
    )
    module.check_single_current_reader_artifact(report, single)

    assert report.errors == [], f"single-current index wrongly rejected: {report.errors}"


def test_current_artifact_gate_rejects_two_claims_and_names_them() -> None:
    """Two builds claiming currency is the FL-4658 defect and must fail loudly."""
    module = _load_paper_validator()
    report = module.Report()

    # This is the exact shape of the defect that shipped: an old build kept its
    # currency sentence while a new build added its own.
    doubled = (
        "`v0.9.49` is the current reader artifact; it deletes the attachment owner.\n"
        "`v0.9.90` is the current reader artifact. It weakens the single-path wording.\n"
    )
    module.check_single_current_reader_artifact(report, doubled)

    assert len(report.errors) == 1, f"expected exactly one error, got {report.errors}"
    message = report.errors[0]
    # The operator has to be told WHICH sentences disagree, otherwise the error
    # is useless against a 450-line index.
    assert "more than one current reader artifact" in message
    assert "v0.9.49" in message
    assert "v0.9.90" in message


def test_current_artifact_gate_rejects_an_index_with_no_current_build() -> None:
    """Forgetting to promote the newest build must also fail, not pass silently."""
    module = _load_paper_validator()
    report = module.Report()

    none_current = "`v0.9.49` is a superseded reader artifact.\n`v0.9.90` was built.\n"
    module.check_single_current_reader_artifact(report, none_current)

    assert len(report.errors) == 1, f"expected exactly one error, got {report.errors}"
    assert "names no current reader artifact" in report.errors[0]


def _retired_reader_fixture_rejects_proof_vocabulary_without_matching_internal_ids() -> None:
    """Reader claims stay bounded while labels, asset paths, and comments remain traceable."""
    module = _load_paper_validator()
    reader = _reader_tex_from_draft(TEX_PATH.read_text())
    prose = module.reader_prose_for_connector_scan(reader)
    assert re.search(r"\b(?:proof|prove|proves|proven)\b", prose, re.IGNORECASE) is None
    assert r"\label{fig:authoring-evidence-boundary}" in reader


def _retired_reader_fixture_section_ownership_keeps_method_and_falsifier_out_of_related_work() -> None:
    module = _load_paper_validator()
    reader = _reader_contract_text(TEX_PATH.read_text())
    definitions_start = reader.index(r"\section{definitions and method}")
    compiler_start = reader.index(r"\subsection{offline glyph compiler}")
    features_start = reader.index(r"\subsection{scene facts and projected surface features}")
    scoring_start = reader.index(r"\subsection{legal rows and glyph-graph scoring}")
    evaluation_start = reader.index(r"\section{current evidence boundary}")
    falsifier_start = reader.index("80 of 97 structural frames")
    related_start = reader.index(r"\section{related work}")
    conclusion_start = reader.index(r"\section{conclusion}")
    assert (
        definitions_start
        < compiler_start
        < features_start
        < scoring_start
        < evaluation_start
        < falsifier_start
        < related_start
        < conclusion_start
    )

    rep = module.Report()
    contaminated = (
        r"\section{Related Work}" "\n"
        r"E_{\mathrm{snake}}" "\n"
        r"\section{Conclusion}"
    )
    module.check_tex_reader_prose_hygiene(rep, contaminated, contaminated)
    assert any("Related Work contains method/result owner token" in error for error in rep.errors)


def test_figure_provenance_hash_binds_every_input_artifact(tmp_path: Path) -> None:
    module = _load_paper_validator()
    repo = tmp_path
    support = repo / "docs/agent/glyph-rendering-paper"
    provenance = support / "figure_provenance"
    provenance.mkdir(parents=True)
    inventory = repo / "docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv"
    inventory.parent.mkdir(parents=True)
    inventory.write_text("leaf_a\n", encoding="utf-8")
    first = repo / "docs/research/ascii/verification/fl4512/first.json"
    second = repo / "assets/glyphs/generated/second.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text('{"verdict":"PASS"}\n', encoding="utf-8")
    second.write_text('{"value":2}\n', encoding="utf-8")
    figure = support / "figures/panel.png"
    figure.parent.mkdir(parents=True)
    figure.write_bytes(b"panel")
    payload = {
        "schema": "fl4512.figure_provenance.v1",
        "figure_id": "fig:demo",
        "package_id": "pkg:demo:v1",
        "panel_id": "fig:demo/panel:01",
        "source_owner": "tester",
        "prediction_id": "demo",
        "ui_leaf_id": "leaf_a",
        "proof_status": "PROOF_OPEN",
        "proof_open_reason": "bounded",
        "receipt_sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
        "input_receipts": [str(first.relative_to(repo))],
        "input_artifact_sha256s": [
            {"path": str(first.relative_to(repo)), "sha256": hashlib.sha256(first.read_bytes()).hexdigest()},
            {"path": str(second.relative_to(repo)), "sha256": "bad"},
        ],
        "panel_images": ["panel.png"],
        "image_sha256s": [hashlib.sha256(figure.read_bytes()).hexdigest()],
        "image_sha256": hashlib.sha256(figure.read_bytes()).hexdigest(),
    }
    (provenance / "fig_demo.json").write_text(json.dumps(payload), encoding="utf-8")
    old_repo, old_support = module.REPO, module.SUPPORT_DIR
    module.REPO = repo
    module.SUPPORT_DIR = support
    try:
        rep = module.Report()
        module.check_figure_namespace_provenance(
            rep,
            "| `fig:demo` | `panel.png` | `fig_demo.json` |\n",
        )
    finally:
        module.REPO = old_repo
        module.SUPPORT_DIR = old_support
    assert any("input artifact hash mismatch" in err for err in rep.errors)


def _retired_reader_fixture_historical_figures_and_open_slots_remain_visible_contracts() -> None:
    draft = TEX_PATH.read_text()
    reader = _reader_tex_from_draft(draft)
    registry = json.loads(FIGURE_DISPOSITIONS_PATH.read_text())
    assert reader.count(r"\missingfigurebox{") == 0
    assert reader.count(r"\caption{Missing direct capture.") == 0
    assert draft.count(r"\historicaldiagnosticbox{") == 2
    assert "HISTORICAL DIAGNOSTIC: LIMITED EVIDENCE" in draft
    assert draft.count("HISTORICAL NATIVE CONTEXT: NOT PRODUCT EVIDENCE") == 1
    assert "HTML GROUPED REPRODUCTION REQUIRED:" not in draft
    assert len(registry["figures"]) == 23
    kept_sources = set()
    for row in registry["figures"]:
        source = ROOT / "docs/agent/glyph-rendering-paper" / row["source_path"]
        archive = ROOT / registry["archive_root"] / row["archive_path"]
        assert source.is_file()
        assert archive.is_file()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == row["sha256"]
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == row["sha256"]
        if row["source_path"] in draft:
            kept_sources.add(row["source_path"])
    assert kept_sources == {
        "figures/panel-skull-teaser.png",
        "figures/plot-same-tone-strip.png",
    }


def _retired_reader_fixture_feature_line_references_and_deferred_source_audit_remain_registered() -> None:
    tex = TEX_PATH.read_text()
    normalized_tex = " ".join(tex.split()).casefold()
    bib = PAPER_BIB_PATH.read_text()
    intake = FEATURE_LINE_INTAKE_PATH.read_text()
    keys = (
        "decarlo2003suggestivecontours",
        "decarlo2004suggestivecontourstemporal",
        "rusinkiewicz2004curvatures",
        "burns2005volumelines",
        "decarlo2007highlightlines",
    )
    for key in keys:
        assert bib.count("{" + key + ",") == 1
        assert key in intake
    for cited_key in (
        "decarlo2003suggestivecontours",
        "decarlo2004suggestivecontourstemporal",
        "judd2007apparentridges",
        "benard2012activestrokes",
        "cardona2016temporalfeaturelines",
    ):
        assert cited_key in tex
    assert "suggestive contours extend silhouettes" in normalized_tex
    assert "gated cubic Monge patch" not in tex
    assert "motion and stroke coherence part of the rendering problem" in normalized_tex
    assert "feature kinds include silhouettes, creases, ridges, valleys" in normalized_tex
    assert r"\includegraphics[width=\linewidth]{figures/panel-fl4652-pieta-progression-v1.png}" not in tex
    assert r"\missingfigurebox{FIG.~\ref{fig:fl4652-pieta-progression}}" not in tex
    assert intake.count("TECHNIQUE_AUDIT_OPEN") == 4
    assert "PRIMARY_SOURCE_AUDIT_OPEN" in intake


def test_pieta_progression_gap_stays_visible_without_status_prose() -> None:
    """Keep the promised experiment visible without printing project status."""
    readme = (ROOT / "docs/research/glyph-rendering/README.md").read_text()
    tex = TEX_PATH.read_text()
    normalized_readme = " ".join(readme.split())
    normalized_tex = _reader_contract_text(tex)
    reader = _reader_tex_from_draft(tex)

    assert r"\label{fig:fl4652-pieta-progression}" in normalized_tex
    assert r"\missingfigurebox{fig.~\ref{fig:fl4652-pieta-progression}}" in normalized_tex

    for status_phrase in (
        "IMPLEMENTED",
        "fedbc3485",
        "a8481dc20",
        "PROOF_OPEN",
        "PROOF\\_OPEN",
        "implemented, unproven",
        "blank paper field",
    ):
        assert status_phrase not in reader, f"status tracker phrase survives in reader TeX: {status_phrase!r}"

    # --- Required IMPLEMENTED blank-field policy in the primary canon (README) ---
    for required in (
        "Tonal-under-strokes structural progression",
        "runtime PROOF_OPEN",
        "Source emits bridge v18",
        "Same-frame product evidence and operator visual signoff remain required",
    ):
        assert required in readme, f"missing README status anchor: {required!r}"

    # --- Rejected base-preserving normative wording (must be gone everywhere) ---
    # None of these appear in the new prose; each was unique to the retired
    # tonal-body policy, so their presence would mean the cutover regressed.
    for banned in (
        "base-surface tuple",
        "base-surface BG",
        "over a legible surface",
        "over a legible base surface",
        "blank presentation class",
        "Preserve the ordinary U6 base-surface",
        "retains the ordinary",
    ):
        assert banned not in normalized_tex, f"base-preserving wording survives in TeX: {banned!r}"
        assert banned not in normalized_readme, f"base-preserving wording survives in README: {banned!r}"


def test_open_pieta_progression_provenance_cannot_supply_reader_pixels() -> None:
    provenance_path = ROOT / (
        "docs/agent/glyph-rendering-paper/figure_provenance/"
        "fig_fl4652_pieta_progression.json"
    )
    provenance = json.loads(provenance_path.read_text())
    tex = TEX_PATH.read_text()
    assert provenance["proof_status"] == "PROOF_OPEN"
    assert provenance["paper_inclusion"] is False
    assert provenance["disposition"] == "HISTORICAL_DIAGNOSTIC"
    assert "panel-fl4652-pieta-progression-v1.png" not in tex


def test_benard_code_repository_inventory_remains_durable_and_unclaimed() -> None:
    markdown = REPOSITORY_MIRROR_PATH.read_text()
    payload = json.loads(REPOSITORY_MIRROR_JSON_PATH.read_text())
    expected = {
        "benardp/ActiveStrokes",
        "benardp/contours_viewer",
        "benardp/contours",
        "benardp/qrtsc",
        "benardp/dpix",
        "benardp/jot-lib",
        "benardp/PaintTween",
    }
    recorded = {row["name"] for row in payload["repositories"]}
    assert expected <= recorded
    for name in expected:
        assert name in markdown
        row = next(row for row in payload["repositories"] if row["name"] == name)
        assert row["scratch"] is None
    assert payload["clone_policy"] == "copy_to_dot_scratch_before_use"
    assert "A Star List entry does not make third-party code part of the runtime" in markdown


def test_end_to_end_repository_mirror_covers_temporal_and_ascii_animation_stages() -> None:
    markdown = REPOSITORY_MIRROR_PATH.read_text()
    payload = json.loads(REPOSITORY_MIRROR_JSON_PATH.read_text())
    recorded = {row["name"] for row in payload["repositories"]}
    required = {
        "MarkMoHR/JoSTC",
        "SNU-VGILab/Liv3Stroke",
        "facebookresearch/co-tracker",
        "princeton-vl/RAFT",
        "hpjansson/chafa",
        "humanbydefinition/textmode.js",
        "CameronFoxly/Ascii-Motion",
    }
    assert payload["schema"] == "fl4512.github_repository_mirror.v2"
    assert payload["repository_count"] == len(payload["repositories"])
    assert len(recorded) == payload["repository_count"]
    assert required <= recorded
    for name in required:
        assert name in markdown


def _retired_reader_fixture_direction_diagnostic_and_failure_table_are_inside_their_sections() -> None:
    draft = TEX_PATH.read_text()
    method = draft.index(r"\section{Definitions and Method}")
    direction = draft.index(r"\subsection{Scene facts and projected surface features}")
    evaluation = draft.index(r"\section{Current Evidence Boundary}")
    limitations = draft.index(r"\section{Limitations}")
    assert method < direction < evaluation < limitations
    assert "plot-direction-vocab-grid.png" not in draft
    assert "plot-direction-runtime-strip.png" not in draft
    assert "HISTORICAL DIAGNOSTIC: LIMITED EVIDENCE" in draft
    assert r"\section{Rendered Material Surface Direction and Synthetic Cube Views}" not in draft
    assert r"\section{Descriptor Owned Routing and Scoring}" not in draft
    assert r"\section{Temporal Revalidation}" not in draft
    assert r"\section{Failure Mode Taxonomy}" not in draft
    failure_taxonomy = draft.index(r"\label{sec:failure-taxonomy}")
    assert evaluation < failure_taxonomy < limitations
    normalized = " ".join(draft.split())
    assert "It can fail structurally through target-port absence, disconnected components" in normalized


def test_direction_figure_copy_has_no_horizontal_word_connectors() -> None:
    module = _load_paper_validator()
    paper_dir = ROOT / "docs/agent/glyph-rendering-paper"
    direction_grid = (paper_dir / "scripts/fl4512_render_direction_grid.py").read_text()
    runtime_strip = (paper_dir / "scripts/fl4512_render_direction_runtime_strip.py").read_text()
    rep = module.Report()
    module.check_embedded_figure_copy_hygiene(rep, direction_grid)
    assert rep.errors == []
    assert "Direction-addressed" not in runtime_strip
    assert "Direction addressed" in runtime_strip


def _retired_reader_fixture_uses_perceptual_motivation_and_normalized_voice() -> None:
    module = _load_paper_validator()
    draft = TEX_PATH.read_text()
    reader = module.strip_latex_comments(draft)
    prose = module.reader_prose_for_connector_scan(reader)
    normalized = " ".join(reader.split())
    assert "A few well-placed lines can make a surface turn in space" in reader
    assert "sparse line sets can carry recoverable shape" in normalized
    assert "The structural branch is a topology-aware discretization problem" in reader
    report = _compact_validator_report()
    assert not report.errors
    for stale in (
        "in this paper",
        "this paper",
        "The paper",
        "Mathematically",
        "Coverage and evaluation.",
        "paper-v1",
        "non-default-ignorable",
        "one-cell",
        "Direction, Perspective, and Aspect Families",
    ):
        assert stale not in prose


def _retired_reader_fixture_symmetry_is_not_an_upstream_admission_owner() -> None:
    tex = TEX_PATH.read_text()
    reader = _load_paper_validator().strip_latex_comments(tex)
    normalized = " ".join(reader.split())
    for stale in (
        "orientation admission uses reviewed cosets",
        "orientation admission is by coset",
        "unstable stabilizer",
        r"\mathrm{conf}(g)\ge0.15",
        r"d_{\mathrm{sym}}",
    ):
        assert stale not in reader
    assert "Raster symmetry and coset labels have no address or admission authority" in normalized
    assert "address selects exactly one compiled row" in normalized
    compiler = (ROOT / "scripts/compile_fl4512_pools.py").read_text()
    assert "candidate_role_admission_invalid" in compiler
    assert "candidate_unadmitted" in compiler


def test_bright_phase_density_is_monotone_and_inversion_is_not_in_evaluated_path() -> None:
    def density_bin(value: float) -> int:
        return min(3, max(0, int(min(1.0, max(0.0, value)) * 4.0)))

    assert [density_bin(v) for v in (0.0, 0.2, 0.3, 0.6, 1.0)] == [0, 0, 1, 2, 3]
    resolver_source = (ROOT / "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp").read_text()
    assert "0.299f * (float)p[0] + 0.587f * (float)p[1] + 0.114f * (float)p[2]" in resolver_source
    resolver = (ROOT / "apps/fl4512-runtime/engine/fl4131_harri_resolver.cpp").read_text()
    assert "density = (int)(avg * 4.0f)" in resolver


def test_mesh_anchor_quantum_and_fail_closed_collision_contract() -> None:
    def quantize(value: float) -> int:
        return min(63, math.floor(64 * min(1.0, max(0.0, value))))

    assert quantize(0.0) == 0
    assert quantize(1 / 64) == 1
    assert quantize(1.0) == 63
    header = (ROOT / "engine/render/fl4208_surface_identity.h").read_text()
    scene = (ROOT / "engine/render/render_scene.cpp").read_text()
    assert "FL4208_MESH_ANCHOR_BINS_V1 = 64" in header
    assert "sizeof(Fl4208SurfaceId) == 24" in header
    assert "FL4208_CELL_ID_REASON_MESH_ANCHOR_COLLISION" in scene
    assert "FL4208_CELL_ID_REASON_TERRAIN_ANCHOR_COLLISION" in header
    assert "FL4208_CELL_ID_REASON_TERRAIN_ANCHOR_COLLISION" in scene
    collision_pass = scene.split(
        "std::unordered_map<Fl4208SurfaceId, int, SurfaceIdHash> first_history_cell",
        1,
    )[1].split("if (trace && !capture_invalid)", 1)[0]
    assert "FL4208_OWNER_TERRAIN" in collision_pass
    assert "FL4208_OWNER_MESH" in collision_pass
    assert "r->fl4208_cell_state[ci] = 2" in collision_pass
    assert "for (int tap = 0; tap < 4; ++tap)" in collision_pass
    assert "Fl4208IdentityInterner::INVALID_INDEX" in collision_pass


def test_q16_saturation_boundary_and_integer_admission_domain() -> None:
    largest = Q_MAX / 65536.0
    assert quantize_projected_scale_q16_16(largest) == Q_MAX
    trigger = (Q_MAX + 0.5) / 65536.0
    with pytest.raises(ProjectedScaleError, match="scale_overflow"):
        quantize_projected_scale_q16_16(math.nextafter(trigger, math.inf))
    threshold = {"enter_q": 3 * 65536, "exit_q": 2 * 65536}
    assert admission_state(3 * 65536, threshold, False) == "CLAIMED_CURRENT"
    assert admission_state(2 * 65536, threshold, True) == "U6_RETAIN_WITHIN_BAND"


def test_runtime_kernel_truncation_thresholds_match_source() -> None:
    assert 5 / 0.22 == pytest.approx(22.7272727)
    assert 5 / 0.18 == pytest.approx(27.7777778)
    resolve = (ROOT / "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp").read_text()
    sprite_blit = (ROOT / "apps/fl4512-runtime/engine/fl4512_sprite_measure_blit.cpp").read_text()
    for source in (resolve, sprite_blit):
        assert "cell_px * 0.22f" in source
        assert "cell_px * 0.18f" in source


def _retired_reader_fixture_figure_2b_names_the_slope_band_glyph_edit() -> None:
    paper_dir = ROOT / "docs/agent/glyph-rendering-paper"
    composer = (paper_dir / "scripts/fl4512_render_same_tone_strip.py").read_text()
    tex = (paper_dir / "2026-07-07-fl4512-first-rough-draft.tex").read_text()
    normalized_tex = " ".join(tex.split())
    provenance = json.loads(
        (paper_dir / "figure_provenance/fig_same_tone_strip.json").read_text()
    )

    assert "BEFORE_FRAME = 58" in composer
    assert "AFTER_FRAME = 59" in composer
    assert "EXPECTED_TIMES_S = (3.87, 3.93)" in composer
    assert "PICKS =" not in composer
    assert "Grass Tops" not in composer
    assert "golden-terrain-ux-runtime.png" not in tex
    assert "golden-vimpallets-runtime.png" not in tex
    assert "The legacy slope band edit predates the registered HTML editor surface" in normalized_tex
    assert "figures/plot-same-tone-strip.png" in tex
    assert "matched temporal glyph change" not in composer
    assert "temporal vocabulary change" not in tex
    assert "One source, one scene, one pose" not in composer
    assert "Before  ·" not in composer
    assert "After  ·" not in composer
    assert provenance["frame_pair"]["same_source"] is True
    assert provenance["frame_pair"]["same_scene"] is True
    assert provenance["frame_pair"]["same_camera"] is True
    assert provenance["frame_pair"]["same_character_pose"] is True
    assert len(provenance["highlighted_cells_xy_in_surface"]) == 6


def test_table_one_uses_conventional_notation_caption() -> None:
    tex = (
        ROOT
        / "docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex"
    ).read_text()
    assert "\\caption{Notation and definitions.}" in tex
    assert "Reader facing terms used throughout the method" not in tex


def test_status_tracker_language_is_not_reader_facing_manuscript() -> None:
    tex = TEX_PATH.read_text()
    reader = _reader_tex_from_draft(tex)
    for banned in (
        r"\label{tab:claim-state-vocabulary}",
        r"\label{tab:implementation-state}",
        "Component & Evidence state & Implementation & Diagnostic input & Current status",
        "Specified & A normative method rule.",
        "Implemented & A current C++ source owner exists.",
        r"\texttt{PROOF\_OPEN}",
        "PROOF_OPEN",
    ):
        assert banned not in reader
        assert banned not in tex
    for stale in (
        "Evaluated method definition",
        "The evaluated subset",
        "Our evaluated subset",
        "Implemented, proof open",
        "remains unevaluated",
    ):
        assert stale not in reader


def _retired_reader_fixture_normative_pipeline_names_suggestive_stage_and_single_finalizer() -> None:
    tex = TEX_PATH.read_text()
    presenter = (ROOT / "platform/terminal_gl_present.cpp").read_text()
    final_tuple_owner = (
        ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"
    ).read_text()
    assert "silhouette, crease, suggestive" in tex
    assert "inactive suggestive" not in tex
    assert "final-tuple owner" in tex
    assert r"\newcommand{\GlyphFact}{\textsc{GlyphFact}}" in tex
    assert r"\newcommand{\SceneFact}{\textsc{SceneFact}}" in tex
    assert "early CP437 promotion is deleted" not in tex
    assert r"\texttt{AnsiCell.gl} remains source content" not in tex
    assert "Fl4512EmitSuggestiveContoursV1" in (
        ROOT / "engine/render/render_world_pass.cpp"
    ).read_text()
    assert "bool Fl4693FinalTupleCommitFrameV1" in final_tuple_owner
    assert final_tuple_owner.count("g_final[compact] = tuple;") == 1
    assert "Fl4652FinalizeOrdinaryCellsV1" not in presenter
    assert "RuntimeFinalTupleOwner has already classified and constructed every tuple" in presenter
    assert "RuntimeFinalTupleOwner remains the sole final writer" in (
        ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.h"
    ).read_text()


def _retired_reader_fixture_end_to_end_pipeline_matches_source_order_and_narrows_candidates() -> None:
    tex = TEX_PATH.read_text()
    figure_start = tex.index(r"\begin{figure}[t]", tex.index("The complete execution"))
    figure_end = tex.index(r"\end{figure}", figure_start)
    figure = tex[figure_start:figure_end]
    for label in (
        "spec:scene-query",
        "spec:raster-depth",
        "spec:cell-facts",
        "spec:descriptor-construction",
        "spec:role-priority",
        "spec:route-pool",
        "spec:scoring",
        "spec:temporal-revalidation",
        "spec:presentation",
        "spec:glyph-admission",
    ):
        assert rf"\label{{{label}}}" in tex
        assert rf"\pageref{{{label}}}" in figure
    for token in (
        "finalized descriptors",
        "priority scan selects role",
        "fact owned address",
        "one compiled legal row",
        "bounded selection",
        "temporal revalidation",
        r"\GlyphId{} lookup and atlas",
        "TERM++ presentation",
    ):
        assert token in figure
    assert (
        r"$\mathcal U_{\mathrm{scalar}}\rightarrow G_{\mathrm{identity}}"
        r"\supseteq G_{\mathrm{cell}}\supseteq G_{m,q}\supseteq P_{\kappa(c)}"
        r"\supseteq\{g^*(c)\}\rightarrow\{\hat g(c)\}$"
    ) in figure

    product_source = (ROOT / "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp").read_text()
    final_tuple_owner = (ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp").read_text()
    assert "kSidecarSha256Hex" in product_source
    assert "Fl4693ProductGlyphProgramImageV1" in product_source
    assert "Fl4683ExactCellSelectV1" in product_source
    assert "Fl4693ProductGlyphProgramImageV1" in final_tuple_owner
    assert "g_final[compact] = tuple;" in final_tuple_owner


def _retired_reader_fixture_pipeline_hygiene_rejects_inverted_boxes_and_missing_page_pointer() -> None:
    module = _load_paper_validator()
    draft = TEX_PATH.read_text()
    inverted = draft.replace(
        r"finalized descriptors\\\globalSix{}",
        r"GPU descriptor\\and role scorer\\\globalSix{}",
        1,
    )
    rep = module.Report()
    module.check_tex_reader_prose_hygiene(rep, _reader_tex_from_draft(inverted), inverted)
    assert any("inverted role/row-before-descriptor" in error for error in rep.errors)

    missing_pointer = draft.replace(
        r"\pageref{spec:scoring}", r"\pageref{spec:missing-scoring}"
    )
    rep = module.Report()
    module.check_tex_reader_prose_hygiene(
        rep, _reader_tex_from_draft(missing_pointer), missing_pointer
    )
    assert any("page pointer to spec:scoring" in error for error in rep.errors)


def _retired_reader_fixture_paper_follower_sync_rejects_architecture_and_math_regressions() -> None:
    module = _load_paper_validator()
    draft = TEX_PATH.read_text()
    mutations = (
        (
            "The presentation contract routes both targets through one finalized tuple plane",
            "Both target presenters consume one finalized tuple plane",
            "follower-sync contract phrase",
        ),
        (
            r"U4 role arbitration\\fact owned route address",
            r"compiled exact cell selector\\fact owned route address",
            "native ownership figure must order",
        ),
        (
            "product-module execution evidence only",
            "working product renderer",
            "compact manuscript retains stale activation prose",
        ),
        (
            "sparse projection is a transport and product-program boundary",
            "all GlyphFact records carry intrinsic full port graphs",
            "compact manuscript missing contract token",
        ),
        (
            "same skeleton\ncomponent",
            "nearby boundary ink",
            "compact manuscript missing contract token",
        ),
    )
    for current, stale, expected_error in mutations:
        mutated = draft.replace(current, stale, 1)
        assert mutated != draft, f"mutation anchor disappeared: {current}"
        rep = module.Report()
        reader = _reader_tex_from_draft(mutated)
        artifact_index = (
            ROOT / "docs/agent/glyph-rendering-paper/PAPER_ARTIFACT_INDEX.md"
        ).read_text()
        module.check_tex_reader_prose_hygiene(rep, reader, mutated)
        module.check_compact_manuscript_contract(rep, reader, mutated)
        module.check_compact_product_activation_contract(rep, reader, mutated, artifact_index)
        module.check_compact_reader_status_hygiene(rep, reader, mutated)
        assert any(expected_error in error for error in rep.errors), rep.errors


def test_projected_scale_equation_matches_largest_singular_value() -> None:
    source = (ROOT / "scripts/fl4512_projected_scale.py").read_text()
    assert "math.sqrt(disc)" in source
    assert "math.sqrt((a - c) ** 2 + 4.0 * b * b)" not in source
    a, b, c = 1.0, 0.0, 4.0
    rho = math.sqrt((a + c + math.sqrt((a - c) ** 2 + 4 * b**2)) / 2)
    assert rho == 2.0


def _retired_reader_fixture_viewpoint_direction_panel_is_reader_facing() -> None:
    paper_dir = ROOT / "docs/agent/glyph-rendering-paper"
    panel = paper_dir / "f.pdf"
    assert panel.exists()
    tex = TEX_PATH.read_text()
    assert r"\includegraphics[width=0.96\linewidth]{f.pdf}" not in tex
    assert "figures/plot-direction-vocab-grid.png" not in tex
    assert "figures/plot-direction-runtime-strip.png" not in tex
    assert "HISTORICAL DIAGNOSTIC: LIMITED EVIDENCE" in tex
    assert r"\label{fig:native-ownership-pipeline}" in tex
    assert r"\label{tab:definitions}" in tex


def test_additive_runtime_variants_have_current_provenance() -> None:
    paper_dir = ROOT / "docs/agent/glyph-rendering-paper"
    tex = TEX_PATH.read_text()
    assert r"\label{fig:mesh200-reglyph-b}" in tex
    assert r"\label{fig:multi-feature-arbitration-b}" in tex
    assert "panel-temporal-pan-start.png" in tex
    assert "panel-temporal-pan-middle.png" in tex
    assert "panel-temporal-pan-end.png" in tex
    assert "panel-u7-pieta-runtime.png" in tex

    temporal = json.loads(
        (paper_dir / "figure_provenance/fig_temporal_presentation.json").read_text()
    )
    structural = json.loads(
        (paper_dir / "figure_provenance/fig_structural_scoring_runtime.json").read_text()
    )
    viewpoint = json.loads(
        (paper_dir / "figure_provenance/fig_viewpoint_direction_axes.json").read_text()
    )
    assert temporal["figure_id"] == "fig:mesh200-reglyph-b"
    assert structural["figure_id"] == "fig:multi-feature-arbitration-b"
    assert viewpoint["figure_id"] == "fig:compiler-pool-c"
    assert temporal["proof_status"] == "PROOF_OPEN"
    assert structural["proof_status"] == "PROOF_OPEN"
    assert temporal["input_receipts"]
    assert structural["input_receipts"]


def test_reference_maps_remain_visible_without_consuming_figure_numbers() -> None:
    tex = TEX_PATH.read_text()
    for heading in (
        "Reference map: projected feature extent.",
        "Reference map: descriptor reduction.",
        "Reference map: channel separation.",
        "Reference map: topology loss.",
        "Reference map: filled and line roles.",
    ):
        assert rf"\caption*{{\textbf{{{heading}}}" in tex
    for stale_label in (
        "fig:scale-reference-map",
        "fig:descriptor-reference-map",
        "fig:temporal-channel-map",
        "fig:topology-loss-map",
        "fig:filled-line-reference-map",
    ):
        assert rf"\label{{{stale_label}}}" not in tex


def _retired_reader_fixture_topology_thread_is_bounded_for_later_extraction() -> None:
    tex = TEX_PATH.read_text()
    assert r"\newcommand{\PaperSubtitle}{Digital Topology and Structural Matching within a Surface Aware Glyph Pipeline}" in tex
    required_once = (
        r"\section{Definitions and Method}",
        r"\label{sec:digital-glyph-topology}",
        r"\subsection{Offline glyph compiler}",
        r"\subsection{Legal rows and glyph-graph scoring}",
        r"\label{sec:feature-chain-topology}",
    )
    for token in required_once:
        assert tex.count(token) == 1
    positions = [
        tex.index(r"\subsection{Offline glyph compiler}"),
        tex.index(r"\subsection{Legal rows and glyph-graph scoring}"),
        tex.index(r"\label{sec:feature-chain-topology}"),
    ]
    assert positions == sorted(positions)


def _retired_reader_fixture_topology_claims_do_not_revive_symmetry_admission() -> None:
    tex = TEX_PATH.read_text()
    normalized_tex = " ".join(tex.split())
    assert "Raster symmetry and coset labels have no address or admission authority" in normalized_tex
    assert "the final tuple owner" in normalized_tex
    assert "The former current frame structural writer plus structural replay are deleted" not in normalized_tex
    assert "persistent active-stroke snakes" in normalized_tex
    assert "presentation boundary accepts exactly one final tuple" in normalized_tex
    assert "The solver chooses a path through a candidate lattice" in tex


def test_view_q15_equations_match_reference_evaluator() -> None:
    source = (ROOT / "scripts/fl4512_d6_aspect.py").read_text()
    assert "math.ceil(value * 32768.0 - 0.5)" in source
    assert "index = (b - 1) // d" in source


def test_missing_primary_captions_stay_short_and_nonpromotional() -> None:
    tex = TEX_PATH.read_text()
    reader = _reader_tex_from_draft(tex)
    assert reader.count(r"\missingfigurebox{") == 5
    for label in (
        "fig:per-cell-walkthrough",
        "fig:shade-band-result",
        "fig:topology-worked-example",
        "fig:admission-chain-offline",
        "fig:fl4652-pieta-progression",
    ):
        assert rf"\missingfigurebox{{FIG.~\ref{{{label}}}}}" in reader
    assert reader.count(r"\caption{Missing direct capture.") == 4


def test_authored_role_codebook_is_reader_legible_and_provenance_backed() -> None:
    paper_dir = ROOT / "docs/agent/glyph-rendering-paper"
    tex = TEX_PATH.read_text()
    assert "figures/plot-authored-role-codebook-v1.png" in tex
    assert r"\label{fig:authored-role-codebook}" in tex
    assert "The panel documents offline authored input" in tex
    assert "it does not report runtime gait quality" in tex
    assert "the raw shapes are recorded in the source comment" not in tex
    assert "head:      & U+25CB" not in tex
    provenance = json.loads(
        (paper_dir / "figure_provenance/fig_authored_role_codebook.json").read_text()
    )
    assert provenance["figure_id"] == "fig:authored-role-codebook"
    assert provenance["proof_status"] == "PROOF_OPEN"
    assert (paper_dir / "figures/plot-authored-role-codebook-v1.png").exists()
    assert "depicted native controls are obsolete authoring instruments" in tex
    assert "obsolete package anatomy, retained as authoring context" in tex
    assert "density bias edit predates the registered HTML editor surface" in tex
    assert "Reserved primary capture" not in tex
    assert "Paper v1" not in tex


def _retired_reader_fixture_separates_playable_product_from_html_authoring_surface() -> None:
    reader = _reader_tex_from_draft(TEX_PATH.read_text())
    normalized = " ".join(reader.split())
    assert "primary product surface" in normalized
    assert "browser authoring interface is a separate user facing editor" in normalized
    assert "Authoring evidence can establish the causal effect of a profile edit" in normalized
    assert "cannot substitute for normal play product evidence" in normalized
    assert "sole product and evidence surface" not in normalized
    assert "native editor and terminal frames" not in normalized
    assert "The GPU selector writes the final glyph identity" not in normalized
    assert "Three experiments use this procedure" not in normalized


def test_versioned_builder_owns_isolated_latex_sidecars() -> None:
    build_source = BUILD_SCRIPT_PATH.read_text()
    assert '"--prebuild-source"' in build_source
    assert "validate_fl4512_paper_lane.py" in build_source
    assert build_source.index('"--prebuild-source"') < build_source.index('"pdflatex"')
    assert "def run_postbuild_validator" in build_source
    postbuild_call = build_source.rindex("run_postbuild_validator()")
    assert build_source.index("record.write_text") < postbuild_call
    assert build_source.index("publish_build_index_row(", build_source.index("def main")) < postbuild_call
    assert postbuild_call < build_source.index("print(snapshot)")
    assert "restore_existing(previous_outputs)" in build_source
    assert "snapshot.unlink(missing_ok=True)" in build_source
    assert "record.unlink(missing_ok=True)" in build_source
    assert 'build_stem = f"{STEM}-versioned-' in build_source
    assert 'f"-jobname={build_stem}"' in build_source
    assert 'run("bibtex", build_stem)' in build_source
    assert 'run("bibtex", STEM)' not in build_source


def test_versioned_builder_transactionally_publishes_artifact_index_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_build_script()
    paper_dir = tmp_path / "paper"
    builds = paper_dir / "builds"
    builds.mkdir(parents=True)
    index = paper_dir / "PAPER_ARTIFACT_INDEX.md"
    index.write_text(
        "# Index\n\n"
        "| Version | Build timestamp | PDF | Build record | SHA-256 |\n"
        "|---|---|---|---|---|\n"
        "| `v0.9.84` | `old` | `builds/old.pdf` | `builds/old.json` | `abc` |\n\n"
        "`v0.9.84` is the current reader artifact.\n\n"
        "Trailing notes.\n",
        encoding="utf-8",
    )
    snapshot = builds / "2026-08-04T144030+0900-fl4512-paper-v0.9.85.pdf"
    record = snapshot.with_suffix(".json")
    snapshot.write_bytes(b"pdf")
    record.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(module, "PAPER_DIR", paper_dir)
    monkeypatch.setattr(module, "ARTIFACT_INDEX", index)

    module.publish_build_index_row(
        "v0.9.85",
        "2026-08-04T14:40:30+0900",
        snapshot,
        record,
        "def",
    )

    published = index.read_text(encoding="utf-8")
    expected = (
        "| `v0.9.85` | `2026-08-04T14:40:30+0900` | "
        "`builds/2026-08-04T144030+0900-fl4512-paper-v0.9.85.pdf` | "
        "`builds/2026-08-04T144030+0900-fl4512-paper-v0.9.85.json` | `def` |"
    )
    assert expected in published
    assert "`v0.9.84` is a superseded reader artifact" in published
    assert "`v0.9.85` is the current reader artifact" in published
    assert published.count("is the current reader artifact") == 1
    assert published.index(expected) < published.index("Trailing notes.")
    with pytest.raises(SystemExit, match="paper version is already indexed"):
        module.publish_build_index_row(
            "v0.9.85",
            "2026-08-04T14:40:30+0900",
            snapshot,
            record,
            "def",
        )


def test_versioned_builder_rollback_restores_prior_publication_bytes(tmp_path: Path) -> None:
    module = _load_build_script()
    alias = tmp_path / "paper.pdf"
    aux = tmp_path / "paper.aux"
    snapshot = tmp_path / "builds/paper-v1.pdf"
    record = tmp_path / "builds/paper-v1.json"
    build_tmp = tmp_path / "paper-versioned.pdf"
    alias.write_bytes(b"old pdf")
    aux.write_bytes(b"old aux")
    previous = module.capture_existing([alias, aux])
    alias.write_bytes(b"new pdf")
    aux.unlink()
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"new snapshot")
    record.write_text("{}\n", encoding="utf-8")
    build_tmp.write_bytes(b"tmp")

    module.rollback_publication(previous, snapshot, record, {".pdf": build_tmp})

    assert alias.read_bytes() == b"old pdf"
    assert aux.read_bytes() == b"old aux"
    assert not snapshot.exists()
    assert not record.exists()
    assert not build_tmp.exists()


def test_root_fl4512_paper_validator_cli_delegates_to_canonical_gate() -> None:
    root_validator = ROOT / "scripts/validate_fl4512_paper_lane.py"
    source = root_validator.read_text()
    assert "validate_contract" in source
    assert "--transform-relation-only" in source
    assert "docs/agent/glyph-rendering-paper/scripts/" in source
    assert "subprocess.run([sys.executable, str(canonical), *argv]).returncode" in source


def test_paper_hygiene_validator_passes_without_closing_runtime() -> None:
    receipt = json.loads(
        (
            ROOT
            / "docs/research/ascii/verification/fl4512/paper/2026-07-13-paper-hygiene/receipt.json"
        ).read_text()
    )
    assert receipt["runtime_closure"] == "OPEN"
    result = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "docs/agent/glyph-rendering-paper/scripts/validate_fl4512_paper_lane.py"
            ),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "[FL-4512] PAPER HYGIENE PASS" in result.stdout


def test_provenance_contracts_match_current_receipt_and_broken_pieta_state() -> None:
    module = _load_paper_validator()
    index = (ROOT / "docs/agent/glyph-rendering-paper/PAPER_ARTIFACT_INDEX.md").read_text()
    rep = module.Report()
    module.check_provenance_contracts(rep, index)
    assert not rep.errors


def test_provenance_contract_rejects_exact_join_claim_without_receipt() -> None:
    module = _load_paper_validator()
    index = (ROOT / "docs/agent/glyph-rendering-paper/PAPER_ARTIFACT_INDEX.md").read_text()
    stale = index.replace(
        "No receipt on disk.",
        "Review 07 exactly joins the pre-input snapshot, final tuples, and receipt.",
        1,
    )
    rep = module.Report()
    module.check_provenance_contracts(rep, stale)
    assert any("overclaims an exact receipt join" in error for error in rep.errors)


def test_current_build_record_carries_revision_and_dirty_scope() -> None:
    tex = TEX_PATH.read_text()
    version = re.search(r"\\newcommand\{\\PaperVersion\}\{([^}]+)\}", tex).group(1)
    timestamp = re.search(
        r"\\newcommand\{\\PaperBuildTimestamp\}\{([^}]+)\}", tex
    ).group(1)
    record = ROOT / "docs/agent/glyph-rendering-paper/builds" / (
        f"{timestamp.replace(':', '')}-fl4512-paper-{version}.json"
    )
    payload = json.loads(record.read_text())
    assert isinstance(payload.get("git_head"), str) and payload["git_head"] != "unknown"
    assert isinstance(payload.get("dirty_scope"), list)
    assert all(isinstance(path, str) and path for path in payload["dirty_scope"])


def test_paper_validator_rejects_stale_routing_owners() -> None:
    module = _load_paper_validator()
    canon = (ROOT / "docs/plans/2026-03-22-multiplayer-canonical-spec.md").read_text()

    current = module.Report()
    module.check_canon(current, canon)
    assert not current.errors

    stale = canon.replace(
        "Keep the manuscript as a follower research artifact, never a status tracker.",
        "The paper ledger owns frozen target-architecture intent.",
        1,
    ).replace("| RQ-202 |", "| RQ-202-REMOVED |", 1)
    rejected = module.Report()
    module.check_canon(rejected, stale)
    assert rejected.errors
    assert any("stale paper architecture owner" in error for error in rejected.errors)
    assert any("RQ-202 native refactor row missing" in error for error in rejected.errors)


def test_paper_validator_rejects_broad_browser_deletion_and_early_cutover() -> None:
    module = _load_paper_validator()
    canon = (ROOT / "docs/plans/2026-03-22-multiplayer-canonical-spec.md").read_text()
    stale = canon.replace(
        "native sidecar/presenter writers, Web identity reconstruction, and CP437 fallback authority are deleted",
        "browser presentation identity remains authoritative",
        1,
    ).replace(
        "P7 deep final-tuple ownership is IMPLEMENTED and CONNECTED",
        "P7 deep final-tuple ownership is deferred",
        1,
    )
    assert stale != canon
    rep = module.Report()
    module.check_canon(rep, stale)
    joined = "\n".join(rep.errors)
    assert "delete Web identity authority" in joined
    assert "current implementation boundary" in joined
