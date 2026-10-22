"""Compiled runtime checks for the FL-4588 Material Profile URDO command.

This links the real editor/urdo.cpp against a narrow profile-publication seam. It is
native unit evidence, not headed HTML proof.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


HARNESS = r"""
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "apps/asciicker-presenter/urdo.h"
#include "engine/enemygen.h"
#include "engine/material_glyph_plane.h"

EnemyGen* enemygen_head = nullptr;
EnemyGen* enemygen_tail = nullptr;

static uint64_t live_state[8] = {};
static bool reject_profile_publish = false;
static int profile_publish_calls = 0;
static int last_profile_count = 0;

size_t Fl4260ProfileUndoBlobSize() { return sizeof(uint64_t); }

bool Fl4260ProfileUndoSwapPublishBatch(int count, const int*,
    const unsigned char*, const int*, void* blob, size_t stride)
{
    profile_publish_calls++;
    last_profile_count = count;
    if (reject_profile_publish || stride != sizeof(uint64_t))
        return false;
    uint64_t* other = static_cast<uint64_t*>(blob);
    for (int i = 0; i < count; i++)
    {
        const uint64_t prior = live_state[i];
        live_state[i] = other[i];
        other[i] = prior;
    }
    return true;
}

void* GetMaterialArr() { static unsigned char data[65536] = {}; return data; }
void* GetPaletteArr() { static unsigned char data[65536] = {}; return data; }
void EditorMaterialUndoUpdate(int) {}
void EditorPaletteUndoUpdate(int) {}
void EditorTerrainOverviewMarkPatchDirty(Patch*) {}
void EditorTerrainOverviewMarkTerrainTopologyDirty(Terrain*) {}

Patch* AddTerrainPatch(Terrain*, int, int, int) { return nullptr; }
size_t TerrainDetach(Terrain*, Patch*, int*, int*) { return 0; }
size_t TerrainAttach(Terrain*, Patch*, int, int) { return 0; }
size_t TerrainDispose(Patch*) { return 0; }
uint16_t* GetTerrainHeightMap(Patch*) { static uint16_t data[4096] = {}; return data; }
uint16_t* GetTerrainVisualMap(Patch*) { static uint16_t data[4096] = {}; return data; }
void UpdateTerrainHeightMap(Patch*) {}
void UpdateTerrainVisualMap(Patch*) {}
uint16_t GetTerrainDiag(Patch*) { return 0; }
void SetTerrainDiag(Patch*, uint16_t) {}

Inst* CreateInst(Mesh*, int, const double[16], const char*, int) { return nullptr; }
Inst* CreateInst(World*, Sprite*, int, float[3], float, int, int, int[4], const char*, int) { return nullptr; }
Inst* CreateInst(World*, Item*, int, float[3], float, int) { return nullptr; }
void SoftInstAdd(Inst*) {}
void SoftInstDel(Inst*) {}
void HardInstDel(Inst*) {}
int GetInstStoryID(Inst*) { return 0; }
void SetInstStoryID(Inst*, int) {}

MaterialGlyphPlane* material_glyph_plane_alloc() { return nullptr; }
void material_glyph_plane_free(MaterialGlyphPlane*) {}
void material_glyph_plane_init(MaterialGlyphPlane*) {}

static void require(bool condition, const char* message)
{
    if (!condition)
    {
        std::fprintf(stderr, "FAIL: %s\n", message);
        std::exit(1);
    }
}

int main()
{
    URDO_Purge();
    require(!URDO_CanUndo(), "fresh history exposes Undo");
    require(!URDO_CanRedo(), "fresh history exposes Redo");
    require(!URDO_Undo(0), "empty Undo reports success");
    require(!URDO_Redo(0), "empty Redo reports success");

    const int indices[2] = {1, 2};
    const unsigned char namespaces[2] = {1, 1};
    const int materials[2] = {5, 6};
    const uint64_t before[2] = {10, 100};
    live_state[0] = 20;
    live_state[1] = 200;

    const uint64_t revision_before_record = URDO_Revision();
    const size_t bytes_before_record = URDO_Bytes();
    URDO_Fl4260ProfileBatch* prepared = URDO_Fl4260ProfilePrepare(
        2, indices, namespaces, materials, before);
    require(prepared != nullptr, "valid batch preparation failed");
    require(URDO_Revision() == revision_before_record,
        "preparation advanced history revision");
    require(URDO_Bytes() == bytes_before_record,
        "unlinked preparation entered history accounting");
    require(!URDO_CanUndo() && !URDO_CanRedo(),
        "unlinked preparation moved the history cursor");
    URDO_Fl4260ProfileLink(prepared);
    require(URDO_CanUndo(), "recorded batch lacks Undo");
    require(!URDO_CanRedo(), "recorded batch exposes Redo");
    require(URDO_Revision() > revision_before_record, "record did not advance revision");
    require(URDO_Bytes() > bytes_before_record, "record did not account owned memory");

    reject_profile_publish = true;
    const uint64_t revision_before_rejected_undo = URDO_Revision();
    require(!URDO_Undo(0), "rejected Undo reported success");
    require(URDO_Revision() == revision_before_rejected_undo,
        "rejected Undo advanced revision");
    require(URDO_CanUndo() && !URDO_CanRedo(),
        "rejected Undo moved the history cursor");
    require(live_state[0] == 20 && live_state[1] == 200,
        "rejected Undo changed live profile state");
    require(last_profile_count == 2, "Undo did not publish one whole batch");

    reject_profile_publish = false;
    const int calls_before_undo = profile_publish_calls;
    require(URDO_Undo(0), "accepted Undo reported failure");
    require(profile_publish_calls == calls_before_undo + 1,
        "accepted Undo published more than one batch");
    require(live_state[0] == 10 && live_state[1] == 100,
        "accepted Undo did not restore the complete batch");
    require(!URDO_CanUndo() && URDO_CanRedo(),
        "accepted Undo did not move the history cursor");

    reject_profile_publish = true;
    const uint64_t revision_before_rejected_redo = URDO_Revision();
    require(!URDO_Redo(0), "rejected Redo reported success");
    require(URDO_Revision() == revision_before_rejected_redo,
        "rejected Redo advanced revision");
    require(!URDO_CanUndo() && URDO_CanRedo(),
        "rejected Redo moved the history cursor");
    require(live_state[0] == 10 && live_state[1] == 100,
        "rejected Redo changed live profile state");

    reject_profile_publish = false;
    const int calls_before_redo = profile_publish_calls;
    require(URDO_Redo(0), "accepted Redo reported failure");
    require(profile_publish_calls == calls_before_redo + 1,
        "accepted Redo published more than one batch");
    require(live_state[0] == 20 && live_state[1] == 200,
        "accepted Redo did not restore the complete batch");
    require(URDO_CanUndo() && !URDO_CanRedo(),
        "accepted Redo did not move the history cursor");

    URDO_Purge();
    require(!URDO_CanUndo() && !URDO_CanRedo(), "purge left history reachable");
    require(URDO_Bytes() == bytes_before_record, "purge leaked batch history memory");

    const int discard_indices[2] = {3, 4};
    const int discard_materials[2] = {7, 8};
    URDO_Fl4260ProfileBatch* discarded = URDO_Fl4260ProfilePrepare(
        2, discard_indices, namespaces, discard_materials, before);
    require(discarded != nullptr, "discard-path preparation failed");
    const uint64_t revision_before_discard = URDO_Revision();
    const size_t bytes_before_discard = URDO_Bytes();
    URDO_Fl4260ProfileDiscard(discarded);
    require(URDO_Revision() == revision_before_discard,
        "discard advanced history revision");
    require(URDO_Bytes() == bytes_before_discard,
        "discard changed history accounting");
    require(!URDO_CanUndo() && !URDO_CanRedo(),
        "discard moved the history cursor");

    const unsigned char duplicate_namespaces[2] = {1, 1};
    const int duplicate_materials[2] = {7, 7};
    require(URDO_Fl4260ProfilePrepare(
        2, indices, duplicate_namespaces, duplicate_materials, before) == nullptr,
        "duplicate qualified profile keys were accepted");
    require(!URDO_CanUndo() && !URDO_CanRedo(),
        "duplicate qualified profile keys entered history");

    const int duplicate_indices[2] = {5, 5};
    const int unique_materials[2] = {7, 8};
    require(URDO_Fl4260ProfilePrepare(
        2, duplicate_indices, namespaces, unique_materials, before) == nullptr,
        "duplicate flattened profile indices were accepted");
    require(!URDO_CanUndo() && !URDO_CanRedo(),
        "duplicate flattened profile indices entered history");

    std::puts("FL4588_MATERIAL_PROFILE_URDO_RUNTIME_PASS");
    return 0;
}
"""


def test_material_profile_urdo_runtime(tmp_path: Path) -> None:
    source = tmp_path / "material_profile_urdo_runtime.cpp"
    binary = tmp_path / "material_profile_urdo_runtime"
    source.write_text(HARNESS, encoding="utf-8")
    compile_result = subprocess.run(
        [
            "clang++",
            "-std=c++20",
            "-I",
            str(ROOT),
            "-I",
            str(ROOT / "editor"),
            "-I",
            str(ROOT / "engine"),
            "-I",
            str(ROOT / "engine" / "render"),
            str(source),
            str(ROOT / "apps" / "asciicker-presenter" / "urdo.cpp"),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    runtime_result = subprocess.run(
        [str(binary)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert runtime_result.returncode == 0, runtime_result.stderr
    assert "FL4588_MATERIAL_PROFILE_URDO_RUNTIME_PASS" in runtime_result.stdout
