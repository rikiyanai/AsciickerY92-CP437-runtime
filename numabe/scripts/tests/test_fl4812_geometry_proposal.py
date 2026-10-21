"""Proposal adapters preserve source identity and camera conventions (FL-4812)."""
import json
import hashlib
import numpy as np
import pytest
from PIL import Image
from scripts.pipeline.camera_solve.geometry_proposal import (
    select_corpus, validate_prediction, depth_to_local_points,
)


def test_nontrivial_world_to_camera_depth_round_trip():
    k = np.array([[200., 0, 2.], [0, 210., 1.], [0, 0, 1.]])
    r = np.array([[0., -1, 0], [1., 0, 0], [0, 0, 1.]])
    e = np.column_stack([r, [3., 4., 5.]])
    depth = np.arange(12, dtype=float).reshape(3, 4) + 1
    world = depth_to_local_points(depth, k, e)
    camera = world @ r.T + e[:, 3]
    uv = camera @ k.T
    uv = uv[..., :2] / uv[..., 2:]
    v, u = np.indices(depth.shape)
    np.testing.assert_allclose(camera[..., 2], depth)
    np.testing.assert_allclose(uv, np.stack([u, v], -1), atol=1e-10)


@pytest.mark.parametrize('bad', ['reflection', 'nan', 'depth', 'count', 'intrinsics'])
def test_corrupt_backend_output_is_rejected(bad):
    depth = np.ones((2, 3, 4)); confidence = depth.copy()
    e = np.tile(np.eye(4)[:3], (2, 1, 1)); k = np.tile(np.eye(3), (2, 1, 1))
    if bad == 'reflection': e[0, 0, 0] = -1
    if bad == 'nan': confidence[0, 0, 0] = np.nan
    if bad == 'depth': depth[0, 0, 0] = 0
    if bad == 'count': e = e[:1]
    if bad == 'intrinsics': k[0, 2, 2] = 5
    with pytest.raises(ValueError): validate_prediction(depth, confidence, e, k, 2)


def test_frozen_corpus_order_and_pixel_changes(tmp_path):
    rows = []
    for cid in ['b', 'a']:
        p = tmp_path / (cid + '.png'); Image.new('RGB', (8, 8)).save(p)
        rows.append({'capture_id': cid, 'local_path': p.name,
                     'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                     'projection_model': 'rectilinear', 'retention_permitted': True,
                     'license': 'CC0-1.0', 'width': 8, 'height': 8})
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({'schema': 'y92.capture_source_index.v1', 'captures': rows,
        'default_reconstruction_component': 'test',
        'reconstruction_components': [{'component_id': 'test', 'capture_ids': ['b', 'a']}]}))
    _, corpus = select_corpus(index)
    assert [r['capture_id'] for r in corpus] == ['b', 'a']
    (tmp_path / 'a.png').write_bytes(b'changed')
    with pytest.raises(ValueError, match='source pixels changed'): select_corpus(index)


def test_e2e_proposal_refuses_runtime_launch(tmp_path):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, 'scripts/sbu_e2e_run.py',
                             '--geometry-proposal-config', 'unused.json',
                             '--run-root', str(tmp_path / 'output'), '--launch'],
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert 'no launch/activation' in result.stderr
    assert not (tmp_path / 'output').exists()


def test_exact_perspective_fov_is_available_as_conditioning():
    from scripts.pipeline.camera_solve.geometry_proposal import capture_intrinsics
    k = capture_intrinsics([{'width': 512, 'height': 256, 'horizontal_fov_deg': 90}])
    np.testing.assert_allclose(k[0], [[256, 0, 256], [0, 256, 128], [0, 0, 1]])
    with pytest.raises(ValueError): capture_intrinsics([{'width': 512, 'height': 512}])


def test_dense_reprojection_detects_wrong_depth_against_independent_image_tracks():
    from scripts.pipeline.camera_solve.geometry_proposal import evaluate_sparse_reprojections
    k = np.array([[10., 0, 4], [0, 10, 4], [0, 0, 1]])
    e = np.tile(np.eye(4)[:3], (2,1,1));e[1,0,3] = -1
    arrays = {'depth': np.full((2,8,8),10.), 'intrinsics': np.stack([k,k]), 'extrinsics': e}
    corpus = [{'capture_id': c, 'width':8, 'height':8} for c in ['a','b']]
    sparse = {'points':[{'observations':[
        {'capture_id':'a','reprojection_error_px':0.,'source_image_pixel_px':[4,4]},
        {'capture_id':'b','reprojection_error_px':0.,'source_image_pixel_px':[3,4]}]}]}
    exact = evaluate_sparse_reprojections(arrays,corpus,sparse)
    assert exact['directed_reprojections'] == 2
    assert exact['median_error_px_finite'] == pytest.approx(0.)
    arrays['depth'][:] = 1.
    wrong = evaluate_sparse_reprojections(arrays,corpus,sparse)
    assert wrong['median_error_px_finite'] > 4
    assert wrong['fraction_within_4px'] == 0


def test_mapanything_pose_direction_and_validity_mask_are_retained():
    from scripts.pipeline.camera_solve.geometry_proposal import mapanything_arrays
    pose = np.eye(4); pose[:3, :3] = [[0,-1,0],[1,0,0],[0,0,1]]; pose[:3,3] = [2,3,4]
    prediction = {'camera_poses': pose[None], 'depth_z': np.ones((1,2,2,1)),
                  'conf': np.ones((1,2,2)), 'intrinsics': np.eye(3)[None],
                  'mask': np.array([[[[1],[0]],[[1],[1]]]])}
    arrays = mapanything_arrays([prediction])
    np.testing.assert_allclose(arrays['extrinsics'][0] @ pose, np.eye(4)[:3])
    assert arrays['valid_mask'][0].tolist() == [[True,False],[True,True]]
    assert arrays['depth'].shape == (1,2,2)
    prediction['camera_poses'][0,3,3] = 2
    with pytest.raises(ValueError, match='homogeneous'): mapanything_arrays([prediction])


def test_masked_depth_does_not_become_valid_geometry():
    from scripts.pipeline.camera_solve.geometry_proposal import validate_prediction
    d = np.ones((2,3,4)); d[0,0,0] = 0
    k = np.tile(np.eye(3), (2,1,1)); e = np.tile(np.eye(4)[:3], (2,1,1))
    mask = d > 0
    validate_prediction(d,np.ones_like(d),e,k,2,mask)
    with pytest.raises(ValueError, match='nonpositive'):
        validate_prediction(d,np.ones_like(d),e,k,2,np.ones_like(mask))
    with pytest.raises(ValueError):
        validate_prediction(d,np.ones_like(d),e,k,2,np.zeros_like(mask))
