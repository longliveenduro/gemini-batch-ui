"""Tests des Hintergrund-Modus (Interactions API, background=true, Long Decode): Anfrageaufbau, Start, Abfrage, Abbruch,
Zeilenparser und die Oberfläche. Ersatzdienst: FakeGemini mit interactions."""
from types import SimpleNamespace

import pytest

from fakes import ApiError, FakeGemini, InteractionsError, install, make_interaction
from helpers import (MODE_BATCH, MODE_BG, TAB_JOBS, TAB_NEW, background_entry, batch_entry, button, captions, click,
                     finished_background, markdown, new_app, read_history, row, set_prompts,
                     write_history)

pytestmark = pytest.mark.usefixtures("fake_time")  # Wiederholungspausen kosten keine Zeit
ui = pytest.mark.ui

PDF = "application/pdf"
IDS = ["v1_fake000001", "v1_fake000002"]


def pdfs(n=1):
    return [SimpleNamespace(uri=f"https://example.test/v1beta/files/pdf{k}") for k in range(1, n + 1)]


def start(app, rt, fake, prompts=("P1",), *, n_pdfs=1, system="", think="high", max_out=262144):
    names = [f"{c}.pdf" for c in "ABC"[:n_pdfs]]
    return app.start_background(fake, rt, "gemini-4-carbon", pdfs(n_pdfs), names, list(prompts), system, think,
                                max_out, "lang")


# ---------- Anfrageaufbau ----------
def test_each_prompt_becomes_a_background_interaction_with_the_pdfs_first(app, rt):
    fake = FakeGemini()
    start(app, rt, fake, ["P1", "P2"], n_pdfs=2)
    assert len(fake.interaction_requests) == 2
    pdf_parts = [{"type": "document", "uri": f"https://example.test/v1beta/files/pdf{k}", "mime_type": PDF}
                 for k in (1, 2)]
    for request, prompt in zip(fake.interaction_requests, ["P1", "P2"]):
        assert request["input"] == pdf_parts + [{"type": "text", "text": prompt}]
        assert request["model"] == "gemini-4-carbon" and request["background"] is True and request["store"] is True
        assert request["generation_config"] == {"max_output_tokens": 262144, "thinking_level": "high"}
        assert "system_instruction" not in request


def test_system_instruction_is_sent_and_standard_thinking_sends_no_level(app, rt):
    fake = FakeGemini()
    start(app, rt, fake, system="Sei kurz.", think="Standard", max_out=100000)
    request = fake.interaction_requests[0]
    assert request["system_instruction"] == "Sei kurz."
    assert request["generation_config"] == {"max_output_tokens": 100000}


def test_without_pdfs_only_the_prompt_is_sent(app, rt):
    fake = FakeGemini()
    start(app, rt, fake, n_pdfs=0)
    assert fake.interaction_requests[0]["input"] == [{"type": "text", "text": "P1"}]


def test_the_request_is_valid_for_the_sdk_schema(app):
    """Die Anfrage muss dem Schema des SDK entsprechen (Schlüsselnamen von generation_config sind dort Felder)."""
    from google.genai import interactions
    request = app.interaction_request("gemini-4-carbon", ["https://example.test/v1beta/files/pdf1"], "Frage",
                                      "Sei kurz.", "high", 262144)
    parsed = interactions.CreateModelInteraction.model_validate(request)
    assert parsed.background is True and parsed.store is True and parsed.system_instruction == "Sei kurz."
    assert parsed.generation_config.max_output_tokens == 262144 and parsed.generation_config.thinking_level == "high"


# ---------- Start ----------
def test_start_stores_the_entry_with_the_interaction_ids(app, rt, home):
    fake = FakeGemini()
    entry = start(app, rt, fake, ["P1", "P2"], n_pdfs=2, system="Kurz.", think="low", max_out=131072)
    assert entry["name"].startswith("bg-") and entry["mode"] == "background" and entry["label"] == "lang"
    assert entry["interactions"] == dict(zip(["prompt-1", "prompt-2"], IDS))
    assert entry["prompts"] == {"prompt-1": "P1", "prompt-2": "P2"} and entry["pdfs"] == ["A.pdf", "B.pdf"]
    assert entry["pdf"] == "A.pdf, B.pdf" and entry["max_out"] == 131072 and entry["think"] == "low"
    assert read_history(home) == [entry] and "state" not in entry  # läuft noch: Endzustand kommt später


def test_a_failing_first_start_raises_the_original_error_and_stores_nothing(app, rt, home):
    fake = FakeGemini(interaction_create_errors={0: InteractionsError(400, "max_output_tokens zu groß")})
    with pytest.raises(InteractionsError):
        start(app, rt, fake, ["P1", "P2"])
    assert fake.cancelled_interactions == [] and read_history(home) == []


def test_a_failing_later_start_cancels_the_ones_already_running(app, rt, home):
    fake = FakeGemini(interaction_create_errors={1: InteractionsError(400, "ungültig")})
    with pytest.raises(RuntimeError) as error:
        start(app, rt, fake, ["P1", "P2", "P3"])
    assert "Prompt 2 von 3" in str(error.value) and "abgebrochen" in str(error.value)
    assert fake.cancelled_interactions == [IDS[0]] and read_history(home) == []


def test_rate_limited_starts_are_retried(app, rt, fake_time):
    fake = FakeGemini(interaction_create_errors={0: InteractionsError(429), 1: InteractionsError(429)})
    start(app, rt, fake)
    assert fake.interaction_create_calls == 3 and fake_time.sleeps == [10, 20]


@pytest.mark.parametrize("code", [500, 503, 504])
def test_server_errors_are_not_retried_because_the_interaction_may_exist_already(app, rt, fake_time, code):
    fake = FakeGemini(interaction_create_errors={0: InteractionsError(code)})
    with pytest.raises(InteractionsError):
        start(app, rt, fake)
    assert fake.interaction_create_calls == 1 and fake_time.sleeps == []


def test_a_start_gives_up_after_three_attempts(app, rt, fake_time):
    errors = {n: InteractionsError(429, "zu viele Anfragen") for n in range(5)}
    fake = FakeGemini(interaction_create_errors=errors)
    with pytest.raises(InteractionsError):
        start(app, rt, fake)
    assert fake.interaction_create_calls == 3 and fake_time.sleeps == [10, 20]


def test_other_start_errors_are_not_retried(app, rt, fake_time):
    fake = FakeGemini(interaction_create_errors={0: ApiError(400)})  # der Code darf auch als code gemeldet werden
    with pytest.raises(ApiError):
        start(app, rt, fake)
    assert fake.interaction_create_calls == 1 and fake_time.sleeps == []


# ---------- Abfrage ----------
def test_finished_interactions_become_rows_in_the_common_form(app):
    fake = FakeGemini(interaction_results=[{"input_tokens": 1000, "cached": 800, "thoughts": 30, "output": 7}, {}])
    state, rows, error, api_model = app.fetch_background(fake, background_entry())
    assert (state, error, api_model) == ("completed", None, "gemini-4-carbon")
    first = rows[0]
    assert (first["key"], first["prompt"], first["text"]) == ("prompt-1", "Eins", "Hintergrund-Antwort 1")
    assert (first["in"], first["cached"], first["think"], first["out"]) == (1000, 800, 30, 7)
    assert (first["model"], first["finish"], first["error"]) == ("gemini-4-carbon", "STOP", None)
    assert first["usage"]["total_tokens"] == 1037
    assert [r["key"] for r in rows] == ["prompt-1", "prompt-2"] and rows[1]["prompt"] == "Zwei"


def test_a_running_interaction_has_no_row_and_finished_ones_are_not_fetched_again(app):
    fake = FakeGemini(interaction_results=[{}, {"status": "in_progress"}])
    entry = background_entry()
    state, rows, error, _ = app.fetch_background(fake, entry)
    assert state == "in_progress" and error is None and [r["key"] for r in rows] == ["prompt-1"]
    app.fetch_background(fake, entry, rows)  # zweite Abfrage: nur noch die laufende
    assert fake.interaction_gets == [IDS[0], IDS[1], IDS[1]]
    fake.interaction_results[1] = {}
    state, rows, _, _ = app.fetch_background(fake, entry, rows)
    assert state == "completed" and [r["key"] for r in rows] == ["prompt-1", "prompt-2"]
    assert fake.interaction_gets.count(IDS[0]) == 1


def test_queued_interactions_count_as_running(app):
    fake = FakeGemini(interaction_results=[{"status": "queued"}, {}])
    state, rows, _, _ = app.fetch_background(fake, background_entry())
    assert state == "in_progress" and [r["key"] for r in rows] == ["prompt-2"]


def test_the_text_of_a_long_decode_answer_is_joined_over_all_hops(app):
    fake = FakeGemini(interaction_results=[{"text": "Teil", "hops": 3}, {}])
    _, rows, _, _ = app.fetch_background(fake, background_entry())
    assert rows[0]["text"] == "Teil (Teil 1)Teil (Teil 2)Teil (Teil 3)"


def test_incomplete_means_max_tokens_and_without_text_an_error_like_in_the_batch(app):
    fake = FakeGemini(interaction_results=[{"status": "incomplete", "text": "", "thoughts": 65000, "output": 0},
                                           {"status": "incomplete", "text": "Halbe Antwort"}])
    _, rows, _, _ = app.fetch_background(fake, background_entry())
    assert rows[0]["finish"] == "MAX_TOKENS" and rows[0]["text"] == ""
    assert rows[0]["error"] == "Keine Antwort (finishReason: MAX_TOKENS)" and rows[0]["think"] == 65000
    assert rows[1]["finish"] == "MAX_TOKENS" and rows[1]["text"] == "Halbe Antwort" and rows[1]["error"] is None


def test_failed_interactions_carry_their_error_and_one_failure_does_not_fail_the_entry(app):
    errors = [{"code": "internal", "message": "Boom"}]
    fake = FakeGemini(interaction_results=[{"status": "failed", "text": "", "errors": errors}, {}])
    state, rows, error, _ = app.fetch_background(fake, background_entry())
    assert state == "completed" and error is None and rows[0]["finish"] == "FAILED" and rows[0]["error"] == "Boom"
    fake = FakeGemini(interaction_results=[{"status": "failed", "text": "", "errors": errors}] * 2)
    state, rows, error, _ = app.fetch_background(fake, background_entry())
    assert state == "failed" and error == "Boom"


def test_a_failed_interaction_without_a_message_still_says_so(app):
    row_ = app.parse_interaction(make_interaction("", status="failed"), "prompt-1", {})
    assert row_["finish"] == "FAILED" and row_["error"] == "Die Interaktion ist fehlgeschlagen."


def test_cancelled_interactions_make_a_cancelled_entry(app):
    fake = FakeGemini(interaction_results=[{"status": "cancelled", "text": ""}] * 2)
    state, rows, error, _ = app.fetch_background(fake, background_entry())
    assert state == "cancelled" and error is None and {r["finish"] for r in rows} == {"CANCELLED"}


def test_an_unexpected_final_status_ends_the_polling_and_is_shown_as_the_reason(app):
    fake = FakeGemini(interaction_results=[{"status": "requires_continuation", "text": ""}, {}])
    state, rows, _, _ = app.fetch_background(fake, background_entry())
    assert state == "completed" and rows[0]["finish"] == "REQUIRES_CONTINUATION" and rows[0]["error"]


def test_a_vanished_interaction_becomes_an_error_row_but_other_errors_stop_the_fetch(app):
    fake = FakeGemini(interaction_results=[InteractionsError(404, "nicht gefunden"), {}])
    state, rows, _, _ = app.fetch_background(fake, background_entry())
    assert state == "completed" and "nicht mehr" in rows[0]["error"] and rows[1]["text"]
    with pytest.raises(InteractionsError):
        app.fetch_background(FakeGemini(interaction_results=[InteractionsError(403, "Zugriff verweigert")]),
                             background_entry())
    with pytest.raises(ApiError):  # auch Fehler, die den Code als code melden
        app.fetch_background(FakeGemini(interaction_results=[ApiError(500)]), background_entry())


# ---------- Zeilenparser ----------
def test_parse_interaction_reads_sdk_objects_and_plain_dicts_alike(app):
    sdk_object = make_interaction("Text", input_tokens=500, cached=100, thoughts=20, output=9)
    from_object = app.parse_interaction(sdk_object, "prompt-1", {"prompt-1": "Frage"})
    from_dict = app.parse_interaction(sdk_object.model_dump(mode="json", exclude_none=True), "prompt-1",
                                      {"prompt-1": "Frage"})
    assert from_object == from_dict
    assert (from_object["in"], from_object["cached"], from_object["think"], from_object["out"]) == (500, 100, 20, 9)
    assert from_object["prompt"] == "Frage" and from_object["model"] == "gemini-4-carbon"


def test_parse_interaction_falls_back_to_output_text_without_steps_and_reads_camel_case(app):
    data = {"status": "completed", "output_text": "Nur Text", "usage": {"totalInputTokens": 7, "totalOutputTokens": 3}}
    parsed = app.parse_interaction(data, "prompt-1", {})
    assert parsed["text"] == "Nur Text" and parsed["in"] == 7 and parsed["out"] == 3 and parsed["error"] is None
    assert parsed["model"] == "unbekannt" and parsed["prompt"] == ""


def test_the_text_of_thought_steps_and_user_input_is_never_part_of_the_answer(app):
    parsed = app.parse_interaction(make_interaction("Antwort"), "prompt-1", {})
    assert parsed["text"] == "Antwort"  # „Frage“ (user_input) und der Denk-Schritt fehlen


# ---------- Abbruch ----------
def test_cancel_background_cancels_every_interaction(app):
    fake = FakeGemini()
    app.cancel_background(fake, background_entry())
    assert fake.cancelled_interactions == IDS


def test_cancel_background_reports_an_error_only_if_nothing_could_be_cancelled(app):
    fake = FakeGemini(interaction_cancel_error=InteractionsError(400, "schon beendet"))
    with pytest.raises(InteractionsError):
        app.cancel_background(fake, background_entry())
    partial = FakeGemini()
    original = partial.interactions.cancel

    def cancel_second_only(id, **kwargs):
        if id == IDS[0]:
            raise InteractionsError(400, "schon beendet")
        return original(id)

    partial.interactions.cancel = cancel_second_only
    app.cancel_background(partial, background_entry())  # ein Abbruch gelang: kein Fehler
    assert partial.cancelled_interactions == [IDS[1]]


# ---------- Betriebsart eines Eintrags ----------
@pytest.mark.parametrize("entry, expected", [({}, "batch"), ({"mode": "sync"}, "sync"),
                                             ({"mode": "background"}, "background"), ({"mode": "anders"}, "batch")])
def test_entry_mode(app, entry, expected):
    assert app.entry_mode(entry) == expected


def test_cancel_batch_sends_background_entries_to_the_interactions_api(app):
    fake = FakeGemini()
    app.cancel_batch(fake, background_entry())
    assert fake.cancelled_interactions == IDS and fake.cancelled == []


# ---------- Oberfläche ----------
@pytest.fixture
def use_fake(monkeypatch):
    def make(*args, **kwargs):
        return install(monkeypatch, FakeGemini(*args, **kwargs))
    return make


def background_mode(at):
    at.radio(key="mode").set_value(MODE_BG)
    at.run()


@ui
def test_defaults_in_background_mode(use_fake):
    use_fake()
    at = new_app()
    at.run()
    assert at.radio(key="mode").options == [MODE_BATCH, "Synchron (nacheinander, Cache-Treffer möglich)", MODE_BG]
    background_mode(at)
    assert not at.exception
    assert at.number_input(key="max_bg").value == 262144 and at.number_input(key="max_bg").max == 1_048_576
    assert not [n for n in at.number_input if n.key == "group"]  # Gespräche gibt es nur synchron
    assert button(at, "Im Hintergrund starten").disabled  # ohne Prompts
    assert any("Hintergrund-Interaktion" in c and "Standardpreis" in c for c in captions(at))
    assert at.text_input(key="label_background").value.startswith("background-")


@ui
def test_background_start_sends_the_pdfs_and_stores_the_entry(use_fake, home, app):
    fake = use_fake()
    at = new_app()
    at.run()
    background_mode(at)
    at.file_uploader[0].set_value([("Buch 1.pdf", b"PDF-1", PDF), ("Buch 2.pdf", b"PDF-2", PDF)])
    at.text_area(key="system").set_value("Sei kurz.")
    set_prompts(at, "Erster Prompt\n---\nZweiter Prompt")
    click(at, "Im Hintergrund starten")
    assert not at.exception and not at.error and "läuft bei Google" in at.success[0].value
    assert fake.uploads == [b"PDF-1", b"PDF-2"] and fake.batch_requests == [] and fake.created_batches == []
    assert [r["input"][-1]["text"] for r in fake.interaction_requests] == ["Erster Prompt", "Zweiter Prompt"]
    first = fake.interaction_requests[0]
    assert [p["type"] for p in first["input"]] == ["document", "document", "text"]
    assert first["system_instruction"] == "Sei kurz." and first["background"] is True
    assert first["generation_config"] == {"max_output_tokens": 262144, "thinking_level": "high"}
    entry = read_history(home)[0]
    assert entry["mode"] == "background" and entry["pdf"] == "Buch 1.pdf, Buch 2.pdf" and "state" not in entry
    assert entry["interactions"] == dict(zip(["prompt-1", "prompt-2"], IDS))
    assert at.session_state["sel"] == app.entry_label(entry)  # der neue Eintrag ist im Jobs-Tab vorausgewählt


@ui
def test_a_changed_token_limit_is_sent(use_fake):
    fake = use_fake()
    at = new_app()
    at.run()
    background_mode(at)
    at.number_input(key="max_bg").set_value(1_000_000)
    set_prompts(at, "Frage")
    click(at, "Im Hintergrund starten")
    assert fake.interaction_requests[0]["generation_config"]["max_output_tokens"] == 1_000_000


@ui
def test_a_failing_background_start_shows_the_error_and_stores_nothing(use_fake, home):
    use_fake(interaction_create_errors={0: InteractionsError(400, "Ungültige Anfrage")})
    at = new_app()
    at.run()
    background_mode(at)
    set_prompts(at, "Frage")
    click(at, "Im Hintergrund starten")
    assert any("Start fehlgeschlagen" in e.value and "400" in e.value for e in at.error)
    assert read_history(home) == []


@ui
def test_the_jobs_tab_shows_partial_results_and_saves_the_final_one(use_fake, home):
    write_history(home, [background_entry()])
    fake = use_fake(interaction_results=[{}, {"status": "in_progress"}])
    at = new_app(TAB_JOBS)
    at.run()
    assert not at.exception and any("Hintergrund (Long Decode)" in c for c in captions(at))
    assert any("in_progress" in m and "1 von 2 Prompts fertig" in m for m in markdown(at))
    assert "Hintergrund-Antwort 1" in markdown(at) and "state" not in read_history(home)[0]
    fake.interaction_results[1] = {}
    click(at, "Status prüfen / Ergebnisse laden")
    saved = read_history(home)[0]
    assert saved["state"] == "completed" and saved["api_model"] == "gemini-4-carbon" and saved["error"] is None
    assert [r["text"] for r in saved["rows"]] == ["Hintergrund-Antwort 1", "Hintergrund-Antwort 2"]
    assert fake.interaction_gets.count(IDS[0]) == 1  # die fertige Interaktion wurde nicht erneut geladen
    at.run()
    assert any("2 von 2 Prompts fertig" in m for m in markdown(at))


@ui
def test_a_finished_background_entry_reopens_without_the_api(use_fake, home):
    write_history(home, [finished_background()])
    fake = use_fake(get_error=ApiError(500))
    at = new_app(TAB_JOBS)
    at.run()
    assert not at.exception and not at.error and fake.interaction_gets == [] and fake.get_calls == 0
    assert any("Gesichertes Endergebnis" in c for c in captions(at))
    assert "Gesicherte Antwort" in markdown(at)


@ui
def test_a_vanished_background_entry_stops_the_polling_and_shows_the_error(use_fake, home):
    write_history(home, [background_entry()])
    fake = use_fake(interaction_results=[InteractionsError(403, "Zugriff verweigert")])
    at = new_app(TAB_JOBS)
    at.run()
    assert any("Abruf fehlgeschlagen" in e.value and "gestoppt" in e.value for e in at.error)
    at.run()
    assert fake.interaction_gets == [IDS[0]]  # kein weiterer Versuch


@ui
def test_the_background_cancel_button_cancels_every_interaction(use_fake, home):
    write_history(home, [background_entry()])
    fake = use_fake(interaction_results=[{"status": "in_progress"}] * 2)
    at = new_app(TAB_JOBS)
    at.run()
    assert not [b for b in at.button if b.label == "Batch abbrechen"]  # eigene Beschriftung
    click(at, "Läufe abbrechen")
    assert fake.cancelled_interactions == IDS and any("Abbruch angefordert" in w.value for w in at.warning)


@ui
def test_reuse_inputs_of_a_background_entry_switches_to_background_mode(use_fake, home):
    entry = background_entry(think="medium", system="Kurz.", max_out=131072)
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key="reuse:" + entry["name"]).click()
    at.run()
    assert not at.exception and at.session_state["view"] == TAB_NEW
    assert at.radio(key="mode").value == MODE_BG and at.selectbox(key="think").value == "medium"
    assert at.text_area(key="raw").value == "Eins\n---\nZwei" and at.text_area(key="system").value == "Kurz."
    assert at.number_input(key="max_bg").value == 131072


@ui
def test_reuse_inputs_keeps_the_background_limit_within_its_range(use_fake, home):
    entry = background_entry(max_out=5_000_000)
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key="reuse:" + entry["name"]).click()
    at.run()
    assert at.number_input(key="max_bg").value == 1_048_576


@ui
def test_continuing_a_background_answer_switches_to_background_mode(use_fake, home, app):
    entry = finished_background()
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key=f"talk:{entry['name']}:prompt-1").click()
    at.run()
    assert not at.exception and at.radio(key="mode").value == MODE_BG
    assert "Gesicherte Antwort" in at.text_area(key="raw").value


@ui
def test_a_truncated_batch_answer_points_to_the_background_mode(use_fake, home):
    truncated = row(text="", finish="MAX_TOKENS", error="Keine Antwort (finishReason: MAX_TOKENS)", think=32765, out=3)
    write_history(home, [batch_entry(state="JOB_STATE_SUCCEEDED", rows=[truncated], error=None,
                                     api_model="models/gemini-4-carbon")])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    assert any("Abgeschnitten" in w.value for w in at.warning)
    assert any("Gemini-Batch ist auf 32,768" in c and "„Hintergrund“" in c for c in captions(at))


@ui
def test_a_truncated_background_answer_gets_no_such_pointer(use_fake, home):
    truncated = row(text="", finish="MAX_TOKENS", error="Keine Antwort (finishReason: MAX_TOKENS)")
    write_history(home, [finished_background(rows=[truncated])])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    assert any("Abgeschnitten" in w.value for w in at.warning)
    assert not any("Gemini-Batch ist auf" in c for c in captions(at))


@ui
def test_a_batch_entry_still_has_the_batch_cancel_button(use_fake, home):
    write_history(home, [batch_entry()])
    use_fake(batch_state="JOB_STATE_RUNNING")
    at = new_app(TAB_JOBS)
    at.run()
    assert button(at, "Batch abbrechen") and not [b for b in at.button if b.label == "Läufe abbrechen"]
