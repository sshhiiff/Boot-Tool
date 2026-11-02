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
