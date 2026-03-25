"""Luci API Client."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import urllib.parse
import uuid
from datetime import datetime
from typing import Any

from httpx import AsyncClient, ConnectError, HTTPError, Response, TransportError

from .const import (
    CLIENT_ADDRESS,
    CLIENT_LOGIN_TYPE,
    CLIENT_NONCE_TYPE,
    CLIENT_PUBLIC_KEY,
    CLIENT_URL,
    CLIENT_USERNAME,
    DEFAULT_TIMEOUT,
    DIAGNOSTIC_CONTENT,
    DIAGNOSTIC_DATE_TIME,
    DIAGNOSTIC_MESSAGE,
)
from .enum import EncryptionAlgorithm
from .exceptions import LuciConnectionError, LuciError, LuciRequestError
from .api_map import MIWIFI_API_MAP

_LOGGER = logging.getLogger(__name__)


# pylint: disable=too-many-public-methods,too-many-arguments
class LuciClient:
    """Luci API Client."""

    ip: str = CLIENT_ADDRESS  # pylint: disable=invalid-name

    _client: AsyncClient
    _password: str | None = None
    _encryption: str = EncryptionAlgorithm.SHA1
    _timeout: int = DEFAULT_TIMEOUT

    _token: str | None = None
    _url: str

    def __init__(
        self,
        client: AsyncClient,
        ip: str = CLIENT_ADDRESS,  # pylint: disable=invalid-name
        password: str | None = None,
        encryption: str = EncryptionAlgorithm.SHA1,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize API client.

        :param client: AsyncClient: AsyncClient object
        :param ip: str: device ip address
        :param password: str: device password
        :param encryption: str: password encryption algorithm
        :param timeout: int: Query execution timeout
        """

        ip = ip.removesuffix("/")

        self._client = client
        self.ip = ip  # pylint: disable=invalid-name
        self._password = password
        self._encryption = encryption
        self._timeout = timeout

        self._url = CLIENT_URL.format(ip=ip)

        self.diagnostics: dict[str, Any] = {}

    async def _execute_api_action(self, action_name: str, **kwargs: Any) -> dict:
        """Universal API action executor based on the manifest."""
        
        schema = MIWIFI_API_MAP.get(action_name)
        if not schema:
            raise ValueError(f"Action '{action_name}' not found in API_MAP")

        endpoint = schema["endpoint"]
        method = schema["method"]
        static_params = schema.get("static_params", {})
        payload_map = schema.get("payload_map", {})
        use_stok = schema.get("use_stok", True)
        errors = schema.get("errors")
        passthrough = schema.get("passthrough", False)
        json_list_payload = schema.get("json_list_payload", False)

        if passthrough:
            payload = {k: v for k, v in kwargs.items() if v is not None}
        else:
            payload = {}
            for internal_key, mapping in payload_map.items():
                if internal_key not in kwargs:
                    continue
                value = kwargs[internal_key]
                if isinstance(mapping, (list, tuple)):
                    router_key, fmt = mapping
                    payload[router_key] = fmt.format(value)
                else:
                    payload[mapping] = str(value)

        if json_list_payload:
            payload = {"data": json.dumps([payload])}

        if method == "POST":
            if static_params:
                payload.update(static_params)
            return await self.post(
                endpoint, payload or None, use_stok=use_stok, errors=errors
            )

        query_params = {}
        if static_params:
            query_params.update(static_params)
        if payload:
            query_params.update(payload)
        return await self.get(
            endpoint, query_params or None, use_stok=use_stok, errors=errors
        )

    async def login(self) -> dict:
        """Login method

        :return dict: dict with login data.
        """

        _method: str = "xqsystem/login"
        _nonce: str = self.generate_nonce()
        _url: str = f"{self._url}/api/{_method}"

        _request_data: dict = {
            "username": CLIENT_USERNAME,
            "logtype": str(CLIENT_LOGIN_TYPE),
            "password": self.generate_password_hash(_nonce, str(self._password)),
            "nonce": _nonce,
        }

        try:
            self._debug("Start request", _url, json.dumps(_request_data), _method, True)

            async with self._client as client:
                response: Response = await client.post(
                    _url,
                    data=_request_data,
                    timeout=self._timeout,
                )

            self._debug("Successful request", _url, response.content, _method)

            _data: dict = json.loads(response.content)
        except (HTTPError, ConnectError, TransportError, ValueError, TypeError) as _e:
            self._debug("Connection error", _url, _e, _method)

            raise LuciConnectionError("Connection error") from _e

        if response.status_code != 200 or "token" not in _data:
            self._debug("Failed to get token", _url, _data, _method)

            raise LuciRequestError("Failed to get token")

        self._token = _data["token"]

        return _data

    async def logout(self) -> None:
        """Logout method"""

        if self._token is None:
            return

        _method: str = "logout"
        _url: str = f"{self._url}/;stok={self._token}/web/{_method}"

        try:
            async with self._client as client:
                response: Response = await client.get(_url, timeout=self._timeout)

                self._debug("Successful request", _url, response.content, _method)
        except (HTTPError, ConnectError, TransportError, ValueError, TypeError) as _e:
            self._debug("Logout error", _url, _e, _method)

    async def get(
        self,
        path: str,
        query_params: dict | None = None,
        use_stok: bool = True,
        errors: dict[int, str] | None = None,
    ) -> dict:
        """GET method.

        :param path: str: api method
        :param query_params: dict | None: Data
        :param use_stok: bool: is use stack
        :param errors: dict[int, str] | None: errors list
        :return dict: dict with api data.
        """

        if use_stok and self._token is None:
            raise LuciRequestError("Token not found")

        if query_params is not None and len(query_params) > 0:
            path += f"?{urllib.parse.urlencode(query_params, doseq=True)}"

        _stok: str = f";stok={self._token}/" if use_stok else ""
        _url: str = f"{self._url}/{_stok}api/{path}"

        try:
            async with self._client as client:
                response: Response = await client.get(_url, timeout=self._timeout)

            self._debug("Successful request", _url, response.content, path)

            _data: dict = json.loads(response.content)
        except (
            HTTPError,
            ConnectError,
            TransportError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as _e:
            self._debug("Connection error", _url, _e, path)

            raise LuciConnectionError("Connection error") from _e

        if "code" not in _data or _data["code"] > 0:
            _code: int = -1 if "code" not in _data else int(_data["code"])

            self._debug("Invalid error code received", _url, _data, path)

            if "code" in _data and errors is not None and _data["code"] in errors:
                raise LuciError(errors[_data["code"]])

            raise LuciRequestError(
                _data.get("msg", f"Invalid error code received: {_code}")
            )

        return _data

    async def post(
        self,
        path: str,
        form_data: dict | None = None,
        use_stok: bool = True,
        errors: dict[int, str] | None = None,
    ) -> dict:
        """POST method for deep debug and state changes."""
        if use_stok and self._token is None:
            raise LuciRequestError("Token not found")

        _stok: str = f";stok={self._token}/" if use_stok else ""
        _url: str = f"{self._url}/{_stok}api/{path}"

        _LOGGER.debug("MiWiFi DEEP DEBUG [POST REQ]: URL=%s | Payload=%s", _url, form_data)

        try:
            async with self._client as client:
                response: Response = await client.post(_url, data=form_data, timeout=self._timeout)

            _LOGGER.debug("MiWiFi DEEP DEBUG [POST RES]: Status=%s | Body=%s", response.status_code, response.content)
            self._debug("Successful request", _url, response.content, path)
            _data: dict = json.loads(response.content)
        except Exception as _e:
            _error_str = str(_e)

            if "clear session" in _error_str or "illegal header" in _error_str or "RemoteProtocolError" in type(_e).__name__:
                _LOGGER.debug(
                    "MiWiFi workaround: successful command with suppressed malformed router response: %s",
                    _error_str,
                )
                return {"code": 0}

            _LOGGER.error("MiWiFi DEEP DEBUG [ERROR]: %s | %s", type(_e).__name__, _e)
            self._debug("Connection/Parse error", _url, _e, path)
            raise LuciConnectionError("Connection error") from _e

        if "code" not in _data or _data["code"] > 0:
            _code: int = -1 if "code" not in _data else int(_data["code"])
            _LOGGER.error("MiWiFi DEEP DEBUG [API ERROR]: Code=%s, Message=%s", _code, _data.get("msg", ""))
            self._debug("Invalid error code received", _url, _data, path)
            if "code" in _data and errors is not None and _data["code"] in errors:
                raise LuciError(errors[_data["code"]])
            raise LuciRequestError(_data.get("msg", f"Invalid error code received: {_code}"))

        return _data

    async def topo_graph(self) -> dict:
        """misystem/topo_graph method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("topo_graph")

    async def init_info(self) -> dict:
        """xqsystem/init_info method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("init_info")

    async def status(self) -> dict:
        """misystem/status method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("status")

    async def new_status(self) -> dict:
        """misystem/newstatus method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("new_status")

    async def mode(self) -> dict:
        """xqnetwork/mode method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("mode")

    async def wifi_ap_signal(self) -> dict:
        """xqnetwork/wifiap_signal method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("wifi_ap_signal")

    async def wifi_detail_all(self) -> dict:
        """xqnetwork/wifi_detail_all method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("wifi_detail_all")

    async def wifi_diag_detail_all(self) -> dict:
        """xqnetwork/wifi_diag_detail_all method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("wifi_diag_detail_all")

    async def vpn_status(self) -> dict:
        """xqsystem/vpn_status method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("vpn_status")

    async def set_wifi(self, data: dict) -> dict:
        """xqnetwork/set_wifi method.

        :param data: dict: Adapter data
        :return dict: dict with api data.
        """

        return await self._execute_api_action("set_wifi", **data)

    async def set_guest_wifi(self, data: dict) -> dict:
        """xqnetwork/set_wifi_without_restart method.

        :param data: dict: Adapter data
        :return dict: dict with api data.
        """

        return await self._execute_api_action("set_guest_wifi", **data)

    async def avaliable_channels(self, index: int = 1) -> dict:
        """xqnetwork/avaliable_channels method.

        :param index: int: Index wifi adapter
        :return dict: dict with api data.
        """

        return await self._execute_api_action("avaliable_channels", index=index)

    async def wan_info(self) -> dict:
        """xqnetwork/wan_info method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("wan_info")

    async def reboot(self) -> dict:
        """xqsystem/reboot method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("reboot")

    async def led(self, state: int | None = None) -> dict:
        """misystem/led method.

        :param state: int|None: on/off state
        :return dict: dict with api data.
        """

        kwargs: dict = {}
        if state is not None:
            kwargs["state"] = state

        return await self._execute_api_action("led", **kwargs)

    async def device_list(self) -> dict:
        """misystem/devicelist method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("device_list")

    async def wifi_connect_devices(self) -> dict:
        """xqnetwork/wifi_connect_devices method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("wifi_connect_devices")

    async def set_mac_filter(self, mac: str, wan: int = 0) -> dict:
        """xqsystem/set_mac_filter method."""

        return await self._execute_api_action("set_mac_filter", mac=mac, wan=wan)

    async def mac_filter_info(self) -> dict:
        """xqsystem/mac_filter_info method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("mac_filter_info")

    async def macbind_info(self) -> dict:
        """xqsystem/macbind_info method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("macbind_info")

    async def mac_bind(self, ip: str, mac: str, name: str) -> dict:
        """xqsystem/mac_bind method.

        :param ip: str: Device IP address
        :param mac: str: Device MAC address
        :param name: str: Device name
        :return dict: dict with api data.
        """

        return await self._execute_api_action("mac_bind", ip=ip, mac=mac, name=name)

    async def mac_unbind(self, mac: str) -> dict:
        """xqsystem/mac_unbind method.

        :param mac: str: Device MAC address
        :return dict: dict with api data.
        """

        return await self._execute_api_action("mac_unbind", mac=mac)

    async def rom_update(self) -> dict:
        """xqsystem/check_rom_update method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("rom_update")

    async def rom_upgrade(self, data: dict) -> dict:
        """xqsystem/upgrade_rom method.

        :param data: dict: Rom data
        :return dict: dict with api data.
        """

        return await self._execute_api_action("rom_upgrade", **data)

    async def flash_permission(self) -> dict:
        """xqsystem/flash_permission method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("flash_permission")

    async def port_forward_list(self) -> dict:
        """Get port forward list."""
        return await self._execute_api_action("get_port_forward_list")

    async def add_port_forward(
        self,
        name: str,
        proto: int,
        fwd_type: int,
        fwd_ip: str,
        fwd_port: int,
        ext_port: int,
    ) -> dict:
        """Add port forward rule."""
        return await self._execute_api_action(
            "add_port_forward",
            name=name,
            proto=proto,
            ext_port=ext_port,
            fwd_ip=fwd_ip,
            fwd_port=fwd_port
        )

    async def delete_port_forward(
        self,
        proto: int,
        ext_port: int,
    ) -> dict:
        """Delete port forward rule."""
        return await self._execute_api_action(
            "delete_port_forward",
            ext_port=ext_port,
            proto=proto
        )

    async def set_qos_switch(self, on: int) -> dict:
        """misystem/qos_switch method.

        :param on: int: 1 = enable Smart QoS, 0 = disable
        """

        return await self._execute_api_action("set_qos_switch", on=on)

    async def set_qos_mode(self, mode: int) -> dict:
        """misystem/qos_mode method.

        :param mode: int: 3 = Auto, 4 = Game, 5 = Web, 6 = Video
        """

        return await self._execute_api_action("set_qos_mode", mode=mode)

    async def set_band(self, upload: int, download: int) -> dict:
        """misystem/set_band method.

        :param upload: int: Global upload speed in Mbps
        :param download: int: Global download speed in Mbps
        """

        return await self._execute_api_action(
            "set_band", upload=upload, download=download
        )

    async def qos_info(self) -> dict:
        """misystem/qos_info method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("qos_info")

    async def set_qos(
        self, mac: str, upload: int, download: int
    ) -> dict:
        """misystem/qos_limits method.

        :param mac: str: Device MAC address
        :param upload: int: Upload limit in KB/s
        :param download: int: Download limit in KB/s
        :return dict: dict with api data.
        """

        return await self._execute_api_action(
            "set_qos", mac=mac, upload=upload, download=download
        )

    async def wifi_timer_info(self) -> dict:
        """xqnetwork/wifi_timer method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("wifi_timer_info")

    async def set_wifi_timer(
        self,
        start_time: str,
        end_time: str,
        days: str,
        enabled: int,
    ) -> dict:
        """xqnetwork/set_wifi_timer method.

        Global WiFi transmitter schedule (turns WiFi on/off on a schedule).

        :param start_time: str: Start time when WiFi turns OFF (HH:MM)
        :param end_time: str: End time when WiFi turns ON (HH:MM)
        :param days: str: Days of week (e.g. "1,2,3,4,5,6,7")
        :param enabled: int: 1 = enable schedule, 0 = disable
        :return dict: dict with api data.
        """

        return await self._execute_api_action(
            "set_wifi_timer",
            start_time=start_time,
            end_time=end_time,
            days=days,
            enabled=enabled,
        )

    async def set_mac_time(
        self,
        mac: str,
        start_time: str,
        end_time: str,
        days: str,
        enabled: int,
    ) -> dict:
        """misystem/set_mac_time method.

        Per-device parental control: restrict internet access by schedule.

        :param mac: str: Device MAC address
        :param start_time: str: Start time of restriction (HH:MM)
        :param end_time: str: End time of restriction (HH:MM)
        :param days: str: Days of week (e.g. "1,2,3,4,5,6,7")
        :param enabled: int: 1 = enable restriction, 0 = disable
        :return dict: dict with api data.
        """

        return await self._execute_api_action(
            "set_mac_time",
            mac=mac,
            start_time=start_time,
            end_time=end_time,
            days=days,
            enabled=enabled,
        )

    async def add_mesh_node(self, locate_ip: str) -> dict:
        """xqnetwork/add_mesh_node method.

        :param locate_ip: str: IP address of the node to add
        :return dict: dict with api data.
        """

        return await self._execute_api_action(
            "add_mesh_node", locate_ip=locate_ip
        )

    async def wifi_macfilter_info(self) -> dict:
        """xqnetwork/wifi_macfilter_info method.

        :return dict: dict with api data.
        """

        return await self._execute_api_action("wifi_macfilter_info")

    async def set_wifi_macfilter(self, model: int, mac: str) -> dict:
        """xqnetwork/set_wifi_macfilter method.

        Configure WiFi MAC filter mode and the list of controlled devices.
        Different from set_mac_filter which only blocks WAN access.

        :param model: int: 0 = disabled, 1 = blacklist, 2 = whitelist
        :param mac: str: MAC address(es); multiple separated by semicolons
        :return dict: dict with api data.
        """

        return await self._execute_api_action(
            "set_wifi_macfilter", model=model, mac=mac
        )

    def sha(self, key: str) -> str:
        """Generate sha by key.

        :param key: str: the key from which to get the hash
        :return str: sha from key.
        """

        if self._encryption == EncryptionAlgorithm.SHA256:
            return hashlib.sha256(key.encode()).hexdigest()

        return hashlib.sha1(key.encode()).hexdigest()

    @staticmethod
    def get_mac_address() -> str:
        """Generate fake mac address.

        :return str: mac address.
        """

        as_hex: str = f"{uuid.getnode():012x}"

        return ":".join(as_hex[i : i + 2] for i in range(0, 12, 2))

    def generate_nonce(self) -> str:
        """Generate fake nonce.

        :return str: nonce.
        """

        rand: str = f"{int(time.time())}_{int(random.random() * 1000)}"

        return f"{CLIENT_NONCE_TYPE}_{self.get_mac_address()}_{rand}"

    def generate_password_hash(self, nonce: str, password: str) -> str:
        """Generate password hash.

        :param nonce: str: nonce
        :param password: str: password
        :return str: sha from password and nonce.
        """

        return self.sha(nonce + self.sha(password + CLIENT_PUBLIC_KEY))

    def _debug(
        self, message: str, url: str, content: Any, path: str, is_only_log: bool = False
    ) -> None:
        """Debug log

        :param message: str: Message
        :param url: str: URL
        :param content: Any: Content
        :param path: str: Path
        :param is_only_log: bool: Is only log
        """

        _LOGGER.debug("%s (%s): %s", message, url, str(content))

        if is_only_log:
            return

        _content: dict | str = {}

        try:
            _content = json.loads(content)
        except (ValueError, TypeError):
            _content = str(content)

        self.diagnostics[path] = {
            DIAGNOSTIC_DATE_TIME: datetime.now().replace(microsecond=0).isoformat(),
            DIAGNOSTIC_MESSAGE: message,
            DIAGNOSTIC_CONTENT: _content,
        }
