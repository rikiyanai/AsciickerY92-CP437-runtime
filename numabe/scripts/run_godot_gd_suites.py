#!/usr/bin/env python3
"""Strict runner for headless GDScript contract suites.

WHY THIS EXISTS
---------------
A bare ``godot --headless --script res://...`` invocation is NOT a sufficient
gate, and FL-4216 has a receipt to prove it: a suite printed ``PASS cases=30``
and exited 0 while emitting 17 ``SCRIPT ERROR:`` lines. A GDScript case body
that hits a stale member access aborts, appends no failure, and lets the harness
print its success line anyway. Reading the exit code — or grepping for a PASS
token — cannot see that.

So this runner gates on four independent channels, and a suite is GREEN only
when all four agree:

1. ``exit code`` must be 0.
2. ``SCRIPT ERROR:`` count must be 0. This is the aborted-case-body channel.
   Any occurrence means some case stopped early without reporting.
3. ``check FAIL`` count must be 0. This is the suite's own assertion channel.
4. ``ERROR:`` count must EXACTLY equal the suite's declared expectation. This is
   the ``push_error`` channel, a different one from (2).

On channel 4: a suite that deliberately exercises a misuse path — reading a
field off an explicitly-absent sample, writing to a read-only property — makes
the guard push an error on purpose. Such a suite declares the exact count by
printing ``<TAG> expected_push_errors=N``. An EXACT match is required rather
than a ceiling, so the check is durable in both directions: an extra error is a
regression, and a MISSING one means a guard was silently dropped and the misuse
now passes quietly. A suite that prints no declaration is expected to push zero.

PYTHON CONTRACTS HAVE THEIR OWN FALSE GREEN (FL-4216, found 2026-08-19)
-----------------------------------------------------------------------
A ``.py`` target is gated too, because the same failure shape exists there in a
different disguise: most contracts in this repository are pytest modules with
no ``__main__`` runner, so ``python3 scripts/test_x.py`` merely IMPORTS the file
and exits 0 without executing a single assertion. That is indistinguishable from
a pass at the shell, and it is how a receipt reported five passing contracts
while three of them ran nothing.

So a ``.py`` target routes through pytest, and **collecting zero tests fails**.
A file that genuinely owns a ``__main__`` runner is executed as a script
instead, since that is its declared entry point.

USAGE
-----
    python3 scripts/run_godot_gd_suites.py --lane fl4216-lane0
    python3 scripts/run_godot_gd_suites.py res://scripts/tests/test_x.gd ...
    python3 scripts/run_godot_gd_suites.py scripts/tests/test_x.py ...

Set ``GODOT_BIN`` to override the Godot binary path.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
GODOT_PROJECT = REPO_ROOT / "godot_project"

# Named suite matrices. A lane names the suites whose contracts a change in that
# lane can break, so a reviewer can re-run the same set the author claimed.
LANES = {
    # FL-4216 F11 lane 0: the SurfaceFactsQuery typed boundary, the deterministic
    # static content identity that keys field invalidation, and the SceneGeometryDB
    # asset-identity rules those two depend on.
    "fl4216-lane0": (
        "res://scripts/tests/test_fl4216_water_interaction_field.gd",
        "res://scripts/tests/test_fl4216_solid_water_seed_contour.gd",
        # The canonical multi-layer fragment representation the waterline field
        # is now derived FROM. In this matrix because a change to the derived
        # specialization and a change to the representation break each other.
        "res://scripts/tests/test_fl4216_surface_neighborhood_atlas.gd",
        "res://scripts/tests/test_scene_geometry_db.gd",
        "res://scripts/tests/test_scene_geometry_db_descriptors.gd",
        # Publication seam into the same database. Kept in this matrix because
        # its 17 SCRIPT ERRORs are what proved a bare invocation cannot gate.
        "res://scripts/tests/test_dynamic_mover_descriptor_publication.gd",
    ),
    # FL-4798 map-keyed catalog lane: World's per-map catalog/payload
    # resolution plus the partition lifecycle it feeds. A change to either the
    # resolver or the catalog contract re-runs both.
    "fl4798-map-catalog": (
        "res://scripts/tests/test_fl4798_map_catalog_resolution.gd",
        "res://scripts/tests/test_fl4789_world_partition.gd",
        # FL-4831 dedicated platform: race-map selection, the empty-catalog
        # law, and the loader-side 4.0 plane. Touches the same resolver.
        "res://scripts/tests/test_fl4831_race_flat_map.gd",
    ),
}

# `<TAG> expected_push_errors=N` — the suite's own declaration of how many
# push_error lines its deliberate-misuse cases produce.
EXPECTED_PUSH_ERRORS_RE = re.compile(r"expected_push_errors=(\d+)")


class SuiteResult:
    """One suite's four-channel outcome plus the evidence for it."""

    def __init__(self, target: str, returncode: int, output: str) -> None:
        self.target = target
        self.returncode = returncode
        self.output = output
        lines = output.splitlines()
        # "SCRIPT ERROR:" is checked first because an "ERROR:"-prefixed line is
        # a different channel; matching on a bare "ERROR" substring would
        # conflate the two and make the exact push-error count meaningless.
        self.script_errors = sum(1 for line in lines if "SCRIPT ERROR" in line)
        self.push_errors = sum(1 for line in lines if line.startswith("ERROR:"))
        self.check_failures = sum(1 for line in lines if "check FAIL" in line)
        declarations = EXPECTED_PUSH_ERRORS_RE.findall(output)
        self.expected_push_errors = int(declarations[-1]) if declarations else 0

    @property
    def reasons(self) -> list[str]:
        out: list[str] = []
        if self.returncode != 0:
            out.append(f"exit={self.returncode}")
        if self.script_errors:
            out.append(f"script_errors={self.script_errors} (aborted case bodies)")
        if self.check_failures:
            out.append(f"check_failures={self.check_failures}")
        if self.push_errors != self.expected_push_errors:
            out.append(
                f"push_errors={self.push_errors} != declared {self.expected_push_errors}"
            )
        return out

    @property
    def green(self) -> bool:
        return not self.reasons


COLLECTED_RE = re.compile(r"collected (\d+) items?")


class PythonContractResult:
    """A .py contract's outcome, with the zero-collected case treated as failure."""

    def __init__(self, target: str, returncode: int, output: str, as_script: bool) -> None:
        self.target = target
        self.returncode = returncode
        self.output = output
        self.as_script = as_script
        self.script_errors = 0
        self.push_errors = 0
        self.expected_push_errors = 0
        match = COLLECTED_RE.search(output)
        self.collected = int(match.group(1)) if match else (-1 if not as_script else 1)
        self.check_failures = 0

    @property
    def reasons(self) -> list[str]:
        out: list[str] = []
        if self.returncode != 0:
            out.append(f"exit={self.returncode}")
        if not self.as_script and self.collected <= 0:
            # The whole point of routing through pytest: importing a module and
            # exiting 0 is not evidence that any assertion ran.
            out.append(f"collected={self.collected} (no assertions executed)")
        return out

    @property
    def green(self) -> bool:
        return not self.reasons


def run_python_contract(target: str, timeout: int) -> PythonContractResult:
    path = Path(target)
    source = path.read_text(encoding="utf-8") if path.exists() else ""
    # A declared __main__ entry point is the file's own runner; respect it.
    as_script = "__main__" in source
    cmd = (
        [sys.executable, target]
        if as_script
        else [sys.executable, "-m", "pytest", target, "-q"]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return PythonContractResult(
        target, proc.returncode, proc.stdout + proc.stderr, as_script
    )


def run_suite(target: str, timeout: int, headed_rendering_device: bool) -> SuiteResult:
    godot_args = [GODOT_BIN]
    if not headed_rendering_device:
        godot_args.append("--headless")
    godot_args.extend(["--path", str(GODOT_PROJECT), "--script", target])
    proc = subprocess.run(
        godot_args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return SuiteResult(target, proc.returncode, proc.stdout + proc.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", help="res:// paths of suites to run")
    parser.add_argument("--lane", choices=sorted(LANES), help="named suite matrix")
    parser.add_argument("--timeout", type=int, default=300, help="per-suite seconds")
    parser.add_argument(
        "--show-output",
        action="store_true",
        help="print the full captured output of every NOT-GREEN suite",
    )
    parser.add_argument(
        "--print-output",
        action="store_true",
        help=(
            "print the full captured output of every suite, green or not "
            "(diagnostic suites publish their measurements on stdout)"
        ),
    )
    parser.add_argument(
        "--headed-rendering-device",
        action="store_true",
        help=(
            "run GDScript suites on the headed renderer so tests that require "
            "the process-global RenderingDevice execute real GPU work"
        ),
    )
    args = parser.parse_args()

    targets = list(args.targets)
    if args.lane:
        targets = list(LANES[args.lane]) + targets
    if not targets:
        parser.error("give at least one target (res:// suite or .py contract) or --lane")

    if not Path(GODOT_BIN).exists():
        print(f"BLOCKED godot binary not found at {GODOT_BIN}; set GODOT_BIN")
        return 2

    failed = []
    for target in targets:
        if target.endswith(".py"):
            result = run_python_contract(target, args.timeout)
            verdict = "GREEN" if result.green else "NOT-GREEN"
            print(
                "%-9s %-52s rc=%d collected=%d mode=%s"
                % (
                    verdict,
                    target.rsplit("/", 1)[-1],
                    result.returncode,
                    result.collected,
                    "script" if result.as_script else "pytest",
                )
            )
            if not result.green:
                failed.append(result)
                for reason in result.reasons:
                    print(f"           reason: {reason}")
                if args.show_output:
                    print(result.output)
            continue
        result = run_suite(target, args.timeout, args.headed_rendering_device)
        verdict = "GREEN" if result.green else "NOT-GREEN"
        print(
            "%-9s %-52s rc=%d script_err=%d push_err=%d/%d check_fail=%d"
            % (
                verdict,
                target.rsplit("/", 1)[-1],
                result.returncode,
                result.script_errors,
                result.push_errors,
                result.expected_push_errors,
                result.check_failures,
            )
        )
        if not result.green:
            failed.append(result)
            for reason in result.reasons:
                print(f"           reason: {reason}")
            if args.show_output and not args.print_output:
                print(result.output)
        if args.print_output:
            print(result.output)

    print(f"suites={len(targets)} green={len(targets) - len(failed)} not_green={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
