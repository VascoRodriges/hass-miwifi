"""Services."""

from __future__ import annotations

import hashlib
import logging
import re
from copy import deepcopy
from ipaddress import IPv4Address
from typing import Final

import homeassistant.components.persistent_notification as pn
import voluptuous as vol
from homeassistant.const import CONF_DEVICE_ID, CONF_IP_ADDRESS, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.core import ServiceCall

from .const import (
    ATTR_DEVICE_HW_VERSION,
    ATTR_DEVICE_MAC_ADDRESS,
    ATTR_PORT_FORWARD,
    ATTR_TRACKER_MAC_BOUND,
    ATTR_TRACKER_WAN,
    CONF_BODY,
    CONF_REQUEST,
    CONF_RESPONSE,
    CONF_URI,
    DEFAULT_CALL_DELAY,
    EVENT_LUCI,
    EVENT_TYPE_RESPONSE,
    NAME,
    SERVICE_ADD_MESH_NODE,
    SERVICE_MAC_BIND,
    SERVICE_MAC_UNBIND,
    SERVICE_MACBIND_INFO,
    SERVICE_ADD_PORT_FORWARD,
    SERVICE_CALC_PASSWD,
    SERVICE_CLEANUP_STALE_CLIENTS,
    SERVICE_DELETE_PORT_FORWARD,
    SERVICE_PORT_FORWARD_LIST,
    SERVICE_SET_BAND,
    SERVICE_QOS_INFO,
    SERVICE_REQUEST,
    SERVICE_SET_MAC_FILTER,
    SERVICE_SET_MAC_TIME,
    SERVICE_SET_QOS,
    SERVICE_SET_WIFI_MACFILTER,
    SERVICE_SET_WIFI_TIMER,
    SERVICE_WIFI_TIMER_INFO,
)
from .api_map import MIWIFI_API_MAP
from .client_identity import (
    binding_attributes,
    binding_snapshot,
    client_mac,
    client_status,
    validate_binding_target,
    wan_authority,
)
from .exceptions import LuciError
from .operations import OperationError, manager_for
from .readback import bandwidth, port_rules, rule_matches
from .updater import LuciUpdater, async_get_updater

_LOGGER = logging.getLogger(__name__)


class MiWifiServiceCall:
    """Parent class for all MiWifi service calls."""

    schema = vol.Schema(
        {
            vol.Required(CONF_DEVICE_ID): vol.All(
                lambda value: [value] if isinstance(value, str) else value,
                list,
                vol.Length(
                    min=1, max=1, msg="The service only supports one device per call."
                ),
            )
        }
    )

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize service call.

        :param hass: HomeAssistant
        """

        self.hass = hass

    def get_updater(self, service: ServiceCall) -> LuciUpdater:
        """Get updater.

        :param service: ServiceCall
        :return LuciUpdater
        """

        device_id: str = dict(service.data).pop(CONF_DEVICE_ID)[0]

        device: dr.DeviceEntry | None = dr.async_get(self.hass).async_get(device_id)

        if device is None:
            raise vol.Invalid(f"Device {device_id} not found.")

        for connection_type, identifier in device.connections:
            if connection_type == CONF_IP_ADDRESS and len(identifier) > 0:
                try:
                    updater = async_get_updater(self.hass, identifier)
                except ValueError as err:
                    raise vol.Invalid("Selected router is not loaded") from err
                router_mac = updater.data.get(ATTR_DEVICE_MAC_ADDRESS)
                if router_mac and not any(
                    kind == dr.CONNECTION_NETWORK_MAC
                    and str(value).upper() == str(router_mac).upper()
                    for kind, value in device.connections
                ):
                    raise vol.Invalid("Choose the MiWiFi router, not a tracked client")
                return updater

        raise vol.Invalid(
            f"Device {device_id} does not support the called service. Choose a router with MiWifi support."  # pylint: disable=line-too-long
        )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call.

        :param service: ServiceCall
        """

        raise NotImplementedError  # pragma: no cover

    def _set_optimistic_mac_bound(
        self, updater: LuciUpdater, mac: str, is_bound: bool
    ) -> None:
        """Apply optimistic MAC binding state locally.

        :param updater: LuciUpdater
        :param mac: str
        :param is_bound: bool
        """

        self._set_optimistic_tracker_value(
            updater, mac, ATTR_TRACKER_MAC_BOUND, is_bound
        )

    def _publish_optimistic_update(self, updater: LuciUpdater) -> None:
        """Publish coordinator data without dropping tracker connectivity.

        :param updater: LuciUpdater
        """

        for key, record in manager_for(updater).records.items():
            if ":" in key:
                _, mac = key.split(":", 1)
                if mac in updater.devices:
                    updater.devices[mac].update(
                        {
                            "operation_status": record.status,
                            "operation_id": record.request_id,
                            "pending": record.status == "pending",
                        }
                    )
        updater._is_optimistic_tracker_update = True
        try:
            updater.async_set_updated_data(dict(updater.data))
        finally:
            updater._is_optimistic_tracker_update = False

    def _set_optimistic_tracker_value(
        self, updater: LuciUpdater, mac: str, attr: str, value: object
    ) -> None:
        """Apply optimistic device_tracker attribute update locally.

        :param updater: LuciUpdater
        :param mac: str
        :param attr: str
        :param value: object
        """

        device = updater.devices.get(mac.upper())
        if device is None:
            device = updater.devices.get(mac)

        if device is None:
            return

        device[attr] = value
        self._publish_optimistic_update(updater)

    def _schedule_router_refresh(
        self, updater: LuciUpdater, delay: float = DEFAULT_CALL_DELAY
    ) -> None:
        """Schedule delayed refresh from router after optimistic update.

        :param updater: LuciUpdater
        :param delay: float
        """

        updater.schedule_followup_refresh(delay)

    async def _verified(
        self,
        updater,
        key,
        desired,
        command,
        read,
        apply,
        *,
        matches=None,
        optimistic=None,
        preflight=None,
    ):
        try:
            return await manager_for(updater).execute(
                key,
                desired,
                command,
                read,
                apply,
                lambda: self._publish_optimistic_update(updater),
                matches=matches,
                optimistic=optimistic,
                preflight=preflight,
            )
        except OperationError as err:
            raise vol.Invalid(str(err)) from err
        finally:
            self._schedule_router_refresh(updater)

    async def _acknowledged(self, updater, command) -> dict:
        """Experimental APIs without a known read-back never return verified."""
        try:
            async with manager_for(updater).lock:
                await command()
            _LOGGER.warning(
                "Router acknowledged an experimental action; applied state is not verified"
            )
            return {"acknowledged": True, "verified": False}
        except (LuciError, ValueError) as err:
            raise vol.Invalid(
                "Router acknowledgement unavailable; command was not retried"
            ) from err
        finally:
            self._schedule_router_refresh(updater)


class MiWifiCalcPasswdServiceCall(MiWifiServiceCall):
    """Calculate passwd."""

    salt_old: str = "A2E371B0-B34B-48A5-8C40-A7133F3B5D88"
    salt_new: str = "6d2df50a-250f-4a30-a5e6-d44fb0960aa0"

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call.

        :param service: ServiceCall
        """

        _updater: LuciUpdater = self.get_updater(service)

        if hw_version := _updater.data.get(ATTR_DEVICE_HW_VERSION):
            _salt: str = hw_version + (
                self.salt_new if "/" in hw_version else self.salt_old
            )

            return pn.async_create(
                self.hass,
                f"Your passwd: {hashlib.md5(_salt.encode()).hexdigest()[:8]}",
                NAME,
            )

        raise vol.Invalid(
            f"Integration with ip address: {_updater.ip} does not support this service."
        )


class MiWifiRequestServiceCall(MiWifiServiceCall):
    """Send request."""

    schema = MiWifiServiceCall.schema.extend(
        {vol.Required(CONF_URI): str, vol.Optional(CONF_BODY): dict}
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call.

        :param service: ServiceCall
        """

        updater: LuciUpdater = self.get_updater(service)
        device_identifier: str = updater.data.get(ATTR_DEVICE_MAC_ADDRESS, updater.ip)

        _data: dict = dict(service.data)

        try:
            # The raw request action is read-only. It must not bypass validation,
            # serialization or reconciliation of the dedicated control actions.
            uri = _data.get(CONF_URI, "")
            body = _data.get(CONF_BODY, {})
            readonly = {
                schema["endpoint"]
                for name, schema in MIWIFI_API_MAP.items()
                if schema["method"] == "GET"
                and name
                not in {
                    "set_wifi",
                    "set_guest_wifi",
                    "rom_upgrade",
                    "reboot",
                    "flash_permission",
                }
            }
            if uri not in readonly or (uri == "misystem/led" and body):
                raise vol.Invalid("Use a dedicated action for router changes")
            response: dict = await updater.luci.get(uri, body)
        except LuciError as err:
            raise vol.Invalid("Router read failed") from err

        device: dr.DeviceEntry | None = dr.async_get(self.hass).async_get_device(
            set(),
            {(dr.CONNECTION_NETWORK_MAC, device_identifier)},
        )

        if device is not None:
            self.hass.bus.async_fire(
                EVENT_LUCI,
                {
                    CONF_DEVICE_ID: device.id,
                    CONF_TYPE: EVENT_TYPE_RESPONSE,
                    CONF_URI: uri,
                    CONF_REQUEST: body,
                    CONF_RESPONSE: response,
                },
            )


CONF_MAC: Final = "mac"
CONF_WAN: Final = "wan"


class MiWifiSetMacFilterServiceCall(MiWifiServiceCall):
    """Set MAC filter (block/unblock device)."""

    supports_response = True

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_MAC): client_mac,
            vol.Optional(CONF_WAN, default=0): vol.All(vol.Coerce(int), vol.In([0, 1])),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> dict:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)

        _data: dict = dict(service.data)
        mac: str = _data.get(CONF_MAC, "")
        wan: int = int(_data.get(CONF_WAN, 0))

        changed = False

        async def read():
            return wan_authority(await updater.luci.device_list(), mac)

        def preflight(current):
            nonlocal changed
            changed = current != wan

        def apply(actual):
            if mac in updater.devices:
                updater.devices[mac][ATTR_TRACKER_WAN] = actual

        await self._verified(
            updater,
            f"wan:{mac}",
            wan,
            lambda: updater.luci.set_mac_filter(mac, wan),
            read,
            apply,
            optimistic=lambda: apply(wan),
            preflight=preflight,
        )
        return {"mac": mac, "wan": wan, "verified": True, "changed": changed}


class MiWifiClientStatusServiceCall(MiWifiServiceCall):
    """Read one exact client's current status without changing router settings."""

    supports_response = True
    schema = MiWifiServiceCall.schema.extend({vol.Required(CONF_MAC): client_mac})

    async def async_call_service(self, service: ServiceCall) -> dict:
        updater = self.get_updater(service)
        try:
            return client_status(
                await updater.luci.device_list(),
                await updater.luci.macbind_info(),
                service.data[CONF_MAC],
            )
        except (LuciError, ValueError) as err:
            raise vol.Invalid("Client status cannot be verified") from err


CONF_PROTO: Final = "proto"
CONF_FWD_TYPE: Final = "fwd_type"
CONF_FWD_IP: Final = "fwd_ip"
CONF_FWD_PORT: Final = "fwd_port"
CONF_EXT_PORT: Final = "ext_port"
CONF_NAME: Final = "name"
CONF_UPLOAD: Final = "upload"
CONF_DOWNLOAD: Final = "download"
CONF_START_TIME: Final = "start_time"
CONF_END_TIME: Final = "end_time"
CONF_DAYS: Final = "days"
CONF_ENABLED: Final = "enabled"
CONF_LOCATE_IP: Final = "locate_ip"
CONF_MODEL: Final = "model"
CONF_IP: Final = "ip"
CONF_DAYS_OVERRIDE: Final = "days"


def schedule_time(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d", value
    ):
        raise ValueError("Expected time HH:MM")
    return value


def schedule_days(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-7](?:,[1-7])*", value):
        raise ValueError("Expected comma-separated weekdays 1..7")
    days = value.split(",")
    if len(days) != len(set(days)):
        raise ValueError("Duplicate weekday")
    return ",".join(sorted(days))


def wifi_mac_list(value):
    if value == "":
        return value
    if not isinstance(value, str):
        raise ValueError("Expected semicolon-separated MAC addresses")
    return ";".join(
        dict.fromkeys(client_mac(item.strip()) for item in value.split(";"))
    )


class _MiWifiDataServiceCall(MiWifiServiceCall):
    """Base class for data-returning services that fire EVENT_LUCI."""

    _uri: str = ""
    supports_response = True

    async def _fetch(self, updater: LuciUpdater) -> dict:
        raise NotImplementedError  # pragma: no cover

    async def async_call_service(self, service: ServiceCall) -> dict:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        device_identifier: str = updater.data.get(ATTR_DEVICE_MAC_ADDRESS, updater.ip)

        try:
            response: dict = await self._fetch(updater)
        except LuciError as err:
            raise vol.Invalid("Router read failed") from err

        device: dr.DeviceEntry | None = dr.async_get(self.hass).async_get_device(
            set(),
            {(dr.CONNECTION_NETWORK_MAC, device_identifier)},
        )

        if device is not None:
            self.hass.bus.async_fire(
                EVENT_LUCI,
                {
                    CONF_DEVICE_ID: device.id,
                    CONF_TYPE: EVENT_TYPE_RESPONSE,
                    CONF_URI: self._uri,
                    CONF_REQUEST: {},
                    CONF_RESPONSE: response,
                },
            )
        return response


class MiWifiMacbindInfoServiceCall(_MiWifiDataServiceCall):
    """Get list of MAC-bound devices."""

    _uri = MIWIFI_API_MAP["macbind_info"]["endpoint"]

    async def _fetch(self, updater: LuciUpdater) -> dict:
        return await updater.luci.macbind_info()


_PROTO_MAP = {"tcp": 1, "udp": 2, "both": 3}


class MiWifiPortForwardListServiceCall(_MiWifiDataServiceCall):
    """List port forwarding rules."""

    async def _fetch(self, updater: LuciUpdater) -> dict:
        return await updater.luci.port_forward_list()


class MiWifiAddPortForwardServiceCall(MiWifiServiceCall):
    """Add a port forwarding rule."""

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_PROTO): vol.In(["tcp", "udp", "both"]),
            vol.Required(CONF_FWD_IP): lambda value: str(IPv4Address(value)),
            vol.Required(CONF_FWD_PORT): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_EXT_PORT): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        proto_int = _PROTO_MAP.get(_data[CONF_PROTO], 1)

        desired = {
            "name": _data[CONF_NAME],
            "proto": proto_int,
            "destip": _data[CONF_FWD_IP],
            "destport": str(_data[CONF_FWD_PORT]),
            "srcport": str(_data[CONF_EXT_PORT]),
        }

        async def command():
            await updater.luci.add_port_forward(
                name=_data[CONF_NAME],
                proto=proto_int,
                fwd_type=1,
                fwd_ip=_data[CONF_FWD_IP],
                fwd_port=int(_data[CONF_FWD_PORT]),
                ext_port=int(_data[CONF_EXT_PORT]),
            )

        async def read():
            return port_rules(await updater.luci.port_forward_list())

        def matches(actual, target):
            return any(
                rule_matches(
                    rule,
                    proto=proto_int,
                    port=int(_data[CONF_EXT_PORT]),
                    ip=_data[CONF_FWD_IP],
                    dest_port=int(_data[CONF_FWD_PORT]),
                    name=_data[CONF_NAME],
                )
                for rule in actual
            )

        def preflight(actual):
            if not matches(actual, desired) and any(
                rule_matches(rule, proto=proto_int, port=int(_data[CONF_EXT_PORT]))
                for rule in actual
            ):
                raise ValueError("A different rule already uses this external port")

        await self._verified(
            updater,
            "port_forward",
            desired,
            command,
            read,
            lambda actual: updater.data.update({ATTR_PORT_FORWARD: deepcopy(actual)}),
            matches=matches,
            preflight=preflight,
            optimistic=lambda: updater.data.update(
                {ATTR_PORT_FORWARD: [*updater.data.get(ATTR_PORT_FORWARD, []), desired]}
            ),
        )


class MiWifiDeletePortForwardServiceCall(MiWifiServiceCall):
    """Delete a port forwarding rule."""

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_PROTO): vol.In(["tcp", "udp", "both"]),
            vol.Required(CONF_EXT_PORT): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        proto_int = _PROTO_MAP.get(_data[CONF_PROTO], 1)
        ext_port_to_del = int(_data[CONF_EXT_PORT])

        async def command():
            await updater.luci.delete_port_forward(
                proto=proto_int,
                ext_port=int(_data[CONF_EXT_PORT]),
            )

        async def read():
            return port_rules(await updater.luci.port_forward_list())

        def matches(actual, target):
            return not any(
                rule_matches(rule, proto=proto_int, port=ext_port_to_del)
                for rule in actual
            )

        await self._verified(
            updater,
            "port_forward",
            None,
            command,
            read,
            lambda actual: updater.data.update({ATTR_PORT_FORWARD: deepcopy(actual)}),
            matches=matches,
            optimistic=lambda: updater.data.update(
                {
                    ATTR_PORT_FORWARD: [
                        rule
                        for rule in updater.data.get(ATTR_PORT_FORWARD, [])
                        if not rule_matches(rule, proto=proto_int, port=ext_port_to_del)
                    ]
                }
            ),
        )


class MiWifiSetBandServiceCall(MiWifiServiceCall):
    """Set Global WAN Bandwidth limits."""

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_UPLOAD): vol.All(vol.Coerce(int), vol.Range(min=1)),
            vol.Required(CONF_DOWNLOAD): vol.All(vol.Coerce(int), vol.Range(min=1)),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        updater: LuciUpdater = self.get_updater(service)
        desired = (int(service.data[CONF_UPLOAD]), int(service.data[CONF_DOWNLOAD]))

        async def read():
            return bandwidth(await updater.luci.qos_info())

        def apply(actual):
            updater.data.update(
                {"qos_band_up": int(actual[0]), "qos_band_down": int(actual[1])}
            )

        await self._verified(
            updater,
            "qos_band",
            desired,
            lambda: updater.luci.set_band(upload=desired[0], download=desired[1]),
            read,
            apply,
            optimistic=lambda: apply(desired),
        )


class MiWifiQosInfoServiceCall(_MiWifiDataServiceCall):
    """Get QoS information."""

    _uri = MIWIFI_API_MAP["qos_info"]["endpoint"]

    async def _fetch(self, updater: LuciUpdater) -> dict:
        return await updater.luci.qos_info()


class MiWifiSetQosServiceCall(MiWifiServiceCall):
    """Set per-device bandwidth limit."""

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_MAC): client_mac,
            vol.Required(CONF_UPLOAD): vol.All(vol.Coerce(int), vol.Range(min=0)),
            vol.Required(CONF_DOWNLOAD): vol.All(vol.Coerce(int), vol.Range(min=0)),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        mac = _data[CONF_MAC]
        desired = (int(_data[CONF_UPLOAD]), int(_data[CONF_DOWNLOAD]))

        async def read():
            return bandwidth(await updater.luci.qos_info(), mac)

        def apply(actual):
            if mac in updater.devices:
                updater.devices[mac].update(
                    {"qos_up": int(actual[0]), "qos_down": int(actual[1])}
                )

        await self._verified(
            updater,
            f"qos:{mac}",
            desired,
            lambda: updater.luci.set_qos(
                mac=mac, upload=desired[0], download=desired[1]
            ),
            read,
            apply,
            optimistic=lambda: apply(desired),
        )


class MiWifiMacBindServiceCall(MiWifiServiceCall):
    """Bind IP to MAC address."""

    supports_response = True

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_IP): lambda value: str(IPv4Address(value)),
            vol.Required(CONF_MAC): client_mac,
            vol.Required(CONF_NAME): str,
        }
    )

    async def async_call_service(self, service: ServiceCall) -> dict:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        mac_to_bind = _data[CONF_MAC]
        ip_to_bind = _data[CONF_IP]
        name_to_bind = _data[CONF_NAME]

        original = None

        async def read():
            response = await updater.luci.macbind_info()
            binding_snapshot(response)
            return response

        def preflight(response):
            nonlocal original
            original = binding_snapshot(response)
            validate_binding_target(response, mac_to_bind, ip_to_bind)

        def matches(response, target):
            snapshot = binding_snapshot(response)
            unaffected = {
                mac: value for mac, value in snapshot.items() if mac != mac_to_bind
            }
            if original is not None and unaffected != {
                mac: value for mac, value in original.items() if mac != mac_to_bind
            }:
                raise ValueError("Unrelated DHCP reservations changed")
            return (
                snapshot.get(mac_to_bind, {}).get("bound_ip") == ip_to_bind
                and snapshot.get(mac_to_bind, {}).get("bound_name") == name_to_bind
            )

        async def command():
            await updater.luci.mac_bind(
                ip=ip_to_bind,
                mac=mac_to_bind,
                name=name_to_bind,
            )

        def apply(response):
            bindings = binding_snapshot(response)
            for device_mac, device in updater.devices.items():
                device.update(
                    binding_attributes(bindings, device_mac, device.get("ip"))
                )

        def optimistic():
            predicted = dict(original)
            predicted[mac_to_bind] = {
                "bound_ip": ip_to_bind,
                "bound_name": name_to_bind,
                "binding_source": "reservation_list",
            }
            for device_mac, device in updater.devices.items():
                device.update(
                    binding_attributes(predicted, device_mac, device.get("ip"))
                )

        await self._verified(
            updater,
            f"binding:{mac_to_bind}",
            ip_to_bind,
            command,
            read,
            apply,
            matches=matches,
            preflight=preflight,
            optimistic=optimistic,
        )
        return {"mac": mac_to_bind, "bound_ip": ip_to_bind, "verified": True}


class MiWifiMacUnbindServiceCall(MiWifiServiceCall):
    """Remove MAC binding."""

    supports_response = True

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_MAC): client_mac,
        }
    )

    async def async_call_service(self, service: ServiceCall) -> dict:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)
        mac_to_unbind = _data[CONF_MAC]

        original = None

        async def read():
            return binding_snapshot(await updater.luci.macbind_info())

        def preflight(snapshot):
            nonlocal original
            original = snapshot

        def matches(snapshot, target):
            if original is not None and {
                mac: value for mac, value in snapshot.items() if mac != mac_to_unbind
            } != {
                mac: value for mac, value in original.items() if mac != mac_to_unbind
            }:
                raise ValueError("Unrelated DHCP reservations changed")
            return mac_to_unbind not in snapshot

        def apply(bindings):
            for device_mac, device in updater.devices.items():
                device.update(
                    binding_attributes(bindings, device_mac, device.get("ip"))
                )

        await self._verified(
            updater,
            f"binding:{mac_to_unbind}",
            None,
            lambda: updater.luci.mac_unbind(mac_to_unbind),
            read,
            apply,
            matches=matches,
            preflight=preflight,
            optimistic=lambda: apply(
                {mac: value for mac, value in original.items() if mac != mac_to_unbind}
            ),
        )
        return {"mac": mac_to_unbind, "verified": True, "mac_bound": False}


class MiWifiCleanupStaleClientsServiceCall(MiWifiServiceCall):
    """Remove stale router clients from HA and integration storage."""

    supports_response = True

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Optional(CONF_DAYS_OVERRIDE): vol.All(
                vol.Coerce(int), vol.Range(min=1)
            ),
            vol.Optional("dry_run", default=True): bool,
            vol.Optional("confirm", default=False): bool,
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        days: int | None = dict(service.data).get(CONF_DAYS_OVERRIDE)

        dry_run = service.data.get("dry_run", True)
        if not dry_run and not service.data.get("confirm", False):
            raise vol.Invalid(
                "Preview cleanup first, then set dry_run=false and confirm=true"
            )
        try:
            result = await updater.async_cleanup_stale_clients(days, dry_run=dry_run)
        except (LuciError, ValueError) as err:
            raise vol.Invalid(
                "Cleanup cancelled: client and reservation state cannot be verified"
            ) from err

        pn.async_create(
            self.hass,
            (
                f"MiWiFi cleanup {'preview' if dry_run else 'completed'}: "
                f"candidates={len(result['candidates'])}, "
                f"clients={result['removed_clients']}, "
                f"devices={result['removed_devices']}, "
                f"entities={result['removed_entities']}, "
                f"days={result['days']}"
            ),
            f"{NAME}: cleanup stale clients",
        )
        return result


class MiWifiWifiTimerInfoServiceCall(_MiWifiDataServiceCall):
    """Get WiFi timer (parental control) information."""

    _uri = MIWIFI_API_MAP["wifi_timer_info"]["endpoint"]

    async def _fetch(self, updater: LuciUpdater) -> dict:
        return await updater.luci.wifi_timer_info()


class MiWifiSetWifiTimerServiceCall(MiWifiServiceCall):
    """Set global WiFi transmitter schedule."""

    supports_response = True

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_START_TIME): schedule_time,
            vol.Required(CONF_END_TIME): schedule_time,
            vol.Required(CONF_DAYS): schedule_days,
            vol.Optional(CONF_ENABLED, default=1): vol.All(
                vol.Coerce(int), vol.In([0, 1])
            ),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        async def command():
            # Probe the advertised read endpoint before submitting a schedule.
            # Unsupported firmware must not silently accept a guessed write.
            await updater.luci.wifi_timer_info()
            return await updater.luci.set_wifi_timer(
                start_time=_data[CONF_START_TIME],
                end_time=_data[CONF_END_TIME],
                days=_data[CONF_DAYS],
                enabled=int(_data.get(CONF_ENABLED, 1)),
            )

        return await self._acknowledged(updater, command)


class MiWifiSetMacTimeServiceCall(MiWifiServiceCall):
    """Set per-device internet access schedule (parental control)."""

    supports_response = True

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_MAC): client_mac,
            vol.Required(CONF_START_TIME): schedule_time,
            vol.Required(CONF_END_TIME): schedule_time,
            vol.Required(CONF_DAYS): schedule_days,
            vol.Optional(CONF_ENABLED, default=1): vol.All(
                vol.Coerce(int), vol.In([0, 1])
            ),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        async def command():
            wan_authority(await updater.luci.device_list(), _data[CONF_MAC])
            return await updater.luci.set_mac_time(
                mac=_data[CONF_MAC],
                start_time=_data[CONF_START_TIME],
                end_time=_data[CONF_END_TIME],
                days=_data[CONF_DAYS],
                enabled=int(_data.get(CONF_ENABLED, 1)),
            )

        return await self._acknowledged(updater, command)


class MiWifiAddMeshNodeServiceCall(MiWifiServiceCall):
    """Add a mesh node."""

    supports_response = True

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_LOCATE_IP): lambda value: str(IPv4Address(value)),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        return await self._acknowledged(
            updater, lambda: updater.luci.add_mesh_node(locate_ip=_data[CONF_LOCATE_IP])
        )


class MiWifiSetWifiMacfilterServiceCall(MiWifiServiceCall):
    """Set WiFi MAC filter mode and controlled device list.

    Configures the router-level WiFi MAC filter (blacklist/whitelist).
    Different from MiWifiSetMacFilterServiceCall which controls WAN access.
    """

    supports_response = True

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_MODEL): vol.All(vol.Coerce(int), vol.In([0, 1, 2])),
            vol.Required(CONF_MAC): wifi_mac_list,
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)
        model: int = int(_data.get(CONF_MODEL, 0))
        mac: str = _data.get(CONF_MAC, "")

        return await self._acknowledged(
            updater, lambda: updater.luci.set_wifi_macfilter(model, mac)
        )


class MiWifiCapabilitiesServiceCall(MiWifiServiceCall):
    """Read-only feature discovery for the frontend; no firmware assumptions."""

    supports_response = True

    async def async_call_service(self, service: ServiceCall) -> dict:
        updater = self.get_updater(service)
        features = {}
        async with manager_for(updater).lock:
            for name, reader in (
                ("wifi_schedule", updater.luci.wifi_timer_info),
                ("qos", updater.luci.qos_info),
                ("port_forward", updater.luci.port_forward_list),
                ("dhcp_reservations", updater.luci.macbind_info),
            ):
                try:
                    await reader()
                    features[name] = {"read_supported": True}
                except LuciError:
                    features[name] = {"read_supported": False}
        return {
            "features": features,
            "experimental_actions": [
                "set_wifi_timer",
                "set_mac_time",
                "add_mesh_node",
                "set_wifi_macfilter",
            ],
        }


SERVICES: Final = (
    ("get_capabilities", MiWifiCapabilitiesServiceCall),
    (SERVICE_CALC_PASSWD, MiWifiCalcPasswdServiceCall),
    (SERVICE_REQUEST, MiWifiRequestServiceCall),
    (SERVICE_SET_MAC_FILTER, MiWifiSetMacFilterServiceCall),
    ("get_client_status", MiWifiClientStatusServiceCall),
    (SERVICE_MACBIND_INFO, MiWifiMacbindInfoServiceCall),
    (SERVICE_MAC_BIND, MiWifiMacBindServiceCall),
    (SERVICE_MAC_UNBIND, MiWifiMacUnbindServiceCall),
    (SERVICE_CLEANUP_STALE_CLIENTS, MiWifiCleanupStaleClientsServiceCall),
    (SERVICE_PORT_FORWARD_LIST, MiWifiPortForwardListServiceCall),
    (SERVICE_ADD_PORT_FORWARD, MiWifiAddPortForwardServiceCall),
    (SERVICE_DELETE_PORT_FORWARD, MiWifiDeletePortForwardServiceCall),
    (SERVICE_SET_BAND, MiWifiSetBandServiceCall),
    (SERVICE_QOS_INFO, MiWifiQosInfoServiceCall),
    (SERVICE_SET_QOS, MiWifiSetQosServiceCall),
    (SERVICE_WIFI_TIMER_INFO, MiWifiWifiTimerInfoServiceCall),
    (SERVICE_SET_WIFI_TIMER, MiWifiSetWifiTimerServiceCall),
    (SERVICE_SET_MAC_TIME, MiWifiSetMacTimeServiceCall),
    (SERVICE_ADD_MESH_NODE, MiWifiAddMeshNodeServiceCall),
    (SERVICE_SET_WIFI_MACFILTER, MiWifiSetWifiMacfilterServiceCall),
)
