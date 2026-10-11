# Ad hoc script: FL-4208 N0a byte-identity proof driver: launches asciiid --cdp, OPEN_TERMPP_CURRENT_VIEW at a deterministic camera, CAPTURE_TERMPP_FRAME_WITH_BUFFER; run OFF vs ON (FL4208_TRACE=1) and diff per-cell final_gid to prove GlyphId presentation-neutrality
# Created: 2026-06-29
# Canonical gap: <describe what tool should own this>

// fl4208_n0a_proof.js — N0a byte-identity capture (one run).
//
// Launches asciiid --cdp headed, enables the embedded TERM++ panel, sets a
// deterministic camera, settles a fixed number of frames, then dumps the TERM++
// rendered AnsiCell+GlyphId buffer to a JSONL. FL-4208 instrumentation is
// controlled by the environment (FL4208_TRACE / FL4208_TRACE_PATH) of THIS
// process. Run it OFF and ON and diff the two JSONLs: byte-identity proves the
// N0a instrumentation is presentation-neutral on the TERM++ rendered GlyphIds.
//
// CLI: node fl4208_n0a_proof.js <cdp_port> <out.jsonl>
'use strict';
const { spawn } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = '/Users/r/Downloads/asciicker-Y9-2';
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const MAP_REL = 'assets/a3d/game_map_y8_original_game_map.a3d';
const PORT = parseInt(process.argv[2] || '48951', 10);
const OUT = path.resolve(process.argv[3] || 'out.jsonl');
const READY_TIMEOUT_MS = 60000;

function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
function log(m){ process.stderr.write(`[n0a] ${m}\n`); }

function startAsciiid(port, mapRel){
  const args = ['--cdp', String(port), '--map', mapRel];
  const proc = spawn(ASCIIID, args, { cwd: REPO_ROOT, stdio:['ignore','pipe','pipe'] });
  proc.stdout.on('data', d=>process.stderr.write(`[a-out] ${String(d).slice(0,200)}`));
  proc.stderr.on('data', d=>process.stderr.write(`[a-err] ${String(d).slice(0,200)}`));
  return proc;
}
async function connectCdp(port){
  const deadline = Date.now()+READY_TIMEOUT_MS; let lastErr=null;
  while(Date.now()<deadline){
    try{
      const s = await new Promise((res,rej)=>{
        const sock = net.connect({host:'127.0.0.1',port},()=>{sock.setTimeout(0);res(sock);});
        sock.once('error',rej);
        sock.setTimeout(1000,()=>{sock.destroy();rej(new Error('connect timeout'));});
      });
      s.setEncoding('utf8'); return s;
    }catch(e){ lastErr=e; await sleep(250); }
  }
  throw new Error(`CDP :${port} not ready: ${lastErr&&lastErr.message}`);
}
class Cdp{
  constructor(s){ this.s=s; this.id=1; this.buf=''; this.p=new Map();
    s.on('data',c=>this._d(c));
    s.on('close',()=>{ for(const {reject} of this.p.values()) reject(new Error('closed')); }); }
  _d(c){ this.buf+=c; for(;;){ const i=this.buf.indexOf('\n'); if(i<0)break;
    const line=this.buf.slice(0,i); this.buf=this.buf.slice(i+1); if(!line.trim())continue;
    let m; try{ m=JSON.parse(line); }catch(_){ continue; }
    if(typeof m.id==='number'&&this.p.has(m.id)){ const {resolve}=this.p.get(m.id);
      this.p.delete(m.id); resolve(String(m.result||'')); } } }
  req(method, params='', timeout=30000){ const id=this.id++;
    const payload=JSON.stringify({id,method,params})+'\n';
    return new Promise((resolve,reject)=>{ const t=setTimeout(()=>{this.p.delete(id);reject(new Error(`timeout ${method}`));},timeout);
      this.p.set(id,{resolve:v=>{clearTimeout(t);resolve(v);},reject:e=>{clearTimeout(t);reject(e);}});
      this.s.write(payload); }); }
  close(){ this.s.destroy(); }
}

async function main(){
  if(!fs.existsSync(ASCIIID)) throw new Error(`missing ${ASCIIID}`);
  if(!fs.existsSync(path.join(REPO_ROOT,MAP_REL))) throw new Error(`missing map ${MAP_REL}`);
  try{ if(fs.existsSync(OUT)) fs.unlinkSync(OUT); }catch(_){}
  log(`FL4208_TRACE=${process.env.FL4208_TRACE||'(unset)'} port=${PORT} out=${OUT}`);
  const proc = startAsciiid(PORT, MAP_REL);
  let client=null, rc=1;
  try{
    client = new Cdp(await connectCdp(PORT));
    log('cdp connected');
    // camera FIRST, then open the REAL TERM++ presenter at that exact view
    // (SET_TERMPP_EMBEDDED_VISIBLE is disabled for FL-4260; OPEN_TERMPP_CURRENT_VIEW
    // is the single-owner real presenter path).
    const CAM = process.env.N0A_CAM || '0 0 60 0 38 14';
    log('camera: '+(await client.req('SET_CAMERA_VIEW',CAM,10000)).trim());
    log('open termpp: '+(await client.req('OPEN_TERMPP_CURRENT_VIEW','',10000)).trim());
    // settle a deterministic time so the TERM++ presenter has drawn and both
    // OFF/ON runs capture the same logical frame
    await sleep(4000);
    const PNG = OUT.replace(/\.jsonl$/, '.png');
    // CAPTURE_TERMPP_FRAME_WITH_BUFFER queues png+jsonl for the SAME next TERM++
    // draw (self-syncs; avoids a stale/null last-rendered read).
    log('capture: '+(await client.req('CAPTURE_TERMPP_FRAME_WITH_BUFFER', PNG+' '+OUT, 30000)).trim());
    const deadline=Date.now()+12000;
    while(Date.now()<deadline){ if(fs.existsSync(OUT)&&fs.statSync(OUT).size>64) break; await sleep(200); }
    if(fs.existsSync(OUT)&&fs.statSync(OUT).size>64){ rc=0; log(`OUT ok size=${fs.statSync(OUT).size}`); }
    else log('OUT missing/empty');
    try{ await client.req('QUIT','',3000); }catch(_){}
  } finally {
    try{ if(client) client.close(); }catch(_){}
    try{ proc.kill('SIGKILL'); }catch(_){}
  }
  process.exit(rc);
}
main().catch(e=>{ log('ERROR '+e.message); process.exit(2); });
