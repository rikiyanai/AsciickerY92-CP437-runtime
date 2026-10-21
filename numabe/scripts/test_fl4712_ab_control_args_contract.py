#!/usr/bin/env python3
"""Contract for the non-runtime FL-4712 paired-arm harness capability.

Two capabilities are pinned here, both living entirely outside the game:

1. The counterbalanced A/B bracket support (--ab-control-args,
   --ab-alternate-order) that lets a control arm express a switch through user
   arguments while launch order alternates.
2. The A=A noise control (--aa-control), where the SAME arm is launched on both
   sides of every bracket. Its defining property is that both arms receive
   IDENTICAL environment and IDENTICAL arguments, so the true difference is
   zero and any surviving paired delta is harness noise plus launch-order bias.

The A=A tests are written as four named FALSIFIERS. Each one describes a way
the A=A path could be wrong, and each has a test that would fail if it were:

  F1  the two arms get different environment or different arguments
  F2  an invalid (failed / zero-sample) arm is accepted into a delta
  F3  the A=A path bypasses the runtime-source fingerprint gate
  F4  a real one-directional A/B effect is reported as A=A noise

F1, F2 and F4 are checked by CALLING the harness helpers with constructed
inputs. F3 is structural and is checked against the source text, because the
gate it must not bypass is a sequence of statements, not a return value.

Nothing here launches Godot, and nothing here measures anything. This file
proves harness shape and harness arithmetic only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "run_godot_camera_turn_perf.py"


def _load_harness():
    """Import the harness module without executing its CLI.

    The harness imports `maintainer.lib.run_budget_guard`, which resolves only
    when scripts/ is on the path, so the path entry is added here rather than
    relying on how pytest happens to be invoked.
    """
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(
        "fl4712_harness_under_test", HARNESS
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_arm(avg_delta_ms: float) -> dict:
    """A fully valid single-mode arm result carrying one frame-time reading."""
    return {
        "ok": True,
        # The dimension contract is satisfied here so a wiring test is not
        # failed by geometry it is not exercising.
        "dimension_valid": True,
        "observed_dimensions": {},
        "mode_results": [
            {
                "invalid": False,
                "validity": {"ready": True},
                "avg_delta_ms": avg_delta_ms,
            }
        ],
    }


def test_control_user_args_and_counterbalanced_pairing_survive() -> None:
    source = HARNESS.read_text(encoding="utf-8")

    assert '"--ab-control-args"' in source
    assert '"--ab-alternate-order"' in source
    assert "control_extra_args: list[str] = shlex.split(" in source
    assert "if alternate_order and i % 2 == 1:" in source
    assert "bracket_slots.append((first_index + 1, first_index))" in source
    assert "run_variant(\n                label,\n                arm_extra_args," in source
    assert "_ab_paired_deltas(results, args.ab_brackets, bracket_slots)" in source
    assert 'summary["ab_arm_order"]' in source


def test_aa_control_flag_builds_both_arms_from_one_env_and_one_arg_list() -> None:
    """The A=A mode exists, and its arms are constructed from a single source."""
    source = HARNESS.read_text(encoding="utf-8")

    assert '"--aa-control"' in source
    assert 'aa_control = bool(getattr(args, "aa_control", False))' in source
    # Both arms are copies of the SAME per-bracket env dict and arg list. If a
    # future edit gave either arm its own inputs, these substrings would go.
    assert "aa_arm_env = dict(shared_env)" in source
    assert "aa_arm_args: list[str] = []" in source
    assert 'f"aa_arm_a_b{i + 1:02d}", dict(aa_arm_env), list(aa_arm_args)' in source
    assert 'f"aa_arm_b_b{i + 1:02d}", dict(aa_arm_env), list(aa_arm_args)' in source
    # An asymmetric request is refused, never silently ignored.
    assert "--aa-control refuses: --ab-control-env/--ab-control-args" in source
    # And the built arm list is re-checked before any launch.
    assert "if aa_control and not _aa_arms_identical(arms, bracket_slots):" in source


def test_f1_divergent_env_or_args_between_arms_is_rejected() -> None:
    """FALSIFIER 1: the A=A path applies different env or args to its two arms."""
    harness = _load_harness()
    slots = [(0, 1)]

    identical = [
        ("aa_arm_a_b01", {"SHARED": "1"}, ["--flag"]),
        ("aa_arm_b_b01", {"SHARED": "1"}, ["--flag"]),
    ]
    assert harness._aa_arms_identical(identical, slots) is True

    divergent_env = [
        ("aa_arm_a_b01", {"SHARED": "1"}, ["--flag"]),
        ("aa_arm_b_b01", {"SHARED": "1", "SWITCH": "1"}, ["--flag"]),
    ]
    assert harness._aa_arms_identical(divergent_env, slots) is False

    divergent_args = [
        ("aa_arm_a_b01", {"SHARED": "1"}, ["--flag"]),
        ("aa_arm_b_b01", {"SHARED": "1"}, ["--flag", "--extra"]),
    ]
    assert harness._aa_arms_identical(divergent_args, slots) is False

    # A truncated arm list cannot be read as "identical by default".
    assert harness._aa_arms_identical(identical[:1], slots) is False


def test_f1_counterbalanced_aa_arms_stay_identical_in_every_bracket() -> None:
    """Order counterbalancing must not become a way for the arms to differ."""
    harness = _load_harness()
    # Two brackets with swapped launch order, as --ab-alternate-order produces.
    arms = [
        ("aa_arm_a_b01", {"SHARED": "1"}, []),
        ("aa_arm_b_b01", {"SHARED": "1"}, []),
        ("aa_arm_b_b02", {"SHARED": "1"}, []),
        ("aa_arm_a_b02", {"SHARED": "1"}, []),
    ]
    slots = [(0, 1), (3, 2)]
    assert harness._aa_arms_identical(arms, slots) is True

    poisoned = list(arms)
    poisoned[2] = ("aa_arm_b_b02", {"SHARED": "1", "SNEAK": "1"}, [])
    assert harness._aa_arms_identical(poisoned, slots) is False


def test_f2_an_invalid_arm_never_reaches_a_delta_or_the_valid_count() -> None:
    """FALSIFIER 2: the A=A path accepts an invalid arm."""
    harness = _load_harness()

    # Bracket 1: both arms valid. Bracket 2: the second arm failed and its
    # receipt carries the numeric 0.0 that must never be averaged.
    results = [
        _valid_arm(16.10),
        _valid_arm(16.50),
        _valid_arm(16.20),
        {"ok": False, "mode_results": [], "avg_delta_ms": 0.0},
    ]
    paired = harness._ab_paired_deltas(results, 2, [(0, 1), (2, 3)])
    assert paired[1] is None
    assert paired[0] is not None
    assert abs(paired[0] - 0.40) < 1e-9

    valid_pairs = [p for p in paired if isinstance(p, (int, float))]
    assert len(valid_pairs) == 1
    # One surviving pair cannot support any noise claim.
    assert harness._aa_noise_control_verdict(paired) == "insufficient_pairs"

    # Every named invalidity route is fail-closed, not just a false ok flag.
    for broken in (
        {"ok": True, "mode_results": []},
        {"ok": True, "mode_results": [
            {"invalid": True, "validity": {"ready": True}, "avg_delta_ms": 16.0}
        ]},
        {"ok": True, "mode_results": [
            {"invalid": False, "validity": {"ready": False}, "avg_delta_ms": 16.0}
        ]},
        {"ok": True, "mode_results": [
            {"invalid": False, "validity": {"ready": True}, "avg_delta_ms": None}
        ]},
    ):
        assert harness._ab_paired_deltas([_valid_arm(16.0), broken], 1, [(0, 1)]) == [None]


# The source-integrity refusals every bracket run must clear BEFORE the A=A
# branch is consulted. Their position, not their presence, is the property:
# a refusal that moved below the branch would be a refusal A=A could skip.
FINGERPRINT_REFUSALS = (
    "--ab-brackets refuses: could not read git status",
    "--ab-brackets refuses: scoped runtime source is dirty",
    "--ab-brackets refuses: could not fingerprint the selected ",
    "--ab-brackets refuses: could not fingerprint the committed ",
    "--ab-brackets refuses: active-index runtime source does not ",
)
AA_BRANCH_MARKER = 'aa_control = bool(getattr(args, "aa_control", False))'
# The single arm launch site, and the per-arm gate called once before and once
# after it.
ARM_LAUNCH_SITE = "run_variant(\n                label,\n                arm_extra_args,"
PER_ARM_GATE_CALL = "gate = _matrix_source_gate("


def _fingerprint_gate_intact(source: str) -> bool:
    """True when the A=A branch cannot reach a launch without the gate.

    `source` is the complete text of the harness. The check is structural
    because the thing being protected is a SEQUENCE of statements, not a
    return value: A=A must remain a branch inside the one bracket runner,
    positioned after every start refusal, sharing the one gated launch site.
    """
    if source.count("def run_ab_brackets(") != 1:
        return False
    # A second runner would own its own (ungated) launch sequence.
    if "def run_aa_brackets(" in source or "def run_aa_control(" in source:
        return False
    if AA_BRANCH_MARKER not in source:
        return False
    aa_branch = source.index(AA_BRANCH_MARKER)
    for refusal in FINGERPRINT_REFUSALS:
        if refusal not in source or source.index(refusal) > aa_branch:
            return False
    # Exactly one launch site, gated immediately before and immediately after.
    if source.count(ARM_LAUNCH_SITE) != 1:
        return False
    if source.count(PER_ARM_GATE_CALL) != 2:
        return False
    return True


def test_f3_aa_control_cannot_bypass_the_source_fingerprint_gate() -> None:
    """FALSIFIER 3: the A=A path bypasses source fingerprints.

    Each mutant below restores exactly one way the A=A path could escape the
    gate and must make the check fail. A mutant that slipped through would mean
    the corresponding check does nothing.
    """
    source = HARNESS.read_text(encoding="utf-8")
    assert _fingerprint_gate_intact(source)

    mutants = [
        # 1. A second, separately gated bracket runner appears.
        source + "\n\ndef run_aa_brackets(args):\n    return 0\n",
        # 2. A start refusal is relocated BELOW the A=A branch, to a position
        #    an A=A run would reach only after the branch was consulted.
        source.replace(FINGERPRINT_REFUSALS[1], "REFUSAL_RELOCATED", 1)
        + "\n# "
        + FINGERPRINT_REFUSALS[1]
        + "\n",
        # 3. The per-arm gate is called once instead of before AND after.
        source.replace(PER_ARM_GATE_CALL, "gate = _skipped_gate(", 1),
        # 4. A second, ungated arm launch site is added.
        source + "\n" + ARM_LAUNCH_SITE + "\n",
    ]
    for index, mutant in enumerate(mutants, start=1):
        assert not _fingerprint_gate_intact(mutant), (
            f"FL-4712 A=A fingerprint-gate mutant {index} did not make the "
            "check fail"
        )


def test_f4_a_one_directional_effect_is_never_called_aa_noise() -> None:
    """FALSIFIER 4: an A/B effect is reported as A=A noise."""
    harness = _load_harness()

    # A real lever: every bracket moves the same way, well past the ceiling.
    assert harness._aa_noise_control_verdict([1.20, 1.40, 1.10]) == "directional_bias"
    assert harness._aa_noise_control_verdict([-1.20, -1.40, -1.10]) == "directional_bias"
    # A consistent small offset is still one-directional, so still not noise.
    assert harness._aa_noise_control_verdict([0.05, 0.06, 0.04]) == "directional_bias"
    # Mixed signs are not enough on their own: the mean must also be inside the
    # ceiling, or an unbalanced spread would pass as noise.
    assert harness._aa_noise_control_verdict([-0.10, 1.50, 1.40]) == "directional_bias"


def test_f4_the_preserved_aa_package_reads_as_noise_only() -> None:
    """The retained FL-4712 A=A deltas must still classify as noise.

    Source: the preserved package at .run/fl4712_fl4216_aa_20260810 — paired
    deltas +0.407225, -0.818092, +0.556742 ms, mean +0.048625 ms. Mixed signs
    plus a near-zero mean is exactly the admissible A=A shape, so this is the
    regression anchor for the verdict rule. It is quoted historical data; this
    test measures nothing.
    """
    harness = _load_harness()
    preserved = [0.407225, -0.818092, 0.556742]
    assert abs(sum(preserved) / len(preserved) - 0.048625) < 1e-6
    assert harness._aa_noise_control_verdict(preserved) == "noise_only"
    assert harness.AA_NOISE_MEAN_TOLERANCE_MS == 0.25


def test_aa_verdict_is_a_total_named_function() -> None:
    """Every input maps to one of exactly three named verdicts."""
    harness = _load_harness()
    allowed = {"insufficient_pairs", "noise_only", "directional_bias"}
    for paired in (
        [],
        [None],
        [None, None],
        [0.5],
        [0.5, None],
        [0.4, -0.3],
        [1.0, 1.0],
        [True, False],  # bools are not measurements and must not be counted
    ):
        assert harness._aa_noise_control_verdict(paired) in allowed
    assert harness._aa_noise_control_verdict([True, False]) == "insufficient_pairs"


def _aa_args(brackets: int = 3, **overrides):
    """An argparse-shaped namespace carrying exactly what run_ab_brackets reads.

    headless=True is deliberate: it skips the real ~8s inter-arm settle sleep,
    so this stays a wiring test with no waiting and no Godot.
    """
    import types

    namespace = types.SimpleNamespace(
        ab_brackets=brackets,
        aa_control=True,
        ab_control_env="",
        ab_control_args="",
        ab_shared_env="",
        ab_alternate_order=True,
        runtime_source_ref="origin/main",
        allow_stable_dirty_runtime=False,
        expect_camera_origin=(78.0999984741211, 17.9365615844727, 144.399993896484),
        stage_timing_gate=False,
        headless=True,
        sample_frames=240,
        warmup_frames=90,
        perf_trace=False,
        godot_gpu_profile=False,
        uncapped=True,
        projection_modes="perspective",
        day_cycle_time=0.5,
        stationary=True,
        expect_presentation_size=(3600, 2252),
        expect_target_size=(3600, 2260),
        expect_ansi_size=(180, 113),
        keep_forever=False,
    )
    for key, value in overrides.items():
        setattr(namespace, key, value)
    return namespace


def _run_aa(harness, avg_delta_ms_by_launch: list, **arg_overrides):
    """Drive run_ab_brackets in A=A mode with every launch and gate mocked.

    Returns (return_code, launches, written_filenames, summary_or_None). Each
    launch is recorded as (label, args_list, env_dict) so the arms' inputs can
    be compared directly.
    The source-integrity and fingerprint helpers are pinned to a clean,
    non-drifting tree because THIS test is about A=A wiring; the gate's own
    refusal behaviour is owned by the FL-4431 harness contract.
    """
    import tempfile
    from unittest.mock import patch

    launches: list = []

    def _recorder(label, extra_args, **kwargs):
        index = len(launches)
        launches.append((label, list(extra_args), dict(kwargs.get("arm_env") or {})))
        return _valid_arm(avg_delta_ms_by_launch[index])

    integrity = harness._SourceIntegrity(
        ok=True, scoped_clean=True, global_dirty=False, scoped_contamination=[]
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        with patch.object(harness, "_scoped_source_integrity", return_value=integrity), \
                patch.object(harness, "_current_head", return_value="H0"), \
                patch.object(harness, "_commit_at_ref", return_value="H0"), \
                patch.object(harness, "_runtime_source_fingerprint", return_value="FP0"), \
                patch.object(harness, "_runtime_source_fingerprint_at_ref", return_value="FP0"), \
                patch.object(harness, "run_variant", _recorder), \
                patch.object(harness, "RUN_DIR", tmp_dir), \
                patch.object(harness, "ROOT", tmp_dir), \
                patch.object(harness, "summarize", lambda results: {"ok": True, "results": results}):
            code = harness.run_ab_brackets(_aa_args(**arg_overrides))
        written = {path.name for path in tmp_dir.iterdir()}
        summary = None
        if (tmp_dir / "aa_summary.json").exists():
            import json

            summary = json.loads((tmp_dir / "aa_summary.json").read_text())
    return code, launches, written, summary


def test_f1_every_launched_aa_arm_pair_receives_identical_env_and_args() -> None:
    """FALSIFIER 1, at the launch site: what the arms are actually given.

    The delta sequence reproduces the preserved FL-4712 A=A package
    (+0.407225, -0.818092, +0.556742 ms) under counterbalanced launch order,
    which is why the recorded per-launch averages are asymmetric even though
    the arms are identical. This is replayed arithmetic, not a measurement.
    """
    harness = _load_harness()
    code, launches, written, summary = _run_aa(
        harness, [16.0, 16.407225, 16.0, 16.818092, 16.0, 16.556742]
    )

    assert code == 0
    assert len(launches) == 6
    assert [label for label, _args, _env in launches] == [
        "aa_arm_a_b01",
        "aa_arm_b_b01",
        "aa_arm_b_b02",
        "aa_arm_a_b02",
        "aa_arm_a_b03",
        "aa_arm_b_b03",
    ]
    # The defining property, read off the launches themselves.
    env_seen = [env for _label, _args, env in launches]
    args_seen = [args for _label, args, _env in launches]
    assert all(env == env_seen[0] for env in env_seen)
    assert all(args == args_seen[0] for args in args_seen)

    # Pair slots follow the ROLE, not the launch position, so bracket 2 (which
    # launched arm_b first) is still oriented arm_b-minus-arm_a.
    assert summary["ab_pair_slots"] == [[0, 1], [3, 2], [4, 5]]
    deltas = summary["ab_paired_control_minus_candidate_ms"]
    for observed, expected in zip(deltas, [0.407225, -0.818092, 0.556742]):
        assert abs(observed - expected) < 1e-6
    assert summary["ab_valid_pair_count"] == 3
    assert summary["aa_control"] is True
    assert summary["aa_noise_control_verdict"] == "noise_only"
    # The A=A summary owns its own filename; no ab_summary.json was touched.
    assert "aa_summary.json" in written
    assert "ab_summary.json" not in written


def test_f4_a_launched_one_directional_result_reads_as_directional_bias() -> None:
    """FALSIFIER 4, end to end: a consistent offset must not read as noise."""
    harness = _load_harness()
    # Every bracket moves the same way by ~1.2 ms regardless of launch order.
    _code, _launches, _written, summary = _run_aa(
        harness, [16.0, 17.2, 16.0, 17.2, 16.0, 17.2]
    )
    assert summary["aa_noise_control_verdict"] == "directional_bias"


def test_f1_an_asymmetric_aa_request_refuses_before_any_launch() -> None:
    """FALSIFIER 1, at the door: a per-arm switch cannot enter A=A mode."""
    harness = _load_harness()

    code, launches, written, _summary = _run_aa(
        harness, [16.0] * 6, ab_control_env="FL4431_DISABLE_EYE_REUSE=1"
    )
    assert code == 2
    assert launches == []
    assert written == set()

    harness = _load_harness()
    code, launches, written, _summary = _run_aa(
        harness, [16.0] * 6, ab_control_args="--some-switch"
    )
    assert code == 2
    assert launches == []
    assert written == set()


def test_ab_mode_is_unchanged_by_the_aa_addition() -> None:
    """The ordinary A/B path still applies the switch to the control arm only."""
    harness = _load_harness()
    code, launches, written, _summary = _run_aa(
        harness,
        [16.0] * 6,
        aa_control=False,
        ab_control_env="FL4431_DISABLE_EYE_REUSE=1",
        ab_control_args="--switch",
    )
    assert code == 0
    assert [label for label, _args, _env in launches][:2] == [
        "ab_candidate_b01",
        "ab_control_b01",
    ]
    candidate = launches[0]
    control = launches[1]
    assert candidate[1] == [] and candidate[2] == {}
    assert control[1] == ["--switch"]
    assert control[2] == {"FL4431_DISABLE_EYE_REUSE": "1"}
    assert "ab_summary.json" in written
    assert "aa_summary.json" not in written


def test_aa_summary_persists_order_slots_deltas_valid_count_and_verdict() -> None:
    """The five required A=A summary facts are written, verdict as its own key."""
    source = HARNESS.read_text(encoding="utf-8")

    assert 'summary["ab_arm_order"] = [label for label, _env, _extra in arms]' in source
    assert 'summary["ab_pair_slots"] = [list(slot) for slot in bracket_slots]' in source
    assert 'summary["ab_paired_control_minus_candidate_ms"] = paired' in source
    assert 'summary["ab_valid_pair_count"] = len(valid_pairs)' in source
    assert 'summary["aa_control"] = aa_control' in source
    # The verdict is an explicit named field, not something a reader infers.
    assert 'summary["aa_noise_control_verdict"] = (' in source
    assert "_aa_noise_control_verdict(paired) if aa_control else None" in source
    assert 'summary["aa_noise_mean_tolerance_ms"] = AA_NOISE_MEAN_TOLERANCE_MS' in source


def test_aa_receipts_and_summary_cannot_overwrite_an_ab_matrix() -> None:
    """Receipt filenames come from the label, so A=A labels must be distinct.

    The prior incident: an A=A run launched right after a feature matrix reused
    the ab_candidate_bNN / ab_control_bNN labels and overwrote that matrix's
    receipts in the shared .run directory.
    """
    source = HARNESS.read_text(encoding="utf-8")

    assert 'receipt = RUN_DIR / f"{label}.json"' in source
    assert "aa_arm_a_b" in source and "aa_arm_b_b" in source
    assert "ab_candidate_b" in source and "ab_control_b" in source
    assert 'RUN_DIR / ("aa_summary.json" if aa_control else "ab_summary.json")' in source


def test_aa_control_requires_brackets_to_pair_against() -> None:
    source = HARNESS.read_text(encoding="utf-8")
    assert '"--aa-control requires --ab-brackets"' in source


if __name__ == "__main__":
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"FL4712_AB_CONTROL_ARGS_CONTRACT_PASS tests={len(tests)}")
