#!/usr/bin/env python3
"""Klassische Betriebsdaten-Anzeige auf dem Waveshare 2.13" e-Paper HAT (Rev2.1,
Panel-Generation V4 - per Live-Test auf dem Geraet bestaetigt, V2/V3 zeigten
kein Bild).

Zeigt Hostname, IP(s), CPU-Temp, Load, RAM, Disk, Uptime. Alle STATS_INTERVAL_S
ein Full-Refresh - kein Partial-Refresh, da dessen Sequenz bei der V4-API vom
V2-Treiber abweicht und fuer eine einmal/Minute aktualisierte Anzeige der
Flacker-Nachteil keine Rolle spielt.
"""
import socket
import subprocess
import time
from datetime import timedelta

import psutil
from PIL import Image, ImageDraw, ImageFont

from waveshare_epd import epd2in13_V4

STATS_INTERVAL_S = 60

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_PATH_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def load_fonts():
    try:
        return {
            "title": ImageFont.truetype(FONT_PATH_BOLD, 16),
            "body": ImageFont.truetype(FONT_PATH, 13),
        }
    except OSError:
        default = ImageFont.load_default()
        return {"title": default, "body": default}


def get_ips():
    ips = []
    for iface, addrs in psutil.net_if_addrs().items():
        if iface == "lo":
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET:
                ips.append(f"{iface}: {addr.address}")
    return ips or ["kein Netz"]


def cpu_temp_c():
    try:
        out = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=2)
        # Format: temp=42.8'C
        return float(out.stdout.strip().split("=")[1].split("'")[0])
    except Exception:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read().strip()) / 1000.0
        except Exception:
            return None


def build_image(fonts, width, height):
    img = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(img)

    hostname = socket.gethostname()
    load1, load5, load15 = psutil.getloadavg()
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime_s = time.time() - psutil.boot_time()
    temp = cpu_temp_c()

    y = 2
    draw.text((2, y), hostname, font=fonts["title"], fill=0)
    y += 20
    draw.line((0, y, width, y), fill=0)
    y += 4

    lines = []
    for ip_line in get_ips():
        lines.append(ip_line)
    lines.append(f"CPU: {temp:.1f}C" if temp is not None else "CPU: n/a")
    lines.append(f"Load: {load1:.2f} {load5:.2f} {load15:.2f}")
    lines.append(f"RAM: {mem.percent:.0f}% ({mem.used // (1024*1024)}/{mem.total // (1024*1024)}MB)")
    lines.append(f"Disk: {disk.percent:.0f}% frei {disk.free // (1024*1024*1024)}GB")
    lines.append(f"Up: {str(timedelta(seconds=int(uptime_s)))}")

    for line in lines:
        draw.text((2, y), line, font=fonts["body"], fill=0)
        y += 15

    draw.text((2, height - 14), time.strftime("%Y-%m-%d %H:%M:%S"), font=fonts["body"], fill=0)
    return img


def main():
    fonts = load_fonts()
    epd = epd2in13_V4.EPD()

    epd.init()
    epd.Clear(0xFF)

    try:
        while True:
            # Panel ist nativ 122x250 (Portrait) - fuer Textanzeige als 250x122
            # (Landscape) gerendert; epd.getbuffer() erkennt das Seitenverhaeltnis
            # und rotiert intern selbst, kein manuelles .rotate() noetig (anders
            # als beim V2-Treiber).
            image = build_image(fonts, epd.height, epd.width)
            epd.init()
            epd.display(epd.getbuffer(image))
            time.sleep(STATS_INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        epd.init()
        epd.Clear(0xFF)
        epd.sleep()


if __name__ == "__main__":
    main()
