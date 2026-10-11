"""
frame_sequence_mirror.py -- Python mirror of the JS FrameSequence model.

This class mirrors frame_sequence.js for use in pytest. It is NOT a
dataclass in schemas.py (avoids importlib complications).

IMPORTANT: Field names MUST match the JS class. A structural drift
detection test in test_frame_sequence.py enforces parity.

Tags: [FLOW:VIEWER] [DATA-CONTRACT:FRAME-SEQUENCE]
"""


class Frame:
    """A single animation frame with pixel data and position indices.

    Attributes:
        data: Base64-encoded PNG image data.
        width: Frame width in pixels.
        height: Frame height in pixels.
        angle_idx: Angle index (0-based).
        anim_idx: Animation index (0-based).
        frame_idx: Frame index within animation (0-based).
    """

    # Canonical field list for drift detection
    FIELDS = ("data", "width", "height", "angle_idx", "anim_idx", "frame_idx")

    def __init__(self, data, width, height, angle_idx=0, anim_idx=0, frame_idx=0):
        if not isinstance(data, str):
            raise TypeError("Frame data must be a base64 string")
        if not isinstance(width, int) or width <= 0:
            raise ValueError("Frame width must be a positive integer")
        if not isinstance(height, int) or height <= 0:
            raise ValueError("Frame height must be a positive integer")

        self.data = data
        self.width = width
        self.height = height
        self.angle_idx = angle_idx
        self.anim_idx = anim_idx
        self.frame_idx = frame_idx

    def __repr__(self):
        return (
            f"Frame(angle={self.angle_idx}, anim={self.anim_idx}, "
            f"frame={self.frame_idx}, {self.width}x{self.height})"
        )


class FrameSequence:
    """Unified animation model holding all frames for a sprite.

    Mirrors the JS createFrameSequence() function. Fields MUST match.

    Attributes:
        frames: List of Frame objects.
        angles: Number of angle views.
        anims: List of frame counts per animation.
        projs: Number of projections (1 or 2).
        fps: Playback FPS.
        metadata: Optional dict with additional data.
        truncated: Whether the response was truncated by payload limits.
        total_frames: Total frames available (before truncation).
        returned_frames: Frames actually returned.
    """

    # Canonical field list for drift detection against JS
    FIELDS = (
        "frames", "angles", "anims", "projs", "fps", "metadata",
        "truncated", "total_frames", "returned_frames",
    )

    def __init__(
        self,
        frames,
        angles,
        anims,
        projs=1,
        fps=8,
        metadata=None,
        truncated=False,
        total_frames=None,
        returned_frames=None,
    ):
        self.frames = list(frames)
        self.angles = angles
        self.anims = list(anims)
        self.projs = projs
        self.fps = fps
        self.metadata = metadata
        self.truncated = truncated
        self.total_frames = total_frames if total_frames is not None else len(self.frames)
        self.returned_frames = returned_frames if returned_frames is not None else len(self.frames)

        # Build lookup index
        self._index = {}
        for f in self.frames:
            key = (f.angle_idx, f.anim_idx, f.frame_idx)
            self._index[key] = f

    def get_frame(self, angle_idx, anim_idx, frame_idx):
        """Get a specific frame by its indices.

        Args:
            angle_idx: Angle index.
            anim_idx: Animation index.
            frame_idx: Frame index within animation.

        Returns:
            Frame object or None if not found.
        """
        return self._index.get((angle_idx, anim_idx, frame_idx))

    def get_frames_for_angle(self, angle_idx):
        """Get all frames for a specific angle.

        Args:
            angle_idx: Angle index.

        Returns:
            List of Frame objects for this angle.
        """
        return [f for f in self.frames if f.angle_idx == angle_idx]

    def get_frames_for_anim(self, angle_idx, anim_idx):
        """Get frames for a specific angle and animation.

        Args:
            angle_idx: Angle index.
            anim_idx: Animation index.

        Returns:
            List of Frame objects for this angle+anim combo.
        """
        return [
            f for f in self.frames
            if f.angle_idx == angle_idx and f.anim_idx == anim_idx
        ]

    def total_frame_count(self):
        """Total number of frames in this sequence.

        Returns:
            int: Number of frames.
        """
        return len(self.frames)

    @classmethod
    def from_api_response(cls, data):
        """Construct a FrameSequence from an API response dict.

        This is the canonical way to create a FrameSequence from the
        shared response shape returned by /api/viewer/load-png and
        /api/viewer/load-xp.

        Args:
            data: Dict with frames, angles, anims, projs, metadata, etc.

        Returns:
            FrameSequence instance.
        """
        frame_dicts = data.get("frames", [])
        frames = [
            Frame(
                data=fd["data"],
                width=fd["width"],
                height=fd["height"],
                angle_idx=fd.get("angle_idx", 0),
                anim_idx=fd.get("anim_idx", 0),
                frame_idx=fd.get("frame_idx", 0),
            )
            for fd in frame_dicts
        ]
        return cls(
            frames=frames,
            angles=data.get("angles", 1),
            anims=data.get("anims", [1]),
            projs=data.get("projs", 1),
            fps=data.get("fps", 8),
            metadata=data.get("metadata"),
            truncated=data.get("truncated", False),
            total_frames=data.get("total_frames"),
            returned_frames=data.get("returned_frames"),
        )

    def __repr__(self):
        return (
            f"FrameSequence(angles={self.angles}, anims={self.anims}, "
            f"projs={self.projs}, frames={len(self.frames)}, fps={self.fps})"
        )
