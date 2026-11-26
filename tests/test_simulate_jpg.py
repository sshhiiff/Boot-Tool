"""Simulate: the measurements in the saved mock-up."""
import numpy as np
import pytest
from PIL import Image

from test_simulate import server  # noqa: F401 (a fixture)


@pytest.mark.parametrize("shirt", ["#000000", "#ffffff"])
def test_measurements_beside_the_shirt_show_in_the_jpg(server, tmp_path, shirt):
    """"Include measurements" writes chest, length and neck into the JPG. Length and neck stand beside
    the shirt, on the JPG's white background, but were drawn in the colour that shows on the shirt:
    on a dark shirt that is white, so they could not be seen."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1200, "height": 850}, accept_downloads=True)
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server)
        page.click('#nav a[data-t="simulate"]')
        page.wait_for_selector("#ssvg path")
        page.evaluate("c => { Simulate._S.colour = c; Simulate._api.draw(); }", shirt)

        def mock_up(measurements):
            page.evaluate("m => { Simulate._S.jpgLines = m; }", measurements)
            with page.expect_download(timeout=60000) as d:
                page.click("#saveJpg")
            d.value.save_as(tmp_path / "m.jpg")
            return np.asarray(Image.open(tmp_path / "m.jpg").convert("L")).astype(int)
        plain, measured = mock_up(False), mock_up(True)
        assert not errs, errs
        b.close()
    drawn = np.abs(measured - plain) > 60                                   # what the measurements added to the picture
    if shirt == "#000000":
        cloth = plain < 128
    else:                                                                   # a white shirt on white: inside its outline
        rows, cols = np.nonzero(plain < 200)
        cloth = np.zeros_like(plain, bool)
        cloth[rows.min():rows.max() + 1, cols.min():cols.max() + 1] = True
    top = np.nonzero(cloth.any(axis=1))[0][0]
    hem = np.nonzero(cloth.any(axis=1))[0][-1]
    body_right = np.nonzero(cloth[hem - 20])[0][-1] if shirt == "#000000" else int(plain.shape[1] * 0.80)
    assert drawn[:top - 3].sum() > 150                                      # "neck", above the collar
    assert drawn[(top + hem) // 2:hem, body_right + 3:].sum() > 150         # "length", to the right of the body, below the sleeve
    assert drawn[top:hem, :body_right][:, plain.shape[1] // 4:].sum() > 150   # "chest", across the shirt


def test_a_designs_measurements_show_in_the_jpg_on_a_dark_shirt(server, tmp_path):
    """So do the two measurements of each design: how far it is from the collar (written to its
    left) and its size (written under it). A design low and to one side has both beside the
    shirt, where they were white on white; the size could also lie below the picture's edge."""
    import base64
    import io
    from boottool import prints
    sync_api = pytest.importorskip("playwright.sync_api")
    png = io.BytesIO()
    Image.new("RGB", (300, 300), (230, 60, 60)).save(png, "PNG")
    prints.save("patch", "data:image/png;base64," + base64.b64encode(png.getvalue()).decode(), 280, 280)
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1200, "height": 850}, accept_downloads=True)
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server)
        page.click('#nav a[data-t="simulate"]')
        page.wait_for_selector("#ssvg path")
        page.evaluate("Simulate._api.addDesign('print', 'patch.png', 20)")
        page.wait_for_selector("#ssvg image")
        page.evaluate("""(() => { const S = Simulate._S, d = S.designs.front[0];
          S.colour = '#000000'; S.jpgLines = true; d.x = -16; d.y = 50; Simulate._api.draw(); })()""")   # at the hem, towards the left side
        with page.expect_download(timeout=60000) as d:
            page.click("#saveJpg")
        d.value.save_as(tmp_path / "m.jpg")
        assert not errs, errs
        b.close()
    dark = np.asarray(Image.open(tmp_path / "m.jpg").convert("L")) < 110
    hem = np.nonzero(dark.mean(axis=1) > 0.3)[0][-1]                        # the last row that is mostly shirt
    body_left = np.nonzero(dark[hem - 20])[0][0]
    assert dark[hem + 12:].sum() > 150                                      # the size, under the design and under the shirt
    assert dark[int(hem * 0.47):int(hem * 0.95), :body_left - 3].sum() > 100   # "50 cm" from the collar, left of the body below the sleeve
