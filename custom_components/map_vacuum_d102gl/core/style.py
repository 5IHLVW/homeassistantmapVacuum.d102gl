"""Style de la carte : thèmes de couleurs, fond, contours entre les pièces."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from PIL import Image, ImageChops, ImageFilter
from vacuum_map_parser_base.config.color import Color, ColorsPalette, SupportedColor

_LOGGER = logging.getLogger(__name__)

WHITE: Color = (255, 255, 255)

# Couleurs des pièces, dans un ordre où deux numéros consécutifs ont des teintes très différentes
# (les robots numérotent les pièces 10, 11, 12... : deux pièces voisines ont souvent des numéros proches).
ROOM_COLORS_PASTEL = [
    "#F4A9A3", "#A7C8F2", "#BFE3A1", "#D2B7EE", "#F8CF96", "#99D8CF",
    "#F2B3D6", "#DDE6A0", "#AEB8EE", "#F0C3A0", "#9FDCEE", "#DDB6DD",
]
ROOM_COLORS_VIVID = [
    "#E57373", "#5C9EE6", "#7CC47F", "#B568C6", "#FFB54D", "#3FB0A4",
    "#EF5F8F", "#C9D65A", "#7380C9", "#FF8A5C", "#43B8E8", "#A1887F",
]

_LIGHT_BASE: dict[SupportedColor, Color] = {
    SupportedColor.MAP_INSIDE: (226, 229, 234),
    SupportedColor.MAP_WALL: (96, 103, 112),
    SupportedColor.MAP_WALL_V2: (96, 103, 112),
    SupportedColor.GREY_WALL: (96, 103, 112),
    SupportedColor.NEW_DISCOVERED_AREA: (208, 212, 218),
    SupportedColor.SCAN: (200, 204, 210),
    SupportedColor.PATH: (70, 76, 86),
    SupportedColor.MOP_PATH: (70, 76, 86, 0x40),
    SupportedColor.CLEANED_AREA: (120, 120, 120, 70),
    SupportedColor.ZONES: (66, 133, 244, 0x50),
    SupportedColor.ZONES_OUTLINE: (66, 133, 244),
    SupportedColor.CHARGER: (32, 178, 120),
    SupportedColor.CHARGER_OUTLINE: (255, 255, 255),
    SupportedColor.ROBO: (255, 255, 255),
    SupportedColor.ROBO_OUTLINE: (40, 40, 40),
    SupportedColor.ROOM_NAMES: (30, 30, 30),
}

THEMES: dict[str, dict[str, Any]] = {
    # Fond blanc, pièces pastel, séparations blanches (style application Mi Home).
    "clair": {"colors": _LIGHT_BASE, "rooms": ROOM_COLORS_PASTEL, "border": WHITE},
    # Fond blanc, pièces aux couleurs franches, séparations gris foncé.
    "contraste": {"colors": _LIGHT_BASE, "rooms": ROOM_COLORS_VIVID, "border": (60, 64, 72)},
    # Couleurs d'origine de la bibliothèque (fond bleu).
    "original": {"colors": {}, "rooms": None, "border": None},
}
DEFAULT_THEME = "clair"


def parse_color(value: Any) -> Color | None:
    """Convertit '#RRGGBB', 'RRGGBB', 'r,g,b' ou [r, g, b] en tuple. Renvoie None si vide ou invalide."""
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        text = str(value).strip()
        if "," in text:
            parts = text.split(",")
        else:
            text = text.lstrip("#")
            if len(text) not in (6, 8):
                _LOGGER.warning("Couleur ignorée (format attendu #RRGGBB): %s", value)
                return None
            try:
                parts = [int(text[i : i + 2], 16) for i in range(0, len(text), 2)]
            except ValueError:
                _LOGGER.warning("Couleur ignorée (format attendu #RRGGBB): %s", value)
                return None
    try:
        rgb = tuple(max(0, min(255, int(float(p)))) for p in parts)
    except (TypeError, ValueError):
        _LOGGER.warning("Couleur ignorée: %s", value)
        return None
    return rgb if len(rgb) in (3, 4) else None  # type: ignore[return-value]


def parse_color_list(value: Any) -> list[Color]:
    """Liste de couleurs séparées par des espaces, ';' ou des virgules ('#AABBCC, #DDEEFF')."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items: Iterable[Any] = value
    else:
        items = str(value).replace(";", " ").replace(",", " ").split()
    return [c for c in (parse_color(item) for item in items) if c is not None]


class MapStyle:
    """Palette + options de contour, construites à partir d'un thème et de surcharges facultatives."""

    def __init__(
        self,
        theme: str | None = DEFAULT_THEME,
        background: Any = None,
        room_colors: Any = None,
        room_borders: bool = True,
        border_color: Any = None,
    ) -> None:
        name = (theme or DEFAULT_THEME).lower()
        if name not in THEMES:
            _LOGGER.warning("Thème inconnu '%s', utilisation de '%s'", theme, DEFAULT_THEME)
            name = DEFAULT_THEME
        preset = THEMES[name]
        self.theme = name

        colors: dict[SupportedColor, Color] = dict(preset["colors"])
        if name != "original":
            colors[SupportedColor.MAP_OUTSIDE] = WHITE
        if (bg := parse_color(background)) is not None:
            colors[SupportedColor.MAP_OUTSIDE] = bg

        rooms = parse_color_list(room_colors) or parse_color_list(preset["rooms"])
        room_dict = {str(i): rooms[(i - 1) % len(rooms)] for i in range(1, 33)} if rooms else None

        self.palette = ColorsPalette(colors, room_dict)
        self.room_borders = room_borders and (parse_color(border_color) or preset["border"]) is not None
        self.border_color: Color = parse_color(border_color) or preset["border"] or WHITE

    def apply(self, parser: Any, scale: float) -> None:
        """Ajoute le tracé des contours entre pièces au parseur d'image (si le parseur le permet)."""
        if not self.room_borders:
            return
        image_parser = getattr(parser, "_image_parser", None)
        if image_parser is None or not hasattr(image_parser, "parse"):
            _LOGGER.debug("Contours de pièces non disponibles pour %s", type(parser).__name__)
            return
        original_parse = image_parser.parse
        width = max(1, round(scale * 0.8)) | 1  # MaxFilter attend une taille impaire

        def parse_with_borders(*args: Any, **kwargs: Any) -> Any:
            result = original_parse(*args, **kwargs)
            image = result[0] if isinstance(result, tuple) else result
            if image is not None:
                try:
                    draw_room_borders(image, self.palette.cached_room_colors.values(), self.border_color, width)
                except Exception:  # noqa: BLE001 - un contour raté ne doit pas empêcher d'afficher la carte
                    _LOGGER.exception("Tracé des contours de pièces impossible")
            return result

        image_parser.parse = parse_with_borders


def _mask(image: Image.Image) -> Image.Image:
    return image.point(lambda v: 255 if v else 0)


def draw_room_borders(image: Image.Image, room_colors: Iterable[Color], color: Color, width: int) -> None:
    """Dessine (sur place) une ligne entre deux pièces voisines de couleurs différentes."""
    rgb = image.convert("RGB")
    label = Image.new("L", rgb.size, 0)
    unique: Sequence[tuple[int, ...]] = list(dict.fromkeys(tuple(c[:3]) for c in room_colors))
    for index, room_color in enumerate(unique[:255], start=1):
        solid = Image.new("RGB", rgb.size, room_color)
        same = ImageChops.difference(rgb, solid).convert("L").point(lambda v: 255 if v == 0 else 0)
        label.paste(index, mask=same)

    is_room = _mask(label)
    edges = Image.new("L", rgb.size, 0)
    for dx, dy in ((1, 0), (0, 1)):
        shifted = ImageChops.offset(label, dx, dy)
        different = _mask(ImageChops.difference(label, shifted))
        both_rooms = ImageChops.multiply(is_room, _mask(shifted))
        edges = ImageChops.lighter(edges, ImageChops.multiply(different, both_rooms))
    if width > 1:
        edges = edges.filter(ImageFilter.MaxFilter(width))
        edges = ImageChops.multiply(edges, is_room)
    fill = tuple(color[:3]) + ((color[3],) if len(color) == 4 else (255,)) if image.mode == "RGBA" else tuple(color[:3])
    image.paste(fill, mask=edges)
