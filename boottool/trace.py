"""Stage 2: turn a Prepared target into SVG text.

Backends are swappable. `builtin` needs only numpy/OpenCV/scikit-image.
`vtracer` wraps the VTracer package when it is installed.
"""
from __future__ import annotations

import os
import tempfile
from typing import Protocol

import cv2
import numpy as np
from skimage import measure

from .config import Params
from .preprocess import Prepared


class Tracer(Protocol):
    name: str

    def trace(self, prep: Prepared, p: Params) -> str: ...


# --------------------------------------------------------------------------- helpers

def _fmt(v: float, prec: int) -> str:
    s = f"{v:.{prec}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _hex(c) -> str:
    return "#%02x%02x%02x" % (int(c[0]), int(c[1]), int(c[2]))


def clean_mask(mask: np.ndarray, speckle: int) -> np.ndarray:
    """Remove tiny blobs, then fill tiny holes. Keeps contour extraction hierarchy-free."""
    m = mask.astype(np.uint8)
    _, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= speckle
    keep[0] = False
    m = keep[lab].astype(np.uint8)

    _, lab, stats, _ = cv2.connectedComponentsWithStats(1 - m, connectivity=4)
    small = stats[:, cv2.CC_STAT_AREA] < speckle
    small[0] = False
    m[small[lab]] = 1
    return m


def polygon_to_path(pts: np.ndarray, corner_deg: float, prec: int) -> str:
    """Closed polygon -> smooth cubic path (Catmull-Rom), keeping sharp corners sharp."""
    n = len(pts)
    if n < 3:
        return ""
    prev = np.roll(pts, 1, axis=0)
    nxt = np.roll(pts, -1, axis=0)
    v1, v2 = prev - pts, nxt - pts
    l1 = np.linalg.norm(v1, axis=1) + 1e-9
    l2 = np.linalg.norm(v2, axis=1) + 1e-9
    cosang = np.clip((v1 * v2).sum(axis=1) / (l1 * l2), -1, 1)
    corner = np.degrees(np.arccos(cosang)) < corner_deg

    def pt(a):
        return f"{_fmt(a[0], prec)} {_fmt(a[1], prec)}"

    # Tangent at each vertex points from the previous to the next vertex. Handle length is
    # a third of the SHORTER adjacent segment, so a handle can never overshoot past a
    # nearby corner (classic Catmull-Rom spikes) yet stays smooth on evenly spaced curves.
    tan = nxt - prev
    tan = tan / (np.linalg.norm(tan, axis=1, keepdims=True) + 1e-9)
    hlen = np.minimum(l1, l2) / 3.0
    hlen[corner] = 0.0

    parts = [f"M{pt(pts[0])}"]
    for i in range(n):
        j = (i + 1) % n
        p0, p3 = pts[i], pts[j]
        if hlen[i] == 0.0 and hlen[j] == 0.0:
            parts.append(f"L{pt(p3)}")
            continue
        c1 = p0 + tan[i] * hlen[i]
        c2 = p3 - tan[j] * hlen[j]
        parts.append(f"C{pt(c1)} {pt(c2)} {pt(p3)}")
    parts.append("Z")
    return "".join(parts)


def _fmt_array(a: np.ndarray, prec: int) -> np.ndarray:
    """Format many numbers like `_fmt`, formatting each distinct value only once."""
    k = 10 ** prec
    q = np.rint(a * k).astype(np.int64)
    u, inv = np.unique(q, return_inverse=True)
    names = np.array([_fmt(v / k, prec) for v in u.tolist()], dtype=object)
    return names[inv.reshape(-1)]


def polygons_to_path(polys: list, corner_deg: float, prec: int) -> str:
    """Many closed polygons -> one path `d`; same result as `polygon_to_path` per polygon,
    but the arithmetic and number formatting run once over all vertices together."""
    if not polys:
        return ""
    lens = np.array([len(q) for q in polys], dtype=np.int64)
    ends = np.cumsum(lens)
    starts = ends - lens
    pts = np.concatenate(polys).astype(np.float64)
    n = len(pts)
    st = np.repeat(starts, lens)
    ln = np.repeat(lens, lens)
    loc = np.arange(n) - st
    nx_i = st + (loc + 1) % ln
    pv_i = st + (loc - 1) % ln
    prev, nxt = pts[pv_i], pts[nx_i]
    v1, v2 = prev - pts, nxt - pts
    l1 = np.linalg.norm(v1, axis=1) + 1e-9
    l2 = np.linalg.norm(v2, axis=1) + 1e-9
    cosang = np.clip((v1 * v2).sum(axis=1) / (l1 * l2), -1, 1)
    corner = np.degrees(np.arccos(cosang)) < corner_deg
    tan = nxt - prev
    tan = tan / (np.linalg.norm(tan, axis=1, keepdims=True) + 1e-9)
    hlen = np.minimum(l1, l2) / 3.0
    hlen[corner] = 0.0
    c1 = pts + tan * hlen[:, None]
    p3 = pts[nx_i]
    c2 = p3 - (tan * hlen[:, None])[nx_i]
    straight = (hlen == 0.0) & (hlen[nx_i] == 0.0)

    X = _fmt_array(np.concatenate([pts[:, 0], c1[:, 0], c2[:, 0], p3[:, 0]]), prec)
    Y = _fmt_array(np.concatenate([pts[:, 1], c1[:, 1], c2[:, 1], p3[:, 1]]), prec)
    x0, xc1, xc2, xp = X[:n], X[n:2 * n], X[2 * n:3 * n], X[3 * n:]
    y0, yc1, yc2, yp = Y[:n], Y[n:2 * n], Y[2 * n:3 * n], Y[3 * n:]
    segs = [f"L{a} {b}" if s else f"C{c} {d} {e} {f} {a} {b}"
            for s, a, b, c, d, e, f in zip(straight.tolist(), xp, yp, xc1, yc1, xc2, yc2)]
    out = []
    for s0, e0 in zip(starts.tolist(), ends.tolist()):
        out.append(f"M{x0[s0]} {y0[s0]}" + "".join(segs[s0:e0]) + "Z")
    return "".join(out)


def _fast_polys(m: np.ndarray, epsilon: float, k: int = 3) -> list:
    """Quick, slightly coarser outlines (OpenCV, whole pixels at 2x) for draft previews."""
    up = cv2.resize(m, (m.shape[1] * k, m.shape[0] * k), interpolation=cv2.INTER_NEAREST)
    contours, _ = cv2.findContours(up, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    polys = []
    for c in contours:
        if len(c) < 3:
            continue
        simp = cv2.approxPolyDP(c.astype(np.float32), k * epsilon * 0.6, True).reshape(-1, 2)
        if len(simp) >= 3:
            polys.append((simp.astype(np.float32) + 0.5) / k)
    return polys


def mask_to_path(mask: np.ndarray, p: Params, fast: bool = False) -> str:
    """Boolean mask -> one SVG path `d` (use with fill-rule=evenodd). `fast` trades some
    outline accuracy for speed and is only meant for draft previews."""
    m = clean_mask(mask, p.speckle)
    if not m.any():
        return ""
    if fast:
        return polygons_to_path(_fast_polys(m, p.epsilon), p.corner_deg, p.precision)
    padded = np.pad(m.astype(np.float32), 1)
    if p.smooth_sigma > 0:
        padded = cv2.GaussianBlur(padded, (0, 0), p.smooth_sigma)
    polys = []
    for c in measure.find_contours(padded, 0.5):
        if len(c) > 2 and (c[0] == c[-1]).all():
            c = c[:-1]
        if len(c) < 3:
            continue
        # index space -> SVG space: undo 1px padding, shift to pixel-edge coordinates
        pts = np.stack([c[:, 1] - 1 + 0.5, c[:, 0] - 1 + 0.5], axis=1).astype(np.float32)
        simp = cv2.approxPolyDP(pts.reshape(-1, 1, 2), p.epsilon, True).reshape(-1, 2)
        if len(simp) < 3:
            continue
        x, y = simp[:, 0], simp[:, 1]
        area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        if area < 1.0:
            continue
        polys.append(simp)
    return polygons_to_path(polys, p.corner_deg, p.precision)


def _svg(w: int, h: int, body: list[str]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">' + "".join(body) + "</svg>"
    )


# --------------------------------------------------------------------------- backends

class BuiltinTracer:
    name = "builtin"

    def trace(self, prep: Prepared, p: Params) -> str:
        w, h = prep.size
        if prep.mode == "lineart":
            d = mask_to_path(prep.mask, p)
            body = [f'<path fill="{prep.ink}" fill-rule="evenodd" d="{d}"/>'] if d else []
            return _svg(w, h, body)

        # Colour: stacked layers. Largest colour is the background rect; each later
        # layer is traced from the union of itself and all smaller colours, so adjacent
        # regions overlap instead of leaving hairline gaps.
        k = len(prep.palette)
        areas = np.bincount(prep.labels.ravel(), minlength=k)
        order = list(np.argsort(-areas, kind="stable"))
        body = [f'<rect width="{w}" height="{h}" fill="{_hex(prep.palette[order[0]])}"/>']
        for i in range(1, len(order)):
            mask = np.isin(prep.labels, order[i:])
            d = mask_to_path(mask, p)
            if d:
                body.append(
                    f'<path fill="{_hex(prep.palette[order[i]])}" fill-rule="evenodd" d="{d}"/>'
                )
        return _svg(w, h, body)


class VTracerTracer:
    """Adapter for the `vtracer` package. Install with: pip install vtracer"""

    name = "vtracer"

    def trace(self, prep: Prepared, p: Params) -> str:
        try:
            import vtracer
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("backend 'vtracer' needs `pip install vtracer`") from e
        from PIL import Image

        if prep.mode == "lineart":
            img = np.where(prep.mask, 0, 255).astype(np.uint8)
            colormode = "binary"
        else:
            img = prep.palette[prep.labels]
            colormode = "color"
        with tempfile.TemporaryDirectory() as td:
            src, dst = os.path.join(td, "in.png"), os.path.join(td, "out.svg")
            Image.fromarray(img).save(src)
            vtracer.convert_image_to_svg_py(
                src, dst, colormode=colormode, filter_speckle=p.speckle,
                path_precision=p.precision,
            )
            with open(dst, encoding="utf-8") as f:
                return f.read()


_BACKENDS = {"builtin": BuiltinTracer, "vtracer": VTracerTracer}


def get_tracer(name: str) -> Tracer:
    try:
        return _BACKENDS[name]()
    except KeyError:
        raise ValueError(f"unknown backend {name!r}; choose from {sorted(_BACKENDS)}") from None
