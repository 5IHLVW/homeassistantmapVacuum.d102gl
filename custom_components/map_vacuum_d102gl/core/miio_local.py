"""Implémentation minimale du protocole local miIO (UDP, port 54321).

Permet d'envoyer des commandes directement au robot sur le réseau local
grâce à son token, sans dépendre de python-miio.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import socket
import time
from typing import Any

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

_LOGGER = logging.getLogger(__name__)


class MiioError(Exception):
    """Erreur de communication locale avec l'appareil."""


class MiioDeviceError(MiioError):
    """L'appareil a répondu par une erreur (commande inconnue, paramètres invalides...)."""


class MiioDevice:
    """Client miIO local très simple (handshake + commandes chiffrées AES-CBC)."""

    PORT = 54321
    HELLO = bytes.fromhex("21310020" + "ff" * 28)
    HANDSHAKE_MAX_AGE = 60  # secondes

    def __init__(self, host: str, token: str, timeout: float = 5.0) -> None:
        if len(token) != 32:
            raise ValueError("Le token doit contenir 32 caractères hexadécimaux")
        self.host = host
        self._token = bytes.fromhex(token)
        self._key = hashlib.md5(self._token).digest()
        self._iv = hashlib.md5(self._key + self._token).digest()
        self._timeout = timeout
        self._device_id: bytes | None = None
        self._stamp = 0
        self._stamp_time = 0.0
        self._msg_id = random.randint(1, 1000)

    # ------------------------------------------------------------------ protocole
    def _handshake(self, sock: socket.socket) -> None:
        sock.sendto(self.HELLO, (self.host, self.PORT))
        data, _ = sock.recvfrom(1024)
        if len(data) < 32 or data[:2] != b"\x21\x31":
            raise MiioError("Réponse de handshake invalide")
        self._device_id = data[8:12]
        self._stamp = int.from_bytes(data[12:16], "big")
        self._stamp_time = time.monotonic()
        _LOGGER.debug("Handshake OK avec %s (device id %s)", self.host, self._device_id.hex())

    def _encrypt(self, payload: bytes) -> bytes:
        return AES.new(self._key, AES.MODE_CBC, self._iv).encrypt(pad(payload, AES.block_size))

    def _decrypt(self, payload: bytes) -> bytes:
        return unpad(AES.new(self._key, AES.MODE_CBC, self._iv).decrypt(payload), AES.block_size)

    def _send_once(self, method: str, params: Any) -> Any:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self._timeout)
            if self._device_id is None or time.monotonic() - self._stamp_time > self.HANDSHAKE_MAX_AGE:
                self._handshake(sock)
            assert self._device_id is not None

            self._msg_id += 1
            message = {"id": self._msg_id, "method": method, "params": [] if params is None else params}
            encrypted = self._encrypt(json.dumps(message, separators=(",", ":")).encode("utf-8"))
            stamp = self._stamp + int(time.monotonic() - self._stamp_time)
            header = (
                b"\x21\x31"
                + (32 + len(encrypted)).to_bytes(2, "big")
                + b"\x00\x00\x00\x00"
                + self._device_id
                + stamp.to_bytes(4, "big")
            )
            checksum = hashlib.md5(header + self._token + encrypted).digest()
            _LOGGER.debug("miIO -> %s: %s", self.host, message)
            sock.sendto(header + checksum + encrypted, (self.host, self.PORT))

            deadline = time.monotonic() + self._timeout
            while time.monotonic() < deadline:
                data, _ = sock.recvfrom(65535)
                if len(data) <= 32:
                    continue  # accusé de réception sans charge utile
                self._stamp = int.from_bytes(data[12:16], "big")
                self._stamp_time = time.monotonic()
                try:
                    text = self._decrypt(data[32:]).rstrip(b"\x00").decode("utf-8", errors="replace")
                    response = json.loads(text)
                except (ValueError, KeyError) as exc:
                    raise MiioError(f"Réponse illisible (token incorrect ?): {exc}") from exc
                _LOGGER.debug("miIO <- %s: %s", self.host, response)
                if response.get("id") != self._msg_id:
                    continue
                if "error" in response:
                    raise MiioDeviceError(f"L'appareil a renvoyé une erreur: {response['error']}")
                return response.get("result")
            raise MiioError("Délai d'attente dépassé")

    # ------------------------------------------------------------------ API publique
    def send(self, method: str, params: Any = None, retries: int = 2) -> Any:
        """Envoie une commande et renvoie le champ ``result`` de la réponse."""
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self._send_once(method, params)
            except MiioDeviceError:
                raise  # réponse valide de l'appareil: inutile de réessayer
            except (socket.timeout, OSError, MiioError) as exc:
                last_error = exc
                self._device_id = None  # force un nouveau handshake
                _LOGGER.debug("Commande locale %s échouée (essai %d): %s", method, attempt + 1, exc)
        raise MiioError(f"{method}: {last_error}")

    def get_property(self, siid: int, piid: int, did: str | None = None) -> Any:
        """Lit une propriété MIoT et renvoie sa valeur."""
        result = self.send("get_properties", [{"did": did or f"{siid}-{piid}", "siid": siid, "piid": piid}])
        if isinstance(result, list) and result:
            return result[0].get("value")
        return None

    def call_action(self, siid: int, aiid: int, params: list | None = None, did: str | None = None) -> Any:
        """Appelle une action MIoT."""
        return self.send(
            "action",
            {"did": did or f"call-{siid}-{aiid}", "siid": siid, "aiid": aiid, "in": params or []},
        )
