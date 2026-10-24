"""Bounded atomic replacement for file-system state and collection checkpoints."""
from __future__ import annotations

import os
import time


def replace_file(source, target) -> None:
    """Keep atomicity while tolerating brief Windows reader/scanner sharing locks."""
    delays = (0, .01, .03, .06, .1, .2)
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == len(delays) - 1:
                raise
