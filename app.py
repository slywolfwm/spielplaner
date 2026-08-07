import os
from pathlib import Path

import pandas as pd
import streamlit as st

from access_control import UserAccess, parse_client_principal, parse_oidc_user
from analysis import (
    Team,
    available_teams,
    default_game_duration,
    default_stoppage_buffer,
    find_home_game_buffer_conflicts,
    find_overlaps,
    games_for_team,
    home_game_blocks,
    load_schedule,
)
from duration_store import DurationStore
from pair_store import PairStore


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = APP_DIR / "Regionsspielplan_Bayern_2026-27.csv"
DEFAULT_DISTRICT_CSV = APP_DIR / "Vereinsspielplan_Alpenvorland_2026-27.csv"
SEASON = "2026-27"
PRE_GAME_BUFFER_MINUTES = 30


def configured_value(name: str, default: str = "") -> str:
    environment_value = os.environ.get(name)
    if environment_value:
        return environment_value

    try:
        secret_value = st.secrets.get(name, default)
    except FileNotFoundError:
        return default
    return str(secret_value) if secret_value else default


def oidc_auth_is_configured() -> bool:
    try:
        auth = st.secrets.get("auth", {})
        return bool(auth.get("microsoft"))
    except (FileNotFoundError, TypeError):
        return False


def current_user_access() -> tuple[UserAccess, bool]:
    app_service_access = parse_client_principal(
        st.context.headers.get("X-MS-CLIENT-PRINCIPAL")
    )
    if app_service_access.authenticated:
        return app_service_access, False

    oidc_access = parse_oidc_user(st.user.to_dict())
    return oidc_access, oidc_access.authenticated


def show_login_action(key: str) -> None:
    if oidc_configured:
        st.button(
            "Mit Microsoft anmelden",
            key=key,
            on_click=st.login,
            args=("microsoft",),
        )
    elif os.environ.get("WEBSITE_HOSTNAME"):
        st.markdown(
            "[Mit Microsoft anmelden](/.auth/login/aad?post_login_redirect_uri=/)"
        )
    else:
        st.caption("Die Microsoft-Anmeldung ist lokal nicht konfiguriert.")


@st.cache_resource(show_spinner=False)
def create_pair_store(connection_string: str, table_name: str) -> PairStore:
    return PairStore.from_connection_string(connection_string, table_name)


@st.cache_resource(show_spinner=False)
def create_duration_store(connection_string: str, table_name: str) -> DurationStore:
    return DurationStore.from_connection_string(connection_string, table_name)


def show_overlap_results(
    pairs_to_check: list[tuple[str, str]],
    schedule: pd.DataFrame,
    team_by_label: dict[str, Team],
    duration_by_team_key: dict[str, int],
) -> None:
    all_results = []
    duplicate_pairs = set()
    seen_pairs = set()

    for label_a, label_b in pairs_to_check:
        pair_key = tuple(sorted((label_a, label_b)))
        if label_a == label_b:
            st.warning(f"Übersprungen: {label_a} wurde mit sich selbst kombiniert.")
            continue
        if label_a not in team_by_label or label_b not in team_by_label:
            st.warning(
                f"Übersprungen: {label_a} ↔ {label_b} ist im aktuellen "
                "Spielplan nicht vollständig enthalten."
            )
            continue
        if pair_key in seen_pairs:
            duplicate_pairs.add(pair_key)
            continue
        seen_pairs.add(pair_key)
        team_a = team_by_label[label_a]
        team_b = team_by_label[label_b]
        result = find_overlaps(
            schedule,
            team_a,
            team_b,
            duration_by_team_key[team_a.key],
            duration_by_team_key[team_b.key],
            PRE_GAME_BUFFER_MINUTES,
        )
        if not result.empty:
            result.insert(0, "Paar", f"{label_a} ↔ {label_b}")
            all_results.append(result)

    if duplicate_pairs:
        st.info("Doppelt angelegte Mannschaftspaare wurden nur einmal geprüft.")

    if not all_results:
        st.success("Für die ausgewählten Paare wurden keine Überschneidungen gefunden.")
        return

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


def initialize_duration_settings() -> dict[str, dict[str, int]]:
    settings = st.session_state.setdefault("duration_settings", {})
    for team in teams:
        if team.key in settings:
            continue
        stored = stored_durations.get(team.key)
        settings[team.key] = {
            "base_minutes": stored.minutes if stored else default_game_duration(team),
            "extra_minutes": (
                stored.extra_minutes
                if stored and stored.extra_minutes is not None
                else default_stoppage_buffer(team)
            ),
        }
    return settings


def current_duration_map() -> dict[str, int]:
    return {
        team.key: duration_settings[team.key]["base_minutes"]
        + duration_settings[team.key]["extra_minutes"]
        for team in teams
    }


def duration_source(team: Team) -> str:
    current = duration_settings[team.key]
    stored = stored_durations.get(team.key)
    if stored:
        stored_extra = (
            stored.extra_minutes
            if stored.extra_minutes is not None
            else default_stoppage_buffer(team)
        )
        if current == {
            "base_minutes": stored.minutes,
            "extra_minutes": stored_extra,
        }:
            return "Gespeichert"
    if current == {
        "base_minutes": default_game_duration(team),
        "extra_minutes": default_stoppage_buffer(team),
    }:
        return "Standardwert"
    return "Sitzung"


def show_hall_page() -> None:
    st.header("Heimspiel-Puffer")
    st.caption(
        "Geprüft werden alle Heimspiele der enthaltenen Mannschaften in derselben "
        f"Halle. Die Vorbereitung beginnt {PRE_GAME_BUFFER_MINUTES} Minuten vor Anwurf."
    )

    if st.button("Heimspiel-Puffer prüfen", type="primary", width="stretch"):
        occupancy = home_game_blocks(
            schedule,
            teams,
            current_duration_map(),
            PRE_GAME_BUFFER_MINUTES,
        )
        conflicts = find_home_game_buffer_conflicts(
            occupancy, PRE_GAME_BUFFER_MINUTES
        )
        if conflicts.empty:
            st.success("Zwischen den Heimspielen ist der benötigte Puffer eingeplant.")
        else:
            st.error(f"{len(conflicts)} zu knappe Hallenabfolge(n) gefunden.")
            st.dataframe(
                conflicts,
                hide_index=True,
                width="stretch",
                column_config={
                    "Datum": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Spielende": st.column_config.DatetimeColumn(
                        format="DD.MM.YYYY HH:mm"
                    ),
                    "Anwurf nächstes Spiel": st.column_config.DatetimeColumn(
                        format="DD.MM.YYYY HH:mm"
                    ),
                    "Vorbereitung ab": st.column_config.DatetimeColumn(
                        format="DD.MM.YYYY HH:mm"
                    ),
                },
            )

        with st.expander("Berechnete Hallenbelegung"):
            st.dataframe(
                occupancy.drop(columns=["Hallenschlüssel"]),
                hide_index=True,
                width="stretch",
                column_config={
                    "Datum": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Anwurf": st.column_config.DatetimeColumn(
                        format="DD.MM.YYYY HH:mm"
                    ),
                    "Vorbereitung ab": st.column_config.DatetimeColumn(
                        format="DD.MM.YYYY HH:mm"
                    ),
                    "Spielende": st.column_config.DatetimeColumn(
                        format="DD.MM.YYYY HH:mm"
                    ),
                },
            )

    with st.expander("Enthaltene Mannschaften"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Mannschaft": team.label,
                        "Spiele": len(games_for_team(schedule, team)),
                    }
                    for team in teams
                ]
            ),
            hide_index=True,
            width="stretch",
        )


def show_duration_page() -> None:
    st.header("Spieldauern")
    st.caption(
        "Die Regeldauer umfasst beide Halbzeiten und die Halbzeitpause. Der separat "
        "änderbare Unterbrechungspuffer berücksichtigt Time-outs und sonstige Stopps. "
        f"Vor jedem Anwurf werden außerdem {PRE_GAME_BUFFER_MINUTES} Minuten Vorlauf "
        "eingeplant."
    )

    duration_notice = st.session_state.pop("duration_notice", "")
    if duration_notice:
        st.success(duration_notice)
    if duration_storage_error:
        st.warning(
            "Gespeicherte Zeitwerte konnten nicht geladen werden. Es werden die "
            "Standardwerte verwendet."
        )

    rows = [
        {
            "Mannschaft": team.label,
            "Regeldauer inkl. Halbzeit (Min.)": duration_settings[team.key][
                "base_minutes"
            ],
            "Unterbrechungspuffer (Min.)": duration_settings[team.key][
                "extra_minutes"
            ],
            "Quelle": duration_source(team),
        }
        for team in teams
    ]
    duration_frame = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        disabled=["Mannschaft", "Quelle"],
        column_config={
            "Regeldauer inkl. Halbzeit (Min.)": st.column_config.NumberColumn(
                min_value=10, max_value=180, step=1, required=True
            ),
            "Unterbrechungspuffer (Min.)": st.column_config.NumberColumn(
                min_value=0, max_value=60, step=1, required=True
            ),
        },
        key="duration_editor",
    )

    for _, row in duration_frame.iterrows():
        team = team_by_label[str(row["Mannschaft"])]
        duration_settings[team.key] = {
            "base_minutes": int(row["Regeldauer inkl. Halbzeit (Min.)"]),
            "extra_minutes": int(row["Unterbrechungspuffer (Min.)"]),
        }

    st.caption(
        "Für die Prüfungen wird die Summe aus Regeldauer und "
        "Unterbrechungspuffer verwendet."
    )

    if access.can_edit_pairings:
        if st.button(
            "Zeitwerte dauerhaft speichern",
            disabled=duration_store is None,
            width="stretch",
        ):
            saved = duration_store.save_durations(
                SEASON,
                [
                    (
                        team.key,
                        team.label,
                        duration_settings[team.key]["base_minutes"],
                        duration_settings[team.key]["extra_minutes"],
                    )
                    for team in teams
                ],
                access.object_id or access.display_name,
            )
            st.session_state["duration_notice"] = (
                f"Zeitwerte für {saved} Mannschaft(en) wurden gespeichert."
            )
            st.rerun()
        if duration_store is None:
            st.caption(
                "Dauerhaftes Speichern ist erst nach der "
                "Azure-Storage-Konfiguration möglich."
            )
    else:
        st.caption(
            "Änderungen wirken sofort in dieser Sitzung. Dauerhaft speichern können "
            "angemeldete Benutzer mit Bearbeiterrolle."
        )
        if not access.authenticated:
            show_login_action("duration_login")


def show_pair_page() -> None:
    st.header("Mannschaftspaare")
    st.caption(
        "Lege Mannschaftspaare fest, deren Belegungszeiten sich nicht "
        "überschneiden dürfen."
    )

    pair_count = st.number_input(
        "Anzahl der zu prüfenden Paare", min_value=1, max_value=20, value=1, step=1
    )
    pairs: list[tuple[str, str]] = []
    for index in range(int(pair_count)):
        col_a, col_b = st.columns(2)
        default_b = min(index + 1, max(len(labels) - 1, 0))
        with col_a:
            label_a = st.selectbox(
                f"Paar {index + 1} · Mannschaft A",
                labels,
                key=f"team_a_{index}",
            )
        with col_b:
            label_b = st.selectbox(
                f"Paar {index + 1} · Mannschaft B",
                labels,
                index=default_b,
                key=f"team_b_{index}",
            )
        pairs.append((label_a, label_b))

    saved_check_requested = False
    saved_pairs_to_check: list[tuple[str, str]] = []

    with st.expander("Gespeicherte Paarungen", expanded=access.can_view_pairings):
        if not access.authenticated:
            st.info("Melde dich an, um freigegebene Paarungen zu sehen.")
            show_login_action("pairing_login")
        elif not access.can_view_pairings:
            st.warning(
                "Deinem Benutzer wurde kein Zugriff auf gespeicherte Paarungen "
                "zugewiesen."
            )
        elif not connection_string:
            st.warning("Azure Table Storage ist noch nicht für die App konfiguriert.")
        else:
            try:
                pair_store = create_pair_store(
                    connection_string,
                    configured_value("AZURE_TABLE_NAME", "teampairs"),
                )
                stored_pairs = pair_store.list_pairs(SEASON)
            except Exception as exc:
                st.error(
                    f"Gespeicherte Paarungen konnten nicht geladen werden: {exc}"
                )
            else:
                notice = st.session_state.pop("pairing_notice", "")
                if notice:
                    st.success(notice)

                st.caption(
                    f"Angemeldet als {access.display_name or 'Microsoft-Benutzer'}"
                )
                if using_oidc:
                    st.button("Abmelden", key="pairing_logout", on_click=st.logout)
                if stored_pairs:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "Mannschaft A": pair.team_a,
                                    "Mannschaft B": pair.team_b,
                                }
                                for pair in stored_pairs
                            ]
                        ),
                        hide_index=True,
                        width="stretch",
                    )
                    stored_by_key = {pair.row_key: pair for pair in stored_pairs}
                    selected_keys = st.multiselect(
                        "Für die Prüfung auswählen",
                        options=list(stored_by_key),
                        default=list(stored_by_key),
                        format_func=lambda key: stored_by_key[key].label,
                    )
                    saved_pairs_to_check = [
                        (stored_by_key[key].team_a, stored_by_key[key].team_b)
                        for key in selected_keys
                    ]
                    saved_check_requested = st.button(
                        "Gespeicherte Paarungen prüfen",
                        disabled=not saved_pairs_to_check,
                        width="stretch",
                    )
                else:
                    st.info("Es wurden noch keine Paarungen gespeichert.")

                if access.can_edit_pairings:
                    st.divider()
                    if st.button("Aktuelle Auswahl speichern", width="stretch"):
                        valid_pairs = [pair for pair in pairs if pair[0] != pair[1]]
                        if not valid_pairs:
                            st.warning(
                                "Wähle mindestens zwei unterschiedliche "
                                "Mannschaften aus."
                            )
                        else:
                            saved = pair_store.save_pairs(
                                SEASON,
                                valid_pairs,
                                access.object_id or access.display_name,
                            )
                            st.session_state["pairing_notice"] = (
                                f"{saved} Paarung(en) wurden gespeichert."
                            )
                            st.rerun()

                    if stored_pairs:
                        delete_keys = st.multiselect(
                            "Zu löschende Paarungen",
                            options=list(stored_by_key),
                            format_func=lambda key: stored_by_key[key].label,
                        )
                        if st.button(
                            "Ausgewählte gespeicherte Paarungen löschen",
                            disabled=not delete_keys,
                            width="stretch",
                        ):
                            for row_key in delete_keys:
                                pair_store.delete_pair(SEASON, row_key)
                            st.session_state["pairing_notice"] = (
                                f"{len(delete_keys)} Paarung(en) wurden gelöscht."
                            )
                            st.rerun()

    manual_check_requested = st.button(
        "Überschneidungen prüfen", type="primary", width="stretch"
    )
    if saved_check_requested:
        show_overlap_results(
            saved_pairs_to_check,
            schedule,
            team_by_label,
            current_duration_map(),
        )
    elif manual_check_requested:
        show_overlap_results(
            pairs,
            schedule,
            team_by_label,
            current_duration_map(),
        )


st.set_page_config(page_title="Spielplaner", page_icon="🤾", layout="wide")
page = st.navigation(
    [
        st.Page(
            show_hall_page,
            title="Heimspiel-Puffer",
            icon="🏟️",
            url_path="heimspiel-puffer",
            default=True,
        ),
        st.Page(
            show_duration_page,
            title="Spieldauern",
            icon="⏱️",
            url_path="spieldauern",
        ),
        st.Page(
            show_pair_page,
            title="Mannschaftspaare",
            icon="🔗",
            url_path="mannschaftspaare",
        ),
    ]
)

st.title("Spielplaner")
st.caption("TSV Weilheim und weibliche A-Jugend des BSC Oberhausen · Saison 2026/27")
uploaded = st.file_uploader(
    "Optional: aktualisierten nuLiga-Gesamtspielplan laden", type="csv"
)
source = uploaded if uploaded is not None else DEFAULT_CSV

try:
    schedule_parts = [load_schedule(source)]
    if DEFAULT_DISTRICT_CSV.exists():
        schedule_parts.append(load_schedule(DEFAULT_DISTRICT_CSV))
    schedule = pd.concat(schedule_parts, ignore_index=True).drop_duplicates(
        subset=[
            "Anwurf",
            "Hallennummer",
            "Spielnummer",
            "Heimmannschaft",
            "Gastmannschaft",
        ],
        keep="last",
    )
except Exception as exc:
    st.error(f"Der Spielplan konnte nicht geladen werden: {exc}")
    st.stop()

teams = available_teams(schedule)
team_by_label = {team.label: team for team in teams}
labels = list(team_by_label)
oidc_configured = oidc_auth_is_configured()
access, using_oidc = current_user_access()
connection_string = configured_value("AZURE_STORAGE_CONNECTION_STRING")
duration_store = None
stored_durations = {}
duration_storage_error = ""

if connection_string:
    try:
        duration_store = create_duration_store(
            connection_string,
            configured_value("AZURE_DURATION_TABLE_NAME", "teamdurations"),
        )
        stored_durations = duration_store.list_durations(SEASON)
    except Exception as exc:
        duration_storage_error = str(exc)

duration_settings = initialize_duration_settings()
page.run()
