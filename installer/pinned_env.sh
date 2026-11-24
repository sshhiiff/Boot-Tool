#!/usr/bin/env bash
# Used by the GitHub workflows (tests and downloads): makes a clean Python environment with
# exactly the versions in installer/build-constraints.txt, checks nothing else got in, and
# makes the later steps use it.
#   bash installer/pinned_env.sh -e . pyinstaller
set -euo pipefail

c=installer/build-constraints.txt
env="$RUNNER_TEMP/pinned-env"
python -m venv "$env"
if [ "$RUNNER_OS" = Windows ]; then bin="$env/Scripts"; else bin="$env/bin"; fi
pip=("$bin/python" -m pip --disable-pip-version-check)

# The installer, and what builds Boot Tool itself, first...
"${pip[@]}" install --only-binary=:all: -c "$c" pip setuptools
# ...then everything else. Ready-made packages only, and Boot Tool is built with the
# setuptools above rather than in a separate, unchecked environment.
"${pip[@]}" install --only-binary=:all: --no-build-isolation --check-build-dependencies -c "$c" "$@"
"$bin/python" installer/check_pins.py

echo "$bin" >> "$GITHUB_PATH"      # later steps' python, pyinstaller and pytest come from here
