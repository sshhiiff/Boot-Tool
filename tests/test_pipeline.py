import json

from click.testing import CliRunner
from lxml import etree

from boottool import Params, run_pipeline
from boottool.cli import main

from conftest import make_color, make_lineart


def _valid_svg(text):
    root = etree.fromstring(text.encode())
    assert root.tag.endswith("svg")
    return root


def test_lineart(lineart):
    res = run_pipeline(lineart)
    _valid_svg(res.svg)
    assert res.mode == "lineart"
    assert res.stats["path_count"] >= 1
    assert res.scores["ssim_target"] >= 0.90


def test_lineart_inverted_polarity(lineart_inverted):
    res = run_pipeline(lineart_inverted)
    assert res.mode == "lineart"
    assert res.scores["ssim_target"] >= 0.90


def test_color(color):
    res = run_pipeline(color)
    _valid_svg(res.svg)
    assert res.mode == "color"
    assert res.stats["color_count"] >= 3
    assert res.scores["ssim_target"] >= 0.90


def test_no_corner_overshoot(lineart):
    """Regression: curve handles used to spike past corners. SSIM is too coarse to see it,
    so check the rendered ink never extends beyond the source ink's bounding box."""
    import numpy as np
    from boottool.preprocess import prepare
    from boottool.score import rasterize

    p = Params()
    prep = prepare(lineart, p)
    res = run_pipeline(lineart, p)
    ink = rasterize(res.svg, *prep.size).min(axis=2) < 128
    (sy, sx), (oy, ox) = np.nonzero(prep.mask), np.nonzero(ink)
    for src, out in ((sx, ox), (sy, oy)):
        assert out.min() >= src.min() - 2 and out.max() <= src.max() + 2


def test_fewer_nodes_with_higher_epsilon(lineart):
    tight = run_pipeline(lineart, Params(epsilon=0.5))
    loose = run_pipeline(lineart, Params(epsilon=3.0))
    assert loose.stats["node_count"] < tight.stats["node_count"]


def test_params_key_ignores_min_score():
    assert Params(min_score=0.5).key() == Params(min_score=0.99).key()
    assert Params(epsilon=1.0).key() != Params().key()


def test_cli_run_resume_report_dataset(tmp_path):
    src = tmp_path / "in"
    (src / "sub").mkdir(parents=True)
    make_lineart(src / "a.png")
    make_color(src / "sub" / "b.png")
    (src / "broken.png").write_bytes(b"not an image")
    prov = tmp_path / "prov.csv"
    prov.write_text("file,license,source\na.png,PD-US pre-1929,example archive\n")
    out = tmp_path / "out"
    runner = CliRunner()

    r = runner.invoke(main, ["run", str(src), str(out), "--workers", "1",
                             "--provenance", str(prov)])
    assert r.exit_code == 0, r.output
    lines = (out / "manifest.jsonl").read_text().splitlines()
    recs = {json.loads(line)["source"]: json.loads(line) for line in lines}
    assert recs["a.png"]["status"] == "ok" and recs["a.png"]["provenance_declared"]
    assert recs["sub/b.png"]["status"] == "ok" and not recs["sub/b.png"]["provenance_declared"]
    assert recs["broken.png"]["status"] == "error"
    assert (out / "svg" / "a.svg").exists() and (out / "svg" / "sub" / "b.svg").exists()

    # resume: nothing to redo except the broken file
    r2 = runner.invoke(main, ["run", str(src), str(out), "--workers", "1",
                              "--provenance", str(prov)])
    assert "2 skipped" in r2.output

    assert runner.invoke(main, ["report", str(out)]).exit_code == 0
    assert (out / "report.html").exists()

    r3 = runner.invoke(main, ["dataset", str(out), "--require-provenance", "--val-frac", "0"])
    assert r3.exit_code == 0, r3.output
    ds = json.loads((out / "dataset.json").read_text())
    assert ds["train"] == ["svg/a.svg"]
    assert ds["excluded"]["no_provenance"] == 1 and ds["excluded"]["error"] == 1


def test_cli_trace(lineart, tmp_path):
    out = tmp_path / "o.svg"
    r = CliRunner().invoke(main, ["trace", str(lineart), str(out)])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["scores"]["ssim_target"] >= 0.90
    _valid_svg(out.read_text())


def test_upscale_and_level(lineart):
    from boottool.preprocess import prepare
    base = prepare(lineart, Params(min_side=1))
    up = prepare(lineart, Params(min_side=1, upscale=3.0))
    assert up.size == (base.size[0] * 3, base.size[1] * 3)
    # a very low manual threshold keeps less ink than a high one
    lo = prepare(lineart, Params(level=40)).mask.sum()
    hi = prepare(lineart, Params(level=200)).mask.sum()
    assert lo < hi


def test_sepia_scan_is_lineart(tmp_path):
    import numpy as np
    from PIL import Image
    from boottool.preprocess import detect_mode
    grey = np.asarray(Image.open(make_lineart(tmp_path / "g.png")).convert("L"), dtype=np.float32) / 255
    sepia = np.stack([grey * 200, grey * 175, grey * 120], axis=2).astype(np.uint8)
    assert detect_mode(sepia) == "lineart"


def test_downscale_keeps_declared_size(lineart):
    res = run_pipeline(lineart, Params(upscale=0.25, min_side=768))
    root = _valid_svg(res.svg)
    assert res.size[0] < 200                              # worked small, not forced up to 768
    assert int(root.get("width")) == 600 and int(root.get("height")) == 400   # opens at source size
    assert root.get("viewBox") == f"0 0 {res.size[0]} {res.size[1]}"
