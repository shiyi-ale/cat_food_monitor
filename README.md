# Cat Food Monitor

ESP32 cat food weight monitor with MQTT, Flask, SQLite, and a browser dashboard.

## Structure

- `hardware/`: PlatformIO firmware for NodeMCU-32S.
- `software/`: Flask service, SQLite storage, MQTT subscriber, and web dashboard.
- `docs/`: Reserved for wiring notes, screenshots, or reports.

## MQTT Topics

- `pet/weight/data`: ESP32 publishes weight data.
- `pet/device/status`: ESP32 publishes device status.
- `pet/device/control`: PC service publishes remote control commands.

## Run PC Service

```powershell
cd software
python -m pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Build Firmware

Open `hardware/` with PlatformIO, then build and upload to NodeMCU-32S.

Before uploading, check:

- WiFi name and password in `hardware/src/main.cpp`.
- MQTT broker IP in `hardware/src/main.cpp`.
- Calibration values `zeroAdc` and `calibrationK`.

