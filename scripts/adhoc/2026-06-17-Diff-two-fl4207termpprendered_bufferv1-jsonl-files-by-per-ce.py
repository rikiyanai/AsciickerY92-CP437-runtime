# Ad hoc script: Diff two fl4207.termpp.rendered_buffer.v1 jsonl files by per-cell final_gid (and fg/bk): independent audit of a standalone TERM++ buffer-readback delta — reports total cells, changed final_gid count, changed fg/bk count, so a reviewer can verify a claimed standalone delta from the canonical term_head buffer without trusting the driver self-report
# Created: 2026-06-17
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Diff two TERM++ rendered-buffer jsonl by final_gid. Usage: <this> A.jsonl B.jsonl"""
import json, sys
def load(p):
    cells={}
    with open(p) as f:
        for ln in f:
            ln=ln.strip()
            if not ln: continue
            o=json.loads(ln)
            if o.get("kind")!="cell": continue
            cells[(o["x"],o["y"])]=(o.get("final_gid"),o.get("fg"),o.get("bk"))
    return cells
a=load(sys.argv[1]); b=load(sys.argv[2])
keys=set(a)|set(b)
gid=sum(1 for k in keys if a.get(k,(None,))[0]!=b.get(k,(None,))[0])
col=sum(1 for k in keys if a.get(k,(0,0,0))[1:]!=b.get(k,(0,0,0))[1:])
print(json.dumps({"total_cells":len(keys),"changed_final_gid":gid,"changed_fg_bk":col}))
