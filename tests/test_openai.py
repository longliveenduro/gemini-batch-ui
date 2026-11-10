"""Tests der OpenAI-Anbindung (nur Batch): Anfrageaufbau, PDF-Upload, Ergebniszeilen, Statusabfrage und Oberfläche.

Die Antwortrümpfe der Fakes sind nach dem Schema des SDK gültig (siehe test_the_sample_bodies_match_the_sdk_schema).
Gegen die echte OpenAI-API laufen diese Tests nicht: Wie sie sich verhält, steht unter „Verifikationsstand“ in der README.
"""
import hashlib
import json
import re
import sys

import pytest

import helpers
from fakes import FakeGemini, FakeOpenAI, OpenAIError, install, install_openai, make_openai_body
from helpers import (APP_PATH, MODE_BATCH, PROVIDER_GEMINI, PROVIDER_OPENAI, TAB_JOBS, TAB_NEW, batch_entry, button,
                     captions, click, finished_batch, finished_openai_batch, markdown, new_app, new_openai_app,
                     openai_entry, read_history, set_prompts, write_history)

ui = pytest.mark.ui
PDF = "application/pdf"
PDF_IDS = ["file-A", "file-B"]


@pytest.fixture
def use_openai(monkeypatch):
    """use_openai(...) installiert einen FakeOpenAI (Argumente wie dort) und liefert ihn zurück."""
    def make(**kwargs):
        return install_openai(monkeypatch, FakeOpenAI(**kwargs))
    return make


@pytest.fixture
def use_gemini(monkeypatch):
    """use_gemini(...) installiert einen FakeGemini (Argumente wie dort) und liefert ihn zurück."""
    def make(*args, **kwargs):
        return install(monkeypatch, FakeGemini(*args, **kwargs))
    return make


def output_line(key="prompt-1", **body_args):
    """Eine Zeile der Ausgabedatei einer erfolgreichen Anfrage."""
    return {"id": f"batch_req_{key}", "custom_id": key, "error": None,
            "response": {"status_code": 200, "request_id": "req_1", "body": make_openai_body(**body_args)}}


def requests(*keys):
    return [{"custom_id": k} for k in keys]


def test_ui_texts_used_by_the_openai_tests_match_the_app(app):
    assert app.PROVIDER_GEMINI == PROVIDER_GEMINI and app.PROVIDER_OPENAI == PROVIDER_OPENAI
    assert app.PROVIDERS == [PROVIDER_GEMINI, PROVIDER_OPENAI]


# ---------- Anbieter und Clients ----------
def test_entry_provider_defaults_to_gemini_for_older_entries(app):
    assert app.entry_provider({}) == PROVIDER_GEMINI
    assert app.entry_provider(batch_entry()) == PROVIDER_GEMINI
    assert app.entry_provider(openai_entry()) == PROVIDER_OPENAI
    assert app.entry_provider({"provider": "etwas Unbekanntes"}) == PROVIDER_GEMINI


def test_make_client_builds_the_client_of_the_provider(app, monkeypatch):
    openai_fake = install_openai(monkeypatch, FakeOpenAI())
    gemini_fake = install(monkeypatch, FakeGemini())
    assert app.make_client(PROVIDER_OPENAI, "sk-1") is openai_fake and openai_fake.client_kwargs == [{"api_key": "sk-1"}]
    assert app.make_client(PROVIDER_GEMINI, "g-1") is gemini_fake and gemini_fake.client_kwargs == [{"api_key": "g-1"}]


def test_openai_client_explains_a_missing_package(app, monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)  # der Import schlägt dann mit ImportError fehl
    with pytest.raises(RuntimeError, match="pip install"):
        app.openai_client("sk-1")


# ---------- Anfrageaufbau und Batch ----------
def test_openai_request_puts_the_pdfs_first_and_sets_all_options(app):
    body = app.openai_request("gpt-6-astra", PDF_IDS, "Frage", "Sei kurz.", "high", 4096)
    assert body == {
        "model": "gpt-6-astra", "instructions": "Sei kurz.", "reasoning": {"effort": "high"}, "max_output_tokens": 4096,
        "input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file-A"},
                                               {"type": "input_file", "file_id": "file-B"},
                                               {"type": "input_text", "text": "Frage"}]}],
    }


def test_openai_request_with_standard_effort_and_no_system_sends_neither(app):
    body = app.openai_request("gpt-6-astra", [], "Frage", "", "Standard", 2048)
    assert "reasoning" not in body and "instructions" not in body
    assert body["input"][0]["content"] == [{"type": "input_text", "text": "Frage"}]  # ohne PDF nur der Prompt


def test_every_effort_except_standard_is_sent_as_reasoning_effort(app):
    for effort in app.OPENAI_EFFORTS[1:]:
        assert app.openai_request("m", [], "P", "", effort, 1024)["reasoning"] == {"effort": effort}


def test_start_openai_batch_writes_one_jsonl_line_per_prompt_and_creates_the_batch(app):
    fake = FakeOpenAI()
    batch_id, keyed = app.start_openai_batch(fake, "gpt-6-astra", PDF_IDS, ["P1", "P2"], "Sei kurz.", "high", 32768,
                                             "mein-lauf")
    assert batch_id == "batch_fake000001" and keyed == {"prompt-1": "P1", "prompt-2": "P2"}
    assert [r["custom_id"] for r in fake.batch_requests] == ["prompt-1", "prompt-2"]
    first = fake.batch_requests[0]
    assert (first["method"], first["url"]) == ("POST", "/v1/responses")
    assert first["body"] == app.openai_request("gpt-6-astra", PDF_IDS, "P1", "Sei kurz.", "high", 32768)
    assert fake.file_calls == [{"purpose": "batch", "expires_after": None}]
    assert fake.created_batches == [{"input_file_id": "file-jsonl1", "endpoint": "/v1/responses",
                                     "completion_window": "24h", "metadata": {"description": "mein-lauf"}}]


def test_the_openai_requests_stay_in_the_data_folder_with_readable_umlauts(app, home):
    fake = FakeOpenAI()
    app.start_openai_batch(fake, "m", [], ["Götzen-Dämmerung"], "", "Standard", 1024, "x")
    path = fake.jsonl_uploads[0]
    assert path.parent == home.resolve() / "data" and path.name.startswith("requests_openai_")
    assert path.suffix == ".jsonl" and "Götzen-Dämmerung" in path.read_text("utf-8")


def test_a_long_label_is_cut_to_the_metadata_limit(app):
    fake = FakeOpenAI()
    app.start_openai_batch(fake, "m", [], ["P"], "", "Standard", 1024, "x" * 600)
    assert len(fake.created_batches[0]["metadata"]["description"]) == 512


# ---------- PDF-Upload ----------
def test_upload_pdf_openai_uploads_once_and_remembers_the_file_by_content(app, home):
    fake = FakeOpenAI()
    first = app.upload_pdf_openai(fake, "buch.pdf", b"PDF-A")
    second = app.upload_pdf_openai(fake, "anderer-name.pdf", b"PDF-A")
    assert len(fake.uploads) == 1 and second.id == first.id
    db = json.loads((home / "data" / "openai_files.json").read_text("utf-8"))
    assert db == {hashlib.sha256(b"PDF-A").hexdigest(): first.id}
    assert not (home / "data" / "pdf_files.json").exists()  # die Gemini-Zuordnung bleibt unberührt


def test_upload_pdf_openai_sends_the_pdf_as_model_input_and_lets_it_expire(app):
    fake = FakeOpenAI()
    app.upload_pdf_openai(fake, "buch", b"X")  # Name ohne Endung
    assert fake.upload_names == ["buch.pdf"]
    assert fake.file_calls == [{"purpose": "user_data",
                                "expires_after": {"anchor": "created_at", "seconds": app.OPENAI_FILE_TTL}}]


def test_upload_pdf_openai_different_content_gets_its_own_file(app):
    fake = FakeOpenAI()
    assert app.upload_pdf_openai(fake, "a.pdf", b"A").id != app.upload_pdf_openai(fake, "b.pdf", b"B").id
    assert fake.uploads == [b"A", b"B"]


def test_upload_pdf_openai_uploads_again_when_the_file_is_gone(app, home):
    fake = FakeOpenAI()
    first = app.upload_pdf_openai(fake, "b.pdf", b"X")
    fake.gone.add(first.id)  # bei OpenAI abgelaufen oder gelöscht (404)
    again = app.upload_pdf_openai(fake, "b.pdf", b"X")
    assert len(fake.uploads) == 2 and again.id != first.id
    assert list(json.loads((home / "data" / "openai_files.json").read_text("utf-8")).values()) == [again.id]


def test_upload_pdf_openai_uploads_again_when_the_remembered_file_has_an_error_status(app):
    fake = FakeOpenAI()
    first = app.upload_pdf_openai(fake, "b.pdf", b"X")
    fake.file_status[first.id] = "error"
    app.upload_pdf_openai(fake, "b.pdf", b"X")
    assert len(fake.uploads) == 2


# ---------- Ergebniszeilen ----------
def test_parse_openai_line_reads_text_tokens_and_model(app):
    row = app.parse_openai_line(output_line(text="Hallo", input_tokens=100, cached=64, reasoning=10, output=25),
                                {"prompt-1": "Frage"})
    assert (row["key"], row["prompt"], row["text"]) == ("prompt-1", "Frage", "Hallo")
    assert (row["in"], row["cached"], row["think"], row["out"]) == (100, 64, 10, 15)  # Ausgabe ohne Denk-Token
    assert (row["model"], row["finish"], row["error"]) == ("gpt-6-astra-2026-09-01", "STOP", None)
    assert row["usage"]["input_tokens"] == 100 and row["usage"]["total_tokens"] == 125


def test_parse_openai_line_ignores_reasoning_items_and_joins_text_parts(app):
    o = output_line(text="")
    o["response"]["body"]["output"] = [
        {"id": "rs", "type": "reasoning", "summary": [{"type": "summary_text", "text": "Denkschritt"}]},
        {"id": "m", "type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "Teil 1. ", "annotations": []},
                     {"type": "output_text", "text": "Teil 2.", "annotations": []}]},
    ]
    assert app.parse_openai_line(o, {})["text"] == "Teil 1. Teil 2."


def test_parse_openai_line_never_reports_more_thinking_than_output(app):
    row = app.parse_openai_line(output_line(reasoning=30, output=20), {})
    assert row["out"] == 0 and row["think"] == 30


def test_parse_openai_line_maps_incomplete_answers(app):
    cut = app.parse_openai_line(output_line(text="Halb", status="incomplete", incomplete="max_output_tokens"), {})
    assert cut["finish"] == "MAX_TOKENS" and cut["text"] == "Halb" and cut["error"] is None  # show_rows warnt wie bei Gemini
    other = app.parse_openai_line(output_line(text="Teil", status="incomplete", incomplete="content_filter"), {})
    assert other["finish"] == "content_filter" and other["text"] == "Teil"


def test_parse_openai_line_reports_errors_from_the_error_file(app):
    o = {"id": "batch_req_2", "custom_id": "prompt-2", "response": None,
         "error": {"code": "batch_expired", "message": "Nicht rechtzeitig fertig."}}
    row = app.parse_openai_line(o, {"prompt-2": "Frage"})
    assert row["error"] == "batch_expired: Nicht rechtzeitig fertig." and row["text"] == "" and row["prompt"] == "Frage"
    assert (row["in"], row["cached"], row["think"], row["out"]) == (0, 0, 0, 0)
    assert (row["finish"], row["model"]) == ("unbekannt", "unbekannt")


def test_parse_openai_line_reports_http_errors_inside_the_output_file(app):
    error = {"message": "Unsupported value: 'none'", "type": "invalid_request_error", "param": "reasoning.effort",
             "code": "unsupported_value"}
    o = {"id": "x", "custom_id": "prompt-1", "error": None,
         "response": {"status_code": 400, "request_id": "r", "body": {"error": error}}}
    row = app.parse_openai_line(o, {})
    assert row["error"] == "unsupported_value: Unsupported value: 'none'" and row["finish"] == "unbekannt"
    bare = {"custom_id": "prompt-1", "response": {"status_code": 500, "body": {}}}
    assert app.parse_openai_line(bare, {})["error"] == "HTTP 500"


def test_parse_openai_line_shows_a_refusal_as_the_reason_for_the_missing_text(app):
    row = app.parse_openai_line(output_line(text="", refusal="Das kann ich nicht."), {})
    assert row["text"] == "" and row["error"] == "Das Modell hat abgelehnt: Das kann ich nicht."


def test_parse_openai_line_without_text_reports_the_finish_reason(app):
    assert app.parse_openai_line(output_line(text=""), {})["error"] == "Keine Antwort (Abschlussgrund: STOP)"


def test_parse_openai_line_of_a_failed_response_carries_its_error(app):
    o = output_line(text="", status="failed", error={"code": "server_error", "message": "boom"})
    row = app.parse_openai_line(o, {})
    assert row["error"] == "server_error: boom" and row["finish"] == "failed"


def test_parse_results_sorts_openai_lines_numerically(app):
    raw = "\n".join(json.dumps(output_line(key=k)) for k in ("prompt-10", "prompt-2", "prompt-1")) + "\n\n"
    rows = app.parse_results(raw.encode("utf-8"), {}, app.parse_openai_line)
    assert [r["key"] for r in rows] == ["prompt-1", "prompt-2", "prompt-10"]


def test_the_sample_bodies_match_the_sdk_schema():
    """Schutz davor, dass Fake und echte API auseinanderlaufen: Die Beispielantworten sind gültige SDK-Objekte."""
    from openai.types.responses import Response
    for args in ({}, {"status": "incomplete", "incomplete": "max_output_tokens"}, {"text": "", "refusal": "Nein."},
                 {"status": "failed", "error": {"code": "server_error", "message": "boom"}}):
        Response.model_validate(make_openai_body(**args))


# ---------- Status und Ergebnisse ----------
def test_fetch_openai_batch_while_running_loads_nothing(app):
    fake = FakeOpenAI(batch_status="in_progress")
    assert app.fetch_openai_batch(fake, openai_entry()) == ("in_progress", None, None, "gpt-6-astra-2026-09-01")
    assert fake.content_calls == []


def test_fetch_openai_batch_completed_reads_the_output_and_the_error_file(app):
    fake = FakeOpenAI(batch_text="Fertig", failing={"prompt-2": {"code": "server_error", "message": "kaputt"}})
    fake.batch_requests = requests("prompt-1", "prompt-2", "prompt-3")
    j = openai_entry(prompts={"prompt-1": "A", "prompt-2": "B", "prompt-3": "C"})
    state, rows, err, model = app.fetch_openai_batch(fake, j)
    assert (state, err, model) == ("completed", None, "gpt-6-astra-2026-09-01")
    assert [r["key"] for r in rows] == ["prompt-1", "prompt-2", "prompt-3"]
    assert [r["text"] for r in rows] == ["Fertig", "", "Fertig"]
    assert rows[1]["error"] == "server_error: kaputt" and rows[1]["prompt"] == "B"
    assert fake.content_calls == ["file-out1", "file-err1"]


def test_fetch_openai_batch_without_failures_reads_only_the_output_file(app):
    fake = FakeOpenAI()
    fake.batch_requests = requests("prompt-1")
    assert len(app.fetch_openai_batch(fake, openai_entry())[1]) == 1
    assert fake.content_calls == ["file-out1"]


@pytest.mark.parametrize("status", ["expired", "cancelled"])
def test_fetch_openai_batch_expired_or_cancelled_keeps_the_finished_answers(app, status):
    fake = FakeOpenAI(batch_status=status, failing={"prompt-2": {"code": f"batch_{status}", "message": "Nicht fertig."}})
    fake.batch_requests = requests("prompt-1", "prompt-2")
    state, rows, err, _ = app.fetch_openai_batch(fake, openai_entry(prompts={"prompt-1": "A", "prompt-2": "B"}))
    assert state == status and state in app.DONE
    assert rows[0]["text"] == "OpenAI-Antwort prompt-1" and rows[1]["error"] == f"batch_{status}: Nicht fertig."


def test_fetch_openai_batch_failed_shows_the_errors_of_the_whole_batch(app):
    fake = FakeOpenAI(batch_status="failed", batch_errors=[
        {"code": "token_limit_exceeded", "message": "Enqueued token limit reached", "line": None},
        {"code": "invalid_request", "message": "Zu groß", "line": 3}])
    state, rows, err, _ = app.fetch_openai_batch(fake, openai_entry())
    assert state == "failed" and rows is None
    assert err == "token_limit_exceeded: Enqueued token limit reached; invalid_request: Zu groß (Zeile 3)"


def test_every_final_openai_status_counts_as_done_and_the_others_do_not(app):
    assert {"completed", "failed", "cancelled", "expired"} <= app.DONE
    assert not {"validating", "in_progress", "finalizing", "cancelling"} & app.DONE


def test_cancel_batch_calls_the_api_of_the_entry_provider(app):
    openai_fake, gemini_fake = FakeOpenAI(), FakeGemini()
    app.cancel_batch(openai_fake, openai_entry())
    app.cancel_batch(gemini_fake, batch_entry())
    assert openai_fake.cancelled == ["batch_abc123def456"] and gemini_fake.cancelled == ["batches/abc123def456"]


# ---------- Oberfläche: Anbieter und Formular ----------
@ui
def test_the_default_provider_is_gemini_and_shows_no_openai_widgets(use_gemini):
    use_gemini()
    at = new_app()
    at.run()
    assert not at.exception and at.radio(key="provider").value == PROVIDER_GEMINI
    assert at.radio(key="mode").value == MODE_BATCH
    assert [t.label for t in at.text_input if "API-Key" in t.label] == ["Gemini API-Key", "OpenAI API-Key"]
    assert not [s for s in at.selectbox if s.key == "effort"]


@ui
def test_openai_shows_its_own_defaults_and_no_mode_switch(use_openai):
    use_openai()
    at = new_openai_app()
    at.run()
    assert not at.exception and not at.error
    assert [t for t in at.text_input if t.label == "Modell"][0].value == "gpt-6-astra"
    assert not [r for r in at.radio if r.key == "mode"]  # mit OpenAI gibt es nur Batch
    assert at.selectbox(key="effort").value == "Standard" and at.selectbox(key="effort").options == [
        "Standard", "none", "minimal", "low", "medium", "high", "xhigh", "max"]
    assert at.number_input(key="max_openai").value == 32768 and at.number_input(key="max_openai").max == 128000
    assert not [s for s in at.selectbox if s.key == "think"]
    assert any("nur der Batch-Modus" in c for c in captions(at))
    assert button(at, "Batch starten").disabled and not [b for b in at.button if b.label == "Synchron starten"]
    assert at.text_input(key="label_openai").value.startswith("openai-")


@ui
def test_switching_the_provider_in_the_sidebar_swaps_the_form(use_gemini, use_openai):
    use_gemini()
    use_openai()
    at = new_app()
    at.run()
    at.radio(key="provider").set_value(PROVIDER_OPENAI)
    at.run()
    assert not at.exception and at.selectbox(key="effort").value == "Standard" and not [
        r for r in at.radio if r.key == "mode"]
    at.radio(key="provider").set_value(PROVIDER_GEMINI)
    at.run()
    assert not at.exception and at.selectbox(key="think").value == "high" and at.radio(key="mode").value == MODE_BATCH
    assert [t for t in at.text_input if t.label == "Modell"][0].value == "gemini-4-argon"


@ui
def test_openai_without_its_key_asks_for_it(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    at = new_openai_app()
    at.run()
    assert not at.exception and not at.button
    assert any("OpenAI API-Key" in i.value and "OPENAI_API_KEY" in i.value for i in at.info)


@ui
def test_a_missing_openai_key_does_not_block_gemini(monkeypatch, use_gemini):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    use_gemini()
    at = new_app()
    at.run()
    assert not at.exception and not any("API-Key" in i.value for i in at.info) and button(at, "Batch starten")


@ui
def test_the_openai_key_from_the_environment_is_prefilled_and_can_be_replaced(use_openai):
    fake = use_openai()
    at = new_openai_app()
    at.run()
    field = [t for t in at.text_input if t.label == "OpenAI API-Key"][0]
    assert field.value == "test-openai-key" and fake.client_kwargs[-1] == {"api_key": "test-openai-key"}
    field.set_value("anderer-key")
    at.run()
    assert fake.client_kwargs[-1] == {"api_key": "anderer-key"}


@ui
def test_a_missing_openai_package_is_reported_instead_of_crashing(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    at = new_openai_app()
    at.run()
    assert not at.exception and any("pip install" in e.value for e in at.error) and not at.button


# ---------- Oberfläche: Formularwerte beim Anbieterwechsel ----------
# Streamlit vergisst den Wert eines Widgets, das einen Lauf lang fehlt, und der Browser baut es dann mit dem Standardwert
# neu auf (Modellfeld leer, Token-Limit 256, Denktiefe „Standard“). AppTest sieht nur den Wert im Backend, der stimmt
# auch ohne Gegenmaßnahme. Darum prüfen diese Tests, was die App dem Browser schickt: set_value samt Wert im Protokoll.
def sent_to_browser(widget, expected):
    """Der Wert steht im Backend und geht beim Zeichnen mit set_value an den Browser."""
    assert widget.value == expected
    assert widget.proto.set_value is True, f"{widget.key}: Der Browser würde das Feld mit dem Standardwert aufbauen"


@ui
def test_the_default_model_is_shown_again_after_a_visit_to_openai_without_its_key(use_gemini, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")  # mit leerem Key endet der OpenAI-Lauf hinter der Seitenleiste (st.stop)
    use_gemini()
    at = new_app()
    at.run()
    at.radio(key="provider").set_value(PROVIDER_OPENAI)
    at.run()
    assert any("OPENAI_API_KEY" in i.value for i in at.info) and not at.button  # der Formularbereich fehlt in diesem Lauf
    at.radio(key="provider").set_value(PROVIDER_GEMINI)
    at.run()
    assert not at.exception
    model = at.text_input(key="model_gemini")
    sent_to_browser(model, "gemini-4-argon")
    assert model.proto.value == "gemini-4-argon"  # nicht leer
    assert at.number_input(key="max_batch").proto.value == 32768  # nicht der Mindestwert des Feldes
    sent_to_browser(at.selectbox(key="think"), "high")


@ui
def test_the_form_values_survive_a_visit_to_openai_without_its_key(use_gemini, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    use_gemini()
    at = new_app()
    at.run()
    at.text_input(key="model_gemini").set_value("mein-eigenes-modell")
    at.selectbox(key="think").set_value("low")
    at.number_input(key="max_batch").set_value(4096)
    at.text_area(key="system").set_value("Sei kurz.")
    set_prompts(at, "Erster Prompt")
    at.radio(key="provider").set_value(PROVIDER_OPENAI)
    at.run()
    at.radio(key="provider").set_value(PROVIDER_GEMINI)
    at.run()
    assert not at.exception
    for widget, expected in ((at.text_input(key="model_gemini"), "mein-eigenes-modell"),
                             (at.selectbox(key="think"), "low"), (at.number_input(key="max_batch"), 4096),
                             (at.text_area(key="system"), "Sei kurz."), (at.text_area(key="raw"), "Erster Prompt"),
                             (at.radio(key="mode"), MODE_BATCH)):
        sent_to_browser(widget, expected)
    assert at.text_input(key="model_gemini").proto.value == "mein-eigenes-modell"
    assert at.text_area(key="raw").proto.value == "Erster Prompt"
    assert at.number_input(key="max_batch").proto.value == 4096


@ui
def test_each_provider_keeps_its_own_settings_when_switching_back_and_forth(use_gemini, use_openai):
    use_gemini()
    use_openai()
    at = new_app()
    at.run()
    at.text_input(key="model_gemini").set_value("mein-eigenes-modell")
    at.selectbox(key="think").set_value("low")
    at.number_input(key="max_batch").set_value(4096)
    at.run()
    at.radio(key="provider").set_value(PROVIDER_OPENAI)
    at.run()
    at.text_input(key="model_openai").set_value("gpt-6-luna")
    at.selectbox(key="effort").set_value("high")
    at.number_input(key="max_openai").set_value(65536)
    at.run()
    for _ in range(2):  # zweimal hin und her: Beide Anbieter behalten ihre eigenen Werte
        at.radio(key="provider").set_value(PROVIDER_GEMINI)
        at.run()
        assert not at.exception
        sent_to_browser(at.text_input(key="model_gemini"), "mein-eigenes-modell")
        sent_to_browser(at.selectbox(key="think"), "low")
        sent_to_browser(at.number_input(key="max_batch"), 4096)
        at.radio(key="provider").set_value(PROVIDER_OPENAI)
        at.run()
        assert not at.exception
        sent_to_browser(at.text_input(key="model_openai"), "gpt-6-luna")
        sent_to_browser(at.selectbox(key="effort"), "high")
        sent_to_browser(at.number_input(key="max_openai"), 65536)


@ui
def test_the_synchronous_settings_survive_a_visit_to_the_other_provider(use_gemini, use_openai):
    use_gemini()
    use_openai()
    at = new_app()
    at.run()
    at.radio(key="mode").set_value(helpers.MODE_SYNC)
    at.run()
    at.number_input(key="group").set_value(3)
    at.number_input(key="max_sync").set_value(8192)
    at.run()
    at.radio(key="provider").set_value(PROVIDER_OPENAI)
    at.run()
    at.radio(key="provider").set_value(PROVIDER_GEMINI)
    at.run()
    assert not at.exception
    sent_to_browser(at.radio(key="mode"), helpers.MODE_SYNC)
    sent_to_browser(at.number_input(key="group"), 3)
    sent_to_browser(at.number_input(key="max_sync"), 8192)


def test_every_form_widget_with_a_key_is_kept_alive_between_runs(app):
    """Wer ein Formularfeld mit Schlüssel ergänzt, muss es in FORM_KEYS aufnehmen (sonst vergisst Streamlit den Wert,
    sobald das Feld einen Lauf lang fehlt). Ausnahmen stehen hier mit Begründung."""
    ui_part = APP_PATH.read_text("utf-8").split("# ---------- UI ----------")[1]
    used = set(re.findall(r'\bkey="([a-z_]+)"', ui_part))
    exempt = {"provider": "steht vor jedem st.stop() und fehlt nie",
              "view": "der Tab-Zustand hält Streamlit selbst",
              "sel": "Auswahl im Jobs-Tab: Ihre Optionen ändern sich, ein alter Wert wäre dort ungültig",
              "label_": "Namensfeld je Modus: hat selbst einen Standardwert (neuer Name), ein Verlust schadet nicht"}
    assert used - set(exempt) == set(app.FORM_KEYS), "FORM_KEYS und die Schlüssel der Formularfelder weichen ab"


# ---------- Oberfläche: Start ----------
@ui
def test_openai_batch_start_uploads_the_pdfs_and_stores_the_entry(use_openai, home, app):
    fake = use_openai()
    at = new_openai_app()
    at.run()
    at.file_uploader[0].set_value([("Buch 1.pdf", b"PDF-1", PDF), ("Buch 2.pdf", b"PDF-2", PDF)])
    at.text_area(key="system").set_value("Sei kurz.")
    at.selectbox(key="effort").set_value("high")
    at.number_input(key="max_openai").set_value(4096)
    set_prompts(at, "Erster Prompt\n---\nZweiter Prompt")
    click(at, "Batch starten")
    assert not at.exception and not at.error and "läuft" in at.success[0].value
    assert fake.uploads == [b"PDF-1", b"PDF-2"] and fake.upload_names == ["Buch 1.pdf", "Buch 2.pdf"]
    assert [c["purpose"] for c in fake.file_calls] == ["user_data", "user_data", "batch"]
    request = fake.batch_requests[0]
    assert (request["custom_id"], request["url"]) == ("prompt-1", "/v1/responses")
    body = request["body"]
    assert body["model"] == "gpt-6-astra" and body["instructions"] == "Sei kurz."
    assert body["reasoning"] == {"effort": "high"} and body["max_output_tokens"] == 4096
    assert [p.get("file_id") or p["text"] for p in body["input"][0]["content"]] == ["file-pdf1", "file-pdf2", "Erster Prompt"]
    entry = read_history(home)[0]
    assert entry["provider"] == PROVIDER_OPENAI and entry["name"] == "batch_fake000001" and "state" not in entry
    assert (entry["model"], entry["think"], entry["max_out"]) == ("gpt-6-astra", "high", 4096)
    assert entry["pdf"] == "Buch 1.pdf, Buch 2.pdf" and entry["system"] == "Sei kurz."
    assert entry["prompts"] == {"prompt-1": "Erster Prompt", "prompt-2": "Zweiter Prompt"}
    assert at.session_state["sel"] == app.entry_label(entry)
    assert (home / "data" / "openai_files.json").exists() and list((home / "data").glob("requests_openai_*.jsonl"))
    assert not (home / "data" / "pdf_files.json").exists()


@ui
def test_openai_standard_effort_and_an_empty_system_prompt_send_neither(use_openai):
    fake = use_openai()
    at = new_openai_app()
    at.run()
    set_prompts(at, "Frage")
    click(at, "Batch starten")
    body = fake.batch_requests[0]["body"]
    assert "reasoning" not in body and "instructions" not in body and body["max_output_tokens"] == 32768
    assert body["input"][0]["content"] == [{"type": "input_text", "text": "Frage"}]


@ui
def test_the_model_field_decides_which_openai_model_is_used(use_openai, home):
    fake = use_openai()
    at = new_openai_app()
    at.run()
    [t for t in at.text_input if t.label == "Modell"][0].set_value("gpt-6-luna")
    set_prompts(at, "Frage")
    click(at, "Batch starten")
    assert fake.batch_requests[0]["body"]["model"] == "gpt-6-luna" and read_history(home)[0]["model"] == "gpt-6-luna"


@ui
def test_identical_pdfs_are_uploaded_to_openai_only_once(use_openai):
    fake = use_openai()
    at = new_openai_app()
    at.run()
    at.file_uploader[0].set_value([("Original.pdf", b"GLEICH", PDF), ("Kopie.pdf", b"GLEICH", PDF)])
    set_prompts(at, "Frage")
    click(at, "Batch starten")
    assert fake.uploads == [b"GLEICH"] and len(fake.batch_requests[0]["body"]["input"][0]["content"]) == 2


@ui
def test_a_failing_openai_start_shows_the_error_and_stores_nothing(use_openai, home):
    use_openai(create_error=OpenAIError(400, "Ungültige Anfrage"))
    at = new_openai_app()
    at.run()
    set_prompts(at, "Frage")
    click(at, "Batch starten")
    assert not at.exception and any("Start fehlgeschlagen" in e.value and "Ungültige Anfrage" in e.value for e in at.error)
    assert read_history(home) == []


@ui
def test_a_failing_pdf_upload_to_openai_shows_the_error_and_stores_nothing(use_openai, home):
    fake = use_openai(upload_error=OpenAIError(413, "Datei zu groß"))
    at = new_openai_app()
    at.run()
    at.file_uploader[0].set_value([("a.pdf", b"X", PDF)])
    set_prompts(at, "Frage")
    click(at, "Batch starten")
    assert any("Start fehlgeschlagen" in e.value and "Datei zu groß" in e.value for e in at.error)
    assert read_history(home) == [] and fake.created_batches == []


# ---------- Oberfläche: Jobs-Tab ----------
@ui
def test_the_jobs_tab_names_the_provider_of_an_openai_entry(use_openai, home):
    write_history(home, [finished_openai_batch(think="medium", max_out=65536)])
    use_openai()
    at = new_openai_app(TAB_JOBS)
    at.run()
    assert not at.exception
    assert any("Batch (OpenAI)" in c and "Modell gpt-6-astra, Denktiefe medium, max. 65536 Ausgabe-Token" in c
               for c in captions(at))


@ui
def test_a_finished_openai_batch_is_saved_locally_and_reopens_without_the_api(use_openai, home):
    write_history(home, [openai_entry()])
    fake = use_openai(batch_cached=64)
    fake.batch_requests = requests("prompt-1")  # der Fake liefert Ergebnisse zu den „gesendeten“ Anfragen
    at = new_openai_app(TAB_JOBS)
    at.run()
    at.run()
    saved = read_history(home)[0]
    assert saved["state"] == "completed" and saved["api_model"] == "gpt-6-astra-2026-09-01"
    assert saved["rows"][0]["cached"] == 64 and saved["rows"][0]["text"] == "OpenAI-Antwort prompt-1"

    broken = use_openai(get_error=OpenAIError(500))  # neue Sitzung, API unbrauchbar: darf nicht gefragt werden
    again = new_openai_app(TAB_JOBS)
    again.run()
    assert broken.get_calls == 0 and not again.error and not again.exception
    assert any("Gesichertes Endergebnis" in c for c in captions(again))
    assert "OpenAI-Antwort prompt-1" in markdown(again)
    assert fake.get_calls >= 1


@ui
def test_a_running_openai_batch_is_polled_and_shows_its_openai_status(use_openai, home):
    write_history(home, [openai_entry()])
    fake = use_openai(batch_status="in_progress")
    at = new_openai_app(TAB_JOBS)
    at.run()
    assert not at.exception and fake.get_calls >= 1
    assert any("Automatische Prüfung" in c for c in captions(at))
    assert any("in_progress" in m for m in markdown(at))
    assert "state" not in read_history(home)[0]  # kein Endzustand: nichts gesichert


@ui
def test_an_expired_openai_batch_keeps_the_finished_answers(use_openai, home):
    write_history(home, [openai_entry(prompts={"prompt-1": "A", "prompt-2": "B"})])
    fake = use_openai(batch_status="expired", failing={"prompt-2": {"code": "batch_expired", "message": "Zu spät."}})
    fake.batch_requests = requests("prompt-1", "prompt-2")
    at = new_openai_app(TAB_JOBS)
    at.run()
    at.run()
    saved = read_history(home)[0]
    assert saved["state"] == "expired" and [bool(r["text"]) for r in saved["rows"]] == [True, False]
    assert saved["rows"][1]["error"] == "batch_expired: Zu spät."


@ui
def test_a_failed_openai_batch_shows_its_errors(use_openai, home):
    write_history(home, [openai_entry()])
    use_openai(batch_status="failed", batch_errors=[{"code": "token_limit_exceeded", "message": "Zu viele Token."}])
    at = new_openai_app(TAB_JOBS)
    at.run()
    at.run()
    saved = read_history(home)[0]
    assert saved["state"] == "failed" and saved["error"] == "token_limit_exceeded: Zu viele Token."
    assert not at.exception


@ui
def test_a_vanished_openai_batch_stops_the_polling(use_openai, home):
    write_history(home, [openai_entry()])
    fake = use_openai(get_error=OpenAIError(404, "No such batch"))
    at = new_openai_app(TAB_JOBS)
    at.run()
    assert any("Abruf fehlgeschlagen" in e.value and "gestoppt" in e.value for e in at.error)
    at.run()
    assert fake.get_calls == 1  # 404 ist endgültig: kein weiterer Versuch


@ui
def test_the_cancel_button_calls_the_openai_api(use_openai, home):
    write_history(home, [openai_entry()])
    fake = use_openai(batch_status="in_progress")
    at = new_openai_app(TAB_JOBS)
    at.run()
    click(at, "Batch abbrechen")
    assert fake.cancelled == ["batch_abc123def456"] and any("Abbruch angefordert" in w.value for w in at.warning)


@ui
def test_each_entry_is_queried_with_the_client_of_its_own_provider(use_openai, use_gemini, home):
    write_history(home, [openai_entry()])
    openai_fake = use_openai(batch_status="in_progress")
    gemini_fake = use_gemini()
    at = new_app(TAB_JOBS)  # als Anbieter für neue Anfragen ist Gemini gewählt
    at.run()
    assert not at.exception and openai_fake.get_calls >= 1 and gemini_fake.get_calls == 0
    assert openai_fake.client_kwargs[-1] == {"api_key": "test-openai-key"}

    write_history(home, [batch_entry()])
    openai_fake.get_calls = 0
    other = new_openai_app(TAB_JOBS)  # umgekehrt: OpenAI gewählt, der Eintrag stammt von Gemini
    other.run()
    assert not other.exception and gemini_fake.get_calls >= 1 and openai_fake.get_calls == 0


@ui
def test_a_missing_key_of_the_entrys_provider_locks_status_check_and_cancel(use_gemini, monkeypatch, home):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    write_history(home, [openai_entry()])
    use_gemini()  # ein OpenAI-Client darf nicht gebaut werden: Der Schutz in conftest.py würde anschlagen
    at = new_app(TAB_JOBS)
    at.run()
    assert not at.exception
    assert any("OpenAI API-Key fehlt" in w.value for w in at.warning)
    assert button(at, "Status prüfen / Ergebnisse laden").disabled and button(at, "Batch abbrechen").disabled
    assert not any("Automatische Prüfung" in c for c in captions(at))


@ui
def test_saved_results_of_an_openai_entry_stay_visible_without_its_key(use_gemini, monkeypatch, home):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    write_history(home, [finished_openai_batch()])
    use_gemini()
    at = new_app(TAB_JOBS)
    at.run()
    assert not at.exception and "Gesicherte Antwort" in markdown(at)
    assert any("Gesichertes Endergebnis" in c for c in captions(at))


# ---------- Oberfläche: Eingaben übernehmen und Gespräch fortsetzen ----------
@ui
def test_reuse_inputs_of_an_openai_entry_switches_to_openai_and_restores_the_settings(use_openai, use_gemini, home):
    entry = openai_entry(think="xhigh", system="Kurz.", max_out=65536, model="gpt-6-luna", prompts={"prompt-1": "Eins"})
    write_history(home, [entry])
    use_openai()
    use_gemini()
    at = new_app(TAB_JOBS)  # Gemini ist gewählt: Das Übernehmen muss den Anbieter umschalten
    at.run()
    at.button(key="reuse:" + entry["name"]).click()
    at.run()
    assert not at.exception and at.session_state["view"] == TAB_NEW
    assert at.radio(key="provider").value == PROVIDER_OPENAI
    assert [t for t in at.text_input if t.label == "Modell"][0].value == "gpt-6-luna"
    assert at.selectbox(key="effort").value == "xhigh" and at.number_input(key="max_openai").value == 65536
    assert at.text_area(key="raw").value == "Eins" and at.text_area(key="system").value == "Kurz."
    assert not [r for r in at.radio if r.key == "mode"]


@ui
def test_reuse_inputs_of_a_gemini_entry_switches_back_to_gemini(use_openai, use_gemini, home):
    entry = batch_entry(think="medium", model="gemini-anderes-modell")
    write_history(home, [entry])
    use_openai()
    use_gemini()
    at = new_openai_app(TAB_JOBS)
    at.run()
    at.button(key="reuse:" + entry["name"]).click()
    at.run()
    assert not at.exception and at.radio(key="provider").value == PROVIDER_GEMINI
    assert at.radio(key="mode").value == MODE_BATCH and at.selectbox(key="think").value == "medium"
    assert [t for t in at.text_input if t.label == "Modell"][0].value == "gemini-anderes-modell"


@ui
def test_reuse_inputs_clamps_the_stored_output_limit_to_the_openai_maximum(use_openai, use_gemini, home):
    entry = openai_entry(max_out=999_999_999)
    write_history(home, [entry])
    use_openai()
    use_gemini()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key="reuse:" + entry["name"]).click()
    at.run()
    assert not at.exception and at.number_input(key="max_openai").value == 128000


@ui
def test_continuing_an_openai_answer_stays_with_openai(use_openai, use_gemini, home):
    entry = finished_openai_batch(think="medium")
    write_history(home, [entry])
    use_openai()
    use_gemini()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key=f"talk:{entry['name']}:prompt-1").click()
    at.run()
    assert not at.exception and at.session_state["view"] == TAB_NEW
    assert at.radio(key="provider").value == PROVIDER_OPENAI and at.selectbox(key="effort").value == "medium"
    assert not [r for r in at.radio if r.key == "mode"]


@ui
def test_gemini_entries_of_older_versions_still_open_next_to_openai_entries(use_openai, use_gemini, home):
    old = finished_batch()
    old.pop("api_model")  # ältere Einträge kennen weder provider noch manche anderen Felder
    write_history(home, [finished_openai_batch(), old])
    use_openai()
    use_gemini()
    at = new_app(TAB_JOBS)
    at.run()
    assert not at.exception and not at.error
    assert any(o.startswith("altbatch") for o in at.selectbox(key="sel").options)
    assert any(o.startswith("openai-batch") for o in at.selectbox(key="sel").options)
