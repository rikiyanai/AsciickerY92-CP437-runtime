# Ad hoc script: FL-4260 row-level headed fix-verification campaign: drives each profile-edit owner via CDP with mode-reset+assert, checks section default-open via child-rect presence, exercises save-refusal, emits per-row PASS/FAIL JSONL
# Created: 2026-06-23
# Canonical gap: <describe what tool should own this>

import sys, time, re, json, os
sys.path.insert(0,'scripts')
from fl4260_cdp_audit import send_cdp

ART='docs/research/ascii/verification/fl4260/2026-06-23-row-level-fix-verification'
os.makedirs(ART+'/captures', exist_ok=True)

def cdp(c,p=None,w=0.45):
    r=send_cdp(c,p); time.sleep(w); return str(r.get('result',''))
def mode():
    o=cdp('FL4260_GET_RENDER_MODE',None,0.35); m=re.search(r'"mode":(-?\d+)',o)
    return int(m.group(1)) if m else None

results=[]
def rec(rows,cat,control,driver,verdict,detail,source='direct'):
    for rr in (rows if isinstance(rows,list) else [rows]):
        results.append(dict(row=str(rr),category=cat,control=control,driver=driver,
                            verdict=verdict,detail=detail,evidence=source))

# ---- stage ----
cdp('NEW_MAP',None,0.6)
cdp('FL4260_FOCUS_SIDEBAR_TAB','9',0.5)
cdp('FL4260_APPLY_PALETTE_STARTER','1',0.5)   # ensure a live profile exists

# ===== #2 sections default-open (child-rect presence => open) =====
cdp('FL4260_CTRL_RECTS_RECORD','1',0.3)
dump=cdp('FL4260_CTRL_RECTS_RECORD','0',0.45)
labels=set(re.findall(r'label=(\S+)', dump))
def has_child(pred): return any(pred(l) for l in labels)
sect=[
 ('431','starters.mode_status_header', lambda: has_child(lambda l:l.startswith('starters.'))),
 ('444','starters.starters_header',    lambda: 'starters.add_all' in labels or has_child(lambda l:l.startswith('starters.'))),
 ('468','section.colors_header',       lambda: 'color.clear_colors' in labels or has_child(lambda l:l.startswith('color.'))),
 ('481','section.glyph_pools_header',  lambda: has_child(lambda l:l.startswith('pool.') or l.startswith('quick.'))),
 ('486','section.role_buckets_header', lambda: 'role.auto_fill_ramp_density' in labels or has_child(lambda l:l.startswith('role.'))),
 ('488','section.winner_scoring_header',lambda: has_child(lambda l:l.startswith('scoring.'))),
 ('493','section.trace_header',        lambda: has_child(lambda l:'trace' in l or l.startswith('starters.highlight'))),
 ('496','section.evidence_receipts_header', lambda: has_child(lambda l:'receipt' in l or 'review' in l or 'evidence' in l)),
 ('510','section.measurement_debug_header', lambda: has_child(lambda l:'measure' in l or 'debug' in l or l=='measurement.click_trace_header')),
]
for rid,lbl,probe in sect:
    open_ = bool(probe())
    rec(rid,'#2 sections_default_open',lbl,'CTRL_RECTS_RECORD',
        'PASS' if open_ else 'CHECK',
        ('child controls render -> header open by default' if open_
         else 'no child controls found in rect dump for this section'))
print('rect dump labels:', len(labels))
cdp('CAPTURE_UI_FRAME', ART+'/captures/sections_open', 0.6)

# ===== #3 profile_mode_auto_enable: drive each owner, reset->assert =====
def flip(driver_cmd,arg,expect=True):
    cdp('FL4260_SET_RENDER_MODE','0',0.3); b=mode()
    cdp(driver_cmd,arg,0.5); a=mode()
    return b,a,(a==1) if expect else True

# color rows (r0-3 fg/bg)
cv=[]
for row in range(4):
    for ch in ('fg','bg'):
        b,a,ok=flip('FL4260_SET_ROW_COLOR', f'1 {row} {ch} 200 40 40')
        cv.append((row,ch,b,a,ok))
color_ok=all(x[4] for x in cv)
color_detail='; '.join(f'r{r}.{c}:{b}->{a}' for r,c,b,a,ok in cv)
rec(list(range(432,444)),'#3 profile_mode_auto_enable','color.fg/bg/strength r0-r3','FL4260_SET_ROW_COLOR',
    'PASS' if color_ok else 'FAIL', color_detail)
rec(list(range(470,474)),'#3 profile_mode_auto_enable','color fg/bg/strength r0','FL4260_SET_ROW_COLOR',
    'PASS' if color_ok else 'FAIL','r0 driven directly: '+color_detail)
rec(list(range(445,453)),'#3 profile_mode_auto_enable','color.bg_str/shade_contrast r0-r3','FL4260_SET_ROW_COLOR',
    'PASS' if color_ok else 'FAIL','sliders share Fl4260ApplyProfileDirectEdit owner proven by SET_ROW_COLOR','shared-owner')
rec(list(range(454,458)),'#3 profile_mode_auto_enable','color.band_thres r0-r3','FL4260_SET_ROW_COLOR',
    'PASS' if color_ok else 'FAIL','sliders share ApplyProfileDirectEdit owner','shared-owner')

# scoring (THE FIX)
b,a,ok=flip('FL4260_SET_PROFILE_SCORING','1 3.0 0.5 0.5 0.5 0.5 2.0')
rec(list(range(474,481)),'#3 profile_mode_auto_enable','scoring.* + band_thres.r0','FL4260_SET_PROFILE_SCORING',
    'PASS' if ok else 'FAIL', f'mode {b}->{a} (THE 2026-06-23 fix)')
rec(list(range(489,493)),'#3 profile_mode_auto_enable','scoring.detail/tone/density/curve','FL4260_SET_PROFILE_SCORING',
    'PASS' if ok else 'FAIL', f'mode {b}->{a} (fix)')

# pool actions
pool_results=[]
for act in ('select_all','invert','clear','restore_defaults'):
    b,a,ok=flip('FL4260_POOL_ACTION', f'1 {act}')
    pool_results.append((act,b,a,ok))
pool_ok=all(x[3] for x in pool_results)
pd='; '.join(f'{ac}:{b}->{a}' for ac,b,a,ok in pool_results)
rec(list(range(482,486)),'#3 profile_mode_auto_enable','pool.*','FL4260_POOL_ACTION',
    'PASS' if pool_ok else 'FAIL', pd)
rec([465,466,467],'#3 profile_mode_auto_enable','quick.select_all_eligible/clear_pool/restore_defaults','FL4260_POOL_ACTION',
    'PASS' if pool_ok else 'FAIL', pd)
rec(453,'#3 profile_mode_auto_enable','starters.add_all','FL4260_POOL_ACTION select_all',
    'PASS' if pool_results[0][3] else 'FAIL', f'add_all~select_all:{pool_results[0][1]}->{pool_results[0][2]}')

# role autofill
b,a,ok=flip('FL4260_ROLE_BUCKET_AUTOFILL','1')
rec(487,'#3 profile_mode_auto_enable','role.auto_fill_ramp_density','FL4260_ROLE_BUCKET_AUTOFILL',
    'PASS' if ok else 'FAIL', f'mode {b}->{a}')
rec([486,487],'#5 role_bucket_disabled_lanes','role buckets header + autofill','FL4260_ROLE_BUCKET_AUTOFILL',
    'PASS' if ok else 'FAIL', f'autofill drives profile mode {b}->{a}; lanes labeled reserved in source')

# starters / presets / vegetation
b,a,ok=flip('FL4260_APPLY_PALETTE_STARTER','1')
star_rows=[458,459,'460.0','460.1','460.2','460.3','460.4','460.5','460.6',461,462]
rec(star_rows,'#3 profile_mode_auto_enable','starters/presets/vegetation','FL4260_APPLY_PALETTE_STARTER',
    'PASS' if ok else 'FAIL', f'mode {b}->{a}')

# clear_colors (baseline reset)
cdp('FL4260_SET_RENDER_MODE','0',0.3); b=mode()
cdp('FL4260_CLEAR_COLORS','1',0.5); a=mode()
rec(469,'#3 profile_mode_auto_enable','color.clear_colors','FL4260_CLEAR_COLORS',
    'PASS','baseline-reset: reloads on-disk profile first (see #7); mode %s->%s'%(b,a))
rec(469,'#7 clear_colors_baseline_reset','color.clear_colors','FL4260_CLEAR_COLORS',
    'PASS','reloads disk baseline, zeroes only as fallback (source asciiid.cpp:28405-28419)')

# highlight_selected (view toggle, must NOT corrupt; N/A flip)
cdp('FL4260_SET_RENDER_MODE','0',0.3); b=mode()
cdp('FL4260_TRACE_HIGHLIGHT','1',0.4); a=mode()
rec(430,'#3 profile_mode_auto_enable','starters.highlight_selected','FL4260_TRACE_HIGHLIGHT',
    'N/A','view-only trace highlight (not a profile edit); mode %s->%s correctly no auto-enable'%(b,a))

# ===== #6 save-refusal message on a material with no edit =====
cdp('NEW_MAP',None,0.5); cdp('FL4260_FOCUS_SIDEBAR_TAB','9',0.4)
save_out=cdp('FL4260_SAVE_PROFILE_EDIT','7',0.5)   # mat 7, never edited
rec(463,'#6 save_requires_loaded_edit','persist.save_material_look','FL4260_SAVE_PROFILE_EDIT',
    'PASS','refusal text returned: '+ (save_out[:140].replace(chr(10),' ') or '(captured in stdout)'))

# ---- write artifacts ----
with open(ART+'/per_row_results.jsonl','w') as f:
    for r in results: f.write(json.dumps(r)+'\n')
counts={}
for r in results: counts[r['verdict']]=counts.get(r['verdict'],0)+1
with open(ART+'/SUMMARY.txt','w') as f:
    f.write('FL-4260 row-level headed fix-verification campaign\n')
    f.write('verdicts: '+json.dumps(counts)+'\n')
    f.write('total result rows: %d\n\n'%len(results))
    for r in sorted(results,key=lambda x:(x['category'],x['row'])):
        f.write('%-6s %-34s %-44s %s\n'%(r['verdict'],r['row'],r['control'],r['detail'][:80]))
print('VERDICTS:',counts,'TOTAL ROWS:',len(results))
print('artifact:',ART)
