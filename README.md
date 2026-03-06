<img src="https://github.com/home-assistant/brands/blob/master/custom_integrations/amshan/icon.png" width="128" alt="logo">

[![GitHub Release](https://img.shields.io/github/release/toreamun/amshan-homeassistant?style=for-the-badge)](https://github.com/toreamun/amshan-homeassistant/releases)
[![License](https://img.shields.io/github/license/toreamun/amshan-homeassistant?style=for-the-badge)](LICENSE)

[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)
![Project Maintenance](https://img.shields.io/badge/maintainer-Tore%20Amundsen%20%40toreamun-blue.svg?style=for-the-badge)
[![buy me a coffee](https://img.shields.io/badge/If%20you%20like%20it-Buy%20me%20a%20coffee-orange.svg?style=for-the-badge)](https://www.buymeacoffee.com/toreamun)

[English](README.en.md)

# AMS HAN Home Assistant integrasjon

Home Assistant integrasjon for norske og svenske strømmålere. Både DLMS og P1 fortmater støttes. Integrasjonen skal i prinsippet fungere med alle typer leserer som videresender datastrømmen fra måleren direkte ([serieport/TCP-IP](https://github.com/toreamun/amshan-homeassistant/wiki/Lesere-serieport-og-nettverk)) eller oppdelt som [meldinger til MQTT](https://github.com/toreamun/amshan-homeassistant/wiki/Lesere-MQTT). Noen aktuelle lesere er:
| Leser | stream/MQTT | DLMS/P1 |Land|
| ------------------------------------------------------------------------------------------------- | ----------- | ---------- |--|
| [Tibber Pulse](https://github.com/toreamun/amshan-homeassistant/wiki/Lesere-MQTT#tibber-pulse) | MQTT | DLMS og P1 | NO, SE|
| [energyintelligence.se P1 elmätaravläsare](https://github.com/toreamun/amshan-homeassistant/wiki/Lesere-MQTT#energyintelligencese-p1-elm%C3%A4taravl%C3%A4sare) | MQTT | P1 | SE |
| [AmsToMqttBridge og amsleser.no](https://github.com/toreamun/amshan-homeassistant/wiki/Lesere-MQTT#amstomqttbridge-og-amsleserno) [ver 2.1](https://github.com/gskjold/AmsToMqttBridge/milestone/22) | MQTT | DLMS | NO, SE? |
| [M-BUS slave](https://github.com/toreamun/amshan-homeassistant/wiki/Lesere-serieport-og-nettverk#m-bus-enhet) | stream | DLMS | NO, SE |
| [Oss brikken](https://github.com/toreamun/amshan-homeassistant/wiki/Lesere-serieport-og-nettverk#oss-brikken) | stream | DLMS | NO |


