from datetime import date

import httpx

from omoc import OmocClient


def test_omoc_keeps_only_handball_bookings_for_target_rooms():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["datumvon"] == "20092026"
        assert request.url.params["datumbis"] == "20092026"
        return httpx.Response(
            200,
            json={
                "response_code": 200,
                "data": [
                    {
                        "buchungsnummer": "1",
                        "name_firma": "Handball",
                        "datum_von": "Sun, 20 Sep 2026 00:00:00 GMT",
                        "datum_bis": "Sun, 20 Sep 2026 00:00:00 GMT",
                        "uhrzeit_von": "16:30",
                        "uhrzeit_bis": "20:00",
                        "raumliste_ids": "7702,7703,7710,7730,9999",
                    },
                    {
                        "buchungsnummer": "2",
                        "name_firma": "Andere Abteilung",
                        "datum_von": "Sun, 20 Sep 2026 00:00:00 GMT",
                        "datum_bis": "Sun, 20 Sep 2026 00:00:00 GMT",
                        "uhrzeit_von": "16:30",
                        "uhrzeit_bis": "20:00",
                        "raumliste_ids": "7707,7708,7709,7725",
                    },
                ],
            },
        )

    client = OmocClient(
        "https://example.invalid/buchungen/",
        "user",
        "password",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    bookings = client.fetch_bookings(date(2026, 9, 20), date(2026, 9, 20))

    assert len(bookings) == 1
    assert bookings.iloc[0]["Raum-IDs"] == frozenset(
        {"7702", "7703", "7710", "7730"}
    )


def test_omoc_404_without_records_is_an_empty_valid_result():
    client = OmocClient(
        "https://example.invalid/buchungen/",
        "user",
        "password",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    404,
                    json={
                        "response_code": 404,
                        "response_desc": "Keine Datensätze gefunden",
                        "data": [],
                    },
                )
            )
        ),
    )

    assert client.fetch_bookings(date(2026, 9, 20), date(2026, 9, 20)).empty
