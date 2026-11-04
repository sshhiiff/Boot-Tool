from boottool.separate import SepParams  # noqa: F401  (import check only)
from boottool import simplify
from boottool.trace import Params


def test_very_large_svg_is_parsed():
    """Big, detailed images make SVGs over 10 MB; lxml refuses those unless huge_tree is on."""
    body = '<path d="M0 0L1 1L2 0Z" fill="#000000"/>' * 450000
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">{body}</svg>'
    assert len(svg) > 15_000_000
    out = simplify.postprocess(svg, Params())
    assert out.startswith("<svg")
    assert simplify.svg_stats(out)["path_count"] == 450000
