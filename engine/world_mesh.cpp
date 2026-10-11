// world_mesh.cpp — Mesh loading and ownership
//
// Extracted from engine/world.cpp: Mesh::Update, LoadMesh, FindOrLoadMesh,
// UpdateMesh.
//
// Owns the PLY-format (.akm) mesh file loader and the top-level mesh
// load/find/update free functions.
//
// SEE ALSO:
// - engine/world_mesh.h — header
// - engine/world.h — Mesh forward declaration, World::AddMesh/DeleteMesh

#include "world_internal.h"

bool Mesh::Update(const char* path)
{
	if (strstr(path,".akm"))
	{
		int bio = 1;
	}
    FILE* f = fopen(path,"rt");
	if (!f)
		return false;

	int plannar = 0x7 | 0x8;

	char buf[1024];
	char tail_str[1024];

	int num_verts = -1;
	int num_faces = -1;
	int element = 0;

	bool face_props = false;
	
	// FIX: Use a mapping array to handle flexible property order and ignore unknown properties (like normals)
    // 0:skip, 1:x, 2:y, 3:z, 4:r, 5:g, 6:b, 7:a
    int prop_types[32]; 
    int prop_count = 0;

	// [DATA-CONTRACT:AKM] PLY header signature line: must be "ply"
	while (fgets(buf, 1024, f))
	{
		int len = (int)strlen(buf);
		while (len && (buf[len - 1] == ' ' || buf[len - 1] == '\r' || buf[len - 1] == '\n' || buf[len - 1] == '\t' || buf[len - 1] == '\v'))
			len--;
		if (!len)
			continue;
		buf[len] = 0;


		if (strcmp(buf, "ply"))
		{
			fclose(f);
			return false;
		}
		else
			break;
	}

	// [DATA-CONTRACT:AKM] PLY format declaration: must be "format ascii 1.0"
	// WHY ascii: Human-readable for debugging, easier to hand-edit, compatible with
	// all PLY parsers. Binary PLY would be faster but harder to troubleshoot.
	while (fgets(buf, 1024, f))
	{
		int len = (int)strlen(buf);
		while (len && (buf[len - 1] == ' ' || buf[len - 1] == '\r' || buf[len - 1] == '\n' || buf[len - 1] == '\t' || buf[len - 1] == '\v'))
			len--;
		if (!len)
			continue;
		buf[len] = 0;

		if (strcmp(buf, "format ascii 1.0"))
		{
			fclose(f);
			return false;
		}
		else
			break;
	}

	// [DATA-CONTRACT:AKM] PLY element/property declarations (vertex and face metadata)
	// mesh header
	while (fgets(buf, 1024, f))
	{
		int len = (int)strlen(buf);
		while (len && (buf[len - 1] == ' ' || buf[len - 1] == '\r' || buf[len - 1] == '\n' || buf[len - 1] == '\t' || buf[len - 1] == '\v'))
			len--;
		if (!len)
			continue;
		buf[len] = 0;

		if (strncmp(buf, "comment", 7) == 0 && (buf[7] == 0 || buf[7] == ' ' || buf[7] == '\t' || buf[7] == '\r' || buf[7] == '\n'))
			continue;

		if (strncmp(buf, "element vertex ",15) == 0)
		{
			if (num_verts >= 0)
			{
				fclose(f);
				return false;
			}

			if (sscanf(buf+15, "%d", &num_verts) != 1 || num_verts < 0)
			{
				fclose(f);
				return false;
			}

			element = 'V';
			prop_count = 0; // Reset property count for vertices

			continue;
		}

		if (strncmp(buf, "element face ",13) == 0)
		{
			if (num_faces >= 0)
			{
				fclose(f);
				return false;
			}

			if (sscanf(buf+13, "%d", &num_faces) != 1 || num_faces < 0)
			{
				fclose(f);
				return false;
			}

			element = 'F';

			continue;
		}

		if (strncmp(buf, "property ", 9) == 0)
		{
			if (element == 'F')
			{
				if (strcmp(buf + 9, "list uchar uint vertex_indices") != 0)
				{
					fclose(f);
					return false;
				}

				face_props = true;
				continue;
			}
			else
			if (element == 'V')
			{
				// FIX: Map properties to types, ignoring unknowns.
				// Parse the property name explicitly to avoid matching nx/ny/nz as x/y/z.
				if (prop_count < 32)
				{
					char prop_type[64];
					char prop_name[64];
					if (sscanf(buf + 9, "%63s %63s", prop_type, prop_name) == 2)
					{
						if (strcmp(prop_name, "x") == 0) prop_types[prop_count++] = 1;
						else if (strcmp(prop_name, "y") == 0) prop_types[prop_count++] = 2;
						else if (strcmp(prop_name, "z") == 0) prop_types[prop_count++] = 3;
						else if (strcmp(prop_name, "red") == 0 || strcmp(prop_name, "diffuse_red") == 0) prop_types[prop_count++] = 4;
						else if (strcmp(prop_name, "green") == 0 || strcmp(prop_name, "diffuse_green") == 0) prop_types[prop_count++] = 5;
						else if (strcmp(prop_name, "blue") == 0 || strcmp(prop_name, "diffuse_blue") == 0) prop_types[prop_count++] = 6;
						else if (strcmp(prop_name, "alpha") == 0) prop_types[prop_count++] = 7;
						else prop_types[prop_count++] = 0; // Skip
					}
					else
					{
						fclose(f);
						return false;
					}
				}

				continue;
			}
			else
			{
				fclose(f);
				return false;
			}
		}

		if (strcmp(buf, "end_header") == 0)
		{
			// FIX: Check if we have at least X, Y, Z (types 1, 2, 3)
			bool has_x = false;
			bool has_y = false;
			bool has_z = false;
			for (int i = 0; i < prop_count; ++i)
			{
				if (prop_types[i] == 1) has_x = true;
				else if (prop_types[i] == 2) has_y = true;
				else if (prop_types[i] == 3) has_z = true;
			}
			if (num_faces <= 0 || num_verts <= 0 || !face_props || !has_x || !has_y || !has_z)
			{
				fclose(f);
				return false;
			}
			break;
		}
		else
		{
			fclose(f);
			return false;
		}
	}

	Vert** index = (Vert**)malloc(sizeof(Vert*)*num_verts);

	// [DATA-CONTRACT:AKM] Vertex data parsing (ASCII, one vertex per line)
	// WHY flexible property order: Different 3D exporters (Blender, etc.) may output
	// properties in different orders or include extras like normals. prop_types mapping
	// ensures we extract x/y/z/r/g/b/a regardless of order, skipping unknown props.
	// verts
	while (fgets(buf, 1024, f))
	{
		int len = (int)strlen(buf);
		while (len && (buf[len - 1] == ' ' || buf[len - 1] == '\r' || buf[len - 1] == '\n' || buf[len - 1] == '\t' || buf[len - 1] == '\v'))
			len--;
		if (!len)
			continue;
		buf[len] = 0;

		if (strncmp(buf, "comment", 7) == 0 && (buf[7] == 0 || buf[7] == ' ' || buf[7] == '\t' || buf[7] == '\r' || buf[7] == '\n'))
			continue;

		float x=0, y=0, z=0;
		int r=255, g=255, b=255, a=255;

		// FIX: Parse based on property mapping
        char* p = buf;
        for (int i = 0; i < prop_count; i++)
        {
            // Skip whitespace
            while (*p == ' ' || *p == '\t') p++;
            if (*p == 0) break; // Should not happen if file is valid

            if (prop_types[i] == 0) // Skip
            {
                 // Move to next token
                 while (*p != ' ' && *p != '\t' && *p != 0) p++;
            }
            else if (prop_types[i] == 1) // x
            {
                x = (float)strtod(p, &p);
            }
            else if (prop_types[i] == 2) // y
            {
                y = (float)strtod(p, &p);
            }
            else if (prop_types[i] == 3) // z
            {
                z = (float)strtod(p, &p);
            }
            else if (prop_types[i] == 4) // r
            {
                r = (int)strtol(p, &p, 10);
            }
            else if (prop_types[i] == 5) // g
            {
                g = (int)strtol(p, &p, 10);
            }
            else if (prop_types[i] == 6) // b
            {
                b = (int)strtol(p, &p, 10);
            }
            else if (prop_types[i] == 7) // a
            {
                a = (int)strtol(p, &p, 10);
            }
        }

		Vert* v = (Vert*)malloc(sizeof(Vert));
		index[verts] = v;

		v->xyzw[0] = x;
		v->xyzw[1] = y;
		v->xyzw[2] = z;
		v->xyzw[3] = 1;
		v->rgba[0] = r;
		v->rgba[1] = g;
		v->rgba[2] = b;
		v->rgba[3] = a;

		if (verts && plannar)
		{
			if (plannar & 1)
			{
				if (v->xyzw[0] != head_vert->xyzw[0])
					plannar &= ~1;
			}
			if (plannar & 2)
			{
				if (v->xyzw[1] != head_vert->xyzw[1])
					plannar &= ~2;
			}
			if (plannar & 4)
			{
				if (v->xyzw[2] != head_vert->xyzw[2])
					plannar &= ~4;
			}
			if (plannar & 8)
			{
				if (v->rgba[0] != head_vert->rgba[0] ||
					v->rgba[1] != head_vert->rgba[1] ||
					v->rgba[2] != head_vert->rgba[2])
				{
					plannar &= ~8;
				}
			}
		}

		v->mesh = this;
		v->face_list = 0;
		v->line_list = 0;
		v->next = 0;
		v->prev = tail_vert;
		if (tail_vert)
			tail_vert->next = v;
		else
			head_vert = v;
		tail_vert = v;

		if (!verts)
		{
			bbox[0] = v->xyzw[0];
			bbox[1] = v->xyzw[0];
			bbox[2] = v->xyzw[1];
			bbox[3] = v->xyzw[1];
			bbox[4] = v->xyzw[2];
			bbox[5] = v->xyzw[2];
		}
		else
		{
			bbox[0] = fminf(bbox[0], v->xyzw[0]);
			bbox[1] = fmaxf(bbox[1], v->xyzw[0]);
			bbox[2] = fminf(bbox[2], v->xyzw[1]);
			bbox[3] = fmaxf(bbox[3], v->xyzw[1]);
			bbox[4] = fminf(bbox[4], v->xyzw[2]);
			bbox[5] = fmaxf(bbox[5], v->xyzw[2]);
		}

		v->sel = false;

			verts++;

		if (verts == num_verts)
			break;
	}

	// [DATA-CONTRACT:AKM] Face data parsing (ASCII, one face per line)
	// Format: "3 <v0_idx> <v1_idx> <v2_idx> [<visual>]"
	// WHY per-face visual field: Stores material ID and shading/elevation per triangle
	// (uint32: matid_8bits + 3×(shade_7bits + elev_1bit)). Enables terrain texture
	// blending and per-triangle material variation within single mesh.
	// faces
    int polys=0;
	while (fgets(buf, 1024, f))
	{
		int len = (int)strlen(buf);
		while (len && (buf[len] == ' ' || buf[len] == '\r' || buf[len] == '\n' || buf[len] == '\t' || buf[len] == '\v'))
			len--;
		if (!len)
			continue;
		buf[len] = 0;

		if (strncmp(buf, "comment", 7) == 0 && (buf[7] == 0 || buf[7] == ' ' || buf[7] == '\t' || buf[7] == '\r' || buf[7] == '\n'))
			continue;

        int vv[4];
		int n;

		if (sscanf(buf, "%d %d %d %d %d %s", &n, vv+0,  vv+1,  vv+2,  vv+3, tail_str) < 3 || 
			n != 2 && n != 3 && n != 4 && n != -3 && n != -4)
		{
 			free(index);
			fclose(f);
			return false;
		}

		// FREESTYLE
		bool freestyle = false;
		if (n<0)
		{
			freestyle = true;
			n=-n;
		}

		if (n>2)
		{
			for (int v1=0; v1<n; v1++)
			{
				if (vv[v1]<0 || vv[v1]>=num_verts)
				{
					free(index);
					fclose(f);
					return false;            
				}  

				if (v1==n-1)
					break;

				for (int v2=v1+1; v2<n; v2++)
				{
					if (vv[v1]==vv[v2])
					{
						free(index);
						fclose(f);
						return false;            
					}            
				}
			}
		
			////////////////
			for (int t=0; t<n-2; t++)
			{
				Face* f = (Face*)malloc(sizeof(Face));

				int abc[3] = { vv[0],vv[t+1],vv[t+2] };

				f->visual = freestyle ? (1<<30) : 0; // highest bit is line, next is freestyle
				f->mesh = this;
				f->freestyle = freestyle;

				f->next = 0;
				f->prev = tail_face;
				if (tail_face)
					tail_face->next = f;
				else
					head_face = f;
				tail_face = f;

				for (int i = 0; i < 3; i++)
				{
					f->abc[i] = index[abc[i]];
					f->share_next[i] = f->abc[i]->face_list;
					f->abc[i]->face_list = f;
				}

				faces++;
			}
		}
		else
		if (n==2)
		{
			if (vv[0]<0 || vv[0]>=num_verts || 
				vv[1]<0 || vv[1]>=num_verts ||
				vv[0] == vv[1])
			{
				free(index);
				fclose(f);
				return false;            
			}  

			Line* l = (Line*)malloc(sizeof(Line));
			l->ab[0] = index[vv[0]];
			l->ab[1] = index[vv[1]];

			l->visual = freestyle ? (3<<30) : (2<<30); // highest bit is line, next is freestyle
			l->mesh = this;

			l->next = 0;
			l->prev = tail_line;
			if (tail_line)
				tail_line->next = l;
			else
				head_line = l;
			tail_line = l;

			lines++;
		}
		
        polys++;
        if (polys == num_faces)
			break;
	}

	free(index);

	// tail
	while (fgets(buf, 1024, f))
	{
		int len = (int)strlen(buf);
		while (len && (buf[len - 1] == ' ' || buf[len - 1] == '\r' || buf[len - 1] == '\n' || buf[len - 1] == '\t' || buf[len - 1] == '\v'))
			len--;
		if (!len)
			continue;
		buf[len] = 0;

		if (strncmp(buf, "comment", 7) == 0 && (buf[7] == 0 || buf[7] == ' ' || buf[7] == '\t' || buf[7] == '\r' || buf[7] == '\n'))
			continue;

		fclose(f);
		return false;
	}


    fclose(f);
    return true;
}

// Summary: Wrapper around AddMesh/Update to load a mesh from a file.
Mesh* World::LoadMesh(const char* path, const char* name)
{
    Mesh* m = AddMesh(name ? name : path);

    if (!m->Update(path))
    {
        DeleteMesh(m);
        return 0;
    }

    return m;
}

Mesh* LoadMesh(World* w, const char* path, const char* name)
{
    return w ? w->LoadMesh(path, name) : 0;
}

Mesh* FindOrLoadMesh(World* w, const char* path, const char* name)
{
    return w ? w->FindOrLoadMesh(path, name) : 0;
}

bool UpdateMesh(Mesh* m, const char* path)
{
    return m ? m->Update(path) : false;
}
