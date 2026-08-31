"""ACL lifecycle boundary for the production package.

The implementation is kept in :mod:`runtime.adapters` because the runner and
adapter share one process-owned ACL environment.  This module exposes only
the lifecycle operations used by the API and shutdown hooks, so callers do not
need to import adapter implementation details.
"""

from .adapters import acl_runtime_status, shutdown_acl_runtime

__all__ = ["acl_runtime_status", "shutdown_acl_runtime"]
