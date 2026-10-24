"""Compatibility entry point; implementation: :mod:`periscope.operations.persist`."""
import sys

if __name__ == "__main__":
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runpy import run_module
    run_module("periscope.operations.persist", run_name="__main__")
else:
    from importlib import import_module
    sys.modules[__name__] = import_module("periscope.operations.persist")
