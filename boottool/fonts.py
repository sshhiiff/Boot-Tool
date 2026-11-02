"""Installed fonts, and text turned into outlines (so lettering is real vector shapes).

Fonts are read from the usual system folders plus ~/BootTool/Fonts (drop any .ttf/.otf there to
add your own). Outlines come from the font file itself, so text looks the same everywhere and
can be cut, edited and printed like any other shape.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTCollection, TTFont

_EXT = {".ttf", ".otf", ".ttc", ".otc"}
_lock = threading.RLock()
_FONTS: list[dict] | None = None
_open: dict[tuple[str, int], TTFont] = {}


def user_folder() -> Path:
    p = Path(os.environ.get("BOOTTOOL_FONTS") or (Path.home() / "BootTool" / "Fonts"))
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def _roots() -> list[Path]:
    home = Path.home()
    if os.environ.get("BOOTTOOL_FONTS"):
        return [user_folder()]
    if sys.platform == "darwin":
        r = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library/Fonts"]
    elif sys.platform.startswith("win"):
        r = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
             Path(os.environ.get("LOCALAPPDATA", str(home))) / "Microsoft/Windows/Fonts"]
    else:
        r = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts", home / ".local/share/fonts"]
    return r + [user_folder()]


def _names(font: TTFont) -> tuple[str, str]:
    n = font["name"]
    def get(*ids):
        for i in ids:
            r = n.getName(i, 3, 1, 0x409) or n.getName(i, 1, 0, 0) or n.getName(i, 3, 1)
            if r:
                try:
                    return r.toUnicode()
                except Exception:
                    pass
        return ""
    return get(16, 1), get(17, 2) or "Regular"


def _scan() -> list[dict]:
    found, seen = [], set()
    for root in _roots():
        if not root.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if Path(fn).suffix.lower() not in _EXT:
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    if path.lower().endswith((".ttc", ".otc")):
                        coll = TTCollection(path, lazy=True)
                        fonts = list(enumerate(coll.fonts))
                    else:
                        fonts = [(0, TTFont(path, lazy=True, fontNumber=0))]
                    for idx, f in fonts:
                        fam, sty = _names(f)
                        if not fam or (fam, sty) in seen or "cmap" not in f:
                            continue
                        seen.add((fam, sty))
                        found.append({"family": fam, "style": sty, "path": path, "index": idx})
                except Exception:
                    continue
    found.sort(key=lambda d: (d["family"].lower(), d["style"].lower()))
    for i, d in enumerate(found):
        d["id"] = i
    return found


def listing(refresh: bool = False) -> list[dict]:
    global _FONTS
    with _lock:
        if _FONTS is None or refresh:
            _FONTS = _scan()
        return [{"id": d["id"], "family": d["family"], "style": d["style"]} for d in _FONTS]


def _font(fid: int) -> TTFont:
    listing()
    d = _FONTS[fid] if _FONTS and 0 <= fid < len(_FONTS) else None
    if d is None:
        raise KeyError("unknown font")
    key = (d["path"], d["index"])
    f = _open.get(key)
    if f is None:
        f = TTFont(d["path"], fontNumber=d["index"]) if d["path"].lower().endswith((".ttc", ".otc")) else TTFont(d["path"])
        _open[key] = f
    return f


def outline(fid: int, text: str, size_mm: float, spacing: float = 0.0, align: str = "left",
            line_height: float = 1.2) -> dict:
    """Text -> SVG path data in millimetres, with its top-left corner at (0, 0).

    size_mm is the em size; spacing is extra space between letters in thousandths of an em."""
    text = str(text)[:2000]
    if not text.strip():
        raise ValueError("Type some text first.")
    size_mm = min(max(float(size_mm), 0.5), 2000.0)
    with _lock:
        font = _font(int(fid))
        gs = font.getGlyphSet()
        cmap = font.getBestCmap() or {}
        upm = font["head"].unitsPerEm
        hmtx = font["hmtx"]
        hhea = font["hhea"]
        asc = hhea.ascent / upm * size_mm
        k = size_mm / upm
        lines = text.replace("\r", "").split("\n")
        widths, laid = [], []
        for line in lines:
            x, items = 0.0, []
            for ch in line:
                g = cmap.get(ord(ch)) or cmap.get(0xFFFD) or ".notdef"
                adv = hmtx[g][0] * k if g in hmtx.metrics else 0.5 * size_mm
                items.append((g, x))
                x += adv + spacing / 1000.0 * size_mm
            widths.append(max(x - spacing / 1000.0 * size_mm, 0.0))
            laid.append(items)
        wmax = max(widths) if widths else 0.0
        pen = SVGPathPen(gs, ntos=lambda v: ("%.3f" % v).rstrip("0").rstrip("."))
        for li, items in enumerate(laid):
            off = {"center": (wmax - widths[li]) / 2, "right": wmax - widths[li]}.get(align, 0.0)
            base = asc + li * size_mm * line_height
            for g, x in items:
                if g not in gs:
                    continue
                tp = TransformPen(pen, (k, 0, 0, -k, x + off, base))
                gs[g].draw(tp)
        h = asc + (len(lines) - 1) * size_mm * line_height - hhea.descent / upm * size_mm
    return {"d": pen.getCommands(), "w": round(wmax, 3), "h": round(h, 3)}
