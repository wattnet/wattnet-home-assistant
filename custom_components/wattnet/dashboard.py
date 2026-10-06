"""A Wattnet dashboard that comes with the integration.

Home Assistant builds it on the fly from the entities and statistics of the
zone, so it always matches what exists and goes away when the integration is
removed. It is registered the same way Home Assistant registers a dashboard
defined in YAML: read-only, listed in the sidebar and in Settings > Dashboards.
Nothing is written to the user's own dashboards.

It only uses Home Assistant's built-in cards.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import json_bytes, json_fragment

from .const import DOMAIN, METRICS

if TYPE_CHECKING:
    from .coordinator import WattnetCoordinator

_LOGGER = logging.getLogger(__name__)

# The Wattnet logo, registered by frontend/wattnet-icons.js.
ICON = "wattnet:logo"

# The name of the dashboard in the sidebar. It is the same in every language, and
# it is just Wattnet because the dashboard is the integration's own.
DASHBOARD_TITLE = "Wattnet"


# The footer of every page of the home dashboard. It is the same in every
# language, since it is a legal notice.
FOOTER_TAGLINE = (
    "An open-source service for tracking the environmental footprint of "
    "electricity across Europe."
)
FOOTER_LEGAL = "© 2026 Spanish National Research Council (CSIC). All rights reserved."
# One row with two cells: the name and the description on the left and the
# copyright on the right. The Markdown card of Home Assistant styles a table with
# ``role="presentation"`` as a plain layout (no borders or padding) and honours
# ``align``, which is its way of supporting columns, since it allows no CSS of its
# own. It must have no line breaks, or the rest would be read as Markdown and not
# as the table.
FOOTER = (
    '<table role="presentation" width="100%"><tr>'
    '<td align="left" valign="top"><b><a href="https://wattnet.eu">Wattnet</a></b>'
    f" · {FOOTER_TAGLINE}</td>"
    f'<td align="right" valign="top">{FOOTER_LEGAL}</td>'
    "</tr></table>"
)

GAUGE_ORDER = (
    "carbon_footprint",
    "water_footprint",
    "water_impact",
    "environmental_score",
)
HISTORY_KEYS = (
    "carbon_footprint",
    "water_footprint",
    "water_impact",
    "environmental_score",
)
METRIC_ICONS = {
    "carbon_footprint": "mdi:molecule-co2",
    "water_footprint": "mdi:water",
    "water_impact": "mdi:water-alert",
    "environmental_score": "mdi:leaf-circle",
}

GREEN, YELLOW, ORANGE, RED = "#43a047", "#fdd835", "#fb8c00", "#e53935"

# Gauge ranges, in the unit of each metric. Calibrated with real data: from the
# cleanest grids (Nordics, France, about 20 to 100 gCO2/kWh) to coal-heavy ones
# (Poland, 600 to 800). Lower is better, except for the score.
GAUGES: dict[str, dict[str, Any]] = {
    "carbon_footprint": {
        "min": 0,
        "max": 800,
        "segments": [(0, GREEN), (150, YELLOW), (400, ORANGE), (600, RED)],
    },
    "water_footprint": {
        "min": 0,
        "max": 12,
        "segments": [(0, GREEN), (3, YELLOW), (6, ORANGE), (9, RED)],
    },
    "water_impact": {
        "min": 0,
        "max": 5,
        "segments": [(0, GREEN), (1, YELLOW), (2.5, ORANGE), (4, RED)],
    },
    # The only one where higher is better.
    "environmental_score": {
        "min": 0,
        "max": 100,
        "segments": [(0, RED), (33, ORANGE), (50, YELLOW), (66, GREEN)],
    },
}

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "all": "ALL",
        "zones": "Zones",
        "compare_intro": "**{count} zones**: {zones}",
        "week": "Last 7 days",
        "consumption": "Your consumption",
        "data": "Data",
        "carbon_footprint": "Carbon footprint",
        "water_footprint": "Water footprint",
        "water_impact": "Water impact",
        "environmental_score": "Environmental score",
        "grid_carbon": "CO₂ emitted today",
        "grid_water": "Water used today",
        "grid_impact": "Water impact today",
        "grid_carbon_days": "CO₂ emitted per day",
        "grid_water_days": "Water used per day",
        "hero": "Every kWh in {zone}",
        "hero_line": (
            "**{carbon} g** of CO₂ · **{water} L** of water · "
            "**{impact} stress-L** of water stress"
        ),
        "hero_score": " · environmental score **{score} out of 100**",
        "updated": "Data of",
        "provisional": "provisional, Wattnet may still revise it",
        "consolidated": "consolidated",
        "no_data": "No data yet",
        "reimport": "Re-import history",
        "reimport_confirm": "Download the history again and replace the statistics?",
        "note": (
            "Gauges show the latest 15-minute value. The graphs use hourly "
            "statistics, which are corrected when Wattnet consolidates the data. "
            "The score compares zones with each other. Water impact is in "
            "stress-litres (stress-L): litres of water weighted by how scarce "
            "water is in the zone."
        ),
    },
    "es": {
        "all": "ALL",
        "zones": "Zonas",
        "compare_intro": "**{count} zonas**: {zones}",
        "week": "Últimos 7 días",
        "consumption": "Tu consumo",
        "data": "Datos",
        "carbon_footprint": "Huella de carbono",
        "water_footprint": "Huella de agua",
        "water_impact": "Impacto en agua",
        "environmental_score": "Puntuación ambiental",
        "grid_carbon": "CO₂ emitido hoy",
        "grid_water": "Agua usada hoy",
        "grid_impact": "Impacto en agua hoy",
        "grid_carbon_days": "CO₂ emitido por día",
        "grid_water_days": "Agua usada por día",
        "hero": "Cada kWh en {zone}",
        "hero_line": (
            "**{carbon} g** de CO₂ · **{water} L** de agua · "
            "**{impact} stress-L** de estrés hídrico"
        ),
        "hero_score": " · puntuación ambiental **{score} de 100**",
        "updated": "Dato de",
        "provisional": "provisional, Wattnet aún puede revisarlo",
        "consolidated": "consolidado",
        "no_data": "Aún no hay datos",
        "reimport": "Reimportar histórico",
        "reimport_confirm": (
            "¿Descargar de nuevo el histórico y sustituir las estadísticas?"
        ),
        "note": (
            "Los medidores muestran el último valor de 15 minutos. Las gráficas "
            "usan estadísticas por hora, que se corrigen cuando Wattnet consolida "
            "los datos. La puntuación compara las zonas entre sí. El impacto en "
            "agua se mide en litros de estrés (stress-L): litros de agua "
            "ponderados por la escasez de agua de la zona."
        ),
    },
}


def texts(language: str) -> dict[str, str]:
    """Return the labels for a Home Assistant language, English by default."""
    return TEXTS.get(language.split("-")[0], TEXTS["en"])


def _gauge(entity_id: str, key: str, label: str) -> dict[str, Any]:
    spec = GAUGES[key]
    return {
        "type": "gauge",
        "entity": entity_id,
        "name": label,
        "needle": True,
        "min": spec["min"],
        "max": spec["max"],
        "segments": [
            {"from": start, "color": color} for start, color in spec["segments"]
        ],
    }


def _history(stat_id: str, title: str) -> dict[str, Any]:
    return {
        "type": "statistics-graph",
        "title": title,
        "chart_type": "line",
        "period": "hour",
        "days_to_show": 7,
        "stat_types": ["mean", "min", "max"],
        "entities": [stat_id],
    }


def _total_today(stat_id: str, title: str, icon: str) -> dict[str, Any]:
    return {
        "type": "statistic",
        "entity": stat_id,
        "name": title,
        "icon": icon,
        "stat_type": "change",
        "period": {"calendar": {"period": "day"}},
    }


def _per_day(stat_id: str, title: str) -> dict[str, Any]:
    return {
        "type": "statistics-graph",
        "title": title,
        "chart_type": "bar",
        "period": "day",
        "days_to_show": 14,
        "stat_types": ["change"],
        "entities": [stat_id],
    }


def _hero(entity_ids: dict[str, str | None], zone_name: str, t: dict[str, str]) -> str:
    """Markdown with a big message, filled in by Home Assistant's templates."""
    carbon, water, impact = (
        entity_ids.get("carbon_footprint"),
        entity_ids.get("water_footprint"),
        entity_ids.get("water_impact"),
    )
    if not (carbon and water and impact):
        return f"## {t['hero'].format(zone=zone_name)}\n\n{t['no_data']}"

    def number(entity_id: str, digits: int) -> str:
        return (
            f"{{{{ (states('{entity_id}') | float(none) | round({digits})) "
            f"if states('{entity_id}') | is_number else '–' }}}}"
        )

    line = t["hero_line"].format(
        carbon=number(carbon, 0), water=number(water, 1), impact=number(impact, 1)
    )
    if score := entity_ids.get("environmental_score"):
        # The score is a number from 0 to 100, so it says what it is out of.
        line += t["hero_score"].format(score=number(score, 0))
    stamp = (
        f"{{% set ts = state_attr('{carbon}', 'data_timestamp') %}}"
        f"{{% if ts %}}{t['updated']} "
        f"{{{{ as_timestamp(ts) | timestamp_custom('%H:%M', true) }}}} · "
        f"{{{{ '{t['consolidated']}' if state_attr('{carbon}', 'valid') "
        f"else '{t['provisional']}' }}}}{{% endif %}}"
    )
    return f"## {t['hero'].format(zone=zone_name)}\n\n{line}\n\n{stamp}"


@dataclass(frozen=True)
class ZoneData:
    """What the dashboard needs to know about one zone (one config entry)."""

    entry_id: str
    zone: str
    zone_name: str
    entity_ids: dict[str, str | None]  # sensors, by metric key
    consolidated_entity: str | None
    stat_ids: dict[str, str]  # corrected statistics, by metric key
    grid_stat_ids: dict[str, str]  # derived from the home's grid, if any

    @property
    def title(self) -> str:
        """Return the zone as ``Spain (ES)``."""
        return f"{self.zone_name} ({self.zone})"


def _section(cards: list[dict[str, Any]], span: int = 1) -> dict[str, Any]:
    """Return a section ``span`` columns wide (the view has up to four).

    A card takes 12 of the columns of the grid inside a section unless told
    otherwise, and a section that spans several columns has 12 for each of them.
    So a card in a wide section fills only one column of it, and the rest stays
    empty. They are told to take the full width, which is what a wide section is
    for. Headings already do, and a card that sets its own width keeps it.
    """
    if span > 1:
        cards = [
            (
                card
                if card["type"] == "heading" or "grid_options" in card
                else {**card, "grid_options": {"columns": "full"}}
            )
            for card in cards
        ]
    section: dict[str, Any] = {"type": "grid", "cards": cards}
    if span > 1:
        section["column_span"] = span
    return section


def _grid(cards: list[dict[str, Any]], columns: int = 2) -> dict[str, Any]:
    """Return cards laid out in a grid of ``columns`` columns."""
    return {"type": "grid", "columns": columns, "square": False, "cards": cards}


def _heading(text: str, icon: str, style: str | None = None) -> dict[str, Any]:
    card: dict[str, Any] = {"type": "heading", "heading": text, "icon": icon}
    if style:
        card["heading_style"] = style
    return card


def _gauge_columns(zones: list[ZoneData], t: dict[str, str]) -> list[dict[str, Any]]:
    """Return a column per metric, with a gauge for each zone, one under another."""
    sections: list[dict[str, Any]] = []
    for key in GAUGE_ORDER:
        gauges = [
            _gauge(entity_id, key, z.zone_name)
            for z in zones
            if (entity_id := z.entity_ids.get(key))
        ]
        if gauges:
            sections.append(_section([_heading(t[key], METRIC_ICONS[key]), *gauges]))
    return sections


def _history_sections(zones: list[ZoneData], t: dict[str, str]) -> list[dict[str, Any]]:
    """Return the heading of the last 7 days and the four graphs, one per column.

    With several zones each graph draws all of them. With one it draws just that.
    """
    sections: list[dict[str, Any]] = [
        _section([_heading(t["week"], "mdi:chart-areaspline")], span=4)
    ]
    for key in HISTORY_KEYS:
        stat_ids = [(z, z.stat_ids[key]) for z in zones if key in z.stat_ids]
        if not stat_ids:
            continue
        graph = _history(stat_ids[0][1], t[key])
        if len(zones) > 1:
            graph["entities"] = [
                {"entity": stat_id, "name": z.zone_name} for z, stat_id in stat_ids
            ]
        sections.append(_section([graph]))
    return sections


def _zone_sections(z: ZoneData, t: dict[str, str]) -> list[dict[str, Any]]:
    """Return the sections that describe one zone.

    It has the same layout as the page that compares all the zones: the headline
    across the whole width, a column of gauges per metric, the four graphs of the
    last 7 days in a row, and then the home and the data, each across the whole
    width.
    """
    sections: list[dict[str, Any]] = [
        _section(
            [
                _heading(f"Wattnet · {z.title}", ICON, "title"),
                {
                    "type": "markdown",
                    "content": _hero(z.entity_ids, z.zone_name, t),
                    "text_only": True,
                },
            ],
            span=4,
        ),
        *_gauge_columns([z], t),
        *_history_sections([z], t),
    ]

    if z.grid_stat_ids:
        grid = z.grid_stat_ids
        home_cards: list[dict[str, Any]] = [_heading(t["consumption"], "mdi:flash")]
        totals = [
            _total_today(grid[key], t[label], icon)
            for key, label, icon in (
                ("carbon_footprint", "grid_carbon", "mdi:molecule-co2"),
                ("water_footprint", "grid_water", "mdi:water"),
                ("water_impact", "grid_impact", "mdi:water-alert"),
            )
            if key in grid
        ]
        if totals:
            home_cards.append({"type": "horizontal-stack", "cards": totals})
        bars = [
            _per_day(grid[key], t[label])
            for key, label in (
                ("carbon_footprint", "grid_carbon_days"),
                ("water_footprint", "grid_water_days"),
            )
            if key in grid
        ]
        if bars:
            home_cards.append(_grid(bars))
        sections.append(_section(home_cards, span=4))

    # Two tiles of the same style and size, one row high and two columns wide each,
    # which together fill the four columns of the section. A card in a wide section
    # would otherwise take the whole width, and a tile that size is far bigger than
    # what it shows.
    half = {"columns": 24, "rows": 1}
    action = {
        "action": "perform-action",
        "perform_action": f"{DOMAIN}.reimport_statistics",
        "data": {"config_entry": z.entry_id},
        "confirmation": {"text": t["reimport_confirm"]},
    }
    data_cards: list[dict[str, Any]]
    if z.consolidated_entity:
        data_cards = [
            {"type": "tile", "entity": z.consolidated_entity, "grid_options": half},
            # The same tile, with the same entity so that it looks the same, but
            # it shows no state and re-imports the history when it is pressed.
            {
                "type": "tile",
                "entity": z.consolidated_entity,
                "name": t["reimport"],
                "icon": "mdi:database-refresh",
                "hide_state": True,
                "tap_action": action,
                "icon_tap_action": action,
                "grid_options": half,
            },
        ]
    else:  # no sensor to base the tiles on: a button does the same
        data_cards = [
            {
                "type": "button",
                "name": t["reimport"],
                "icon": "mdi:database-refresh",
                "tap_action": action,
                "grid_options": half,
            }
        ]
    sections.append(
        _section(
            [
                _heading(t["data"], "mdi:database-check"),
                *data_cards,
                {"type": "markdown", "content": t["note"], "text_only": True},
            ],
            # Across the whole width, in a row of its own, like the home: the
            # number of columns depends on the screen, so a fixed width beside
            # another section would leave gaps.
            span=4,
        )
    )
    return sections


def _zone_list(zones: list[ZoneData]) -> str:
    """List the zones on one line: names up to four, then only the codes."""
    if len(zones) <= 4:
        return ", ".join(z.title for z in zones)
    return ", ".join(z.zone for z in zones)


def _compare_sections(zones: list[ZoneData], t: dict[str, str]) -> list[dict[str, Any]]:
    """Return the sections that put all the zones side by side.

    The intro takes the whole width, then there is a column of gauges per metric
    (one gauge per zone, one under the other) and the four graphs of the last 7
    days in a row, each with all the zones drawn in it.
    """
    intro = _section(
        [
            _heading(f"Wattnet · {t['zones']}", ICON, "title"),
            {
                "type": "markdown",
                "content": t["compare_intro"].format(
                    count=len(zones), zones=_zone_list(zones)
                ),
                "text_only": True,
            },
        ],
        span=4,
    )
    return [intro, *_gauge_columns(zones, t), *_history_sections(zones, t)]


def _footer() -> dict[str, Any]:
    """Return the section with the copyright notice, across the whole width.

    The view's own ``footer`` is not used: it is a floating bar of at most 600
    pixels meant for actions. This is a section as wide as the page. The card has
    no background or border (``text_only``), so the notice reads as plain text on
    the page, and takes every column of the section, so that its table can put
    the description on the left and the copyright on the right.
    """
    return _section(
        [{"type": "markdown", "content": FOOTER, "text_only": True}], span=4
    )


def build_dashboard(zones: list[ZoneData], language: str) -> dict[str, Any]:
    """Build the Lovelace configuration of the one Wattnet dashboard.

    With a single zone it has one view. With several it starts with a view that
    compares them, followed by one view per zone. A zone whose Energy dashboard
    has a grid source also gets a section about the home.
    """
    t = texts(language)
    zones = sorted(zones, key=lambda z: z.zone)  # the tabs read ES, FR, PL...
    if len(zones) == 1:
        views = [
            {
                "type": "sections",
                "title": "Wattnet",
                "path": "overview",
                "max_columns": 4,
                "sections": [*_zone_sections(zones[0], t), _footer()],
            }
        ]
    else:
        views = [
            {
                "type": "sections",
                "title": t["all"],
                "path": "compare",
                "max_columns": 4,
                "sections": [*_compare_sections(zones, t), _footer()],
            }
        ] + [
            {
                "type": "sections",
                "title": z.zone,
                "path": f"zone-{z.zone.lower()}",
                "max_columns": 4,
                "sections": [*_zone_sections(z, t), _footer()],
            }
            for z in zones
        ]
    # The views have no icon on purpose: Home Assistant shows only the icon of a
    # tab when it has one and hides its title, and the titles are what tell the
    # zones apart.
    return {"title": DASHBOARD_TITLE, "views": views}


def entity_ids_of(hass: HomeAssistant, zone: str) -> dict[str, str | None]:
    """Look up the sensors of a zone in the entity registry, by metric key."""
    registry = er.async_get(hass)
    keys = [m.key for m in METRICS] + ["consolidated_until"]
    return {
        key: registry.async_get_entity_id("sensor", DOMAIN, f"{zone}_{key}")
        for key in keys
    }


class WattnetDashboardConfig:
    """Builds the dashboard of all the zones each time Home Assistant asks for it."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinators: Callable[[], list[WattnetCoordinator]],
    ) -> None:
        """Remember how to get the coordinators of the zones to show."""
        self.hass = hass
        self._coordinators = coordinators

    async def _zone(self, c: WattnetCoordinator) -> ZoneData:
        from .coordinator import grid_statistic_id, statistic_id

        entity_ids = entity_ids_of(self.hass, c.zone)
        grid_ids = await c.async_grid_statistic_ids()
        return ZoneData(
            entry_id=c.config_entry.entry_id,
            zone=c.zone,
            zone_name=c.zone_name,
            entity_ids=entity_ids,
            consolidated_entity=entity_ids.get("consolidated_until"),
            stat_ids={m.key: statistic_id(c.zone, m, c.scope) for m in METRICS},
            grid_stat_ids=(
                {
                    m.key: sid
                    for m in METRICS
                    if (sid := grid_statistic_id(c.zone, m, c.scope))
                }
                if grid_ids
                else {}
            ),
        )

    async def async_build(self) -> dict[str, Any]:
        """Return the configuration for the current zones, entities and statistics."""
        zones = [await self._zone(c) for c in self._coordinators()]
        return build_dashboard(zones, self.hass.config.language)


def to_json(config: dict[str, Any]) -> Any:
    """Return the configuration as the JSON fragment Home Assistant serves."""
    return json_fragment(json_bytes(config))


__all__ = [
    "FOOTER",
    "GAUGES",
    "DASHBOARD_TITLE",
    "ZoneData",
    "WattnetDashboardConfig",
    "build_dashboard",
    "entity_ids_of",
    "texts",
    "to_json",
]
