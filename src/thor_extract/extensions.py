"""Extension seam for additional features.

The production extractor already has the shape we want: a
``Transformation`` subclass declares ``features`` (name → dtype) and
``transform(df) -> df``, and ``init_transformations`` runs the registered
classes in a fixed order. New features are therefore added as transformations,
not by editing the fork in `src/extractor` — this module inserts them into its
ordered registry before initialisation.

A plugin is a module path (``my_pkg.features``) or ``module:function``; the
callable runs once and registers the transformations it defines::

    from extractor.transformations.base_transformation import Transformation
    from thor_extract import extensions

    class TimeseriesFeatures(Transformation):
        def transform(self, df):
            df["ts_span_days"] = ...
            return df

        @property
        def features(self):
            return {"ts_span_days": "float64"}

    extensions.register("timeseries", TimeseriesFeatures, before="drop")

Columns a plugin needs from the raw document must already be in the frame the
earlier transformations produce; the compat frame currently carries the
collector blocks, so most new features can be computed from them directly.
"""

from __future__ import annotations

import importlib
from typing import Callable

from . import engine


class ExtensionError(RuntimeError):
    """A plugin could not be registered."""


def register(name: str, factory: Callable[..., object], *, before: str = "drop") -> None:
    """Insert a transformation into the extractor order, before ``before``."""
    dr = engine.load_extractor()
    registry = dr._all_transformations
    if name in registry:
        raise ExtensionError(f"transformation '{name}' is already registered")
    if before not in registry:
        raise ExtensionError(f"unknown anchor transformation '{before}'")

    items = list(registry.items())
    position = [key for key, _ in items].index(before)
    items.insert(position, (name, factory))
    registry.clear()
    registry.update(items)


def apply_plugins(specs: tuple[str, ...]) -> None:
    """Import each plugin and call its registration entry point."""
    for spec in specs:
        module_path, _, attribute = spec.partition(":")
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise ExtensionError(f"cannot import plugin '{module_path}': {exc}") from exc
        target = getattr(module, attribute, None) if attribute else getattr(module, "register", None)
        if target is None:
            raise ExtensionError(f"plugin '{spec}' has no '{attribute or 'register'}' entry point")
        target()
