#!/usr/bin/env python3
"""FL-4693 P3 typed observation-channel ownership contracts."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / "engine/render/fl4512_spacetime_track_owner.h"
INPUT = ROOT / "engine/render/fl4512_structural_sequence_input.h"
ACTIVE = ROOT / "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp"
WORLD = ROOT / "engine/render/render_world_pass.cpp"
RESOLVE = ROOT / "engine/render/render_resolve.cpp"
PRESENTER = ROOT / "platform/terminal_gl_present.cpp"
CORE = ROOT / "engine/render/render_core.cpp"
INTERNAL = ROOT / "engine/render/render_internal.h"


def test_p3_deletes_presenter_and_claim_side_derivations() -> None:
    owner = OWNER.read_text(encoding="utf-8")
    input_header = INPUT.read_text(encoding="utf-8")
    active = ACTIVE.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    resolve = RESOLVE.read_text(encoding="utf-8")
    presenter = PRESENTER.read_text(encoding="utf-8")

    assert "disocclusion_reset" not in presenter
    assert "g_fl4512_temporal_evictions" not in presenter
    assert "Fl4512TopologyEventFromActiveStrokeLifecycleV1" not in input_header
    claim = input_header.split("struct Fl4512SequenceCellClaimV2", 1)[1]
    claim = claim.split("};", 1)[0]
    assert "topology_event_kind" not in claim
    assert "topology_event_magnitude" not in claim
    assert "candidate.topology_event_kind" not in resolve
    assert "candidate.topology_event_magnitude" not in resolve
    assert "event.topology_event_kind" in active
    assert "left.related_semantic_chain_id = left_semantic_chain_id;" in active
    assert "Fl4512SpacetimeObserveVisibilityV1" in world
    assert "Fl4512SpacetimeObserveTopologyV1" in world
    assert "Fl4512SpacetimeTopologyObservationFindV1" in owner


def test_renderer_runs_track_store_constructors_and_destructors() -> None:
    core = CORE.read_text(encoding="utf-8")
    internal = INTERNAL.read_text(encoding="utf-8")
    assert "new (std::nothrow) Renderer()" in core
    assert "delete r;" in core
    assert "malloc(sizeof(Renderer))" not in core
    assert "memset(this, 0, sizeof(Renderer))" not in internal


def test_p3_owner_accepts_complete_epoch_bound_observations(tmp_path: Path) -> None:
    source = tmp_path / "fl4693_p3_observation_probe.cpp"
    binary = tmp_path / "fl4693_p3_observation_probe"
    source.write_text(
        r'''
#include "engine/render/fl4512_spacetime_track_owner.h"
#include <cstring>

static Fl4693RuntimeEpochV1 Epoch(unsigned long long frame) {
    Fl4693RuntimeEpochV1 epoch = {};
    epoch.world_generation = 9;
    epoch.profile_revision = 12;
    epoch.frame_id.value = frame;
    std::memset(epoch.active_registry_sha256, 'a', 64);
    epoch.active_registry_sha256[64] = 0;
    return epoch;
}

static Fl4512FeatureRefV1 Ref(unsigned long long chain) {
    Fl4512FeatureRefV1 ref = {};
    ref.surface.world_generation = 9;
    ref.surface.owner_kind = FL4208_OWNER_MESH;
    ref.surface.owner_x = 77;
    ref.provenance.producer_kind = FL4512_FEATURE_PRODUCER_MESH;
    ref.provenance.mesh.mesh_instance_uid = 77;
    ref.material_key = 2;
    ref.provenance.mesh.depth_owner = 4;
    ref.dispatch_surface = 1;
    ref.role = 1;
    ref.feature_kind = 2;
    ref.aspect_region_id = 3;
    ref.semantic_chain_id = chain;
    return ref;
}

int main() {
    Fl4512SpacetimeTrackStoreV1 store = {};
    Fl4693RuntimeEpochV1 epoch = Epoch(1);
    if (!Fl4512SpacetimeStoreBeginFrameV3(&store, &epoch)) return 1;

    Fl4512VisibilityObservationV1 visible = {};
    visible.feature_ref = Ref(10);
    visible.epoch = epoch;
    visible.feature_ref_complete = 1;
    visible.observed_visibility = FL4512_OBSERVED_VISIBILITY_VISIBLE;
    visible.source_kind = FL4512_OBSERVATION_SOURCE_ACTIVE_STROKE;
    if (!Fl4512SpacetimeObserveVisibilityV1(&store, visible)) return 2;

    Fl4512TopologyObservationV1 topology = {};
    topology.feature_ref = Ref(10);
    topology.epoch = epoch;
    topology.participants[0] = Ref(10);
    topology.participants[1] = Ref(11);
    topology.magnitude = kFl4512TopologyMagnitudeUnmeasuredV1;
    topology.feature_ref_complete = 1;
    topology.event_kind = FL4512_TOPOLOGY_EVENT_COMPONENT_SPLIT;
    topology.magnitude_measured = 0;
    topology.participant_count = 2;
    topology.source_kind = FL4512_OBSERVATION_SOURCE_ACTIVE_STROKE;
    if (!Fl4512SpacetimeObserveTopologyV1(&store, topology)) return 3;
    if (store.visibility_observations.size() != 1 ||
        store.topology_observations.size() != 1 ||
        !store.observation_rejections.empty()) return 4;

    Fl4512TopologySignatureV1 a = {}, b = {};
    b.endpoints = 1;
    if (Fl4512SpacetimeTopologyCostV1(a, b, &topology) != 1) return 5;
    topology.magnitude_measured = 1;
    topology.magnitude = 1;
    if (Fl4512SpacetimeTopologyCostV1(a, b, &topology) != 1) return 6;
    b = {};
    b.components = 1;
    if (Fl4512SpacetimeTopologyCostV1(a, b, &topology) != 0) return 13;
    b.holes = 1;
    if (Fl4512SpacetimeTopologyCostV1(a, b, &topology) != 1) return 14;
    b.holes = 0;
    b.endpoints = 1;
    if (Fl4512SpacetimeTopologyCostV1(a, b, &topology) != 1) return 44;
    b.endpoints = 0;
    b.junctions = 1;
    if (Fl4512SpacetimeTopologyCostV1(a, b, &topology) != 1) return 45;
    a.components = 1;
    b.components = 0;
    b.junctions = 0;
    b.holes = 0;
    if (Fl4512SpacetimeTopologyCostV1(a, b, &topology) != 1) return 15;
    topology.event_kind = FL4512_TOPOLOGY_EVENT_COMPONENT_MERGE;
    if (Fl4512SpacetimeTopologyCostV1(a, b, &topology) != 0) return 16;

    struct TransitionCase {
        uint8_t event_kind;
        Fl4512TopologySignatureV1 before;
        Fl4512TopologySignatureV1 after;
    };
    TransitionCase transitions[8] = {};
    transitions[0].event_kind = FL4512_TOPOLOGY_EVENT_ENDPOINT_APPEAR;
    transitions[0].after.endpoints = 1;
    transitions[1].event_kind = FL4512_TOPOLOGY_EVENT_ENDPOINT_VANISH;
    transitions[1].before.endpoints = 1;
    transitions[2].event_kind = FL4512_TOPOLOGY_EVENT_JUNCTION_SPLIT;
    transitions[2].after.junctions = 1;
    transitions[3].event_kind = FL4512_TOPOLOGY_EVENT_JUNCTION_MERGE;
    transitions[3].before.junctions = 1;
    transitions[4].event_kind = FL4512_TOPOLOGY_EVENT_COMPONENT_SPLIT;
    transitions[4].after.components = 1;
    transitions[5].event_kind = FL4512_TOPOLOGY_EVENT_COMPONENT_MERGE;
    transitions[5].before.components = 1;
    transitions[6].event_kind = FL4512_TOPOLOGY_EVENT_HOLE_OPEN;
    transitions[6].after.holes = 1;
    transitions[7].event_kind = FL4512_TOPOLOGY_EVENT_HOLE_CLOSE;
    transitions[7].before.holes = 1;
    for (int i = 0; i < 8; ++i) {
        topology.event_kind = transitions[i].event_kind;
        if (Fl4512SpacetimeTopologyCostV1(
                transitions[i].before, transitions[i].after, &topology) != 0)
            return 20 + i;
        if (Fl4512SpacetimeTopologyCostV1(
                transitions[i].after, transitions[i].before, &topology) != 1)
            return 30 + i;
    }
    topology.event_kind = FL4512_TOPOLOGY_EVENT_COMPONENT_SPLIT;
    topology.magnitude = 2;
    a = {};
    b = {};
    b.components = 1;
    if (Fl4512SpacetimeTopologyCostV1(a, b, &topology) != 1) return 40;
    if (Fl4512SpacetimeTopologyCostV1(a, b, nullptr) != 1) return 41;
    topology.magnitude = kFl4512TopologyMagnitudeUnmeasuredV1;
    if (Fl4512SpacetimeObserveTopologyV1(&store, topology)) return 46;
    if (store.observation_rejections.back().failure !=
        FL4512_OBSERVATION_FAILURE_MAGNITUDE_INVALID) return 47;
    topology.magnitude = 0;
    if (Fl4512SpacetimeObserveTopologyV1(&store, topology)) return 42;
    if (store.observation_rejections.back().failure !=
        FL4512_OBSERVATION_FAILURE_MAGNITUDE_INVALID) return 43;

    Fl4512VisibilityObservationV1 stale = visible;
    stale.epoch.frame_id.value = 2;
    if (Fl4512SpacetimeObserveVisibilityV1(&store, stale)) return 7;
    if (store.observation_rejections.back().failure !=
        FL4512_OBSERVATION_FAILURE_EPOCH_CHANGED) return 8;

    Fl4512TopologyObservationV1 incomplete = topology;
    incomplete.magnitude_measured = 0;
    incomplete.magnitude = kFl4512TopologyMagnitudeUnmeasuredV1;
    incomplete.participant_count = 2;
    incomplete.participants[1] = {};
    if (Fl4512SpacetimeObserveTopologyV1(&store, incomplete)) return 9;
    if (store.observation_rejections.back().failure !=
        FL4512_OBSERVATION_FAILURE_PARTICIPANTS_INCOMPLETE) return 10;

    epoch = Epoch(2);
    if (!Fl4512SpacetimeStoreBeginFrameV3(&store, &epoch)) return 11;
    if (!store.visibility_observations.empty() ||
        !store.topology_observations.empty() ||
        !store.observation_rejections.empty()) return 12;
    return 0;
}
''',
        encoding="utf-8",
    )
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-I",
            str(ROOT),
            "-I",
            str(ROOT / "engine"),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)
