from scripts.pipeline.population_asset_geometry import build_point_mesh


def test_twin_opposed_path_lamp_has_two_symmetric_heads():
    vertices, faces = build_point_mesh(
        "pole_streetlight", "twin_opposed_path_lamp", 1.0, {})

    xs = [v[0] for v in vertices]
    zs = [v[2] for v in vertices]
    assert min(xs) == -0.68
    assert max(xs) == 0.68
    assert max(zs) == 5.0
    assert len(faces) == 24
