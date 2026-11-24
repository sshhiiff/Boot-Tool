#!/usr/bin/env bash
# Used by "Build downloads" when a release is made: writes the release's version number
# (tag v0.9.1 -> 0.9.1) into boottool/__init__.py, so the app and `boottool --version`
# always show the version they were released as.
#   bash installer/set_version.sh v0.9.1
set -euo pipefail

tag="$1"
if ! [[ "$tag" =~ ^v(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){1,3}$ ]]; then
  echo "The release tag '$tag' is not a version number like v0.9.1, so the downloads can't be numbered."
  exit 1
fi
version="${tag#v}"

file=boottool/__init__.py
grep -q '^__version__ = "' "$file" || { echo "No __version__ line found in $file"; exit 1; }
sed "s/^__version__ = \".*\"/__version__ = \"$version\"/" "$file" > "$file.new"
mv "$file.new" "$file"
grep '^__version__ = ' "$file"
