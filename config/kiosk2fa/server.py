#!/usr/bin/env python3
import time

import pyotp
from flask import Flask, jsonify, send_from_directory

import secrets_matrix5 as secrets

RED_THRESHOLD_SECONDS = 5
BIND_HOST = "127.0.0.1"  # nur lokal fuer den Kiosk-Browser auf diesem Geraet, nie ins Netz
BIND_PORT = 5000

app = Flask(__name__, static_folder="static", static_url_path="")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/codes")
def api_codes():
    now = int(time.time())
    accounts = []
    for name, secret in secrets.ACCOUNTS.items():
        totp = pyotp.TOTP(secret)
        remaining = totp.interval - (now % totp.interval)
        accounts.append({
            "name": name,
            "code": totp.now(),
            "remaining": remaining,
            "interval": totp.interval,
            "warn": remaining <= RED_THRESHOLD_SECONDS,
        })
    accounts.sort(key=lambda a: a["name"].lower())
    return jsonify(accounts)


if __name__ == "__main__":
    app.run(host=BIND_HOST, port=BIND_PORT, threaded=True)
