"""Prüft die JARVIS-Installation und erklärt fehlende Schritte verständlich.

Aufruf:  python scripts/check_setup.py [--quick]
Gibt Exit-Code 0 zurück, wenn JARVIS starten kann (fehlender API-Key ist nur eine Warnung).
"""
from __future__ import annotations

import importlib
import shutil
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OK, WARN, FAIL = "  [ OK ]", "  [WARN]", "  [FAIL]"
REQUIRED = ["fastapi", "uvicorn", "google.genai", "pydantic", "pydantic_settings", "psutil", "httpx", "defusedxml", "send2trash", "keyring"]


def read_env() -> dict[str, str]:
    env_file = ROOT / ".env"
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    quick = "--quick" in sys.argv
    errors = 0
    print("\nJARVIS – Setup-Prüfung\n" + "-" * 40)

    if sys.version_info < (3, 10):
        print(f"{FAIL} Python {sys.version.split()[0]} – benötigt wird Python 3.10 oder neuer.")
        return 1
    print(f"{OK} Python {sys.version.split()[0]}")

    missing = []
    for module in REQUIRED:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    if missing:
        print(f"{FAIL} Fehlende Pakete: {', '.join(missing)} – bitte install.bat / install.sh ausführen.")
        errors += 1
    else:
        print(f"{OK} Alle Python-Pakete installiert")

    for folder in ("data", "logs", "config"):
        (ROOT / folder).mkdir(exist_ok=True)
    print(f"{OK} Ordner data/, logs/, config/ vorhanden")

    env_file = ROOT / ".env"
    if not env_file.exists():
        example = ROOT / ".env.example"
        if example.exists():
            shutil.copy(example, env_file)
            print(f"{WARN} .env wurde aus .env.example erstellt.")
        else:
            print(f"{FAIL} .env.example fehlt.")
            errors += 1
    env = read_env()
    key = env.get("GEMINI_API_KEY", "")
    if not key:
        print(f"{WARN} GEMINI_API_KEY ist nicht gesetzt.")
        print("         -> Key erstellen: https://aistudio.google.com/apikey")
        print(f"         -> In {env_file} eintragen:  GEMINI_API_KEY=dein-schlüssel")
        print("         JARVIS startet trotzdem, Sprache/KI sind aber erst danach verfügbar.")
    else:
        print(f"{OK} GEMINI_API_KEY ist gesetzt (Wert wird nicht angezeigt)")

    host = env.get("JARVIS_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"{WARN} JARVIS_HOST={host} – das Backend wäre im Netzwerk erreichbar. Empfohlen: 127.0.0.1")

    port = int(env.get("JARVIS_PORT", "8765") or 8765)
    with socket.socket() as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            print(f"{WARN} Port {port} ist bereits belegt – läuft JARVIS schon? (JARVIS_PORT in .env ändern)")
        else:
            print(f"{OK} Port {port} ist frei")

    if not quick:
        try:
            socket.getaddrinfo("generativelanguage.googleapis.com", 443)
            print(f"{OK} Internet / Gemini-Endpunkt erreichbar (DNS)")
        except OSError:
            print(f"{WARN} Gemini-Endpunkt nicht auflösbar – keine Internetverbindung?")

    print("-" * 40)
    if errors:
        print("Bitte die oben genannten Probleme beheben.\n")
        return 1
    print("Bereit.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
