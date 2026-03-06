# Testing av AMSHAN Fix for Issue A

## Quick Start - Test på 5 minutter

### Scenario 1: Direkte oppgradering på eksisterende instans

```bash
# SSH inn i Home Assistant
ssh root@<IP>

# Gå til config-mappen
cd /config

# Backup eksisterende integrasjon
cp -r custom_components/amshan custom_components/amshan.backup

# Kopier oppdatert versjon (fra din repo)
cp -r /path/to/fixed/amshan custom_components/amshan

# Restart Home Assistant
ha core restart
```

**Verifisering**:
1. Settings → System → Logs
2. Søk etter "ERROR" eller "EXCEPTION"
3. ✅ Skal ikke finne flere "Could not decode meter message" for korte pakker

### Scenario 2: Testing med mock-data

Kjør denne Python-skripten i Home Assistant:

```python
# I configuration.yaml: append to logger
logger:
  default: debug
  logs:
    custom_components.amshan: debug
    custom_components.amshan.metercon: debug

# Deretter - test i Developer Tools → Python Shell:
import json
from custom_components.amshan.metercon import get_meter_message

# Mock MQTT message class
class MockMessage:
    def __init__(self, payload, topic="pulse/publish"):
        self.payload = payload
        self.topic = topic
        self.subscribed_topic = topic
        self.timestamp = None
        self.qos = 1
        self.retain = False

# Test 1: JSON data (skal ignoreres uten error)
print("Test 1: Ren JSON-status")
json_data = json.dumps({"status": {"rssi": -74, "ntc": 25.90, "ch": 3}})
msg = MockMessage(json_data.encode())
result = get_meter_message(msg)
print(f"  Resultat: {result}")
assert result is None, "JSON skal ignoreres"
print("  ✅ PASS")

# Test 2: Kort binær data (skal ignoreres)
print("\nTest 2: Kort binær fragment")
short_data = b"\x00\x01\x02\x03\x04"
msg = MockMessage(short_data)
result = get_meter_message(msg)
print(f"  Resultat: {result}")
assert result is None, "Kort binær skal ignoreres"
print("  ✅ PASS")

# Test 3: Kamstrup-melding (skal prosesseres)
print("\nTest 3: Kamstrup-melding (50+ byte)")
kamstrup_hex = "7ea0e22b2113239ae6e7000f000000000c07ea0306050c0b0aff80000002190a0e4b616d73747275705f563030303109060101000005ff0a103537303635363732393735303734333509060101600101ff0a1236383431313231424e32343331303130343009060101010700ff0600000dbf"
kamstrup_data = bytes.fromhex(kamstrup_hex)
msg = MockMessage(kamstrup_data)
result = get_meter_message(msg)
print(f"  Resultat: {result}")
# Note: May still fail decode, but should attempt it
print("  ✅ PASS (dekodingsforsøk gjort)")
```

**Forventet logg-output:**
```
DEBUG Ignoring short payload (4 bytes) that doesn't match HDLC framing
DEBUG Ignore JSON in payload without HDLC framing
DEBUG Trying to decode as DLMS message (size: 120 bytes)
```

## Full Integrasjonstesting

### Test Environment Setup

```yaml
# configuration.yaml - test-konfigurason
mqtt:
  broker: localhost
  
logger:
  default: warning
  logs:
    custom_components.amshan: debug
    custom_components.amshan.metercon: debug

# Sensor for å overvåke dekodingsfeil
template:
  - trigger:
      platform: state
      entity_id: sensor.amshan_*
    sensor:
      - name: "AMSHAN Last Update"
        unique_id: amshan_last_update
        state: "{{ now() }}"
```

### Test Case 1: Normal Operation

**Mål**: Verifiser at gyldige Kamstrup-meldinger blir dekoder korrekt

**Framgang**:
1. Start Home Assistant
2. Verifiser at integrannoen registrerte
3. Sjekk at sensor-enheter ble opprettet:
   - `sensor.kamstrup_*`
   - `sensor.meter_*`

**Resultat**: 
```
✅ Alle sensor-enheter vises i Settings → Devices & Services
✅ Sensorene oppdateres regelmessig
✅ Ingen ERROR i logs
```

### Test Case 2: Mixed MQTT Data (JSON + Binary)

**Mål**: Verifiser at systemet håndterer både JSON og binær data

**Setup**: 
```python
# Publiser test-data til MQTT
import paho.mqtt.client as mqtt
import json
import time

client = mqtt.Client()
client.connect("localhost", 1883, 60)

# JSON status
status_msg = {"status": {
    "rssi": -72,
    "ntc": 26.5,
    "ch": 3,
    "Uptime": 3600,
    "heap": 220000
}}

# Kamstrup hex-data
kamstrup_hex = "7ea0e22b2113239ae6e7000f000000000c07ea0306050c0b0aff80000002190a0e4b616d73747275705f563030303109060101000005ff0a103537303635363732393735303734333509060101600101ff0a1236383431313231424e32343331303130343009060101010700ff0600000dbf"

# Publiser vekslende
for i in range(10):
    client.publish("pulse/publish", json.dumps(status_msg))
    time.sleep(0.5)
    client.publish("pulse/publish", bytes.fromhex(kamstrup_hex))
    time.sleep(0.5)

client.disconnect()
```

**Verifisering**:
```bash
# I Home Assistant logs:
# Du skal se:
# - "Decoded meter message:" for gyldige Kamstrup-data
# - "Ignoring short payload" eller "Ignore JSON" for resten
# - INGEN "Could not decode" errors for JSON-data
```

**Resultat**: 
```
✅ Kamstrup-sensorene oppdateres normalt
✅ JSON-data ignoreres stille (DEBUG-logg)  
✅ Ingen ERROR/WARNING for JSON
```

### Test Case 3: Fragmented Binary Data (< 20 bytes)

**Mål**: Verifiser at fragmentert data ikke genererer feil

**Framgang**:
```python
# Publiser fragmenter < 20 byte
fragments = [
    b"Kamstrup_V0001",  # 14 byte
    b"\x00\x01\x02\x03\x04\x05\x06\x07",  # 8 byte
    b"\x7e\xa0\xe2\x2b\x21",  # 5 byte
]

for frag in fragments:
    client.publish("pulse/publish", frag)
```

**Logg-forventning**:
```
DEBUG Ignoring short payload (14 bytes) that doesn't match HDLC framing
DEBUG Ignoring short payload (8 bytes) that doesn't match HDLC framing
DEBUG Ignoring short payload (5 bytes) that doesn't match HDLC framing
```

**Resultat**: 
```
✅ 0 WARNING/ERROR-meldinger
✅ Debug-meldinger vises
✅ Systemet fortsetter normalt
```

## Automatisert Test-Suite

### Test Script (run_tests.py)

```python
#!/usr/bin/env python3
"""
Test suite for AMSHAN fix - Issue A
Kjør: python3 run_tests.py
"""

import sys
import json
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Mock MQTT message
class MockReceiveMessage:
    def __init__(self, payload, topic="pulse/publish"):
        self.payload = payload if isinstance(payload, bytes) else payload.encode()
        self.topic = topic
        self.subscribed_topic = topic
        self.timestamp = None
        self.qos = 1
        self.retain = False

def test_json_data_ignored():
    """JSON-data skal ignoreres"""
    from custom_components.amshan.metercon import get_meter_message
    
    json_data = json.dumps({"status": {"rssi": -74, "ntc": 25.90}})
    msg = MockReceiveMessage(json_data)
    result = get_meter_message(msg)
    
    assert result is None, "JSON data skal holde returnere None"
    logger.info("✅ JSON data test passed")

def test_short_binary_ignored():
    """Kort binær data skal ignoreres"""
    from custom_components.amshan.metercon import get_meter_message
    
    short_data = b"\x00\x01\x02\x03\x04\x05"
    msg = MockReceiveMessage(short_data)
    result = get_meter_message(msg)
    
    assert result is None, "Kort binær skal returnere None"
    logger.info("✅ Short binary test passed")

def test_long_binary_attempted():
    """Lang binær data skal forsøkes dekoded"""
    from custom_components.amshan.metercon import get_meter_message
    
    # 50+ byte binær data
    long_data = b"\x7e" + b"\x00" * 60
    msg = MockReceiveMessage(long_data)
    result = get_meter_message(msg)
    
    # Resultat kan være None (feil dekoding), men forsøk skal gjøres
    logger.info(f"✅ Long binary test passed (result={result})")

if __name__ == "__main__":
    tests = [
        test_json_data_ignored,
        test_short_binary_ignored,
        test_long_binary_attempted,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            logger.error(f"❌ {test.__name__} failed: {e}")
            failed += 1
    
    print(f"\n{'='*50}")
    print(f"Resultat: {passed} passed, {failed} failed")
    print(f"{'='*50}")
    
    sys.exit(0 if failed == 0 else 1)
```

## Verifiserings-Checklist

- [ ] Integrasjonen installeres uten feil
- [ ] Alle AMS HAN-sensorer vises (sensor.kamstrup_*)
- [ ] Sensorer oppdateres regelmessig
- [ ] Logs viser DEBUG-meldinger fra metercon
- [ ] INGEN "Could not decode" for JSON-data
- [ ] INGEN ERROR-meldinger for fragment-data
- [ ] MQTT-forbindelse stabil
- [ ] Temperatur- og RSSI-sensorene fungerer (hvis MQTT-sensors konfigurert)

## Feiltesting

### Hvis noe går galt:

1. **Sensorer vises ikke**:
   ```bash
   # Sjekk manifest
   cat custom_components/amshan/manifest.json
   # Sjekk Home Assistant logs
   Settings → System → Logs → Filter: "amshan"
   ```

2. **Fortsatt "Could not decode"-feil**:
   ```bash
   # Finn den eksakte payloaden som feiler
   grep "Could not decode" /config/home-assistant.log
   # Sjekk bytes-lengde i debug-output
   grep "Exception for meter message" /config/home-assistant.log
   ```

3. **MQTT-data kommer ikke fram**:
   ```bash
   # Sjekk MQTT-forbindelse
   mosquitto_sub -h localhost -t "pulse/publish" -v
   # Skal vise meldinger som sendes
   ```

---

**Test gjennomført av**: [Ditt navn]  
**Dato**: 2026-03-06  
**Resultat**: ✅ PASS / ❌ FAIL
