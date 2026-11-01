"""Tests der reinen Logik ohne Oberfläche: Parser, Hilfsfunktionen, Dateiverwaltung, Anfrageaufbau."""
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import helpers
from fakes import ApiError, FakeGemini
from helpers import read_history, write_history


def result_line(key="prompt-1", text="Hallo", finish="STOP", usage=None, parts=None, **extra):
    """Eine Ergebniszeile, wie sie die Batch-API liefert."""
    if usage is None:
        usage = {"promptTokenCount": 100, "candidatesTokenCount": 5, "thoughtsTokenCount": 7}
    response = {"candidates": [{"content": {"parts": parts or [{"text": text}]}, "finishReason": finish}],
                "usageMetadata": usage, "modelVersion": "gemini-4-argon"}
    return {"key": key, "response": response, **extra}


def test_ui_texts_used_by_tests_match_the_app(app):
    assert app.MODE_BATCH == helpers.MODE_BATCH
    assert app.MODE_SYNC == helpers.MODE_SYNC
    assert app.TABS == [helpers.TAB_NEW, helpers.TAB_JOBS]


# ---------- parse_line / parse_results ----------
def test_parse_line_reads_text_tokens_and_model(app):
    row = app.parse_line(result_line(), {"prompt-1": "Frage"})
    assert (row["key"], row["prompt"], row["text"]) == ("prompt-1", "Frage", "Hallo")
    assert (row["in"], row["cached"], row["think"], row["out"]) == (100, 0, 7, 5)
    assert (row["model"], row["finish"], row["error"]) == ("gemini-4-argon", "STOP", None)
    assert row["usage"]["promptTokenCount"] == 100


@pytest.mark.parametrize("field", ["cachedContentTokenCount", "cached_content_token_count",
                                   "totalCachedTokens", "total_cached_tokens"])
def test_parse_line_accepts_all_known_cache_field_names(app, field):
    assert app.parse_line(result_line(usage={"promptTokenCount": 10, field: 7}), {})["cached"] == 7


def test_parse_line_accepts_snake_case_responses(app):
    response = {"candidates": [{"content": {"parts": [{"text": "A"}]}, "finish_reason": "STOP"}],
                "usage_metadata": {"prompt_token_count": 3, "candidates_token_count": 2, "thoughts_token_count": 1},
                "model_version": "m"}
    row = app.parse_line({"key": "prompt-1", "response": response}, {})
    assert (row["in"], row["out"], row["think"], row["model"], row["finish"]) == (3, 2, 1, "m", "STOP")


def test_parse_line_ignores_thought_parts(app):
    parts = [{"text": "Denkschritt", "thought": True}, {"text": "Antwort"}]
    assert app.parse_line(result_line(parts=parts), {})["text"] == "Antwort"


def test_parse_line_without_text_reports_the_finish_reason(app):
    row = app.parse_line(result_line(parts=[{}], finish="SAFETY"), {})
    assert row["text"] == ""
    assert "SAFETY" in row["error"]


def test_parse_line_passes_errors_through(app):
    obj = {"code": 500, "message": "kaputt"}
    assert app.parse_line({"key": "prompt-2", "error": obj}, {})["error"] == obj
    assert app.parse_line({"key": "prompt-2", "error": "Übersprungen: x"}, {})["error"] == "Übersprungen: x"


def test_parse_line_without_response_has_zero_tokens(app):
    row = app.parse_line({"key": "prompt-1", "error": "x"}, {})
    assert (row["in"], row["cached"], row["think"], row["out"]) == (0, 0, 0, 0)
    assert (row["text"], row["finish"]) == ("", "unbekannt")


def test_parse_line_unknown_key_has_empty_prompt(app):
    assert app.parse_line(result_line(key="prompt-9"), {"prompt-1": "x"})["prompt"] == ""


def test_parse_results_sorts_numerically_and_skips_blank_lines(app):
    raw = "\n".join(json.dumps(result_line(key=f"prompt-{i}")) for i in (10, 2, 1)) + "\n\n"
    assert [r["key"] for r in app.parse_results(raw.encode("utf-8"), {})] == ["prompt-1", "prompt-2", "prompt-10"]


def test_parse_results_puts_unnumbered_keys_first(app):
    raw = "\n".join(json.dumps(result_line(key=k)) for k in ("prompt-3", "sonderfall"))
    assert [r["key"] for r in app.parse_results(raw.encode("utf-8"), {})] == ["sonderfall", "prompt-3"]


def test_pick_skips_none_but_keeps_zero(app):
    assert app.pick({"a": None, "b": 0}, "a", "b", default=9) == 0
    assert app.pick({}, "a", default=5) == 5


# ---------- kleine Hilfsfunktionen ----------
@pytest.mark.parametrize("text, expected", [
    ("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view?usp=sharing", "1AbCdEfGhIjKlMnOpQrStUv"),
    ("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUv", "1AbCdEfGhIjKlMnOpQrStUv"),
    ("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/edit", "1AbCdEfGhIjKlMnOpQrStUv"),
    ("  1AbCdEfGhIjKlMnOpQrStUv  ", "1AbCdEfGhIjKlMnOpQrStUv"),
])
def test_drive_id_extracts_the_file_id(app, text, expected):
    assert app.drive_id(text) == expected


def test_entry_label_format(app):
    entry = {"label": "Lauf", "created": "2026-10-19T14:38:00", "name": "batches/abc123def456"}
    assert app.entry_label(entry) == "Lauf (2026-10-19 14:38) def456"


@pytest.mark.parametrize("version, expected", [
    ("2.31.0", True), ("2.30.1", True), ("3.0.0", True), ("2.30.0", False), ("1.99.9", False),
    ("2.30.1rc1", True),  # nicht auswertbar: im Zweifel nicht einschränken
])
def test_sdk_has_long_decode(app, monkeypatch, version, expected):
    monkeypatch.setattr(app.ns["genai"], "__version__", version)
    assert app.sdk_has_long_decode() is expected


# ---------- Ordner ----------
def test_base_dir_prefers_environment_variable(app, home):
    assert app._base_dir() == home.resolve()
    assert app.CONF_DIR == home.resolve() / "conf" and app.DATA_DIR == home.resolve() / "data"


def test_base_dir_defaults_to_the_project_folder_above_src(app, monkeypatch):
    monkeypatch.delenv("GEMINI_UI_HOME")
    assert app._base_dir() == helpers.ROOT  # die App-Datei liegt in <Projektordner>/src/


def test_base_dir_for_other_file_locations(app, monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_UI_HOME")
    app.ns["__file__"] = str(tmp_path / "projekt" / "src" / "gemini_batch_ui.py")  # Ordner „src“: der Ordner darüber
    assert app._base_dir() == (tmp_path / "projekt").resolve()
    app.ns["__file__"] = str(tmp_path / "flach" / "gemini_batch_ui.py")  # älteres, flaches Layout: der eigene Ordner
    assert app._base_dir() == (tmp_path / "flach").resolve()
    del app.ns["__file__"]  # ohne Dateiangabe (z. B. exec): der Arbeitsordner
    assert app._base_dir() == Path.cwd()


LEGACY_FILES = ["credentials.json", "token.json", "batch_jobs.json", "pdf_files.json", "requests_1.jsonl",
                "batch_jobs.defekt-5.json"]


def test_prepare_folders_creates_both_folders(app, home):
    assert app.prepare_folders() == []
    assert (home / "conf").is_dir() and (home / "data").is_dir()


def test_prepare_folders_moves_legacy_files_and_leaves_others(app, home):
    for name in LEGACY_FILES + ["notes.txt"]:
        (home / name).write_text(name)
    moved = app.prepare_folders()
    assert len(moved) == len(LEGACY_FILES)
    assert sorted(p.name for p in (home / "conf").iterdir()) == ["credentials.json", "token.json"]
    assert sorted(p.name for p in (home / "data").iterdir()) == sorted(
        ["batch_jobs.json", "pdf_files.json", "requests_1.jsonl", "batch_jobs.defekt-5.json"])
    assert (home / "notes.txt").read_text() == "notes.txt"
    assert app.prepare_folders() == []  # ein zweiter Aufruf findet nichts mehr


def test_prepare_folders_never_overwrites(app, home):
    (home / "data").mkdir()
    (home / "data" / "pdf_files.json").write_text("neu")
    (home / "pdf_files.json").write_text("alt")
    assert app.prepare_folders() == []
    assert (home / "data" / "pdf_files.json").read_text() == "neu"
    assert (home / "pdf_files.json").read_text() == "alt"


# ---------- Verlaufsdatei ----------
def test_load_jobs_without_file_is_empty(app):
    assert app.load_jobs() == []


def test_load_jobs_reads_entries(app, home):
    write_history(home, [{"name": "x"}])
    assert app.load_jobs() == [{"name": "x"}]


def test_load_jobs_backs_up_a_corrupt_file(app, home):
    path = helpers.history_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("{kaputt")
    assert app.load_jobs() == []
    backups = list(path.parent.glob("batch_jobs.defekt-*.json"))
    assert len(backups) == 1 and backups[0].read_text() == "{kaputt" and not path.exists()


def test_mutate_jobs_creates_the_data_folder(app, home, rt):
    app.mutate_jobs(rt.lock, lambda jobs: jobs.append({"name": "a"}))
    assert read_history(home) == [{"name": "a"}]


def test_mutate_jobs_is_thread_safe_and_leaves_no_temp_file(app, home, rt):
    def add(i):
        app.mutate_jobs(rt.lock, lambda jobs: jobs.append({"name": f"e{i}"}))

    threads = [threading.Thread(target=add, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(e["name"] for e in read_history(home)) == sorted(f"e{i}" for i in range(30))
    assert not (home / "data" / "batch_jobs.tmp").exists()


# ---------- PDF-Upload ----------
def test_upload_pdf_uploads_once_and_remembers_the_file_by_content(app, home):
    fake = FakeGemini()
    first = app.upload_pdf(fake, "buch.pdf", b"PDF-A")
    second = app.upload_pdf(fake, "andere-bezeichnung.pdf", b"PDF-A")
    assert len(fake.uploads) == 1 and second.uri == first.uri
    key = hashlib.sha256(b"PDF-A").hexdigest()
    assert json.loads((home / "data" / "pdf_files.json").read_text()) == {key: first.name}


def test_upload_pdf_different_content_gets_a_new_file(app):
    fake = FakeGemini()
    a, b = app.upload_pdf(fake, "a.pdf", b"A"), app.upload_pdf(fake, "b.pdf", b"B")
    assert len(fake.uploads) == 2 and a.uri != b.uri


def test_upload_pdf_reuploads_when_the_file_expired(app, home):
    fake = FakeGemini()
    app.upload_pdf(fake, "b.pdf", b"X")
    fake.get_states.append(ApiError(404))  # bei Google abgelaufen
    again = app.upload_pdf(fake, "b.pdf", b"X")
    assert len(fake.uploads) == 2
    assert list(json.loads((home / "data" / "pdf_files.json").read_text()).values()) == [again.name]


def test_upload_pdf_reuploads_when_the_remembered_file_is_not_active(app):
    fake = FakeGemini()
    app.upload_pdf(fake, "b.pdf", b"X")
    fake.get_states.append("PROCESSING")
    app.upload_pdf(fake, "b.pdf", b"X")
    assert len(fake.uploads) == 2


def test_upload_pdf_waits_while_google_processes_the_file(app, fake_time):
    fake = FakeGemini(upload_state="PROCESSING", get_states=["PROCESSING", "ACTIVE"])
    f = app.upload_pdf(fake, "b.pdf", b"X")
    assert f.state.name == "ACTIVE" and fake_time.sleeps == [2, 2]


def test_upload_pdf_raises_and_remembers_nothing_when_processing_fails(app, home):
    with pytest.raises(RuntimeError, match="FAILED"):
        app.upload_pdf(FakeGemini(upload_state="FAILED"), "b.pdf", b"X")
    assert not (home / "data" / "pdf_files.json").exists()


# ---------- Batch-Anfragen ----------
PDFS = [SimpleNamespace(uri="https://example.test/v1beta/files/A"), SimpleNamespace(uri="https://example.test/v1beta/files/B")]


def test_start_batch_builds_requests_with_pdfs_first(app, home):
    fake = FakeGemini()
    job, keyed = app.start_batch(fake, "gemini-4-argon", PDFS, ["P1", "P2"], "Sei kurz.", "high", 32768, "lauf")
    assert keyed == {"prompt-1": "P1", "prompt-2": "P2"} and job.name.startswith("batches/")
    assert [r["key"] for r in fake.batch_requests] == ["prompt-1", "prompt-2"]
    request = fake.batch_requests[0]["request"]
    parts = request["contents"][0]["parts"]
    assert [p["file_data"]["file_uri"].rsplit("/", 1)[1] for p in parts[:2]] == ["A", "B"]
    assert all(p["file_data"]["mime_type"] == "application/pdf" for p in parts[:2])
    assert parts[2] == {"text": "P1"}
    assert request["contents"][0]["role"] == "user"
    assert request["generation_config"] == {"max_output_tokens": 32768, "thinking_config": {"thinking_level": "high"}}
    assert request["system_instruction"] == {"parts": [{"text": "Sei kurz."}]}


def test_start_batch_uploads_the_jsonl_into_the_data_folder_and_creates_the_batch(app, home):
    fake = FakeGemini()
    app.start_batch(fake, "gemini-4-argon", PDFS, ["P1"], "", "high", 1024, "mein-lauf")
    assert fake.jsonl_uploads[0].parent == home.resolve() / "data"
    assert fake.jsonl_uploads[0].name.startswith("requests_") and fake.jsonl_uploads[0].suffix == ".jsonl"
    assert (fake.jsonl_configs[0].mime_type, fake.jsonl_configs[0].display_name) == ("jsonl", "mein-lauf")
    assert fake.created_batches == [("gemini-4-argon", "files/jsonl1", {"display_name": "mein-lauf"})]


def test_start_batch_standard_thinking_and_no_system_send_neither(app):
    fake = FakeGemini()
    app.start_batch(fake, "gemini-4-argon", [], ["P1"], "", "Standard", 2048, "x")
    request = fake.batch_requests[0]["request"]
    assert request["generation_config"] == {"max_output_tokens": 2048}
    assert "system_instruction" not in request
    assert request["contents"][0]["parts"] == [{"text": "P1"}]  # ohne PDF nur der Prompt


def test_start_batch_keeps_umlauts_readable_in_the_file(app, home):
    fake = FakeGemini()
    app.start_batch(fake, "m", [], ["Götzen-Dämmerung"], "", "Standard", 1024, "x")
    assert "Götzen-Dämmerung" in fake.jsonl_uploads[0].read_text("utf-8")


# ---------- Wiederholungen ----------
def test_ask_retries_transient_errors_with_growing_pauses(app, fake_time):
    fake = FakeGemini([ApiError(503), ApiError(429), "ok"])
    resp = app.ask(fake, "m", [], None)
    assert resp.candidates[0].content.parts[0].text == "Antwort 3"
    assert fake_time.sleeps == [10, 20] and len(fake.calls) == 3


def test_ask_does_not_retry_client_errors(app, fake_time):
    fake = FakeGemini([ApiError(400)])
    with pytest.raises(ApiError):
        app.ask(fake, "m", [], None)
    assert fake_time.sleeps == [] and len(fake.calls) == 1


def test_ask_gives_up_after_three_attempts(app, fake_time):
    fake = FakeGemini([ApiError(503)] * 3)
    with pytest.raises(ApiError):
        app.ask(fake, "m", [], None)
    assert fake_time.sleeps == [10, 20] and len(fake.calls) == 3
