"""Access to the forked extractor (`src/extractor`) and its metadata."""

from __future__ import annotations

from pathlib import Path

import extractor as engine_package
from extractor import extractor as pipeline_module
from extractor.upstream import LOCAL_PATCHES, UPSTREAM_BRANCH, UPSTREAM_COMMIT, UPSTREAM_REPO

DATA_DIR = Path(engine_package.__file__).parent / "data"

DNSKEY_SOURCES = ("dnsdata", "zone")
HTML_SELECTORS = ("upstream", "fixed")

__all__ = [
    "DATA_DIR",
    "DNSKEY_SOURCES",
    "HTML_SELECTORS",
    "LOCAL_PATCHES",
    "UPSTREAM_BRANCH",
    "UPSTREAM_COMMIT",
    "UPSTREAM_REPO",
    "pipeline_module",
]


def load_extractor():
    """The extractor module (``init_transformations`` / ``extract_features``)."""
    if not DATA_DIR.is_dir():
        raise RuntimeError(f"extractor data directory missing: {DATA_DIR}")
    return pipeline_module
