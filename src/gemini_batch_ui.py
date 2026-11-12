"""Lokale Batch- und Synchron-UI für Gemini 4 Carbon und OpenAI (Streamlit).

Start:    pip install -r requirements.txt && streamlit run src/gemini_batch_ui.py   (im Projektordner)
API-Key:  Umgebungsvariablen GEMINI_API_KEY und OPENAI_API_KEY oder Seitenleiste.
Anbieter: Gemini (Batch und Synchron) oder OpenAI (nur Batch, Responses-Endpunkt), gewählt in der Seitenleiste.
Modi:     Batch (asynchron, 50 % Rabatt) oder Synchron (nur Gemini: nacheinander, impliziter Cache über alle Prompts,
          optional in Gesprächen: je N Prompts bilden einen gemeinsamen Kontext, das nächste Gespräch beginnt neu).
Ordner:   conf/ (Zugangsdaten, geheim: credentials.json, token.json) und data/ (batch_jobs.json = Verlauf und
          Ergebnisse, pdf_files.json und openai_files.json = hochgeladene PDFs, requests_*.jsonl = gesendete
          Batch-Anfragen).
          Beide liegen im Projektordner neben src/ (dem Ordner dieser Datei), oder unter dem Ordner aus der
          Umgebungsvariable GEMINI_UI_HOME.
Drive:    (optional) In der Google Cloud Console die Drive API aktivieren, eine OAuth-Client-ID
          vom Typ „Desktop-App“ erstellen und als conf/credentials.json ablegen.
          Beim ersten Zugriff öffnet sich der Browser zur Freigabe (nur Lesezugriff).
Aufbau:   Diese Datei enthält nur die Oberfläche (Seitenleiste, Tabs, Formular, Start-Handler). Die Logik steht
          im Paket gbui/ neben dieser Datei (Übersicht in der README, Abschnitt 8).
"""
import hashlib
import os
from datetime import datetime

import streamlit as st
from google import genai

from gbui.config import (BATCH_MAX_OUT, DEFAULT_MODEL, DEFAULT_OPENAI_MODEL, DONE, FORM_KEYS, HINT, MAX_POLL_ERRORS,
                         MODE_BATCH, MODE_SYNC, OPENAI_EFFORTS, OPENAI_MAX_OUT, POLL_SECONDS, PROVIDER_GEMINI,
                         PROVIDER_OPENAI, PROVIDERS, SEPARATOR, SYNC_MAX_OUT, TABS, TALK_NEW, THINK_LEVELS,
                         prepare_folders)
from gbui.drive import drive_fetch, drive_id, drive_service
from gbui.forms import delete_entry, entry_label, reuse_inputs
from gbui.gemini import sdk_has_long_decode, start_batch, upload_pdf
from gbui.history import load_jobs, mutate_jobs, runtime
from gbui.openai_batch import start_openai_batch, upload_pdf_openai
from gbui.providers import batch_info, entry_provider, make_client
from gbui.sync import start_sync, sync_view
from gbui.talk import entry_group, parse_conversation
from gbui.views import job_panel

st.set_page_config(page_title="Gemini Carbon", layout="wide")

moved = prepare_folders()
if moved:
    print("Dateien einsortiert:", ", ".join(moved))
    st.toast("Dateien einsortiert: " + ", ".join(moved))
for key in FORM_KEYS:  # hält die Werte am Leben und schickt sie beim erneuten Zeichnen an den Browser
    if key in st.session_state:
        st.session_state[key] = st.session_state[key]
with st.sidebar:
    provider = st.radio("Anbieter für neue Anfragen", PROVIDERS, key="provider", horizontal=True,
                        help="Jeder Eintrag im Tab „Jobs und Ergebnisse“ gehört zu seinem Anbieter und wird mit "
                             "dessen API-Key abgefragt. Beide Keys dürfen deshalb eingetragen bleiben.")
    keys = {PROVIDER_GEMINI: st.text_input("Gemini API-Key", os.getenv("GEMINI_API_KEY", ""), type="password"),
            PROVIDER_OPENAI: st.text_input("OpenAI API-Key", os.getenv("OPENAI_API_KEY", ""), type="password")}
    if provider == PROVIDER_OPENAI:
        st.session_state.setdefault("model_openai", DEFAULT_OPENAI_MODEL)
        model = st.text_input("Modell", key="model_openai",
                              help="Modellname laut OpenAI, zum Beispiel gpt-6-astra, gpt-6.1-sol oder gpt-6-luna. "
                                   "Das Modell muss die Batch API und PDF-Eingaben unterstützen.")
    else:
        st.session_state.setdefault("model_gemini", DEFAULT_MODEL)
        model = st.text_input("Modell", key="model_gemini")
if not keys[provider]:
    env_name = "OPENAI_API_KEY" if provider == PROVIDER_OPENAI else "GEMINI_API_KEY"
    st.info(f"Trage links deinen {provider} API-Key ein oder setze die Umgebungsvariable {env_name}.")
    st.stop()


def client_for(p):
    """Client des Anbieters p, oder None, wenn dessen API-Key fehlt."""
    return make_client(p, keys[p]) if keys[p] else None


try:
    client = client_for(provider)
except RuntimeError as e:  # z. B. das Paket openai fehlt
    st.error(f"Der {provider}-Client lässt sich nicht erstellen: {e}")
    st.stop()
rt = runtime()

try:  # ab Streamlit 1.55 weiß die App, welcher Tab offen ist
    tab_new, tab_jobs = st.tabs(TABS, key="view", on_change="rerun")
except TypeError:
    tab_new, tab_jobs = st.tabs(TABS)
on_jobs_tab = getattr(tab_jobs, "open", None) is not False  # ältere Versionen: immer prüfen

with tab_new:
    note = st.session_state.pop("reused_note", None)
    if note:
        st.info(note)
    if provider == PROVIDER_OPENAI:
        mode = MODE_BATCH
        st.caption("Mit OpenAI läuft nur der Batch-Modus (50 % Rabatt, Ergebnis innerhalb von 24 Stunden). "
                   "Der synchrone Modus ist Gemini vorbehalten.")
    else:
        mode = st.radio("Modus", [MODE_BATCH, MODE_SYNC], horizontal=True, key="mode")
    sync = mode == MODE_SYNC
    uploads = st.file_uploader("PDF-Dateien (mehrere möglich)", type="pdf", accept_multiple_files=True)
    with st.container(border=True):
        st.markdown("**PDFs aus Google Drive hinzufügen** (optional)")
        if st.button("Meine PDFs aus Drive laden"):
            try:
                res = drive_service().files().list(
                    q="mimeType='application/pdf' and trashed=false", orderBy="modifiedTime desc",
                    pageSize=50, fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True,
                ).execute()
                st.session_state["drive_pdfs"] = {f"{f['name']} [{f['id'][:6]}]": f["id"] for f in res["files"]}
            except Exception as e:
                st.error(f"Drive-Zugriff fehlgeschlagen: {e}. Falls der Login hängt, conf/token.json löschen.")
        drive_list = st.session_state.get("drive_pdfs", {})
        chosen = st.multiselect("Zuletzt geänderte PDFs", list(drive_list), placeholder="PDFs auswählen") if drive_list else []
        links = st.text_area("Oder Drive-Links / Datei-IDs einfügen (eine pro Zeile)", height=80)
    pasted = [drive_id(x) for x in links.splitlines() if x.strip()]
    drive_refs = [drive_list[c] for c in chosen] + pasted
    pdf_order = [f.name for f in uploads] + chosen + [f"Drive-Datei {ref[:6]}" for ref in pasted]
    if pdf_order:
        st.caption("Reihenfolge der PDFs in jeder Anfrage: "
                   + ", ".join(f"{k}. {n}" for k, n in enumerate(pdf_order, 1))
                   + " (hochgeladene Dateien zuerst, dann Drive). Gleiche Dateien werden nur einmal gesendet.")
    else:
        st.caption("Kein PDF angehängt: Die Prompts werden ohne PDF gesendet.")

    system = st.text_area("Systemanweisung (optional)", height=80, key="system")
    raw = st.text_area("Prompts (mehrere mit einer Zeile --- trennen)", height=260, key="raw")
    prompts = [p.strip() for p in SEPARATOR.split(raw) if p.strip()]
    talks = [parse_conversation(p) for p in prompts]  # Prompts, die ein früheres Gespräch fortsetzen
    unfinished = [k for k, (turns, own) in enumerate(talks, 1) if turns and not own]
    if any(turns for turns, _ in talks):
        st.caption(f"{sum(1 for turns, _ in talks if turns)} von {len(prompts)} Prompt(s) setzen ein früheres Gespräch "
                   "fort. Der Verlauf steht im Prompt und zählt bei jeder Anfrage als Eingabe.")
    if unfinished:
        st.warning(f"Unter „{TALK_NEW}“ fehlt noch dein neuer Prompt (Prompt {', '.join(map(str, unfinished))}). "
                   "Der Start ist gesperrt, bis dort Text steht.")
    group = 1
    if sync:
        gcol, _ = st.columns([1, 3])
        group = int(gcol.number_input(
            "Prompts pro Gespräch", min_value=1, max_value=50, step=1, key="group",
            help="1: Jeder Prompt läuft für sich. 2: Je zwei Prompts bilden ein Gespräch (der zweite sieht den ersten "
                 "und dessen Antwort), die nächsten zwei ein neues Gespräch usw. Die PDFs gehen in jedem Gespräch mit "
                 "dem ersten Prompt raus."))
    sdk_ok = sdk_has_long_decode()
    if sync and not sdk_ok:
        st.warning(f"Dein google-genai ({genai.__version__}) ist älter als 2.30.1, daher ist die Ausgabe auf "
                   f"{BATCH_MAX_OUT} Token begrenzt. Aktualisieren: pip install -U google-genai")
    c1, c2, c3 = st.columns(3)
    if provider == PROVIDER_OPENAI:
        st.session_state.setdefault("effort", "Standard")
        think = c1.selectbox("Denktiefe", OPENAI_EFFORTS, key="effort",
                             help="reasoning.effort der Responses API. „Standard“ sendet nichts, dann gilt die "
                                  "Voreinstellung des Modells. Welche Stufen ein Modell kennt, hängt vom Modell ab "
                                  "(z. B. kennt gpt-6-astra kein „none“). Andere Stufen lehnt die API ab, "
                                  "der Fehler steht dann im Ergebnis.")
    else:
        st.session_state.setdefault("think", "high")
        think = c1.selectbox("Denktiefe", THINK_LEVELS, key="think")
    if provider == PROVIDER_OPENAI:
        st.session_state.setdefault("max_openai", 32768)
        max_out = c2.number_input("Max. Ausgabe-Token", min_value=256, max_value=OPENAI_MAX_OUT, step=1024,
                                  key="max_openai",
                                  help="max_output_tokens. Denk-Token zählen zum Limit. Das Maximum hängt vom "
                                       "Modell ab (bei den aktuellen Modellen bis 128.000).")
    elif sync:
        cap = SYNC_MAX_OUT if sdk_ok else BATCH_MAX_OUT
        st.session_state.setdefault("max_sync", min(65536, cap))
        max_out = c2.number_input("Max. Ausgabe-Token", min_value=256, max_value=cap, step=1024, key="max_sync",
                                  help="Über 32768 setzt das SDK automatisch Long Decode ein "
                                       "(mehrere Teilanfragen, eine zusammengesetzte Antwort).")
    else:
        st.session_state.setdefault("max_batch", BATCH_MAX_OUT)
        max_out = c2.number_input("Max. Ausgabe-Token", min_value=256, max_value=BATCH_MAX_OUT, step=1024, key="max_batch",
                                  help="Im Batch-Betrieb begrenzt die API die Ausgabe auf 32768 Token.")
    kind = "openai" if provider == PROVIDER_OPENAI else "sync" if sync else "batch"
    label = c3.text_input("Name", f"{kind}-{datetime.now():%Y%m%d-%H%M}", key="label_" + kind)
    talk_info = ""
    if sync and group > 1 and prompts:
        n_conv, rest = -(-len(prompts) // group), len(prompts) % group
        if n_conv == 1:
            split = f"Alle {len(prompts)} Prompts bilden ein Gespräch"
        else:
            split = f"Sie bilden {n_conv} Gespräche mit je {group} Prompts" + (f" (das letzte mit {rest})" if rest else "")
        talk_info = (f" {split}. Jedes Gespräch beginnt mit den PDFs, innerhalb eines Gesprächs wird bei jedem Prompt "
                     "der ganze Verlauf erneut gesendet. Sein Anfang (PDFs und erster Prompt) kann gecacht werden, "
                     "neue Prompts und Antworten werden normal berechnet.")
    if sync:
        st.caption(
            f"{len(prompts)} Prompt(s) erkannt. Die Prompts laufen nacheinander im Hintergrund, auch wenn du den Tab "
            "wechselst. Die erste Anfrage füllt den impliziten Cache (nur implizit möglich, ab 4.096 Token, ohne "
            "Rabattgarantie), die folgenden können davon profitieren. Die PDFs stehen in jeder Anfrage an erster Stelle. "
            "Es gilt der Standardpreis statt des Batch-Rabatts. Cache-Treffer siehst du unter „Jobs und Ergebnisse“."
            + talk_info
        )
    elif provider == PROVIDER_OPENAI:
        st.caption(
            f"{len(prompts)} Prompt(s) erkannt. Die OpenAI Batch API kostet 50 % des Standardpreises und liefert die "
            "Ergebnisse innerhalb von 24 Stunden. Die PDFs stehen in jeder Anfrage an erster Stelle, damit der "
            "Prefix für das automatische Caching (ab 1.024 Token) identisch ist. Treffer siehst du unter "
            "„Jobs und Ergebnisse“."
        )
    else:
        st.caption(
            f"{len(prompts)} Prompt(s) erkannt. Batch kostet 50 % des Standardpreises. Carbon unterstützt nur "
            "implizites Caching (ab 4.096 Token, ohne Rabattgarantie): Die PDFs stehen in jeder Anfrage an erster "
            "Stelle, damit der Prefix identisch ist. Treffer siehst du unter „Jobs und Ergebnisse“."
        )

    if st.button("Synchron starten" if sync else "Batch starten", type="primary",
                 disabled=not prompts or bool(unfinished)):
        try:
            with st.status("Bereite Anfragen vor …" + HINT) as status:
                files = [(f.name, f.getvalue()) for f in uploads]
                if drive_refs:
                    status.update(label="Lade PDFs aus Google Drive …" + HINT)
                    files += [drive_fetch(ref) for ref in drive_refs]
                unique, seen = [], set()
                for fname, data in files:  # gleiche Dateien nur einmal senden
                    digest = hashlib.sha256(data).hexdigest()
                    if digest not in seen:
                        seen.add(digest)
                        unique.append((fname, data))
                names, pdfs = [n for n, _ in unique], []
                for k, (fname, data) in enumerate(unique, 1):
                    status.update(label=f"Lade PDF {k} von {len(unique)} zu {provider} hoch …" + HINT)
                    pdfs.append((upload_pdf_openai if provider == PROVIDER_OPENAI else upload_pdf)(client, fname, data))
                status.update(label="Starte …" + HINT)
                if sync:
                    entry = start_sync(client, rt, model, pdfs, names, prompts, system.strip(), think, max_out, label, group)
                elif provider == PROVIDER_OPENAI:
                    batch_id, keyed = start_openai_batch(client, model, [f.id for f in pdfs], prompts, system.strip(),
                                                         think, max_out, label)
                    entry = {"name": batch_id, "provider": PROVIDER_OPENAI, "label": label, "model": model,
                             "pdf": ", ".join(names) or None, "pdfs": names, "prompts": keyed, "system": system.strip(),
                             "think": think, "max_out": int(max_out),
                             "created": datetime.now().isoformat(timespec="seconds")}
                    mutate_jobs(rt.lock, lambda jobs: jobs.insert(0, entry))
                else:
                    job, keyed = start_batch(client, model, pdfs, prompts, system.strip(), think, max_out, label)
                    entry = {"name": job.name, "label": label, "model": model, "pdf": ", ".join(names) or None,
                             "pdfs": names, "prompts": keyed, "system": system.strip(), "think": think,
                             "max_out": int(max_out), "created": datetime.now().isoformat(timespec="seconds")}
                    mutate_jobs(rt.lock, lambda jobs: jobs.insert(0, entry))
                status.update(label="Gestartet", state="complete")
            st.session_state["sel"] = entry_label(entry)  # der neue Eintrag ist im Jobs-Tab vorausgewählt
            if sync:
                st.success(f"{entry['name']} läuft im Hintergrund. Fortschritt und Ergebnisse siehst du im Tab "
                           "„Jobs und Ergebnisse“.")
            else:
                st.success(f"{entry['name']} läuft. Im Tab „Jobs und Ergebnisse“ wird der Status alle "
                           f"{POLL_SECONDS} Sekunden geprüft.")
        except Exception as e:
            st.error(f"Start fehlgeschlagen: {e}")

with tab_jobs:
    jobs = load_jobs()
    if not jobs:
        st.info("Noch nichts gestartet.")
    else:
        opts = {entry_label(x): x for x in jobs}
        if st.session_state.get("sel") not in opts:
            st.session_state.pop("sel", None)
        j = opts[st.selectbox("Eintrag", list(opts), key="sel")]
        is_sync = j.get("mode") == "sync"
        grp, entry_prov = entry_group(j), entry_provider(j)
        kind_txt = ((f"synchron, {grp} Prompts pro Gespräch" if grp > 1 else "synchron") if is_sync
                    else "Batch (OpenAI)" if entry_prov == PROVIDER_OPENAI else "Batch")
        st.caption(f"{j['name']}, {kind_txt}, gesendet: Modell {j['model']}, "
                   f"Denktiefe {j.get('think', 'unbekannt')}, max. {j.get('max_out', '?')} Ausgabe-Token, "
                   f"PDF: {j['pdf'] or 'keins'}, {len(j['prompts'])} Prompt(s)")
        if is_sync:
            running = sync_view(rt, j["name"])[1] == "RUNNING"
        else:
            info = batch_info(j)
            failed = st.session_state.get("errn:" + j["name"], 0) >= MAX_POLL_ERRORS
            running = not ((info and info["state"] in DONE) or failed)
        a1, a2, _ = st.columns([1, 1, 3])
        a1.button("Eingaben übernehmen", on_click=reuse_inputs, args=(j,), key="reuse:" + j["name"],
                  help="Übernimmt Prompts und Einstellungen ins Formular „Neue Anfrage“.")
        with a2.popover("Eintrag löschen", disabled=running):
            st.write("Entfernt den Eintrag samt lokal gesicherter Ergebnisse aus dem Verlauf. "
                     "Beim Anbieter bleibt alles unverändert.")
            st.button("Ja, endgültig löschen", on_click=delete_entry, args=(j["name"],), key="del:" + j["name"])
        job_client = client if entry_prov == provider else client_for(entry_prov)  # jeder Eintrag mit seinem Anbieter
        polling = on_jobs_tab and running and (is_sync or job_client is not None)
        st.fragment(run_every=POLL_SECONDS if polling else None)(job_panel)(job_client, rt, j, polling)
