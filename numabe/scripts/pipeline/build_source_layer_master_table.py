#!/usr/bin/env python3
"""Build the repo-owned Phase 3 source-layer master table.

The master table is a layer-resolution review surface.  It combines the
Desktop audit rows, the curated source-layer ledger, per-layer PNG paths, glyph
identifier summaries, and upstream engine evidence into one folder so the
compiler-facing source truth does not get split across stale spreadsheets,
semantic helper code, and loose preview images.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from collections import Counter
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.xp_core import XPFile

DEFAULT_AUDIT_DIR = Path("/Users/r/Desktop/bundle_layer_audit_20260520")
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "docs/research/ascii/semantic_maps/source_layer_master"
)
SOURCE_LEDGER = REPO_ROOT / "docs/research/ascii/semantic_maps/source_layer_ledger.json"

ENGINE_EVIDENCE = {
    "upstream_repo": "https://github.com/msokalski/asciicker",
    "file_identity_code": (
        "upstream/master:game.cpp:2979-3017 enumerates player/plydie/"
        "wolfie/bigbee/attack/wolack XP files with sprintf(\"family-%x%x%x%x.xp\", "
        "armor, helmet, shield, weapon). game.cpp:1592-1595 and 1649-1652 "
        "decode network sprite bits into req.armor/helmet/shield/weapon."
    ),
    "layer_merge_code": (
        "upstream/master:sprite.cpp:350-352 assigns L0=color-key metadata, "
        "L1=height/depth, L2=image map. sprite.cpp:354-530 merges L3+ into "
        "L2 in raw file order; the final layer has special cyan-fg swoosh "
        "handling. sprite.cpp:541-622 decodes L0 angles, anim_len, y/z "
        "projection/reflection offsets."
    ),
    "role_rule": (
        "Engine code selects the XP file from A/H/S/W tuple bits and merges raw "
        "layers; it does not name per-layer roles. Raw layer meaning must come "
        "from source_xp_path + raw_layer_index evidence, PNG/cell review, and "
        "ledger receipts."
    ),
}

SEMANTIC_DICT_EVIDENCE = (
    "scripts/pipeline/bundle_wizard/semantic_dict.py:984-1065 contains "
    "role-fingerprint glyph signatures. Those signatures are identifier "
    "evidence only; they are not compiler authority without a ledger receipt."
)

HUMAN_VISUAL_RE = re.compile(
    r"USER_VISUALLY_VERIFIED|User visual call|user visual call|User corrected|Reviewer visual call",
    re.IGNORECASE,
)

CP437_NAMES = {
    0x07: "BEL",
    0x19: "EM",
    0x1E: "tri_up",
    0x1F: "tri_down",
    0x20: "space",
    0x22: '"',
    0x27: "'",
    0x2F: "/",
    0x40: "@",
    0x5C: "\\",
    0x5E: "^",
    0x60: "`",
    0x76: "v",
    0xB1: "shade",
    0xDB: "full",
    0xDC: "lower",
    0xDD: "left",
    0xDE: "right",
    0xDF: "upper",
    0xC4: "hbar",
    0xB3: "vbar",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_table(path: Path, rows: list[dict[str, Any]], delimiter: str) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            delimiter=delimiter,
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def normalized_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        return data["rows"]
    if isinstance(data, list):
        return data
    raise TypeError("audit JSON must be either a row list or an object with rows")


def parse_ahsw(source_xp_path: str) -> dict[str, str]:
    stem = Path(source_xp_path).stem
    if "-" not in stem:
        return {
            "source_family": stem,
            "ahsw_code": "",
            "ahsw_armor": "",
            "ahsw_helmet": "",
            "ahsw_shield": "",
            "ahsw_weapon": "",
            "ahsw_enabled_slots": "",
            "ahsw_weapon_variant": "",
        }
    family, code = stem.rsplit("-", 1)
    if len(code) != 4 or any(ch not in "0123456789abcdefABCDEF" for ch in code):
        return {
            "source_family": family,
            "ahsw_code": code,
            "ahsw_armor": "",
            "ahsw_helmet": "",
            "ahsw_shield": "",
            "ahsw_weapon": "",
            "ahsw_enabled_slots": "",
            "ahsw_weapon_variant": "",
        }
    armor, helmet, shield, weapon = code.lower()
    slots: list[str] = []
    if armor != "0":
        slots.append(f"armor:{armor}")
    if helmet != "0":
        slots.append(f"helmet:{helmet}")
    if shield != "0":
        slots.append(f"shield:{shield}")
    weapon_variant = {"0": "none", "1": "sword", "2": "crossbow"}.get(
        weapon, f"code_{weapon}"
    )
    if weapon_variant != "none":
        slots.append(f"weapon:{weapon_variant}")
    return {
        "source_family": family,
        "ahsw_code": code.lower(),
        "ahsw_armor": armor,
        "ahsw_helmet": helmet,
        "ahsw_shield": shield,
        "ahsw_weapon": weapon,
        "ahsw_enabled_slots": ",".join(slots),
        "ahsw_weapon_variant": weapon_variant,
    }


def cell_visible(cell: tuple[Any, Any, Any], key_cell: tuple[Any, Any, Any]) -> bool:
    glyph, fg, bg = cell
    key = tuple(key_cell[2])
    fg = tuple(fg)
    bg = tuple(bg)
    if bg == (255, 0, 255):
        return False
    return not (fg == key and bg == key)


def glyph_name(glyph: int) -> str:
    if glyph in CP437_NAMES:
        return CP437_NAMES[glyph]
    if 32 <= glyph <= 126:
        return chr(glyph)
    return f"0x{glyph:02x}"


def glyph_summary_for_layer(xp: XPFile, layer_index: int) -> dict[str, Any]:
    layer = xp.layers[layer_index]
    layer0 = xp.layers[0]
    glyphs: Counter[int] = Counter()
    fgs: Counter[tuple[int, int, int]] = Counter()
    visible = 0
    cyan_fg = 0
    for y, row in enumerate(layer.data):
        for x, cell in enumerate(row):
            if not cell_visible(cell, layer0.data[y][x]):
                continue
            glyph = int(cell[0])
            fg = tuple(cell[1])
            glyphs[glyph] += 1
            fgs[fg] += 1
            visible += 1
            if fg == (0, 255, 255):
                cyan_fg += 1
    top_glyphs = ";".join(
        f"{glyph_name(g)}:{n}" for g, n in glyphs.most_common(12)
    )
    top_fgs = ";".join(f"{rgb}:{n}" for rgb, n in fgs.most_common(8))
    identifiers: list[str] = []
    if layer_index == 0:
        identifiers.append("system_l0_metadata_color_key_animation_projection")
    elif layer_index == 1:
        identifiers.append("system_l1_height_depth")
    if cyan_fg:
        identifiers.append(f"swoosh_cyan_fg:{cyan_fg}")
    if glyphs.get(0x40, 0):
        identifiers.append(f"shield_at:{glyphs[0x40]}")
    if glyphs.get(0xB1, 0):
        identifiers.append(f"armor_shade:{glyphs[0xB1]}")
    if glyphs.get(0x5E, 0):
        identifiers.append(f"helmet_or_head_caret:{glyphs[0x5E]}")
    crossbow_count = (
        glyphs.get(0x07, 0)
        + glyphs.get(0x5C, 0)
        + glyphs.get(0x2F, 0)
        + glyphs.get(0xC4, 0)
        + glyphs.get(0xB3, 0)
        + glyphs.get(0x19, 0)
    )
    if crossbow_count:
        identifiers.append(f"crossbow_glyph_family:{crossbow_count}")
    if glyphs.get(0x22, 0) or glyphs.get(0x76, 0) or glyphs.get(0x60, 0):
        identifiers.append("face_or_rider_body_glyphs")
    if not identifiers and visible:
        identifiers.append("visible_cells_no_strong_role_fingerprint")
    if not identifiers:
        identifiers.append("empty_or_engine_transparent")
    return {
        "glyph_visible_cells": str(visible),
        "glyph_top": top_glyphs,
        "glyph_fg_top": top_fgs,
        "glyph_identifier_summary": ",".join(identifiers),
        "glyph_identifier_authority": "heuristic_only_not_compiler_authority",
    }


def active_slot_names(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if str(row.get("ahsw_armor", "")) not in ("", "0"):
        out.append("armor")
    if str(row.get("ahsw_helmet", "")) not in ("", "0"):
        out.append("helmet")
    if str(row.get("ahsw_shield", "")) not in ("", "0"):
        out.append("shield")
    weapon_variant = str(row.get("ahsw_weapon_variant", ""))
    if weapon_variant and weapon_variant != "none":
        out.append(f"weapon:{weapon_variant}")
    return out


def structural_layer_label(row: dict[str, Any]) -> tuple[str, str]:
    """Return a concrete non-authoritative attribution for every layer.

    This is deliberately not compiler authority.  It is the complete layer
    accounting surface that prevents rows from sitting as ambiguous
    "manual_review_required" blanks.
    """
    layer_index = int(row.get("layer_index", 0))
    family = str(row.get("source_family", ""))
    active = active_slot_names(row)
    active_text = "+".join(active) if active else "none"
    glyph_ids = str(row.get("glyph_identifier_summary", ""))
    is_final = layer_index == int(row.get("layer_count_in_xp", "1")) - 1

    if layer_index == 0:
        return (
            "system:L0_color_key_geometry_animation_projection_offsets",
            "engine_contract_l0",
        )
    if layer_index == 1:
        return ("system:L1_height_depth_spare_elevation", "engine_contract_l1")
    if layer_index == 2:
        if family in {"player", "plydie"}:
            if active:
                return (
                    f"composite_base:{family}_body+{active_text}",
                    "L2 base image map plus any equipment authored into base/composite layer",
                )
            return (f"base_body:{family}", "L2 base image map")
        if family == "attack":
            return (
                f"composite_base:attack_body+{active_text}",
                "attack family L2 base image map; attack sources require weapon context",
            )
        if family == "wolfie":
            if "-" not in str(row.get("source_xp_path", "")):
                return ("base_mount_body:wolf", "bare wolf mount source")
            return (
                f"composite_base:wolf_mount_rider+{active_text}",
                "mounted wolf idle/walk L2 base image map for selected AHSW tuple",
            )
        if family == "wolack":
            return (
                f"composite_base:wolf_mounted_attack+{active_text}",
                "mounted wolf attack L2 base image map for selected AHSW tuple",
            )
        if family == "bigbee":
            if "-" not in str(row.get("source_xp_path", "")):
                return ("base_mount_body:bee", "bare bee mount source")
            return (
                f"composite_base:bee_mount+{active_text}",
                "bigbee L2 bee/mount base image map for selected AHSW tuple",
            )
        return ("base_visual:L2_image_map", "generic L2 base image map")

    if "swoosh_cyan_fg" in glyph_ids and is_final:
        return (
            f"effect:swoosh_or_motion_fx+{active_text}",
            "final raw layer has cyan foreground cells; upstream sprite.cpp applies swoosh handling",
        )
    if "crossbow_glyph_family" in glyph_ids:
        return ("weapon:crossbow", "crossbow glyph family identifiers")
    if "shield_at" in glyph_ids:
        if "face_or_rider_body_glyphs" in glyph_ids:
            return (
                f"composite_overlay:shield_plus_body_context+{active_text}",
                "shield @ glyphs plus rider/body glyphs indicate composite context",
            )
        return ("shield", "shield @ glyph identifiers")
    if "armor_shade" in glyph_ids:
        if "armor" in active:
            return ("armor", "armor shade glyphs with active AHSW armor slot")
        return (
            f"composite_overlay:armor_or_shaded_context+{active_text}",
            "armor shade glyphs without sole-slot proof",
        )
    if "helmet_or_head_caret" in glyph_ids:
        if "helmet" in active:
            return ("helmet", "helmet/head caret glyphs with active AHSW helmet slot")
        return (
            f"head_or_helmet_context+{active_text}",
            "caret glyphs indicate head/helmet/context; AHSW tuple disambiguates only file identity",
        )
    if "face_or_rider_body_glyphs" in glyph_ids:
        return (
            f"rider_or_body_context+{active_text}",
            "face/rider body glyphs with no stronger equipment glyph fingerprint",
        )
    if "visible_cells_no_strong_role_fingerprint" in glyph_ids:
        return (
            f"context_overlay_for_active_slots:{active_text}",
            "visible overlay cells with no strong glyph fingerprint; attributed to selected tuple context",
        )
    return (
        f"transparent_or_empty_overlay:{active_text}",
        "no engine-visible cells after L0 key filtering",
    )


def apply_complete_layer_attribution(master: dict[str, Any]) -> None:
    """Fill complete attribution fields and remove un-attributed states."""
    structural_label, structural_basis = structural_layer_label(master)
    ledger_meaning = str(master.get("ledger_meanings_joined", ""))
    layer_index = int(master.get("layer_index", 0))

    if layer_index in (0, 1):
        label = structural_label
        confidence = "ENGINE_CONTRACT"
        review_state = "ENGINE_CONTRACT_ATTRIBUTED"
        authority_state = "SYSTEM_COPY_VERBATIM_ENGINE_CONTRACT"
        basis = structural_basis
    elif master.get("ledger_human_visual_reviewed") == "True":
        label = ledger_meaning or structural_label
        confidence = "USER_VISUALLY_VERIFIED"
        review_state = "USER_VISUALLY_VERIFIED"
        authority_state = str(
            master.get("full_ledger_authority_state")
            or "LEDGER_VISUAL_EVIDENCE_NON_AUTHORITY_UNLESS_PROMOTED"
        )
        basis = master.get("ledger_human_visual_evidence_joined", "") or structural_basis
    elif ledger_meaning:
        label = ledger_meaning
        confidence = "LEDGER_ATTRIBUTED"
        review_state = "LEDGER_ATTRIBUTED"
        authority_state = str(
            master.get("full_ledger_authority_state")
            or "LEDGER_NON_AUTHORITY_UNLESS_PROMOTED"
        )
        basis = master.get("ledger_evidence_joined", "") or structural_basis
    else:
        label = structural_label
        confidence = "STRUCTURAL_INFERRED"
        review_state = "STRUCTURAL_ATTRIBUTED"
        authority_state = "STRUCTURAL_INFERENCE_NON_AUTHORITY"
        basis = structural_basis

    master["layer_attribution_label"] = label
    master["layer_attribution_confidence"] = confidence
    master["layer_attribution_review_state"] = review_state
    master["layer_attribution_authority_state"] = authority_state
    master["layer_attribution_basis"] = basis
    master["layer_attribution_png"] = master.get("master_png_path", "")
    master["layer_attribution_engine_rule"] = ENGINE_EVIDENCE["role_rule"]

    master["full_ledger_semantic_label"] = label
    master["full_ledger_review_state"] = review_state
    master["full_ledger_confidence"] = confidence.lower()
    master["full_ledger_authority_state"] = authority_state
    master["full_ledger_evidence"] = (
        str(master.get("full_ledger_evidence") or "")
        + (" | " if master.get("full_ledger_evidence") else "")
        + f"{confidence}: {basis}"
    )


def ledger_index(ledger: dict[str, Any]) -> dict[tuple[str | None, int | None], list[dict[str, Any]]]:
    out: dict[tuple[str | None, int | None], list[dict[str, Any]]] = {}
    for row in ledger.get("rows", []):
        key = (row.get("source_xp_path"), row.get("raw_layer_index"))
        out.setdefault(key, []).append(row)
    return out


def summarize_ledger_rows(rows: list[dict[str, Any]]) -> dict[str, str]:
    if not rows:
        return {
            "ledger_row_count": "0",
            "ledger_meanings_joined": "",
            "ledger_statuses_joined": "",
            "ledger_confidences_joined": "",
            "ledger_source_kinds_joined": "",
            "ledger_allowed_source_ids_joined": "",
            "ledger_evidence_joined": "",
            "ledger_contradictions_joined": "",
            "ledger_human_visual_reviewed": "False",
            "ledger_human_visual_evidence_joined": "",
        }
    meanings = sorted({str(r.get("meaning", "")) for r in rows if r.get("meaning")})
    statuses = sorted({str(r.get("status", "")) for r in rows if r.get("status")})
    confidences = sorted({str(r.get("confidence", "")) for r in rows if r.get("confidence")})
    kinds = sorted({str(r.get("source_kind", "")) for r in rows if r.get("source_kind")})
    allowed: list[str] = []
    evidence: list[str] = []
    contradictions: list[str] = []
    for row in rows:
        allowed.extend(str(x) for x in row.get("allowed_as_source_for", []) or [])
        evidence.extend(str(x) for x in row.get("evidence", []) or [])
        for contradiction in row.get("contradicts", []) or []:
            contradictions.append(
                f"{contradiction.get('file', '')}: {contradiction.get('claim', '')}"
            )
    human_visual_evidence = [item for item in evidence if HUMAN_VISUAL_RE.search(item)]
    return {
        "ledger_row_count": str(len(rows)),
        "ledger_meanings_joined": "|".join(meanings),
        "ledger_statuses_joined": "|".join(statuses),
        "ledger_confidences_joined": "|".join(confidences),
        "ledger_source_kinds_joined": "|".join(kinds),
        "ledger_allowed_source_ids_joined": "|".join(sorted(set(allowed))),
        "ledger_evidence_joined": " || ".join(evidence),
        "ledger_contradictions_joined": " || ".join(contradictions),
        "ledger_human_visual_reviewed": str(bool(human_visual_evidence)),
        "ledger_human_visual_evidence_joined": " || ".join(human_visual_evidence),
    }


def build_master_rows(
    audit_rows: list[dict[str, Any]],
    ledger: dict[str, Any],
    *,
    audit_dir: Path,
    output_dir: Path,
) -> list[dict[str, Any]]:
    by_ledger = ledger_index(ledger)
    xp_cache: dict[str, XPFile] = {}
    master_rows: list[dict[str, Any]] = []
    png_output_dir = output_dir / "png_layers"
    png_output_dir.mkdir(parents=True, exist_ok=True)

    for row in audit_rows:
        source_xp_path = str(row.get("source_xp_path") or row.get("xp_path") or "")
        layer_index = int(row["layer_index"])
        xp_repo_path = REPO_ROOT / "assets/sprites" / Path(source_xp_path).name
        xp = xp_cache.get(source_xp_path)
        if xp is None and xp_repo_path.exists():
            xp = XPFile()
            with redirect_stdout(StringIO()):
                xp.load(xp_repo_path)
            xp_cache[source_xp_path] = xp

        png_source = Path(str(row.get("layer_png") or ""))
        if not png_source.is_absolute():
            png_source = audit_dir / png_source
        png_dest = png_output_dir / png_source.name
        if png_source.exists():
            shutil.copy2(png_source, png_dest)

        ledger_rows = by_ledger.get((source_xp_path, layer_index), [])
        master = dict(row)
        master.update(parse_ahsw(source_xp_path))
        master.update(summarize_ledger_rows(ledger_rows))
        master["source_layer_key"] = f"{source_xp_path}:L{layer_index}"
        master["master_png_path"] = str(png_dest.relative_to(REPO_ROOT))
        master["master_png_exists"] = str(png_dest.exists())
        master["source_png_path"] = str(png_source)
        master["engine_file_identity_evidence"] = ENGINE_EVIDENCE["file_identity_code"]
        master["engine_layer_merge_evidence"] = ENGINE_EVIDENCE["layer_merge_code"]
        master["engine_role_authority_rule"] = ENGINE_EVIDENCE["role_rule"]
        master["semantic_dict_identifier_evidence"] = SEMANTIC_DICT_EVIDENCE
        master["master_authority_rule"] = (
            "AHSW validates source combo file identity; source_xp_path/raw_layer_index "
            "plus ledger/PNG/cell evidence validates layer role."
        )
        if xp is not None:
            master.update(glyph_summary_for_layer(xp, layer_index))
        else:
            master.update(
                {
                    "glyph_visible_cells": "",
                    "glyph_top": "",
                    "glyph_fg_top": "",
                    "glyph_identifier_summary": "xp_missing",
                    "glyph_identifier_authority": "unavailable",
                }
            )
        if master.get("ledger_human_visual_reviewed") == "True":
            master["user_visual_verification"] = "USER_VISUALLY_VERIFIED_FROM_LEDGER"
            master["user_visual_semantic_label"] = master.get(
                "ledger_meanings_joined", ""
            )
            master["user_visual_evidence"] = master.get(
                "ledger_human_visual_evidence_joined", ""
            )
        apply_complete_layer_attribution(master)
        master_rows.append(master)
    master_rows.sort(
        key=lambda r: (
            str(r.get("source_family", "")),
            str(r.get("source_xp_path", "")),
            int(r.get("layer_index", 0)),
        )
    )
    return master_rows


def write_human_verified_attributions(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    human_rows = [
        {
            "source_layer_key": row.get("source_layer_key", ""),
            "source_xp_path": row.get("source_xp_path", ""),
            "raw_layer_index": row.get("layer_index", ""),
            "source_family": row.get("source_family", ""),
            "ahsw_code": row.get("ahsw_code", ""),
            "ahsw_enabled_slots": row.get("ahsw_enabled_slots", ""),
            "semantic_label": row.get("ledger_meanings_joined")
            or row.get("full_ledger_semantic_label", ""),
            "ledger_status": row.get("ledger_statuses_joined", ""),
            "ledger_confidence": row.get("ledger_confidences_joined", ""),
            "source_kind": row.get("ledger_source_kinds_joined", ""),
            "review_state": row.get("full_ledger_review_state", ""),
            "png": row.get("master_png_path", ""),
            "glyph_identifier_summary": row.get("glyph_identifier_summary", ""),
            "glyph_top": row.get("glyph_top", ""),
            "engine_trace": row.get("engine_role_authority_rule", ""),
            "human_visual_evidence": row.get("ledger_human_visual_evidence_joined", ""),
        }
        for row in rows
        if row.get("ledger_human_visual_reviewed") == "True"
    ]
    human_rows.sort(key=lambda r: (r["source_xp_path"], int(r["raw_layer_index"])))
    write_json(
        output_dir / "human_verified_layer_attributions_20260520.json",
        {
            "schema": "asciicker.source_layer_master.human_verified_attributions.v1",
            "row_count": len(human_rows),
            "rows": human_rows,
        },
    )
    write_table(
        output_dir / "human_verified_layer_attributions_20260520.csv",
        human_rows,
        ",",
    )
    write_table(
        output_dir / "human_verified_layer_attributions_20260520.tsv",
        human_rows,
        "\t",
    )
    lines = [
        "# Human-Verified Layer Attributions",
        "",
        "This is the compact review surface for source-layer rows whose ledger",
        "evidence contains a user/reviewer visual call.",
        "",
        "| source layer | semantic label | PNG | glyph identifiers |",
        "| --- | --- | --- | --- |",
    ]
    for row in human_rows:
        lines.append(
            "| {source_layer_key} | {semantic_label} | {png} | {glyph_identifier_summary} |".format(
                **row
            )
        )
    (output_dir / "human_verified_layer_attributions_20260520.md").write_text(
        "\n".join(lines) + "\n"
    )


def write_full_layer_ledger(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    ledger_rows = []
    for row in rows:
        ledger_rows.append(
            {
                "source_layer_key": row.get("source_layer_key", ""),
                "source_xp_path": row.get("source_xp_path", ""),
                "raw_layer_index": row.get("layer_index", ""),
                "source_family": row.get("source_family", ""),
                "ahsw_code": row.get("ahsw_code", ""),
                "ahsw_enabled_slots": row.get("ahsw_enabled_slots", ""),
                "layer_kind": row.get("layer_kind", ""),
                "attribution_label": row.get("layer_attribution_label", ""),
                "attribution_confidence": row.get("layer_attribution_confidence", ""),
                "review_state": row.get("layer_attribution_review_state", ""),
                "authority_state": row.get("layer_attribution_authority_state", ""),
                "source_kind": row.get("ledger_source_kinds_joined", "")
                or row.get("layer_kind", ""),
                "png": row.get("master_png_path", ""),
                "glyph_identifier_summary": row.get("glyph_identifier_summary", ""),
                "glyph_top": row.get("glyph_top", ""),
                "engine_visible_cell_count": row.get("engine_visible_cell_count", ""),
                "l0_angles": row.get("l0_angles", ""),
                "l0_anim_len": row.get("l0_anim_len", ""),
                "l0_y_proj": row.get("l0_y_proj", ""),
                "l0_y_refl": row.get("l0_y_refl", ""),
                "l0_z_proj": row.get("l0_z_proj", ""),
                "l0_z_refl": row.get("l0_z_refl", ""),
                "basis": row.get("layer_attribution_basis", ""),
                "engine_rule": row.get("layer_attribution_engine_rule", ""),
                "ledger_evidence": row.get("ledger_evidence_joined", ""),
                "contradictions": row.get("ledger_contradictions_joined", ""),
            }
        )
    payload = {
        "schema": "asciicker.source_layer_full_ledger.v1",
        "row_count": len(ledger_rows),
        "rule": (
            "Every upstream XP layer has an attribution. Confidence/authority "
            "are separate: ENGINE_CONTRACT and USER_VISUALLY_VERIFIED rows are "
            "stronger; STRUCTURAL_INFERRED rows are complete accounting but not "
            "compiler authority."
        ),
        "rows": ledger_rows,
    }
    write_json(output_dir / "source_layer_full_ledger.json", payload)
    write_table(output_dir / "source_layer_full_ledger.csv", ledger_rows, ",")
    write_table(output_dir / "source_layer_full_ledger.tsv", ledger_rows, "\t")


def write_attribution_check_report(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    missing = [
        row
        for row in rows
        if not row.get("layer_attribution_label")
        or row.get("full_ledger_review_state") == "manual_review_required"
    ]
    by_conf = Counter(str(row.get("layer_attribution_confidence", "")) for row in rows)
    by_label = Counter(str(row.get("layer_attribution_label", "")) for row in rows)
    report = {
        "schema": "asciicker.source_layer_master.attribution_check.v1",
        "row_count": len(rows),
        "missing_or_manual_review_required": len(missing),
        "confidence_counts": dict(sorted(by_conf.items())),
        "top_label_counts": dict(by_label.most_common(80)),
        "missing_rows": [
            {
                "source_layer_key": row.get("source_layer_key", ""),
                "review_state": row.get("full_ledger_review_state", ""),
                "label": row.get("layer_attribution_label", ""),
            }
            for row in missing
        ],
    }
    write_json(output_dir / "source_layer_attribution_check_20260520.json", report)
    if missing:
        raise RuntimeError(
            f"{len(missing)} source-layer rows still lack complete attribution"
        )


def write_mounted_helmet_shield_trace(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    key = "sprites/wolfie-0110.xp:L3"
    row = next((r for r in rows if r.get("source_layer_key") == key), None)
    lines = [
        "# Mounted Helmet + Shield Upstream Trace",
        "",
        "Scenario: player equips helmet and shield, no armor, no weapon, mounted on wolf, idle/action NONE.",
        "",
        "Engine trace:",
        "",
        "1. `game.cpp:1592-1595` / `1649-1652` decode sprite bits into `req.armor`, `req.helmet`, `req.shield`, `req.weapon`.",
        "2. `LoadSprites()` preloads `wolfie-%x%x%x%x.xp` for every A/H/S/W tuple at `game.cpp:2979-3002`.",
        "3. `GetSprite()` returns `wolfie[clr][req->armor][req->helmet][req->shield][req->weapon]` for `MOUNT::WOLF` + `ACTION::NONE` at `game.cpp:3248-3254`.",
        "4. Helmet+shield with no armor/no weapon is A=0,H=1,S=1,W=0, therefore the file is `sprites/wolfie-0110.xp`.",
        "5. `sprite.cpp:350-352` interprets L0 as metadata/color key, L1 as height/depth, L2 as image map.",
        "6. `sprite.cpp:354-530` merges L3+ into L2 in raw file order. The engine does not name L3 as helmet; the XP content/ledger does.",
        "",
        "Layer attribution for `wolfie-0110.xp`:",
        "",
        "- L0: system metadata/color-key/animation/projection contract, copied verbatim.",
        "- L1: system height/depth contract, copied verbatim.",
        "- L2: base mounted wolf+rider+shield composite image map for the selected A/H/S/W combo.",
        "- L3: helmet/head overlay. User-verified; glyph identifiers include `^` helmet/head carets and sparse head-cap halfblocks.",
        "",
    ]
    if row:
        lines.extend(
            [
                "Master table row:",
                "",
                f"- source_layer_key: `{row.get('source_layer_key')}`",
                f"- semantic label: `{row.get('ledger_meanings_joined') or row.get('full_ledger_semantic_label')}`",
                f"- PNG: `{row.get('master_png_path')}`",
                f"- glyph identifiers: `{row.get('glyph_identifier_summary')}`",
                "",
            ]
        )
    (output_dir / "mounted_helmet_shield_wolf_trace_20260520.md").write_text(
        "\n".join(lines)
    )


def write_readme(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    readme = output_dir / "README.md"
    verified = sum(
        1
        for row in rows
        if str(row.get("full_ledger_review_state")) == "USER_VISUALLY_VERIFIED"
    )
    png_count = sum(1 for row in rows if str(row.get("master_png_exists")) == "True")
    readme.write_text(
        "\n".join(
            [
                "# Source Layer Master Table",
                "",
                "This folder is the repo-owned Phase 3 layer-resolution review surface.",
                "",
                "It combines the old Desktop spreadsheet, curated source-layer ledger,",
                "semantic/glyph identifiers, per-layer PNGs, and upstream engine-code",
                "evidence into one table.",
                "",
                "Authority rules:",
                "",
                "- AHSW filename digits are canonical upstream tuple/file identity only.",
                "- Layer order is not deterministic from AHSW.",
                "- Raw layer role requires source_xp_path + raw_layer_index + ledger/PNG/cell evidence.",
                "- L0 and L1 are engine contract layers, copied verbatim, not role candidates.",
                "- Glyph signatures are identifiers and review aids, not compiler authority by themselves.",
                "",
                f"Rows: {len(rows)}",
                f"PNG files present: {png_count}",
                f"User visually verified rows: {verified}",
                "",
                "Primary files:",
                "",
                "- `source_layer_master_table.json`",
                "- `source_layer_master_table.csv`",
                "- `source_layer_master_table.tsv`",
                "- `source_layer_full_ledger.json`",
                "- `source_layer_full_ledger.csv`",
                "- `source_layer_full_ledger.tsv`",
                "- `source_layer_attribution_check_20260520.json`",
                "- `png_layers/`",
                "- `engine_code_evidence.md`",
                "",
            ]
        )
        + "\n"
    )


def write_engine_evidence(output_dir: Path) -> None:
    (output_dir / "engine_code_evidence.md").write_text(
        "\n".join(
            [
                "# Upstream Engine Code Evidence",
                "",
                f"Repo: {ENGINE_EVIDENCE['upstream_repo']}",
                "",
                "## File Identity",
                "",
                ENGINE_EVIDENCE["file_identity_code"],
                "",
                "The A/H/S/W tuple names the source XP combo file. It does not provide",
                "a deterministic raw layer index for armor/helmet/shield/weapon.",
                "",
                "## Layer Merge",
                "",
                ENGINE_EVIDENCE["layer_merge_code"],
                "",
                "The engine merges layers in file order. It does not assign semantic",
                "role names to L3/L4/L5/etc.",
                "",
                "## Compiler Rule",
                "",
                ENGINE_EVIDENCE["role_rule"],
                "",
                "## Semantic Identifier Helper",
                "",
                SEMANTIC_DICT_EVIDENCE,
                "",
            ]
        )
        + "\n"
    )


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_state = Counter(str(r.get("full_ledger_review_state", "")) for r in rows)
    by_authority = Counter(str(r.get("full_ledger_authority_state", "")) for r in rows)
    by_family = Counter(str(r.get("source_family", "")) for r in rows)
    by_attribution_confidence = Counter(
        str(r.get("layer_attribution_confidence", "")) for r in rows
    )
    by_attribution_authority = Counter(
        str(r.get("layer_attribution_authority_state", "")) for r in rows
    )
    return {
        "schema": "asciicker.source_layer_master.summary.v1",
        "row_count": len(rows),
        "png_count": sum(1 for r in rows if str(r.get("master_png_exists")) == "True"),
        "review_state_counts": dict(sorted(by_state.items())),
        "authority_state_counts": dict(sorted(by_authority.items())),
        "attribution_confidence_counts": dict(sorted(by_attribution_confidence.items())),
        "attribution_authority_counts": dict(sorted(by_attribution_authority.items())),
        "family_counts": dict(sorted(by_family.items())),
        "engine_evidence": ENGINE_EVIDENCE,
        "semantic_dict_identifier_evidence": SEMANTIC_DICT_EVIDENCE,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    full_audit_path = args.audit_dir / "full_source_layer_ledger_20260520.json"
    audit_data = load_json(full_audit_path)
    audit_rows = normalized_rows(audit_data)
    ledger = load_json(SOURCE_LEDGER)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = build_master_rows(
        audit_rows,
        ledger,
        audit_dir=args.audit_dir,
        output_dir=args.output_dir,
    )

    payload = {
        "schema": "asciicker.source_layer_master_table.v1",
        "source_audit": str(full_audit_path),
        "source_ledger": str(SOURCE_LEDGER.relative_to(REPO_ROOT)),
        "engine_evidence": ENGINE_EVIDENCE,
        "semantic_dict_identifier_evidence": SEMANTIC_DICT_EVIDENCE,
        "rows": rows,
    }
    write_json(args.output_dir / "source_layer_master_table.json", payload)
    write_table(args.output_dir / "source_layer_master_table.csv", rows, ",")
    write_table(args.output_dir / "source_layer_master_table.tsv", rows, "\t")
    write_json(args.output_dir / "source_layer_master_summary.json", build_summary(rows))
    write_full_layer_ledger(args.output_dir, rows)
    write_human_verified_attributions(args.output_dir, rows)
    write_mounted_helmet_shield_trace(args.output_dir, rows)
    write_attribution_check_report(args.output_dir, rows)
    write_readme(args.output_dir, rows)
    write_engine_evidence(args.output_dir)

    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.relative_to(REPO_ROOT)),
                "rows": len(rows),
                "png_files": sum(
                    1 for r in rows if str(r.get("master_png_exists")) == "True"
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
