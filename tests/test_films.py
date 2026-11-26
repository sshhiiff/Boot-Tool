"""Plates as pictures (boottool/raster.py): PNG and TIFF films at a chosen resolution."""
import io
import json
import shutil
import subprocess
import zipfile

import numpy as np
import pytest
from PIL import Image

from boottool import halftone as ht
from boottool import page as pg
from boottool import printing as pr
from boottool import raster
from boottool import separate as sep

from test_plates import _post, server  # noqa: F401 (server is a fixture)


def ink(im: Image.Image) -> np.ndarray:
    """Where a film picture is black."""
    assert im.mode == "1"                                                   # one bit per dot: black or white, nothing between
    return ~np.asarray(im)


def one_plate(body: str, name: str = "Ink", **opts) -> pg.Job:
    return pg.Job(opts=pg.PageOpts(**opts), art=(20, 20, 50, 50), plates=[pg.Plate(name, "#000000", body)])


def test_a_film_picture_is_the_page_at_its_real_size_dot_for_dot():
    """300 dots to the inch: an A5 page is 1748 x 2480 dots, and a 50 mm square is black over
    590.55 x 590.55 of them, where it lies on the page, with nothing grown or shrunk."""
    job = one_plate('<rect x="20" y="20" width="50" height="50"/>', size="A5", orientation="portrait", marks=False, ink_names=False)
    black = ink(raster.plate_picture(job, 0, 300))
    assert black.shape == (2480, 1748)
    rows, cols = np.nonzero(black)
    k = 300 / 25.4
    assert (rows.min(), cols.min()) == (round(20 * k), round(20 * k))       # a dot is black when its centre is inside
    assert rows.max() - rows.min() + 1 in (590, 591) and cols.max() - cols.min() + 1 in (590, 591)
    assert black.sum() == (rows.max() - rows.min() + 1) * (cols.max() - cols.min() + 1)   # a full square, nothing else
    for dpi, size in ((600, (3496, 4961)), (1200, (6992, 9921))):
        assert raster.plate_picture(job, 0, dpi).size == size


def test_small_dots_keep_their_size():
    """A film of halftone dots must not come out darker or lighter than the plate: a thousand dots
    of 0.4 mm across cover what a thousand such circles cover (to within the dots at their edges)."""
    r, n = 0.2, 0
    parts = []
    for i in range(40):
        for j in range(25):
            cx, cy = 20.13 + i * 1.07, 20.29 + j * 1.11
            c = r * 0.5523
            parts.append(f"M{cx - r} {cy}C{cx - r} {cy - c} {cx - c} {cy - r} {cx} {cy - r}C{cx + c} {cy - r} {cx + r} {cy - c} {cx + r} {cy}"
                         f"C{cx + r} {cy + c} {cx + c} {cy + r} {cx} {cy + r}C{cx - c} {cy + r} {cx - r} {cy + c} {cx - r} {cy}Z")
            n += 1
    job = one_plate(f'<path fill-rule="evenodd" d="{"".join(parts)}"/>', marks=False, ink_names=False)
    for dpi in (300, 600, 1200):
        k = dpi / 25.4
        covered = ink(raster.plate_picture(job, 0, dpi)).sum() / (n * np.pi * (r * k) ** 2)
        assert abs(covered - 1) < 0.03, (dpi, covered)                      # (drawing every dot its edge touches gives 1.3 and more)


def test_holes_overlaps_and_lines_are_drawn_as_the_svg_means_them():
    def covered(body, **opts):
        return int(ink(raster.plate_picture(one_plate(body, marks=False, ink_names=False, **opts), 0, 300)).sum())
    k2 = (300 / 25.4) ** 2
    square = "M20 20H60V60H20Z"
    assert covered(f'<path fill-rule="evenodd" d="{square}M30 30H50V50H30Z"/>') == pytest.approx((1600 - 400) * k2, rel=0.01)   # a hole
    assert covered(f'<path d="{square}M30 30V50H50V30Z"/>') == pytest.approx((1600 - 400) * k2, rel=0.01)   # a hole by going the other way round
    assert covered(f'<path d="{square}M30 30H50V50H30Z"/>') == pytest.approx(1600 * k2, rel=0.01)           # the same way round: no hole
    assert covered(f'<path d="{square}M40 40H80V80H40Z"/>') == pytest.approx((3200 - 400) * k2, rel=0.01)   # overlapping: covered once
    assert covered(f'<path fill-rule="evenodd" d="{square}M40 40H80V80H40Z"/>') == pytest.approx((3200 - 800) * k2, rel=0.01)
    assert covered('<g transform="translate(20 20) scale(2 0.5)"><path d="M0 0h10v10h-10z"/></g>') == pytest.approx(20 * 5 * k2, rel=0.02)
    lines = '<g fill="none" stroke="#000" stroke-width="2"><path d="M20 40H60"/><rect x="20" y="60" width="20" height="10"/></g>'
    assert covered(lines) == pytest.approx((40 * 2 + (22 * 12 - 18 * 8)) * k2, rel=0.02)   # a line 2 mm thick, and a frame


def test_marks_and_the_plates_name_are_in_the_picture():
    job = one_plate("", name="Zürich — Красный", size="A5")                 # landscape: 210 x 148 mm
    black = ink(raster.plate_picture(job, 0, 300))
    k = 300 / 25.4
    for cx, cy in pg.mark_positions(job.opts):                              # each mark: a square with a cross through it
        x, y = round(cx * k), round(cy * k)
        assert black[y, x - 40:x + 40].all() and black[y - 40:y + 40, x].all()
    name = black[round(9 * k):round(15 * k), round(21 * k):round(95 * k)]   # to the right of the first mark
    assert 1500 < name.sum() < name.size * 0.4 and name.any(axis=0).sum() > 300   # letters, not a blot and not nothing
    bare = ink(raster.plate_picture(one_plate("", size="A5", marks=False, ink_names=False), 0, 300))
    assert not bare.any()

    def name_of(n):                                                         # just the name's dots
        return ink(raster.plate_picture(one_plate("", name=n, size="A5", marks=False), 0, 300))
    assert np.array_equal(name_of("Zürich é ñ"), name_of("Zurich e n"))     # the built-in font has no accents: the plain letter is drawn
    assert np.array_equal(name_of("Красный 赤"), name_of("??????? ?"))       # and "?" for other alphabets
    assert not np.array_equal(name_of("Red"), name_of("Rod"))


def test_png_and_tiff_say_their_resolution_and_hold_the_same_dots(color):
    job = sep.build(color, sep.SepParams(inks=3), pg.PageOpts(size="A5"))
    names = zipfile.ZipFile(io.BytesIO(pg.plates_zip(job, "badge"))).namelist()
    pngs = zipfile.ZipFile(io.BytesIO(raster.films_zip(job, "png", 300, "badge")))
    tiffs = zipfile.ZipFile(io.BytesIO(raster.films_zip(job, "tiff", 300, "badge")))
    assert pngs.namelist() == [n[:-4] + ".png" for n in names] and tiffs.namelist() == [n[:-4] + ".tif" for n in names]
    assert len(names) == len(job.plates) > 1
    for i, (a, b) in enumerate(zip(pngs.namelist(), tiffs.namelist())):
        png, tiff = Image.open(io.BytesIO(pngs.read(a))), Image.open(io.BytesIO(tiffs.read(b)))
        assert png.format == "PNG" and tiff.format == "TIFF" and png.mode == tiff.mode == "1"
        assert [round(float(v)) for v in png.info["dpi"]] == [300, 300] == [round(float(v)) for v in tiff.info["dpi"]]
        assert tiff.info["compression"] == "group4" and tiff.tag_v2[262] == 0   # fax-type, and "white is 0" as such files are by custom
        assert png.size == tiff.size == (2480, 1748)                        # 210 x 148 mm at 300 dpi: it prints at its real size
        assert np.array_equal(np.asarray(png), np.asarray(tiff))
        assert np.array_equal(np.asarray(png), np.asarray(raster.plate_picture(job, i, 300)))
    again = zipfile.ZipFile(io.BytesIO(raster.films_zip(job, "png", 300, "badge")))
    assert [again.read(n) for n in again.namelist()] == [pngs.read(n) for n in pngs.namelist()]   # the same files every time


def _jobs(color, lineart, tmp_path):
    """Plates of every kind Boot Tool makes (coarse ones, to keep this quick)."""
    small = tmp_path / "small.png"
    Image.open(color).resize((240, 160)).save(small)
    yield "flat colours", sep.build(color, sep.SepParams(inks=4), pg.PageOpts())
    yield "halftone", ht.build(lineart, ht.HalftoneParams(lpi=20, ppi=150), pg.PageOpts(size="A5", orientation="portrait"))
    yield "one-ink dither on a dark shirt", pr.build(small, pr.coerce_print({"render": "dither", "dither_size": 1.0, "dark": True, "underbase": True}),
                                                     pg.PageOpts(size="A5", mark_layout="corners"))
    yield "CMYK at a set size", pr.build(small, pr.coerce_print({"method": "cmyk", "lpi": 12, "ppi": 100}),
                                         pg.PageOpts(art_w=250.0, size="Custom", custom_w=320.0, custom_h=240.0))


def test_every_kind_of_plate_looks_like_its_svg_film(color, lineart, tmp_path):
    """Each film picture against the same plate's SVG drawn by another program (rsvg-convert): the
    same dots are black, except single dots along edges, and as much is black in all."""
    if not shutil.which("rsvg-convert"):
        pytest.skip("rsvg-convert not installed")
    import cv2
    dpi = 300
    for what, job in _jobs(color, lineart, tmp_path):
        assert job.plates, what
        for i in range(len(job.plates)):
            ours = ink(raster.plate_picture(job, i, dpi))
            (tmp_path / "f.svg").write_text(pg.compose(job, i), encoding="utf-8")
            subprocess.run(["rsvg-convert", "-w", str(ours.shape[1]), "-h", str(ours.shape[0]), "-b", "white", "-o", str(tmp_path / "f.png"),
                            str(tmp_path / "f.svg")], check=True)
            theirs = np.asarray(Image.open(tmp_path / "f.png").convert("L")) < 128
            mm = dpi / 25.4
            ours, theirs = ours.copy(), theirs.copy()
            for im in (ours, theirs):
                im[int(8 * mm):int(16 * mm), int(21 * mm):int(150 * mm)] = False   # not the plate's name (the two use different fonts)
            edge = cv2.dilate(theirs.astype(np.uint8), np.ones((3, 3), np.uint8)) != cv2.erode(theirs.astype(np.uint8), np.ones((3, 3), np.uint8))
            assert theirs.sum() > 2000, (what, i)
            assert int(((ours != theirs) & ~edge).sum()) <= 20, (what, i)   # (a dot or two where a hairline falls between dot centres)
            assert abs(ours.sum() / theirs.sum() - 1) < 0.03, (what, i, ours.sum(), theirs.sum())


def test_what_cannot_be_drawn_stops_with_an_error():
    for body, said in (('<circle cx="30" cy="30" r="5"/>', "circle"), ('<path fill="#ff0000" d="M0 0L5 0L5 5Z"/>', "black"),
                       ('<g transform="rotate(30)"><path d="M0 0L5 0L5 5Z"/></g>', "transform")):
        with pytest.raises(ValueError, match=said):
            raster.plate_picture(one_plate(body), 0, 300)
    job = one_plate('<rect x="20" y="20" width="50" height="50"/>')
    with pytest.raises(ValueError, match="300, 600 or 1200 dpi"):
        raster.plate_picture(job, 0, 150)
    with pytest.raises(ValueError, match="PNG or TIFF"):
        raster.films_zip(job, "jpg", 300)
    with pytest.raises(ValueError, match="no plates"):
        raster.films_zip(pg.Job(opts=pg.PageOpts(), art=(20, 20, 50, 50)), "png", 300)
    banner = one_plate('<rect x="20" y="20" width="50" height="50"/>', size="Custom", custom_w=2000.0, custom_h=1000.0)
    with pytest.raises(ValueError, match="too big. Choose a lower resolution"):
        raster.plate_picture(banner, 0, 1200)                               # 94,488 x 47,244 dots
    assert raster.plate_picture(banner, 0, 300).size == (23622, 11811)      # at 300 dpi it is made
    with pytest.raises(ValueError, match="Plate 1 could not be drawn as a picture. If its ink name"):
        raster.plate_picture(one_plate("", name="Red\x01"), 0, 300)


def test_downloading_films_from_boot_tool(server, color):
    iid = json.loads(_post(server + "/api/upload", color.read_bytes(), "application/octet-stream")[1])["id"]
    req = {"id": iid, "tool": "separate", "params": {"inks": 3}, "page": {"size": "A5", "orientation": "portrait"}}
    plates = json.loads(_post(server + "/api/plates", {**req, "session": "t", "seq": 1, "view": "all"})[1])["plates"]
    for fmt, ext in (("png", ".png"), ("tiff", ".tif")):
        code, body, h = _post(server + "/api/films", {**req, "format": fmt, "dpi": 300})
        assert code == 200 and h["Content-Type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(body))
        assert len(z.namelist()) == len(plates) > 1 and all(n.endswith(ext) for n in z.namelist())
        assert Image.open(io.BytesIO(z.read(z.namelist()[0]))).size == (1748, 2480)
    for bad in ({"dpi": 150}, {"dpi": "600"}, {"dpi": None}, {"format": "bmp", "dpi": 300}):
        code, body, _ = _post(server + "/api/films", {**req, "format": "png", **bad})
        assert code == 400 and json.loads(body)["error"], bad


def test_the_films_box_in_the_browser(server, color, tmp_path):
    """Print has a box "Films as pictures": a resolution to choose and two buttons."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        png, tiff = "button:text-is('PNG — .zip, one per plate')", "button:text-is('TIFF — .zip, one per plate')"
        page.click('a[data-t="print"]')
        page.wait_for_selector(png)
        assert page.is_disabled(png) and page.is_disabled(tiff)            # nothing to download yet
        page.set_input_files("#file", str(color))
        page.wait_for_function("lastRes && lastRes.plates && lastRes.plates.length > 0", timeout=120000)
        page.click("#side span:text-is('Spot colour')")                    # (flat colours: quick to draw)
        page.wait_for_function("lastRes.plates.length > 1", timeout=120000)
        page.wait_for_function("(b => b.length === 2 && b.every(x => !x.disabled))([...document.querySelectorAll('button')].filter(x => x.textContent.includes('one per plate')))", timeout=20000)
        on = "[...document.querySelectorAll('#side .cells span.on')].map(s => s.textContent).filter(t => /dpi/.test(t))"
        assert page.evaluate(on) == ["600 dpi"]
        page.click("#side span:text-is('300 dpi')")
        assert page.evaluate(on) == ["300 dpi"]
        with page.expect_download(timeout=180000) as d:
            page.click(png)
        assert d.value.suggested_filename == "color-print-films-300dpi-png.zip"
        d.value.save_as(tmp_path / "f.zip")
        z = zipfile.ZipFile(tmp_path / "f.zip")
        assert len(z.namelist()) == page.evaluate("lastRes.plates.length") and Image.open(io.BytesIO(z.read(z.namelist()[0]))).size == (3508, 2480)
        page.wait_for_function("document.querySelector('#toast').textContent === 'Films downloaded.'", timeout=20000)
        with page.expect_download(timeout=180000) as d:
            page.click(tiff)
        assert d.value.suggested_filename == "color-print-films-300dpi-tiff.zip"
        assert not errs, errs
        b.close()
