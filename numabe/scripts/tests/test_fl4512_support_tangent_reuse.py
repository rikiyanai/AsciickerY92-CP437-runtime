"""Freeze actual indexed-support semantics before removing repeated arithmetic."""
from pathlib import Path
import hashlib
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def support_probe(tmp_path_factory):
    executable = tmp_path_factory.mktemp("support-arithmetic") / "support"
    command = ["c++", "-std=c++17", "-O2", "-fsanitize=address,undefined"]
    for directory in (".", "engine", "engine/render", "platform", "server"):
        command.extend(["-I", str(ROOT / directory)])
    command.extend([str(ROOT / "scripts/tests/fl4512_support_tangent_reuse_harness.cpp"),
                    "-o", str(executable)])
    built = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    return executable


def test_indexed_support_preserves_preoptimization_bytes(support_probe):
    observed = subprocess.run([str(support_probe)], cwd=ROOT, capture_output=True)
    assert observed.returncode == 0, observed.stderr.decode()
    assert len(observed.stdout.splitlines()) == 125
    # Recorded by this exact corpus against b67b03454 before tangent reuse.
    # Covers support counts, covered flags, contributor identities, and numeric
    # rejection; this is equivalence evidence, not a topology correctness oracle.
    assert hashlib.sha256(observed.stdout).hexdigest() == (
        "901f5ab89ecc3db13dfb3579a9db049ff8cc84aab7d8d1c295b8a349d3678ccf"
    )


def test_segment_distance_matches_unbounded_integer_oracle(support_probe):
    observed = subprocess.run([str(support_probe), "--distance"], cwd=ROOT,
                              capture_output=True, text=True)
    assert observed.returncode == 0, observed.stderr
    rows = observed.stdout.splitlines()
    assert len(rows) == 12000

    def half_away(numerator, denominator):
        quotient, remainder = divmod(abs(numerator), denominator)
        return (quotient + (2 * remainder >= denominator)) * (-1 if numerator < 0 else 1)

    for row in rows:
        px, py, ax, ay, bx, by, actual_distance, actual_t, actual_valid = map(int, row.split())
        vx, vy = bx - ax, by - ay
        length = vx * vx + vy * vy
        dot = (px - ax) * vx + (py - ay) * vy
        t = 0 if not length or dot <= 0 else 65535 if dot >= length else half_away(dot * 65535, length)
        qx, qy = ax + half_away(vx * t, 65535), ay + half_away(vy * t, 65535)
        distance = (px - qx) ** 2 + (py - qy) ** 2
        valid = distance <= 2**64 - 1
        assert (actual_distance, actual_t, actual_valid) == (distance if valid else 0, t, valid), row


def test_merge_rejoins_shared_control_without_inventing_self_edge(support_probe):
    observed = subprocess.run([str(support_probe), "--merge-shared-control"], cwd=ROOT,
                              capture_output=True, text=True)
    assert observed.returncode == 0, observed.stdout + observed.stderr + f" exit={observed.returncode}"
    assert "shared-control-merge:identity-and-segments-preserved" in observed.stdout


def test_box_bound_preserves_exact_radius_and_relaxation(support_probe):
    observed = subprocess.run([str(support_probe), "--box-bound"], cwd=ROOT,
                              capture_output=True, text=True)
    assert observed.returncode == 0, observed.stdout + observed.stderr + f" exit={observed.returncode}"
    assert "box-bound:20000-distances+window-boundaries+100-relaxation-equivalences" in observed.stdout


def test_witness_transport_preserves_every_edit_and_retirement(support_probe):
    observed = subprocess.run([str(support_probe), "--witness-chunks"], cwd=ROOT,
                              capture_output=True, text=True)
    assert observed.returncode == 0, observed.stdout + observed.stderr + f" exit={observed.returncode}"
    assert "witness-chunks:65-ordered-edits-and-retirement-preserved" in observed.stdout
