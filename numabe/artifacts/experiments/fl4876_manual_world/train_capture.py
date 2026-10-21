"""Time-separated Classic captures of the station train on a build (final dynamic check only).
usage: train_capture.py RUN OUT_DIR X Y YAW [frames] [interval_s] [offset_s]
Writes production_view-K.png per shot plus train-states.jsonl (runtime truth per shot)."""
import json, os, shutil, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from scripts.pipeline import classic_traversal_capture as CTC
if os.environ.get("FL4876_ALLOW_RUNTIME") != "operator":
    # Operator 2026-10-16: "use the editor" -- the editor's Classic view is the real renderer; launching the game
    # for checks is not allowed. Only the operator may re-enable this (FL4876_ALLOW_RUNTIME=operator).
    sys.exit("train_capture.py is disabled: use the map editor (ev_pose_shots.mjs / audit_shots.mjs) instead")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_lock  # noqa: E402
capture_lock.acquire("train_capture " + " ".join(sys.argv[1:4]))  # one headless Chrome / game capture at a time
run, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
x, y, yaw = map(float, sys.argv[3:6])
frames = sys.argv[6] if len(sys.argv) > 6 else "10"
interval = sys.argv[7] if len(sys.argv) > 7 else "6"
offset = sys.argv[8] if len(sys.argv) > 8 else "72"
rec = json.loads((run / "build_receipt.json").read_text())
env = dict(os.environ, ASCIICKER_ACTIVE_MESH_ROOT=rec["mesh_root"], ASCIICKER_TRAIN_ROUTES=rec["train_routes"],
           ASCIICKER_OBSERVE_RENDER_FRAMES=frames, ASCIICKER_OBSERVE_RENDER_INTERVAL_S=interval,
           ASCIICKER_TRAIN_TIME_OFFSET_S=offset)
scratch = out / "map_pose.a3d"
shutil.copy2(rec["map_path"], scratch)
sprites = Path(rec["map_path"]).with_suffix(".sprites")
if sprites.is_dir():
    shutil.copytree(sprites, scratch.with_suffix(".sprites"), dirs_exist_ok=True)
CTC.embed_player_start(scratch, x, y, yaw)
with open(out / "launch_stdout.log", "wb") as so, open(out / "launch_stderr.log", "wb") as se:
    rc = subprocess.run([str(ROOT / ".run/game_classic"), "--auto-start-map", str(scratch), "--observe-render", str(out)],
                        cwd=str(ROOT), env=env, stdout=so, stderr=se, timeout=900).returncode
for k in range(int(frames)):
    xp = out / f"source-shot-{k}.xp"
    if xp.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts/xp_to_png.py"), str(xp), "--classic-resolved-cells",
                        str(out / f"source-resolved-cells-{k}.jsonl"), "-o", str(out / f"production_view-{k}.png")],
                       capture_output=True)
print("rc", rc, "pngs", len(list(out.glob("production_view-*.png"))))
ts = out / "train-states.jsonl"
if ts.exists():
    for l in ts.read_text().splitlines():
        d = json.loads(l)
        print(d["shot_index"], d["grid"], [(t["route_id"][-1], t["phase"], round(t["s_m"], 1)) for t in d["trains"]])
