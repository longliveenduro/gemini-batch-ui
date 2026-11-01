"""Gemeinsame Fixtures. Jeder Test läuft in einem Wegwerf-Ordner und ohne echten API-Zugriff."""
import os

# Streamlit meldet im „bare mode“ (ohne laufende App) „missing ScriptRunContext“. Das ist hier erwartet und stört nur.
os.environ.setdefault("STREAMLIT_LOGGER_LEVEL", "error")

import threading  # noqa: E402
import time as real_time  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from helpers import APP_PATH, ROOT  # noqa: E402

UI_MARKER = "# ---------- UI ----------"


@pytest.fixture(scope="session", autouse=True)
def project_folder_stays_untouched():
    """Sicherheitsnetz: Kein Test darf conf/ oder data/ im echten Projektordner anlegen oder verändern."""
    def snapshot():
        state = {}
        for name in ("conf", "data"):
            folder = ROOT / name
            if folder.exists():
                state[name] = sorted((str(p.relative_to(ROOT)), p.stat().st_size, p.stat().st_mtime_ns)
                                     for p in folder.rglob("*") if p.is_file())
        return state

    before = snapshot()
    yield
    assert snapshot() == before, ("Ein Test hat conf/ oder data/ im Projektordner verändert. "
                                  "Testdaten gehören nach GEMINI_UI_HOME (Fixture „home“).")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Wegwerf-Ordner als Wurzel für conf/ und data/ (GEMINI_UI_HOME) und ein Dummy-API-Key."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("GEMINI_UI_HOME", str(folder))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    return folder


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    """Ohne installierten Fake-Client scheitert jeder Versuch, einen echten Client zu bauen."""
    import google.genai as genai

    def refuse(*args, **kwargs):
        raise AssertionError("Kein Fake-Client installiert: Der Test hätte die echte API angesprochen.")

    monkeypatch.setattr(genai, "Client", refuse)


class StubTime:
    """Ersatz für das Modul time: keine echten Pausen (sleep wird nur notiert), aber echte Uhr."""

    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def time(self):
        return real_time.time()


class AppModule:
    """Alles oberhalb der UI-Grenze der App als Objekt: app.parse_line(...), app.ns['time'] = ... für Ersatz."""

    def __init__(self, ns):
        self.ns = ns

    def __getattr__(self, name):
        try:
            return self.ns[name]
        except KeyError:
            raise AttributeError(name) from None


@pytest.fixture
def app(home):
    """Die App ohne Oberfläche (Code oberhalb von „# ---------- UI ----------“), Ordner aus der Fixture home."""
    head = APP_PATH.read_text("utf-8").split(UI_MARKER)[0]
    ns = {"__name__": "appmod", "__file__": str(APP_PATH)}
    exec(compile(head, str(APP_PATH), "exec"), ns)
    return AppModule(ns)


@pytest.fixture
def fake_time(app):
    """Ersetzt time in der App-Logik: Wiederholungs- und Wartepausen kosten keine Zeit."""
    stub = StubTime()
    app.ns["time"] = stub
    return stub


@pytest.fixture
def rt():
    """Ersatz für runtime(): Sperre, Threads, Abbruchwünsche."""
    return SimpleNamespace(lock=threading.Lock(), threads={}, cancel=set())
