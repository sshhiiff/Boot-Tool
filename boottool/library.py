"""The library: every SVG the Convert tab makes is kept in one fixed folder on this computer.

The folder is ~/BootTool/Library (set BOOTTOOL_LIBRARY to use another place, mainly for tests).
Compose picks graphics from here. Files are plain SVGs, so Illustrator or Inkscape can open them.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9 _.()+\-]")
_lock = threading.Lock()
_keys: dict[str, str] = {}          # save key (one image in one session) -> file name


def folder() -> Path:
    p = Path(os.environ.get("BOOTTOOL_LIBRARY") or (Path.home() / "BootTool" / "Library"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def clean_stem(name: str) -> str:
    stem = _SAFE.sub("_", Path(str(name)).stem).strip(" .")
    return (stem or "graphic")[:100]


def _unique(stem: str) -> str:
    d = folder()
    n = 1
    while True:
        fname = f"{stem}.svg" if n == 1 else f"{stem}-{n}.svg"
        if not (d / fname).exists():
            return fname
        n += 1


def save(name: str, svg: str, key: str | None = None) -> str:
    """Write an SVG into the library and return its file name. The same key keeps overwriting
    the same file (so moving a slider updates one item instead of adding a new one each time)."""
    if "<svg" not in svg[:2000]:
        raise ValueError("That is not an SVG.")
    with _lock:
        d = folder()
        fname = _keys.get(key) if key else None
        if not fname or not (d / fname).exists():
            fname = _unique(clean_stem(name))
            if key:
                _keys[key] = fname
        tmp = d / (fname + ".tmp")
        tmp.write_text(svg, encoding="utf-8")
        tmp.replace(d / fname)
        return fname


def _path(fname: str) -> Path:
    if not fname or fname != Path(fname).name or not fname.lower().endswith(".svg") or fname.startswith("."):
        raise ValueError("bad file name")
    return folder() / fname


def listing() -> list[dict]:
    out = []
    for f in folder().glob("*.svg"):
        if f.name.startswith("."):
            continue
        st = f.stat()
        out.append({"name": f.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda x: -x["mtime"])
    return out


def read(fname: str) -> bytes:
    p = _path(fname)
    if not p.is_file():
        raise KeyError("no such library item")
    return p.read_bytes()


def rename(fname: str, new_stem: str) -> str:
    src = _path(fname)
    if not src.is_file():
        raise KeyError("no such library item")
    with _lock:
        stem = clean_stem(new_stem)
        target = f"{stem}.svg"
        if target != fname:
            target = _unique(stem)
            src.rename(folder() / target)
            for k, v in list(_keys.items()):
                if v == fname:
                    _keys[k] = target
        return target


def delete(fname: str) -> None:
    p = _path(fname)
    if p.is_file():
        p.unlink()
    for k, v in list(_keys.items()):
        if v == fname:
            del _keys[k]
