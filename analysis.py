from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pandas as pd

from priorities import normalize_priority


REQUIRED_COLUMNS = {
    "Datum",
    "Zeit",
    "Hallennummer",
    "Inhalt Tooltip Halle",
    "Liga",
    "Staffelkurzbezeichnung",
    "Heimmannschaft",
    "Gastmannschaft",
}

ISSUE_COLUMNS = [
    "Priorität",
    "Regel",
    "Datum",
    "Spiele",
    "Halle",
    "Kommentar",
]
RULE_TEAM_OVERLAP = "Überschneidung Mannschaftspaar"
RULE_HOME_BUFFER = "Puffer zwischen Heimspielen"
RULE_TRAVEL_TIME = "Fahrzeit zwischen Spielen"
RULE_HALL_BOOKING = "Hallenbuchung unvollständig"
RULE_HALL_BOOKING_EXCESS = "Hallenbuchung zu lang"
HALL_SETUP_MINUTES = 45
HALL_TEARDOWN_MINUTES = 45
RULE_PRIORITIES = {
    RULE_TEAM_OVERLAP: "Hoch",
    RULE_HOME_BUFFER: "Mittel",
    RULE_HALL_BOOKING: "Hoch",
    RULE_HALL_BOOKING_EXCESS: "Niedrig",
}

HALL_BOOKING_REQUIREMENTS = {
    "270461": (
        ("7702", "Halle Süd"),
        ("7703", "Halle Mitte"),
        ("7710", "Halle Nord"),
        ("7730", "Bewirtungsraum (Verkaufsraum)"),
    ),
    "270462": (
        ("7707", "Halle Ost"),
        ("7708", "Halle Mitte"),
        ("7709", "Halle West"),
        ("7725", "Bewirtungsraum (Küche)"),
    ),
}
HALL_BOOKING_DISPLAY_NAMES = {
    "270461": "Jahnhalle",
    "270462": "Hardtschule",
}

TRAVEL_LEG_COLUMNS = [
    "Priorität",
    "Datum",
    "Team früher",
    "Gegner früher",
    "Spielort früher",
    "Spiel früher",
    "Anwurf früher",
    "Abfahrt",
    "Team später",
    "Gegner später",
    "Spielort später",
    "Spiel später",
    "Anwurf später",
    "Vorbereitung ab",
    "Verfügbar (Min.)",
    "Startschlüssel",
    "Starthalle",
    "Startadresse",
    "Zielschlüssel",
    "Zielhalle",
    "Zieladresse",
]


@dataclass(frozen=True)
class Team:
    key: str
    label: str
    club_name: str
    liga: str
    staffel: str


DISTRICT_TEAMS = (
    Team("TSV Weilheim||BZOL M||Herren", "TSV Weilheim – Herren", "TSV Weilheim", "BZOL M", "Herren"),
    Team("TSV Weilheim||BZOL F||Damen", "TSV Weilheim – Damen", "TSV Weilheim", "BZOL F", "Damen"),
    Team("TSV Weilheim II||AKL F||Damen II", "TSV Weilheim – Damen II", "TSV Weilheim II", "AKL F", "Damen II"),
    Team("TSV Weilheim||BZL MD||mD", "TSV Weilheim – mD", "TSV Weilheim", "BZL MD", "mD"),
    Team("TSV Weilheim II||BZK MD||mD II", "TSV Weilheim – mD II", "TSV Weilheim II", "BZK MD", "mD II"),
    Team("TSV Weilheim||BZK WD||wD", "TSV Weilheim – wD", "TSV Weilheim", "BZK WD", "wD"),
    Team("TSV Weilheim||E||E-Jugend", "TSV Weilheim – E-Jugend", "TSV Weilheim", "E", "E-Jugend"),
    Team("TSV Weilheim II||E||E-Jugend II", "TSV Weilheim – E-Jugend II", "TSV Weilheim II", "E", "E-Jugend II"),
    Team("TSV Weilheim||F||Minis", "TSV Weilheim – Minis", "TSV Weilheim", "F", "Minis"),
    Team("TSV Weilheim II||F||Minis II", "TSV Weilheim – Minis II", "TSV Weilheim II", "F", "Minis II"),
)


def load_schedule(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=";", encoding="cp1252", dtype=str).fillna("")
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"Fehlende CSV-Spalten: {', '.join(sorted(missing))}")

    frame["Zeit"] = frame["Zeit"].str.extract(
        r"(\d{1,2}:\d{2})", expand=False
    ).fillna("")
    frame["Anwurf"] = pd.to_datetime(
        frame["Datum"].str.strip() + " " + frame["Zeit"].str.strip(),
        format="%d.%m.%Y %H:%M",
        errors="coerce",
    )
    for side in ("Heimmannschaft", "Gastmannschaft"):
        frame[side] = frame[side].str.replace(
            r"\s+\(a\.K\.\)$", "", regex=True
        )
    return frame.loc[frame["Anwurf"].notna()].copy()


def available_teams(frame: pd.DataFrame) -> list[Team]:
    candidates: set[tuple[str, str, str]] = set()
    for side in ("Heimmannschaft", "Gastmannschaft"):
        for row in frame.loc[
            frame[side].str.startswith("TSV Weilheim")
            | (
                frame[side].eq("BSC Oberhausen")
                & frame["Liga"].str.contains("WA", case=False, na=False)
            ),
            [side, "Liga", "Staffelkurzbezeichnung"],
        ].itertuples(index=False, name=None):
            candidates.add(tuple(str(value).strip() for value in row))

    teams_by_key = {team.key: team for team in DISTRICT_TEAMS}
    for club_name, liga, staffel in sorted(candidates):
        key = "||".join((club_name, liga, staffel))
        teams_by_key.setdefault(
            key,
            Team(
                key=key,
                label=f"{club_name} – {staffel} ({liga})",
                club_name=club_name,
                liga=liga,
                staffel=staffel,
            )
        )
    return sorted(teams_by_key.values(), key=lambda team: team.label)


def games_for_team(frame: pd.DataFrame, team: Team) -> pd.DataFrame:
    in_competition = frame["Liga"].eq(team.liga) & frame[
        "Staffelkurzbezeichnung"
    ].eq(team.staffel)
    is_team = frame["Heimmannschaft"].eq(team.club_name) | frame[
        "Gastmannschaft"
    ].eq(team.club_name)
    return frame.loc[in_competition & is_team].copy()


def find_overlaps(
    frame: pd.DataFrame,
    team_a: Team,
    team_b: Team,
    game_minutes_a: int,
    game_minutes_b: int,
    pre_buffer_minutes: int = 30,
) -> pd.DataFrame:
    a = games_for_team(frame, team_a).copy()
    b = games_for_team(frame, team_b).copy()
    a["HalleAnzeige"] = _display_halls(a)
    b["HalleAnzeige"] = _display_halls(b)
    duration_a = pd.Timedelta(minutes=game_minutes_a)
    duration_b = pd.Timedelta(minutes=game_minutes_b)
    pre_buffer = pd.Timedelta(minutes=pre_buffer_minutes)
    results: list[dict[str, object]] = []

    for game_a in a.itertuples(index=False):
        a_start = game_a.Anwurf - pre_buffer
        a_end = game_a.Anwurf + duration_a
        for game_b in b.itertuples(index=False):
            b_start = game_b.Anwurf - pre_buffer
            b_end = game_b.Anwurf + duration_b
            if a_start < b_end and b_start < a_end:
                overlap_minutes = int(
                    (min(a_end, b_end) - max(a_start, b_start)).total_seconds()
                    // 60
                )
                results.append(
                    {
                        "Datum": game_a.Anwurf.date(),
                        "Team A": team_a.label,
                        "Spiel A": _meeting(game_a),
                        "Anwurf A": game_a.Anwurf,
                        "Team B": team_b.label,
                        "Spiel B": _meeting(game_b),
                        "Anwurf B": game_b.Anwurf,
                        "Abstand (Min.)": int(
                            abs((game_b.Anwurf - game_a.Anwurf).total_seconds()) // 60
                        ),
                        "Überschneidung (Min.)": overlap_minutes,
                        "Halle A": _hall(game_a),
                        "Halle B": _hall(game_b),
                        "Gegner A": _opponent(game_a, team_a),
                        "Gegner B": _opponent(game_b, team_b),
                        "Spielort A": _home_or_away(game_a, team_a),
                        "Spielort B": _home_or_away(game_b, team_b),
                    }
                )

    columns = [
        "Datum",
        "Team A",
        "Spiel A",
        "Anwurf A",
        "Team B",
        "Spiel B",
        "Anwurf B",
        "Abstand (Min.)",
        "Überschneidung (Min.)",
        "Halle A",
        "Halle B",
        "Gegner A",
        "Gegner B",
        "Spielort A",
        "Spielort B",
    ]
    return pd.DataFrame(results, columns=columns).sort_values(
        ["Anwurf A", "Anwurf B"], ignore_index=True
    )


def default_game_duration(team: Team) -> int:
    description = f"{team.liga} {team.staffel}".upper()
    if team.liga.upper() in {"E", "F"} or "MINI" in description:
        return 22
    age_match = re.search(r"\b[MW]([A-D])\b", team.liga, re.IGNORECASE)
    if not age_match or age_match.group(1).upper() == "A":
        return 70
    if age_match.group(1).upper() in {"B", "C"}:
        return 60
    return 40


def default_stoppage_buffer(team: Team) -> int:
    """Return the planning allowance for time-outs and other stoppages."""
    if team.liga.upper() in {"E", "F"}:
        return 3
    return 10


def home_game_blocks(
    frame: pd.DataFrame,
    teams: list[Team],
    duration_by_team_key: dict[str, int],
    pre_buffer_minutes: int = 30,
) -> pd.DataFrame:
    blocks: list[dict[str, object]] = []
    pre_buffer = pd.Timedelta(minutes=pre_buffer_minutes)

    for team in teams:
        home_games = frame.loc[
            frame["Heimmannschaft"].eq(team.club_name)
            & frame["Liga"].eq(team.liga)
            & frame["Staffelkurzbezeichnung"].eq(team.staffel)
        ]
        duration = int(duration_by_team_key[team.key])
        for _, game in home_games.iterrows():
            hall_number = str(game["Hallennummer"]).strip()
            hall_name = str(game["Inhalt Tooltip Halle"]).strip()
            hall_key = hall_number or hall_name or f"Spiel-{game['Spielnummer']}"
            kickoff = game["Anwurf"]
            blocks.append(
                {
                    "Datum": kickoff.date(),
                    "Hallenschlüssel": hall_key,
                    "Hallennummer": hall_number,
                    "Halle": hall_name,
                    "Mannschaft": team.label,
                    "Gegner": game["Gastmannschaft"],
                    "Spiel": f"{game['Heimmannschaft']} – {game['Gastmannschaft']}",
                    "Anwurf": kickoff,
                    "Vorbereitung ab": kickoff - pre_buffer,
                    "Spielende": kickoff + pd.Timedelta(minutes=duration),
                    "Planungsdauer (Min.)": duration,
                }
            )

    columns = [
        "Datum",
        "Hallenschlüssel",
        "Hallennummer",
        "Halle",
        "Mannschaft",
        "Gegner",
        "Spiel",
        "Anwurf",
        "Vorbereitung ab",
        "Spielende",
        "Planungsdauer (Min.)",
    ]
    return pd.DataFrame(blocks, columns=columns).sort_values(
        ["Hallenschlüssel", "Anwurf"], ignore_index=True
    )


def find_home_game_buffer_conflicts(
    blocks: pd.DataFrame, pre_buffer_minutes: int = 30
) -> pd.DataFrame:
    conflicts: list[dict[str, object]] = []

    for _, hall_games in blocks.groupby("Hallenschlüssel", sort=False):
        hall_games = hall_games.sort_values("Anwurf").reset_index(drop=True)
        for previous_index in range(len(hall_games)):
            previous = hall_games.iloc[previous_index]
            for next_index in range(previous_index + 1, len(hall_games)):
                following = hall_games.iloc[next_index]
                if following["Vorbereitung ab"] >= previous["Spielende"]:
                    break
                gap_minutes = int(
                    (following["Anwurf"] - previous["Spielende"]).total_seconds()
                    // 60
                )
                conflicts.append(
                    {
                        "Datum": following["Datum"],
                        "Halle": following["Halle"] or following["Hallennummer"],
                        "Vorherige Mannschaft": previous["Mannschaft"],
                        "Vorheriger Gegner": previous["Gegner"],
                        "Vorheriges Spiel": previous["Spiel"],
                        "Anwurf vorheriges Spiel": previous["Anwurf"],
                        "Spielende": previous["Spielende"],
                        "Nächste Mannschaft": following["Mannschaft"],
                        "Nächster Gegner": following["Gegner"],
                        "Nächstes Spiel": following["Spiel"],
                        "Anwurf nächstes Spiel": following["Anwurf"],
                        "Vorbereitung ab": following["Vorbereitung ab"],
                        "Verfügbarer Puffer (Min.)": gap_minutes,
                        "Fehlender Puffer (Min.)": pre_buffer_minutes - gap_minutes,
                    }
                )

    columns = [
        "Datum",
        "Halle",
        "Vorherige Mannschaft",
        "Vorheriger Gegner",
        "Vorheriges Spiel",
        "Anwurf vorheriges Spiel",
        "Spielende",
        "Nächste Mannschaft",
        "Nächster Gegner",
        "Nächstes Spiel",
        "Anwurf nächstes Spiel",
        "Vorbereitung ab",
        "Verfügbarer Puffer (Min.)",
        "Fehlender Puffer (Min.)",
    ]
    return pd.DataFrame(conflicts, columns=columns)


def find_hall_booking_conflicts(
    blocks: pd.DataFrame,
    bookings: pd.DataFrame,
    setup_minutes: int = HALL_SETUP_MINUTES,
    teardown_minutes: int = HALL_TEARDOWN_MINUTES,
) -> pd.DataFrame:
    """Find hall days not fully covered by every required OMOC room booking."""
    conflicts: list[dict[str, object]] = []
    for day in _hall_booking_days(blocks, setup_minutes, teardown_minutes):
        requirements = HALL_BOOKING_REQUIREMENTS[day["Hallennummer"]]
        missing_rooms = []
        for room_id, room_label in requirements:
            intervals = [
                (booking["Buchungsbeginn"], booking["Buchungsende"])
                for _, booking in bookings.iterrows()
                if room_id in booking["Raum-IDs"]
            ]
            if not _interval_is_covered(
                intervals, day["Benötigt von"], day["Benötigt bis"]
            ):
                missing_rooms.append(room_label)
        if missing_rooms:
            conflicts.append(
                {
                    **day,
                    "Fehlende Räume": ", ".join(missing_rooms),
                }
            )
    return pd.DataFrame(
        conflicts,
        columns=[
            "Datum",
            "Hallennummer",
            "Halle",
            "Spiele",
            "Benötigt von",
            "Benötigt bis",
            "Fehlende Räume",
        ],
    )


def find_hall_booking_excesses(
    blocks: pd.DataFrame,
    bookings: pd.DataFrame,
    setup_minutes: int = HALL_SETUP_MINUTES,
    teardown_minutes: int = HALL_TEARDOWN_MINUTES,
) -> pd.DataFrame:
    """Find connected OMOC bookings extending beyond a hall day's need."""
    excesses: list[dict[str, object]] = []
    for day in _hall_booking_days(blocks, setup_minutes, teardown_minutes):
        requirements = HALL_BOOKING_REQUIREMENTS[day["Hallennummer"]]
        grouped_rooms: dict[tuple[int, int], list[str]] = {}
        for room_id, room_label in requirements:
            intervals = _merge_intervals(
                [
                    (booking["Buchungsbeginn"], booking["Buchungsende"])
                    for _, booking in bookings.iterrows()
                    if room_id in booking["Raum-IDs"]
                ]
            )
            before = 0
            after = 0
            for start, end in intervals:
                if end <= day["Benötigt von"] or start >= day["Benötigt bis"]:
                    continue
                before = max(
                    before,
                    max(
                        0,
                        int(
                            (day["Benötigt von"] - start).total_seconds() // 60
                        ),
                    ),
                )
                after = max(
                    after,
                    max(
                        0,
                        int((end - day["Benötigt bis"]).total_seconds() // 60),
                    ),
                )
            if before or after:
                grouped_rooms.setdefault((before, after), []).append(room_label)

        if grouped_rooms:
            details = []
            for (before, after), room_labels in grouped_rooms.items():
                rooms = ", ".join(room_labels)
                if before and after:
                    deviation = f"{before} Min. davor und {after} Min. danach"
                elif before:
                    deviation = f"{before} Min. davor"
                else:
                    deviation = f"{after} Min. danach"
                details.append(f"{rooms}: {deviation}")
            excesses.append(
                {
                    **day,
                    "Zusätzliche Zeit": "; ".join(details),
                }
            )

    return pd.DataFrame(
        excesses,
        columns=[
            "Datum",
            "Hallennummer",
            "Halle",
            "Spiele",
            "Benötigt von",
            "Benötigt bis",
            "Zusätzliche Zeit",
        ],
    )


def find_relevant_travel_legs(
    frame: pd.DataFrame,
    team_pairs: list[tuple[Team, Team] | tuple[Team, Team, str]],
    duration_by_team_key: dict[str, int],
    pre_buffer_minutes: int = 30,
    max_gap_minutes: int = 480,
) -> pd.DataFrame:
    """Return only same-day routes that could constrain a configured team pair."""
    legs: list[dict[str, object]] = []
    seen_pairs: set[tuple[str, str]] = set()
    pre_buffer = pd.Timedelta(minutes=pre_buffer_minutes)
    hall_catalog = _hall_catalog(frame)

    for pair in team_pairs:
        team_a, team_b = pair[:2]
        priority = normalize_priority(pair[2] if len(pair) == 3 else None)
        pair_key = tuple(sorted((team_a.key, team_b.key)))
        if team_a.key == team_b.key or pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        games_a = games_for_team(frame, team_a)
        games_b = games_for_team(frame, team_b)
        duration_a = pd.Timedelta(minutes=duration_by_team_key[team_a.key])
        duration_b = pd.Timedelta(minutes=duration_by_team_key[team_b.key])

        for _, game_a in games_a.iterrows():
            for _, game_b in games_b.iterrows():
                if game_a["Anwurf"].date() != game_b["Anwurf"].date():
                    continue

                a_start = game_a["Anwurf"] - pre_buffer
                a_end = game_a["Anwurf"] + duration_a
                b_start = game_b["Anwurf"] - pre_buffer
                b_end = game_b["Anwurf"] + duration_b
                if a_start < b_end and b_start < a_end:
                    continue

                if game_a["Anwurf"] <= game_b["Anwurf"]:
                    earlier_team, earlier_game, earlier_end = team_a, game_a, a_end
                    later_team, later_game, later_start = team_b, game_b, b_start
                else:
                    earlier_team, earlier_game, earlier_end = team_b, game_b, b_end
                    later_team, later_game, later_start = team_a, game_a, a_start

                available_minutes = int(
                    (later_start - earlier_end).total_seconds() // 60
                )
                if available_minutes < 0 or available_minutes > max_gap_minutes:
                    continue

                origin_key, origin_name, origin_address = _hall_identity(
                    earlier_game, hall_catalog
                )
                destination_key, destination_name, destination_address = (
                    _hall_identity(later_game, hall_catalog)
                )
                if not origin_key or not destination_key or origin_key == destination_key:
                    continue

                legs.append(
                    {
                        "Priorität": priority,
                        "Datum": earlier_game["Anwurf"].date(),
                        "Team früher": earlier_team.label,
                        "Gegner früher": _opponent_series(earlier_game, earlier_team),
                        "Spielort früher": _home_or_away_series(
                            earlier_game, earlier_team
                        ),
                        "Spiel früher": _meeting_series(earlier_game),
                        "Anwurf früher": earlier_game["Anwurf"],
                        "Abfahrt": earlier_end,
                        "Team später": later_team.label,
                        "Gegner später": _opponent_series(later_game, later_team),
                        "Spielort später": _home_or_away_series(
                            later_game, later_team
                        ),
                        "Spiel später": _meeting_series(later_game),
                        "Anwurf später": later_game["Anwurf"],
                        "Vorbereitung ab": later_start,
                        "Verfügbar (Min.)": available_minutes,
                        "Startschlüssel": origin_key,
                        "Starthalle": origin_name,
                        "Startadresse": origin_address,
                        "Zielschlüssel": destination_key,
                        "Zielhalle": destination_name,
                        "Zieladresse": destination_address,
                    }
                )

    if not legs:
        return pd.DataFrame(columns=TRAVEL_LEG_COLUMNS)
    return (
        pd.DataFrame(legs, columns=TRAVEL_LEG_COLUMNS)
        .drop_duplicates(
            subset=[
                "Team früher",
                "Team später",
                "Anwurf früher",
                "Anwurf später",
                "Startschlüssel",
                "Zielschlüssel",
            ]
        )
        .sort_values(
            ["Verfügbar (Min.)", "Anwurf früher", "Anwurf später"],
            ignore_index=True,
        )
    )


def analyze_schedule(
    frame: pd.DataFrame,
    teams: list[Team],
    duration_by_team_key: dict[str, int],
    team_pairs: list[tuple[Team, Team] | tuple[Team, Team, str]],
    pre_buffer_minutes: int = 30,
    travel_minutes_by_leg: dict[tuple[str, str, str], int] | None = None,
    hall_bookings: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Run all active rules and return one concise row per finding."""
    findings: list[dict[str, object]] = []

    blocks = home_game_blocks(
        frame, teams, duration_by_team_key, pre_buffer_minutes
    )
    for _, conflict in find_home_game_buffer_conflicts(
        blocks, pre_buffer_minutes
    ).iterrows():
        findings.append(
            {
                "Priorität": RULE_PRIORITIES[RULE_HOME_BUFFER],
                "Regel": RULE_HOME_BUFFER,
                "Datum": conflict["Datum"],
                "Spiele": " | ".join(
                    (
                        _game_summary(
                            conflict["Anwurf vorheriges Spiel"],
                            conflict["Vorherige Mannschaft"],
                            conflict["Vorheriger Gegner"],
                            "gegen",
                        ),
                        _game_summary(
                            conflict["Anwurf nächstes Spiel"],
                            conflict["Nächste Mannschaft"],
                            conflict["Nächster Gegner"],
                            "gegen",
                        ),
                    )
                ),
                "Halle": conflict["Halle"],
                "Kommentar": (
                    f"Verfügbar: {conflict['Verfügbarer Puffer (Min.)']} "
                    f"Min.; erforderlich: {pre_buffer_minutes} Min.; es fehlen "
                    f"{conflict['Fehlender Puffer (Min.)']} Min."
                ),
            }
        )

    seen_pairs: set[tuple[str, str]] = set()
    for pair in team_pairs:
        team_a, team_b = pair[:2]
        priority = normalize_priority(pair[2] if len(pair) == 3 else None)
        pair_key = tuple(sorted((team_a.key, team_b.key)))
        if team_a.key == team_b.key or pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        overlaps = find_overlaps(
            frame,
            team_a,
            team_b,
            duration_by_team_key[team_a.key],
            duration_by_team_key[team_b.key],
            pre_buffer_minutes,
        )
        for _, overlap in overlaps.iterrows():
            findings.append(
                {
                    "Priorität": priority,
                    "Regel": RULE_TEAM_OVERLAP,
                    "Datum": min(overlap["Anwurf A"], overlap["Anwurf B"]).date(),
                    "Spiele": " | ".join(
                        (
                            _game_summary(
                                overlap["Anwurf A"],
                                team_a.label,
                                overlap["Gegner A"],
                                overlap["Spielort A"],
                            ),
                            _game_summary(
                                overlap["Anwurf B"],
                                team_b.label,
                                overlap["Gegner B"],
                                overlap["Spielort B"],
                            ),
                        )
                    ),
                    "Halle": _join_unique(overlap["Halle A"], overlap["Halle B"]),
                    "Kommentar": (
                        "Die eingeplanten Zeitfenster überschneiden sich um "
                        f"{overlap['Überschneidung (Min.)']} Min."
                    ),
                }
            )

    for _, leg in find_relevant_travel_legs(
        frame,
        team_pairs,
        duration_by_team_key,
        pre_buffer_minutes,
    ).iterrows():
        leg_key = travel_leg_key(
            leg["Startschlüssel"], leg["Zielschlüssel"], leg["Abfahrt"]
        )
        planning_minutes = (travel_minutes_by_leg or {}).get(leg_key)
        if planning_minutes is None or planning_minutes <= leg["Verfügbar (Min.)"]:
            continue
        missing_minutes = planning_minutes - leg["Verfügbar (Min.)"]
        findings.append(
            {
                "Priorität": leg["Priorität"],
                "Regel": RULE_TRAVEL_TIME,
                "Datum": leg["Datum"],
                "Spiele": " | ".join(
                    (
                        _game_summary(
                            leg["Anwurf früher"],
                            leg["Team früher"],
                            leg["Gegner früher"],
                            leg["Spielort früher"],
                        ),
                        _game_summary(
                            leg["Anwurf später"],
                            leg["Team später"],
                            leg["Gegner später"],
                            leg["Spielort später"],
                        ),
                    )
                ),
                "Halle": f"{leg['Starthalle']} → {leg['Zielhalle']}",
                "Kommentar": (
                    f"Verfügbar: {leg['Verfügbar (Min.)']} Min.; konservative "
                    f"Fahrzeit: {planning_minutes} Min.; es fehlen "
                    f"{missing_minutes} Min."
                ),
            }
        )

    if hall_bookings is not None:
        for _, conflict in find_hall_booking_conflicts(
            blocks, hall_bookings
        ).iterrows():
            findings.append(
                {
                    "Priorität": RULE_PRIORITIES[RULE_HALL_BOOKING],
                    "Regel": RULE_HALL_BOOKING,
                    "Datum": conflict["Datum"],
                    "Spiele": conflict["Spiele"],
                    "Halle": conflict["Halle"],
                    "Kommentar": (
                        f"{conflict['Halle']} nicht vollständig gebucht. "
                        f"Erforderliches OMOC-Fenster: "
                        f"{conflict['Benötigt von']:%H:%M}-"
                        f"{conflict['Benötigt bis']:%H:%M} Uhr."
                    ),
                }
            )
        for _, excess in find_hall_booking_excesses(
            blocks, hall_bookings
        ).iterrows():
            findings.append(
                {
                    "Priorität": RULE_PRIORITIES[RULE_HALL_BOOKING_EXCESS],
                    "Regel": RULE_HALL_BOOKING_EXCESS,
                    "Datum": excess["Datum"],
                    "Spiele": excess["Spiele"],
                    "Halle": excess["Halle"],
                    "Kommentar": (
                        f"Nicht benötigte Buchungszeit: "
                        f"{excess['Zusätzliche Zeit']}. Benötigtes OMOC-Fenster: "
                        f"{excess['Benötigt von']:%H:%M}-"
                        f"{excess['Benötigt bis']:%H:%M} Uhr."
                    ),
                }
            )

    if not findings:
        return pd.DataFrame(columns=ISSUE_COLUMNS)

    priority_order = {"Hoch": 0, "Mittel": 1, "Niedrig": 2}
    result = pd.DataFrame(findings, columns=ISSUE_COLUMNS).drop_duplicates()
    result["_Priorität"] = result["Priorität"].map(priority_order)
    return result.sort_values(
        ["_Priorität", "Datum", "Regel", "Spiele"],
        ignore_index=True,
    ).drop(columns="_Priorität")


def _meeting(row: object) -> str:
    return f"{row.Heimmannschaft} – {row.Gastmannschaft}"


def _meeting_series(row: pd.Series) -> str:
    return f"{row['Heimmannschaft']} – {row['Gastmannschaft']}"


def _hall(row: object) -> str:
    values = row._asdict() if hasattr(row, "_asdict") else {}
    return str(
        values.get("HalleAnzeige") or values.get("Hallennummer") or ""
    ).strip()


def _display_halls(frame: pd.DataFrame) -> pd.Series:
    hall_names = (
        frame["Inhalt Tooltip Halle"]
        if "Inhalt Tooltip Halle" in frame
        else pd.Series("", index=frame.index)
    )
    hall_numbers = (
        frame["Hallennummer"]
        if "Hallennummer" in frame
        else pd.Series("", index=frame.index)
    )
    return hall_names.where(hall_names.astype(str).str.strip().ne(""), hall_numbers)


def _opponent(row: object, team: Team) -> str:
    if row.Heimmannschaft == team.club_name:
        return str(row.Gastmannschaft)
    return str(row.Heimmannschaft)


def _home_or_away(row: object, team: Team) -> str:
    return "gegen" if row.Heimmannschaft == team.club_name else "bei"


def _opponent_series(row: pd.Series, team: Team) -> str:
    if row["Heimmannschaft"] == team.club_name:
        return str(row["Gastmannschaft"])
    return str(row["Heimmannschaft"])


def _home_or_away_series(row: pd.Series, team: Team) -> str:
    return "gegen" if row["Heimmannschaft"] == team.club_name else "bei"


def _hall_identity(
    row: pd.Series, hall_catalog: dict[str, str]
) -> tuple[str, str, str]:
    hall_number = str(row.get("Hallennummer", "")).strip()
    hall_address = str(row.get("Inhalt Tooltip Halle", "")).strip()
    if not hall_address and hall_number:
        hall_address = hall_catalog.get(hall_number, "")
    hall_key = hall_number or _normalized_hall(hall_address)
    hall_name = hall_address or hall_number
    return hall_key, hall_name, hall_address


def _hall_catalog(frame: pd.DataFrame) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for hall_number, hall_name in frame[
        ["Hallennummer", "Inhalt Tooltip Halle"]
    ].itertuples(index=False, name=None):
        number = str(hall_number).strip()
        name = str(hall_name).strip()
        if number and name:
            catalog.setdefault(number, name)
    return catalog


def _normalized_hall(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def travel_leg_key(
    origin_key: object, destination_key: object, departure: object
) -> tuple[str, str, str]:
    return (
        str(origin_key),
        str(destination_key),
        pd.Timestamp(departure).isoformat(),
    )


def _game_summary(
    kickoff: pd.Timestamp, team_label: str, opponent: str, location: str
) -> str:
    compact_label = re.sub(r"\s+\([^()]+\)$", "", team_label)
    return f"{kickoff:%H:%M} · {compact_label} {location} {opponent}"


def _join_unique(*values: object) -> str:
    unique = list(dict.fromkeys(str(value).strip() for value in values if value))
    return " / ".join(unique)


def _interval_is_covered(
    intervals: list[tuple[object, object]], required_start: object, required_end: object
) -> bool:
    coverage_end = None
    for start, end in sorted(intervals, key=lambda item: item[0]):
        if end <= required_start or start >= required_end:
            continue
        if coverage_end is None:
            if start > required_start:
                return False
            coverage_end = end
        elif start <= coverage_end and end > coverage_end:
            coverage_end = end
        if coverage_end >= required_end:
            return True
    return False


def _hall_booking_days(
    blocks: pd.DataFrame, setup_minutes: int, teardown_minutes: int
) -> list[dict[str, object]]:
    target_blocks = blocks.loc[
        blocks["Hallennummer"].astype(str).isin(HALL_BOOKING_REQUIREMENTS)
    ]
    days: list[dict[str, object]] = []
    for (_, hall_number), hall_games in target_blocks.groupby(
        ["Datum", "Hallennummer"], sort=True
    ):
        hall_games = hall_games.sort_values("Anwurf")
        first = hall_games.iloc[0]
        last = hall_games.iloc[-1]
        days.append(
            {
                "Datum": first["Datum"],
                "Hallennummer": str(hall_number),
                "Halle": HALL_BOOKING_DISPLAY_NAMES.get(
                    str(hall_number), first["Halle"] or str(hall_number)
                ),
                "Spiele": " | ".join(
                    _game_summary(
                        game["Anwurf"],
                        game["Mannschaft"],
                        game["Gegner"],
                        "gegen",
                    )
                    for _, game in hall_games.iterrows()
                ),
                "Benötigt von": first["Anwurf"]
                - pd.Timedelta(minutes=int(setup_minutes)),
                "Benötigt bis": last["Spielende"]
                + pd.Timedelta(minutes=int(teardown_minutes)),
            }
        )
    return days


def _merge_intervals(
    intervals: list[tuple[object, object]],
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    merged: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for raw_start, raw_end in sorted(intervals, key=lambda item: item[0]):
        start = pd.Timestamp(raw_start)
        end = pd.Timestamp(raw_end)
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        if end > merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
    return merged
