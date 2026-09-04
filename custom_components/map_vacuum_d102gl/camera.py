"""Entité caméra : image de la carte (compatible avec la carte Lovelace Xiaomi Vacuum Map Card)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MapVacuumConfigEntry
from .coordinator import MapVacuumCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: MapVacuumConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([MapVacuumCamera(entry.runtime_data)])


class MapVacuumCamera(CoordinatorEntity[MapVacuumCoordinator], Camera):
    """Caméra affichant la dernière carte."""

    _attr_has_entity_name = True
    _attr_translation_key = "map"

    def __init__(self, coordinator: MapVacuumCoordinator) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        assert coordinator.device is not None
        self._attr_unique_id = f"{coordinator.device.did}_map_camera"
        self._attr_device_info = coordinator.device_info

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.image_png

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.attributes
