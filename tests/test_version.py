"""Releases number the app from their tag, and the app shows that number."""
import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import boottool

ROOT = Path(__file__).resolve().parents[1]
# GitHub runs the release step with Git Bash on Windows; elsewhere any bash will do. (On Windows a
# plain "bash" may be the WSL launcher, so use Git's own.)
def _git_bash():
    candidates = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"]
    git = shutil.which("git")
    if git:                                          # ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
        candidates.append(Path(git).resolve().parent.parent / "bin" / "bash.exe")
    return next((str(c) for c in candidates if c.exists()), None)


BASH = _git_bash() if os.name == "nt" else shutil.which("bash")
if os.name == "nt" and not BASH and os.environ.get("GITHUB_ACTIONS"):
    raise RuntimeError("Git Bash not found on the Windows test machine; the release step needs it")
needs_bash = pytest.mark.skipif(not BASH, reason="needs bash (Git Bash on Windows)")


def _set_version(tmp_path, tag, init=None):
    (tmp_path / "boottool").mkdir(exist_ok=True)
    (tmp_path / "boottool" / "__init__.py").write_bytes(init or (ROOT / "boottool" / "__init__.py").read_bytes())
    # run the script exactly as checked out, so Windows line endings in it would show up here
    return subprocess.run([BASH, str(ROOT / "installer" / "set_version.sh"), tag], cwd=tmp_path,
                          capture_output=True, text=True)


def _lines(data: bytes) -> list[str]:
    # Git Bash's sed on Windows writes Unix line endings; Python reads either, so compare the lines
    return data.decode("utf-8").splitlines()


@needs_bash
def test_windows_line_endings_in_init_still_work(tmp_path):
    original = (ROOT / "boottool" / "__init__.py").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    r = _set_version(tmp_path, "v3.1.4", original)
    assert r.returncode == 0, r.stdout + r.stderr
    out = _lines((tmp_path / "boottool" / "__init__.py").read_bytes())
    want = [l.replace(boottool.__version__, "3.1.4", 1) if l.startswith("__version__") else l for l in _lines(original)]
    assert out == want


@needs_bash
@pytest.mark.parametrize("tag, version", [("v0.9.1", "0.9.1"), ("v1.0", "1.0"), ("v2.10.3.1", "2.10.3.1")])
def test_release_tag_sets_the_version(tmp_path, tag, version):
    r = _set_version(tmp_path, tag)
    assert r.returncode == 0, r.stdout + r.stderr
    text = (tmp_path / "boottool" / "__init__.py").read_bytes().decode("utf-8")
    assert re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1) == version
    original = _lines((ROOT / "boottool" / "__init__.py").read_bytes())
    assert _lines(text.replace(version, boottool.__version__, 1).encode()) == original   # nothing else changed


@needs_bash
@pytest.mark.parametrize("tag", ["v1", "v0.9.1-beta", "V0.9.1", "0.9.1", "v0.09.1", "main",
                                 'v1.2"; touch hacked; "', "v1.2.3.4.5", "vv0.9.1", ""])
def test_tags_that_are_not_version_numbers_stop_the_build(tmp_path, tag):
    r = _set_version(tmp_path, tag)
    assert r.returncode != 0
    assert (tmp_path / "boottool" / "__init__.py").read_bytes() == (ROOT / "boottool" / "__init__.py").read_bytes()
    assert not (tmp_path / "hacked").exists()


def test_the_version_lives_in_one_place():
    tomllib = pytest.importorskip("tomllib")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in project["project"] and "version" in project["project"]["dynamic"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "boottool.__version__"}


def test_release_build_sets_the_version_before_building():
    steps = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    m = re.search(r"run: bash installer/set_version\.sh \"(\$GITHUB_REF_NAME|\$\{\{ github\.ref_name \}\})\"", steps)
    assert m, "release.yml must run set_version.sh with the tag"
    step = steps[steps.rindex("- name:", 0, m.start()):m.start()]
    assert re.search(r"if:\s*(\$\{\{\s*)?startsWith\(github\.ref, 'refs/tags/v'\)", step), "only for releases"
    assert m.start() < steps.index("run: pyinstaller")


def test_only_a_published_release_attaches_downloads():
    steps = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    job = steps[steps.index("\n  release:\n"):]
    cond = re.search(r"^    if: (.*)$", job, re.M).group(1).split(" #")[0].strip()
    assert cond == "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"


def test_release_notes_are_only_generated_for_a_new_release():
    steps = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s*generate_release_notes: \$\{\{ steps\.existing\.outputs\.found != 'true' \}\}", steps, re.M)
    assert 'gh api "repos/$GH_REPO/releases/tags/$GITHUB_REF_NAME"' in steps and "id: existing" in steps
    assert 'grep -q "HTTP 404"' in steps, "only a missing release (404) counts as new; other errors stop"


def _smoke():
    spec = importlib.util.spec_from_file_location("smoke_test", ROOT / "installer" / "smoke_test.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    return smoke


def test_smoke_test_only_compares_with_tags_that_number_the_build():
    src = (ROOT / "installer" / "smoke_test.py").read_text(encoding="utf-8")
    assert 'os.environ.get("GITHUB_REF_TYPE") == "tag" and os.environ.get("GITHUB_REF_NAME", "").startswith(("v", "V"))' in src


def test_smoke_test_expects_the_source_version():
    assert _smoke()._source_version() == boottool.__version__
