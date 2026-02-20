# TrueNAS Auto Update

Yes, I know what you're thinking - "You shouldn't auto-update your TrueNAS apps!" And you're probably right. But if you're feeling adventurous and want to live on the edge, this Docker container will automatically update your TrueNAS SCALE apps whenever updates are available.

## Environment Variables

- `BASE_URL`: Your TrueNAS SCALE instance URL (e.g., `https://truenas.local`)
- `API_KEY`: Your TrueNAS API key (see "Getting Started" for how to generate one)
- `API_USERNAME` (_optional_): Username associated with the API key (default: `root`). **Required for TrueNAS 25.04+** where API keys are user-linked.
- `CRON_SCHEDULE` (_optional_): Cron schedule for when to check for updates (e.g., `0 4 * * *` for daily at 4 AM). If not set, the script will run once and then exit.
- `APPRISE_URLS` (_optional_): Apprise URLs to send notifications to (e.g., `https://example.com/apprise,https://example.com/apprise2`) More info on [Apprise](https://github.com/caronc/apprise)
- `NOTIFY_ON_SUCCESS` (_optional_): Set to "true" to receive notifications when apps are successfully updated (default: "false")
- `ONLY_UPDATE_STARTED_APPS` (_optional_): Set to "true" to only update apps that are currently running/powered-on (default: "false"). This helps avoid unnecessary updates for apps that are stopped.
- `EXCLUDE_APPS` (_optional_): Comma-separated list of app names to skip during updates (e.g., `app1,app2`). This is useful if you want to exclude certain apps from being updated automatically.
- `INCLUDE_APPS` (_optional_): Comma-separated list of app names to include during updates (e.g., `app1,app2`). This is useful if you want to only update certain apps and skip the rest.
- `SSL_VERIFY` (_optional_): Set to "true" to enable SSL certificate verification for TrueNAS API calls (default: "false"). When enabled, the connection will verify SSL certificates, which is recommended when using valid certificates like Let's Encrypt. When disabled, SSL warnings may be displayed but connections to TrueNAS instances with self-signed certificates will work.
- `AUTO_CLEANUP_IMAGES` (_optional_): Set to "true" to automatically clean up unused Docker images after all updates are complete (default: "false"). This runs `docker image prune -a -f` to remove all unused images and free up disk space. **Requires the Docker socket to be mounted** (see Docker Image Cleanup section below).

NOTE: The `EXCLUDE_APPS` and `INCLUDE_APPS` variables are mutually exclusive. If both are set, the application will error out.

## Getting Started

1. Generate an API key in your TrueNAS SCALE UI:

   **For TrueNAS 25.04 (Fangtooth) and later:**
   - Go to Credentials → Users → click on your user (e.g., `admin` or `root`)
   - Scroll to the "API Keys" section
   - Click "Add" to create a new key
   - Copy the API key and note the username - you'll need both
   - Set `API_USERNAME` to match the user who owns the key

   **For TrueNAS 24.10 and earlier:**
   - Go to System Settings → API Keys
   - Click "Add"
   - Give it a name and save
   - Copy the API key to your clipboard
   
2. Deploy the container:

### Using Docker Compose (Recommended for Testing)

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set your `BASE_URL` and `API_KEY`

3. Run the container:
   ```bash
   # Run once and exit
   docker-compose up

   # Run in background with restart policy
   docker-compose up -d
   ```

### Using Docker CLI

- Run the container on any Docker host:

```bash
docker run --name truenas-auto-update \
         --restart unless-stopped \
         -e BASE_URL=https://your-truenas-url \
         -e API_KEY=your-api-key \
         -e API_USERNAME=admin \
         -e CRON_SCHEDULE="0 4 * * *" \
         -e APPRISE_URLS="https://example.com/apprise,https://example.com/apprise2" \
         -e NOTIFY_ON_SUCCESS="true" \
         -e ONLY_UPDATE_STARTED_APPS="true" \
         ghcr.io/marvinvr/truenas-auto-update
```

- **With Docker image cleanup enabled** (see Docker Image Cleanup section below):

```bash
docker run --name truenas-auto-update \
         --restart unless-stopped \
         -v /var/run/docker.sock:/var/run/docker.sock \
         -e BASE_URL=https://your-truenas-url \
         -e API_KEY=your-api-key \
         -e API_USERNAME=admin \
         -e CRON_SCHEDULE="0 4 * * *" \
         -e AUTO_CLEANUP_IMAGES="true" \
         ghcr.io/marvinvr/truenas-auto-update
```

- **or** install it as a Custom App in TrueNAS:

1. Go to the Apps page in SCALE
2. Click "Discover Apps" in the top right
3. Click "Custom App" in the top right
4. Set the following values:
   - Name: `truenas-auto-update`
   - Repository: `ghcr.io/marvinvr/truenas-auto-update`
   - Tag: `latest`
   - Environment Variables (As described above)
   - Restart Policy: `Unless Stopped`
   - Network: `Host` (if you have restricted the Allowed IP Addresses)
   - Storage: `/var/run/docker.sock` both for mount & host path (if you want to run the Docker Image Cleanup)
5. Install the app
6. (_optional_) Review the app logs to ensure it's working as expected

## Docker Image Cleanup

When TrueNAS apps are updated, old Docker images are left behind and can consume significant disk space over time. This container includes an optional Docker image cleanup feature to automatically remove these unused images after all updates are complete.

### How It Works

When `AUTO_CLEANUP_IMAGES` is set to `true`, the container will:

1. Complete all app updates first
2. Check if the Docker daemon is accessible
3. Run `docker image prune -a -f` to remove all unused Docker images
4. Send an Apprise notification if the Docker daemon is not accessible or if cleanup fails

### Requirements

To use this feature, you **must** mount the Docker socket into the container:

```bash
-v /var/run/docker.sock:/var/run/docker.sock
```

### Important Notes

- The cleanup happens **after all updates are complete**, so if an update fails, cleanup will still proceed for other successfully updated apps
- The script uses `docker image prune -a -f` which removes **all unused images**, not just those related to TrueNAS apps
- If `AUTO_CLEANUP_IMAGES` is enabled but the Docker socket is not mounted, you will receive an Apprise notification warning (if configured)
- The feature is **disabled by default** and requires explicit opt-in via the environment variable
- Everything works normally even without the Docker socket mounted and with `AUTO_CLEANUP_IMAGES` set to `false` (the default)

### Example with Docker Image Cleanup

```bash
docker run --name truenas-auto-update \
         --restart unless-stopped \
         -v /var/run/docker.sock:/var/run/docker.sock \
         -e BASE_URL=https://your-truenas-url \
         -e API_KEY=your-api-key \
         -e API_USERNAME=admin \
         -e CRON_SCHEDULE="0 4 * * *" \
         -e AUTO_CLEANUP_IMAGES="true" \
         -e APPRISE_URLS="https://example.com/apprise" \
         ghcr.io/marvinvr/truenas-auto-update
```

## Disclaimer

This tool automatically updates your TrueNAS SCALE apps without manual intervention. While convenient, this could potentially lead to issues if an update introduces problems. Use at your own risk and make sure you have proper backups!

Cheers,
[marvinvr](https://github.com/marvinvr)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
