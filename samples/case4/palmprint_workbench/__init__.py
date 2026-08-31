"""Public package boundary for the NPU-only palmprint workbench.

Production callers use :mod:`palmprint_workbench.api`; export, benchmark, and
board diagnostics live under the separate :mod:`tools` package.
"""

from __future__ import annotations

__all__ = ["__version__"]

# Keep the release identifier in one importable location.  A downstream
# release process may replace this value from its tagged build metadata.
__version__ = "1.0.0"
