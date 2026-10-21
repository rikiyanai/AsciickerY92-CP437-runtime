"""FL-4812 learned geometry experiment; outputs evidence, never world authority.

This optional experiment stage is called through the existing E2E runner.
Frozen input order and pixel digests let backends use the same corpus.
Predicted camera-relative geometry is never labelled accepted ENU truth.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.pipeline.capture_corpus import load_capture_corpus, sha256_file
import subprocess
import time

import numpy as np


digest = sha256_file


def depth_to_local_points(depth, intrinsics, extrinsics):
    """Unproject camera-Z depth using OpenCV world-to-camera R,t (FL-4812).

    The returned coordinates retain the model's unknown scale/world frame.
    This conversion never georegisters the proposal or creates sparse tracks.
    """
    depth = np.asarray(depth)
    v, u = np.indices(depth.shape)
    rays = np.stack([u, v, np.ones_like(u)], axis=-1) @ np.linalg.inv(intrinsics).T
    camera_points = rays * depth[..., None]
    return (camera_points - extrinsics[:, 3]) @ extrinsics[:, :3]


def capture_intrinsics(corpus):
    """Use declared perspective FOV; do not substitute a learned focal length."""
    matrices = []
    for row in corpus:
        fov = float(row.get('horizontal_fov_deg') or 0.)
        if not np.isfinite(fov) or not 0 < fov < 180:
            raise ValueError('capture has no usable perspective FOV prior')
        width, height = row['width'], row['height']
        focal = width / (2 * np.tan(np.deg2rad(fov) / 2))
        matrices.append([[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]])
    return np.asarray(matrices, dtype=np.float32)


def evaluate_sparse_reprojections(arrays, corpus, sparse):
    """Compare dense proposals with observed cross-image feature coordinates.

    This tests model-relative rays/depth without trusting either model scale
    or the sparse reconstruction's absolute registration. It grants no world
    acceptance. Only actual matched image observations are used as targets.
    """
    by_id = {row['capture_id']: (i, row) for i, row in enumerate(corpus)}
    height, width = arrays['depth'].shape[1:]
    errors, used_tracks, masked_samples = [], 0, 0
    for point in sparse['points']:
        observations = [row for row in point['observations']
                        if row['capture_id'] in by_id and row['reprojection_error_px'] <= 2.]
        if len(observations) < 2:
            continue
        used_tracks += 1
        for source in observations:
            i, row = by_id[source['capture_id']]
            pixel = np.asarray(source['source_image_pixel_px']) * [width / row['width'], height / row['height']]
            u, v = pixel
            if not (0 <= u < width - 1 and 0 <= v < height - 1):
                continue
            x, y = int(u), int(v)
            if 'valid_mask' in arrays and not arrays['valid_mask'][i, y:y+2, x:x+2].all():
                masked_samples += 1
                continue
            dx, dy = u - x, v - y
            depth = arrays['depth'][i]
            z = ((1-dx)*(1-dy)*depth[y,x] + dx*(1-dy)*depth[y,x+1]
                 + (1-dx)*dy*depth[y+1,x] + dx*dy*depth[y+1,x+1])
            local = np.linalg.solve(arrays['intrinsics'][i], [u, v, 1.]) * z
            e = arrays['extrinsics'][i]
            world = (local - e[:,3]) @ e[:,:3]
            for target in observations:
                if target['capture_id'] == source['capture_id']:
                    continue
                j, target_row = by_id[target['capture_id']]
                target_e = arrays['extrinsics'][j]
                projected = arrays['intrinsics'][j] @ (target_e[:,:3] @ world + target_e[:,3])
                if projected[2] <= 0:
                    errors.append(float('inf'))
                    continue
                target_px = np.asarray(target['source_image_pixel_px']) * [width/target_row['width'], height/target_row['height']]
                errors.append(float(np.linalg.norm(projected[:2]/projected[2] - target_px)))
    finite = [e for e in errors if np.isfinite(e)]
    return {'matched_tracks': used_tracks, 'directed_reprojections': len(errors),
            'masked_source_samples': masked_samples,
            'behind_camera': len(errors)-len(finite),
            'median_error_px_finite': float(np.median(finite)) if finite else None,
            'p90_error_px_finite': float(np.quantile(finite,.9)) if finite else None,
            'fraction_within_4px': sum(e <= 4 for e in errors)/len(errors) if errors else None,
            'evaluation_frame': 'model_local_no_absolute_alignment',
            'world_ir_promotion_ready': False}


def mapanything_arrays(predictions):
    """Normalize the documented camera-to-world output without losing masks."""
    def array(value):
        if hasattr(value, 'detach'):
            value = value.detach().float().cpu().numpy()
        return np.asarray(value)
    poses = np.stack([array(p['camera_poses'])[0] for p in predictions])
    if not np.allclose(poses[:, 3], [0, 0, 0, 1]):
        raise ValueError('invalid homogeneous camera-to-world pose')
    return {
        'depth': np.stack([array(p['depth_z'])[0, ..., 0] for p in predictions]),
        'conf': np.stack([array(p['conf'])[0] for p in predictions]),
        'intrinsics': np.stack([array(p['intrinsics'])[0] for p in predictions]),
        'extrinsics': np.linalg.inv(poses)[:, :3, :],
        'valid_mask': np.stack([array(p['mask'])[0, ..., 0] > 0 for p in predictions]),
    }


def infer_mapanything(checkpoint, corpus, config, device, resolution):
    """Use upstream preprocessing and optional calibration, retaining validity."""
    from PIL import Image
    from mapanything.models import MapAnything
    from mapanything.utils.image import preprocess_inputs
    # The bounded comparison uses square captures; no unrecorded crop is allowed.
    if any(row['width'] != row['height'] for row in corpus):
        raise ValueError('MapAnything comparison currently requires square captures')
    inputs = []
    matrices = capture_intrinsics(corpus) if config.get('use_capture_intrinsics') else None
    for i, row in enumerate(corpus):
        with Image.open(row['path']) as image:
            view = {'img': np.array(image.convert('RGB'))}
        if matrices is not None:
            view['intrinsics'] = matrices[i]
        inputs.append(view)
    views = preprocess_inputs(inputs, resize_mode='fixed_size', size=(resolution, resolution))
    model = MapAnything.from_pretrained(str(checkpoint), local_files_only=True).to(device).eval()
    predictions = model.infer(views, memory_efficient_inference=True, minibatch_size=1,
                              use_amp=True, amp_dtype='bf16', apply_mask=True,
                              mask_edges=True, apply_confidence_mask=False,
                              use_multiview_confidence=False)
    return mapanything_arrays(predictions)


def select_corpus(index_path, component_id=None, limit=None):
    """Select the declared reconstruction component without changing its order."""
    index_path = Path(index_path)
    document, source_rows = load_capture_corpus(index_path)
    if document.get('schema') != 'y92.capture_source_index.v1':
        raise ValueError('unsupported capture index')
    component_id = component_id or document['default_reconstruction_component']
    component = next(c for c in document['reconstruction_components']
                     if c['component_id'] == component_id)
    ids = component['capture_ids']
    if limit is not None:
        if limit < 2:
            raise ValueError('a multiview experiment requires at least two images')
        ids = ids[:limit]
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise ValueError('component must contain distinct image identities')
    captures = {c['capture_id']: c for c in source_rows}
    rows = []
    for cid in ids:
        source = captures[cid]
        path = (index_path.parent / source['local_path']).resolve()
        if not source.get('retention_permitted') or not source.get('license'):
            raise ValueError(f'capture has no retention/license receipt: {cid}')
        if source.get('projection_model') != 'rectilinear':
            raise ValueError(f'capture is not a calibrated perspective image: {cid}')
        actual = digest(path)
        if actual != source['sha256'].removeprefix('sha256:'):
            raise ValueError(f'source pixels changed: {cid}')
        from PIL import Image
        with Image.open(path) as im:
            if im.size != (source['width'], source['height']):
                raise ValueError(f'image dimensions differ from capture receipt: {cid}')
        rows.append({'capture_id': cid, 'path': str(path), 'sha256': actual,
                     'license': source['license'], 'attribution': source.get('attribution'),
                     'width': source['width'], 'height': source['height'],
                     'horizontal_fov_deg': source.get('horizontal_fov_deg')})
        if source['width'] <= 0 or source['height'] <= 0:
            raise ValueError('invalid image dimensions')
    return component_id, rows


def validate_prediction(depth, confidence, extrinsics, intrinsics, count, valid_mask=None):
    """Reject corrupt geometry without mistaking numeric validity for acceptance."""
    if depth.ndim != 3 or depth.shape[0] != count or min(depth.shape[1:]) < 2:
        raise ValueError('depth shape does not match corpus')
    if confidence.shape != depth.shape:
        raise ValueError('confidence shape does not match depth')
    if extrinsics.shape != (count, 3, 4) or intrinsics.shape != (count, 3, 3):
        raise ValueError('camera shape does not match corpus')
    if not all(np.isfinite(a).all() for a in (depth, confidence, extrinsics, intrinsics)):
        raise ValueError('nonfinite model prediction')
    if valid_mask is None:
        valid_mask = np.ones_like(depth, dtype=bool)
    if valid_mask.shape != depth.shape or valid_mask.dtype != np.bool_:
        raise ValueError('invalid depth validity mask')
    if not valid_mask.any() or not (depth[valid_mask] > 0).all() or not (intrinsics[:, (0, 1), (0, 1)] > 0).all():
        raise ValueError('nonpositive depth or focal length')
    rotations = extrinsics[:, :, :3]
    if not np.allclose(rotations @ rotations.transpose(0, 2, 1), np.eye(3), atol=1e-3):
        raise ValueError('camera rotations are not orthonormal')
    if not np.allclose(np.linalg.det(rotations), 1, atol=1e-3):
        raise ValueError('camera rotations contain a reflection')
    if not np.allclose(intrinsics[:, 2, :], [0, 0, 1], atol=1e-6):
        raise ValueError('intrinsics are not calibrated pixel coordinates')


def run_proposal(config_path, output_dir):
    """Execute a pinned checkpoint through its published inference API."""
    config_path, output_dir = Path(config_path).resolve(), Path(output_dir).resolve()
    if output_dir.exists():
        raise ValueError('proposal output directory already exists; preserve prior experiment')
    config = json.loads(config_path.read_text())
    if config.get('backend') not in ('depth_anything_3', 'map_anything'):
        raise ValueError('backend not implemented; do not silently substitute a model')
    required = ('code_root', 'code_revision', 'checkpoint_dir', 'checkpoint_revision',
                'checkpoint_sha256', 'config_sha256', 'model_id', 'license', 'capture_index')
    for key in required:
        if not config.get(key):
            raise ValueError(f'missing pinned proposal input: {key}')
    def resolve(value):
        return (config_path.parent / value).resolve()
    code_root = resolve(config['code_root'])
    revision = subprocess.check_output(['git', '-C', str(code_root), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(code_root), 'status', '--porcelain'], text=True)
    if revision != config['code_revision'] or dirty.strip():
        raise ValueError('model implementation differs from pinned clean revision')
    checkpoint = resolve(config['checkpoint_dir'])
    for name, expected in [('model.safetensors', config['checkpoint_sha256']),
                           ('config.json', config['config_sha256'])]:
        if digest(checkpoint / name) != expected:
            raise ValueError(f'checkpoint changed: {name}')
    component, corpus = select_corpus(resolve(config['capture_index']),
                                      config.get('component_id'), config.get('limit'))
    import torch
    if config['backend'] == 'depth_anything_3':
        import depth_anything_3.api as api
    else:
        import mapanything.models as api
    if not Path(api.__file__).resolve().is_relative_to(code_root):
        raise ValueError('imported backend is not the pinned implementation')
    device = config.get('device', 'cuda')
    resolution = int(config.get('process_res', 504))
    if resolution < 126 or resolution > 504:
        raise ValueError('experiment resolution must be between 126 and 504')
    torch.manual_seed(int(config.get('seed', 4812)))
    output_dir.mkdir(parents=True)
    receipt = {'schema': 'y92.geometry_proposal.v1', 'backend': config['backend'],
               'evidence_state': 'unaccepted_model_proposal', 'world_ir_promotion_ready': False,
               'coordinate_frame': 'model_local_opencv_world_to_camera',
               'depth_domain': ('linear_camera_z_relative' if config['backend'] == 'depth_anything_3'
                                else 'linear_camera_z_predicted_metric_unverified'),
               'component_id': component, 'captures': corpus, 'configuration': config,
               'versions': {'torch': torch.__version__, 'numpy': np.__version__},
               'confidence_semantics': 'backend_raw_not_probability',
               'preprocessing': {'method': 'upper_bound_resize', 'crop': False},
               'counts': {'input_frames': len(corpus)}, 'stage': 'started'}
    receipt_path = output_dir / 'proposal_receipt.json'
    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + '\n')
    save()
    started = time.monotonic()
    try:
        if device == 'cuda':
            torch.cuda.reset_peak_memory_stats()
        if config['backend'] == 'depth_anything_3':
            model = api.DepthAnything3.from_pretrained(str(checkpoint), local_files_only=True).to(device).eval()
            input_intrinsics = capture_intrinsics(corpus) if config.get('use_capture_intrinsics') else None
            prediction = model.inference([row['path'] for row in corpus], process_res=resolution,
                                         intrinsics=input_intrinsics,
                                         process_res_method='upper_bound_resize')
            arrays = {name: np.asarray(getattr(prediction, name))
                      for name in ('depth', 'conf', 'extrinsics', 'intrinsics')}
            if input_intrinsics is not None:
                receipt['conditioning_warning'] = 'Pinned DA3 requires extrinsics too; intrinsics-only input is ignored upstream'
        else:
            receipt['preprocessing']['method'] = 'fixed_size_square_no_crop'
            arrays = infer_mapanything(checkpoint, corpus, config, device, resolution)
        validate_prediction(arrays['depth'], arrays['conf'], arrays['extrinsics'],
                            arrays['intrinsics'], len(corpus), arrays.get('valid_mask'))
        path = output_dir / 'geometry.npz'
        np.savez_compressed(path, **arrays)
        receipt['outputs'] = {'geometry': {'path': path.name, 'sha256': digest(path)}}
        receipt['counts'].update({'predicted_frames': len(corpus),
                                  'depth_pixels': int(arrays['depth'].size)})
        receipt['processed_dimensions_px'] = list(arrays['depth'].shape[1:][::-1])
        h, w = arrays['depth'].shape[1:]
        receipt['preprocessing']['source_to_processed_scale_xy'] = [
            [w / row['width'], h / row['height']] for row in corpus]
        if device == 'cuda':
            receipt['peak_allocated_gpu_bytes'] = torch.cuda.max_memory_allocated()
        receipt['stage'] = 'executed_unaccepted_proposal'
        receipt['remaining_checks'] = ['cross_view_consistency', 'gravity', 'OSM_DEM_alignment',
                                        'held_out_reprojection', 'parent_facade_support']
    except Exception as exc:
        receipt.update(stage='failed', failure=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        receipt['elapsed_seconds'] = time.monotonic() - started
        save()
    return receipt
