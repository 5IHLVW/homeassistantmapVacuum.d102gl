"""Services d'édition des pièces : lire, fusionner, diviser (expérimental)."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import MapVacuumCoordinator
from .core.vacuum import MapError
from .core.xiaomi_cloud import XiaomiCloudError

ATTR_ENTRY_ID = "config_entry_id"
ATTR_ROOMS = "rooms"
ATTR_PARAMS = "params"
ATTR_CONFIRM = "confirm"

SERVICE_READ_ROOMS = "read_rooms"
SERVICE_MERGE_ROOMS = "merge_rooms"
SERVICE_SPLIT_ROOM = "split_room"

_BASE = {vol.Optional(ATTR_ENTRY_ID): cv.string}
READ_SCHEMA = vol.Schema(_BASE)
MERGE_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required(ATTR_ROOMS): vol.All(cv.ensure_list, [vol.Coerce(int)], vol.Length(min=2)),
        vol.Optional(ATTR_PARAMS): cv.string,
        vol.Required(ATTR_CONFIRM): cv.boolean,
    }
)
SPLIT_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required(ATTR_PARAMS): cv.string,
        vol.Required(ATTR_CONFIRM): cv.boolean,
    }
)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> MapVacuumCoordinator:
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED and (ATTR_ENTRY_ID not in call.data or e.entry_id == call.data[ATTR_ENTRY_ID])
    ]
    if not entries:
        raise ServiceValidationError("Aucun robot Map Vacuum chargé (vérifiez config_entry_id)")
    if len(entries) > 1:
        raise ServiceValidationError("Plusieurs robots configurés : précisez config_entry_id")
    coordinator: MapVacuumCoordinator = entries[0].runtime_data
    if coordinator.service is None:
        raise HomeAssistantError("Le robot n'est pas encore prêt")
    return coordinator


def _require_confirm(call: ServiceCall) -> None:
    if not call.data[ATTR_CONFIRM]:
        raise ServiceValidationError(
            "Commande expérimentale qui modifie la carte du robot : sauvegardez la carte puis mettez confirm à true"
        )


async def _run(coordinator: MapVacuumCoordinator, func: Any, *args: Any, refresh: bool) -> dict[str, Any]:
    try:
        result = await coordinator.hass.async_add_executor_job(func, *args)
    except (MapError, XiaomiCloudError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    if refresh:
        await coordinator.async_request_refresh()
    return result


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_READ_ROOMS):
        return

    async def read_rooms(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        return await _run(coordinator, coordinator.service.read_room_information, refresh=False)

    async def merge_rooms(call: ServiceCall) -> ServiceResponse:
        _require_confirm(call)
        coordinator = _coordinator(hass, call)
        value = call.data.get(ATTR_PARAMS) or json.dumps({"room": call.data[ATTR_ROOMS]}, separators=(",", ":"))
        return await _run(coordinator, coordinator.service.merge_rooms, value, refresh=True)

    async def split_room(call: ServiceCall) -> ServiceResponse:
        _require_confirm(call)
        coordinator = _coordinator(hass, call)
        return await _run(coordinator, coordinator.service.split_room, call.data[ATTR_PARAMS], refresh=True)

    hass.services.async_register(DOMAIN, SERVICE_READ_ROOMS, read_rooms, READ_SCHEMA, SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_MERGE_ROOMS, merge_rooms, MERGE_SCHEMA, SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_SPLIT_ROOM, split_room, SPLIT_SCHEMA, SupportsResponse.OPTIONAL)
