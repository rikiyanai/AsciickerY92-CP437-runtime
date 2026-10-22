"""Bounded operator sidecar consumer used by the existing final-field reader."""
from __future__ import annotations
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
import numpy as np

SCHEMA = 'fl4784.postprocess_operator_trace.v1'
FUSED_SCHEMA = 'fl4784.postprocess_operator_trace.v2'
MAX_HEIGHT = 2252
MAX_WIDTH = 3600
MAX_CHUNK_ROWS = 128
FRAME_BYTES = MAX_HEIGHT * 22 * 4
REPLAY_CHANNELS = 4
REPLAY_WEIGHTS = (1, 4, 6, 4, 1)
REPLAY_REC709 = np.array((0.2126, 0.7152, 0.0722), dtype='<f4')
STORE_CONTRACT_SCHEMA = 'fl4784.rgba16f_store_contract.v1'
STORE_OPERATOR = 'float32_to_rgba16f_trunc_subnormal'
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')

class PostprocessTraceError(ValueError):
    pass


@dataclass(frozen=True)
class PostprocessReplayChunk:
    """One bounded row window of post-store RGBA16F replay bits.

    ``blur_half`` and ``present_half`` contain uint16 half-float bit patterns,
    not float values.  A disabled stage is represented by ``None``.  Arrays
    are limited to ``chunk_rows`` output rows so callers can replay a native
    field without creating a second full-resolution image.
    """

    start_y: int
    blur_half: np.ndarray | None
    present_half: np.ndarray | None

class PostprocessTrace:
    def __init__(self, receipt: dict, capture_dir: Path, *, frame_index: int,
                 request_id: int, sequence_frame: int,
                 expected_runtime_identity: Mapping | None = None):
        self.words = None
        self.expected_runtime_identity = expected_runtime_identity
        if not isinstance(receipt, Mapping) or receipt.get('status') != 'valid' or receipt.get('schema') not in (SCHEMA, FUSED_SCHEMA):
            raise PostprocessTraceError('postprocess sidecar is not valid')
        for key, expected in [('frame_index', frame_index), ('request_id', request_id),
                              ('sequence_frame', sequence_frame)]:
            if type(receipt.get(key)) is not int or receipt.get(key) != expected:
                raise PostprocessTraceError(f'postprocess {key} mismatch')
        relative = receipt.get('raw_path')
        if not isinstance(relative, str) or not relative:
            raise PostprocessTraceError('postprocess path missing')
        path = (capture_dir / relative).resolve()
        if not path.is_relative_to(capture_dir.resolve()) or not path.is_file():
            raise PostprocessTraceError('postprocess path invalid')
        if path.stat().st_size != FRAME_BYTES or receipt.get('bytes') != FRAME_BYTES:
            raise PostprocessTraceError('postprocess size mismatch')
        if hashlib.sha256(path.read_bytes()).hexdigest() != receipt.get('raw_sha256'):
            raise PostprocessTraceError('postprocess hash mismatch')
        self.fused = receipt['schema'] == FUSED_SCHEMA
        self.words = np.memmap(path, dtype='<u4', mode='r')
        self.operators = receipt.get('operators', {})
        # The producer may attach a verified engine/backend/device contract to
        # this same receipt. Callers can override it explicitly per replay.
        self.store_contract = receipt.get('store_contract')
        try:
            self._validate_operators(frame_index)
        except Exception:
            self.close()
            raise

    def _validate_operators(self, frame_index: int) -> None:
        if not isinstance(self.operators, Mapping):
            raise PostprocessTraceError('postprocess operators missing')
        for name in ('blur', 'present'):
            op = self.operators.get(name, {})
            if not isinstance(op, Mapping) or type(op.get('render_frame_index')) is not int or op.get('render_frame_index') != frame_index:
                raise PostprocessTraceError(f'{name} frame mismatch')
            if op.get('status') == 'not_dispatched':
                if self.fused:
                    allowed = {'direct_native_paint'} if name == 'blur' else {
                        'fused_into_postprocess' if self.operators.get('blur', {}).get('status') == 'dispatched'
                        else 'direct_native_paint'}
                else:
                    allowed = {'tilt_shift_disabled' if name == 'blur' else 'presentation_compensation_inactive'}
                if op.get('reason') not in allowed:
                    raise PostprocessTraceError(f'{name} unexpectedly skipped')
                continue
            if self.fused and name == 'present':
                raise PostprocessTraceError('fused trace cannot contain a separate present dispatch')
            if op.get('status') != 'dispatched':
                raise PostprocessTraceError(f'{name} unsupported')
            for extent_name in ('source', 'destination'):
                extent = op.get(extent_name)
                if not isinstance(extent, Mapping):
                    raise PostprocessTraceError(f'{name} extent missing')
                for key, maximum in [('width', MAX_WIDTH), ('height', MAX_HEIGHT)]:
                    value = extent.get(key)
                    if type(value) is not int or not 0 < value <= maximum:
                        raise PostprocessTraceError(f'{name} extent invalid')
            if name == 'blur' and not self.fused and self._extent(op['source']) != self._extent(op['destination']):
                raise PostprocessTraceError('blur source and destination differ')
            height = op['destination']['height']
            rows = self._rows(name, height)
            flags = rows[:, 6 if name == 'blur' else 10]
            frames = rows[:, 9 if name == 'blur' else 4]
            if np.any((flags & 0x80000000) == 0) or np.any(frames != frame_index):
                raise PostprocessTraceError(f'{name} unwritten or stale rows')
            if name == 'blur' and np.any(flags & 8):
                raise PostprocessTraceError('blur offset overflow')
            if name == 'present' and np.any(rows[:, 5] != np.arange(1, height + 1)):
                raise PostprocessTraceError('present row identity mismatch')
            if name == 'present':
                values = rows[:, [0, 1, 2, 3, 6, 7]].copy().view('<f4')
                if not np.all(np.isfinite(values)):
                    raise PostprocessTraceError('present nonfinite operator')
            if name == 'blur':
                values = rows[:, :3].copy().view('<f4')
                if not np.all(np.isfinite(values)) or np.any(values < 0):
                    raise PostprocessTraceError('blur nonfinite or negative operator')
                if np.any(values[:, (0, 2)] > 1):
                    raise PostprocessTraceError('blur mix outside unit interval')
        blur, present = self.operators['blur'], self.operators['present']
        if self.fused and blur['status'] == 'dispatched':
            if blur.get('operator') != 'nearest_source_coordinate_then_defocus':
                raise PostprocessTraceError('fused operator mismatch')
            height = blur['destination']['height']
            rows = self._rows('present', height)
            flags = self._rows('blur', height)[:, 6]
            if np.any((flags & 16) == 0) or np.any((rows[:, 10] & 0x80000010) != 0x80000010):
                raise PostprocessTraceError('fused coordinate rows missing')
            if np.any(rows[:, 4] != frame_index) or np.any(rows[:, 5] != np.arange(1, height + 1)):
                raise PostprocessTraceError('fused coordinate rows stale')
            if not np.all(np.isfinite(rows[:, 6:8].copy().view('<f4'))):
                raise PostprocessTraceError('fused nonfinite shift')
            for y in range(height):
                self._present_sources(y, rows,
                    source_width=blur['source']['width'], source_height=blur['source']['height'],
                    destination_width=1)
        if blur['status'] == present['status'] == 'dispatched':
            if self._extent(blur['destination']) != self._extent(present['source']):
                raise PostprocessTraceError('postprocess image chain extent mismatch')
            if blur['destination'].get('rid') != present['source'].get('rid'):
                raise PostprocessTraceError('postprocess image chain RID mismatch')

    @staticmethod
    def _extent(value: Mapping) -> tuple[int, int]:
        return value['width'], value['height']

    def close(self) -> None:
        if self.words is not None:
            self.words._mmap.close()
            self.words = None

    def __enter__(self) -> PostprocessTrace:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _rows(self, name: str, height: int):
        start, stride = (0, 10) if name == 'blur' else (MAX_HEIGHT * 10, 12)
        return self.words[start:start + height * stride].reshape(height, stride)

    @staticmethod
    def _word_float32(word: np.uint32 | int) -> np.float32:
        return np.array([word], dtype='<u4').view('<f4')[0]

    @staticmethod
    def _observed_metal_half_bits(value: np.ndarray) -> np.ndarray:
        """Observed pinned Metal RGBA16F store model, including subnormals."""
        values = np.asarray(value, dtype='<f4')
        words = values.view('<u4')
        sign = (words >> np.uint32(31)) & np.uint32(1)
        exponent = ((words >> np.uint32(23)) & np.uint32(0xff)).astype(np.int32)
        mantissa = words & np.uint32(0x7fffff)
        half = np.zeros(values.shape, dtype='<u2')
        normal = (exponent >= 113) & (exponent <= 142)
        half[normal] = (((exponent[normal] - 112) << 10) | (mantissa[normal] >> np.uint32(13))).astype('<u2')
        subnormal = (exponent >= 103) & (exponent <= 112)
        half[subnormal] = ((np.uint32(0x800000) | mantissa[subnormal]) >> (126 - exponent[subnormal]).astype(np.uint32)).astype('<u2')
        half[exponent >= 143] = np.uint16(0x7c00)
        half[(exponent == 255) & (mantissa != 0)] = np.uint16(0x7e00)
        return (half | (sign.astype('<u2') << np.uint16(15))).astype('<u2')

    @classmethod
    def _half_bits(cls, value: np.ndarray) -> np.ndarray:
        """Encode one RGBA16F store under the verified Metal contract."""
        return cls._observed_metal_half_bits(value)

    @classmethod
    def _quantized_input_row(cls, final_rgb: np.ndarray, y: int, width: int) -> np.ndarray:
        """Decode one pre-imageStore RGB row through the compositor's RGBA16F input."""
        row = np.asarray(final_rgb[y], dtype='<f4')
        if row.shape != (width, 3):
            raise PostprocessTraceError('final_rgb row shape mismatch')
        if not np.isfinite(row).all() or np.any(row < 0.0) or np.any(row > 1.0):
            raise PostprocessTraceError('final_rgb row is nonfinite or outside unit interval')
        # The compositor writes vec4(color, 1) into an rgba16f image.  Decode
        # the half row again before any blur arithmetic, exactly as texelFetch
        # sees it.  Only this row is materialized.
        quantized = cls._half_bits(row).view('<f2').astype('<f4')
        rgba = np.empty((width, REPLAY_CHANNELS), dtype='<f4')
        rgba[:, :3] = quantized
        rgba[:, 3] = np.float32(1.0)
        return rgba

    @staticmethod
    def _offsets(row: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        packed = np.asarray(row[3:6], dtype='<u4').view('<i1')
        return packed[:5].astype(np.int32), packed[5:10].astype(np.int32)

    def _blur_input_rows(self, y: int, blur_rows: np.ndarray, height: int) -> set[int]:
        row = blur_rows[y]
        if not int(row[6]) & 1:
            return {y}
        _, offsets_y = self._offsets(row)
        # The center texel is consumed separately by _replay_blur_row. Keep it
        # resident even if a malformed trace omits offset zero.
        rows = {y}
        rows.update(min(max(y + int(offset), 0), height - 1) for offset in offsets_y)
        return rows

    def _replay_blur_row(
        self,
        y: int,
        source_rows: Mapping[int, np.ndarray],
        blur_rows: np.ndarray,
        *,
        width: int,
        height: int,
        center_coordinates: tuple[np.ndarray, int] | None = None,
        source_width: int | None = None,
    ) -> np.ndarray:
        row = blur_rows[y]
        center_x, center_y = center_coordinates if center_coordinates is not None else (
            np.arange(width, dtype=np.int64), y)
        input_width = width if source_width is None else source_width
        center = source_rows[center_y][center_x]
        if not int(row[6]) & 1:
            return self._half_bits(center)

        offsets_x, offsets_y = self._offsets(row)
        accumulated = np.zeros((width, REPLAY_CHANNELS), dtype='<f4')
        for ky, offset_y in enumerate(offsets_y):
            source_y = min(max(center_y + int(offset_y), 0), height - 1)
            source = source_rows[source_y]
            for kx, offset_x in enumerate(offsets_x):
                source_x = np.clip(
                    center_x + int(offset_x), 0, input_width - 1)
                weight = np.float32(REPLAY_WEIGHTS[kx] * REPLAY_WEIGHTS[ky])
                # Keep the shader's ky/kx accumulation order.  The host uses
                # float32 operations here and reports any Metal FMA residuals
                # through the exact half-bit comparison; no tolerance is used.
                accumulated = np.float32(
                    accumulated + np.float32(source[source_x] * weight))
        blurred = np.float32(accumulated / np.float32(256.0))
        luma = np.float32(
            np.float32(blurred[:, 0] * REPLAY_REC709[0])
            + np.float32(blurred[:, 1] * REPLAY_REC709[1]))
        luma = np.float32(luma + np.float32(blurred[:, 2] * REPLAY_REC709[2]))
        desaturation = self._word_float32(row[2])
        grayscale = luma[:, None]
        one_minus_desaturation = np.float32(1.0 - desaturation)
        desaturated = np.float32(
            np.float32(blurred[:, :3] * one_minus_desaturation)
            + np.float32(grayscale * desaturation))
        defocus = self._word_float32(row[0])
        one_minus_defocus = np.float32(1.0 - defocus)
        result = np.empty_like(blurred)
        result[:, :3] = np.float32(
            np.float32(center[:, :3] * one_minus_defocus)
            + np.float32(desaturated * defocus))
        result[:, 3] = np.float32(
            np.float32(center[:, 3] * one_minus_defocus)
            + np.float32(blurred[:, 3] * defocus))
        return self._half_bits(result)

    def _present_sources(
        self,
        y: int,
        present_rows: np.ndarray,
        *,
        source_width: int,
        source_height: int,
        destination_width: int,
    ) -> tuple[np.ndarray, int]:
        row = present_rows[y]
        shift = row[6:8].copy().view('<f4')
        x = np.arange(destination_width, dtype='<f4')
        source_size = np.float32([source_width, source_height])
        destination = np.float32([x, np.full(destination_width, y, dtype='<f4')]).T
        raw_uv = (destination + np.float32(0.5) - shift) / source_size
        half_texel = np.float32(0.5) / source_size
        uv = np.minimum(np.maximum(raw_uv, half_texel), np.float32(1.0) - half_texel)
        source = np.floor(uv * source_size).astype(np.int64)
        source[:, 0] = np.clip(source[:, 0], 0, source_width - 1)
        source[:, 1] = np.clip(source[:, 1], 0, source_height - 1)
        observed = (int(row[11]) & 0xffff, int(row[11]) >> 16)
        if (int(source[0, 0]), int(source[0, 1])) != observed:
            raise PostprocessTraceError('present replay contradicts observed x=0 texel')
        return source[:, 0], int(source[0, 1])

    @staticmethod
    def _validate_store_contract(
        store_contract: Mapping | None,
        expected_runtime_identity: Mapping | None,
    ) -> None:
        """Require the contract to match the capture runtime identity."""
        if not isinstance(store_contract, Mapping):
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: store contract missing')
        if store_contract.get('schema') != STORE_CONTRACT_SCHEMA:
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: store contract schema unsupported')
        if store_contract.get('status') != 'verified':
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: store contract is not verified')
        if store_contract.get('backend') != 'metal':
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: backend is not Metal')
        if store_contract.get('operator') != STORE_OPERATOR:
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: store operator unsupported')
        engine = store_contract.get('engine')
        if not isinstance(engine, Mapping) or not isinstance(engine.get('version'), str) \
                or not engine.get('version').strip():
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: engine identity missing')
        if not isinstance(engine.get('binary_sha256'), str) \
                or SHA256_RE.fullmatch(engine['binary_sha256']) is None:
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: engine binary digest missing')
        device = store_contract.get('device')
        if not isinstance(device, Mapping) \
                or any(not isinstance(device.get(key), str) or not device[key].strip()
                       for key in ('vendor', 'family', 'name')):
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: device identity missing')
        evidence = store_contract.get('evidence')
        if not isinstance(evidence, Mapping) or evidence.get('status') != 'PASS':
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: discriminator evidence missing')
        if evidence.get('fixture') != 'fl4784.metal_store_discriminator':
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: discriminator fixture mismatch')
        if evidence.get('witness') != 'packHalf2x16' \
                or evidence.get('texture_readback') != 'texture_get_data' \
                or evidence.get('sidecar_readback') != 'storage_buffer_get_data':
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: raw witness path mismatch')
        diagnostic_rows = evidence.get('diagnostic_rows')
        production_pixels = evidence.get('production_pixels')
        if (isinstance(diagnostic_rows, bool) or not isinstance(diagnostic_rows, int)
                or diagnostic_rows < 10 or isinstance(production_pixels, bool)
                or not isinstance(production_pixels, int) or production_pixels < 128):
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: discriminator coverage insufficient')
        source_digest = evidence.get('fixture_source_sha256')
        if not isinstance(source_digest, str) or SHA256_RE.fullmatch(source_digest) is None:
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: fixture source digest missing')
        if not isinstance(expected_runtime_identity, Mapping):
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: capture runtime identity required')
        expected_engine = expected_runtime_identity.get('engine')
        expected_device = expected_runtime_identity.get('device')
        expected_backend = expected_runtime_identity.get('backend')
        if expected_backend != 'metal' or not isinstance(expected_engine, Mapping) \
                or not isinstance(expected_device, Mapping):
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: expected runtime identity invalid')
        identity_keys = ('version', 'binary_sha256')
        device_keys = ('vendor', 'family', 'name')
        if any(not isinstance(expected_engine.get(key), str) or not expected_engine[key].strip()
               for key in identity_keys) \
                or SHA256_RE.fullmatch(expected_engine.get('binary_sha256', '')) is None \
                or any(not isinstance(expected_device.get(key), str) or not expected_device[key].strip()
                       for key in device_keys):
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: expected runtime identity invalid')
        observed_identity = {
            'backend': store_contract['backend'],
            'engine': {key: engine[key] for key in identity_keys},
            'device': {key: device[key] for key in device_keys},
        }
        expected_identity = {
            'backend': expected_backend,
            'engine': {key: expected_engine[key] for key in identity_keys},
            'device': {key: expected_device[key] for key in device_keys},
        }
        if observed_identity != expected_identity:
            raise PostprocessTraceError(
                'exact postprocess replay unavailable: capture runtime identity mismatch')

    def replay_final_rgb(
        self,
        final_rgb: np.ndarray,
        *,
        chunk_rows: int = 32,
        store_contract: Mapping | None = None,
        runtime_identity: Mapping | None = None,
    ) -> Iterator[PostprocessReplayChunk]:
        """Validate the producer contract before creating the replay iterator."""
        effective_contract = self.store_contract if store_contract is None else store_contract
        effective_identity = (
            self.expected_runtime_identity
            if runtime_identity is None else runtime_identity
        )
        self._validate_store_contract(effective_contract, effective_identity)
        if isinstance(chunk_rows, bool) or not isinstance(chunk_rows, int) or chunk_rows <= 0:
            raise PostprocessTraceError('replay chunk_rows must be a positive integer')
        if chunk_rows > MAX_CHUNK_ROWS:
            raise PostprocessTraceError(
                f'replay chunk_rows exceeds bounded maximum {MAX_CHUNK_ROWS}')
        return self._replay_final_rgb(final_rgb, chunk_rows=chunk_rows)

    def _replay_final_rgb(
        self,
        final_rgb: np.ndarray,
        *,
        chunk_rows: int = 32,
    ) -> Iterator[PostprocessReplayChunk]:
        """Replay native pre-imageStore RGB through verified Metal post-stores.

        Without a producer-attached store contract this method fails closed;
        ``source_contributors`` remains available for ordinary operator reads.

        The input is the existing ``ActualFieldFrame.fields['final_rgb']``
        array with shape ``(height, width, 3)``.  Each yielded chunk contains
        exact RGBA16F bit patterns after blur and/or present imageStore.  The
        implementation retains only the source rows needed by the current
        chunk and the current blur/present output rows; it never builds a
        second full-resolution image or a pixel dictionary.

        GPU and host arithmetic are compared as exact half bits by callers.
        A mismatch is evidence of arithmetic or driver behavior and must not
        be hidden with a tolerance.
        """
        if isinstance(chunk_rows, bool) or not isinstance(chunk_rows, int) or chunk_rows <= 0:
            raise PostprocessTraceError('replay chunk_rows must be a positive integer')
        if chunk_rows > MAX_CHUNK_ROWS:
            raise PostprocessTraceError(
                f'replay chunk_rows exceeds bounded maximum {MAX_CHUNK_ROWS}')
        source = np.asarray(final_rgb)
        if source.ndim != 3 or source.shape[2] != 3:
            raise PostprocessTraceError('final_rgb must have shape (height, width, 3)')
        blur = self.operators['blur']
        present = self.operators['present']
        blur_active = blur['status'] == 'dispatched'
        present_active = present['status'] == 'dispatched'
        if not blur_active and not present_active:
            raise PostprocessTraceError('postprocess replay has no dispatched stage')

        if blur_active:
            input_width = int(blur['source']['width'])
            input_height = int(blur['source']['height'])
            blur_width = int(blur['destination']['width'])
            blur_height = int(blur['destination']['height'])
        else:
            input_width = int(present['source']['width'])
            input_height = int(present['source']['height'])
            blur_width = blur_height = 0
        if tuple(source.shape[:2]) != (input_height, input_width):
            raise PostprocessTraceError('final_rgb extent disagrees with postprocess source')
        present_width = int(present['destination']['width']) if present_active else 0
        present_height = int(present['destination']['height']) if present_active else 0
        output_height = max(blur_height, present_height)
        blur_rows = self._rows('blur', blur_height) if blur_active else None
        present_rows = self._rows('present', present_height) if present_active else None

        if self.fused:
            coordinates = self._rows('present', blur_height)
            for start in range(0, blur_height, chunk_rows):
                output = []
                for y in range(start, min(start + chunk_rows, blur_height)):
                    center_x, center_y = self._present_sources(
                        y, coordinates, source_width=input_width, source_height=input_height,
                        destination_width=blur_width)
                    _, offsets_y = self._offsets(blur_rows[y])
                    needed = {center_y}
                    if int(blur_rows[y, 6]) & 1:
                        needed.update(min(max(center_y + int(dy), 0), input_height - 1)
                                      for dy in offsets_y)
                    source_rows = {sy: self._quantized_input_row(source, sy, input_width)
                                   for sy in needed}
                    output.append(self._replay_blur_row(
                        y, source_rows, blur_rows, width=blur_width, height=input_height,
                        center_coordinates=(center_x, center_y), source_width=input_width))
                yield PostprocessReplayChunk(start, np.stack(output), None)
            return

        for start in range(0, output_height, chunk_rows):
            stop = min(start + chunk_rows, output_height)
            blur_targets = range(start, min(stop, blur_height)) if blur_active else range(0)
            present_targets = range(start, min(stop, present_height)) if present_active else range(0)
            needed_blur_rows = set(blur_targets)
            present_coords: dict[int, tuple[np.ndarray, int]] = {}
            if present_active:
                for y in present_targets:
                    coords, source_y = self._present_sources(
                        y, present_rows, source_width=blur_width if blur_active else input_width,
                        source_height=blur_height if blur_active else input_height,
                        destination_width=present_width)
                    present_coords[y] = (coords, source_y)
                    if blur_active:
                        needed_blur_rows.add(source_y)

            needed_input_rows: set[int] = set()
            if blur_active:
                for y in needed_blur_rows:
                    needed_input_rows.update(self._blur_input_rows(y, blur_rows, input_height))
            else:
                needed_input_rows.update(source_y for _, source_y in present_coords.values())
            source_rows = {
                y: self._quantized_input_row(source, y, input_width)
                for y in sorted(needed_input_rows)
            }
            blur_cache: dict[int, np.ndarray] = {}
            if blur_active:
                for y in sorted(needed_blur_rows):
                    blur_cache[y] = self._replay_blur_row(
                        y, source_rows, blur_rows, width=blur_width, height=input_height)

            blur_output = None
            if blur_active and start < blur_height:
                blur_output = np.stack(
                    [blur_cache[y] for y in range(start, min(stop, blur_height))], axis=0)
            present_output = None
            if present_active and start < present_height:
                present_rows_out = []
                for y in range(start, min(stop, present_height)):
                    source_x, source_y = present_coords[y]
                    image = blur_cache[source_y] if blur_active else self._half_bits(source_rows[source_y])
                    present_rows_out.append(image[source_x])
                present_output = np.stack(present_rows_out, axis=0)
            yield PostprocessReplayChunk(start, blur_output, present_output)

    def source_contributors(self, x: int, y: int) -> dict:
        """Derive source coordinates from observed operators; never re-elect a cell.

        The output is an operator graph, not a claim of bit-exact reconstructed
        viewport color. RGBA16 image stores and subsequent Canvas blending are
        explicit later stages. Blur taps remain ordered, including edge repeats.
        """
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
            raise PostprocessTraceError('pixel coordinates must be integers')
        present, blur = self.operators['present'], self.operators['blur']
        sx, sy = x, y
        if present['status'] == 'dispatched':
            dst, src = present['destination'], present['source']
            if not (0 <= x < dst['width'] and 0 <= y < dst['height']):
                raise PostprocessTraceError('pixel outside present destination')
            row = self._rows('present', dst['height'])[y]
            shift = row[6:8].copy().view('<f4')
            size = np.array([src['width'], src['height']], dtype=np.float32)
            uv = (np.array([x, y], dtype=np.float32) + np.float32(.5) - shift) / size
            half = np.float32(.5) / size
            uv = np.clip(uv, half, np.float32(1) - half)
            sx, sy = np.clip(np.floor(uv * size).astype(np.int64), 0, size.astype(np.int64)-1)
            sx, sy = int(sx), int(sy)
            if x == 0 and (int(row[11]) & 65535, int(row[11]) >> 16) != (sx, sy):
                raise PostprocessTraceError('present source derivation contradicts observed x=0 texel')
        if self.fused and blur['status'] == 'dispatched':
            dst, src = blur['destination'], blur['source']
            if not (0 <= x < dst['width'] and 0 <= y < dst['height']):
                raise PostprocessTraceError('pixel outside fused destination')
            coordinates, sy = self._present_sources(
                y, self._rows('present', dst['height']), source_width=src['width'],
                source_height=src['height'], destination_width=x + 1)
            sx = int(coordinates[x])
        result = {'present_source': (sx, sy), 'address_basis': 'shader_operator_derivation',
                  'center': (sx, sy), 'taps': [], 'defocus': 0., 'desaturation': 0.,
                  'post_store_format': 'rgba16f', 'canvas_applied': False}
        if blur['status'] != 'dispatched':
            return result
        dst = blur['destination']
        if not self.fused and not (0 <= sx < dst['width'] and 0 <= sy < dst['height']):
            raise PostprocessTraceError('present source outside blur destination')
        row = self._rows('blur', dst['height'])[y if self.fused else sy]
        sample_extent = blur['source']
        mix = row[:3].copy().view('<f4')
        result['defocus'], result['desaturation'] = float(mix[0]), float(mix[2])
        if not int(row[6]) & 1:
            return result
        offsets = row[3:6].copy().view('i1')
        weights = (1, 4, 6, 4, 1)
        result['taps'] = [(min(max(sx + int(dx), 0), sample_extent['width']-1),
                           min(max(sy + int(dy), 0), sample_extent['height']-1),
                           weights[kx] * weights[ky] / 256.)
                          for ky, dy in enumerate(offsets[5:10])
                          for kx, dx in enumerate(offsets[:5])]
        return result
