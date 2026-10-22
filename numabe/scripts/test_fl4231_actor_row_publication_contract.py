#!/usr/bin/env python3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
SHADER = ROOT / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl"
STACK = ROOT / "godot_project" / "scripts" / "visual" / "ActorLayerStack.gd"


def _assert_actor_row_single_owner_contract(effect: str, shader: str) -> None:
    """Require one canonical R32UI row texture across Product and compositor."""
    required_effect = [
        'var actor_cells_rd: RID',
        'const ACTOR_CELL_WORDS_PER_ROW := 18',
        'const ACTOR_CELL_MAX_ROWS := 256',
        'actor_cells_rd = _upload_r32ui_bytes(actor_bytes, ACTOR_CELL_WORDS_PER_ROW, actor_count)',
        'func _publish_actor_cell_words_render(actor_bytes: PackedByteArray, actor_count: int, cache_key: String = "") -> void:',
        'u_actor_cells.binding = 3',
        'u_actor_cells.add_id(sampler_rd)',
        'u_compositor_actor_candidates.binding = 45',
        'u_compositor_actor_rows.binding = 46',
        'u_compositor_actor_rows.add_id(sampler_rd)',
        'u_compositor_actor_rows.add_id(compositor_actor_cells_bind)',
        'u_compositor_actor_atlas.binding = 47',
        'u_compositor_actor_atlas.add_id(sampler_rd)',
        'actor_cells_rd if actor_cells_rd.is_valid() else _splat_depth_placeholder_rd',
    ]
    for needle in required_effect:
        assert needle in effect, needle

    forbidden_effect = [
        'actor_cells_product_rd',
        'ACTOR_CELL_PRODUCT_BYTES',
        '_actor_cells_product_alloc_bytes',
        '_pending_actor_cell_product_bytes',
        '_actor_product_pack_cache_product_bytes',
        '_ensure_actor_cells_product_buffer',
    ]
    for needle in forbidden_effect:
        assert needle not in effect, needle

    publish = effect.split(
        'func _publish_actor_cell_words_render(', 1
    )[1].split('\n\n# FL-4346 Attempt 95:', 1)[0]
    assert 'product_bytes' not in publish
    assert 'storage_buffer_create' not in publish

    cache = effect.split(
        'func _publish_actor_sprite_source_rows_for_scene(', 1
    )[1].split('\nfunc _resolve_actor_sprite_rows_for_pixel_context', 1)[0]
    assert '_actor_product_pack_cache_bytes' in cache
    assert '_actor_product_pack_cache_product_bytes' not in cache
    assert '_pending_actor_cell_product_bytes' not in cache

    required_shader = [
        'layout(set = 0, binding = 45, std430) readonly buffer ActorCandidateProduct',
        'layout(set = 0, binding = 46) uniform usampler2D actor_cell_tex;',
        'layout(set = 0, binding = 47) uniform sampler2DArray actor_atlas_tex;',
        'return texelFetch(actor_cell_tex, ivec2(col, row), 0).r;',
    ]
    for needle in required_shader:
        assert needle in shader, needle
    assert 'readonly buffer ActorCellRows' not in shader
    assert 'actor_cell_rows.values' not in shader

    fragment = shader.split(
        'CompositorActorFragment compositor_actor_fragment_for_cell(', 1
    )[1].split('\n}\n', 1)[0]
    guard = 'if (actor_count == 0u) return fragment;'
    assert guard in fragment
    assert fragment.index(guard) < fragment.index('CompositorActorRay ray')


def test_actor_rows_have_one_canonical_texture_owner() -> None:
    effect = EFFECT.read_text(encoding='utf-8')
    shader = SHADER.read_text(encoding='utf-8')
    stack_src = STACK.read_text(encoding='utf-8')
    _assert_actor_row_single_owner_contract(effect, shader)

    stack_required = [
        'var _actor_frame_rect_queue_cache: Array = []',
        'var _actor_frame_rect_queue_dirty := true',
        'if not _actor_frame_rect_queue_dirty:\n\t\treturn _actor_frame_rect_queue_cache.duplicate(false)',
        '_actor_frame_rect_queue_dirty = true',
        '_actor_frame_rect_queue_cache = rows',
        'return _actor_frame_rect_queue_cache.duplicate(false)',
    ]
    for needle in stack_required:
        assert needle in stack_src, needle


def test_actor_row_contract_rejects_duplicate_product_resource_writer_and_cache() -> None:
    effect = EFFECT.read_text(encoding='utf-8')
    shader = SHADER.read_text(encoding='utf-8')
    mutations = [
        effect.replace(
            'var actor_cells_rd: RID',
            'var actor_cells_product_rd: RID\nvar actor_cells_rd: RID',
            1,
        ),
        effect.replace(
            'var _pending_actor_cell_bytes: PackedByteArray = PackedByteArray()',
            'var _pending_actor_cell_bytes: PackedByteArray = PackedByteArray()\n'
            'var _pending_actor_cell_product_bytes: PackedByteArray = PackedByteArray()',
            1,
        ),
        effect.replace(
            'var _actor_product_pack_cache_bytes: PackedByteArray = PackedByteArray()',
            'var _actor_product_pack_cache_bytes: PackedByteArray = PackedByteArray()\n'
            'var _actor_product_pack_cache_product_bytes: PackedByteArray = PackedByteArray()',
            1,
        ),
        effect.replace(
            'actor_cells_rd = _upload_r32ui_bytes(actor_bytes, ACTOR_CELL_WORDS_PER_ROW, actor_count)',
            'actor_cells_product_rd = rd.storage_buffer_create(4, PackedByteArray())\n\t'
            'actor_cells_rd = _upload_r32ui_bytes(actor_bytes, ACTOR_CELL_WORDS_PER_ROW, actor_count)',
            1,
        ),
    ]
    for mutated_effect in mutations:
        with pytest.raises(AssertionError):
            _assert_actor_row_single_owner_contract(mutated_effect, shader)


def test_actor_row_contract_rejects_storage_binding_and_binding_guard_mutations() -> None:
    effect = EFFECT.read_text(encoding='utf-8')
    shader = SHADER.read_text(encoding='utf-8')
    shader_mutations = [
        shader.replace(
            'layout(set = 0, binding = 46) uniform usampler2D actor_cell_tex;',
            'layout(set = 0, binding = 46, std430) readonly buffer ActorCellRows {\n'
            '\tuint values[];\n} actor_cell_rows;',
            1,
        ),
        shader.replace(
            'return texelFetch(actor_cell_tex, ivec2(col, row), 0).r;',
            'return actor_cell_rows.values[uint(row * ACTOR_CELL_WORDS_PER_ROW + col)];',
            1,
        ),
        shader.replace('if (actor_count == 0u) return fragment;\n', '', 1),
    ]
    for mutated_shader in shader_mutations:
        with pytest.raises(AssertionError):
            _assert_actor_row_single_owner_contract(effect, mutated_shader)

    binding_mutations = [
        effect.replace('u_compositor_actor_rows.binding = 46', 'u_compositor_actor_rows.binding = 45', 1),
        effect.replace('u_actor_cells.binding = 3', 'u_actor_cells.binding = 4', 1),
        effect.replace('u_compositor_actor_candidates.binding = 45', 'u_compositor_actor_candidates.binding = 44', 1),
        effect.replace('u_compositor_actor_atlas.binding = 47', 'u_compositor_actor_atlas.binding = 48', 1),
        effect.replace('u_compositor_actor_rows.add_id(sampler_rd)', 'u_compositor_actor_rows.add_id(actor_cells_rd)', 1),
    ]
    for mutated_effect in binding_mutations:
        with pytest.raises(AssertionError):
            _assert_actor_row_single_owner_contract(mutated_effect, shader)
