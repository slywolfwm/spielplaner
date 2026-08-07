from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {
    "Datum",
    "Zeit",
    "Liga",
    "Staffelkurzbezeichnung",
    "Heimmannschaft",
    "Gastmannschaft",
}


@dataclass(frozen=True)
class Team:
    key: str
    label: str
    club_name: str
    liga: str
    staffel: str


def load_schedule(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=";", encoding="cp1252", dtype=str).fillna("")
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"Fehlende CSV-Spalten: {', '.join(sorted(missing))}")

    frame["Anwurf"] = pd.to_datetime(
        frame["Datum"].str.strip() + " " + frame["Zeit"].str.strip(),
        format="%d.%m.%Y %H:%M",
        errors="coerce",
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

    teams = []
    for club_name, liga, staffel in sorted(candidates):
        key = "||".join((club_name, liga, staffel))
        teams.append(
            Team(
                key=key,
                label=f"{club_name} – {staffel} ({liga})",
                club_name=club_name,
                liga=liga,
                staffel=staffel,
            )
        )
    return teams


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
    game_minutes: int = 120,
    buffer_minutes: int = 0,
) -> pd.DataFrame:
    a = games_for_team(frame, team_a).copy()
    b = games_for_team(frame, team_b).copy()
    duration = pd.Timedelta(minutes=game_minutes)
    buffer = pd.Timedelta(minutes=buffer_minutes)
    results: list[dict[str, object]] = []

    for game_a in a.itertuples(index=False):
        a_start = game_a.Anwurf - buffer
        a_end = game_a.Anwurf + duration + buffer
        for game_b in b.itertuples(index=False):
            b_start = game_b.Anwurf - buffer
            b_end = game_b.Anwurf + duration + buffer
            if a_start < b_end and b_start < a_end:
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
    ]
    return pd.DataFrame(results, columns=columns).sort_values(
        ["Anwurf A", "Anwurf B"], ignore_index=True
    )


def _meeting(row: object) -> str:
    return f"{row.Heimmannschaft} – {row.Gastmannschaft}"
