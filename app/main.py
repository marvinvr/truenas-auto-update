import logging
import os
import subprocess
import time
from urllib.parse import urlparse

import apprise
from truenas_api_client import Client
from truenas_api_client.exc import ClientException, CallTimeout

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = os.getenv("BASE_URL")
API_KEY = os.getenv("API_KEY")
API_USERNAME = os.getenv("API_USERNAME", "root")
APPRISE_URLS = os.getenv("APPRISE_URLS", "").strip()
NOTIFY_ON_SUCCESS = os.getenv("NOTIFY_ON_SUCCESS", "false").lower() == "true"
ONLY_UPDATE_STARTED_APPS = os.getenv("ONLY_UPDATE_STARTED_APPS", "false").lower() == "true"
AUTO_CLEANUP_IMAGES = os.getenv("AUTO_CLEANUP_IMAGES", "false").lower() == "true"
SSL_VERIFY = os.getenv("SSL_VERIFY", "false").lower() == "true"
APP_START_TIMEOUT_SECONDS = 600
APP_STATE_POLL_INTERVAL_SECONDS = 10


def parse_csv_env(name):
    """Parse a comma-separated environment variable into a clean list."""
    return [item.strip() for item in os.getenv(name, "").strip().split(",") if item.strip()]


EXCLUDE_APPS = parse_csv_env("EXCLUDE_APPS")
INCLUDE_APPS = parse_csv_env("INCLUDE_APPS")
SCHEDULE_EXCLUDE_APP_IDS = parse_csv_env("SCHEDULE_EXCLUDE_APP_IDS")
SCHEDULE_INCLUDE_APP_IDS = parse_csv_env("SCHEDULE_INCLUDE_APP_IDS")

if EXCLUDE_APPS and INCLUDE_APPS:
    logger.error("Cannot use both EXCLUDE_APPS and INCLUDE_APPS simultaneously")
    exit(1)

if SCHEDULE_EXCLUDE_APP_IDS and SCHEDULE_INCLUDE_APP_IDS:
    logger.error("Cannot use both SCHEDULE_EXCLUDE_APP_IDS and SCHEDULE_INCLUDE_APP_IDS simultaneously")
    exit(1)

# Initialize Apprise
apobj = apprise.Apprise()
if APPRISE_URLS:
    for url in APPRISE_URLS.split(","):
        apobj.add(url.strip())


def send_notification(title, message):
    """Send notification using Apprise if configured"""
    if APPRISE_URLS:
        apobj.notify(title=title, body=message)
        logger.info(f"Notification sent: {title}")


def cleanup_docker_images():
    """Clean up unused Docker images if AUTO_CLEANUP_IMAGES is enabled"""
    if not AUTO_CLEANUP_IMAGES:
        logger.info("Docker image cleanup is disabled")
        return

    logger.info("Checking Docker daemon availability...")

    # Check if Docker daemon is accessible
    check_cmd = ["docker", "info"]
    try:
        result = subprocess.run(
            check_cmd,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            error_msg = "Docker cleanup enabled but Docker daemon is not accessible. Make sure the Docker socket is mounted at /var/run/docker.sock"
            logger.error(error_msg)
            send_notification("Docker Cleanup Warning", error_msg)
            return
    except subprocess.TimeoutExpired:
        error_msg = "Docker cleanup enabled but Docker daemon check timed out. Make sure the Docker socket is mounted at /var/run/docker.sock"
        logger.error(error_msg)
        send_notification("Docker Cleanup Warning", error_msg)
        return
    except FileNotFoundError:
        error_msg = "Docker cleanup enabled but Docker CLI is not installed"
        logger.error(error_msg)
        send_notification("Docker Cleanup Warning", error_msg)
        return
    except Exception as e:
        error_msg = f"Docker cleanup enabled but failed to check Docker daemon: {str(e)}"
        logger.error(error_msg)
        send_notification("Docker Cleanup Warning", error_msg)
        return

    logger.info("Docker daemon is accessible, proceeding with image cleanup...")

    # Run docker image prune -a -f
    cleanup_cmd = ["docker", "image", "prune", "-a", "-f"]
    try:
        result = subprocess.run(
            cleanup_cmd,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode == 0:
            logger.info("Docker image cleanup completed successfully")
            logger.info(f"Cleanup output: {result.stdout.strip()}")
        else:
            error_msg = f"Docker image cleanup failed with return code {result.returncode}: {result.stderr.strip()}"
            logger.error(error_msg)
            send_notification("Docker Cleanup Failed", error_msg)
    except subprocess.TimeoutExpired:
        error_msg = "Docker image cleanup timed out after 5 minutes"
        logger.error(error_msg)
        send_notification("Docker Cleanup Failed", error_msg)
    except Exception as e:
        error_msg = f"Docker image cleanup failed: {str(e)}"
        logger.error(error_msg)
        send_notification("Docker Cleanup Failed", error_msg)


def build_websocket_uri(base_url):
    """Convert HTTP(S) URL to WebSocket URI for TrueNAS API"""
    parsed = urlparse(base_url)

    # In newer TrueNAS versions, WebSocket endpoint is always wss, otherwise it disables API access
    ws_scheme = "wss"

    # Build the WebSocket URI with the API endpoint
    return f"{ws_scheme}://{parsed.netloc}/api/current"


def find_app_by_name(client, app_name):
    """Fetch the latest app entry for app_name."""
    apps = client.call("app.query")
    return next((app for app in apps if app.get("name") == app_name), None)


def get_app_state(client, app_name):
    """Fetch the latest app state for app_name."""
    app = find_app_by_name(client, app_name)
    if not app:
        logger.warning(f"Could not find app after upgrade: {app_name}")
        return "unknown"

    return app.get("state", "unknown")


def normalize_state(state):
    """Normalize app states for comparisons."""
    return str(state or "unknown").upper()


def wait_for_app_state(client, app_name, desired_state, timeout_seconds, reason):
    """Wait for an app to reach a desired state, returning its last observed state."""
    deadline = time.monotonic() + timeout_seconds
    last_state = "unknown"

    logger.info(
        f"Waiting up to {timeout_seconds} seconds for {app_name} to reach "
        f"{desired_state} after {reason}"
    )

    while time.monotonic() < deadline:
        try:
            last_state = get_app_state(client, app_name)
            if normalize_state(last_state) == desired_state:
                return last_state
        except Exception as e:
            logger.warning(f"Failed to query state for {app_name}: {str(e)}")

        logger.info(
            f"{app_name} is {last_state}; waiting "
            f"{APP_STATE_POLL_INTERVAL_SECONDS} seconds before checking again"
        )
        time.sleep(APP_STATE_POLL_INTERVAL_SECONDS)

    return last_state


def ensure_running_after_upgrade(client, app_name, operation="upgrade"):
    """Restart an app if TrueNAS leaves it stopped after a successful operation."""
    operation_title = operation.capitalize()
    final_state = wait_for_app_state(
        client,
        app_name,
        "RUNNING",
        APP_START_TIMEOUT_SECONDS,
        operation,
    )

    if normalize_state(final_state) == "RUNNING":
        logger.info(f"{app_name} is running after {operation}")
        return True

    if normalize_state(final_state) != "STOPPED":
        error_msg = (
            f"{operation_title} of {app_name} completed, but the app did "
            f"not return to RUNNING within {APP_START_TIMEOUT_SECONDS} seconds "
            f"(current state: {final_state})"
        )
        logger.error(error_msg)
        send_notification(f"App Not Running After {operation_title}", error_msg)
        return False

    logger.warning(f"{app_name} is stopped after {operation}; attempting to start it")
    try:
        client.call("app.start", app_name, job=True)
    except CallTimeout:
        error_msg = f"Start of {app_name} timed out after {operation}"
        logger.error(error_msg)
        send_notification("App Start Timeout", error_msg)
        return False
    except ClientException as e:
        error_msg = f"Failed to start {app_name} after {operation}: {e.error}"
        logger.error(error_msg)
        send_notification(f"App Start Failed After {operation_title}", error_msg)
        return False
    except Exception as e:
        error_msg = f"Failed to start {app_name} after {operation}: {str(e)}"
        logger.error(error_msg)
        send_notification(f"App Start Failed After {operation_title}", error_msg)
        return False

    final_state = wait_for_app_state(
        client,
        app_name,
        "RUNNING",
        APP_START_TIMEOUT_SECONDS,
        "start",
    )

    if normalize_state(final_state) == "RUNNING":
        logger.info(f"{app_name} started successfully after {operation}")
        return True

    error_msg = (
        f"{operation_title} of {app_name} completed and a start was "
        f"attempted, but the app did not reach RUNNING within "
        f"{APP_START_TIMEOUT_SECONDS} seconds (current state: {final_state})"
    )
    logger.error(error_msg)
    send_notification(f"App Start Failed After {operation_title}", error_msg)
    return False


def redeploy_stale_app(client, app_name, app_was_running):
    """Redeploy an app that TrueNAS reported as having no upgrade available.

    Custom apps (and ix-apps) are flagged ``upgrade_available`` whenever a newer
    image exists for one of their docker tags. ``app.upgrade`` pulls that image,
    redeploys the app and clears the shared image-update flag. When several apps
    reference the same image tag, the first upgrade clears the flag for all of
    them, so every sibling processed afterwards raises ``No upgrade available``
    and is left running the old image. A redeploy stops the app, pulls latest
    images and restarts it, which is exactly what those siblings need.
    """
    logger.info(
        f"{app_name} reports no upgrade available; a sibling app likely already "
        f"handled the shared image update. Redeploying to pick up the latest image."
    )
    try:
        client.call("app.redeploy", app_name, job=True)

        if app_was_running and not ensure_running_after_upgrade(
            client, app_name, operation="redeploy"
        ):
            return

        logger.info(f"Redeploy of {app_name} completed successfully")
        if NOTIFY_ON_SUCCESS:
            send_notification("App Redeployed", f"Redeployed {app_name} onto the latest image")
    except CallTimeout:
        error_msg = f"Redeploy of {app_name} timed out"
        logger.error(error_msg)
        send_notification("Redeploy Timeout", error_msg)
    except ClientException as e:
        error_msg = f"Failed to redeploy {app_name}: {e.error}"
        logger.error(error_msg)
        send_notification("Redeploy Failed", error_msg)
    except Exception as e:
        error_msg = f"Failed to redeploy {app_name}: {str(e)}"
        logger.error(error_msg)
        send_notification("Redeploy Failed", error_msg)


if not BASE_URL or not API_KEY:
    logger.error("BASE_URL or API_KEY is not set")
    send_notification("Configuration Error", "BASE_URL or API_KEY is not set")
    exit(1)

# Build WebSocket URI from BASE_URL
ws_uri = build_websocket_uri(BASE_URL)
logger.info(f"Connecting to TrueNAS API at {ws_uri}")

try:
    with Client(uri=ws_uri, verify_ssl=SSL_VERIFY) as client:
        # Authenticate with API key using auth.login_ex (required for TrueNAS 25.04+)
        logger.info(f"Authenticating as {API_USERNAME} with API key...")
        auth_response = client.call("auth.login_ex", {
            "mechanism": "API_KEY_PLAIN",
            "username": API_USERNAME,
            "api_key": API_KEY
        })
        if not auth_response or not auth_response.get("response_type") == "SUCCESS":
            logger.error(f"Authentication failed: {auth_response}")
            send_notification("Error", f"Authentication failed for {BASE_URL}")
            exit(1)

        logger.info("Authentication successful")

        # Get all apps using app.query
        logger.info("Fetching apps...")
        apps = client.call("app.query")
        logger.info(f"Total apps found: {len(apps)}")

        # Warn about configured app ids that do not match any app. APP_SCHEDULES
        # keys are TrueNAS app ids, so a typo here silently skips the app forever.
        known_app_ids = {app.get("id") for app in apps}
        for configured_id in (*SCHEDULE_INCLUDE_APP_IDS, *SCHEDULE_EXCLUDE_APP_IDS):
            if configured_id not in known_app_ids:
                logger.warning(
                    f"Scheduled app id '{configured_id}' does not match any TrueNAS app "
                    f"(check APP_SCHEDULES; keys must be app ids, not names)"
                )

        apps_with_upgrade = [app for app in apps if app.get("upgrade_available")]

        logger.info(f"Found {len(apps_with_upgrade)} apps with upgrade available")

        for app in apps_with_upgrade:
            # Use 'name' field - this is what app.upgrade expects as 'app_name' parameter
            app_name = app.get("name")
            if not app_name:
                logger.warning(f"Skipping app with missing name: {app}")
                continue
            app_id = app.get("id")

            app_state = app.get("state", "unknown")
            app_was_running = normalize_state(app_state) == "RUNNING"
            app_had_image_update = bool(app.get("image_updates_available"))

            if EXCLUDE_APPS and app_name in EXCLUDE_APPS:
                logger.info(f"Skipping upgrade for: {app_name} (APP in EXCLUDE_APPS)")
                continue
            if INCLUDE_APPS and app_name not in INCLUDE_APPS:
                logger.info(f"Skipping upgrade for: {app_name} (APP not in INCLUDE_APPS)")
                continue
            if SCHEDULE_EXCLUDE_APP_IDS and app_id in SCHEDULE_EXCLUDE_APP_IDS:
                logger.info(f"Skipping upgrade for: {app_name} (APP id in SCHEDULE_EXCLUDE_APP_IDS)")
                continue
            if SCHEDULE_INCLUDE_APP_IDS and app_id not in SCHEDULE_INCLUDE_APP_IDS:
                logger.info(f"Skipping upgrade for: {app_name} (APP id not in SCHEDULE_INCLUDE_APP_IDS)")
                continue
            if ONLY_UPDATE_STARTED_APPS and not app_was_running:
                logger.info(f"Skipping upgrade for: {app_name} (APP not running, state: {app_state})")
                continue

            logger.info(f"Upgrading {app_name}...")

            try:
                # Call app.upgrade with job=True to wait for completion
                # app.upgrade takes app_name as first parameter
                client.call("app.upgrade", app_name, job=True)

                if app_was_running and not ensure_running_after_upgrade(client, app_name):
                    continue

                success_msg = f"Upgrade of {app_name} completed successfully"
                logger.info(success_msg)
                if NOTIFY_ON_SUCCESS:
                    send_notification("App Updated", f"Successfully updated {app_name} to the latest version")

            except CallTimeout:
                error_msg = f"Upgrade of {app_name} timed out"
                logger.error(error_msg)
                send_notification("Upgrade Timeout", error_msg)
            except ClientException as e:
                # An app with image updates can report "No upgrade available" when a
                # sibling sharing the same image tag was upgraded first and cleared the
                # shared image-update flag. The app still needs a redeploy to move onto
                # the updated image.
                if app_had_image_update and "No upgrade available" in str(e.error):
                    redeploy_stale_app(client, app_name, app_was_running)
                else:
                    error_msg = f"Failed to upgrade {app_name}: {e.error}"
                    logger.error(error_msg)
                    send_notification("Upgrade Failed", error_msg)
            except Exception as e:
                error_msg = f"Failed to upgrade {app_name}: {str(e)}"
                logger.error(error_msg)
                send_notification("Upgrade Failed", error_msg)

            time.sleep(1)

        logger.info("All app updates completed")

except ClientException as e:
    logger.error(f"TrueNAS API error: {e.error}")
    send_notification("Error", f"TrueNAS API error at {BASE_URL}: {e.error}")
    exit(1)
except Exception as e:
    logger.error(f"Failed to connect to TrueNAS API: {str(e)}")
    send_notification("Error", f"Failed to connect to TrueNAS API at {BASE_URL}: {str(e)}")
    exit(1)

# Run Docker image cleanup after all updates are done
cleanup_docker_images()

logger.info("Done")
