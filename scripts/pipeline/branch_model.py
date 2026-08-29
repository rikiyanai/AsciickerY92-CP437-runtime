from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from scripts.pipeline.branch_enums import BranchStage, BranchStatus


ALLOWED_ARTIFACT_KINDS = {"image", "sprite_list", "xp_file", "grid_data"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return _utc_now()


@dataclass
class BranchNode:
    """A single candidate branch artifact in the multi-track pipeline."""

    id: str
    parent_id: Optional[str]
    stage: BranchStage
    track_id: str
    settings_snapshot: dict[str, Any]
    artifact_kind: str
    artifact_path: str
    artifact_refs: list[str] = field(default_factory=list)
    child_count: int = 0
    quality_score: float = 0.0
    source_cell_px: Optional[int] = None
    target_cell_px: int = 12
    status: BranchStatus = BranchStatus.ACTIVE
    thumbnail_path: Optional[str] = None
    score_type: str = "heuristic"  # "confidence", "heuristic", or "user-rated"
    process_mode: Optional[str] = None  # explicit processor mode for this branch

    def __post_init__(self) -> None:
        self.child_count = len(self.artifact_refs)
        self.validate()

    def validate(self) -> None:
        if not self.id:
            raise ValueError("BranchNode.id must be non-empty")
        if not self.track_id:
            raise ValueError("BranchNode.track_id must be non-empty")
        if self.artifact_kind not in ALLOWED_ARTIFACT_KINDS:
            raise ValueError(
                f"artifact_kind must be one of {sorted(ALLOWED_ARTIFACT_KINDS)}, got '{self.artifact_kind}'"
            )
        if not self.artifact_path:
            raise ValueError("artifact_path must be non-empty")
        if not (0.0 <= float(self.quality_score) <= 1.0):
            raise ValueError("quality_score must be within [0.0, 1.0]")
        if self.source_cell_px is not None and self.source_cell_px <= 0:
            raise ValueError("source_cell_px must be > 0 when provided")
        if self.target_cell_px <= 0:
            raise ValueError("target_cell_px must be > 0")
        if self.score_type not in {"confidence", "heuristic", "measured", "user-rated"}:
            raise ValueError(
                f"score_type must be one of confidence/heuristic/measured/user-rated, got '{self.score_type}'"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "stage": self.stage.value,
            "track_id": self.track_id,
            "settings_snapshot": self.settings_snapshot,
            "artifact_kind": self.artifact_kind,
            "artifact_path": self.artifact_path,
            "artifact_refs": list(self.artifact_refs),
            "child_count": len(self.artifact_refs),
            "quality_score": float(self.quality_score),
            "source_cell_px": self.source_cell_px,
            "target_cell_px": int(self.target_cell_px),
            "status": self.status.value,
            "thumbnail_path": self.thumbnail_path,
            "score_type": self.score_type,
            "process_mode": self.process_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BranchNode":
        return cls(
            id=str(data["id"]),
            parent_id=data.get("parent_id"),
            stage=BranchStage(data.get("stage", BranchStage.EXTRACT.value)),
            track_id=str(data.get("track_id", "unknown")),
            settings_snapshot=dict(data.get("settings_snapshot", {})),
            artifact_kind=str(data.get("artifact_kind", "image")),
            artifact_path=str(data.get("artifact_path", "")),
            artifact_refs=[str(v) for v in data.get("artifact_refs", [])],
            child_count=int(data.get("child_count", 0)),
            quality_score=float(data.get("quality_score", 0.0)),
            source_cell_px=(
                int(data["source_cell_px"])
                if data.get("source_cell_px") is not None
                else None
            ),
            target_cell_px=int(data.get("target_cell_px", 12)),
            status=BranchStatus(data.get("status", BranchStatus.ACTIVE.value)),
            thumbnail_path=data.get("thumbnail_path"),
            score_type=str(data.get("score_type", "heuristic")),
            process_mode=data.get("process_mode"),
        )


@dataclass
class BranchTree:
    """Tree container for all branch candidates in a single job."""

    nodes: dict[str, BranchNode] = field(default_factory=dict)
    root_id: Optional[str] = None
    pruned_warnings: list[str] = field(default_factory=list)

    def add_node(self, node: BranchNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Branch node already exists: {node.id}")
        if node.parent_id is not None and node.parent_id not in self.nodes:
            raise ValueError(f"Parent branch does not exist: {node.parent_id}")
        if node.parent_id is None:
            if self.root_id is None:
                self.root_id = node.id
            elif self.root_id != node.id:
                raise ValueError(
                    f"Branch tree already has root_id={self.root_id}; "
                    f"cannot add second root node {node.id}"
                )
        self.nodes[node.id] = node

    def prune(self, node_id: str) -> None:
        node = self.nodes[node_id]
        if node.status != BranchStatus.PROMOTED:
            node.status = BranchStatus.PRUNED

    def promote(self, node_id: str) -> None:
        for existing in self.nodes.values():
            if existing.status == BranchStatus.PROMOTED:
                existing.status = BranchStatus.ACTIVE
        node = self.nodes[node_id]
        node.status = BranchStatus.PROMOTED

    def get_active(self) -> list[BranchNode]:
        return [
            node
            for node in self.nodes.values()
            if node.status in {BranchStatus.ACTIVE, BranchStatus.PROMOTED}
        ]

    @staticmethod
    def _track_rank(track_id: str, tie_break_order: Optional[list[str]]) -> int:
        if not tie_break_order:
            return 10_000
        try:
            return tie_break_order.index(track_id)
        except ValueError:
            return len(tie_break_order) + 1

    def deterministic_tiebreak_key(
        self, node: BranchNode, tie_break_order: Optional[list[str]] = None
    ) -> tuple[float, int, str]:
        return (
            -float(node.quality_score),
            self._track_rank(node.track_id, tie_break_order),
            node.id,
        )

    def enforce_caps(
        self,
        per_stage: int = 4,
        global_max: int = 8,
        tie_break_order: Optional[list[str]] = None,
    ) -> list[str]:
        if per_stage <= 0:
            raise ValueError("per_stage must be > 0")
        if global_max <= 0:
            raise ValueError("global_max must be > 0")

        pruned_ids: list[str] = []

        # Stage-level cap first.
        by_stage: dict[BranchStage, list[BranchNode]] = {}
        for node in self.nodes.values():
            if node.status != BranchStatus.ACTIVE:
                continue
            by_stage.setdefault(node.stage, []).append(node)

        for stage_nodes in by_stage.values():
            ranked = sorted(
                stage_nodes,
                key=lambda n: self.deterministic_tiebreak_key(n, tie_break_order),
            )
            for node in ranked[per_stage:]:
                node.status = BranchStatus.PRUNED
                pruned_ids.append(node.id)

        # Global cap on active + promoted branches.
        promoted = [n for n in self.nodes.values() if n.status == BranchStatus.PROMOTED]
        active = [n for n in self.nodes.values() if n.status == BranchStatus.ACTIVE]

        allowed_active = max(global_max - len(promoted), 0)
        if len(active) > allowed_active:
            ranked_active = sorted(
                active,
                key=lambda n: self.deterministic_tiebreak_key(n, tie_break_order),
            )
            for node in ranked_active[allowed_active:]:
                node.status = BranchStatus.PRUNED
                if node.id not in pruned_ids:
                    pruned_ids.append(node.id)

        return pruned_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "nodes": [
                self.nodes[node_id].to_dict() for node_id in sorted(self.nodes.keys())
            ],
            "pruned_warnings": list(self.pruned_warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BranchTree":
        tree = cls(root_id=data.get("root_id"))
        raw_nodes = data.get("nodes", [])

        for raw in raw_nodes:
            node = BranchNode.from_dict(raw)
            tree.nodes[node.id] = node

        if tree.root_id is None:
            for node in tree.nodes.values():
                if node.parent_id is None:
                    tree.root_id = node.id
                    break

        tree.pruned_warnings = list(data.get("pruned_warnings", []))
        return tree


@dataclass
class JobManifest:
    """Persisted branch graph metadata for one pipeline job."""

    job_id: str
    input_path: str
    branch_tree: BranchTree
    created_at: datetime = field(default_factory=_utc_now)
    version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "input_path": self.input_path,
            "branch_tree": self.branch_tree.to_dict(),
            "created_at": self.created_at.isoformat(),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobManifest":
        return cls(
            job_id=str(data["job_id"]),
            input_path=str(data["input_path"]),
            branch_tree=BranchTree.from_dict(data.get("branch_tree", {})),
            created_at=_parse_datetime(data.get("created_at")),
            version=str(data.get("version", "1.0")),
        )

    def save_json(self, path: str | Path) -> None:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(self.to_dict(), indent=2, allow_nan=False), encoding="utf-8")

    @classmethod
    def load_json(cls, path: str | Path) -> "JobManifest":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)
