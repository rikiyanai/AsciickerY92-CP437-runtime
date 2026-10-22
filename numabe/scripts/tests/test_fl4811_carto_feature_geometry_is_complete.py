"""The carto feature sidecar must emit COMPLETE geometry (FL-4811 / FL-4809).

WHAT THIS GUARDS
================
``scripts/pipeline/osm_carto_stamper.py`` used to write

    "world_vertices": world_pts if len(world_pts) <= 40
                      else world_pts[:40] + ["...truncated"]

which put a STRING inside a coordinate array and silently dropped vertices:
in the shipped SBU run 6 of 391 features were lossy that way and
``way/55446707`` lost 33 of its 73 points. Anything that trusted the field
received a corrupt polygon, and the World IR ingest owner had to carry a
permanent accommodation for a malformed producer.

The field is geometry EVIDENCE, so the producer is now the thing that is
correct: the complete ring is written and ``world_vertex_count`` is written
beside it. This test fails if the cap ever comes back, and it asserts the
completeness invariant rather than the absence of one particular marker
string, so a differently-spelled truncation is caught too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / "scripts" / "pipeline"
for p in (str(REPO_ROOT), str(PIPELINE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

RUN_DIR = REPO_ROOT / "assets" / "meshes" / "osm_runs" / "sbu_carto_scale225_20260601_canonical_ramps"

pytest.importorskip("PIL", reason="osm_carto_render needs Pillow")


def _stamper():
    import osm_carto_stamper  # noqa: PLC0415

    return osm_carto_stamper


def _feature(n_vertices: int, feature_type: str = "building"):
    from osm_carto_render import CartoFeature  # noqa: PLC0415

    # A closed ring on a circle: every point distinct, first == last.
    import math

    pts = [
        (round(50.0 + 20.0 * math.cos(2 * math.pi * i / (n_vertices - 1)), 3),
         round(50.0 + 20.0 * math.sin(2 * math.pi * i / (n_vertices - 1)), 3))
        for i in range(n_vertices - 1)
    ]
    pts.append(pts[0])
    assert len(pts) == n_vertices
    return CartoFeature(
        feature_type=feature_type,
        geometry=pts,
        is_polygon=True,
        tags={"building": "yes"},
        osm_id="999000111",
        osm_type="way",
        z_order=40,
        width_cells=0.0,
    )


@pytest.mark.skipif(not RUN_DIR.is_dir(), reason="a run dir is needed for load_run_params")
@pytest.mark.parametrize("n", [3, 40, 41, 73, 200])
def test_world_vertices_are_never_truncated(n):
    from collections import Counter  # noqa: PLC0415

    records = _stamper()._build_feature_jsonl([_feature(n)], RUN_DIR, Counter())
    assert len(records) == 1
    rec = records[0]

    verts = rec["world_vertices"]
    assert len(verts) == n, f"{n} vertices in, {len(verts)} out"
    assert rec["world_vertex_count"] == n
    # completeness invariant: every element is a real 2-D coordinate
    for i, pt in enumerate(verts):
        assert isinstance(pt, list), f"vertex {i} is {type(pt).__name__}, not a coordinate"
        assert len(pt) == 2, f"vertex {i} has {len(pt)} components"
        assert all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pt)
    # and the ring still closes, which truncation always broke
    assert verts[0] == verts[-1]


@pytest.mark.skipif(not RUN_DIR.is_dir(), reason="a run dir is needed for load_run_params")
def test_no_string_sentinel_survives_anywhere_in_the_record():
    from collections import Counter  # noqa: PLC0415

    rec = _stamper()._build_feature_jsonl([_feature(73)], RUN_DIR, Counter())[0]

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                yield from strings(v)

    assert not [s for s in strings(rec) if "truncat" in s.lower()]


@pytest.mark.skipif(not RUN_DIR.is_dir(), reason="a run dir is needed for load_run_params")
def test_the_cap_is_gone_from_the_producer_source():
    """A source-level guard: the literal cap must not reappear."""
    src = (PIPELINE_DIR / "osm_carto_stamper.py").read_text(encoding="utf-8")
    emitting = [
        line for line in src.splitlines()
        if "world_pts[:40]" in line and not line.lstrip().startswith("#")
    ]
    assert emitting == [], emitting
