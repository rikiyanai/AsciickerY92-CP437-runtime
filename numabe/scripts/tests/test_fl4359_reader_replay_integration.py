from pathlib import Path
import sys
import numpy as np
import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))
from fl4359_actual_field import ActualFieldError, ActualFieldFrame
from fl4359_postprocess_trace import (
    STORE_CONTRACT_SCHEMA, STORE_OPERATOR, PostprocessTrace,
)


def frame_with_trace(tmp_path):
    words = np.zeros(2252 * 22, dtype='<u4')
    height, width = 13, 32
    blur = words[:height * 10].reshape(height, 10)
    blur[:, :3] = np.array([.37123457, 1.3, .15123457], dtype='<f4').view('<u4')
    offsets = np.array([-3, -1, 0, 1, 3, -3, -1, 0, 1, 3, 0, 0], dtype='i1').view('<u4')
    blur[:, 3:6] = offsets
    blur[:, 6] = 0x80000007
    blur[:, 9] = 17
    present = words[2252 * 10:2252 * 10 + height * 12].reshape(height, 12)
    present[:, 4] = 17
    present[:, 5] = np.arange(1, height + 1)
    present[:, 10] = 0x8000000e
    present[:, 11] = np.arange(height, dtype=np.uint32) << 16
    operators = {
        name: {
            'status': 'dispatched', 'render_frame_index': 17,
            'source': {'width': width, 'height': height},
            'destination': {'width': width, 'height': height},
        }
        for name in ('blur', 'present')
    }
    import hashlib
    raw = words.tobytes()
    (tmp_path / 'trace.bin').write_bytes(raw)
    receipt = {
        'status': 'valid', 'schema': 'fl4784.postprocess_operator_trace.v1',
        'frame_index': 17, 'request_id': 8, 'sequence_frame': 0,
        'raw_path': 'trace.bin', 'bytes': len(raw), 'operators': operators,
        'raw_sha256': hashlib.sha256(raw).hexdigest(),
    }
    trace = PostprocessTrace(receipt, tmp_path, frame_index=17,
                             request_id=8, sequence_frame=0)
    frame = ActualFieldFrame(
        path=tmp_path / 'field.bin', metadata={}, fields={
            'final_rgb': np.full((height, width, 3), .25, dtype='<f4'),
        }, field_size_px=(width, height), logical_target_size=(width, height),
        presentation_size=(width, height), cell_px=(1, 1), grid_cells=(1, 1),
        capture_id='test', actual_frame_index=17, sequence_frame=0,
        frame_id=17, request_id=8, profile='test', projection='ortho',
        policy={}, source_eligible=True, atlas_eligible=True,
        composite_eligible=True, postprocess_trace=trace,
    )
    return frame, trace


def contract():
    return {
        'schema': STORE_CONTRACT_SCHEMA, 'status': 'verified', 'backend': 'metal',
        'operator': STORE_OPERATOR,
        'engine': {'version': '4.8.dev5.fixture', 'binary_sha256': 'a' * 64},
        'device': {'vendor': 'Apple', 'family': 'Apple9', 'name': 'M3 Pro'},
        'evidence': {
            'status': 'PASS', 'fixture': 'fl4784.metal_store_discriminator',
            'witness': 'packHalf2x16', 'texture_readback': 'texture_get_data',
            'sidecar_readback': 'storage_buffer_get_data', 'diagnostic_rows': 10,
            'production_pixels': 128, 'fixture_source_sha256': 'b' * 64,
        },
    }


def runtime_identity():
    value = contract()
    return {
        'backend': value['backend'],
        'engine': value['engine'].copy(),
        'device': value['device'].copy(),
    }


def test_frame_exact_replay_is_fail_closed_and_live_connected(tmp_path):
    frame, trace = frame_with_trace(tmp_path)
    try:
        assert frame.postprocess_sources(0, 0)['center'] == (0, 0)
        with pytest.raises(ActualFieldError, match='store contract missing'):
            frame.replay_postprocess_exact()
        chunks = list(frame.replay_postprocess_exact(
            chunk_rows=4, store_contract=contract(),
            runtime_identity=runtime_identity()))
        assert sum(chunk.blur_half.shape[0] for chunk in chunks) == frame.height
        assert sum(chunk.present_half.shape[0] for chunk in chunks) == frame.height
    finally:
        trace.close()
