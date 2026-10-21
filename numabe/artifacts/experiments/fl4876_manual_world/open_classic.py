"""open_classic.py MAP.a3d MESH_ROOT -- opens ONE headed ASCIIID Classic on MAP for the operator's own
interactive inspection (normal headed mode, vsync on, never --mcp), with ASCIICKER_ACTIVE_MESH_ROOT set to
the build's mesh folder, detached so it stays open. Refuses if an asciiid/presenter process is running."""
import os, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
if subprocess.run(["pgrep", "-f", "asciicker-presenter|asciiid"], capture_output=True, text=True).stdout.split():
    sys.exit("refusing: an asciiid/presenter process is already running")
env = dict(os.environ, ASCIICKER_ACTIVE_MESH_ROOT=str(Path(sys.argv[2]).resolve()))
p = subprocess.Popen([str(ROOT / ".run/asciiid-classic"), "--map", str(Path(sys.argv[1]).resolve())], cwd=str(ROOT),
                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
print("ASCIIID Classic pid", p.pid, "map", sys.argv[1])
