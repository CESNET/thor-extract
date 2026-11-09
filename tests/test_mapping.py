"""Unit tests for the dataset → extractor-input mapping (no extractor needed)."""

import json
from datetime import datetime

from thor_extract import mapping


def _row(**overrides):
    row = {
        "domain": "example.com",
        "dns_json": json.dumps(
            {
                "dnsData": {"A": ["1.2.3.4"], "ttlValues": {"A": 300}},
                "ips": [{"ip": "1.2.3.4", "rrType": "A"}],
            }
        ),
        "dns_collectedAt": "2026-09-25T20:00:00+00:00",
        "zone_json": json.dumps(
            {"zone": {"zone": "example.com", "soa": {"primaryNS": "ns.example.com"},
                      "hasDNSKEY": True}}
        ),
        "tls_json": json.dumps({"tlsData": {"cipher": "TLS_AES_128_GCM_SHA256"}}),
        "http_json": json.dumps({"textData": "<html>hi</html>"}),
        "rdap_dn_json": json.dumps({"rdapData": None, "entities": None}),
        "rdap_dn_collectedAt": "2026-09-25T20:01:00+00:00",
        "rdap_ip_json": json.dumps(
            {"1.2.3.4": {"statusCode": 0, "data": {"handle": "NET-1"}}}
        ),
        "geo_asn_json": json.dumps(
            {"1.2.3.4": {"statusCode": 0, "data": {"asn": 64500, "countryCode": "CZ"}}}
        ),
        "rtt_json": json.dumps(
            {"1.2.3.4": {"statusCode": 0, "data": {"avg": 12.5, "received": 2}}}
        ),
    }
    row.update(overrides)
    return row


def test_missing_columns_detects_v3_contract():
    assert mapping.missing_columns(mapping.REQUIRED_COLUMNS) == []
    assert mapping.missing_columns(["domain", "dns_json"]) == [
        c for c in mapping.REQUIRED_COLUMNS if c not in {"domain", "dns_json"}
    ]


def test_blocks_are_renested():
    raw = mapping.row_to_raw(_row())
    assert raw["domain_name"] == "example.com"
    assert raw["dnsResult"]["dnsData"]["A"] == ["1.2.3.4"]
    assert raw["dnsResult"]["ips"] == [{"ip": "1.2.3.4", "rrType": "A"}]
    assert raw["zone"]["zone"] == "example.com"
    assert raw["tlsResult"]["tlsData"]["cipher"] == "TLS_AES_128_GCM_SHA256"
    assert raw["rdapDomainResult"]["rdapData"] is None


def test_http_body_is_lifted_into_tls_result():
    # DomainRadar keeps the fetched HTML in tlsResult; our dataset has it in http.
    assert mapping.row_to_raw(_row())["tlsResult"]["html"] == "<html>hi</html>"
    assert mapping.row_to_raw(_row(http_json=None))["tlsResult"]["html"] == ""


def test_per_ip_blocks_are_merged_under_production_keys():
    results = mapping.row_to_raw(_row())["ipResults"]["1.2.3.4"]
    assert set(results) == {"geo-asn", "rdap-ip", "rtt"}
    assert results["rtt"]["data"]["avg"] == 12.5


def test_collector_result_without_status_code_is_dropped():
    # compat._make_ip_average_rtt indexes results["statusCode"] unconditionally.
    row = _row(rtt_json=json.dumps({"1.2.3.4": {"data": {"avg": 1.0}}}))
    assert "rtt" not in mapping.row_to_raw(row)["ipResults"]["1.2.3.4"]


def test_zone_block_is_carried_verbatim():
    # hasDNSKEY stays where the collector put it; compat decides whether to read it.
    assert mapping.row_to_raw(_row())["zone"]["hasDNSKEY"] is True


def test_timestamps_are_parsed():
    raw = mapping.row_to_raw(_row())
    assert raw["dnsResult"]["lastAttempt"] == datetime.fromisoformat(
        "2026-09-25T20:00:00+00:00"
    )
    assert raw["rdapDomainResult"]["lastAttempt"].minute == 1


def test_missing_blocks_map_to_empty_documents():
    raw = mapping.row_to_raw(
        {"domain": "empty.example"} | {c: None for c in mapping.REQUIRED_COLUMNS if c != "domain"}
    )
    assert raw["dnsResult"] == {"dnsData": {}, "ips": [], "lastAttempt": None}
    assert raw["ipResults"] == {}
    assert raw["zone"] is None and raw["tlsResult"]["tlsData"] is None
