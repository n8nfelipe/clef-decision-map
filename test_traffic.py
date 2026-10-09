import os
import unittest
from unittest.mock import patch

from traffic import decode_polyline, get_traffic_route


class TrafficRouteTest(unittest.TestCase):
    def test_decodes_google_polyline(self):
        points = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")

        self.assertEqual(len(points), 3)
        self.assertAlmostEqual(points[0]["latitude"], 38.5)
        self.assertAlmostEqual(points[0]["longitude"], -120.2)

    @patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": "test-key"}, clear=False)
    @patch(
        "traffic._post_route",
        return_value={
            "distanceMeters": 1000,
            "duration": "100s",
            "staticDuration": "50s",
            "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
            "travelAdvisory": {
                "speedReadingIntervals": [
                    {"endPolylinePointIndex": 1, "speed": "NORMAL"},
                    {"startPolylinePointIndex": 1, "endPolylinePointIndex": 2, "speed": "TRAFFIC_JAM"},
                ]
            },
        },
    )
    def test_extracts_critical_point_from_traffic_interval(self, _post_route):
        route = get_traffic_route("Brasília, DF", "Valparaíso de Goiás, GO")

        self.assertEqual(route["delay_seconds"], 50)
        self.assertEqual(len(route["critical_points"]), 1)
        self.assertEqual(route["critical_points"][0]["speed"], "TRAFFIC_JAM")
        self.assertIn("google.com/maps", route["critical_points"][0]["maps_url"])


if __name__ == "__main__":
    unittest.main()
