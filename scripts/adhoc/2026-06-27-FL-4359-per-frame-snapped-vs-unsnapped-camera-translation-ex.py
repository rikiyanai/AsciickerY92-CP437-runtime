# Ad hoc script: FL-4359 per-frame snapped vs unsnapped camera translation extractor — disambiguates 'snapped camera_world frozen while live pose moves' (scenario_moving_camera_continuous false-fail) from genuine steady-camera spectator capture; reads first row per frame_id from a jitter.jsonl capture
# Created: 2026-06-27
# Canonical gap: <describe what tool should own this>

import sys, json
path = sys.argv[1]
seen = {}
order = []
with open(path) as f:
    for line in f:
        if not line.strip():
            continue
        r = json.loads(line)
        fid = r.get("frame_id")
        if fid in seen:
            continue
        cw = r.get("camera_world") or {}
        cu = r.get("camera_world_unsnapped") or {}
        seen[fid] = (
            (cw.get("x",0.0), cw.get("y",0.0), cw.get("z",0.0)),
            (cu.get("x",0.0), cu.get("y",0.0), cu.get("z",0.0)),
        )
        order.append(fid)
def dist(a,b):
    return sum((a[i]-b[i])**2 for i in range(3))**0.5
snap_tot=unsnap_tot=0.0
snap_moves=unsnap_moves=0
prev=None
rows=[]
for fid in order:
    cw,cu=seen[fid]
    if prev is not None:
        ds=dist(cw,prev[0]); du=dist(cu,prev[1])
        snap_tot+=ds; unsnap_tot+=du
        if ds>1e-4: snap_moves+=1
        if du>1e-4: unsnap_moves+=1
        rows.append((fid,ds,du))
    prev=(cw,cu)
print(f"frames={len(order)} pairs={len(rows)}")
print(f"SNAPPED  camera_world:           total_translation={snap_tot:.3f}  moving_pairs={snap_moves}")
print(f"UNSNAPPED camera_world_unsnapped: total_translation={unsnap_tot:.3f}  moving_pairs={unsnap_moves}")
print("per-frame (frame_id, snapped_delta, unsnapped_delta) — first 12 and any big snapped jumps:")
for fid,ds,du in rows[:12]:
    print(f"  f{fid}: snap={ds:.4f}  unsnap={du:.4f}")
print("  ...")
for fid,ds,du in rows:
    if ds>1.0:
        print(f"  BIG SNAP JUMP f{fid}: snap={ds:.4f}  unsnap={du:.4f}")
