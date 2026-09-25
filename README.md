# J.A.R.V.I.S — persönlicher KI-Assistent (lokal, sprachgesteuert, Gemini)

JARVIS ist eine lokale Webanwendung: ein **Python/FastAPI-Backend** läuft auf deinem Computer, die **futuristische HUD-Oberfläche** öffnest du im Browser. Du sprichst mit JARVIS, er antwortet mit einer **tiefen, ruhigen, männlichen Stimme** (Gemini Live API, native Audio), ruft bei Bedarf **aktuelle Informationen** ab und führt über **kontrollierte, berechtigte Tools** Aktionen auf deinem Computer aus.

```
 Mikrofon ─► Browser (AudioWorklet, 16 kHz PCM) ─► WebSocket ─► Backend ─► Gemini Live API
                                                                   │            │
 Lautsprecher ◄─ Browser (24 kHz PCM) ◄─ WebSocket ◄───────────────┘◄───────────┘
                                                                   │
                                           Tool-Call ─► ToolManager ─► PermissionManager
                                                              │          └► ConfirmationManager (Dialog)
                                                              └► Tools: System · Dateien · Apps · Browser
                                                                        Web · Memory · Erinnerungen · World · E-Mail
```

---

## Inhalt

1. [Schnellstart](#schnellstart)
2. [Bedienung](#bedienung)
3. [Seiten](#seiten)
4. [Sicherheitsmodell](#sicherheitsmodell)
5. [Tools](#tools)
6. [Stimme & Sprache](#stimme--sprache)
7. [Wake Word](#wake-word)
8. [Aktuelle Informationen](#aktuelle-informationen)
9. [Memory](#memory)
10. [E-Mail-Modul](#e-mail-modul)
11. [Architektur & Projektstruktur](#architektur--projektstruktur)
12. [Erweitern: eigenes Tool](#erweitern-eigenes-tool)
13. [Tests](#tests)
14. [Fehlerbehebung](#fehlerbehebung)
15. [Bekannte Grenzen](#bekannte-grenzen)

---

## Schnellstart

**Voraussetzungen:** Python 3.10+, ein aktueller Chromium-Browser (Chrome oder Edge – für das Wake Word empfohlen), Mikrofon, Internet, ein Gemini API Key.

### Windows
```bat
install.bat        :: legt .venv an, installiert Pakete, erstellt .env
notepad .env       :: GEMINI_API_KEY=... eintragen
start.bat          :: startet JARVIS und öffnet den Browser
```

### macOS / Linux
```bash
./install.sh
nano .env          # GEMINI_API_KEY=... eintragen
./start.sh
```

Danach öffnet sich **http://127.0.0.1:8765**. Die Startsequenz führt echte Prüfungen durch (System, Mikrofon, Gemini, Stimme, Tools, Netzwerk, Memory, E-Mail). Ein Klick auf **JARVIS AKTIVIEREN** gibt Mikrofon und Audioausgabe frei (Browser-Richtlinie).

### Gemini API Key
1. Key erstellen: <https://aistudio.google.com/apikey>
2. In die Datei `.env` im Projektordner eintragen: `GEMINI_API_KEY=dein-schlüssel`
3. JARVIS neu starten.

Der Key wird **nur** vom lokalen Backend gelesen. Er wird nie an den Browser ausgeliefert, nie geloggt (Redaction-Filter) und `.env` ist in `.gitignore`.

Alle Einstellungen der `.env` sind in [`.env.example`](.env.example) dokumentiert.

---

## Bedienung

| Aktion | So geht's |
|---|---|
| Sprechen (Wake-Word-Modus) | „**Jarvis**, wie ist das Wetter heute?“ – oder auf den Kern tippen / Leertaste |
| Sprechen (Offen) | einfach sprechen; JARVIS ignoriert Gespräche, die nicht ihm gelten |
| Push-to-talk | Leertaste oder Kern gedrückt halten |
| Unterbrechen | einfach dazwischenreden – JARVIS verstummt sofort (Barge-in) |
| Mikrofon stumm | Taste **M** oder „MIC AUS“ |
| Textchat (Fallback) | Taste **T** oder „TEXT“ |

Nach einer Antwort bleibt ein **Follow-up-Fenster** (Standard 12 s) offen – Rückfragen ohne erneutes „Jarvis“.

**Beispiele**
- „Jarvis, was ist heute in der Welt passiert?“ → aktuelle Quellen (Google-Suche / RSS), Quelle + Zeitpunkt werden genannt und angezeigt
- „Jarvis, öffne Google.“ → `EXECUTING` → `COMPLETED` → „Google ist geöffnet.“
- „Jarvis, öffne Spotify.“ → Programm wird gestartet
- „Jarvis, zeig mir Washington.“ → WORLD öffnet sich, der Globus fliegt nach Washington
- „Jarvis, wie ist meine CPU-Auslastung?“ → echte Messwerte
- „Jarvis, erinnere mich in 20 Minuten an den Tee.“
- „Jarvis, merk dir, dass ich Espresso trinke.“
- „Jarvis, lösch die Datei alt.txt auf dem Desktop.“ → `WAITING FOR CONFIRMATION` – erst dein Klick führt aus

**Zustände des Kerns:** `READY` · `STANDBY` (wartet auf Wake Word) · `LISTENING` · `THINKING` · `EXECUTING` · `WAITING FOR CONFIRMATION` · `SPEAKING` · `COMPLETED` · `ERROR` · `OFFLINE`

---

## Seiten

| Seite | Inhalt |
|---|---|
| **JARVIS** | Kern-Visualisierung (audio-reaktiv), Status, Untertitel, Aktivitäts-Feed, Quellen, News, Systemwerte, Erinnerungen, Chat |
| **WORLD** | Interaktiver 3D-Globus (globe.gl/WebGL, lokal gebündelt), Ortssuche, Markierungen – von JARVIS steuerbar |
| **SYSTEM** | CPU (pro Kern), RAM, GPU/VRAM, Laufwerke, Netzwerk, Temperaturen, Prozesse, Dienste – live alle 2 s |
| **MEMORY** | Langzeit-Erinnerungen ansehen, suchen, hinzufügen, löschen; Kurzzeit-Kontext ansehen/löschen |
| **EMAIL** | Posteingang, Lesen, Suchen, Entwürfe, Senden (immer mit Bestätigung) |
| **LOGS** | Ereignisprotokoll live, filterbar |
| **SETTINGS** | Modell, Stimme, Sprache, Wake Word, Mikrofon, Berechtigungen (pro Tool), Benachrichtigungen, Memory, Web/News-Quellen, Logging, Theme |

---

## Sicherheitsmodell

JARVIS hat **keinen** uneingeschränkten Zugriff auf den Computer.

1. **Nur definierte Tools.** Gemini kann ausschließlich strukturierte Tool-Calls *anfordern*. Es gibt kein Tool für beliebigen Code oder Shell-Befehle (per Test abgesichert). Programme werden als Argumentliste ohne Shell gestartet, Namen streng validiert.
2. **Validierung.** Jeder Parameter wird per Pydantic-Schema geprüft, bevor irgendetwas passiert.
3. **Berechtigungen.** Jedes Tool hat eine Risikostufe (`read` / `write` / `critical`):

   | Zugriffsstufe | lesen | ändern | kritisch |
   |---|---|---|---|
   | `READ_ONLY` | erlaubt | verboten | verboten |
   | `LIMITED` (Standard) | erlaubt | erlaubt¹ | Bestätigung |
   | `FULL_ACCESS` | erlaubt | erlaubt | **Bestätigung** |

   ¹ Dateien anlegen/ändern erfordert in `LIMITED` eine Bestätigung. Pro Tool kann in den Settings *erlauben / Bestätigung / verbieten* gesetzt werden. **Nicht überschreibbar:** kritische Tools (löschen, E-Mail senden, installieren, Programme beenden) brauchen **immer** eine Bestätigung; `READ_ONLY` lässt sich nicht per Tool-Regel aufweichen.
4. **Bestätigung nur per Klick.** Der Dialog kann ausschließlich durch einen echten Klick auf „AUSFÜHREN“ bestätigt werden – nicht per Sprache, nicht durch das Modell, nicht durch einen anderen Browser-Tab. Der Fokus liegt auf „Abbrechen“, Enter bestätigt nicht, nach Timeout gilt „abgelehnt“.
5. **Datei-Sandbox.** Datei-Tools arbeiten nur in `ALLOWED_PATHS` (Standard: Benutzerordner). Symlink-Ausbrüche und sensible Orte (`.ssh`, `.env`, Schlüsselbunde, Browserprofile …) sind gesperrt. Löschen = Papierkorb, Bearbeiten legt `.bak` an, Anlegen überschreibt nie.
6. **Nur lokal erreichbar.** Das Backend bindet an `127.0.0.1`. Host- und Origin-Prüfung blockieren DNS-Rebinding sowie Zugriffe fremder Webseiten auf REST und WebSocket.
7. **Web-Zugriffe mit SSRF-Schutz.** Web-Tools dürfen keine lokalen/privaten Adressen abrufen (auch nicht über Redirects). RSS wird mit `defusedxml` geparst. Web- und E-Mail-Inhalte werden dem Modell ausdrücklich als *Fremdinhalt* markiert (Prompt-Injection-Schutz).
8. **Datenschutz im Log.** Tool-Parameter und Gesprächsinhalte werden standardmäßig **nicht** protokolliert (in den Settings einschaltbar). Secrets werden aus allen Logs entfernt.

---

## Tools

| Kategorie | Tools |
|---|---|
| System (lesen) | `get_system_info`, `get_cpu_usage`, `get_memory_usage`, `get_gpu_usage`, `get_disk_usage`, `get_network_status`, `get_temperatures`, `list_processes`, `get_current_time` |
| Dateien | `search_files`, `list_directory`, `read_file`, `create_file`, `edit_file`, `delete_file` (kritisch) |
| Programme | `open_application`, `close_application` (kritisch), `install_application` (kritisch, winget/Homebrew), `list_known_applications` |
| Browser | `open_url`, `open_web_search` |
| Web-Informationen | `get_weather` (Open-Meteo), `get_news` (RSS), `wikipedia_lookup`, `read_webpage` + Gemini **Google Search Grounding** |
| Memory | `remember`, `recall_memories`, `forget_memory` |
| Erinnerungen | `create_reminder`, `list_reminders`, `delete_reminder` |
| World / UI | `show_location_on_globe`, `clear_globe_markers`, `navigate_ui` |
| E-Mail | `email_list_recent`, `email_search`, `email_read`, `email_create_draft`, `email_send` (kritisch) |

Programme, die JARVIS namentlich kennt, stehen in [`config/apps.json`](config/apps.json). Eigene Einträge in `config/apps.local.json` (gleiches Format) ergänzen.

---

## Stimme & Sprache

JARVIS nutzt die **native Audioausgabe der Gemini Live API** (Modell `gemini-3.8-live`): Zuhören, Denken und Sprechen in einem Modell – minimale Latenz, natürliche Stimme, unterbrechbar.

| Variable | Bedeutung |
|---|---|
| `VOICE_NAME` | Prebuilt-Voice. Empfohlen (tief, ruhig, männlich): **Charon** (Standard), Orus, Iapetus, Algenib, Schedar, Umbriel |
| `VOICE_LANGUAGE` | Primärsprache, Standard `de-DE` |
| `VOICE_SPEED` | `slow` · `calm` · `normal` · `fast` |
| `VOICE_STYLE` | Freitext zur Sprechweise |

Alles ist zur Laufzeit in SETTINGS änderbar. Tempo und Stil werden über die Systemanweisung gesteuert (die Live API hat dafür keine direkten Parameter). **Sprachwechsel:** JARVIS antwortet standardmäßig auf Deutsch; sprichst du Englisch, antwortet er auf Englisch („Sprache fixieren“ schaltet das ab).

**Anderen TTS-Anbieter einbinden:** `backend/jarvis/voice/profile.py` definiert den Vertrag `VoiceBackend`. Ein alternatives Backend (z. B. Live-Transkription → Textmodell → ElevenLabs/Azure/Piper) liefert weiterhin PCM16 an den Browser; das Frontend bleibt unverändert.

---

## Wake Word

- **Aktuelle Engine:** Web Speech API (Chrome/Edge) erkennt „Jarvis“ (inkl. typischer Fehlhörungen). Ein **1,5-s-Pre-Roll-Puffer** sorgt dafür, dass „Jarvis, wie ist das Wetter?“ in einem Atemzug vollständig bei Gemini ankommt.
- **Ohne Web Speech** (z. B. Firefox): Kern antippen oder Leertaste – oder Modus „Offen“.
- **Datenschutz-Hinweis:** Chrome verarbeitet Web-Speech-Audio auf Google-Servern.
- **Austauschbar:** `frontend/js/audio/wakeword.js` definiert den Vertrag `WakeWordEngine` (`start/stop/onWake/feedAudio`). Eine lokale Engine (Picovoice Porcupine WASM, openWakeWord) kann ihn implementieren und erhält die 16-kHz-Frames über `feedAudio`.

---

## Aktuelle Informationen

JARVIS trennt strikt zwischen Modellwissen und aktuellen Daten. Für Aktuelles nutzt er:
- **Google Search Grounding** (in Gemini integriert) – die gefundenen Quellen werden im Panel QUELLEN mit Abrufzeit angezeigt,
- **RSS-Nachrichtenquellen** (Standard: tagesschau, ORF, Deutsche Welle, BBC, Reuters – in SETTINGS editierbar),
- **Open-Meteo** (Wetter, Geocoding), **Wikipedia**, **Webseiten lesen**.

Die Systemanweisung verbietet erfundene Live-Daten und verlangt Quelle + Zeitpunkt.

---

## Memory

| Art | Speicherort | Kontrolle |
|---|---|---|
| Kurzzeit-Kontext | RAM (letzte N Runden) | MEMORY → „Löschen“; wird bei neuer Gemini-Session als Kontext mitgegeben |
| Langzeit-Memory | `data/jarvis.db` (SQLite) | nur auf ausdrücklichen Wunsch („Merk dir …“), ansehen/löschen in MEMORY, optional Bestätigung pro Speicherung |
| Einstellungen | `data/settings.json` | SETTINGS |

Gespräche selbst werden nicht dauerhaft gespeichert (außer „Gespräche protokollieren“ ist aktiv).

---

## E-Mail-Modul

Optional, per IMAP/SMTP (Gmail, Outlook, GMX, web.de, …):

1. In `.env`: `EMAIL_ADDRESS`, `EMAIL_IMAP_HOST`, `EMAIL_IMAP_PORT`, `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`
2. Passwort sicher im **Schlüsselbund des Betriebssystems** speichern (nie im Klartext):
   ```bash
   .venv/bin/python scripts/set_email_password.py        # Windows: .venv\Scripts\python scripts\set_email_password.py
   ```
   Bei 2-Faktor-Anmeldung ein **App-Passwort** verwenden.
3. JARVIS neu starten.

Funktionen: lesen, Absender/Betreff, zusammenfassen (durch Gemini), suchen, „vermutlich wichtig“ (markiert oder Schlüsselwörter wie *dringend, Frist, Rechnung*), Entwürfe, Senden **nur mit Bestätigung**. Neue wichtige E-Mails werden gemeldet.

---

## Architektur & Projektstruktur

```
.
├── backend/
│   ├── jarvis/
│   │   ├── main.py                 FastAPI-App, statisches Frontend, Lifespan
│   │   ├── config/                 env.py (.env/Secrets) · user_settings.py (Laufzeit-Settings)
│   │   ├── core/                   hub (WebSocket-Clients) · eventlog (LOGS/Audit) · security · logging
│   │   ├── ai/                     gemini.py (Client/Health) · live_session.py (Gemini Live) · prompts.py
│   │   ├── voice/                  profile.py (Stimme, VoiceBackend-Vertrag)
│   │   ├── tools/                  base · registry · manager · builtin/* (alle Tools)
│   │   ├── permissions/            manager (Richtlinien) · confirmation (Dialoge)
│   │   ├── memory/                 store.py (SQLite + Kurzzeit-Puffer)
│   │   ├── web/                    http.py (SSRF-sicherer Client) · sources.py (Wetter, News, Wiki)
│   │   ├── system/                 monitor.py (psutil, nvidia-smi)
│   │   ├── services/               container · notifications · email_service
│   │   └── api/                    routes.py (REST) · ws.py (WebSocket-Protokoll)
│   ├── tests/                      pytest (Permissions, Security, Tools, Live-Session)
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── css/                        base (Tokens/Themes) · hud · pages
│   ├── js/
│   │   ├── core/                   bus · store · ws · api · state (Zustandsautomat)
│   │   ├── audio/                  mic · player · wakeword · conversation · worklets/
│   │   ├── components/             core-viz · startup · confirm · toasts · nav
│   │   └── pages/                  jarvis · world · system · memory · email · logs · settings
│   └── assets/                     favicon · vendor/globe (globe.gl, MIT)
├── config/apps.json                bekannte Programme
├── scripts/                        check_setup.py · set_email_password.py
├── data/  logs/                    Laufzeitdaten (nicht im Git)
├── install.bat  start.bat  install.sh  start.sh
└── .env.example
```

**WebSocket-Protokoll** (`/ws`): Binärframes = Audio (rein 16 kHz, raus 24 kHz PCM16 mono). JSON-Events u. a. `transcript`, `model.speaking`, `interrupted`, `turn.complete`, `tool.start/end/denied`, `confirm.request/closed`, `sources`, `search`, `system.stats`, `notification`, `ui.navigate`, `world.focus`, `gemini.status`, `log`, `error`. Details im Kopf von `backend/jarvis/api/ws.py`.

**Gemini-Live-Details:** automatische Sprachaktivitätserkennung mit Barge-in, Ein-/Ausgabe-Transkription, asynchrone Tool-Calls (`NON_BLOCKING`, Antworten `WHEN_IDLE`), Kontextfenster-Kompression (lange Gespräche), Session-Resumption bei `GoAway`/Verbindungsabbruch, automatisches Schließen nach Inaktivität (Kosten).

---

## Erweitern: eigenes Tool

Neue Datei, z. B. `backend/jarvis/tools/builtin/my_tools.py`, und das Modul in `BUILTIN_MODULES` (`tools/registry.py`) eintragen:

```python
from pydantic import BaseModel, Field
from jarvis.tools.base import Risk, ToolContext, ToolError, tool

class VolumeArgs(BaseModel):
    level: int = Field(ge=0, le=100, description="Lautstärke in Prozent")

@tool(
    name="set_volume",
    description="Setzt die Systemlautstärke.",
    args=VolumeArgs,
    risk=Risk.WRITE,                # read | write | critical
    category="system",
    summarize=lambda a: f"Lautstärke auf {a.level} %",
)
async def set_volume(args: VolumeArgs, ctx: ToolContext) -> dict:
    ...                              # echte Implementierung; bei Problemen: raise ToolError("…")
    return {"volume": args.level}
```

Validierung, Berechtigung, Bestätigung, Logging, UI-Events und die Anmeldung bei Gemini übernimmt das Framework automatisch.

---

## Tests

```bash
.venv/bin/python -m pip install pytest pytest-asyncio
cd backend && ../.venv/bin/python -m pytest
```

Getestet werden u. a. das Permission-System (inkl. nicht überschreibbarer Regeln), Datei-Sandbox & Symlink-Ausbruch, SSRF-Schutz, Origin-Prüfung, Secret-Redaction, Bestätigungsablauf, RSS-Parsing/XML-Bomben und ein kompletter Sprach-Turn der Live-Session gegen ein simuliertes Gemini (Transkript → Tool-Call → Tool-Antwort → Audio).

---

## Fehlerbehebung

| Problem | Lösung |
|---|---|
| „Gemini API Key fehlt“ | `GEMINI_API_KEY` in `.env` eintragen, neu starten |
| „Die Verbindung zu Gemini konnte nicht hergestellt werden.“ | Internet prüfen; LOGS-Seite und `logs/jarvis.log` enthalten Details |
| „Das eingestellte Gemini-Modell ist nicht verfügbar.“ | In SETTINGS ein anderes Live-Modell wählen (z. B. `gemini-3.8-live`) |
| „Das Mikrofon ist momentan nicht verfügbar.“ | Browser-Mikrofonfreigabe für 127.0.0.1 erlauben; Gerät in SETTINGS wählen |
| Wake Word reagiert nicht | Chrome/Edge verwenden; sonst Kern antippen / Leertaste / Modus „Offen“ |
| JARVIS hört sich selbst | Kopfhörer nutzen oder Modus „Push-to-talk“ (Echo-Unterdrückung ist aktiv, aber nicht perfekt) |
| Port belegt | `JARVIS_PORT` in `.env` ändern |
| Programm wird nicht gefunden | Eintrag in `config/apps.local.json` ergänzen |

---

## Bekannte Grenzen

- **Wake Word** nutzt derzeit die Web Speech API (Chrome/Edge; Audio wird dafür von Google verarbeitet). Eine lokale Engine lässt sich über `WakeWordEngine` einsetzen.
- **GPU-Werte** aktuell nur für NVIDIA (`nvidia-smi`); andere GPUs werden als „nicht verfügbar“ angezeigt – es werden keine Werte erfunden.
- **Temperaturen** liefert psutil vor allem unter Linux; unter Windows/macOS oft „nicht verfügbar“.
- **Programme installieren** nur mit winget (Windows) bzw. Homebrew (macOS).
- **E-Mail** per IMAP/SMTP mit App-Passwort; OAuth (Gmail/Microsoft Graph) wäre eine mögliche Erweiterung in `services/email_service.py`.
- Die Gemini Live API ist kostenpflichtig nach Nutzung; inaktive Sessions werden automatisch geschlossen (`Session-Timeout` in SETTINGS).
