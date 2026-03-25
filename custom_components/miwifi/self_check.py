"""Self check."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Final

import homeassistant.components.persistent_notification as pn
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .api_map import MIWIFI_API_MAP
from .const import DOMAIN, NAME
from .exceptions import LuciError
from .luci import LuciClient


def _ep(action: str) -> str:
    """Get endpoint label from API map."""
    return MIWIFI_API_MAP[action]["endpoint"]


SELF_CHECK_METHODS: Final = (
    (_ep("login"), "🟢"),
    (_ep("init_info"), "🟢"),
    (_ep("status"), "status"),
    (_ep("mode"), "mode"),
    (_ep("vpn_status"), "vpn_status"),
    (_ep("topo_graph"), "topo_graph"),
    (_ep("rom_update"), "rom_update"),
    (_ep("wan_info"), "wan_info"),
    (_ep("led"), "led"),
    (_ep("wifi_detail_all"), "wifi_detail_all"),
    (_ep("wifi_diag_detail_all"), "wifi_diag_detail_all"),
    (_ep("avaliable_channels"), "avaliable_channels"),
    (_ep("wifi_connect_devices"), "wifi_connect_devices"),
    (_ep("device_list"), "device_list"),
    (_ep("wifi_ap_signal"), "wifi_ap_signal"),
    (_ep("new_status"), "new_status"),
    (_ep("reboot"), "⚪"),
    (_ep("rom_upgrade"), "⚪"),
    (_ep("flash_permission"), "⚪"),
    (_ep("set_wifi"), "⚪"),
    (_ep("set_guest_wifi"), "⚪"),
)

_LOGGER = logging.getLogger(__name__)


async def async_self_check(hass: HomeAssistant, client: LuciClient, model: str) -> None:
    """Self check

    :param hass: HomeAssistant: HomeAssistant object
    :param client: LuciClient: Luci Client
    :param model: str: Router model
    """

    data: dict = {}

    for code, method in SELF_CHECK_METHODS:
        if method in ["🟢", "🔴", "⚪"]:
            data[code] = method

            continue

        if action := getattr(client, method):
            try:
                await action()
                data[code] = "🟢"
            except LuciError:
                data[code] = "🔴"

    title: str = f"Router {client.ip} not supported.\n\nModel: {model}"

    message: str = "Check list:"

    for method, value in data.items():
        message += f"\n * {method}: {value}"

    integration = await async_get_integration(hass, DOMAIN)

    # fmt: off
    link: str = f"{integration.issue_tracker}/new?title=" \
        + urllib.parse.quote_plus(f"Add supports {model}") \
        + "&body=" \
        + urllib.parse.quote_plus(message)
    # fmt: on

    message = f"{title}\n\n{message}\n\n"

    # fmt: off
    # pylint: disable=line-too-long
    message += \
        f'<a href="{link}" target="_blank">Create an issue with the data from this post to add support</a>'
    # fmt: on

    pn.async_create(hass, message, NAME)
