import os
from pathlib import Path

import pandas as pd
import streamlit as st

from access_control import UserAccess, parse_client_principal, parse_oidc_user
from analysis import (
    RULE_HOME_BUFFER,
    RULE_PRIORITIES,
    RULE_TEAM_OVERLAP,
    Team,
    analyze_schedule,
    available_teams,
    default_game_duration,
    default_stoppage_buffer,
    games_for_team,
    load_schedule,
)
from duration_store import DurationStore
from pair_store import PairStore, StoredPair


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


def load_saved_pairs() -> tuple[PairStore | None, list[StoredPair], str]:
    if not access.can_view_pairings or not connection_string:
        return None, [], ""

    try:
        pair_store = create_pair_store(
            connection_string,
            configured_value("AZURE_TABLE_NAME", "teampairs"),
        )
        return pair_store, pair_store.list_pairs(SEASON), ""
    except Exception as exc:
        return None, [], str(exc)


def pairs_for_analysis(
    saved_pairs: list[StoredPair],
) -> tuple[list[tuple[Team, Team]], int]:
    raw_pairs = list(st.session_state.get("manual_pairs", []))
    raw_pairs.extend((pair.team_a, pair.team_b) for pair in saved_pairs)
    result: list[tuple[Team, Team]] = []
    seen: set[tuple[str, str]] = set()
    skipped = 0

    for label_a, label_b in raw_pairs:
        if (
            label_a == label_b
            or label_a not in team_by_label
            or label_b not in team_by_label
        ):
            skipped += 1
            continue
        key = tuple(sorted((label_a, label_b), key=str.casefold))
        if key in seen:
            continue
        seen.add(key)
        result.append((team_by_label[label_a], team_by_label[label_b]))
    return result, skipped


def show_analysis_page() -> None:
    st.header("Spielplanprüfung")
    st.caption(
        "Alle aktiven Regeln werden auf den vollständigen Spielplan der enthaltenen "
        "Mannschaften angewendet. Jeder gefundene Sachverhalt erscheint genau einmal."
    )

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Priorität": RULE_PRIORITIES[RULE_TEAM_OVERLAP],
                    "Regel": RULE_TEAM_OVERLAP,
                    "Prüfumfang": "Alle definierten Mannschaftspaare",
                },
                {
                    "Priorität": RULE_PRIORITIES[RULE_HOME_BUFFER],
                    "Regel": RULE_HOME_BUFFER,
                    "Prüfumfang": (
                        f"Alle Heimspiele; {PRE_GAME_BUFFER_MINUTES} Min. Vorlauf"
                    ),
                },
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption("Hallenbuchungen werden in einer späteren Ausbaustufe geprüft.")

    _, saved_pairs, pair_error = load_saved_pairs()
    if pair_error:
        st.warning(
            "Gespeicherte Mannschaftspaare konnten nicht geladen werden. "
            "Manuell festgelegte Paare werden weiterhin geprüft."
        )
    analysis_pairs, skipped_pairs = pairs_for_analysis(saved_pairs)
    if not analysis_pairs:
        st.info(
            "Es ist noch kein gültiges Mannschaftspaar definiert. Die Pufferregel "
            "wird trotzdem vollständig geprüft."
        )
    elif skipped_pairs:
        st.caption(
            f"{skipped_pairs} ungültige Paarung(en) werden bei der Prüfung übersprungen."
        )

    if st.button("Gesamten Spielplan prüfen", type="primary", width="stretch"):
        findings = analyze_schedule(
            schedule,
            teams,
            current_duration_map(),
            analysis_pairs,
            PRE_GAME_BUFFER_MINUTES,
        )
        if findings.empty:
            st.success("Die aktiven Regeln haben keine Auffälligkeiten gefunden.")
        else:
            st.error(f"{len(findings)} Auffälligkeit(en) gefunden.")
            st.dataframe(
                findings,
                hide_index=True,
                width="stretch",
                column_config={
                    "Priorität": st.column_config.TextColumn(width="small"),
                    "Regel": st.column_config.TextColumn(width="medium"),
                    "Datum": st.column_config.DateColumn(
                        format="DD.MM.YYYY", width="small"
                    ),
                    "Spiele": st.column_config.TextColumn(width="large"),
                    "Kommentar": st.column_config.TextColumn(width="large"),
                },
            )
            st.download_button(
                "Kommentierte Prüfung als CSV herunterladen",
                findings.to_csv(index=False, sep=";").encode("utf-8-sig"),
                "spielplan_pruefung.csv",
                "text/csv",
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
        "überschneiden dürfen. Die Spielplanprüfung berücksichtigt automatisch "
        "alle gültigen manuellen und für dich sichtbaren gespeicherten Paarungen."
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

    st.session_state["manual_pairs"] = pairs
    valid_manual_pairs = [pair for pair in pairs if pair[0] != pair[1]]
    if len(valid_manual_pairs) != len(pairs):
        st.warning("Paarungen einer Mannschaft mit sich selbst werden nicht geprüft.")
    else:
        st.success(
            "Die aktuelle Auswahl wird bei der nächsten Spielplanprüfung verwendet."
        )

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
            pair_store, stored_pairs, pair_error = load_saved_pairs()
            if pair_error:
                st.error(
                    "Gespeicherte Paarungen konnten nicht geladen werden: "
                    f"{pair_error}"
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
                else:
                    st.info("Es wurden noch keine Paarungen gespeichert.")

                if access.can_edit_pairings:
                    st.divider()
                    if st.button("Aktuelle Auswahl speichern", width="stretch"):
                        if not valid_manual_pairs:
                            st.warning(
                                "Wähle mindestens zwei unterschiedliche "
                                "Mannschaften aus."
                            )
                        else:
                            saved = pair_store.save_pairs(
                                SEASON,
                                valid_manual_pairs,
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


st.set_page_config(page_title="Spielplaner", page_icon="🤾", layout="wide")
page = st.navigation(
    [
        st.Page(
            show_analysis_page,
            title="Spielplanprüfung",
            icon="✅",
            url_path="spielplanpruefung",
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
