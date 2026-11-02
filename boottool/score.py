"""Stage 4: re-rasterize the SVG and measure how faithful it is."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import cv2
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity

from .preprocess import Prepared


def rasterize(svg_text: str, w: int, h: int) -> np.ndarray:
    """SVG text -> (h, w, 3) uint8 on white. Uses rsvg-convert, else cairosvg."""
    if shutil.which("rsvg-convert"):
        with tempfile.TemporaryDirectory() as td:
            src, dst = os.path.join(td, "in.svg"), os.path.join(td, "out.png")
            with open(src, "w", encoding="utf-8") as f:
                f.write(svg_text)
            subprocess.run(
                ["rsvg-convert", "-w", str(w), "-h", str(h), "-b", "white", "-f", "png",
                 "-o", dst, src],
                check=True, capture_output=True,
            )
            return np.asarray(Image.open(dst).convert("RGB"), dtype=np.uint8).copy()
    try:
        import cairosvg
    except ImportError as e:
        raise RuntimeError(
            "No SVG rasterizer found. Install librsvg (rsvg-convert) or `pip install cairosvg`."
        ) from e
    import io

    png = cairosvg.svg2png(
        bytestring=svg_text.encode("utf-8"), output_width=w, output_height=h,
        background_color="white",
    )
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.uint8).copy()


def _gray(a: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    win = min(7, min(a.shape[:2]))
    win -= (win % 2 == 0)
    return float(structural_similarity(_gray(a), _gray(b), data_range=255, win_size=max(win, 3)))


def score_svg(svg_text: str, prep: Prepared) -> dict:
    """
    ssim_target : SVG vs the binarized / quantized target. This measures tracing quality
                  and is what the quality gate uses.
    ssim_source : SVG vs the cleaned source. Informational; low for noisy scans by design.
    mae_target  : mean absolute colour error vs the target, 0..1 (lower is better).
    """
    w, h = prep.size
    out = rasterize(svg_text, w, h)
    if prep.mode == "lineart":
        target = np.repeat(np.where(prep.mask, 0, 255).astype(np.uint8)[..., None], 3, axis=2)
    else:
        target = prep.palette[prep.labels]
    return {
        "ssim_target": round(_ssim(out, target), 4),
        "ssim_source": round(_ssim(out, prep.rgb), 4),
        "mae_target": round(float(np.abs(out.astype(np.int16) - target).mean() / 255.0), 4),
    }
