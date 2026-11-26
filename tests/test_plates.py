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
    (tmp_path / "r.svg").write_text(pg.compose(job, 0), encoding="utf-8")   # the plate label has a "—"
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


def test_ink_names_with_special_characters_keep_the_files_valid():
    name = 'Red 5" stripe & <trim>'
    job = pg.Job(opts=pg.PageOpts(), art=(20, 20, 50, 50),
                 plates=[pg.Plate(name, "#ff0000", '<rect x="20" y="20" width="50" height="50"/>')])
    lay = ok(pg.layered_svg(job))                                                  # a double quote used to break this file
    layer = lay.find("{*}g")
    assert layer.get("data-name") == name
    assert layer.get("{http://www.inkscape.org/namespaces/inkscape}label") == "1 " + name
    assert name.upper() in "".join(lay.itertext())                                 # the printed plate label
    z = zipfile.ZipFile(io.BytesIO(pg.plates_zip(job)))
    assert name.upper() in "".join(ok(z.read(z.namelist()[0])).itertext())
    assert name.upper() in "".join(ok(pg.compose(job, 0)).itertext())


def test_ink_names_with_special_characters_show_as_typed_in_the_plate_tabs(server, color):
    """A name typed for an ink is shown as it was typed in the plate's tab (< and > in it used to be
    taken for HTML there)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    name = '<b>Red</b> 5" stripe & <trim>'
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.set_input_files("#file", str(color))
        page.wait_for_function("imageId && lastShown", timeout=60000)
        page.click('a[data-t="print"]')
        page.wait_for_function("lastRes && lastRes.plates && lastRes.plates.length > 0", timeout=120000)
        page.click("#side span:text-is('Spot colour')")                    # one box per ink, for its name
        page.wait_for_function("lastRes.plates.length > 1 && document.querySelectorAll('.pal input[type=text]').length === lastRes.plates.length", timeout=120000)
        others = page.evaluate("lastRes.plates.slice(1).map((p, i) => `${i + 2} ${p.name}`)")
        page.locator(".pal input[type=text]").first.fill(name)
        page.wait_for_function("n => lastRes.plates[0].name === n", arg=name, timeout=120000)
        tabs = "[...document.querySelectorAll('#plateTabs div')]"
        page.wait_for_function(f"n => {tabs}.some(d => d.textContent.includes(n))", arg="stripe", timeout=20000)
        assert page.evaluate(f"{tabs}.map(d => d.textContent)") == ["All", "1 " + name] + others
        assert page.evaluate("document.querySelectorAll('#plateTabs div *').length") == 0   # the name made no elements of its own
        page.locator("#plateTabs div").nth(1).click()                      # and its tab opens that plate
        page.wait_for_function(f"lastRes.view === 0 && plateView === 0 && {tabs}[1].className === 'on'", timeout=120000)   # Boot Tool sent that plate
        assert not errs, errs
        b.close()


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


def test_picked_inks_match_every_pixel_to_its_nearest_ink_without_huge_memory(tmp_path):
    import tracemalloc
    import numpy as np
    from PIL import Image
    from boottool.config import Params
    rgb = (np.random.default_rng(3).random((400, 600, 3)) * 255).astype(np.uint8)
    Image.fromarray(rgb).save(tmp_path / "noise.png")
    inks = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff", "#000000", "#888888", "#ff8800", "#0088ff"]
    p = Params(min_side=1, max_side=4000)                                           # keep the picture's own size
    tracemalloc.start()
    labels, pal, paper = sep._fixed_labels(tmp_path / "noise.png", inks, True, p)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert labels.shape == (400, 600) and len(pal) == 11 and paper == 10
    import cv2
    lab = cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)              # the straightforward way, for comparison
    cen = cv2.cvtColor(pal[None].astype(np.float32) / 255, cv2.COLOR_RGB2Lab)[0]
    cen[-1] = np.median(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]), axis=0)
    want = np.argmin(((lab[:, :, None, :] - cen[None, None]) ** 2).sum(3), axis=2)
    assert (labels == want).all()
    assert peak < 20 * labels.nbytes, peak                                           # all inks at once needed about 48x


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
