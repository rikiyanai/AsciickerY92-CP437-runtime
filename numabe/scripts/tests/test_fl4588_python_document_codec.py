"""FL-4588 Python document-codec twin gate (runs without native builds).

Exercises scripts/fl4588/a3d_document_codec.py — the independent
observational mirror of the authoritative C++ product codec — over
synthesized hostile fixtures and the real tracked maps. This is the
Python half of the cross-evaluator parity gate; the C++ half re-runs
after the FL-4162 runtime coordination clears.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "fl4588"))

from a3d_document_codec import (  # noqa: E402
    A3dCodecError,
    parse_document,
    roundtrip_verdict,
    serialize_canonical,
)


def minimal_doc(patches=(), enemies=b"", enemy_count=0) -> bytes:
    """One synthetic document: N patches, empty world, given enemy bytes."""
    out = bytearray(struct.pack("<4sIII", b"AS3D", 16, len(patches), 0))
    for x, y in patches:
        out += struct.pack("<ii", x, y) + bytes(180)
    out += bytes(256 * 4 * 16 * 8)          # material table
    out += struct.pack("<ii", -6, 0)        # world v6, zero instances
    out += struct.pack("<i", 0)             # no player start
    out += struct.pack("<i", enemy_count) + enemies
    out += struct.pack("<I", 0)             # zero markers
    return bytes(out)


def enemy(alive=1, rmin=0, rmax=5, equip=5) -> bytes:
    return struct.pack("<3f8i", 1.0, 2.0, 3.0, alive, rmin, rmax,
                       equip, equip, equip, equip, equip)


def test_minimal_document_is_canonical_and_stable():
    data = minimal_doc()
    verdict = roundtrip_verdict(data)
    assert verdict["roundtrip_stable"] and verdict["input_canonical"]


def test_duplicate_patch_coordinates_rejected():
    with pytest.raises(A3dCodecError, match="duplicate patch"):
        parse_document(minimal_doc(patches=((3, 4), (3, 4))))


def test_canonical_patch_order_is_sorted_y_then_x():
    data = minimal_doc(patches=((5, 1), (2, 1), (0, 0)))
    doc = parse_document(data)
    ordered = serialize_canonical(doc)
    coords = [struct.unpack_from("<ii", ordered, 16 + i * 188)[:2]
              for i in range(3)]
    assert coords == [(0, 0), (2, 1), (5, 1)]


def test_enemy_frozen_ranges_match_consumption_evidence():
    # Corpus-evidenced saturating equipment (11) and shift-safe revive (30).
    ok = minimal_doc(enemies=enemy(rmax=30, equip=11), enemy_count=1)
    assert roundtrip_verdict(ok)["roundtrip_stable"]
    for bad in (enemy(equip=16), enemy(rmin=31, rmax=31), enemy(alive=0),
                enemy(rmin=6, rmax=2)):
        with pytest.raises(A3dCodecError, match="enemy record"):
            parse_document(minimal_doc(enemies=bad, enemy_count=1))


def test_real_maps_match_cpp_gate_verdicts():
    # The same three verdicts the C++ gate produced (codec contract doc).
    original = roundtrip_verdict(
        (ROOT / "assets/a3d/game_map_y8_original_game_map.a3d").read_bytes())
    assert original["roundtrip_stable"]
    assert original["canonical_sha_input_len"] == 1243989  # == C++ canonical1
    assert original["canonical_sha256"] == (
        "b8db82c7a54ee1096038ea6a84c5245538bd492b6dee15d5ab77b72a1022c629"
    )
    y8 = roundtrip_verdict((ROOT / "assets/a3d/game_map_y8.a3d").read_bytes())
    assert y8["roundtrip_stable"] and y8["markers"] == 3  # incl. TYPE_REGION
    assert y8["canonical_sha256"] == (
        "05919f1fe1bc5baa2329c3d150099cc3100ce5e126ff70a0e2cd2b0619b797c5"
    )
    with pytest.raises(A3dCodecError, match="enemy record"):
        parse_document((ROOT / "assets/a3d/sandbox_20x20.a3d").read_bytes())
