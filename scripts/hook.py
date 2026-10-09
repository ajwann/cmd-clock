#!/usr/bin/env python3
"""cmd-clock hook dispatcher. Always exits 0."""
import os
import sys

try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
    import hookio
    from cmd_clock.core import handle
except BaseException:  # a broken install must never block Claude
    sys.exit(0)

sys.exit(hookio.run("cmd-clock", handle))
