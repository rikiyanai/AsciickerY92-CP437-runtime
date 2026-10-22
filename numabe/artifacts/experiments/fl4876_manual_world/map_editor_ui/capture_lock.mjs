import { fileURLToPath } from 'node:url';
// FL-4876 CAPTURE-LOCK-01: a bounded number of heavy captures (headless Chrome with WebGL, or the game runtime) at a
// time on this machine. 2026-10-16 the operator's computer went down while six agents each ran an editor server, a
// headless Chrome and runtime captures at once. Protocol shared with capture_lock.py: slot directories
// .run/capture_lock (slot 0) and .run/capture_lock_<k> (k >= 1) exist while a capture runs and hold 'pid'; a slot whose
// pid is no longer alive is stale and is taken over. SLOTS = 1: 2026-10-17 the machine went down again with two agent
// captures plus unlocked probe Chromes on the larger map (Tama far bank +35k patches, trees); free memory was ~160 MB
// with 5.9 GB swap just before. Every headless Chrome on the editor page must take this lock, probes included.
import fs from 'node:fs';
const ROOT = fileURLToPath(new URL('../../../../.run/capture_lock', import.meta.url));
const SLOTS = parseInt(process.env.FL4876_CAPTURE_SLOTS || '1', 10);
const slotDir = k => (k === 0 ? ROOT : `${ROOT}_${k}`);
const alive = pid => { try { process.kill(pid, 0); return true; } catch { return false; } };
export async function acquireCaptureLock(label) {
  let dir = null;
  for (let waited = 0; !dir; waited += 2) {
    for (let k = 0; k < SLOTS && !dir; k++) {
      const d = slotDir(k);
      try { fs.mkdirSync(d); fs.writeFileSync(d + '/pid', String(process.pid) + ' ' + label); dir = d; }
      catch (e) {
        if (e.code !== 'EEXIST') throw e;
        let pid = 0; try { pid = parseInt(fs.readFileSync(d + '/pid', 'utf8'), 10); } catch {}
        if (pid && !alive(pid)) { fs.rmSync(d, { recursive: true, force: true }); k--; }
      }
    }
    if (!dir) {
      if (waited % 30 === 0) console.log(`all ${SLOTS} capture slots busy; waiting (${waited}s)`);
      await new Promise(r => setTimeout(r, 2000));
    }
  }
  const release = () => { try { if (parseInt(fs.readFileSync(dir + '/pid', 'utf8'), 10) === process.pid) fs.rmSync(dir, { recursive: true, force: true }); } catch {} };
  process.on('exit', release);
  for (const s of ['SIGINT', 'SIGTERM']) process.on(s, () => { release(); process.exit(1); });
  return release;
}
