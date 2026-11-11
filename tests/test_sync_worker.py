"""Tests des synchronen Modus: Hintergrund-Thread, Gespräche, Fehlerfälle, Abbruch, Zustände."""
import threading
from types import SimpleNamespace

import pytest

from fakes import ApiError, FakeGemini
from helpers import read_history, sync_entry, write_history

pytestmark = pytest.mark.usefixtures("fake_time")  # Wiederholungspausen kosten keine Zeit

PDF = "PDF:A"


def run(app, rt, fake, prompts, *, group=1, n_pdfs=1, system="", think="high", max_out=65536):
    """Startet einen synchronen Lauf, wartet auf den Thread und liefert Eintrag, Zustand und Zeilen."""
    pdfs = [SimpleNamespace(uri=f"https://example.test/v1beta/files/{c}") for c in "ABC"[:n_pdfs]]
    names = [f"{c}.pdf" for c in "ABC"[:n_pdfs]]
    entry = app.start_sync(fake, rt, "gemini-4-carbon", pdfs, names, prompts, system, think, max_out, "lauf", group)
    thread = rt.threads[entry["name"]]
    thread.join(timeout=20)
    assert not thread.is_alive(), "Der Hintergrund-Thread ist nicht fertig geworden"
    job, state = app.sync_view(rt, entry["name"])
    return SimpleNamespace(entry=entry, job=job, state=state, rows=job["rows"])


# ---------- Anfrageaufbau ----------
def test_independent_prompts_each_start_with_the_pdf(app, rt):
    fake = FakeGemini()
    result = run(app, rt, fake, ["P1", "P2", "P3"])
    assert result.state == "DONE" and len(result.rows) == 3
    assert [c["contents"] for c in fake.calls] == [[("user", [PDF, f"P{i}"])] for i in (1, 2, 3)]


def test_several_pdfs_precede_the_prompt_in_the_given_order(app, rt):
    fake = FakeGemini()
    run(app, rt, fake, ["P1"], n_pdfs=2)
    assert fake.calls[0]["contents"] == [("user", ["PDF:A", "PDF:B", "P1"])]


def test_without_pdfs_only_the_prompt_is_sent(app, rt):
    fake = FakeGemini()
    run(app, rt, fake, ["P1"], n_pdfs=0)
    assert fake.calls[0]["contents"] == [("user", ["P1"])]


def test_generation_config_is_sent_with_every_request(app, rt):
    fake = FakeGemini()
    run(app, rt, fake, ["P1", "P2"], system="Sei kurz.", think="high", max_out=65536)
    for call in fake.calls:
        cfg = call["config"]
        assert cfg.thinking_config.thinking_level.name == "HIGH"
        assert cfg.max_output_tokens == 65536
        assert cfg.http_options.timeout == app.SYNC_TIMEOUT_MS
        assert "Sei kurz." in str(cfg.system_instruction)
    assert {c["model"] for c in fake.calls} == {"gemini-4-carbon"}


def test_standard_thinking_and_empty_system_send_neither(app, rt):
    fake = FakeGemini()
    run(app, rt, fake, ["P1"], system="", think="Standard", max_out=2048)
    cfg = fake.calls[0]["config"]
    assert cfg.thinking_config is None and cfg.system_instruction is None and cfg.max_output_tokens == 2048


# ---------- Gespräche ----------
def test_groups_of_two_form_separate_conversations(app, rt):
    fake = FakeGemini()
    result = run(app, rt, fake, ["P1", "P2", "P3", "P4", "P5"], group=2)
    assert result.state == "DONE" and len(result.rows) == 5
    assert [c["contents"] for c in fake.calls] == [
        [("user", [PDF, "P1"])],
        [("user", [PDF, "P1"]), ("model", ["Antwort 1"]), ("user", ["P2"])],
        [("user", [PDF, "P3"])],
        [("user", [PDF, "P3"]), ("model", ["Antwort 3"]), ("user", ["P4"])],
        [("user", [PDF, "P5"])],
    ]


def test_a_group_larger_than_the_prompt_count_is_one_conversation(app, rt):
    fake = FakeGemini()
    run(app, rt, fake, ["P1", "P2", "P3"], group=5)
    assert fake.calls[-1]["contents"] == [
        ("user", [PDF, "P1"]), ("model", ["Antwort 1"]), ("user", ["P2"]), ("model", ["Antwort 2"]), ("user", ["P3"])]


def test_the_model_reply_is_passed_back_unchanged(app, rt):
    fake = FakeGemini()
    run(app, rt, fake, ["P1", "P2"], group=2)
    assert fake.calls[1]["raw"][1] is fake.responses[0].candidates[0].content  # Signaturen bleiben erhalten


def test_a_failed_first_prompt_skips_only_the_rest_of_its_conversation(app, rt):
    fake = FakeGemini([ApiError(400), "ok", "ok"])
    result = run(app, rt, fake, ["P1", "P2", "P3", "P4"], group=2)
    assert result.state == "DONE" and len(result.rows) == 4 and len(fake.calls) == 3
    assert "Übersprungen" in result.rows[1]["error"]
    assert result.rows[0]["error"] and not result.rows[2]["error"] and not result.rows[3]["error"]
    assert fake.calls[1]["contents"] == [("user", [PDF, "P3"])]
    assert fake.calls[2]["contents"] == [("user", [PDF, "P3"]), ("model", ["Antwort 2"]), ("user", ["P4"])]


def test_an_empty_reply_ends_its_conversation_too(app, rt):
    fake = FakeGemini(["empty", "ok", "ok"])
    result = run(app, rt, fake, ["P1", "P2", "P3", "P4"], group=2)
    assert len(fake.calls) == 3 and "Übersprungen" in result.rows[1]["error"]
    assert "Keine Antwort" in result.rows[0]["error"]


def test_a_failing_last_prompt_keeps_the_run_done(app, rt):
    fake = FakeGemini(["ok", "ok", ApiError(400)])
    result = run(app, rt, fake, ["P1", "P2", "P3"])
    assert result.state == "DONE" and len(result.rows) == 3
    assert [bool(r["error"]) for r in result.rows] == [False, False, True]


def test_a_failing_second_prompt_of_a_pair_is_a_plain_error_row(app, rt):
    fake = FakeGemini(["ok", ApiError(400)])
    result = run(app, rt, fake, ["P1", "P2"], group=2)
    assert result.state == "DONE" and "Übersprungen" not in str(result.rows[1]["error"])


# ---------- Fehler, Wiederholung, Abbruch ----------
def test_transient_errors_are_retried_inside_the_run(app, rt, fake_time):
    fake = FakeGemini([ApiError(503), "ok"])
    result = run(app, rt, fake, ["P1"])
    assert len(fake.calls) == 2 and not result.rows[0]["error"] and fake_time.sleeps == [10]


def test_a_broken_setting_fails_the_whole_run_without_calling_the_api(app, rt):
    fake = FakeGemini()
    result = run(app, rt, fake, ["P1"], system=123)  # keine gültige Systemanweisung: die Konfiguration scheitert
    assert result.state == "FAILED" and result.job["error"] and result.rows == [] and fake.calls == []


def test_cancel_stops_before_the_next_prompt_and_cleans_up(app, rt):
    fake = FakeGemini()
    fake.on_call = lambda n: rt.cancel.update(x["name"] for x in app.load_jobs())  # Wunsch während Anfrage 1
    result = run(app, rt, fake, ["P1", "P2", "P3"])
    assert result.state == "CANCELLED" and len(result.rows) == 1 and len(fake.calls) == 1
    assert rt.cancel == set()


# ---------- Zeilen und Verlauf ----------
def test_rows_carry_usage_cache_model_and_duration(app, rt):
    fake = FakeGemini([{"text": "Fertig", "cached": 79000, "prompt_tokens": 80000, "thoughts": 2500, "out": 900}])
    row = run(app, rt, fake, ["P1"]).rows[0]
    assert (row["in"], row["cached"], row["think"], row["out"]) == (80000, 79000, 2500, 900)
    assert (row["model"], row["finish"], row["text"]) == ("gemini-4-carbon", "STOP", "Fertig")
    assert row["usage"]["cachedContentTokenCount"] == 79000 and isinstance(row["secs"], int)


def test_the_history_entry_describes_the_run(app, rt, home):
    fake = FakeGemini()
    run(app, rt, fake, ["P1", "P2"], group=2, n_pdfs=2, system="S", think="low", max_out=4096)
    saved = read_history(home)[0]
    assert saved["mode"] == "sync" and saved["name"].startswith("sync-") and saved["state"] == "DONE"
    assert (saved["pdf"], saved["pdfs"]) == ("A.pdf, B.pdf", ["A.pdf", "B.pdf"])
    assert saved["prompts"] == {"prompt-1": "P1", "prompt-2": "P2"}
    assert (saved["system"], saved["think"], saved["max_out"], saved["group"]) == ("S", "low", 4096, 2)
    assert len(saved["rows"]) == 2


def test_newest_run_comes_first_in_the_history(app, rt, home):
    first = run(app, rt, FakeGemini(), ["P1"]).entry["name"]
    second = run(app, rt, FakeGemini(), ["P1"]).entry["name"]
    assert [e["name"] for e in read_history(home)] == [second, first]


def test_rows_are_written_while_the_run_is_still_going(app, rt, home):
    seen = {}

    def inspect_history(n):
        if n == 1:  # Anfrage 2 beginnt: Zeile 1 muss schon gespeichert sein, der Lauf läuft noch
            seen["rows_then"] = len(read_history(home)[0]["rows"])
            seen["state_then"] = read_history(home)[0]["state"]

    result = run(app, rt, FakeGemini(on_call=inspect_history), ["P1", "P2"])
    assert seen == {"rows_then": 1, "state_then": "RUNNING"} and result.state == "DONE"


# ---------- Zustände ----------
NAME = "sync-20261002-120000-bbbb"


def test_sync_view_reports_interrupted_runs(app, rt, home):
    write_history(home, [sync_entry(state="RUNNING", rows=[])])
    assert app.sync_view(rt, NAME)[1] == "INTERRUPTED"


def test_sync_view_reports_running_only_while_the_thread_lives(app, rt, home):
    write_history(home, [sync_entry(state="RUNNING", rows=[])])
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    rt.threads[NAME] = thread
    assert app.sync_view(rt, NAME)[1] == "RUNNING"
    release.set()
    thread.join(timeout=5)
    assert app.sync_view(rt, NAME)[1] == "INTERRUPTED"


def test_sync_view_keeps_final_states(app, rt, home):
    write_history(home, [sync_entry(state="CANCELLED")])
    assert app.sync_view(rt, NAME)[1] == "CANCELLED"


def test_sync_view_unknown_entry(app, rt):
    assert app.sync_view(rt, "gibt-es-nicht") == (None, None)
