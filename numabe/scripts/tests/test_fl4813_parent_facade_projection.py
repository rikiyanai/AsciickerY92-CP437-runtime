"""FL-4813: parent surfaces replace independent tiny-region plane fitting."""
import copy
import hashlib
import json

import cv2
import numpy as np
import pytest

from scripts.pipeline.reconstruct_anchored_facade import (
    bounded_parent_plane, project_child_to_parent, project_point,
)
from scripts.pipeline.reconstruct_planar_tracks import build_batch


def fixture():
    # Elevated facade catches accidentally adding its base elevation twice.
    parent = {'parent_id': 'way/1:edge/0', 'source_refs': ['osm:way/1'],
              'edge_enu_m': [[0, 10, 7], [10, 10, 7]], 'z_bounds_enu_m': [7, 13]}
    rotation = cv2.Rodrigues(np.array([[1., 0, 0], [0, 0, -1.], [0, 1., 0]]))[0].ravel().tolist()
    cameras = [{'capture_id': f'c{i}', 'frame_rank': i,
                'solved_center_enu_m': [x, 0, 8.5],
                'solved_rotation_rodrigues': rotation,
                'working_image': {'dimensions_px': [512, 512]}}
               for i, x in enumerate([2., 4., 6.])]
    intrinsics = {'focal_px': 256., 'principal_point_px': [256., 256.]}
    receipt = {'cameras': cameras, 'intrinsics': {'per_frame': {str(i): intrinsics for i in range(3)}}}
    corners = np.array([[3., 10., 8.], [5., 10., 8.], [5., 10., 10.], [3., 10., 10.]])
    rows = []
    for camera in cameras:
        pixels = np.array([project_point(camera, intrinsics, p) for p in corners])
        box = [*pixels.min(axis=0), *pixels.max(axis=0)]
        rows.append({'capture_id': camera['capture_id'], 'observation_id': 'd' + camera['capture_id'],
                     'image_size_px': [512, 512], 'bbox_xyxy_px': box,
                     'detector_confidence': .9, 'detector_text_label': 'window'})
    return parent, receipt, rows


def test_child_without_sparse_points_has_held_out_agreement_and_correct_absolute_z():
    parent, cameras, observations = fixture()
    result = project_child_to_parent(parent, observations, cameras)
    assert result['gate']['pass'], result['gate']
    assert result['world_ir_promotion_ready'] is False
    assert np.array(result['world_corners_enu_m'])[:, 2].min() == pytest.approx(8.)
    assert np.array(result['world_corners_enu_m'])[:, 2].max() == pytest.approx(10.)
    assert all(row['bbox_iou'] == pytest.approx(1.) for row in result['held_out_back_projection'])


@pytest.mark.parametrize('failure', ['bounds', 'behind', 'one_view', 'same_position', 'disagreement'])
def test_rejects_unusable_or_inconsistent_parent_association(failure):
    parent, cameras, observations = fixture()
    if failure == 'bounds': parent['z_bounds_enu_m'] = [11, 13]
    if failure == 'behind': parent['edge_enu_m'] = [[0, -10, 7], [10, -10, 7]]
    if failure == 'one_view': observations = observations[:1]
    if failure == 'same_position':
        for c in cameras['cameras']: c['solved_center_enu_m'] = [2, 0, 8.5]
    if failure == 'disagreement': observations[-1]['bbox_xyxy_px'] = [220, 190, 230, 200]
    result = project_child_to_parent(parent, observations, cameras)
    assert not result['gate']['pass']
    assert result['world_ir_promotion_ready'] is False


def test_duplicate_frame_cannot_satisfy_multiview():
    parent, cameras, rows = fixture()
    with pytest.raises(ValueError, match='one region per capture'):
        project_child_to_parent(parent, [rows[0], rows[0]], cameras)


@pytest.mark.parametrize('field', ['edge', 'height', 'focal', 'center'])
def test_nonfinite_domains_are_rejected(field):
    parent, cameras, rows = fixture()
    if field == 'edge': parent['edge_enu_m'][0][0] = float('nan')
    if field == 'height': parent['z_bounds_enu_m'][1] = float('inf')
    if field == 'focal': cameras['intrinsics']['per_frame']['0']['focal_px'] = float('nan')
    if field == 'center': cameras['cameras'][0]['solved_center_enu_m'][0] = float('nan')
    with pytest.raises(ValueError): project_child_to_parent(parent, rows, cameras)


def write_inputs(tmp_path):
    parent, cameras, rows = fixture()
    def write(name, document):
        p = tmp_path / name
        p.write_text(json.dumps(document))
        return p
    camera_path = write('cameras.json', cameras)
    captures, frames = [], []
    for row in rows:
        image = tmp_path / (row['capture_id'] + '.jpg')
        image.write_bytes(row['capture_id'].encode())
        captures.append({'capture_id': row['capture_id'], 'local_path': image.name,
                         'sha256': hashlib.sha256(image.read_bytes()).hexdigest()})
        frames.append({'capture_id': row['capture_id'], 'mask_area_px': 100,
                       'mask_size_px': row['image_size_px'], 'mask_bbox_xyxy_px': row['bbox_xyxy_px']})
    parents = {'schema': 'y92.parent_facade_proposals.v1',
               'camera_receipt_sha256': hashlib.sha256(camera_path.read_bytes()).hexdigest(),
               'parents': [parent]}
    paths = dict(camera_receipt_path=camera_path,
                capture_index_path=write('captures.json', {'schema': 'y92.capture_source_index.v1', 'captures': captures}),
                tracks_path=write('tracks.json', {'tracks': [{'track_id': 't1', 'frames': frames,
                                  'semantic_candidates': ['facade_window']}]}),
                masks_path=write('masks.json', {}), parent_facades_path=write('parents.json', parents))
    tracks = json.loads(paths['tracks_path'].read_text())
    tracks['inputs'] = {key: {'sha256': hashlib.sha256(paths[path].read_bytes()).hexdigest()}
                        for key, path in [('capture_manifest', 'capture_index_path'), ('masks', 'masks_path')]}
    paths['tracks_path'].write_text(json.dumps(tracks))
    return paths


def test_batch_uses_parent_and_retains_ambiguous_alternatives(tmp_path):
    paths = write_inputs(tmp_path)
    result = build_batch(**paths)
    assert result['counts']['geometry_gate_passed'] == 1
    assert result['surfaces'][0]['selected_parent_id'] == 'way/1:edge/0'
    assert result['counts']['canonical_features'] == 0
    document = json.loads(paths['parent_facades_path'].read_text())
    second = copy.deepcopy(document['parents'][0]); second['parent_id'] = 'way/2:edge/0'
    document['parents'].append(second)
    paths['parent_facades_path'].write_text(json.dumps(document))
    result = build_batch(**paths)
    assert result['counts']['geometry_gate_passed'] == 0
    assert result['surfaces'][0]['gate']['failed'] == ['ambiguous_parent']
    assert len(result['surfaces'][0]['parent_candidates']) == 2


def test_parent_receipt_cannot_be_reused_with_changed_cameras(tmp_path):
    paths = write_inputs(tmp_path)
    paths['camera_receipt_path'].write_text(paths['camera_receipt_path'].read_text() + ' ')
    with pytest.raises(ValueError, match='camera frame differs'):
        build_batch(**paths)


def test_changed_tracking_masks_are_rejected(tmp_path):
    paths = write_inputs(tmp_path)
    paths['masks_path'].write_text('{"different":"mask"}')
    with pytest.raises(ValueError, match='tracked region input changed: masks'):
        build_batch(**paths)
