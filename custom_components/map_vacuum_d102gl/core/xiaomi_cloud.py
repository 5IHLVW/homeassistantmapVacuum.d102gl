"""Client pour l'API cloud Xiaomi (Mi Home).

Reprend l'algorithme de connexion et de signature utilisé par
Xiaomi-cloud-tokens-extractor et Xiaomi Cloud Map Extractor (PiotrMachowski).

La connexion ne pose jamais de question dans le terminal : si Xiaomi demande un
captcha ou un code de vérification, une exception ``CaptchaRequired`` ou
``TwoFactorRequired`` est levée et la connexion se poursuit avec
``continue_with_captcha`` / ``continue_with_two_factor``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests
from Crypto.Cipher import ARC4
from urllib.parse import parse_qs, urlparse

_LOGGER = logging.getLogger(__name__)

SERVERS = ["cn", "de", "us", "ru", "tw", "sg", "in", "i2"]
ACCOUNT_URL = "https://account.xiaomi.com"
HTTP_TIMEOUT = 20
SESSION_LIFETIME = timedelta(days=20)


# --------------------------------------------------------------------------- erreurs
class XiaomiCloudError(Exception):
    """Erreur générique de l'API cloud."""


class LoginError(XiaomiCloudError):
    """Connexion impossible."""


class CaptchaRequired(LoginError):
    """Xiaomi demande un captcha: appeler ``continue_with_captcha(code)``."""

    def __init__(self, image: bytes, url: str, invalid_code: bool = False) -> None:
        super().__init__("Captcha requis" if not invalid_code else "Captcha invalide")
        self.image = image
        self.url = url
        self.invalid_code = invalid_code


class TwoFactorRequired(LoginError):
    """Xiaomi demande un code de vérification: appeler ``continue_with_two_factor(code)``."""

    def __init__(self, url: str, context: str) -> None:
        super().__init__("Vérification en deux étapes requise")
        self.url = url
        self.context = context


class SessionExpired(XiaomiCloudError):
    """La session cloud n'est plus valide, il faut se reconnecter."""


class RpcError(XiaomiCloudError):
    """L'appareil (via le cloud) a renvoyé une erreur."""


# --------------------------------------------------------------------------- modèles
@dataclass
class DeviceInfo:
    did: str
    name: str
    model: str
    token: str
    local_ip: str | None
    mac: str | None
    server: str
    user_id: str
    online: bool | None = None

    @property
    def is_vacuum(self) -> bool:
        return ".vacuum." in self.model or self.model.startswith(("rockrobo.vacuum", "roborock.vacuum"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "did": self.did,
            "name": self.name,
            "model": self.model,
            "token": self.token,
            "local_ip": self.local_ip,
            "mac": self.mac,
            "server": self.server,
            "user_id": self.user_id,
            "online": self.online,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceInfo:
        return cls(
            did=str(data["did"]),
            name=str(data.get("name", "")),
            model=str(data.get("model", "")),
            token=str(data.get("token", "") or ""),
            local_ip=data.get("local_ip"),
            mac=data.get("mac"),
            server=str(data.get("server", "de")),
            user_id=str(data.get("user_id", "")),
            online=data.get("online"),
        )


# --------------------------------------------------------------------------- utilitaires
def extract(obj: Any, *path: str | int) -> Any:
    """Extrait une valeur imbriquée (clés de dict et index de liste). None si absent."""
    for key in path:
        if obj is None:
            return None
        if isinstance(key, int):
            if isinstance(obj, list) and -len(obj) <= key < len(obj):
                obj = obj[key]
            else:
                return None
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


def generate_agent() -> str:
    agent_id = "".join(chr(random.randint(65, 69)) for _ in range(13))
    random_text = "".join(chr(random.randint(97, 122)) for _ in range(18))
    return f"{random_text}-{agent_id} APP/com.xiaomi.mihome APPV/10.5.201"


def generate_device_id() -> str:
    return "".join(chr(random.randint(97, 122)) for _ in range(6))


def generate_nonce(millis: int) -> str:
    nonce_bytes = os.urandom(8) + int(millis / 60000).to_bytes(4, byteorder="big")
    return base64.b64encode(nonce_bytes).decode()


def to_json(response_text: str) -> Any:
    return json.loads(response_text.replace("&&&START&&&", ""))


def encrypt_rc4(password: str, payload: str) -> str:
    r = ARC4.new(base64.b64decode(password))
    r.encrypt(bytes(1024))
    return base64.b64encode(r.encrypt(payload.encode())).decode()


def decrypt_rc4(password: str, payload: str) -> bytes:
    r = ARC4.new(base64.b64decode(password))
    r.encrypt(bytes(1024))
    return r.encrypt(base64.b64decode(payload))


def generate_enc_signature(url: str, method: str, signed_nonce: str, params: dict[str, str]) -> str:
    signature_params = [str(method).upper(), url.split("com")[1].replace("/app/", "/")]
    for k, v in params.items():
        signature_params.append(f"{k}={v}")
    signature_params.append(signed_nonce)
    signature_string = "&".join(signature_params)
    return base64.b64encode(hashlib.sha1(signature_string.encode("utf-8")).digest()).decode()


def generate_enc_params(
    url: str, method: str, signed_nonce: str, nonce: str, params: dict[str, str], ssecurity: str
) -> dict[str, str]:
    params = dict(params)
    params["rc4_hash__"] = generate_enc_signature(url, method, signed_nonce, params)
    for k, v in params.items():
        params[k] = encrypt_rc4(signed_nonce, v)
    params.update(
        {
            "signature": generate_enc_signature(url, method, signed_nonce, params),
            "ssecurity": ssecurity,
            "_nonce": nonce,
        }
    )
    return params


# --------------------------------------------------------------------------- connecteur
class XiaomiCloudConnector:
    """Connexion au cloud Xiaomi et appels d'API signés."""

    def __init__(
        self,
        username: str,
        password: str,
        server: str | None = None,
        session_file: Path | str | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self.server = server
        self._session_file = Path(session_file) if session_file else None
        # Appelé avec les données de session à chaque (re)connexion réussie.
        self.session_listener: Callable[[dict[str, Any]], None] | None = None

        self._agent = generate_agent()
        self._device_id = generate_device_id()
        self._session = requests.Session()
        self._sign: str | None = None
        self._ssecurity: str | None = None
        self.user_id: str | None = None
        self._c_user_id: str | None = None
        self._location: str | None = None
        self._service_token: str | None = None
        self._expiration: datetime | None = None
        self._two_factor_context: str | None = None

    # ------------------------------------------------------------------ session
    @property
    def is_logged_in(self) -> bool:
        return bool(self._service_token and self._ssecurity and self.user_id)

    def _session_valid(self) -> bool:
        if not self.is_logged_in:
            return False
        return self._expiration is None or self._expiration > datetime.now() + timedelta(hours=1)

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._agent, "Content-Type": "application/x-www-form-urlencoded"}

    def _new_http_session(self) -> None:
        self._session.close()
        self._session = requests.Session()
        for domain in ("mi.com", "xiaomi.com"):
            self._session.cookies.set("sdkVersion", "accountsdk-18.8.15", domain=domain)
            self._session.cookies.set("deviceId", self._device_id, domain=domain)

    def _get_cookie(self, name: str, *domains: str) -> str | None:
        for domain in domains:
            try:
                value = self._session.cookies.get(name, domain=domain)
            except Exception:  # CookieConflictError
                value = None
            if value:
                return value
        try:
            return self._session.cookies.get(name)
        except Exception:
            for cookie in self._session.cookies:
                if cookie.name == name and cookie.value:
                    return cookie.value
        return None

    def _install_service_token_cookies(self) -> None:
        if not self._service_token:
            return
        for domain in (".api.io.mi.com", ".io.mi.com", ".mi.com"):
            self._session.cookies.set("serviceToken", self._service_token, domain=domain)
            self._session.cookies.set("yetAnotherServiceToken", self._service_token, domain=domain)

    def export_session(self) -> dict[str, Any]:
        """Données de session réutilisables (à conserver en lieu sûr)."""
        return {
            "username": self._username,
            "agent": self._agent,
            "device_id": self._device_id,
            "ssecurity": self._ssecurity,
            "user_id": self.user_id,
            "c_user_id": self._c_user_id,
            "service_token": self._service_token,
            "expiration": self._expiration.isoformat() if self._expiration else None,
        }

    def import_session(self, data: dict[str, Any] | None) -> bool:
        """Restaure une session exportée. Renvoie False si elle est inutilisable."""
        if not data or data.get("username") != self._username:
            return False
        if not (data.get("ssecurity") and data.get("service_token") and data.get("user_id")):
            return False
        expiration: datetime | None = None
        if data.get("expiration"):
            try:
                expiration = datetime.fromisoformat(data["expiration"])
            except ValueError:
                expiration = None
        if expiration and expiration < datetime.now() + timedelta(hours=1):
            return False
        self._agent = data.get("agent") or generate_agent()
        self._device_id = data.get("device_id") or generate_device_id()
        self._ssecurity = data["ssecurity"]
        self.user_id = str(data["user_id"])
        self._c_user_id = data.get("c_user_id")
        self._service_token = data["service_token"]
        self._expiration = expiration
        self._new_http_session()
        self._install_service_token_cookies()
        return True

    def _save_session(self) -> None:
        data = self.export_session()
        if self._session_file:
            try:
                self._session_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                _LOGGER.debug("Session enregistrée dans %s", self._session_file)
            except OSError as exc:
                _LOGGER.warning("Impossible d'enregistrer la session: %s", exc)
        if self.session_listener is not None:
            try:
                self.session_listener(data)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("session_listener a échoué: %s", exc)

    def _restore_session_file(self) -> bool:
        if not self._session_file or not self._session_file.exists():
            return False
        try:
            data = json.loads(self._session_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return self.import_session(data)

    def forget_session(self) -> None:
        self._service_token = None
        self._ssecurity = None
        if self._session_file and self._session_file.exists():
            try:
                self._session_file.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ login
    def login(self, force: bool = False) -> None:
        """Se connecte, en réutilisant une session enregistrée ou importée si possible.

        Peut lever ``CaptchaRequired``, ``TwoFactorRequired`` ou ``LoginError``.
        """
        if not force:
            if self._session_valid():
                _LOGGER.debug("Session Xiaomi déjà valide")
                return
            if self._restore_session_file():
                _LOGGER.info("Session Xiaomi restaurée depuis %s", self._session_file)
                return

        _LOGGER.info("Connexion au compte Xiaomi %s...", self._username)
        self._agent = generate_agent()
        self._device_id = generate_device_id()
        self._sign = None
        self._ssecurity = None
        self._service_token = None
        self._location = None
        self._expiration = None
        self._two_factor_context = None
        self._new_http_session()

        if not self._login_step_1():
            raise LoginError("Étape 1 échouée: nom d'utilisateur invalide ou service indisponible.")
        if self._sign:
            self._login_step_2()
        self._finish_login()

    def continue_with_captcha(self, code: str) -> None:
        """Poursuit la connexion avec le code du captcha."""
        if not self._sign:
            raise LoginError("Aucune connexion en attente de captcha.")
        self._login_step_2(captcha_code=code.strip())
        self._finish_login()

    def continue_with_two_factor(self, code: str) -> None:
        """Poursuit la connexion avec le code de vérification reçu par e-mail."""
        if not self._two_factor_context:
            raise LoginError("Aucune connexion en attente de vérification.")
        self._verify_two_factor(code.strip())
        self._finish_login()

    def _finish_login(self) -> None:
        if self._location and not self._service_token and not self._login_step_3():
            raise LoginError("Étape 3 échouée: impossible d'obtenir le service token.")
        if not self.is_logged_in:
            raise LoginError("Connexion incomplète (service token ou ssecurity manquant).")
        if self._expiration is None:
            self._expiration = datetime.now() + SESSION_LIFETIME
        self._two_factor_context = None
        _LOGGER.info("Connecté (userId=%s)", self.user_id)
        self._save_session()

    def _login_step_1(self) -> bool:
        url = f"{ACCOUNT_URL}/pass/serviceLogin?sid=xiaomiio&_json=true"
        try:
            response = self._session.get(url, headers=self._headers(), cookies={"userId": self._username}, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise LoginError(f"Erreur réseau lors de la connexion: {exc}") from exc
        _LOGGER.debug("login step 1: %s %s", response.status_code, response.text[:300])
        if response.status_code != 200:
            return False
        try:
            data = to_json(response.text)
        except ValueError:
            return False
        if "_sign" in data:
            self._sign = data["_sign"]
            return True
        if "ssecurity" in data:  # déjà connecté
            self._ssecurity = data["ssecurity"]
            self.user_id = str(data.get("userId"))
            self._c_user_id = data.get("cUserId")
            self._location = data.get("location")
            return True
        return False

    def _login_step_2(self, captcha_code: str | None = None) -> None:
        url = f"{ACCOUNT_URL}/pass/serviceLoginAuth2"
        fields = {
            "sid": "xiaomiio",
            "hash": hashlib.md5(self._password.encode()).hexdigest().upper(),
            "callback": "https://sts.api.io.mi.com/sts",
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "user": self._username,
            "_sign": self._sign,
            "_json": "true",
        }
        if captcha_code:
            fields["captCode"] = captcha_code
        try:
            response = self._session.post(url, headers=self._headers(), params=fields, allow_redirects=False, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise LoginError(f"Erreur réseau lors de la connexion: {exc}") from exc
        _LOGGER.debug("login step 2: %s %s", response.status_code, response.text[:500])
        if response.status_code != 200:
            raise LoginError(f"Étape 2 échouée (HTTP {response.status_code}).")
        data = to_json(response.text)

        if data.get("captchaUrl"):
            image = self._fetch_captcha(data["captchaUrl"])
            raise CaptchaRequired(image, data["captchaUrl"], invalid_code=captcha_code is not None)
        if data.get("code") == 87001:
            raise LoginError("Captcha invalide.")

        if "ssecurity" in data and len(str(data["ssecurity"])) > 4:
            self._ssecurity = data["ssecurity"]
            self.user_id = str(data.get("userId"))
            self._c_user_id = data.get("cUserId")
            self._location = data.get("location")
            for cookie in response.cookies:
                if cookie.name == "userId" and cookie.expires:
                    self._expiration = datetime.fromtimestamp(cookie.expires)
            return

        if data.get("notificationUrl"):
            self._start_two_factor(data["notificationUrl"])  # lève TwoFactorRequired

        raise LoginError(f"Identifiant ou mot de passe refusé par Xiaomi ({data.get('desc') or data.get('code')}).")

    def _login_step_3(self) -> bool:
        assert self._location
        try:
            response = self._session.get(self._location, headers=self._headers(), timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise LoginError(f"Erreur réseau lors de la connexion: {exc}") from exc
        _LOGGER.debug("login step 3: %s", response.status_code)
        if response.status_code != 200:
            return False
        self._service_token = response.cookies.get("serviceToken") or self._get_cookie("serviceToken")
        return bool(self._service_token)

    def _fetch_captcha(self, captcha_url: str) -> bytes:
        if captcha_url.startswith("/"):
            captcha_url = ACCOUNT_URL + captcha_url
        try:
            response = self._session.get(captcha_url, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise LoginError(f"Impossible de télécharger le captcha: {exc}") from exc
        if response.status_code != 200:
            raise LoginError(f"Impossible de télécharger le captcha (HTTP {response.status_code}).")
        return response.content

    def _start_two_factor(self, notification_url: str) -> None:
        """Démarre la vérification par e-mail puis lève ``TwoFactorRequired``."""
        if notification_url.startswith("/"):
            notification_url = ACCOUNT_URL + notification_url
        headers = self._headers()
        _LOGGER.debug("2FA: ouverture de %s", notification_url)
        self._session.get(notification_url, headers=headers, timeout=HTTP_TIMEOUT)

        query = parse_qs(urlparse(notification_url).query)
        if "context" not in query:
            raise LoginError(
                "Xiaomi demande une vérification supplémentaire. Ouvrez cette URL dans un navigateur, "
                f"validez, puis réessayez : {notification_url}"
            )
        context = query["context"][0]
        list_params = {"sid": "xiaomiio", "context": context, "_locale": "en_US"}
        self._session.get(f"{ACCOUNT_URL}/identity/list", params=list_params, headers=headers, timeout=HTTP_TIMEOUT)

        send_params = {
            "_dc": str(int(time.time() * 1000)),
            "sid": "xiaomiio",
            "context": context,
            "mask": "0",
            "_locale": "en_US",
        }
        send_data = {"retry": "0", "icode": "", "_json": "true", "ick": self._get_cookie("ick") or ""}
        response = self._session.post(
            f"{ACCOUNT_URL}/identity/auth/sendEmailTicket", params=send_params, data=send_data, headers=headers, timeout=HTTP_TIMEOUT
        )
        _LOGGER.debug("2FA sendEmailTicket: %s %s", response.status_code, response.text[:300])
        self._two_factor_context = context
        raise TwoFactorRequired(notification_url, context)

    def _verify_two_factor(self, code: str) -> None:
        headers = self._headers()
        context = self._two_factor_context or ""
        verify_params = {
            "_flag": "8",
            "_json": "true",
            "sid": "xiaomiio",
            "context": context,
            "mask": "0",
            "_locale": "en_US",
        }
        verify_data = {"_flag": "8", "ticket": code, "trust": "false", "_json": "true", "ick": self._get_cookie("ick") or ""}
        response = self._session.post(
            f"{ACCOUNT_URL}/identity/auth/verifyEmail", params=verify_params, data=verify_data, headers=headers, timeout=HTTP_TIMEOUT
        )
        if response.status_code != 200:
            raise LoginError(f"Vérification du code échouée (HTTP {response.status_code}).")

        finish_location: str | None = None
        try:
            finish_location = response.json().get("location")
        except ValueError:
            finish_location = response.headers.get("Location")
            if not finish_location and response.text:
                match = re.search(r"https://account\.xiaomi\.com/identity/result/check\?[^\"']+", response.text)
                if match:
                    finish_location = match.group(0)

        if not finish_location:
            r0 = self._session.get(
                f"{ACCOUNT_URL}/identity/result/check",
                params={"sid": "xiaomiio", "context": context, "_locale": "en_US"},
                headers=headers,
                allow_redirects=False,
                timeout=HTTP_TIMEOUT,
            )
            if r0.status_code in (301, 302) and r0.headers.get("Location"):
                finish_location = r0.url if "serviceLoginAuth2/end" in r0.url else r0.headers["Location"]
        if not finish_location:
            raise LoginError("Code de vérification refusé ou expiré.")

        if "identity/result/check" in finish_location:
            r = self._session.get(finish_location, headers=headers, allow_redirects=False, timeout=HTTP_TIMEOUT)
            end_url = r.headers.get("Location")
        else:
            end_url = finish_location
        if not end_url:
            raise LoginError("Vérification: URL de fin introuvable.")

        r = self._session.get(end_url, headers=headers, allow_redirects=False, timeout=HTTP_TIMEOUT)
        if r.status_code == 200 and "Xiaomi Account - Tips" in r.text:
            r = self._session.get(end_url, headers=headers, allow_redirects=False, timeout=HTTP_TIMEOUT)

        extension_pragma = r.headers.get("extension-pragma")
        if extension_pragma:
            try:
                ssecurity = json.loads(extension_pragma).get("ssecurity")
                if ssecurity:
                    self._ssecurity = ssecurity
            except ValueError:
                pass
        if not self._ssecurity:
            raise LoginError("Vérification: ssecurity absent de la réponse.")

        sts_url = r.headers.get("Location")
        if not sts_url and r.text:
            idx = r.text.find("https://sts.api.io.mi.com/sts")
            if idx != -1:
                end = r.text.find('"', idx)
                sts_url = r.text[idx : end if end != -1 else idx + 300]
        if not sts_url:
            raise LoginError("Vérification: redirection STS absente.")

        r = self._session.get(sts_url, headers=headers, allow_redirects=True, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            raise LoginError(f"Vérification: STS a échoué (HTTP {r.status_code}).")

        self._service_token = self._get_cookie("serviceToken", ".sts.api.io.mi.com")
        if not self._service_token:
            raise LoginError("Vérification: serviceToken introuvable.")
        self._install_service_token_cookies()
        self.user_id = self.user_id or self._get_cookie("userId", ".xiaomi.com", ".sts.api.io.mi.com")
        self._c_user_id = self._c_user_id or self._get_cookie("cUserId", ".xiaomi.com", ".sts.api.io.mi.com")
        self._expiration = datetime.now() + SESSION_LIFETIME
        self._location = None

    # ------------------------------------------------------------------ API signée
    @staticmethod
    def get_api_url(server: str) -> str:
        return "https://" + ("" if server == "cn" else server + ".") + "api.io.mi.com/app"

    def _signed_nonce(self, nonce: str) -> str:
        assert self._ssecurity
        digest = hashlib.sha256(base64.b64decode(self._ssecurity) + base64.b64decode(nonce)).digest()
        return base64.b64encode(digest).decode("utf-8")

    def execute_api_call_encrypted(self, url: str, params: dict[str, str]) -> Any:
        if not self.is_logged_in:
            raise SessionExpired("Non connecté au cloud Xiaomi")
        headers = {
            "Accept-Encoding": "identity",
            "User-Agent": self._agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
        }
        cookies = {
            "userId": str(self.user_id),
            "yetAnotherServiceToken": str(self._service_token),
            "serviceToken": str(self._service_token),
            "locale": "en_GB",
            "timezone": "GMT+02:00",
            "is_daylight": "1",
            "dst_offset": "3600000",
            "channel": "MI_APP_STORE",
        }
        if self._c_user_id:
            cookies["cUserId"] = str(self._c_user_id)
        millis = round(time.time() * 1000)
        nonce = generate_nonce(millis)
        signed_nonce = self._signed_nonce(nonce)
        fields = generate_enc_params(url, "POST", signed_nonce, nonce, params, self._ssecurity or "")
        try:
            response = self._session.post(url, headers=headers, cookies=cookies, params=fields, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise XiaomiCloudError(f"Erreur réseau ({url}): {exc}") from exc
        if response.status_code == 200:
            decoded = decrypt_rc4(self._signed_nonce(fields["_nonce"]), response.text)
            try:
                result = json.loads(decoded)
            except ValueError as exc:
                raise XiaomiCloudError(f"Réponse illisible de {url}") from exc
            _LOGGER.debug("API %s -> %s", url, str(result)[:500])
            return result
        if response.status_code in (401, 403):
            raise SessionExpired(f"Session cloud refusée (HTTP {response.status_code})")
        _LOGGER.debug("API %s -> HTTP %s: %s", url, response.status_code, response.text[:300])
        return None

    # ------------------------------------------------------------------ appareils
    def get_devices(self, server: str | None = None) -> list[DeviceInfo]:
        """Liste les appareils du compte (appareils propres + partagés)."""
        chosen = server or self.server
        servers = [chosen] if chosen else SERVERS
        devices: list[DeviceInfo] = []
        for srv in servers:
            try:
                devices.extend(self._get_devices_from_server(srv))
            except SessionExpired:
                raise
            except XiaomiCloudError as exc:
                _LOGGER.debug("Serveur %s ignoré: %s", srv, exc)
        return devices

    def _get_devices_from_server(self, server: str) -> list[DeviceInfo]:
        devices: list[DeviceInfo] = []
        seen: set[str] = set()

        url = self.get_api_url(server) + "/home/device_list"
        response = self.execute_api_call_encrypted(url, {"data": '{"getVirtualModel":false,"getHuamiDevices":0}'})
        for raw in extract(response, "result", "list") or []:
            device = self._device_from_raw(raw, server, str(raw.get("uid") or self.user_id))
            devices.append(device)
            seen.add(device.did)

        for home_id, owner in self._get_homes(server):
            for device in self._get_devices_from_home(server, home_id, owner):
                if device.did not in seen:
                    devices.append(device)
                    seen.add(device.did)
        return devices

    def _get_homes(self, server: str) -> list[tuple[int, int]]:
        url = self.get_api_url(server) + "/v2/homeroom/gethome"
        params = {"data": '{"fg": true, "fetch_share": true, "fetch_share_dev": true, "limit": 300, "app_ver": 7}'}
        try:
            response = self.execute_api_call_encrypted(url, params)
        except SessionExpired:
            raise
        except XiaomiCloudError as exc:
            _LOGGER.debug("gethome %s: %s", server, exc)
            return []
        homes: list[tuple[int, int]] = []
        for key in ("homelist", "share_home_list"):
            for home in extract(response, "result", key) or []:
                try:
                    homes.append((int(home["id"]), int(home["uid"])))
                except (KeyError, TypeError, ValueError):
                    continue
        return homes

    def _get_devices_from_home(self, server: str, home_id: int, owner_id: int) -> list[DeviceInfo]:
        url = self.get_api_url(server) + "/v2/home/home_device_list"
        params = {
            "data": json.dumps(
                {
                    "home_id": home_id,
                    "home_owner": owner_id,
                    "limit": 200,
                    "get_split_device": True,
                    "support_smart_home": True,
                }
            )
        }
        try:
            response = self.execute_api_call_encrypted(url, params)
        except SessionExpired:
            raise
        except XiaomiCloudError as exc:
            _LOGGER.debug("home_device_list %s/%s: %s", server, home_id, exc)
            return []
        return [
            self._device_from_raw(raw, server, str(owner_id))
            for raw in extract(response, "result", "device_info") or []
        ]

    @staticmethod
    def _device_from_raw(raw: dict[str, Any], server: str, user_id: str) -> DeviceInfo:
        return DeviceInfo(
            did=str(raw.get("did", "")),
            name=str(raw.get("name", "")),
            model=str(raw.get("model", "")),
            token=str(raw.get("token", "") or ""),
            local_ip=raw.get("localip") or None,
            mac=raw.get("mac") or None,
            server=server,
            user_id=user_id,
            online=raw.get("isOnline"),
        )

    def find_device(
        self, token: str | None = None, device_id: str | None = None, server: str | None = None
    ) -> tuple[DeviceInfo | None, list[DeviceInfo]]:
        """Recherche un appareil par token ou identifiant. Renvoie (appareil, tous les appareils)."""
        devices = self.get_devices(server)
        for device in devices:
            if device_id and device.did == str(device_id):
                return device, devices
            if token and device.token.casefold() == token.casefold():
                return device, devices
        return None, devices

    # ------------------------------------------------------------------ commandes et cartes
    def rpc(self, server: str, did: str, method: str, params: Any) -> Any:
        """Envoie une commande miIO au robot via le cloud et renvoie ``result``."""
        data = json.dumps({"method": method, "params": params}, separators=(",", ":"))
        last_response = None
        for endpoint in ("/v2/home/rpc/", "/home/rpc/"):
            response = self.execute_api_call_encrypted(self.get_api_url(server) + endpoint + did, {"data": data})
            if response is None:
                continue
            last_response = response
            if response.get("code") == 0 and "result" in response:
                return response["result"]
            break
        if last_response is None:
            raise RpcError(f"Le cloud n'a pas répondu à la commande {method}")
        raise RpcError(f"Commande {method} refusée: {last_response.get('message') or last_response}")

    def get_roborock_map_url(self, server: str, map_name: str) -> str | None:
        url = self.get_api_url(server) + "/home/getmapfileurl"
        response = self.execute_api_call_encrypted(url, {"data": json.dumps({"obj_name": map_name})})
        return extract(response, "result", "url")

    def get_interim_file_url(self, server: str, user_id: str, did: str, map_name: str, pro: bool = False) -> str | None:
        endpoint = "/v2/home/get_interim_file_url_pro" if pro else "/v2/home/get_interim_file_url"
        url = self.get_api_url(server) + endpoint
        response = self.execute_api_call_encrypted(url, {"data": json.dumps({"obj_name": f"{user_id}/{did}/{map_name}"})})
        return extract(response, "result", "url")

    def download(self, url: str) -> bytes | None:
        try:
            response = self._session.get(url, timeout=30)
        except requests.RequestException as exc:
            _LOGGER.warning("Téléchargement échoué: %s", exc)
            return None
        if response.status_code == 200:
            return response.content
        _LOGGER.warning("Téléchargement échoué (HTTP %s)", response.status_code)
        return None
