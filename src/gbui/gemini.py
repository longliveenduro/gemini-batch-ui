"""Gemini: PDF in die Files API laden, Batch-Anfragen bauen und starten."""
import hashlib
import io
import json
import time

from google.genai import types

from .config import DATA_DIR, FILES_DB


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
