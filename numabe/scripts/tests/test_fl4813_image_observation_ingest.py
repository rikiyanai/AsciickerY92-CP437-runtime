import json
from pathlib import Path

import pytest

from scripts.pipeline.world_ir_ingest_image_observations import (
    build_document, digest, ingest_configured_corpora,
)


def corpus(tmp_path):
    def write(name, data):
        p = tmp_path / name
        p.write_text(json.dumps(data))
        return p
    image = tmp_path / 'frame.jpg'; image.write_bytes(b'frozen-pixel-fixture')
    capture = {'capture_id': 'c1', 'local_path': image.name, 'sha256': digest(image)}
    index = write('captures.json', {'schema': 'y92.capture_source_index.v1', 'captures': [capture]})
    detection = {'observation_id': 'd1', 'capture_id': 'c1', 'capture_sha256': digest(image),
                 'image_size_px': [2, 2], 'detector_confidence': .9,
                 'semantic_candidates': ['facade_window']}
    detections = write('detections.json', {'schema': 'y92.bulk_object_observations.v1',
        'capture_manifest': {'sha256': digest(index)}, 'observations': [detection], 'model': {}})
    mask = {'object_observation_id': 'd1', 'capture_id': 'c1', 'mask_size_px': [2, 2],
            'segmentation_score': .8, 'mask_area_px': 1, 'foreground_runs': [[0, 1]]}
    masks = write('masks.json', {'schema': 'y92.bulk_mask_observations.v1',
        'capture_manifest': {'sha256': digest(index)}, 'detections': {'sha256': digest(detections)},
        'masks': [mask], 'model': {}})
    return index, detections, masks


def test_ingest_is_deterministic_evidence_without_canonical_geometry(tmp_path):
    paths = corpus(tmp_path)
    first = build_document(*paths, ir_id='test')
    first.save(tmp_path / 'first.json')
    build_document(*paths, ir_id='test').save(tmp_path / 'second.json')
    assert (tmp_path / 'first.json').read_bytes() == (tmp_path / 'second.json').read_bytes()
    assert len(first.observations) == 2
    assert first.features == first.representations == []
    assert all(row['geometry']['kind'] == 'none' for row in first.observations)


@pytest.mark.parametrize('failure', ['capture', 'detection', 'mask_identity', 'missing_mask'])
def test_ingest_refuses_broken_source_chain(tmp_path, failure):
    index, detections, masks = corpus(tmp_path)
    if failure == 'capture':
        (tmp_path / 'frame.jpg').write_bytes(b'changed')
    elif failure == 'detection':
        detections.write_text(detections.read_text() + ' ')
    else:
        x = json.loads(masks.read_text())
        if failure == 'mask_identity': x['masks'][0]['capture_id'] = 'wrong'
        else: x['masks'] = []
        masks.write_text(json.dumps(x))
    with pytest.raises(ValueError):
        build_document(index, detections, masks, ir_id='test')


def test_e2e_corpus_configuration_consumes_all_declared_sources(tmp_path):
    paths = corpus(tmp_path)
    config = tmp_path / 'requirements.json'
    config.write_text(json.dumps({'fl4813_image_corpora': [dict(
        id='route', capture_index=paths[0].name, detections=paths[1].name, masks=paths[2].name)]}))
    receipts = ingest_configured_corpora(config, tmp_path / 'out')
    assert receipts[0]['source_observations'] == 2
    assert receipts[0]['canonical_features'] == 0
    assert digest(Path(receipts[0]['world_ir_path'])) == receipts[0]['sha256']


def test_surface_evidence_identity_does_not_depend_on_checkout_path():
    from scripts.pipeline.world_ir_ingest_image_observations import surface_evidence
    first = {'track_id': 't1', 'inputs': {'masks': {'sha256': 'abc', 'path': '/checkout/a/masks.json'}}}
    second = {'track_id': 't1', 'inputs': {'masks': {'sha256': 'abc', 'path': '/checkout/b/masks.json'}}}
    assert surface_evidence(first) == surface_evidence(second)
    assert first['inputs']['masks']['path'] == '/checkout/a/masks.json'
