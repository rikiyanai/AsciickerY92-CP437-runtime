#!/usr/bin/env python3
"""Option (B) implementation: switch every mounted row in
engine/actor_visual_profile_table.generated.h from the broken
3-layer decomposed wrappers to a single-layer upstream-monolith reference
per game.cpp:3253-3267.

Per upstream LoadSprites():
  - wolfie[a][h][s][w]        loaded from wolfie-AHSW.xp (mounted idle/walk)
  - wolfie_attack[a][h][s][w] loaded from wolack-AHSW.xp (mounted melee attack)
  - bigbee[a][h][s][w]        loaded from bigbee-AHSW.xp (mounted idle/walk)
  - bigbee_attack[]           = 0 in upstream (no mounted-bee melee)

Per upstream GetSprite() selector (game.cpp:3253-3290):
  - MOUNT::WOLF + ACTION::NONE          -> wolfie[]
  - MOUNT::WOLF + ACTION::ATTACK + CROSSBOW -> wolfie[]   (no attack pose)
  - MOUNT::WOLF + ACTION::ATTACK + other -> wolfie_attack[] = wolack
  - MOUNT::WOLF + ACTION::FALL/DEAD/STAND -> wolfie_fall[] = 0 (upstream)
  - MOUNT::BEE  + same shape

This script writes:
  1. New source XP entries (UPSTREAM_AUTHORED kind) for each existing
     wolfie-AHSW.xp / wolack-AHSW.xp / bigbee-AHSW.xp file.
  2. Matching BODY masks (ALL_VISIBLE method) for each new source.
  3. Rewrites every kCompiledActorVisualRowLayers_N used by a mounted row
     into a single-layer BODY pointing at the matched monolith.
  4. Updates each mounted row's timeline_source_xp_index to the same.
  5. Updates LoadActorVisualProfileSourceSprite in actor_visual_profile_runtime.h
     to honor kind == UPSTREAM_AUTHORED by passing merge_extra_layers=true
     to LoadSpriteLayer (matches upstream LoadSpriteBP).

Death rows: upstream wolfie_fall/bigbee_fall = 0. Y9-2 has no equivalent
authoring restored at this commit. Death rows are left pointing at their
current layer sets; they will render whatever the current sets produce
(currently broken, same as before this script). Closure requires a
separate authoring pass.

Scope of this script:
  - REWRITE: idle_walk + attack mounted rows.
  - KEEP AS-IS: death mounted rows, unmounted rows of any kind.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GEN = REPO / "engine" / "actor_visual_profile_table.generated.h"
RT = REPO / "engine" / "actor_visual_profile_runtime.h"
ASSETS = REPO / "assets" / "sprites"


def derive_ahsw_from_row_name(name: str) -> str:
    """name = 'normal_player.{action}.default.{mount}[.{slot1}][.{slot2}]...'

    Map slot tokens to AHSW digits.

    Returns the AHSW string (e.g. '1101') or None if unknown.
    """
    armor = helmet = shield = weapon = 0
    parts = name.split('.')
    # parts[0]='normal_player', parts[1]=action, parts[2]='default', parts[3]=mount, rest=loadout
    loadout = parts[4:]
    for tok in loadout:
        if tok == "normal_armour":
            armor = 1
        elif tok == "normal_helmet":
            helmet = 1
        elif tok in ("shield_item", "normal_shield"):
            shield = 1
        elif tok == "normal_sword":
            weapon = 1
        elif tok == "weapon_crossbow":
            weapon = 2
        else:
            # Unknown slot token — fail closed
            return None
    return f"{armor}{helmet}{shield}{weapon}"


def derive_monolith_for_row(name: str) -> tuple[str, str] | None:
    """Return (id, asset_path) of the monolith XP for this row, or None.

    Per upstream GetSprite():
      idle_walk + wolf_mount      -> wolfie-AHSW.xp
      attack    + wolf_mount + W=2 (crossbow) -> wolfie-AHSW.xp
      attack    + wolf_mount + W=1 (sword)    -> wolack-AHSW.xp
      attack    + wolf_mount + W=0 (unarmed)  -> wolack-AHS0.xp (does not exist upstream;
                                                  upstream wolfie_attack[c][a][h][s][NONE]=0)
      idle_walk + bee_mount       -> bigbee-AHSW.xp
      attack    + bee_mount + W=2 -> bigbee-AHSW.xp
      attack    + bee_mount + other -> bigbee_attack[] = 0 upstream; not authored here
    """
    parts = name.split('.')
    action = parts[1]
    mount = parts[3]
    ahsw = derive_ahsw_from_row_name(name)
    if ahsw is None:
        return None
    w_digit = int(ahsw[-1])

    if mount == "wolf_mount":
        if action == "idle_walk":
            family = "wolfie"
        elif action == "attack":
            if w_digit == 2:
                family = "wolfie"  # upstream special-case for crossbow attack
            else:
                family = "wolack"
        elif action == "death":
            # Per user 2026-05-22: default mounted death to unmounted plydie pose.
            # Upstream wolfie_fall = 0; player-dead body is what shows. Mount
            # gameplay separately spawns a world-item mountable for the wolf.
            family = "plydie"
        else:
            return None
    elif mount == "bee_mount":
        if action == "idle_walk":
            family = "bigbee"
        elif action == "attack":
            if w_digit == 2:
                family = "bigbee"  # crossbow uses idle sheet (upstream selector)
            elif w_digit == 1:
                # Bee+sword attack: upstream bigbee_attack = 0. User 2026-05-22
                # via state_FINAL_20260521-163326.json: bigbee-???1.xp exists as
                # the idle bee+sword-equipped sheet. Use that for sword attack
                # too — same shape as crossbow-uses-idle precedent.
                family = "bigbee"
            else:
                return None  # bee+attack+unarmed — no asset path
        elif action == "death":
            family = "plydie"  # same plydie default
        else:
            return None
    else:
        return None

    asset = f"assets/sprites/{family}-{ahsw}.xp"
    if not (REPO / asset).exists():
        return None
    sid = f"{family}_{ahsw}_monolith"
    return (sid, asset)


# ────────────────────────────────────────────────────────────────────────
# Source XP table mutation
# ────────────────────────────────────────────────────────────────────────


def rewrite_table(generated_text: str) -> tuple[str, dict[str, int]]:
    """Returns (rewritten_text, stats)."""
    stats = {
        "monolith_sources_added": 0,
        "monolith_masks_added": 0,
        "mounted_rows_rewritten": 0,
        "death_rows_left_alone": 0,
        "layer_sets_rewritten": 0,
    }

    text = generated_text

    # Step 1: collect existing source XPs to find next free index
    src_table_re = re.compile(
        r'(static constexpr ActorVisualCompiledSourceXp kActorVisualSourceXps\[\] = \{)(.*?)(\n\};)',
        re.S,
    )
    m = src_table_re.search(text)
    if not m:
        raise SystemExit("could not locate kActorVisualSourceXps[]")
    head, body, tail = m.group(1), m.group(2), m.group(3)
    src_entries = re.findall(
        r'\{"([^"]+)",\s*"([^"]+)",\s*(\d+),\s*(ACTOR_VISUAL_SOURCE_XP_KIND_\w+)\}',
        body,
    )
    src_by_id = {e[0]: i for i, e in enumerate(src_entries)}
    next_src_idx = len(src_entries)

    # Step 2: collect all mounted rows + derive monolith per row
    row_re = re.compile(
        r'(\{\s*"(normal_player\.[^"]+)",\s*\{[^{}]*\{[^}]*\},\s*\{[^}]*\},\s*\{[^}]*\},\s*\},\s*)'
        r'(\d+)'                                  # timeline_source_xp_index
        r'(,\s*ACTOR_VISUAL_PLAYBACK_DIRECTION_\w+,\s*\d+,\s*\d+,\s*\{[^}]*\},\s*)'
        r'kCompiledActorVisualRowLayers_(\d+)'    # layer set idx
        r'(,\s*\},)',
        re.S,
    )

    # Build mapping: monolith_id -> source_idx
    needed_monoliths: dict[str, str] = {}  # id -> asset
    row_plan: list[tuple[int, str, str, str | None]] = []  # (match_start, row_name, action, monolith_id_or_None)
    for rm in row_re.finditer(text):
        row_name = rm.group(2)
        action = row_name.split('.')[1]
        mount = row_name.split('.')[3] if len(row_name.split('.')) > 3 else ''
        if mount not in ("wolf_mount", "bee_mount"):
            continue  # unmounted, leave alone
        mono = derive_monolith_for_row(row_name)
        if mono is None:
            row_plan.append((rm.start(), row_name, action, None))
            if action == "death":
                stats["death_rows_left_alone"] += 1
            continue
        sid, asset = mono
        needed_monoliths[sid] = asset
        row_plan.append((rm.start(), row_name, action, sid))

    # Step 3: append new source entries for any monolith not yet present
    new_src_lines = []
    for sid, asset in sorted(needed_monoliths.items()):
        if sid not in src_by_id:
            src_by_id[sid] = next_src_idx
            next_src_idx += 1
            new_src_lines.append(
                f'    {{"{sid}", "{asset}", 2, ACTOR_VISUAL_SOURCE_XP_KIND_UPSTREAM_AUTHORED}},'
            )
            stats["monolith_sources_added"] += 1
    if new_src_lines:
        text = text[:m.start()] + head + body.rstrip() + '\n' + '\n'.join(new_src_lines) + '\n' + tail + text[m.end():]

    # Step 4: collect existing masks to find next free index + check for monolith masks
    mask_table_re = re.compile(
        r'(static constexpr ActorVisualCompiledSemanticMask kActorVisualSemanticMasks\[\] = \{)(.*?)(\n\};)',
        re.S,
    )
    mm = mask_table_re.search(text)
    if not mm:
        raise SystemExit("could not locate kActorVisualSemanticMasks[]")
    mhead, mbody, mtail = mm.group(1), mm.group(2), mm.group(3)
    mask_entries = re.findall(
        r'\{"([^"]+)",\s*ACTOR_VISUAL_SEMANTIC_MASK_METHOD_\w+,\s*ACTOR_VISUAL_LAYER_ROLE_\w+,\s*(\d+),',
        mbody,
    )
    mask_by_id = {e[0]: i for i, e in enumerate(mask_entries)}
    next_mask_idx = len(mask_entries)

    # Step 5: append BODY masks for each monolith
    new_mask_lines = []
    monolith_mask_idx: dict[str, int] = {}
    for sid, asset in sorted(needed_monoliths.items()):
        mask_id = f"{sid}__L2__body"
        if mask_id in mask_by_id:
            monolith_mask_idx[sid] = mask_by_id[mask_id]
            continue
        src_idx = src_by_id[sid]
        new_mask_lines.append(
            f'    {{"{mask_id}", ACTOR_VISUAL_SEMANTIC_MASK_METHOD_ALL_VISIBLE, '
            f'ACTOR_VISUAL_LAYER_ROLE_BODY, {src_idx}, 2, 0, 0}},'
        )
        monolith_mask_idx[sid] = next_mask_idx
        mask_by_id[mask_id] = next_mask_idx
        next_mask_idx += 1
        stats["monolith_masks_added"] += 1
    if new_mask_lines:
        mm = mask_table_re.search(text)
        mhead, mbody, mtail = mm.group(1), mm.group(2), mm.group(3)
        text = text[:mm.start()] + mhead + mbody.rstrip() + '\n' + '\n'.join(new_mask_lines) + '\n' + mtail + text[mm.end():]

    # Step 6: replace each mounted row's timeline_source_xp_index + create
    #         new dedicated layer set, OR overwrite the existing layer set
    #         in-place. Easier: emit per-row dedicated layer sets and rewrite
    #         row to point at the new set.
    #
    # We'll iterate rows again (text now has new sources + masks; offsets shifted),
    # parse each row, write a unique kCompiledActorVisualRowLayers_<row_index>_mono
    # constexpr at the END of the existing layer-set block, and rewrite the row
    # to use it.
    #
    # Simpler approach: redirect the row to use the EXISTING layer-set slot via
    # a parallel array, but C++ requires constexpr identifiers. So instead we
    # append a new set per monolith (one per (mount, action, AHSW) unique key)
    # and reuse it across rows that share the same monolith.

    # Build a per-monolith unique layer-set name + emit constexpr declarations
    # Insertion point: after the LAST kCompiledActorVisualRowLayers_* block
    # (numeric OR _mono_ named). Both forms must come AFTER the shared frame
    # map declaration to avoid use-before-declaration errors. The frame map
    # is emitted once on first run; subsequent runs need to insert AFTER any
    # existing _mono_* blocks (which reference the frame map) so the ordering
    # stays definition -> reference.
    any_layer_set_re = re.compile(
        r'(static constexpr CompiledActorVisualLayer kCompiledActorVisualRowLayers_\w+\[\] = \{.*?\};)',
        re.S,
    )
    last_layer_set_match = None
    for m in any_layer_set_re.finditer(text):
        last_layer_set_match = m
    if last_layer_set_match is None:
        raise SystemExit("could not locate any kCompiledActorVisualRowLayers_* block")
    insertion_offset = last_layer_set_match.end()

    new_blocks = []
    monolith_layer_set_name: dict[str, str] = {}
    shared_frame_map_name = "kMonolithMountAllFramesIdentity"

    # Only emit the shared frame map if not already defined.
    has_shared_fm = (f"{shared_frame_map_name}[" in text)
    if not has_shared_fm:
        new_blocks.append(
            f"\nstatic constexpr uint16_t {shared_frame_map_name}[144] = {{\n"
            + "    " + ",\n    ".join(
                ", ".join(str(i + j) for j in range(8))
                for i in range(0, 144, 8)
            )
            + "\n};\n"
        )

    for sid, asset in sorted(needed_monoliths.items()):
        ls_name = f"kCompiledActorVisualRowLayers_mono_{sid}"
        monolith_layer_set_name[sid] = ls_name
        src_idx = src_by_id[sid]
        mask_idx = monolith_mask_idx[sid]
        # Skip if this layer-set is already defined (idempotent re-run).
        if f"{ls_name}[" in text:
            continue
        new_blocks.append(
            f"\nstatic constexpr CompiledActorVisualLayer {ls_name}[] = {{\n"
            f"    {{0, ACTOR_VISUAL_LAYER_ROLE_BODY, 2, false, "
            f"{src_idx}, {mask_idx}, 0, {shared_frame_map_name}, 144}},\n"
            f"}};\n"
        )

    if new_blocks:
        insertion_blob = "".join(new_blocks)
        text = text[:insertion_offset] + insertion_blob + text[insertion_offset:]

    # Step 7: rewrite each mounted row's timeline + layer-set ref
    def rewrite_row(match: re.Match) -> str:
        row_name = match.group(2)
        action = row_name.split('.')[1]
        mount = row_name.split('.')[3] if len(row_name.split('.')) > 3 else ''
        if mount not in ("wolf_mount", "bee_mount"):
            return match.group(0)
        mono = derive_monolith_for_row(row_name)
        if mono is None:
            return match.group(0)
        sid, _asset = mono
        if sid not in src_by_id or sid not in monolith_layer_set_name:
            return match.group(0)
        new_timeline = src_by_id[sid]
        new_ls = monolith_layer_set_name[sid]
        # Preserve playback metadata block (group 4), replace timeline (group 3) and
        # the layer set name suffix.
        stats["mounted_rows_rewritten"] += 1
        head = match.group(1)
        playback = match.group(4)
        tail = match.group(6)
        return f"{head}{new_timeline}{playback}{new_ls}{tail}"

    text = row_re.sub(rewrite_row, text)
    stats["layer_sets_rewritten"] = len(monolith_layer_set_name)

    return text, stats


# ────────────────────────────────────────────────────────────────────────
# Runtime loader: honor kind == UPSTREAM_AUTHORED via merge_extra_layers=true
# ────────────────────────────────────────────────────────────────────────


def patch_runtime(text: str) -> str:
    """Rewrite the LoadSpriteLayer call in LoadActorVisualProfileSourceSprite
    to pass merge_extra_layers=true when kind == UPSTREAM_AUTHORED.

    Idempotent: if the patch was already applied (detected by the marker
    comment), this is a no-op so the script can be re-run safely.
    """
    if "FL-3912 option-B: UPSTREAM_AUTHORED monoliths" in text:
        return text  # already patched
    old = (
        '\tSprite* sprite = LoadSpriteLayer(\n'
        '\t\tfull_path,\n'
        '\t\tsprite_name,\n'
        '\t\tsource_layer_index,\n'
        '\t\t0,\n'
        '\t\ttrue,\n'
        '\t\ttrue,\n'
        '\t\tfalse);'
    )
    new = (
        '\t// FL-3912 option-B: UPSTREAM_AUTHORED monoliths require L3+ merge\n'
        '\t// matching upstream LoadSpriteBP (sprite.cpp). DERIVED_SINGLEROLE\n'
        '\t// and PIPELINE_DECOMPOSED stay at single-layer load.\n'
        '\tconst bool merge_extra =\n'
        '\t\t(source_xp.kind == ACTOR_VISUAL_SOURCE_XP_KIND_UPSTREAM_AUTHORED);\n'
        '\tSprite* sprite = LoadSpriteLayer(\n'
        '\t\tfull_path,\n'
        '\t\tsprite_name,\n'
        '\t\tsource_layer_index,\n'
        '\t\t0,\n'
        '\t\ttrue,\n'
        '\t\ttrue,\n'
        '\t\tmerge_extra);'
    )
    if old not in text:
        old_alt = old.expandtabs(4)
        if old_alt in text:
            text = text.replace(old_alt, new.expandtabs(4))
        else:
            raise SystemExit("could not locate LoadSpriteLayer call to patch")
    else:
        text = text.replace(old, new)
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print stats only")
    args = ap.parse_args()

    gen_text = GEN.read_text()
    rt_text = RT.read_text()

    new_gen, stats = rewrite_table(gen_text)
    new_rt = patch_runtime(rt_text)

    print("=== rewrite stats ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  generated.h delta: {len(new_gen) - len(gen_text):+d} bytes")
    print(f"  runtime.h delta:    {len(new_rt) - len(rt_text):+d} bytes")

    if args.dry_run:
        print("\n--dry-run: no files written")
        return

    GEN.write_text(new_gen)
    RT.write_text(new_rt)
    print(f"\nwrote {GEN}")
    print(f"wrote {RT}")


if __name__ == "__main__":
    main()
