from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

STRUCTURAL_PROBE_SYMBOLS = (
    "Fl4512GetStructuralTurnSnapshotJsonV1",
    "Fl4512ArmStructuralTurnMeasurementV1",
    "Fl4512GetStructuralTurnMeasurementStatusJsonV1",
)


def test_structural_probe_exports_are_unicode_product_only() -> None:
    header = (ROOT / "platform/term.h").read_text(encoding="utf-8")
    guard = "#if defined(FL4693_PRODUCT_GLYPH_PROGRAM_ENABLED)"
    block_start = header.index(guard, header.index("int TermDumpBridgeCells"))
    block_end = header.index("#endif", block_start)

    for symbol in STRUCTURAL_PROBE_SYMBOLS:
        assert block_start < header.index(symbol) < block_end

    assert header.index("int TermDumpBridgeCells") < block_start
    assert header.index("TermCaptureArtifactSnapshotC") > block_end


def test_classic_preprocessed_closure_has_no_structural_probe_exports() -> None:
    preprocessed = ROOT / ".o_game_classic"
    if not preprocessed.exists():
        return

    for path in preprocessed.rglob("*.ii"):
        source = path.read_text(encoding="utf-8", errors="ignore")
        for symbol in STRUCTURAL_PROBE_SYMBOLS:
            assert symbol not in source, f"{symbol} remains in {path}"
