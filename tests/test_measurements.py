"""Tests for checking units, NaN and value ranges before sending."""

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ortsnetz_auslastung import _send
from custom_components.ortsnetz_auslastung.const import DOMAIN

URL = "https://example.test/v1/measurements"


async def _payload(hass, aioclient_mock, voltages=("230", "231", "229"), voltage_unit="V", forecast=None, frequency=None, capacity=None):
    """Send once and return the payload, or None if nothing was sent."""
    for index, value in enumerate(voltages, start=1):
        hass.states.async_set(f"sensor.l{index}", value, {"unit_of_measurement": voltage_unit} if voltage_unit else {})
    data = {
        "api_url": URL, "l1_entity": "sensor.l1", "l2_entity": "sensor.l2", "l3_entity": "sensor.l3",
        "latitude": 52.5, "longitude": 13.4,
    }
    if forecast is not None:
        hass.states.async_set("sensor.forecast", forecast[0], {"unit_of_measurement": forecast[1]} if forecast[1] else {})
        data["pv_forecast_entity"] = "sensor.forecast"
    if capacity is not None:
        data["plant_capacity_kwp"] = capacity
    if frequency is not None:
        hass.states.async_set("sensor.frequency", frequency[0], {"unit_of_measurement": frequency[1]} if frequency[1] else {})
        data["grid_frequency_entity"] = "sensor.frequency"
    aioclient_mock.post(URL, json={})
    entry = MockConfigEntry(domain=DOMAIN, version=3, data=data)
    entry.add_to_hass(hass)
    await _send(hass, entry)
    return aioclient_mock.mock_calls[0][2] if aioclient_mock.call_count else None


@pytest.mark.parametrize("voltages", [("nan", "231", "229"), ("230", "inf", "229"), ("230", "231", "-inf")])
async def test_non_finite_voltage_is_not_sent(hass, aioclient_mock, voltages):
    assert await _payload(hass, aioclient_mock, voltages=voltages) is None


async def test_voltage_in_other_unit_is_not_sent(hass, aioclient_mock):
    assert await _payload(hass, aioclient_mock, voltages=("0.23", "0.231", "0.229"), voltage_unit="kV") is None


async def test_voltage_without_unit_is_still_sent(hass, aioclient_mock):
    payload = await _payload(hass, aioclient_mock, voltage_unit=None)
    assert (payload["l1_v"], payload["l2_v"], payload["l3_v"]) == (230, 231, 229)


@pytest.mark.parametrize("voltages", [
    ("230", "-1", "-1"), ("-1", "231", "-1"), ("-1", "-1", "229"),
    ("230", "231", "-1"), ("230", "-1", "229"), ("-1", "231", "229"),
    ("150", "-1", "300"),
])
async def test_excluded_phases_are_sent_as_minus_one(hass, aioclient_mock, voltages):
    payload = await _payload(hass, aioclient_mock, voltages=voltages)
    assert tuple(payload[key] for key in ("l1_v", "l2_v", "l3_v")) == tuple(map(float, voltages))


@pytest.mark.parametrize("voltages", [
    ("-1", "-1", "-1"), ("230", "-2", "-1"), ("230", "0", "-1"),
    ("230", "149.9", "-1"), ("230", "300.1", "-1"),
    ("230", "unavailable", "-1"), ("230", "nan", "-1"),
])
async def test_exclusion_does_not_bypass_voltage_validation(hass, aioclient_mock, voltages):
    assert await _payload(hass, aioclient_mock, voltages=voltages) is None


@pytest.mark.parametrize(
    ("forecast", "expected"),
    [
        (("12.3", "kWh"), 12.3),
        (("12300", "Wh"), 12.3),
        (("0.0123", "MWh"), 12.3),
        (("12.3", None), 12.3),
        (("0", "kWh"), 0),
        (("12.3", "W"), None),
        (("nan", "kWh"), None),
        (("-1", "kWh"), None),
        (("100001", "kWh"), None),
    ],
)
async def test_forecast_unit_and_range(hass, aioclient_mock, forecast, expected):
    payload = await _payload(hass, aioclient_mock, forecast=forecast, capacity=9.8)
    assert payload["l1_v"] == 230
    if expected is None:
        assert "pv_forecast_kwh" not in payload
    else:
        assert payload["pv_forecast_kwh"] == pytest.approx(expected)


@pytest.mark.parametrize("forecast", [("12.3", "kWh"), ("0", "kWh")])
async def test_forecast_without_capacity_is_omitted_but_voltages_are_sent(hass, aioclient_mock, caplog, forecast):
    payload = await _payload(hass, aioclient_mock, forecast=forecast)
    assert tuple(payload[key] for key in ("l1_v", "l2_v", "l3_v")) == (230, 231, 229)
    assert "pv_forecast_kwh" not in payload
    assert "plant_capacity_kwp" not in payload
    assert aioclient_mock.call_count == 1
    assert "Anlagengröße" in caplog.text


@pytest.mark.parametrize(("forecast", "expected"), [
    (("0", "kWh"), 0),
    (("98", "kWh"), 98),
    (("98000", "Wh"), 98),
    (("98.1", "kWh"), None),
    (("98100", "Wh"), None),
])
async def test_forecast_respects_capacity_limit(hass, aioclient_mock, forecast, expected):
    payload = await _payload(hass, aioclient_mock, forecast=forecast, capacity=9.8)
    assert payload["l1_v"] == 230
    assert payload["plant_capacity_kwp"] == 9.8
    assert aioclient_mock.call_count == 1
    if expected is None:
        assert "pv_forecast_kwh" not in payload
    else:
        assert payload["pv_forecast_kwh"] == pytest.approx(expected)


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [
        (("50.01", "Hz"), 50.01),
        (("49.9", None), 49.9),
        (("45", "Hz"), 45),
        (("55", "Hz"), 55),
        (("60.5", "Hz"), None),
        (("0", "Hz"), None),
        (("50010", "mHz"), None),
        (("nan", "Hz"), None),
    ],
)
async def test_frequency_unit_and_range(hass, aioclient_mock, frequency, expected):
    payload = await _payload(hass, aioclient_mock, frequency=frequency)
    assert payload["l1_v"] == 230
    if expected is None:
        assert "grid_frequency_hz" not in payload
    else:
        assert payload["grid_frequency_hz"] == expected


def _form_input(latitude, longitude):
    return {
        "api_url": URL,
        "phase_voltages": {"l1_entity": "sensor.l1", "l2_entity": "sensor.l2", "l3_entity": "sensor.l3"},
        "latitude": latitude, "longitude": longitude,
    }


@pytest.mark.parametrize(("latitude", "longitude"), [(999, 13.4), (-90.1, 13.4), (52.5, 180.5), (52.5, -999)])
async def test_flow_rejects_coordinates_out_of_range(hass, latitude, longitude):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(result["flow_id"], _form_input(latitude, longitude))


async def test_flow_accepts_coordinates_at_limits(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch("custom_components.ortsnetz_auslastung.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _form_input(-90, 180))
    assert result["type"] is FlowResultType.CREATE_ENTRY
