import pandas as pd

from analysis import (
    Team,
    available_teams,
    default_game_duration,
    default_stoppage_buffer,
    find_home_game_buffer_conflicts,
    find_overlaps,
    home_game_blocks,
)


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

    result = find_overlaps(frame, team_a, team_b, 120, 120, 0)

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

    without_buffer = find_overlaps(frame, team_a, team_b, 120, 120, 0)
    with_buffer = find_overlaps(frame, team_a, team_b, 120, 120, 40)

    assert without_buffer.empty
    assert len(with_buffer) == 1


def test_default_duration_includes_halftime_break():
    a_youth = Team("a", "A", "TSV Weilheim", "BOL MA", "A1")
    b_youth = Team("b", "B", "TSV Weilheim", "OL MB", "B1")
    c_youth = Team("c", "C", "TSV Weilheim", "BOL WC", "C1")

    assert default_game_duration(a_youth) == 70
    assert default_game_duration(b_youth) == 60
    assert default_game_duration(c_youth) == 60
    assert default_stoppage_buffer(a_youth) == 10
    assert default_stoppage_buffer(b_youth) == 10


def test_defaults_for_d_youth_and_children():
    d_youth = Team("d", "D", "TSV Weilheim", "BZL MD", "mD")
    e_youth = Team("e", "E", "TSV Weilheim", "E", "E-Jugend")
    minis = Team("f", "F", "TSV Weilheim", "F", "Minis")

    assert default_game_duration(d_youth) == 40
    assert default_game_duration(e_youth) == 22
    assert default_game_duration(minis) == 22
    assert default_stoppage_buffer(e_youth) == 3
    assert default_stoppage_buffer(minis) == 3


def test_district_catalog_includes_teams_without_published_games():
    frame = pd.DataFrame(
        columns=["Heimmannschaft", "Gastmannschaft", "Liga", "Staffelkurzbezeichnung"]
    )

    labels = {team.label for team in available_teams(frame)}

    assert "TSV Weilheim – Herren" in labels
    assert "TSV Weilheim – Damen II" in labels
    assert "TSV Weilheim – E-Jugend" in labels
    assert "TSV Weilheim – Minis II" in labels


def test_home_game_check_detects_missing_pre_game_buffer():
    frame = pd.DataFrame(
        [
            {
                "Anwurf": pd.Timestamp("2026-10-11 14:00"),
                "Hallennummer": "270462",
                "Inhalt Tooltip Halle": "Weilheim, Am Hardt",
                "Spielnummer": "1",
                "Liga": "BOL MA",
                "Staffelkurzbezeichnung": "A1",
                "Heimmannschaft": "TSV Weilheim",
                "Gastmannschaft": "Gegner 1",
            },
            {
                "Anwurf": pd.Timestamp("2026-10-11 15:30"),
                "Hallennummer": "270462",
                "Inhalt Tooltip Halle": "Weilheim, Am Hardt",
                "Spielnummer": "2",
                "Liga": "OL MB",
                "Staffelkurzbezeichnung": "B1",
                "Heimmannschaft": "TSV Weilheim",
                "Gastmannschaft": "Gegner 2",
            },
        ]
    )
    teams = [
        Team("a", "A-Jugend", "TSV Weilheim", "BOL MA", "A1"),
        Team("b", "B-Jugend", "TSV Weilheim", "OL MB", "B1"),
    ]

    blocks = home_game_blocks(frame, teams, {"a": 70, "b": 60})
    conflicts = find_home_game_buffer_conflicts(blocks)

    assert len(conflicts) == 1
    assert conflicts.iloc[0]["Verfügbarer Puffer (Min.)"] == 20
    assert conflicts.iloc[0]["Fehlender Puffer (Min.)"] == 10
