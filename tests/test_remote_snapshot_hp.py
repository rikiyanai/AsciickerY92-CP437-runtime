import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    not any(shutil.which(name) for name in ("c++", "clang++", "g++")),
    reason="C++ compiler unavailable",
)
def test_remote_snapshot_applies_hp_and_telemetry_reads_it(tmp_path: Path) -> None:
    compiler = next(
        path
        for name in ("c++", "clang++", "g++")
        if (path := shutil.which(name)) is not None
    )
    executable = tmp_path / "test_remote_snapshot_hp"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Iengine",
            "-Iengine/render",
            "-Iserver",
            "-I.",
            "tests/test_remote_snapshot_hp.cpp",
            "engine/snapshot_client/remote_snapshot_presentation_track.cpp",
            "-o",
            str(executable),
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run([str(executable)], cwd=ROOT, check=True)

    bridge = (ROOT / "engine/game_render_bridge.cpp").read_text(encoding="utf-8")
    assert "debug.dbg_remote0_hp = 0;" not in bridge
    assert bridge.count("debug.dbg_remote0_hp = draw_h->HP;") == 3
