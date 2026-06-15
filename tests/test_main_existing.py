import os
import runpy
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "app" / "main.py"


class FakeApprise:
    def add(self, url):
        pass

    def notify(self, title, body):
        pass


class FakeClientException(Exception):
    def __init__(self, error):
        super().__init__(error)
        self.error = error


class FakeCallTimeout(Exception):
    pass


class FakeClient:
    instances = []

    def __init__(self, uri, verify_ssl=False):
        self.uri = uri
        self.verify_ssl = verify_ssl
        self.upgraded_apps = []
        FakeClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def call(self, method, *args, **kwargs):
        if method == "auth.login_ex":
            return {"response_type": "SUCCESS"}
        if method == "app.query":
            return [
                {
                    "id": "app-one-id",
                    "name": "app-one",
                    "upgrade_available": True,
                    "state": "STOPPED",
                },
                {
                    "id": "app-two-id",
                    "name": "app-two",
                    "upgrade_available": True,
                    "state": "STOPPED",
                },
                {
                    "id": "current-id",
                    "name": "current-app",
                    "upgrade_available": False,
                    "state": "STOPPED",
                },
            ]
        if method == "app.upgrade":
            self.upgraded_apps.append(args[0])
            return None
        raise AssertionError(f"Unexpected API call: {method}")


def run_main(extra_env=None):
    FakeClient.instances = []
    apprise_module = types.SimpleNamespace(Apprise=FakeApprise)
    truenas_module = types.SimpleNamespace(Client=FakeClient)
    exc_module = types.SimpleNamespace(
        ClientException=FakeClientException,
        CallTimeout=FakeCallTimeout,
    )
    env = {
        "BASE_URL": "https://truenas.local",
        "API_KEY": "test-api-key",
    }
    env.update(extra_env or {})

    original_sleep = time.sleep
    time.sleep = lambda seconds: None
    try:
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules,
            {
                "apprise": apprise_module,
                "truenas_api_client": truenas_module,
                "truenas_api_client.exc": exc_module,
            },
        ):
            runpy.run_path(str(MAIN_PATH), run_name="__main__")
    finally:
        time.sleep = original_sleep

    return FakeClient.instances[-1]


class ExistingMainBehaviorTest(unittest.TestCase):
    def test_exclude_apps_skips_matching_app_names(self):
        client = run_main({"EXCLUDE_APPS": "app-one"})

        self.assertEqual(client.upgraded_apps, ["app-two"])

    def test_include_apps_limits_updates_to_matching_app_names(self):
        client = run_main({"INCLUDE_APPS": "app-two"})

        self.assertEqual(client.upgraded_apps, ["app-two"])

    def test_only_update_started_apps_skips_stopped_apps(self):
        client = run_main({"ONLY_UPDATE_STARTED_APPS": "true"})

        self.assertEqual(client.upgraded_apps, [])

    def test_include_and_exclude_apps_cannot_be_combined(self):
        with self.assertRaises(SystemExit) as raised:
            run_main({"INCLUDE_APPS": "app-one", "EXCLUDE_APPS": "app-two"})

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(FakeClient.instances, [])


if __name__ == "__main__":
    unittest.main()
