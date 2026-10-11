"""
prompt_pack.py -- Define and serialize AI generation prompt packs.

ARCHITECTURE:
    A prompt pack is a JSON file that describes the AI generation parameters
    for a set of sprite frames. It serves as the contract between the prompt
    engineer and the batch runner: the pack says what to generate and how,
    the batch runner consumes it to drive AI image generation.

KEY EXPORTS:
    - FrameOverride: Frozen dataclass for per-frame prompt customization
    - PromptPack: Frozen dataclass describing all AI generation parameters
    - load_prompt_pack: Deserialize a prompt pack from JSON file
    - write_prompt_pack: Serialize a prompt pack to JSON file

PIPELINE CONTEXT:
    [FLOW:MANIFEST] Created by prompt engineer, consumed by batch runner (Task 3.1).
    See docs/research/ascii/verification/archive/MULTIPLAYER_DOCS_ARCHIVE.md for the archived specification.
"""

from dataclasses import dataclass, asdict
from pathlib import Path
import json


@dataclass(frozen=True)
class FrameOverride:
    """Per-frame prompt customization for specific angle/frame combinations.

    Allows overriding or augmenting the base prompt for individual frames,
    e.g. adding "breathing fire" to a specific attack frame.

    Attributes:
        angle: The viewing angle index this override applies to.
        frame: The frame index this override applies to.
        prompt_suffix: Text appended to the base prompt for this frame.
        negative_prompt_suffix: Text appended to the negative prompt.
        seed_offset: Added to default_seed for this frame's generation.
    """

    angle: int
    frame: int
    prompt_suffix: str = ""
    negative_prompt_suffix: str = ""
    seed_offset: int = 0


@dataclass(frozen=True)
class PromptPack:
    """Frozen description of AI generation parameters for a sprite asset.

    All fields are immutable after creation to prevent accidental mutation
    during batch processing.

    Attributes:
        name: Asset identifier (used for output filenames).
        base_prompt: Core prompt text sent to the AI model.
        style_prompt: Style description appended to every frame prompt.
        negative_prompt: Global negative prompt for all frames.
        frame_overrides: Tuple of per-frame customizations (tuple for hashability).
        reference_policy: How to use reference images:
            "none" -- no reference images
            "master_only" -- use only the master/first frame as reference
            "master_plus_prev" -- use master + previous frame as reference
        default_seed: Base seed for reproducible generation.
    """

    name: str
    base_prompt: str
    style_prompt: str = ""
    negative_prompt: str = ""
    frame_overrides: tuple = ()
    reference_policy: str = "none"
    default_seed: int = 0


def load_prompt_pack(path: Path) -> PromptPack:
    """Load a prompt pack from a JSON file.

    Deserializes frame_overrides dicts into FrameOverride objects and wraps
    them in a tuple for frozen dataclass compatibility.

    Args:
        path: Path to the prompt pack JSON file.

    Returns:
        PromptPack instance with all fields populated.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    with open(path) as f:
        data = json.load(f)

    raw_overrides = data.pop("frame_overrides", [])
    overrides = tuple(
        FrameOverride(**override_dict) for override_dict in raw_overrides
    )

    return PromptPack(frame_overrides=overrides, **data)


def write_prompt_pack(pack: PromptPack, output_path: Path) -> None:
    """Serialize a prompt pack to a JSON file.

    Converts FrameOverride objects to dicts via dataclasses.asdict().
    Creates parent directories if they do not exist.

    Args:
        pack: The prompt pack to write.
        output_path: Destination file path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(pack)
    # asdict converts tuple of FrameOverride to list of dicts automatically
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
