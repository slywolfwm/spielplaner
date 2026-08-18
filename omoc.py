from __future__ import annotations

from datetime import date, datetime

import httpx
import pandas as pd


BOOKING_COLUMNS = [
    "Buchungsnummer",
    "Buchungsbeginn",
    "Buchungsende",
    "Raum-IDs",
]
TARGET_ROOM_IDS = frozenset(
    {"7702", "7703", "7710", "7730", "7707", "7708", "7709", "7725"}
)


class OmocError(RuntimeError):
    """An OMOC booking request could not be completed."""


class OmocClient:
    def __init__(
        self,
        bookings_url: str,
        username: str,
        password: str,
        http_client: httpx.Client | None = None,
    ):
        self.bookings_url = bookings_url.partition("?")[0]
        self.username = username
        self.password = password
        self.http_client = http_client or httpx.Client(timeout=60.0)

    def fetch_bookings(self, date_from: date, date_to: date) -> pd.DataFrame:
        if date_to < date_from:
            raise ValueError("Das OMOC-Enddatum liegt vor dem Startdatum.")
        try:
            response = self.http_client.get(
                self.bookings_url,
                params={
                    "datumvon": date_from.strftime("%d%m%Y"),
                    "datumbis": date_to.strftime("%d%m%Y"),
                },
                auth=(self.username, self.password),
            )
            payload = response.json()
            if response.status_code == 404 and int(
                payload.get("response_code", 0)
            ) == 404:
                return pd.DataFrame(columns=BOOKING_COLUMNS)
            response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            raise OmocError("Die Hallenbuchungen konnten nicht aus OMOC geladen werden.") from exc

        if int(payload.get("response_code", 0)) != 200:
            raise OmocError("OMOC hat die Buchungsabfrage abgelehnt.")
        rows = []
        for item in payload.get("data", []):
            if str(item.get("name_firma", "")).strip().casefold() != "handball":
                continue
            start = _booking_datetime(item.get("datum_von"), item.get("uhrzeit_von"))
            end = _booking_datetime(item.get("datum_bis"), item.get("uhrzeit_bis"))
            if end <= start:
                continue
            room_ids = frozenset(
                value.strip()
                for value in str(item.get("raumliste_ids", "")).split(",")
                if value.strip()
            )
            room_ids = room_ids.intersection(TARGET_ROOM_IDS)
            if not room_ids:
                continue
            rows.append(
                {
                    "Buchungsnummer": str(item.get("buchungsnummer", "")),
                    "Buchungsbeginn": start,
                    "Buchungsende": end,
                    "Raum-IDs": room_ids,
                }
            )
        return pd.DataFrame(rows, columns=BOOKING_COLUMNS)


def _booking_datetime(raw_date: object, raw_time: object) -> datetime:
    parsed_date = pd.to_datetime(raw_date, utc=True, errors="raise").date()
    parsed_time = datetime.strptime(str(raw_time), "%H:%M").time()
    return datetime.combine(parsed_date, parsed_time)
