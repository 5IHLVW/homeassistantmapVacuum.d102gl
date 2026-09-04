"""Intégration Home Assistant : carte d'un aspirateur robot Xiaomi via le cloud Xiaomi."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import MapVacuumCoordinator, session_store

type MapVacuumConfigEntry = ConfigEntry[MapVacuumCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MapVacuumConfigEntry) -> bool:
    coordinator = MapVacuumCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: MapVacuumConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MapVacuumConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await session_store(hass, entry.entry_id).async_remove()
