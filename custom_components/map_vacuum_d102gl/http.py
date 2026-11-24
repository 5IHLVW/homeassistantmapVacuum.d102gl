"""HTTP view with the vector map geometry (for custom vector map cards)."""

from __future__ import annotations

from http import HTTPStatus

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN


class MapGeometryView(HomeAssistantView):
    """GET /api/map_vacuum_d102gl/geometry/<entry_id> -> JSON with outlines, path and position.

    Served as a view instead of a state attribute, so the recorder and the
    state machine do not carry the (fairly large) polygons.
    """

    url = f"/api/{DOMAIN}/geometry/{{entry_id}}"
    name = f"api:{DOMAIN}:geometry"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        hass = request.app["hass"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
            return self.json_message("Unknown map", HTTPStatus.NOT_FOUND)
        coordinator = entry.runtime_data
        snap = coordinator.data
        if snap is None or snap.geometry is None:
            return self.json_message("Geometry not available yet", HTTPStatus.SERVICE_UNAVAILABLE)
        attrs = coordinator.attributes
        return self.json(
            {
                **snap.geometry,
                "last_update": attrs.get("last_update"),
                "vacuum_position": attrs.get("vacuum_position"),
                "vacuum_room": attrs.get("vacuum_room"),
                "charger": attrs.get("charger"),
                "no_go_areas": attrs.get("no_go_areas"),
                "no_mopping_areas": attrs.get("no_mopping_areas"),
                "virtual_walls": attrs.get("walls"),
                "cleaned_rooms": [r["id"] for r in attrs.get("rooms") or [] if r.get("cleaned")],
            }
        )
