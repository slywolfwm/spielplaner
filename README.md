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
  und weist jedem Paar eine Priorität zu.
- **Fahrzeiten** zeigt die aus diesen Paaren abgeleitete, relevante Teilmatrix der
  Hallenverbindungen.
- **Anleitung** bündelt die Erläuterungen zur Bedienung und zu den Prüfregeln.

Aktuell sind drei Regeln aktiv: Für Überschneidungen definierter
Mannschaftspaare wird die Priorität je Paar als **hoch**, **mittel** oder
**niedrig** festgelegt. Dieselbe Priorität gilt für eine zu knappe Fahrzeit
zwischen zwei Spielen dieses Paars. Ein fehlender Puffer zwischen Heimspielen
hat die Priorität **mittel**. Die spätere Kontrolle der Hallenbuchungen ist noch
nicht aktiv. Pro Auffälligkeit wird genau eine Zeile mit Datum, betroffenen
Spielen, Halle und einer konkreten Erläuterung ausgegeben. Doppelte und
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

## Fahrzeitprüfung mit Google Maps

Die Fahrzeitprüfung erzeugt bewusst keine vollständige Matrix aller bayerischen
Hallen. Eine Verbindung wird nur dann live abgefragt, wenn

1. die beiden Mannschaften als zu prüfendes Paar festgelegt sind,
2. beide Mannschaften am selben Kalendertag spielen,
3. ihre Spiel- und Vorbereitungsfenster sich nicht bereits überschneiden,
4. die Spiele in unterschiedlichen Hallen stattfinden und
5. zwischen beiden Fenstern höchstens acht Stunden liegen.

Die Richtung folgt der tatsächlichen Spielabfolge. Mehrfach identische
Verbindungen zur gleichen Abfahrtszeit werden innerhalb eines Prüflaufs nur
einmal abgefragt. Die Routes API wird mit `TRAFFIC_AWARE_OPTIMAL` und dem
Verkehrsmodell `PESSIMISTIC` aufgerufen. Für die Planungszeit addiert die App
standardmäßig 15 Prozent Sicherheitszuschlag und 10 Minuten für Parkplatz und
Weg in die Halle und rundet anschließend auf fünf Minuten auf.

Google-Fahrtdauern und Entfernungen werden nicht dauerhaft in Azure gespeichert.
Das ist für ein EWR-Abrechnungskonto wichtig: Die aktuellen
[Google Maps Platform EEA Service Specific Terms](https://cloud.google.com/terms/maps-platform/eea/maps-service-terms)
erlauben bei der Routes API nur das zeitweise Speichern von Breiten- und
Längengraden, nicht von Fahrtdauern. Die relevante Hallenmatrix wird deshalb bei
jeder Prüfung aus dem eigenen nuLiga-Spielplan neu gebildet und live bewertet.
Die Darstellung nennt Google Maps unmittelbar als Datenquelle, wie in den
[Routes-Attributionsregeln](https://developers.google.com/maps/documentation/routes/policies)
vorgesehen.

### Kosten und Schutz vor unerwarteten Ausgaben

Das pessimistische Verkehrsmodell benötigt Compute Routes Pro. Nach der
[aktuellen Google-Preisliste](https://developers.google.com/maps/billing-and-pricing/pricing)
sind monatlich 5.000 Pro-Aufrufe kostenlos; anschließend beginnt die erste
Preisstufe bei 10 US-Dollar je 1.000 Aufrufe. Bei ungefähr zehn Nutzern und nur
wenigen Prüfläufen ist daher mit 0 US-Dollar API-Kosten zu rechnen. Trotzdem
sollten im Google-Cloud-Projekt ein Budgetalarm und eine Tagesquote von zum
Beispiel 100 Requests gesetzt werden. Die App begrenzt einen Prüflauf zusätzlich
standardmäßig auf 100 Aufrufe.

### Einmalige Google-Konfiguration

1. In einem Google-Cloud-Projekt die Abrechnung aktivieren und die
   [Routes API einschalten](https://developers.google.com/maps/documentation/routes/get-api-key).
2. Einen API-Schlüssel anlegen und als API-Einschränkung ausschließlich
   **Routes API** zulassen.
3. Als Anwendungseinschränkung nach Möglichkeit die möglichen ausgehenden
   IP-Adressen des Azure App Service hinterlegen. Diese zeigt Azure CLI mit
   `az webapp show --resource-group rg-spielplaner --name spielplaner-handamball-azure --query possibleOutboundIpAddresses --output tsv`.
4. Den Schlüssel direkt als Azure App Setting speichern, niemals in den Chat,
   das Repository oder eine lokale `secrets.toml` einchecken:

   ```bash
   az webapp config appsettings set \
     --resource-group rg-spielplaner \
     --name spielplaner-handamball-azure \
     --settings GOOGLE_MAPS_API_KEY="HIER_DIREKT_DEN_SCHLUESSEL_EINSETZEN"
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

Paarungen und Spieldauern werden in Azure Table Storage gespeichert. Dafür
benötigt der Azure App Service folgende Umgebungsvariablen:

- `AZURE_STORAGE_CONNECTION_STRING`: Verbindungszeichenfolge des Storage Accounts
- `AZURE_TABLE_NAME`: optionaler Tabellenname, Standard ist `teampairs`
- `AZURE_DURATION_TABLE_NAME`: optionaler Tabellenname für Spieldauern, Standard
  ist `teamdurations`
- `MICROSOFT_TENANT_ID`: Tenant, dessen angemeldete Benutzer die App verwenden
  dürfen
- `GOOGLE_MAPS_API_KEY`: serverseitiger, auf die Routes API eingeschränkter
  Google-Maps-API-Schlüssel
- `TRAVEL_TIME_SAFETY_PERCENT`: optionaler Sicherheitszuschlag, Standard `15`
- `TRAVEL_TIME_TRANSFER_BUFFER_MINUTES`: optionaler Parkplatz-/Hallenpuffer,
  Standard `10`
- `GOOGLE_ROUTES_MAX_REQUESTS_PER_RUN`: Schutzlimit je Prüflauf, Standard `100`

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
