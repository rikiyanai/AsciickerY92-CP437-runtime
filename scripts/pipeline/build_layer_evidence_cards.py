#!/usr/bin/env python3
"""FL-4306 / FL-4162 — layer evidence card emitter.

Turns every immutable hand-labeled row in state_FINAL into a SOURCE-BACKED
evidence card, one per (source_xp_path + raw_layer_index). The output
`layer_evidence_cards.jsonl` is the bridge from normalization to the bundle
refactor: it is what a human reviews (rejects-first) through the XP Body
Viewer, before any family topology contract or ActorVisualProfile row is
authored.

AUTHORITY DISCIPLINE (do not regress — see
docs/research/ascii/semantic_maps/upstream_sprite_layer_conventions.json):
  1. state_FINAL stays IMMUTABLE evidence (sha256-pinned; this tool only reads).
  2. These cards are a PROPOSAL / contradiction surface, never authority.
  3. Glyph similarity CLUSTERS evidence; it does NOT define a role.
  4. AHSW is tuple identity + equipment presence, NOT raw-layer role authority.
  5. Raw layer role must be proven per (source_xp_path + layer_index) by a human
     inspecting cells. This tool deliberately emits NO machine label / no
     recommendation / no confidence-as-semantic. accept/partial/reject is
     guesser-vs-handcorpus AGREEMENT, not semantic confidence, and rejects are
     the HIGHEST-value rows (where automation failed and a human supplied role
     semantics by eye).

This replaces the label-authoring path in scripts/pipeline/xp_labeling_tool.py
(uncommitted), whose _convention_candidates/recommended_label/confidence
re-derived roles from note prose — the exact glyph-classifier-from-cooccurrence
trap this lane exists to avoid. Mechanical helpers (visible-cell extraction,
fingerprinting, cell-delta) are reimplemented here against the VIEWER's parser
and transparency rule so the card's visible cells match the microscope.

Usage:
    python3 scripts/pipeline/build_layer_evidence_cards.py            # emit cards
    python3 scripts/pipeline/build_layer_evidence_cards.py --check    # dry-run, print summary only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
# The VIEWER's parser is the canonical one — use it so visible cells match what
# the XP Body Viewer (the human review microscope) actually shows.
VIEWER_SCRIPTS = REPO_ROOT / "pipeline-v3" / "scripts"
if str(VIEWER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(VIEWER_SCRIPTS))

from xp_core import XPFile  # noqa: E402  (path injected above)

DEFAULT_STATE_FINAL = Path(
    "/Users/r/Desktop/bundle_layer_audit_20260520/verifier_state_backups/state_FINAL_20260521-163326.json"
)
SOURCE_FINAL_SHA256 = "ecc9a16112ce48beaeb0e24beba2ccc7399c4efc50d32505f3fd54f8e8d76020"
SPRITES_DIR = REPO_ROOT / "assets" / "sprites"
SEMANTIC_MAPS_DIR = REPO_ROOT / "docs" / "research" / "ascii" / "semantic_maps"
CONVENTIONS_JSON = SEMANTIC_MAPS_DIR / "upstream_sprite_layer_conventions.json"
CARDS_OUT = SEMANTIC_MAPS_DIR / "layer_evidence_cards.jsonl"
MANIFEST_OUT = SEMANTIC_MAPS_DIR / "layer_evidence_cards.manifest.json"

KEY_RE = re.compile(r"^(?P<xp_stem>.+)-L(?P<layer>\d+)$")
STEM_RE = re.compile(r"^(?P<family>[a-z]+)-(?P<digits>[0-9a-fA-F]{4})$")
MOUNT_FAMILIES = {"bigbee", "wolfie", "wolack"}
WEAPON_NAMES = {0: "none", 1: "sword", 2: "crossbow"}
# pre_guess values that carry NO information (a reject here is not a "wrong guess",
# just an absent one).
NONINFORMATIVE_GUESS = {"", "unknown", "none", "empty", "null", "n/a", "na"}
MAGENTA = (255, 0, 255)  # viewer transparency sentinel (xp_uv_body_viewer.py:1691)
CYAN = (0, 255, 255)     # swoosh foreground key (sprite.cpp:361,384-526)
NEAR_THRESHOLD = 3
CELL_STORE_CAP = 600     # bounded per-card frame-0 cell store (avoids 40MB bloat)

LINEAGE_FL = ["FL-4306", "FL-4162", "FL-2345", "FL-903", "FL-869", "FL-813", "FL-2897"]


# --------------------------------------------------------------------------- #
# key / stem parsing
# --------------------------------------------------------------------------- #
def parse_key(key: str) -> tuple[str, int]:
    m = KEY_RE.match(key)
    if not m:
        raise ValueError(f"invalid layer key: {key}")
    return m.group("xp_stem"), int(m.group("layer"))


def decode_ahsw(xp_stem: str) -> dict[str, Any] | None:
    """AHSW is filename-only tuple identity (game.cpp:2978-3016), NOT layer role."""
    m = STEM_RE.match(xp_stem)
    if not m:
        return None
    digits = m.group("digits")
    a, h, s, w = (int(d, 16) for d in digits)
    return {
        "raw": digits,
        "armor": a,
        "helmet": h,
        "shield": s,
        "weapon": w,
        "weapon_name": WEAPON_NAMES.get(w, f"w{w}"),
        "note": "filename-only tuple identity (game.cpp:2978-3016); sprite.cpp never parses the name. NOT raw-layer role authority.",
    }


def family_of(xp_stem: str) -> str:
    return xp_stem.split("-", 1)[0]


def resolve_xp_path(xp_stem: str, sprites_dir: Path = SPRITES_DIR) -> tuple[Path, str]:
    """Resolve a corpus stem to its on-disk .xp source.

    Most stems map directly to ``{stem}.xp``. The non-AHSW monolith bases are
    recorded in the corpus as ``<prefix>-base`` but live on disk as the bare
    monolith ``<prefix>.xp`` (e.g. ``bigbee-base`` -> ``bigbee.xp``,
    ``player-nude-base`` -> ``player-nude.xp``). Those monoliths load via a
    separate engine path (game.cpp:2964,2967-2970). Resolving them here is the
    correct source mapping (FL-4162); marking them ``missing_xp`` would be FALSE
    null evidence. Returns ``(path, resolution_kind)`` where kind is
    ``direct`` | ``monolith_base`` | ``missing`` (path is the direct candidate
    when missing, for an honest error message).
    """
    direct = sprites_dir / f"{xp_stem}.xp"
    if direct.is_file():
        return direct, "direct"
    if xp_stem.endswith("-base"):
        monolith = sprites_dir / f"{xp_stem[:-len('-base')]}.xp"
        if monolith.is_file():
            return monolith, "monolith_base"
    return direct, "missing"


# --------------------------------------------------------------------------- #
# visible-cell extraction (VIEWER rule: visible iff bg != magenta and glyph != 0)
# --------------------------------------------------------------------------- #
def frame0_rect(width: int, height: int, meta: dict[str, Any] | None) -> tuple[int, int]:
    """Top-left frame (frame 0, angle 0) sub-rectangle, per engine atlas math.

    fr_num_x = projs * sum(anims); fr_num_y = angles  (sprite.cpp:541-599).
    Returns (frame_w, frame_h), falling back to full layer if it does not divide.
    """
    if not meta:
        return width, height
    angles = max(1, int(meta.get("angles", 1)))
    anims = meta.get("anims") or []
    projs = 2 if angles > 1 or (meta.get("angles", 0) and int(meta["angles"]) > 0) else 1
    # get_metadata sets projs=2 whenever raw_angles>0; mirror that.
    if anims and angles >= 1:
        cols = projs * sum(int(x) for x in anims)
    else:
        cols = 1
    if cols <= 0 or width % cols != 0:
        return width, height
    if height % angles != 0:
        return width, height
    return width // cols, height // angles


def visible_cells_in_rect(layer, x0: int, y0: int, w: int, h: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    data = layer.data
    for ry in range(h):
        y = y0 + ry
        if y >= layer.height:
            break
        row = data[y]
        for rx in range(w):
            x = x0 + rx
            if x >= layer.width:
                break
            glyph, fg, bg = row[x]
            if glyph == 0 or tuple(bg) == MAGENTA:
                continue  # viewer transparency rule
            out.append({"x": rx, "y": ry, "glyph": int(glyph),
                        "fg": [int(c) for c in fg], "bg": [int(c) for c in bg]})
    return out


def whole_atlas_fingerprint(layer) -> tuple[str, int, bool]:
    """SHA over ALL visible (glyph,x,y) across the full atlas + cyan-fg presence.

    Returns (fingerprint, visible_count, has_cyan_fg). Streamed: we never persist
    the full cell list (that is what bloated glyph_fingerprints.json to 40MB).
    """
    hasher = hashlib.sha256()
    count = 0
    has_cyan = False
    for y in range(layer.height):
        row = layer.data[y]
        for x in range(layer.width):
            glyph, fg, bg = row[x]
            if glyph == 0 or tuple(bg) == MAGENTA:
                continue
            hasher.update(f"{x},{y},{int(glyph)};".encode("ascii"))
            count += 1
            if tuple(fg) == CYAN:
                has_cyan = True
    return hasher.hexdigest(), count, has_cyan


def cell_delta(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> int:
    aset = {(c["x"], c["y"], c["glyph"]) for c in a}
    bset = {(c["x"], c["y"], c["glyph"]) for c in b}
    return len(aset ^ bset)


# --------------------------------------------------------------------------- #
# review ranking (step 6) — rejects first; mount-family wrong guesses loudest
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_prose_caps_accept(status: str, label: str) -> bool:
    """An 'accept' whose label is a CAPS prose observation, not a clean id.

    Mirrors the heuristic from xp_labeling_tool._review_needed: these are visual
    observations wearing accept status and must still be human-read.
    """
    if status != "accept":
        return False
    letters = [c for c in label if c.isalpha()]
    caps = [c for c in letters if c.isupper()]
    return bool(letters) and bool(re.search(r"\s", label)) and len(caps) >= max(2, len(letters) // 2)


def wrong_guess_class(status: str, pre_guess: str) -> str | None:
    if status != "reject":
        return None
    g = _norm(pre_guess)
    if g in NONINFORMATIVE_GUESS:
        return None
    return pre_guess.strip()  # the actual wrong label the machine asserted


QUEUE_CLASSES = [
    "wrong_guess_reject",   # 0 — automation asserted a WRONG label; human overrode by eye
    "reject",               # 1 — reject with absent/unknown guess
    "ambig",                # 2 — flagged ambiguous
    "partial",              # 3 — guess directionally close, hand-corrected
    "prose_caps_accept",    # 4 — accept whose label is a CAPS visual observation
    "clean_accept",         # 5 — accept confirmed verbatim (auto-confirmable subset)
]


def queue_class(status: str, label: str, pre_guess: str) -> int:
    if wrong_guess_class(status, pre_guess) is not None:
        return 0
    if status == "reject":
        return 1
    if status == "ambig":
        return 2
    if status == "partial":
        return 3
    if is_prose_caps_accept(status, label):
        return 4
    return 5  # clean accept (and any unknown status falls here, flagged by name)


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def read_state_final(path: Path) -> dict[str, dict[str, Any]]:
    payload = path.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if path == DEFAULT_STATE_FINAL and actual != SOURCE_FINAL_SHA256:
        raise SystemExit(
            f"state_FINAL sha256 mismatch — corpus is supposed to be IMMUTABLE.\n"
            f"  expected {SOURCE_FINAL_SHA256}\n  got      {actual}"
        )
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("state_FINAL root must be an object")
    return data, actual


def load_engine_refs() -> dict[str, str]:
    """Single owner of the source receipts is the committed conventions JSON."""
    if not CONVENTIONS_JSON.is_file():
        return {"_warning": f"missing {CONVENTIONS_JSON.name}; receipts unavailable"}
    doc = json.loads(CONVENTIONS_JSON.read_text(encoding="utf-8"))
    elc = doc.get("engine_layer_contract", {})
    return {
        "fixed_roles_L0_L1_L2": elc.get("fixed_roles_only_three", {}).get("cite", ""),
        "ordinal_merge_L3_to_N": elc.get("overlays_L3_to_N", {}).get("cite", ""),
        "swoosh_final_cyan": elc.get("swoosh_special_case", {}).get("cite", ""),
        "frame_topology_math": doc.get("bigbee_frame_exception", {}).get("cite", ""),
        "ahsw_filename_only": doc.get("ahsw_filename_only", {}).get("generation", ""),
        "conventions_doc": str(CONVENTIONS_JSON.relative_to(REPO_ROOT)),
        "conventions_authority": "false (durable evidence, not authority)",
    }


def engine_facts(layer_index: int, layer_count: int, has_cyan_final: bool,
                 meta: dict[str, Any] | None, refs: dict[str, str]) -> dict[str, Any]:
    is_final = layer_index == layer_count - 1
    fixed = {0: "L0 metadata/colorkey", 1: "L1 height", 2: "L2 base accumulator"}.get(layer_index)
    facts: dict[str, Any] = {
        "family_layer_count": layer_count,
        "fixed_role": fixed,  # None for L3+
        "is_base": layer_index == 2,
        "is_overlay": layer_index >= 3,
        "overlay_ordinal": (layer_index - 2) if layer_index >= 3 else None,
        "is_final_layer": is_final,
        "swoosh_candidate": is_final,
        "swoosh_cyan_fg_detected": bool(has_cyan_final and is_final),
        "engine_role_tag": None if layer_index >= 3 else fixed,
        "engine_contract_note": (
            "L3..N-1 merge ORDINALLY with NO per-layer role tag — 'the Nth thing "
            "painted', not 'armor=L3'. Role must be proven from the cells, not the index."
            if layer_index >= 3 else
            "Fixed engine role (one of only three: L0/L1/L2)."
        ),
        "refs": refs,
    }
    if meta:
        angles = max(1, int(meta.get("angles", 1)))
        anims = [int(x) for x in (meta.get("anims") or [])]
        projs = 2 if int(meta.get("angles", 0)) > 0 else 1
        fpa = projs * sum(anims) if anims else 1
        facts["frame_topology"] = {
            "angles": angles, "anims": anims, "projs": projs,
            "frames_per_angle": fpa, "total_frames": angles * fpa,
        }
    else:
        facts["frame_topology"] = None
    return facts


def build_cards(state: dict[str, dict[str, Any]], sha: str, refs: dict[str, str],
                sprites_dir: Path = SPRITES_DIR) -> list[dict[str, Any]]:
    keys = sorted(state)
    # --- pass 1: per-key visible-cell evidence + fingerprints ---------------- #
    xp_cache: dict[str, XPFile | None] = {}
    frame0: dict[str, list[dict[str, Any]]] = {}
    atlas_fp: dict[str, str] = {}
    per_key: dict[str, dict[str, Any]] = {}

    for key in keys:
        xp_stem, layer_index = parse_key(key)
        xp_path, resolution = resolve_xp_path(xp_stem, sprites_dir)
        rec: dict[str, Any] = {"xp_stem": xp_stem, "layer_index": layer_index,
                               "xp_path": xp_path, "xp_resolution": resolution,
                               "error": None}
        if xp_stem not in xp_cache:
            try:
                xp_cache[xp_stem] = XPFile(str(xp_path)) if xp_path.is_file() else None
            except Exception as exc:  # noqa: BLE001
                xp_cache[xp_stem] = None
                rec["error"] = f"load_failed: {exc}"
        xp = xp_cache[xp_stem]
        if xp is None and rec["error"] is None:
            rec["error"] = "missing_xp"
        if xp is not None and layer_index >= len(xp.layers):
            rec["error"] = "missing_layer"
            xp = None
        if xp is not None:
            try:
                meta = xp.get_metadata()
            except Exception:  # noqa: BLE001
                meta = None
            layer = xp.layers[layer_index]
            fw, fh = frame0_rect(layer.width, layer.height, meta)
            cells = visible_cells_in_rect(layer, 0, 0, fw, fh)
            fp, total_vis, has_cyan = whole_atlas_fingerprint(layer)
            rec.update({
                "meta": meta, "layer_count": len(xp.layers),
                "frame_wh": [fw, fh], "cells": cells,
                "fingerprint": fp, "atlas_visible_count": total_vis,
                "has_cyan_fg": has_cyan,
            })
            frame0[key] = cells
            atlas_fp[key] = fp
        per_key[key] = rec

    # --- pass 2: glyph similarity (comparison evidence only) ----------------- #
    fp_groups: dict[str, list[str]] = defaultdict(list)
    for key, fp in atlas_fp.items():
        fp_groups[fp].append(key)
    exact_of: dict[str, list[str]] = {}
    near_of: dict[str, list[dict[str, Any]]] = {}
    valid = [k for k in keys if k in frame0]
    for key in valid:
        exact_of[key] = sorted(k for k in fp_groups[atlas_fp[key]] if k != key)
        near: list[dict[str, Any]] = []
        for other in valid:
            if other == key or atlas_fp[other] == atlas_fp[key]:
                continue
            d = cell_delta(frame0[key], frame0[other])
            if d <= NEAR_THRESHOLD:
                near.append({"key": other, "cell_delta": d})
        near_of[key] = sorted(near, key=lambda r: (r["cell_delta"], r["key"]))

    fp_group_id = {fp: f"G{idx:04d}" for idx, fp in
                   enumerate(sorted(g for g, members in fp_groups.items() if len(members) > 1), 1)}

    # --- pass 3: assemble cards + ranking ------------------------------------ #
    cards: list[dict[str, Any]] = []
    for key in keys:
        xp_stem, layer_index = parse_key(key)
        row = state[key]
        status = str(row.get("status", "")).strip()
        label = str(row.get("corrected_label", "") or "")
        pre_guess = str(row.get("pre_guess", "") or "")
        rec = per_key[key]
        family = family_of(xp_stem)
        wg = wrong_guess_class(status, pre_guess)
        qc = queue_class(status, label, pre_guess)

        card: dict[str, Any] = {
            "schema": "fl4306.layer_evidence_card.v1",
            "authority": False,
            "is_proposal": True,
            "machine_recommendation": None,  # deliberate: no machine label authoring
            "card_id": key,
            "source_key": key,
            "source_xp_path": str(rec["xp_path"].relative_to(REPO_ROOT))
            if str(rec["xp_path"]).startswith(str(REPO_ROOT)) else str(rec["xp_path"]),
            "source_xp_resolution": rec["xp_resolution"],  # direct | monolith_base | missing
            "family": family,
            "is_mount_family": family in MOUNT_FAMILIES,
            "ahsw": decode_ahsw(xp_stem),
            "raw_layer_index": layer_index,
            "source_final_sha256": sha,
            "hand": {  # immutable corpus facts, verbatim — never authored
                # FL-4162: provenance must survive every phase. source_row_verbatim
                # is the ENTIRE raw state_FINAL row, byte-for-byte, so propagation
                # fields (auto_propagated_from / auto_propagation_kind, present on
                # 36 rows) and any future field are never dropped.
                "source_row_verbatim": dict(row),
                "status": status,
                "corrected_label": label,
                "note": str(row.get("note", "") or ""),
                "pre_source": str(row.get("pre_source", "") or ""),
                "pre_guess": pre_guess,
                "ts": str(row.get("ts", "") or ""),
                "auto_propagated_from": row.get("auto_propagated_from"),
                "auto_propagation_kind": row.get("auto_propagation_kind"),
            },
        }

        if rec.get("error"):
            card["cells"] = {"error": rec["error"]}
            card["engine"] = {"error": rec["error"], "refs": refs}
            card["glyph_similarity"] = {"error": rec["error"]}
        else:
            cells = rec["cells"]
            stored = cells[:CELL_STORE_CAP]
            card["cells"] = {
                "transparency_rule": "visible iff bg!=(255,0,255) and glyph!=0 (viewer xp_uv_body_viewer.py:1691)",
                "frame_scope": "frame0_angle0",
                "frame_wh": rec["frame_wh"],
                "glyph_count": len(cells),
                "visible_glyph_set": sorted({c["glyph"] for c in cells}),
                "cell_positions": stored,
                "cell_positions_truncated": len(cells) > CELL_STORE_CAP,
                "atlas_visible_count": rec["atlas_visible_count"],
                "whole_atlas_fingerprint": rec["fingerprint"],
            }
            card["engine"] = engine_facts(
                layer_index, rec["layer_count"], rec["has_cyan_fg"], rec.get("meta"), refs)
            card["glyph_similarity"] = {
                "scope": "exact=whole_atlas_fingerprint equality; near=frame0 cell_delta<=%d" % NEAR_THRESHOLD,
                "note": "Clusters evidence; does NOT define role (authority_chain link 3).",
                "exact_matches": exact_of.get(key, []),
                "near_matches": near_of.get(key, [])[:24],
            }

        card["groups"] = {
            "note_group_id": "N:" + _norm(card["hand"]["note"]) if card["hand"]["note"] else None,
            "label_group_id": "L:" + _norm(label) if label else None,
            "family_layer_group_id": f"{family}:L{layer_index}",
            "glyph_exact_group_id": fp_group_id.get(rec.get("fingerprint")) if not rec.get("error") else None,
            "wrong_guess_class": wg,
        }
        card["review"] = {
            "queue_class": qc,
            "queue_class_name": QUEUE_CLASSES[qc],
            "is_mount_family": family in MOUNT_FAMILIES,
            # within a class, mount families surface first, then by family/layer
            "_sort": (qc, 0 if family in MOUNT_FAMILIES else 1, family, layer_index, key),
            "rationale": _rationale(qc, family in MOUNT_FAMILIES, wg),
        }
        cards.append(card)

    cards.sort(key=lambda c: c["review"]["_sort"])
    for rank, card in enumerate(cards, 1):
        card["review"]["review_rank"] = rank
        del card["review"]["_sort"]
    return cards


def _rationale(qc: int, is_mount: bool, wg: str | None) -> str:
    if qc == 0:
        base = f"REJECT overriding a WRONG machine guess ('{wg}') — highest-value evidence: automation asserted a role, the cells disproved it."
        return ("MOUNT family — review FIRST. " + base) if is_mount else base
    if qc == 1:
        return "Reject with absent/unknown guess — human supplied role by eye; preserve note."
    if qc == 2:
        return "Flagged ambiguous — needs human disambiguation."
    if qc == 3:
        return "Partial — machine guess was directionally close and hand-corrected."
    if qc == 4:
        return "Accept whose label is a CAPS prose observation — a visual note wearing accept status; still read it."
    return "Clean accept — guess confirmed verbatim; auto-confirmable subset, review last."


def build_manifest(cards: list[dict[str, Any]], state: dict[str, dict[str, Any]], sha: str) -> dict[str, Any]:
    status_counts = Counter(str(r.get("status", "")) for r in state.values())
    qc_counts = Counter(c["review"]["queue_class_name"] for c in cards)
    err_counts = Counter(c["cells"].get("error") for c in cards if "error" in c["cells"])
    return {
        "schema": "fl4306.layer_evidence_cards.manifest.v1",
        "authority": False,
        "status": "proposal_and_contradiction_surface_not_authority",
        "purpose": "Index + provenance for layer_evidence_cards.jsonl. Each card turns one "
                   "immutable hand-labeled state_FINAL row into a source-backed evidence record "
                   "for rejects-first human review through the XP Body Viewer.",
        "related_fl": LINEAGE_FL,
        "provenance": {
            "state_final": str(DEFAULT_STATE_FINAL),
            "state_final_sha256": sha,
            "xp_source_dir": str(SPRITES_DIR.relative_to(REPO_ROOT)),
            "xp_parser": "pipeline-v3/scripts/xp_core.py:XPFile (viewer-canonical)",
            "transparency_rule": "visible iff bg!=(255,0,255) and glyph!=0 (xp_uv_body_viewer.py:1691)",
            "receipts_owner": str(CONVENTIONS_JSON.relative_to(REPO_ROOT)),
        },
        "authority_chain": [
            "1. state_FINAL stays IMMUTABLE evidence (sha256 above).",
            "2. These cards are a PROPOSAL/contradiction surface, not final labels.",
            "3. Glyph similarity CLUSTERS evidence; it does not DEFINE truth.",
            "4. AHSW is tuple identity + equipment presence, NOT raw-layer role authority.",
            "5. Raw layer role MUST be proven per (source_xp_path + layer_index) by a human.",
            "6. Runtime validation derives reachable ActorVisualProfile keys from server/runtime "
            "truth, then FAILS CLOSED against authored profiles. This file never defines the check.",
        ],
        "review_class_legend": {str(i): name for i, name in enumerate(QUEUE_CLASSES)},
        "review_order": "rejects-first: wrong_guess_reject (mount families first) -> reject -> "
                        "ambig -> partial -> prose_caps_accept -> clean_accept",
        "counts": {
            "total_cards": len(cards),
            "source_status": dict(sorted(status_counts.items())),
            "queue_class": dict(sorted(qc_counts.items())),
            "errors": {k: v for k, v in err_counts.items() if k},
        },
        "non_goals": [
            "Does NOT author labels, recommendations, or confidence-as-semantic.",
            "Does NOT promote any card to a semantic-map or ActorVisualProfile row.",
            "accept/partial/reject == guesser-vs-handcorpus AGREEMENT, not semantic confidence.",
        ],
    }


def write_jsonl(cards: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for card in cards:
            fh.write(json.dumps(card, sort_keys=True, separators=(",", ":")) + "\n")
    tmp.replace(path)


def write_json(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-final", type=Path, default=DEFAULT_STATE_FINAL)
    ap.add_argument("--check", action="store_true", help="dry-run: build + print summary, write nothing")
    args = ap.parse_args()

    state, sha = read_state_final(args.state_final)
    refs = load_engine_refs()
    cards = build_cards(state, sha, refs)
    manifest = build_manifest(cards, state, sha)

    c = manifest["counts"]
    print("layer evidence cards")
    print(f"  total           : {c['total_cards']}")
    print(f"  source status   : {c['source_status']}")
    print(f"  queue class      : {c['queue_class']}")
    if c["errors"]:
        print(f"  errors          : {c['errors']}")
    print(f"  review order    : {manifest['review_order']}")
    top = cards[:5]
    print("  first 5 (rejects-first):")
    for card in top:
        print(f"    #{card['review']['review_rank']:>3} {card['card_id']:<18} "
              f"{card['review']['queue_class_name']:<18} status={card['hand']['status']}")

    if args.check:
        print("\n[--check] no files written.")
        return 0

    write_jsonl(cards, CARDS_OUT)
    write_json(manifest, MANIFEST_OUT)
    print(f"\nwrote {CARDS_OUT.relative_to(REPO_ROOT)}")
    print(f"wrote {MANIFEST_OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
