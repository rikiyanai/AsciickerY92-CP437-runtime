#!/usr/bin/env python3
"""FL-4876 MINIMAP-ORIGIN-01. The minimap marker projection had no origin.

`MARGIN_WORLD` appeared exactly once in
scripts/pipeline/compile_world_ir_classic_base.py -- at its use site, never at
a definition -- from the commit that introduced the loop (fe908e6e4). The
first time the marker path was ever reached it raised

    NameError: name 'MARGIN_WORLD' is not defined

so it has never produced a marker. Nothing caught it because the pre-assembly
gate refuses 27 lines before compile_base runs, and the builders below that
gate have never executed on this lane.

The projection belongs to compile_world_frame.WorldFrame.world_point: origin
shift, times content scale, plus the frame's own declared offsets. This module
re-implemented it and substituted a constant that did not exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = ROOT / "scripts" / "pipeline" / "compile_world_ir_classic_base.py"
FRAME = ROOT / "scripts" / "pipeline" / "compile_world_frame.py"


def test_the_phantom_constant_is_gone():
    """Falsifier: restore MARGIN_WORLD and this fails."""
    src = BASE.read_text(encoding="utf-8")
    uses = [ln for ln in src.splitlines()
            if "MARGIN_WORLD" in ln and not ln.lstrip().startswith("#")]
    assert uses == [], uses


def test_the_module_defines_every_name_it_uses():
    """The defect was a name used without a definition. Compile the module and
    check the marker function's globals resolve."""
    import py_compile, tempfile
    with tempfile.TemporaryDirectory() as d:
        py_compile.compile(str(BASE), cfile=str(Path(d) / "o.pyc"),
                           doraise=True)
    src = BASE.read_text(encoding="utf-8")
    import ast
    tree = ast.parse(src)
    assigned = {n.id for node in ast.walk(tree)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                for n in [node]}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                imported.add((a.asname or a.name).split(".")[0])
    assert "MARGIN_WORLD" not in (assigned | imported)


def test_the_projection_uses_the_frames_declared_offsets():
    src = BASE.read_text(encoding="utf-8")
    assert "frame.offset_x" in src
    assert "frame.offset_y" in src


def test_the_frame_is_the_owner_of_that_projection():
    """Pin the shape this module must agree with."""
    src = FRAME.read_text(encoding="utf-8")
    i = src.index("def world_point")
    block = src[i:i + 500]
    assert "origin_easting" in block
    assert "content_scale" in block
    assert "self.offset_x" in block
    assert "self.offset_y" in block


def test_the_two_axes_use_different_offsets():
    """The phantom constant was applied to BOTH axes, which cannot be right
    for a frame that declares an x and a y offset separately."""
    src = BASE.read_text(encoding="utf-8")
    i = src.index("frame.offset_x")
    j = src.index("frame.offset_y")
    assert i != j
    line_x = src[:i].rsplit("\n", 1)[-1] + src[i:].split("\n", 1)[0]
    assert "offset_y" not in line_x
