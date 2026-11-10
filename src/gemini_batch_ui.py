"""Lokale Batch- und Synchron-UI für Gemini 4 Argon und OpenAI (Streamlit).

Start:    pip install -r requirements.txt && streamlit run src/gemini_batch_ui.py   (im Projektordner)
API-Key:  Umgebungsvariablen GEMINI_API_KEY und OPENAI_API_KEY oder Seitenleiste.
Anbieter: Gemini (Batch und Synchron) oder OpenAI (nur Batch, Responses-Endpunkt), gewählt in der Seitenleiste.
Modi:     Batch (asynchron, 50 % Rabatt) oder Synchron (nur Gemini: nacheinander, impliziter Cache über alle Prompts,
          optional in Gesprächen: je N Prompts bilden einen gemeinsamen Kontext, das nächste Gespräch beginnt neu).
Ordner:   conf/ (Zugangsdaten, geheim: credentials.json, token.json) und data/ (batch_jobs.json = Verlauf und
          Ergebnisse, pdf_files.json und openai_files.json = hochgeladene PDFs, requests_*.jsonl = gesendete
          Batch-Anfragen).
          Beide liegen im Projektordner neben src/ (dem Ordner dieser Datei), oder unter dem Ordner aus der
          Umgebungsvariable GEMINI_UI_HOME.
Drive:    (optional) In der Google Cloud Console die Drive API aktivieren, eine OAuth-Client-ID
          vom Typ „Desktop-App“ erstellen und als conf/credentials.json ablegen.
          Beim ersten Zugriff öffnet sich der Browser zur Freigabe (nur Lesezugriff).
"""
import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import streamlit as st
from google import genai
from google.genai import types


def _base_dir():
    """Projektordner, in dem conf/ und data/ liegen: GEMINI_UI_HOME, sonst der Ordner über src/.
    Liegt diese Datei nicht in einem Ordner namens src, ist es ihr eigener Ordner (älteres, flaches Layout)."""
    home = os.getenv("GEMINI_UI_HOME")
    if home:
        return Path(home).expanduser().resolve()
    try:
        app_dir = Path(__file__).resolve().parent
    except NameError:  # z. B. bei exec() in Tests
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
DEFAULT_MODEL = "gemini-4-argon"
DEFAULT_OPENAI_MODEL = "gpt-6-astra"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
POLL_SECONDS = 10  # Abstand der automatischen Prüfung im Tab „Jobs und Ergebnisse“
DONE = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED",  # Gemini
        "completed", "failed", "cancelled", "expired"}  # OpenAI (Status der Batch API, klein geschrieben)
BATCH_MAX_OUT = 32768      # Batch-Anfragen unterstützen kein Long Decode
SYNC_MAX_OUT = 1_048_576   # synchron: Long Decode über das SDK (ab google-genai 2.30.1)
SYNC_TIMEOUT_MS = 25 * 60 * 1000  # Argon-Aufrufe können lange laufen (Doku: mindestens 20 Minuten)
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

st.set_page_config(page_title="Gemini Argon", layout="wide")


# ---------- Verlauf (Batches laufen bis zu 24 h, synchrone Läufe im Hintergrund: daher lokal gespeichert) ----------
@st.cache_resource(show_spinner=False)
def runtime():
    """Prozessweit geteilt: Sperre für den Verlauf, laufende Threads, Abbruchwünsche."""
    return SimpleNamespace(lock=threading.Lock(), threads={}, cancel=set())


def load_jobs():
    if not HISTORY.exists():
        return []
    try:
        return json.loads(HISTORY.read_text("utf-8"))
    except json.JSONDecodeError:  # beschädigte Datei sichern, statt die App abstürzen zu lassen
        HISTORY.rename(HISTORY.with_name(f"batch_jobs.defekt-{int(time.time())}.json"))
        return []


def mutate_jobs(lock, change):
    """Verlauf unter Sperre lesen, mit change(jobs) ändern und atomar zurückschreiben (auch aus Threads nutzbar)."""
    with lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        jobs = load_jobs()
        change(jobs)
        tmp = HISTORY.with_suffix(".tmp")
        tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, HISTORY)


# ---------- Ordner conf/ und data/ ----------
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


# ---------- Google Drive ----------
def drive_service():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        try:
            if not (creds and creds.expired and creds.refresh_token):
                raise RefreshError("kein gültiger Login")
            creds.refresh(Request())
        except RefreshError:  # keine, abgelaufene oder widerrufene Freigabe (z. B. nach 7 Tagen im Testmodus)
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(f"credentials.json fehlt. Lege sie nach {CONF_DIR} (Anleitung oben im Skript).")
            creds = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES).run_local_server(port=0)
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)


def drive_id(text):
    m = re.search(r"/d/([\w-]{20,})|[?&]id=([\w-]{20,})", text)
    return (m.group(1) or m.group(2)) if m else text.strip()


def drive_fetch(file_id):
    svc = drive_service()
    meta = svc.files().get(fileId=file_id, fields="name,mimeType", supportsAllDrives=True).execute()
    mime, name = meta["mimeType"], meta["name"]
    if mime.startswith("application/vnd.google-apps"):  # Docs, Slides usw. werden als PDF exportiert
        data = svc.files().export_media(fileId=file_id, mimeType="application/pdf").execute()
    elif mime == "application/pdf":
        data = svc.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
    else:
        raise ValueError(f"„{name}“ ist kein PDF (Typ: {mime}).")
    return (name if name.lower().endswith(".pdf") else name + ".pdf"), data


# ---------- Gemini ----------
def sdk_has_long_decode():
    try:
        return tuple(int(x) for x in genai.__version__.split(".")[:3]) >= (2, 30, 1)
    except ValueError:
        return True


def upload_pdf(client, name, data):
    key = hashlib.sha256(data).hexdigest()
    db = json.loads(FILES_DB.read_text("utf-8")) if FILES_DB.exists() else {}
    if key in db:  # dasselbe PDF wiederverwenden: gleiche Datei-Referenz, solange die Files API sie hält (48 h)
        try:
            f = client.files.get(name=db[key])
            if not f.state or f.state.name == "ACTIVE":
                return f
        except Exception:
            pass
    f = client.files.upload(
        file=io.BytesIO(data),
        config=types.UploadFileConfig(mime_type="application/pdf", display_name=name[:100]),
    )
    while f.state and f.state.name == "PROCESSING":
        time.sleep(2)
        f = client.files.get(name=f.name)
    if f.state and f.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini konnte das PDF nicht verarbeiten (Status {f.state.name}).")
    db[key] = f.name
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DB.write_text(json.dumps(db), "utf-8")
    return f


def start_batch(client, model, pdfs, prompts, system, think, max_out, label):
    gen = {"max_output_tokens": int(max_out)}
    if think != "Standard":
        gen["thinking_config"] = {"thinking_level": think}
    lines, keyed = [], {}
    for i, p in enumerate(prompts, 1):
        key = f"prompt-{i}"
        keyed[key] = p
        # PDFs stehen in jeder Anfrage ganz vorn, immer in derselben Reihenfolge -> identischer Prefix fürs Caching
        parts = [{"file_data": {"mime_type": "application/pdf", "file_uri": f.uri}} for f in pdfs] + [{"text": p}]
        req = {"contents": [{"role": "user", "parts": parts}], "generation_config": gen}
        if system:
            req["system_instruction"] = {"parts": [{"text": system}]}
        lines.append(json.dumps({"key": key, "request": req}, ensure_ascii=False))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"requests_{int(time.time())}.jsonl"
    path.write_text("\n".join(lines), "utf-8")
    jf = client.files.upload(file=str(path), config=types.UploadFileConfig(display_name=label, mime_type="jsonl"))
    job = client.batches.create(model=model, src=jf.name, config={"display_name": label})
    return job, keyed


def pick(d, *names, default=0):
    for n in names:
        if d.get(n) is not None:
            return d[n]
    return default


def parse_line(o, prompts):
    """Eine Ergebniszeile (Batch-JSONL oder umgewandelte synchrone Antwort) in eine Tabellenzeile überführen."""
    resp = o.get("response") or {}
    usage = pick(resp, "usageMetadata", "usage_metadata", default={})
    cand = (resp.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    reason = pick(cand, "finishReason", "finish_reason", default="unbekannt")
    return {
        "key": o.get("key", "?"),
        "prompt": prompts.get(o.get("key"), ""),
        "text": text,
        "model": pick(resp, "modelVersion", "model_version", default="unbekannt"),
        "finish": reason,
        "usage": usage,
        "error": o.get("error") or (None if text else f"Keine Antwort (finishReason: {reason})"),
        "in": pick(usage, "promptTokenCount", "prompt_token_count"),
        "cached": pick(usage, "cachedContentTokenCount", "cached_content_token_count",
                       "totalCachedTokens", "total_cached_tokens"),
        "think": pick(usage, "thoughtsTokenCount", "thoughts_token_count"),
        "out": pick(usage, "candidatesTokenCount", "candidates_token_count"),
    }


def parse_results(raw, prompts, parse=parse_line):
    rows = [parse(json.loads(line), prompts) for line in raw.decode("utf-8").splitlines() if line.strip()]
    num = lambda r: int(r["key"].rsplit("-", 1)[-1]) if r["key"].rsplit("-", 1)[-1].isdigit() else 0
    return sorted(rows, key=num)


def refresh(client, j):
    if entry_provider(j) == PROVIDER_OPENAI:
        state, rows, err, api_model = fetch_openai_batch(client, j)
    else:
        job = client.batches.get(name=j["name"])
        state, rows = job.state.name, None
        if state == "JOB_STATE_SUCCEEDED" and job.dest and job.dest.file_name:
            rows = parse_results(client.files.download(file=job.dest.file_name), j["prompts"])
        err, api_model = (str(job.error) if job.error else None), job.model
    st.session_state["res:" + j["name"]] = {"state": state, "rows": rows, "error": err, "model": api_model}
    if state in DONE:  # Endergebnis lokal sichern: bleibt nach Ablauf beim Anbieter erhalten und lädt ohne Abruf
        def save(jobs):
            for x in jobs:
                if x["name"] == j["name"]:
                    x.update(state=state, rows=rows, error=err, api_model=api_model)
        mutate_jobs(runtime().lock, save)


def batch_info(j):
    """Stand eines Batches: aus der laufenden Sitzung, sonst aus der lokalen Sicherung des Endergebnisses."""
    info = st.session_state.get("res:" + j["name"])
    if info is None and j.get("state") in DONE:
        info = {"state": j["state"], "rows": j.get("rows"), "error": j.get("error"), "model": j.get("api_model")}
    return info


# ---------- OpenAI (nur Batch, Endpunkt /v1/responses) ----------
def openai_client(api_key):
    """OpenAI-Client. Das Paket wird erst hier geladen: Wer nur Gemini nutzt, braucht es nicht."""
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("Das Paket openai fehlt. Installieren: pip install -r requirements.txt") from e
    return OpenAI(api_key=api_key)


def make_client(provider, api_key):
    return openai_client(api_key) if provider == PROVIDER_OPENAI else genai.Client(api_key=api_key)


def entry_provider(j):
    """Anbieter eines Eintrags. Ältere Einträge haben kein Feld provider und stammen von Gemini."""
    return PROVIDER_OPENAI if j.get("provider") == PROVIDER_OPENAI else PROVIDER_GEMINI


def upload_pdf_openai(client, name, data):
    """PDF in die OpenAI Files API laden (Zweck user_data). Gleicher Inhalt: gleiche Datei, solange OpenAI sie hält."""
    key = hashlib.sha256(data).hexdigest()
    db = json.loads(OPENAI_FILES_DB.read_text("utf-8")) if OPENAI_FILES_DB.exists() else {}
    if key in db:
        try:
            f = client.files.retrieve(db[key])
            if getattr(f, "status", None) != "error":
                return f
        except Exception:  # abgelaufen, gelöscht oder aus einem anderen Projekt: neu hochladen
            pass
    f = client.files.create(
        file=(name if name.lower().endswith(".pdf") else name + ".pdf", io.BytesIO(data), "application/pdf"),
        purpose="user_data",
        expires_after={"anchor": "created_at", "seconds": OPENAI_FILE_TTL},
    )
    db[key] = f.id
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OPENAI_FILES_DB.write_text(json.dumps(db), "utf-8")
    return f


def openai_request(model, pdf_ids, prompt, system, effort, max_out):
    """Rumpf einer Responses-Anfrage für einen Prompt. Die PDFs stehen ganz vorn, immer in derselben Reihenfolge:
    identischer Prefix fürs automatische Caching."""
    parts = [{"type": "input_file", "file_id": i} for i in pdf_ids] + [{"type": "input_text", "text": prompt}]
    body = {"model": model, "input": [{"role": "user", "content": parts}], "max_output_tokens": int(max_out)}
    if system:
        body["instructions"] = system
    if effort != "Standard":
        body["reasoning"] = {"effort": effort}
    return body


def start_openai_batch(client, model, pdf_ids, prompts, system, effort, max_out, label):
    """JSONL bauen (je Prompt eine Zeile), hochladen, Batch anlegen. Gibt (Batch-ID, Prompts je Schlüssel) zurück."""
    lines, keyed = [], {}
    for i, p in enumerate(prompts, 1):
        key = f"prompt-{i}"
        keyed[key] = p
        body = openai_request(model, pdf_ids, p, system, effort, max_out)
        lines.append(json.dumps({"custom_id": key, "method": "POST", "url": OPENAI_ENDPOINT, "body": body},
                                ensure_ascii=False))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"requests_openai_{int(time.time())}.jsonl"
    path.write_text("\n".join(lines), "utf-8")
    with path.open("rb") as f:
        jf = client.files.create(file=f, purpose="batch")
    batch = client.batches.create(input_file_id=jf.id, endpoint=OPENAI_ENDPOINT, completion_window="24h",
                                  metadata={"description": label[:512]})
    return batch.id, keyed


def openai_error_text(err):
    """Fehlerobjekt der API ({"code": …, "message": …}) als Text."""
    if isinstance(err, dict):
        return ": ".join(str(err[k]) for k in ("code", "message") if err.get(k)) or json.dumps(err, ensure_ascii=False)
    return str(err)


def parse_openai_line(o, prompts):
    """Eine Ergebniszeile des OpenAI-Batches (Ausgabe- oder Fehlerdatei) in eine Tabellenzeile überführen, in der Form
    von parse_line(). OpenAI rechnet Denk-Token in output_tokens ein, die Zeile trennt sie: „Ausgabe“ ohne Denken."""
    key = o.get("custom_id", "?")
    resp = o.get("response") or {}
    body = resp.get("body") or {}
    parts = [c for item in body.get("output") or [] if item.get("type") == "message" for c in item.get("content") or []]
    text = "".join(c.get("text", "") for c in parts if c.get("type") == "output_text")
    refusal = " ".join(c.get("refusal", "") for c in parts if c.get("type") == "refusal").strip()
    status, why = body.get("status"), (body.get("incomplete_details") or {}).get("reason")
    if not status:  # Fehlerzeile oder Fehlerrumpf statt einer Antwort
        finish = "unbekannt"
    elif status == "incomplete":
        # wie bei Gemini „MAX_TOKENS“, damit die Warnung in show_rows() greift
        finish = "MAX_TOKENS" if why == "max_output_tokens" else why or status
    else:
        finish = "STOP" if status == "completed" else status
    err = o.get("error") or body.get("error")
    if err:
        err = openai_error_text(err)
    elif resp.get("status_code") not in (None, 200):
        err = f"HTTP {resp['status_code']}"
    elif refusal and not text:
        err = f"Das Modell hat abgelehnt: {refusal}"
    elif not text:
        err = f"Keine Antwort (Abschlussgrund: {finish})"
    usage = body.get("usage") or {}
    think = pick(usage.get("output_tokens_details") or {}, "reasoning_tokens")
    return {
        "key": key,
        "prompt": prompts.get(key, ""),
        "text": text,
        "model": body.get("model") or "unbekannt",
        "finish": finish,
        "usage": usage,
        "error": err,
        "in": pick(usage, "input_tokens"),
        "cached": pick(usage.get("input_tokens_details") or {}, "cached_tokens"),
        "think": think,
        "out": max(pick(usage, "output_tokens") - think, 0),
    }


def fetch_openai_batch(client, j):
    """Stand eines OpenAI-Batches als (Status, Zeilen, Fehlertext, Modell). Hat er einen Endzustand, werden Ausgabe- und
    Fehlerdatei geladen. Bei Ablauf oder Abbruch enthalten sie die bis dahin fertigen Antworten und die Fehler."""
    b = client.batches.retrieve(j["name"])
    rows = None
    if b.status in DONE:
        raw = b"\n".join(client.files.content(fid).read() for fid in (b.output_file_id, b.error_file_id) if fid)
        rows = parse_results(raw, j["prompts"], parse_openai_line) if raw.strip() else None
    errors = []  # Fehler des ganzen Batches, z. B. ein überschrittenes Token-Limit der Warteschlange
    for e in (b.errors.data if b.errors and b.errors.data else []):
        where = f" (Zeile {e.line})" if e.line else ""
        errors.append(openai_error_text({"code": e.code, "message": e.message}) + where)
    return b.status, rows, "; ".join(errors) or None, b.model


def cancel_batch(client, j):
    if entry_provider(j) == PROVIDER_OPENAI:
        client.batches.cancel(j["name"])
    else:
        client.batches.cancel(name=j["name"])


# ---------- Synchroner Modus (Hintergrund-Thread, kein Streamlit-Aufruf darin) ----------
def ask(client, model, contents, cfg):
    """Eine synchrone Anfrage (Antwortobjekt); bei vorübergehenden Fehlern (429/5xx) bis zu zweimal wiederholen."""
    for attempt in range(3):
        try:
            return client.models.generate_content(model=model, contents=contents, config=cfg)
        except Exception as e:
            if attempt < 2 and getattr(e, "code", None) in (429, 500, 503, 504):
                time.sleep(10 * (attempt + 1))
                continue
            raise


def run_sync(client, rt, name, model, pdf_uris, prompts, system, think, max_out, group=1):
    """Prompts nacheinander senden. Die erste Anfrage füllt den impliziten Cache, die folgenden können davon profitieren.
    Mit group > 1 bilden je group aufeinanderfolgende Prompts ein Gespräch: Jeder sieht die vorherigen Prompts und
    Antworten seines Gesprächs. Das nächste Gespräch beginnt wieder mit dem PDF und ohne Verlauf."""
    def set_state(state, error=None):
        def change(jobs):
            for x in jobs:
                if x["name"] == name:
                    x["state"] = state
                    if error:
                        x["error"] = error
        mutate_jobs(rt.lock, change)

    def add_row(row):
        def change(jobs):
            for x in jobs:
                if x["name"] == name:
                    x["rows"].append(row)
        mutate_jobs(rt.lock, change)

    try:
        cfg = types.GenerateContentConfig(
            system_instruction=system or None,
            max_output_tokens=int(max_out),
            thinking_config=types.ThinkingConfig(thinking_level=think) if think != "Standard" else None,
            http_options=types.HttpOptions(timeout=SYNC_TIMEOUT_MS),
        )
        history, broken = [], None  # Verlauf des aktuellen Gesprächs; Nummer eines Gesprächs, das nicht weitergeht
        for i, (key, prompt) in enumerate(prompts.items()):
            if i % group == 0:
                history = []  # neues Gespräch
            if name in rt.cancel:
                set_state("CANCELLED")
                return
            if broken == i // group:
                skipped = "Übersprungen: Ein vorheriger Prompt dieses Gesprächs hat keine Antwort geliefert."
                add_row(parse_line({"key": key, "error": skipped}, prompts))
                continue
            # PDFs stehen in jeder Anfrage (im Gespräch: im ersten Prompt) ganz vorn -> identischer Prefix fürs Caching
            parts = [types.Part.from_uri(file_uri=u, mime_type="application/pdf") for u in pdf_uris] if not history else []
            user = types.Content(role="user", parts=parts + [types.Part(text=prompt)])
            reply, started = None, time.time()
            try:
                resp = ask(client, model, history + [user], cfg)
                line = {"key": key, "response": resp.model_dump(mode="json", by_alias=True, exclude_none=True)}
                reply = resp.candidates[0].content if resp.candidates else None
            except Exception as e:
                line = {"key": key, "error": str(e)}
            row = parse_line(line, prompts)
            row["secs"] = round(time.time() - started)
            add_row(row)
            if group > 1:
                if reply and reply.parts:  # Antwort unverändert zurückgeben, damit Signaturen erhalten bleiben
                    history += [user, reply]
                else:
                    broken = i // group  # der Rest dieses Gesprächs wird übersprungen
        set_state("DONE")
    except Exception as e:
        set_state("FAILED", str(e))
    finally:
        rt.cancel.discard(name)


def start_sync(client, rt, model, pdfs, pdf_names, prompts, system, think, max_out, label, group=1):
    name = f"sync-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    keyed = {f"prompt-{i}": p for i, p in enumerate(prompts, 1)}
    entry = {"name": name, "mode": "sync", "state": "RUNNING", "label": label, "model": model,
             "pdf": ", ".join(pdf_names) or None, "pdfs": list(pdf_names),
             "prompts": keyed, "system": system, "think": think, "max_out": int(max_out), "group": int(group),
             "rows": [],
             "created": datetime.now().isoformat(timespec="seconds")}
    mutate_jobs(rt.lock, lambda jobs: jobs.insert(0, entry))
    thread = threading.Thread(
        target=run_sync, daemon=True,
        args=(client, rt, name, model, [f.uri for f in pdfs], keyed, system, think, max_out, group),
    )
    rt.threads[name] = thread
    thread.start()
    return entry


def sync_view(rt, name):
    """Eintrag und Zustand eines synchronen Laufs. RUNNING gilt nur, solange der Thread lebt."""
    thread = rt.threads.get(name)
    alive = bool(thread and thread.is_alive())  # erst den Thread prüfen, dann die Datei lesen
    j = next((x for x in load_jobs() if x["name"] == name), None)
    state = j.get("state", "RUNNING") if j else None
    if state == "RUNNING" and not alive:
        state = "INTERRUPTED"
    return j, state


# ---------- Gesprächsverlauf als Text (früherer Prompt und deine Antwort dazu, zum Fortsetzen) ----------
def entry_group(j):
    """Prompts pro Gespräch eines Eintrags (Batch: 1). Ältere synchrone Einträge kennen nur „chat“ (alles ein Gespräch)."""
    if j.get("mode") != "sync":
        return 1
    return max(int(j.get("group") or (len(j["prompts"]) if j.get("chat") else 1)), 1)


def talk_safe(text):
    """Text für den Gesprächsverlauf vorbereiten. Eine Zeile nur aus --- würde im Formular die Prompts trennen (daraus
    wird ***, in Markdown dieselbe Trennlinie), und Zeilen, die wie eine Marke aussehen, bekommen einen Rückstrich.
    Ein zweites Anwenden ändert nichts mehr."""
    text = re.sub(r"^[^\S\n]*---[^\S\n]*$", "***", text.strip(), flags=re.M)
    return TALK_MARK.sub(lambda m: "\\" + m.group(0).strip(), text)


def conversation_text(turns, new_prompt=""):
    """Gesprächsverlauf als Text, der einem neuen Prompt vorangestellt wird: je Runde mein früherer Prompt und deine
    Antwort dazu (turns = [(Prompt, Antwort), …]), darunter Platz für den neuen Prompt."""
    lines = [TALK_INTRO, "", TALK_BEGIN]
    for n, (prompt, answer) in enumerate(turns, 1):
        lines += ["", TALK_MINE.format(n=n), talk_safe(prompt), "", TALK_YOURS.format(n=n), talk_safe(answer)]
    return "\n".join(lines + ["", TALK_END, "", TALK_NEW, new_prompt])


def parse_conversation(text):
    """Gegenstück zu conversation_text(): ([(Prompt, Antwort), …], neuer Prompt), wenn text einen Gesprächsverlauf
    enthält, sonst ([], text). Ist der Aufbau durcheinander (z. B. von Hand bearbeitet), gilt text als gewöhnlicher Prompt."""
    after = {"begin": ("mine",), "mine": ("yours",), "yours": ("mine", "end"), "end": ("new",)}
    turns, prompt, state, pos, end = [], "", None, 0, None
    for m in TALK_MARK.finditer(text):
        kind, chunk = m.lastgroup, text[pos:m.start()].strip()
        if state is None:
            if kind != "begin":  # vor dem Beginn steht nur die Einleitung
                continue
        elif kind not in after.get(state, ()):
            return [], text
        if state == "mine":
            prompt = chunk
        elif state == "yours":
            turns.append((prompt, chunk))
        state, pos = kind, m.end()
        if kind == "end":
            end = pos
        elif kind == "new":
            return turns, text[pos:].strip()
    if turns and end is not None:  # Marke für den neuen Prompt gelöscht: der Rest zählt als neuer Prompt
        return turns, text[end:].strip()
    return [], text


def history_turns(j, rows, key):
    """Gesprächsverlauf bis einschließlich der Antwort zu key als [(Prompt, Antwort), …], oder None, wenn er sich nicht
    bilden lässt (eine Antwort fehlt). Bei synchronen Gesprächen gehören die früheren Runden desselben Gesprächs dazu.
    Beginnt der erste Prompt selbst mit einem Verlauf (frühere Fortsetzung), wird dieser davor eingereiht statt
    verschachtelt: Bei jeder Fortsetzung wächst der Verlauf um genau eine Runde."""
    keys, group = list(j["prompts"]), entry_group(j)
    if key not in keys:
        return None
    last = keys.index(key)
    done, turns = {r["key"]: r for r in rows}, []
    for k in keys[last // group * group:last + 1]:
        r = done.get(k)
        if not r or not r.get("text"):
            return None
        turns.append((r.get("prompt") or j["prompts"].get(k, ""), r["text"]))
    earlier, own = parse_conversation(turns[0][0])
    if earlier:
        turns[0] = (own, turns[0][1])
    return earlier + turns


def short_prompt(prompt, n=90):
    """Anfang eines Prompts für Überschriften. Setzt er ein früheres Gespräch fort, zählt der neue Prompt (mit ↪)."""
    turns, own = parse_conversation(prompt)
    return "↪ " + own[:n] if turns else prompt[:n]


# ---------- Anzeige (läuft als Fragment, siehe Tab „Jobs und Ergebnisse“) ----------
def show_text(text):
    """Text im Wortlaut zeigen (festbreit, mit Kopierknopf). Lange Texte scrollen in einem eigenen Rahmen."""
    box = st.container(height=400) if len(text) > 3000 else st.container()
    box.code(text, language=None, wrap_lines=True)


def show_prompt(prompt):
    """Einen gesendeten Prompt im Wortlaut zeigen, mit Hinweis, wenn er ein früheres Gespräch fortsetzt."""
    turns, _ = parse_conversation(prompt)
    if turns:
        st.caption(f"Setzt ein früheres Gespräch fort: Der Prompt enthält den Verlauf von {len(turns)} Runde(n) "
                   "vor dem neuen Prompt.")
    show_text(prompt)


def request_details(j):
    """Die gesendete Anfrage eines Eintrags im Wortlaut: PDFs, Systemanweisung und jeder Prompt. Funktioniert auch,
    solange noch keine Ergebnisse da sind."""
    pdfs = j.get("pdfs") or ([j["pdf"]] if j.get("pdf") else [])
    st.markdown("**PDFs** (Reihenfolge in jeder Anfrage): "
                + (", ".join(f"{k}. {n}" for k, n in enumerate(pdfs, 1)) if pdfs else "keine"))
    st.markdown("**Systemanweisung**")
    if j.get("system"):
        show_text(j["system"])
    else:
        st.caption("Keine Systemanweisung.")
    group = entry_group(j)
    for n, (key, prompt) in enumerate(j["prompts"].items()):
        where = f" (Gespräch {n // group + 1}, Runde {n % group + 1})" if group > 1 else ""
        st.markdown(f"**{key}**{where}")
        show_prompt(prompt)


def talk_tools(j, rows, r):
    """Unter einer Antwort: „Gespräch fortsetzen“ übernimmt den Verlauf als Text ins Formular, „Verlaufstext zeigen“
    zeigt ihn zum Kopieren. Ohne Antwort (Fehler) gibt es nichts fortzusetzen."""
    turns = history_turns(j, rows, r["key"])
    if not turns:
        return
    tag = f"{j['name']}:{r['key']}"
    c1, c2, _ = st.columns([1, 1, 2])
    if c1.button("Gespräch fortsetzen", key="talk:" + tag, on_click=continue_conversation, args=(j, turns),
                 help="Übernimmt den bisherigen Verlauf (früherer Prompt und deine Antwort dazu) als Text ins "
                      f"Formular „Neue Anfrage“. Dort schreibst du unter „{TALK_NEW}“ den nächsten Prompt."):
        st.rerun()  # Fragmentläufe ändern nur das Fragment: ein voller Rerun zeigt das gefüllte Formular
    show = c2.toggle("Verlaufstext zeigen", key="hist:" + tag, help="Der Verlauf als Text, wie er im Formular landet.")
    if show:
        text = conversation_text(turns)
        st.caption(f"Verlauf aus {len(turns)} Runde(n), {len(text):,} Zeichen. Der Text lässt sich mit dem Symbol "
                   "rechts oben in der Textbox kopieren.")
        show_text(text)


def show_rows(j, rows):
    if not rows:
        return
    tin, tc = sum(r["in"] for r in rows), sum(r["cached"] for r in rows)
    st.caption(f"Eingabe-Token gesamt: {tin:,}, davon aus dem Cache: {tc:,}" + (f" ({tc / tin:.0%})" if tin else ""))
    md = "\n\n".join(f"## {r['key']}\n\n**Prompt:** {r['prompt']}\n\n{r['text'] or r['error']}" for r in rows)
    st.download_button("Ergebnisse als Markdown", md, file_name=f"{j['label']}.md", key="dl:" + j["name"])
    for r in rows:
        with st.expander(f"{r['key']}: {short_prompt(r['prompt'])}", expanded=len(rows) <= 3):
            if st.toggle("Prompt im Wortlaut", key=f"prompt:{j['name']}:{r['key']}",
                         help="Zeigt den vollständigen Prompt, so wie er gesendet wurde."):
                show_prompt(r["prompt"])
            if r["text"]:
                st.markdown(r["text"])
            else:
                st.error(str(r["error"]))
            fin = r.get("finish")
            if fin == "MAX_TOKENS":
                st.warning("Abgeschnitten: Denk- und Ausgabe-Token haben das Limit erreicht.")
            elif fin not in (None, "STOP", "unbekannt"):
                st.warning(f"Die Antwort endete mit dem Grund „{fin}“ und ist möglicherweise unvollständig.")
            st.caption(f"Modell {r.get('model', '?')}, Ende: {fin or '?'}, "
                       f"Eingabe {r['in']:,} (Cache {r['cached']:,}), "
                       f"Denken {r['think']:,}, Ausgabe {r['out']:,} Token"
                       + (f", Dauer {r['secs']} s" if r.get("secs") is not None else ""))
            if r.get("usage"):
                st.json(r["usage"], expanded=False)
            talk_tools(j, rows, r)


def sync_panel(rt, name, polling):
    j, state = sync_view(rt, name)
    if j is None:
        st.warning("Eintrag nicht gefunden.")
        return
    if polling and state != "RUNNING":
        st.rerun()  # Lauf beendet: ein kompletter Rerun beendet die automatische Prüfung
    rows, total = j.get("rows", []), len(j["prompts"])
    if state == "RUNNING":
        if st.button("Lauf abbrechen", key="stop:" + name):
            rt.cancel.add(name)
            st.warning("Abbruch angefordert. Die laufende Anfrage wird noch zu Ende geführt.")
        if polling:
            st.caption(f"Automatische Prüfung alle {POLL_SECONDS} Sekunden, zuletzt um {datetime.now():%H:%M:%S}.")
    texts = {
        "RUNNING": "läuft",
        "DONE": "fertig",
        "CANCELLED": "abgebrochen",
        "FAILED": "fehlgeschlagen",
        "INTERRUPTED": "unterbrochen (die App wurde beendet oder neu gestartet)",
    }
    st.write(f"Status: **{texts.get(state, state)}**, {len(rows)} von {total} Prompts fertig")
    if j.get("error"):
        st.error(j["error"])
    show_rows(j, rows)


def job_panel(client, rt, j, polling):
    """Status und Ergebnisse. Mit polling=True prüft das Fragment alle POLL_SECONDS Sekunden neu."""
    if st.toggle("Gesendete Anfrage im Detail", key="detail:" + j["name"],
                 help="PDFs, Systemanweisung und jeden Prompt im Wortlaut, auch solange noch keine Ergebnisse da sind."):
        request_details(j)
    if j.get("mode") == "sync":
        return sync_panel(rt, j["name"], polling)
    name = j["name"]
    errn_key, err_key = f"errn:{name}", f"err:{name}"
    no_client = client is None  # der API-Key des Anbieters dieses Eintrags fehlt
    if no_client:
        st.warning(f"Der {entry_provider(j)} API-Key fehlt (Seitenleiste): Die Knöpfe „Status prüfen“ und "
                   "„Batch abbrechen“ sind gesperrt. Gesicherte Ergebnisse bleiben sichtbar.")
    b1, b2, _ = st.columns([1, 1, 3])
    manual = b1.button("Status prüfen / Ergebnisse laden", key="chk:" + name, disabled=no_client)
    if b2.button("Batch abbrechen", key="cx:" + name, disabled=no_client):
        try:
            cancel_batch(client, j)
            st.warning("Abbruch angefordert. Der Status wird gleich aktualisiert.")
        except Exception as e:
            st.error(f"Abbruch fehlgeschlagen: {e}")
    if (manual or polling) and not no_client:
        try:
            refresh(client, j)
            st.session_state[errn_key] = 0
        except Exception as e:
            # Batch existiert nicht mehr oder ist nicht zugänglich. Den HTTP-Code meldet Gemini als code,
            # OpenAI als status_code.
            fatal = getattr(e, "code", None) in (403, 404) or getattr(e, "status_code", None) in (403, 404)
            st.session_state[errn_key] = MAX_POLL_ERRORS if fatal else st.session_state.get(errn_key, 0) + 1
            st.session_state[err_key] = str(e)
    info = batch_info(j)
    stopped = st.session_state.get(errn_key, 0) >= MAX_POLL_ERRORS
    if polling and ((info and info["state"] in DONE) or stopped):
        st.rerun()  # fertig oder aufgegeben: ein kompletter Rerun beendet die automatische Prüfung
    if polling:
        st.caption(f"Automatische Prüfung alle {POLL_SECONDS} Sekunden, zuletzt um {datetime.now():%H:%M:%S}.")
    if st.session_state.get(errn_key, 0) > 0:
        hint = " Die automatische Prüfung ist gestoppt, „Status prüfen“ versucht es erneut." if stopped else ""
        st.error(f"Abruf fehlgeschlagen: {st.session_state.get(err_key)}{hint}")
    if not info:
        return
    if "res:" + name not in st.session_state:
        st.caption("Gesichertes Endergebnis aus data/batch_jobs.json, kein Abruf beim Anbieter nötig.")
    st.write(f"Status: **{info['state']}**, Modell laut API: **{info.get('model') or 'unbekannt'}**")
    if info["error"]:
        st.error(info["error"])
    show_rows(j, info["rows"] or [])


# ---------- Verlauf und Formular ----------
def entry_label(x):
    return f"{x['label']} ({x['created'][:16].replace('T', ' ')}) {x['name'][-6:]}"


def fill_form(j, raw, group, text):
    """Callback-Hilfe: Formular „Neue Anfrage“ mit dem Prompt-Text raw und den Einstellungen eines Eintrags füllen
    und zu dem Tab wechseln. text beginnt den einmaligen Hinweis. Nur aus Callbacks aufrufen (Widget-Werte lassen
    sich nur setzen, bevor das Widget im Lauf erzeugt wurde)."""
    ss, is_sync, prov = st.session_state, j.get("mode") == "sync", entry_provider(j)
    ss["provider"] = prov
    if j.get("model"):
        ss["model_openai" if prov == PROVIDER_OPENAI else "model_gemini"] = j["model"]
    ss["mode"] = MODE_SYNC if is_sync else MODE_BATCH
    ss["raw"] = raw
    ss["system"] = j.get("system", "")
    if prov == PROVIDER_OPENAI:
        if j.get("think") in OPENAI_EFFORTS:
            ss["effort"] = j["think"]
        if j.get("max_out"):
            ss["max_openai"] = max(256, min(int(j["max_out"]), OPENAI_MAX_OUT))
    else:
        if j.get("think") in THINK_LEVELS:
            ss["think"] = j["think"]
        if j.get("max_out"):
            cap = (SYNC_MAX_OUT if sdk_has_long_decode() else BATCH_MAX_OUT) if is_sync else BATCH_MAX_OUT
            ss["max_sync" if is_sync else "max_batch"] = max(256, min(int(j["max_out"]), cap))
    if is_sync:
        ss["group"] = min(max(int(group or 1), 1), 50)
    pdfs = j.get("pdf")
    note = text
    if pdfs:
        note += f" Bitte die PDFs erneut anhängen: {pdfs}. Schon hochgeladene Dateien werden wiederverwendet."
    ss["reused_note"] = note
    ss["view"] = TABS[0]  # wechselt zum Tab „Neue Anfrage“ (ab Streamlit 1.55)


def reuse_inputs(j):
    """Callback: Prompts und Einstellungen eines Eintrags ins Formular „Neue Anfrage“ übernehmen."""
    fill_form(j, "\n---\n".join(j["prompts"].values()), j.get("group"), f"Eingaben aus „{j['label']}“ übernommen.")


def continue_conversation(j, turns):
    """Callback: Gesprächsverlauf (turns, siehe history_turns) als Text ins Formular übernehmen. Darunter schreibt man
    den neuen Prompt. Der Verlauf steckt im Prompt selbst, deshalb „Prompts pro Gespräch“ = 1."""
    fill_form(j, conversation_text(turns), 1,
              f"Gespräch aus „{j['label']}“ fortgesetzt: {len(turns)} Runde(n) Verlauf stehen im Prompt. "
              f"Schreibe deinen neuen Prompt unter „{TALK_NEW}“.")


def delete_entry(name):
    """Callback: Eintrag samt lokal gesicherter Ergebnisse aus dem Verlauf entfernen (beim Anbieter bleibt alles)."""
    def change(jobs):
        jobs[:] = [x for x in jobs if x["name"] != name]
    mutate_jobs(runtime().lock, change)
    for prefix in ("res:", "err:", "errn:"):
        st.session_state.pop(prefix + name, None)
    st.session_state.pop("sel", None)


# ---------- UI ----------
moved = prepare_folders()
if moved:
    print("Dateien einsortiert:", ", ".join(moved))
    st.toast("Dateien einsortiert: " + ", ".join(moved))
with st.sidebar:
    provider = st.radio("Anbieter für neue Anfragen", PROVIDERS, key="provider", horizontal=True,
                        help="Jeder Eintrag im Tab „Jobs und Ergebnisse“ gehört zu seinem Anbieter und wird mit "
                             "dessen API-Key abgefragt. Beide Keys dürfen deshalb eingetragen bleiben.")
    keys = {PROVIDER_GEMINI: st.text_input("Gemini API-Key", os.getenv("GEMINI_API_KEY", ""), type="password"),
            PROVIDER_OPENAI: st.text_input("OpenAI API-Key", os.getenv("OPENAI_API_KEY", ""), type="password")}
    if provider == PROVIDER_OPENAI:
        st.session_state.setdefault("model_openai", DEFAULT_OPENAI_MODEL)
        model = st.text_input("Modell", key="model_openai",
                              help="Modellname laut OpenAI, zum Beispiel gpt-6-astra, gpt-6.1-sol oder gpt-6-luna. "
                                   "Das Modell muss die Batch API und PDF-Eingaben unterstützen.")
    else:
        st.session_state.setdefault("model_gemini", DEFAULT_MODEL)
        model = st.text_input("Modell", key="model_gemini")
if not keys[provider]:
    env_name = "OPENAI_API_KEY" if provider == PROVIDER_OPENAI else "GEMINI_API_KEY"
    st.info(f"Trage links deinen {provider} API-Key ein oder setze die Umgebungsvariable {env_name}.")
    st.stop()


def client_for(p):
    """Client des Anbieters p, oder None, wenn dessen API-Key fehlt."""
    return make_client(p, keys[p]) if keys[p] else None


try:
    client = client_for(provider)
except RuntimeError as e:  # z. B. das Paket openai fehlt
    st.error(f"Der {provider}-Client lässt sich nicht erstellen: {e}")
    st.stop()
rt = runtime()

try:  # ab Streamlit 1.55 weiß die App, welcher Tab offen ist
    tab_new, tab_jobs = st.tabs(TABS, key="view", on_change="rerun")
except TypeError:
    tab_new, tab_jobs = st.tabs(TABS)
on_jobs_tab = getattr(tab_jobs, "open", None) is not False  # ältere Versionen: immer prüfen

with tab_new:
    note = st.session_state.pop("reused_note", None)
    if note:
        st.info(note)
    if provider == PROVIDER_OPENAI:
        mode = MODE_BATCH
        st.caption("Mit OpenAI läuft nur der Batch-Modus (50 % Rabatt, Ergebnis innerhalb von 24 Stunden). "
                   "Der synchrone Modus ist Gemini vorbehalten.")
    else:
        mode = st.radio("Modus", [MODE_BATCH, MODE_SYNC], horizontal=True, key="mode")
    sync = mode == MODE_SYNC
    uploads = st.file_uploader("PDF-Dateien (mehrere möglich)", type="pdf", accept_multiple_files=True)
    with st.container(border=True):
        st.markdown("**PDFs aus Google Drive hinzufügen** (optional)")
        if st.button("Meine PDFs aus Drive laden"):
            try:
                res = drive_service().files().list(
                    q="mimeType='application/pdf' and trashed=false", orderBy="modifiedTime desc",
                    pageSize=50, fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True,
                ).execute()
                st.session_state["drive_pdfs"] = {f"{f['name']} [{f['id'][:6]}]": f["id"] for f in res["files"]}
            except Exception as e:
                st.error(f"Drive-Zugriff fehlgeschlagen: {e}. Falls der Login hängt, conf/token.json löschen.")
        drive_list = st.session_state.get("drive_pdfs", {})
        chosen = st.multiselect("Zuletzt geänderte PDFs", list(drive_list), placeholder="PDFs auswählen") if drive_list else []
        links = st.text_area("Oder Drive-Links / Datei-IDs einfügen (eine pro Zeile)", height=80)
    pasted = [drive_id(x) for x in links.splitlines() if x.strip()]
    drive_refs = [drive_list[c] for c in chosen] + pasted
    pdf_order = [f.name for f in uploads] + chosen + [f"Drive-Datei {ref[:6]}" for ref in pasted]
    if pdf_order:
        st.caption("Reihenfolge der PDFs in jeder Anfrage: "
                   + ", ".join(f"{k}. {n}" for k, n in enumerate(pdf_order, 1))
                   + " (hochgeladene Dateien zuerst, dann Drive). Gleiche Dateien werden nur einmal gesendet.")
    else:
        st.caption("Kein PDF angehängt: Die Prompts werden ohne PDF gesendet.")

    system = st.text_area("Systemanweisung (optional)", height=80, key="system")
    raw = st.text_area("Prompts (mehrere mit einer Zeile --- trennen)", height=260, key="raw")
    prompts = [p.strip() for p in SEPARATOR.split(raw) if p.strip()]
    talks = [parse_conversation(p) for p in prompts]  # Prompts, die ein früheres Gespräch fortsetzen
    unfinished = [k for k, (turns, own) in enumerate(talks, 1) if turns and not own]
    if any(turns for turns, _ in talks):
        st.caption(f"{sum(1 for turns, _ in talks if turns)} von {len(prompts)} Prompt(s) setzen ein früheres Gespräch "
                   "fort. Der Verlauf steht im Prompt und zählt bei jeder Anfrage als Eingabe.")
    if unfinished:
        st.warning(f"Unter „{TALK_NEW}“ fehlt noch dein neuer Prompt (Prompt {', '.join(map(str, unfinished))}). "
                   "Der Start ist gesperrt, bis dort Text steht.")
    group = 1
    if sync:
        gcol, _ = st.columns([1, 3])
        group = int(gcol.number_input(
            "Prompts pro Gespräch", min_value=1, max_value=50, step=1, key="group",
            help="1: Jeder Prompt läuft für sich. 2: Je zwei Prompts bilden ein Gespräch (der zweite sieht den ersten "
                 "und dessen Antwort), die nächsten zwei ein neues Gespräch usw. Die PDFs gehen in jedem Gespräch mit "
                 "dem ersten Prompt raus."))
    sdk_ok = sdk_has_long_decode()
    if sync and not sdk_ok:
        st.warning(f"Dein google-genai ({genai.__version__}) ist älter als 2.30.1, daher ist die Ausgabe auf "
                   f"{BATCH_MAX_OUT} Token begrenzt. Aktualisieren: pip install -U google-genai")
    c1, c2, c3 = st.columns(3)
    if provider == PROVIDER_OPENAI:
        st.session_state.setdefault("effort", "Standard")
        think = c1.selectbox("Denktiefe", OPENAI_EFFORTS, key="effort",
                             help="reasoning.effort der Responses API. „Standard“ sendet nichts, dann gilt die "
                                  "Voreinstellung des Modells. Welche Stufen ein Modell kennt, hängt vom Modell ab "
                                  "(z. B. kennt gpt-6-astra kein „none“). Andere Stufen lehnt die API ab, "
                                  "der Fehler steht dann im Ergebnis.")
    else:
        st.session_state.setdefault("think", "high")
        think = c1.selectbox("Denktiefe", THINK_LEVELS, key="think")
    if provider == PROVIDER_OPENAI:
        st.session_state.setdefault("max_openai", 32768)
        max_out = c2.number_input("Max. Ausgabe-Token", min_value=256, max_value=OPENAI_MAX_OUT, step=1024,
                                  key="max_openai",
                                  help="max_output_tokens. Denk-Token zählen zum Limit. Das Maximum hängt vom "
                                       "Modell ab (bei den aktuellen Modellen bis 128.000).")
    elif sync:
        cap = SYNC_MAX_OUT if sdk_ok else BATCH_MAX_OUT
        st.session_state.setdefault("max_sync", min(65536, cap))
        max_out = c2.number_input("Max. Ausgabe-Token", min_value=256, max_value=cap, step=1024, key="max_sync",
                                  help="Über 32768 setzt das SDK automatisch Long Decode ein "
                                       "(mehrere Teilanfragen, eine zusammengesetzte Antwort).")
    else:
        st.session_state.setdefault("max_batch", BATCH_MAX_OUT)
        max_out = c2.number_input("Max. Ausgabe-Token", min_value=256, max_value=BATCH_MAX_OUT, step=1024, key="max_batch",
                                  help="Im Batch-Betrieb begrenzt die API die Ausgabe auf 32768 Token.")
    kind = "openai" if provider == PROVIDER_OPENAI else "sync" if sync else "batch"
    label = c3.text_input("Name", f"{kind}-{datetime.now():%Y%m%d-%H%M}", key="label_" + kind)
    talk_info = ""
    if sync and group > 1 and prompts:
        n_conv, rest = -(-len(prompts) // group), len(prompts) % group
        if n_conv == 1:
            split = f"Alle {len(prompts)} Prompts bilden ein Gespräch"
        else:
            split = f"Sie bilden {n_conv} Gespräche mit je {group} Prompts" + (f" (das letzte mit {rest})" if rest else "")
        talk_info = (f" {split}. Jedes Gespräch beginnt mit den PDFs, innerhalb eines Gesprächs wird bei jedem Prompt "
                     "der ganze Verlauf erneut gesendet. Sein Anfang (PDFs und erster Prompt) kann gecacht werden, "
                     "neue Prompts und Antworten werden normal berechnet.")
    if sync:
        st.caption(
            f"{len(prompts)} Prompt(s) erkannt. Die Prompts laufen nacheinander im Hintergrund, auch wenn du den Tab "
            "wechselst. Die erste Anfrage füllt den impliziten Cache (nur implizit möglich, ab 4.096 Token, ohne "
            "Rabattgarantie), die folgenden können davon profitieren. Die PDFs stehen in jeder Anfrage an erster Stelle. "
            "Es gilt der Standardpreis statt des Batch-Rabatts. Cache-Treffer siehst du unter „Jobs und Ergebnisse“."
            + talk_info
        )
    elif provider == PROVIDER_OPENAI:
        st.caption(
            f"{len(prompts)} Prompt(s) erkannt. Die OpenAI Batch API kostet 50 % des Standardpreises und liefert die "
            "Ergebnisse innerhalb von 24 Stunden. Die PDFs stehen in jeder Anfrage an erster Stelle, damit der "
            "Prefix für das automatische Caching (ab 1.024 Token) identisch ist. Treffer siehst du unter "
            "„Jobs und Ergebnisse“."
        )
    else:
        st.caption(
            f"{len(prompts)} Prompt(s) erkannt. Batch kostet 50 % des Standardpreises. Argon unterstützt nur "
            "implizites Caching (ab 4.096 Token, ohne Rabattgarantie): Die PDFs stehen in jeder Anfrage an erster "
            "Stelle, damit der Prefix identisch ist. Treffer siehst du unter „Jobs und Ergebnisse“."
        )

    if st.button("Synchron starten" if sync else "Batch starten", type="primary",
                 disabled=not prompts or bool(unfinished)):
        try:
            with st.status("Bereite Anfragen vor …" + HINT) as status:
                files = [(f.name, f.getvalue()) for f in uploads]
                if drive_refs:
                    status.update(label="Lade PDFs aus Google Drive …" + HINT)
                    files += [drive_fetch(ref) for ref in drive_refs]
                unique, seen = [], set()
                for fname, data in files:  # gleiche Dateien nur einmal senden
                    digest = hashlib.sha256(data).hexdigest()
                    if digest not in seen:
                        seen.add(digest)
                        unique.append((fname, data))
                names, pdfs = [n for n, _ in unique], []
                for k, (fname, data) in enumerate(unique, 1):
                    status.update(label=f"Lade PDF {k} von {len(unique)} zu {provider} hoch …" + HINT)
                    pdfs.append((upload_pdf_openai if provider == PROVIDER_OPENAI else upload_pdf)(client, fname, data))
                status.update(label="Starte …" + HINT)
                if sync:
                    entry = start_sync(client, rt, model, pdfs, names, prompts, system.strip(), think, max_out, label, group)
                elif provider == PROVIDER_OPENAI:
                    batch_id, keyed = start_openai_batch(client, model, [f.id for f in pdfs], prompts, system.strip(),
                                                         think, max_out, label)
                    entry = {"name": batch_id, "provider": PROVIDER_OPENAI, "label": label, "model": model,
                             "pdf": ", ".join(names) or None, "pdfs": names, "prompts": keyed, "system": system.strip(),
                             "think": think, "max_out": int(max_out),
                             "created": datetime.now().isoformat(timespec="seconds")}
                    mutate_jobs(rt.lock, lambda jobs: jobs.insert(0, entry))
                else:
                    job, keyed = start_batch(client, model, pdfs, prompts, system.strip(), think, max_out, label)
                    entry = {"name": job.name, "label": label, "model": model, "pdf": ", ".join(names) or None,
                             "pdfs": names, "prompts": keyed, "system": system.strip(), "think": think,
                             "max_out": int(max_out), "created": datetime.now().isoformat(timespec="seconds")}
                    mutate_jobs(rt.lock, lambda jobs: jobs.insert(0, entry))
                status.update(label="Gestartet", state="complete")
            st.session_state["sel"] = entry_label(entry)  # der neue Eintrag ist im Jobs-Tab vorausgewählt
            if sync:
                st.success(f"{entry['name']} läuft im Hintergrund. Fortschritt und Ergebnisse siehst du im Tab "
                           "„Jobs und Ergebnisse“.")
            else:
                st.success(f"{entry['name']} läuft. Im Tab „Jobs und Ergebnisse“ wird der Status alle "
                           f"{POLL_SECONDS} Sekunden geprüft.")
        except Exception as e:
            st.error(f"Start fehlgeschlagen: {e}")

with tab_jobs:
    jobs = load_jobs()
    if not jobs:
        st.info("Noch nichts gestartet.")
    else:
        opts = {entry_label(x): x for x in jobs}
        if st.session_state.get("sel") not in opts:
            st.session_state.pop("sel", None)
        j = opts[st.selectbox("Eintrag", list(opts), key="sel")]
        is_sync = j.get("mode") == "sync"
        grp, entry_prov = entry_group(j), entry_provider(j)
        kind_txt = ((f"synchron, {grp} Prompts pro Gespräch" if grp > 1 else "synchron") if is_sync
                    else "Batch (OpenAI)" if entry_prov == PROVIDER_OPENAI else "Batch")
        st.caption(f"{j['name']}, {kind_txt}, gesendet: Modell {j['model']}, "
                   f"Denktiefe {j.get('think', 'unbekannt')}, max. {j.get('max_out', '?')} Ausgabe-Token, "
                   f"PDF: {j['pdf'] or 'keins'}, {len(j['prompts'])} Prompt(s)")
        if is_sync:
            running = sync_view(rt, j["name"])[1] == "RUNNING"
        else:
            info = batch_info(j)
            failed = st.session_state.get("errn:" + j["name"], 0) >= MAX_POLL_ERRORS
            running = not ((info and info["state"] in DONE) or failed)
        a1, a2, _ = st.columns([1, 1, 3])
        a1.button("Eingaben übernehmen", on_click=reuse_inputs, args=(j,), key="reuse:" + j["name"],
                  help="Übernimmt Prompts und Einstellungen ins Formular „Neue Anfrage“.")
        with a2.popover("Eintrag löschen", disabled=running):
            st.write("Entfernt den Eintrag samt lokal gesicherter Ergebnisse aus dem Verlauf. "
                     "Beim Anbieter bleibt alles unverändert.")
            st.button("Ja, endgültig löschen", on_click=delete_entry, args=(j["name"],), key="del:" + j["name"])
        job_client = client if entry_prov == provider else client_for(entry_prov)  # jeder Eintrag mit seinem Anbieter
        polling = on_jobs_tab and running and (is_sync or job_client is not None)
        st.fragment(run_every=POLL_SECONDS if polling else None)(job_panel)(job_client, rt, j, polling)
