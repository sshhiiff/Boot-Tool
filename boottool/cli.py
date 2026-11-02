from __future__ import annotations

import html
import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import click

from . import __version__
from .config import Params
from .manifest import Manifest, latest_by_source, load_provenance, sha256_file
from .pipeline import run_pipeline

IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}

_PARAM_OPTIONS = [
    click.option("--mode", type=click.Choice(["auto", "lineart", "color"]), default="auto",
                 show_default=True, help="auto picks line art vs colour per image."),
    click.option("--backend", type=click.Choice(["builtin", "vtracer"]), default="builtin",
                 show_default=True),
    click.option("--colors", type=int, default=8, show_default=True, help="Palette size (colour)."),
    click.option("--threshold", type=click.Choice(["otsu", "adaptive"]), default="otsu",
                 show_default=True, help="Binarization for line art."),
    click.option("--ink", default="#000000", show_default=True,
                 help='Line-art ink colour, or "auto" to sample it from the source.'),
    click.option("--upscale", type=float, default=1.0, show_default=True,
                 help="Scale factor before thresholding, e.g. 3 for low-res scans."),
    click.option("--upscale-style", type=click.Choice(["smooth", "pixel"]), default="smooth",
                 show_default=True, help="pixel = stair-stepped, crunchier edges."),
    click.option("--smooth", "smooth_sigma", type=float, default=0.6, show_default=True,
                 help="Edge smoothing before tracing. 0 = raw, jagged."),
    click.option("--corner", "corner_deg", type=float, default=125.0, show_default=True,
                 help="Angles sharper than this stay corners."),
    click.option("--level", type=click.IntRange(0, 254), default=0, show_default=True,
                 help="Manual threshold like Photoshop (higher = more ink). 0 = automatic."),
    click.option("--min-side", type=int, default=768, show_default=True),
    click.option("--max-side", type=int, default=4096, show_default=True,
                 help="Cap on working size after upscaling."),
    click.option("--speckle", type=int, default=6, show_default=True,
                 help="Drop blobs / fill holes smaller than N px."),
    click.option("--epsilon", type=float, default=0.75, show_default=True,
                 help="Path simplification tolerance (px). Higher = fewer nodes."),
    click.option("--smooth-sigma", type=float, default=0.6, show_default=True,
                 help="Edge smoothing before contouring. 0 = hard pixel edges."),
    click.option("--corner-deg", type=float, default=125.0, show_default=True,
                 help="Vertices sharper than this angle stay corners."),
    click.option("--min-score", type=float, default=0.90, show_default=True,
                 help="ssim_target below this is flagged low_score."),
    click.option("--denoise/--no-denoise", default=True, show_default=True),
    click.option("--normalize-background/--no-normalize-background", default=True,
                 show_default=True, help="Flatten uneven paper tone (line art)."),
]


def params_options(f):
    for opt in reversed(_PARAM_OPTIONS):
        f = opt(f)
    return f


def make_params(kw: dict) -> Params:
    return Params(**{k: v for k, v in kw.items() if k in Params.__dataclass_fields__})


@click.group()
@click.version_option(__version__)
def main():
    """boottool: clean raster graphics into tidy SVG and curate a vector dataset."""


# --------------------------------------------------------------------------- ui

@main.command()
@click.option("--port", type=int, default=8765, show_default=True)
@click.option("--no-browser", is_flag=True, help="Don't open a browser tab automatically.")
def ui(port, no_browser):
    """Open the interactive slider interface in your browser."""
    from .ui import serve
    serve(port=port, open_browser=not no_browser)


# --------------------------------------------------------------------------- trace

@main.command()
@click.argument("src", type=click.Path(exists=True, dir_okay=False))
@click.argument("dst", type=click.Path(dir_okay=False))
@params_options
def trace(src, dst, **kw):
    """Trace one image to SVG and print its scores."""
    p = make_params(kw)
    res = run_pipeline(src, p)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(res.svg, encoding="utf-8")
    click.echo(json.dumps(
        {"mode": res.mode, "size": res.size, "stats": res.stats, "scores": res.scores}, indent=2
    ))


# --------------------------------------------------------------------------- run

def _work(args: tuple) -> dict:
    src, rel, out_svg, pdict, prov, sha = args
    p = Params(**pdict)
    rec = {
        "id": sha[:16], "sha256": sha, "source": rel, "source_abs": str(Path(src).resolve()),
        "source_bytes": os.path.getsize(src), "params": pdict, "params_key": p.key(),
        "provenance": prov, "provenance_declared": bool(prov.get("license")),
        "boottool_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        res = run_pipeline(src, p)
        Path(out_svg).parent.mkdir(parents=True, exist_ok=True)
        Path(out_svg).write_text(res.svg, encoding="utf-8")
        rec.update(
            svg="svg/" + Path(rel).with_suffix(".svg").as_posix(),
            mode=res.mode, size=list(res.size), scale=res.scale,
            stats=res.stats, scores=res.scores,
            status="ok" if res.scores["ssim_target"] >= p.min_score else "low_score",
        )
    except Exception as e:  # noqa: BLE001 - one bad file must not stop a batch
        rec.update(status="error", error=f"{type(e).__name__}: {e}")
    return rec


@main.command()
@click.argument("input_dir", type=click.Path(exists=True, file_okay=False))
@click.argument("out_dir", type=click.Path(file_okay=False))
@click.option("--provenance", "prov_csv", type=click.Path(exists=True, dir_okay=False),
              help="CSV with columns: file, license, source, author, year.")
@click.option("--license", "default_license", default="",
              help="Fallback licence label for files not in the provenance CSV.")
@click.option("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1), show_default=True)
@click.option("--force", is_flag=True, help="Reprocess files already in the manifest.")
@params_options
def run(input_dir, out_dir, prov_csv, default_license, workers, force, **kw):
    """Process every image under INPUT_DIR; write SVGs and manifest.jsonl to OUT_DIR."""
    p = make_params(kw)
    in_root, out_root = Path(input_dir), Path(out_dir)
    svg_root = out_root / "svg"
    manifest = Manifest(out_root / "manifest.jsonl")
    prov_map = load_provenance(prov_csv)
    done = set() if force else manifest.done_keys()

    files = sorted(f for f in in_root.rglob("*") if f.suffix.lower() in IMG_EXTS and f.is_file())
    jobs, skipped = [], 0
    for f in files:
        rel = f.relative_to(in_root).as_posix()
        sha = sha256_file(f)
        out_svg = svg_root / Path(rel).with_suffix(".svg")
        if (sha, p.key()) in done and out_svg.exists():
            skipped += 1
            continue
        prov = dict(prov_map.get(rel, {}))
        if default_license and not prov.get("license"):
            prov["license"] = default_license
        jobs.append((str(f), rel, str(out_svg), p.to_dict(), prov, sha))

    click.echo(f"{len(files)} images found, {skipped} skipped (already done), {len(jobs)} to process")
    counts: Counter = Counter()

    def handle(i, rec):
        manifest.append(rec)
        counts[rec["status"]] += 1
        score = rec.get("scores", {}).get("ssim_target", "-")
        click.echo(f"[{i}/{len(jobs)}] {rec['source']}  {rec['status']}  ssim={score}"
                   + (f"  {rec['error']}" if rec["status"] == "error" else ""))

    if workers <= 1 or len(jobs) <= 1:
        for i, job in enumerate(jobs, 1):
            handle(i, _work(job))
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, rec in enumerate(ex.map(_work, jobs, chunksize=1), 1):
                handle(i, rec)

    click.echo("done: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) if counts
               else "nothing new to process")


# --------------------------------------------------------------------------- report

@main.command()
@click.argument("out_dir", type=click.Path(exists=True, file_okay=False))
def report(out_dir):
    """Write report.html: source vs SVG side by side, worst scores first."""
    out_root = Path(out_dir)
    recs = [r for r in latest_by_source(Manifest(out_root / "manifest.jsonl").load())]
    recs.sort(key=lambda r: r.get("scores", {}).get("ssim_target", -1))
    rows = []
    for r in recs:
        sc = r.get("scores", {})
        svg_rel = "svg/" + Path(r["source"]).with_suffix(".svg").as_posix()
        src_uri = Path(r["source_abs"]).as_uri()
        rows.append(
            "<tr>"
            f"<td><img src='{html.escape(src_uri)}' width=220></td>"
            f"<td>{'<img src=' + repr(html.escape(svg_rel)) + ' width=220>' if r['status'] != 'error' else ''}</td>"
            f"<td>{html.escape(r['source'])}<br>status: <b>{r['status']}</b><br>"
            f"ssim_target: {sc.get('ssim_target', '-')}<br>"
            f"paths: {r.get('stats', {}).get('path_count', '-')} "
            f"nodes: {r.get('stats', {}).get('node_count', '-')}<br>"
            f"licence: {html.escape(r.get('provenance', {}).get('license', '') or 'MISSING')}"
            f"{'<br>' + html.escape(r['error']) if r.get('error') else ''}</td></tr>"
        )
    page = ("<!doctype html><meta charset=utf-8><title>boottool report</title>"
            "<style>body{font:14px sans-serif}td{padding:6px;vertical-align:top;"
            "border-bottom:1px solid #ddd}img{background:#fff;border:1px solid #ccc}</style>"
            f"<h1>boottool report ({len(recs)} images)</h1><table>{''.join(rows)}</table>")
    (out_root / "report.html").write_text(page, encoding="utf-8")
    click.echo(f"wrote {out_root / 'report.html'}")


# --------------------------------------------------------------------------- dataset

@main.command()
@click.argument("out_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--min-score", type=float, default=0.90, show_default=True)
@click.option("--max-paths", type=int, default=400, show_default=True,
              help="Exclude SVGs too complex to be useful training sequences.")
@click.option("--val-frac", type=float, default=0.05, show_default=True)
@click.option("--require-provenance", is_flag=True,
              help="Exclude images with no declared licence.")
def dataset(out_dir, min_score, max_paths, val_frac, require_provenance):
    """Select clean, deduplicated SVGs and write a deterministic train/val split."""
    out_root = Path(out_dir)
    recs = sorted(latest_by_source(Manifest(out_root / "manifest.jsonl").load()),
                  key=lambda r: r["source"])
    train, val, excluded, seen = [], [], Counter(), set()
    for r in recs:
        if r["status"] == "error":
            excluded["error"] += 1
        elif r["scores"]["ssim_target"] < min_score:
            excluded["low_score"] += 1
        elif r["stats"]["path_count"] > max_paths:
            excluded["too_complex"] += 1
        elif require_provenance and not r["provenance_declared"]:
            excluded["no_provenance"] += 1
        elif r["sha256"] in seen:
            excluded["duplicate"] += 1
        else:
            seen.add(r["sha256"])
            svg = "svg/" + Path(r["source"]).with_suffix(".svg").as_posix()
            bucket = int(r["sha256"][:8], 16) / 0xFFFFFFFF
            (val if bucket < val_frac else train).append(svg)
    doc = {
        "criteria": {"min_score": min_score, "max_paths": max_paths, "val_frac": val_frac,
                     "require_provenance": require_provenance},
        "train": train, "val": val, "excluded": dict(excluded),
    }
    (out_root / "dataset.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    click.echo(f"train={len(train)} val={len(val)} excluded={dict(excluded)}")
