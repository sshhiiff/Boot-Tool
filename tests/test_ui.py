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


def test_the_page_opens_whatever_follows_its_address(server):
    """A bookmark or link that adds "?something" to the address used to get a bare "not found"
    instead of the page."""
    page = urllib.request.urlopen(server + "/").read()
    assert b"<title>" in page
    for address in ("/?x=1", "/?", "/index.html?v=2"):
        assert urllib.request.urlopen(server + address).read() == page, address
    assert json.loads(urllib.request.urlopen(server + "/api/defaults?x=1").read())["convert"]
    with pytest.raises(urllib.error.HTTPError) as e:                        # but not just anything is the page
        urllib.request.urlopen(server + "/index.htmlx?x=1")
    assert e.value.code == 404


def test_a_request_with_a_garbled_length_gets_an_answer(server, capfd):
    """It used to get none: the connection was dropped and an error was printed in Boot Tool's window."""
    import socket
    host, port = server.rsplit("/", 1)[1].split(":")
    with socket.create_connection((host, int(port)), timeout=10) as s:
        s.sendall(f"POST /api/upload HTTP/1.1\r\nHost: {host}:{port}\r\nContent-Length: abc\r\n\r\n".encode())
        answer = b""
        while chunk := s.recv(2000):                                        # until Boot Tool has said all it has to say
            answer += chunk
    assert answer.startswith(b"HTTP/1.0 400") and b"empty or too large" in answer
    assert "Traceback" not in capfd.readouterr().err


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

