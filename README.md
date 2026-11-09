# thor-extract

Feature extraction over THOR datasets, built on the **production DomainRadar
extractor**. Reads a canonical-v1 dataset Parquet (`dataset_v3_*`), writes a
Parquet with `domain` + the 263-column feature vector.

The extractor is not reimplemented, it is **forked**: `src/extractor/` is a copy
of `nesfit/domainradar-colext` @ `main` (the branch `nesfit/domainradar`
deploys) at one fixed commit, with a short enumerated list of local patches —
provenance and the full list are in [`src/extractor/UPSTREAM.md`](src/extractor/UPSTREAM.md).
Defaults reproduce upstream behaviour exactly (verified: this fork's 3 000-row
output is table-equal to the submodule's), and every deviation is an opt-in flag.

```bash
git clone <repo> && cd thor-extract

# run it — uv builds .venv from uv.lock on first use, no activation
uv run thor-extract info data/parquet/dataset_v3_analytic.parquet
uv run thor-extract features data/parquet/dataset_v3_analytic.parquet -o out/v3_features.parquet

# dev checkout (env + dev group + the project in editable mode)
uv sync
uv run pytest

# or put the command on PATH; --editable keeps it tracking the working tree
uv tool install --editable .
thor-extract features ... -o ... --with label,declared_class   # carry label columns along
```

Neither the wheel nor the command needs uv: `pipx install .`, `pip install .` and
`poetry add git+<repo>` all work (verified from a clean venv — the wheel carries the n-gram tables).
Only the dev workflow is uv's: `uv.lock`, `.python-version` and the `dev` dependency group are
ignored by other tools, so they resolve their own versions.

Python is pinned to 3.11 by `.python-version` (upstream requires `^3.11`, `pyarrow` is capped
below 17) and `uv.lock` pins the exact dependency versions — feature values depend on
`lxml`/`bs4`/`pandas` behaviour, so two runs should not differ by a resolver upgrade.

| flag | default | effect |
|---|---|---|
| `--with label,label_tags,…` | none | carry input columns through; types copied from the input |
| `--dnskey-source zone` | `dnsdata` | `dns_has_dnskey` from `zone.hasDNSKEY` (~6 % of v3 rows) instead of upstream's always-0 |
| `--html-selectors fixed` | `upstream` | fixes 10 `html_*` features upstream always emits as 0 |
| `--extraction-date YYYY-MM-DD` | today | pins the date used by age/validity features |
| `--plugin module[:fn]` | none | registers extra `Transformation` classes |

Docs: [`docs/input-mapping.md`](docs/input-mapping.md) (dataset column → extractor
input), [`docs/output.md`](docs/output.md) (output contract, manifest, units).

Requires network on start (the extractor's `whoisit` bootstrap); the run aborts
if the bootstrap fails, because every RDAP feature would silently become empty.
