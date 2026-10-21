"""set_view.py SRC.a3d DST.a3d X Y YAW -- viewing aid only: writes DST = SRC with the map player-start
centred on map point X,Y (Z from terrain, via the repository writer a3d_edit.cmd_set_player_start) and its
yaw set to YAW degrees (a3d_edit parses --yaw but drops it, so the yaw is written here through the same
section reader/writer). The classic editor centres its view on the player start with that yaw."""
import shutil, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "docs/agent/cli-anything"))
import a3d_edit  # noqa: E402
src, dst, x, y, yaw = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
shutil.copyfile(src, dst)
a3d_edit.cmd_set_player_start(dst, x, y)
pre, fv, inst, ps, eg, mk = a3d_edit.read_a3d_sections(dst)
ps.yaw = yaw
a3d_edit.write_a3d_sections(dst, pre, fv, inst, ps, eg, mk)
pre, fv, inst, ps, eg, mk = a3d_edit.read_a3d_sections(dst)
print("player start", [round(v, 1) for v in ps.pos], "yaw", ps.yaw)
