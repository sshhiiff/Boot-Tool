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


def _curves(polys: list, corner_deg: float):
    """The curve through each polygon, as polygons_to_path draws it: for every vertex the segment to
    the next one as a cubic (start, two handles, end; `straight` where it is a plain line), and where
    each polygon starts and ends in those arrays."""
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
    return pts, c1, c2, p3, straight, starts, ends


def polygons_to_path(polys: list, corner_deg: float, prec: int) -> str:
    """Many closed polygons -> one path `d`; same result as `polygon_to_path` per polygon,
    but the arithmetic and number formatting run once over all vertices together."""
    if not polys:
        return ""
    pts, c1, c2, p3, straight, starts, ends = _curves(polys, corner_deg)
    n = len(pts)

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


def _area(q: np.ndarray) -> float:
    """A polygon's area, worked out as it always was here (in the precision the points come in)."""
    x, y = q[:, 0], q[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _true_area(q: np.ndarray) -> float:
    """A polygon's area, exactly: measured from its first point and in double precision. (Far from
    the picture's corner, _area's two large sums in single precision can round a small area to nothing.)"""
    return _area(q.astype(np.float64) - q[0].astype(np.float64))


def _crossing(inside: np.ndarray, outside: np.ndarray, w: int, h: int) -> tuple:
    """Where the line from a point inside the picture (w x h) to one outside leaves it: the point, and
    how far round the picture's sides it is (clockwise from the top left corner)."""
    (x0, y0), (dx, dy) = inside, outside - inside
    t, side = 1.0, -1
    for k, (beyond, far) in enumerate(((outside[1] < 0, -y0 / dy if dy else 1.0), (outside[0] > w, (w - x0) / dx if dx else 1.0),
                                       (outside[1] > h, (h - y0) / dy if dy else 1.0), (outside[0] < 0, -x0 / dx if dx else 1.0))):
        if beyond and far <= t:
            t, side = far, k
    x, y = min(max(x0 + t * dx, 0.0), w), min(max(y0 + t * dy, 0.0), h)
    if side == 0:
        return (x, 0.0), x
    if side == 1:
        return (float(w), y), w + y
    if side == 2:
        return (x, float(h)), w + h + (w - x)
    return (0.0, y), 2 * w + h + (h - y)


def _within(pts: np.ndarray, closed: bool, eps: float, w: int, h: int) -> list:
    """The parts of one outline that lie within the picture (w x h), each from the point where it
    comes in over a side to the point where it goes out again: (how far round the sides it starts,
    how far round it ends, its points, whether the whole outline was large enough to be drawn).
    `pts` is the outline as found; of its points within the picture, the ones kept are those that
    simplifying the whole outline keeps."""
    inside = (pts[:, 0] >= 0) & (pts[:, 0] <= w) & (pts[:, 1] >= 0) & (pts[:, 1] <= h)
    if not inside.any():
        return []
    pts = np.ascontiguousarray(pts, np.float32)
    simp = np.ascontiguousarray(cv2.approxPolyDP(pts.reshape(-1, 1, 2), eps, True).reshape(-1, 2), np.float32)
    kept = np.isin(pts.view(np.complex64).ravel(), simp.view(np.complex64).ravel())
    drawn = len(simp) >= 3 and _area(simp) >= 1.0
    out = np.flatnonzero(~inside)
    if closed:                                                               # start outside the picture
        pts, inside, kept = np.roll(pts, -out[0], axis=0), np.roll(inside, -out[0]), np.roll(kept, -out[0])
    elif inside[0] or inside[-1]:                                            # (an outline that just stops: not one of ours)
        return []
    step = np.diff(inside.astype(np.int8))
    first, after = np.flatnonzero(step == 1) + 1, np.flatnonzero(step == -1) + 1
    if len(after) < len(first):
        after = np.append(after, len(pts))
    parts, pts64 = [], pts.astype(np.float64)
    for a, b in zip(first.tolist(), after.tolist()):
        (start, t0), (end, t1) = _crossing(pts64[a], pts64[a - 1], w, h), _crossing(pts64[b - 1], pts64[b % len(pts)], w, h)
        run = [start] + [tuple(q) for q in pts64[a:b][kept[a:b]].tolist()] + [end]
        parts.append((t0, t1, [q for k, q in enumerate(run) if k == 0 or q != run[k - 1]], drawn))
    return parts


def _along_sides(t0: float, t1: float, w: int, h: int, whole: bool = False) -> list:
    """The way round the picture's sides (w x h), clockwise, from the point t0 round to the point t1
    (both measured as _crossing does; all the way round if `whole`), without those two points
    themselves: the corners passed, and beside every point on the way a twin a twentieth of a pixel
    along, so that the curve drawn through them is a straight line on each side, with sharp corners."""
    def at(t):
        t %= around
        if t <= w:
            return (t, 0.0)
        if t <= w + h:
            return (float(w), t - w)
        if t <= 2 * w + h:
            return (2 * w + h - t, float(h))
        return (0.0, around - t)
    around = 2.0 * (w + h)
    span = around if whole else (t1 - t0) % around
    stops = [t0] + sorted(t0 + d for d in ((c - t0) % around for c in (0.0, w, w + h, 2 * w + h)) if 0 < d < span) + [t0 + span]
    out = []
    for k, (a, b) in enumerate(zip(stops, stops[1:])):
        if b - a > 0.2:
            out += [at(a + 0.05), at(b - 0.05)]
        if k < len(stops) - 2:
            out.append(at(b))
    return out


def _joined_round_the_sides(parts: list, w: int, h: int) -> list:
    """Closed outlines from the parts of outlines within the picture (see _within): from where each
    part ends, on along the picture's sides (clockwise: what is filled lies to the right of an
    outline) to where the next one starts, until back at the first.

    Each is drawn if it is large enough (a pixel), as any outline; or if it is part of an outline
    that was large enough before it was cut off, however little of it lies within the picture."""
    order = sorted(range(len(parts)), key=lambda k: parts[k][0])
    starts = [parts[k][0] for k in order]
    used, rings = [False] * len(parts), []
    for k in range(len(parts)):
        ring, drawn = [], False
        while not used[k]:
            used[k] = True
            t0, t1, run, was = parts[k]
            nxt = order[int(np.searchsorted(starts, t1, side="left")) % len(order)]
            ring += run + _along_sides(t1, parts[nxt][0], w, h)
            k, drawn = nxt, drawn or was
        ring = np.array(ring, np.float64).reshape(-1, 2)
        if len(ring) >= 3 and _true_area(ring) >= (1e-4 if drawn else 1.0):
            rings.append(ring.astype(np.float32))
    return rings


def _curve_kept_in(ring: np.ndarray, corner_deg: float, w: int, h: int) -> np.ndarray:
    """A closed outline from _joined_round_the_sides has all its points within the picture (w x h),
    but the curve drawn through them (see _curves) leaves each point along a handle, and the handle
    of a point close to a side can reach over the side: the curve then bulges out of the picture.
    Such a point gets a twin a twentieth of a pixel away on either side, which makes its handles
    that short: the outline turns a corner there instead."""
    for _ in range(2):
        pts, c1, c2, _p3, _straight, _starts, _ends = _curves([ring], corner_deg)
        over = [(c[:, 0] < -0.02) | (c[:, 0] > w + 0.02) | (c[:, 1] < -0.02) | (c[:, 1] > h + 0.02) for c in (c1, c2)]
        bad = over[0] | np.roll(over[1], 1)                                  # (a point's handles: forward, and back from the one before)
        if not bad.any():
            break
        out = []
        for k in range(len(pts)):
            if not bad[k]:
                out.append(pts[k])
                continue
            for other, after in ((pts[k - 1], False), (pts[(k + 1) % len(pts)], True)):
                step = other - pts[k]
                far = float(np.hypot(*step))
                twin = [pts[k] + step * (0.05 / far)] if far > 0.11 else []
                out += twin if after else twin + [pts[k]]
        ring = np.array(out, np.float32)
    return ring


def mask_to_polys(mask: np.ndarray, p: Params, fast: bool = False, edge: bool = False) -> list:
    """Boolean mask -> the closed outlines that mask_to_path draws.

    `edge`: the mask is a picture cut off at its sides, and no outline goes beyond them. An outline
    that would (it ran half a pixel out, where it was closed by a straight line between its two
    ends: cutting across a corner block, and losing the outer edge of a shape that fills the picture
    or crosses it) is cut off exactly on the side and closed along the sides instead. Within the
    picture it is the same outline (but that its curve is kept from bulging over a side, see
    _curve_kept_in), and an outline that stays within the picture is not touched at all. (Such a
    one, if it runs very near a side or exactly on it, can still bulge over the side as it always
    could: by a few hundredths of a pixel as a rule, by up to about half a pixel with no smoothing
    at all.)"""
    m = clean_mask(mask, p.speckle)
    if not m.any():
        return []
    if fast:
        return _fast_polys(m, p.epsilon)
    padded = np.pad(m.astype(np.float32), 1)
    if p.smooth_sigma > 0:
        padded = cv2.GaussianBlur(padded, (0, 0), p.smooth_sigma)
    h, w = m.shape
    polys, parts = [], []
    for c in measure.find_contours(padded, 0.5):
        closed = len(c) > 2 and bool((c[0] == c[-1]).all())
        if closed:
            c = c[:-1]
        if len(c) < 3:
            continue
        # index space -> SVG space: undo 1px padding, shift to pixel-edge coordinates
        pts = np.stack([c[:, 1] - 1 + 0.5, c[:, 0] - 1 + 0.5], axis=1).astype(np.float32)
        if edge and not (closed and pts.min() >= 0 and pts[:, 0].max() <= w and pts[:, 1].max() <= h):
            parts += _within(pts, closed, p.epsilon, w, h)
            continue
        simp = cv2.approxPolyDP(pts.reshape(-1, 1, 2), p.epsilon, True).reshape(-1, 2)
        if len(simp) < 3 or _area(simp) < 1.0:
            continue
        polys.append(simp)
    if edge:
        polys += [_curve_kept_in(ring, p.corner_deg, w, h) for ring in _joined_round_the_sides(parts, w, h)]
        middle = w // 2 + 1                                                  # no outline over the sides at all, and the
        if not parts and padded[0, middle] + padded[1, middle] > 1.0:        # picture is filled there: filled up to its sides
            polys.append(np.array([(0.0, 0.0)] + _along_sides(0.0, 0.0, w, h, whole=True), np.float32))
    return polys


def mask_to_path(mask: np.ndarray, p: Params, fast: bool = False, edge: bool = False) -> str:
    """Boolean mask -> one SVG path `d` (use with fill-rule=evenodd). `fast` trades some
    outline accuracy for speed and is only meant for draft previews. `edge`: see mask_to_polys."""
    return polygons_to_path(mask_to_polys(mask, p, fast, edge), p.corner_deg, p.precision)


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
