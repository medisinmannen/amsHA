"""amshan platform."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from han import autodecoder, obis_map
from han import common as han_type
from homeassistant import const as ha_const
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfReactivePower,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import dispatcher, restore_state
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from . import AmsHanConfigEntry, MeterInfo, StopMessage
from .const import (
    CONF_CONNECTION_TYPE,
    CONF_OPTIONS_SCALE_FACTOR,
    DOMAIN,
    ICON_COUNTER,
    ICON_CURRENT,
    ICON_POWER_EXPORT,
    ICON_POWER_IMPORT,
    ICON_VOLTAGE,
    UNIT_KILO_VOLT_AMPERE_REACTIVE_HOURS,
)
from .mqtt_status import async_setup_mqtt_status_sensors

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER: logging.Logger = logging.getLogger(__name__)

PARTIAL_DLMS_OBIS_MAP: dict[bytes, str] = {
    b"\x01\x07\x00\xff": obis_map.FIELD_ACTIVE_POWER_IMPORT,
    b"\x02\x07\x00\xff": obis_map.FIELD_ACTIVE_POWER_EXPORT,
    b"\x03\x07\x00\xff": obis_map.FIELD_REACTIVE_POWER_IMPORT,
    b"\x04\x07\x00\xff": obis_map.FIELD_REACTIVE_POWER_EXPORT,
    b"\x1f\x07\x00\xff": obis_map.FIELD_CURRENT_L1,
    b"\x33\x07\x00\xff": obis_map.FIELD_CURRENT_L2,
    b"\x47\x07\x00\xff": obis_map.FIELD_CURRENT_L3,
    b"\x20\x07\x00\xff": obis_map.FIELD_VOLTAGE_L1,
    b"\x34\x07\x00\xff": obis_map.FIELD_VOLTAGE_L2,
    b"\x48\x07\x00\xff": obis_map.FIELD_VOLTAGE_L3,
    b"\x00\x00\x05\xff": obis_map.FIELD_METER_ID,
    b"\x60\x01\x01\xff": obis_map.FIELD_METER_TYPE_ID,
    b"\x01\x08\x00\xff": obis_map.FIELD_ACTIVE_POWER_IMPORT_TOTAL,
    b"\x02\x08\x00\xff": obis_map.FIELD_ACTIVE_POWER_EXPORT_TOTAL,
    b"\x03\x08\x00\xff": obis_map.FIELD_REACTIVE_POWER_IMPORT_TOTAL,
    b"\x04\x08\x00\xff": obis_map.FIELD_REACTIVE_POWER_EXPORT_TOTAL,
}


@dataclass(frozen=True)
class AmsHanSensorEntityDescription(SensorEntityDescription):
    """A class that describes sensor entities."""

    scale: float | None = None
    decimals: int | None = None
    use_configured_scaling: bool = False
    is_hour_sensor: bool = False


SENSOR_TYPES: dict[str, AmsHanSensorEntityDescription] = {
    sensor.key: sensor
    for sensor in [
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_METER_ID,
            entity_category=EntityCategory.DIAGNOSTIC,
            name="Meter ID",
            use_configured_scaling=False,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_METER_MANUFACTURER,
            entity_category=EntityCategory.DIAGNOSTIC,
            name="Meter manufacturer",
            use_configured_scaling=False,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_METER_MANUFACTURER_ID,
            entity_category=EntityCategory.DIAGNOSTIC,
            name="Meter manufacturer ID",
            use_configured_scaling=False,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_METER_TYPE,
            entity_category=EntityCategory.DIAGNOSTIC,
            name="Meter type",
            use_configured_scaling=False,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_OBIS_LIST_VER_ID,
            entity_category=EntityCategory.DIAGNOSTIC,
            name="OBIS List version identifier",
            use_configured_scaling=False,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_ACTIVE_POWER_IMPORT,
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
            icon=ICON_POWER_IMPORT,
            name="Active power import (Q1+Q4)",
            decimals=0,
            use_configured_scaling=True,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_ACTIVE_POWER_EXPORT,
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
            icon=ICON_POWER_EXPORT,
            name="Active power export (Q2+Q3)",
            decimals=0,
            use_configured_scaling=True,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_REACTIVE_POWER_IMPORT,
            device_class=SensorDeviceClass.REACTIVE_POWER,
            native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
            state_class=SensorStateClass.MEASUREMENT,
            icon=ICON_POWER_IMPORT,
            name="Reactive power import (Q1+Q2)",
            decimals=0,
            use_configured_scaling=True,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_REACTIVE_POWER_EXPORT,
            device_class=SensorDeviceClass.REACTIVE_POWER,
            native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
            state_class=SensorStateClass.MEASUREMENT,
            icon=ICON_POWER_EXPORT,
            name="Reactive power export (Q3+Q4)",
            decimals=0,
            use_configured_scaling=True,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_CURRENT_L1,
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
            icon=ICON_CURRENT,
            name="Current phase L1",
            decimals=3,
            use_configured_scaling=True,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_CURRENT_L2,
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
            name="Current phase L2",
            decimals=3,
            use_configured_scaling=True,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_CURRENT_L3,
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
            name="Current phase L3",
            decimals=3,
            use_configured_scaling=True,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_VOLTAGE_L1,
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
            icon=ICON_VOLTAGE,
            name="Phase L1 voltage",
            decimals=1,
            use_configured_scaling=False,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_VOLTAGE_L2,
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
            icon=ICON_VOLTAGE,
            name="Phase L2 voltage",
            decimals=1,
            use_configured_scaling=False,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_VOLTAGE_L3,
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
            icon=ICON_VOLTAGE,
            name="Phase L3 voltage",
            decimals=1,
            use_configured_scaling=False,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_ACTIVE_POWER_IMPORT_TOTAL,
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
            icon=ICON_COUNTER,
            name="Cumulative hourly active import energy (A+) (Q1+Q4)",
            scale=0.001,
            decimals=2,
            use_configured_scaling=True,
            is_hour_sensor=True,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_ACTIVE_POWER_EXPORT_TOTAL,
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
            icon=ICON_COUNTER,
            name="Cumulative hourly active export energy (A-) (Q2+Q3)",
            scale=0.001,
            decimals=2,
            use_configured_scaling=True,
            is_hour_sensor=True,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_REACTIVE_POWER_IMPORT_TOTAL,
            native_unit_of_measurement=UNIT_KILO_VOLT_AMPERE_REACTIVE_HOURS,
            state_class=SensorStateClass.TOTAL_INCREASING,
            icon=ICON_COUNTER,
            name="Cumulative hourly reactive import energy (R+) (Q1+Q2)",
            scale=0.001,
            decimals=2,
            use_configured_scaling=True,
            is_hour_sensor=True,
        ),
        AmsHanSensorEntityDescription(
            key=obis_map.FIELD_REACTIVE_POWER_EXPORT_TOTAL,
            native_unit_of_measurement=UNIT_KILO_VOLT_AMPERE_REACTIVE_HOURS,
            state_class=SensorStateClass.TOTAL_INCREASING,
            icon=ICON_COUNTER,
            name="Cumulative hourly reactive export energy (R-) (Q3+Q4)",
            scale=0.001,
            decimals=2,
            use_configured_scaling=True,
            is_hour_sensor=True,
        ),
    ]
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AmsHanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add hantest sensor platform from a config_entry."""
    _LOGGER.debug("Sensor async_setup_entry starting.")

    processor: MeterMeasureProcessor = MeterMeasureProcessor(
        hass,
        config_entry,
        async_add_entities,
        config_entry.runtime_data.integration.measure_queue,
    )

    config_entry.runtime_data.integration.add_task(
        hass.loop.create_task(processor.async_process_measures_loop())
    )

    # Add MQTT status sensors (RSSI, temperature) if using MQTT connection
    connection_type = config_entry.data.get(CONF_CONNECTION_TYPE)
    _LOGGER.debug("Config entry connection type: %s", connection_type)
    if str(connection_type).lower() in {"hass_mqtt", "mqtt"}:
        mqtt_sensors = await async_setup_mqtt_status_sensors(hass, config_entry)
        if mqtt_sensors:
            async_add_entities(mqtt_sensors, update_before_add=False)
            _LOGGER.debug(
                "Added %d MQTT status sensors (RSSI, temperature)", len(mqtt_sensors)
            )
        else:
            _LOGGER.warning(
                "MQTT connection configured, but no status sensors were created"
            )
    else:
        _LOGGER.debug(
            "Skipping MQTT status sensors because connection type is %s",
            connection_type,
        )

    _LOGGER.debug("Sensor async_setup_entry ended.")


class AmsHanEntity(SensorEntity):
    """Representation of a AmsHan sensor."""

    def __init__(
        self,
        entity_description: AmsHanSensorEntityDescription,
        measure_data: dict[str, str | int | float | dt.datetime],
        new_measure_signal_name: str,
        scale_factor: float,
        meter_info: MeterInfo,
        config_entry_id: str,
    ) -> None:
        """Initialize AmsHanEntity class."""
        if entity_description is None:
            msg = "entity_description is required"
            raise TypeError(msg)
        if measure_data is None:
            msg = "measure_data is required"
            raise TypeError(msg)
        if obis_map.FIELD_METER_ID not in measure_data and (
            obis_map.FIELD_METER_MANUFACTURER not in measure_data
            and obis_map.FIELD_METER_MANUFACTURER_ID not in measure_data
        ):
            msg = (
                f"Expected element {obis_map.FIELD_METER_ID} or "
                f"{obis_map.FIELD_METER_MANUFACTURER} / "
                f"{obis_map.FIELD_METER_MANUFACTURER_ID} not in measure_data."
            )
            raise ValueError(msg)
        if new_measure_signal_name is None:
            msg = "new_measure_signal_name is required"
            raise TypeError(msg)

        self.entity_description: AmsHanSensorEntityDescription = entity_description
        self._measure_data = measure_data
        self._new_measure_signal_name = new_measure_signal_name
        self._async_remove_dispatcher: Callable[[], None] | None = None
        self._meter_info: MeterInfo = (
            meter_info if meter_info else MeterInfo.from_measure_data(measure_data)
        )
        self._scale_factor = (
            int(scale_factor)
            if scale_factor == math.floor(scale_factor)
            else scale_factor
        )
        self._config_entry_id = config_entry_id

        manufacturer = (
            self._meter_info.manufacturer
            if self._meter_info.manufacturer
            else self._meter_info.manufacturer_id
        )
        self.entity_id = f"sensor.{manufacturer}_{entity_description.key}".lower()
        self._unique_id = None

    @staticmethod
    def is_measure_id_supported(measure_id: str) -> bool:
        """Check if an entity can be created for measure id."""
        return measure_id in SENSOR_TYPES

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""

        @callback
        def on_new_measure(
            measure_data: dict[str, str | int | float | dt.datetime],
        ) -> None:
            if self.measure_id in measure_data:
                self._measure_data = measure_data
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "Update sensor %s with state %s",
                        self.unique_id,
                        self.state,
                    )
                self.async_write_ha_state()

        self._async_remove_dispatcher = dispatcher.async_dispatcher_connect(
            self.hass,
            self._new_measure_signal_name,
            on_new_measure,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        if self._async_remove_dispatcher:
            self._async_remove_dispatcher()

    @property
    def measure_id(self) -> str:
        """Return the measure_id handled by this entity."""
        return self.entity_description.key

    @property
    def should_poll(self) -> bool:
        """Return False since updates are pushed from this sensor."""
        return False

    @property
    def unique_id(self) -> str | None:
        """Return the unique id."""
        if self._unique_id is None:
            if self._meter_info.meter_id:
                self._unique_id = (
                    f"{self._meter_info.manufacturer}-{self._meter_info.meter_id}-"
                    f"{self.measure_id}"
                )
            else:
                manufacturer = {
                    self._meter_info.manufacturer_id
                    if self._meter_info.manufacturer_id
                    else self._meter_info.manufacturer
                }
                self._unique_id = (
                    f"CEID-{self._config_entry_id}-"
                    f"{manufacturer}{self._meter_info.type_id}"
                    f"-{self.measure_id}"
                )
        return self._unique_id

    @property
    def native_value(self) -> None | str | int | float:
        """Return the native value of the entity."""
        measure = self._measure_data.get(self.measure_id)

        if measure is None:
            return None

        if isinstance(measure, str):
            return measure

        if isinstance(measure, dt.datetime):
            return measure.isoformat()

        if self.entity_description.scale is not None:
            measure = measure * self.entity_description.scale

        if self.entity_description.use_configured_scaling:
            measure = measure * self._scale_factor

        if self.entity_description.decimals is not None:
            measure = (
                round(measure)
                if self.entity_description.decimals == 0
                else round(measure, self.entity_description.decimals)
            )

        return measure

    @property
    def device_info(self) -> DeviceInfo:
        """Return device specific attributes."""
        manufacturer = (
            self._meter_info.manufacturer
            if self._meter_info.manufacturer
            else self._meter_info.manufacturer_id
        )

        meter_type = (
            self._meter_info.type if self._meter_info.type else self._meter_info.type_id
        )

        return DeviceInfo(
            name=f"{manufacturer} {meter_type}",
            identifiers={(DOMAIN, self._config_entry_id)},
            manufacturer=manufacturer,
            model=meter_type,
            sw_version=self._meter_info.list_version_id,
        )


class AmsHanHourlyEntity(AmsHanEntity, restore_state.RestoreEntity):
    """Representation of a AmsHan sensor each hour."""

    def __init__(
        self,
        entity_description: AmsHanSensorEntityDescription,
        measure_data: dict[str, str | int | float | dt.datetime],
        new_measure_signal_name: str,
        scale_factor: float,
        meter_info: MeterInfo,
        config_entry_id: str,
    ) -> None:
        """Initialize AmsHanHourlyEntity class."""
        super().__init__(
            entity_description,
            measure_data,
            new_measure_signal_name,
            scale_factor,
            meter_info,
            config_entry_id,
        )
        self._restored_last_state: State | None = None

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        self._restored_last_state = await self.async_get_last_state()
        if (
            self._restored_last_state
            and self._restored_last_state.state == ha_const.STATE_UNKNOWN
        ):
            _LOGGER.debug(
                "Restored state from %s for sensor %s is unknown. No need to keep.",
                self._restored_last_state.last_updated,
                self.unique_id,
            )
            self._restored_last_state = None

        await super().async_added_to_hass()

    @property
    def native_value(self) -> None | str | int | float:
        """Return native value from current measure or cache if current hour."""
        measured_value = super().native_value
        if measured_value is not None:
            self._restored_last_state = None
            return measured_value

        if self._restored_last_state:
            if self._is_restored_state_from_current_hour():
                _LOGGER.debug(
                    "Use restored state from %s for sensor %s",
                    self._restored_last_state.last_updated,
                    self.unique_id,
                )
                return self._restored_last_state.state

            _LOGGER.debug(
                "Restored state from %s for sensor %s is too old to be used",
                self._restored_last_state.last_updated,
                self.unique_id,
            )

            self._restored_last_state = None

        return None

    def _is_restored_state_from_current_hour(self) -> bool:
        if not self._restored_last_state:
            return False
        now = dt_util.utcnow()
        time_since_update = now - self._restored_last_state.last_updated
        return (
            now.hour == self._restored_last_state.last_updated.hour
            and time_since_update < dt.timedelta(hours=1)
        )


class MeterMeasureProcessor:
    """Process meter measures from queue and setup/update entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: AmsHanConfigEntry,
        async_add_entities: AddEntitiesCallback,
        measure_queue: asyncio.Queue[han_type.MeterMessageBase],
    ) -> None:
        """Initialize MeterMeasureProcessor class."""
        self._hass = hass
        self._async_add_entities = async_add_entities
        self._measure_queue = measure_queue
        self._decoder: autodecoder.AutoDecoder = autodecoder.AutoDecoder()
        self._known_measures: set[str] = set()
        self._new_measure_signal_name: str | None = None
        self._scale_factor = float(
            config_entry.options.get(CONF_OPTIONS_SCALE_FACTOR, 1)
        )
        self._config_entry_id: str = config_entry.entry_id
        self._meter_info: MeterInfo | None = None

    async def async_process_measures_loop(self) -> None:
        """Start processing loop. Exits on StopMessage from queue."""
        _LOGGER.debug("Processing loop starting.")
        while True:
            try:
                message = await self._async_decode_next_valid_message()
                if not message:
                    _LOGGER.debug("Received stop signal. Exit processing.")
                    return

                _LOGGER.debug("Received meter measures: %s", message)
                self._update_entities(message)
            except asyncio.CancelledError:
                _LOGGER.debug("Processing loop cancelled.")
                return
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Error processing meter readings")

    async def _async_decode_next_valid_message(
        self,
    ) -> dict[str, str | int | float | dt.datetime]:
        while True:
            message = await self._measure_queue.get()
            if isinstance(message, StopMessage):
                return {}

            try:
                decoded_measure = self._decoder.decode_message(message)
                if decoded_measure:
                    _LOGGER.debug("Decoded meter message: %s", decoded_measure)
                    return decoded_measure

                partial_decoded = self._decode_partial_dlms_message(message)
                if partial_decoded:
                    _LOGGER.debug(
                        "Recovered partial meter message with keys: %s",
                        sorted(partial_decoded.keys()),
                    )
                    return partial_decoded

                raw_hex = message.as_bytes.hex() if message.as_bytes else ""
                _LOGGER.warning(
                    "Could not decode meter message (length %d bytes): %s",
                    len(message.as_bytes) if message.as_bytes else 0,
                    raw_hex,
                )
            except Exception:  # pylint: disable=broad-except
                partial_decoded = self._decode_partial_dlms_message(message)
                if partial_decoded:
                    _LOGGER.debug(
                        "Recovered partial meter message after decoder exception with keys: %s",
                        sorted(partial_decoded.keys()),
                    )
                    return partial_decoded

                raw_hex = message.as_bytes.hex() if message.as_bytes else ""
                _LOGGER.exception(
                    "Exception when decoding meter message (length %d bytes): %s",
                    len(message.as_bytes) if message.as_bytes else 0,
                    raw_hex,
                )

    def _decode_partial_dlms_message(
        self, message: han_type.MeterMessageBase
    ) -> dict[str, str | int | float | dt.datetime] | None:
        """Recover key OBIS values from truncated DLMS payload.

        Some MQTT bridges publish payload fragments that lose HDLC framing
        and parts of the DLMS preamble, but still contain OBIS/value tuples.
        Parse those tuples directly as a best-effort fallback.
        """
        if not isinstance(message, han_type.DlmsMessage) or not message.as_bytes:
            return None

        payload = message.as_bytes
        if payload.endswith(b"\x7e"):
            payload = payload[:-1]

        parsed: dict[str, str | int | float | dt.datetime] = {}
        idx = 0
        while True:
            idx = payload.find(b"\x09\x06\x01\x01", idx)
            if idx < 0 or (idx + 8) > len(payload):
                break

            obis_code = payload[idx + 4 : idx + 8]
            value_start = idx + 8
            if value_start >= len(payload):
                break

            tag = payload[value_start]
            value: str | int | None = None
            next_idx = value_start + 1

            if tag == 0x06 and (value_start + 5) <= len(payload):
                value = int.from_bytes(
                    payload[value_start + 1 : value_start + 5], "big", signed=False
                )
                next_idx = value_start + 5
            elif tag == 0x12 and (value_start + 3) <= len(payload):
                value = int.from_bytes(
                    payload[value_start + 1 : value_start + 3], "big", signed=False
                )
                next_idx = value_start + 3
            elif tag == 0x0A and (value_start + 2) <= len(payload):
                text_len = payload[value_start + 1]
                text_end = value_start + 2 + text_len
                if text_end <= len(payload):
                    value = payload[value_start + 2 : text_end].decode(
                        "ascii", errors="ignore"
                    )
                    next_idx = text_end

            key = PARTIAL_DLMS_OBIS_MAP.get(obis_code)
            if key and value is not None:
                parsed[key] = value

            idx = next_idx

        # Require at least active power import as minimum meaningful reading.
        if obis_map.FIELD_ACTIVE_POWER_IMPORT not in parsed:
            return None

        # Fill in meter ID from a previous successful decode if the fragment
        # was cut before the meter serial OBIS entry.
        if obis_map.FIELD_METER_ID not in parsed:
            if self._meter_info and self._meter_info.meter_id:
                parsed[obis_map.FIELD_METER_ID] = self._meter_info.meter_id
            else:
                return None

        # Fill in manufacturer/type from fragment regex, then fall back to meter_info.
        if obis_map.FIELD_METER_MANUFACTURER not in parsed:
            model_match = re.search(rb"([A-Za-z0-9]+)_([A-Za-z0-9]+)", payload)
            if model_match:
                parsed[obis_map.FIELD_METER_MANUFACTURER] = model_match.group(
                    1
                ).decode("ascii", errors="ignore")
                parsed[obis_map.FIELD_METER_TYPE] = model_match.group(2).decode(
                    "ascii", errors="ignore"
                )
            elif self._meter_info:
                if self._meter_info.manufacturer:
                    parsed[obis_map.FIELD_METER_MANUFACTURER] = (
                        self._meter_info.manufacturer
                    )
                if self._meter_info.type:
                    parsed[obis_map.FIELD_METER_TYPE] = self._meter_info.type

        if self._meter_info is None and (
            obis_map.FIELD_METER_MANUFACTURER not in parsed
            or obis_map.FIELD_METER_TYPE not in parsed
        ):
            return None

        return parsed

    def _update_entities(
        self, measure_data: dict[str, str | int | float | dt.datetime]
    ) -> None:
        self._ensure_entities_are_created(measure_data)

        if self._known_measures:
            if self._new_measure_signal_name is None:
                _LOGGER.debug("New measure signal name is not set. Unexpected")
            else:
                dispatcher.async_dispatcher_send(
                    self._hass, self._new_measure_signal_name, measure_data
                )

    def _ensure_entities_are_created(
        self, measure_data: dict[str, str | int | float | dt.datetime]
    ) -> None:
        if obis_map.FIELD_VOLTAGE_L1 in measure_data:
            missing_measures = measure_data.keys() - self._known_measures

            if missing_measures:
                hour_sensors = {
                    s.key for s in SENSOR_TYPES.values() if s.is_hour_sensor
                }
                missing_hour_sensors = hour_sensors - self._known_measures
                if missing_hour_sensors:
                    missing_measures.update(missing_hour_sensors)

                new_enitities = self._create_entities(
                    missing_measures,
                    str(measure_data.get(obis_map.FIELD_METER_ID)),
                    measure_data,
                )
                if new_enitities:
                    self._add_entities(new_enitities)

    def _add_entities(self, entities: list[AmsHanEntity]) -> None:
        new_measures = [x.measure_id for x in entities]
        self._known_measures.update(new_measures)
        _LOGGER.debug(
            "Register new entities for measures: %s",
            new_measures,
        )
        self._async_add_entities(list(entities), update_before_add=True)

    def _create_entities(
        self,
        new_measures: Iterable[str],
        meter_id: str,
        measure_data: dict[str, str | int | float | dt.datetime],
    ) -> list[AmsHanEntity]:
        new_enitities: list[AmsHanEntity] = []
        for measure_id in new_measures:
            if AmsHanEntity.is_measure_id_supported(measure_id):
                if not self._new_measure_signal_name:
                    self._new_measure_signal_name = (
                        f"{DOMAIN}_measure_available_meterid_{meter_id}"
                    )
                if not self._meter_info:
                    self._meter_info = MeterInfo.from_measure_data(measure_data)

                entity_description = SENSOR_TYPES[measure_id]
                new_entity = (
                    AmsHanHourlyEntity(
                        entity_description,
                        measure_data,
                        self._new_measure_signal_name,
                        self._scale_factor,
                        self._meter_info,
                        self._config_entry_id,
                    )
                    if entity_description.is_hour_sensor
                    else AmsHanEntity(
                        entity_description,
                        measure_data,
                        self._new_measure_signal_name,
                        self._scale_factor,
                        self._meter_info,
                        self._config_entry_id,
                    )
                )
                new_enitities.append(cast(AmsHanEntity, new_entity))
            else:
                _LOGGER.debug("Ignore unhandled measure_id %s", measure_id)
        return new_enitities
