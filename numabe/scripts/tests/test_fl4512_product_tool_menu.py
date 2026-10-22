"""Product startup declares optional tool policy; shared menus only consume it.

The executable test crosses the same installed startup Interface as mainmenu.
The source check is an ownership guard, not a visual/runtime acceptance claim.
"""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("has_tool", [False, True])
def test_startup_tool_capability_and_dispatch(tmp_path, has_tool):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable")
    binary = tmp_path / "product_tool"
    source = r'''
#include "engine/render/renderer_ports_v1.h"
#include <cassert>
#include <cstring>
static int launches = 0;
static int installs = 0;
static void launch() { ++launches; }
static const ProductToolV1 tool = {"PRODUCT AUTHORING", &launch};
void AsciickerProductInstallV1() {
    ++installs;
    static const ProductStartupV1 startup = {
        1, nullptr, nullptr, HAS_TOOL ? &tool : nullptr
    };
    static const AsciickerProductPortsV1 ports = {
        1, nullptr, nullptr, nullptr, &startup, nullptr
    };
    AsciickerProductSetPortsV1(&ports);
}
int main() {
    const ProductStartupV1* startup = AsciickerStartup();
    assert(startup && startup->version == 1 && installs == 1);
    assert(AsciickerStartup() == startup && installs == 1);
    if (HAS_TOOL) {
        assert(startup->tool == &tool);
        assert(std::strcmp(startup->tool->label, "PRODUCT AUTHORING") == 0);
        startup->tool->launch();
        startup->tool->launch();
        assert(launches == 2);
    } else {
        assert(startup->tool == nullptr && launches == 0);
    }
}
'''
    compiled = subprocess.run(
        [compiler, "-std=c++11", f"-DHAS_TOOL={int(has_tool)}", "-I", str(ROOT),
         "-x", "c++", "-", "-o", str(binary)],
        input=source, text=True, capture_output=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    executed = subprocess.run([str(binary)], capture_output=True, text=True)
    assert executed.returncode == 0, executed.stderr


def test_shared_menu_has_no_product_policy_or_classic_stub():
    menu = (ROOT / "engine/mainmenu.cpp").read_text()
    assert "ASCIICKER_RENDERER_CLASSIC_CP437" not in menu
    assert "FL4512" not in menu
    assert "launch_presenter" not in menu
    classic = (ROOT / "apps/classic-runtime/classic_product_adapter.cpp").read_text()
    assert "ClassicLaunchPresenterTool" not in classic
    ports = (ROOT / "engine/render/renderer_ports_v1.h").read_text()
    assert "launch_presenter_tool" not in ports
