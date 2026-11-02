from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Params:
    """All knobs for one pipeline run. Stored verbatim in the manifest."""

    # preprocess
    mode: str = "auto"              # auto | lineart | color
    upscale: float = 1.0            # scale factor applied before thresholding (e.g. 3.0)
    upscale_style: str = "smooth"   # smooth (cubic, soft edges) | pixel (nearest, stair-stepped, crunchy)
    min_side: int = 768             # upscale images whose longer side is below this
    max_side: int = 4096            # cap on the working size (after upscale)
    denoise: bool = True
    normalize_background: bool = True   # flatten uneven paper tone (line art only)
    threshold: str = "otsu"         # otsu | adaptive (line art only)
    level: int = 0                  # manual threshold 1-254 like Photoshop; 0 = automatic
    ink: str = "#000000"            # ink colour for line art, or "auto" to sample it
    colors: int = 8                 # palette size for colour mode

    # trace (builtin backend)
    backend: str = "builtin"        # builtin | vtracer
    speckle: int = 6                # drop blobs and fill holes smaller than this many px
    epsilon: float = 0.75           # polyline simplification tolerance in px
    corner_deg: float = 125.0       # vertices sharper than this stay corners
    smooth_sigma: float = 0.6       # blur before contouring (sub-pixel smoothing)

    # simplify
    precision: int = 2              # decimal places kept in path data
    merge_tol: int = 12             # merge fills closer than this (RGB distance)

    # quality gate (not part of the cache key)
    min_score: float = 0.90

    def to_dict(self) -> dict:
        return asdict(self)

    def key(self) -> str:
        """Stable hash of everything that changes the output SVG."""
        d = asdict(self)
        d.pop("min_score", None)
        return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:10]
