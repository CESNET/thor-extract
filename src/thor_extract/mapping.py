"""Dataset → extractor input mapping.

The production DomainRadar extractor consumes the raw *merged collector
document* (``dnsResult``, ``zone``, ``tlsResult``, ``ipResults``,
``rdapDomainResult``) and normalises it in ``extractor.compat``. Our published
datasets store the same collectors as canonical-v1 blocks in flat Parquet
columns (``dns_json``, ``zone_json``, …), so the whole adapter is this module:
re-nest the blocks, lift the HTTP body into ``tlsResult.html`` (that is where
the DomainRadar collector keeps it), and key the per-IP collector results by IP
under the names ``compat._make_ip_data`` looks up.

Nothing here may change a value — only the shape. Where ``dns_has_dnskey``
comes from is decided by the extractor (`dnskey_source`), not here: upstream
reads ``dnsResult.dnsData.hasDNSKEY`` while the collector writes it into
``zone.zone``, so production's value is constant 0.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Iterable

# Columns the extractor needs. A dataset missing one of these is not a v3
# dataset and the run has to stop before the first batch, not mid-file.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "domain",
    "dns_json",
    "dns_collectedAt",
    "zone_json",
    "tls_json",
    "http_json",
    "rdap_dn_json",
    "rdap_dn_collectedAt",
    "rdap_ip_json",
    "geo_asn_json",
    "rtt_json",
)

# dataset column -> key under ipResults[ip] that compat._make_ip_data reads
IP_BLOCK_COLUMNS: dict[str, str] = {
    "geo_asn_json": "geo-asn",
    "rdap_ip_json": "rdap-ip",
    "rtt_json": "rtt",
}

class MappingError(ValueError):
    """A dataset row or column set cannot be mapped onto the extractor input."""


def missing_columns(columns: Iterable[str]) -> list[str]:
    present = set(columns)
    return [c for c in REQUIRED_COLUMNS if c not in present]


def _load(value: Any) -> dict:
    """Unpack a JSON block column; NULL and empty string both mean "no block"."""
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    block = json.loads(value)
    if not isinstance(block, dict):
        raise MappingError(f"expected a JSON object, got {type(block).__name__}")
    return block


def _timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return datetime.fromisoformat(str(value))


def _ip_results(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Merge the per-IP collector blocks into ``{ip: {collector: result}}``.

    ``compat._make_ip_data`` walks the DNS ``ips`` list and looks each address
    up here; a collector whose result lacks ``statusCode`` would raise a
    ``KeyError`` inside ``_make_ip_average_rtt``, so it is dropped instead.
    """
    merged: dict[str, dict[str, Any]] = {}
    for column, key in IP_BLOCK_COLUMNS.items():
        for ip, result in _load(row.get(column)).items():
            if not isinstance(result, dict) or "statusCode" not in result:
                continue
            merged.setdefault(ip, {})[key] = result
    return merged


def row_to_raw(row: dict[str, Any]) -> dict[str, Any]:
    """Build the raw DomainRadar document for one dataset row."""
    dns = _load(row.get("dns_json"))
    zone_block = _load(row.get("zone_json"))
    tls = _load(row.get("tls_json"))
    http = _load(row.get("http_json"))
    rdap_dn = _load(row.get("rdap_dn_json"))

    zone = zone_block.get("zone")

    return {
        "domain_name": row["domain"],
        "dnsResult": {
            "dnsData": dns.get("dnsData") or {},
            "ips": dns.get("ips") or [],
            "lastAttempt": _timestamp(row.get("dns_collectedAt")),
        },
        "zone": zone,
        "tlsResult": {
            "tlsData": tls.get("tlsData"),
            # DomainRadar keeps the fetched HTML inside tlsResult; our dataset
            # has it in the `http` collector block.
            "html": http.get("textData") or "",
        },
        "ipResults": _ip_results(row),
        "rdapDomainResult": {
            "rdapData": rdap_dn.get("rdapData"),
            "entities": rdap_dn.get("entities"),
            "lastAttempt": _timestamp(row.get("rdap_dn_collectedAt")),
        },
    }
