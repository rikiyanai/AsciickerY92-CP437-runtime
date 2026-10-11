# Ad hoc script: Compare FL-4336 Attempt 188 current/clone ortho panel luma and glyph-frequency mismatch
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

from pathlib import Path
from PIL import Image
from collections import Counter
import json, statistics
ROOT = Path('docs/research/ascii/verification/fl4336')
cur_root = ROOT / '2026-06-20-attempt-188-current-head48e7583ba-exactpose-fullscreen'
clone_root = ROOT / '2026-06-19-attempt-177-grid' / 'panels'
rows = []
for yaw in (30,150,270):
    cur = Image.open(cur_root / f'yaw{yaw}' / 'fl4270_ortho.png').convert('RGB')
    clo = Image.open(clone_root / f'clone_y{yaw}' / 'fl4270_ortho.png').convert('RGB')
    for name, im in [('current',cur),('clone',clo)]:
        vals = []
        rg = []
        bg = []
        for r,g,b in im.getdata():
            y = 0.2126*r + 0.7152*g + 0.0722*b
            vals.append(y)
            rg.append(r-g)
            bg.append(b-g)
        vals_sorted = sorted(vals)
        def pct(p): return vals_sorted[int((len(vals_sorted)-1)*p)]
        rows.append({
            'yaw': yaw,
            'panel': name,
            'mean_luma': round(statistics.fmean(vals),3),
            'p05': round(pct(.05),3), 'p10': round(pct(.10),3), 'p25': round(pct(.25),3),
            'p50': round(pct(.50),3), 'p75': round(pct(.75),3), 'p90': round(pct(.90),3), 'p95': round(pct(.95),3),
            'dark_frac_lt120': round(sum(v<120 for v in vals)/len(vals),4),
            'bright_frac_gt230': round(sum(v>230 for v in vals)/len(vals),4),
            'unique_rgb': len(set(im.getdata())),
            'mean_r_minus_g': round(statistics.fmean(rg),3),
            'mean_b_minus_g': round(statistics.fmean(bg),3),
        })
    # simple absolute diff stats
    diffs = []
    for a,b in zip(cur.getdata(), clo.getdata()):
        diffs.append(sum(abs(a[i]-b[i]) for i in range(3))/3.0)
    ds = sorted(diffs)
    rows.append({'yaw':yaw,'panel':'absdiff_current_clone','mean_abs':round(statistics.fmean(diffs),3),'p75_abs':round(ds[int(len(ds)*.75)],3),'p90_abs':round(ds[int(len(ds)*.90)],3),'p95_abs':round(ds[int(len(ds)*.95)],3)})
out = cur_root / 'attempt188_ortho_luma_compare.json'
out.write_text(json.dumps(rows, indent=2))
print(out)
print(json.dumps(rows, indent=2))
