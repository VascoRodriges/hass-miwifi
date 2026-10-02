"""Services."""

from __future__ import annotations

import asyncio
import hashlib
import logging
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
    CONF_CLEANUP_STALE_CLIENTS,
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
from .updater import LuciUpdater, async_get_updater

_LOGGER = logging.getLogger(__name__)


class MiWifiServiceCall:
    """Parent class for all MiWifi service calls."""

    schema = vol.Schema(
        {
            vol.Required(CONF_DEVICE_ID): vol.All(
                vol.Coerce(list),
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
                return async_get_updater(self.hass, identifier)

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

        async def _delayed_refresh() -> None:
            await asyncio.sleep(delay)
            await updater.async_request_refresh()

        self.hass.async_create_task(_delayed_refresh())


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
            response: dict = await updater.luci.get(
                uri := _data.get(CONF_URI), body := _data.get(CONF_BODY, {})  # type: ignore
            )
        except LuciError:
            return

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

        try:
            # Verify identity against router data BEFORE writing, not a stale tracker.
            current = wan_authority(await updater.luci.device_list(), mac)
            changed = current != wan
            if changed:
                await updater.luci.set_mac_filter(mac, wan)
                for attempt in range(3):
                    actual = wan_authority(await updater.luci.device_list(), mac)
                    if actual == wan:
                        break
                    if attempt < 2:
                        await asyncio.sleep(1)
                if actual != wan:
                    raise ValueError("Router did not confirm requested WAN authority")
            self._set_optimistic_tracker_value(updater, mac, ATTR_TRACKER_WAN, wan)
            return {"mac": mac, "wan": wan, "verified": True, "changed": changed}
        except (LuciError, ValueError) as _e:
            raise vol.Invalid(
                f"Failed to set MAC filter for {mac}: {_e}"
            ) from _e
        finally:
            self._schedule_router_refresh(updater)


class MiWifiClientStatusServiceCall(MiWifiServiceCall):
    """Read one exact client's current status without changing router settings."""

    supports_response = True
    schema = MiWifiServiceCall.schema.extend({vol.Required(CONF_MAC): client_mac})

    async def async_call_service(self, service: ServiceCall) -> dict:
        updater = self.get_updater(service)
        try:
            return client_status(await updater.luci.device_list(),
                                 await updater.luci.macbind_info(), service.data[CONF_MAC])
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
            vol.Required(CONF_FWD_IP): str,
            vol.Required(CONF_FWD_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(CONF_EXT_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        proto_int = _PROTO_MAP.get(_data[CONF_PROTO], 1)

        try:
            await updater.luci.add_port_forward(
                name=_data[CONF_NAME],
                proto=proto_int,
                fwd_type=1,
                fwd_ip=_data[CONF_FWD_IP],
                fwd_port=int(_data[CONF_FWD_PORT]),
                ext_port=int(_data[CONF_EXT_PORT]),
            )

            if ATTR_PORT_FORWARD not in updater.data:
                updater.data[ATTR_PORT_FORWARD] = []

            updater.data[ATTR_PORT_FORWARD].append({
                "name": _data[CONF_NAME],
                "proto": proto_int,
                "destip": _data[CONF_FWD_IP],
                "destport": str(_data[CONF_FWD_PORT]),
                "srcport": str(_data[CONF_EXT_PORT]),
            })

            self._publish_optimistic_update(updater)

        except LuciError as _e:
            raise vol.Invalid(
                f"Failed to add port forward rule: {_e}"
            ) from _e


class MiWifiDeletePortForwardServiceCall(MiWifiServiceCall):
    """Delete a port forwarding rule."""

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_PROTO): vol.In(["tcp", "udp", "both"]),
            vol.Required(CONF_EXT_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        proto_int = _PROTO_MAP.get(_data[CONF_PROTO], 1)
        ext_port_to_del = int(_data[CONF_EXT_PORT])

        try:
            await updater.luci.delete_port_forward(
                proto=proto_int,
                ext_port=int(_data[CONF_EXT_PORT]),
            )

            if ATTR_PORT_FORWARD in updater.data:
                updater.data[ATTR_PORT_FORWARD] = [
                    rule for rule in updater.data[ATTR_PORT_FORWARD]
                    if not (int(rule.get("srcport", 0)) == ext_port_to_del and int(rule.get("proto", 1)) == proto_int)
                ]

            self._publish_optimistic_update(updater)

        except LuciError as _e:
            raise vol.Invalid(
                f"Failed to delete port forward rule: {_e}"
            ) from _e


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
        try:
            await updater.luci.set_band(
                upload=int(service.data[CONF_UPLOAD]),
                download=int(service.data[CONF_DOWNLOAD]),
            )
        except LuciError as _e:
            raise vol.Invalid(f"Failed to set global bandwidth: {_e}") from _e

class MiWifiQosInfoServiceCall(_MiWifiDataServiceCall):
    """Get QoS information."""

    _uri = MIWIFI_API_MAP["qos_info"]["endpoint"]

    async def _fetch(self, updater: LuciUpdater) -> dict:
        return await updater.luci.qos_info()


class MiWifiSetQosServiceCall(MiWifiServiceCall):
    """Set per-device bandwidth limit."""

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_MAC): str,
            vol.Required(CONF_UPLOAD): vol.All(vol.Coerce(int), vol.Range(min=0)),
            vol.Required(CONF_DOWNLOAD): vol.All(vol.Coerce(int), vol.Range(min=0)),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        try:
            await updater.luci.set_qos(
                mac=_data[CONF_MAC],
                upload=int(_data[CONF_UPLOAD]),
                download=int(_data[CONF_DOWNLOAD]),
            )
        except LuciError as _e:
            raise vol.Invalid(
                f"Failed to set QoS for {_data[CONF_MAC]}: {_e}"
            ) from _e


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

        try:
            original_response = await updater.luci.macbind_info()
            validate_binding_target(original_response, mac_to_bind, ip_to_bind)
            original = binding_snapshot(original_response)
            await updater.luci.mac_bind(
                ip=ip_to_bind,
                mac=mac_to_bind,
                name=name_to_bind,
            )
            bindings = binding_snapshot(await updater.luci.macbind_info())
            if {mac: value for mac, value in original.items() if mac != mac_to_bind} != {
                mac: value for mac, value in bindings.items() if mac != mac_to_bind
            }:
                raise ValueError("Unrelated reservations changed; inspect the router before further writes")
            if bindings.get(mac_to_bind, {}).get('bound_ip') != ip_to_bind:
                raise ValueError("Router did not confirm the reservation")
            for device_mac, device in updater.devices.items():
                device.update(binding_attributes(bindings, device_mac, device.get('ip')))
            self._publish_optimistic_update(updater)
            return {"mac": mac_to_bind, "bound_ip": ip_to_bind, "verified": True}
        except (LuciError, ValueError) as _e:
            raise vol.Invalid(f"MAC binding not confirmed for {mac_to_bind}: {_e}") from _e
        finally:
            self._schedule_router_refresh(updater)


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

        try:
            original = binding_snapshot(await updater.luci.macbind_info())
            await updater.luci.mac_unbind(mac_to_unbind)
            bindings = binding_snapshot(await updater.luci.macbind_info())
            if {mac: value for mac, value in original.items() if mac != mac_to_unbind} != bindings:
                raise ValueError("Unrelated reservations changed or target remains; inspect the router")
            if mac_to_unbind in bindings:
                raise ValueError("Router still reports the reservation")
            for device_mac, device in updater.devices.items():
                device.update(binding_attributes(bindings, device_mac, device.get('ip')))
            self._publish_optimistic_update(updater)
            return {"mac": mac_to_unbind, "verified": True, "mac_bound": False}
        except (LuciError, ValueError) as _e:
            raise vol.Invalid(f"MAC unbinding not confirmed for {mac_to_unbind}: {_e}") from _e
        finally:
            self._schedule_router_refresh(updater)


class MiWifiCleanupStaleClientsServiceCall(MiWifiServiceCall):
    """Remove stale router clients from HA and integration storage."""

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Optional(CONF_DAYS_OVERRIDE): vol.All(vol.Coerce(int), vol.Range(min=1)),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        days: int | None = dict(service.data).get(CONF_DAYS_OVERRIDE)

        result = await updater.async_cleanup_stale_clients(days)

        pn.async_create(
            self.hass,
            (
                "Removed stale MiWiFi clients: "
                f"clients={result['removed_clients']}, "
                f"devices={result['removed_devices']}, "
                f"entities={result['removed_entities']}, "
                f"days={result['days']}"
            ),
            f"{NAME}: cleanup stale clients",
        )


class MiWifiWifiTimerInfoServiceCall(_MiWifiDataServiceCall):
    """Get WiFi timer (parental control) information."""

    _uri = MIWIFI_API_MAP["wifi_timer_info"]["endpoint"]

    async def _fetch(self, updater: LuciUpdater) -> dict:
        return await updater.luci.wifi_timer_info()


class MiWifiSetWifiTimerServiceCall(MiWifiServiceCall):
    """Set global WiFi transmitter schedule."""

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_START_TIME): str,
            vol.Required(CONF_END_TIME): str,
            vol.Required(CONF_DAYS): str,
            vol.Optional(CONF_ENABLED, default=1): vol.All(vol.Coerce(int), vol.In([0, 1])),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        try:
            await updater.luci.set_wifi_timer(
                start_time=_data[CONF_START_TIME],
                end_time=_data[CONF_END_TIME],
                days=_data[CONF_DAYS],
                enabled=int(_data.get(CONF_ENABLED, 1)),
            )
        except LuciError as _e:
            raise vol.Invalid(
                f"Failed to set WiFi timer: {_e}"
            ) from _e


class MiWifiSetMacTimeServiceCall(MiWifiServiceCall):
    """Set per-device internet access schedule (parental control)."""

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_MAC): str,
            vol.Required(CONF_START_TIME): str,
            vol.Required(CONF_END_TIME): str,
            vol.Required(CONF_DAYS): str,
            vol.Optional(CONF_ENABLED, default=1): vol.All(vol.Coerce(int), vol.In([0, 1])),
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        try:
            await updater.luci.set_mac_time(
                mac=_data[CONF_MAC],
                start_time=_data[CONF_START_TIME],
                end_time=_data[CONF_END_TIME],
                days=_data[CONF_DAYS],
                enabled=int(_data.get(CONF_ENABLED, 1)),
            )
        except LuciError as _e:
            raise vol.Invalid(
                f"Failed to set parental control for {_data[CONF_MAC]}: {_e}"
            ) from _e


class MiWifiAddMeshNodeServiceCall(MiWifiServiceCall):
    """Add a mesh node."""

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_LOCATE_IP): str,
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)

        try:
            await updater.luci.add_mesh_node(
                locate_ip=_data[CONF_LOCATE_IP],
            )
        except LuciError as _e:
            raise vol.Invalid(
                f"Failed to add mesh node: {_e}"
            ) from _e


class MiWifiSetWifiMacfilterServiceCall(MiWifiServiceCall):
    """Set WiFi MAC filter mode and controlled device list.

    Configures the router-level WiFi MAC filter (blacklist/whitelist).
    Different from MiWifiSetMacFilterServiceCall which controls WAN access.
    """

    schema = MiWifiServiceCall.schema.extend(
        {
            vol.Required(CONF_MODEL): vol.All(vol.Coerce(int), vol.In([0, 1, 2])),
            vol.Required(CONF_MAC): str,
        }
    )

    async def async_call_service(self, service: ServiceCall) -> None:
        """Execute service call."""

        updater: LuciUpdater = self.get_updater(service)
        _data: dict = dict(service.data)
        model: int = int(_data.get(CONF_MODEL, 0))
        mac: str = _data.get(CONF_MAC, "")

        try:
            await updater.luci.set_wifi_macfilter(model, mac)
        except LuciError as _e:
            raise vol.Invalid(
                f"Failed to set WiFi MAC filter: {_e}"
            ) from _e


SERVICES: Final = (
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
