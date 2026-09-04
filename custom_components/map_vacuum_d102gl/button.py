"""Bouton : forcer le rafraîchissement de la carte."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MapVacuumConfigEntry
from .coordinator import MapVacuumCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: MapVacuumConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([MapVacuumRefreshButton(entry.runtime_data)])


class MapVacuumRefreshButton(CoordinatorEntity[MapVacuumCoordinator], ButtonEntity):
    """Déclenche une mise à jour immédiate de la carte."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"

    def __init__(self, coordinator: MapVacuumCoordinator) -> None:
        super().__init__(coordinator)
        assert coordinator.device is not None
        self._attr_unique_id = f"{coordinator.device.did}_refresh"
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
