import copy,hashlib,json
from pathlib import Path
import sys
import numpy as np
import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))
from fl4359_postprocess_trace import (
    PostprocessTrace, PostprocessTraceError, FRAME_BYTES, MAX_HEIGHT, SCHEMA,
    STORE_CONTRACT_SCHEMA, STORE_OPERATOR,
)

def fixture(tmp_path):
    w=np.zeros(FRAME_BYTES//4,dtype='<u4'); b=w[:130].reshape(13,10)
    b[:,:3]=np.array([.37123457,1.3,.15123457],dtype='<f4').view('<u4')
    offsets=np.array([-3,-1,0,1,3,-3,-1,0,1,3,0,0],dtype='i1').view('<u4')
    b[:,3:6]=offsets;b[:,6]=0x80000007;b[:,9]=17
    p=w[MAX_HEIGHT*10:MAX_HEIGHT*10+13*12].reshape(13,12)
    p[:,4]=17;p[:,5]=np.arange(1,14);p[:,10]=0x8000000e;p[:,11]=np.arange(13,dtype=np.uint32)<<16
    operators={name:{'status':'dispatched','render_frame_index':17,'source':{'width':32,'height':13},'destination':{'width':32,'height':13}} for name in ['blur','present']}
    r={'status':'valid','schema':SCHEMA,'frame_index':17,'request_id':8,'sequence_frame':0,'raw_path':'trace.bin','bytes':FRAME_BYTES,'operators':operators}
    save(tmp_path,w,r)
    return w,r

def save(path,w,r):
    (path/'trace.bin').write_bytes(w.tobytes());r['raw_sha256']=hashlib.sha256(w.tobytes()).hexdigest()

def load(tmp_path,r):
    return PostprocessTrace(r,tmp_path,frame_index=17,request_id=8,sequence_frame=0)

def test_ordered_cartesian_taps_and_precision(tmp_path):
    w,r=fixture(tmp_path);t=load(tmp_path,r);v=t.source_contributors(0,0)
    assert len(v['taps'])==25 and sum(q[2] for q in v['taps'])==1
    assert len(set((q[0],q[1]) for q in v['taps']))==9
    assert v['defocus']==float(np.float32(.37123457))
    assert v['defocus']!=float(np.float16(.37123457))
    assert v['taps'][0]==(0,0,1/256)
    assert v['taps'][-1]==(3,3,1/256)

@pytest.mark.parametrize('kind',['frame','request','sequence','hash','missing','overflow','row','nonfinite','absent'])
def test_rejects_broken_trace(tmp_path,kind):
    w,r=fixture(tmp_path)
    if kind=='frame':r['frame_index']=18
    elif kind=='request':r['request_id']=9
    elif kind=='sequence':r['sequence_frame']=1
    elif kind=='hash':r['raw_sha256']='0'*64
    elif kind=='absent':r['operators']['blur']={'status':'not_dispatched','render_frame_index':17,'reason':'dispatch_failed'}
    else:
        if kind=='missing':w[6]=0
        elif kind=='overflow':w[6]|=8
        elif kind=='row':w[MAX_HEIGHT*10+5]=2
        elif kind=='nonfinite':w[0]=np.array([np.nan],dtype='<f4').view('<u4')[0]
        save(tmp_path,w,r)
    with pytest.raises(PostprocessTraceError):load(tmp_path,r)

def test_absent_operators_explicit(tmp_path):
    w,r=fixture(tmp_path)
    for name,reason in [('blur','tilt_shift_disabled'),('present','presentation_compensation_inactive')]:
        r['operators'][name]={'status':'not_dispatched','render_frame_index':17,'reason':reason}
    assert load(tmp_path,r).source_contributors(10,12)['center']==(10,12)

def test_missing_file(tmp_path):
    w,r=fixture(tmp_path);(tmp_path/'trace.bin').unlink()
    with pytest.raises(PostprocessTraceError):load(tmp_path,r)

@pytest.mark.parametrize('kind', ['boolean_extent', 'float_frame', 'missing_source', 'nonfinite_shift', 'different_image', 'broken_chain'])
def test_rejects_invalid_operator_inputs(tmp_path, kind):
    words, receipt = fixture(tmp_path)
    if kind == 'boolean_extent':
        receipt['operators']['blur']['source']['width'] = True
    elif kind == 'float_frame':
        receipt['operators']['present']['render_frame_index'] = 17.0
    elif kind == 'missing_source':
        del receipt['operators']['present']['source']
    elif kind == 'nonfinite_shift':
        words[MAX_HEIGHT * 10 + 6] = np.array([np.nan], dtype='<f4').view('<u4')[0]
        save(tmp_path, words, receipt)
    elif kind == 'different_image':
        receipt['operators']['blur']['destination']['rid'] = 'RID(1)'
        receipt['operators']['present']['source']['rid'] = 'RID(2)'
    else:
        receipt['operators']['present']['source']['width'] = 31
    with pytest.raises(PostprocessTraceError):
        load(tmp_path, receipt)


def test_operator_context_releases_mapping(tmp_path):
    _, receipt = fixture(tmp_path)
    with load(tmp_path, receipt) as trace:
        mapping = trace.words._mmap
        assert not mapping.closed
    assert mapping.closed


def store_contract():
    return {
        "schema": STORE_CONTRACT_SCHEMA,
        "status": "verified",
        "backend": "metal",
        "operator": STORE_OPERATOR,
        "engine": {"version": "4.8.dev5.fixture", "binary_sha256": "a" * 64},
        "device": {"vendor": "Apple", "family": "Apple9", "name": "M3 Pro"},
        "evidence": {
            "status": "PASS",
            "fixture": "fl4784.metal_store_discriminator",
            "witness": "packHalf2x16",
            "texture_readback": "texture_get_data",
            "sidecar_readback": "storage_buffer_get_data",
            "diagnostic_rows": 10,
            "production_pixels": 128,
            "fixture_source_sha256": "b" * 64,
        },
    }


def runtime_identity():
    contract = store_contract()
    return {
        "backend": contract["backend"],
        "engine": contract["engine"].copy(),
        "device": contract["device"].copy(),
    }


def test_exact_replay_requires_contract_but_ordinary_reader_remains_available(tmp_path):
    _, receipt = fixture(tmp_path)
    trace = load(tmp_path, receipt)
    try:
        assert trace.source_contributors(0, 0)["center"] == (0, 0)
        with pytest.raises(PostprocessTraceError, match="store contract missing"):
            trace.replay_final_rgb(np.full((13, 32, 3), 0.25, dtype="<f4"))
    finally:
        trace.close()


def test_exact_replay_accepts_verified_contract_and_is_chunked(tmp_path):
    _, receipt = fixture(tmp_path)
    trace = load(tmp_path, receipt)
    try:
        source = np.full((13, 32, 3), 0.25, dtype="<f4")
        trace.store_contract = store_contract()
        trace.expected_runtime_identity = runtime_identity()
        chunks = list(trace.replay_final_rgb(source, chunk_rows=2))
        assert [chunk.start_y for chunk in chunks] == [0, 2, 4, 6, 8, 10, 12]
        assert all(chunk.blur_half is not None and chunk.present_half is not None
                   for chunk in chunks)
        assert all(chunk.blur_half.dtype == np.dtype("<u2")
                   for chunk in chunks)
        assert sum(chunk.blur_half.shape[0] for chunk in chunks) == 13
        assert sum(chunk.present_half.shape[0] for chunk in chunks) == 13
    finally:
        trace.close()


@pytest.mark.parametrize("mutation", [
    lambda c: c.pop("engine"),
    lambda c: c.__setitem__("backend", "vulkan"),
    lambda c: c["evidence"].__setitem__("status", "FAIL"),
    lambda c: c["evidence"].__setitem__("production_pixels", 127),
    lambda c: c["evidence"].__setitem__("fixture_source_sha256", "wrong"),
])
def test_exact_replay_rejects_unverified_contract(tmp_path, mutation):
    _, receipt = fixture(tmp_path)
    trace = load(tmp_path, receipt)
    try:
        contract = store_contract()
        mutation(contract)
        with pytest.raises(PostprocessTraceError, match="exact postprocess replay unavailable"):
            trace.replay_final_rgb(np.full((13, 32, 3), 0.25, dtype="<f4"),
                                   store_contract=contract,
                                   runtime_identity=runtime_identity())
    finally:
        trace.close()


def test_exact_replay_requires_capture_runtime_identity(tmp_path):
    _, receipt = fixture(tmp_path)
    trace = load(tmp_path, receipt)
    try:
        with pytest.raises(PostprocessTraceError, match='capture runtime identity required'):
            trace.replay_final_rgb(np.full((13, 32, 3), 0.25, dtype='<f4'),
                                   store_contract=store_contract())
    finally:
        trace.close()


def test_exact_replay_rejects_runtime_identity_mismatch(tmp_path):
    _, receipt = fixture(tmp_path)
    trace = load(tmp_path, receipt)
    try:
        bad = runtime_identity()
        bad['device']['name'] = 'other-device'
        with pytest.raises(PostprocessTraceError, match='runtime identity mismatch'):
            trace.replay_final_rgb(np.full((13, 32, 3), 0.25, dtype='<f4'),
                                   store_contract=store_contract(),
                                   runtime_identity=bad)
    finally:
        trace.close()


def test_replay_rejects_oversized_chunk_and_keeps_center_row(tmp_path):
    words, receipt = fixture(tmp_path)
    trace = load(tmp_path, receipt)
    try:
        with pytest.raises(PostprocessTraceError, match='bounded maximum'):
            trace.replay_final_rgb(np.full((13, 32, 3), 0.25, dtype='<f4'),
                                   chunk_rows=129,
                                   store_contract=store_contract(),
                                   runtime_identity=runtime_identity())
        blur_rows = trace._rows('blur', 13).copy()
        # Corrupt the recorded vertical offsets by removing zero. The center is
        # still required by _replay_blur_row and must remain in the row set.
        packed = np.array([1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 0, 0], dtype='i1').view('<u4')
        blur_rows[4, 3:6] = packed
        assert 4 in trace._blur_input_rows(4, blur_rows, 13)
    finally:
        trace.close()


@pytest.mark.parametrize("shift", [(0.0, 0.0), (2.25, -1.75), (-3.5, 2.0)])
@pytest.mark.parametrize("blur_enabled", [False, True])
def test_fused_trace_replays_one_store_with_shift_and_clipped_extent(tmp_path, shift, blur_enabled):
    from fl4359_postprocess_trace import FUSED_SCHEMA
    words, receipt = fixture(tmp_path)
    paint = words[:130].reshape(13, 10)
    if not blur_enabled:
        paint[:, 6] &= np.uint32(0xfffffffe)
    save(tmp_path, words, receipt)
    source = np.linspace(0.0, 1.0, 13 * 32 * 3, dtype="<f4").reshape(13, 32, 3)
    with load(tmp_path, receipt) as old:
        old.store_contract = store_contract()
        old.expected_runtime_identity = runtime_identity()
        reference = np.concatenate([chunk.blur_half for chunk in old.replay_final_rgb(source)])

    receipt["schema"] = FUSED_SCHEMA
    receipt["operators"]["blur"]["operator"] = "nearest_source_coordinate_then_defocus"
    receipt["operators"]["blur"]["destination"] = {"width": 17, "height": 9}
    receipt["operators"]["present"] = {
        "status": "not_dispatched", "reason": "fused_into_postprocess", "render_frame_index": 17}
    paint[:, 6] |= 16
    coords = words[MAX_HEIGHT * 10:MAX_HEIGHT * 10 + 13 * 12].reshape(13, 12)
    coords[:, 6:8] = np.array(shift, dtype="<f4").view("<u4")
    coords[:, 10] = 0x80000010
    xy = np.stack(np.meshgrid(np.arange(17), np.arange(9)), axis=-1).astype("<f4")
    size = np.array([32, 13], dtype="<f4")
    uv = np.clip((xy + np.float32(.5) - np.array(shift, dtype="<f4")) / size,
                 np.float32(.5) / size, np.float32(1) - np.float32(.5) / size)
    mapped = np.floor(uv * size).astype(np.int64)
    coords[:9, 11] = mapped[:, 0, 0] | (mapped[:, 0, 1] << 16)
    save(tmp_path, words, receipt)
    with load(tmp_path, receipt) as fused:
        fused.store_contract = store_contract()
        fused.expected_runtime_identity = runtime_identity()
        chunks = list(fused.replay_final_rgb(source, chunk_rows=3))
        assert all(chunk.present_half is None for chunk in chunks)
        actual = np.concatenate([chunk.blur_half for chunk in chunks])
        assert np.array_equal(actual, reference[mapped[:, :, 1], mapped[:, :, 0]])
        for x, y in ((0, 0), (16, 8)):
            facts = fused.source_contributors(x, y)
            assert facts["center"] == tuple(mapped[y, x])
            assert all(0 <= tx < 32 and 0 <= ty < 13 for tx, ty, _ in facts["taps"])


def test_fused_trace_rejects_a_separate_present_dispatch(tmp_path):
    from fl4359_postprocess_trace import FUSED_SCHEMA
    _, receipt = fixture(tmp_path)
    receipt["schema"] = FUSED_SCHEMA
    with pytest.raises(PostprocessTraceError, match="separate present"):
        load(tmp_path, receipt)
