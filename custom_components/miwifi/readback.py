"""Strict readers for state-changing services. Missing state is never zero."""

from decimal import Decimal, InvalidOperation


def port_rules(response):
    rules = response.get("list")
    if not isinstance(rules, list) or not all(isinstance(rule, dict) for rule in rules):
        raise ValueError("Port forwarding state unavailable")
    return rules


def rule_matches(rule, *, proto, port, ip=None, dest_port=None, name=None):
    try:
        return (
            int(rule["proto"]) == proto
            and int(rule["srcport"]) == port
            and (ip is None or rule.get("destip") == ip)
            and (dest_port is None or int(rule["destport"]) == dest_port)
            and (name is None or rule.get("name") == name)
        )
    except (KeyError, ValueError, TypeError):
        raise ValueError("Unknown port forwarding rule format") from None


def bandwidth(response, mac=None):
    if mac is None:
        band = response.get("band", {})
        fields = ("upload", "download")
    else:
        entries = response.get("list")
        if not isinstance(entries, list):
            raise ValueError("QoS client state unavailable")
        entries = [
            item
            for item in entries
            if isinstance(item, dict) and str(item.get("mac", "")).upper() == mac
        ]
        if len(entries) != 1:
            raise ValueError("QoS client identity unavailable or duplicated")
        band = entries[0].get("qos", {})
        fields = ("upmax", "downmax")
    try:
        values = tuple(Decimal(str(band[field])) for field in fields)
        if any(not value.is_finite() or value < 0 for value in values):
            raise ValueError("Invalid bandwidth state")
        return values
    except (KeyError, InvalidOperation, TypeError):
        raise ValueError("Bandwidth state unavailable") from None
