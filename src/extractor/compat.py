"""compat.py: A compatibility transformation that converts the raw data collected by the DomainRadar Collector
into a format that can be used by the "legacy" transformations."""
__author__ = "Ondřej Ondryáš <xondry02@vut.cz>"

import base64
from datetime import datetime, UTC

import cryptography.x509
from cryptography.x509 import Certificate
from whois.parser import WhoisEntry
from whoisit import Bootstrap
from whoisit.errors import ParseError
from whoisit.parser import ParseIPNetwork, ParseDomain

from .util import get_safe


# omitted:
# - label, category
# - dns_dnssec, dns_has_dnskey, dns_zone_dnskey_selfsign_ok (DNSSEC not collected)
# - tls_evaluated_on (== dns_evaluated_on)
# added: ip_data.nerd_rep
# ip_data.geo.country -> country_code
# ip_data.remarks.average_rtt -> ip_data.average_rtt
# ip_data.rdap.network is an IPvXNetwork object
# rdap dates are always UTC
# flattened geo data lists present already (previously done in a transformation)
# only one of SOA and zone_SOA is always present
# certificates are represented directly as cryptography's Certificate; or str (parse errors)

class CompatibilityTransformation:
    datatypes = {
        "domain_name": "str",
        "dns_email_extras": "object",
        "dns_ttls": "object",
        "dns_zone": "str",
        "dns_SOA": "object",
        "dns_zone_SOA": "object",
        "dns_A": "object",
        "dns_AAAA": "object",
        "dns_TXT": "object",
        "dns_NS": "object",
        "dns_MX": "object",
        "dns_CNAME": "object",
        "dns_has_dnskey": "Int64",
        "tls": "object",
        "html": "str",
        "dns_evaluated_on": "datetime64[ms, UTC]",
        "rdap_evaluated_on": "datetime64[ms, UTC]",
        "rdap_registration_date": "datetime64[ms, UTC]",
        "rdap_expiration_date": "datetime64[ms, UTC]",
        "rdap_last_changed_date": "datetime64[ms, UTC]",
        "rdap_dnssec": "object",
        "rdap_entities": "object",
        "ip_data": "object",
        "countries": "object",
        "latitudes": "object",
        "longitudes": "object"
    }

    def __init__(self, dnskey_source: str = "dnsdata"):
        # local patch: where dns_has_dnskey comes from (see UPSTREAM.md)
        if dnskey_source not in ("dnsdata", "zone"):
            raise ValueError(f"dnskey_source must be 'dnsdata' or 'zone', got {dnskey_source!r}")
        self.dnskey_source = dnskey_source
        self.whoisit_bootstrap = Bootstrap(allow_insecure_ssl=False)
        try:
            self.whoisit_bootstrap.bootstrap(True, True)
            # TODO: save bootstrap data?
        except Exception as e:
            print(f"Failed to bootstrap the whoisit library: {e}")

    def transform(self, data: dict) -> dict:
        """
        Transforms the raw data object collected by the DomainRadar collector into a format that can be used by the
        "legacy" transformations.

        This method takes a dictionary of raw data as input and returns a dictionary of
        transformed data. The transformation process involves several steps, including parsing DNS and RDAP data,
        extracting and formatting specific data fields, and handling potential errors or inconsistencies in the raw data.

        Args:
            data (dict): A dictionary containing the raw data collected by the DomainRadar collector.

        Returns:
            dict: A dictionary containing the transformed data.
        """
        if data.get("invalid_data"):
            return data

        dns_data = get_safe(data, "dnsResult.dnsData") or {}
        rdap_result = data.get("rdapDomainResult", {}) or {}

        rdap_data: dict | None = rdap_result.get("rdapData")
        rdap_entities: list | None = rdap_result.get("entities")
        whois_parsed: dict | None = rdap_result.get("whoisParsed")
        whois_raw: str | None = rdap_result.get("whoisRaw")

        soa, zone_soa = self._make_soa(data)
        rdap_parsed = self._parse_rdap_dn(data["domain_name"], rdap_data, rdap_entities, whois_parsed, whois_raw)
        ip_data = self._make_ip_data(data)
        country_codes, latitudes, longitudes = self._flatten_ip_data(ip_data)

        reg_date = self._ensure_utc_datetime(rdap_parsed.get("registration_date"))
        exp_date = self._ensure_utc_datetime(rdap_parsed.get("expiration_date"))
        last_changed_date = self._ensure_utc_datetime(rdap_parsed.get("last_changed_date"))

        res = {
            "domain_name": data["domain_name"],
            "dns_email_extras": self._make_email_extras(data),
            "dns_ttls": dns_data.get("ttlValues", None),
            "dns_zone": get_safe(data, "zone.zone"),
            "dns_SOA": soa,
            "dns_zone_SOA": zone_soa,
            "dns_A": dns_data.get("A", None),
            "dns_AAAA": dns_data.get("AAAA", None),
            "dns_TXT": dns_data.get("TXT", None),
            "dns_NS": self._make_ns(dns_data),
            "dns_MX": self._make_mx(dns_data),
            "dns_CNAME": get_safe(dns_data, "CNAME.value"),
            "dns_has_dnskey": 1 if self._has_dnskey(data, dns_data) else 0,
            "tls": self._make_tls(data),
            "html": get_safe(data, "tlsResult.html") or "",
            "dns_evaluated_on": get_safe(data, "dnsResult.lastAttempt"),
            "rdap_evaluated_on": get_safe(data, "rdapDomainResult.lastAttempt"),
            "rdap_registration_date": reg_date,
            "rdap_expiration_date": exp_date,
            "rdap_last_changed_date": last_changed_date,
            "rdap_dnssec": self._make_rdap_dnssec(rdap_data),
            "rdap_entities": rdap_parsed.get("entities"),
            "ip_data": ip_data,
            "countries": country_codes,
            "latitudes": latitudes,
            "longitudes": longitudes
        }

        return res

    def _has_dnskey(self, data: dict, dns_data: dict) -> bool:
        """DNSKEY presence.

        Upstream reads `dnsResult.dnsData.hasDNSKEY`; the collector writes it into
        `zone.zone` (`common/models.py` → ZoneInfo), so upstream's value is 0 for
        every row. `dnskey_source="zone"` reads the real place.
        """
        if self.dnskey_source == "zone":
            zone = data.get("zone")
            return bool(isinstance(zone, dict) and zone.get("hasDNSKEY"))
        return bool(get_safe(dns_data, "hasDNSKEY"))

    @staticmethod
    def _ensure_utc_datetime(dt: datetime | None) -> datetime | None:
        """
        Ensures that the provided datetime object is timezone-aware and in UTC.

        This method takes a datetime object as input and returns a datetime object that is timezone-aware and in UTC.
        If the input datetime object is naive (i.e., not timezone-aware), it is assumed to be in UTC.
        If the input datetime object is already timezone-aware, it is converted to UTC.

        Args:
            dt (datetime | None): The input datetime object. If None, None is returned.

        Returns:
            datetime | None: The input datetime object converted to a timezone-aware datetime object in UTC.
            If the input was None, None is returned.
        """
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    @staticmethod
    def _flatten_ip_data(ip_data: list):
        """
        Flattens the IP data by extracting country codes, latitudes, and longitudes.

        This method takes a list of IP data dictionaries as input. Each dictionary is expected to have a 'geo' key
        containing a dictionary with 'country_code', 'latitude', and 'longitude' keys. The method iterates over the list
        and appends the values of these keys to respective lists. If the 'geo' key is not present or its value is None,
        the IP data dictionary is skipped.

        Args:
            ip_data (list): A list of dictionaries containing IP data.

        Returns:
            tuple: A tuple containing three lists - country codes, latitudes, and longitudes extracted from the IP data.
        """
        country_codes = []
        latitudes = []
        longitudes = []

        for ip in ip_data:
            if ip["geo"] is None:
                continue
            country_codes.append(ip['geo']['country_code'])
            latitudes.append(ip['geo']['latitude'])
            longitudes.append(ip['geo']['longitude'])

        return country_codes, latitudes, longitudes

    @staticmethod
    def _make_email_extras(data: dict) -> dict:
        """
        Extracts and returns the presence of SPF, DKIM, and DMARC records from the DNS TXT records.

        This method takes a dictionary of raw data as input and checks the DNS TXT records for the presence of SPF,
        DKIM, and DMARC records. It returns a dictionary with boolean values indicating the presence of these records.

        Args:
            data (dict): A dictionary containing the raw data collected by the DomainRadar collector.

        Returns:
            dict: A dictionary with keys 'spf', 'dkim', and 'dmarc'. The values are boolean indicating the presence of
            the respective records in the DNS TXT records.
        """
        ret = {
            "spf": False,
            "dkim": False,
            "dmarc": False
        }

        txt = get_safe(data, "dnsResult.dnsData.TXT")
        if not isinstance(txt, list):
            return ret
        for record in txt:
            record_norm = record.lower()
            if "v=spf" in record_norm:
                ret["spf"] = True
            if "v=dkim" in record_norm:
                ret["dkim"] = True
            if "v=dmarc" in record_norm:
                ret["dmarc"] = True

        return ret

    @staticmethod
    def _make_soa(data: dict) -> (dict | None, dict | None):
        """
        Extracts and formats the SOA (Start of Authority) record from the DNS data.

        This method takes a dictionary of raw data as input and extracts the SOA record. The method returns a tuple of
        two dictionaries. The first dictionary contains the SOA record for the domain name, and the second dictionary
        contains the SOA record for the zone. Only one of these is always present in the data (if the input domain
        name is also the zone, only the first dictionary is present; otherwise, only the second dictionary is present).

        Args:
            data (dict): A dictionary containing the raw data collected by the DomainRadar collector.

        Returns:
            tuple: A tuple containing two dictionaries representing the SOA records for the domain name and the zone.
            If the SOA record is not present, None is returned.
        """
        soa = get_safe(data, "zone.soa")
        if soa is None:
            return None, None

        soa = {
            "primary_ns": soa["primaryNS"],
            "resp_mailbox_dname": soa["respMailboxDname"],
            "serial": soa["serial"],
            "refresh": soa["refresh"],
            "retry": soa["retry"],
            "expire": soa["expire"],
            "min_ttl": soa["minTTL"]
        }

        dn = data["domain_name"]
        if dn == get_safe(data, "zone.zone"):
            return soa, None
        else:
            return None, soa

    @staticmethod
    def _make_ns(dns_data: dict) -> list | None:
        ns = dns_data.get("NS")
        if ns is None:
            return None

        return [x["nameserver"] for x in ns]

    @staticmethod
    def _make_mx(dns_data: dict) -> list | None:
        mx = dns_data.get("MX")
        if mx is None:
            return None

        return [{"name": x["value"], "priority": x["priority"]} for x in mx]

    @staticmethod
    def _make_tls(data: dict) -> dict | None:
        """
        Extracts and formats the TLS handshake and certificate data from the raw data.

        This method takes a dictionary of raw data as input and extracts the TLS data. The TLS data includes information
        about the cipher, protocol, count of certificates, and the certificates themselves. The certificates are
        represented directly as cryptography's Certificate or as a string in case of parse errors.

        Args:
            data (dict): A dictionary containing the raw data collected by the DomainRadar collector.

        Returns:
            dict | None: A dictionary containing the TLS data. If the TLS data is not present, None is returned.
        """
        def make_certificate(cert_data: dict) -> Certificate | str:
            data_b64: str = cert_data.get("derData", "")
            dn: str = cert_data.get("dn", "")

            if len(data_b64) == 0:
                return dn

            # noinspection PyBroadException
            try:
                data_der = base64.b64decode(data_b64)
                cert = cryptography.x509.load_der_x509_certificate(data_der)
            except Exception:
                return dn

            return cert

        tls = get_safe(data, "tlsResult.tlsData")
        if tls is None:
            return None

        certs = tls["certificates"]
        return {
            "cipher": tls["cipher"],
            "protocol": tls["protocol"],
            "count": len(certs),
            "certificates": [make_certificate(x) for x in certs]
        }

    @staticmethod
    def _parse_rdap_backup(rdap_data: dict):
        # TODO: custom parsing of the key elements from RDAP responses for the responses
        #       where the parser fails
        return {}

    @staticmethod
    def _parse_whois(dn: str, whois_parsed: dict, whois_raw: str):
        """
        Parses WHOIS data to the RDAP-like representation expected by the transformations.

        This method takes a domain name, parsed WHOIS data, and raw WHOIS data as input. It attempts to parse the WHOIS
        data into the format returned by `_parse_rdap_dn`. The RDAP format includes entities, registration date,
        expiration date, and last changed date. If the WHOIS data is not available or parsing fails,
        it returns an empty dictionary.

        Args:
            dn (str): The domain name.
            whois_parsed (dict): The parsed WHOIS data.
            whois_raw (str): The raw WHOIS data.

        Returns:
            dict: A dictionary containing the parsed WHOIS data in the format expected by the transformations.
        """
        def adjust_format(in_date, last):
            if in_date is None:
                return None
            if isinstance(in_date, list):
                if len(in_date) == 0:
                    return None
                else:
                    return in_date[-1 if last else 0]
            if isinstance(in_date, str):
                try:
                    return datetime.fromisoformat(in_date)
                except ValueError:
                    return None

        reg_date = exp_date = last_changed_date = None

        if whois_parsed is not None:
            reg_date = whois_parsed.get("created_date", whois_parsed.get("created", None))
            exp_date = whois_parsed.get("expires_date", whois_parsed.get("expires", None))
            last_changed_date = whois_parsed.get("updated_date", whois_parsed.get("updated", None))

        has_all = reg_date is not None and exp_date is not None and last_changed_date is not None
        if not has_all and whois_raw:
            # noinspection PyBroadException
            try:
                entry = WhoisEntry.load(dn, whois_raw)

                if not reg_date:
                    reg_date = entry.get("creation_date", None)
                if not exp_date:
                    exp_date = entry.get("expiration_date", None)
                if not last_changed_date:
                    last_changed_date = entry.get("updated_date", None)
            except Exception:
                pass

        reg_date = adjust_format(reg_date, False)
        exp_date = adjust_format(exp_date, True)
        last_changed_date = adjust_format(last_changed_date, True)

        return {
            "entities": [],  # TODO: WHOIS entities
            "registration_date": reg_date,
            "expiration_date": exp_date,
            "last_changed_date": last_changed_date
        }

    def _parse_rdap_dn(self, dn: str, rdap_data: dict | None, rdap_entities: list | None,
                       whois_parsed: dict | None, whois_raw: str | None) -> dict:
        """
        Parses RDAP and WHOIS data into a unified format expected by the transformations.

        This method takes a domain name, RDAP data, RDAP entities, parsed WHOIS data, and raw WHOIS data as input.
        It attempts to parse the RDAP and WHOIS data into a unified format that includes entities, registration date,
        expiration date, and last changed date. If the RDAP or WHOIS data is not available or parsing fails,
        it returns an empty dictionary.

        Args:
            dn (str): The domain name.
            rdap_data (dict | None): The RDAP data.
            rdap_entities (list | None): The RDAP entities.
            whois_parsed (dict | None): The parsed WHOIS data.
            whois_raw (str | None): The raw WHOIS data.

        Returns:
            dict: A dictionary containing the parsed RDAP and WHOIS data in a unified format.
        """
        if rdap_data is not None:
            if rdap_entities is not None:
                for entity in rdap_entities:
                    # Some RDAP servers return (incorrectly) roles as objects
                    # Convert to lists of arrays, as per the RDAP spec (RFC 7483, Sec. 5.1)
                    if "roles" in entity and isinstance(entity["roles"], list):
                        new_roles = []
                        for role in entity["roles"]:
                            if isinstance(role, dict) and "value" in role:
                                new_roles.append(role["value"])
                            else:
                                new_roles.append(role)
                        entity["roles"] = new_roles
                    # Some RDAP servers don't encapsulate a single link in a list
                    if "links" in entity and isinstance(entity["links"], dict):
                        entity["links"] = [entity["links"]]

                # Certain RDAP responses have "EventAction" and "EventDate" instead of "eventAction" and "eventDate"
                if "events" in rdap_data and isinstance(rdap_data["events"], list):
                    for event in rdap_data["events"]:
                        if isinstance(event, dict) and "EventAction" in event:
                            event["eventAction"] = event["EventAction"]
                            del event["EventAction"]
                            event["eventDate"] = event.get("EventDate", event.get("eventDate", None))
                            del event["EventDate"]

            if "events" in rdap_data:
                # Fix the event date and action keys having invalid casing in some responses
                for event in rdap_data["events"]:
                    if "eventdate" in event:
                        event["eventDate"] = event["eventdate"]
                    if "eventaction" in event:
                        event["eventAction"] = event["eventaction"]

            rdap_data["entities"] = rdap_entities or []
            # noinspection PyBroadException
            try:
                # The parser actually does some parsing in the constructor
                rdap_parser = ParseDomain(self.whoisit_bootstrap, rdap_data, dn, True)
                return rdap_parser.parse()
            except (ParseError, AttributeError, KeyError):
                # Some responses are not spec-compliant, leading to weird errors
                return self._parse_rdap_backup(rdap_data)
        elif whois_parsed is not None or whois_raw is not None:
            return self._parse_whois(dn, whois_parsed, whois_raw or "")
        else:
            return {}

    @staticmethod
    def _make_rdap_dnssec(rdap_data: dict | None) -> bool | None:
        if rdap_data is None:
            return None

        return rdap_data.get('secureDNS', {}).get('delegationSigned', None)

    @staticmethod
    def _make_ip_average_rtt(results_for_ip: dict) -> float:
        """
        Calculates the average round-trip time (RTT) for the IP addresses related to a domain name.

        If an IP result does not have the 'average_rtt' key or if the key's value is not a number, the result is not
        included in the calculation. If none of the IP results have the 'average_rtt' key, the method returns 0.0.

        Args:
            results_for_ip (dict): A dictionary containing the results from the IP-based collectors.

        Returns:
            float: The average round-trip time (RTT) for the IP address.
        """
        count = 0
        total = 0
        for collector, results in results_for_ip.items():
            if collector.startswith("rtt") and results["statusCode"] == 0:
                data = results.get("data", {})
                col_count = data.get("received", 0)
                count += col_count
                total += data.get("avg", 0) * col_count

        return total / count if count > 0 else 0.0

    def _make_ip_data(self, data: dict) -> list[dict]:
        """
        Extracts and formats the IP data from the raw data.

        This method takes a dictionary of raw data as input and extracts the IP data. The IP data includes information
        about the IP address, the record from which it was obtained, ASN details, RDAP details, geolocation details,
        average round-trip time (RTT), and NERD reputation.

        The method returns a list of dictionaries, each representing an IP address and its associated data. If the IP
        data is not present in the raw data, an empty list is returned.

        Args:
            data (dict): A dictionary containing the raw data collected by the DomainRadar collector.

        Returns:
            list[dict]: A list of dictionaries representing the IP data. Each dictionary contains the IP address and its
            associated data, including ASN details, RDAP details, geolocation details, average RTT, and NERD reputation.
        """
        def t_ip_ver(ver: str | None):
            return "6" if ver == "v6" else ("4" if ver == "v4" else ver)

        ips = get_safe(data, "dnsResult.ips") or []
        ip_results = get_safe(data, "ipResults") or {}
        ret = []
        for ip_obj in ips:
            ip = ip_obj["ip"]
            collectors_results = ip_results.get(ip, {})
            # the 'data' properties may be null if the collectors failed
            geo_asn = get_safe(collectors_results, "geo-asn.data") or {}
            rdap = get_safe(collectors_results, "rdap-ip.data") or {}
            rdap_parsed = {}
            if len(rdap) != 0:
                rdap_parser = ParseIPNetwork(self.whoisit_bootstrap, rdap, ip, True)
                try:
                    rdap_parsed = rdap_parser.parse()
                except ParseError:
                    pass

            network_address: str | None = geo_asn.get("networkAddress", None)
            if network_address is not None and len(network_address) > 0 and network_address[0] == '/':
                network_address = network_address[1:]

            new_ip = {
                "ip": ip,
                "from_record": ip_obj["rrType"],
                "asn": {
                    "asn": geo_asn.get("asn", None),
                    "as_org": geo_asn.get("asnOrg", None),
                    "network_address": network_address,
                    "prefix_len": geo_asn.get("prefixLength", None)
                } if "asn" in geo_asn else None,
                "rdap": {
                    "ip_version": t_ip_ver(rdap.get("ipVersion", None)),
                    "entities": rdap_parsed.get("entities", None),
                    "network": rdap_parsed.get("network", None)
                } if ("ipVersion" in rdap) or ("entities" in rdap_parsed) else None,
                "geo": {
                    "country_code": geo_asn.get("countryCode", None),
                    "latitude": geo_asn.get("latitude", None),
                    "longitude": geo_asn.get("longitude", None)
                } if "countryCode" in geo_asn else None,
                "average_rtt": self._make_ip_average_rtt(collectors_results),
                "nerd_rep": get_safe(collectors_results, "nerd.data.reputation") or -1
            }

            ret.append(new_ip)

        return ret
