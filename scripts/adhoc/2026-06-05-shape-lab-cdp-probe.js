#!/usr/bin/env node
// Connect to ASCIIID CDP and exercise FL4131_SHAPE_LAB_OPEN + UI captures.
const net = require('net');
const fs = require('fs');
const path = require('path');

const PORT = parseInt(process.env.PORT || '9333', 10);
const OUT_DIR = process.env.OUT_DIR || 'docs/research/ascii/verification/fl4131/shape_lab_ux_probe';
fs.mkdirSync(OUT_DIR, { recursive: true });

function connect(port) {
  return new Promise((res, rej) => {
    const s = net.connect({ host: '127.0.0.1', port }, () => res(s));
    s.once('error', rej);
  });
}

class C {
  constructor(s) { this.s = s; this.id = 1; this.buf = ''; this.pend = new Map();
    s.setEncoding('utf8');
    s.on('data', c => { this.buf += c; for (;;) { const i = this.buf.indexOf('\n'); if (i<0) break;
      const line = this.buf.slice(0,i); this.buf = this.buf.slice(i+1);
      if (!line.trim()) continue;
      try { const m = JSON.parse(line); if (this.pend.has(m.id)) { const {res} = this.pend.get(m.id); this.pend.delete(m.id); res(String(m.result||'')); } } catch(_) {}
    }});
  }
  req(method, params='') { const id = this.id++; const p = JSON.stringify({id,method,params})+'\n';
    return new Promise((res, rej) => { const t = setTimeout(() => { this.pend.delete(id); rej(new Error('timeout '+method)); }, 30000);
      this.pend.set(id, {res: v=>{clearTimeout(t); res(v);}, rej: e=>{clearTimeout(t); rej(e);}}); this.s.write(p);
    });
  }
}

(async () => {
  const s = await connect(PORT);
  const c = new C(s);
  console.log('[probe] connected to CDP', PORT);

  // 1) Load and open Shape Lab
  const open_r = await c.req('FL4131_SHAPE_LAB_OPEN');
  console.log('[probe] SHAPE_LAB_OPEN:', open_r);
  fs.writeFileSync(path.join(OUT_DIR, 'shape_lab_open_result.txt'), open_r);

  // 2) Dump Shape Lab state
  const state_r = await c.req('FL4131_SHAPE_LAB_DUMP_STATE');
  fs.writeFileSync(path.join(OUT_DIR, 'shape_lab_state.json'), state_r);
  console.log('[probe] SHAPE_LAB_DUMP_STATE size:', state_r.length);

  // 3) Set a known camera so the editor map view is consistent
  await c.req('SET_CAMERA_VIEW', '0.0 0.0 30.0 60.0 -60.0 16.0');
  await new Promise(r => setTimeout(r, 500));

  // 4) Capture UI frame WITHOUT Shape Lab open (baseline)
  await c.req('SET_TERMPP_EMBEDDED_VISIBLE', '0');
  await new Promise(r => setTimeout(r, 300));
  await c.req('CAPTURE_UI_FRAME', path.join(OUT_DIR, 'ui_no_shapelab.png'));
  console.log('[probe] captured ui_no_shapelab.png');

  // 5) Open Shape Lab again (force open), capture
  await c.req('FL4131_SHAPE_LAB_OPEN');
  await new Promise(r => setTimeout(r, 500));
  await c.req('CAPTURE_UI_FRAME', path.join(OUT_DIR, 'ui_with_shapelab.png'));
  console.log('[probe] captured ui_with_shapelab.png');

  // 6) Also capture with embedded TERM++ enabled (the blog-style live diff layout)
  await c.req('SET_TERMPP_EMBEDDED_VISIBLE', '1');
  await new Promise(r => setTimeout(r, 500));
  await c.req('CAPTURE_UI_FRAME', path.join(OUT_DIR, 'ui_with_shapelab_and_termpp.png'));
  console.log('[probe] captured ui_with_shapelab_and_termpp.png');

  // 7) Dump GPU bridge + runtime hook for record
  const bridge = await c.req('FL4131_HARRI_DUMP_GPU_BRIDGE');
  fs.writeFileSync(path.join(OUT_DIR, 'gpu_bridge.json'), bridge);
  const hook = await c.req('FL4131_DUMP_RUNTIME_SHAPE6_HOOK');
  fs.writeFileSync(path.join(OUT_DIR, 'runtime_hook.json'), hook);

  s.destroy();
  console.log('[probe] done. outputs in', OUT_DIR);
})().catch(e => { console.error('[probe] error:', e); process.exit(1); });
