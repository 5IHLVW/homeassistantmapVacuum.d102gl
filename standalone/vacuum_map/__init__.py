"""Xiaomi Vacuum Map - affichage de la carte d'un aspirateur robot Xiaomi.

Le moteur (cloud Xiaomi, accès local, décodage et style de la carte) est celui de l'intégration
Home Assistant : vacuum_map.vacuum, vacuum_map.xiaomi_cloud, etc. sont chargés depuis
custom_components/map_vacuum_d102gl/core, pour n'avoir qu'une seule copie du code.
"""

from pathlib import Path

__version__ = "1.2.0"

__path__.append(str(Path(__file__).resolve().parents[2] / "custom_components" / "map_vacuum_d102gl" / "core"))
