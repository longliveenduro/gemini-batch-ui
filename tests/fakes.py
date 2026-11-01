"""Ersatz für die Google-Dienste. Nichts hier ruft das Netzwerk auf.

Die Antworten sind echte SDK-Objekte (types.GenerateContentResponse), damit der Code im Test dieselben Datenformen
sieht wie in der Praxis. Aufrufe werden aufgezeichnet, damit Tests prüfen können, was die App gesendet hat.
"""
import json
import time as real_time
from pathlib import Path
from types import SimpleNamespace

from google.genai import types


class ApiError(Exception):
    """Ähnelt google.genai.errors.APIError: trägt einen HTTP-Code im Attribut code."""

    def __init__(self, code, message="Fehler"):
        super().__init__(f"{code} {message}")
        self.code = code


def make_response(text="Antwort", finish="STOP", prompt_tokens=100, cached=0, thoughts=10, out=5,
                  model="gemini-4-argon", empty=False):
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
                "modelVersion": "gemini-4-argon"}}))
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
                               model="models/gemini-4-argon", dest=SimpleNamespace(file_name="files/results"))

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
