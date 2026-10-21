"""FL-4588 hardened A3D section-codec gate.

Compiles and runs the standalone C++ harness for the extracted
enemy-generator and minimap-marker document-codec cores
(scripts/fl4588/a3d_section_codec_test_cli.cpp). The harness enforces the
operator reviews of 2026-07-16:

- explicit little-endian wire encoding with known-byte fixtures
  (host-order independence; one exact wire definition the independent
  Python evaluator can mirror);
- byte-stable round trips through caller-owned lists in file order;
- fail-closed parsing: truncated prefixes, every per-field boundary cut,
  hostile counts, NaN values, frozen authoring-range violations, embedded
  NULs, nonzero reserved bytes, unknown marker types, overlong text, and
  unexplained trailing bytes under an exact budget are all rejected;
- serializer preflight: an invalid in-memory node emits ZERO bytes;
- global-registry preservation: malformed input never destroys the
  previously loaded document sections (parse first, swap after success).

One harness process, one pytest entry: the first failing check aborts the
binary with its label on stdout, which pytest surfaces verbatim.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_a3d_section_codec_harness(tmp_path: Path) -> None:
    binary = tmp_path / "a3d_section_codec_test"
    # Same include set the editor build uses; no EDITOR define, so the
    # harness stays free of sprite/GL editor-only symbols.
    subprocess.run(
        [
            "c++", "-std=c++20",
            "-I.", "-Ivendor", "-Iengine", "-Iengine/render",
            "-Ieditor", "-Iplatform", "-Iserver",
            "scripts/fl4588/a3d_section_codec_test_cli.cpp",
            "engine/enemygen.cpp",
            "engine/world_minimap_markers.cpp",
            "-o", str(binary),
        ],
        cwd=ROOT,
        check=True,
    )
    completed = subprocess.run(
        [str(binary)], cwd=ROOT, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "all " in completed.stdout and " checks passed" in completed.stdout
