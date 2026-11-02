"""boottool: raster graphics -> clean SVG, with scoring and provenance tracking."""

__version__ = "0.8.4"

from .config import Params  # noqa: E402,F401
from .pipeline import run_pipeline, Result  # noqa: E402,F401
