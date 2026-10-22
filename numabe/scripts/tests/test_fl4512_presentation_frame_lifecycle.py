"""Exercise actual frame-open bodies across menu, world, resize, and rejection."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "apps/fl4512-runtime/platform/fl4512_presenter_services.cpp"
TRANSPORT = ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"


def test_common_game_entry_opens_presentation_before_any_early_return():
    game = (ROOT / "engine/game_render_bridge.cpp").read_text()
    body = game.split("void Game::Render(", 1)[1]
    assert body.count("frame_presentation->bridge_reset_for_frame(width, height)") == 1
    assert body.index("frame_presentation->bridge_reset_for_frame(width, height)") < body.index("return;")
    native = (ROOT / "platform/terminal_gl_present.cpp").read_text()
    assert "->bridge_reset_for_frame(" not in native
    web = (ROOT / "web/web_platform.cpp").read_text()
    assert web.index("game->Render(render_begin_us, render_buf, width, height)") < web.index("commit_nrt->commit_web_frame(render_buf, width, height)")


def test_product_reset_opens_actual_tuple_owner_without_world_state(tmp_path):
    services = SERVICES.read_text()
    reset = services[services.index("void Fl4512PresenterBridgeResetForFrameV1("):
                     services.index("void Fl4512PresenterBridgeUploadExtentV1(")]
    transport = TRANSPORT.read_text()
    begin = transport[transport.index("void Fl4693FinalTupleBeginFrameV1("):
                      transport.index("bool Fl4693FinalTupleStageInputV1(")]
    source = tmp_path / "frame.cpp"
    source.write_text(r'''
#include <cassert>
#include <cstring>
static const int kMaxWidth = 160, kMaxHeight = 90;
static int g_width, g_height, profile_resets, bridge_resets;
static unsigned char g_staged[160*90], g_final[160*90], g_committed[160*90];
static struct { unsigned fl4693_product_u6_finalized; } g_fl4131_harri_gpu_bridge;
static void Fl4693ProfileTupleBeginFrameV1() { ++profile_resets; }
struct Fl4512RuntimeStateV1 {};
struct Fl4512RuntimeHooksV1 {
    void (*bridge_reset_for_frame)(Fl4512RuntimeStateV1*, int, int);
};
static Fl4512RuntimeStateV1 state;
static bool world_exists = false, hooks_exist = true;
static void ResetBridge(Fl4512RuntimeStateV1*, int w, int h) {
    assert(g_width == w && g_height == h);
    assert(g_fl4131_harri_gpu_bridge.fl4693_product_u6_finalized == 1);
    ++bridge_resets;
}
static Fl4512RuntimeHooksV1 hooks = {ResetBridge};
static const Fl4512RuntimeHooksV1* Fl4512RuntimeHooks() {
    return hooks_exist ? &hooks : nullptr;
}
static Fl4512RuntimeStateV1* Fl4512RuntimeBridgeStateV1() {
    return world_exists ? &state : nullptr;
}
''' + begin + reset + r'''
int main() {
    memset(g_final, 7, sizeof(g_final));
    memset(g_staged, 7, sizeof(g_staged));
    memset(g_committed, 7, sizeof(g_committed));
    Fl4512PresenterBridgeResetForFrameV1(128, 72);
    assert(g_width == 128 && g_height == 72 && profile_resets == 1);
    assert(bridge_resets == 0);
    for (unsigned i = 0; i < sizeof(g_final); ++i)
        assert(!g_final[i] && !g_staged[i] && !g_committed[i]);
    world_exists = true;
    Fl4512PresenterBridgeResetForFrameV1(160, 90);
    assert(profile_resets == 2 && bridge_resets == 1);
    hooks_exist = false;
    Fl4512PresenterBridgeResetForFrameV1(161, 91);
    assert(g_width == 0 && g_height == 0 && profile_resets == 3);
    Fl4512PresenterBridgeResetForFrameV1(80, 45);
    assert(g_width == 80 && g_height == 45 && profile_resets == 4);
}
''')
    binary = tmp_path / "frame"
    built = subprocess.run(["c++", "-std=c++11", "-fsanitize=address,undefined",
                            str(source), "-o", str(binary)], capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    executed = subprocess.run([str(binary)], capture_output=True, text=True)
    assert executed.returncode == 0, executed.stdout + executed.stderr
