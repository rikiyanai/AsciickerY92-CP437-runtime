"""Shared CLI contract for failure-log tooling.

This is the single index for the failure-log front door:
`scripts/analyze_failure_log.py` native commands only.

The old run/proof analyzer compatibility surface was removed. Do not route new
work through that deleted analyzer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FlCommandContract:
    name: str
    owner: str
    summary: str
    supports_json: bool = False
    json_shape: str = ""


FRONT_DOOR_NATIVE_COMMANDS: tuple[FlCommandContract, ...] = (
    FlCommandContract("path", "analyze_failure_log", "Resolve canonical and legacy FAILURE_LOG paths", True, "{canonical, legacy, exists}"),
    FlCommandContract("preflight", "analyze_failure_log", "Pre-fix FL decision report with family expansion and drift hints", True, "{matches, family, related_terms, files, symbols}"),
    FlCommandContract("prefix", "analyze_failure_log", "Alias for preflight", True, "{matches, family, related_terms, files, symbols}"),
    FlCommandContract("by-file", "analyze_failure_log", "List FL entries mentioning one or more files", True, "{matches, files}"),
    FlCommandContract("by-symbol", "analyze_failure_log", "List FL entries mentioning one or more symbols/fields/gates", True, "{matches, symbols}"),
    FlCommandContract("categories", "analyze_failure_log", "Histogram of raw Category values", True, "{categories:[{category,count}]}"),
    FlCommandContract("tags", "analyze_failure_log", "Histogram of normalized FL tags", True, "{tags:[{tag,count}]}"),
    FlCommandContract("overlay", "analyze_failure_log", "Inspect or generate FL overlay rows", True, "{overlay_count|missing_count|written_count, records|rows}"),
    FlCommandContract("context", "analyze_failure_log", "Session-start FL context for dirty files and proof-state counts", True, "{branch, head, modified_files, proof_counts, related_entries}"),
    FlCommandContract("session-start", "analyze_failure_log", "Alias for context", True, "{branch, head, modified_files, proof_counts, related_entries}"),
    FlCommandContract("strict-ledger", "analyze_failure_log", "Classify the bundle-refactor ledger and fail while raw-open rows remain", True, "{rows, open_rows, verdict}"),
    FlCommandContract("lifecycle", "analyze_failure_log", "Show proof-state lifecycle and next-step guidance for an FL entry", True, "{fl_id, lifecycle, next_transition}"),
    FlCommandContract("contract", "analyze_failure_log", "Print the shared FL command contract index", True, "{schema_version, native_commands, forwarded_commands}"),
    FlCommandContract("required-fields", "analyze_failure_log", "Extract required recorder/evidence fields from an FL entry for smart-derive (FL-1601)", True, "{fl_id, required_fields, gate_names, status}"),
    FlCommandContract("epochs", "analyze_failure_log", "Show epoch boundary timeline (FL entries tagged epoch_boundary)", True, "{epoch_count, epochs:[{fl, epoch_num, epoch_label, date, proof_state, summary}]}"),
    FlCommandContract("epoch-statuses", "analyze_failure_log", "List all distinct EpochStatus values used in the overlay", True, "{epoch_statuses:{status:count}, total_values}"),
    FlCommandContract("signoff", "analyze_failure_log", "Human signoff storage: record / query / verify (RQ-038)", True, "{fl, action|signoff_count|valid, ...}"),
    FlCommandContract("lineage-suggest", "analyze_failure_log", "Suggest ancestor/descendant FL entries for lineage backfill; outputs JSONL for overlay --patch-file", True, "{fl, terms, candidates:[{fl,score,title}], patch_row:{fl,ComplaintRefs}}"),
)


FORWARDED_FL_COMMANDS: tuple[FlCommandContract, ...] = ()


FRONT_DOOR_NATIVE_COMMAND_NAMES = frozenset(spec.name for spec in FRONT_DOOR_NATIVE_COMMANDS)
FORWARDED_FL_COMMAND_NAMES = frozenset(spec.name for spec in FORWARDED_FL_COMMANDS)


def front_door_contract_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "native_commands": [asdict(spec) for spec in FRONT_DOOR_NATIVE_COMMANDS],
        "forwarded_commands": [asdict(spec) for spec in FORWARDED_FL_COMMANDS],
    }
