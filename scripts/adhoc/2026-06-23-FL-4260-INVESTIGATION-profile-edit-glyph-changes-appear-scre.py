# Ad hoc script: FL-4260 INVESTIGATION: profile-edit glyph changes appear screen-level, not material/edge/curvature/worldspace-tied. Diff of mat-1 drastic edit changed 0% of the resolved glyph strip but 0.3% of frame (UI text only). Captures change-map to determine screen-keyed vs world-keyed output. Operator observation: cells change with no material/world logic.
# Created: 2026-06-23
# Canonical gap: <describe what tool should own this>

# FL-4260 screen-vs-world resolver investigation
# Captures clean (no-UI) before/after a controlled single-material edit and
# writes a CHANGE MAP png so the spatial pattern of changed cells is visible:
#   - screen-grid-aligned regular pattern => screen-keyed (BUG)
#   - changes following material/terrain features => world-keyed (correct)
import sys,time,os
sys.path.insert(0,'scripts')
from fl4260_cdp_audit import send_cdp
from PIL import Image
D='docs/research/ascii/verification/fl4260/2026-06-23-row-level-fix-verification/screen_vs_world_probe'
os.makedirs(D,exist_ok=True)
def cdp(c,p=None,w=0.45):
    r=send_cdp(c,p); time.sleep(w); return str(r.get('result',''))
def cap(name,wait=2.4):
    cdp('CAPTURE_CLEAN_FRAME',D+'/'+name,0.3); time.sleep(wait)
    return D+'/'+name+'/frame.png'
os.system("osascript -e 'tell application \"System Events\" to set frontmost of (first process whose name contains \"asciiid\") to true' 2>/dev/null")
time.sleep(1)
cdp('LOAD_MAP','assets/a3d/fl4260_fixture_all_materials.a3d',1.5)
cdp('SET_TERMPP_RUNTIME_HARRI_RESOLVE','1',0.4)
cdp('FL4260_SET_RENDER_MODE','1',0.5)
cdp('FL4260_APPLY_PALETTE_STARTER','1',0.6)
b=cap('before')
# controlled drastic edit to ONE material only
for row in range(4):
    cdp('FL4260_SET_ROW_COLOR',f'1 {row} fg 255 0 0',0.25)
cdp('FL4260_SET_PROFILE_SCORING','1 9 0 0 0 0 9',0.4)
a=cap('after')
# change map
ia=Image.open(b).convert('RGB'); ib=Image.open(a).convert('RGB')
W,H=ia.size; pa=ia.load(); pb=ib.load()
out=Image.new('RGB',(W,H),(0,0,0)); po=out.load()
ch=0
for y in range(H):
    for x in range(W):
        if pa[x,y]!=pb[x,y]:
            po[x,y]=(255,0,0); ch+=1
out.save(D+'/change_map.png')
print('changed pixels %d / %d (%.2f%%)'%(ch,W*H,100.0*ch/(W*H)))
print('change_map:',D+'/change_map.png')
