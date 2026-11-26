"""The downloaded app's launcher and the smoke test that tries a built app out (Mac and Linux: they
are run here through small shell scripts)."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(os.name == "nt", reason="uses shell scripts")


def _script(path, text):
    path.write_text("#!/bin/sh\n" + text + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _env(tmp_path):
    """A "browser" that only notes that it was asked to open something, and Boot Tool from this checkout."""
    browser = _script(tmp_path / "browser.sh", f'echo "$@" >> "{tmp_path / "opened.txt"}"')
    return {**os.environ, "BROWSER": str(browser), "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}


@pytest.mark.parametrize("flag", [None, "--no-browser"])
def test_the_launcher_opens_the_browser_unless_told_not_to(tmp_path, flag):
    """The app opens its page in the browser when it starts. Started with --no-browser it does not
    (there was no way to stop it: the smoke test set a made-up BROWSER name, and Python then went on
    to the desktop's own way of opening a page)."""
    proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "installer" / "launcher.py")] + ([flag] if flag else []),
                            stdout=subprocess.PIPE, text=True, env=_env(tmp_path))
    try:
        said = ""
        while "Close this window" not in said:
            line = proc.stdout.readline()
            assert line, "the launcher ended early:\n" + said
            said += line
        opened = tmp_path / "opened.txt"
        for _ in range(60):                                                 # it opens the page 0.8 s after starting
            if opened.exists():
                break
            time.sleep(0.1 if flag is None else 0.05)
        if flag is None:
            assert "It should open in your browser: http://127.0.0.1:" in said
            assert opened.read_text().strip().startswith("http://127.0.0.1:")
        else:
            assert "Open it in your browser: http://127.0.0.1:" in said and not opened.exists()
    finally:
        proc.terminate()
        proc.wait(timeout=30)


def test_the_smoke_test_tries_the_app_without_opening_a_browser(tmp_path):
    """The smoke test starts the app, uses it once and stops it. It must not open a browser on the
    machine it runs on, and it turns text into outlines with a font of its own, so that this is
    tried on a machine without fonts too (it used to be passed over there)."""
    app = _script(tmp_path / "BootTool", f'exec "{sys.executable}" -u "{ROOT / "installer" / "launcher.py"}" "$@"')
    proc = subprocess.Popen([sys.executable, str(ROOT / "installer" / "smoke_test.py"), str(app)], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=_env(tmp_path), start_new_session=True)
    try:
        said = proc.communicate(timeout=300)[0]
    finally:
        if proc.poll() is None:                                             # it hung: stop it and the app it started
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    assert proc.returncode == 0 and "SMOKE TEST PASSED" in said, said
    assert "fonts: the test font is found; text outlines work" in said
    assert not (tmp_path / "opened.txt").exists()
