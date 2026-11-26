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


def _old_svg_names(rels):
    """svg_names as released in v0.9.0."""
    from collections import Counter
    from pathlib import Path
    key = lambda r: Path(r).with_suffix("").as_posix().lower()
    count = Counter(key(r) for r in rels)
    return {r: r + ".svg" if count[key(r)] > 1 else Path(r).with_suffix(".svg").as_posix() for r in rels}


def _three_that_clash(src, tmp_path):
    """a.png and a.jpg share a name, so a.png becomes a.png.svg: which is what a.png.webp would get too."""
    from PIL import Image
    src.mkdir()
    make_lineart(src / "a.png")
    Image.open(make_color(tmp_path / "c.png")).convert("RGB").save(src / "a.jpg")
    Image.open(make_lineart(tmp_path / "w.png", invert=True)).save(src / "a.png.webp")
    return {f.name: run_pipeline(f, Params()).svg for f in src.iterdir()}       # each picture's own trace


def _saved(out):
    """What `run` has saved: {picture: its SVG's text}, by the newest record of each picture."""
    recs = {}
    for line in (out / "manifest.jsonl").read_text().splitlines():
        recs[json.loads(line)["source"]] = json.loads(line)
    return {src: (out / rec["svg"]).read_text(encoding="utf-8") for src, rec in recs.items()}


def test_cli_run_never_writes_two_pictures_to_one_svg(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    own = _three_that_clash(src, tmp_path)
    runner = CliRunner()
    r = runner.invoke(main, ["run", str(src), str(out), "--workers", "1"])
    assert r.exit_code == 0, r.output
    assert sorted(p.name for p in (out / "svg").iterdir()) == ["a.jpg.svg", "a.png.svg", "a.png.webp.svg"]
    assert _saved(out) == own and len(set(own.values())) == 3                  # every picture has its own trace (one used to be lost)
    assert "3 skipped" in runner.invoke(main, ["run", str(src), str(out), "--workers", "1"]).output


def test_svg_names_are_unique_and_names_that_were_fine_stay(tmp_path):
    import random
    from collections import Counter
    from pathlib import Path
    from boottool.cli import svg_names
    assert svg_names(["a.png", "b.jpg", "sub/c.gif"]) == {"a.png": "a.svg", "b.jpg": "b.svg", "sub/c.gif": "sub/c.svg"}
    assert svg_names(["a.png", "A.jpg"]) == {"a.png": "a.png.svg", "A.jpg": "A.jpg.svg"}
    assert svg_names(["a.png", "a.jpg", "a.png.webp"]) == {"a.jpg": "a.jpg.svg", "a.png": "a.png.svg", "a.png.webp": "a.png.webp.svg"}
    assert svg_names(["Sub/a.png", "sub/a.png"]) == {"Sub/a.png": "Sub/a.png.svg", "sub/a.png": "sub/a.png-2.svg"}
    rng = random.Random(7)
    words = ["a", "A", "a.png", "a.png-2", "b", "a.png.svg", "x-2"]
    for _ in range(2000):
        rels = sorted({"/".join([rng.choice(words) for _ in range(rng.randint(0, 1))] + [rng.choice(words) + rng.choice([".png", ".jpg", ".PNG", ".webp"])])
                       for _ in range(rng.randint(1, 8))})
        if len({r.lower() for r in rels}) < len(rels):
            continue                                       # (two names that differ only in case can't lie side by side on Mac or Windows)
        names = svg_names(rels)
        shuffled = rels[:]
        rng.shuffle(shuffled)
        assert svg_names(shuffled) == names                                            # the same names whatever the order
        assert len({n.lower() for n in names.values()}) == len(rels), (rels, names)   # never two pictures on one SVG
        old = _old_svg_names(rels)
        old_count = Counter(n.lower() for n in old.values())
        for r, n in names.items():
            assert Path(n).parent == Path(r).parent and n.endswith(".svg"), (r, n)     # in its own folder
            if old_count[old[r].lower()] == 1:
                assert n == old[r], (rels, names, old)                                 # a name that was fine is unchanged


def test_run_again_puts_right_a_folder_where_two_pictures_shared_an_svg(tmp_path, monkeypatch):
    """An output folder made by v0.9.0, with one picture lost under another's name: running again
    used to skip all three ("already done"), because some SVG existed under each name."""
    src, out = tmp_path / "in", tmp_path / "out"
    own = _three_that_clash(src, tmp_path)
    runner = CliRunner()
    with monkeypatch.context() as m:
        m.setattr("boottool.cli.svg_names", _old_svg_names)
        assert runner.invoke(main, ["run", str(src), str(out), "--workers", "1"]).exit_code == 0
    assert sorted(p.name for p in (out / "svg").iterdir()) == ["a.jpg.svg", "a.png.svg"]   # the mix-up, as it was
    r = runner.invoke(main, ["run", str(src), str(out), "--workers", "1"])
    assert r.exit_code == 0 and "1 skipped" in r.output, r.output                      # a.jpg was right; the other two are done again
    assert _saved(out) == own
    assert "3 skipped" in runner.invoke(main, ["run", str(src), str(out), "--workers", "1"]).output


def test_run_again_when_a_picture_is_replaced_by_one_traced_before(tmp_path):
    """b.png is copied over a.png. Both names have an SVG and the picture "was traced before", so
    a.png used to be skipped: a.svg went on showing the old a.png."""
    import shutil
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    make_lineart(src / "a.png")
    make_color(src / "b.png")
    runner = CliRunner()
    assert "2 to process" in runner.invoke(main, ["run", str(src), str(out), "--workers", "1"]).output
    old_a = (out / "svg" / "a.svg").read_text(encoding="utf-8")
    shutil.copyfile(src / "b.png", src / "a.png")
    r = runner.invoke(main, ["run", str(src), str(out), "--workers", "1"])
    assert "1 skipped" in r.output and "1 to process" in r.output, r.output
    new_a = (out / "svg" / "a.svg").read_text(encoding="utf-8")
    assert new_a == (out / "svg" / "b.svg").read_text(encoding="utf-8") != old_a
    assert "2 skipped" in runner.invoke(main, ["run", str(src), str(out), "--workers", "1"]).output


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
