from test_main_existing import FakeClient, run_main

import unittest


class ScheduleFilterBehaviorTest(unittest.TestCase):
    def test_schedule_include_app_ids_limits_updates_by_app_id(self):
        client = run_main({"SCHEDULE_INCLUDE_APP_IDS": "app-two-id"})

        self.assertEqual(client.upgraded_apps, ["app-two"])

    def test_schedule_exclude_app_ids_skips_matching_app_ids(self):
        client = run_main({"SCHEDULE_EXCLUDE_APP_IDS": "app-one-id"})

        self.assertEqual(client.upgraded_apps, ["app-two"])

    def test_schedule_filters_use_id_not_name(self):
        client = run_main({"SCHEDULE_INCLUDE_APP_IDS": "app-two"})

        self.assertEqual(client.upgraded_apps, [])

    def test_user_exclude_apps_still_applies_with_schedule_include_ids(self):
        client = run_main({
            "EXCLUDE_APPS": "app-two",
            "SCHEDULE_INCLUDE_APP_IDS": "app-two-id",
        })

        self.assertEqual(client.upgraded_apps, [])

    def test_schedule_include_and_exclude_ids_cannot_be_combined(self):
        with self.assertRaises(SystemExit) as raised:
            run_main({
                "SCHEDULE_INCLUDE_APP_IDS": "app-one-id",
                "SCHEDULE_EXCLUDE_APP_IDS": "app-two-id",
            })

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(FakeClient.instances, [])

    def test_warns_when_scheduled_app_id_matches_no_app(self):
        with self.assertLogs("__main__", level="WARNING") as captured:
            run_main({"SCHEDULE_INCLUDE_APP_IDS": "does-not-exist"})

        self.assertTrue(
            any("does-not-exist" in message for message in captured.output)
        )

    def test_does_not_warn_when_scheduled_app_id_exists(self):
        with self.assertLogs("__main__", level="WARNING") as captured:
            run_main({"SCHEDULE_INCLUDE_APP_IDS": "app-two-id"})
            import logging

            logging.getLogger("__main__").warning("sentinel")

        self.assertFalse(
            any("does not match any TrueNAS app" in message for message in captured.output)
        )


if __name__ == "__main__":
    unittest.main()
