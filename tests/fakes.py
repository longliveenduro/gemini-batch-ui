"""Ersatz für die Google- und OpenAI-Dienste. Nichts hier ruft das Netzwerk auf.

Die Antworten sind echte SDK-Objekte (types.GenerateContentResponse, openai.types.Batch), damit der Code im Test
dieselben Datenformen sieht wie in der Praxis. Aufrufe werden aufgezeichnet, damit Tests prüfen können, was die App
gesendet hat.
"""
import json
import time as real_time
from pathlib import Path
from types import SimpleNamespace

from google.genai import types
from openai.types import Batch, FileObject


class ApiError(Exception):
    """Ähnelt google.genai.errors.APIError: trägt einen HTTP-Code im Attribut code."""

    def __init__(self, code, message="Fehler"):
        super().__init__(f"{code} {message}")
        self.code = code


def make_response(text="Antwort", finish="STOP", prompt_tokens=100, cached=0, thoughts=10, out=5,
                  model="gemini-4-carbon", empty=False):
    """Echtes SDK-Antwortobjekt. empty=True liefert eine Antwort ohne Kandidaten (z. B. blockiert)."""
    candidates = None if empty else [types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]), finish_reason=finish)]
    return types.GenerateContentResponse(
        candidates=candidates, model_version=model,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens, cached_content_token_count=cached,
            candidates_token_count=out, thoughts_token_count=thoughts))


def shape(contents):
    """Anfrageinhalt als einfache Tupel [(rolle, [teile])]. PDFs erscheinen als 'PDF:<id>'."""
    return [(c.role, ["PDF:" + p.file_data.file_uri.rsplit("/", 1)[1] if p.file_data else p.text for p in c.parts])
            for c in contents]


def _state(name):
    return SimpleNamespace(name=name) if name else None


class _Files:
    def __init__(self, parent):
        self.p = parent

    def upload(self, file, config):
        p = self.p
        if hasattr(file, "read"):  # PDF-Bytes
            p.uploads.append(file.read())
            n = len(p.uploads)
            return SimpleNamespace(name=f"files/pdf{n}", uri=f"https://example.test/v1beta/files/pdf{n}",
                                   state=_state(p.upload_state), mime_type=config.mime_type)
        path = Path(file)  # JSONL-Datei eines Batches
        p.jsonl_uploads.append(path)
        p.jsonl_configs.append(config)
        p.batch_requests = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
        return SimpleNamespace(name="files/jsonl1", state=None)

    def get(self, name):
        state = self.p.get_states.pop(0) if self.p.get_states else None
        if isinstance(state, Exception):
            raise state
        return SimpleNamespace(name=name, uri=f"https://example.test/v1beta/{name}", state=_state(state))

    def download(self, file):
        p = self.p
        lines = []
        for req in p.batch_requests:
            key = req["key"]
            lines.append(json.dumps({"key": key, "response": {
                "candidates": [{"content": {"parts": [{"text": p.batch_text or f"Batch-Antwort {key}"}]},
                                "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 100, "cachedContentTokenCount": p.batch_cached,
                                  "thoughtsTokenCount": 10, "candidatesTokenCount": 5},
                "modelVersion": "gemini-4-carbon"}}))
        return ("\n".join(lines) + "\n").encode()


class _Batches:
    def __init__(self, parent):
        self.p = parent

    def create(self, model, src, config):
        if self.p.create_error:
            raise self.p.create_error
        self.p.created_batches.append((model, src, config))
        return SimpleNamespace(name=f"batches/fake{len(self.p.created_batches):06d}")

    def get(self, name):
        self.p.get_calls += 1
        if self.p.get_error:
            raise self.p.get_error
        return SimpleNamespace(state=SimpleNamespace(name=self.p.batch_state), error=None,
                               model="models/gemini-4-carbon", dest=SimpleNamespace(file_name="files/results"))

    def cancel(self, name):
        self.p.cancelled.append(name)


class _Models:
    def __init__(self, parent):
        self.p = parent

    def generate_content(self, model, contents, config):
        p = self.p
        n = len(p.calls)
        p.calls.append({"model": model, "contents": shape(contents), "raw": list(contents), "config": config})
        if p.on_call:
            p.on_call(n)
        if p.delay:
            real_time.sleep(p.delay)
        item = p.script[n] if n < len(p.script) else "ok"
        if isinstance(item, Exception):
            raise item
        if item == "empty":
            resp = make_response(empty=True)
        elif isinstance(item, types.GenerateContentResponse):
            resp = item
        elif isinstance(item, dict):
            resp = make_response(**item)
        else:
            resp = make_response(text=f"Antwort {n + 1}")
        p.responses.append(resp)
        return resp


class FakeGemini:
    """Ersatz für google.genai.Client.

    script: je Aufruf von generate_content ein Eintrag: "ok", "empty", eine Exception (wird ausgelöst),
            ein dict (Argumente für make_response) oder ein fertiges Antwortobjekt. Danach immer "ok".
    delay: Wartezeit in Sekunden pro generate_content (für Tests mit Abbruch).
    batch_state / get_error: Zustand bzw. Fehler von batches.get. create_error: Fehler von batches.create.
    batch_cached: Cache-Token in Batch-Ergebnissen.
    upload_state / get_states: Dateizustände der Files API (z. B. "PROCESSING", Exception für „abgelaufen“).
    on_call: Funktion f(index), die zu Beginn jedes generate_content-Aufrufs läuft.
    """

    def __init__(self, script=None, *, delay=0.0, batch_state="JOB_STATE_SUCCEEDED", get_error=None,
                 batch_cached=0, batch_text=None, upload_state=None, get_states=None, on_call=None,
                 create_error=None):
        self.script = list(script or [])
        self.delay = delay
        self.batch_state = batch_state
        self.get_error = get_error
        self.batch_cached = batch_cached
        self.batch_text = batch_text
        self.upload_state = upload_state
        self.get_states = list(get_states or [])
        self.on_call = on_call
        self.create_error = create_error
        self.client_kwargs = []  # Argumente der Client-Erzeugung (z. B. api_key)
        self.calls, self.responses = [], []
        self.uploads, self.jsonl_uploads, self.jsonl_configs = [], [], []
        self.batch_requests, self.created_batches, self.cancelled = [], [], []
        self.get_calls = 0
        self.files, self.batches, self.models = _Files(self), _Batches(self), _Models(self)


def install(monkeypatch, fake):
    """Lässt google.genai.Client immer den Fake liefern."""
    import google.genai as genai

    def build_client(*args, **kwargs):
        fake.client_kwargs.append(kwargs)
        return fake

    monkeypatch.setattr(genai, "Client", build_client)
    return fake


# ---------- Google Drive ----------
class _Req:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _DriveFiles:
    def __init__(self, drive):
        self.d = drive

    def list(self, **kwargs):
        return _Req({"files": [{"id": i, "name": n} for i, (n, mime, _) in self.d.files_by_id.items()
                               if mime == "application/pdf"]})

    def get(self, fileId, **kwargs):
        name, mime, _ = self.d.files_by_id[fileId]
        return _Req({"name": name, "mimeType": mime})

    def get_media(self, fileId, **kwargs):
        self.d.downloaded.append(fileId)
        return _Req(self.d.files_by_id[fileId][2])

    def export_media(self, fileId, mimeType):
        self.d.exported.append((fileId, mimeType))
        return _Req(self.d.files_by_id[fileId][2])


class FakeDrive:
    """Ersatz für den Drive-Dienst. files_by_id: {id: (name, mime_type, bytes)}."""

    def __init__(self, files_by_id):
        self.files_by_id = files_by_id
        self.downloaded, self.exported = [], []

    def files(self):
        return _DriveFiles(self)


def install_drive(monkeypatch, home, drive):
    """Ersetzt Anmeldung und Dienst, sodass kein Browser und kein Netzwerk gebraucht wird."""
    import google.oauth2.credentials as gc
    import googleapiclient.discovery as gd
    (home / "conf").mkdir(parents=True, exist_ok=True)
    (home / "conf" / "token.json").write_text("{}")
    monkeypatch.setattr(gc.Credentials, "from_authorized_user_file",
                        staticmethod(lambda path, scopes: SimpleNamespace(valid=True)))
    monkeypatch.setattr(gd, "build", lambda *args, **kwargs: drive)
    return drive


# ---------- OpenAI (Files API und Batch API) ----------
class OpenAIError(Exception):
    """Ähnelt openai.APIStatusError: trägt den HTTP-Code im Attribut status_code (code ist dort ein Text oder leer)."""

    def __init__(self, status_code, message="Fehler"):
        super().__init__(f"{status_code} {message}")
        self.status_code = status_code
        self.code = None


def make_openai_body(text="Antwort", *, status="completed", incomplete=None, input_tokens=100, cached=0, reasoning=10,
                     output=25, model="gpt-6-astra-2026-09-01", refusal=None, error=None):
    """Antwortrumpf einer Responses-Anfrage, wie ihn die Ausgabedatei der Batch API enthält. output zählt wie bei OpenAI
    die Denk-Token mit. Gültig nach dem Schema des SDK (openai.types.responses.Response), das prüft ein Test."""
    if refusal:
        content = [{"type": "refusal", "refusal": refusal}]
    else:
        content = [{"type": "output_text", "text": text, "annotations": []}] if text else []
    return {
        "id": "resp_fake", "object": "response", "created_at": 1790000000, "status": status, "model": model,
        "output": [{"id": "rs_fake", "type": "reasoning", "summary": []},
                   {"id": "msg_fake", "type": "message", "role": "assistant", "status": "completed", "content": content}],
        "usage": {"input_tokens": input_tokens,
                  "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
                  "output_tokens": output, "output_tokens_details": {"reasoning_tokens": reasoning},
                  "total_tokens": input_tokens + output},
        "error": error, "incomplete_details": {"reason": incomplete} if incomplete else None, "instructions": None,
        "metadata": {}, "parallel_tool_calls": True, "temperature": None, "tool_choice": "auto", "tools": [],
        "top_p": None,
    }


class _OpenAIFiles:
    def __init__(self, parent):
        self.p = parent

    def create(self, *, file, purpose, expires_after=None):
        p = self.p
        if p.upload_error:
            raise p.upload_error
        p.file_calls.append({"purpose": purpose, "expires_after": expires_after})
        if purpose == "batch":  # JSONL-Datei eines Batches (geöffnete Datei)
            p.jsonl_uploads.append(Path(file.name))
            p.batch_requests = [json.loads(line) for line in file.read().decode("utf-8").splitlines() if line.strip()]
            return FileObject(id="file-jsonl1", bytes=1, created_at=1790000000, filename=Path(file.name).name,
                              object="file", purpose="batch", status="processed")
        filename, stream, _mime = file  # PDF: (Name, Bytes-Strom, MIME-Typ)
        p.uploads.append(stream.read())
        p.upload_names.append(filename)
        obj = FileObject(id=f"file-pdf{len(p.uploads)}", bytes=len(p.uploads[-1]), created_at=1790000000,
                         filename=filename, object="file", purpose=purpose, status="processed")
        p.pdf_files[obj.id] = obj
        return obj

    def retrieve(self, file_id):
        p = self.p
        if file_id in p.gone or file_id not in p.pdf_files:
            raise OpenAIError(404, "No such file")
        return p.pdf_files[file_id].model_copy(update={"status": p.file_status.get(file_id, "processed")})

    def content(self, file_id):
        p = self.p
        p.content_calls.append(file_id)
        data = p.output_bytes() if file_id == "file-out1" else p.error_bytes()
        return SimpleNamespace(read=lambda: data)


class _OpenAIBatches:
    def __init__(self, parent):
        self.p = parent

    def create(self, *, input_file_id, endpoint, completion_window, metadata=None):
        p = self.p
        if p.create_error:
            raise p.create_error
        p.created_batches.append({"input_file_id": input_file_id, "endpoint": endpoint,
                                  "completion_window": completion_window, "metadata": metadata})
        return self.batch(f"batch_fake{len(p.created_batches):06d}", "validating")

    def retrieve(self, batch_id):
        self.p.get_calls += 1
        if self.p.get_error:
            raise self.p.get_error
        return self.batch(batch_id, self.p.batch_status)

    def cancel(self, batch_id):
        self.p.cancelled.append(batch_id)
        return self.batch(batch_id, "cancelling")

    def batch(self, batch_id, status):
        p = self.p
        ended = status in ("completed", "expired", "cancelled")
        keys = [r["custom_id"] for r in p.batch_requests]
        ok = [k for k in keys if k not in p.failing]
        return Batch.model_validate({
            "id": batch_id, "object": "batch", "endpoint": "/v1/responses", "completion_window": "24h",
            "created_at": 1790000000, "input_file_id": "file-jsonl1", "status": status,
            "output_file_id": "file-out1" if ended and ok else None,
            "error_file_id": "file-err1" if ended and p.failing else None,
            "errors": {"object": "list", "data": p.batch_errors} if p.batch_errors else None,
            "model": "gpt-6-astra-2026-09-01",
            "request_counts": {"total": len(keys), "completed": len(ok), "failed": len(keys) - len(ok)},
        })


class FakeOpenAI:
    """Ersatz für openai.OpenAI (Files API und Batch API).

    batch_status: Status, den batches.retrieve meldet (validating, in_progress, completed, expired, cancelled, failed).
    get_error, create_error, upload_error: Fehler von batches.retrieve, batches.create bzw. files.create.
    batch_text, batch_cached: Antworttext und Cache-Token in der Ausgabedatei.
    failing: {Schlüssel: Fehlerobjekt} der Anfragen, die statt in der Ausgabedatei in der Fehlerdatei stehen.
    bodies: {Schlüssel: Argumente für make_openai_body} für einzelne Antworten (z. B. unvollständige).
    batch_errors: Fehler des ganzen Batches (Liste von {code, message, line}), z. B. bei Status failed.
    gone, file_status: IDs hochgeladener PDFs, die bei OpenAI fehlen (404), und abweichende Dateistatus.
    """

    def __init__(self, *, batch_status="completed", get_error=None, create_error=None, upload_error=None,
                 batch_text=None, batch_cached=0, failing=None, bodies=None, batch_errors=None):
        self.batch_status = batch_status
        self.get_error, self.create_error, self.upload_error = get_error, create_error, upload_error
        self.batch_text, self.batch_cached = batch_text, batch_cached
        self.failing, self.bodies, self.batch_errors = dict(failing or {}), dict(bodies or {}), list(batch_errors or [])
        self.client_kwargs = []  # Argumente der Client-Erzeugung (z. B. api_key)
        self.uploads, self.upload_names, self.pdf_files = [], [], {}
        self.jsonl_uploads, self.batch_requests, self.file_calls = [], [], []
        self.created_batches, self.cancelled, self.content_calls = [], [], []
        self.gone, self.file_status = set(), {}
        self.get_calls = 0
        self.files, self.batches = _OpenAIFiles(self), _OpenAIBatches(self)

    def output_bytes(self):
        lines = []
        for req in self.batch_requests:
            key = req["custom_id"]
            if key in self.failing:
                continue
            args = {"text": self.batch_text or f"OpenAI-Antwort {key}", "cached": self.batch_cached,
                    **self.bodies.get(key, {})}
            lines.append(json.dumps({"id": f"batch_req_{key}", "custom_id": key, "error": None, "response": {
                "status_code": 200, "request_id": f"req_{key}", "body": make_openai_body(**args)}}))
        return ("\n".join(lines) + "\n").encode()

    def error_bytes(self):
        lines = [json.dumps({"id": f"batch_req_{key}", "custom_id": key, "response": None, "error": error})
                 for key, error in self.failing.items()]
        return ("\n".join(lines) + "\n").encode()


def install_openai(monkeypatch, fake):
    """Lässt openai.OpenAI immer den Fake liefern."""
    import openai

    def build_client(*args, **kwargs):
        fake.client_kwargs.append(kwargs)
        return fake

    monkeypatch.setattr(openai, "OpenAI", build_client)
    return fake
