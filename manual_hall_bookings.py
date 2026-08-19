from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd

from booking_calendar import is_visible_booking_day
from omoc import BOOKING_COLUMNS


def bookings_from_editor(
    editor: pd.DataFrame,
    hall_by_name: dict[str, str],
    room_id_by_hall: dict[str, str],
    date_from: date,
    date_to: date,
    game_days: set[tuple[date, str]],
) -> pd.DataFrame:
    rows = []
    seen = set()
    for row_number, (_, values) in enumerate(editor.iterrows(), start=1):
        raw_values = [
            values.get("Sportstätte"),
            values.get("Datum"),
            values.get("Von"),
            values.get("Bis"),
        ]
        if all(_value_missing(value) for value in raw_values):
            continue
        if any(_value_missing(value) for value in raw_values):
            raise ValueError(f"Zeile {row_number}: Bitte alle Felder ausfüllen.")

        hall_name = str(values["Sportstätte"])
        hall_number = hall_by_name.get(hall_name)
        if hall_number is None:
            raise ValueError(f"Zeile {row_number}: Die Sportstätte ist ungültig.")
        try:
            booking_date = pd.Timestamp(values["Datum"]).date()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Zeile {row_number}: Das Datum ist ungültig.") from exc
        try:
            starts_at = datetime.combine(booking_date, _as_time(values["Von"]))
            ends_at = datetime.combine(booking_date, _as_time(values["Bis"]))
        except ValueError as exc:
            raise ValueError(f"Zeile {row_number}: Die Uhrzeit ist ungültig.") from exc
        if not date_from <= booking_date <= date_to:
            raise ValueError(
                f"Zeile {row_number}: Das Datum liegt außerhalb der Saison."
            )
        if ends_at <= starts_at:
            raise ValueError(
                f"Zeile {row_number}: Das Ende muss nach dem Beginn liegen."
            )
        if not is_visible_booking_day(booking_date, hall_number, game_days):
            raise ValueError(
                f"Zeile {row_number}: Zulässig sind Samstage, Sonntage, "
                "Feiertage und Wochentage mit einem Spiel in dieser Halle."
            )
        duplicate_key = (hall_number, booking_date, starts_at.time(), ends_at.time())
        if duplicate_key in seen:
            raise ValueError(f"Zeile {row_number}: Diese Buchung ist doppelt.")
        seen.add(duplicate_key)
        rows.append(
            {
                "Buchungsnummer": (
                    f"MAN-{hall_number}-{booking_date:%Y%m%d}-{starts_at:%H%M}"
                ),
                "Buchungsbeginn": starts_at,
                "Buchungsende": ends_at,
                "Raum-IDs": frozenset({room_id_by_hall[hall_number]}),
            }
        )
    return pd.DataFrame(rows, columns=BOOKING_COLUMNS)


def _value_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return not str(value).strip()


def _as_time(value: object) -> time:
    if isinstance(value, time):
        return value
    try:
        return pd.Timestamp(str(value)).time()
    except (TypeError, ValueError) as exc:
        raise ValueError("Die Uhrzeit ist ungültig.") from exc
