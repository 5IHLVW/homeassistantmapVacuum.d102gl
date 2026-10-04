#!/usr/bin/env python3
"""Point d'entrée : affiche la carte de l'aspirateur robot Xiaomi.

    python run.py              # interface web (http://127.0.0.1:5000), fenêtre de configuration au premier lancement
    python run.py --setup      # rouvre la fenêtre de configuration
    python run.py --devices    # liste les appareils du compte Xiaomi
    python run.py --once       # enregistre la carte dans maps/ et quitte
    python run.py --debug      # journaux détaillés
    python run.py --relogin    # force une nouvelle connexion au cloud
    python run.py --no-gui     # pas de fenêtres : tout se passe dans le terminal
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import threading
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

try:
    from vacuum_map import gui
    from vacuum_map.config import ENV_EXAMPLE_FILE, ENV_FILE, Settings, load_settings, write_env
    from vacuum_map.style import MapStyle
    from vacuum_map.vacuum import MapError, VacuumApi, VacuumMapService, detect_api
    from vacuum_map.web import MapUpdater, create_app, save_snapshot
    from vacuum_map.xiaomi_cloud import (
        CaptchaRequired,
        DeviceInfo,
        LoginError,
        SessionExpired,
        TwoFactorRequired,
        XiaomiCloudConnector,
        XiaomiCloudError,
    )
except ImportError as exc:  # dépendances manquantes
    print(f"Dépendance manquante ({exc}).")
    print("Installez les dépendances avec :  python -m pip install -r requirements.txt")
    print("(pensez à activer l'environnement virtuel .venv s'il existe)")
    sys.exit(1)

_LOGGER = logging.getLogger("run")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Affiche la carte d'un aspirateur robot Xiaomi.")
    parser.add_argument("--setup", action="store_true", help="ouvre la fenêtre de configuration (même si .env existe)")
    parser.add_argument("--devices", action="store_true", help="liste les appareils du compte et quitte")
    parser.add_argument("--once", action="store_true", help="récupère la carte une seule fois, l'enregistre dans maps/ et quitte")
    parser.add_argument("--debug", action="store_true", help="journaux détaillés")
    parser.add_argument("--relogin", action="store_true", help="ignore la session enregistrée et se reconnecte")
    parser.add_argument("--no-local", action="store_true", help="n'utilise jamais l'accès local (cloud uniquement)")
    parser.add_argument("--no-gui", action="store_true", help="pas de fenêtres, tout se passe dans le terminal")
    parser.add_argument("--no-browser", action="store_true", help="n'ouvre pas le navigateur automatiquement")
    parser.add_argument("--env", type=Path, default=None, help="fichier .env à utiliser (défaut: .env du projet)")
    parser.add_argument("--host", default=None, help="adresse d'écoute du serveur web (défaut: WEB_HOST)")
    parser.add_argument("--port", type=int, default=None, help="port du serveur web (défaut: WEB_PORT)")
    return parser.parse_args()


# ---------------------------------------------------------------------- configuration
def settings_to_form(settings: Settings | None) -> dict[str, str]:
    if settings is None:
        return {"XIAOMI_SERVER": "de"}
    return {
        "XIAOMI_USERNAME": settings.username,
        "XIAOMI_PASSWORD": settings.password,
        "XIAOMI_SERVER": settings.server or "",
        "VACUUM_TOKEN": settings.token or "",
        "VACUUM_HOST": settings.host or "",
        "MAP_REFRESH_SECONDS": str(settings.refresh_seconds),
        "MAP_SCALE": f"{settings.scale:g}",
        "MAP_THEME": settings.theme,
        "MAP_BACKGROUND": settings.background or "",
    }


def configure_with_dialog(env_path: Path, current: Settings | None) -> bool:
    """Ouvre la fenêtre de configuration et enregistre le résultat dans .env."""
    values = gui.ask_settings(settings_to_form(current))
    if values is None:
        print("Configuration annulée.")
        return False
    path = write_env(values, env_path)
    print(f"Configuration enregistrée : {path}")
    return True


def ensure_env_file(env_path: Path | None) -> bool:
    """Mode terminal : crée .env à partir de .env.example s'il n'existe pas."""
    target = env_path or ENV_FILE
    if target.exists():
        return True
    if env_path is None and ENV_EXAMPLE_FILE.exists():
        shutil.copy(ENV_EXAMPLE_FILE, ENV_FILE)
        print(f"Fichier de configuration créé : {ENV_FILE}")
        print("Renseignez XIAOMI_USERNAME, XIAOMI_PASSWORD (et le reste si besoin) puis relancez :  python run.py")
        return False
    print(f"Fichier de configuration introuvable : {target}")
    return False


def load_or_configure(args: argparse.Namespace, use_gui: bool) -> Settings | None:
    """Charge la configuration, en ouvrant la fenêtre si elle est absente ou incomplète."""
    env_path = args.env or ENV_FILE
    settings: Settings | None = None
    if env_path.exists():
        try:
            settings = load_settings(env_path)
        except ValueError as exc:
            print(f"Configuration invalide : {exc}")
            if not use_gui:
                return None

    incomplete = settings is None or not settings.username or not settings.password
    if args.setup or incomplete:
        if use_gui:
            if not configure_with_dialog(env_path, settings):
                return None
            settings = load_settings(env_path)
        else:
            if not ensure_env_file(args.env):
                return None
            settings = load_settings(env_path)
        if not settings.username or not settings.password:
            print(f"Renseignez XIAOMI_USERNAME et XIAOMI_PASSWORD dans {env_path}")
            return None
    return settings


# ---------------------------------------------------------------------- connexion
def login_interactive(cloud: XiaomiCloudConnector, force: bool = False, use_gui: bool = False) -> None:
    """Connexion au cloud, avec demande du captcha ou du code 2FA (fenêtre ou terminal)."""
    pending: CaptchaRequired | TwoFactorRequired | None = None
    try:
        cloud.login(force=force)
        return
    except (CaptchaRequired, TwoFactorRequired) as exc:
        pending = exc

    while pending is not None:
        try:
            if isinstance(pending, CaptchaRequired):
                if use_gui:
                    code = gui.ask_captcha(pending.image, pending.invalid_code)
                else:
                    captcha_path = PROJECT_DIR / "captcha.png"
                    captcha_path.write_bytes(pending.image)
                    print()
                    if pending.invalid_code:
                        print("Captcha incorrect, nouvel essai.")
                    print("Xiaomi demande un captcha. Ouvrez l'image suivante et recopiez le code :")
                    print(f"  {captcha_path}")
                    code = input("Code captcha (sensible à la casse) : ")
                if not code:
                    raise LoginError("Connexion annulée (captcha non saisi).")
                cloud.continue_with_captcha(code)
            else:
                if use_gui:
                    code = gui.ask_two_factor(pending.url)
                else:
                    print()
                    print("Xiaomi demande une vérification en deux étapes : un code vient d'être envoyé par e-mail.")
                    print(f"(Si vous ne recevez rien, ouvrez {pending.url} dans un navigateur, validez, puis relancez.)")
                    code = input("Code de vérification : ")
                if not code:
                    raise LoginError("Connexion annulée (code de vérification non saisi).")
                cloud.continue_with_two_factor(code)
            pending = None
        except (CaptchaRequired, TwoFactorRequired) as exc:
            pending = exc


def with_relogin(cloud: XiaomiCloudConnector, action, use_gui: bool = False):
    """Exécute une action cloud, en se reconnectant une fois si la session a expiré."""
    try:
        return action()
    except SessionExpired:
        _LOGGER.info("Session cloud expirée, reconnexion...")
        cloud.forget_session()
        login_interactive(cloud, force=True, use_gui=use_gui)
        return action()


# ---------------------------------------------------------------------- appareils
def print_devices(devices: list[DeviceInfo]) -> None:
    if not devices:
        print("Aucun appareil trouvé sur ce compte. Vérifiez XIAOMI_SERVER (cn, de, us, ru, tw, sg, in, i2).")
        return
    print()
    print(f"{'Nom':<28} {'Modèle':<28} {'API':<10} {'Serveur':<7} {'IP locale':<15} {'DID':<14} Token")
    print("-" * 140)
    for d in sorted(devices, key=lambda x: (not x.is_vacuum, x.name)):
        api = detect_api(d.model).value if d.is_vacuum else "-"
        print(f"{d.name[:27]:<28} {d.model[:27]:<28} {api:<10} {d.server:<7} {(d.local_ip or '-'):<15} {d.did:<14} {d.token or '-'}")
    print()


def select_device(cloud: XiaomiCloudConnector, settings: Settings, use_gui: bool) -> DeviceInfo:
    device, devices = with_relogin(
        cloud,
        lambda: cloud.find_device(token=settings.token, device_id=settings.device_id, server=settings.server),
        use_gui,
    )
    if device is not None:
        return device
    if settings.token or settings.device_id:
        print("Aucun appareil ne correspond au token / identifiant configuré. Appareils disponibles :")
        print_devices(devices)
        raise MapError("Aucun appareil ne correspond au token ou à l'identifiant configuré (voir la liste dans le terminal).")

    vacuums = [d for d in devices if d.is_vacuum and detect_api(d.model) != VacuumApi.UNSUPPORTED]
    if not vacuums:
        print_devices(devices)
        raise MapError("Aucun aspirateur robot pris en charge sur ce compte. Vérifiez le serveur choisi.")
    if len(vacuums) > 1:
        print_devices(vacuums)
        raise MapError("Plusieurs aspirateurs trouvés : indiquez le token ou l'identifiant du robot (python run.py --setup).")
    return vacuums[0]


# ---------------------------------------------------------------------- programme principal
def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.debug:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)

    use_gui = not args.no_gui and gui.available()
    settings = load_or_configure(args, use_gui)
    if settings is None:
        return 1

    cloud = XiaomiCloudConnector(
        settings.username,
        settings.password,
        server=settings.server,
        session_file=settings.session_file,
    )
    try:
        login_interactive(cloud, force=args.relogin, use_gui=use_gui)
    except LoginError as exc:
        print(f"Connexion au cloud Xiaomi impossible : {exc}")
        if use_gui:
            gui.show_error(f"Connexion au cloud Xiaomi impossible :\n{exc}\n\nRelancez avec  python run.py --setup  pour corriger la configuration.")
        return 1

    try:
        if args.devices:
            print_devices(with_relogin(cloud, lambda: cloud.get_devices(settings.server), use_gui))
            return 0

        device = select_device(cloud, settings, use_gui)
        service = VacuumMapService(
            cloud,
            device,
            api=settings.api,
            host=settings.host,
            token=settings.token,
            scale=settings.scale,
            rotate=settings.rotate,
            use_local=not args.no_local,
            style=MapStyle(
                settings.theme,
                background=settings.background,
                room_colors=settings.room_colors,
                room_borders=settings.room_borders,
                border_color=settings.border_color,
            ),
        )

        if args.once:
            snapshot = service.fetch()
            path = save_snapshot(snapshot, settings.maps_dir)
            summary = snapshot.summary()
            print(f"Carte enregistrée : {path}")
            print(f"  nom de carte : {snapshot.map_name}")
            if summary["image"]:
                print(f"  taille       : {summary['image']['width']} x {summary['image']['height']} px")
            if summary["rooms"]:
                print("  pièces       : " + ", ".join(r["name"] or f"#{r['id']}" for r in summary["rooms"]))
            return 0

        updater = MapUpdater(service, settings.refresh_seconds, settings.maps_dir, settings.save_maps)
        updater.start()
        app = create_app(updater)
        host = args.host or settings.web_host
        port = args.port or settings.web_port
        url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
        print()
        print(f"Interface web : {url}   (Ctrl+C pour quitter)")
        print()
        if not args.no_browser:
            threading.Timer(1.5, webbrowser.open, args=(url,)).start()
        try:
            app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
        finally:
            updater.stop()
        return 0
    except (MapError, XiaomiCloudError, LoginError) as exc:
        print(f"Erreur : {exc}")
        if use_gui:
            gui.show_error(str(exc))
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
