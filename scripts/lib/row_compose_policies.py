"""FL-4061 / Q4 ratchet: closed row-canvas compose policy enum.

Policies are compiler-owned generic visual capabilities. They are resolved at
compile/validation time into per-layer canvas-cell masks; runtime only
intersects source semantic masks with those resolved masks.
"""

from __future__ import annotations

from scripts.lib import semantic_roles


COMPOSE_POLICY_DISJOINT_STRICT = "disjoint_strict"
COMPOSE_POLICY_CULL_ROLE_IN_OVERLAP = "cull_role_in_overlap"
COMPOSE_POLICY_EXPLICIT_CANVAS_TABLE = "explicit_canvas_table"

ALLOWED_ROW_COMPOSE_POLICIES = frozenset({
    COMPOSE_POLICY_DISJOINT_STRICT,
    COMPOSE_POLICY_CULL_ROLE_IN_OVERLAP,
    COMPOSE_POLICY_EXPLICIT_CANVAS_TABLE,
})

ROLE_GROUPS = {
    "RIDER_VISIBLE_GROUP": semantic_roles.RIDER_VISIBLE_GROUP,
}


def validate_policy(policy: dict | None, *, mounted: bool, label: str) -> dict:
    if policy is None:
        return {
            "name": COMPOSE_POLICY_DISJOINT_STRICT,
            "params": {},
            "reason": "implicit disjoint_strict for row with no authored overlap policy",
        }
    if not isinstance(policy, dict):
        raise ValueError(f"{label}: compose_policy must be object")
    name = policy.get("name")
    if name not in ALLOWED_ROW_COMPOSE_POLICIES:
        raise ValueError(
            f"{label}: compose_policy.name {name!r} not in "
            f"{sorted(ALLOWED_ROW_COMPOSE_POLICIES)}"
        )
    reason = policy.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"{label}: compose_policy.reason must be non-empty string")
    params = policy.get("params") or {}
    if name == COMPOSE_POLICY_DISJOINT_STRICT:
        if params not in ({}, None):
            raise ValueError(f"{label}: disjoint_strict takes no params")
        return {"name": name, "params": {}, "reason": reason}
    if name == COMPOSE_POLICY_CULL_ROLE_IN_OVERLAP:
        if set(params.keys()) != {"culled_role", "by_role_group"}:
            raise ValueError(
                f"{label}: cull_role_in_overlap requires params "
                "{'culled_role': role, 'by_role_group': group}"
            )
        culled_role = semantic_roles.validate_role(
            params["culled_role"], label=f"{label}: culled_role"
        )
        group_name = params["by_role_group"]
        if group_name not in ROLE_GROUPS:
            raise ValueError(
                f"{label}: by_role_group {group_name!r} not in {sorted(ROLE_GROUPS)}"
            )
        return {
            "name": name,
            "params": {"culled_role": culled_role, "by_role_group": group_name},
            "reason": reason,
        }
    if name == COMPOSE_POLICY_EXPLICIT_CANVAS_TABLE:
        table = params.get("table") if isinstance(params, dict) else None
        if not isinstance(table, dict):
            raise ValueError(f"{label}: explicit_canvas_table requires params.table object")
        if not table:
            raise ValueError(f"{label}: explicit_canvas_table params.table must be non-empty")
        normalised_table: dict[str, dict] = {}
        for raw_key, raw_entry in table.items():
            key = str(raw_key)
            if not key.strip():
                raise ValueError(f"{label}: explicit_canvas_table has blank layer key")
            if not isinstance(raw_entry, dict):
                raise ValueError(
                    f"{label}: explicit_canvas_table[{key!r}] must be object"
                )
            cells = raw_entry.get("cells")
            if not isinstance(cells, list) or not cells:
                raise ValueError(
                    f"{label}: explicit_canvas_table[{key!r}].cells must be non-empty list"
                )
            normalised_cells: list[list[int]] = []
            for idx, cell in enumerate(cells):
                if (
                    not isinstance(cell, list)
                    or len(cell) != 3
                    or not all(isinstance(value, int) for value in cell)
                ):
                    raise ValueError(
                        f"{label}: explicit_canvas_table[{key!r}].cells[{idx}] "
                        "must be [frame, x, y] integers"
                    )
                frame, x, y = cell
                if frame < 0 or x < 0 or x > 255 or y < 0 or y > 255:
                    raise ValueError(
                        f"{label}: explicit_canvas_table[{key!r}].cells[{idx}] "
                        f"out of range: {cell!r}"
                    )
                normalised_cells.append([frame, x, y])
            normalised_table[key] = {"cells": normalised_cells}
        return {"name": name, "params": {"table": normalised_table}, "reason": reason}
    raise AssertionError(name)
