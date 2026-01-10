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
APPRISE_URLS = os.getenv("APPRISE_URLS", "").strip()
NOTIFY_ON_SUCCESS = os.getenv("NOTIFY_ON_SUCCESS", "false").lower() == "true"
ONLY_UPDATE_STARTED_APPS = os.getenv("ONLY_UPDATE_STARTED_APPS", "false").lower() == "true"
AUTO_CLEANUP_IMAGES = os.getenv("AUTO_CLEANUP_IMAGES", "false").lower() == "true"
SSL_VERIFY = os.getenv("SSL_VERIFY", "false").lower() == "true"
EXCLUDE_APPS = [app.strip() for app in os.getenv("EXCLUDE_APPS", "").strip().split(",") if app.strip()]
INCLUDE_APPS = [app.strip() for app in os.getenv("INCLUDE_APPS", "").strip().split(",") if app.strip()]

if EXCLUDE_APPS and INCLUDE_APPS:
    logger.error("Cannot use both EXCLUDE_APPS and INCLUDE_APPS simultaneously")
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

    # Determine WebSocket scheme based on HTTP scheme
    if parsed.scheme == "https":
        ws_scheme = "wss"
    else:
        ws_scheme = "ws"

    # Build the WebSocket URI with the API endpoint
    return f"{ws_scheme}://{parsed.netloc}/api/current"


if not BASE_URL or not API_KEY:
    logger.error("BASE_URL or API_KEY is not set")
    send_notification("Configuration Error", "BASE_URL or API_KEY is not set")
    exit(1)

# Build WebSocket URI from BASE_URL
ws_uri = build_websocket_uri(BASE_URL)
logger.info(f"Connecting to TrueNAS API at {ws_uri}")

try:
    with Client(uri=ws_uri, verify_ssl=SSL_VERIFY) as client:
        # Authenticate with API key
        logger.info("Authenticating with API key...")
        if not client.call("auth.login_with_api_key", API_KEY):
            logger.error("Authentication failed")
            send_notification("Error", f"Authentication failed for {BASE_URL}")
            exit(1)

        logger.info("Authentication successful")

        # Get all apps using app.query
        logger.info("Fetching apps...")
        apps = client.call("app.query")
        logger.info(f"Total apps found: {len(apps)}")

        apps_with_upgrade = [app for app in apps if app.get("upgrade_available")]

        logger.info(f"Found {len(apps_with_upgrade)} apps with upgrade available")

        for app in apps_with_upgrade:
            # Use 'name' field - this is what app.upgrade expects as 'app_name' parameter
            app_name = app.get("name")
            if not app_name:
                logger.warning(f"Skipping app with missing name: {app}")
                continue

            app_state = app.get("state", "unknown")

            if EXCLUDE_APPS and app_name in EXCLUDE_APPS:
                logger.info(f"Skipping upgrade for: {app_name} (APP in EXCLUDE_APPS)")
                continue
            if INCLUDE_APPS and app_name not in INCLUDE_APPS:
                logger.info(f"Skipping upgrade for: {app_name} (APP not in INCLUDE_APPS)")
                continue
            if ONLY_UPDATE_STARTED_APPS and app_state.upper() != "RUNNING":
                logger.info(f"Skipping upgrade for: {app_name} (APP not running, state: {app_state})")
                continue

            logger.info(f"Upgrading {app_name}...")

            try:
                # Call app.upgrade with job=True to wait for completion
                # app.upgrade takes app_name as first parameter
                result = client.call("app.upgrade", app_name, job=True)

                success_msg = f"Upgrade of {app_name} completed successfully"
                logger.info(success_msg)
                if NOTIFY_ON_SUCCESS:
                    send_notification("App Updated", f"Successfully updated {app_name} to the latest version")

            except CallTimeout:
                error_msg = f"Upgrade of {app_name} timed out"
                logger.error(error_msg)
                send_notification("Upgrade Timeout", error_msg)
            except ClientException as e:
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
