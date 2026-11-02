"""Print tab: one ink, CMYK or spot colour, as screen halftone or dither, with Levels.

Everything is laid out on a real page in millimetres and comes back as plates (a Job), the
same as the older halftone and separate builders. Spot colour has two image types:
  flat   flat-colour artwork split into one solid plate per ink (see separate.py)
  photo  each ink is halftoned (or dithered) on its own plate, with soft colour matching so
         gradients are shared between neighbouring inks
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, fields
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image

from . import halftone as ht
from . import separate as sep
from .config import Params
from .page import Job, PageOpts, Plate, fit_artwork
from .preprocess import load_rgb
from .trace import mask_to_path

MAX_INKS = 10
MAX_DITHER_PX = 3_000_000       # dither pixels per plate
SPOT_WORK_PX = 2_500_000        # colour-matching resolution for spot photos
DITHERS = ("floyd", "atkinson", "stucki", "bayer", "blue", "serpentine", "random")
PATTERNS = ("mazes", "coral", "fingerprints", "loops", "spots")
DEFAULT_ANGLES = (15.0, 75.0, 0.0, 45.0, 22.5, 67.5, 30.0, 60.0, 7.5, 52.5)


@dataclass(frozen=True)
class PrintParams:
    method: str = "one"             # one | cmyk | spot
    kind: str = "flat"              # spot only: flat | photo
    render: str = "screen"          # screen | dither
    # levels (like Photoshop): black point, midtone gamma, white point
    lv_black: float = 0.0
    lv_gamma: float = 1.0
    lv_white: float = 255.0
    # screen halftone
    lpi: float = 30.0
    angle: float = 22.5             # one-ink angle
    shape: str = "round"
    ppi: float = 300.0
    edge: float = 0.5
    angles: tuple = ()              # per plate (CMYK / spot photo); negative = automatic
    # dither
    dither: str = "floyd"
    dither_size: float = 0.3        # mm per dither pixel
    pattern: str = "mazes"          # serpentine dither: mazes | coral | fingerprints | loops | spots
    pattern_size: float = 9.0       # serpentine dither: width of one stripe cycle, in grains
    # tone
    contrast: float = 1.0
    brightness: float = 0.0
    smoothing: float = 0.0
    invert: bool = False
    draft: bool = False             # preview only: coarser dots so it draws quickly
    # colour
    ink: str = "#000000"
    black_amount: float = 0.6
    cmyk_colors: tuple = ()         # 4 hex, "" = standard
    # spot colour
    inks: int = 4
    paper: bool = True
    trap: float = 0.2
    speckle: int = 8
    smooth: float = 0.6
    colors: tuple = ()              # hex per ink, "" = automatic (photo: used for the separation)
    names: tuple = ()
    picked: bool = False            # the colours are exactly the inks to use (eyedropper), in print order
    dark: bool = False              # printing on a dark garment / paper
    dark_bg: str = "#1b1b1d"        # its colour (preview only; never printed)
    dark_invert: bool = False       # swap the dark and empty areas so it looks the same on the garment
    outline_mm: float = 5.0         # ink margin grown round the design when inverted
    underbase: bool = False         # extra first plate (white) cut to the printed shape
    keep_solid: bool = False        # spot photo: flat areas of an ink stay solid instead of turning into dots
    solid_detect: float = 0.5       # 0 strict .. 1 loose: how readily an area counts as flat
    stamp: tuple = ()               # 256 ints for shape == "custom"


_BOUNDS = {
    "lv_black": (0.0, 253.0), "lv_gamma": (0.1, 9.99), "lv_white": (2.0, 255.0),
    "lpi": (5.0, 100.0), "angle": (0.0, 90.0), "ppi": (72.0, 1200.0), "edge": (0.0, 2.0),
    "dither_size": (0.1, 2.0), "pattern_size": (3.0, 40.0), "contrast": (0.3, 3.0), "brightness": (-0.5, 0.5),
    "smoothing": (0.0, 6.0), "black_amount": (0.0, 1.0), "inks": (1, MAX_INKS),
    "trap": (0.0, 1.0), "speckle": (0, 100), "smooth": (0.0, 2.0), "solid_detect": (0.0, 1.0), "outline_mm": (0.0, 30.0),
}
_CHOICES = {"method": {"one", "cmyk", "spot"}, "kind": {"flat", "photo"},
            "render": {"screen", "dither"}, "shape": set(ht.SHAPES), "dither": set(DITHERS),
            "pattern": set(PATTERNS)}


def _is_hex(s) -> bool:
    s = str(s)
    return len(s) == 7 and s[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in s[1:])


def coerce_print(raw: dict) -> PrintParams:
    d = PrintParams()
    out = {}
    raw = dict(raw)
    if raw.get("shape") == "dot":
        raw["shape"] = "round"
    for f in fields(PrintParams):
        if f.name not in raw:
            continue
        v, dv = raw[f.name], getattr(d, f.name)
        if f.name == "stamp":
            bits = [1 if x else 0 for x in list(v)[:256]]
            out["stamp"] = tuple(bits) if len(bits) == 256 else ()
        elif f.name == "angles":
            vals = []
            for x in list(v)[:MAX_INKS]:
                try:
                    a = float(x)
                except (TypeError, ValueError):
                    a = -1.0
                vals.append(min(a, 90.0) if a >= 0 else -1.0)
            out["angles"] = tuple(vals)
        elif f.name == "cmyk_colors":
            out["cmyk_colors"] = tuple(str(x) if _is_hex(x) else "" for x in list(v)[:4])
        elif f.name == "colors":
            out["colors"] = tuple(str(x) if _is_hex(x) else "" for x in list(v)[:MAX_INKS])
        elif f.name == "names":
            out["names"] = tuple(str(x)[:30] for x in list(v)[:MAX_INKS])
        elif f.name in ("ink", "dark_bg"):
            out[f.name] = str(v) if _is_hex(v) else dv
        elif isinstance(dv, bool):
            out[f.name] = bool(v)
        elif f.name in _CHOICES:
            out[f.name] = str(v) if str(v) in _CHOICES[f.name] else dv
        else:
            lo, hi = _BOUNDS[f.name]
            out[f.name] = type(dv)(round(min(max(float(v), lo), hi))) if isinstance(dv, int) \
                else min(max(float(v), lo), hi)
    p = PrintParams(**out)
    if p.lv_white - p.lv_black < 2:                       # keep the handles apart
        p = PrintParams(**{**out, "lv_black": min(p.lv_black, p.lv_white - 2)})
    return p


# --------------------------------------------------------------------------- levels

def auto_points(g: np.ndarray) -> tuple[int, int]:
    """Black and white points that stretch the picture's tones, ignoring pure paper."""
    body = g[g < 250]
    if body.size < 100:
        return 0, 255
    lo, hi = np.percentile(body, [1, 99])
    if hi - lo < 8:
        return 0, 255
    return int(lo), int(hi)


def analyse(path) -> dict:
    """Histogram, suggested levels and whether the picture has continuous tones."""
    rgb = load_rgb(path)
    h, w = rgb.shape[:2]
    k = 900 / max(h, w)
    if k < 1:
        rgb = cv2.resize(rgb, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hist = np.bincount(g.ravel(), minlength=256)
    b, wh = auto_points(g)
    tonal = int((hist / g.size > 0.002).sum()) > 12
    return {"hist": [int(x) for x in hist], "auto": [b, 1.0, wh], "tonal": bool(tonal)}


def _lut(p: PrintParams) -> np.ndarray | None:
    if p.lv_black <= 0 and p.lv_white >= 255 and abs(p.lv_gamma - 1) < 1e-6:
        return None
    x = np.clip((np.arange(256, dtype=np.float32) - p.lv_black) / max(p.lv_white - p.lv_black, 1.0), 0, 1)
    return np.round(x ** (1.0 / p.lv_gamma) * 255).astype(np.uint8)


# --------------------------------------------------------------------------- dithering

_KERNELS = {
    "floyd": [(1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)],
    "atkinson": [(1, 0, 1 / 8), (2, 0, 1 / 8), (-1, 1, 1 / 8), (0, 1, 1 / 8), (1, 1, 1 / 8), (0, 2, 1 / 8)],
    "stucki": [(1, 0, 8 / 42), (2, 0, 4 / 42), (-2, 1, 2 / 42), (-1, 1, 4 / 42), (0, 1, 8 / 42),
               (1, 1, 4 / 42), (2, 1, 2 / 42), (-2, 2, 1 / 42), (-1, 2, 2 / 42), (0, 2, 4 / 42),
               (1, 2, 2 / 42), (2, 2, 1 / 42)],
}


def _diffuse(tone: np.ndarray, kernel) -> np.ndarray:
    """Error diffusion, processed along diagonal wavefronts so whole fronts run in numpy.
    Pixels with the same k = x + 3y never feed each other, so the result is exact."""
    h, w = tone.shape
    pad = 2
    a = np.zeros((h + pad, w + 2 * pad), np.float32)
    a[:h, pad:pad + w] = tone
    out = np.zeros((h, w), bool)
    for k in range(w + 3 * (h - 1)):
        y0 = max(0, -(-(k - (w - 1)) // 3))
        y1 = min(h - 1, k // 3)
        if y1 < y0:
            continue
        ys = np.arange(y0, y1 + 1)
        xs = k - 3 * ys
        v = a[ys, xs + pad]
        on = v >= 0.5
        out[ys, xs] = on
        e = v - on
        for dx, dy, wt in kernel:
            a[ys + dy, xs + dx + pad] += e * wt
    return out


@lru_cache(maxsize=1)
def _bayer(n: int = 8) -> np.ndarray:
    m = np.array([[0]])
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return (m + 0.5) / (n * n)


@lru_cache(maxsize=1)
def _blue(n: int = 64) -> np.ndarray:
    """Blue-noise threshold tile by void filling: each new point goes in the emptiest spot."""
    yy, xx = np.mgrid[0:n, 0:n]
    dy, dx = np.minimum(yy, n - yy), np.minimum(xx, n - xx)
    kern = np.exp(-(dx * dx + dy * dy) / (2 * 1.5 ** 2)).astype(np.float32)
    rng = np.random.default_rng(7)
    energy = (rng.random((n, n)) * 1e-3).astype(np.float32)
    placed = np.zeros((n, n), bool)
    rank = np.zeros((n, n), np.float32)
    for i in range(n * n):
        y, x = divmod(int(np.argmin(np.where(placed, np.inf, energy))), n)
        rank[y, x] = i
        placed[y, x] = True
        energy += np.roll(np.roll(kern, y, 0), x, 1)
    return (rank + 0.5) / (n * n)


# name -> (bandwidth as a fraction of the stripe frequency, iterations, gain, stretch, seed)
_PATTERN_SPEC = {
    "mazes": (0.16, 45, 3.0, 1.0, 1),
    "coral": (0.45, 25, 2.2, 1.0, 2),
    "fingerprints": (0.13, 55, 3.2, 0.5, 3),
    "loops": (0.30, 30, 1.6, 1.0, 4),
    "spots": (0.20, 10, 1.1, 1.0, 5),
}


@lru_cache(maxsize=32)
def _serpentine(pattern: str = "mazes", size: float = 9.0, n: int = 256) -> np.ndarray:
    """Tileable maze-like threshold map (a Turing-style pattern). Random noise is filtered to one
    stripe width, then squashed and filtered again; repeating that lets stripes join into long
    serpentine mazes. The result is ranked, so a tone of t inks exactly the fraction t of the tile."""
    bw, iters, gain, stretch, seed = _PATTERN_SPEC[pattern]
    k0 = 1.0 / size
    fy = np.fft.fftfreq(n)[:, None]
    fx = np.fft.fftfreq(n)[None, :]
    ring = np.exp(-((np.sqrt(fx ** 2 + (fy * stretch) ** 2) - k0) / (bw * k0)) ** 2 / 2)
    u = np.random.default_rng(seed).standard_normal((n, n))

    def filt(a):
        f = np.fft.ifft2(np.fft.fft2(a) * ring).real
        return f / (f.std() + 1e-9)
    for _ in range(iters):
        u = np.tanh(gain * filt(u))
    field = filt(u)
    rank = np.empty(n * n)
    rank[np.argsort(field.ravel(), kind="stable")] = np.arange(n * n)
    return ((rank + 0.5) / (n * n)).reshape(n, n).astype(np.float32)


def dither_mask(tone: np.ndarray, algo: str, plate: int = 0, tile: np.ndarray | None = None) -> np.ndarray:
    """tone 0 (no ink) .. 1 (full ink) -> boolean ink mask."""
    if algo in _KERNELS:
        return _diffuse(tone.astype(np.float32), _KERNELS[algo])
    h, w = tone.shape
    if algo == "random":
        return tone > np.random.default_rng(11 + plate).random((h, w), dtype=np.float32)
    if algo == "serpentine":
        tile = tile if tile is not None else _serpentine()
        off = (61, 97)
    else:
        tile = _bayer() if algo == "bayer" else _blue()
        off = (17, 29)
    th, tw = tile.shape
    ys = (np.arange(h) + plate * off[0]) % th
    xs = (np.arange(w) + plate * off[1]) % tw
    return tone > tile[ys[:, None], xs[None, :]]


def runs_path(mask: np.ndarray) -> tuple[str, int]:
    """Horizontal runs of ink as rectangles in one path (one pixel = one unit)."""
    d = np.diff(np.pad(mask.astype(np.int8), ((0, 0), (1, 1))), axis=1)
    ys, xs = np.nonzero(d == 1)
    _, xe = np.nonzero(d == -1)
    ln = xe - xs
    body = "".join(f"M{x} {y}h{l}v1h-{l}z" for x, y, l in zip(xs.tolist(), ys.tolist(), ln.tolist()))
    return body, int(len(xs))


# --------------------------------------------------------------------------- planes

def _hex_to_lab(h: str) -> np.ndarray:
    rgb = np.array([[[int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)]]], np.float32) / 255
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2Lab)[0, 0]


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(round(c)) for c in rgb)


def _work(rgb: np.ndarray, W: int, H: int, max_px: int | None = None) -> np.ndarray:
    h, w = rgb.shape[:2]
    tw, th = min(w, W), min(h, H)
    if max_px and tw * th > max_px:
        s = (max_px / (tw * th)) ** 0.5
        tw, th = max(1, int(tw * s)), max(1, int(th * s))
    if (tw, th) != (w, h):
        rgb = cv2.resize(rgb, (tw, th), interpolation=cv2.INTER_AREA)
    return rgb


def _solid_masks(lab, cen, planes, detect: float):
    """Per ink: pixels that are flat (no texture around them) and sit on that ink's colour."""
    d = float(min(max(detect, 0.0), 1.0))
    k9 = np.ones((9, 9), np.uint8)
    rng = sum(cv2.dilate(lab[..., c], k9) - cv2.erode(lab[..., c], k9) for c in range(3))
    dist = np.stack([np.linalg.norm(lab - c, axis=2) for c in cen])
    win = dist.argmin(0)
    core = ((rng < 4 + 20 * d) & (dist.min(0) < 12 + 20 * d)).astype(np.uint8)
    core = cv2.morphologyEx(core, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    grown = cv2.dilate(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(bool)
    out = []
    for i, pl in enumerate(planes):
        m = (core.astype(bool) | (grown & (pl > 0.5))) & (win == i)
        out.append(m)
    return np.stack(out)


def _spot_planes(work: np.ndarray, p: PrintParams):
    """Soft colour matching: each ink's plane is how close each pixel is to that ink."""
    lab = cv2.cvtColor(work.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    paper = None
    if p.paper:
        border = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
        paper = np.median(border, axis=0)
        is_paper = np.linalg.norm(lab - paper, axis=2) < 8
        samples = lab[~is_paper]
    else:
        samples = lab.reshape(-1, 3)
    if samples.shape[0] < 50:
        samples = lab.reshape(-1, 3)
    samples = np.ascontiguousarray(samples[:: max(1, samples.shape[0] // 60000)], dtype=np.float32)
    picked = [c for c in p.colors if c] if p.picked else []
    if picked:                                                           # eyedropper: exactly these inks, in this order
        k = len(picked)
        cen = np.array([_hex_to_lab(c) for c in picked], np.float32)
        auto = cen.copy()
    else:
        k = int(min(p.inks, max(1, samples.shape[0] // 20)))
        cv2.setRNGSeed(20240)                                            # same picture, same inks, every time
        _, _, cen = cv2.kmeans(samples, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.1),
                               3, cv2.KMEANS_PP_CENTERS)
        cen = cen[np.argsort(-cen[:, 0])]                                # light to dark = print order
        auto = cen.copy()
        for i in range(k):
            if i < len(p.colors) and p.colors[i]:
                cen[i] = _hex_to_lab(p.colors[i])
    if k > 1:
        near = [np.sort(np.linalg.norm(cen - c, axis=1))[1] for c in cen]
        sigma = max(6.0, 0.55 * float(np.mean(near)))
    else:
        sigma = 20.0
    cents = list(cen) + ([paper] if paper is not None else [])
    ws = [np.exp(-((lab - c) ** 2).sum(2) / (2 * sigma ** 2)).astype(np.float32) + 1e-6 for c in cents]
    tot = np.sum(ws, axis=0)
    planes = [w_ / tot for w_ in ws[:k]]
    if paper is not None:                       # pixels that are paper print nothing, however pale the lightest ink
        t = np.clip((np.linalg.norm(lab - paper, axis=2) - 3.0) / 6.0, 0, 1)
        keep = (t * t * (3 - 2 * t)).astype(np.float32)
        planes = [pl * keep for pl in planes]
    to_rgb = lambda c: np.clip(cv2.cvtColor(np.array([[c]], np.float32), cv2.COLOR_Lab2RGB)[0, 0], 0, 1) * 255
    final = [to_rgb(c) for c in cen]
    autos = [to_rgb(c) for c in auto]
    seen: dict[str, int] = {}
    pal, names = [], []
    for i in range(k):
        base = sep.color_name(autos[i])
        seen[base] = seen.get(base, 0) + 1
        auto_name = base if seen[base] == 1 else f"{base} {seen[base]}"
        name = p.names[i] if i < len(p.names) and p.names[i] else auto_name
        names.append(name)
        pal.append({"hex": _hex(final[i]), "auto_hex": _hex(autos[i]), "name": name, "auto_name": auto_name})
    solid = _solid_masks(lab, cen, planes, p.solid_detect) if p.keep_solid else None
    if solid is not None:
        any_solid = np.any(solid, axis=0)
        planes = [pl * (~any_solid) for pl in planes]                    # solid areas are drawn as vector, not dots
    return planes, pal, (_hex(to_rgb(paper)) if paper is not None else None), solid


def _angle(p: PrintParams, i: int, default: float) -> float:
    return p.angles[i] if i < len(p.angles) and p.angles[i] >= 0 else default


def _design_region(dark: np.ndarray, mm_px: float, outline_mm: float) -> np.ndarray:
    """dark: 0..1 ink amount of the artwork. Returns a 0/1 region: the design's outer shape (gaps closed,
    holes filled) grown outwards by the outline."""
    t = (dark > 0.08).astype(np.uint8)
    k = max(1, int(round(0.8 / mm_px)))
    sil = cv2.morphologyEx(t, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    h, w = sil.shape
    pad = cv2.copyMakeBorder((1 - sil) * 255, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=255)
    flood = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(pad, flood, (0, 0), 128)
    filled = (pad[1:-1, 1:-1] != 128).astype(np.uint8)
    r = int(round(outline_mm / mm_px))
    if r > 0:
        filled = cv2.dilate(filled, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    return filled


def _underbase_mask(printed: np.ndarray, mm_px: float) -> np.ndarray:
    """Solid shape under everything that prints, pulled in a little so white never peeks out."""
    m = (printed > 0.03).astype(np.uint8)
    k = max(1, int(round(0.8 / mm_px)))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    e = max(1, int(round(0.25 / mm_px)))
    return cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * e + 1, 2 * e + 1))).astype(np.float32)


def _is_white(h: str) -> bool:
    return min(int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)) > 235


def _plate_specs(rgb: np.ndarray, p: PrintParams, W: int, H: int, aw: float = 0.0):
    """Returns (specs, plane(i), info): specs are (name, hex, angle); plane(i) is the ink amount
    (0..1) for plate i at working resolution."""
    lut = _lut(p)
    if p.method == "one":
        work = _work(rgb, W, H)
        g = cv2.cvtColor(work, cv2.COLOR_RGB2GRAY)
        if lut is not None:
            g = cv2.LUT(g, lut)
        plane = 1 - g.astype(np.float32) / 255
        if p.dark and aw:
            mm_px = aw / W
            if p.dark_invert:
                region = _design_region(plane, mm_px, p.outline_mm)
                plane = (1 - plane) * cv2.GaussianBlur(region.astype(np.float32), (0, 0), 0.6)
            if p.underbase and not _is_white(p.ink):
                ub = _underbase_mask(plane, mm_px)
                return ([("White underbase", "#ffffff", 0.0), ("Ink", p.ink, p.angle)],
                        (lambda i: ub if i == 0 else plane), {"garment": p.dark_bg})
            return [("Ink", p.ink, p.angle)], (lambda i: plane), {"garment": p.dark_bg}
        return [("Ink", p.ink, p.angle)], (lambda i: plane), {}
    if p.method == "cmyk":
        work = _work(rgb, W, H)
        if lut is not None:
            work = cv2.LUT(work, lut)
        chans = ht.cmyk_split(work, p.black_amount)
        specs = []
        for i, (name, hx, ang) in enumerate(ht.CMYK_PLATES):
            col = p.cmyk_colors[i] if i < len(p.cmyk_colors) and p.cmyk_colors[i] else hx
            specs.append((name, col, _angle(p, i, ang)))
        return specs, (lambda i: chans[i]), {}
    work = _work(rgb, W, H, SPOT_WORK_PX)
    if lut is not None:
        work = cv2.LUT(work, lut)
    planes, pal, paper, solid = _spot_planes(work, p)
    specs = [(pal[i]["name"], pal[i]["hex"], _angle(p, i, DEFAULT_ANGLES[i % len(DEFAULT_ANGLES)]))
             for i in range(len(planes))]
    fn = lambda i: planes[i]
    fn.solid = (solid, work.shape[1], work.shape[0]) if solid is not None else None
    return specs, fn, {"palette": pal, "paper": paper}


def _to_grid(plane: np.ndarray, W: int, H: int, smoothing: float) -> np.ndarray:
    h, w = plane.shape
    if (w, h) != (W, H):
        plane = cv2.resize(plane, (W, H), interpolation=cv2.INTER_AREA if W < w else cv2.INTER_CUBIC)
    if smoothing > 0:
        plane = cv2.GaussianBlur(plane, (0, 0), smoothing)
    return plane


# --------------------------------------------------------------------------- build

def _flat_dark(rgb: np.ndarray, p: PrintParams, mm_px: float) -> np.ndarray:
    """Swap positives and negatives: dark parts of the design become garment, light parts (and the outline
    margin) print as white, other colours stay."""
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    dark = 1 - g
    region = _design_region(dark, mm_px, p.outline_mm).astype(bool)
    f = rgb.astype(np.float32)
    chroma = f.max(2) - f.min(2)
    is_dark = (g < 0.3) & (chroma < 60)
    is_light = (g > 0.85) & (chroma < 40)
    gar = np.array([int(p.dark_bg[i:i + 2], 16) for i in (1, 3, 5)], np.uint8)
    out = np.empty_like(rgb); out[:] = gar
    keep = region & ~is_dark
    out[keep] = rgb[keep]
    out[region & is_light | (region & (dark < 0.08))] = (255, 255, 255)
    return out


def _flat(rgb: np.ndarray, p: PrintParams, page: PageOpts) -> Job:
    lut = _lut(p)
    if lut is not None:
        rgb = cv2.LUT(rgb, lut)
    mm_px = fit_artwork(page, rgb.shape[1], rgb.shape[0])[2] / rgb.shape[1]
    if p.dark and p.dark_invert:
        rgb = _flat_dark(rgb, p, mm_px)
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "levels.png")
        Image.fromarray(rgb).save(f)
        picked = [c for c in p.colors if c] if p.picked else []
        sp = sep.SepParams(inks=len(picked) or p.inks, paper=p.paper, trap=p.trap, speckle=p.speckle,
                           smooth=p.smooth, colors=tuple(picked) if picked else p.colors, names=p.names, fixed=bool(picked))
        job = sep.build(f, sp, page)
        if p.dark and p.underbase:
            border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
            gar = np.median(border, axis=0).astype(np.uint8)                       # the design's own paper
            printed = (np.abs(rgb.astype(np.int16) - gar.astype(np.int16)).sum(2) > 24).astype(np.float32)
            ub = _underbase_mask(printed, mm_px)
            two = np.empty_like(rgb); two[:] = gar if int(gar.sum()) > 40 else 255; two[ub > 0.5] = (0, 0, 0)
            f2 = os.path.join(td, "under.png")
            Image.fromarray(two).save(f2)
            ju = sep.build(f2, sep.SepParams(inks=1, paper=True, trap=0.0, speckle=p.speckle, smooth=p.smooth), page)
            if ju.plates:
                u = ju.plates[0]
                job.plates.insert(0, Plate("White underbase", "#ffffff", u.body))
    job.info["kind"] = "flat"
    if p.dark:
        job.info["garment"] = p.dark_bg
        job.info["overprint"] = False
    return job


def _parallel(fn, n):
    """Run fn(0..n-1) on threads so the plates build side by side (screening, blurring and
    contour finding spend much of their time outside Python's lock)."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(n, os.cpu_count() or 1)) as ex:
        return list(ex.map(fn, range(n)))


def build(path, p: PrintParams, page: PageOpts) -> Job:
    rgb = load_rgb(path)
    if p.method == "spot" and p.kind == "flat":
        return _flat(rgb, p, page)
    h, w = rgb.shape[:2]
    art = fit_artwork(page, w, h)
    ax, ay, aw, ah = art
    job = Job(opts=page, art=art)
    info: dict = {}

    if p.render == "screen":
        cell_mm = 25.4 / p.lpi
        if aw * ah / (cell_mm * cell_mm) > ht.MAX_DOTS:
            raise ValueError(f"About {int(aw * ah / (cell_mm * cell_mm)):,} dots per plate is too many. "
                             f"Lower the LPI or use a smaller page.")
        px_mm = 25.4 / p.ppi
        if p.draft:                 # draft: about 6 pixels across a cell instead of ~10
            px_mm = max(px_mm, cell_mm / 6)
        W, H = max(8, int(round(aw / px_mm))), max(8, int(round(ah / px_mm)))
        if W * H > ht.MAX_PIXELS:
            k = (ht.MAX_PIXELS / (W * H)) ** 0.5
            W, H = max(8, int(W * k)), max(8, int(H * k))
            px_mm = aw / W
        cell_px = cell_mm / px_mm
        if cell_px < 3:
            raise ValueError("Output resolution is too low for this LPI. Raise the pixels/inch or lower the LPI.")
        stamp = ht._stamp_field(p.stamp) if p.shape == "custom" else None
        table = ht._rank_table(p.shape, stamp)
        tp = Params(speckle=0, smooth_sigma=p.edge, epsilon=0.8, corner_deg=125.0, precision=1)
        info.update(kind="screen", spacing_mm=round(cell_mm, 3), lpi=p.lpi, ppi=round(25.4 / px_mm))
    else:
        size = p.dither_size
        W, H = max(8, int(round(aw / size))), max(8, int(round(ah / size)))
        if W * H > MAX_DITHER_PX:
            raise ValueError(f"A dither size of {size:.2f} mm makes {W * H:,} pixels on this page, which is too "
                             f"many. Make the dither size larger or use a smaller page.")
        info.update(kind="dither", size_mm=round(size, 3), dither_ppi=round(25.4 / size))
        if p.dither == "serpentine":
            info["pattern_mm"] = round(p.pattern_size * size, 2)

    serp = _serpentine(p.pattern, round(p.pattern_size, 2)) if (p.render == "dither" and p.dither == "serpentine") else None
    specs, plane, extra = _plate_specs(rgb, p, W, H, aw)
    info.update(extra)
    total = 0

    solid = getattr(plane, "solid", None)

    def solid_body(i):
        """Flat areas of ink i as clean vector shapes, in page millimetres."""
        if not solid:
            return "", 0
        masks, sw, sh = solid
        if not masks[i].any():
            return "", 0
        sd = mask_to_path(masks[i], Params(speckle=0, smooth_sigma=0.6, epsilon=0.8, corner_deg=125.0, precision=1), fast=p.draft)
        return ((f'<g transform="translate({ax:.3f} {ay:.3f}) scale({aw / sw:.6f} {ah / sh:.6f})">'
                 f'<path fill-rule="evenodd" d="{sd}"/></g>') if sd else ""), sd.count("M")

    def one(i):
        name, ink, ang = specs[i]
        t = ht._tone(np.clip(_to_grid(plane(i), W, H, p.smoothing), 0, 1), p)
        if specs[i][0] == "White underbase":                       # a solid, never screened
            t = np.where(plane(i) > 0.5, 1.0, 0.0).astype(np.float32)
        if p.render == "screen":
            d = mask_to_path(ht._screen(t, cell_px, ang, table, p.shape, stamp), tp, fast=p.draft)
            body = (f'<g transform="translate({ax:.3f} {ay:.3f}) scale({px_mm:.6f})">'
                    f'<path fill-rule="evenodd" d="{d}"/></g>') if d else ""
            sb, sn = solid_body(i)
            return body + sb, d.count("M") + sn
        d, n = runs_path(dither_mask(t, p.dither, i, serp))
        body = (f'<g transform="translate({ax:.3f} {ay:.3f}) scale({aw / W:.6f} {ah / H:.6f})">'
                f'<path d="{d}"/></g>') if d else ""
        sb, sn = solid_body(i)
        return body + sb, n + sn

    results = _parallel(one, len(specs)) if (p.render == "screen" and len(specs) > 1) else [one(i) for i in range(len(specs))]
    for (name, ink, ang), (body, n) in zip(specs, results):
        job.plates.append(Plate(name, ink, body))
        total += n
    info.update(dots=total, overprint=p.method != "one" and not p.dark, draft=bool(p.draft and p.render == "screen"))
    job.info = info
    return job
