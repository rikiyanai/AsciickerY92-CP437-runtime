"""Build a single-file offline HTML verifier for 343 role-candidate XP layers.

Reads:
  - /Users/r/Desktop/bundle_layer_audit_20260520/full_source_layer_ledger_20260520.json
  - /Users/r/Desktop/bundle_layer_audit_20260520/png_layers/*_L[2-9]*.png
  - assets/sprites/<family>-<AHSW>.xp (for live glyph signature computation)

Writes:
  - /Users/r/Desktop/bundle_layer_audit_20260520/verify_layers.html

The HTML:
  - All 343 role-candidate L>=2 PNGs base64-embedded (single offline file, no network)
  - Per layer: AHSW tuple decoding, L0 metadata, glyph signature counts, prior verification
  - Educated-guess pre-fill from prior ledger labels
  - Editable typable NOTE field per layer
  - Status buttons: ACCEPT (educated guess is correct), REJECT (clear + use note), AMBIGUOUS
  - localStorage persistence keyed by xp_path+layer_index
  - JSON export button -> downloads verification_results.json
  - Filter buttons: all / unverified / accepted / rejected / ambiguous / family selector
  - Keyboard nav: J/K next/prev, A accept, R reject, U ambiguous, / focus search
"""
from __future__ import annotations

import base64
import contextlib
import html
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "pipeline"))
from xp_core import XPFile  # type: ignore
from bundle_wizard.semantic_dict import role_signature_summary, SWOOSH_FG_RGB  # type: ignore

AUDIT_DIR = Path("/Users/r/Desktop/bundle_layer_audit_20260520")
LEDGER_JSON = AUDIT_DIR / "full_source_layer_ledger_20260520.json"
PNG_DIR = AUDIT_DIR / "png_layers"
OUT_HTML = AUDIT_DIR / "verify_layers.html"
HINTS_JSON = AUDIT_DIR / "identity_hints.json"  # optional — written by compute_identity_hints.py

AHSW_NAMES = ("armor", "helmet", "shield", "weapon")


def parse_ahsw(xp_basename: str) -> tuple[str, str | None, list[str]]:
    """Extract (family, ahsw_digits, equipped_slots) from filename like 'wolfie-0110.xp'."""
    m = re.match(r"^([a-z]+)-(\d{4})\.xp$", xp_basename)
    if not m:
        # baseline like 'wolfie.xp', 'bigbee.xp', 'player-nude.xp'
        m2 = re.match(r"^([a-z]+(?:-[a-z]+)?)\.xp$", xp_basename)
        family = m2.group(1) if m2 else xp_basename
        return family, None, []
    family, ahsw = m.group(1), m.group(2)
    equipped = [AHSW_NAMES[i] for i, c in enumerate(ahsw) if c == "1"]
    return family, ahsw, equipped


def compute_glyph_signature(xp_path: Path, layer_index: int) -> dict:
    """Compute role-signature summary for one layer using semantic_dict rules."""
    if not xp_path.exists():
        return {"error": f"file not found: {xp_path}"}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            xp = XPFile(str(xp_path))
    except Exception as e:
        return {"error": f"XPFile load: {e}"}
    if layer_index >= len(xp.layers):
        return {"error": f"layer_index {layer_index} >= layer_count {len(xp.layers)}"}
    layer = xp.layers[layer_index]
    layer0 = xp.layers[0] if xp.layers else None

    # Per-cell L0 background-key transparency: cell visible iff its bg differs
    # from L0 bg at the same (x,y), per upstream sprite.cpp:541-622 contract.
    glyph_counts: Counter[int] = Counter()
    cyan_fg_cells = 0
    visible_cells = 0
    width = layer.width
    height = layer.height
    for x in range(width):
        for y in range(height):
            cell = layer.data[y][x] if hasattr(layer, "data") else None
            if cell is None:
                continue
            glyph, fg, bg = cell[0], cell[1], cell[2]
            # L0 bg-key transparency
            if layer0 is not None and x < layer0.width and y < layer0.height:
                l0_cell = layer0.data[y][x]
                l0_bg = l0_cell[2]
                if bg == l0_bg:
                    continue  # transparent at this cell
            visible_cells += 1
            glyph_counts[glyph] += 1
            if tuple(fg) == SWOOSH_FG_RGB:
                cyan_fg_cells += 1
    sig = role_signature_summary(dict(glyph_counts), cyan_fg_count=cyan_fg_cells)
    return {
        "visible_cells": visible_cells,
        "glyph_counts_top": glyph_counts.most_common(8),
        "role_signature": sig,
        "cyan_fg_cells": cyan_fg_cells,
    }


def educated_guess_from_signature(sig: dict, layer_index: int, layer_count: int) -> dict:
    """Apply role-class heuristics from semantic_dict.role_signature_summary docstring.

    Returns dict with `guess` (list of role labels) and `rationale` (string).
    """
    rs = sig.get("role_signature") or {}
    visible = sig.get("visible_cells", 0)
    is_final_layer = layer_index == layer_count - 1
    guesses: list[str] = []
    parts: list[str] = []

    if rs.get("swoosh_fg_cells", 0) > 0 and is_final_layer:
        guesses.append("weapon_swoosh")
        parts.append(f"cyan_fg={rs['swoosh_fg_cells']} on final layer -> swoosh per sprite.cpp")
    if rs.get("shield_boss", 0) >= 10:
        guesses.append("shield")
        parts.append(f"@ x{rs['shield_boss']} (shield boss)")
    if rs.get("armor_chainmail", 0) >= 30:
        guesses.append("armor")
        parts.append(f"0xb1 x{rs['armor_chainmail']} (chainmail dither)")
    if rs.get("crossbow_bolt", 0) > 0 or rs.get("crossbow_stock", 0) > 0:
        bolt = rs.get("crossbow_bolt", 0)
        stock = rs.get("crossbow_stock", 0)
        trig = rs.get("crossbow_trigger", 0)
        if bolt + stock + trig >= 4:
            guesses.append("weapon_crossbow")
            parts.append(f"BEL x{bolt} + hbar x{stock} + vbar x{trig} (crossbow parts)")
    if rs.get("bee_wing_left", 0) > 0 or rs.get("bee_wing_right", 0) > 0:
        guesses.append("mount_body_bee")
        parts.append(
            f"< x{rs.get('bee_wing_left',0)} > x{rs.get('bee_wing_right',0)} (bee wings)"
        )
    if rs.get("wolf_fur", 0) >= 20:
        guesses.append("mount_body_wolf")
        parts.append(f"0xb0 x{rs['wolf_fur']} (wolf fur)")
    if rs.get("slash_left", 0) >= 20 and rs.get("slash_right", 0) >= 20 and not guesses:
        # heavy backslash+slash with no other equipment glyphs => sword swing or sword
        guesses.append("weapon_sword?")
        parts.append(f"\\\\ x{rs['slash_left']} / x{rs['slash_right']}")

    # If nothing matched and we have visible cells, prefer to leave guess blank but record stats
    if not guesses and visible > 0:
        guesses.append("unknown")
        parts.append(f"no role-discriminating glyphs above threshold; visible={visible}")

    return {"guess": guesses, "rationale": "; ".join(parts) if parts else ""}


def b64_png(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def main() -> int:
    print("[1/4] reading ledger...", flush=True)
    with LEDGER_JSON.open() as f:
        data = json.load(f)
    rows = data["rows"]
    role_candidates = [r for r in rows if int(r["layer_index"]) >= 2]
    print(f"  {len(role_candidates)} role-candidate rows (L>=2)", flush=True)

    # Optional identity hints from prior verifications
    hints = {}
    if HINTS_JSON.exists():
        try:
            hints = json.loads(HINTS_JSON.read_text())
            print(f"  loaded {len(hints)} identity hints from {HINTS_JSON.name}", flush=True)
        except Exception as e:
            print(f"  hints file load error: {e}", flush=True)

    print("[2/4] computing glyph signatures + base64 PNGs...", flush=True)
    items: list[dict] = []
    for i, r in enumerate(role_candidates):
        xp_rel = r["source_xp_path"]  # e.g. "sprites/wolfie-0110.xp"
        xp_basename = Path(xp_rel).name
        xp_full = REPO_ROOT / "godot_project" / "assets" / xp_rel
        if not xp_full.exists():
            xp_full = REPO_ROOT / "assets" / xp_rel
        if not xp_full.exists():
            # try various asset roots
            for cand in [
                REPO_ROOT / xp_rel,
                REPO_ROOT / "godot_project" / xp_rel,
                REPO_ROOT / "assets" / xp_rel,
                REPO_ROOT / xp_rel.replace("sprites/", "godot_project/assets/sprites/"),
            ]:
                if cand.exists():
                    xp_full = cand
                    break
        family, ahsw, equipped = parse_ahsw(xp_basename)
        layer_index = int(r["layer_index"])
        sig = compute_glyph_signature(xp_full, layer_index)
        eg = educated_guess_from_signature(sig, layer_index, int(r["layer_count_in_xp"]))

        png_path = Path(r["layer_png"])
        if not png_path.exists():
            # try basename in PNG_DIR
            cand = PNG_DIR / f"{xp_basename.replace('.xp','')}_L{layer_index}.png"
            if cand.exists():
                png_path = cand
        png_b64 = b64_png(png_path) if png_path.exists() else ""

        review_state = r.get("full_ledger_review_state", "")
        prior_label = r.get("full_ledger_semantic_label", "") or ""
        user_label = r.get("user_visual_semantic_label", "") or ""
        prior_target = r.get("target_compiler_mapping", "") or ""
        # Build pre-fill educated guess: prefer user-verified, then phase3, then heuristic
        if user_label:
            pre_guess = user_label
            pre_source = "USER_VISUAL"
        elif prior_target:
            pre_guess = prior_target
            pre_source = "PHASE3_COMPILER"
        elif eg["guess"]:
            pre_guess = ";".join(eg["guess"])
            pre_source = "GLYPH_SIGNATURE"
        elif prior_label:
            pre_guess = prior_label
            pre_source = "LEDGER_HEURISTIC"
        else:
            pre_guess = ""
            pre_source = "NONE"

        # Identity hint overlay: if this layer is byte-identical to a user-verified
        # layer, override pre_guess + rationale so the verifier shows the user's
        # earlier note as the suggested guess. Does NOT change status (the layer
        # stays unverified; user still has to click YES/PARTIAL/NO/SKIP).
        # Format the layer_id the way compute_identity_hints.py does:
        family_part = family
        ahsw_part = ahsw if ahsw else "base"
        hint_layer_id = f"{family_part}-{ahsw_part}-L{layer_index}"
        hint = hints.get(hint_layer_id)
        if hint:
            pre_guess = hint["hint_guess"]
            pre_source = hint["hint_source"]
            # Replace the educated-guess rationale with the hint's own
            eg = {
                "guess": [hint["hint_guess"]],
                "rationale": hint["hint_rationale"] + (
                    " · suggested: " + hint["hint_suggested_action"]
                    if hint.get("hint_suggested_action") else ""
                ),
            }

        items.append({
            "id": f"{family}-{ahsw or 'base'}-L{layer_index}",
            "xp_path": r["xp_path"],
            "source_xp_path": xp_rel,
            "xp_basename": xp_basename,
            "family": family,
            "ahsw": ahsw,
            "equipped_slots": equipped,
            "layer_index": layer_index,
            "layer_count_in_xp": int(r["layer_count_in_xp"]),
            "layer_width_cells": r.get("layer_width_cells"),
            "layer_height_cells": r.get("layer_height_cells"),
            "engine_visible_cell_count": r.get("engine_visible_cell_count"),
            "angles": r.get("angles"),
            "anims": r.get("anims"),
            "frame_w": r.get("frame_w"),
            "frame_h": r.get("frame_h"),
            "l0_y_proj": r.get("l0_y_proj"),
            "l0_y_refl": r.get("l0_y_refl"),
            "l0_z_proj": r.get("l0_z_proj"),
            "l0_z_refl": r.get("l0_z_refl"),
            "has_cyan_fg": bool(r.get("has_cyan_fg_visible_cells")),
            "review_state": review_state,
            "authority_state": r.get("full_ledger_authority_state"),
            "confidence": r.get("full_ledger_confidence"),
            "compiler_status": r.get("compiler_status"),
            "prior_target_mapping": prior_target,
            "prior_user_label": user_label,
            "prior_user_glyph_ids": r.get("user_visual_glyph_identifiers", "") or "",
            "prior_ledger_label": prior_label,
            "glyph_signature": sig,
            "educated_guess": eg,
            "pre_guess": pre_guess,
            "pre_source": pre_source,
            "png_b64": png_b64,
        })
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(role_candidates)}", flush=True)

    print(f"[3/4] assembling HTML (items={len(items)})...", flush=True)
    items_json = json.dumps(items)

    # Pre-accept verified items so the user only needs to focus on unverified ones.
    pre_accept_ids = [
        it["id"] for it in items
        if it["pre_source"] in ("USER_VISUAL", "PHASE3_COMPILER")
    ]
    pre_accept_json = json.dumps(pre_accept_ids)

    html_doc = HTML_TEMPLATE.replace("__ITEMS_JSON__", items_json) \
                            .replace("__PRE_ACCEPT_JSON__", pre_accept_json)
    OUT_HTML.write_text(html_doc, encoding="utf-8")
    print(f"[4/4] wrote {OUT_HTML} ({len(html_doc):,} bytes, ~{len(html_doc)//1024//1024} MB)", flush=True)
    print(f"  Pre-accepted (PHASE3 + USER_VISUAL): {len(pre_accept_ids)} / {len(items)}")
    print(f"  open with: open {OUT_HTML}")
    return 0


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>FL-4078 Phase 0 — Tinder for XP Layers</title>
<style>
  :root {
    --bg: #0e1014;
    --fg: #e6e8eb;
    --muted: #8a90a0;
    --panel: #161922;
    --panel-2: #1c2030;
    --border: #2a2f3d;
    --accept: #2ecc71;
    --reject: #e74c3c;
    --ambig: #f39c12;
    --hi: #4fc3f7;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--fg); font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Helvetica, Arial, sans-serif; font-size: 14px; }
  body { display: flex; flex-direction: column; min-height: 100vh; }
  header { background: var(--panel); border-bottom: 1px solid var(--border); display: flex; align-items: center; padding: 10px 20px; gap: 16px; }
  header h1 { margin: 0; font-size: 14px; font-weight: 600; }
  header .stats { color: var(--muted); font-size: 13px; }
  header .stats b.accept { color: var(--accept); }
  header .stats b.reject { color: var(--reject); }
  header .stats b.ambig { color: var(--ambig); }
  header .progress-track { flex: 1; height: 6px; background: var(--panel-2); border-radius: 3px; overflow: hidden; max-width: 400px; }
  header .progress-track .bar { height: 100%; background: linear-gradient(90deg, var(--accept), var(--hi)); transition: width 0.2s; }
  header button { background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); padding: 6px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; }
  header button:hover { background: #252b3d; }
  main { flex: 1; display: flex; justify-content: center; padding: 24px 20px; overflow-y: auto; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 24px; max-width: 900px; width: 100%; box-shadow: 0 4px 24px rgba(0,0,0,0.3); }
  .card .file-row { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
  .card .filename { font-size: 18px; font-weight: 700; font-family: ui-monospace, "SF Mono", Menlo, monospace; }
  .card .position { font-size: 12px; color: var(--muted); }
  .card .status-badge { display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 700; margin-left: 8px; text-transform: uppercase; vertical-align: middle; }
  .card .status-badge.accept  { background: var(--accept); color: #062; }
  .card .status-badge.partial { background: #6c5ce7;       color: #fff; }
  .card .status-badge.reject  { background: var(--reject); color: #500; }
  .card .status-badge.ambig   { background: var(--ambig);  color: #420; }
  .card .status-badge.none    { background: var(--panel-2); color: var(--muted); border: 1px solid var(--border); }
  .ahsw { display: flex; gap: 6px; margin-bottom: 16px; flex-wrap: wrap; }
  .ahsw .slot { padding: 4px 10px; border-radius: 4px; font-size: 12px; font-family: ui-monospace, Menlo, monospace; background: var(--panel-2); color: var(--muted); border: 1px solid var(--border); }
  .ahsw .slot.on { background: var(--hi); color: #000; border-color: var(--hi); font-weight: 700; }
  .png-wrap { background: #ffffff; border: 1px solid var(--border); border-radius: 6px; padding: 12px; margin: 0 0 18px; text-align: center; overflow: auto; max-height: 50vh; }
  .png-wrap img { max-width: 100%; image-rendering: pixelated; image-rendering: crisp-edges; }
  .png-wrap.invert { background: #1a1d24; }
  .guess-block { background: #1a2330; border-left: 4px solid var(--hi); border-radius: 0 6px 6px 0; padding: 14px 18px; margin-bottom: 18px; }
  .guess-block .row { display: flex; align-items: baseline; gap: 12px; margin: 4px 0; }
  .guess-block .lab { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; min-width: 70px; }
  .guess-block .val { font-family: ui-monospace, Menlo, monospace; font-size: 18px; color: var(--hi); font-weight: 600; }
  .guess-block .why { font-family: ui-monospace, Menlo, monospace; font-size: 13px; color: #ccd; line-height: 1.5; }
  .guess-block .src { font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: var(--muted); }
  .actions { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 10px; margin: 18px 0 0; }
  .actions button { padding: 18px 0; border-radius: 8px; border: none; cursor: pointer; font-size: 15px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase; transition: transform 0.05s; }
  .actions button:hover { filter: brightness(1.08); transform: translateY(-1px); }
  .actions button:active { transform: translateY(0); }
  .actions .btn-yes     { background: var(--accept); color: #023; }
  .actions .btn-partial { background: #6c5ce7;       color: #fff; }
  .actions .btn-no      { background: var(--reject); color: #fee; }
  .actions .btn-skip    { background: var(--ambig);  color: #420; }
  .actions button .kbd { display: block; font-size: 10px; font-weight: 600; opacity: 0.75; margin-top: 2px; letter-spacing: 1px; }
  .correction-box { margin-top: 18px; background: #2a1820; border: 1px solid #6e2d3b; border-radius: 6px; padding: 14px 16px; display: none; }
  .correction-box.show { display: block; animation: flash 0.3s ease-out; }
  @keyframes flash { from { background: #5e1828; } to { background: #2a1820; } }
  .correction-box .lab { font-size: 11px; color: #ffabab; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 700; margin-bottom: 6px; }
  .correction-box textarea { width: 100%; min-height: 70px; background: #1a0e12; color: var(--fg); border: 1px solid #5e2d3b; border-radius: 4px; padding: 10px; font-family: ui-monospace, Menlo, monospace; font-size: 14px; resize: vertical; }
  .correction-box .save-row { display: flex; gap: 10px; margin-top: 10px; align-items: center; }
  .correction-box .save-row button { padding: 8px 16px; border-radius: 4px; border: none; cursor: pointer; font-size: 13px; font-weight: 700; background: var(--accept); color: #023; }
  .correction-box .save-row .hint { font-size: 11px; color: var(--muted); }
  .controls-row { display: flex; gap: 10px; margin-top: 18px; align-items: center; }
  .controls-row button { background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); padding: 7px 14px; border-radius: 4px; font-size: 12px; cursor: pointer; }
  .controls-row .spacer { flex: 1; }
  .controls-row select { background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); padding: 6px 8px; border-radius: 4px; font-size: 12px; }
  details { background: var(--panel-2); border-radius: 4px; padding: 4px 10px; margin-top: 12px; }
  details summary { cursor: pointer; font-size: 12px; color: var(--muted); padding: 6px 0; }
  details pre { background: #0a0c11; padding: 10px; border-radius: 3px; font-size: 11px; overflow-x: auto; margin: 4px 0 0; line-height: 1.4; }
  .kbd-hint { margin-top: 14px; font-size: 11px; color: var(--muted); text-align: center; padding-top: 10px; border-top: 1px solid var(--border); }
  .kbd-hint kbd { background: var(--panel-2); border: 1px solid var(--border); border-radius: 3px; padding: 1px 6px; font-family: ui-monospace, Menlo, monospace; font-size: 11px; margin: 0 2px; }
  .done-overlay { padding: 60px 20px; text-align: center; color: var(--muted); }
  .done-overlay h2 { font-size: 28px; color: var(--accept); margin: 0 0 12px; }
  /* Notes panel — uses a body wrapper with grid columns, no use of position-pinning */
  body { display: grid; grid-template-rows: auto 1fr; grid-template-columns: 1fr 0px; transition: grid-template-columns 0.2s ease; }
  body.notes-open { grid-template-columns: 1fr 360px; }
  body > header { grid-column: 1 / span 2; }
  body > main { grid-column: 1; grid-row: 2; }
  body > .notes-panel { grid-column: 2; grid-row: 2; background: var(--panel); border-left: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }
  body:not(.notes-open) > .notes-panel { display: none; }
  .notes-panel .panel-header { padding: 10px 14px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; background: var(--panel-2); }
  .notes-panel .panel-header h3 { margin: 0; font-size: 13px; font-weight: 700; }
  .notes-panel .panel-header .count { font-size: 11px; color: var(--muted); }
  .notes-panel .panel-header .spacer { flex: 1; }
  .notes-panel .panel-header button { background: transparent; color: var(--muted); border: 1px solid var(--border); padding: 3px 8px; border-radius: 3px; font-size: 11px; cursor: pointer; }
  .notes-panel .search { padding: 8px 12px; border-bottom: 1px solid var(--border); }
  .notes-panel .search input { width: 100%; background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); padding: 6px 8px; border-radius: 4px; font-size: 12px; }
  .notes-panel .list { flex: 1; overflow-y: auto; padding: 6px 0; }
  .notes-panel .note-item { padding: 10px 14px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background 0.1s; }
  .notes-panel .note-item:hover { background: #1e2230; }
  .notes-panel .note-item .row1 { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .notes-panel .note-item .freq { background: var(--hi); color: #000; font-weight: 700; padding: 1px 8px; border-radius: 10px; font-size: 11px; }
  .notes-panel .note-item .status-pill { padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; text-transform: uppercase; }
  .notes-panel .note-item .status-pill.reject { background: var(--reject); color: #500; }
  .notes-panel .note-item .status-pill.partial { background: #6c5ce7; color: #fff; }
  .notes-panel .note-item .status-pill.accept { background: var(--accept); color: #062; }
  .notes-panel .note-item .panel-actions { margin-left: auto; display: flex; gap: 4px; }
  .notes-panel .note-item .panel-actions button { background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); padding: 2px 8px; border-radius: 3px; font-size: 10px; cursor: pointer; }
  .notes-panel .note-item .panel-actions button:hover { background: var(--hi); color: #000; }
  .notes-panel .note-item .text { font-size: 12px; line-height: 1.4; color: var(--fg); white-space: pre-wrap; word-break: break-word; max-height: 70px; overflow: hidden; }
  .notes-panel .note-item.expanded .text { max-height: none; }
  .notes-panel .note-item .layers { font-size: 10px; color: var(--muted); margin-top: 4px; font-family: ui-monospace, Menlo, monospace; }
</style>
</head>
<body>
<header>
  <h1>FL-4078 Phase 0 — Tinder for XP Layers</h1>
  <span class="stats" id="stats">…</span>
  <div class="progress-track"><div class="bar" id="bar"></div></div>
  <select id="filter">
    <option value="unverified" selected>show: unverified</option>
    <option value="all">show: all</option>
    <option value="accept">show: yes</option>
    <option value="partial">show: partial</option>
    <option value="reject">show: no</option>
    <option value="ambig">show: skipped</option>
  </select>
  <button id="btn-invert" title="Toggle PNG background">Invert BG</button>
  <button id="btn-notes" title="Toggle notes panel (T)">Notes</button>
  <button id="btn-backup" title="Quick raw-state backup download">Backup raw</button>
  <button id="btn-export">Export JSON</button>
  <button id="btn-clear-all" title="Wipe all local state">Clear All</button>
</header>
<main id="main"></main>
<aside class="notes-panel">
  <div class="panel-header">
    <h3>Previous notes</h3>
    <span class="count" id="notes-count"></span>
    <span class="spacer"></span>
    <button id="btn-notes-close">×</button>
  </div>
  <div class="search">
    <input id="notes-search" type="text" placeholder="filter notes (substring)…" />
  </div>
  <div class="list" id="notes-list"></div>
</aside>

<script>
// ---------------------------------------------------------------------------
// Declarations (hoisted; must precede saveState() seeding call below)
// ---------------------------------------------------------------------------
const ITEMS = __ITEMS_JSON__;
const PRE_ACCEPT_IDS = __PRE_ACCEPT_JSON__;
const STORAGE_KEY = "fl4078_phase0_verifier_v2";
const SEED_KEY = STORAGE_KEY + "_seeded_v1";

let state = (() => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) {}
  return {};
})();
let currentId = null;
let activeFilter = "unverified";
let invertBG = false;
// Browser-history-style navigation: navHistory is ordered list of ids visited,
// navPos is current position within it. Prev moves -1, advance(+1) appends.
const NAV_KEY = STORAGE_KEY + "_nav";
let navHistory = [];
let navPos = -1;
try {
  const rawNav = localStorage.getItem(NAV_KEY);
  if (rawNav) {
    const obj = JSON.parse(rawNav);
    if (Array.isArray(obj.history)) navHistory = obj.history;
    if (typeof obj.pos === "number") navPos = obj.pos;
  }
} catch (e) {}

function statusOf(id) { return (state[id] && state[id].status) || null; }

function counts() {
  let a = 0, p = 0, r = 0, m = 0, u = 0;
  for (const it of ITEMS) {
    const s = statusOf(it.id);
    if (s === "accept") a++;
    else if (s === "partial") p++;
    else if (s === "reject") r++;
    else if (s === "ambig") m++;
    else u++;
  }
  return { a, p, r, m, u, total: ITEMS.length };
}

function renderStats() {
  const c = counts();
  const verified = c.a + c.p + c.r + c.m;
  const pct = (verified / c.total * 100).toFixed(1);
  document.getElementById("stats").innerHTML =
    `<b class="accept">${c.a} yes</b> · ` +
    `<b style="color:#9b8aff">${c.p} partial</b> · ` +
    `<b class="reject">${c.r} no</b> · ` +
    `<b class="ambig">${c.m} skip</b> · ${c.u} left · ${pct}%`;
  document.getElementById("bar").style.width = pct + "%";
}

function matchesFilter(it) {
  const s = statusOf(it.id);
  if (activeFilter === "all") return true;
  if (activeFilter === "unverified") return !s;
  if (activeFilter === "accept") return s === "accept";
  if (activeFilter === "partial") return s === "partial";
  if (activeFilter === "reject") return s === "reject";
  if (activeFilter === "ambig") return s === "ambig";
  return true;
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  localStorage.setItem(NAV_KEY, JSON.stringify({ history: navHistory, pos: navPos }));
}

function navGoTo(id) {
  if (!id) return;
  currentId = id;
  // Truncate forward history then append
  navHistory = navHistory.slice(0, navPos + 1);
  if (navHistory[navHistory.length - 1] !== id) {
    navHistory.push(id);
  }
  navPos = navHistory.length - 1;
  saveState();
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function pickNext() {
  const pool = ITEMS.filter(matchesFilter);
  if (pool.length === 0) return null;
  // Continue from currentId if still in pool, else start at 0
  let idx = pool.findIndex(x => x.id === currentId);
  if (idx === -1) idx = 0;
  return pool[idx];
}

function advance(direction) {
  if (direction < 0) {
    // Prev: step back through navigation history (only items you actually visited).
    // This never jumps to seeded/auto-accepted layers you never saw.
    if (navPos > 0) {
      navPos--;
      currentId = navHistory[navPos];
      saveState();
    }
    renderCurrent();
    return;
  }
  if (direction > 0) {
    // Try forward in history first (if user previously pressed Prev and now wants Next back)
    if (navPos < navHistory.length - 1) {
      navPos++;
      currentId = navHistory[navPos];
      saveState();
      renderCurrent();
      return;
    }
    // Else find next item matching the current filter and push to history
    const pool = ITEMS.filter(matchesFilter);
    if (pool.length === 0) { renderCurrent(); return; }
    let idx = pool.findIndex(x => x.id === currentId);
    const next = (idx === -1) ? pool[0] : pool[(idx + 1) % pool.length];
    navGoTo(next.id);
    renderCurrent();
    return;
  }
}

function renderCurrent() {
  const main = document.getElementById("main");
  const it = ITEMS.find(x => x.id === currentId);
  if (!it) {
    main.innerHTML = `<div class="done-overlay"><h2>All done in this filter!</h2><p>Change the filter to review more, or hit Export JSON to download results.</p></div>`;
    return;
  }
  const s = state[it.id] || {};
  const status = s.status || "none";
  const note = s.note || "";

  const ahswHTML = it.ahsw
    ? `<div class="ahsw">
        <span class="slot ${it.ahsw[0]==='1'?'on':''}">A=${it.ahsw[0]} armor</span>
        <span class="slot ${it.ahsw[1]==='1'?'on':''}">H=${it.ahsw[1]} helmet</span>
        <span class="slot ${it.ahsw[2]==='1'?'on':''}">S=${it.ahsw[2]} shield</span>
        <span class="slot ${it.ahsw[3]==='1'?'on':''}">W=${it.ahsw[3]} weapon</span>
        <span class="slot">L${it.layer_index} of ${it.layer_count_in_xp}</span>
        ${it.has_cyan_fg ? '<span class="slot" style="background:#055;color:#fff;border-color:#077">cyan-fg (swoosh-candidate)</span>' : ''}
       </div>`
    : `<div class="ahsw"><span class="slot">baseline (no AHSW)</span><span class="slot">L${it.layer_index} of ${it.layer_count_in_xp}</span></div>`;

  const why = (it.educated_guess && it.educated_guess.rationale) || "";
  const userPrior = it.prior_user_glyph_ids || "";

  const pool = ITEMS.filter(matchesFilter);
  const posIdx = pool.findIndex(x => x.id === currentId);

  const badgeText = status === "accept" ? "YES" : status === "partial" ? "PARTIAL" : status === "reject" ? "NO" : status === "ambig" ? "SKIPPED" : "";
  const badgeHTML = status === "none" ? "" : `<span class="status-badge ${status}">${badgeText}</span>`;
  const needsNote = (status === "reject" || status === "partial");
  const noteLabel = status === "partial"
    ? "Partial — what ELSE is in this layer beyond the guess? (e.g. \"+ player body context\")"
    : status === "reject"
      ? "Correction — what role is this layer actually?"
      : "Add a note (only used for NO / PARTIAL)";

  // Glyph signature pretty
  const sig = it.glyph_signature && it.glyph_signature.role_signature || {};
  const sigPretty = Object.keys(sig).length === 0 ? "(no role-discriminating glyphs)" :
    Object.entries(sig).sort((a,b)=>b[1]-a[1]).map(([k,v]) => `${k}: ${v}`).join("  |  ");

  // top glyph counts (codepoint -> count)
  const topGlyphs = (it.glyph_signature && it.glyph_signature.glyph_counts_top) || [];
  const topGlyphsHTML = topGlyphs.length === 0 ? "(no visible cells)" :
    topGlyphs.map(([g, n]) => {
      const ch = (g >= 32 && g <= 126) ? String.fromCharCode(g) : `0x${g.toString(16).padStart(2,'0')}`;
      return `${ch}×${n}`;
    }).join("  ");

  main.innerHTML = `
    <div class="card">
      <div class="file-row">
        <div class="filename">${escapeHtml(it.xp_basename)}:L${it.layer_index}${badgeHTML}</div>
        <div class="position">${posIdx + 1} / ${pool.length} in filter · ${counts().u} unverified total</div>
      </div>
      ${ahswHTML}

      <div class="png-wrap ${invertBG ? 'invert' : ''}">
        ${it.png_b64 ? `<img src="data:image/png;base64,${it.png_b64}" alt="L${it.layer_index}">` : '<i>(no PNG)</i>'}
      </div>

      <div class="guess-block">
        <div class="row"><div class="lab">GUESS</div><div class="val">${escapeHtml(it.pre_guess) || "(no guess)"}</div></div>
        ${why ? `<div class="row"><div class="lab">WHY</div><div class="why">${escapeHtml(why)}</div></div>` : ""}
        ${userPrior ? `<div class="row"><div class="lab">PRIOR</div><div class="why">${escapeHtml(userPrior)}</div></div>` : ""}
        <div class="row"><div class="lab">GLYPHS</div><div class="why">${escapeHtml(topGlyphsHTML)}</div></div>
        <div class="row"><div class="lab">SOURCE</div><div class="src">${escapeHtml(it.pre_source)}</div></div>
      </div>

      <div class="actions">
        <button class="btn-yes"     id="btn-yes">YES<span class="kbd">(Y)</span></button>
        <button class="btn-partial" id="btn-partial">PARTIAL<span class="kbd">(P)</span></button>
        <button class="btn-no"      id="btn-no">NO<span class="kbd">(N)</span></button>
        <button class="btn-skip"    id="btn-skip">SKIP<span class="kbd">(S)</span></button>
      </div>

      <div class="correction-box ${needsNote ? "show" : ""}" id="correction-box">
        <div class="lab">${escapeHtml(noteLabel)}</div>
        <textarea id="ta-note" placeholder="${status === "partial" ? "e.g. '+ player body context' or '+ helmet point at top'" : "e.g. 'shield (boss + brown context)' or 'composite: armor+helmet' or 'unknown — looks like body fragment'"}">${escapeHtml(note)}</textarea>
        <div class="save-row">
          <button id="btn-save-next">Save &amp; Next →</button>
          <span class="hint">Saves note + keeps ${status === "partial" ? "PARTIAL" : "NO"} status + auto-advances.</span>
        </div>
      </div>

      <div class="controls-row">
        <button id="btn-prev">← Prev</button>
        <button id="btn-undo">Undo (clear this layer)</button>
        <span class="spacer"></span>
        <span style="color:var(--muted);font-size:11px">jump:</span>
        <button id="btn-jump-first-unverified">first unverified</button>
      </div>

      <details>
        <summary>Power-user: full glyph signature + prior ledger labels</summary>
        <pre>role_signature: ${escapeHtml(sigPretty)}

prior_review_state:   ${escapeHtml(it.review_state)}
prior_authority:      ${escapeHtml(it.authority_state)}
prior_target_mapping: ${escapeHtml(it.prior_target_mapping || "(none)")}
prior_user_label:     ${escapeHtml(it.prior_user_label || "(none)")}
prior_ledger_label:   ${escapeHtml(it.prior_ledger_label || "(none)")}

full_sig_json: ${escapeHtml(JSON.stringify(it.glyph_signature))}</pre>
      </details>

      <div class="kbd-hint">
        <kbd>Y</kbd> yes · <kbd>P</kbd> partial · <kbd>N</kbd> no · <kbd>S</kbd> skip · <kbd>←</kbd>/<kbd>Backspace</kbd> prev (history only) · <kbd>→</kbd> next · <kbd>Enter</kbd> save+next in note · <kbd>T</kbd> toggle notes panel · <kbd>I</kbd> invert bg · <kbd>U</kbd> undo
        <br>history pos: ${navPos + 1} / ${navHistory.length} · reviewing L${it.layer_index} of ${it.layer_count_in_xp} in <b>${escapeHtml(it.xp_basename)}</b>${status !== "none" ? ' · already answered: <b style="color:var(--hi)">'+status.toUpperCase()+'</b>' : ''}
      </div>
    </div>
  `;

  document.getElementById("btn-yes").onclick = () => setYes();
  document.getElementById("btn-partial").onclick = () => setPartial();
  document.getElementById("btn-no").onclick = () => setNo();
  document.getElementById("btn-skip").onclick = () => setSkip();
  document.getElementById("btn-prev").onclick = () => advance(-1);
  document.getElementById("btn-undo").onclick = () => undoCurrent();
  document.getElementById("btn-jump-first-unverified").onclick = () => jumpFirstUnverified();
  document.getElementById("btn-save-next").onclick = () => saveAndNext();
  const ta = document.getElementById("ta-note");
  ta.addEventListener("input", () => {
    state[it.id] = state[it.id] || {};
    state[it.id].note = ta.value;
    state[it.id].ts = new Date().toISOString();
    saveState();
  });
  ta.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" && (e.metaKey || e.ctrlKey)) || (e.key === "Enter" && !e.shiftKey)) {
      e.preventDefault();
      saveAndNext();
    }
  });
  renderStats();
}

function setYes() {
  const it = ITEMS.find(x => x.id === currentId);
  if (!it) return;
  state[it.id] = state[it.id] || {};
  state[it.id].status = "accept";
  state[it.id].corrected_label = it.pre_guess;
  state[it.id].pre_source = it.pre_source;
  state[it.id].pre_guess = it.pre_guess;
  state[it.id].ts = new Date().toISOString();
  saveState();
  advance(+1);
}

function setNo() {
  const it = ITEMS.find(x => x.id === currentId);
  if (!it) return;
  state[it.id] = state[it.id] || {};
  state[it.id].status = "reject";
  state[it.id].pre_source = it.pre_source;
  state[it.id].pre_guess = it.pre_guess;
  state[it.id].ts = new Date().toISOString();
  saveState();
  renderStats();
  renderCurrent();
  setTimeout(() => {
    const ta = document.getElementById("ta-note");
    if (ta) ta.focus();
    const box = document.getElementById("correction-box");
    if (box) box.classList.add("show");
  }, 50);
}

function setPartial() {
  const it = ITEMS.find(x => x.id === currentId);
  if (!it) return;
  state[it.id] = state[it.id] || {};
  state[it.id].status = "partial";
  state[it.id].pre_source = it.pre_source;
  state[it.id].pre_guess = it.pre_guess;
  state[it.id].ts = new Date().toISOString();
  saveState();
  renderStats();
  renderCurrent();
  setTimeout(() => {
    const ta = document.getElementById("ta-note");
    if (ta) ta.focus();
    const box = document.getElementById("correction-box");
    if (box) box.classList.add("show");
  }, 50);
}

function setSkip() {
  const it = ITEMS.find(x => x.id === currentId);
  if (!it) return;
  state[it.id] = state[it.id] || {};
  state[it.id].status = "ambig";
  state[it.id].pre_source = it.pre_source;
  state[it.id].pre_guess = it.pre_guess;
  state[it.id].ts = new Date().toISOString();
  saveState();
  advance(+1);
}

function saveAndNext() {
  const it = ITEMS.find(x => x.id === currentId);
  if (!it) return;
  const ta = document.getElementById("ta-note");
  state[it.id] = state[it.id] || {};
  // Preserve whichever status was already set (NO or PARTIAL); default to NO if none.
  if (state[it.id].status !== "partial" && state[it.id].status !== "reject") {
    state[it.id].status = "reject";
  }
  state[it.id].note = ta ? ta.value : "";
  state[it.id].corrected_label = ta ? ta.value : "";
  state[it.id].pre_source = it.pre_source;
  state[it.id].pre_guess = it.pre_guess;
  state[it.id].ts = new Date().toISOString();
  saveState();
  advance(+1);
}

function undoCurrent() {
  const it = ITEMS.find(x => x.id === currentId);
  if (!it) return;
  delete state[it.id];
  saveState();
  renderCurrent();
}

function jumpFirstUnverified() {
  const it = ITEMS.find(it => !statusOf(it.id));
  if (it) { navGoTo(it.id); renderCurrent(); }
}

// ---------------------------------------------------------------------------
// Wire-up
// ---------------------------------------------------------------------------
document.getElementById("filter").onchange = (e) => {
  activeFilter = e.target.value;
  const pool = ITEMS.filter(matchesFilter);
  if (!pool.find(x => x.id === currentId)) {
    if (pool.length) navGoTo(pool[0].id); else { currentId = null; saveState(); }
  }
  renderCurrent();
};

document.getElementById("btn-invert").onclick = () => {
  invertBG = !invertBG;
  renderCurrent();
};

document.getElementById("btn-export").onclick = () => {
  const out = {
    schema: "fl4078_phase0_verification.v3_tinder_partial",
    created: new Date().toISOString(),
    total_items: ITEMS.length,
    counts: counts(),
    items: ITEMS.map(it => ({
      id: it.id,
      xp_basename: it.xp_basename,
      source_xp_path: it.source_xp_path,
      layer_index: it.layer_index,
      family: it.family,
      ahsw: it.ahsw,
      pre_source: it.pre_source,
      pre_guess: it.pre_guess,
      glyph_signature: it.glyph_signature,
      verification: state[it.id] || null,
    })),
  };
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  const url = URL.createObjectURL(blob);
  a.href = url;
  a.download = "fl4078_phase0_verification.json";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 100);
};

document.getElementById("btn-backup").onclick = () => {
  // Just dump the raw state object so it can be restored with simple JSON.parse.
  const blob = new Blob([JSON.stringify(state, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  const url = URL.createObjectURL(blob);
  a.href = url;
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  a.download = `fl4078_phase0_state_${ts}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 100);
};

document.getElementById("btn-clear-all").onclick = () => {
  if (!confirm("Wipe ALL local verification state? (Export first if you want to keep it.)")) return;
  state = {};
  navHistory = [];
  navPos = -1;
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(NAV_KEY);
  localStorage.removeItem(SEED_KEY);
  location.reload();
};

// ---------------------------------------------------------------------------
// Notes panel
// ---------------------------------------------------------------------------
let notesSearchStr = "";
function renderNotesPanel() {
  // Aggregate: note text -> { count, status_counts: {accept,reject,partial,ambig}, layer_ids: [] }
  const agg = new Map();
  for (const id of Object.keys(state)) {
    const v = state[id];
    const note = (v && v.note ? String(v.note).trim() : "");
    if (!note) continue;
    if (!agg.has(note)) agg.set(note, { count: 0, statuses: {}, layer_ids: [] });
    const a = agg.get(note);
    a.count++;
    a.statuses[v.status || "(none)"] = (a.statuses[v.status || "(none)"] || 0) + 1;
    a.layer_ids.push(id);
  }
  // Sort by count desc, then alpha
  let entries = Array.from(agg.entries()).sort((x, y) => y[1].count - x[1].count || x[0].localeCompare(y[0]));
  // Filter
  const q = notesSearchStr.toLowerCase().trim();
  if (q) entries = entries.filter(([note]) => note.toLowerCase().includes(q));
  // Render
  document.getElementById("notes-count").textContent = `${entries.length} unique${q ? ` (filtered)` : ""}`;
  const list = document.getElementById("notes-list");
  if (entries.length === 0) {
    list.innerHTML = `<div style="padding:18px;color:var(--muted);text-align:center;font-size:12px">${q ? "no notes match filter" : "no notes yet — type a NO or PARTIAL correction to start"}</div>`;
    return;
  }
  list.innerHTML = entries.map(([note, info]) => {
    const dominantStatus = Object.entries(info.statuses).sort((a, b) => b[1] - a[1])[0][0];
    const layerSample = info.layer_ids.slice(0, 3).join(", ") + (info.layer_ids.length > 3 ? ` +${info.layer_ids.length - 3} more` : "");
    return `<div class="note-item" data-note="${escapeAttr(note)}">
      <div class="row1">
        <span class="freq">${info.count}×</span>
        <span class="status-pill ${dominantStatus}">${dominantStatus}</span>
        <div class="panel-actions">
          <button data-act="use" title="Insert into current note textarea">USE</button>
          <button data-act="copy" title="Copy to clipboard">COPY</button>
        </div>
      </div>
      <div class="text">${escapeHtml(note)}</div>
      <div class="layers">${escapeHtml(layerSample)}</div>
    </div>`;
  }).join("");
  // Wire up actions
  list.querySelectorAll(".note-item").forEach(el => {
    const note = el.dataset.note;
    el.querySelector('[data-act="copy"]').onclick = (e) => {
      e.stopPropagation();
      navigator.clipboard.writeText(note).then(() => {
        const btn = e.currentTarget;
        const orig = btn.textContent;
        btn.textContent = "COPIED";
        setTimeout(() => btn.textContent = orig, 800);
      });
    };
    el.querySelector('[data-act="use"]').onclick = (e) => {
      e.stopPropagation();
      const ta = document.getElementById("ta-note");
      if (ta) {
        ta.value = note;
        ta.dispatchEvent(new Event("input", { bubbles: true }));
        ta.focus();
        // also auto-show correction box (in case current status was YES/SKIP)
        const box = document.getElementById("correction-box");
        if (box) box.classList.add("show");
      } else {
        // No textarea yet — store for next render and trigger NO
        navigator.clipboard.writeText(note);
      }
    };
    el.onclick = () => el.classList.toggle("expanded");
  });
}

document.getElementById("btn-notes").onclick = () => {
  document.body.classList.toggle("notes-open");
  if (document.body.classList.contains("notes-open")) renderNotesPanel();
};
document.getElementById("btn-notes-close").onclick = () => {
  document.body.classList.remove("notes-open");
};
document.getElementById("notes-search").addEventListener("input", (e) => {
  notesSearchStr = e.target.value;
  renderNotesPanel();
});
// Auto-refresh panel when state changes (overlay saveState to also refresh panel if open)
const _origSaveState = saveState;
saveState = function() {
  _origSaveState();
  if (document.body.classList.contains("notes-open")) renderNotesPanel();
};

function escapeAttr(s) {
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/'/g,'&#39;');
}

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.key === "y" || e.key === "Y") { e.preventDefault(); setYes(); }
  else if (e.key === "p" || e.key === "P") { e.preventDefault(); setPartial(); }
  else if (e.key === "n" || e.key === "N") { e.preventDefault(); setNo(); }
  else if (e.key === "s" || e.key === "S") { e.preventDefault(); setSkip(); }
  else if (e.key === "u" || e.key === "U") { e.preventDefault(); undoCurrent(); }
  else if (e.key === "i" || e.key === "I") { e.preventDefault(); invertBG = !invertBG; renderCurrent(); }
  else if (e.key === "t" || e.key === "T") { e.preventDefault(); document.getElementById("btn-notes").click(); }
  else if (e.key === "ArrowLeft" || e.key === "Backspace") { e.preventDefault(); advance(-1); }
  else if (e.key === "ArrowRight") { e.preventDefault(); advance(+1); }
});

// ---------------------------------------------------------------------------
// One-time seed PHASE3 + USER_VISUAL prior verifications as YES
// ---------------------------------------------------------------------------
if (!localStorage.getItem(SEED_KEY)) {
  for (const id of PRE_ACCEPT_IDS) {
    if (!state[id]) {
      const it = ITEMS.find(x => x.id === id);
      state[id] = {
        status: "accept",
        corrected_label: it ? it.pre_guess : "",
        note: "",
        pre_source: it ? it.pre_source : "",
        pre_guess: it ? it.pre_guess : "",
        ts: new Date().toISOString(),
      };
    }
  }
  saveState();
  localStorage.setItem(SEED_KEY, "1");
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
{
  // Restore last position from history if valid; else start at first unverified
  let bootId = null;
  if (navPos >= 0 && navPos < navHistory.length) {
    const stored = navHistory[navPos];
    if (ITEMS.find(x => x.id === stored)) bootId = stored;
  }
  if (!bootId) {
    const pool = ITEMS.filter(matchesFilter);
    bootId = pool.length ? pool[0].id : null;
    if (bootId) navGoTo(bootId);
  } else {
    currentId = bootId;
  }
  renderCurrent();
  renderStats();
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
