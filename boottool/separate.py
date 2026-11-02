"""Spot-colour separation for screen printing: one flat plate per ink (up to 10)."""
from __future__ import annotations

import colorsys
from dataclasses import dataclass, fields

import cv2
import numpy as np

from .config import Params
from .page import Job, PageOpts, Plate, fit_artwork
from .preprocess import prepare
from .trace import mask_to_path

MAX_INKS = 10


@dataclass(frozen=True)
class SepParams:
    inks: int = 4
    paper: bool = True          # the paper colour is detected and never printed
    trap: float = 0.2           # mm that lighter colours spread under darker neighbours
    speckle: int = 8            # remove specks smaller than this many px
    smooth: float = 0.6         # edge smoothing
    colors: tuple = ()          # optional hex per plate, in print order ("" = automatic)
    names: tuple = ()           # optional name per plate ("" = automatic)
    fixed: bool = False         # colours are exactly the inks to use (picked with the eyedropper), in print order


_BOUNDS = {"inks": (1, MAX_INKS), "trap": (0.0, 1.0), "speckle": (0, 100), "smooth": (0.0, 2.0)}


def _is_hex(s: str) -> bool:
    return len(s) == 7 and s[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in s[1:])


def coerce_sep(raw: dict) -> SepParams:
    d = SepParams()
    out = {}
    for f in fields(SepParams):
        if f.name not in raw:
            continue
        v, dv = raw[f.name], getattr(d, f.name)
        if f.name == "colors":
            out["colors"] = tuple(str(x) if _is_hex(str(x)) else "" for x in list(v)[:MAX_INKS])
        elif f.name == "names":
            out["names"] = tuple(str(x)[:30] for x in list(v)[:MAX_INKS])
        elif isinstance(dv, bool):
            out[f.name] = bool(v)
        else:
            lo, hi = _BOUNDS[f.name]
            out[f.name] = type(dv)(min(max(float(v), lo), hi))
    return SepParams(**out)


def color_name(rgb) -> str:
    r, g, b = (int(c) / 255 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h *= 360
    if v < 0.22:
        return "Black"
    if s < 0.15:
        return "White" if v > 0.85 else "Grey"
    if h < 15 or h >= 345:
        return "Red"
    if h < 40:
        return "Brown" if v < 0.6 else "Orange"
    if h < 70:
        return "Yellow"
    if h < 165:
        return "Green"
    if h < 200:
        return "Cyan"
    if h < 255:
        return "Blue"
    if h < 290:
        return "Purple"
    return "Pink"


def _fixed_labels(path, colors, want_paper: bool, p: Params):
    """Every pixel goes to the nearest of the given inks (and the paper colour, if detected)."""
    from .preprocess import load_rgb
    rgb = load_rgb(path)
    h, w = rgb.shape[:2]
    longer = max(h, w)
    scale = p.min_side / longer if longer < p.min_side else (p.max_side / longer if longer > p.max_side else 1.0)
    if scale != 1.0:
        rgb = cv2.resize(rgb, (max(1, round(w * scale)), max(1, round(h * scale))),
                         interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    lab = cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    cols = np.array([[int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)] for c in colors], np.uint8)
    cen = cv2.cvtColor(cols[None].astype(np.float32) / 255, cv2.COLOR_RGB2Lab)[0]
    pal = [c for c in cols]
    paper = None
    if want_paper:
        border = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
        med = np.median(border, axis=0)
        paper_rgb = np.clip(cv2.cvtColor(med[None, None].astype(np.float32), cv2.COLOR_Lab2RGB)[0, 0] * 255, 0, 255).astype(np.uint8)
        cen = np.vstack([cen, med]); pal.append(paper_rgb); paper = len(pal) - 1
    d = ((lab[:, :, None, :] - cen[None, None]) ** 2).sum(3)
    return np.argmin(d, axis=2).astype(np.int32), np.array(pal, np.uint8), paper


def build(path, p: SepParams, page: PageOpts) -> Job:
    k = p.inks + (1 if p.paper else 0)
    tp = Params(mode="color", colors=k, min_side=1500, max_side=3000, speckle=p.speckle,
                smooth_sigma=p.smooth, epsilon=0.75)
    fixed = [c for c in p.colors if c] if p.fixed else []
    if fixed:
        lab, pal, paper = _fixed_labels(path, fixed, p.paper, tp)
        h, w = lab.shape
        n = len(pal)
        order = list(range(len(fixed)))                              # the picked order is the print order
    else:
        prep = prepare(path, tp)
        lab, pal = prep.labels, prep.palette
        w, h = prep.size
        n = len(pal)
        paper = None
        if p.paper and n > 1:
            border = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
            paper = int(np.argmax(np.bincount(border, minlength=n)))
        lum = pal.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        order = [int(i) for i in np.argsort(-lum) if i != paper]       # light to dark = print order

    art = fit_artwork(page, w, h)
    ox, oy, aw, _ = art
    sc = aw / w                                                     # mm per working pixel
    trap_px = 0 if p.trap <= 0 else max(1, int(round(p.trap / sc)))
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * trap_px + 1, 2 * trap_px + 1)) if trap_px else None

    job = Job(opts=page, art=art)
    seen: dict[str, int] = {}
    palette_info = []
    for idx, lbl in enumerate(order):
        mask = (lab == lbl)
        if kern is not None and idx < len(order) - 1:
            later = np.isin(lab, order[idx + 1:])
            grown = cv2.dilate(mask.astype(np.uint8), kern).astype(bool)
            mask = mask | (grown & later)                           # spread only under darker colours
        auto_hex = "#%02x%02x%02x" % tuple(int(c) for c in pal[lbl])
        hex_ = p.colors[idx] if idx < len(p.colors) and p.colors[idx] else auto_hex
        base = color_name(pal[lbl])
        seen[base] = seen.get(base, 0) + 1
        auto_name = base if seen[base] == 1 else f"{base} {seen[base]}"
        name = p.names[idx] if idx < len(p.names) and p.names[idx] else auto_name
        palette_info.append({"hex": hex_, "auto_hex": auto_hex, "name": name, "auto_name": auto_name})
        d = mask_to_path(mask, tp)
        if not d:
            continue
        body = (f'<g transform="translate({ox:.3f} {oy:.3f}) scale({sc:.6f})">'
                f'<path fill-rule="evenodd" d="{d}"/></g>')
        job.plates.append(Plate(name, hex_, body))
    job.info = {
        "palette": palette_info,
        "paper": ("#%02x%02x%02x" % tuple(int(c) for c in pal[paper])) if paper is not None else None,
        "trap_px": trap_px,
    }
    return job
