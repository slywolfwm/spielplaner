# Spielplaner

Lokale Streamlit-App für den Gesamtspielplan Bayern 2026/27. Enthalten sind alle
Mannschaften des TSV Weilheim sowie die weibliche A-Jugend des BSC Oberhausen.

## Start

Unter Windows `start_app.bat` doppelt anklicken. Alternativ in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Die voreingestellte Spieldauer beträgt 120 Minuten. Dauer und Puffer lassen sich
in der Seitenleiste ändern. Über den CSV-Upload kann später ein aktualisierter
nuLiga-Gesamtspielplan eingelesen werden.

## Azure App Service

Die App ist für einen Linux App Service vorbereitet. Als Startbefehl in Azure
`startup.sh` eintragen. Azure installiert die Python-Pakete aus der
`requirements.txt` im Stammverzeichnis des Repositorys.
