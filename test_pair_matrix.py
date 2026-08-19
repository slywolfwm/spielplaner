import pandas as pd

from pair_matrix import (
    build_pair_matrix,
    selected_pairs_from_matrix,
    sort_pair_labels,
    team_category,
)
from pair_store import pair_row_key


def test_pair_matrix_contains_each_pair_only_in_upper_triangle():
    labels = ["TSV Weilheim – Herren", "TSV Weilheim – Damen", "BSC Oberhausen – wA"]
    active = {pair_row_key(labels[0], labels[2]): "Mittel"}

    matrix, column_teams = build_pair_matrix(labels, active)

    assert matrix["Mannschaft"].tolist() == ["Herren", "Damen", "BSC wA"]
    assert matrix.loc[0, "BSC wA"] == "Mittel"
    assert pd.isna(matrix.loc[2, "Herren"])
    assert pd.isna(matrix.loc[1, "Damen"])


def test_pair_matrix_ignores_diagonal_and_reverse_direction():
    labels = ["TSV Weilheim – Herren", "TSV Weilheim – Damen"]
    matrix, column_teams = build_pair_matrix(labels, {})
    matrix.loc[0, "Damen"] = "Niedrig"
    matrix.loc[1, "Herren"] = "Hoch"
    matrix.loc[1, "Damen"] = "Mittel"

    pairs = selected_pairs_from_matrix(matrix, labels, column_teams)

    assert pairs == [(labels[0], labels[1], "Niedrig")]


def test_pair_matrix_makes_duplicate_axis_labels_unique():
    labels = ["Verein A – Team", "Verein A – Team (Liga)"]

    _, column_teams = build_pair_matrix(labels, {})

    assert list(column_teams) == ["Verein A – Team", "Verein A – Team 2"]


def test_pair_matrix_sorts_adults_then_female_male_and_children():
    labels = [
        "TSV Weilheim – Minis",
        "TSV Weilheim – BOL mC S (BOL mC)",
        "TSV Weilheim – Damen II",
        "TSV Weilheim – BOL wC S2 (BOL wC)",
        "TSV Weilheim – Herren",
        "BSC Oberhausen – BOL wA SW (BOL wA)",
        "TSV Weilheim – BOL mA SW (BOL mA)",
        "TSV Weilheim – wD",
        "TSV Weilheim – mD",
    ]

    sorted_labels = sort_pair_labels(labels)

    assert sorted_labels == [
        "TSV Weilheim – Herren",
        "TSV Weilheim – Damen II",
        "BSC Oberhausen – BOL wA SW (BOL wA)",
        "TSV Weilheim – BOL wC S2 (BOL wC)",
        "TSV Weilheim – wD",
        "TSV Weilheim – BOL mA SW (BOL mA)",
        "TSV Weilheim – BOL mC S (BOL mC)",
        "TSV Weilheim – mD",
        "TSV Weilheim – Minis",
    ]
    assert [team_category(label) for label in sorted_labels] == [
        "Erwachsene",
        "Erwachsene",
        "Weibliche Jugend",
        "Weibliche Jugend",
        "Weibliche Jugend",
        "Männliche Jugend",
        "Männliche Jugend",
        "Männliche Jugend",
        "Kinder",
    ]
