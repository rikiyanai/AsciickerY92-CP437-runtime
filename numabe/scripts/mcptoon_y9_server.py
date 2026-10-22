#!/usr/bin/env python3
"""Project-scoped MCPToon front door for bounded Y9 failure-log context.

The analyzer remains the authority for FL context.  This server only validates
the two operator inputs, invokes the existing read-only command, and bounds the
returned envelope for MCP clients.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYZER = PROJECT_ROOT / "scripts" / "analyze_runs.py"
ANALYZER_TIMEOUT_SECONDS = 240
MAX_TASK_CHARS = 400
MAX_RESPONSE_CHARS = 15_000
MAX_DIAGNOSTIC_CHARS = 2_000
MAX_RECOMMENDED_COMMANDS = 6
MAX_RECOMMENDED_COMMAND_CHARS = 180
MAX_EXTERNAL_HITS = 5
MAX_NESTED_STRING_CHARS = 1_200
MAX_PROVENANCE_CHARS = 800
MAX_HIT_FIELD_CHARS = 120

_FL_ID_RE = re.compile(r"FL-[0-9]+")

mcp = MCPServer(
    "mcptoon-y9",
    instructions=(
        "Read-only Y9 failure-log context. Native FAILURE_LOG data owns status, "
        "proof, and closure."
    ),
)


def _command(fl_id: str, task: str) -> list[str]:
    """Return the one permitted analyzer invocation."""

    return [
        "python3",
        str(ANALYZER.relative_to(PROJECT_ROOT)),
        "fl",
        "context",
        fl_id,
        "--task",
        task,
        "--json",
    ]


def _bounded_text(value: Any, limit: int = MAX_NESTED_STRING_CHARS) -> str:
    text = str(value or "")
    return text[:limit]


def _bound_value(value: Any, *, string_limit: int = MAX_NESTED_STRING_CHARS) -> Any:
    """Bound incidental analyzer fields without changing their meaning."""

    if isinstance(value, str):
        return value[:string_limit]
    if isinstance(value, list):
        return [_bound_value(item, string_limit=string_limit) for item in value[:MAX_EXTERNAL_HITS]]
    if isinstance(value, dict):
        return {
            str(key): _bound_value(item, string_limit=string_limit)
            for key, item in value.items()
        }
    return value


def _json_size(value: Any, *, pretty: bool = False) -> int:
    if pretty:
        return len(json.dumps(value, ensure_ascii=False, indent=2))
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _compact_card(card: Any) -> dict[str, Any]:
    """Keep the native card, without copying arbitrary future fields."""

    if not isinstance(card, dict):
        return {}
    keys = (
        "fl", "title", "status", "proof_state", "priority", "ledger_state",
        "category", "area", "family_size", "fix_attempt_count", "primary_ref",
        "overlay",
    )
    result = {key: card[key] for key in keys if key in card}
    if isinstance(result.get("overlay"), dict):
        overlay = result["overlay"]
        result["overlay"] = {
            key: [_bounded_text(item, 120) for item in value[:5]]
            if isinstance(value, list) else _bounded_text(value, 240)
            for key, value in overlay.items()
        }
    return _bound_value(result, string_limit=300)


def _compact_required_fields(required: Any) -> dict[str, Any]:
    if not isinstance(required, dict):
        return {"has_gap": False, "fields": [], "source": "none"}
    fields = required.get("fields")
    if not isinstance(fields, list):
        fields = []
    return {
        "has_gap": bool(required.get("has_gap")),
        "fields": [_bounded_text(item, 160) for item in fields[:10]],
        "source": _bounded_text(required.get("source"), 80),
    }


def _compact_prior_attempts(attempts: Any) -> dict[str, Any]:
    """Preserve analyzer-bounded attempt bodies, newest-first."""

    if not isinstance(attempts, dict):
        return {"total": 0, "returned": 0, "attempts": [], "truncated": False}
    source = attempts.get("attempts")
    if not isinstance(source, list):
        source = []
    compact: list[dict[str, Any]] = []
    for item in source[:5]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "date": _bounded_text(item.get("date"), 80),
                "branch": _bounded_text(item.get("branch"), 160),
                "suffix": _bounded_text(item.get("suffix"), 160),
                "body": _bounded_text(item.get("body"), 1_200),
                "body_truncated": bool(item.get("body_truncated")),
            }
        )
    result = {
        "total": min(int(attempts.get("total") or 0), 10_000),
        "returned": len(compact),
        "total_body_chars": min(int(attempts.get("total_body_chars") or 0), 1_000_000),
        "returned_body_chars": sum(len(item["body"]) for item in compact),
        "truncated": bool(attempts.get("truncated")) or len(source) > len(compact),
        "max_attempts": 5,
        "max_body_chars": 1_200,
        "max_total_chars": min(int(attempts.get("max_total_chars") or 4_000), 4_000),
        "attempts": compact,
    }
    return result


_HIT_FIELDS = ("id", "title", "score", "authority", "projection", "adapter")


def _compact_hit_provenance(raw: dict[str, Any]) -> dict[str, Any] | None:
    provenance = raw.get("provenance")
    if not isinstance(provenance, dict):
        return None
    commands = provenance.get("commands")
    locations = provenance.get("locations")
    result: dict[str, Any] = {}
    if isinstance(commands, list):
        result["commands"] = [_bounded_text(item, 40) for item in commands[:2] if isinstance(item, str)]
    if isinstance(locations, list):
        compact_locations = []
        for location in locations[:3]:
            if not isinstance(location, dict):
                continue
            compact_locations.append(
                {
                    key: (_bounded_text(location[key], 50) if isinstance(location[key], str) else location[key])
                    for key in ("path", "line", "kind")
                    if key in location
                }
            )
        result["locations"] = compact_locations
    return result or None


def _compact_provenance(value: Any) -> Any:
    compact = _bound_value(value, string_limit=300)
    if _json_size(compact) <= MAX_PROVENANCE_CHARS:
        return compact
    compact = _bound_value(compact, string_limit=120)
    if _json_size(compact) <= MAX_PROVENANCE_CHARS:
        return compact
    return {"truncated": True}


def _compact_external(payload: dict[str, Any]) -> dict[str, Any]:
    graft = payload.get("graft_query")
    result = graft.get("result") if isinstance(graft, dict) else None
    external = result.get("external") if isinstance(result, dict) else None
    if not isinstance(external, dict):
        return {"status": "unavailable", "hits": [], "provenance": {}}
    raw_hits = external.get("hits")
    if not isinstance(raw_hits, list):
        raw_hits = []
    hits: list[dict[str, Any]] = []
    for raw in raw_hits[:MAX_EXTERNAL_HITS]:
        if not isinstance(raw, dict):
            continue
        attrs = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
        hit: dict[str, Any] = {}
        for key in _HIT_FIELDS:
            value = raw.get(key)
            if value is None and key == "authority":
                value = attrs.get("authority")
            if value is None and key == "projection":
                value = attrs.get("projection_role")
            if value is not None:
                hit[key] = _bounded_text(value, MAX_HIT_FIELD_CHARS) if isinstance(value, str) else value
        hit_provenance = _compact_hit_provenance(raw)
        if hit_provenance:
            hit["provenance"] = hit_provenance
        hits.append(hit)
    provenance = external.get("provenance")
    if not isinstance(provenance, (dict, list, str)):
        provenance = {"authority": _bounded_text(external.get("authority"), 120)}
    return {
        "status": _bounded_text(external.get("status"), 64),
        "hits": hits,
        "provenance": _compact_provenance(provenance),
    }


def _validation_error(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": code,
        "message": message,
        "read_only": True,
        "command": None,
        "exit_code": None,
        "timed_out": False,
        "timeout_seconds": ANALYZER_TIMEOUT_SECONDS,
        "truncation": {
            "truncated": False,
            "source_chars": 0,
            "returned_chars": 0,
            "max_response_chars": MAX_RESPONSE_CHARS,
        },
    }


def _recommended(payload: dict[str, Any]) -> list[str]:
    source = payload.get("recommended_next_commands")
    if not isinstance(source, list):
        source = []
    commands = [
        _bounded_text(item, MAX_RECOMMENDED_COMMAND_CHARS).strip()
        for item in source
        if isinstance(item, str) and item.strip()
    ]
    return commands[:MAX_RECOMMENDED_COMMANDS]


def _enforce_response_cap(response: dict[str, Any]) -> dict[str, Any]:
    """Ensure the pretty MCPToon JSON remains below the transport cap."""

    truncation = response.setdefault("truncation", {})
    for _ in range(4):
        measured = _json_size(response, pretty=True)
        if truncation.get("returned_chars") == measured:
            break
        truncation["returned_chars"] = measured
    if truncation["returned_chars"] > MAX_RESPONSE_CHARS:
        # Keep native attempt bodies intact while reducing optional provenance
        # if a future schema adds fields beyond the curated budget.
        external = response.get("external")
        if isinstance(external, dict):
            external["provenance"] = {"truncated": True}
        for _ in range(4):
            measured = _json_size(response, pretty=True)
            truncation["returned_chars"] = measured
            if measured <= MAX_RESPONSE_CHARS:
                break
        truncation["truncated"] = True
    return response


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def failure_log_context(fl_id: str, task: str) -> dict[str, Any]:
    """Read bounded native FL context.

    ``fl_id`` must be an uppercase identifier such as ``FL-4748`` and ``task``
    must be a non-empty bounded search term.  This tool cannot update the
    failure log or promote external status, proof, or closure.
    """

    if not isinstance(fl_id, str) or not _FL_ID_RE.fullmatch(fl_id):
        return _validation_error(
            "invalid_fl_id",
            "fl_id must match the strict FL-NNN form (for example FL-4748)",
        )
    if not isinstance(task, str):
        return _validation_error("invalid_task", "task must be a non-empty string")
    task = task.strip()
    if not task:
        return _validation_error("invalid_task", "task must be a non-empty string")
    if "\x00" in task:
        return _validation_error("invalid_task", "task must not contain NUL characters")
    if len(task) > MAX_TASK_CHARS:
        return _validation_error(
            "task_too_long",
            f"task must be at most {MAX_TASK_CHARS} characters",
        )

    argv = _command(fl_id, task)
    command_text = shlex.join(argv)
    metadata: dict[str, Any] = {
        "read_only": True,
        "command": command_text,
        "command_argv": argv,
        "exit_code": None,
        "timed_out": False,
        "timeout_seconds": ANALYZER_TIMEOUT_SECONDS,
    }

    try:
        completed = subprocess.run(
            argv,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=ANALYZER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        metadata.update(
            {
                "ok": False,
                "error": "timeout",
                "timed_out": True,
                "stderr": _bounded_text(exc.stderr, MAX_DIAGNOSTIC_CHARS),
                "stdout": _bounded_text(exc.stdout, MAX_DIAGNOSTIC_CHARS),
            }
        )
        metadata["truncation"] = {
            "truncated": bool(
                (exc.stderr and len(str(exc.stderr)) > MAX_DIAGNOSTIC_CHARS)
                or (exc.stdout and len(str(exc.stdout)) > MAX_DIAGNOSTIC_CHARS)
            ),
            "max_response_chars": MAX_RESPONSE_CHARS,
        }
        return metadata
    except OSError as exc:
        metadata.update(
            {
                "ok": False,
                "error": "spawn_failed",
                "stderr": _bounded_text(exc, MAX_DIAGNOSTIC_CHARS),
                "truncation": {"truncated": False, "max_response_chars": MAX_RESPONSE_CHARS},
            }
        )
        return metadata

    metadata["exit_code"] = completed.returncode
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if not isinstance(parsed, dict):
        metadata.update(
            {
                "ok": False,
                "error": "invalid_json" if completed.returncode == 0 else "analyzer_failed",
                "stdout": stdout[:MAX_DIAGNOSTIC_CHARS],
                "stderr": stderr[:MAX_DIAGNOSTIC_CHARS],
                "truncation": {
                    "truncated": len(stdout) > MAX_DIAGNOSTIC_CHARS or len(stderr) > MAX_DIAGNOSTIC_CHARS,
                    "stdout_chars": len(stdout),
                    "stderr_chars": len(stderr),
                    "max_response_chars": MAX_RESPONSE_CHARS,
                },
            }
        )
        return metadata

    native_source = parsed.get("native") if isinstance(parsed.get("native"), dict) else {}
    native = {
        "card": _compact_card(native_source.get("card")),
        "required_fields": _compact_required_fields(native_source.get("required_fields")),
        "prior_attempts": _compact_prior_attempts(native_source.get("prior_attempts")),
        "authority": "native_failure_log",
    }
    native_origin = native_source.get("source")
    if isinstance(native_origin, dict):
        native["source"] = {
            "path": _bounded_text(native_origin.get("path"), 240),
            "commands": [
                _bounded_text(item, MAX_RECOMMENDED_COMMAND_CHARS)
                for item in (native_origin.get("commands") or [])[:4]
                if isinstance(item, str)
            ],
        }
    response = {
        "ok": bool(completed.returncode == 0 and parsed.get("ok", True)),
        "fl": fl_id,
        "task": task,
        "native": native,
        "authority_boundary": {
            key: _bounded_text(parsed.get("authority_boundary", {}).get(key), 120)
            for key in ("status_owner", "proof_owner", "closure_owner")
            if isinstance(parsed.get("authority_boundary"), dict)
        },
        "external": _compact_external(parsed),
        "projection_source": {},
        "read_only": True,
        "command": command_text,
        "command_argv": argv,
        "analyzer_command": parsed.get("command", "fl context"),
        "exit_code": completed.returncode,
        "timed_out": False,
        "timeout_seconds": ANALYZER_TIMEOUT_SECONDS,
        "first_valid_command": command_text,
        "recommended_next_commands": _recommended(parsed),
        "truncation": {
            "truncated": _json_size(parsed) > _json_size(native),
            "source_chars": len(stdout),
            "returned_chars": 0,
            "max_response_chars": MAX_RESPONSE_CHARS,
        },
    }
    projection_source = parsed.get("projection_source")
    if isinstance(projection_source, dict):
        response["projection_source"] = {
            key: _bounded_text(projection_source.get(key), 240)
            for key in ("manifest", "payload", "authority")
        }
        for key in ("fresh", "available"):
            if isinstance(projection_source.get(key), bool):
                response["projection_source"][key] = projection_source[key]
    if parsed.get("error") is not None:
        response["error"] = _bounded_text(parsed.get("error"), 120)
    if parsed.get("detail") is not None:
        response["detail"] = _bounded_text(parsed.get("detail"), MAX_DIAGNOSTIC_CHARS)
    if stderr:
        response["stderr"] = stderr[:MAX_DIAGNOSTIC_CHARS]
        response["truncation"]["stderr_chars"] = len(stderr)
        response["truncation"]["truncated"] |= len(stderr) > MAX_DIAGNOSTIC_CHARS
    return _enforce_response_cap(response)


if __name__ == "__main__":
    mcp.run(transport="stdio")
