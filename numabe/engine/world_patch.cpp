// World Patch Utilities — helper functions for querying terrain patch data
// across the entire world. Used by the editor to determine material usage
// before allowing material deletion or replacement.
//
// NOTE: This is an extracted/refactored version of code from world.cpp.
// Currently NOT compiled (not in makefile). The active implementation is in world.cpp line 5719.

// IsMaterialUsedInWorld: Check if a given material ID is used anywhere in the world
//
// WHY: The editor needs to validate material deletions — can't delete a material
// that's actively used by meshes or instances, as that would corrupt visual data.
//
// Algorithm:
//   1. Iterate all meshes in world, check each face's visual field (low byte = material ID)
//   2. Collect all mesh instances via CollectMeshInsts(), check instance material overrides
//      (INST_MATID flag indicates instance has per-instance material)
//
// Returns: true if ANY face or instance uses mat_id, false otherwise
//
// NOTE: This version includes INST_MATID instance checking (more complete than world.cpp version)
bool IsMaterialUsedInWorld(World* w, int mat_id)
{
	if (!w) return false;
	
	// 1. Check direct mesh usage
	Mesh* m = w->head_mesh;
	while (m)
	{
		Face* f = m->head_face;
		while (f)
		{
			if ((f->visual & 0xFF) == mat_id) return true;
			f = f->next;
		}
		m = m->next;
	}

	// 2. Check instance usage (using iterators or collection if available)
	// Since we don't have a simple list, we can use CollectMeshInsts or just rely on Mesh sharing lists if implemented.
	// world.h has: int CollectMeshInsts(World* w, Inst*** out);
	Inst** insts = 0;
	int count = CollectMeshInsts(w, &insts);
	if (insts)
	{
		for (int i=0; i<count; i++)
		{
			if (insts[i] && (GetInstFlags(insts[i]) & INST_MATID))
			{
				if (GetInstMaterialID(insts[i]) == mat_id) {
					free(insts);
					return true;
				}
			}
		}
		free(insts);
	}

	return false;
}
