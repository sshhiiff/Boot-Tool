"""Stage 3: backend-agnostic SVG tidy-up and statistics."""
from __future__ import annotations

import re
from collections import Counter

from lxml import etree

from .config import Params

_DEC = re.compile(r"-?\d+\.\d+")
_HEX = re.compile(r"^#([0-9a-f]{6})$")
_CMD = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]")


def fmt_num(v: float, prec: int) -> str:
    s = f"{v:.{prec}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _merge_map(fills: list[str], tol: int) -> dict[str, str]:
    """Greedy merge: most-used colours become representatives, near neighbours map onto them."""
    reps: list[tuple[tuple[int, int, int], str]] = []
    mapping: dict[str, str] = {}
    for color, _ in Counter(fills).most_common():
        m = _HEX.match(color)
        if not m:
            continue
        rgb = tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
        for r_rgb, r_hex in reps:
            if sum((a - b) ** 2 for a, b in zip(rgb, r_rgb)) ** 0.5 <= tol:
                mapping[color] = r_hex
                break
        else:
            reps.append((rgb, color))
            mapping[color] = color
    return mapping


def postprocess(svg_text: str, p: Params, display: tuple[int, int] | None = None) -> str:
    parser = etree.XMLParser(remove_comments=True, remove_blank_text=True)
    root = etree.fromstring(svg_text.encode("utf-8"), parser)
    if display:                      # keep the viewBox, change only the declared size
        root.set("width", str(display[0]))
        root.set("height", str(display[1]))
    elems = [el for el in root.iter() if isinstance(el.tag, str)]

    fills = [el.get("fill").lower() for el in elems if el.get("fill")]
    mapping = _merge_map(fills, p.merge_tol)
    for el in elems:
        f = el.get("fill")
        if f and f.lower() in mapping:
            el.set("fill", mapping[f.lower()])
        if el.tag.split("}")[-1] == "path" and el.get("d"):
            el.set("d", _DEC.sub(lambda m: fmt_num(float(m.group()), p.precision), el.get("d")))
    return etree.tostring(root, encoding="unicode")


def svg_stats(svg_text: str) -> dict:
    root = etree.fromstring(svg_text.encode("utf-8"))
    paths = [el for el in root.iter() if isinstance(el.tag, str) and el.tag.split("}")[-1] == "path"]
    nodes = sum(len(_CMD.findall(el.get("d", ""))) for el in paths)
    fills = {el.get("fill") for el in root.iter() if isinstance(el.tag, str) and el.get("fill")}
    return {
        "path_count": len(paths),
        "node_count": nodes,
        "color_count": len(fills),
        "bytes": len(svg_text.encode("utf-8")),
    }
