import socket
import subprocess
import types
import unittest
from unittest.mock import patch

from main_harness import ConfigurableClient, make_app, run_main


# Real-looking container ID: Docker uses its first 12 characters as the hostname.
SELF_CONTAINER_ID = "3aee7b81711d12e567a4c4b80ce2a7de69a690df6ff48bd4a52cb609ee5e6839"


def with_container(app, container_id, image):
    """Attach the active_workloads data that app.query reports for running apps."""
    app["active_workloads"] = {"container_details": [{"id": container_id, "image": image}]}
    return app


def updater_app(name="truenas-auto-update", container_id=SELF_CONTAINER_ID,
                image="ghcr.io/marvinvr/truenas-auto-update:2.1.1"):
    return with_container(make_app(name), container_id, image)


def other_app(name):
    return with_container(make_app(name), f"{name}-container-id", f"example/{name}:latest")


def run_as(hostname, config, env=None):
    with patch.object(socket, "gethostname", return_value=hostname):
        return run_main(config, env)


class FindSelfAppNameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.find = staticmethod(run_main({"apps": []}).namespace["find_self_app_name"])

    def test_short_hostnames_are_not_treated_as_container_ids(self):
        apps = [with_container(make_app("plex"), SELF_CONTAINER_ID, "example/plex:latest")]

        self.assertIsNone(self.find(apps, hostname=SELF_CONTAINER_ID[:4]))

    def test_apps_without_workloads_are_ignored(self):
        self.assertIsNone(self.find([make_app("plex")], hostname=SELF_CONTAINER_ID[:12]))


class UpgradeSelfLastTest(unittest.TestCase):
    def test_updater_is_upgraded_after_every_other_app(self):
        result = run_as(
            SELF_CONTAINER_ID[:12],
            {"apps": [updater_app(), other_app("radarr"), other_app("sonarr")]},
        )

        self.assertEqual(
            result.client.upgraded_apps, ["radarr", "sonarr", "truenas-auto-update"]
        )

    def test_detection_does_not_depend_on_app_or_image_name(self):
        apps = [updater_app(name="auto-updater", image="local/custom:dev"), other_app("radarr")]

        result = run_as(SELF_CONTAINER_ID[:12], {"apps": apps})

        self.assertEqual(result.client.upgraded_apps, ["radarr", "auto-updater"])

    def test_custom_hostname_falls_back_to_the_image_name(self):
        result = run_as("auto-updater", {"apps": [updater_app(), other_app("radarr")]})

        self.assertEqual(result.client.upgraded_apps, ["radarr", "truenas-auto-update"])

    def test_order_is_unchanged_when_the_updater_is_not_an_app(self):
        result = run_as("some-host", {"apps": [other_app("plex"), other_app("radarr")]})

        self.assertEqual(result.client.upgraded_apps, ["plex", "radarr"])

    def test_several_updater_images_without_hostname_match_leave_order_unchanged(self):
        apps = [
            updater_app(name="updater-a", container_id="a" * 64),
            other_app("radarr"),
            updater_app(name="updater-b", container_id="b" * 64),
        ]

        result = run_as("custom-host", {"apps": apps})

        self.assertEqual(result.client.upgraded_apps, ["updater-a", "radarr", "updater-b"])

    def test_excluded_updater_is_still_skipped(self):
        result = run_as(
            SELF_CONTAINER_ID[:12],
            {"apps": [updater_app(), other_app("radarr")]},
            {"EXCLUDE_APPS": "truenas-auto-update"},
        )

        self.assertEqual(result.client.upgraded_apps, ["radarr"])

    def test_image_cleanup_runs_once_just_before_the_updater_upgrade(self):
        timeline = []

        def fake_run(cmd, **kwargs):
            timeline.append(" ".join(cmd[:3]))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        original_call = ConfigurableClient.call

        def recording_call(self, method, *args, **kwargs):
            if method == "app.upgrade":
                timeline.append(f"upgrade {args[0]}")
            return original_call(self, method, *args, **kwargs)

        with patch.object(subprocess, "run", fake_run), patch.object(
            ConfigurableClient, "call", recording_call
        ):
            run_as(
                SELF_CONTAINER_ID[:12],
                {"apps": [updater_app(), other_app("radarr")]},
                {"AUTO_CLEANUP_IMAGES": "true"},
            )

        self.assertEqual(
            timeline,
            ["upgrade radarr", "docker info", "docker image prune", "upgrade truenas-auto-update"],
        )


if __name__ == "__main__":
    unittest.main()
