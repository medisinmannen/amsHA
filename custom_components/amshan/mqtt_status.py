"""MQTT status sensors for bridge information (RSSI, temperature, etc.)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from homeassistant.components import mqtt
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_CONNECTION_CONFIG, CONF_MQTT_TOPICS, DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_mqtt_status_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> list[MqttBridgeStatusSensor]:
    """Set up MQTT status sensors for bridge info."""
    connection_config = config_entry.data.get(CONF_CONNECTION_CONFIG, {})
    topics_str = connection_config.get(CONF_MQTT_TOPICS, "pulse/publish")
    # Use first topic for status sensors
    topic = topics_str.split(",")[0].strip() if topics_str else "pulse/publish"

    _LOGGER.debug("Setting up MQTT status sensors for topic: %s", topic)

    sensors = [
        MqttBridgeRssiSensor(hass, config_entry, topic),
        MqttBridgeTemperatureSensor(hass, config_entry, topic),
    ]

    return sensors


class MqttBridgeStatusSensor(SensorEntity):
    """Base class for MQTT bridge status sensors."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        topic: str,
        name: str,
        json_key: str,
        entity_id_suffix: str,
        device_class: SensorDeviceClass | None = None,
        unit_of_measurement: str | None = None,
        icon: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._topic = topic
        self._json_key = json_key
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit_of_measurement
        self._attr_icon = icon
        self._attr_native_value: float | int | str | None = None
        self._unsubscribe: Callable[[], None] | None = None
        # Set entity_id to match meter sensor pattern (e.g., sensor.kamstrup_rssi)
        self.entity_id = f"sensor.kamstrup_{entity_id_suffix}"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self._config_entry.entry_id}_{self._json_key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the bridge."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._config_entry.entry_id}_bridge")},
            name="AMS HAN Bridge",
            manufacturer="Tibber",
            model="Pulse",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to MQTT topic when entity is added."""

        @callback
        def message_received(mqtt_message: mqtt.models.ReceiveMessage) -> None:
            """Handle new MQTT message."""
            try:
                # Try to parse as JSON
                json_data = json.loads(mqtt_message.payload)
                if not isinstance(json_data, dict):
                    return

                # Extract status object
                if "status" not in json_data:
                    return

                status = json_data["status"]
                if not isinstance(status, dict):
                    return

                # Extract the specific value we're looking for
                value = status.get(self._json_key)
                if value is not None:
                    self._process_value(value)

            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                # Not JSON or doesn't have the expected structure - ignore silently
                pass
            except Exception:
                _LOGGER.exception(
                    "Unexpected error processing MQTT message for %s",
                    self._attr_name,
                )

        # Subscribe to MQTT topic with encoding=None to receive raw bytes
        self._unsubscribe = await mqtt.client.async_subscribe(
            self.hass, self._topic, message_received, encoding=None
        )

        _LOGGER.debug(
            "Subscribed to MQTT topic %s for sensor %s",
            self._topic,
            self._attr_name,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from MQTT topic when entity is removed."""
        if self._unsubscribe:
            self._unsubscribe()

    def _process_value(self, value: int | float | str) -> None:
        """Process and update the sensor value."""
        self._attr_native_value = value
        self.async_write_ha_state()


class MqttBridgeRssiSensor(MqttBridgeStatusSensor):
    """Sensor for bridge WiFi signal strength."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, topic: str
    ) -> None:
        """Initialize RSSI sensor."""
        super().__init__(
            hass,
            config_entry,
            topic,
            "Bridge RSSI",
            "rssi",
            "bridge_signal_strength",
            device_class=SensorDeviceClass.SIGNAL_STRENGTH,
            unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
            icon="mdi:wifi",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT

    def _process_value(self, value: int | float | str) -> None:
        """Process RSSI value."""
        try:
            self._attr_native_value = int(value)
            self.async_write_ha_state()
            _LOGGER.debug("Updated RSSI sensor: %d dBm", self._attr_native_value)
        except (TypeError, ValueError):
            _LOGGER.warning("Invalid RSSI value: %s", value)


class MqttBridgeTemperatureSensor(MqttBridgeStatusSensor):
    """Sensor for bridge temperature (NTC thermistor)."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, topic: str
    ) -> None:
        """Initialize temperature sensor."""
        super().__init__(
            hass,
            config_entry,
            topic,
            "Bridge Temperature",
            "ntc",
            "bridge_temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            unit_of_measurement=UnitOfTemperature.CELSIUS,
            icon="mdi:thermometer",
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT

    def _process_value(self, value: int | float | str) -> None:
        """Process temperature value."""
        try:
            self._attr_native_value = round(float(value), 2)
            self.async_write_ha_state()
            _LOGGER.debug("Updated temperature sensor: %.2f °C", self._attr_native_value)
        except (TypeError, ValueError):
            _LOGGER.warning("Invalid temperature value: %s", value)
