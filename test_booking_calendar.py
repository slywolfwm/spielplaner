from datetime import date

from booking_calendar import bavarian_public_holidays, is_visible_booking_day


def test_bavarian_holidays_include_movable_and_local_relevant_days():
    holidays = bavarian_public_holidays(2027)

    assert date(2027, 3, 26) in holidays  # Karfreitag
    assert date(2027, 5, 27) in holidays  # Fronleichnam
    assert date(2027, 8, 15) in holidays  # Mariä Himmelfahrt


def test_booking_day_filter_keeps_weekends_holidays_and_weekday_games():
    game_days = {(date(2026, 9, 23), "270141")}

    assert is_visible_booking_day(date(2026, 9, 19), "270141", game_days)
    assert is_visible_booking_day(date(2026, 12, 25), "270141", game_days)
    assert is_visible_booking_day(date(2026, 9, 23), "270141", game_days)
    assert not is_visible_booking_day(date(2026, 9, 22), "270141", game_days)
    assert not is_visible_booking_day(date(2026, 9, 23), "270461", game_days)
