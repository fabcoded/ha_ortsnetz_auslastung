"""Minimum, maximum and time-weighted mean go out with each measurement after the first."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ortsnetz_auslastung.const import DOMAIN

URL = "https://example.test/v1/measurements"
VOLT = {"unit_of_measurement": "V"}
HERTZ = {"unit_of_measurement": "Hz"}


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("custom_components.ortsnetz_auslastung.asyncio.sleep"):
        yield


async def _setup(hass, aioclient_mock, l3="229"):
    for entity_id, value in (("sensor.l1", "230"), ("sensor.l2", "231"), ("sensor.l3", l3)):
        hass.states.async_set(entity_id, value, VOLT)
    hass.states.async_set("sensor.frequency", "50.00", HERTZ)
    aioclient_mock.post(URL, json={})
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={
        "api_url": URL, "l1_entity": "sensor.l1", "l2_entity": "sensor.l2", "l3_entity": "sensor.l3",
        "grid_frequency_entity": "sensor.frequency", "latitude": 52.5, "longitude": 13.4,
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _set(hass, freezer, after, entity_id, value, attributes=VOLT):
    freezer.tick(after)
    hass.states.async_set(entity_id, value, attributes)
    await hass.async_block_till_done()


async def _cycle(hass, aioclient_mock, freezer, after):
    """Advance to the next send and return its payload."""
    freezer.tick(after)
    aioclient_mock.clear_requests()
    aioclient_mock.post(URL, json={})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    return aioclient_mock.mock_calls[0][2]


async def test_first_send_has_no_statistics(hass, aioclient_mock):
    await _setup(hass, aioclient_mock)
    payload = aioclient_mock.mock_calls[0][2]
    assert payload["l1_v"] == 230
    assert not [key for key in payload if key.endswith(("_min", "_max", "_avg"))]


async def test_time_weighted_mean_and_peak(hass, aioclient_mock, freezer):
    await _setup(hass, aioclient_mock)
    await _set(hass, freezer, timedelta(minutes=4), "sensor.l1", "240")
    payload = await _cycle(hass, aioclient_mock, freezer, timedelta(minutes=1))
    assert (payload["l1_v_min"], payload["l1_v_max"], payload["l1_v_avg"]) == (230, 240, 232)
    assert (payload["l2_v_min"], payload["l2_v_max"], payload["l2_v_avg"]) == (231, 231, 231)
    assert payload["l1_v"] == 240


async def test_frequency_statistics(hass, aioclient_mock, freezer):
    await _setup(hass, aioclient_mock)
    await _set(hass, freezer, timedelta(seconds=150), "sensor.frequency", "49.98", HERTZ)
    payload = await _cycle(hass, aioclient_mock, freezer, timedelta(seconds=150))
    assert (payload["grid_frequency_hz_min"], payload["grid_frequency_hz_max"]) == (49.98, 50)
    assert payload["grid_frequency_hz_avg"] == pytest.approx(49.99)


async def test_unavailable_time_is_left_out(hass, aioclient_mock, freezer):
    await _setup(hass, aioclient_mock)
    await _set(hass, freezer, timedelta(minutes=1), "sensor.l2", "unavailable", {})
    await _set(hass, freezer, timedelta(minutes=1), "sensor.l2", "237")
    payload = await _cycle(hass, aioclient_mock, freezer, timedelta(minutes=3))
    assert (payload["l2_v_min"], payload["l2_v_max"], payload["l2_v_avg"]) == (231, 237, 235.5)


async def test_mostly_unavailable_sends_no_statistics(hass, aioclient_mock, freezer):
    await _setup(hass, aioclient_mock)
    await _set(hass, freezer, timedelta(minutes=1), "sensor.l2", "unavailable", {})
    await _set(hass, freezer, timedelta(minutes=3), "sensor.l2", "237")
    payload = await _cycle(hass, aioclient_mock, freezer, timedelta(minutes=1))
    assert payload["l2_v"] == 237
    assert "l2_v_avg" not in payload
    assert payload["l1_v_avg"] == 230


async def test_excluded_phase_gets_no_statistics(hass, aioclient_mock, freezer):
    await _setup(hass, aioclient_mock, l3="-1")
    payload = await _cycle(hass, aioclient_mock, freezer, timedelta(minutes=5))
    assert payload["l3_v"] == -1
    assert "l3_v_avg" not in payload
    assert payload["l1_v_avg"] == 230


async def test_changed_sensor_starts_a_new_interval(hass, aioclient_mock, freezer):
    entry = await _setup(hass, aioclient_mock)
    hass.states.async_set("sensor.l1_new", "233", VOLT)
    hass.config_entries.async_update_entry(entry, options={**entry.data, "l1_entity": "sensor.l1_new"})
    await hass.async_block_till_done()
    payload = await _cycle(hass, aioclient_mock, freezer, timedelta(minutes=5))
    assert payload["l1_v"] == 233
    assert "l1_v_avg" not in payload  # the new sensor's interval begins with this send
    await _set(hass, freezer, timedelta(minutes=2), "sensor.l1_new", "235")
    payload = await _cycle(hass, aioclient_mock, freezer, timedelta(minutes=3))
    assert (payload["l1_v_min"], payload["l1_v_max"]) == (233, 235)
