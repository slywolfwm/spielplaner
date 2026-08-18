from datetime import datetime, timezone
import json

import httpx
import pytest

from travel_times import AzureMapsClient, AzureMapsError


def test_azure_route_geocodes_and_uses_traffic_with_conservative_rounding():
    captured: dict[str, object] = {"geocodes": []}
    coordinates = iter(((11.1, 47.7), (11.5, 47.8)))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/geocode":
            captured["geocodes"].append(dict(request.url.params))
            longitude, latitude = next(coordinates)
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "geometry": {
                                "type": "Point",
                                "coordinates": [longitude, latitude],
                            },
                            "properties": {"geocodePoints": []},
                        }
                    ]
                },
            )
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"Cache-Control": "public, max-age=3600"},
            json={
                "features": [
                    {
                        "properties": {
                            "type": "RoutePath",
                            "durationTrafficInSeconds": 3601,
                            "distanceInMeters": 42000,
                        }
                    }
                ]
            },
        )

    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    client = AzureMapsClient(
        "secret-key",
        safety_percent=15,
        transfer_buffer_minutes=10,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        now_provider=lambda: now,
    )

    result = client.compute_route(
        "Murnau, James-Loeb-Halle",
        "Schongau, Lechsporthalle",
        datetime(2026, 10, 11, 14, 0),
    )

    body = captured["body"]
    assert body["optimizeRoute"] == "fastestWithTraffic"
    assert body["departAt"] == "2026-10-11T12:00:00+00:00"
    assert body["features"][0]["geometry"]["coordinates"] == [11.1, 47.7]
    assert captured["geocodes"] == [
        {
            "api-version": "2025-01-01",
            "top": "1",
            "locality": "Murnau",
            "countryRegion": "DE",
            "bbox": "8.8,47.2,13.9,50.6",
        },
        {
            "api-version": "2025-01-01",
            "top": "1",
            "locality": "Schongau",
            "countryRegion": "DE",
            "bbox": "8.8,47.2,13.9,50.6",
        },
    ]
    assert captured["headers"]["subscription-key"] == "secret-key"
    assert result.source_minutes == 61
    assert result.planning_minutes == 85
    assert result.distance_meters == 42000
    assert result.valid_until == datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc)


def test_known_home_hall_uses_exact_street_address():
    captured: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/geocode":
            captured.append(dict(request.url.params))
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "geometry": {
                                "type": "Point",
                                "coordinates": [11.14, 47.84],
                            },
                            "properties": {"geocodePoints": []},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {
                            "durationInSeconds": 600,
                            "distanceInMeters": 5000,
                        }
                    }
                ]
            },
        )

    client = AzureMapsClient(
        "secret-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.compute_route("Weilheim, Jahnhalle", "Weilheim, Am Hardt")

    assert captured == [
        {
            "api-version": "2025-01-01",
            "top": "1",
            "query": "Jahnstrasse 2, 82362 Weilheim in Oberbayern, Deutschland",
        },
        {
            "api-version": "2025-01-01",
            "top": "1",
            "query": "Hardtkapellenstrasse 2, 82362 Weilheim in Oberbayern, Deutschland",
        },
    ]


def test_azure_route_failure_does_not_expose_subscription_key():
    client = AzureMapsClient(
        "do-not-leak",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(403, text="denied")
            )
        ),
    )

    with pytest.raises(AzureMapsError) as error:
        client.compute_route("Start", "Ziel")

    assert "do-not-leak" not in str(error.value)
