from enum import Enum


class BranchStage(str, Enum):
    """Pipeline stages represented in the branch graph."""

    UPLOAD = "upload"
    EXTRACT = "extract"
    GENERATE = "generate"
    SLICE = "slice"
    PROCESS = "process"
    ASSEMBLE = "assemble"


class BranchStatus(str, Enum):
    """Status values for branch graph nodes."""

    ACTIVE = "active"
    PRUNED = "pruned"
    PROMOTED = "promoted"
