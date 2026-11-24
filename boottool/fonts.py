"""Installed fonts, and text turned into outlines (so lettering is real vector shapes).

Fonts are read from the usual system folders plus ~/BootTool/Fonts (drop any .ttf/.otf there to
add your own). Outlines come from the font file itself, so text looks the same everywhere and
can be cut, edited and printed like any other shape.
"""
from __future__ import annotations

import hashlib
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
_BY_ID: dict[str, dict] = {}
_open: dict[tuple[str, int], tuple] = {} # (file, number in it) -> (the font, its id, its exact contents), once drawn with
GONE = "That font is not installed any more. Press Refresh fonts and pick another font for this text."


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


def _face(font: TTFont) -> str:
    """What tells one font from another of the same name (an older or newer version of it): the
    checksum and length of each of its tables, as its file lists them. Where the font sits in its
    file doesn't count, so copies of one font say the same."""
    try:
        return ";".join(f"{tag}:{e.checkSum}:{e.length}" for tag, e in sorted(font.reader.tables.items()))
    except Exception:
        return ""


def _fid(family: str, style: str, face: str = "") -> str:
    """A font's id. It is made from the font itself (its name, and what tells it from another version
    of the same name), so a font keeps its id whatever else is installed, after "Refresh fonts", and
    after a restart: text on the page stays in the font it was set in, and is never quietly drawn
    in another one."""
    return hashlib.sha1(f"{family}\0{style}\0{face}".encode("utf-8")).hexdigest()[:12]


def _scan() -> list[dict]:
    """Every font found, copies of one font once (with each file it is in). Of fonts with the same
    name (versions of one font) the first is the one shown in the list; the others are kept too, so
    text set in one of them still has its font."""
    found, ids, seen = [], {}, set()
    for root in _roots():
        if not root.is_dir():
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()                  # always the same order, so of two fonts with one name the same one is listed
            for fn in sorted(files):
                if Path(fn).suffix.lower() not in _EXT:
                    continue
                path = os.path.join(dirpath, fn)
                opened = None
                try:
                    if path.lower().endswith((".ttc", ".otc")):
                        opened = TTCollection(path, lazy=True)
                        fonts = list(enumerate(opened.fonts))
                    else:
                        opened = TTFont(path, lazy=True, fontNumber=0)
                        fonts = [(0, opened)]
                    for idx, f in fonts:
                        fam, sty = _names(f)
                        if not fam or "cmap" not in f:
                            continue
                        fid = _fid(fam, sty, _face(f))
                        if fid in ids:
                            ids[fid]["files"].append((path, idx))   # a copy (or, see _font, one that passes for it)
                            continue
                        ids[fid] = {"id": fid, "family": fam, "style": sty, "files": [(path, idx)],
                                    "shown": (fam, sty) not in seen}
                        found.append(ids[fid])
                        seen.add((fam, sty))
                except Exception:
                    continue
                finally:
                    if opened is not None:
                        try:
                            opened.close()       # (an open file can't be replaced or removed on Windows)
                        except Exception:
                            pass
    found.sort(key=lambda d: (d["family"].lower(), d["style"].lower()))
    return found


def _read() -> None:
    """The fonts as last scanned (lock held): scanned now if they never were."""
    global _FONTS
    if _FONTS is None:
        _FONTS = _scan()
        _open.clear()                    # a font file may have been replaced since it was opened
        _BY_ID.clear()
        _BY_ID.update((d["id"], d) for d in _FONTS)


def catalog(refresh: bool = False) -> dict:
    """The fonts for the list ("fonts"), and the installed fonts that are not in it because another
    font with the same name is, i.e. other versions of listed fonts ("others"; text set in one of
    these keeps it). Both are from one look at the font folders."""
    global _FONTS
    with _lock:
        if refresh:
            _FONTS = None
        _read()
        row = lambda d: {"id": d["id"], "family": d["family"], "style": d["style"]}   # noqa: E731
        return {"fonts": [row(d) for d in _FONTS if d["shown"]], "others": [row(d) for d in _FONTS if not d["shown"]]}


def listing(refresh: bool = False) -> list[dict]:
    return catalog(refresh)["fonts"]


def others() -> list[dict]:
    return catalog()["others"]


def _exact(font: TTFont) -> str:
    """The font's own contents, table by table: the same for copies of one font, different for anything
    else, however alike. How the font is packed doesn't count (where its tables sit in the file, and
    the checksum of the whole file, which a font inside a collection doesn't have)."""
    h = hashlib.sha1()
    for tag, e in sorted(font.reader.tables.items()):
        font.reader.file.seek(e.offset)
        data = font.reader.file.read(e.length)
        if tag == "head":
            data = data[:8] + bytes(4) + data[12:]       # head.checkSumAdjustment
        h.update(f"{tag}:{e.length}:".encode("utf-8"))
        h.update(data)
    return h.hexdigest()


def _font(fid: str, face: str | None = None) -> tuple[TTFont, str]:
    """The font with this id, and its exact contents (see _exact). `face`: the contents the text was
    last drawn from; then only that very font will do, so text is never drawn in a different font
    that passes for the same one."""
    _read()
    d = _BY_ID.get(fid)
    for path, index in (d["files"] if d else []):
        got = _open.get((path, index))
        if got is None:
            try:
                f = TTFont(path, fontNumber=index) if path.lower().endswith((".ttc", ".otc")) else TTFont(path)
                got = (f, _fid(*_names(f), _face(f)), _exact(f))
            except Exception:
                continue                 # its file was removed since the list was made
            _open[(path, index)] = got
        f, is_id, exact = got
        if is_id == fid and face in (None, exact):   # (not if its file was replaced by another font)
            return f, exact
    raise KeyError(GONE)


def outline(fid: str, text: str, size_mm: float, spacing: float = 0.0, align: str = "left",
            line_height: float = 1.2, face: str | None = None) -> dict:
    """Text -> SVG path data in millimetres, with its top-left corner at (0, 0).

    fid is the font's id from listing(); size_mm is the em size; spacing is extra space between
    letters in thousandths of an em. The answer's "face" says exactly which font it was drawn from;
    give it back as `face` when the same text is drawn again, and it is drawn from that very font
    or not at all."""
    text = str(text)[:2000]
    if not text.strip():
        raise ValueError("Type some text first.")
    size_mm = min(max(float(size_mm), 0.5), 2000.0)
    with _lock:
        font, exact = _font(str(fid), face)
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
    return {"d": pen.getCommands(), "w": round(wmax, 3), "h": round(h, 3), "face": exact}
