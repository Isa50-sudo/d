"""Lädt die lokalen Sprachmodelle einmalig herunter (danach läuft JARVIS offline).

  * Piper-Stimmen (Sprachausgabe)       -> data/voices/
  * Whisper-Modell (Spracherkennung)    -> data/models/whisper/
  * Prüft, ob das Ollama-Modell installiert ist (und erklärt 'ollama pull').

Aufruf:  python scripts/download_models.py [--voices-only] [--stt-only]
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.config.env import get_env  # noqa: E402
from jarvis.core.paths import DATA_DIR  # noqa: E402


def download_voices(names: list[str]) -> bool:
    try:
        from piper.download_voices import download_voice
    except ImportError:
        print("  [FAIL] Paket piper-tts fehlt – bitte install.bat / install.sh ausführen.")
        return False
    target = DATA_DIR / "voices"
    target.mkdir(parents=True, exist_ok=True)
    ok = True
    for name in dict.fromkeys(names):
        if (target / f"{name}.onnx").exists() and (target / f"{name}.onnx.json").exists():
            print(f"  [ OK ] Stimme {name} bereits vorhanden")
            continue
        print(f"  [....] Lade Stimme {name} …")
        try:
            download_voice(name, target)
            print(f"  [ OK ] Stimme {name}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [FAIL] Stimme {name}: {exc}")
    return ok


def download_whisper(model: str) -> bool:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  [FAIL] Paket faster-whisper fehlt – bitte install.bat / install.sh ausführen.")
        return False
    target = DATA_DIR / "models" / "whisper"
    target.mkdir(parents=True, exist_ok=True)
    print(f"  [....] Lade Whisper-Modell '{model}' (einmalig, je nach Modell 75 MB – 3 GB) …")
    try:
        WhisperModel(model, device="cpu", compute_type="int8", download_root=str(target))
        print(f"  [ OK ] Whisper-Modell {model}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] Whisper-Modell {model}: {exc}")
        return False


def check_ollama(host: str, model: str) -> None:
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=3) as response:  # noqa: S310 - lokale Adresse
            names = [m.get("name") for m in json.load(response).get("models", [])]
    except Exception:  # noqa: BLE001
        print(f"  [WARN] Ollama ist unter {host} nicht erreichbar.")
        print("         -> Ollama installieren/starten: https://ollama.com/download")
        print(f"         -> danach:  ollama pull {model}")
        return
    if model in names or f"{model}:latest" in names:
        print(f"  [ OK ] Ollama-Modell {model} installiert")
    else:
        print(f"  [WARN] Ollama-Modell {model} fehlt  ->  ollama pull {model}")


def main() -> int:
    env = get_env()
    print("\nJARVIS – lokale Modelle\n" + "-" * 40)
    ok = True
    if "--stt-only" not in sys.argv:
        ok &= download_voices([env.voice_name, env.voice_name_en])
    if "--voices-only" not in sys.argv:
        ok &= download_whisper(env.stt_model)
    check_ollama(env.ollama_host, env.ollama_model)
    print("-" * 40)
    print("Fertig.\n" if ok else "Einige Downloads sind fehlgeschlagen (Internetverbindung?).\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
