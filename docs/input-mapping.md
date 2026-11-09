# Input mapping: dataset column → extractor input

The production extractor (`vendor/domainradar-colext`, `python/extractor`) reads
the raw merged collector document; `extractor.compat.CompatibilityTransformation`
then normalises it into the legacy frame. Our datasets carry the same collectors
as canonical-v1 blocks in flat Parquet columns, so the adapter is
`src/thor_extract/mapping.py` and nothing else.

| dataset column | raw document key | consumed by |
|---|---|---|
| `domain` | `domain_name` | `lexical` (all `lex_*`, 62 features) |
| `dns_json` → `dnsData` | `dnsResult.dnsData` | `compat` → `dns_*` record columns, TTLs, CNAME, email extras |
| `dns_json` → `ips` | `dnsResult.ips` | `compat._make_ip_data` (which IPs get looked up) |
| `dns_collectedAt` | `dnsResult.lastAttempt` | `compat` → `dns_evaluated_on` (dropped later) |
| `zone_json` → `zone` | `zone` (`zone`, `soa`, `hasDNSKEY`) | `compat._make_soa` (SOA features), `dns_zone*` |
| `tls_json` → `tlsData` | `tlsResult.tlsData` | `compat._make_tls` → `tls_*` (24 features) |
| `http_json` → `textData` | `tlsResult.html` | `html` (87 features) |
| `rdap_ip_json` (per IP) | `ipResults.<ip>.rdap-ip` | `compat._make_ip_data` → `rdap_ip_*` |
| `geo_asn_json` (per IP) | `ipResults.<ip>.geo-asn` | `compat._make_ip_data` → ASN + `geo_*` |
| `rtt_json` (per IP) | `ipResults.<ip>.rtt` | `compat._make_ip_average_rtt` → `ip_mean_average_rtt` |
| `rdap_dn_json` → `rdapData`, `entities` | `rdapDomainResult.rdapData`, `.entities` | `compat._parse_rdap_dn` → `rdap_*` (24 features) |
| `rdap_dn_collectedAt` | `rdapDomainResult.lastAttempt` | `compat` → `rdap_evaluated_on` (dropped later) |

Notes that cost time if missed:

- **The HTML lives in `tlsResult` in DomainRadar**, not in an `http` collector —
  the fetched body is lifted from `http_json.textData` into `tlsResult.html`.
- **Per-IP results are looked up by the IP in `dnsResult.ips`**, so the merge key
  is the address, not the position; a collector result without `statusCode`
  would break `_make_ip_average_rtt` and is dropped instead.
- **`hasDNSKEY` is written by the collector into `zone.zone`**, but upstream `compat`
  reads it from `dnsResult.dnsData`, so production yields
  `dns_has_dnskey = 0` for every row. The fork makes the source a parameter:
  default `--dnskey-source dnsdata` keeps upstream behaviour, `--dnskey-source zone`
  reads the block the collector actually fills. The zone block is passed through
  verbatim either way — the mapping does not rewrite it.
- **`rdapTarget`** is not used: `compat` keys the RDAP parse on `domain`.
- Blocks that are NULL are passed as empty documents; the extractor already
  treats "collector failed" and "collector absent" the same way.
- Row identity is `domain`; rows without it are dropped and counted, and the
  output is joined back on the domain, so a row the extractor refuses cannot
  shift the feature vector onto the next domain.
