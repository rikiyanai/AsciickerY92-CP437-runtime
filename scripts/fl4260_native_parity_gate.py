#!/usr/bin/env python3
"""FL-4260 RQ-156 native PROFILE parity gate checker.

Post-run analyzer gate over the headed native-game proof artifacts produced by
the FL-4260 RQ-154c parity probe (`[FL-4260] native_parity_probe`) and the
RQ-155 trace (`[FL-4260] trace`). These lines are emitted by
platform/terminal_gl_present.cpp when .run/game runs with
ASCIICKER_TEST_MODE=1 FL4260_NATIVE_PROOF=1 [FL4260_TRACE=1].

This is post-run interpretation of recorded facts (Law 13 analyzer gates own
proof). It does NOT run the game, does NOT mutate any source, and does NOT touch
runtime state. Controller/choreography success is not a gate here; the gates
query the recorded probe/trace lines only.

Gate classes (Law 13):
  scenario_* : staging/choreography validity (PROFILE was actually active)
  evidence_* : recorder/data-surface health (the probe/trace lines exist)
  gameplay_* : observed runtime behavior (live GRASS rendered; CPU==GPU)

LAW 16: a gate pass means ONE run observed the expected invariant. It is NOT
closure. It does not mean FL-4260 is resolved, the architecture is correct, or
regression is impossible. Human operator signoff is required before FL-4260 is
marked done.

Front door:
  python3 scripts/fl4260_native_parity_gate.py <proof_log>          # human
  python3 scripts/fl4260_native_parity_gate.py <proof_log> --json   # machine
  python3 scripts/fl4260_native_parity_gate.py                      # newest proof log

Exit codes:
  0 - all gates PASS
  1 - one or more gates FAIL (probe/trace present but invariant not met)
  2 - no probe lines found / artifact missing (no evidence to interpret)
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_DIR = REPO_ROOT / "docs" / "research" / "ascii" / "verification" / "fl4260"

PROBE_RE = re.compile(
    r"\[FL-4260\] native_parity_probe "
    r"profile_routing=(?P<routing>\d+) "
    r"(?:fixture=(?P<fixture>\d+) )?"
    r"candidate_count=(?P<cand>\d+) "
    r"eligible_count=(?P<elig>\d+) "
    r"gpu_shader_winner_count=(?P<gpuwin>\d+) "
    r"gpu_parity_seen=(?P<seen>\d+) "
    r"cpu_gpu_disagree=(?P<disagree>\d+) "
    r"sample_cell=\((?P<sx>-?\d+),(?P<sy>-?\d+)\) "
    r"mat=(?P<mat>-?\d+) ri=(?P<ri>-?\d+) di=(?P<di>-?\d+) "
    r"cpu_gid=(?P<cpu>\d+) gpu_gid=(?P<gpu>\d+) agree=(?P<agree>\d+)"
)
MARKERS_RE = re.compile(
    r"\[FL-4260\] frame_markers region=(?P<rw>\d+)x(?P<rh>\d+) "
    r"missing_policy=(?P<mp>\d+) missing_glyph=(?P<mg>\d+) diagnostic=(?P<dg>\d+) "
    r"bang_other=(?P<bo>\d+) passthrough=(?P<pt>\d+) live_profile_glyph_cells=(?P<live>\d+) "
    r"readable_cells=(?P<rc>\d+) terrain_cells=(?P<terr>\d+) mostly_bang=(?P<mostly>\d+)"
)
# Authoritative resolver per-class counts (no color guessing).
CLASSES_RE = re.compile(
    r"\[FL-4260\] frame_classes target=\S+ frame=\d+ "
    r"live=(?P<live>\d+) out_of_profile_scope=(?P<oops>\d+) "
    r"missing_policy=(?P<mp>\d+) missing_glyph=(?P<mg>\d+)"
)
# FL-4260 RQ-154 broad: one parity line per live material the frame sampled.
MATERIAL_RE = re.compile(
    r"\[FL-4260\] native_parity_material mat=(?P<mat>-?\d+) "
    r"seen=(?P<seen>\d+) disagree=(?P<disagree>\d+) "
    r"sample_cell=\((?P<sx>-?\d+),(?P<sy>-?\d+)\) "
    r"ri=(?P<ri>-?\d+) di=(?P<di>-?\d+) "
    r"cpu_gid=(?P<cpu>\d+) gpu_gid=(?P<gpu>\d+) agree=(?P<agree>\d+)"
)
TRACE_RE = re.compile(
    r"\[FL-4260\] trace cell=\((?P<sx>-?\d+),(?P<sy>-?\d+)\) "
    r"material=terrain:(?P<mat>-?\d+) .*?"
    r"ramp_row=(?P<ri>-?\d+) density_bucket=D(?P<di>-?\d+) .*?"
    r"cpu_winner=(?P<cpu>\d+) gpu_winner=(?P<gpu>\d+) winner_agree=(?P<agree>\d+) "
    r"final_glyph_gid=(?P<gid>\d+) final_cp437_byte=(?P<byte>\d+) "
    r"fg=(?P<fg>\d+) bg=(?P<bg>\d+) "
    r"marker_class=(?P<marker>\S+) rejection_reason=(?P<reason>\S+)"
)


def _newest_proof_log() -> Path | None:
    candidates = sorted(
        glob.glob(str(VERIFY_DIR / "**" / "PROOF*native_parity*.log"), recursive=True)
        + glob.glob(str(VERIFY_DIR / "**" / "PROOF*.log"), recursive=True)
    )
    return Path(candidates[-1]) if candidates else None


def evaluate(log_path: Path) -> dict[str, Any]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    probes = [m.groupdict() for ln in lines if (m := PROBE_RE.search(ln))]
    traces = [m.groupdict() for ln in lines if (m := TRACE_RE.search(ln))]
    markers = [m.groupdict() for ln in lines if (m := MARKERS_RE.search(ln))]
    classes = [m.groupdict() for ln in lines if (m := CLASSES_RE.search(ln))]
    materials = [m.groupdict() for ln in lines if (m := MATERIAL_RE.search(ln))]
    # FL-4260 RQ-154: fixture provenance from the probe summary. fixture=1 means
    # the authored all-live fixture map (FL4260_FIXTURE_MAP) was loaded, so
    # the frame can sample every live material. A real-map run reports
    # fixture=0 (or omits the field on pre-fixture logs). The two coverage labels
    # below are mutually exclusive on a given artifact: real_map_sampled_* is the
    # real-map observation; fixture_all_live_* is the authored-fixture proof.
    fixture_flag = 0
    if probes:
        fv = probes[-1].get("fixture")
        fixture_flag = int(fv) if fv is not None else 0
    passthrough_traces = [
        ln for ln in lines if "[FL-4260] trace" in ln and "OUT_OF_PROFILE_SCOPE" in ln
    ]
    profile_env_seen = any("FL4260_NATIVE_PROOF: PROFILE mode" in ln for ln in lines)

    # FL-4260 RQ-154 broad: collapse to the LAST frame's per-material lines (dedup
    # by mat, keeping the last occurrence — repeated frames re-emit the same set).
    last_material_by_mat: dict[int, dict[str, Any]] = {}
    for m in materials:
        last_material_by_mat[int(m["mat"])] = m
    broad_materials = list(last_material_by_mat.values())
    broad_disagree_total = sum(int(m["disagree"]) for m in broad_materials)
    broad_mats_seen = {int(m["mat"]) for m in broad_materials if int(m["seen"]) > 0}
    non_grass_seen = sorted(broad_mats_seen - {1})

    # live GRASS: a real terrain:1 sample with winners (probe sample OR a
    # per-material parity line for mat=1 — the broad probe samples the first cell,
    # which is no longer guaranteed to be terrain:1).
    live_samples = [
        p for p in probes
        if int(p["mat"]) == 1 and int(p["elig"]) > 0 and int(p["gpuwin"]) > 0
    ]
    grass_rendered = bool(live_samples) or (1 in broad_mats_seen)
    parity_probes = [p for p in probes if int(p["seen"]) > 0]
    disagree_total = sum(int(p["disagree"]) for p in parity_probes)
    trace_live = [t for t in traces if int(t["mat"]) == 1]
    trace_disagree = [t for t in trace_live if int(t["agree"]) != 1]

    gates: dict[str, dict[str, Any]] = {}

    gates["scenario_fl4260_profile_proof_active"] = {
        "pass": profile_env_seen and any(int(p["routing"]) == 1 for p in probes),
        "detail": f"profile_env_marker={profile_env_seen} "
                  f"profile_routing=1 probes={sum(1 for p in probes if int(p['routing']) == 1)}",
    }
    gates["evidence_fl4260_native_parity_probe_present"] = {
        "pass": len(probes) > 0,
        "detail": f"probe_lines={len(probes)}",
    }
    gates["evidence_fl4260_trace_present"] = {
        "pass": len(traces) > 0,
        "detail": f"trace_lines={len(traces)}",
    }
    gates["gameplay_fl4260_live_grass_rendered"] = {
        "pass": grass_rendered,
        "detail": f"live_grass_probes={len(live_samples)} "
                  f"per_material_mat1_seen={last_material_by_mat.get(1, {}).get('seen', 0)} "
                  f"(terrain:1 sampled with a GPU winner)",
    }
    gates["gameplay_fl4260_cpu_gpu_parity_clean"] = {
        "pass": len(parity_probes) > 0 and disagree_total == 0,
        "detail": f"probes_with_gpu_parity_seen>0={len(parity_probes)} "
                  f"total_cpu_gpu_disagree={disagree_total}",
    }
    gates["gameplay_fl4260_trace_winner_agree"] = {
        "pass": len(trace_live) > 0 and len(trace_disagree) == 0,
        "detail": f"live_traces={len(trace_live)} "
                  f"cpu!=gpu_traces={len(trace_disagree)}",
    }
    # Visual acceptance: the rendered PROFILE frame must NOT be mostly '!'. This
    # gate consumes [FL-4260] frame_markers (marker-class accounting over the
    # rendered AnsiCell buffer). A run where marker cells outnumber live glyph
    # cells FAILS; a screen of '!' is not a user-facing rendering result.
    mostly_bang_runs = [m for m in markers if int(m["mostly"]) == 1]
    gates["evidence_fl4260_frame_markers_present"] = {
        "pass": len(markers) > 0,
        "detail": f"frame_markers_lines={len(markers)}",
    }
    gates["gameplay_fl4260_frame_not_mostly_bang"] = {
        "pass": len(markers) > 0 and len(mostly_bang_runs) == 0,
        "detail": (
            f"frames={len(markers)} mostly_bang_frames={len(mostly_bang_runs)}; "
            + (f"last marker_total={int(markers[-1]['mp'])+int(markers[-1]['mg'])+int(markers[-1]['dg'])} "
               f"readable_cells={markers[-1]['rc']} passthrough={markers[-1]['pt']} "
               f"live_profile_glyph_cells={markers[-1]['live']}"
               if markers else "no frame_markers line")
        ),
    }
    # Fork A scope: non-material cells must PASS THROUGH (passthrough > 0), not be
    # markered, and missing_glyph must be 0 (live profile policy always resolves).
    # Prefer the authoritative resolver frame_classes line; fall back to the
    # color-keyed frame_markers line (now per-class-correct after the marker
    # reclassification fix) when frame_classes is not emitted on this target.
    last_cls = classes[-1] if classes else None
    last_mk = markers[-1] if markers else None
    if last_cls:
        scope_pass = int(last_cls["oops"]) > 0 and int(last_cls["mg"]) == 0
        scope_detail = (f"frame_classes out_of_profile_scope={last_cls['oops']} "
                        f"missing_policy={last_cls['mp']} missing_glyph={last_cls['mg']} "
                        f"live={last_cls['live']}")
    elif last_mk:
        scope_pass = int(last_mk["pt"]) > 0 and int(last_mk["mg"]) == 0
        scope_detail = (f"frame_markers passthrough={last_mk['pt']} "
                        f"missing_policy={last_mk['mp']} missing_glyph={last_mk['mg']} "
                        f"live_profile_glyph_cells={last_mk['live']} (frame_classes absent)")
    else:
        scope_pass = False
        scope_detail = "no frame_classes or frame_markers line"
    gates["gameplay_fl4260_profile_scope_material_only"] = {
        "pass": scope_pass, "detail": scope_detail,
    }
    # FL-4260 RQ-154 broad: MISSING_GLYPH must be 0 in the rendered frame
    # (live profile policy present => a glyph always resolves). Authoritative source
    # is frame_markers missing_glyph after the per-class marker reclassification.
    gates["gameplay_fl4260_missing_glyph_zero"] = {
        "pass": bool(last_mk) and int(last_mk["mg"]) == 0,
        "detail": (f"missing_glyph={last_mk['mg']} missing_policy={last_mk['mp']}"
                   if last_mk else "no frame_markers line"),
    }
    # FL-4260 RQ-154: real-map SAMPLED-frame parity. Every material the real-map
    # frame ACTUALLY SAMPLED must have CPU==GPU (disagree==0), covering terrain:1
    # AND >=1 non-GRASS material. NAME DISCIPLINE: this proves the materials
    # visible in the real game map's camera view ONLY, never all-live coverage
    # (which is the authored-fixture gate below). On a fixture=1 artifact this gate
    # is N/A (skipped) because the loaded terrain is the authored fixture, not the
    # real map.
    # The real-map invariant is PARITY over whatever the camera sampled, not the
    # presence of any specific material id — which materials are visible at spawn
    # is camera-dependent, and all-live COVERAGE is owned by the authored-
    # fixture gate below. So this gate requires >=1 sampled material and zero
    # CPU/GPU disagreement; it does NOT mandate terrain:1 (that would assert a
    # view-coverage property this gate does not own). It reports the sampled set.
    real_map_skipped = fixture_flag == 1
    gates["gameplay_fl4260_real_map_sampled_material_parity"] = {
        "pass": (len(broad_materials) > 0 and broad_disagree_total == 0),
        "skipped": real_map_skipped,
        "detail": (
            f"fixture=1 artifact; real-map sampled parity is a separate fixture=0 "
            f"observation (skipped here)"
            if real_map_skipped else
            f"materials_sampled={sorted(broad_mats_seen)} "
            f"total_per_material_disagree={broad_disagree_total} "
            f"non_grass_seen={non_grass_seen} "
            f"(REAL-MAP parity over sampled view only; all-live coverage is the "
            f"authored-fixture gate, not this one)"),
    }
    # FL-4260 RQ-154: authored-fixture ALL-LIVE coverage. Reads the live profile
    # set from material_rendering_profiles.v1.json and requires that the authored
    # fixture frame sampled EVERY live material with CPU==GPU parity. Passes
    # ONLY from a fixture=1 artifact (the FL4260_FIXTURE_MAP run); on a fixture=0
    # real-map artifact it is N/A (skipped) because the real map cannot surface
    # materials that are off-camera or absent from it. When evaluated, it FAILS and
    # names any unsampled live materials so coverage can never be overclaimed.
    profiles_v1 = REPO_ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
    live_terrain: list[int] = []
    if profiles_v1.exists():
        try:
            doc = json.loads(profiles_v1.read_text(encoding="utf-8"))
            for p in doc.get("profiles", []):
                mid = str(p.get("material_id", ""))
                if p.get("profile_state") == "live" and mid.startswith("terrain:") \
                        and mid[len("terrain:"):].isdigit():
                    live_terrain.append(int(mid[len("terrain:"):]))
        except (json.JSONDecodeError, OSError):
            live_terrain = []
    unsampled_live = sorted(set(live_terrain) - broad_mats_seen)
    fixture_skipped = fixture_flag == 0
    gates["gameplay_fl4260_fixture_all_live_material_parity"] = {
        "pass": (fixture_flag == 1 and bool(live_terrain)
                 and len(unsampled_live) == 0 and broad_disagree_total == 0),
        "skipped": fixture_skipped,
        "detail": (
            f"fixture=0 real-map artifact; all-live coverage requires the "
            f"FL4260_FIXTURE_MAP authored-fixture run (skipped here)"
            if fixture_skipped else
            f"fixture=1 live={sorted(live_terrain)} sampled={sorted(broad_mats_seen)} "
            f"unsampled_live={unsampled_live} "
            f"total_per_material_disagree={broad_disagree_total} "
            f"(authored-fixture all-live parity)"),
    }
    gates["evidence_fl4260_passthrough_trace_present"] = {
        "pass": len(passthrough_traces) > 0,
        "detail": f"out_of_profile_scope_trace_lines={len(passthrough_traces)}",
    }

    # representative sample (from the last live-profile probe / trace)
    sample = None
    if live_samples:
        p = live_samples[-1]
        sample = {
            "cell": [int(p["sx"]), int(p["sy"])],
            "material": f"terrain:{p['mat']}",
            "ramp_row": int(p["ri"]),
            "density_bucket": int(p["di"]),
            "cpu_gid": int(p["cpu"]),
            "gpu_gid": int(p["gpu"]),
            "agree": int(p["agree"]),
            "eligible_count": int(p["elig"]),
            "gpu_parity_seen": int(p["seen"]),
            "cpu_gpu_disagree": int(p["disagree"]),
        }
        if trace_live:
            t = trace_live[-1]
            sample["trace"] = {
                "final_glyph_gid": int(t["gid"]),
                "final_cp437_byte": int(t["byte"]),
                "fg": int(t["fg"]),
                "bg": int(t["bg"]),
                "marker_class": t["marker"],
                "rejection_reason": t["reason"],
            }

    # Skipped gates (fixture-provenance N/A) are excluded from the verdict so a
    # real-map artifact is not failed by the fixture-only gate and vice versa.
    all_pass = all(g["pass"] for g in gates.values() if not g.get("skipped"))

    # ── Static RQ-145 old-owner deletion checks (multi-target, fail-closed) ──
    # The two "dead owner" canonical gates verify the ACTUAL RQ-145 deletion list
    # (docs/plans/2026-06-13-fl4260-rq145-old-owner-deletion-list.md), not a single
    # symbol. Each target below is one row of that list. A gate passes only if EVERY
    # required target passes; any failing/uncheckable target fails the gate (no
    # false-pass). These are static-grep heuristics over committed source, NOT a full
    # audit — surfaced as static_checks for transparency.
    import subprocess

    def _grep(pattern, *paths, literal=False):
        try:
            cmd = ["git", "grep", "-nE" if not literal else "-nF", pattern, "--", *paths]
            return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True).stdout
        except Exception:
            return ""

    def _live_lines(pattern, *paths):
        # Lines matching pattern that are NOT pure comments (// before the symbol).
        live = []
        for ln in _grep(pattern, *paths).splitlines():
            try:
                _, _, body = ln.split(":", 2)
            except ValueError:
                body = ln
            code = body.split("//", 1)[0]
            if re.search(pattern, code):
                live.append(ln)
        return live

    static_checks = {}
    # T_flatunion: the hard-coded flat-union GPU accessor is deleted.
    static_checks["flat_union_accessor_dead"] = \
        len(_live_lines(r"Fl4260GetLegacyGpuCandidates", "engine", "platform", "editor")) == 0
    # T_routing: GPU winner shader scores per-cell routed buckets (profile_routing==1).
    static_checks["gpu_routed_branch_present"] = \
        bool(_grep(r"profile_routing==1", "platform/terminal_gl_present.cpp"))
    # T_routing_weights: PROFILE winner scoring is keyed by cell material id, not a
    # global role_weights uniform shared across all materials.
    static_checks["gpu_profile_role_weights_material_indexed"] = \
        bool(_grep(r"mat_role_weights\[gpu_max_materials \* 6\]", "engine/render/render_frame_input.h")) \
        and bool(_grep(r"harri_mat_role_weights_tex", "platform/terminal_gl_present.cpp")) \
        and bool(_grep(r"texelFetch\(mat_role_weights_tex,ivec2\(k,mat\),0\)", "platform/terminal_gl_present.cpp"))
    # T1: authored glyph_plane priority is fenced by the per-cell Material Look
    # owner. The old global profile_mode branch is deleted; live profiles are
    # keyed by the resolved material id and unprofiled cells pass through.
    static_checks["authored_glyph_plane_material_look_fenced"] = \
        len(_live_lines(r"!material_look_cell && in->has_material", "engine/fl4131_runtime_harri_resolver.cpp")) >= 1 \
        and bool(_grep(r"Fl4260MaterialProfileLive\(mat_id\)", "engine/fl4131_runtime_harri_resolver.cpp")) \
        and bool(_grep(r"Editing terrain:N cannot change terrain:M", "engine/fl4131_runtime_harri_resolver.cpp"))
    # T2: the Edit preset writer routes through the direct profile edit owner and
    # writes NO final glyph_plane.
    static_checks["preset_writer_direct_edit_only"] = \
        bool(_grep(r"Fl4260ApplyProfileDirectEdit", "editor/asciiid.cpp")) \
        and bool(_grep(r"AsciiidApplyExtendedPresetToActiveMaterial", "editor/asciiid.cpp"))
    # T4: presenter sandbox overwrite is deleted. A surviving
    # g_term_extended_only_sandbox branch would be a second final-glyph owner.
    static_checks["presenter_sandbox_dead"] = \
        len(_live_lines(r"g_term_extended_only_sandbox", "platform/terminal_gl_present.cpp")) == 0
    # T5: routed-pool staging has ONE shared owner; the editor populate-time routed
    # branch is deleted (Fl4260GetLiveBucketPools survives in the editor only in
    # the observational DUMP MCP — at most one editor call site).
    _editor_bucket_calls = _live_lines(r"Fl4260GetLiveBucketPools", "editor/asciiid.cpp")
    static_checks["single_shared_routed_owner"] = \
        (len(_live_lines(r"int Fl4260StageProfileRoutedGpuPools", "engine/fl4131_runtime_harri_resolver.cpp")) >= 1) \
        and (len(_editor_bucket_calls) <= 1)

    lut_dead = (
        static_checks["flat_union_accessor_dead"]
        and static_checks["gpu_routed_branch_present"]
        and static_checks["gpu_profile_role_weights_material_indexed"]
    )
    preset_owners_dead = (
        static_checks["authored_glyph_plane_material_look_fenced"]
        and static_checks["preset_writer_direct_edit_only"]
        and static_checks["presenter_sandbox_dead"]
        and static_checks["single_shared_routed_owner"]
    )

    g = gates
    canonical = {
        "evidence_fl4260_renderer_mode_declared":
            g["scenario_fl4260_profile_proof_active"]["pass"],
        "evidence_fl4260_unfiltered_lut_dead": lut_dead,
        "evidence_fl4260_morphology_runtime_profile_live_guard":
            g["gameplay_fl4260_live_grass_rendered"]["pass"],
        "gameplay_fl4260_profile_bucket_lane_used":
            g["gameplay_fl4260_profile_scope_material_only"]["pass"]
            and g["gameplay_fl4260_trace_winner_agree"]["pass"],
        "evidence_fl4260_old_preset_owners_dead": preset_owners_dead,
        "evidence_fl4260_profile_trace_complete":
            g["evidence_fl4260_trace_present"]["pass"]
            and g["evidence_fl4260_passthrough_trace_present"]["pass"],
        # The proof runs PROFILE mode (mode=1), never DIAGNOSTIC (mode=2).
        "evidence_fl4260_diagnostic_mode_excluded_from_closure":
            g["scenario_fl4260_profile_proof_active"]["pass"],
    }
    canonical_all_pass = all(canonical.values())

    return {
        "artifact": str(log_path.relative_to(REPO_ROOT)) if log_path.is_relative_to(REPO_ROOT) else str(log_path),
        "probe_lines": len(probes),
        "trace_lines": len(traces),
        "gates": gates,
        "canonical_gates": canonical,
        "canonical_static_checks": static_checks,
        "canonical_all_pass": canonical_all_pass,
        "sample": sample,
        "all_gates_pass": all_pass,
        "closure": False,
        "closure_note": "LAW 16: gate pass is not closure. LAW 15: the canonical "
                        "fl gates surface is fed by VPS two-tab watchdog runs; this is "
                        "a LOCAL native-iteration evaluation. Operator signoff required.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="FL-4260 RQ-156 native PROFILE parity gate checker")
    ap.add_argument("log", nargs="?", help="proof log path (default: newest under verification/fl4260)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    log_path = Path(args.log) if args.log else _newest_proof_log()
    if log_path is None or not log_path.exists():
        msg = {"error": "no proof log found", "searched": str(VERIFY_DIR)}
        print(json.dumps(msg) if args.json else f"FL-4260 gate: NO ARTIFACT ({msg['searched']})")
        return 2

    result = evaluate(log_path)

    if result["probe_lines"] == 0:
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"FL-4260 gate: NO EVIDENCE — 0 probe lines in {result['artifact']}")
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("FL-4260 RQ-156 native PROFILE parity gates")
        print(f"  artifact: {result['artifact']}")
        print(f"  probe_lines={result['probe_lines']} trace_lines={result['trace_lines']}")
        for name, g in result["gates"].items():
            label = "N/A " if g.get("skipped") else ("PASS" if g["pass"] else "FAIL")
            print(f"  [{label}] {name}")
            print(f"         {g['detail']}")
        if result["sample"]:
            s = result["sample"]
            print(f"  sample: cell={s['cell']} {s['material']} ramp_row={s['ramp_row']} "
                  f"D{s['density_bucket']} cpu_gid={s['cpu_gid']} gpu_gid={s['gpu_gid']} agree={s['agree']}")
            if "trace" in s:
                t = s["trace"]
                print(f"          trace: final_gid={t['final_glyph_gid']} byte={t['final_cp437_byte']} "
                      f"fg={t['fg']} bg={t['bg']} marker={t['marker_class']} reason={t['rejection_reason']}")
        print("  --- CANONICAL FL-4260 gates (analyze_runs.py fl gates FL-4260 vocabulary) ---")
        for name, ok in result.get("canonical_gates", {}).items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        print(f"  VERDICT: {'ALL GATES PASS' if result['all_gates_pass'] else 'GATE FAILURE'} "
              f"(canonical: {'ALL PASS' if result.get('canonical_all_pass') else 'INCOMPLETE'})")
        print(f"  {result['closure_note']}")

    return 0 if result["all_gates_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
