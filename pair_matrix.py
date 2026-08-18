from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from pair_store import pair_row_key
from priorities import PRIORITY_LEVELS


def matrix_team_label(label: str) -> str:
    """Return a compact, unique-friendly label for a matrix axis."""
    concise = label.rsplit(" (", 1)[0]
    prefixes = (
        ("TSV Weilheim II – ", "II "),
        ("TSV Weilheim – ", ""),
        ("BSC Oberhausen – ", "BSC "),
    )
    for prefix, replacement in prefixes:
        if concise.startswith(prefix):
            return f"{replacement}{concise.removeprefix(prefix)}"
    return concise


def build_pair_matrix(
    labels: Sequence[str], active_by_key: Mapping[str, str]
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Build an upper-triangular team matrix and its column-to-team mapping."""
    column_teams: dict[str, str] = {}
    used_headings: set[str] = set()
    for label in labels:
        base = matrix_team_label(label)
        heading = base
        number = 2
        while heading in used_headings:
            heading = f"{base} {number}"
            number += 1
        used_headings.add(heading)
        column_teams[heading] = label

    rows: list[dict[str, object]] = []
    headings = list(column_teams)
    for row_index, label_a in enumerate(labels):
        row: dict[str, object] = {"Mannschaft": headings[row_index]}
        for column_index, heading in enumerate(headings):
            if column_index <= row_index:
                row[heading] = None
                continue
            row_key = pair_row_key(label_a, labels[column_index])
            row[heading] = active_by_key.get(row_key, "")
        rows.append(row)
    return pd.DataFrame(rows), column_teams


def selected_pairs_from_matrix(
    matrix: pd.DataFrame,
    labels: Sequence[str],
    column_teams: Mapping[str, str],
) -> list[tuple[str, str, str]]:
    """Read only the upper triangle so a pair can never occur twice."""
    headings = list(column_teams)
    selected: list[tuple[str, str, str]] = []
    for row_index, label_a in enumerate(labels):
        for column_index in range(row_index + 1, len(headings)):
            priority = matrix.iloc[row_index].get(headings[column_index])
            if priority not in PRIORITY_LEVELS:
                continue
            selected.append((label_a, labels[column_index], str(priority)))
    return selected
