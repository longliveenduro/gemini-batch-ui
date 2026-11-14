"""Hintergrund-Modus (nur Gemini): Jeder Prompt läuft als Interaktion mit background=true auf Googles Servern (Interactions
API). Nur so geht Long Decode über 65.536 Ausgabe-Token: Die Server setzen die Antwort in Schritten zu je 64K Token fort
(bis 1.048.576 Token minus Eingabe) und fügen sie zu einer Antwort zusammen. Die App muss dafür nicht laufen. Sie fragt wie
bei einem Batch nur den Stand ab. Die Batch API kennt kein Long Decode, die Interactions API keinen Batch-Rabatt: Es gilt
der Standardpreis. Kein Streamlit-Aufruf in diesem Modul."""
import time
import uuid
from datetime import datetime

from .config import BG_RUNNING
from .history import mutate_jobs
from .rows import as_dict, parse_interaction

RETRY_CODES = (429,)  # nur Ratenbegrenzung. Nach einem 5xx könnte die Interaktion schon angelegt sein: Ein zweiter
                      # Aufruf würde sie doppelt berechnen (das SDK wiederholt 5xx selbst, kurz und höchstens zweimal)


def http_code(e):
    """HTTP-Code eines Fehlers: Das Interactions-SDK meldet ihn als status_code, ältere Fehlerklassen als code."""
    return getattr(e, "status_code", None) or getattr(e, "code", None)


def interaction_request(model, pdf_uris, prompt, system, think, max_out):
    """Anfrage für client.interactions.create(). Die PDFs stehen ganz vorn, immer in derselben Reihenfolge (gleicher
    Prefix fürs Caching), dann der Prompt. background=True verlangt store=True (das ist auch der Standard)."""
    config = {"max_output_tokens": int(max_out)}
    if think != "Standard":
        config["thinking_level"] = think
    request = {
        "model": model,
        "input": [{"type": "document", "uri": uri, "mime_type": "application/pdf"} for uri in pdf_uris]
                 + [{"type": "text", "text": prompt}],
        "generation_config": config,
        "background": True,
        "store": True,
    }
    if system:
        request["system_instruction"] = system
    return request


def create_interaction(client, request):
    """Eine Hintergrund-Interaktion anlegen (kehrt sofort mit der ID zurück). Bei Ratenbegrenzung (429) bis zu zweimal
    mit 10 und 20 Sekunden Pause wiederholen, andere Fehler gehen sofort nach oben."""
    for attempt in range(3):
        try:
            return client.interactions.create(**request)
        except Exception as e:
            if attempt < 2 and http_code(e) in RETRY_CODES:
                time.sleep(10 * (attempt + 1))
                continue
            raise


def cancel_interactions(client, ids):
    """Interaktionen abbrechen. Fehler (z. B. bei schon beendeten) werden gesammelt zurückgegeben, nicht ausgelöst."""
    errors = []
    for interaction_id in ids:
        try:
            client.interactions.cancel(id=interaction_id)
        except Exception as e:
            errors.append(e)
    return errors


def start_background(client, rt, model, pdfs, pdf_names, prompts, system, think, max_out, label):
    """Jeden Prompt als Hintergrund-Interaktion starten (alle gleichzeitig) und den Eintrag im Verlauf speichern. Scheitert
    einer, werden die schon gestarteten wieder abgebrochen, damit nichts unbeobachtet Kosten verursacht."""
    keyed = {f"prompt-{i}": p for i, p in enumerate(prompts, 1)}
    uris = [f.uri for f in pdfs]
    started = {}
    try:
        for key, prompt in keyed.items():
            created = create_interaction(client, interaction_request(model, uris, prompt, system, think, max_out))
            started[key] = created.id
    except Exception as e:
        cancel_interactions(client, started.values())
        if not started:
            raise
        raise RuntimeError(f"Prompt {len(started) + 1} von {len(keyed)} ließ sich nicht starten ({e}). "
                           f"Die {len(started)} schon gestarteten Läufe wurden abgebrochen.") from e
    entry = {"name": f"bg-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}", "mode": "background",
             "label": label, "model": model, "pdf": ", ".join(pdf_names) or None, "pdfs": list(pdf_names),
             "prompts": keyed, "system": system, "think": think, "max_out": int(max_out), "interactions": started,
             "created": datetime.now().isoformat(timespec="seconds")}
    mutate_jobs(rt.lock, lambda jobs: jobs.insert(0, entry))
    return entry


def fetch_background(client, j, known=None):
    """Stand eines Hintergrund-Eintrags als (state, rows, error, api_model), wie bei den Batches. Je Prompt wird die
    Interaktion abgefragt. Zeilen aus known (frühere Abfrage) sind beendet und werden nicht erneut geladen. Zeilen
    erscheinen, sobald ihre Interaktion beendet ist. state ist in_progress, solange eine läuft, danach completed
    (einzelne Fehler stehen in den Zeilen), cancelled (alle abgebrochen) oder failed (alle fehlgeschlagen)."""
    rows, running = {r["key"]: r for r in known or []}, False
    for key, interaction_id in j["interactions"].items():
        if key in rows:
            continue
        try:
            d = as_dict(client.interactions.get(id=interaction_id))
        except Exception as e:
            if http_code(e) != 404:
                raise
            d = {"status": "failed", "errors": [{"message": "Die Interaktion gibt es bei Google nicht mehr "
                                                            "(gelöscht oder abgelaufen)."}]}
        if d.get("status") in BG_RUNNING:
            running = True
        else:
            rows[key] = parse_interaction(d, key, j["prompts"])
    ordered = [rows[k] for k in j["interactions"] if k in rows]
    api_model = next((r["model"] for r in ordered if r["model"] != "unbekannt"), None)
    finishes = {r["finish"] for r in ordered}
    if running:
        return "in_progress", ordered, None, api_model
    if finishes == {"CANCELLED"}:
        return "cancelled", ordered, None, api_model
    if finishes == {"FAILED"}:
        return "failed", ordered, ordered[0]["error"], api_model
    return "completed", ordered, None, api_model


def cancel_background(client, j):
    """Alle Läufe eines Eintrags abbrechen. Beendete lassen sich nicht abbrechen: Ihre Fehler zählen nur, wenn gar
    kein Abbruch gelang."""
    ids = list(j["interactions"].values())
    errors = cancel_interactions(client, ids)
    if errors and len(errors) == len(ids):
        raise errors[0]
