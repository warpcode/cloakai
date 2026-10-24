"""Entrypoint for `python -m jules_mcp`."""

import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())
