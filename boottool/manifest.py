"""Provenance manifest: one JSON line per processed image."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Manifest:
    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def last_saved(self) -> dict[str, tuple[str, str]]:
        """For every SVG written so far: the (sha256, params_key) of the picture whose trace was saved
        there last. Names are folded to lower case, as they are told apart ignoring case."""
        out: dict[str, tuple[str, str]] = {}
        for r in self.load():
            if r.get("status") in ("ok", "low_score") and r.get("sha256"):
                svg = r.get("svg") or "svg/" + Path(r["source"]).with_suffix(".svg").as_posix()
                out[svg.lower()] = (r["sha256"], r["params_key"])
        return out


def latest_by_source(records: list[dict]) -> list[dict]:
    """Keep the newest record per source file (later lines win)."""
    latest: dict[str, dict] = {}
    for r in records:
        latest[r["source"]] = r
    return list(latest.values())


def load_provenance(csv_path) -> dict[str, dict]:
    """CSV with a `file` column (path relative to the input dir) plus any of:
    license, source, author, year."""
    out: dict[str, dict] = {}
    if not csv_path:
        return out
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row.get("file") or "").strip().replace("\\", "/")
            if key:
                out[key] = {k: (v or "").strip() for k, v in row.items() if k != "file"}
    return out
