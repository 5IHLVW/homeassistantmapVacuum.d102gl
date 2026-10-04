"""Chargement de la configuration depuis le fichier .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"
ENV_EXAMPLE_FILE = PROJECT_DIR / ".env.example"

SUPPORTED_APIS = ("auto", "roborock", "dreame", "viomi", "roidmi", "ijai", "xiaomi")


@dataclass
class Settings:
    """Paramètres de l'application."""

    username: str
    password: str
    server: str | None
    token: str | None
    device_id: str | None
    host: str | None
    api: str
    refresh_seconds: int
    scale: float
    rotate: float
    theme: str
    background: str | None
    room_colors: str | None
    room_borders: bool
    border_color: str | None
    web_host: str
    web_port: int
    save_maps: bool
    maps_dir: Path
    session_file: Path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} doit être un nombre entier (valeur: {value!r})") from exc


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} doit être un nombre (valeur: {value!r})") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "oui", "on")


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_env(values: dict[str, str], env_file: Path | None = None) -> Path:
    """Écrit les valeurs dans le fichier .env en conservant les commentaires et les autres clés."""
    target = env_file or ENV_FILE
    if target.exists():
        template = target.read_text(encoding="utf-8")
    elif ENV_EXAMPLE_FILE.exists():
        template = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    else:
        template = ""
    remaining = dict(values)
    lines: list[str] = []
    for line in template.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                lines.append(f"{key}={_quote(remaining.pop(key))}")
                continue
        lines.append(line)
    for key, value in remaining.items():
        lines.append(f"{key}={_quote(value)}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def load_settings(env_file: Path | None = None) -> Settings:
    """Lit le fichier .env (ou celui passé en paramètre) et renvoie les paramètres."""
    load_dotenv(env_file or ENV_FILE, override=True)

    server = _env("XIAOMI_SERVER")
    if server is not None and server.lower() in ("", "auto"):
        server = None

    api = (_env("VACUUM_API", "auto") or "auto").lower()
    if api not in SUPPORTED_APIS:
        raise ValueError(f"VACUUM_API doit être l'une des valeurs {SUPPORTED_APIS} (valeur: {api!r})")

    token = _env("VACUUM_TOKEN")
    if token is not None and len(token) != 32:
        raise ValueError("VACUUM_TOKEN doit contenir exactement 32 caractères hexadécimaux")

    return Settings(
        username=_env("XIAOMI_USERNAME", "") or "",
        password=_env("XIAOMI_PASSWORD", "") or "",
        server=server.lower() if server else None,
        token=token,
        device_id=_env("VACUUM_DEVICE_ID"),
        host=_env("VACUUM_HOST"),
        api=api,
        refresh_seconds=max(5, _env_int("MAP_REFRESH_SECONDS", 30)),
        scale=_env_float("MAP_SCALE", 3.0),
        rotate=_env_float("MAP_ROTATE", 0.0),
        theme=(_env("MAP_THEME", "clair") or "clair").lower(),
        background=_env("MAP_BACKGROUND"),
        room_colors=_env("MAP_ROOM_COLORS"),
        room_borders=_env_bool("MAP_ROOM_BORDERS", True),
        border_color=_env("MAP_BORDER_COLOR"),
        web_host=_env("WEB_HOST", "127.0.0.1") or "127.0.0.1",
        web_port=_env_int("WEB_PORT", 5000),
        save_maps=_env_bool("SAVE_MAPS", True),
        maps_dir=PROJECT_DIR / "maps",
        session_file=PROJECT_DIR / ".xiaomi_session.json",
    )
