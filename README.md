# Gemini 4 Carbon: Batch- und Synchron-UI

Lokale Streamlit-Oberfläche, um **Gemini 4 Carbon** (`gemini-4-carbon`, der Vorgänger `gemini-4-argon` lässt sich im Feld „Modell“ weiter eintragen) oder ein **OpenAI-Modell** (z. B. `gpt-6-astra`) mit **einem oder mehreren PDFs** und **vielen Prompts** zu benutzen. Es gibt drei Betriebsarten:

- **Batch** (Gemini und OpenAI): asynchron, 50 % des Standardpreises, Ergebnis nach Minuten bis Stunden (bei OpenAI innerhalb von 24 Stunden).
- **Synchron** (nur Gemini): Prompts laufen nacheinander im Hintergrund, mit Aussicht auf Cache-Treffer für das PDF. Optional bilden je N Prompts ein Gespräch (gemeinsamer Kontext).
- **Hintergrund** (nur Gemini): Jeder Prompt läuft als Hintergrund-Interaktion bei Google (Interactions API, `background=true`). Nur so geht **Long Decode** über 65.536 Ausgabe-Token hinaus (bis 1.048.576 minus Eingabe), etwa wenn Carbon bei hoher Denktiefe das Limit von 32.768 im Batch aufbraucht. Standardpreis statt Batch-Rabatt, die App muss nicht durchgehend laufen.

Dokumentationsstand: 01.11.2026 (Python 3.12, Streamlit 1.66, google-genai 2.34). Alle Aussagen zur Google-API gelten für diesen Stand, die zu Gemini 4 Carbon beruhen auf der Google-Dokumentation vom 11.11.2026, die zum Hintergrund-Modus (Long Decode, Background Execution) auf der vom 13.11.2026. Die Aussagen zur OpenAI-Anbindung beruhen auf der OpenAI-Dokumentation vom 10.11.2026 (openai 3.46). Vor Änderungen an API-nahem Code die Links in Abschnitt 13 prüfen, Preise und Limits ändern sich.

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
- **Robust**: Batches und Hintergrund-Läufe laufen bei Google (bis zu 24 Stunden), synchrone Läufe als Thread in der App. Alles überlebt Tab-Wechsel und Seiten-Neuladen. Ergebnisse werden lokal gesichert.

**Nicht-Ziele**: Mehrbenutzerbetrieb, Hosting im Netz, Anmeldung, Kostenabrechnung, explizites Caching (Carbon und Argon unterstützen es nicht, siehe Abschnitt 5).

**Herkunft.** Die Idee war ein kleines Streamlit-Werkzeug für den Batch-Betrieb mit dem Gemini-Batch-API. Daraus wurde zuerst eine Ein-Datei-App und mit dem Umfang ein Hauptskript für die Oberfläche plus das Paket `gbui` für die Logik (Abschnitt 8). Die Oberfläche ist deutsch.

## 2. Funktionen im Überblick

- Zwei Anbieter: **Gemini** (Batch, Synchron und Hintergrund) und **OpenAI** (nur Batch, Endpunkt `/v1/responses`). Der Anbieter für neue Anfragen steht in der Seitenleiste, jeder Verlaufseintrag merkt sich seinen eigenen.
- Drei Modi: **Batch**, **Synchron** und **Hintergrund** (synchron und im Hintergrund nur mit Gemini).
- **Mehrere PDFs** pro Anfrage: Datei-Upload (mehrfach), Auswahl aus Google Drive (Liste der 50 zuletzt geänderten PDFs, mehrfach wählbar) und/oder eingefügte Drive-Links oder Datei-IDs (eine pro Zeile). Google Docs/Slides aus Drive werden als PDF exportiert. Gleiche Dateien werden nur einmal gesendet.
- **Mehrere Prompts**, getrennt durch eine Zeile mit `---`. Optional **Systemanweisung**.
- **Denktiefe** (Gemini: `low`, `medium`, `high`. OpenAI: `none` bis `max`. Jeweils „Standard“ = nichts senden) und **max. Ausgabe-Token**.
- **Gespräche (nur synchron)**: „Prompts pro Gespräch“ = N. Je N aufeinanderfolgende Prompts teilen einen Kontext, das nächste Gespräch beginnt neu.
- **Long Decode im Hintergrund (nur Gemini)**: Im Modus „Hintergrund“ startet jeder Prompt als Interaktion mit `background=true` (Interactions API). Googles Server setzen die Antwort in Schritten zu je 64K Token fort, bis zu 1.048.576 Token minus Eingabe. Das löst `MAX_TOKENS` bei hoher Denktiefe, wo ein Batch bei 32.768 Token endet. Die App fragt nur den Stand ab, wie bei einem Batch.
- **Jobs und Ergebnisse**: Verlauf aller Läufe, automatische Statusprüfung alle 10 Sekunden solange der Tab offen ist, Ergebnisse mit Token- und Cache-Angaben, Markdown-Download, Rohdaten-JSON.
- **Lokale Sicherung** der Ergebnisse (`data/batch_jobs.json`), auch nach Ablauf der Aufbewahrung bei Google.
- **Eingaben übernehmen**: Prompts und Einstellungen eines früheren Eintrags zurück ins Formular holen. **Eintrag löschen** (nur lokal).
- **Prompts im Wortlaut**: Im Tab „Jobs und Ergebnisse“ zeigt ein Klick die gesendete Anfrage im Detail (PDFs, Systemanweisung, jeder Prompt, auch solange ein Batch noch keine Ergebnisse hat) und je Ergebnis den vollständigen Prompt.
- **Gespräch fortsetzen**: Zu jeder Antwort erzeugt die App auf Klick den bisherigen Gesprächsverlauf als Text (früherer Prompt und deine Antwort dazu, bei synchronen Gesprächen alle Runden) und legt ihn ins Formular „Neue Anfrage“. Darunter schreibt man den neuen Prompt. Das geht beliebig oft hintereinander, der Verlauf wächst dabei nur um eine Runde.
- **Wiederverwendung hochgeladener PDFs** über Neustarts hinweg (`data/pdf_files.json` bei Gemini, `data/openai_files.json` bei OpenAI), damit die Datei-Referenz gleich bleibt.
- **Drive-Anmeldung erneuert sich selbst**, wenn die Freigabe abgelaufen ist.
- **Aufgeräumte Ordner**: Zugangsdaten in `conf/`, erzeugte Daten in `data/`. Dateien eines älteren Stands werden beim Start dorthin übernommen.

## 3. Schnellstart

**Voraussetzungen**

- Python 3.12 (vom Besitzer genutzt). Die App und die Tests unterstützen mindestens Python 3.12, außerdem mindestens Streamlit 1.67.0 und google-genai 3.3.0 (Abschnitt 6.5). Ältere Versionen werden weder unterstützt noch getestet.
- Ein API-Key für den Anbieter, den man nutzt: Gemini (in Google AI Studio erzeugen) und/oder OpenAI (auf der OpenAI-Plattform erzeugen).
- Optional für Drive: OAuth-Zugang, siehe 6.4.

**Installation** (am besten in einer eigenen Umgebung, damit andere Pakete unberührt bleiben):

```bash
conda create -n gemini-batch python=3.12 -y
conda activate gemini-batch
pip install -r requirements.txt
```

Für die Tests zusätzlich `pip install -r requirements-dev.txt` (Abschnitt 9.3).

`requirements.txt`:

```text
streamlit>=1.67.0
google-genai>=3.3.0
openai>=1.100.0
google-api-python-client
google-auth-oauthlib
```

Kein `pip install -U -r ...` in einer gemeinsam genutzten Umgebung (z. B. Anaconda „base“ mit TensorFlow): `-U` hebt unbeteiligte Pakete an und erzeugt Abhängigkeitskonflikte.

**Starten**

```bash
export GEMINI_API_KEY="dein-key"      # optional, sonst in der Seitenleiste eintragen
export OPENAI_API_KEY="dein-key"      # optional, für den Anbieter OpenAI
streamlit run src/gemini_batch_ui.py  # im Projektordner ausführen, öffnet http://localhost:8501
```

Anderer Port: `streamlit run src/gemini_batch_ui.py --server.port 8502`. Beenden mit Strg+C. Laufende synchrone Läufe brechen dann ab und erscheinen später als „unterbrochen“. Beim ersten Start legt die App die Ordner `conf/` und `data/` im Projektordner neben `src/` an (Abschnitt 7).

**Erster Lauf in 60 Sekunden**

1. Seitenleiste: Anbieter wählen (Gemini oder OpenAI), API-Key prüfen, Modell (`gemini-4-carbon` bzw. `gpt-6-astra`).
2. Tab „Neue Anfrage“: Modus wählen (nur bei Gemini), PDF hochladen, Prompts eintragen (mehrere mit `---` auf eigener Zeile trennen).
3. „Batch starten“ oder „Synchron starten“. Der neue Eintrag ist danach im Tab „Jobs und Ergebnisse“ vorausgewählt.

## 4. Bedienung

### Seitenleiste

| Element | Bedeutung |
|---|---|
| Anbieter für neue Anfragen | `Gemini` (Voreinstellung) oder `OpenAI`. Das Formular „Neue Anfrage“ passt sich an: Mit OpenAI entfallen der Modus (es gibt nur Batch) und „Prompts pro Gespräch“, und die Denktiefe kennt die Stufen von OpenAI. Einträge im Tab „Jobs und Ergebnisse“ werden unabhängig von dieser Wahl mit dem Anbieter und dem Key abgefragt, zu denen sie gehören. Beim Hin- und Herwechseln merkt sich die App die Werte beider Anbieter (Modell, Denktiefe, Token-Limit), Prompts und Systemanweisung gelten für beide. |
| Gemini API-Key, OpenAI API-Key | Vorbelegt aus `GEMINI_API_KEY` bzw. `OPENAI_API_KEY`, beide dürfen eingetragen bleiben. Fehlt der Key des gewählten Anbieters, bleibt die App stehen und sagt es. Fehlt der Key des Anbieters eines Verlaufseintrags, sind dort „Status prüfen / Ergebnisse laden“ und „Batch abbrechen“ (bei Hintergrund-Einträgen „Läufe abbrechen“) gesperrt, gesicherte Ergebnisse bleiben sichtbar. |
| Modell | Freitext, je Anbieter ein eigenes Feld (Voreinstellung `gemini-4-carbon` bzw. `gpt-6-astra`). Bei OpenAI muss das Modell die Batch API und PDF-Eingaben unterstützen. |

### Tab „Neue Anfrage“

| Element | Bedeutung |
|---|---|
| Modus | Batch (asynchron, günstiger), Synchron (nacheinander, als Thread in der App) oder Hintergrund (Long Decode: jeder Prompt läuft als Interaktion bei Google, Standardpreis). Mit OpenAI gibt es nur Batch, der Schalter entfällt. |
| PDF-Dateien | Mehrere Dateien möglich. Ein zweites PDF ersetzt das erste nicht. |
| PDFs aus Google Drive | Button „Meine PDFs aus Drive laden“ füllt die Mehrfachauswahl. Zusätzlich Links oder Datei-IDs einfügen. Reihenfolge: hochgeladene Dateien, dann Drive-Auswahl, dann eingefügte Links. Die Reihenfolge steht als Hinweis unter dem Bereich. |
| Systemanweisung | Optional, gilt für jede Anfrage. |
| Prompts | Ein Textfeld, mehrere Prompts durch eine Zeile nur mit `---` trennen. Beginnt ein Prompt mit einem Gesprächsverlauf (aus „Gespräch fortsetzen“), steht der neue Prompt unter `[Mein neuer Prompt]`. Solange dort nichts steht, ist der Start gesperrt. |
| Prompts pro Gespräch | Nur synchron. 1 = jeder Prompt für sich. 2 = je zwei Prompts bilden ein Gespräch (der zweite sieht den ersten und dessen Antwort), die nächsten zwei ein neues usw. Das PDF geht in jedem Gespräch mit dem ersten Prompt raus. |
| Denktiefe | Gemini: „Standard“ (kein Parameter, Carbon nutzt dann `medium`), `low`, `medium`, `high`. Voreinstellung `high`. Carbon denkt länger als Argon: `medium` ist laut Google für die meisten Aufgaben richtig, `high` verbraucht die meisten Token. OpenAI: „Standard“ (kein Parameter, es gilt die Voreinstellung des Modells), `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. Voreinstellung „Standard“. Welche Stufen ein Modell kennt, hängt vom Modell ab (z. B. kennt `gpt-6-astra` kein `none`). Die API lehnt andere ab, der Fehler steht dann je Prompt im Ergebnis. |
| Max. Ausgabe-Token | Gemini-Batch: bis 32.768 (Voreinstellung 32.768). Gemini synchron: bis 1.048.576 (Voreinstellung 65.536), über 32.768 setzt das SDK automatisch Long Decode ein. Gemini Hintergrund: bis 1.048.576 (Voreinstellung 262.144), über 65.536 setzen Googles Server Long Decode ein, die Eingabe zählt mit (höchstens 1.048.576 minus Eingabe). OpenAI: bis 128.000 (Voreinstellung 32.768), je nach Modell darunter. Denk-Token zählen zum Limit. |
| Name | Bezeichnung des Eintrags im Verlauf. |
| Start-Button | Lädt PDFs hoch, sendet bzw. startet. Während dieser Vorbereitung nicht den Tab wechseln, sonst bricht der Start ab. |

Ohne angehängtes PDF werden die Prompts ohne PDF gesendet (steht als Hinweis im Formular).

### Tab „Jobs und Ergebnisse“

- **Eintrag**: Auswahlliste, neuester Eintrag oben.
- **Eingaben übernehmen**: füllt „Neue Anfrage“ mit Prompts, Systemanweisung, Anbieter, Modell, Modus, Denktiefe, Token-Limit und Gesprächsgröße. PDFs müssen neu angehängt werden, bereits hochgeladene Dateien werden dabei wiederverwendet.
- **Eintrag löschen** (Bestätigung im Popover): entfernt den lokalen Eintrag samt gesicherter Ergebnisse. Beim Anbieter bleibt alles unverändert. Deaktiviert, solange der Eintrag läuft.
- **Gesendete Anfrage im Detail** (Schalter, standardmäßig aus): zeigt die PDFs in ihrer Reihenfolge, die Systemanweisung und jeden Prompt im Wortlaut, bei synchronen Gesprächen mit Gespräch und Runde. Das geht auch, solange ein Batch noch läuft und es noch keine Ergebnisse gibt.
- **Batch**: Buttons „Status prüfen / Ergebnisse laden“ und „Batch abbrechen“. Solange der Tab offen und der Batch nicht fertig ist, prüft die App alle 10 Sekunden selbst. Ist der Batch fertig, werden die Ergebnisse geladen und lokal gesichert. Danach braucht ein Öffnen keinen Abruf mehr. Bei OpenAI-Einträgen („Batch (OpenAI)“) zeigt die Statuszeile den Status der Batch API (`validating`, `in_progress`, `finalizing`, `completed`, `failed`, `expired`, `cancelling`, `cancelled`). Bei `expired` und `cancelled` lädt die App die bis dahin fertigen Antworten, die übrigen stehen mit Fehlertext in der Liste. Scheitert der ganze Batch (`failed`, z. B. wegen des Token-Limits der Warteschlange), steht der Grund als Fehlermeldung über den Ergebnissen.
- **Hintergrund** („Hintergrund (Long Decode)“ in der Kopfzeile): wie beim Batch die Buttons „Status prüfen / Ergebnisse laden“ und „Läufe abbrechen“ und die automatische Prüfung alle 10 Sekunden, solange der Tab offen und nicht alles beendet ist. Je Prompt läuft eine eigene Interaktion bei Google. Die Statuszeile zeigt `in_progress`, solange mindestens eine läuft (dazu „n von m Prompts fertig“), danach `completed` (einzelne Fehler stehen in den Ergebnissen), `cancelled` (alle abgebrochen) oder `failed` (alle fehlgeschlagen). Fertige Antworten erscheinen sofort, auch wenn andere noch laufen. Erst wenn alle beendet sind, sichert die App das Endergebnis lokal. „Läufe abbrechen“ bricht alle Interaktionen ab, bezahlt wird, was bis dahin angefallen ist.
- **Synchron**: Zustand `läuft`, `fertig`, `abgebrochen`, `fehlgeschlagen` oder `unterbrochen`, Fortschritt „n von m Prompts“, Button „Lauf abbrechen“ (greift nach der gerade laufenden Anfrage).
- **Ergebnisse**: Summenzeile (Eingabe-Token gesamt und davon aus dem Cache), Download „Ergebnisse als Markdown“, pro Prompt ein Ausklappfeld mit Antwort, Warnungen, Detailzeile (Modell, Abschlussgrund, Token, Dauer) und rohen `usage`-Daten als einklappbarem JSON. Die Überschrift zeigt nur die ersten 90 Zeichen des Prompts (bei einer Fortsetzung ein ↪ und den neuen Prompt). Der Schalter „Prompt im Wortlaut“ zeigt den ganzen Prompt. Unter der Antwort stehen „Gespräch fortsetzen“ und der Schalter „Verlaufstext zeigen“.

**Warnungen in den Ergebnissen.** `MAX_TOKENS` bedeutet: Denk- und Ausgabe-Token haben das Limit erreicht, die Antwort ist abgeschnitten. Jeder andere Abschlussgrund außer `STOP` (z. B. `SAFETY`) wird ebenfalls gemeldet. Bei einem Gemini-Batch weist die App bei `MAX_TOKENS` zusätzlich auf den Modus „Hintergrund“ hin. Im Hintergrund-Modus gilt der Status der Interaktion als Abschlussgrund: `completed` ist `STOP`, `incomplete` (alle `max_output_tokens` verbraucht, der Text ist leer, wenn das Modell noch dachte) ist `MAX_TOKENS`, jeder andere Status erscheint in Großbuchstaben (z. B. `FAILED`, `CANCELLED`).

**Token bei OpenAI.** OpenAI rechnet Denk-Token in `output_tokens` ein. Die App trennt sie, damit die Zeilen beider Anbieter gleich zu lesen sind: „Denken“ = `reasoning_tokens`, „Ausgabe“ = `output_tokens` minus `reasoning_tokens`, „Cache“ = `cached_tokens`. Eine unvollständige Antwort (`incomplete` wegen `max_output_tokens`) erscheint als `MAX_TOKENS`, damit dieselbe Warnung greift, jeder andere Grund (z. B. `content_filter`) steht im Wortlaut der API da.

**Gespräch fortsetzen.** Gemini behält zwischen zwei Anfragen nichts. Wer ein früheres Gespräch weiterführen will, muss es mitsenden. „Gespräch fortsetzen“ (unter jeder Antwort mit Text) macht das in einem Schritt:

1. Die App baut aus dem früheren Prompt und deiner Antwort dazu einen Text (Format in Abschnitt 7). Bei synchronen Gesprächen (Prompts pro Gespräch größer als 1) gehören alle früheren Runden dieses Gesprächs bis zur gewählten Antwort dazu.
2. Sie legt den Text ins Feld „Prompts“ von „Neue Anfrage“ und übernimmt Systemanweisung, Modus, Denktiefe und Token-Limit des Eintrags. „Prompts pro Gespräch“ steht danach auf 1, weil der Verlauf im Prompt steckt. Die PDFs müssen wieder angehängt werden (der Hinweis nennt sie).
3. Man schreibt den neuen Prompt unter `[Mein neuer Prompt]` und startet. Der Start bleibt gesperrt, solange dort nichts steht.

„Verlaufstext zeigen“ zeigt denselben Text zum Kopieren, z. B. für ein anderes Programm.

- **Beliebig oft**: Setzt man eine Fortsetzung selbst fort, liest die App den Verlauf aus dem gesendeten Prompt zurück und hängt nur die neue Runde an (Runde 1, 2, 3 …). Verläufe werden nicht ineinander geschachtelt.
- **Kosten**: Der Verlauf geht bei jeder Anfrage als Text mit und zählt als Eingabe. Lange Verläufe kosten entsprechend. Die PDFs stehen weiter an erster Stelle, der gemeinsame Anfang (Prefix) für das implizite Caching bleibt also gleich.
- **Was es technisch ist**: eine einzelne Nutzernachricht mit eingefügtem Verlauf, kein echter Mehr-Runden-Verlauf mit Modellrolle (siehe Abschnitt 11).

## 5. Kosten sparen: Batch, Caching, Gespräche

### Carbon-Besonderheiten (laut Google-Doku, Stand 11.11.2026)

Carbon (`gemini-4-carbon`, seit 10.11.2026) und sein Vorgänger Argon haben laut Google dieselbe Schnittstelle, dieselben Denkstufen, dasselbe Kontextfenster und denselben Preis. Der Umstieg ist deshalb nur ein anderer Modellname. Der Unterschied: Carbon verbraucht bei schweren Aufgaben mehr Denk-Token und braucht länger. Die Tabelle gilt für beide Modelle, wo nichts anderes steht. Ältere Verlaufseinträge behalten ihr Modell (z. B. `gemini-4-argon`), „Eingaben übernehmen“ trägt es im Feld „Modell“ wieder ein.

| Thema | Verhalten | Folge in der App |
|---|---|---|
| Modell-ID | `gemini-4-carbon` (Vorgänger: `gemini-4-argon`) | Voreinstellung im Feld „Modell“ |
| Denktiefe | `thinking_level` ∈ {`low`, `medium`, `high`}, Standard `medium` | Auswahl „Denktiefe“, „Standard“ sendet nichts |
| Denk-Token | zählen zum Limit `max_output_tokens` und werden wie Ausgabe-Token berechnet (Preisseite). Carbon kann mehr davon verbrauchen als Argon. Google empfiehlt ein Limit ab 32.768, zum Sparen die Denktiefe zu senken | Warnung bei `MAX_TOKENS`, Voreinstellungen 32.768 (Batch), 65.536 (synchron) und 262.144 (Hintergrund) |
| Explizites Caching | wird für Carbon (wie für Argon) nicht unterstützt, Aufrufe mit `cached_content` entfallen | kein `cached_content` in der App |
| Implizites Caching | automatisch ab 4.096 Token, gecachte Eingabe-Token 95 % günstiger, keine Garantie | PDFs ganz vorn in fester Reihenfolge |
| Batch | JSONL-Datei mit Zeilen `{"key", "request"}`, 50 % des Standardpreises, Ziel 24 Stunden, Ergebnisse 6 Wochen abrufbar | Batch-Modus |
| Batch-Ausgabelimit | 32.768 Token, kein Long Decode (API-Fehler sonst: „max_output_tokens cannot exceed 32768“). Der Batch läuft über `generateContent` (nicht streamend: 32.768 je Anfrage), und die Interactions API, die 65.536 und Long Decode kennt, bietet laut Google noch keinen Batch. Beobachtet bei Argon, für Carbon nennt die Doku keinen anderen Wert (ungeprüft). Prüfen lässt es sich so: `BATCH_MAX_OUT` auf `65536` setzen und einen kleinen Batch starten, lehnt die API ab, erscheint der Fehler schon beim Start | Feld im Batch-Modus begrenzt |
| Ausgabe pro Anfrage | `generateContent`: 32.768 (nicht streamend), 65.536 (streamend). Das SDK setzt automatisch fort (Long Decode), bis 1.048.576 | Feld im Sync-Modus bis 1.048.576 |
| Hintergrund (Long Decode) | Interactions API: 65.536 je Interaktion, wenn `background` fehlt oder `max_output_tokens` nicht gesetzt ist (höhere Werte ohne `background=true` lehnt die API mit 400 ab). Mit `background=true` (verlangt `store=true`) und `max_output_tokens` über 65.536 setzen Googles Server die Antwort in Schritten zu je 64K Token fort, bis 1.048.576 minus Eingabe, Laufzeit bis 24 Stunden. Jedes Token jedes Schritts wird berechnet, wiederholte Eingabe ist durch implizites Caching 95 % günstiger. Google bewahrt Interaktionen bei kostenpflichtigen Konten bis zu 55 Tage auf (in AI Studio auf 7, 14, 28 oder 55 Tage einstellbar), bei kostenlosen 1 Tag | Modus „Hintergrund“, Feld bis 1.048.576, Voreinstellung 262.144 (Empfehlung der Doku für schwere Aufgaben) |
| Timeouts | mindestens 15 bis 20 Minuten pro Aufruf empfohlen (Carbon denkt länger als Argon) | `SYNC_TIMEOUT_MS` = 25 Minuten |
| Preis | wie Argon. Standard je 1 Mio. Token: 2 $ Eingabe und 10 $ Ausgabe (inklusive Denk-Token) bis 30.11.2026, ab 01.12.2026 4 $ und 20 $. Batch: die Hälfte | keine Kostenanzeige in der App (Preisseite prüfen) |
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
- **Batch** läuft asynchron, die Verarbeitungszeitpunkte bestimmt Google. Zu implizitem Caching im Batch steht in der Doku nichts. Die Batch-Doku nennt Caching dort nur über `cached_content` (explizit, bei Carbon nicht möglich).
- **Hintergrund** startet alle Prompts gleichzeitig. Ein Cache-Treffer für die PDFs ist deshalb weniger wahrscheinlich als synchron, wo die erste Anfrage den Cache füllt. Bei Long Decode zählt `total_input_tokens` die Eingabe jedes 64K-Schritts erneut mit (vor allem als Cache-Token) und ist deshalb viel größer als der Kontext. Für dessen Größe gilt: PDF-Token plus Denk- plus Ausgabe-Token.
- **Gespräche** (Prompts pro Gespräch > 1): Der zweite Prompt sendet den ganzen Verlauf erneut (PDFs, erster Prompt, Antwort, zweiter Prompt). Der Anfang bis einschließlich erstem Prompt ist identisch zur vorherigen Anfrage und kann gecacht werden. Die neue Antwort und der neue Prompt werden normal berechnet. Jedes neue Gespräch beginnt wieder mit denselben PDFs, deshalb kann das PDF auch über Gesprächsgrenzen hinweg gecacht werden.
- Sicher sparen lässt sich nur, wenn das PDF gar nicht mehrfach gesendet wird (mehrere Fragen in einem Prompt) oder mit einem Modell, das explizites Caching unterstützt. Beides ist in der App nicht eingebaut.

### Beobachtung aus der Praxis

Erster echter Batch (19.10.2026, noch mit Argon): 3 Prompts, 1 PDF, je rund 80.260 Eingabe-Token, Denk-Token 2.188 bis 3.359, Ausgabe-Token 1.133 bis 1.827. **Cache-Token: überall 0.** Das deckt sich mit der Annahme, dass Batch bei Argon keine verlässlichen impliziten Treffer liefert. Für Carbon liegt noch keine eigene Beobachtung vor. Ob die API das Feld in Batch-Ergebnissen unter anderem Namen führt, lässt sich im einklappbaren `usage`-JSON jeder Antwort prüfen (die App liest `cachedContentTokenCount`, `cached_content_token_count`, `totalCachedTokens`, `total_cached_tokens`).

Erster Carbon-Batch (12.11.2026, der Eintrag aus Issue #19): Denktiefe `high`, max. 32.768 Ausgabe-Token, Eingabe 32.407 Token. Ergebnis: `finishReason: MAX_TOKENS` mit 32.765 Denk- und nur 3 Ausgabe-Token, also keine Antwort: Carbon hat das gesamte Limit mit Denken verbraucht. Abhilfe: Modus „Hintergrund“ mit höherem Limit (Long Decode) oder eine niedrigere Denktiefe.

### OpenAI-Besonderheiten (laut OpenAI-Doku, Stand 10.11.2026)

| Thema | Verhalten | Folge in der App |
|---|---|---|
| Schnittstelle | Die App nutzt nur die Responses API (Batch-Endpunkt `/v1/responses`). Sie nimmt `reasoning` und PDFs als `file_id` entgegen | `OPENAI_ENDPOINT` |
| Batch | JSONL-Datei mit Zeilen `{"custom_id", "method", "url", "body"}`, 50 % des Standardpreises, Fenster `24h`. Eine Batch-Datei darf nur Anfragen an **ein** Modell enthalten, höchstens 50.000 Anfragen und 200 MB | Batch-Modus, ein Modell je Batch |
| Warteschlangenlimit | OpenAI begrenzt je Modell und Kontostufe die Summe der Token aller eingereihten Batches. Ein langes PDF zählt in jedem Prompt voll mit. Überschreitet ein Batch das Limit, endet er mit `failed` | Der Fehlertext (typisch `token_limit_exceeded`) steht im Eintrag |
| PDFs | Files API mit Zweck `user_data`, in der Anfrage als `input_file` mit `file_id`. Pro Datei unter 50 MB, zusammen höchstens 50 MB je Anfrage | `upload_pdf_openai()` |
| PDF-Token | OpenAI schickt Text **und** Seitenbilder des PDFs ans Modell, das erhöht die Eingabe-Token. `detail` (`auto`, `low`, `high`) steuert die Bildverarbeitung. `auto` bedeutet bei GPT-5.6 und neuer `high`, bei älteren Modellen `low` | `detail` wird nicht gesetzt (Idee in Abschnitt 11) |
| Denktiefe | `reasoning.effort` mit `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. Welche Stufen ein Modell kennt, ist verschieden (`gpt-6-astra` kennt kein `none`) | Auswahl „Denktiefe“ mit allen Stufen, „Standard“ sendet nichts. Die API lehnt unpassende Stufen ab |
| Denk-Token | zählen zu `max_output_tokens` und sind in `output_tokens` enthalten (`output_tokens_details.reasoning_tokens`) | `parse_openai_line()` trennt sie ab |
| Caching | automatisch ab 1.024 Token bei gleichem Prefix, ohne Parameter. Bei GPT-5.6 und neuer wird das Schreiben in den Cache gesondert berechnet (`cache_write_tokens` in den Nutzungsdaten) | PDFs ganz vorn. Spalte „Cache“ = `cached_tokens` |
| Dateien bei OpenAI | `user_data`-Dateien bleiben bis zum Löschen, wenn man keinen Ablauf setzt (mit Ablauf: 1 Stunde bis 30 Tage) | Ablauf nach 3 Tagen (`OPENAI_FILE_TTL`) |
| Ergebnisse | `output_file_id` (Antworten) und `error_file_id` (fehlgeschlagene Anfragen). Bei `expired` und `cancelled` liegen dort die bis dahin fertigen Antworten und die übrigen Fehler | `fetch_openai_batch()` liest beide Dateien |

Die **Cache-Strategie** (Reihenfolge `[Systemanweisung] [PDF 1] [PDF 2] … [Prompt]`) gilt bei OpenAI genauso: Die Anfragen eines Batches laufen in unbestimmter Reihenfolge und Zeit, ob und wie oft sie Treffer bekommen, ist unbekannt. Die Preise für Eingabe, Cache-Lesen und Cache-Schreiben stehen je Modell auf der OpenAI-Preisseite.

## 6. Konfiguration

### 6.1 API-Key und Modell

- Seitenleiste „Gemini API-Key“ und „OpenAI API-Key“ (Passwortfelder) oder die Umgebungsvariablen `GEMINI_API_KEY` und `OPENAI_API_KEY` (werden als Vorbelegung genutzt). Die Keys werden nicht gespeichert. Gebraucht wird der Key des gewählten Anbieters, dazu der des anderen Anbieters, wenn man dessen Verlaufseinträge abfragen will.
- Seitenleiste „Anbieter für neue Anfragen“: `Gemini` (Voreinstellung) oder `OpenAI`.
- Seitenleiste „Modell“, je Anbieter ein eigenes Feld. Gemini, Voreinstellung `gemini-4-carbon`: Andere Modelle sind möglich (z. B. `gemini-4-argon`, laut Google schneller und mit weniger Denk-Token), aber die App ist auf die Gemini-4-Modelle zugeschnitten (Batch-Limit, `thinking_level`-Werte, Caching-Annahmen). OpenAI, Voreinstellung `gpt-6-astra`: Jedes Modell mit Responses API, Batch API und PDF-Eingabe geht, etwa `gpt-6.1-sol` oder `gpt-6-luna` (Namen laut OpenAI-Doku, Stand 10.11.2026).
- Optional die Umgebungsvariable `GEMINI_UI_HOME`: Ordner, unter dem `conf/` und `data/` liegen. Voreinstellung ist der Projektordner (der Ordner über `src/`), unabhängig vom Arbeitsordner. Nützlich für getrennte Datenbestände und für Tests.

### 6.2 Konstanten im Code (`src/gbui/config.py`)

| Konstante | Wert | Bedeutung |
|---|---|---|
| `DEFAULT_MODEL` | `gemini-4-carbon` | Voreinstellung Modell |
| `DEFAULT_OPENAI_MODEL` | `gpt-6-astra` | Voreinstellung Modell bei OpenAI |
| `PROVIDER_GEMINI`, `PROVIDER_OPENAI` | „Gemini“, „OpenAI“ | Anbieter. `PROVIDER_OPENAI` steht auch im Feld `provider` der OpenAI-Einträge |
| `PROVIDERS` | beide Anbieter | Auswahl in der Seitenleiste, Voreinstellung ist der erste |
| `BASE` | Projektordner: Ordner über `src/` (liegt das Paket `gbui` in keinem Ordner namens `src`, der Ordner mit dem Paket), oder `GEMINI_UI_HOME` | Wurzel für `conf/` und `data/` (`_base_dir()`) |
| `CONF_DIR` | `BASE/conf` | Zugangsdaten, geheim |
| `DATA_DIR` | `BASE/data` | erzeugte Daten |
| `CREDENTIALS_FILE`, `TOKEN_FILE` | `conf/credentials.json`, `conf/token.json` | Drive-OAuth-Client und Drive-Login |
| `HISTORY` | `data/batch_jobs.json` | Verlauf und Ergebnisse |
| `FILES_DB` | `data/pdf_files.json` | PDF-Hash → Datei in der Gemini Files API |
| `OPENAI_FILES_DB` | `data/openai_files.json` | PDF-Hash → Datei in der OpenAI Files API |
| `SCOPES` | `drive.readonly` | Drive-Berechtigung (nur lesen) |
| `POLL_SECONDS` | `10` | Abstand der automatischen Prüfung |
| `DONE` | 4 `JOB_STATE_*`-Endzustände (Gemini) und `completed`, `failed`, `cancelled`, `expired` (OpenAI) | Wann ein Batch als abgeschlossen gilt |
| `BATCH_MAX_OUT` | `32768` | Batch-Limit für Ausgabe-Token |
| `SYNC_MAX_OUT` | `1_048_576` | Obergrenze synchron (Long Decode) |
| `BG_MAX_OUT` | `1_048_576` | Obergrenze im Hintergrund-Modus (Long Decode auf Googles Servern, minus Eingabe) |
| `BG_DEFAULT_OUT` | `262_144` | Voreinstellung im Hintergrund-Modus (Empfehlung der Google-Doku für schwere Aufgaben) |
| `BG_RUNNING` | `in_progress`, `queued` | Status einer Hintergrund-Interaktion, solange sie läuft (jeder andere gilt als Ende) |
| `SYNC_TIMEOUT_MS` | 25 Minuten | HTTP-Timeout pro synchronem Aufruf |
| `MAX_POLL_ERRORS` | `3` | So viele Abruffehler in Folge stoppen die automatische Prüfung |
| `TABS` | „Neue Anfrage“, „Jobs und Ergebnisse“ | Tab-Namen (auch Schlüsselwerte für `view`) |
| `MODE_BATCH`, `MODE_SYNC`, `MODE_BG` | Radio-Texte | Werte des Modus-Schalters |
| `THINK_LEVELS` | Standard, low, medium, high | Auswahl Denktiefe bei Gemini |
| `OPENAI_EFFORTS` | Standard, none, minimal, low, medium, high, xhigh, max | Auswahl Denktiefe bei OpenAI (die Stufen von `reasoning.effort`) |
| `OPENAI_ENDPOINT` | `/v1/responses` | Endpunkt der OpenAI Batch API (JSONL-Zeile und `batches.create`) |
| `OPENAI_MAX_OUT` | `128_000` | Obergrenze für Ausgabe-Token bei OpenAI, je Modell kann sie niedriger sein |
| `OPENAI_FILE_TTL` | 3 Tage | Ablauf hochgeladener PDFs bei OpenAI (erlaubt sind 1 Stunde bis 30 Tage) |
| `FORM_KEYS` | Schlüssel der Formularfelder | Werte, die vor jedem Lauf neu zugewiesen werden, damit sie einen Anbieterwechsel überstehen (siehe „Streamlit-Besonderheiten“) |
| `HINT` | „(bitte den Tab nicht wechseln)“ | Zusatz im Statusfeld beim Start |
| `SEPARATOR` | Muster: Zeile nur mit `---` | trennt im Formular die Prompts |
| `TALK_INTRO` | Einleitungssatz | steht über jedem Gesprächsverlauf („Unten steht der bisherige Verlauf …“) |
| `TALK_BEGIN`, `TALK_END`, `TALK_NEW` | `[Bisheriger Gesprächsverlauf]`, `[Ende des bisherigen Gesprächsverlaufs]`, `[Mein neuer Prompt]` | Marken des Verlaufstexts, je in einer eigenen Zeile |
| `TALK_MINE`, `TALK_YOURS` | `[Runde {n}: Mein Prompt]`, `[Runde {n}: Deine Antwort dazu]` | Marken je Runde |
| `TALK_MARK` | Muster | erkennt genau diese Marken wieder (`parse_conversation()`) |

Weitere Voreinstellungen stehen direkt im Hauptskript (`src/gemini_batch_ui.py`): Denktiefe `high` (`st.session_state.setdefault("think", "high")`), Ausgabe-Token synchron 65.536, im Batch 32.768 und im Hintergrund `BG_DEFAULT_OUT`, Prompts pro Gespräch 1. Bei OpenAI: Denktiefe „Standard“ (`st.session_state.setdefault("effort", "Standard")`) und Ausgabe-Token 32.768. Wiederholungen bei vorübergehenden Fehlern in `ask()` (synchron): 3 Versuche, Pause 10 s und 20 s, nur für HTTP 429, 500, 503, 504. `create_interaction()` (Hintergrund) wiederholt genauso, aber nur bei 429: Nach einem 5xx könnte die Interaktion schon angelegt sein, und ein zweiter Aufruf würde sie doppelt berechnen. Das SDK wiederholt 5xx selbst, kurz und höchstens zweimal.

### 6.3 Streamlit-Konfiguration (optional, `.streamlit/config.toml`)

```toml
[server]
fileWatcherType = "none"   # vermeidet „inotify watch limit reached“ unter Linux, Änderungen am Code laden dann nicht automatisch
maxUploadSize = 500        # MB pro hochgeladener Datei (Standard 200)
```

Streamlit liest diese Datei aus `.streamlit/` im **Arbeitsordner**, in dem `streamlit run` gestartet wird, nicht aus `conf/`. Am besten startet man die App im Projektordner (dann passt auch der Pfad `src/gemini_batch_ui.py`).

### 6.4 Google Drive einrichten (optional)

Nur nötig für „PDFs aus Google Drive hinzufügen“. Der Datei-Upload funktioniert ohne.

1. [Google Cloud Console](https://console.cloud.google.com) mit dem Konto öffnen, auf dessen Drive zugegriffen werden soll. Neues Projekt anlegen (die Drive API ist kostenlos, ein Rechnungskonto sollte dafür in der Regel nicht nötig sein).
2. **Drive API aktivieren**: `https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com`, Projekt wählen, aktivieren.
3. **Google Auth Platform** (Menü) → „Get started“: App-Name, Support-E-Mail, Zielgruppe „External“ (mit Workspace-Konto „Internal“), Kontakt-E-Mail, Richtlinie akzeptieren.
4. Nur bei „External“: unter „Audience“ die eigene Google-Adresse als **Test user** eintragen (sonst „Zugriff blockiert“).
5. Unter „Data Access“ den Scope `https://www.googleapis.com/auth/drive.readonly` hinzufügen.
6. Unter „Clients“ → „Create client“ → Anwendungstyp **Desktop-App** → JSON herunterladen und als `conf/credentials.json` ablegen (der Ordner `conf/` liegt im Projektordner neben `src/` und wird beim Start angelegt).
7. In der App „Meine PDFs aus Drive laden“. Beim ersten Zugriff öffnet sich der Browser zur Freigabe (Warnung „Google hat diese App nicht überprüft“ über „Erweitert“ bestätigen). Danach liegt `conf/token.json` vor.

Hinweise:

- Im Status „Testing“ (Zielgruppe „External“) läuft die Freigabe nach **7 Tagen** ab. Die App öffnet dann selbstständig erneut den Browser zur Anmeldung (`drive_service()` fängt `RefreshError`). Dauerhaft vermeidet das „Publish app“ unter „Audience“ (Status „In production“, Warnung bleibt) oder ein Workspace-Konto mit „Internal“.
- Ohne `conf/credentials.json` meldet die App einen klaren Fehler samt Zielordner. `conf/token.json` zu löschen erzwingt eine neue Anmeldung.
- Menünamen der Konsole können je nach Sprache und Version abweichen. Maßgeblich ist der Drive-Python-Quickstart (Abschnitt 13).

### 6.5 Abhängigkeiten

| Paket | Mindestversion | Wofür |
|---|---|---|
| `streamlit` | 1.67.0 | Oberfläche. `st.tabs` kennt mit `key` und `on_change` den aktiven Tab (`.open`), und `AppTest` unterstützt Datei-Uploads. Ältere Versionen werden nicht unterstützt. |
| `google-genai` | 3.3.0 | Gemini-SDK. `generate_content` setzt Long Decode automatisch fort. Ältere Versionen werden nicht unterstützt. |
| `openai` | 1.100.0 | OpenAI-SDK (Files API, Batch API). Ab 1.100.0 kennt `files.create` den Parameter `expires_after`, mit dem die App hochgeladene PDFs ablaufen lässt. Wird erst bei OpenAI-Nutzung importiert: Ohne das Paket läuft Gemini weiter, und die App meldet bei OpenAI, was fehlt. |
| `google-api-python-client`, `google-auth-oauthlib` | beliebig | Drive-Zugriff (werden erst bei Drive-Nutzung importiert) |
| `pytest` | 8 | nur für die Tests (`requirements-dev.txt`) |
| `pyyaml` | 6 | nur für die Tests (`requirements-dev.txt`), prüft die Workflow-Datei |

Die Mindestversionen (Python 3.12, Streamlit 1.67.0, google-genai 3.3.0) sind die Versionen, mit denen die Testsuite zuletzt vollständig lief (Abschnitt 13). Ältere Versionen werden nicht unterstützt, der Code enthält keine Ausweichzweige dafür. Beim Anheben der Mindestversionen `requirements.txt`, die Voraussetzungen in Abschnitt 3 und, bei Python, die Matrix des Workflows (Abschnitt 9.5) mitziehen. `tests/test_docs.py` und `tests/test_ci.py` melden Abweichungen.

## 7. Lokale Dateien und Datenformate

Die App legt `conf/` (Konfiguration und Zugangsdaten) und `data/` (erzeugte Daten) im **Projektordner** an, neben `src/` und unabhängig vom Arbeitsordner. Projektordner ist der Ordner über `src/`. Mit der Umgebungsvariable `GEMINI_UI_HOME` lässt sich ein anderer Ordner festlegen, unter dem beide liegen.

```text
gemini-4-batch/                     # Projektordner
├── README.md
├── requirements.txt
├── requirements-dev.txt            # zusätzlich pytest und pyyaml, nur für die Tests
├── pytest.ini                      # pytest-Einstellungen
├── src/
│   ├── gemini_batch_ui.py          # Hauptskript: nur die Oberfläche
│   └── gbui/                       # Paket mit der Logik, ein Modul je Thema (Abschnitt 8)
├── tests/                          # automatisierte Tests (Abschnitt 9.3)
├── .github/workflows/tests.yml     # GitHub Actions: führt pytest automatisch aus (Abschnitt 9.5)
├── .streamlit/config.toml          # optional, Streamlit-Einstellungen (aus dem Arbeitsordner gelesen)
├── conf/                           # Konfiguration und Zugangsdaten, GEHEIM
│   ├── credentials.json            # optional: Drive-OAuth-Client (von Hand hineinlegen)
│   └── token.json                  # automatisch: Drive-Login
└── data/                           # erzeugte Daten
    ├── batch_jobs.json             # Verlauf + Ergebnisse (+ batch_jobs.tmp beim Schreiben)
    ├── batch_jobs.defekt-<ts>.json # Sicherung, falls batch_jobs.json beschädigt war
    ├── pdf_files.json              # PDF-Hash -> Files-API-Name (Gemini)
    ├── openai_files.json           # PDF-Hash -> Datei-ID (OpenAI Files API)
    ├── requests_<unixzeit>.jsonl   # je Gemini-Batch die exakt gesendeten Anfragen
    └── requests_openai_<unixzeit>.jsonl  # dasselbe für OpenAI-Batches
```

**Lage von Hauptskript und Paket.** `README.md`, `requirements*.txt` und `pytest.ini` bleiben im Projektordner, weil Werkzeuge sie dort erwarten. Der Ordner `gbui/` muss **neben** `gemini_batch_ui.py` liegen: Streamlit nimmt den Ordner des Hauptskripts in den Suchpfad für Importe auf. Liegen beide noch direkt im Projektordner (flaches Layout, älterer Stand), läuft die App ebenfalls: `conf/` und `data/` entstehen dann im Ordner mit dem Paket und damit an derselben Stelle. Für bestehende Installationen genügt es, das Hauptskript **und** den Ordner `gbui/` nach `src/` zu verschieben. `conf/` und `data/` bleiben unverändert, nur der Startbefehl ändert sich zu `streamlit run src/gemini_batch_ui.py`.

**Übernahme älterer Stände.** Lagen `credentials.json`, `token.json`, `batch_jobs.json`, `pdf_files.json`, `requests_*.jsonl` oder `batch_jobs.defekt-*.json` noch direkt im Projektordner (statt in `conf/` bzw. `data/`), verschiebt `prepare_folders()` sie beim Start dorthin (Hinweis im Terminal und als Toast). Es wird nie etwas überschrieben: Liegt am Ziel schon eine gleichnamige Datei, bleibt die alte liegen. Durchsucht wird nur der Projektordner, nie der Arbeitsordner. Tipp: Vor dem ersten Start einer neuen Version `batch_jobs.json` kopieren, denn Ergebnisse sind nach Ablauf der Aufbewahrung bei Google nicht neu abrufbar.

Empfohlene `.gitignore`:

```text
conf/credentials.json
conf/token.json
data/
.streamlit/secrets.toml
__pycache__/
.pytest_cache/
```

### `data/batch_jobs.json`: Liste von Einträgen, neueste zuerst

Batch-Eintrag (Felder `state`, `rows`, `error`, `api_model` kommen erst, wenn der Batch einen Endzustand hat):

```json
{
  "name": "batches/9bo5q27hzqyh6unvm1z9slccnav15hnkybmy",
  "label": "batch-20261019-1437",
  "model": "gemini-4-carbon",
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
  "api_model": "models/gemini-4-carbon"
}
```

OpenAI-Eintrag (nur Batch): dieselben Felder, dazu `provider` (nur bei OpenAI vorhanden). `name` ist die Batch-ID, `state` der Status der Batch API in Kleinbuchstaben (`completed`, `failed`, `expired`, `cancelled`), `api_model` der von OpenAI gemeldete Modellname:

```json
{
  "name": "batch_<id>",
  "provider": "OpenAI",
  "label": "openai-20261110-0930",
  "model": "gpt-6-astra",
  "think": "high",
  "max_out": 32768,
  "state": "completed",
  "api_model": "gpt-6-astra-<datum>"
}
```

Die übrigen Felder (`pdf`, `pdfs`, `prompts`, `system`, `created`, `rows`, `error`) sind wie beim Batch-Eintrag oben. Fehlt `provider`, gilt der Eintrag als Gemini-Eintrag (`entry_provider()`).

Synchroner Eintrag (Zeilen wachsen live):

```json
{
  "name": "sync-20261028-143015-3f9a",
  "mode": "sync",
  "state": "RUNNING",
  "label": "sync-20261028-1430",
  "model": "gemini-4-carbon",
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

Hintergrund-Eintrag (die Felder `state`, `rows`, `error`, `api_model` kommen erst, wenn alle Interaktionen beendet sind, wie beim Batch):

```json
{
  "name": "bg-20261112-230500-3f9a",
  "mode": "background",
  "label": "background-20261112-2305",
  "model": "gemini-4-carbon",
  "pdf": "Buch.pdf",
  "pdfs": ["Buch.pdf"],
  "prompts": {"prompt-1": "…", "prompt-2": "…"},
  "system": "",
  "think": "high",
  "max_out": 262144,
  "interactions": {"prompt-1": "v1_…", "prompt-2": "v1_…"},
  "created": "2026-11-12T23:05:00",
  "state": "completed",
  "rows": [],
  "error": null,
  "api_model": "gemini-4-carbon"
}
```

`name` ist ein lokaler Name (`bg-<Datum>-<Uhrzeit>-<Zufall>`), `interactions` ordnet jedem Prompt-Schlüssel die ID seiner Interaktion bei Google zu. `state` ist `completed`, `cancelled` oder `failed` (klein geschrieben, wie bei OpenAI), solange eine Interaktion läuft, steht es nicht in der Datei. `api_model` ist das von der API gemeldete Modell. Google bewahrt die Interaktionen bei kostenpflichtigen Konten bis zu 55 Tage auf (in AI Studio auf 7, 14, 28 oder 55 Tage einstellbar), bei kostenlosen 1 Tag. Danach sind sie dort weg, das lokal gesicherte Endergebnis bleibt.

Eine Zeile in `rows` (alle Modi und Anbieter, erzeugt von `parse_line()`, bei OpenAI von `parse_openai_line()` und im Hintergrund von `parse_interaction()`, dieselbe Form):

```json
{
  "key": "prompt-1",
  "prompt": "…",
  "text": "Antworttext ohne Denk-Teile",
  "model": "gemini-4-carbon",
  "finish": "STOP",
  "usage": {"promptTokenCount": 80259, "cachedContentTokenCount": 0, "thoughtsTokenCount": 2188, "candidatesTokenCount": 1133},
  "error": null,
  "in": 80259, "cached": 0, "think": 2188, "out": 1133,
  "secs": 42
}
```

`secs` gibt es nur synchron. `error` ist ein Text (bei Gemini auch das Fehlerobjekt der API, bei OpenAI immer als Text `code: Meldung`). Bei OpenAI ist `usage` das `usage`-Objekt der Antwort, `out` die Ausgabe ohne Denk-Token, `finish` ist `STOP` (Status `completed`), `MAX_TOKENS` (`incomplete` wegen `max_output_tokens`) oder sonst der Grund bzw. Status. Im Hintergrund ist `usage` das `usage`-Objekt der Interaktion (`total_input_tokens` usw., über alle Schritte), der Text die Summe aller Schritte vom Typ `model_output`, `finish` ist `STOP` (Status `completed`), `MAX_TOKENS` (`incomplete`) oder der Status in Großbuchstaben. Übersprungene Prompts eines abgebrochenen Gesprächs haben `error` = „Übersprungen: …“.

Ältere Einträge können Felder nicht haben (`system`, `pdfs`, `think`, `group`, `state`, `rows` …). Neue Felder deshalb **immer optional** lesen (`.get()`).

### `data/pdf_files.json`

```json
{"<sha256 des PDF-Inhalts>": "files/<id der Gemini Files API>"}
```

### `data/openai_files.json`

```json
{"<sha256 des PDF-Inhalts>": "file-<id der OpenAI Files API>"}
```

Getrennt von `pdf_files.json`, weil dieselbe Datei bei jedem Anbieter eine andere Kennung hat. Vor jeder Wiederverwendung fragt die App die Datei bei OpenAI ab und lädt neu hoch, wenn sie abgelaufen oder gelöscht ist (hier nach 3 Tagen).

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

### `data/requests_openai_<unixzeit>.jsonl`

Eine Zeile pro Prompt, im Format der OpenAI Batch API (Responses-Endpunkt):

```json
{"custom_id": "prompt-1", "method": "POST", "url": "/v1/responses", "body": {
  "model": "gpt-6-astra",
  "instructions": "…",
  "input": [{"role": "user", "content": [
    {"type": "input_file", "file_id": "file-…"},
    {"type": "input_text", "text": "Prompt-Text"}]}],
  "max_output_tokens": 32768,
  "reasoning": {"effort": "high"}}}
```

`instructions` (Systemanweisung) und `reasoning` (Denktiefe) fehlen, wenn nichts gesetzt ist („Standard“). Kontrolle: `grep -o '"effort": "[a-z]*"' data/requests_openai_*.jsonl | sort | uniq -c`.

### Gesprächsverlauf im Prompt (Fortsetzung eines Gesprächs)

„Gespräch fortsetzen“ speichert nichts Neues in `batch_jobs.json`. Der Verlauf ist reiner Text im Prompt des neuen Eintrags und sieht so aus (`conversation_text()`):

```text
Unten steht der bisherige Verlauf unseres Gesprächs: meine früheren Prompts und deine Antworten dazu. …

[Bisheriger Gesprächsverlauf]

[Runde 1: Mein Prompt]
<früherer Prompt>

[Runde 1: Deine Antwort dazu]
<Antwort des Modells>

[Runde 2: Mein Prompt]
…

[Ende des bisherigen Gesprächsverlaufs]

[Mein neuer Prompt]
<der neue Prompt, vom Benutzer geschrieben>
```

- Jede Marke steht allein in ihrer Zeile. `parse_conversation()` liest den Text wieder ein. Ist der Aufbau durcheinander (z. B. von Hand bearbeitet), gilt der Prompt als gewöhnlicher Prompt und wird beim nächsten Fortsetzen nicht abgeflacht. Fehlt nur die Marke `[Mein neuer Prompt]`, zählt der Rest nach dem Ende des Verlaufs als neuer Prompt.
- Frühere Prompts und Antworten gehen durch `talk_safe()`: Eine Zeile nur mit `---` würde im Formular die Prompts trennen und wird zu `***` (in Markdown dieselbe Trennlinie). Zeilen, die wie eine Marke aussehen, bekommen einen Rückstrich. Das ist wiederholbar, ohne dass sich etwas ändert.
- Beim Fortsetzen holt `history_turns()` die Runden aus dem Eintrag. Beginnt der erste Prompt selbst mit einem Verlauf, wird dieser davor eingereiht, statt zu verschachteln.

## 8. Architektur

Bewusst ohne Klassen und ohne Datenbank: Die Logik steht als Funktionen im Paket `gbui`, die Oberfläche im Hauptskript `src/gemini_batch_ui.py`. Streamlit führt das **Hauptskript** bei **jeder** Interaktion von oben nach unten neu aus, die Module von `gbui` dagegen nur beim ersten Import. Alles, was länger leben muss, steckt deshalb in Dateien, in `st.session_state` oder in `runtime()`.

### Landkarte (Dateien von oben nach unten)

Ein Modul von `gbui` importiert nur Module, die in dieser Tabelle über ihm stehen. So gibt es keine Importzyklen (ein Test prüft es, die Liste steht auch in `src/gbui/__init__.py`).

| Datei | Inhalt |
|---|---|
| `src/gbui/config.py` | Konstanten (siehe 6.2), `_base_dir()` (Wurzel), `prepare_folders()` (legt `conf/` und `data/` an, übernimmt Altbestand) |
| `src/gbui/history.py` | `runtime()` (prozessweit: `lock`, `threads`, `cancel`), `load_jobs()`, `mutate_jobs(lock, change)` |
| `src/gbui/rows.py` | die gemeinsame Zeilenform: `pick()`, `parse_line()`, `parse_results()` (mit wählbarem Zeilenleser), für den Hintergrund-Modus `as_dict()` und `parse_interaction()` |
| `src/gbui/drive.py` | `drive_service()` (OAuth, Neuanmeldung bei `RefreshError`), `drive_id()` (ID aus Link), `drive_fetch()` (PDF laden/exportieren) |
| `src/gbui/gemini.py` | `upload_pdf()` (Files API + `data/pdf_files.json`), `start_batch()` (JSONL bauen, hochladen, `batches.create`) |
| `src/gbui/background.py` | Hintergrund-Modus (Interactions API, Long Decode): `interaction_request()` (Anfrage), `create_interaction()` (mit Wiederholung), `start_background()` (alle Prompts starten, Eintrag speichern), `fetch_background()` (Stand, Zeilen, Endzustand), `cancel_background()` |
| `src/gbui/openai_batch.py` | `openai_client()` (lädt das Paket erst hier), `upload_pdf_openai()` (Files API + `data/openai_files.json`), `openai_request()` (Anfragerumpf), `start_openai_batch()` (JSONL bauen, hochladen, `batches.create`), `openai_error_text()`, `parse_openai_line()`, `fetch_openai_batch()` (Status, Ausgabe- und Fehlerdatei) |
| `src/gbui/providers.py` | `make_client()`, `entry_provider()`, `entry_mode()` (`sync`, `background` oder `batch`), `refresh()` (Status + Ergebnisse laden, Endergebnis sichern, bei OpenAI-Einträgen über `fetch_openai_batch()`, bei Hintergrund-Einträgen über `fetch_background()`), `batch_info()`, `cancel_batch()` (je Anbieter und Modus) |
| `src/gbui/sync.py` | synchroner Modus: `ask()` (Retry), `run_sync()` (Thread-Funktion), `start_sync()`, `sync_view()` |
| `src/gbui/talk.py` | Gesprächsverlauf als Text: `entry_group()` (Prompts pro Gespräch eines Eintrags), `talk_safe()`, `conversation_text()`, `parse_conversation()`, `history_turns()`, `short_prompt()` |
| `src/gbui/forms.py` | Verlauf und Formular: `entry_label()`, `fill_form()` (Hilfe), `reuse_inputs()` und `continue_conversation()` (Callbacks), `delete_entry()` (Callback) |
| `src/gbui/views.py` | Anzeige: `show_text()`, `show_prompt()`, `request_details()`, `talk_tools()`, `show_rows()`, `sync_panel()`, `job_panel()` (läuft als Fragment) |
| `src/gemini_batch_ui.py` | Hauptskript, nur Oberfläche: Seitenleiste (Anbieter, Keys, Modell), `client_for()` (Client je Anbieter, `None` ohne Key), Tabs, `with tab_new:` (Formular + Start-Handler), `with tab_jobs:` (Auswahl, Buttons, Fragment mit dem Client des Eintragsanbieters) |

Die Grenze zwischen Logik und Oberfläche ist die Paketgrenze: Das Hauptskript importiert aus `gbui`, `gbui` importiert es nie (ein Test prüft es). Gezeichnet wird nur in `views.py`. Die übrigen Module sind ohne Oberfläche testbar (siehe 9.3): `forms.py` und `providers.py` nutzen von Streamlit nur `st.session_state`, `history.py` nur `@st.cache_resource`. `views.py` läuft in den Tests über `AppTest`. Die Fixture `app` in `tests/conftest.py` lädt alle Module von `gbui` als ein Objekt.

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

### Datenfluss Batch (OpenAI)

```text
Formular ──► Dateien sammeln (Upload, Drive) ──► Duplikate per SHA-256 entfernen
         ──► upload_pdf_openai() je PDF: Files API (Zweck user_data, Ablauf nach 3 Tagen,
             wiederverwendet über data/openai_files.json)
         ──► start_openai_batch(): JSONL (custom_id + Anfrage je Prompt) in data/requests_openai_<ts>.jsonl,
             JSONL per Files API hochladen (Zweck batch), client.batches.create(input_file_id, endpoint, completion_window)
         ──► Verlaufseintrag (provider OpenAI, ohne state) in data/batch_jobs.json
Jobs-Tab ──► Fragment alle 10 s: refresh() → fetch_openai_batch() → client.batches.retrieve(id)
         ──► bei completed, expired, cancelled oder failed: output_file_id und error_file_id per files.content laden
             → parse_results(…, parse_openai_line) → rows
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

### Datenfluss Hintergrund

```text
Formular ──► Dateien sammeln, upload_pdf() je PDF (wie beim Batch)
         ──► start_background(): je Prompt client.interactions.create(model, input=[PDFs als document + uri, Prompt],
             generation_config={max_output_tokens, thinking_level}, background=True, store=True)
             → Interaktions-ID. Scheitert einer, werden die schon gestarteten abgebrochen.
         ──► Verlaufseintrag (mode background, interactions {Schlüssel: ID}, ohne state) in data/batch_jobs.json
Jobs-Tab ──► Fragment alle 10 s: refresh() → fetch_background() → client.interactions.get(id) je Prompt
             (Zeilen aus der letzten Abfrage bleiben stehen, beendete Interaktionen werden nicht erneut geladen)
         ──► beendet (alles außer in_progress/queued): parse_interaction() → Zeile. Text = alle model_output-Schritte
         ──► alle beendet: Endzustand (state, rows, error, api_model) in den Eintrag schreiben, danach kein Abruf mehr
Abbruch  ──► cancel_batch() → cancel_background() → client.interactions.cancel(id) je Prompt
```

### Streamlit-Besonderheiten, die man kennen muss

- **Tab-Erkennung**: `st.tabs(TABS, key="view", on_change="rerun")`. `tab_jobs.open` sagt, ob der Jobs-Tab offen ist. Der Inhalt versteckter Tabs läuft trotzdem, gesteuert wird über `.open`. Ein Tab-Wechsel löst einen **vollständigen Rerun** aus. Das unterbricht jedes Skript, das gerade lange läuft (Uploads im Start-Handler). Lange Arbeit gehört deshalb in einen Hintergrund-Thread (siehe `start_sync`).
- **Polling per Fragment**: `st.fragment(run_every=POLL_SECONDS if polling else None)(job_panel)(...)`. Die Entscheidung `polling` wird bei jedem vollen Lauf neu getroffen (Tab offen und Eintrag läuft). Um einen laufenden Timer zu beenden, ruft das Fragment `st.rerun()` auf (voller Rerun). Die Argumente eines Timer-Reruns sind **veraltet** (Stand des letzten vollen Laufs). Ein Fragment muss veränderliche Daten deshalb selbst neu laden (`sync_panel` liest den Eintrag aus der Datei, `job_panel` nutzt `batch_info()` und die Session).
- **Button im Fragment, der das Formular füllt**: Ein Klick im Fragment (die Ergebnisse laufen in `job_panel`) löst nur einen Fragment-Lauf aus. „Gespräch fortsetzen“ füllt das Formular deshalb in einem **Callback** (`continue_conversation`: Widget-Werte lassen sich nur setzen, bevor das Widget im Lauf erzeugt wurde) und ruft danach `st.rerun()` auf, damit ein voller Lauf das Formular zeigt und den Tab wechselt. Im `AppTest` ist schon der erste Lauf voll. Dort verbraucht er den einmaligen Hinweis `reused_note`, im Browser erscheint er nach dem Rerun.
- **Felder, die einen Lauf lang fehlen**: Streamlit vergisst den Wert eines Widgets, das in einem Lauf nicht gezeichnet wird (Felder des anderen Anbieters, alles hinter `st.stop()` bei fehlendem API-Key). Das Backend behält dabei teils noch Werte, der Browser baut das Feld aber mit dem Standardwert neu auf: Das Modellfeld war leer, „Max. Ausgabe-Token“ stand auf 256, die Denktiefe auf „Standard“, Prompts und Systemanweisung waren weg. Darum weist das Hauptskript jeden Wert aus `FORM_KEYS` vor dem Zeichnen neu zu (`st.session_state[key] = st.session_state[key]`). Das hält ihn am Leben und schickt ihn mit `set_value` an den Browser. `AppTest` sieht den Fehler nicht, weil es nur den Wert im Backend zeigt. Die Tests in `test_openai.py` prüfen deshalb, was die App dem Browser schickt (`proto.set_value`, Hilfsfunktion `sent_to_browser`). Namensfelder (`label_*`), die Auswahl im Jobs-Tab (`sel`), der Anbieterschalter und der Tab-Zustand (`view`) brauchen das nicht, ein Test hält die Ausnahmen fest.
- **Threads**: `run_sync` läuft ohne `ScriptRunContext`. Dort **keine** `st.*`-Aufrufe und keine Streamlit-Cache-Funktionen. `rt = runtime()` wird im Hauptthread geholt und als Argument übergeben. Schreibzugriffe auf den Verlauf nur über `mutate_jobs(rt.lock, …)` (liest unter Sperre, schreibt atomar über `batch_jobs.tmp` + `os.replace`).
- **Abbruch**: `rt.cancel` ist eine Menge von Eintragsnamen. `run_sync` prüft sie vor jedem Prompt. Eine laufende Anfrage wird nicht abgebrochen.
- **Module und Hauptskript**: Streamlit führt das Hauptskript bei jedem Lauf neu aus, die Module von `gbui` dagegen nur beim ersten Import (danach kommen sie aus dem Importspeicher von Python). Darum steht auf Modulebene kein Streamlit-Aufruf außer Dekoratoren wie `@st.cache_resource` (ein Test prüft es), und `st.set_page_config` bleibt im Hauptskript, weil es der erste Streamlit-Aufruf sein muss. Konstanten wie `DATA_DIR` werden einmal je Prozess berechnet. Ändert man beim Entwickeln eine Datei von `gbui`, wirft Streamlit alle beobachteten Module aus dem Importspeicher, und der nächste Lauf importiert sie neu (nur mit Datei-Beobachter, siehe 6.3).
- **Importpfad**: Beim Start mit `streamlit run` nimmt Streamlit den Ordner des Hauptskripts in `sys.path` auf, deshalb findet `import gbui` den Ordner daneben. `AppTest` tut das erst in neueren Versionen (in 1.56.0 noch nicht), darum trägt `pytest.ini` `src` mit `pythonpath = src` ein. Ein Test, der eine Kopie des Hauptskripts an einen anderen Ort stellt, ergänzt den Pfad selbst.

### Session-State- und Widget-Schlüssel

| Schlüssel | Zweck |
|---|---|
| `view` | aktiver Tab. `reuse_inputs` und `continue_conversation` setzen ihn auf „Neue Anfrage“ |
| `provider` | Anbieter-Radio der Seitenleiste (`PROVIDERS`). `fill_form()` setzt es, wenn ein Eintrag übernommen wird |
| `model_gemini`, `model_openai` | Modellfeld je Anbieter (`fill_form()` setzt es beim Übernehmen) |
| `mode` | Modus-Radio (`MODE_BATCH` / `MODE_SYNC` / `MODE_BG`). Mit OpenAI wird es nicht angezeigt |
| `system`, `raw` | Systemanweisung, Prompts-Textfeld |
| `think`, `effort` | Denktiefe bei Gemini bzw. OpenAI (Voreinstellung per `setdefault`) |
| `max_batch`, `max_sync`, `max_bg`, `max_openai` | Ausgabe-Token je Modus bei Gemini bzw. bei OpenAI (Voreinstellung per `setdefault`) |
| `group` | Prompts pro Gespräch |
| `label_batch`, `label_sync`, `label_background`, `label_openai` | Namensfeld je Modus bzw. für OpenAI |
| `sel` | Auswahl im Jobs-Tab (Wert = `entry_label(eintrag)`). Der Start-Handler setzt ihn auf den neuen Eintrag |
| `drive_pdfs` | `{Anzeigename: Drive-ID}` der geladenen Drive-Liste |
| `reused_note` | einmaliger Hinweis nach „Eingaben übernehmen“ und „Gespräch fortsetzen“ (wird beim Anzeigen entfernt) |
| `res:<name>` | Ergebnisstand eines Batches oder Hintergrund-Eintrags in der Sitzung (`state`, `rows`, `error`, `model`) |
| `err:<name>`, `errn:<name>` | letzte Abruffehlermeldung, Zähler aufeinanderfolgender Fehler |
| `detail:<name>` | Schalter „Gesendete Anfrage im Detail“ je Eintrag |
| `prompt:<name>`, `hist:<name>` | Schalter „Prompt im Wortlaut“ und „Verlaufstext zeigen“ je Ergebnis (nach dem Eintragsnamen folgt `:` und der Prompt-Schlüssel, z. B. `prompt-1`) |
| Button-Schlüssel | `chk:`, `cx:` (Batch bzw. Hintergrund-Läufe prüfen/abbrechen), `stop:` (Lauf abbrechen), `dl:` (Download), `reuse:`, `del:`, `talk:` (Gespräch fortsetzen) jeweils plus Eintragsname (bei `talk:` danach `:` und der Prompt-Schlüssel) |

## 9. Weiterentwickeln

### 9.1 Spielregeln

1. **Erst diese Datei lesen**, dann das Hauptskript `src/gemini_batch_ui.py` und das Modul in `src/gbui/`, das zum Thema passt (Landkarte in Abschnitt 8). Kleine, gezielte Änderungen statt Umbauten. Logik gehört in ein Modul von `gbui`, das Hauptskript bleibt die Oberfläche. Ein neues Thema bekommt ein neues Modul, eingetragen in der Landkarte und in der Liste in `src/gbui/__init__.py`. Ein Modul importiert nur Module, die in dieser Liste über ihm stehen. Wächst eine Datei deutlich über etwa 1.000 Zeilen, wird sie nach Thema geteilt (Importe werden gecacht, nur das Hauptskript läuft bei jedem Rerun neu).
2. **Prefix stabil halten** (Abschnitt 5). Alles, was vor oder zwischen den PDFs steht, beeinflusst die Cache-Trefferquote.
3. **Verlauf nur über `mutate_jobs`** schreiben, nie `data/batch_jobs.json` direkt. Lesen mit `load_jobs()`. Neue Eintragsfelder optional (`.get()`), weil alte Einträge sie nicht haben.
4. **Zeilenform nur in `parse_line()`, `parse_openai_line()` und `parse_interaction()` ändern** und dort neue Felder ergänzen (alle drei liefern dieselbe Form). Anzeige in `show_rows()`. Alle Modi und Anbieter nutzen dieselbe Zeilenform, und gespeicherte Zeilen alter Läufe haben neue Felder nicht (`.get()`).
5. **Lange Arbeit in einen Thread** mit Verlaufseintrag und Polling-Fragment legen (Muster: `start_sync` → `run_sync` → `sync_panel`). Alles im Hauptskript kann durch einen Tab-Wechsel unterbrochen werden.
6. **Widgets, die per Code gesetzt werden sollen, brauchen einen `key`.** Voreinstellung über `st.session_state.setdefault(key, wert)` setzen und dem Widget kein `value=`/`index=` mitgeben (sonst Streamlit-Warnung). Widget-Zustand nur **vor** dem Erzeugen des Widgets im selben Lauf ändern (in Callbacks oder weiter oben im Skript), danach gibt es eine Ausnahme. Ein neues Formularfeld mit Schlüssel gehört außerdem in `FORM_KEYS`, sonst vergisst Streamlit seinen Wert, sobald es einen Lauf lang fehlt. Ein Test erinnert daran.
7. **Keine verschachtelten Expander** (Streamlit verbietet es). Popover in Spalten ist erlaubt.
8. **Fragmente**: Argumente sind in Timer-Läufen veraltet, Timer mit `st.rerun()` beenden, Zustände für Abbruchbedingungen aus Datei oder Session lesen.
9. **Fehler verständlich melden** (deutsch, mit Hinweis, was zu tun ist). Geheimnisse (API-Key, Tokens) nie loggen oder speichern.
10. **Modellverhalten nicht raten.** Bei neuen Parametern die aktuelle Google-Doku prüfen und im Zweifel die rohen `usage`-Daten und Fehlermeldungen der API ansehen. Carbon und Argon haben Eigenheiten (Abschnitt 5).
11. **Stil**: deutsche Oberflächentexte und Kommentare, 4 Leerzeichen, Zeilen bis etwa 120 Zeichen, flache Funktionen, Docstrings kurz.

### 9.2 Rezepte

**Neuen Generierungsparameter hinzufügen (z. B. Temperatur)**

1. Widget mit `key` neben „Denktiefe“ ergänzen (Voreinstellung per `setdefault`, Schlüssel in `FORM_KEYS` aufnehmen).
2. In `start_batch()` in das Dict `gen` aufnehmen, in `run_sync()` in `types.GenerateContentConfig(...)`, im Hintergrund-Modus in `interaction_request()` (`generation_config`), bei OpenAI in `openai_request()`.
3. Wert im Eintrag speichern (`start_sync()` bzw. das Batch-Eintrags-Dict im Start-Handler).
4. `reuse_inputs()` und die Beschriftung im Jobs-Tab erweitern.
5. Testen (9.3). In der Carbon-Doku prüfen, ob der Parameter noch wirkt.

**Neuen Modus hinzufügen (z. B. ein weiterer Rabatt-Tarif)**

1. Konstante `MODE_…` und Eintrag im Modus-Radio.
2. Startfunktion nach dem Muster `start_sync()`. Eintrag mit `"mode": "<neu>"`.
3. `job_panel()` verteilt nach `j.get("mode")` (`entry_mode()` in `providers.py` nennt die Betriebsart). Neues Panel ergänzen. Entscheidung `running` im `with tab_jobs:`-Block ergänzen. Ein Modus, der wie ein Batch abgefragt wird, braucht kein neues Panel: Muster dafür ist der Hintergrund-Modus (Zweige in `refresh()` und `cancel_batch()`, eigene Beschriftung in `job_panel()`).
4. `reuse_inputs()` für den neuen Modus erweitern.

**Weiteren Anbieter ergänzen** (Muster: OpenAI)

1. Konstante `PROVIDER_…` und Eintrag in `PROVIDERS` (`config.py`), der Client in `make_client()` (`providers.py`).
2. Ein neues Modul nach dem Muster von `openai_batch.py` (Upload, Start, Zeilen lesen, Status holen), eingetragen in der Landkarte und in `src/gbui/__init__.py`. Der Zeilenleser muss die Form von `parse_line()` liefern, dann laufen Anzeige, Download und „Gespräch fortsetzen“ unverändert.
3. Eintrag mit Feld `provider` speichern. `entry_provider()`, `refresh()`, `cancel_batch()` (alle in `providers.py`), `fill_form()` (`forms.py`) und die Formularzweige im Hauptskript erweitern, die Endzustände des Anbieters in `DONE` aufnehmen.
4. Einen Fake mit echten SDK-Objekten nach dem Muster von `FakeOpenAI` bauen und Tests schreiben (9.3).

**Aufbau des Verlaufstexts ändern** (Einleitung, Marken, Beschriftung): `TALK_*` anpassen. `TALK_MARK` muss zu den Marken passen, `test_every_mark_constant_is_recognized_by_the_mark_pattern` meldet Abweichungen. Bereits gesendete Prompts bleiben nur dann „abflachbar“, wenn ihre Marken noch erkannt werden. Sonst gelten sie als gewöhnliche Prompts.

**Neues Ergebnisfeld anzeigen**: Feld in `parse_line()` berechnen, in `show_rows()` ausgeben (mit `.get()`), bei Bedarf in den Markdown-Download aufnehmen.

**Explizites Caching für andere Modelle**: Für Carbon und Argon nicht möglich. Für Modelle, die es unterstützen, wäre der Weg: Cache mit den PDFs anlegen (`client.caches.create(...)` mit Laufzeit), im Request nur noch `cached_content` referenzieren und die PDFs weglassen, Speicherkosten und Mindestgrößen beachten. Als Umschalter nur anzeigen, wenn das Modell es kann. Garantiert die Ersparnis, kostet aber Speicherzeit.

**Weitere Ideen** stehen in Abschnitt 11.

### 9.3 Automatisierte Tests

Die Testsuite liegt in `tests/` und braucht weder Internet noch API-Key. Alle Google-Dienste sind durch Ersatzobjekte ersetzt (`tests/fakes.py`), und jeder Test läuft in einem Wegwerf-Ordner (`GEMINI_UI_HOME`).

**Ausführen**

```bash
pip install -r requirements-dev.txt        # installiert zusätzlich pytest
pytest                                     # alle Tests (rund 280, etwa 30 Sekunden)
pytest -m "not ui"                         # nur die schnellen Tests ohne Oberfläche (unter 2 Sekunden)
pytest tests/test_sync_worker.py -k group  # einzelne Datei oder Namensausschnitt
```

**Aufbau**

| Datei | Inhalt |
|---|---|
| `pytest.ini` | Einstellungen, Marker `ui` für die langsameren Oberflächentests, `pythonpath = src` (damit `import gbui` in den Tests klappt) |
| `tests/conftest.py` | Fixtures: `home` (Wegwerf-Ordner als `GEMINI_UI_HOME`, Dummy-Keys für beide Anbieter), `fresh_package` (importiert die Module von `gbui` in jedem Test neu, weil sie ihre Ordner beim Import aus `GEMINI_UI_HOME` berechnen), `app` (alle Module von `gbui` als ein Objekt), `rt` (Ersatz für `runtime()`), `fake_time` (keine echten Pausen, in allen Modulen). Dazu eine Sperre gegen echte API-Zugriffe und ein Sicherheitsnetz: ändert ein Test `conf/` oder `data/` im Projektordner, schlägt der Lauf am Ende fehl |
| `tests/fakes.py` | `FakeGemini` (Files API, Batch-API, `generate_content`, Interactions API mit `create`, `get` und `cancel`, zeichnet Aufrufe auf), `make_response`, `make_interaction` (echte SDK-Interaktion), `ApiError`, `InteractionsError`, `FakeDrive`, `install`, `install_drive`. Für OpenAI: `FakeOpenAI` (Files API und Batch API, liefert echte SDK-Objekte `Batch` und `FileObject`), `make_openai_body` (Antwortrumpf wie in der Ausgabedatei), `OpenAIError`, `install_openai` |
| `tests/helpers.py` | `wait_until`, `wait_for_run_end`, Verlauf lesen und schreiben, Eintrags-Vorlagen (`batch_entry`, `sync_entry`, `background_entry`, `finished_batch`, `finished_background`, `openai_entry`, `finished_openai_batch`, `row`), AppTest-Kürzel (`new_app`, `new_openai_app`, `click`, `set_prompts`, `captions`, `markdown`) |
| `tests/test_logic.py` | reine Logik: Parser, Hilfsfunktionen, Ordner und Altbestand, Verlaufsdatei, PDF-Upload, Batch-Anfragen, Wiederholungen, Gesprächsverlauf als Text (Aufbau, Wiedereinlesen, mehrfaches Fortsetzen) |
| `tests/test_sync_worker.py` | synchroner Modus: Anfrageaufbau, Gespräche, Fehlerfälle, Abbruch, Zustände |
| `tests/test_background.py` | Hintergrund-Modus: Anfrageaufbau (auch gegen das Schema des SDK), Start mit Wiederholung und Aufräumen bei Fehlern, Abfrage (laufend, beendet, `incomplete`, `failed`, `cancelled`, Long-Decode-Schritte), Abbruch, Zeilenparser, Oberfläche (Formular, Start, Jobs-Tab, Übernehmen, Hinweis bei abgeschnittenem Batch; Oberflächentests mit Marker `ui`) |
| `tests/test_drive.py` | Drive-Anmeldung (Token, Neuanmeldung, Netzwerkfehler) und Laden von Dateien |
| `tests/test_ui.py` | Oberfläche mit AppTest: Formular, Starts, Jobs-Tab, automatische Prüfung, Übernehmen, Löschen, Altbestand, Ordnerlage, Prompt-Anzeige, Gespräch fortsetzen (Marker `ui`) |
| `tests/test_openai.py` | OpenAI-Anbindung: Anfrageaufbau, PDF-Upload, Ergebniszeilen, Status und Ergebnisdateien, Anbieter-Schalter, Formularwerte beim Anbieterwechsel (was die App dem Browser schickt), Start, Jobs-Tab, Übernehmen, Fortsetzen. Ein Test prüft, dass die Beispielantworten des Fakes gültige Objekte des SDK sind (Oberflächentests mit Marker `ui`) |
| `tests/test_modules.py` | Aufbau des Pakets `gbui`: jedes Modul lässt sich einzeln importieren (keine Importzyklen), keines importiert das Hauptskript, auf Modulebene steht kein Streamlit-Aufruf |
| `tests/test_docs.py` | gleicht README und Code ab: genannte Funktionen, Konstanten, Schlüssel, Versionen, Testdateien, Module von `gbui` |
| `tests/test_ci.py` | prüft die GitHub-Actions-Datei: Auslöser, Befehle, Python-Versionen laut README, Rechte |

**Wie getestet wird**

- Die Module von `gbui` werden in jedem Test frisch importiert (Fixture `fresh_package`) und als `app` bereitgestellt (`app.parse_line(...)`: der Name wird in allen Modulen gesucht, `app.modules["history"]` liefert ein Modul selbst). Streamlit-Aufrufe darin laufen im „bare mode“, das ist gewollt.
- Die Oberfläche läuft in Streamlits `AppTest` (`new_app()`): Widgets per `key` ansprechen, Tabs über `at.session_state["view"]` wechseln, Uploads mit `at.file_uploader[0].set_value([(name, bytes, mime)])`.
- Dienste ersetzt man mit `FakeGemini(...)` (Logiktests) bzw. `use_fake(...)` (Oberflächentests). Das Argument `script` bestimmt die Antworten von `generate_content` der Reihe nach: `"ok"`, `"empty"`, ein `dict` für `make_response`, oder eine Exception, die ausgelöst wird. Für den Hintergrund-Modus bestimmt `interaction_results` je Interaktion, wie `interactions.get` antwortet (Argumente für `make_interaction` oder eine Exception), `interaction_create_errors` lässt einzelne `create`-Aufrufe scheitern.
- OpenAI ersetzt `FakeOpenAI(...)` (in `test_openai.py` über `use_openai(...)`). `batch_status`, `failing`, `bodies` und `batch_errors` bestimmen, was die Batch API meldet und was in Ausgabe- und Fehlerdatei steht. Oberflächentests für OpenAI starten mit `new_openai_app()`: Der Anbieter ist dann schon gewählt, es wird nie ein Gemini-Client gebaut.
- Drive ersetzt `install_drive(...)`: Anmeldung und Dienst sind Fakes, `token.json` liegt dann in `conf/` des Wegwerf-Ordners.

**Ein neuer Test**

```python
from fakes import FakeGemini, install
from helpers import MODE_SYNC, click, new_app, set_prompts, wait_for_run_end


def test_beispiel(monkeypatch, home):
    install(monkeypatch, FakeGemini([{"text": "Hallo", "cached": 500}]))
    at = new_app()
    at.run()
    at.radio(key="mode").set_value(MODE_SYNC)
    at.run()
    set_prompts(at, "Frage")
    click(at, "Synchron starten")
    assert wait_for_run_end(home)["rows"][0]["cached"] == 500
```

**Regeln für Tests**

1. Keine echten Dienste und kein Netzwerk. Wer den Fake-Client vergisst, bekommt sofort einen Fehler (automatische Fixture).
2. Nie im Projektordner schreiben, immer die Fixture `home` nutzen.
3. Pausen über `fake_time` überspringen, Hintergrund-Threads mit `wait_until` oder `wait_for_run_end` abwarten, nie mit festem `sleep`.
4. Neue Funktion: Test in `test_logic.py` oder `test_sync_worker.py`. Neue Oberflächenfunktion: ein `ui`-Test. Neue Datei, Konstante, Schlüssel oder Funktion, die die README nennt: README anpassen, sonst schlägt `test_docs.py` an.
5. Ältere Einträge ohne neue Felder müssen offen bleiben. `test_entries_from_older_versions_still_open` ist das Muster dafür.

**Grenze der Tests.** Sie zeigen, dass die App mit Antworten in der Form des echten SDK richtig umgeht. Sie beweisen nicht, dass Google so antwortet wie `FakeGemini` und OpenAI wie `FakeOpenAI` (Caching, Limits, Fehlertexte). Das bleibt beim ersten echten Einsatz zu beobachten (Abschnitt 13).

### 9.4 Checkliste vor der Übergabe

- [ ] `pytest` läuft durch (bei reinen Logikänderungen genügt vorab `pytest -m "not ui"`).
- [ ] Neue Funktionen, Anzeigen und Abläufe haben einen Test (9.3), Fehlerfälle und ältere Einträge ohne neue Felder sind abgedeckt.
- [ ] Neue Voreinstellungen, Schlüssel, Funktionen und Dateien stehen in dieser README (Abschnitte 6, 7, 8). `tests/test_docs.py` prüft die wichtigsten Verweise.
- [ ] Es wird nicht behauptet, etwas sei mit der echten API geprüft, wenn es nur simuliert lief (Abschnitt 13).
- [ ] Liegt das Projekt auf GitHub: Der Lauf „Tests“ im Tab „Actions“ ist grün (Abschnitt 9.5).

### 9.5 Tests automatisch auf GitHub (GitHub Actions)

Die Datei `.github/workflows/tests.yml` führt die Testsuite auf GitHub aus, ohne dass man etwas tun muss.

**Wann.** Bei jedem Push, bei jedem Pull Request, auf Knopfdruck (Tab „Actions“ → „Tests“ → „Run workflow“) und einmal pro Woche (montags, 05:17 UTC). Der Wochenlauf ist gewollt: Die Abhängigkeiten stehen als `>=` in `requirements.txt`. Der Wochenlauf meldet, wenn eine neue Version von Streamlit oder google-genai etwas bricht.

**Was.** Für jede Python-Version der Matrix (derzeit nur 3.12, die Mindestversion und zugleich die genutzte Version) installiert der Lauf `requirements-dev.txt` und führt `python -m pytest` aus, also dasselbe wie lokal. Es braucht weder API-Key noch Secrets, denn alle Google-Dienste sind in den Tests ersetzt.

**Einrichten.** Der Projektordner muss das Hauptverzeichnis des Repositorys sein, denn dort liegt `.github/`. Repository anlegen, pushen, danach erscheint im Tab „Actions“ der Lauf „Tests“. Ein Badge für die README:

```markdown
![Tests](https://github.com/<benutzer>/<repository>/actions/workflows/tests.yml/badge.svg)
```

**Bewusste Entscheidungen**

- Nur Leserechte (`permissions: contents: read`), keine Secrets, `persist-credentials: false` beim Auschecken.
- Ein neuer Push bricht den noch laufenden Lauf desselben Branches ab (`concurrency`).
- `fail-fast: false`: Enthält die Matrix mehrere Python-Versionen, läuft jede zu Ende, damit man alle Ergebnisse sieht (derzeit steht nur 3.12 darin).
- Die Actions sind an Hauptversionen gebunden (`actions/checkout@v7`, `actions/setup-python@v6`, Stand November 2026). Neue Hauptversionen zieht man von Hand oder per Dependabot nach.

**Pflegen.** Ändert sich die Mindest- oder die genutzte Python-Version in Abschnitt 3, muss die Matrix im Workflow mitziehen, sonst schlägt `tests/test_ci.py` an. Neue Test-Abhängigkeiten gehören in `requirements-dev.txt`, nicht nur in den Workflow.

**Rote Läufe lesen**

- Rot, besonders im Wochenlauf: Das Log im Schritt „Tests ausführen“ lesen. Meist hat eine neue Paketversion etwas gebrochen. Lokal mit `pip install -U streamlit google-genai` bestätigen, dann den Code anpassen oder in `requirements.txt` eine Obergrenze setzen.
- Der Lauf startet gar nicht: meist ein YAML-Fehler. Die Meldung steht im Tab „Actions“. Lokal prüft `tests/test_ci.py` die Datei mit PyYAML.

## 10. Fehlersuche

| Meldung / Symptom | Ursache | Lösung |
|---|---|---|
| `400 INVALID_ARGUMENT … max_output_tokens cannot exceed 32768` | Batch unterstützt kein Long Decode | Im Batch höchstens 32.768 Token. Mehr im Modus „Hintergrund“ oder synchron. |
| Hintergrund: „Start fehlgeschlagen“ mit `400` | Die Interactions API hat die Anfrage abgelehnt. Laut Doku liefern `max_output_tokens` über 65.536 ohne `background=true` und `background=true` ohne `store=true` einen 400-Fehler, beides sendet die App richtig. Möglich bleiben ein zu großes `max_output_tokens` bei großer Eingabe (Doku: höchstens 1.048.576 minus Eingabe) oder ein Modell ohne Unterstützung für den Hintergrund-Modus | Meldung lesen, Limit verkleinern, Modellnamen prüfen. Schon gestartete Prompts desselben Laufs bricht die App ab. |
| Hintergrund: „Abruf fehlgeschlagen … 403“ | Zugriff verweigert, vermutlich gehört der API-Key zu einem anderen Projekt als der, mit dem der Lauf gestartet wurde (ungeprüft) | Den Key des Startprojekts eintragen (Seitenleiste). |
| Hintergrund: Ergebnis „Die Interaktion gibt es bei Google nicht mehr“ | Google antwortet mit 404: Die Aufbewahrung ist abgelaufen oder die Interaktion wurde gelöscht, und das Ergebnis wurde lokal nie gesichert, weil noch nicht alle Prompts beendet waren | Nichts zu retten. Mit „Eingaben übernehmen“ neu starten. Beendete Läufe sichert die App lokal, sobald alle Prompts fertig sind. |
| Hintergrund: Status `incomplete` oder Warnung `MAX_TOKENS` | Denk- plus Ausgabe-Token haben das Limit erreicht | Limit erhöhen (bis 1.048.576 minus Eingabe) oder Denktiefe senken. |
| Hintergrund: „Eingabe“ viel größer als das PDF | Bei Long Decode zählt `total_input_tokens` die Eingabe jedes 64K-Schritts erneut mit | Normal, der größte Teil davon ist Cache (95 % günstiger). Kontextgröße = PDF plus Denk- plus Ausgabe-Token. |
| `OSError: inotify watch limit reached` im Terminal | Linux-Limit für Dateibeobachtung erschöpft (oft durch IDE oder Sync-Programm). Die App läuft trotzdem, nur das automatische Neuladen bei Codeänderung fehlt | `--server.fileWatcherType none` oder in `.streamlit/config.toml` setzen, oder `fs.inotify.max_user_watches` erhöhen. |
| pip-Fehler zu `tensorboard`/`tensorflow` nach Installation | `google-auth-oauthlib` oder `numpy`/`protobuf` wurden hochgezogen, bestehende Pakete verlangen ältere Versionen | Eigene Umgebung benutzen (siehe 3), oder `pip install "google-auth-oauthlib<1.1"`. Kein `-U` in gemeinsamen Umgebungen. |
| Cache-Token überall 0 | implizites Caching ist nicht garantiert. Im Batch zusätzlich undokumentiert. Die erste synchrone Anfrage hat per Definition 0 | `usage`-JSON einer Antwort ansehen. Synchron testen. PDFs, Reihenfolge und Systemanweisung unverändert lassen. |
| Drive: „Zugriff blockiert“ | Konto nicht als Test user eingetragen | Cloud Console → Audience → Test users (6.4, Schritt 4) |
| Drive: `credentials.json fehlt. Lege sie nach …/conf` | OAuth-Client nicht in `conf/` abgelegt | 6.4, Schritt 6 |
| Drive fragt nach einer Woche erneut nach Anmeldung | „Testing“-Status, Freigabe läuft nach 7 Tagen ab | erwartetes Verhalten, die App meldet sich selbst neu an. Dauerhaft: „Publish app“ oder Workspace „Internal“. |
| Start bricht ab, Statusfeld verschwindet | Tab während der Vorbereitung gewechselt (löst einen Rerun aus) | Start wiederholen und währenddessen im Tab bleiben. |
| Synchroner Lauf steht auf „unterbrochen“ | App wurde beendet oder neu gestartet, solange er lief | Bisherige Zeilen sind gespeichert. Mit „Eingaben übernehmen“ neu starten. |
| Antwort abgeschnitten, Warnung `MAX_TOKENS` | Denk- plus Ausgabe-Token haben das Limit erreicht | Denktiefe senken, Token-Limit erhöhen (synchron oder im Modus „Hintergrund“) oder Aufgabe auf mehrere Prompts aufteilen. |
| „Gespräch“: Prompt 2 „Übersprungen“ | Prompt 1 lieferte Fehler oder keine Antwort | Fehler in Prompt 1 beheben und neu starten. Folgende Paare laufen weiter. |
| „Abruf fehlgeschlagen … 404“ bei altem Batch | Batch existiert bei Google nicht mehr (nach Ablauf) und wurde lokal nie gesichert | Nichts zu retten. Eintrag löschen. Neue Batches sichern ihr Ergebnis lokal. |
| OpenAI-Eintrag: „Der OpenAI API-Key fehlt (Seitenleiste)“ | Das Keyfeld dieses Anbieters ist leer, während ein Eintrag von ihm geöffnet ist | Key eintragen. Gesicherte Ergebnisse sind auch ohne Key sichtbar. |
| „Das Paket openai fehlt“ beim Wechsel auf OpenAI | Nach dem Update wurde `pip install -r requirements.txt` nicht ausgeführt | Nachholen. Gemini läuft auch ohne das Paket. |
| OpenAI: Fehler je Prompt zu `reasoning.effort` | Das Modell kennt die gewählte Denktiefe nicht (z. B. `none` bei `gpt-6-astra`) | Andere Stufe oder „Standard“ wählen und neu starten (Eingaben übernehmen). |
| OpenAI-Batch endet mit `failed`, die Meldung nennt ein Token-Limit | Die Summe der Token aller eingereihten Batches überschreitet das Warteschlangenlimit des Modells | Weniger Prompts oder kürzere PDFs je Batch, warten bis andere Batches fertig sind, Limits der Kontostufe bei OpenAI prüfen. |
| OpenAI-Batch `expired`, einzelne Prompts mit Fehler | Das Zeitfenster von 24 Stunden reichte nicht für alle Anfragen. Die fertigen Antworten sind geladen | Die fehlenden Prompts mit „Eingaben übernehmen“ erneut starten. |
| `data/batch_jobs.defekt-<zahl>.json` taucht auf | `data/batch_jobs.json` war beschädigt, die App hat sie gesichert und neu begonnen | Sicherung prüfen, ggf. Einträge von Hand zurückkopieren. |
| Verlauf leer nach dem Wechsel auf `conf/` und `data/` | Alte `batch_jobs.json` lag nicht im Projektordner, oder `GEMINI_UI_HOME` zeigt woanders hin | Datei von Hand nach `data/` kopieren bzw. `GEMINI_UI_HOME` prüfen. |
| `ModuleNotFoundError: No module named 'gbui'` | Das Hauptskript steht ohne den Ordner `gbui/` daneben (z. B. nur die eine Datei kopiert) | Den Ordner `gbui/` neben `gemini_batch_ui.py` legen und mit `streamlit run src/gemini_batch_ui.py` starten (Abschnitt 7). |

## 11. Bekannte Einschränkungen und Ideen

**Einschränkungen**

- OpenAI gibt es nur im **Batch-Modus**. Der synchrone Modus (samt „Prompts pro Gespräch“) ist Gemini vorbehalten, weil er an Gemini-Typen und Gemini-Caching hängt.
- Die gültigen Denktiefen sind bei OpenAI nicht je Modell hinterlegt, weil sie sich schnell ändern: Die Auswahl zeigt alle Stufen, unpassende lehnt die API je Prompt ab.
- OpenAI bekommt PDFs mit Text und Seitenbildern, die Detailstufe (`detail`) ist nicht einstellbar. Die Eingabe-Token können sich deshalb von denen bei Gemini unterscheiden.
- Implizites Caching ist nicht garantiert und bei Carbon (wie bei Argon) die einzige Cache-Art. Im Batch gibt es dazu keine Doku, synchron ist es bestmöglich vorbereitet, aber unbewiesen.
- Synchron läuft **sequenziell**. Parallele Anfragen würden Cache-Treffer am Anfang eher verhindern.
- Der Modus „Hintergrund“ startet alle Prompts **gleichzeitig** (mögliche Ratenbegrenzungen bei vielen Prompts, ein Cache-Treffer fürs PDF ist weniger wahrscheinlich), kennt keine Gespräche (je Prompt eine Interaktion, eine Fortsetzung steckt im Prompt), hat keinen Batch-Rabatt und zeigt keine Zwischenstände oder Denk-Zusammenfassungen. Eine Interaktion läuft laut Google höchstens 24 Stunden, danach gilt sie als `failed`. Long Decode im **Batch** gibt es nicht, solange die Interactions API keinen Batch kennt.
- Der Start (Dateien laden, hochladen) läuft im Hauptskript. Ein Tab-Wechsel währenddessen bricht ihn ab (im Statusfeld vermerkt).
- Die Sperre in `runtime()` gilt nur innerhalb **eines** Streamlit-Prozesses. Nicht zwei Instanzen im selben Ordner starten.
- Der Verlauf wächst ohne Seitenaufteilung. Löschen geht nur einzeln. `data/requests_*.jsonl` sammeln sich an.
- Keine Kostenanzeige (Preise ändern sich), keine Streaming-Anzeige, keine Prompt-Bibliothek, kein Zählen der Token vor dem Start.
- Texte der Oberfläche sind fest auf Deutsch im Code.
- Drive: nur PDFs und Google-Dokumente (Export als PDF). Liste zeigt höchstens 50 Dateien, weitere per Link oder ID.
- „Gespräch fortsetzen“ schickt den Verlauf als Text in **einer** Nutzernachricht. Es ist kein echter Mehr-Runden-Verlauf mit Modellrolle, und gespeichert sind nur die sichtbaren Antworten (ohne Denkteile). Lange Verläufe kosten bei jeder Anfrage Eingabe-Token, und die PDFs müssen neu angehängt werden.

**Ideen** (grob nach Nutzen)

- Prompt-Vorlagen mit Platzhaltern und einer Werteliste (ein Prompt pro Wert), z. B. „Kapitel {k}, Abschnitt {a}“.
- Einzel-Download je Antwort als `.txt` (z. B. zum Vorlesen) und alle Ergebnisse als ZIP.
- Optionale Kostenschätzung mit **einstellbaren** Preisen (nicht fest verdrahten), Vergleich Batch gegen synchron mit Cache.
- Token zählen vor dem Start (`count_tokens`), um die PDF-Größe und die 4.096-Token-Schwelle zu sehen.
- Streaming der Antworten im synchronen Modus mit Zwischenständen im Eintrag.
- Eingaben des Formulars beim Neustart wiederherstellen.
- Fortsetzung als echter Mehr-Runden-Verlauf (`user`/`model`-Nachrichten statt Text im Prompt). Dazu müsste der Eintrag die Modellantworten samt Denk-Signaturen speichern.
- Long Decode im Batch, sobald Google die Interactions API für die Batch API öffnet (laut Doku „noch nicht verfügbar“), und die clientseitige Fortsetzung der Interactions API (laut Doku „bald“) als Alternative ohne Speicherung auf dem Server.
- Streaming für Hintergrund-Läufe (`stream`, `thinking_summaries`) mit Zwischenständen im Eintrag.
- Tarif/Modus „Flex“ oder andere Preisstufen, falls Google sie für Carbon anbietet (Doku prüfen).
- Explizites Caching als Umschalter für Modelle, die es unterstützen.
- Aufräumen alter `data/requests_*.jsonl` (und `data/requests_openai_*.jsonl`).
- OpenAI: Detailstufe der PDF-Seitenbilder (`detail`) wählbar machen, synchroner Modus über die Responses API, Modellvorschläge aus der Modellliste der API statt Freitext.
- Dependabot für die Versionen der Actions (`.github/dependabot.yml`).
- Wenige, bewusst kostenpflichtige „Live“-Tests gegen die echte API (eigener Marker, standardmäßig übersprungen), die Annahmen aus Abschnitt 5 prüfen.

## 12. Sicherheit und Datenschutz

- **Daten verlassen den Rechner**: PDFs, Prompts und Ergebnisse gehen an den gewählten Anbieter: Google (Gemini Files API, Batch-API, Modell) oder OpenAI (Files API, Batch API, Modell). Bei Google liegen hochgeladene Dateien zeitlich begrenzt (derzeit 48 Stunden), Batch-Ergebnisse 6 Wochen. Bei OpenAI setzt die App für PDFs einen Ablauf von 3 Tagen (`OPENAI_FILE_TTL`), die Batch-Datei folgt dem Standard von OpenAI (30 Tage). Die Ausgabe- und Fehlerdateien der Batches löscht die App nicht, sie unterliegen den Aufbewahrungsregeln der OpenAI-Plattform. Das gilt für Inhalte, die diese Weitergabe erlauben.
- **Drive**: nur Lesezugriff (`drive.readonly`). Die App kann nichts ändern oder löschen.
- **Geheimnisse**: `conf/` (vor allem `credentials.json` und `token.json`) nicht weitergeben und nicht einchecken. Die API-Keys stehen nur in den Browserfeldern oder in den Umgebungsvariablen.
- **Lokale Daten**: `data/` (z. B. `batch_jobs.json`, `requests_*.jsonl`) enthält Prompts und Antworten im Klartext. Ordner entsprechend schützen (`.gitignore`, Dateirechte).
- Die App ist für **einen Benutzer auf dem eigenen Rechner** gedacht, ohne Anmeldung. Streamlit lauscht standardmäßig auch im lokalen Netz (die Startmeldung nennt eine „Network URL“), dann könnte jeder im Netz die App samt API-Key-Feld bedienen. Wer das nicht möchte, startet mit `streamlit run src/gemini_batch_ui.py --server.address localhost`. Nicht ungeschützt im Internet betreiben.

## 13. Verifikationsstand und Quellen

**Verifikationsstand (bitte beim ersten echten Einsatz beobachten)**

- Automatisierte Tests (`pytest`, Abschnitt 9.3) prüfen die App durchgehend gegen Ersatzdienste. Sie bestätigen die Logik, nicht das Verhalten der echten Google-Dienste. Alle 281 Tests (Stand der Änderung für Issue #20) liefen grün in frisch installierten Umgebungen mit Python 3.12.3: mit den neuesten Versionen (Streamlit 1.67.0, google-genai 3.3.0, openai 3.51.0) und mit der niedrigsten unterstützten Kombination (Streamlit 1.67.0, google-genai 3.3.0, openai 1.100.0). Streamlit und google-genai liegen mit ihrer Mindestversion derzeit auf dem neuesten Stand, beide Läufe unterscheiden sich deshalb nur in der openai-Version. Gegenüber den 287 Tests davor entfallen sechs Testfälle, die nur die entfernte Versionsprüfung für google-genai vor 2.30.1 abdeckten. Mit Python 3.11 und mit älteren Versionen von Streamlit und google-genai wird nicht mehr getestet. Die GitHub-Actions-Matrix deckt 3.12 ab.
- Die Aufteilung in Module (Abschnitt 8) wurde zusätzlich ohne die Testsuite geprüft: Ein Vergleich der Syntaxbäume mit dem Stand davor zeigte, dass der Code unverändert verschoben wurde (nur `_base_dir()` ist angepasst, weil nun der Ort des Pakets zählt) und der Oberflächenteil identisch ist. Außerdem lief die App unter einem echten `streamlit run` (Streamlit 1.55.0, 1.56.0 und 1.67.0) gegen eine echte WebSocket-Sitzung ohne Browser: Beide Tabs wurden ohne Ausnahme gezeichnet, und nach einer Änderung an `gbui/config.py` zeigte der nächste Lauf den neuen Wert (die Module wurden neu importiert). Dieser Serverlauf gehört nicht zur Testsuite.
- „Gespräch fortsetzen“ und die Prompt-Anzeige wurden zusätzlich einmal in einem echten Headless-Chromium gegen die laufende App mit Ersatzdiensten durchgespielt (Klick im Fragment, Tab-Wechsel, Hinweis, Startsperre, zweites Fortsetzen ohne Verschachtelung). Dieser Browsertest gehört nicht zur Testsuite.
- Der Anbieterwechsel (Gemini, OpenAI, zurück zu Gemini, mit leerem und mit gesetztem OpenAI-Key, auch bei geöffnetem Jobs-Tab) wurde ebenfalls in einem echten Headless-Chromium gegen die laufende App durchgespielt. Modell, Denktiefe, Token-Limit, Prompts und Systemanweisung blieben erhalten, beide Anbieter behielten ihre eigenen Werte. Ohne die Zuweisung vor dem Zeichnen (`FORM_KEYS`) standen nach dem Rückwechsel das Modellfeld leer, das Token-Limit auf 256, die Denktiefe auf „Standard“, und Prompts und Systemanweisung fehlten. Auch dieser Browsertest gehört nicht zur Testsuite.
- Der GitHub-Workflow (Abschnitt 9.5) wurde mit `actionlint` geprüft, und seine Befehle liefen wie oben beschrieben in frischen Umgebungen. Auf GitHub selbst lief er noch nicht.
- Mit der echten API bestätigt (19.10.2026, noch mit Argon): Batch-Modus mit **einem** PDF und mehreren Prompts, Denktiefe `high`, Ergebnisabruf und Anzeige. Dabei meldeten alle Antworten Cache 0.
- **Umstellung auf Gemini 4 Carbon: nur anhand der Google-Dokumentation (11.11.2026) und mit den simulierten Diensten der Testsuite geprüft**, nicht gegen die echte API. Geändert wurden der Modellname (`DEFAULT_MODEL`), Beschriftungen und die Doku. Anfrageformat, `thinking_level`-Werte, SDK-Mindestversion (damals 2.30.1 für `generate_content`) und Token-Limits blieben unverändert, weil Google den Umstieg von Argon als reinen Modellnamenwechsel beschreibt.
- **Nur mit simulierten Diensten getestet**, nicht gegen echte Dienste: synchroner Modus (inklusive Long Decode und Gespräche), mehrere PDFs, Drive-Anbindung samt Neuanmeldung, lokale Sicherung der Batch-Ergebnisse, „Eingaben übernehmen“, „Gespräch fortsetzen“ (wie das Modell auf einen Prompt mit eingefügtem Verlauf antwortet), Löschen, Fehlerzähler der automatischen Prüfung. Die Ordnerstruktur (`src/`, `conf/`, `data/`) samt Übernahme alter Dateien lief nur in Wegwerf-Ordnern, noch nicht im echten Projektordner.
- **OpenAI-Anbindung: nur mit simulierten Diensten getestet**, nicht gegen die echte OpenAI-API (in der Entwicklungsumgebung gab es weder Key noch Zugriff). `FakeOpenAI` liefert echte SDK-Objekte (`Batch`, `FileObject`), und ein Test stellt sicher, dass die Beispielantworten gültige `Response`-Objekte des SDK sind (openai 3.46.0). Feldnamen, Endpunkt, Parameter und die erlaubten Denktiefen stammen aus der OpenAI-Dokumentation und dem SDK. Ungeprüft, bitte beim ersten echten Einsatz beobachten: ob ein Batch am Endpunkt `/v1/responses` PDFs per `file_id` (`input_file`) annimmt (die gelesenen Dokumentationsstellen schließen es nicht aus, bestätigen es für Batches aber auch nicht), welche Denktiefen das gewählte Modell kennt, ob Cache-Treffer im Batch vorkommen, die Fehlercodes der Fehlerdatei (die Beispiele in den Tests sind nicht aus echten Läufen) und die Meldung bei überschrittenem Warteschlangenlimit.
- **Hintergrund-Modus (Issue #19): nur anhand der Google-Dokumentation (Long Decode, Background Execution, Interactions API, Developer Guide zu Carbon, Stand 13.11.2026) und mit den simulierten Diensten der Testsuite geprüft**, nicht gegen die echte API (in der Entwicklungsumgebung gab es keinen API-Key). `FakeGemini` liefert echte SDK-Objekte (`google.genai.interactions.Interaction`), und ein Test stellt sicher, dass die gesendete Anfrage dem Schema des SDK entspricht (`CreateModelInteraction`). Ungeprüft, bitte beim ersten echten Einsatz beobachten: ob `gemini-4-carbon` die Interactions API mit `background=true` wie dokumentiert annimmt (der Developer Guide zu Carbon beschreibt `background=True`, die Doku zu Background Execution nennt in ihrer Modellliste, Stand 21.10.2026, nur Argon und Gemini 3.8 Flash), ob die Files-API-Referenz eines PDFs als `document` mit `uri` angenommen wird (so zeigt es die Doku zur Dokumentverarbeitung), ob die API ein `max_output_tokens` nahe 1.048.576 bei großer Eingabe ablehnt oder kürzt (Doku: „minus Eingabe“), ob mehrere gleichzeitige Läufe in Ratenbegrenzungen laufen und wie die Abrechnung der Schritte aussieht. Die App behandelt jeden Status außer `in_progress` und `queued` als Ende, damit eine unerwartete Statusangabe das Abfragen nicht endlos weiterlaufen lässt.
- Zur Frage in Issue #19, ob Carbon ohne Long Decode schon 65.536 statt 32.768 schafft: laut Google-Doku **ja, aber nur in der Interactions API** (Standardlimit 65.536, ohne `background=true` auch Obergrenze). Der Batch läuft über `generateContent` (nicht streamend 32.768 je Anfrage) und die Interactions API kennt laut Doku noch keinen Batch. Ob Carbon im Batch dennoch mehr als 32.768 annimmt, bleibt **ungeprüft** (Prüfweg in Abschnitt 5, Tabelle „Batch-Ausgabelimit“).
- Ungeprüft, bisher nur mit Argon beobachtet oder dokumentiert: ob die Batch-Antworten für Carbon ein Cache-Feld unter anderem Namen führen (siehe `usage`-JSON), ob der Batch bei Carbon dasselbe Ausgabelimit von 32.768 Token hat, ob der synchrone Aufruf mit `thinking_level` und Long Decode bei Carbon exakt wie dokumentiert antwortet und ob die 25 Minuten Timeout für `high` reichen.

**Quellen** (Stand Oktober 2026, bitte auf Änderungen prüfen)

- Was ist neu in Gemini 4 Carbon (Modell-ID, Denktiefe, Long Decode, Umstieg von Argon, Stand 11.11.2026): <https://aistudio.google.com/docs/whats-new-gemini-4-carbon>
- Preise für Gemini 4 Carbon und Argon (Standard, Batch, Stand 11.11.2026): <https://aistudio.google.com/docs/pricing>
- Gemini 4 Argon, Modellseite: <https://ai.google.dev/gemini-api/docs/models/gemini-4-argon>
- Was ist neu in Gemini 4 Argon (Denktiefe, Long Decode, Timeouts, Caching): <https://ai.google.dev/gemini-api/docs/generate-content/latest-model>
- Batch-API (JSONL-Format, Preis, Aufbewahrung): <https://ai.google.dev/gemini-api/docs/batch-api>
- Long Decode (Interactions API, `background=true`, Limits, Abrechnung, Statuswerte): <https://ai.google.dev/gemini-api/docs/long-decode>
- Background Execution (Anlegen, Abfragen, Abbrechen, Aufbewahrung): <https://ai.google.dev/gemini-api/docs/background-execution>
- Interactions API (Überblick, Aufbewahrung 55 Tage, Batch API „noch nicht verfügbar“): <https://ai.google.dev/gemini-api/docs/interactions-overview>
- Long Decode mit `generateContent` (clientseitige Fortsetzung, 32K je nicht streamender Anfrage): <https://ai.google.dev/gemini-api/docs/generate-content/long-decode>
- Gemini 4 Carbon: Developer Guide (65.536 Standardlimit der Interactions API, `background=True` bis 1M): <https://aistudio.google.com/learn/gemini-4-carbon-developer-guide>
- Context Caching (implizit/explizit, Nutzungsfelder): <https://ai.google.dev/gemini-api/docs/caching>
- Drive-API-Quickstart für Python (OAuth-Einrichtung): <https://developers.google.com/workspace/drive/api/quickstart/python>
- Streamlit-Dokumentation (`st.tabs`, `st.fragment`, `AppTest`): <https://docs.streamlit.io>

OpenAI (Stand 10.11.2026, bitte auf Änderungen prüfen):

- Batch API (Format, Fenster, Limits, Statuswerte, Ergebnis- und Fehlerdatei): <https://developers.openai.com/api/docs/guides/batch>
- Batch anlegen (Parameter): <https://developers.openai.com/api/docs/api-reference/batch/create>
- Dateien als Eingabe (`input_file`, `user_data`, Größenlimits): <https://developers.openai.com/api/docs/guides/file-inputs>
- PDF-Dateien (Text und Seitenbilder, `detail`): <https://developers.openai.com/api/docs/guides/pdf-files>
- Prompt Caching (Prefix, `cached_tokens`, Cache-Schreiben): <https://developers.openai.com/api/docs/guides/prompt-caching>
- Aktuelle Modelle und ihre Denktiefen: <https://developers.openai.com/api/docs/guides/latest-model> und Modellseite <https://developers.openai.com/api/docs/models/gpt-6-astra>

## Glossar

- **Anbieter**: Gemini (Google) oder OpenAI. Jeder Verlaufseintrag gehört zu einem Anbieter (Feld `provider`, fehlt es, gilt Gemini).
- **Responses API**: OpenAIs Schnittstelle für Textantworten. Die Batch API nimmt sie als Endpunkt `/v1/responses` und liefert Ausgabe- und Fehlerdatei.
- **Prefix**: der gemeinsame Anfang zweier Anfragen. Implizites Caching greift darauf.
- **Implizites Caching**: Google erkennt gleiche Anfänge automatisch und berechnet sie günstiger. Keine Zusage.
- **Explizites Caching**: man legt selbst einen Cache an und referenziert ihn. Garantiert die Ersparnis, für Carbon und Argon nicht verfügbar.
- **Long Decode**: automatische Fortsetzung über mehrere Teilanfragen, wenn die Ausgabe das Limit pro Anfrage überschreitet. Synchron setzt das SDK fort, im Hintergrund-Modus Googles Server (Schritte zu je 64K Token). Im Batch gibt es das nicht.
- **Hintergrund-Interaktion** (Background Execution): eine Anfrage der Interactions API mit `background=true`. Sie läuft bei Google bis zu 24 Stunden und wird über ihre ID abgefragt oder abgebrochen.
- **Runde**: ein früherer Prompt samt deiner Antwort dazu. Ein Gesprächsverlauf besteht aus Runden (Abschnitt 7).
- **Fragment**: ein Streamlit-Abschnitt, der sich allein neu ausführen kann (hier für die automatische Statusprüfung).
- **ScriptRunContext**: Streamlits Laufzeitkontext. Hintergrund-Threads haben keinen, deshalb dort keine `st.*`-Aufrufe.
