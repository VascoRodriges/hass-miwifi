"""Pure client identity and authoritative DHCP/WAN readback helpers."""

from ipaddress import IPv4Address
import re


def client_mac(value: str) -> str:
    """A single unicast client MAC; never a router-wide filter list."""
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", value
    ):
        raise ValueError("Expected one colon-separated client MAC")
    result = value.upper()
    if result == "00:00:00:00:00:00" or int(result[:2], 16) & 1:
        raise ValueError("Expected a nonzero unicast client MAC")
    return result


def binding_snapshot(response: dict) -> dict[str, dict]:
    """Use the actual binding list, not current leases disguised as reservations.

    Older firmware can only provide tag=2; preserve that fact without inventing
    a reserved IP from the current address. Invalid/ambiguous data fails closed.
    """
    if not isinstance(response, dict) or response.get("code", 0) != 0:
        raise ValueError("Binding snapshot unavailable")
    explicit = "list" in response
    rows = response.get("list") if explicit else response.get("devicelist")
    if not isinstance(rows, list):
        raise ValueError("Binding snapshot has no valid list")
    result = {}
    ips = {}
    for row in rows:
        if not explicit:
            if not isinstance(row, dict) or str(row.get("tag")) != "2":
                continue
        if isinstance(row, str) and explicit:
            row = {"mac": row}
        if not isinstance(row, dict):
            raise ValueError("Malformed binding row")
        mac = client_mac(row.get("mac"))
        ip = str(IPv4Address(row["ip"])) if explicit and row.get("ip") else None
        item = {
            "bound_ip": ip,
            "bound_name": row.get("name"),
            "binding_source": "reservation_list" if explicit else "tag_only",
        }
        if mac in result and result[mac] != item:
            raise ValueError("Conflicting reservations for one MAC")
        if ip and ip in ips and ips[ip] != mac:
            raise ValueError("Reserved IP belongs to multiple MACs")
        result[mac] = item
        if ip:
            ips[ip] = mac
    return result


def binding_attributes(bindings: dict | None, mac: str, current_ip: str | None) -> dict:
    """Keep current and reserved addresses separate; unknown is not false."""
    if bindings is None:
        return {
            "mac_bound": None,
            "bound_ip": None,
            "bound_name": None,
            "binding_source": "unknown",
            "binding_ip_matches": None,
        }
    item = bindings.get(mac.upper())
    if item is None:
        return {
            "mac_bound": False,
            "bound_ip": None,
            "bound_name": None,
            "binding_source": "reservation_list",
            "binding_ip_matches": None,
        }
    return {
        "mac_bound": True,
        **item,
        "binding_ip_matches": current_ip == item["bound_ip"]
        if current_ip and item["bound_ip"]
        else None,
    }


def wan_authority(response: dict, mac: str) -> int:
    """Read router authority, never optimistic HA state or internet reachability."""
    if not isinstance(response, dict) or response.get("code", 0) != 0:
        raise ValueError("Router client snapshot unavailable")
    rows = response.get("list")
    if not isinstance(rows, list):
        raise ValueError("Router client snapshot has no list")
    mac = client_mac(mac)
    values = []
    for row in rows:
        if isinstance(row, dict) and str(row.get("mac", "")).upper() == mac:
            authority = row.get("authority")
            value = authority.get("wan") if isinstance(authority, dict) else None
            if isinstance(value, bool) or value not in (0, 1, "0", "1"):
                raise ValueError("Client WAN authority unknown")
            values.append(int(value))
    if len(values) != 1:
        raise ValueError("Client absent or ambiguous in router snapshot")
    return values[0]


def validate_binding_target(response: dict, mac: str, ip: str) -> None:
    """Reject another MAC's reserved OR currently-used address before writing."""
    mac, ip = client_mac(mac), str(IPv4Address(ip))
    if "list" not in response:
        raise ValueError("An explicit reservation list is required before writing")
    bindings = binding_snapshot(response)
    for other_mac, item in bindings.items():
        if item["bound_ip"] == ip and other_mac != mac:
            raise ValueError("IP already reserved for another MAC")
    rows = response.get("devicelist", [])
    if not isinstance(rows, list):
        raise ValueError("Malformed current client list")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed current client row")
        if str(row.get("ip", "")) == ip and str(row.get("mac", "")).upper() != mac:
            raise ValueError("IP currently used by another MAC")


def client_status(response: dict, reservations: dict, mac: str) -> dict:
    """Selected client's fresh authority and addresses, with no optimistic data."""
    mac = client_mac(mac)
    wan = wan_authority(response, mac)
    row = next(
        item for item in response["list"] if str(item.get("mac", "")).upper() == mac
    )
    addresses = row.get("ip")
    if isinstance(addresses, list):
        addresses = next(
            (
                item.get("ip")
                for item in addresses
                if isinstance(item, dict) and item.get("ip")
            ),
            None,
        )
    ip = str(IPv4Address(addresses)) if addresses else None
    online = row.get("online")
    online = (
        bool(int(online))
        if not isinstance(online, bool) and online in (0, 1, "0", "1")
        else None
    )
    return {
        "mac": mac,
        "wan": wan,
        "online": online,
        "ip": ip,
        **binding_attributes(binding_snapshot(reservations), mac, ip),
        "verified": True,
    }
