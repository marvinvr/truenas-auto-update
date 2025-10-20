import logging
import os
import subprocess
import time

import apprise
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = os.getenv("BASE_URL")
API_KEY = os.getenv("API_KEY")
APPRISE_URLS = os.getenv("APPRISE_URLS", "").strip()
NOTIFY_ON_SUCCESS = os.getenv("NOTIFY_ON_SUCCESS", "false").lower() == "true"
ONLY_UPDATE_STARTED_APPS = os.getenv("ONLY_UPDATE_STARTED_APPS", "false").lower() == "true"
AUTO_CLEANUP_IMAGES = os.getenv("AUTO_CLEANUP_IMAGES", "false").lower() == "true"
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


if not BASE_URL or not API_KEY:
    logger.error("BASE_URL or API_KEY is not set")
    send_notification("Configuration Error", "BASE_URL or API_KEY is not set")
    exit(1)

BASE_URL = BASE_URL + "/api/v2.0"

try:
    response = requests.get(
        f"{BASE_URL}/app", headers={"Authorization": f"Bearer {API_KEY}"}, verify=False
    )
except Exception as e:
    logger.error(f"Failed to get apps: {str(e)}")
    send_notification("Error", f"Failed to get apps on {BASE_URL}: {str(e)}")
    exit(1)

if response.status_code != 200:
    logger.error(f"Failed to get apps: {response.status_code}")
    send_notification(
        "Error", f"Failed to get apps on {BASE_URL}: {response.status_code}"
    )
    exit(1)

apps = response.json()

apps_with_upgrade = [app for app in apps if app["upgrade_available"]]

logger.info(f"Found {len(apps_with_upgrade)} apps with upgrade available")

def await_job(job_id):
    logger.info(f"Waiting for job {job_id} to complete...")
    job = requests.post(
        f"{BASE_URL}/core/job_wait",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json=job_id,
        verify=False,
    )

    if job.status_code != 200:
        logger.error(f"Failed to wait for job {job_id}: {job.status_code}")
        return None

    return job


for app in apps_with_upgrade:
    if EXCLUDE_APPS and app["name"] in EXCLUDE_APPS:
        logger.info(f"Skipping upgrade for: {app['name']} (APP in EXCLUDE_APPS)")
        continue
    if INCLUDE_APPS and app["name"] not in INCLUDE_APPS:
        logger.info(f"Skipping upgrade for: {app['name']} (APP not in INCLUDE_APPS)")
        continue
    if ONLY_UPDATE_STARTED_APPS and app.get("state", "").upper() != "RUNNING":
        logger.info(f"Skipping upgrade for: {app['name']} (APP not running, state: {app.get('state', 'unknown')})")
        continue
        
    logger.info(f"Upgrading {app['name']}...")
    response = requests.post(
        f"{BASE_URL}/app/upgrade",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"app_name": app["id"]},
        verify=False,
    )

    if response.status_code != 200:
        error_msg = f"Failed to upgrade {app['name']}: {response.status_code}"
        logger.error(error_msg)
        send_notification("Upgrade Failed", error_msg)
        continue

    job_id = response.text
    response = await_job(job_id)

    if response.status_code == 200:
        success_msg = f"Upgrade of {app['name']} triggered successfully"
        logger.info(success_msg)
        if NOTIFY_ON_SUCCESS:
            send_notification("App Updated", f"Successfully updated {app['name']} to the latest version")
    else:
        error_msg = f"Failed to upgrade {app['name']}: {response.status_code}"
        logger.error(error_msg)
        send_notification("Upgrade Failed", error_msg)

    time.sleep(1)

logger.info("All app updates completed")

# Run Docker image cleanup after all updates are done
cleanup_docker_images()

logger.info("Done")
