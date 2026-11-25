"""Plates as PDF (boottool/pdf.py).

The PDFs are read back here by a small, strict reader of this file's own, and every point of every
shape is compared with the SVG film of the same plate, read by another small reader. Neither uses
the code that wrote the PDF.
"""
import io
import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
import zipfile
import zlib

import numpy as np
import pytest
from lxml import etree
from PIL import Image

from boottool import halftone as ht
from boottool import page as pg
from boottool import pdf
from boottool import printing as pr
from boottool import separate as sep
from boottool.ui import make_server

K = 72 / 25.4                                                             # points in a millimetre


# ------------------------------------------------------------------ reading a PDF back
def read_pdf(data: bytes) -> list[dict]:
    """The pages of a PDF: {"box": its size in points, "ops": its drawing operators, one per line}.
    Everything a reader relies on to find its way is checked on the way: the table of objects points
    at each object exactly, lengths are exact, and the page list matches the pages."""
    assert data.startswith(b"%PDF-1.4\n") and data.endswith(b"%%EOF\n")
    at = int(re.search(rb"startxref\n(\d+)\n%%EOF\n$", data).group(1))
    head = re.match(rb"xref\n0 (\d+)\n", data[at:])
    count = int(head.group(1))
    rows = data[at + head.end():at + head.end() + 20 * count]
    assert rows[:20] == b"0000000000 65535 f \n"
    objs = {}
    for n in range(1, count):
        row = rows[20 * n:20 * n + 20]
        assert re.fullmatch(rb"\d{10} 00000 n \n", row), row
        start = int(row[:10])
        opener = f"{n} 0 obj\n".encode()
        assert data[start:start + len(opener)] == opener, f"object {n} is not where the table says"
        body = data[start + len(opener):]
        m = re.match(rb"<< /Length (\d+) /Filter /FlateDecode >>\nstream\n", body)
        if m:
            raw = body[m.end():m.end() + int(m.group(1))]
            assert body[m.end() + int(m.group(1)):].startswith(b"\nendstream\nendobj\n")
            objs[n] = zlib.decompress(raw)
        else:
            objs[n] = body[:body.index(b"\nendobj\n")]
    trailer = data[at + head.end() + 20 * count:]
    assert re.match(rb"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\n" % (count, count - 1), trailer)
    assert objs[1] == b"<< /Type /Catalog /Pages 2 0 R >>"
    m = re.fullmatch(rb"<< /Type /Pages /Count (\d+) /Kids \[(.*)\] >>", objs[2])
    kids = [int(k) for k in re.findall(rb"(\d+) 0 R", m.group(2))]
    assert int(m.group(1)) == len(kids)
    pages = []
    for k in kids:
        m = re.fullmatch(rb"<< /Type /Page /Parent 2 0 R /MediaBox \[0 0 ([\d.]+) ([\d.]+)\] /Resources << /Font << /F1 3 0 R >> >> "
                         rb"/Contents (\d+) 0 R >>", objs[k])
        assert m, objs[k]
        pages.append({"box": (float(m.group(1)), float(m.group(2))), "ops": objs[int(m.group(3))].decode("ascii").split("\n")})
    assert b"/BaseFont /Helvetica" in objs[3]
    return pages


def _times(m, n):
    """Transform m, then n (each a, b, c, d, e, f as PDF and SVG write them)."""
    return (m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3], m[2] * n[0] + m[3] * n[2], m[2] * n[1] + m[3] * n[3],
            m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5])


def _at(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def pdf_shapes(ops: list[str]) -> tuple[list, list]:
    """What a page draws, in points from the page's bottom left: shapes as (how it is painted, line
    width, its outlines), each outline a list of ("M" | "L" | "C" | "Z", points); and its texts."""
    ctm, stack, shapes, texts, subs, width = (1, 0, 0, 1, 0, 0), [], [], [], [], 1.0
    for line in ops:
        if line.startswith("BT "):
            m = re.fullmatch(r"BT /F1 ([\d.]+) Tf ([\d.]+) Tc ([\d.]+) Tz 1 0 0 -1 ([\d.]+) ([\d.]+) Tm \((.*)\) Tj ET", line)
            assert m, line
            texts.append({"size": float(m.group(1)) * K, "at": _at(ctm, float(m.group(4)), float(m.group(5))), "raw": m.group(6),
                          "narrow": float(m.group(3)), "gap": float(m.group(2)) * float(m.group(3)) / 100})   # the gap between letters is narrowed too
            continue
        *nums, op = line.split(" ")
        v = [float(x) for x in nums]
        assert all(re.fullmatch(r"-?\d+(\.\d+)?", x) for x in nums), line   # plain numbers only
        if op == "q":
            stack.append((ctm, width))
        elif op == "Q":
            ctm, width = stack.pop()
        elif op == "cm":
            ctm = _times(v, ctm)
        elif op in ("g", "G"):
            assert v == [0], "films are pure black"
        elif op == "w":
            width = v[0] * abs(ctm[0])
        elif op == "m":
            subs.append([("M", [_at(ctm, *v)])])
        elif op == "l":
            subs[-1].append(("L", [_at(ctm, *v)]))
        elif op == "c":
            subs[-1].append(("C", [_at(ctm, v[0], v[1]), _at(ctm, v[2], v[3]), _at(ctm, v[4], v[5])]))
        elif op == "h":
            subs[-1].append(("Z", []))
        elif op == "re":
            x, y, w, h = v
            subs.append([("M", [_at(ctm, x, y)]), ("L", [_at(ctm, x + w, y)]), ("L", [_at(ctm, x + w, y + h)]), ("L", [_at(ctm, x, y + h)]), ("Z", [])])
        elif op in ("f", "f*", "S", "B", "B*"):
            shapes.append((op, width if op in ("S", "B", "B*") else None, subs))
            subs = []
        else:
            raise AssertionError("unexpected in the PDF: " + line)
    assert not stack and not subs
    return shapes, texts


# ------------------------------------------------------------------ reading the SVG film
_TOKEN = re.compile(r"[MLCHVZmlchvz]|-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?")


def _outlines(d: str, m) -> list:
    """An SVG path's outlines, as pdf_shapes gives them (transformed by m)."""
    subs, cur, start, toks, i = [], (0.0, 0.0), (0.0, 0.0), _TOKEN.findall(d), 0
    cmd = ""
    while i < len(toks):
        if toks[i].isalpha():
            cmd, i = toks[i], i + 1
        rel, c = cmd.islower(), cmd.upper()

        def take(n):
            nonlocal i
            out = [float(t) for t in toks[i:i + n]]
            i += n
            return out
        if c == "Z":
            subs[-1].append(("Z", []))
            cur = start
        elif c in "ML":
            x, y = take(2)
            cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
            if c == "M":
                subs.append([("M", [_at(m, *cur)])])
                start, cmd = cur, "l" if rel else "L"                      # more pairs after a move are lines
            else:
                subs[-1].append(("L", [_at(m, *cur)]))
        elif c in "HV":
            (n,) = take(1)
            cur = ((cur[0] + n if rel else n), cur[1]) if c == "H" else (cur[0], (cur[1] + n if rel else n))
            subs[-1].append(("L", [_at(m, *cur)]))
        elif c == "C":
            v = take(6)
            pts = [(v[j] + (cur[0] if rel else 0), v[j + 1] + (cur[1] if rel else 0)) for j in (0, 2, 4)]
            cur = pts[-1]
            subs[-1].append(("C", [_at(m, *p) for p in pts]))
        else:
            raise AssertionError("unexpected in the SVG path: " + cmd)
    return subs


def svg_shapes(svg: bytes) -> tuple[list, list]:
    """What one plate's SVG film draws, in the same form and units as pdf_shapes."""
    root = etree.fromstring(svg, etree.XMLParser(huge_tree=True))
    ph = float(root.get("viewBox").split()[3])
    shapes, texts = [], []

    def walk(el, m, st):
        st = {**st, **{k: el.get(k) for k in ("fill", "stroke", "stroke-width", "fill-rule") if el.get(k) is not None}}
        tag = etree.QName(el).localname
        if tag in ("svg", "g"):
            for name, args in re.findall(r"(\w+)\(([^)]*)\)", el.get("transform") or ""):
                a = [float(x) for x in args.split()]
                step = {"translate": lambda: (1, 0, 0, 1, a[0], a[1]), "scale": lambda: (a[0], 0, 0, a[-1], 0, 0)}[name]()
                m = _times(step, m)
            for child in el:
                walk(child, m, st)
            return
        fill, stroke = st.get("fill", "#000") != "none", st.get("stroke", "none") != "none"
        even = "*" if st.get("fill-rule") == "evenodd" else ""
        op = ("B" + even) if fill and stroke else "S" if stroke else ("f" + even)
        width = float(st.get("stroke-width", 1)) * K if stroke else None
        if tag == "path":
            subs = _outlines(el.get("d"), m)
            if subs:
                shapes.append((op, width, subs))
        elif tag == "rect":
            x, y, w, h = (float(el.get(k)) for k in ("x", "y", "width", "height"))
            shapes.append((op, width, [[("M", [_at(m, x, y)]), ("L", [_at(m, x + w, y)]), ("L", [_at(m, x + w, y + h)]),
                                        ("L", [_at(m, x, y + h)]), ("Z", [])]]))
        elif tag == "text":
            texts.append({"size": float(el.get("font-size")) * K, "at": _at(m, float(el.get("x")), float(el.get("y"))), "text": el.text})
        else:
            raise AssertionError("unexpected in the SVG film: " + tag)

    walk(root, (K, 0, 0, -K, 0, ph * K), {})                               # millimetres from the top left -> points from the bottom left
    return shapes, texts


def _closed_plainly(subs: list) -> list:
    """Outlines without a last line back to their start just before closing (closing draws it anyway)."""
    return [s[:-2] + s[-1:] if len(s) > 2 and s[-1][0] == "Z" and s[-2][0] == "L" and np.allclose(s[-2][1][0], s[0][1][0], atol=1e-9) else s
            for s in subs]


def same_shapes(a: list, b: list, tol=2e-3) -> None:
    """Shape for shape, outline for outline, point for point (to 2/1000 of a point: under a micron)."""
    assert len(a) == len(b)
    for (op_a, w_a, subs_a), (op_b, w_b, subs_b) in zip(a, b):
        subs_a, subs_b = _closed_plainly(subs_a), _closed_plainly(subs_b)
        assert op_a == op_b and (w_a is None) == (w_b is None) and (w_a is None or abs(w_a - w_b) < tol)
        assert [[k for k, _ in s] for s in subs_a] == [[k for k, _ in s] for s in subs_b]
        pa = np.array([p for s in subs_a for _, pts in s for p in pts])
        pb = np.array([p for s in subs_b for _, pts in s for p in pts])
        assert pa.shape == pb.shape and (pa.size == 0 or np.abs(pa - pb).max() < tol)


def films(job) -> list[bytes]:
    z = zipfile.ZipFile(io.BytesIO(pg.plates_zip(job)))
    return [z.read(n) for n in z.namelist()]


# ------------------------------------------------------------------ the tests
def _jobs(color, lineart, tmp_path):
    """Plates of every kind Boot Tool makes (coarse ones, to keep this quick)."""
    small = tmp_path / "small.png"
    Image.open(color).resize((240, 160)).save(small)
    yield "flat colours", sep.build(color, sep.SepParams(inks=4), pg.PageOpts())
    yield "halftone", ht.build(lineart, ht.HalftoneParams(lpi=12, ppi=150), pg.PageOpts(size="A5", orientation="portrait"))
    yield "one-ink dither on a dark shirt", pr.build(small, pr.coerce_print({"render": "dither", "dither_size": 1.0, "dark": True, "underbase": True}),
                                                     pg.PageOpts(size="A3", mark_layout="corners"))
    yield "CMYK at a set size", pr.build(small, pr.coerce_print({"method": "cmyk", "lpi": 8, "ppi": 100}),
                                         pg.PageOpts(art_w=400.0, size="Custom", custom_w=520.0, custom_h=380.0))


def test_every_shape_of_every_plate_is_in_the_pdf_where_the_svg_film_has_it(color, lineart, tmp_path):
    """One page per plate, the page's real size, and on it exactly the film: the artwork, the
    registration marks and the plate's name, point for point where the SVG has them."""
    kinds = set()
    for what, job in _jobs(color, lineart, tmp_path):
        pages = read_pdf(pdf.plates_pdf(job))
        assert len(pages) == len(job.plates) >= 1, what
        for page, film, plate in zip(pages, films(job), job.plates):
            assert page["box"] == pytest.approx((job.page[0] * K, job.page[1] * K), abs=1e-6), what
            shapes, texts = pdf_shapes(page["ops"])
            want, want_texts = svg_shapes(film)
            assert len(want) > 6 and sum(len(s) for _, _, s in want) > 12, what   # artwork and marks, not an empty film
            same_shapes(shapes, want)
            kinds |= {(op, k) for op, _, subs in shapes for s in subs for k, _ in s}
            (t,), (w,) = texts, want_texts
            assert t["at"] == pytest.approx(w["at"], abs=1e-3) and t["size"] == pytest.approx(w["size"]), what
            assert t["raw"].replace("\\227", "—") == w["text"] == f"PLATE {job.plates.index(plate) + 1} — {plate.name.upper()}"
    assert {("f*", "C"), ("f*", "L"), ("f", "L"), ("S", "L")} <= kinds        # curves and straight outlines, both ways of filling, and lines


def test_the_plate_name_in_the_pdf():
    """The name is written so that brackets and backslashes in it cannot break the file; letters the
    PDF's built-in font does not have become "?"."""
    def job(name, **opts):
        return pg.Job(opts=pg.PageOpts(**opts), art=(20, 20, 50, 50), plates=[pg.Plate(name, "#ff0000", '<rect x="20" y="20" width="50" height="50"/>')])
    def texts(j):
        (page,) = read_pdf(pdf.plates_pdf(j))
        return pdf_shapes(page["ops"])[1]
    def raw(j):
        return [t["raw"] for t in texts(j)]
    (t,) = texts(job("Red"))
    assert t["narrow"] == 82 and t["gap"] == pytest.approx(0.3, abs=1e-3)   # narrowed like the SVG's Arial Narrow, letters 0.3 mm apart as there
    assert raw(job('Red (5") \\ & <trim>')) == ['PLATE 1 \\227 RED \\(5"\\) \\\\ & <TRIM>']
    assert raw(job("Zürich Красный 赤")) == ["PLATE 1 \\227 Z\\334RICH ??????? ?"]
    assert raw(job("Red", ink_names=False)) == []
    with_marks, without = (pdf_shapes(read_pdf(pdf.plates_pdf(job("Red", marks=m)))[0]["ops"])[0] for m in (True, False))
    assert [op for op, _, _ in without] == ["f"] and [op for op, _, _ in with_marks] == ["f"] + ["S", "S"] * 6
    assert with_marks[1][1] == pytest.approx(2.0, abs=0.01)                       # the marks' lines are 2 points thick


def test_one_pdf_per_plate_in_a_zip(color):
    job = sep.build(color, sep.SepParams(inks=3), pg.PageOpts())
    z = zipfile.ZipFile(io.BytesIO(pdf.plates_pdf_zip(job, "badge")))
    svgs = zipfile.ZipFile(io.BytesIO(pg.plates_zip(job, "badge"))).namelist()
    assert z.namelist() == [n[:-4] + ".pdf" for n in svgs] and len(svgs) == len(job.plates) > 1
    together = read_pdf(pdf.plates_pdf(job))
    for i, n in enumerate(z.namelist()):
        (page,) = read_pdf(z.read(n))
        assert page == together[i]                                         # the same film as that page of the one-file PDF
    assert pdf.plates_pdf(job) == pdf.plates_pdf(job)                      # nothing in the file changes from one time to the next


def test_a_film_that_cannot_be_written_as_pdf_stops_with_an_error():
    """Nothing is left out of a film without a word: a shape or colour this does not know is an error."""
    def job(body):
        return pg.Job(opts=pg.PageOpts(), art=(20, 20, 50, 50), plates=[pg.Plate("Ink", "#000000", body)])
    for body, said in (('<circle cx="30" cy="30" r="5"/>', "circle"), ('<path fill="#ff0000" d="M0 0L5 0L5 5Z"/>', "black"),
                       ('<g transform="rotate(30)"><path d="M0 0L5 0L5 5Z"/></g>', "transform")):
        with pytest.raises(ValueError, match=said):
            pdf.plates_pdf(job(body))
    with pytest.raises(ValueError, match="no plates"):
        pdf.plates_pdf(pg.Job(opts=pg.PageOpts(), art=(20, 20, 50, 50)))
    with pytest.raises(ValueError, match="Plate 1 could not be written as PDF. If its ink name"):   # a character no file can hold
        pdf.plates_pdf(pg.Job(opts=pg.PageOpts(), art=(20, 20, 50, 50), plates=[pg.Plate("Red\x01", "#ff0000", "")]))
    assert pdf._r(1e-7) == "0" and pdf._r(-1e-7) == "0" and pdf._r(12.50004) == "12.5" and pdf._m(0.084667) == "0.084667"
    (page,) = read_pdf(pdf.plates_pdf(job("")))                            # a plate with no ink on it is still a page
    assert [op for op, _, _ in pdf_shapes(page["ops"])[0]] == ["S", "S"] * 6


def test_the_pdf_looks_like_the_svg_in_a_real_pdf_reader(color, tmp_path):
    """Drawn by a PDF reader that is not ours (poppler), each page covers what the SVG film covers."""
    if not (shutil.which("pdftoppm") and shutil.which("rsvg-convert")):
        pytest.skip("pdftoppm or rsvg-convert not installed")
    import cv2
    job = pr.build(color, pr.coerce_print({"method": "spot", "kind": "flat"}), pg.PageOpts())
    (tmp_path / "p.pdf").write_bytes(pdf.plates_pdf(job))
    subprocess.run(["pdftoppm", "-r", "200", "-gray", str(tmp_path / "p.pdf"), str(tmp_path / "page")], check=True)
    pages = sorted(tmp_path.glob("page-*.pgm"))
    assert len(pages) == len(job.plates) > 1
    for i, f in enumerate(pages):
        a = (np.array(Image.open(f)) < 128).astype(np.uint8)
        (tmp_path / "s.svg").write_text(pg.compose(job, i), encoding="utf-8")
        subprocess.run(["rsvg-convert", "-w", str(a.shape[1]), "-h", str(a.shape[0]), "-b", "white", "-o", str(tmp_path / "s.png"), str(tmp_path / "s.svg")], check=True)
        b = (np.array(Image.open(tmp_path / "s.png").convert("L")) < 128).astype(np.uint8)
        mm = 200 / 25.4
        for im in (a, b):
            im[int(8 * mm):int(16 * mm), int(21 * mm):int(140 * mm)] = 0   # not the plate's name (the two use different fonts)
        edge = cv2.dilate(b, np.ones((3, 3), np.uint8)) != cv2.erode(b, np.ones((3, 3), np.uint8))
        assert b.sum() > 20000 and int(((a != b) & ~edge).sum()) == 0


@pytest.fixture
def server():
    srv = make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _post(url, data, ctype="application/json"):
    req = urllib.request.Request(url, data=data if isinstance(data, bytes) else json.dumps(data).encode(), method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_downloading_pdfs_from_boot_tool(server, color):
    iid = json.loads(_post(server + "/api/upload", color.read_bytes(), "application/octet-stream")[1])["id"]
    req = {"id": iid, "tool": "separate", "params": {"inks": 3}, "page": {"size": "A5", "orientation": "portrait"}}
    plates = json.loads(_post(server + "/api/plates", {**req, "session": "t", "seq": 1, "view": "all"})[1])["plates"]
    code, body, h = _post(server + "/api/download", {**req, "kind": "pdf"})
    assert code == 200 and h["Content-Type"] == "application/pdf"
    pages = read_pdf(body)
    assert len(pages) == len(plates) > 1 and pages[0]["box"] == pytest.approx((148 * K, 210 * K), abs=1e-6)
    code, body, h = _post(server + "/api/download", {**req, "kind": "pdfzip"})
    assert code == 200 and h["Content-Type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(body))
    assert len(z.namelist()) == len(plates) and all(n.endswith(".pdf") for n in z.namelist())
    assert [read_pdf(z.read(n))[0] for n in z.namelist()] == pages
    code, body, h = _post(server + "/api/download", {**req, "kind": "svg"})   # the SVG downloads are as they were
    assert code == 200 and h["Content-Type"] == "image/svg+xml" and body.startswith(b"<svg")
    assert _post(server + "/api/download", {**req, "kind": "zip"})[2]["Content-Type"] == "application/zip"


def test_the_pdf_buttons_in_the_browser(server, color, tmp_path):
    """Print has two more download buttons; each gives a file named after the picture."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        one, zipped = "button:text-is('PDF — one page per plate')", "button:text-is('All plates — .zip, one PDF each')"
        page.click('a[data-t="print"]')
        page.wait_for_selector(one)
        assert page.is_disabled(one) and page.is_disabled(zipped)          # nothing to download yet
        page.set_input_files("#file", str(color))
        page.wait_for_function("lastRes && lastRes.plates && lastRes.plates.length > 0", timeout=120000)
        page.wait_for_function("(b => b.length === 2 && b.every(x => !x.disabled))([...document.querySelectorAll('button')].filter(x => x.textContent.includes('PDF')))", timeout=20000)
        note = "document.querySelector('#toast').textContent"
        held = []
        page.route("**/api/download", lambda route: held.append(route))    # Boot Tool's answer is held back for a moment
        page.click(one)
        page.wait_for_function(f"{note} === 'Making the PDF…'")
        page.click(zipped)                                                 # a click while one is being made does not start another
        page.wait_for_function(f"{note} === 'Still making the PDF…'")
        page.wait_for_timeout(300)
        assert len(held) == 1 and held[0].request.post_data_json["kind"] == "pdf"
        with page.expect_download(timeout=120000) as d:
            held[0].continue_()
        page.unroute("**/api/download")
        assert d.value.suggested_filename == "color-print-plates.pdf"
        d.value.save_as(tmp_path / "one.pdf")
        assert len(read_pdf((tmp_path / "one.pdf").read_bytes())) == page.evaluate("lastRes.plates.length")
        page.wait_for_function("document.querySelector('#toast').textContent === 'PDF downloaded.'", timeout=20000)
        with page.expect_download(timeout=120000) as d:
            page.click(zipped)
        assert d.value.suggested_filename == "color-print-plates-pdf.zip"
        d.value.save_as(tmp_path / "all.zip")
        assert [n[-4:] for n in zipfile.ZipFile(tmp_path / "all.zip").namelist()] == [".pdf"] * page.evaluate("lastRes.plates.length")
        assert not errs, errs
        b.close()
