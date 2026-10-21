from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_native_presenter_observes_the_committed_plane_after_copy() -> None:
    owner = _read("apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp")
    presenter = _read("platform/terminal_gl_present.cpp")
    census = _read("platform/fl4684_scene_fact_emission_census.h")

    assert "g_committed_plane_hash = HashFinalTuplePlaneV1" in owner
    assert "Fl4693FinalTupleObserveNativeConsumerV1" in owner
    assert "presented_glyphs[index] != tuple.final_gid" in owner
    assert "cell.fg != tuple.final_fg || cell.bk != tuple.final_bg" in owner
    assert "consumer_hash == g_committed_plane_hash" in owner
    assert "Fl4693FinalTupleObserveNativeConsumerV1(" in presenter
    assert "fl4693_final_tuple_consumer_unchanged_frames" in census
    assert "fl4693_final_tuple_consumer_mismatch_cells" in census


def test_web_proof_reads_back_the_gpu_transport_bytes() -> None:
    proof = _read("scripts/proofs/proof_fl4684_web_live_producer_motion.js")

    assert "FL4693_TUPLE_CONSUMER_NO_INPUT" in proof
    assert "FL4693_TUPLE_EQUIVALENCE_NO_INPUT" not in proof
    assert "gl.framebufferTexture2D(" in proof
    assert "gl.readPixels(0, 0, width * 12, height" in proof
    assert "gpu_readback_mismatch_bytes" in proof
    assert "gpu_readback_byte_identical" in proof
    assert "source_fnv1a32" in proof
    assert "gpu_fnv1a32" in proof
