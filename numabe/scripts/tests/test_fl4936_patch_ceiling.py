"""FL-4936 phase 1 guards: shared 1,000,000 patch ceiling and exact AKM float parse.

Operator approval 2026-10-13 replaced the 2026-07-16 freeze of 100,000 patches
with 1,000,000. The ceiling lives in four places that must agree:
engine/terrain.cpp CLASSIC_MAX_PATCHES (load and save), scripts/validate_a3d.py
MAX_PATCHES, scripts/fl4588/a3d_document_codec.py MAX_PATCHES and
apps/asciicker-presenter/asciiid_counterfactual.cpp kA3dMaxPatches.
"""

import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import validate_a3d  # noqa: E402
from scripts.fl4588 import a3d_document_codec as codec  # noqa: E402

CEILING = 1_000_000
NUMABE_1_5X_PATCHES = 115_092  # build_reg_69, exceeded the old 100k freeze


def _header(patches: int) -> bytes:
    return b"AS3D" + struct.pack("<III", 16, patches, 0)


def test_python_ceilings_are_one_million():
    assert validate_a3d.MAX_PATCHES == CEILING
    assert codec.MAX_PATCHES == CEILING


def test_cpp_ceilings_match_python():
    terrain = (REPO / "engine/terrain.cpp").read_text()
    match = re.search(r"#define\s+CLASSIC_MAX_PATCHES\s+(\d+)u", terrain)
    assert match and int(match.group(1)) == CEILING
    # Both enforcement sites must use the one constant, not a second literal.
    assert terrain.count("> CLASSIC_MAX_PATCHES") == 2
    presenter = (REPO / "apps/asciicker-presenter/asciiid_counterfactual.cpp").read_text()
    match = re.search(r"kA3dMaxPatches\s*=\s*(\d+);", presenter)
    assert match and int(match.group(1)) == CEILING


def test_validator_header_accepts_1_5x_numabe_and_ceiling():
    assert validate_a3d.read_file_header(_header(NUMABE_1_5X_PATCHES))[1] == NUMABE_1_5X_PATCHES
    assert validate_a3d.read_file_header(_header(CEILING))[1] == CEILING


def test_validator_header_rejects_above_ceiling():
    with pytest.raises(validate_a3d.ValidationError, match="Unreasonable patch count"):
        validate_a3d.read_file_header(_header(CEILING + 1))


def test_codec_rejects_above_ceiling():
    with pytest.raises(codec.A3dCodecError, match="patch count invalid"):
        codec.parse_document(_header(CEILING + 1))


@pytest.mark.skipif(shutil.which("c++") is None, reason="no C++ compiler")
def test_akm_fast_float_parse_is_bit_identical_to_strtod(tmp_path):
    binary = tmp_path / "akm_harness"
    subprocess.run(
        ["c++", "-std=c++17", "-O2", "-I", str(REPO / "engine"),
         str(REPO / "scripts/tests/fl4936_akm_number_harness.cpp"), "-o", str(binary)],
        check=True, cwd=REPO)
    corpus = REPO / ".run/fl4876_builds/build_reg_64/meshes"
    args = [str(binary)] + ([str(corpus)] if corpus.is_dir() else [])
    result = subprocess.run(args, check=True, capture_output=True, text=True, cwd=REPO)
    assert "bit-identical to strtod" in result.stdout
