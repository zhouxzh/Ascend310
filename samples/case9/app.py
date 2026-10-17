"""Deprecated compatibility entry point for the Ascend Edge Chat gateway."""

from ascend_edge_chat.api.gateway import *  # noqa: F401,F403
from ascend_edge_chat.api.gateway import main


if __name__ == "__main__":
    import sys

    print("Deprecated entry point: use 'ascend-edge-chat gateway'.", file=sys.stderr)
    main()
