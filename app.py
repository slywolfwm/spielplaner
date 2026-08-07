from pathlib import Path

import pandas as pd
import streamlit as st

from analysis import available_teams, find_overlaps, load_schedule


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = APP_DIR / "Regionsspielplan_Bayern_2026-27.csv"

st.set_page_config(page_title="Spielplaner", page_icon="🤾", layout="wide")
st.title("Spielplaner")
st.caption("TSV Weilheim und weibliche A-Jugend des BSC Oberhausen · Saison 2026/27")

uploaded = st.file_uploader("Optional: aktualisierten nuLiga-Gesamtspielplan laden", type="csv")
source = uploaded if uploaded is not None else DEFAULT_CSV

try:
    schedule = load_schedule(source)
except Exception as exc:
    st.error(f"Der Spielplan konnte nicht geladen werden: {exc}")
    st.stop()

teams = available_teams(schedule)
team_by_label = {team.label: team for team in teams}
labels = list(team_by_label)

with st.sidebar:
    st.header("Prüfregeln")
    game_minutes = st.number_input(
        "Angenommene Spieldauer (Minuten)", min_value=30, max_value=240, value=120, step=5
    )
    buffer_minutes = st.number_input(
        "Puffer vor und nach jedem Spiel (Minuten)",
        min_value=0,
        max_value=360,
        value=0,
        step=5,
    )
    min_date = schedule["Anwurf"].min().date()
    max_date = schedule["Anwurf"].max().date()
    date_range = st.date_input(
        "Zeitraum", value=(min_date, max_date), min_value=min_date, max_value=max_date
    )

if len(date_range) == 2:
    start_date, end_date = date_range
    schedule = schedule.loc[
        schedule["Anwurf"].dt.date.between(start_date, end_date)
    ].copy()

st.subheader("Mannschaftspaare")
pair_count = st.number_input(
    "Anzahl der zu prüfenden Paare", min_value=1, max_value=20, value=1, step=1
)

pairs: list[tuple[str, str]] = []
for index in range(int(pair_count)):
    col_a, col_b = st.columns(2)
    default_b = min(index + 1, max(len(labels) - 1, 0))
    with col_a:
        label_a = st.selectbox(
            f"Paar {index + 1} · Mannschaft A", labels, key=f"team_a_{index}"
        )
    with col_b:
        label_b = st.selectbox(
            f"Paar {index + 1} · Mannschaft B",
            labels,
            index=default_b,
            key=f"team_b_{index}",
        )
    pairs.append((label_a, label_b))

if st.button("Überschneidungen prüfen", type="primary", width="stretch"):
    all_results = []
    duplicate_pairs = set()
    seen_pairs = set()

    for label_a, label_b in pairs:
        pair_key = tuple(sorted((label_a, label_b)))
        if label_a == label_b:
            st.warning(f"Übersprungen: {label_a} wurde mit sich selbst kombiniert.")
            continue
        if pair_key in seen_pairs:
            duplicate_pairs.add(pair_key)
            continue
        seen_pairs.add(pair_key)
        result = find_overlaps(
            schedule,
            team_by_label[label_a],
            team_by_label[label_b],
            int(game_minutes),
            int(buffer_minutes),
        )
        if not result.empty:
            result.insert(0, "Paar", f"{label_a} ↔ {label_b}")
            all_results.append(result)

    if duplicate_pairs:
        st.info("Doppelt angelegte Mannschaftspaare wurden nur einmal geprüft.")

    if not all_results:
        st.success("Für die ausgewählten Paare wurden keine Überschneidungen gefunden.")
    else:
        result = pd.concat(all_results, ignore_index=True)
        st.error(f"{len(result)} Überschneidung(en) gefunden.")
        st.dataframe(
            result,
            width="stretch",
            hide_index=True,
            column_config={
                "Anwurf A": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
                "Anwurf B": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
                "Datum": st.column_config.DateColumn(format="DD.MM.YYYY"),
            },
        )
        st.download_button(
            "Ergebnis als CSV herunterladen",
            result.to_csv(index=False, sep=";").encode("utf-8-sig"),
            "spielplan_ueberschneidungen.csv",
            "text/csv",
        )

with st.expander("Enthaltene Mannschaften"):
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Mannschaft": team.label,
                    "Spiele": len(
                        schedule.loc[
                            (
                                schedule["Heimmannschaft"].eq(team.club_name)
                                | schedule["Gastmannschaft"].eq(team.club_name)
                            )
                            & schedule["Liga"].eq(team.liga)
                            & schedule["Staffelkurzbezeichnung"].eq(team.staffel)
                        ]
                    ),
                }
                for team in teams
            ]
        ),
        hide_index=True,
        width="stretch",
    )
