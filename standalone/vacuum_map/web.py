"""Interface web (Flask) pour afficher la carte."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template

from .vacuum import MapSnapshot, VacuumMapService

_LOGGER = logging.getLogger(__name__)


class MapUpdater(threading.Thread):
    """Rafraîchit la carte en arrière-plan à intervalle régulier."""

    def __init__(
        self,
        service: VacuumMapService,
        interval: int,
        maps_dir: Path | None = None,
        save_maps: bool = False,
    ) -> None:
        super().__init__(name="map-updater", daemon=True)
        self.service = service
        self.interval = interval
        self.maps_dir = maps_dir
        self.save_maps = save_maps
        self.snapshot: MapSnapshot | None = None
        self.error: str | None = None
        self.last_attempt: datetime | None = None
        self.refreshing = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            self.refresh_now()
            self._wake.wait(self.interval)
            self._wake.clear()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger(self) -> None:
        """Demande un rafraîchissement immédiat."""
        self._wake.set()

    def refresh_now(self) -> bool:
        with self._lock:
            if self.refreshing:
                return False
            self.refreshing = True
        try:
            snapshot = self.service.fetch()
            if self.save_maps and self.maps_dir is not None:
                save_snapshot(snapshot, self.maps_dir)
            with self._lock:
                self.snapshot = snapshot
                self.error = None
            _LOGGER.info("Carte mise à jour (%s)", snapshot.map_name)
            return True
        except Exception as exc:  # noqa: BLE001 - on veut afficher n'importe quelle erreur dans l'interface
            _LOGGER.error("Mise à jour de la carte échouée: %s", exc)
            with self._lock:
                self.error = str(exc)
            return False
        finally:
            with self._lock:
                self.refreshing = False
                self.last_attempt = datetime.now()

    def status(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self.snapshot
            error = self.error
            refreshing = self.refreshing
            last_attempt = self.last_attempt
        device = self.service.device
        return {
            "device": {
                "name": device.name,
                "model": device.model,
                "did": device.did,
                "server": device.server,
                "local_ip": device.local_ip,
                "api": self.service.api.value,
                "local_access": self.service.local is not None,
            },
            "refresh_seconds": self.interval,
            "refreshing": refreshing,
            "last_attempt": last_attempt.isoformat(timespec="seconds") if last_attempt else None,
            "last_update": snapshot.timestamp.isoformat(timespec="seconds") if snapshot else None,
            "error": error,
            "map": snapshot.summary() if snapshot else None,
        }


def save_snapshot(snapshot: MapSnapshot, maps_dir: Path) -> Path:
    """Enregistre la carte (PNG + données brutes) dans le dossier maps/."""
    maps_dir.mkdir(parents=True, exist_ok=True)
    png_path = maps_dir / "map_latest.png"
    png_path.write_bytes(snapshot.image_png)
    (maps_dir / "map_latest.raw").write_bytes(snapshot.raw)
    return png_path


def create_app(updater: MapUpdater) -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
    app.config["JSON_SORT_KEYS"] = False

    no_cache = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache", "Expires": "0"}

    @app.get("/")
    def index() -> str:
        device = updater.service.device
        return render_template(
            "index.html",
            device=device,
            api=updater.service.api.value,
            refresh_seconds=updater.interval,
        )

    @app.get("/map.png")
    def map_png() -> Response:
        snapshot = updater.snapshot
        if snapshot is None:
            return Response("Aucune carte disponible pour le moment", status=404, mimetype="text/plain", headers=no_cache)
        return Response(snapshot.image_png, mimetype="image/png", headers=no_cache)

    @app.get("/map.raw")
    def map_raw() -> Response:
        snapshot = updater.snapshot
        if snapshot is None:
            return Response("Aucune carte disponible pour le moment", status=404, mimetype="text/plain", headers=no_cache)
        return Response(
            snapshot.raw,
            mimetype="application/octet-stream",
            headers={**no_cache, "Content-Disposition": "attachment; filename=map.raw"},
        )

    @app.get("/api/status")
    def api_status() -> Response:
        response = jsonify(updater.status())
        response.headers.update(no_cache)
        return response

    @app.get("/api/map")
    def api_map() -> Response:
        snapshot = updater.snapshot
        if snapshot is None:
            response = jsonify({"error": updater.error or "Aucune carte disponible"})
            response.status_code = 404
        else:
            response = jsonify(snapshot.summary())
        response.headers.update(no_cache)
        return response

    @app.post("/api/refresh")
    def api_refresh() -> Response:
        updater.trigger()
        return jsonify({"ok": True})

    return app
