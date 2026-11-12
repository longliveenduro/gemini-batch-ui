"""Oberflächentests mit Streamlits AppTest und Ersatzdiensten. Langsamer als die übrigen Tests (Marker „ui“)."""
import shutil

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from fakes import ApiError, FakeDrive, FakeGemini, install, install_drive
from helpers import (APP_PATH, MODE_BATCH, MODE_SYNC, PACKAGE, PACKAGE_DIR, TAB_JOBS, TAB_NEW, batch_entry, button,
                     captions, click, finished_batch, markdown, new_app, read_history, row, set_prompts, sync_entry,
                     wait_for_run_end, wait_until, write_history)

pytestmark = pytest.mark.ui

PDF = "application/pdf"
needs_tab_state = pytest.mark.skipif(tuple(int(x) for x in st.__version__.split(".")[:2]) < (1, 55),
                                     reason="Die Tab-Erkennung braucht Streamlit ab 1.55")


@pytest.fixture
def use_fake(monkeypatch):
    """use_fake(...) installiert einen FakeGemini (Argumente wie dort) und liefert ihn zurück."""
    def make(*args, **kwargs):
        return install(monkeypatch, FakeGemini(*args, **kwargs))
    return make


def sync_mode(at):
    at.radio(key="mode").set_value(MODE_SYNC)
    at.run()


# ---------- Formular ----------
def test_without_an_api_key_the_app_asks_for_it(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    at = new_app()
    at.run()
    assert not at.exception and any("API-Key" in i.value for i in at.info) and not at.button


def test_the_api_key_from_the_environment_is_prefilled_and_can_be_replaced(use_fake):
    fake = use_fake()
    at = new_app()
    at.run()
    key_field = [t for t in at.text_input if t.label == "Gemini API-Key"][0]
    assert key_field.value == "test-key" and fake.client_kwargs[-1]["api_key"] == "test-key"
    key_field.set_value("anderer-key")
    at.run()
    assert fake.client_kwargs[-1]["api_key"] == "anderer-key"


def test_defaults_in_batch_mode(use_fake):
    use_fake()
    at = new_app()
    at.run()
    assert not at.exception
    assert at.radio(key="mode").value == MODE_BATCH and at.selectbox(key="think").value == "high"
    assert at.number_input(key="max_batch").value == 32768 and at.number_input(key="max_batch").max == 32768
    assert not [n for n in at.number_input if n.key == "group"]  # Gesprächsgröße gibt es nur synchron
    assert button(at, "Batch starten").disabled  # ohne Prompts
    assert any("Kein PDF angehängt" in c for c in captions(at))


def test_defaults_in_sync_mode(use_fake):
    use_fake()
    at = new_app()
    at.run()
    sync_mode(at)
    assert at.number_input(key="group").value == 1
    assert at.number_input(key="max_sync").value == 65536 and at.number_input(key="max_sync").max == 1_048_576
    assert button(at, "Synchron starten").disabled


@pytest.mark.parametrize("text, expected", [
    ("A\n---\nB\n  ---  \nC", 3),
    ("eine --- Zeile", 1),  # --- mitten im Text trennt nichts
    ("---\nA\n---\n\n---", 1),  # leere Prompts werden ignoriert
])
def test_prompts_are_split_on_separator_lines(use_fake, text, expected):
    use_fake()
    at = new_app()
    at.run()
    set_prompts(at, text)
    assert any(f"{expected} Prompt(s) erkannt" in c for c in captions(at))
    assert not button(at, "Batch starten").disabled


# ---------- Start im Batch-Modus ----------
def test_batch_start_sends_both_pdfs_and_stores_the_entry(use_fake, home, app):
    fake = use_fake()
    at = new_app()
    at.run()
    at.file_uploader[0].set_value([("Buch 1.pdf", b"PDF-1", PDF), ("Buch 2.pdf", b"PDF-2", PDF)])
    at.text_area(key="system").set_value("Sei kurz.")
    set_prompts(at, "Erster Prompt\n---\nZweiter Prompt")
    click(at, "Batch starten")
    assert not at.exception and not at.error and "läuft" in at.success[0].value
    assert fake.uploads == [b"PDF-1", b"PDF-2"]
    request = fake.batch_requests[0]["request"]
    parts = request["contents"][0]["parts"]
    assert [("file" if "file_data" in p else p["text"]) for p in parts] == ["file", "file", "Erster Prompt"]
    assert request["system_instruction"] == {"parts": [{"text": "Sei kurz."}]}
    assert request["generation_config"] == {"max_output_tokens": 32768, "thinking_config": {"thinking_level": "high"}}
    entry = read_history(home)[0]
    assert entry["pdf"] == "Buch 1.pdf, Buch 2.pdf" and entry["system"] == "Sei kurz." and "state" not in entry
    assert entry["prompts"] == {"prompt-1": "Erster Prompt", "prompt-2": "Zweiter Prompt"}
    assert at.session_state["sel"] == app.entry_label(entry)  # der neue Eintrag ist im Jobs-Tab vorausgewählt
    assert (home / "data" / "pdf_files.json").exists() and list((home / "data").glob("requests_*.jsonl"))


def test_the_model_field_decides_which_model_is_used(use_fake):
    fake = use_fake()
    at = new_app()
    at.run()
    [t for t in at.text_input if t.label == "Modell"][0].set_value("gemini-anderes-modell")
    set_prompts(at, "Frage")
    click(at, "Batch starten")
    assert fake.created_batches[0][0] == "gemini-anderes-modell"


def test_identical_pdfs_are_sent_only_once(use_fake):
    fake = use_fake()
    at = new_app()
    at.run()
    at.file_uploader[0].set_value([("Original.pdf", b"GLEICH", PDF), ("Kopie.pdf", b"GLEICH", PDF)])
    set_prompts(at, "Frage")
    click(at, "Batch starten")
    assert fake.uploads == [b"GLEICH"]
    assert len(fake.batch_requests[0]["request"]["contents"][0]["parts"]) == 2  # 1 PDF + Prompt


def test_a_failing_start_shows_the_error_and_stores_nothing(use_fake, home):
    use_fake(create_error=ApiError(400, "Ungültige Anfrage"))
    at = new_app()
    at.run()
    set_prompts(at, "Frage")
    click(at, "Batch starten")
    assert any("Start fehlgeschlagen" in e.value and "Ungültige Anfrage" in e.value for e in at.error)
    assert read_history(home) == []


# ---------- Start im synchronen Modus ----------
def test_sync_run_shows_results_and_the_cache_sum(use_fake, home):
    use_fake([{"text": "Erste Antwort", "cached": 0}, {"text": "Zweite Antwort", "cached": 79000}])
    at = new_app()
    at.run()
    sync_mode(at)
    set_prompts(at, "Eins\n---\nZwei")
    click(at, "Synchron starten")
    assert not at.exception and "im Hintergrund" in at.success[0].value
    assert wait_for_run_end(home)["state"] == "DONE"
    at.session_state["view"] = TAB_JOBS
    at.run()
    assert not at.exception
    assert any("davon aus dem Cache: 79,000" in c for c in captions(at))
    assert any("fertig" in m and "2 von 2" in m for m in markdown(at))
    assert {"Erste Antwort", "Zweite Antwort"} <= set(markdown(at))
    assert not any("Automatische Prüfung" in c for c in captions(at))  # nichts läuft mehr


def test_conversations_via_the_form(use_fake, home):
    fake = use_fake()
    at = new_app()
    at.run()
    sync_mode(at)
    at.number_input(key="group").set_value(2)
    set_prompts(at, "Eins\n---\nZwei\n---\nDrei")
    assert any("Sie bilden 2 Gespräche mit je 2 Prompts (das letzte mit 1)" in c for c in captions(at))
    click(at, "Synchron starten")
    wait_for_run_end(home)
    assert [c["contents"] for c in fake.calls] == [
        [("user", ["Eins"])],
        [("user", ["Eins"]), ("model", ["Antwort 1"]), ("user", ["Zwei"])],
        [("user", ["Drei"])],
    ]
    at.session_state["view"] = TAB_JOBS
    at.run()
    assert any("synchron, 2 Prompts pro Gespräch" in c for c in captions(at))


def test_pdfs_from_uploads_and_drive_are_combined_in_order(use_fake, monkeypatch, home):
    fake = use_fake()
    install_drive(monkeypatch, home, FakeDrive({
        "ID_A_000000000000000001": ("Drive A.pdf", PDF, b"PDF-DRIVE-A"),
        "ID_B_000000000000000002": ("Drive B.pdf", PDF, b"PDF-DRIVE-B"),
        "ID_C_000000000000000003": ("Drive C.pdf", PDF, b"PDF-DRIVE-C"),
    }))
    at = new_app()
    at.run()
    sync_mode(at)
    at.file_uploader[0].set_value([("Lokal 1.pdf", b"PDF-LOCAL-1", PDF), ("Lokal 2.pdf", b"PDF-LOCAL-2", PDF)])
    click(at, "Meine PDFs aus Drive laden")
    at.multiselect[0].set_value(list(at.session_state["drive_pdfs"])[:2])  # Drive A und B
    [t for t in at.text_area if t.label.startswith("Oder Drive-Links")][0].set_value(
        "https://drive.google.com/file/d/ID_C_000000000000000003/view\nID_A_000000000000000001")  # C neu, A doppelt
    set_prompts(at, "Eins")
    order = [c for c in captions(at) if c.startswith("Reihenfolge")][0]
    assert order.index("Lokal 1.pdf") < order.index("Lokal 2.pdf") < order.index("Drive A.pdf") < order.index("Drive B.pdf")
    click(at, "Synchron starten")
    wait_for_run_end(home)
    assert fake.uploads == [b"PDF-LOCAL-1", b"PDF-LOCAL-2", b"PDF-DRIVE-A", b"PDF-DRIVE-B", b"PDF-DRIVE-C"]
    assert fake.calls[0]["contents"] == [("user", [f"PDF:pdf{i}" for i in range(1, 6)] + ["Eins"])]
    assert read_history(home)[0]["pdf"] == "Lokal 1.pdf, Lokal 2.pdf, Drive A.pdf, Drive B.pdf, Drive C.pdf"


def test_the_cancel_button_stops_a_running_sync_run(use_fake, home):
    use_fake(delay=1.0)
    at = new_app()
    at.run()
    sync_mode(at)
    set_prompts(at, "A\n---\nB\n---\nC\n---\nD")
    click(at, "Synchron starten")
    wait_until(lambda: read_history(home) and read_history(home)[0]["rows"], message="keine erste Zeile")
    at.session_state["view"] = TAB_JOBS
    at.run()
    click(at, "Lauf abbrechen")
    entry = wait_for_run_end(home)
    assert entry["state"] == "CANCELLED" and 1 <= len(entry["rows"]) < 4


@pytest.mark.parametrize("finish, warning", [("MAX_TOKENS", "Abgeschnitten"), ("SAFETY", "SAFETY")])
def test_unusual_finish_reasons_are_flagged_and_durations_shown(use_fake, home, finish, warning):
    use_fake([{"text": "Teilantwort", "finish": finish}])
    at = new_app()
    at.run()
    sync_mode(at)
    set_prompts(at, "Eins")
    click(at, "Synchron starten")
    wait_for_run_end(home)
    at.session_state["view"] = TAB_JOBS
    at.run()
    assert any(warning in w.value for w in at.warning)
    assert any("Dauer" in c and f"Ende: {finish}" in c for c in captions(at))


def test_a_normal_stop_raises_no_warning(use_fake, home):
    use_fake()
    at = new_app()
    at.run()
    sync_mode(at)
    set_prompts(at, "Eins")
    click(at, "Synchron starten")
    wait_for_run_end(home)
    at.session_state["view"] = TAB_JOBS
    at.run()
    assert not at.warning


# ---------- Jobs-Tab: Batches ----------
def test_a_finished_batch_is_saved_locally_and_reopens_without_the_api(use_fake, home):
    write_history(home, [batch_entry()])
    fake = use_fake(batch_cached=60)
    fake.batch_requests = [{"key": "prompt-1"}]  # der Fake liefert Ergebnisse zu den „gesendeten“ Anfragen
    at = new_app(TAB_JOBS)
    at.run()
    at.run()
    saved = read_history(home)[0]
    assert saved["state"] == "JOB_STATE_SUCCEEDED" and saved["api_model"] == "models/gemini-4-carbon"
    assert saved["rows"][0]["cached"] == 60 and saved["rows"][0]["text"] == "Batch-Antwort prompt-1"

    broken = use_fake(get_error=ApiError(500))  # neue Sitzung, API unbrauchbar: darf nicht gefragt werden
    again = new_app(TAB_JOBS)
    again.run()
    assert broken.get_calls == 0 and not again.error and not again.exception
    assert any("Gesichertes Endergebnis" in c for c in captions(again))
    assert "Batch-Antwort prompt-1" in markdown(again)
    assert fake.get_calls >= 1


@needs_tab_state
def test_polling_runs_only_while_the_jobs_tab_is_open(use_fake, home):
    write_history(home, [batch_entry()])
    fake = use_fake(batch_state="JOB_STATE_RUNNING")
    at = new_app()
    at.run()
    assert fake.get_calls == 0  # Tab „Neue Anfrage“ ist offen
    at.session_state["view"] = TAB_JOBS
    at.run()
    assert fake.get_calls >= 1 and any("Automatische Prüfung" in c for c in captions(at))
    assert any("JOB_STATE_RUNNING" in m for m in markdown(at))
    at.session_state["view"] = TAB_NEW
    at.run()
    calls = fake.get_calls
    at.run()
    assert fake.get_calls == calls  # wieder im anderen Tab: keine Abfragen


def test_a_vanished_batch_stops_the_polling_and_shows_the_error(use_fake, home):
    write_history(home, [batch_entry()])
    fake = use_fake(get_error=ApiError(404, "nicht gefunden"))
    at = new_app(TAB_JOBS)
    at.run()
    assert any("Abruf fehlgeschlagen" in e.value and "gestoppt" in e.value for e in at.error)
    assert not any("Automatische Prüfung" in c for c in captions(at))
    at.run()
    assert fake.get_calls == 1  # kein weiterer Versuch


def test_transient_fetch_errors_stop_the_polling_only_after_three_in_a_row(use_fake, home):
    write_history(home, [batch_entry()])
    use_fake(get_error=ApiError(503, "überlastet"))
    at = new_app(TAB_JOBS)
    at.run()
    assert any("Abruf fehlgeschlagen" in e.value and "gestoppt" not in e.value for e in at.error)
    assert any("Automatische Prüfung" in c for c in captions(at))
    at.run()
    at.run()
    assert any("gestoppt" in e.value for e in at.error)


def test_the_batch_cancel_button_calls_the_api(use_fake, home):
    write_history(home, [batch_entry()])
    fake = use_fake(batch_state="JOB_STATE_RUNNING")
    at = new_app(TAB_JOBS)
    at.run()
    click(at, "Batch abbrechen")
    assert fake.cancelled == ["batches/abc123def456"] and any("Abbruch angefordert" in w.value for w in at.warning)


# ---------- Jobs-Tab: Verlauf ----------
def test_a_new_entry_is_preselected_over_an_older_selection(use_fake, home, app):
    old = sync_entry()
    write_history(home, [old])
    use_fake()
    at = new_app()
    at.run()
    at.session_state["sel"] = app.entry_label(old)
    sync_mode(at)
    set_prompts(at, "Frage")
    click(at, "Synchron starten")
    wait_for_run_end(home)
    newest = read_history(home)[0]
    assert newest["name"] != old["name"] and at.session_state["sel"] == app.entry_label(newest)
    at.session_state["view"] = TAB_JOBS
    at.run()
    assert any(c.startswith(newest["name"]) for c in captions(at))


def test_reuse_inputs_fills_the_form_and_switches_the_tab(use_fake, home):
    entry = sync_entry()
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key="reuse:" + entry["name"]).click()
    at.run()
    assert not at.exception and at.session_state["view"] == TAB_NEW
    assert at.radio(key="mode").value == MODE_SYNC
    assert at.text_area(key="raw").value == "P1\n---\nP2" and at.text_area(key="system").value == "Sei kurz."
    assert at.selectbox(key="think").value == "low"
    assert at.number_input(key="group").value == 2 and at.number_input(key="max_sync").value == 4096
    assert any("übernommen" in i.value and "a.pdf, b.pdf" in i.value for i in at.info)
    at.run()
    assert not at.info  # der Hinweis erscheint nur einmal


def test_reuse_inputs_of_a_batch_entry_switches_to_batch_mode(use_fake, home):
    entry = batch_entry(think="medium", system="Kurz.")
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key="reuse:" + entry["name"]).click()
    at.run()
    assert at.radio(key="mode").value == MODE_BATCH and at.selectbox(key="think").value == "medium"
    assert at.text_area(key="raw").value == "Eins" and at.number_input(key="max_batch").value == 32768


def test_delete_removes_the_entry_locally_only(use_fake, home):
    entry = finished_batch()
    write_history(home, [entry])
    fake = use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key="del:" + entry["name"]).click()
    at.run()
    assert read_history(home) == [] and any("Noch nichts gestartet" in i.value for i in at.info)
    assert fake.cancelled == [] and not at.exception


def test_a_corrupt_history_file_is_backed_up(use_fake, home):
    path = home / "data" / "batch_jobs.json"
    path.parent.mkdir(parents=True)
    path.write_text("{kaputt")
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    assert not at.exception and any("Noch nichts gestartet" in i.value for i in at.info)
    assert [p.read_text() for p in (home / "data").glob("batch_jobs.defekt-*.json")] == ["{kaputt"]


def test_legacy_files_in_the_project_folder_are_moved_on_start(use_fake, home):
    import json
    (home / "batch_jobs.json").write_text(json.dumps([finished_batch()]))
    (home / "token.json").write_text("{}")
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    assert not at.exception and "Gesicherte Antwort" in markdown(at)
    assert (home / "data" / "batch_jobs.json").exists() and (home / "conf" / "token.json").exists()
    assert not (home / "batch_jobs.json").exists() and not (home / "token.json").exists()
    assert any("einsortiert" in t.value for t in at.toast)


def test_folders_are_created_beside_src_whatever_the_working_directory(tmp_path, monkeypatch, use_fake):
    project = tmp_path / "projekt"
    (project / "src").mkdir(parents=True)
    shutil.copy(APP_PATH, project / "src" / "gemini_batch_ui.py")
    package_copy = project / "src" / PACKAGE  # das Paket liegt neben dem Hauptskript
    shutil.copytree(PACKAGE_DIR, package_copy, ignore=shutil.ignore_patterns("__pycache__"))
    elsewhere = tmp_path / "anderswo"
    elsewhere.mkdir()
    monkeypatch.delenv("GEMINI_UI_HOME")  # nur so zählt der Ort der Datei
    monkeypatch.chdir(elsewhere)
    monkeypatch.syspath_prepend(str(project / "src"))  # wie streamlit run: der Ordner des Hauptskripts steht vorn
    use_fake()
    at = AppTest.from_file(str(project / "src" / "gemini_batch_ui.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert (project / "conf").is_dir() and (project / "data").is_dir()
    assert not (project / "src" / "conf").exists() and not (project / "src" / "data").exists()
    assert list(elsewhere.iterdir()) == []  # im Arbeitsordner entsteht nichts


def test_entries_from_older_versions_still_open(use_fake, home):
    minimal_row = {"key": "prompt-1", "prompt": "Eins", "text": "Alte Antwort", "error": None,
                   "in": 5, "cached": 0, "think": 0, "out": 1}  # ohne model, finish, usage, secs
    # Einträge von vor der Umstellung auf Carbon behalten ihr Modell (gemini-4-argon) und lassen sich weiter öffnen.
    write_history(home, [
        {"name": "batches/uralt000000", "label": "uralt", "model": "gemini-4-argon", "pdf": None,
         "prompts": {"prompt-1": "Eins"}, "created": "2026-10-01T09:00:00"},  # ohne think, max_out, system, pdfs, state
        {"name": "sync-20261001-100000-zzzz", "mode": "sync", "state": "DONE", "label": "gespraech", "model": "gemini-4-argon",
         "pdf": "a.pdf", "prompts": {"prompt-1": "Eins", "prompt-2": "Zwei"}, "chat": True, "rows": [minimal_row],
         "created": "2026-10-01T10:00:00"},  # Zwischenstand mit „chat“ statt „group“
    ])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()  # ausgewählt ist der erste Eintrag der Liste: der Batch ohne neuere Felder
    assert not at.exception and any("Denktiefe unbekannt" in c for c in captions(at))
    at.selectbox(key="sel").set_value([o for o in at.selectbox(key="sel").options if o.startswith("gespraech")][0])
    at.run()
    assert not at.exception
    assert any("synchron, 2 Prompts pro Gespräch" in c for c in captions(at)) and "Alte Antwort" in markdown(at)


# ---------- Jobs-Tab: Prompts im Wortlaut und Gespräche fortsetzen ----------
LONG_PROMPT = ("Erzähle Abschnitt 3 in einfacher Sprache nach und achte auf kurze Sätze und klare, einfache Wörter. "
               "Am Ende folgt eine Zusammenfassung in drei Sätzen.")


def codes(at):
    return [c.value for c in at.code]


def with_rows(entry, *rows):
    entry["rows"] = list(rows)
    return entry


def test_the_full_prompt_is_shown_only_on_demand(use_fake, home):
    assert len(LONG_PROMPT) > 90
    entry = with_rows(finished_batch(prompts={"prompt-1": LONG_PROMPT}), row(prompt=LONG_PROMPT))
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    assert not at.exception and not codes(at)  # nichts ungefragt
    assert at.expander[0].label == "prompt-1: " + LONG_PROMPT[:90]  # die Überschrift bleibt kurz
    at.toggle(key=f"prompt:{entry['name']}:prompt-1").set_value(True)
    at.run()
    assert codes(at) == [LONG_PROMPT]


def test_the_sent_request_is_shown_in_detail_even_before_results_exist(use_fake, home):
    entry = batch_entry(prompts={"prompt-1": "Erster\nmit zwei Zeilen", "prompt-2": "Zweiter"}, system="Sei kurz.",
                        pdf="a.pdf, b.pdf", pdfs=["a.pdf", "b.pdf"])
    write_history(home, [entry])
    use_fake(batch_state="JOB_STATE_RUNNING")  # noch keine Ergebnisse
    at = new_app(TAB_JOBS)
    at.run()
    assert not at.exception and not codes(at)
    at.toggle(key="detail:" + entry["name"]).set_value(True)
    at.run()
    assert codes(at) == ["Sei kurz.", "Erster\nmit zwei Zeilen", "Zweiter"]
    assert any("1. a.pdf, 2. b.pdf" in m for m in markdown(at))


def test_request_details_name_the_conversations_and_a_missing_system_instruction(use_fake, home):
    entry = sync_entry(system="", prompts={"prompt-1": "A", "prompt-2": "B", "prompt-3": "C"}, group=2)
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    at.toggle(key="detail:" + entry["name"]).set_value(True)
    at.run()
    assert codes(at) == ["A", "B", "C"]
    assert "**prompt-2** (Gespräch 1, Runde 2)" in markdown(at) and "**prompt-3** (Gespräch 2, Runde 1)" in markdown(at)
    assert any("Keine Systemanweisung" in c for c in captions(at))


def test_a_continued_prompt_is_listed_by_its_new_part_and_says_so_in_detail(use_fake, home, app):
    sent = app.conversation_text([("Frage", "Antwort"), ("Frage 2", "Antwort 2")], "Neue Frage")
    entry = with_rows(finished_batch(prompts={"prompt-1": sent}), row(prompt=sent))
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    assert at.expander[0].label == "prompt-1: ↪ Neue Frage"
    at.toggle(key=f"prompt:{entry['name']}:prompt-1").set_value(True)
    at.run()
    assert codes(at) == [sent] and any("Verlauf von 2 Runde(n)" in c for c in captions(at))


def test_the_history_text_can_be_shown_for_copying(use_fake, home, app):
    entry = finished_batch()
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    assert not codes(at)
    at.toggle(key=f"hist:{entry['name']}:prompt-1").set_value(True)
    at.run()
    assert codes(at) == [app.conversation_text([("Eins", "Gesicherte Antwort")]).strip()]  # st.code kürzt Leerraum am Rand
    assert any("Verlauf aus 1 Runde(n)" in c for c in captions(at))


def test_a_row_without_an_answer_offers_no_continuation(use_fake, home):
    write_history(home, [with_rows(finished_batch(), row(text="", error="Blockiert"))])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    assert not at.exception and any("Blockiert" in e.value for e in at.error)
    assert not [b for b in at.button if b.label == "Gespräch fortsetzen"]
    assert not [t for t in at.toggle if t.label == "Verlaufstext zeigen"]


def test_continue_conversation_fills_the_form_with_the_history(use_fake, home, app):
    entry = sync_entry(rows=[row(key="prompt-1", prompt="P1", text="A1"), row(key="prompt-2", prompt="P2", text="A2")])
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key=f"talk:{entry['name']}:prompt-2").click()
    at.run()
    assert not at.exception and at.session_state["view"] == TAB_NEW
    assert at.text_area(key="raw").value == app.conversation_text([("P1", "A1"), ("P2", "A2")])  # beide Runden
    assert at.radio(key="mode").value == MODE_SYNC and at.text_area(key="system").value == "Sei kurz."
    assert at.selectbox(key="think").value == "low" and at.number_input(key="max_sync").value == 4096
    assert at.number_input(key="group").value == 1  # der Verlauf steckt im Prompt: keine Gesprächsgruppen
    # Der einmalige Hinweis (st.info) erscheint erst im echten Browser nach dem vollen Rerun, nicht im AppTest.
    assert any(app.TALK_NEW in w.value for w in at.warning) and button(at, "Synchron starten").disabled
    assert any("1 von 1 Prompt(s) setzen ein früheres Gespräch fort" in c for c in captions(at))
    set_prompts(at, at.text_area(key="raw").value + "Neuer Prompt")
    assert not [w for w in at.warning if app.TALK_NEW in w.value] and not button(at, "Synchron starten").disabled


def test_continuing_a_batch_answer_switches_to_batch_mode(use_fake, home):
    entry = finished_batch(think="medium")
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key=f"talk:{entry['name']}:prompt-1").click()
    at.run()
    assert at.radio(key="mode").value == MODE_BATCH and at.selectbox(key="think").value == "medium"
    assert button(at, "Batch starten").disabled  # unter der Marke fehlt der neue Prompt


def test_a_separator_line_in_an_answer_does_not_split_the_continued_prompt(use_fake, home):
    entry = with_rows(finished_batch(), row(text="Oben\n---\nUnten"))
    write_history(home, [entry])
    use_fake()
    at = new_app(TAB_JOBS)
    at.run()
    at.button(key=f"talk:{entry['name']}:prompt-1").click()
    at.run()
    assert not [line for line in at.text_area(key="raw").value.splitlines() if line.strip() == "---"]
    assert any("1 Prompt(s) erkannt" in c for c in captions(at))


def test_a_conversation_can_be_continued_again_and_again(use_fake, home, app):
    fake = use_fake()
    at = new_app()
    at.run()
    sync_mode(at)
    set_prompts(at, "Eins")
    click(at, "Synchron starten")
    wait_for_run_end(home)  # Antwort 1
    for rounds, follow_up in ((1, "Zwei"), (2, "Drei")):
        newest = read_history(home)[0]
        at.session_state["view"] = TAB_JOBS
        at.run()
        at.button(key=f"talk:{newest['name']}:prompt-1").click()
        at.run()
        raw = at.text_area(key="raw").value
        assert raw.count(app.TALK_BEGIN) == 1 and raw.count(app.TALK_INTRO) == 1  # nichts ist verschachtelt
        assert len(app.parse_conversation(raw)[0]) == rounds
        set_prompts(at, raw + follow_up)
        click(at, "Synchron starten")
        wait_for_run_end(home)
    sent = [call["contents"][0][1][0] for call in fake.calls]  # der Prompt jeder Anfrage (keine PDFs)
    assert sent[0] == "Eins"
    assert app.parse_conversation(sent[1]) == ([("Eins", "Antwort 1")], "Zwei")
    assert app.parse_conversation(sent[2]) == ([("Eins", "Antwort 1"), ("Zwei", "Antwort 2")], "Drei")
