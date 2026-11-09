"""Helpers taken from the upstream `common` package (MIT, Ondřej Ondryáš).

Upstream's extractor imports exactly one function from `common` — `get_safe`.
Copying the package instead would pull `read_config`, the Kafka client helpers
and their dependencies into a tool that never talks to Kafka.
"""

from __future__ import annotations

from typing import Any


def get_safe(data: dict, path: str) -> Any | None:
    """Gets a value from a nested dictionary, returning None if the path doesn't exist."""
    if data is None:
        return None

    try:
        for key in path.split("."):
            if data is None:
                return None
            data = data[key]
        return data
    except (KeyError, TypeError):
        return None
