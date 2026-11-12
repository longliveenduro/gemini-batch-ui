"""Konstanten der App und die Ordner conf/ und data/. Was mehrere Module brauchen, steht hier.

Die Pfade (BASE, CONF_DIR, DATA_DIR usw.) werden beim ersten Import berechnet: aus GEMINI_UI_HOME, sonst aus dem Ort
dieses Pakets (der Ordner über src/).
"""
import os
import re
import shutil
from pathlib import Path


def _base_dir():
    """Projektordner, in dem conf/ und data/ liegen: GEMINI_UI_HOME, sonst der Ordner über src/.
    Das Paket gbui liegt neben dem Hauptskript. Steht es in keinem Ordner namens src, ist es der Ordner, der das
    Paket enthält (älteres, flaches Layout)."""
    home = os.getenv("GEMINI_UI_HOME")
    if home:
        return Path(home).expanduser().resolve()
    try:
        app_dir = Path(__file__).resolve().parent.parent  # der Ordner mit dem Paket gbui, also der des Hauptskripts
    except NameError:  # ohne __file__ (z. B. interaktiv oder per exec)
        return Path.cwd()
    return app_dir.parent if app_dir.name == "src" else app_dir


BASE = _base_dir()
CONF_DIR = BASE / "conf"  # Konfiguration und Zugangsdaten (geheim)
DATA_DIR = BASE / "data"  # erzeugte Daten: Verlauf, PDF-Zuordnung, gesendete Batch-Anfragen
CREDENTIALS_FILE = CONF_DIR / "credentials.json"
TOKEN_FILE = CONF_DIR / "token.json"
HISTORY = DATA_DIR / "batch_jobs.json"
FILES_DB = DATA_DIR / "pdf_files.json"  # PDF-Hash -> Name der Datei in der Gemini Files API
OPENAI_FILES_DB = DATA_DIR / "openai_files.json"  # PDF-Hash -> ID der Datei in der OpenAI Files API
PROVIDER_GEMINI = "Gemini"
PROVIDER_OPENAI = "OpenAI"
PROVIDERS = [PROVIDER_GEMINI, PROVIDER_OPENAI]  # Auswahl in der Seitenleiste, Voreinstellung ist der erste
DEFAULT_MODEL = "gemini-4-carbon"
DEFAULT_OPENAI_MODEL = "gpt-6-astra"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
POLL_SECONDS = 10  # Abstand der automatischen Prüfung im Tab „Jobs und Ergebnisse“
DONE = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED",  # Gemini
        "completed", "failed", "cancelled", "expired"}  # OpenAI (Status der Batch API, klein geschrieben)
BATCH_MAX_OUT = 32768      # Batch-Anfragen unterstützen kein Long Decode
SYNC_MAX_OUT = 1_048_576   # synchron: Long Decode über das SDK (ab google-genai 2.30.1)
SYNC_TIMEOUT_MS = 25 * 60 * 1000  # Carbon denkt länger als Argon (Doku: Timeouts von mindestens 15 bis 20 Minuten)
MAX_POLL_ERRORS = 3        # so viele Abruffehler hintereinander, dann stoppt die automatische Prüfung
TABS = ["Neue Anfrage", "Jobs und Ergebnisse"]
MODE_BATCH = "Batch (50 % Rabatt, Ergebnis nach Minuten bis Stunden)"
MODE_SYNC = "Synchron (nacheinander, Cache-Treffer möglich)"
THINK_LEVELS = ["Standard", "low", "medium", "high"]
# OpenAI: reasoning.effort. Welche Stufen ein Modell kennt, hängt vom Modell ab, die API lehnt andere ab.
OPENAI_EFFORTS = ["Standard", "none", "minimal", "low", "medium", "high", "xhigh", "max"]
OPENAI_ENDPOINT = "/v1/responses"  # Endpunkt der OpenAI Batch API (nimmt Denktiefe und PDFs als Datei-ID entgegen)
OPENAI_MAX_OUT = 128_000   # größte Ausgabe-Obergrenze der aktuellen Modelle, je Modell kann sie niedriger sein
OPENAI_FILE_TTL = 3 * 24 * 3600  # hochgeladene PDFs verfallen bei OpenAI nach 3 Tagen (erlaubt: 1 Stunde bis 30 Tage)
# Formularfelder mit Schlüssel. Streamlit vergisst den Wert eines Widgets, das einen Lauf lang fehlt (Felder des anderen
# Anbieters, alles hinter st.stop()), und der Browser baut es dann mit dem Standardwert neu auf. Darum wird jeder Wert
# vor dem Zeichnen neu zugewiesen: Das hält ihn am Leben und schickt ihn an den Browser.
FORM_KEYS = ["model_gemini", "model_openai", "mode", "system", "raw", "group", "think", "effort", "max_batch",
             "max_sync", "max_openai"]
HINT = " (bitte den Tab nicht wechseln)"
SEPARATOR = re.compile(r"^\s*---\s*$", re.M)  # eine Zeile nur mit --- trennt im Formular die Prompts
# Gesprächsverlauf als Text („Gespräch fortsetzen“): Je Runde steht mein früherer Prompt und deine Antwort dazu.
# Jede Marke steht allein in ihrer Zeile. parse_conversation() liest den Text wieder ein, damit sich ein Gespräch
# beliebig oft fortsetzen lässt, ohne dass Verläufe ineinander geschachtelt werden.
TALK_INTRO = ("Unten steht der bisherige Verlauf unseres Gesprächs: meine früheren Prompts und deine Antworten dazu. "
              "Setze das Gespräch fort und beantworte meinen neuen Prompt am Ende. "
              "Der Verlauf ist nur Kontext, arbeite ihn nicht noch einmal ab.")
TALK_BEGIN = "[Bisheriger Gesprächsverlauf]"
TALK_MINE = "[Runde {n}: Mein Prompt]"
TALK_YOURS = "[Runde {n}: Deine Antwort dazu]"
TALK_END = "[Ende des bisherigen Gesprächsverlaufs]"
TALK_NEW = "[Mein neuer Prompt]"
TALK_MARK = re.compile(  # erkennt genau diese Marken (auch mit Leerzeichen davor oder dahinter)
    r"^[^\S\n]*\[(?:(?P<begin>Bisheriger Gesprächsverlauf)|(?P<mine>Runde \d+: Mein Prompt)"
    r"|(?P<yours>Runde \d+: Deine Antwort dazu)|(?P<end>Ende des bisherigen Gesprächsverlaufs)"
    r"|(?P<new>Mein neuer Prompt))\][^\S\n]*$", re.M)


def prepare_folders():
    """conf/ und data/ anlegen und Dateien aus dem Hauptordner (älterer Stand) dorthin verschieben, sofern am Ziel
    noch nichts liegt. Gibt die verschobenen Dateien als Text zurück."""
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    legacy = [(BASE / n, CONF_DIR) for n in ("credentials.json", "token.json")]
    legacy += [(BASE / n, DATA_DIR) for n in ("batch_jobs.json", "pdf_files.json")]
    legacy += [(f, DATA_DIR) for pattern in ("requests_*.jsonl", "batch_jobs.defekt-*.json") for f in BASE.glob(pattern)]
    moved = []
    for src, folder in legacy:
        if src.is_file() and not (folder / src.name).exists():
            shutil.move(str(src), str(folder / src.name))
            moved.append(f"{src.name} nach {folder.name}/")
    return moved
