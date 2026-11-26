from __future__ import annotations

from dataclasses import dataclass

from .config import Params
from .preprocess import prepare
from .score import score_or_none
from .simplify import postprocess, svg_stats
from .trace import get_tracer


@dataclass
class Result:
    svg: str
    mode: str
    size: tuple[int, int]
    scale: float
    stats: dict
    scores: dict


def run_pipeline(path, p: Params | None = None, score: bool = True) -> Result:
    """preprocess -> trace -> simplify -> score for a single image.

    score=False skips the re-render and SSIM step (used by the live UI preview). The scores are
    None when no SVG rasteriser is installed, and None with an "error" when the check went wrong.
    """
    p = p or Params()
    prep = prepare(path, p)
    svg = get_tracer(p.backend).trace(prep, p)
    svg = postprocess(svg, p, prep.display_size)
    return Result(
        svg=svg,
        mode=prep.mode,
        size=prep.size,
        scale=round(prep.scale, 4),
        stats=svg_stats(svg),
        scores=score_or_none(svg, prep) if score else {},
    )
