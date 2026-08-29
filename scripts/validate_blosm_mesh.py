import bpy
import bmesh

def validate_object(obj, max_faces=None, check_manifold=True):
    """
    Validates a single Blender object for export.
    Checks for:
    - Mesh data existence
    - Manifold geometry (watertight) - Optional
    - Poly count limits
    
    Args:
        obj: The Blender object to validate
        max_faces: Optional integer limit for face count
        check_manifold: Boolean, whether to enforce watertight geometry
        
    Returns:
        dict: {'valid': bool, 'errors': [str]}
    """
    results = {
        'valid': True,
        'errors': []
    }
    
    if obj.type != 'MESH':
        results['valid'] = False
        results['errors'].append(f"Object {obj.name} is not a mesh (Type: {obj.type})")
        return results
        
    if obj.data is None:
        results['valid'] = False
        results['errors'].append(f"Object {obj.name} has no mesh data")
        return results
        
    # Check Poly Count
    # We use loop triangles or polygons depending on what we care about. 
    # Usually face count (polygons) is the user-facing metric.
    face_count = len(obj.data.polygons)
    if max_faces is not None and face_count > max_faces:
        results['valid'] = False
        results['errors'].append(f"Poly count ({face_count}) exceeds limit ({max_faces})")
        
    if not check_manifold:
        return results

    # Check Manifold (Watertight)
    # We need to create a bmesh to check this efficiently
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    
    non_manifold_edges = [e for e in bm.edges if not e.is_manifold]
    non_manifold_verts = [v for v in bm.verts if not v.is_manifold]
    print(
        f"DEBUG_VALIDATE: {obj.name}: "
        f"{len(non_manifold_edges)} non-manifold edges, "
        f"{len(non_manifold_verts)} non-manifold verts. "
        f"check_manifold={check_manifold}"
    )

    if non_manifold_edges or non_manifold_verts:
        # It might be open, which is sometimes okay, but for 'watertight' validation it's not.
        results['valid'] = False
        if non_manifold_edges:
            results['errors'].append(f"Mesh is not manifold ({len(non_manifold_edges)} non-manifold edges)")
        else:
            results['errors'].append(f"Mesh is not manifold ({len(non_manifold_verts)} non-manifold verts)")
        
    # Clean up bmesh
    bm.free()
    
    return results
