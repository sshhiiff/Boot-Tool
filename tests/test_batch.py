import io
import json
import time
import urllib.error
import urllib.request
import zipfile

import pytest

from test_plates import _post, server  # noqa: F401 (server is a fixture)


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def _upload(server, path):
    return json.loads(_post(server + "/api/upload", path.read_bytes(), "application/octet-stream")[1])["id"]


def _wait(server, job, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        code, body, _ = _get(f"{server}/api/batch/status?job={job}")
        st = json.loads(body)
        if st["finished"]:
            return st
        time.sleep(0.2)
    raise AssertionError("batch did not finish")


def _start(server, items, **kw):
    code, body, _ = _post(server + "/api/batch/start", {"items": items, **kw})
    return code, json.loads(body)


def test_batch_saves_into_one_folder_with_own_settings(server, lineart, color, tmp_path):
    a, b = _upload(server, lineart), _upload(server, color)
    out = tmp_path / "svg out"
    code, res = _start(server, [
        {"id": a, "name": "my scan.png", "params": {}},
        {"id": a, "name": "my scan.jpg", "params": {"speckle": 0, "epsilon": 0.25, "smooth_sigma": 0}},   # same stem
        {"id": b, "name": "../evil/logo.png", "params": {"mode": "color", "colors": 4}},
    ], out_dir=str(out))
    assert code == 200 and res["out_dir"] == str(out)
    st = _wait(server, res["job"])
    assert st["counts"]["done"] == 3 and st["counts"]["error"] == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == ["logo.svg", "my scan-2.svg", "my scan.svg"]            # one flat folder, safe names, no clash
    assert not (tmp_path / "evil").exists()
    sizes = {i["fname"]: i["node_count"] for i in st["items"]}
    assert sizes["my scan-2.svg"] != sizes["my scan.svg"]                   # each image used its own settings
    assert all((out / n).read_text().lstrip().startswith("<svg") or "<svg" in (out / n).read_text() for n in names)
    code, svg, h = _get(f"{server}/api/batch/svg?job={res['job']}&i=0")
    assert code == 200 and h["Content-Type"] == "image/svg+xml" and b"<svg" in svg
    code, z, h = _get(f"{server}/api/batch/zip?job={res['job']}")
    assert code == 200 and sorted(zipfile.ZipFile(io.BytesIO(z)).namelist()) == names


def test_batch_never_overwrites_existing_files(server, lineart, tmp_path):
    a = _upload(server, lineart)
    out = tmp_path / "o"
    out.mkdir()
    (out / "x.svg").write_text("KEEP ME")
    _, res = _start(server, [{"id": a, "name": "x.png", "params": {}}], out_dir=str(out))
    st = _wait(server, res["job"])
    assert (out / "x.svg").read_text() == "KEEP ME" and (out / "x-2.svg").exists()
    assert st["items"][0]["saved_as"] == "x-2.svg"


def test_batch_keeps_going_after_a_failure_and_can_stop(server, lineart, tmp_path):
    a = _upload(server, lineart)
    _, res = _start(server, [{"id": "nope", "name": "bad.png", "params": {}}, {"id": a, "name": "ok.png", "params": {}}],
                    out_dir=str(tmp_path / "o"), keep_going=True)
    st = _wait(server, res["job"])
    assert [i["state"] for i in st["items"]] == ["error", "done"] and "unknown image" in st["items"][0]["error"]
    _, res = _start(server, [{"id": "nope", "name": "bad.png", "params": {}}, {"id": a, "name": "ok.png", "params": {}}],
                    keep_going=False)
    st = _wait(server, res["job"])
    assert [i["state"] for i in st["items"]] == ["error", "stopped"]
    assert _post(server + "/api/batch/stop", {"job": res["job"]})[0] == 200


def test_batch_rejects_bad_requests(server, lineart, tmp_path):
    a = _upload(server, lineart)
    assert _start(server, [])[0] == 400
    code, res = _start(server, [{"id": a, "name": "a.png"}], out_dir="relative/folder")
    assert code == 400 and "full folder path" in res["error"]
    assert _get(f"{server}/api/batch/status?job=nope")[0] == 404
    blocker = tmp_path / "file"
    blocker.write_text("x")
    code, res = _start(server, [{"id": a, "name": "a.png"}], out_dir=str(blocker / "sub"))
    assert code == 400 and "folder" in res["error"].lower()


def test_only_this_computers_own_page_may_use_the_api(server, lineart):
    port = server.rsplit(":", 1)[1]
    assert _get(server + "/api/defaults")[0] == 200
    assert _get(server + "/api/defaults", {"Host": "evil.example"})[0] == 403
    assert _get(server + "/api/defaults", {"Host": f"localhost:{port}"})[0] == 200
    req = urllib.request.Request(server + "/api/batch/start", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403


@pytest.mark.parametrize("terminal", ["there", "gone"])
def test_closing_boot_tool_during_a_batch_lets_the_picture_in_hand_finish(lineart, tmp_path, monkeypatch, terminal):
    """Closing Boot Tool deletes its session folder. A batch that was still running went on writing
    into that folder while it was deleted (which could leave the folder behind), and could be cut
    off halfway through an SVG in its output folder. The batch is stopped first now and the picture
    it has in hand finished: that one is saved whole, the rest are not started, and the folder goes.
    A batch in another Boot Tool is left alone. And it is the same when Boot Tool is closed by
    closing its terminal window, so that there is nowhere left to print to."""
    import threading

    from boottool import batch as bt
    from boottool.ui import make_server

    real_pipeline, real_run, jobs = bt.run_pipeline, bt.Job.run, []

    def slow(path, params, score=False):                                    # every picture takes two seconds
        time.sleep(2.0)
        return real_pipeline(path, params, score=score)
    monkeypatch.setattr(bt, "run_pipeline", slow)
    monkeypatch.setattr(bt.Job, "run", lambda self, *a: (jobs.append(self), real_run(self, *a))[1])
    if terminal == "gone":
        def no_terminal(*a, **kw):
            raise OSError(5, "Input/output error")
        monkeypatch.setattr(bt, "print", no_terminal, raising=False)

    servers = [make_server("127.0.0.1", 0) for _ in range(2)]
    for srv in servers:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    one, other = (f"http://127.0.0.1:{srv.server_address[1]}" for srv in servers)
    try:
        code, there = _start(other, [{"id": _upload(other, lineart), "name": f"o{i}.png", "params": {}} for i in range(2)])
        assert code == 200
        out = tmp_path / "out"
        image = _upload(one, lineart)
        code, here = _start(one, [{"id": image, "name": f"p{i}.png", "params": {}} for i in range(4)], out_dir=str(out))
        assert code == 200
        for _ in range(600):                                                # until both batches are at work on their first picture
            if len(jobs) == 2 and all(j.items[0]["state"] == "working" for j in jobs):   # (it takes two seconds; closing, below,
                break                                                       # takes half a second at most to begin)
            time.sleep(0.05)
        assert len(jobs) == 2
        job = next(j for j in jobs if j.id == here["job"])
        folder = job.dir.parent
        assert folder.is_dir()

        servers[0].shutdown()
        servers[0].server_close()                                           # Boot Tool is closed

        assert not job.thread.is_alive() and job.finished
        assert [it["state"] for it in job.items] == ["done", "stopped", "stopped", "stopped"]
        assert [p.name for p in out.iterdir()] == ["p0.svg"]
        assert (out / "p0.svg").read_text(encoding="utf-8").rstrip().endswith("</svg>")
        assert not folder.exists()
        st = _wait(other, there["job"])                                     # the other Boot Tool's batch runs on to its end
        assert st["counts"]["done"] == 2
    finally:
        for srv in servers:                                                 # (closing the first again does no harm)
            srv.shutdown()
            srv.server_close()


def test_a_picture_that_takes_too_long_is_not_waited_for_for_ever(lineart, tmp_path, monkeypatch, capsys):
    """The wait for the picture in hand has an end. When the time is up, closing goes ahead, and
    says that this picture was not saved."""
    import threading

    from boottool import batch as bt
    from boottool.ui import make_server

    real_pipeline, real_run, jobs, at_work = bt.run_pipeline, bt.Job.run, [], threading.Event()

    def slow(path, params, score=False):
        at_work.set()
        time.sleep(1.5)
        return real_pipeline(path, params, score=score)
    monkeypatch.setattr(bt, "run_pipeline", slow)
    monkeypatch.setattr(bt.Job, "run", lambda self, *a: (jobs.append(self), real_run(self, *a))[1])
    srv = make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        code, res = _start(url, [{"id": _upload(url, lineart), "name": f"p{i}.png", "params": {}} for i in range(2)])
        assert code == 200 and at_work.wait(30)
        job = jobs[0]
        assert bt.stop_all(job.dir.parent, wait=0.2) is False               # not done in a fifth of a second
        said = capsys.readouterr().out
        assert "Finishing the picture" in said and "not finished in time: that picture was not saved" in said
        assert job.stop.is_set()
        job.thread.join(30)                                                 # it ends by itself after its picture, starting no other
        assert [it["state"] for it in job.items] == ["done", "stopped"]
        assert bt.stop_all(job.dir.parent) is True                          # and then there is nothing to wait for
        assert capsys.readouterr().out == ""
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGHUP"), reason="Mac and Linux only")
@pytest.mark.parametrize("terminal", ["there", "gone"])
def test_stopping_the_real_tool_during_a_batch(lineart, tmp_path, terminal):
    """The same with Boot Tool run for real and asked to stop (as closing its window does) while a
    batch of thirty is under way: it says what it is waiting for, ends cleanly, leaves only whole
    SVGs in the output folder and no session folder. With nothing left to print to ("gone": what it
    prints to is closed first), it cannot say so, and still tidies up. (A check of the whole way
    through; the tests above are the ones that pin the behaviour down.)"""
    import os
    import signal
    import subprocess
    import sys
    from pathlib import Path
    tmp, out = tmp_path / "tmp", tmp_path / "out"
    tmp.mkdir()
    env = {**os.environ, "TMPDIR": str(tmp), "TMP": str(tmp), "TEMP": str(tmp), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    proc = subprocess.Popen([sys.executable, "-u", "-m", "boottool", "ui", "--no-browser", "--port", "0"],
                            stdout=subprocess.PIPE, text=True, env=env)
    try:
        url = proc.stdout.readline().split("at ", 1)[1].strip().rstrip("/")
        image = _upload(url, lineart)
        (folder,) = Path(tmp).glob("boottool-ui-*")
        code, res = _start(url, [{"id": image, "name": f"p{i}.png", "params": {}} for i in range(30)], out_dir=str(out))
        assert code == 200
        for _ in range(600):                                                # until the first picture is done
            st = json.loads(_get(f"{url}/api/batch/status?job={res['job']}")[1])
            if st["counts"]["done"] >= 1:
                break
            time.sleep(0.05)
        assert 1 <= st["counts"]["done"] < 30 and not st["finished"]
        if terminal == "gone":
            proc.stdout.close()
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=90)                                           # (its exit code is not 0 then: Python's own last flush fails)
        else:
            proc.send_signal(signal.SIGTERM)
            said = proc.communicate(timeout=90)[0]
            assert proc.returncode == 0 and "Finishing the picture" in said
    finally:
        if proc.poll() is None:
            proc.kill()
    saved = sorted(out.iterdir())
    assert 1 <= len(saved) < 30 and all(p.read_text(encoding="utf-8").rstrip().endswith("</svg>") for p in saved)
    assert not folder.exists()
