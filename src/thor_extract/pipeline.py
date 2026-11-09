"""Batch pipeline: dataset Parquet → raw documents → extractor → feature Parquet."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from . import engine, extensions, mapping

log = logging.getLogger("thor_extract")

PANDAS_TO_ARROW: dict[str, pa.DataType] = {
    "Int64": pa.int64(),
    "int64": pa.int64(),
    "float64": pa.float64(),
    "Float64": pa.float64(),
    "bool": pa.bool_(),
    "str": pa.string(),
    "datetime64[ms, UTC]": pa.timestamp("ms", tz="UTC"),
}

# Stored as minutes (like the extractor's JSON output topic) so the column is a
# plain number for the trainer; parquet durations are a trap for most readers.
TIMEDELTA_COLUMNS = {"rdap_registration_period": "minutes"}

# Columns that may be carried through from the input verbatim (types are taken
# from the input file, so maps/lists survive untouched).
PASSTHROUGH_COLUMNS = (
    "label",
    "declared_class",
    "origin",
    "provenance",
    "score",
    "evaluated_on",
    "label_tags",
    "label_tag_counts",
    "content_categories",
)


class BuildError(RuntimeError):
    """The build cannot produce a trustworthy artifact."""


@dataclass(frozen=True)
class BuildConfig:
    batch_size: int = 1000
    dnskey_source: str = "dnsdata"
    html_selectors: str = "upstream"
    enabled_transformations: tuple[str, ...] | None = None
    extraction_date: date | None = None
    passthrough: tuple[str, ...] = ()
    plugins: tuple[str, ...] = ()
    limit: int | None = None
    overwrite: bool = False


@dataclass
class BuildStats:
    rows_in: int = 0
    rows_out: int = 0
    dropped: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    batches: int = 0
    elapsed_s: float = 0.0


def init_extractor(config: BuildConfig):
    """Initialise the forked extractor once per process."""
    dr = engine.load_extractor()
    if config.plugins:
        extensions.apply_plugins(config.plugins)

    dr_config: dict[str, Any] = {
        "data_dir": str(engine.DATA_DIR) + "/",
        "dnskey_source": config.dnskey_source,
        "html_selectors": config.html_selectors,
    }
    if config.enabled_transformations is not None:
        dr_config["enabled_transformations"] = list(config.enabled_transformations)
    if config.extraction_date is not None:
        dr_config["extraction_date"] = config.extraction_date.isoformat()
    dr.init_transformations(dr_config)

    # Fail loudly on a degraded run: without the whoisit bootstrap every RDAP
    # document silently parses to {} (compat swallows the error), which turns
    # the run into zeros rather than into a failure.
    if not dr._compat_transformation.whoisit_bootstrap.is_bootstrapped():
        raise BuildError(
            "whoisit bootstrap failed (no network?); RDAP features would silently be empty"
        )
    return dr


def feature_columns(dr) -> list[tuple[str, pa.DataType]]:
    """Output feature columns and their target types, in extractor order.

    Intermediate columns are excluded the same way the extractor excludes them:
    the ``drop`` transformation's list (``dns_A``, ``ip_data``, ``html``, …),
    plus ``tmp_*`` scratch columns and ``domain_name``. ``build`` verifies that
    every declared column is actually produced.
    """
    from extractor.transformations.drop_columns import _to_drop  # noqa: PLC0415

    columns: list[tuple[str, pa.DataType]] = []
    for name, dtype in dr._all_columns_with_types.items():
        if name == "domain_name" or name.startswith("tmp_") or name in _to_drop:
            continue
        if name in TIMEDELTA_COLUMNS:
            columns.append((name, pa.float64()))
            continue
        arrow_type = PANDAS_TO_ARROW.get(dtype)
        if arrow_type is None:
            raise BuildError(f"no arrow type for extractor column {name}:{dtype}")
        columns.append((name, arrow_type))
    return columns


def iter_batches(
    path: Path, columns: list[str], batch_size: int, limit: int | None
) -> Iterator[dict[str, list[Any]]]:
    pf = pq.ParquetFile(path)
    seen = 0
    for batch in pf.iter_batches(batch_size=batch_size, columns=columns):
        if limit is not None and seen + batch.num_rows > limit:
            batch = batch.slice(0, limit - seen)
        seen += batch.num_rows
        yield batch.to_pydict()
        if limit is not None and seen >= limit:
            return


@dataclass
class BatchFeatures:
    domains: list[str]
    extras: dict[str, list[Any]]
    values: dict[str, list[Any]]


def _to_list(series) -> list[Any]:
    """Arrow-compatible values: pandas' NA sentinel is not a Python None."""
    return series.astype(object).where(series.notna(), None).tolist()


def _extract_batch(dr, batch: dict[str, list[Any]], config: BuildConfig,
                   stats: BuildStats, extra: list[str]) -> BatchFeatures | None:
    rows = [{key: batch[key][i] for key in batch} for i in range(len(batch["domain"]))]
    raw_docs, kept = [], []
    for row in rows:
        domain = row.get("domain")
        if not domain:
            stats.dropped.append("<missing domain>")
            continue
        try:
            raw_docs.append(mapping.row_to_raw(row))
            kept.append(row)
        except Exception as exc:  # noqa: BLE001 — one bad row must not kill the build
            stats.errors[str(domain)] = f"{type(exc).__name__}: {exc}"
    if not raw_docs:
        return None

    df, errors = dr.extract_features(raw_docs)
    for domain, exc in errors.items():
        stats.errors[str(domain)] = f"{type(exc).__name__}: {exc}"
    if df is None:
        stats.dropped.extend(str(row["domain"]) for row in kept)
        return None

    df = df.reset_index(drop=True)
    for name, unit in TIMEDELTA_COLUMNS.items():
        if name in df.columns and unit == "minutes":
            df[name] = df[name].dt.total_seconds() / 60

    # Rows the extractor refused (parse error in a compat step) are absent from
    # the frame; keep the artifact aligned by joining the passthrough columns on
    # the domain instead of on row position.
    domains = [str(d) for d in df["domain_name"]]
    by_domain = {str(row["domain"]): row for row in kept}
    extras = {name: [by_domain[d][name] for d in domains] for name in extra}
    extracted = set(domains)
    for row in kept:
        if str(row["domain"]) not in extracted:
            stats.dropped.append(str(row["domain"]))

    values = {}
    for name, _ in feature_columns(dr):
        if name not in df.columns:
            raise BuildError(f"extractor did not produce the declared column {name}")
        values[name] = _to_list(df[name])
    unhandled = set(df.columns) - {"domain_name"} - set(values)
    if unhandled:
        raise BuildError(
            f"extractor emitted columns without a declared type: {', '.join(sorted(unhandled))}"
        )

    return BatchFeatures(domains=domains, extras=extras, values=values)


def build(input_path: Path, output_path: Path, config: BuildConfig) -> BuildStats:
    started = time.monotonic()
    stats = BuildStats()

    pf = pq.ParquetFile(input_path)
    input_names = list(pf.schema_arrow.names)
    missing = mapping.missing_columns(input_names)
    if missing:
        raise BuildError(
            f"{input_path} is not a supported dataset, missing columns: {', '.join(missing)}"
        )
    unknown = [c for c in config.passthrough if c not in input_names]
    if unknown:
        raise BuildError(f"--with columns not in {input_path.name}: {', '.join(unknown)}")
    extra = [c for c in config.passthrough if c in input_names]
    if output_path.exists() and not config.overwrite:
        raise BuildError(f"{output_path} exists; pass --overwrite to replace it")

    dr = init_extractor(config)
    features = feature_columns(dr)
    passthrough_fields = [pf.schema_arrow.field(name) for name in extra]
    schema = pa.schema(
        [pa.field("domain", pa.string())]
        + passthrough_fields
        + [pa.field(name, arrow_type) for name, arrow_type in features]
    )

    input_columns = list(mapping.REQUIRED_COLUMNS) + extra
    total = pf.metadata.num_rows if config.limit is None else min(pf.metadata.num_rows, config.limit)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    writer = pq.ParquetWriter(output_path, schema)
    try:
        for batch in iter_batches(input_path, input_columns, config.batch_size, config.limit):
            stats.rows_in += len(batch["domain"])
            result = _extract_batch(dr, batch, config, stats, extra)
            if result is None:
                continue
            arrays = [pa.array(result.domains, type=pa.string())]
            for field_ in passthrough_fields:
                arrays.append(pa.array(result.extras[field_.name], type=field_.type))
            for name, arrow_type in features:
                arrays.append(pa.array(result.values[name], type=arrow_type))
            table = pa.Table.from_arrays(arrays, schema=schema)
            writer.write_table(table)
            stats.batches += 1
            stats.rows_out += table.num_rows
            log.info(
                "batch %d: %d rows extracted (%d/%d input rows)",
                stats.batches,
                table.num_rows,
                stats.rows_in,
                total,
            )
    finally:
        writer.close()

    stats.elapsed_s = round(time.monotonic() - started, 1)
    if stats.rows_out == 0:
        output_path.unlink(missing_ok=True)
        raise BuildError("no rows were extracted; nothing written")
    _write_manifest(input_path, output_path, config, stats, features, extra, dr)
    return stats


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_manifest(input_path, output_path, config, stats, features, extra, dr) -> None:
    manifest = {
        "dataset": output_path.stem,
        "producedBy": "thor-extract",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "input": {
            "path": str(input_path),
            "size": input_path.stat().st_size,
            "sha256": _sha256(input_path),
        },
        "output": {
            "path": str(output_path),
            "size": output_path.stat().st_size,
            "sha256": _sha256(output_path),
        },
        "rows": {
            "input": stats.rows_in,
            "written": stats.rows_out,
            "dropped": len(stats.dropped),
            "droppedSample": stats.dropped[:20],
            "rowErrors": len(stats.errors),
            "rowErrorsSample": dict(list(stats.errors.items())[:20]),
        },
        "extractor": {
            "repo": engine.UPSTREAM_REPO,
            "branch": engine.UPSTREAM_BRANCH,
            "commit": engine.UPSTREAM_COMMIT,
            "localPatches": list(engine.LOCAL_PATCHES),
            "dnskeySource": config.dnskey_source,
            "htmlSelectors": config.html_selectors,
            "extractionDate": (config.extraction_date or date.today()).isoformat(),
            "enabledTransformations": (
                list(config.enabled_transformations)
                if config.enabled_transformations is not None
                else list(dr._all_transformations)
            ),
        },
        "columns": {
            "domain": "domain",
            "passthrough": extra,
            "featureCount": len(features),
            "features": [name for name, _ in features],
            "units": TIMEDELTA_COLUMNS,
        },
        "settings": {"batchSize": config.batch_size, "limit": config.limit},
        "elapsedSeconds": stats.elapsed_s,
    }
    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    log.info("manifest written to %s", manifest_path)
