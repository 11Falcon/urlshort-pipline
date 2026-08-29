"""urlshort — the service this course builds, ships, breaks and rolls back."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:  # installed as a wheel (which is how it runs in the container)
    __version__ = _pkg_version("urlshort")
except PackageNotFoundError:  # running from a source checkout
    __version__ = "0.0.0+source"

__all__ = ["__version__"]
