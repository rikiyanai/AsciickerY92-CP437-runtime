// world_serialization_a3d.cpp — .a3d file save/load
//
// Extracted from engine/world.cpp.
// Contains: SaveWorld, LoadWorld, ResolveMeshAssetPath,
// WorldGetPlayerStart, WorldSetPlayerStart, and serialization helpers.
//
// Minimap markers moved to engine/world_minimap_markers.cpp.
//
// SEE ALSO: world.h, world_internal.h, a3d_load_context.h

#include "world_internal.h"
#include "a3d_load_context.h"
#include "enemygen.h"
#include "inventory.h"

#include <sys/stat.h>
#include <errno.h>

// ============================================================================
// ResolveMeshAssetPath — mesh file path resolution
// ============================================================================

static bool ReadActiveMeshRoot(char* out, int out_size, const char* base_path)
{
    if (!out || out_size <= 0)
        return false;

    const char* env_root = getenv("ASCIICKER_ACTIVE_MESH_ROOT");
    if (env_root && env_root[0])
    {
        if (env_root[0] == '/' || (strlen(env_root) > 1 && env_root[1] == ':'))
            snprintf(out, out_size, "%s", env_root);
        else
            snprintf(out, out_size, "%s%s", base_path ? base_path : "", env_root);
        return true;
    }

    char pointer_path[4096];
    snprintf(pointer_path, sizeof(pointer_path), "%sassets/meshes/osm_runs/.active_mesh_root",
        base_path ? base_path : "");
    FILE* f = fopen(pointer_path, "rb");
    if (!f)
        return false;

    char rel_root[4096];
    bool ok = false;
    if (fgets(rel_root, sizeof(rel_root), f))
    {
        int len = (int)strlen(rel_root);
        while (len > 0 && isspace((unsigned char)rel_root[len - 1]))
        {
            rel_root[len - 1] = 0;
            len--;
        }
        if (rel_root[0])
        {
            if (rel_root[0] == '/' || (strlen(rel_root) > 1 && rel_root[1] == ':'))
                snprintf(out, out_size, "%s", rel_root);
            else
                snprintf(out, out_size, "%s%s", base_path ? base_path : "", rel_root);
            ok = true;
        }
    }
    fclose(f);
    return ok;
}

bool ResolveMeshAssetPath(char* out, int out_size, const char* base_path, const char* mesh_name)
{
    if (!out || out_size <= 0 || !mesh_name || !mesh_name[0])
        return false;

    char active_root[4096];
    if (ReadActiveMeshRoot(active_root, sizeof(active_root), base_path))
    {
        struct stat st;
        snprintf(out, out_size, "%s/%s", active_root, mesh_name);
        if (stat(out, &st) == 0 && S_ISREG(st.st_mode))
            return true;
    }

    char fixture_path[4096];
    snprintf(fixture_path,
             sizeof(fixture_path),
             "%sassets/meshes/fixtures/%s",
             base_path ? base_path : "",
             mesh_name);
    struct stat st;
    if (stat(fixture_path, &st) == 0 && S_ISREG(st.st_mode))
    {
        snprintf(out, out_size, "%s", fixture_path);
        return true;
    }

    snprintf(out,
             out_size,
             "%sassets/meshes/%s",
             base_path ? base_path : "",
             mesh_name);
    return true;
}

// Minimap marker storage moved to engine/world_minimap_markers.cpp.
// Load/Save/Free/Get* functions are declared in world_minimap_markers.h
// and included transitively via world_internal.h.

// ============================================================================
// SaveWorld
// ============================================================================

static void SaveInst(Inst* inst, FILE* f)
{
    if (!inst || !f)
        return;
    if (inst->flags & INST_FLAGS::INST_VOLATILE)
        return;

    if (inst->inst_type == Inst::INST_TYPE::MESH)
    {
        MeshInst* i = (MeshInst*)inst;
        int mesh_id_len = i->mesh && i->mesh->name ? (int)strlen(i->mesh->name) : 0;
        fwrite(&mesh_id_len, 1, 4, f);
        if (mesh_id_len)
            fwrite(i->mesh->name, 1, mesh_id_len, f);

        int inst_name_len = i->name ? (int)strlen(i->name) : 0;
        fwrite(&inst_name_len, 1, 4, f);
        if (inst_name_len)
            fwrite(i->name, 1, inst_name_len, f);

        fwrite(i->tm, 1, 16 * 8, f);
        fwrite(&i->flags, 1, 4, f);
        fwrite(&i->story_id, 1, 4, f);
    }
    else if (inst->inst_type == Inst::INST_TYPE::SPRITE)
    {
        SpriteInst* i = (SpriteInst*)inst;
        int mesh_id_len = -1; // identify sprite
        fwrite(&mesh_id_len, 1, 4, f);

        int inst_name_len = (i->sprite && i->sprite->name) ? (int)strlen(i->sprite->name) : 0;
        fwrite(&inst_name_len, 1, 4, f);
        if (inst_name_len)
            fwrite(i->sprite->name, 1, inst_name_len, f);

        fwrite(i->pos, 1, sizeof(float[3]), f);
        fwrite(&i->yaw, 1, sizeof(float), f);
        fwrite(&i->anim, 1, sizeof(int), f);
        fwrite(&i->frame, 1, sizeof(int), f);
        fwrite(&i->reps, 1, sizeof(int[4]), f);
        fwrite(&i->flags, 1, 4, f);
        fwrite(&i->story_id, 1, 4, f);
    }
    else if (inst->inst_type == Inst::INST_TYPE::ITEM)
    {
        ItemInst* i = (ItemInst*)inst;
        int mesh_id_len = -2; // identify item
        fwrite(&mesh_id_len, 1, 4, f);

        int item_definition_id = (int)i->item->item_definition_id;
        int visual_style_id = (int)i->item->visual_style_id;
        int presentation_kind_id = (int)i->item->presentation_kind_id;
        fwrite(&item_definition_id, 1, sizeof(int), f);
        fwrite(&visual_style_id, 1, sizeof(int), f);
        fwrite(&presentation_kind_id, 1, sizeof(int), f);
        fwrite(&i->item->count, 1, sizeof(int), f);

        fwrite(i->pos, 1, sizeof(float[3]), f);
        fwrite(&i->yaw, 1, sizeof(float), f);
        fwrite(&i->flags, 1, 4, f);
        fwrite(&i->story_id, 1, 4, f);
    }
}

static void SaveQueryBSP(BSP* bsp, FILE* f)
{
    if (!bsp || !f)
        return;

    if (bsp->type == BSP::BSP_TYPE_LEAF)
    {
        Inst* i = ((BSP_Leaf*)bsp)->head;
        while (i)
        {
            SaveInst(i, f);
            i = i->next;
        }
    }
    else if (bsp->type == BSP::BSP_TYPE_INST)
    {
        SaveInst((Inst*)bsp, f);
    }
    else if (bsp->type == BSP::BSP_TYPE_NODE)
    {
        BSP_Node* n = (BSP_Node*)bsp;
        if (n->bsp_child[0])
            SaveQueryBSP(n->bsp_child[0], f);
        if (n->bsp_child[1])
            SaveQueryBSP(n->bsp_child[1], f);
    }
    else if (bsp->type == BSP::BSP_TYPE_NODE_SHARE)
    {
        BSP_NodeShare* s = (BSP_NodeShare*)bsp;
        if (s->bsp_child[0])
            SaveQueryBSP(s->bsp_child[0], f);
        if (s->bsp_child[1])
            SaveQueryBSP(s->bsp_child[1], f);
        Inst* i = s->head;
        while (i)
        {
            SaveInst(i, f);
            i = i->next;
        }
    }
}

bool SaveWorld(World* w, FILE* f)
{
    if (!w || !f)
        return false;

    int format_version = w->has_player_start ? -4 : -3;
    fwrite(&format_version, 1, 4, f);

    int num_of_instances = w->insts - w->temp_insts;
    fwrite(&num_of_instances, 1, 4, f);

    Inst* i = w->head_inst;
    while (i)
    {
        SaveInst(i, f);
        i = i->next;
    }

    if (w->root)
        SaveQueryBSP(w->root, f);
    if (format_version <= -4)
    {
        int has_player_start = w->has_player_start ? 1 : 0;
        fwrite(&has_player_start, 1, 4, f);
        if (has_player_start)
        {
            fwrite(w->player_start_pos, 1, sizeof(float[3]), f);
            fwrite(&w->player_start_yaw, 1, sizeof(float), f);
            fwrite(&w->player_start_dir, 1, sizeof(float), f);
        }
    }
    return !ferror(f);
}

// ============================================================================
// LoadWorld
// ============================================================================

static World* LoadWorldInternal(FILE* f, bool editor)
{
    World* w = CreateWorld();
    if (!w)
        return 0;

    int num_of_instances = 0;
    if (1 != fread(&num_of_instances, 4, 1, f))
    {
        DeleteWorld(w);
        return 0;
    }

    int format_version = 0;
    if (num_of_instances < 0)
    {
        format_version = -num_of_instances;
        if (1 != fread(&num_of_instances, 4, 1, f))
        {
            DeleteWorld(w);
            return 0;
        }
    }

    for (int i = 0; i < num_of_instances; i++)
    {
        int mesh_id_len = 0;
        if (1 != fread(&mesh_id_len, 4, 1, f))
        {
            DeleteWorld(w);
            return 0;
        }

        if (mesh_id_len >= 0)
        {
            char mesh_id[256] = "";
            if (mesh_id_len)
            {
                if (1 != fread(mesh_id, mesh_id_len, 1, f))
                {
                    DeleteWorld(w);
                    return 0;
                }
            }
            mesh_id[mesh_id_len] = 0;

            if (mesh_id_len >= 4 && strcmp(mesh_id + mesh_id_len - 4, ".ply") == 0)
                strcpy(mesh_id + mesh_id_len - 4, ".akm");

            int inst_name_len = 0;
            if (1 != fread(&inst_name_len, 4, 1, f))
            {
                DeleteWorld(w);
                return 0;
            }

            char inst_name[256] = "";
            if (inst_name_len)
            {
                if (1 != fread(inst_name, inst_name_len, 1, f))
                {
                    DeleteWorld(w);
                    return 0;
                }
            }
            inst_name[inst_name_len] = 0;

            double tm[16] = {0};
            if (1 != fread(tm, 16 * 8, 1, f))
            {
                DeleteWorld(w);
                return 0;
            }

            int flags = 0;
            if (1 != fread(&flags, 4, 1, f))
            {
                DeleteWorld(w);
                return 0;
            }

            int story_id = -1;
            if (format_version > 0)
            {
                if (1 != fread(&story_id, 4, 1, f))
                {
                    DeleteWorld(w);
                    return 0;
                }
            }

            Mesh* m = w->head_mesh;
            while (m && strcmp(m->name, mesh_id))
                m = m->next;

            if (!m)
                m = w->AddMesh(mesh_id);

            if (!editor)
                flags |= INST_FLAGS::INST_VOLATILE;

            CreateInst(m, flags, tm, inst_name, story_id);
        }
        else if (mesh_id_len == -1)
        {
            int inst_name_len = 0;
            if (1 != fread(&inst_name_len, 4, 1, f))
            {
                DeleteWorld(w);
                return 0;
            }

            char inst_name[256] = "";
            if (inst_name_len)
            {
                if (1 != fread(inst_name, inst_name_len, 1, f))
                {
                    DeleteWorld(w);
                    return 0;
                }
            }
            inst_name[inst_name_len] = 0;

            float pos[3];
            float yaw;
            int anim;
            int frame;
            int reps[4];
            int flags;

            if ((int)fread(pos, 1, sizeof(float[3]), f) != (int)sizeof(float[3]) ||
                (int)fread(&yaw, 1, sizeof(float), f) != (int)sizeof(float) ||
                (int)fread(&anim, 1, sizeof(int), f) != (int)sizeof(int) ||
                (int)fread(&frame, 1, sizeof(int), f) != (int)sizeof(int) ||
                (int)fread(reps, 1, sizeof(int[4]), f) != (int)sizeof(int[4]) ||
                (int)fread(&flags, 1, 4, f) != 4)
            {
                DeleteWorld(w);
                return 0;
            }

            if (!editor)
                flags |= INST_FLAGS::INST_VOLATILE;

            int story_id = -1;
            if (format_version > 0)
            {
                if (1 != fread(&story_id, 4, 1, f))
                {
                    DeleteWorld(w);
                    return 0;
                }
            }

            Sprite* s = GetFirstSprite();
            while (s)
            {
                if (strcmp(inst_name, s->name) == 0)
                {
                    CreateInst(w, s, flags, pos, yaw, anim, frame, reps, 0, story_id);
                    break;
                }
                s = s->next;
            }
        }
        else if (mesh_id_len == -2)
        {
            int item_definition_id = 0;
            int visual_style_id = 0;
            int presentation_kind_id = 0;
            int count = 0;
            if (format_version >= 3)
            {
                if ((int)fread(&item_definition_id, 1, sizeof(int), f) != (int)sizeof(int) ||
                    (int)fread(&visual_style_id, 1, sizeof(int), f) != (int)sizeof(int) ||
                    (int)fread(&presentation_kind_id, 1, sizeof(int), f) != (int)sizeof(int) ||
                    (int)fread(&count, 1, sizeof(int), f) != (int)sizeof(int))
                {
                    DeleteWorld(w);
                    return 0;
                }
            }
            else
            {
                DeleteWorld(w);
                return 0;
            }

            float pos[3] = {0, 0, 0};
            float yaw = 0.0f;
            if ((int)fread(pos, 1, sizeof(float[3]), f) != (int)sizeof(float[3]) ||
                (int)fread(&yaw, 1, sizeof(float), f) != (int)sizeof(float))
            {
                DeleteWorld(w);
                return 0;
            }

            int flags = 0;
            if ((int)fread(&flags, 1, 4, f) != 4)
            {
                DeleteWorld(w);
                return 0;
            }

            int story_id = -1;
            if (format_version > 0)
            {
                if (1 != fread(&story_id, 4, 1, f))
                {
                    DeleteWorld(w);
                    return 0;
                }
            }

            Item* item = CreateItem();
            if (!editor)
                flags |= INST_FLAGS::INST_VOLATILE;

            item->item_definition_id = (uint16_t)item_definition_id;
            item->visual_style_id = (uint16_t)visual_style_id;
            item->presentation_kind_id = (uint16_t)presentation_kind_id;
            item->count = count;
            item->purpose = editor ? Item::EDIT : Item::WORLD;
            item->inst = CreateInst(w, item, flags, pos, yaw, story_id);

            if (editor)
            {
                Item* clone = CreateItem();
                memcpy(clone, item, sizeof(Item));
                clone->purpose = Item::WORLD;
                clone->inst = CreateInst(w, clone, flags | INST_FLAGS::INST_VOLATILE, pos, yaw, story_id);
            }
        }
    }

    if (format_version >= 4)
    {
        int has_player_start = 0;
        if (1 != fread(&has_player_start, 4, 1, f))
        {
            DeleteWorld(w);
            return 0;
        }
        if (has_player_start)
        {
            float pos[3] = {0.0f, 0.0f, 0.0f};
            float yaw = 0.0f;
            float dir = 0.0f;
            if ((int)fread(pos, 1, sizeof(float[3]), f) != (int)sizeof(float[3]) ||
                (int)fread(&yaw, 1, sizeof(float), f) != (int)sizeof(float) ||
                (int)fread(&dir, 1, sizeof(float), f) != (int)sizeof(float))
            {
                DeleteWorld(w);
                return 0;
            }
            WorldSetPlayerStart(w, pos, yaw, dir);
        }
    }

    return w;
}

World* LoadWorldRuntime(FILE* f)
{
    return LoadWorldInternal(f, false);
}

World* LoadWorldForEditor(FILE* f)
{
    return LoadWorldInternal(f, true);
}

// ============================================================================
// Player start
// ============================================================================

bool WorldGetPlayerStart(World* w, float pos[3], float* yaw, float* dir)
{
    if (!w) return false;
    if (!w->has_player_start) return false;
    pos[0] = w->player_start_pos[0];
    pos[1] = w->player_start_pos[1];
    pos[2] = w->player_start_pos[2];
    if (yaw) *yaw = w->player_start_yaw;
    if (dir) *dir = w->player_start_dir;
    return true;
}

void WorldSetPlayerStart(World* w, const float pos[3], float yaw, float dir)
{
    if (!w) return;
    w->has_player_start = true;
    w->player_start_pos[0] = pos[0];
    w->player_start_pos[1] = pos[1];
    w->player_start_pos[2] = pos[2];
    w->player_start_yaw = yaw;
    w->player_start_dir = dir;
}
