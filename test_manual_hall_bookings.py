from datetime import date, time

import pandas as pd
import pytest

from manual_hall_bookings import bookings_from_editor


HALL_BY_NAME = {"Oberhausen": "270141"}
ROOM_BY_HALL = {"270141": "manual:270141"}
DATE_FROM = date(2026, 9, 1)
DATE_TO = date(2027, 4, 30)


def editor_row(day: date, starts: time = time(10), ends: time = time(18)):
    return {
        "Sportstätte": "Oberhausen",
        "Datum": day,
        "Von": starts,
        "Bis": ends,
    }


def test_manual_booking_editor_creates_persistable_booking():
    editor = pd.DataFrame([editor_row(date(2026, 9, 19))])

    bookings = bookings_from_editor(
        editor, HALL_BY_NAME, ROOM_BY_HALL, DATE_FROM, DATE_TO, set()
    )

    assert bookings.iloc[0]["Buchungsnummer"] == "MAN-270141-20260919-1000"
    assert bookings.iloc[0]["Raum-IDs"] == frozenset({"manual:270141"})


def test_manual_booking_editor_accepts_weekday_only_with_matching_game():
    day = date(2026, 9, 23)
    editor = pd.DataFrame([editor_row(day)])

    bookings = bookings_from_editor(
        editor,
        HALL_BY_NAME,
        ROOM_BY_HALL,
        DATE_FROM,
        DATE_TO,
        {(day, "270141")},
    )
    assert len(bookings) == 1

    with pytest.raises(ValueError, match="Wochentage mit einem Spiel"):
        bookings_from_editor(
            editor, HALL_BY_NAME, ROOM_BY_HALL, DATE_FROM, DATE_TO, set()
        )


def test_manual_booking_editor_rejects_invalid_times_and_duplicates():
    invalid_time = pd.DataFrame(
        [editor_row(date(2026, 9, 19), time(18), time(10))]
    )
    with pytest.raises(ValueError, match="Ende muss nach dem Beginn"):
        bookings_from_editor(
            invalid_time, HALL_BY_NAME, ROOM_BY_HALL, DATE_FROM, DATE_TO, set()
        )

    duplicate = pd.DataFrame(
        [editor_row(date(2026, 9, 19)), editor_row(date(2026, 9, 19))]
    )
    with pytest.raises(ValueError, match="doppelt"):
        bookings_from_editor(
            duplicate, HALL_BY_NAME, ROOM_BY_HALL, DATE_FROM, DATE_TO, set()
        )
