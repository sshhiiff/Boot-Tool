"""Local web interface: live sliders over the same pipeline the CLI uses.

Standard library only. Binds to 127.0.0.1, so nothing leaves your machine.
"""
from __future__ import annotations

import json
import tempfile
import subprocess
import threading
import time
import uuid
import webbrowser
from collections import OrderedDict
from urllib.parse import parse_qs, urlparse
from dataclasses import fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from dataclasses import asdict

from . import halftone as ht
from . import batch as bt
from . import fonts as fonts_mod
from . import library as lib
from . import prints as prints_mod
from . import printing as pr
from . import separate as sep
from .config import Params
from .page import coerce_page, compose, layered_svg, plates_zip
from .preprocess import prepare
from .score import score_svg
from .simplify import postprocess, svg_stats
from .trace import get_tracer

MAX_UPLOAD = 80 * 1024 * 1024
STATIC = Path(__file__).parent / "static"

# Params that change the preprocessed target. Everything else only re-runs tracing,
# which is much faster, so slider drags on those reuse the cached target.
PRE_FIELDS = ("upscale", "upscale_style", "min_side", "max_side", "mode", "denoise",
              "normalize_background", "threshold", "level", "ink", "colors")

_BOUNDS = {
    "upscale": (0.25, 8), "level": (0, 254), "speckle": (0, 500), "epsilon": (0.05, 10),
    "smooth_sigma": (0, 4), "corner_deg": (30, 179), "colors": (2, 32),
    "min_side": (1, 4096), "max_side": (256, 6000), "merge_tol": (0, 100),
}
_CHOICES = {
    "mode": {"auto", "lineart", "color"}, "threshold": {"otsu", "adaptive"},
    "upscale_style": {"smooth", "pixel"}, "backend": {"builtin"},
}


def coerce_params(raw: dict) -> Params:
    """Build Params from untrusted JSON: right types, clamped ranges, known keys only."""
    defaults = Params()
    out = {}
    for f in fields(Params):
        if f.name not in raw:
            continue
        v, d = raw[f.name], getattr(defaults, f.name)
        if isinstance(d, bool):
            out[f.name] = bool(v)
        elif isinstance(d, int):
            out[f.name] = int(round(float(v)))
        elif isinstance(d, float):
            out[f.name] = float(v)
        else:
            out[f.name] = str(v)
        if f.name in _BOUNDS:
            lo, hi = _BOUNDS[f.name]
            out[f.name] = type(d)(min(max(out[f.name], lo), hi))
        if f.name in _CHOICES and out[f.name] not in _CHOICES[f.name]:
            out[f.name] = d
        if f.name == "ink" and out[f.name] != "auto" and not _is_hex(out[f.name]):
            out[f.name] = d
    return Params(**out)


def _is_hex(s: str) -> bool:
    return len(s) == 7 and s[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in s[1:])


class Engine:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="boottool-ui-"))
        self.files: dict[str, Path] = {}
        self.cache: OrderedDict = OrderedDict()
        self.jobs: OrderedDict = OrderedDict()
        self.lock = threading.Lock()
        self.latest: dict[str, int] = {}
        self.batches: OrderedDict = OrderedDict()

    def add(self, data: bytes) -> dict:
        image_id = uuid.uuid4().hex[:12]
        path = self.dir / image_id
        path.write_bytes(data)
        try:
            with Image.open(path) as im:
                size = im.size
        except Exception as e:  # noqa: BLE001
            path.unlink(missing_ok=True)
            raise ValueError(f"Not a readable image: {e}") from e
        self.files[image_id] = path
        out = {"id": image_id, "width": size[0], "height": size[1]}
        try:
            out.update(pr.analyse(path))
        except Exception:  # noqa: BLE001 - the histogram is a nicety; never block opening an image
            out.update({"hist": [0] * 256, "auto": [0, 1.0, 255], "tonal": False})
        return out

    def trace(self, image_id: str, raw: dict, session: str, seq: int):
        """Returns a result dict, or None if a newer request for this session superseded it."""
        if image_id not in self.files:
            raise KeyError("unknown image id; re-open the image")
        p = coerce_params(raw)
        self.latest[session] = max(self.latest.get(session, 0), seq)
        with self.lock:
            if self.latest[session] > seq:
                return None
            t0 = time.perf_counter()
            key = (image_id, json.dumps({k: getattr(p, k) for k in PRE_FIELDS}, sort_keys=True))
            prep = self.cache.get(key)
            if prep is None:
                prep = prepare(self.files[image_id], p)
                self.cache[key] = prep
                while len(self.cache) > 4:
                    self.cache.popitem(last=False)
            else:
                self.cache.move_to_end(key)
            svg = postprocess(get_tracer(p.backend).trace(prep, p), p, prep.display_size)
            result = {
                "svg": svg, "mode": prep.mode, "size": list(prep.size),
                "scale": round(prep.scale, 3), "stats": svg_stats(svg),
                "scores": _scores(svg, prep), "ink": prep.ink,
            }
            result["ms"] = int((time.perf_counter() - t0) * 1000)
            return result


def _plate_job(engine: "Engine", tool: str, image_id: str, raw: dict):
    """Build (or fetch from cache) the plate Job for the halftone or separate tool."""
    if image_id not in engine.files:
        raise KeyError("unknown image id; re-open the image")
    if tool not in ("halftone", "separate", "print"):
        raise ValueError("unknown tool")
    page = coerce_page(raw.get("page", {}))
    if tool == "print":
        params = pr.coerce_print(raw.get("params", {}))
        builder = pr.build
    elif tool == "halftone":
        params = ht.coerce_halftone(raw.get("params", {}))
        builder = ht.build
    else:
        params = sep.coerce_sep(raw.get("params", {}))
        builder = sep.build
    key = (tool, image_id, json.dumps(asdict(params), sort_keys=True), json.dumps(asdict(page), sort_keys=True))
    job = engine.jobs.get(key)
    if job is None:
        job = builder(engine.files[image_id], params, page)
        engine.jobs[key] = job
        while len(engine.jobs) > 3:
            engine.jobs.popitem(last=False)
    else:
        engine.jobs.move_to_end(key)
    return job


def plates_response(engine: "Engine", req: dict):
    """Returns a result dict, or None if a newer request superseded this one."""
    session, seq = str(req.get("session", "")), int(req.get("seq", 0))
    engine.latest[session] = max(engine.latest.get(session, 0), seq)
    with engine.lock:
        if engine.latest[session] > seq:
            return None
        t0 = time.perf_counter()
        job = _plate_job(engine, req.get("tool", ""), req["id"], req)
        view = req.get("view", "all")
        view = "all" if view == "all" or int(view) >= len(job.plates) else int(view)
        pw, ph = job.page
        info = dict(job.info)
        info["layered_bytes"] = len(layered_svg(job).encode())
        return {
            "svg": compose(job, view), "view": view, "page": [pw, ph], "art": list(job.art),
            "plates": [{"name": p.name, "ink": p.ink} for p in job.plates],
            "info": info, "ms": int((time.perf_counter() - t0) * 1000),
        }


def download_response(engine: "Engine", req: dict):
    if isinstance(req.get("params"), dict):
        req = {**req, "params": {**req["params"], "draft": False}}      # files are never drafts
    with engine.lock:
        job = _plate_job(engine, req.get("tool", ""), req["id"], req)
        if not job.plates:
            raise ValueError("Nothing to download: no plates were produced.")
        if req.get("kind") == "zip":
            return plates_zip(job), "application/zip"
        return layered_svg(job).encode("utf-8"), "image/svg+xml"


def _scores(svg, prep) -> dict:
    """The self-check needs an SVG rasteriser (rsvg-convert or cairosvg). Without one the tool still works; the check is just skipped."""
    try:
        return score_svg(svg, prep)
    except (RuntimeError, OSError, subprocess.CalledProcessError):
        return {"ssim_target": None, "ssim_source": None, "mae_target": None}


def design_response(engine: "Engine", req: dict) -> dict:
    """The design on its own (no page, no marks) in its ink colours, at full quality, plus its real size in mm."""
    if isinstance(req.get("params"), dict):
        req = {**req, "params": {**req["params"], "draft": False}}
    with engine.lock:
        job = _plate_job(engine, req.get("tool", ""), req["id"], req)
        if not job.plates:
            raise ValueError("Nothing to save: no plates were produced.")
        return {"svg": compose(job, "design"), "width_mm": job.art[2], "height_mm": job.art[3],
                "garment": job.info.get("garment", "")}


def make_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    engine = Engine()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json"):
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass            # the browser tab was closed or the request was replaced mid-draw

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj).encode())

        def _local_only(self) -> bool:
            """Refuse requests that did not come from this computer's own page (stops other websites
            from driving the tool, which can now write files)."""
            ok = {"127.0.0.1", "localhost", "::1"}
            host = urlparse("//" + (self.headers.get("Host") or "")).hostname or ""
            origin = self.headers.get("Origin")
            if host not in ok or (origin and (urlparse(origin).hostname or "") not in ok):
                self._json(403, {"error": "This tool only answers its own page on this computer."})
                return False
            return True

        def do_GET(self):
            if not self._local_only():
                return
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path.startswith("/api/batch/"):
                job = engine.batches.get((q.get("job") or [""])[0])
                if job is None:
                    return self._json(404, {"error": "unknown batch; run it again"})
                if url.path == "/api/batch/status":
                    return self._json(200, job.status())
                if url.path == "/api/batch/svg":
                    try:
                        f = job.svg_path(int((q.get("i") or ["-1"])[0]))
                    except ValueError:
                        f = None
                    return self._send(200, f.read_bytes(), "image/svg+xml") if f else self._json(404, {"error": "not ready"})
                if url.path == "/api/batch/zip":
                    return self._send(200, job.zip_bytes(), "application/zip")
                return self._json(404, {"error": "not found"})
            if url.path == "/api/library":
                return self._json(200, {"items": lib.listing(), "folder": str(lib.folder())})
            if url.path == "/api/library/file":
                try:
                    return self._send(200, lib.read((q.get("name") or [""])[0]), "image/svg+xml")
                except (ValueError, KeyError) as e:
                    return self._json(404, {"error": str(e).strip("'")})
            if url.path == "/api/prints":
                return self._json(200, {"items": prints_mod.listing(), "folder": str(prints_mod.folder())})
            if url.path == "/api/prints/file":
                try:
                    return self._send(200, prints_mod.read((q.get("name") or [""])[0]), "image/png")
                except (ValueError, KeyError) as e:
                    return self._json(404, {"error": str(e).strip("'")})
            if url.path == "/api/fonts":
                return self._json(200, {"fonts": fonts_mod.listing(refresh="refresh" in q), "folder": str(fonts_mod.user_folder())})
            if url.path.startswith("/static/"):
                f = STATIC / url.path[len("/static/"):]
                ok = {"compose.js", "simulate.js", "vendor/paper-core.js"}
                if url.path[len("/static/"):] in ok and f.is_file():
                    return self._send(200, f.read_bytes(), "text/javascript; charset=utf-8")
                return self._json(404, {"error": "not found"})
            if self.path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/defaults":
                self._json(200, {"convert": Params().to_dict(), "halftone": asdict(ht.HalftoneParams()),
                                 "separate": asdict(sep.SepParams()),
                                 "print": asdict(pr.PrintParams()), "page": asdict(coerce_page({})),
                                 "version": __import__("boottool").__version__})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._local_only():
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_UPLOAD:
                return self._json(413 if n > 0 else 400, {"error": "empty or too large (80 MB max)"})
            body = self.rfile.read(n)
            try:
                if self.path == "/api/upload":
                    return self._json(200, engine.add(body))
                if self.path == "/api/plates":
                    res = plates_response(engine, json.loads(body))
                    return self._send(204, b"") if res is None else self._json(200, res)
                if self.path == "/api/download":
                    data, ctype = download_response(engine, json.loads(body))
                    return self._send(200, data, ctype)
                if self.path == "/api/design":
                    return self._json(200, design_response(engine, json.loads(body)))
                if self.path == "/api/prints/save":
                    req = json.loads(body)
                    name = prints_mod.save(str(req.get("name") or "print"), str(req.get("png") or ""), float(req.get("width_mm", 0)),
                                           float(req.get("height_mm", 0)), str(req.get("garment") or ""))
                    return self._json(200, {"name": name, "folder": str(prints_mod.folder())})
                if self.path == "/api/prints/delete":
                    prints_mod.delete(str(json.loads(body).get("name", "")))
                    return self._json(200, {"ok": True})
                if self.path == "/api/library/save":
                    req = json.loads(body)
                    name = lib.save(str(req.get("name") or "graphic"), str(req.get("svg") or ""), req.get("key") and str(req["key"]))
                    return self._json(200, {"name": name, "folder": str(lib.folder())})
                if self.path == "/api/library/rename":
                    req = json.loads(body)
                    return self._json(200, {"name": lib.rename(str(req.get("name", "")), str(req.get("to", "")))})
                if self.path == "/api/library/delete":
                    lib.delete(str(json.loads(body).get("name", "")))
                    return self._json(200, {"ok": True})
                if self.path == "/api/text":
                    req = json.loads(body)
                    return self._json(200, fonts_mod.outline(int(req.get("font", 0)), req.get("text", ""), float(req.get("size", 10)),
                                                             float(req.get("spacing", 0)), str(req.get("align", "left")),
                                                             float(req.get("line", 1.2))))
                if self.path == "/api/batch/start":
                    job = bt.start(engine, json.loads(body), coerce_params)
                    return self._json(200, {"job": job.id, "out_dir": str(job.out_dir) if job.out_dir else None})
                if self.path == "/api/batch/stop":
                    job = engine.batches.get(str(json.loads(body).get("job", "")))
                    if job:
                        job.stop.set()
                    return self._json(200, {"ok": True})
                if self.path == "/api/trace":
                    req = json.loads(body)
                    res = engine.trace(req["id"], req.get("params", {}),
                                       str(req.get("session", "")), int(req.get("seq", 0)))
                    return self._send(204, b"") if res is None else self._json(200, res)
                self._json(404, {"error": "not found"})
            except (ValueError, KeyError) as e:
                self._json(400, {"error": str(e).strip("'")})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

    return ThreadingHTTPServer((host, port), Handler)


def serve(port: int = 8765, open_browser: bool = True) -> None:
    srv = make_server("127.0.0.1", port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"boottool UI running at {url}\nPress Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        srv.server_close()
