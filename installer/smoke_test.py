"""Start the built app and use it once, the way a person would: open the page, open a picture,
convert it, make print plates and turn text into outlines, then quit. Fails loudly if anything that
works from source is missing from the app (a file or library PyInstaller did not pick up).

    python installer/smoke_test.py dist/BootTool/BootTool
"""
from __future__ import annotations

import io
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request

from PIL import Image, ImageDraw


def _request(url: str, data=None, ctype="application/json"):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype} if data is not None else {})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.status, r.read()


def _picture() -> bytes:
    im = Image.new("RGB", (480, 320), (250, 245, 235))
    d = ImageDraw.Draw(im)
    d.ellipse((40, 40, 220, 220), fill=(200, 40, 40), outline=(0, 0, 0), width=6)
    d.rectangle((260, 80, 440, 260), fill=(40, 80, 200), outline=(0, 0, 0), width=6)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def main(exe: str) -> None:
    if os.name == "nt" and not exe.lower().endswith(".exe"):
        exe += ".exe"
    home = tempfile.mkdtemp(prefix="boottool-smoke-")
    env = {**os.environ, "BROWSER": "no-browser-during-tests",            # never open a real browser
           "BOOTTOOL_LIBRARY": os.path.join(home, "Library"), "BOOTTOOL_PRINTS": os.path.join(home, "Prints")}
    proc = subprocess.Popen([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in proc.stdout], daemon=True).start()
    try:
        url, seen = None, []
        while url is None:
            try:
                line = lines.get(timeout=180)                                  # the first start of a new app can be slow
            except queue.Empty:
                raise SystemExit("The app did not start within 3 minutes. It printed:\n" + "".join(seen))
            seen.append(line)
            m = re.search(r"http://127\.0\.0\.1:\d+/", line)
            url = m.group(0) if m else None
            if proc.poll() is not None and url is None:
                raise SystemExit("The app quit at start-up. It printed:\n" + "".join(seen))
        print("app running at", url)

        code, body = _request(url)
        assert code == 200 and b"Boot Tool" in body, "the page did not load"
        for f in ("compose.js", "simulate.js", "vendor/paper-core.js"):
            assert _request(url + "static/" + f)[0] == 200, f"{f} is missing from the app"
        print("page and scripts load")

        image = json.loads(_request(url + "api/upload", _picture(), "application/octet-stream")[1])
        res = json.loads(_request(url + "api/trace", json.dumps(
            {"id": image["id"], "session": "smoke", "seq": 1, "params": {}}).encode())[1])
        assert "<path" in res["svg"] and res["stats"]["path_count"] > 0, "converting produced no shapes"
        print("convert:", res["mode"], res["stats"]["path_count"], "paths")

        for params, plates in (({"method": "cmyk"}, 4), ({"method": "spot", "kind": "flat", "inks": 3}, None),
                               ({"method": "one", "render": "dither", "dither": "serpentine"}, 1)):
            res = json.loads(_request(url + "api/plates", json.dumps(
                {"id": image["id"], "tool": "print", "session": "smoke", "seq": 2, "params": params, "page": {}}).encode())[1])
            assert res["plates"] and (plates is None or len(res["plates"]) == plates), f"print {params} made {len(res['plates'])} plates"
            print("print", params["method"], "->", [p["name"] for p in res["plates"]])
        pdf = _request(url + "api/download", json.dumps(
            {"id": image["id"], "tool": "print", "params": params, "page": {}, "kind": "pdf"}).encode())[1]
        assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF"), "the plates were not written as PDF"
        print("pdf:", len(pdf), "bytes")

        fonts = json.loads(_request(url + "api/fonts")[1])["fonts"]
        if fonts:
            out = json.loads(_request(url + "api/text", json.dumps({"font": fonts[0]["id"], "text": "Boot", "size": 20}).encode())[1])
            assert out["d"].startswith("M"), "text was not turned into outlines"
        print("fonts:", len(fonts), "found; text outlines work" if fonts else "found (text not checked)")
        print("SMOKE TEST PASSED")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join("dist", "BootTool", "BootTool"))
