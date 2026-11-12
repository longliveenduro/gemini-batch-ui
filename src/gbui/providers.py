"""Anbieter (Gemini, OpenAI): Client bauen, Anbieter eines Eintrags, Status holen und sichern, Batch abbrechen.
Hier kommt ein weiterer Anbieter dazu (siehe README, Abschnitt 9.2)."""
import streamlit as st
from google import genai

from .config import DONE, PROVIDER_GEMINI, PROVIDER_OPENAI
from .history import mutate_jobs, runtime
from .openai_batch import fetch_openai_batch, openai_client
from .rows import parse_results


def make_client(provider, api_key):
    return openai_client(api_key) if provider == PROVIDER_OPENAI else genai.Client(api_key=api_key)


def entry_provider(j):
    """Anbieter eines Eintrags. Ältere Einträge haben kein Feld provider und stammen von Gemini."""
    return PROVIDER_OPENAI if j.get("provider") == PROVIDER_OPENAI else PROVIDER_GEMINI


def refresh(client, j):
    if entry_provider(j) == PROVIDER_OPENAI:
        state, rows, err, api_model = fetch_openai_batch(client, j)
    else:
        job = client.batches.get(name=j["name"])
        state, rows = job.state.name, None
        if state == "JOB_STATE_SUCCEEDED" and job.dest and job.dest.file_name:
            rows = parse_results(client.files.download(file=job.dest.file_name), j["prompts"])
        err, api_model = (str(job.error) if job.error else None), job.model
    st.session_state["res:" + j["name"]] = {"state": state, "rows": rows, "error": err, "model": api_model}
    if state in DONE:  # Endergebnis lokal sichern: bleibt nach Ablauf beim Anbieter erhalten und lädt ohne Abruf
        def save(jobs):
            for x in jobs:
                if x["name"] == j["name"]:
                    x.update(state=state, rows=rows, error=err, api_model=api_model)
        mutate_jobs(runtime().lock, save)


def batch_info(j):
    """Stand eines Batches: aus der laufenden Sitzung, sonst aus der lokalen Sicherung des Endergebnisses."""
    info = st.session_state.get("res:" + j["name"])
    if info is None and j.get("state") in DONE:
        info = {"state": j["state"], "rows": j.get("rows"), "error": j.get("error"), "model": j.get("api_model")}
    return info


def cancel_batch(client, j):
    if entry_provider(j) == PROVIDER_OPENAI:
        client.batches.cancel(j["name"])
    else:
        client.batches.cancel(name=j["name"])
