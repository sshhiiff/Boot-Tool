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


def _make_font(path, family="Test Sans", advance=600, space=300, notdef=500):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A", "space"])
    fb.setupCharacterMap({65: "A", 32: "space"})
    pen = TTGlyphPen(None)
    pen.moveTo((100, 0)); pen.lineTo((100, 700)); pen.lineTo((500, 700)); pen.lineTo((500, 0)); pen.closePath()
    glyphs = {".notdef": TTGlyphPen(None).glyph(), "A": pen.glyph(), "space": TTGlyphPen(None).glyph()}
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({".notdef": (notdef, 0), "A": (advance, 100), "space": (space, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
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


def test_edited_text_keeps_its_own_font(server):
    """Picking a font for new text must not redraw existing text in it; picking a font for selected text does."""
    _make_font(fonts.user_folder() / "TestSans.ttf")
    _make_font(fonts.user_folder() / "TestWide.ttf", family="Test Wide", advance=1200)
    fonts.listing(refresh=True)
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")
        w = page.evaluate("""async () => {
          const A = Compose._api, S = Compose._S, L = A.getL(), wait = () => new Promise(r => setTimeout(r, 1200));
          const j = await (await fetch('/api/fonts')).json(); S.fonts = j.fonts;
          const narrow = S.fonts.find(f => f.family === 'Test Sans'), wide = S.fonts.find(f => f.family === 'Test Wide');
          S.font = narrow; S.text.text = 'AAAA';
          await A.addText(new paper.Point(20, 20));
          const t = L.art.lastChild, own = t.bounds.width;
          S.font = wide;                                   // the font chosen for the NEXT text
          A.retext(t, {spacing: 0}); await wait();         // then the first text is edited without touching its font
          const kept = L.art.lastChild.bounds.width;
          A.select([L.art.lastChild]);
          return {own, kept, font: L.art.lastChild.data.text.font, narrow: narrow.id, wide: wide.id};
        }""")
        assert abs(w["kept"] - w["own"]) < 0.01 and w["font"] == w["narrow"], w      # used to be redrawn in Test Wide
        page.select_option("#side select", str(w["wide"]))                         # choosing a font for the selected text still works
        page.wait_for_timeout(1200)
        wider = page.evaluate("[Compose._api.getL().art.lastChild.bounds.width, Compose._api.getL().art.lastChild.data.text.font]")
        assert wider[0] > w["own"] * 1.5 and wider[1] == w["wide"], wider
        assert not errs, errs
        b.close()


def test_a_font_keeps_its_id_when_other_fonts_come_and_go(server):
    """Font ids used to be positions in the list, so a new font that sorts earlier moved every id on."""
    _make_font(fonts.user_folder() / "TestSans.ttf")
    _make_font(fonts.user_folder() / "TestWide.ttf", family="Test Wide", advance=1200)
    before = {f["family"]: f["id"] for f in fonts.listing(refresh=True)}
    wide = fonts.outline(before["Test Wide"], "AA", 10)
    _make_font(fonts.user_folder() / "First.ttf", family="A First Font", advance=300)   # sorts before both
    after = {f["family"]: f["id"] for f in fonts.listing(refresh=True)}
    assert len(after) == 3 and len(set(after.values())) == 3
    assert {k: after[k] for k in before} == before
    assert fonts.outline(before["Test Wide"], "AA", 10) == wide          # the same id is still the same font
    fonts._FONTS = None                                                  # as after a restart of Boot Tool
    assert {f["family"]: f["id"] for f in fonts.listing()} == after
    code, j = _post(server + "/api/text", {"font": before["Test Wide"], "text": "AA", "size": 10})
    assert code == 200 and j == wide
    (fonts.user_folder() / "TestWide.ttf").unlink()                      # a font that is taken away
    fonts.listing(refresh=True)
    code, j = _post(server + "/api/text", {"font": before["Test Wide"], "text": "AA", "size": 10})
    assert code == 400 and "not installed" in j["error"]                 # says so, and never draws another font instead
    assert fonts.outline(before["Test Sans"], "AA", 10)["w"] < wide["w"]


def test_two_fonts_with_one_name_are_never_swapped_for_each_other():
    """Two versions of a font have the same name. If the one in use is taken away, text set in it must
    not quietly be drawn in the other; and a font file that is replaced is read again."""
    import shutil
    d = fonts.user_folder()
    (d / "a").mkdir()
    (d / "b").mkdir()
    _make_font(d / "a" / "Sans.ttf")                                     # the one listed (first by folder name)
    _make_font(d / "b" / "Sans.ttf", advance=1200)                       # another version: same name, wider letters
    one = [f for f in fonts.listing(refresh=True) if f["family"] == "Test Sans"]
    assert len(one) == 1
    narrow = fonts.outline(one[0]["id"], "AA", 10)
    shutil.copy(d / "a" / "Sans.ttf", d / "c.ttf")                       # (a copy of the same file is the same font)
    (d / "a" / "Sans.ttf").unlink()
    (d / "c.ttf").replace(d / "a" / "Copy.ttf")
    assert [f["id"] for f in fonts.listing(refresh=True) if f["family"] == "Test Sans"] == [one[0]["id"]]
    assert fonts.outline(one[0]["id"], "AA", 10) == narrow
    (d / "a" / "Copy.ttf").unlink()                                      # now only the other version is left
    other = [f for f in fonts.listing(refresh=True) if f["family"] == "Test Sans"]
    assert len(other) == 1 and other[0]["id"] != one[0]["id"]
    with pytest.raises(KeyError, match="not installed"):
        fonts.outline(one[0]["id"], "AA", 10)                            # used to come back in the wider version
    assert fonts.outline(other[0]["id"], "AA", 10)["w"] > narrow["w"] * 1.5

    fonts.listing(refresh=True)
    _make_font(d / "b" / "Sans.ttf", family="Test Other", advance=300)   # the file is replaced by another font
    with pytest.raises(KeyError, match="not installed"):
        fonts.outline(other[0]["id"], "AA", 10)                          # (before the next refresh: not the new file under the old id)
    new = next(f for f in fonts.listing(refresh=True) if f["family"] == "Test Other")
    assert fonts.outline(new["id"], "AA", 10)["w"] < narrow["w"]         # used to be drawn from the old file


def _make_collection(path, advance, scratch):
    """A font collection (.ttc) holding one Test Sans. Like many collections it carries no checksum of
    the whole font, and these are given the same dates, so only what is in them tells two apart."""
    from fontTools.ttLib import TTCollection, TTFont
    _make_font(scratch / "one.ttf", advance=advance)
    f = TTFont(scratch / "one.ttf", recalcTimestamp=False)
    f["head"].created = f["head"].modified = 0
    c = TTCollection()
    c.fonts = [f]
    c.save(str(path))
    c = TTCollection(str(path), lazy=True)
    at = c.fonts[0].reader.tables["head"].offset + 8                     # head.checkSumAdjustment
    c.close()
    data = bytearray(path.read_bytes())
    data[at:at + 4] = bytes(4)
    path.write_bytes(bytes(data))


def test_another_version_of_a_font_in_use_changes_nothing_for_its_text(tmp_path):
    """Text is set in a font. Another version of that font (same name) is installed, in a file that
    comes first. The text's own font is still there, so it must neither be drawn in the new version
    nor be called missing."""
    d = fonts.user_folder()
    _make_collection(d / "Normal.ttc", 600, tmp_path)
    mine = [f for f in fonts.listing(refresh=True) if f["family"] == "Test Sans"]
    assert len(mine) == 1 and fonts.others() == []
    narrow = fonts.outline(mine[0]["id"], "AA", 10)
    _make_collection(d / "ATest.ttc", 1200, tmp_path)                    # the other version: wider letters, nothing else
    listed = [f for f in fonts.listing(refresh=True) if f["family"] == "Test Sans"]
    assert len(listed) == 1 and listed[0]["id"] != mine[0]["id"]         # one entry in the list, the new version
    assert fonts.others() == mine                                        # and the one in use is still known
    assert fonts.outline(mine[0]["id"], "AA", 10) == narrow              # and still drawn as before
    assert fonts.outline(listed[0]["id"], "AA", 10)["w"] > narrow["w"] * 1.5


def test_text_is_only_ever_redrawn_from_the_very_font_it_was_drawn_from(server, monkeypatch):
    """Two different fonts can look the same to the list: the same name, and tables with the same
    checksums and lengths (here the widths of the space and of one other glyph are swapped). Text
    keeps the one it was drawn from, by its exact contents."""
    from fontTools.ttLib import TTFont
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")               # both made "at the same moment"
    d = fonts.user_folder()
    (d / "n").mkdir()
    (d / "a").mkdir()
    _make_font(d / "n" / "Mine.ttf", space=300, notdef=500)
    mine = [f for f in fonts.listing(refresh=True) if f["family"] == "Test Sans"]
    drawn = fonts.outline(mine[0]["id"], "A A", 10)
    assert abs(drawn["w"] - 15.0) < 0.01 and len(drawn["face"]) == 40
    _make_font(d / "a" / "Other.ttf", space=500, notdef=300)             # comes first in the folder
    assert fonts._face(TTFont(d / "a" / "Other.ttf")) == fonts._face(TTFont(d / "n" / "Mine.ttf"))   # (they do look the same)
    assert fonts.catalog(refresh=True) == {"fonts": mine, "others": []}
    assert fonts.outline(mine[0]["id"], "A A", 10, face=drawn["face"]) == drawn   # used to come back 17 mm wide, from the other one
    code, j = _post(server + "/api/text", {"font": mine[0]["id"], "face": drawn["face"], "text": "A A", "size": 10})
    assert code == 200 and j == drawn
    assert abs(fonts.outline(mine[0]["id"], "A A", 10)["w"] - 17.0) < 0.01   # (new text gets the one that comes first)
    (d / "n" / "Mine.ttf").unlink()                                      # its own font is taken away
    fonts.listing(refresh=True)
    code, j = _post(server + "/api/text", {"font": mine[0]["id"], "face": drawn["face"], "text": "A A", "size": 10})
    assert code == 400 and "not installed" in j["error"]


def test_a_font_packed_another_way_is_still_the_same_font(tmp_path):
    """The same font, once as a .ttf and once inside a collection (.ttc): text set in one is drawn
    from the other without a word."""
    from fontTools.ttLib import TTCollection, TTFont
    d = fonts.user_folder()
    _make_font(d / "Sans.ttf")
    mine = [f for f in fonts.listing(refresh=True) if f["family"] == "Test Sans"]
    drawn = fonts.outline(mine[0]["id"], "AA", 10)
    c = TTCollection()
    c.fonts = [TTFont(d / "Sans.ttf", recalcTimestamp=False, recalcBBoxes=False)]
    c.save(str(tmp_path / "Sans.ttc"))
    (d / "Sans.ttf").unlink()
    (tmp_path / "Sans.ttc").replace(d / "Sans.ttc")
    assert [f for f in fonts.listing(refresh=True) if f["family"] == "Test Sans"] == mine
    assert fonts.outline(mine[0]["id"], "AA", 10, face=drawn["face"]) == drawn   # used to be "not installed any more"


def _text_width(page):
    return page.evaluate("Compose._api.getL().art.lastChild.bounds.width")


def _wait_for_width(page, want):
    page.wait_for_function("w => Math.abs(Compose._api.getL().art.lastChild.bounds.width - w) < 0.05", arg=want, timeout=20000)


def test_refresh_fonts_leaves_existing_text_in_its_font(server):
    """Text is on the page, a new font is installed, and "Refresh fonts" is pressed. Editing the text
    afterwards used to redraw it in whichever font had taken its place in the list."""
    _make_font(fonts.user_folder() / "TestSans.ttf")
    _make_font(fonts.user_folder() / "TestWide.ttf", family="Test Wide", advance=1200)
    fonts.listing(refresh=True)
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")
        own, em = page.evaluate("""async () => {
          const A = Compose._api, S = Compose._S, L = A.getL();
          const j = await (await fetch('/api/fonts')).json(); S.fonts = j.fonts;
          S.font = S.fonts.find(f => f.family === 'Test Wide'); S.text.text = 'AAAA';
          await A.addText(new paper.Point(20, 20));
          A.select([L.art.lastChild]);
          return [L.art.lastChild.bounds.width, S.text.size * 25.4 / 72];
        }""")
        assert abs(own - 4.0 * em) < 0.05                                 # three steps of 1.2 em and a letter 0.4 em wide
        shown = "(() => { const s = document.querySelector('#side select'); return s.options[s.selectedIndex].textContent; })()"
        assert page.evaluate(shown).startswith("Test Wide")
        _make_font(fonts.user_folder() / "First.ttf", family="A First Font", advance=300)   # sorts before both
        page.click("#side .bsm:text('Refresh fonts')")
        page.wait_for_function("Compose._S.fonts.length === 3")
        assert page.evaluate(shown).startswith("Test Wide")              # the panel still names the text's own font
        assert page.evaluate("Compose._S.font.family") == "Test Wide"    # and new text still gets the font that was picked
        page.evaluate("Compose._api.retext(Compose._api.getL().art.lastChild, {spacing: 100})")   # the text is edited
        _wait_for_width(page, own + 0.3 * em)                            # redrawn in Test Wide (in Test Sans: 2.5 em)

        # Another version of the text's font is installed, in a file that comes first: the list shows
        # that one, and the text keeps its own.
        _make_font(fonts.user_folder() / "A-Wide.ttf", family="Test Wide", advance=900)
        page.click("#side .bsm:text('Refresh fonts')")
        page.wait_for_function("Compose._S.fontsOther.length === 1")
        assert page.evaluate("Compose._S.fonts.length") == 3
        assert page.evaluate(shown) == "Test Wide — Regular (another version)"
        page.evaluate("Compose._api.retext(Compose._api.getL().art.lastChild, {spacing: 200})")
        _wait_for_width(page, own + 0.6 * em)                            # still in its own version (the listed one: 3.7 em)
        page.evaluate("Compose._api.retext(Compose._api.getL().art.lastChild, {spacing: 100})")
        _wait_for_width(page, own + 0.3 * em)

        page.fill("#side textarea", "")                                  # the box is emptied on the way to typing something else
        page.wait_for_timeout(800)
        assert page.evaluate("document.activeElement.tagName") == "TEXTAREA"   # it stays as it is, with the cursor in it,
        assert page.evaluate("document.querySelector('#toast').style.display") != "block"   # and nothing complains
        assert page.evaluate("Compose._api.getL().art.lastChild.data.text.text") == "AAAA"
        page.evaluate("document.activeElement.blur()")
        assert page.input_value("#side textarea") == "AAAA"              # left empty, it shows the text again

        (fonts.user_folder() / "TestWide.ttf").unlink()                  # its font is taken away
        page.click("#side .bsm:text('Refresh fonts')")
        page.wait_for_function("Compose._S.fontsOther.length === 0")
        assert page.evaluate(shown) == "Test Wide Regular — not installed"   # it says so, rather than naming another font
        assert page.evaluate("document.querySelector('#side select').options[0].disabled")   # (and that can't be picked)
        page.evaluate("""() => { const A = Compose._api, t = A.getL().art.lastChild;
          A.retext(t, {text: 'AA', spacing: 0}); A.rotateSel(90); }""")   # an edit, and before it is refused the text is turned
        page.wait_for_function("document.querySelector('#toast') && /not installed/.test(document.querySelector('#toast').textContent)", timeout=10000)
        assert page.evaluate("(({text, spacing, rot}) => [text, spacing, rot])(Compose._api.getL().art.lastChild.data.text)") == ["AAAA", 100, 90]   # as it was, and still turned
        tall = page.evaluate("Compose._api.getL().art.lastChild.bounds.height")
        assert abs(tall - (own + 0.3 * em)) < 0.05                       # the drawing is left as it is (on its side)
        assert page.input_value("#side textarea") == "AAAA"
        page.evaluate("Compose._api.rotateSel(-90)")
        page.evaluate("""() => { const A = Compose._api, t = A.getL().art.lastChild, b = t.bounds;
          A.retext(t, {size: 72}); A.setBounds(b.x, b.y, b.width * 2, b.height * 2); }""")   # likewise a new size, and the text is made twice as large by hand
        page.wait_for_function("Compose._api.getL().art.lastChild.data.text.size === 72", timeout=10000)   # refused: 36 pt, made twice as large
        _wait_for_width(page, 2 * (own + 0.3 * em))
        sans = page.evaluate("Compose._S.fonts.find(f => f.family === 'Test Sans').id")
        page.select_option("#side select", sans)                         # until another font is picked for it
        _wait_for_width(page, 2 * 2.5 * em)                              # three steps of 0.7 em and a letter, twice as large
        assert page.evaluate("Compose._S.font.family") == "Test Sans"
        gone = page.evaluate("""(() => { const s = document.querySelector('#side select'); s.value = s.options[0].value;
          s.dispatchEvent(new Event('change')); return [s.options[0].disabled, Compose._S.font && Compose._S.font.family]; })()""")
        assert gone == [True, "Test Sans"]                               # the font that is gone can't be made the one for new text

        # The font picked for new text is taken away too: new text falls back to a font that is there.
        (fonts.user_folder() / "TestSans.ttf").unlink()
        page.click("#side .bsm:text('Refresh fonts')")
        page.wait_for_function("Compose._S.fonts.length === 2")
        page.evaluate("() => { Compose._S.text.text = 'AAAA'; return Compose._api.addText(new paper.Point(20, 80)); }")
        page.wait_for_function("Compose._api.getL().art.children.length === 2")
        assert page.evaluate("Compose._api.getL().art.lastChild.data.text.fname") == "A First Font Regular"
        assert abs(_text_width(page) - 1.3 * em) < 0.05                  # three steps of 0.3 em and a letter
        (fonts.user_folder() / "First.ttf").unlink()                     # and with no font left, it says what to do
        (fonts.user_folder() / "A-Wide.ttf").unlink()
        page.click('[data-ct="text"]')
        page.click("#side .bsm:text('Refresh fonts')")
        page.wait_for_function("Compose._S.fonts.length === 0")
        page.evaluate("Compose._api.addText(new paper.Point(20, 120))")
        page.wait_for_function("document.querySelector('#toast') && /No fonts found/.test(document.querySelector('#toast').textContent)", timeout=10000)
        assert page.evaluate("Compose._api.getL().art.children.length") == 2
        assert not errs, errs
        b.close()


def test_text_is_made_of_what_was_asked_for(server):
    """While new text is being made, another font is picked (or the list is refreshed). The text keeps
    the font it was asked in. And an edit still on its way when Undo is pressed doesn't spoil Redo."""
    _make_font(fonts.user_folder() / "TestSans.ttf")
    _make_font(fonts.user_folder() / "TestWide.ttf", family="Test Wide", advance=1200)
    fonts.listing(refresh=True)
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")
        em = page.evaluate("""async () => {
          const S = Compose._S, j = await (await fetch('/api/fonts')).json(); S.fonts = j.fonts;
          S.font = S.fonts.find(f => f.family === 'Test Wide'); S.text.text = 'AAAA';
          return S.text.size * 25.4 / 72;
        }""")
        held, sent = [], []
        page.on("request", lambda r: sent.append(r.post_data_json) if r.url.endswith("/api/text") else None)
        page.route("**/api/text", lambda route: held.append(route))      # the answer is slow
        page.evaluate("() => { window.__adding = Compose._api.addText(new paper.Point(20, 20)); }")   # (not waited for)
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        assert len(held) == 1
        page.evaluate("() => { Compose._S.font = Compose._S.fonts.find(f => f.family === 'Test Sans'); Compose._S.text.text = 'A'; }")
        held[0].continue_()
        page.unroute("**/api/text")
        page.evaluate("window.__adding")
        made = page.evaluate("(({text, fname}) => [text, fname])(Compose._api.getL().art.lastChild.data.text)")
        assert made == ["AAAA", "Test Wide Regular"]                     # used to say A, in Test Sans
        assert abs(_text_width(page) - 4.0 * em) < 0.05

        face = page.evaluate("Compose._api.getL().art.lastChild.data.text.face")
        assert len(face) == 40 and "face" not in sent[-1]                # new text: whichever font has that id
        page.evaluate("Compose._api.retext(Compose._api.getL().art.lastChild, {spacing: 100})")
        _wait_for_width(page, 4.3 * em)
        assert sent[-1]["face"] == face                                  # drawn again: only from that very font
        page.evaluate("() => { Compose._api.retext(Compose._api.getL().art.lastChild, {spacing: 500}); Compose._api.undo(); }")   # Undo while an edit is on its way
        page.wait_for_timeout(1200)
        assert abs(_text_width(page) - 4.0 * em) < 0.05
        assert page.evaluate("Compose._S.hi < Compose._S.hist.length - 1")   # Redo is still there
        page.evaluate("Compose._api.redo()")
        assert abs(_text_width(page) - 4.3 * em) < 0.05
        assert not errs, errs
        b.close()


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


@pytest.mark.parametrize("target", ["compose", "print", "simulate", "help"])
def test_leaving_the_batch_view_for_another_tab(server, lineart, target):
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.set_input_files("#file", str(lineart))
        page.wait_for_function("/Paths/i.test(document.querySelector('#stats').textContent)", timeout=60000)
        page.click("#toolbar [data-m=batch]")
        page.click(f'a[data-t="{target}"]')
        page.wait_for_timeout(1000)
        assert not errs, errs
        assert page.evaluate("document.querySelector('#side').innerText.trim()")          # the new tab drew its panel
        page.click('a[data-t="convert"]')                                                    # and Convert still works afterwards
        page.wait_for_function("/Paths/i.test(document.querySelector('#stats').textContent)", timeout=60000)
        assert not errs, errs
        b.close()


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


def test_renaming_in_the_header_renames_the_library_item(server, lineart):
    """The header name names the library item. Renaming renames the file and leaves the graphic alone,
    even on Print (where it used to save the whole print page over the graphic)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.set_input_files("#file", str(lineart))
        page.wait_for_function("libName !== ''", timeout=60000)
        traced = library.read(page.evaluate("libName"))

        def rename(to):
            page.click("#nmEdit")                                         # clicking selects the whole name
            if to is None:
                page.keyboard.press("Escape")                              # looked at the name, changed nothing
            else:
                page.keyboard.type(to)
                page.keyboard.press("Enter")
            page.wait_for_timeout(2500)                                    # longer than the delayed save

        rename("my badge")
        assert [i["name"] for i in library.listing()] == ["my badge.svg"]  # used to stay line.svg
        page.click('a[data-t="print"]')
        page.wait_for_function("lastRes && lastRes.plates && lastRes.plates.length > 0", timeout=120000)
        rename(None)
        rename("final badge")
        assert [i["name"] for i in library.listing()] == ["final badge.svg"]
        assert library.read("final badge.svg") == traced                   # still the Convert graphic, not the print page
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


def test_names_with_html_are_shown_as_text(server):
    """A graphic's name comes from its file name; it must be shown as written, never run as part of the page."""
    sync_api = pytest.importorskip("playwright.sync_api")
    name = "<img src=x onerror=window.__ran=1>"     # not a valid Windows file name, so it is handed to the browser directly
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")
        page.set_input_files("#cfile", {"name": name + ".svg", "mimeType": "image/svg+xml", "buffer": SVG.encode()})
        page.wait_for_function("Compose._S.sel.length === 1")
        page.wait_for_timeout(300)
        assert not page.evaluate("window.__ran === 1")
        assert name in page.evaluate("[...document.querySelectorAll('#side .bh')].map(e => e.textContent)")
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


def test_position_and_size_boxes(server):
    """Typing X then Y moves to both; a flat line survives the boxes; the boxes follow a drag."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")
        bounds = "(() => { const b = Compose._api.getL().art.lastChild.bounds; return [b.x, b.y, b.width, b.height].map(v => Math.round(v * 10) / 10); })()"
        def type_in(label, value):
            n = page.evaluate("l => [...document.querySelectorAll('.cell input')].findLastIndex(i => (i.closest('label,div') || {}).textContent.trim() === l)", label)   # the artboard has its own Width / Height above
            assert n >= 0, label
            i = page.locator(".cell input").nth(n)
            i.fill(str(value))
            i.press("Enter")

        page.evaluate("""(() => { const A = Compose._api, L = A.getL();
          const r = new paper.Path.Rectangle({point: [10, 10], size: [40, 30], fillColor: '#000'}); L.art.addChild(r); A.commit(); A.select([r]); })()""")
        units = page.evaluate("[...document.querySelectorAll('.cell input')].findLast(i => (i.closest('label,div') || {}).textContent.trim() === 'X').value")
        k = float(units) / 10                                              # the boxes may show cm or mm
        type_in("X", 100 * k)
        type_in("Y", 80 * k)
        assert page.evaluate(bounds)[:2] == [100, 80]                       # typing Y used to put X back
        type_in("Width", 60 * k)
        assert page.evaluate(bounds) == [100, 80, 60, 45]                   # proportions kept, position kept

        page.evaluate("""(() => { const A = Compose._api, L = A.getL(); L.art.removeChildren();
          const l = new paper.Path.Line({from: [20, 50], to: [120, 50], strokeColor: '#000', strokeWidth: 1}); L.art.addChild(l); A.commit(); A.select([l]); })()""")
        type_in("X", 30 * k)
        x, y, w, h = page.evaluate(bounds)
        assert (x, y, w, h) == (30, 50, 100, 0)                             # a flat line used to vanish (its points became NaN)
        assert "NaN" not in page.evaluate("Compose._api.svgString()")
        assert not errs, errs
        b.close()
