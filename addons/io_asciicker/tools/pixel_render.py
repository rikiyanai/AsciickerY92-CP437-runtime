# Pixel Art Rendering -- Eevee pixel art materials and render settings
# Ported from PixelArtAddon_v_3_1.py
# Original Copyright (C) 2023-2025 Lucas Roedel (GPL v3+)
#   https://lucasroedel.com
# [DEPENDENCY:BLENDER] - Requires Blender 4.0+

import bpy


# ---------------------------------------------------------------------------
# Module-level functions -- reusable by render_to_xp pipeline
# ---------------------------------------------------------------------------

def create_mix_rgb_node(node_tree, blend_type='MIX', location=(0, 0)):
    """Create a ShaderNodeMix configured for RGBA mixing (Blender 3.4+)."""
    mix_node = node_tree.nodes.new(type="ShaderNodeMix")
    mix_node.data_type = 'RGBA'
    mix_node.blend_type = blend_type
    mix_node.location = location
    return mix_node


def render_settings(context):
    """Configure Eevee for pixel art rendering."""
    scene = bpy.data.scenes['Scene']
    scene.render.engine = 'BLENDER_EEVEE'
    scene.eevee.taa_render_samples = 1
    scene.eevee.taa_samples = 1
    scene.eevee.use_taa_reprojection = False
    scene.render.filter_size = 0.00
    scene.render.use_freestyle = True
    scene.render.line_thickness = 0.3
    scene.render.resolution_x = 200
    scene.render.resolution_y = 150

    bpy.ops.scene.freestyle_color_modifier_add(type='MATERIAL')
    bpy.data.linestyles["LineStyle"].thickness_position = 'INSIDE'


def _get_or_create_bayer_matrix():
    """Get or create the 2x2 Bayer dither matrix image."""
    bayer = bpy.data.images.get("Bayer Matrix")
    if bayer is not None:
        return bayer

    bayer = bpy.data.images.new("Bayer Matrix", 2, 2)
    bayer.use_fake_user = True
    bayer.pixels[0] = 0.75294
    bayer.pixels[1] = 0.75294
    bayer.pixels[2] = 0.75294
    bayer.pixels[4] = 0.25098
    bayer.pixels[5] = 0.25098
    bayer.pixels[6] = 0.25098
    bayer.pixels[12] = 0.50196
    bayer.pixels[13] = 0.50196
    bayer.pixels[14] = 0.50196
    bayer.filepath_raw = "/tmp/bayerMatrix.png"
    bayer.file_format = 'PNG'
    bayer.save()
    return bayer


def _add_resolution_drivers(node_tree, combine_node):
    """Add resolution/2 drivers to X and Y inputs of a CombineXYZ node."""
    rx = node_tree.nodes.new(type="ShaderNodeValue")
    rx.location = (combine_node.location[0] - 200, combine_node.location[1])
    rx.label = "ResolutionX / 2"
    node_tree.links.new(rx.outputs[0], combine_node.inputs[0])
    drv = rx.outputs['Value'].driver_add("default_value")
    var = drv.driver.variables.new()
    var.name = "resolutionX"
    var.targets[0].id_type = 'SCENE'
    var.targets[0].id = bpy.data.scenes["Scene"]
    var.targets[0].data_path = "render.resolution_x"
    drv.driver.expression = "resolutionX / 2"

    ry = node_tree.nodes.new(type="ShaderNodeValue")
    ry.location = (combine_node.location[0] - 200, combine_node.location[1] - 100)
    ry.label = "ResolutionY / 2"
    node_tree.links.new(ry.outputs[0], combine_node.inputs[1])
    drv2 = ry.outputs['Value'].driver_add("default_value")
    var2 = drv2.driver.variables.new()
    var2.name = "resolutionY"
    var2.targets[0].id_type = 'SCENE'
    var2.targets[0].id = bpy.data.scenes["Scene"]
    var2.targets[0].data_path = "render.resolution_y"
    drv2.driver.expression = "resolutionY / 2"


def _build_dither_nodes(node_tree, bayer_image, output_socket, location_x=-850):
    """Build bayer texture + multiply + texcoord + resolution driver chain.

    Returns the bayer texture node's color output socket.
    """
    bayer_tex = node_tree.nodes.new(type="ShaderNodeTexImage")
    bayer_tex.location = (location_x, -250)
    bayer_tex.image = bayer_image
    bayer_tex.interpolation = 'Closest'

    multiply = node_tree.nodes.new(type="ShaderNodeVectorMath")
    multiply.location = (location_x - 200, -450)
    multiply.operation = 'MULTIPLY'
    node_tree.links.new(multiply.outputs[0], bayer_tex.inputs[0])

    tex_coord = node_tree.nodes.new(type="ShaderNodeTexCoord")
    tex_coord.location = (location_x - 400, -300)
    node_tree.links.new(tex_coord.outputs[5], multiply.inputs[0])

    combine = node_tree.nodes.new(type="ShaderNodeCombineXYZ")
    combine.location = (location_x - 400, -600)
    node_tree.links.new(combine.outputs[0], multiply.inputs[1])

    _add_resolution_drivers(node_tree, combine)

    return bayer_tex.outputs[0]


def single_material(context):
    """Create/reset the PixelArt_Simple material with dithered color ramp."""
    bayer = _get_or_create_bayer_matrix()

    for mat in bpy.data.materials:
        if mat.name == "PixelArt_Simple":
            bpy.data.materials.remove(mat)
    material = bpy.data.materials.new(name="PixelArt_Simple")
    material.use_nodes = True
    material.use_fake_user = True

    tree = material.node_tree
    mat_output = next(n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL')
    for n in tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            tree.nodes.remove(n)
            break

    emission = tree.nodes.new(type="ShaderNodeEmission")
    emission.location = (100, 300)
    tree.links.new(emission.outputs[0], mat_output.inputs[0])

    ramp = tree.nodes.new(type="ShaderNodeValToRGB")
    ramp.location = (-250, 300)
    tree.links.new(ramp.outputs[0], emission.inputs[0])
    ramp.color_ramp.interpolation = 'CONSTANT'
    ramp.color_ramp.elements.remove(ramp.color_ramp.elements[1])
    ramp.color_ramp.elements.new(0.075)
    ramp.color_ramp.elements.new(0.225)
    ramp.color_ramp.elements.new(0.450)
    ramp.color_ramp.elements.new(0.800)
    ramp.color_ramp.elements[0].color = [0.191202, 0.033105, 0.063010, 1.0]
    ramp.color_ramp.elements[1].color = [0.337164, 0.063010, 0.045186, 1.0]
    ramp.color_ramp.elements[2].color = [0.603828, 0.138432, 0.049707, 1.0]
    ramp.color_ramp.elements[3].color = [0.783538, 0.274677, 0.078187, 1.0]
    ramp.color_ramp.elements[4].color = [0.955974, 0.473532, 0.090842, 1.0]

    mix_soft = create_mix_rgb_node(tree, 'SOFT_LIGHT', (-500, 138))
    mix_soft.inputs[0].default_value = 0.2
    tree.links.new(mix_soft.outputs[2], ramp.inputs[0])

    shader_rgb = tree.nodes.new(type="ShaderNodeShaderToRGB")
    shader_rgb.location = (-750, 250)
    tree.links.new(shader_rgb.outputs[0], mix_soft.inputs[6])

    bsdf = tree.nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (-1100, 500)
    tree.links.new(bsdf.outputs[0], shader_rgb.inputs[0])

    dither_out = _build_dither_nodes(tree, bayer)
    tree.links.new(dither_out, mix_soft.inputs[7])


def multiple_material(context):
    """Create/reset the PixelArt_MultipleLights material with per-channel ramps."""
    bayer = _get_or_create_bayer_matrix()

    for mat in bpy.data.materials:
        if mat.name == "PixelArt_MultipleLights":
            bpy.data.materials.remove(mat)
    material = bpy.data.materials.new(name="PixelArt_MultipleLights")
    material.use_nodes = True
    material.use_fake_user = True

    tree = material.node_tree
    mat_output = next(n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL')
    for n in tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            tree.nodes.remove(n)
            break

    # Dithering node group
    for group in bpy.data.node_groups:
        if group.name == 'Dithering':
            bpy.data.node_groups.remove(group)
    dither_group = bpy.data.node_groups.new('Dithering', 'ShaderNodeTree')
    dither_group.interface.new_socket("Color", in_out='OUTPUT', socket_type="NodeSocketColor")

    group_output = dither_group.nodes.new("NodeGroupOutput")
    group_output.location = (0, 0)

    bayer_tex = dither_group.nodes.new(type="ShaderNodeTexImage")
    bayer_tex.location = (-300, 0)
    dither_group.links.new(bayer_tex.outputs[0], group_output.inputs[0])
    bayer_tex.image = bayer
    bayer_tex.interpolation = 'Closest'

    multiply = dither_group.nodes.new(type="ShaderNodeVectorMath")
    multiply.location = (-500, -210)
    multiply.operation = 'MULTIPLY'
    dither_group.links.new(multiply.outputs[0], bayer_tex.inputs[0])

    tex_coord = dither_group.nodes.new(type="ShaderNodeTexCoord")
    tex_coord.location = (-700, -100)
    dither_group.links.new(tex_coord.outputs[5], multiply.inputs[0])

    combine = dither_group.nodes.new(type="ShaderNodeCombineXYZ")
    combine.location = (-700, -400)
    dither_group.links.new(combine.outputs[0], multiply.inputs[1])

    _add_resolution_drivers(dither_group, combine)

    # Main material tree
    emission_out = tree.nodes.new(type="ShaderNodeEmission")
    emission_out.location = (100, 300)
    tree.links.new(emission_out.outputs[0], mat_output.inputs[0])

    mix1 = create_mix_rgb_node(tree, 'LIGHTEN', (-150, 300))
    tree.links.new(mix1.outputs[2], emission_out.inputs[0])

    mix2 = create_mix_rgb_node(tree, 'LIGHTEN', (-400, 100))
    tree.links.new(mix2.outputs[2], mix1.inputs[7])

    # Channel color ramp definitions: (y_offset, mix_input, colors)
    channel_defs = [
        (600, mix1.inputs[6], [
            [0, 0, 0, 1], [0.191202, 0.033105, 0.063010, 1],
            [0.337164, 0.063010, 0.045186, 1], [0.603828, 0.138432, 0.049707, 1],
            [0.783538, 0.274677, 0.078187, 1], [0.955974, 0.473532, 0.090842, 1],
        ]),
        (200, mix2.inputs[6], [
            [0, 0, 0, 1], [0.011612, 0.102242, 0.074214, 1],
            [0.011612, 0.102242, 0.074214, 1], [0.016807, 0.496933, 0.168269, 1],
            [0.278894, 0.701102, 0.141263, 1], [0.603828, 0.730461, 0.149960, 1],
        ]),
        (-100, mix2.inputs[7], [
            [0, 0, 0, 1], [0.035601, 0.036889, 0.088656, 1],
            [0.068478, 0.070360, 0.181164, 1], [0.076185, 0.130137, 0.450786, 1],
            [0.076185, 0.323143, 0.783538, 1], [0.270498, 0.644480, 1.000000, 1],
        ]),
    ]

    for y_off, target_input, colors in channel_defs:
        ramp = tree.nodes.new(type="ShaderNodeValToRGB")
        ramp.location = (-850, y_off)
        tree.links.new(ramp.outputs[0], target_input)
        ramp.color_ramp.interpolation = 'CONSTANT'
        ramp.color_ramp.elements.remove(ramp.color_ramp.elements[1])
        for pos in [0.01, 0.075, 0.225, 0.450, 0.800]:
            ramp.color_ramp.elements.new(pos)
        for i, c in enumerate(colors):
            ramp.color_ramp.elements[i].color = c

        mix_soft = create_mix_rgb_node(tree, 'SOFT_LIGHT', (-1100, y_off))
        mix_soft.inputs[0].default_value = 0.2
        tree.links.new(mix_soft.outputs[2], ramp.inputs[0])

        dg = tree.nodes.new("ShaderNodeGroup")
        dg.node_tree = dither_group
        dg.location = (-1300, y_off)
        tree.links.new(dg.outputs[0], mix_soft.inputs[7])

    # Shader to RGB + Separate Color + BSDF
    shader_rgb = tree.nodes.new(type="ShaderNodeShaderToRGB")
    shader_rgb.location = (-2200, 0)
    sep_color = tree.nodes.new(type="ShaderNodeSeparateColor")
    sep_color.location = (-2000, 0)
    tree.links.new(shader_rgb.outputs[0], sep_color.inputs[0])

    # Connect separate color channels to the soft-light mix nodes
    soft_nodes = [n for n in tree.nodes if n.bl_idname == 'ShaderNodeMix' and n.blend_type == 'SOFT_LIGHT']
    soft_nodes.sort(key=lambda n: -n.location[1])  # top to bottom
    for i, sn in enumerate(soft_nodes):
        tree.links.new(sep_color.outputs[i], sn.inputs[6])

    bsdf = tree.nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (-2500, 0)
    tree.links.new(bsdf.outputs[0], shader_rgb.inputs[0])


def lights_setup(context):
    """Create a tri-light setup (R/G/B point lights) for multi-channel material."""
    for obj in bpy.data.objects:
        if obj.name.startswith("PixelArt_Light_"):
            bpy.data.objects.remove(obj, do_unlink=True)
    for light in bpy.data.lights:
        if light.name.startswith("PixelArt_Light_"):
            bpy.data.lights.remove(light, do_unlink=True)

    light_defs = [
        ("PixelArt_Light_R", (1, 0, 0), 250, (3.46, -0.41, 1.04)),
        ("PixelArt_Light_G", (0, 1, 0), 250, (-2.1, 2, 1.37)),
        ("PixelArt_Light_B", (0, 0, 1), 150, (-0.06, -1.46, 2.18)),
    ]
    for name, color, energy, loc in light_defs:
        light = bpy.data.lights.new(name=name, type='POINT')
        light.color = color
        light.energy = energy
        obj = bpy.data.objects.new(name=name, object_data=light)
        bpy.context.collection.objects.link(obj)
        obj.location = loc

    bpy.data.worlds["World"].node_tree.nodes["Background"].inputs[1].default_value = 0


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class ASCIICKER_OT_pixel_render_settings(bpy.types.Operator):
    """Set up Eevee for pixel art rendering"""
    bl_idname = "asciicker.pixel_render_settings"
    bl_label = "Pixel Render Settings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        render_settings(context)
        return {'FINISHED'}


class ASCIICKER_OT_pixel_single_material(bpy.types.Operator):
    """Create/reset the simple pixel art material"""
    bl_idname = "asciicker.pixel_single_material"
    bl_label = "Create Simple Material"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        single_material(context)
        return {'FINISHED'}


class ASCIICKER_OT_pixel_multiple_material(bpy.types.Operator):
    """Create/reset the multi-light pixel art material"""
    bl_idname = "asciicker.pixel_multiple_material"
    bl_label = "Create Multi-Light Material"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        multiple_material(context)
        return {'FINISHED'}


class ASCIICKER_OT_pixel_lights_setup(bpy.types.Operator):
    """Create a tri-light setup for the multi-light pixel art material"""
    bl_idname = "asciicker.pixel_lights_setup"
    bl_label = "Tri Light Setup"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        lights_setup(context)
        return {'FINISHED'}


classes = (
    ASCIICKER_OT_pixel_render_settings,
    ASCIICKER_OT_pixel_single_material,
    ASCIICKER_OT_pixel_multiple_material,
    ASCIICKER_OT_pixel_lights_setup,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
