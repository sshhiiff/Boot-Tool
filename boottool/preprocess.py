"""Stage 1: load, clean and reduce an image to a tracing target.

Line art  -> boolean ink mask
Colour    -> label map + palette (k-means in Lab space)
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps
from scipy.spatial import cKDTree

from .config import Params


@dataclass
class Prepared:
    mode: str
    rgb: np.ndarray                 # processed reference image (H, W, 3) uint8
    scale: float
    ink: str = "#000000"
    mask: np.ndarray | None = None      # line art: True where ink
    labels: np.ndarray | None = None    # colour: (H, W) uint8 palette index
    palette: np.ndarray | None = None   # colour: (K, 3) uint8

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.rgb.shape[:2]
        return w, h

    @property
    def display_size(self) -> tuple[int, int]:
        """Size the SVG should declare: the working size, but never smaller than the source
        (so a 0.25x 'crunch' still opens at the original dimensions)."""
        w, h = self.size
        if self.scale < 1:
            return max(1, round(w / self.scale)), max(1, round(h / self.scale))
        return w, h


def load_rgb(path) -> np.ndarray:
    im = Image.open(path)
    im.load()
    im = ImageOps.exif_transpose(im)
    if im.mode.startswith("I") or im.mode == "F":
        arr = np.asarray(im, dtype=np.float32)
        arr = arr / max(float(arr.max()), 1.0) * 255.0
        im = Image.fromarray(arr.astype(np.uint8))
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    return np.asarray(im.convert("RGB"), dtype=np.uint8).copy()


def detect_mode(rgb: np.ndarray) -> str:
    """Line art if the image is grey, or toned in a single hue (sepia scans, blue prints)."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    colourful = (hsv[..., 1] > 45) & (hsv[..., 2] > 40)
    if colourful.mean() < 0.03:
        return "lineart"
    hist = np.bincount(hsv[..., 0][colourful].astype(np.int64), minlength=180)
    ext = np.concatenate([hist[-12:], hist, hist[:12]])
    window = np.convolve(ext, np.ones(25), mode="valid")      # circular +-12 hue window
    return "lineart" if window.max() / hist.sum() >= 0.93 else "color"


def prepare(path, p: Params) -> Prepared:
    rgb = load_rgb(path)
    h, w = rgb.shape[:2]
    longer = max(h, w)
    scale = p.upscale
    # The minimum-size floor only applies when not deliberately shrinking (upscale < 1).
    if scale >= 1 and longer * scale < p.min_side:
        scale = p.min_side / longer
    if longer * scale > p.max_side:
        scale = p.max_side / longer
    if scale != 1.0:
        if scale < 1:
            interp = cv2.INTER_AREA
        else:
            interp = cv2.INTER_NEAREST if p.upscale_style == "pixel" else cv2.INTER_CUBIC
        rgb = cv2.resize(
            rgb, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=interp
        )
    mode = detect_mode(rgb) if p.mode == "auto" else p.mode
    if mode == "lineart":
        return _prep_lineart(rgb, p, scale)
    if mode == "color":
        return _prep_color(rgb, p, scale)
    raise ValueError(f"unknown mode: {mode!r}")


# --------------------------------------------------------------------------- line art

def _normalize_background(gray: np.ndarray) -> np.ndarray:
    """Divide by an estimate of the paper tone so stains and gradients flatten out."""
    h, w = gray.shape
    f = 0.25
    small = cv2.resize(gray, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)
    k = max(5, int(max(small.shape) / 6)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
    bg = cv2.GaussianBlur(bg, (0, 0), max(1.0, k / 4))
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.divide(gray, np.maximum(bg, 1), scale=255)


def _binarize(gray: np.ndarray, method: str, level: int = 0) -> np.ndarray:
    if level > 0:                        # manual threshold: ink = darker than `level`
        _, bw = cv2.threshold(gray, level, 255, cv2.THRESH_BINARY_INV)
    elif method == "adaptive":
        block = max(11, (max(gray.shape) // 20)) | 1
        bw = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 8
        )
    else:
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return bw > 0


def _prep_lineart(rgb: np.ndarray, p: Params, scale: float) -> Prepared:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    if np.median(border) < 110:          # light ink on a dark ground: flip so ink is dark
        gray = 255 - gray
    if p.denoise:
        gray = cv2.medianBlur(gray, 3)

    mask = _binarize(gray, p.threshold, p.level)
    if p.normalize_background:
        norm_mask = _binarize(_normalize_background(gray), p.threshold, p.level)
        # Guard: huge solid fills can be mistaken for paper tone and erased.
        if norm_mask.sum() >= 0.6 * mask.sum():
            mask = norm_mask

    ink = p.ink
    if ink == "auto":
        core = cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        sel = core if core.any() else mask
        if sel.any():
            r, g, b = (int(v) for v in rgb[sel].mean(axis=0))
            ink = f"#{r:02x}{g:02x}{b:02x}"
        else:
            ink = "#000000"
    return Prepared(mode="lineart", rgb=rgb, scale=scale, ink=ink, mask=mask)


# --------------------------------------------------------------------------- colour

def _prep_color(rgb: np.ndarray, p: Params, scale: float) -> Prepared:
    sm = cv2.bilateralFilter(rgb, 9, 40, 7) if p.denoise else rgb
    h, w = sm.shape[:2]
    lab = cv2.cvtColor(sm, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)

    rng = np.random.default_rng(0)
    idx = rng.choice(len(lab), size=min(len(lab), 60000), replace=False)
    sample = lab[idx]
    k = max(2, min(p.colors, len(np.unique(sample, axis=0))))

    cv2.setRNGSeed(0)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, _, centers = cv2.kmeans(sample, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    _, assign = cKDTree(centers).query(lab)
    labels = assign.reshape(h, w).astype(np.uint8)
    labels = cv2.medianBlur(labels, 3)

    used = np.unique(labels)
    remap = np.zeros(256, np.uint8)
    remap[used] = np.arange(len(used), dtype=np.uint8)
    labels = remap[labels]

    n = len(used)
    counts = np.maximum(np.bincount(labels.ravel(), minlength=n), 1)
    palette = np.stack(
        [
            np.bincount(labels.ravel(), weights=sm[..., c].ravel().astype(np.float64), minlength=n)
            / counts
            for c in range(3)
        ],
        axis=1,
    )
    return Prepared(
        mode="color",
        rgb=sm,
        scale=scale,
        labels=labels,
        palette=np.clip(np.rint(palette), 0, 255).astype(np.uint8),
    )
