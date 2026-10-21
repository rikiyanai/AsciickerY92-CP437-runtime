#!/usr/bin/env python3
"""FL-4876 MASSCLIP-RECONF-01. Mass-clip pieces must re-conform.

A mass-clip kept piece can end at a mass-ring crossing that still sits
inside a surface-model forbidden kind (observed: a curb run whose south
end stood 0.09 m inside a building ring after the structural stage had
conformed it). The final gate refused the whole build for that one sliver.
These tests pin reconform_mass_clip_pieces: conformant pieces pass through
untouched, grazes are trimmed to their conformant remainder, fully
swallowed pieces are dropped, and face-coherent building contact is left
alone.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.compile_population_plan import (  # noqa: E402
    placement_conforms,
    reconform_mass_clip_pieces,
)

CATALOG = {"curb_edge_line": {"owning_domain": "road_cross_section"}}


class _StubSurf:
    """Ground truth: carriageway band |N| <= 2.25, building box E >= 10."""

    def point_surfaces(self, E, N):
        out = set()
        if -2.25 <= N <= 2.25:
            out.add("carriageway")
        if E >= 10.0 and -3.0 <= N <= 3.0:
            out.add("building")
        return out

    def clip_segment(self, a, b, kinds):
        kinds = set(kinds)
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(L / 0.5)))
        flags = []
        for i in range(n + 1):
            t = i / n
            E = a[0] + t * (b[0] - a[0])
            N = a[1] + t * (b[1] - a[1])
            flags.append(
                (t, (E, N), bool(set(self.point_surfaces(E, N)) & kinds)))
        out = []
        i = 0
        while i < len(flags):
            if flags[i][2]:
                i += 1
                continue
            j = i
            while j + 1 < len(flags) and not flags[j + 1][2]:
                j += 1
            out.append((flags[i][1], flags[j][1]))
            i = j + 1
        return out

    def _detail(self, E, N, expand=0.0):
        return {k: k for k in self.point_surfaces(E, N)}

    def segment_conflicts(self, a, b, half_width_m, kinds):
        kinds = set(kinds)
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(L / 0.5)))
        tagged = []
        for i in range(n + 1):
            t = i / n
            det = self._detail(a[0] + t * (b[0] - a[0]),
                               a[1] + t * (b[1] - a[1]), half_width_m)
            pick = None
            for k in sorted(det):
                if k in kinds:
                    pick = (k, det[k])
                    break
            tagged.append((t, pick))
        out = []
        i = 0
        while i < len(tagged):
            key = tagged[i][1]
            if key is None:
                i += 1
                continue
            j = i
            while j + 1 < len(tagged) and tagged[j + 1][1] == key:
                j += 1
            out.append({"kind": key[0], "carrier_id": key[1],
                        "t0": tagged[i][0], "t1": tagged[j][0]})
            i = j + 1
        return out


def _conforms(run):
    v = placement_conforms(
        _StubSurf(), module_id="curb_edge_line",
        carrier_class="road:residential", evidence_kind="rule",
        run=(run[0], run[1]), catalog_by_id=CATALOG)
    return v["ok"]


def test_graze_end_trimmed_to_conformant_remainder():
    surf = _StubSurf()
    run = ((0.0, 0.0), (10.09, 0.0))  # end 0.09 m inside the building box
    assert not _conforms(run)
    kept, clipped, dropped = reconform_mass_clip_pieces(
        [run], module_id="curb_edge_line", carrier_class="road:residential",
        catalog_by_id=CATALOG, surf=surf, tracks=[])
    assert dropped == 0
    assert clipped == 1
    assert len(kept) == 1
    assert kept[0][1][0] < 10.0  # building end pulled back out
    assert math.hypot(kept[0][1][0] - kept[0][0][0],
                      kept[0][1][1] - kept[0][0][1]) >= 1.0
    assert _conforms(kept[0])


def test_conformant_piece_untouched():
    surf = _StubSurf()
    run = ((0.0, 0.0), (8.0, 0.0))
    kept, clipped, dropped = reconform_mass_clip_pieces(
        [run], module_id="curb_edge_line", carrier_class="road:residential",
        catalog_by_id=CATALOG, surf=surf, tracks=[])
    assert (kept, clipped, dropped) == ([run], 0, 0)


def test_swallowed_piece_dropped():
    surf = _StubSurf()
    run = ((11.0, 0.0), (14.0, 0.0))  # fully inside the building box
    kept, clipped, dropped = reconform_mass_clip_pieces(
        [run], module_id="curb_edge_line", carrier_class="road:residential",
        catalog_by_id=CATALOG, surf=surf, tracks=[])
    assert kept == []
    assert clipped == 0
    assert dropped == 1


def test_face_coherent_contact_left_alone():
    surf = _StubSurf()
    run = ((11.0, 0.0), (14.0, 0.0))  # inside building, but coherent face
    kept, clipped, dropped = reconform_mass_clip_pieces(
        [run], module_id="curb_edge_line", carrier_class="road:residential",
        catalog_by_id=CATALOG, surf=surf, tracks=[],
        face_coherent=lambda _a, _b: True)
    assert (kept, clipped, dropped) == ([run], 0, 0)
