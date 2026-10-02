"""MiWifi entity."""

from __future__ import annotations

import logging

from homeassistant.helpers.entity import EntityDescription
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_DEVICE_MAC_ADDRESS, ATTR_STATE, ATTRIBUTION
from .helper import generate_entity_id
from .updater import LuciUpdater
from .operations import OperationError, manager_for

_LOGGER = logging.getLogger(__name__)


class MiWifiEntity(CoordinatorEntity):
    """MiWifi entity."""

    _attr_attribution: str = ATTRIBUTION

    def __init__(
        self,
        unique_id: str,
        description: EntityDescription,
        updater: LuciUpdater,
        entity_id_format: str,
    ) -> None:
        """Initialize sensor.

        :param unique_id: str: Unique ID
        :param description: EntityDescription: EntityDescription object
        :param updater: LuciUpdater: Luci updater object
        :param entity_id_format: str: ENTITY_ID_FORMAT
        """

        CoordinatorEntity.__init__(self, coordinator=updater)

        self.entity_description = description
        self._updater: LuciUpdater = updater

        self.entity_id = generate_entity_id(
            entity_id_format,
            updater.data.get(ATTR_DEVICE_MAC_ADDRESS, updater.ip),
            description.name,
        )

        self._attr_name = description.name
        self._attr_unique_id = unique_id
        self._attr_available = updater.data.get(ATTR_STATE, False)

        self._attr_device_info = updater.device_info

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""

        await CoordinatorEntity.async_added_to_hass(self)

    @property
    def available(self) -> bool:
        """Is available

        :return bool: Is available
        """

        record = manager_for(self._updater).records.get(self.entity_description.key)
        return (
            self._attr_available
            and self.coordinator.last_update_success
            and (record is None or record.status != "unknown")
        )

    @property
    def extra_state_attributes(self) -> dict:
        record = manager_for(self._updater).records.get(self.entity_description.key)
        if record is None:
            return {}
        return {
            "operation_status": record.status,
            "operation_id": record.request_id,
            "pending": record.status == "pending",
        }

    def _control_pending(self) -> bool:
        record = manager_for(self._updater).records.get(self.entity_description.key)
        return record is not None and record.status == "pending"

    async def _verified_control(self, desired, command, apply) -> None:
        """Optimistic state, authoritative read-back and exact rollback."""
        key = self.entity_description.key
        try:
            await manager_for(self._updater).execute(
                key,
                desired,
                command,
                lambda: self._updater.read_control(key),
                apply,
                self.async_write_ha_state,
                optimistic=lambda: apply(desired),
            )
        except OperationError as err:
            raise HomeAssistantError(str(err)) from err
        finally:
            self._updater.schedule_followup_refresh()

    def _handle_coordinator_update(self) -> None:
        """Update state."""

        raise NotImplementedError  # pragma: no cover
