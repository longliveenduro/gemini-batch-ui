"""Anzeige der Einträge und Ergebnisse. job_panel() läuft als Fragment (siehe Tab „Jobs und Ergebnisse“)."""
from datetime import datetime

import streamlit as st

from .config import BATCH_MAX_OUT, DONE, MAX_POLL_ERRORS, POLL_SECONDS, PROVIDER_GEMINI, TALK_NEW
from .forms import continue_conversation
from .providers import batch_info, cancel_batch, entry_mode, entry_provider, refresh
from .sync import sync_view
from .talk import conversation_text, entry_group, history_turns, parse_conversation, short_prompt


def show_text(text):
    """Text im Wortlaut zeigen (festbreit, mit Kopierknopf). Lange Texte scrollen in einem eigenen Rahmen."""
    box = st.container(height=400) if len(text) > 3000 else st.container()
    box.code(text, language=None, wrap_lines=True)


def show_prompt(prompt):
    """Einen gesendeten Prompt im Wortlaut zeigen, mit Hinweis, wenn er ein früheres Gespräch fortsetzt."""
    turns, _ = parse_conversation(prompt)
    if turns:
        st.caption(f"Setzt ein früheres Gespräch fort: Der Prompt enthält den Verlauf von {len(turns)} Runde(n) "
                   "vor dem neuen Prompt.")
    show_text(prompt)


def request_details(j):
    """Die gesendete Anfrage eines Eintrags im Wortlaut: PDFs, Systemanweisung und jeder Prompt. Funktioniert auch,
    solange noch keine Ergebnisse da sind."""
    pdfs = j.get("pdfs") or ([j["pdf"]] if j.get("pdf") else [])
    st.markdown("**PDFs** (Reihenfolge in jeder Anfrage): "
                + (", ".join(f"{k}. {n}" for k, n in enumerate(pdfs, 1)) if pdfs else "keine"))
    st.markdown("**Systemanweisung**")
    if j.get("system"):
        show_text(j["system"])
    else:
        st.caption("Keine Systemanweisung.")
    group = entry_group(j)
    for n, (key, prompt) in enumerate(j["prompts"].items()):
        where = f" (Gespräch {n // group + 1}, Runde {n % group + 1})" if group > 1 else ""
        st.markdown(f"**{key}**{where}")
        show_prompt(prompt)


def talk_tools(j, rows, r):
    """Unter einer Antwort: „Gespräch fortsetzen“ übernimmt den Verlauf als Text ins Formular, „Verlaufstext zeigen“
    zeigt ihn zum Kopieren. Ohne Antwort (Fehler) gibt es nichts fortzusetzen."""
    turns = history_turns(j, rows, r["key"])
    if not turns:
        return
    tag = f"{j['name']}:{r['key']}"
    c1, c2, _ = st.columns([1, 1, 2])
    if c1.button("Gespräch fortsetzen", key="talk:" + tag, on_click=continue_conversation, args=(j, turns),
                 help="Übernimmt den bisherigen Verlauf (früherer Prompt und deine Antwort dazu) als Text ins "
                      f"Formular „Neue Anfrage“. Dort schreibst du unter „{TALK_NEW}“ den nächsten Prompt."):
        st.rerun()  # Fragmentläufe ändern nur das Fragment: ein voller Rerun zeigt das gefüllte Formular
    show = c2.toggle("Verlaufstext zeigen", key="hist:" + tag, help="Der Verlauf als Text, wie er im Formular landet.")
    if show:
        text = conversation_text(turns)
        st.caption(f"Verlauf aus {len(turns)} Runde(n), {len(text):,} Zeichen. Der Text lässt sich mit dem Symbol "
                   "rechts oben in der Textbox kopieren.")
        show_text(text)


def show_rows(j, rows):
    if not rows:
        return
    tin, tc = sum(r["in"] for r in rows), sum(r["cached"] for r in rows)
    st.caption(f"Eingabe-Token gesamt: {tin:,}, davon aus dem Cache: {tc:,}" + (f" ({tc / tin:.0%})" if tin else ""))
    md = "\n\n".join(f"## {r['key']}\n\n**Prompt:** {r['prompt']}\n\n{r['text'] or r['error']}" for r in rows)
    st.download_button("Ergebnisse als Markdown", md, file_name=f"{j['label']}.md", key="dl:" + j["name"])
    for r in rows:
        with st.expander(f"{r['key']}: {short_prompt(r['prompt'])}", expanded=len(rows) <= 3):
            if st.toggle("Prompt im Wortlaut", key=f"prompt:{j['name']}:{r['key']}",
                         help="Zeigt den vollständigen Prompt, so wie er gesendet wurde."):
                show_prompt(r["prompt"])
            if r["text"]:
                st.markdown(r["text"])
            else:
                st.error(str(r["error"]))
            fin = r.get("finish")
            if fin == "MAX_TOKENS":
                st.warning("Abgeschnitten: Denk- und Ausgabe-Token haben das Limit erreicht.")
                if entry_mode(j) == "batch" and entry_provider(j) == PROVIDER_GEMINI:
                    st.caption(f"Ein Gemini-Batch ist auf {BATCH_MAX_OUT:,} Ausgabe-Token begrenzt. Mehr geht im Modus "
                               "„Hintergrund“ (Long Decode). „Eingaben übernehmen“ holt Prompts und Einstellungen "
                               "ins Formular.")
            elif fin not in (None, "STOP", "unbekannt"):
                st.warning(f"Die Antwort endete mit dem Grund „{fin}“ und ist möglicherweise unvollständig.")
            st.caption(f"Modell {r.get('model', '?')}, Ende: {fin or '?'}, "
                       f"Eingabe {r['in']:,} (Cache {r['cached']:,}), "
                       f"Denken {r['think']:,}, Ausgabe {r['out']:,} Token"
                       + (f", Dauer {r['secs']} s" if r.get("secs") is not None else ""))
            if r.get("usage"):
                st.json(r["usage"], expanded=False)
            talk_tools(j, rows, r)


def sync_panel(rt, name, polling):
    j, state = sync_view(rt, name)
    if j is None:
        st.warning("Eintrag nicht gefunden.")
        return
    if polling and state != "RUNNING":
        st.rerun()  # Lauf beendet: ein kompletter Rerun beendet die automatische Prüfung
    rows, total = j.get("rows", []), len(j["prompts"])
    if state == "RUNNING":
        if st.button("Lauf abbrechen", key="stop:" + name):
            rt.cancel.add(name)
            st.warning("Abbruch angefordert. Die laufende Anfrage wird noch zu Ende geführt.")
        if polling:
            st.caption(f"Automatische Prüfung alle {POLL_SECONDS} Sekunden, zuletzt um {datetime.now():%H:%M:%S}.")
    texts = {
        "RUNNING": "läuft",
        "DONE": "fertig",
        "CANCELLED": "abgebrochen",
        "FAILED": "fehlgeschlagen",
        "INTERRUPTED": "unterbrochen (die App wurde beendet oder neu gestartet)",
    }
    st.write(f"Status: **{texts.get(state, state)}**, {len(rows)} von {total} Prompts fertig")
    if j.get("error"):
        st.error(j["error"])
    show_rows(j, rows)


def job_panel(client, rt, j, polling):
    """Status und Ergebnisse. Mit polling=True prüft das Fragment alle POLL_SECONDS Sekunden neu."""
    if st.toggle("Gesendete Anfrage im Detail", key="detail:" + j["name"],
                 help="PDFs, Systemanweisung und jeden Prompt im Wortlaut, auch solange noch keine Ergebnisse da sind."):
        request_details(j)
    if j.get("mode") == "sync":
        return sync_panel(rt, j["name"], polling)
    name = j["name"]
    errn_key, err_key = f"errn:{name}", f"err:{name}"
    background = entry_mode(j) == "background"  # läuft bei Google, wird wie ein Batch abgefragt
    cancel_label = "Läufe abbrechen" if background else "Batch abbrechen"
    no_client = client is None  # der API-Key des Anbieters dieses Eintrags fehlt
    if no_client:
        st.warning(f"Der {entry_provider(j)} API-Key fehlt (Seitenleiste): Die Knöpfe „Status prüfen“ und "
                   f"„{cancel_label}“ sind gesperrt. Gesicherte Ergebnisse bleiben sichtbar.")
    b1, b2, _ = st.columns([1, 1, 3])
    manual = b1.button("Status prüfen / Ergebnisse laden", key="chk:" + name, disabled=no_client)
    if b2.button(cancel_label, key="cx:" + name, disabled=no_client):
        try:
            cancel_batch(client, j)
            st.warning("Abbruch angefordert. Der Status wird gleich aktualisiert.")
        except Exception as e:
            st.error(f"Abbruch fehlgeschlagen: {e}")
    if (manual or polling) and not no_client:
        try:
            refresh(client, j)
            st.session_state[errn_key] = 0
        except Exception as e:
            # Batch existiert nicht mehr oder ist nicht zugänglich. Den HTTP-Code meldet Gemini als code,
            # OpenAI als status_code.
            fatal = getattr(e, "code", None) in (403, 404) or getattr(e, "status_code", None) in (403, 404)
            st.session_state[errn_key] = MAX_POLL_ERRORS if fatal else st.session_state.get(errn_key, 0) + 1
            st.session_state[err_key] = str(e)
    info = batch_info(j)
    stopped = st.session_state.get(errn_key, 0) >= MAX_POLL_ERRORS
    if polling and ((info and info["state"] in DONE) or stopped):
        st.rerun()  # fertig oder aufgegeben: ein kompletter Rerun beendet die automatische Prüfung
    if polling:
        st.caption(f"Automatische Prüfung alle {POLL_SECONDS} Sekunden, zuletzt um {datetime.now():%H:%M:%S}.")
    if st.session_state.get(errn_key, 0) > 0:
        hint = " Die automatische Prüfung ist gestoppt, „Status prüfen“ versucht es erneut." if stopped else ""
        st.error(f"Abruf fehlgeschlagen: {st.session_state.get(err_key)}{hint}")
    if not info:
        return
    if "res:" + name not in st.session_state:
        st.caption("Gesichertes Endergebnis aus data/batch_jobs.json, kein Abruf beim Anbieter nötig.")
    progress = f", {len(info['rows'] or [])} von {len(j['prompts'])} Prompts fertig" if background else ""
    st.write(f"Status: **{info['state']}**{progress}, Modell laut API: **{info.get('model') or 'unbekannt'}**")
    if info["error"]:
        st.error(info["error"])
    show_rows(j, info["rows"] or [])
