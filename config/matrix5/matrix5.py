#!/usr/bin/env python3
"""Matrix5: Stromkosten-/Waschzeitpunkt-Anzeige fuer den Waschkeller (HUB75-Panel).

Ersetzt die fruehere TOTP/2FA-Anzeige, die vollstaendig auf Kiosk2FA umgezogen ist
(siehe IceMatrix/docs/kiosk2fa-epaper.md) - dort ist kein 4-Slot-Limit mehr noetig.

Zeile 1: aktuelle Uhrzeit + aktueller Strompreis (ct/kWh)
Zeile 2: naechste guenstigere Zeit + der dann gueltige Preis - oder "JETZT", falls
         der aktuelle Moment bereits die guenstigste Preisperiode des Tages ist.

Home Assistant publiziert die Preisdaten per MQTT (Tibber via HACS tibber_prices),
dieses Skript haelt nur die aktuelle Uhrzeit selbst nach (sekundengenau), damit die
Anzeige nicht auf den 1-Minuten-Takt der HA-Automation warten muss.
"""
import json
import threading
import time

from PIL import Image, ImageDraw, ImageFont
import paho.mqtt.client as mqtt
from rgbmatrix import RGBMatrix, RGBMatrixOptions

MQTT_HOST = "<broker-ip>"
MQTT_PORT = 1883
MQTT_USER = "<mqtt-user>"
MQTT_PASS = "<mqtt-pass>"
TOPIC_PRICE = "cmnd/Matrix5/strompreis"  # HA -> Pi, JSON: price_now, time_cheap, price_cheap

PANEL_ROWS = 32
PANEL_COLS = 64

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
FONT_SIZE = 10

# Farbe je Preis-Einstufung (guenstig/normal/teuer, von HA per "level_now"/
# "level_cheap" mitgeliefert - gleiche Schwellwerte wie sensor.tibber_preis_status:
# <70% vom Tagesdurchschnitt = guenstig, >130% = teuer, sonst normal).
LEVEL_COLORS = {
    "guenstig": (0, 200, 0),
    "normal": (200, 160, 0),
    "teuer": (220, 0, 0),
}
COLOR_UNKNOWN = (120, 120, 120)

lock = threading.Lock()
price_data = {
    "price_now": None, "level_now": None,
    "time_cheap": "--:--", "price_cheap": None, "level_cheap": None,
}


def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe(TOPIC_PRICE)
    print(f"MQTT verbunden, abonniere {TOPIC_PRICE}")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"Ungueltiges Payload ignoriert: {msg.payload!r}")
        return

    with lock:
        price_data.update(data)
    print(f"Preisdaten aktualisiert: {data}")


def setup_matrix():
    options = RGBMatrixOptions()
    options.rows = PANEL_ROWS
    options.cols = PANEL_COLS
    options.chain_length = 1
    options.parallel = 1
    options.hardware_mapping = "regular"
    options.gpio_slowdown = 2
    options.disable_hardware_pulsing = True  # snd_bcm2835-Konflikt, siehe matrix5-totp.md
    options.brightness = 60
    return RGBMatrix(options=options)


def render(matrix, font, data):
    img = Image.new("RGB", (PANEL_COLS, PANEL_ROWS), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    now_str = time.strftime("%H:%M")
    price_now = data.get("price_now")
    price_now_str = f"{price_now:.1f}" if price_now is not None else "--.-"
    time_cheap = data.get("time_cheap") or "--:--"
    price_cheap = data.get("price_cheap")
    price_cheap_str = f"{price_cheap:.1f}" if price_cheap is not None else "--.-"

    color_now = LEVEL_COLORS.get(data.get("level_now"), COLOR_UNKNOWN)
    color_cheap = LEVEL_COLORS.get(data.get("level_cheap"), COLOR_UNKNOWN)

    draw.text((1, 1), f"{now_str} {price_now_str}", font=font, fill=color_now)
    draw.text((1, 17), f"{time_cheap} {price_cheap_str}", font=font, fill=color_cheap)

    matrix.SetImage(img)


def main():
    matrix = setup_matrix()

    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except OSError:
        font = ImageFont.load_default()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    try:
        while True:
            with lock:
                data = dict(price_data)
            render(matrix, font, data)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        matrix.Clear()
        client.loop_stop()


if __name__ == "__main__":
    main()
