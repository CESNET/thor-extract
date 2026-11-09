"""Fork of the DomainRadar feature extractor.

Copied from `nesfit/domainradar-colext` at the commit the DomainRadar deployment
pins; provenance and the exhaustive list of local patches are in `UPSTREAM.md`
(machine-readable in `upstream.py`). The fork exists so the fixes we need — a
correct DNSKEY source, attribute-value selectors, and a non-quadratic HTML tag
index — live in code we can test and review instead of a `sys.path` shim over a
submodule.

The default configuration reproduces upstream behaviour exactly; the deviations
are opt-in (`extractor.init_transformations({"dnskey_source": ..., "html_selectors": ...})`).
"""

from .upstream import LOCAL_PATCHES, UPSTREAM_BRANCH, UPSTREAM_COMMIT, UPSTREAM_REPO

__all__ = [
    "LOCAL_PATCHES",
    "UPSTREAM_BRANCH",
    "UPSTREAM_COMMIT",
    "UPSTREAM_REPO",
]
