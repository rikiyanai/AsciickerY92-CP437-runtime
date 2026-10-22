#!/usr/bin/env python3
"""Canonical identity reader for the FL-4588 HTML UI registry.

The generator remains the sole stamp writer.  This module is the sole reader
of the content digest algorithm so packaging and proof capture cannot accept a
declared hash that does not describe the registry body they actually loaded.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class RegistryIdentityError(ValueError):
    """The registry body and its stored identity do not form one contract."""


def _contract_rows(registry: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = registry.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RegistryIdentityError(f"registry_{key}_invalid")
    return rows


def canonical_registry_sha256(registry: dict[str, Any]) -> str:
    """Hash canonical registry content with all recursive stamps blanked."""
    if not isinstance(registry, dict):
        raise RegistryIdentityError("registry_document_invalid")
    leaves = _contract_rows(registry, "leaves")
    interactions = _contract_rows(registry, "interactions")
    try:
        unhashed = json.loads(json.dumps(registry))
    except (TypeError, ValueError) as exc:
        raise RegistryIdentityError("registry_document_not_json") from exc
    unhashed["registry_sha256"] = ""
    for row in unhashed["leaves"] + unhashed["interactions"]:
        row["registry_sha256"] = ""
    canonical = json.dumps(
        unhashed, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    # Keep the validated source rows live in this owner; callers cannot pass a
    # non-list body that happens to serialize into the expected shape.
    assert isinstance(leaves, list) and isinstance(interactions, list)
    return hashlib.sha256(canonical).hexdigest()


def validate_registry_identity(registry: dict[str, Any]) -> str:
    """Return the body digest after every document, count, and child join passes."""
    leaves = _contract_rows(registry, "leaves")
    interactions = _contract_rows(registry, "interactions")
    if registry.get("leaf_count") != len(leaves):
        raise RegistryIdentityError("registry_leaf_count_mismatch")
    if registry.get("interaction_count") != len(interactions):
        raise RegistryIdentityError("registry_interaction_count_mismatch")
    expected = canonical_registry_sha256(registry)
    if registry.get("registry_sha256") != expected:
        raise RegistryIdentityError("registry_self_hash_mismatch")
    if any(row.get("registry_sha256") != expected for row in leaves):
        raise RegistryIdentityError("registry_leaf_hash_mismatch")
    if any(row.get("registry_sha256") != expected for row in interactions):
        raise RegistryIdentityError("registry_interaction_hash_mismatch")
    return expected
