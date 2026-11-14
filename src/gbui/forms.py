"""Formular „Neue Anfrage“ und Verlaufseinträge: Beschriftung und Callbacks (Eingaben übernehmen, Gespräch
fortsetzen, Eintrag löschen). Callbacks setzen Widget-Werte, bevor das Widget im Lauf erzeugt wird."""
import streamlit as st

from .config import (BATCH_MAX_OUT, BG_MAX_OUT, MODE_BATCH, MODE_BG, MODE_SYNC, OPENAI_EFFORTS, OPENAI_MAX_OUT,
                     PROVIDER_OPENAI, SYNC_MAX_OUT, TABS, TALK_NEW, THINK_LEVELS)
from .gemini import sdk_has_long_decode
from .history import mutate_jobs, runtime
from .providers import entry_mode, entry_provider
from .talk import conversation_text


def entry_label(x):
    return f"{x['label']} ({x['created'][:16].replace('T', ' ')}) {x['name'][-6:]}"


def fill_form(j, raw, group, text):
    """Callback-Hilfe: Formular „Neue Anfrage“ mit dem Prompt-Text raw und den Einstellungen eines Eintrags füllen
    und zu dem Tab wechseln. text beginnt den einmaligen Hinweis. Nur aus Callbacks aufrufen (Widget-Werte lassen
    sich nur setzen, bevor das Widget im Lauf erzeugt wurde)."""
    ss, mode, prov = st.session_state, entry_mode(j), entry_provider(j)
    is_sync = mode == "sync"
    ss["provider"] = prov
    if j.get("model"):
        ss["model_openai" if prov == PROVIDER_OPENAI else "model_gemini"] = j["model"]
    ss["mode"] = {"sync": MODE_SYNC, "background": MODE_BG}.get(mode, MODE_BATCH)
    ss["raw"] = raw
    ss["system"] = j.get("system", "")
    if prov == PROVIDER_OPENAI:
        if j.get("think") in OPENAI_EFFORTS:
            ss["effort"] = j["think"]
        if j.get("max_out"):
            ss["max_openai"] = max(256, min(int(j["max_out"]), OPENAI_MAX_OUT))
    else:
        if j.get("think") in THINK_LEVELS:
            ss["think"] = j["think"]
        if j.get("max_out"):
            if is_sync:
                cap, field = (SYNC_MAX_OUT if sdk_has_long_decode() else BATCH_MAX_OUT), "max_sync"
            elif mode == "background":
                cap, field = BG_MAX_OUT, "max_bg"
            else:
                cap, field = BATCH_MAX_OUT, "max_batch"
            ss[field] = max(256, min(int(j["max_out"]), cap))
    if is_sync:
        ss["group"] = min(max(int(group or 1), 1), 50)
    pdfs = j.get("pdf")
    note = text
    if pdfs:
        note += f" Bitte die PDFs erneut anhängen: {pdfs}. Schon hochgeladene Dateien werden wiederverwendet."
    ss["reused_note"] = note
    ss["view"] = TABS[0]  # wechselt zum Tab „Neue Anfrage“ (ab Streamlit 1.55)


def reuse_inputs(j):
    """Callback: Prompts und Einstellungen eines Eintrags ins Formular „Neue Anfrage“ übernehmen."""
    fill_form(j, "\n---\n".join(j["prompts"].values()), j.get("group"), f"Eingaben aus „{j['label']}“ übernommen.")


def continue_conversation(j, turns):
    """Callback: Gesprächsverlauf (turns, siehe history_turns) als Text ins Formular übernehmen. Darunter schreibt man
    den neuen Prompt. Der Verlauf steckt im Prompt selbst, deshalb „Prompts pro Gespräch“ = 1."""
    fill_form(j, conversation_text(turns), 1,
              f"Gespräch aus „{j['label']}“ fortgesetzt: {len(turns)} Runde(n) Verlauf stehen im Prompt. "
              f"Schreibe deinen neuen Prompt unter „{TALK_NEW}“.")


def delete_entry(name):
    """Callback: Eintrag samt lokal gesicherter Ergebnisse aus dem Verlauf entfernen (beim Anbieter bleibt alles)."""
    def change(jobs):
        jobs[:] = [x for x in jobs if x["name"] != name]
    mutate_jobs(runtime().lock, change)
    for prefix in ("res:", "err:", "errn:"):
        st.session_state.pop(prefix + name, None)
    st.session_state.pop("sel", None)
