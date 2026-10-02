"""Conservative recursive redaction, including identity-bearing dictionary keys."""

import re
from typing import Any

SENSITIVE = {
    "password",
    "pwd",
    "token",
    "stok",
    "nonce",
    "username",
    "ssid",
    "key",
    "url",
    "hostname",
    "ipv4",
    "ip",
    "mac",
    "name",
    "bound_ip",
    "bound_name",
    "routerid",
    "gateway",
    "device_name",
    "device_mac_address",
    "sn",
    "device_hw_version",
    "serial_number",
}
IDENTITY = re.compile(
    r"^(?:[0-9a-f]{2}(?::[0-9a-f]{2}){5}|(?:\d{1,3}\.){3}\d{1,3})$", re.I
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            safe_key = (
                f"redacted_client_{index}" if IDENTITY.fullmatch(str(key)) else key
            )
            lowered = str(key).lower()
            result[safe_key] = (
                "**REDACTED**"
                if (
                    lowered in SENSITIVE
                    or any(
                        part in lowered
                        for part in ("password", "token", "ssid", "secret")
                    )
                )
                else redact(item)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str) and (
        "stok=" in value
        or IDENTITY.fullmatch(value)
        or value.startswith(("http://", "https://"))
    ):
        return "**REDACTED**"
    return value
