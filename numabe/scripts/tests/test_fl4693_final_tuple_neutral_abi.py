"""FL-4693/FL-4512 neutral-ABI decision gate for the final-tuple header.

Decision: engine/render/fl4693_final_tuple_transport.h is a shared neutral
ABI and stays in the shared root. It carries the frozen 48-byte
FinalGlyphResultV2, forward declarations, and function declarations only.
Zero mutable state. The sole mutable writer lives in
apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp.

This gate proves:
- Classic/server views can include the neutral representation without
  linking the current-runtime writer (compile + link + run twice).
- A TU that calls the writer API fails to link without the owner .cpp,
  so the header cannot smuggle a second writer (falsifier).
- Only the current-runtime .cpp defines writer state/symbols.
- The terminal target consumes the owner intentionally through its
  narrow-adapter source closure (no second writer).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "engine/render/fl4693_final_tuple_transport.h"
OWNER = ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"
LEGACY_OWNER = ROOT / "engine/render/fl4693_final_tuple_transport.cpp"
TERMINAL_MK = ROOT / "apps/terminal-runtime/terminal_runtime_sources.mk"
TERMINAL_BACKEND = ROOT / "apps/terminal-runtime/platform/backend/host_terminal_ansi.cpp"

NEUTRAL_INCLUDES = ('<stddef.h>', '<stdint.h>', '<string.h>', '../glyph_id.h')
WRITER_SYMBOLS = (
    "Fl4693FinalTupleCommitFrameV1",
    "Fl4693ProfileTupleApplyTemporalV1",
    "Fl4693ProfileTupleFinalizeV1",
    "Fl4693FinalTupleCloseWorldFrameV1",
)
WRITER_STATE = (
    "g_final[",
    "g_profile_tuples",
    "ProfileTupleStoreV1",
    "g_staged[",
    "g_committed[",
)


def _cxx() -> str:
    cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    assert cxx, "no host c++ compiler available"
    return cxx


def test_header_is_neutral_decls_only() -> None:
    text = HEADER.read_text()
    assert "struct FinalGlyphResultV2" in text
    assert "sizeof(FinalGlyphResultV2) == 48" in text
    assert "offsetof(FinalGlyphResultV2, failure) == 29" in text
    for inc in NEUTRAL_INCLUDES:
        assert inc in text, f"neutral include missing: {inc}"
    assert "apps/fl4512-runtime" not in text, "header must not reach into current-only paths"
    assert "ASCIICKER_RENDERER_CLASSIC_CP437" not in text, (
        "neutral header must be includable under Classic without its own guard"
    )
    for fwd in (
        "struct Fl4131HarriGpuBridge;",
        "struct AnsiCell;",
        "struct FinalGlyphResultV1;",
        "struct SceneFactV1;",
    ):
        assert fwd in text, f"missing forward declaration: {fwd}"
    for state in WRITER_STATE:
        assert state not in text, f"mutable writer state leaks into header: {state}"
    assert "bool Fl4693FinalTupleCommitFrameV1(" in text
    decl = text.split("bool Fl4693FinalTupleCommitFrameV1(", 1)[1].split(";", 1)[0]
    assert "{" not in decl, "writer API must be a declaration, not a body"


def test_only_current_runtime_cpp_writes_state() -> None:
    assert OWNER.exists(), "current-runtime writer missing"
    assert not LEGACY_OWNER.exists(), "legacy engine/render writer must stay removed"
    owner = OWNER.read_text()
    for state in WRITER_STATE:
        assert state in owner, f"writer state missing from owner: {state}"
    for sym in WRITER_SYMBOLS:
        assert re.search(r"\b" + re.escape(sym) + r"\s*\(", owner), (
            f"writer symbol missing from owner: {sym}"
        )
    roots = [ROOT / "engine", ROOT / "platform", ROOT / "apps", ROOT / "web", ROOT / "server"]
    hits: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.cpp"):
            if path == OWNER:
                continue
            try:
                body = path.read_text(errors="ignore")
            except OSError:
                continue
            if re.search(r"\bbool\s+Fl4693FinalTupleCommitFrameV1\s*\(", body):
                hits.append(str(path.relative_to(ROOT)))
            for state in ("ProfileTupleStoreV1", "g_profile_tuples"):
                if state in body:
                    hits.append(f"{path.relative_to(ROOT)}:{state}")
    assert not hits, f"second writer definition found: {hits}"


def test_neutral_repr_links_without_writer_plain_and_classic(tmp_path: Path) -> None:
    tu = tmp_path / "neutral_repr_probe.cpp"
    tu.write_text(
        '#include "engine/render/fl4693_final_tuple_transport.h"\n'
        "#include <stdio.h>\n"
        "static_assert(sizeof(FinalGlyphResultV2) == 48, \"abi drift\");\n"
        "static_assert(offsetof(FinalGlyphResultV2, failure) == 29, \"authority byte drift\");\n"
        "int main() {\n"
        "  FinalGlyphResultV2 t = {};\n"
        "  t.failure = 1;\n"
        "  printf(\"neutral-ok %u %u\\n\", (unsigned)sizeof(t), (unsigned)t.failure);\n"
        "  return 0;\n"
        "}\n"
    )
    for tag, extra in (("plain", []), ("classic", ["-DASCIICKER_RENDERER_CLASSIC_CP437=1"])):
        out = tmp_path / f"neutral_repr_{tag}"
        proc = subprocess.run(
            [_cxx(), "-std=c++17", "-O0", f"-I{ROOT}", *extra, str(tu), "-o", str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, (
            f"{tag} neutral TU must link without the writer:\n{proc.stdout}\n{proc.stderr}"
        )
        run = subprocess.run([str(out)], capture_output=True, text=True, check=False)
        assert run.returncode == 0, f"{tag} neutral probe failed:\n{run.stdout}\n{run.stderr}"
        assert "neutral-ok 48 1" in run.stdout


def test_writer_call_fails_to_link_without_owner(tmp_path: Path) -> None:
    tu = tmp_path / "writer_needs_owner.cpp"
    tu.write_text(
        '#include "engine/render/fl4693_final_tuple_transport.h"\n'
        "int main() { return Fl4693FinalTupleCommitFrameV1(0, 0, 0, 0) ? 0 : 1; }\n"
    )
    out = tmp_path / "writer_needs_owner"
    proc = subprocess.run(
        [_cxx(), "-std=c++17", "-O0", f"-I{ROOT}", str(tu), "-o", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0, (
        "writer call linked without the owner: header smuggles a writer body"
    )
    assert "Fl4693FinalTupleCommitFrameV1" in proc.stderr, proc.stderr[:2000]


def test_terminal_adapter_consumes_owner_intentionally() -> None:
    mk = TERMINAL_MK.read_text()
    assert "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp" in mk, (
        "terminal narrow adapter must list the current-runtime owner explicitly"
    )
    backend = TERMINAL_BACKEND.read_text()
    assert "HostTerminalPresentFinalCellsV1" in backend, (
        "terminal must present the final AnsiCell plane through its named helper"
    )
    for sym in ("bool Fl4693FinalTupleCommitFrameV1(", "ProfileTupleStoreV1", "g_final["):
        assert sym not in backend, f"terminal backend must not define writer state: {sym}"
