# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# It is recommended that this script is used with Blender 3.6.1 or 
# later because of improvements to the UV unwrapping and packing 
# performance in these versions.

import bpy
import math
import time
import datetime

starttime = datetime.datetime.now()
print("Cleanup started at ", starttime)

# user controls
bake = True
res = 8192
rem_doub = True
emission_shader = False
limited_dissolve = False
hide_old_data = True
delete_old_data = False
uv_margin = 0.001
bake_margin = 2
cage_extrusion = 0.01
shade_flat = True
save_image_disk = True

# define collections, user should start with one collection selected in the outliner
act_coll = bpy.context.view_layer.active_layer_collection.collection

# check if empty
if not act_coll.objects:
    def draw(self, context):
        self.layout.label(text="The active collection is empty!")
        self.layout.label(text="Please select the collection with the photogrammetry objects.")

    bpy.context.window_manager.popup_menu(draw, title="No Objects Found", icon='ERROR')
    raise Exception("Active collection is empty. Please select the correct collection.")

comb_coll = bpy.data.collections.new(f'{act_coll.name}_combined')
clean_coll = bpy.data.collections.new(f'{act_coll.name}_cleanup')

# link to scene
bpy.context.scene.collection.children.link(comb_coll)
bpy.context.scene.collection.children.link(clean_coll)

#Recursivly transverse layer_collection for a particular name
def recurLayerCollection(layerColl, collName):
    found = None
    if (layerColl.name == collName):
        return layerColl
    for layer in layerColl.children:
        found = recurLayerCollection(layer, collName)
        if found:
            return found

# clean out non-mesh objects
for ob in act_coll.objects:
    if ob.type != 'MESH':
        bpy.data.objects.remove(ob, do_unlink=True)

# create clean mesh collection and copy data into it (unlinked)
for ob in act_coll.objects:
    obj_copy = ob.copy()
    obj_copy.name = 'Mesh Combined'
    obj_copy.data = ob.data.copy()

    # NOTE the use of 'coll.name' to account for potential automatic renaming
    layer_collection = bpy.context.view_layer.layer_collection.children[comb_coll.name]
    bpy.context.view_layer.active_layer_collection = layer_collection
    bpy.context.collection.objects.link(obj_copy)
    
# create bake collection and copy data into it (unlinked)
for ob in act_coll.objects:
    obj_copy = ob.copy()
    obj_copy.name = 'Mesh Cleanup'
    obj_copy.data = ob.data.copy()

    # NOTE the use of 'collection.name' to account for potential automatic renaming
    layer_collection = bpy.context.view_layer.layer_collection.children[clean_coll.name]
    bpy.context.view_layer.active_layer_collection = layer_collection
    bpy.context.collection.objects.link(obj_copy)

# get 3D view context
for window in bpy.context.window_manager.windows:
    screen = window.screen

    for area in screen.areas:
        if area.type == 'VIEW_3D':
            override = {'window': window, 'screen': screen, 'area': area}
            break
        
#delete old data
if delete_old_data == True:
    print('Step 0 | Deleting duplicate objects.')
    for ob in act_coll.objects:
        bpy.data.objects.remove(ob, do_unlink=True)

# set an active object
bpy.context.view_layer.objects.active = comb_coll.objects[0]

# join objects in bake collection
print("Step 1 | Joining objects, this could take a little while if there are many objects.")

for ob in comb_coll.objects:
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
bpy.ops.object.join()
bpy.context.object.name = f'{act_coll.name}_combined'
bpy.context.object.data.name = f'{act_coll.name}_combined'

# define objects in new clean collection
objs = clean_coll.all_objects
num_objs = len(objs)

for obj in bpy.context.selected_objects:
    obj.select_set(False)

# select and join objects
for ob in objs:        
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
bpy.ops.object.join()
bpy.context.object.name = f'{act_coll.name}_cleanup'
bpy.context.object.data.name = f'{act_coll.name}_cleanup'

# remove doubles
if rem_doub == True:
    print('Step 1.1 | Removing doubles.')
    bpy.ops.object.editmode_toggle()
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles()
    bpy.ops.object.editmode_toggle()

idx = 0
slots_length = len(bpy.context.object.material_slots)

# remove all new materials from the original object
print('Step 2 | Removing materials from duplicate.')
for i in range(0,len(bpy.context.object.material_slots)):
    bpy.context.object.active_material_index = 1
    bpy.ops.object.material_slot_remove()
    print(
    "Step 2 |", idx + 1,
    "/", num_objs,
    "|", round((((idx + 1)/slots_length) * 100),2),"%",
    "| Removed material."  
    )
    idx += 1
    
bpy.ops.object.mode_set(mode = 'EDIT') 
bpy.ops.mesh.select_all(action = 'SELECT')
bpy.ops.object.material_slot_assign()
bpy.ops.object.mode_set(mode = 'OBJECT')

# add a material to the clean bake object
clean = clean_coll.objects[0]
print('Step 3 | Creating new material.')
mat = bpy.data.materials.new(name=f'{act_coll.name}_material')

# add the material to the object
clean.data.materials.append(mat)
mat.use_nodes = True
principlednode = mat.node_tree.nodes["Principled BSDF"]
#principlednode.inputs[5].default_value = 0
#principlednode.inputs[7].default_value = 0
#principlednode.inputs[9].default_value = 1
outputnode = mat.node_tree.nodes['Material Output']

# add an emission shader
emissionnode = mat.node_tree.nodes.new(type="ShaderNodeEmission")
emissionnode.location = (500, 0)

# add image texture
texture = bpy.ops.image.new(name=f'{act_coll.name}_texture',width=res,height=res,color=(0.0,0.0,0.0,0.0), alpha=True)

# add texture node
texturenode = mat.node_tree.nodes.new(type = 'ShaderNodeTexImage')
texturenode.image = bpy.data.images[f'{act_coll.name}_texture']
texturenode.location = (-300,200)

# UV Unwrapping
print('Step 4 | UV Unwrapping.')
bpy.ops.object.editmode_toggle()
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(island_margin=0)
bpy.ops.uv.pack_islands(margin=0)
bpy.ops.object.editmode_toggle()

# bake settings
# set renderer to cycles
bpy.context.scene.render.engine = 'CYCLES'
# influence settings
bpy.context.scene.render.bake.use_pass_direct = False
bpy.context.scene.render.bake.use_pass_indirect = False
bpy.context.scene.render.bake.use_pass_color = True
# enable selected to active
bpy.context.scene.render.bake.use_selected_to_active = True
# set cage distance
bpy.context.scene.render.bake.cage_extrusion = cage_extrusion
# set bake margin
bpy.context.scene.render.bake.margin = bake_margin
# cycles render settings
bpy.context.scene.cycles.adaptive_threshold = 1.0
bpy.context.scene.cycles.samples = 10
bpy.context.scene.cycles.time_limit = 5

orig = comb_coll.objects[0]

# select original
orig.select_set(True)
# select active
clean.select_set(True)

if shade_flat == True:
    bpy.ops.object.shade_flat()

# bake
if bake == True:
    # --- New Logic Here ---
    # First, check for an emission shader in the original materials
    has_emission = False
    for ob in comb_coll.objects:
        for mat_slot in ob.material_slots:
            if mat_slot.material and mat_slot.material.use_nodes:
                if 'Emission' in mat_slot.material.node_tree.nodes:
                    has_emission = True
                    break
            if has_emission:
                break
        if has_emission:
            break

    bake_type = 'DIFFUSE'
    if has_emission:
        bake_type = 'EMIT'
        print("Detected emission shader, setting bake type to 'EMIT'.")
    else:
        print("No emission shader detected, setting bake type to 'DIFFUSE'.")

    poly_count = len(bpy.context.object.data.polygons)
    print('Step 5 | Baking texture, this may take some time...')

    print('This process took about 45 minutes using a 2012 desktop on a mesh with 3800000 triangles.')
    print('Your mesh has ', poly_count, 'triangles.')
    print('Assuming your computer is as slow as mine, this could take', 
    str(round((poly_count/3800000/45*3600), 2)), 
    "minutes...")
    print(datetime.datetime.now())
    
    bpy.context.object.data.uv_layers[0].active_render = True
    # The actual bake command with the conditional type
    bpy.ops.object.bake(type=bake_type)
    print('The new image is called:',f'{act_coll.name}_texture')

    # connect nodes
    if emission_shader == True:
        mat.node_tree.links.new(texturenode.outputs[0], emissionnode.inputs[0])
        mat.node_tree.links.new(emissionnode.outputs[0], outputnode.inputs[0])
    else:
        mat.node_tree.links.new(texturenode.outputs[0], principlednode.inputs[0])

# hide data
if hide_old_data == True:  
    print('Step 6 | Hiding unused collections.')
    
    # hide original collections when finished    
    layer_collection = bpy.context.view_layer.layer_collection    
    layerColl = recurLayerCollection(layer_collection, act_coll.name)
    bpy.context.view_layer.active_layer_collection = layerColl
    bpy.context.view_layer.active_layer_collection.exclude = True
    
    layer_collection = bpy.context.view_layer.layer_collection 
    layerColl = recurLayerCollection(layer_collection, comb_coll.name)
    bpy.context.view_layer.active_layer_collection = layerColl
    bpy.context.view_layer.active_layer_collection.exclude = True
    
    # give feedback on process duration
    endtime = datetime.datetime.now()
    duration = endtime - starttime

# delete old data
if delete_old_data == True:
    print('Step 7 | Deleting old data.')
    for ob in comb_coll.objects:
        bpy.data.objects.remove(ob, do_unlink=True)    

print('cleanup started at',starttime)
print('cleanup finished at',endtime)
print('process duration: ', duration)