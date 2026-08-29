#!/usr/bin/env python3
"""Generate a source-anchored FL-4260 UI control inventory from editor/asciiid.cpp."""
from __future__ import annotations

# Ad hoc script: FL-4260 Phase 0 current HEAD UI control inventory generator
# Created: 2026-06-18
# Canonical gap: durable ImGui source-to-rendered-control inventory with loop and dynamic-label expansion.

import csv
import datetime as _dt
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "editor" / "asciiid.cpp"
OUT_DIR = ROOT / "docs" / "research" / "ascii" / "verification" / "fl4260" / "2026-06-18-phase0-current-head-control-inventory"
OUT_CSV = OUT_DIR / "asciiid-ui-current-head-control-inventory.csv"
OUT_RESIDUAL_CSV = OUT_DIR / "asciiid-ui-current-head-control-residual-candidates.csv"
OUT_BACKEND_GAP_CSV = OUT_DIR / "fl4260-backend-matrix-excluded-control-gaps.csv"
OUT_MD = OUT_DIR / "README.md"

CONTROL_PATTERNS = [
    ("Button", re.compile(r'ImGui::(?:SmallButton|Button)\("([^"]+)"')),
    ("Button", re.compile(r'ImGui::(?:SmallButton|Button)\((?!")([^,\)]+)')),
    ("ImageButton", re.compile(r'ImGui::ImageButton\(')),
    ("ArrowButton", re.compile(r'ImGui::ArrowButton\("([^"]+)"')),
    ("Checkbox", re.compile(r'ImGui::Checkbox\("([^"]+)"')),
    ("Checkbox", re.compile(r'ImGui::Checkbox\((?!")([^,\)]+)')),
    ("SliderFloat3", re.compile(r'ImGui::SliderFloat3\("([^"]+)"')),
    ("SliderFloat2", re.compile(r'ImGui::SliderFloat2\("([^"]+)"')),
    ("SliderFloat", re.compile(r'ImGui::SliderFloat\("([^"]+)"')),
    ("SliderFloat", re.compile(r'ImGui::SliderFloat\((?!")([^,\)]+)')),
    ("SliderInt", re.compile(r'ImGui::SliderInt\("([^"]+)"')),
    ("SliderInt2", re.compile(r'ImGui::SliderInt2\("([^"]+)"')),
    ("DragIntRange2", re.compile(r'ImGui::DragIntRange2\("([^"]+)"')),
    ("InputText", re.compile(r'ImGui::InputText\("([^"]+)"')),
    ("InputInt", re.compile(r'ImGui::InputInt\("([^"]+)"')),
    ("Combo", re.compile(r'ImGui::(?:BeginCombo|Combo)\("([^"]+)"')),
    ("Selectable", re.compile(r'ImGui::Selectable\(([^,\)]+)')),
    ("ColorEdit3", re.compile(r'ImGui::ColorEdit3\("([^"]*)"')),
    ("ColorButton", re.compile(r'ImGui::ColorButton\("([^"]+)"')),
    ("ListBoxHeader", re.compile(r'ImGui::ListBoxHeader\("([^"]+)"')),
    ("ListBox", re.compile(r'ImGui::ListBox\("([^"]+)"')),
    ("CollapsingHeader", re.compile(r'ImGui::CollapsingHeader\("([^"]+)"')),
    ("CollapsingHeader", re.compile(r'ImGui::CollapsingHeader\((?!")([^,\)]+)')),
    ("TreeNodeEx", re.compile(r'ImGui::TreeNodeEx\("([^"]+)"')),
    ("TreeNode", re.compile(r'ImGui::TreeNode\("([^"]+)"')),
    ("BeginTabItem", re.compile(r'ImGui::BeginTabItem\("([^"]+)"')),
]

CONTROL_CALL_PATTERN = re.compile(
    r"ImGui::("
    r"SmallButton|Button|ImageButton|ArrowButton|Checkbox|SliderFloat3|SliderFloat2|SliderFloat|"
    r"SliderInt2|SliderInt|DragIntRange2|InputText|InputInt|BeginCombo|Combo|"
    r"Selectable|ColorEdit3|ColorButton|ListBoxHeader|ListBox|CollapsingHeader|"
    r"TreeNodeEx|TreeNode|BeginTabItem"
    r")\("
)

SECTION_RULES = [
    (4319, 5887, "RENDERING / Measurement Debug", "AsciiidShapeLabRebuildMaterialPanels and Shape Lab material-panel state", "diagnostic TERM++/GPU parity only"),
    (5950, 6190, "RENDERING / Measurement Debug", "g_asciiid_harri_pipeline and Shape Lab state", "diagnostic TERM++/GPU parity only"),
    (11370, 11470, "RENDERING / Evidence Receipts", "morphology profile assignment and review receipt writers", "metadata receipts; not closure"),
    (15870, 16000, "ROOT UI / Shared proof/navigation", "deferred operation state and confirm callback owner", "modal outcome evidence"),
    (25120, 25449, "ROOT UI / Global font palette glyph browser", "active_font, active_palette, glyph filter/page/category state", "GL font texture, material swatches, glyph browser display"),
    (25450, 25640, "EDIT / Legacy diagnostic appearance repair", "legacy glyph picker and CP437 pixel editor state", "diagnostic only; guarded by Fl4260LegacyMaterialUiEnabled"),
    (25670, 25940, "EDIT / Legacy diagnostic appearance repair", "legacy palette and options state", "diagnostic only; guarded by Fl4260LegacyMaterialUiEnabled"),
    (25950, 26125, "MESH / Mesh selector and placement", "mesh combo, transform, bake, delete, selection state", "mesh viewport/render preview"),
    (26150, 26740, "EDIT / Legacy diagnostic appearance repair", "legacy material grid, raw material shade rows, extended workspace state", "diagnostic only; guarded by Fl4260LegacyMaterialUiEnabled"),
    (26741, 26920, "EDIT / Legacy diagnostic appearance repair", "legacy material grid, raw material shade rows, extended workspace state", "diagnostic only; guarded by Fl4260LegacyMaterialUiEnabled"),
    (27042, 27082, "ROOT UI / Top tab strip", "g_asciiid_sidebar_tab plus proof-mode navigation", "CDP/screenshot tab evidence"),
    (27112, 27235, "RENDERING / Active Materials", "g_asciiid_active_material_selection and g_fl4260_trace_highlight_enabled", "selected-set viewport proof pending"),
    (27236, 27276, "RENDERING / Mode and Status", "renderer mode, selected material, and Material Look profile availability", "read-only renderer/profile state evidence"),
    (27284, 27556, "RENDERING / Starters", "g_fl4260_profile_edit_state via Fl4260ApplyProfileDirectEdit", "detached TERM++ rendered-cell delta pending"),
    (27564, 27666, "RENDERING / Colors and Shade Bands", "row_fg_strength, row_bg_strength, row_shade_contrast, shade_band_thresholds via Fl4260SetActiveProfileColors", "Fl4260GetActiveProfileColor -> render_resolve -> detached TERM++"),
    (27668, 27859, "RENDERING / Glyph Pools", "selected profile glyph pool edit state", "profile resolver pool plus detached TERM++ proof pending"),
    (27861, 27978, "RENDERING / Role Buckets", "profile bucket edit state", "ramp/density resolver lanes; Direction Edge Flow Accent blocked"),
    (27980, 28044, "RENDERING / Winner Scoring", "detail_contrast, tone_contrast, density_bias, scoring_role_weights via Fl4260SetActiveProfileScoring", "weighted glyph scorer and detached TERM++"),
    (28046, 28361, "RENDERING / Trace", "trace highlight and clicked-cell state", "viewport selected-set proof plus detached TERM++ clicked-cell proof pending"),
    (28363, 28558, "RENDERING / Evidence Receipts", "receipt refresh, row lookup, reason, accept/reject/defer state", "proof metadata only; not runtime closure"),
    (28559, 28572, "RENDERING / Measurement Debug", "Shape Lab and TERM++ diagnostic state", "diagnostic TERM++/GPU parity only"),
    (28574, 28620, "SPRITE / Sprite selector", "active_sprite selection and sprite filter state", "sprite preview widget and viewport placement evidence"),
    (28621, 28690, "INST / Instance inspection", "instance cache refresh and selectable list", "instance selection evidence"),
    (28700, 29119, "VIEW / Rendered-scene inspection", "file ops, TERM++ launch, camera, window, light, weather, modal state", "headed screenshot plus TERM++ artifact proof when relevant"),
    (29120, 29449, "EDIT / Shared undo/proof/brush shell", "proof-step state, URDO state, brush section owner", "editor mutation evidence; not Rendering proof"),
    (29450, 29537, "EDIT / Raw-world sculpt", "edit_mode=0 brush/radius/shape/height state", "terrain height/paint render after map mutation"),
    (29545, 29681, "EDIT / Raw material id paint", "edit_mode=1 active material Auto MAT-elev Auto Texture", "terrain material-id map and viewport render"),
    (29695, 29752, "EDIT / Raw elevation/ramp paint", "edit_mode=3 elevated bit height/probe state", "terrain visual-map elevation/ramp render"),
    (29771, 29817, "EDIT / Placement paint / SPRITE", "sprite anim/repeat/randomization state", "viewport placement/render evidence"),
    (29835, 29902, "EDIT / Placement paint / ITEM", "item reset/list state", "viewport placement/render evidence"),
    (29921, 30001, "EDIT / Placement paint / ENEMY", "enemy generator sliders and toggles", "viewport placement/render evidence"),
    (30019, 30032, "EDIT / Placement paint / STORY", "story id state", "map story metadata evidence"),
    (30040, 30091, "EDIT / Retired shade subtabs", "commented legacy shade/elev tab code", "not live; historical inventory only"),
    (30106, 30175, "FONT / SKIN inventory", "font legacy and skin V2 selector state", "font/skin headed proof; focused write audit required"),
    (30177, 30229, "INFO / Probe/debug", "camera/probe readouts DebugProbe", "diagnostic evidence only"),
    (15730, 15835, "ROOT UI / Shared proof/navigation", "deferred operation state and confirm callback owner", "modal outcome evidence"),
    (24680, 25050, "ROOT UI / Global font palette glyph browser", "active_font, active_palette, glyph filter/page/category state", "GL font texture, material swatches, glyph browser display"),
    (11230, 11310, "RENDERING / Evidence Receipts", "morphology profile assignment and review receipt writers", "metadata receipts; not closure"),
]

STATUS_RULES = [
    # ── Trace (must precede Evidence Receipts to avoid being swallowed) ──
    (28046, 28361, "SOURCE_WIRED_PROOF_PENDING"),

    # ── Legacy diagnostic appearance repair (diagnostic only; guarded by Fl4260LegacyMaterialUiEnabled) ──
    (25450, 25640, "DIAGNOSTIC_ONLY"),
    (25670, 25940, "DIAGNOSTIC_ONLY"),
    (26150, 26740, "DIAGNOSTIC_ONLY"),
    (26741, 26920, "DIAGNOSTIC_ONLY"),

    # ── Measurement Debug (diagnostic only; never mutates live profile policy) ──
    (4319, 5887, "DIAGNOSTIC_ONLY"),
    (5950, 6190, "DIAGNOSTIC_ONLY"),
    (28559, 28572, "DIAGNOSTIC_ONLY"),

    # ── Evidence Receipts (metadata only; writes receipts, not runtime closure) ──
    (11230, 11470, "METADATA_ONLY_NOT_CLOSURE"),
    (28363, 28558, "METADATA_ONLY_NOT_CLOSURE"),

    # ── Winner Scoring (partial local proof) ──
    (27980, 28044, "SOURCE_WIRED_LOCAL_PROOF_PARTIAL"),

    # ── Role Buckets (partial disabled lanes) ──
    (27861, 27978, "PARTIAL_DISABLED_LANES"),

    # ── Colors and Shade Bands (source wired, proof pending) ──
    (27564, 27666, "SOURCE_WIRED_PROOF_PENDING"),

    # ── Glyph Pools (source wired, proof pending) ──
    (27668, 27859, "SOURCE_WIRED_PROOF_PENDING"),

    # ── FONT/SKIN (focused write audit required) ──
    (30106, 30175, "FOCUSED_WRITE_AUDIT_REQUIRED"),

    # ── Retired shade subtabs (commented out, not live) ──
    (30040, 30091, "COMMENTED_OUT_NOT_LIVE"),

    # ── Probe/debug (diagnostic only) ──
    (30177, 30229, "DIAGNOSTIC_ONLY"),

    # ── ROOT UI: Shared proof/navigation (source wired, proof pending) ──
    (15730, 15835, "SOURCE_WIRED_PROOF_PENDING"),
    (15870, 16000, "SOURCE_WIRED_PROOF_PENDING"),

    # ── ROOT UI: Global font palette glyph browser (source wired, proof pending) ──
    (24680, 25050, "SOURCE_WIRED_PROOF_PENDING"),
    (25120, 25449, "SOURCE_WIRED_PROOF_PENDING"),

    # ── MESH: Mesh selector and placement (source wired, proof pending) ──
    (25950, 26125, "SOURCE_WIRED_PROOF_PENDING"),

    # ── ROOT UI: Top tab strip (source wired, proof pending) ──
    (27042, 27082, "SOURCE_WIRED_PROOF_PENDING"),

    # ── Active Materials strip (source wired, proof pending) ──
    (27112, 27235, "SOURCE_WIRED_PROOF_PENDING"),

    # ── RENDERING: Starters (source wired, proof pending) ──
    (27236, 27276, "SOURCE_WIRED_PROOF_PENDING"),
    (27284, 27556, "SOURCE_WIRED_PROOF_PENDING"),

    # ── SPRITE: Sprite selector (source wired, proof pending) ──
    (28574, 28620, "SOURCE_WIRED_PROOF_PENDING"),

    # ── INST: Instance inspection (source wired, proof pending) ──
    (28621, 28690, "SOURCE_WIRED_PROOF_PENDING"),

    # ── VIEW: Rendered-scene inspection (source wired, proof pending) ──
    (28700, 29119, "SOURCE_WIRED_PROOF_PENDING"),

    # ── EDIT: Shared undo/proof/brush shell (source wired, proof pending) ──
    (29120, 29449, "SOURCE_WIRED_PROOF_PENDING"),

    # ── EDIT: Raw-world sculpt (source wired, proof pending) ──
    (29450, 29537, "SOURCE_WIRED_PROOF_PENDING"),

    # ── EDIT: Raw material id paint (source wired, proof pending) ──
    (29545, 29681, "SOURCE_WIRED_PROOF_PENDING"),

    # ── EDIT: Raw elevation/ramp paint (source wired, proof pending) ──
    (29695, 29752, "SOURCE_WIRED_PROOF_PENDING"),

    # ── EDIT: Placement paint / SPRITE (source wired, proof pending) ──
    (29771, 29817, "SOURCE_WIRED_PROOF_PENDING"),

    # ── EDIT: Placement paint / ITEM (source wired, proof pending) ──
    (29835, 29902, "SOURCE_WIRED_PROOF_PENDING"),

    # ── EDIT: Placement paint / ENEMY (source wired, proof pending) ──
    (29921, 30001, "SOURCE_WIRED_PROOF_PENDING"),

    # ── EDIT: Placement paint / STORY (source wired, proof pending) ──
    (30019, 30032, "SOURCE_WIRED_PROOF_PENDING"),
]

EXCLUDE_RANGES = [
    (30240, 30285),  # Dear ImGui sample windows, not ASCIIID product UI.
]

RAMP_ROWS = ["Fall / Lower (elv0)", "High (elv1)", "Rise (elv2)", "Flat / Low (elv3)"]
ROLE_WEIGHTS = ["curve", "diagonal", "horizontal", "vertical", "sparse", "dense"]
PALETTE_INDICES = [f"palette cell {idx:03d}" for idx in range(256)]

EXPANDED_CONTROLS = {
    5430: [f"Shape Lab target mat {mat_id}" for mat_id in range(16)],
    5466: ["Shape Lab visible high-material target checkbox"],
    5986: [f"Shape Lab {axis} role weight" for axis in ROLE_WEIGHTS],
    25769: PALETTE_INDICES,
    27605: [f"{row} foreground color" for row in RAMP_ROWS],
    27612: [f"{row} background color" for row in RAMP_ROWS],
    27620: [f"{row} fg str" for row in RAMP_ROWS],
    27629: [f"{row} bg str" for row in RAMP_ROWS],
    27636: [f"{row} shade contrast" for row in RAMP_ROWS],
    27644: [f"{row} band threshold" for row in RAMP_ROWS],
    28028: [f"{axis} role weight" for axis in ROLE_WEIGHTS],
}

DYNAMIC_LABEL_PREFIXES = (
    "kAsciiidHarriBlogScenes",
    "kAsciiidMorphologyProfileNames",
    "option_label",
    "candidate_name",
    "commit_label",
    "btn",
    "btn_label",
    "custom_edit_open",
    "g_palette_expanded",
    "g_palette_themes",
    "glyph_category_filter",
    "hdr",
    "id",
    "label",
    "lab",
    "presets",
    "raw_cells_open",
    "save ==",
    "shade_label",
    "sname",
    "(*di",
)


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()


def clean_label(raw: str) -> str:
    raw = raw.strip()
    if any(raw.startswith(prefix) for prefix in DYNAMIC_LABEL_PREFIXES):
        return f"<dynamic label: {raw}>"
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    if "##" in raw:
        raw = raw.split("##", 1)[0]
    if raw.startswith("###"):
        raw = raw[3:]
    if not raw:
        return "<id-only control; visible label from adjacent text>"
    return raw


def classify(line_no: int) -> tuple[str, str, str]:
    for start, end, leaf, owner, consumer in SECTION_RULES:
        if start <= line_no <= end:
            return leaf, owner, consumer
    return "UNMAPPED / requires manual classification", "unclassified current source state", "manual proof/render consumer review required"


def status_for(line_no: int, label: str) -> str:
    if label.startswith("<dynamic label:"):
        return "LABEL_NEEDS_MANUAL_RESOLUTION"
    if label.startswith("<id-only control"):
        return "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT"
    if "ReviveMax" in label or "ReviveMin" in label:
        return "KNOWN_LABEL_BACKING_ANOMALY"
    if "##" in label and not label.strip("#"):
        return "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT"
    for start, end, status in STATUS_RULES:
        if start <= line_no <= end:
            return status
    return "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED"


def append_row(rows: list[dict[str, object]], widget: str, line_no: int, label: str, line: str) -> None:
    leaf, owner, consumer = classify(line_no)
    rows.append({
        "row": len(rows) + 1,
        "widget": widget,
        "source_anchor": f"editor/asciiid.cpp:{line_no}",
        "current_user_label": label,
        "target_leaf": leaf,
        "backend_mutation_owner": owner,
        "render_proof_consumer": consumer,
        "status": status_for(line_no, label),
        "source_line": line.strip(),
    })


def append_residual(residuals: list[dict[str, object]], line_no: int, scan: str, line: str) -> None:
    functions = sorted({m.group(1) for m in CONTROL_CALL_PATTERN.finditer(scan)})
    residuals.append({
        "row": len(residuals) + 1,
        "source_anchor": f"editor/asciiid.cpp:{line_no}",
        "candidate_functions": ";".join(functions),
        "status": "RESIDUAL_CONTROL_CALL_REQUIRES_MANUAL_REVIEW",
        "source_line": line.strip(),
    })


def matrix_gap_reason(status: str) -> str:
    if status == "DIAGNOSTIC_ONLY":
        return "Excluded as diagnostic; fine-grained backend pipeline class still absent from 214-row matrix."
    if status == "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT":
        return "Visible label unresolved; headed UI capture must bind adjacent text/icon semantics before matrix inclusion."
    if status == "LABEL_NEEDS_MANUAL_RESOLUTION":
        return "Runtime-computed label unresolved; headed UI capture must bind rendered label before matrix inclusion."
    if status == "KNOWN_LABEL_BACKING_ANOMALY":
        return "Known label/backing-variable anomaly; matrix inclusion blocked until label-variable truth is re-audited."
    return "Not excluded from the 214-row backend matrix."


def matrix_gap_next_action(status: str) -> str:
    if status == "DIAGNOSTIC_ONLY":
        return "Keep out of closure proof; add a diagnostic-only pipeline class if the row remains visible."
    if status == "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT":
        return "Capture headed UI context, resolve visible label, then assign the matching backend pipeline class."
    if status == "LABEL_NEEDS_MANUAL_RESOLUTION":
        return "Capture the rendered runtime label, bind it to the source row, then assign the matching backend pipeline class."
    if status == "KNOWN_LABEL_BACKING_ANOMALY":
        return "Fix the label/backing audit note, then classify the corrected slider pipeline."
    return "No matrix gap action."


def main() -> int:
    lines = SRC.read_text().splitlines()
    rows = []
    residuals = []
    in_block_comment = False
    for idx, line in enumerate(lines, start=1):
        if any(start <= idx <= end for start, end in EXCLUDE_RANGES):
            continue
        scan = line
        if in_block_comment:
            if "*/" in scan:
                scan = scan.split("*/", 1)[1]
                in_block_comment = False
            else:
                continue
        while "/*" in scan:
            before, after = scan.split("/*", 1)
            if "*/" in after:
                after = after.split("*/", 1)[1]
                scan = before + after
            else:
                scan = before
                in_block_comment = True
                break
        scan = scan.split("//", 1)[0]
        if idx in EXPANDED_CONTROLS:
            for label in EXPANDED_CONTROLS[idx]:
                widget = "ExpandedLoopControl"
                if "ColorEdit3" in scan:
                    widget = "ColorEdit3"
                elif "SliderInt" in scan:
                    widget = "SliderInt"
                elif "SliderFloat" in scan:
                    widget = "SliderFloat"
                append_row(rows, widget, idx, label, line)
            continue
        matched = False
        for widget, pattern in CONTROL_PATTERNS:
            m = pattern.search(scan)
            if not m:
                continue
            if pattern.groups == 0:
                label = "<id-only control; visible label from adjacent text>"
            else:
                label = clean_label(m.group(1))
            append_row(rows, widget, idx, label, line)
            matched = True
        if CONTROL_CALL_PATTERN.search(scan) and not matched:
            append_residual(residuals, idx, scan, line)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    with OUT_RESIDUAL_CSV.open("w", newline="") as f:
        fieldnames = ["row", "source_anchor", "candidate_functions", "status", "source_line"]
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(residuals)
    backend_gap_statuses = {
        "DIAGNOSTIC_ONLY",
        "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT",
        "LABEL_NEEDS_MANUAL_RESOLUTION",
        "KNOWN_LABEL_BACKING_ANOMALY",
    }
    backend_gap_rows = []
    for r in rows:
        status = str(r["status"])
        if status not in backend_gap_statuses:
            continue
        backend_gap_rows.append({
            "inventory_row": r["row"],
            "widget": r["widget"],
            "source_anchor": r["source_anchor"],
            "current_user_label": r["current_user_label"],
            "target_leaf": r["target_leaf"],
            "backend_mutation_owner": r["backend_mutation_owner"],
            "render_proof_consumer": r["render_proof_consumer"],
            "inventory_status": status,
            "backend_matrix_gap": matrix_gap_reason(status),
            "required_next_action": matrix_gap_next_action(status),
            "source_line": r["source_line"],
        })
    non_diagnostic_backend_gap_rows = sum(1 for r in backend_gap_rows if r["inventory_status"] != "DIAGNOSTIC_ONLY")
    with OUT_BACKEND_GAP_CSV.open("w", newline="") as f:
        fieldnames = [
            "inventory_row",
            "widget",
            "source_anchor",
            "current_user_label",
            "target_leaf",
            "backend_mutation_owner",
            "render_proof_consumer",
            "inventory_status",
            "backend_matrix_gap",
            "required_next_action",
            "source_line",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(backend_gap_rows)
    head = git_head()
    unmapped = sum(1 for r in rows if r["target_leaf"].startswith("UNMAPPED"))
    by_leaf: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for r in rows:
        by_leaf[r["target_leaf"]] = by_leaf.get(r["target_leaf"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    md = []
    md.append("# FL-4260 Phase 0 Current-Source UI Control Inventory")
    md.append("")
    md.append(f"Generated: {_dt.datetime.now().isoformat(timespec='seconds')}")
    md.append(f"Source snapshot: `editor/asciiid.cpp` @ `{head}`")
    md.append(f"Matched scaffold rows: `{len(rows)}`")
    md.append(f"Residual control-call candidates: `{len(residuals)}`")
    md.append(f"Unmapped matched rows: `{unmapped}`")
    md.append("")
    md.append("This is a current-source scanner inventory for the supported ASCIIID ImGui control-call scanner surface at the source snapshot above. It expands known source-determined loops and classifies matched controls by the current FL-4260 target leaves. It is not a completed product inventory, backend proof package, headed UI proof, TERM++ proof, closure artifact, nor operator acceptance.")
    md.append("")
    md.append("Every matched scanner row has a resolved inventory status:")
    md.append("")
    md.append("- `DIAGNOSTIC_ONLY` — legacy diagnostic panels, Shape Lab measurement debug, and probe/debug readouts that never mutate live profile policy.")
    md.append("- `SOURCE_WIRED_PROOF_PENDING` — live source-wired controls (VIEW file ops/camera/light/weather, EDIT sculpt/paint/place, MESH selector, ROOT UI tab strip / glyph browser / proof navigation, RENDERING Starters / Glyph Pools / Trace / Colors and Shade Bands) where detached TERM++ rendered-cell proof has not yet been run.")
    md.append("- `SOURCE_WIRED_LOCAL_PROOF_PARTIAL` — Winner Scoring sliders; local proof exists but is partial.")
    md.append("- `PARTIAL_DISABLED_LANES` — Role Buckets controls; some resolver lanes are disabled.")
    md.append("- `METADATA_ONLY_NOT_CLOSURE` — Evidence Receipts controls; writes receipts only, does not perform runtime closure.")
    md.append("- `FOCUSED_WRITE_AUDIT_REQUIRED` — FONT/SKIN Reload button; needs focused write audit.")
    md.append("- `LABEL_NEEDS_MANUAL_RESOLUTION` — dynamic labels (runtime-computed strings) that require headed UI context to resolve to rendered text.")
    md.append("- `ID_ONLY_LABEL_REQUIRES_UI_CONTEXT` — controls with no visible string label (ID-only `##...` labels, `ImageButton` texture-pointer cells, `ArrowButton` direction-only buttons) that require headed UI context to determine visible label from adjacent text.")
    md.append("- `KNOWN_LABEL_BACKING_ANOMALY` — ReviveMin / ReviveMax sliders with known label-backing anomaly.")
    md.append("")
    md.append("Count correction: the earlier `289` number counted source call sites and was not one row per rendered control. This rerun expands the known Colors and Shade Bands loop to 24 rows plus `Clear colors`, expands Winner Scoring role weights to six rows, expands Shape Lab target material checkboxes, Shape Lab role weights, and the 256-cell legacy palette editor, includes `SliderFloat2`, `SliderFloat3`, `ListBoxHeader`, `TreeNode`, `TreeNodeEx`, `ImageButton`, and non-literal first-argument button/header calls, then keeps matched dynamic labels explicitly flagged for manual review.")
    md.append("")
    md.append("Scanner boundary: `Residual control-call candidates: 0` means the supported scanner found no remaining unclassified ASCIIID ImGui control-call lines outside the inventory. `Unmapped matched rows: 0` means every matched row falls inside the current source-range target-leaf table. `UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED: 0` means every matched row has been assigned a concrete inventory status via expanded source-range status rules. This does not prove the scanner covers every user-visible affordance, does not resolve every dynamic label to rendered text, and does not prove backend-to-TERM++ pixel deltas.")
    md.append("")
    md.append("Scope note: this generator records current anchors, labels, target leaves, backend owner summaries, render/proof consumer summaries, and status. It does not run headed UI proof, detached TERM++ proof, native parity, Law 15, Law 16, nor any closure gate.")
    md.append("")
    md.append("## Status Counts")
    md.append("")
    for key in sorted(by_status):
        md.append(f"- `{key}`: {by_status[key]}")
    md.append("")
    md.append("## Target Leaf Counts")
    md.append("")
    for key in sorted(by_leaf):
        md.append(f"- `{key}`: {by_leaf[key]}")
    md.append("")
    md.append("## CSV")
    md.append("")
    md.append("`asciiid-ui-current-head-control-inventory.csv`")
    md.append("")
    md.append("## Residual Candidate CSV")
    md.append("")
    md.append("`asciiid-ui-current-head-control-residual-candidates.csv`")
    md.append("")
    md.append("## Included-Row Backend Pipeline Matrix")
    md.append("")
    md.append("`fl4260-complete-backend-proof-matrix.csv`")
    md.append("")
    md.append(f"The filename is historical. The file is a 214-row matrix for the included inventory statuses: `SOURCE_WIRED_PROOF_PENDING`, `SOURCE_WIRED_LOCAL_PROOF_PARTIAL`, `PARTIAL_DISABLED_LANES`, `METADATA_ONLY_NOT_CLOSURE`, and `FOCUSED_WRITE_AUDIT_REQUIRED`. It is not a `{len(rows)}`-row per-control backend matrix and it is not TERM++ proof.")
    md.append("")
    md.append("Included-row matrix facts retained from the source verification audit:")
    md.append("")
    md.append("- 49 rows are profile-path controls: 8 ColorEdit3 palette editors, 16 shade-band sliders, 1 Clear colors button, 9 Winner Scoring sliders, 8 starter actions, 4 glyph-pool actions, 1 Role Buckets auto-fill control, and 2 persistence actions.")
    md.append("- 165 rows are non-profile-path controls: viewport controls, raw map edit controls, editor infrastructure, UI navigation, global display settings, read-only observation, metadata receipt controls, selection scoping, and FONT/SKIN Reload focused-audit row.")
    md.append("- The 49 profile-path rows remain proof-open: 37 source-wired profile controls still need independent detached TERM++ rendered-cell delta evidence, 9 scoring controls have local render-buffer evidence only, 1 Role Buckets control needs disabled-lane proof, and 2 persistence controls have no immediate TERM++ delta expectation.")
    md.append("- Source-citation corrections from the previous matrix audit remain part of this package: starter rows use `platform/terminal_gl_present.cpp:1693-1710` for profile-color consumption, the runtime resolver range is `engine/fl4131_runtime_harri_resolver.cpp:1421-1475`, and section3 sliders consume profile color through `Fl4260GetActiveProfileColor` in render paths.")
    md.append("")
    md.append("## Backend Matrix Excluded Control Gaps")
    md.append("")
    md.append("`fl4260-backend-matrix-excluded-control-gaps.csv`")
    md.append("")
    md.append(f"This gap CSV contains `{len(backend_gap_rows)}` inventory rows that are not covered by the 214-row backend proof matrix. The excluded surface is not empty background; it includes diagnostic controls, ID-only controls, runtime-label controls, and the ReviveMin/ReviveMax label-backing anomaly. Each row keeps its target leaf, backend owner summary, render/proof consumer summary, gap reason, and required next action.")
    md.append("")
    md.append("Gap counts by inventory status:")
    md.append("")
    for key in sorted(backend_gap_statuses):
        md.append(f"- `{key}`: {by_status.get(key, 0)}")
    md.append("")
    md.append(f"Current backend-matrix status: `fl4260-complete-backend-proof-matrix.csv` is a 214-row included-row matrix, not a complete per-control matrix for all `{len(rows)}` scanner rows. The backend classification gap remains open for the `{len(backend_gap_rows)}` excluded rows above, with the highest product-risk subset being the `{non_diagnostic_backend_gap_rows}` non-diagnostic excluded rows.")
    OUT_MD.write_text("\n".join(md) + "\n")
    print(OUT_CSV)
    print(OUT_RESIDUAL_CSV)
    print(OUT_BACKEND_GAP_CSV)
    print(OUT_MD)
    print(f"rows={len(rows)} residuals={len(residuals)} unmapped={unmapped} backend_gap_rows={len(backend_gap_rows)} head={head}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
