"""FL-4693 corrected P2 contracts for declared-lifetime temporal ownership.

P2 consolidates retained glyph state without inventing structural identity for
tonal surfaces. Active Stroke remains the upstream geometry-correspondence
producer; it does not retain presented GlyphIds.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_p2_deletes_presenter_local_temporal_stores() -> None:
    presenter = _read("platform/terminal_gl_present.cpp")
    owner = _read("engine/render/fl4512_spacetime_track_owner.h")
    internal = _read("engine/render/render_internal.h")
    bridge = _read("engine/render/render_frame_input.h")

    assert "static Fl4512SpacetimeTrackStoreV1 s_track_store" not in presenter
    assert "static std::unordered_map<Fl4208SurfaceId" not in presenter
    assert "Fl4208TemporalMeasureHistoryV1" not in presenter
    assert "auto& temporal_history = track_owner->tonal_history;" in presenter
    assert "Fl4512SpacetimeTrackStoreV1 fl4512_spacetime_track_store = {};" in internal
    assert "Fl4512SpacetimeTrackStoreV1* spacetime_track_store;" in bridge
    assert "std::unordered_map<Fl4208SurfaceId, Fl4512TonalTrackRecordV1" in owner
    assert "std::vector<Fl4512SpacetimeTrackRecordV1> records" in owner


def test_p2_preserves_distinct_upstream_geometry_correspondence() -> None:
    active = _read("apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp")
    resolve = _read("engine/render/render_resolve.cpp")

    assert "for (size_t prior_i = 0; prior_i < group.prior_rows.size(); ++prior_i)" in active
    assert "snake.semantic_chain_id = row.semantic_chain_id;" in active
    assert "const Fl4512FeatureRefV1 feature_ref = Fl4512MakeFeatureRefV1(f);" in resolve
    process = active.split("static bool ProcessOwnerGroup", 1)[1].split(
        "bool Fl4512AssignActiveStrokeSnakeCurrentFrameV2", 1
    )[0]
    assert process.index("group.prior_rows") < process.index(
        "snake.semantic_chain_id = row.semantic_chain_id;"
    )


def test_p2_epoch_transition_invalidates_both_identity_domains(tmp_path: Path) -> None:
    source = tmp_path / "track_owner_epoch.cpp"
    executable = tmp_path / "track_owner_epoch"
    source.write_text(
        r'''
#include <cstring>
#include "engine/render/fl4512_spacetime_track_owner.h"

int main()
{
    Fl4512SpacetimeTrackStoreV1 store = {};
    Fl4512SpacetimeTrackRecordV1 structural = {};
    structural.feature_ref.surface.world_generation = 1;
    structural.feature_ref.semantic_chain_id = 7;
    store.records.push_back(structural);

    Fl4208SurfaceId tonal_id = {};
    tonal_id.world_generation = 1;
    tonal_id.owner_kind = FL4208_OWNER_TERRAIN;
    Fl4512TonalTrackRecordV1 tonal = {};
    tonal.gid = 9;
    store.tonal_history[tonal_id] = tonal;

    Fl4693RuntimeEpochV1 first = {};
    first.world_generation = 1;
    first.profile_revision = 2;
    first.frame_id.value = 1;
    std::memset(first.active_registry_sha256, 'a', 64);
    first.active_registry_sha256[64] = '\0';
    if (!Fl4512SpacetimeStoreBeginFrameV3(&store, &first)) return 1;
    if (store.records.size() != 1 || store.tonal_history.size() != 1) return 2;

    Fl4693RuntimeEpochV1 changed = first;
    changed.profile_revision = 3;
    changed.frame_id.value = 2;
    if (Fl4512SpacetimeStoreBeginFrameV3(&store, &changed)) return 3;
    if (!store.records.empty() || !store.tonal_history.empty()) return 4;
    return 0;
}
''',
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-O2",
            "-I",
            str(ROOT),
            "-I",
            str(ROOT / "engine"),
            str(source),
            "-o",
            str(executable),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    executed = subprocess.run(
        [str(executable)], cwd=ROOT, capture_output=True, text=True
    )
    assert executed.returncode == 0, executed.stderr
