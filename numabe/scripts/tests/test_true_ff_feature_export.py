"""Offline export-filter contract; does not run an engine or build a PCK."""
import configparser
import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_each_preset_includes_feature_banks_without_widening_non_ff_filters():
    presets = configparser.ConfigParser(interpolation=None)
    presets.read(ROOT / "godot_project/export_presets.cfg")
    ff_filters = {
        "assets/world_partition/true_ff_feature_bank/*.bin",
        "assets/world_partition/true_ff_feature_bank/index.v1.json",
        "assets/world_partition/true_ff_actor_feature_sources.v1.json",
    }
    banks = ROOT / "godot_project/assets/world_partition/true_ff_feature_bank"
    paths = [path.relative_to(ROOT / "godot_project").as_posix()
             for path in banks.glob("*.bin")]
    assert paths
    paths += ["assets/world_partition/true_ff_feature_bank/index.v1.json",
              "assets/world_partition/true_ff_actor_feature_sources.v1.json"]
    checked = 0
    for name in presets.sections():
        if not name.startswith("preset.") or name.endswith(".options"):
            continue
        filters = set(presets[name]["include_filter"].strip('"').split(","))
        assert filters == {"*.a3d", "*.xp"} | ff_filters
        expected_exclude = ('""' if presets[name]["platform"] == '"Web"'
                            else '"render/backends/web/*,web_shell.html"')
        assert presets[name]["exclude_filter"] == expected_exclude
        for path in paths:
            assert any(fnmatch.fnmatchcase(path, pattern) for pattern in filters), (name, path)
        for foreign in ("assets/unrelated.bin", "assets/world_partition/debug.json",
                        "assets/world_partition/true_ff_feature_bank/notes.txt"):
            assert not any(fnmatch.fnmatchcase(foreign, pattern) for pattern in filters)
        checked += 1
    assert checked == 6
