from __future__ import annotations

import unittest

from ingest.activities import canonical_activity_type, normalize_withings_activity


class ActivitiesTest(unittest.TestCase):
    def test_normalizes_withings_activity(self) -> None:
        activity = normalize_withings_activity(
            {
                "source_id": "w1",
                "start_time": "2026-05-30T07:00:00+00:00",
                "end_time": "2026-05-30T07:30:00+00:00",
                "duration_min": "30.00",
                "distance_km": "2.00",
                "activity_type": "walking",
                "raw_type": "walking",
                "name": "Morning Walk",
            }
        )

        self.assertEqual(activity.source, "withings")
        self.assertEqual(activity.source_id, "w1")
        self.assertEqual(activity.activity_type, "walk")
        self.assertEqual(activity.distance_km, 2.0)

    def test_canonicalizes_supported_activity_types(self) -> None:
        self.assertEqual(canonical_activity_type("indoor walking"), "walk")
        self.assertEqual(canonical_activity_type("swimming"), "swim")
        self.assertEqual(canonical_activity_type("running"), "run")
        self.assertEqual(canonical_activity_type("treadmill"), "run")
        self.assertEqual(canonical_activity_type("cycling"), "ride")


if __name__ == "__main__":
    unittest.main()
