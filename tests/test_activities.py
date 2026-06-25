from __future__ import annotations

import unittest
from zoneinfo import ZoneInfo

from ingest.activities import (
    canonical_activity_type,
    normalize_suunto_activity,
    normalize_withings_activity,
)


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

    def test_normalizes_naive_withings_and_utc_suunto_to_configured_timezone(self) -> None:
        timezone = ZoneInfo("Asia/Tokyo")
        withings = normalize_withings_activity(
            {
                "start_time": "2026-06-24T14:14:21",
                "duration_min": "140.13",
                "activity_type": "walk",
            },
            timezone,
        )
        suunto = normalize_suunto_activity(
            {
                "start_time": "2026-06-24T05:14:21+00:00",
                "duration_min": "68.69",
                "activity_type": "walk",
            },
            timezone,
        )

        self.assertEqual(withings.start_time, "2026-06-24T14:14:21+09:00")
        self.assertEqual(suunto.start_time, "2026-06-24T14:14:21+09:00")


if __name__ == "__main__":
    unittest.main()
