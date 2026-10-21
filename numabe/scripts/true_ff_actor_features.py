#!/usr/bin/env python3
"""Actor pose feature-bank extension for the single True FF bank compiler.

This module deliberately has no command-line entry point and does not write
files.  ``scripts/true_ff_feature_bank.py`` remains the only compiler and can
import ``compile_actor_feature_sources`` to add actor pose rows to its existing
bank/index output.  The extension calls the bank module's existing mesh
builders, so actor feature records have the same ABI and file digest as every
other True FF asset.

The actor pose products already carry a fixed rig-part word in face-row lane
15.  A pose product is admitted only when every frame has the same source
corner-incidence partition, part-word sequence, first-seen edge addresses, and
face topology. The first pose owns one spatial tree; each later pose clones
that tree by exact feature.local and refits its own spheres/cones, classes,
chains, and geometry. Feature records receive a stable id from the validated
product/topology/address namespace, never from a pose digest, screen
coordinate, or independently rebuilt tree. If the baked topology does not
satisfy those invariants, the product is reported in
``manifest["rejections"]`` and produces no bank or runtime source.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


FLOATS_PER_FACE = 16
PART_WORD_LANE = 15
ACTOR_FEATURE_MANIFEST_SCHEMA = "asciicker.true_ff.actor_feature_sources.v2"
ACTOR_FEATURE_IDENTITY = "actor_product_address_v2"
ACTOR_ADDRESS_LAYOUT_VERSION = "actor_edge_address_v2"
ACTOR_PART_WORD_ENCODING = "rig_bone_part_u8_v1"
ACTOR_PART_LOCAL_LIMIT = 8192


class ActorTopologyError(ValueError):
    """A pose product cannot preserve face/part identity across its frames."""


def _f32(value: Any) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _product_path(value: str | Path, root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _load_rows(product: Mapping[str, Any], pose: Mapping[str, Any], root: Path,
               blob_cache: dict[Path, bytes]) -> list[float]:
    """Read one baked pose's float32 face rows.

    FL-4907: the packed X Bot row bank (row_resource/row_offset) is deleted;
    the X Bot is a 52-part rigid rig compiled by true_ff_rigid_features.
    """
    direct = pose.get("face_rows")
    if isinstance(direct, list):
        return [_f32(value) for value in direct]
    raise ActorTopologyError("pose_face_rows_missing")


def _part_words(rows: Sequence[float], face_count: int) -> tuple[int, ...]:
    if face_count <= 0 or len(rows) != face_count * FLOATS_PER_FACE:
        raise ActorTopologyError("pose_face_rows_invalid")
    words: list[int] = []
    for face in range(face_count):
        value = float(rows[face * FLOATS_PER_FACE + PART_WORD_LANE])
        if not math.isfinite(value) or value < 0 or value != int(value) or value >= (1 << 20):
            raise ActorTopologyError("pose_part_word_invalid")
        words.append(int(value))
    if len(set(words)) != len(words):
        raise ActorTopologyError("pose_part_word_not_unique")
    return tuple(words)


def _stable_edge_id(namespace: str, address: tuple[int, int]) -> int:
    """Return the uint address identity used by the existing bank ABI.

    ``namespace`` is made once per validated product.  It deliberately does
    not contain the pose digest: a pose is a geometry/classification variant
    of one address layout, not a new identity namespace.
    """
    payload = (f"{namespace}|{ACTOR_ADDRESS_LAYOUT_VERSION}|{address[0]}|{address[1]}").encode()
    return struct.unpack("<I", hashlib.sha256(payload).digest()[:4])[0]


def _stable_chain_ids(feats: Sequence[Any], namespace: str) -> None:
    """Make chain partition ids stable when the baked edge topology is stable."""
    groups: dict[int, list[int]] = {}
    for feature in feats:
        groups.setdefault(int(feature.chain), []).append(int(feature.sid))
    for feature in feats:
        members = groups[int(feature.chain)]
        payload = (f"{namespace}|chain|{min(members)}").encode()
        feature.chain = struct.unpack("<I", hashlib.sha256(payload).digest()[:4])[0]


def _corner_incidence(triangles: Sequence[Any], bank_module: Any) -> tuple:
    """Canonical source connectivity, independent of the pose coordinates.

    FL-4907: counts do not establish correspondence. Number welded vertices
    by first source-corner occurrence using the mesh builder's own weld law.
    Keep triangle ordering, winding and source-face membership in the result.
    This validates what the current compiler actually builds; it does not
    invent rig topology or repair a product whose welds change during a pose.
    """
    vertices: dict[tuple, int] = {}
    incidence = []
    for points, _material, source_face in triangles:
        corners = []
        for point in points:
            key = bank_module._key(point)
            corners.append(vertices.setdefault(key, len(vertices)))
        incidence.append((int(source_face), *corners))
    return tuple(incidence)


def _edge_layout(rows: Sequence[float], triangles: Sequence[Any],
                 tri_edges: Sequence[Sequence[int]], features: Sequence[Any]) -> tuple[tuple, tuple]:
    """Return first-seen source addresses and the face topology they address.

    The bank's mesh builder intentionally numbers edges by first appearance.
    Preserve that ordering as the product's address layout, then validate it
    for every later pose. The second address word includes the triangle split
    ordinal (3 * split + edge slot), so the two halves of a source quad cannot
    alias. ``setdefault`` retains the shared diagonal's first occurrence.
    """
    addresses: dict[int, tuple[int, int]] = {}
    splits: dict[int, int] = {}
    for triangle_index, edge_ids in enumerate(tri_edges):
        source_face = int(triangles[triangle_index][2])
        split = splits.get(source_face, 0)
        splits[source_face] = split + 1
        part_word = int(rows[source_face * FLOATS_PER_FACE + PART_WORD_LANE])
        for edge_slot, local_index in enumerate(edge_ids):
            addresses.setdefault(int(local_index), (part_word, 3 * split + edge_slot))
    if set(addresses) != set(range(len(features))):
        raise ActorTopologyError("edge_address_layout_invalid")
    ordered = tuple(addresses[index] for index in range(len(features)))
    faces = tuple(tuple(int(value) for value in edge_ids) for edge_ids in tri_edges)
    return ordered, faces


def _address_signature(addresses: Sequence[tuple[int, int]],
                       faces: Sequence[Sequence[int]], tree: Any) -> str:
    def layout(node):
        return [node.level, [layout(child) for child in node.children],
                [int(feature.local) for feature, _ in node.feats]]
    payload = {
        "layout_version": ACTOR_ADDRESS_LAYOUT_VERSION,
        "addresses": [list(address) for address in addresses],
        "faces": [list(face) for face in faces],
        # Leaf order determines serialized feature AND face addresses.
        # Connectivity alone does not certify that spatial permutation.
        "tree": layout(tree),
    }
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def _product_namespace(actor_kind: str, topology_signature: str,
                       address_signature: str) -> str:
    payload = (
        f"{ACTOR_FEATURE_IDENTITY}|{ACTOR_ADDRESS_LAYOUT_VERSION}|{actor_kind}|"
        f"{topology_signature}|{address_signature}"
    ).encode()
    return _sha256(payload)


def _actor_content_hash(rows: Sequence[float], face_count: int,
                        layout_signature: str, namespace: str, bank_module: Any) -> str:
    """Hash pose payload and address-layout identity together.

    The normal bank digest is still used as the pose-payload primitive, but a
    layout-version/signature change must produce a different bank filename and
    runtime header even when the float rows happen to be unchanged.
    """
    pose_hash = bank_module.content_hash_of(rows, face_count)
    payload = (
        f"{ACTOR_FEATURE_IDENTITY}|{ACTOR_ADDRESS_LAYOUT_VERSION}|"
        f"{namespace}|{layout_signature}|{pose_hash}"
    ).encode()
    return _sha256(payload)


def _restore_source_orientation(features: Sequence[Any], snapshots: Mapping[int, tuple]) -> None:
    """Restore source edge direction after chain construction.

    ``build_chains`` may reverse a feature while walking a class chain.  The
    source row's edge parameter direction is the stable correspondence law,
    so restore its endpoints and normals and swap the cumulative arc endpoints
    when the walk was reversed.  This keeps ``a0`` attached to source ``p0``
    and ``a1`` attached to source ``p1``.
    """
    for feature in features:
        original = snapshots[id(feature)]
        p0, p1, k0, k1, fn0, fn1, vn0, vn1 = original
        if feature.k0 == k0:
            pass
        elif feature.k1 == k0:
            feature.a0, feature.a1 = feature.a1, feature.a0
        else:
            raise ActorTopologyError("chain_orientation_lost_source_edge")
        feature.p0, feature.p1 = p0, p1
        feature.k0, feature.k1 = k0, k1
        feature.fn0, feature.fn1 = fn0, fn1
        feature.vn0, feature.vn1 = vn0, vn1


def _build_reference_tree(features: Sequence[Any], bank_module: Any) -> Any:
    """Build the one product-owned tree through the bank's octree primitive."""
    if not features:
        raise ActorTopologyError("actor_product_has_no_features")
    items = [
        (feature, tuple((feature.p0[j] + feature.p1[j]) * 0.5 for j in range(3)))
        for feature in features
    ]
    all_points = [feature.p0 for feature in features] + [feature.p1 for feature in features]
    lo = [min(point[j] for point in all_points) for j in range(3)]
    hi = [max(point[j] for point in all_points) for j in range(3)]
    centre = tuple((lo[j] + hi[j]) * 0.5 for j in range(3))
    half = max(hi[j] - lo[j] for j in range(3)) * 0.5 + 1e-9
    return bank_module._octree(items, centre, half, 0)


def _clone_reference_tree(root: Any, features: Sequence[Any], bank_module: Any) -> Any:
    """Clone node topology while rebinding leaves by exact canonical local."""
    by_local = {int(feature.local): feature for feature in features}

    def clone(source: Any) -> Any:
        node = bank_module.Node()
        node.level = source.level
        node.children = [clone(child) for child in source.children]
        if source.children:
            node.feats = []
        else:
            rebound = []
            for feature, midpoint in source.feats:
                try:
                    rebound.append((by_local[int(feature.local)], midpoint))
                except KeyError as exc:
                    raise ActorTopologyError("canonical_tree_feature_rebind_failed") from exc
            node.feats = rebound
        node.region_points = source.region_points
        node.region_normals = source.region_normals
        node.terrain_fixed = source.terrain_fixed
        return node

    return clone(root)


def _build_pose_bank(rows: Sequence[float], face_count: int, actor_kind: str,
                     bank_module: Any, *, root_nodes: Any | None = None,
                     canonical_addresses: Sequence[tuple[int, int]] | None = None,
                     canonical_tri_edges: Sequence[Sequence[int]] | None = None,
                     address_namespace: str | None = None,
                     address_signature: str | None = None,
                     part_local_limit: int = ACTOR_PART_LOCAL_LIMIT,
                     prepared: tuple[Sequence[Any], Sequence[Any], Sequence[Sequence[int]]] | None = None,
                     ) -> dict[str, Any]:
    """Build one pose through the existing bank primitives.

    Production passes a cloned product tree and canonical addresses.  The
    optional arguments intentionally leave this helper directly callable for
    focused compiler tests and small fixtures.
    """
    if part_local_limit != ACTOR_PART_LOCAL_LIMIT:
        raise ActorTopologyError("actor_part_word_encoding_not_declared")
    if prepared is None:
        triangles = bank_module.triangles_from_rows(rows, face_count)
        features, source_tri_edges, _face_normals = bank_module.mesh_features(triangles)
    else:
        triangles, features, source_tri_edges = prepared
    source_addresses, _source_faces = _edge_layout(rows, triangles, source_tri_edges, features)
    if canonical_addresses is None:
        canonical_addresses = source_addresses
    elif tuple(source_addresses) != tuple(canonical_addresses):
        raise ActorTopologyError("topology_varies_between_poses")
    canonical_local = {address: index for index, address in enumerate(canonical_addresses)}
    if len(canonical_local) != len(canonical_addresses):
        raise ActorTopologyError("canonical_address_collision")
    for feature in features:
        try:
            feature.local = canonical_local[source_addresses[int(feature.local)]]
        except (IndexError, KeyError) as exc:
            raise ActorTopologyError("canonical_address_rebind_failed") from exc
    features_by_local = {int(feature.local): feature for feature in features}
    if set(features_by_local) != set(range(len(features))):
        raise ActorTopologyError("canonical_feature_range_invalid")
    if canonical_tri_edges is None:
        tri_edges = tuple(
            tuple(int(canonical_local[source_addresses[int(local)]]) for local in edge_ids)
            for edge_ids in source_tri_edges
        )
    else:
        tri_edges = tuple(tuple(int(value) for value in edge_ids)
                          for edge_ids in canonical_tri_edges)
    if len(tri_edges) != len(source_tri_edges):
        raise ActorTopologyError("canonical_face_count_invalid")
    topology_signature = _sha256(json.dumps(
        [_part_words(rows, face_count), _corner_incidence(triangles, bank_module)],
        separators=(",", ":")).encode())
    if root_nodes is None:
        root_nodes = _build_reference_tree(features, bank_module)
    layout_signature = address_signature or _address_signature(canonical_addresses, tri_edges, root_nodes)
    namespace = address_namespace or _product_namespace(actor_kind, topology_signature, layout_signature)
    for feature in features:
        feature.sid = _stable_edge_id(namespace, tuple(canonical_addresses[int(feature.local)]))
    if len({int(feature.sid) for feature in features}) != len(features):
        raise ActorTopologyError("stable_edge_identity_collision")
    snapshots = {
        id(feature): (feature.p0, feature.p1, feature.k0, feature.k1,
                      feature.fn0, feature.fn1, feature.vn0, feature.vn1)
        for feature in features
    }
    bank_module.build_chains(features)
    _restore_source_orientation(features, snapshots)
    _stable_chain_ids(features, namespace)
    assemble_root = None
    output_addresses: tuple[tuple[int, int], ...] | None = None
    if root_nodes is not None:
        assemble_root = _clone_reference_tree(root_nodes, features, bank_module)
        output_addresses = tuple(
            tuple(canonical_addresses[int(feature.local)])
            for leaf in bank_module._subtree_leaves(assemble_root)
            for feature, _midpoint in leaf.feats
        )
    content_hash = _actor_content_hash(rows, face_count, layout_signature, namespace, bank_module)
    result = bank_module.assemble(
        features, tri_edges, [triangle[1] for triangle in triangles], root_nodes=assemble_root,
        triangle_source_faces=[triangle[2] for triangle in triangles], source_face_count=face_count
    )
    # Preserve the source bone part in each retained feature, not the active
    # pose/face index. Upper flag bits are unused by mesh material/open facts;
    # P2 carries this part into P5's representation word. The address namespace
    # and spatial permutation still come from the canonical product tree.
    if output_addresses is None:
        raise ActorTopologyError("canonical_output_addresses_missing")
    feature_bytes = bytearray(result["features"])
    for index, address in enumerate(output_addresses):
        part = int(address[0]) // part_local_limit
        if part < 0 or part > 255:
            raise ActorTopologyError("actor_part_representation_range_invalid")
        offset = index * int(bank_module.ABI["FEATURE_BYTES"]) + 60
        flags = struct.unpack_from("<I", feature_bytes, offset)[0]
        struct.pack_into("<I", feature_bytes, offset, (flags & 0x00ffffff) | (part << 24))
    result["features"] = bytes(feature_bytes)
    # A representative belongs to the selected source triangle, whose rig
    # part can differ from the part that first owned one of its shared edges.
    # Preserve that exact part through the primitive -> serialized-face map.
    face_bytes = bytearray(result["faces"])
    for source_face in range(face_count):
        part = int(rows[source_face * FLOATS_PER_FACE + PART_WORD_LANE]) // part_local_limit
        if part < 0 or part > 255:
            raise ActorTopologyError("actor_face_representation_range_invalid")
        for split in range(2):
            _leaf, face = struct.unpack_from("<2I", result["primitive_refs"],
                                            (source_face * 2 + split) * bank_module.ABI["PRIMITIVE_REF_BYTES"])
            if face == bank_module.ABI["UINT_NONE"]:
                continue
            offset = face * bank_module.ABI["FACE_BYTES"] + 12
            flags = struct.unpack_from("<I", face_bytes, offset)[0]
            struct.pack_into("<I", face_bytes, offset, (flags & 0x00ffffff) | (part << 24))
    result["faces"] = bytes(face_bytes)
    # A new semantic payload must not alias an old digest whose upper flag
    # byte was empty. This is ordinary retained-bank content addressing.
    content_hash = _sha256(bytes.fromhex(content_hash) + ACTOR_PART_WORD_ENCODING.encode())
    result.update({
        "content_hash": content_hash,
        "chain_count": len({int(feature.chain) for feature in features}),
        "triangle_count": len(triangles),
        "source_face_count": face_count,
        "actor_kind": actor_kind,
        "actor_feature_identity": ACTOR_FEATURE_IDENTITY,
        "address_layout_version": ACTOR_ADDRESS_LAYOUT_VERSION,
        "canonical_address_signature": layout_signature,
        "topology_signature": topology_signature,
        "pose_payload_hash": bank_module.content_hash_of(rows, face_count),
        "canonical_addresses": tuple(canonical_addresses),
        "output_addresses": output_addresses,
        "source_corner_incidence": _corner_incidence(triangles, bank_module),
    })
    return result


def _pose_records(product: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = product.get("poses")
    if isinstance(value, dict):
        records: list[Mapping[str, Any]] = []
        for pose_id, record in value.items():
            if not isinstance(record, dict):
                raise ActorTopologyError("pose_record_not_dictionary")
            copy = dict(record)
            copy.setdefault("pose_id", str(pose_id))
            records.append(copy)
        return records
    if isinstance(value, list) and all(isinstance(record, dict) for record in value):
        return list(value)
    raise ActorTopologyError("poses_missing_or_not_collection")


def _source_digest(record: Mapping[str, Any], owner: str) -> str:
    """Require the source certificate consumed by ActorPoseGeometryDB.

    This validates the certificate's representation, not its source producer's
    hashing recipe. Preserve its exact spelling for the runtime equality check.
    """
    value = record.get("content_sha256", record.get("content_hash", ""))
    if not isinstance(value, str) or len(value) != 64 \
            or any(character not in "0123456789abcdef" for character in value.lower()):
        raise ActorTopologyError(f"{owner}_source_digest_invalid")
    return value


def compile_actor_feature_sources(
    product_paths: Iterable[str | Path],
    bank_module: Any,
    root: Path | None = None,
) -> dict[str, Any]:
    """Compile actor pose banks for the existing ``true_ff_feature_bank``.

    ``bank_module`` is the imported ``true_ff_feature_bank`` module.  Passing
    it explicitly keeps this extension an importable compiler component and
    prevents a second ABI or command-line compiler from forming.  The return
    value is pure data: ``banks`` maps digest to the normal bank dictionaries,
    and ``manifest`` maps ``actor_kind/pose_id`` to those digests.  The caller
    owns writing ``encode_file(bank, sources)`` and merging the bank index.
    """
    repo_root = (root or Path(__file__).resolve().parents[1]).resolve()
    banks: dict[str, dict[str, Any]] = {}
    products: dict[str, dict[str, Any]] = {}
    rejections: list[dict[str, str]] = []
    blob_cache: dict[Path, bytes] = {}
    for product_value in product_paths:
        product_path = _product_path(product_value, repo_root).resolve()
        product_rel = _rel(product_path, repo_root)
        try:
            product = json.loads(product_path.read_text(encoding="utf-8"))
            actor_kind = str(product.get("actor_kind", ""))
            if not actor_kind and str(product.get("schema", "")).endswith(".hunter_rig_animation_rows.v2"):
                actor_kind = "hunter"
            if not actor_kind:
                raise ActorTopologyError("actor_kind_missing")
            if product.get("geometry_contract", {}).get("face_part_identity") == \
                    "row 3 w = base bone index * 8192 + triangle ordinal inside the body":
                part_local_limit = ACTOR_PART_LOCAL_LIMIT
            else:
                raise ActorTopologyError("actor_part_word_encoding_not_declared")
            poses = _pose_records(product)
            if not poses:
                raise ActorTopologyError("poses_missing_or_not_collection")
            # Reject an unusable runtime certificate before building any bank,
            # including when the malformed record occurs late in the product.
            product_source_hash = _source_digest(product, "product")
            pose_source_hashes = [_source_digest(pose, "pose") for pose in poses]
            pose_entries: dict[str, dict[str, Any]] = {}
            reference_words: tuple[int, ...] | None = None
            reference_face_count: int | None = None
            reference_feature_count: int | None = None
            reference_incidence: tuple | None = None
            canonical_addresses: tuple[tuple[int, int], ...] | None = None
            canonical_tri_edges: tuple[tuple[int, ...], ...] | None = None
            address_signature: str | None = None
            topology_signature: str | None = None
            address_namespace: str | None = None
            canonical_face_addresses: tuple[tuple[tuple[int, int], ...], ...] | None = None
            reference_tree: Any | None = None
            product_banks: dict[str, dict[str, Any]] = {}
            for pose, pose_source_hash in zip(poses, pose_source_hashes):
                pose_id = str(pose.get("pose_id", ""))
                face_count_value = pose.get("face_count")
                if not pose_id or not isinstance(face_count_value, (int, float)) \
                        or int(face_count_value) != face_count_value:
                    raise ActorTopologyError("pose_identity_or_count_invalid")
                face_count = int(face_count_value)
                rows = _load_rows(product, pose, repo_root, blob_cache)
                words = _part_words(rows, face_count)
                triangles = bank_module.triangles_from_rows(rows, face_count)
                raw_features, raw_tri_edges, _raw_normals = bank_module.mesh_features(triangles)
                source_addresses, _source_faces = _edge_layout(
                    rows, triangles, raw_tri_edges, raw_features
                )
                source_face_addresses = tuple(
                    tuple(source_addresses[int(local)] for local in edge_ids)
                    for edge_ids in raw_tri_edges
                )
                incidence = _corner_incidence(triangles, bank_module)
                if reference_words is None:
                    reference_words = words
                    reference_face_count = face_count
                    reference_feature_count = len(raw_features)
                    reference_incidence = incidence
                    canonical_addresses = tuple(source_addresses)
                    canonical_tri_edges = tuple(
                        tuple(int(value) for value in edge_ids) for edge_ids in raw_tri_edges
                    )
                    canonical_face_addresses = source_face_addresses
                    reference_tree = _build_reference_tree(raw_features, bank_module)
                    address_signature = _address_signature(canonical_addresses, canonical_tri_edges, reference_tree)
                    topology_signature = _sha256(
                        json.dumps(
                            [reference_words, reference_incidence],
                            separators=(",", ":"),
                        ).encode()
                    )
                    address_namespace = _product_namespace(
                        actor_kind, topology_signature, address_signature
                    )
                    # The first-seen pose establishes node topology once.  All
                    # later poses clone and rebind this tree by exact feature.local.
                elif words != reference_words or face_count != reference_face_count \
                        or incidence != reference_incidence:
                    raise ActorTopologyError("topology_varies_between_poses")
                if tuple(source_addresses) != canonical_addresses:
                    raise ActorTopologyError("topology_varies_between_poses")
                if source_face_addresses != canonical_face_addresses:
                    raise ActorTopologyError("topology_varies_between_poses")
                pose_bank = _build_pose_bank(
                    rows,
                    face_count,
                    actor_kind,
                    bank_module,
                    root_nodes=reference_tree,
                    canonical_addresses=canonical_addresses,
                    canonical_tri_edges=canonical_tri_edges,
                    address_namespace=address_namespace,
                    address_signature=address_signature,
                    part_local_limit=part_local_limit,
                    prepared=(triangles, raw_features, raw_tri_edges),
                )
                feature_count = int(pose_bank["feature_count"])
                pose_incidence = pose_bank.pop("source_corner_incidence")
                if pose_incidence != reference_incidence:
                    raise ActorTopologyError("topology_varies_between_poses")
                digest = str(pose_bank["content_hash"])
                product_banks.setdefault(digest, pose_bank)
                pose_entries[pose_id] = {
                    "pose_id": pose_id,
                    "content_hash": digest,
                    "face_count": face_count,
                    "feature_count": feature_count,
                    "stable_identity": ACTOR_FEATURE_IDENTITY,
                    "address_layout_version": ACTOR_ADDRESS_LAYOUT_VERSION,
                    "canonical_address_signature": address_signature,
                    "pose_source_hash": pose_source_hash,
                    "pose_payload_hash": pose_bank["pose_payload_hash"],
                }
            products[actor_kind] = {
                "product_path": product_rel,
                "product_content_sha256": product_source_hash,
                "pose_count": len(pose_entries),
                "topology_signature": topology_signature,
                "address_layout_version": ACTOR_ADDRESS_LAYOUT_VERSION,
                "identity": ACTOR_FEATURE_IDENTITY,
                "canonical_address_signature": address_signature,
                "feature_count": int(reference_feature_count or 0),
                "poses": pose_entries,
            }
            banks.update(product_banks)
        except (OSError, json.JSONDecodeError, ActorTopologyError, KeyError, struct.error) as exc:
            rejections.append({"product": product_rel, "reason": str(exc)})
    return {
        "banks": banks,
        "manifest": {
            "schema": ACTOR_FEATURE_MANIFEST_SCHEMA,
            "identity": ACTOR_FEATURE_IDENTITY,
            "address_layout_version": ACTOR_ADDRESS_LAYOUT_VERSION,
            "p0_actor_kind": 128,
            "products": products,
            "rejections": rejections,
        },
    }
