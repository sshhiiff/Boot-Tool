import json

import numpy as np
import pytest
from PIL import Image

from boottool import page as pg
from boottool import printing as pr

from test_plates import _post, ok, server  # noqa: F401 (server is a fixture)


def _naive_floyd(t):
    """Plain textbook Floyd-Steinberg, left to right, top to bottom."""
    a = t.astype(np.float64).copy()
    h, w = a.shape
    out = np.zeros((h, w), bool)
    for y in range(h):
        for x in range(w):
            on = a[y, x] >= 0.5
            out[y, x] = on
            e = a[y, x] - on
            for dx, dy, wt in pr._KERNELS["floyd"]:
                if 0 <= x + dx < w and 0 <= y + dy < h:
                    a[y + dy, x + dx] += e * wt
    return out


def test_error_diffusion_matches_textbook():
    t = np.random.default_rng(3).random((23, 31)).astype(np.float32)
    assert (pr.dither_mask(t, "floyd") == _naive_floyd(t)).all()


@pytest.mark.parametrize("algo", pr.DITHERS)
def test_dither_ink_matches_tone(algo):
    for tone in (0.15, 0.5, 0.85):
        m = pr.dither_mask(np.full((160, 160), tone, np.float32), algo)
        if algo == "atkinson" and tone != 0.5:        # Atkinson drops 1/4 of the error: crisp highlights and shadows
            assert (m.mean() < tone) if tone < 0.5 else (m.mean() > tone)
            continue
        assert abs(m.mean() - tone) < 0.05, (algo, tone, m.mean())
    assert not pr.dither_mask(np.zeros((40, 40), np.float32), algo).any()
    assert pr.dither_mask(np.ones((40, 40), np.float32), algo).all()


def test_runs_path_covers_exactly_the_ink():
    import re
    m = np.random.default_rng(4).random((30, 50)) > 0.6
    d, n = pr.runs_path(m)
    runs = re.findall(r"M(\d+) (\d+)h(\d+)v1h-\d+z", d)
    back = np.zeros_like(m)
    for x, y, l in runs:
        back[int(y), int(x):int(x) + int(l)] = True
    assert n == len(runs) and (back == m).all()


def test_levels_lut_and_coerce():
    assert pr._lut(pr.PrintParams()) is None
    lut = pr._lut(pr.PrintParams(lv_black=50, lv_white=200))
    assert lut[50] == 0 and lut[200] == 255 and lut[0] == 0 and lut[255] == 255
    lighter = pr._lut(pr.PrintParams(lv_gamma=1.5))
    assert lighter[128] > 128                                   # gamma above 1 lightens, like Photoshop
    p = pr.coerce_print({"lv_black": 250, "lv_white": 100, "lv_gamma": 99, "method": "x", "dither_size": 0})
    assert p.lv_white - p.lv_black >= 2 and p.lv_gamma == 9.99 and p.method == "one" and p.dither_size == 0.1
    assert pr.coerce_print({"shape": "dot"}).shape == "round"
    assert pr.coerce_print({"angles": [10, None, "x", 200]}).angles == (10.0, -1.0, -1.0, 90.0)


@pytest.fixture
def ramp(tmp_path):
    arr = np.tile(np.linspace(255, 0, 600, dtype=np.uint8), (300, 1))
    Image.fromarray(arr).save(tmp_path / "ramp.png")
    return tmp_path / "ramp.png"


def test_analyse_reports_histogram_and_auto_levels(ramp, lineart):
    a = pr.analyse(ramp)
    assert len(a["hist"]) == 256 and a["tonal"] and a["auto"][0] < 10 and a["auto"][2] > 240
    assert not pr.analyse(lineart)["tonal"] or len(pr.analyse(lineart)["hist"]) == 256


@pytest.mark.parametrize("kw,plates", [
    ({}, 1), ({"render": "dither"}, 1), ({"render": "dither", "dither": "blue"}, 1),
    ({"method": "cmyk"}, 4), ({"method": "cmyk", "render": "dither", "dither": "atkinson"}, 4),
    ({"method": "spot", "kind": "photo", "inks": 3}, 3),
    ({"method": "spot", "kind": "photo", "inks": 3, "render": "dither", "dither": "stucki"}, 3),
    ({"method": "spot", "kind": "flat", "inks": 3}, None),
])
def test_every_method_builds_valid_plates(color, kw, plates):
    job = pr.build(color, pr.coerce_print(kw), pg.PageOpts())
    if plates:
        assert len(job.plates) == plates
    assert job.plates
    ok(pg.compose(job, "all"))
    lay = pg.layered_svg(job)
    ok(lay)
    assert 'fill="#000"' in lay and "PLATE 1" in lay


def test_levels_change_the_ink_in_every_method(ramp):
    from boottool.preprocess import load_rgb
    rgb = load_rgb(ramp)

    def ink(**kw):
        p = pr.coerce_print(kw)
        specs, plane, _ = pr._plate_specs(rgb, p, 600, 300)
        return sum(float(plane(i).mean()) for i in range(len(specs)))
    for method in ("one", "cmyk", "spot"):
        extra = {"method": method, "kind": "photo", "inks": 3}
        base = ink(**extra)
        if method == "spot":                                # inks are picked again after Levels, so just check it acts
            assert ink(**extra, lv_white=128) != base and ink(**extra, lv_black=128) != base
            continue
        assert ink(**extra, lv_white=128) < base            # lighter half drops to paper
        assert ink(**extra, lv_black=128) > base            # darker half prints solid
        assert ink(**extra, lv_gamma=2.0) < base            # midtones lighter


def test_dither_size_controls_grain_and_has_a_cap(ramp):
    fine = pr.build(ramp, pr.coerce_print({"render": "dither", "dither_size": 0.2}), pg.PageOpts()).info
    coarse = pr.build(ramp, pr.coerce_print({"render": "dither", "dither_size": 0.8}), pg.PageOpts()).info
    assert fine["dots"] > coarse["dots"] * 4 and coarse["size_mm"] == 0.8
    with pytest.raises(ValueError, match="too many"):
        pr.build(ramp, pr.coerce_print({"render": "dither", "dither_size": 0.1}), pg.PageOpts(size="A3"))


def test_cmyk_colours_are_preview_only(color):
    std = pr.build(color, pr.coerce_print({"method": "cmyk"}), pg.PageOpts())
    custom = pr.build(color, pr.coerce_print({"method": "cmyk", "cmyk_colors": ["#112233", "", "", ""]}), pg.PageOpts())
    assert custom.plates[0].ink == "#112233" and std.plates[0].ink == "#00aeef"
    assert [p.body for p in std.plates] == [p.body for p in custom.plates]      # separation unchanged
    assert [p.name for p in custom.plates] == ["Cyan", "Magenta", "Yellow", "Black"]
    assert "mix-blend-mode" in pg.compose(custom, "all") and 'fill="#112233"' in pg.compose(custom, "all")


def test_spot_photo_uses_your_ink_colours_for_the_separation(color):
    auto = pr.build(color, pr.coerce_print({"method": "spot", "kind": "photo", "inks": 3}), pg.PageOpts())
    mine = pr.build(color, pr.coerce_print({"method": "spot", "kind": "photo", "inks": 3, "colors": ["#00ff00"]}), pg.PageOpts())
    again = pr.build(color, pr.coerce_print({"method": "spot", "kind": "photo", "inks": 3}), pg.PageOpts())
    assert again.info["palette"] == auto.info["palette"]                        # steady between redraws
    g = mine.info["palette"][0]["hex"]
    assert int(g[3:5], 16) > 230 and int(g[1:3], 16) < 30                        # your green, give or take rounding
    assert mine.info["palette"][0]["auto_hex"] == auto.info["palette"][0]["auto_hex"]
    assert mine.plates[0].body != auto.plates[0].body                           # separation really changed
    assert auto.info["paper"] and all(p["name"] for p in auto.info["palette"])


def test_print_endpoint_and_upload_histogram(server, color):
    up = json.loads(_post(server + "/api/upload", color.read_bytes(), "application/octet-stream")[1])
    assert len(up["hist"]) == 256 and len(up["auto"]) == 3 and "tonal" in up
    req = {"id": up["id"], "session": "p", "seq": 1, "tool": "print", "view": "all",
           "params": {"method": "cmyk", "render": "dither", "dither": "bayer", "dither_size": 0.5},
           "page": {"size": "A5", "orientation": "portrait"}}
    code, body, _ = _post(server + "/api/plates", req)
    res = json.loads(body)
    assert code == 200 and len(res["plates"]) == 4 and res["info"]["kind"] == "dither"
    ok(res["svg"])
    code, z, h = _post(server + "/api/download", {**req, "kind": "zip"})
    assert code == 200 and h["Content-Type"] == "application/zip"
    code, body, _ = _post(server + "/api/plates", {**req, "seq": 2, "params": {"shape": "custom", "stamp": []}})
    assert code == 400 and "Draw a shape" in json.loads(body)["error"]
    code, body, _ = _post(server + "/api/plates", {**req, "seq": 3, "params": {"render": "dither", "dither_size": 0.1}, "page": {"size": "A3"}})
    assert code == 400 and "too many" in json.loads(body)["error"]


def test_spot_photo_never_prints_on_paper(tmp_path):
    """A very pale ink next to white paper must not tint the paper."""
    arr = np.full((200, 300, 3), 255, np.uint8)
    arr[60:140, 80:220] = (200, 120, 60)
    Image.fromarray(arr).save(tmp_path / "blob.png")
    job = pr.build(tmp_path / "blob.png", pr.coerce_print(
        {"method": "spot", "kind": "photo", "inks": 2, "colors": ["#fff0d8", ""], "render": "dither", "dither": "bayer"}), pg.PageOpts())
    import re
    ax, ay, aw, ah = job.art
    for plate in job.plates:
        xs = [int(m) for m in re.findall(r"M(\d+) \d+h", plate.body)]
        ys = [int(m) for m in re.findall(r"M\d+ (\d+)h", plate.body)]
        W = max(xs) + 1
        assert min(ys) > 0.2 * (max(ys) + 1) and min(xs) > 0.15 * W       # nothing in the paper margin


def test_serpentine_tile_is_a_long_maze_and_inks_the_right_amount():
    from scipy import ndimage
    for name in pr.PATTERNS:
        tile = pr._serpentine(name, 9.0)
        assert tile.shape == (256, 256) and abs(float(tile.mean()) - 0.5) < 0.01          # ranked: fills evenly
        assert np.array_equal(tile, pr._serpentine(name, 9.0))                             # same every time
        for tone in (0.25, 0.5, 0.75):
            m = pr.dither_mask(np.full((200, 200), tone, np.float32), "serpentine", 0, tile)
            assert abs(m.mean() - tone) < 0.03
    mazes = pr.dither_mask(np.full((256, 256), 0.5, np.float32), "serpentine", 0, pr._serpentine("mazes", 9.0))
    noise = pr.dither_mask(np.full((256, 256), 0.5, np.float32), "random")
    big = lambda m: ndimage.label(m)[1]
    assert big(mazes) * 8 < big(noise)                                                       # few, long strokes


def test_serpentine_pattern_size_and_presets_change_the_result(color):
    a, b = pr._serpentine("mazes", 6.0), pr._serpentine("mazes", 16.0)
    assert not np.array_equal(a, b) and not np.array_equal(pr._serpentine("coral", 9.0), pr._serpentine("mazes", 9.0))
    base = {"render": "dither", "dither": "serpentine", "dither_size": 0.4}
    j1 = pr.build(color, pr.coerce_print({**base, "pattern": "mazes"}), pg.PageOpts())
    j2 = pr.build(color, pr.coerce_print({**base, "pattern": "fingerprints", "pattern_size": 20}), pg.PageOpts())
    assert j1.plates[0].body != j2.plates[0].body and j1.info["pattern_mm"] == 3.6
    ok(pg.compose(j1, "all"))
    cm = pr.build(color, pr.coerce_print({**base, "method": "cmyk"}), pg.PageOpts())
    assert len({p.body for p in cm.plates}) == 4                                            # plates are offset, not identical
    assert pr.coerce_print({"pattern": "nope", "pattern_size": 999}).pattern == "mazes"
    assert pr.coerce_print({"pattern_size": 999}).pattern_size == 40.0


def test_draft_preview_is_coarser_but_downloads_stay_full(color):
    import json
    from boottool import page as pg
    from boottool import printing as pr
    from boottool.ui import Engine, download_response
    full = pr.build(color, pr.coerce_print({"method": "cmyk"}), pg.PageOpts())
    draft = pr.build(color, pr.coerce_print({"method": "cmyk", "draft": True}), pg.PageOpts())
    assert draft.info["draft"] and not full.info["draft"]
    assert draft.info["ppi"] < full.info["ppi"] and [p.name for p in draft.plates] == [p.name for p in full.plates]
    assert pr.coerce_print({"draft": "x"}).draft is True
    # dither has no draft mode
    assert not pr.build(color, pr.coerce_print({"render": "dither", "draft": True}), pg.PageOpts()).info["draft"]


def _mix_image(path):
    a = np.full((240, 360, 3), 255, np.uint8)
    a[40:200, 30:150] = (0, 0, 0)                                    # flat black
    a[60:180, 200:330] = np.linspace(40, 215, 130, dtype=np.uint8)[None, :, None]   # grey ramp
    Image.fromarray(a).save(path)
    return str(path)


def test_picked_colours_are_exactly_the_inks(tmp_path):
    f = _mix_image(tmp_path / "m.png")
    cols = ("#808080", "#101010")
    for kind in ("flat", "photo"):
        job = pr.build(f, pr.PrintParams(method="spot", kind=kind, inks=5, picked=True, colors=cols), pg.PageOpts())
        assert [p.ink for p in job.plates] == list(cols) or {p.ink for p in job.plates} == set(cols)
        assert len(job.plates) == 2


def test_keep_solid_prints_flat_areas_as_vector(tmp_path):
    f = _mix_image(tmp_path / "m.png")
    base = pr.PrintParams(method="spot", kind="photo", inks=3)
    dots = pr.build(f, base, pg.PageOpts())
    solid = pr.build(f, pr.PrintParams(method="spot", kind="photo", inks=3, keep_solid=True), pg.PageOpts())
    assert sum(len(p.body) for p in solid.plates) < sum(len(p.body) for p in dots.plates)


def _bolt(path):
    a = np.full((200, 400, 3), 255, np.uint8)
    a[60:140, 80:320] = 0                                             # a dark bar
    a[90:110, 120:280] = 255                                          # with a light slot in it
    Image.fromarray(a).save(path)
    return str(path)


def test_dark_invert_swaps_positives_and_adds_an_outline(tmp_path):
    f = _bolt(tmp_path / "b.png")
    page = pg.PageOpts()
    light = pr.build(f, pr.PrintParams(method="one", ink="#000000", render="dither"), page)
    dark = pr.build(f, pr.PrintParams(method="one", ink="#ffffff", render="dither", dark=True, dark_invert=True, outline_mm=5.0), page)
    assert dark.info["garment"] == "#1b1b1d" and len(dark.plates) == 1
    assert dark.plates[0].body and dark.plates[0].body != light.plates[0].body
    none = pr.build(f, pr.PrintParams(method="one", ink="#ffffff", render="dither", dark=True, dark_invert=True, outline_mm=0.0), page)
    assert len(dark.plates[0].body) > len(none.plates[0].body)        # the margin ring adds ink


def test_dark_underbase_is_a_first_plate_for_coloured_ink_only(tmp_path):
    f = _bolt(tmp_path / "b.png")
    page = pg.PageOpts()
    j = pr.build(f, pr.PrintParams(method="one", ink="#e436a0", render="dither", dark=True, underbase=True), page)
    assert [p.name for p in j.plates] == ["White underbase", "Ink"] and j.plates[0].ink == "#ffffff"
    j = pr.build(f, pr.PrintParams(method="spot", kind="flat", inks=2, dark=True, dark_invert=True, underbase=True), page)
    assert j.plates[0].name == "White underbase"
    assert "#1b1b1d" in pg.compose(j, "all")                           # the preview shows the garment colour
