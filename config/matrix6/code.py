"""Matrix6 (Vorlage fuer weitere baugleiche Displays): Stromkosten-/Waschzeitpunkt-Anzeige
auf ESP32-S3 + CircuitPython + HUB75-Panel.

Gleiche Funktion wie Matrix5 (siehe docs/matrix5-strompreis.md), aber auf einem eigenen
ESP32-S3 statt einem Raspberry Pi - siehe docs/matrix6-strompreis.md fuer die komplette
Schritt-fuer-Schritt-Bauanleitung (auch als Vorlage fuer MatrixN mit identischer Hardware).

Zeile 1: aktuelle Uhrzeit + aktueller Strompreis (ct/kWh)
Zeile 2: naechste guenstigere Zeit (+ "^" falls erst morgen) + der dann gueltige Preis

Nur die 6 Werte in der KONFIGURATION unten muessen pro Geraet angepasst werden, der Rest
ist unveraendert wiederverwendbar.
"""
import time
import json
import wifi
import ipaddress
import socketpool
import rtc
import board
import digitalio
import microcontroller
import watchdog
import rgbmatrix
import framebufferio
import displayio
import terminalio
from adafruit_display_text import label
import adafruit_ntp
import adafruit_minimqtt.adafruit_minimqtt as MQTT

# ==================== KONFIGURATION - pro Geraet anpassen ====================
WIFI_SSID = "<wifi-ssid>"
WIFI_PASSWORD = "<wifi-passwort>"
STATIC_IP = "<statische-ip>"        # pro Geraet eine eigene freie IP im selben Subnetz
GATEWAY = "<gateway-ip>"
SUBNET = "255.255.255.0"
DNS = "<dns-ip>"

MQTT_BROKER = "<mqtt-broker-ip>"
MQTT_PORT = 1883
MQTT_USER = "<mqtt-user>"
MQTT_PASSWORD = "<mqtt-passwort>"
MQTT_TOPIC = "cmnd/Matrix6/strompreis"  # pro Geraet: cmnd/Matrix7/strompreis, ...

# VLAN mit IoT-Isolation blockt ggf. externes NTP -> lokalen Router/Gateway
# als NTP-Server verwenden, nicht pool.ntp.org.
NTP_SERVER = "<ntp-server-ip>"
# ===============================================================================

PANEL_WIDTH = 64
PANEL_HEIGHT = 32

# ---- FM6126A Unlock-Sequenz (nur noetig bei FM6126A-Shift-Treiber-Panels -
# ohne diese Sequenz bleibt das Panel dauerhaft schwarz) ----
C12 = [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
C13 = [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0]


def fm6126a_reset():
    pins = {}
    for name, io in (
        ("r1", board.IO4), ("g1", board.IO5), ("b1", board.IO6),
        ("r2", board.IO7), ("g2", board.IO15), ("b2", board.IO16),
        ("clk", board.IO13), ("lat", board.IO11), ("oe", board.IO12),
    ):
        p = digitalio.DigitalInOut(io)
        p.direction = digitalio.Direction.OUTPUT
        pins[name] = p

    data_pins = [pins["r1"], pins["g1"], pins["b1"], pins["r2"], pins["g2"], pins["b2"]]
    clk, lat, oe = pins["clk"], pins["lat"], pins["oe"]

    oe.value = True
    lat.value = False
    clk.value = False

    for reg, threshold in ((C12, PANEL_WIDTH - 12), (C13, PANEL_WIDTH - 13)):
        for l in range(PANEL_WIDTH):
            y = l % 16
            val = 1 if reg[y] == 1 else 0
            for dp in data_pins:
                dp.value = bool(val)
            lat.value = l > threshold
            clk.value = True
            clk.value = False
        lat.value = False
        clk.value = False

    for p in pins.values():
        p.deinit()


print("FM6126A-Unlock...")
fm6126a_reset()
time.sleep(0.05)

# ---- Display-Init. WICHTIG: release_displays() IMMER vor rgbmatrix.RGBMatrix(),
# sonst "ValueError: IOx in use" bei jedem Soft-Reload (Pins werden bei einem
# Absturz/Ctrl-C nicht automatisch freigegeben). ----
displayio.release_displays()

matrix = rgbmatrix.RGBMatrix(
    width=PANEL_WIDTH,
    height=PANEL_HEIGHT,
    bit_depth=4,
    rgb_pins=[board.IO4, board.IO5, board.IO6, board.IO7, board.IO15, board.IO16],
    addr_pins=[board.IO17, board.IO18, board.IO8, board.IO9],  # 1/16-Scan, kein E-Pin
    clock_pin=board.IO13,
    latch_pin=board.IO11,
    output_enable_pin=board.IO12,
)
display = framebufferio.FramebufferDisplay(matrix, auto_refresh=True)

# ---- Farben je Preis-Einstufung (gleiche Schwellwerte wie Matrix5/sensor.tibber_preis_status) ----
WHITE = 0xFFFFFF
GREEN = 0x00DC00
YELLOW = 0xE6BE00
RED = 0xE60000


def color_for(level):
    if level == "guenstig":
        return GREEN
    if level == "teuer":
        return RED
    return YELLOW


# ---- Anzeige-Layout: 2 Zeilen a 16px, links Uhrzeit/Zeit+Marker (weiss),
# rechts farbcodierter Preis ----
group = displayio.Group()

time_label = label.Label(terminalio.FONT, text="--:--", color=WHITE, x=0, y=6)
price_now_label = label.Label(terminalio.FONT, text="--.-", color=YELLOW, x=38, y=6)
time_cheap_label = label.Label(terminalio.FONT, text="--:-- ", color=WHITE, x=0, y=22)
price_cheap_label = label.Label(terminalio.FONT, text="--.-", color=YELLOW, x=38, y=22)

group.append(time_label)
group.append(price_now_label)
group.append(time_cheap_label)
group.append(price_cheap_label)
display.root_group = group


# ---- WiFi (mit Reconnect-Funktion fuer Dauerbetrieb) ----
def wifi_connect():
    print("WiFi verbinde mit", WIFI_SSID, "...")
    wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)
    print("WiFi verbunden, IP (DHCP):", wifi.radio.ipv4_address)
    try:
        wifi.radio.set_ipv4_address(
            ipv4=ipaddress.IPv4Address(STATIC_IP),
            netmask=ipaddress.IPv4Address(SUBNET),
            gateway=ipaddress.IPv4Address(GATEWAY),
            ipv4_dns=ipaddress.IPv4Address(DNS),
        )
        print("Statische IP gesetzt:", wifi.radio.ipv4_address)
    except Exception as e:
        print("Statische IP fehlgeschlagen, bleibe bei DHCP:", e)


wifi_connect()
pool = socketpool.SocketPool(wifi.radio)


# ---- NTP + EU-DST-Berechnung. adafruit_ntp kennt keine Zeitzonen/Sommerzeit,
# daher hier per last-Sunday-of-March/October-Regel selbst berechnet
# (Howard-Hinnant civil_from_days-Algorithmus fuer die Wochentagsberechnung). ----
def days_from_civil(y, m, d):
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def last_sunday(year, month):
    for day in range(31, 24, -1):
        days = days_from_civil(year, month, day)
        wd = (days + 3) % 7  # 0=Montag ... 6=Sonntag
        if wd == 6:
            return day
    return 25


def eu_is_dst(year, month, day, hour):
    if month < 3 or month > 10:
        return False
    if 3 < month < 10:
        return True
    if month == 3:
        ls = last_sunday(year, 3)
        return (day > ls) or (day == ls and hour >= 1)
    ls = last_sunday(year, 10)
    return (day < ls) or (day == ls and hour < 1)


ntp = adafruit_ntp.NTP(pool, server=NTP_SERVER, tz_offset=0)


def sync_time():
    try:
        utc_now = ntp.datetime
        dst = eu_is_dst(utc_now.tm_year, utc_now.tm_mon, utc_now.tm_mday, utc_now.tm_hour)
        ntp._tz_offset = (2 if dst else 1) * 3600
        ntp.next_sync = 0
        local_now = ntp.datetime
        rtc.RTC().datetime = local_now
        print("NTP sync ok, lokale Zeit:", local_now, "DST:", dst)
    except Exception as e:
        print("NTP sync fehlgeschlagen:", e)


sync_time()

# ---- MQTT ----
state = {
    "price_now": 0.0,
    "level_now": "normal",
    "time_cheap": "--:--",
    "cheap_tomorrow": False,
    "price_cheap": 0.0,
    "level_cheap": "normal",
}


def update_display():
    price_now_label.text = "{:.1f}".format(state["price_now"])
    price_now_label.color = color_for(state["level_now"])
    marker = "^" if state["cheap_tomorrow"] else " "
    time_cheap_label.text = state["time_cheap"] + marker
    price_cheap_label.text = "{:.1f}".format(state["price_cheap"])
    price_cheap_label.color = color_for(state["level_cheap"])


def on_message(client, topic, message):
    print("MQTT <", topic, message)
    try:
        data = json.loads(message)
    except ValueError as e:
        print("JSON-Parse-Fehler:", e)
        return
    state["price_now"] = float(data.get("price_now", state["price_now"]))
    state["level_now"] = data.get("level_now", state["level_now"])
    state["time_cheap"] = data.get("time_cheap", state["time_cheap"])
    state["cheap_tomorrow"] = bool(data.get("cheap_tomorrow", state["cheap_tomorrow"]))
    state["price_cheap"] = float(data.get("price_cheap", state["price_cheap"]))
    state["level_cheap"] = data.get("level_cheap", state["level_cheap"])
    update_display()


mqtt_client = MQTT.MQTT(
    broker=MQTT_BROKER,
    port=MQTT_PORT,
    username=MQTT_USER,
    password=MQTT_PASSWORD,
    socket_pool=pool,
)
mqtt_client.on_message = on_message

print("MQTT verbinde...")
mqtt_client.connect()
mqtt_client.subscribe(MQTT_TOPIC)
print("MQTT verbunden + abonniert:", MQTT_TOPIC)

# ---- Watchdog fuer unbeaufsichtigten Dauerbetrieb: haengt die Hauptschleife
# (Netzwerk-Hang, unerwartete Exception o.ae.) laenger als 60s fest, setzt der
# Watchdog den ESP32-S3 hart zurueck -> code.py startet automatisch neu. ----
microcontroller.watchdog.timeout = 60
microcontroller.watchdog.mode = watchdog.WatchDogMode.RESET

# ---- Hauptschleife ----
last_clock_update = 0
last_ntp_sync = time.monotonic()
last_wifi_check = time.monotonic()

while True:
    microcontroller.watchdog.feed()

    now = time.monotonic()

    # WiFi-Verbindung ueberwachen, bei Ausfall neu verbinden (max. alle 10s pruefen)
    if now - last_wifi_check >= 10:
        last_wifi_check = now
        if not wifi.radio.connected:
            print("WiFi getrennt, verbinde neu...")
            try:
                wifi_connect()
                microcontroller.watchdog.feed()
                mqtt_client.connect()
                mqtt_client.subscribe(MQTT_TOPIC)
                print("MQTT nach WiFi-Reconnect wieder verbunden")
            except Exception as e:
                print("WiFi/MQTT-Reconnect fehlgeschlagen:", e)

    try:
        mqtt_client.loop(timeout=1)
    except Exception as e:
        print("MQTT-Loop-Fehler, versuche Reconnect:", e)
        try:
            mqtt_client.reconnect()
            mqtt_client.subscribe(MQTT_TOPIC)
        except Exception as e2:
            print("Reconnect fehlgeschlagen:", e2)
            time.sleep(5)

    now = time.monotonic()
    if now - last_clock_update >= 1:
        last_clock_update = now
        t = time.localtime()
        time_label.text = "{:02d}:{:02d}".format(t.tm_hour, t.tm_min)

    if now - last_ntp_sync >= 3600:
        last_ntp_sync = now
        sync_time()
