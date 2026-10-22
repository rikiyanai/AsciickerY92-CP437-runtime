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
#include "a3d_wire.h" // explicit little-endian A3D wire primitives (FL-4588)
#include "shared/runtime_data/a3d_abi.h" // neutral cross-product A3D ABI
#include "enemygen.h"
#include "inventory.h"

#include <sys/stat.h>
#include <errno.h>
#include <math.h> // isfinite: NaN/Inf world fields are rejected (FL-4588)
#include <stdio.h>

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

// FL-4588: the world's instance-name wire fields are 4-byte lengths, but
// the parser stores names in 256-byte buffers. Freeze the authoring bound
// at 255 so serializer and parser agree, and reject longer names instead
// of emitting a document the parser cannot load.
static const size_t kWorldNameMax = 255;

// A length-prefixed name read from uploaded bytes must not contain an
// embedded NUL: every downstream consumer (strcmp lookups, strlen-based
// re-serialization) treats these as C strings, so a NUL would silently
// truncate and break byte-stable round trips.
static bool WorldNameBytesValid(const char* name, size_t len)
{
    return len == 0 || memchr(name, 0, len) == nullptr;
}

// FL-4588 mesh-asset identifier contract (operator review 2026-07-16):
// the tracked corpus stores basename-only .akm identifiers, and the name
// is later joined onto asset roots by ResolveMeshAssetPath. An uploaded
// document must therefore never smuggle path structure through this
// field: no slash and no backslash (which also rules out absolute paths
// and traversal SEGMENTS), no ':' (Windows drive prefixes), no ".."
// (checked explicitly for clarity even though the extension rule already
// excludes it), non-empty, and ending in the one supported ".akm"
// extension. Validation runs AFTER the legacy .ply->.akm rename so old
// documents keep loading through the same contract.
static bool MeshAssetNameValid(const char* name)
{
    const size_t len = strlen(name);
    if (len < 5 || len > kWorldNameMax) // shortest legal name: "x.akm"
        return false;
    if (strchr(name, '/') || strchr(name, '\\') || strchr(name, ':'))
        return false;
    if (strstr(name, ".."))
        return false;
    return strcmp(name + len - 4, ".akm") == 0;
}

// Serialize one instance record, explicitly little-endian (a3d_wire.h).
// Returns false on any write failure or on a name the frozen wire bound
// rejects; volatile instances are skipped successfully (they are runtime
// clones, never document content).
static bool SaveInst(Inst* inst, FILE* f)
{
    if (!inst || !f)
        return false;
    if (inst->flags & INST_FLAGS::INST_VOLATILE)
        return true;

    if (inst->inst_type == Inst::INST_TYPE::MESH)
    {
        MeshInst* i = (MeshInst*)inst;
        const char* mesh_name = i->mesh && i->mesh->name ? i->mesh->name : "";
        const char* inst_name = i->name ? i->name : "";
        const size_t mesh_id_len = strlen(mesh_name);
        const size_t inst_name_len = strlen(inst_name);
        if (mesh_id_len > kWorldNameMax || inst_name_len > kWorldNameMax)
            return false;
        // The serializer must never emit a mesh identifier its own
        // loader rejects (basename-only admitted .akm contract).
        if (!MeshAssetNameValid(mesh_name))
            return false;
        if (!A3dWriteI32LE(f, (int32_t)mesh_id_len) ||
            (mesh_id_len && fwrite(mesh_name, 1, mesh_id_len, f) != mesh_id_len) ||
            !A3dWriteI32LE(f, (int32_t)inst_name_len) ||
            (inst_name_len && fwrite(inst_name, 1, inst_name_len, f) != inst_name_len))
            return false;
        // 4x4 transform: sixteen doubles, each a little-endian 64-bit
        // IEEE-754 bit pattern.
        for (int component = 0; component < 16; component++)
            if (!A3dWriteF64LE(f, i->tm[component]))
                return false;
		// FL-4512 U1 MaterialKeyV1: keep the placed-object override explicit in
		// the map ABI. Old maps load through the asset default below; no vertex
		// colour inference is revived as compatibility behaviour.
		const int material_key = GetInstMaterialKey(i);
		const int material_key_override = i->material_key_override ? 1 : 0;
		if (!A3dWriteI32LE(f, i->flags) ||
			!A3dWriteI32LE(f, i->story_id) ||
			!A3dWriteI32LE(f, material_key) ||
			!A3dWriteI32LE(f, material_key_override) ||
			!A3dWriteU64LE(f, GetInstPersistentUid(i)))
			return false;
        return true;
    }
    else if (inst->inst_type == Inst::INST_TYPE::SPRITE)
    {
        SpriteInst* i = (SpriteInst*)inst;
        const char* sprite_name = (i->sprite && i->sprite->name) ? i->sprite->name : "";
        const size_t inst_name_len = strlen(sprite_name);
        if (inst_name_len > kWorldNameMax)
            return false;
        if (!A3dWriteI32LE(f, -1) || // -1 tags a sprite record
            !A3dWriteI32LE(f, (int32_t)inst_name_len) ||
            (inst_name_len && fwrite(sprite_name, 1, inst_name_len, f) != inst_name_len) ||
            !A3dWriteF32LE(f, i->pos[0]) ||
            !A3dWriteF32LE(f, i->pos[1]) ||
            !A3dWriteF32LE(f, i->pos[2]) ||
            !A3dWriteF32LE(f, i->yaw) ||
            !A3dWriteI32LE(f, i->anim) ||
            !A3dWriteI32LE(f, i->frame) ||
            !A3dWriteI32LE(f, i->reps[0]) ||
            !A3dWriteI32LE(f, i->reps[1]) ||
            !A3dWriteI32LE(f, i->reps[2]) ||
            !A3dWriteI32LE(f, i->reps[3]) ||
            !A3dWriteI32LE(f, i->flags) ||
            !A3dWriteI32LE(f, i->story_id))
            return false;
        return true;
    }
    else if (inst->inst_type == Inst::INST_TYPE::ITEM)
    {
        ItemInst* i = (ItemInst*)inst;
        if (!A3dWriteI32LE(f, -2) || // -2 tags an item record
            !A3dWriteI32LE(f, (int32_t)i->item->item_definition_id) ||
            !A3dWriteI32LE(f, (int32_t)i->item->visual_style_id) ||
            !A3dWriteI32LE(f, (int32_t)i->item->presentation_kind_id) ||
            !A3dWriteI32LE(f, i->item->count) ||
            !A3dWriteF32LE(f, i->pos[0]) ||
            !A3dWriteF32LE(f, i->pos[1]) ||
            !A3dWriteF32LE(f, i->pos[2]) ||
            !A3dWriteF32LE(f, i->yaw) ||
            !A3dWriteI32LE(f, i->flags) ||
            !A3dWriteI32LE(f, i->story_id))
            return false;
        return true;
    }
    // Unknown instance type: refuse rather than silently under-count the
    // records the header promised.
    return false;
}

// Serialize every instance reachable through the query BSP, propagating
// the first failure upward (fail closed, FL-4588).
static bool SaveQueryBSP(BSP* bsp, FILE* f)
{
    if (!bsp || !f)
        return false;

    if (bsp->type == BSP::BSP_TYPE_LEAF)
    {
        Inst* i = ((BSP_Leaf*)bsp)->head;
        while (i)
        {
            if (!SaveInst(i, f))
                return false;
            i = i->next;
        }
        return true;
    }
    else if (bsp->type == BSP::BSP_TYPE_INST)
    {
        return SaveInst((Inst*)bsp, f);
    }
    else if (bsp->type == BSP::BSP_TYPE_NODE)
    {
        BSP_Node* n = (BSP_Node*)bsp;
        if (n->bsp_child[0] && !SaveQueryBSP(n->bsp_child[0], f))
            return false;
        if (n->bsp_child[1] && !SaveQueryBSP(n->bsp_child[1], f))
            return false;
        return true;
    }
    else if (bsp->type == BSP::BSP_TYPE_NODE_SHARE)
    {
        BSP_NodeShare* s = (BSP_NodeShare*)bsp;
        if (s->bsp_child[0] && !SaveQueryBSP(s->bsp_child[0], f))
            return false;
        if (s->bsp_child[1] && !SaveQueryBSP(s->bsp_child[1], f))
            return false;
        Inst* i = s->head;
        while (i)
        {
            if (!SaveInst(i, f))
                return false;
            i = i->next;
        }
        return true;
    }
    // Unknown BSP node type: refuse rather than silently skip content.
    return false;
}

// FL-4588 validating preflight (operator review 2026-07-16): before ANY
// world byte is written, every record the traversal will emit must be
// proven serializable — otherwise a late invalid instance would leave a
// partial world header in a supposedly canonical document. One instance
// passes preflight when:
//   - its type is a KNOWN instance kind;
//   - its payload pointer (mesh / sprite / item) is non-null;
//   - its names respect the frozen 255-byte wire bound;
//   - its mesh transform / sprite pose / item pose values are finite;
//   - its material-key fields are in range (0..255, override 0/1);
//   - its item identity fields fit their uint16 wire sources and the
//     item count is a sane authorable value (0..1000000, the canonical
//     validate_a3d.py instance ceiling reused as a generous bound).
// Volatile instances are runtime clones and are skipped (not counted).
static bool PreflightInst(Inst* inst, long* emit_count)
{
    if (!inst)
        return false;
    if (inst->flags & INST_FLAGS::INST_VOLATILE)
        return true;

    if (inst->inst_type == Inst::INST_TYPE::MESH)
    {
        MeshInst* i = (MeshInst*)inst;
        if (!i->mesh)
            return false;
        const char* mesh_name = i->mesh->name ? i->mesh->name : "";
        const char* inst_name = i->name ? i->name : "";
        if (strlen(mesh_name) > kWorldNameMax || strlen(inst_name) > kWorldNameMax)
            return false;
        // Preflight the same mesh-identifier contract the loader
        // enforces, so no world with an unserializable name gets past
        // the header.
        if (!MeshAssetNameValid(mesh_name))
            return false;
        for (int component = 0; component < 16; component++)
            if (!isfinite(i->tm[component]))
                return false;
        const int material_key = GetInstMaterialKey(i);
        if (material_key < 0 || material_key > 255)
            return false;
    }
    else if (inst->inst_type == Inst::INST_TYPE::SPRITE)
    {
        SpriteInst* i = (SpriteInst*)inst;
        if (!i->sprite)
            return false;
        const char* sprite_name = i->sprite->name ? i->sprite->name : "";
        if (strlen(sprite_name) > kWorldNameMax)
            return false;
        if (!isfinite(i->pos[0]) || !isfinite(i->pos[1]) ||
            !isfinite(i->pos[2]) || !isfinite(i->yaw))
            return false;
    }
    else if (inst->inst_type == Inst::INST_TYPE::ITEM)
    {
        ItemInst* i = (ItemInst*)inst;
        if (!i->item)
            return false;
        if (!isfinite(i->pos[0]) || !isfinite(i->pos[1]) ||
            !isfinite(i->pos[2]) || !isfinite(i->yaw))
            return false;
        // The three identity fields are uint16_t at the source; the count
        // must be a sane authorable quantity.
        if (i->item->count < 0 || i->item->count > 1000000)
            return false;
    }
    else
    {
        // Unknown instance kind: the document cannot represent it.
        return false;
    }

    ++*emit_count;
    return true;
}

static bool PreflightInsts(Inst* head, long* emit_count)
{
    for (Inst* i = head; i; i = i->next)
        if (!PreflightInst(i, emit_count))
            return false;
    return true;
}

// Walk the query BSP validating every reachable instance. Unknown node
// types are a FAILURE (never silently counted as zero): an unwalkable
// subtree means the traversal integrity of the save cannot be proven.
static bool PreflightBSP(BSP* bsp, long* emit_count)
{
    if (!bsp)
        return true; // an absent child is fine; only UNKNOWN types fail
    if (bsp->type == BSP::BSP_TYPE_LEAF)
        return PreflightInsts(((BSP_Leaf*)bsp)->head, emit_count);
    if (bsp->type == BSP::BSP_TYPE_INST)
        return PreflightInst((Inst*)bsp, emit_count);
    if (bsp->type == BSP::BSP_TYPE_NODE)
    {
        BSP_Node* n = (BSP_Node*)bsp;
        return PreflightBSP(n->bsp_child[0], emit_count) &&
            PreflightBSP(n->bsp_child[1], emit_count);
    }
    if (bsp->type == BSP::BSP_TYPE_NODE_SHARE)
    {
        BSP_NodeShare* s = (BSP_NodeShare*)bsp;
        return PreflightBSP(s->bsp_child[0], emit_count) &&
            PreflightBSP(s->bsp_child[1], emit_count) &&
            PreflightInsts(s->head, emit_count);
    }
    return false;
}

bool SaveWorld(World* w, FILE* f)
{
    if (!w || !f)
        return false;

    // FL-4588 validating preflight: prove the WHOLE world serializable
    // and count the records BEFORE the first header byte. The count the
    // header promises must equal the count the traversal emits, and the
    // optional player-start block must be finite.
    int num_of_instances = w->insts - w->temp_insts;
    long emit_count = 0;
    if (!PreflightInsts(w->head_inst, &emit_count) ||
        !PreflightBSP(w->root, &emit_count))
        return false;
    if ((long)num_of_instances != emit_count)
        return false;
    if (w->has_player_start &&
        (!isfinite(w->player_start_pos[0]) || !isfinite(w->player_start_pos[1]) ||
         !isfinite(w->player_start_pos[2]) || !isfinite(w->player_start_yaw) ||
         !isfinite(w->player_start_dir)))
        return false;

    // Version bound owned by shared/runtime_data/a3d_abi.h: the writer emits
    // kCurrentWriteVersion, which adds transform-independent persistent_uid
    // to every mesh instance. All multi-byte fields are explicitly little-endian.
    if (!A3dWriteI32LE(f, -a3d_abi::kCurrentWriteVersion) ||
        !A3dWriteI32LE(f, num_of_instances))
        return false;

    Inst* i = w->head_inst;
    while (i)
    {
        if (!SaveInst(i, f))
            return false;
        i = i->next;
    }

    if (w->root && !SaveQueryBSP(w->root, f))
        return false;

    // Format version 6 (<= -4 on the wire) always carries the optional
    // player-start block.
    if (!A3dWriteI32LE(f, w->has_player_start ? 1 : 0))
        return false;
    if (w->has_player_start)
    {
        if (!A3dWriteF32LE(f, w->player_start_pos[0]) ||
            !A3dWriteF32LE(f, w->player_start_pos[1]) ||
            !A3dWriteF32LE(f, w->player_start_pos[2]) ||
            !A3dWriteF32LE(f, w->player_start_yaw) ||
            !A3dWriteF32LE(f, w->player_start_dir))
            return false;
    }
    return !ferror(f);
}

// ============================================================================
// LoadWorld
// ============================================================================

// FL-4588: the world loader historically failed with a bare null, leaving
// callers (and the document codec's staged-open reasons) unable to say WHY
// a document was rejected. Each hardened rejection below records a short
// static reason; WorldLoadRejectReason() returns the most recent one.
static const char* s_world_reject_reason = nullptr;

const char* WorldLoadRejectReason()
{
    return s_world_reject_reason;
}

static World* LoadWorldInternal(FILE* f, bool editor, bool candidate = false)
{
    s_world_reject_reason = "world_stream_invalid";
    // FL-4588 hardened intake (operator source audit 2026-07-16): measure
    // the remaining stream FIRST so the declared instance count can be
    // bounded against the bytes that physically exist. The legacy loader
    // accepted unbounded counts and copied unbounded name lengths into
    // 256-byte stack arrays — a buffer overflow on hostile input.
    long world_pos = ftell(f);
    if (world_pos < 0 || fseek(f, 0, SEEK_END) != 0)
        return 0;
    long stream_end = ftell(f);
    if (stream_end < 0 || fseek(f, world_pos, SEEK_SET) != 0 ||
        stream_end < world_pos)
        return 0;
    const long world_budget = stream_end - world_pos;

    // FL-4588: candidate parses build a candidate world so a rejected
    // upload never advances the live SurfaceId generation sequence.
    World* w = candidate ? CreateWorldCandidate() : CreateWorld();
    if (!w)
        return 0;

    int32_t num_of_instances = 0;
    if (!A3dReadI32LE(f, &num_of_instances))
    {
        DeleteWorld(w);
        return 0;
    }

    int format_version = 0;
    if (num_of_instances < 0)
    {
        // FL-4588: INT32_MIN cannot be negated (undefined behavior), and
        // the supported version set is owned by
        // shared/runtime_data/a3d_abi.h (0..kCurrentMaxVersion) — a future
        // newer document must be rejected, not silently parsed with older
        // semantics.
        if (num_of_instances == INT32_MIN)
        {
            DeleteWorld(w);
            return 0;
        }
        format_version = -num_of_instances;
        if (format_version > a3d_abi::kCurrentMaxVersion || !A3dReadI32LE(f, &num_of_instances))
        {
            s_world_reject_reason = "world_version_unsupported";
            DeleteWorld(w);
            return 0;
        }
    }

    // Bound the count three ways: never negative, never above the
    // canonical scripts/validate_a3d.py MAX_INSTANCES = 1000000, and
    // never more records than the remaining bytes can physically hold
    // (the smallest record — an item without story id — is 40 bytes).
    if (num_of_instances < 0 || num_of_instances > 1000000 ||
        (long)num_of_instances > world_budget / 40)
    {
        s_world_reject_reason = "instance_count_bound";
        DeleteWorld(w);
        return 0;
    }

    for (int i = 0; i < num_of_instances; i++)
    {
        int32_t mesh_id_len = 0;
        if (!A3dReadI32LE(f, &mesh_id_len))
        {
            DeleteWorld(w);
            return 0;
        }

        if (mesh_id_len >= 0)
        {
            // Names live in 256-byte buffers; the frozen wire bound is
            // 255 bytes (kWorldNameMax). A longer declared length is a
            // malformed document, rejected instead of overflowing.
            if ((size_t)mesh_id_len > kWorldNameMax)
            {
                DeleteWorld(w);
                return 0;
            }
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

            // FL-4588: no embedded NULs, and the (post-rename) asset
            // identifier must be a basename-only admitted .akm name —
            // uploaded bytes never carry path structure into AddMesh and
            // the later ResolveMeshAssetPath joins.
            if (!WorldNameBytesValid(mesh_id, (size_t)mesh_id_len) ||
                !MeshAssetNameValid(mesh_id))
            {
                s_world_reject_reason = "mesh_asset_name_invalid";
                DeleteWorld(w);
                return 0;
            }

            int32_t inst_name_len = 0;
            if (!A3dReadI32LE(f, &inst_name_len) ||
                inst_name_len < 0 || (size_t)inst_name_len > kWorldNameMax)
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
            if (!WorldNameBytesValid(inst_name, (size_t)inst_name_len))
            {
                DeleteWorld(w);
                return 0;
            }

            // Sixteen little-endian doubles: the 4x4 world transform.
            // Non-finite components are never an authorable placement.
            double tm[16] = {0};
            bool tm_ok = true;
            for (int component = 0; tm_ok && component < 16; component++)
                tm_ok = A3dReadF64LE(f, &tm[component]) && isfinite(tm[component]);
            if (!tm_ok)
            {
                DeleteWorld(w);
                return 0;
            }

            int32_t flags = 0;
            if (!A3dReadI32LE(f, &flags))
            {
                DeleteWorld(w);
                return 0;
            }

            int32_t story_id = -1;
            if (format_version >= a3d_abi::kStoryIdVersion)
            {
                if (!A3dReadI32LE(f, &story_id))
                {
                    DeleteWorld(w);
                    return 0;
                }
            }

			int32_t material_key = 200;
			int32_t material_key_override = 0;
			uint64_t persistent_uid = 0;
			if (format_version >= a3d_abi::kMaterialKeyVersion)
			{
				if (!A3dReadI32LE(f, &material_key) ||
					!A3dReadI32LE(f, &material_key_override) ||
					material_key < 0 || material_key > 255 ||
					(material_key_override != 0 && material_key_override != 1))
				{
					DeleteWorld(w);
					return 0;
				}
			}
			if (format_version >= a3d_abi::kMeshUidVersion && !A3dReadU64LE(f, &persistent_uid))
			{
				DeleteWorld(w);
				return 0;
			}

            Mesh* m = w->head_mesh;
            while (m && strcmp(m->name, mesh_id))
                m = m->next;

            if (!m)
                m = w->AddMesh(mesh_id);
            // FL-4588: a failed mesh registration must fail the load, not
            // hand a null mesh to CreateInst.
            if (!m)
            {
                s_world_reject_reason = "mesh_register_failed";
                DeleteWorld(w);
                return 0;
            }

            if (!editor)
                flags |= INST_FLAGS::INST_VOLATILE;

			Inst* inst = CreateInst(m, flags, tm, inst_name, story_id);
			if (!inst ||
				(material_key_override != 0 && !SetInstMaterialKey(inst, material_key)) ||
				(persistent_uid != 0 && !SetInstPersistentUid(inst, persistent_uid)))
			{
				DeleteWorld(w);
				return 0;
			}
        }
        else if (mesh_id_len == -1)
        {
            int32_t inst_name_len = 0;
            if (!A3dReadI32LE(f, &inst_name_len) ||
                inst_name_len < 0 || (size_t)inst_name_len > kWorldNameMax)
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
            // Sprite lookup below is strcmp-based; an embedded NUL would
            // silently truncate the comparison.
            if (!WorldNameBytesValid(inst_name, (size_t)inst_name_len))
            {
                DeleteWorld(w);
                return 0;
            }

            float pos[3];
            float yaw;
            int32_t anim;
            int32_t frame;
            int32_t reps[4];
            int32_t flags;

            // Explicit little-endian fields; non-finite placement values
            // are never an authorable document.
            if (!A3dReadF32LE(f, &pos[0]) ||
                !A3dReadF32LE(f, &pos[1]) ||
                !A3dReadF32LE(f, &pos[2]) ||
                !A3dReadF32LE(f, &yaw) ||
                !isfinite(pos[0]) || !isfinite(pos[1]) ||
                !isfinite(pos[2]) || !isfinite(yaw) ||
                !A3dReadI32LE(f, &anim) ||
                !A3dReadI32LE(f, &frame) ||
                !A3dReadI32LE(f, &reps[0]) ||
                !A3dReadI32LE(f, &reps[1]) ||
                !A3dReadI32LE(f, &reps[2]) ||
                !A3dReadI32LE(f, &reps[3]) ||
                !A3dReadI32LE(f, &flags))
            {
                DeleteWorld(w);
                return 0;
            }

            if (!editor)
                flags |= INST_FLAGS::INST_VOLATILE;

            int32_t story_id = -1;
            if (format_version >= a3d_abi::kStoryIdVersion)
            {
                if (!A3dReadI32LE(f, &story_id))
                {
                    DeleteWorld(w);
                    return 0;
                }
            }

            // FL-4588: a sprite record naming an asset this process has
            // not loaded is a FAILURE. The legacy loader silently dropped
            // the record, so the world held fewer instances than the
            // header declared — an invisible data loss that also broke
            // byte-stable re-serialization.
            Sprite* s = GetFirstSprite();
            while (s && strcmp(inst_name, s->name) != 0)
                s = s->next;
            if (!s)
            {
                s_world_reject_reason = "sprite_asset_unresolved";
                printf("[LOAD] missing sprite asset: %s\n", inst_name);
                fflush(stdout);
                DeleteWorld(w);
                return 0;
            }
            if (!CreateInst(w, s, flags, pos, yaw, anim, frame, reps, 0, story_id))
            {
                s_world_reject_reason = "sprite_instance_create_failed";
                DeleteWorld(w);
                return 0;
            }
        }
        else if (mesh_id_len == -2)
        {
            int32_t item_definition_id = 0;
            int32_t visual_style_id = 0;
            int32_t presentation_kind_id = 0;
            int32_t count = 0;
            if (format_version >= a3d_abi::kItemRecordVersion)
            {
                // The three identity fields narrow to uint16_t at the
                // consumer, and the count must be a sane authorable
                // quantity (mirrors the save preflight bounds).
                if (!A3dReadI32LE(f, &item_definition_id) ||
                    !A3dReadI32LE(f, &visual_style_id) ||
                    !A3dReadI32LE(f, &presentation_kind_id) ||
                    !A3dReadI32LE(f, &count) ||
                    item_definition_id < 0 || item_definition_id > 65535 ||
                    visual_style_id < 0 || visual_style_id > 65535 ||
                    presentation_kind_id < 0 || presentation_kind_id > 65535 ||
                    count < 0 || count > 1000000)
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
            if (!A3dReadF32LE(f, &pos[0]) ||
                !A3dReadF32LE(f, &pos[1]) ||
                !A3dReadF32LE(f, &pos[2]) ||
                !A3dReadF32LE(f, &yaw) ||
                !isfinite(pos[0]) || !isfinite(pos[1]) ||
                !isfinite(pos[2]) || !isfinite(yaw))
            {
                DeleteWorld(w);
                return 0;
            }

            int32_t flags = 0;
            if (!A3dReadI32LE(f, &flags))
            {
                DeleteWorld(w);
                return 0;
            }

            int32_t story_id = -1;
            if (format_version >= a3d_abi::kStoryIdVersion)
            {
                if (!A3dReadI32LE(f, &story_id))
                {
                    DeleteWorld(w);
                    return 0;
                }
            }

            // FL-4588: every allocation and instance creation must
            // succeed — a null item, failed instance, or failed editor
            // clone means the world does not hold what the document
            // declared, so the load fails instead of proceeding partial.
            Item* item = CreateItem();
            if (!item)
            {
                DeleteWorld(w);
                return 0;
            }
            if (!editor)
                flags |= INST_FLAGS::INST_VOLATILE;

            item->item_definition_id = (uint16_t)item_definition_id;
            item->visual_style_id = (uint16_t)visual_style_id;
            item->presentation_kind_id = (uint16_t)presentation_kind_id;
            item->count = count;
            item->purpose = editor ? Item::EDIT : Item::WORLD;
            item->inst = CreateInst(w, item, flags, pos, yaw, story_id);
            if (!item->inst)
            {
                DeleteWorld(w);
                return 0;
            }

            if (editor)
            {
                Item* clone = CreateItem();
                if (!clone)
                {
                    DeleteWorld(w);
                    return 0;
                }
                memcpy(clone, item, sizeof(Item));
                clone->purpose = Item::WORLD;
                clone->inst = CreateInst(w, clone, flags | INST_FLAGS::INST_VOLATILE, pos, yaw, story_id);
                if (!clone->inst)
                {
                    DeleteWorld(w);
                    return 0;
                }
            }
        }
        else
        {
            // FL-4588: any other negative tag is an unknown record kind.
            // The legacy loader silently skipped the record WITHOUT
            // consuming its bytes, desynchronizing every field after it —
            // fail closed instead.
            s_world_reject_reason = "unknown_record_tag";
            DeleteWorld(w);
            return 0;
        }
    }

    if (format_version >= a3d_abi::kPlayerStartVersion)
    {
        int32_t has_player_start = 0;
        if (!A3dReadI32LE(f, &has_player_start) ||
            (has_player_start != 0 && has_player_start != 1))
        {
            DeleteWorld(w);
            return 0;
        }
        if (has_player_start)
        {
            float pos[3] = {0.0f, 0.0f, 0.0f};
            float yaw = 0.0f;
            float dir = 0.0f;
            if (!A3dReadF32LE(f, &pos[0]) ||
                !A3dReadF32LE(f, &pos[1]) ||
                !A3dReadF32LE(f, &pos[2]) ||
                !A3dReadF32LE(f, &yaw) ||
                !A3dReadF32LE(f, &dir) ||
                !isfinite(pos[0]) || !isfinite(pos[1]) ||
                !isfinite(pos[2]) || !isfinite(yaw) || !isfinite(dir))
            {
                DeleteWorld(w);
                return 0;
            }
            WorldSetPlayerStart(w, pos, yaw, dir);
        }
    }

    // Success: clear the reject reason so a stale one never leaks into a
    // later caller's diagnostics.
    s_world_reject_reason = nullptr;
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

// FL-4588: candidate entry for staged document validation — editor-shaped
// content (items get their edit/world clone pair, ready for a later atomic
// swap) inside a candidate world whose generation stays 0 until commit.
World* LoadWorldForCandidate(FILE* f)
{
    return LoadWorldInternal(f, true, true);
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
