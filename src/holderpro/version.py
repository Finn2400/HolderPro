"""HolderPro version helpers.

The fallback is used from an unpacked source tree.  Installed wheels use their
distribution metadata, so the CLI and diagnostics always report the version of
the artifact that is actually running.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

FALLBACK_VERSION = "0.1.0a1"


def get_version() -> str:
    """Return the installed HolderPro distribution version."""

    # An editable checkout can retain metadata from before pyproject.toml was
    # updated.  The source constant is authoritative while that checkout is
    # running; immutable installed wheels use their own metadata.
    if (Path(__file__).resolve().parents[2] / "pyproject.toml").is_file():
        return FALLBACK_VERSION
    try:
        return version("holderpro")
    except PackageNotFoundError:
        return FALLBACK_VERSION


__version__ = get_version()


__all__ = ["FALLBACK_VERSION", "__version__", "get_version"]
