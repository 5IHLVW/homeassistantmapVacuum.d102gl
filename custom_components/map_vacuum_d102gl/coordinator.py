"""Coordinateur : connexion au cloud, récupération périodique de la carte."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceInfo as HaDeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_API,
    CONF_DEVICE_ID,
    CONF_DEVICE_INFO,
    CONF_HOST,
    CONF_REFRESH_SECONDS,
    CONF_ROTATE,
    CONF_SCALE,
    CONF_SERVER,
    CONF_SESSION,
    CONF_TOKEN,
    CONF_USE_LOCAL,
    DEFAULT_REFRESH_SECONDS,
    DEFAULT_ROTATE,
    DEFAULT_SCALE,
    DOMAIN,
    STORAGE_VERSION,
)
from .core.vacuum import MapError, MapSnapshot, VacuumMapService
from .core.xiaomi_cloud import DeviceInfo, LoginError, XiaomiCloudConnector, XiaomiCloudError

_LOGGER = logging.getLogger(__name__)


def session_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Stockage de la session cloud (évite de refaire le captcha / 2FA à chaque redémarrage)."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.session")


class MapVacuumCoordinator(DataUpdateCoordinator[MapSnapshot]):
    """Récupère la carte à intervalle régulier."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        interval = int(entry.options.get(CONF_REFRESH_SECONDS, DEFAULT_REFRESH_SECONDS))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=max(5, interval)),
        )
        self.connector: XiaomiCloudConnector | None = None
        self.device: DeviceInfo | None = None
        self.service: VacuumMapService | None = None
        self._store = session_store(hass, entry.entry_id)

    async def async_setup(self) -> None:
        """Connexion au cloud et préparation du service (exécuté hors de la boucle d'événements)."""
        data = self.entry.data
        options = self.entry.options
        session = await self._store.async_load() or data.get(CONF_SESSION)
        server = data.get(CONF_SERVER) or None

        def _setup() -> tuple[XiaomiCloudConnector, DeviceInfo, VacuumMapService]:
            connector = XiaomiCloudConnector(data[CONF_USERNAME], data[CONF_PASSWORD], server=server)
            connector.import_session(session)
            connector.session_listener = self._session_listener
            connector.login()
            device, _ = connector.find_device(device_id=data[CONF_DEVICE_ID], server=server)
            if device is None:
                if data.get(CONF_DEVICE_INFO):
                    _LOGGER.warning("Robot %s absent de la liste cloud, utilisation des informations enregistrées", data[CONF_DEVICE_ID])
                    device = DeviceInfo.from_dict(data[CONF_DEVICE_INFO])
                else:
                    raise MapError(f"Robot {data[CONF_DEVICE_ID]} introuvable sur le compte Xiaomi")
            service = VacuumMapService(
                connector,
                device,
                api=options.get(CONF_API, "auto"),
                host=data.get(CONF_HOST) or None,
                token=data.get(CONF_TOKEN) or None,
                scale=float(options.get(CONF_SCALE, DEFAULT_SCALE)),
                rotate=float(options.get(CONF_ROTATE, DEFAULT_ROTATE)),
                use_local=bool(options.get(CONF_USE_LOCAL, True)),
            )
            return connector, device, service

        try:
            self.connector, self.device, self.service = await self.hass.async_add_executor_job(_setup)
        except LoginError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except (XiaomiCloudError, MapError) as exc:
            raise ConfigEntryNotReady(str(exc)) from exc

    def _session_listener(self, session: dict[str, Any]) -> None:
        """Appelé depuis un thread : planifie l'enregistrement de la session."""
        self.hass.loop.call_soon_threadsafe(self._schedule_session_save, session)

    @callback
    def _schedule_session_save(self, session: dict[str, Any]) -> None:
        self.hass.async_create_task(self._store.async_save(session))

    async def _async_update_data(self) -> MapSnapshot:
        assert self.service is not None
        try:
            return await self.hass.async_add_executor_job(self.service.fetch)
        except LoginError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except (MapError, XiaomiCloudError) as exc:
            raise UpdateFailed(str(exc)) from exc

    @property
    def device_info(self) -> HaDeviceInfo:
        assert self.device is not None
        return HaDeviceInfo(
            identifiers={(DOMAIN, self.device.did)},
            name=self.device.name or self.device.model,
            manufacturer="Xiaomi",
            model=self.device.model,
        )

    @property
    def attributes(self) -> dict[str, Any]:
        """Attributs exposés par les entités (compatibles avec Xiaomi Vacuum Map Card)."""
        if self.data is None or self.device is None or self.service is None:
            return {}
        summary = self.data.summary()
        return {
            "calibration_points": summary["calibration_points"],
            "rooms": summary["rooms"],
            "vacuum_position": summary["vacuum_position"],
            "vacuum_room": summary["vacuum_room"],
            "vacuum_room_name": summary["vacuum_room_name"],
            "charger": summary["charger"],
            "goto": summary["goto"],
            "walls": summary["walls"],
            "no_go_areas": summary["no_go_areas"],
            "no_mopping_areas": summary["no_mopping_areas"],
            "zones": summary["zones"],
            "obstacles": summary["obstacles"],
            "image": summary["image"],
            "map_name": summary["map_name"],
            "last_update": summary["timestamp"],
            "model": self.device.model,
            "api": self.service.api.value,
            "local_access": self.service.local is not None,
        }
