import pandas as pd

from analysis import Team, find_overlaps


def test_detects_overlap_and_ignores_later_game():
    frame = pd.DataFrame(
        [
            {
                "Anwurf": pd.Timestamp("2026-09-12 14:00"),
                "Liga": "A",
                "Staffelkurzbezeichnung": "A1",
                "Heimmannschaft": "TSV Weilheim",
                "Gastmannschaft": "Gegner 1",
            },
            {
                "Anwurf": pd.Timestamp("2026-09-12 15:30"),
                "Liga": "B",
                "Staffelkurzbezeichnung": "B1",
                "Heimmannschaft": "BSC Oberhausen",
                "Gastmannschaft": "Gegner 2",
            },
            {
                "Anwurf": pd.Timestamp("2026-09-12 18:00"),
                "Liga": "B",
                "Staffelkurzbezeichnung": "B1",
                "Heimmannschaft": "Gegner 3",
                "Gastmannschaft": "BSC Oberhausen",
            },
        ]
    )
    team_a = Team("a", "Weilheim", "TSV Weilheim", "A", "A1")
    team_b = Team("b", "Oberhausen", "BSC Oberhausen", "B", "B1")

    result = find_overlaps(frame, team_a, team_b, game_minutes=120)

    assert len(result) == 1
    assert result.iloc[0]["Abstand (Min.)"] == 90


def test_buffer_can_create_conflict():
    frame = pd.DataFrame(
        [
            {
                "Anwurf": pd.Timestamp("2026-09-12 14:00"),
                "Liga": "A",
                "Staffelkurzbezeichnung": "A1",
                "Heimmannschaft": "TSV Weilheim",
                "Gastmannschaft": "Gegner 1",
            },
            {
                "Anwurf": pd.Timestamp("2026-09-12 16:30"),
                "Liga": "B",
                "Staffelkurzbezeichnung": "B1",
                "Heimmannschaft": "BSC Oberhausen",
                "Gastmannschaft": "Gegner 2",
            },
        ]
    )
    team_a = Team("a", "Weilheim", "TSV Weilheim", "A", "A1")
    team_b = Team("b", "Oberhausen", "BSC Oberhausen", "B", "B1")

    without_buffer = find_overlaps(frame, team_a, team_b, 120, 0)
    with_buffer = find_overlaps(frame, team_a, team_b, 120, 20)

    assert without_buffer.empty
    assert len(with_buffer) == 1
