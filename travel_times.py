from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re
from zoneinfo import ZoneInfo

import httpx


ROUTES_ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"


class GoogleRoutesError(RuntimeError):
    """A Google Routes request could not be completed."""


@dataclass(frozen=True)
class RouteEstimate:
    google_minutes: int
    planning_minutes: int
    distance_meters: int


class GoogleRoutesClient:
    def __init__(
        self,
        api_key: str,
        safety_percent: int = 15,
        transfer_buffer_minutes: int = 10,
        http_client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.safety_percent = safety_percent
        self.transfer_buffer_minutes = transfer_buffer_minutes
        self.http_client = http_client or httpx.Client(timeout=20.0)

    def compute_route(
        self,
        origin: str,
        destination: str,
        departure_time: datetime | None = None,
    ) -> RouteEstimate:
        if not origin.strip() or not destination.strip():
            raise ValueError("Start und Ziel müssen angegeben werden.")

        body: dict[str, object] = {
            "origin": {"address": _german_address(origin)},
            "destination": {"address": _german_address(destination)},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
            "trafficModel": "PESSIMISTIC",
            "languageCode": "de-DE",
            "units": "METRIC",
        }
        utc_departure = _future_departure(departure_time)
        body["departureTime"] = utc_departure.isoformat().replace("+00:00", "Z")

        try:
            response = self.http_client.post(
                ROUTES_ENDPOINT,
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
                },
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GoogleRoutesError(
                "Die Fahrzeit konnte nicht bei Google Maps abgefragt werden."
            ) from exc

        routes = data.get("routes", [])
        if not routes:
            raise GoogleRoutesError("Google Maps hat keine Fahrtroute gefunden.")

        route = routes[0]
        seconds = _duration_seconds(str(route.get("duration", "")))
        google_minutes = max(1, math.ceil(seconds / 60))
        planning_minutes = _round_up_to_five(
            google_minutes * (1 + self.safety_percent / 100)
            + self.transfer_buffer_minutes
        )
        return RouteEstimate(
            google_minutes=google_minutes,
            planning_minutes=planning_minutes,
            distance_meters=int(route.get("distanceMeters", 0)),
        )


def _duration_seconds(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)s", value)
    if not match:
        raise GoogleRoutesError("Google Maps hat keine gültige Fahrzeit geliefert.")
    return float(match.group(1))


def _round_up_to_five(value: float) -> int:
    return int(math.ceil(value / 5) * 5)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("Europe/Berlin"))
    return value.astimezone(timezone.utc)


def _future_departure(value: datetime | None) -> datetime:
    now = datetime.now(timezone.utc)
    departure = _as_utc(value) if value is not None else now + timedelta(days=7)
    while departure <= now:
        departure += timedelta(days=7)
    return departure


def _german_address(value: str) -> str:
    address = value.strip()
    if "deutschland" not in address.casefold():
        return f"{address}, Deutschland"
    return address
