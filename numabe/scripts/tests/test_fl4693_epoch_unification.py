"""FL-4693 P1 contracts for one typed renderer continuity epoch."""

from pathlib import Path
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_epoch_comparison_is_single_and_old_store_fields_are_deleted() -> None:
    epoch = _read("apps/fl4512-runtime/engine/fl4693_runtime_epoch.h")
    active = _read("apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp")
    track = _read("engine/render/fl4512_spacetime_track_owner.h")
    presenter = _read("platform/terminal_gl_present.cpp")

    assert epoch.count("Fl4693RuntimeEpochTransitionV1(") == 1
    assert "Fl4693RuntimeEpochTransitionV1(" in active
    assert "Fl4693RuntimeEpochTransitionV1(" in track
    assert "uint64_t store_frame;" not in active
    assert "uint64_t world_generation;\n\tuint64_t next_semantic" not in active
    assert "char active_registry_sha256[65];\n\tuint8_t initialized" not in active
    assert "uint64_t selection_epoch;" not in track
    assert "Fl4512SpacetimeStoreBeginFrameV2" not in track
    assert "Fl4512SpacetimeStoreBeginFrameV2" not in presenter


def test_scene_and_publication_clocks_are_not_interchangeable(tmp_path: Path) -> None:
    source = tmp_path / "clock_type_rejection.cpp"
    source.write_text(
        textwrap.dedent(
            """
            #include "apps/fl4512-runtime/engine/fl4693_runtime_epoch.h"
            static void consume_scene(Fl4693SceneFrameIdV1) {}
            int main() {
                Fl4693PublicationFrameIdV1 publication = {1u};
                consume_scene(publication);
                return 0;
            }
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), "-c", str(source)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_epoch_transition_reasons_execute(tmp_path: Path) -> None:
    source = tmp_path / "epoch_transition.cpp"
    binary = tmp_path / "epoch_transition"
    source.write_text(
        textwrap.dedent(
            """
            #include <cstring>
            #include "apps/fl4512-runtime/engine/fl4693_runtime_epoch.h"
            int main() {
                Fl4693RuntimeEpochV1 a = {};
                a.world_generation = 1;
                a.profile_revision = 2;
                a.frame_id.value = 3;
                std::memset(a.active_registry_sha256, 'a', 64);
                Fl4693RuntimeEpochV1 b = a;
                b.frame_id.value = 4;
                if (Fl4693RuntimeEpochTransitionV1(&a, &b, true) !=
                    FL4693_EPOCH_TRANSITION_CONTINUE) return 1;
                b.profile_revision = 5;
                if (Fl4693RuntimeEpochTransitionV1(&a, &b, true) !=
                    FL4693_EPOCH_TRANSITION_PROFILE_REVISION_CHANGE) return 2;
                b = a;
                b.frame_id.value = 3;
                if (Fl4693RuntimeEpochTransitionV1(&a, &b, true) !=
                    FL4693_EPOCH_TRANSITION_FRAME_NONMONOTONIC) return 3;
                return 0;
            }
            """
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        cwd=ROOT,
        check=True,
    )
    subprocess.run([str(binary)], cwd=ROOT, check=True)
