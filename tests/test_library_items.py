"""Every picture opened in the page has its own library item."""
import pytest

from boottool import library

from test_compose import server  # noqa: F401 (a fixture)

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0h5v5z"/></svg>'


def test_each_picture_keeps_its_own_library_item(server, lineart, lineart_inverted, color):
    """With one picture converted, a second one opened from the Batch list and renamed before its
    first save: the first picture's library file got the new name. And going on to the next picture
    within a second and a half dropped the save of the one before."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.set_input_files("#file", str(lineart))
        page.wait_for_function("libName !== ''", timeout=60000)
        first = library.read("line.svg")

        def open_from_batch(name):
            page.click("#toolbar [data-m=batch]")
            page.click(f'.brow:has-text("{name}")')
            page.wait_for_function("n => lastShown && BATCH.items[BATCH.sel].name === n && imageId === BATCH.items[BATCH.sel].id", arg=name, timeout=60000)
            return page.evaluate("svgText")

        def rename(to):
            page.click("#nmEdit")
            page.keyboard.type(to)
            page.keyboard.press("Enter")

        page.click("#toolbar [data-m=batch]")
        page.set_input_files("#addImgs", [str(color), str(lineart_inverted)])
        page.wait_for_function("BATCH.items.length >= 2 && BATCH.adding === 0", timeout=60000)
        colour = open_from_batch("color.png")
        rename("renamed colour")                                         # before its first save
        page.wait_for_function("libName === 'renamed colour.svg'", timeout=60000)
        assert library.read("line.svg") == first                         # the first picture's item is untouched
        assert library.read("renamed colour.svg").decode() == colour
        assert sorted(i["name"] for i in library.listing()) == ["line.svg", "renamed colour.svg"]

        inverted = open_from_batch("inv.png")                            # shown, then left before its save
        open_from_batch("color.png")
        assert page.inner_text("#nmEdit") == "renamed colour"            # under the name it was given
        page.wait_for_function("LIB.files[BATCH.items.find(i => i.name === 'inv.png').id] === 'inv.svg'", timeout=60000)
        assert sorted(i["name"] for i in library.listing()) == ["inv.svg", "line.svg", "renamed colour.svg"]
        assert library.read("inv.svg").decode() == inverted and library.read("renamed colour.svg").decode() == colour

        rename("final colour")                                           # an ordinary rename of a saved picture
        page.wait_for_function("libName === 'final colour.svg'", timeout=60000)
        assert sorted(i["name"] for i in library.listing()) == ["final colour.svg", "inv.svg", "line.svg"]
        assert library.read("final colour.svg").decode() == colour and library.read("line.svg") == first
        assert not errs, errs
        b.close()


def test_nothing_to_download_until_the_pictures_own_result_is_here(server, lineart, color):
    """Just after another picture was opened, "Download SVG" still gave the picture before, under the
    new picture's name."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.set_input_files("#file", str(lineart))
        page.wait_for_function("lastShown", timeout=60000)
        assert page.is_enabled("#dl")
        held = []
        page.route("**/api/trace", lambda route: held.append(route))     # the next trace is held back
        page.set_input_files("#file", str(color))
        page.wait_for_function("fileName === 'color'", timeout=20000)
        assert page.is_disabled("#dl")                                   # used to offer line's SVG as color.svg
        page.unroute("**/api/trace")
        for route in held:
            try:
                route.continue_()
            except Exception:                                            # the page gave up on that request meanwhile
                pass
        page.evaluate("run()")
        page.wait_for_function("lastShown && !document.querySelector('#dl').disabled", timeout=60000)
        assert not errs, errs
        b.close()


def test_double_clicking_a_library_name_in_compose_renames_without_adding_copies(server):
    sync_api = pytest.importorskip("playwright.sync_api")
    library.save("badge", SVG)
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.hover("#clib .ltab")
        page.wait_for_selector("#clibStrip .lt", state="attached", timeout=10000)
        count = "paper.project.getItems({match: i => i.data && i.data.name}).length"
        start = page.evaluate(count)
        page.click("#clibStrip .lt .ltn")                                 # one click on the name adds it, once
        page.wait_for_function(count + " > " + str(start), timeout=10000)
        before = page.evaluate(count)
        page.wait_for_timeout(800)
        assert page.evaluate(count) == before
        page.hover("#clib .ltab")
        page.dblclick("#clibStrip .lt .ltn")                              # a double-click renames...
        page.keyboard.type("crest")
        page.keyboard.press("Enter")
        page.wait_for_function("document.querySelector('#clibStrip .ltn').textContent === 'crest'", timeout=10000)
        for _ in range(100):                                              # (the name on screen is what was typed; the library follows a moment later)
            if [i["name"] for i in library.listing()] == ["crest.svg"]:
                break
            page.wait_for_timeout(100)
        assert [i["name"] for i in library.listing()] == ["crest.svg"]
        page.wait_for_timeout(800)
        assert page.evaluate(count) == before                             # ...and adds nothing to the page (it used to add two copies)
        assert not errs, errs
        b.close()


def test_changing_only_the_capitals_of_a_name_keeps_the_name():
    """On Mac and Windows "Logo.svg" is the file "logo.svg", so the new name looked taken: it became Logo-2."""
    assert library.save("logo", SVG) == "logo.svg"
    assert library.rename("logo.svg", "Logo") == "Logo.svg"
    assert [i["name"] for i in library.listing()] == ["Logo.svg"]
    other = SVG.replace("5", "8")                                         # another item's name is still taken, in any capitals
    assert library.save("other", other) == "other.svg" and library.rename("other.svg", "Logo") == "Logo-2.svg"
    taken = library.rename("Logo-2.svg", "LOGO")
    assert library.read("Logo.svg").decode() == SVG and library.read(taken).decode() == other and taken != "Logo.svg"
    assert len(library.listing()) == 2
