"""Source declarations that need to import both models and scrapers.

This package must stay import-light: `registry` pulls in every scraper, so
nothing under `models.py` or `corpus/` may import this package, and this file
must not re-export `registry`.
"""
