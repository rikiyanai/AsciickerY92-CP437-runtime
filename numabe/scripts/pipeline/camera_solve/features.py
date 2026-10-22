"""Feature extraction, cross-frame matching and track building (FL-4812).

DETERMINISM
===========
Every stage here is pinned so the same frozen frames produce the same tracks:

* SIFT with an explicit ``nfeatures`` cap and default octave parameters is
  deterministic for a given image.
* Brute-force L2 knn matching is exhaustive, so it has no RNG at all.
* Lowe ratio + a mutual (cross-check) test make the match set symmetric and
  order-independent.
* Keypoints are sorted by ``(-response, pt.x, pt.y)`` before the cap so ties do
  not depend on OpenCV's internal ordering.

RANSAC does have an RNG.  It lives in :mod:`relative`, not here, and that
module seeds ``cv2.setRNGSeed`` explicitly and records the seed in the receipt.

WHY OPENCV
==========
``cv2`` 4.13 is already importable in this repo's interpreter and in ``.venv``;
no dependency was added.  If it were absent the pieces that matter (matching,
the 8-point fundamental estimate, DLT triangulation, the bundle adjustment)
are all plain linear algebra and would be written against numpy, at the cost of
SIFT itself.  That is stated so the choice is visible rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

SIFT_N_FEATURES = 6000
LOWE_RATIO = 0.75


@dataclass
class FrameFeatures:
    """Detected keypoints for one frame, in working-image pixel coordinates."""

    capture_id: str
    xy: np.ndarray  # (N, 2) float64
    descriptors: np.ndarray  # (N, 128) float32
    responses: np.ndarray  # (N,) float32

    def __len__(self) -> int:
        return int(self.xy.shape[0])


def detect(image_path: str, capture_id: str, n_features: int = SIFT_N_FEATURES) -> FrameFeatures:
    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"cannot read image {image_path}")
    sift = cv2.SIFT_create(nfeatures=n_features)
    keypoints, descriptors = sift.detectAndCompute(image, None)
    if descriptors is None or len(keypoints) == 0:
        raise ValueError(f"no features detected in {image_path}")
    xy = np.array([k.pt for k in keypoints], dtype=np.float64)
    responses = np.array([k.response for k in keypoints], dtype=np.float32)
    # Stable total order: strongest first, ties broken by position.
    order = np.lexsort((xy[:, 1], xy[:, 0], -responses))
    return FrameFeatures(
        capture_id=capture_id,
        xy=xy[order],
        descriptors=np.ascontiguousarray(descriptors[order]),
        responses=responses[order],
    )


def match_pair(a: FrameFeatures, b: FrameFeatures, ratio: float = LOWE_RATIO) -> np.ndarray:
    """Mutual ratio-tested matches between two frames as an (M, 2) index array."""
    matcher = cv2.BFMatcher(cv2.NORM_L2)

    def one_way(src: np.ndarray, dst: np.ndarray) -> dict[int, int]:
        knn = matcher.knnMatch(src, dst, k=2)
        out: dict[int, int] = {}
        for pair in knn:
            if len(pair) < 2:
                continue
            first, second = pair
            if first.distance < ratio * second.distance:
                out[first.queryIdx] = first.trainIdx
        return out

    forward = one_way(a.descriptors, b.descriptors)
    backward = one_way(b.descriptors, a.descriptors)
    pairs = [(i, j) for i, j in forward.items() if backward.get(j) == i]
    pairs.sort()
    return np.array(pairs, dtype=np.int64).reshape(-1, 2)


class TrackBuilder:
    """Union-find over ``(frame index, keypoint index)`` observations.

    A track is one physical world point seen in several frames.  Chaining
    pairwise matches transitively is what turns two-view geometry into a
    multi-view reconstruction, and it is also where a wrong match poisons a
    whole track, so tracks that see the same frame twice are dropped rather
    than arbitrated: an inconsistent track is evidence of a bad match, not a
    point to average.
    """

    def __init__(self) -> None:
        self._parent: dict[tuple[int, int], tuple[int, int]] = {}

    def _find(self, node: tuple[int, int]) -> tuple[int, int]:
        root = self._parent.setdefault(node, node)
        while root != self._parent[root]:
            root = self._parent[root]
        while self._parent[node] != root:  # path compression
            self._parent[node], node = root, self._parent[node]
        return root

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            # Deterministic merge direction: smaller key wins.
            lo, hi = (ra, rb) if ra < rb else (rb, ra)
            self._parent[hi] = lo

    def tracks(self, min_length: int = 2) -> list[list[tuple[int, int]]]:
        groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for node in self._parent:
            groups.setdefault(self._find(node), []).append(node)
        out: list[list[tuple[int, int]]] = []
        for members in groups.values():
            members.sort()
            frames = [f for f, _ in members]
            if len(set(frames)) != len(frames):
                continue  # same frame twice: inconsistent, drop the track
            if len(members) >= min_length:
                out.append(members)
        out.sort()
        return out


@dataclass
class MatchGraph:
    """All pairwise geometry-verified matches plus the tracks chained from them."""

    features: list[FrameFeatures]
    pair_matches: dict[tuple[int, int], np.ndarray] = field(default_factory=dict)
    tracks: list[list[tuple[int, int]]] = field(default_factory=list)

    def observation_xy(self, frame: int, kp: int) -> np.ndarray:
        return self.features[frame].xy[kp]
