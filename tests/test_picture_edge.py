"""Colours that reach the edge of the picture: flat-colour plates (Print) and the Separate tool."""
import re

import cv2
import numpy as np
import pytest
from PIL import Image

from boottool import page as pg
from boottool import printing as pr
from boottool import separate as sep
from boottool import trace as tr
from boottool.config import Params

RED = (200, 30, 30)
TWO_METRES = dict(size="Custom", custom_w=2000, art_w=2000.0)


def path_rings(d, px=1.0):
    """The outlines of a path `d` as it is drawn: each a list of points, curves turned into lines that
    keep within a tenth of a pixel of them (`px`: how many pixels one unit of the path will be)."""
    rings, ring, at = [], [], np.zeros(2)
    for cmd, rest in re.findall(r"([MLCZzhv])([^MLCZzhv]*)", d):
        n = [float(v) for v in re.findall(r"-?[\d.]+", rest)]
        if cmd == "M":
            if ring:
                rings.append(ring)
            at = np.array(n[:2])
            ring = [at]
        elif cmd == "L":
            at = np.array(n[:2])
            ring.append(at)
        elif cmd == "C":
            c1, c2, end = np.array(n[0:2]), np.array(n[2:4]), np.array(n[4:6])
            bend = max(np.abs(at - 2 * c1 + c2).max(), np.abs(c1 - 2 * c2 + end).max()) * px
            steps = max(12, int(np.ceil((bend / 0.1) ** 0.5)))
            ring += [(1 - t) ** 3 * at + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * end for t in np.linspace(0, 1, steps + 1)[1:]]
            at = end
        elif cmd in "hv":
            at = at + (np.array([n[0], 0.0]) if cmd == "h" else np.array([0.0, n[0]]))
            ring.append(at)
    if ring:
        rings.append(ring)
    return [np.array(r) for r in rings]


def plate_mask(job, plate, per_mm=20, lean=False):
    """A plate as it is drawn (curves and all, each path by its own fill rule), as a mask over the
    artwork: `per_mm` pixels to the millimetre, from 2 mm outside the artwork's corner. Pixels an
    outline only passes through are counted in, or with `lean` counted out: so the mask is a touch
    too wide, or a touch too narrow."""
    ox, oy, aw, ah = job.art
    out = np.zeros((int((ah + 4) * per_mm), int((aw + 4) * per_mm)), np.uint8)
    for tx, ty, sx, sy, how, d in re.findall(r'translate\(([\d.-]+) ([\d.-]+)\) scale\(([\d.]+) ?([\d.]*)\)"><path([^>]*) d="([^"]+)"', plate.body):
        tx, ty, sx, sy = float(tx), float(ty), float(sx), float(sy or sx)
        page = [np.rint((r * (sx, sy) + (tx - ox + 2, ty - oy + 2)) * per_mm * 16).astype(np.int32) for r in path_rings(d, max(sx, sy) * per_mm)]
        if "evenodd" in how:
            cv2.fillPoly(out, page, 1, lineType=cv2.LINE_8, shift=4)
        else:                                                               # the default rule (nonzero): what any of its rings covers
            for r in page:                                                  # (all of ours go round the same way)
                cv2.fillPoly(out, [r], 1, lineType=cv2.LINE_8, shift=4)
    if lean:
        out = cv2.erode(out, cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3)))
    return out.astype(bool)


def beyond_the_picture(job, plate):
    """How far a plate's path reaches outside the picture, in the pixels it was traced on: the
    furthest that any of its points or curve handles lies beyond a side (0 if none does). A curve
    stays within the box of its handles, so this bounds all that is drawn."""
    sx, sy, d = re.search(r'scale\(([\d.]+) ?([\d.]*)\)"><path[^>]* d="([^"]+)"', plate.body).groups()
    w, h = job.art[2] / float(sx), job.art[3] / float(sy or sx)             # the traced picture's size
    n = np.array([float(v) for v in re.findall(r"-?[\d.]+", d)]).reshape(-1, 2)
    return max(0.0, -n.min(), n[:, 0].max() - w, n[:, 1].max() - h)


def flat(tmp_path, a, page=None, **more):
    """The picture `a` as flat colour for one red ink (no white underbase), on the page given."""
    f = tmp_path / "red.png"
    Image.fromarray(a).save(f)
    return pr.build(f, pr.PrintParams(method="spot", kind="flat", inks=1, picked=True, colors=("#c81e1e",), trap=0.0, **more),
                    page or pg.PageOpts())


def _edge_shapes():
    full = np.ones((40, 60), bool)
    block = np.zeros((40, 60), bool)
    block[0:20, 15:45] = True
    corner = np.zeros((40, 60), bool)
    corner[0:20, 0:30] = True
    across = np.zeros((40, 60), bool)
    across[14:26] = True
    frame = np.ones((40, 60), bool)
    frame[10:30, 10:50] = False
    ell = np.zeros((40, 60), bool)
    ell[:, 0:10] = True
    ell[30:] = True
    notch = block.copy()
    notch[0:8, 28:32] = False
    step = block.copy()
    step[20, 20:26] = True
    teeth = np.zeros((40, 60), bool)
    for k in range(6):
        teeth[0:12 + k % 3, 4 + 9 * k:9 + 9 * k] = True
    return {"full": full, "block": block, "corner": corner, "across": across, "frame": frame, "two sides": ell,
            "notch": notch, "step": step, "teeth": teeth, "all but a corner": ~corner}


@pytest.mark.parametrize("sigma", [0.0, 0.6, 1.0, 2.0, 2.2])
def test_outlines_of_a_picture_cut_off_at_its_sides(sigma):
    """Tracing a picture that is cut off at its sides (a colour plate). An outline that reaches a
    side used to run half a pixel out of the picture and be closed there by a straight line: across
    a block in a corner, and losing the outer edge of a shape that fills the picture or crosses it.
    It is cut off on the picture's sides now and closed along them, and nothing of it lies outside
    the picture. Within the picture nothing else changes for these shapes: every point of the outline
    as it was is still there, and no other point is added."""
    p = Params(smooth_sigma=sigma)
    for name, m in _edge_shapes().items():
        h, w = m.shape
        polys = tr.mask_to_polys(m, p, edge=True)
        d = tr.polygons_to_path(polys, p.corner_deg, 3)
        numbers = np.array([float(v) for v in re.findall(r"-?[\d.]+", d)]).reshape(-1, 2)
        assert numbers.min() >= -0.021 and numbers[:, 0].max() <= w + 0.021 and numbers[:, 1].max() <= h + 0.021, (name, "beyond the picture")
        k = 8                                                               # drawn 8 times the size
        got = np.zeros((h * k, w * k), np.uint8)
        cv2.fillPoly(got, [np.rint((r * k - 0.5) * 16).astype(np.int32) for r in path_rings(d, k)], 1, lineType=cv2.LINE_8, shift=4)
        padded = np.pad(m.astype(np.float32), 1)                            # what is traced: the smoothed mask, at a half
        smooth = cv2.GaussianBlur(padded, (0, 0), sigma) if sigma else padded
        want = np.repeat(np.repeat(smooth[1:-1, 1:-1] > 0.5, k, axis=0), k, axis=1).astype(np.uint8)
        box = np.ones((2 * 2 * k + 1, 2 * 2 * k + 1), np.uint8)             # two pixels either way: simplifying moves an outline
        sure_in = cv2.erode(want, box, borderType=cv2.BORDER_CONSTANT, borderValue=1).astype(bool)   # (the picture's sides are no outline)
        sure_in[:k] = sure_in[-k:] = sure_in[:, :k] = sure_in[:, -k:] = False   # (but a corner of the picture may be rounded a little)
        sure_out = ~cv2.dilate(want, box).astype(bool)
        assert got.astype(bool)[sure_in].all() and not got.astype(bool)[sure_out].any(), name   # away from the outline: exactly right
        inner = lambda q: q[(q[:, 0] > 1e-3) & (q[:, 0] < w - 1e-3) & (q[:, 1] > 1e-3) & (q[:, 1] < h - 1e-3)]   # noqa: E731
        before = tr.mask_to_polys(m, p)                                     # (nothing at all, for some of these)
        was = {tuple(v) for v in inner(np.concatenate(before)).tolist()} if before else set()
        now = {tuple(v) for v in inner(np.concatenate(polys)).tolist()}
        assert now == was, (name, sorted(now ^ was)[:4])


@pytest.mark.parametrize("sigma", [0.0, 0.6, 2.2])
def test_outlines_away_from_the_sides_are_not_touched(sigma):
    """Shapes that keep two pixels away from the picture's sides are traced exactly as before: the
    same path, character for character, at the smoothing the plates use (2.2) or less. (Nearer than
    that, or smoothed more, an outline can be carried over the side, and what lies outside the
    picture is then cut off.) This is here so that it stays so; it was never otherwise."""
    rng = np.random.default_rng(7)
    for _ in range(12):
        m = cv2.GaussianBlur(rng.random((70, 90)).astype(np.float32), (0, 0), 3) > 0.5
        m[:2] = m[-2:] = False
        m[:, :2] = m[:, -2:] = False
        p = Params(smooth_sigma=sigma)
        assert m.any() and tr.mask_to_path(m, p, edge=True) == tr.mask_to_path(m, p)


def test_a_block_in_a_corner_of_the_picture_is_whole(tmp_path):
    """A block of colour in a corner reaches two sides of the picture. Its plate is the whole block
    (it was cut off slantwise, from one side to the other), up to the picture's edge and not beyond."""
    a = np.full((100, 100, 3), 255, np.uint8)
    a[:40, :40] = RED
    j = flat(tmp_path, a)
    assert [p.name for p in j.plates] == ["Red"]
    mm = j.art[2] / 100
    ink = plate_mask(j, j.plates[0], 10)
    assert abs(ink.sum() / 100 - (40 * mm) ** 2) < 0.04 * (40 * mm) ** 2    # the whole block
    assert ink[25:int((2 + 35 * mm) * 10), 25:int((2 + 35 * mm) * 10)].all()   # corner and all
    assert not ink[:19].any() and not ink[:, :19].any()                    # nothing outside the picture
    assert beyond_the_picture(j, j.plates[0]) < 0.03


def test_the_separate_tool_draws_colours_at_the_edge_whole(tmp_path):
    """The Separate tool traces its plates the same way: a band right across the picture and a block
    in a corner are whole (the band's plate was two slivers, the block cut off slantwise)."""
    a = np.full((120, 160, 3), 255, np.uint8)
    a[50:80, :] = RED
    a[:30, :40] = (30, 30, 200)
    f = tmp_path / "band.png"
    Image.fromarray(a).save(f)
    j = sep.build(f, sep.SepParams(inks=2, trap=0.0), pg.PageOpts())
    assert len(j.plates) == 2
    mm = j.art[2] / 160
    areas = sorted(plate_mask(j, p, 10).sum() / 100 for p in j.plates)
    assert abs(areas[0] - 30 * 40 * mm * mm) < 0.06 * 30 * 40 * mm * mm     # the block
    assert abs(areas[1] - 30 * 160 * mm * mm) < 0.06 * 30 * 160 * mm * mm   # the band
    assert all(beyond_the_picture(j, p) < 0.03 for p in j.plates)


def test_with_the_background_printed_its_plate_is_the_background(tmp_path):
    """With "paper" off the background is printed as an ink. Its plate is the background: all of the
    picture but the block in it. (It came out as the block alone, because the plate's outer edge,
    which lies on the picture's edge, was lost.)"""
    a = np.full((300, 300, 3), 255, np.uint8)
    a[100:200, 60:240] = RED
    f = tmp_path / "block.png"
    Image.fromarray(a).save(f)
    j = pr.build(f, pr.PrintParams(method="spot", kind="flat", inks=2, paper=False, trap=0.0), pg.PageOpts())
    assert [p.name for p in j.plates] == ["White", "Red"]
    aw, ah = j.art[2], j.art[3]
    ground, block = plate_mask(j, j.plates[0], 10), plate_mask(j, j.plates[1], 10)
    assert abs(ground.sum() / 100 - (aw * ah - block.sum() / 100)) < 0.02 * aw * ah
    assert ground[25:40, 25:40].all() and not ground[int((2 + ah / 2) * 10), int((2 + aw / 2) * 10)]   # its corner is ink, the block's middle is not
    assert (ground & block).sum() < 0.03 * block.sum()                      # (the two only share the line between them)
    assert beyond_the_picture(j, j.plates[0]) < 0.03


def test_a_picture_that_is_all_one_colour_gets_its_plate(tmp_path):
    """A picture filled with one colour up to all four sides has no outline inside it at all; the
    plate is the whole picture. (There was no plate.)"""
    a = np.full((80, 120, 3), 0, np.uint8)
    a[:] = RED
    j = flat(tmp_path, a)
    assert [p.name for p in j.plates] == ["Red"]
    ink = plate_mask(j, j.plates[0], 10)
    assert ink[20:-20, 20:-20].all() and not ink[:19].any() and not ink[:, :19].any()


@pytest.mark.parametrize("joined", [False, True])
def test_a_stroke_just_inside_the_edge_of_the_picture_is_kept(tmp_path, joined):
    """Two metres wide: a thin stroke one pixel away from the picture's edge is traced like any other
    shape, also when a single pixel joins it to the edge. (This was already so; it must stay so.)"""
    a = np.full((180, 3000, 3), 255, np.uint8)
    a[1:4, 1000:1140] = RED
    if joined:
        a[0:1, 1000:1001] = RED
    j = flat(tmp_path, a, pg.PageOpts(custom_h=200, **TWO_METRES))
    assert [p.name for p in j.plates] == ["Red"]
    ink = plate_mask(j, j.plates[0], 20)
    assert 60 < ink.sum() / 400 < 100                                       # mm²: the stroke, 93 mm long and nearly a millimetre thick
    assert not ink[:38].any()


def test_a_block_on_the_edge_of_a_large_picture_is_drawn_up_to_the_edge(tmp_path):
    """Two metres wide: one working pixel is two thirds of a millimetre. A block that lies on the
    picture's edge is drawn up to it, and not beyond it (it ran a third of a millimetre over)."""
    a = np.full((180, 3000, 3), 255, np.uint8)
    a[0:20, 920:998] = RED
    j = flat(tmp_path, a, pg.PageOpts(custom_h=200, **TWO_METRES))
    ink = plate_mask(j, j.plates[0], 40)
    along = slice(int((2 + 925 * 2000 / 3000) * 40), int((2 + 925 * 2000 / 3000 + 25) * 40))   # 25 mm of the block's top
    short = (~ink[2 * 40: 3 * 40, along]).sum(axis=0) / 40                  # mm from the picture's top edge to the ink, per column
    assert short.mean() < 0.1, short.mean()
    assert not ink[: 2 * 40 - 2, along].any()                               # and nothing beyond the picture


def test_a_notch_in_a_colour_at_the_edge_of_a_large_picture_stays_open(tmp_path):
    """Two metres wide: a block of colour on the picture's top edge with a slit of paper cut into it
    from the edge, 2 mm wide and 10 mm deep. The slit is as wide where it meets the edge as further
    in (that was already so, and must stay so), and the block to either side of it ends on the edge
    (it ran a third of a millimetre over)."""
    a = np.full((180, 3000, 3), 255, np.uint8)
    a[0:55, 500:1100] = RED
    a[0:15, 800:803] = 255
    j = flat(tmp_path, a, pg.PageOpts(custom_h=200, **TWO_METRES))
    ink = plate_mask(j, j.plates[0], 40)
    mm = 2000 / 3000
    near = slice(int((2 + 795 * mm) * 40), int((2 + 808 * mm) * 40))        # across the slit
    for depth in (0.05, 0.5, 1.0, 2.0):                                     # mm below the picture's top edge
        open_mm = (~ink[int((2 + depth) * 40), near]).sum() / 40
        assert 1.3 < open_mm < 2.4, (depth, open_mm)                        # (smoothing narrows it a little, further in)
    assert not ink[:78, near].any()                                         # and the block ends on the edge, to either side of it too


def test_a_stripe_along_the_edge_of_a_large_picture_lies_on_the_edge(tmp_path):
    """Two metres wide: a stripe of colour two pixels thick along the picture's top edge. What is
    left of it after smoothing is drawn from the edge down; nothing is drawn above the edge (it ran
    half a pixel, 0.67 mm, out of the picture)."""
    a = np.full((180, 1500, 3), 255, np.uint8)
    a[0:2, 75:1425] = RED
    j = flat(tmp_path, a, pg.PageOpts(custom_h=300, **TWO_METRES))
    ink = plate_mask(j, j.plates[0], 40)
    middle = slice((2 + 400) * 40, (2 + 1600) * 40)
    rows = np.flatnonzero(ink[:, middle].any(axis=1))
    top, thick = rows[0] / 40 - 2, (rows[-1] + 1 - rows[0]) / 40
    assert -0.03 <= top <= 0.05, top                                        # from the edge, not above it
    assert 1.0 < thick < 1.5, thick                                         # (2 px are 2.67 mm; smoothing leaves less than half)
    assert ink[rows[0] + 2:rows[0] + 32, middle].all()                      # solid for its first 0.8 mm, all along


@pytest.mark.parametrize("side", ["top", "bottom"])
def test_small_marks_on_the_edge_of_a_large_picture_are_kept(tmp_path, side):
    """Two metres wide: a hairline along the picture's edge with fifty short dashes hanging from it.
    Each dash used to be drawn half outside the picture; cut off at the edge, less than a pixel of it
    is left. It is still drawn (what was large enough to be drawn before it was cut off is not
    dropped as a speck), at the top edge and at the bottom one, 1500 pixels from the picture's
    corner. (All but the last, which runs into the hairline's end.)"""
    a = np.full((1500, 1500, 3), 255, np.uint8)
    a[0, 201:1191] = RED
    for x in range(206, 1190, 20):
        a[1, x:x + 6] = RED
    if side == "bottom":
        a = a[::-1, ::-1].copy()
    j = flat(tmp_path, a, pg.PageOpts(custom_h=2000, **TWO_METRES), speckle=0)
    assert [p.name for p in j.plates] == ["Red"]
    assert 46 <= j.plates[0].body.count("M") <= 50                         # the dashes (49 here; none was left at first, then 41)
    assert beyond_the_picture(j, j.plates[0]) < 0.03                        # and nothing outside the picture


def test_a_curve_beside_the_edge_of_the_picture_does_not_bulge_over_it(tmp_path):
    """Two metres wide: a small wavy shape on the picture's top edge. Its outline is cut off on the
    edge; the curve drawn through the outline's points must not swing back out over the edge on its
    way to the first point inside. A curve stays within the box of its handles, so none of those may
    lie outside the picture."""
    a = np.full((120, 1500, 3), 255, np.uint8)
    cols = np.arange(22)
    top, bottom = np.interp(cols, [0, 7, 14, 21], [-1, 1, 0, 3]), np.interp(cols, [0, 7, 14, 21], [7, 5, 3, 7])
    rows = np.arange(120)[:, None]
    a[:, 8:30][(rows >= top[None]) & (rows < bottom[None])] = RED
    j = flat(tmp_path, a, pg.PageOpts(custom_h=200, **TWO_METRES), speckle=0)
    assert [p.name for p in j.plates] == ["Red"]
    assert j.plates[0].body.count("C") > 5
    assert beyond_the_picture(j, j.plates[0]) < 0.03
