"""Constantes de l'intégration Map Vacuum d102gl."""

from homeassistant.const import Platform

DOMAIN = "map_vacuum_d102gl"
PLATFORMS = [Platform.BUTTON, Platform.CAMERA, Platform.IMAGE]

CONF_SERVER = "server"
CONF_DEVICE_ID = "device_id"
CONF_HOST = "host"
CONF_TOKEN = "token"
CONF_SESSION = "session"
CONF_DEVICE_INFO = "device_info"
CONF_CODE = "code"

CONF_REFRESH_SECONDS = "refresh_seconds"
CONF_SCALE = "scale"
CONF_ROTATE = "rotate"
CONF_USE_LOCAL = "use_local"
CONF_API = "api"

DEFAULT_REFRESH_SECONDS = 30
DEFAULT_SCALE = 3.0
DEFAULT_ROTATE = 0.0

SERVER_AUTO = "auto"
SERVERS = [SERVER_AUTO, "cn", "de", "us", "ru", "tw", "sg", "in", "i2"]
APIS = ["auto", "roborock", "dreame", "viomi", "roidmi", "ijai", "xiaomi"]

STORAGE_VERSION = 1
