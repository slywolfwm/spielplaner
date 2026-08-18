import os
from base64 import b64encode
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from access_control import (
    UserAccess,
    parse_client_principal,
    parse_oidc_user,
)
from analysis import (
    HALL_BOOKING_REQUIREMENTS,
    RULE_HOME_BUFFER,
    RULE_HALL_BOOKING,
    RULE_HALL_BOOKING_EXCESS,
    RULE_PRIORITIES,
    RULE_TEAM_OVERLAP,
    RULE_TRAVEL_TIME,
    Team,
    analyze_schedule,
    available_teams,
    default_game_duration,
    default_stoppage_buffer,
    find_relevant_travel_legs,
    games_for_team,
    home_game_blocks,
    load_schedule,
    travel_leg_key,
)
from duration_store import DurationStore
from omoc import OmocClient, OmocError
from pair_matrix import build_pair_matrix, selected_pairs_from_matrix
from pair_store import PairStore, StoredPair, pair_row_key
from priorities import PRIORITY_LEVELS, normalize_priority
from schedule_store import ScheduleStore, StoredSchedule
from travel_time_store import TravelTimeStore
from travel_times import AzureMapsClient, AzureMapsError


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = APP_DIR / "Regionsspielplan_Bayern_2026-27.csv"
DEFAULT_DISTRICT_CSV = APP_DIR / "Vereinsspielplan_Alpenvorland_2026-27.csv"
SEASON = "2026-27"
PRE_GAME_BUFFER_MINUTES = 30
MAX_RELEVANT_TRAVEL_GAP_MINUTES = 480
DEFAULT_TRAVEL_SAFETY_PERCENT = 15
DEFAULT_TRAVEL_TRANSFER_BUFFER_MINUTES = 10
DEFAULT_MAX_AZURE_MAPS_REQUESTS_PER_RUN = 100
DEFAULT_MICROSOFT_TENANT_ID = "c0cba668-b196-49f4-b4e8-36af0e1cc1bd"
BRAND_LOGO = APP_DIR / "static" / "tsv-handball.webp"
BRAND_FONT_MEDIUM = APP_DIR / "static" / "eras-medium.ttf"
BRAND_FONT_DEMI = APP_DIR / "static" / "eras-demi.ttf"
BRAND_FONT_BOLD = APP_DIR / "static" / "eras-bold.ttf"
ANALYSIS_HELP = (
    "Prüft den gesamten Spielplan auf Überschneidungen der festgelegten "
    "Mannschaftspaare, zu knappe Fahrzeiten und fehlenden Puffer zwischen "
    "Heimspielen sowie auf fehlende oder unnötig lange Hallenbuchungen. Die "
    "Ergebnisse werden nach Priorität sortiert."
)
DURATION_HELP = (
    "Lege je Mannschaft die Regeldauer einschließlich Halbzeit und einen "
    "zusätzlichen Unterbrechungspuffer fest. Vor jedem Spiel werden außerdem "
    f"{PRE_GAME_BUFFER_MINUTES} Minuten Vorlauf berücksichtigt."
)
PAIR_HELP = (
    "Wähle in der Paarmatrix Mannschaften aus, deren Belegungszeiten sich nicht "
    "überschneiden dürfen, und weise jedem Paar eine Priorität zu. Jede "
    "ungeordnete Kombination wird genau einmal angeboten."
)
TRAVEL_HELP = (
    "Zeigt ausschließlich die gerichteten Hallenverbindungen, die für definierte "
    "Mannschaftspaare am selben Spieltag relevant sind. Azure Maps wird nur bei "
    "einem fehlenden oder abgelaufenen Cache-Eintrag aufgerufen. Die Planungszeit "
    "besteht aus der Verkehrsfahrtzeit, einem Sicherheitszuschlag und einem "
    "Puffer für Parkplatz und Hallenweg."
)


def configured_value(name: str, default: str = "") -> str:
    environment_value = os.environ.get(name)
    if environment_value:
        return environment_value

    try:
        secret_value = st.secrets.get(name, default)
    except FileNotFoundError:
        return default
    return str(secret_value) if secret_value else default


def configured_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(max(int(configured_value(name, str(default))), minimum), maximum)
    except ValueError:
        return default


def oidc_auth_is_configured() -> bool:
    try:
        auth = st.secrets.get("auth", {})
        return bool(auth.get("microsoft"))
    except (FileNotFoundError, TypeError):
        return False


def current_user_access(_expected_tenant_id: str) -> tuple[UserAccess, bool]:
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


def require_tenant_access(expected_tenant_id: str) -> None:
    if access.belongs_to_tenant(expected_tenant_id):
        return

    show_brand_header()
    if access.authenticated:
        st.error("Dieses Microsoft-Konto gehört nicht zum freigegebenen Tenant.")
        if using_oidc:
            st.button("Abmelden", key="tenant_logout", on_click=st.logout)
    else:
        st.header("Anmeldung erforderlich")
        st.write(
            "Der Spielplaner ist ausschließlich für Personen im Microsoft-Tenant "
            "von handamball.de zugänglich."
        )
        show_login_action("app_login")
    st.stop()


def asset_data_uri(path: Path, media_type: str) -> str:
    encoded = b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def concise_team_label(label: str) -> str:
    name, separator, suffix = label.rpartition(" (")
    return name if separator and suffix.endswith(")") else label


def apply_brand_theme() -> None:
    medium_font = asset_data_uri(BRAND_FONT_MEDIUM, "font/ttf")
    demi_font = asset_data_uri(BRAND_FONT_DEMI, "font/ttf")
    bold_font = asset_data_uri(BRAND_FONT_BOLD, "font/ttf")
    theme = (
        """
        <style>
        @font-face {
            font-family: "Eras Web";
            src: url("__MEDIUM_FONT__") format("truetype");
            font-display: swap;
            font-style: normal;
            font-weight: 500;
        }

        @font-face {
            font-family: "Eras Web";
            src: url("__DEMI_FONT__") format("truetype");
            font-display: swap;
            font-style: normal;
            font-weight: 700 800;
        }

        @font-face {
            font-family: "Eras Web";
            src: url("__BOLD_FONT__") format("truetype");
            font-display: swap;
            font-style: normal;
            font-weight: 900;
        }
        """
        .replace("__MEDIUM_FONT__", medium_font)
        .replace("__DEMI_FONT__", demi_font)
        .replace("__BOLD_FONT__", bold_font)
    )
    st.markdown(
        theme
        + """
        :root {
            --brand-blue: #053782;
            --brand-blue-hover: #022b66;
            --brand-red: #e00a1d;
            --brand-background: #f4f6fb;
            --brand-surface: #ffffff;
            --brand-line: #e5e7eb;
            --brand-text: #1f2937;
            --brand-heading: #111827;
            --brand-muted: #667085;
            --brand-card-radius: 0.85rem;
            --brand-button-radius: 999px;
            --brand-shadow: 0 8px 22px rgba(5, 55, 130, 0.07);
            --brand-font: "Eras Web", "Trebuchet MS", system-ui, sans-serif;
        }

        html, body, [data-testid="stAppViewContainer"] {
            font-family: var(--brand-font);
        }

        [data-testid="stAppViewContainer"] {
            background: var(--brand-background);
            color: var(--brand-text);
        }

        [data-testid="stHeader"] {
            background: rgba(255, 255, 255, 0.98);
            border-bottom: 1px solid var(--brand-line);
        }

        [data-testid="stSidebar"] {
            background: var(--brand-surface);
            border-right: 1px solid var(--brand-line);
        }

        [data-testid="stSidebarNav"] a {
            display: inline-flex;
            width: fit-content;
            max-width: 100%;
            border-radius: var(--brand-button-radius);
            color: var(--brand-heading);
            font-family: var(--brand-font) !important;
            font-weight: 700;
            padding: 0.55rem 0.8rem;
        }

        [data-testid="stSidebarNav"] a[aria-current="page"] {
            background: rgba(5, 55, 130, 0.1);
            color: var(--brand-blue);
        }

        [data-testid="stMainBlockContainer"] {
            max-width: 1200px;
            padding-top: 1.5rem;
            padding-bottom: 4rem;
        }

        h1, h2, h3 {
            color: var(--brand-heading) !important;
            font-family: var(--brand-font) !important;
            font-weight: 900 !important;
            letter-spacing: 0 !important;
        }

        h2 {
            color: var(--brand-blue) !important;
        }

        p, label, [data-testid="stCaptionContainer"] {
            color: var(--brand-text);
            font-family: var(--brand-font) !important;
        }

        [data-testid="stCaptionContainer"] {
            color: var(--brand-muted);
        }

        .spielplaner-masthead {
            margin: 0 0 2rem;
            padding: 1rem 1.15rem;
            background: var(--brand-surface);
            border: 1px solid var(--brand-line);
            border-left: 0.38rem solid var(--brand-red);
            border-radius: var(--brand-card-radius);
            box-shadow: var(--brand-shadow);
        }

        .spielplaner-masthead__club {
            margin: 0 0 0.1rem;
            color: var(--brand-blue);
            font-family: var(--brand-font) !important;
            font-size: 0.82rem;
            font-weight: 800;
            letter-spacing: 0.045em;
            text-transform: uppercase;
        }

        .spielplaner-masthead h1 {
            margin: 0;
            font-size: clamp(2rem, 4vw, 3.2rem);
            line-height: 1;
        }

        .stButton > button,
        .stDownloadButton > button,
        [data-testid="stBaseButton-primary"],
        [data-testid="stBaseButton-secondary"] {
            min-height: 2.75rem;
            border-radius: var(--brand-button-radius) !important;
            font-family: var(--brand-font);
            font-weight: 800;
        }

        [data-testid="stBaseButton-primary"] {
            border-color: var(--brand-blue) !important;
            background: var(--brand-blue) !important;
            color: #ffffff !important;
        }

        [data-testid="stBaseButton-primary"] p {
            color: #ffffff !important;
        }

        [data-testid="stBaseButton-primary"]:hover {
            border-color: var(--brand-blue-hover) !important;
            background: var(--brand-blue-hover) !important;
        }

        [data-testid="stBaseButton-secondary"] {
            border-color: var(--brand-blue) !important;
            background: var(--brand-surface) !important;
            color: var(--brand-blue) !important;
        }

        [data-testid="stBaseButton-secondary"]:hover {
            background: rgba(5, 55, 130, 0.08) !important;
        }

        [data-testid="stDataFrame"],
        [data-testid="stDataEditor"],
        [data-testid="stFileUploader"],
        [data-testid="stExpander"] {
            overflow: hidden;
            border: 1px solid var(--brand-line);
            border-radius: var(--brand-card-radius);
            background: var(--brand-surface);
            box-shadow: var(--brand-shadow);
        }

        [data-testid="stAlert"] {
            border-radius: var(--brand-card-radius);
            box-shadow: none;
        }

        [data-testid="stFileUploader"] {
            padding: 1rem;
        }

        [data-testid="stFileUploader"] label {
            display: block;
            margin-bottom: 0.45rem;
        }

        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        [data-testid="stNumberInput"] input {
            border-radius: var(--brand-card-radius) !important;
            font-family: var(--brand-font) !important;
        }

        a:focus-visible, button:focus-visible, input:focus-visible {
            outline: 3px solid #ffb3bb !important;
            outline-offset: 3px;
        }

        @media (max-width: 700px) {
            [data-testid="stMainBlockContainer"] {
                padding-top: 3rem;
            }

            .spielplaner-masthead {
                padding: 0.85rem;
            }

        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def show_brand_header() -> None:
    st.markdown(
        """
        <div class="spielplaner-masthead">
            <p class="spielplaner-masthead__club">TSV Weilheim Handball</p>
            <h1>Spielplaner</h1>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource(show_spinner=False)
def create_pair_store(connection_string: str, table_name: str) -> PairStore:
    return PairStore.from_connection_string(connection_string, table_name)


@st.cache_resource(show_spinner=False)
def create_duration_store(connection_string: str, table_name: str) -> DurationStore:
    return DurationStore.from_connection_string(connection_string, table_name)


@st.cache_resource(show_spinner=False)
def create_schedule_store(
    connection_string: str, container_name: str
) -> ScheduleStore:
    return ScheduleStore.from_connection_string(connection_string, container_name)


@st.cache_resource(show_spinner=False)
def create_azure_maps_client(
    subscription_key: str, safety_percent: int, transfer_buffer_minutes: int
) -> AzureMapsClient:
    return AzureMapsClient(
        subscription_key,
        safety_percent=safety_percent,
        transfer_buffer_minutes=transfer_buffer_minutes,
    )


@st.cache_resource(show_spinner=False)
def create_travel_time_store(
    connection_string: str, table_name: str
) -> TravelTimeStore:
    return TravelTimeStore.from_connection_string(connection_string, table_name)


@st.cache_resource(show_spinner=False)
def create_omoc_client(
    bookings_url: str, username: str, password: str
) -> OmocClient:
    return OmocClient(bookings_url, username, password)


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
) -> tuple[list[tuple[Team, Team, str]], int]:
    if "manual_pairs" in st.session_state:
        raw_pairs = list(st.session_state["manual_pairs"])
    else:
        raw_pairs = [
            (pair.team_a, pair.team_b, pair.priority) for pair in saved_pairs
        ]
    result: list[tuple[Team, Team, str]] = []
    seen: set[tuple[str, str]] = set()
    skipped = 0

    for pair in raw_pairs:
        label_a, label_b = pair[:2]
        priority = normalize_priority(pair[2] if len(pair) == 3 else None)
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
        result.append(
            (team_by_label[label_a], team_by_label[label_b], priority)
        )
    return result, skipped


def relevant_travel_legs(
    analysis_pairs: list[tuple[Team, Team, str]],
) -> pd.DataFrame:
    return find_relevant_travel_legs(
        schedule,
        analysis_pairs,
        current_duration_map(),
        PRE_GAME_BUFFER_MINUTES,
        MAX_RELEVANT_TRAVEL_GAP_MINUTES,
    )


def resolve_travel_times(
    legs: pd.DataFrame,
) -> tuple[dict[tuple[str, str, str], int], dict[str, int]]:
    estimates: dict[tuple[str, str, str], int] = {}
    stats = {
        "relevant": len(legs),
        "requested": 0,
        "resolved": 0,
        "cached": 0,
        "missing_address": 0,
        "failed": 0,
        "deferred": 0,
        "cache_failed": 0,
    }
    if legs.empty:
        return estimates, stats

    unique_legs = legs.drop_duplicates(
        subset=["Startschlüssel", "Zielschlüssel", "Abfahrt"]
    ).sort_values(["Verfügbar (Min.)", "Abfahrt"])
    missing_address = unique_legs[
        unique_legs["Startadresse"].astype(str).str.strip().eq("")
        | unique_legs["Zieladresse"].astype(str).str.strip().eq("")
    ]
    stats["missing_address"] = len(missing_address)
    requestable = unique_legs.drop(index=missing_address.index)
    unresolved = []
    for _, leg in requestable.iterrows():
        cached = None
        if travel_time_store is not None:
            try:
                cached = travel_time_store.get(
                    leg["Startschlüssel"], leg["Zielschlüssel"], leg["Abfahrt"]
                )
            except Exception:
                stats["cache_failed"] += 1
        if cached is None:
            unresolved.append(leg)
            continue
        estimates[
            travel_leg_key(
                leg["Startschlüssel"], leg["Zielschlüssel"], leg["Abfahrt"]
            )
        ] = cached.planning_minutes
        stats["cached"] += 1
        stats["resolved"] += 1

    request_batch = unresolved[:max_azure_maps_requests_per_run]
    stats["deferred"] = max(0, len(unresolved) - len(request_batch))
    if azure_maps_client is None:
        stats["deferred"] += len(request_batch)
        return estimates, stats

    for leg in request_batch:
        stats["requested"] += 1
        try:
            estimate = azure_maps_client.compute_route(
                str(leg["Startadresse"]),
                str(leg["Zieladresse"]),
                pd.Timestamp(leg["Abfahrt"]).to_pydatetime(),
            )
        except (AzureMapsError, ValueError):
            stats["failed"] += 1
            continue
        estimates[
            travel_leg_key(
                leg["Startschlüssel"], leg["Zielschlüssel"], leg["Abfahrt"]
            )
        ] = estimate.planning_minutes
        if travel_time_store is not None:
            try:
                travel_time_store.save(
                    leg["Startschlüssel"],
                    leg["Zielschlüssel"],
                    leg["Abfahrt"],
                    estimate.source_minutes,
                    estimate.planning_minutes,
                    estimate.distance_meters,
                    estimate.valid_until,
                )
            except Exception:
                stats["cache_failed"] += 1
        stats["resolved"] += 1
    return estimates, stats


def show_travel_status(stats: dict[str, int]) -> None:
    if stats["missing_address"]:
        st.warning(
            f"{stats['missing_address']} relevante Verbindung(en) konnten nicht "
            "abgefragt werden, weil im Spielplan eine Hallenadresse fehlt."
        )
    if stats["failed"]:
        st.warning(
            f"Azure Maps konnte {stats['failed']} Fahrzeit(en) nicht liefern. "
            "Diese Verbindungen wurden in diesem Prüflauf nicht bewertet."
        )
    if stats["cache_failed"]:
        st.warning(
            "Der persistente Fahrzeitcache war teilweise nicht erreichbar. "
            "Ermittelte Fahrzeiten wurden trotzdem für diesen Prüflauf verwendet."
        )
    if stats["deferred"]:
        if azure_maps_client is None:
            st.warning(
                "Die Fahrzeitprüfung ist vorbereitet, aber der serverseitige "
                "Azure-Maps-Schlüssel ist noch nicht konfiguriert."
            )
        else:
            st.warning(
                f"{stats['deferred']} Verbindung(en) wurden wegen des Limits von "
                f"{max_azure_maps_requests_per_run} Azure-Maps-Aufrufen pro "
                "Prüflauf nicht "
                "bewertet."
            )
    if stats["cached"]:
        st.caption(
            f"{stats['cached']} Fahrzeit(en) wurden aus dem persistenten "
            "Azure-Cache verwendet."
        )


def show_azure_maps_attribution() -> None:
    st.markdown(
        '<p style="font-family:Arial,sans-serif;font-size:14px;'
        'font-weight:400;color:#5f6368;margin-top:.35rem">'
        "Fahrzeitdaten: Azure Maps</p>",
        unsafe_allow_html=True,
    )


def resolve_hall_bookings() -> tuple[pd.DataFrame | None, str]:
    if omoc_client is None:
        return None, "OMOC ist noch nicht konfiguriert."
    blocks = home_game_blocks(
        schedule, teams, current_duration_map(), PRE_GAME_BUFFER_MINUTES
    )
    relevant = blocks[
        blocks["Hallennummer"].astype(str).isin(HALL_BOOKING_REQUIREMENTS)
    ]
    if relevant.empty:
        return pd.DataFrame(), ""
    try:
        return (
            omoc_client.fetch_bookings(
                relevant["Datum"].min(), relevant["Datum"].max()
            ),
            "",
        )
    except (OmocError, ValueError) as exc:
        return None, str(exc)


def show_analysis_page() -> None:
    st.header("Spielplanprüfung", help=ANALYSIS_HELP)

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
        legs = relevant_travel_legs(analysis_pairs)
        with st.spinner("Fahrzeiten und Hallenbuchungen werden geprüft …"):
            travel_minutes, travel_stats = resolve_travel_times(legs)
            hall_bookings, hall_booking_error = resolve_hall_bookings()
            findings = analyze_schedule(
                schedule,
                teams,
                current_duration_map(),
                analysis_pairs,
                PRE_GAME_BUFFER_MINUTES,
                travel_minutes,
                hall_bookings,
            )
        show_travel_status(travel_stats)
        if hall_booking_error:
            st.warning(
                f"Die Hallenbuchungsregel wurde nicht ausgewertet: "
                f"{hall_booking_error}"
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
                "Prüfung als CSV herunterladen",
                findings.to_csv(index=False, sep=";").encode("utf-8-sig"),
                "spielplan_pruefung.csv",
                "text/csv",
            )
        if travel_stats["resolved"]:
            show_azure_maps_attribution()

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
    st.header("Spieldauern", help=DURATION_HELP)

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
            "TeamKey": team.key,
            "Mannschaft": concise_team_label(team.label),
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
            "TeamKey": None,
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
        team = team_by_key[str(row["TeamKey"])]
        duration_settings[team.key] = {
            "base_minutes": int(row["Regeldauer inkl. Halbzeit (Min.)"]),
            "extra_minutes": int(row["Unterbrechungspuffer (Min.)"]),
        }

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
    st.header("Mannschaftspaare", help=PAIR_HELP)
    pair_store, stored_pairs, pair_error = load_saved_pairs()
    if pair_error:
        st.warning("Gespeicherte Mannschaftspaare konnten nicht geladen werden.")

    notice = st.session_state.pop("pairing_notice", "")
    if notice:
        st.success(notice)

    stored_by_key = {
        pair_row_key(pair.team_a, pair.team_b): pair for pair in stored_pairs
    }
    manual_pairs = st.session_state.get("manual_pairs")
    active_pairs = manual_pairs if manual_pairs is not None else [
        (pair.team_a, pair.team_b, pair.priority) for pair in stored_pairs
    ]
    active_by_key = {
        pair_row_key(pair[0], pair[1]): normalize_priority(
            pair[2] if len(pair) == 3 else None
        )
        for pair in active_pairs
        if pair[0] != pair[1]
    }

    matrix_source, column_teams = build_pair_matrix(labels, active_by_key)
    matrix_columns = {
        "Mannschaft": st.column_config.TextColumn(
            width="medium",
            disabled=True,
            pinned=True,
        )
    }
    matrix_columns.update(
        {
            heading: st.column_config.SelectboxColumn(
                label=heading,
                help=concise_team_label(team_label),
                options=("", *PRIORITY_LEVELS),
                width="small",
            )
            for heading, team_label in column_teams.items()
        }
    )
    matrix = st.data_editor(
        matrix_source,
        hide_index=True,
        width="stretch",
        height=520,
        disabled=["Mannschaft"],
        column_config=matrix_columns,
        key="pair_matrix_editor",
    )
    selected_pairs = selected_pairs_from_matrix(matrix, labels, column_teams)
    selected_by_key = {
        pair_row_key(team_a, team_b): (team_a, team_b, priority)
        for team_a, team_b, priority in selected_pairs
    }
    st.session_state["manual_pairs"] = selected_pairs
    st.caption(
        f"{len(selected_pairs)} Paarung(en) ausgewählt. Eine leere Zelle bedeutet "
        "keine Prüfung. Nur die obere Hälfte der Matrix wird ausgewertet."
    )

    if not access.can_edit_pairings:
        st.caption("Die Auswahl gilt für diese Sitzung und ist nicht dauerhaft.")
        return
    if pair_store is None:
        st.warning("Azure Table Storage ist noch nicht für die App konfiguriert.")
        return

    removed_keys = set(stored_by_key).difference(selected_by_key)
    deletion_confirmed = True
    if removed_keys:
        deletion_confirmed = st.checkbox(
            f"Das Entfernen von {len(removed_keys)} gespeicherten Paarung(en) "
            "bestätigen"
        )
    if st.button(
        "Paarmatrix dauerhaft speichern",
        type="primary",
        disabled=not deletion_confirmed,
        width="stretch",
    ):
        saved = pair_store.replace_pairs(
            SEASON,
            selected_pairs,
            access.object_id or access.display_name,
        )
        st.session_state["pairing_notice"] = (
            f"Die Paarmatrix mit {saved} Paarung(en) wurde gespeichert."
        )
        st.rerun()


def show_travel_page() -> None:
    st.header("Fahrzeiten", help=TRAVEL_HELP)

    _, saved_pairs, pair_error = load_saved_pairs()
    if pair_error:
        st.warning("Gespeicherte Mannschaftspaare konnten nicht geladen werden.")
    analysis_pairs, _ = pairs_for_analysis(saved_pairs)
    legs = relevant_travel_legs(analysis_pairs)

    if legs.empty:
        st.info("Für die aktuellen Mannschaftspaare ist keine Fahrstrecke relevant.")
    else:
        matrix = (
            legs.groupby(
                ["Startschlüssel", "Starthalle", "Zielschlüssel", "Zielhalle"],
                as_index=False,
            )
            .agg(
                **{
                    "Betroffene Spielabfolgen": ("Datum", "count"),
                    "Knappster Zeitrahmen (Min.)": ("Verfügbar (Min.)", "min"),
                }
            )
            .rename(columns={"Starthalle": "Von", "Zielhalle": "Nach"})
        )
        st.dataframe(
            matrix[
                [
                    "Von",
                    "Nach",
                    "Betroffene Spielabfolgen",
                    "Knappster Zeitrahmen (Min.)",
                ]
            ],
            hide_index=True,
            width="stretch",
        )
        st.caption(
            f"{len(legs)} relevante Spielabfolge(n), gebündelt zu "
            f"{len(matrix)} gerichteten Hallenverbindung(en)."
        )

    if azure_maps_client is None:
        st.warning(
            "Azure Maps ist noch nicht konfiguriert. Bis der Schlüssel im "
            "Azure App Service hinterlegt ist, werden Fahrzeiten nicht bewertet."
        )
    else:
        st.success(
            "Azure Maps ist konfiguriert. Bereits ermittelte Fahrzeiten werden "
            "entsprechend ihrer zulässigen Gültigkeit persistent wiederverwendet."
        )

def show_guide_page() -> None:
    st.header("Anleitung")
    st.markdown(
        "Der Spielplaner kontrolliert die Spiele des TSV Weilheim und der "
        "weiblichen A-Jugend des BSC Oberhausen in der Saison 2026/27. Ein "
        "aktualisierter nuLiga-Gesamtspielplan kann bei Bedarf als CSV hochgeladen "
        "werden."
    )

    st.subheader("1. Mannschaftspaare festlegen")
    st.markdown(
        "Markiere in der Paarmatrix alle Mannschaftskombinationen, deren "
        "Belegungszeiten sich nicht überschneiden dürfen. Jede Kombination "
        "erscheint unabhängig von ihrer Richtung genau einmal. Für jedes Paar "
        "kann die Priorität **Hoch**, **Mittel** oder **Niedrig** festgelegt "
        "werden. Berechtigte Benutzer können die vollständige Matrix dauerhaft "
        "in Azure speichern."
    )

    st.subheader("2. Spieldauern prüfen")
    st.markdown(
        "Die Regeldauer umfasst beide Halbzeiten und die Halbzeitpause. Der "
        "Unterbrechungspuffer berücksichtigt Time-outs und sonstige Stopps. Für "
        f"die Prüfung wird beides addiert; vor jedem Anwurf werden zusätzlich "
        f"{PRE_GAME_BUFFER_MINUTES} Minuten Vorlauf eingeplant."
    )

    st.subheader("3. Fahrzeiten berücksichtigen")
    st.markdown(
        "Für Mannschaftspaare mit zwei nicht überlappenden Spielen am selben Tag "
        "ermittelt die App nur die tatsächlich benötigten Fahrstrecken. Beim "
        "Prüflauf wird die Verkehrsfahrtzeit über Azure Maps ermittelt. "
        f"Hinzu kommen {travel_safety_percent} % Sicherheitszuschlag und "
        f"{travel_transfer_buffer_minutes} Minuten für Parkplatz und Hallenweg. "
        "Die Ergebnisse werden höchstens sechs Monate beziehungsweise entsprechend "
        "einer kürzeren von Azure vorgegebenen Gültigkeit gespeichert."
    )

    st.subheader("4. Hallenbuchungen prüfen")
    st.markdown(
        "Für Heimspiele in Jahnhalle und Hardtschule gleicht die App das gesamte "
        "Planungsfenster mit OMOC ab. Erforderlich sind jeweils alle drei "
        "Hallenteile sowie Verkaufsraum beziehungsweise Küche als Bewirtungsraum. "
        "Das Tagesfenster beginnt 45 Minuten vor dem ersten Spiel und endet "
        "45 Minuten nach dem berechneten Ende des letzten Spiels. Fehlende und "
        "unnötig lange Buchungszeiten werden getrennt ausgewiesen. "
        "Andere Sportstätten, Kostensätze und personenbezogene Buchungsdaten werden "
        "nicht verarbeitet."
    )

    st.subheader("5. Gesamten Spielplan prüfen")
    st.markdown(
        "Die Prüfung wendet alle aktiven Regeln auf den vollständigen Spielplan an "
        "und gibt jeden Sachverhalt genau einmal aus. Die Ergebnistabelle ist nach "
        "Priorität sortiert und kann als CSV heruntergeladen werden."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Priorität": "Je Mannschaftspaar",
                    "Regel": RULE_TEAM_OVERLAP,
                    "Prüfumfang": "Alle definierten Mannschaftspaare",
                },
                {
                    "Priorität": "Je Mannschaftspaar",
                    "Regel": RULE_TRAVEL_TIME,
                    "Prüfumfang": "Relevante Abfolgen an verschiedenen Hallen",
                },
                {
                    "Priorität": RULE_PRIORITIES[RULE_HOME_BUFFER],
                    "Regel": RULE_HOME_BUFFER,
                    "Prüfumfang": (
                        f"Alle Heimspiele; {PRE_GAME_BUFFER_MINUTES} Min. Vorlauf"
                    ),
                },
                {
                    "Priorität": RULE_PRIORITIES[RULE_HALL_BOOKING],
                    "Regel": RULE_HALL_BOOKING,
                    "Prüfumfang": "Jahnhalle und Hardtschule einschließlich Bewirtungsraum",
                },
                {
                    "Priorität": RULE_PRIORITIES[RULE_HALL_BOOKING_EXCESS],
                    "Regel": RULE_HALL_BOOKING_EXCESS,
                    "Prüfumfang": "Buchungszeit vor Aufbau und nach Abbau",
                },
            ]
        ),
        hide_index=True,
        width="stretch",
    )


st.set_page_config(page_title="Spielplaner", page_icon=str(BRAND_LOGO), layout="wide")
apply_brand_theme()
st.logo(str(BRAND_LOGO), size="large")
oidc_configured = oidc_auth_is_configured()
tenant_id = configured_value(
    "MICROSOFT_TENANT_ID", DEFAULT_MICROSOFT_TENANT_ID
)
access, using_oidc = current_user_access(tenant_id)
require_tenant_access(tenant_id)
connection_string = configured_value("AZURE_STORAGE_CONNECTION_STRING")
schedule_store = None
persisted_schedule: StoredSchedule | None = None
schedule_storage_error = ""
if connection_string:
    try:
        schedule_store = create_schedule_store(
            connection_string,
            configured_value("AZURE_SCHEDULE_CONTAINER_NAME", "schedules"),
        )
        persisted_schedule = schedule_store.latest_schedule(SEASON)
    except Exception as exc:
        schedule_storage_error = str(exc)

page = st.navigation(
    [
        st.Page(
            show_analysis_page,
            title="Spielplanprüfung",
            icon=":material/fact_check:",
            url_path="spielplanpruefung",
            default=True,
        ),
        st.Page(
            show_duration_page,
            title="Spieldauern",
            icon=":material/schedule:",
            url_path="spieldauern",
        ),
        st.Page(
            show_pair_page,
            title="Mannschaftspaare",
            icon=":material/group_work:",
            url_path="mannschaftspaare",
        ),
        st.Page(
            show_travel_page,
            title="Fahrzeiten",
            icon=":material/route:",
            url_path="fahrzeiten",
        ),
        st.Page(
            show_guide_page,
            title="Anleitung",
            icon=":material/help:",
            url_path="anleitung",
        ),
    ]
)

with st.sidebar:
    st.caption(f"Angemeldet als {access.display_name or 'Microsoft-Benutzer'}")
    if using_oidc:
        st.button("Abmelden", key="sidebar_logout", on_click=st.logout)

show_brand_header()
uploaded = st.file_uploader(
    "Aktualisierten nuLiga-Gesamtspielplan dauerhaft laden",
    type="csv",
    disabled=not access.can_edit_pairings or schedule_store is None,
)

try:
    active_schedule = persisted_schedule
    if uploaded is not None:
        uploaded_content = uploaded.getvalue()
        main_schedule = load_schedule(BytesIO(uploaded_content))
        upload_digest = sha256(uploaded_content).hexdigest()
        if st.session_state.get("persisted_schedule_digest") != upload_digest:
            active_schedule = schedule_store.save_schedule(
                SEASON,
                uploaded.name,
                uploaded_content,
                access.object_id or access.display_name,
            )
            st.session_state["persisted_schedule_digest"] = upload_digest
    elif active_schedule is not None:
        main_schedule = load_schedule(BytesIO(active_schedule.content))
    else:
        main_schedule = load_schedule(DEFAULT_CSV)

    schedule_parts = [main_schedule]
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

if schedule_storage_error:
    st.warning(
        "Der persistente Spielplanspeicher ist derzeit nicht erreichbar. "
        "Es wird der mitgelieferte Spielplan verwendet."
    )
elif active_schedule is not None:
    uploaded_at = pd.Timestamp(active_schedule.uploaded_at).tz_convert(
        ZoneInfo("Europe/Berlin")
    )
    st.caption(
        f"Aktiver Spielplan: {active_schedule.original_name} · hochgeladen am "
        f"{uploaded_at:%d.%m.%Y um %H:%M Uhr}"
    )
elif schedule_store is None:
    st.warning("Azure Blob Storage ist noch nicht für Spielplan-Uploads verfügbar.")
else:
    st.caption("Aktiver Spielplan: mitgelieferter Saisonstand")

teams = available_teams(schedule)
team_by_label = {team.label: team for team in teams}
team_by_key = {team.key: team for team in teams}
labels = list(team_by_label)
duration_store = None
stored_durations = {}
duration_storage_error = ""
travel_safety_percent = configured_int(
    "TRAVEL_TIME_SAFETY_PERCENT",
    DEFAULT_TRAVEL_SAFETY_PERCENT,
    0,
    100,
)
travel_transfer_buffer_minutes = configured_int(
    "TRAVEL_TIME_TRANSFER_BUFFER_MINUTES",
    DEFAULT_TRAVEL_TRANSFER_BUFFER_MINUTES,
    0,
    120,
)
max_azure_maps_requests_per_run = configured_int(
    "AZURE_MAPS_MAX_REQUESTS_PER_RUN",
    DEFAULT_MAX_AZURE_MAPS_REQUESTS_PER_RUN,
    1,
    1000,
)
azure_maps_subscription_key = configured_value("AZURE_MAPS_SUBSCRIPTION_KEY")
azure_maps_client = (
    create_azure_maps_client(
        azure_maps_subscription_key,
        travel_safety_percent,
        travel_transfer_buffer_minutes,
    )
    if azure_maps_subscription_key
    else None
)
travel_time_store = None
if connection_string:
    try:
        travel_time_store = create_travel_time_store(
            connection_string,
            configured_value("AZURE_TRAVEL_TIME_TABLE_NAME", "traveltimes"),
        )
    except Exception:
        travel_time_store = None

omoc_bookings_url = configured_value("OMOC_BOOKINGS_URL")
omoc_username = configured_value("OMOC_API_USERNAME")
omoc_password = configured_value("OMOC_API_PASSWORD")
omoc_client = (
    create_omoc_client(omoc_bookings_url, omoc_username, omoc_password)
    if omoc_bookings_url and omoc_username and omoc_password
    else None
)

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

