"""Status entity for Ortsnetz-Auslastung."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import status_signal, storage_recommendation_signal

_STATUS_STATES = {"green": "normal", "yellow": "warning", "red": "critical", "unavailable": "unavailable"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the voltage status and storage recommendation sensors."""
    async_add_entities([VoltageStatusSensor(entry), BatteryStorageRecommendationSensor(entry)])


class VoltageStatusSensor(SensorEntity):
    """Expose the last server-evaluated voltage status."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "voltage_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["normal", "warning", "critical"]
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_voltage_status"
        self._entry = entry
        self._status: dict[str, str] | None = None

    @property
    def native_value(self) -> str | None:
        if self._status is None:
            return None
        return _STATUS_STATES[self._status["overall"]]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._status is None:
            return None
        return {
            "L1": _STATUS_STATES[self._status["l1"]],
            "L2": _STATUS_STATES[self._status["l2"]],
            "L3": _STATUS_STATES[self._status["l3"]],
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, status_signal(self._entry.entry_id), self._async_update_status)
        )

    @callback
    def _async_update_status(self, status: dict[str, str] | None) -> None:
        """Store a dispatcher update in Home Assistant's event loop."""
        self._status = status
        self._attr_available = status is not None
        self.async_write_ha_state()


class BatteryStorageRecommendationSensor(SensorEntity):
    """Expose the API's battery storage recommendation."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "storage_recommendation"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["charge", "discharge", "none"]
    _attr_icon = "mdi:battery-sync-outline"

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_storage_recommendation"
        self._entry = entry
        self._recommendation: str | None = None

    @property
    def native_value(self) -> str | None:
        if self._recommendation is None:
            return None
        return self._recommendation

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                storage_recommendation_signal(self._entry.entry_id),
                self._async_update_recommendation,
            )
        )

    @callback
    def _async_update_recommendation(self, recommendation: str | None) -> None:
        """Store a dispatcher update in Home Assistant's event loop."""
        self._recommendation = recommendation
        self._attr_available = recommendation is not None
        self.async_write_ha_state()
