import os
from base64 import b64encode
from datetime import date
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, JsCode

from access_control import (
    UserAccess,
    parse_client_principal,
    parse_oidc_user,
)
from analysis import (
    HALL_BOOKING_DISPLAY_NAMES,
    HALL_BOOKING_REQUIREMENTS,
    MANUAL_HALL_NUMBERS,
    OMOC_HALL_NUMBERS,
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
from booking_calendar import is_visible_booking_day
from duration_store import DurationStore
from excel_report import build_excel_report
from hall_booking_store import HallBookingStore, StoredHallBookings
from manual_hall_bookings import bookings_from_editor
from omoc import BOOKING_COLUMNS, OmocClient
from pair_matrix import (
    TEAM_CATEGORY_ORDER,
    build_pair_matrix,
    selected_pairs_from_matrix,
    sort_pair_labels,
    team_category,
)
from pair_store import PairStore, StoredPair, pair_row_key
from priorities import PRIORITY_LEVELS, normalize_priority
from schedule_store import ScheduleStore, ScheduleVersion, StoredSchedule
from schedule_workflow import (
    compare_findings,
    compare_schedules,
    relevant_schedule_overview,
    reschedule_game,
)
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
ACTIVE_RULE_COUNT = 5
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
HALL_BOOKING_HELP = (
    "Zeigt OMOC-Buchungen für Jahnhalle und Hardtschule sowie manuelle "
    "Buchungen für weitere Spielhallen. Berechtigte Benutzer können beide "
    "Datenbestände für den benötigten Saisonzeitraum aktualisieren."
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


def show_page_metrics(items: list[tuple[str, object]]) -> None:
    columns = st.columns(len(items))
    for column, (label, value) in zip(columns, items):
        column.metric(label, value)


def show_core_metrics() -> None:
    show_page_metrics(
        [
            ("Spiele im Plan", len(schedule)),
            ("Mannschaften", len(teams)),
            ("Aktive Regeln", ACTIVE_RULE_COUNT),
        ]
    )


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
            font-weight: 700 !important;
            padding: 0.55rem 0.8rem;
            transition: background-color 140ms ease, color 140ms ease;
        }

        [data-testid="stSidebarNav"] a p,
        [data-testid="stSidebarNav"] a:hover,
        [data-testid="stSidebarNav"] a:hover p {
            font-weight: 700 !important;
        }

        [data-testid="stSidebarNav"] a:hover {
            background: rgba(224, 10, 29, 0.08);
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
            display: flex;
            align-items: baseline;
            gap: 0.9rem;
            margin: 0 0 1.5rem;
            padding: 0.1rem 0 0.8rem;
            border-bottom: 1px solid var(--brand-line);
        }

        .spielplaner-masthead__club {
            order: 2;
            margin: 0;
            padding-left: 0.9rem;
            color: var(--brand-blue);
            border-left: 0.2rem solid var(--brand-red);
            font-family: var(--brand-font) !important;
            font-size: 0.92rem;
            font-weight: 700;
        }

        .spielplaner-masthead h1 {
            margin: 0;
            color: var(--brand-blue) !important;
            font-size: clamp(1.7rem, 3vw, 2.25rem);
            line-height: 1.1;
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
                align-items: flex-start;
                flex-direction: column;
                gap: 0.35rem;
            }

            .spielplaner-masthead__club {
                order: 2;
                padding-left: 0;
                border-left: 0;
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
def create_hall_booking_store(
    connection_string: str, container_name: str
) -> HallBookingStore:
    return HallBookingStore.from_connection_string(connection_string, container_name)


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
    frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    source = schedule if frame is None else frame
    return find_relevant_travel_legs(
        source,
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


def relevant_hall_booking_blocks(
    frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    source = schedule if frame is None else frame
    return home_game_blocks(
        source, teams, current_duration_map(), PRE_GAME_BUFFER_MINUTES
    )


def relevant_hall_booking_period(
    hall_numbers: set[str] | frozenset[str] | None = None,
    frame: pd.DataFrame | None = None,
) -> tuple[date, date] | None:
    blocks = relevant_hall_booking_blocks(frame)
    targets = hall_numbers or frozenset(HALL_BOOKING_REQUIREMENTS)
    relevant = blocks[
        blocks["Hallennummer"].astype(str).isin(targets)
    ]
    if relevant.empty:
        return None
    return relevant["Datum"].min(), relevant["Datum"].max()


def resolve_hall_bookings(
    frame: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame | None, str]:
    if hall_booking_storage_error:
        return None, "Der gespeicherte OMOC-Buchungsstand ist nicht erreichbar."
    frames = []
    omoc_period = relevant_hall_booking_period(OMOC_HALL_NUMBERS, frame)
    if omoc_period is not None:
        if stored_hall_bookings is None:
            return (
                None,
                "Noch keine Hallenbuchungen gespeichert. Bitte auf der Seite "
                "Hallenbuchungen aktualisieren.",
            )
        date_from, date_to = omoc_period
        if (
            stored_hall_bookings.date_from > date_from
            or stored_hall_bookings.date_to < date_to
        ):
            return (
                None,
                "Der gespeicherte OMOC-Buchungsstand deckt den aktuellen "
                "Spielplan nicht vollständig ab. Bitte Hallenbuchungen "
                "aktualisieren.",
            )
        frames.append(stored_hall_bookings.bookings)
    if stored_manual_hall_bookings is not None:
        frames.append(stored_manual_hall_bookings.bookings)
    if not frames:
        return pd.DataFrame(columns=BOOKING_COLUMNS), ""
    return pd.concat(frames, ignore_index=True), ""


def local_timestamp(value: object) -> str:
    if not value:
        return "Noch nicht gespeichert"
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    return parsed.tz_convert(ZoneInfo("Europe/Berlin")).strftime(
        "%d.%m.%Y, %H:%M Uhr"
    )


def latest_value(values: list[tuple[object, str]]) -> tuple[object, str]:
    available = [(pd.Timestamp(value), person) for value, person in values if value]
    if not available:
        return "", ""
    return max(available, key=lambda item: item[0])


def data_status_frame(saved_pairs: list[StoredPair]) -> pd.DataFrame:
    pair_time, pair_user = latest_value(
        [(pair.created_at, pair.created_by) for pair in saved_pairs]
    )
    duration_time, duration_user = latest_value(
        [
            (duration.updated_at, duration.updated_by)
            for duration in stored_durations.values()
        ]
    )
    hall_values = []
    if stored_hall_bookings is not None:
        hall_values.append(
            (stored_hall_bookings.updated_at, stored_hall_bookings.updated_by)
        )
    if stored_manual_hall_bookings is not None:
        hall_values.append(
            (
                stored_manual_hall_bookings.updated_at,
                stored_manual_hall_bookings.updated_by,
            )
        )
    hall_time, hall_user = latest_value(hall_values)

    if active_schedule is not None:
        schedule_time = active_schedule.uploaded_at
        schedule_user = active_schedule.uploaded_by
        schedule_details = active_schedule.original_name
    else:
        schedule_time = ""
        schedule_user = ""
        schedule_details = "Mitgelieferter Saisonstand"

    return pd.DataFrame(
        [
            {
                "Datenbestand": "Spielplan",
                "Stand": local_timestamp(schedule_time),
                "Person": schedule_user or "System",
                "Details": schedule_details,
            },
            {
                "Datenbestand": "Mannschaftspaare",
                "Stand": local_timestamp(pair_time),
                "Person": pair_user or "-",
                "Details": f"{len(saved_pairs)} gespeicherte Paarungen",
            },
            {
                "Datenbestand": "Spieldauern",
                "Stand": local_timestamp(duration_time),
                "Person": duration_user or "-",
                "Details": f"{len(stored_durations)} gespeicherte Werte",
            },
            {
                "Datenbestand": "Hallenbuchungen",
                "Stand": local_timestamp(hall_time),
                "Person": hall_user or "-",
                "Details": "OMOC und manuelle Buchungen",
            },
        ]
    )


def report_metadata_frame(saved_pairs: list[StoredPair]) -> pd.DataFrame:
    status = data_status_frame(saved_pairs).copy()
    generated_at = pd.Timestamp.now(tz=ZoneInfo("Europe/Berlin"))
    rows = [
        {
            "Datenbestand": "Bericht",
            "Stand": generated_at.strftime("%d.%m.%Y, %H:%M Uhr"),
            "Person": access.display_name or access.object_id or "Microsoft-Benutzer",
            "Details": f"Saison {SEASON}",
        }
    ]
    rows.extend(status.to_dict("records"))
    if active_schedule is not None and active_schedule.sha256:
        rows.append(
            {
                "Datenbestand": "Spielplan-Prüfsumme",
                "Stand": "SHA-256",
                "Person": "System",
                "Details": active_schedule.sha256,
            }
        )
    comparison = st.session_state.get("schedule_comparison")
    if comparison and comparison.get("active_blob") == (
        active_schedule.blob_name if active_schedule is not None else ""
    ):
        version = comparison["version"]
        rows.append(
            {
                "Datenbestand": "Vergleichsversion",
                "Stand": local_timestamp(version.uploaded_at),
                "Person": version.uploaded_by or "-",
                "Details": version.original_name,
            }
        )
    return pd.DataFrame(rows)


def analysis_context_key(
    analysis_pairs: list[tuple[Team, Team, str]],
) -> str:
    digest = sha256()
    schedule_columns = [
        "Anwurf",
        "Hallennummer",
        "Spielnummer",
        "Liga",
        "Staffelkurzbezeichnung",
        "Heimmannschaft",
        "Gastmannschaft",
    ]
    digest.update(
        pd.util.hash_pandas_object(
            schedule[schedule_columns], index=False
        ).values.tobytes()
    )
    digest.update(repr(sorted(current_duration_map().items())).encode("utf-8"))
    digest.update(
        repr(
            sorted(
                (team_a.key, team_b.key, priority)
                for team_a, team_b, priority in analysis_pairs
            )
        ).encode("utf-8")
    )
    return digest.hexdigest()


def show_findings_table(findings: pd.DataFrame) -> None:
    st.dataframe(
        findings,
        hide_index=True,
        width="stretch",
        column_config={
            "Priorität": st.column_config.TextColumn(width="small"),
            "Regel": st.column_config.TextColumn(width="medium"),
            "Datum": st.column_config.DateColumn(format="DD.MM.YYYY", width="small"),
            "Spiele": st.column_config.TextColumn(width="large"),
            "Kommentar": st.column_config.TextColumn(width="large"),
        },
    )


def show_analysis_result(
    result: dict[str, object],
    overview: pd.DataFrame,
    saved_pairs: list[StoredPair],
) -> None:
    findings = result["findings"]
    travel_stats = result["travel_stats"]
    hall_booking_error = str(result.get("hall_booking_error", ""))
    show_travel_status(travel_stats)
    if hall_booking_error:
        st.warning(
            "Die Hallenbuchungsregel wurde nicht ausgewertet: "
            f"{hall_booking_error}"
        )
    if findings.empty:
        st.success("Die aktiven Regeln haben keine Auffälligkeiten gefunden.")
    else:
        st.error(f"{len(findings)} Auffälligkeit(en) gefunden.")
        show_findings_table(findings)

    comparison = st.session_state.get("schedule_comparison", {})
    changes = comparison.get("changes")
    report = build_excel_report(
        findings,
        overview.drop(columns="Spielschlüssel"),
        report_metadata_frame(saved_pairs),
        changes if isinstance(changes, pd.DataFrame) else None,
    )
    generated = pd.Timestamp.now(tz=ZoneInfo("Europe/Berlin"))
    st.download_button(
        "Excel-Bericht herunterladen",
        report,
        f"spielplan_pruefung_{generated:%Y-%m-%d_%H%M}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    if travel_stats["resolved"]:
        show_azure_maps_attribution()


def show_reschedule_check(
    overview: pd.DataFrame,
    analysis_pairs: list[tuple[Team, Team, str]],
    context_key: str,
) -> None:
    st.subheader("Spielplanübersicht")
    st.caption(
        "Spiel auswählen, möglichen Termin eintragen und gegen alle aktiven Regeln prüfen."
    )
    display = overview.drop(
        columns=["Spielschlüssel", "Wettbewerb", "Spielnummer"]
    )
    event = st.dataframe(
        display,
        key="schedule_overview",
        hide_index=True,
        width="stretch",
        height=360,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Anwurf": st.column_config.DatetimeColumn(
                format="DD.MM.YYYY HH:mm", width="medium"
            ),
            "Mannschaft": st.column_config.TextColumn(width="medium"),
            "Gegner": st.column_config.TextColumn(width="medium"),
            "Heim/Auswärts": st.column_config.TextColumn(width="small"),
            "Wettbewerb": st.column_config.TextColumn(width="medium"),
            "Halle": st.column_config.TextColumn(width="large"),
        },
    )
    selected_rows = event.selection.rows
    if not selected_rows:
        st.info("Für eine Terminprobe bitte eine Tabellenzeile auswählen.")
        return

    selected = overview.iloc[selected_rows[0]]
    kickoff = pd.Timestamp(selected["Anwurf"])
    widget_suffix = sha256(str(selected["Spielschlüssel"]).encode("utf-8")).hexdigest()[:10]
    st.markdown(
        f"**Ausgewählt:** {selected['Mannschaft']} gegen {selected['Gegner']} "
        f"am {kickoff:%d.%m.%Y um %H:%M Uhr}"
    )
    date_column, time_column = st.columns(2)
    proposed_date = date_column.date_input(
        "Neues Datum", value=kickoff.date(), key=f"move_date_{widget_suffix}"
    )
    proposed_time = time_column.time_input(
        "Neue Anwurfzeit",
        value=kickoff.time().replace(second=0, microsecond=0),
        key=f"move_time_{widget_suffix}",
        step=900,
    )
    if st.button(
        "Verlegung gegen Regeln prüfen",
        type="primary",
        key=f"move_check_{widget_suffix}",
    ):
        proposed_schedule = reschedule_game(
            schedule,
            str(selected["Spielschlüssel"]),
            proposed_date,
            proposed_time,
        )
        baseline_legs = relevant_travel_legs(analysis_pairs, schedule)
        proposed_legs = relevant_travel_legs(analysis_pairs, proposed_schedule)
        all_legs = pd.concat([baseline_legs, proposed_legs], ignore_index=True).drop_duplicates(
            subset=["Startschlüssel", "Zielschlüssel", "Abfahrt"]
        )
        with st.spinner("Der Verlegungstermin wird geprüft ..."):
            travel_minutes, travel_stats = resolve_travel_times(all_legs)
            baseline_bookings, _ = resolve_hall_bookings(schedule)
            proposed_bookings, proposed_booking_error = resolve_hall_bookings(
                proposed_schedule
            )
            baseline_findings = analyze_schedule(
                schedule,
                teams,
                current_duration_map(),
                analysis_pairs,
                PRE_GAME_BUFFER_MINUTES,
                travel_minutes,
                baseline_bookings,
            )
            proposed_findings = analyze_schedule(
                proposed_schedule,
                teams,
                current_duration_map(),
                analysis_pairs,
                PRE_GAME_BUFFER_MINUTES,
                travel_minutes,
                proposed_bookings,
            )
            new_findings, resolved_findings = compare_findings(
                baseline_findings, proposed_findings
            )
        st.session_state["reschedule_check"] = {
            "context_key": context_key,
            "game_key": selected["Spielschlüssel"],
            "kickoff": pd.Timestamp.combine(proposed_date, proposed_time),
            "new": new_findings,
            "resolved": resolved_findings,
            "travel_stats": travel_stats,
            "hall_booking_error": proposed_booking_error,
        }

    result = st.session_state.get("reschedule_check")
    if (
        not result
        or result.get("context_key") != context_key
        or result.get("game_key") != selected["Spielschlüssel"]
    ):
        return
    st.markdown("#### Ergebnis der Terminprobe")
    show_travel_status(result["travel_stats"])
    if result["hall_booking_error"]:
        st.warning(
            "Die Hallenbuchung konnte für den vorgeschlagenen Termin nicht "
            f"vollständig geprüft werden: {result['hall_booking_error']}"
        )
    if result["new"].empty:
        st.success("Der vorgeschlagene Termin erzeugt keine neue Auffälligkeit.")
    else:
        st.error(
            f"Der vorgeschlagene Termin erzeugt {len(result['new'])} neue "
            "Auffälligkeit(en)."
        )
        show_findings_table(result["new"])
    if not result["resolved"].empty:
        with st.expander(
            f"{len(result['resolved'])} dadurch gelöste Auffälligkeit(en)"
        ):
            show_findings_table(result["resolved"])


def combined_schedule(main_frame: pd.DataFrame) -> pd.DataFrame:
    parts = [main_frame]
    if DEFAULT_DISTRICT_CSV.exists():
        parts.append(load_schedule(DEFAULT_DISTRICT_CSV))
    return pd.concat(parts, ignore_index=True).drop_duplicates(
        subset=[
            "Anwurf",
            "Hallennummer",
            "Spielnummer",
            "Heimmannschaft",
            "Gastmannschaft",
        ],
        keep="last",
    )


def version_label(version: ScheduleVersion) -> str:
    return (
        f"{local_timestamp(version.uploaded_at)} | {version.original_name} | "
        f"{version.uploaded_by or 'unbekannt'}"
    )


def show_schedule_comparison() -> None:
    st.subheader("Spielplanversionen vergleichen")
    if schedule_store is None or active_schedule is None:
        st.info("Für den Versionsvergleich werden mindestens zwei gespeicherte Uploads benötigt.")
        return
    try:
        versions = schedule_store.list_versions(SEASON)
    except Exception as exc:
        st.warning(f"Die gespeicherten Spielplanversionen sind nicht erreichbar: {exc}")
        return
    candidates = [
        version for version in versions if version.blob_name != active_schedule.blob_name
    ]
    if not candidates:
        st.info("Es ist noch keine ältere Spielplanversion gespeichert.")
        return

    selected_version = st.selectbox(
        "Vergleichsversion",
        candidates,
        format_func=version_label,
    )
    if st.button("Änderungen anzeigen"):
        with st.spinner("Spielplanversionen werden verglichen ..."):
            previous = schedule_store.load_version(selected_version)
            previous_schedule = combined_schedule(load_schedule(BytesIO(previous.content)))
            changes = compare_schedules(schedule, previous_schedule, teams)
        st.session_state["schedule_comparison"] = {
            "active_blob": active_schedule.blob_name,
            "version": selected_version,
            "changes": changes,
        }

    comparison = st.session_state.get("schedule_comparison")
    if not comparison or comparison.get("active_blob") != active_schedule.blob_name:
        return
    changes = comparison["changes"]
    if changes.empty:
        st.success("Bei den relevanten Mannschaften wurden keine Änderungen gefunden.")
        return
    counts = changes["Status"].value_counts()
    show_page_metrics(
        [
            ("Geändert", int(counts.get("Geändert", 0))),
            ("Hinzugefügt", int(counts.get("Hinzugefügt", 0))),
            ("Entfernt", int(counts.get("Entfernt", 0))),
        ]
    )
    st.dataframe(
        changes,
        hide_index=True,
        width="stretch",
        column_config={
            "Status": st.column_config.TextColumn(width="small"),
            "Spiel": st.column_config.TextColumn(width="large"),
            "Änderung": st.column_config.TextColumn(width="medium"),
            "Vorher": st.column_config.TextColumn(width="large"),
            "Aktuell": st.column_config.TextColumn(width="large"),
        },
    )


def show_analysis_page() -> None:
    st.header("Spielplanprüfung", help=ANALYSIS_HELP)
    show_core_metrics()

    _, saved_pairs, pair_error = load_saved_pairs()
    with st.expander("Datenstände"):
        st.dataframe(
            data_status_frame(saved_pairs), hide_index=True, width="stretch"
        )

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

    overview = relevant_schedule_overview(schedule, teams)
    context_key = analysis_context_key(analysis_pairs)
    if st.button("Gesamten Spielplan prüfen", type="primary", width="stretch"):
        legs = relevant_travel_legs(analysis_pairs)
        with st.spinner("Fahrzeiten und Hallenbuchungen werden geprüft ..."):
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
        st.session_state["analysis_result"] = {
            "context_key": context_key,
            "findings": findings,
            "travel_stats": travel_stats,
            "hall_booking_error": hall_booking_error,
        }

    analysis_result = st.session_state.get("analysis_result")
    if analysis_result and analysis_result.get("context_key") == context_key:
        show_analysis_result(analysis_result, overview, saved_pairs)

    st.divider()
    show_reschedule_check(overview, analysis_pairs, context_key)
    st.divider()
    show_schedule_comparison()

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
    show_core_metrics()

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
    show_core_metrics()
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

    matrix_labels = sort_pair_labels(labels)
    matrix_source, column_teams = build_pair_matrix(matrix_labels, active_by_key)
    categories = [team_category(label) for label in matrix_labels]
    category_starts = [
        index == 0 or category != categories[index - 1]
        for index, category in enumerate(categories)
    ]
    matrix_source.insert(
        0,
        "Bereich",
        [
            category if is_start else ""
            for category, is_start in zip(categories, category_starts)
        ],
    )
    matrix_source.insert(0, "_category_start", category_starts)
    matrix_source.insert(0, "_row_index", range(len(matrix_source)))
    editable_cell = JsCode(
        """
        function(params) {
            return params.data._row_index < params.colDef.matrixIndex;
        }
        """
    )
    priority_cell_style = JsCode(
        """
        function(params) {
            if (params.data._row_index >= params.colDef.matrixIndex) {
                return {
                    backgroundColor: '#e5e7eb',
                    color: '#e5e7eb',
                    cursor: 'not-allowed'
                };
            }
            if (params.value === 'Hoch') {
                return {backgroundColor: '#ffc9c9', color: '#8f111c'};
            }
            if (params.value === 'Mittel') {
                return {backgroundColor: '#ffd8a8', color: '#7a3d00'};
            }
            if (params.value === 'Niedrig') {
                return {backgroundColor: '#fff3bf', color: '#5f4b00'};
            }
            return {backgroundColor: '#f1f3f5', color: '#667085'};
        }
        """
    )
    category_row_style = JsCode(
        """
        function(params) {
            if (params.data._category_start && params.data._row_index > 0) {
                return {borderTop: '2px solid #9bb2d1'};
            }
            return null;
        }
        """
    )
    column_defs = [
        {
            "field": "_row_index",
            "hide": True,
        },
        {
            "field": "_category_start",
            "hide": True,
        },
        {
            "field": "Bereich",
            "headerName": "Bereich",
            "pinned": "left",
            "lockPinned": True,
            "editable": False,
            "width": 135,
            "minWidth": 125,
            "cellStyle": {
                "fontWeight": "700",
                "backgroundColor": "#edf2f8",
                "color": "#053782",
            },
        },
        {
            "field": "Mannschaft",
            "headerName": "Mannschaft",
            "pinned": "left",
            "lockPinned": True,
            "editable": False,
            "width": 190,
            "minWidth": 160,
            "cellStyle": {
                "fontWeight": "700",
                "backgroundColor": "#ffffff",
                "color": "#1f2937",
            },
        },
    ]
    indexed_columns = list(enumerate(column_teams.items()))
    for category in TEAM_CATEGORY_ORDER:
        children = [
            {
                "field": heading,
                "headerName": heading,
                "headerTooltip": concise_team_label(team_label),
                "matrixIndex": column_index,
                "editable": editable_cell,
                "cellEditor": "agSelectCellEditor",
                "cellEditorParams": {"values": ["", *PRIORITY_LEVELS]},
                "cellStyle": priority_cell_style,
                "width": 105,
                "minWidth": 90,
            }
            for column_index, (heading, team_label) in indexed_columns
            if team_category(team_label) == category
        ]
        if children:
            column_defs.append(
                {
                    "headerName": category,
                    "marryChildren": True,
                    "children": children,
                }
            )
    matrix_font = asset_data_uri(BRAND_FONT_MEDIUM, "font/ttf")
    grid_response = AgGrid(
        matrix_source,
        gridOptions={
            "columnDefs": column_defs,
            "defaultColDef": {
                "sortable": False,
                "filter": False,
                "resizable": True,
                "suppressMovable": True,
            },
            "getRowStyle": category_row_style,
            "rowHeight": 38,
            "headerHeight": 42,
            "groupHeaderHeight": 36,
            "alwaysShowHorizontalScroll": True,
            "suppressColumnVirtualisation": True,
            "stopEditingWhenCellsLoseFocus": True,
        },
        height=520,
        theme="streamlit",
        allow_unsafe_jscode=True,
        enable_enterprise_modules=False,
        update_on=["cellValueChanged"],
        custom_css={
            "@font-face": {
                "font-family": "'Eras Matrix'",
                "src": f"url('{matrix_font}') format('truetype')",
                "font-style": "normal",
                "font-weight": "500",
            },
            ".ag-root-wrapper": {
                "border": "1px solid #e5e7eb",
                "border-radius": "0.85rem",
                "overflow": "hidden",
            },
            ".ag-root-wrapper, .ag-select-list-item, .ag-popup, "
            ".ag-header-cell-label, .ag-header-group-cell-label": {
                "font-family": "'Eras Matrix', 'Trebuchet MS', sans-serif",
            },
            ".ag-header-cell-label, .ag-header-group-cell-label": {
                "font-weight": "700",
            },
            ".ag-cell": {
                "font-family": "'Eras Matrix', 'Trebuchet MS', sans-serif",
                "display": "flex",
                "align-items": "center",
            },
        },
        key="pair_matrix_editor",
    )
    matrix = pd.DataFrame(grid_response.data).drop(
        columns=["_row_index", "_category_start", "Bereich"], errors="ignore"
    )
    selected_pairs = selected_pairs_from_matrix(matrix, matrix_labels, column_teams)
    selected_by_key = {
        pair_row_key(team_a, team_b): (team_a, team_b, priority)
        for team_a, team_b, priority in selected_pairs
    }
    st.session_state["manual_pairs"] = selected_pairs
    st.caption(
        f"{len(selected_pairs)} Paarung(en) ausgewählt. Die Achsen sind nach "
        "Altersbereichen gruppiert. Eine leere Zelle bedeutet keine Prüfung; "
        "die graue untere Hälfte ist gesperrt."
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
    show_core_metrics()

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


def hall_booking_room_details() -> dict[str, tuple[str, str, str]]:
    return {
        room_id: (hall_id, HALL_BOOKING_DISPLAY_NAMES[hall_id], room_name)
        for hall_id, rooms in HALL_BOOKING_REQUIREMENTS.items()
        for room_id, room_name in rooms
    }


def hall_booking_game_days() -> set[tuple[date, str]]:
    return {
        (row["Datum"], str(row["Hallennummer"]))
        for row in relevant_hall_booking_blocks().to_dict("records")
        if str(row["Hallennummer"]) in HALL_BOOKING_REQUIREMENTS
    }


def visible_hall_bookings(bookings: pd.DataFrame) -> pd.DataFrame:
    room_details = hall_booking_room_details()
    game_days = hall_booking_game_days()
    visible_indices = []
    for index, booking in bookings.iterrows():
        day = pd.Timestamp(booking["Buchungsbeginn"]).date()
        hall_numbers = {
            room_details[room_id][0]
            for room_id in booking["Raum-IDs"]
            if room_id in room_details
        }
        if any(
            is_visible_booking_day(day, hall_number, game_days)
            for hall_number in hall_numbers
        ):
            visible_indices.append(index)
    return bookings.loc[visible_indices].copy()


def hall_booking_display(bookings: pd.DataFrame, source: str) -> pd.DataFrame:
    room_details = {
        room_id: details[1:] for room_id, details in hall_booking_room_details().items()
    }
    room_order = {room_id: index for index, room_id in enumerate(room_details)}
    rows = []
    for booking in bookings.sort_values("Buchungsbeginn").to_dict("records"):
        room_ids = sorted(
            booking["Raum-IDs"], key=lambda room_id: room_order.get(room_id, 999)
        )
        facilities = list(
            dict.fromkeys(
                room_details[room_id][0]
                for room_id in room_ids
                if room_id in room_details
            )
        )
        room_names = [
            room_details[room_id][1]
            for room_id in room_ids
            if room_id in room_details
        ]
        starts_at = pd.Timestamp(booking["Buchungsbeginn"])
        ends_at = pd.Timestamp(booking["Buchungsende"])
        rows.append(
            {
                "Quelle": source,
                "Buchungsnummer": booking["Buchungsnummer"],
                "Datum": starts_at.date(),
                "Von": starts_at.time(),
                "Bis": ends_at.time(),
                "Sportstätte": ", ".join(facilities),
                "Gebuchte Bereiche": ", ".join(room_names),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "Quelle",
            "Buchungsnummer",
            "Datum",
            "Von",
            "Bis",
            "Sportstätte",
            "Gebuchte Bereiche",
        ],
    )


def manual_hall_editor_frame(bookings: pd.DataFrame) -> pd.DataFrame:
    room_details = hall_booking_room_details()
    rows = []
    for booking in bookings.sort_values("Buchungsbeginn").to_dict("records"):
        manual_rooms = [
            room_id
            for room_id in booking["Raum-IDs"]
            if room_id in room_details
            and room_details[room_id][0] in MANUAL_HALL_NUMBERS
        ]
        if not manual_rooms:
            continue
        starts_at = pd.Timestamp(booking["Buchungsbeginn"])
        ends_at = pd.Timestamp(booking["Buchungsende"])
        rows.append(
            {
                "Sportstätte": room_details[manual_rooms[0]][1],
                "Datum": starts_at.date(),
                "Von": starts_at.time(),
                "Bis": ends_at.time(),
            }
        )
    return pd.DataFrame(rows, columns=["Sportstätte", "Datum", "Von", "Bis"])


def manual_bookings_from_editor(
    editor: pd.DataFrame,
    date_from: date,
    date_to: date,
) -> pd.DataFrame:
    hall_by_name = {
        HALL_BOOKING_DISPLAY_NAMES[hall_number]: hall_number
        for hall_number in MANUAL_HALL_NUMBERS
    }
    room_id_by_hall = {
        hall_number: HALL_BOOKING_REQUIREMENTS[hall_number][0][0]
        for hall_number in MANUAL_HALL_NUMBERS
    }
    return bookings_from_editor(
        editor,
        hall_by_name,
        room_id_by_hall,
        date_from,
        date_to,
        hall_booking_game_days(),
    )


def show_hall_booking_page() -> None:
    st.header("Hallenbuchungen", help=HALL_BOOKING_HELP)
    show_core_metrics()

    notice = st.session_state.pop("hall_booking_notice", "")
    if notice:
        st.success(notice)

    period = relevant_hall_booking_period()
    if period is None:
        st.info("Im aktuellen Spielplan sind keine relevanten Heimspiele enthalten.")
        return

    date_from, date_to = period
    omoc_period = relevant_hall_booking_period(OMOC_HALL_NUMBERS)
    if stored_hall_bookings is None:
        st.info("Es wurde noch kein Buchungsstand aus OMOC gespeichert.")
    else:
        updated_at = pd.Timestamp(stored_hall_bookings.updated_at).tz_convert(
            ZoneInfo("Europe/Berlin")
        )
        st.markdown(
            f"**Letzte Aktualisierung:** {updated_at:%d.%m.%Y um %H:%M Uhr}  \n"
            f"**Aktualisiert von:** "
            f"{stored_hall_bookings.updated_by or 'Unbekannter Benutzer'}"
        )
        st.caption(
            f"Gespeicherter Abfragezeitraum: "
            f"{stored_hall_bookings.date_from:%d.%m.%Y} bis "
            f"{stored_hall_bookings.date_to:%d.%m.%Y}"
        )

    can_refresh = (
        access.can_edit_pairings
        and hall_booking_store is not None
        and omoc_client is not None
        and omoc_period is not None
    )
    if st.button(
        "Hallenbuchungen aus OMOC aktualisieren",
        type="primary",
        disabled=not can_refresh,
    ):
        try:
            omoc_date_from, omoc_date_to = omoc_period
            with st.spinner("Hallenbuchungen werden aus OMOC geladen …"):
                bookings = omoc_client.fetch_bookings(
                    omoc_date_from, omoc_date_to
                )
                hall_booking_store.save_bookings(
                    SEASON,
                    bookings,
                    omoc_date_from,
                    omoc_date_to,
                    access.display_name or access.object_id,
                )
            st.session_state["hall_booking_notice"] = (
                f"{len(bookings)} Buchung(en) wurden aus OMOC aktualisiert."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Die Hallenbuchungen konnten nicht aktualisiert werden: {exc}")

    if hall_booking_storage_error:
        st.warning("Azure Blob Storage ist für Hallenbuchungen nicht erreichbar.")
    elif hall_booking_store is None:
        st.warning("Azure Blob Storage ist noch nicht konfiguriert.")
    elif omoc_client is None:
        st.warning("OMOC ist noch nicht konfiguriert.")
    elif not access.can_edit_pairings:
        st.caption("Für die Aktualisierung ist die Bearbeiterrolle erforderlich.")

    st.subheader("Buchungsübersicht")
    display_frames = []
    if stored_hall_bookings is not None:
        visible = visible_hall_bookings(stored_hall_bookings.bookings)
        display_frames.append(hall_booking_display(visible, "OMOC"))
    if stored_manual_hall_bookings is not None:
        visible = visible_hall_bookings(stored_manual_hall_bookings.bookings)
        display_frames.append(hall_booking_display(visible, "Manuell"))
    display_bookings = (
        pd.concat(display_frames, ignore_index=True)
        if display_frames
        else pd.DataFrame()
    )
    if display_bookings.empty:
        st.info(
            "Für Wochenenden, Feiertage und abweichende Spieltage sind noch "
            "keine Buchungen gespeichert."
        )
    else:
        display_bookings = display_bookings.sort_values(
            ["Datum", "Von", "Sportstätte"], ignore_index=True
        )
        st.dataframe(
            display_bookings,
            hide_index=True,
            width="stretch",
            column_config={
                "Quelle": st.column_config.TextColumn(width="small"),
                "Buchungsnummer": st.column_config.TextColumn(width="medium"),
                "Datum": st.column_config.DateColumn(
                    format="DD.MM.YYYY", width="small"
                ),
                "Von": st.column_config.TimeColumn(format="HH:mm", width="small"),
                "Bis": st.column_config.TimeColumn(format="HH:mm", width="small"),
                "Sportstätte": st.column_config.TextColumn(width="large"),
                "Gebuchte Bereiche": st.column_config.TextColumn(width="large"),
            },
        )

    st.subheader("Manuelle Buchungen")
    st.caption(
        "Aktuell steht Oberhausen zur Auswahl. Weitere Hallen können später "
        "als feste Auswahl ergänzt werden."
    )
    if stored_manual_hall_bookings is not None:
        manual_updated_at = pd.Timestamp(
            stored_manual_hall_bookings.updated_at
        ).tz_convert(ZoneInfo("Europe/Berlin"))
        st.caption(
            f"Zuletzt gespeichert am {manual_updated_at:%d.%m.%Y um %H:%M Uhr} "
            f"von {stored_manual_hall_bookings.updated_by or 'Unbekannter Benutzer'}"
        )

    manual_source = manual_hall_editor_frame(
        stored_manual_hall_bookings.bookings
        if stored_manual_hall_bookings is not None
        else pd.DataFrame(columns=BOOKING_COLUMNS)
    )
    editor_version = st.session_state.get("manual_hall_editor_version", 0)
    manual_editor = st.data_editor(
        manual_source,
        hide_index=True,
        width="stretch",
        num_rows="dynamic" if access.can_edit_pairings else "fixed",
        disabled=not access.can_edit_pairings,
        key=f"manual_hall_editor_{editor_version}",
        column_config={
            "Sportstätte": st.column_config.SelectboxColumn(
                options=[
                    HALL_BOOKING_DISPLAY_NAMES[hall_number]
                    for hall_number in sorted(MANUAL_HALL_NUMBERS)
                ],
                required=True,
                width="large",
            ),
            "Datum": st.column_config.DateColumn(
                format="DD.MM.YYYY",
                min_value=date_from,
                max_value=date_to,
                required=True,
                width="medium",
            ),
            "Von": st.column_config.TimeColumn(
                format="HH:mm", required=True, width="small"
            ),
            "Bis": st.column_config.TimeColumn(
                format="HH:mm", required=True, width="small"
            ),
        },
    )
    st.caption(
        "Erlaubt sind Samstage, Sonntage, bayerische Feiertage und einzelne "
        "Wochentage, an denen laut Spielplan ein Spiel in der gewählten Halle "
        "stattfindet."
    )

    deletion_confirmed = True
    if len(manual_editor) < len(manual_source):
        deletion_confirmed = st.checkbox(
            "Das Entfernen gespeicherter manueller Buchungen bestätigen"
        )
    if st.button(
        "Manuelle Buchungen speichern",
        type="primary",
        disabled=(
            not access.can_edit_pairings
            or hall_booking_store is None
            or not deletion_confirmed
        ),
    ):
        try:
            manual_bookings = manual_bookings_from_editor(
                pd.DataFrame(manual_editor), date_from, date_to
            )
            hall_booking_store.save_manual_bookings(
                SEASON,
                manual_bookings,
                date_from,
                date_to,
                access.display_name or access.object_id,
            )
            st.session_state["manual_hall_editor_version"] = editor_version + 1
            st.session_state["hall_booking_notice"] = (
                f"{len(manual_bookings)} manuelle Buchung(en) wurden gespeichert."
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("Die manuellen Buchungen konnten nicht gespeichert werden.")

def show_guide_page() -> None:
    st.header("Anleitung")
    show_core_metrics()
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
        "unnötig lange Buchungszeiten werden getrennt ausgewiesen. Für die "
        "Sporthalle an der Seeleite des BSC Oberhausen können Buchungen auf der "
        "Seite Hallenbuchungen manuell gepflegt werden. Die Übersicht zeigt nur "
        "Samstage, Sonntage und bayerische Feiertage sowie einzelne Wochentage "
        "mit einem Spiel in der jeweiligen Halle. Kostensätze und "
        "personenbezogene Buchungsdaten werden nicht verarbeitet."
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
            show_hall_booking_page,
            title="Hallenbuchungen",
            icon=":material/event_available:",
            url_path="hallenbuchungen",
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
uploaded = None
if page.title == "Spielplanprüfung":
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

    schedule = combined_schedule(main_schedule)
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
hall_booking_store = None
stored_hall_bookings: StoredHallBookings | None = None
stored_manual_hall_bookings: StoredHallBookings | None = None
hall_booking_storage_error = ""
if connection_string:
    try:
        hall_booking_store = create_hall_booking_store(
            connection_string,
            configured_value(
                "AZURE_HALL_BOOKING_CONTAINER_NAME", "hall-bookings"
            ),
        )
        stored_hall_bookings = hall_booking_store.latest_bookings(SEASON)
        stored_manual_hall_bookings = hall_booking_store.latest_manual_bookings(
            SEASON
        )
    except Exception as exc:
        hall_booking_storage_error = str(exc)

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

