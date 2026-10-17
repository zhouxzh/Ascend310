"""Deprecated compatibility entry point for the Ascend Edge Chat browser UI."""

from ascend_edge_chat.ui.web import *  # noqa: F401,F403
from ascend_edge_chat.ui.web import main


if __name__ == "__main__":
    import sys

    print("Deprecated entry point: use 'ascend-edge-chat ui'.", file=sys.stderr)
    main()
