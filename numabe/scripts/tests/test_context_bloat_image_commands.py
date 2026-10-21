"""Image-output protection must allow byte comparisons (observer 0102)."""
import json
from pathlib import Path
import subprocess
import sys

HOOK = Path(__file__).resolve().parents[1] / 'maintainer/hooks/context_bloat_guard_hook.py'


def run(command):
    return subprocess.run([sys.executable, str(HOOK), '--host', 'codex'],
                          input=json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}}),
                          text=True, capture_output=True)


def test_read_only_atlas_comparison_is_not_an_image_emitter():
    for command in ('cmp base/atlas.png new/atlas.png',
                    'cmp -s base/atlas.png new/atlas.png',
                    'shasum -a 256 base/atlas.png new/atlas.png'):
        result = run(command)
        assert result.returncode == 0, result.stderr


def test_real_image_dump_is_still_refused_for_the_intended_reason():
    for command in ('cat base/atlas.png', 'base64 base/atlas.png', 'xxd base/atlas.png'):
        result = run(command)
        assert result.returncode == 2
        assert 'BLOCKED inline-image command' in result.stderr
        assert json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'
