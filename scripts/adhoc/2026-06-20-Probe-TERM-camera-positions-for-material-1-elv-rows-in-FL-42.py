# Ad hoc script: Probe TERM++ camera positions for material-1 elv rows in FL-4260 fixture
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

import socket, json, time, re, os, sys
host, base_port = "127.0.0.1", 8850
outdir = "docs/research/ascii/verification/fl4260/2026-06-19-color-precondition-slider-proof/camera_probe"
os.makedirs(outdir, exist_ok=True)
def connect(port):
    s=socket.socket(); s.settimeout(8); s.connect((host,port)); return s
def cmd(s,c,p):
    s.sendall((json.dumps({"id":1,"method":c,"params":p})+"\n").encode()); time.sleep(0.6)
    r=b""
    try:
        while True:
            b=s.recv(4096)
            if not b: break
            r+=b
    except: pass
    return r.decode('utf-8','replace')
port=base_port
s=connect(port)
cmd(s,"LOAD_MAP","assets/a3d/fl4260_fixture_all_materials.a3d")
cmd(s,"FL4260_SET_RENDER_MODE","1")
cmd(s,"FL4260_RENDERING_PROOF","1 0 0")
cmd(s,"FL4260_APPLY_PALETTE_STARTER","1")
cmd(s,"CLOSE_TERMPP","")
time.sleep(0.2)
cmd(s,"OPEN_TERMPP_CURRENT_VIEW","")
time.sleep(0.5)
results=[]
# Safe grid around the fixture; use SAR-like z/pitch/yaw, vary x,y
for x in range(10, 110, 15):
    for y in range(10, 110, 15):
        z=14
        yaw=225; pitch=48
        cam=f"{x} {y} {z} {yaw} {pitch} 32 0"
        cmd(s,"SET_TERMPP_CAMERA_VIEW",cam)
        time.sleep(0.3)
        r=cmd(s,"FL4260_DUMP_PERCELL","1 0 0")
        m=re.search(r'"mat":(\d+),"elv":(\d+),"shade":(\d+),"glyph_id":(\d+),"source_state":"([^"]+)","marker_class":(\d+),"fg_bg_is_marker":(\w+),"fg":(\d+),"bg":(\d+)', r)
        if m:
            mat,elv,shade,glyph,src,mc,fb,fg,bg=m.groups()
            results.append((x,y,int(mat),int(elv),int(shade),int(fg),int(bg)))
            line=f"x={x:3} y={y:3} -> mat={mat} elv={elv} shade={shade} fg={fg} bg={bg}"
            print(line, flush=True)
        # capture frame for later visual triage (skip if crash-prone)
        try:
            cmd(s,"CAPTURE_TERMPP_FRAME",f"{outdir}/cam_{x}_{y}.ppm")
        except Exception as e:
            print(f"capture failed at {x},{y}: {e}", flush=True)
# Save results before any potential crash
with open(f"{outdir}/probe_results.json","w") as f:
    json.dump(results, f, indent=2)
cmd(s,"QUIT","")
s.close()
