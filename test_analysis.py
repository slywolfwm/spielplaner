import pandas as pd

from analysis import (
    ISSUE_COLUMNS,
    RULE_HOME_BUFFER,
    RULE_HALL_BOOKING,
    RULE_HALL_BOOKING_EXCESS,
    RULE_TEAM_OVERLAP,
    RULE_TRAVEL_TIME,
    Team,
    analyze_schedule,
    available_teams,
    default_game_duration,
    default_stoppage_buffer,
    find_home_game_buffer_conflicts,
    find_hall_booking_conflicts,
    find_hall_booking_excesses,
    find_overlaps,
    find_relevant_travel_legs,
    home_game_blocks,
    travel_leg_key,
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


def test_schedule_analysis_combines_rules_without_duplicate_pair_findings():
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
    team_a = Team("a", "A-Jugend", "TSV Weilheim", "BOL MA", "A1")
    team_b = Team("b", "B-Jugend", "TSV Weilheim", "OL MB", "B1")

    result = analyze_schedule(
        frame,
        [team_a, team_b],
        {"a": 70, "b": 60},
        [(team_a, team_b, "Niedrig"), (team_b, team_a, "Hoch")],
    )

    assert list(result.columns) == ISSUE_COLUMNS
    assert len(result) == 2
    assert result["Regel"].tolist() == [RULE_HOME_BUFFER, RULE_TEAM_OVERLAP]
    assert result["Priorität"].tolist() == ["Mittel", "Niedrig"]
    assert "es fehlen 10 Min." in result.iloc[0]["Kommentar"]
    assert "10 Min." in result.iloc[1]["Kommentar"]


def test_schedule_analysis_returns_stable_empty_table():
    frame = pd.DataFrame(
        columns=[
            "Anwurf",
            "Hallennummer",
            "Inhalt Tooltip Halle",
            "Spielnummer",
            "Liga",
            "Staffelkurzbezeichnung",
            "Heimmannschaft",
            "Gastmannschaft",
        ]
    )

    result = analyze_schedule(frame, [], {}, [])

    assert result.empty
    assert list(result.columns) == ISSUE_COLUMNS


def _travel_test_data(second_kickoff: str = "2026-10-11 14:30"):
    frame = pd.DataFrame(
        [
            {
                "Anwurf": pd.Timestamp("2026-10-11 12:00"),
                "Hallennummer": "100",
                "Inhalt Tooltip Halle": "Murnau, James-Loeb-Halle",
                "Spielnummer": "1",
                "Liga": "A",
                "Staffelkurzbezeichnung": "A1",
                "Heimmannschaft": "Gegner 1",
                "Gastmannschaft": "TSV Weilheim",
            },
            {
                "Anwurf": pd.Timestamp(second_kickoff),
                "Hallennummer": "200",
                "Inhalt Tooltip Halle": "Schongau, Lechsporthalle",
                "Spielnummer": "2",
                "Liga": "B",
                "Staffelkurzbezeichnung": "B1",
                "Heimmannschaft": "Gegner 2",
                "Gastmannschaft": "BSC Oberhausen",
            },
        ]
    )
    team_a = Team("a", "TSV Weilheim – A", "TSV Weilheim", "A", "A1")
    team_b = Team("b", "BSC Oberhausen – B", "BSC Oberhausen", "B", "B1")
    return frame, team_a, team_b


def test_relevant_travel_leg_is_directional_and_same_day_only():
    frame, team_a, team_b = _travel_test_data()

    legs = find_relevant_travel_legs(
        frame,
        [(team_a, team_b, "Niedrig")],
        {"a": 70, "b": 70},
    )

    assert len(legs) == 1
    assert legs.iloc[0]["Startschlüssel"] == "100"
    assert legs.iloc[0]["Zielschlüssel"] == "200"
    assert legs.iloc[0]["Verfügbar (Min.)"] == 50
    assert legs.iloc[0]["Priorität"] == "Niedrig"

    next_day = frame.copy()
    next_day.loc[1, "Anwurf"] += pd.Timedelta(days=1)
    assert find_relevant_travel_legs(
        next_day, [(team_a, team_b)], {"a": 70, "b": 70}
    ).empty


def test_overlapping_windows_and_same_hall_need_no_route():
    frame, team_a, team_b = _travel_test_data("2026-10-11 13:20")
    assert find_relevant_travel_legs(
        frame, [(team_a, team_b)], {"a": 70, "b": 70}
    ).empty

    frame, team_a, team_b = _travel_test_data()
    frame.loc[1, "Hallennummer"] = "100"
    frame.loc[1, "Inhalt Tooltip Halle"] = "Murnau, James-Loeb-Halle"
    assert find_relevant_travel_legs(
        frame, [(team_a, team_b)], {"a": 70, "b": 70}
    ).empty


def test_schedule_analysis_flags_insufficient_travel_time_with_pair_priority():
    frame, team_a, team_b = _travel_test_data()
    departure = pd.Timestamp("2026-10-11 13:10")

    result = analyze_schedule(
        frame,
        [team_a, team_b],
        {"a": 70, "b": 70},
        [(team_a, team_b, "Niedrig")],
        travel_minutes_by_leg={
            travel_leg_key("100", "200", departure): 65,
        },
    )

    assert len(result) == 1
    assert result.iloc[0]["Regel"] == RULE_TRAVEL_TIME
    assert result.iloc[0]["Priorität"] == "Niedrig"
    assert "konservative Fahrzeit: 65 Min." in result.iloc[0]["Kommentar"]
    assert "es fehlen 15 Min." in result.iloc[0]["Kommentar"]


def test_hall_booking_requires_all_parts_and_catering_room_for_full_window():
    blocks = pd.DataFrame(
        [
            {
                "Datum": pd.Timestamp("2026-09-20").date(),
                "Hallennummer": "270461",
                "Halle": "Weilheim, Jahnhalle",
                "Mannschaft": "TSV Weilheim – Herren",
                "Gegner": "Gegner",
                "Anwurf": pd.Timestamp("2026-09-20 17:30"),
                "Vorbereitung ab": pd.Timestamp("2026-09-20 17:00"),
                "Spielende": pd.Timestamp("2026-09-20 18:50"),
            }
        ]
    )
    bookings = pd.DataFrame(
        [
            {
                "Buchungsbeginn": pd.Timestamp("2026-09-20 16:45"),
                "Buchungsende": pd.Timestamp("2026-09-20 19:35"),
                "Raum-IDs": frozenset({"7702", "7703", "7710"}),
            },
            {
                "Buchungsbeginn": pd.Timestamp("2026-09-20 17:30"),
                "Buchungsende": pd.Timestamp("2026-09-20 19:35"),
                "Raum-IDs": frozenset({"7730"}),
            },
        ]
    )

    conflicts = find_hall_booking_conflicts(blocks, bookings)

    assert len(conflicts) == 1
    assert conflicts.iloc[0]["Fehlende Räume"] == "Bewirtungsraum (Verkaufsraum)"


def test_hall_booking_uses_first_and_last_game_with_setup_and_teardown():
    blocks = pd.DataFrame(
        [
            {
                "Datum": pd.Timestamp("2026-09-20").date(),
                "Hallennummer": "270461",
                "Halle": "Weilheim, Jahnhalle",
                "Mannschaft": "TSV Weilheim – E-Jugend",
                "Gegner": "Gegner 1",
                "Anwurf": pd.Timestamp("2026-09-20 10:00"),
                "Spielende": pd.Timestamp("2026-09-20 10:25"),
            },
            {
                "Datum": pd.Timestamp("2026-09-20").date(),
                "Hallennummer": "270461",
                "Halle": "Weilheim, Jahnhalle",
                "Mannschaft": "TSV Weilheim – Herren",
                "Gegner": "Gegner 2",
                "Anwurf": pd.Timestamp("2026-09-20 17:30"),
                "Spielende": pd.Timestamp("2026-09-20 18:50"),
            },
        ]
    )
    bookings = pd.DataFrame(
        [
            {
                "Buchungsbeginn": pd.Timestamp("2026-09-20 09:15"),
                "Buchungsende": pd.Timestamp("2026-09-20 19:35"),
                "Raum-IDs": frozenset({"7702", "7703", "7710", "7730"}),
            }
        ]
    )

    assert find_hall_booking_conflicts(blocks, bookings).empty
    assert find_hall_booking_excesses(blocks, bookings).empty


def test_hall_booking_flags_excess_connected_to_required_window_once_per_day():
    blocks = pd.DataFrame(
        [
            {
                "Datum": pd.Timestamp("2026-09-20").date(),
                "Hallennummer": "270461",
                "Halle": "Weilheim, Jahnhalle",
                "Mannschaft": "TSV Weilheim – Herren",
                "Gegner": "Gegner",
                "Anwurf": pd.Timestamp("2026-09-20 17:30"),
                "Spielende": pd.Timestamp("2026-09-20 18:50"),
            }
        ]
    )
    bookings = pd.DataFrame(
        [
            {
                "Buchungsbeginn": pd.Timestamp("2026-09-20 16:15"),
                "Buchungsende": pd.Timestamp("2026-09-20 20:05"),
                "Raum-IDs": frozenset({"7702", "7703", "7710", "7730"}),
            },
            {
                "Buchungsbeginn": pd.Timestamp("2026-09-20 21:00"),
                "Buchungsende": pd.Timestamp("2026-09-20 22:00"),
                "Raum-IDs": frozenset({"7702", "7703", "7710", "7730"}),
            },
        ]
    )

    excesses = find_hall_booking_excesses(blocks, bookings)

    assert len(excesses) == 1
    assert "30 Min. davor und 30 Min. danach" in excesses.iloc[0][
        "Zusätzliche Zeit"
    ]
    assert "21:00" not in excesses.iloc[0]["Zusätzliche Zeit"]


def test_schedule_analysis_reports_one_missing_hall_booking_finding_per_day():
    frame = pd.DataFrame(
        [
            {
                "Anwurf": pd.Timestamp("2026-10-03 17:30"),
                "Hallennummer": "270462",
                "Inhalt Tooltip Halle": "Weilheim, Am Hardt",
                "Spielnummer": "1",
                "Liga": "M",
                "Staffelkurzbezeichnung": "Herren",
                "Heimmannschaft": "TSV Weilheim",
                "Gastmannschaft": "Gegner",
            }
        ]
    )
    team = Team("a", "TSV Weilheim – Herren", "TSV Weilheim", "M", "Herren")

    result = analyze_schedule(
        frame,
        [team],
        {"a": 80},
        [],
        hall_bookings=pd.DataFrame(
            columns=["Buchungsbeginn", "Buchungsende", "Raum-IDs"]
        ),
    )

    assert len(result) == 1
    assert result.iloc[0]["Regel"] == RULE_HALL_BOOKING
    assert result.iloc[0]["Priorität"] == "Hoch"
    assert "Halle Ost" in result.iloc[0]["Kommentar"]
    assert "Bewirtungsraum (Küche)" in result.iloc[0]["Kommentar"]


def test_schedule_analysis_reports_excess_hall_booking_as_low_priority():
    frame = pd.DataFrame(
        [
            {
                "Anwurf": pd.Timestamp("2026-10-03 17:30"),
                "Hallennummer": "270462",
                "Inhalt Tooltip Halle": "Weilheim, Am Hardt",
                "Spielnummer": "1",
                "Liga": "M",
                "Staffelkurzbezeichnung": "Herren",
                "Heimmannschaft": "TSV Weilheim",
                "Gastmannschaft": "Gegner",
            }
        ]
    )
    team = Team("a", "TSV Weilheim – Herren", "TSV Weilheim", "M", "Herren")
    bookings = pd.DataFrame(
        [
            {
                "Buchungsbeginn": pd.Timestamp("2026-10-03 16:30"),
                "Buchungsende": pd.Timestamp("2026-10-03 20:00"),
                "Raum-IDs": frozenset({"7707", "7708", "7709", "7725"}),
            }
        ]
    )

    result = analyze_schedule(
        frame,
        [team],
        {"a": 80},
        [],
        hall_bookings=bookings,
    )

    assert len(result) == 1
    assert result.iloc[0]["Regel"] == RULE_HALL_BOOKING_EXCESS
    assert result.iloc[0]["Priorität"] == "Niedrig"
    assert "15 Min. davor und 25 Min. danach" in result.iloc[0]["Kommentar"]
