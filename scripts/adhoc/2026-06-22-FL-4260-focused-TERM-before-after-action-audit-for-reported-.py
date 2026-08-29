# Ad hoc script: FL-4260 focused TERM++ before-after action audit for reported Material Look controls
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ASCIIID = ROOT / ".run" / "asciiid"
OUT_DEFAULT = ROOT / "docs/research/ascii/verification/fl4260/2026-06-22-termpp-before-after-reported-controls"
TERM_CAMERA = "64 64 41160 0 48 32 0"
MAT = 1

class Cdp:
    def __init__(self, port: int, proc: subprocess.Popen[bytes], deadline: float = 45.0) -> None:
        self.next_id = 1
        self.buf = ""
        end = time.time() + deadline
        while time.time() < end:
            if proc.poll() is not None:
                out, err = proc.communicate(timeout=1)
                raise RuntimeError("asciiid exited before CDP listen\n" + out.decode('utf-8','replace')[-4000:] + err.decode('utf-8','replace')[-4000:])
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self.sock.settimeout(None)
                return
            except OSError:
                time.sleep(0.25)
        raise RuntimeError(f"CDP not ready on {port}")

    def call(self, method: str, params: str = "", timeout: float = 30.0) -> str:
        msg_id = self.next_id
        self.next_id += 1
        self.sock.sendall((json.dumps({"id": msg_id, "method": method, "params": params}) + "\n").encode())
        end = time.time() + timeout
        while time.time() < end:
            self.sock.settimeout(max(0.05, end - time.time()))
            try:
                chunk = self.sock.recv(65536).decode('utf-8', 'replace')
            except socket.timeout:
                continue
            if not chunk:
                raise RuntimeError("CDP socket closed")
            self.buf += chunk
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if response.get("id") == msg_id:
                    return str(response.get("result", ""))
        raise TimeoutError(method)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if line and line[0] == '{':
            rows.append(json.loads(line))
    return rows


def cells_map(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    out = {}
    for row in read_jsonl(path):
        if row.get('kind') == 'cell':
            out[(int(row['x']), int(row['y']))] = row
    return out


def changed(before: Path, after: Path) -> list[dict[str, Any]]:
    bmap = cells_map(before)
    amap = cells_map(after)
    rows = []
    for key in sorted(set(bmap) | set(amap)):
        b = bmap.get(key, {})
        a = amap.get(key, {})
        fields = [name for name in ('final_gid', 'fg', 'bk') if b.get(name) != a.get(name)]
        if fields:
            rows.append({
                'x': key[0], 'y': key[1], 'changed': fields,
                'before': {'final_gid': b.get('final_gid'), 'fg': b.get('fg'), 'bk': b.get('bk')},
                'after': {'final_gid': a.get('final_gid'), 'fg': a.get('fg'), 'bk': a.get('bk')},
            })
    return rows


def summarize_delta(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, int] = {'final_gid': 0, 'fg': 0, 'bk': 0}
    for row in rows:
        for field in row.get('changed', []):
            fields[field] = fields.get(field, 0) + 1
    return {'changed_count': len(rows), 'changed_fields': fields}


def dump(cdp: Cdp, out: Path, name: str) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    buffer_path = out / f"{name}.termpp.rendered_buffer.jsonl"
    bridge_path = out / f"{name}.bridge_cells.jsonl"
    png_path = out / f"{name}.termpp.png"
    ui_dir = out / f"{name}.ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    for p in (buffer_path, bridge_path, png_path):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    cdp.call('RENDER_TERMPP_ONCE', '', timeout=20)
    time.sleep(0.3)
    cdp.call('FL4207_DUMP_TERMPP_RENDERED_BUFFER', str(buffer_path.resolve()), timeout=20)
    cdp.call('FL4260_DUMP_BRIDGE_CELLS', str(bridge_path.resolve()), timeout=20)
    cdp.call('CAPTURE_TERMPP_FRAME', str(png_path.resolve()), timeout=20)
    cdp.call('CAPTURE_UI_FRAME', str(ui_dir.resolve()), timeout=20)
    deadline = time.time() + 15
    while time.time() < deadline:
        if buffer_path.exists() and bridge_path.exists() and png_path.exists():
            break
        time.sleep(0.1)
    return {'buffer': buffer_path, 'bridge': bridge_path, 'png': png_path, 'ui': ui_dir / 'ui_frame.png'}


def write_expected(path: Path, action: dict[str, Any], before: Path, bridge: Path) -> dict[str, Any]:
    cells = cells_map(before)
    bridge_cells = cells_map(bridge)
    selected = []
    for key, cell in sorted(cells.items()):
        br = bridge_cells.get(key, {})
        if int(br.get('material_id', -1)) == MAT and int(br.get('eligible', 0)) == 1:
            selected.append({
                'x': key[0], 'y': key[1],
                'before': {'final_gid': cell.get('final_gid'), 'fg': cell.get('fg'), 'bk': cell.get('bk')},
                'bridge': {'material_id': br.get('material_id'), 'eligible': br.get('eligible'), 'ramp': br.get('ramp'), 'density': br.get('density'), 'winner_gid': br.get('winner_gid')},
                'expected_delta': action['expected_delta'],
                'reason': action['expected_reason'],
            })
    payload = {
        'schema': 'fl4260.reported_control_expected_before_action_cells.v1',
        'action_id': action['id'],
        'visible_label': action['visible_label'],
        'selected_material': MAT,
        'termpp_pose': TERM_CAMERA,
        'before_buffer': before.name,
        'before_bridge': bridge.name,
        'expected_delta_class': action['expected_delta_class'],
        'expected_cell_count': len(selected),
        'expected_cells': selected,
    }
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return payload


def parse_rects(text: str) -> dict[str, dict[str, float]]:
    rects = {}
    for line in text.splitlines():
        if 'CTRL_RECT' not in line:
            continue
        parts: dict[str, str] = {}
        for token in line.split():
            if '=' in token:
                k, v = token.split('=', 1)
                parts[k] = v
        label = parts.get('label')
        if label:
            rects[label] = {k: float(parts[k]) for k in ('x', 'y', 'w', 'h') if k in parts}
    return rects


def record_rects(cdp: Cdp, scroll_focus: int) -> dict[str, dict[str, float]]:
    cdp.call('FL4260_RENDERING_PROOF', f'{MAT} 0 {scroll_focus}', timeout=20)
    time.sleep(0.8)
    cdp.call('FL4260_CTRL_RECTS_RECORD', '1', timeout=10)
    time.sleep(0.4)
    return parse_rects(cdp.call('FL4260_CTRL_RECTS_RECORD', '0', timeout=20))


def click_label(cdp: Cdp, label: str, scroll_focus: int, action_dir: Path, rects_name: str = 'visible-rects.json') -> dict[str, Any]:
    rects = record_rects(cdp, scroll_focus)
    (action_dir / rects_name).write_text(json.dumps(rects, indent=2, sort_keys=True), encoding='utf-8')
    rect = rects.get(label)
    if not rect:
        return {'clicked': False, 'reason': 'rect_not_visible', 'label': label}
    x = int(rect['x'] + rect['w'] / 2)
    y = int(rect['y'] + rect['h'] / 2)
    result = cdp.call('RUN_MOUSE_CLICK_PROBE', f'{x} {y}', timeout=10)
    time.sleep(0.8)
    return {'clicked': True, 'label': label, 'x': x, 'y': y, 'result': result.strip()}


def setup(cdp: Cdp) -> None:
    global TERM_CAMERA
    cdp.call('NEW_MAP', '', timeout=20)
    time.sleep(0.8)
    topdown = cdp.call('SET_TOPDOWN_VIEW', 'FULL', timeout=20)
    match = re.search(r'pos=\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\).*yaw=([-0-9.]+)', topdown)
    if match:
        x, y, z, yaw = match.groups()
        TERM_CAMERA = f'{x} {y} {z} {yaw} 48 32 0'
    cdp.call('FL4260_SET_RENDER_MODE', '1', timeout=10)
    cdp.call('FL4260_RENDERING_PROOF', f'{MAT} 0 0', timeout=20)
    cdp.call('CLOSE_TERMPP', '', timeout=10)
    cdp.call('OPEN_TERMPP_CURRENT_VIEW', '', timeout=20)
    time.sleep(2.0)
    cdp.call('SET_TERMPP_CAMERA_VIEW', TERM_CAMERA, timeout=20)
    time.sleep(0.8)
    cdp.call('RENDER_TERMPP_ONCE', '', timeout=20)


def prepare_visible_state(cdp: Cdp, action: dict[str, Any], action_dir: Path) -> list[dict[str, Any]]:
    prep_results = []
    for prep in action.get('prep_clicks', []):
        prep_results.append(click_label(
            cdp,
            prep['rect_label'],
            int(prep.get('scroll_focus', action.get('scroll_focus', 0))),
            action_dir,
            rects_name=f"prep-{prep['rect_label'].replace('.', '_')}-rects.json",
        ))
        time.sleep(0.8)
    return prep_results


def verdict_for(action: dict[str, Any], exec_result: dict[str, Any], delta: list[dict[str, Any]], jitter: list[dict[str, Any]]) -> str:
    if action['kind'] == 'visible_click' and not exec_result.get('clicked'):
        return 'FAIL_ACTION_NOT_EXECUTED'
    actual_count = len(delta)
    jitter_count = len(jitter)
    gate = max(8, jitter_count * 2)
    expected_nonzero = action['expected_delta_class'] != 'NO_DELTA_NAVIGATION'
    if expected_nonzero:
        return 'PASS_DELTA_OBSERVED' if actual_count > gate else 'FAIL_NO_DELTA_ABOVE_JITTER'
    return 'PASS_NO_DELTA_EXPECTED' if actual_count <= gate else 'FAIL_UNEXPECTED_DELTA_ABOVE_JITTER'


def run_action(cdp: Cdp, out: Path, action: dict[str, Any]) -> dict[str, Any]:
    action_dir = out / action['id']
    action_dir.mkdir(parents=True, exist_ok=True)
    setup(cdp)
    if action.get('pre'):
        for method, params in action['pre']:
            cdp.call(method, params, timeout=20)
            time.sleep(0.5)
    prep_results = prepare_visible_state(cdp, action, action_dir)
    jitter_a = dump(cdp, action_dir, 'jitter_a')
    jitter_b = dump(cdp, action_dir, 'jitter_b')
    jitter_delta = changed(jitter_a['buffer'], jitter_b['buffer'])
    (action_dir / 'baseline-jitter-delta-cells.json').write_text(json.dumps(jitter_delta, indent=2), encoding='utf-8')
    before = dump(cdp, action_dir, 'before')
    expected = write_expected(action_dir / 'expected-before-action-cells.json', action, before['buffer'], before['bridge'])
    if action['kind'] == 'visible_click':
        exec_result = click_label(cdp, action['rect_label'], action['scroll_focus'], action_dir)
    else:
        result = cdp.call(action['method'], action['params'], timeout=20)
        time.sleep(0.8)
        exec_result = {'clicked': False, 'method': action['method'], 'params': action['params'], 'result': result.strip()}
    after = dump(cdp, action_dir, 'after')
    delta = changed(before['buffer'], after['buffer'])
    (action_dir / 'actual-delta-cells.json').write_text(json.dumps(delta, indent=2), encoding='utf-8')
    verdict = verdict_for(action, exec_result, delta, jitter_delta)
    gate = max(8, len(jitter_delta) * 2)
    summary = {
        'schema': 'fl4260.reported_control_action_effect.v1',
        'action': action,
        'prep_execution': prep_results,
        'execution': exec_result,
        'expected_before_action_file': 'expected-before-action-cells.json',
        'expected_cell_count': expected['expected_cell_count'],
        'expected_cell_gap': 'bridge_selected_cells_empty' if expected['expected_cell_count'] == 0 else '',
        'baseline_jitter_changed_count': len(jitter_delta),
        'baseline_jitter_summary': summarize_delta(jitter_delta),
        'delta_gate': gate,
        'actual_changed_count': len(delta),
        'actual_delta_summary': summarize_delta(delta),
        'verdict': verdict,
        'jitter': {k: str(v.relative_to(action_dir)) for k, v in jitter_a.items()} | {f"{k}_second": str(v.relative_to(action_dir)) for k, v in jitter_b.items()},
        'before': {k: str(v.relative_to(action_dir)) for k, v in before.items()},
        'after': {k: str(v.relative_to(action_dir)) for k, v in after.items()},
    }
    (action_dir / 'ACTION_PROOF.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8799)
    parser.add_argument('--out', type=Path, default=OUT_DEFAULT)
    parser.add_argument('--only', default='', help='Comma-separated action ids to run')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    actions = [
        {'id': 'click_glyph_style_presets', 'kind': 'visible_click', 'rect_label': 'starters.glyph_style_presets', 'scroll_focus': 0, 'visible_label': 'Glyph Style Presets', 'expected_delta_class': 'NO_DELTA_NAVIGATION', 'expected_delta': {'final_gid': 'same', 'fg_bk': 'same'}, 'expected_reason': 'Navigation button should reveal full starter tiles, not mutate TERM++ cells.'},
        {'id': 'click_color_presets', 'kind': 'visible_click', 'rect_label': 'starters.color_presets', 'scroll_focus': 0, 'visible_label': 'Color Presets', 'expected_delta_class': 'NO_DELTA_NAVIGATION', 'expected_delta': {'final_gid': 'same', 'fg_bk': 'same'}, 'expected_reason': 'Navigation button should reveal Colors and Shade Bands, not mutate TERM++ cells.'},
        {'id': 'click_vegetation_cp437_starter', 'kind': 'visible_click', 'rect_label': 'starters.vegetation_cp437', 'scroll_focus': 0, 'visible_label': 'Vegetation CP437 glyph starter', 'expected_delta_class': 'TERMPLUSPLUS_GLYPH_DELTA_EXPECTED', 'expected_delta': {'final_gid': 'change', 'fg_bk': 'same'}, 'expected_reason': 'Glyph starter changes the selected material glyph pool, so eligible TERM++ cells for terrain:1 should change final_gid.'},
        {'id': 'click_vegetation_ramp_color_starter', 'kind': 'visible_click', 'rect_label': 'starters.vegetation_ramp', 'scroll_focus': 0, 'visible_label': 'Vegetation ramp color starter', 'expected_delta_class': 'TERMPLUSPLUS_COLOR_DELTA_EXPECTED', 'expected_delta': {'final_gid': 'same', 'fg_bk': 'change'}, 'expected_reason': 'Palette starter changes selected material row colors, so eligible TERM++ cells for terrain:1 should change fg or bk.'},
        {'id': 'click_first_full_starter', 'kind': 'visible_click', 'rect_label': 'starters.preset_0_GRASS', 'scroll_focus': 0, 'visible_label': 'First full starter tile', 'expected_delta_class': 'TERMPLUSPLUS_GLYPH_OR_COLOR_DELTA_EXPECTED', 'expected_delta': {'final_gid': 'change', 'fg_bk': 'change_allowed'}, 'expected_reason': 'Full starter changes pool and roles for the selected profile, so eligible TERM++ cells should change glyph and may change colors.'},
        {'id': 'click_fg_swatch_r0', 'kind': 'visible_click', 'rect_label': 'color.fg.r0', 'scroll_focus': 5, 'visible_label': 'fg swatch row 0 click only', 'pre': [('FL4260_APPLY_PALETTE_STARTER', str(MAT))], 'prep_clicks': [{'rect_label': 'starters.color_presets', 'scroll_focus': 0}], 'expected_delta_class': 'TERMPLUSPLUS_COLOR_DELTA_EXPECTED', 'expected_delta': {'final_gid': 'same', 'fg_bk': 'change'}, 'expected_reason': 'User-facing fg control is expected to change visible TERM++ foreground when operated; a click-only no-op is a product finding.'},
        {'id': 'click_bg_swatch_r0', 'kind': 'visible_click', 'rect_label': 'color.bg.r0', 'scroll_focus': 5, 'visible_label': 'bg swatch row 0 click only', 'pre': [('FL4260_APPLY_PALETTE_STARTER', str(MAT))], 'prep_clicks': [{'rect_label': 'starters.color_presets', 'scroll_focus': 0}], 'expected_delta_class': 'TERMPLUSPLUS_COLOR_DELTA_EXPECTED', 'expected_delta': {'final_gid': 'same', 'fg_bk': 'change'}, 'expected_reason': 'User-facing bg control is expected to change visible TERM++ background when operated; a click-only no-op is a product finding.'},
        {'id': 'backend_set_fg_r0_control_path', 'kind': 'backend_command', 'method': 'FL4260_SET_ROW_COLOR', 'params': f'{MAT} 0 fg 255 0 0', 'visible_label': 'fg row 0 backend control path', 'pre': [('FL4260_APPLY_PALETTE_STARTER', str(MAT))], 'expected_delta_class': 'TERMPLUSPLUS_COLOR_DELTA_EXPECTED', 'expected_delta': {'final_gid': 'same', 'fg_bk': 'change'}, 'expected_reason': 'Backend path matching ColorEdit3 mutation should change foreground in TERM++; proves render path when UI operation supplies a changed value.'},
        {'id': 'backend_set_bg_r0_control_path', 'kind': 'backend_command', 'method': 'FL4260_SET_ROW_COLOR', 'params': f'{MAT} 0 bg 0 0 255', 'visible_label': 'bg row 0 backend control path', 'pre': [('FL4260_APPLY_PALETTE_STARTER', str(MAT))], 'expected_delta_class': 'TERMPLUSPLUS_COLOR_DELTA_EXPECTED', 'expected_delta': {'final_gid': 'same', 'fg_bk': 'change'}, 'expected_reason': 'Backend path matching ColorEdit3 mutation should change background in TERM++; proves render path when UI operation supplies a changed value.'},
    ]
    if args.only.strip():
        wanted = {part.strip() for part in args.only.split(',') if part.strip()}
        actions = [action for action in actions if action['id'] in wanted]
        if not actions:
            raise SystemExit(f'no actions matched --only={args.only!r}')
    proc = subprocess.Popen([str(ASCIIID), '--cdp', str(args.port)], cwd=str(ROOT), stdout=(args.out / 'asciiid.stdout.log').open('ab'), stderr=(args.out / 'asciiid.stderr.log').open('ab'))
    cdp = Cdp(args.port, proc)
    results = []
    try:
        for action in actions:
            print(f"[FL-4260] {action['id']}")
            results.append(run_action(cdp, args.out, action))
            (args.out / 'PROOF.json').write_text(json.dumps({'schema': 'fl4260.reported_controls_termpp_audit.v1', 'results': results}, indent=2), encoding='utf-8')
    finally:
        try:
            cdp.call('QUIT', '', timeout=2)
        except Exception:
            pass
        cdp.close()
        try:
            proc.terminate(); proc.wait(timeout=5)
        except Exception:
            proc.kill(); proc.wait(timeout=5)
    failures = [r for r in results if r['verdict'].startswith('FAIL')]
    print(json.dumps({'out': str(args.out.relative_to(ROOT)), 'failures': len(failures), 'results': results}, indent=2))
    return 1 if failures else 0

if __name__ == '__main__':
    raise SystemExit(main())
