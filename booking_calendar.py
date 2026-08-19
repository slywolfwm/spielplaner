from __future__ import annotations

from datetime import date, timedelta


def bavarian_public_holidays(year: int) -> set[date]:
    easter = easter_sunday(year)
    return {
        date(year, 1, 1),
        date(year, 1, 6),
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        date(year, 5, 1),
        easter + timedelta(days=39),
        easter + timedelta(days=50),
        easter + timedelta(days=60),
        date(year, 8, 15),
        date(year, 10, 3),
        date(year, 11, 1),
        date(year, 12, 25),
        date(year, 12, 26),
    }


def is_visible_booking_day(
    day: date,
    hall_number: str,
    game_days: set[tuple[date, str]],
) -> bool:
    return (
        day.weekday() >= 5
        or day in bavarian_public_holidays(day.year)
        or (day, str(hall_number)) in game_days
    )


def easter_sunday(year: int) -> date:
    """Return Gregorian Easter Sunday using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)
