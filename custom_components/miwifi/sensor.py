"""Sensor component."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Final

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_SENSOR_AP_SIGNAL,
    ATTR_SENSOR_AP_SIGNAL_NAME,
    ATTR_SENSOR_DEVICES,
    ATTR_SENSOR_DEVICES_2_4,
    ATTR_SENSOR_DEVICES_2_4_NAME,
    ATTR_SENSOR_DEVICES_5_0,
    ATTR_SENSOR_DEVICES_5_0_GAME,
    ATTR_SENSOR_DEVICES_5_0_GAME_NAME,
    ATTR_SENSOR_DEVICES_5_0_NAME,
    ATTR_SENSOR_DEVICES_GUEST,
    ATTR_SENSOR_DEVICES_GUEST_NAME,
    ATTR_SENSOR_DEVICES_LAN,
    ATTR_SENSOR_DEVICES_LAN_NAME,
    ATTR_SENSOR_DEVICES_NAME,
    ATTR_SENSOR_MAC_FILTER,
    ATTR_SENSOR_MAC_FILTER_LIST,
    ATTR_SENSOR_MAC_FILTER_NAME,
    ATTR_SENSOR_MEMORY_TOTAL,
    ATTR_SENSOR_MEMORY_TOTAL_NAME,
    ATTR_SENSOR_MEMORY_USAGE,
    ATTR_SENSOR_MEMORY_USAGE_NAME,
    ATTR_SENSOR_MODE,
    ATTR_SENSOR_MODE_NAME,
    ATTR_SENSOR_TEMPERATURE,
    ATTR_SENSOR_TEMPERATURE_NAME,
    ATTR_SENSOR_UPTIME,
    ATTR_SENSOR_UPTIME_NAME,
    ATTR_SENSOR_VPN_UPTIME,
    ATTR_SENSOR_VPN_UPTIME_NAME,
    ATTR_SENSOR_WAN_DOWNLOAD_SPEED,
    ATTR_SENSOR_WAN_DOWNLOAD_SPEED_NAME,
    ATTR_SENSOR_WAN_UPLOAD_SPEED,
    ATTR_SENSOR_WAN_UPLOAD_SPEED_NAME,
    ATTR_STATE,
    ATTR_PORT_FORWARD,
    ATTR_PORT_FORWARD_NAME
)
from .entity import MiWifiEntity
from .enum import DeviceClass
from .updater import LuciUpdater, async_get_updater

PARALLEL_UPDATES = 0

DISABLE_ZERO: Final = (
    ATTR_SENSOR_TEMPERATURE,
    ATTR_SENSOR_AP_SIGNAL,
)

ONLY_WAN: Final = (
    ATTR_SENSOR_WAN_DOWNLOAD_SPEED,
    ATTR_SENSOR_WAN_UPLOAD_SPEED,
)

PCS: Final = "pcs"
BS: Final = "B/s"

MIWIFI_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key=ATTR_SENSOR_UPTIME,
        name=ATTR_SENSOR_UPTIME_NAME,
        icon="mdi:timer-sand",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_VPN_UPTIME,
        name=ATTR_SENSOR_VPN_UPTIME_NAME,
        icon="mdi:timer-sand",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_MEMORY_USAGE,
        name=ATTR_SENSOR_MEMORY_USAGE_NAME,
        icon="mdi:memory",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_MEMORY_TOTAL,
        name=ATTR_SENSOR_MEMORY_TOTAL_NAME,
        icon="mdi:memory",
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_TEMPERATURE,
        name=ATTR_SENSOR_TEMPERATURE_NAME,
        icon="mdi:temperature-celsius",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_MODE,
        name=ATTR_SENSOR_MODE_NAME,
        icon="mdi:transit-connection-variant",
        device_class=DeviceClass.MODE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_AP_SIGNAL,
        name=ATTR_SENSOR_AP_SIGNAL_NAME,
        icon="mdi:wifi-arrow-left-right",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_WAN_DOWNLOAD_SPEED,
        name=ATTR_SENSOR_WAN_DOWNLOAD_SPEED_NAME,
        icon="mdi:speedometer",
        native_unit_of_measurement=BS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_WAN_UPLOAD_SPEED,
        name=ATTR_SENSOR_WAN_UPLOAD_SPEED_NAME,
        icon="mdi:speedometer",
        native_unit_of_measurement=BS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_DEVICES,
        name=ATTR_SENSOR_DEVICES_NAME,
        icon="mdi:counter",
        native_unit_of_measurement=PCS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=True,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_DEVICES_LAN,
        name=ATTR_SENSOR_DEVICES_LAN_NAME,
        icon="mdi:counter",
        native_unit_of_measurement=PCS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_DEVICES_2_4,
        name=ATTR_SENSOR_DEVICES_2_4_NAME,
        icon="mdi:counter",
        native_unit_of_measurement=PCS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_DEVICES_5_0,
        name=ATTR_SENSOR_DEVICES_5_0_NAME,
        icon="mdi:counter",
        native_unit_of_measurement=PCS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_DEVICES_GUEST,
        name=ATTR_SENSOR_DEVICES_GUEST_NAME,
        icon="mdi:counter",
        native_unit_of_measurement=PCS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_SENSOR_DEVICES_5_0_GAME,
        name=ATTR_SENSOR_DEVICES_5_0_GAME_NAME,
        icon="mdi:counter",
        native_unit_of_measurement=PCS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_PORT_FORWARD,
        name=ATTR_PORT_FORWARD_NAME,
        icon="mdi:transit-connection-variant",
        native_unit_of_measurement=PCS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MiWifi sensor entry."""

    updater: LuciUpdater = async_get_updater(hass, config_entry.entry_id)

    entities: list[MiWifiSensor] = []
    for description in MIWIFI_SENSORS:
        if description.key in [ATTR_PORT_FORWARD, ATTR_SENSOR_MAC_FILTER]:
            continue
            
        if (
            description.key == ATTR_SENSOR_DEVICES_5_0_GAME
            and not updater.supports_game
        ):
            continue

        if (
            description.key in DISABLE_ZERO
            and updater.data.get(description.key, 0) == 0
        ):
            continue

        if description.key in ONLY_WAN and not updater.supports_wan:
            continue

        entities.append(
            MiWifiSensor(
                f"{config_entry.entry_id}-{description.key}",
                description,
                updater,
            )
        )

    async_add_entities(entities)

    custom_entities = [
        MiWifiMacFilterSensor(
            f"{config_entry.entry_id}-{ATTR_SENSOR_MAC_FILTER}",
            SensorEntityDescription(
                key=ATTR_SENSOR_MAC_FILTER,
                name=ATTR_SENSOR_MAC_FILTER_NAME,
                icon="mdi:shield-lock",
                native_unit_of_measurement=PCS,
                state_class=SensorStateClass.MEASUREMENT,
                entity_category=EntityCategory.DIAGNOSTIC,
                entity_registry_enabled_default=True,
            ),
            updater,
        ),
        MiWifiPortForwardSensor(
            f"{config_entry.entry_id}-{ATTR_PORT_FORWARD}",
            SensorEntityDescription(
                key=ATTR_PORT_FORWARD,
                name=ATTR_PORT_FORWARD_NAME,
                icon="mdi:transit-connection-variant",
                native_unit_of_measurement=PCS,
                state_class=SensorStateClass.MEASUREMENT,
                entity_category=EntityCategory.DIAGNOSTIC,
                entity_registry_enabled_default=True,
            ),
            updater,
        )
    ]
    
    async_add_entities(custom_entities)


class MiWifiSensor(MiWifiEntity, SensorEntity):
    """MiWifi sensor entry — tracks various metrics."""

    def __init__(
        self,
        unique_id: str,
        description: SensorEntityDescription,
        updater: LuciUpdater,
    ) -> None:
        """Initialize sensor."""
        MiWifiEntity.__init__(self, unique_id, description, updater, ENTITY_ID_FORMAT)

        state: Any = self._updater.data.get(description.key, None)
        if state is not None and isinstance(state, Enum):
            state = state.phrase  # type: ignore

        self._attr_native_value = state

    def _handle_coordinator_update(self) -> None:
        """Update state."""
        is_available: bool = self._updater.data.get(ATTR_STATE, False)
        state: Any = self._updater.data.get(self.entity_description.key, None)

        if state is not None and isinstance(state, Enum):
            state = state.phrase  # type: ignore

        if (
            self._attr_native_value == state
            and self._attr_available == is_available
        ):
            return

        self._attr_available = is_available
        self._attr_native_value = state
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.key == ATTR_SENSOR_DEVICES:
            return {
                "qos_band_up": self._updater.data.get("qos_band_up", 1000),
                "qos_band_down": self._updater.data.get("qos_band_down", 1000),
            }
        return None


class MiWifiPortForwardSensor(MiWifiSensor):
    """MiWifi Port Forward sensor — tracks rules."""

    def __init__(
        self,
        unique_id: str,
        description: SensorEntityDescription,
        updater: LuciUpdater,
    ) -> None:
        """Initialize sensor."""

        MiWifiSensor.__init__(self, unique_id, description, updater)
        
        rules: list = updater.data.get(ATTR_PORT_FORWARD, [])
        self._attr_native_value = len(rules)

    def _handle_coordinator_update(self) -> None:
        """Update state."""
        is_available: bool = self._updater.data.get(ATTR_STATE, False)
        rules: list = self._updater.data.get(ATTR_PORT_FORWARD, [])
        count: int = len(rules)

        if (
            self._attr_native_value == count
            and self._attr_available == is_available
            and self.extra_state_attributes.get("rules") == rules
        ):
            return

        self._attr_available = is_available
        self._attr_native_value = count
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"rules": self._updater.data.get(ATTR_PORT_FORWARD, [])}


class MiWifiMacFilterSensor(MiWifiSensor):
    """MiWifi MAC filter sensor — tracks blocked devices."""

    def __init__(
        self,
        unique_id: str,
        description: SensorEntityDescription,
        updater: LuciUpdater,
    ) -> None:
        MiWifiSensor.__init__(self, unique_id, description, updater)
        blocked: list = updater.data.get(ATTR_SENSOR_MAC_FILTER, [])
        self._attr_native_value = len(blocked)
        self._blocked_macs: list = list(blocked)

    def _handle_coordinator_update(self) -> None:
        """Update state."""
        is_available: bool = self._updater.data.get(ATTR_STATE, False)
        blocked: list = self._updater.data.get(ATTR_SENSOR_MAC_FILTER, [])
        count: int = len(blocked)

        if (
            self._attr_native_value == count
            and self._attr_available == is_available
            and self._blocked_macs == blocked
        ):
            return

        self._attr_available = is_available
        self._attr_native_value = count
        self._blocked_macs = list(blocked)

        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_SENSOR_MAC_FILTER_LIST: self._blocked_macs}