"""The Print library: finished prints saved from the Print tab, kept in one fixed folder.

The folder is ~/BootTool/Prints (set BOOTTOOL_PRINTS to use another place, mainly for tests).
Each print is a transparent PNG picture of the design (in its ink colours) plus a small JSON file
with its real size in millimetres. Simulate puts these on a shirt.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9 _.()+\-]")
_lock = threading.Lock()
MAX_PNG = 40 * 1024 * 1024


def folder() -> Path:
    p = Path(os.environ.get("BOOTTOOL_PRINTS") or (Path.home() / "BootTool" / "Prints"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def clean_stem(name: str) -> str:
    stem = _SAFE.sub("_", Path(str(name)).stem).strip(" .")
    return (stem or "print")[:100]


def _unique(stem: str) -> str:
    d = folder()
    n = 1
    while True:
        s = stem if n == 1 else f"{stem}-{n}"
        if not (d / f"{s}.png").exists():
            return s
        n += 1


def save(name: str, png_b64: str, width_mm: float, height_mm: float, garment: str = "") -> str:
    data = base64.b64decode(png_b64.split(",")[-1], validate=False)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("That is not a PNG picture.")
    if len(data) > MAX_PNG:
        raise ValueError("That picture is too large to store.")
    if not (1 <= float(width_mm) <= 5000 and 1 <= float(height_mm) <= 5000):
        raise ValueError("The design size is not valid.")
    with _lock:
        stem = _unique(clean_stem(name))
        d = folder()
        (d / f"{stem}.png.tmp").write_bytes(data)
        (d / f"{stem}.png.tmp").replace(d / f"{stem}.png")
        meta = {"width_mm": round(float(width_mm), 2), "height_mm": round(float(height_mm), 2),
                "garment": garment if re.fullmatch(r"#[0-9a-fA-F]{6}", garment or "") else "", "created": int(time.time())}
        (d / f"{stem}.json").write_text(json.dumps(meta), encoding="utf-8")
        return f"{stem}.png"


def _path(fname: str) -> Path:
    if not fname or fname != Path(fname).name or not fname.lower().endswith(".png") or fname.startswith("."):
        raise ValueError("bad file name")
    return folder() / fname


def listing() -> list[dict]:
    out = []
    for f in folder().glob("*.png"):
        if f.name.startswith("."):
            continue
        meta = {}
        try:
            meta = json.loads(f.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out.append({"name": f.name, "width_mm": meta.get("width_mm", 0), "height_mm": meta.get("height_mm", 0),
                    "garment": meta.get("garment", ""), "mtime": int(f.stat().st_mtime)})
    out.sort(key=lambda x: -x["mtime"])
    return out


def read(fname: str) -> bytes:
    p = _path(fname)
    if not p.is_file():
        raise KeyError("no such print")
    return p.read_bytes()


def delete(fname: str) -> None:
    p = _path(fname)
    for f in (p, p.with_suffix(".json")):
        if f.is_file():
            f.unlink()
