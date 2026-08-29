"""FL-4060 / Q3 ratchet: closed compiler-owned render role vocabulary.

Runtime carries numeric role enums, never authored strings. Source/profile
data may use these readable slugs, but the compiler validates them against
this closed set and emits the C++ enum value.

Per FL-4065 / Q8, roles must describe generic visual ownership concepts, not
skin/mount/item-specific exceptions.
"""

from __future__ import annotations


ROLE_MOUNT_REAR = "mount_rear"
ROLE_RIDER_BODY = "body"
ROLE_HEAD_VISIBLE = "head"
ROLE_CHEST_VISIBLE = "chest"
ROLE_WEAPON_VISIBLE = "weapon"
ROLE_SHIELD_VISIBLE = "shield"
ROLE_MOUNT_FRONT = "mount_front"
ROLE_SLOT = "slot"

ALLOWED_RENDER_ROLES = frozenset({
    ROLE_MOUNT_REAR,
    ROLE_RIDER_BODY,
    ROLE_HEAD_VISIBLE,
    ROLE_CHEST_VISIBLE,
    ROLE_WEAPON_VISIBLE,
    ROLE_SHIELD_VISIBLE,
    ROLE_MOUNT_FRONT,
    ROLE_SLOT,
})

RIDER_VISIBLE_GROUP = frozenset({
    ROLE_RIDER_BODY,
    ROLE_HEAD_VISIBLE,
    ROLE_CHEST_VISIBLE,
    ROLE_WEAPON_VISIBLE,
    ROLE_SHIELD_VISIBLE,
})

ROLE_TO_CPP_ENUM = {
    ROLE_MOUNT_REAR: "ACTOR_VISUAL_LAYER_ROLE_MOUNT_REAR",
    ROLE_RIDER_BODY: "ACTOR_VISUAL_LAYER_ROLE_BODY",
    ROLE_HEAD_VISIBLE: "ACTOR_VISUAL_LAYER_ROLE_HEAD",
    ROLE_CHEST_VISIBLE: "ACTOR_VISUAL_LAYER_ROLE_CHEST",
    ROLE_WEAPON_VISIBLE: "ACTOR_VISUAL_LAYER_ROLE_WEAPON",
    ROLE_SHIELD_VISIBLE: "ACTOR_VISUAL_LAYER_ROLE_SHIELD",
    ROLE_MOUNT_FRONT: "ACTOR_VISUAL_LAYER_ROLE_MOUNT_FRONT",
    ROLE_SLOT: "ACTOR_VISUAL_LAYER_ROLE_SLOT",
}


def validate_role(role: str, *, label: str = "role") -> str:
    if role not in ALLOWED_RENDER_ROLES:
        raise ValueError(
            f"{label}: unknown semantic role {role!r}; "
            f"allowed={sorted(ALLOWED_RENDER_ROLES)}"
        )
    return role


def structural_semantic_mask_id(
    source_xp_id: str,
    source_layer_index: int,
    role: str,
) -> str:
    """Compiler-synthesised semantic mask identity.

    Authors choose the tuple (source_xp_id, source layer, role); the ID is the
    deterministic rendering of that tuple. Free-form mask names are refused by
    Q3 because they recreate selector/list authority.
    """
    validate_role(role, label=f"{source_xp_id} L{source_layer_index}")
    return f"{source_xp_id}__L{int(source_layer_index)}__{role}"
