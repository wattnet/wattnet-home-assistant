"""Tests for the dashboard that ships with the integration."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.wattnet.const import CONF_SHOW_DASHBOARD, METRICS
from custom_components.wattnet.dashboard import (
    DASHBOARD_TITLE,
    FOOTER,
    GAUGES,
    ICON,
    ZoneData,
    build_dashboard,
    texts,
)

from .conftest import FakeClient

ENTITIES = {
    "carbon_footprint": "sensor.wattnet_spain_es_carbon_footprint",
    "water_footprint": "sensor.wattnet_spain_es_water_footprint",
    "water_impact": "sensor.wattnet_spain_es_water_impact",
    "environmental_score": "sensor.wattnet_spain_es_environmental_score",
    "consolidated_until": "sensor.wattnet_spain_es_statistics_consolidated_until",
}
STATS = {
    "carbon_footprint": "wattnet:es_carbon_footprint_life_cycle",
    "water_footprint": "wattnet:es_water_footprint_life_cycle",
    "water_impact": "wattnet:es_water_impact",
    "environmental_score": "wattnet:es_environmental_score",
}
GRID = {
    "carbon_footprint": "wattnet:es_grid_carbon_emissions_life_cycle",
    "water_footprint": "wattnet:es_grid_water_consumption_life_cycle",
    "water_impact": "wattnet:es_grid_water_impact",
}


def _zone(code: str = "ES", name: str = "Spain", grid: bool = False) -> ZoneData:
    lower = code.lower()
    return ZoneData(
        entry_id=f"entry-{lower}",
        zone=code,
        zone_name=name,
        entity_ids={k: v.replace("_es_", f"_{lower}_") for k, v in ENTITIES.items()},
        consolidated_entity=ENTITIES["consolidated_until"].replace(
            "_es_", f"_{lower}_"
        ),
        stat_ids={k: v.replace("es_", f"{lower}_") for k, v in STATS.items()},
        grid_stat_ids=(
            {k: v.replace("es_", f"{lower}_") for k, v in GRID.items()} if grid else {}
        ),
    )


def _build(language: str = "en", grid: bool = False) -> dict[str, Any]:
    return build_dashboard([_zone(grid=grid)], language)


def _cards_of(section: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(card: dict[str, Any]) -> None:
        out.append(card)
        for inner in card.get("cards", []):
            walk(inner)

    for card in section["cards"]:
        walk(card)
    return out


def _cards(config: dict[str, Any], view: int = 0) -> list[dict[str, Any]]:
    """All the cards of a view, flattening the stacks."""
    return [
        card
        for section in config["views"][view]["sections"]
        for card in _cards_of(section)
    ]


def _of_type(config: dict[str, Any], card_type: str, view: int = 0):
    return [c for c in _cards(config, view) if c["type"] == card_type]


# -- one zone ---------------------------------------------------------------


def test_the_logo_is_the_icon_of_the_dashboard():
    assert ICON == "wattnet:logo"


def test_the_dashboard_is_just_called_wattnet_in_every_language():
    assert DASHBOARD_TITLE == "Wattnet"
    assert _build()["title"] == "Wattnet"
    assert build_dashboard([_zone()], "es")["title"] == "Wattnet"
    assert _several()["title"] == "Wattnet"


def test_the_tabs_have_no_icon_so_that_their_names_show():
    # With an icon Home Assistant draws only the icon and hides the title.
    for config in (_build(), _several()):
        for view in config["views"]:
            assert "icon" not in view
            assert view["title"]


def test_one_zone_is_a_single_sections_view_with_a_title():
    config = _build()
    assert config["title"] == "Wattnet"
    (view,) = config["views"]
    assert view["type"] == "sections"
    assert view["max_columns"] == 4
    assert view["sections"][0]["cards"][0]["heading"] == "Wattnet · Spain (ES)"


def test_there_is_a_gauge_per_metric_on_the_matching_entity():
    gauges = {g["entity"]: g for g in _of_type(_build(), "gauge")}
    assert set(gauges) == {ENTITIES[k] for k in GAUGES}
    assert all(g["needle"] for g in gauges.values())


def test_gauge_colours_go_green_to_red_except_the_score_which_goes_red_to_green():
    gauges = {g["entity"]: g for g in _of_type(_build(), "gauge")}

    def colours(key: str) -> list[str]:
        return [s["color"] for s in gauges[ENTITIES[key]]["segments"]]

    for key in ("carbon_footprint", "water_footprint", "water_impact"):
        assert colours(key)[0] == "#43a047"  # low is green
        assert colours(key)[-1] == "#e53935"  # high is red
    assert colours("environmental_score")[0] == "#e53935"  # a low score is red
    assert colours("environmental_score")[-1] == "#43a047"  # a high one is green


def test_gauge_segments_ascend_and_fit_the_range():
    for gauge in _of_type(_build(), "gauge"):
        starts = [s["from"] for s in gauge["segments"]]
        assert starts == sorted(starts)
        assert gauge["min"] <= starts[0] and starts[-1] < gauge["max"]


def test_the_ranges_cover_what_real_zones_report():
    # Real values seen: carbon 67 to 223 (Spain) and 315 to 804 (Poland).
    assert GAUGES["carbon_footprint"]["max"] >= 800
    assert GAUGES["environmental_score"]["max"] == 100


def test_history_graphs_use_the_corrected_statistics_for_seven_days():
    graphs = _of_type(_build(), "statistics-graph")
    assert [g["entities"][0] for g in graphs] == [
        STATS["carbon_footprint"],
        STATS["water_footprint"],
        STATS["water_impact"],
        STATS["environmental_score"],
    ]
    for graph in graphs:
        assert graph["days_to_show"] == 7
        assert graph["period"] == "hour"
    assert graphs[-1]["title"] == "Environmental score"


def test_the_hero_message_reads_the_three_footprints_with_templates():
    (hero,) = _of_type(_build(), "markdown")[:1]
    content = hero["content"]
    assert "Every kWh in Spain" in content
    for key in ("carbon_footprint", "water_footprint", "water_impact"):
        assert ENTITIES[key] in content
    assert "{{" in content and "is_number" in content  # safe with no data
    assert "consolidated" in content and "provisional" in content


def test_the_headline_includes_the_score_and_says_it_is_out_of_100():
    content = _of_type(_build(), "markdown")[0]["content"]
    line = content.split("\n\n")[1]
    assert "environmental score **" in line and "out of 100" in line
    assert ENTITIES["environmental_score"] in line  # it reads the score sensor
    # The score comes last, after the three per kWh figures.
    assert line.index("stress-L") < line.index("environmental score")
    spanish = _of_type(build_dashboard([_zone()], "es"), "markdown")[0]["content"]
    assert "puntuación ambiental **" in spanish and "de 100" in spanish


def test_the_headline_has_no_score_when_there_is_no_score_sensor():
    zone = _zone()
    without = ZoneData(
        entry_id=zone.entry_id,
        zone=zone.zone,
        zone_name=zone.zone_name,
        entity_ids={
            k: v for k, v in zone.entity_ids.items() if k != "environmental_score"
        },
        consolidated_entity=zone.consolidated_entity,
        stat_ids=zone.stat_ids,
        grid_stat_ids={},
    )
    line = _of_type(build_dashboard([without], "en"), "markdown")[0]["content"]
    assert "environmental score" not in line and "out of 100" not in line


def test_the_headline_is_one_short_line_and_water_stress_is_in_litres():
    content = _of_type(_build(), "markdown")[0]["content"]
    title, line, stamp = content.split("\n\n")
    assert "\n" not in line  # one line, which Home Assistant does not wrap
    # Without the templates it is short: g of CO2, L of water and stress-L.
    assert "stress-L" in line and "weighs" not in line
    assert " · " in line
    spanish = _of_type(build_dashboard([_zone()], "es"), "markdown")[0]["content"]
    assert "stress-L" in spanish and "pesa" not in spanish


def test_the_note_explains_what_a_stress_litre_is():
    note = _of_type(_build(), "markdown")[-2]["content"]  # the footer is last
    assert "stress-litres" in note and "scarce" in note
    es = _of_type(build_dashboard([_zone()], "es"), "markdown")[-2]["content"]
    assert "litros de estrés" in es and "escasez" in es


def test_the_data_section_has_the_marker_and_a_reimport_tile_for_this_entry():
    config = _build()
    marker, reimport = _of_type(config, "tile")
    assert marker["entity"] == ENTITIES["consolidated_until"]
    action = reimport["tap_action"]
    assert action["perform_action"] == "wattnet.reimport_statistics"
    assert action["data"] == {"config_entry": "entry-es"}
    assert action["confirmation"]["text"]


def test_the_home_section_is_left_out_without_a_grid_source():
    config = _build(grid=False)
    headings = [s["cards"][0].get("heading") for s in config["views"][0]["sections"]]
    assert "Your consumption" not in headings
    assert not _of_type(config, "statistic")


def test_the_home_section_shows_todays_totals_and_daily_bars_with_a_grid():
    config = _build(grid=True)
    headings = [s["cards"][0].get("heading") for s in config["views"][0]["sections"]]
    assert "Your consumption" in headings
    totals = _of_type(config, "statistic")
    assert [t["entity"] for t in totals] == [
        GRID["carbon_footprint"],
        GRID["water_footprint"],
        GRID["water_impact"],
    ]
    assert all(t["stat_type"] == "change" for t in totals)
    assert all(t["period"] == {"calendar": {"period": "day"}} for t in totals)
    bars = [g for g in _of_type(config, "statistics-graph") if g["chart_type"] == "bar"]
    assert [b["entities"][0] for b in bars] == [
        GRID["carbon_footprint"],
        GRID["water_footprint"],
    ]
    assert all(b["period"] == "day" and b["stat_types"] == ["change"] for b in bars)


# -- layout ---------------------------------------------------------------------


def _spans(view: dict[str, Any]) -> list[int]:
    return [s.get("column_span", 1) for s in view["sections"]]


def test_a_zone_has_the_same_layout_as_the_page_with_all_the_zones():
    view = _build(grid=True)["views"][0]
    # Four columns: [headline 4] / four gauge columns / [last 7 days 4] / four
    # graphs / [home 4] / [data 4] / [footer 4].
    assert _spans(view) == [4, 1, 1, 1, 1, 4, 1, 1, 1, 1, 4, 4, 4]
    headings = [s["cards"][0].get("heading") for s in view["sections"]]
    assert headings[0] == "Wattnet · Spain (ES)"
    assert headings[1:5] == [
        "Carbon footprint",
        "Water footprint",
        "Water impact",
        "Environmental score",
    ]
    assert headings[5] == "Last 7 days"
    assert headings[-3:-1] == ["Your consumption", "Data"]


def test_the_footer_has_the_name_and_description_on_the_left_and_the_copyright_right():
    left, right = FOOTER.split("</td><td")
    assert 'align="left"' in left and 'align="right"' in right
    assert '<a href="https://wattnet.eu">Wattnet</a>' in left  # the name links
    assert (
        "An open-source service for tracking the environmental footprint of "
        "electricity across Europe." in left
    )
    assert (
        "© 2026 Spanish National Research Council (CSIC). All rights reserved." in right
    )
    assert "Spanish National" not in left  # the legal text is not on the left


def test_the_footer_has_no_logo_or_picture():
    assert "<img" not in FOOTER
    assert "wattnet_static" not in FOOTER


def test_the_footer_is_a_layout_table_that_home_assistant_renders_without_borders():
    assert FOOTER.startswith('<table role="presentation" width="100%">')
    assert FOOTER.endswith("</tr></table>")
    # No line breaks at all, or the rest would be read as Markdown, not the table.
    assert "\n" not in FOOTER
    assert FOOTER.count("<td") == 2


def test_no_logo_files_are_shipped_for_the_footer():
    from pathlib import Path

    folder = Path(__file__).parent.parent / "custom_components" / "wattnet" / "frontend"
    assert sorted(p.name for p in folder.iterdir()) == ["wattnet-icons.js"]


def test_every_page_of_the_home_dashboard_ends_with_the_footer():
    for config in (
        _build(),
        _build(grid=True),
        _several(),
        build_dashboard([_zone()], "es"),
    ):
        for view in config["views"]:
            footer = view["sections"][-1]
            assert footer["column_span"] == 4
            (card,) = footer["cards"]
            assert card["type"] == "markdown" and card["content"] == FOOTER
            # No background or border, across every column of the section.
            assert card["text_only"] is True
            assert card["grid_options"] == {"columns": "full"}
            assert "visibility" not in card  # one footer, not one per theme
            contents = [
                c.get("content") for sec in view["sections"] for c in sec["cards"]
            ]
            assert contents.count(FOOTER) == 1


# -- cards in wide sections take the whole width ---------------------------------


def test_every_card_in_a_wide_section_takes_the_full_width():
    for config in (_build(grid=True), _several(), build_dashboard([_zone()], "es")):
        for view in config["views"]:
            for section in view["sections"]:
                if section.get("column_span", 1) == 1:
                    continue
                for card in section["cards"]:
                    # A heading is full width by itself; the rest must say so, or
                    # they fill 12 of the columns of the section: one column of
                    # the four.
                    # (the small marker and button set their own, smaller width).
                    small = card["type"] in ("tile", "button")
                    assert (
                        card["type"] == "heading"
                        or small
                        or card["grid_options"] == {"columns": "full"}
                    ), (section["cards"][0].get("heading"), card["type"])


def test_the_data_and_home_blocks_fill_the_four_columns():
    sections = _build(grid=True)["views"][0]["sections"]
    home, data = sections[-3], sections[-2]
    for block in (home, data):
        assert block["column_span"] == 4
        content = [
            c for c in block["cards"] if c["type"] not in ("heading", "tile", "button")
        ]
        assert content and all(
            c["grid_options"] == {"columns": "full"} for c in content
        )
    assert [c["type"] for c in data["cards"]] == [
        "heading",
        "tile",
        "tile",
        "markdown",
    ]
    assert [c["type"] for c in home["cards"]][:2] == ["heading", "horizontal-stack"]


def test_the_headline_and_the_comparison_intro_also_fill_the_width():
    zone = _build()["views"][0]["sections"][0]
    compare = _several()["views"][0]["sections"][0]
    for section in (zone, compare):
        markdown = next(c for c in section["cards"] if c["type"] == "markdown")
        assert markdown["grid_options"] == {"columns": "full"}


def test_a_card_that_sets_its_own_width_keeps_it():
    from custom_components.wattnet.dashboard import _section

    own = {"type": "markdown", "content": "x", "grid_options": {"columns": 6}}
    (card,) = _section([own], span=4)["cards"]
    assert card["grid_options"] == {"columns": 6}
    # A section of one column is left alone.
    (narrow,) = _section([{"type": "gauge", "entity": "sensor.x"}])["cards"]
    assert "grid_options" not in narrow


def test_home_and_data_each_take_the_whole_width_so_there_are_no_gaps():
    view = _build(grid=False)["views"][0]
    assert _spans(view) == [4, 1, 1, 1, 1, 4, 1, 1, 1, 1, 4, 4]


def test_zone_pages_and_the_comparison_share_their_structure():
    single = _build()["views"][0]["sections"]
    several = _several()["views"][1]["sections"]  # a zone page
    comparison = _several()["views"][0]["sections"]

    def shape(sections):
        return [(s.get("column_span", 1), s["cards"][0]["type"]) for s in sections]

    # Intro, gauge columns and the last 7 days are laid out the same way.
    assert shape(single)[:10] == shape(comparison)[:10]
    assert shape(several)[:10] == shape(comparison)[:10]


def test_each_metric_column_has_a_heading_and_the_gauge_of_the_zone():
    view = _build()["views"][0]
    for column in view["sections"][1:5]:
        assert [c["type"] for c in column["cards"]] == ["heading", "gauge"]
        assert column["cards"][1]["name"] == "Spain"  # same as in the comparison


def test_the_four_graphs_share_one_row_of_columns_of_their_own():
    view = _build()["views"][0]
    graphs = view["sections"][6:10]
    assert [s.get("column_span", 1) for s in graphs] == [1, 1, 1, 1]
    assert [s["cards"][0]["type"] for s in graphs] == ["statistics-graph"] * 4


def test_the_score_graph_is_the_fourth_of_the_last_seven_days():
    sections = _build()["views"][0]["sections"][6:10]
    assert [s["cards"][0]["title"] for s in sections] == [
        "Carbon footprint",
        "Water footprint",
        "Water impact",
        "Environmental score",
    ]
    assert sections[-1]["cards"][0]["entities"] == [STATS["environmental_score"]]


def test_the_marker_and_the_reimport_are_two_tiles_of_two_columns_each():
    data = _build()["views"][0]["sections"][-2]
    marker, reimport = data["cards"][1:3]
    assert (marker["type"], reimport["type"]) == ("tile", "tile")
    # One row high and two of the four columns wide each, so that together they
    # fill the row.
    for card in (marker, reimport):
        assert card["grid_options"] == {"columns": 24, "rows": 1}


def test_the_reimport_tile_looks_like_the_marker_and_runs_the_action():
    data = _build()["views"][0]["sections"][-2]
    marker, reimport = data["cards"][1:3]
    # Same card type and entity, so it has the same style.
    assert reimport["entity"] == marker["entity"] == ENTITIES["consolidated_until"]
    assert reimport["name"] == "Re-import history"
    assert reimport["icon"] == "mdi:database-refresh"
    assert reimport["hide_state"] is True  # it is an action, not a value
    for key in ("tap_action", "icon_tap_action"):  # pressing anywhere works
        assert reimport[key]["perform_action"] == "wattnet.reimport_statistics"
        assert reimport[key]["data"] == {"config_entry": "entry-es"}
        assert reimport[key]["confirmation"]["text"]
    assert "tap_action" not in marker  # the marker only shows its value


def test_without_the_sensor_the_reimport_falls_back_to_a_button_of_the_same_size():
    zone = ZoneData(
        entry_id="x",
        zone="ES",
        zone_name="Spain",
        entity_ids={},
        consolidated_entity=None,
        stat_ids=dict(STATS),
        grid_stat_ids={},
    )
    data = build_dashboard([zone], "en")["views"][0]["sections"][-2]
    (button,) = [c for c in data["cards"] if c["type"] in ("tile", "button")]
    assert button["type"] == "button"
    assert button["grid_options"] == {"columns": 24, "rows": 1}
    assert button["tap_action"]["perform_action"] == "wattnet.reimport_statistics"


def test_the_note_has_no_background():
    note = _of_type(_build(), "markdown")[-2]  # the footer is the last one
    assert "Gauges show" in note["content"]
    assert note["text_only"] is True


def test_the_home_bars_sit_side_by_side():
    home = _build(grid=True)["views"][0]["sections"][-3]
    bars = home["cards"][-1]
    assert bars["type"] == "grid" and bars["columns"] == 2
    assert [c["chart_type"] for c in bars["cards"]] == ["bar", "bar"]


def test_the_comparison_has_a_column_per_metric_and_the_graphs_in_one_row():
    view = _several()["views"][0]
    spans = _spans(view)
    # intro / four gauge columns / "last 7 days" heading / four graphs of one
    # column each, which make a row of four.
    assert spans == [4, 1, 1, 1, 1, 4, 1, 1, 1, 1, 4]
    gauge_columns = view["sections"][1:5]
    for column in gauge_columns:
        kinds = [c["type"] for c in column["cards"]]
        assert kinds == ["heading", "gauge", "gauge", "gauge"]  # one per zone
    assert [s["cards"][0]["type"] for s in view["sections"][6:10]] == [
        "statistics-graph"
    ] * 4


# -- several zones in the same dashboard --------------------------------------


def _several() -> dict[str, Any]:
    return build_dashboard(
        [_zone("FR", "France"), _zone("ES", "Spain", grid=True), _zone("PL", "Poland")],
        "en",
    )


def test_several_zones_give_a_comparison_followed_by_one_view_per_zone():
    config = _several()
    views = config["views"]
    # The first tab is ALL, then one per zone with its code, sorted by code.
    assert [v["path"] for v in views] == ["compare", "zone-es", "zone-fr", "zone-pl"]
    assert [v["title"] for v in views] == ["ALL", "ES", "FR", "PL"]
    assert all(v["type"] == "sections" for v in views)


def test_the_comparison_has_a_gauge_per_zone_for_each_metric():
    config = _several()
    gauges = _of_type(config, "gauge", view=0)
    assert len(gauges) == 3 * 4  # three zones, four metrics
    carbon = [g for g in gauges if "carbon_footprint" in g["entity"]]
    assert [g["name"] for g in carbon] == ["Spain", "France", "Poland"]
    assert {g["entity"] for g in carbon} == {
        f"sensor.wattnet_spain_{z}_carbon_footprint" for z in ("es", "fr", "pl")
    }


def test_the_comparison_draws_all_the_zones_in_the_same_graph():
    config = _several()
    graphs = _of_type(config, "statistics-graph", view=0)
    assert len(graphs) == 4  # carbon, water footprint, water impact and score
    carbon = graphs[0]
    assert carbon["entities"] == [
        {"entity": "wattnet:es_carbon_footprint_life_cycle", "name": "Spain"},
        {"entity": "wattnet:fr_carbon_footprint_life_cycle", "name": "France"},
        {"entity": "wattnet:pl_carbon_footprint_life_cycle", "name": "Poland"},
    ]


def test_each_zone_view_has_its_own_entities_and_reimport_button():
    config = _several()
    for index, code in ((1, "es"), (2, "fr"), (3, "pl")):
        gauges = _of_type(config, "gauge", view=index)
        assert {g["entity"] for g in gauges} == {
            f"sensor.wattnet_spain_{code}_{key}" for key in GAUGES
        }
        action = _of_type(config, "tile", view=index)[1]["tap_action"]
        assert action["data"] == {"config_entry": f"entry-{code}"}


def test_only_the_zone_with_a_grid_source_gets_the_home_section():
    config = _several()
    assert _of_type(config, "statistic", view=1)  # Spain has a grid source
    assert not _of_type(config, "statistic", view=2)  # France
    assert not _of_type(config, "statistic", view=3)  # Poland


def test_the_comparison_names_the_zones_on_one_short_line():
    intro = _of_type(_several(), "markdown", view=0)[0]["content"]
    assert intro == "**3 zones**: Spain (ES), France (FR), Poland (PL)"
    two = build_dashboard([_zone(), _zone("PL", "Poland")], "en")
    text = _of_type(two, "markdown", view=0)[0]["content"]
    assert text == "**2 zones**: Spain (ES), Poland (PL)"
    assert "\n" not in text and "." not in text  # one line, no second sentence


def test_with_many_zones_the_comparison_lists_only_the_codes():
    codes = ["ES", "FR", "PL", "DE", "IT"]
    many = build_dashboard([_zone(c, c.title()) for c in codes], "en")
    intro = _of_type(many, "markdown", view=0)[0]["content"]
    assert intro == "**5 zones**: DE, ES, FR, IT, PL"


# -- language and missing data --------------------------------------------------


def test_spanish_and_english_have_the_same_labels():
    assert set(texts("es")) == set(texts("en"))
    assert texts("es-ES")["week"] == "Últimos 7 días"
    assert texts("fr")["week"] == "Last 7 days"  # English by default


def test_the_spanish_dashboard_is_in_spanish():
    config = build_dashboard([_zone(grid=True)], "es")
    headings = [c["heading"] for c in _of_type(config, "heading")]
    assert "Huella de carbono" in headings and "Tu consumo" in headings
    assert "Últimos 7 días" in headings
    assert _of_type(config, "tile")[1]["name"] == "Reimportar histórico"
    several = build_dashboard([_zone(), _zone("FR", "France")], "es")
    assert several["views"][0]["title"] == "ALL"
    assert [v["title"] for v in several["views"][1:]] == ["ES", "FR"]
    assert "2 zonas" in _of_type(several, "markdown", view=0)[0]["content"]


def test_missing_entities_do_not_break_the_dashboard():
    zone = ZoneData(
        entry_id="x",
        zone="ES",
        zone_name="Spain",
        entity_ids={},
        consolidated_entity=None,
        stat_ids=dict(STATS),
        grid_stat_ids={},
    )
    config = build_dashboard([zone], "en")
    assert not _of_type(config, "gauge")
    assert not _of_type(config, "tile")
    assert "No data yet" in _of_type(config, "markdown")[0]["content"]


def test_the_dashboard_is_json_serialisable():
    import json

    json.dumps(_build(grid=True))
    json.dumps(_several())


def test_the_icon_script_registers_the_logo_as_a_custom_icon_set():
    from pathlib import Path

    script = (
        Path(__file__).parent.parent
        / "custom_components"
        / "wattnet"
        / "frontend"
        / "wattnet-icons.js"
    ).read_text()
    assert "window.customIconsets.wattnet" in script
    assert "viewBox" in script and "path:" in script
    # A single path, so Home Assistant can tint it with the theme colour.
    assert script.count(" d=") == 0 and script.count("M797.85") == 1


def _flatten(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for card in cards:
        out.append(card)
        out += _flatten(card.get("cards", []))
    return out


# -- registered in Home Assistant ---------------------------------------------


async def _setup_with_lovelace(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "frontend", {})
    assert await async_setup_component(hass, "lovelace", {})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


def _second_zone(config_entry: MockConfigEntry) -> MockConfigEntry:
    from custom_components.wattnet.const import CONF_ZONE, CONF_ZONE_NAME

    return MockConfigEntry(
        domain="wattnet",
        unique_id="FR",
        title="Wattnet - France (FR)",
        data={**config_entry.data, CONF_ZONE: "FR", CONF_ZONE_NAME: "France"},
        options=config_entry.options,
    )


async def _config(client, id_: int = 1) -> dict[str, Any]:
    await client.send_json(
        {"id": id_, "type": "lovelace/config", "url_path": "wattnet"}
    )
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]


async def test_the_dashboard_is_registered_with_the_logo_and_served(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    await _setup_with_lovelace(hass, config_entry)

    assert "wattnet" in hass.data[LOVELACE_DATA].dashboards
    panel = hass.data["frontend_panels"]["wattnet"]
    assert panel.sidebar_icon == "wattnet:logo"
    assert panel.sidebar_title == "Wattnet"

    config = await _config(await hass_ws_client(hass))
    (view,) = config["views"]
    gauges = [
        card
        for section in view["sections"]
        for card in _flatten(section["cards"])
        if card["type"] == "gauge"
    ]
    # The headline has no badges: the line under it already says the values.
    headline = view["sections"][0]
    assert "badges" not in headline["cards"][0]
    # The entity ids come from the registry, not from a guess.
    assert gauges[0]["entity"] == "sensor.wattnet_spain_es_carbon_footprint"
    assert hass.states.get(gauges[0]["entity"]) is not None


async def test_the_script_with_the_logo_is_served_and_loaded_by_the_frontend(
    hass: HomeAssistant,
    hass_client,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL

    await _setup_with_lovelace(hass, config_entry)

    assert "/wattnet_static/wattnet-icons.js" in hass.data[DATA_EXTRA_MODULE_URL].urls
    client = await hass_client()
    response = await client.get("/wattnet_static/wattnet-icons.js")
    assert response.status == 200
    assert "window.customIconsets.wattnet" in await response.text()


async def test_it_is_listed_in_the_dashboards_and_cannot_be_edited(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    await _setup_with_lovelace(hass, config_entry)
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "lovelace/dashboards/list"})
    listed = (await client.receive_json())["result"]
    item = next(d for d in listed if d["url_path"] == "wattnet")
    assert item["mode"] == "yaml"
    assert item["title"] == "Wattnet"
    assert item["icon"] == "wattnet:logo"
    assert item["show_in_sidebar"] is True

    await client.send_json(
        {
            "id": 2,
            "type": "lovelace/config/save",
            "url_path": "wattnet",
            "config": {"views": []},
        }
    )
    assert not (await client.receive_json())["success"]


async def test_all_the_zones_share_the_one_dashboard(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    await _setup_with_lovelace(hass, config_entry)
    client = await hass_ws_client(hass)
    assert len((await _config(client, 1))["views"]) == 1  # just Spain

    second = _second_zone(config_entry)
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    # Still one dashboard, now with a comparison and a view per zone.
    assert [d for d in hass.data[LOVELACE_DATA].dashboards if d == "wattnet"]
    assert "wattnet-fr" not in hass.data[LOVELACE_DATA].dashboards
    views = (await _config(client, 2))["views"]
    assert [v["path"] for v in views] == ["compare", "zone-es", "zone-fr"]


async def test_removing_a_zone_takes_it_out_and_the_last_one_removes_the_dashboard(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    await _setup_with_lovelace(hass, config_entry)
    second = _second_zone(config_entry)
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    client = await hass_ws_client(hass)
    assert len((await _config(client, 1))["views"]) == 3

    assert await hass.config_entries.async_unload(second.entry_id)
    assert [v["path"] for v in (await _config(client, 2))["views"]] == ["overview"]
    assert "wattnet" in hass.data[LOVELACE_DATA].dashboards

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert "wattnet" not in hass.data[LOVELACE_DATA].dashboards
    assert "wattnet" not in hass.data["frontend_panels"]


async def test_a_zone_that_hides_the_dashboard_is_left_out(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    off = MockConfigEntry(
        domain="wattnet",
        unique_id="ES",
        title=config_entry.title,
        data=config_entry.data,
        options={**config_entry.options, CONF_SHOW_DASHBOARD: False},
    )
    await _setup_with_lovelace(hass, off)
    assert "wattnet" not in hass.data[LOVELACE_DATA].dashboards


async def test_turning_it_off_and_on_adds_and_removes_the_zone(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    await _setup_with_lovelace(hass, config_entry)
    assert "wattnet" in hass.data[LOVELACE_DATA].dashboards

    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_SHOW_DASHBOARD: False}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "wattnet" not in hass.data[LOVELACE_DATA].dashboards

    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_SHOW_DASHBOARD: True}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "wattnet" in hass.data[LOVELACE_DATA].dashboards


async def test_open_dashboards_are_told_when_the_zones_change(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    from homeassistant.components.lovelace.const import EVENT_LOVELACE_UPDATED
    from pytest_homeassistant_custom_component.common import async_capture_events

    await _setup_with_lovelace(hass, config_entry)
    events = async_capture_events(hass, EVENT_LOVELACE_UPDATED)

    second = _second_zone(config_entry)
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert any(e.data == {"url_path": "wattnet"} for e in events)


async def test_without_lovelace_the_integration_still_works(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.wattnet_spain_es_carbon_footprint") is not None


@pytest.mark.parametrize("metric", METRICS, ids=lambda m: m.key)
def test_every_metric_has_a_label_in_both_languages(metric):
    for language in ("en", "es"):
        assert metric.key in texts(language)


# -- only one dashboard ----------------------------------------------------------


async def test_the_dashboard_is_the_only_one_and_is_called_wattnet(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    await _setup_with_lovelace(hass, config_entry)
    ours = [d for d in hass.data[LOVELACE_DATA].dashboards if d and "wattnet" in d]
    assert ours == ["wattnet"]  # no embedded web dashboard, no other one
    assert [p for p in hass.data["frontend_panels"] if "wattnet" in p] == ["wattnet"]
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "lovelace/dashboards/list"})
    listed = [
        d for d in (await client.receive_json())["result"] if "wattnet" in d["url_path"]
    ]
    assert [(d["url_path"], d["title"], d["icon"]) for d in listed] == [
        ("wattnet", "Wattnet", "wattnet:logo")
    ]


def test_nothing_in_the_integration_embeds_the_official_web_dashboard():
    from pathlib import Path

    folder = Path(__file__).parent.parent / "custom_components" / "wattnet"
    for path in folder.rglob("*"):
        if path.suffix in (".py", ".json", ".js") and "__pycache__" not in str(path):
            text = path.read_text(encoding="utf-8")
            assert "dashboard.wattnet.eu" not in text, path
            assert '"iframe"' not in text, path
            assert "wattnet-web" not in text, path
