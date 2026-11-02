"""Library, fonts, text outlines and custom page size (the server side of Compose), plus a browser smoke test."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from boottool import fonts, library
from boottool import page as pg
from boottool.ui import make_server

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10"><rect width="5" height="5"/></svg>'


@pytest.fixture
def server():
    srv = make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _post(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_library_keys_overwrite_and_names_are_unique():
    a = library.save("My Star!", SVG, key="s1:img1")
    assert a == "My Star_.svg" and library.save("My Star!", SVG.replace("5", "6"), key="s1:img1") == a
    assert b"6" in library.read(a)
    assert library.save("My Star!", SVG) == "My Star_-2.svg"                       # no key: never overwrites
    assert {i["name"] for i in library.listing()} == {"My Star_.svg", "My Star_-2.svg"}
    new = library.rename(a, "Badge")
    assert new == "Badge.svg" and library.save("x", SVG, key="s1:img1") == "Badge.svg"   # key follows the rename
    library.delete(new)
    assert "Badge.svg" not in {i["name"] for i in library.listing()}


def test_library_rejects_bad_names_and_non_svg():
    with pytest.raises(ValueError):
        library.save("x", "hello")
    for bad in ("../x.svg", "a/b.svg", ".hidden.svg", "x.txt", ""):
        with pytest.raises(ValueError):
            library.read(bad)
    with pytest.raises(KeyError):
        library.read("missing.svg")


def test_library_endpoints(server):
    code, j = _post(server + "/api/library/save", {"name": "Wave", "svg": SVG, "key": "k"})
    assert code == 200 and j["name"] == "Wave.svg"
    code, body, h = _get(server + "/api/library")
    assert [i["name"] for i in json.loads(body)["items"]] == ["Wave.svg"]
    code, body, h = _get(server + "/api/library/file?name=Wave.svg")
    assert code == 200 and h["Content-Type"] == "image/svg+xml" and b"<rect" in body
    assert _get(server + "/api/library/file?name=../../etc/passwd")[0] == 404
    assert _post(server + "/api/library/rename", {"name": "Wave.svg", "to": "Surf"})[1]["name"] == "Surf.svg"
    assert _post(server + "/api/library/save", {"name": "x", "svg": "nope"})[0] == 400
    assert _post(server + "/api/library/delete", {"name": "Surf.svg"})[0] == 200
    assert json.loads(_get(server + "/api/library")[1])["items"] == []


def test_static_files_are_limited_to_the_known_scripts(server):
    assert _get(server + "/static/compose.js")[0] == 200
    code, body, _ = _get(server + "/static/vendor/paper-core.js")
    assert code == 200 and b"Paper.js" in body[:400]
    assert _get(server + "/static/../ui.py")[0] == 404 and _get(server + "/static/index.html")[0] == 404


def _make_font(path):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A", "space"])
    fb.setupCharacterMap({65: "A", 32: "space"})
    pen = TTGlyphPen(None)
    pen.moveTo((100, 0)); pen.lineTo((100, 700)); pen.lineTo((500, 700)); pen.lineTo((500, 0)); pen.closePath()
    glyphs = {".notdef": TTGlyphPen(None).glyph(), "A": pen.glyph(), "space": TTGlyphPen(None).glyph()}
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({".notdef": (500, 0), "A": (600, 100), "space": (300, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Test Sans", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200)
    fb.setupPost()
    fb.save(str(path))


def test_fonts_are_found_and_text_becomes_outlines(tmp_path):
    _make_font(fonts.user_folder() / "TestSans.ttf")
    lst = fonts.listing(refresh=True)
    f = next(x for x in lst if x["family"] == "Test Sans")
    one = fonts.outline(f["id"], "A", 10)
    assert one["d"].startswith("M") and one["d"].endswith("Z")
    assert abs(one["w"] - 6.0) < 0.01 and abs(one["h"] - 10.0) < 0.01          # 600/1000 em wide, ascent+descent tall
    two = fonts.outline(f["id"], "A A", 10, spacing=100)
    assert two["w"] > one["w"] * 2.5
    lines = fonts.outline(f["id"], "A\nA", 10)
    assert lines["h"] > one["h"] * 1.5
    with pytest.raises(ValueError, match="Type some text"):
        fonts.outline(f["id"], "  ", 10)
    with pytest.raises(KeyError):
        fonts.outline(99999, "A", 10)


def test_text_endpoint(server):
    _make_font(fonts.user_folder() / "TestSans.ttf")
    fid = next(x for x in fonts.listing(refresh=True) if x["family"] == "Test Sans")["id"]
    code, j = _post(server + "/api/text", {"font": fid, "text": "AA", "size": 20, "align": "center"})
    assert code == 200 and j["d"].count("M") == 2 and j["w"] > 20
    assert _post(server + "/api/text", {"font": fid, "text": "", "size": 20})[0] == 400


def test_custom_page_size():
    o = pg.coerce_page({"size": "Custom", "custom_w": 353, "custom_h": 266})
    assert o.dims() == (353.0, 266.0)
    assert pg.coerce_page({"size": "Custom", "custom_w": 9999}).custom_w == 2000
    assert pg.coerce_page({"size": "A4"}).dims() == (297.0, 210.0)
    assert len(pg.mark_positions(o)) == 6


def test_batch_results_also_land_in_the_library(server, lineart):
    iid = json.loads(urllib.request.urlopen(urllib.request.Request(server + "/api/upload", data=lineart.read_bytes(), method="POST")).read())["id"]
    code, j = _post(server + "/api/batch/start", {"items": [{"id": iid, "name": "plate one.png", "params": {}}], "keep_going": True})
    assert code == 200
    import time
    for _ in range(100):
        s = json.loads(_get(server + f"/api/batch/status?job={j['job']}")[1])
        if s.get("finished"):
            break
        time.sleep(0.2)
    assert any(i["name"].startswith("plate one") for i in library.listing())


def test_compose_in_the_browser(server):
    sync_api = pytest.importorskip("playwright.sync_api")
    library.save("badge", '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
                          '<circle cx="50" cy="50" r="45" fill="#c8102e"/><rect x="30" y="30" width="40" height="40" fill="#1d3f8f"/></svg>')
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")
        page.hover("#clib .ltab")
        page.click("#clibStrip .lt")
        page.wait_for_function("Compose._api.getL().art.children.length === 1")
        # recolour the circle, then cut the badge in two with a line
        page.evaluate("Compose._api.select([Compose._api.getL().art.children[0]])")
        page.evaluate("Compose._api.applyColour('fill', '#00a651')")
        assert page.evaluate("Compose._api.leaves(Compose._S.sel).map(l => l.fillColor.toCSS(true))[0]") == "#00a651"
        page.evaluate("Compose._api.sendToPrint()")
        page.wait_for_function("document.querySelector('#nav a.on').dataset.t === 'print'", timeout=60000)
        assert page.evaluate("[PG.size, PG.custom_w, PG.custom_h]") == ["Custom", 353, 266]
        assert not errs
        b.close()


def test_compose_send_rename_and_eyedropper(server):
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")
        assert page.query_selector('[data-ct="cut"]') is None
        page.evaluate("""(() => { const L = Compose._api.getL();
          L.art.addChild(new paper.Path.Rectangle({point: [40, 40], size: [100, 100], fillColor: '#c8102e'}));
          L.art.addChild(new paper.Path.Circle({center: [220, 100], radius: 40, fillColor: '#000000'})); Compose._api.commit(); })()""")
        page.evaluate("Compose._api.sendToPrint()")
        page.wait_for_function("document.querySelector('#nav a.on').dataset.t === 'print'", timeout=60000)
        page.wait_for_function("lastPalette.length > 0", timeout=120000)
        page.click("#nmEdit")
        page.keyboard.type("my badge")
        page.keyboard.press("Enter")
        assert page.evaluate("fileName") == "my badge"
        page.evaluate("eyeStart(0)")
        assert page.evaluate("EYE.on && P.print.picked")
        page.keyboard.press("Escape")
        assert page.evaluate("!EYE.on")
        assert not errs, errs
        b.close()


def test_type_on_the_page_layer_thumbnails_and_shortcuts(server):
    sync_api = pytest.importorskip("playwright.sync_api")
    _make_font(fonts.user_folder() / "TestSans.ttf")
    fonts.listing(refresh=True)
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")
        assert page.inner_text('[data-ct="pen"] i') == "P"
        page.click('[data-ct="text"]')
        page.wait_for_function("Compose._S.fonts && Compose._S.fonts.length")
        box = page.eval_on_selector("#ccanvas", "e => { const r = e.getBoundingClientRect(); return [r.x + 200, r.y + 150]; }")
        page.mouse.click(*box)
        page.wait_for_selector("#cwrap textarea")
        page.wait_for_function("document.activeElement && document.activeElement.tagName === 'TEXTAREA'")
        page.keyboard.type("AA")
        page.keyboard.press("Escape")
        page.wait_for_function("Compose._api.getL().art.children.length === 1", timeout=20000)
        assert page.evaluate("Compose._api.getL().art.children[0].data.text.text") == "AA"
        # layers show a thumbnail and the selected row is highlighted
        page.wait_for_selector("#clayers .lyr.on .th svg")
        # double-click the text with the select tool to edit it in place
        page.click('[data-ct="select"]')
        c = page.evaluate("(() => { const it = Compose._api.getL().art.children[0]; const p = paper.view.projectToView(it.bounds.leftCenter.add([it.bounds.width * 0.1, 0])); const r = document.querySelector('#ccanvas').getBoundingClientRect(); return [r.x + p.x, r.y + p.y]; })()")
        page.mouse.dblclick(*c)
        page.wait_for_selector("#cwrap textarea")
        page.wait_for_function("document.activeElement && document.activeElement.tagName === 'TEXTAREA'")
        page.keyboard.type("A")
        page.keyboard.press("Escape")
        page.wait_for_function("Compose._api.getL().art.children[0].data.text.text.length === 3 || Compose._api.getL().art.children[0].data.text.text.includes('AAA')", timeout=20000)
        assert not errs, errs
        b.close()


def test_groups_and_fill_tool_in_the_browser(server):
    sync_api = pytest.importorskip("playwright.sync_api")
    ring = '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="60" viewBox="0 0 60 60"><path fill-rule="evenodd" d="M5 5H55V55H5ZM15 15V45H45V15Z"/></svg>'
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        pg_ = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        pg_.on("pageerror", lambda e: errors.append(str(e)))
        pg_.goto(server); pg_.click('#nav a[data-t="compose"]'); pg_.wait_for_timeout(500)
        pg_.evaluate("s => { Compose._api.addSvg(s, 'One'); Compose._api.addSvg(s, 'Two'); }", ring)
        # group two graphics: one new row with a caret, children listed, then ungroup
        pg_.evaluate("() => { const L = Compose._api.getL(); Compose._api.select(L.art.children.slice()); Compose._api.group(); }")
        assert pg_.evaluate("Compose._api.getL().art.children.length") == 1
        assert pg_.locator("#clayers .lyr").count() == 3          # the group and its two children
        pg_.keyboard.press("Control+Shift+G")
        assert pg_.evaluate("Compose._api.getL().art.children.length") == 2
        pg_.evaluate("() => { const L = Compose._api.getL(); Compose._api.select(L.art.children.slice()); }")
        pg_.keyboard.press("Control+G")
        assert pg_.evaluate("Compose._api.getL().art.children.length") == 1
        pg_.keyboard.press("Control+Shift+G")
        pg_.evaluate("() => { const L = Compose._api.getL(); L.art.children[1].remove(); Compose._api.commit(); Compose._api.select([]); }")
        # Fill: click the hollow middle of the ring
        pg_.keyboard.press("f")
        assert pg_.locator("[data-ct=fill].on").count() == 1
        c = pg_.evaluate("() => { const it = Compose._api.getL().art.children[0], b = it.bounds; return [b.center.x, b.center.y]; }")
        pg_.evaluate("([x, y]) => Compose._api.fillAt(new paper.Point(x, y), false)", c)
        pg_.wait_for_function("Compose._api.getL().art.children.length === 2")
        info = pg_.evaluate("() => { const L = Compose._api.getL(), f = L.art.children[0], r = L.art.children[1]; return [f.data.kind, f.fillColor.toCSS(true), f.index, r.index, f.bounds.width, r.bounds.width]; }")
        assert info[0] == "fill" and info[1] == "#ffffff" and info[2] == 0 and info[3] == 1      # behind the ring
        assert info[5] * 0.6 < info[4] < info[5] * 0.6 + 1.0                                           # the hole is half the ring wide, grown a little
        # clicking the fill again recolours it; clicking outside the ring says it is open
        pg_.evaluate("([x, y]) => Compose._api.fillAt(new paper.Point(x, y), false)", c)
        assert pg_.evaluate("Compose._api.getL().art.children.length") == 2
        pg_.evaluate("() => Compose._api.fillAt(new paper.Point(4, 4), false)")
        pg_.wait_for_function("document.querySelector('#toast') && /open to the outside/.test(document.querySelector('#toast').textContent)")
        assert not errors, errors
        b.close()
