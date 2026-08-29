# Ad hoc script: Update Phase 3 source-layer visual verification rows
# Created: 2026-05-20
# Canonical gap: <describe what tool should own this>

import csv
import json
from pathlib import Path
from collections import Counter

REPO = Path('/Users/r/Downloads/asciicker-Y9-2')
DESKTOP = Path('/Users/r/Desktop/bundle_layer_audit_20260520')
TODAY = '2026-05-20'
PNG_ROOT = DESKTOP / 'png_layers'

# User-confirmed current truth. AHSW selects the source combo file only;
# layer meaning is evidence-derived from upstream merge behavior + visual/cell proof.
CONFIRMED = {
    ('sprites/attack-0011.xp', 3): {
        'png': 'attack-0011_L3.png',
        'meaning': 'weapon_sword_swoosh',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': [],
        'compiler_action': 'not_compiler_authority_effect_layer_without_explicit_target_mapping',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'cyan swoosh/motion glyphs: lower-half 0xdc x18, upper-half 0xdf x18, left/right halfblocks 0xdd/0xde x14; all fg cyan',
        'evidence': 'User verified attack-0011_L3 as weapon/sword swing swoosh, not body/armor/helmet. Upstream sprite.cpp treats final cyan layer as special swoosh overlay during merge.',
    },
    ('sprites/bigbee-0011.xp', 5): {
        'png': 'bigbee-0011_L5.png',
        'meaning': 'composite_source:rider_body_shield_fragment',
        'source_kind': 'composite_source',
        'status': 'LEDGER_ONLY',
        'target_ids': [],
        'compiler_action': 'not_compiler_authority_composite_context_until_target_mapping_exists',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'rider/body halfblock glyph family plus shield marker @ x16; no armor shade glyphs and no helmet-only head cap',
        'evidence': 'User corrected prior weapon guess: bigbee-0011_L5 is rider/body overlay with shield fragments, no chest armor, no helm; excludes hair so it can combine with helm.',
    },
    ('sprites/bigbee-0110.xp', 4): {
        'png': 'bigbee-0110_L4.png',
        'meaning': 'helmet',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': ['bigbee_helmet_regular'],
        'compiler_action': 'candidate_only_until_phase3_mapping_promotes_this_exact_receipt',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'sparse head/helmet cap signature: ^ x9 plus symmetric top halfblock marks; no @ shield and no 0xb1 armor shade',
        'evidence': 'User verified bigbee-0110_L4 as helmet. Layer order is combo-specific; AHSW confirms H/S tuple only, not layer index.',
    },
    ('sprites/bigbee-1010.xp', 4): {
        'png': 'bigbee-1010_L4.png',
        'meaning': 'armor',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': ['bigbee_armor_regular'],
        'compiler_action': 'candidate_only_until_phase3_mapping_promotes_this_exact_receipt',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'dense chest/armor signature: shade 0xb1 x46 plus torso halfblock distribution; no @ shield and no sparse helmet cap',
        'evidence': 'User verified bigbee-1010_L4 as armor/chest. Layer order is combo-specific; AHSW confirms A/S tuple only, not layer index.',
    },
    ('sprites/bigbee-1110.xp', 3): {
        'png': 'bigbee-1110_L3.png',
        'meaning': 'rider_body_context',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': [],
        'compiler_action': 'not_equipment_source_body_context_only',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'rider/body halfblock glyph family (0xdc/0xdf/0xde/0xdd, quotes, v) and no @/0xb1 equipment signature',
        'evidence': 'User verified bigbee-1110_L3 as rider/body-context, not equipment. Bigbee rider/body layers are player/rider stamps, not vague metadata.',
    },
    ('sprites/player-1100.xp', 3): {
        'png': 'player-1100_L3.png',
        'meaning': 'armor',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': ['player_armor_regular'],
        'compiler_action': 'supporting_combo_receipt_not_primary_phase3_mapping',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'dense chest/armor signature: shade 0xb1 x152 with torso halfblocks; no @ shield and no weapon slash/BEL',
        'evidence': 'User verified player-1100_L3 as armor/chest.',
    },
    ('sprites/player-1100.xp', 4): {
        'png': 'player-1100_L4.png',
        'meaning': 'helmet',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': ['player_helmet_regular'],
        'compiler_action': 'supporting_combo_receipt_not_primary_phase3_mapping',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'head/helmet cap signature: sparse top halfblocks and spaces; no @ shield, no armor shade, no weapon slash/BEL',
        'evidence': 'User verified player-1100_L4 as helmet/head.',
    },
    ('sprites/player-1110.xp', 3): {
        'png': 'player-1110_L3.png',
        'meaning': 'composite_source:armor_shield_context',
        'source_kind': 'composite_source',
        'status': 'LEDGER_ONLY',
        'target_ids': [],
        'compiler_action': 'not_clean_weapon_source_composite_context_only',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'armor shade 0xb1 x126 plus brown shield/context pixels; no sword slash/backslash/BEL weapon glyph family',
        'evidence': 'User verified player-1110_L3 as armor+shield context, no sword; brown bits are shield/context, not weapon.',
    },
    ('sprites/plydie-0010.xp', 3): {
        'png': 'plydie-0010_L3.png',
        'meaning': 'shield',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': ['plydie_shield_regular'],
        'compiler_action': 'candidate_only_no_current_phase3_target_row',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'shield signature: @ x25 with sideways/brown shield-context pixels; no weapon slash/BEL',
        'evidence': 'User verified plydie-0010_L3 as shield bit for plydie, sideways with brown bits.',
    },
    ('sprites/wolack-0011.xp', 4): {
        'png': 'wolack-0011_L4.png',
        'meaning': 'shield',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': ['wolack_shield_regular'],
        'compiler_action': 'candidate_only_no_current_phase3_target_row',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'shield signature: @ x48 plus shield orientation glyphs v/^; no sword slash/BEL',
        'evidence': 'User corrected prior weapon guess: wolack-0011_L4 is shield, probably correct.',
    },
    ('sprites/wolfie-0110.xp', 3): {
        'png': 'wolfie-0110_L3.png',
        'meaning': 'helmet',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': ['wolfie_helmet_regular'],
        'compiler_action': 'supporting_combo_receipt_not_primary_phase3_mapping',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'helmet signature: ^ x18 plus sparse head cap halfblocks; identical glyph signature to wolfie-1110_L3; no 0xb1 armor shade',
        'evidence': 'User corrected prior armor guess: wolfie-0110_L3 is helmet/head bit only.',
    },
    ('sprites/wolfie-1000.xp', 4): {
        'png': 'wolfie-1000_L4.png',
        'meaning': 'armor',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': ['wolfie_armor_regular'],
        'compiler_action': 'supporting_combo_receipt_not_primary_phase3_mapping',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'chest armor signature: dense 0xb1 shade x126 plus torso-distributed halfblocks; not sparse helmet',
        'evidence': 'User corrected prior helmet guess: wolfie-1000_L4 is chest armor.',
    },
    ('sprites/wolfie-1110.xp', 3): {
        'png': 'wolfie-1110_L3.png',
        'meaning': 'helmet',
        'source_kind': 'raw_layer',
        'status': 'LEDGER_ONLY',
        'target_ids': ['wolfie_helmet_regular'],
        'compiler_action': 'supporting_combo_receipt_not_primary_phase3_mapping',
        'authority_state': 'USER_VISUAL_VERIFIED_LEDGER_ONLY',
        'review_state': 'USER_VISUALLY_VERIFIED',
        'glyphs': 'helmet/head signature: ^ x18 plus sparse head cap halfblocks; identical to wolfie-0110_L3; no armor shade and no weapon glyph family',
        'evidence': 'User corrected prior weapon/armor guess: wolfie-1110_L3 is head/helmet bit only.',
    },
}

CONTRADICTS = {
    ('sprites/bigbee-0011.xp', 5): 'slot_affinity=weapon for L5 is stale; user visual evidence shows composite rider/body+shield fragment',
    ('sprites/bigbee-0110.xp', 4): 'slot_affinity=shield/armor-derived source map does not own role; user visual evidence shows helmet',
    ('sprites/bigbee-1010.xp', 4): 'slot_affinity=head/helmet-derived source map does not own role; user visual evidence shows armor/chest',
    ('sprites/bigbee-1110.xp', 3): 'slot_affinity=weapon is stale for L3; user visual evidence shows rider/body context',
    ('sprites/player-1110.xp', 3): 'slot_affinity=weapon is stale for L3; user visual evidence shows armor+shield context and no sword',
    ('sprites/plydie-0010.xp', 3): 'slot_affinity=weapon is stale for L3; user visual evidence shows shield',
    ('sprites/wolack-0011.xp', 4): 'slot_affinity=weapon is stale for L4; user visual evidence shows shield',
    ('sprites/wolfie-0110.xp', 3): 'slot_affinity=armor is stale for L3; user visual evidence shows helmet',
    ('sprites/wolfie-1000.xp', 4): 'slot_affinity=head is stale for L4; user visual evidence shows armor/chest',
    ('sprites/wolfie-1110.xp', 3): 'slot_affinity=weapon is stale for L3; user visual evidence shows helmet/head',
}

IMPORTANT_WARNINGS = [
    'AHSW filename digits are canonical upstream tuple selectors for XP file identity only; raw layer order is combo-specific and not deterministic from AHSW.',
    'Every compiler-consumable role source requires explicit source_xp_path + raw_layer_index + evidence; do not infer raw layer meaning from digit position.',
    'Every user-verified visual row must keep its rendered PNG path so the review can be reopened without rerendering.',
]


def load_json_rows(path):
    data = json.loads(path.read_text())
    if isinstance(data, dict) and isinstance(data.get('rows'), list):
        return data, data['rows']
    if isinstance(data, list):
        return data, data
    raise TypeError(f'{path} has unsupported JSON shape')


def dump_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def append_note(old, note):
    old = old or ''
    if note in old:
        return old
    return (old + (' | ' if old else '') + note)


def desktop_key(row):
    return (row.get('source_xp_path') or row.get('xp_path'), int(row.get('layer_index')))


def update_desktop_rows(rows):
    hits = 0
    for row in rows:
        try:
            key = desktop_key(row)
        except Exception:
            continue
        info = CONFIRMED.get(key)
        if not info:
            continue
        hits += 1
        png_path = str(PNG_ROOT / info['png'])
        row['layer_png'] = row.get('layer_png') or png_path
        row['user_visual_verification'] = f'USER_VISUALLY_VERIFIED {TODAY}'
        row['user_visual_semantic_label'] = info['meaning']
        row['user_visual_glyph_identifiers'] = info['glyphs']
        row['full_ledger_semantic_label'] = info['meaning']
        row['full_ledger_review_state'] = info['review_state']
        row['full_ledger_confidence'] = 'manual_visual_review'
        row['full_ledger_authority_state'] = info['authority_state']
        row['full_ledger_compiler_action'] = info['compiler_action']
        row['full_ledger_target_source_ids'] = ','.join(info['target_ids'])
        row['ledger_meanings'] = info['meaning']
        row['ledger_statuses'] = info['status']
        row['ledger_confidences'] = 'manual_visual_review'
        row['full_ledger_blockers'] = 'none_for_visual_label; compiler_authority_requires_phase3_mapping_promotion' if info['target_ids'] else 'none_for_visual_label; no_current_compiler_target'
        row['full_ledger_evidence'] = append_note(row.get('full_ledger_evidence'), f"USER_VISUALLY_VERIFIED {TODAY}: {info['evidence']} Glyph identifiers: {info['glyphs']}. PNG: {png_path}")
        row['evidence'] = append_note(row.get('evidence'), f"USER_VISUALLY_VERIFIED {TODAY}: {info['meaning']}; PNG {png_path}")
    return hits


def write_table(path, rows, delimiter):
    old_fields = []
    if path.exists():
        with path.open(newline='') as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            old_fields = reader.fieldnames or []
    fields = list(old_fields)
    for row in rows:
        for k in row:
            if k not in fields:
                fields.append(k)
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=delimiter, lineterminator='\n', extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def update_delimited(path, delimiter):
    if not path.exists():
        return None
    with path.open(newline='') as f:
        rows = list(csv.DictReader(f, delimiter=delimiter))
    hits = update_desktop_rows(rows)
    write_table(path, rows, delimiter)
    return hits


def make_repo_row(source_xp_path, raw_layer_index, info):
    row = {
        'source_xp_path': source_xp_path,
        'raw_layer_index': raw_layer_index,
        'meaning': info['meaning'],
        'source_kind': info['source_kind'],
        'status': info['status'],
        'confidence': 'manual_visual_review',
        'evidence': [
            f"USER_VISUALLY_VERIFIED {TODAY}: {info['evidence']}",
            f"Glyph identifiers: {info['glyphs']}",
            f"PNG: /Users/r/Desktop/bundle_layer_audit_20260520/png_layers/{info['png']}",
            'Engine/source rule: upstream game.cpp A/H/S/W tuple selects combo file identity; upstream sprite.cpp merges raw layers in file order, with final cyan layer treated as swoosh. Therefore raw layer index is evidence-derived, not AHSW-derived.',
        ],
        'allowed_as_source_for': info['target_ids'],
        'notes': 'User visual verification row. Ledger-only unless a Phase 3 mapping row explicitly promotes this exact source_xp_path/raw_layer_index receipt.',
    }
    if (source_xp_path, raw_layer_index) in CONTRADICTS:
        row['contradicts'] = [{
            'file': f"docs/research/ascii/semantic_maps/source_overlay_domains/{Path(source_xp_path).stem}-source-overlay-domain.json",
            'claim': CONTRADICTS[(source_xp_path, raw_layer_index)],
        }]
    return row


def update_repo_ledger():
    path = REPO / 'docs/research/ascii/semantic_maps/source_layer_ledger.json'
    data = json.loads(path.read_text())
    rows = data['rows']
    warnings = data.setdefault('known_global_hazards', [])
    for warning in IMPORTANT_WARNINGS:
        if warning not in warnings:
            warnings.append(warning)
    rules = data.setdefault('authority_rules', [])
    rule = 'Layer order is not deterministic from AHSW; AHSW selects tuple/file identity only, and raw layer role requires ledger evidence plus rendered PNG/cell proof.'
    if rule not in rules:
        rules.append(rule)
    by = {(r.get('source_xp_path'), r.get('raw_layer_index')): r for r in rows}
    added = updated = 0
    for key, info in CONFIRMED.items():
        source_xp_path, raw_layer_index = key
        new_row = make_repo_row(source_xp_path, raw_layer_index, info)
        existing = by.get(key)
        if existing:
            # Preserve any existing broader notes while replacing stale semantic authority fields.
            existing.update(new_row)
            updated += 1
        else:
            rows.append(new_row)
            added += 1
    rows.sort(key=lambda r: (str(r.get('source_xp_path')), -1 if r.get('raw_layer_index') is None else int(r.get('raw_layer_index'))))
    dump_json(path, data)
    return added, updated


def main():
    full_json = DESKTOP / 'full_source_layer_ledger_20260520.json'
    data, rows = load_json_rows(full_json)
    full_hits = update_desktop_rows(rows)
    dump_json(full_json, data)
    write_table(DESKTOP / 'full_source_layer_ledger_20260520.csv', rows, ',')
    write_table(DESKTOP / 'full_source_layer_ledger_20260520.tsv', rows, '\t')

    csv_hits = update_delimited(DESKTOP / 'blocked_layer_review_queue_20260520.csv', ',')
    tsv_hits = update_delimited(DESKTOP / 'blocked_layer_review_queue_20260520.tsv', '\t')

    added, updated = update_repo_ledger()

    summary = {
        'created': f'{TODAY}T00:00:00-04:00',
        'purpose': 'Record user visual verification corrections for Phase 3 source-layer audit rows.',
        'rule': 'AHSW file tuple is canonical file identity only; layer order is not deterministic from AHSW and must be proven per source_xp_path/raw_layer_index.',
        'verified_rows': [
            {
                'source_xp_path': k[0],
                'raw_layer_index': k[1],
                'png': str(PNG_ROOT / v['png']),
                'meaning': v['meaning'],
                'source_kind': v['source_kind'],
                'glyph_identifiers': v['glyphs'],
            }
            for k, v in sorted(CONFIRMED.items())
        ],
        'desktop_full_rows_updated': full_hits,
        'blocked_queue_csv_rows_updated': csv_hits,
        'blocked_queue_tsv_rows_updated': tsv_hits,
        'repo_ledger_rows_added': added,
        'repo_ledger_rows_updated': updated,
    }
    dump_json(DESKTOP / 'user_visual_verification_updates_20260520.json', summary)
    print(json.dumps(summary, indent=2))

if __name__ == '__main__':
    main()
