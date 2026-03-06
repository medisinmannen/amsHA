"""Meter connection module."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from han import common as han_type
from han import dlde, hdlc, meter_connection
from han import serial_connection_factory as han_serial
from han import tcp_connection_factory as han_tcp
from homeassistant.components import mqtt
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .const import (
    CONF_MQTT_TOPICS,
    CONF_SERIAL_BAUDRATE,
    CONF_SERIAL_BYTESIZE,
    CONF_SERIAL_DSRDTR,
    CONF_SERIAL_PARITY,
    CONF_SERIAL_PORT,
    CONF_SERIAL_RTSCTS,
    CONF_SERIAL_STOPBITS,
    CONF_SERIAL_XONXOFF,
    CONF_TCP_HOST,
    CONF_TCP_PORT,
)

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Mapping

_LOGGER: logging.Logger = logging.getLogger(__name__)


def setup_meter_connection(
    loop: asyncio.AbstractEventLoop,
    config: Mapping[str, Any],
    measure_queue: asyncio.Queue[han_type.MeterMessageBase],
) -> meter_connection.ConnectionManager:
    """Initialize ConnectionManager using configured connection type."""
    connection_factory = get_connection_factory(loop, config, measure_queue)
    return meter_connection.ConnectionManager(connection_factory)


def get_connection_factory(
    loop: asyncio.AbstractEventLoop,
    config: Mapping[str, Any],
    measure_queue: asyncio.Queue[han_type.MeterMessageBase],
) -> meter_connection.AsyncConnectionFactory:
    """Get connection factory based on configured connection type."""

    async def tcp_connection_factory() -> meter_connection.MeterTransportProtocol:
        return await han_tcp.create_tcp_message_connection(
            measure_queue,
            loop,
            None,
            host=config[CONF_TCP_HOST],
            port=config[CONF_TCP_PORT],
        )

    async def serial_connection_factory() -> meter_connection.MeterTransportProtocol:
        return await han_serial.create_serial_message_connection(
            measure_queue,
            loop,
            None,
            url=config[CONF_SERIAL_PORT],
            baudrate=config[CONF_SERIAL_BAUDRATE],
            parity=config[CONF_SERIAL_PARITY],
            bytesize=config[CONF_SERIAL_BYTESIZE],
            stopbits=float(config[CONF_SERIAL_STOPBITS]),
            xonxoff=config[CONF_SERIAL_XONXOFF],
            rtscts=config[CONF_SERIAL_RTSCTS],
            dsrdtr=config[CONF_SERIAL_DSRDTR],
        )

    return (
        tcp_connection_factory if CONF_TCP_HOST in config else serial_connection_factory
    )


async def async_setup_meter_mqtt_subscriptions(
    hass: HomeAssistant,
    config: Mapping[str, Any],
    measure_queue: asyncio.Queue[han_type.MeterMessageBase],
) -> CALLBACK_TYPE:
    """Set up MQTT topic subscriptions."""

    @callback
    def message_received(mqtt_message: mqtt.models.ReceiveMessage) -> None:
        """Handle new MQTT messages."""
        _LOGGER.debug(
            (
                "Message with timestamp %s, QOS %d, retain flagg %s, "
                "and payload length %d received "
                "from topic %s from subscription to topic %s"
            ),
            mqtt_message.timestamp,
            mqtt_message.qos,
            bool(mqtt_message.retain),
            len(mqtt_message.payload),
            mqtt_message.topic,
            mqtt_message.subscribed_topic,
        )
        meter_message = get_meter_message(mqtt_message)
        if meter_message:
            measure_queue.put_nowait(meter_message)

    topics = {x.strip() for x in config[CONF_MQTT_TOPICS].split(",")}

    _LOGGER.debug("Try to subscribe to %d MQTT topic(s): %s", len(topics), topics)
    unsubscibers = [
        await mqtt.client.async_subscribe(
            hass, topic, message_received, 1, encoding=None
        )
        for topic in topics
    ]
    _LOGGER.debug(
        "Successfully subscribed to %d MQTT topic(s): %s", len(topics), topics
    )

    @callback
    def unsubscribe_mqtt() -> None:
        _LOGGER.debug("Unsubscribe %d MQTT topic(s): %s", len(unsubscibers), topics)
        for unsubscribe in unsubscibers:
            unsubscribe()

    return unsubscribe_mqtt


def get_meter_message(
    mqtt_message: mqtt.models.ReceiveMessage,
) -> han_type.MeterMessageBase | None:
    """Get frame information part from mqtt message."""
    payload: bytes = mqtt_message.payload  # type: ignore[attr-defined]
    message = _try_read_meter_message(payload)
    if message is not None:
        if message.message_type == han_type.MeterMessageType.P1:
            if message.is_valid:
                _LOGGER.debug(
                    "Got valid P1 message from topic %s: %s",
                    mqtt_message.topic,
                    payload.hex(),
                )
                return message
            _LOGGER.debug(
                "Got invalid P1 message from topic %s: %s",
                mqtt_message.topic,
                payload.hex(),
            )
            return None

        if message.is_valid:
            if message.payload is not None:
                _LOGGER.debug(
                    (
                        "Got valid frame of expected length with correct "
                        "checksum from topic %s: %s"
                    ),
                    mqtt_message.topic,
                    payload.hex(),
                )
                return message
            _LOGGER.debug(
                (
                    "Got empty frame of expected length with correct "
                    "checksum from topic %s: %s"
                ),
                mqtt_message.topic,
                payload.hex(),
            )

        # Invalid HDLC frame (e.g. wrong FCS) but we may still have usable
        # DLMS payload - pass it to the decoder for a decode attempt
        if not message.is_valid and message.payload and len(message.payload) >= 10:
            _LOGGER.debug(
                "Got invalid HDLC frame but trying DLMS decode of payload from topic %s",
                mqtt_message.topic,
            )
            return han_type.DlmsMessage(message.payload)

        _LOGGER.debug(
            "Got invalid frame from topic %s: %s",
            mqtt_message.topic,
            payload.hex(),
        )
        return None

    try:
        json_data = json.loads(mqtt_message.payload)
        if isinstance(json_data, dict):
            _LOGGER.debug(
                "Ignore JSON in payload without HDLC framing from topic %s: %s",
                mqtt_message.topic,
                json_data,
            )
            return None
    except ValueError:
        pass

    _LOGGER.debug(
        "Got payload without HDLC framing from topic %s: %s",
        mqtt_message.topic,
        payload.hex(),
    )

    # Try message containing DLMS (binary) message without HDLC framing.
    # Some bridges encode the binary data as hex string, and this must be decoded.
    # Also try raw binary payload as DLMS when it was not parseable as HDLC.
    if _is_hex_string(payload):
        payload = _hex_payload_to_binary(payload)
    normalized_payload = _normalize_dlms_payload(payload)
    if len(normalized_payload) >= 10:
        return han_type.DlmsMessage(normalized_payload)
    return None


def _try_read_meter_message(payload: bytes) -> han_type.MeterMessageBase | None:
    """Try to parse HDLC-frame from payload."""
    if payload.startswith(b"/"):
        try:
            return dlde.DataReadout(payload)
        except ValueError as ex:
            _LOGGER.debug("Starts with '/', but not a valid P1 message: %s", ex)

    # Clean payload by finding first HDLC flag (0x7e) if not at start.
    # This removes noise/fragments at the beginning of the payload.
    flag_sequence = hdlc.HdlcFrameReader.FLAG_SEQUENCE.to_bytes(1, byteorder="big")
    if not payload.startswith(flag_sequence):
        first_flag_idx = payload.find(flag_sequence[0:1])
        if first_flag_idx > 0:
            _LOGGER.debug(
                "Found HDLC flag at position %d, trimming %d bytes of noise from start",
                first_flag_idx,
                first_flag_idx,
            )
            payload = payload[first_flag_idx:]
        elif first_flag_idx < 0:
            # No HDLC flag found. Some bridges deliver fragments with the leading
            # 0x7E stripped; try to recover a frame by locating frame format (0xA0).
            frame_format_idx = payload.find(b"\xa0")
            if frame_format_idx >= 0 and payload.endswith(flag_sequence):
                candidate = flag_sequence + payload[frame_format_idx:]
                _LOGGER.debug(
                    "No leading HDLC flag found; trying recovery from 0xA0 at index %d",
                    frame_format_idx,
                )
                recovered = _try_read_meter_message(candidate)
                if recovered is not None:
                    return recovered

            # No flag/recoverable frame found in binary payload.
            if _is_hex_string(payload):
                return _try_read_meter_message(_hex_payload_to_binary(payload))
            return None

    frame_reader = hdlc.HdlcFrameReader(
        use_octet_stuffing=False, use_abort_sequence=False
    )

    frames = frame_reader.read(payload)
    if len(frames) == 0:
        frames = frame_reader.read(flag_sequence)

    if len(frames) > 0:
        return frames[0]

    if not _is_hex_string(payload):
        return None

    return _try_read_meter_message(_hex_payload_to_binary(payload))


def _is_hex_string(payload: bytes) -> bool:
    if (len(payload) % 2) == 0:
        try:
            int(payload, 16)
        except ValueError:
            return False
        else:
            return True
    return False


def _hex_payload_to_binary(payload: str | bytes) -> bytes:
    if isinstance(payload, bytes):
        return bytes.fromhex(payload.decode("utf8"))
    if isinstance(payload, str):
        return bytes.fromhex(payload)
    msg = f"Unsupported payload type: {type(payload)}"
    raise ValueError(msg)


def _normalize_dlms_payload(payload: bytes) -> bytes:
    """Normalize unframed DLMS payload from bridges.

    Bridges sometimes deliver raw DLMS bytes with trailing HDLC end delimiter,
    and occasionally include trailing FCS before the delimiter. Strip these
    markers when present so the DLMS decoder sees only DLMS content.
    """
    if not payload:
        return payload

    had_trailing_flag = payload.endswith(b"\x7e")
    if had_trailing_flag:
        payload = payload[:-1]

    # If payload appears to end with 16-bit FCS bytes from HDLC, strip them.
    # This catches common "DLMS-with-HDLC-tail" fragments from MQTT bridges.
    if had_trailing_flag and payload.startswith(b"\x00\x00\x00\x00") and len(payload) >= 12:
        payload = payload[:-2]

    return payload
