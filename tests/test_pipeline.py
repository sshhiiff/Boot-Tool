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


def test_more_colours_than_256_are_not_mixed_up(tmp_path):
    """`--colors 300` used to give 300 colours numbers that only go up to 255: the last 44 were
    counted from 0 again, and their parts of the picture came out in other colours' colours."""
    import re
    import numpy as np
    from PIL import Image
    from boottool.preprocess import prepare
    blocks = np.random.default_rng(0).integers(0, 256, (20, 20, 3), dtype=np.uint8)       # 400 different flat colours
    picture = np.kron(blocks, np.ones((48, 48, 1), dtype=np.uint8))                       # 960 x 960: traced at its own size
    Image.fromarray(picture).save(tmp_path / "many.png")
    for asked in (256, 300):
        prep = prepare(tmp_path / "many.png", Params(mode="color", colors=asked, denoise=False))
        assert 250 <= len(prep.palette) <= 256
        traced = np.asarray(prep.palette)[prep.labels].astype(int)                         # every pixel in the colour it was given
        off = np.abs(traced - picture.astype(int)).max(axis=2)
        assert np.percentile(off, 99) < 60 and off.mean() < 12, asked                      # (400 colours in 256: each near its own)
    out = tmp_path / "many.svg"                                                            # and the command line takes "300" without a fuss
    res = CliRunner().invoke(main, ["trace", str(tmp_path / "many.png"), str(out), "--mode", "color", "--colors", "300"])
    assert res.exit_code == 0, res.output
    assert 200 < len(set(re.findall(r'fill="(#[0-9a-f]{6})"', out.read_text()))) <= 256


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


def test_cli_run_keeps_files_that_differ_only_by_extension(tmp_path):
    """a.png and a.jpg used to both write svg/a.svg, so one silently replaced the other."""
    from PIL import Image
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    make_lineart(src / "a.png")
    Image.open(make_color(tmp_path / "c.png")).convert("RGB").save(src / "A.jpg")      # same name apart from case and type
    make_lineart(src / "b.png", invert=True)
    runner = CliRunner()
    r = runner.invoke(main, ["run", str(src), str(out), "--workers", "1"])
    assert r.exit_code == 0, r.output
    recs = {json.loads(line)["source"]: json.loads(line) for line in (out / "manifest.jsonl").read_text().splitlines()}
    assert recs["a.png"]["svg"] == "svg/a.png.svg" and recs["A.jpg"]["svg"] == "svg/A.jpg.svg"
    assert recs["b.png"]["svg"] == "svg/b.svg"                                         # names that do not clash are unchanged
    assert recs["a.png"]["mode"] == "lineart" and recs["A.jpg"]["mode"] == "color"
    for rec in recs.values():
        _valid_svg((out / rec["svg"]).read_text())
    assert (out / "svg" / "a.png.svg").read_text() != (out / "svg" / "A.jpg.svg").read_text()
    assert "3 skipped" in runner.invoke(main, ["run", str(src), str(out), "--workers", "1"]).output
    assert runner.invoke(main, ["report", str(out)]).exit_code == 0
    page = (out / "report.html").read_text()
    assert "svg/a.png.svg" in page and "svg/A.jpg.svg" in page
    assert runner.invoke(main, ["dataset", str(out), "--val-frac", "0", "--min-score", "0"]).exit_code == 0
    assert sorted(json.loads((out / "dataset.json").read_text())["train"]) == ["svg/A.jpg.svg", "svg/a.png.svg", "svg/b.svg"]


def test_cli_trace(lineart, tmp_path):
    out = tmp_path / "o.svg"
    r = CliRunner().invoke(main, ["trace", str(lineart), str(out)])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["scores"]["ssim_target"] >= 0.90
    _valid_svg(out.read_text())


def test_cli_works_without_an_svg_rasteriser(lineart, tmp_path, monkeypatch):
    """The self-check is optional on the command line too: no rsvg/cairo means no scores, not a crash."""
    import boottool.score as sc
    monkeypatch.setattr(sc.shutil, "which", lambda name: None)
    monkeypatch.setitem(__import__("sys").modules, "cairosvg", None)
    runner = CliRunner()

    out = tmp_path / "o.svg"
    r = runner.invoke(main, ["trace", str(lineart), str(out)])
    assert r.exit_code == 0, r.output
    assert "self-check was skipped" in r.output
    assert json.loads(r.output[r.output.index("{"):])["scores"]["ssim_target"] is None
    _valid_svg(out.read_text())

    src, dst = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    make_lineart(src / "a.png")
    r = runner.invoke(main, ["run", str(src), str(dst), "--workers", "1"])
    assert r.exit_code == 0 and "self-check was skipped" in r.output, r.output
    rec = json.loads((dst / "manifest.jsonl").read_text().splitlines()[0])
    assert rec["status"] == "ok" and rec["scores"]["ssim_target"] is None
    _valid_svg((dst / "svg" / "a.svg").read_text())
    assert "1 skipped" in runner.invoke(main, ["run", str(src), str(dst), "--workers", "1"]).output   # resume still works

    assert runner.invoke(main, ["report", str(dst)]).exit_code == 0
    r = runner.invoke(main, ["dataset", str(dst)])
    assert r.exit_code == 0, r.output
    assert json.loads((dst / "dataset.json").read_text())["excluded"] == {"unscored": 1}   # quality unknown, so left out


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
