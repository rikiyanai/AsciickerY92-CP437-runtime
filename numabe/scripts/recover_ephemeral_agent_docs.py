#!/usr/bin/env python3
"""Recover ephemeral agent documents (typically /tmp/claude-*.md) from Claude
Code session transcripts, by replaying every recorded mutation in order.

Why this exists
---------------
Agent scratch documents written to /tmp are evicted by the OS and are then
assumed lost. They are not: every mutation that produced them is recorded in
the session transcripts under ~/.claude/projects. On 2026-09-13 three documents
declared unrecoverable (FL-4361) were restored byte-exact with this method.

Two traps this tool exists to avoid
-----------------------------------
1. TRANSCRIPT DISCOVERY. Subagent transcripts live in
   ``<project>/<session-id>/subagents/agent-*.jsonl``. A ``*/*.jsonl`` glob
   misses them entirely -- and the agent that WROTE the document is usually a
   subagent. Always recurse (``rglob``).

2. MUTATION DISCOVERY. Documents are not only written with the Write tool.
   Agents commonly use ``cat > f <<'EOF'`` heredocs and in-place ``python3``
   string surgery inside Bash commands. A Write-only search finds nothing and
   yields a false "unrecoverable" verdict.

   Critically, embedded python edits must be EXECUTED, not pattern-matched.
   Regex-scraping ``s=s.replace(...)`` calls silently drops anchor-based
   insertions and produces a plausible but WRONG shorter document. This tool
   executes each recorded block with the target path redirected.

Verification
------------
Recorded ``wc -l`` outputs in the transcript act as checkpoints. --verify
compares them against the replay and reports any divergence, so a
reconstruction is never trusted on faith.

Safety
------
Embedded python is executed. Blocks are refused unless they are self-contained
single-file edits (no subprocess/network/filesystem-removal), and the target
path is rewritten into the output directory, so the original location is never
touched. Use --no-exec to skip execution entirely (faster, but may under-recover).

Usage
-----
    python3 scripts/recover_ephemeral_agent_docs.py --list steam-mvp-plan
    python3 scripts/recover_ephemeral_agent_docs.py -o out/ --verify steam-mvp-plan
"""
from __future__ import annotations
import argparse, hashlib, json, pathlib, re, subprocess, sys
from collections import defaultdict

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
DANGER = re.compile(r"\b(subprocess|os\.system|shutil|socket|urllib|requests|rmtree|os\.remove|__import__|eval|exec)\b")
HEREDOC = re.compile(r"cat\s+(>>?)\s+(\S+)\s+<<'(\w+)'\n(.*?)\n\3(?:\n|$)", re.S)
PYBLOCK = re.compile(r"python3\s+-\s+<<'(\w+)'\n(.*?)\n\1", re.S)


def discover(pattern: str):
    """Yield (timestamp, transcript, lineno, tool_name, input_dict)."""
    for f in sorted(PROJECTS.rglob("*.jsonl")):          # rglob: subagents/ included
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pattern not in raw:
            continue
        for i, line in enumerate(raw.splitlines()):
            if pattern not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    yield rec.get("timestamp", ""), f, i, b.get("name"), (b.get("input") or {})


def group(pattern: str):
    """Map each concrete file path -> ordered list of mutation ops."""
    ops, cmds = defaultdict(list), {}
    for ts, f, i, name, inp in discover(pattern):
        if name in ("Write", "Edit") and pattern in str(inp.get("file_path", "")):
            ops[inp["file_path"]].append((ts, f, i, 0, name, inp))
        elif name == "Bash":
            cmd = str(inp.get("command", ""))
            if pattern not in cmd:
                continue
            cmds[(f.name, i)] = cmd
            for m in HEREDOC.finditer(cmd):
                op, tgt, _tag, body = m.group(1), m.group(2), m.group(3), m.group(4)
                if pattern in tgt:
                    ops[tgt].append((ts, f, i, m.start(), "heredoc" + op, body))
            pm = PYBLOCK.search(cmd)
            if pm:
                pv = re.search(r"^\s*p\s*=\s*['\"]([^'\"]+)['\"]", pm.group(2), re.M)
                if pv and pattern in pv.group(1):
                    ops[pv.group(1)].append((ts, f, i, pm.start(), "pyexec", pm.group(2)))
                    cmds[(f.name, i)] = cmd
    for k in ops:
        ops[k].sort(key=lambda o: (o[0], o[2], o[3]))
    return ops, cmds


def result_index(pattern: str):
    """Map (transcript name, lineno of the tool_use) -> recorded result text.

    A Bash mutation typically ends with `wc -l <file>`, so the recorded result
    carries the true line count immediately after that step. Comparing it to
    the replayed line count at the same step is the strongest available proof
    that a reconstruction is faithful.
    """
    idx = {}
    for f in sorted(PROJECTS.rglob("*.jsonl")):
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pattern not in raw:
            continue
        lines = raw.splitlines()
        use = {}
        for i, line in enumerate(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            content = (rec.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    use[b.get("id")] = i
                elif b.get("type") == "tool_result":
                    c = b.get("content")
                    t = ("\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                         if isinstance(c, list) else str(c))
                    if b.get("tool_use_id") in use:
                        idx[(f.name, use[b["tool_use_id"]])] = t
    return idx


def replay(oplist, target: pathlib.Path, allow_exec=True, log=print, steps=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    doc = ""
    for ts, f, i, _seq, kind, payload in oplist:
        if kind == "Write":
            doc = payload["content"]
        elif kind == "Edit":
            doc = doc.replace(payload["old_string"], payload["new_string"],
                              -1 if payload.get("replace_all") else 1)
        elif kind == "heredoc>":
            doc = payload + "\n"
        elif kind == "heredoc>>":
            doc = doc + payload + "\n"
        elif kind == "pyexec":
            if not allow_exec:
                log(f"    SKIP pyexec (--no-exec) {f.name}:{i}")
                continue
            hit = DANGER.search(payload)
            if hit:
                log(f"    REFUSED pyexec ({hit.group(0)}) {f.name}:{i} -- inspect manually")
                continue
            target.write_text(doc, encoding="utf-8")
            src = re.sub(r"(['\"])([^'\"]*" + re.escape(target.name) + r")\1",
                         lambda m: repr(str(target)), payload)
            r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                               text=True, cwd=str(target.parent), timeout=120)
            if r.returncode != 0:
                log(f"    ERROR pyexec {f.name}:{i}: {r.stderr.strip()[:160]}")
            doc = target.read_text(encoding="utf-8")
        if steps is not None:
            steps[(f.name, i)] = doc
        log(f"    {ts[:19]} {kind:10s} -> {doc.count(chr(10))} lines  ({f.name}:{i})")
    target.write_text(doc, encoding="utf-8")
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pattern", help="substring of the lost path, e.g. steam-mvp-plan")
    ap.add_argument("-o", "--outdir", default=".", help="where to write recovered files")
    ap.add_argument("--list", action="store_true", help="list matching paths and op counts only")
    ap.add_argument("--verify", action="store_true", help="compare against recorded wc -l checkpoints")
    ap.add_argument("--no-exec", action="store_true", help="do not execute embedded python edits")
    a = ap.parse_args()

    ops, cmds = group(a.pattern)
    if not ops:
        sys.exit(f"no recorded mutations match {a.pattern!r} "
                 f"(searched {sum(1 for _ in PROJECTS.rglob('*.jsonl'))} transcripts)")
    outdir = pathlib.Path(a.outdir)
    for path, lst in sorted(ops.items()):
        print(f"\n=== {path}  ({len(lst)} recorded mutations)")
        if a.list:
            continue
        dest = outdir / pathlib.Path(path).name
        steps = {}
        doc = replay(lst, dest, allow_exec=not a.no_exec,
                     log=lambda s: print(s), steps=steps)
        nl = doc.count("\n")
        raw = doc.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        print(f"    -> {dest}  {len(raw)} bytes, {nl} lines, sha256 {sha[:16]}")
        if a.verify:
            idx = result_index(pathlib.Path(path).name)
            base = re.escape(pathlib.Path(path).name)
            checked = agreed = 0
            for (fname, i), snap in sorted(steps.items()):
                res = idx.get((fname, i))
                if not res:
                    continue
                nl = snap.count("\n")
                body = snap.splitlines()
                # (a) line-count checkpoints: `wc -l file` or a bare `wc -l < file`
                counts = [int(m.group(1)) for m in
                          re.finditer(r"(?m)^\s*(\d+)\s+\S*" + base, res)]
                cmd = cmds.get((fname, i), "")
                # `echo "... $(wc -l < f) lines"` renders the count next to "lines"
                counts += [int(m.group(1)) for m in re.finditer(r"(\d+)\s+lines\b", res)]
                # a bare integer line is a `wc -l <` result -- but `grep -c` also
                # emits bare integers, so only trust it when grep -c is absent.
                if re.search(r"wc\s+-l\s*<", cmd) and "grep -c" not in cmd:
                    counts += [int(m.group(1)) for m in re.finditer(r"(?m)^\s*(\d+)\s*$", res)]
                for c in counts:
                    checked += 1
                    ok = c == nl
                    agreed += ok
                    print(f"    {fname}:{i} line-count recorded {c} vs replayed {nl}"
                          f" -> {'AGREE' if ok else 'DIVERGE'}")
                # (b) grep -n checkpoints: `N:text` proves content sits at that line
                for m in re.finditer(r"(?m)^(\d+):(.*)$", res):
                    n, text = int(m.group(1)), m.group(2)
                    if n < 1 or n > len(body):
                        continue
                    checked += 1
                    ok = body[n - 1].strip() == text.strip()
                    agreed += ok
                    print(f"    {fname}:{i} line {n} content -> "
                          f"{'AGREE' if ok else 'DIVERGE: ' + body[n-1][:50]!r}")
            if checked == 0:
                print("    no recorded checkpoints found; reconstruction UNVERIFIED")
            else:
                print(f"    VERIFIED {agreed}/{checked} recorded checkpoints agree")


if __name__ == "__main__":
    main()
