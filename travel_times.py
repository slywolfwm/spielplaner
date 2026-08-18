from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import math
from typing import Callable
from zoneinfo import ZoneInfo

import httpx


AZURE_MAPS_ENDPOINT = "https://atlas.microsoft.com"
AZURE_MAPS_API_VERSION = "2025-01-01"
MAX_CACHE_AGE = timedelta(days=180)
BAVARIA_BBOX = "8.8,47.2,13.9,50.6"
KNOWN_HALL_ADDRESSES = {
    "huglfing, sporthalle an der seeleite": "Seeleite, 82386 Huglfing",
    "peißenberg, glückauf-halle": "Alpspitzstrasse 11, 82380 Peißenberg",
    "weilheim, am hardt": "Hardtkapellenstrasse 2, 82362 Weilheim in Oberbayern",
    "weilheim, jahnhalle": "Jahnstrasse 2, 82362 Weilheim in Oberbayern",
}


class AzureMapsError(RuntimeError):
    """An Azure Maps request could not be completed."""


@dataclass(frozen=True)
class RouteEstimate:
    source_minutes: int
    planning_minutes: int
    distance_meters: int
    valid_until: datetime


class AzureMapsClient:
    def __init__(
        self,
        subscription_key: str,
        safety_percent: int = 15,
        transfer_buffer_minutes: int = 10,
        http_client: httpx.Client | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.subscription_key = subscription_key
        self.safety_percent = safety_percent
        self.transfer_buffer_minutes = transfer_buffer_minutes
        self.http_client = http_client or httpx.Client(timeout=20.0)
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def compute_route(
        self,
        origin: str,
        destination: str,
        departure_time: datetime | None = None,
    ) -> RouteEstimate:
        if not origin.strip() or not destination.strip():
            raise ValueError("Start und Ziel müssen angegeben werden.")

        origin_coordinates = self._geocode(origin)
        destination_coordinates = self._geocode(destination)
        body = {
            "type": "FeatureCollection",
            "features": [
                _waypoint(origin_coordinates, 0),
                _waypoint(destination_coordinates, 1),
            ],
            "departAt": _future_departure(
                departure_time, self.now_provider()
            ).isoformat(),
            "optimizeRoute": "fastestWithTraffic",
            "routeOutputOptions": ["itinerary"],
            "travelMode": "driving",
        }

        try:
            response = self.http_client.post(
                f"{AZURE_MAPS_ENDPOINT}/route/directions",
                params={"api-version": AZURE_MAPS_API_VERSION},
                headers=self._headers("application/geo+json"),
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AzureMapsError(
                "Die Fahrzeit konnte nicht bei Azure Maps abgefragt werden."
            ) from exc

        seconds, distance_meters = _route_summary(data)
        source_minutes = max(1, math.ceil(seconds / 60))
        planning_minutes = _round_up_to_five(
            source_minutes * (1 + self.safety_percent / 100)
            + self.transfer_buffer_minutes
        )
        return RouteEstimate(
            source_minutes=source_minutes,
            planning_minutes=planning_minutes,
            distance_meters=distance_meters,
            valid_until=_cache_expiry(response, self.now_provider()),
        )

    def _geocode(self, address: str) -> tuple[float, float]:
        known_address = KNOWN_HALL_ADDRESSES.get(_hall_lookup_key(address))
        params: dict[str, object] = {
            "api-version": AZURE_MAPS_API_VERSION,
            "top": 1,
        }
        if known_address:
            params["query"] = _german_address(known_address)
        else:
            # nuLiga exports a hall label rather than a street address. Resolving
            # the locality within Bavaria avoids ambiguous names such as Weilheim.
            params.update(
                {
                    "locality": _hall_locality(address),
                    "countryRegion": "DE",
                    "bbox": BAVARIA_BBOX,
                }
            )
        try:
            response = self.http_client.get(
                f"{AZURE_MAPS_ENDPOINT}/geocode",
                params=params,
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AzureMapsError(
                "Eine Hallenadresse konnte nicht über Azure Maps gefunden werden."
            ) from exc

        features = data.get("features", [])
        if not features:
            raise AzureMapsError("Azure Maps hat die Hallenadresse nicht gefunden.")
        feature = features[0]
        for point in feature.get("properties", {}).get("geocodePoints", []):
            if "Route" in point.get("usageTypes", []):
                return _coordinates(point.get("geometry", {}))
        return _coordinates(feature.get("geometry", {}))

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "subscription-key": self.subscription_key,
            "Accept-Language": "de-DE",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers


def _waypoint(coordinates: tuple[float, float], index: int) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": list(coordinates)},
        "properties": {"pointIndex": index, "pointType": "waypoint"},
    }


def _coordinates(geometry: dict[str, object]) -> tuple[float, float]:
    values = geometry.get("coordinates", [])
    if not isinstance(values, list) or len(values) < 2:
        raise AzureMapsError("Azure Maps hat keine gültigen Koordinaten geliefert.")
    return float(values[0]), float(values[1])


def _route_summary(data: object) -> tuple[int, int]:
    candidates: list[tuple[int, int]] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            duration = value.get("durationTrafficInSeconds") or value.get(
                "durationInSeconds"
            )
            distance = value.get("distanceInMeters")
            if duration is not None and distance is not None:
                candidates.append((int(duration), int(distance)))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    if not candidates:
        raise AzureMapsError("Azure Maps hat keine gültige Fahrtroute geliefert.")
    return max(candidates, key=lambda item: (item[1], item[0]))


def _cache_expiry(response: httpx.Response, now: datetime) -> datetime:
    maximum = now + MAX_CACHE_AGE
    cache_control = response.headers.get("cache-control", "")
    for directive in cache_control.split(","):
        name, _, value = directive.strip().partition("=")
        if name.casefold() in {"no-cache", "no-store"}:
            maximum = now
        if name.casefold() == "max-age" and value.isdigit():
            maximum = min(maximum, now + timedelta(seconds=int(value)))

    expires = response.headers.get("expires")
    if expires:
        try:
            maximum = min(maximum, parsedate_to_datetime(expires).astimezone(timezone.utc))
        except (TypeError, ValueError):
            pass
    return maximum


def _round_up_to_five(value: float) -> int:
    return int(math.ceil(value / 5) * 5)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("Europe/Berlin"))
    return value.astimezone(timezone.utc)


def _future_departure(value: datetime | None, now: datetime) -> datetime:
    departure = _as_utc(value) if value is not None else now + timedelta(days=7)
    while departure <= now:
        departure += timedelta(days=7)
    return departure


def _german_address(value: str) -> str:
    address = value.strip()
    if "deutschland" not in address.casefold():
        return f"{address}, Deutschland"
    return address


def _hall_locality(value: str) -> str:
    return value.split(",", 1)[0].strip()


def _hall_lookup_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())
