from datetime import datetime
import json

import httpx
import pytest

from travel_times import GoogleRoutesClient, GoogleRoutesError


def test_google_route_uses_pessimistic_traffic_and_conservative_rounding():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"routes": [{"duration": "3601s", "distanceMeters": 42000}]},
        )

    client = GoogleRoutesClient(
        "secret-key",
        safety_percent=15,
        transfer_buffer_minutes=10,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.compute_route(
        "Murnau, James-Loeb-Halle",
        "Schongau, Lechsporthalle",
        datetime(2026, 10, 11, 14, 0),
    )

    body = captured["body"]
    assert body["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"
    assert body["trafficModel"] == "PESSIMISTIC"
    assert body["departureTime"] == "2026-10-11T12:00:00Z"
    assert body["origin"]["address"].endswith(", Deutschland")
    assert captured["headers"]["x-goog-fieldmask"] == (
        "routes.duration,routes.distanceMeters"
    )
    assert result.google_minutes == 61
    assert result.planning_minutes == 85
    assert result.distance_meters == 42000


def test_google_route_failure_does_not_expose_api_key():
    client = GoogleRoutesClient(
        "do-not-leak",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(403, text="denied")
            )
        ),
    )

    with pytest.raises(GoogleRoutesError) as error:
        client.compute_route("Start", "Ziel")

    assert "do-not-leak" not in str(error.value)
