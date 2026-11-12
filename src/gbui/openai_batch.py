"""OpenAI (nur Batch, Endpunkt /v1/responses): PDF hochladen, Batch starten, Ergebnisse lesen."""
import hashlib
import io
import json
import time

from .config import DATA_DIR, DONE, OPENAI_ENDPOINT, OPENAI_FILE_TTL, OPENAI_FILES_DB
from .rows import parse_results, pick


def openai_client(api_key):
    """OpenAI-Client. Das Paket wird erst hier geladen: Wer nur Gemini nutzt, braucht es nicht."""
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("Das Paket openai fehlt. Installieren: pip install -r requirements.txt") from e
    return OpenAI(api_key=api_key)


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
