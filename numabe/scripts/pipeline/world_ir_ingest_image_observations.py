#!/usr/bin/env python3
"""Ingest frozen image-space detections and masks as World IR evidence (FL-4813)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.capture_corpus import load_capture_corpus
from scripts.pipeline.world_ir import WorldIRStore, schema as S


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_document(capture_index: Path, detections_path: Path, masks_path: Path,
                   *, ir_id: str) -> WorldIRStore:
    """Preserve source coordinates; this function never assigns a world position."""
    _, captures = load_capture_corpus(capture_index)
    captures_by_id = {row['capture_id']: row for row in captures}
    detections = json.loads(detections_path.read_text())
    masks = json.loads(masks_path.read_text())
    if detections.get('schema') != 'y92.bulk_object_observations.v1':
        raise ValueError('unsupported detection schema')
    if masks.get('schema') != 'y92.bulk_mask_observations.v1':
        raise ValueError('unsupported mask schema')
    capture_digest = digest(capture_index)
    detection_digest, mask_digest = digest(detections_path), digest(masks_path)
    if detections['capture_manifest']['sha256'] != capture_digest:
        raise ValueError('detections refer to a different capture corpus')
    if masks['detections']['sha256'] != detection_digest:
        raise ValueError('masks refer to different detections')
    if masks['capture_manifest']['sha256'] != capture_digest:
        raise ValueError('masks refer to a different capture corpus')
    detections_by_id = {row['observation_id']: row for row in detections['observations']}
    masks_by_id = {row['object_observation_id']: row for row in masks['masks']}
    if len(detections_by_id) != len(detections['observations']):
        raise ValueError('duplicate detection IDs')
    if len(masks_by_id) != len(masks['masks']):
        raise ValueError('duplicate mask detection IDs')
    if set(detections_by_id) != set(masks_by_id):
        raise ValueError('every detection must resolve to exactly one mask')

    store = WorldIRStore.new(ir_id, notes=(
        'Frozen image evidence only. No world geometry, physical identity, '
        'collision authority or runtime representation is asserted.'
    ))
    for capture_id, capture in sorted(captures_by_id.items()):
        path = capture_index.parent / capture['local_path']
        if digest(path) != capture['sha256']:
            raise ValueError(f'capture digest mismatch: {capture_id}')
        store.add_observation(S.source_observation(
            observation_id='capture:' + capture_id, source_id=capture_id,
            source_kind='imagery', observed_at=str(capture.get('timestamp') or 'unknown'),
            digest='sha256:' + capture['sha256'], geometry=S.geometry_none(),
            attributes={'capture': capture, 'camera_pose_state': 'source_prior_only'},
        ))
    for detection_id, detection in sorted(detections_by_id.items()):
        capture_id = detection['capture_id']
        capture = captures_by_id.get(capture_id)
        if capture is None or detection['capture_sha256'] != capture['sha256']:
            raise ValueError(f'detection source mismatch: {detection_id}')
        mask = masks_by_id[detection_id]
        if mask['capture_id'] != capture_id:
            raise ValueError(f'mask capture mismatch: {detection_id}')
        if mask.get('capture_sha256', capture['sha256']) != capture['sha256']:
            raise ValueError(f'mask digest mismatch: {detection_id}')
        if mask['mask_size_px'] != detection['image_size_px']:
            raise ValueError(f'mask dimensions mismatch: {detection_id}')
        store.add_observation(S.source_observation(
            observation_id=detection_id, source_id=capture_id, source_kind='imagery',
            observed_at=str(capture.get('timestamp') or 'unknown'),
            digest='sha256:' + capture['sha256'], geometry=S.geometry_none(),
            attributes={
                'capture_observation_id': 'capture:' + capture_id,
                'image_region': detection, 'segmentation': mask,
                'detector_model': detections['model'], 'segmentation_model': masks['model'],
                'detection_artifact_sha256': detection_digest,
                'mask_artifact_sha256': mask_digest,
                'world_localization_state': 'unresolved',
            },
            quality={'detector_score': detection['detector_confidence'],
                     'segmentation_score': mask['segmentation_score'],
                     'physical_identity_reviewed': False},
        ))
    findings = store.validate()
    if findings:
        raise ValueError(f'image observation IR validation failed: {findings[:3]}')
    return store


def surface_evidence(surface):
    """Keep source identities stable when the frozen bundle moves on disk."""
    evidence = dict(surface)
    evidence['inputs'] = {key: {'sha256': value['sha256']}
                          for key, value in surface['inputs'].items()}
    payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return evidence, hashlib.sha256(payload).hexdigest()


def ingest_configured_corpora(requirements_path: Path, output_root: Path) -> list[dict]:
    """Called by the single E2E owner; corpus IDs and paths are source configuration."""
    requirements = json.loads(requirements_path.read_text())
    receipts, seen = [], set()
    for corpus in requirements.get('fl4813_image_corpora', []):
        corpus_id = corpus['id']
        if not corpus_id or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in corpus_id):
            raise ValueError('image corpus ID must be a portable slug')
        if corpus_id in seen:
            raise ValueError('duplicate image corpus ID')
        seen.add(corpus_id)
        inputs = {key: requirements_path.parent / corpus[key]
                  for key in ('capture_index', 'detections', 'masks')}
        store = build_document(inputs['capture_index'], inputs['detections'], inputs['masks'],
                               ir_id='image_observations_' + corpus_id)
        planar_receipt = None
        if corpus.get('planar_tracks') or corpus.get('camera_registration'):
            if not corpus.get('planar_tracks') or not corpus.get('camera_registration'):
                raise ValueError('planar extraction requires both tracks and camera registration')
            from scripts.pipeline.reconstruct_planar_tracks import build_batch
            tracks_path = requirements_path.parent / corpus['planar_tracks']
            camera_path = requirements_path.parent / corpus['camera_registration']
            parent_path = (requirements_path.parent / corpus['parent_facades']
                           if corpus.get('parent_facades') else None)
            if corpus.get('frozen_planar_candidates'):
                # FL-4814: a style build consumes frozen observations; it must
                # not rerun the same rejected sparse-plane experiment each time.
                frozen = requirements_path.parent / corpus['frozen_planar_candidates']
                planar = json.loads(frozen.read_text())
                if planar.get('schema') != 'y92.planar_track_reconstruction_batch.v1':
                    raise ValueError('unsupported frozen planar schema')
                expected = {'tracks':tracks_path,'masks':inputs['masks'],
                            'camera_receipt':camera_path,'capture_index':inputs['capture_index']}
                if parent_path is not None:
                    expected['parent_facades'] = parent_path
                elif 'parent_facades' in planar['inputs']:
                    raise ValueError('frozen parent projection requires configured parent facades')
                for key, source in expected.items():
                    if planar['inputs'][key]['sha256'] != digest(source):
                        raise ValueError(f'frozen planar input changed: {key}')
                for surface in planar['surfaces']:
                    for key, item in surface['inputs'].items():
                        source = expected.get(key, requirements_path.parent / Path(item['path']).name)
                        if item['sha256'] != digest(source):
                            raise ValueError(f'frozen surface input changed: {key}')
            else:
                planar = build_batch(tracks_path=tracks_path, masks_path=inputs['masks'],
                                     camera_receipt_path=camera_path, capture_index_path=inputs['capture_index'],
                                     parent_facades_path=parent_path)
            planar_path = output_root / (corpus_id + '.planar_candidates.json')
            planar_path.parent.mkdir(parents=True, exist_ok=True)
            planar_path.write_text(json.dumps(planar, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n')
            planar_digest = digest(planar_path)
            for surface in planar['surfaces']:
                evidence, evidence_digest = surface_evidence(surface)
                store.add_observation(S.source_observation(
                    observation_id='surface:' + surface['track_id'],
                    source_id=('parent_facade:' if parent_path else 'shared_sfm:') + corpus_id,
                    source_kind='imagery', observed_at='unknown',
                    geometry=S.geometry_none(), digest='sha256:' + evidence_digest,
                    attributes={'surface_candidate': evidence, 'world_localization_state': 'unresolved'},
                ))
            planar_receipt = {'path': str(planar_path.resolve()), 'sha256': planar_digest,
                              'counts': planar['counts']}
        path = output_root / (corpus_id + '.world_ir.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        store.save(path)
        reloaded = WorldIRStore.load(path)
        if reloaded.validate() or reloaded.features or reloaded.representations:
            raise ValueError('image ingest emitted invalid or promoted world content')
        receipts.append({'corpus_id': corpus_id, 'world_ir_path': str(path.resolve()),
                         'sha256': digest(path), 'source_observations': len(reloaded.observations),
                         'canonical_features': 0, 'generated_representations': 0,
                         'planar_reconstruction': planar_receipt,
                         'inputs': {key: {'path': str(p), 'sha256': digest(p)} for key, p in inputs.items()}})
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requirements', type=Path, required=True)
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    receipts = ingest_configured_corpora(args.requirements, args.out_dir)
    print(json.dumps(receipts, ensure_ascii=False))


if __name__ == '__main__':
    main()
