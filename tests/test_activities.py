from __future__ import annotations

import unittest

from life_log_sync.activities import NormalizedActivity, coverage_summary, deduplicate_activities, primary_activities


class ActivitiesTest(unittest.TestCase):
    def test_deduplicates_overlapping_outdoor_walk_and_prefers_strava(self) -> None:
        activities = deduplicate_activities(
            [
                NormalizedActivity(
                    source="withings",
                    source_id="w1",
                    start_time="2026-05-30T07:05:00+00:00",
                    end_time="2026-05-30T07:35:00+00:00",
                    duration_min=30,
                    distance_km=2.0,
                    activity_type="walk",
                    raw_type="walk",
                ),
                NormalizedActivity(
                    source="strava",
                    source_id="s1",
                    start_time="2026-05-30T07:00:00+00:00",
                    end_time="2026-05-30T07:32:00+00:00",
                    duration_min=32,
                    distance_km=2.1,
                    activity_type="walk",
                    raw_type="Walk",
                ),
            ]
        )

        primary = primary_activities(activities)

        self.assertEqual(len(primary), 1)
        self.assertEqual(primary[0].source, "strava")
        self.assertEqual(coverage_summary(activities)["deduplicated_pairs"], "1")

    def test_prefers_strava_for_overlapping_walk_when_withings_has_no_indoor_flag(self) -> None:
        activities = deduplicate_activities(
            [
                NormalizedActivity(
                    source="strava",
                    source_id="s1",
                    start_time="2026-05-30T07:00:00+00:00",
                    end_time="2026-05-30T07:30:00+00:00",
                    duration_min=30,
                    distance_km=2.0,
                    activity_type="walk",
                    raw_type="Walk",
                ),
                NormalizedActivity(
                    source="withings",
                    source_id="w1",
                    start_time="2026-05-30T07:00:00+00:00",
                    end_time="2026-05-30T07:31:00+00:00",
                    duration_min=31,
                    distance_km=2.0,
                    activity_type="walk",
                    raw_type="indoor walking",
                ),
            ]
        )

        primary = primary_activities(activities)

        self.assertEqual(len(primary), 1)
        self.assertEqual(primary[0].source, "strava")

    def test_keeps_activities_when_distance_differs_too_much(self) -> None:
        activities = deduplicate_activities(
            [
                NormalizedActivity(
                    source="strava",
                    source_id="s1",
                    start_time="2026-05-30T07:00:00+00:00",
                    end_time="2026-05-30T07:30:00+00:00",
                    duration_min=30,
                    distance_km=2.0,
                    activity_type="walk",
                    raw_type="Walk",
                ),
                NormalizedActivity(
                    source="withings",
                    source_id="w1",
                    start_time="2026-05-30T07:05:00+00:00",
                    end_time="2026-05-30T07:34:00+00:00",
                    duration_min=29,
                    distance_km=3.0,
                    activity_type="walk",
                    raw_type="walk",
                ),
            ]
        )

        self.assertEqual(len(primary_activities(activities)), 2)

    def test_keeps_incompatible_activity_types(self) -> None:
        activities = deduplicate_activities(
            [
                NormalizedActivity(
                    source="strava",
                    source_id="s1",
                    start_time="2026-05-30T07:00:00+00:00",
                    end_time="2026-05-30T07:30:00+00:00",
                    duration_min=30,
                    distance_km=2.0,
                    activity_type="walk",
                    raw_type="Walk",
                ),
                NormalizedActivity(
                    source="withings",
                    source_id="w1",
                    start_time="2026-05-30T07:05:00+00:00",
                    end_time="2026-05-30T07:34:00+00:00",
                    duration_min=29,
                    distance_km=2.0,
                    activity_type="swim",
                    raw_type="swimming",
                ),
            ]
        )

        self.assertEqual(len(primary_activities(activities)), 2)


if __name__ == "__main__":
    unittest.main()
