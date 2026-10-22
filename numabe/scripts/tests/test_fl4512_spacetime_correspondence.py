"""FL-4512 ordered, injective spacetime correspondence contract."""

from pathlib import Path
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "apps/fl4512-runtime/engine/fl4512_spacetime_track_owner.h"


def test_lineage_prior_reparameterizes_without_changing_previous_store(tmp_path: Path):
    source = tmp_path / "lineage.cpp"
    binary = tmp_path / "lineage"
    source.write_text(textwrap.dedent(r'''
        #include <vector>
        #include "apps/fl4512-runtime/engine/fl4512_spacetime_track_owner.h"
        int main() {
            Fl4512FeatureRefV1 first = {}, second = {};
            first.semantic_chain_id = 5; second.semantic_chain_id = 6;
            Fl4512SequenceSegmentContributorV1 segments[2] = {};
            segments[0].semantic_segment_id = 101;
            segments[1].semantic_segment_id = 102;
            Fl4512SpacetimeTrackStoreV1 store = {};
            for (int i = 0; i < 2; ++i) {
                Fl4512SpacetimeTrackRecordV1 record = {};
                record.feature_ref = i ? second : first;
                record.last_frame = 4;
                Fl4512SpacetimeTrackNodeV1 node = {};
                node.u_q16 = i ? 1000 : 60000;
                node.glyph_id = 40 + i;
                node.segment_contributors.push_back(segments[i]);
                record.previous_nodes.push_back(node);
                store.records.push_back(record);
            }
            Fl4512SequenceNodeInputV2 inputs[2] = {};
            for (int i = 0; i < 2; ++i) {
                inputs[i].claim.chain_phase0_q16 = i ? 50000 : 10000;
                inputs[i].claim.chain_phase1_q16 = inputs[i].claim.chain_phase0_q16;
                inputs[i].segment_contributors = &segments[i];
                inputs[i].segment_contributor_count = 1;
            }
            std::vector<const Fl4512SequenceNodeInputV2*> chain = {&inputs[0], &inputs[1]};
            Fl4512TopologyObservationV1 event = {};
            event.participant_count = 2;
            event.participants[0] = first; event.participants[1] = second;
            Fl4512SpacetimeTrackRecordV1 assembled = {};
            const auto* prior = Fl4512SpacetimeLineagePriorV4(chain, first, store, &event, &assembled);
            if (prior != &assembled || prior->previous_nodes.size() != 2) return 1;
            const auto correspondence = Fl4512SpacetimeCorrespondenceV3(
                {10000, 50000}, chain, prior->previous_nodes);
            if (correspondence != std::vector<int16_t>({0, 1})) return 2;
            for (int i = 0; i < 2; ++i) {
                const auto& n = prior->previous_nodes[i];
                if (n.u_q16 != (i ? 50000 : 10000) || n.glyph_id != 40u + i ||
                    n.segment_contributors[0].semantic_segment_id != 101u + i) return 3;
                if (store.records[i].previous_nodes[0].u_q16 != (i ? 1000 : 60000)) return 4;
            }
            // An unchanged exact track must retain its prior parameterization.
            if (Fl4512SpacetimeLineagePriorV4(chain, first, store, nullptr, &assembled)
                    != &store.records[0]) return 5;
            return 0;
        }
    '''))
    compiled = subprocess.run(["c++", "-std=c++20", "-fsanitize=address,undefined",
                               "-I", str(ROOT), "-I", str(ROOT / "engine"),
                               "-I", str(ROOT / "engine/render"),
                               str(source), "-o", str(binary)], capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, (result.returncode, result.stderr)


def test_spacetime_correspondence_is_ordered_and_injective(tmp_path: Path):
    source = tmp_path / "correspondence.cpp"
    binary = tmp_path / "correspondence"
    source.write_text(
        textwrap.dedent(
            r"""
            #include <cstdio>
            #include <vector>
            #include "apps/fl4512-runtime/engine/fl4512_spacetime_track_owner.h"

            static Fl4512SpacetimeTrackNodeV1 prior(unsigned u)
            {
                Fl4512SpacetimeTrackNodeV1 value = {};
                value.u_q16 = (uint16_t)u;
                return value;
            }

            static void emit(const std::vector<uint16_t>& current,
                const std::vector<Fl4512SpacetimeTrackNodeV1>& previous)
            {
                const std::vector<int16_t> result =
                    Fl4512SpacetimeCorrespondenceV2(current, previous);
                for (size_t i = 0; i < result.size(); ++i)
                    std::printf("%s%d", i ? "," : "", (int)result[i]);
                std::printf("\n");
            }

            int main()
            {
                emit({0, 100, 200}, {prior(0), prior(200)});
                emit({0, 200}, {prior(0), prior(100), prior(200)});
                emit({100, 100, 100}, {prior(100), prior(100)});
                emit({200, 100}, {prior(0), prior(200)});
                return 0;
            }
            """
        )
    )
    compile_result = subprocess.run(
        [
            "g++",
            "-std=c++20",
            "-I",
            str(ROOT),
            "-I",
            str(ROOT / "engine"),
            "-I", str(ROOT / "engine/render"),
            str(source),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [str(binary)], cwd=ROOT, capture_output=True, text=True
    )
    assert run_result.returncode == 0, run_result.stderr
    assert run_result.stdout.splitlines() == [
        "0,-1,1", "0,2", "0,1,-1", ""
    ]


def test_independent_nearest_prior_owner_is_deleted():
    text = HEADER.read_text()
    assert "Fl4512SpacetimeCorrespondV1" not in text
    assert "Fl4512SpacetimeCorrespondenceV2" in text
    assert "return the previous node whose arc is closest" not in text
    assert "FL4512_SPACETIME_FAILURE_CORRESPONDENCE" in text
    assert "uint16_t prior_node_index" in text
    assert "tr.prior_node_index = (uint16_t)prior_index" in text
    commit = text.split("Fl4512SpacetimeStoreCommitV2", 1)[1].split(
        "struct Fl4512SpacetimeBuildResultV4", 1
    )[0]
    assert "std::stable_sort(rec->pending_nodes.begin()" in commit
    assert "return a.u_q16 < b.u_q16" in commit


def test_observation_rejection_overflow_is_sticky_and_resets_at_frame_boundary(
    tmp_path: Path,
):
    source = tmp_path / "rejection_overflow.cpp"
    binary = tmp_path / "rejection_overflow"
    source.write_text(
        textwrap.dedent(
            r"""
            #include <cstring>
            #include "apps/fl4512-runtime/engine/fl4512_spacetime_track_owner.h"

            int main()
            {
                Fl4512SpacetimeTrackStoreV1 store = {};
                store.observation_rejections.resize(
                    kFl4512SpacetimeTrackCapacityV1);
                Fl4512FeatureRefV1 ref = {};
                Fl4512RecordObservationRejectionV1(
                    &store, ref,
                    FL4512_OBSERVATION_FAILURE_CAPACITY_EXHAUSTED,
                    FL4512_OBSERVATION_SOURCE_ACTIVE_STROKE);
                if (!store.observation_rejection_overflow ||
                    store.observation_rejections.size() !=
                        kFl4512SpacetimeTrackCapacityV1)
                    return 1;

                Fl4693RuntimeEpochV1 epoch = {};
                epoch.world_generation = 7;
                epoch.profile_revision = 3;
                epoch.frame_id.value = 11;
                std::strcpy(epoch.active_registry_sha256, "registry");
                if (!Fl4512SpacetimeStoreBeginFrameV3(&store, &epoch))
                    return 2;
                return store.observation_rejection_overflow == 0 &&
                    store.observation_rejections.empty() ? 0 : 3;
            }
            """
        )
    )
    compile_result = subprocess.run(
        [
            "g++",
            "-std=c++20",
            "-I",
            str(ROOT),
            "-I",
            str(ROOT / "engine"),
            "-I", str(ROOT / "engine/render"),
            str(source),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [str(binary)], cwd=ROOT, capture_output=True, text=True
    )
    assert run_result.returncode == 0, run_result.stderr


def test_raster_gaps_are_solved_once_per_complete_feature_ref():
    text = HEADER.read_text()
    track_pass = text.split(
        "--- Track pass: validate and solve one whole FeatureRef chain.", 1
    )[1].split("return out;", 1)[0]
    assert "Fl4512SequenceCellsEightNeighborV1" not in track_pass
    assert "Fl4512SolveSpacetimeChainV4(" in track_pass
    assert "singleton_nodes.begin() + begin" in track_pass
    assert "singleton_nodes.begin() + end" in track_pass
    assert "a->claim.cell_index < b->claim.cell_index" in track_pass
