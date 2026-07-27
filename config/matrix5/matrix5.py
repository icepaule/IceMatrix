#!/usr/bin/env python3
import time
import json
import threading

from PIL import Image, ImageDraw, ImageFont
import pyotp
import paho.mqtt.client as mqtt
from rgbmatrix import RGBMatrix, RGBMatrixOptions

import secrets_matrix5 as secrets

MQTT_HOST = "<broker-ip>"
MQTT_PORT = 1883
MQTT_USER = "<mqtt-user>"
MQTT_PASS = "<mqtt-pass>"
TOPIC_SHOW = "cmnd/Matrix5/show"   # Payload: JSON-Liste der anzuzeigenden Account-Namen, Reihenfolge = Anzeigereihenfolge
TOPIC_STATE = "stat/Matrix5/shown"

PANEL_ROWS = 32
PANEL_COLS = 64
# Anzahl physisch geketteter 64x32-Panels (horizontal). Bei Erweiterung einfach hochzaehlen.
CHAIN_LENGTH = 1

# Ziffern als handgezeichnete 4x5-Pixel-Bitmap (20 LEDs/Ziffer, kein Font/Antialiasing) -
# Vorgabe war 3x4 (12 LEDs) oder ersatzweise 4x5 (20 LEDs); 4x5 gewaehlt, weil 0/6/8/9 bei
# nur 4 Zeilen Hoehe zu leicht verwechselbar waeren - Fehllesen eines TOTP-Codes ist kein
# akzeptables Risiko. 1px Luecke zwischen Ziffern (Pitch 5px).
DIGIT_W, DIGIT_H = 4, 5
DIGIT_PITCH = DIGIT_W + 1

CELL_WIDTH = 32
CELL_HEIGHT = 16  # eng an den gemessenen Font-/Bitmap-Massen ausgerichtet, keine verschenkten Zeilen
COLS = (PANEL_COLS * CHAIN_LENGTH) // CELL_WIDTH
ROWS = PANEL_ROWS // CELL_HEIGHT
NUM_CELLS = COLS * ROWS

RED_THRESHOLD_SECONDS = 5
MAX_POOL = 20  # Sanity-Limit fuer die per MQTT waehlbare Account-Menge

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
# Deutlich groesser als die vorherigen 6-7px-Versuche - bei so kleiner Schrift verschwimmen
# Buchstaben unvermeidlich (anders als bei Ziffern gibt es keine einfache Pixel-Bitmap fuer
# beliebigen Text).
NAME_FONT_SIZE = 10
NAME_MAX_CHARS = (CELL_WIDTH - 2) // 6
NAME_Y_OFFSET = -2  # Font-bbox beginnt bei y~2-3, oben buendig an die Zelle ausrichten (keine Leerzeile)

# Bitmap-Ziffern 0-9, je 5 Zeilen a 4 Zeichen ('#'=an, '.'=aus)
DIGIT_BITMAPS = {
    "0": [".##.", "#..#", "#..#", "#..#", ".##."],
    "1": ["..#.", ".##.", "..#.", "..#.", ".###"],
    "2": [".##.", "#..#", "..#.", ".#..", "####"],
    "3": ["###.", "..#.", ".##.", "...#", "###."],
    "4": ["#.#.", "#.#.", "####", "..#.", "..#."],
    "5": ["####", "#...", "###.", "...#", "###."],
    "6": [".##.", "#...", "###.", "#..#", ".##."],
    "7": ["####", "..#.", ".#..", ".#..", ".#.."],
    "8": [".##.", "#..#", ".##.", "#..#", ".##."],
    "9": [".##.", "#..#", ".###", "...#", ".##."],
}


def draw_bitmap_digit(draw, x0, y0, digit, color):
    for row, line in enumerate(DIGIT_BITMAPS[digit]):
        for col, cell in enumerate(line):
            if cell == "#":
                draw.point((x0 + col, y0 + row), fill=color)


lock = threading.Lock()
shown_accounts = list(secrets.ACCOUNTS.keys())[:MAX_POOL]


def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe(TOPIC_SHOW)
    print(f"MQTT verbunden, abonniere {TOPIC_SHOW}")


def on_message(client, userdata, msg):
    global shown_accounts
    try:
        names = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"Ungueltiges Payload ignoriert: {msg.payload!r}")
        return

    valid = [n for n in names if n in secrets.ACCOUNTS]
    dropped = [n for n in names if n not in secrets.ACCOUNTS]
    if dropped:
        print(f"Unbekannte Accounts ignoriert: {dropped}")
    if len(valid) > MAX_POOL:
        print(f"Sanity-Limit {MAX_POOL} erreicht, Rest abgeschnitten: {valid[MAX_POOL:]}")
        valid = valid[:MAX_POOL]
    if len(valid) > NUM_CELLS:
        print(f"Nur Platz fuer {NUM_CELLS} Zellen (keine Rotation mehr), nicht angezeigt: {valid[NUM_CELLS:]}")

    with lock:
        shown_accounts = valid
    client.publish(TOPIC_STATE, json.dumps(valid), retain=True)
    print(f"Anzeige aktualisiert: {valid}")


def setup_matrix():
    options = RGBMatrixOptions()
    options.rows = PANEL_ROWS
    options.cols = PANEL_COLS
    options.chain_length = CHAIN_LENGTH
    options.parallel = 1
    options.hardware_mapping = "regular"
    options.gpio_slowdown = 2
    options.disable_hardware_pulsing = True  # snd_bcm2835-Konflikt, siehe Diagnose-Test 27.07.
    options.brightness = 60
    return RGBMatrix(options=options)


def render(matrix, pool, name_font):
    img = Image.new("RGB", (PANEL_COLS * CHAIN_LENGTH, PANEL_ROWS), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    now = int(time.time())
    for cell, name in enumerate(pool[:NUM_CELLS]):
        col, row = cell % COLS, cell // COLS
        x0, y0 = col * CELL_WIDTH, row * CELL_HEIGHT
        totp = pyotp.TOTP(secrets.ACCOUNTS[name])
        code = totp.now()
        remaining = totp.interval - (now % totp.interval)
        status_color = (0, 200, 0) if remaining > RED_THRESHOLD_SECONDS else (220, 0, 0)

        draw.text((x0 + 1, y0 + NAME_Y_OFFSET), name[:NAME_MAX_CHARS], fill=(140, 140, 140), font=name_font)
        code_y = y0 + 11
        for i, digit in enumerate(code):
            draw_bitmap_digit(draw, x0 + i * DIGIT_PITCH, code_y, digit, status_color)

    matrix.SetImage(img)


def main():
    matrix = setup_matrix()

    try:
        name_font = ImageFont.truetype(FONT_PATH, NAME_FONT_SIZE)
    except OSError:
        name_font = ImageFont.load_default()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    try:
        while True:
            with lock:
                names = list(shown_accounts)
            render(matrix, names, name_font)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        matrix.Clear()
        client.loop_stop()


if __name__ == "__main__":
    main()
