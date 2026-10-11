from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageSequence


ROOT = Path(__file__).resolve().parents[1]
GIF = ROOT / "docs/recordings/cp437-runtime-layer-transitions.gif"
RECEIPT = ROOT / "docs/recordings/cp437-runtime-layer-transitions.receipt.json"
README = ROOT / "README.md"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_layer_transition_recording_contract() -> None:
    receipt = json.loads(RECEIPT.read_text())
    gif_bytes = GIF.read_bytes()
    readme = README.read_text()

    assert receipt["schema_version"] == 1
    assert receipt["artifact_sha256"] == _sha256_bytes(gif_bytes)
    assert receipt["artifact_width"] == 956
    assert receipt["artifact_height"] == 386
    assert receipt["decoded_frame_count"] == 52
    assert receipt["frame_duration_ms"] == 150
    assert receipt["capture"]["source"] == (
        "real browser/WebAssembly client connected to the real authoritative C++ server"
    )
    assert receipt["composition"]["detail_resampling"] == "nearest-neighbor"
    assert receipt["composition"]["server_slot_evidence_rendered_in_gif"] is False

    image = Image.open(GIF)
    frames = [frame.convert("RGBA") for frame in ImageSequence.Iterator(image)]
    assert image.size == (956, 386)
    assert len(frames) == 52
    assert {frame.info.get("duration") for frame in ImageSequence.Iterator(Image.open(GIF))} == {
        150
    }

    frame_receipts = receipt["frames"]
    assert [frame["gif_index"] for frame in frame_receipts] == list(range(52))
    assert [_sha256_bytes(frame.tobytes()) for frame in frames] == [
        frame["decoded_rgba_sha256"] for frame in frame_receipts
    ]
    assert all(len(frame["source_png_sha256"]) == 64 for frame in frame_receipts)

    expected = {
        "baseline": (range(0, 8), [], []),
        "armor": (range(8, 16), [306], [411]),
        "helmet": (range(16, 24), [306, 301], [411, 410]),
        "sword": (range(24, 32), [306, 301, 303], [411, 410, 409]),
        "turn": (range(32, 44), [306, 301, 303], [411, 410, 409]),
        "move": (range(44, 52), [306, 301, 303], [411, 410, 409]),
    }
    phase_receipts = {phase["name"]: phase for phase in receipt["phases"]}
    assert list(phase_receipts) == list(expected)
    for name, (indices, slots, definitions) in expected.items():
        indices = list(indices)
        phase = phase_receipts[name]
        assert [phase["gif_start"], phase["gif_end"]] == [indices[0], indices[-1]]
        assert phase["server_slots"] == slots
        assert phase["server_definitions"] == definitions
        for index in indices:
            frame = frame_receipts[index]
            assert frame["phase"] == name
            assert frame["server_slots"] == slots
            assert frame["server_definitions"] == definitions

    assert phase_receipts["turn"]["sprite_angles"] == [0, 1, 2, 3, 4, 6, 7]
    move_start = phase_receipts["move"]["position_start"]
    move_end = phase_receipts["move"]["position_end"]
    assert math.dist(move_start[:2], move_end[:2]) > 2.0
    assert {frame_receipts[index]["authoritative_position"][2] for index in range(44, 52)} == {
        57.25
    }

    world_box = (0, 48, 610, 386)
    detail_box = (619, 49, 955, 385)
    black_world = Image.new("RGB", (610, 338), (0, 0, 0))
    black_detail = Image.new("RGB", (336, 336), (0, 0, 0))
    for frame in frames:
        assert ImageChops.difference(frame.crop(world_box).convert("RGB"), black_world).getbbox()
        assert ImageChops.difference(frame.crop(detail_box).convert("RGB"), black_detail).getbbox()

    representative_indices = [0, 8, 16, 24]
    representative_details = [
        frames[index].crop(detail_box).convert("RGB") for index in representative_indices
    ]
    assert all(
        ImageChops.difference(before, after).getbbox()
        for before, after in zip(representative_details, representative_details[1:])
    )

    assert "docs/recordings/cp437-runtime-layer-transitions.gif" in readme
    assert "docs/recordings/armored-block-feature-gameplay.gif" in readme
    assert "private standalone" not in readme.casefold()
    assert "CP437 byte-cell" in readme
    assert "extended-glyph sidecar" in readme
