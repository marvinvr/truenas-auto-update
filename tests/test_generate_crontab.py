import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_crontab",
    ROOT / "app" / "generate_crontab.py",
)
generate_crontab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_crontab)


class GenerateCrontabTest(unittest.TestCase):
    def test_builds_global_schedule_without_per_app_schedules(self):
        crontab = generate_crontab.build_crontab("0 4 * * *", {})

        self.assertIn("0 4 * * * /app/run-script.sh >> /var/log/cron.log 2>&1", crontab)
        self.assertNotIn("SCHEDULE_EXCLUDE_APP_IDS", crontab)

    def test_builds_per_app_schedules_and_global_fallback(self):
        schedules = generate_crontab.parse_app_schedules(
            '{"plex":"0 3 * * *","immich":"30 4 * * 0"}'
        )

        crontab = generate_crontab.build_crontab("0 4 * * *", schedules)

        self.assertIn(
            "0 3 * * * SCHEDULE_INCLUDE_APP_IDS=plex /app/run-script.sh >> /var/log/cron.log 2>&1",
            crontab,
        )
        self.assertIn(
            "30 4 * * 0 SCHEDULE_INCLUDE_APP_IDS=immich /app/run-script.sh >> /var/log/cron.log 2>&1",
            crontab,
        )
        self.assertIn(
            "0 4 * * * SCHEDULE_EXCLUDE_APP_IDS=plex,immich /app/run-script.sh >> /var/log/cron.log 2>&1",
            crontab,
        )

    def test_groups_apps_that_share_the_same_schedule(self):
        schedules = generate_crontab.parse_app_schedules(
            '{"plex":"0 3 * * *","sonarr":"0 3 * * *"}'
        )

        crontab = generate_crontab.build_crontab("", schedules)

        self.assertIn(
            "0 3 * * * SCHEDULE_INCLUDE_APP_IDS=plex,sonarr /app/run-script.sh >> /var/log/cron.log 2>&1",
            crontab,
        )

    def test_rejects_invalid_json(self):
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            generate_crontab.parse_app_schedules("{not-json")

    def test_rejects_non_object_json(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            generate_crontab.parse_app_schedules('["plex"]')

    def test_rejects_invalid_cron_schedule(self):
        with self.assertRaisesRegex(ValueError, "5-field cron"):
            generate_crontab.parse_app_schedules('{"plex":"0 3 * *"}')

    def test_rejects_five_fields_with_invalid_tokens(self):
        with self.assertRaisesRegex(ValueError, "invalid cron field"):
            generate_crontab.parse_app_schedules('{"plex":"a b c d e"}')

    def test_accepts_cron_ranges_steps_lists_and_names(self):
        schedules = generate_crontab.parse_app_schedules(
            '{"plex":"*/15 0-6 1,15 * MON-FRI"}'
        )

        self.assertEqual(schedules["plex"], "*/15 0-6 1,15 * MON-FRI")

    def test_rejects_app_ids_with_commas(self):
        with self.assertRaisesRegex(ValueError, "only contain"):
            generate_crontab.parse_app_schedules('{"plex,other":"0 3 * * *"}')

    def test_rejects_app_ids_with_shell_metacharacters(self):
        with self.assertRaisesRegex(ValueError, "only contain"):
            generate_crontab.parse_app_schedules(
                '{"plex;touch${IFS}/tmp/pwned":"0 3 * * *"}'
            )


if __name__ == "__main__":
    unittest.main()
