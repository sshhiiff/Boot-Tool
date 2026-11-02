"""Batch convert: many images to SVG in a background job, each with its own settings.

Files go into one output folder (never overwriting what is already there) and are also kept in
a temporary folder so a .zip can be downloaded afterwards.
"""
from __future__ import annotations

import io
import os
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path

from . import library
from .pipeline import run_pipeline
from .simplify import svg_stats

MAX_ITEMS = 2000
_SAFE = re.compile(r"[^A-Za-z0-9 _.()+\-]")


def safe_stem(name: str) -> str:
    stem = _SAFE.sub("_", Path(str(name)).stem).strip(" .")
    return (stem or "image")[:120]


def resolve_out_dir(text: str | None) -> Path | None:
    """The output folder, created if needed. Must be a full path; '~' is allowed."""
    text = (text or "").strip()
    if not text:
        return None
    p = Path(os.path.expanduser(text))
    if not p.is_absolute():
        raise ValueError("Type the full folder path, for example /Users/you/Desktop/svg-out.")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Could not create that folder: {e.strerror or e}") from e
    if not p.is_dir() or not os.access(p, os.W_OK):
        raise ValueError("That folder cannot be written to.")
    return p


class Job:
    def __init__(self, root: Path, items: list[dict], out_dir: Path | None, keep_going: bool):
        self.id = uuid.uuid4().hex[:12]
        self.dir = root / f"batch-{self.id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.items = items
        self.out_dir = out_dir
        self.keep_going = keep_going
        self.stop = threading.Event()
        self.finished = False
        self.started = time.time()
        self.thread: threading.Thread | None = None

    # ------------------------------------------------------------------ worker
    def _save_out(self, fname: str, svg: str) -> str:
        """Write into the output folder without ever replacing an existing file."""
        stem, n = fname[:-4], 1
        while True:
            name = fname if n == 1 else f"{stem}-{n}.svg"
            try:
                with open(self.out_dir / name, "x", encoding="utf-8") as f:
                    f.write(svg)
                return name
            except FileExistsError:
                n += 1

    def run(self, files: dict, coerce) -> None:
        try:
            for it in self.items:
                if self.stop.is_set():
                    break
                it["state"] = "working"
                try:
                    path = files.get(it["id"])
                    if path is None:
                        raise KeyError("unknown image id; add the image again")
                    svg = run_pipeline(path, coerce(it["params"]), score=False).svg
                    (self.dir / it["fname"]).write_text(svg, encoding="utf-8")
                    try:
                        library.save(Path(it["fname"]).stem, svg)           # every result also goes into the library
                    except (OSError, ValueError):
                        pass
                    st = svg_stats(svg)
                    it.update(state="done", bytes=st["bytes"], path_count=st["path_count"], node_count=st["node_count"])
                    if self.out_dir is not None:
                        it["saved_as"] = self._save_out(it["fname"], svg)
                except Exception as e:  # noqa: BLE001 - one bad image must not end the batch
                    it.update(state="error", error=str(e).strip("'") or type(e).__name__)
                    if not self.keep_going:
                        self.stop.set()
            for it in self.items:
                if it["state"] in ("queued", "working"):
                    it["state"] = "stopped"
        finally:
            self.finished = True

    # ------------------------------------------------------------------ views
    def status(self) -> dict:
        items = [{k: it.get(k) for k in ("name", "fname", "state", "bytes", "path_count", "node_count",
                                         "error", "saved_as")} for it in self.items]
        counts = {s: sum(1 for i in items if i["state"] == s) for s in ("queued", "working", "done", "error", "stopped")}
        return {"job": self.id, "finished": self.finished, "out_dir": str(self.out_dir) if self.out_dir else None,
                "counts": counts, "items": items}

    def svg_path(self, i: int) -> Path | None:
        if not 0 <= i < len(self.items) or self.items[i]["state"] != "done":
            return None
        return self.dir / self.items[i]["fname"]

    def zip_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for it in self.items:
                if it["state"] == "done":
                    z.write(self.dir / it["fname"], it["fname"])
        return buf.getvalue()


def start(engine, req: dict, coerce) -> Job:
    raw = req.get("items")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Add some images first.")
    if len(raw) > MAX_ITEMS:
        raise ValueError(f"That is too many images at once (limit {MAX_ITEMS}).")
    out_dir = resolve_out_dir(req.get("out_dir"))
    used: set[str] = set()
    items = []
    for r in raw:
        stem, n = safe_stem(r.get("name", "image")), 1
        while True:
            fname = f"{stem}.svg" if n == 1 else f"{stem}-{n}.svg"
            if fname.lower() not in used:
                break
            n += 1
        used.add(fname.lower())
        items.append({"id": str(r.get("id", "")), "name": str(r.get("name", ""))[:200], "fname": fname,
                      "params": r.get("params") or {}, "state": "queued"})
    job = Job(engine.dir, items, out_dir, bool(req.get("keep_going", True)))
    engine.batches[job.id] = job
    while len(engine.batches) > 4:                         # keep only the latest few
        engine.batches.pop(next(iter(engine.batches)))
    job.thread = threading.Thread(target=job.run, args=(dict(engine.files), coerce), daemon=True)
    job.thread.start()
    return job
