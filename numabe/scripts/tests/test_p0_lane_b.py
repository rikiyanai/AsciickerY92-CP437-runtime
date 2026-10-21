"""Focused P0 tests, lane B: race tripwire + FL-4512 recipe capture."""

import hashlib
import json
import os
import stat
import subprocess
import sys

import pytest

from scripts.maintainer.p0 import lane_fl4512, lane_race, lane_trueff


ROOM_REL = "colyseus_server/src/rooms/AuthoritativeRoom.ts"
WORLD_REL = "colyseus_server/src/sim/world.ts"

GUARD_BLOCK = (
    'const FORBIDDEN_INPUT_FIELDS = new Set([\n'
    '  "position",\n'
    '  "hp",\n'
    ']);\n'
    'function validateInputFields(payload, allowedFields) {\n'
    '  if (FORBIDDEN_INPUT_FIELDS.has("x")) { return false; }\n'
    '  return true;\n'
    '}\n'
    'function findForbiddenInputField(value) {\n'
    '  for (const key of Object.keys(value)) {\n'
    '    if (FORBIDDEN_INPUT_FIELDS.has(key)) { return key; }\n'
    '  }\n'
    '  return null;\n'
    '}\n'
)
ROOM_ONE = 'import { syncRaceState } from "./schema/RaceStateSchema.js";\n syncRaceState(this.state.race, raceRunSnapshot(this.world));\n'
ROOM_TWO = ROOM_ONE + ' syncRaceState(this.state.race, raceRunSnapshot(this.world));\n'


def _fixture(repo_root, room_text, world_text):
    room = repo_root / ROOM_REL
    world = repo_root / WORLD_REL
    room.parent.mkdir(parents=True, exist_ok=True)
    world.parent.mkdir(parents=True, exist_ok=True)
    room.write_text(room_text, encoding="utf-8")
    world.write_text(world_text, encoding="utf-8")
    return repo_root


def _write(repo_root, rel, text):
    path = repo_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_race_single_site_with_guard_passes(tmp_path):
    _fixture(tmp_path, ROOM_ONE, GUARD_BLOCK)
    result = lane_race.second_writer_tripwire(tmp_path)
    assert result["sites"] == 1
    assert result["room_sites"] == 1
    assert result["guard_ok"] is True
    assert result["ok"] is True


def test_race_second_site_fails(tmp_path):
    _fixture(tmp_path, ROOM_TWO, GUARD_BLOCK)
    result = lane_race.second_writer_tripwire(tmp_path)
    assert result["sites"] == 2
    assert result["room_sites"] == 2
    assert result["ok"] is False


def test_race_missing_guard_fails(tmp_path):
    _fixture(tmp_path, ROOM_ONE, "// no input sanitizer here\n")
    result = lane_race.second_writer_tripwire(tmp_path)
    assert result["sites"] == 1
    assert result["guard_ok"] is False
    assert result["ok"] is False


def test_race_position_outside_set_literal_fails(tmp_path):
    # Adversarial: "position" appears (comment + unrelated string) but is
    # NOT a member of the FORBIDDEN_INPUT_FIELDS set literal. The old
    # substring check passed this; the member parse must fail it.
    world = (
        '// client "position" updates must never be trusted\n'
        'constOTHER = "position";\n'
        'const FORBIDDEN_INPUT_FIELDS = new Set([\n'
        '  "hp",\n'
        ']);\n'
        'function validateInputFields(payload, allowedFields) {\n'
        '  if (FORBIDDEN_INPUT_FIELDS.has("x")) { return false; }\n'
        '  return true;\n'
        '}\n'
    )
    _fixture(tmp_path, ROOM_ONE, world)
    result = lane_race.second_writer_tripwire(tmp_path)
    assert result["sites"] == 1
    assert result["guard_ok"] is False
    assert result["ok"] is False


def test_race_dead_literal_without_validator_wiring_fails(tmp_path):
    # The set literal forbids position but no validation function
    # references the guard identifier: dead literal, lane fails.
    world = (
        'const FORBIDDEN_INPUT_FIELDS = new Set([\n'
        '  "position",\n'
        ']);\n'
        'function validateInputFields(payload, allowedFields) {\n'
        '  return true;\n'
        '}\n'
    )
    _fixture(tmp_path, ROOM_ONE, world)
    result = lane_race.second_writer_tripwire(tmp_path)
    assert result["guard_ok"] is False
    assert result["ok"] is False


def test_race_second_call_in_another_server_file_fails(tmp_path):
    _fixture(tmp_path, ROOM_ONE, GUARD_BLOCK)
    _write(tmp_path, "colyseus_server/src/sim/encounters.ts",
           "export function tick() {\n  syncRaceState(a, b);\n}\n")
    result = lane_race.second_writer_tripwire(tmp_path)
    assert result["sites"] == 2
    assert result["room_sites"] == 1
    assert result["ok"] is False
    assert "colyseus_server/src/sim/encounters.ts" in result["sources"]


def test_race_definition_comment_and_string_are_not_sites(tmp_path):
    room = (
        ROOM_ONE
        + '// syncRaceState(stale, note);\n'
        + '/* syncRaceState(blocked, note);\n*/\n'
        + 'const s = "syncRaceState(in, string)";\n'
    )
    _fixture(tmp_path, room, GUARD_BLOCK)
    _write(tmp_path, "colyseus_server/src/rooms/schema/RaceStateSchema.ts",
           "export function syncRaceState(target, snapshot) {\n"
           "  return;\n"
           "}\n")
    result = lane_race.second_writer_tripwire(tmp_path)
    assert result["sites"] == 1
    assert result["room_sites"] == 1
    assert result["ok"] is True


def test_race_cli_exit_codes_and_receipt(tmp_path, capsys):
    _fixture(tmp_path, ROOM_ONE, GUARD_BLOCK)
    receipt = tmp_path / "receipt.json"
    assert lane_race.main(["--repo-root", str(tmp_path), "--receipt", str(receipt)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sites"] == 1 and out["guard_ok"] is True
    assert out["room_sites"] == 1
    assert json.loads(receipt.read_text(encoding="utf-8"))["ok"] is True

    _fixture(tmp_path, ROOM_TWO, GUARD_BLOCK)
    assert lane_race.main(["--repo-root", str(tmp_path)]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["sites"] == 2


def test_race_cli_receipt_hashes_every_verdict_source(tmp_path, capsys):
    _fixture(tmp_path, ROOM_ONE, GUARD_BLOCK)
    extra = _write(tmp_path, "colyseus_server/src/sim/encounters.ts",
                   "export function tick() {\n  syncRaceState(a, b);\n}\n")
    assert lane_race.main(["--repo-root", str(tmp_path)]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    expected_sources = {
        ROOM_REL, WORLD_REL, "colyseus_server/src/sim/encounters.ts",
    }
    assert set(out["sources"]) == expected_sources
    assert set(out["source_sha256"]) == expected_sources
    for rel, digest in out["source_sha256"].items():
        raw = (tmp_path / rel).read_bytes()
        assert digest == hashlib.sha256(raw).hexdigest()
    assert out["room_sha256"] == out["source_sha256"][ROOM_REL]
    assert extra.read_bytes()


def test_trueff_receipt_labels_syntax_instrumentation(tmp_path, capsys):
    log = tmp_path / "frame.log"
    log.write_text("[render] fl4691_frame_3_perspective\n", encoding="utf-8")
    assert lane_trueff.main([str(log)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["evidence_class"] == "syntax-instrumentation"
    assert out["provenance_absent"] == [
        "godot", "head", "tree", "fingerprint", "producer", "frame-binding",
    ]


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run_factory(seen, recipe="game-classic:\n\t@echo ok\n"):
    def fake(argv, cwd):
        seen.append(list(argv))
        if argv[:2] == ["make", "-n"] and argv[2] == "audit-game-classic":
            return _Proc(returncode=2, stdout="", stderr="no such target")
        if argv[:2] == ["make", "-n"]:
            return _Proc(stdout=recipe)
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return _Proc(stdout="deadbee" * 7 + "\n")
        if argv[:2] == ["git", "diff"]:
            return _Proc(stdout="")
        raise AssertionError("unexpected argv: %r" % (argv,))
    return fake


def test_fl4512_fallback_dry_run_only_and_receipt_shape(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(lane_fl4512, "_run", _fake_run_factory(seen))
    receipt = lane_fl4512.capture_classic_audit(tmp_path, tmp_path / "art")
    make_calls = [c for c in seen if c[0] == "make"]
    assert make_calls, "expected at least one make invocation"
    for call in make_calls:
        assert "-n" in call, "must never build: %r" % (call,)
    assert receipt["target"] == "game-classic"
    assert set(("head", "recipe_sha", "recipe_text_path")) <= set(receipt)
    assert receipt["recipe_sha"] == hashlib.sha256(
        "game-classic:\n\t@echo ok\n".encode()).hexdigest()
    assert receipt["classic_audit_recipe_surface_present"] is True
    body = open(receipt["recipe_text_path"], encoding="utf-8").read()
    assert "game-classic" in body


def test_fl4512_path_shim_proves_no_build_invocation(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "make_args.log"
    shim = bindir / "make"
    shim.write_text(
        '#!/bin/sh\necho "$@" >> "%s"\n'
        'if [ "$1" = "-n" ]; then echo "shim-recipe for $2"; exit 0; fi\n'
        'echo "BUILD ATTEMPTED" >&2; exit 99\n' % log,
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    env = dict(os.environ, PATH="%s%s%s" % (bindir, os.pathsep, os.environ.get("PATH", "")))
    code = subprocess.run(
        [sys.executable, "-m", "scripts.maintainer.p0.lane_fl4512",
         "--repo-root", str(tmp_path), "--artifacts-dir", str(tmp_path / "art2")],
        capture_output=True, text=True, env=env, cwd=".",
    )
    assert code.returncode == 0, code.stderr
    recorded = log.read_text(encoding="utf-8").splitlines()
    assert recorded, "shim make was never invoked"
    for line in recorded:
        assert line.split()[0] == "-n", "live build invocation: %r" % (line,)
    payload = json.loads(code.stdout)
    assert set(("head", "recipe_sha", "recipe_text_path")) <= set(payload)
    assert payload["classic_audit_recipe_surface_present"] is True


def test_fl4512_cli_failure_exit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lane_fl4512, "_run",
                        lambda argv, cwd: _Proc(returncode=1, stdout="", stderr="boom"))
    assert lane_fl4512.main(["--repo-root", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_lane_clis_run_as_direct_scripts_from_foreign_cwd(tmp_path):
    """Direct `python path/to/lane.py` must work without package context.

    Regression: the sibling-fallback hash import broke twice under
    direct-script invocation (NameError on Path, then on mod) while
    `python -m` tests stayed green. Run from a foreign cwd with an
    empty PYTHONPATH so only the fallback can satisfy the import.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH="")
    for lane in ("lane_race", "lane_fl4512"):
        script = repo / "scripts" / "maintainer" / "p0" / (lane + ".py")
        args = [sys.executable, str(script), "--repo-root", str(repo)]
        if lane == "lane_fl4512":
            args += ["--artifacts-dir", str(tmp_path / ("art_" + lane))]
        proc = subprocess.run(args, capture_output=True, text=True,
                              env=env, cwd=str(tmp_path))
        assert proc.returncode == 0, (lane, proc.stderr[-2000:])
        assert json.loads(proc.stdout)["ok"] is True
