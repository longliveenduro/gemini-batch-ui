"""Lokale Batch- und Synchron-UI für Gemini 4 Argon (Streamlit).

Start:    pip install -U -r requirements.txt && streamlit run gemini_batch_ui.py
API-Key:  Umgebungsvariable GEMINI_API_KEY oder Seitenleiste.
Modi:     Batch (asynchron, 50 % Rabatt) oder Synchron (nacheinander, impliziter Cache über alle Prompts,
          optional in Gesprächen: je N Prompts bilden einen gemeinsamen Kontext, das nächste Gespräch beginnt neu).
Drive:    (optional) In der Google Cloud Console die Drive API aktivieren, eine OAuth-Client-ID
          vom Typ „Desktop-App“ erstellen und als credentials.json neben dieses Skript legen.
          Beim ersten Zugriff öffnet sich der Browser zur Freigabe (nur Lesezugriff).
"""
import hashlib
import io
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import streamlit as st
from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-4-argon"
HISTORY = Path("batch_jobs.json")
FILES_DB = Path("pdf_files.json")  # PDF-Hash -> Name der Datei in der Gemini Files API
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
POLL_SECONDS = 10  # Abstand der automatischen Prüfung im Tab „Jobs und Ergebnisse“
DONE = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}
BATCH_MAX_OUT = 32768      # Batch-Anfragen unterstützen kein Long Decode
SYNC_MAX_OUT = 1_048_576   # synchron: Long Decode über das SDK (ab google-genai 2.30.1)
SYNC_TIMEOUT_MS = 25 * 60 * 1000  # Argon-Aufrufe können lange laufen (Doku: mindestens 20 Minuten)

st.set_page_config(page_title="Gemini Argon", layout="wide")


# ---------- Verlauf (Batches laufen bis zu 24 h, synchrone Läufe im Hintergrund: daher lokal gespeichert) ----------
@st.cache_resource(show_spinner=False)
def runtime():
    """Prozessweit geteilt: Sperre für den Verlauf, laufende Threads, Abbruchwünsche."""
    return SimpleNamespace(lock=threading.Lock(), threads={}, cancel=set())


def load_jobs():
    return json.loads(HISTORY.read_text("utf-8")) if HISTORY.exists() else []


def mutate_jobs(lock, change):
    """Verlauf unter Sperre lesen, mit change(jobs) ändern und atomar zurückschreiben (auch aus Threads nutzbar)."""
    with lock:
        jobs = load_jobs()
        change(jobs)
        tmp = HISTORY.with_suffix(".tmp")
        tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, HISTORY)


# ---------- Google Drive ----------
def drive_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if Path("token.json").exists():
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path("credentials.json").exists():
                raise FileNotFoundError("credentials.json fehlt (Anleitung oben im Skript).")
            creds = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES).run_local_server(port=0)
        Path("token.json").write_text(creds.to_json())
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
    path = Path(f"requests_{int(time.time())}.jsonl")
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


def parse_results(raw, prompts):
    rows = [parse_line(json.loads(line), prompts) for line in raw.decode("utf-8").splitlines() if line.strip()]
    num = lambda r: int(r["key"].rsplit("-", 1)[-1]) if r["key"].rsplit("-", 1)[-1].isdigit() else 0
    return sorted(rows, key=num)


def refresh(client, j):
    job = client.batches.get(name=j["name"])
    state, rows = job.state.name, None
    if state == "JOB_STATE_SUCCEEDED" and job.dest and job.dest.file_name:
        rows = parse_results(client.files.download(file=job.dest.file_name), j["prompts"])
    st.session_state["res:" + j["name"]] = {
        "state": state, "rows": rows, "error": str(job.error) if job.error else None, "model": job.model,
    }


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
            reply = None
            try:
                resp = ask(client, model, history + [user], cfg)
                line = {"key": key, "response": resp.model_dump(mode="json", by_alias=True, exclude_none=True)}
                reply = resp.candidates[0].content if resp.candidates else None
            except Exception as e:
                line = {"key": key, "error": str(e)}
            add_row(parse_line(line, prompts))
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
             "prompts": keyed, "think": think, "max_out": int(max_out), "group": int(group), "rows": [],
             "created": datetime.now().isoformat(timespec="seconds")}
    mutate_jobs(rt.lock, lambda jobs: jobs.insert(0, entry))
    thread = threading.Thread(
        target=run_sync, daemon=True,
        args=(client, rt, name, model, [f.uri for f in pdfs], keyed, system, think, max_out, group),
    )
    rt.threads[name] = thread
    thread.start()
    return name


def sync_view(rt, name):
    """Eintrag und Zustand eines synchronen Laufs. RUNNING gilt nur, solange der Thread lebt."""
    thread = rt.threads.get(name)
    alive = bool(thread and thread.is_alive())  # erst den Thread prüfen, dann die Datei lesen
    j = next((x for x in load_jobs() if x["name"] == name), None)
    state = j.get("state", "RUNNING") if j else None
    if state == "RUNNING" and not alive:
        state = "INTERRUPTED"
    return j, state


# ---------- Anzeige (läuft als Fragment, siehe Tab „Jobs und Ergebnisse“) ----------
def show_rows(j, rows):
    if not rows:
        return
    tin, tc = sum(r["in"] for r in rows), sum(r["cached"] for r in rows)
    st.caption(f"Eingabe-Token gesamt: {tin:,}, davon aus dem Cache: {tc:,}" + (f" ({tc / tin:.0%})" if tin else ""))
    md = "\n\n".join(f"## {r['key']}\n\n**Prompt:** {r['prompt']}\n\n{r['text'] or r['error']}" for r in rows)
    st.download_button("Ergebnisse als Markdown", md, file_name=f"{j['label']}.md", key="dl:" + j["name"])
    for r in rows:
        with st.expander(f"{r['key']}: {r['prompt'][:90]}", expanded=len(rows) <= 3):
            if r["text"]:
                st.markdown(r["text"])
            else:
                st.error(str(r["error"]))
            if r.get("finish") == "MAX_TOKENS":
                st.warning("Abgeschnitten: Denk- und Ausgabe-Token haben das Limit erreicht.")
            st.caption(f"Modell {r.get('model', '?')}, Ende: {r.get('finish', '?')}, "
                       f"Eingabe {r['in']:,} (Cache {r['cached']:,}), "
                       f"Denken {r['think']:,}, Ausgabe {r['out']:,} Token")
            if r.get("usage"):
                st.json(r["usage"], expanded=False)


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
    if j.get("mode") == "sync":
        return sync_panel(rt, j["name"], polling)
    name = j["name"]
    b1, b2, _ = st.columns([1, 1, 3])
    manual = b1.button("Status prüfen / Ergebnisse laden", key="chk:" + name)
    if b2.button("Batch abbrechen", key="cx:" + name):
        try:
            client.batches.cancel(name=name)
            st.warning("Abbruch angefordert. Der Status wird gleich aktualisiert.")
        except Exception as e:
            st.error(f"Abbruch fehlgeschlagen: {e}")
    if manual or polling:
        try:
            refresh(client, j)
        except Exception as e:
            st.error(f"Abruf fehlgeschlagen: {e}")
    info = st.session_state.get("res:" + name)
    if polling and info and info["state"] in DONE:
        st.rerun()  # Batch fertig: ein kompletter Rerun beendet die automatische Prüfung
    if polling:
        st.caption(f"Automatische Prüfung alle {POLL_SECONDS} Sekunden, zuletzt um {datetime.now():%H:%M:%S}.")
    if not info:
        return
    st.write(f"Status: **{info['state']}**, Modell laut API: **{info.get('model') or 'unbekannt'}**")
    if info["error"]:
        st.error(info["error"])
    show_rows(j, info["rows"] or [])


# ---------- UI ----------
with st.sidebar:
    api_key = st.text_input("Gemini API-Key", os.getenv("GEMINI_API_KEY", ""), type="password")
    model = st.text_input("Modell", DEFAULT_MODEL)
if not api_key:
    st.info("Trage links deinen API-Key ein oder setze die Umgebungsvariable GEMINI_API_KEY.")
    st.stop()
client = genai.Client(api_key=api_key)
rt = runtime()

TABS = ["Neue Anfrage", "Jobs und Ergebnisse"]
try:  # ab Streamlit 1.55 weiß die App, welcher Tab offen ist
    tab_new, tab_jobs = st.tabs(TABS, key="view", on_change="rerun")
except TypeError:
    tab_new, tab_jobs = st.tabs(TABS)
on_jobs_tab = getattr(tab_jobs, "open", None) is not False  # ältere Versionen: immer prüfen

with tab_new:
    mode = st.radio("Modus", ["Batch (50 % Rabatt, Ergebnis nach Minuten bis Stunden)",
                              "Synchron (nacheinander, Cache-Treffer möglich)"], horizontal=True)
    sync = mode.startswith("Synchron")
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
                st.error(f"Drive-Zugriff fehlgeschlagen: {e}. Bei abgelaufenem Login token.json löschen.")
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

    system = st.text_area("Systemanweisung (optional)", height=80)
    raw = st.text_area("Prompts (mehrere mit einer Zeile --- trennen)", height=260)
    prompts = [p.strip() for p in re.split(r"^\s*---\s*$", raw, flags=re.M) if p.strip()]
    group = 1
    if sync:
        gcol, _ = st.columns([1, 3])
        group = int(gcol.number_input(
            "Prompts pro Gespräch", 1, 50, 1, step=1, key="group",
            help="1: Jeder Prompt läuft für sich. 2: Je zwei Prompts bilden ein Gespräch (der zweite sieht den ersten "
                 "und dessen Antwort), die nächsten zwei ein neues Gespräch usw. Die PDFs gehen in jedem Gespräch mit "
                 "dem ersten Prompt raus."))
    sdk_ok = sdk_has_long_decode()
    if sync and not sdk_ok:
        st.warning(f"Dein google-genai ({genai.__version__}) ist älter als 2.30.1, daher ist die Ausgabe auf "
                   f"{BATCH_MAX_OUT} Token begrenzt. Aktualisieren: pip install -U google-genai")
    c1, c2, c3 = st.columns(3)
    think = c1.selectbox("Denktiefe", ["Standard", "low", "medium", "high"], index=3)
    if sync:
        cap = SYNC_MAX_OUT if sdk_ok else BATCH_MAX_OUT
        max_out = c2.number_input("Max. Ausgabe-Token", 256, cap, min(65536, cap), step=1024, key="max_sync",
                                  help="Über 32768 setzt das SDK automatisch Long Decode ein "
                                       "(mehrere Teilanfragen, eine zusammengesetzte Antwort).")
    else:
        max_out = c2.number_input("Max. Ausgabe-Token", 256, BATCH_MAX_OUT, BATCH_MAX_OUT, step=1024, key="max_batch",
                                  help="Im Batch-Betrieb begrenzt die API die Ausgabe auf 32768 Token.")
    kind = "sync" if sync else "batch"
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
    else:
        st.caption(
            f"{len(prompts)} Prompt(s) erkannt. Batch kostet 50 % des Standardpreises. Argon unterstützt nur "
            "implizites Caching (ab 4.096 Token, ohne Rabattgarantie): Die PDFs stehen in jeder Anfrage an erster "
            "Stelle, damit der Prefix identisch ist. Treffer siehst du unter „Jobs und Ergebnisse“."
        )

    if st.button("Synchron starten" if sync else "Batch starten", type="primary", disabled=not prompts):
        try:
            with st.status("Bereite Anfragen vor …") as status:
                files = [(f.name, f.getvalue()) for f in uploads]
                if drive_refs:
                    status.update(label="Lade PDFs aus Google Drive …")
                    files += [drive_fetch(ref) for ref in drive_refs]
                unique, seen = [], set()
                for fname, data in files:  # gleiche Dateien nur einmal senden
                    digest = hashlib.sha256(data).hexdigest()
                    if digest not in seen:
                        seen.add(digest)
                        unique.append((fname, data))
                names, pdfs = [n for n, _ in unique], []
                for k, (fname, data) in enumerate(unique, 1):
                    status.update(label=f"Lade PDF {k} von {len(unique)} zu Gemini hoch …")
                    pdfs.append(upload_pdf(client, fname, data))
                status.update(label="Starte …")
                if sync:
                    job_name = start_sync(client, rt, model, pdfs, names, prompts, system.strip(), think, max_out, label, group)
                else:
                    job, keyed = start_batch(client, model, pdfs, prompts, system.strip(), think, max_out, label)
                    job_name = job.name
                    entry = {"name": job_name, "label": label, "model": model, "pdf": ", ".join(names) or None,
                             "pdfs": names, "prompts": keyed, "think": think, "max_out": int(max_out),
                             "created": datetime.now().isoformat(timespec="seconds")}
                    mutate_jobs(rt.lock, lambda jobs: jobs.insert(0, entry))
                status.update(label="Gestartet", state="complete")
            if sync:
                st.success(f"{job_name} läuft im Hintergrund. Fortschritt und Ergebnisse siehst du im Tab "
                           "„Jobs und Ergebnisse“.")
            else:
                st.success(f"{job_name} läuft. Im Tab „Jobs und Ergebnisse“ wird der Status alle "
                           f"{POLL_SECONDS} Sekunden geprüft.")
        except Exception as e:
            st.error(f"Start fehlgeschlagen: {e}")

with tab_jobs:
    jobs = load_jobs()
    if not jobs:
        st.info("Noch nichts gestartet.")
    else:
        opts = {f"{x['label']} ({x['created'][:16].replace('T', ' ')}) {x['name'][-6:]}": x for x in jobs}
        j = opts[st.selectbox("Eintrag", list(opts))]
        is_sync = j.get("mode") == "sync"
        grp = j.get("group") or (len(j["prompts"]) if j.get("chat") else 1)
        kind_txt = (f"synchron, {grp} Prompts pro Gespräch" if grp > 1 else "synchron") if is_sync else "Batch"
        st.caption(f"{j['name']}, {kind_txt}, gesendet: Modell {j['model']}, "
                   f"Denktiefe {j.get('think', 'unbekannt')}, max. {j.get('max_out', '?')} Ausgabe-Token, "
                   f"PDF: {j['pdf'] or 'keins'}, {len(j['prompts'])} Prompt(s)")
        if is_sync:
            running = sync_view(rt, j["name"])[1] == "RUNNING"
        else:
            info = st.session_state.get("res:" + j["name"])
            running = not (info and info["state"] in DONE)
        polling = on_jobs_tab and running
        st.fragment(run_every=POLL_SECONDS if polling else None)(job_panel)(client, rt, j, polling)
