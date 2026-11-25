"""Plates as PDF: one page per plate, pure black, at the page's real size.

Many print shops and film printers take PDF rather than SVG. A plate's film is already vector
shapes (page.py), so this writes those same shapes into a PDF: the artwork, the registration marks
and the plate's name, exactly where the SVG film has them. It is made from the SVG film itself, so
the two cannot drift apart, and it needs no extra library.

Only what the films are made of is understood (groups, paths, rectangles and one line of text, in
black). Anything else stops with an error rather than being left out of a film without a word.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
import zlib

from fontTools.pens.basePen import BasePen
from fontTools.svgLib.path import parse_path
from lxml import etree

from . import __version__
from .page import PT, Job, _film_group, slug

_BLACK = {"#000", "#000000", "black"}
_NARROW = 82                          # % of Helvetica's width: about as wide as the SVG's Arial Narrow


def _r(v: float) -> str:
    """A number as PDF wants it: plain decimals, never 1e-05."""
    v = float(v)
    if not math.isfinite(v):
        raise ValueError("A plate has a shape with a broken number in it; it cannot be written as PDF.")
    s = repr(round(v, 4))
    if "e" in s:
        s = f"{v:.4f}"
    s = s[:-2] if s.endswith(".0") else s
    return "0" if s == "-0" else s


def _m(v: float) -> str:
    """A number of a transform: kept finer, because it multiplies every point drawn with it."""
    if not math.isfinite(v):
        raise ValueError("A plate has a transform with a broken number in it; it cannot be written as PDF.")
    return f"{v:.8f}".rstrip("0").rstrip(".") or "0"


class _PathPen(BasePen):
    """Takes a path's moves, lines and curves and writes them as PDF path operators."""

    def __init__(self, out: list):
        super().__init__(None)
        self.out = out
        self.back = ""                 # the line that would lead back to where this outline began

    def _moveTo(self, p):
        at = f"{_r(p[0])} {_r(p[1])}"
        self.out.append(at + " m")
        self.back = at + " l"

    def _lineTo(self, p):
        self.out.append(f"{_r(p[0])} {_r(p[1])} l")

    def _curveToOne(self, a, b, c):
        self.out.append(f"{_r(a[0])} {_r(a[1])} {_r(b[0])} {_r(b[1])} {_r(c[0])} {_r(c[1])} c")

    def _closePath(self):
        if self.out[-1] == self.back:  # closing draws that line anyway (the path reader spells it out)
            self.out.pop()
        self.out.append("h")


def _matrix(text: str) -> list[str]:
    """An SVG transform as PDF "cm" lines (translate, scale and matrix: what the films use)."""
    out, rest = [], text.strip()
    while rest:
        m = re.match(r"(\w+)\s*\(([^)]*)\)\s*,?\s*", rest)
        if not m:
            raise ValueError(f"A plate has a transform that cannot be written as PDF: {text!r}")
        v = [float(x) for x in re.split(r"[\s,]+", m.group(2).strip()) if x]
        kind = m.group(1)
        if kind == "translate" and len(v) in (1, 2):
            mat = (1, 0, 0, 1, v[0], v[1] if len(v) == 2 else 0)
        elif kind == "scale" and len(v) in (1, 2):
            mat = (v[0], 0, 0, v[-1], 0, 0)
        elif kind == "matrix" and len(v) == 6:
            mat = tuple(v)
        else:
            raise ValueError(f"A plate has a transform that cannot be written as PDF: {text!r}")
        out.append(" ".join(_m(x) for x in mat) + " cm")
        rest = rest[m.end():]
    return out


def _paint(el, inherited: dict) -> dict:
    """Fill, outline and line width of an element: its own, or what its group gave it."""
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


def _draw(st: dict) -> list[str]:
    if st["fill"] and st["stroke"]:
        return [f"{_r(st['width'])} w", "B*" if st["evenodd"] else "B"]
    if st["stroke"]:
        return [f"{_r(st['width'])} w", "S"]
    return ["f*" if st["evenodd"] else "f"] if st["fill"] else ["n"]


def _pdf_text(s: str) -> str:
    """Text inside a PDF's ( ). The built-in fonts know Western letters only: anything else becomes "?"."""
    raw = s.encode("cp1252", errors="replace")
    return "".join("\\" + chr(b) if b in b"()\\" else chr(b) if 32 <= b <= 126 else f"\\{b:03o}" for b in raw)


def _walk(el, st: dict, out: list) -> None:
    tag = etree.QName(el).localname
    st = _paint(el, st)
    if tag == "g":
        out.append("q")
        out.extend(_matrix(el.get("transform") or ""))
        for child in el:
            _walk(child, st, out)
        out.append("Q")
    elif tag == "path":
        if el.get("transform"):
            raise ValueError("A plate has a path with its own transform; it cannot be written as PDF.")
        d = el.get("d") or ""
        if d.strip():
            parse_path(d, _PathPen(out))
            out.extend(_draw(st))
    elif tag == "rect":
        if el.get("transform") or el.get("rx") or el.get("ry"):
            raise ValueError("A plate has a rectangle that cannot be written as PDF.")
        x, y, w, h = (float(el.get(k) or 0) for k in ("x", "y", "width", "height"))
        out.append(f"{_r(x)} {_r(y)} {_r(w)} {_r(h)} re")
        out.extend(_draw(st))
    elif tag == "text":
        if len(el) or el.get("transform"):
            raise ValueError("A plate has text that cannot be written as PDF.")
        size, gap = float(el.get("font-size") or 4.2), float(el.get("letter-spacing") or 0)
        # the page is drawn with y running down, as in the SVG; letters are turned upright again here
        out.append(f"BT /F1 {_r(size)} Tf {_r(gap * 100 / _NARROW)} Tc {_NARROW} Tz "
                   f"1 0 0 -1 {_r(float(el.get('x') or 0))} {_r(float(el.get('y') or 0))} Tm ({_pdf_text(el.text or '')}) Tj ET")
    else:
        raise ValueError(f"A plate has something that cannot be written as PDF (<{tag}>).")


def _page_stream(job: Job, i: int) -> bytes:
    """Everything on plate i's film, as PDF drawing operators (in millimetres, y running down)."""
    pw, ph = job.page
    try:
        film = etree.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg">{_film_group(job, i, ident=False)}</svg>'.encode("utf-8"),
                                etree.XMLParser(huge_tree=True))
    except etree.XMLSyntaxError:       # in practice: a character in an ink's name that no file can hold (pasted in, not typed)
        raise ValueError(f'Plate {i + 1} could not be written as PDF. If its ink name has unusual characters in it, '
                         f'type the name again and try once more.') from None
    k = 1 / PT                                                             # points per millimetre
    out = ["q", f"{_m(k)} 0 0 {_m(-k)} 0 {_m(ph * k)} cm", "0 g", "0 G"]
    for el in film:
        _walk(el, {"fill": True, "stroke": False, "width": 1.0, "evenodd": False}, out)
    out.append("Q")
    return "\n".join(out).encode("ascii")


def _hex_text(s: str) -> str:
    return "<FEFF" + s.encode("utf-16-be").hex().upper() + ">"


def plates_pdf(job: Job, only: int | None = None, title: str = "") -> bytes:
    """One PDF with a page per plate (or just plate `only`), each page the film as it prints."""
    which = list(range(len(job.plates))) if only is None else [only]
    if not which:
        raise ValueError("Nothing to download: no plates were produced.")
    pw, ph = job.page
    box = f"[0 0 {_m(pw / PT)} {_m(ph / PT)}]"
    objs: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for i in which:
        data = zlib.compress(_page_stream(job, i), 6)
        kids.append(len(objs) + 1)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox {box} /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {len(objs) + 2} 0 R >>".encode("ascii"))
        objs.append(f"<< /Length {len(data)} /Filter /FlateDecode >>\nstream\n".encode("ascii") + data + b"\nendstream")
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Count {len(kids)} /Kids [{' '.join(f'{n} 0 R' for n in kids)}] >>".encode("ascii")
    name = title or " / ".join(job.plates[i].name for i in which)
    objs.append(f"<< /Title {_hex_text(name)} /Producer {_hex_text('Boot Tool ' + __version__)} >>".encode("ascii"))

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    at = []
    for n, body in enumerate(objs, 1):
        at.append(buf.tell())
        buf.write(f"{n} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref = buf.tell()
    buf.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for a in at:
        buf.write(f"{a:010d} 00000 n \n".encode("ascii"))
    buf.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return buf.getvalue()


def plates_pdf_zip(job: Job, base: str = "plate") -> bytes:
    """A .zip with one single-page PDF per plate, named like the SVGs in the SVG .zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:                # the PDFs are compressed already
        for i, p in enumerate(job.plates):
            z.writestr(f"{base}-{i + 1:02d}-{slug(p.name)}.pdf", plates_pdf(job, i))
    return buf.getvalue()
