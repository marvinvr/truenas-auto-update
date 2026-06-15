"""Shared harness for exercising app/main.py end-to-end with a fake TrueNAS client.

main.py runs its logic at module top-level (it is invoked as ``python main.py``),
so tests drive it with ``runpy`` under ``run_name="__main__"`` while patching the
``apprise`` and ``truenas_api_client`` imports with fakes. ``time.sleep`` is
neutralised and ``time.monotonic`` is replaced with a deterministic clock that
advances by more than the wait timeout each call, so every ``wait_for_app_state``
loop polls exactly once and then times out instead of busy-spinning.
"""

import os
import runpy
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "app" / "main.py"

# Sentinel meaning "no configured behaviour for this app/method".
_UNSET = object()


class ClientException(Exception):
    """Stand-in for truenas_api_client.exc.ClientException."""

    def __init__(self, error):
        super().__init__(error)
        self.error = error


class CallTimeout(Exception):
    """Stand-in for truenas_api_client.exc.CallTimeout."""


class FakeApprise:
    """Records notifications so tests can assert on titles/bodies."""

    def __init__(self):
        self.notifications = []

    def add(self, url):
        pass

    def notify(self, title, body):
        self.notifications.append((title, body))


class ConfigurableClient:
    """Fake TrueNAS client driven by a per-run config dict on the class.

    Config keys (all optional):
      - ``apps``: list of app dicts (id/name/upgrade_available/state/...).
        ``app.query`` returns the live dicts so state changes are observable.
      - ``auth``: response_type returned by ``auth.login_ex`` (default SUCCESS).
      - ``upgrade``/``start``/``redeploy``: {app_name: action}, where action is
        either an Exception instance (raised) or a string (the app's new state).
        Defaults: upgrade/redeploy leave state unchanged; start sets RUNNING.
    """

    instances = []
    config = {}

    def __init__(self, uri, verify_ssl=False):
        self.uri = uri
        self.verify_ssl = verify_ssl
        self.calls = []
        self.upgraded_apps = []
        self.started_apps = []
        self.redeployed_apps = []
        ConfigurableClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def _set_state(self, name, state):
        for app in ConfigurableClient.config.get("apps", []):
            if app.get("name") == name:
                app["state"] = state

    def _act(self, method, name, default_state):
        action = ConfigurableClient.config.get(method, {}).get(name, _UNSET)
        if isinstance(action, Exception):
            raise action
        new_state = action if isinstance(action, str) else default_state
        if new_state is not None:
            self._set_state(name, new_state)

    def call(self, method, *args, **kwargs):
        self.calls.append((method, args))
        cfg = ConfigurableClient.config

        if method == "auth.login_ex":
            return {"response_type": cfg.get("auth", "SUCCESS")}
        if method == "app.query":
            query_error = cfg.get("query_error")
            if query_error is not None:
                raise query_error
            return list(cfg.get("apps", []))
        if method == "app.upgrade":
            self.upgraded_apps.append(args[0])
            self._act("upgrade", args[0], default_state=None)
            return None
        if method == "app.start":
            self.started_apps.append(args[0])
            self._act("start", args[0], default_state="RUNNING")
            return None
        if method == "app.redeploy":
            self.redeployed_apps.append(args[0])
            self._act("redeploy", args[0], default_state=None)
            return None
        raise AssertionError(f"Unexpected API call: {method}")


class Result:
    """Bundles the outcome of a harness run for assertions."""

    def __init__(self, client, namespace):
        self.client = client
        self.namespace = namespace

    @property
    def notifications(self):
        apobj = self.namespace.get("apobj") if self.namespace else None
        return list(getattr(apobj, "notifications", []))

    @property
    def notification_titles(self):
        return [title for title, _ in self.notifications]


def run_main(config=None, extra_env=None):
    """Run app/main.py against a ConfigurableClient and return a Result.

    ``config`` is stored on ConfigurableClient for the duration of the run.
    ``APPRISE_URLS`` defaults to a dummy value so notifications are recorded;
    pass ``{"APPRISE_URLS": ""}`` to disable.
    """
    ConfigurableClient.instances = []
    ConfigurableClient.config = config or {"apps": []}

    apprise_module = types.SimpleNamespace(Apprise=FakeApprise)
    truenas_module = types.SimpleNamespace(Client=ConfigurableClient)
    exc_module = types.SimpleNamespace(
        ClientException=ClientException,
        CallTimeout=CallTimeout,
    )

    env = {
        "BASE_URL": "https://truenas.local",
        "API_KEY": "test-api-key",
        "APPRISE_URLS": "json://example.com",
    }
    env.update(extra_env or {})

    counter = {"t": 0}

    def fake_monotonic():
        counter["t"] += 400
        return counter["t"]

    namespace = {}
    with patch.dict(os.environ, env, clear=True), patch.dict(
        sys.modules,
        {
            "apprise": apprise_module,
            "truenas_api_client": truenas_module,
            "truenas_api_client.exc": exc_module,
        },
    ), patch.object(time, "sleep", lambda seconds: None), patch.object(
        time, "monotonic", fake_monotonic
    ):
        namespace = runpy.run_path(str(MAIN_PATH), run_name="__main__")

    client = ConfigurableClient.instances[-1] if ConfigurableClient.instances else None
    return Result(client, namespace)


def make_app(name, *, app_id=None, upgrade_available=True, state="RUNNING",
             image_updates_available=False):
    """Build an app dict with sensible defaults for tests."""
    return {
        "id": app_id or f"{name}-id",
        "name": name,
        "upgrade_available": upgrade_available,
        "state": state,
        "image_updates_available": image_updates_available,
    }
