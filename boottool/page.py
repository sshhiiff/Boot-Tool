"""Shared page layout for plate-based output (halftone and separation).

Everything here is in millimetres. A Job holds the plates; compose() turns it into SVG for
the preview, and layered_svg() / plates_zip() produce the pure-black films for printing.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field, fields

PAGES = {"A5": (148, 210), "A4": (210, 297), "A3": (297, 420)}   # portrait mm
PT = 25.4 / 72                                                    # one point in mm
FONT = "'Arial Narrow','Helvetica Neue Condensed',Arial,sans-serif"


@dataclass(frozen=True)
class PageOpts:
    size: str = "A4"
    orientation: str = "landscape"      # portrait | landscape
    margin: float = 28.0                # mm kept clear around the artwork
    marks: bool = True
    mark_layout: str = "six"            # six | corners
    mark_size: float = 10.0             # mm, full width of one mark
    mark_weight_pt: float = 2.0
    ink_names: bool = True
    custom_w: float = 297.0             # mm, used when size == "Custom" (set by Compose)
    custom_h: float = 210.0
    art_w: float = 0.0                  # mm; 0 = fit the artwork to the page, otherwise print it exactly this wide

    def dims(self) -> tuple[float, float]:
        if self.size == "Custom":
            return float(self.custom_w), float(self.custom_h)
        w, h = PAGES[self.size]
        return (float(h), float(w)) if self.orientation == "landscape" else (float(w), float(h))


_BOUNDS = {"margin": (10, 80), "mark_size": (4, 20), "mark_weight_pt": (0.25, 6),
           "custom_w": (40, 2000), "custom_h": (40, 2000), "art_w": (0, 2000)}
_CHOICES = {"size": set(PAGES) | {"Custom"}, "orientation": {"portrait", "landscape"},
            "mark_layout": {"six", "corners"}}


def coerce_page(raw: dict) -> PageOpts:
    d = PageOpts()
    out = {}
    for f in fields(PageOpts):
        if f.name not in raw:
            continue
        v, dv = raw[f.name], getattr(d, f.name)
        if isinstance(dv, bool):
            out[f.name] = bool(v)
        elif isinstance(dv, float):
            lo, hi = _BOUNDS[f.name]
            out[f.name] = min(max(float(v), lo), hi)
        else:
            out[f.name] = str(v) if str(v) in _CHOICES[f.name] else dv
    return PageOpts(**out)


@dataclass
class Plate:
    name: str
    ink: str            # preview colour (#rrggbb)
    body: str           # SVG elements in page millimetres; they inherit fill from the group


@dataclass
class Job:
    opts: PageOpts
    art: tuple[float, float, float, float]      # x, y, w, h of the artwork on the page (mm)
    plates: list[Plate] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    @property
    def page(self) -> tuple[float, float]:
        return self.opts.dims()


def fit_artwork(opts: PageOpts, w_px: float, h_px: float) -> tuple[float, float, float, float]:
    """Centre the artwork in the page minus margins, keeping its proportions."""
    pw, ph = opts.dims()
    aw, ah = max(pw - 2 * opts.margin, 10), max(ph - 2 * opts.margin, 10)
    sc = min(aw / w_px, ah / h_px)
    if opts.art_w >= 5:
        sc = opts.art_w / w_px
    w, h = w_px * sc, h_px * sc
    return (pw - w) / 2, (ph - h) / 2, w, h


def _n(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def mark_positions(opts: PageOpts) -> list[tuple[float, float]]:
    pw, ph = opts.dims()
    xs = (14, pw / 2, pw - 14) if opts.mark_layout == "six" else (14, pw - 14)
    return [(x, y) for y in (12, ph - 12) for x in xs]


def marks_svg(opts: PageOpts) -> str:
    """Square with a cross through it, ticks extending past the square. Pure black."""
    if not opts.marks:
        return ""
    m = opts.mark_size
    sq = m * 0.45
    parts = []
    for cx, cy in mark_positions(opts):
        parts.append(
            f'<rect x="{_n(cx - sq / 2)}" y="{_n(cy - sq / 2)}" width="{_n(sq)}" height="{_n(sq)}"/>'
            f'<path d="M{_n(cx - m / 2)} {_n(cy)}H{_n(cx + m / 2)}M{_n(cx)} {_n(cy - m / 2)}V{_n(cy + m / 2)}"/>'
        )
    return (f'<g class="registration" fill="none" stroke="#000" '
            f'stroke-width="{_n(opts.mark_weight_pt * PT)}">' + "".join(parts) + "</g>")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def label_svg(opts: PageOpts, index: int, name: str) -> str:
    if not opts.ink_names:
        return ""
    x = 14 + (opts.mark_size / 2 + 3 if opts.marks else 0)
    return (f'<text x="{_n(x)}" y="13.4" fill="#000" font-size="4.2" letter-spacing=".3" '
            f'font-family="{FONT}">PLATE {index + 1} — {_esc(name.upper())}</text>')


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "plate"


def _svg(job: Job, inner: str, background, fill: str = "#fff") -> str:
    pw, ph = job.page
    bg = f'<rect width="{_n(pw)}" height="{_n(ph)}" fill="{fill}"/>' if background else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{_n(pw)}mm" height="{_n(ph)}mm" viewBox="0 0 {_n(pw)} {_n(ph)}">{bg}{inner}</svg>')


def compose(job: Job, view="all") -> str:
    """Preview SVG. 'all' stacks the plates in their ink colours; an integer shows that one
    plate as it will print (pure black, with marks and name)."""
    if view == "all":
        blend = ' style="mix-blend-mode:multiply"' if job.info.get("overprint") else ""   # preview only
        body = "".join(f'<g fill="{p.ink}"{blend}>{p.body}</g>' for p in job.plates)
        return _svg(job, body + marks_svg(job.opts), True, job.info.get("garment") or "#fff")
    if view == "design":
        ax, ay, aw, ah = job.art
        blend = ' style="mix-blend-mode:multiply"' if job.info.get("overprint") else ""
        body = "".join(f'<g fill="{p.ink}"{blend}>{p.body}</g>' for p in job.plates)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(aw)}mm" height="{_n(ah)}mm" '
                f'viewBox="{_n(ax)} {_n(ay)} {_n(aw)} {_n(ah)}">{body}</svg>')
    i = int(view)
    p = job.plates[i]
    return _svg(job, f'<g fill="#000">{p.body}</g>{marks_svg(job.opts)}{label_svg(job.opts, i, p.name)}', True)


def _film_group(job: Job, i: int, ident: bool = True) -> str:
    p = job.plates[i]
    gid = f' id="plate-{i + 1}-{slug(p.name)}" data-name="{_esc(p.name)}" inkscape:groupmode="layer" inkscape:label="{i + 1} {_esc(p.name)}"' if ident else ""
    return (f'<g{gid} fill="#000">{p.body}{marks_svg(job.opts)}{label_svg(job.opts, i, p.name)}</g>')


def _doc(job: Job, inner: str) -> str:
    pw, ph = job.page
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'width="{_n(pw)}mm" height="{_n(ph)}mm" viewBox="0 0 {_n(pw)} {_n(ph)}">{inner}</svg>')


def layered_svg(job: Job) -> str:
    """One file, one layer per plate, everything pure black, marks and name on each layer."""
    return _doc(job, "".join(_film_group(job, i) for i in range(len(job.plates))))


def plates_zip(job: Job, base: str = "plate") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, p in enumerate(job.plates):
            z.writestr(f"{base}-{i + 1:02d}-{slug(p.name)}.svg", _doc(job, _film_group(job, i, ident=False)))
    return buf.getvalue()
