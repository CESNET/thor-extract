"""Provenance of the fork in this directory.

Copied from `nesfit/domainradar-colext` (branch `main` — the branch the
DomainRadar deployment pins in its `options.sh`), `python/extractor/extractor/`.
`LOCAL_PATCHES` is the exhaustive list of changes against that commit; anything
not listed here is upstream code, verbatim.
"""

UPSTREAM_REPO = "https://github.com/nesfit/domainradar-colext"
UPSTREAM_BRANCH = "main"
UPSTREAM_COMMIT = "b99f22334bf02a1aa450154aafe2264c6952f8f4"
UPSTREAM_DATE = "2026-11-09"
UPSTREAM_LICENSE = "MIT (declared in python/extractor/pyproject.toml; the repo ships no LICENSE file)"
UPSTREAM_AUTHORS = (
    "Ondřej Ondryáš <xondry02@vut.cz>, Radek Hranický, Adam Horák — see the "
    "__author__ headers kept in each file"
)

LOCAL_PATCHES = (
    "compat: CompatibilityTransformation takes dnskey_source; dns_has_dnskey can read "
    "zone.hasDNSKEY (upstream reads dnsResult.dnsData.hasDNSKEY, which the collector never sets)",
    "compat: get_safe imported from .util instead of the `common` package (only that one "
    "helper was used, the package drags a Kafka client in)",
    "extractor: the compatibility transformation is built in init_transformations() from the "
    "config instead of at import time, so importing the module has no network side effect",
    "extractor: extract_features() refuses to run before init_transformations()",
    "_helpers: EXTRACTION_DATE is honoured by todays_midnight_timestamp() so a rebuild can be "
    "reproducible",
    "html: the tag index is built in one pass over the tree instead of soup.find_all(name) per "
    "tag name (same lists, same order, ~4x faster)",
    "html: selectors that upstream passes as 'attr=\"value\"' to get_elements() are matched "
    "properly when html_selectors='fixed'; the default 'upstream' reproduces the original "
    "(always-empty) selector",
    "app.py and __main__.py (the Kafka consumer wrapper) are not copied — this fork only exposes "
    "the extraction API",
    "data/ moved from python/extractor/data/ to src/extractor/data/ so it travels with the package",
)

KNOWN_UPSTREAM_ISSUES = (
    "get_tags_f: quadratic tag index (fixed here, see LOCAL_PATCHES)",
    "get_elements: attribute-value selectors treated as attribute names, 10 html_* features are "
    "constant 0 (behind html_selectors='fixed' here)",
    "compat: hasDNSKEY read from the wrong block (behind dnskey_source='zone' here)",
    "compat: DNSSEC score never computed (dns_dnssec_score stays 0.0)",
    "html_num_of_tags counts distinct tag names, not tags — kept as-is (it is a feature value)",
)
