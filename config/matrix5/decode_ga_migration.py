#!/usr/bin/env python3
"""
Dekodiert einen Google-Authenticator "Konten uebertragen"-Export-Link
(otpauth-migration://offline?data=...) und schreibt die enthaltenen
Accounts in secrets_matrix5.py.

Bewusst ohne externe protobuf-Abhaengigkeit: das Migrations-Format ist ein
einfaches, festes Schema, das hier von Hand als rohes Protobuf-Wire-Format
geparst wird.

Aufruf (direkt auf dem Pi, NICHT die Ausgabe/den Link irgendwo hin kopieren):
    python3 decode_ga_migration.py 'otpauth-migration://offline?data=...'

Gibt NUR Account-Namen aus, nie die Secrets selbst.
"""
import sys
import base64
import urllib.parse
from pathlib import Path

SECRETS_FILE = Path(__file__).parent / "secrets_matrix5.py"


def read_varint(data, pos):
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def parse_protobuf(data):
    """Generischer Parser: liefert dict field_number -> Liste roher Werte
    (bytes fuer wire_type 2, int fuer wire_type 0)."""
    fields = {}
    pos = 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:
            value, pos = read_varint(data, pos)
        elif wire_type == 2:
            length, pos = read_varint(data, pos)
            value = data[pos:pos + length]
            pos += length
        elif wire_type == 5:
            value = data[pos:pos + 4]
            pos += 4
        elif wire_type == 1:
            value = data[pos:pos + 8]
            pos += 8
        else:
            raise ValueError(f"Unbekannter wire_type {wire_type} bei Feld {field_num}")
        fields.setdefault(field_num, []).append(value)
    return fields


def parse_otp_parameters(raw):
    fields = parse_protobuf(raw)
    secret = fields.get(1, [b""])[0]
    name = fields.get(2, [b""])[0].decode("utf-8", errors="replace")
    issuer = fields.get(3, [b""])[0].decode("utf-8", errors="replace")
    return name, issuer, secret


def decode_migration_uri(uri):
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "otpauth-migration":
        raise ValueError("Kein otpauth-migration://-Link")
    qs = urllib.parse.parse_qs(parsed.query)
    data_b64 = qs["data"][0]
    raw = base64.b64decode(data_b64)

    top = parse_protobuf(raw)
    accounts = []
    for otp_raw in top.get(1, []):
        name, issuer, secret = parse_otp_parameters(otp_raw)
        base32_secret = base64.b32encode(secret).decode("ascii")
        if issuer and name and issuer != name:
            display_name = f"{issuer}: {name}"
        else:
            display_name = issuer or name or "Unbenannt"
        accounts.append((display_name, base32_secret))
    return accounts


def merge_into_secrets_file(accounts):
    existing = {}
    if SECRETS_FILE.exists():
        namespace = {}
        exec(SECRETS_FILE.read_text(), namespace)
        existing = namespace.get("ACCOUNTS", {})

    added = []
    for name, secret in accounts:
        key = name
        suffix = 2
        while key in existing and existing[key] != secret:
            key = f"{name}{suffix}"
            suffix += 1
        if key not in existing:
            added.append(key)
        existing[key] = secret

    lines = [
        "# Automatisch aus Google-Authenticator-Export ergaenzt.",
        "# NIEMALS committen (siehe .gitignore) - liegt nur lokal auf dem Pi.",
        "ACCOUNTS = {",
    ]
    for key, secret in existing.items():
        lines.append(f"    {key!r}: {secret!r},")
    lines.append("}")
    SECRETS_FILE.write_text("\n".join(lines) + "\n")
    return added, list(existing.keys())


def main():
    if len(sys.argv) != 2:
        print("Aufruf: python3 decode_ga_migration.py 'otpauth-migration://offline?data=...'")
        sys.exit(1)

    accounts = decode_migration_uri(sys.argv[1])
    added, all_names = merge_into_secrets_file(accounts)

    print(f"{len(accounts)} Account(s) im Export gefunden.")
    print(f"Neu hinzugefuegt: {added}")
    print(f"secrets_matrix5.py enthaelt jetzt insgesamt: {all_names}")
    print("(Secrets selbst wurden nicht ausgegeben.)")


if __name__ == "__main__":
    main()
