"""The tabs and the choices in the side panel can be used with the keyboard."""
import pytest

from test_plates import server  # noqa: F401 (a fixture)

FOCUS = "(() => { const a = document.activeElement; return a && a !== document.body ? a.textContent.trim() : null; })()"


def test_tabs_and_choices_work_with_the_keyboard(server, color):
    """They are not real buttons, so the Tab key skipped them and nothing but a click could press
    them. Now Tab reaches them, Enter or Space presses them, and the focus stays on the choice
    when its panel is redrawn."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.set_input_files("#file", str(color))
        page.wait_for_function("imageId && lastShown", timeout=60000)

        reached = []
        for _ in range(5):
            page.keyboard.press("Tab")
            reached.append(page.evaluate(FOCUS))
        assert reached == ["Convert", "Compose", "Print", "Simulate", "Help"]   # used to go straight to "Open image…"

        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(FOCUS) == "Print"
        page.keyboard.press("Enter")                                       # Enter on the Print tab
        page.wait_for_function("tool === 'print' && lastRes && lastRes.plates && lastRes.plates.length > 0", timeout=120000)
        assert page.evaluate(FOCUS) == "Print"

        for _ in range(40):                                                # on through the page to a choice in the panel
            page.keyboard.press("Tab")
            if page.evaluate(FOCUS) == "Spot colour":
                break
        assert page.evaluate(FOCUS) == "Spot colour" and page.evaluate("P.print.method") != "spot"
        page.keyboard.press(" ")                                           # Space chooses it; the panel is drawn again
        page.wait_for_function("P.print.method === 'spot' && lastRes && lastRes.plates.length > 1", timeout=120000)
        page.wait_for_timeout(300)
        assert page.evaluate(FOCUS) == "Spot colour"                       # and the focus is still on it
        assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "solid"   # shown by a frame
        page.keyboard.press("Tab")
        assert page.evaluate(FOCUS) == "Flat graphic"                      # so Tab goes on from there

        page.locator("#toolbar [data-m=orig]").focus()                     # the view buttons too
        page.keyboard.press("Enter")
        assert page.evaluate("mode") == "orig"

        assert page.evaluate("[document.activeElement.getAttribute('role'), document.querySelector('#toolbar [data-m=orig]').getAttribute('aria-pressed'), "
                             "document.querySelector('#toolbar [data-m=svg]').getAttribute('aria-pressed')]") == ["button", "true", "false"]
        assert not errs, errs
        b.close()


def test_the_mouse_works_as_it_did(server):
    """A choice clicked with the mouse must not keep the focus: in Compose the arrow keys nudge and
    Space pans, and pressing them after a click on "Duplicate" must not duplicate again."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")
        page.evaluate("""(() => { const A = Compose._api, L = A.getL();
          const r = new paper.Path.Rectangle({point: [30, 30], size: [40, 30], fillColor: '#000'}); L.art.addChild(r); A.commit(); A.select([r]); })()""")
        shapes = "Compose._api.getL().art.children.length"
        page.click("#side span:text-is('Duplicate')")
        assert page.evaluate(shapes) == 2
        assert page.evaluate("document.activeElement === document.body")   # the focus is where a click always left it
        x = page.evaluate("Compose._S.sel[0].bounds.x")
        page.keyboard.press("ArrowRight")                                  # nudges the shape...
        assert page.evaluate("Compose._S.sel[0].bounds.x") > x
        page.keyboard.press(" ")                                           # ...and Space is not a second click on "Duplicate"
        page.keyboard.press("Enter")
        assert page.evaluate(shapes) == 2
        assert page.evaluate("document.querySelectorAll(':focus-visible').length") == 0   # no frame either

        side = "document.querySelector('#side')"                           # and the panel stays where it was scrolled to
        page.click('a[data-t="print"]')
        page.wait_for_selector("#side span:text-is('Dark')")
        page.click("#side span:text-is('Dark')")
        page.evaluate(f"{side}.scrollTop = 400")
        before = page.evaluate(f"{side}.scrollTop")
        page.evaluate("renderSide()")
        assert before > 100 and page.evaluate(f"{side}.scrollTop") == before

        spot = page.locator("#side span:text-is('Spot colour')")           # pressed on a choice, dragged away and let go: no focus left
        box = spot.bounding_box()
        page.mouse.move(box["x"] + 5, box["y"] + 5)
        page.mouse.down()
        page.mouse.move(700, 500, steps=3)
        page.mouse.up()
        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement === document.body && document.querySelectorAll(':focus-visible').length === 0")
        assert not errs, errs
        b.close()
