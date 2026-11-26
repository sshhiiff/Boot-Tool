"""Compose: combining shapes, moving them forward and back in the stack, and joining lines."""
import pytest

from test_compose import server  # noqa: F401 (a fixture)

FIVE = """(() => { const A = Compose._api, L = A.getL(); L.art.removeChildren();
  const r = (n, x, y, w, h) => { const p = new paper.Path.Rectangle({point: [x, y], size: [w, h], fillColor: '#000'}); p.data = {name: n, kind: 'shape'}; p.name = n; L.art.addChild(p); return p; };
  window.IT = {a: r('a', 10, 10, 40, 40), b: r('b', 30, 30, 40, 40), c: r('c', 20, 20, 60, 60), d: r('d', 60, 60, 30, 30), e: r('e', 0, 0, 5, 5)};
  A.commit(); })()"""                                                       # five squares, a at the back, e in front
STACK = "Compose._api.getL().art.children.map(c => c.data.name)"


@pytest.fixture
def compose(server):
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/")
        page.click('a[data-t="compose"]')
        page.wait_for_function("typeof Compose !== 'undefined' && Compose._S.ready")

        def after(selected, button):
            """Five squares, these ones selected (in this order), that button pressed: the stack from back to front."""
            page.evaluate(FIVE)
            page.evaluate(f"Compose._api.select([{', '.join('IT.' + n for n in selected)}])")
            page.click(f"#side .btn:text-is('{button}'), #side span:text-is('{button}')")
            return page.evaluate(STACK)
        yield page, after
        assert not errs, errs
        b.close()


def test_a_combined_shape_takes_the_place_of_the_shapes_it_was_made_from(compose):
    """It used to land too high in the stack: uniting the two shapes at the back put the result in
    front of the third."""
    page, after = compose
    assert after("ab", "Unite") == ["Shape", "c", "d", "e"]                # was c, Shape, d, e
    assert after("ac", "Unite") == ["b", "Shape", "d", "e"]                # where c was, behind d: was b, d, Shape, e
    assert after("ca", "Unite") == ["b", "Shape", "d", "e"]
    assert after("de", "Unite") == ["a", "b", "c", "Shape"]                # the front two: still in front
    assert after("bc", "Intersect") == ["a", "Shape", "d", "e"]


def test_forward_and_backward_move_the_selected_shapes_whatever_order_they_were_clicked_in(compose):
    """Two neighbours selected back-then-front did not move at all with Forward (front-then-back with
    Backward): each was moved past the other in turn."""
    page, after = compose
    for clicked in ("ab", "ba"):
        assert after(clicked, "Forward") == ["c", "a", "b", "d", "e"]      # "ab" used to stay a, b, c, d, e
    for clicked in ("cd", "dc"):
        assert after(clicked, "Backward") == ["a", "c", "d", "b", "e"]
    assert after("ac", "Forward") == ["b", "a", "d", "c", "e"]              # shapes apart each pass one
    assert after("de", "Forward") == ["a", "b", "c", "d", "e"]             # already in front: stay
    assert after("ab", "Backward") == ["a", "b", "c", "d", "e"]
    assert after("a", "Forward") == ["b", "a", "c", "d", "e"]
    for clicked in ("ab", "ba"):                                           # and to the very front or back they keep their own order
        assert after(clicked, "To front") == ["c", "d", "e", "a", "b"]
    for clicked in ("bd", "db"):
        assert after(clicked, "To back") == ["b", "d", "a", "c", "e"]


def test_join_ends_keeps_every_point_of_both_lines(compose):
    """Joining two lines connected their nearest ends by making one point of the two: the second
    line lost its end and changed shape."""
    page, _ = compose

    def joined(*lines):
        page.evaluate("""lines => { const A = Compose._api, L = A.getL(); L.art.removeChildren();
          const made = lines.map(pts => { const p = new paper.Path({segments: pts, strokeColor: '#000', strokeWidth: 1}); p.data = {name: 'line', kind: 'shape'}; L.art.addChild(p); return p; });
          A.commit(); A.select(made); }""", [list(map(list, line)) for line in lines])
        page.click("#side .btn:text-is('Join ends'), #side span:text-is('Join ends')")
        out = page.evaluate("Compose._api.getL().art.children.map(c => [c.closed, c.segments.map(s => [Math.round(s.point.x), Math.round(s.point.y)])])")
        assert len(out) == 1 and out[0][0] is False                         # one open line
        points = [tuple(pt) for pt in out[0][1]]
        return points if points[0] <= points[-1] else points[::-1]          # (either end first)

    assert joined([(10, 10), (50, 10)], [(60, 30), (100, 30)]) == [(10, 10), (50, 10), (60, 30), (100, 30)]   # was 10,10  50,10  100,30
    assert joined([(50, 10), (10, 10)], [(100, 30), (60, 30)]) == [(10, 10), (50, 10), (60, 30), (100, 30)]   # drawn the other way round
    assert joined([(10, 10), (50, 10)], [(100, 30), (60, 30)]) == [(10, 10), (50, 10), (60, 30), (100, 30)]
    assert joined([(10, 10), (50, 10)], [(50, 10), (90, 40)]) == [(10, 10), (50, 10), (90, 40)]             # ends that touch already are one point
    assert joined([(10, 10), (50, 10)], [(60, 30), (100, 30)], [(110, 50), (150, 50)]) == [(10, 10), (50, 10), (60, 30), (100, 30), (110, 50), (150, 50)]
