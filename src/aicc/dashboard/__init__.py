"""Static dashboard generation."""

from .build import build_fragment, build_site, verify_site  # noqa: F401

__all__ = ["build_site", "build_fragment", "verify_site"]
