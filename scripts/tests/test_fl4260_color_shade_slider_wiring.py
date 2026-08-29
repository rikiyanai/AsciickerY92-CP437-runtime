from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ASCIIID = ROOT / "editor" / "asciiid.cpp"
HARRI = ROOT / "engine" / "fl4131_runtime_harri_resolver.cpp"
RESOLVE = ROOT / "engine" / "render" / "render_resolve.cpp"


def test_fl4260_color_shade_slider_fields_reach_live_profile() -> None:
    asciiid = ASCIIID.read_text()
    harri = HARRI.read_text()
    resolve = RESOLVE.read_text()

    assert (
        "Fl4260SetActiveProfileColors(int mat, const uint8_t* fg_rgb, const uint8_t* bg_rgb, int stride,\n"
        "\tconst int* fg_strength, const int* bg_strength,\n"
        "\tconst int* shade_contrast, const int* shade_band_thresholds)"
    ) in harri

    for field in (
        "row_fg_strength[4]",
        "row_bg_strength[4]",
        "row_shade_contrast[4]",
        "shade_band_thresholds[4]",
    ):
        assert field in harri

    assert "Fl4260ApplyColorModulation(" in harri
    assert "Fl4260ResolveColorRow(" in harri
    assert "shade_band_thresholds" in harri

    assert "d.row_fg_strength, d.row_bg_strength," in asciiid
    assert "d.row_shade_contrast, d.shade_band_thresholds" in asciiid

    assert "Fl4260GetActiveProfileColor(int mat, int elv, int shade" in harri
    assert "Fl4260GetActiveProfileColor(in.mat_id, in.elev_quantized, in.shade_quantized" in resolve
