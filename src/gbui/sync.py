"""Synchroner Modus (nur Gemini): Hintergrund-Thread, kein Streamlit-Aufruf darin."""
import threading
import time
import uuid
from datetime import datetime

from google.genai import types

from .config import SYNC_TIMEOUT_MS
from .history import load_jobs, mutate_jobs
from .rows import parse_line


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
