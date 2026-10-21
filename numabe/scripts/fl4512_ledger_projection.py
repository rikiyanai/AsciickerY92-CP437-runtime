"""Project one native ledger write into HEAD without taking sibling changes."""
from __future__ import annotations

import json
import re


def section(text: str, fl: str) -> tuple[int, int]:
    if not re.fullmatch(r"FL-\d+", fl):
        raise ValueError("invalid FL identifier")
    matches = list(re.finditer(rf"^### {re.escape(fl)}:", text, re.MULTILINE))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one section for {fl}")
    start = matches[0].start()
    following = re.search(r"^### FL-\d+:", text[matches[0].end():], re.MULTILINE)
    end = matches[0].end() + following.start() if following else len(text)
    return start, end


def append_attempt(head: str, worktree: str, fl: str, key: str) -> str:
    ws, we = section(worktree, fl)
    body = worktree[ws:we]
    if not key or body.count(key) != 1 or key in head:
        raise ValueError("attempt key must identify one uncommitted target section")
    position = body.index(key)
    starts = list(re.finditer(r"^#### Fix attempt[^\n]*", body, re.MULTILINE))
    preceding = [match for match in starts if match.start() <= position]
    if not preceding:
        raise ValueError("key is not in a native fix-attempt block")
    start = preceding[-1].start()
    following = re.search(r"^#### ", body[position + len(key):], re.MULTILINE)
    end = position + len(key) + following.start() if following else len(body)
    block = body[start:end].rstrip()
    if "\n- **Proof state:**" not in block:
        raise ValueError("native fix-attempt proof state missing")
    hs, he = section(head, fl)
    return head[:he].rstrip("\n") + "\n\n" + block + "\n\n" + head[he:]


def append_entry(head: str, worktree: str, fl: str, key: str,
                 head_parts: dict[str, str], worktree_parts: dict[str, str]) -> dict[str, str]:
    if re.search(rf"^### {re.escape(fl)}:", head, re.MULTILINE):
        raise ValueError("new FL already exists in HEAD; use the existing-entry route")
    start, end = section(worktree, fl)
    block = worktree[start:end].rstrip()
    if not key or block.count(key) != 1 or key in head:
        raise ValueError("entry key must identify one uncommitted native entry")
    result = {"docs/FAILURE_LOG.md": head.rstrip("\n") + "\n\n" + block + "\n"}
    selected = []
    for path, text in worktree_parts.items():
        if not re.fullmatch(r"docs/FAILURE_LOG\.overlay/part-\d+\.jsonl", path):
            raise ValueError("unexpected overlay owner path")
        before = head_parts.get(path, "")
        for line in before.splitlines():
            if line.strip() and json.loads(line).get("fl") == fl:
                raise ValueError("new FL conflicts with a HEAD overlay-only identifier")
        rows = [line for line in text.splitlines()
                if line.strip() and json.loads(line).get("fl") == fl]
        if rows:
            selected.extend(json.loads(line) for line in rows)
            result[path] = before.rstrip("\n") + ("\n" if before.strip() else "") + "\n".join(rows) + "\n"
    if not selected:
        raise ValueError("native entry overlay missing; refusing to lose lineage/code references")
    # An old shard absent from the worktree is not a licence to ignore an ID.
    for text in head_parts.values():
        if any(json.loads(line).get("fl") == fl for line in text.splitlines() if line.strip()):
            raise ValueError("new FL conflicts with a HEAD overlay-only identifier")
    return result
