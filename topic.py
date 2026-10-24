"""Compatibility entry point for topic search."""
import sys

if __name__ == "__main__":
    from periscope.cli.topic import main
    raise SystemExit(main())
else:
    from importlib import import_module
    sys.modules[__name__] = import_module("periscope.cli.topic")
