// world_mesh.h — Mesh loading and ownership
//
// Extracted from engine/world.cpp: Mesh::Update, mesh load/update helpers.
// Owns the PLY mesh file parser and the LoadMesh/FindOrLoadMesh/UpdateMesh
// free functions.
//
// SEE ALSO:
// - engine/world_mesh.cpp — implementation
// - engine/world.h — Mesh forward declaration, World class mesh methods

#pragma once

struct World;
struct Mesh;

// Load a mesh from a .akm (PLY-format) file and add it to the world.
// Returns pointer to the loaded mesh, or 0 on failure.
Mesh* LoadMesh(World* w, const char* path, const char* name);

// Find a mesh by name, or load it if not found (O(n) scan — deduplicates).
Mesh* FindOrLoadMesh(World* w, const char* path, const char* name);

// Update an existing mesh by re-loading from file (hot-reload support).
bool UpdateMesh(Mesh* m, const char* path);
