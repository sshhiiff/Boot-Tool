import io
import json
import re
import threading
import urllib.request
import zipfile

import pytest
from lxml import etree

from boottool import halftone as ht
from boottool import page as pg
from boottool import separate as sep
from boottool.ui import make_server

PLUS = tuple(1 if (6 <= i // 16 <= 9 or 6 <= i % 16 <= 9) else 0 for i in range(256))


def ok(svg):
    root = etree.fromstring(svg.encode() if isinstance(svg, str) else svg)
    assert root.tag.endswith("svg")
    return root


def test_page_sizes_and_fit():
    assert pg.PageOpts(size="A5", orientation="portrait").dims() == (148, 210)
    assert pg.PageOpts(size="A3", orientation="landscape").dims() == (420, 297)
    x, y, w, h = pg.fit_artwork(pg.PageOpts(), 1000, 700)
    assert abs((x + w / 2) - 148.5) < 1e-6 and abs((y + h / 2) - 105) < 1e-6      # centred on A4 landscape
    assert abs(w / h - 1000 / 700) < 1e-6 and w <= 297 - 56 + 1e-6


def test_registration_marks_are_2pt_black():
    o = pg.PageOpts()
    root = ok(pg._svg(pg.Job(opts=o, art=(0, 0, 1, 1)), pg.marks_svg(o), False))
    g = root.find(".//{*}g")
    assert g.get("stroke") == "#000" and abs(float(g.get("stroke-width")) - 2 * 25.4 / 72) < 0.001
    assert len(root.findall(".//{*}rect")) == 6                                    # six marks
    assert len(pg.mark_positions(pg.PageOpts(mark_layout="corners"))) == 4
    assert pg.marks_svg(pg.PageOpts(marks=False)) == ""


def test_coerce_page_clamps():
    o = pg.coerce_page({"size": "A0", "margin": 999, "mark_weight_pt": -3, "orientation": "portrait"})
    assert o.size == "A4" and o.margin == 80 and o.mark_weight_pt == 0.25 and o.orientation == "portrait"


@pytest.mark.parametrize("shape", ["round", "square", "diamond", "ellipse", "line", "cross", "custom"])
def test_halftone_shapes(lineart, shape):
    job = ht.build(lineart, ht.HalftoneParams(shape=shape, stamp=PLUS), pg.PageOpts())
    assert len(job.plates) == 1 and job.info["dots"] > 100
    ok(pg.compose(job, "all"))
    ok(pg.layered_svg(job))


def test_halftone_cmyk_and_errors(color, lineart):
    job = ht.build(color, ht.HalftoneParams(mode="cmyk"), pg.PageOpts(size="A3", orientation="portrait"))
    assert [p.name for p in job.plates] == ["Cyan", "Magenta", "Yellow", "Black"]
    assert job.page == (297, 420)
    with pytest.raises(ValueError, match="Draw a shape"):
        ht.build(lineart, ht.HalftoneParams(shape="custom"), pg.PageOpts())
    with pytest.raises(ValueError, match="too many"):
        ht.build(lineart, ht.HalftoneParams(lpi=100), pg.PageOpts(size="A3"))


def test_halftone_ink_area_follows_tone(tmp_path):
    """Darker image areas get proportionally more ink, and a solid black area closes up."""
    import shutil, subprocess
    from PIL import Image
    import numpy as np
    if not shutil.which("rsvg-convert"):
        pytest.skip("rsvg-convert not installed")
    ramp = np.tile(np.linspace(255, 0, 400, dtype=np.uint8), (200, 1))
    Image.fromarray(ramp).save(tmp_path / "ramp.png")
    job = ht.build(tmp_path / "ramp.png", ht.HalftoneParams(auto_levels=False, lpi=20), pg.PageOpts())
    (tmp_path / "r.svg").write_text(pg.compose(job, 0))
    subprocess.run(["rsvg-convert", "-w", "1200", "-b", "white", "-o", str(tmp_path / "r.png"), str(tmp_path / "r.svg")], check=True)
    img = np.array(Image.open(tmp_path / "r.png").convert("L")) < 128
    k = 1200 / job.page[0]
    ax, ay, aw, ah = job.art
    img = img[int((ay + 2) * k):int((ay + ah - 2) * k), int(ax * k):int((ax + aw) * k)]   # artwork only
    x0, x1 = 0, img.shape[1]
    thirds = [img[:, x0 + int((x1 - x0) * a):x0 + int((x1 - x0) * b)].mean() for a, b in ((0, .33), (.33, .66), (.66, 1))]
    assert thirds[0] < thirds[1] < thirds[2]
    assert thirds[2] > 0.5 and thirds[0] < 0.5


def test_halftone_old_shape_name_still_accepted():
    assert ht.coerce_halftone({"shape": "dot"}).shape == "round"


def test_separation_plates_layers_and_zip(color):
    job = sep.build(color, sep.SepParams(inks=3), pg.PageOpts())
    assert 1 <= len(job.plates) <= 3 and job.info["paper"]                         # paper never printed
    lay = ok(pg.layered_svg(job))
    layers = [g for g in lay if g.get("{http://www.inkscape.org/namespaces/inkscape}groupmode") == "layer"]
    assert len(layers) == len(job.plates)
    assert all(g.get("fill") == "#000" for g in layers)                            # film is pure black
    assert "PLATE 1" in pg.layered_svg(job) and len(lay.findall(".//{*}rect")) == 6 * len(job.plates)
    z = zipfile.ZipFile(io.BytesIO(pg.plates_zip(job)))
    assert len(z.namelist()) == len(job.plates) and all(n.endswith(".svg") for n in z.namelist())
    for n in z.namelist():
        assert not re.findall(r'fill="#(?!000)[0-9a-fA-F]{3,6}"', z.read(n).decode())


def test_separation_inks_limit_overrides_and_trap(color):
    many = sep.build(color, sep.SepParams(inks=10), pg.PageOpts())
    assert len(many.plates) <= 10
    assert sep.coerce_sep({"inks": 99}).inks == 10
    named = sep.build(color, sep.SepParams(inks=3, colors=("#123456",), names=("Spot A",)), pg.PageOpts())
    assert named.plates[0].ink == "#123456" and named.plates[0].name == "Spot A"
    flat = sep.build(color, sep.SepParams(inks=3, trap=0), pg.PageOpts())
    trapped = sep.build(color, sep.SepParams(inks=3, trap=0.5), pg.PageOpts())
    assert flat.plates[0].body != trapped.plates[0].body                           # lighter plate spreads
    assert flat.plates[-1].body == trapped.plates[-1].body                         # darkest plate does not


@pytest.fixture
def server():
    srv = make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _post(url, data, ctype="application/json"):
    import urllib.error
    req = urllib.request.Request(url, data=data if isinstance(data, bytes) else json.dumps(data).encode(),
                                 method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_endpoints_plates_and_download(server, color):
    iid = json.loads(_post(server + "/api/upload", color.read_bytes(), "application/octet-stream")[1])["id"]
    req = {"id": iid, "session": "t", "seq": 1, "tool": "separate", "params": {"inks": 3},
           "page": {"size": "A5", "orientation": "portrait"}, "view": "all"}
    code, body, _ = _post(server + "/api/plates", req)
    res = json.loads(body)
    assert code == 200 and res["page"] == [148, 210] and res["plates"] and res["info"]["palette"]
    ok(res["svg"])
    code, body, _ = _post(server + "/api/plates", {**req, "seq": 2, "view": 0})
    assert code == 200 and "PLATE 1" in json.loads(body)["svg"]
    assert _post(server + "/api/plates", {**req, "seq": 1})[0] == 204                # stale request dropped
    code, z, h = _post(server + "/api/download", {**req, "kind": "zip"})
    assert code == 200 and h["Content-Type"] == "application/zip" and zipfile.ZipFile(io.BytesIO(z)).namelist()
    code, s, h = _post(server + "/api/download", {**req, "kind": "svg"})
    assert code == 200 and h["Content-Type"] == "image/svg+xml" and b'fill="#000"' in s
    code, body, _ = _post(server + "/api/plates", {**req, "seq": 3, "tool": "halftone", "params": {"shape": "custom"}})
    assert code == 400 and "Draw a shape" in json.loads(body)["error"]
