#!/usr/bin/env python3
# =============================================================================
# scripts/tests/test_fl4684_live_producer_evidence.py
# =============================================================================
# CONTRACT TESTS FOR THE FL-4684 / FL-4685 LIVE PRODUCER EVIDENCE ARTIFACT
#
# WHAT THESE TESTS ARE FOR, IN PLAIN LANGUAGE
# -------------------------------------------
# The artifact under test,
# assets/glyphs/generated/fl4684.live_producer_evidence.v1.json, records what
# the renderer's scene-fact producer actually emitted while a person (or a
# driver script standing in for one) played the real product. Every number in it
# came from a launched game.
#
# Because that artifact is the evidence a later decision will lean on, these
# tests do not check that the numbers are GOOD. They check that the artifact
# cannot quietly lie about how the numbers were obtained:
#
#   - it must say the game was launched, and pin the exact revision;
#   - every run it cites must name a real receipt and real census;
#   - the arithmetic inside it must be self-consistent;
#   - a run whose camera did not actually move must NOT be counted as motion
#     evidence, no matter what mode it says it was driven in;
#   - a topology claim must be traceable to counters, not to prose;
#   - the FL-4683 glyph-program loader must still be switched off, because this
#     lane is only allowed to OBSERVE the existing producer.
#
# A test that asserted "coverage is high" would break the moment the product
# legitimately changed. A test that asserts "the evidence describes its own
# provenance honestly" stays meaningful forever. These are the second kind.
#
# Run with:  python3 -m pytest scripts/tests/test_fl4684_live_producer_evidence.py -q
# =============================================================================

from __future__ import annotations

import json
import importlib.util
import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = (
    ROOT / "assets" / "glyphs" / "generated" / "fl4684.live_producer_evidence.v1.json"
)
REACHABILITY = (
    ROOT / "assets" / "glyphs" / "generated"
    / "fl4684.compiled_address_reachability.v1.json"
)
CENSUS_HEADER = ROOT / "apps" / "fl4512-runtime" / "platform" / "fl4684_scene_fact_emission_census.h"
# FL-4512 seam: the scene-fact producer (writer + hash + census tap) lives in
# the Current scene-fact builder; the shared resolver routes through hooks.
PRODUCER_SOURCE = (
    ROOT / "apps" / "fl4512-runtime" / "engine" / "fl4512_scene_fact_builder.cpp"
)


def test_native_app_envelope_keeps_binary_bytes_and_refuses_reuse(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "native_recorder_envelope", ROOT / "scripts/proofs/proof_fl4684_native_live_producer_motion.py")
    recorder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recorder)
    binary = tmp_path / "game"
    binary.write_bytes(b"immutable native binary test bytes")
    binary.chmod(0o755)
    output = tmp_path / "capture"
    packaged, receipt = recorder.prepare_native_app_envelope(binary, output)
    assert packaged.read_bytes() == binary.read_bytes()
    assert packaged.stat().st_mode & 0o111 == 0o111
    assert receipt["source_sha256"] == receipt["executable_sha256"]
    with (Path(receipt["bundle"]) / "Contents/Info.plist").open("rb") as stream:
        plist = plistlib.load(stream)
    launcher = packaged.parent / plist["CFBundleExecutable"]
    assert launcher.is_file()
    assert 'exec "$(dirname "$0")/game" "$@"' in launcher.read_text()
    assert plist["CFBundleIdentifier"] == receipt["bundle_id"]
    with pytest.raises(FileExistsError):
        recorder.prepare_native_app_envelope(binary, output)
    command = recorder.native_app_launch_command(receipt, ["--auto-start-map", "a map.a3d"],
        {"FL_TEST": "one", "PATH": "/usr/bin", "UNRELATED_SECRET": "not-forwarded"},
        output / "stdout.log", output / "stderr.log")
    assert command[:3] == ["open", "-n", "-W"]
    assert command[-3:] == ["--args", "--auto-start-map", "a map.a3d"]
    assert "FL_TEST=one" in command and "UNRELATED_SECRET=not-forwarded" not in command
    source = tmp_path / "assets"
    for relative in ("profiles/material_rendering_profiles.v1.json",
                     "generated/fl4512.compiled_pool.v1.json"):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relative.encode())
    workspace, snapshot = recorder.prepare_isolated_profile_workspace(output, source)
    for relative, digest in snapshot["initial_sha256"].items():
        assert (workspace / relative).read_bytes() == (source / relative).read_bytes()
        assert digest == recorder.sha256_file(source / relative)
    with pytest.raises(FileExistsError):
        recorder.prepare_isolated_profile_workspace(output, source)


@pytest.fixture(scope="module")
def evidence() -> dict:
    """Load the evidence artifact once and share it across every test."""
    assert ARTIFACT.exists(), f"missing evidence artifact: {ARTIFACT}"
    return json.loads(ARTIFACT.read_text())


def test_launch_identity_survives_later_rebuild_and_source_change(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "native_recorder_identity", ROOT / "scripts/proofs/proof_fl4684_native_live_producer_motion.py")
    recorder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recorder)
    binary = tmp_path / "game"
    binary.write_bytes(b"launched executable")
    monkeypatch.setattr(recorder, "git_output", lambda *args:
                        " M engine/first.cpp" if args[0] == "status" else "launch-source")
    launched = recorder.capture_source_identity(binary)
    binary.write_bytes(b"a later rebuild")
    monkeypatch.setattr(recorder, "git_output", lambda *args:
                        " M engine/second.cpp" if args[0] == "status" else "later-source")
    ended = recorder.capture_source_identity(binary)
    assert launched["binary_sha256"] != ended["binary_sha256"]
    assert launched["revision"] == {
        "head": "launch-source", "branch": "launch-source", "dirty_paths": ["engine/first.cpp"]}
    assert ended["revision"]["head"] == "later-source"
    assert ended["revision"]["dirty_paths"] == ["engine/second.cpp"]


@pytest.fixture(scope="module")
def reachability() -> dict:
    """Load the earlier source-reading measurement this evidence is compared to."""
    assert REACHABILITY.exists(), f"missing reachability artifact: {REACHABILITY}"
    return json.loads(REACHABILITY.read_text())


# -----------------------------------------------------------------------------
# Shape and provenance
# -----------------------------------------------------------------------------

def test_schema_and_version_are_pinned(evidence: dict) -> None:
    """The artifact names its own schema, so a reader can never guess wrong."""
    assert evidence["schema"] == "fl4684.live_producer_evidence.v1"
    assert evidence["version"] == 1
    assert evidence["generated_by"] == (
        "scripts/measure_fl4684_live_producer_evidence.py"
    )


def test_evidence_kind_is_runtime_not_static(evidence: dict) -> None:
    """This artifact must never be confused with the source-reading measurement.

    The earlier FL-4684 artifact says its evidence kind is
    "static_source_and_artifact_measurement". This one is the opposite: it only
    exists because a product was launched. If that label ever flipped, a reader
    could mistake reading for running.
    """
    assert evidence["evidence_kind"] == "live_runtime_measurement"
    assert evidence["the_game_was_launched"] is True


def test_revision_is_pinned(evidence: dict) -> None:
    """A commit hash and branch must be recorded, or the run cannot be located."""
    revision = evidence["revision"]
    assert len(revision["head"]) == 40, "head must be a full 40-character commit id"
    assert revision["branch"], "branch must be recorded"
    # The dirty-path list may be empty, but the KEY must exist: a reader has to
    # be able to tell "clean tree" from "nobody wrote it down".
    assert isinstance(revision["dirty_paths"], list)


def test_every_run_pins_its_own_receipt_and_revision(evidence: dict) -> None:
    """Each run must carry its own receipt path, checksum, and revision block."""
    assert evidence["runs"], "at least one run must be recorded"
    for name, run in evidence["runs"].items():
        assert run["receipt"], f"{name}: receipt path missing"
        assert len(run["receipt_sha256"]) == 64, f"{name}: receipt checksum missing"
        assert run["target"], f"{name}: target build not named"
        assert run["revision"].get("head"), f"{name}: run revision not pinned"


# -----------------------------------------------------------------------------
# Internal arithmetic
# -----------------------------------------------------------------------------

def test_compiled_address_total_matches_the_reachability_artifact(
    evidence: dict, reachability: dict
) -> None:
    """The denominator must be the real compiled address count, not a guess."""
    assert (
        evidence["compared_against"]["compiled_address_total"]
        == reachability["summary"]["compiled_address_total"]
        == len(reachability["addresses"])
    )


def test_seen_and_never_seen_add_up_per_run(evidence: dict, reachability: dict) -> None:
    """Addresses seen live plus addresses never seen must equal the total."""
    total = len(reachability["addresses"])
    for name, run in evidence["runs"].items():
        coverage = run["compiled_address_coverage"]
        assert (
            coverage["compiled_addresses_seen_live"]
            + coverage["compiled_addresses_never_seen_live"]
            == total
        ), f"{name}: per-run coverage does not add up to {total}"


def test_union_across_runs_adds_up(evidence: dict, reachability: dict) -> None:
    """The same arithmetic must hold for the union across every run."""
    total = len(reachability["addresses"])
    union = evidence["union_across_runs"]
    assert (
        union["compiled_addresses_seen_live_in_any_run"]
        + union["compiled_addresses_never_seen_live"]
        == total
    )
    # The explicit never-seen list must have exactly as many entries as the count
    # claims, so a shortened list cannot understate a gap.
    assert len(union["addresses_never_seen_live"]) == (
        union["compiled_addresses_never_seen_live"]
    )


def test_per_domain_compiled_counts_match_the_reachability_artifact(
    evidence: dict, reachability: dict
) -> None:
    """Each domain's denominator must match the compiled artifact's own count."""
    expected: dict[str, int] = {}
    for row in reachability["addresses"]:
        key = f"{row['profile_namespace']}/{row['compiler_role']}"
        expected[key] = expected.get(key, 0) + 1
    actual = {
        k: v["compiled"] for k, v in evidence["union_across_runs"]["per_domain"].items()
    }
    assert actual == expected


def test_no_run_silently_dropped_observations(evidence: dict) -> None:
    """The census table must not have overflowed.

    The tap remembers a bounded number of distinct address tuples. If it filled
    up, some sightings were lost and every count below it would understate
    reality. A nonzero overflow means the evidence is incomplete and must not be
    read as a measurement.
    """
    for name, run in evidence["runs"].items():
        assert run["producer_emission"]["address_table_overflow"] == 0, (
            f"{name}: the emission table overflowed, so its counts are incomplete"
        )


def test_emitted_facts_are_consistent_with_route_family_expansion(
    evidence: dict,
) -> None:
    """Every emitted fact must have named at least one route family.

    The FL-4683 contract says a valid scene-fact row always names at least one
    route family, and a tonal row names exactly two. So the number of
    (fact, family) pairs must be at least the number of facts, and never more
    than twice it.
    """
    for name, run in evidence["runs"].items():
        emission = run["producer_emission"]
        facts = emission["scene_facts_emitted"]
        pairs = emission["address_observations"]
        assert emission["facts_with_empty_route_family_mask"] == 0, (
            f"{name}: a scene fact was emitted naming no route family, which the "
            f"FL-4683 contract forbids"
        )
        assert emission["facts_with_role_out_of_range"] == 0, (
            f"{name}: a scene fact carried a role byte outside the defined range"
        )
        assert facts <= pairs <= 2 * facts, (
            f"{name}: {pairs} route-family pairs cannot come from {facts} facts"
        )


# -----------------------------------------------------------------------------
# Honesty about motion
# -----------------------------------------------------------------------------

def test_motion_is_decided_by_measurement_not_by_the_declared_mode(
    evidence: dict,
) -> None:
    """A run only counts as motion evidence when its own pictures say so.

    A driver can ATTEMPT to move the camera and fail -- for example when the
    operating system refuses to deliver keystrokes. The artifact must therefore
    never treat the declared mode as proof. Every run carries a measured
    frame-to-frame difference, and only that decides.
    """
    for name, run in evidence["runs"].items():
        motion = run["motion"]
        assert "camera_motion_evidenced" in motion, f"{name}: no motion verdict"
        assert isinstance(motion["camera_motion_evidenced"], bool)
        threshold = motion["camera_motion_threshold_pct"]
        diffs = motion["consecutive_frame_difference_pct"]
        expected = bool(diffs) and max(diffs) >= threshold
        assert motion["camera_motion_evidenced"] == expected, (
            f"{name}: the motion verdict disagrees with its own measurements"
        )


def test_motion_runs_listed_are_exactly_the_runs_that_evidenced_motion(
    evidence: dict,
) -> None:
    """The topology section may only cite runs whose camera provably moved."""
    section = evidence["fl4685_topology_during_motion"]
    expected = sorted(
        name
        for name, run in evidence["runs"].items()
        if run["motion"]["camera_motion_evidenced"]
    )
    assert sorted(section["runs_with_evidenced_camera_motion"]) == expected
    assert section["runs_with_evidenced_camera_motion_count"] == len(expected)


def test_topology_totals_are_sums_of_the_cited_runs(evidence: dict) -> None:
    """The split and merge totals must be the sum of the runs actually cited.

    This is what stops a summary number from drifting away from the per-run
    counters underneath it.
    """
    section = evidence["fl4685_topology_during_motion"]
    cited = section["runs_with_evidenced_camera_motion"]
    for field, key in (
        ("active_stroke_split_total_in_motion_runs", "active_stroke_split_total"),
        ("active_stroke_merge_total_in_motion_runs", "active_stroke_merge_total"),
        (
            "active_stroke_published_snake_total_in_motion_runs",
            "active_stroke_published_snake_total",
        ),
    ):
        expected = sum(
            evidence["runs"][name]["topology"][key] or 0 for name in cited
        )
        assert section[field] == expected, f"{field} does not sum its cited runs"


def test_scene_fact_topology_event_counts_have_one_bucket_per_declared_kind(
    evidence: dict,
) -> None:
    """The scene-fact topology histogram must cover every declared event kind.

    Fl4683TopologyEventV1 declares six values (none, birth, death, split, merge,
    loop closure). A histogram with fewer buckets could hide a kind entirely.
    """
    for name, run in evidence["runs"].items():
        counts = run["topology"]["scene_fact_topology_event_counts_by_code"]
        assert len(counts) == 6, f"{name}: expected 6 topology buckets, got {len(counts)}"
        assert run["topology"]["scene_fact_topology_events_non_none"] == sum(counts[1:])
        # Every emitted fact must land in exactly one bucket.
        assert sum(counts) == run["producer_emission"]["scene_facts_emitted"]


# -----------------------------------------------------------------------------
# The lane's standing constraint: observe, never consume
# -----------------------------------------------------------------------------

def test_artifact_records_that_runtime_consumption_stays_blocked(
    evidence: dict,
) -> None:
    """This lane measures the producer; it must not switch the selector on."""
    instrumentation = evidence["instrumentation"]
    assert instrumentation["read_only"] is True
    assert instrumentation["runtime_consumption_allowed"] is False


def test_the_tap_is_declared_read_only_in_its_own_source(evidence: dict) -> None:
    """The header the artifact names must exist and declare itself observation-only."""
    assert CENSUS_HEADER.exists(), f"missing census header: {CENSUS_HEADER}"
    text = CENSUS_HEADER.read_text()
    assert "OBSERVATION ONLY" in text.upper(), (
        "the census header must state in its own comments that it only observes"
    )
    # The bounded FL-4693 tuple trace adds one raw plane file plus one JSONL
    # manifest beside the original census file. All three opens remain
    # observation-only and are named by the proof-run output directory.
    assert text.count("fopen(") == 3, (
        "the census header may open only its census, bounded tuple plane, and "
        "tuple manifest outputs"
    )
    assert "final_tuple.frame-%06llu.scene-%06llu.bin" in text
    assert "final_tuple_trace.jsonl" in text


def test_the_tap_is_called_from_the_producer_and_only_after_the_row_is_final(
    evidence: dict,
) -> None:
    """The tap must be invoked where the artifact says it is.

    If the call site drifted, every number in the artifact would describe a
    different moment in the frame than the artifact claims.
    """
    assert PRODUCER_SOURCE.exists()
    text = PRODUCER_SOURCE.read_text()
    assert "Fl4684EmissionCensusObserveSceneFactV1" in text, (
        "the producer no longer calls the census tap the artifact names"
    )
    # It must come AFTER the row is marked valid and hashed, so the tap always
    # sees a complete row.
    hash_at = text.index("Fl4683HashSceneFactPayloadV1(&out, in);")
    tap_at = text.index("Fl4684EmissionCensusObserveSceneFactV1")
    assert tap_at > hash_at, (
        "the census tap must run after the scene-fact row is complete and hashed"
    )


# -----------------------------------------------------------------------------
# The main-menu trap
# -----------------------------------------------------------------------------
# These tests exist because of a real wrong answer. The desktop run's only large
# frame-to-frame difference was 82.8%, and that was first read as camera motion.
# It was not: it was the player leaving the MAIN MENU. The menu draws a full 3D
# scene behind it, so the producer emits and the pictures look like gameplay.
# A percentage cannot tell those apart, so somebody has to look, and the result
# of that looking has to be recorded and then actually honoured.

def test_every_capture_is_classified_as_menu_or_gameplay(evidence: dict) -> None:
    """No captured frame may sit in the evidence unclassified.

    An unclassified frame is dangerous precisely because it looks harmless: it
    would silently behave like gameplay in any comparison that forgets to check.
    """
    for name, run in evidence["runs"].items():
        phases = run["motion"]["capture_scene_phases"]
        assert phases, f"{name}: no captures were classified"
        unknown = [p for p in phases if p not in {"main_menu", "in_play"}]
        assert not unknown, (
            f"{name}: {len(unknown)} capture(s) are still unclassified; an "
            f"unclassified frame must never support a motion claim"
        )


def test_scene_phase_classification_records_who_looked_and_what_they_saw(
    evidence: dict,
) -> None:
    """The classification is a human judgement, so it must carry its provenance."""
    for name, run in evidence["runs"].items():
        classification = run["capture_scene_phase_classification"]
        assert classification, f"{name}: scene phases carry no provenance block"
        assert classification["classified_by"], f"{name}: nobody is named as classifier"
        assert len(classification["evidence"]) > 40, (
            f"{name}: the classification must say what was actually seen"
        )


def test_menu_frames_can_never_contribute_to_a_motion_verdict(evidence: dict) -> None:
    """Only gameplay-to-gameplay pairs may appear in the usable differences.

    Every pair involving a menu frame must be recorded in rejected_pairs rather
    than dropped silently, so a reader can see the 82.8% number AND see why it
    was refused.
    """
    for name, run in evidence["runs"].items():
        motion = run["motion"]
        phases = motion["capture_scene_phases"]
        # Count the gameplay-to-gameplay adjacencies the phases actually allow.
        allowed_pairs = sum(
            1 for a, b in zip(phases, phases[1:]) if a == "in_play" and b == "in_play"
        )
        assert len(motion["consecutive_frame_difference_pct"]) <= allowed_pairs, (
            f"{name}: more usable differences than there are gameplay pairs"
        )
        for rejected in motion["rejected_pairs"]:
            assert "in_play" not in rejected["scene_phases"] or (
                rejected["scene_phases"].count("in_play") < 2
            ), f"{name}: a gameplay-only pair was wrongly rejected"


def test_a_run_without_two_consecutive_gameplay_frames_claims_no_motion(
    evidence: dict,
) -> None:
    """Fewer than two adjacent gameplay frames means no motion evidence exists."""
    for name, run in evidence["runs"].items():
        phases = run["motion"]["capture_scene_phases"]
        allowed_pairs = sum(
            1 for a, b in zip(phases, phases[1:]) if a == "in_play" and b == "in_play"
        )
        if allowed_pairs == 0:
            assert run["motion"]["camera_motion_evidenced"] is False, (
                f"{name}: claims camera motion with no consecutive gameplay pair"
            )


def test_artifact_states_its_own_measurement_limitations(evidence: dict) -> None:
    """The two traps found while checking this run must travel with the artifact.

    Both can turn a correct number into a wrong conclusion, so neither may be
    left as folklore in a chat log.
    """
    limitations = evidence["measurement_limitations"]
    assert "frames_observed_counts_frame_id_transitions" in limitations
    assert "main_menu_renders_the_producer_too" in limitations
    assert "desktop_run_has_no_camera_motion_evidence" in limitations
    for key, text in limitations.items():
        assert len(text) > 80, f"{key}: limitation text is too thin to be useful"
