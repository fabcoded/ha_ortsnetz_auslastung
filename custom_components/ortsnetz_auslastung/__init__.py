from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util.unit_conversion import EnergyConverter
from homeassistant.loader import async_get_integration

from .const import CONF_API_URL, CONF_GRID_FREQUENCY_ENTITY, CONF_L1_ENTITY, CONF_L2_ENTITY, CONF_L3_ENTITY, CONF_LATITUDE, CONF_LONGITUDE, CONF_PLANT_CAPACITY_KWP, CONF_PV_FORECAST_ENTITY, CONF_SMARTMETER_MODEL, DOMAIN, MAX_PHASE_VOLTAGE_V, MIN_PHASE_VOLTAGE_V, PLATFORMS, status_signal, storage_recommendation_signal

_LOGGER = logging.getLogger(__name__)


def _value(hass: HomeAssistant, entity_id: str, unit: str | None = None) -> float | None:
    """Return a finite sensor value in the expected unit, or None if that is not possible."""
    state = hass.states.get(entity_id)
    if state is None:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    state_unit = state.attributes.get("unit_of_measurement")
    # Sensors without a unit are trusted as before.
    if unit is None or state_unit in (None, unit):
        return value
    if unit == "kWh" and state_unit in EnergyConverter.VALID_UNITS:
        return EnergyConverter.convert(value, state_unit, unit)
    _LOGGER.warning("Sensor %s liefert %s, erwartet wird %s", entity_id, state_unit, unit)
    return None


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Update existing entries to the current integration name."""
    if entry.version < 3:
        data = dict(entry.data)
        data.pop("token", None)
        hass.config_entries.async_update_entry(entry, title="Ortsnetz-Auslastung", data=data, version=3)
    return True


def _publish(hass: HomeAssistant, entry: ConfigEntry, status: dict | None = None, recommendation: str | None = None) -> None:
    """Update both sensors; None marks a value as unavailable."""
    async_dispatcher_send(hass, status_signal(entry.entry_id), status)
    async_dispatcher_send(hass, storage_recommendation_signal(entry.entry_id), recommendation)


async def _send(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Options override the initial setup data.
    data = {**entry.data, **entry.options}
    values = [_value(hass, data[key], "V") for key in (CONF_L1_ENTITY, CONF_L2_ENTITY, CONF_L3_ENTITY)]
    if any(value is None for value in values):
        _LOGGER.warning("Spannungssensor für Ortsnetz-Auslastung ist nicht verfügbar")
        _publish(hass, entry)
        return
    invalid_phases = [
        f"L{index}={value:g} V"
        for index, value in enumerate(values, start=1)
        if value < MIN_PHASE_VOLTAGE_V or value > MAX_PHASE_VOLTAGE_V
    ]
    if invalid_phases:
        _LOGGER.error(
            "Ortsnetz-Auslastung sendet keine Messung: unplausible Phasenspannung (%s). "
            "Erwartet werden Werte zwischen %g und %g V; Sensor-Auswahl und Einheit prüfen.",
            ", ".join(invalid_phases), MIN_PHASE_VOLTAGE_V, MAX_PHASE_VOLTAGE_V,
        )
        _publish(hass, entry)
        return
    payload = {
        "observed_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "latitude": data[CONF_LATITUDE], "longitude": data[CONF_LONGITUDE],
        "l1_v": values[0], "l2_v": values[1], "l3_v": values[2],
        "integration_version": str((await async_get_integration(hass, DOMAIN)).version),
    }
    if data.get(CONF_PLANT_CAPACITY_KWP) is not None:
        payload[CONF_PLANT_CAPACITY_KWP] = data[CONF_PLANT_CAPACITY_KWP]
    smartmeter_model = (data.get(CONF_SMARTMETER_MODEL) or "").strip()
    if smartmeter_model:
        payload[CONF_SMARTMETER_MODEL] = smartmeter_model
    forecast_entity = data.get(CONF_PV_FORECAST_ENTITY)
    if forecast_entity:
        forecast_kwh = _value(hass, forecast_entity, "kWh")
        if forecast_kwh is None:
            _LOGGER.warning("PV-Forecast-Sensor für Ortsnetz-Auslastung ist nicht verfügbar")
        elif not 0 <= forecast_kwh <= 100000:
            _LOGGER.warning("PV-Forecast %g kWh liegt außerhalb von 0 bis 100000 kWh und wird nicht gesendet", forecast_kwh)
        else:
            payload["pv_forecast_kwh"] = forecast_kwh
    frequency_entity = data.get(CONF_GRID_FREQUENCY_ENTITY)
    if frequency_entity:
        frequency_hz = _value(hass, frequency_entity, "Hz")
        if frequency_hz is None:
            _LOGGER.warning("Netzfrequenz-Sensor für Ortsnetz-Auslastung ist nicht verfügbar")
        elif not 45 <= frequency_hz <= 55:
            _LOGGER.warning("Netzfrequenz %g Hz liegt außerhalb von 45 bis 55 Hz und wird nicht gesendet", frequency_hz)
        else:
            payload["grid_frequency_hz"] = frequency_hz
    url = data[CONF_API_URL].rstrip("/")
    if not url.endswith("/v1/measurements"):
        url = f"{url}/v1/measurements"
    # Entries saved before the HTTPS check may still contain an http:// address.
    if not url.lower().startswith("https://"):
        _LOGGER.error("Ortsnetz-Auslastung sendet keine Messung: Die API-Adresse muss mit https:// beginnen; bitte unter Konfigurieren korrigieren")
        return
    session = async_get_clientsession(hass)
    for attempt in range(2):
        try:
            async with session.post(url, json=payload, timeout=10, allow_redirects=False) as response:
                if response.status < 300:
                    try:
                        result = await response.json(content_type=None)
                    except ValueError:
                        _LOGGER.warning("Ortsnetz-API lieferte kein gültiges JSON")
                        result = None
                    if not isinstance(result, dict):
                        result = {}
                    # Tuples instead of sets: unexpected lists or dicts must not raise TypeError.
                    status = result.get("status")
                    if not (isinstance(status, dict) and all(status.get(key) in ("green", "yellow", "red") for key in ("l1", "l2", "l3", "overall"))):
                        _LOGGER.debug("Ortsnetz-API lieferte noch keinen Spannungsstatus")
                        status = None
                    recommendation = result.get("storage_recommendation")
                    if recommendation not in ("charge", "discharge", "none"):
                        recommendation = None
                    _publish(hass, entry, status, recommendation)
                    return
                if response.status in (403, 422):
                    if response.status == 403:
                        _LOGGER.error(
                            "Ortsnetz-API hat den Standort gesperrt (HTTP 403); "
                            "Standortangaben prüfen oder den Server-Admin kontaktieren"
                        )
                    else:
                        _LOGGER.error(
                            "Ortsnetz-API hat die Messung abgelehnt (HTTP 422); "
                            "Sensorwerte und Konfiguration prüfen"
                        )
                    _publish(hass, entry)
                    return
                _LOGGER.warning("Ortsnetz-API antwortete mit HTTP %s", response.status)
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as error:
            _LOGGER.warning("Ortsnetz-Messung konnte nicht gesendet werden: %s", error)
        if attempt == 0:
            await asyncio.sleep(5)
    _publish(hass, entry)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def start_send(_: datetime | None = None) -> None:
        """Run each send as an entry task so unloading cancels it, including the retry wait."""
        entry.async_create_background_task(hass, _send(hass, entry), f"{DOMAIN}_send_{entry.entry_id}")

    entry.async_on_unload(async_track_time_interval(hass, start_send, timedelta(minutes=5)))
    start_send()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
