"""Command line: ``thor-extract features`` and ``thor-extract info``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from . import engine, extensions, mapping, pipeline


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _info(path: Path) -> int:
    pf = pq.ParquetFile(path)
    names = list(pf.schema_arrow.names)
    missing = mapping.missing_columns(names)
    meta = pf.metadata

    print(f"file:        {path}")
    print(f"rows:        {meta.num_rows}")
    print(f"row groups:  {meta.num_row_groups}")
    print(f"extractor:   {engine.UPSTREAM_COMMIT[:8]} (fork of {engine.UPSTREAM_REPO} @ {engine.UPSTREAM_BRANCH})")
    print(f"mapped:      {'yes' if not missing else 'NO — missing ' + ', '.join(missing)}")

    print("\ncollector blocks (non-null rows):")
    first_group = meta.row_group(0)
    # The flattened parquet schema expands maps/structs into several leaves, so
    # the arrow field index is not the physical column index — match by path.
    physical = {
        first_group.column(i).path_in_schema: i for i in range(first_group.num_columns)
    }
    for column in mapping.REQUIRED_COLUMNS:
        if column not in names or column == "domain":
            continue
        index = physical.get(column)
        present = 0
        for group in range(meta.num_row_groups):
            chunk = meta.row_group(group).column(index) if index is not None else None
            stats = chunk.statistics if chunk is not None else None
            if stats is None or stats.null_count is None:
                present = -1
                break
            present += meta.row_group(group).num_rows - stats.null_count
        share = "" if present < 0 else f" ({present / meta.num_rows:.1%})"
        print(f"  {column:<22} {'?' if present < 0 else present}{share}")

    try:
        dr = engine.load_extractor()
        dr.init_transformations({"data_dir": str(engine.DATA_DIR) + "/"})
        features = pipeline.feature_columns(dr)
    except Exception as exc:  # noqa: BLE001 — info must not fail on a missing submodule
        print(f"\nfeature list unavailable: {exc}")
        return 0 if not missing else 1
    print(f"\nfeature columns: {len(features)}")
    return 0 if not missing else 1


def _features(args: argparse.Namespace) -> int:
    config = pipeline.BuildConfig(
        batch_size=args.batch_size,
        dnskey_source=args.dnskey_source,
        html_selectors=args.html_selectors,
        enabled_transformations=_csv(args.transformations) if args.transformations else None,
        extraction_date=args.extraction_date,
        passthrough=_csv(args.with_columns) if args.with_columns else (),
        limit=args.limit,
        overwrite=args.overwrite,
        plugins=_csv(args.plugin) if args.plugin else (),
    )
    try:
        stats = pipeline.build(args.input, args.output, config)
    except (pipeline.BuildError, extensions.ExtensionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"{stats.rows_out}/{stats.rows_in} rows → {args.output} "
        f"({stats.batches} batches, {stats.elapsed_s}s, "
        f"{len(stats.errors)} row errors, {len(stats.dropped)} dropped)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="log every batch")

    parser = argparse.ArgumentParser(
        prog="thor-extract",
        description="Compute the production DomainRadar feature vector over a THOR dataset.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    features = sub.add_parser(
        "features", parents=[common], help="build a feature Parquet from a dataset Parquet"
    )
    features.add_argument("input", type=Path)
    features.add_argument("-o", "--output", type=Path, required=True)
    features.add_argument("--batch-size", type=int, default=1000)
    features.add_argument("--limit", type=int, help="stop after N input rows")
    features.add_argument("--overwrite", action="store_true")
    features.add_argument(
        "--with",
        dest="with_columns",
        help=f"comma-separated input columns to carry through ({', '.join(pipeline.PASSTHROUGH_COLUMNS)})",
    )
    features.add_argument(
        "--dnskey-source",
        choices=engine.DNSKEY_SOURCES,
        default="dnsdata",
        help="where dns_has_dnskey comes from; 'dnsdata' replicates production (always 0)",
    )
    features.add_argument(
        "--html-selectors",
        choices=engine.HTML_SELECTORS,
        default="upstream",
        help="'upstream' reproduces the always-empty attribute-value selectors, "
        "'fixed' matches them (10 html_* features)",
    )
    features.add_argument("--transformations", help="comma-separated subset of extractor transformations")
    features.add_argument("--extraction-date", type=date.fromisoformat, help="pin the run date (YYYY-MM-DD)")
    features.add_argument("--plugin", action="append", help="module[:function] registering extra transformations")
    features.set_defaults(func=_features)

    info = sub.add_parser(
        "info", parents=[common], help="describe an input dataset and check it is mappable"
    )
    info.add_argument("input", type=Path)
    info.set_defaults(func=lambda args: _info(args.input))

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
