"""Logik der App ohne die Oberfläche (die steht in src/gemini_batch_ui.py), nach Thema in Module geteilt.

Ein Modul importiert nur Module, die in dieser Liste über ihm stehen. So bleibt die Reihenfolge ohne Zyklen:

    config         Konstanten, Ordner conf/ und data/
    history        Verlauf in data/batch_jobs.json (Sperre, Lesen, atomares Schreiben)
    rows           Ergebniszeilen: gemeinsame Zeilenform, Parser für Gemini
    drive          Google Drive
    gemini         Gemini: PDF hochladen, Batch starten
    background     Gemini: Hintergrund-Modus (Interactions API, Long Decode), starten, abfragen, abbrechen
    openai_batch   OpenAI: PDF hochladen, Batch starten, Ergebnisse lesen
    providers      Anbieter: Client, Betriebsart eines Eintrags, Status holen und sichern, abbrechen
    sync           synchroner Modus (Hintergrund-Thread)
    talk           Gesprächsverlauf als Text
    forms          Formular „Neue Anfrage“ und Verlaufseinträge (Callbacks)
    views          Anzeige der Einträge und Ergebnisse (läuft als Fragment)

Auf Modulebene steht kein Streamlit-Aufruf außer Dekoratoren wie @st.cache_resource: Module laufen nur beim ersten
Import, das Hauptskript dagegen bei jedem Rerun.
"""
