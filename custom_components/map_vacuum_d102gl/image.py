"""Entité image : dernière carte reçue."""

from __future__ import annotations

from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import MapVacuumConfigEntry
from .coordinator import MapVacuumCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: MapVacuumConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([MapVacuumImage(hass, entry.runtime_data)])


class MapVacuumImage(CoordinatorEntity[MapVacuumCoordinator], ImageEntity):
    """Image de la carte."""

    _attr_has_entity_name = True
    _attr_translation_key = "map"
    _attr_content_type = "image/png"

    def __init__(self, hass: HomeAssistant, coordinator: MapVacuumCoordinator) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        assert coordinator.device is not None
        self._attr_unique_id = f"{coordinator.device.did}_map_image"
        self._attr_device_info = coordinator.device_info
        if coordinator.data is not None:
            self._attr_image_last_updated = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data is not None:
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.image_png

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.attributes
