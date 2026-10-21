#!/usr/bin/env python3
"""FL-4873 task 4: portable session.json (viewer writer, Python reader).

The viewer's own sessionDoc/sessionProblem JavaScript is executed under node
against a synthetic bundle; the document it writes must validate in
scripts/recording/f9_session.py, and every mismatch must be rejected by both
sides with no substitution (a 162-cell selection never stands in for 315).

Run: python3 -m pytest scripts/tests/test_f9_session.py -q
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "recording"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import f9_session  # noqa: E402
from test_f9_bundle_finalize import _bundle  # noqa: E402

BUILDER = ROOT / "scripts" / "build_f10_frames_html.py"


def _viewer_js(bundle: Path) -> str:
    subprocess.run([sys.executable, str(BUILDER), str(bundle)], check=True, capture_output=True)
    html = (bundle / "frames.html").read_text()
    js = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    consts = "\n".join(re.findall(r"^const (?:FRAMES|CAPTURE|SESSION_EMBED) = .*;$", js, re.M))
    geometry = js[js.index("function matrixOf(f)"):js.index("function drawGrid(")]
    session = js[js.index("let notes = {}"):js.index("(function readState()")]
    return (consts + "\nconst location = {pathname: '/bundle/frames.html'};\nlet cur = 0, selSet = [];\n" + geometry) + "\n" + session


def _node(bundle: Path, tmp_path: Path, body: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node unavailable; viewer session JS unverified")
    driver = tmp_path / "session_driver.js"
    driver.write_text(_viewer_js(bundle) + "\n" + body)
    out = subprocess.run([node, str(driver)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_viewer_session_validates_in_python(tmp_path):
    bundle = _bundle(tmp_path)
    doc = _node(bundle, tmp_path,
                "selSet = [[3,1],[0,0],[4,1]]; notes = {'1': 'shimmer here'}; marked = [2]; cur = 1;\n"
                "console.log(JSON.stringify(sessionDoc()));")
    assert doc["selection"]["cells"] == [[0, 0], [3, 1], [4, 1]]
    assert doc["selection"]["digest"] == f9_session.selection_digest(doc["selection"]["cells"])
    (bundle / "session.json").write_text(json.dumps(doc))
    loaded = f9_session.load_session(bundle)
    assert loaded["count"] == 3 and loaded["cells"] == [(0, 0), (3, 1), (4, 1)]
    assert loaded["referenced"] == [1, 2] and loaded["notes"] == {"1": "shimmer here"}
    assert loaded["capture"]["id"] == bundle.name
    # Moving the bundle keeps the annotations: identity is content, not path.
    moved = tmp_path / "moved" / bundle.name
    shutil.copytree(bundle, moved)
    assert f9_session.load_session(moved)["digest"] == loaded["digest"]


@pytest.mark.parametrize("mutate,code", [
    (lambda d: d["capture"].update(id="FL4359_other"), "capture_id"),
    (lambda d: d["capture"].update(manifest_sha256="0" * 64), "manifest_hash"),
    (lambda d: d["grid"].update(cols=6), "grid"),
    (lambda d: d["selection"]["cells"].append([9, 9]), "selection_range"),
    (lambda d: d["selection"].update(count=5), "selection_count"),
    (lambda d: d["selection"]["cells"].__setitem__(0, [2, 1]), "selection_digest"),
    (lambda d: d["frames"].update(referenced=[99]), "frame_reference"),
    (lambda d: d.update(schema="asciicker.f10_session.v1"), "schema"),
])
def test_mismatches_are_rejected_by_python_and_viewer(tmp_path, mutate, code):
    bundle = _bundle(tmp_path)
    good = _node(bundle, tmp_path, "selSet = [[0,0],[1,0]]; cur = 0;\nconsole.log(JSON.stringify(sessionDoc()));")
    bad = copy.deepcopy(good)
    mutate(bad)
    with pytest.raises(f9_session.SessionMismatch) as err:
        f9_session.validate(bad, bundle)
    assert err.value.code == code
    problem = _node(bundle, tmp_path, f"console.log(JSON.stringify(sessionProblem({json.dumps(bad)})));")
    assert problem, "viewer import must reject the same mismatch"


def test_selection_size_never_substitutes(tmp_path):
    bundle = _bundle(tmp_path)
    doc = _node(bundle, tmp_path, "selSet = [[0,0],[1,0],[2,0]]; console.log(JSON.stringify(sessionDoc()));")
    (bundle / "session.json").write_text(json.dumps(doc))
    assert f9_session.load_session(bundle, expect_cells=3)["count"] == 3
    # Multi-row selections: the viewer and the reader agree on (y, x) order.
    doc = _node(bundle, tmp_path, "selSet = [[4,0],[0,1],[2,0],[1,1]]; console.log(JSON.stringify(sessionDoc()));")
    (bundle / "session.json").write_text(json.dumps(doc))
    assert f9_session.load_session(bundle)["cells"] == [(2, 0), (4, 0), (0, 1), (1, 1)]
    doc = _node(bundle, tmp_path, "selSet = [[0,0],[1,0],[2,0]]; console.log(JSON.stringify(sessionDoc()));")
    (bundle / "session.json").write_text(json.dumps(doc))
    with pytest.raises(f9_session.SessionMismatch) as err:
        f9_session.load_session(bundle, expect_cells=2)
    assert err.value.code == "selection_count_mismatch"


def test_saved_session_is_embedded_on_rebuild(tmp_path):
    bundle = _bundle(tmp_path)
    doc = _node(bundle, tmp_path, "selSet = [[2,1]]; console.log(JSON.stringify(sessionDoc()));")
    (bundle / "session.json").write_text(json.dumps(doc))
    subprocess.run([sys.executable, str(BUILDER), str(bundle)], check=True, capture_output=True)
    html = (bundle / "frames.html").read_text()
    embedded = json.loads(re.search(r"^const SESSION_EMBED = (.*);$", html, re.M).group(1))
    assert embedded["selection"]["cells"] == [[2, 1]]
