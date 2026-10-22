import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from scripts.pipeline.equirectangular_capture_views import derive_views, rectilinear_view


def test_rectilinear_projection_maps_cardinal_longitudes_to_view_center():
    panorama = np.zeros((180, 360, 3), dtype=np.uint8)
    panorama[:, 175:186] = (0, 0, 255)
    panorama[:, 265:276] = (0, 255, 0)

    forward = rectilinear_view(panorama, yaw_offset_deg=0, width_px=65, height_px=65)
    right = rectilinear_view(panorama, yaw_offset_deg=90, width_px=65, height_px=65)

    assert forward[32, 32, 2] > 240
    assert forward[32, 32, 1] < 10
    assert right[32, 32, 1] > 240
    assert right[32, 32, 2] < 10


def test_derived_index_preserves_source_identity_and_declares_pinhole_components(tmp_path):
    panorama = np.zeros((64, 128, 3), dtype=np.uint8)
    panorama[:, :64] = (20, 40, 60)
    source_image = tmp_path / "panorama.jpg"
    assert cv2.imwrite(str(source_image), panorama)
    digest = hashlib.sha256(source_image.read_bytes()).hexdigest()
    source_index = tmp_path / "source.json"
    source_index.write_text(json.dumps({
        "schema": "y92.capture_source_index.v1",
        "captures": [{
            "capture_id": "pano-1",
            "provider": "Mapillary",
            "sequence_id": "sequence-1",
            "sequence_index": 7,
            "local_path": source_image.name,
            "projection_model": "equirectangular",
            "heading_deg": 30.0,
            "sha256": digest,
            "width": 128,
            "height": 64,
        }],
    }))
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({
        "schema": "y92.route_capture_manifest.v1",
        "captures": [{"capture_id": "pano-1", "selected_keyframe": True}],
    }))

    document = derive_views(
        source_index_path=source_index,
        selected_capture_manifest_path=selection,
        output_dir=tmp_path / "views",
        output_index_path=tmp_path / "views.json",
        yaw_offsets_deg=(0.0, 90.0),
        size_px=32,
    )

    assert document["counts"] == {
        "source_panoramas": 1,
        "derived_views": 2,
        "reconstruction_components": 2,
    }
    assert document["default_reconstruction_component"] in {
        "sequence-1__yaw_p000", "sequence-1__yaw_p090"
    }
    assert {row["heading_deg"] for row in document["captures"]} == {30.0, 120.0}
    assert all(row["projection_model"] == "rectilinear" for row in document["captures"])
    assert all(row["derived_from"]["capture_id"] == "pano-1" for row in document["captures"])
    assert all((tmp_path / row["local_path"]).is_file() for row in document["captures"])
