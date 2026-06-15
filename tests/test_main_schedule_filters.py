import unittest

from main_harness import ConfigurableClient, make_app, run_main


def _apps():
    # Two running apps with distinct ids and names, so tests can verify that
    # schedule filters route by id while upgrade calls use name. RUNNING matches
    # what the real API requires for an upgrade (it rejects STOPPED apps).
    return [
        make_app("app-one", app_id="app-one-id", state="RUNNING"),
        make_app("app-two", app_id="app-two-id", state="RUNNING"),
    ]


class ScheduleFilterBehaviorTest(unittest.TestCase):
    def test_schedule_include_app_ids_limits_updates_by_app_id(self):
        result = run_main({"apps": _apps()}, {"SCHEDULE_INCLUDE_APP_IDS": "app-two-id"})

        self.assertEqual(result.client.upgraded_apps, ["app-two"])

    def test_schedule_exclude_app_ids_skips_matching_app_ids(self):
        result = run_main({"apps": _apps()}, {"SCHEDULE_EXCLUDE_APP_IDS": "app-one-id"})

        self.assertEqual(result.client.upgraded_apps, ["app-two"])

    def test_schedule_filters_use_id_not_name(self):
        # Passing a name where an id is expected matches nothing.
        result = run_main({"apps": _apps()}, {"SCHEDULE_INCLUDE_APP_IDS": "app-two"})

        self.assertEqual(result.client.upgraded_apps, [])

    def test_user_exclude_apps_still_applies_with_schedule_include_ids(self):
        result = run_main(
            {"apps": _apps()},
            {
                "EXCLUDE_APPS": "app-two",
                "SCHEDULE_INCLUDE_APP_IDS": "app-two-id",
            },
        )

        self.assertEqual(result.client.upgraded_apps, [])

    def test_schedule_include_and_exclude_ids_cannot_be_combined(self):
        with self.assertRaises(SystemExit) as raised:
            run_main(
                {"apps": _apps()},
                {
                    "SCHEDULE_INCLUDE_APP_IDS": "app-one-id",
                    "SCHEDULE_EXCLUDE_APP_IDS": "app-two-id",
                },
            )

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(ConfigurableClient.instances, [])

    def test_warns_when_scheduled_app_id_matches_no_app(self):
        with self.assertLogs("__main__", level="WARNING") as captured:
            run_main({"apps": _apps()}, {"SCHEDULE_INCLUDE_APP_IDS": "does-not-exist"})

        self.assertTrue(
            any("does-not-exist" in message for message in captured.output)
        )

    def test_does_not_warn_when_scheduled_app_id_exists(self):
        with self.assertNoLogs("__main__", level="WARNING"):
            run_main({"apps": _apps()}, {"SCHEDULE_INCLUDE_APP_IDS": "app-two-id"})


if __name__ == "__main__":
    unittest.main()
