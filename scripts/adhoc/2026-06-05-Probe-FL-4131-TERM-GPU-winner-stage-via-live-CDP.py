#!/usr/bin/env node
'use strict';
const net = require('net');
const fs = require('fs');
const path = require('path');
const PORT = parseInt(process.env.PROOF_CDP_PORT || process.env.PORT || '9333', 10);
const OUT_DIR = process.env.OUT_DIR || 'docs/research/ascii/verification/fl4131/termpp_gpu_probe';
fs.mkdirSync(OUT_DIR, { recursive: true });
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function connect(port) {
  return new Promise((resolve, reject) => {
    const s = net.connect({ host: '127.0.0.1', port }, () => resolve(s));
    s.once('error', reject);
  });
}
class Cdp {
  constructor(socket) {
    this.socket = socket; this.nextId = 1; this.buffer = ''; this.pending = new Map();
    socket.setEncoding('utf8');
    socket.on('data', chunk => this.onData(chunk));
    socket.on('error', err => this.rejectAll(err));
    socket.on('close', () => this.rejectAll(new Error('CDP socket closed')));
  }
  onData(chunk) {
    this.buffer += chunk;
    for (;;) {
      const idx = this.buffer.indexOf('\n');
      if (idx < 0) break;
      const line = this.buffer.slice(0, idx); this.buffer = this.buffer.slice(idx + 1);
      if (!line.trim()) continue;
      let msg = null;
      try { msg = JSON.parse(line); } catch (_) { continue; }
      const p = this.pending.get(msg.id);
      if (!p) continue;
      this.pending.delete(msg.id);
      p.resolve(String(msg.result || ''));
    }
  }
  rejectAll(err) {
    for (const p of this.pending.values()) p.reject(err);
    this.pending.clear();
  }
  request(method, params = '', timeoutMs = 45000) {
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params }) + '\n';
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => { this.pending.delete(id); reject(new Error(`timeout ${method}`)); }, timeoutMs);
      this.pending.set(id, { resolve: v => { clearTimeout(t); resolve(v); }, reject: e => { clearTimeout(t); reject(e); } });
      this.socket.write(payload);
    });
  }
  close() { this.socket.destroy(); }
}
function parseFields(line) {
  const out = {};
  for (const m of String(line || '').matchAll(/([a-z_]+)=(-?\d+)/g)) out[m[1]] = Number(m[2]);
  return out;
}
async function main() {
  const socket = await connect(PORT);
  const c = new Cdp(socket);
  const log = [];
  const req = async (method, params = '', timeout = 45000) => {
    const r = await c.request(method, params, timeout);
    log.push(`--- ${method} ${params}\n${r}`);
    return r;
  };
  await req('GET_LOADED_MAP');
  await req('FL4131_SHAPE_LAB_OPEN');
  for (const mat of [1, 2, 3, 4, 5]) {
    await req('FL4131_HARRI_SET_MAT_PROFILE', `${mat} arabic=1 math=1 shapes=1 box=1 punct=0 other=1 katakana=1 min_density=0.05 max_density=1.0`, 15000);
  }
  await req('FL4131_RESET_RUNTIME_SHAPE6_HOOK', '', 15000);
  await req('SET_TERMPP_CAMERA_VIEW', '24 58 14 225 48 32 0', 45000);
  await req('OPEN_TERMPP_CURRENT_VIEW', 'harri=1', 45000);
  await sleep(2500);
  const png = path.join(OUT_DIR, 'termpp_gpu_probe.png');
  try { fs.unlinkSync(png); } catch (_) {}
  await req('CAPTURE_TERMPP_FRAME', png, 45000);
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (fs.existsSync(png) && fs.statSync(png).size > 1024) break;
    await sleep(150);
  }
  const bridge = await req('FL4131_HARRI_DUMP_GPU_BRIDGE', '64 36', 15000);
  const hook = await req('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 15000);
  fs.writeFileSync(path.join(OUT_DIR, 'cdp_log.txt'), log.join('\n'));
  const firstBridge = bridge.split('\n').find(l => l.includes('FL4131_HARRI_GPU_BRIDGE ')) || '';
  const firstHook = hook.split('\n').find(l => l.includes('FL4131_RUNTIME_SHAPE6_HOOK ')) || '';
  const result = {
    ok: fs.existsSync(png) && fs.statSync(png).size > 1024,
    png_path: png,
    bridge: parseFields(firstBridge),
    hook: parseFields(firstHook),
    first_bridge_line: firstBridge.trim(),
    first_hook_line: firstHook.trim()
  };
  fs.writeFileSync(path.join(OUT_DIR, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  c.close();
}
main().catch(err => { console.error(err && err.stack || err); process.exit(1); });
