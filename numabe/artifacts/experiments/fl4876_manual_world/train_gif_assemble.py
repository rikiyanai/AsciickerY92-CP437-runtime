"""Assemble production_view-K.png (shot order) into a GIF with ffmpeg palettegen/paletteuse and
write a receipt JSON (frame SHA-256 + train-states phase per frame).
usage: train_gif_assemble.py CAPTURE_DIR OUT_GIF WIDTH FPS"""
import hashlib, json, subprocess, sys
from pathlib import Path
cap, gif, width, fps = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
pngs = sorted(cap.glob("production_view-*.png"), key=lambda p: int(p.stem.split("-")[1]))
vf = f"scale={width}:-1:flags=lanczos"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", fps, "-start_number", "0", "-i",
                str(cap / "production_view-%d.png"), "-vf", f"{vf},palettegen=stats_mode=full", str(cap / "palette.png")], check=True)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", fps, "-start_number", "0", "-i",
                str(cap / "production_view-%d.png"), "-i", str(cap / "palette.png"), "-lavfi",
                f"{vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3", "-loop", "0", str(gif)], check=True)
states = {}
for l in (cap / "train-states.jsonl").read_text().splitlines():
    d = json.loads(l)
    states[d["shot_index"]] = [(t["route_id"], t["phase"], round(t["s_m"], 2)) for t in d["trains"]]
rows = []
for p in pngs:
    k = int(p.stem.split("-")[1])
    rows.append({"shot": k, "png": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "trains": states.get(k)})
print(json.dumps({"gif": str(gif), "gif_bytes": gif.stat().st_size, "frames": len(rows),
                  "distinct_sha": len({r["sha256"] for r in rows}), "rows": rows}, indent=1))
