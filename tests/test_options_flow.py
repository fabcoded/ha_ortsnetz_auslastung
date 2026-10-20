"""Tests for removing optional settings through the options flow."""

from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ortsnetz_auslastung import _send
from custom_components.ortsnetz_auslastung.const import DOMAIN

URL = "https://example.test/v1/measurements"
PHASES = {"l1_entity": "sensor.l1", "l2_entity": "sensor.l2", "l3_entity": "sensor.l3"}
OPTIONAL = {
    "grid_frequency_entity": "sensor.frequency",
    "plant_capacity_kwp": 9.8,
    "pv_forecast_entity": "sensor.forecast",
    "smartmeter_model": "Testzähler",
}
PAYLOAD_KEYS = {"grid_frequency_hz", "plant_capacity_kwp", "pv_forecast_kwh", "smartmeter_model"}


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("custom_components.ortsnetz_auslastung.asyncio.sleep"):
        yield


def _entry(hass):
    for entity_id, value in (("sensor.l1", "230"), ("sensor.l2", "231"), ("sensor.l3", "229"),
                             ("sensor.frequency", "50.01"), ("sensor.forecast", "12.5")):
        hass.states.async_set(entity_id, value)
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={
        "api_url": URL, **PHASES, "latitude": 52.5, "longitude": 13.4, **OPTIONAL,
    })
    entry.add_to_hass(hass)
    return entry


def _form(**optional):
    return {"api_url": URL, "phase_voltages": PHASES, "latitude": 52.5, "longitude": 13.4, **optional}


async def _payload(hass, aioclient_mock, entry):
    aioclient_mock.clear_requests()
    aioclient_mock.post(URL, json={})
    await _send(hass, entry)
    return aioclient_mock.mock_calls[0][2]


def _schema_key(result, name):
    return next(key for key in result["data_schema"].schema if key == name)


async def test_saved_optional_values_are_suggested_not_defaults(hass):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    for name, value in OPTIONAL.items():
        key = _schema_key(result, name)
        assert key.description == {"suggested_value": value}
        assert key.default is vol.UNDEFINED


async def test_cleared_optional_fields_are_no_longer_sent(hass, aioclient_mock):
    entry = _entry(hass)
    assert PAYLOAD_KEYS <= set(await _payload(hass, aioclient_mock, entry))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], _form())
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert {key: entry.options[key] for key in OPTIONAL} == dict.fromkeys(OPTIONAL)

    assert not PAYLOAD_KEYS & set(await _payload(hass, aioclient_mock, entry))


async def test_cleared_fields_are_not_suggested_again(hass):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], _form())

    result = await hass.config_entries.options.async_init(entry.entry_id)
    for name in OPTIONAL:
        key = _schema_key(result, name)
        assert not (key.description or {}).get("suggested_value")
        assert key.default is vol.UNDEFINED


async def test_cleared_capacity_also_omits_kept_forecast(hass, aioclient_mock):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], _form(pv_forecast_entity="sensor.forecast")
    )
    payload = await _payload(hass, aioclient_mock, entry)
    assert entry.options["plant_capacity_kwp"] is None
    assert entry.options["pv_forecast_entity"] == "sensor.forecast"
    assert payload["l1_v"] == 230
    assert "plant_capacity_kwp" not in payload
    assert "pv_forecast_kwh" not in payload


async def test_kept_and_changed_optional_values_are_sent(hass, aioclient_mock):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], _form(plant_capacity_kwp=12.0, smartmeter_model="Neuer Zähler")
    )
    payload = await _payload(hass, aioclient_mock, entry)
    assert payload["plant_capacity_kwp"] == 12.0
    assert payload["smartmeter_model"] == "Neuer Zähler"
    assert "grid_frequency_hz" not in payload
    assert "pv_forecast_kwh" not in payload
