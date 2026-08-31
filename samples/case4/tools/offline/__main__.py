"""Run the offline benchmark CLI as ``python -m tools.offline``.

The benchmark imports optional image/CPU dependencies.  Keep ``--help`` and
``-h`` usable on a source-only controller where those dependencies are not
installed; import the heavy module only when a real command is requested.
"""

from __future__ import annotations

import sys


def _help() -> int:
    print(
        "Offline benchmark commands require the export/offline dependency set.\n"
        "Use: python -m tools.offline compare --help (or performance/evaluate/audit)\n"
        "For candidate inventory validation use: "
        "python -m tools.offline.candidates validate --strict"
    )
    return 0


def main() -> int:
    if len(sys.argv) == 1 or sys.argv[1] in {"-h", "--help"}:
        return _help()
    from .benchmark import main as benchmark_main

    return int(benchmark_main())


if __name__ == "__main__":
    raise SystemExit(main())
