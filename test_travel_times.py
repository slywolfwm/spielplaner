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
            captured["geocodes"].append(request.url.params["query"])
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
        "Murnau, James-Loeb-Halle, Deutschland",
        "Schongau, Lechsporthalle, Deutschland",
    ]
    assert captured["headers"]["subscription-key"] == "secret-key"
    assert result.source_minutes == 61
    assert result.planning_minutes == 85
    assert result.distance_meters == 42000
    assert result.valid_until == datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc)


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
