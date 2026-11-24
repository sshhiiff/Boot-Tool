"""boottool: raster graphics -> clean SVG, with scoring and provenance tracking."""

__version__ = "0.9.0"   # releases set this from their tag (installer/set_version.sh)

from .config import Params  # noqa: E402,F401
from .pipeline import run_pipeline, Result  # noqa: E402,F401
