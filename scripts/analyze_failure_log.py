#!/usr/bin/env python3
"""Dedicated failure-log front door.

Extra repo-scope query commands:
  path        show resolved canonical/legacy FAILURE_LOG paths
  context     compact session-start context for dirty files + proof state
  categories  histogram of raw Category values
  tags        histogram of normalized tags derived from Category values
  epochs      epoch boundary timeline (FL entries tagged epoch_boundary)
  epoch-statuses  all distinct EpochStatus values used in the overlay
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import cli_style
except ModuleNotFoundError:
    class _PlainCliStyle:
        @staticmethod
        def set_color(_enabled: bool) -> None:
            return

        @staticmethod
        def style(value: object, _kind: str = "") -> str:
            return str(value)

        @staticmethod
        def status(value: object) -> str:
            return str(value)

        @staticmethod
        def header(value: object) -> None:
            print(str(value))

        @staticmethod
        def kv(pairs: list[tuple[str, object]], indent: int = 0) -> str:
            prefix = " " * indent
            return "\n".join(f"{prefix}{key}: {value}" for key, value in pairs)

    cli_style = _PlainCliStyle()
from fl_cli_contract import (
    FORWARDED_FL_COMMAND_NAMES,
    FRONT_DOOR_NATIVE_COMMAND_NAMES,
    front_door_contract_payload,
)
from maintainer.lib.failure_log import (
    _FL_OVERLAY_BLOCK_RE,
    find_failure_entry_blocks as shared_find_failure_entry_blocks,
    iter_failure_entry_blocks,
    read_failure_log,
    read_fl_overlay,
)
from maintainer.lib.fl_config import (
    CANONICAL_FAILURE_LOG_CANDIDATES,
    CANONICAL_FAILURE_LOG_REL,
    LEGACY_FAILURE_LOG_REL,
)
from maintainer.lib.fl_overlay import _effective_overlay_record, _normalize_overlay_list_value

_JSON_MODE: bool = False

SCRIPT_DIR = Path(__file__).resolve().parent

_COMPAT_FL_COMMANDS = FORWARDED_FL_COMMAND_NAMES


class _RunsCompat:
    """Small FL-only compatibility layer after the run/proof analyzer deletion."""

    _fl_cache: dict[str, object] = {}
    _FL_OVERLAY_BLOCK_RE = _FL_OVERLAY_BLOCK_RE
    CANONICAL_FAILURE_LOG_CANDIDATES = CANONICAL_FAILURE_LOG_CANDIDATES

    @staticmethod
    def repo_root() -> Path:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SystemExit("error: could not determine repo root via git")
        return Path(result.stdout.strip())

    @staticmethod
    def canonical_failure_log_path(root: Path) -> Path:
        current = root / CANONICAL_FAILURE_LOG_REL
        if current.exists():
            return current
        legacy = root / LEGACY_FAILURE_LOG_REL
        if legacy.exists():
            return legacy
        return current

    @classmethod
    def _load(cls, root: Path) -> tuple[list[dict], dict, dict]:
        fl_path = cls.canonical_failure_log_path(root)
        cache_key = str(fl_path)
        if cache_key in cls._fl_cache:
            return (
                cls._fl_cache[cache_key + ":entries"],  # type: ignore[return-value]
                cls._fl_cache[cache_key + ":overlay"],  # type: ignore[return-value]
                cls._fl_cache[cache_key + ":overlay_raw"],  # type: ignore[return-value]
            )
        overlay = read_fl_overlay(fl_path)
        overlay_raw = dict(overlay)
        blocks = iter_failure_entry_blocks(fl_path)
        parsed = read_failure_log(fl_path)
        entries: list[dict] = []
        for idx, entry in enumerate(parsed):
            block = blocks[idx][2] if idx < len(blocks) else ""
            fl_id = entry.failure_id
            try:
                num = int(re.search(r"\d+", fl_id).group(0))  # type: ignore[union-attr]
            except (AttributeError, ValueError):
                num = 0
            row = {
                "fl": fl_id,
                "num": num,
                "title": entry.title,
                "status": entry.status,
                "effective_status": entry.effective_status,
                "date": entry.date_opened,
                "category": entry.category,
                "description": entry.description,
                "related": list(entry.related_ids),
                "overlay": overlay.get(fl_id) or {},
                "_lines": block.splitlines(),
            }
            entries.append(row)
        cls._fl_cache[cache_key] = True
        cls._fl_cache[cache_key + ":entries"] = entries
        cls._fl_cache[cache_key + ":overlay"] = overlay
        cls._fl_cache[cache_key + ":overlay_raw"] = overlay_raw
        return entries, overlay, overlay_raw

    @classmethod
    def load_fl_entries(cls, root: Path) -> list[dict]:
        return cls._load(root)[0]

    @classmethod
    def load_fl_overlay(cls, root: Path) -> dict:
        return cls._load(root)[1]

    @classmethod
    def load_fl_overlay_raw(cls, root: Path) -> dict:
        return cls._load(root)[2]

    @staticmethod
    def normalize_fl_tag(raw: str) -> str:
        return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(raw or "").lower())).strip("_")

    @classmethod
    def effective_fl_overlay_record(cls, entry: dict | None = None, overlay: dict | None = None, *, fl_id: str | None = None) -> dict:
        category = str((entry or {}).get("category") or "")
        return _effective_overlay_record(
            fl_id or str((entry or {}).get("fl") or ""),
            default_category=category,
            overlay=overlay or ((entry or {}).get("overlay") if isinstance((entry or {}).get("overlay"), dict) else None),
            default_kinds=["unclassified"],
            default_proof_state="OPEN",
        )

    @classmethod
    def _overlay_record(cls, entry: dict) -> dict:
        return cls.effective_fl_overlay_record(entry, entry.get("overlay"), fl_id=str(entry.get("fl") or ""))

    @classmethod
    def _overlay_scalar(cls, entry: dict, key: str) -> str:
        value = cls._overlay_record(entry).get(key)
        return str(value).strip() if isinstance(value, str) else ""

    @classmethod
    def _overlay_list(cls, entry: dict, key: str) -> list[str]:
        return _normalize_overlay_list_value(cls._overlay_record(entry).get(key))

    @staticmethod
    def fl_status_display(entry: dict) -> str:
        return str(entry.get("effective_status") or entry.get("status") or "UNKNOWN")

    @classmethod
    def fl_status_matches(cls, entry: dict, needle: str | None) -> bool:
        return not needle or str(needle).lower() in cls.fl_status_display(entry).lower()

    @classmethod
    def failure_entry_tags(cls, entry: dict) -> list[str]:
        tags: set[str] = set()
        for part in re.split(r"[|/,]+", str(entry.get("category") or "")):
            normalized = cls.normalize_fl_tag(part)
            if normalized:
                tags.add(normalized)
        for key in ("Area", "ProofState", "EpochStatus"):
            value = cls.normalize_fl_tag(cls._overlay_scalar(entry, key))
            if value:
                tags.add(value if key == "Area" else f"{key.lower()}_{value}")
        for key in ("Subsystems", "Kinds"):
            for value in cls._overlay_list(entry, key):
                normalized = cls.normalize_fl_tag(value)
                if normalized:
                    tags.add(normalized)
        return sorted(tags)

    @classmethod
    def fl_entry_matches_overlay(
        cls,
        entry: dict,
        *,
        area: str | None = None,
        subsystem: str | None = None,
        kind: str | None = None,
        proof_state: str | None = None,
        epoch_status: str | None = None,
        complaint_ref: str | None = None,
        complaint_counter_state: str | None = None,
        code_ref: str | None = None,
        touched_file: str | None = None,
    ) -> bool:
        scalar_checks = {"Area": area, "ProofState": proof_state, "EpochStatus": epoch_status}
        for key, wanted in scalar_checks.items():
            if wanted and cls.normalize_fl_tag(wanted) != cls.normalize_fl_tag(cls._overlay_scalar(entry, key)):
                return False
        list_checks = {"Subsystems": subsystem, "Kinds": kind, "ComplaintRefs": complaint_ref, "CodeRefs": code_ref, "TouchedFiles": touched_file}
        for key, wanted in list_checks.items():
            if wanted and cls.normalize_fl_tag(wanted) not in {cls.normalize_fl_tag(v) for v in cls._overlay_list(entry, key)}:
                return False
        if complaint_counter_state:
            return cls.normalize_fl_tag(complaint_counter_state) in cls.normalize_fl_tag(cls._overlay_scalar(entry, "ComplaintCounterState"))
        return True

    @classmethod
    def _fl_text(cls, entry: dict) -> str:
        overlay = cls._overlay_record(entry)
        overlay_text = " ".join(
            str(value)
            for value in overlay.values()
            if isinstance(value, str)
        )
        overlay_text += " " + " ".join(
            " ".join(map(str, value))
            for value in overlay.values()
            if isinstance(value, list)
        )
        return " ".join(
            [
                str(entry.get("fl") or ""),
                str(entry.get("title") or ""),
                str(entry.get("category") or ""),
                str(entry.get("description") or ""),
                overlay_text,
                "\n".join(entry.get("_lines") or []),
            ]
        )

    @classmethod
    def _expand_cluster(cls, seed_ids: set[str], entries_by_id: dict[str, dict], entries: list[dict], terms: list[str]) -> set[str]:
        cluster = set(seed_ids)
        changed = True
        while changed:
            changed = False
            for entry in entries:
                fl_id = str(entry.get("fl") or "")
                related = set(entry.get("related") or []) | set(cls._overlay_list(entry, "ComplaintRefs"))
                if fl_id in cluster:
                    before = len(cluster)
                    cluster.update(ref for ref in related if ref in entries_by_id)
                    changed = changed or len(cluster) != before
                elif related & cluster:
                    cluster.add(fl_id)
                    changed = True
        return cluster

    @classmethod
    def fl_ledger_state(cls, entry: dict | None) -> str:
        if not entry:
            return "missing"
        status = cls.fl_status_display(entry).upper()
        proof = cls.normalize_fl_tag(cls._overlay_scalar(entry, "ProofState")).upper()
        if status in {"RESOLVED", "ACCOUNTED"} or proof in {"RESOLVED", "ACCOUNTED", "VERIFIED", "FIXED", "CLOSED"}:
            return "resolved-accounted"
        if proof in {"FAILED", "REJECTED", "RETRACTED"}:
            return "failed/rejected"
        if proof in {"IMPLEMENTED", "PARTIAL"}:
            return "implemented-unproven"
        if proof in {"RAW_OPEN", "RAWOPEN"}:
            return "raw-open-actionable"
        if status in {"OPEN", "PARTIAL", "MONITORING"}:
            body = "\n".join(entry.get("_lines") or [])
            return "implemented-unproven" if re.search(r"(?m)^#### Fix attempt\b", body) else "raw-open-actionable"
        return "unknown"

    @staticmethod
    def print_table(headers: list[str], rows: list[object]) -> None:
        data = [tuple(map(str, row)) for row in rows]
        widths = [len(str(h)) for h in headers]
        for row in data:
            for idx, cell in enumerate(row):
                if idx < len(widths):
                    widths[idx] = max(widths[idx], len(cell))
        print("  ".join(str(h).ljust(widths[idx]) for idx, h in enumerate(headers)))
        print("  ".join("-" * w for w in widths))
        for row in data:
            print("  ".join((row[idx] if idx < len(row) else "").ljust(widths[idx]) for idx in range(len(widths))))

    @staticmethod
    def _normalize_terms(raw: list) -> list[str]:
        result: list[str] = []
        for item in raw:
            result.extend(token.lower() for token in str(item).split() if len(token) >= 2)
        return result

    @classmethod
    def fl_entry_is_implemented_unproven(cls, entry: dict) -> bool:
        return cls.fl_ledger_state(entry) == "implemented-unproven"

    @staticmethod
    def fl_entries_missing_overlay(entries: list[dict], overlay_raw: dict) -> list[dict]:
        return [entry for entry in entries if str(entry.get("fl") or "") not in overlay_raw]

    @classmethod
    def fl_entry_by_id(cls, root: Path, fl_id: str) -> dict | None:
        target = str(fl_id or "").upper()
        for entry in reversed(cls.load_fl_entries(root)):
            if str(entry.get("fl") or "").upper() == target:
                return entry
        return None

    @classmethod
    def fl_required_fields_advisory_for_id(cls, root: Path, fl_id: str) -> dict:
        target = str(fl_id or "").strip().upper()
        entry = cls.fl_entry_by_id(root, target)
        if entry is None:
            return {
                "fl_id": target,
                "error": "not_found",
                "has_gap": False,
                "required_fields": [],
                "source": "missing",
            }
        required_fields = _normalize_overlay_list_value(
            cls._overlay_record(entry).get("RequiredFields") or []
        )
        if required_fields:
            return {
                "fl_id": target,
                "has_gap": True,
                "required_fields": required_fields,
                "source": "overlay",
            }
        return {"fl_id": target, "has_gap": False, "required_fields": [], "source": "fl-only"}

    @classmethod
    def fl_entry_gates(cls, root: Path, entry: dict) -> list[str]:
        overlay = cls._overlay_record(entry)
        gates = _normalize_overlay_list_value(overlay.get("GateNames") or overlay.get("gates"))
        if gates:
            return gates
        text = cls._fl_text(entry)
        return sorted(set(re.findall(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+){2,}\b", text)))


runs = _RunsCompat

_SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".js",
    ".mjs",
    ".py",
    ".html",
    ".md",
    ".json",
}

_DRIFT_SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".js",
    ".mjs",
    ".py",
    ".html",
}

STRICT_BUNDLE_LEDGER_IDS = (
    "FL-935", "FL-936", "FL-937", "FL-938", "FL-939", "FL-940", "FL-941",
    "FL-942", "FL-943", "FL-944", "FL-945", "FL-946", "FL-947", "FL-948",
    "FL-949", "FL-950", "FL-951", "FL-952", "FL-953", "FL-954", "FL-955",
    "FL-956", "FL-957", "FL-958", "FL-959", "FL-960", "FL-961", "FL-962",
    "FL-963", "FL-965", "FL-966", "FL-967", "FL-968", "FL-969", "FL-978",
    "FL-979", "FL-980", "FL-982", "FL-984", "FL-985", "FL-986", "FL-989",
    "FL-990", "FL-992", "FL-999", "FL-1001", "FL-1002", "FL-1003",
    "FL-1004", "FL-1005", "FL-1006", "FL-1007", "FL-1008", "FL-1009",
    "FL-1010", "FL-1011", "FL-1012", "FL-1013", "FL-1015", "FL-1016",
    "FL-1026", "FL-1027", "FL-1028", "FL-1029", "FL-1030", "FL-1031",
    "FL-1045", "FL-1046", "FL-1047", "FL-1048", "FL-1049", "FL-1050",
    "FL-1051", "FL-1052",
)
# Historical note: this remains the exact April 20 pre-candidate gate mirrored
# in the canon spec. The broader bundle-refactor lineage index lives in
# docs/audits/2026-05-14-bundle-refactor-fl-history.md and intentionally does
# not redefine this executable 74-row gate.


def _start_card(cmd_name: str, **kwargs: object) -> None:
    """Print a start card (suppressed in JSON mode)."""
    if _JSON_MODE:
        return
    cli_style.header(cmd_name)
    pairs = [(k.replace("_", " "), v) for k, v in kwargs.items() if v is not None]
    if pairs:
        print(cli_style.kv(pairs, indent=1))
    print()


def _repo_root() -> Path:
    return runs.repo_root()


def _load_entries() -> list[dict]:
    return runs.load_fl_entries(_repo_root())


def _filter_entries(
    entries: list[dict],
    *,
    status: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    area: str | None = None,
    subsystem: str | None = None,
    kind: str | None = None,
    proof_state: str | None = None,
    epoch_status: str | None = None,
    complaint_ref: str | None = None,
    complaint_counter_state: str | None = None,
    code_ref: str | None = None,
    touched_file: str | None = None,
) -> list[dict]:
    filtered = entries
    if status:
        filtered = [e for e in filtered if runs.fl_status_matches(e, status)]
    if category:
        needle = category.lower()
        filtered = [e for e in filtered if needle in (e.get("category") or "").lower()]
    if tag:
        wanted = runs.normalize_fl_tag(tag)
        filtered = [e for e in filtered if wanted in runs.failure_entry_tags(e)]
    filtered = [
        e
        for e in filtered
        if runs.fl_entry_matches_overlay(
            e,
            area=area,
            subsystem=subsystem,
            kind=kind,
            proof_state=proof_state,
            epoch_status=epoch_status,
            complaint_ref=complaint_ref,
            complaint_counter_state=complaint_counter_state,
            code_ref=code_ref,
            touched_file=touched_file,
        )
    ]
    return filtered


def _emit_json(payload: dict) -> int:
    print(json.dumps(payload, indent=2))
    return 0


def _write_overlay_rows(root: Path, rows: list[dict]) -> tuple[bool, str]:
    fl_path = runs.canonical_failure_log_path(root)
    content = fl_path.read_text(encoding="utf-8")
    m = runs._FL_OVERLAY_BLOCK_RE.search(content)
    if not m:
        return False, "overlay_block_not_found"
    insert_pos = m.end(1)
    new_lines = "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n"
    updated = content[:insert_pos] + new_lines + content[insert_pos:]
    fl_path.write_text(updated, encoding="utf-8")
    cache_key = str(fl_path)
    for suffix in ("", ":entries", ":overlay", ":overlay_raw"):
        runs._fl_cache.pop(cache_key + suffix, None)
    return True, ""


def _entry_num(entry: dict) -> int:
    try:
        return int(entry.get("num") or str(entry.get("fl", "FL-0")).split("-")[1])
    except Exception:
        return 0


def _entry_text(entry: dict) -> str:
    return runs._fl_text(entry)


def _entry_overlay(entry: dict) -> dict:
    overlay = entry.get("overlay") if isinstance(entry.get("overlay"), dict) else None
    return runs.effective_fl_overlay_record(entry, overlay, fl_id=entry.get("fl") or "")


def _normalize_rel_path(value: str) -> str:
    value = (value or "").strip().strip("`'\"")
    value = value.split(":", 1)[0] if ":" in value else value
    return value.replace("\\", "/").lstrip("./")


_FILE_REFS_CACHE: dict[str, set[str]] = {}


def _entry_file_refs(entry: dict) -> set[str]:
    fl_id = entry.get("fl") or ""
    if fl_id in _FILE_REFS_CACHE:
        return _FILE_REFS_CACHE[fl_id]

    refs: set[str] = set()
    overlay = _entry_overlay(entry)
    for key in ("CodeRefs", "TouchedFiles"):
        for raw in overlay.get(key) or []:
            normalized = _normalize_rel_path(str(raw))
            if normalized:
                refs.add(normalized)

    raw_text = _entry_text(entry)
    for match in re.finditer(
        r"(?<![A-Za-z0-9_./-])((?:addons|assets|docs|engine|scripts|server|tests|web)/[A-Za-z0-9_./@+\- ]+\.[A-Za-z0-9_]+)(?::\d+)?",
        raw_text,
    ):
        refs.add(_normalize_rel_path(match.group(1)))

    _FILE_REFS_CACHE[fl_id] = refs
    return refs


def _entry_mentions_file(entry: dict, rel_path: str) -> bool:
    wanted = _normalize_rel_path(rel_path).lower()
    if not wanted:
        return False
    for ref in _entry_file_refs(entry):
        ref_l = ref.lower()
        if ref_l == wanted or ref_l.endswith("/" + wanted) or wanted.endswith("/" + ref_l):
            return True
    return wanted in _entry_text(entry).lower()


def _entry_mentions_symbol(entry: dict, symbol: str) -> bool:
    symbol = (symbol or "").strip()
    if not symbol:
        return False
    return symbol.lower() in _entry_text(entry).lower()


def _term_score(entry: dict, terms: list[str]) -> int:
    if not terms:
        return 0
    text = _entry_text(entry).lower()
    return sum(1 for term in terms if term in text)


def _expanded_family(entries: list[dict], seed_ids: set[str], terms: list[str]) -> set[str]:
    if not seed_ids:
        return set()
    entries_by_id = {entry["fl"]: entry for entry in entries if entry.get("fl")}
    return runs._expand_cluster(seed_ids, entries_by_id, entries, terms)


def _sort_entries(entries: list[dict], score_by_id: dict[str, int] | None = None) -> list[dict]:
    score_by_id = score_by_id or {}
    return sorted(
        entries,
        key=lambda entry: (-score_by_id.get(entry.get("fl") or "", 0), -_entry_num(entry)),
    )


def _risk_label(entry: dict) -> str:
    state = runs.fl_ledger_state(entry)
    if state == "raw-open-actionable":
        return "open"
    return state


def _normalize_priority_value(value: object) -> str:
    matches = re.findall(r"\b(P[0-4])\b", str(value or "").upper())
    return matches[-1] if matches else ""


def _entry_has_fix_attempt(entry: dict) -> bool:
    return bool(re.search(r"(?m)^#### Fix attempt\b", _entry_text(entry)))


def _entry_proof_state(entry: dict) -> str:
    return str(_entry_overlay(entry).get("ProofState") or "").strip().upper()


def _strict_ledger_classification(entry: dict | None) -> str:
    return runs.fl_ledger_state(entry)


def _strict_bundle_ledger_payload(entries: list[dict]) -> dict:
    entries_by_id = {entry.get("fl"): entry for entry in entries if entry.get("fl")}
    buckets: dict[str, list[str]] = defaultdict(list)
    records: list[dict] = []
    for fl_id in STRICT_BUNDLE_LEDGER_IDS:
        entry = entries_by_id.get(fl_id)
        classification = _strict_ledger_classification(entry)
        buckets[classification].append(fl_id)
        records.append(
            {
                "fl": fl_id,
                "classification": classification,
                "status": runs.fl_status_display(entry) if entry else "MISSING",
                "proof_state": _entry_proof_state(entry) if entry else "MISSING",
                "has_fix_attempt": _entry_has_fix_attempt(entry) if entry else False,
                "title": entry.get("title") if entry else "",
            }
        )
    blocking = buckets.get("missing", []) + buckets.get("raw-open-actionable", [])
    return {
        "ledger": "strict_bundle_refactor",
        "total": len(STRICT_BUNDLE_LEDGER_IDS),
        "blocking": blocking,
        "ok": not blocking,
        "counts": {key: len(value) for key, value in sorted(buckets.items())},
        "buckets": {key: value for key, value in sorted(buckets.items())},
        "records": records,
    }


def _entry_row(entry: dict, score: int | None = None) -> tuple:
    score_text = "-" if score is None else str(score)
    return (
        entry.get("fl") or "",
        _risk_label(entry),
        score_text,
        (entry.get("title") or "")[:72],
    )


def _print_entry_table(title: str, entries: list[dict], score_by_id: dict[str, int] | None = None) -> None:
    print(cli_style.style(title, "subheader"))
    if not entries:
        print("  none")
        return
    rows = [
        _entry_row(entry, (score_by_id or {}).get(entry.get("fl") or ""))
        for entry in entries
    ]
    runs.print_table(["FL", "RISK", "SCORE", "TITLE"], rows)


def _requested_terms(args: argparse.Namespace) -> list[str]:
    raw_terms: list[str] = []
    raw_terms.extend(getattr(args, "terms", None) or [])
    raw_terms.extend(getattr(args, "term", None) or [])
    raw_terms.extend(getattr(args, "symbols", None) or [])
    return runs._normalize_terms(raw_terms)


def _source_file_candidates(
    entries: list[dict],
    requested_files: list[str],
    *,
    max_files: int = 20,
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        normalized = _normalize_rel_path(path)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    for path in requested_files:
        add(path)
    for entry in entries:
        for ref in sorted(_entry_file_refs(entry)):
            add(ref)
            if len(candidates) >= max_files:
                return candidates
    return candidates


def _read_source_lines(root: Path, rel_path: str) -> list[str]:
    rel_path = _normalize_rel_path(rel_path)
    if not rel_path:
        return []
    path = root / rel_path
    if path.suffix and path.suffix.lower() not in _SOURCE_EXTENSIONS:
        return []
    try:
        if not path.is_file():
            return []
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _is_drift_source_path(rel_path: str) -> bool:
    suffix = Path(rel_path).suffix.lower()
    return suffix in _DRIFT_SOURCE_EXTENSIONS


def _near_source_matches(
    root: Path,
    rel_path: str,
    *,
    anchor: str,
    forbidden: str,
    window: int = 5,
) -> list[dict]:
    lines = _read_source_lines(root, rel_path)
    if not lines:
        return []
    anchor_u = anchor.upper()
    forbidden_u = forbidden.upper()
    matches: list[dict] = []
    for index, line in enumerate(lines):
        if anchor_u not in line.upper():
            continue
        start = max(0, index - window)
        end = min(len(lines), index + window + 1)
        block = "\n".join(lines[start:end])
        if forbidden_u not in block.upper():
            continue
        matches.append(
            {
                "file": rel_path,
                "line": index + 1,
                "anchor": anchor,
                "forbidden": forbidden,
                "snippet": line.strip()[:160],
            }
        )
    return matches


def _drift_claims(entry: dict) -> list[dict]:
    text = _entry_text(entry).lower()
    claims: list[dict] = []
    if "airborne" in text and "falling" in text:
        if "no longer maps airborne" in text or "maps airborne to idle" in text or "airborne\u2192idle" in text or "airborne -> idle" in text:
            claims.append(
                {
                    "anchor": "AIRBORNE",
                    "forbidden": "FALLING",
                    "description": "entry says AIRBORNE should no longer map to FALLING",
                }
            )
    return claims


def _source_drift_signals(
    root: Path,
    entries: list[dict],
    requested_files: list[str],
    *,
    limit: int = 12,
) -> list[dict]:
    signals: list[dict] = []
    for entry in entries:
        claims = _drift_claims(entry)
        if not claims:
            continue
        files = [
            rel_path
            for rel_path in _source_file_candidates([entry], requested_files)
            if _is_drift_source_path(rel_path)
        ]
        for claim in claims:
            for rel_path in files:
                for match in _near_source_matches(
                    root,
                    rel_path,
                    anchor=claim["anchor"],
                    forbidden=claim["forbidden"],
                ):
                    signals.append(
                        {
                            "fl": entry.get("fl") or "",
                            "description": claim["description"],
                            **match,
                        }
                    )
                    if len(signals) >= limit:
                        return signals
    return signals


def _print_source_drift(signals: list[dict]) -> None:
    print(f"\n{cli_style.style('SOURCE DRIFT SIGNALS:', 'subheader')}")
    if not signals:
        print("  none")
        return
    for signal in signals:
        print(
            f"  {signal['fl']}: {signal['description']} but {signal['file']}:{signal['line']} "
            f"has {signal['anchor']} near {signal['forbidden']}"
        )
        if signal.get("snippet"):
            print(f"    > {signal['snippet']}")


def _preflight_payload(args: argparse.Namespace) -> dict:
    root = _repo_root()
    entries = _load_entries()
    terms = _requested_terms(args)
    files = [_normalize_rel_path(path) for path in (getattr(args, "files", None) or []) if path]
    symbols = [str(symbol).strip() for symbol in (getattr(args, "symbols", None) or []) if str(symbol).strip()]

    scores: dict[str, int] = {}
    seed_ids: set[str] = set()
    for entry in entries:
        fl_id = entry.get("fl") or ""
        term_score = _term_score(entry, terms)
        file_score = sum(1 for path in files if _entry_mentions_file(entry, path))
        symbol_score = sum(1 for symbol in symbols if _entry_mentions_symbol(entry, symbol))
        score = term_score + (file_score * 3) + (symbol_score * 4)
        if score <= 0:
            continue
        scores[fl_id] = score
        seed_ids.add(fl_id)

    family_ids = set(seed_ids)
    if not getattr(args, "no_family", False):
        family_ids = _expanded_family(entries, seed_ids, terms)

    entries_by_id = {entry["fl"]: entry for entry in entries if entry.get("fl")}
    direct_entries = _sort_entries([entries_by_id[fid] for fid in seed_ids if fid in entries_by_id], scores)
    family_entries = _sort_entries([entries_by_id[fid] for fid in family_ids if fid in entries_by_id], scores)
    high_risk = [
        entry
        for entry in family_entries
        if _risk_label(entry) in {"open", "failed/rejected", "implemented-unproven"}
    ]
    high_risk = sorted(
        high_risk,
        key=lambda entry: (
            0 if runs.fl_entry_is_implemented_unproven(entry) else 1,
            -scores.get(entry.get("fl") or "", 0),
            -_entry_num(entry),
        ),
    )
    limit = int(getattr(args, "limit", 12) or 12)
    drift_signals = _source_drift_signals(root, family_entries[:limit], files)

    return {
        "terms": terms,
        "files": files,
        "symbols": symbols,
        "direct": direct_entries[:limit],
        "family": family_entries[:limit],
        "high_risk": high_risk[:limit],
        "scores": scores,
        "source_drift_signals": drift_signals,
        "total_direct": len(direct_entries),
        "total_family": len(family_entries),
    }


def cmd_preflight(args: argparse.Namespace) -> int:
    payload = _preflight_payload(args)
    if args.json:
        serializable = dict(payload)
        for key in ("direct", "family", "high_risk"):
            serializable[key] = [
                {
                    "fl": entry.get("fl"),
                    "risk": _risk_label(entry),
                    "score": payload["scores"].get(entry.get("fl") or "", 0),
                    "title": entry.get("title"),
                }
                for entry in payload[key]
            ]
        return _emit_json(serializable)

    cli_style.header("FAILURE-LOG PREFLIGHT")
    print(f"  terms: {' '.join(payload['terms']) or '-'}")
    print(f"  files: {', '.join(cli_style.style(f, 'path') for f in payload['files']) or '-'}")
    print(f"  symbols: {', '.join(payload['symbols']) or '-'}")
    print()
    _print_entry_table("DIRECT MATCHES:", payload["direct"], payload["scores"])
    print()
    _print_entry_table("EXPANDED FAMILY:", payload["family"], payload["scores"])
    print()
    _print_entry_table("HIGH-RISK / UNPROVEN:", payload["high_risk"], payload["scores"])
    _print_source_drift(payload["source_drift_signals"])
    print(f"\n{cli_style.style('NEXT COMMANDS:', 'warn')}")
    if payload["family"]:
        top = payload["family"][0].get("fl")
        print(f"  python3 scripts/analyze_failure_log.py family {top}")
        print(f"  python3 scripts/analyze_failure_log.py history-audit --fl {top}")
    else:
        print("  No FL family found. Use suggest-related before adding a new entry.")
    return 0


def cmd_by_file(args: argparse.Namespace) -> int:
    entries = _filter_entries(
        _load_entries(),
        status=getattr(args, "status", None),
        proof_state=getattr(args, "proof_state", None),
    )
    if getattr(args, "implemented_unproven", False):
        entries = [entry for entry in entries if runs.fl_entry_is_implemented_unproven(entry)]
    if getattr(args, "date_from", None):
        entries = [e for e in entries if (e.get("date") or "") >= args.date_from]
    if getattr(args, "date_to", None):
        entries = [e for e in entries if (e.get("date") or "") <= args.date_to]
    files = [_normalize_rel_path(path) for path in args.files]
    scores: dict[str, int] = {}
    seed_ids: set[str] = set()
    for entry in entries:
        score = sum(1 for path in files if _entry_mentions_file(entry, path))
        if score:
            fl_id = entry.get("fl") or ""
            scores[fl_id] = score
            seed_ids.add(fl_id)
    entries_by_id = {entry["fl"]: entry for entry in entries if entry.get("fl")}
    ids = seed_ids
    if getattr(args, "family", False):
        ids = _expanded_family(entries, seed_ids, files)
    matched = _sort_entries([entries_by_id[fid] for fid in ids if fid in entries_by_id], scores)
    matched = matched[: args.limit]
    if args.json:
        return _emit_json(
            {
                "files": files,
                "records": [
                    {
                        "fl": entry.get("fl"),
                        "risk": _risk_label(entry),
                        "score": scores.get(entry.get("fl") or "", 0),
                        "title": entry.get("title"),
                    }
                    for entry in matched
                ],
            }
        )
    _print_entry_table(f"FL ENTRIES TOUCHING: {', '.join(files)}", matched, scores)
    return 0


def cmd_by_symbol(args: argparse.Namespace) -> int:
    entries = _filter_entries(
        _load_entries(),
        status=getattr(args, "status", None),
        proof_state=getattr(args, "proof_state", None),
    )
    if getattr(args, "implemented_unproven", False):
        entries = [entry for entry in entries if runs.fl_entry_is_implemented_unproven(entry)]
    if getattr(args, "date_from", None):
        entries = [e for e in entries if (e.get("date") or "") >= args.date_from]
    if getattr(args, "date_to", None):
        entries = [e for e in entries if (e.get("date") or "") <= args.date_to]
    symbols = [str(symbol).strip() for symbol in args.symbols if str(symbol).strip()]
    scores: dict[str, int] = {}
    seed_ids: set[str] = set()
    for entry in entries:
        score = sum(1 for symbol in symbols if _entry_mentions_symbol(entry, symbol))
        if score:
            fl_id = entry.get("fl") or ""
            scores[fl_id] = score
            seed_ids.add(fl_id)
    entries_by_id = {entry["fl"]: entry for entry in entries if entry.get("fl")}
    ids = seed_ids
    if getattr(args, "family", False):
        ids = _expanded_family(entries, seed_ids, symbols)
    matched = _sort_entries([entries_by_id[fid] for fid in ids if fid in entries_by_id], scores)
    matched = matched[: args.limit]
    if args.json:
        return _emit_json(
            {
                "symbols": symbols,
                "records": [
                    {
                        "fl": entry.get("fl"),
                        "risk": _risk_label(entry),
                        "score": scores.get(entry.get("fl") or "", 0),
                        "title": entry.get("title"),
                    }
                    for entry in matched
                ],
            }
        )
    _print_entry_table(f"FL ENTRIES MENTIONING: {', '.join(symbols)}", matched, scores)
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    root = _repo_root()
    resolved = runs.canonical_failure_log_path(root)
    payload = {
        "resolved": str(resolved),
        "exists": resolved.exists(),
        "candidates": [
            {
                "path": str(root / rel),
                "exists": (root / rel).exists(),
            }
            for rel in runs.CANONICAL_FAILURE_LOG_CANDIDATES
        ],
    }
    if args.json:
        return _emit_json(payload)

    print(f"resolved: {payload['resolved']}")
    for candidate in payload["candidates"]:
        mark = "active" if candidate["path"] == payload["resolved"] else "legacy"
        exists = "present" if candidate["exists"] else "missing"
        print(f"  {mark:9} {exists:7} {candidate['path']}")
    return 0


def cmd_dump(args: argparse.Namespace) -> int:
    """Print the full raw markdown body of an FL entry (all append blocks).

    The canonical FAILURE_LOG is hook-guarded against direct reads; this is the
    front-door reader so agents can audit an entry's full history (e.g. the
    FL-4231 actor-occlusion attempt log) instead of only the preflight title.
    """
    root = _repo_root()
    fl_path = runs.canonical_failure_log_path(root)
    blocks = shared_find_failure_entry_blocks(fl_path, args.fl_id)
    if not blocks:
        print(f"No FL entry found for {args.fl_id} in {fl_path}")
        return 1
    for block in blocks:
        print(block)
        print()
    return 0


def cmd_categories(args: argparse.Namespace) -> int:
    _start_card("categories", mode="read-only")
    entries = _filter_entries(
        _load_entries(),
        status=args.status,
        category=args.category,
        tag=args.tag,
        area=getattr(args, "area", None),
        subsystem=getattr(args, "subsystem", None),
        kind=getattr(args, "kind", None),
        proof_state=getattr(args, "proof_state", None),
        epoch_status=getattr(args, "epoch_status", None),
        complaint_ref=getattr(args, "complaint_ref", None),
        complaint_counter_state=getattr(args, "complaint_counter_state", None),
        code_ref=getattr(args, "code_ref", None),
        touched_file=getattr(args, "touched_file", None),
    )
    buckets: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        buckets[(entry.get("category") or "(none)").strip() or "(none)"].append(entry)

    rows = []
    payload_rows = []
    for category, bucket in sorted(
        buckets.items(),
        key=lambda item: (-len(item[1]), item[0].lower()),
    )[: args.limit]:
        tag_counter = Counter()
        for entry in bucket:
            tag_counter.update(runs.failure_entry_tags(entry))
        top_tags = ", ".join(tag for tag, _ in tag_counter.most_common(6)) or "-"
        rows.append((category, len(bucket), top_tags))
        payload_rows.append(
            {"category": category, "entries": len(bucket), "top_tags": top_tags}
        )

    payload = {"total_entries": len(entries), "rows": payload_rows}
    if args.json:
        return _emit_json(payload)

    runs.print_table(["CATEGORY", "ENTRIES", "TOP TAGS"], rows)
    print(f"\n{len(entries)} entries across {len(buckets)} categories.")
    return 0


def cmd_tags(args: argparse.Namespace) -> int:
    _start_card("tags", mode="read-only")
    entries = _filter_entries(
        _load_entries(),
        status=args.status,
        category=args.category,
        tag=args.tag,
        area=getattr(args, "area", None),
        subsystem=getattr(args, "subsystem", None),
        kind=getattr(args, "kind", None),
        proof_state=getattr(args, "proof_state", None),
        epoch_status=getattr(args, "epoch_status", None),
        complaint_ref=getattr(args, "complaint_ref", None),
        complaint_counter_state=getattr(args, "complaint_counter_state", None),
        code_ref=getattr(args, "code_ref", None),
        touched_file=getattr(args, "touched_file", None),
    )
    counts: Counter[str] = Counter()
    raw_categories: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        raw_category = (entry.get("category") or "(none)").strip() or "(none)"
        for tag in runs.failure_entry_tags(entry):
            counts[tag] += 1
            raw_categories[tag].add(raw_category)

    rows = []
    payload_rows = []
    for tag, count in counts.most_common(args.limit):
        categories = sorted(raw_categories[tag])
        sample = ", ".join(categories[:4])
        if len(categories) > 4:
            sample += ", ..."
        rows.append((tag, count, sample or "-"))
        payload_rows.append(
            {
                "tag": tag,
                "entries": count,
                "raw_categories": categories,
            }
        )

    payload = {"total_entries": len(entries), "rows": payload_rows}
    if args.json:
        return _emit_json(payload)

    runs.print_table(["TAG", "ENTRIES", "RAW CATEGORIES"], rows)
    print(f"\n{len(entries)} entries produced {len(counts)} normalized tags.")
    return 0


# ---------------------------------------------------------------------------
# overlay audit helpers
# ---------------------------------------------------------------------------

_OVERLAY_LOSS_LIST_FIELDS = (
    "Subsystems",
    "Kinds",
    "ComplaintRefs",
    "CodeRefs",
    "TouchedFiles",
    "RQRefs",
    "GitHubRefs",
    "RequiredFields",
    "AnchorFunctions",
)


def _overlay_jsonl_rows_with_lines(root: Path) -> list[tuple[int, dict]]:
    fl_path = runs.canonical_failure_log_path(root)
    try:
        lines = fl_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    block_start = None
    for idx, line in enumerate(lines, start=1):
        if line.startswith("## FL Metadata Overlay"):
            for sub_idx in range(idx + 1, len(lines) + 1):
                if lines[sub_idx - 1].startswith("```jsonl"):
                    block_start = sub_idx + 1
                    break
            break
    if block_start is None:
        return []
    rows: list[tuple[int, dict]] = []
    for abs_line in range(block_start, len(lines) + 1):
        raw_line = lines[abs_line - 1].strip()
        if raw_line.startswith("```"):
            break
        if not raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict) and record.get("fl"):
            rows.append((abs_line, record))
    return rows


def _overlay_loss_audit(root: Path, *, include_blame: bool = False) -> dict:
    rows = _overlay_jsonl_rows_with_lines(root)
    by_fl: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    effective: dict[str, dict] = {}
    last_line: dict[str, int] = {}
    for abs_line, record in rows:
        fl_id = str(record.get("fl") or "").strip()
        if not fl_id:
            continue
        by_fl[fl_id].append((abs_line, record))
        merged = dict(effective.get(fl_id) or {})
        merged.update(record)
        effective[fl_id] = merged
        last_line[fl_id] = abs_line

    losses: list[dict] = []
    for fl_id, fl_rows in by_fl.items():
        current = effective.get(fl_id, {})
        for field in _OVERLAY_LOSS_LIST_FIELDS:
            historical: list[str] = []
            seen: set[str] = set()
            for _abs_line, record in fl_rows:
                value = record.get(field)
                if not isinstance(value, list):
                    continue
                for item in value:
                    cleaned = str(item).strip()
                    if cleaned and cleaned not in seen:
                        historical.append(cleaned)
                        seen.add(cleaned)
            current_values = current.get(field)
            current_set = set(str(item).strip() for item in current_values if str(item).strip()) if isinstance(current_values, list) else set()
            lost = [item for item in historical if item not in current_set]
            if lost:
                losses.append(
                    {
                        "fl": fl_id,
                        "line": last_line.get(fl_id),
                        "field": field,
                        "lost_count": len(lost),
                        "lost_examples": lost[:12],
                    }
                )

    blame_by_line: dict[int, tuple[str, str]] = {}
    if include_blame and losses:
        try:
            blame = subprocess.check_output(
                ["git", "blame", "--line-porcelain", "docs/FAILURE_LOG.md"],
                cwd=str(root),
                text=True,
                errors="replace",
            )
        except (OSError, subprocess.CalledProcessError):
            blame = ""
        current_commit = ""
        line_no = 0
        summaries: dict[str, str] = {}
        commits: dict[int, str] = {}
        for line in blame.splitlines():
            if re.match(r"^[0-9a-f]{40} ", line):
                current_commit = line.split()[0]
                line_no += 1
                commits[line_no] = current_commit
            elif line.startswith("summary ") and current_commit and current_commit not in summaries:
                summaries[current_commit] = line[len("summary ") :]
        for line in {int(item["line"] or 0) for item in losses if item.get("line")}:
            commit = commits.get(line, "")
            if commit:
                blame_by_line[line] = (commit, summaries.get(commit, ""))
        for item in losses:
            line = int(item.get("line") or 0)
            if line in blame_by_line:
                commit, summary = blame_by_line[line]
                item["blame_commit"] = commit[:8]
                item["blame_summary"] = summary

    by_field = Counter(str(item["field"]) for item in losses)
    by_commit = Counter(str(item.get("blame_commit") or "") for item in losses if item.get("blame_commit"))
    return {
        "overlay_rows": len(rows),
        "unique_fl": len(by_fl),
        "loss_fields": len(losses),
        "affected_fl": len({str(item["fl"]) for item in losses}),
        "by_field": dict(sorted(by_field.items())),
        "by_commit": [
            {"commit": commit, "loss_fields": count}
            for commit, count in by_commit.most_common()
        ],
        "losses": losses,
    }


def cmd_overlay(args: argparse.Namespace) -> int:
    """Show FL metadata overlay records."""
    if not getattr(args, "json", False):
        _start_card("overlay", mode="read-only")
    root = _repo_root()
    overlay = runs.load_fl_overlay(root)
    overlay_raw = runs.load_fl_overlay_raw(root)
    missing = getattr(args, "missing", False)
    loss_audit = getattr(args, "loss_audit", False)

    fill_defaults = getattr(args, "fill_defaults", False)
    import_proposals = getattr(args, "import_proposals", "")
    write_mode = getattr(args, "write", False)
    patch_mode = getattr(args, "patch", False)

    if patch_mode:
        print("error: overlay --patch is HARD-DISABLED. It replaced array fields (Kinds, ComplaintRefs, Subsystems, etc.) instead of merging. Use overlay --import-proposals for gap-fill-only patches, or fl fix-attempt for LINEAGE_JSON blocks. See doc-hygiene/SKILL.md.", file=sys.stderr)
        return 2

    if fill_defaults and import_proposals:
        print("--fill-defaults and --import-proposals cannot be combined", file=sys.stderr)
        return 2

    if loss_audit:
        payload = _overlay_loss_audit(root, include_blame=getattr(args, "blame", False))
        if args.json:
            return _emit_json(payload)
        cli_style.header("OVERLAY LOSS AUDIT")
        print(f"  overlay rows:      {payload['overlay_rows']}")
        print(f"  unique FL ids:     {payload['unique_fl']}")
        print(f"  affected FL ids:   {cli_style.style(payload['affected_fl'], 'count')}")
        print(f"  lost field sets:   {cli_style.style(payload['loss_fields'], 'count')}")
        print(f"  by field:          {payload['by_field']}")
        if payload.get("by_commit"):
            print("  top commits:")
            for row in payload["by_commit"][:10]:
                print(f"    {row['commit']}  {row['loss_fields']}")
        print()
        limit = getattr(args, "limit", None) or 30
        for row in payload["losses"][:limit]:
            commit = f" {row.get('blame_commit')}" if row.get("blame_commit") else ""
            print(
                f"  {row['fl']} line={row['line']} field={row['field']}"
                f" lost={row['lost_count']}{commit} examples={row['lost_examples']}"
            )
        if len(payload["losses"]) > limit:
            print(f"  ... and {len(payload['losses']) - limit} more")
        return 0

    if write_mode and not (fill_defaults or import_proposals):
        print("--write requires --fill-defaults or --import-proposals", file=sys.stderr)
        return 2

    if fill_defaults:
        entries = _load_entries()
        missing_entries = runs.fl_entries_missing_overlay(entries, overlay_raw)
        new_rows = []
        for entry in sorted(
            missing_entries,
            key=lambda e: int(str(e.get("fl", "FL-0")).split("-")[1]),
        ):
            fl_id = entry.get("fl") or ""
            record = runs.effective_fl_overlay_record(entry, None, fl_id=fl_id)
            # Keep only the fields that constitute a minimal overlay row
            row = {
                "fl": fl_id,
                "Area": record.get("Area", ""),
                "Kinds": record.get("Kinds", []),
                "ProofState": record.get("ProofState", "OPEN"),
                "CodeRefs": record.get("CodeRefs", []),
            }
            new_rows.append(row)

        if not new_rows:
            payload = {
                "generated": 0,
                "rows": [],
                "write_requested": write_mode,
                "written_count": 0,
            }
            if args.json:
                return _emit_json(payload)
            print("All entries already have overlay rows — nothing to generate.")
            return 0

        mode_label = "WRITE" if write_mode else "DRY-RUN"
        if not args.json:
            cli_style.header(f"OVERLAY FILL-DEFAULTS ({mode_label})")
            print(f"  entries missing overlay: {cli_style.style(len(new_rows), 'count')}")
            print()
            for row in new_rows[:20]:
                print(f"  {json.dumps(row, separators=(',', ':'))}")
            if len(new_rows) > 20:
                print(f"  ... and {len(new_rows) - 20} more")

        payload = {
            "generated": len(new_rows),
            "rows": new_rows,
            "write_requested": write_mode,
            "written_count": 0,
        }

        if write_mode:
            ok, error = _write_overlay_rows(root, new_rows)
            if not ok:
                if args.json:
                    print(json.dumps({
                        "ok": False,
                        "command": "overlay",
                        "error": error,
                    }, indent=2))
                    return 1
                print("error: FL Metadata Overlay block not found in FAILURE_LOG.md", file=sys.stderr)
                return 1
            payload["written_count"] = len(new_rows)
            if not args.json:
                print(f"\n{cli_style.style('WRITTEN', 'ok')}: {cli_style.style(len(new_rows), 'count')} rows appended to overlay block.")
        else:
            if not args.json:
                print(f"\nDry run — no changes written. Use --write to append these rows.")
        if args.json:
            return _emit_json(payload)
        return 0

    if import_proposals:
        entries = _load_entries()
        entries_by_id = {entry.get("fl") or "": entry for entry in entries if entry.get("fl")}
        proposal_path = Path(import_proposals)
        if not proposal_path.exists():
            if args.json:
                return _emit_json(
                    {
                        "generated": 0,
                        "rows": [],
                        "write_requested": write_mode,
                        "written_count": 0,
                        "error": "proposal_file_not_found",
                        "path": str(proposal_path),
                    }
                )
            print(f"error: proposal file not found: {proposal_path}", file=sys.stderr)
            return 1

        new_rows: list[dict] = []
        skipped: list[dict[str, str]] = []
        for lineno, raw_line in enumerate(proposal_path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                proposal = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                skipped.append({"line": str(lineno), "reason": "invalid_json"})
                continue
            fl_id = str(proposal.get("fl") or "").strip().upper()
            if not re.fullmatch(r"FL-\d{3,}", fl_id):
                skipped.append({"line": str(lineno), "fl": fl_id or "-", "reason": "noncanonical_fl_id"})
                continue
            entry = entries_by_id.get(fl_id)
            if not entry:
                skipped.append({"line": str(lineno), "fl": fl_id, "reason": "unknown_fl_id"})
                continue
            raw_record = overlay_raw.get(fl_id) if isinstance(overlay_raw.get(fl_id), dict) else {}
            patch_row = {"fl": fl_id}

            proposed_category = str(proposal.get("proposed_category") or "").strip()
            if proposed_category and not str(raw_record.get("Category") or "").strip():
                patch_row["Category"] = proposed_category

            proposed_priority = _normalize_priority_value(proposal.get("proposed_priority"))
            if proposed_priority and not str(raw_record.get("Priority") or "").strip():
                patch_row["Priority"] = proposed_priority

            if not str(raw_record.get("Area") or "").strip():
                proposed_area = str(proposal.get("proposed_area") or "").strip()
                if proposed_area:
                    patch_row["Area"] = proposed_area

            if not raw_record.get("Kinds"):
                proposed_kinds = proposal.get("proposed_kinds")
                if isinstance(proposed_kinds, list) and proposed_kinds:
                    patch_row["Kinds"] = [str(item).strip() for item in proposed_kinds if str(item).strip()]

            if not str(raw_record.get("ProofState") or "").strip():
                proposed_proof_state = str(proposal.get("proposed_proof_state") or "").strip().upper()
                if proposed_proof_state:
                    patch_row["ProofState"] = proposed_proof_state

            if not raw_record.get("ComplaintRefs"):
                proposed_complaint_refs = proposal.get("proposed_complaint_refs")
                if isinstance(proposed_complaint_refs, list) and proposed_complaint_refs:
                    patch_row["ComplaintRefs"] = [str(item).strip() for item in proposed_complaint_refs if str(item).strip()]

            if not raw_record.get("TouchedFiles"):
                proposed_touched_files = proposal.get("proposed_touched_files")
                if isinstance(proposed_touched_files, list) and proposed_touched_files:
                    patch_row["TouchedFiles"] = [str(item).strip() for item in proposed_touched_files if str(item).strip()]

            if not raw_record.get("CodeRefs"):
                proposed_code_refs = proposal.get("proposed_code_refs")
                if isinstance(proposed_code_refs, list) and proposed_code_refs:
                    patch_row["CodeRefs"] = [str(item).strip() for item in proposed_code_refs if str(item).strip()]

            if len(patch_row) == 1:
                continue
            new_rows.append(patch_row)

        payload = {
            "generated": len(new_rows),
            "rows": new_rows,
            "write_requested": write_mode,
            "written_count": 0,
            "skipped": skipped,
            "path": str(proposal_path),
        }

        if write_mode and new_rows:
            ok, error = _write_overlay_rows(root, new_rows)
            if not ok:
                if args.json:
                    payload["error"] = error
                    return _emit_json(payload)
                print("error: FL Metadata Overlay block not found in FAILURE_LOG.md", file=sys.stderr)
                return 1
            payload["written_count"] = len(new_rows)

        if args.json:
            return _emit_json(payload)

        mode_label = "WRITE" if write_mode else "DRY-RUN"
        cli_style.header(f"OVERLAY IMPORT-PROPOSALS ({mode_label})")
        print(f"  proposal file: {proposal_path}")
        print(f"  generated rows: {cli_style.style(len(new_rows), 'count')}")
        print(f"  skipped lines:  {cli_style.style(len(skipped), 'count')}")
        print()
        for row in new_rows[:20]:
            print(f"  {json.dumps(row, separators=(',', ':'))}")
        if len(new_rows) > 20:
            print(f"  ... and {len(new_rows) - 20} more")
        if not write_mode:
            print("\nDry run — no changes written. Use --write to append these rows.")
        elif new_rows:
            print(f"\n{cli_style.style('WRITTEN', 'ok')}: {cli_style.style(len(new_rows), 'count')} rows appended to overlay block.")
        else:
            print("\nNo missing metadata fields were eligible for import.")
        return 0

    if args.fl and missing:
        print("--fl and --missing cannot be combined", file=sys.stderr)
        return 2

    if args.fl:
        fl_id = args.fl.upper()
        if not fl_id.startswith("FL-"):
            fl_id = f"FL-{fl_id}"
        record = overlay.get(fl_id)
        if not record:
            print(f"No failure log entry for {fl_id}", file=sys.stderr)
            return 1
        if args.json:
            return _emit_json(record)
        for k, v in record.items():
            if k == "fl":
                continue
            print(f"  {k}: {v}")
        return 0

    entries = _load_entries()

    filtered_entries = _filter_entries(
        entries,
        status=getattr(args, "status", None),
        category=getattr(args, "category", None),
        tag=getattr(args, "tag", None),
        area=getattr(args, "area", None),
        subsystem=getattr(args, "subsystem", None),
        kind=getattr(args, "kind", None),
        proof_state=getattr(args, "proof_state", None),
        epoch_status=getattr(args, "epoch_status", None),
        complaint_ref=getattr(args, "complaint_ref", None),
        complaint_counter_state=getattr(args, "complaint_counter_state", None),
        code_ref=getattr(args, "code_ref", None),
        touched_file=getattr(args, "touched_file", None),
    )
    if missing:
        filtered_entries = runs.fl_entries_missing_overlay(filtered_entries, overlay_raw)
    if getattr(args, "implemented_unproven", False):
        filtered_entries = [
            entry for entry in filtered_entries if runs.fl_entry_is_implemented_unproven(entry)
        ]

    rows = []
    payload_records = []
    limit = getattr(args, "limit", None)
    for entry in sorted(
        filtered_entries,
        key=lambda item: int(str(item.get("fl", "FL-0")).split("-")[1]),
    )[:limit]:
        fl_id = entry.get("fl") or ""
        record = runs.effective_fl_overlay_record(entry, overlay.get(fl_id), fl_id=fl_id)
        area = record.get("Area", "")
        proof_state = record.get("ProofState", "")
        epoch_status = record.get("EpochStatus", "")
        kinds = ", ".join(record.get("Kinds") or [])
        rows.append((fl_id, area, proof_state, epoch_status, kinds))
        payload_records.append(record)

    payload_key = "missing_count" if missing else "overlay_count"
    payload = {payload_key: len(payload_records), "records": payload_records}
    if args.json:
        return _emit_json(payload)

    if not rows:
        if missing:
            print("No entries are missing raw overlay rows.")
        else:
            print("No overlay records found.")
        return 0

    runs.print_table(["FL", "AREA", "PROOF STATE", "EPOCH STATUS", "KINDS"], rows)
    if missing:
        print(f"\n{len(payload_records)} missing overlay row(s).")
    else:
        print(f"\n{len(payload_records)} overlay record(s).")
    return 0


def cmd_epochs(args: argparse.Namespace) -> int:
    """Show epoch boundary timeline — FL entries tagged with epoch_boundary subsystem."""
    root = _repo_root()
    overlay = runs.load_fl_overlay(root)
    entries = _load_entries()

    # Find entries with epoch_boundary in Subsystems
    epoch_entries = []
    for entry in entries:
        fl_id = entry.get("fl") or ""
        record = runs.effective_fl_overlay_record(
            entry,
            overlay.get(fl_id) or (entry.get("overlay") if isinstance(entry.get("overlay"), dict) else None),
            fl_id=fl_id,
        )
        subsystems = record.get("Subsystems") or []
        if "epoch_boundary" in subsystems:
            # Extract EPOCH N label from ComplaintCounterState
            ccs = record.get("ComplaintCounterState") or ""
            epoch_label = ""
            epoch_num = -1
            m = re.search(r"EPOCH\s+(\d+)\s*/\s*([^(]+)", ccs)
            if m:
                epoch_num = int(m.group(1))
                epoch_label = f"EPOCH {m.group(1)} / {m.group(2).strip()}"
            else:
                m2 = re.search(r"EPOCH\s+(\d+)", ccs)
                if m2:
                    epoch_num = int(m2.group(1))
                    epoch_label = f"EPOCH {m2.group(1)}"
            # Extract date from parenthetical
            date_match = re.search(r"\((\d{4}-\d{2}-\d{2})[^)]*\)", ccs)
            epoch_date = date_match.group(1) if date_match else (entry.get("date") or "")
            proof_state = record.get("ProofState") or ""
            # First line of CCS as summary (up to 120 chars)
            summary = ccs.split(".")[0].strip() if ccs else ""
            if len(summary) > 120:
                summary = summary[:117] + "..."
            epoch_entries.append({
                "fl": fl_id,
                "epoch_num": epoch_num,
                "epoch_label": epoch_label,
                "date": epoch_date,
                "proof_state": proof_state,
                "summary": summary,
                "complaint_counter_state": ccs,
            })

    epoch_entries.sort(key=lambda x: x["epoch_num"])

    if args.json:
        return _emit_json({"epoch_count": len(epoch_entries), "epochs": epoch_entries})

    if not epoch_entries:
        print("No epoch boundary entries found.")
        return 0

    if not getattr(args, "json", False):
        _start_card("epochs", mode="timeline")

    rows = []
    for ep in epoch_entries:
        rows.append((ep["fl"], str(ep["epoch_num"]), ep["date"], ep["proof_state"], ep["epoch_label"]))

    runs.print_table(["FL", "EPOCH", "DATE", "PROOF STATE", "LABEL"], rows)
    print(f"\n{len(epoch_entries)} epoch boundary(s).")
    return 0


def cmd_epoch_statuses(args: argparse.Namespace) -> int:
    """List all distinct EpochStatus values used in the overlay."""
    root = _repo_root()
    overlay = runs.load_fl_overlay(root)
    entries = _load_entries()

    status_counts: dict[str, int] = {}
    for entry in entries:
        fl_id = entry.get("fl") or ""
        record = runs.effective_fl_overlay_record(
            entry,
            overlay.get(fl_id) or (entry.get("overlay") if isinstance(entry.get("overlay"), dict) else None),
            fl_id=fl_id,
        )
        es = (record.get("EpochStatus") or "").strip()
        if es:
            status_counts[es] = status_counts.get(es, 0) + 1

    if args.json:
        return _emit_json({"epoch_statuses": status_counts, "total_values": len(status_counts)})

    if not status_counts:
        print("No EpochStatus values found in any overlay entry.")
        return 0

    if not getattr(args, "json", False):
        _start_card("epoch-statuses", mode="read-only")

    rows = [(status, str(count)) for status, count in sorted(status_counts.items(), key=lambda x: -x[1])]
    runs.print_table(["EPOCH STATUS", "COUNT"], rows)
    print(f"\n{len(status_counts)} distinct EpochStatus value(s) across {sum(status_counts.values())} entries.")
    return 0


def cmd_required_fields(args: argparse.Namespace) -> int:
    """Show the native overlay-first required-fields contract for one FL entry."""
    # ANALYZE_FAILURE_LOG_REQUIRED_FIELDS_OWNER:
    # This command is the dedicated FL front door. Native required-fields truth
    # belongs here; the old run/proof analyzer passthrough was removed.
    fl_id = (args.fl_id or "").strip().upper()
    if not re.match(r"^FL-\d+$", fl_id):
        print(f"Invalid FL id: {fl_id!r}")
        return 1

    root = _repo_root()
    entry = runs.fl_entry_by_id(root, fl_id)
    if entry is None:
        payload = {
            "fl_id": fl_id,
            "required_fields": [],
            "gate_names": [],
            "status": "not_found",
            "has_gap": False,
            "source": "missing",
        }
        if args.json:
            _emit_json(payload)
            return 1
        print(f"  FL entry not found: {fl_id}")
        return 1

    advisory = runs.fl_required_fields_advisory_for_id(root, fl_id)
    gate_names = runs.fl_entry_gates(root, entry)

    payload = {
        "fl_id": fl_id,
        "required_fields": advisory.get("required_fields") or [],
        "gate_names": gate_names,
        "status": (entry.get("status") or "").strip(),
        "has_gap": bool(advisory.get("has_gap")),
        "source": str(advisory.get("source") or "none"),
        "lineage_sources": list(advisory.get("lineage_sources") or []),
    }

    if args.json:
        return _emit_json(payload)

    _start_card("required-fields", mode="read-only")
    print(f"  FL: {fl_id}")
    print(f"  Status: {payload['status']}")
    print(f"  Source: {payload['source']}")
    if payload["lineage_sources"]:
        print(f"  Lineage: {', '.join(payload['lineage_sources'])}")
    if not payload["has_gap"] and not payload["required_fields"]:
        print("  No required-fields advisory found.")
        return 0
    print(f"  Required fields ({len(payload['required_fields'])}):")
    for field in payload["required_fields"]:
        print(f"    {field}")
    if gate_names:
        print(f"  Gate names ({len(gate_names)}):")
        for gate in gate_names:
            print(f"    {gate}")
    return 0


def cmd_contract(args: argparse.Namespace) -> int:
    payload = front_door_contract_payload()
    if getattr(args, "json", False):
        return _emit_json(payload)
    _start_card("contract", mode="shared-index")
    print("Native commands:")
    for spec in payload["native_commands"]:
        json_bit = " [json]" if spec.get("supports_json") else ""
        print(f"  {spec['name']:<14} {spec['summary']}{json_bit}")
    print("\nForwarded commands:")
    for spec in payload["forwarded_commands"]:
        json_bit = " [json]" if spec.get("supports_json") else ""
        print(f"  {spec['name']:<22} {spec['summary']}{json_bit}")
    return 0


def _git_output(root: Path, cmd: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *cmd],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def cmd_context(args: argparse.Namespace) -> int:
    _start_card("context", mode="read-only")
    root = _repo_root()
    head = _git_output(root, ["rev-parse", "--short", "HEAD"]) or "unknown"
    branch = _git_output(root, ["branch", "--show-current"]) or "unknown"
    modified = [
        line.strip()
        for line in _git_output(root, ["diff", "--name-only", "HEAD"]).splitlines()
        if line.strip()
    ]
    entries = _load_entries()
    proof_counts = Counter(runs.fl_ledger_state(entry) for entry in entries)
    touched: dict[str, list[dict]] = {}
    for path in modified:
        matches = [
            entry
            for entry in entries
            if _entry_mentions_file(entry, path)
            and runs.fl_ledger_state(entry) in {"raw-open-actionable", "implemented-unproven", "monitoring"}
        ]
        if matches:
            touched[path] = _sort_entries(matches)[: int(getattr(args, "limit", 6) or 6)]

    payload = {
        "head": head,
        "branch": branch,
        "modified_files": modified,
        "proof_state_counts": dict(sorted(proof_counts.items())),
        "file_matches": {
            path: [
                {
                    "fl": entry.get("fl"),
                    "ledger": runs.fl_ledger_state(entry),
                    "title": entry.get("title"),
                }
                for entry in matches
            ]
            for path, matches in touched.items()
        },
    }
    if args.json:
        return _emit_json(payload)

    cli_style.header("FL CONTEXT")
    print(f"  branch: {branch}")
    print(f"  head:   {head}")
    print(f"  dirty files: {len(modified)}")
    for path in modified[: int(getattr(args, "limit", 12) or 12)]:
        print(f"    {path}")
    print("\nProof-state counts:")
    for state, count in sorted(proof_counts.items()):
        print(f"  {state:<24} {count}")
    print("\nOpen/Unproven FLs touching dirty files:")
    if not touched:
        print("  none")
    else:
        for path, matches in touched.items():
            print(f"  {path}")
            for entry in matches:
                print(
                    f"    {entry.get('fl'):<8} {runs.fl_ledger_state(entry):<22} {(entry.get('title') or '')[:70]}"
                )
    return 0


def cmd_strict_ledger(args: argparse.Namespace) -> int:
    payload = _strict_bundle_ledger_payload(_load_entries())
    if args.json:
        _emit_json(payload)
    else:
        cli_style.header("STRICT BUNDLE-REFACTOR LEDGER")
        print(f"  total: {payload['total']}")
        print(f"  ok: {payload['ok']}")
        rows = [
            (name, count, ", ".join(payload["buckets"].get(name, [])[:12]))
            for name, count in payload["counts"].items()
        ]
        runs.print_table(["CLASSIFICATION", "COUNT", "SAMPLE IDS"], rows)
        if payload["blocking"]:
            print(f"\n{cli_style.style('BLOCKING IDS:', 'fail')}")
            print("  " + ", ".join(payload["blocking"]))
    return 0 if payload["ok"] else 1


# Proof-state lifecycle transitions
_LIFECYCLE_TRANSITIONS: dict[str, dict] = {
    "OPEN": {
        "next": "WIRED",
        "evidence": "commit hash showing code change that addresses the issue",
        "command": "analyze_failure_log.py fix-attempt FL-NNN \"description of change\"",
    },
    "WIRED": {
        "next": "IMPLEMENTED",
        "evidence": "gate wiring or test harness change that can observe the fix",
        "command": "append a reviewed FAILURE_LOG.md fix-attempt note for FL-NNN",
    },
    "IMPLEMENTED": {
        "next": "PROVEN",
        "evidence": "runtime evidence with exact run ID",
        "command": "inspect the runtime artifact directly; no deleted proof analyzer is authoritative",
    },
    "PARTIAL": {
        "next": "IMPLEMENTED",
        "evidence": "additional fix-attempt addressing remaining gaps",
        "command": "analyze_failure_log.py fix-attempt FL-NNN \"additional fix\"",
    },
    "PROVEN": {
        "next": None,
        "evidence": "terminal state — no further transitions",
        "command": None,
    },
    "BLOCKED": {
        "next": "OPEN",
        "evidence": "blocker cleared, re-open for new attempt",
        "command": "update overlay ProofState to OPEN after blocker clears",
    },
    "DEFERRED": {
        "next": "OPEN",
        "evidence": "decision to resume work on this entry",
        "command": "update overlay ProofState to OPEN",
    },
}


def cmd_lifecycle(args: argparse.Namespace) -> int:
    _start_card("lifecycle", fl_id=args.fl_id, mode="read-only")
    root = _repo_root()
    fl_id = args.fl_id.upper()
    if not fl_id.startswith("FL-") and fl_id.isdigit():
        fl_id = f"FL-{int(fl_id):03d}"
    if not re.match(r"^FL-\d+$", fl_id):
        print(f"error: invalid FL ID format: {args.fl_id!r}  (expected FL-NNN)", file=sys.stderr)
        return 2

    entries = _load_entries()
    entry = None
    for e in entries:
        if (e.get("fl") or "").upper() == fl_id:
            entry = e
            break
    if not entry:
        print(f"error: {fl_id} not found", file=sys.stderr)
        return 1

    overlay = runs.load_fl_overlay(root)
    record = runs.effective_fl_overlay_record(entry, overlay.get(fl_id), fl_id=fl_id)
    proof_state = record.get("ProofState", "OPEN").upper()
    status_display = runs.fl_status_display(entry) if hasattr(runs, "fl_status_display") else "-"
    gates = runs.fl_entry_gates(root, entry) if hasattr(runs, "fl_entry_gates") else []
    ledger_state = runs.fl_ledger_state(entry) if hasattr(runs, "fl_ledger_state") else "-"

    transition = _LIFECYCLE_TRANSITIONS.get(proof_state, _LIFECYCLE_TRANSITIONS.get("OPEN"))

    result = {
        "fl": fl_id,
        "title": (entry.get("title") or "-")[:80],
        "current_state": proof_state,
        "ledger_state": ledger_state,
        "status": status_display,
        "gates": gates,
        "next_state": transition["next"],
        "required_evidence": transition["evidence"],
        "suggested_command": transition["command"],
    }

    if getattr(args, "json", False):
        return _emit_json(result)

    cli_style.header(f"LIFECYCLE: {fl_id}")
    print(f"  title:  {result['title']}")
    print(f"  status: {result['status']}")
    print(f"  proof:  {cli_style.status(proof_state)}")
    print(f"  ledger: {ledger_state}")
    if gates:
        print(f"  gates:  {', '.join(gates[:6])}")
    print()

    if transition["next"]:
        print(cli_style.style(f"NEXT TRANSITION: {proof_state} -> {transition['next']}", "subheader"))
        print(f"  evidence needed: {transition['evidence']}")
        if transition["command"]:
            print(f"  suggested:       {cli_style.style(transition['command'], 'dim')}")
    else:
        print(cli_style.style("Terminal state — no further transitions available.", "ok"))

    return 0


# ---------------------------------------------------------------------------
# Signoff storage (RQ-038)
# ---------------------------------------------------------------------------

_SIGNOFFS_PATH = SCRIPT_DIR.parent / "docs" / "signoffs.json"


def _load_signoffs() -> dict[str, list[dict]]:
    """Load the signoff ledger from docs/signoffs.json."""
    if not _SIGNOFFS_PATH.exists():
        return {}
    try:
        data = json.loads(_SIGNOFFS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _save_signoffs(data: dict[str, list[dict]]) -> None:
    """Write the signoff ledger back to docs/signoffs.json."""
    _SIGNOFFS_PATH.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _normalize_fl_id(raw: str) -> str:
    """Normalize a user-supplied FL id to FL-NNN form."""
    raw = raw.strip().upper()
    if not raw.startswith("FL-") and raw.isdigit():
        raw = f"FL-{int(raw)}"
    return raw


def _resolve_commit(root: Path, commit: str) -> bool:
    """Return True if git can resolve the commit hash."""
    return bool(_git_output(root, ["rev-parse", "--verify", commit]))


def cmd_signoff_record(args: argparse.Namespace) -> int:
    """Record a human signoff for an FL entry."""
    fl_id = _normalize_fl_id(args.fl_id)
    if not re.match(r"^FL-\d+$", fl_id):
        print(f"error: invalid FL id: {args.fl_id!r}", file=sys.stderr)
        return 2

    verdict = args.verdict.lower()
    if verdict not in ("pass", "fail"):
        print(f"error: verdict must be 'pass' or 'fail', got {args.verdict!r}", file=sys.stderr)
        return 2

    if not args.operator:
        print("error: --operator is required", file=sys.stderr)
        return 2
    if not args.date:
        print("error: --date is required", file=sys.stderr)
        return 2
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
        print(f"error: --date must be YYYY-MM-DD, got {args.date!r}", file=sys.stderr)
        return 2

    record = {
        "operator": args.operator,
        "date": args.date,
        "branch": args.branch or "",
        "commit": args.commit or "",
        "environment": args.environment or "",
        "exercised_path": args.path or "",
        "verdict": verdict,
        "usability_note": args.note or "",
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    signoffs = _load_signoffs()
    signoffs.setdefault(fl_id, []).append(record)
    _save_signoffs(signoffs)

    payload = {"fl": fl_id, "action": "recorded", "record": record}
    if getattr(args, "json", False):
        return _emit_json(payload)

    _start_card("signoff record", fl=fl_id)
    print(f"  Signoff recorded for {fl_id}")
    for k, v in record.items():
        print(f"    {k}: {v}")
    return 0


def cmd_signoff_query(args: argparse.Namespace) -> int:
    """Show all signoffs for an FL entry."""
    fl_id = _normalize_fl_id(args.fl_id)
    if not re.match(r"^FL-\d+$", fl_id):
        print(f"error: invalid FL id: {args.fl_id!r}", file=sys.stderr)
        return 2

    signoffs = _load_signoffs()
    records = signoffs.get(fl_id, [])

    payload = {"fl": fl_id, "signoff_count": len(records), "signoffs": records}
    if getattr(args, "json", False):
        return _emit_json(payload)

    _start_card("signoff query", fl=fl_id)
    if not records:
        print(f"  No signoffs recorded for {fl_id}")
        return 0

    print(f"  {len(records)} signoff(s) for {fl_id}:\n")
    for i, rec in enumerate(records, 1):
        print(f"  --- signoff {i} ---")
        for k, v in rec.items():
            print(f"    {k}: {v}")
        print()
    return 0


def cmd_signoff_verify(args: argparse.Namespace) -> int:
    """Verify that an FL entry has a valid signoff meeting §4 requirements.

    §4 requires: operator, date, commit resolvable, verdict is pass/fail.
    Full PROVEN requires: branch+commit, exercised path, environment,
    and one usability note — flagged as warnings when missing.
    """
    fl_id = _normalize_fl_id(args.fl_id)
    if not re.match(r"^FL-\d+$", fl_id):
        print(f"error: invalid FL id: {args.fl_id!r}", file=sys.stderr)
        return 2

    root = _repo_root()
    signoffs = _load_signoffs()
    records = signoffs.get(fl_id, [])

    if not records:
        payload = {
            "fl": fl_id,
            "valid": False,
            "reason": "no_signoffs",
            "signoff_count": 0,
            "passing_count": 0,
            "errors": ["No signoffs recorded for this FL entry"],
            "warnings": [],
        }
        if getattr(args, "json", False):
            return _emit_json(payload)
        _start_card("signoff verify", fl=fl_id)
        print(f"  {cli_style.style('FAIL', 'fail')}: No signoffs recorded for {fl_id}")
        return 1

    # Evaluate each record; at least one must pass all hard checks
    passing_records: list[dict] = []
    all_errors: list[str] = []
    all_warnings: list[str] = []

    for i, rec in enumerate(records, 1):
        errors: list[str] = []
        warnings: list[str] = []

        # Hard requirements (§4 minimum)
        if not rec.get("operator"):
            errors.append(f"signoff {i}: operator missing")
        if not rec.get("date"):
            errors.append(f"signoff {i}: date missing")
        verdict = (rec.get("verdict") or "").lower()
        if verdict not in ("pass", "fail"):
            errors.append(f"signoff {i}: verdict must be pass/fail, got {verdict!r}")
        commit = rec.get("commit") or ""
        if not commit:
            errors.append(f"signoff {i}: commit missing")
        elif not _resolve_commit(root, commit):
            errors.append(f"signoff {i}: commit {commit!r} not resolvable in this repo")

        # Soft requirements (PROVEN completeness)
        if not rec.get("branch"):
            warnings.append(f"signoff {i}: branch not specified")
        if not rec.get("environment"):
            warnings.append(f"signoff {i}: environment not specified")
        if not rec.get("exercised_path"):
            warnings.append(f"signoff {i}: exercised_path not specified")
        if not rec.get("usability_note"):
            warnings.append(f"signoff {i}: usability_note not specified")

        all_errors.extend(errors)
        all_warnings.extend(warnings)

        if not errors and verdict == "pass":
            passing_records.append(rec)

    valid = len(passing_records) > 0
    payload = {
        "fl": fl_id,
        "valid": valid,
        "reason": "valid_signoff_found" if valid else "no_passing_signoff",
        "signoff_count": len(records),
        "passing_count": len(passing_records),
        "errors": all_errors,
        "warnings": all_warnings,
    }

    if getattr(args, "json", False):
        return _emit_json(payload)

    _start_card("signoff verify", fl=fl_id)
    if valid:
        print(f"  {cli_style.style('PASS', 'ok')}: {fl_id} has {len(passing_records)} valid passing signoff(s)")
    else:
        print(f"  {cli_style.style('FAIL', 'fail')}: {fl_id} has no valid passing signoff")

    if all_errors:
        print(f"\n  Errors:")
        for err in all_errors:
            print(f"    {err}")
    if all_warnings:
        print(f"\n  Warnings:")
        for warn in all_warnings:
            print(f"    {warn}")
    return 0 if valid else 1


def cmd_lineage_suggest(args: argparse.Namespace) -> int:
    """Suggest lineage links for an FL entry — outputs JSONL for overlay --patch-file.

    Extracts key terms from the target entry's title, category, and description,
    runs history + search queries, scores candidates by relevance, and outputs
    a JSONL patch row merging suggested links into ComplaintRefs.
    """
    import json as _json

    fl_id = _normalize_fl_id(args.fl_id)
    if not re.match(r"^FL-\d+$", fl_id):
        print(f"error: invalid FL id: {args.fl_id!r}", file=sys.stderr)
        return 2

    entries = _load_entries()
    entry_map = {e.get("fl"): e for e in entries}
    target = entry_map.get(fl_id)
    if not target:
        print(f"error: {fl_id} not found in failure log", file=sys.stderr)
        return 2

    # --- Extract search terms from the target entry ---
    title = str(target.get("title") or "")
    category = str(target.get("category") or "")
    desc = str(target.get("description") or "")

    stop_words = {
        "the", "a", "an", "and", "or", "of", "in", "for", "on", "with", "to",
        "from", "is", "are", "was", "were", "has", "have", "had", "not", "no",
        "this", "that", "it", "its", "by", "at", "be", "as", "if", "but",
        "when", "via", "can", "all", "any", "each", "also", "than", "then",
    }

    def _extract_terms(text: str) -> list[str]:
        words = re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower())
        return [w for w in words if w not in stop_words]

    title_terms = _extract_terms(title)
    category_parts = re.findall(r"[A-Za-z][A-Za-z0-9_/-]{2,}", category)
    category_terms = _extract_terms(" ".join(category_parts))

    # Pick top 6 terms: title terms first, then category, then description words
    desc_terms = _extract_terms(desc)[:3]
    raw_terms = title_terms[:4] + category_terms[:2] + desc_terms
    seen: set[str] = set()
    key_terms: list[str] = []
    for t in raw_terms:
        if t not in seen:
            seen.add(t)
            key_terms.append(t)
    key_terms = key_terms[:6]

    if not key_terms:
        print(f"error: could not extract search terms from {fl_id}", file=sys.stderr)
        return 2

    # --- Score all other entries by term overlap ---
    target_num = target.get("num", 0)
    existing_refs = set(_fl_overlay_list_local(target))

    def _fl_text_local(e: dict) -> str:
        parts = [
            str(e.get("title") or ""),
            str(e.get("category") or ""),
            str(e.get("description") or ""),
        ]
        return " ".join(parts).lower()

    candidates: list[tuple[int, str, str]] = []  # (score, fl_id, title)
    for e in entries:
        eid = e.get("fl")
        if not eid or eid == fl_id:
            continue
        text = _fl_text_local(e)
        score = sum(1 for t in key_terms if t in text)
        if score < args.min_score:
            continue
        # Slight preference for entries near the target in number (± 200)
        num = e.get("num", 0)
        if abs(num - target_num) <= 200:
            score += 1
        candidates.append((score, eid, str(e.get("title") or "")))

    candidates.sort(key=lambda x: -x[0])
    top = candidates[: args.limit]

    if not top:
        if args.json:
            return _emit_json({"fl": fl_id, "terms": key_terms, "candidates": []})
        print(f"No candidates found for {fl_id} with terms: {key_terms}")
        return 0

    # Merge with existing ComplaintRefs
    new_refs = [eid for _, eid, _ in top]
    merged = list(existing_refs | set(new_refs))
    merged.sort(key=lambda x: int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else 0)

    if args.json:
        return _emit_json({
            "fl": fl_id,
            "terms": key_terms,
            "candidates": [{"fl": eid, "score": sc, "title": t} for sc, eid, t in top],
            "patch_row": {"fl": fl_id, "ComplaintRefs": merged},
        })

    print(f"LINEAGE SUGGEST for {fl_id}: {title}")
    print(f"  Search terms: {', '.join(key_terms)}")
    print(f"  Existing ComplaintRefs: {', '.join(sorted(existing_refs)) or '(none)'}")
    print()
    print(f"TOP {len(top)} CANDIDATES (score = term overlap):")
    for sc, eid, t in top:
        marker = " [already linked]" if eid in existing_refs else ""
        print(f"  {eid:<8}  score={sc}  {t[:60]}{marker}")
    print()
    print("UNION-PRESERVING LINEAGE ROW (review manually; overlay --patch is disabled):")
    print(_json.dumps({"fl": fl_id, "ComplaintRefs": merged}))
    print()
    print("To apply: use reviewed union-preserving tooling only. Do not use overlay --patch.")
    return 0


def _fl_overlay_list_local(entry: dict) -> list[str]:
    """Extract ComplaintRefs from the overlay record of an entry."""
    overlay = entry.get("overlay") or {}
    refs = overlay.get("ComplaintRefs") or []
    if isinstance(refs, list):
        return [str(r).strip() for r in refs if str(r).strip()]
    if isinstance(refs, str):
        return [r.strip() for r in re.split(r"[,\s]+", refs) if r.strip()]
    return []


def _forward(argv: list[str]) -> int:
    print(
        "error: the old FL passthrough was removed with the deleted proof analyzer corpus; "
        f"use a native analyze_failure_log.py command instead: {' '.join(argv)}",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    compat_cmds = ", ".join(sorted(_COMPAT_FL_COMMANDS))
    parser = argparse.ArgumentParser(
        description=(
            "Failure-log front door.\n\n"
            "NATIVE commands (handled here):\n"
            "  path, preflight/prefix, by-file, by-symbol, categories, tags,\n"
            "  overlay, context, lifecycle\n\n"
            "DELETED commands (old proof-analyzer passthrough; no longer routed):\n"
            f"  {compat_cmds}\n\n"
            "Deleted commands fail closed instead of invoking the removed proof analyzer.\n"
            "Use `contract` for the shared machine-readable command index."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dump = sub.add_parser("dump", help="Print the full raw markdown body of an FL entry (all append blocks)")
    p_dump.add_argument("fl_id", help="FL id, e.g. FL-4231")

    p_path = sub.add_parser("path", help="Show resolved canonical/legacy FAILURE_LOG paths")
    p_path.add_argument("--json", action="store_true", help="Emit JSON")

    for name in ("preflight", "prefix"):
        p_preflight = sub.add_parser(
            name,
            help="Pre-fix FL decision report from terms/files/symbols, with family expansion and source-drift hints",
        )
        p_preflight.add_argument("terms", nargs="*", help="Search terms describing the intended fix or failure")
        p_preflight.add_argument("--term", action="append", default=[], help="Additional search term")
        p_preflight.add_argument("--file", dest="files", action="append", default=[], help="Source/doc file involved")
        p_preflight.add_argument("--symbol", dest="symbols", action="append", default=[], help="Function, enum, gate, or field name involved")
        p_preflight.add_argument("--limit", type=int, default=12, help="Maximum rows per section")
        p_preflight.add_argument("--no-family", action="store_true", help="Do not expand related FL family")
        p_preflight.add_argument("--json", action="store_true", help="Emit JSON")

    p_by_file = sub.add_parser("by-file", help="List FL entries that mention or overlay-reference files")
    p_by_file.add_argument("files", nargs="+", help="Repo-relative file path(s)")
    p_by_file.add_argument("--family", action="store_true", help="Expand matching entries to their FL family")
    p_by_file.add_argument("--status", metavar="S", help="Filter by status substring")
    p_by_file.add_argument("--proof-state", dest="proof_state", metavar="P", help="Filter by overlay ProofState")
    p_by_file.add_argument("--implemented-unproven", action="store_true", help="Only implemented-but-unproven entries")
    p_by_file.add_argument("--date-from", metavar="YYYY-MM-DD", dest="date_from", help="Only entries dated on or after this date")
    p_by_file.add_argument("--date-to", metavar="YYYY-MM-DD", dest="date_to", help="Only entries dated on or before this date")
    p_by_file.add_argument("--limit", type=int, default=30, help="Maximum rows to print")
    p_by_file.add_argument("--json", action="store_true", help="Emit JSON")

    p_by_symbol = sub.add_parser("by-symbol", help="List FL entries that mention symbols/gates/fields")
    p_by_symbol.add_argument("symbols", nargs="+", help="Symbol, function, gate, or field name(s)")
    p_by_symbol.add_argument("--family", action="store_true", help="Expand matching entries to their FL family")
    p_by_symbol.add_argument("--status", metavar="S", help="Filter by status substring")
    p_by_symbol.add_argument("--proof-state", dest="proof_state", metavar="P", help="Filter by overlay ProofState")
    p_by_symbol.add_argument("--implemented-unproven", action="store_true", help="Only implemented-but-unproven entries")
    p_by_symbol.add_argument("--date-from", metavar="YYYY-MM-DD", dest="date_from", help="Only entries dated on or after this date")
    p_by_symbol.add_argument("--date-to", metavar="YYYY-MM-DD", dest="date_to", help="Only entries dated on or before this date")
    p_by_symbol.add_argument("--limit", type=int, default=30, help="Maximum rows to print")
    p_by_symbol.add_argument("--json", action="store_true", help="Emit JSON")

    for name, help_text in (
        ("categories", "Histogram of raw Category values"),
        ("tags", "Histogram of normalized tags derived from Category values"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--status", metavar="S", help="Filter by status substring")
        p.add_argument("--category", metavar="C", help="Filter by category substring")
        p.add_argument("--tag", metavar="T", help="Filter by normalized tag")
        p.add_argument("--limit", type=int, default=40, metavar="N", help="Maximum rows to print")
        p.add_argument("--json", action="store_true", help="Emit JSON")
        p.add_argument("--area", metavar="A", help="Filter by overlay Area")
        p.add_argument("--subsystem", metavar="S", help="Filter by overlay Subsystem")
        p.add_argument("--kind", metavar="K", help="Filter by overlay Kind")
        p.add_argument("--proof-state", dest="proof_state", metavar="P", help="Filter by overlay ProofState")
        p.add_argument("--complaint-ref", metavar="R", help="Filter by overlay ComplaintRefs substring")
        p.add_argument(
            "--complaint-counter-state",
            dest="complaint_counter_state",
            metavar="C",
            help="Filter by overlay ComplaintCounterState substring",
        )
        p.add_argument("--code-ref", metavar="X", help="Filter by overlay CodeRefs substring")
        p.add_argument("--touched-file", metavar="F", help="Filter by overlay TouchedFiles substring")
        p.add_argument("--epoch-status", dest="epoch_status", metavar="E", help="Filter by overlay EpochStatus substring (e.g. LIKELY_SUPERSEDED, OVERSIGHT_WARNING, RELAY_ERA_RETIRED, FIXED_UNATTRIBUTED)")

    p_overlay = sub.add_parser("overlay", help="Show FL metadata overlay records")
    p_overlay.add_argument("--fl", metavar="ID", help="Show overlay record for a specific FL id")
    p_overlay.add_argument("--status", metavar="S", help="Filter by entry status substring")
    p_overlay.add_argument("--category", metavar="C", help="Filter by entry category substring")
    p_overlay.add_argument("--tag", metavar="T", help="Filter by normalized tag")
    p_overlay.add_argument("--area", metavar="A", help="Filter by overlay Area")
    p_overlay.add_argument("--subsystem", metavar="S", help="Filter by overlay Subsystem")
    p_overlay.add_argument("--kind", metavar="K", help="Filter by overlay Kind")
    p_overlay.add_argument("--proof-state", dest="proof_state", metavar="P", help="Filter by overlay ProofState")
    p_overlay.add_argument("--complaint-ref", metavar="R", help="Filter by overlay ComplaintRefs substring")
    p_overlay.add_argument(
        "--complaint-counter-state",
        dest="complaint_counter_state",
        metavar="C",
        help="Filter by overlay ComplaintCounterState substring",
    )
    p_overlay.add_argument("--code-ref", metavar="X", help="Filter by overlay CodeRefs substring")
    p_overlay.add_argument("--touched-file", metavar="F", help="Filter by overlay TouchedFiles substring")
    p_overlay.add_argument("--epoch-status", dest="epoch_status", metavar="E", help="Filter by overlay EpochStatus substring (e.g. LIKELY_SUPERSEDED, OVERSIGHT_WARNING, RELAY_ERA_RETIRED, FIXED_UNATTRIBUTED)")
    p_overlay.add_argument(
        "--implemented-unproven",
        action="store_true",
        help="Only entries with fix attempts or IMPLEMENTED/PARTIAL overlays still needing proof",
    )
    p_overlay.add_argument("--limit", type=int, default=None, metavar="N", help="Maximum rows to print")
    p_overlay.add_argument(
        "--missing",
        action="store_true",
        help="List entries that have no raw overlay row and rely on synthesized defaults",
    )
    p_overlay.add_argument(
        "--loss-audit",
        action="store_true",
        help="Audit current effective overlay rows that lost earlier non-empty list metadata",
    )
    p_overlay.add_argument(
        "--blame",
        action="store_true",
        help="With --loss-audit, include git blame commit summaries for destructive overlay rows",
    )
    p_overlay.add_argument("--json", action="store_true", help="Emit JSON")
    p_overlay.add_argument(
        "--fill-defaults",
        action="store_true",
        help="Generate missing overlay rows with safe defaults (dry-run by default)",
    )
    p_overlay.add_argument(
        "--import-proposals",
        metavar="PATH",
        help="Import proposal JSONL and append only the metadata keys still missing from each raw overlay row",
    )
    p_overlay.add_argument("--patch", action="store_true", help=argparse.SUPPRESS)
    p_overlay.add_argument("--patch-file", metavar="PATH", help=argparse.SUPPRESS)
    p_overlay.add_argument("--patch-stdin", action="store_true", help=argparse.SUPPRESS)
    p_overlay.add_argument("--patch-jsonl", action="append", default=[], help=argparse.SUPPRESS)
    p_overlay.add_argument("--batch-confirm", action="store_true", help=argparse.SUPPRESS)
    for legacy_patch_arg in (
        "--set-proof-state",
        "--set-area",
        "--set-priority",
        "--set-category",
        "--set-epoch-status",
        "--set-complaint-counter-state",
        "--set-kinds",
        "--set-subsystems",
        "--set-code-refs",
        "--set-touched-files",
        "--set-anchor-functions",
    ):
        p_overlay.add_argument(legacy_patch_arg, help=argparse.SUPPRESS)
    p_overlay.add_argument(
        "--write",
        action="store_true",
        help="Write generated overlay rows to FAILURE_LOG.md (requires --fill-defaults or --import-proposals)",
    )
    p_epochs = sub.add_parser("epochs", help="Show epoch boundary timeline (FL entries tagged epoch_boundary)")
    p_epochs.add_argument("--json", action="store_true", help="Emit JSON")

    p_epoch_statuses = sub.add_parser("epoch-statuses", help="List all distinct EpochStatus values used in the overlay")
    p_epoch_statuses.add_argument("--json", action="store_true", help="Emit JSON")

    p_context = sub.add_parser("context", help="Session-start FL context for dirty files and proof-state counts")
    p_context.add_argument("--limit", type=int, default=12, metavar="N", help="Maximum dirty files/FL matches to print")
    p_context.add_argument("--json", action="store_true", help="Emit JSON")

    p_session_start = sub.add_parser("session-start", help="Alias for context")
    p_session_start.add_argument("--limit", type=int, default=12, metavar="N", help="Maximum dirty files/FL matches to print")
    p_session_start.add_argument("--json", action="store_true", help="Emit JSON")

    p_strict_ledger = sub.add_parser(
        "strict-ledger",
        help="Classify the exact 74-row bundle-refactor ledger and fail while raw-open rows remain",
    )
    p_strict_ledger.add_argument("--json", action="store_true", help="Emit JSON")

    p_lifecycle = sub.add_parser(
        "lifecycle",
        help="Show proof-state lifecycle and suggest next transition for an FL entry",
    )
    p_lifecycle.add_argument("fl_id", help="FL entry ID (e.g. FL-542)")
    p_lifecycle.add_argument("--json", action="store_true", help="Emit JSON")

    p_contract = sub.add_parser("contract", help="Show the shared FL command contract index")
    p_contract.add_argument("--json", action="store_true", help="Emit JSON")

    p_required_fields = sub.add_parser(
        "required-fields",
        help="Show native overlay-first required recorder/debug fields for one FL entry (FL-1601)",
    )
    p_required_fields.add_argument("fl_id", help="FL entry ID (e.g. FL-1559)")
    p_required_fields.add_argument("--json", action="store_true", help="Emit JSON")

    # --- signoff sub-commands (RQ-038) ---
    p_signoff = sub.add_parser("signoff", help="Human signoff storage for FL entries (RQ-038)")
    signoff_sub = p_signoff.add_subparsers(dest="signoff_cmd", required=True)

    p_so_record = signoff_sub.add_parser("record", help="Record a human signoff for an FL entry")
    p_so_record.add_argument("fl_id", help="FL entry ID (e.g. FL-542)")
    p_so_record.add_argument("--operator", required=True, help="Human operator name")
    p_so_record.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="Signoff date")
    p_so_record.add_argument("--branch", default="", help="Git branch at signoff time")
    p_so_record.add_argument("--commit", default="", help="Git commit hash at signoff time")
    p_so_record.add_argument("--environment", default="", help="Environment/topology exercised (e.g. candidate-VPS)")
    p_so_record.add_argument("--path", default="", help="Exercised path description")
    p_so_record.add_argument("--verdict", required=True, choices=["pass", "fail"], help="Signoff verdict")
    p_so_record.add_argument("--note", default="", help="One short usability note")
    p_so_record.add_argument("--json", action="store_true", help="Emit JSON")

    p_so_query = signoff_sub.add_parser("query", help="Show all signoffs for an FL entry")
    p_so_query.add_argument("fl_id", help="FL entry ID (e.g. FL-542)")
    p_so_query.add_argument("--json", action="store_true", help="Emit JSON")

    p_so_verify = signoff_sub.add_parser("verify", help="Check if an FL entry has a valid signoff meeting §4 requirements")
    p_so_verify.add_argument("fl_id", help="FL entry ID (e.g. FL-542)")
    p_so_verify.add_argument("--json", action="store_true", help="Emit JSON")

    # --- lineage-suggest ---
    p_ls = sub.add_parser(
        "lineage-suggest",
        help=(
            "Suggest ancestor/descendant FL entries for lineage backfill. "
            "Extracts key terms, scores candidates, outputs JSONL for overlay --patch-file."
        ),
    )
    p_ls.add_argument("fl_id", help="FL entry ID to find lineage for (e.g. FL-2586)")
    p_ls.add_argument(
        "--limit",
        type=int,
        default=15,
        metavar="N",
        help="Maximum candidate suggestions to return (default 15)",
    )
    p_ls.add_argument(
        "--min-score",
        type=int,
        default=2,
        dest="min_score",
        metavar="N",
        help="Minimum term-overlap score to include a candidate (default 2)",
    )
    p_ls.add_argument("--json", action="store_true", help="Emit JSON with patch_row included")

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (["-h"], ["--help"]) or not argv:
        build_parser().print_help(sys.stderr)
        return 0 if argv else 2

    local_cmds = set(FRONT_DOOR_NATIVE_COMMAND_NAMES) | {"dump"}
    if argv[0] not in local_cmds:
        if argv[0] in _COMPAT_FL_COMMANDS or argv[0].startswith("-"):
            return _forward(argv)
        if argv[0] in ("fix-attempt", "fix_attempt"):
            # fix-attempt is NOT an analyze_failure_log.py command. It used to fall
            # through to the read-only `preflight` report below and append nothing,
            # which silently lost fix-attempt records. Error out and redirect to
            # the single ledger-append owner (analyze_runs.py) instead.
            print(
                "error: 'fix-attempt' is not an analyze_failure_log.py command and appends nothing.\n"
                "The ledger-append front door is:\n"
                '    python3 scripts/analyze_runs.py fl fix-attempt FL-NNN "<what changed>"\n'
                "    (add --stage to also git-add the ledger)\n"
                "Previously this silently ran the read-only 'preflight' report and wrote nothing.",
                file=sys.stderr,
            )
            return 2
        argv = ["preflight", *argv]

    args = build_parser().parse_args(argv)
    global _JSON_MODE
    if getattr(args, "json", False):
        _JSON_MODE = True
        cli_style.set_color(False)
    if args.cmd == "path":
        return cmd_path(args)
    if args.cmd == "dump":
        return cmd_dump(args)
    if args.cmd in ("preflight", "prefix"):
        return cmd_preflight(args)
    if args.cmd == "by-file":
        return cmd_by_file(args)
    if args.cmd == "by-symbol":
        return cmd_by_symbol(args)
    if args.cmd == "categories":
        return cmd_categories(args)
    if args.cmd == "tags":
        return cmd_tags(args)
    if args.cmd == "overlay":
        return cmd_overlay(args)
    if args.cmd in ("context", "session-start"):
        return cmd_context(args)
    if args.cmd == "strict-ledger":
        return cmd_strict_ledger(args)
    if args.cmd == "lifecycle":
        return cmd_lifecycle(args)
    if args.cmd == "contract":
        return cmd_contract(args)
    if args.cmd == "required-fields":
        return cmd_required_fields(args)
    if args.cmd == "epochs":
        return cmd_epochs(args)
    if args.cmd == "epoch-statuses":
        return cmd_epoch_statuses(args)
    if args.cmd == "signoff":
        if args.signoff_cmd == "record":
            return cmd_signoff_record(args)
        if args.signoff_cmd == "query":
            return cmd_signoff_query(args)
        if args.signoff_cmd == "verify":
            return cmd_signoff_verify(args)
        return 2
    if args.cmd == "lineage-suggest":
        return cmd_lineage_suggest(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
