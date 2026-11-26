import json
import threading
import urllib.error
import urllib.request

import pytest

from boottool.ui import coerce_params, make_server


@pytest.fixture
def server():
    srv = make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _post(url, data, ctype="application/octet-stream"):
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_coerce_clamps_and_ignores_junk():
    p = coerce_params({"upscale": 99, "level": -5, "mode": "evil", "ink": "red;x",
                       "speckle": "7", "bogus": 1, "denoise": 0})
    assert p.upscale == 8 and p.level == 0 and p.mode == "auto"
    assert p.ink == "#000000" and p.speckle == 7 and p.denoise is False


def test_page_defaults_upload_trace(server, lineart):
    assert b"boottool" in urllib.request.urlopen(server + "/").read()
    assert json.loads(urllib.request.urlopen(server + "/api/defaults").read())["convert"]["speckle"] == 6

    code, body = _post(server + "/api/upload", lineart.read_bytes())
    assert code == 200
    image_id = json.loads(body)["id"]

    def trace(params, seq):
        return _post(server + "/api/trace",
                     json.dumps({"id": image_id, "session": "t", "seq": seq, "params": params}).encode(),
                     "application/json")

    code, body = trace({}, 1)
    assert code == 200
    clean = json.loads(body)
    assert clean["svg"].startswith("<svg") and clean["scores"]["ssim_target"] > 0.9

    # slider that only affects tracing reuses the cached target and changes the output
    code, body = trace({"speckle": 0, "smooth_sigma": 0, "epsilon": 0.2}, 2)
    assert json.loads(body)["stats"]["node_count"] > clean["stats"]["node_count"]

    # a stale request (older seq) is dropped rather than computed
    assert trace({}, 1)[0] == 204


def test_bad_inputs(server):
    assert _post(server + "/api/upload", b"not an image")[0] == 400
    code, _ = _post(server + "/api/trace", json.dumps({"id": "nope"}).encode(), "application/json")
    assert code == 400


def test_trace_works_without_an_svg_rasteriser(server, lineart, monkeypatch):
    """Downloaded apps have no rsvg/cairo: the self-check is skipped, not fatal."""
    import boottool.score as sc
    monkeypatch.setattr(sc.shutil, "which", lambda name: None)
    monkeypatch.setitem(__import__("sys").modules, "cairosvg", None)
    code, body = _post(server + "/api/upload", lineart.read_bytes())
    image_id = json.loads(body)["id"]
    code, body = _post(server + "/api/trace", json.dumps({"id": image_id, "session": "n", "seq": 1, "params": {}}).encode(), "application/json")
    assert code == 200 and json.loads(body)["scores"]["ssim_target"] is None


def test_temporary_folder_is_removed_when_the_tool_stops(lineart):
    import tempfile
    from pathlib import Path
    before = set(Path(tempfile.gettempdir()).glob("boottool-ui-*"))
    srv = make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    assert _post(url + "/api/upload", lineart.read_bytes())[0] == 200
    (folder,) = set(Path(tempfile.gettempdir()).glob("boottool-ui-*")) - before
    assert any(folder.iterdir())                                        # the uploaded picture is kept while it runs
    srv.shutdown()
    srv.server_close()
    assert not folder.exists()


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGHUP"), reason="Mac and Linux only")
@pytest.mark.parametrize("how", ["SIGTERM", "SIGHUP"])
def test_closing_the_terminal_also_removes_the_temporary_folder(lineart, tmp_path, how):
    """SIGHUP is what closing the Terminal window sends; SIGTERM is an ordinary stop request."""
    import os
    import signal
    import subprocess
    import sys
    from pathlib import Path
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = {**os.environ, "TMPDIR": str(tmp), "TMP": str(tmp), "TEMP": str(tmp)}
    proc = subprocess.Popen([sys.executable, "-u", "-m", "boottool", "ui", "--no-browser", "--port", "0"],
                            stdout=subprocess.PIPE, text=True, env=env)
    try:
        url = proc.stdout.readline().split("at ", 1)[1].strip().rstrip("/")
        assert _post(url + "/api/upload", lineart.read_bytes())[0] == 200
        (folder,) = Path(tmp).glob("boottool-ui-*")
        proc.send_signal(getattr(signal, how))
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert not folder.exists()


_CLOSED_WINDOW = """
import json, sys, threading, time
from boottool import ui
slow = sys.argv[1] == "slow"
srv = ui.make_server("127.0.0.1", 0)
(folder,) = ui._SESSION_DIRS
(folder / "picture.png").write_bytes(b"an opened picture")
assert ui._console_closing(0) is False and folder.exists()      # Ctrl+C is not its business
if slow:
    ui._CLOSE_WAIT = 0.5
said = []
def window_closed():
    time.sleep(0.5)                                             # the tool is up and waiting for requests by now
    t0 = time.monotonic()
    said.append([ui._console_closing(2), folder.exists(), time.monotonic() - t0])
threading.Thread(target=window_closed).start()
try:
    srv.serve_forever()
    how = "went on"
except KeyboardInterrupt:
    how = "stopped as after Ctrl+C"
    if slow:
        time.sleep(2)                                           # a stop that takes longer than Windows allows
finally:
    left = folder.exists()
    srv.server_close()
while not said:
    time.sleep(0.01)
print(json.dumps({"how": how, "handled": said[0][0], "folder then": said[0][1], "after": said[0][2], "left for the tool": left}))
"""


@pytest.mark.parametrize("stop", ["in time", "slow"])
def test_a_closed_console_window_stops_the_tool_and_removes_the_temporary_folder(tmp_path, stop):
    """Windows tells a program in its own way that its console window was closed, and ends it five
    seconds later. Boot Tool then stops as after Ctrl+C and deletes its temporary folder; if stopping
    takes too long, the folder is deleted all the same before the time is up. (What Windows calls is
    run here directly, so this is tried on every system.)"""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    env = {**os.environ, "TMPDIR": str(tmp_path), "TMP": str(tmp_path), "TEMP": str(tmp_path),
           "PYTHONPATH": str(Path(__file__).resolve().parent.parent) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    r = subprocess.run([sys.executable, "-c", _CLOSED_WINDOW, stop], capture_output=True, text=True, env=env, timeout=180)   # starting Python with all of Boot Tool can take half a minute on a busy machine
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["how"] == "stopped as after Ctrl+C" and out["handled"] is True and out["folder then"] is False
    if stop == "slow":
        assert out["left for the tool"] is False and 0.4 < out["after"] < 1.9   # deleted while the tool was still stopping
    else:
        assert out["left for the tool"] is True and out["after"] < 3.5      # the tool tidied up itself, well inside Windows' five seconds
    assert not list(tmp_path.glob("boottool-ui-*"))


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows only")
def test_ctrl_break_on_windows_also_removes_the_temporary_folder(lineart, tmp_path):
    """Ctrl+Break reaches a program the same way a closed console window does, and (unlike a closed
    window) a test can send it. It used to end Boot Tool on the spot, leaving the folder behind."""
    import os
    import signal
    import subprocess
    import sys
    from pathlib import Path
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = {**os.environ, "TMPDIR": str(tmp), "TMP": str(tmp), "TEMP": str(tmp)}
    proc = subprocess.Popen([sys.executable, "-u", "-m", "boottool", "ui", "--no-browser", "--port", "0"],
                            stdout=subprocess.PIPE, text=True, env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    guard = threading.Timer(60, lambda: proc.poll() is None and proc.kill())   # never left waiting for a Boot Tool that hangs
    guard.daemon = True
    guard.start()
    try:
        url = proc.stdout.readline().split("at ", 1)[1].strip().rstrip("/")
        assert _post(url + "/api/upload", lineart.read_bytes())[0] == 200
        (folder,) = Path(tmp).glob("boottool-ui-*")
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        assert proc.wait(timeout=20) == 0
    finally:
        guard.cancel()
        if proc.poll() is None:
            proc.kill()
    assert not folder.exists()

