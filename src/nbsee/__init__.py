"""nbsee: a terminal reader for Jupyter notebooks."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("nbsee")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
