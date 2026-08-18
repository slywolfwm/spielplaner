# Spielplaner

Lokale Streamlit-App für den Gesamtspielplan Bayern 2026/27. Enthalten sind alle
Mannschaften des TSV Weilheim sowie die weibliche A-Jugend des BSC Oberhausen.

## Start

Unter Windows `start_app.bat` doppelt anklicken. Alternativ in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Die Navigation teilt die App in fünf Seiten auf:

- **Spielplanprüfung** wendet alle aktiven Regeln auf die vollständige Saison an
  und gibt eine priorisierte, kommentierte Ergebnistabelle aus.
- **Spieldauern** verwaltet Regeldauer und Unterbrechungspuffer je Mannschaft.
- **Mannschaftspaare** legt die Teams fest, die sich nicht überschneiden dürfen,
  und weist jedem Paar eine Priorität zu. Jede ungeordnete Kombination erscheint
  in der Auswahlmatrix genau einmal.
- **Fahrzeiten** zeigt die aus diesen Paaren abgeleitete, relevante Teilmatrix der
  Hallenverbindungen.
- **Anleitung** bündelt die Erläuterungen zur Bedienung und zu den Prüfregeln.

Aktuell sind vier Regeln aktiv: Für Überschneidungen definierter
Mannschaftspaare wird die Priorität je Paar als **hoch**, **mittel** oder
**niedrig** festgelegt. Dieselbe Priorität gilt für eine zu knappe Fahrzeit
zwischen zwei Spielen dieses Paars. Ein fehlender Puffer zwischen Heimspielen
hat die Priorität **mittel**. Unvollständige OMOC-Buchungen für Jahnhalle und
Hardtschule haben die Priorität **hoch**. Pro Auffälligkeit wird genau eine Zeile
mit Datum, betroffenen Spielen, Halle und einer konkreten Erläuterung ausgegeben. Doppelte und
umgekehrt eingetragene Mannschaftspaare erzeugen keine redundanten Ergebnisse.

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
werden. Berechtigte Benutzer speichern einen Upload automatisch versioniert in
Azure Blob Storage. Beim nächsten Start verwendet die App die zuletzt
hochgeladene gültige Datei und zeigt deren Upload-Zeitpunkt an.

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
Mannschaften je Halle, meldet zu knappe Abfolgen und gleicht Jahnhalle sowie
Hardtschule mit den tatsächlich in OMOC gebuchten Zeiten ab.

## Fahrzeitprüfung mit Azure Maps

Die Fahrzeitprüfung erzeugt bewusst keine vollständige Matrix aller bayerischen
Hallen. Eine Verbindung wird nur dann live abgefragt, wenn

1. die beiden Mannschaften als zu prüfendes Paar festgelegt sind,
2. beide Mannschaften am selben Kalendertag spielen,
3. ihre Spiel- und Vorbereitungsfenster sich nicht bereits überschneiden,
4. die Spiele in unterschiedlichen Hallen stattfinden und
5. zwischen beiden Fenstern höchstens acht Stunden liegen.

Die Richtung folgt der tatsächlichen Spielabfolge. Azure Maps geocodiert die
beiden Hallenadressen und berechnet die schnellste Route unter Berücksichtigung
des Verkehrs. Für die Planungszeit addiert die App standardmäßig 15 Prozent
Sicherheitszuschlag und 10 Minuten für Parkplatz und Weg in die Halle und rundet
anschließend auf fünf Minuten auf.

Eine ermittelte Fahrtdauer wird gerichtet und abhängig von Wochentag und
Abfahrtszeit in Azure Table Storage gespeichert. Die Gültigkeit entspricht dem
von Azure zurückgegebenen Cache-Zeitraum, höchstens jedoch sechs Monaten. Nach
Ablauf wird nur dieser benötigte Eintrag aktualisiert. Dadurch wächst die Matrix
ausschließlich mit tatsächlich relevanten Spielabfolgen.

### Kosten und Schutz vor unerwarteten Ausgaben

Azure Maps Gen2 enthält monatlich 1.000 kostenlose Routing-Transaktionen. Bei
ungefähr zehn Nutzern, dem bedarfsgesteuerten Cache und maximal 100 neuen
Routen je Prüflauf ist daher mit keinen zusätzlichen API-Kosten zu rechnen. Eine
eigene Ressourcengruppe für Azure Maps erhält trotzdem ein Monatsbudget mit
Warnungen, damit andere App-Service-Kosten den Alarm nicht verfälschen.

### Einmalige Azure-Maps-Konfiguration

Azure Maps wird in der Ressourcengruppe `rg-spielplaner-maps` als Gen2-Konto
angelegt. Der Schlüssel wird direkt als App Setting gespeichert und weder im
Repository noch in einer lokalen `secrets.toml` eingecheckt:

```bash
az group create --name rg-spielplaner-maps --location westeurope
az maps account create --name maps-spielplaner-wm2026 \
  --resource-group rg-spielplaner-maps --kind Gen2 --sku G2 \
  --accept-tos --disable-local-auth false
MAPS_KEY=$(az maps account keys list --name maps-spielplaner-wm2026 \
  --resource-group rg-spielplaner-maps --query primaryKey --output tsv)
az webapp config appsettings set --resource-group rg-spielplaner \
  --name spielplaner-handamball-azure \
  --settings AZURE_MAPS_SUBSCRIPTION_KEY="$MAPS_KEY" \
    AZURE_TRAVEL_TIME_TABLE_NAME="traveltimes"
unset MAPS_KEY
```

Für die separate Maps-Ressourcengruppe wird ein Monatsbudget von 1 Euro mit
Warnungen bei 50 und 100 Prozent angelegt:

```bash
BUDGET_EMAIL="sylvester.wolf@handamball.de"
BUDGET_START="$(date -u +%Y-%m-01)"
BUDGET_END="$(date -u -d '+10 years' +%Y-%m-01)"
NOTIFICATIONS=$(printf '{"Half":{"enabled":true,"operator":"GreaterThanOrEqualTo","threshold":50,"contact-emails":["%s"]},"Full":{"enabled":true,"operator":"GreaterThanOrEqualTo","threshold":100,"contact-emails":["%s"]}}' "$BUDGET_EMAIL" "$BUDGET_EMAIL")
az consumption budget create-with-rg \
  --resource-group rg-spielplaner-maps \
  --budget-name budget-maps-spielplaner \
  --amount 1 --category Cost --time-grain Monthly \
  --time-period "{\"start-date\":\"$BUDGET_START\",\"end-date\":\"$BUDGET_END\"}" \
  --notifications "$NOTIFICATIONS"
```

Budgetwarnungen informieren nur; sie stoppen Azure Maps nicht automatisch.

## Hallenbuchungsprüfung mit OMOC

Die Hallenbuchungsregel verarbeitet ausschließlich Buchungen mit
`name_firma = Handball` und ausschließlich diese Ressourcen:

- Jahnhalle: Halle Süd, Mitte und Nord sowie der Verkaufsraum als
  Bewirtungsraum.
- Hardtschule: Halle Ost, Mitte und West sowie die Küche als Bewirtungsraum.

Für jedes Heimspiel muss jeder der vier Räume das vollständige Fenster vom
30-Minuten-Vorlauf bis zum berechneten Spielende abdecken. Mehrere unmittelbar
aneinander anschließende Buchungen dürfen das Fenster gemeinsam abdecken. Eine
fehlende oder zeitlich zu kurze Buchung erzeugt genau einen Befund pro Spiel.
Andere Sportstätten, Kostensätze, Namen und Veranstaltungstitel werden verworfen.

OMOC-Zugangsdaten werden ausschließlich als Azure App Settings gespeichert:

```bash
az webapp config appsettings set --resource-group rg-spielplaner \
  --name spielplaner-handamball-azure \
  --settings OMOC_BOOKINGS_URL="HIER_OMOC_BUCHUNGS_URL" \
    OMOC_API_USERNAME="HIER_OMOC_BENUTZER" \
    OMOC_API_PASSWORD="HIER_NEUES_OMOC_KENNWORT"
```

Regelgrundlagen für die Startwerte:

- [BHV-Durchführungsbestimmungen A-/B-/C-Jugend 2026/27](https://www.bhv-online.de/filemanager/BHV/Daten/Spielbetrieb/Durchfuehrungsbestimmungen/26_27/2026-03-dfb-c-a.pdf)
- [Aktuelles DHB-Handballregelwerk](https://www.dhb.de/services/schiedsrichter/handball-regeln-deutschland-regelwerk)
- [DHB-Zusatzbestimmungen zu Team-Time-outs ab 01.07.2025](https://www.bhv-online.de/filemanager/BHV/Daten/Schiedsrichter_neu%202023/Spielregeln/dhb-zusatzbestimmungen-zu-den-internationalen-handballregeln-ab-dem-01.07.2025-1.pdf)
- [Zuständigkeit Spielbetrieb Alpenvorland](https://www.bhv-online.de/bezirke-des-bhv/alpenvorland/spielbetrieb/)
- [Durchführungsbestimmungen D-Jugend Alpenvorland 2025/26](https://www.bhv-online.de/filemanager/Bezirke/Alpenvorland/Daten/Spielbetrieb/Saison%202025_26/dbst-25-26-djugend.pdf)
- [Durchführungsbestimmungen E-Jugend Alpenvorland 2025/26](https://www.bhv-online.de/filemanager/Bezirke/Alpenvorland/Daten/Spielbetrieb/Saison%202025_26/dbst-25-26-ejugend.pdf)
- [Durchführungsbestimmungen F-Jugend/Minis Alpenvorland 2025/26](https://www.bhv-online.de/filemanager/Bezirke/Alpenvorland/Daten/Spielbetrieb/Saison%202025_26/dbst-25-26-fjugend.pdf)

## Anmeldung und geschützte gespeicherte Paarungen

Die gesamte App ist ausschließlich für angemeldete Benutzer aus dem
konfigurierten Microsoft-Entra-Tenant zugänglich. Zusätzlich steuern die
Microsoft-Entra-App-Rollen den Zugriff auf dauerhaft gespeicherte Einstellungen:

- `Pairings.Viewer`: gespeicherte Paarungen sehen und prüfen
- `Pairings.Editor`: zusätzlich Paarungen speichern und löschen sowie
  Spieldauern dauerhaft ändern

Paarungen und Spieldauern werden in Azure Table Storage gespeichert. Hochgeladene
Spielpläne werden als versionierte CSV-Dateien in einem privaten Azure-Blob-
Container abgelegt. Dafür benötigt der Azure App Service folgende
Umgebungsvariablen:

- `AZURE_STORAGE_CONNECTION_STRING`: Verbindungszeichenfolge des Storage Accounts
- `AZURE_TABLE_NAME`: optionaler Tabellenname, Standard ist `teampairs`
- `AZURE_DURATION_TABLE_NAME`: optionaler Tabellenname für Spieldauern, Standard
  ist `teamdurations`
- `AZURE_SCHEDULE_CONTAINER_NAME`: optionaler Blob-Container für versionierte
  Spielplan-Uploads, Standard ist `schedules`
- `AZURE_TRAVEL_TIME_TABLE_NAME`: optionaler Tabellenname für den Fahrzeitcache,
  Standard ist `traveltimes`
- `MICROSOFT_TENANT_ID`: Tenant, dessen angemeldete Benutzer die App verwenden
  dürfen
- `AZURE_MAPS_SUBSCRIPTION_KEY`: serverseitiger Azure-Maps-Schlüssel
- `TRAVEL_TIME_SAFETY_PERCENT`: optionaler Sicherheitszuschlag, Standard `15`
- `TRAVEL_TIME_TRANSFER_BUFFER_MINUTES`: optionaler Parkplatz-/Hallenpuffer,
  Standard `10`
- `AZURE_MAPS_MAX_REQUESTS_PER_RUN`: Schutzlimit für neue Azure-Maps-Abfragen je
  Prüflauf, Standard `100`
- `OMOC_BOOKINGS_URL`, `OMOC_API_USERNAME`, `OMOC_API_PASSWORD`: serverseitige
  Zugangsdaten der OMOC-Buchungsabfrage

## Azure App Service

Die App läuft im Linux App Service `spielplaner-handamball-azure` und ist unter
`https://spielplaner.handamball.de` erreichbar. Als Startbefehl ist
`bash startup.sh` konfiguriert. Azure installiert die Python-Pakete aus der
`requirements.txt` im Stammverzeichnis des Repositorys.

Microsoft Entra wird über App Service Authentication (Easy Auth) vorgeschaltet.
Die Entra-App-Registrierung benötigt die beiden Web-Weiterleitungs-URIs:

```text
https://spielplaner-handamball-azure.azurewebsites.net/.auth/login/aad/callback
https://spielplaner.handamball.de/.auth/login/aad/callback
```

Der Aussteller ist
`https://login.microsoftonline.com/c0cba668-b196-49f4-b4e8-36af0e1cc1bd/v2.0`.
In der App-Registrierung muss die Ausgabe von ID-Token für Hybridflows aktiviert
sein. App Service Authentication verlangt eine Anmeldung und akzeptiert nur
Token aus dem konfigurierten Tenant.

Jeder Push auf `main` führt die Tests aus und stellt die App über
`.github/workflows/main_spielplaner-handamball-azure.yml` bereit. Für lokale
Entwicklung kann `.streamlit/secrets.toml.example` kopiert und mit einer lokalen
OIDC-Weiterleitungs-URI verwendet werden; `secrets.toml` darf nicht in Git
eingecheckt werden.
