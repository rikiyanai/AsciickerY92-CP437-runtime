from __future__ import annotations

from typing import Any, Callable


def BuildReachableAppearanceStateSpace(
    bundle: dict[str, Any],
    deps: dict[str, Any],
) -> dict[str, Any]:
    _ensure_object: Callable[..., Any] = deps["_ensure_object"]
    _ensure_list: Callable[..., Any] = deps["_ensure_list"]
    _ensure_string_list: Callable[..., Any] = deps["_ensure_string_list"]
    _stable_compile_component_id: Callable[..., Any] = deps["_stable_compile_component_id"]
    _normalize_variant_signature: Callable[..., Any] = deps["_normalize_variant_signature"]
    _variant_signature_tuple: Callable[..., Any] = deps["_variant_signature_tuple"]
    BundleCompileError = deps["BundleCompileError"]

    catalog = _ensure_object(bundle.get("catalog"), "bundle.catalog")
    appearance_profiles = [
        _ensure_object(entry, "catalog.appearance_profiles[]")
        for entry in _ensure_list(catalog.get("appearance_profiles", []), "catalog.appearance_profiles")
    ]
    item_definitions = [
        _ensure_object(entry, "catalog.item_definitions[]")
        for entry in _ensure_list(catalog.get("item_definitions", []), "catalog.item_definitions")
    ]
    mount_definitions = [
        _ensure_object(entry, "catalog.mount_definitions[]")
        for entry in _ensure_list(catalog.get("mount_definitions", []), "catalog.mount_definitions")
    ]
    layer_definitions = [
        _ensure_object(entry, "catalog.layer_definitions[]")
        for entry in _ensure_list(catalog.get("layer_definitions", []), "catalog.layer_definitions")
    ]
    selector_tables = [
        _ensure_object(entry, "presentation_selector_tables[]")
        for entry in _ensure_list(bundle.get("presentation_selector_tables", []), "presentation_selector_tables")
    ]
    server_seat_subjects = [
        _ensure_object(entry, "server_seat_subjects[]")
        for entry in _ensure_list(bundle.get("server_seat_subjects", []), "server_seat_subjects")
    ]

    profiles_by_id = {int(entry["id"]): entry for entry in appearance_profiles}
    profiles_by_slug = {
        str(entry["slug"]): entry
        for entry in appearance_profiles
        if isinstance(entry.get("slug"), str)
    }
    default_profile = profiles_by_slug.get("default_profile")

    equip_rule_rows: list[dict[str, Any]] = []
    seen_equip_rows: set[tuple[int, int, int]] = set()
    gameplay_kind_by_item: dict[int, str] = {}
    for item_definition in item_definitions:
        gameplay_kind = item_definition.get("gameplay_kind")
        if isinstance(gameplay_kind, str):
            gameplay_kind_by_item[int(item_definition["id"])] = gameplay_kind
    for layer in layer_definitions:
        if layer.get("owner_definition_kind") != "item":
            continue
        if layer.get("slot_kind_slug") == "mount":
            continue
        if layer.get("mount_qualifier_definition_id") is not None:
            continue
        item_definition_id = int(layer.get("owner_definition_id") or 0)
        gameplay_kind = gameplay_kind_by_item.get(item_definition_id)
        if gameplay_kind not in {"wearable", "weapon"}:
            continue
        row_key = (
            item_definition_id,
            int(layer.get("slot_kind_id") or 0),
            int(layer.get("visual_style_id") or 0),
        )
        if 0 in row_key or row_key in seen_equip_rows:
            continue
        seen_equip_rows.add(row_key)
        equip_rule_rows.append(
            {
                "item_definition_id": row_key[0],
                "item_definition_slug": layer.get("owner_definition_slug"),
                "slot_kind_id": row_key[1],
                "slot_kind_slug": layer.get("slot_kind_slug"),
                "visual_style_id": row_key[2],
                "visual_style_slug": layer.get("visual_style_slug"),
                "gameplay_kind": gameplay_kind,
            }
        )
    equip_rule_rows.sort(
        key=lambda row: (
            int(row["slot_kind_id"]),
            int(row["item_definition_id"]),
            int(row["visual_style_id"]),
        )
    )

    mount_rule_rows: list[dict[str, Any]] = []
    for item_definition in item_definitions:
        if item_definition.get("gameplay_kind") != "mountable":
            continue
        mount_definition_id = int(item_definition.get("mount_definition_id") or 0)
        if mount_definition_id == 0:
            continue
        mount_rule_rows.append(
            {
                "item_definition_id": int(item_definition["id"]),
                "item_definition_slug": item_definition.get("slug"),
                "slot_kind_id": int(item_definition.get("slot_kind_id") or 0),
                "slot_kind_slug": item_definition.get("slot_kind_slug"),
                "mount_definition_id": mount_definition_id,
                "mount_definition_slug": item_definition.get("mount_definition_slug"),
            }
        )
    mount_rule_rows.sort(
        key=lambda row: (
            int(row["mount_definition_id"]),
            int(row["item_definition_id"]),
        )
    )

    default_injection_rule_rows: list[dict[str, Any]] = []
    spawn_injection_rule_rows: list[dict[str, Any]] = []
    equip_rule_set_id = _stable_compile_component_id("equip_rule_set", equip_rule_rows)
    mount_rule_set_id = _stable_compile_component_id("mount_rule_set", mount_rule_rows)
    default_injection_rule_set_id = _stable_compile_component_id(
        "default_injection_rule_set",
        default_injection_rule_rows,
    )
    spawn_injection_rule_set_id = _stable_compile_component_id(
        "spawn_injection_rule_set",
        spawn_injection_rule_rows,
    )

    scope_rows: list[dict[str, Any]] = []

    def append_scope(
        profile: dict[str, Any],
        source_kind: str,
        subject_kind: str,
        subject_key: str,
    ) -> None:
        scope_row = {
            "appearance_profile_id": int(profile["id"]),
            "appearance_profile_slug": profile.get("slug"),
            "skin_definition_id": int(profile.get("skin_definition_id") or 0),
            "skin_definition_slug": profile.get("skin_definition_slug"),
            "source_kind": source_kind,
            "subject_kind": subject_kind,
            "subject_key": subject_key,
            "projection_kind": "SVR_APPEARANCE_PROJECTION_PROFILE",
            "equip_rule_set_id": equip_rule_set_id,
            "mount_rule_set_id": mount_rule_set_id,
            "default_injection_rule_set_id": default_injection_rule_set_id,
            "spawn_injection_rule_set_id": spawn_injection_rule_set_id,
        }
        scope_row["appearance_emission_scope_id"] = _stable_compile_component_id(
            "appearance_emission_scope",
            [scope_row],
        )
        scope_rows.append(scope_row)

    if default_profile:
        append_scope(
            default_profile,
            "SVR_APPEARANCE_SOURCE_DEFAULT_PROFILE",
            "SVR_APPEARANCE_SUBJECT_DEFAULT",
            "default_subject",
        )
        append_scope(
            default_profile,
            "SVR_APPEARANCE_SOURCE_DEFAULT_PROFILE",
            "SVR_APPEARANCE_SUBJECT_NPC_SPAWN",
            "npc_spawn",
        )
    for seat in server_seat_subjects:
        profile_id = int(seat.get("appearance_profile_id") or 0)
        profile = profiles_by_id.get(profile_id)
        if not profile:
            continue
        append_scope(
            profile,
            "SVR_APPEARANCE_SOURCE_SERVER_SEAT_PROFILE",
            "SVR_APPEARANCE_SUBJECT_SERVER_SEAT",
            str(seat.get("seat_alias") or ""),
        )

    scope_rows.sort(
        key=lambda row: (
            str(row["subject_kind"]),
            str(row["subject_key"]),
            int(row["appearance_profile_id"]),
        )
    )

    reachable_skin_definitions: list[dict[str, Any]] = []
    reachable_equipped_rows: list[dict[str, Any]] = []
    reachable_default_rows: list[dict[str, Any]] = []
    reachable_mounted_family_keys: list[dict[str, Any]] = []

    mount_definitions_by_runtime: dict[str, list[dict[str, Any]]] = {}
    for mount_definition in mount_definitions:
        runtime_mount_state = mount_definition.get("runtime_mount_state")
        if isinstance(runtime_mount_state, str) and runtime_mount_state:
            mount_definitions_by_runtime.setdefault(runtime_mount_state, []).append(
                mount_definition
            )

    actor_mounted_scopes = [
        scope
        for scope in scope_rows
        if scope["projection_kind"] == "SVR_APPEARANCE_PROJECTION_PROFILE"
        and scope["subject_kind"]
        in {
            "SVR_APPEARANCE_SUBJECT_DEFAULT",
            "SVR_APPEARANCE_SUBJECT_SERVER_SEAT",
            "SVR_APPEARANCE_SUBJECT_NPC_SPAWN",
        }
    ]

    for scope in scope_rows:
        reachable_skin_definitions.append(
            {
                "appearance_emission_scope_id": scope["appearance_emission_scope_id"],
                "appearance_profile_id": scope["appearance_profile_id"],
                "appearance_profile_slug": scope["appearance_profile_slug"],
                "source_kind": scope["source_kind"],
                "subject_kind": scope["subject_kind"],
                "projection_kind": scope["projection_kind"],
                "skin_definition_id": scope["skin_definition_id"],
                "skin_definition_slug": scope["skin_definition_slug"],
            }
        )
        for equip_row in equip_rule_rows:
            reachable_equipped_rows.append(
                {
                    "appearance_emission_scope_id": scope["appearance_emission_scope_id"],
                    "appearance_profile_id": scope["appearance_profile_id"],
                    "source_kind": scope["source_kind"],
                    "subject_kind": scope["subject_kind"],
                    "projection_kind": scope["projection_kind"],
                    "item_definition_id": equip_row["item_definition_id"],
                    "item_definition_slug": equip_row["item_definition_slug"],
                    "slot_kind_id": equip_row["slot_kind_id"],
                    "slot_kind_slug": equip_row["slot_kind_slug"],
                    "visual_style_id": equip_row["visual_style_id"],
                    "visual_style_slug": equip_row["visual_style_slug"],
                    "gameplay_kind": equip_row["gameplay_kind"],
                    "row_source_kind": "equip_rule_set",
                }
            )

    seen_mounted_family_keys: set[tuple[str, int, int, str, str, str]] = set()
    for scope in actor_mounted_scopes:
        for selector in selector_tables:
            if selector.get("subject_kind") != "actor":
                continue
            contract = _ensure_object(
                selector.get("selector_input_contract"),
                f"presentation_selector_tables[{selector.get('selector_slug', '?')}].selector_input_contract",
            )
            mounted_states = [
                state
                for state in _ensure_string_list(
                    contract.get("mount_states", []),
                    f"presentation_selector_tables[{selector.get('selector_slug', '?')}].selector_input_contract.mount_states",
                )
                if state != "unmounted"
            ]
            if not mounted_states:
                continue
            unmapped_states = [
                state
                for state in mounted_states
                if not mount_definitions_by_runtime.get(state)
            ]
            if unmapped_states:
                raise BundleCompileError(
                    f"selector '{selector.get('selector_slug', '?')}' admits mount_state "
                    f"'{unmapped_states[0]}' but no mount_definition maps to that runtime state"
                )
            fallback_chain = [
                _normalize_variant_signature(
                    signature,
                    f"presentation_selector_tables[{selector.get('selector_slug', '?')}].selector_input_contract.variant_fallback_chain",
                )
                for signature in _ensure_list(
                    contract.get("variant_fallback_chain", []),
                    f"presentation_selector_tables[{selector.get('selector_slug', '?')}].selector_input_contract.variant_fallback_chain",
                )
            ]
            if not fallback_chain:
                continue
            for desired_signature in fallback_chain:
                for mounted_state in mounted_states:
                    for mount_definition in mount_definitions_by_runtime.get(mounted_state, []):
                        signature_key = _variant_signature_tuple(desired_signature)
                        row_key = (
                            scope["appearance_emission_scope_id"],
                            int(selector["presentation_kind_id"]),
                            int(mount_definition["id"]),
                            *signature_key,
                        )
                        if row_key in seen_mounted_family_keys:
                            continue
                        seen_mounted_family_keys.add(row_key)
                        reachable_mounted_family_keys.append(
                            {
                                "appearance_emission_scope_id": scope["appearance_emission_scope_id"],
                                "appearance_profile_id": scope["appearance_profile_id"],
                                "source_kind": scope["source_kind"],
                                "subject_kind": scope["subject_kind"],
                                "projection_kind": scope["projection_kind"],
                                "selector_slug": selector.get("selector_slug"),
                                "combat_states": list(contract.get("combat_states") or []),
                                "presentation_kind_id": int(selector["presentation_kind_id"]),
                                "presentation_kind_slug": selector.get("presentation_kind_slug"),
                                "mount_definition_id": int(mount_definition["id"]),
                                "mount_definition_slug": mount_definition.get("slug"),
                                "mount_state": mounted_state,
                                "height_class": desired_signature["height_class"],
                                "width_class": desired_signature["width_class"],
                                "silhouette_class": desired_signature["silhouette_class"],
                            }
                        )

    return {
        "appearance_emission_scopes": scope_rows,
        "rule_sets": {
            "equip_rule_set_id": equip_rule_set_id,
            "mount_rule_set_id": mount_rule_set_id,
            "default_injection_rule_set_id": default_injection_rule_set_id,
            "spawn_injection_rule_set_id": spawn_injection_rule_set_id,
            "equip_rule_rows": equip_rule_rows,
            "mount_rule_rows": mount_rule_rows,
            "default_injection_rule_rows": default_injection_rule_rows,
            "spawn_injection_rule_rows": spawn_injection_rule_rows,
        },
        "actor_mounted_scope_filter": {
            "projection_kind": "SVR_APPEARANCE_PROJECTION_PROFILE",
            "subject_kinds": [
                "SVR_APPEARANCE_SUBJECT_DEFAULT",
                "SVR_APPEARANCE_SUBJECT_SERVER_SEAT",
                "SVR_APPEARANCE_SUBJECT_NPC_SPAWN",
            ],
        },
        "reachable_skin_definitions": reachable_skin_definitions,
        "reachable_equipped_appearance_rows": reachable_equipped_rows,
        "reachable_default_injected_rows": reachable_default_rows,
        "reachable_mounted_family_keys": reachable_mounted_family_keys,
    }


def ProveMountedClosureOrReject(
    bundle: dict[str, Any],
    runtime_sheet_cache: dict[str, Any],
    reachable_state_space: dict[str, Any],
    deps: dict[str, Any],
) -> dict[str, Any]:
    _ensure_object: Callable[..., Any] = deps["_ensure_object"]
    _ensure_list: Callable[..., Any] = deps["_ensure_list"]
    _normalize_variant_signature: Callable[..., Any] = deps["_normalize_variant_signature"]
    _variant_signature_tuple: Callable[..., Any] = deps["_variant_signature_tuple"]
    _format_variant_signature_key: Callable[..., Any] = deps["_format_variant_signature_key"]
    _attachment_order_rows_by_presentation: Callable[..., Any] = deps["_attachment_order_rows_by_presentation"]
    _validate_mounted_runtime_compose_contract: Callable[..., Any] = deps[
        "_validate_mounted_runtime_compose_contract"
    ]
    BundleCompileError = deps["BundleCompileError"]

    catalog = _ensure_object(bundle.get("catalog"), "bundle.catalog")
    layer_definitions = [
        _ensure_object(entry, "catalog.layer_definitions[]")
        for entry in _ensure_list(catalog.get("layer_definitions", []), "catalog.layer_definitions")
    ]
    mounted_wrappers = [
        _ensure_object(entry, "bundle._compile_mounted_wrapper_definitions[]")
        for entry in _ensure_list(
            bundle.get("_compile_mounted_wrapper_definitions", []),
            "bundle._compile_mounted_wrapper_definitions",
        )
    ]
    attachment_order_by_presentation = _attachment_order_rows_by_presentation(bundle)

    layer_by_slug: dict[str, dict[str, Any]] = {}
    rider_layers_by_lane: dict[tuple[str, str, tuple[str, str, str]], list[dict[str, Any]]] = {}
    body_layer_by_key: dict[tuple[int, int, int, int, str, str, str], dict[str, Any]] = {}
    item_layer_by_key: dict[tuple[int, int, int, int, int, int, str, str, str], dict[str, Any]] = {}
    default_visual_style_id = int(deps["APPEARANCE_VISUAL_STYLE_DEFAULT"])

    for layer in layer_definitions:
        slug = layer.get("slug")
        if isinstance(slug, str) and slug:
            layer_by_slug[slug] = layer
        signature = _normalize_variant_signature(
            layer.get("variant_signature"),
            f"layer_definition '{layer.get('slug', '?')}'.variant_signature",
        )
        signature_key = _variant_signature_tuple(signature)
        mount_qualifier_id = layer.get("mount_qualifier_definition_id")
        mount_qualifier_slug = layer.get("mount_qualifier_definition_slug")
        if not isinstance(mount_qualifier_slug, str) or not mount_qualifier_slug:
            continue
        presentation_slug = layer.get("presentation_kind_slug")
        if not isinstance(presentation_slug, str) or not presentation_slug:
            continue
        rider_layers_by_lane.setdefault(
            (mount_qualifier_slug, presentation_slug, signature_key),
            [],
        ).append(layer)
        if (
            layer.get("owner_definition_kind") == "skin"
            and layer.get("slot_kind_slug") == "body"
            and isinstance(mount_qualifier_id, int)
            and mount_qualifier_id > 0
            and int(layer.get("visual_style_id") or 0) == default_visual_style_id
        ):
            body_layer_by_key[
                (
                    int(layer["presentation_kind_id"]),
                    int(layer["owner_definition_id"]),
                    mount_qualifier_id,
                    int(layer.get("condition_item_definition_id") or 0),
                    *signature_key,
                )
            ] = layer
        if (
            layer.get("owner_definition_kind") == "item"
            and layer.get("slot_kind_slug") != "mount"
            and isinstance(mount_qualifier_id, int)
            and mount_qualifier_id > 0
        ):
            item_layer_by_key[
                (
                    int(layer["presentation_kind_id"]),
                    int(layer["owner_definition_id"]),
                    int(layer["slot_kind_id"]),
                    int(layer["visual_style_id"]),
                    mount_qualifier_id,
                    int(layer.get("condition_item_definition_id") or 0),
                    *signature_key,
                )
            ] = layer

    wrapper_by_family: dict[tuple[int, int, str, str, str], list[dict[str, Any]]] = {}
    for wrapper in mounted_wrappers:
        signature = _normalize_variant_signature(
            wrapper.get("variant_signature"),
            f"mounted_wrapper_definition '{wrapper.get('slug', '?')}'.variant_signature",
        )
        signature_key = _variant_signature_tuple(signature)
        family_key = (
            int(wrapper["presentation_kind_id"]),
            int(wrapper["mount_definition_id"]),
            *signature_key,
        )
        existing_rows = wrapper_by_family.get(family_key, [])
        if existing_rows and int(existing_rows[0].get("visual_style_id") or 0) != int(wrapper.get("visual_style_id") or 0):
            raise BundleCompileError(
                "mounted admitted family key may not vary by visual_style_id for "
                f"{wrapper.get('mount_definition_slug', '?')}/"
                f"{wrapper.get('presentation_kind_slug', '?')}/"
                f"{_format_variant_signature_key(signature_key)}"
            )
        wrapper_by_family.setdefault(family_key, []).append(wrapper)

    scope_skins: dict[str, list[dict[str, Any]]] = {}
    for row in reachable_state_space["reachable_skin_definitions"]:
        scope_skins.setdefault(row["appearance_emission_scope_id"], []).append(row)
    scope_items: dict[str, list[dict[str, Any]]] = {}
    for row in reachable_state_space["reachable_equipped_appearance_rows"]:
        scope_items.setdefault(row["appearance_emission_scope_id"], []).append(row)

    mounted_admission_rows: list[dict[str, Any]] = []
    body_rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    closure_rejects: list[dict[str, Any]] = []
    seen_admission_rows: set[tuple[int, int, int, str, str, str]] = set()
    seen_body_rows: set[tuple[int, int, int, int, str, str, str]] = set()
    seen_item_rows: set[tuple[int, int, int, int, int, int, str, str, str]] = set()

    unique_families = sorted(
        reachable_state_space["reachable_mounted_family_keys"],
        key=lambda row: (
            str(row["appearance_emission_scope_id"]),
            int(row["presentation_kind_id"]),
            int(row["mount_definition_id"]),
            str(row["height_class"]),
            str(row["width_class"]),
            str(row["silhouette_class"]),
        ),
    )

    for family in unique_families:
        presentation_kind_id = int(family["presentation_kind_id"])
        mount_definition_id = int(family["mount_definition_id"])
        signature_key = (
            str(family["height_class"]),
            str(family["width_class"]),
            str(family["silhouette_class"]),
        )
        lane_label = (
            f"{family.get('mount_definition_slug', '?')}/"
            f"{family.get('presentation_kind_slug', '?')}/"
            f"{_format_variant_signature_key(signature_key)}"
        )
        lane_failed = False
        wrappers = wrapper_by_family.get((presentation_kind_id, mount_definition_id, *signature_key), [])
        if not wrappers:
            closure_rejects.append(
                {
                    "path": lane_label,
                    "reason": "reachable mounted family lacks admitted wrapper coverage",
                }
            )
            continue
        admitted_body_condition_ids: set[int] = set()
        for wrapper in wrappers:
            condition_item_definition_id = int(wrapper.get("condition_item_definition_id") or 0)
            rider_lane_layers = [
                layer
                for layer in rider_layers_by_lane.get(
                    (
                        str(family.get("mount_definition_slug") or ""),
                        str(family.get("presentation_kind_slug") or ""),
                        signature_key,
                    ),
                    [],
                )
                if int(layer.get("condition_item_definition_id") or 0) == condition_item_definition_id
            ]
            if not rider_lane_layers:
                closure_rejects.append(
                    {
                        "path": lane_label,
                        "reason": "reachable mounted family lacks mount-qualified rider lane coverage",
                    }
                )
                continue
            rider_body_layers = [
                layer
                for layer in rider_lane_layers
                if layer.get("owner_definition_kind") == "skin" and layer.get("slot_kind_slug") == "body"
            ]
            if not rider_body_layers:
                closure_rejects.append(
                    {
                        "path": lane_label,
                        "reason": "reachable mounted family lacks mount-qualified rider body coverage",
                    }
                )
                continue
            parity_layer = layer_by_slug.get(wrapper.get("parity_reference_layer_definition_slug"))
            rear_layer = layer_by_slug.get(wrapper.get("rear_layer_definition_slug"))
            front_layer = layer_by_slug.get(wrapper.get("front_layer_definition_slug"))
            if parity_layer is None or rear_layer is None or front_layer is None:
                closure_rejects.append(
                    {
                        "path": lane_label,
                        "reason": "mounted admitted family references missing compiled surface rows",
                    }
                )
                continue
            try:
                _validate_mounted_runtime_compose_contract(
                    slug=str(wrapper.get("slug", "?")),
                    rear_layer=rear_layer,
                    parity_layer=parity_layer,
                    front_layer=front_layer,
                    rider_offset_by_facing=_ensure_list(
                        wrapper.get("rider_offset_by_facing", []),
                        f"mounted_wrapper_definition '{wrapper.get('slug', '?')}'.rider_offset_by_facing",
                    ),
                    rider_lane_layers=rider_lane_layers,
                    rider_body_layers=rider_body_layers,
                    runtime_sheet_cache=runtime_sheet_cache,
                )
            except BundleCompileError as exc:
                closure_rejects.append(
                    {
                        "path": lane_label,
                        "reason": str(exc),
                    }
                )
                continue
            admitted_body_condition_ids.add(condition_item_definition_id)
            admission_key = (
                presentation_kind_id,
                mount_definition_id,
                condition_item_definition_id,
                *signature_key,
            )
            if admission_key not in seen_admission_rows:
                seen_admission_rows.add(admission_key)
                attachment_order = attachment_order_by_presentation.get(presentation_kind_id, {})
                row = {
                    "presentation_kind_id": presentation_kind_id,
                    "presentation_kind_slug": family.get("presentation_kind_slug"),
                    "mount_definition_id": mount_definition_id,
                    "mount_definition_slug": family.get("mount_definition_slug"),
                    "condition_item_definition_id": condition_item_definition_id,
                    "condition_item_definition_slug": wrapper.get("condition_item_definition_slug"),
                    "height_class": signature_key[0],
                    "width_class": signature_key[1],
                    "silhouette_class": signature_key[2],
                    "rear_layer_definition_id": int(wrapper["rear_layer_definition_id"]),
                    "front_layer_definition_id": int(wrapper["front_layer_definition_id"]),
                    "parity_reference_layer_definition_id": int(
                        wrapper["parity_reference_layer_definition_id"]
                    ),
                    "mounted_effect_lane_policy": str(
                        wrapper["mounted_effect_lane_policy"]
                    ),
                    "rider_offset_by_facing": [
                        dict(offset)
                        for offset in _ensure_list(
                            wrapper.get("rider_offset_by_facing", []),
                            f"mounted_wrapper_definition '{wrapper.get('slug', '?')}'.rider_offset_by_facing",
                        )
                    ],
                    "attachment_order_slot_kind_ids": list(
                        attachment_order.get("slot_kind_ids") or []
                    ),
                    "attachment_order_slot_kind_slugs": list(
                        attachment_order.get("slot_kind_slugs") or []
                    ),
                }
                if condition_item_definition_id == 0:
                    row.pop("condition_item_definition_id", None)
                    row.pop("condition_item_definition_slug", None)
                mounted_admission_rows.append(row)

        if any(int(wrapper.get("condition_item_definition_id") or 0) != 0 for wrapper in wrappers):
            lane_failed = False

        for body_condition_item_id in sorted(admitted_body_condition_ids):
            for skin_row in scope_skins.get(family["appearance_emission_scope_id"], []):
                body_key = (
                    presentation_kind_id,
                    int(skin_row["skin_definition_id"]),
                    mount_definition_id,
                    body_condition_item_id,
                    *signature_key,
                )
                body_layer = body_layer_by_key.get(body_key)
                if not body_layer:
                    closure_rejects.append(
                        {
                            "path": (
                                f"{skin_row.get('skin_definition_slug', '?')}/"
                                f"{lane_label}"
                            ),
                            "reason": "reachable mounted family lacks admitted mount-qualified body coverage",
                        }
                    )
                    lane_failed = True
                    continue
                if body_key not in seen_body_rows:
                    seen_body_rows.add(body_key)
                    row = {
                        "presentation_kind_id": presentation_kind_id,
                        "presentation_kind_slug": family.get("presentation_kind_slug"),
                        "skin_definition_id": int(skin_row["skin_definition_id"]),
                        "skin_definition_slug": skin_row.get("skin_definition_slug"),
                        "mount_definition_id": mount_definition_id,
                        "mount_definition_slug": family.get("mount_definition_slug"),
                        "condition_item_definition_id": body_condition_item_id,
                        "condition_item_definition_slug": body_layer.get("condition_item_definition_slug"),
                        "height_class": signature_key[0],
                        "width_class": signature_key[1],
                        "silhouette_class": signature_key[2],
                        "body_layer_definition_id": int(body_layer["id"]),
                    }
                    if body_condition_item_id != 0 and row["condition_item_definition_slug"] is None:
                        raise BundleCompileError(
                            f"condition_item_definition_slug missing for "
                            f"body_condition_item_id={body_condition_item_id} "
                            f"in presentation_kind_id={presentation_kind_id}"
                        )
                    if body_condition_item_id == 0:
                        row.pop("condition_item_definition_id", None)
                        row.pop("condition_item_definition_slug", None)
                    body_rows.append(row)

        for item_row in scope_items.get(family["appearance_emission_scope_id"], []):
            for condition_item_definition_id in sorted(admitted_body_condition_ids):
                if (
                    condition_item_definition_id
                    and item_row.get("slot_kind_slug") == "weapon"
                    and int(item_row["item_definition_id"]) != condition_item_definition_id
                ):
                    continue
                item_key = (
                    presentation_kind_id,
                    int(item_row["item_definition_id"]),
                    int(item_row["slot_kind_id"]),
                    int(item_row["visual_style_id"]),
                    mount_definition_id,
                    condition_item_definition_id,
                    *signature_key,
                )
                item_layer = item_layer_by_key.get(item_key)
                if not item_layer:
                    closure_rejects.append(
                        {
                            "path": (
                                f"{item_row.get('item_definition_slug', '?')}/"
                                f"{item_row.get('slot_kind_slug', '?')}/"
                                f"{item_row.get('visual_style_slug', '?')}/"
                                f"{lane_label}"
                            ),
                            "reason": "reachable mounted family lacks admitted mount-qualified item coverage",
                        }
                    )
                    lane_failed = True
                    continue
                if item_key not in seen_item_rows:
                    seen_item_rows.add(item_key)
                    row = {
                        "presentation_kind_id": presentation_kind_id,
                        "presentation_kind_slug": family.get("presentation_kind_slug"),
                        "item_definition_id": int(item_row["item_definition_id"]),
                        "item_definition_slug": item_row.get("item_definition_slug"),
                        "slot_kind_id": int(item_row["slot_kind_id"]),
                        "slot_kind_slug": item_row.get("slot_kind_slug"),
                        "visual_style_id": int(item_row["visual_style_id"]),
                        "visual_style_slug": item_row.get("visual_style_slug"),
                        "mount_definition_id": mount_definition_id,
                        "mount_definition_slug": family.get("mount_definition_slug"),
                        "condition_item_definition_id": condition_item_definition_id,
                        "condition_item_definition_slug": item_layer.get("condition_item_definition_slug"),
                        "height_class": signature_key[0],
                        "width_class": signature_key[1],
                        "silhouette_class": signature_key[2],
                        "layer_definition_id": int(item_layer["id"]),
                    }
                    if condition_item_definition_id == 0:
                        row.pop("condition_item_definition_id", None)
                        row.pop("condition_item_definition_slug", None)
                    else:
                        if int(item_layer.get("condition_item_definition_id") or 0) != condition_item_definition_id:
                            raise BundleCompileError(
                                f"mounted conditioned item row lost condition ownership: "
                                f"item={item_row.get('item_definition_slug', '?')} "
                                f"slot={item_row.get('slot_kind_slug', '?')} "
                                f"condition_item={condition_item_definition_id}"
                            )
                        if row["condition_item_definition_slug"] is None:
                            raise BundleCompileError(
                                f"condition_item_definition_slug missing for "
                                f"item={item_row.get('item_definition_slug', '?')} "
                                f"slot={item_row.get('slot_kind_slug', '?')} "
                                f"condition_item={condition_item_definition_id}"
                            )
                    item_rows.append(row)

        if lane_failed:
            continue

    mounted_admission_rows.sort(
        key=lambda row: (
            int(row["presentation_kind_id"]),
            int(row["mount_definition_id"]),
            str(row["height_class"]),
            str(row["width_class"]),
            str(row["silhouette_class"]),
        )
    )
    body_rows.sort(
        key=lambda row: (
            int(row["presentation_kind_id"]),
            int(row["skin_definition_id"]),
            int(row["mount_definition_id"]),
            str(row["height_class"]),
            str(row["width_class"]),
            str(row["silhouette_class"]),
        )
    )
    item_rows.sort(
        key=lambda row: (
            int(row["presentation_kind_id"]),
            int(row["item_definition_id"]),
            int(row["slot_kind_id"]),
            int(row["visual_style_id"]),
            int(row["mount_definition_id"]),
            int(row.get("condition_item_definition_id", 0) or 0),
            str(row["height_class"]),
            str(row["width_class"]),
            str(row["silhouette_class"]),
        )
    )

    if closure_rejects:
        raise BundleCompileError(
            "reachable mounted closure failed closed on full cartesian-product audit",
            rejects=closure_rejects,
        )

    return {
        "mounted_admission": mounted_admission_rows,
        "admitted_mount_qualified_body_layers": body_rows,
        "admitted_mount_qualified_item_layers": item_rows,
        "raw_mounted_authoring_rows": mounted_wrappers,
    }


def EmitAppearanceRuntimeBundle(
    bundle: dict[str, Any],
    runtime_sheet_cache: dict[str, Any],
    deps: dict[str, Any],
) -> dict[str, Any]:
    reachable_state_space = BuildReachableAppearanceStateSpace(bundle, deps)
    # FL-2345: ProveMountedClosureOrReject is no longer called here.
    # bundle catalog mutations (mounted_admission, etc.) now belong to
    # appearance_bundle._validate_selector_reachability_mounted_admission,
    # the single compile-time mounted admission owner.
    return {
        "reachable_state_space": reachable_state_space,
    }
