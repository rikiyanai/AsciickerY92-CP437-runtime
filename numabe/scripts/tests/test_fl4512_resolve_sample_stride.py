"""The actual shared resolve loop must preserve the supersampled grid."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_real_resolve_loop_uses_two_by_two_cells_for_every_product_route(tmp_path):
    binary = tmp_path / "resolve_stride"
    command = ["c++", "-std=c++20", "-fsanitize=address,undefined", "-g"]
    for directory in (".", "engine", "engine/render", "editor", "platform", "server"):
        command.extend(["-I", str(ROOT / directory)])
    command.extend([str(ROOT / "scripts/tests/fl4512_resolve_sample_stride_harness.cpp"),
                    "-o", str(binary)])
    built = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
    assert built.returncode == 0, built.stdout + built.stderr
    executed = subprocess.run([str(binary)], capture_output=True, text=True, cwd=ROOT)
    assert executed.returncode == 0, executed.stdout + executed.stderr
