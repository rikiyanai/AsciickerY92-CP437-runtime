from __future__ import annotations

import copy
import hashlib
import json
import math
import struct
import sys
import tarfile
import pytest
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import fl4359_canvas_trace as trace

CANVAS_ROOT: Path
RECEIPT_PATH: Path


@pytest.fixture(scope="session", autouse=True)
def preserved_canvas_capture(tmp_path_factory):
    """Use tracked GPU evidence; never depend on a developer's ignored run tree."""
    global CANVAS_ROOT, RECEIPT_PATH
    directory = ROOT / "artifacts/fl4691-optimization-traceability-20260913/canvas-r10-preservation"
    archive = next(directory.glob("*ROLE-COMPONENT-EVIDENCE*.gz"))
    CANVAS_ROOT = tmp_path_factory.mktemp("canvas-r10")
    prefix = ".run/fl4691-recovery-work/canvas-root-integration-r10/raw/canvas/"
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            if member.isfile() and member.name.startswith(prefix):
                name = member.name[len(prefix):]
                assert Path(name).name == name and member.size <= 2 * 1024 * 1024
                with bundle.extractfile(member) as source:
                    (CANVAS_ROOT / name).write_bytes(source.read())
    RECEIPT_PATH = CANVAS_ROOT / "observer_receipt.json"
    receipt = json.loads(RECEIPT_PATH.read_text())
    for frame in receipt["frames"]:
        frame["path"] = str(CANVAS_ROOT / Path(frame["path"]).name)
        for sidecar in frame["canvas_provenance"]["binary_sidecars"].values():
            if "path" in sidecar:
                sidecar["path"] = str(CANVAS_ROOT / Path(sidecar["path"]).name)
    RECEIPT_PATH.write_text(json.dumps(receipt))


def _receipt() -> dict:
    return json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))


def _frame() -> dict:
    return _receipt()["frames"][0]


def _sidecar(path: Path, data: bytes, *, status: str = "ok") -> dict:
    path.write_bytes(data)
    return {"status": status, "path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "encoding": "raw_packed_bytes"}


def _read(path: Path) -> bytes:
    return path.read_bytes()


def test_all_five_preserved_r10_frames_replay_exact() -> None:
    result = trace.read_capture(RECEIPT_PATH, replay=True, row_chunk=17)
    assert result["status"] == "ok"
    assert result["exact"] is True
    assert result["frame_count"] == 5
    assert [row["mismatched_pixels"] for row in result["frames"]] == [0] * 5
    assert [row["max_channel_error"] for row in result["frames"]] == [0] * 5
    first = result["frames"][0]
    assert len(first["pixel_record_pixels"]) == 3345
    assert len(first["pixel_record_offsets"]) == 3346
    assert len(first["ordered_record_indices"]) == 4134
    assert len(first["contributing_sources"]) == 38


def test_structural_path_is_explicitly_non_replaying() -> None:
    result = trace.read_frame(_frame(), receipt_root=CANVAS_ROOT, replay=False)
    assert result["status"] == "ok"
    assert result["reason"] == "structural_validation_only"
    assert result["replay_performed"] is False
    assert result["exact"] is False
    assert len(result["ordered_record_indices"]) == 4134


def test_integer_identity_and_row_chunk_bounds() -> None:
    large_integer = 2**54 + 123
    assert trace._int(large_integer, "rid") == large_integer
    try:
        trace._int(float(2**53 + 2), "rid")
    except trace.CanvasTraceError as exc:
        assert str(exc) == "rid_precision_loss"
    else:
        raise AssertionError("imprecise JSON float was accepted")

    frame = _frame()
    assert trace.read_frame(frame, receipt_root=CANVAS_ROOT, row_chunk=128)["status"] == "ok"
    assert trace.read_frame(frame, receipt_root=CANVAS_ROOT, row_chunk=129)["reason"] == "row_chunk_out_of_bounds"
    assert trace.read_frame(frame, receipt_root=CANVAS_ROOT, row_chunk="bad")["reason"] == "row_chunk_out_of_bounds"


def test_rejects_frame_hash_token_target_and_sidecar_hash() -> None:
    frame = _frame()
    bad_png = copy.deepcopy(frame)
    bad_png["sha256"] = "0" * 64
    assert trace.read_frame(bad_png, receipt_root=CANVAS_ROOT)["reason"] == "final_png_sha256_mismatch"

    bad_token = copy.deepcopy(frame)
    bad_token["canvas_request_token"] += 100
    assert trace.read_frame(bad_token, receipt_root=CANVAS_ROOT)["reason"] == "canvas_dispatch_request_token_mismatch"

    bad_target = copy.deepcopy(frame)
    bad_target["canvas_provenance"]["target_info"]["format"] = trace.FORMAT_RGBA8_SRGB
    assert trace.read_frame(bad_target, receipt_root=CANVAS_ROOT)["reason"] == "canvas_target_format_transfer_mismatch"

    bad_sidecar = copy.deepcopy(frame)
    bad_sidecar["canvas_provenance"]["binary_sidecars"]["fragments"]["sha256"] = "0" * 64
    assert trace.read_frame(bad_sidecar, receipt_root=CANVAS_ROOT)["reason"] == "canvas_fragments_sha256_mismatch"


def test_rejects_nonfinite_fragment_and_unjoined_source(tmp_path: Path) -> None:
    frame = _frame()

    bad_fragment_frame = copy.deepcopy(frame)
    fragment_path = tmp_path / "nan.fragments.bin"
    fragment_bytes = bytearray(_read(Path(frame["canvas_provenance"]["binary_sidecars"]["fragments"]["path"])))
    struct.pack_into("<f", fragment_bytes, 0, math.nan)
    bad_fragment_frame["canvas_provenance"]["binary_sidecars"]["fragments"] = _sidecar(fragment_path, bytes(fragment_bytes))
    assert trace.read_frame(bad_fragment_frame, receipt_root=CANVAS_ROOT)["reason"] == "canvas_fragment_non_finite_color"

    bad_source_frame = copy.deepcopy(frame)
    source_path = tmp_path / "bad.sources.bin"
    source_bytes = bytearray(_read(Path(frame["canvas_provenance"]["binary_sidecars"]["sources"]["path"])))
    struct.pack_into("<Q", source_bytes, 16, 0xDEADBEEF)
    bad_source_frame["canvas_provenance"]["binary_sidecars"]["sources"] = _sidecar(source_path, bytes(source_bytes))
    assert trace.read_frame(bad_source_frame, receipt_root=CANVAS_ROOT)["reason"] == "source_canvas_item_rid_not_in_inventory"


def test_png_and_external_raw_binding_fail_closed(tmp_path: Path) -> None:
    frame = _frame()
    with Image.open(frame["path"]) as image:
        raw = image.convert("RGBA").tobytes()
    raw_path = tmp_path / "frame.rgba8.bin"
    raw_path.write_bytes(raw)
    result = trace.read_frame(frame, receipt_root=CANVAS_ROOT, actual_raw_path=raw_path)
    assert result["status"] == "ok" and result["exact"] is True
    structural = trace.read_frame(frame, receipt_root=CANVAS_ROOT, replay=False, actual_raw_path=raw_path)
    assert structural["status"] == "ok" and structural["reason"] == "structural_validation_only"

    tampered = bytearray(raw)
    tampered[0] ^= 1
    raw_path.write_bytes(tampered)
    result = trace.read_frame(frame, receipt_root=CANVAS_ROOT, actual_raw_path=raw_path)
    assert result["status"] == "failed"
    assert result["reason"] == "actual_raw_payload_mismatch"
    structural = trace.read_frame(frame, receipt_root=CANVAS_ROOT, replay=False, actual_raw_path=raw_path)
    assert structural["status"] == "failed"
    assert structural["reason"] == "actual_raw_payload_mismatch"


def test_histogram_counts_each_mismatch_once(tmp_path: Path) -> None:
    frame = copy.deepcopy(_frame())
    with Image.open(frame["path"]) as image:
        image = image.convert("RGBA")
        pixel = list(image.getpixel((0, 0)))
        pixel[0] ^= 1
        image.putpixel((0, 0), tuple(pixel))
        png_path = tmp_path / "tampered.png"
        image.save(png_path, format="PNG")
    frame["path"] = str(png_path)
    frame["sha256"] = hashlib.sha256(png_path.read_bytes()).hexdigest()
    result = trace.read_frame(frame, receipt_root=tmp_path, replay=True)
    assert result["status"] == "ok"
    assert result["exact"] is False
    assert result["mismatched_pixels"] == 1
    assert sum(result["signed_error_histogram"].values()) == 1


def test_png_extent_is_rejected_before_decode(monkeypatch) -> None:
    frame = copy.deepcopy(_frame())
    frame["canvas_provenance"]["target_info"]["width"] = 1
    frame["canvas_provenance"]["target_info"]["height"] = 1
    frame["size"] = [1, 1]
    calls: list[str] = []

    class FakeImage:
        format = "PNG"
        mode = "RGBA"
        size = (1 << 30, 1 << 30)

        def load(self) -> None:
            calls.append("load")

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(trace.Image, "open", lambda _path: FakeImage())
    result = trace.read_frame(frame, receipt_root=CANVAS_ROOT)
    assert result["status"] == "failed"
    assert result["reason"] == "final_png_format_or_extent_invalid"
    assert calls == ["close"]


def test_legitimate_empty_capture_replays_exact(tmp_path: Path) -> None:
    frame = copy.deepcopy(_frame())
    width, height = frame["size"]
    base_path = Path(frame["canvas_provenance"]["binary_sidecars"]["pre_canvas_base"]["path"])
    base = base_path.read_bytes()
    empty_png = tmp_path / "empty.png"
    Image.frombytes("RGBA", (width, height), base).save(empty_png, format="PNG")
    empty_frame_path = tmp_path / "empty.frame"
    empty_frame_path.write_bytes(base)
    fragments_meta = _sidecar(tmp_path / "empty.fragments.bin", b"")
    sources_meta = _sidecar(tmp_path / "empty.sources.bin", b"")
    base_meta = _sidecar(tmp_path / "empty.base.bin", base)
    frame["path"] = str(empty_png)
    frame["sha256"] = hashlib.sha256(empty_png.read_bytes()).hexdigest()
    frame["canvas_provenance"]["record_count"] = 0
    frame["canvas_provenance"]["binary_sidecars"] = {
        "fragments": fragments_meta,
        "sources": sources_meta,
        "pre_canvas_base": base_meta,
    }
    result = trace.read_frame(frame, receipt_root=tmp_path, replay=True, actual_raw_path=empty_frame_path)
    assert result["status"] == "ok" and result["exact"] is True
    assert len(result["pixel_record_pixels"]) == 0
    assert len(result["pixel_record_offsets"]) == 1

    absent = copy.deepcopy(frame)
    absent["canvas_provenance"]["binary_sidecars"]["fragments"] = {"status": "absent"}
    result = trace.read_frame(absent, receipt_root=tmp_path, replay=True)
    assert result["status"] == "failed"
    assert result["reason"] == "canvas_fragments_not_present"


def test_nonzero_clear_capture_replays_encoded_clear(tmp_path: Path) -> None:
    frame = copy.deepcopy(_frame())
    width, height = frame["size"]
    clear = np.asarray([0.2, 0.4, 0.6, 0.8], dtype=np.float32)
    encoded = trace._encode_rgba(clear, False)
    clear_bytes = np.tile(encoded, width * height).astype(np.uint8).tobytes()
    png_path = tmp_path / "clear.png"
    Image.frombytes("RGBA", (width, height), clear_bytes).save(png_path, format="PNG")
    frame["path"] = str(png_path)
    frame["sha256"] = hashlib.sha256(png_path.read_bytes()).hexdigest()
    provenance = frame["canvas_provenance"]
    provenance["record_count"] = 0
    provenance["binary_sidecars"] = {
        "fragments": _sidecar(tmp_path / "clear.fragments.bin", b""),
        "sources": _sidecar(tmp_path / "clear.sources.bin", b""),
        "pre_canvas_base": {"status": "absent", "reason": "clear_action"},
    }
    target = provenance["target_info"]
    target["load_action"] = "clear"
    target["clear_requested"] = True
    target["clear_color_rgba"] = clear.tolist()
    target["base_capture_supported"] = False
    result = trace.read_frame(frame, receipt_root=tmp_path, replay=True)
    assert result["status"] == "ok" and result["exact"] is True


def test_canonical_extent_empty_replay_uses_row_chunks(tmp_path: Path) -> None:
    width, height = 3600, 2252
    frame = copy.deepcopy(_frame())
    frame["size"] = [width, height]
    base = np.zeros((height, width, 4), dtype=np.uint8).tobytes()
    base_meta = _sidecar(tmp_path / "canonical.base.bin", base)
    fragment_meta = _sidecar(tmp_path / "canonical.fragments.bin", b"")
    source_meta = _sidecar(tmp_path / "canonical.sources.bin", b"")
    png_path = tmp_path / "canonical.png"
    Image.frombytes("RGBA", (width, height), base).save(png_path, format="PNG")
    frame["path"] = str(png_path)
    frame["sha256"] = hashlib.sha256(png_path.read_bytes()).hexdigest()
    provenance = frame["canvas_provenance"]
    provenance["record_count"] = 0
    provenance["binary_sidecars"] = {"fragments": fragment_meta, "sources": source_meta, "pre_canvas_base": base_meta}
    target = provenance["target_info"]
    target["width"] = width
    target["height"] = height
    result = trace.read_frame(frame, receipt_root=tmp_path, replay=True, row_chunk=97)
    assert result["status"] == "ok" and result["exact"] is True
    assert result["mismatched_pixels"] == 0


def test_v3_preserves_fragment_abi_and_requires_target_texture_identity():
    frame = _frame()
    frame["canvas_provenance"]["schema"] = "fl4691.canvas_provenance.v3"
    frame["canvas_provenance"]["target_info"]["texture_rid"] = 123
    result = trace.read_frame(frame, receipt_root=CANVAS_ROOT, replay=True)
    assert result["status"] == "ok" and result["exact"]
    del frame["canvas_provenance"]["target_info"]["texture_rid"]
    result = trace.read_frame(frame, receipt_root=CANVAS_ROOT, replay=False)
    assert result["status"] != "ok"
    frame["canvas_provenance"]["schema"] = "fl4691.canvas_provenance.v999"
    result = trace.read_frame(frame, receipt_root=CANVAS_ROOT, replay=False)
    assert result["reason"] == "canvas_provenance_schema_unsupported"
