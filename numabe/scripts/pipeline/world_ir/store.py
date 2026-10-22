"""Y92 World IR store - deterministic load/save and record insertion.

FL-4811.  The store keeps records as plain dicts.  There is no object model
that could drift from the serialized form, so ``load -> save -> load -> save``
is byte-identical by construction, and the validator always sees exactly what
is on disk.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from . import schema as S
from .validate import Finding, WorldIRValidationError, validate_document


class WorldIRStore:
    """A mutable World IR document with canonical serialization."""

    def __init__(self, doc: Mapping[str, Any] | None = None, *, ir_id: str = "world_ir") -> None:
        self._doc: dict[str, Any] = (
            dict(doc) if doc is not None else S.document(ir_id=ir_id)
        )
        self._doc.setdefault("coordinate_frames", {})
        self._doc.setdefault("source_observations", [])
        self._doc.setdefault("canonical_features", [])
        self._doc.setdefault("generated_representations", [])

    # -- construction ------------------------------------------------------

    @classmethod
    def new(cls, ir_id: str, generated_at: str | None = None, notes: str | None = None):
        return cls(S.document(ir_id=ir_id, generated_at=generated_at, notes=notes))

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]):
        return cls(doc)

    @classmethod
    def loads(cls, text: str):
        return cls(S.loads(text))

    @classmethod
    def load(cls, path: str | os.PathLike[str]):
        return cls.loads(Path(path).read_text(encoding="utf-8"))

    # -- content -----------------------------------------------------------

    @property
    def schema_version(self) -> str:
        return self._doc.get("schema_version", "")

    @property
    def frames(self) -> dict[str, Any]:
        return self._doc["coordinate_frames"]

    @property
    def observations(self) -> list[dict]:
        return self._doc["source_observations"]

    @property
    def features(self) -> list[dict]:
        return self._doc["canonical_features"]

    @property
    def representations(self) -> list[dict]:
        return self._doc["generated_representations"]

    def add_frame(self, frame: Mapping[str, Any]) -> dict:
        frame = dict(frame)
        self._doc["coordinate_frames"][frame["frame_id"]] = frame
        return frame

    def add_observation(self, record: Mapping[str, Any]) -> dict:
        return self._append("source_observations", record)

    def add_feature(self, record: Mapping[str, Any]) -> dict:
        return self._append("canonical_features", record)

    def add_representation(self, record: Mapping[str, Any]) -> dict:
        return self._append("generated_representations", record)

    def extend_observations(self, records: Iterable[Mapping[str, Any]]) -> None:
        for record in records:
            self.add_observation(record)

    def extend_features(self, records: Iterable[Mapping[str, Any]]) -> None:
        for record in records:
            self.add_feature(record)

    def _append(self, collection: str, record: Mapping[str, Any]) -> dict:
        """Append a record, refusing one whose record_kind does not belong.

        This is the insertion-time half of the three-kind separation: a
        GeneratedRepresentation cannot even reach ``canonical_features``.
        """
        expected = S.COLLECTION_RECORD_KIND[collection]
        kind = record.get("record_kind")
        if kind != expected:
            raise WorldIRValidationError(
                [
                    Finding(
                        "E_WRONG_RECORD_KIND",
                        f"$.{collection}",
                        f"collection {collection!r} holds {expected!r} records, got {kind!r}",
                    )
                ]
            )
        item = dict(record)
        self._doc[collection].append(item)
        return item

    # -- lookup ------------------------------------------------------------

    def feature(self, feature_id: str) -> dict | None:
        for rec in self.features:
            if rec.get("feature_id") == feature_id:
                return rec
        return None

    def observation(self, observation_id: str) -> dict | None:
        for rec in self.observations:
            if rec.get("observation_id") == observation_id:
                return rec
        return None

    def features_of_class(self, semantic_class: str) -> Iterator[dict]:
        for rec in self.features:
            if rec.get("semantic_class") == semantic_class:
                yield rec

    def representations_for(self, feature_id: str) -> Iterator[dict]:
        for rec in self.representations:
            if rec.get("feature_id") == feature_id:
                yield rec

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict:
        return self._doc

    def dumps(self) -> str:
        return S.canonical_dumps(self._doc)

    def dump_bytes(self) -> bytes:
        return S.canonical_bytes(self._doc)

    def save(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.dump_bytes())
        return target

    # -- validation --------------------------------------------------------

    def validate(self) -> list[Finding]:
        return validate_document(self._doc)

    def assert_valid(self) -> None:
        findings = self.validate()
        if findings:
            raise WorldIRValidationError(findings)

    def __len__(self) -> int:
        return len(self.observations) + len(self.features) + len(self.representations)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"WorldIRStore(ir_id={self._doc.get('ir_id')!r}, "
            f"observations={len(self.observations)}, features={len(self.features)}, "
            f"representations={len(self.representations)})"
        )
