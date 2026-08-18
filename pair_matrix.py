from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

import pandas as pd

from pair_store import pair_row_key
from priorities import PRIORITY_LEVELS


TEAM_CATEGORY_ORDER = (
    "Erwachsene",
    "Weibliche Jugend",
    "Männliche Jugend",
    "Kinder",
)


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


def team_category(label: str) -> str:
    """Return the display group used on both matrix axes."""
    concise = matrix_team_label(label)
    if re.search(r"\b(?:Herren|Damen)\b", concise, flags=re.IGNORECASE):
        return "Erwachsene"
    if re.search(r"\bw[ABC]\b", concise):
        return "Weibliche Jugend"
    if re.search(r"\bm[ABC]\b", concise):
        return "Männliche Jugend"
    return "Kinder"


def sort_pair_labels(labels: Sequence[str]) -> list[str]:
    """Sort teams by age group and then by their handball age class."""

    def sort_key(label: str) -> tuple[int, int, str]:
        concise = matrix_team_label(label)
        category = team_category(label)
        category_rank = TEAM_CATEGORY_ORDER.index(category)
        if category == "Erwachsene":
            age_rank = 0 if re.search(r"\bHerren\b", concise) else 1
        elif category == "Weibliche Jugend":
            age_rank = next(
                index
                for index, age in enumerate(("wA", "wB", "wC"))
                if re.search(rf"\b{age}\b", concise)
            )
        elif category == "Männliche Jugend":
            age_rank = next(
                index
                for index, age in enumerate(("mA", "mB", "mC"))
                if re.search(rf"\b{age}\b", concise)
            )
        else:
            child_markers = (
                r"\bwD\b",
                r"\bmD\b",
                r"\bE-Jugend\b",
                r"\bMinis\b",
            )
            age_rank = next(
                (
                    index
                    for index, marker in enumerate(child_markers)
                    if re.search(marker, concise)
                ),
                len(child_markers),
            )
        return category_rank, age_rank, concise.casefold()

    return sorted(labels, key=sort_key)


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
