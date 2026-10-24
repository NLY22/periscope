"""Compatibility alias; implementation: :mod:`periscope.collection.strategies.broad`."""
import sys

if __name__ == "__main__":
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from runpy import run_module
    run_module('periscope.collection.strategies.broad', run_name="__main__")
else:
    from importlib import import_module
    sys.modules[__name__] = import_module('periscope.collection.strategies.broad')
