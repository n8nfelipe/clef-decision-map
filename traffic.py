"""Consulta de trânsito na rota Brasília -> Valparaíso de Goiás."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
DEFAULT_ORIGIN = "Brasília, DF, Brazil"
DEFAULT_DESTINATION = "Valparaíso de Goiás, GO, Brazil"


class GoogleMapsError(RuntimeError):
    pass


def _duration_seconds(value: str | None) -> float:
    if not isinstance(value, str) or not value.endswith("s"):
        return 0.0
    try:
        return float(value[:-1])
    except ValueError as exc:
        raise GoogleMapsError(f"Duração inválida recebida do Google: {value!r}") from exc


def decode_polyline(encoded: str) -> list[dict[str, float]]:
    """Decodifica a Polyline Encoding do Google em latitude/longitude."""
    points: list[dict[str, float]] = []
    index = 0
    latitude = 0
    longitude = 0

    while index < len(encoded):
        for coordinate in ("latitude", "longitude"):
            result = 0
            shift = 0
            while True:
                if index >= len(encoded):
                    raise GoogleMapsError("Polyline incompleta recebida do Google.")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if coordinate == "latitude":
                latitude += delta
            else:
                longitude += delta
        points.append({"latitude": latitude / 1e5, "longitude": longitude / 1e5})

    return points


def _maps_url(latitude: float, longitude: float) -> str:
    query = urllib.parse.urlencode({"api": "1", "query": f"{latitude},{longitude}"})
    return f"https://www.google.com/maps/search/?{query}"


def _post_route(body: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        ROUTES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": ",".join(
                (
                    "routes.duration",
                    "routes.staticDuration",
                    "routes.distanceMeters",
                    "routes.polyline.encodedPolyline",
                    "routes.travelAdvisory.speedReadingIntervals",
                )
            ),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:1000]
        raise GoogleMapsError(f"Google Routes respondeu HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise GoogleMapsError(f"Falha de conexão com Google Routes: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise GoogleMapsError("Google Routes devolveu uma resposta que não é JSON válido.") from exc

    if not isinstance(payload.get("routes"), list) or not payload["routes"]:
        raise GoogleMapsError(f"Google Routes não retornou uma rota: {payload}")
    return payload["routes"][0]


def get_traffic_route(
    origin: str = DEFAULT_ORIGIN,
    destination: str = DEFAULT_DESTINATION,
) -> dict:
    """Consulta uma rota com trânsito e retorna os trechos críticos com coordenadas."""
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise GoogleMapsError(
            "Configure GOOGLE_MAPS_API_KEY no .env e habilite a Google Routes API."
        )

    route = _post_route(
        {
            "origin": {"address": origin},
            "destination": {"address": destination},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
            "computeAlternativeRoutes": False,
            "languageCode": "pt-BR",
            "units": "METRIC",
            "polylineQuality": "HIGH_QUALITY",
            "extraComputations": ["TRAFFIC_ON_POLYLINE"],
        },
        api_key,
    )
    encoded_polyline = route.get("polyline", {}).get("encodedPolyline", "")
    polyline = decode_polyline(encoded_polyline) if encoded_polyline else []
    intervals = route.get("travelAdvisory", {}).get("speedReadingIntervals", [])
    critical_points = []

    for interval_number, interval in enumerate(intervals, start=1):
        speed = interval.get("speed", "UNKNOWN")
        if speed not in {"SLOW", "TRAFFIC_JAM"}:
            continue
        start_index = int(interval.get("startPolylinePointIndex", 0))
        end_index = int(interval.get("endPolylinePointIndex", start_index + 1))
        if not polyline:
            continue
        start_index = max(0, min(start_index, len(polyline) - 1))
        end_index = max(start_index, min(end_index, len(polyline) - 1))
        midpoint = polyline[(start_index + end_index) // 2]
        severity = "crítica" if speed == "TRAFFIC_JAM" else "alta"
        critical_points.append(
            {
                "id": f"p{interval_number}",
                "severity": severity,
                "speed": speed,
                "start_polyline_point": start_index,
                "end_polyline_point": end_index,
                "latitude": midpoint["latitude"],
                "longitude": midpoint["longitude"],
                "maps_url": _maps_url(midpoint["latitude"], midpoint["longitude"]),
            }
        )

    duration_seconds = _duration_seconds(route.get("duration"))
    static_duration_seconds = _duration_seconds(route.get("staticDuration"))
    return {
        "origin": origin,
        "destination": destination,
        "distance_meters": route.get("distanceMeters", 0),
        "duration_seconds": duration_seconds,
        "static_duration_seconds": static_duration_seconds,
        "delay_seconds": max(0.0, duration_seconds - static_duration_seconds),
        "critical_points": critical_points,
        "traffic_intervals": intervals,
        "polyline": encoded_polyline,
        "maps_url": (
            "https://www.google.com/maps/dir/?api=1&"
            + urllib.parse.urlencode({"origin": origin, "destination": destination})
        ),
    }
