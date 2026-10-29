# Gemini 4 Argon: Batch- und Synchron-UI

Lokale Streamlit-Oberfläche, um **Gemini 4 Argon** (`gemini-4-argon`) mit **einem oder mehreren PDFs** und **vielen Prompts** zu benutzen. Es gibt zwei Betriebsarten:

- **Batch**: asynchron, 50 % des Standardpreises, Ergebnis nach Minuten bis Stunden.
- **Synchron**: Prompts laufen nacheinander im Hintergrund, mit Aussicht auf Cache-Treffer für das PDF. Optional bilden je N Prompts ein Gespräch (gemeinsamer Kontext).

Dokumentationsstand: 29.10.2026 (Streamlit 1.66, google-genai 2.31, Gemini 4 Argon). Alle Aussagen zur Google-API gelten für diesen Stand. Vor Änderungen an API-nahem Code die Links in Abschnitt 13 prüfen, Preise und Limits ändern sich.

## Inhalt

1. [Worum es geht und warum](#1-worum-es-geht-und-warum)
2. [Funktionen im Überblick](#2-funktionen-im-überblick)
3. [Schnellstart](#3-schnellstart)
4. [Bedienung](#4-bedienung)
5. [Kosten sparen: Batch, Caching, Gespräche](#5-kosten-sparen-batch-caching-gespräche)
6. [Konfiguration](#6-konfiguration)
7. [Lokale Dateien und Datenformate](#7-lokale-dateien-und-datenformate)
8. [Architektur](#8-architektur)
9. [Weiterentwickeln](#9-weiterentwickeln)
10. [Fehlersuche](#10-fehlersuche)
11. [Bekannte Einschränkungen und Ideen](#11-bekannte-einschränkungen-und-ideen)
12. [Sicherheit und Datenschutz](#12-sicherheit-und-datenschutz)
13. [Verifikationsstand und Quellen](#13-verifikationsstand-und-quellen)

---

## 1. Worum es geht und warum

**Ausgangslage.** Typischer Einsatz: Ein langes PDF (z. B. ein Buch mit rund 80.000 Token) wird mit mehreren Prompts bearbeitet, etwa „erzähle Abschnitt X in einfacher Sprache nach“. Bei jedem Prompt wird das ganze PDF erneut als Eingabe berechnet. Die PDF-Token dominieren deshalb die Kosten.

**Ziele der App**

- **Günstig**: Batch-Rabatt nutzen, und wo möglich das PDF aus dem (impliziten) Cache lesen lassen.
- **Bequem**: kein Skript pro Lauf. Ein Prompts-Feld, PDF-Upload (auch mehrere) oder PDFs aus Google Drive.
- **Nachvollziehbar**: pro Antwort Modell, Abschlussgrund, Eingabe-, Cache-, Denk- und Ausgabe-Token, Dauer und rohe Nutzungsdaten.
- **Robust**: Batches laufen bis zu 24 Stunden, synchrone Läufe im Hintergrund. Beides überlebt Tab-Wechsel und Seiten-Neuladen. Ergebnisse werden lokal gesichert.

**Nicht-Ziele**: Mehrbenutzerbetrieb, Hosting im Netz, Anmeldung, Kostenabrechnung, explizites Caching (Argon unterstützt es nicht, siehe Abschnitt 5).

**Herkunft.** Die Idee war ein kleines Streamlit-Werkzeug für den Batch-Betrieb mit dem Gemini-Batch-API. Daraus wurde eine Ein-Datei-App. Die Oberfläche ist deutsch.

## 2. Funktionen im Überblick

- Zwei Modi: **Batch** und **Synchron**.
- **Mehrere PDFs** pro Anfrage: Datei-Upload (mehrfach), Auswahl aus Google Drive (Liste der 50 zuletzt geänderten PDFs, mehrfach wählbar) und/oder eingefügte Drive-Links oder Datei-IDs (eine pro Zeile). Google Docs/Slides aus Drive werden als PDF exportiert. Gleiche Dateien werden nur einmal gesendet.
- **Mehrere Prompts**, getrennt durch eine Zeile mit `---`. Optional **Systemanweisung**.
- **Denktiefe** (`low`, `medium`, `high`, oder „Standard“ = nichts senden) und **max. Ausgabe-Token**.
- **Gespräche (nur synchron)**: „Prompts pro Gespräch“ = N. Je N aufeinanderfolgende Prompts teilen einen Kontext, das nächste Gespräch beginnt neu.
- **Jobs und Ergebnisse**: Verlauf aller Läufe, automatische Statusprüfung alle 10 Sekunden solange der Tab offen ist, Ergebnisse mit Token- und Cache-Angaben, Markdown-Download, Rohdaten-JSON.
- **Lokale Sicherung** der Ergebnisse (`data/batch_jobs.json`), auch nach Ablauf der Aufbewahrung bei Google.
- **Eingaben übernehmen**: Prompts und Einstellungen eines früheren Eintrags zurück ins Formular holen. **Eintrag löschen** (nur lokal).
- **Wiederverwendung hochgeladener PDFs** über Neustarts hinweg (`data/pdf_files.json`), damit die Datei-Referenz gleich bleibt.
- **Drive-Anmeldung erneuert sich selbst**, wenn die Freigabe abgelaufen ist.
- **Aufgeräumte Ordner**: Zugangsdaten in `conf/`, erzeugte Daten in `data/`. Dateien eines älteren Stands werden beim Start dorthin übernommen.

## 3. Schnellstart

**Voraussetzungen**

- Python 3.11 (vom Besitzer genutzt, 3.10+ sollte genügen).
- Ein Gemini-API-Key (in Google AI Studio erzeugen).
- Optional für Drive: OAuth-Zugang, siehe 6.4.

**Installation** (am besten in einer eigenen Umgebung, damit andere Pakete unberührt bleiben):

```bash
conda create -n gemini-batch python=3.11 -y
conda activate gemini-batch
pip install -r requirements.txt
```

`requirements.txt`:

```text
streamlit>=1.55
google-genai>=2.30.1
google-api-python-client
google-auth-oauthlib
```

Kein `pip install -U -r ...` in einer gemeinsam genutzten Umgebung (z. B. Anaconda „base“ mit TensorFlow): `-U` hebt unbeteiligte Pakete an und erzeugt Abhängigkeitskonflikte.

**Starten**

```bash
export GEMINI_API_KEY="dein-key"      # optional, sonst in der Seitenleiste eintragen
streamlit run gemini_batch_ui.py      # öffnet http://localhost:8501
```

Anderer Port: `streamlit run gemini_batch_ui.py --server.port 8502`. Beenden mit Strg+C. Laufende synchrone Läufe brechen dann ab und erscheinen später als „unterbrochen“. Beim ersten Start legt die App die Ordner `conf/` und `data/` neben `gemini_batch_ui.py` an (Abschnitt 7).

**Erster Lauf in 60 Sekunden**

1. Seitenleiste: API-Key prüfen, Modell `gemini-4-argon`.
2. Tab „Neue Anfrage“: Modus wählen, PDF hochladen, Prompts eintragen (mehrere mit `---` auf eigener Zeile trennen).
3. „Batch starten“ oder „Synchron starten“. Der neue Eintrag ist danach im Tab „Jobs und Ergebnisse“ vorausgewählt.

## 4. Bedienung

### Tab „Neue Anfrage“

| Element | Bedeutung |
|---|---|
| Modus | Batch (asynchron, günstiger) oder Synchron (nacheinander im Hintergrund). |
| PDF-Dateien | Mehrere Dateien möglich. Ein zweites PDF ersetzt das erste nicht. |
| PDFs aus Google Drive | Button „Meine PDFs aus Drive laden“ füllt die Mehrfachauswahl. Zusätzlich Links oder Datei-IDs einfügen. Reihenfolge: hochgeladene Dateien, dann Drive-Auswahl, dann eingefügte Links. Die Reihenfolge steht als Hinweis unter dem Bereich. |
| Systemanweisung | Optional, gilt für jede Anfrage. |
| Prompts | Ein Textfeld, mehrere Prompts durch eine Zeile nur mit `---` trennen. |
| Prompts pro Gespräch | Nur synchron. 1 = jeder Prompt für sich. 2 = je zwei Prompts bilden ein Gespräch (der zweite sieht den ersten und dessen Antwort), die nächsten zwei ein neues usw. Das PDF geht in jedem Gespräch mit dem ersten Prompt raus. |
| Denktiefe | „Standard“ (kein Parameter, Argon nutzt dann `medium`), `low`, `medium`, `high`. Voreinstellung `high`. |
| Max. Ausgabe-Token | Batch: bis 32.768 (Voreinstellung 32.768). Synchron: bis 1.048.576 (Voreinstellung 65.536), über 32.768 setzt das SDK automatisch Long Decode ein. Denk-Token zählen zum Limit. |
| Name | Bezeichnung des Eintrags im Verlauf. |
| Start-Button | Lädt PDFs hoch, sendet bzw. startet. Während dieser Vorbereitung nicht den Tab wechseln, sonst bricht der Start ab. |

Ohne angehängtes PDF werden die Prompts ohne PDF gesendet (steht als Hinweis im Formular).

### Tab „Jobs und Ergebnisse“

- **Eintrag**: Auswahlliste, neuester Eintrag oben.
- **Eingaben übernehmen**: füllt „Neue Anfrage“ mit Prompts, Systemanweisung, Modus, Denktiefe, Token-Limit und Gesprächsgröße. PDFs müssen neu angehängt werden, bereits hochgeladene Dateien werden dabei wiederverwendet.
- **Eintrag löschen** (Bestätigung im Popover): entfernt den lokalen Eintrag samt gesicherter Ergebnisse. Bei Google bleibt alles unverändert. Deaktiviert, solange der Eintrag läuft.
- **Batch**: Buttons „Status prüfen / Ergebnisse laden“ und „Batch abbrechen“. Solange der Tab offen und der Batch nicht fertig ist, prüft die App alle 10 Sekunden selbst. Ist der Batch fertig, werden die Ergebnisse geladen und lokal gesichert. Danach braucht ein Öffnen keinen Abruf mehr.
- **Synchron**: Zustand `läuft`, `fertig`, `abgebrochen`, `fehlgeschlagen` oder `unterbrochen`, Fortschritt „n von m Prompts“, Button „Lauf abbrechen“ (greift nach der gerade laufenden Anfrage).
- **Ergebnisse**: Summenzeile (Eingabe-Token gesamt und davon aus dem Cache), Download „Ergebnisse als Markdown“, pro Prompt ein Ausklappfeld mit Antwort, Warnungen, Detailzeile (Modell, Abschlussgrund, Token, Dauer) und rohen `usage`-Daten als einklappbarem JSON.

**Warnungen in den Ergebnissen.** `MAX_TOKENS` bedeutet: Denk- und Ausgabe-Token haben das Limit erreicht, die Antwort ist abgeschnitten. Jeder andere Abschlussgrund außer `STOP` (z. B. `SAFETY`) wird ebenfalls gemeldet.

## 5. Kosten sparen: Batch, Caching, Gespräche

### Argon-Besonderheiten (laut Google-Doku, Stand Oktober 2026)

| Thema | Verhalten | Folge in der App |
|---|---|---|
| Modell-ID | `gemini-4-argon` | Voreinstellung im Feld „Modell“ |
| Denktiefe | `thinking_level` ∈ {`low`, `medium`, `high`}, Standard `medium` | Auswahl „Denktiefe“, „Standard“ sendet nichts |
| Denk-Token | zählen zum Limit `max_output_tokens`, werden bei Gemini-Modellen üblicherweise wie Ausgabe-Token berechnet (Preisseite prüfen) | Warnung bei `MAX_TOKENS` |
| Explizites Caching | wird für Argon nicht unterstützt und liefert einen Fehler | kein `cached_content` in der App |
| Implizites Caching | automatisch ab 4.096 Token, gecachte Eingabe-Token 95 % günstiger, keine Garantie | PDFs ganz vorn in fester Reihenfolge |
| Batch | JSONL-Datei mit Zeilen `{"key", "request"}`, 50 % des Standardpreises, Ziel 24 Stunden, Ergebnisse 6 Wochen abrufbar | Batch-Modus |
| Batch-Ausgabelimit | 32.768 Token, kein Long Decode (API-Fehler sonst: „max_output_tokens cannot exceed 32768“) | Feld im Batch-Modus begrenzt |
| Ausgabe pro Anfrage | 32.768 (nicht streamend), 65.536 (streamend). Mit google-genai ≥ 2.30.1 setzt das SDK automatisch fort (Long Decode), bis 1.048.576 | Feld im Sync-Modus bis 1.048.576 |
| Timeouts | mindestens 20 Minuten pro Aufruf empfohlen | `SYNC_TIMEOUT_MS` = 25 Minuten |
| Thought Signatures | Modellantwort unverändert in den Verlauf zurückgeben | Gespräche übernehmen `candidates[0].content` unverändert |

### Cache-Strategie der App

Implizites Caching vergleicht den **Anfang (Prefix)** einer Anfrage mit früheren Anfragen. Deshalb gilt in jeder Anfrage dieselbe Reihenfolge:

```text
[Systemanweisung]  [PDF 1] [PDF 2] …  [Prompt]
 ^ stabil           ^ immer gleiche Reihenfolge, ganz vorn      ^ variabel, ganz hinten
```

- Nichts Variables (Zeitstempel, IDs, Prompt-Text) vor die PDFs setzen.
- Andere PDF-Menge oder -Reihenfolge, andere Systemanweisung oder anderes Modell ergeben einen anderen Prefix und damit keine Treffer.
- Hochgeladene PDFs werden über ihren SHA-256 in `data/pdf_files.json` gemerkt, damit dieselbe Datei-Referenz (URI) auch nach einem Neustart benutzt wird, solange die Files API sie noch hält (derzeit 48 Stunden). Ob Google intern Dateiinhalt oder Datei-Referenz vergleicht, ist nicht dokumentiert. Eine gleiche Referenz ist der sicherere Weg.
- **Synchron** sendet strikt nacheinander. Die erste Anfrage füllt den Cache (zeigt meist „Cache 0“), die folgenden können treffen. Zeitliche Nähe hilft, eine Garantie gibt es nicht.
- **Batch** läuft asynchron, die Verarbeitungszeitpunkte bestimmt Google. Zu implizitem Caching im Batch steht in der Doku nichts. Die Batch-Doku nennt Caching dort nur über `cached_content` (explizit, bei Argon nicht möglich).
- **Gespräche** (Prompts pro Gespräch > 1): Der zweite Prompt sendet den ganzen Verlauf erneut (PDFs, erster Prompt, Antwort, zweiter Prompt). Der Anfang bis einschließlich erstem Prompt ist identisch zur vorherigen Anfrage und kann gecacht werden. Die neue Antwort und der neue Prompt werden normal berechnet. Jedes neue Gespräch beginnt wieder mit denselben PDFs, deshalb kann das PDF auch über Gesprächsgrenzen hinweg gecacht werden.
- Sicher sparen lässt sich nur, wenn das PDF gar nicht mehrfach gesendet wird (mehrere Fragen in einem Prompt) oder mit einem Modell, das explizites Caching unterstützt. Beides ist in der App nicht eingebaut.

### Beobachtung aus der Praxis

Erster echter Batch (19.10.2026): 3 Prompts, 1 PDF, je rund 80.260 Eingabe-Token, Denk-Token 2.188 bis 3.359, Ausgabe-Token 1.133 bis 1.827. **Cache-Token: überall 0.** Das deckt sich mit der Annahme, dass Batch bei Argon keine verlässlichen impliziten Treffer liefert. Ob die API das Feld in Batch-Ergebnissen unter anderem Namen führt, lässt sich im einklappbaren `usage`-JSON jeder Antwort prüfen (die App liest `cachedContentTokenCount`, `cached_content_token_count`, `totalCachedTokens`, `total_cached_tokens`).

## 6. Konfiguration

### 6.1 API-Key und Modell

- Seitenleiste „Gemini API-Key“ (Passwortfeld) oder Umgebungsvariable `GEMINI_API_KEY` (wird als Vorbelegung genutzt). Der Key wird nicht gespeichert.
- Seitenleiste „Modell“, Voreinstellung `gemini-4-argon`. Andere Modelle sind möglich, aber die App ist auf Argon zugeschnitten (Batch-Limit, `thinking_level`-Werte, Caching-Annahmen).
- Optional die Umgebungsvariable `GEMINI_UI_HOME`: Ordner, unter dem `conf/` und `data/` liegen. Voreinstellung ist der Ordner von `gemini_batch_ui.py`, unabhängig vom Arbeitsordner. Nützlich für getrennte Datenbestände und für Tests.

### 6.2 Konstanten im Code (Kopf von `gemini_batch_ui.py`)

| Konstante | Wert | Bedeutung |
|---|---|---|
| `DEFAULT_MODEL` | `gemini-4-argon` | Voreinstellung Modell |
| `BASE` | Ordner von `gemini_batch_ui.py`, oder `GEMINI_UI_HOME` | Wurzel für `conf/` und `data/` (`_base_dir()`) |
| `CONF_DIR` | `BASE/conf` | Zugangsdaten, geheim |
| `DATA_DIR` | `BASE/data` | erzeugte Daten |
| `CREDENTIALS_FILE`, `TOKEN_FILE` | `conf/credentials.json`, `conf/token.json` | Drive-OAuth-Client und Drive-Login |
| `HISTORY` | `data/batch_jobs.json` | Verlauf und Ergebnisse |
| `FILES_DB` | `data/pdf_files.json` | PDF-Hash → Datei in der Gemini Files API |
| `SCOPES` | `drive.readonly` | Drive-Berechtigung (nur lesen) |
| `POLL_SECONDS` | `10` | Abstand der automatischen Prüfung |
| `DONE` | 4 `JOB_STATE_*`-Endzustände | Wann ein Batch als abgeschlossen gilt |
| `BATCH_MAX_OUT` | `32768` | Batch-Limit für Ausgabe-Token |
| `SYNC_MAX_OUT` | `1_048_576` | Obergrenze synchron (Long Decode) |
| `SYNC_TIMEOUT_MS` | 25 Minuten | HTTP-Timeout pro synchronem Aufruf |
| `MAX_POLL_ERRORS` | `3` | So viele Abruffehler in Folge stoppen die automatische Prüfung |
| `TABS` | „Neue Anfrage“, „Jobs und Ergebnisse“ | Tab-Namen (auch Schlüsselwerte für `view`) |
| `MODE_BATCH`, `MODE_SYNC` | Radio-Texte | Werte des Modus-Schalters |
| `THINK_LEVELS` | Standard, low, medium, high | Auswahl Denktiefe |
| `HINT` | „(bitte den Tab nicht wechseln)“ | Zusatz im Statusfeld beim Start |

Weitere Voreinstellungen stehen direkt im UI-Teil: Denktiefe `high` (`st.session_state.setdefault("think", "high")`), Ausgabe-Token synchron 65.536 und im Batch 32.768, Prompts pro Gespräch 1. Wiederholungen bei vorübergehenden Fehlern in `ask()`: 3 Versuche, Pause 10 s und 20 s, nur für HTTP 429, 500, 503, 504.

### 6.3 Streamlit-Konfiguration (optional, `.streamlit/config.toml`)

```toml
[server]
fileWatcherType = "none"   # vermeidet „inotify watch limit reached“ unter Linux, Änderungen am Code laden dann nicht automatisch
maxUploadSize = 500        # MB pro hochgeladener Datei (Standard 200)
```

Streamlit liest diese Datei aus `.streamlit/` im **Arbeitsordner**, in dem `streamlit run` gestartet wird, nicht aus `conf/`. Am besten startet man die App im Projektordner.

### 6.4 Google Drive einrichten (optional)

Nur nötig für „PDFs aus Google Drive hinzufügen“. Der Datei-Upload funktioniert ohne.

1. [Google Cloud Console](https://console.cloud.google.com) mit dem Konto öffnen, auf dessen Drive zugegriffen werden soll. Neues Projekt anlegen (die Drive API ist kostenlos, ein Rechnungskonto sollte dafür in der Regel nicht nötig sein).
2. **Drive API aktivieren**: `https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com`, Projekt wählen, aktivieren.
3. **Google Auth Platform** (Menü) → „Get started“: App-Name, Support-E-Mail, Zielgruppe „External“ (mit Workspace-Konto „Internal“), Kontakt-E-Mail, Richtlinie akzeptieren.
4. Nur bei „External“: unter „Audience“ die eigene Google-Adresse als **Test user** eintragen (sonst „Zugriff blockiert“).
5. Unter „Data Access“ den Scope `https://www.googleapis.com/auth/drive.readonly` hinzufügen.
6. Unter „Clients“ → „Create client“ → Anwendungstyp **Desktop-App** → JSON herunterladen und als `conf/credentials.json` ablegen (der Ordner `conf/` liegt neben `gemini_batch_ui.py` und wird beim Start angelegt).
7. In der App „Meine PDFs aus Drive laden“. Beim ersten Zugriff öffnet sich der Browser zur Freigabe (Warnung „Google hat diese App nicht überprüft“ über „Erweitert“ bestätigen). Danach liegt `conf/token.json` vor.

Hinweise:

- Im Status „Testing“ (Zielgruppe „External“) läuft die Freigabe nach **7 Tagen** ab. Die App öffnet dann selbstständig erneut den Browser zur Anmeldung (`drive_service()` fängt `RefreshError`). Dauerhaft vermeidet das „Publish app“ unter „Audience“ (Status „In production“, Warnung bleibt) oder ein Workspace-Konto mit „Internal“.
- Ohne `conf/credentials.json` meldet die App einen klaren Fehler samt Zielordner. `conf/token.json` zu löschen erzwingt eine neue Anmeldung.
- Menünamen der Konsole können je nach Sprache und Version abweichen. Maßgeblich ist der Drive-Python-Quickstart (Abschnitt 13).

### 6.5 Abhängigkeiten

| Paket | Mindestversion | Wofür |
|---|---|---|
| `streamlit` | 1.55 | Oberfläche. Ab 1.55 kennt `st.tabs` den aktiven Tab (`key`, `on_change`, `.open`). Mit älteren Versionen läuft die App, prüft dann aber auch im anderen Tab. |
| `google-genai` | 2.30.1 | Gemini-SDK. Ab 2.30.1 setzt `generate_content` Long Decode automatisch fort. Ältere Version: synchron auf 32.768 Ausgabe-Token begrenzt (die App warnt). |
| `google-api-python-client`, `google-auth-oauthlib` | beliebig | Drive-Zugriff (werden erst bei Drive-Nutzung importiert) |

## 7. Lokale Dateien und Datenformate

Die App legt `conf/` (Konfiguration und Zugangsdaten) und `data/` (erzeugte Daten) **neben `gemini_batch_ui.py`** an, unabhängig vom Arbeitsordner. Mit der Umgebungsvariable `GEMINI_UI_HOME` lässt sich ein anderer Ordner festlegen, unter dem beide liegen.

```text
gemini-4-batch/
├── gemini_batch_ui.py              # die gesamte App (eine Datei, bewusst)
├── requirements.txt
├── README.md
├── .streamlit/config.toml          # optional, Streamlit-Einstellungen (aus dem Arbeitsordner gelesen)
├── conf/                           # Konfiguration und Zugangsdaten, GEHEIM
│   ├── credentials.json            # optional: Drive-OAuth-Client (von Hand hineinlegen)
│   └── token.json                  # automatisch: Drive-Login
└── data/                           # erzeugte Daten
    ├── batch_jobs.json             # Verlauf + Ergebnisse (+ batch_jobs.tmp beim Schreiben)
    ├── batch_jobs.defekt-<ts>.json # Sicherung, falls batch_jobs.json beschädigt war
    ├── pdf_files.json              # PDF-Hash -> Files-API-Name
    └── requests_<unixzeit>.jsonl   # je Batch die exakt gesendeten Anfragen
```

**Übernahme älterer Stände.** Lagen `credentials.json`, `token.json`, `batch_jobs.json`, `pdf_files.json`, `requests_*.jsonl` oder `batch_jobs.defekt-*.json` noch direkt neben `gemini_batch_ui.py`, verschiebt `prepare_folders()` sie beim Start nach `conf/` bzw. `data/` (Hinweis im Terminal und als Toast). Es wird nie etwas überschrieben: Liegt am Ziel schon eine gleichnamige Datei, bleibt die alte liegen. Durchsucht wird nur der Ordner der App, nie der Arbeitsordner. Tipp: Vor dem ersten Start einer neuen Version `batch_jobs.json` kopieren, denn Ergebnisse sind nach Ablauf der Aufbewahrung bei Google nicht neu abrufbar.

Empfohlene `.gitignore`:

```text
conf/credentials.json
conf/token.json
data/
.streamlit/secrets.toml
```

### `data/batch_jobs.json`: Liste von Einträgen, neueste zuerst

Batch-Eintrag (Felder `state`, `rows`, `error`, `api_model` kommen erst, wenn der Batch einen Endzustand hat):

```json
{
  "name": "batches/9bo5q27hzqyh6unvm1z9slccnav15hnkybmy",
  "label": "batch-20261019-1437",
  "model": "gemini-4-argon",
  "pdf": "Buch 1.pdf, Buch 2.pdf",
  "pdfs": ["Buch 1.pdf", "Buch 2.pdf"],
  "prompts": {"prompt-1": "…", "prompt-2": "…"},
  "system": "",
  "think": "high",
  "max_out": 32768,
  "created": "2026-10-19T14:38:00",
  "state": "JOB_STATE_SUCCEEDED",
  "rows": [],
  "error": null,
  "api_model": "models/gemini-4-argon"
}
```

Synchroner Eintrag (Zeilen wachsen live):

```json
{
  "name": "sync-20261028-143015-3f9a",
  "mode": "sync",
  "state": "RUNNING",
  "label": "sync-20261028-1430",
  "model": "gemini-4-argon",
  "pdf": "Buch.pdf",
  "pdfs": ["Buch.pdf"],
  "prompts": {"prompt-1": "…", "prompt-2": "…"},
  "system": "",
  "think": "high",
  "max_out": 65536,
  "group": 2,
  "rows": [],
  "created": "2026-10-28T14:30:15"
}
```

`state` synchron: `RUNNING`, `DONE`, `FAILED` (mit Feld `error`), `CANCELLED`. `INTERRUPTED` wird **nicht gespeichert**, sondern in `sync_view()` abgeleitet, wenn `RUNNING` in der Datei steht, aber kein Thread mehr lebt.

Eine Zeile in `rows` (beide Modi, erzeugt von `parse_line()`):

```json
{
  "key": "prompt-1",
  "prompt": "…",
  "text": "Antworttext ohne Denk-Teile",
  "model": "gemini-4-argon",
  "finish": "STOP",
  "usage": {"promptTokenCount": 80259, "cachedContentTokenCount": 0, "thoughtsTokenCount": 2188, "candidatesTokenCount": 1133},
  "error": null,
  "in": 80259, "cached": 0, "think": 2188, "out": 1133,
  "secs": 42
}
```

`secs` gibt es nur synchron. `error` ist ein Text (oder das Fehlerobjekt der API). Übersprungene Prompts eines abgebrochenen Gesprächs haben `error` = „Übersprungen: …“.

Ältere Einträge können Felder nicht haben (`system`, `pdfs`, `think`, `group`, `state`, `rows` …). Neue Felder deshalb **immer optional** lesen (`.get()`).

### `data/pdf_files.json`

```json
{"<sha256 des PDF-Inhalts>": "files/<id der Gemini Files API>"}
```

### `data/requests_<unixzeit>.jsonl`

Eine Zeile pro Prompt, genau das, was an die Batch-API ging (Anfrageformat `GenerateContentRequest`, Feldnamen in snake_case):

```json
{"key": "prompt-1", "request": {
  "contents": [{"role": "user", "parts": [
    {"file_data": {"mime_type": "application/pdf", "file_uri": "https://generativelanguage.googleapis.com/v1beta/files/…"}},
    {"text": "Prompt-Text"}]}],
  "generation_config": {"max_output_tokens": 32768, "thinking_config": {"thinking_level": "high"}},
  "system_instruction": {"parts": [{"text": "…"}]}}}
```

Nützlich zur Kontrolle: `grep -o '"thinking_level": "[a-z]*"' data/requests_*.jsonl | sort | uniq -c`.

## 8. Architektur

Eine Datei, bewusst ohne Klassen und ohne Datenbank: Funktionen oben, Oberfläche unten. Streamlit führt das Skript bei **jeder** Interaktion von oben nach unten neu aus. Alles, was länger leben muss, steckt deshalb in Dateien, in `st.session_state` oder in `runtime()`.

### Landkarte von `gemini_batch_ui.py` (von oben nach unten)

| Abschnitt | Inhalt |
|---|---|
| Konstanten | siehe 6.2 |
| Ordner | `_base_dir()` (Wurzel), `prepare_folders()` (legt `conf/` und `data/` an, übernimmt Altbestand) |
| Verlauf | `runtime()` (prozessweit: `lock`, `threads`, `cancel`), `load_jobs()`, `mutate_jobs(lock, change)` |
| Google Drive | `drive_service()` (OAuth, Neuanmeldung bei `RefreshError`), `drive_id()` (ID aus Link), `drive_fetch()` (PDF laden/exportieren) |
| Gemini | `sdk_has_long_decode()`, `upload_pdf()` (Files API + `data/pdf_files.json`), `start_batch()` (JSONL bauen, hochladen, `batches.create`), `pick()`, `parse_line()`, `parse_results()`, `refresh()` (Status + Ergebnisse laden, Endergebnis sichern), `batch_info()` |
| Synchroner Modus | `ask()` (Retry), `run_sync()` (Thread-Funktion), `start_sync()`, `sync_view()` |
| Anzeige | `show_rows()`, `sync_panel()`, `job_panel()` (läuft als Fragment) |
| Verlauf und Formular | `entry_label()`, `reuse_inputs()` (Callback), `delete_entry()` (Callback) |
| UI | Seitenleiste, Tabs, `with tab_new:` (Formular + Start-Handler), `with tab_jobs:` (Auswahl, Buttons, Fragment) |

Die UI-Grenze ist die Zeile `# ---------- UI ----------`. Alles darüber ist ohne Oberfläche testbar (siehe 9.3).

### Datenfluss Batch

```text
Formular ──► Dateien sammeln (Upload, Drive) ──► Duplikate per SHA-256 entfernen
         ──► upload_pdf() je PDF: Files API (wiederverwendet über data/pdf_files.json)
         ──► start_batch(): JSONL (key + request je Prompt) in data/requests_<ts>.jsonl,
             JSONL per Files API hochladen (mime_type "jsonl"), client.batches.create(model, src)
         ──► Verlaufseintrag (ohne state) in data/batch_jobs.json
Jobs-Tab ──► Fragment alle 10 s: refresh() → client.batches.get(name)
         ──► bei JOB_STATE_SUCCEEDED: client.files.download(dest.file_name) → parse_results() → rows
         ──► Endzustand (state, rows, error, api_model) in den Eintrag schreiben → ab da kein Abruf mehr nötig
```

### Datenfluss Synchron

```text
Formular ──► Dateien sammeln, upload_pdf() je PDF ──► start_sync(): Eintrag (state RUNNING, rows []) speichern,
             Daemon-Thread run_sync() starten, in runtime().threads eintragen
Thread   ──► je Prompt: Content bauen (PDFs nur im ersten Prompt eines Gesprächs), ask() → generate_content
         ──► Antwort per model_dump(by_alias=True) in die Zeilenform bringen (parse_line), Dauer messen, Zeile anhängen
         ──► bei Gesprächen: Modellantwort unverändert in den Verlauf, bei Fehler Rest des Gesprächs überspringen
         ──► am Ende state DONE (oder CANCELLED / FAILED)
Jobs-Tab ──► sync_panel(): Eintrag aus der Datei lesen, solange RUNNING alle 10 s neu
```

### Streamlit-Besonderheiten, die man kennen muss

- **Tab-Erkennung**: `st.tabs(TABS, key="view", on_change="rerun")` (ab 1.55). `tab_jobs.open` sagt, ob der Jobs-Tab offen ist. Der Inhalt versteckter Tabs läuft trotzdem, gesteuert wird über `.open`. Ein Tab-Wechsel löst einen **vollständigen Rerun** aus. Das unterbricht jedes Skript, das gerade lange läuft (Uploads im Start-Handler). Lange Arbeit gehört deshalb in einen Hintergrund-Thread (siehe `start_sync`). Bei älteren Streamlit-Versionen schlägt der Aufruf mit `TypeError` fehl, der Fallback ist `st.tabs(TABS)`; `getattr(tab_jobs, "open", None) is not False` ergibt dann „immer prüfen“.
- **Polling per Fragment**: `st.fragment(run_every=POLL_SECONDS if polling else None)(job_panel)(...)`. Die Entscheidung `polling` wird bei jedem vollen Lauf neu getroffen (Tab offen und Eintrag läuft). Um einen laufenden Timer zu beenden, ruft das Fragment `st.rerun()` auf (voller Rerun). Die Argumente eines Timer-Reruns sind **veraltet** (Stand des letzten vollen Laufs). Ein Fragment muss veränderliche Daten deshalb selbst neu laden (`sync_panel` liest den Eintrag aus der Datei, `job_panel` nutzt `batch_info()` und die Session).
- **Threads**: `run_sync` läuft ohne `ScriptRunContext`. Dort **keine** `st.*`-Aufrufe und keine Streamlit-Cache-Funktionen. `rt = runtime()` wird im Hauptthread geholt und als Argument übergeben. Schreibzugriffe auf den Verlauf nur über `mutate_jobs(rt.lock, …)` (liest unter Sperre, schreibt atomar über `batch_jobs.tmp` + `os.replace`).
- **Abbruch**: `rt.cancel` ist eine Menge von Eintragsnamen. `run_sync` prüft sie vor jedem Prompt. Eine laufende Anfrage wird nicht abgebrochen.
- **Unbekannte Attribute**: `DeltaGenerator` liefert für unbekannte Namen ein aufrufbares Objekt statt `AttributeError`. Darum ist `getattr(…, "open", None) is not False` korrekt für ältere Versionen.

### Session-State- und Widget-Schlüssel

| Schlüssel | Zweck |
|---|---|
| `view` | aktiver Tab (nur ab 1.55 wirksam). `reuse_inputs` setzt ihn auf „Neue Anfrage“ |
| `mode` | Modus-Radio (`MODE_BATCH` / `MODE_SYNC`) |
| `system`, `raw` | Systemanweisung, Prompts-Textfeld |
| `think` | Denktiefe (Voreinstellung per `setdefault`) |
| `max_batch`, `max_sync` | Ausgabe-Token je Modus (Voreinstellung per `setdefault`) |
| `group` | Prompts pro Gespräch |
| `label_batch`, `label_sync` | Namensfeld je Modus |
| `sel` | Auswahl im Jobs-Tab (Wert = `entry_label(eintrag)`). Der Start-Handler setzt ihn auf den neuen Eintrag |
| `drive_pdfs` | `{Anzeigename: Drive-ID}` der geladenen Drive-Liste |
| `reused_note` | einmaliger Hinweis nach „Eingaben übernehmen“ (wird beim Anzeigen entfernt) |
| `res:<name>` | Ergebnisstand eines Batches in der Sitzung (`state`, `rows`, `error`, `model`) |
| `err:<name>`, `errn:<name>` | letzte Abruffehlermeldung, Zähler aufeinanderfolgender Fehler |
| Button-Schlüssel | `chk:`, `cx:` (Batch prüfen/abbrechen), `stop:` (Lauf abbrechen), `dl:` (Download), `reuse:`, `del:` jeweils plus Eintragsname |

## 9. Weiterentwickeln

### 9.1 Spielregeln

1. **Erst diese Datei lesen**, dann `gemini_batch_ui.py` von oben nach unten. Kleine, gezielte Änderungen statt Umbauten. Die Datei bleibt eine Datei, bis sie deutlich über etwa 1.000 Zeilen wächst. Dann Logik oberhalb der UI-Grenze in Module auslagern (Importe werden gecacht, nur das Hauptskript läuft bei jedem Rerun neu).
2. **Prefix stabil halten** (Abschnitt 5). Alles, was vor oder zwischen den PDFs steht, beeinflusst die Cache-Trefferquote.
3. **Verlauf nur über `mutate_jobs`** schreiben, nie `data/batch_jobs.json` direkt. Lesen mit `load_jobs()`. Neue Eintragsfelder optional (`.get()`), weil alte Einträge sie nicht haben.
4. **Zeilenform nur in `parse_line()` ändern** und dort neue Felder ergänzen. Anzeige in `show_rows()`. Beide Modi nutzen dieselbe Zeilenform, und gespeicherte Zeilen alter Läufe haben neue Felder nicht (`.get()`).
5. **Lange Arbeit in einen Thread** mit Verlaufseintrag und Polling-Fragment legen (Muster: `start_sync` → `run_sync` → `sync_panel`). Alles im Hauptskript kann durch einen Tab-Wechsel unterbrochen werden.
6. **Widgets, die per Code gesetzt werden sollen, brauchen einen `key`.** Voreinstellung über `st.session_state.setdefault(key, wert)` setzen und dem Widget kein `value=`/`index=` mitgeben (sonst Streamlit-Warnung). Widget-Zustand nur **vor** dem Erzeugen des Widgets im selben Lauf ändern (in Callbacks oder weiter oben im Skript), danach gibt es eine Ausnahme.
7. **Keine verschachtelten Expander** (Streamlit verbietet es). Popover in Spalten ist erlaubt.
8. **Fragmente**: Argumente sind in Timer-Läufen veraltet, Timer mit `st.rerun()` beenden, Zustände für Abbruchbedingungen aus Datei oder Session lesen.
9. **Fehler verständlich melden** (deutsch, mit Hinweis, was zu tun ist). Geheimnisse (API-Key, Tokens) nie loggen oder speichern.
10. **Modellverhalten nicht raten.** Bei neuen Parametern die aktuelle Google-Doku prüfen und im Zweifel die rohen `usage`-Daten und Fehlermeldungen der API ansehen. Argon hat Eigenheiten (Abschnitt 5).
11. **Stil**: deutsche Oberflächentexte und Kommentare, 4 Leerzeichen, Zeilen bis etwa 120 Zeichen, flache Funktionen, Docstrings kurz.

### 9.2 Rezepte

**Neuen Generierungsparameter hinzufügen (z. B. Temperatur)**

1. Widget mit `key` neben „Denktiefe“ ergänzen (Voreinstellung per `setdefault`).
2. In `start_batch()` in das Dict `gen` aufnehmen, in `run_sync()` in `types.GenerateContentConfig(...)`.
3. Wert im Eintrag speichern (`start_sync()` bzw. das Batch-Eintrags-Dict im Start-Handler).
4. `reuse_inputs()` und die Beschriftung im Jobs-Tab erweitern.
5. Testen (9.3). In der Argon-Doku prüfen, ob der Parameter noch wirkt.

**Neuen Modus hinzufügen (z. B. ein weiterer Rabatt-Tarif)**

1. Konstante `MODE_…` und Eintrag im Modus-Radio.
2. Startfunktion nach dem Muster `start_sync()`. Eintrag mit `"mode": "<neu>"`.
3. `job_panel()` verteilt nach `j.get("mode")`. Neues Panel ergänzen. Entscheidung `running` im `with tab_jobs:`-Block ergänzen.
4. `reuse_inputs()` für den neuen Modus erweitern.

**Neues Ergebnisfeld anzeigen**: Feld in `parse_line()` berechnen, in `show_rows()` ausgeben (mit `.get()`), bei Bedarf in den Markdown-Download aufnehmen.

**Explizites Caching für andere Modelle**: Für Argon nicht möglich. Für Modelle, die es unterstützen, wäre der Weg: Cache mit den PDFs anlegen (`client.caches.create(...)` mit Laufzeit), im Request nur noch `cached_content` referenzieren und die PDFs weglassen, Speicherkosten und Mindestgrößen beachten. Als Umschalter nur anzeigen, wenn das Modell es kann. Garantiert die Ersparnis, kostet aber Speicherzeit.

**Weitere Ideen** stehen in Abschnitt 11.

### 9.3 Testen ohne echte API

Die App wurde bisher überwiegend mit simulierten Diensten getestet, es gibt keine echte Testsuite im Repository. Vorgehen, das sich bewährt hat:

1. **Syntax**: `python -m py_compile gemini_batch_ui.py`.
2. **Logik ohne Oberfläche**: alles oberhalb der UI-Grenze ausführen und Funktionen einzeln aufrufen:

   ```python
   import os, tempfile
   from pathlib import Path
   os.environ["GEMINI_UI_HOME"] = tempfile.mkdtemp()   # sonst entstehen conf/ und data/ im Projektordner
   src = Path("gemini_batch_ui.py").read_text()
   ns = {"__name__": "appmod"}
   exec(src.split("# ---------- UI ----------")[0], ns)
   # ns["parse_line"], ns["start_batch"], ns["run_sync"] … mit Fake-Clients aufrufen
   ```

3. **Oberfläche mit Streamlits `AppTest`** und einem Fake-Client:

   ```python
   import os, tempfile, time
   from pathlib import Path
   APP = str(Path("gemini_batch_ui.py").resolve())
   os.environ["GEMINI_UI_HOME"] = tempfile.mkdtemp()   # conf/ und data/ landen in einem Wegwerf-Ordner
   os.environ["GEMINI_API_KEY"] = "test"
   import google.genai as g
   from google.genai import types
   from streamlit.testing.v1 import AppTest

   class FakeModels:
       def generate_content(self, model, contents, config):
           return types.GenerateContentResponse(
               candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text="Antwort")]),
                                           finish_reason="STOP")],
               usage_metadata=types.GenerateContentResponseUsageMetadata(
                   prompt_token_count=100, cached_content_token_count=0, candidates_token_count=5, thoughts_token_count=5),
               model_version="gemini-4-argon")

   class FakeClient:
       def __init__(self, api_key=None): self.models = FakeModels()

   g.Client = FakeClient                         # vor at.run() ersetzen

   at = AppTest.from_file(APP, default_timeout=30)
   at.run()
   at.radio(key="mode").set_value("Synchron (nacheinander, Cache-Treffer möglich)")
   at.text_area(key="raw").set_value("Frage 1\n---\nFrage 2")
   at.run()
   [b for b in at.button if b.label == "Synchron starten"][0].click()
   at.run()
   time.sleep(1)                                 # Hintergrund-Thread abwarten
   at.session_state["view"] = "Jobs und Ergebnisse"   # Tab wechseln
   at.run()
   assert not at.exception
   ```

   Weitere Hinweise: Widgets per `key` ansprechen (`at.text_area(key="raw")`, `at.number_input(key="group")`, `at.button(key="reuse:<name>")`). Uploads mit `at.file_uploader[0].set_value([(name, bytes, mime)])`. Für Batch braucht der Fake-Client `files.upload/get/download` und `batches.create/get/cancel`. Drive lässt sich ersetzen, indem `googleapiclient.discovery.build` und `google.oauth2.credentials.Credentials.from_authorized_user_file` überschrieben werden (und eine `token.json` unter `$GEMINI_UI_HOME/conf/` liegt).
4. **Muss jeder Test abdecken**: Start im Batch- und im Synchron-Modus, mehrere PDFs, Gespräche (Fehler im ersten Prompt eines Paares), Abbruch, Öffnen alter Einträge ohne neue Felder, beschädigte `data/batch_jobs.json`, Übernahme von Altbestand aus dem Hauptordner, Abruffehler (HTTP 404 beendet die automatische Prüfung).

### 9.4 Checkliste vor der Übergabe

- [ ] `python -m py_compile gemini_batch_ui.py` läuft durch.
- [ ] Relevante `AppTest`-Szenarien laufen, ohne dass `at.exception` gefüllt ist.
- [ ] Alte Einträge in `data/batch_jobs.json` (ohne neue Felder) öffnen weiterhin.
- [ ] Neue Voreinstellungen, Schlüssel und Dateien sind in dieser README eingetragen (Abschnitte 6, 7, 8).
- [ ] Es wird nicht behauptet, etwas sei mit der echten API geprüft, wenn es nur simuliert lief (Abschnitt 13).

## 10. Fehlersuche

| Meldung / Symptom | Ursache | Lösung |
|---|---|---|
| `400 INVALID_ARGUMENT … max_output_tokens cannot exceed 32768` | Batch unterstützt kein Long Decode | Im Batch höchstens 32.768 Token. Mehr nur synchron. |
| `OSError: inotify watch limit reached` im Terminal | Linux-Limit für Dateibeobachtung erschöpft (oft durch IDE oder Sync-Programm). Die App läuft trotzdem, nur das automatische Neuladen bei Codeänderung fehlt | `--server.fileWatcherType none` oder in `.streamlit/config.toml` setzen, oder `fs.inotify.max_user_watches` erhöhen. |
| pip-Fehler zu `tensorboard`/`tensorflow` nach Installation | `google-auth-oauthlib` oder `numpy`/`protobuf` wurden hochgezogen, bestehende Pakete verlangen ältere Versionen | Eigene Umgebung benutzen (siehe 3), oder `pip install "google-auth-oauthlib<1.1"`. Kein `-U` in gemeinsamen Umgebungen. |
| Cache-Token überall 0 | implizites Caching ist nicht garantiert. Im Batch zusätzlich undokumentiert. Die erste synchrone Anfrage hat per Definition 0 | `usage`-JSON einer Antwort ansehen. Synchron testen. PDFs, Reihenfolge und Systemanweisung unverändert lassen. |
| Drive: „Zugriff blockiert“ | Konto nicht als Test user eingetragen | Cloud Console → Audience → Test users (6.4, Schritt 4) |
| Drive: `credentials.json fehlt. Lege sie nach …/conf` | OAuth-Client nicht in `conf/` abgelegt | 6.4, Schritt 6 |
| Drive fragt nach einer Woche erneut nach Anmeldung | „Testing“-Status, Freigabe läuft nach 7 Tagen ab | erwartetes Verhalten, die App meldet sich selbst neu an. Dauerhaft: „Publish app“ oder Workspace „Internal“. |
| Start bricht ab, Statusfeld verschwindet | Tab während der Vorbereitung gewechselt (löst einen Rerun aus) | Start wiederholen und währenddessen im Tab bleiben. |
| Synchroner Lauf steht auf „unterbrochen“ | App wurde beendet oder neu gestartet, solange er lief | Bisherige Zeilen sind gespeichert. Mit „Eingaben übernehmen“ neu starten. |
| Antwort abgeschnitten, Warnung `MAX_TOKENS` | Denk- plus Ausgabe-Token haben das Limit erreicht | Denktiefe senken, Token-Limit erhöhen (synchron) oder Aufgabe auf mehrere Prompts aufteilen. |
| „Gespräch“: Prompt 2 „Übersprungen“ | Prompt 1 lieferte Fehler oder keine Antwort | Fehler in Prompt 1 beheben und neu starten. Folgende Paare laufen weiter. |
| Synchron nur 32.768 Ausgabe-Token wählbar, Warnung zu google-genai | SDK älter als 2.30.1 | `pip install -U google-genai` (in eigener Umgebung). |
| Automatische Prüfung läuft auch im Tab „Neue Anfrage“ | Streamlit älter als 1.55, kennt den aktiven Tab nicht | Streamlit aktualisieren. |
| „Abruf fehlgeschlagen … 404“ bei altem Batch | Batch existiert bei Google nicht mehr (nach Ablauf) und wurde lokal nie gesichert | Nichts zu retten. Eintrag löschen. Neue Batches sichern ihr Ergebnis lokal. |
| `data/batch_jobs.defekt-<zahl>.json` taucht auf | `data/batch_jobs.json` war beschädigt, die App hat sie gesichert und neu begonnen | Sicherung prüfen, ggf. Einträge von Hand zurückkopieren. |
| Verlauf leer nach dem Wechsel auf `conf/` und `data/` | Alte `batch_jobs.json` lag nicht neben der App, oder `GEMINI_UI_HOME` zeigt woanders hin | Datei von Hand nach `data/` kopieren bzw. `GEMINI_UI_HOME` prüfen. |

## 11. Bekannte Einschränkungen und Ideen

**Einschränkungen**

- Implizites Caching ist nicht garantiert und bei Argon die einzige Cache-Art. Im Batch gibt es dazu keine Doku, synchron ist es bestmöglich vorbereitet, aber unbewiesen.
- Synchron läuft **sequenziell**. Parallele Anfragen würden Cache-Treffer am Anfang eher verhindern.
- Der Start (Dateien laden, hochladen) läuft im Hauptskript. Ein Tab-Wechsel währenddessen bricht ihn ab (im Statusfeld vermerkt).
- Die Sperre in `runtime()` gilt nur innerhalb **eines** Streamlit-Prozesses. Nicht zwei Instanzen im selben Ordner starten.
- Der Verlauf wächst ohne Seitenaufteilung. Löschen geht nur einzeln. `data/requests_*.jsonl` sammeln sich an.
- Keine Kostenanzeige (Preise ändern sich), keine Streaming-Anzeige, keine Prompt-Bibliothek, kein Zählen der Token vor dem Start.
- Texte der Oberfläche sind fest auf Deutsch im Code.
- Drive: nur PDFs und Google-Dokumente (Export als PDF). Liste zeigt höchstens 50 Dateien, weitere per Link oder ID.

**Ideen** (grob nach Nutzen)

- Prompt-Vorlagen mit Platzhaltern und einer Werteliste (ein Prompt pro Wert), z. B. „Kapitel {k}, Abschnitt {a}“.
- Einzel-Download je Antwort als `.txt` (z. B. zum Vorlesen) und alle Ergebnisse als ZIP.
- Optionale Kostenschätzung mit **einstellbaren** Preisen (nicht fest verdrahten), Vergleich Batch gegen synchron mit Cache.
- Token zählen vor dem Start (`count_tokens`), um die PDF-Größe und die 4.096-Token-Schwelle zu sehen.
- Streaming der Antworten im synchronen Modus mit Zwischenständen im Eintrag.
- Eingaben des Formulars beim Neustart wiederherstellen.
- Tarif/Modus „Flex“ oder andere Preisstufen, falls Google sie für Argon anbietet (Doku prüfen).
- Explizites Caching als Umschalter für Modelle, die es unterstützen.
- Aufräumen alter `data/requests_*.jsonl`.

## 12. Sicherheit und Datenschutz

- **Daten verlassen den Rechner**: PDFs, Prompts und Ergebnisse gehen an Google (Gemini Files API, Batch-API, Modell). Hochgeladene Dateien liegen dort zeitlich begrenzt (derzeit 48 Stunden), Batch-Ergebnisse 6 Wochen. Das gilt für Inhalte, die diese Weitergabe erlauben.
- **Drive**: nur Lesezugriff (`drive.readonly`). Die App kann nichts ändern oder löschen.
- **Geheimnisse**: `conf/` (vor allem `credentials.json` und `token.json`) nicht weitergeben und nicht einchecken. Der API-Key steht nur im Browserfeld oder in der Umgebungsvariable.
- **Lokale Daten**: `data/` (z. B. `batch_jobs.json`, `requests_*.jsonl`) enthält Prompts und Antworten im Klartext. Ordner entsprechend schützen (`.gitignore`, Dateirechte).
- Die App ist für **einen Benutzer auf dem eigenen Rechner** gedacht, ohne Anmeldung. Streamlit lauscht standardmäßig auch im lokalen Netz (die Startmeldung nennt eine „Network URL“), dann könnte jeder im Netz die App samt API-Key-Feld bedienen. Wer das nicht möchte, startet mit `streamlit run gemini_batch_ui.py --server.address localhost`. Nicht ungeschützt im Internet betreiben.

## 13. Verifikationsstand und Quellen

**Verifikationsstand (bitte beim ersten echten Einsatz beobachten)**

- Mit der echten API bestätigt: Batch-Modus mit **einem** PDF und mehreren Prompts, Denktiefe `high`, Ergebnisabruf und Anzeige (19.10.2026). Dabei meldeten alle Antworten Cache 0.
- **Nur mit simulierten Diensten getestet**, nicht gegen echte Dienste: synchroner Modus (inklusive Long Decode und Gespräche), mehrere PDFs, Drive-Anbindung samt Neuanmeldung, lokale Sicherung der Batch-Ergebnisse, „Eingaben übernehmen“, Löschen, Fehlerzähler der automatischen Prüfung. Die Ordnerstruktur `conf/`/`data/` samt Übernahme alter Dateien lief nur in Wegwerf-Ordnern, noch nicht im echten Projektordner.
- Ungeprüft: ob die Batch-Antworten für Argon ein Cache-Feld unter anderem Namen führen (siehe `usage`-JSON), ob der synchrone Aufruf mit `thinking_level` und Long Decode bei Argon exakt wie dokumentiert antwortet.

**Quellen** (Stand Oktober 2026, bitte auf Änderungen prüfen)

- Gemini 4 Argon, Modellseite: <https://ai.google.dev/gemini-api/docs/models/gemini-4-argon>
- Was ist neu in Gemini 4 Argon (Denktiefe, Long Decode, Timeouts, Caching): <https://ai.google.dev/gemini-api/docs/generate-content/latest-model>
- Batch-API (JSONL-Format, Preis, Aufbewahrung): <https://ai.google.dev/gemini-api/docs/batch-api>
- Context Caching (implizit/explizit, Nutzungsfelder): <https://ai.google.dev/gemini-api/docs/caching>
- Drive-API-Quickstart für Python (OAuth-Einrichtung): <https://developers.google.com/workspace/drive/api/quickstart/python>
- Streamlit-Dokumentation (`st.tabs`, `st.fragment`, `AppTest`): <https://docs.streamlit.io>

## Glossar

- **Prefix**: der gemeinsame Anfang zweier Anfragen. Implizites Caching greift darauf.
- **Implizites Caching**: Google erkennt gleiche Anfänge automatisch und berechnet sie günstiger. Keine Zusage.
- **Explizites Caching**: man legt selbst einen Cache an und referenziert ihn. Garantiert die Ersparnis, für Argon nicht verfügbar.
- **Long Decode**: automatische Fortsetzung über mehrere Teilanfragen, wenn die Ausgabe das Limit pro Anfrage überschreitet (SDK ab 2.30.1, nur synchron).
- **Fragment**: ein Streamlit-Abschnitt, der sich allein neu ausführen kann (hier für die automatische Statusprüfung).
- **ScriptRunContext**: Streamlits Laufzeitkontext. Hintergrund-Threads haben keinen, deshalb dort keine `st.*`-Aufrufe.
