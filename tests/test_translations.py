"""Translations must stay complete and in sync between languages."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.wattnet.const import METRICS

DIR = Path(__file__).parent.parent / "custom_components" / "wattnet" / "translations"


def _keys(data: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key, value in data.items():
        if isinstance(value, dict):
            out |= _keys(value, f"{prefix}{key}.")
        else:
            out.add(f"{prefix}{key}")
    return out


def _load(lang: str) -> dict:
    return json.loads((DIR / f"{lang}.json").read_text(encoding="utf-8"))


def test_spanish_has_exactly_the_keys_of_english():
    assert _keys(_load("es")) == _keys(_load("en"))


def test_every_metric_and_the_consolidation_sensor_has_a_name():
    sensors = _load("en")["entity"]["sensor"]
    for metric in METRICS:
        assert sensors[metric.key]["name"]
    assert sensors["consolidated_until"]["name"]


def test_every_form_field_is_labelled_and_described():
    for lang in ("en", "es"):
        data = _load(lang)
        steps = {**data["config"]["step"], **data["options"]["step"]}
        for name, step in steps.items():
            assert step["title"], (lang, name)
            for field in step["data"]:
                assert step["data"][field], (lang, name, field)
                if "data_description" in step:
                    assert field in step["data_description"], (lang, name, field)
