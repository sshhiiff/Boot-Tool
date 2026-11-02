"""Halftone maker, modelled on Photoshop's Image > Mode > Bitmap > Halftone Screen.

Every output pixel is compared with a repeating dot pattern (the "spot function"): it turns
ink when the image tone at that pixel is darker than the pattern value there. Dots therefore
grow and merge inside a shape and are clipped by its edge, with no stray circles outside it.
The 1-bit result is traced to vector, so plates stay vector.

Frequency is in lines per inch (LPI); output resolution is in pixels per inch.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

import cv2
import numpy as np

from .config import Params
from .page import Job, PageOpts, Plate, fit_artwork
from .preprocess import load_rgb
from .trace import mask_to_path

MAX_DOTS = 300_000          # cells per plate; beyond this the SVG is unusably heavy
MAX_PIXELS = 30_000_000     # output pixels per plate
SHAPES = ("round", "diamond", "ellipse", "line", "square", "cross", "custom")


@dataclass(frozen=True)
class HalftoneParams:
    mode: str = "one"               # one | cmyk
    lpi: float = 30.0               # frequency, lines per inch
    angle: float = 22.5             # one-ink screen angle
    shape: str = "round"            # round | diamond | ellipse | line | square | cross | custom
    ppi: float = 300.0              # output resolution, pixels per inch
    auto_levels: bool = True        # stretch the tones so the image uses the full range
    contrast: float = 1.0
    brightness: float = 0.0         # -0.5 darker .. +0.5 lighter
    smoothing: float = 0.0          # blur (output px) before the threshold
    edge: float = 0.5               # rounding of traced dot edges (output px); 0 = raw pixels
    invert: bool = False
    ink: str = "#000000"
    black_amount: float = 0.6       # CMYK: how much of the dark tones go to the black plate
    stamp: tuple = ()               # 256 ints, a 16x16 drawing for shape == "custom"


_BOUNDS = {"lpi": (5, 100), "angle": (0, 90), "ppi": (72, 1200), "contrast": (0.3, 3),
           "brightness": (-0.5, 0.5), "smoothing": (0, 6), "edge": (0, 2), "black_amount": (0, 1)}
_CHOICES = {"mode": {"one", "cmyk"}, "shape": set(SHAPES)}


def coerce_halftone(raw: dict) -> HalftoneParams:
    d = HalftoneParams()
    out = {}
    raw = dict(raw)
    if raw.get("shape") == "dot":                       # name used by earlier versions
        raw["shape"] = "round"
    for f in fields(HalftoneParams):
        if f.name not in raw:
            continue
        v, dv = raw[f.name], getattr(d, f.name)
        if f.name == "stamp":
            bits = [1 if x else 0 for x in list(v)[:256]]
            out["stamp"] = tuple(bits) if len(bits) == 256 else ()
        elif isinstance(dv, bool):
            out[f.name] = bool(v)
        elif isinstance(dv, float):
            lo, hi = _BOUNDS[f.name]
            out[f.name] = min(max(float(v), lo), hi)
        elif f.name == "ink":
            s = str(v)
            ok = len(s) == 7 and s[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in s[1:])
            out["ink"] = s if ok else dv
        else:
            out[f.name] = str(v) if str(v) in _CHOICES[f.name] else dv
    return HalftoneParams(**out)


# --------------------------------------------------------------------------- spot function

def _stamp_field(bits) -> np.ndarray:
    """16x16 drawing -> smooth 'depth' field (higher = inked first), tiled by the caller."""
    if len(bits) != 256 or not any(bits):
        raise ValueError("Draw a shape in the pattern creator first.")
    a = np.kron(np.array(bits, np.uint8).reshape(16, 16), np.ones((8, 8), np.uint8))
    inside = cv2.distanceTransform(a, cv2.DIST_L2, 3)
    outside = cv2.distanceTransform(1 - a, cv2.DIST_L2, 3)
    return cv2.GaussianBlur(inside - outside, (0, 0), 2.0).astype(np.float32)


def _spot(shape: str, u, v, stamp=None):
    """Pattern value over one cell; u, v in [-0.5, 0.5). Higher = inked sooner."""
    if shape == "round":
        return np.cos(2 * np.pi * u) + np.cos(2 * np.pi * v)
    if shape == "ellipse":
        return np.cos(2 * np.pi * u) + 0.6 * np.cos(2 * np.pi * v)
    if shape == "square":
        return -np.maximum(np.abs(u), np.abs(v))
    if shape == "diamond":
        return -(np.abs(u) + np.abs(v))
    if shape == "line":
        return -np.abs(v)
    if shape == "cross":
        return -np.minimum(np.abs(u), np.abs(v))
    # custom: look the drawing up in a tiled field
    h, w = stamp.shape
    xi = ((u + 0.5) * w).astype(np.int64) % w
    yi = ((v + 0.5) * h).astype(np.int64) % h
    return stamp[yi, xi]


def _rank_table(shape: str, stamp=None) -> np.ndarray:
    """Sorted pattern values over one cell, so a pattern value maps to the fraction of the
    cell inked before it (this is what makes tone and ink area agree)."""
    g = (np.arange(256) + 0.5) / 256 - 0.5
    gu, gv = np.meshgrid(g, g)
    return np.sort(_spot(shape, gu, gv, stamp).ravel())


# --------------------------------------------------------------------------- tone

def _prepare(path, p: HalftoneParams) -> np.ndarray:
    return load_rgb(path)


def _tone(t: np.ndarray, p: HalftoneParams) -> np.ndarray:
    """t = 0 (no ink) .. 1 (full ink)."""
    t = np.clip(t * p.contrast - p.brightness, 0, 1)
    return 1 - t if p.invert else t


def _levels(g: np.ndarray) -> np.ndarray:
    """Stretch greyscale (0..255 float) between its 1st and 99th percentile, ignoring paper."""
    body = g[g < 250]
    if body.size < 100:
        return g
    lo, hi = np.percentile(body, [1, 99])
    if hi - lo < 8:
        return g
    return np.clip((g - lo) / (hi - lo) * 255, 0, 255)


CMYK_PLATES = (("Cyan", "#00aeef", 15.0), ("Magenta", "#ec008c", 75.0),
               ("Yellow", "#fff200", 0.0), ("Black", "#000000", 45.0))


def cmyk_split(rgb: np.ndarray, black_amount: float):
    f = rgb.astype(np.float32) / 255
    k0 = 1 - f.max(axis=2)
    k = k0 * black_amount
    den = np.maximum(1 - k, 1e-3)
    c = (1 - f[..., 0] - k) / den
    m = (1 - f[..., 1] - k) / den
    y = (1 - f[..., 2] - k) / den
    return [np.clip(a, 0, 1) for a in (c, m, y, k)]


# --------------------------------------------------------------------------- screening

def _screen(tone: np.ndarray, cell_px: float, ang_deg: float, table: np.ndarray,
            shape: str, stamp) -> np.ndarray:
    """tone (H, W) in 0..1 -> boolean ink mask, in row chunks to keep memory flat."""
    h, w = tone.shape
    a = np.deg2rad(ang_deg)
    ca, sa = np.cos(a), np.sin(a)
    xs = (np.arange(w, dtype=np.float32) - w / 2)[None, :]
    out = np.empty((h, w), bool)
    step = 256
    lin = np.linspace(0, 1, table.size, dtype=np.float32)
    for y0 in range(0, h, step):
        y1 = min(h, y0 + step)
        ys = (np.arange(y0, y1, dtype=np.float32) - h / 2)[:, None]
        xr = (xs * ca + ys * sa) / cell_px
        yr = (-xs * sa + ys * ca) / cell_px
        u = xr - np.floor(xr) - 0.5
        v = yr - np.floor(yr) - 0.5
        f = _spot(shape, u, v, stamp)
        rank = 1 - np.interp(f, table, lin)
        out[y0:y1] = tone[y0:y1] > rank
    return out


def build(path, p: HalftoneParams, page: PageOpts) -> Job:
    rgb = _prepare(path, p)
    h, w = rgb.shape[:2]
    art = fit_artwork(page, w, h)
    ax, ay, aw, ah = art
    cell_mm = 25.4 / p.lpi
    expected = aw * ah / (cell_mm * cell_mm)
    if expected > MAX_DOTS:
        raise ValueError(f"About {int(expected):,} dots per plate is too many. Lower the LPI "
                         f"or use a smaller page.")
    px_mm = 25.4 / p.ppi
    W, H = max(8, int(round(aw / px_mm))), max(8, int(round(ah / px_mm)))
    if W * H > MAX_PIXELS:
        k = (MAX_PIXELS / (W * H)) ** 0.5
        W, H = max(8, int(W * k)), max(8, int(H * k))
        px_mm = aw / W
    cell_px = cell_mm / px_mm
    if cell_px < 3:
        raise ValueError("Output resolution is too low for this LPI. Raise the pixels/inch "
                         "or lower the LPI.")
    interp = cv2.INTER_AREA if W < w else cv2.INTER_CUBIC
    big = cv2.resize(rgb, (W, H), interpolation=interp)

    stamp = _stamp_field(p.stamp) if p.shape == "custom" else None
    table = _rank_table(p.shape, stamp)

    if p.mode == "one":
        g = cv2.cvtColor(big, cv2.COLOR_RGB2GRAY).astype(np.float32)
        if p.auto_levels:
            g = _levels(g)
        planes = [("Ink", p.ink, p.angle, 1 - g / 255)]
    else:
        planes = [(n, hx, ang, ch) for (n, hx, ang), ch in zip(CMYK_PLATES, cmyk_split(big, p.black_amount))]

    tp = Params(speckle=0, smooth_sigma=p.edge, epsilon=0.8, corner_deg=125.0, precision=1)
    job = Job(opts=page, art=art)
    shapes_total = 0
    for name, ink, ang, plane in planes:
        if p.smoothing > 0:
            plane = cv2.GaussianBlur(plane.astype(np.float32), (0, 0), p.smoothing)
        mask = _screen(_tone(plane, p), cell_px, ang, table, p.shape, stamp)
        d = mask_to_path(mask, tp)
        body = (f'<g transform="translate({ax:.3f} {ay:.3f}) scale({px_mm:.6f})">'
                f'<path fill-rule="evenodd" d="{d}"/></g>') if d else ""
        job.plates.append(Plate(name, ink, body))
        shapes_total += d.count("M")
    job.info = {"dots": shapes_total, "spacing_mm": round(cell_mm, 3), "lpi": p.lpi,
                "ppi": round(25.4 / px_mm), "overprint": p.mode == "cmyk"}
    return job
