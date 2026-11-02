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
