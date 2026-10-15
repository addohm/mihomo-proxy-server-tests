#!/usr/bin/env python3
"""Test every server in your running mihomo. Run with --help for details; the code is in mihomotest/."""
import sys

if sys.version_info < (3, 8):
    sys.exit("mihomo_speedtest needs Python 3.8 or newer.")

from mihomotest.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
