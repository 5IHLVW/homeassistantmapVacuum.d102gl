"""Récupération et décodage de la carte selon le constructeur du robot."""

from __future__ import annotations

import base64
import io
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from vacuum_map_parser_base.config.drawable import Drawable
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes
from vacuum_map_parser_base.map_data import MapData
from vacuum_map_parser_base.map_data_parser import MapDataParser

from .miio_local import MiioDevice, MiioError
from .style import MapStyle
from .xiaomi_cloud import DeviceInfo, RpcError, SessionExpired, XiaomiCloudConnector, XiaomiCloudError, extract

_LOGGER = logging.getLogger(__name__)


class MapError(Exception):
    """Impossible d'obtenir ou de décoder la carte."""


class VacuumApi(StrEnum):
    ROBOROCK = "roborock"
    DREAME = "dreame"
    VIOMI = "viomi"
    ROIDMI = "roidmi"
    IJAI = "ijai"
    XIAOMI = "xiaomi"
    UNSUPPORTED = "unsupported"


AVAILABLE_APIS: dict[VacuumApi, list[str]] = {
    VacuumApi.DREAME: ["dreame.vacuum."],
    VacuumApi.ROIDMI: ["roidmi.vacuum.", "zhimi.vacuum.", "chuangmi.vacuum."],
    VacuumApi.VIOMI: ["viomi.vacuum."],
    VacuumApi.ROBOROCK: ["roborock.vacuum", "rockrobo.vacuum"],
    VacuumApi.IJAI: ["ijai.vacuum."],
    VacuumApi.XIAOMI: ["xiaomi.vacuum."],
}

API_EXCEPTIONS: dict[str, VacuumApi] = {
    "viomi.vacuum.v18": VacuumApi.ROIDMI,
    "viomi.vacuum.v23": VacuumApi.ROIDMI,
    "viomi.vacuum.v38": VacuumApi.ROIDMI,
    "xiaomi.vacuum.b106eu": VacuumApi.IJAI,
    "xiaomi.vacuum.c103": VacuumApi.IJAI,
    "xiaomi.vacuum.d106gl": VacuumApi.IJAI,
}

# Propriété MIoT contenant le nom de la carte pour les modèles xiaomi.vacuum.*
_XIAOMI_MAP_PROP_DEFAULT = (10, 1)
_XIAOMI_MAP_PROP_OVERRIDES: list[tuple[list[str], tuple[int, int]]] = [
    (["xiaomi.vacuum.b108gl"], (7, 1)),
    (
        ["xiaomi.vacuum.b108gp", "xiaomi.vacuum.ov32gl", "xiaomi.vacuum.ov43gl", "xiaomi.vacuum.ov51", "xiaomi.vacuum.ov81"],
        (9, 1),
    ),
    (
        [
            "xiaomi.vacuum.b106bk",
            "xiaomi.vacuum.b106tr",
            "xiaomi.vacuum.b112",
            "xiaomi.vacuum.b112bk",
            "xiaomi.vacuum.b112gl",
            "xiaomi.vacuum.b112tr",
            "xiaomi.vacuum.c101",
            "xiaomi.vacuum.c101eu",
            "xiaomi.vacuum.c102",
            "xiaomi.vacuum.c104",
            "xiaomi.vacuum.e101gl",
        ],
        (10, 2),
    ),
]


def detect_api(model: str) -> VacuumApi:
    """Déduit l'API (constructeur) à partir du nom de modèle."""
    if model in API_EXCEPTIONS:
        return API_EXCEPTIONS[model]
    for api, prefixes in AVAILABLE_APIS.items():
        if any(model.startswith(prefix) for prefix in prefixes):
            return api
    return VacuumApi.UNSUPPORTED


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return str(value)


@dataclass
class MapSnapshot:
    """Carte décodée à un instant donné."""

    timestamp: datetime
    map_name: str
    image_png: bytes
    map_data: MapData
    raw: bytes

    def summary(self) -> dict[str, Any]:
        md = self.map_data
        rooms = []
        for number, room in (md.rooms or {}).items():
            rooms.append(
                {
                    "id": number,
                    "name": room.name,
                    "x0": room.x0,
                    "y0": room.y0,
                    "x1": room.x1,
                    "y1": room.y1,
                    "cleaned": bool(md.cleaned_rooms and number in md.cleaned_rooms),
                }
            )
        return {
            "timestamp": self.timestamp.isoformat(timespec="seconds"),
            "map_name": self.map_name,
            "image": md.image.as_dict() if md.image else None,
            "vacuum_position": md.vacuum_position.as_dict() if md.vacuum_position else None,
            "vacuum_room": md.vacuum_room,
            "vacuum_room_name": md.vacuum_room_name,
            "charger": md.charger.as_dict() if md.charger else None,
            "goto": md.goto.as_dict() if md.goto else None,
            "rooms": rooms,
            "walls": [w.as_list() for w in md.walls or []],
            "no_go_areas": [a.as_list() for a in md.no_go_areas or []],
            "no_mopping_areas": [a.as_list() for a in md.no_mopping_areas or []],
            "zones": [z.as_dict() for z in md.zones or []],
            "obstacles": [o.as_dict() for o in md.obstacles or []],
            "path_points": sum(len(p) for p in md.path.path) if md.path else 0,
            "calibration_points": md.calibration(),
            "additional_parameters": _json_safe(md.additional_parameters),
        }


class VacuumMapService:
    """Récupère la carte d'un robot (nom de carte, téléchargement, décodage, image)."""

    WIFI_SN_LENGTHS = (18, 20)

    def __init__(
        self,
        cloud: XiaomiCloudConnector,
        device: DeviceInfo,
        api: str | VacuumApi | None = "auto",
        host: str | None = None,
        token: str | None = None,
        scale: float = 3.0,
        rotate: float = 0.0,
        drawables: list[Drawable] | None = None,
        use_local: bool = True,
        style: MapStyle | None = None,
    ) -> None:
        self.cloud = cloud
        self.device = device
        if api in (None, "auto"):
            self.api = detect_api(device.model)
        else:
            self.api = VacuumApi(str(api).lower())
        if self.api == VacuumApi.UNSUPPORTED:
            raise MapError(f"Modèle non supporté: {device.model}")

        self.host = host or device.local_ip
        self.token = token or device.token
        self.local: MiioDevice | None = None
        if use_local and self.host and self.token and len(self.token) == 32:
            self.local = MiioDevice(self.host, self.token)
        self._local_failures = 0

        self._enc_key: str | None = None
        self._robot_stamp: Any = 0
        self._wifi_info_sn: str | None = None

        self.scale = scale
        self.rotate = rotate
        self.drawables = list(Drawable) if drawables is None else drawables
        self.style = style or MapStyle()
        self._parser = self._build_parser()
        _LOGGER.info(
            "Robot %s (%s) - API %s - accès local %s",
            device.name,
            device.model,
            self.api.value,
            f"activé ({self.host})" if self.local else "désactivé (cloud uniquement)",
        )

    # ------------------------------------------------------------------ parseur
    def _build_parser(self) -> MapDataParser:
        parser = self._create_parser()
        self.style.apply(parser, self.scale)
        return parser

    def _create_parser(self) -> MapDataParser:
        palette = self.style.palette
        sizes = Sizes()
        image_config = ImageConfig(scale=self.scale, rotate=self.rotate)
        texts: list = []
        args = (palette, sizes, self.drawables, image_config, texts)
        try:
            if self.api == VacuumApi.ROBOROCK:
                from vacuum_map_parser_roborock.map_data_parser import RoborockMapDataParser

                return RoborockMapDataParser(*args)
            if self.api == VacuumApi.DREAME:
                from vacuum_map_parser_dreame.map_data_parser import DreameMapDataParser

                return DreameMapDataParser(*args, self.device.model)
            if self.api == VacuumApi.VIOMI:
                from vacuum_map_parser_viomi.map_data_parser import ViomiMapDataParser

                return ViomiMapDataParser(*args)
            if self.api == VacuumApi.ROIDMI:
                from vacuum_map_parser_roidmi.map_data_parser import RoidmiMapDataParser

                return RoidmiMapDataParser(*args)
            if self.api == VacuumApi.IJAI:
                from vacuum_map_parser_ijai.map_data_parser import IjaiMapDataParser

                return IjaiMapDataParser(*args)
            if self.api == VacuumApi.XIAOMI:
                from vacuum_map_parser_xiaomi.map_data_parser import XiaomiMapDataParser

                return XiaomiMapDataParser(*args)
        except ImportError as exc:
            raise MapError(
                f"Le parseur pour l'API {self.api.value} n'est pas installé ({exc}). "
                "Lancez: pip install -r requirements.txt"
            ) from exc
        raise MapError(f"API non gérée: {self.api}")

    # ------------------------------------------------------------------ commandes
    def command(self, method: str, params: Any = None) -> Any:
        """Envoie une commande miIO: en local si possible, sinon via le cloud."""
        if self.local is not None:
            try:
                result = self.local.send(method, params)
                self._local_failures = 0
                return result
            except MiioError as exc:
                self._local_failures += 1
                _LOGGER.warning("Commande locale %s échouée (%s), passage par le cloud", method, exc)
                if self._local_failures >= 3:
                    _LOGGER.warning("Accès local désactivé après 3 échecs, utilisation du cloud uniquement")
                    self.local = None
        return self.cloud.rpc(self.device.server, self.device.did, method, [] if params is None else params)

    def get_property(self, siid: int, piid: int) -> Any:
        result = self.command("get_properties", [{"did": self.device.did, "siid": siid, "piid": piid}])
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return result[0].get("value")
        return None

    def call_action(self, siid: int, aiid: int, params: list | None = None) -> Any:
        return self.command("action", {"did": self.device.did, "siid": siid, "aiid": aiid, "in": params or []})

    # ------------------------------------------------------------------ nom de carte
    def get_map_name(self) -> str:
        if self.api == VacuumApi.ROBOROCK:
            return self._roborock_map_name()
        if self.api == VacuumApi.DREAME:
            return self._dreame_map_name()
        if self.api == VacuumApi.XIAOMI:
            return self._xiaomi_map_name()
        return "0"

    def _roborock_map_name(self) -> str:
        delay = 0.2
        for attempt in range(10):
            try:
                result = self.command("get_map_v1")
            except (MiioError, XiaomiCloudError) as exc:
                _LOGGER.warning("get_map_v1 échoué (essai %d): %s", attempt + 1, exc)
                result = None
            if isinstance(result, list) and result and result[0] != "retry":
                return str(result[0])
            time.sleep(delay)
            delay = min(delay * 2, 15)
        raise MapError("Le robot n'a pas fourni de nom de carte (get_map_v1 renvoie 'retry').")

    def _dreame_map_name(self) -> str:
        from vacuum_map_parser_dreame.map_data_parser import DreameMapDataParser

        if self.device.model in DreameMapDataParser.IVs:
            # Modèles à carte chiffrée: la clé est renvoyée avec le nom de la carte.
            for _ in range(5):
                request: dict[str, Any] = {"req_type": 1, "frame_type": "I"}
                if self._robot_stamp:
                    request["time"] = self._robot_stamp
                params = {
                    "did": self.device.did,
                    "siid": 6,
                    "aiid": 1,
                    "in": [{"piid": 2, "value": json.dumps(request, separators=(",", ":"))}],
                }
                result = self.cloud.rpc(self.device.server, self.device.did, "action", params)
                key = extract(result, "out", 1, "value")
                if key:
                    parts = str(key).split(",")
                    path_parts = parts[0].split("/")
                    map_name = path_parts[2] if len(path_parts) > 2 else path_parts[-1]
                    self._enc_key = parts[1] if len(parts) > 1 else None
                    self._robot_stamp = 0
                    return map_name
                self._robot_stamp = extract(result, "out", 2, "value") or 0
                time.sleep(1)
            raise MapError("Le robot Dreame n'a pas renvoyé de carte (réessayez dans quelques secondes).")

        # Autres modèles Dreame: on demande au robot de publier une image complète de la carte.
        try:
            self.call_action(6, 1, [{"piid": 2, "value": '{"frame_type":"I"}'}])
        except (MiioError, XiaomiCloudError) as exc:
            _LOGGER.debug("Action map_view ignorée: %s", exc)
        return "0"

    def _xiaomi_map_name(self) -> str:
        siid, piid = next(
            (prop for models, prop in _XIAOMI_MAP_PROP_OVERRIDES if self.device.model in models),
            _XIAOMI_MAP_PROP_DEFAULT,
        )
        try:
            value = self.get_property(siid, piid)
        except (MiioError, XiaomiCloudError) as exc:
            _LOGGER.warning("Lecture de la propriété carte échouée: %s", exc)
            value = None
        if value is None:
            return "0"
        if isinstance(value, int):
            return str(value)
        map_name = None
        try:
            map_name = json.loads(value).get("obj_name")
        except (ValueError, TypeError, AttributeError):
            if isinstance(value, str) and "/" in value:
                map_name = value
        if not map_name:
            return "0"
        return str(map_name).split("/")[-1]

    # ------------------------------------------------------------------ URL de la carte
    def get_map_url(self, map_name: str) -> str | None:
        server, uid, did = self.device.server, self.device.user_id, self.device.did
        if self.api == VacuumApi.ROBOROCK:
            return self.cloud.get_roborock_map_url(server, map_name)
        if self.api in (VacuumApi.IJAI, VacuumApi.XIAOMI):
            return self.cloud.get_interim_file_url(server, uid, did, map_name, pro=True)
        url = self.cloud.get_interim_file_url(server, uid, did, map_name)
        if url is None:
            url = self.cloud.get_interim_file_url(server, uid, did, map_name, pro=True)
        return url

    # ------------------------------------------------------------------ décodage
    def _ijai_wifi_info_sn(self) -> str:
        if self._wifi_info_sn:
            return self._wifi_info_sn
        for piid in (3, 5):
            try:
                value = self.get_property(1, piid)
            except (MiioError, XiaomiCloudError):
                value = None
            if isinstance(value, str) and len(value) in self.WIFI_SN_LENGTHS and value.isupper():
                self._wifi_info_sn = value
                return value
        value = self.get_property(7, 45)
        if isinstance(value, str):
            for prop in value.split(","):
                cleaned = prop.replace('"', "")
                if str(self.device.user_id) in cleaned:
                    cleaned = cleaned.split(";")[0]
                if len(cleaned) in self.WIFI_SN_LENGTHS and cleaned.isalnum() and cleaned.isupper():
                    self._wifi_info_sn = cleaned
                    return cleaned
        raise MapError("Impossible d'obtenir le numéro de série wifi du robot (nécessaire pour déchiffrer la carte).")

    def decode(self, raw: bytes) -> MapData:
        parser = self._parser
        if self.api == VacuumApi.DREAME:
            return parser.parse(parser.unpack_map(raw, enckey=self._enc_key))
        if self.api == VacuumApi.IJAI:
            if not self.device.mac:
                raise MapError("Adresse MAC du robot inconnue (nécessaire pour déchiffrer la carte).")
            unpacked = parser.unpack_map(
                raw,
                wifi_sn=self._ijai_wifi_info_sn(),
                owner_id=str(self.device.user_id),
                device_id=str(self.device.did),
                model=self.device.model,
                device_mac=self.device.mac,
            )
            return parser.parse(unpacked)
        if self.api == VacuumApi.XIAOMI:
            try:
                raw = base64.decodebytes(json.loads(raw)["data"].encode("latin1"))
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                pass
            unpacked = parser.unpack_map(
                raw.hex(),
                model=self.device.model.replace("xiaomi", "mi"),
                device_id=str(self.device.did),
            )
            return parser.parse(unpacked)
        return parser.parse(parser.unpack_map(raw))

    # ------------------------------------------------------------------ récupération complète
    def fetch(self) -> MapSnapshot:
        """Récupère la carte courante. Se reconnecte automatiquement si la session a expiré."""
        try:
            return self._fetch()
        except SessionExpired:
            _LOGGER.info("Session cloud expirée, reconnexion...")
            self.cloud.login(force=True)
            return self._fetch()

    def _fetch(self) -> MapSnapshot:
        map_name = self.get_map_name()
        _LOGGER.debug("Nom de carte: %s", map_name)
        url = self.get_map_url(map_name)
        if not url:
            raise MapError(
                f"Le cloud n'a pas renvoyé d'URL pour la carte '{map_name}'. "
                "Le robot n'a peut-être pas encore envoyé de carte (lancez un nettoyage) ou le serveur est incorrect."
            )
        raw = self.cloud.download(url)
        if not raw:
            raise MapError("Le téléchargement de la carte a échoué.")
        try:
            map_data = self.decode(raw)
        except MapError:
            raise
        except Exception as exc:  # noqa: BLE001 - les parseurs lèvent des erreurs variées
            raise MapError(f"Décodage de la carte impossible ({type(exc).__name__}: {exc})") from exc
        if map_data.image is None or map_data.image.is_empty:
            raise MapError("La carte reçue est vide.")
        map_data.map_name = map_name
        buffer = io.BytesIO()
        map_data.image.data.save(buffer, format="PNG")
        return MapSnapshot(datetime.now(), map_name, buffer.getvalue(), map_data, raw)
