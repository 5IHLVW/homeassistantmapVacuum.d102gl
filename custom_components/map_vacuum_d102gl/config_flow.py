"""Flux de configuration : compte Xiaomi, captcha / 2FA, choix du robot, options."""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    ColorRGBSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    APIS,
    CONF_API,
    CONF_BACKGROUND,
    CONF_CODE,
    CONF_DEVICE_ID,
    CONF_DEVICE_INFO,
    CONF_HOST,
    CONF_REFRESH_SECONDS,
    CONF_ROOM_BORDERS,
    CONF_ROOM_COLORS,
    CONF_ROTATE,
    CONF_SCALE,
    CONF_SERVER,
    CONF_SESSION,
    CONF_THEME,
    CONF_TOKEN,
    CONF_USE_LOCAL,
    DEFAULT_REFRESH_SECONDS,
    DEFAULT_ROTATE,
    DEFAULT_SCALE,
    DEFAULT_THEME,
    DOMAIN,
    SERVER_AUTO,
    THEMES,
    SERVERS,
)
from .coordinator import session_store
from .core.vacuum import VacuumApi, detect_api
from .core.xiaomi_cloud import (
    CaptchaRequired,
    DeviceInfo,
    LoginError,
    TwoFactorRequired,
    XiaomiCloudConnector,
    XiaomiCloudError,
)

_LOGGER = logging.getLogger(__name__)

RESULT_CAPTCHA = "captcha"
RESULT_TWO_FACTOR = "two_factor"


class MapVacuumConfigFlow(ConfigFlow, domain=DOMAIN):
    """Assistant de configuration."""

    VERSION = 1

    def __init__(self) -> None:
        self._connector: XiaomiCloudConnector | None = None
        self._username: str = ""
        self._password: str = ""
        self._server: str | None = None
        self._captcha: CaptchaRequired | None = None
        self._two_factor: TwoFactorRequired | None = None
        self._devices: list[DeviceInfo] = []
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MapVacuumOptionsFlow:
        return MapVacuumOptionsFlow()

    # ------------------------------------------------------------------ aides
    async def _async_login(self, action: Callable[..., None], *args: Any) -> str | None:
        """Exécute une étape de connexion. Renvoie None si connecté, sinon une clé d'erreur ou d'étape."""
        try:
            await self.hass.async_add_executor_job(action, *args)
        except CaptchaRequired as exc:
            self._captcha = exc
            return RESULT_CAPTCHA
        except TwoFactorRequired as exc:
            self._two_factor = exc
            return RESULT_TWO_FACTOR
        except LoginError as exc:
            _LOGGER.warning("Connexion Xiaomi refusée: %s", exc)
            return "invalid_auth"
        except XiaomiCloudError as exc:
            _LOGGER.warning("Cloud Xiaomi injoignable: %s", exc)
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Erreur inattendue pendant la connexion Xiaomi")
            return "unknown"
        return None

    async def _async_dispatch(self, result: str | None, errors: dict[str, str]) -> ConfigFlowResult | None:
        """Passe à l'étape suivante selon le résultat de la connexion, ou remplit ``errors``."""
        if result is None:
            return await self._async_after_auth()
        if result == RESULT_CAPTCHA:
            return await self.async_step_captcha()
        if result == RESULT_TWO_FACTOR:
            return await self.async_step_two_factor()
        errors["base"] = result
        return None

    async def _async_after_auth(self) -> ConfigFlowResult:
        assert self._connector is not None
        if self._reauth_entry is not None:
            await session_store(self.hass, self._reauth_entry.entry_id).async_remove()
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data={
                    **self._reauth_entry.data,
                    CONF_PASSWORD: self._password,
                    CONF_SESSION: self._connector.export_session(),
                },
                reason="reauth_successful",
            )
        return await self.async_step_device()

    # ------------------------------------------------------------------ étapes
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            server = user_input.get(CONF_SERVER, SERVER_AUTO)
            self._server = None if server == SERVER_AUTO else server
            self._connector = XiaomiCloudConnector(self._username, self._password, server=self._server)
            result = await self._async_login(self._connector.login)
            if (flow_result := await self._async_dispatch(result, errors)) is not None:
                return flow_result

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=self._username): str,
                vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Required(CONF_SERVER, default=self._server or SERVER_AUTO): SelectSelector(
                    SelectSelectorConfig(options=SERVERS, mode=SelectSelectorMode.DROPDOWN, translation_key="server")
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_captcha(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._connector is not None and self._captcha is not None
        if user_input is not None:
            result = await self._async_login(self._connector.continue_with_captcha, user_input[CONF_CODE])
            if result == RESULT_CAPTCHA and self._captcha.invalid_code:
                errors[CONF_CODE] = "invalid_captcha"
            elif (flow_result := await self._async_dispatch(result, errors)) is not None:
                return flow_result

        image_b64 = base64.b64encode(self._captcha.image).decode()
        return self.async_show_form(
            step_id="captcha",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
            description_placeholders={"captcha_image": image_b64},
        )

    async def async_step_two_factor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._connector is not None and self._two_factor is not None
        if user_input is not None:
            result = await self._async_login(self._connector.continue_with_two_factor, user_input[CONF_CODE])
            if result == "invalid_auth":
                errors[CONF_CODE] = "invalid_code"
            elif (flow_result := await self._async_dispatch(result, errors)) is not None:
                return flow_result

        return self.async_show_form(
            step_id="two_factor",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
            description_placeholders={"url": self._two_factor.url},
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._connector is not None
        errors: dict[str, str] = {}
        if not self._devices:
            try:
                devices = await self.hass.async_add_executor_job(self._connector.get_devices, self._server)
            except XiaomiCloudError as exc:
                _LOGGER.warning("Liste des appareils impossible: %s", exc)
                return self.async_abort(reason="cannot_connect")
            self._devices = [d for d in devices if d.is_vacuum and detect_api(d.model) != VacuumApi.UNSUPPORTED]
            if not self._devices:
                return self.async_abort(reason="no_devices")

        if user_input is not None:
            device = next(d for d in self._devices if d.did == user_input[CONF_DEVICE_ID])
            await self.async_set_unique_id(device.did)
            self._abort_if_unique_id_configured()
            data = {
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_SERVER: device.server,
                CONF_DEVICE_ID: device.did,
                CONF_TOKEN: device.token,
                CONF_HOST: (user_input.get(CONF_HOST) or "").strip() or device.local_ip or "",
                CONF_DEVICE_INFO: device.as_dict(),
                CONF_SESSION: self._connector.export_session(),
            }
            options = {
                CONF_USE_LOCAL: bool(user_input.get(CONF_USE_LOCAL, True)),
                CONF_REFRESH_SECONDS: DEFAULT_REFRESH_SECONDS,
                CONF_SCALE: DEFAULT_SCALE,
                CONF_ROTATE: DEFAULT_ROTATE,
                CONF_API: "auto",
                CONF_THEME: DEFAULT_THEME,
                CONF_ROOM_BORDERS: True,
            }
            return self.async_create_entry(title=device.name or device.model, data=data, options=options)

        choices = [
            SelectOptionDict(value=d.did, label=f"{d.name or d.model} ({d.model}) - {d.server}") for d in self._devices
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID, default=self._devices[0].did): SelectSelector(
                    SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_HOST, default=""): str,
                vol.Optional(CONF_USE_LOCAL, default=True): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="device", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------ ré-authentification
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._username = entry_data[CONF_USERNAME]
        self._server = entry_data.get(CONF_SERVER) or None
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            self._connector = XiaomiCloudConnector(self._username, self._password, server=self._server)
            result = await self._async_login(self._connector.login, True)
            if (flow_result := await self._async_dispatch(result, errors)) is not None:
                return flow_result

        schema = vol.Schema({vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"username": self._username},
        )


class MapVacuumOptionsFlow(OptionsFlow):
    """Options : intervalle, échelle, rotation, couleurs, accès local, API."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_REFRESH_SECONDS, default=current.get(CONF_REFRESH_SECONDS, DEFAULT_REFRESH_SECONDS)
                ): NumberSelector(NumberSelectorConfig(min=5, max=3600, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="s")),
                vol.Required(CONF_SCALE, default=current.get(CONF_SCALE, DEFAULT_SCALE)): NumberSelector(
                    NumberSelectorConfig(min=1, max=10, step=0.5, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_ROTATE, default=current.get(CONF_ROTATE, DEFAULT_ROTATE)): NumberSelector(
                    NumberSelectorConfig(min=0, max=359, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="°")
                ),
                vol.Required(CONF_THEME, default=current.get(CONF_THEME, DEFAULT_THEME)): SelectSelector(
                    SelectSelectorConfig(options=THEMES, mode=SelectSelectorMode.DROPDOWN, translation_key="theme")
                ),
                vol.Optional(CONF_BACKGROUND, description={"suggested_value": current.get(CONF_BACKGROUND)}): ColorRGBSelector(),
                vol.Required(CONF_ROOM_BORDERS, default=current.get(CONF_ROOM_BORDERS, True)): BooleanSelector(),
                vol.Optional(CONF_ROOM_COLORS, description={"suggested_value": current.get(CONF_ROOM_COLORS)}): TextSelector(),
                vol.Required(CONF_USE_LOCAL, default=current.get(CONF_USE_LOCAL, True)): BooleanSelector(),
                vol.Required(CONF_API, default=current.get(CONF_API, "auto")): SelectSelector(
                    SelectSelectorConfig(options=APIS, mode=SelectSelectorMode.DROPDOWN, translation_key="api")
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
