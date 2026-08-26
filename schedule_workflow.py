from __future__ import annotations

from datetime import date, datetime, time
from typing import Iterable

import pandas as pd

from analysis import ISSUE_COLUMNS, Team, games_for_team


OVERVIEW_COLUMNS = [
    "Spielschlüssel",
    "Anwurf",
    "Mannschaft",
    "Gegner",
    "Heim/Auswärts",
    "Wettbewerb",
    "Spielnummer",
    "Halle",
]

CHANGE_COLUMNS = [
    "Status",
    "Spielnummer",
    "Wettbewerb",
    "Spiel",
    "Änderung",
    "Vorher",
    "Aktuell",
]


def relevant_schedule_overview(
    frame: pd.DataFrame, teams: Iterable[Team]
) -> pd.DataFrame:
    """Return each relevant game once in a compact, selectable overview."""
    team_list = list(teams)
    relevant_indexes: set[object] = set()
    for team in team_list:
        relevant_indexes.update(games_for_team(frame, team).index)

    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for index in sorted(relevant_indexes):
        game = frame.loc[index]
        key = schedule_game_key(game)
        if key in seen:
            continue
        seen.add(key)
        matching_team = next(
            (team for team in team_list if _game_belongs_to_team(game, team)),
            None,
        )
        if matching_team is None:
            continue
        is_home = str(game["Heimmannschaft"]) == matching_team.club_name
        rows.append(
            {
                "Spielschlüssel": key,
                "Anwurf": pd.Timestamp(game["Anwurf"]),
                "Mannschaft": matching_team.label,
                "Gegner": (
                    str(game["Gastmannschaft"])
                    if is_home
                    else str(game["Heimmannschaft"])
                ),
                "Heim/Auswärts": "Heim" if is_home else "Auswärts",
                "Wettbewerb": _competition(game),
                "Spielnummer": str(game.get("Spielnummer", "")).strip(),
                "Halle": _hall(game),
            }
        )

    return pd.DataFrame(rows, columns=OVERVIEW_COLUMNS).sort_values(
        ["Anwurf", "Mannschaft"], ignore_index=True
    )


def reschedule_game(
    frame: pd.DataFrame,
    game_key: str,
    proposed_date: date,
    proposed_time: time,
) -> pd.DataFrame:
    """Move exactly one game in memory while keeping teams and hall unchanged."""
    result = frame.copy()
    matches = result.apply(schedule_game_key, axis=1).eq(game_key)
    if matches.sum() != 1:
        raise ValueError("Das ausgewählte Spiel ist im Spielplan nicht eindeutig.")

    kickoff = pd.Timestamp(datetime.combine(proposed_date, proposed_time))
    result.loc[matches, "Anwurf"] = kickoff
    result.loc[matches, "Datum"] = kickoff.strftime("%d.%m.%Y")
    result.loc[matches, "Zeit"] = kickoff.strftime("%H:%M")
    if "Tag" in result.columns:
        result.loc[matches, "Tag"] = kickoff.strftime("%a.")
    return result


def compare_schedules(
    current: pd.DataFrame,
    previous: pd.DataFrame,
    teams: Iterable[Team],
) -> pd.DataFrame:
    """Summarize added, removed and operationally changed relevant games."""
    team_list = list(teams)
    current_games = _relevant_games_by_key(current, team_list)
    previous_games = _relevant_games_by_key(previous, team_list)
    rows: list[dict[str, object]] = []

    for key in sorted(set(current_games) | set(previous_games)):
        current_game = current_games.get(key)
        previous_game = previous_games.get(key)
        reference = current_game if current_game is not None else previous_game
        assert reference is not None

        if previous_game is None:
            rows.append(_change_row("Hinzugefügt", reference, "Neues Spiel", None, current_game))
            continue
        if current_game is None:
            rows.append(_change_row("Entfernt", reference, "Spiel entfernt", previous_game, None))
            continue

        changed_fields = _changed_fields(previous_game, current_game)
        if changed_fields:
            rows.append(
                _change_row(
                    "Geändert",
                    reference,
                    ", ".join(changed_fields),
                    previous_game,
                    current_game,
                )
            )

    result = pd.DataFrame(rows, columns=CHANGE_COLUMNS)
    if result.empty:
        return result
    status_order = {"Geändert": 0, "Hinzugefügt": 1, "Entfernt": 2}
    result["_Status"] = result["Status"].map(status_order)
    return result.sort_values(
        ["_Status", "Wettbewerb", "Spielnummer"], ignore_index=True
    ).drop(columns="_Status")


def compare_findings(
    before: pd.DataFrame, after: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return newly introduced and resolved findings without duplicates."""
    before_rows = {_finding_key(row) for _, row in before.iterrows()}
    after_rows = {_finding_key(row) for _, row in after.iterrows()}
    new_rows = [row for _, row in after.iterrows() if _finding_key(row) not in before_rows]
    resolved_rows = [
        row for _, row in before.iterrows() if _finding_key(row) not in after_rows
    ]
    return (
        pd.DataFrame(new_rows, columns=ISSUE_COLUMNS).reset_index(drop=True),
        pd.DataFrame(resolved_rows, columns=ISSUE_COLUMNS).reset_index(drop=True),
    )


def schedule_game_key(game: pd.Series) -> str:
    game_number = str(game.get("Spielnummer", "")).strip()
    competition = (
        str(game.get("Liga", "")).strip(),
        str(game.get("Staffelkurzbezeichnung", "")).strip(),
    )
    if game_number and game_number != "0":
        parts = (*competition, game_number)
    else:
        parts = (
            *competition,
            str(game.get("Runde", "")).strip(),
            str(game.get("Heimmannschaft", "")).strip(),
            str(game.get("Gastmannschaft", "")).strip(),
        )
    return "\x1f".join(parts)


def _relevant_games_by_key(
    frame: pd.DataFrame, teams: list[Team]
) -> dict[str, pd.Series]:
    indexes: set[object] = set()
    for team in teams:
        indexes.update(games_for_team(frame, team).index)
    return {schedule_game_key(frame.loc[index]): frame.loc[index] for index in indexes}


def _game_belongs_to_team(game: pd.Series, team: Team) -> bool:
    return bool(
        str(game.get("Liga", "")) == team.liga
        and str(game.get("Staffelkurzbezeichnung", "")) == team.staffel
        and team.club_name
        in {
            str(game.get("Heimmannschaft", "")),
            str(game.get("Gastmannschaft", "")),
        }
    )


def _competition(game: pd.Series) -> str:
    values = [
        str(game.get("Liga", "")).strip(),
        str(game.get("Staffelkurzbezeichnung", "")).strip(),
    ]
    return " / ".join(value for value in values if value)


def _hall(game: pd.Series) -> str:
    return str(
        game.get("Inhalt Tooltip Halle", "") or game.get("Hallennummer", "")
    ).strip()


def _changed_fields(previous: pd.Series, current: pd.Series) -> list[str]:
    fields = []
    comparisons = (
        ("Anwurf", pd.Timestamp(previous["Anwurf"]), pd.Timestamp(current["Anwurf"])),
        (
            "Halle",
            _hall_identity(previous),
            _hall_identity(current),
        ),
        (
            "Heimmannschaft",
            str(previous.get("Heimmannschaft", "")),
            str(current.get("Heimmannschaft", "")),
        ),
        (
            "Gastmannschaft",
            str(previous.get("Gastmannschaft", "")),
            str(current.get("Gastmannschaft", "")),
        ),
    )
    for label, old_value, new_value in comparisons:
        if old_value != new_value:
            fields.append(label)
    return fields


def _change_row(
    status: str,
    reference: pd.Series,
    change: str,
    previous: pd.Series | None,
    current: pd.Series | None,
) -> dict[str, object]:
    return {
        "Status": status,
        "Spielnummer": str(reference.get("Spielnummer", "")).strip(),
        "Wettbewerb": _competition(reference),
        "Spiel": (
            f"{reference.get('Heimmannschaft', '')} - "
            f"{reference.get('Gastmannschaft', '')}"
        ),
        "Änderung": change,
        "Vorher": _schedule_value(previous),
        "Aktuell": _schedule_value(current),
    }


def _schedule_value(game: pd.Series | None) -> str:
    if game is None:
        return ""
    kickoff = pd.Timestamp(game["Anwurf"])
    hall = _hall_identity(game)
    return f"{kickoff:%d.%m.%Y, %H:%M Uhr}" + (f" | {hall}" if hall else "")


def _hall_identity(game: pd.Series) -> str:
    number = str(game.get("Hallennummer", "")).strip()
    name = _hall(game)
    if number and name and number != name:
        return f"{name} ({number})"
    return name or number


def _finding_key(row: pd.Series) -> tuple[str, ...]:
    values = []
    for column in ISSUE_COLUMNS:
        value = row.get(column, "")
        if isinstance(value, (date, datetime, pd.Timestamp)):
            values.append(pd.Timestamp(value).isoformat())
        else:
            values.append(str(value))
    return tuple(values)
