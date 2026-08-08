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
RULE_PRIORITIES = {
    RULE_TEAM_OVERLAP: "Hoch",
    RULE_HOME_BUFFER: "Mittel",
}


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


def analyze_schedule(
    frame: pd.DataFrame,
    teams: list[Team],
    duration_by_team_key: dict[str, int],
    team_pairs: list[tuple[Team, Team] | tuple[Team, Team, str]],
    pre_buffer_minutes: int = 30,
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


def _game_summary(
    kickoff: pd.Timestamp, team_label: str, opponent: str, location: str
) -> str:
    compact_label = re.sub(r"\s+\([^()]+\)$", "", team_label)
    return f"{kickoff:%H:%M} · {compact_label} {location} {opponent}"


def _join_unique(*values: object) -> str:
    unique = list(dict.fromkeys(str(value).strip() for value in values if value))
    return " / ".join(unique)
