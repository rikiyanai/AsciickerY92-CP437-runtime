#!/usr/bin/env python3
"""Reference F10 frame/cell scroller builder (proves the recording contract).

Reads a frame-exact recording dir (frame_*.png + frame_*.txt + manifest.txt)
and writes frames.html beside them with the frame table and grid geometry
embedded, so it works from file:// with no server. PNGs stay external and are
referenced by relative name.

Usage: python3 build_frames_html.py <frames_dir>
"""
import base64
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "recording"))
import f9_symbolic_codec as symbolic_codec
import f9_session


def parse_txt(path: Path) -> dict:
    meta = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta


def parse_matrix(value: str) -> tuple:
    match = re.match(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]", value or "")
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


HTML_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>F9 frame/cell viewer</title>
<style>
body { background: #000; color: #f0f; font-family: monospace; margin: 0; }
#layout { display: flex; height: 100vh; }
#stage { flex: 1; position: relative; overflow: auto; }
#view { position: relative; display: inline-block; }
#view img { display: block; }
#grid { position: absolute; left: 0; top: 0; pointer-events: none; }
#panel { width: 340px; padding: 12px; border-left: 1px solid #f0f; overflow: auto; }
#panel table { border-collapse: collapse; font-size: 12px; }
#panel td { border: 1px solid #530053; padding: 2px 6px; }
#zoom { image-rendering: pixelated; border: 1px solid #f0f; margin-top: 8px; }
#sellist { font-size: 12px; max-height: 120px; overflow: auto; }
#notes { width: 100%; height: 70px; background: #100010; color: #f0f; border: 1px solid #f0f; }
#coord { color: #0ff; }
button { background: #300030; color: #f0f; border: 1px solid #f0f; margin: 2px; }
input[type=range] { width: 100%; }
.sel { color: #ff0; }
#ident { font-size: 11px; color: #0ff; margin-bottom: 6px; word-break: break-all; }
#viewbanner { font-size: 14px; font-weight: bold; padding: 3px 6px; margin-bottom: 6px; }
#viewbanner.f10 { background: #003300; color: #0f0; }
#viewbanner.notf10 { background: #400000; color: #f44; }
#preservenotice { font-size: 12px; background: #402000; color: #ffb000; padding: 4px 6px; margin-bottom: 6px; white-space: pre-wrap; }
#preservenotice:empty { display: none; }
</style>
</head>
<body>
<div id="layout">
<div id="stage"><div id="view"><img id="img" alt="frame"><canvas id="grid"></canvas></div></div>
<div id="panel">
<div id="preservenotice"></div>
<div id="ident"></div>
<div id="viewbanner"></div>
<div><button id="prev">&lt; prev</button><button id="next">next &gt;</button><button id="play">play</button>
<span id="pos"></span> <span id="fps"></span></div>
<input id="slider" type="range" min="0" max="0" value="0">
<table id="meta"></table>
<div id="coord">hover: -,-</div>
<div id="cell" class="sel">no cell selected (click toggles, shift+click ranges, shift+drag paints, esc clears)</div>
<div id="sellist"></div>
<canvas id="zoom" width="320" height="320"></canvas>
<div>notes (seq <span id="noteseq"></span>) <button id="markframe">mark frame</button> <span id="savestate"></span></div>
<div id="reflist"></div>
<textarea id="notes"></textarea>
<div><button id="savefolder">save session to folder (then autosave)</button> <button id="export">export session json</button><input type="file" id="import" accept=".json"></div>
<div id="folderstate"></div>
<div id="foot">wrapper v<span id="wver"></span> • built <span id="built"></span> • <span id="savestate2"></span></div>
</div>
</div>
<script>
const FRAMES = /*FRAMES_JSON*/;
const VIEW_NAMES = ['NORMAL', 'FGONLY', 'DISOCCLUSION'];
const FPS = /*FPS_VALUE*/;
const WRAPPER_VERSION = 7;
const BUILT_AT = /*BUILT_AT*/;
// Recording identity (FL-4873): manifest.json "identity" of a bundle, or the
// manifest.txt recording_identity line of an older recording; the viewer
// header shows the same record the folder name and MOV title come from.
const IDENTITY = /*IDENTITY_JSON*/;
// Capture identity for session.json (FL-4873): id plus sha256 of manifest.txt
// computed by the builder, settings segments from manifest.json, and a
// session.json that already sat in the folder when the viewer was built.
const CAPTURE = /*CAPTURE_JSON*/;
const SESSION_EMBED = /*SESSION_JSON*/;
const SYMBOLIC_B64 = /*SYMBOLIC_B64*/;
// Preserved crop viewer (FL-4873): null for the recording's own frames.html.
// Otherwise {notice: text shown in the page}; each FRAMES entry then names a
// region crop in 'png' and its full-frame pixel rect in 'crop'. A preserved
// copy never writes browser storage or the folder: it is durable evidence.
const PRESERVED = /*PRESERVED_JSON*/;
// __SYM_DECODER_START__
const SYM_CRC_T = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1); t[n] = c >>> 0; } return t; })();
function symCrc(bin, start, end) { let c = 0xFFFFFFFF; for (let i = start; i < end; i++) c = SYM_CRC_T[(c ^ bin[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }
// Binary sidecar decoder: mirrors scripts/recording/f9_symbolic_codec.py
// decode(). Any bound violation throws; the caller treats that as invalid
// sidecar data, never invented tuples.
function symDecode(b64) {
  const raw = atob(b64);
  const bin = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bin[i] = raw.charCodeAt(i);
  const dv = new DataView(bin.buffer);
  function need(off, n, what) { if (off + n > bin.length) throw new Error('sym truncated at ' + what); }
  need(0, 24, 'header');
  let magic = '';
  for (let i = 0; i < 8; i++) magic += String.fromCharCode(bin[i]);
  let version;
  if (magic === 'F9SYMv01') version = 1;
  else if (magic === 'F9SYMv02') version = 2;
  else throw new Error('sym magic/version mismatch');
  const cols = dv.getUint16(8, true), rows = dv.getUint16(10, true);
  const kf = dv.getUint16(12, true), reserved = dv.getUint16(14, true);
  const count = dv.getUint32(16, true), crc = dv.getUint32(20, true);
  if (reserved !== 0) throw new Error('sym reserved nonzero');
  if (cols < 1 || cols > 1024 || rows < 1 || rows > 1024) throw new Error('sym dims');
  if (cols * rows > 1048576) throw new Error('sym grid ceiling');
  if (kf < 1) throw new Error('sym keyframe_every');
  if (count < 1 || count > 2048) throw new Error('sym frame count');
  if (bin.length > 33554432) throw new Error('sym file ceiling');
  if (symCrc(bin, 24, bin.length) !== crc) throw new Error('sym CRC');
  const cells = cols * rows;
  const frames = [];
  let off = 24, last = -1, run = 0, gapThrough = -1, chainOk = false;
  for (let p = 0; p < count; p++) {
    need(off, 9, 'record');
    const kind = dv.getUint8(off), seq = dv.getUint32(off + 1, true);
    const mode = dv.getUint16(off + 5, true), f10 = dv.getUint16(off + 7, true);
    off += 9;
    if (kind > 3) throw new Error('sym kind');
    if (seq <= last) throw new Error('sym seq order');
    last = seq;
    if (seq <= gapThrough) throw new Error('sym gap overlap');
    if (p === 0 && kind !== 0) throw new Error('sym first key');
    const e = {seq: seq, mode: mode, f10: f10};
    if (kind === 0) {
      e.kind = 'key';
      const cellSize = version === 2 ? 16 : 10;
      need(off, cells * cellSize, 'key');
      e.cells = [];
      for (let i = 0; i < cells; i++) {
        const g = dv.getUint32(off, true);
        const fg = bin[off + 4] | (bin[off + 5] << 8) | (bin[off + 6] << 16);
        const bg = bin[off + 7] | (bin[off + 8] << 8) | (bin[off + 9] << 16);
        if (g > 0x10FFFF) throw new Error('sym glyph');
        const cell = [g, fg, bg];
        if (version === 2) {
          const roleTag = dv.getUint16(off + 10, true);
          if (roleTag & 0xFE00) throw new Error('sym reserved role/tag bits');
          cell.push((roleTag & 0x100) ? 1 : 0);
          cell.push(roleTag & 0xFF);
          cell.push(dv.getUint32(off + 12, true));
        }
        off += cellSize;
        e.cells.push(cell);
      }
      run = 0;
      chainOk = true;
    } else if (kind === 1) {
      if (!chainOk) throw new Error('sym delta after loss');
      e.kind = 'delta';
      need(off, 4, 'delta count');
      const n = dv.getUint32(off, true);
      off += 4;
      if (n > cells) throw new Error('sym changes');
      const cellSize = version === 2 ? 16 : 10;
      const changeSize = 4 + cellSize;
      need(off, n * changeSize, 'delta');
      e.changes = [];
      const seen = {};
      for (let i = 0; i < n; i++) {
        const idx = dv.getUint32(off, true), g = dv.getUint32(off + 4, true);
        const fg = bin[off + 8] | (bin[off + 9] << 8) | (bin[off + 10] << 16);
        const bg = bin[off + 11] | (bin[off + 12] << 8) | (bin[off + 13] << 16);
        if (idx >= cells) throw new Error('sym index');
        if (seen[idx]) throw new Error('sym duplicate');
        if (g > 0x10FFFF) throw new Error('sym glyph');
        seen[idx] = 1;
        const cell = [g, fg, bg];
        if (version === 2) {
          const roleTag = dv.getUint16(off + 14, true);
          if (roleTag & 0xFE00) throw new Error('sym reserved role/tag bits');
          cell.push((roleTag & 0x100) ? 1 : 0);
          cell.push(roleTag & 0xFF);
          cell.push(dv.getUint32(off + 16, true));
        }
        off += changeSize;
        e.changes.push([idx].concat(cell));
      }
      run++;
      if (run > kf) throw new Error('sym cadence');
    } else if (kind === 2) {
      e.kind = 'drop';
      chainOk = false;
    } else {
      e.kind = 'gap';
      need(off, 4, 'gap');
      const through = dv.getUint32(off, true);
      off += 4;
      if (through <= seq) throw new Error('sym gap range');
      e.through = through;
      gapThrough = through;
      chainOk = false;
    }
    frames.push(e);
  }
  if (off !== bin.length) throw new Error('sym trailing bytes');
  return {version: version, format_version: version, cols: cols, rows: rows,
    keyframe_every: kf, frames: frames};
}
let SYMBOLIC = null, SYM_INVALID = false;
if (SYMBOLIC_B64) {
  try { SYMBOLIC = symDecode(SYMBOLIC_B64); }
  catch (e) { SYM_INVALID = true; }
}
const SYM_BY_SEQ = {};
if (SYMBOLIC) { for (const _e of SYMBOLIC.frames) { if (_e.kind !== 'gap') SYM_BY_SEQ[_e.seq] = _e; } }
function symGapCover(seq) {
  if (!SYMBOLIC) return null;
  for (const _e of SYMBOLIC.frames) { if (_e.kind === 'gap' && _e.seq <= seq && seq <= _e.through) return _e; }
  return null;
}
// Sole cell-tuple interpreter in this viewer: keyframe roll-forward with
// changed-cell deltas. Returns {ok,glyph,fg,bg,mode} or {ok:false,reason}
// where reason is 'dropped' (frame kept, data lost) or 'missing' (gap or
// broken chain). Never invents values.
function symbolicAt(seq, cx, cy) {
  if (!SYMBOLIC) return {ok: false, reason: 'absent'};
  if (cx < 0 || cy < 0 || cx >= SYMBOLIC.cols || cy >= SYMBOLIC.rows) return {ok: false, reason: 'range'};
  if (symGapCover(seq)) return {ok: false, reason: 'missing'};
  const t = SYM_BY_SEQ[seq];
  if (!t) return {ok: false, reason: 'missing'};
  if (t.kind === 'drop') return {ok: false, reason: 'dropped'};
  let base = -1;
  for (const _e of SYMBOLIC.frames) { if (_e.kind === 'key' && _e.seq <= seq && _e.seq > base) base = _e.seq; }
  if (base < 0) return {ok: false, reason: 'missing'};
  const grid = SYM_BY_SEQ[base].cells.slice();
  const steps = Object.keys(SYM_BY_SEQ).map(Number).filter((s) => s >= base && s <= seq).sort((a, b) => a - b);
  for (const s of steps) {
    if (s === base) continue;
    const _e = SYM_BY_SEQ[s];
    if (_e.kind === 'drop') return {ok: false, reason: 'missing'};
    if (_e.kind === 'delta') for (const _c of _e.changes) grid[_c[0]] = _c.slice(1);
  }
  const cell = grid[cy * SYMBOLIC.cols + cx];
  const out = {ok: true, glyph: cell[0], fg: cell[1], bg: cell[2], mode: t.mode || 0};
  if (SYMBOLIC.version === 2) {
    out.keyframe_tag = cell[3];
    out.role = cell[4];
    out.chain_id = cell[5];
  }
  return out;
}
function symText(seq, cx, cy) {
  if (SYM_INVALID) return ' sym: invalid sidecar';
  const r = symbolicAt(seq, cx, cy);
  if (r.ok) {
    let text = ' sym glyph=U+' + r.glyph.toString(16).toUpperCase().padStart(4, '0') + ' fg=#' + r.fg.toString(16).padStart(6, '0') + ' bg=#' + r.bg.toString(16).padStart(6, '0') + ' mode=' + r.mode;
    if (r.keyframe_tag !== undefined) {
      text += ' keyframe_tag=' + r.keyframe_tag + ' role=' + r.role + ' chain_id=' + r.chain_id;
      if (r.role === 255) text += ' validity=invalid-cell';
    }
    return text;
  }
  if (r.reason === 'dropped') return ' sym: dropped frame';
  if (r.reason === 'absent') return '';
  return ' sym: no symbolic data';
}
// __SYM_DECODER_END__
let cur = 0, selSet = [], lastClick = null, playing = false, playTimer = null;
let notes = {}, marked = [], dirtySince = null, folderSavedAt = null;
// Session state is keyed by the capture, not the file path, so moving or
// reopening the bundle keeps the annotations (FL-4873).
const SESSION_KEY = 'f9session_' + CAPTURE.id + '_' + CAPTURE.manifest_sha256.slice(0, 16);
const LEGACY_KEY = 'f10state_' + location.pathname.split('/').slice(-2, -1)[0];
function fnv1a32(text) {
  let h = 0x811C9DC5;
  const bytes = new TextEncoder().encode(text);
  for (const b of bytes) { h ^= b; h = Math.imul(h, 0x01000193) >>> 0; }
  return h.toString(16).padStart(8, '0');
}
function sortedCells(cells) {
  return cells.map((c) => [c[0], c[1]]).sort((a, b) => (a[1] - b[1]) || (a[0] - b[0]));
}
function selectionDigest(cells) { return fnv1a32(sortedCells(cells).map((c) => c[0] + ',' + c[1]).join(';')); }
function referencedFrames() {
  const seqs = new Set(marked.map(Number));
  for (const k of Object.keys(notes)) if (String(notes[k]).trim()) seqs.add(Number(k));
  return Array.from(seqs).sort((a, b) => a - b);
}
function frameRanges(seqs) {
  const out = [];
  for (const s of seqs) {
    if (out.length && out[out.length - 1][1] === s - 1) out[out.length - 1][1] = s;
    else out.push([s, s]);
  }
  return out;
}
function segmentOf(seq) {
  for (const seg of CAPTURE.segments || []) if (seg.first_seq <= seq && seq <= seg.last_seq) return seg;
  return null;
}
// session.json schema asciicker.f9_session.v2; the reader and validator for
// other processes is scripts/recording/f9_session.py (same digest).
function sessionDoc() {
  const f = FRAMES[cur] || {};
  const g = geomOf(f);
  const cells = sortedCells(selSet);
  const refs = referencedFrames();
  const kept = {};
  for (const k of Object.keys(notes)) if (String(notes[k]).trim()) kept[k] = notes[k];
  return {schema: 'asciicker.f9_session.v2',
    capture: {id: CAPTURE.id, manifest_sha256: CAPTURE.manifest_sha256},
    grid: {cols: g.cols, rows: g.rows, cell: g.cw + 'x' + g.ch, image: g.W + 'x' + g.H,
      geometry: g.mode, segment: segmentOf(parseInt(f['seq']))},
    selection: {cells: cells, count: cells.length, bbox: selBBox(), digest: selectionDigest(cells)},
    frames: {referenced: refs, ranges: frameRanges(refs), current: parseInt(f['seq'])},
    notes: kept, saved_at: new Date().toISOString()};
}
// Mismatches are rejected, never adopted: another capture, another manifest,
// another grid, cells out of range, or a cell list that fails its digest.
function sessionProblem(doc) {
  if (!doc || doc.schema !== 'asciicker.f9_session.v2') return 'schema is not asciicker.f9_session.v2';
  if (!doc.capture || doc.capture.id !== CAPTURE.id) return 'capture id differs: ' + (doc.capture && doc.capture.id);
  if (doc.capture.manifest_sha256 !== CAPTURE.manifest_sha256) return 'saved against a different manifest.txt';
  const g = geomOf(FRAMES[0]);
  if (!doc.grid || doc.grid.cols !== g.cols || doc.grid.rows !== g.rows) return 'grid differs';
  const cells = (doc.selection && doc.selection.cells) || [];
  const seen = new Set();
  for (const c of cells) {
    if (c[0] < 0 || c[1] < 0 || c[0] >= g.cols || c[1] >= g.rows) return 'cell outside the grid: ' + c;
    const key = c[0] + ',' + c[1];
    if (seen.has(key)) return 'duplicate cell ' + key;
    seen.add(key);
  }
  if (doc.selection.count !== cells.length) return 'selection count ' + doc.selection.count + ' != ' + cells.length + ' cells';
  if (doc.selection.digest !== selectionDigest(cells)) return 'selection digest mismatch';
  const known = new Set(FRAMES.map((f) => parseInt(f['seq'])));
  for (const s of (doc.frames && doc.frames.referenced) || []) if (!known.has(s)) return 'frame ' + s + ' not in this recording';
  return '';
}
function adoptSession(doc) {
  notes = Object.assign({}, doc.notes || {});
  selSet = sortedCells((doc.selection && doc.selection.cells) || []);
  marked = ((doc.frames && doc.frames.referenced) || []).filter((s) => !(String(s) in notes));
  const at = FRAMES.findIndex((f) => parseInt(f['seq']) === (doc.frames && doc.frames.current));
  cur = at >= 0 ? at : 0;
}
(function readState() {
  const candidates = [];
  if (!PRESERVED) try { const raw = localStorage.getItem(SESSION_KEY); if (raw) candidates.push(JSON.parse(raw)); } catch (e) {}
  if (SESSION_EMBED) candidates.push(SESSION_EMBED);
  let best = null;
  for (const c of candidates) {
    if (sessionProblem(c)) continue;
    if (!best || String(c.saved_at) > String(best.saved_at)) best = c;
  }
  if (best) { adoptSession(best); return; }
  if (PRESERVED) return;
  // One-time read of a path-keyed v1 state from before FL-4873.
  try {
    const raw = localStorage.getItem(LEGACY_KEY);
    const data = raw ? JSON.parse(raw) : null;
    if (data && data.notes) { notes = data.notes || {}; selSet = data.sel || []; cur = data.frame || 0; }
  } catch (e) {}
})();
let dragAnchor = null, dragCell = null, dragPreview = null;
const img = document.getElementById('img');
const grid = document.getElementById('grid');
const gx = grid.getContext('2d');
const zoom = document.getElementById('zoom');
const zx = zoom.getContext('2d');
function matrixOf(f) {
  const m = (f['matrix'] || '').match(/\[\s*(\d+)\s*,\s*(\d+)\s*\]/);
  return m ? [parseInt(m[1]), parseInt(m[2])] : [0, 0];
}
function imageOf(f) {
  const m = (f['image'] || '').match(/(\d+)x(\d+)/);
  return m ? [parseInt(m[1]), parseInt(m[2])] : [0, 0];
}
function cellPitchOf(f) {
  const m = (f['cell'] || '').match(/^(\d+)x(\d+)/);
  return m ? [parseInt(m[1]), parseInt(m[2])] : [0, 0];
}
// Raster geometry of the FF terminal (FL-4873). The FF raster maps a
// pixel to cell_xy = pos / cell_px with a fixed pitch anchored top-left, and
// ASCIICompositorEffect sizes the grid as ceil(presentation / cell_px), so the
// last row and column are clipped by the image edge (225x71 at 16x32 over
// 3600x2252: row 70 is 12 px tall). Cell (cx,cy) is therefore
// [cx*cw, cy*ch, min(W,(cx+1)*cw), min(H,(cy+1)*ch)]. A manifest whose cell
// pitch does not reproduce the matrix (older or synthetic recordings) keeps
// the proportional partition and says so in the panel.
function geomOf(f) {
  const [W, H] = imageOf(f), [cols, rows] = matrixOf(f), [cw, ch] = cellPitchOf(f);
  const raster = cw > 0 && ch > 0 && cols > 0 && rows > 0 &&
    Math.ceil(W / cw) === cols && Math.ceil(H / ch) === rows;
  return {W: W, H: H, cols: cols, rows: rows, cw: cw, ch: ch,
    mode: raster ? 'raster' : 'proportional'};
}
function cellRect(cx, cy, g) {
  if (g.mode === 'raster')
    return [cx * g.cw, cy * g.ch, Math.min(g.W, (cx + 1) * g.cw), Math.min(g.H, (cy + 1) * g.ch)];
  return [Math.floor(cx * g.W / g.cols), Math.floor(cy * g.H / g.rows),
    Math.min(g.W, Math.ceil((cx + 1) * g.W / g.cols)), Math.min(g.H, Math.ceil((cy + 1) * g.H / g.rows))];
}
function pixelCell(px, py, g) {
  if (g.mode === 'raster')
    return [Math.max(0, Math.min(g.cols - 1, Math.floor(px / g.cw))),
      Math.max(0, Math.min(g.rows - 1, Math.floor(py / g.ch)))];
  return [Math.max(0, Math.min(g.cols - 1, Math.floor(px / g.W * g.cols))),
    Math.max(0, Math.min(g.rows - 1, Math.floor(py / g.H * g.rows)))];
}
function rectCells(a, b) {
  const out = [];
  for (let x = Math.min(a[0], b[0]); x <= Math.max(a[0], b[0]); x++)
    for (let y = Math.min(a[1], b[1]); y <= Math.max(a[1], b[1]); y++)
      out.push([x, y]);
  return out;
}
function selBBox() {
  if (!selSet.length) return null;
  const xs = selSet.map((s) => s[0]), ys = selSet.map((s) => s[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
function drawGrid(f) {
  const g = geomOf(f), W = g.W, H = g.H, cols = g.cols, rows = g.rows;
  grid.width = img.clientWidth; grid.height = img.clientHeight;
  if (!cols || !rows || !W || !H) return;
  // A preserved crop (f.crop, full-frame pixel rect) shows only part of the
  // frame: cell math stays in full-frame pixels and is shifted by the crop
  // origin, so a cell lands on the same pixels it covers in the full frame.
  const cr = f.crop || null, ox = cr ? cr.x : 0, oy = cr ? cr.y : 0;
  const sx = img.clientWidth / (cr ? cr.w : W), sy = img.clientHeight / (cr ? cr.h : H);
  gx.clearRect(0, 0, grid.width, grid.height);
  gx.strokeStyle = 'rgba(255,0,255,0.25)'; gx.lineWidth = 1;
  gx.beginPath();
  for (let c = 0; c <= cols; c += 4) { const x = (cellRect(Math.min(c, cols - 1), 0, g)[c < cols ? 0 : 2] - ox) * sx; gx.moveTo(x, 0); gx.lineTo(x, grid.height); }
  for (let r = 0; r <= rows; r += 4) { const y = (cellRect(0, Math.min(r, rows - 1), g)[r < rows ? 1 : 3] - oy) * sy; gx.moveTo(0, y); gx.lineTo(grid.width, y); }
  gx.stroke();
  if (selSet.length) {
    selSet.forEach((s, idx) => {
      const [x0, y0, x1, y1] = cellRect(s[0], s[1], g);
      gx.strokeStyle = idx === 0 ? '#ff0' : '#0ff'; gx.lineWidth = 2;
      gx.strokeRect((x0 - ox) * sx, (y0 - oy) * sy, (x1 - x0) * sx, (y1 - y0) * sy);
    });
  }
  if (dragPreview) {
    const [x0, y0, x1, y1] = dragPreview;
    gx.strokeStyle = '#fff'; gx.lineWidth = 1; gx.setLineDash([6, 4]);
    gx.strokeRect((x0 - ox) * sx, (y0 - oy) * sy, (x1 - x0) * sx, (y1 - y0) * sy);
    gx.setLineDash([]);
  }
}
function show(i) {
  cur = Math.max(0, Math.min(FRAMES.length - 1, i));
  const f = FRAMES[cur];
  document.getElementById('pos').textContent = (cur + 1) + '/' + FRAMES.length + ' seq=' + f['seq'];
  document.getElementById('slider').value = cur;
  img.src = f['png'];
  img.onload = () => { drawGrid(f); drawZoom(f); };
  let rows = '';
  for (const k of ['seq','engine_frame','unix_ms','rendered_frame','f10_view','camera','camera_pos','fov','cell','matrix','image','png','capture_ms'])
    rows += '<tr><td>' + k + '</td><td>' + (f[k] || '') + '</td></tr>';
  if (f.crop) rows += '<tr><td>crop</td><td>px ' + [f.crop.x, f.crop.y, f.crop.w, f.crop.h].join(',') +
    ' of ' + f.full_png + ' (full frame by hash only)</td></tr>';
  rows += '<tr><td>geometry</td><td>' + geomOf(f).mode + '</td></tr>';
  document.getElementById('meta').innerHTML = rows;
  document.getElementById('fps').textContent = FPS.toFixed(2) + ' fps';
  loadNote();
  if (SYMBOLIC) drawZoom(f);
  showView(f);
}
function showView(f) {
  const el = document.getElementById('viewbanner');
  const v = f['f10_view'] === undefined || f['f10_view'] === '' ? null : Number(f['f10_view']);
  const name = v === null ? 'UNRECORDED' : (VIEW_NAMES[v] || String(v));
  const glyphOnly = v === 1;
  el.className = glyphOnly ? 'f10' : 'notf10';
  el.textContent = glyphOnly ? 'VIEW: FGONLY (F10 glyph-only)' : 'VIEW: ' + name + ' - NOT the F10 glyph-only view';
}
function drawZoom(f) {
  const box = document.getElementById('cell');
  const list = document.getElementById('sellist');
  if (!selSet.length) {
    box.textContent = 'no cell selected (click toggles, shift+click ranges, shift+drag paints, esc clears)';
    list.innerHTML = '';
    zx.clearRect(0, 0, zoom.width, zoom.height);
    return;
  }
  const g = geomOf(f);
  const s = selSet[0];
  const [x0, y0, x1, y1] = cellRect(s[0], s[1], g);
  zx.imageSmoothingEnabled = false;
  zx.clearRect(0, 0, zoom.width, zoom.height);
  const cr = f.crop || null;
  zx.drawImage(img, x0 - (cr ? cr.x : 0), y0 - (cr ? cr.y : 0), x1 - x0, y1 - y0, 0, 0, zoom.width, zoom.height);
  box.textContent = 'primary (' + s[0] + ',' + s[1] + ') rect ' + [x0, y0, x1, y1].join(',') + ' seq=' + f['seq'];
  box.textContent += symText(parseInt(f['seq']), s[0], s[1]);
  const bbox = selBBox();
  list.innerHTML = '<div>region (' + bbox[0] + ',' + bbox[1] + ')-(' + bbox[2] + ',' + bbox[3] + ') n=' + selSet.length + '</div>' + selSet.map((c, i) => {
    const r = cellRect(c[0], c[1], g);
    return '<div>' + i + ': (' + c[0] + ',' + c[1] + ') [' + r.join(',') + ']</div>';
  }).join('');
}
function eventCell(e) {
  const g = geomOf(FRAMES[cur]);
  if (!g.cols || !g.rows || !g.W || !g.H) return null;
  const r = img.getBoundingClientRect();
  const cr = FRAMES[cur].crop || null;
  if (cr) return pixelCell(cr.x + (e.clientX - r.left) / r.width * cr.w, cr.y + (e.clientY - r.top) / r.height * cr.h, g);
  return pixelCell((e.clientX - r.left) / r.width * g.W, (e.clientY - r.top) / r.height * g.H, g);
}
img.parentElement.addEventListener('mousedown', (e) => {
  if (e.button !== 0) return;
  const c = eventCell(e);
  if (!c) return;
  dragAnchor = c;
  dragCell = c;
  dragPreview = null;
});
img.parentElement.addEventListener('mousemove', (e) => {
  const c = eventCell(e);
  document.getElementById('coord').textContent = c ? ('hover: ' + c[0] + ',' + c[1]) : 'hover: -,-';
  if (!dragAnchor || !(e.buttons & 1) || !c) return;
  if (c[0] === dragCell[0] && c[1] === dragCell[1]) return;
  dragCell = c;
  const f = FRAMES[cur];
  const g = geomOf(f);
  if (!g.cols || !g.W) { dragPreview = null; return; }
  const [ax, ay] = dragAnchor;
  const lo = cellRect(Math.min(ax, c[0]), Math.min(ay, c[1]), g);
  const hi = cellRect(Math.max(ax, c[0]), Math.max(ay, c[1]), g);
  dragPreview = [lo[0], lo[1], hi[2], hi[3]];
  drawGrid(f);
});
window.addEventListener('mouseup', (e) => {
  if (!dragAnchor) return;
  const a = dragAnchor, b = dragCell || dragAnchor;
  const moved = (a[0] !== b[0] || a[1] !== b[1]);
  dragAnchor = null;
  dragCell = null;
  dragPreview = null;
  if (!moved) {
    const at = selSet.findIndex((s) => s[0] === b[0] && s[1] === b[1]);
    if (e.shiftKey && lastClick) {
      selSet = rectCells(lastClick, b);
    } else if (at >= 0) {
      selSet.splice(at, 1);
    } else {
      selSet.unshift(b);
    }
    lastClick = b;
  } else if (e.shiftKey) {
    for (const q of rectCells(a, b)) {
      if (!selSet.some((s) => s[0] === q[0] && s[1] === q[1])) selSet.push(q);
    }
    lastClick = b;
  } else {
    selSet = rectCells(a, b);
    lastClick = b;
  }
  scheduleSave();
  drawGrid(FRAMES[cur]);
  drawZoom(FRAMES[cur]);
});
function setPlaying(on) {
  playing = on;
  document.getElementById('play').textContent = playing ? 'pause' : 'play';
  if (playTimer) { clearInterval(playTimer); playTimer = null; }
  if (playing) playTimer = setInterval(() => show((cur + 1) % FRAMES.length), 1000 / FPS);
}
let saveTimer = null;
function showSaveState() {
  const el = document.getElementById('folderstate');
  if (!dirHandle) el.textContent = 'session.json: not saved to the recording folder yet (click save session to folder)';
  else if (dirtySince) el.textContent = 'session.json: unsaved changes...';
  else el.textContent = 'session.json: saved to folder ' + (folderSavedAt || '');
  el.style.color = dirHandle && !dirtySince ? '#0f0' : '#ff0';
}
function scheduleSave() {
  if (PRESERVED) { document.getElementById('savestate').textContent = 'preserved copy: edits are not saved (export only)'; return; }
  document.getElementById('savestate').textContent = 'editing...';
  dirtySince = Date.now();
  showSaveState();
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      localStorage.setItem(SESSION_KEY, JSON.stringify(sessionDoc()));
      document.getElementById('savestate').textContent = 'browser copy ' + new Date().toLocaleTimeString();
    } catch (e) {
      document.getElementById('savestate').textContent = 'browser copy unavailable';
    }
    if (dirHandle) await writeSessionToFolder(false);
  }, 700);
}
function loadNote() {
  const f = FRAMES[cur];
  document.getElementById('noteseq').textContent = f['seq'];
  document.getElementById('notes').value = notes[f['seq']] || '';
  document.getElementById('savestate').textContent = '';
  const refs = referencedFrames();
  document.getElementById('reflist').textContent = 'referenced frames: ' +
    (refs.length ? frameRanges(refs).map((r) => r[0] === r[1] ? r[0] : r[0] + '-' + r[1]).join(', ') : 'none');
  document.getElementById('markframe').textContent = marked.includes(parseInt(f['seq'])) ? 'unmark frame' : 'mark frame';
}
document.getElementById('notes').addEventListener('input', (e) => {
  notes[FRAMES[cur]['seq']] = e.target.value;
  scheduleSave();
});
document.getElementById('markframe').onclick = () => {
  const seq = parseInt(FRAMES[cur]['seq']);
  marked = marked.includes(seq) ? marked.filter((s) => s !== seq) : marked.concat([seq]);
  loadNote();
  scheduleSave();
};
// Folder save: the first save asks for the recording folder explicitly
// (showDirectoryPicker); the granted handle is kept in IndexedDB so later
// edits autosave there. After a reload the browser may need the permission
// re-granted, which again takes an explicit click.
let dirHandle = null;
function idb(mode, fn) {
  return new Promise((resolve) => {
    try {
      const open = indexedDB.open('f9_session_handles', 1);
      open.onupgradeneeded = () => open.result.createObjectStore('h');
      open.onsuccess = () => {
        const tx = open.result.transaction('h', mode);
        const req = fn(tx.objectStore('h'));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null);
      };
      open.onerror = () => resolve(null);
    } catch (e) { resolve(null); }
  });
}
async function folderMatches(handle) {
  try {
    const file = await (await handle.getFileHandle('manifest.txt')).getFile();
    const text = await file.text();
    return text.includes(CAPTURE.id) || text.includes('recording_identity: ' + CAPTURE.id);
  } catch (e) { return false; }
}
async function writeSessionToFolder(interactive) {
  const status = document.getElementById('savestate2');
  try {
    if (!dirHandle) {
      if (!interactive) return;
      if (!window.showDirectoryPicker) { status.textContent = 'folder save needs Chrome/Edge file access; use export'; return; }
      const picked = await window.showDirectoryPicker({mode: 'readwrite'});
      if (!(await folderMatches(picked))) { status.textContent = 'that folder is not this recording (manifest.txt differs); nothing saved'; return; }
      dirHandle = picked;
      await idb('readwrite', (st) => st.put(dirHandle, SESSION_KEY));
    } else if (interactive && dirHandle.requestPermission) {
      if ((await dirHandle.requestPermission({mode: 'readwrite'})) !== 'granted') { status.textContent = 'folder permission refused'; return; }
    }
    const handle = await dirHandle.getFileHandle('session.json', {create: true});
    const stream = await handle.createWritable();
    await stream.write(JSON.stringify(sessionDoc(), null, 1));
    await stream.close();
    dirtySince = null;
    folderSavedAt = new Date().toLocaleTimeString();
    status.textContent = '';
  } catch (e) {
    status.textContent = 'folder save failed (' + e.name + '); click save session to folder';
    if (!interactive) dirHandle = null;
  }
  showSaveState();
}
(async function restoreFolder() {
  if (PRESERVED) {
    document.getElementById('savefolder').disabled = true;
    document.getElementById('folderstate').textContent = 'preserved copy: read-only (export session json only)';
    document.getElementById('preservenotice').textContent = PRESERVED.notice;
    return;
  }
  const handle = await idb('readonly', (st) => st.get(SESSION_KEY));
  if (handle && handle.queryPermission && (await handle.queryPermission({mode: 'readwrite'})) === 'granted') dirHandle = handle;
  showSaveState();
})();
document.getElementById('savefolder').onclick = () => { if (!PRESERVED) writeSessionToFolder(true); };
document.getElementById('export').onclick = () => {
  const blob = new Blob([JSON.stringify(sessionDoc(), null, 1)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'session.json';
  a.click();
};
document.getElementById('wver').textContent = WRAPPER_VERSION;
document.getElementById('built').textContent = BUILT_AT;
document.getElementById('import').onchange = (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      const problem = sessionProblem(data);
      if (problem) {
        document.getElementById('savestate').textContent = 'import REJECTED: ' + problem;
        return;
      }
      adoptSession(data);
      show(cur);
      drawGrid(FRAMES[cur]);
      drawZoom(FRAMES[cur]);
      scheduleSave();
      document.getElementById('savestate').textContent = 'imported ' + data.selection.count + ' cells';
    } catch (err) { document.getElementById('savestate').textContent = 'import failed: not JSON'; }
  };
  reader.readAsText(file);
};
document.getElementById('prev').onclick = () => show(cur - 1);
document.getElementById('next').onclick = () => show(cur + 1);
document.getElementById('play').onclick = () => setPlaying(!playing);
document.getElementById('slider').oninput = (e) => show(parseInt(e.target.value));
document.addEventListener('keydown', (e) => {
  if (e.target && (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT')) return;
  if (e.key === 'ArrowLeft') show(cur - 1);
  if (e.key === 'ArrowRight') show(cur + 1);
  if (e.key === ' ') { e.preventDefault(); setPlaying(!playing); }
  if (e.key === 'Escape') { selSet = []; scheduleSave(); drawGrid(FRAMES[cur]); drawZoom(FRAMES[cur]); }
});
window.addEventListener('resize', () => drawGrid(FRAMES[cur]));
document.getElementById('slider').max = FRAMES.length - 1;
showSaveState();
// Direct links: frames.html#seq=<seq>&cells=x,y;x,y
// opens that frame with those cells selected, without touching the session.
function applyLinkHash() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ''));
  const seq = params.get('seq');
  if (seq !== null) {
    const at = FRAMES.findIndex((f) => String(f['seq']) === seq);
    if (at >= 0) cur = at;
  }
  const cells = params.get('cells');
  if (cells) selSet = cells.split(';').map((c) => c.split(',').map(Number)).filter((c) => c.length === 2);
}
applyLinkHash();
window.addEventListener('hashchange', () => { applyLinkHash(); show(cur); });
(function showIdentity() {
  const el = document.getElementById('ident');
  if (!IDENTITY) { el.textContent = 'identity: not recorded'; return; }
  document.title = IDENTITY.name || document.title;
  const src = IDENTITY.source || {};
  const cam = IDENTITY.camera || {};
  el.textContent = [IDENTITY.name,
    'mode=' + IDENTITY.mode + ' aspect=' + IDENTITY.cell_aspect + ' grid=' + IDENTITY.cols + 'x' + IDENTITY.rows +
      ' cell=' + IDENTITY.cell_w + 'x' + IDENTITY.cell_h + ' raster=' + IDENTITY.raster_w + 'x' + IDENTITY.raster_h,
    'projection=' + IDENTITY.projection + ' fov=' + cam.fov + ' ortho_size=' + cam.ortho_size + ' profile=' + IDENTITY.diagnostic_profile,
    'source=' + IDENTITY.git_identity + ' observed_at=' + (src.observed_at || 'unknown') + ' stamp=' + IDENTITY.stamp,
    'segments=' + (IDENTITY.segments === undefined ? 'n/a' : IDENTITY.segments)].join(' | ');
})();
show(cur);
</script>
</body>
</html>
"""


def parse_manifest_frame(line):
    """New single-line format: frame: seq=.. engine=.. unix_ms=.. ... (no spaces in values)."""
    parts = line.split()
    if len(parts) < 2 or parts[0] != "frame:":
        return None
    meta = {}
    for token in parts[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            meta[key] = value
    if "seq" not in meta:
        return None
    rename = {"engine": "engine_frame", "rendered": "rendered_frame",
              "pos": "camera_pos"}
    for old, new in rename.items():
        if old in meta:
            meta[new] = meta.pop(old)
    if "f10" in meta:
        ident, _, name = meta.pop("f10").partition(":")
        meta["f10_view"] = ident
        meta["f10_view_name"] = name
    return meta


def raster_geometry(frame: dict) -> dict:
    """Cell geometry the viewer draws (mirror of geomOf/cellRect in the JS).

    The FF raster addresses cell = pixel // cell_px
    from the top-left; the grid is ceil(image / cell_px), so the last row and
    column are clipped by the image edge. When the manifest cell pitch does
    not reproduce the matrix, the proportional partition is kept.
    """
    cols, rows = parse_matrix(frame.get("matrix", ""))
    w, h = (int(v) for v in frame["image"].split("x"))
    match = re.match(r"(\d+)x(\d+)", frame.get("cell", ""))
    cw, ch = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    raster = cw > 0 and ch > 0 and -(-w // cw) == cols and -(-h // ch) == rows
    if raster:
        last = [(cols - 1) * cw, (rows - 1) * ch, min(w, cols * cw), min(h, rows * ch)]
    else:
        last = [(cols - 1) * w // cols, (rows - 1) * h // rows, w, h]
    return {"mode": "raster" if raster else "proportional", "cell": [cw, ch], "last_cell": last}


def identity_payload(frames_dir: Path, manifest: dict):
    """Identity record for the viewer header (FL-4873), or None."""
    path = frames_dir / "manifest.json"
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
        identity = dict(doc.get("identity") or {})
        identity["segments"] = len(doc.get("segments") or [])
        return identity
    name = manifest.get("recording_identity", "")
    return {"name": name, "source": {"observed_at": "manifest.txt only"}} if name else None


def load_frames(frames_dir: Path) -> list:
    """Frame rows of a recording, in manifest.txt order (the viewer's FRAMES)."""
    frames = []
    for line in (frames_dir / "manifest.txt").read_text(encoding="utf-8").splitlines():
        meta = parse_manifest_frame(line)
        if meta is not None:
            frames.append(meta)
            continue
        # Back-compat: original 4-token lines plus per-frame TXT sidecars.
        parts = line.split()
        if len(parts) == 4 and parts[0] == "frame:":
            _, seq, _engine, png = parts
            meta = parse_txt(frames_dir / f"frame_{int(seq):05d}.txt")
            meta["png"] = png
            frames.append(meta)
    return frames


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 1:
        print("usage: build_f10_frames_html.py <frames_dir>", file=sys.stderr)
        return 2
    frames_dir = Path(args[0])
    frames = load_frames(frames_dir)
    # Contract: every manifest frame resolves to PNG+TXT on disk.
    missing = [
        f["png"] for f in frames
        if not (frames_dir / f["png"]).exists()
    ]
    assert not missing, f"missing PNGs: {missing[:5]}"
    assert frames, "no frames in manifest"
    try:
        html = render_html(frames_dir, frames, all_seqs=[int(f["seq"]) for f in frames])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    out = frames_dir / "frames.html"
    out.write_text(html, encoding="utf-8")
    cols, rows = parse_matrix(frames[0].get("matrix", ""))
    w, h = (int(v) for v in frames[0]["image"].split("x"))
    assert cols > 0 and rows > 0, "matrix geometry missing"
    geometry = raster_geometry(frames[0])
    print(f"frames.html frames={len(frames)} cols={cols} rows={rows} image={w}x{h} "
          f"geometry={geometry['mode']} last_cell={geometry['last_cell']}")
    return 0


def render_html(frames_dir: Path, frames: list, *, all_seqs: list,
                preserved: dict | None = None, session_doc: dict | None = None) -> str:
    """The viewer page for `frames` (all of a recording, or preserved crops).

    all_seqs is every recorded seq: the symbolic sidecar joins against the
    whole recording even when only crops are shown. preserved (a dict with a
    visible 'notice') switches the page to the read-only preserved mode.
    session_doc overrides the folder's session.json as the embedded session.
    Raises ValueError on an invalid sidecar instead of embedding invented data.
    """
    manifest = parse_txt(frames_dir / "manifest.txt")
    # Optional versioned BINARY symbolic sidecar: packed keyframe/delta
    # bytes, base64-embedded for the offline viewer decoder. Absent sidecar
    # keeps the legacy path: empty payload, SYMBOLIC null, PNG playback
    # untouched. Present-but-invalid sidecar fails closed here (nonzero
    # exit), never a silent fallback to invented values. The rejected 936
    # JSON tuple stream is not read: a stale symbolic.v1.json beside the
    # binary warns and is ignored, never parsed as a production format.
    symbolic_b64 = ""
    stale_json = frames_dir / "symbolic.v1.json"
    if stale_json.exists():
        print("symbolic.v1.json ignored: binary symbolic.v1.sym only",
              file=sys.stderr)
    sidecar_path = frames_dir / symbolic_codec.SIDECAR_FILENAME
    if sidecar_path.exists():
        try:
            sidecar_raw = sidecar_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"symbolic sidecar unreadable: {exc}") from exc
        try:
            symbolic_doc = symbolic_codec.decode(sidecar_raw)
        except symbolic_codec.SidecarError as exc:
            raise ValueError(f"symbolic sidecar rejected: {exc}") from exc
        try:
            symbolic_codec.join_manifest(symbolic_doc, list(all_seqs))
        except symbolic_codec.SidecarError as exc:
            raise ValueError(f"symbolic sidecar join mismatch: {exc}") from exc
        symbolic_b64 = base64.b64encode(sidecar_raw).decode("ascii")
    html = HTML_HEAD.replace("/*FRAMES_JSON*/", json.dumps(frames))
    t0 = int(frames[0].get("unix_ms", 0))
    t1 = int(frames[-1].get("unix_ms", t0))
    fps = (len(frames) - 1) * 1000.0 / max(1, t1 - t0) if len(frames) > 1 else 0.0
    html = html.replace("/*FPS_VALUE*/", "%.2f" % fps)
    import datetime
    built_at = (preserved or {}).get("built_at") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = html.replace("/*BUILT_AT*/", json.dumps(built_at))
    html = html.replace("/*SYMBOLIC_B64*/", json.dumps(symbolic_b64))
    html = html.replace("/*PRESERVED_JSON*/", json.dumps(preserved))
    html = html.replace("/*IDENTITY_JSON*/", json.dumps(identity_payload(frames_dir, manifest)))
    capture = f9_session.capture_identity(frames_dir)
    html = html.replace("/*CAPTURE_JSON*/", json.dumps(
        {"id": capture["id"], "manifest_sha256": capture["manifest_sha256"], "segments": capture["segments"]}))
    embedded = session_doc
    if embedded is None and (frames_dir / f9_session.SESSION_NAME).is_file():
        try:
            embedded = json.loads((frames_dir / f9_session.SESSION_NAME).read_text(encoding="utf-8"))
            f9_session.validate(embedded, frames_dir)
        except (ValueError, OSError) as exc:
            print(f"session.json not embedded: {exc}", file=sys.stderr)
            embedded = None
    html = html.replace("/*SESSION_JSON*/", json.dumps(embedded))
    return html


# ------------------------------------------------ preserved selection viewer
# FL-4873: a durable copy of a recording keeps full frames by hash only (no
# approved archive location exists for ~155 MB per recording). What makes the
# durable copy viewable is a lossless crop of the selected region, one cell of
# margin, for every referenced frame and the frame on each side of every
# referenced run, shown by this same viewer with its cell math offset to the
# crop origin.
PRESERVED_VIEWER_NAME = "viewer.html"
PRESERVED_CROPS_DIR = "crops"
CROP_MARGIN_CELLS = 1


def cell_rect(cx: int, cy: int, frame: dict) -> list:
    """Full-frame pixel rect [x0, y0, x1, y1) of a cell (mirror of JS cellRect)."""
    cols, rows = parse_matrix(frame.get("matrix", ""))
    w, h = (int(v) for v in frame["image"].split("x"))
    geometry = raster_geometry(frame)
    if geometry["mode"] == "raster":
        cw, ch = geometry["cell"]
        return [cx * cw, cy * ch, min(w, (cx + 1) * cw), min(h, (cy + 1) * ch)]
    return [cx * w // cols, cy * h // rows, min(w, -(-(cx + 1) * w // cols)), min(h, -(-(cy + 1) * h // rows))]


def selection_crop(frame: dict, cells, margin: int = CROP_MARGIN_CELLS) -> dict | None:
    """Pixel crop (x, y, w, h) and cell bbox covering the cells plus margin, or None."""
    cols, rows = parse_matrix(frame.get("matrix", ""))
    cells = [(int(x), int(y)) for x, y in cells]
    if not cells or cols <= 0 or rows <= 0 or any(not (0 <= x < cols and 0 <= y < rows) for x, y in cells):
        return None
    bx0 = max(0, min(x for x, _ in cells) - margin)
    by0 = max(0, min(y for _, y in cells) - margin)
    bx1 = min(cols - 1, max(x for x, _ in cells) + margin)
    by1 = min(rows - 1, max(y for _, y in cells) + margin)
    x0, y0 = cell_rect(bx0, by0, frame)[:2]
    x1, y1 = cell_rect(bx1, by1, frame)[2:]
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "cells": [bx0, by0, bx1, by1]}


def crop_seqs(all_seqs: list, referenced: list, current=None) -> list:
    """Referenced frames plus the recorded frame before and after every run."""
    order = sorted(set(int(s) for s in all_seqs))
    index = {s: i for i, s in enumerate(order)}
    wanted = [int(s) for s in referenced if int(s) in index]
    if not wanted and current is not None and int(current) in index:
        wanted = [int(current)]
    out = set(wanted)
    for s in wanted:
        i = index[s]
        if i > 0:
            out.add(order[i - 1])
        if i + 1 < len(order):
            out.add(order[i + 1])
    return sorted(out)


def _frame_image(frames_dir: Path, png_name: str):
    """Full frame as a PIL image: the PNG, or the recorder raw frame before
    the finalizer converted it. None when neither exists."""
    from PIL import Image

    path = frames_dir / png_name
    if path.is_file():
        image = Image.open(path)
        image.load()
        return image
    import f9_finalize_bundle

    raw = f9_finalize_bundle.raw_frame_path(frames_dir, png_name)
    if raw.is_file():
        width, height, rgba = f9_finalize_bundle.read_raw_frame(raw)
        return Image.frombytes("RGBA", (width, height), rgba)
    return None


def build_preserved_viewer(frames_dir: Path, session_path: Path | None, out_dir: Path, *,
                           full_frames_location: str, storage_status: str = "PENDING",
                           selection_input: dict | None = None) -> dict:
    """Write lossless selection crops and viewer.html for a durable copy.

    out_dir receives crops/frame_<seq>.png and viewer.html whose image paths
    are relative to out_dir. Returns the record the preservation manifest
    stores: every crop with its sha256 and full-frame rect, the frames that
    are absent and why, and the visible notice text. Raises ValueError when
    the session does not validate against the recording. selection_input
    (f9_session.read_selection) is the one read of the selection the caller
    already made; without it session_path is read once here.
    """
    import hashlib

    frames_dir = Path(frames_dir)
    if selection_input is None:
        selection_input = f9_session.read_selection(frames_dir, Path(session_path))
    session = selection_input["session"]
    session_doc = selection_input["doc"]
    frames = load_frames(frames_dir)
    by_seq = {int(f["seq"]): f for f in frames}
    seqs = crop_seqs(list(by_seq), session["referenced"], session.get("current"))
    crops_dir = Path(out_dir) / PRESERVED_CROPS_DIR
    crops_dir.mkdir(parents=True, exist_ok=True)
    rows, shown, absent = [], [], []
    for seq in seqs:
        frame = dict(by_seq[seq])
        if not re.fullmatch(r"\d+x\d+", str(frame.get("image", ""))):
            absent.append({"seq": seq, "reason": "frame_geometry_unrecorded"})
            continue
        crop = selection_crop(frame, session["cells"])
        if crop is None:
            absent.append({"seq": seq, "reason": "selection_outside_frame_grid"})
            continue
        image = _frame_image(frames_dir, frame["png"])
        if image is None:
            absent.append({"seq": seq, "reason": "full_frame_missing_at_preservation"})
            continue
        name = f"{PRESERVED_CROPS_DIR}/frame_{seq:05d}.png"
        box = (crop["x"], crop["y"], crop["x"] + crop["w"], crop["y"] + crop["h"])
        image.crop(box).save(Path(out_dir) / name, format="PNG")
        data = (Path(out_dir) / name).read_bytes()
        rows.append({"seq": seq, "file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                     "crop_px": [crop["x"], crop["y"], crop["w"], crop["h"]], "crop_cells": crop["cells"],
                     "full_png": frame["png"], "referenced": seq in session["referenced"]})
        frame["full_png"] = frame["png"]
        frame["png"] = name
        frame["crop"] = {k: crop[k] for k in ("x", "y", "w", "h")}
        shown.append(frame)
    bbox = [min(c[0] for c in session["cells"]), min(c[1] for c in session["cells"]),
            max(c[0] for c in session["cells"]), max(c[1] for c in session["cells"])] if session["cells"] else None
    notice = (
        "PRESERVED SELECTION VIEWER (durable copy). Present: lossless crops of cells "
        f"{bbox} plus a {CROP_MARGIN_CELLS}-cell margin for frames {[r['seq'] for r in rows]} "
        f"(referenced {session['referenced']} and the frame on each side of every referenced run). "
        f"Absent here: {absent or 'none'}. Every other frame and every pixel outside the crop is NOT in this "
        f"folder: full frames and recording.mov are stored by sha256 only at {full_frames_location} "
        f"(storage {storage_status}: no approved archive location). The full-frame viewer is NOT preserved."
    )
    record = {"entry": PRESERVED_VIEWER_NAME, "crops": rows, "absent": absent, "selection_bbox": bbox,
              "selection_sha256": selection_input["sha256"],
              "margin_cells": CROP_MARGIN_CELLS, "notice": notice,
              "full_frames": {"location": full_frames_location, "storage_status": storage_status,
                              "viewer_preserved": False}}
    if not shown:
        record["entry"] = None
        return record
    html = render_html(frames_dir, shown, all_seqs=sorted(by_seq),
                       preserved={"notice": notice, "built_at": str(session.get("saved_at") or "preserved")},
                       session_doc=session_doc)
    (Path(out_dir) / PRESERVED_VIEWER_NAME).write_text(html, encoding="utf-8")
    return record


if __name__ == "__main__":
    raise SystemExit(main())
