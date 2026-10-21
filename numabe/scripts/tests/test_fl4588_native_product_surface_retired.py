from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ASCIIID = ROOT / "apps" / "asciicker-presenter" / "asciiid.cpp"
SDL = ROOT / "platform" / "sdl.cpp"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_visible_native_launch_flags_are_present_for_default_product() -> None:
    source = _source(ASCIIID)
    assert 'strcmp(argv[i], "--viewer")' in source
    assert 'strcmp(argv[i], "--sprite-browser")' in source
    assert '"  --viewer' in source
    assert '"  --sprite-browser' in source


def test_default_native_product_entrypoint_reaches_runtime_boot() -> None:
    source = _source(ASCIIID)
    runtime_boot = source.index("char global_profile_assets_root[PATH_MAX]")
    guard = source.find("if (!g_asciiid_html_shell_backend && !g_mcp_mode)")

    if guard != -1 and guard < runtime_boot:
        guard_body = source[guard:runtime_boot]
        assert "native product surface retired" not in guard_body
        assert "return 64;" not in guard_body
    assert "native product surface retired" not in source


def test_html_backend_is_explicit_alternate_and_not_native_default() -> None:
    source = _source(ASCIIID)
    html_arg = source.index('strcmp(argv[i], "--authoring-port")')
    service_boot = source.index("new AsciiidApplicationService", html_arg)
    runtime_boot = source.index("char global_profile_assets_root[PATH_MAX]")

    assert html_arg < service_boot < runtime_boot
    assert 'ASCIIID_HTML_SHELL_BACKEND", "1"' in source[html_arg:runtime_boot]
    assert "g_asciiid_html_shell_backend = true" in source[html_arg:runtime_boot]
    assert "new AsciiidApplicationService" not in source[:html_arg]


def test_one_cpp_runtime_intent_authority_is_linked_for_both_modes() -> None:
    makefile = _source(ROOT / "apps/asciicker-presenter/makefile_asciiid_mac")
    header = _source(ROOT / "apps" / "asciicker-presenter" / "asciiid_application_service.h")
    transport = _source(ROOT / "apps" / "asciicker-presenter" / "asciiid_authoring_http.h")

    assert makefile.count("apps/asciicker-presenter/asciiid_application_service.cpp") == 1
    assert makefile.count("apps/asciicker-presenter/asciiid_authoring_http.cpp") == 1
    assert "class AsciiidApplicationService" in header
    assert "struct RuntimeIntentRegistryRowV1" in header
    assert "AsciiidApplicationService* service_" in transport


def test_macos_window_backend_shows_native_default_and_hides_html_backend() -> None:
    source = _source(SDL)
    function = source[source.index("void a3dSetVisible"):]
    function = function[:function.index("\n}") + 2]
    assert 'if (set && !getenv("ASCIIID_HTML_SHELL_BACKEND"))' in function
    assert "SDL_ShowWindow(wnd->win);" in function
    assert "SDL_HideWindow(wnd->win);" in function
