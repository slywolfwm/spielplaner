from datetime import date, time

import pandas as pd

from analysis import Team
from schedule_workflow import (
    compare_findings,
    compare_schedules,
    relevant_schedule_overview,
    reschedule_game,
)


TEAM = Team(
    "TSV Weilheim||BZOL M||Herren",
    "TSV Weilheim - Herren",
    "TSV Weilheim",
    "BZOL M",
    "Herren",
)


def schedule_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Tag": "Sa.",
                "Datum": "12.09.2026",
                "Zeit": "14:00",
                "Anwurf": pd.Timestamp("2026-09-12 14:00"),
                "Hallennummer": "270462",
                "Inhalt Tooltip Halle": "Hardtschule",
                "Spielnummer": "4711",
                "Runde": "1",
                "Liga": "BZOL M",
                "Staffelkurzbezeichnung": "Herren",
                "Heimmannschaft": "TSV Weilheim",
                "Gastmannschaft": "Gastverein",
            },
            {
                "Tag": "So.",
                "Datum": "13.09.2026",
                "Zeit": "16:00",
                "Anwurf": pd.Timestamp("2026-09-13 16:00"),
                "Hallennummer": "999999",
                "Inhalt Tooltip Halle": "Andere Halle",
                "Spielnummer": "9999",
                "Runde": "1",
                "Liga": "Andere Liga",
                "Staffelkurzbezeichnung": "Andere Staffel",
                "Heimmannschaft": "Anderer Verein",
                "Gastmannschaft": "Gastverein",
            },
        ]
    )


def test_overview_contains_relevant_game_once_and_reschedule_changes_only_time():
    frame = schedule_frame()
    overview = relevant_schedule_overview(frame, [TEAM])

    assert len(overview) == 1
    assert overview.iloc[0]["Gegner"] == "Gastverein"

    moved = reschedule_game(
        frame,
        overview.iloc[0]["Spielschlüssel"],
        date(2026, 9, 19),
        time(18, 30),
    )
    assert moved.iloc[0]["Anwurf"] == pd.Timestamp("2026-09-19 18:30")
    assert moved.iloc[0]["Hallennummer"] == "270462"
    assert moved.iloc[1]["Anwurf"] == frame.iloc[1]["Anwurf"]


def test_compare_schedules_shows_changed_added_and_removed_games():
    previous = schedule_frame().iloc[[0]].copy()
    current = previous.copy()
    current.loc[current.index[0], "Anwurf"] = pd.Timestamp("2026-09-12 15:00")
    current.loc[current.index[0], "Zeit"] = "15:00"

    changes = compare_schedules(current, previous, [TEAM])

    assert len(changes) == 1
    assert changes.iloc[0]["Status"] == "Geändert"
    assert changes.iloc[0]["Änderung"] == "Anwurf"
    assert "14:00" in changes.iloc[0]["Vorher"]
    assert "15:00" in changes.iloc[0]["Aktuell"]


def test_compare_findings_returns_only_delta():
    common = {
        "Priorität": "Mittel",
        "Regel": "Regel A",
        "Datum": date(2026, 9, 12),
        "Spiele": "Spiel A",
        "Halle": "Halle",
        "Kommentar": "Unverändert",
    }
    before = pd.DataFrame([common, {**common, "Kommentar": "Gelöst"}])
    after = pd.DataFrame([common, {**common, "Kommentar": "Neu"}])

    new, resolved = compare_findings(before, after)

    assert new["Kommentar"].tolist() == ["Neu"]
    assert resolved["Kommentar"].tolist() == ["Gelöst"]
