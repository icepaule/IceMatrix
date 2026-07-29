#!/bin/bash
# Einmaliges Erstboot-Provisioning fuer kiosk2fa: Pakete installieren, kiosk-User
# anlegen, Services aktivieren. Deaktiviert sich selbst am Ende (Marker-Datei).
#
# Alles wird zusaetzlich in eine eigene Log-Datei geschrieben (nicht nur ins
# journal), weil auf RPi-OS-Lite journald standardmaessig volatile ist und
# Logs sonst den naechsten Reboot nicht ueberleben.
set -uo pipefail
exec > >(tee -a /var/log/kiosk2fa-firstboot.log) 2>&1

MARKER=/var/lib/kiosk2fa-firstboot-done
if [ -f "$MARKER" ]; then
  exit 0
fi

echo "$(date -Is) kiosk2fa: Erstboot-Provisioning startet"

echo "$(date -Is) kiosk2fa: warte auf Netzwerk (DNS)..."
NET_OK=0
for i in $(seq 1 60); do
  if getent hosts deb.debian.org >/dev/null 2>&1; then
    NET_OK=1
    break
  fi
  sleep 5
done
echo "$(date -Is) kiosk2fa: DNS-Check Ergebnis: $NET_OK (nach $((i*5))s)"

# Ohne RTC-Batterie startet der Pi mit falscher/alter Systemzeit. apt-get update
# via HTTPS schlaegt dann an der TLS-Zertifikatspruefung fehl, obwohl Netzwerk
# an sich funktioniert - deshalb explizit auf NTP-Sync warten, nicht nur auf DNS.
echo "$(date -Is) kiosk2fa: warte auf NTP-Sync..."
NTP_OK=0
for i in $(seq 1 60); do
  if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q "^yes$"; then
    NTP_OK=1
    break
  fi
  sleep 5
done
echo "$(date -Is) kiosk2fa: NTP-Sync Ergebnis: $NTP_OK, aktuelle Zeit: $(date -Is)"

# Pi Zero 2W hat nur 512MB RAM. Das Standard-zram-Swap (~50% RAM komprimiert)
# reicht fuer "apt-get install chromium" NICHT - dpkg-Unpack/Postinst laeuft
# dann in Swapping Hell fest (gut dokumentiertes Community-Problem, siehe
# forums.raspberrypi.com "Zero 2W setup, unpacking chromium stuck progress").
# Zusaetzlich ein echtes Swapfile anlegen, bevor Chromium installiert wird.
SWAPFILE=/var/swap-kiosk2fa
if [ ! -f "$SWAPFILE" ]; then
  echo "$(date -Is) kiosk2fa: lege 3G Swapfile an (Chromium-Install braucht mehr als zram allein)..."
  fallocate -l 3G "$SWAPFILE"
  chmod 600 "$SWAPFILE"
  mkswap "$SWAPFILE"
  swapon "$SWAPFILE"
  # persistent, damit Chromium auch im laufenden Kiosk-Betrieb genug Headroom hat
  grep -q "$SWAPFILE" /etc/fstab || echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
fi
free -h

export DEBIAN_FRONTEND=noninteractive

echo "$(date -Is) kiosk2fa: apt-get update..."
if ! apt-get update; then
  echo "$(date -Is) kiosk2fa: apt-get update FEHLGESCHLAGEN - Abbruch, Service versucht es via Restart erneut"
  exit 1
fi

echo "$(date -Is) kiosk2fa: apt-get install..."
if ! apt-get install -y --no-install-recommends \
  cage chromium fonts-dejavu-core \
  python3-flask python3-psutil python3-pil \
  python3-spidev python3-gpiozero python3-rpi.gpio python3-pip; then
  echo "$(date -Is) kiosk2fa: apt-get install FEHLGESCHLAGEN - Abbruch, Service versucht es via Restart erneut"
  exit 1
fi

echo "$(date -Is) kiosk2fa: pip install pyotp..."
if ! pip3 install --break-system-packages pyotp; then
  echo "$(date -Is) kiosk2fa: pip install FEHLGESCHLAGEN - Abbruch, Service versucht es via Restart erneut"
  exit 1
fi

if ! id kiosk >/dev/null 2>&1; then
  echo "$(date -Is) kiosk2fa: lege kiosk-User an..."
  useradd -m -s /bin/bash -G video,input,render kiosk
  passwd -l kiosk
fi

# Chromiums eigene Translate-Bubble ignoriert --disable-translate/--disable-
# features=Translate in dieser Version - nur die Enterprise-Policy zieht
# zuverlaessig (per Live-Test auf dem Geraet verifiziert).
mkdir -p /etc/chromium/policies/managed
cat > /etc/chromium/policies/managed/kiosk2fa.json << 'POLICY_EOF'
{
  "TranslateEnabled": false
}
POLICY_EOF

systemctl daemon-reload
systemctl enable --now epaper-stats.service
systemctl enable kiosk2fa-server.service
systemctl enable kiosk2fa-browser.service

if [ -f /home/mpauli/kiosk2fa/secrets_matrix5.py ]; then
  systemctl start kiosk2fa-server.service
  systemctl start kiosk2fa-browser.service
else
  echo "$(date -Is) kiosk2fa: secrets_matrix5.py fehlt noch - Server/Browser sind enabled," \
       "starten aber erst nach 'cp secrets_matrix5.py.example secrets_matrix5.py'" \
       "(oder Kopie der echten Datei vom Matrix5-Pi) + 'systemctl start kiosk2fa-server kiosk2fa-browser'"
fi

mkdir -p /var/lib
touch "$MARKER"
echo "$(date -Is) kiosk2fa: Erstboot-Provisioning erfolgreich abgeschlossen"
systemctl disable kiosk2fa-firstboot.service
