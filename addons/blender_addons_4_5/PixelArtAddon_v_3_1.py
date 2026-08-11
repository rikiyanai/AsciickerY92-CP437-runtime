"""
Copyright (C) 2023-2025 Lucas Roedel
    https://lucasroedel.com
    contato@lucasroedel.com

Created by Lucas Roedel Ribeiro

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""





bl_info = {
    "name": "Pixel Art Rendering (Eevee)",
    "author": "Lucas Roedel",
    "version": (3, 1),
    "blender": (5, 0, 0),
    "location": "3D Viewport > Side panel",
    "description": "Pixel Art rendering addon using eevee. Based on the work of Mezaka",
    "warning": "",
    "doc_url": "",
    "category": "Render",
}








import bpy



########## Set Render Settings ##########

def render_settings(context):
    
    # Changed from BLENDER_EEVEE_NEXT to BLENDER_EEVEE for Blender 5.0
    bpy.data.scenes['Scene'].render.engine = 'BLENDER_EEVEE'
    bpy.data.scenes["Scene"].eevee.taa_render_samples = 1
    bpy.data.scenes["Scene"].eevee.taa_samples = 1
    bpy.data.scenes["Scene"].eevee.use_taa_reprojection = False
    # use_gtao was removed in Blender 5.0 (it did nothing since 4.2)
    bpy.data.scenes["Scene"].render.filter_size = 0.00
    bpy.data.scenes["Scene"].render.use_freestyle = True
    bpy.data.scenes["Scene"].render.line_thickness = 0.3

    bpy.data.scenes["Scene"].render.resolution_x = 200
    bpy.data.scenes["Scene"].render.resolution_y = 150

    bpy.ops.scene.freestyle_color_modifier_add(type='MATERIAL')
    bpy.data.linestyles["LineStyle"].thickness_position = 'INSIDE'




class PIXEL_ART_OT_render_settings(bpy.types.Operator):
    """Sets up blender with the correct settings for pixel art rendering"""
    bl_idname = "render.render_settings"
    bl_label = "Render Settings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        render_settings(context)
        return {'FINISHED'}










########## Helper function to create Mix nodes (Blender 5.0 compatible) ##########

def create_mix_rgb_node(node_tree, blend_type='MIX', location=(0, 0)):
    """
    Creates a Mix node configured for RGB mixing.
    In Blender 3.4+, ShaderNodeMixRGB was replaced by ShaderNodeMix.
    This function creates the appropriate node for Blender 5.0.
    """
    mix_node = node_tree.nodes.new(type="ShaderNodeMix")
    mix_node.data_type = 'RGBA'
    mix_node.blend_type = blend_type
    mix_node.location = location
    return mix_node


########## Creates Simple Default Material ##########

def single_material(context):
    
    # Generate Bayer Matrix 2x2
    bayerMatrix = bpy.data.images.get("Bayer Matrix")

    if bayerMatrix == None:

        bayerMatrix = bpy.data.images.new("Bayer Matrix", 2, 2)
        bayerMatrix.use_fake_user = True
        bayerMatrix.pixels[0] = (0.75294)
        bayerMatrix.pixels[1] = (0.75294)
        bayerMatrix.pixels[2] = (0.75294)
        bayerMatrix.pixels[4] = (0.25098)
        bayerMatrix.pixels[5] = (0.25098)
        bayerMatrix.pixels[6] = (0.25098)
        bayerMatrix.pixels[12] = (0.50196)
        bayerMatrix.pixels[13] = (0.50196)
        bayerMatrix.pixels[14] = (0.50196)
        
        bayerMatrix.filepath_raw = "/tmp/bayerMatrix.png"
        bayerMatrix.file_format = 'PNG'
        bayerMatrix.save()        

    # Creates a material with the name if it doesn' exist already
    for material in bpy.data.materials:
        if material.name == "PixelArt_Simple":
            bpy.data.materials.remove(material)       
    material = bpy.data.materials.new(name = "PixelArt_Simple")
    material.use_nodes = True
    material.use_fake_user = True

    materialOutput = None
    for node in material.node_tree.nodes:
        if node.type == 'OUTPUT_MATERIAL':
            materialOutput = node
            break

    for node in material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            material.node_tree.nodes.remove(node)
            break

    # Creates the emission shader node
    emissionNode = material.node_tree.nodes.new(type = "ShaderNodeEmission")
    emissionNode.location = (100,300)
    material.node_tree.links.new(emissionNode.outputs[0], materialOutput.inputs[0])

    # Creates the color ramp node
    colorRampNode = material.node_tree.nodes.new(type = "ShaderNodeValToRGB")
    colorRampNode.location = (-250,300)
    material.node_tree.links.new(colorRampNode.outputs[0], emissionNode.inputs[0])
    colorRampNode.color_ramp.interpolation = 'CONSTANT'
    colorRampNode.color_ramp.elements.remove(colorRampNode.color_ramp.elements[1])
    colorRampNode.color_ramp.elements.new(0.075)
    colorRampNode.color_ramp.elements.new(0.225)
    colorRampNode.color_ramp.elements.new(0.450)
    colorRampNode.color_ramp.elements.new(0.800)
    colorRampNode.color_ramp.elements[0].color = [0.191202, 0.033105, 0.063010, 1.000000]
    colorRampNode.color_ramp.elements[1].color = [0.337164, 0.063010, 0.045186, 1.000000]
    colorRampNode.color_ramp.elements[2].color = [0.603828, 0.138432, 0.049707, 1.000000]
    colorRampNode.color_ramp.elements[3].color = [0.783538, 0.274677, 0.078187, 1.000000]
    colorRampNode.color_ramp.elements[4].color = [0.955974, 0.473532, 0.090842, 1.000000]

    # Creates the Mix node (using ShaderNodeMix with RGBA for Blender 5.0)
    mixSoftLightNode = create_mix_rgb_node(material.node_tree, 'SOFT_LIGHT', (-500, 138))
    mixSoftLightNode.inputs[0].default_value = 0.2
    # For ShaderNodeMix with RGBA: outputs[2] is the Result (Color output)
    material.node_tree.links.new(mixSoftLightNode.outputs[2], colorRampNode.inputs[0])

    # Creates the shader to RGB node
    shaderToRgbNode = material.node_tree.nodes.new(type = "ShaderNodeShaderToRGB")
    shaderToRgbNode.location = (-750, 250)
    # For ShaderNodeMix with RGBA: inputs[6] is A (first color), inputs[7] is B (second color)
    material.node_tree.links.new(shaderToRgbNode.outputs[0], mixSoftLightNode.inputs[6])

    # Creates the principled BSDF node
    bsdfNode = material.node_tree.nodes.new(type = "ShaderNodeBsdfPrincipled")
    bsdfNode.location = (-1100, 500)
    material.node_tree.links.new(bsdfNode.outputs[0], shaderToRgbNode.inputs[0])

    # Creates the bayer texture node
    bayerTexNode = material.node_tree.nodes.new(type = "ShaderNodeTexImage")
    bayerTexNode.location = (-850, -250)
    # inputs[7] is B (second color) for ShaderNodeMix with RGBA
    material.node_tree.links.new(bayerTexNode.outputs[0], mixSoftLightNode.inputs[7])
    bayerTexNode.image = bayerMatrix
    bayerTexNode.interpolation = 'Closest'

    # Creates the multiply node
    multiplyVector = material.node_tree.nodes.new(type = "ShaderNodeVectorMath")
    multiplyVector.location = (-1050, -450)
    multiplyVector.operation = 'MULTIPLY'
    material.node_tree.links.new(multiplyVector.outputs[0], bayerTexNode.inputs[0])

    # Creates the Texture Coordinate node
    texCoordNode = material.node_tree.nodes.new(type = "ShaderNodeTexCoord")
    texCoordNode.location = (-1250, -300)
    material.node_tree.links.new(texCoordNode.outputs[5], multiplyVector.inputs[0])

    # Creates the combineXYZ node
    combineXyzNode = material.node_tree.nodes.new(type = "ShaderNodeCombineXYZ")
    combineXyzNode.location = (-1250, -600)
    material.node_tree.links.new(combineXyzNode.outputs[0], multiplyVector.inputs[1])

    # Creates the Resolution nodes
    resolutionXnode = material.node_tree.nodes.new(type = "ShaderNodeValue")
    resolutionXnode.location = (-1450, -600)
    resolutionXnode.label = "ResolutionX / 2"
    material.node_tree.links.new(resolutionXnode.outputs[0], combineXyzNode.inputs[0])

    resolutionXdriver = resolutionXnode.outputs['Value'].driver_add("default_value")
    var1 = resolutionXdriver.driver.variables.new()
    var1.name = "resolutionX"
    var1.targets[0].id_type = 'SCENE'
    var1.targets[0].id = bpy.data.scenes["Scene"]
    var1.targets[0].data_path = "render.resolution_x"
    resolutionXdriver.driver.expression = "resolutionX / 2"

    resolutionYnode = material.node_tree.nodes.new(type = "ShaderNodeValue")
    resolutionYnode.location = (-1450, -700)
    resolutionYnode.label = "ResolutionY / 2"
    material.node_tree.links.new(resolutionYnode.outputs[0], combineXyzNode.inputs[1])

    resolutionYdriver = resolutionYnode.outputs['Value'].driver_add("default_value")
    var2 = resolutionYdriver.driver.variables.new()
    var2.name = "resolutionY"
    var2.targets[0].id_type = 'SCENE'
    var2.targets[0].id = bpy.data.scenes["Scene"]
    var2.targets[0].data_path = "render.resolution_y"
    resolutionYdriver.driver.expression = "resolutionY / 2"




class PIXEL_ART_OT_single_material(bpy.types.Operator):
    """Creates default pixel art material. If material with name 'PixelArt_Simple' already exists, resets it to default"""
    bl_idname = "render.single_material"
    bl_label = "Create/Reset Material"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        single_material(context)
        return {'FINISHED'}










########## Creates Multiple Lights Default Material ##########

def multiple_material(context):
    
    # Generate Bayer Matrix 2x2
    bayerMatrix = bpy.data.images.get("Bayer Matrix")

    if bayerMatrix == None:

        bayerMatrix = bpy.data.images.new("Bayer Matrix", 2, 2)
        bayerMatrix.use_fake_user = True
        bayerMatrix.pixels[0] = (0.75294)
        bayerMatrix.pixels[1] = (0.75294)
        bayerMatrix.pixels[2] = (0.75294)
        bayerMatrix.pixels[4] = (0.25098)
        bayerMatrix.pixels[5] = (0.25098)
        bayerMatrix.pixels[6] = (0.25098)
        bayerMatrix.pixels[12] = (0.50196)
        bayerMatrix.pixels[13] = (0.50196)
        bayerMatrix.pixels[14] = (0.50196)
        
        bayerMatrix.filepath_raw = "/tmp/bayerMatrix.png"
        bayerMatrix.file_format = 'PNG'
        bayerMatrix.save()

    # Creates a material with the name if it doesn' exist already
    for material in bpy.data.materials:
        if material.name == "PixelArt_MultipleLights":
            bpy.data.materials.remove(material)
        
    material = bpy.data.materials.new(name = "PixelArt_MultipleLights")

    material.use_nodes = True
    material.use_fake_user = True

    materialOutput = None
    for node in material.node_tree.nodes:
        if node.type == 'OUTPUT_MATERIAL':
            materialOutput = node
            break

    for node in material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            material.node_tree.nodes.remove(node)
            break

    # Creates a group for the dithering part
    for group in bpy.data.node_groups:
        if group.name == 'Dithering':
            bpy.data.node_groups.remove(group)

    ditherGroup = bpy.data.node_groups.new('Dithering', 'ShaderNodeTree')
    ditherGroup.interface.new_socket("Color", in_out='OUTPUT', socket_type="NodeSocketColor")

    outputNode = ditherGroup.nodes.new("NodeGroupOutput")
    outputNode.location = (0, 0)

    # Creates the bayer texture node
    bayerTexNode = ditherGroup.nodes.new(type = "ShaderNodeTexImage")
    bayerTexNode.location = (-300, 0)
    ditherGroup.links.new(bayerTexNode.outputs[0], outputNode.inputs[0])
    bayerTexNode.image = bayerMatrix
    bayerTexNode.interpolation = 'Closest'

    # Creates the multiply node
    multiplyVector = ditherGroup.nodes.new(type = "ShaderNodeVectorMath")
    multiplyVector.location = (-500, -210)
    multiplyVector.operation = 'MULTIPLY'
    ditherGroup.links.new(multiplyVector.outputs[0], bayerTexNode.inputs[0])

    # Creates the Texture Coordinate node
    texCoordNode = ditherGroup.nodes.new(type = "ShaderNodeTexCoord")
    texCoordNode.location = (-700, -100)
    ditherGroup.links.new(texCoordNode.outputs[5], multiplyVector.inputs[0])

    # Creates the combineXYZ node
    combineXyzNode = ditherGroup.nodes.new(type = "ShaderNodeCombineXYZ")
    combineXyzNode.location = (-700, -400)
    ditherGroup.links.new(combineXyzNode.outputs[0], multiplyVector.inputs[1])

    # Creates the Resolution nodes
    resolutionXnode = ditherGroup.nodes.new(type = "ShaderNodeValue")
    resolutionXnode.location = (-900, -400)
    resolutionXnode.label = "ResolutionX / 2"
    ditherGroup.links.new(resolutionXnode.outputs[0], combineXyzNode.inputs[0])

    resolutionXdriver = resolutionXnode.outputs['Value'].driver_add("default_value")
    var1 = resolutionXdriver.driver.variables.new()
    var1.name = "resolutionX"
    var1.targets[0].id_type = 'SCENE'
    var1.targets[0].id = bpy.data.scenes["Scene"]
    var1.targets[0].data_path = "render.resolution_x"
    resolutionXdriver.driver.expression = "resolutionX / 2"

    resolutionYnode = ditherGroup.nodes.new(type = "ShaderNodeValue")
    resolutionYnode.location = (-900, -500)
    resolutionYnode.label = "ResolutionY / 2"
    ditherGroup.links.new(resolutionYnode.outputs[0], combineXyzNode.inputs[1])

    resolutionYdriver = resolutionYnode.outputs['Value'].driver_add("default_value")
    var2 = resolutionYdriver.driver.variables.new()
    var2.name = "resolutionY"
    var2.targets[0].id_type = 'SCENE'
    var2.targets[0].id = bpy.data.scenes["Scene"]
    var2.targets[0].data_path = "render.resolution_y"
    resolutionYdriver.driver.expression = "resolutionY / 2"


    # Creates the two mix RGB nodes (using ShaderNodeMix for Blender 5.0)

    emissionOutputNode = material.node_tree.nodes.new(type = "ShaderNodeEmission")
    emissionOutputNode.location = (100, 300)
    material.node_tree.links.new(emissionOutputNode.outputs[0], materialOutput.inputs[0])

    mixShaderNode1 = create_mix_rgb_node(material.node_tree, 'LIGHTEN', (-150, 300))
    # outputs[2] is the Result for ShaderNodeMix with RGBA
    material.node_tree.links.new(mixShaderNode1.outputs[2], emissionOutputNode.inputs[0])

    mixShaderNode2 = create_mix_rgb_node(material.node_tree, 'LIGHTEN', (-400, 100))
    # inputs[6] is A, inputs[7] is B for ShaderNodeMix with RGBA
    material.node_tree.links.new(mixShaderNode2.outputs[2], mixShaderNode1.inputs[7])


    ### RED CHANNEL ###
    # Creates the color ramp node
    colorRampNode = material.node_tree.nodes.new(type = "ShaderNodeValToRGB")
    colorRampNode.location = (-850,600)
    material.node_tree.links.new(colorRampNode.outputs[0], mixShaderNode1.inputs[6])
    colorRampNode.color_ramp.interpolation = 'CONSTANT'
    colorRampNode.color_ramp.elements.remove(colorRampNode.color_ramp.elements[1])
    colorRampNode.color_ramp.elements.new(0.01)
    colorRampNode.color_ramp.elements.new(0.075)
    colorRampNode.color_ramp.elements.new(0.225)
    colorRampNode.color_ramp.elements.new(0.450)
    colorRampNode.color_ramp.elements.new(0.800)

    colorRampNode.color_ramp.elements[0].color = [0, 0, 0, 1.000000]
    colorRampNode.color_ramp.elements[1].color = [0.191202, 0.033105, 0.063010, 1.000000]
    colorRampNode.color_ramp.elements[2].color = [0.337164, 0.063010, 0.045186, 1.000000]
    colorRampNode.color_ramp.elements[3].color = [0.603828, 0.138432, 0.049707, 1.000000]
    colorRampNode.color_ramp.elements[4].color = [0.783538, 0.274677, 0.078187, 1.000000]
    colorRampNode.color_ramp.elements[5].color = [0.955974, 0.473532, 0.090842, 1.000000]

    # Creates the Mix soft light node
    mixSoftLightNode = create_mix_rgb_node(material.node_tree, 'SOFT_LIGHT', (-1100, 600))
    mixSoftLightNode.inputs[0].default_value = 0.2
    material.node_tree.links.new(mixSoftLightNode.outputs[2], colorRampNode.inputs[0])

    # Adds the dithering node group to the tree
    ditherGroupNode = material.node_tree.nodes.new("ShaderNodeGroup")
    ditherGroupNode.node_tree = ditherGroup
    ditherGroupNode.location = (-1300, 600)
    material.node_tree.links.new(ditherGroupNode.outputs[0], mixSoftLightNode.inputs[7])

    ### GREEN CHANNEL ###
    # Creates the color ramp node
    colorRampNodeG = material.node_tree.nodes.new(type = "ShaderNodeValToRGB")
    colorRampNodeG.location = (-850,200)
    material.node_tree.links.new(colorRampNodeG.outputs[0], mixShaderNode2.inputs[6])
    colorRampNodeG.color_ramp.interpolation = 'CONSTANT'
    colorRampNodeG.color_ramp.elements.remove(colorRampNodeG.color_ramp.elements[1])
    colorRampNodeG.color_ramp.elements.new(0.01)
    colorRampNodeG.color_ramp.elements.new(0.075)
    colorRampNodeG.color_ramp.elements.new(0.225)
    colorRampNodeG.color_ramp.elements.new(0.450)
    colorRampNodeG.color_ramp.elements.new(0.800)

    colorRampNodeG.color_ramp.elements[0].color = [0, 0, 0, 1.000000]
    colorRampNodeG.color_ramp.elements[1].color = [0.011612, 0.102242, 0.074214, 1.000000]
    colorRampNodeG.color_ramp.elements[2].color = [0.011612, 0.102242, 0.074214, 1.000000]
    colorRampNodeG.color_ramp.elements[3].color = [0.016807, 0.496933, 0.168269, 1.000000]
    colorRampNodeG.color_ramp.elements[4].color = [0.278894, 0.701102, 0.141263, 1.000000]
    colorRampNodeG.color_ramp.elements[5].color = [0.603828, 0.730461, 0.149960, 1.000000]

    # Creates the Mix soft light node
    mixSoftLightNodeG = create_mix_rgb_node(material.node_tree, 'SOFT_LIGHT', (-1100, 200))
    mixSoftLightNodeG.inputs[0].default_value = 0.2
    material.node_tree.links.new(mixSoftLightNodeG.outputs[2], colorRampNodeG.inputs[0])

    # Adds the dithering node group to the tree
    ditherGroupNodeG = material.node_tree.nodes.new("ShaderNodeGroup")
    ditherGroupNodeG.node_tree = ditherGroup
    ditherGroupNodeG.location = (-1300, 200)
    material.node_tree.links.new(ditherGroupNodeG.outputs[0], mixSoftLightNodeG.inputs[7])

    ### BLUE CHANNEL ###
    # Creates the color ramp node
    colorRampNodeB = material.node_tree.nodes.new(type = "ShaderNodeValToRGB")
    colorRampNodeB.location = (-850,-100)
    material.node_tree.links.new(colorRampNodeB.outputs[0], mixShaderNode2.inputs[7])
    colorRampNodeB.color_ramp.interpolation = 'CONSTANT'
    colorRampNodeB.color_ramp.elements.remove(colorRampNodeB.color_ramp.elements[1])
    colorRampNodeB.color_ramp.elements.new(0.01)
    colorRampNodeB.color_ramp.elements.new(0.075)
    colorRampNodeB.color_ramp.elements.new(0.225)
    colorRampNodeB.color_ramp.elements.new(0.450)
    colorRampNodeB.color_ramp.elements.new(0.800)

    colorRampNodeB.color_ramp.elements[0].color = [0, 0, 0, 1.000000]
    colorRampNodeB.color_ramp.elements[1].color = [0.035601, 0.036889, 0.088656, 1.000000]
    colorRampNodeB.color_ramp.elements[2].color = [0.068478, 0.070360, 0.181164, 1.000000]
    colorRampNodeB.color_ramp.elements[3].color = [0.076185, 0.130137, 0.450786, 1.000000]
    colorRampNodeB.color_ramp.elements[4].color = [0.076185, 0.323143, 0.783538, 1.000000]
    colorRampNodeB.color_ramp.elements[5].color = [0.270498, 0.644480, 1.000000, 1.000000]

    # Creates the Mix soft light node
    mixSoftLightNodeB = create_mix_rgb_node(material.node_tree, 'SOFT_LIGHT', (-1100, -100))
    mixSoftLightNodeB.inputs[0].default_value = 0.2
    material.node_tree.links.new(mixSoftLightNodeB.outputs[2], colorRampNodeB.inputs[0])

    # Adds the dithering node group to the tree
    ditherGroupNodeB = material.node_tree.nodes.new("ShaderNodeGroup")
    ditherGroupNodeB.node_tree = ditherGroup
    ditherGroupNodeB.location = (-1300, -100)
    material.node_tree.links.new(ditherGroupNodeB.outputs[0], mixSoftLightNodeB.inputs[7])

    # Creates the shader to RGB node
    shaderToRgbNode = material.node_tree.nodes.new(type = "ShaderNodeShaderToRGB")
    shaderToRgbNode.location = (-2200, 0)

    # Creates the separate color node
    separateColorNode = material.node_tree.nodes.new(type = "ShaderNodeSeparateColor")
    separateColorNode.location = (-2000, 0)
    material.node_tree.links.new(shaderToRgbNode.outputs[0], separateColorNode.inputs[0])
    material.node_tree.links.new(separateColorNode.outputs[0], mixSoftLightNode.inputs[6])
    material.node_tree.links.new(separateColorNode.outputs[1], mixSoftLightNodeG.inputs[6])
    material.node_tree.links.new(separateColorNode.outputs[2], mixSoftLightNodeB.inputs[6])

    # Creates the principled BSDF node
    bsdfNode = material.node_tree.nodes.new(type = "ShaderNodeBsdfPrincipled")
    bsdfNode.location = (-2500, 0)
    material.node_tree.links.new(bsdfNode.outputs[0], shaderToRgbNode.inputs[0])






class PIXEL_ART_OT_multiple_material(bpy.types.Operator):
    """Creates pixel art material with multiple lights setup. If material with name 'PixelArt_MultipleLights' already exists, resets it to default"""
    bl_idname = "render.multiple_material"
    bl_label = "Create/Reset Material"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        multiple_material(context)
        return {'FINISHED'}










########## Creates Tri Lights Setup ##########

def lights_setup(context):
    
    # Deletes previous light sources with the same name
    for obj in bpy.data.objects:
        if obj.name.startswith("PixelArt_Light_"):
            bpy.data.objects.remove(obj, do_unlink = True)

    for light in bpy.data.lights:
        if light.name.startswith("PixelArt_Light_"):
            bpy.data.lights.remove(light, do_unlink = True)
        

    # Creates the red light
    lightR = bpy.data.lights.new(name = "PixelArt_Light_R", type = 'POINT')
    lightR.color = (1,0,0)
    lightR.energy = 250
    lightR_object = bpy.data.objects.new(name = "PixelArt_Light_R", object_data = lightR)
    bpy.context.collection.objects.link(lightR_object)
    lightR_object.location = (3.46, -0.41, 1.04)

    # Creates the green light
    lightG = bpy.data.lights.new(name = "PixelArt_Light_G", type = 'POINT')
    lightG.color = (0,1,0)
    lightG.energy = 250
    lightG_object = bpy.data.objects.new(name = "PixelArt_Light_G", object_data = lightG)
    bpy.context.collection.objects.link(lightG_object)
    lightG_object.location = (-2.1, 2, 1.37)

    # Creates the blue light
    lightB = bpy.data.lights.new(name = "PixelArt_Light_B", type = 'POINT')
    lightB.color = (0,0,1)
    lightB.energy = 150
    lightB_object = bpy.data.objects.new(name = "PixelArt_Light_B", object_data = lightB)
    bpy.context.collection.objects.link(lightB_object)
    lightB_object.location = (-0.06, -1.46, 2.18)

    # Removes world light
    bpy.data.worlds["World"].node_tree.nodes["Background"].inputs[1].default_value = 0





class PIXEL_ART_OT_lights_setup(bpy.types.Operator):
    """Creates a setup of three point lights to work with the multiple lights pixel art material"""
    bl_idname = "render.lights_setup"
    bl_label = "Tri Light Setup"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        lights_setup(context)
        return {'FINISHED'}










########## Creating the UI panel ##########

class PIXEL_RENDER_PT_pixel_render_panel(bpy.types.Panel):
    """Creates a Panel for Pixel Rendering in the UI Panels"""
    bl_label = "Pixel Render"
    bl_idname = "PIXEL_RENDER_PT_pixel_render_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Pixel Render"


    def draw(self, context):
        layout = self.layout
        scene = context.scene     
        
        box = layout.box()
        row = box.row()
        row.alignment = 'CENTER'          
        row.label(text="Render Settings for Pixel Art", icon = "RESTRICT_RENDER_OFF")
        row = box.row()
        row = box.row()
        row.scale_y = 1.5
        row.operator("render.render_settings")
        row = box.row()
        row = layout.row()

        box = layout.box()
        row = box.row()
        row.alignment = 'CENTER'          
        row.label(text="Default Pixel Art Material", icon = "MATERIAL")
        row = box.row()
        row = box.row()
        row.scale_y = 1.5
        row.operator("render.single_material")
        row = box.row()
        row = layout.row()
        
        
        box = layout.box()
        row = box.row()
        row.alignment = 'CENTER'          
        row.label(text="Multiple Lights Material", icon = "NODE_MATERIAL")
        row = box.row()
        row = box.row()
        row.scale_y = 1.5
        row.operator("render.multiple_material")
        row = box.row()
        row.scale_y = 1
        row.operator("render.lights_setup")   











def register():
    bpy.utils.register_class(PIXEL_ART_OT_render_settings)
    bpy.utils.register_class(PIXEL_ART_OT_single_material)
    bpy.utils.register_class(PIXEL_ART_OT_multiple_material)
    bpy.utils.register_class(PIXEL_ART_OT_lights_setup)
    bpy.utils.register_class(PIXEL_RENDER_PT_pixel_render_panel)


def unregister():
    # Fixed: was using register_class instead of unregister_class
    bpy.utils.unregister_class(PIXEL_ART_OT_render_settings)
    bpy.utils.unregister_class(PIXEL_ART_OT_single_material)
    bpy.utils.unregister_class(PIXEL_ART_OT_multiple_material)
    bpy.utils.unregister_class(PIXEL_ART_OT_lights_setup)
    bpy.utils.unregister_class(PIXEL_RENDER_PT_pixel_render_panel)



if __name__ == "__main__":
    register()