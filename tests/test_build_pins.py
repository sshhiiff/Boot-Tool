"""The tests and downloads on GitHub use the exact versions in installer/build-constraints.txt."""
import importlib.util
import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_pins", ROOT / "installer" / "check_pins.py")
check_pins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_pins)

PINS = ROOT / "installer" / "build-constraints.txt"
WORKFLOWS = ROOT / ".github" / "workflows"


def _code_lines(path):
    """Lines that do something: comments and blank lines left out."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip()
        if line:
            out.append(line)
    return out


def test_every_requirement_and_the_app_builder_have_an_exact_version():
    tomllib = pytest.importorskip("tomllib")
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"] + data["project"]["optional-dependencies"]["dev"]
    deps += data["build-system"]["requires"]
    names = {check_pins.norm(re.split(r"[\s;<>=!~\[]", d, maxsplit=1)[0]) for d in deps}
    missing = sorted((names | {"pyinstaller", "pip", "playwright"}) - set(check_pins.read_pins(PINS)))
    assert not missing, f"add these to installer/build-constraints.txt: {missing}"


def _run_commands(path):
    """The shell commands of a workflow: single-line `run:` values and the lines of `run: |` blocks."""
    out, block = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        indent = len(line) - len(line.lstrip())
        if block is not None:
            if line.strip() and indent <= block:
                block = None
            elif line.strip():
                out.append(line.strip())
                continue
        m = re.match(r"\s*(?:- )?run:\s*(.*)$", line)
        if m:
            if m.group(1) in ("|", ">"):
                block = indent
            elif m.group(1):
                out.append(m.group(1).strip())
    return out


def test_workflows_install_only_through_the_pinned_environment():
    installer = re.compile(r"\b(pip3?|pipx|uv|conda|easy_install)\b|\bpython3? -m pip\b")
    for wf in ("release.yml", "tests.yml"):
        cmds = _run_commands(WORKFLOWS / wf)
        setup = [i for i, c in enumerate(cmds) if c.startswith("bash installer/pinned_env.sh ")]
        assert len(setup) == 1, wf
        others = [c for c in cmds if not c.startswith("bash installer/pinned_env.sh ")]
        assert not [c for c in others if installer.search(c)], f"{wf} installs outside pinned_env.sh"
        python_users = [i for i, c in enumerate(cmds) if i != setup[0] and re.search(r"\b(python|pyinstaller|pytest)\b", c)]
        assert all(i > setup[0] for i in python_users), f"{wf} uses Python before the pinned environment exists"


def test_pinned_environment_script():
    lines = _code_lines(ROOT / "installer" / "pinned_env.sh")
    assert "set -euo pipefail" in lines
    installs = [shlex.split(l.replace('"${pip[@]}"', "pip")) for l in lines if l.startswith('"${pip[@]}" install')]
    assert len(installs) == 2
    for args in installs:
        assert "--only-binary=:all:" in args
        assert args[args.index("-c") + 1] == "$c"
    assert "--no-build-isolation" in installs[1] and "--check-build-dependencies" in installs[1]
    assert "c=installer/build-constraints.txt" in lines
    check = lines.index('"$bin/python" installer/check_pins.py')
    path = [i for i, l in enumerate(lines) if "GITHUB_PATH" in l]
    assert path and path[0] > check, "check the versions before later steps use the environment"


def test_check_pins_reports_unlisted_different_and_repeated_versions():
    pins = {"numpy": "2.5.5", "pip": "26.2.1"}
    ok = [("numpy", "2.5.5.0"), ("pip", "26.2.1"), ("boottool", "0.9.0")]
    assert check_pins.problems(ok, pins) == []
    found = check_pins.problems([("NumPy", "2.6.0"), ("new_thing", "1.0"), ("pip", "26.2.1"), ("pip", "26.3")], pins)
    assert found == ["new-thing 1.0 has no fixed version in installer/build-constraints.txt",
                     "numpy is 2.6.0, but build-constraints.txt says 2.5.5",
                     "pip is installed more than once (26.2.1, 26.3)"]
    same_twice = check_pins.problems([("numpy", "2.5.5"), ("numpy", "2.5.5"), ("pip", "26.2.1")], pins)
    assert same_twice == ["numpy is installed more than once (2.5.5, 2.5.5)"]


def test_check_pins_rejects_loose_or_repeated_lines(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("Click==8.5.1  # comment\nscikit_image==0.26.0\n", encoding="utf-8")
    assert check_pins.read_pins(f) == {"click": "8.5.1", "scikit-image": "0.26.0"}
    for bad in ("numpy>=2\n", "numpy==2.5.5\nNumPy==2.5.5\n"):
        f.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError):
            check_pins.read_pins(f)
