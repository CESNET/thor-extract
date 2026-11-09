# Output contract

One Parquet file plus `<name>.parquet.manifest.json` next to it.

## Columns

| position | column | type | note |
|---|---|---|---|
| 1 | `domain` | `string` | row identity, same values and order as the input |
| 2…n | `--with` columns | as in input | optional passthrough (`label`, `declared_class`, `label_tags`, `label_tag_counts`, …); types are copied from the input schema so maps and lists survive |
| n+1… | 263 features | see below | in extractor order (`_all_columns_with_types`, `tmp_*` and dropped intermediates excluded) |

Feature families: `html` 87, `lex` 62, `dns` 40, `tls` 24, `rdap` 24, `geo` 18,
`ip` 8. Types are `int64`, `double`, `bool` and `timestamp[ms, UTC]`.

Exceptions to the extractor's own dtypes:

- `rdap_registration_period` is written as **minutes (double)** — the extractor
  carries a `timedelta64[ms]`, and its JSON output topic already converts it the
  same way. `dns_evaluated_on`, `rdap_evaluated_on` and the raw collector columns
  are dropped by the extractor's `drop` transformation and never appear.

Twelve columns are constant with the default flags — they are upstream behaviour, and the fork
can change eleven of them per run (the twelfth has no data behind it):

| column(s) | default | flag that changes it |
|---|---|---|
| `dns_has_dnskey` | always `0` | `--dnskey-source zone` → ~6 % of v3 rows are 1 |
| `html_num_of_css_internal`, `html_num_of_css_external`, `html_num_of_anchors_to_content`, `html_num_of_anchors_to_void`, `html_num_of_anchors_to_hash`, `html_num_of_input_password`, `html_num_of_link_type_app`, `html_num_of_link_rel`, `html_num_of_icon`, `html_icon_external` | always `0` | `--html-selectors fixed` → real counts (upstream passes `'rel="stylesheet"'` as an attribute *name*) |
| `dns_dnssec_score` | always `0.0` | none — DNSSEC was never collected; kept as a placeholder |

Measured on 3 000 rows: `--dnskey-source zone --html-selectors fixed` changes exactly those 11
columns and nothing else. Both choices are recorded in the manifest (`extractor.dnskeySource`,
`extractor.htmlSelectors`), so two feature files are always comparable.

## Manifest

Everything needed to decide whether two feature files are comparable:
`input.path/size/sha256`, `output.size/sha256`, `rows.input/written/dropped/rowErrors`
with samples, `extractor.commit` (the upstream commit), `branch`, `localPatches` (the fork's diff to it),
`dnskeySource`, `htmlSelectors`,
`extractionDate`, `enabledTransformations`, `columns.features`, `units`,
`settings.batchSize/limit`, `elapsedSeconds`.

`extractionDate` matters: `rdap_domain_age`, `rdap_time_from_last_change`,
`rdap_domain_active_time` and the TLS validity lengths are computed against
"today". Pin it with `--extraction-date` to make a rebuild reproducible.
