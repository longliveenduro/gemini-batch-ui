"""Verlauf der Läufe (data/batch_jobs.json). Batches laufen bis zu 24 h, synchrone Läufe im Hintergrund: daher
lokal gespeichert. Geschrieben wird nur über mutate_jobs()."""
import json
import os
import threading
import time
from types import SimpleNamespace

import streamlit as st

from .config import DATA_DIR, HISTORY


@st.cache_resource(show_spinner=False)
def runtime():
    """Prozessweit geteilt: Sperre für den Verlauf, laufende Threads, Abbruchwünsche."""
    return SimpleNamespace(lock=threading.Lock(), threads={}, cancel=set())


def load_jobs():
    if not HISTORY.exists():
        return []
    try:
        return json.loads(HISTORY.read_text("utf-8"))
    except json.JSONDecodeError:  # beschädigte Datei sichern, statt die App abstürzen zu lassen
        HISTORY.rename(HISTORY.with_name(f"batch_jobs.defekt-{int(time.time())}.json"))
        return []


def mutate_jobs(lock, change):
    """Verlauf unter Sperre lesen, mit change(jobs) ändern und atomar zurückschreiben (auch aus Threads nutzbar)."""
    with lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        jobs = load_jobs()
        change(jobs)
        tmp = HISTORY.with_suffix(".tmp")
        tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, HISTORY)
