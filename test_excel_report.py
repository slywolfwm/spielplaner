from io import BytesIO

from openpyxl import load_workbook
import pandas as pd

from excel_report import build_excel_report


def test_excel_report_contains_formatted_data_and_metadata_sheets():
    findings = pd.DataFrame(
        [
            {
                "Priorität": "Hoch",
                "Regel": "Überschneidung",
                "Datum": pd.Timestamp("2026-09-12").date(),
                "Spiele": "Spiel A | Spiel B",
                "Halle": "Hardtschule",
                "Kommentar": "Konflikt",
            }
        ]
    )
    schedule = pd.DataFrame(
        [
            {
                "Anwurf": pd.Timestamp("2026-09-12 14:00"),
                "Mannschaft": "TSV Weilheim",
                "Gegner": "Gastverein",
            }
        ]
    )
    metadata = pd.DataFrame(
        [
            {
                "Datenbestand": "Spielplan",
                "Stand": "12.09.2026, 12:00 Uhr",
                "Person": "Testnutzer",
                "Details": "Plan.csv",
            }
        ]
    )

    content = build_excel_report(findings, schedule, metadata)
    workbook = load_workbook(BytesIO(content))

    assert workbook.sheetnames == ["Metadaten", "Prüfergebnisse", "Spielplan"]
    assert workbook["Metadaten"]["A1"].value == "Spielplanprüfung: Datenstände"
    assert workbook["Prüfergebnisse"].freeze_panes == "A2"
    assert workbook["Prüfergebnisse"]["A2"].fill.fgColor.rgb.endswith("FCE8EA")
