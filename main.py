"""Launch Gazer. See `python main.py --help` (e.g. `--demo` for no-webcam mode)."""

import sys

from gazer.app import main

if __name__ == "__main__":
    sys.exit(main())
