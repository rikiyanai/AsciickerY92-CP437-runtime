"""FL-4691 provenance preserves exact diff identity without retaining its bytes."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys


def runner():
    scripts = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("fl4691_provenance_runner", scripts / "run_fl4359_headed_movement.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_streamed_binary_diff_keeps_the_exact_git_digest(tmp_path, monkeypatch):
    module = runner()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / "binary.dat"
    source.write_bytes(bytes(range(256)) * 4096)
    subprocess.run(["git", "add", "binary.dat"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    source.write_bytes(bytes(reversed(range(256))) * 4096)
    expected = subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=tmp_path)
    assert module._worktree_diff_digest() == (0, hashlib.sha256(expected).hexdigest())


def test_preflight_marks_diff_absent_and_does_not_encode_binary_assets(monkeypatch):
    module = runner()

    def forbidden():
        raise AssertionError("preflight encoded the worktree diff")

    monkeypatch.setattr(module, "_worktree_diff_digest", forbidden)
    result = module._repo_provenance(preflight_only=True)
    assert result["worktree_diff_status"] == "not_requested_preflight"
    assert result["worktree_diff_sha256"] == ""
    assert result["worktree_diff_rc"] is None
