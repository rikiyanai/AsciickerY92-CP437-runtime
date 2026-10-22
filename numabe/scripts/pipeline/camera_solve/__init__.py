"""Observation-to-world geometric solve (FL-4812).

Turns a sequence of street-level pictures plus a vector map into georegistered
camera poses, an OSM-corroborated facade anchor and triangulated geometry the
map does not contain - each carrying its own residuals, covariance and gate
decision.

The package is deliberately source-agnostic.  It knows about cameras, priors
and residuals; it does not know about Panoramax, Overpass, any particular
corridor, or the World IR.  The per-source ingest owner
``scripts/pipeline/world_ir_ingest_cameras.py`` maps its output onto world
truth, exactly as ``world_ir_ingest_carto.py`` does for carto runs.
"""

from .capture import (
    Capture,
    IntrinsicsPrior,
    capture_from_item,
    capture_from_source_record,
    intrinsics_from_item,
)
from .geodesy import EnuFrame
from .georegister import GeoSolution, global_solve, initial_alignment
from .gravity import GravityEstimate, detect_vertical_lines, estimate_from_solution, level_solution
from .solve import CorridorInputs, run

__all__ = [
    "Capture",
    "CorridorInputs",
    "EnuFrame",
    "GeoSolution",
    "GravityEstimate",
    "IntrinsicsPrior",
    "capture_from_item",
    "capture_from_source_record",
    "global_solve",
    "initial_alignment",
    "detect_vertical_lines",
    "estimate_from_solution",
    "intrinsics_from_item",
    "level_solution",
    "run",
]
