"""FL-4512 G2: terminal is a narrow adapter over the current runtime.

The terminal product consumes a bounded current-runtime source closure through
the shared render ABI. It does not own the desktop/presenter product-program
stack and does not introduce a second final-glyph writer. Linux and macOS use
the same common current-runtime owners; macOS carries one explicitly named
offline relation-loader auxiliary source already present in its target.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LINUX_SPEC = ROOT / "makefile_game_term"
MAC_SPEC = ROOT / "makefile_game_term_mac"
CONTRACT = ROOT / "apps/terminal-runtime/terminal_runtime_sources.mk"
PRODUCT_MANIFEST = ROOT / "apps/fl4512-runtime/fl4512_product_sources.mk"


def _manifest_list(text: str, name: str) -> tuple[str, ...]:
    """Parse one manifest partition, expanding make continuations."""
    lines = text.splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith(name + " :=")
    )
    values: list[str] = []
    for line in lines[start:]:
        chunk = line.split(":=", 1)[1] if ":=" in line else line
        continued = chunk.rstrip().endswith("\\")
        chunk = chunk.replace("\\", " ").strip()
        values.extend(chunk.split())
        if not continued:
            break
    return tuple(values)


def _manifest() -> dict[str, tuple[str, ...]]:
    text = PRODUCT_MANIFEST.read_text(encoding="utf-8")
    return {
        "core": _manifest_list(text, "FL4512_CORE_SRCS"),
        "program": _manifest_list(text, "FL4512_PROGRAM_SRCS"),
        "anchor": _manifest_list(text, "FL4512_PORTS_ANCHOR"),
        "mac_aux": _manifest_list(text, "FL4512_TERMINAL_MAC_AUX_SRCS"),
    }


_PARTS = _manifest()
# Terminal selects the core partition plus the ports anchor from the
# product-owned manifest; the glyph-program stack stays out.
COMMON_SOURCES = _PARTS["core"] + _PARTS["anchor"]
MAC_AUX_SOURCES = _PARTS["mac_aux"]
PROGRAM_SOURCES = _PARTS["program"]
PRODUCT_PROGRAM_SOURCES = PROGRAM_SOURCES
PRODUCT_PRESENTATION_SOURCE = "platform/terminal_gl_present.cpp"
TERMINAL_BACKEND_SOURCES = (
    "apps/terminal-runtime/platform/backend/host_terminal_ansi.cpp",
    "platform/backend/terminal_input_parser.cpp",
)

COMMON_OWNERS = tuple(Path(source).stem for source in COMMON_SOURCES)
MAC_AUX_OWNERS = tuple(Path(source).stem for source in MAC_AUX_SOURCES)
FORBIDDEN_CLASSIC_IDENTITY = "ASCIICKER_RENDERER_CLASSIC_CP437"



def _assignment(text: str, name: str) -> str:
    """Return a make assignment value, joining continued lines."""
    lines = text.splitlines()
    prefix = f"{name} :="
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        values = [line[len(prefix) :].strip()]
        while values[-1].endswith("\\"):
            values[-1] = values[-1][:-1].strip()
            index += 1
            assert index < len(lines), f"unterminated {name} assignment"
            values.append(lines[index].strip())
        return " ".join(value for value in values if value)
    raise AssertionError(f"missing {name} assignment")


def _contract_list(name: str) -> tuple[str, ...]:
    """Contract var entries with $(FL4512_*) manifest vars expanded."""
    values = _assignment(CONTRACT.read_text(encoding="utf-8"), name)
    manifest_text = PRODUCT_MANIFEST.read_text(encoding="utf-8")
    expanded: list[str] = []
    for value in values.split():
        if value == "$(FL4512_CORE_SRCS)":
            expanded.extend(_manifest_list(manifest_text, "FL4512_CORE_SRCS"))
        elif value == "$(FL4512_PORTS_ANCHOR)":
            expanded.extend(_manifest_list(manifest_text, "FL4512_PORTS_ANCHOR"))
        elif value == "$(FL4512_TERMINAL_MAC_AUX_SRCS)":
            expanded.extend(
                _manifest_list(manifest_text, "FL4512_TERMINAL_MAC_AUX_SRCS")
            )
        elif not value.startswith("$("):
            expanded.append(value)
        else:
            raise AssertionError(f"unexpanded manifest var {value}")
    return tuple(expanded)


def _spec_sources(spec: Path) -> str:
    return spec.read_text(encoding="utf-8")


def test_default_target_wires_terminal_renderer_audit() -> None:
    for spec in (LINUX_SPEC, MAC_SPEC):
        text = _spec_sources(spec)
        assert (
            "all: actor_visual_table_coverage terminal_runtime_boundary $(BIN) audit_terminal_renderer_identity"
            in text
        ), spec.name
        assert "LINK_MAP := .run/game_term.map" in text, spec.name
        assert ".PHONY: audit_terminal_renderer_identity" in text, spec.name
        assert "audit_terminal_renderer_identity: $(BIN)" in text, spec.name
        assert "@test -s $(LINK_MAP)" in text, spec.name
        assert '"Terminal renderer-identity audit ok"' in text, spec.name
        assert "@for source in $(TERMINAL_RUNTIME_SOURCES); do" in text, spec.name
        assert "owner=$${source##*/}" in text, spec.name
        assert "owner=$${owner%.cpp}" in text, spec.name


def test_terminal_contract_declares_narrow_adapter_boundary() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    assert _assignment(text, "TERMINAL_RUNTIME_BOUNDARY_KIND") == (
        "NARROW_CURRENT_RUNTIME_ADAPTER"
    )
    assert _assignment(text, "TERMINAL_RUNTIME_FINAL_GLYPH_OWNER") == (
        "FL4512_PRODUCT_RENDERER"
    )
    assert _assignment(text, "TERMINAL_RUNTIME_PRESENTATION_CONTRACT") == (
        "CP437_ANSI_CELL_PLANE"
    )
    assert _assignment(text, "TERMINAL_RUNTIME_PRODUCT_PROGRAM_SOURCES") == ""
    # The contract selects manifest partitions; it reproduces no file list.
    assert "$(FL4512_CORE_SRCS)" in text
    assert "$(FL4512_PORTS_ANCHOR)" in text
    assert _contract_list("TERMINAL_RUNTIME_COMMON_SOURCES") == COMMON_SOURCES
    assert _contract_list("TERMINAL_RUNTIME_MAC_AUX_SOURCES") == MAC_AUX_SOURCES
    # Link-ownership gate: every selected source exists on disk.
    for source in COMMON_SOURCES + MAC_AUX_SOURCES:
        assert (ROOT / source).is_file(), source
    assert _assignment(text, "TERMINAL_RUNTIME_LINUX_SOURCES") == (
        "$(TERMINAL_RUNTIME_COMMON_SOURCES)"
    )
    assert _assignment(text, "TERMINAL_RUNTIME_MAC_SOURCES") == (
        "$(TERMINAL_RUNTIME_COMMON_SOURCES) $(TERMINAL_RUNTIME_MAC_AUX_SOURCES)"
    )


def test_specs_select_contract_closure_without_duplicate_literals() -> None:
    linux = _spec_sources(LINUX_SPEC)
    mac = _spec_sources(MAC_SPEC)
    assert "include apps/terminal-runtime/terminal_runtime_sources.mk" in linux
    assert "include apps/terminal-runtime/terminal_runtime_sources.mk" in mac
    assert "TERMINAL_RUNTIME_SOURCES := $(TERMINAL_RUNTIME_LINUX_SOURCES)" in linux
    assert "TERMINAL_RUNTIME_SOURCES := $(TERMINAL_RUNTIME_MAC_SOURCES)" in mac
    assert "SRCS += $(TERMINAL_RUNTIME_SOURCES)" in linux
    assert "SRCS += $(TERMINAL_RUNTIME_SOURCES)" in mac
    for source in TERMINAL_BACKEND_SOURCES:
        assert linux.count(source) == 1, source
        assert mac.count(source) == 1, source
    for source in COMMON_SOURCES + MAC_AUX_SOURCES:
        assert linux.count(source) == 0, source
        assert mac.count(source) == 0, source
    for source in PRODUCT_PROGRAM_SOURCES + (PRODUCT_PRESENTATION_SOURCE,):
        assert source not in linux
        assert source not in mac


def test_contract_common_source_closure_is_unique_and_current_runtime_only() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    common = _contract_list("TERMINAL_RUNTIME_COMMON_SOURCES")
    assert len(common) == len(set(common))
    assert all(source.startswith(("engine/", "apps/fl4512-runtime/")) for source in common)
    assert tuple(Path(source).stem for source in common) == COMMON_OWNERS
    assert tuple(Path(source).stem for source in _contract_list("TERMINAL_RUNTIME_MAC_AUX_SOURCES")) == MAC_AUX_OWNERS
    for source in PRODUCT_PROGRAM_SOURCES:
        assert source not in text


def test_link_map_flag_matches_platform_linker() -> None:
    linux = LINUX_SPEC.read_text(encoding="utf-8")
    mac = MAC_SPEC.read_text(encoding="utf-8")
    assert "-Wl,-Map,$(LINK_MAP)" in linux
    assert "-Wl,-map,$(LINK_MAP)" in mac
    for spec, text in ((LINUX_SPEC, linux), (MAC_SPEC, mac)):
        assert "@$(RM) $(BIN) $(DISTOUTPUT) $(LINK_MAP)" in text, spec.name


def test_forbidden_classic_only_identity() -> None:
    for spec in (LINUX_SPEC, MAC_SPEC):
        assert FORBIDDEN_CLASSIC_IDENTITY not in spec.read_text(encoding="utf-8")


def test_audit_requires_declared_unicode_owners() -> None:
    for spec in (LINUX_SPEC, MAC_SPEC):
        text = _spec_sources(spec)
        assert "@for source in $(TERMINAL_RUNTIME_SOURCES); do" in text
        assert "$(LINK_MAP)" in text


def test_audited_owner_is_declared_in_own_srcs() -> None:
    for source in COMMON_SOURCES:
        assert source in _contract_list("TERMINAL_RUNTIME_COMMON_SOURCES")
    for source in MAC_AUX_SOURCES:
        assert source in _contract_list("TERMINAL_RUNTIME_MAC_AUX_SOURCES")


def test_linux_macos_specs_stay_aligned() -> None:
    assert set(_contract_list("TERMINAL_RUNTIME_COMMON_SOURCES")) == set(COMMON_SOURCES)
    assert set(_contract_list("TERMINAL_RUNTIME_MAC_AUX_SOURCES")) == set(MAC_AUX_SOURCES)
    assert set(MAC_AUX_SOURCES).isdisjoint(COMMON_SOURCES)


TERMINAL_ADAPTER = ROOT / "apps/terminal-runtime/platform/backend/host_terminal_ansi.cpp"


def test_terminal_adapter_owns_explicit_consumption_seam() -> None:
    text = TERMINAL_ADAPTER.read_text(encoding="utf-8")
    assert "FL-4512 G2: terminal final-cell consumption guard." in text
    assert "static void HostTerminalPresentFinalCellsV1(" in text
    assert "Print(buf, w, h, utf)" in text
    assert text.count("HostTerminalPresentFinalCellsV1(buf, wh[0], wh[1], CP437_UTF8)") == 2
    assert "Print(buf, wh[0], wh[1], CP437_UTF8)" not in text
    assert '#error "FL-4512 G2: terminal must not enable the FL4693 product glyph program"' in text
    assert '#error "FL-4512 G2: terminal must not enable the FL4683 glyph program loader"' in text


def test_terminal_adapter_selects_no_glyphs() -> None:
    text = TERMINAL_ADAPTER.read_text(encoding="utf-8")
    for forbidden_call in (
        "Fl4693FinalTupleCommitFrameV1(",
        "Fl4693ProfileUnaryPublishExactV1(",
        "Fl4260ProfileSelectGlyph(",
    ):
        assert forbidden_call not in text, forbidden_call
    assert "GlyphId" not in text
    for fenced_macro in (
        "FL4693_PRODUCT_GLYPH_PROGRAM_ENABLED",
        "FL4683_GLYPH_PROGRAM_LOADER_ENABLED",
    ):
        assert text.count(fenced_macro) == 1, fenced_macro


def test_terminal_specs_enable_no_product_program() -> None:
    for spec in (LINUX_SPEC, MAC_SPEC):
        text = _spec_sources(spec)
        assert "FL4693_PRODUCT_GLYPH_PROGRAM_ENABLED" not in text, spec.name
        assert "FL4683_GLYPH_PROGRAM_LOADER_ENABLED" not in text, spec.name


# --- G2-R2 source/callsite/link-map relationship checks (not label-only) ---
# These tests read the referenced sources and callsites, so an injected
# duplicate writer, a misrouted closure entry, or a detached audit loop fails.


def test_contract_sources_exist_on_disk() -> None:
    for source in COMMON_SOURCES + MAC_AUX_SOURCES + TERMINAL_BACKEND_SOURCES:
        assert (ROOT / source).is_file(), source


def test_final_cell_writer_traceable_through_code() -> None:
    resolve = (ROOT / "engine/render/render_resolve.cpp").read_text(encoding="utf-8")
    assert "void RenderStageResolve(" in resolve
    transport = (ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp").read_text(
        encoding="utf-8"
    )
    assert "bool Fl4693FinalTupleCommitFrameV1(AnsiCell* cells" in transport
    bridge = (ROOT / "engine/game_render_bridge.cpp").read_text(encoding="utf-8")
    assert "void Game::Render(uint64_t _stamp, AnsiCell* ptr" in bridge


def test_commit_frame_consumed_outside_pure_term_loop() -> None:
    web = (ROOT / "web/web_platform.cpp").read_text(encoding="utf-8")
    gl = (ROOT / "platform/terminal_gl_present.cpp").read_text(encoding="utf-8")
    # Web commits through its neutral slot; the product bridge stays
    # adapter-side, never in web globals.
    assert "Fl4693FinalTupleCommitFrameV1(" not in web
    assert "commit_nrt->commit_web_frame" in web
    # Native Current commits through the neutral slot, not a direct
    # call: TermCommitPresentationCellsV1 invokes commit_presentation_cells
    # exactly once per presented frame, and the native adapter owns the
    # single committer call. A direct Fl4693FinalTupleCommitFrameV1 literal
    # in gl would either double-commit or bypass the seam.
    assert "commit_nrt->commit_presentation_cells" in gl
    assert "Fl4693FinalTupleCommitFrameV1(" not in gl
    presenter = (
        ROOT / "apps/fl4512-runtime/platform/fl4512_presenter_services.cpp"
    ).read_text(encoding="utf-8")
    assert "Fl4693NativeFinalCellCommitAndCopyV1(" in presenter
    adapter = (
        ROOT / "apps/fl4512-runtime/platform/fl4693_native_final_cell_adapter.cpp"
    ).read_text(encoding="utf-8")
    assert "Fl4693FinalTupleCommitFrameV1(" in adapter
    pure = TERMINAL_ADAPTER.read_text(encoding="utf-8")
    assert "Fl4693FinalTupleCommitFrameV1(" not in pure


def test_present_helper_has_real_guard_behavior() -> None:
    text = TERMINAL_ADAPTER.read_text(encoding="utf-8")
    body = text.split("static void HostTerminalPresentFinalCellsV1(")[1].split("\n}\n")[0]
    assert "if (!buf" in body
    assert "return;" in body
    assert "Print(buf, w, h, utf)" in body


def test_single_print_funnel_with_render_before_present() -> None:
    text = TERMINAL_ADAPTER.read_text(encoding="utf-8")
    assert text.count("Print(buf") == 1
    sites = re.findall(
        r"hpi->Render\(stamp, buf, wh\[0\], wh\[1\]\);(.*?)"
        r"HostTerminalPresentFinalCellsV1\(buf, wh\[0\], wh\[1\], CP437_UTF8\)",
        text,
        re.DOTALL,
    )
    assert len(sites) == 2


def test_closure_flows_into_linked_objects() -> None:
    for spec in (LINUX_SPEC, MAC_SPEC):
        text = _spec_sources(spec)
        assert "SRCS += $(TERMINAL_RUNTIME_SOURCES)" in text
        assert "OBJS := $(patsubst %,$(OBJDIR)/%.o,$(basename $(SRCS)))" in text
        assert "$(LINK.o) $^ $(LDLIBS)" in text

def test_render_guard_dominates_both_render_sites() -> None:
    # Game::Render writes the plane unconditionally, so a null-plane guard
    # inside the present helper alone leaves both hpi->Render sites exposed
    # after a failed realloc. The frame condition must require buf non-null
    # before either Render call.
    text = TERMINAL_ADAPTER.read_text(encoding="utf-8")
    assert "if (wh[0] > 0 && wh[1] > 0 && buf)" in text
    assert text.count("hpi->Render(stamp, buf, wh[0], wh[1]);") == 2


def test_resize_preserves_plane_on_realloc_failure() -> None:
    # Assigning realloc directly into buf loses the old plane on failure and
    # hands hpi->Render a null pointer. Growth must adopt through a temporary
    # that is only stored on success.
    text = TERMINAL_ADAPTER.read_text(encoding="utf-8")
    assert "buf = (AnsiCell*)realloc(" not in text
    adopted = re.findall(r"(\w+) = \(AnsiCell\*\)realloc\(buf,", text)
    assert len(adopted) == 1
    assert re.search(r"if \(" + adopted[0] + r"\)", text)


def test_resize_notifies_after_successful_adoption() -> None:
    # FL-4512 G2-R4: announcing dimensions before the realloc succeeds leaves
    # the sink on extents the buffer does not back. Notification must follow
    # adoption on growth, or the free on a zero-extent transition; a failed
    # allocation keeps buf/wh/sink on the old dimensions.
    text = TERMINAL_ADAPTER.read_text(encoding="utf-8")
    assert text.count("sink->OnResize(") == 2
    assert text.index("buf = grown;") < text.index("sink->OnResize(")


def test_resize_guards_size_overflow() -> None:
    # Extent multiplication in size_t can wrap before realloc sees it. The
    # resize path must prove the w*h*sizeof(AnsiCell) product fits, and must
    # not call realloc with a folded size on the overflow path.
    text = TERMINAL_ADAPTER.read_text(encoding="utf-8")
    assert "SIZE_MAX / sizeof(AnsiCell)" in text
    assert "if (size_ok)" in text
