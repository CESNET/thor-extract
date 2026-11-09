# Upstream: `nesfit/domainradar-colext` @ `b99f223`

This directory is a copy of the production feature extractor. It is a fork, not a dependency:
the DomainRadar deployment pins branch `main` in its `options.sh` and `pull.sh` fast-forwards it,
so `main` is the code that actually runs in DR — and from 2026‑11‑09 we carry it in-tree so the
fixes we need live in code we can test instead of a `sys.path` shim over a submodule.

| | |
|---|---|
| repo | `https://github.com/nesfit/domainradar-colext` |
| branch | `main` |
| commit | `b99f22334bf02a1aa450154aafe2264c6952f8f4` („connect: Add domain to value transformation") |
| copied | 2026‑11‑09, `python/extractor/extractor/` (3 067 lines of `.py`) + `python/extractor/data/` (1.2 MB, 4 n‑gram files) |
| not copied | `app.py`, `__main__.py` (the Kafka consumer wrapper), `python/common/` (only `get_safe`, re-implemented in `util.py`), the Java collectors, merger, classifier unit |
| license | MIT, as declared in `python/extractor/pyproject.toml` — **the upstream repo ships no LICENSE file**, so this note is the licence basis; the `__author__` headers (Ondřej Ondryáš, Radek Hranický, Adam Horák) are kept in every file |

## Defaults = upstream

`dnskey_source="dnsdata"` and `html_selectors="upstream"` reproduce the original behaviour,
quirk for quirk. Verified 2026‑11‑09: 3 000 rows of `dataset_v3_analytic` through the fork are
**table-equal** (264 columns, schema included) to the same run through the submodule.

## Local patches (exhaustive)

1. `compat.py` — `CompatibilityTransformation(dnskey_source=…)`. Upstream reads
   `dnsResult.dnsData.hasDNSKEY`; the collector writes it into `zone.zone`
   (`python/common/models.py` → `ZoneInfo`), so upstream's `dns_has_dnskey` is 0 for every row.
   `dnskey_source="zone"` reads the real block.
2. `compat.py` — `get_safe` imported from `.util` instead of `common` (only that helper was used).
3. `extractor.py` — the compatibility transformation is built in `init_transformations()` from the
   config, not at import time. Importing the module no longer performs a network call (the
   `whoisit` bootstrap).
4. `extractor.py` — `extract_features()` raises instead of silently using an uninitialised state.
5. `_helpers.py` — `EXTRACTION_DATE` is honoured by `todays_midnight_timestamp()`, so
   `init_transformations({"extraction_date": "2026-11-09"})` makes a rebuild reproducible.
6. `transformations/html.py` — the tag index is one pass over the tree instead of
   `soup.find_all(name)` per tag name. Same lists, same order; **4.3× faster** on the html
   transformer, which is 92 % of extraction time.
7. `transformations/html.py` — `get_elements(tag, attr, value)`. Upstream callers passed
   `'rel="stylesheet"'` as an *attribute name*, so 10 features were always 0; with
   `html_selectors="fixed"` the value is matched properly. Default still emits the zeros.
8. `data/` moved from `python/extractor/data/` to `src/extractor/data/` so it travels with the
   package.

Known upstream issues kept as features (they change model input, so they are flags, not fixes):
`dns_dnssec_score` is never computed (DNSSEC is not collected) and `html_num_of_tags` counts
distinct tag names, not tags.

## Re-syncing with upstream

```bash
git clone --depth 1 https://github.com/nesfit/domainradar-colext /tmp/colext
diff -r /tmp/colext/python/extractor/extractor src/extractor \
  -x __pycache__ -x app.py -x __main__.py -x data
```

Every hunk that is not in the list above is either a new upstream change to review or a patch we
forgot to document. When upstream adopts a patch, delete the local hunk and the line in
`upstream.py:LOCAL_PATCHES` in the same change.
