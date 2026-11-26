"""The self-check (drawing the finished SVG again and comparing it with the picture) when it cannot be done."""
import json
import shutil
import subprocess

import pytest
from click.testing import CliRunner

import boottool.score as sc
from boottool import Params
from boottool.cli import main
from boottool.preprocess import prepare

from conftest import make_lineart

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400"><path d="M10 10h100v100z"/></svg>'


def _no_rasteriser(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda name: None)
    monkeypatch.setitem(__import__("sys").modules, "cairosvg", None)


def _broken_rasteriser(monkeypatch, error=None):
    """rsvg-convert is "installed", and fails on every SVG."""
    error = error or subprocess.CalledProcessError(1, ["rsvg-convert"], stderr=b"Error reading SVG in.svg: XML parse error\n\n")

    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/rsvg-convert")
    monkeypatch.setattr(sc.subprocess, "run", fail)


def test_a_check_that_fails_is_not_taken_for_no_rasteriser(lineart, monkeypatch):
    """Without a rasteriser the check is skipped: no scores, and nothing wrong. With one that fails,
    there are no scores either, but the reason is kept. (Both used to look the same: skipped.)"""
    prep = prepare(lineart, Params())
    _no_rasteriser(monkeypatch)
    assert sc.score_or_none(SVG, prep) == sc.NO_SCORES
    _broken_rasteriser(monkeypatch)
    got = sc.score_or_none(SVG, prep)
    assert {k: got[k] for k in sc.NO_SCORES} == sc.NO_SCORES
    assert got["error"] == "rsvg-convert: Error reading SVG in.svg: XML parse error"
    for error, said in (
        (subprocess.CalledProcessError(-11, ["rsvg-convert"]), "rsvg-convert: crashed (signal 11)"),        # it crashed, and said nothing
        (subprocess.CalledProcessError(4, ["rsvg-convert"], stderr=b" \n"), "rsvg-convert: stopped with exit code 4"),
        (subprocess.CalledProcessError(1, ["rsvg-convert"], stderr=b"Error reading SVG /tmp/tmpab12/in.svg: \x1b[2Jbad\x07\tXML\xff\n"),
         "rsvg-convert: Error reading SVG in.svg: [2Jbad XML\ufffd"),                                       # no temporary path, nothing unprintable
        (FileNotFoundError(2, "No such file or directory", "/tmp/tmpab12/out.png"), "rsvg-convert gave no picture that can be read"),
    ):
        _broken_rasteriser(monkeypatch, error)
        assert sc.score_or_none(SVG, prep)["error"] == said
    _broken_rasteriser(monkeypatch, PermissionError(13, "Permission denied"))                        # it could not be started
    assert sc.score_or_none(SVG, prep)["error"].startswith("PermissionError: ")
    _broken_rasteriser(monkeypatch, ValueError("x" * 1000))                                          # anything else, kept short
    assert len(sc.score_or_none(SVG, prep)["error"]) == 300 and sc.score_or_none(SVG, prep)["error"].endswith("…")


def test_cairosvg_without_its_cairo_library_counts_as_no_rasteriser(lineart, monkeypatch):
    """`pip install cairosvg` on a machine without the cairo library gives a cairosvg that cannot be
    loaded (an OSError, not an ImportError). That is "no rasteriser" too: the check is skipped, as
    the note that sends people to cairosvg says it would be."""
    import builtins
    real_import = builtins.__import__

    def no_cairo(name, *a, **kw):
        if name == "cairosvg":
            raise OSError('no library called "cairo-2" was found')
        return real_import(name, *a, **kw)
    monkeypatch.setattr(sc.shutil, "which", lambda name: None)
    monkeypatch.delitem(__import__("sys").modules, "cairosvg", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_cairo)
    assert sc.score_or_none(SVG, prepare(lineart, Params())) == sc.NO_SCORES


@pytest.mark.skipif(not shutil.which("rsvg-convert"), reason="needs rsvg-convert")
def test_what_the_real_rasteriser_says_is_passed_on(lineart):
    got = sc.score_or_none("<svg", prepare(lineart, Params()))               # not an SVG: rsvg-convert refuses it
    assert got["ssim_target"] is None and got["error"].startswith("rsvg-convert: ") and "SVG" in got["error"]
    assert "error" not in sc.score_or_none(SVG, prepare(lineart, Params()))   # and a good one is scored as ever


def test_the_command_line_says_when_the_check_failed(lineart, tmp_path, monkeypatch):
    """`trace` and `run` said "skipped because no SVG rasteriser was found" for a check that had
    failed. They say that it failed, and why; the SVGs are written as before."""
    _broken_rasteriser(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "o.svg"
    r = runner.invoke(main, ["trace", str(lineart), str(out)])
    assert r.exit_code == 0, r.output
    assert "self-check could not be done for this picture: rsvg-convert: Error reading SVG" in r.output
    assert "no SVG rasteriser was found" not in r.output
    scores = json.loads(r.output[r.output.index("{"):])["scores"]
    assert scores["ssim_target"] is None and scores["error"].startswith("rsvg-convert: ")
    assert out.read_text(encoding="utf-8").lstrip().startswith("<")

    src, dst = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    make_lineart(src / "a.png")
    make_lineart(src / "b.png", invert=True)
    r = runner.invoke(main, ["run", str(src), str(dst), "--workers", "1"])
    assert r.exit_code == 0, r.output
    assert r.output.count("  self-check failed") == 2                       # each picture's line says so
    assert "self-check could not be done for 2 pictures (marked above). The first, a.png: rsvg-convert: Error reading SVG" in r.output
    assert "no SVG rasteriser was found" not in r.output
    recs = [json.loads(line) for line in (dst / "manifest.jsonl").read_text().splitlines()]
    recs = list({rec["source"]: rec for rec in recs}.values())              # (each picture's last record)
    assert [rec["status"] for rec in recs] == ["ok", "ok"] and all(rec["scores"]["error"] for rec in recs)
    assert (dst / "svg" / "a.svg").exists() and (dst / "svg" / "b.svg").exists()


def test_the_command_line_still_says_skipped_without_a_rasteriser(lineart, tmp_path, monkeypatch):
    _no_rasteriser(monkeypatch)
    r = CliRunner().invoke(main, ["trace", str(lineart), str(tmp_path / "o.svg")])
    assert r.exit_code == 0 and "self-check was skipped because no SVG rasteriser was found" in r.output
    assert "could not be done" not in r.output
