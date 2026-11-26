"""Print with a set design size: the design stays on the page."""
import pytest
from PIL import Image

from test_plates import server  # noqa: F401 (a fixture)


def test_a_set_design_size_stays_on_the_page_after_another_picture_or_page_size(server, lineart, tmp_path):
    """Typing a size makes the page bigger when the design would not fit. But opening a taller
    picture afterwards, or choosing a smaller page, left the design at its size on a page too
    small for it: it ran off the top and bottom (and was cut off in the films), or lay over the
    registration marks."""
    sync_api = pytest.importorskip("playwright.sync_api")
    tall = tmp_path / "tall.png"
    Image.open(lineart).rotate(90, expand=True).save(tall)                 # 400 x 600 where the line art is 600 x 400
    state = "JSON.stringify([lastRes.page, lastRes.art])"

    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))

        def after(do):
            """Do it, wait for the new plates, and give back the page size, where the design is, and the room around it."""
            before = page.evaluate(state)
            do()
            page.wait_for_function("b => lastRes && lastRes.art && " + state + " !== b", arg=before, timeout=120000)
            (pw, ph), (x, y, w, h) = page.evaluate("[lastRes.page, lastRes.art]")
            return (pw, ph), (round(w, 1), round(h, 1)), round(min(x, y, pw - x - w, ph - y - h), 1)

        page.goto(server + "/")
        page.set_input_files("#file", str(lineart))
        page.wait_for_function("imageId && lastShown", timeout=60000)
        page.click('a[data-t="print"]')
        page.wait_for_function("lastRes && lastRes.plates && lastRes.plates.length > 0", timeout=120000)
        page.click("#side span:text-is('Spot colour')")                    # (flat colours: quick to make)
        page.wait_for_function("lastRes && lastRes.plates.length > 1", timeout=120000)
        page.click("#side span:text-is('Set size')")
        page.wait_for_selector("#side .row:has-text('Width') input")
        note = "document.querySelector('#toast').textContent"

        def type_width():
            box = page.locator("#side .row", has_text="Width").locator("input").last
            box.fill("200")
            box.press("Enter")
        sheet, design, room = after(type_width)
        assert sheet == (297, 210) and design == (200, 133.3) and room >= 28   # fits A4, with the usual room for the marks

        sheet, design, room = after(lambda: page.set_input_files("#file", str(tall)))
        assert design == (200, 300)                                        # still the width that was typed
        assert room >= 28 and sheet == (297, 356)                          # used to be A4 still: 45 mm cut off top and bottom
        assert "made bigger" in page.evaluate(note)
        assert page.evaluate("[PG.size, [...document.querySelectorAll('#side .on')].some(s => s.textContent === '297×356 mm')]") == ["Custom", True]

        sheet, design, room = after(lambda: page.set_input_files("#file", str(lineart)))
        assert sheet == (297, 210) and design == (200, 133.3)              # the first picture again: A4 is enough again, so it is A4
        assert page.evaluate("PG.size") == "A4" and "so the page is A4" in page.evaluate(note)

        after(lambda: page.set_input_files("#file", str(tall)))
        sheet, design, room = after(lambda: page.click("#side span:text-is('A5')"))   # a page too small is made big enough at once
        assert design == (200, 300) and room >= 28 and page.evaluate("PG.size") == "Custom"

        sheet, design, room = after(lambda: page.click("#side span:text-is('Fit to page')"))   # without a set size, the page chosen is the page
        assert page.evaluate("[PG.art_w, PG.size]") == [0, "A5"] and sheet in ((210, 148), (148, 210)) and room >= 27.9

        page.click("#side span:text-is('Set size')")                       # a size is set again, and then a page laid out in Compose
        page.wait_for_function("PG.art_w > 0")                             # is sent to Print: it is printed as laid out, not at that size
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")
        page.evaluate("""(() => { const A = Compose._api, L = A.getL(); A.setBoard(120, 90);
          L.art.addChild(new paper.Path.Rectangle({point: [20, 20], size: [60, 40], fillColor: '#000'})); A.commit(); A.sendToPrint(); })()""")
        page.wait_for_function("tool === 'print' && PG.size === 'Custom' && lastRes && lastRes.page[0] === PG.custom_w", timeout=120000)
        m = page.evaluate("PG.margin")
        assert page.evaluate("[PG.art_w, lastRes.page]") == [0, [120 + 2 * m, 90 + 2 * m]]
        assert [round(v, 1) for v in page.evaluate("lastRes.art")] == [m, m, 120, 90]

        own = (120 + 2 * m, 90 + 2 * m)                                    # that page is the page chosen now: a size set for it makes it
        page.click("#side span:text-is('Set size')")                       # bigger, and it comes back when the size is no longer set
        page.wait_for_selector("#side .row:has-text('Width') input")
        box = page.locator("#side .row", has_text="Width").locator("input").last
        sheet, design, room = after(lambda: (box.fill("300"), box.press("Enter")))
        assert design == (300, 225) and room >= 28 and sheet[0] > own[0]
        sheet, design, room = after(lambda: page.click("#side span:text-is('Fit to page')"))
        assert sheet == own and design == (120, 90)                        # used to stay big, with the design printed 2.5 times too large
        assert not errs, errs
        b.close()
