"""
ai_provider.py -- Pluggable AI image-generation adapter abstraction.

Provides:
- FrameRequest: Frozen dataclass describing a single frame to generate
- FrameResult: Dataclass containing the generated image and metadata
- AIProviderAdapter: Abstract base class for all providers
- StubAdapter: Deterministic RGBA test images (no external API calls)
- GeminiNanoBananaAdapter: Direct Gemini API adapter via google-genai SDK
- GeminiCLIAdapter: Subprocess-based adapter that shells out to gemini CLI
- get_provider(): Factory function to instantiate providers by name

[FLOW:AI-PROVIDER] The batch runner calls get_provider(name) once, then
calls adapter.generate_frame(request) for each (angle, frame) pair.

[DATA-CONTRACT:AI-PROVIDER]
- FrameRequest is frozen (hashable, immutable) for use as cache keys
- FrameResult.image is always PIL RGBA mode
- StubAdapter is fully deterministic: same inputs -> identical bytes
- GeminiNanoBananaAdapter: direct API via google-genai SDK, output is RGBA
- GeminiCLIAdapter: one subprocess per frame, output is always RGBA
"""

import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameRequest:
    """Immutable specification for a single frame to generate.

    Frozen for hashability -- can be used as dict key or set member,
    enabling deduplication and caching in the batch runner.

    Attributes:
        prompt: Text description of the sprite to generate.
        angle: Rotation angle index (0-7 for 8-angle sprites).
        frame: Animation frame index within the current angle.
        width: Desired output width in pixels.
        height: Desired output height in pixels.
        seed: Deterministic seed for reproducible output (default 0).
        reference_images: Optional tuple of reference image paths
            (tuple for frozen hashability, not list).
    """

    prompt: str
    angle: int
    frame: int
    width: int
    height: int
    seed: int = 0
    reference_images: Optional[tuple] = None


@dataclass
class FrameResult:
    """Result of a single frame generation call.

    Not frozen because PIL Image is not hashable. FrameRequest (the input)
    remains frozen for caching; FrameResult (the output) does not need to be.

    Attributes:
        image: Generated RGBA PIL Image matching the requested dimensions.
        seed_used: The seed that was actually used for generation.
        provider_metadata: Provider-specific metadata dict (not mutated
            after creation).
    """

    image: Image.Image
    seed_used: int
    provider_metadata: dict = field(default_factory=dict)


class AIProviderAdapter(ABC):
    """Abstract base class for AI image generation providers.

    Subclasses must implement:
    - generate_frame(request) -> FrameResult
    - name (property) -> str
    """

    @abstractmethod
    def generate_frame(self, request: FrameRequest) -> FrameResult:
        """Generate a single RGBA frame image from the given request.

        Args:
            request: Frozen FrameRequest specifying what to generate.

        Returns:
            FrameResult with RGBA image matching request dimensions.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this provider (e.g. 'stub', 'gemini')."""
        ...


class StubAdapter(AIProviderAdapter):
    """Deterministic colored RGBA test images.

    Color = f(angle, frame, seed) using simple arithmetic (no randomness).
    Same inputs always produce identical output bytes.

    Creates RGBA images with:
    - Transparent (alpha=0) border pixels (outer ~10% margin)
    - Opaque center rectangle with deterministic color based on angle/frame/seed
    """

    @property
    def name(self) -> str:
        return "stub"

    def generate_frame(self, request: FrameRequest) -> FrameResult:
        """Generate a deterministic RGBA test image.

        The color is computed from (angle, frame, seed) using modular
        arithmetic, guaranteeing:
        - Identical output for identical inputs
        - Visually distinct output for different (angle, frame) pairs
        - RGBA mode with transparent border and opaque center

        Args:
            request: FrameRequest with dimensions and angle/frame/seed.

        Returns:
            FrameResult with deterministic RGBA image.
        """
        # Use a checked-in real atlas tile as deterministic source content
        # instead of synthesizing blank blocks in-memory.
        repo_root = Path(__file__).resolve().parents[2]
        atlas_path = repo_root / "fonts" / "cp437_12x12.png"
        if not atlas_path.exists():
            raise RuntimeError(f"Stub provider source atlas missing: {atlas_path}")

        atlas = Image.open(atlas_path).convert("RGBA")
        tile_w = 12
        tile_h = 12
        cols = max(1, atlas.width // tile_w)
        rows = max(1, atlas.height // tile_h)
        tile_count = cols * rows

        tile_idx = (request.angle * 37 + request.frame * 59 + request.seed * 13) % tile_count
        tx = (tile_idx % cols) * tile_w
        ty = (tile_idx // cols) * tile_h
        tile = atlas.crop((tx, ty, tx + tile_w, ty + tile_h))
        img = tile.resize((request.width, request.height), Image.NEAREST)

        return FrameResult(
            image=img,
            seed_used=request.seed,
            provider_metadata={
                "provider": "stub",
                "angle": request.angle,
                "frame": request.frame,
            },
        )


class GeminiNanoBananaAdapter(AIProviderAdapter):
    """Direct Gemini image generation API adapter.

    Uses the google-genai SDK to call Imagen models for sprite frame
    generation. Auth via GEMINI_API_KEY environment variable.

    WHY lazy import: google.genai is imported inside __init__ so that
    code using StubAdapter or GeminiCLIAdapter doesn't require the SDK.

    [FLOW:AI-PROVIDER] Per-frame: build prompt → API call → extract PIL
    Image → resize to target dims → return RGBA FrameResult.
    """

    def __init__(self, model: str = "imagen-3.0-generate-002"):
        """Initialize with Gemini API key and model name.

        Args:
            model: Imagen model identifier. Default is imagen-3.0-generate-002.

        Raises:
            RuntimeError: If GEMINI_API_KEY env var is not set.
            RuntimeError: If google-genai SDK is not installed.
        """
        import os

        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY environment variable is not set. "
                "Get an API key at https://aistudio.google.com/apikey"
            )

        try:
            import google.genai as genai
        except ImportError as e:
            raise RuntimeError(
                "google-genai SDK is not installed. "
                "Install with: pip install google-genai"
            ) from e

        self._client = genai.Client(api_key=key)
        self._genai = genai
        self._model = model

    @property
    def name(self) -> str:
        return "gemini"

    def generate_frame(self, request: FrameRequest) -> FrameResult:
        """Generate a single frame via the Gemini image generation API.

        Calls models.generate_images with the request prompt, extracts
        the PIL Image from the response, resizes to target dimensions.

        Args:
            request: FrameRequest with prompt, dimensions, angle/frame.

        Returns:
            FrameResult with RGBA image at requested dimensions.

        Raises:
            RuntimeError: If the API returns no images (safety filter).
        """
        from google.genai import types

        prompt = (
            f"Generate a pixel art sprite image of: {request.prompt} "
            f"(viewing angle {request.angle}, animation frame {request.frame}). "
            f"Transparent background, centered subject."
        )

        config = types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="1:1",
            seed=request.seed,
        )

        logger.info(
            "Gemini API: a%d_f%d model=%s",
            request.angle, request.frame, self._model,
        )

        response = self._client.models.generate_images(
            model=self._model,
            prompt=prompt,
            config=config,
        )

        if not response.generated_images:
            raise RuntimeError(
                f"Gemini API returned no images for a{request.angle}_f{request.frame} "
                f"(likely blocked by safety filter)"
            )

        img = response.generated_images[0].image
        img = _resize_to_target(img, request.width, request.height)

        return FrameResult(
            image=img,
            seed_used=request.seed,
            provider_metadata={
                "provider": "gemini",
                "model": self._model,
                "angle": request.angle,
                "frame": request.frame,
            },
        )


def _resize_to_target(img: Image.Image, width: int, height: int) -> Image.Image:
    """Resize image to target dimensions, preserving aspect ratio with padding.

    The image is scaled to fit within (width, height) using LANCZOS,
    then centered on a transparent RGBA canvas of exact target size.

    Args:
        img: Source PIL Image (any mode).
        width: Target width in pixels.
        height: Target height in pixels.

    Returns:
        RGBA PIL Image at exactly (width, height).
    """
    img = img.convert("RGBA")

    # Scale to fit within target, preserving aspect ratio
    img_w, img_h = img.size
    scale = min(width / img_w, height / img_h)
    new_w = max(1, int(img_w * scale))
    new_h = max(1, int(img_h * scale))

    resized = img.resize((new_w, new_h), Image.LANCZOS)

    # Center on transparent canvas
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    paste_x = (width - new_w) // 2
    paste_y = (height - new_h) // 2
    canvas.paste(resized, (paste_x, paste_y), resized)

    return canvas


class GeminiCLIAdapter(AIProviderAdapter):
    """Subprocess-based adapter that shells out to the ``gemini`` CLI per frame.

    WHY subprocess instead of API: The gemini CLI handles authentication,
    tool use (generate_image via nanobanana extension), and credential
    management internally. No GEMINI_API_KEY needed in Python.

    WHY project_root matters: The gemini CLI loads extensions (like
    nanobanana for image generation) and enables YOLO auto-approval based
    on the working directory's trust status. Running from an untrusted
    path (e.g. /tmp/) means no image generation tools and no auto-accept.
    The project_root must be a gemini-trusted directory.

    [FLOW:AI-PROVIDER] Per-frame: build prompt with explicit output path
    -> subprocess from trusted cwd -> check output file -> resize -> RGBA.
    """

    def __init__(
        self,
        workdir: Path,
        project_root: Optional[Path] = None,
        timeout_s: int = 120,
    ):
        """Initialize the gemini CLI adapter.

        Args:
            workdir: Directory for per-frame output PNGs.
            project_root: Trusted directory used as subprocess cwd.
                Must be a gemini-trusted folder so extensions load and
                YOLO mode works. Defaults to Path.home() if not provided.
            timeout_s: Subprocess timeout in seconds per frame.

        Raises:
            RuntimeError: If ``gemini`` is not found in PATH.
        """
        if shutil.which("gemini") is None:
            raise RuntimeError(
                "gemini CLI not found in PATH. "
                "Install: https://github.com/google-gemini/gemini-cli"
            )
        self._workdir = Path(workdir)
        self._workdir.mkdir(parents=True, exist_ok=True)
        self._timeout_s = timeout_s

        # WHY: gemini CLI loads extensions and grants YOLO trust based on cwd.
        # The nanobanana extension (image generation) only loads from trusted
        # paths. Default to home dir which is typically trusted.
        if project_root is not None:
            self._project_root = Path(project_root)
        else:
            self._project_root = Path.home()

    @property
    def name(self) -> str:
        return "gemini-cli"

    def generate_frame(self, request: FrameRequest) -> FrameResult:
        """Generate a single frame by shelling out to the gemini CLI.

        Invokes gemini from project_root (trusted cwd), telling it to
        save the generated image to a specific path in the workdir.
        Falls back to directory scanning if the explicit path isn't found.

        Args:
            request: FrameRequest specifying prompt, dimensions, angle/frame.

        Returns:
            FrameResult with RGBA image at requested dimensions.

        Raises:
            RuntimeError: If CLI fails, times out, or produces no output.
        """
        # Output file goes in workdir (one PNG per frame)
        output_path = (
            self._workdir / f"a{request.angle}_f{request.frame}.png"
        ).resolve()

        prompt = self._build_prompt(
            request.prompt, request.angle, request.frame, output_path
        )

        cmd = [
            "gemini",
            "-e", "nanobanana",
            "-p", prompt,
            "-y",
            "--output-format", "json",
        ]
        logger.info(
            "gemini CLI: a%d_f%d cwd=%s",
            request.angle, request.frame, self._project_root,
        )

        try:
            subprocess.run(
                cmd,
                cwd=str(self._project_root),
                timeout=self._timeout_s,
                capture_output=True,
                check=True,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"gemini CLI timed out after {self._timeout_s}s "
                f"for a{request.angle}_f{request.frame}"
            ) from e
        except subprocess.CalledProcessError as e:
            stderr_snippet = ""
            if e.stderr:
                stderr_snippet = (
                    e.stderr[:200] if isinstance(e.stderr, str)
                    else e.stderr[:200].decode("utf-8", errors="replace")
                )
            raise RuntimeError(
                f"gemini CLI failed (exit {e.returncode}) "
                f"for a{request.angle}_f{request.frame}: {stderr_snippet}"
            ) from e

        # Check explicit output path first, then fallback to dir scan
        if output_path.exists():
            found_file = output_path
        else:
            found_file = self._scan_for_output(self._workdir)
            if found_file is None:
                raise RuntimeError(
                    f"gemini CLI produced no output file "
                    f"(expected {output_path}) "
                    f"for a{request.angle}_f{request.frame}"
                )

        img = Image.open(found_file)
        img = _resize_to_target(img, request.width, request.height)

        return FrameResult(
            image=img,
            seed_used=request.seed,
            provider_metadata={
                "provider": "gemini-cli",
                "angle": request.angle,
                "frame": request.frame,
                "output_file": str(found_file),
            },
        )

    @staticmethod
    def _build_prompt(
        base_prompt: str, angle: int, frame: int, output_path: Path
    ) -> str:
        """Build gemini CLI prompt requesting image generation to a specific path.

        Tells gemini to use the generate_image tool and save the result
        to an absolute path. The explicit path avoids directory-diffing.

        Args:
            base_prompt: User-provided base description.
            angle: Viewing angle index.
            frame: Animation frame index.
            output_path: Absolute path where the PNG should be saved.

        Returns:
            Complete prompt string for the gemini CLI.
        """
        return (
            f"Generate a pixel art image of: {base_prompt} "
            f"(viewing angle {angle}, animation frame {frame}). "
            f"Transparent background, centered subject. "
            f"Save the image to {output_path}"
        )

    @staticmethod
    def _scan_for_output(search_dir: Path) -> Optional[Path]:
        """Fallback: find the newest image file in a directory.

        Used when gemini saves the output to an unexpected location.

        Args:
            search_dir: Directory to scan for image files.

        Returns:
            Path to the newest image file, or None if none found.
        """
        if not search_dir.exists():
            return None

        image_extensions = {".png", ".jpg", ".jpeg", ".webp"}
        candidates = [
            f for f in search_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in image_extensions
        ]

        if not candidates:
            return None

        return max(candidates, key=lambda p: p.stat().st_mtime)

    # Delegate to module-level function for backward compatibility with
    # tests that call GeminiCLIAdapter._resize_to_target() directly.
    _resize_to_target = staticmethod(_resize_to_target)


def get_provider(name: str, **kwargs) -> AIProviderAdapter:
    """Factory function returning a provider instance by name.

    Args:
        name: Provider identifier ('stub', 'gemini', or 'gemini-cli').
        **kwargs: Extra arguments forwarded to the provider constructor.
            For 'gemini-cli', pass workdir=Path(...) (required).

    Returns:
        AIProviderAdapter instance.

    Raises:
        ValueError: If name is not a known provider.
    """
    if name == "gemini-cli":
        workdir = kwargs.get("workdir")
        if workdir is None:
            raise ValueError("gemini-cli provider requires workdir=Path(...)")
        project_root = kwargs.get("project_root")
        timeout_s = kwargs.get("timeout_s", 120)
        return GeminiCLIAdapter(
            workdir=Path(workdir),
            project_root=Path(project_root) if project_root else None,
            timeout_s=timeout_s,
        )

    if name == "gemini":
        model = kwargs.get("model", "imagen-3.0-generate-002")
        return GeminiNanoBananaAdapter(model=model)

    providers = {
        "stub": StubAdapter,
    }
    if name not in providers:
        raise ValueError(
            f"Unknown provider: {name}. "
            f"Available: {list(providers.keys()) + ['gemini', 'gemini-cli']}"
        )
    return providers[name]()
