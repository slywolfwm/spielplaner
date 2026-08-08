# Spielplaner

Lokale Streamlit-App für den Gesamtspielplan Bayern 2026/27. Enthalten sind alle
Mannschaften des TSV Weilheim sowie die weibliche A-Jugend des BSC Oberhausen.

## Start

Unter Windows `start_app.bat` doppelt anklicken. Alternativ in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Die Navigation teilt die App in vier Seiten auf:

- **Spielplanprüfung** wendet alle aktiven Regeln auf die vollständige Saison an
  und gibt eine priorisierte, kommentierte Ergebnistabelle aus.
- **Spieldauern** verwaltet Regeldauer und Unterbrechungspuffer je Mannschaft.
- **Mannschaftspaare** legt die Teams fest, die sich nicht überschneiden dürfen,
  und weist jedem Paar eine Priorität zu.
- **Anleitung** bündelt die Erläuterungen zur Bedienung und zu den Prüfregeln.

Aktuell sind zwei Regeln aktiv: Für Überschneidungen definierter
Mannschaftspaare wird die Priorität je Paar als **hoch**, **mittel** oder
**niedrig** festgelegt; ein fehlender Puffer zwischen Heimspielen hat die
Priorität **mittel**. Die spätere Kontrolle der Hallenbuchungen ist noch nicht
aktiv. Pro Auffälligkeit wird genau eine Zeile mit Datum, betroffenen Spielen,
Halle und einer konkreten Erläuterung ausgegeben. Doppelte und umgekehrt
eingetragene Mannschaftspaare erzeugen keine redundanten Ergebnisse.

Ein Zeitraumsfilter ist nicht erforderlich; die Prüfungen beziehen sich immer
auf den vollständigen geladenen Saisonspielplan.

Die Regeldauer und ein zusätzlicher Unterbrechungspuffer lassen sich je
Mannschaft pflegen. Als Startwerte werden für die A-Jugend 70 Minuten und für
die B-/C-Jugend 60 Minuten einschließlich Halbzeitpause verwendet. Hinzu kommen
standardmäßig 10 Minuten für Team-Time-outs und sonstige Unterbrechungen. Dieser
Wert ist eine konservative Planungsannahme, keine vom Verband festgelegte
Bruttospieldauer. Vor jedem Anwurf berücksichtigt die App außerdem einen festen
Vorlauf von 30 Minuten. Über
den CSV-Upload kann später ein aktualisierter nuLiga-Gesamtspielplan eingelesen
werden.

Der mitgelieferte Vereinsspielplan ergänzt den Regionsspielplan um die bereits
veröffentlichten Begegnungen der Herren, Damen sowie mD-/wD-Jugend aus dem Bezirk
Alpenvorland. Damen II, E-Jugend und Minis sind ebenfalls auswählbar; für diese
Mannschaften hat nuLiga derzeit noch keine Saisonspiele terminiert. Sobald diese
Termine veröffentlicht sind, kann die ergänzende CSV aktualisiert werden.

Für D-Jugend-Turnierspiele gelten als Startwert 40 Minuten (2 × 15 Minuten plus
10 Minuten Pause). Für E-Jugend und Minis werden konservativ 22 Minuten angesetzt
(längste reguläre Variante 2 × 10 Minuten plus 2 Minuten Pause); weil dort keine
Team-Time-outs vorgesehen sind, beträgt der Unterbrechungspuffer zunächst 3
Minuten. Diese Werte sind in der App je Mannschaft änderbar.

Die Heimspielprüfung vergleicht die Belegungsblöcke der enthaltenen
Mannschaften je Halle und meldet zu knappe Abfolgen. Der spätere Abgleich mit
tatsächlich gebuchten Hallenzeiten ist noch nicht enthalten.

Regelgrundlagen für die Startwerte:

- [BHV-Durchführungsbestimmungen A-/B-/C-Jugend 2026/27](https://www.bhv-online.de/filemanager/BHV/Daten/Spielbetrieb/Durchfuehrungsbestimmungen/26_27/2026-03-dfb-c-a.pdf)
- [Aktuelles DHB-Handballregelwerk](https://www.dhb.de/services/schiedsrichter/handball-regeln-deutschland-regelwerk)
- [DHB-Zusatzbestimmungen zu Team-Time-outs ab 01.07.2025](https://www.bhv-online.de/filemanager/BHV/Daten/Schiedsrichter_neu%202023/Spielregeln/dhb-zusatzbestimmungen-zu-den-internationalen-handballregeln-ab-dem-01.07.2025-1.pdf)
- [Zuständigkeit Spielbetrieb Alpenvorland](https://www.bhv-online.de/bezirke-des-bhv/alpenvorland/spielbetrieb/)
- [Durchführungsbestimmungen D-Jugend Alpenvorland 2025/26](https://www.bhv-online.de/filemanager/Bezirke/Alpenvorland/Daten/Spielbetrieb/Saison%202025_26/dbst-25-26-djugend.pdf)
- [Durchführungsbestimmungen E-Jugend Alpenvorland 2025/26](https://www.bhv-online.de/filemanager/Bezirke/Alpenvorland/Daten/Spielbetrieb/Saison%202025_26/dbst-25-26-ejugend.pdf)
- [Durchführungsbestimmungen F-Jugend/Minis Alpenvorland 2025/26](https://www.bhv-online.de/filemanager/Bezirke/Alpenvorland/Daten/Spielbetrieb/Saison%202025_26/dbst-25-26-fjugend.pdf)

## Geschützte gespeicherte Paarungen

Die normale Analyse bleibt öffentlich. Gespeicherte Paarungen werden nur für
angemeldete Benutzer mit einer der Microsoft-Entra-App-Rollen angezeigt:

- `Pairings.Viewer`: gespeicherte Paarungen sehen und prüfen
- `Pairings.Editor`: zusätzlich Paarungen speichern und löschen sowie
  Spieldauern dauerhaft ändern

Paarungen und Spieldauern werden in Azure Table Storage gespeichert. Dafür
benötigt die App folgende Umgebungsvariablen beziehungsweise gleichnamige
Einträge in den Streamlit-Secrets:

- `AZURE_STORAGE_CONNECTION_STRING`: Verbindungszeichenfolge des Storage Accounts
- `AZURE_TABLE_NAME`: optionaler Tabellenname, Standard ist `teampairs`
- `AZURE_DURATION_TABLE_NAME`: optionaler Tabellenname für Spieldauern, Standard
  ist `teamdurations`

In Azure App Service muss Microsoft Entra als Identitätsanbieter eingerichtet
sein. Nicht authentifizierte Anforderungen bleiben erlaubt, damit die allgemeine
Analyse öffentlich erreichbar bleibt. Die App prüft die Rollen ausschließlich
für den geschützten Bereich.

## Streamlit Community Cloud und eigene Domain

Für die geplante Bereitstellung wird die App aus dem Branch `main` auf
Streamlit Community Cloud veröffentlicht. Repository und Einstiegspunkt sind:

- Repository: `slywolfwm/spielplaner`
- Branch: `main`
- App-Datei: `app.py`
- gewünschte Streamlit-URL: `spielplaner-handamball.streamlit.app`

Die Inhalte aus `.streamlit/secrets.toml.example` werden in den App-Einstellungen
unter **Secrets** mit den echten Azure- und Entra-Werten hinterlegt. Die Datei
`secrets.toml` selbst darf nicht in Git eingecheckt werden.

Die Microsoft-Entra-App benötigt als Web-Weiterleitungs-URI:

```text
https://spielplaner.handamball.de/oauth2callback
```

Der Cloudflare Worker im Ordner `cloudflare` veröffentlicht die Streamlit-App
unter `https://spielplaner.handamball.de`. Die Befehle und Voraussetzungen für
das Deployment stehen in `cloudflare/README.md`.

## Azure App Service

Die App ist für einen Linux App Service vorbereitet. Als Startbefehl in Azure
`startup.sh` eintragen. Azure installiert die Python-Pakete aus der
`requirements.txt` im Stammverzeichnis des Repositorys.
