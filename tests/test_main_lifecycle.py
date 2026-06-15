import subprocess
import types
import unittest
from unittest.mock import patch

from main_harness import (
    CallTimeout,
    ClientException,
    ConfigurableClient,
    make_app,
    run_main,
)


class PureHelperTest(unittest.TestCase):
    """Unit tests for the side-effect-free helpers in main.py."""

    @classmethod
    def setUpClass(cls):
        cls.ns = run_main({"apps": []}).namespace

    def test_build_websocket_uri_converts_https_to_wss(self):
        build = self.ns["build_websocket_uri"]
        self.assertEqual(
            build("https://truenas.local"), "wss://truenas.local/api/current"
        )

    def test_build_websocket_uri_preserves_host_and_port(self):
        build = self.ns["build_websocket_uri"]
        self.assertEqual(
            build("http://10.0.0.5:8080"), "wss://10.0.0.5:8080/api/current"
        )

    def test_normalize_state_uppercases(self):
        normalize = self.ns["normalize_state"]
        self.assertEqual(normalize("running"), "RUNNING")

    def test_normalize_state_handles_none(self):
        normalize = self.ns["normalize_state"]
        self.assertEqual(normalize(None), "UNKNOWN")

    def test_parse_csv_env_strips_and_drops_blanks(self):
        parse = self.ns["parse_csv_env"]
        with patch.dict("os.environ", {"X": " a , ,b ,"}, clear=False):
            self.assertEqual(parse("X"), ["a", "b"])


class UpgradeSuccessTest(unittest.TestCase):
    def test_running_app_stays_running_and_notifies_on_success(self):
        result = run_main(
            {"apps": [make_app("plex", state="RUNNING")]},
            {"NOTIFY_ON_SUCCESS": "true"},
        )

        self.assertEqual(result.client.upgraded_apps, ["plex"])
        self.assertEqual(result.client.started_apps, [])
        self.assertIn("App Updated", result.notification_titles)

    def test_success_does_not_notify_when_notify_on_success_disabled(self):
        result = run_main({"apps": [make_app("plex", state="RUNNING")]})

        self.assertEqual(result.client.upgraded_apps, ["plex"])
        self.assertEqual(result.notifications, [])

    def test_non_running_app_is_not_restarted_after_upgrade(self):
        # app_was_running is False (DEPLOYING is neither RUNNING nor STOPPED, so
        # the real API still permits the upgrade), so the restart path is skipped.
        result = run_main({"apps": [make_app("plex", state="DEPLOYING")]})

        self.assertEqual(result.client.upgraded_apps, ["plex"])
        self.assertEqual(result.client.started_apps, [])
        self.assertEqual(result.notifications, [])


class EnsureRunningAfterUpgradeTest(unittest.TestCase):
    def test_app_stopped_after_upgrade_is_started(self):
        result = run_main(
            {
                "apps": [make_app("plex", state="RUNNING")],
                "upgrade": {"plex": "STOPPED"},  # upgrade leaves it stopped
                "start": {"plex": "RUNNING"},  # start brings it back
            }
        )

        self.assertEqual(result.client.started_apps, ["plex"])
        self.assertEqual(result.notifications, [])

    def test_start_failure_is_reported(self):
        result = run_main(
            {
                "apps": [make_app("plex", state="RUNNING")],
                "upgrade": {"plex": "STOPPED"},
                "start": {"plex": ClientException("boom")},
            }
        )

        self.assertEqual(result.client.started_apps, ["plex"])
        self.assertIn("App Start Failed After Upgrade", result.notification_titles)

    def test_start_timeout_is_reported(self):
        result = run_main(
            {
                "apps": [make_app("plex", state="RUNNING")],
                "upgrade": {"plex": "STOPPED"},
                "start": {"plex": CallTimeout()},
            }
        )

        self.assertIn("App Start Timeout", result.notification_titles)

    def test_app_that_stays_stopped_after_start_is_reported(self):
        result = run_main(
            {
                "apps": [make_app("plex", state="RUNNING")],
                "upgrade": {"plex": "STOPPED"},
                "start": {"plex": "STOPPED"},  # start does not bring it back
            }
        )

        self.assertEqual(result.client.started_apps, ["plex"])
        self.assertIn("App Start Failed After Upgrade", result.notification_titles)

    def test_app_in_unexpected_state_after_upgrade_is_reported(self):
        result = run_main(
            {
                "apps": [make_app("plex", state="RUNNING")],
                "upgrade": {"plex": "CRASHED"},  # neither RUNNING nor STOPPED
            }
        )

        # No start is attempted for a non-STOPPED state.
        self.assertEqual(result.client.started_apps, [])
        self.assertIn("App Not Running After Upgrade", result.notification_titles)


class UpgradeErrorHandlingTest(unittest.TestCase):
    def test_upgrade_timeout_is_reported(self):
        result = run_main(
            {
                "apps": [make_app("plex", state="RUNNING")],
                "upgrade": {"plex": CallTimeout()},
            }
        )

        self.assertIn("Upgrade Timeout", result.notification_titles)

    def test_generic_upgrade_failure_is_reported(self):
        result = run_main(
            {
                "apps": [make_app("plex", state="RUNNING")],
                "upgrade": {"plex": ClientException("nope")},
            }
        )

        self.assertIn("Upgrade Failed", result.notification_titles)

    def test_unexpected_exception_during_upgrade_is_reported(self):
        result = run_main(
            {
                "apps": [make_app("plex", state="RUNNING")],
                "upgrade": {"plex": RuntimeError("kaboom")},
            }
        )

        self.assertIn("Upgrade Failed", result.notification_titles)

    def test_stopped_app_upgrade_rejected_by_server_is_reported(self):
        # The real TrueNAS API rejects upgrading a STOPPED app
        # (CallError: "In order to upgrade an app, it must not be in stopped
        # state"). main.py surfaces it as a generic upgrade failure, and the
        # message must NOT match the redeploy trigger.
        result = run_main(
            {
                "apps": [
                    make_app("plex", state="STOPPED", image_updates_available=True)
                ],
                "upgrade": {
                    "plex": ClientException(
                        "In order to upgrade an app, it must not be in stopped state"
                    )
                },
            }
        )

        self.assertEqual(result.client.redeployed_apps, [])
        self.assertIn("Upgrade Failed", result.notification_titles)


class RedeployStaleAppTest(unittest.TestCase):
    def _stale_config(self, **overrides):
        config = {
            "apps": [
                make_app("plex", state="RUNNING", image_updates_available=True)
            ],
            # Real middleware message: CallError(f'No upgrade available for {app_name!r}').
            # This is what a sibling sharing an image tag sees after the first
            # app's upgrade clears the shared image-update flag.
            "upgrade": {"plex": ClientException("No upgrade available for 'plex'")},
        }
        config.update(overrides)
        return config

    def test_no_upgrade_available_with_image_update_triggers_redeploy(self):
        result = run_main(self._stale_config(), {"NOTIFY_ON_SUCCESS": "true"})

        self.assertEqual(result.client.redeployed_apps, ["plex"])
        self.assertIn("App Redeployed", result.notification_titles)

    def test_no_upgrade_available_without_image_update_is_a_failure(self):
        result = run_main(
            {
                "apps": [
                    make_app("plex", state="RUNNING", image_updates_available=False)
                ],
                "upgrade": {"plex": ClientException("No upgrade available for 'plex'")},
            }
        )

        self.assertEqual(result.client.redeployed_apps, [])
        self.assertIn("Upgrade Failed", result.notification_titles)

    def test_redeploy_timeout_is_reported(self):
        result = run_main(self._stale_config(redeploy={"plex": CallTimeout()}))

        self.assertIn("Redeploy Timeout", result.notification_titles)

    def test_redeploy_failure_is_reported(self):
        result = run_main(
            self._stale_config(redeploy={"plex": ClientException("denied")})
        )

        self.assertIn("Redeploy Failed", result.notification_titles)

    def test_stopped_stale_app_is_redeployed_without_restart(self):
        result = run_main(
            self._stale_config(
                apps=[
                    make_app("plex", state="STOPPED", image_updates_available=True)
                ]
            )
        )

        self.assertEqual(result.client.redeployed_apps, ["plex"])
        self.assertEqual(result.client.started_apps, [])


class CleanupDockerImagesTest(unittest.TestCase):
    def _run_with_docker(self, fake_run, env):
        with patch.object(subprocess, "run", fake_run):
            return run_main({"apps": []}, env)

    def test_disabled_by_default_runs_no_docker_commands(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        self._run_with_docker(fake_run, {})
        self.assertEqual(calls, [])

    def test_successful_cleanup_emits_no_warning(self):
        def fake_run(cmd, **kwargs):
            return types.SimpleNamespace(returncode=0, stdout="done", stderr="")

        result = self._run_with_docker(fake_run, {"AUTO_CLEANUP_IMAGES": "true"})
        self.assertEqual(
            [t for t in result.notification_titles if "Docker" in t], []
        )

    def test_inaccessible_daemon_warns(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "info"]:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="no")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        result = self._run_with_docker(fake_run, {"AUTO_CLEANUP_IMAGES": "true"})
        self.assertIn("Docker Cleanup Warning", result.notification_titles)

    def test_missing_docker_cli_warns(self):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("docker")

        result = self._run_with_docker(fake_run, {"AUTO_CLEANUP_IMAGES": "true"})
        self.assertIn("Docker Cleanup Warning", result.notification_titles)

    def test_prune_failure_notifies(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "info"]:
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            return types.SimpleNamespace(returncode=1, stdout="", stderr="prune failed")

        result = self._run_with_docker(fake_run, {"AUTO_CLEANUP_IMAGES": "true"})
        self.assertIn("Docker Cleanup Failed", result.notification_titles)


class ConfigAndConnectionErrorTest(unittest.TestCase):
    def test_missing_base_url_exits_without_connecting(self):
        with self.assertRaises(SystemExit) as raised:
            run_main({"apps": []}, {"BASE_URL": ""})

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(ConfigurableClient.instances, [])

    def test_missing_api_key_exits_without_connecting(self):
        with self.assertRaises(SystemExit) as raised:
            run_main({"apps": []}, {"API_KEY": ""})

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(ConfigurableClient.instances, [])

    def test_auth_failure_exits(self):
        # AUTH_ERR is a real auth.login_ex response_type; any non-SUCCESS value
        # is treated as a failure by main.py.
        with self.assertRaises(SystemExit) as raised:
            run_main({"apps": [], "auth": "AUTH_ERR"})

        self.assertEqual(raised.exception.code, 1)

    def test_api_error_during_query_exits(self):
        with self.assertRaises(SystemExit) as raised:
            run_main({"apps": [], "query_error": ClientException("api down")})

        self.assertEqual(raised.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
