"""Plates as pictures: one black-and-white PNG or TIFF per plate, at a chosen resolution.

Some film printers and their software want a picture rather than a drawing. Each picture is the
plate's film as the SVG has it (page.py): the artwork, the registration marks and the plate's name,
drawn dot for dot. One bit per dot, black or white, nothing in between, with the resolution written
into the file so that it prints at its real size.

The film is drawn here with numpy and Pillow, which Boot Tool already uses: no SVG program is
needed. A dot is black when its centre lies inside a shape, so shapes come out neither fatter nor
thinner than they are (halftone dots keep their tone). Only what films are made of is understood (groups, paths, rectangles and one line of
text, in black). Anything else stops with an error rather than being left out of a film.
"""
from __future__ import annotations

import io
import math
import re
import struct
import unicodedata
import zipfile

import numpy as np
from fontTools.pens.basePen import BasePen
from fontTools.svgLib.path import parse_path
from lxml import etree
from PIL import Image, ImageDraw, ImageFont

from .page import Job, _film_group, slug

DPI = (300, 600, 1200)
MAX_DOTS = 450_000_000          # A3 at 1200 dpi is 278 million dots; much more than that is no longer one picture
_BLACK = {"#000", "#000000", "black"}
_BAND = 256                     # rows drawn at a time, to keep memory small
_FLAT = 0.02                    # curves are cut into straight pieces that stay within a fiftieth of a dot (small dots keep their area)


class _Outlines(BasePen):
    """A path's outlines as straight-sided ones: [(points, closed)]. `scale` is dots per unit of the path."""

    def __init__(self, scale: float):
        super().__init__(None)
        self.scale, self.subs, self.cur = scale, [], None

    def _moveTo(self, p):
        self.done(False)
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, a, b, c):
        p = self.cur[-1]
        bend = max(math.hypot(p[0] - 2 * a[0] + b[0], p[1] - 2 * a[1] + b[1]),
                   math.hypot(a[0] - 2 * b[0] + c[0], a[1] - 2 * b[1] + c[1])) * self.scale
        n = min(400, max(1, math.ceil(math.sqrt(0.75 * bend / _FLAT))))
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            k0, k1, k2, k3 = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
            self.cur.append((k0 * p[0] + k1 * a[0] + k2 * b[0] + k3 * c[0], k0 * p[1] + k1 * a[1] + k2 * b[1] + k3 * c[1]))

    def _closePath(self):
        self.done(True)

    def _endPath(self):
        self.done(False)

    def done(self, closed: bool):
        if self.cur and len(self.cur) > 1:
            self.subs.append((self.cur, closed))
        self.cur = None


def _times(m, n):
    """Transform m, then n (each a, b, c, d, e, f as SVG writes them)."""
    return (m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3], m[2] * n[0] + m[3] * n[2], m[2] * n[1] + m[3] * n[3],
            m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5])


def _matrix(text: str, m):
    rest = text.strip()
    while rest:
        got = re.match(r"(\w+)\s*\(([^)]*)\)\s*,?\s*", rest)
        if not got:
            raise ValueError(f"A plate has a transform that cannot be drawn as a picture: {text!r}")
        v = [float(x) for x in re.split(r"[\s,]+", got.group(2).strip()) if x]
        kind = got.group(1)
        if kind == "translate" and len(v) in (1, 2):
            step = (1, 0, 0, 1, v[0], v[1] if len(v) == 2 else 0)
        elif kind == "scale" and len(v) in (1, 2):
            step = (v[0], 0, 0, v[-1], 0, 0)
        elif kind == "matrix" and len(v) == 6:
            step = tuple(v)
        else:
            raise ValueError(f"A plate has a transform that cannot be drawn as a picture: {text!r}")
        m = _times(step, m)
        rest = rest[got.end():]
    return m


def _placed(points, m) -> np.ndarray:
    """Points of a path, where they come to lie in the picture (in dots)."""
    p = np.asarray(points, np.float64)
    return np.stack([p[:, 0] * m[0] + p[:, 1] * m[2] + m[4], p[:, 0] * m[1] + p[:, 1] * m[3] + m[5]], axis=1)


def _paint(el, inherited: dict) -> dict:
    st = dict(inherited)
    for k in ("fill", "stroke"):
        v = el.get(k)
        if v is not None:
            if v != "none" and v.lower() not in _BLACK:
                raise ValueError(f"A plate has a colour other than black ({v!r}); films are pure black.")
            st[k] = v != "none"
    if el.get("stroke-width") is not None:
        st["width"] = float(el.get("stroke-width"))
    if el.get("fill-rule") is not None:
        st["evenodd"] = el.get("fill-rule") == "evenodd"
    return st


def _lines(subs, half: float) -> list[np.ndarray]:
    """The outlines of straight lines `2 * half` dots thick, cut off square where they end (as SVG draws them)."""
    out = []
    for pts, closed in subs:
        ring = np.vstack([pts, pts[:1]]) if closed else pts
        for a, b in zip(ring[:-1], ring[1:]):
            d = b - a
            n = math.hypot(d[0], d[1])
            if n == 0:
                continue
            side = np.array([-d[1], d[0]]) / n * half
            out.append(np.array([a + side, b + side, b - side, a - side]))
    return out


def _walk(el, m, st: dict, shapes: list, texts: list) -> None:
    """Collect what is to be drawn: shapes as (rule, outlines in dots), texts as (x, y, size, gap, words)."""
    tag = etree.QName(el).localname
    st = _paint(el, st)
    scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))                       # dots per unit here
    if tag == "g":
        m = _matrix(el.get("transform") or "", m)
        for child in el:
            _walk(child, m, st, shapes, texts)
    elif tag == "path":
        if el.get("transform"):
            raise ValueError("A plate has a path with its own transform; it cannot be drawn as a picture.")
        pen = _Outlines(scale)
        parse_path(el.get("d") or "", pen)
        pen.done(False)
        subs = [(_placed(pts, m), closed) for pts, closed in pen.subs]
        if st["fill"] and subs:
            shapes.append(("evenodd" if st["evenodd"] else "nonzero", [p for p, _ in subs]))
        if st["stroke"] and subs:
            shapes.append(("nonzero", _lines(subs, st["width"] * scale / 2)))
    elif tag == "rect":
        if el.get("transform") or el.get("rx") or el.get("ry") or abs(m[1]) > 1e-12 or abs(m[2]) > 1e-12:
            raise ValueError("A plate has a rectangle that cannot be drawn as a picture.")
        x, y, w, h = (float(el.get(k) or 0) for k in ("x", "y", "width", "height"))

        def box(grow):
            return _placed([(x - grow, y - grow), (x + w + grow, y - grow), (x + w + grow, y + h + grow), (x - grow, y + h + grow)], m)
        if st["fill"]:
            shapes.append(("nonzero", [box(st["width"] / 2 if st["stroke"] else 0)]))
        elif st["stroke"]:                                                 # a frame: the outside of its line, less the inside
            shapes.append(("evenodd", [box(st["width"] / 2), box(-st["width"] / 2)]))
    elif tag == "text":
        if len(el) or el.get("transform"):
            raise ValueError("A plate has text that cannot be drawn as a picture.")
        x, y = _placed([(float(el.get("x") or 0), float(el.get("y") or 0))], m)[0]
        texts.append((x, y, float(el.get("font-size") or 4.2) * scale, float(el.get("letter-spacing") or 0) * scale, el.text or ""))
    else:
        raise ValueError(f"A plate has something that cannot be drawn as a picture (<{tag}>).")


def _edges(outlines: list) -> tuple:
    """The sides of a shape's outlines that cross rows of dots: for each, where it starts (x, y), how far x
    moves per row, the first and last row whose dot centres it passes, and whether it runs down or up."""
    n = np.array([len(c) for c in outlines])
    pts = np.vstack(outlines)
    start = np.cumsum(n) - n
    nxt = np.arange(len(pts)) + 1
    nxt[start + n - 1] = start                                              # every outline is closed
    a, b = pts, pts[nxt]
    keep = a[:, 1] != b[:, 1]
    a, b = a[keep], b[keep]
    down = b[:, 1] > a[:, 1]
    hi, lo = np.where(down[:, None], a, b), np.where(down[:, None], b, a)   # upper and lower end
    first = np.ceil(hi[:, 1] - 0.5).astype(np.int64)                        # a dot's centre is at row + 0.5
    last = np.ceil(lo[:, 1] - 0.5).astype(np.int64) - 1
    ok = last >= first
    return (hi[ok, 0], hi[ok, 1], ((lo[:, 0] - hi[:, 0]) / (lo[:, 1] - hi[:, 1]))[ok], first[ok], last[ok], down[ok])


def _fill(band: np.ndarray, top: int, rule: str, edges: tuple) -> None:
    """Blacken the dots of `band` (rows of the picture from row `top`) whose centres lie inside the shape.
    Along each row, every side of the shape that is crossed is counted; a dot is inside by the count so far."""
    x0, y0, slope, first, last, down = edges
    h, W = band.shape
    lo, hi = np.maximum(first, top), np.minimum(last, top + h - 1)
    sel = np.nonzero(hi >= lo)[0]
    if not len(sel):
        return
    n = (hi - lo + 1)[sel]
    idx = np.repeat(sel, n)
    rows = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n) + np.repeat(lo[sel], n)
    x = x0[idx] + (rows + 0.5 - y0[idx]) * slope[idx]
    col = np.clip(np.ceil(x - 0.5), 0, W).astype(np.int64)                  # the first dot whose centre is past the crossing
    at = (rows - top) * (W + 1) + col
    size = h * (W + 1)
    if rule == "evenodd":                                                  # inside after an odd number of crossings
        inside = np.cumsum(np.bincount(at, minlength=size).astype(np.int32).reshape(h, W + 1), axis=1)[:, :W] & 1
    else:                                                                  # inside while downward and upward crossings do not cancel
        d = down[idx]
        turns = np.bincount(at[d], minlength=size).astype(np.int32) - np.bincount(at[~d], minlength=size).astype(np.int32)
        inside = np.cumsum(turns.reshape(h, W + 1), axis=1)[:, :W]
    band[inside != 0] = 1


def _drawn(font, ch: str) -> bytes:
    """What the font draws for a letter (to tell the letters it has from those it has not)."""
    im = Image.new("1", (int(font.size * 2) + 4, int(font.size * 2) + 4), 1)
    ImageDraw.Draw(im).text((2, int(font.size * 1.5)), ch, font=font, fill=0, anchor="ls")
    return im.tobytes()


def check(fmt: str, dpi) -> None:
    """Say so at once if this kind of picture or resolution is not on offer (before any plate is made)."""
    if fmt not in ("png", "tiff"):
        raise ValueError("Films as pictures are made as PNG or TIFF.")
    if type(dpi) is not int or dpi not in DPI:
        raise ValueError(f"Choose a resolution of {', '.join(str(d) for d in DPI[:-1])} or {DPI[-1]} dpi.")


def plate_picture(job: Job, i: int, dpi: int) -> Image.Image:
    """Plate i's film as a picture of black and white dots, `dpi` to the inch."""
    check("png", dpi)
    pw, ph = job.page
    k = dpi / 25.4                                                          # dots per millimetre
    W, H = max(1, round(pw * k)), max(1, round(ph * k))
    if W * H > MAX_DOTS:
        raise ValueError(f"At {dpi} dpi this page would be a picture of {W * H // 1_000_000} million dots, which is too big. "
                         f"Choose a lower resolution.")
    try:
        film = etree.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg">{_film_group(job, i, ident=False)}</svg>'.encode("utf-8"),
                                etree.XMLParser(huge_tree=True))
    except etree.XMLSyntaxError:
        raise ValueError(f"Plate {i + 1} could not be drawn as a picture. If its ink name has unusual characters in it, "
                         f"type the name again and try once more.") from None
    shapes, texts = [], []
    for el in film:
        _walk(el, (k, 0, 0, k, 0, 0), {"fill": True, "stroke": False, "width": 1.0, "evenodd": False}, shapes, texts)
    ready = []
    for rule, outlines in shapes:
        if any(not np.isfinite(c).all() for c in outlines):
            raise ValueError("A plate has a shape with a broken number in it; it cannot be drawn as a picture.")
        ready.append((rule, _edges(outlines)))

    rows = bytearray()
    for top in range(0, H, _BAND):
        band = np.zeros((min(_BAND, H - top), W), np.uint8)
        for rule, edges in ready:
            _fill(band, top, rule, edges)
        rows += np.packbits(band ^ 1, axis=1).tobytes()                    # in the file a set bit is white
    im = Image.frombytes("1", (W, H), bytes(rows))
    draw = ImageDraw.Draw(im)
    for x, y, size, gap, words in texts:                                   # the plate's name, letter by letter (they stand a little apart)
        font = ImageFont.load_default(size=max(4, round(size)))            # Pillow's own font: the same on every computer
        for ch in words.replace("—", "-"):                                 # (it has no long dash)
            if not ch.isspace() and _drawn(font, ch) == _drawn(font, "\uffff"):   # nor accents or other alphabets: a box would be drawn
                plain = unicodedata.normalize("NFKD", ch)[0]               # the letter without its accent, where there is one
                ch = plain if plain.isascii() and not plain.isspace() else "?"
            draw.text((x, y), ch, font=font, fill=0, anchor="ls")
            x += font.getlength(ch) + gap
    return im


def _file(im: Image.Image, fmt: str, dpi: int) -> bytes:
    buf = io.BytesIO()
    if fmt == "png":
        im.save(buf, "PNG", dpi=(dpi, dpi))
    else:
        return _tiff(im, dpi)
    return buf.getvalue()


def _tiff(im: Image.Image, dpi: int) -> bytes:
    """A fax-type (group 4) TIFF in which "white is 0", as such files are by custom: some film programs read them
    that way whatever the file says. Pillow can write that itself, but turns the picture over one dot at a time
    (twenty seconds for an A3 film at 1200 dpi). So the picture is turned over here, at once, saved the plain
    way, and the one number in the file that says which way round it is, is set."""
    buf = io.BytesIO()
    over = Image.frombytes("1", im.size, np.bitwise_not(np.frombuffer(im.tobytes(), np.uint8)).tobytes())   # every bit, as it is stored
    over.save(buf, "TIFF", compression="group4", dpi=(dpi, dpi))
    data = bytearray(buf.getvalue())
    order = "<" if data[:2] == b"II" else ">"
    if struct.unpack_from(order + "H", data, 2)[0] != 42:
        raise ValueError("The TIFF could not be written the way film programs expect it.")
    at, = struct.unpack_from(order + "I", data, 4)                          # where the file's list of tags is
    count, = struct.unpack_from(order + "H", data, at)
    for i in range(count):
        entry = at + 2 + 12 * i
        tag, kind, n, value = struct.unpack_from(order + "HHIH", data, entry)
        if tag == 262 and kind == 3 and n == 1 and value == 1:              # PhotometricInterpretation: "black is 0"
            struct.pack_into(order + "H", data, entry + 8, 0)               # -> "white is 0"
            return bytes(data)
    raise ValueError("The TIFF could not be written the way film programs expect it.")


def films_zip(job: Job, fmt: str = "png", dpi: int = 600, base: str = "plate") -> bytes:
    """A .zip with one picture per plate (PNG or TIFF), named like the SVGs in the SVG .zip."""
    check(fmt, dpi)
    if not job.plates:
        raise ValueError("Nothing to download: no plates were produced.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:                # the pictures are packed tightly already
        for i, p in enumerate(job.plates):
            z.writestr(f"{base}-{i + 1:02d}-{slug(p.name)}.{'png' if fmt == 'png' else 'tif'}", _file(plate_picture(job, i, dpi), fmt, dpi))
    return buf.getvalue()
