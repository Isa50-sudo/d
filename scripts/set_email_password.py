"""Speichert das E-Mail-Passwort (bzw. App-Passwort) sicher im Schlüsselbund des Betriebssystems.

Windows: Anmeldeinformationsverwaltung · macOS: Schlüsselbund · Linux: Secret Service
Das Passwort wird NICHT in .env oder anderen Dateien gespeichert.

Aufruf:  python scripts/set_email_password.py            (setzen/ändern)
         python scripts/set_email_password.py --delete   (entfernen)
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

KEYRING_SERVICE = "jarvis-email"


def main() -> int:
    try:
        import keyring
    except ImportError:
        print("Das Paket 'keyring' fehlt. Bitte zuerst install.bat / install.sh ausführen.")
        return 1

    from jarvis.config.env import get_env

    address = get_env().email_address
    if not address:
        print("EMAIL_ADDRESS ist in der .env nicht gesetzt. Bitte zuerst eintragen.")
        return 1

    if "--delete" in sys.argv:
        try:
            keyring.delete_password(KEYRING_SERVICE, address)
            print(f"Passwort für {address} entfernt.")
        except keyring.errors.PasswordDeleteError:
            print("Es war kein Passwort gespeichert.")
        return 0

    print(f"E-Mail-Konto: {address}")
    print("Tipp: Bei Gmail/Outlook mit Zwei-Faktor-Anmeldung ein App-Passwort erstellen und hier eingeben.")
    password = getpass.getpass("Passwort / App-Passwort (Eingabe unsichtbar): ")
    if not password:
        print("Abgebrochen.")
        return 1
    try:
        keyring.set_password(KEYRING_SERVICE, address, password)
    except Exception as exc:  # noqa: BLE001
        print(f"Konnte nicht im Schlüsselbund speichern: {exc}")
        print("Unter Linux wird ein Secret-Service (z. B. GNOME Keyring / KWallet) benötigt.")
        return 1
    print("Gespeichert. Bitte JARVIS neu starten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
